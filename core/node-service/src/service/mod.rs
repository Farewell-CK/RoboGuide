//! Formal Node Protocol v0.4 lifecycle around the generic Local Integration Engine.

use crate::{EngineError, ExecuteDisposition, LocalIntegrationEngine};
use integration::grpc::v0_4::node_message::Message as NodePayload;
use integration::grpc::v0_4::robo_guide_node_protocol_client::RoboGuideNodeProtocolClient;
use integration::grpc::v0_4::server_message::Message as ServerPayload;
use integration::grpc::v0_4::{
    Cancel, Capability, ExecutionEvent, Heartbeat, Hello, LocalRuntime, LocalSystemDescriptor,
    MemoryKind, MemoryProviderDescriptor, MemoryScopeKind, MemoryVisibility, NODE_CONTRACT_VERSION,
    NodeMessage, NodeRegistration, PROTOCOL_VERSION, PeerChannelReadiness, ProtocolError, Register,
    RegistrationUpdate, Resource, Sensor, ServerMessage, StateExportDescriptor, StateObjectClass,
    StateObservation, StateObservationBatch, StateSemantic,
};
use std::collections::{BTreeMap, BTreeSet};
use std::fmt::{Display, Formatter};
use std::sync::Arc;
use tokio::sync::{broadcast, mpsc};
use tokio_stream::wrappers::UnboundedReceiverStream;

mod protocol_projection;

use protocol_projection::*;

/// Maximum State records carried by one Node Protocol management message.
const MAX_STATE_BATCH_RECORDS: usize = 64;
/// Maximum aggregate State JSON bytes carried by one management message.
const MAX_STATE_BATCH_BYTES: usize = 512 * 1024;

/// Long-running, vendor-neutral node-side RoboGuide service.
pub struct NodeService {
    /// Generic local catalog, drivers, locks, journal, and workflow tasks.
    engine: LocalIntegrationEngine,
}

impl NodeService {
    /// Creates the single node-side service around an immutable local engine.
    pub const fn new(engine: LocalIntegrationEngine) -> Self {
        Self { engine }
    }

    /// Recovers durable executions and reconnects forever after session loss.
    pub async fn run(&self) -> Result<(), NodeServiceError> {
        self.engine.recover()?;
        loop {
            if let Err(error) = self.run_session().await {
                eprintln!("roboguide-node session ended: {error}");
            }
            tokio::time::sleep(std::time::Duration::from_millis(
                self.engine.catalog().reconnect_delay_ms(),
            ))
            .await;
        }
    }

