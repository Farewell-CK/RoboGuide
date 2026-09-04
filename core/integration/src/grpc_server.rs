//! Formal gRPC Node Protocol server and concurrent session command routing.

use crate::grpc::v0_3::node_message::Message as NodePayload;
use crate::grpc::v0_3::robo_guide_node_protocol_server::RoboGuideNodeProtocol;
use crate::grpc::v0_3::server_message::Message as ServerPayload;
use crate::grpc::v0_3::{
    Ack, Cancel, Execute, NODE_CONTRACT_VERSION, NodeMessage, PROTOCOL_VERSION, Registered,
    ServerMessage, Welcome,
};
use std::collections::{BTreeMap, BTreeSet};
use std::pin::Pin;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use tokio::sync::{mpsc, oneshot};
use tokio_stream::{Stream, StreamExt, wrappers::UnboundedReceiverStream};
use tonic::{Request, Response, Status};

/// Current Integration Server implementation version.
const SERVER_VERSION: &str = "roboguide.server/v0.3";
/// Maximum time transport waits for Controller composition to durably accept one fact.
const APPLICATION_ACCEPTANCE_TIMEOUT: std::time::Duration = std::time::Duration::from_secs(30);

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
        registration: crate::grpc::v0_3::NodeRegistration,
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

/// One validated transport fact plus an optional application-acceptance response channel.
///
/// Integration does not interpret the decision. The Controller composition completes the
/// response only after its existing authorities and durable checkpoint accept the fact.
#[derive(Debug)]
pub struct GrpcNodeEventDelivery {
    /// Validated Node Protocol fact for Controller composition.
    event: GrpcNodeEvent,
    /// Response required before transport emits `Registered` or `Ack`.
    response: Option<oneshot::Sender<Result<(), ApplicationAcceptanceFailure>>>,
}

/// Application-level failure category preserved until it becomes a gRPC session status.
#[derive(Debug)]
enum ApplicationAcceptanceFailure {
    /// Existing application authority conclusively rejected the supplied fact.
    Rejected(String),
    /// Application acceptance could not be completed because its service became unavailable.
    Unavailable(String),
}

impl GrpcNodeEventDelivery {
    /// Splits the validated fact from the application-owned completion handle.
    pub fn into_parts(self) -> (GrpcNodeEvent, GrpcNodeEventCompletion) {
        (
            self.event,
            GrpcNodeEventCompletion {
                response: self.response,
            },
        )
    }

    /// Builds a fact whose remote peer is waiting for application acceptance.
    fn requiring_acceptance(
        event: GrpcNodeEvent,
        response: oneshot::Sender<Result<(), ApplicationAcceptanceFailure>>,
    ) -> Self {
        Self {
            event,
            response: Some(response),
        }
    }

    /// Builds a transport observation that has no remote acknowledgement.
    fn observation(event: GrpcNodeEvent) -> Self {
        Self {
            event,
            response: None,
        }
    }
}

/// Application-owned completion handle for one delivered Node Protocol fact.
#[derive(Debug)]
pub struct GrpcNodeEventCompletion {
    /// Pending transport response, absent for local unavailability observations.
    response: Option<oneshot::Sender<Result<(), ApplicationAcceptanceFailure>>>,
}

impl GrpcNodeEventCompletion {
    /// Confirms transport acceptance after application processing and persistence finish.
    pub fn accept(self) {
        self.complete(Ok(()));
    }

    /// Reports a conclusive application-authority rejection to the remote Node.
    pub fn reject(self, reason: impl Into<String>) {
        self.complete(Err(ApplicationAcceptanceFailure::Rejected(reason.into())));
    }

    /// Reports that application acceptance could not be completed reliably.
    pub fn unavailable(self, reason: impl Into<String>) {
        self.complete(Err(ApplicationAcceptanceFailure::Unavailable(
            reason.into(),
        )));
    }

    /// Sends one typed application decision when the remote session is still waiting.
    fn complete(mut self, result: Result<(), ApplicationAcceptanceFailure>) {
        if let Some(response) = self.response.take() {
            let _ = response.send(result);
        }
    }
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
    /// Last accepted management sequence for heartbeat/registration updates.
    management_sequence: u64,
    /// State export identities accepted in the latest complete registration snapshot.
    state_export_ids: BTreeSet<String>,
    /// Whether Controller composition accepted registration and `Registered` was emitted.
    active: bool,
}

impl GrpcNodeRouter {
    /// Returns whether a route still belongs to the supplied session, regardless of lease state.
    pub fn session_matches(&self, node_id: &str, session_id: &str) -> Result<bool, Status> {
        let sessions = self
            .sessions
            .lock()
            .map_err(|_| Status::internal("session registry unavailable"))?;
        Ok(sessions
            .get(node_id)
            .is_some_and(|route| route.session_id == session_id))
    }

