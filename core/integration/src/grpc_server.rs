//! Formal gRPC Node Protocol server and concurrent session command routing.

use crate::grpc::v0_2::node_message::Message as NodePayload;
use crate::grpc::v0_2::robo_guide_node_protocol_server::RoboGuideNodeProtocol;
use crate::grpc::v0_2::server_message::Message as ServerPayload;
use crate::grpc::v0_2::{
    Ack, Cancel, Execute, NODE_CONTRACT_VERSION, NodeMessage, PROTOCOL_VERSION, Registered,
    ServerMessage, Welcome,
};
use std::collections::{BTreeMap, BTreeSet};
use std::pin::Pin;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use tokio::sync::mpsc;
use tokio_stream::{Stream, StreamExt, wrappers::UnboundedReceiverStream};
use tonic::{Request, Response, Status};

/// Current Integration Server implementation version.
const SERVER_VERSION: &str = "roboguide.server/v0.2";

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
        registration: crate::grpc::v0_2::NodeRegistration,
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
    /// Last accepted management sequence for heartbeat/registration updates.
    management_sequence: u64,
}

impl GrpcNodeRouter {
    /// Sends a canonical Execute through the node's current session.
    pub fn execute(
        &self,
        node_id: &str,
        execution_id: String,
        invocation: crate::grpc::v0_2::CanonicalInvocation,
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
                management_sequence: 0,
            },
        );
    if let Some(previous) = previous {
        let _ = previous
            .sender
            .send(Err(Status::aborted("session superseded by reconnect")));
    }
    let node_id = registration.node_id.clone();
    if events
        .send(GrpcNodeEvent::Registered {
            session_id: session_id.clone(),
            lease_id,
            registration,
        })
        .is_err()
    {
        remove_current_route(&router, &node_id, &session_id)?;
        return Err(Status::unavailable("Runtime event sink is closed"));
    }
    let mut lease_check = tokio::time::interval(std::time::Duration::from_millis(250));
    let session_result: Result<(), Status> = async {
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
            events
                .send(GrpcNodeEvent::NodeMessage {
                    node_id: node_id.clone(),
                    session_id: session_id.clone(),
                    message,
                })
                .map_err(|_| Status::unavailable("Runtime event sink is closed"))?;
            if sequence > 0 {
                let _ = outbound.send(Ok(ServerMessage {
                    message: Some(ServerPayload::Ack(Ack { sequence })),
                }));
            }
        }
        Ok(())
    }
    .await;
    let removed = remove_current_route(&router, &node_id, &session_id)?;
    if removed {
        let _ = events.send(GrpcNodeEvent::Unavailable {
            node_id,
            session_id,
        });
    }
    session_result
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

/// Validates complete v0.2 ownership without inferring Local How on the Server.
fn validate_registration(registration: &crate::grpc::v0_2::NodeRegistration) -> Result<(), Status> {
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
                && !version.contains('@')
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
            },
        );
        assert_eq!(
            router
                .execute(
                    "dog-a",
                    "execution-1".to_string(),
                    crate::grpc::v0_2::CanonicalInvocation {
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
            },
        );
        let message = NodeMessage {
            message: Some(NodePayload::Heartbeat(crate::grpc::v0_2::Heartbeat {
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
