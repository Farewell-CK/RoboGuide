//! Formal Node Protocol v0.4 lifecycle around the generic Local Integration Engine.

use crate::MemoryImportStatus;

mod memory_continuity;
use crate::{EngineError, ExecuteDisposition, LocalIntegrationEngine};
use integration::grpc::v0_4::node_message::Message as NodePayload;
use integration::grpc::v0_4::robo_guide_node_protocol_client::RoboGuideNodeProtocolClient;
use integration::grpc::v0_4::server_message::Message as ServerPayload;
use integration::grpc::v0_4::{
    Cancel, Capability, CapabilityProfile, ExecutionEvent, Heartbeat, Hello, LocalRuntime,
    LocalSystemDescriptor, MemoryImportReceipt, MemoryImportReceiptStatus, MemoryKind,
    MemoryProviderDescriptor, MemoryScopeKind, MemoryVisibility, NodeMessage, NodeRegistration,
    OperationRef, OperationSupport, PROTOCOL_VERSION, PeerChannelReadiness, ProtocolError,
    Register, RegistrationUpdate, Resource, ScalarValue, Sensor, ServerMessage,
    StateExportDescriptor, StateObjectClass, StateObservation, StateObservationBatch,
    StateSemantic,
};
use std::collections::{BTreeMap, BTreeSet};
use std::fmt::{Display, Formatter};
use std::sync::Arc;
use tokio::sync::{broadcast, mpsc};
use tokio_stream::wrappers::UnboundedReceiverStream;
use tonic::transport::{Certificate, ClientTlsConfig, Endpoint, Identity};

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

/// Fences interrupted provider side effects, including dropped futures and session teardown.
struct MemoryImportGuard {
    /// Shared local journal authority.
    engine: LocalIntegrationEngine,
    /// Stable selective-import identity.
    import_id: String,
    /// Immutable Controller command identity.
    command_id: String,
    /// Provider/workflow identity fence.
    digest: String,
}

impl Drop for MemoryImportGuard {
    /// Retains ambiguity unless a terminal outcome was already durably recorded.
    fn drop(&mut self) {
        let _ = self.engine.record_memory_import(
            &self.import_id,
            &self.command_id,
            &self.digest,
            MemoryImportStatus::Unknown,
            "import processing interrupted; provider reconciliation is required",
        );
    }
}

impl NodeService {
    /// Creates the single node-side service around an immutable local engine.
    pub const fn new(engine: LocalIntegrationEngine) -> Self {
        Self { engine }
    }

    /// Recovers durable executions and reconnects forever after session loss.
    pub async fn run(&self) -> Result<(), NodeServiceError> {
        self.engine.recover()?;
        let mut route_attempt = 0_usize;
        loop {
            if let Err(error) = self
                .run_session_on(
                    self.engine
                        .catalog()
                        .server_endpoint_for_attempt(route_attempt),
                )
                .await
            {
                eprintln!("roboguide-node session ended: {error}");
            }
            route_attempt = route_attempt.wrapping_add(1);
            tokio::time::sleep(std::time::Duration::from_millis(
                self.engine.catalog().reconnect_delay_ms(),
            ))
            .await;
        }
    }

    /// Runs one Hello -> Welcome -> Register -> Registered v0.4 session.
    pub async fn run_session(&self) -> Result<(), NodeServiceError> {
        self.run_session_on(self.engine.catalog().server_endpoint())
            .await
    }

