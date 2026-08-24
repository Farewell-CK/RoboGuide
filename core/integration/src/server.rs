//! Integration Server accepting主动 connector sessions and routing facts.

use crate::protocol::{
    ClientFrame, PROTOCOL_VERSION_V0_1, Registration, SERVER_VERSION_V0_1, ServerFrame,
    decode_frame, encode_frame,
};
use crate::session::SessionState;
use std::fmt::{Display, Formatter};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::mpsc;

/// Server-side fact emitted by a connector session.
#[derive(Debug, Clone, PartialEq)]
pub enum ServerEvent {
    /// Registration and lease/session authority completed.
    Registered {
        /// Accepted node registration.
        registration: Registration,
        /// Session identity.
        session_id: String,
        /// Lease identity.
        lease_id: String,
    },
    /// Node pushed an execution fact.
    ExecutionFact {
        /// Node session.
        session_id: String,
        /// Stable execution identity.
        execution_id: String,
        /// Node sequence.
        sequence: u64,
        /// Local fact.
        fact: crate::protocol::ExecutionFact,
    },
    /// Node pushed heartbeat or registration update.
    ClientFact(ClientFrame),
}

/// A server bound to a fixed listener address.
pub struct IntegrationServer {
    /// Bound TCP listener.
    listener: TcpListener,
    /// Monotonic identity source for local sessions.
    next_identity: u64,
}

impl IntegrationServer {
    /// Binds an integration listener; connectors must initiate connections to it.
    pub async fn bind(address: &str) -> Result<Self, ServerError> {
        Ok(Self {
            listener: TcpListener::bind(address).await.map_err(ServerError::Io)?,
            next_identity: 1,
        })
    }

    /// Returns the actual listener address, useful for deterministic local tests.
    pub fn local_addr(&self) -> Result<std::net::SocketAddr, ServerError> {
        self.listener.local_addr().map_err(ServerError::Io)
    }

    /// Accepts one connector, completes Hello/Register, and returns its session.
    pub async fn accept(&mut self) -> Result<ServerSession, ServerError> {
        let (stream, _) = self.listener.accept().await.map_err(ServerError::Io)?;
        let identity = self.next_identity;
        self.next_identity += 1;
        ServerSession::negotiate(stream, identity).await
    }

    /// Accepts nodes continuously and runs every session in an independent Tokio task.
    pub async fn serve(
        mut self,
        events: mpsc::UnboundedSender<ServerEvent>,
    ) -> Result<(), ServerError> {
        loop {
            let session = self.accept().await?;
            let session_events = events.clone();
            tokio::spawn(async move { run_session(session, session_events).await });
        }
    }
}

/// Server side of one negotiated connector stream.
pub struct ServerSession {
    /// Inbound connector frames.
    reader: tokio::io::Lines<BufReader<tokio::net::tcp::OwnedReadHalf>>,
    /// Outbound server control frames.
    writer: tokio::net::tcp::OwnedWriteHalf,
    /// Session and execution idempotency state.
    state: SessionState,
    /// Accepted node registration.
    registration: Registration,
}

impl ServerSession {
    /// Performs Hello and registration negotiation before exposing the session.
    async fn negotiate(stream: TcpStream, identity: u64) -> Result<Self, ServerError> {
        let (reader, mut writer) = stream.into_split();
        let mut lines = BufReader::new(reader).lines();
        let hello: ClientFrame = next(&mut lines).await?;
        let ClientFrame::Hello(hello) = hello else {
            return Err(ServerError::Protocol("expected Hello".to_string()));
        };
        if !hello
            .protocol_versions
            .iter()
            .any(|version| version == PROTOCOL_VERSION_V0_1)
        {
            return Err(ServerError::Protocol(
                "no compatible integration protocol".to_string(),
            ));
        }
        writer
            .write_all(&encode_frame(&ServerFrame::HelloAck {
                server_version: SERVER_VERSION_V0_1.to_string(),
                protocol_version: PROTOCOL_VERSION_V0_1.to_string(),
                node_contract_version: hello
                    .node_contract_versions
                    .first()
                    .cloned()
                    .unwrap_or_default(),
            })?)
            .await
            .map_err(ServerError::Io)?;
        let registration: ClientFrame = next(&mut lines).await?;
        let ClientFrame::Register(registration) = registration else {
            return Err(ServerError::Protocol("expected Register".to_string()));
        };
        let contract = hello
            .node_contract_versions
            .first()
            .cloned()
            .unwrap_or_default();
        registration.validate(&contract)?;
        let session_id = format!("session-{identity}");
        let lease_id = format!("lease-{identity}");
        writer
            .write_all(&encode_frame(&ServerFrame::RegistrationAccepted {
                session_id: session_id.clone(),
                lease_id: lease_id.clone(),
                heartbeat_interval_ms: 5_000,
            })?)
            .await
            .map_err(ServerError::Io)?;
        Ok(Self {
            reader: lines,
            writer,
            state: SessionState::new(session_id, lease_id),
            registration,
        })
    }

