//! Formal gRPC Node Protocol server and concurrent session command routing.

use crate::grpc::v0_1::node_message::Message as NodePayload;
use crate::grpc::v0_1::robo_guide_node_protocol_server::RoboGuideNodeProtocol;
use crate::grpc::v0_1::server_message::Message as ServerPayload;
use crate::grpc::v0_1::{Ack, Cancel, Execute, NodeMessage, Registered, ServerMessage, Welcome};
use crate::protocol::{PROTOCOL_VERSION_V0_1, SERVER_VERSION_V0_1};
use std::collections::BTreeMap;
use std::pin::Pin;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use tokio::sync::mpsc;
use tokio_stream::{Stream, StreamExt, wrappers::UnboundedReceiverStream};
use tonic::{Request, Response, Status};

/// Events received from all formal gRPC Node sessions.
#[derive(Debug)]
pub enum GrpcNodeEvent {
    /// A node completed registration in a new session.
    Registered {
        /// New authoritative session identity.
        session_id: String,
        /// Server-issued lease identity.
        lease_id: String,
        /// Accepted node registration.
        registration: crate::grpc::v0_1::NodeRegistration,
    },
    /// A heartbeat or registration update was received.
    NodeMessage {
        /// Stable node identity owning the message.
        node_id: String,
        /// Session identity validated before emission.
        session_id: String,
        /// Validated node message.
        message: NodeMessage,
    },
    /// The current route disconnected or its lease expired.
    Unavailable {
        /// Node whose current route is unavailable.
        node_id: String,
        /// Fenced session identity.
        session_id: String,
    },
}

/// Cloneable command router for currently connected Node sessions.
#[derive(Clone, Default)]
pub struct GrpcNodeRouter {
    /// Node identities mapped to their current session and outbound stream.
    sessions: Arc<Mutex<BTreeMap<String, RoutedSession>>>,
}

/// Current command route for one connected node.
struct RoutedSession {
    /// Current session identity inserted into commands.
    session_id: String,
    /// Outbound stream producer.
    sender: mpsc::UnboundedSender<Result<ServerMessage, Status>>,
    /// Lease identity required on heartbeats.
    lease_id: String,
    /// Latest accepted heartbeat receive instant.
    last_heartbeat: std::time::Instant,
    /// Maximum heartbeat silence before routing is fenced.
    lease_duration: std::time::Duration,
}

impl GrpcNodeRouter {
    /// Sends a canonical Execute through the node's current session.
    pub fn execute(
        &self,
        node_id: &str,
        execution_id: String,
        invocation: crate::grpc::v0_1::CanonicalInvocation,
    ) -> Result<(), Status> {
        let sessions = self
            .sessions
            .lock()
            .map_err(|_| Status::internal("session registry unavailable"))?;
        let route = sessions
            .get(node_id)
            .ok_or_else(|| Status::unavailable("node is not connected"))?;
        if route.last_heartbeat.elapsed() >= route.lease_duration {
            return Err(Status::unavailable("node lease expired"));
        }
        route
            .sender
            .send(Ok(ServerMessage {
                message: Some(ServerPayload::Execute(Execute {
                    session_id: route.session_id.clone(),
                    execution_id,
                    invocation: Some(invocation),
                })),
            }))
            .map_err(|_| Status::unavailable("node session closed"))
    }

    /// Sends Cancel through the node's current session.
    pub fn cancel(&self, node_id: &str, execution_id: String) -> Result<(), Status> {
        let sessions = self
            .sessions
            .lock()
            .map_err(|_| Status::internal("session registry unavailable"))?;
        let route = sessions
            .get(node_id)
            .ok_or_else(|| Status::unavailable("node is not connected"))?;
        if route.last_heartbeat.elapsed() >= route.lease_duration {
            return Err(Status::unavailable("node lease expired"));
        }
        route
            .sender
            .send(Ok(ServerMessage {
                message: Some(ServerPayload::Cancel(Cancel {
                    session_id: route.session_id.clone(),
                    execution_id,
                })),
            }))
            .map_err(|_| Status::unavailable("node session closed"))
    }
}

/// Formal gRPC service implementation supporting concurrent bidirectional sessions.
pub struct GrpcIntegrationService {
    /// Current session command routes.
    router: GrpcNodeRouter,
    /// Cross-session event sink for Runtime composition.
    events: mpsc::UnboundedSender<GrpcNodeEvent>,
    /// Process-local unique session/lease source.
    next_session: Arc<AtomicU64>,
}

impl GrpcIntegrationService {
    /// Creates a service and returns its command router.
    pub fn new(events: mpsc::UnboundedSender<GrpcNodeEvent>) -> (Self, GrpcNodeRouter) {
        let router = GrpcNodeRouter::default();
        (
            Self {
                router: router.clone(),
                events,
                next_session: Arc::new(AtomicU64::new(1)),
            },
            router,
        )
    }
}