    /// Reconnects one fixed route with the same durable journal and handles; registration does not replay Execute.
    async fn run_session_on(&self, endpoint: &str) -> Result<(), NodeServiceError> {
        let catalog = self.engine.catalog();
        let mut client = connect_node_client(endpoint).await?;
        let (outbound, receiver) = mpsc::unbounded_channel();
        outbound
            .send(NodeMessage {
                message: Some(NodePayload::Hello(Hello {
                    node_id: catalog.node_id().to_string(),
                    protocol_versions: vec![PROTOCOL_VERSION.to_string()],
                    node_contract_versions: vec![catalog.node_contract_version().to_string()],
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
            || welcome.selected_node_contract_version != catalog.node_contract_version()
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
        let mut imports = tokio::task::JoinSet::new();
        let session_result: Result<(), NodeServiceError> = async {
            loop {
                tokio::select! {
                    message = inbound.message() => {
                        let Some(message) = message.map_err(NodeServiceError::Status)? else {
                            return Ok(());
                        };
                        if let Some(ServerPayload::MemoryImport(import)) = &message.message {
                            if import.session_id != registered.session_id {
                                return Err(NodeServiceError::Protocol("Memory import session differs".into()));
                            }
                            if imports.len() >= 4 {
                                self.send_memory_import_receipt(
                                    &registered.session_id, import,
                                    MemoryImportReceiptStatus::MemoryImportRejected,
                                    "bounded Memory import workers are busy; command was not admitted".into(),
                                    &management_sequence, &outbound,
                                ).await?;
                            } else {
                                let service = Self::new(self.engine.clone());
                                let import = import.clone();
                                let session = registered.session_id.clone();
                                let sender = outbound.clone();
                                let sequence = Arc::clone(&management_sequence);
                                imports.spawn(async move {
                                    service.handle_memory_import(import, &session, &sender, &sequence).await
                                });
                            }
                            continue;
                        }
                        if let Some(ServerPayload::MemoryImportQuery(query)) = &message.message {
                            if query.session_id != registered.session_id { return Err(NodeServiceError::Protocol("Memory query session differs".into())); }
                            if imports.len() >= 4 {
                                self.send_memory_query_receipt(&registered.session_id, query, None, "bounded Memory workers are busy", &outbound, &management_sequence).await?;
                            } else {
                                let service = Self::new(self.engine.clone());
                                let query = query.clone(); let session = registered.session_id.clone();
                                let sender = outbound.clone(); let sequence = Arc::clone(&management_sequence);
                                imports.spawn(async move { service.handle_memory_query(query, &session, &sender, &sequence).await });
                            }
                            continue;
                        }
                        self.handle_server_message(
                            message,
                            &registered.session_id,
                            &outbound,
                            &management_sequence,
                        ).await?;
                    }
                    result = imports.join_next(), if !imports.is_empty() => {
                        result.ok_or(NodeServiceError::Closed)?
                            .map_err(|error| NodeServiceError::Protocol(format!("Memory import worker failed: {error}")))??;
                    }
                    event = local_events.recv() => self.forward_execution_event(
                        event, &registered.session_id, &outbound,
                    )?,
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
                let receipt_status = match self
                    .engine
                    .admit_controller_epoch(&execute.controller_epoch)
                {
                    Err(error) => {
                        receipt_reason = error.to_string();
                        send_local_rejection(
                            outbound,
                            session_id,
                            &execution_id,
                            "execute_epoch_rejected",
                            &error,
                        )?;
                        integration::grpc::v0_4::CommandReceiptStatus::CommandRejected
                    }
                    Ok(()) => match self.engine.execute_with_session(
                        execution_id.clone(),
                        invocation,
                        execute.resource_ids,
                        &execute.execution_session_json,
                    ) {
                        Ok(ExecuteDisposition::Started | ExecuteDisposition::DispatchPending) => {
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
                    },
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
                controller_epoch,
            })) if command_session == session_id => {
                if command_id != format!("cancel-{execution_id}") {
                    return Err(NodeServiceError::Protocol(
                        "Cancel command identity does not match attempt".to_string(),
                    ));
                }
                let mut receipt_reason = String::new();
                let receipt_status =
                    if let Err(error) = self.engine.admit_controller_epoch(&controller_epoch) {
                        receipt_reason = error.to_string();
                        send_local_rejection(
                            outbound,
                            session_id,
                            &execution_id,
                            "cancel_epoch_rejected",
                            &error,
                        )?;
                        integration::grpc::v0_4::CommandReceiptStatus::CommandRejected
                    } else if let Err(error) = self.engine.cancel(&execution_id) {
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

    /// Admits and executes one Controller-authorized Memory import with a durable retry fence.
    async fn handle_memory_import(
        &self,
        import: integration::grpc::v0_4::MemoryImport,
        session_id: &str,
        outbound: &mpsc::UnboundedSender<NodeMessage>,
        management_sequence: &Arc<tokio::sync::Mutex<u64>>,
    ) -> Result<(), NodeServiceError> {
        if import.command_id != format!("memory-import-{}", import.import_id)
            || import.consumer_provider_id.trim().is_empty()
            || import.manifest_json.trim().is_empty()
            || import.manifest_json.len() > integration::MAX_MEMORY_IMPORT_MANIFEST_BYTES
            || import.mission_id.trim().is_empty()
            || import.group_id.trim().is_empty()
            || import.task_id.trim().is_empty()
            || import.role_id.trim().is_empty()
            || import.execution_id.trim().is_empty()
        {
            return self
                .send_memory_import_receipt(
                    session_id,
                    &import,
                    MemoryImportReceiptStatus::MemoryImportRejected,
                    "Memory import identity or manifest is invalid".to_string(),
                    management_sequence,
                    outbound,
                )
                .await;
        }
        let expected = integration::memory_import_authorization_digest(
            &import.import_id,
            &import.command_id,
            &import.consumer_provider_id,
            &import.manifest_json,
            &import.mission_id,
            &import.group_id,
            &import.task_id,
            &import.role_id,
            &import.execution_id,
            &import.controller_epoch,
        );
        if import.authorization_digest != expected {
            return self
                .send_memory_import_receipt(
                    session_id,
                    &import,
                    MemoryImportReceiptStatus::MemoryImportRejected,
                    "Memory import authorization digest is invalid".to_string(),
                    management_sequence,
                    outbound,
                )
                .await;
        }
        if let Err(error) = self.engine.admit_controller_epoch(&import.controller_epoch) {
            return self
                .send_memory_import_receipt(
                    session_id,
                    &import,
                    MemoryImportReceiptStatus::MemoryImportRejected,
                    error.to_string(),
                    management_sequence,
                    outbound,
                )
                .await;
        }
        let identity_digest = match self.engine.memory_import_identity(&import) {
            Ok(digest) => digest,
            Err(error) => {
                return self
                    .send_memory_import_receipt(
                        session_id,
                        &import,
                        MemoryImportReceiptStatus::MemoryImportRejected,
                        error.to_string(),
                        management_sequence,
                        outbound,
                    )
                    .await;
            }
        };
        let prepared = match self
            .engine
            .prepare_authorized_memory_import(&import, &identity_digest)
            .await
        {
            Ok(prepared) => prepared,
            Err(error) => {
                return self
                    .send_memory_import_receipt(
                        session_id,
                        &import,
                        MemoryImportReceiptStatus::MemoryImportUnknown,
                        error.to_string(),
                        management_sequence,
                        outbound,
                    )
                    .await;
            }
        };
        if let crate::PrepareMemoryImport::Conflict(_) = prepared {
            return self
                .send_memory_import_receipt(
                    session_id,
                    &import,
                    MemoryImportReceiptStatus::MemoryImportRejected,
                    "Memory import identity was reused with different evidence".to_string(),
                    management_sequence,
                    outbound,
                )
                .await;
        }
        if let crate::PrepareMemoryImport::Existing(record) = &prepared {
            let status = match record.status() {
                crate::MemoryImportStatus::Completed => {
                    MemoryImportReceiptStatus::MemoryImportCompleted
                }
                crate::MemoryImportStatus::Rejected => {
                    MemoryImportReceiptStatus::MemoryImportRejected
                }
                crate::MemoryImportStatus::Pending => {
                    MemoryImportReceiptStatus::MemoryImportPersisted
                }
                crate::MemoryImportStatus::Unknown => {
                    MemoryImportReceiptStatus::MemoryImportUnknown
                }
            };
            return self
                .send_memory_import_receipt(
                    session_id,
                    &import,
                    status,
                    record.reason().to_string(),
                    management_sequence,
                    outbound,
                )
                .await;
        }
        let _guard = MemoryImportGuard {
            engine: self.engine.clone(),
            import_id: import.import_id.clone(),
            command_id: import.command_id.clone(),
            digest: identity_digest.clone(),
        };
        if let Err(error) = self.engine.validate_memory_consumer(&import) {
            let reason = error.to_string();
            self.engine.record_memory_import(
                &import.import_id,
                &import.command_id,
                &identity_digest,
                MemoryImportStatus::Rejected,
                &reason,
            )?;
            return self
                .send_memory_import_receipt(
                    session_id,
                    &import,
                    MemoryImportReceiptStatus::MemoryImportRejected,
                    reason,
                    management_sequence,
                    outbound,
                )
                .await;
        }
        self.send_memory_import_receipt(
            session_id,
            &import,
            MemoryImportReceiptStatus::MemoryImportPersisted,
            String::new(),
            management_sequence,
            outbound,
        )
        .await?;
        let manifest: domain::MemoryArtifactManifest =
            match serde_json::from_str(&import.manifest_json) {
                Ok(manifest) => manifest,
                Err(error) => {
                    self.engine.record_memory_import(
                        &import.import_id,
                        &import.command_id,
                        &identity_digest,
                        crate::MemoryImportStatus::Rejected,
                        &error.to_string(),
                    )?;
                    return self
                        .send_memory_import_receipt(
                            session_id,
                            &import,
                            MemoryImportReceiptStatus::MemoryImportRejected,
                            error.to_string(),
                            management_sequence,
                            outbound,
                        )
                        .await;
                }
            };
        if let Err(error) = self
            .engine
            .validate_memory_import(&import.consumer_provider_id, &manifest)
        {
            self.engine.record_memory_import(
                &import.import_id,
                &import.command_id,
                &identity_digest,
                MemoryImportStatus::Rejected,
                &error.to_string(),
            )?;
            return self
                .send_memory_import_receipt(
                    session_id,
                    &import,
                    MemoryImportReceiptStatus::MemoryImportRejected,
                    error.to_string(),
                    management_sequence,
                    outbound,
                )
                .await;
        }
        let invocation = serde_json::json!({
            "mission_id": import.mission_id,
            "group_id": import.group_id,
            "task_id": import.task_id,
            "role_id": import.role_id,
            "execution_id": import.execution_id,
            "consumer_provider_id": import.consumer_provider_id,
            "memory_import": self.engine.original_import_continuity(&import.import_id)?,
        });
        let result = self
            .engine
            .exchange_memory(
                &import.consumer_provider_id,
                &manifest,
                session_id,
                invocation,
            )
            .await;
        let (status, mut reason) = match result {
            Ok(()) => (MemoryImportStatus::Completed, String::new()),
            Err(error) => (MemoryImportStatus::Unknown, error.to_string()),
        };
        while reason.len() > 4_096 {
            reason.pop();
        }
        self.engine.record_memory_import(
            &import.import_id,
            &import.command_id,
            &identity_digest,
            status,
            &reason,
        )?;
        self.send_memory_import_receipt(
            session_id,
            &import,
            match status {
                MemoryImportStatus::Completed => MemoryImportReceiptStatus::MemoryImportCompleted,
                MemoryImportStatus::Unknown => MemoryImportReceiptStatus::MemoryImportUnknown,
                MemoryImportStatus::Pending | MemoryImportStatus::Rejected => {
                    MemoryImportReceiptStatus::MemoryImportRejected
                }
            },
            reason.to_string(),
            management_sequence,
            outbound,
        )
        .await
    }

    /// Sends one monotonic selective-import receipt after durable local admission/outcome.
    async fn send_memory_import_receipt(
        &self,
        session_id: &str,
        import: &integration::grpc::v0_4::MemoryImport,
        status: MemoryImportReceiptStatus,
        reason: String,
        management_sequence: &Arc<tokio::sync::Mutex<u64>>,
        outbound: &mpsc::UnboundedSender<NodeMessage>,
    ) -> Result<(), NodeServiceError> {
        let mut sequence = management_sequence.lock().await;
        *sequence = sequence.saturating_add(1);
        outbound
            .send(NodeMessage {
                message: Some(NodePayload::MemoryImportReceipt(MemoryImportReceipt {
                    session_id: session_id.to_string(),
                    sequence: *sequence,
                    import_id: import.import_id.clone(),
                    command_id: import.command_id.clone(),
                    status: status as i32,
                    reason,
                })),
            })
            .map_err(|_| NodeServiceError::Closed)
    }

    /// Forwards local facts, recovering a lagged receiver from the journal without redispatch.
    fn forward_execution_event(
        &self,
        event: Result<crate::LocalExecutionEvent, broadcast::error::RecvError>,
        session_id: &str,
        outbound: &mpsc::UnboundedSender<NodeMessage>,
    ) -> Result<(), NodeServiceError> {
        match event {
            Ok(event) => outbound
                .send(NodeMessage {
                    message: Some(NodePayload::ExecutionEvent(ExecutionEvent {
                        session_id: session_id.to_string(),
                        execution_id: event.execution_id,
                        sequence: event.sequence,
                        phase: event.phase as i32,
                        reason: event.reason,
                    })),
                })
                .map_err(|_| NodeServiceError::Closed),
            Err(broadcast::error::RecvError::Lagged(_)) => {
                self.replay_snapshots(session_id, outbound)
            }
            Err(broadcast::error::RecvError::Closed) => Err(NodeServiceError::Closed),
        }
    }

    /// Replays durable lifecycle facts; live dispatch admission is covered by command receipts.
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

/// Connects a Node session with the optional certificate-bound mTLS profile.
async fn connect_node_client(
    endpoint: &str,
) -> Result<RoboGuideNodeProtocolClient<tonic::transport::Channel>, NodeServiceError> {
    let names = [
        "ROBOGUIDE_NODE_MTLS_CLIENT_CERT",
        "ROBOGUIDE_NODE_MTLS_CLIENT_KEY",
        "ROBOGUIDE_NODE_MTLS_SERVER_CA",
        "ROBOGUIDE_NODE_MTLS_DOMAIN",
    ];
    let values = names
        .iter()
        .map(|name| std::env::var(name).ok())
        .collect::<Vec<_>>();
    let any = values.iter().any(Option::is_some);
    if any && values.iter().any(Option::is_none) {
        return Err(NodeServiceError::Protocol(
            "Node mTLS requires client cert, client key, server CA and domain".into(),
        ));
    }
    let mut builder = Endpoint::from_shared(endpoint.to_string())
        .map_err(|error| NodeServiceError::Protocol(error.to_string()))?;
    if any {
        if !endpoint.starts_with("https://") {
            return Err(NodeServiceError::Protocol(
                "Node mTLS requires an https:// Controller endpoint".into(),
            ));
        }
        let [cert, key, ca, domain] = values
            .try_into()
            .expect("four Node mTLS variables are collected");
        let tls = ClientTlsConfig::new()
            .domain_name(domain.expect("validated Node mTLS domain"))
            .ca_certificate(Certificate::from_pem(
                std::fs::read(ca.expect("validated Node mTLS CA path"))
                    .map_err(|error| NodeServiceError::Protocol(error.to_string()))?,
            ))
            .identity(Identity::from_pem(
                std::fs::read(cert.expect("validated Node mTLS cert path"))
                    .map_err(|error| NodeServiceError::Protocol(error.to_string()))?,
                std::fs::read(key.expect("validated Node mTLS key path"))
                    .map_err(|error| NodeServiceError::Protocol(error.to_string()))?,
            ));
        builder = builder
            .tls_config(tls)
            .map_err(NodeServiceError::Transport)?;
    }
    Ok(RoboGuideNodeProtocolClient::new(
        builder
            .connect()
            .await
            .map_err(NodeServiceError::Transport)?,
    ))
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