    /// Returns the accepted node registration.
    pub fn registration(&self) -> &Registration {
        &self.registration
    }
    /// Returns the stable session identity.
    pub fn session_id(&self) -> &str {
        &self.state.session_id
    }
    /// Sends one Execute command. The execution id is caller-owned and must be reused on retry.
    pub async fn send_execute(
        &mut self,
        execution_id: impl Into<String>,
        command: crate::protocol::ExecuteCommand,
    ) -> Result<(), ServerError> {
        self.writer
            .write_all(&encode_frame(&ServerFrame::Execute {
                session_id: self.state.session_id.clone(),
                execution_id: execution_id.into(),
                command,
            })?)
            .await
            .map_err(ServerError::Io)
    }
    /// Sends a cancellation request without claiming cancellation completion.
    pub async fn send_cancel(
        &mut self,
        execution_id: impl Into<String>,
    ) -> Result<(), ServerError> {
        self.writer
            .write_all(&encode_frame(&ServerFrame::Cancel {
                session_id: self.state.session_id.clone(),
                execution_id: execution_id.into(),
            })?)
            .await
            .map_err(ServerError::Io)
    }
    /// Reads the next node fact; reconnects must negotiate a new session but reuse execution_id.
    pub async fn next_event(&mut self) -> Result<Option<ServerEvent>, ServerError> {
        let Some(line) = self.reader.next_line().await.map_err(ServerError::Io)? else {
            return Ok(None);
        };
        let frame: ClientFrame = decode_frame(line.as_bytes())?;
        match frame {
            ClientFrame::ExecutionEvent {
                session_id,
                execution_id,
                sequence,
                fact,
            } if session_id == self.state.session_id => Ok(Some(ServerEvent::ExecutionFact {
                session_id,
                execution_id,
                sequence,
                fact,
            })),
            ClientFrame::Heartbeat { .. } | ClientFrame::RegistrationUpdate { .. } => {
                Ok(Some(ServerEvent::ClientFact(frame)))
            }
            ClientFrame::ExecutionStatus {
                session_id,
                execution_id,
                fact,
            } if session_id == self.state.session_id => Ok(Some(ServerEvent::ExecutionFact {
                session_id,
                execution_id,
                sequence: 0,
                fact,
            })),
            other => Err(ServerError::Protocol(format!(
                "unexpected client frame: {other:?}"
            ))),
        }
    }
}

/// Owns one node session independently of the listener and all other nodes.
async fn run_session(mut session: ServerSession, events: mpsc::UnboundedSender<ServerEvent>) {
    let _ = events.send(ServerEvent::Registered {
        registration: session.registration().clone(),
        session_id: session.session_id().to_string(),
        lease_id: session.state.lease_id.clone(),
    });
    while let Ok(Some(event)) = session.next_event().await {
        if events.send(event).is_err() {
            break;
        }
    }
}

/// Server failure.
#[derive(Debug)]
pub enum ServerError {
    /// Listener or stream I/O failure.
    Io(std::io::Error),
    /// Protocol failure.
    Protocol(String),
    /// Wire validation failure.
    Wire(crate::protocol::ProtocolError),
}
impl Display for ServerError {
    /// Formats server failures.
    fn fmt(&self, f: &mut Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Io(e) => e.fmt(f),
            Self::Protocol(e) => f.write_str(e),
            Self::Wire(e) => e.fmt(f),
        }
    }
}
impl std::error::Error for ServerError {}
impl From<crate::protocol::ProtocolError> for ServerError {
    fn from(value: crate::protocol::ProtocolError) -> Self {
        Self::Wire(value)
    }
}
/// Reads one framed connector message.
async fn next<T: for<'de> serde::Deserialize<'de>>(
    lines: &mut tokio::io::Lines<BufReader<tokio::net::tcp::OwnedReadHalf>>,
) -> Result<T, ServerError> {
    let line = lines
        .next_line()
        .await
        .map_err(ServerError::Io)?
        .ok_or_else(|| ServerError::Protocol("connection closed".to_string()))?;
    decode_frame(line.as_bytes()).map_err(ServerError::Wire)
}
