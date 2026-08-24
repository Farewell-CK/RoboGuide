//! Generic Node Connector with non-blocking networking and daemon-wide execution identity.

use crate::execution::{ExecutionRegistry, ExecutionRegistryDecision};
use crate::protocol::{
    ClientFrame, ExecutionFact, Hello, PROTOCOL_VERSION_V0_1, Registration, ServerFrame,
    decode_frame, encode_frame,
};
use std::fmt::{Display, Formatter};
use std::sync::{Arc, Mutex};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::net::TcpStream;
use tokio::sync::mpsc;

/// Local EAIOS/backend boundary. Implementations retain Immediate How and final safety.
pub trait LocalExecutionBackend: Send + Sync + 'static {
    /// Runs one execution and pushes lifecycle facts progressively through `events`.
    fn execute(
        &self,
        execution_id: &str,
        command: &crate::protocol::ExecuteCommand,
        events: mpsc::UnboundedSender<ExecutionFact>,
    );
    /// Requests cancellation without claiming success; the backend emits the resulting fact.
    fn cancel(&self, execution_id: &str, events: mpsc::UnboundedSender<ExecutionFact>);
}

/// Long-lived connector whose execution registry survives individual network sessions.
pub struct NodeConnector<B> {
    /// Server address initiated by this connector.
    address: String,
    /// Registration sent after negotiation.
    registration: Registration,
    /// Generic local EAIOS backend.
    backend: Arc<B>,
    /// Execution identity authority retained across reconnects.
    executions: Arc<Mutex<ExecutionRegistry>>,
}

impl<B: LocalExecutionBackend> NodeConnector<B> {
    /// Creates a connector that always initiates the network connection.
    pub fn new(address: impl Into<String>, registration: Registration, backend: B) -> Self {
        Self {
            address: address.into(),
            registration,
            backend: Arc::new(backend),
            executions: Arc::new(Mutex::new(ExecutionRegistry::default())),
        }
    }

    /// Connects, negotiates, registers, and processes one transport session.
    pub async fn run(&self) -> Result<(), ConnectorError> {
        let stream = TcpStream::connect(&self.address)
            .await
            .map_err(ConnectorError::Io)?;
        let (reader, writer) = stream.into_split();
        let (outgoing_tx, mut outgoing_rx) = mpsc::unbounded_channel::<ClientFrame>();
        let writer_task = tokio::spawn(async move {
            let mut writer = writer;
            while let Some(frame) = outgoing_rx.recv().await {
                let bytes =
                    encode_frame(&frame).map_err(|error| ConnectorError::Protocol(error.0))?;
                writer.write_all(&bytes).await.map_err(ConnectorError::Io)?;
            }
            Ok::<(), ConnectorError>(())
        });
        outgoing_tx
            .send(ClientFrame::Hello(Hello {
                protocol_versions: vec![PROTOCOL_VERSION_V0_1.to_string()],
                node_contract_versions: vec![self.registration.node_contract_version.clone()],
                node_id: self.registration.node_id.clone(),
            }))
            .map_err(|_| ConnectorError::Closed)?;
        let mut lines = BufReader::new(reader).lines();
        match next_frame(&mut lines).await? {
            ServerFrame::HelloAck { .. } => {}
            _ => return Err(ConnectorError::Protocol("expected HelloAck".to_string())),
        }
        outgoing_tx
            .send(ClientFrame::Register(self.registration.clone()))
            .map_err(|_| ConnectorError::Closed)?;
        let (session_id, lease_id, heartbeat_interval_ms) = match next_frame(&mut lines).await? {
            ServerFrame::RegistrationAccepted {
                session_id,
                lease_id,
                heartbeat_interval_ms,
            } => (session_id, lease_id, heartbeat_interval_ms),
            _ => {
                return Err(ConnectorError::Protocol(
                    "expected RegistrationAccepted".to_string(),
                ));
            }
        };
        for (execution_id, status) in self
            .executions
            .lock()
            .map_err(|_| ConnectorError::Registry)?
            .snapshots()
        {
            outgoing_tx
                .send(ClientFrame::ExecutionStatus {
                    session_id: session_id.clone(),
                    execution_id,
                    fact: status.as_fact(),
                })
                .map_err(|_| ConnectorError::Closed)?;
        }
        let mut heartbeat =
            tokio::time::interval(std::time::Duration::from_millis(heartbeat_interval_ms));
        let mut heartbeat_sequence = 0_u64;
        loop {
            tokio::select! {
                line = lines.next_line() => {
                    let Some(line) = line.map_err(ConnectorError::Io)? else { break };
                    let frame = decode_frame(line.as_bytes()).map_err(|error| ConnectorError::Protocol(error.0))?;
                    self.handle_server_frame(frame, &session_id, &outgoing_tx)?;
                }
                _ = heartbeat.tick() => {
                    heartbeat_sequence += 1;
                    outgoing_tx.send(ClientFrame::Heartbeat { session_id: session_id.clone(), lease_id: lease_id.clone(), sequence: heartbeat_sequence, status: None }).map_err(|_| ConnectorError::Closed)?;
                }
            }
        }
        drop(outgoing_tx);
        writer_task.abort();
        Ok(())
    }