    /// Runs one Hello -> Welcome -> Register -> Registered v0.4 session.
    pub async fn run_session(&self) -> Result<(), NodeServiceError> {
        let catalog = self.engine.catalog();
        let mut client =
            RoboGuideNodeProtocolClient::connect(catalog.server_endpoint().to_string())
                .await
                .map_err(NodeServiceError::Transport)?;
        let (outbound, receiver) = mpsc::unbounded_channel();
        outbound
            .send(NodeMessage {
                message: Some(NodePayload::Hello(Hello {
                    node_id: catalog.node_id().to_string(),
                    protocol_versions: vec![PROTOCOL_VERSION.to_string()],
                    node_contract_versions: vec![NODE_CONTRACT_VERSION.to_string()],
                })),
            })
            .map_err(|_| NodeServiceError::Closed)?;
        let mut inbound = client
            .node_session(UnboundedReceiverStream::new(receiver))
            .await
            .map_err(NodeServiceError::Status)?
            .into_inner();
        let welcome = next_server_payload(&mut inbound).await?;
        let ServerPayload::Welcome(welcome) = welcome else {
            return Err(NodeServiceError::Protocol("expected Welcome".to_string()));
        };
        if welcome.selected_protocol_version != PROTOCOL_VERSION
            || welcome.selected_node_contract_version != NODE_CONTRACT_VERSION
        {
            return Err(NodeServiceError::Protocol(
                "server selected an unsupported protocol or contract".to_string(),
            ));
        }
        let initial_observation = self.engine.observe().await;
        let initial_readiness = readiness_snapshot(&initial_observation);
        outbound
            .send(NodeMessage {
                message: Some(NodePayload::Register(Register {
                    registration: Some(registration_from_observation(
                        catalog,
                        &initial_observation,
                    )),
                })),
            })
            .map_err(|_| NodeServiceError::Closed)?;
        let registered = next_server_payload(&mut inbound).await?;
        let ServerPayload::Registered(registered) = registered else {
            return Err(NodeServiceError::Protocol(
                "expected Registered".to_string(),
            ));
        };
        let mut local_events = self.engine.subscribe();
        let mut peer_readiness_events = self.engine.subscribe_peer_channel_readiness();
        self.replay_snapshots(&registered.session_id, &outbound)?;
        let management_sequence = Arc::new(tokio::sync::Mutex::new(0_u64));
        let heartbeat_task = self.spawn_heartbeat(
            registered.session_id.clone(),
            registered.lease_id.clone(),
            welcome.heartbeat_interval_ms.max(1),
            outbound.clone(),
            initial_readiness,
            Arc::clone(&management_sequence),
        );
        let state_task = self.spawn_state_observations(
            registered.session_id.clone(),
            outbound.clone(),
            Arc::clone(&management_sequence),
        );
        let peer_observation_task = self.spawn_peer_channel_observations(
            registered.session_id.clone(),
            outbound.clone(),
            Arc::clone(&management_sequence),
        );
        let memory_task = self.spawn_memory_exports(registered.session_id.clone());
        let session_result: Result<(), NodeServiceError> = async {
            loop {
                tokio::select! {
                    message = inbound.message() => {
                        let Some(message) = message.map_err(NodeServiceError::Status)? else {
                            return Ok(());
                        };
                        self.handle_server_message(
                            message,
                            &registered.session_id,
                            &outbound,
                            &management_sequence,
                        ).await?;
                    }
                    event = local_events.recv() => match event {
                        Ok(event) => outbound.send(NodeMessage { message: Some(NodePayload::ExecutionEvent(ExecutionEvent {
                            session_id: registered.session_id.clone(),
                            execution_id: event.execution_id,
                            sequence: event.sequence,
                            phase: event.phase as i32,
                            reason: event.reason,
                        })) }).map_err(|_| NodeServiceError::Closed)?,
                        Err(broadcast::error::RecvError::Lagged(_)) => self.replay_snapshots(&registered.session_id, &outbound)?,
                        Err(broadcast::error::RecvError::Closed) => return Err(NodeServiceError::Closed),
                    },
                    event = peer_readiness_events.recv() => match event {
                        Ok(event) => {
                            let mut sequence = management_sequence.lock().await;
                            *sequence = sequence.saturating_add(1);
                            outbound.send(peer_readiness_message(
                                &registered.session_id,
                                *sequence,
                                event,
                            )).map_err(|_| NodeServiceError::Closed)?;
                        }
                        Err(broadcast::error::RecvError::Lagged(_)) => {
                            return Err(NodeServiceError::Protocol(
                                "peer readiness stream lagged; reconnect is required to fence stale evidence"
                                    .to_string(),
                            ));
                        }
                        Err(broadcast::error::RecvError::Closed) => return Err(NodeServiceError::Closed),
                    },
                }
            }
        }.await;
        heartbeat_task.abort();
        state_task.abort();
        peer_observation_task.abort();
        memory_task.abort();
        session_result
    }

    /// Runs health observation independently so a slow local system cannot block Cancel/Execute.
    fn spawn_heartbeat(
        &self,
        session_id: String,
        lease_id: String,
        interval_ms: u64,
        outbound: mpsc::UnboundedSender<NodeMessage>,
        mut previous_readiness: BTreeMap<String, bool>,
        sequence: Arc<tokio::sync::Mutex<u64>>,
    ) -> tokio::task::JoinHandle<()> {
        let engine = self.engine.clone();
        tokio::spawn(async move {
            let mut heartbeat =
                tokio::time::interval(std::time::Duration::from_millis(interval_ms));
            loop {
                heartbeat.tick().await;
                let observation = engine.observe().await;
                let mut sequence = sequence.lock().await;
                for message in management_messages(
                    &session_id,
                    &lease_id,
                    &mut sequence,
                    engine.catalog(),
                    &observation,
                    &mut previous_readiness,
                ) {
                    if outbound.send(message).is_err() {
                        return;
                    }
                }
            }
        })
    }

