//! Formal gRPC Node Service lifecycle and reconnect behavior.

use crate::{AdapterError, LocalEaiosAdapter, NodeServiceConfig};
use integration::PROTOCOL_VERSION_V0_1;
use integration::grpc::v0_1::node_message::Message as NodePayload;
use integration::grpc::v0_1::robo_guide_node_protocol_client::RoboGuideNodeProtocolClient;
use integration::grpc::v0_1::server_message::Message as ServerPayload;
use integration::grpc::v0_1::{
    Cancel, ExecutionEvent, ExecutionSnapshot, Heartbeat, Hello, NodeMessage, Register,
    ServerMessage,
};
use std::collections::BTreeMap;
use std::fmt::{Display, Formatter};
use std::sync::{Arc, Mutex};
use tokio::sync::mpsc;
use tokio_stream::wrappers::UnboundedReceiverStream;

/// Long-running node-side service independent of any concrete Local EAIOS.
pub struct NodeService<A> {
    /// User configuration loaded at process startup.
    config: NodeServiceConfig,
    /// Injected Local EAIOS Adapter.
    adapter: Arc<A>,
    /// Connector-owned snapshots retained across gRPC sessions.
    executions: Arc<Mutex<BTreeMap<String, ExecutionSnapshot>>>,
}

impl<A: LocalEaiosAdapter> NodeService<A> {
    /// Creates a service from validated configuration and an adapter implementation.
    pub fn new(config: NodeServiceConfig, adapter: A) -> Self {
        Self {
            config,
            adapter: Arc::new(adapter),
            executions: Arc::new(Mutex::new(BTreeMap::new())),
        }
    }

    /// Runs forever, creating a new session and lease after each transport loss.
    pub async fn run(&self) -> Result<(), NodeServiceError> {
        loop {
            let _ = self.run_session().await;
            tokio::time::sleep(std::time::Duration::from_millis(
                self.config.reconnect_delay_ms,
            ))
            .await;
        }
    }

    /// Runs one Hello→Welcome→Register→Registered gRPC session.
    pub async fn run_session(&self) -> Result<(), NodeServiceError> {
        let mut client = RoboGuideNodeProtocolClient::connect(self.config.server_endpoint.clone())
            .await
            .map_err(NodeServiceError::Transport)?;
        let (outbound, receiver) = mpsc::unbounded_channel();
        outbound
            .send(NodeMessage {
                message: Some(NodePayload::Hello(Hello {
                    node_id: self.config.node_id.clone(),
                    protocol_versions: vec![PROTOCOL_VERSION_V0_1.to_string()],
                    node_contract_versions: vec!["roboguide.node.v0.1".to_string()],
                })),
            })
            .map_err(|_| NodeServiceError::Closed)?;
        let mut inbound = client
            .node_session(UnboundedReceiverStream::new(receiver))
            .await
            .map_err(NodeServiceError::Status)?
            .into_inner();
        let welcome = inbound
            .message()
            .await
            .map_err(NodeServiceError::Status)?
            .and_then(|message| message.message)
            .ok_or(NodeServiceError::Closed)?;
        let ServerPayload::Welcome(welcome) = welcome else {
            return Err(NodeServiceError::Protocol("expected Welcome".to_string()));
        };
        if welcome.selected_protocol_version != PROTOCOL_VERSION_V0_1 {
            return Err(NodeServiceError::Protocol(
                "server selected unsupported protocol".to_string(),
            ));
        }
        let registration = self
            .adapter
            .discover(
                &self.config.node_id,
                &welcome.selected_node_contract_version,
            )
            .map_err(NodeServiceError::Adapter)?;
        outbound
            .send(NodeMessage {
                message: Some(NodePayload::Register(Register {
                    registration: Some(registration),
                })),
            })
            .map_err(|_| NodeServiceError::Closed)?;
        let registered = inbound
            .message()
            .await
            .map_err(NodeServiceError::Status)?
            .and_then(|message| message.message)
            .ok_or(NodeServiceError::Closed)?;
        let ServerPayload::Registered(registered) = registered else {
            return Err(NodeServiceError::Protocol(
                "expected Registered".to_string(),
            ));
        };
        self.replay_snapshots(&registered.session_id, &outbound)?;
        let interval_ms = welcome.heartbeat_interval_ms.max(1);
        let mut heartbeat = tokio::time::interval(std::time::Duration::from_millis(interval_ms));
        let mut sequence = 0_u64;
        loop {
            tokio::select! {
                message = inbound.message() => {
                    let Some(message) = message.map_err(NodeServiceError::Status)? else { return Ok(()); };
                    self.handle_server_message(message, &registered.session_id, &outbound)?;
                }
                _ = heartbeat.tick() => {
                    sequence += 1;
                    let status = self.adapter.status().map_err(NodeServiceError::Adapter)?;
                    outbound.send(NodeMessage { message: Some(NodePayload::Heartbeat(Heartbeat { session_id: registered.session_id.clone(), lease_id: registered.lease_id.clone(), sequence, status: Some(status) })) }).map_err(|_| NodeServiceError::Closed)?;
                }
            }
        }
    }

