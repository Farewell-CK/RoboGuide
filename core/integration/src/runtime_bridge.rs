//! Composition bridge from formal Node Protocol facts into Runtime/Control/State semantics.

use crate::grpc::v0_1::node_message::Message as NodePayload;
use crate::grpc::v0_1::{CanonicalInvocation, ExecutionPhase, NodeRegistration, ScalarValue};
use crate::{GrpcNodeEvent, GrpcNodeRouter};
use control::ControlPlane;
use domain::{
    Capability, CapabilityContractRef, CapabilityKind, CorrelationId, EventPayload,
    ExecutionCommand, ExecutionValue, LeaseId, LocalRuntime, NodeContractVersion, NodeEvent,
    NodeHealth, NodeHeartbeat, NodeId, NodeLease, NodeStatus, Resource, ResourceId, ResourceKind,
    TimestampMs,
};
use ports::{EventSink, SharedNodeStateWriter};
use state::InMemorySharedNodeState;
use std::collections::BTreeMap;
use std::fmt::{Display, Formatter};

/// Remote execution lifecycle observed by Runtime before Control terminal handling.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum RemoteExecutionStatus {
    /// Node accepted the command.
    Accepted,
    /// Node reports active execution.
    Running,
    /// Node completed the command.
    Completed,
    /// Node failed the command.
    Failed,
    /// Node cancelled the command.
    Cancelled,
    /// Node could not identify the command.
    Unknown,
}

/// Live composition state consuming Integration events and routing Runtime commands.
pub struct IntegrationRuntimeBridge<E> {
    /// Existing Control authority for node leases and registration.
    control: ControlPlane,
    /// Shared Node State updated by remote facts.
    state: InMemorySharedNodeState,
    /// Existing domain event sink used by Runtime/Control.
    events: E,
    /// Current formal gRPC Node routes.
    router: GrpcNodeRouter,
    /// Dispatched execution contexts needed to turn terminal facts into NodeEvent.
    executions: BTreeMap<String, ExecutionCommand>,
    /// Latest execution lifecycle facts.
    execution_status: BTreeMap<String, RemoteExecutionStatus>,
}

impl<E: EventSink> IntegrationRuntimeBridge<E> {
    /// Creates the composition bridge around existing core authorities.
    pub fn new(
        control: ControlPlane,
        state: InMemorySharedNodeState,
        events: E,
        router: GrpcNodeRouter,
    ) -> Self {
        Self {
            control,
            state,
            events,
            router,
            executions: BTreeMap::new(),
            execution_status: BTreeMap::new(),
        }
    }

    /// Consumes one validated Integration Server event into Control, State, and Runtime evidence.
    pub fn consume(
        &mut self,
        event: GrpcNodeEvent,
        received_at: TimestampMs,
        correlation_id: &CorrelationId,
    ) -> Result<(), IntegrationRuntimeError> {
        match event {
            GrpcNodeEvent::Registered {
                lease_id,
                registration,
                ..
            } => {
                let registration = registration_from_wire(registration)?;
                let lease = NodeLease::new(
                    LeaseId::new(lease_id)?,
                    registration.node_id().clone(),
                    received_at,
                    15_000,
                )?;
                self.control.register_node_with_lease(
                    &mut self.state,
                    registration,
                    NodeStatus::new(NodeHealth::Online, received_at),
                    lease,
                    received_at,
                    correlation_id,
                    &mut self.events,
                )?;
            }
            GrpcNodeEvent::NodeMessage {
                node_id, message, ..
            } => match message.message {
                Some(NodePayload::Heartbeat(heartbeat)) => {
                    let node_id = NodeId::new(node_id)?;
                    let lease_id = LeaseId::new(heartbeat.lease_id)?;
                    let status = status_from_wire(heartbeat.status.as_ref(), received_at)?;
                    self.control.accept_heartbeat(
                        &mut self.state,
                        NodeHeartbeat::new(node_id, lease_id, status),
                        received_at,
                        15_000,
                        correlation_id,
                        &mut self.events,
                    )?;
                }
                Some(NodePayload::ExecutionEvent(event)) => self.consume_execution(
                    &node_id,
                    &event.execution_id,
                    event.phase,
                    &event.reason,
                    received_at,
                    correlation_id,
                )?,
                Some(NodePayload::ExecutionSnapshot(snapshot)) => self.consume_execution(
                    &node_id,
                    &snapshot.execution_id,
                    snapshot.phase,
                    &snapshot.reason,
                    received_at,
                    correlation_id,
                )?,
                Some(NodePayload::RegistrationUpdate(update)) => {
                    let registration =
                        registration_from_wire(update.registration.ok_or_else(|| {
                            IntegrationRuntimeError::Protocol(
                                "registration update is empty".to_string(),
                            )
                        })?)?;
                    let lease_id = self.control_lease_id(registration.node_id())?;
                    let lease = NodeLease::new(
                        lease_id,
                        registration.node_id().clone(),
                        received_at,
                        15_000,
                    )?;
                    self.control.register_node_with_lease(
                        &mut self.state,
                        registration,
                        NodeStatus::new(NodeHealth::Online, received_at),
                        lease,
                        received_at,
                        correlation_id,
                        &mut self.events,
                    )?;
                }
                _ => {}
            },
            GrpcNodeEvent::Unavailable { node_id, .. } => {
                self.state.record_node_liveness(
                    &NodeId::new(node_id)?,
                    domain::NodeLivenessObservation::new(
                        domain::NodeLiveness::Unreachable,
                        received_at,
                    ),
                )?;
            }
        }
        Ok(())
    }