    /// Handles Execute/Cancel without running backend work on the network task.
    fn handle_server_frame(
        &self,
        frame: ServerFrame,
        session_id: &str,
        outgoing: &mpsc::UnboundedSender<ClientFrame>,
    ) -> Result<(), ConnectorError> {
        match frame {
            ServerFrame::Execute {
                session_id: frame_session,
                execution_id,
                command,
            } if frame_session == session_id => {
                let decision = self
                    .executions
                    .lock()
                    .map_err(|_| ConnectorError::Registry)?
                    .begin(&execution_id, &command);
                match decision {
                    ExecutionRegistryDecision::Start => self.spawn_execution(
                        execution_id,
                        command,
                        session_id.to_string(),
                        outgoing.clone(),
                    ),
                    ExecutionRegistryDecision::Existing(status) => outgoing
                        .send(ClientFrame::ExecutionStatus {
                            session_id: session_id.to_string(),
                            execution_id,
                            fact: status.as_fact(),
                        })
                        .map_err(|_| ConnectorError::Closed)?,
                    ExecutionRegistryDecision::Conflict => outgoing
                        .send(ClientFrame::ExecutionStatus {
                            session_id: session_id.to_string(),
                            execution_id,
                            fact: ExecutionFact::Failed {
                                reason: "execution_id reused with a different command".to_string(),
                            },
                        })
                        .map_err(|_| ConnectorError::Closed)?,
                }
            }
            ServerFrame::Cancel {
                session_id: frame_session,
                execution_id,
            } if frame_session == session_id => {
                self.spawn_cancel(execution_id, session_id.to_string(), outgoing.clone())
            }
            ServerFrame::Ack { .. } | ServerFrame::Error { .. } => {}
            _ => {
                return Err(ConnectorError::Protocol(
                    "session identity mismatch".to_string(),
                ));
            }
        }
        Ok(())
    }

    /// Starts backend work and forwards progressive facts through the writer queue.
    fn spawn_execution(
        &self,
        execution_id: String,
        command: crate::protocol::ExecuteCommand,
        session_id: String,
        outgoing: mpsc::UnboundedSender<ClientFrame>,
    ) {
        let backend = Arc::clone(&self.backend);
        let executions = Arc::clone(&self.executions);
        let (fact_tx, mut fact_rx) = mpsc::unbounded_channel();
        let backend_id = execution_id.clone();
        tokio::task::spawn_blocking(move || backend.execute(&backend_id, &command, fact_tx));
        tokio::spawn(async move {
            while let Some(fact) = fact_rx.recv().await {
                let sequence = executions
                    .lock()
                    .ok()
                    .and_then(|mut registry| registry.record_fact(&execution_id, &fact));
                if let Some(sequence) = sequence {
                    let _ = outgoing.send(ClientFrame::ExecutionEvent {
                        session_id: session_id.clone(),
                        execution_id: execution_id.clone(),
                        sequence,
                        fact,
                    });
                }
            }
        });
    }