    /// Replays adapter and connector-owned snapshots after every new registration.
    fn replay_snapshots(
        &self,
        session_id: &str,
        outbound: &mpsc::UnboundedSender<NodeMessage>,
    ) -> Result<(), NodeServiceError> {
        let mut snapshots = self
            .adapter
            .execution_snapshots()
            .map_err(NodeServiceError::Adapter)?;
        snapshots.extend(
            self.executions
                .lock()
                .map_err(|_| NodeServiceError::Registry)?
                .values()
                .cloned(),
        );
        snapshots.sort_by(|left, right| left.execution_id.cmp(&right.execution_id));
        snapshots.dedup_by(|left, right| left.execution_id == right.execution_id);
        for mut snapshot in snapshots {
            snapshot.session_id = session_id.to_string();
            outbound
                .send(NodeMessage {
                    message: Some(NodePayload::ExecutionSnapshot(snapshot)),
                })
                .map_err(|_| NodeServiceError::Closed)?;
        }
        Ok(())
    }

    /// Routes Execute/Cancel while preventing duplicate physical invocation.
    fn handle_server_message(
        &self,
        message: ServerMessage,
        session_id: &str,
        outbound: &mpsc::UnboundedSender<NodeMessage>,
    ) -> Result<(), NodeServiceError> {
        match message.message {
            Some(ServerPayload::Execute(execute)) if execute.session_id == session_id => self
                .handle_execute(
                    execute.execution_id,
                    execute.invocation.ok_or_else(|| {
                        NodeServiceError::Protocol("Execute lacks invocation".to_string())
                    })?,
                    session_id.to_string(),
                    outbound.clone(),
                ),
            Some(ServerPayload::Cancel(Cancel {
                session_id: command_session,
                execution_id,
            })) if command_session == session_id => {
                self.handle_cancel(execution_id, session_id.to_string(), outbound.clone())
            }
            Some(ServerPayload::Ack(_)) | Some(ServerPayload::Error(_)) => Ok(()),
            _ => Err(NodeServiceError::Protocol(
                "unexpected server message or session".to_string(),
            )),
        }
    }

    /// Starts a new execution once or reports the existing snapshot.
    fn handle_execute(
        &self,
        execution_id: String,
        invocation: integration::grpc::v0_1::CanonicalInvocation,
        session_id: String,
        outbound: mpsc::UnboundedSender<NodeMessage>,
    ) -> Result<(), NodeServiceError> {
        if let Some(mut existing) = self
            .executions
            .lock()
            .map_err(|_| NodeServiceError::Registry)?
            .get(&execution_id)
            .cloned()
        {
            existing.session_id = session_id;
            outbound
                .send(NodeMessage {
                    message: Some(NodePayload::ExecutionSnapshot(existing)),
                })
                .map_err(|_| NodeServiceError::Closed)?;
            return Ok(());
        }
        let accepted = ExecutionSnapshot {
            session_id: session_id.clone(),
            execution_id: execution_id.clone(),
            last_sequence: 0,
            phase: integration::grpc::v0_1::ExecutionPhase::Accepted as i32,
            reason: String::new(),
        };
        self.executions
            .lock()
            .map_err(|_| NodeServiceError::Registry)?
            .insert(execution_id.clone(), accepted);
        let mut events = self
            .adapter
            .execute(&execution_id, invocation)
            .map_err(NodeServiceError::Adapter)?;
        let executions = Arc::clone(&self.executions);
        tokio::spawn(async move {
            while let Some(mut event) = events.recv().await {
                event.session_id = session_id.clone();
                let snapshot = snapshot_from_event(&event);
                if let Ok(mut registry) = executions.lock() {
                    registry.insert(execution_id.clone(), snapshot);
                }
                let _ = outbound.send(NodeMessage {
                    message: Some(NodePayload::ExecutionEvent(event)),
                });
            }
        });
        Ok(())
    }

    /// Routes cancellation facts through the same snapshot/event path as execution.
    fn handle_cancel(
        &self,
        execution_id: String,
        session_id: String,
        outbound: mpsc::UnboundedSender<NodeMessage>,
    ) -> Result<(), NodeServiceError> {
        let mut events = self
            .adapter
            .cancel(&execution_id)
            .map_err(NodeServiceError::Adapter)?;
        let executions = Arc::clone(&self.executions);
        tokio::spawn(async move {
            while let Some(mut event) = events.recv().await {
                event.session_id = session_id.clone();
                if let Ok(mut registry) = executions.lock() {
                    registry.insert(execution_id.clone(), snapshot_from_event(&event));
                }
                let _ = outbound.send(NodeMessage {
                    message: Some(NodePayload::ExecutionEvent(event)),
                });
            }
        });
        Ok(())
    }
}

/// Converts a progressive event into reconnect reconciliation state.
fn snapshot_from_event(event: &ExecutionEvent) -> ExecutionSnapshot {
    ExecutionSnapshot {
        session_id: event.session_id.clone(),
        execution_id: event.execution_id.clone(),
        last_sequence: event.sequence,
        phase: event.phase,
        reason: event.reason.clone(),
    }
}

/// Node Service lifecycle failure.
#[derive(Debug)]
pub enum NodeServiceError {
    /// gRPC channel connection failed.
    Transport(tonic::transport::Error),
    /// gRPC stream status failed.
    Status(tonic::Status),
    /// Local Adapter failed.
    Adapter(AdapterError),
    /// Protocol lifecycle was invalid.
    Protocol(String),
    /// Stream closed.
    Closed,
    /// Execution registry unavailable.
    Registry,
}
impl Display for NodeServiceError {
    /// Formats the service failure.
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Transport(error) => error.fmt(formatter),
            Self::Status(error) => error.fmt(formatter),
            Self::Adapter(error) => error.fmt(formatter),
            Self::Protocol(reason) => formatter.write_str(reason),
            Self::Closed => formatter.write_str("Node Protocol stream closed"),
            Self::Registry => formatter.write_str("execution registry unavailable"),
        }
    }
}
impl std::error::Error for NodeServiceError {}