    /// Periodically samples configured State channels without coupling failure to heartbeat health.
    fn spawn_state_observations(
        &self,
        session_id: String,
        outbound: mpsc::UnboundedSender<NodeMessage>,
        sequence: Arc<tokio::sync::Mutex<u64>>,
    ) -> tokio::task::JoinHandle<()> {
        let engine = self.engine.clone();
        tokio::spawn(async move {
            let intervals = engine
                .catalog()
                .state_exports()
                .iter()
                .map(|(id, export)| (id.clone(), export.interval_ms()))
                .collect::<BTreeMap<_, _>>();
            let Some(minimum_interval) = intervals.values().copied().min() else {
                std::future::pending::<()>().await;
                return;
            };
            let mut next_due = intervals
                .keys()
                .cloned()
                .map(|id| (id, tokio::time::Instant::now()))
                .collect::<BTreeMap<_, _>>();
            let mut ticker =
                tokio::time::interval(std::time::Duration::from_millis(minimum_interval));
            loop {
                ticker.tick().await;
                let now = tokio::time::Instant::now();
                let due = next_due
                    .iter_mut()
                    .filter_map(|(id, due_at)| {
                        if now < *due_at {
                            return None;
                        }
                        let interval = intervals[id];
                        *due_at = now + std::time::Duration::from_millis(interval);
                        Some(id.clone())
                    })
                    .collect::<BTreeSet<_>>();
                let facts = engine.observe_state_exports(&due).await;
                if facts.is_empty() {
                    continue;
                }
                let batches = state_observation_batches(facts);
                if batches.is_empty() {
                    continue;
                }
                let mut sequence = sequence.lock().await;
                for observations in batches {
                    *sequence = sequence.saturating_add(1);
                    if outbound
                        .send(NodeMessage {
                            message: Some(NodePayload::StateObservationBatch(
                                StateObservationBatch {
                                    session_id: session_id.clone(),
                                    sequence: *sequence,
                                    observations,
                                },
                            )),
                        })
                        .is_err()
                    {
                        return;
                    }
                }
            }
        })
    }

    /// Periodically observes channels already established by configured Local EAIOS endpoints.
    fn spawn_peer_channel_observations(
        &self,
        session_id: String,
        outbound: mpsc::UnboundedSender<NodeMessage>,
        sequence: Arc<tokio::sync::Mutex<u64>>,
    ) -> tokio::task::JoinHandle<()> {
        let engine = self.engine.clone();
        tokio::spawn(async move {
            let intervals = engine
                .catalog()
                .peer_channel_observers()
                .iter()
                .map(|(id, observer)| (id.clone(), observer.interval_ms()))
                .collect::<BTreeMap<_, _>>();
            let Some(minimum_interval) = intervals.values().copied().min() else {
                std::future::pending::<()>().await;
                return;
            };
            let mut next_due = intervals
                .keys()
                .cloned()
                .map(|id| (id, tokio::time::Instant::now()))
                .collect::<BTreeMap<_, _>>();
            let mut ticker =
                tokio::time::interval(std::time::Duration::from_millis(minimum_interval));
            loop {
                ticker.tick().await;
                let now = tokio::time::Instant::now();
                let due = next_due
                    .iter_mut()
                    .filter_map(|(id, due_at)| {
                        if now < *due_at {
                            return None;
                        }
                        *due_at = now + std::time::Duration::from_millis(intervals[id]);
                        Some(id.clone())
                    })
                    .collect::<BTreeSet<_>>();
                let facts = engine.observe_peer_channel_readiness(&due).await;
                if facts.is_empty() {
                    continue;
                }
                let mut sequence = sequence.lock().await;
                for fact in facts {
                    *sequence = sequence.saturating_add(1);
                    if outbound
                        .send(peer_readiness_message(&session_id, *sequence, fact))
                        .is_err()
                    {
                        return;
                    }
                }
            }
        })
    }

