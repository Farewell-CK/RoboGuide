//! Formal gRPC Node Protocol server and concurrent session command routing.

use crate::grpc::v0_4::node_message::Message as NodePayload;
use crate::grpc::v0_4::robo_guide_node_protocol_server::RoboGuideNodeProtocol;
use crate::grpc::v0_4::server_message::Message as ServerPayload;
use crate::grpc::v0_4::{
    Ack, Cancel, Execute, LEGACY_NODE_CONTRACT_VERSION, NODE_CONTRACT_VERSION, NodeMessage,
    PROTOCOL_VERSION, Registered, ServerMessage, Welcome,
};
use std::collections::{BTreeMap, BTreeSet};
use std::pin::Pin;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use tokio::sync::{mpsc, oneshot};
use tokio_stream::{Stream, StreamExt, wrappers::UnboundedReceiverStream};
use tonic::{Request, Response, Status};

/// Current Integration Server implementation version.
const SERVER_VERSION: &str = "roboguide.server/v0.4";
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
        registration: crate::grpc::v0_4::NodeRegistration,
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
    /// Explicit semantic contract negotiated for this route.
    node_contract_version: String,
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
        command_id: String,
        execution_id: String,
        invocation: crate::grpc::v0_4::CanonicalInvocation,
        resource_ids: Vec<String>,
    ) -> Result<(), Status> {
        if command_id.trim().is_empty()
            || execution_id.trim().is_empty()
            || invocation.mission_id.trim().is_empty()
            || invocation.task_id.trim().is_empty()
            || invocation.group_id.trim().is_empty()
            || invocation.role_id.trim().is_empty()
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
        let invocation = invocation_for_contract(invocation, &route.node_contract_version)?;
        route
            .sender
            .send(Ok(ServerMessage {
                message: Some(ServerPayload::Execute(Execute {
                    session_id: route.session_id.clone(),
                    execution_id,
                    invocation: Some(invocation),
                    resource_ids,
                    command_id,
                })),
            }))
            .map_err(|_| Status::unavailable("node session closed"))
    }

    /// Sends Cancel through the node's current session.
    pub fn cancel(
        &self,
        node_id: &str,
        command_id: String,
        execution_id: String,
    ) -> Result<(), Status> {
        if command_id.trim().is_empty() || execution_id.trim().is_empty() {
            return Err(Status::invalid_argument(
                "Cancel command and execution identities must be nonblank",
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
                message: Some(ServerPayload::Cancel(Cancel {
                    session_id: route.session_id.clone(),
                    execution_id,
                    command_id,
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

    /// Rejects v0.2 sessions because durable commands require Protocol v0.4.
    async fn node_session(
        &self,
        _request: Request<tonic::Streaming<crate::grpc::v0_2::NodeMessage>>,
    ) -> Result<Response<Self::NodeSessionStream>, Status> {
        Err(Status::failed_precondition(
            "Node Protocol v0.2 is retired; configure roboguide.node-protocol/v0.4 and roboguide.node.v0.4",
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
    let node_contract = [NODE_CONTRACT_VERSION, LEGACY_NODE_CONTRACT_VERSION]
        .into_iter()
        .find(|supported| {
            hello
                .node_contract_versions
                .iter()
                .any(|offered| offered == supported)
        })
        .map(str::to_string)
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
                node_contract_version: node_contract,
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
                Some(NodePayload::PeerChannelReadiness(value)) => value.sequence,
                Some(NodePayload::CommandReceipt(value)) => value.sequence,
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

/// Adapts and validates the invocation representation selected during contract negotiation.
fn invocation_for_contract(
    mut invocation: crate::grpc::v0_4::CanonicalInvocation,
    node_contract_version: &str,
) -> Result<crate::grpc::v0_4::CanonicalInvocation, Status> {
    match node_contract_version {
        NODE_CONTRACT_VERSION => {
            if !invocation.capability_contract.is_empty() || !invocation.parameters.is_empty() {
                return Err(Status::invalid_argument(
                    "Node Contract v0.5 invocation cannot mix legacy fields with ExecutionIntent",
                ));
            }
            let intent = invocation.intent.as_ref().ok_or_else(|| {
                Status::failed_precondition("Node Contract v0.5 requires semantic ExecutionIntent")
            })?;
            let operation = intent
                .operation
                .as_ref()
                .ok_or_else(|| Status::invalid_argument("ExecutionIntent requires OperationRef"))?;
            if intent.objective.trim().is_empty()
                || operation.namespace.trim().is_empty()
                || operation.name.trim().is_empty()
                || operation.version.trim().is_empty()
            {
                return Err(Status::invalid_argument(
                    "ExecutionIntent operation and objective must be nonblank",
                ));
            }
            validate_scalar_map(&intent.parameters, "ExecutionIntent parameters")?;
            Ok(invocation)
        }
        LEGACY_NODE_CONTRACT_VERSION => {
            if let Some(intent) = invocation.intent.take() {
                if !invocation.capability_contract.is_empty() || !invocation.parameters.is_empty() {
                    return Err(Status::invalid_argument(
                        "legacy compatibility invocation cannot mix legacy and semantic fields",
                    ));
                }
                let operation = intent.operation.as_ref().ok_or_else(|| {
                    Status::invalid_argument("ExecutionIntent requires OperationRef")
                })?;
                let canonical_operation = format!(
                    "{}.{}@{}",
                    operation.namespace, operation.name, operation.version
                );
                if operation.namespace.trim().is_empty()
                    || operation.name.trim().is_empty()
                    || operation.version.trim().is_empty()
                    || intent.objective != canonical_operation
                {
                    return Err(Status::failed_precondition(
                        "legacy Node Contract v0.4 cannot preserve semantic objective",
                    ));
                }
                validate_scalar_map(&intent.parameters, "legacy invocation parameters")?;
                invocation.capability_contract = canonical_operation;
                invocation.parameters = intent.parameters;
            }
            if invocation.capability_contract.trim().is_empty() {
                return Err(Status::failed_precondition(
                    "legacy Node Contract v0.4 requires capability contract",
                ));
            }
            validate_scalar_map(&invocation.parameters, "legacy invocation parameters")?;
            Ok(invocation)
        }
        _ => Err(Status::failed_precondition(
            "route negotiated an unsupported Node Contract",
        )),
    }
}

/// Rejects absent and non-finite scalar values before they reach a Node workflow.
fn validate_scalar_map(
    values: &std::collections::HashMap<String, crate::grpc::v0_4::ScalarValue>,
    kind: &str,
) -> Result<(), Status> {
    use crate::grpc::v0_4::scalar_value::Value;
    if values.iter().any(|(key, value)| {
        key.trim().is_empty()
            || value.value.as_ref().is_none_or(
                |value| matches!(value, Value::FloatValue(number) if !number.is_finite()),
            )
    }) {
        return Err(Status::invalid_argument(format!(
            "{kind} must have nonblank keys and finite typed values"
        )));
    }
    Ok(())
}

mod session;

use session::{
    accept_current_message, activate_current_route, deliver_for_acceptance, emit_unavailable,
    remove_current_route, route_is_expired, validate_registration,
};

#[cfg(test)]
mod tests;