#[tonic::async_trait]
impl RoboGuideNodeProtocol for GrpcIntegrationService {
    type NodeSessionStream = Pin<Box<dyn Stream<Item = Result<ServerMessage, Status>> + Send>>;

    /// Negotiates and runs one independent bidirectional Node session.
    async fn node_session(
        &self,
        request: Request<tonic::Streaming<NodeMessage>>,
    ) -> Result<Response<Self::NodeSessionStream>, Status> {
        let (outbound, receiver) = mpsc::unbounded_channel();
        let inbound = request.into_inner();
        let router = self.router.clone();
        let events = self.events.clone();
        let identity = self.next_session.fetch_add(1, Ordering::Relaxed);
        tokio::spawn(async move {
            if let Err(status) =
                run_grpc_session(inbound, outbound.clone(), router, events, identity).await
            {
                let _ = outbound.send(Err(status));
            }
        });
        Ok(Response::new(Box::pin(UnboundedReceiverStream::new(
            receiver,
        ))))
    }
}

/// Negotiates and consumes one formal gRPC session after its response stream is live.
async fn run_grpc_session(
    mut inbound: tonic::Streaming<NodeMessage>,
    outbound: mpsc::UnboundedSender<Result<ServerMessage, Status>>,
    router: GrpcNodeRouter,
    events: mpsc::UnboundedSender<GrpcNodeEvent>,
    identity: u64,
) -> Result<(), Status> {
    let first = inbound
        .next()
        .await
        .ok_or_else(|| Status::invalid_argument("Hello required"))??;
    let Some(NodePayload::Hello(hello)) = first.message else {
        return Err(Status::invalid_argument("first message must be Hello"));
    };
    if !hello
        .protocol_versions
        .iter()
        .any(|version| version == PROTOCOL_VERSION_V0_1)
    {
        return Err(Status::failed_precondition(
            "no compatible protocol version",
        ));
    }
    let node_contract = hello
        .node_contract_versions
        .iter()
        .find(|version| version.as_str() == "roboguide.node.v0.1")
        .cloned()
        .ok_or_else(|| Status::failed_precondition("no compatible Node Contract version"))?;
    outbound
        .send(Ok(ServerMessage {
            message: Some(ServerPayload::Welcome(Welcome {
                server_version: SERVER_VERSION_V0_1.to_string(),
                selected_protocol_version: PROTOCOL_VERSION_V0_1.to_string(),
                selected_node_contract_version: node_contract.clone(),
                heartbeat_interval_ms: 5_000,
                lease_duration_ms: 15_000,
            })),
        }))
        .map_err(|_| Status::unavailable("response stream closed"))?;
    let second = inbound
        .next()
        .await
        .ok_or_else(|| Status::invalid_argument("Register required"))??;
    let Some(NodePayload::Register(register)) = second.message else {
        return Err(Status::invalid_argument("second message must be Register"));
    };
    let registration = register
        .registration
        .ok_or_else(|| Status::invalid_argument("registration required"))?;
    if registration.node_id != hello.node_id || registration.node_contract_version != node_contract
    {
        return Err(Status::invalid_argument(
            "registration differs from negotiation",
        ));
    }
    let session_id = format!("grpc-session-{identity}");
    let lease_id = format!("grpc-lease-{identity}");
    outbound
        .send(Ok(ServerMessage {
            message: Some(ServerPayload::Registered(Registered {
                session_id: session_id.clone(),
                lease_id: lease_id.clone(),
            })),
        }))
        .map_err(|_| Status::unavailable("response stream closed"))?;
    let previous = router
        .sessions
        .lock()
        .map_err(|_| Status::internal("session registry unavailable"))?
        .insert(
            registration.node_id.clone(),
            RoutedSession {
                session_id: session_id.clone(),
                sender: outbound.clone(),
                lease_id: lease_id.clone(),
                last_heartbeat: std::time::Instant::now(),
                lease_duration: std::time::Duration::from_millis(15_000),
            },
        );
    if let Some(previous) = previous {
        let _ = previous
            .sender
            .send(Err(Status::aborted("session superseded by reconnect")));
    }
    let node_id = registration.node_id.clone();
    let _ = events.send(GrpcNodeEvent::Registered {
        session_id: session_id.clone(),
        lease_id,
        registration,
    });
    let mut lease_check = tokio::time::interval(std::time::Duration::from_millis(250));
    loop {
        let message = tokio::select! {
            message = inbound.next() => match message { Some(message) => message?, None => break },
            _ = lease_check.tick() => {
                if route_is_expired(&router, &node_id, &session_id)? { break; }
                continue;
            }
        };
        if !accept_current_message(&router, &node_id, &session_id, &message)? {
            continue;
        }
        let sequence = match &message.message {
            Some(NodePayload::Heartbeat(value)) => value.sequence,
            Some(NodePayload::RegistrationUpdate(value)) => value.sequence,
            Some(NodePayload::ExecutionEvent(value)) => value.sequence,
            Some(NodePayload::ExecutionSnapshot(value)) => value.last_sequence,
            _ => 0,
        };
        let _ = events.send(GrpcNodeEvent::NodeMessage {
            node_id: node_id.clone(),
            session_id: session_id.clone(),
            message,
        });
        if sequence > 0 {
            let _ = outbound.send(Ok(ServerMessage {
                message: Some(ServerPayload::Ack(Ack { sequence })),
            }));
        }
    }
    let removed = {
        let mut sessions = router
            .sessions
            .lock()
            .map_err(|_| Status::internal("session registry unavailable"))?;
        if sessions
            .get(&node_id)
            .is_some_and(|route| route.session_id == session_id)
        {
            sessions.remove(&node_id);
            true
        } else {
            false
        }
    };
    if removed {
        let _ = events.send(GrpcNodeEvent::Unavailable {
            node_id,
            session_id,
        });
    }
    Ok(())
}