    /// Routes an existing Runtime command to its Control-selected NodeId.
    pub fn execute(
        &mut self,
        execution_id: String,
        command: ExecutionCommand,
    ) -> Result<(), IntegrationRuntimeError> {
        if let Some(existing) = self.executions.get(&execution_id) {
            if existing != &command {
                return Err(IntegrationRuntimeError::ExecutionConflict(execution_id));
            }
            return Ok(());
        }
        self.router.execute(
            command.node_id().as_str(),
            execution_id.clone(),
            invocation_from_command(&command),
        )?;
        self.executions.insert(execution_id.clone(), command);
        self.execution_status
            .insert(execution_id, RemoteExecutionStatus::Accepted);
        Ok(())
    }

    /// Routes cancellation without claiming local cancellation completion.
    pub fn cancel(&self, execution_id: &str) -> Result<(), IntegrationRuntimeError> {
        let command = self
            .executions
            .get(execution_id)
            .ok_or_else(|| IntegrationRuntimeError::Protocol("unknown execution id".to_string()))?;
        self.router
            .cancel(command.node_id().as_str(), execution_id.to_string())
            .map_err(Into::into)
    }

    /// Returns current Control authority.
    pub const fn control(&self) -> &ControlPlane {
        &self.control
    }
    /// Returns current Shared Node State.
    pub const fn state(&self) -> &InMemorySharedNodeState {
        &self.state
    }
    /// Returns current remote execution status.
    pub fn execution_status(&self, execution_id: &str) -> Option<RemoteExecutionStatus> {
        self.execution_status.get(execution_id).copied()
    }
    /// Returns the current lease id for a registered node.
    fn control_lease_id(&self, node_id: &NodeId) -> Result<LeaseId, IntegrationRuntimeError> {
        self.control
            .node_lease(node_id)
            .map(|lease| lease.lease_id().clone())
            .ok_or_else(|| {
                IntegrationRuntimeError::Protocol(
                    "registration update has no active lease".to_string(),
                )
            })
    }

    /// Converts execution facts into Runtime evidence and terminal NodeEvent values.
    fn consume_execution(
        &mut self,
        node_id: &str,
        execution_id: &str,
        phase: i32,
        reason: &str,
        received_at: TimestampMs,
        correlation_id: &CorrelationId,
    ) -> Result<(), IntegrationRuntimeError> {
        let phase = ExecutionPhase::try_from(phase).map_err(|_| {
            IntegrationRuntimeError::Protocol("unknown execution phase".to_string())
        })?;
        let status = match phase {
            ExecutionPhase::Accepted => RemoteExecutionStatus::Accepted,
            ExecutionPhase::Started => RemoteExecutionStatus::Running,
            ExecutionPhase::Completed => RemoteExecutionStatus::Completed,
            ExecutionPhase::Failed => RemoteExecutionStatus::Failed,
            ExecutionPhase::Cancelled => RemoteExecutionStatus::Cancelled,
            ExecutionPhase::Unknown | ExecutionPhase::Unspecified => RemoteExecutionStatus::Unknown,
        };
        self.execution_status
            .insert(execution_id.to_string(), status);
        let Some(command) = self.executions.get(execution_id) else {
            return Ok(());
        };
        if command.node_id().as_str() != node_id {
            return Err(IntegrationRuntimeError::Protocol(
                "execution fact node differs from dispatched command".to_string(),
            ));
        }
        let node_event = match phase {
            ExecutionPhase::Completed => Some(NodeEvent::TaskCompleted {
                node_id: command.node_id().clone(),
                task_ref: command.task_ref().clone(),
                group_id: command.group_id().clone(),
                role_id: command.role_id().clone(),
            }),
            ExecutionPhase::Failed | ExecutionPhase::Cancelled => Some(NodeEvent::TaskFailed {
                node_id: command.node_id().clone(),
                task_ref: command.task_ref().clone(),
                group_id: command.group_id().clone(),
                role_id: command.role_id().clone(),
                reason: reason.to_string(),
            }),
            _ => None,
        };
        if let Some(node_event) = node_event {
            self.events.append(
                received_at,
                correlation_id,
                None,
                EventPayload::NodeObservation(node_event),
            );
        }
        Ok(())
    }
}

