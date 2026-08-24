//!主动连接 RoboGuide 的 generic Node Connector。

use crate::protocol::{
    ClientFrame, ExecutionFact, Hello, PROTOCOL_VERSION_V0_1, Registration, ServerFrame,
    decode_frame, encode_frame,
};
use crate::session::{ExecutionDisposition, SessionState};
use std::fmt::{Display, Formatter};
use std::sync::Arc;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::net::TcpStream;

/// Local EAIOS/backend boundary. It does not prescribe Robonix or any vendor API.
pub trait LocalExecutionBackend: Send + Sync + 'static {
    /// Accepts one canonical execution and returns deterministic local facts.
    fn execute(
        &self,
        execution_id: &str,
        command: &crate::protocol::ExecuteCommand,
    ) -> Vec<ExecutionFact>;
    /// Requests local cancellation without assuming physical success.
    fn cancel(&self, execution_id: &str) -> Vec<ExecutionFact>;
}

/// Long-lived active connector session.
pub struct NodeConnector<B> {
    /// Server address initiated by this connector.
    address: String,
    /// Registration sent after negotiation.
    registration: Registration,
    /// Generic local EAIOS backend.
    backend: Arc<B>,
}

impl<B: LocalExecutionBackend> NodeConnector<B> {
    /// Creates a connector that always initiates the TCP connection.
    pub fn new(address: impl Into<String>, registration: Registration, backend: B) -> Self {
        Self {
            address: address.into(),
            registration,
            backend: Arc::new(backend),
        }
    }

    /// Connects, negotiates, registers, and processes server control messages until EOF.
    pub async fn run(&self) -> Result<(), ConnectorError> {
        let stream = TcpStream::connect(&self.address)
            .await
            .map_err(ConnectorError::Io)?;
        let (reader, mut writer) = stream.into_split();
        writer
            .write_all(&encode_frame(&ClientFrame::Hello(Hello {
                protocol_versions: vec![PROTOCOL_VERSION_V0_1.to_string()],
                node_contract_versions: vec![self.registration.node_contract_version.clone()],
                node_id: self.registration.node_id.clone(),
            }))?)
            .await
            .map_err(ConnectorError::Io)?;
        let mut lines = BufReader::new(reader).lines();
        let ack: ServerFrame = next_frame(&mut lines).await?;
        match ack {
            ServerFrame::HelloAck { .. } => {}
            _ => return Err(ConnectorError::Protocol("expected HelloAck".to_string())),
        };
        let (session_id, lease_id) =
            register_after_ack(&mut writer, &mut lines, &self.registration).await?;
        let mut session = SessionState::new(session_id, lease_id);
        let mut heartbeat = tokio::time::interval(std::time::Duration::from_secs(5));
        let mut sequence = 0_u64;
        loop {
            let line = tokio::select! {
                line = lines.next_line() => line.map_err(ConnectorError::Io)?,
                _ = heartbeat.tick() => {
                    sequence += 1;
                    writer.write_all(&encode_frame(&ClientFrame::Heartbeat { session_id: session.session_id.clone(), lease_id: session.lease_id.clone(), sequence, status: None })?).await.map_err(ConnectorError::Io)?;
                    continue;
                }
            };
            let Some(line) = line else { break };
            let frame: ServerFrame =
                decode_frame(line.as_bytes()).map_err(|error| ConnectorError::Protocol(error.0))?;
            match frame {
                ServerFrame::Execute {
                    session_id,
                    execution_id,
                    command,
                } if session_id == session.session_id => {
                    let fingerprint = serde_json::to_string(&command)
                        .map_err(|e| ConnectorError::Protocol(e.to_string()))?;
                    if session.accept_execution(&execution_id, &fingerprint)
                        != ExecutionDisposition::New
                    {
                        continue;
                    }
                    for (sequence, fact) in self
                        .backend
                        .execute(&execution_id, &command)
                        .into_iter()
                        .enumerate()
                    {
                        writer
                            .write_all(&encode_frame(&ClientFrame::ExecutionEvent {
                                session_id: session.session_id.clone(),
                                execution_id: execution_id.clone(),
                                sequence: sequence as u64 + 1,
                                fact,
                            })?)
                            .await
                            .map_err(ConnectorError::Io)?;
                    }
                }
                ServerFrame::Cancel {
                    session_id,
                    execution_id,
                } if session_id == session.session_id => {
                    for (sequence, fact) in
                        self.backend.cancel(&execution_id).into_iter().enumerate()
                    {
                        writer
                            .write_all(&encode_frame(&ClientFrame::ExecutionEvent {
                                session_id: session.session_id.clone(),
                                execution_id: execution_id.clone(),
                                sequence: sequence as u64 + 1,
                                fact,
                            })?)
                            .await
                            .map_err(ConnectorError::Io)?;
                    }
                }
                ServerFrame::Ack { .. } | ServerFrame::Error { .. } => {}
                _ => {
                    return Err(ConnectorError::Protocol(
                        "session identity mismatch".to_string(),
                    ));
                }
            }
        }
        Ok(())
    }

    /// Reconnects after stream loss while preserving caller-owned execution identities.
    pub async fn run_with_reconnect(
        &self,
        attempts: usize,
        delay: std::time::Duration,
    ) -> Result<(), ConnectorError> {
        let mut remaining = attempts;
        loop {
            match self.run().await {
                Ok(()) => {
                    if remaining == 0 {
                        return Ok(());
                    }
                    remaining -= 1;
                    tokio::time::sleep(delay).await;
                }
                Err(_error) if remaining > 0 => {
                    remaining -= 1;
                    tokio::time::sleep(delay).await;
                }
                Err(error) => return Err(error),
            }
        }
    }
}

/// Connector failure.
#[derive(Debug)]
pub enum ConnectorError {
    /// TCP or stream I/O failure.
    Io(std::io::Error),
    /// Protocol failure.
    Protocol(String),
}

impl Display for ConnectorError {
    /// Formats connector failures.
    fn fmt(&self, f: &mut Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Io(e) => e.fmt(f),
            Self::Protocol(e) => e.fmt(f),
        }
    }
}
impl std::error::Error for ConnectorError {}
impl From<crate::protocol::ProtocolError> for ConnectorError {
    fn from(value: crate::protocol::ProtocolError) -> Self {
        Self::Protocol(value.0)
    }
}

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

/// Sends registration after HelloAck and waits for lease authority.
async fn register_after_ack(
    writer: &mut tokio::net::tcp::OwnedWriteHalf,
    lines: &mut tokio::io::Lines<BufReader<tokio::net::tcp::OwnedReadHalf>>,
    registration: &Registration,
) -> Result<(String, String), ConnectorError> {
    writer
        .write_all(&encode_frame(&ClientFrame::Register(registration.clone()))?)
        .await
        .map_err(ConnectorError::Io)?;
    match next_frame(lines).await? {
        ServerFrame::RegistrationAccepted {
            session_id,
            lease_id,
            ..
        } => Ok((session_id, lease_id)),
        _ => Err(ConnectorError::Protocol(
            "expected RegistrationAccepted".to_string(),
        )),
    }
}