/// Returns whether the current route exceeded its heartbeat lease.
fn route_is_expired(
    router: &GrpcNodeRouter,
    node_id: &str,
    session_id: &str,
) -> Result<bool, Status> {
    let sessions = router
        .sessions
        .lock()
        .map_err(|_| Status::internal("session registry unavailable"))?;
    Ok(sessions.get(node_id).is_none_or(|route| {
        route.session_id != session_id || route.last_heartbeat.elapsed() >= route.lease_duration
    }))
}

/// Accepts only the current session and renews only its matching lease heartbeat.
fn accept_current_message(
    router: &GrpcNodeRouter,
    node_id: &str,
    session_id: &str,
    message: &NodeMessage,
) -> Result<bool, Status> {
    let mut sessions = router
        .sessions
        .lock()
        .map_err(|_| Status::internal("session registry unavailable"))?;
    let Some(route) = sessions.get_mut(node_id) else {
        return Ok(false);
    };
    if route.session_id != session_id {
        return Ok(false);
    }
    if let Some(NodePayload::Heartbeat(heartbeat)) = &message.message {
        if heartbeat.session_id != session_id || heartbeat.lease_id != route.lease_id {
            return Ok(false);
        }
        route.last_heartbeat = std::time::Instant::now();
    } else {
        let message_session = match &message.message {
            Some(NodePayload::RegistrationUpdate(value)) => &value.session_id,
            Some(NodePayload::ExecutionEvent(value)) => &value.session_id,
            Some(NodePayload::ExecutionSnapshot(value)) => &value.session_id,
            _ => return Ok(false),
        };
        if message_session != session_id {
            return Ok(false);
        }
    }
    Ok(true)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Expired leases cannot route Execute or Cancel.
    #[test]
    fn expired_lease_rejects_new_commands() {
        let router = GrpcNodeRouter::default();
        let (sender, _receiver) = mpsc::unbounded_channel();
        router.sessions.lock().expect("registry lock").insert(
            "dog-a".to_string(),
            RoutedSession {
                session_id: "session-old".to_string(),
                sender,
                lease_id: "lease-old".to_string(),
                last_heartbeat: std::time::Instant::now() - std::time::Duration::from_secs(2),
                lease_duration: std::time::Duration::from_secs(1),
            },
        );
        assert_eq!(
            router
                .execute(
                    "dog-a",
                    "execution-1".to_string(),
                    crate::grpc::v0_1::CanonicalInvocation::default()
                )
                .expect_err("expired route rejected")
                .code(),
            tonic::Code::Unavailable
        );
    }

    /// A late message from a fenced session cannot refresh the current route.
    #[test]
    fn newer_session_fences_late_old_heartbeat() {
        let router = GrpcNodeRouter::default();
        let (sender, _receiver) = mpsc::unbounded_channel();
        router.sessions.lock().expect("registry lock").insert(
            "dog-a".to_string(),
            RoutedSession {
                session_id: "session-new".to_string(),
                sender,
                lease_id: "lease-new".to_string(),
                last_heartbeat: std::time::Instant::now(),
                lease_duration: std::time::Duration::from_secs(15),
            },
        );
        let message = NodeMessage {
            message: Some(NodePayload::Heartbeat(crate::grpc::v0_1::Heartbeat {
                session_id: "session-old".to_string(),
                lease_id: "lease-old".to_string(),
                sequence: 10,
                status: None,
            })),
        };
        assert!(
            !accept_current_message(&router, "dog-a", "session-old", &message)
                .expect("message validates safely")
        );
    }
}