    /// Sends cancellation to Local EAIOS concurrently with a running execution.
    fn spawn_cancel(
        &self,
        execution_id: String,
        session_id: String,
        outgoing: mpsc::UnboundedSender<ClientFrame>,
    ) {
        if self.executions.lock().map_or(true, |registry| {
            registry.status(&execution_id) == crate::ExecutionStatus::Unknown
        }) {
            let _ = outgoing.send(ClientFrame::ExecutionStatus {
                session_id,
                execution_id,
                fact: ExecutionFact::Unknown,
            });
            return;
        }
        let backend = Arc::clone(&self.backend);
        let executions = Arc::clone(&self.executions);
        let (fact_tx, mut fact_rx) = mpsc::unbounded_channel();
        let backend_id = execution_id.clone();
        tokio::task::spawn_blocking(move || backend.cancel(&backend_id, fact_tx));
        tokio::spawn(async move {
            while let Some(fact) = fact_rx.recv().await {
                let sequence = executions
                    .lock()
                    .ok()
                    .and_then(|mut registry| registry.record_fact(&execution_id, &fact));
                if let Some(sequence) = sequence {
                    let _ = outgoing.send(ClientFrame::ExecutionEvent {
                        session_id: session_id.clone(),
                        execution_id: execution_id.clone(),
                        sequence,
                        fact,
                    });
                }
            }
        });
    }

    /// Reconnects after stream loss while retaining execution identities.
    pub async fn run_with_reconnect(
        &self,
        attempts: usize,
        delay: std::time::Duration,
    ) -> Result<(), ConnectorError> {
        let mut remaining = attempts;
        loop {
            match self.run().await {
                Ok(()) if remaining == 0 => return Ok(()),
                Ok(()) | Err(_) if remaining > 0 => {
                    remaining -= 1;
                    tokio::time::sleep(delay).await;
                }
                Err(error) => return Err(error),
                Ok(()) => return Ok(()),
            }
        }
    }

    /// Returns current execution state for diagnostics and tests.
    pub fn execution_status(
        &self,
        execution_id: &str,
    ) -> Result<crate::ExecutionStatus, ConnectorError> {
        Ok(self
            .executions
            .lock()
            .map_err(|_| ConnectorError::Registry)?
            .status(execution_id))
    }
}

/// Connector failure.
#[derive(Debug)]
pub enum ConnectorError {
    /// TCP or stream I/O failure.
    Io(std::io::Error),
    /// Protocol failure.
    Protocol(String),
    /// Outgoing stream closed.
    Closed,
    /// Execution registry lock was poisoned.
    Registry,
}
impl Display for ConnectorError {
    /// Formats connector failures.
    fn fmt(&self, f: &mut Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Io(error) => error.fmt(f),
            Self::Protocol(reason) => f.write_str(reason),
            Self::Closed => f.write_str("connector stream closed"),
            Self::Registry => f.write_str("execution registry unavailable"),
        }
    }
}
impl std::error::Error for ConnectorError {}

/// Reads one framed server message.
async fn next_frame<T: for<'de> serde::Deserialize<'de>>(
    lines: &mut tokio::io::Lines<BufReader<tokio::net::tcp::OwnedReadHalf>>,
) -> Result<T, ConnectorError> {
    let line = lines
        .next_line()
        .await
        .map_err(ConnectorError::Io)?
        .ok_or_else(|| ConnectorError::Protocol("connection closed".to_string()))?;
    decode_frame(line.as_bytes()).map_err(|error| ConnectorError::Protocol(error.0))
}