    /// Returns whether any route currently exists for a Node.
    pub fn has_session(&self, node_id: &str) -> Result<bool, Status> {
        let sessions = self
            .sessions
            .lock()
            .map_err(|_| Status::internal("session registry unavailable"))?;
        Ok(sessions.contains_key(node_id))
    }

    /// Returns whether one identity names the active, unexpired session for a Node.
    pub fn session_is_current(&self, node_id: &str, session_id: &str) -> Result<bool, Status> {
        let sessions = self
            .sessions
            .lock()
            .map_err(|_| Status::internal("session registry unavailable"))?;
        Ok(sessions.get(node_id).is_some_and(|route| {
            route.active
                && route.session_id == session_id
                && route.last_heartbeat.elapsed() < route.lease_duration
        }))
    }

    /// Sends a canonical Execute through the node's current session.
    pub fn execute(
        &self,
        node_id: &str,
        execution_id: String,
        invocation: crate::grpc::v0_3::CanonicalInvocation,
        resource_ids: Vec<String>,
    ) -> Result<(), Status> {
        if execution_id.trim().is_empty()
            || invocation.mission_id.trim().is_empty()
            || invocation.task_id.trim().is_empty()
            || invocation.group_id.trim().is_empty()
            || invocation.role_id.trim().is_empty()
            || invocation.capability_contract.trim().is_empty()
        {
            return Err(Status::invalid_argument(
                "Execute identity and canonical invocation fields must be nonblank",
            ));
        }
        let resource_set = resource_ids.iter().collect::<BTreeSet<_>>();
        if resource_set.len() != resource_ids.len()
            || resource_ids
                .iter()
                .any(|resource_id| resource_id.trim().is_empty())
        {
            return Err(Status::invalid_argument(
                "Execute resource IDs must be nonblank and unique",
            ));
        }
        let sessions = self
            .sessions
            .lock()
            .map_err(|_| Status::internal("session registry unavailable"))?;
        let route = sessions
            .get(node_id)
            .ok_or_else(|| Status::unavailable("node is not connected"))?;
        if !route.active {
            return Err(Status::unavailable("node registration is pending"));
        }
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
                    resource_ids,
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
        if !route.active {
            return Err(Status::unavailable("node registration is pending"));
        }
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
    events: mpsc::UnboundedSender<GrpcNodeEventDelivery>,
    /// Process-local unique session/lease source.
    next_session: Arc<AtomicU64>,
    /// Boot-unique nonce preventing session identity reuse after process restart.
    boot_nonce: String,
}

/// Legacy v0.2 service endpoint retained only to return an explicit migration diagnostic.
#[derive(Debug, Clone, Copy, Default)]
pub struct GrpcLegacyV02Service;

#[tonic::async_trait]
impl crate::grpc::v0_2::robo_guide_node_protocol_server::RoboGuideNodeProtocol
    for GrpcLegacyV02Service
{
    type NodeSessionStream = Pin<
        Box<dyn Stream<Item = Result<crate::grpc::v0_2::ServerMessage, Status>> + Send + 'static>,
    >;

    /// Rejects v0.2 sessions because State/Memory declarations require Protocol v0.3.
    async fn node_session(
        &self,
        _request: Request<tonic::Streaming<crate::grpc::v0_2::NodeMessage>>,
    ) -> Result<Response<Self::NodeSessionStream>, Status> {
        Err(Status::failed_precondition(
            "Node Protocol v0.2 is retired; configure roboguide.node-protocol/v0.3 and roboguide.node.v0.3",
        ))
    }
}

impl GrpcIntegrationService {
    /// Creates a service and returns its command router.
    pub fn new(events: mpsc::UnboundedSender<GrpcNodeEventDelivery>) -> (Self, GrpcNodeRouter) {
        let router = GrpcNodeRouter::default();
        (
            Self {
                router: router.clone(),
                events,
                next_session: Arc::new(AtomicU64::new(1)),
                boot_nonce: format!(
                    "{}-{}",
                    std::process::id(),
                    std::time::SystemTime::now()
                        .duration_since(std::time::UNIX_EPOCH)
                        .map(|duration| duration.as_nanos())
                        .unwrap_or_default()
                ),
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
        let boot_nonce = self.boot_nonce.clone();
        tokio::spawn(async move {
            if let Err(status) = run_grpc_session(
                inbound,
                outbound.clone(),
                router,
                events,
                identity,
                boot_nonce,
            )
            .await
            {
                eprintln!("RoboGuide gRPC node session {identity} ended: {status}");
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
    events: mpsc::UnboundedSender<GrpcNodeEventDelivery>,
    identity: u64,
    boot_nonce: String,
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
        .any(|version| version == PROTOCOL_VERSION)
    {
        return Err(Status::failed_precondition(
            "no compatible protocol version",
        ));
    }
    let node_contract = hello
        .node_contract_versions
        .iter()
        .find(|version| version.as_str() == NODE_CONTRACT_VERSION)
        .cloned()
        .ok_or_else(|| Status::failed_precondition("no compatible Node Contract version"))?;
    outbound
        .send(Ok(ServerMessage {
            message: Some(ServerPayload::Welcome(Welcome {
                server_version: SERVER_VERSION.to_string(),
                selected_protocol_version: PROTOCOL_VERSION.to_string(),
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
    validate_registration(&registration)?;
    let session_id = format!("grpc-session-{boot_nonce}-{identity}");
    let lease_id = format!("grpc-lease-{boot_nonce}-{identity}");
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
                management_sequence: 0,
                state_export_ids: registration
                    .state_exports
                    .iter()
                    .map(|export| export.export_id.clone())
                    .collect(),
                active: false,
            },
        );
    if let Some(previous) = previous {
        let _ = previous
            .sender
            .send(Err(Status::aborted("session superseded by reconnect")));
    }
    let node_id = registration.node_id.clone();
    let registration_result = deliver_for_acceptance(
        &events,
        GrpcNodeEvent::Registered {
            session_id: session_id.clone(),
            lease_id: lease_id.clone(),
            registration,
        },
    )
    .await;
    if let Err(status) = registration_result {
        let removed = remove_current_route(&router, &node_id, &session_id)?;
        if removed && status.code() != tonic::Code::FailedPrecondition {
            emit_unavailable(&events, node_id.clone(), session_id.clone());
        }
        return Err(status);
    }
    if let Err(status) = activate_current_route(&router, &node_id, &session_id, &lease_id) {
        let removed = remove_current_route(&router, &node_id, &session_id)?;
        if removed {
            emit_unavailable(&events, node_id.clone(), session_id.clone());
        }
        return Err(status);
    }
    // Keep reading and validating the transport stream while Controller application acceptance
    // may be slow. ACKs are emitted only by the serial application loop after durable acceptance.
    let (validated_sender, mut validated_receiver) =
        mpsc::unbounded_channel::<Result<NodeMessage, Status>>();
    let reader_router = router.clone();
    let reader_node_id = node_id.clone();
    let reader_session_id = session_id.clone();
    let reader_task = tokio::spawn(async move {
        let mut lease_check = tokio::time::interval(std::time::Duration::from_millis(250));
        loop {
            let message = tokio::select! {
                message = inbound.next() => match message {
                    Some(Ok(message)) => message,
                    Some(Err(error)) => {
                        let _ = validated_sender.send(Err(error));
                        break;
                    }
                    None => break,
                },
                _ = lease_check.tick() => {
                    match route_is_expired(&reader_router, &reader_node_id, &reader_session_id) {
                        Ok(true) => {
                            let _ = validated_sender.send(Err(Status::deadline_exceeded(
                                "node heartbeat lease expired",
                            )));
                            break;
                        }
                        Ok(false) => continue,
                        Err(error) => {
                            let _ = validated_sender.send(Err(error));
                            break;
                        }
                    }
                }
            };
            match reader_router.session_matches(&reader_node_id, &reader_session_id) {
                Ok(true) => {}
                Ok(false) => {
                    let _ = validated_sender
                        .send(Err(Status::aborted("session superseded by reconnect")));
                    break;
                }
                Err(error) => {
                    let _ = validated_sender.send(Err(error));
                    break;
                }
            }
            match accept_current_message(
                &reader_router,
                &reader_node_id,
                &reader_session_id,
                &message,
            ) {
                Ok(true) => {}
                Ok(false) => continue,
                Err(error) => {
                    let _ = validated_sender.send(Err(error));
                    break;
                }
            }
            if validated_sender.send(Ok(message)).is_err() {
                break;
            }
        }
    });
    let session_result: Result<(), Status> = async {
        while let Some(message) = validated_receiver.recv().await {
            let message = message?;
            let sequence = match &message.message {
                Some(NodePayload::Heartbeat(value)) => value.sequence,
                Some(NodePayload::RegistrationUpdate(value)) => value.sequence,
                Some(NodePayload::StateObservationBatch(value)) => value.sequence,
                Some(NodePayload::ExecutionEvent(value)) => value.sequence,
                Some(NodePayload::ExecutionSnapshot(value)) => value.last_sequence,
                _ => 0,
            };
            let delivery = deliver_for_acceptance(
                &events,
                GrpcNodeEvent::NodeMessage {
                    node_id: node_id.clone(),
                    session_id: session_id.clone(),
                    message,
                },
            )
            .await;
            delivery?;
            if sequence > 0 {
                let _ = outbound.send(Ok(ServerMessage {
                    message: Some(ServerPayload::Ack(Ack { sequence })),
                }));
            }
        }
        Ok(())
    }
    .await;
    reader_task.abort();
    let removed = remove_current_route(&router, &node_id, &session_id)?;
    if removed {
        emit_unavailable(&events, node_id, session_id);
    }
    session_result
}

/// Emits one local route-loss observation without requiring a remote acknowledgement.
fn emit_unavailable(
    events: &mpsc::UnboundedSender<GrpcNodeEventDelivery>,
    node_id: String,
    session_id: String,
) {
    let _ = events.send(GrpcNodeEventDelivery::observation(
        GrpcNodeEvent::Unavailable {
            node_id,
            session_id,
        },
    ));
}

/// Delivers one validated fact and waits for application authority plus persistence acceptance.
async fn deliver_for_acceptance(
    events: &mpsc::UnboundedSender<GrpcNodeEventDelivery>,
    event: GrpcNodeEvent,
) -> Result<(), Status> {
    let (response, decision) = oneshot::channel();
    events
        .send(GrpcNodeEventDelivery::requiring_acceptance(event, response))
        .map_err(|_| Status::unavailable("Controller fact consumer is closed"))?;
    let decision = tokio::time::timeout(APPLICATION_ACCEPTANCE_TIMEOUT, decision)
        .await
        .map_err(|_| Status::deadline_exceeded("Controller fact acceptance timed out"))?
        .map_err(|_| Status::unavailable("Controller fact response was dropped"))?;
    decision.map_err(|failure| match failure {
        ApplicationAcceptanceFailure::Rejected(reason) => {
            Status::failed_precondition(format!("Controller rejected fact: {reason}"))
        }
        ApplicationAcceptanceFailure::Unavailable(reason) => {
            Status::unavailable(format!("Controller fact acceptance unavailable: {reason}"))
        }
    })
}

/// Activates one accepted session and emits `Registered` before commands can be routed.
fn activate_current_route(
    router: &GrpcNodeRouter,
    node_id: &str,
    session_id: &str,
    lease_id: &str,
) -> Result<(), Status> {
    let mut sessions = router
        .sessions
        .lock()
        .map_err(|_| Status::internal("session registry unavailable"))?;
    let route = sessions
        .get_mut(node_id)
        .filter(|route| route.session_id == session_id)
        .ok_or_else(|| Status::aborted("registration session was superseded"))?;
    route
        .sender
        .send(Ok(ServerMessage {
            message: Some(ServerPayload::Registered(Registered {
                session_id: session_id.to_string(),
                lease_id: lease_id.to_string(),
            })),
        }))
        .map_err(|_| Status::unavailable("response stream closed"))?;
    route.last_heartbeat = std::time::Instant::now();
    route.active = true;
    Ok(())
}

/// Removes a route only when it is still owned by the supplied session.
fn remove_current_route(
    router: &GrpcNodeRouter,
    node_id: &str,
    session_id: &str,
) -> Result<bool, Status> {
    let mut sessions = router
        .sessions
        .lock()
        .map_err(|_| Status::internal("session registry unavailable"))?;
    if sessions
        .get(node_id)
        .is_some_and(|route| route.session_id == session_id)
    {
        sessions.remove(node_id);
        Ok(true)
    } else {
        Ok(false)
    }
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
        if heartbeat.sequence <= route.management_sequence {
            return Ok(false);
        }
        route.management_sequence = heartbeat.sequence;
        route.last_heartbeat = std::time::Instant::now();
    } else {
        let message_session = match &message.message {
            Some(NodePayload::RegistrationUpdate(value)) => {
                if value.session_id != session_id {
                    return Ok(false);
                }
                if value.sequence <= route.management_sequence {
                    return Ok(false);
                }
                let registration = value.registration.as_ref().ok_or_else(|| {
                    Status::invalid_argument("RegistrationUpdate requires registration")
                })?;
                if registration.node_id != node_id
                    || registration.node_contract_version != NODE_CONTRACT_VERSION
                {
                    return Ok(false);
                }
                validate_registration(registration)?;
                route.management_sequence = value.sequence;
                route.state_export_ids = registration
                    .state_exports
                    .iter()
                    .map(|export| export.export_id.clone())
                    .collect();
                &value.session_id
            }
            Some(NodePayload::StateObservationBatch(value)) => {
                if value.session_id != session_id || value.sequence <= route.management_sequence {
                    return Ok(false);
                }
                validate_state_observation_batch(value, &route.state_export_ids)?;
                route.management_sequence = value.sequence;
                &value.session_id
            }
            Some(NodePayload::ExecutionEvent(value)) => &value.session_id,
            Some(NodePayload::ExecutionSnapshot(value)) => &value.session_id,
            Some(NodePayload::Error(value)) => &value.session_id,
            _ => return Ok(false),
        };
        if message_session != session_id {
            return Ok(false);
        }
    }
    Ok(true)
}

/// Validates complete v0.3 ownership without inferring Local How on the Server.
fn validate_registration(registration: &crate::grpc::v0_3::NodeRegistration) -> Result<(), Status> {
    if registration.node_id.trim().is_empty()
        || registration.node_contract_version != NODE_CONTRACT_VERSION
    {
        return Err(Status::invalid_argument(
            "registration node and contract identities are invalid",
        ));
    }
    let mut local_system_ids = BTreeSet::new();
    for local_system in &registration.local_systems {
        if local_system.id.trim().is_empty() || !local_system_ids.insert(local_system.id.as_str()) {
            return Err(Status::invalid_argument(
                "local system IDs must be nonblank and unique",
            ));
        }
        let runtime = local_system
            .runtime
            .as_ref()
            .ok_or_else(|| Status::invalid_argument("local system runtime is required"))?;
        if runtime.name.trim().is_empty() || runtime.version.trim().is_empty() {
            return Err(Status::invalid_argument(
                "local runtime name and version must be nonblank",
            ));
        }
    }
    if local_system_ids.is_empty() {
        return Err(Status::invalid_argument(
            "registration requires at least one local system",
        ));
    }
    let mut contracts = BTreeSet::new();
    for capability in &registration.capabilities {
        require_known_owner(&capability.local_system_id, &local_system_ids)?;
        if !matches!(
            capability.kind.as_str(),
            "mobility" | "transport" | "compute" | "observation"
        ) || capability.contracts.is_empty()
        {
            return Err(Status::invalid_argument(
                "capability kind is unsupported or has no canonical contracts",
            ));
        }
        for contract in &capability.contracts {
            if !valid_contract_identity(contract) || !contracts.insert(contract) {
                return Err(Status::invalid_argument(
                    "canonical capability contracts must have one unique owner",
                ));
            }
        }
    }
    let mut sensor_ids = BTreeSet::new();
    for sensor in &registration.sensors {
        require_known_owner(&sensor.local_system_id, &local_system_ids)?;
        if sensor.id.trim().is_empty()
            || sensor.kind.trim().is_empty()
            || !sensor_ids.insert(&sensor.id)
        {
            return Err(Status::invalid_argument(
                "sensor IDs must be nonblank and unique",
            ));
        }
    }
    let mut resource_ids = BTreeSet::new();
    for resource in &registration.resources {
        require_known_owner(&resource.local_system_id, &local_system_ids)?;
        if resource.id.trim().is_empty()
            || !matches!(resource.kind.as_str(), "space" | "compute" | "time")
            || resource.capacity == 0
            || !resource_ids.insert(&resource.id)
        {
            return Err(Status::invalid_argument(
                "resource IDs must be unique with positive capacity",
            ));
        }
    }
    let mut export_ids = BTreeSet::new();
    for export in &registration.state_exports {
        require_known_owner(&export.local_system_id, &local_system_ids)?;
        if export.export_id.trim().is_empty()
            || !export_ids.insert(export.export_id.as_str())
            || export.object_type.trim().is_empty()
            || export.object_id.trim().is_empty()
            || export.payload_schema.trim().is_empty()
            || export.valid_for_ms == 0
            || !matches!(export.object_class, 1 | 2)
            || !matches!(export.semantic, 3 | 4)
        {
            return Err(Status::invalid_argument(
                "State exports must be unique node/world Reported/Observed channels with schemas and positive validity",
            ));
        }
    }
    let mut provider_ids = BTreeSet::new();
    for provider in &registration.memory_providers {
        require_known_owner(&provider.local_system_id, &local_system_ids)?;
        let scope_valid = matches!(provider.scope, 1 | 3) && provider.execution_group_id.is_empty();
        if provider.provider_id.trim().is_empty()
            || !provider_ids.insert(provider.provider_id.as_str())
            || !matches!(provider.kind, 1..=5)
            || !scope_valid
            || !matches!(provider.visibility, 1 | 2)
            || provider.payload_schema.trim().is_empty()
            || provider.media_type.trim().is_empty()
        {
            return Err(Status::invalid_argument(
                "Memory providers must have unique identities, local/global maximum scope, known kinds/visibility, and nonblank schemas",
            ));
        }
    }
    Ok(())
}

/// Validates one bounded State batch against the current registration snapshot.
fn validate_state_observation_batch(
    batch: &crate::grpc::v0_3::StateObservationBatch,
    export_ids: &BTreeSet<String>,
) -> Result<(), Status> {
    const MAX_BATCH_RECORDS: usize = 64;
    const MAX_RECORD_BYTES: usize = 64 * 1024;
    const MAX_BATCH_BYTES: usize = 512 * 1024;

    if batch.observations.is_empty() || batch.observations.len() > MAX_BATCH_RECORDS {
        return Err(Status::invalid_argument(
            "State observation batch must contain 1 through 64 records",
        ));
    }
    let mut observed_exports = BTreeSet::new();
    let mut total_bytes = 0usize;
    for observation in &batch.observations {
        total_bytes = total_bytes.saturating_add(observation.json_value.len());
        if !export_ids.contains(&observation.export_id)
            || !observed_exports.insert(observation.export_id.as_str())
            || observation.json_value.len() > MAX_RECORD_BYTES
            || observation.has_confidence && observation.confidence_millionths > 1_000_000
            || serde_json::from_slice::<serde_json::Value>(&observation.json_value).is_err()
        {
            return Err(Status::invalid_argument(
                "State observations must reference unique registered exports and contain bounded valid JSON",
            ));
        }
    }
    if total_bytes > MAX_BATCH_BYTES {
        return Err(Status::invalid_argument(
            "State observation batch exceeds 512 KiB",
        ));
    }
    Ok(())
}

/// Validates the extensible `namespace.name@version` canonical identity shape.
fn valid_contract_identity(contract: &str) -> bool {
    contract
        .rsplit_once('@')
        .and_then(|(name, version)| name.rsplit_once('.').map(|parts| (parts, version)))
        .is_some_and(|((namespace, name), version)| {
            !namespace.trim().is_empty()
                && !name.trim().is_empty()
                && !version.trim().is_empty()
                && namespace
                    .split('.')
                    .all(|segment| !segment.is_empty() && !segment.chars().any(char::is_whitespace))
                && !namespace.contains('@')
                && !name.contains(['.', '@'])
                && !name.chars().any(char::is_whitespace)
                && !version.contains('@')
                && !version.chars().any(char::is_whitespace)
        })
}

/// Rejects a declaration whose owner is absent from the registration snapshot.
fn require_known_owner(owner: &str, known: &BTreeSet<&str>) -> Result<(), Status> {
    if owner.trim().is_empty() || !known.contains(owner) {
        return Err(Status::invalid_argument(
            "declaration references an unknown local system",
        ));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Registration accepts static local/global provider maxima and rejects a concrete Group scope.
    #[test]
    fn registration_rejects_execution_group_memory_provider_scope() {
        let mut registration = crate::grpc::v0_3::NodeRegistration {
            node_id: "dog-a".to_string(),
            local_systems: vec![crate::grpc::v0_3::LocalSystemDescriptor {
                id: "memory".to_string(),
                runtime: Some(crate::grpc::v0_3::LocalRuntime {
                    name: "memory-runtime".to_string(),
                    version: "1".to_string(),
                }),
                metadata: Default::default(),
            }],
            capabilities: Vec::new(),
            sensors: Vec::new(),
            resources: Vec::new(),
            metadata: Default::default(),
            node_contract_version: NODE_CONTRACT_VERSION.to_string(),
            state_exports: Vec::new(),
            memory_providers: vec![crate::grpc::v0_3::MemoryProviderDescriptor {
                provider_id: "experience".to_string(),
                local_system_id: "memory".to_string(),
                kind: crate::grpc::v0_3::MemoryKind::Experience as i32,
                scope: crate::grpc::v0_3::MemoryScopeKind::Global as i32,
                execution_group_id: String::new(),
                visibility: crate::grpc::v0_3::MemoryVisibility::Discoverable as i32,
                payload_schema: "example.experience/v1".to_string(),
                media_type: "application/json".to_string(),
            }],
        };
        validate_registration(&registration).expect("global provider maximum should be valid");

        registration.memory_providers[0].scope =
            crate::grpc::v0_3::MemoryScopeKind::ExecutionGroup as i32;
        registration.memory_providers[0].execution_group_id = "group-a".to_string();
        let error = validate_registration(&registration)
            .expect_err("static execution Group provider scope should be rejected");
        assert_eq!(error.code(), tonic::Code::InvalidArgument);
    }

    /// Builds one registered-export set for State batch validation tests.
    fn state_exports() -> BTreeSet<String> {
        BTreeSet::from(["hazard-state".to_string(), "contact-state".to_string()])
    }

    /// A bounded batch may carry independent valid JSON observations for registered exports.
    #[test]
    fn state_batch_accepts_registered_bounded_json() {
        let batch = crate::grpc::v0_3::StateObservationBatch {
            session_id: "session-a".to_string(),
            sequence: 2,
            observations: vec![
                crate::grpc::v0_3::StateObservation {
                    export_id: "hazard-state".to_string(),
                    json_value: br#"{"present":true}"#.to_vec(),
                    has_source_observed_at: true,
                    source_observed_at_ms: 10,
                    has_confidence: true,
                    confidence_millionths: 900_000,
                },
                crate::grpc::v0_3::StateObservation {
                    export_id: "contact-state".to_string(),
                    json_value: br#"{"connected":false}"#.to_vec(),
                    has_source_observed_at: false,
                    source_observed_at_ms: 0,
                    has_confidence: false,
                    confidence_millionths: 0,
                },
            ],
        };

        validate_state_observation_batch(&batch, &state_exports())
            .expect("registered bounded observations should be accepted");
    }

    /// State batches cannot smuggle undeclared channels or duplicate one channel in a batch.
    #[test]
    fn state_batch_rejects_undeclared_and_duplicate_exports() {
        let observation = crate::grpc::v0_3::StateObservation {
            export_id: "unknown-state".to_string(),
            json_value: b"true".to_vec(),
            has_source_observed_at: false,
            source_observed_at_ms: 0,
            has_confidence: false,
            confidence_millionths: 0,
        };
        let undeclared = crate::grpc::v0_3::StateObservationBatch {
            session_id: "session-a".to_string(),
            sequence: 2,
            observations: vec![observation],
        };
        assert_eq!(
            validate_state_observation_batch(&undeclared, &state_exports())
                .expect_err("undeclared export should be rejected")
                .code(),
            tonic::Code::InvalidArgument
        );

        let observation = crate::grpc::v0_3::StateObservation {
            export_id: "hazard-state".to_string(),
            json_value: b"true".to_vec(),
            has_source_observed_at: false,
            source_observed_at_ms: 0,
            has_confidence: false,
            confidence_millionths: 0,
        };
        let duplicate = crate::grpc::v0_3::StateObservationBatch {
            session_id: "session-a".to_string(),
            sequence: 2,
            observations: vec![observation.clone(), observation],
        };
        assert_eq!(
            validate_state_observation_batch(&duplicate, &state_exports())
                .expect_err("duplicate export should be rejected")
                .code(),
            tonic::Code::InvalidArgument
        );
    }

    /// Invalid JSON, oversized payloads, and invalid confidence fail at the protocol boundary.
    #[test]
    fn state_batch_rejects_invalid_payload_bounds() {
        for (json_value, has_confidence, confidence) in [
            (b"not-json".to_vec(), false, 0),
            (vec![b' '; 64 * 1024 + 1], false, 0),
            (b"true".to_vec(), true, 1_000_001),
        ] {
            let batch = crate::grpc::v0_3::StateObservationBatch {
                session_id: "session-a".to_string(),
                sequence: 2,
                observations: vec![crate::grpc::v0_3::StateObservation {
                    export_id: "hazard-state".to_string(),
                    json_value,
                    has_source_observed_at: false,
                    source_observed_at_ms: 0,
                    has_confidence,
                    confidence_millionths: confidence,
                }],
            };
            assert_eq!(
                validate_state_observation_batch(&batch, &state_exports())
                    .expect_err("invalid State payload should be rejected")
                    .code(),
                tonic::Code::InvalidArgument
            );
        }
    }

    /// Transport emits success only after application authority explicitly accepts the fact.
    #[tokio::test]
    async fn fact_delivery_waits_for_application_acceptance() {
        let (events, mut receiver) = mpsc::unbounded_channel();
        let delivery = tokio::spawn(async move {
            deliver_for_acceptance(
                &events,
                GrpcNodeEvent::Unavailable {
                    node_id: "dog-a".to_string(),
                    session_id: "session-a".to_string(),
                },
            )
            .await
        });
        let event = receiver.recv().await.expect("fact delivery exists");
        assert!(!delivery.is_finished());
        let (_event, completion) = event.into_parts();
        completion.accept();
        delivery
            .await
            .expect("delivery task joins")
            .expect("application acceptance reaches transport");
    }

    /// Application rejection is returned as a protocol failure instead of a false acknowledgement.
    #[tokio::test]
    async fn fact_delivery_preserves_application_rejection() {
        let (events, mut receiver) = mpsc::unbounded_channel();
        let delivery = tokio::spawn(async move {
            deliver_for_acceptance(
                &events,
                GrpcNodeEvent::Unavailable {
                    node_id: "dog-a".to_string(),
                    session_id: "session-a".to_string(),
                },
            )
            .await
        });
        let event = receiver.recv().await.expect("fact delivery exists");
        let (_event, completion) = event.into_parts();
        completion.reject("resource conflict");
        let error = delivery
            .await
            .expect("delivery task joins")
            .expect_err("application rejection reaches transport");
        assert_eq!(error.code(), tonic::Code::FailedPrecondition);
        assert!(error.message().contains("resource conflict"));
    }

    /// Application infrastructure failure stays retryable instead of becoming a fact rejection.
    #[tokio::test]
    async fn fact_delivery_preserves_application_unavailability() {
        let (events, mut receiver) = mpsc::unbounded_channel();
        let delivery = tokio::spawn(async move {
            deliver_for_acceptance(
                &events,
                GrpcNodeEvent::Unavailable {
                    node_id: "dog-a".to_string(),
                    session_id: "session-a".to_string(),
                },
            )
            .await
        });
        let event = receiver.recv().await.expect("fact delivery exists");
        let (_event, completion) = event.into_parts();
        completion.unavailable("checkpoint store is offline");
        let error = delivery
            .await
            .expect("delivery task joins")
            .expect_err("application unavailability reaches transport");
        assert_eq!(error.code(), tonic::Code::Unavailable);
        assert!(error.message().contains("checkpoint store is offline"));
    }

    /// A session cannot receive commands before Controller application registration acceptance.
    #[test]
    fn pending_registration_cannot_route_commands() {
        let router = GrpcNodeRouter::default();
        let (sender, _receiver) = mpsc::unbounded_channel();
        router.sessions.lock().expect("registry lock").insert(
            "dog-a".to_string(),
            RoutedSession {
                session_id: "session-pending".to_string(),
                sender,
                lease_id: "lease-pending".to_string(),
                last_heartbeat: std::time::Instant::now(),
                lease_duration: std::time::Duration::from_secs(15),
                management_sequence: 0,
                state_export_ids: BTreeSet::new(),
                active: false,
            },
        );
        let error = router
            .execute(
                "dog-a",
                "execution-1".to_string(),
                crate::grpc::v0_3::CanonicalInvocation {
                    mission_id: "m".to_string(),
                    task_id: "t".to_string(),
                    group_id: "g".to_string(),
                    role_id: "r".to_string(),
                    capability_contract: "compute.noop@v1".to_string(),
                    parameters: Default::default(),
                },
                Vec::new(),
            )
            .expect_err("pending route rejects commands");
        assert_eq!(error.code(), tonic::Code::Unavailable);
        assert!(error.message().contains("pending"));
    }

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
                management_sequence: 0,
                state_export_ids: BTreeSet::new(),
                active: true,
            },
        );
        assert_eq!(
            router
                .execute(
                    "dog-a",
                    "execution-1".to_string(),
                    crate::grpc::v0_3::CanonicalInvocation {
                        mission_id: "m".to_string(),
                        task_id: "t".to_string(),
                        group_id: "g".to_string(),
                        role_id: "r".to_string(),
                        capability_contract: "compute.noop@v1".to_string(),
                        parameters: Default::default(),
                    },
                    Vec::new(),
                )
                .expect_err("expired route rejected")
                .code(),
            tonic::Code::Unavailable
        );
    }

    /// Memory data-plane callers can identify only the active, unexpired route for one Node.
    #[test]
    fn current_session_check_rejects_pending_wrong_and_expired_routes() {
        let router = GrpcNodeRouter::default();
        let (sender, _receiver) = mpsc::unbounded_channel();
        router.sessions.lock().expect("registry lock").insert(
            "dog-a".to_string(),
            RoutedSession {
                session_id: "session-current".to_string(),
                sender,
                lease_id: "lease-current".to_string(),
                last_heartbeat: std::time::Instant::now(),
                lease_duration: std::time::Duration::from_secs(15),
                management_sequence: 0,
                state_export_ids: BTreeSet::new(),
                active: false,
            },
        );
        assert!(
            !router
                .session_is_current("dog-a", "session-current")
                .expect("pending route can be inspected")
        );

        {
            let mut sessions = router.sessions.lock().expect("registry lock");
            sessions.get_mut("dog-a").expect("route exists").active = true;
        }
        assert!(
            router
                .session_is_current("dog-a", "session-current")
                .expect("active route can be inspected")
        );
        assert!(
            !router
                .session_is_current("dog-a", "session-old")
                .expect("stale identity can be inspected")
        );
        assert!(
            !router
                .session_is_current("dog-b", "session-current")
                .expect("unknown Node can be inspected")
        );

        {
            let mut sessions = router.sessions.lock().expect("registry lock");
            let route = sessions.get_mut("dog-a").expect("route exists");
            route.last_heartbeat = std::time::Instant::now() - std::time::Duration::from_secs(16);
        }
        assert!(
            !router
                .session_is_current("dog-a", "session-current")
                .expect("expired route can be inspected")
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
                management_sequence: 0,
                state_export_ids: BTreeSet::new(),
                active: true,
            },
        );
        let message = NodeMessage {
            message: Some(NodePayload::Heartbeat(crate::grpc::v0_3::Heartbeat {
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