    /// Periodically publishes the provider-authorized Memory set via the Artifact data plane.
    ///
    /// `discover` is the provider's publish-eligibility decision. Node Service performs only the
    /// publication mechanism and mechanical safety checks; it does not promote or select from all
    /// Local EAIOS Memory. ExecutionGroup-scoped items are excluded because complete distributed
    /// Group authorization/handoff is not part of the current Node Protocol.
    fn spawn_memory_exports(&self, session_id: String) -> tokio::task::JoinHandle<()> {
        let engine = self.engine.clone();
        tokio::spawn(async move {
            if engine.catalog().memory_providers().is_empty() || engine.artifact_stager().is_none()
            {
                std::future::pending::<()>().await;
                return;
            }
            let mut ticker = tokio::time::interval(std::time::Duration::from_secs(30));
            loop {
                ticker.tick().await;
                let provider_ids = engine
                    .catalog()
                    .memory_providers()
                    .values()
                    .filter(|provider| provider.operational())
                    .map(|provider| provider.id().to_string())
                    .collect::<Vec<_>>();
                for provider_id in provider_ids {
                    let invocation = serde_json::json!({
                        "node_id": engine.catalog().node_id(),
                        "provider_id": provider_id,
                    });
                    let publish_eligible_manifests = match engine
                        .discover_memories(
                            &provider_id,
                            &crate::MemoryQuery::default(),
                            invocation.clone(),
                        )
                        .await
                    {
                        Ok(manifests) => manifests,
                        Err(error) => {
                            eprintln!("Memory discovery for {provider_id} will retry: {error}");
                            continue;
                        }
                    };
                    for manifest in publish_eligible_manifests {
                        if matches!(manifest.scope(), domain::MemoryScope::ExecutionGroup(_)) {
                            continue;
                        }
                        if let Err(error) = engine.validate_memory_export(&provider_id, &manifest) {
                            eprintln!(
                                "Memory discovery for {} returned an inadmissible export: {error}",
                                manifest.selector()
                            );
                            continue;
                        }
                        let artifact_path = match engine
                            .export_memory(&provider_id, &manifest, invocation.clone())
                            .await
                        {
                            Ok(path) => path,
                            Err(error) => {
                                eprintln!(
                                    "Memory export for {} will retry: {error}",
                                    manifest.selector()
                                );
                                continue;
                            }
                        };
                        let Some(stager) = engine.artifact_stager() else {
                            eprintln!(
                                "Memory publication for {} requires the Artifact data plane",
                                manifest.selector()
                            );
                            continue;
                        };
                        if manifest.visibility() == domain::MemoryVisibility::Exchangeable {
                            let Some(path) = artifact_path else {
                                eprintln!(
                                    "Memory export for {} lacks its configured artifact path",
                                    manifest.selector()
                                );
                                continue;
                            };
                            if let Err(error) = stager.upload_memory_output(&manifest, &path).await
                            {
                                eprintln!(
                                    "Memory Artifact upload for {} will retry: {error}",
                                    manifest.selector()
                                );
                                continue;
                            }
                        }
                        let node_id = match domain::NodeId::new(engine.catalog().node_id()) {
                            Ok(node_id) => node_id,
                            Err(error) => {
                                eprintln!("Memory publisher Node identity is invalid: {error}");
                                continue;
                            }
                        };
                        if let Err(error) = stager
                            .publish_memory(&manifest, &node_id, &session_id)
                            .await
                        {
                            eprintln!(
                                "Memory publication for {} is fenced until retry: {error}",
                                manifest.selector()
                            );
                        }
                    }
                }
            }
        })
    }

    /// Handles commands without allowing Server input to select Local How.
    async fn handle_server_message(
        &self,
        message: ServerMessage,
        session_id: &str,
        outbound: &mpsc::UnboundedSender<NodeMessage>,
        management_sequence: &Arc<tokio::sync::Mutex<u64>>,
    ) -> Result<(), NodeServiceError> {
        match message.message {
            Some(ServerPayload::Execute(execute)) if execute.session_id == session_id => {
                let invocation = execute.invocation.ok_or_else(|| {
                    NodeServiceError::Protocol("Execute lacks canonical invocation".to_string())
                })?;
                let execution_id = execute.execution_id;
                let command_id = execute.command_id;
                if command_id != format!("dispatch-{execution_id}") {
                    return Err(NodeServiceError::Protocol(
                        "Execute command identity does not match attempt".to_string(),
                    ));
                }
                let mut receipt_reason = String::new();
                let receipt_status = match self.engine.execute(
                    execution_id.clone(),
                    invocation,
                    execute.resource_ids,
                ) {
                    Ok(ExecuteDisposition::Started) => {
                        integration::grpc::v0_4::CommandReceiptStatus::CommandPersisted
                    }
                    Ok(ExecuteDisposition::Existing(mut snapshot)) => {
                        snapshot.session_id = session_id.to_string();
                        outbound
                            .send(NodeMessage {
                                message: Some(NodePayload::ExecutionSnapshot(snapshot)),
                            })
                            .map_err(|_| NodeServiceError::Closed)?;
                        integration::grpc::v0_4::CommandReceiptStatus::CommandPersisted
                    }
                    Err(error) => {
                        receipt_reason = error.to_string();
                        send_local_rejection(
                            outbound,
                            session_id,
                            &execution_id,
                            "execute_rejected",
                            &error,
                        )?;
                        integration::grpc::v0_4::CommandReceiptStatus::CommandRejected
                    }
                };
                send_command_receipt(
                    outbound,
                    session_id,
                    command_id,
                    execution_id,
                    integration::grpc::v0_4::CommandKind::CommandExecute,
                    receipt_status,
                    receipt_reason,
                    management_sequence,
                )
                .await?;
                Ok(())
            }
            Some(ServerPayload::Cancel(Cancel {
                session_id: command_session,
                execution_id,
                command_id,
            })) if command_session == session_id => {
                if command_id != format!("cancel-{execution_id}") {
                    return Err(NodeServiceError::Protocol(
                        "Cancel command identity does not match attempt".to_string(),
                    ));
                }
                let mut receipt_reason = String::new();
                let receipt_status = if let Err(error) = self.engine.cancel(&execution_id) {
                    receipt_reason = error.to_string();
                    send_local_rejection(
                        outbound,
                        session_id,
                        &execution_id,
                        "cancel_rejected",
                        &error,
                    )?;
                    integration::grpc::v0_4::CommandReceiptStatus::CommandRejected
                } else {
                    let mut snapshot = self
                        .engine
                        .snapshots()?
                        .into_iter()
                        .find(|snapshot| snapshot.execution_id == execution_id)
                        .unwrap_or_else(|| integration::grpc::v0_4::ExecutionSnapshot {
                            execution_id: execution_id.clone(),
                            session_id: session_id.to_string(),
                            last_sequence: 1,
                            phase: integration::grpc::v0_4::ExecutionPhase::Cancelled as i32,
                            reason: "cancelled before Execute reached Node journal".to_string(),
                        });
                    snapshot.session_id = session_id.to_string();
                    outbound
                        .send(NodeMessage {
                            message: Some(NodePayload::ExecutionSnapshot(snapshot)),
                        })
                        .map_err(|_| NodeServiceError::Closed)?;
                    integration::grpc::v0_4::CommandReceiptStatus::CommandPersisted
                };
                send_command_receipt(
                    outbound,
                    session_id,
                    command_id,
                    execution_id,
                    integration::grpc::v0_4::CommandKind::CommandCancel,
                    receipt_status,
                    receipt_reason,
                    management_sequence,
                )
                .await?;
                Ok(())
            }
            Some(ServerPayload::Ack(_)) | Some(ServerPayload::Error(_)) => Ok(()),
            _ => Err(NodeServiceError::Protocol(
                "unexpected server message or session".to_string(),
            )),
        }
    }