/// Converts a formal registration into transport-neutral Domain facts.
fn registration_from_wire(
    wire: NodeRegistration,
) -> Result<domain::NodeRegistration, IntegrationRuntimeError> {
    let runtime = wire.runtime.ok_or_else(|| {
        IntegrationRuntimeError::Protocol("registration lacks runtime".to_string())
    })?;
    let mut capabilities = Vec::new();
    let mut contracts = Vec::new();
    for capability in wire.capabilities {
        let kind = capability_kind(&capability.kind)?;
        capabilities.push(Capability::new(kind, capability.available));
        for contract in capability.contracts {
            contracts.push(parse_contract(&contract)?);
        }
    }
    let resources = wire
        .resources
        .into_iter()
        .map(|resource| {
            Ok(Resource::new(
                ResourceId::new(resource.id)?,
                resource_kind(&resource.kind)?,
                resource.capacity,
            )?)
        })
        .collect::<Result<Vec<_>, IntegrationRuntimeError>>()?;
    Ok(domain::NodeRegistration::new_with_contracts(
        NodeId::new(wire.node_id)?,
        LocalRuntime::new(runtime.name, runtime.version)?,
        NodeContractVersion::new(wire.node_contract_version)?,
        capabilities,
        contracts,
        resources,
    ))
}

/// Converts current protocol health into Domain health.
fn status_from_wire(
    status: Option<&crate::grpc::v0_1::NodeStatus>,
    observed_at: TimestampMs,
) -> Result<NodeStatus, IntegrationRuntimeError> {
    let health = match status
        .map(|value| value.health.as_str())
        .unwrap_or("online")
    {
        "online" => NodeHealth::Online,
        "degraded" => NodeHealth::Degraded,
        "offline" => NodeHealth::Offline,
        other => {
            return Err(IntegrationRuntimeError::Protocol(format!(
                "unknown node health {other}"
            )));
        }
    };
    Ok(NodeStatus::new(health, observed_at))
}
/// Parses a wire capability kind.
fn capability_kind(value: &str) -> Result<CapabilityKind, IntegrationRuntimeError> {
    match value {
        "mobility" => Ok(CapabilityKind::Mobility),
        "transport" => Ok(CapabilityKind::Transport),
        "compute" => Ok(CapabilityKind::Compute),
        "observation" => Ok(CapabilityKind::Observation),
        _ => Err(IntegrationRuntimeError::Protocol(format!(
            "unknown capability {value}"
        ))),
    }
}
/// Parses a wire resource kind.
fn resource_kind(value: &str) -> Result<ResourceKind, IntegrationRuntimeError> {
    match value {
        "space" => Ok(ResourceKind::Space),
        "compute" => Ok(ResourceKind::Compute),
        "time" => Ok(ResourceKind::Time),
        _ => Err(IntegrationRuntimeError::Protocol(format!(
            "unknown resource {value}"
        ))),
    }
}
/// Parses `namespace.name@version` canonical identity.
fn parse_contract(value: &str) -> Result<CapabilityContractRef, IntegrationRuntimeError> {
    let (name, version) = value
        .rsplit_once('@')
        .ok_or_else(|| IntegrationRuntimeError::Protocol("contract lacks version".to_string()))?;
    let (namespace, name) = name
        .rsplit_once('.')
        .ok_or_else(|| IntegrationRuntimeError::Protocol("contract lacks namespace".to_string()))?;
    Ok(CapabilityContractRef::new(namespace, name, version)?)
}

/// Converts existing canonical ExecutionCommand into formal wire invocation.
fn invocation_from_command(command: &ExecutionCommand) -> CanonicalInvocation {
    CanonicalInvocation {
        mission_id: command.mission_id().as_str().to_string(),
        task_id: command.task_id().as_str().to_string(),
        group_id: command.group_id().as_str().to_string(),
        role_id: command.role_id().as_str().to_string(),
        capability_contract: command.intent().capability_contract().to_string(),
        parameters: command
            .intent()
            .parameters()
            .iter()
            .map(|(key, value)| (key.clone(), scalar(value)))
            .collect(),
    }
}
/// Converts one transport-neutral scalar.
fn scalar(value: &ExecutionValue) -> ScalarValue {
    use crate::grpc::v0_1::scalar_value::Value;
    ScalarValue {
        value: Some(match value {
            ExecutionValue::Bool(value) => Value::BoolValue(*value),
            ExecutionValue::Integer(value) => Value::IntegerValue(*value),
            ExecutionValue::Float(value) => Value::FloatValue(*value),
            ExecutionValue::String(value) => Value::StringValue(value.clone()),
        }),
    }
}

