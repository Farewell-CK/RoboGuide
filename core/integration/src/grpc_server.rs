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
    Registered(crate::grpc::v0_1::NodeRegistration),
    /// A heartbeat or registration update was received.
    NodeMessage(NodeMessage),
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
                lease_id,
            })),
        }))
        .map_err(|_| Status::unavailable("response stream closed"))?;
    router
        .sessions
        .lock()
        .map_err(|_| Status::internal("session registry unavailable"))?
        .insert(
            registration.node_id.clone(),
            RoutedSession {
                session_id: session_id.clone(),
                sender: outbound.clone(),
            },
        );
    let _ = events.send(GrpcNodeEvent::Registered(registration));
    while let Some(message) = inbound.next().await {
        let message = message?;
        let sequence = match &message.message {
            Some(NodePayload::Heartbeat(value)) => value.sequence,
            Some(NodePayload::RegistrationUpdate(value)) => value.sequence,
            Some(NodePayload::ExecutionEvent(value)) => value.sequence,
            Some(NodePayload::ExecutionSnapshot(value)) => value.last_sequence,
            _ => 0,
        };
        let _ = events.send(GrpcNodeEvent::NodeMessage(message));
        if sequence > 0 {
            let _ = outbound.send(Ok(ServerMessage {
                message: Some(ServerPayload::Ack(Ack { sequence })),
            }));
        }
    }
    Ok(())
}