    /// Replays durable execution state into the current transport session.
    fn replay_snapshots(
        &self,
        session_id: &str,
        outbound: &mpsc::UnboundedSender<NodeMessage>,
    ) -> Result<(), NodeServiceError> {
        for mut snapshot in self.engine.snapshots()? {
            snapshot.session_id = session_id.to_string();
            outbound
                .send(NodeMessage {
                    message: Some(NodePayload::ExecutionSnapshot(snapshot)),
                })
                .map_err(|_| NodeServiceError::Closed)?;
        }
        Ok(())
    }
}

/// Sends command-admission evidence with monotonic management ordering.
#[allow(clippy::too_many_arguments)]
async fn send_command_receipt(
    outbound: &mpsc::UnboundedSender<NodeMessage>,
    session_id: &str,
    command_id: String,
    execution_id: String,
    kind: integration::grpc::v0_4::CommandKind,
    status: integration::grpc::v0_4::CommandReceiptStatus,
    reason: String,
    management_sequence: &Arc<tokio::sync::Mutex<u64>>,
) -> Result<(), NodeServiceError> {
    let mut sequence = management_sequence.lock().await;
    *sequence = sequence.saturating_add(1);
    outbound
        .send(NodeMessage {
            message: Some(NodePayload::CommandReceipt(
                integration::grpc::v0_4::CommandReceipt {
                    session_id: session_id.to_string(),
                    sequence: *sequence,
                    command_id,
                    execution_id,
                    kind: kind as i32,
                    status: status as i32,
                    reason,
                },
            )),
        })
        .map_err(|_| NodeServiceError::Closed)
}

/// Attaches current Node session ordering to one Local EAIOS peer readiness fact.
/// Node Service lifecycle failure.
#[derive(Debug)]
pub enum NodeServiceError {
    /// gRPC channel connection failed.
    Transport(tonic::transport::Error),
    /// gRPC stream status failed.
    Status(tonic::Status),
    /// Generic local engine rejected an operation.
    Engine(EngineError),
    /// Protocol lifecycle was invalid.
    Protocol(String),
    /// Stream closed.
    Closed,
}

impl Display for NodeServiceError {
    /// Formats a stable Node Service diagnostic.
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Transport(error) => error.fmt(formatter),
            Self::Status(error) => error.fmt(formatter),
            Self::Engine(error) => error.fmt(formatter),
            Self::Protocol(reason) => formatter.write_str(reason),
            Self::Closed => formatter.write_str("Node Protocol stream closed"),
        }
    }
}

impl std::error::Error for NodeServiceError {}
impl From<EngineError> for NodeServiceError {
    fn from(value: EngineError) -> Self {
        Self::Engine(value)
    }
}

#[cfg(test)]
#[path = "tests/mod.rs"]
mod tests;