/// Runtime bridge failure.
#[derive(Debug)]
pub enum IntegrationRuntimeError {
    /// Core Control rejected a fact.
    Control(control::ControlError),
    /// Shared State rejected a fact.
    State(ports::SharedStateError),
    /// Domain conversion failed.
    Domain(domain::DomainError),
    /// gRPC router rejected a command.
    Route(tonic::Status),
    /// Protocol conversion failed.
    Protocol(String),
    /// Stable execution id was reused for another command.
    ExecutionConflict(String),
}
impl Display for IntegrationRuntimeError {
    /// Formats bridge failures.
    fn fmt(&self, f: &mut Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Control(error) => error.fmt(f),
            Self::State(error) => error.fmt(f),
            Self::Domain(error) => error.fmt(f),
            Self::Route(error) => error.fmt(f),
            Self::Protocol(reason) => f.write_str(reason),
            Self::ExecutionConflict(id) => {
                write!(f, "execution {id} was reused with another command")
            }
        }
    }
}
impl std::error::Error for IntegrationRuntimeError {}
impl From<control::ControlError> for IntegrationRuntimeError {
    fn from(value: control::ControlError) -> Self {
        Self::Control(value)
    }
}
impl From<ports::SharedStateError> for IntegrationRuntimeError {
    fn from(value: ports::SharedStateError) -> Self {
        Self::State(value)
    }
}
impl From<domain::DomainError> for IntegrationRuntimeError {
    fn from(value: domain::DomainError) -> Self {
        Self::Domain(value)
    }
}
impl From<tonic::Status> for IntegrationRuntimeError {
    fn from(value: tonic::Status) -> Self {
        Self::Route(value)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::grpc::v0_1::{Capability as WireCapability, LocalRuntime as WireRuntime};
    use ports::SharedNodeStateReader;
    use testkit::InMemoryEventLog;

    /// Registration and heartbeat facts enter existing Control lease authority and Shared State.
    #[test]
    fn integration_facts_update_control_and_shared_state() {
        let router = GrpcNodeRouter::default();
        let mut bridge = IntegrationRuntimeBridge::new(
            ControlPlane::new(),
            InMemorySharedNodeState::new(),
            InMemoryEventLog::new(),
            router,
        );
        let correlation = CorrelationId::new("integration-test").expect("correlation valid");
        bridge
            .consume(
                GrpcNodeEvent::Registered {
                    session_id: "session-1".to_string(),
                    lease_id: "lease-1".to_string(),
                    registration: NodeRegistration {
                        node_id: "dog-a".to_string(),
                        runtime: Some(WireRuntime {
                            name: "robonix".to_string(),
                            version: "dev".to_string(),
                        }),
                        capabilities: vec![WireCapability {
                            kind: "mobility".to_string(),
                            available: true,
                            contracts: vec!["mobility.reach_region@v1".to_string()],
                        }],
                        sensors: Vec::new(),
                        resources: Vec::new(),
                        metadata: std::collections::HashMap::new(),
                        node_contract_version: "roboguide.node.v0.1".to_string(),
                    },
                },
                TimestampMs::new(0),
                &correlation,
            )
            .expect("registration consumed");
        let node_id = NodeId::new("dog-a").expect("node id valid");
        assert!(bridge.control().node_lease(&node_id).is_some());
        assert_eq!(
            bridge
                .state()
                .node(&node_id)
                .expect("node visible")
                .reported_status()
                .health(),
            NodeHealth::Online
        );
        bridge
            .consume(
                GrpcNodeEvent::NodeMessage {
                    node_id: "dog-a".to_string(),
                    session_id: "session-1".to_string(),
                    message: crate::grpc::v0_1::NodeMessage {
                        message: Some(NodePayload::Heartbeat(crate::grpc::v0_1::Heartbeat {
                            session_id: "session-1".to_string(),
                            lease_id: "lease-1".to_string(),
                            sequence: 1,
                            status: Some(crate::grpc::v0_1::NodeStatus {
                                health: "degraded".to_string(),
                                detail: String::new(),
                            }),
                        })),
                    },
                },
                TimestampMs::new(1),
                &correlation,
            )
            .expect("heartbeat consumed");
        assert_eq!(
            bridge
                .state()
                .node(&node_id)
                .expect("node visible")
                .reported_status()
                .health(),
            NodeHealth::Degraded
        );
    }
}
