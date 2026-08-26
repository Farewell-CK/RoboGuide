//! Composition bridge from formal Node Protocol facts into Runtime/Control/State semantics.

use crate::grpc::v0_2::node_message::Message as NodePayload;
use crate::grpc::v0_2::{CanonicalInvocation, ExecutionPhase, NodeRegistration, ScalarValue};
use crate::{GrpcNodeEvent, GrpcNodeRouter};
use control::ControlPlane;
use domain::{
    Capability, CapabilityContractRef, CapabilityKind, CorrelationId, EventPayload,
    ExecutionCommand, ExecutionValue, LeaseId, LocalRuntime, LocalSystemDescriptor, LocalSystemId,
    NodeContractVersion, NodeEvent, NodeHealth, NodeHeartbeat, NodeId, NodeLease, NodeStatus,
    Resource, ResourceId, ResourceKind, SensorDescriptor, SensorId, TimestampMs,
};
use ports::{EventSink, SharedNodeStateWriter};
use runtime::{
    ExecutionEvent, ExecutionStatus, RuntimeExecutionCheckpoint, RuntimeExecutionManager,
};
use state::InMemorySharedNodeState;
use std::collections::{BTreeMap, VecDeque};
use std::fmt::{Display, Formatter};

/// Schema marker for the complete Integration/Control/State controller checkpoint.
pub const CONTROLLER_CHECKPOINT_SCHEMA: &str = "roboguide.controller-checkpoint/v4";

/// Remote execution lifecycle observed by Runtime before Control terminal handling.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
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

/// Terminal Task result derived from role execution facts without mutating Control.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ObservedTaskResult {
    /// Every currently bound role completed successfully.
    Succeeded,
    /// At least one currently bound role failed, cancelled, or became unknown.
    Failed,
}

/// One terminal Task result for Mission orchestration to consume explicitly.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ObservedTaskOutcome {
    /// Mission-level Group containing the TaskExecution.
    group_id: domain::ExecutionGroupId,
    /// Mission-scoped Task represented by the result.
    task_ref: domain::TaskRef,
    /// Runtime-derived terminal role result.
    result: ObservedTaskResult,
}

impl ObservedTaskOutcome {
    /// Returns the Mission-level Group containing this Task.
    pub const fn group_id(&self) -> &domain::ExecutionGroupId {
        &self.group_id
    }

    /// Returns the Mission-scoped Task represented by this result.
    pub const fn task_ref(&self) -> &domain::TaskRef {
        &self.task_ref
    }

    /// Returns the terminal role result observed by Runtime.
    pub const fn result(&self) -> ObservedTaskResult {
        self.result
    }
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
    /// Runtime-owned live execution registry and sole execution checkpoint authority.
    runtime: RuntimeExecutionManager,
    /// Canonical Runtime transitions awaiting application/orchestration consumption.
    runtime_events: VecDeque<ExecutionEvent>,
}

/// Complete durable projection required to reconstruct the controller process.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
struct ControllerCheckpoint {
    /// Exact schema marker validated before any state is restored.
    schema: String,
    /// Control-owned commitments, bindings, and Group lifecycle.
    control: control::ControlCheckpoint,
    /// Shared reported node facts; local receive/liveness times are rebased on restore.
    nodes: Vec<domain::NodeStateSnapshot>,
    /// Runtime-owned live execution contexts and continuity state.
    runtime: RuntimeExecutionCheckpoint,
}

/// Validated execution fact context received from one current Node session.
struct ReceivedExecutionFact<'a> {
    /// Reporting node identity.
    node_id: &'a str,
    /// Stable cross-session execution identity.
    execution_id: &'a str,
    /// Execution-local monotonic sequence.
    sequence: u64,
    /// Wire execution phase.
    phase: i32,
    /// Local diagnostic detail.
    reason: &'a str,
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
            runtime: RuntimeExecutionManager::new(),
            runtime_events: VecDeque::new(),
        }
    }

    /// Serializes a versioned checkpoint of Control, Shared State, and Runtime projections.
    pub fn checkpoint_json(&self) -> Result<String, IntegrationRuntimeError> {
        let checkpoint = ControllerCheckpoint {
            schema: CONTROLLER_CHECKPOINT_SCHEMA.to_string(),
            control: self.control.checkpoint(),
            nodes: self.state.snapshots(),
            runtime: self.runtime.checkpoint(),
        };
        serde_json::to_string(&checkpoint)
            .map_err(|error| IntegrationRuntimeError::Checkpoint(error.to_string()))
    }

    /// Restores one versioned checkpoint with fresh routes and conservative process-local facts.
    ///
    /// Leases are cleared by Control restore, node liveness is reset by State restore, and every
    /// nonterminal execution becomes Unknown. This method never routes or replays a command.
    pub fn restore_from_checkpoint(
        checkpoint_json: &str,
        events: E,
        router: GrpcNodeRouter,
        restored_at: TimestampMs,
    ) -> Result<Self, IntegrationRuntimeError> {
        let checkpoint: ControllerCheckpoint = serde_json::from_str(checkpoint_json)
            .map_err(|error| IntegrationRuntimeError::Checkpoint(error.to_string()))?;
        if checkpoint.schema != CONTROLLER_CHECKPOINT_SCHEMA {
            return Err(IntegrationRuntimeError::Checkpoint(format!(
                "unsupported controller checkpoint schema {}",
                checkpoint.schema
            )));
        }
        let control = ControlPlane::restore(checkpoint.control)?;
        let state = InMemorySharedNodeState::restore(checkpoint.nodes, restored_at)
            .map_err(IntegrationRuntimeError::Checkpoint)?;
        let runtime = RuntimeExecutionManager::restore(checkpoint.runtime)
            .map_err(|error| IntegrationRuntimeError::Checkpoint(error.to_string()))?;
        Ok(Self {
            control,
            state,
            events,
            router,
            runtime,
            runtime_events: VecDeque::new(),
        })
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
                    NodeStatus::new(NodeHealth::Offline, received_at),
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
                    ReceivedExecutionFact {
                        node_id: &node_id,
                        execution_id: &event.execution_id,
                        sequence: event.sequence,
                        phase: event.phase,
                        reason: &event.reason,
                    },
                    received_at,
                    correlation_id,
                )?,
                Some(NodePayload::ExecutionSnapshot(snapshot)) => self.consume_execution(
                    ReceivedExecutionFact {
                        node_id: &node_id,
                        execution_id: &snapshot.execution_id,
                        sequence: snapshot.last_sequence,
                        phase: snapshot.phase,
                        reason: &snapshot.reason,
                    },
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
                    self.control.update_node_registration(
                        &mut self.state,
                        registration,
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
        mut resource_ids: Vec<ResourceId>,
    ) -> Result<(), IntegrationRuntimeError> {
        let dispatch = self
            .runtime
            .validate_dispatch(&execution_id, &command, &resource_ids)
            .map_err(|error| IntegrationRuntimeError::Protocol(error.to_string()))?;
        if dispatch == runtime::DispatchDecision::AlreadyRouted {
            return Ok(());
        }
        resource_ids.sort();
        resource_ids.dedup();
        self.router.execute(
            command.node_id().as_str(),
            execution_id.clone(),
            invocation_from_command(&command),
            resource_ids
                .iter()
                .map(|resource_id| resource_id.as_str().to_string())
                .collect(),
        )?;
        self.runtime
            .record_dispatched(execution_id, command, resource_ids)
            .map_err(|error| IntegrationRuntimeError::Protocol(error.to_string()))?;
        Ok(())
    }

    /// Builds and routes a command only from a Control-owned bound Group role.
    pub fn execute_bound(
        &mut self,
        execution_id: String,
        group_id: &domain::ExecutionGroupId,
        role_id: &domain::RoleId,
        intent: domain::ExecutionIntent,
        correlation_id: CorrelationId,
    ) -> Result<ExecutionCommand, IntegrationRuntimeError> {
        let task_ref = self
            .control
            .group(group_id)
            .map(|group| group.task_ref().clone())
            .ok_or_else(|| {
                IntegrationRuntimeError::Protocol("execution group is unknown".to_string())
            })?;
        self.execute_task_bound(
            execution_id,
            group_id,
            &task_ref,
            role_id,
            intent,
            correlation_id,
        )
    }

    /// Builds and routes a command for one specific Task execution inside a Group.
    pub fn execute_task_bound(
        &mut self,
        execution_id: String,
        group_id: &domain::ExecutionGroupId,
        task_ref: &domain::TaskRef,
        role_id: &domain::RoleId,
        intent: domain::ExecutionIntent,
        correlation_id: CorrelationId,
    ) -> Result<ExecutionCommand, IntegrationRuntimeError> {
        let group = self.control.group(group_id).ok_or_else(|| {
            IntegrationRuntimeError::Protocol("execution group is unknown".to_string())
        })?;
        if !matches!(
            group.lifecycle(),
            control::GroupLifecycle::Bound
                | control::GroupLifecycle::Active
                | control::GroupLifecycle::Adapted
        ) {
            return Err(IntegrationRuntimeError::Protocol(
                "execution group is not bound".to_string(),
            ));
        }
        let assignment =
            if task_ref == group.task_ref() && group.task_executions().next().is_none() {
                group
                    .assignments()
                    .iter()
                    .find(|assignment| assignment.role_id() == role_id)
            } else {
                group
                    .task_execution(task_ref)
                    .into_iter()
                    .flat_map(|execution| execution.assignments())
                    .find(|assignment| assignment.role_id() == role_id)
            }
            .ok_or_else(|| {
                IntegrationRuntimeError::Protocol("group role is not bound".to_string())
            })?;
        let command = ExecutionCommand::new(
            task_ref.mission_id().clone(),
            task_ref.task_id().clone(),
            group_id.clone(),
            role_id.clone(),
            assignment.node_id().clone(),
            intent,
            correlation_id,
        );
        self.execute(
            execution_id,
            command.clone(),
            assignment.resource_ids().to_vec(),
        )?;
        Ok(command)
    }

    /// Routes cancellation without claiming local cancellation completion.
    pub fn cancel(&self, execution_id: &str) -> Result<(), IntegrationRuntimeError> {
        let node_id = self
            .runtime
            .cancellation_node(execution_id)
            .ok_or_else(|| IntegrationRuntimeError::Protocol("unknown execution id".to_string()))?;
        self.router
            .cancel(node_id.as_str(), execution_id.to_string())
            .map_err(Into::into)
    }

    /// Returns current Control authority.
    pub const fn control(&self) -> &ControlPlane {
        &self.control
    }
    /// Returns mutable Control authority for composition-level Group lifecycle.
    pub const fn control_mut(&mut self) -> &mut ControlPlane {
        &mut self.control
    }
    /// Returns current Shared Node State.
    pub const fn state(&self) -> &InMemorySharedNodeState {
        &self.state
    }
    /// Returns mutable Shared Node State for composition-level Control calls.
    pub const fn state_mut(&mut self) -> &mut InMemorySharedNodeState {
        &mut self.state
    }
    /// Returns current remote execution status.
    pub fn execution_status(&self, execution_id: &str) -> Option<RemoteExecutionStatus> {
        self.runtime
            .execution_status(execution_id)
            .map(remote_status)
    }

    /// Drains canonical Runtime transitions for application-level lifecycle handling.
    pub fn take_runtime_events(&mut self) -> Vec<ExecutionEvent> {
        self.runtime_events.drain(..).collect()
    }

    /// Reports terminal outcomes for active TaskExecutions without changing Mission or Group state.
    pub fn terminal_task_outcomes(&self) -> Vec<ObservedTaskOutcome> {
        let mut outcomes = Vec::new();
        for group_id in self.control.group_ids() {
            let Some(group) = self.control.group(&group_id) else {
                continue;
            };
            for task in group.task_executions().filter(|task| {
                task.lifecycle() == domain::TaskExecutionLifecycle::Active
                    && !task.assignments().is_empty()
            }) {
                let role_ids = task
                    .assignments()
                    .iter()
                    .map(|assignment| assignment.role_id());
                if let Some(result) = self
                    .runtime
                    .task_result(&group_id, task.task_ref(), role_ids)
                {
                    outcomes.push(ObservedTaskOutcome {
                        group_id: group_id.clone(),
                        task_ref: task.task_ref().clone(),
                        result: match result {
                            runtime::ObservedTaskResult::Succeeded => ObservedTaskResult::Succeeded,
                            runtime::ObservedTaskResult::Failed => ObservedTaskResult::Failed,
                        },
                    });
                    continue;
                }
            }
        }
        outcomes
    }
    /// Converts execution facts into Runtime evidence and terminal NodeEvent values.
    fn consume_execution(
        &mut self,
        fact: ReceivedExecutionFact<'_>,
        received_at: TimestampMs,
        correlation_id: &CorrelationId,
    ) -> Result<(), IntegrationRuntimeError> {
        let phase = ExecutionPhase::try_from(fact.phase).map_err(|_| {
            IntegrationRuntimeError::Protocol("unknown execution phase".to_string())
        })?;
        let node_id = NodeId::new(fact.node_id)?;
        let runtime_status = match phase {
            ExecutionPhase::Accepted => ExecutionStatus::Accepted,
            ExecutionPhase::Started => ExecutionStatus::Running,
            ExecutionPhase::Completed => ExecutionStatus::Completed,
            ExecutionPhase::Failed => ExecutionStatus::Failed,
            ExecutionPhase::Cancelled => ExecutionStatus::Cancelled,
            ExecutionPhase::Unknown | ExecutionPhase::Unspecified => ExecutionStatus::Unknown,
        };
        let runtime_events = self
            .runtime
            .observe_execution(
                fact.execution_id,
                node_id.clone(),
                fact.sequence,
                runtime_status,
                fact.reason,
            )
            .map_err(|error| IntegrationRuntimeError::Protocol(error.to_string()))?;
        for event in runtime_events {
            append_runtime_evidence(&mut self.events, &event, received_at, correlation_id);
            self.runtime_events.push_back(event);
        }
        Ok(())
    }
}

/// Persists canonical Runtime facts without granting Integration lifecycle authority.
fn append_runtime_evidence<E: EventSink>(
    events: &mut E,
    event: &ExecutionEvent,
    timestamp: TimestampMs,
    correlation_id: &CorrelationId,
) {
    let payload = match event {
        ExecutionEvent::TaskActivated { .. } => return,
        ExecutionEvent::RoleCompleted { command } => {
            EventPayload::NodeObservation(NodeEvent::TaskCompleted {
                node_id: command.node_id().clone(),
                task_ref: command.task_ref().clone(),
                group_id: command.group_id().clone(),
                role_id: command.role_id().clone(),
            })
        }
        ExecutionEvent::RoleFailed { command, reason } => {
            EventPayload::NodeObservation(NodeEvent::TaskFailed {
                node_id: command.node_id().clone(),
                task_ref: command.task_ref().clone(),
                group_id: command.group_id().clone(),
                role_id: command.role_id().clone(),
                reason: reason.clone(),
            })
        }
        ExecutionEvent::RecoveryRequired {
            execution_id,
            node_id,
            context,
            reason,
        } => EventPayload::RuntimeExecutionRecoveryRequired {
            execution_id: execution_id.clone(),
            node_id: node_id.clone(),
            group_id: context.as_ref().map(|command| command.group_id().clone()),
            task_ref: context.as_ref().map(|command| command.task_ref().clone()),
            role_id: context.as_ref().map(|command| command.role_id().clone()),
            reason: reason.clone(),
        },
    };
    events.append(timestamp, correlation_id, None, payload);
}

/// Converts the transport-neutral Runtime status into the bridge compatibility enum.
fn remote_status(status: ExecutionStatus) -> RemoteExecutionStatus {
    match status {
        ExecutionStatus::Dispatched | ExecutionStatus::Accepted => RemoteExecutionStatus::Accepted,
        ExecutionStatus::Running => RemoteExecutionStatus::Running,
        ExecutionStatus::Completed => RemoteExecutionStatus::Completed,
        ExecutionStatus::Failed => RemoteExecutionStatus::Failed,
        ExecutionStatus::Cancelled => RemoteExecutionStatus::Cancelled,
        ExecutionStatus::Unknown => RemoteExecutionStatus::Unknown,
    }
}

/// Converts a formal registration into transport-neutral Domain facts.
fn registration_from_wire(
    wire: NodeRegistration,
) -> Result<domain::NodeRegistration, IntegrationRuntimeError> {
    let local_systems = wire
        .local_systems
        .into_iter()
        .map(|local_system| {
            let runtime = local_system.runtime.ok_or_else(|| {
                IntegrationRuntimeError::Protocol("local system lacks runtime".to_string())
            })?;
            Ok(LocalSystemDescriptor::new(
                LocalSystemId::new(local_system.id)?,
                LocalRuntime::new(runtime.name, runtime.version)?,
                local_system.metadata.into_iter().collect(),
            ))
        })
        .collect::<Result<Vec<_>, IntegrationRuntimeError>>()?;
    let mut capability_kinds = BTreeMap::<CapabilityKind, bool>::new();
    let mut capability_owners = BTreeMap::new();
    for capability in wire.capabilities {
        let kind = capability_kind(&capability.kind)?;
        capability_kinds
            .entry(kind)
            .and_modify(|available| *available |= capability.available)
            .or_insert(capability.available);
        let owner = LocalSystemId::new(capability.local_system_id)?;
        for contract in capability.contracts {
            if capability_owners
                .insert(parse_contract(&contract)?, owner.clone())
                .is_some()
            {
                return Err(IntegrationRuntimeError::Protocol(
                    "canonical capability has multiple owners".to_string(),
                ));
            }
        }
    }
    let capabilities = capability_kinds
        .into_iter()
        .map(|(kind, available)| Capability::new(kind, available))
        .collect();
    let sensors = wire
        .sensors
        .into_iter()
        .map(|sensor| {
            Ok(SensorDescriptor::new(
                SensorId::new(sensor.id)?,
                sensor.kind,
                LocalSystemId::new(sensor.local_system_id)?,
                sensor.metadata.into_iter().collect(),
            ))
        })
        .collect::<Result<Vec<_>, IntegrationRuntimeError>>()?;
    let mut resources = Vec::new();
    let mut resource_owners = BTreeMap::new();
    for resource in wire.resources {
        let resource_id = ResourceId::new(resource.id)?;
        let owner = LocalSystemId::new(resource.local_system_id)?;
        resources.push(Resource::new(
            resource_id.clone(),
            resource_kind(&resource.kind)?,
            resource.capacity,
        )?);
        resource_owners.insert(resource_id, owner);
    }
    Ok(domain::NodeRegistration::new_with_local_systems(
        NodeId::new(wire.node_id)?,
        local_systems,
        NodeContractVersion::new(wire.node_contract_version)?,
        capabilities,
        capability_owners,
        sensors,
        resources,
        resource_owners,
    )?)
}

/// Converts current protocol health into Domain health.
fn status_from_wire(
    status: Option<&crate::grpc::v0_2::NodeStatus>,
    observed_at: TimestampMs,
) -> Result<NodeStatus, IntegrationRuntimeError> {
    let status = status.ok_or_else(|| {
        IntegrationRuntimeError::Protocol("heartbeat is missing local health status".to_string())
    })?;
    let health = match status.health.as_str() {
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
    use crate::grpc::v0_2::scalar_value::Value;
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
    /// Durable controller checkpoint was malformed or incompatible.
    Checkpoint(String),
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
            Self::Checkpoint(reason) => write!(f, "controller checkpoint failure: {reason}"),
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
    use crate::grpc::v0_2::{
        Capability as WireCapability, LocalRuntime as WireRuntime, LocalSystemDescriptor,
    };
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
                        local_systems: vec![LocalSystemDescriptor {
                            id: "motion".to_string(),
                            runtime: Some(WireRuntime {
                                name: "local-motion".to_string(),
                                version: "1".to_string(),
                            }),
                            metadata: Default::default(),
                        }],
                        capabilities: vec![WireCapability {
                            kind: "mobility".to_string(),
                            available: true,
                            contracts: vec!["mobility.reach_region@v1".to_string()],
                            local_system_id: "motion".to_string(),
                        }],
                        sensors: Vec::new(),
                        resources: Vec::new(),
                        metadata: std::collections::HashMap::new(),
                        node_contract_version: "roboguide.node.v0.2".to_string(),
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
            NodeHealth::Offline
        );
        bridge
            .consume(
                GrpcNodeEvent::NodeMessage {
                    node_id: "dog-a".to_string(),
                    session_id: "session-1".to_string(),
                    message: crate::grpc::v0_2::NodeMessage {
                        message: Some(NodePayload::Heartbeat(crate::grpc::v0_2::Heartbeat {
                            session_id: "session-1".to_string(),
                            lease_id: "lease-1".to_string(),
                            sequence: 1,
                            status: Some(crate::grpc::v0_2::NodeStatus {
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
        bridge
            .consume(
                GrpcNodeEvent::NodeMessage {
                    node_id: "dog-a".to_string(),
                    session_id: "session-1".to_string(),
                    message: crate::grpc::v0_2::NodeMessage {
                        message: Some(NodePayload::RegistrationUpdate(
                            crate::grpc::v0_2::RegistrationUpdate {
                                session_id: "session-1".to_string(),
                                sequence: 2,
                                registration: Some(NodeRegistration {
                                    node_id: "dog-a".to_string(),
                                    local_systems: vec![LocalSystemDescriptor {
                                        id: "motion".to_string(),
                                        runtime: Some(WireRuntime {
                                            name: "local-motion".to_string(),
                                            version: "2".to_string(),
                                        }),
                                        metadata: Default::default(),
                                    }],
                                    capabilities: vec![WireCapability {
                                        kind: "mobility".to_string(),
                                        available: true,
                                        contracts: vec!["mobility.reach_region@v1".to_string()],
                                        local_system_id: "motion".to_string(),
                                    }],
                                    sensors: Vec::new(),
                                    resources: Vec::new(),
                                    metadata: std::collections::HashMap::new(),
                                    node_contract_version: "roboguide.node.v0.2".to_string(),
                                }),
                            },
                        )),
                    },
                },
                TimestampMs::new(2),
                &correlation,
            )
            .expect("registration update is consumed");
        let updated = bridge.state().node(&node_id).expect("updated node visible");
        assert_eq!(updated.reported_status().health(), NodeHealth::Degraded);
        assert_eq!(updated.reported_status_received_at(), TimestampMs::new(1));
        assert_eq!(updated.liveness().observed_at(), TimestampMs::new(1));
        assert_eq!(updated.registration().local_runtime().version(), "2");
    }

    /// Bound Group assignments are the only source of NodeId for Runtime routing.
    #[test]
    fn execute_bound_rejects_unbound_group_role() {
        let mut bridge = IntegrationRuntimeBridge::new(
            ControlPlane::new(),
            InMemorySharedNodeState::new(),
            InMemoryEventLog::new(),
            GrpcNodeRouter::default(),
        );
        let group_id = domain::ExecutionGroupId::new("group-unknown").expect("group id valid");
        let role_id = domain::RoleId::new("carrier").expect("role id valid");
        let contract =
            CapabilityContractRef::new("mobility", "reach_region", "v1").expect("contract valid");
        let intent = domain::ExecutionIntent::new(contract, BTreeMap::new()).expect("intent valid");
        let correlation = CorrelationId::new("bound-route-test").expect("correlation valid");
        assert!(
            matches!(bridge.execute_bound("execution-1".to_string(), &group_id, &role_id, intent, correlation), Err(IntegrationRuntimeError::Protocol(reason)) if reason.contains("unknown"))
        );
    }

    /// Older execution events cannot regress a newer reconnect snapshot.
    #[test]
    fn execution_sequence_fences_stale_reconnect_events() {
        let mut bridge = IntegrationRuntimeBridge::new(
            ControlPlane::new(),
            InMemorySharedNodeState::new(),
            InMemoryEventLog::new(),
            GrpcNodeRouter::default(),
        );
        let correlation = CorrelationId::new("sequence-test").expect("correlation valid");
        bridge
            .consume(
                GrpcNodeEvent::NodeMessage {
                    node_id: "dog-a".to_string(),
                    session_id: "session-new".to_string(),
                    message: crate::grpc::v0_2::NodeMessage {
                        message: Some(NodePayload::ExecutionSnapshot(
                            crate::grpc::v0_2::ExecutionSnapshot {
                                session_id: "session-new".to_string(),
                                execution_id: "execution-1".to_string(),
                                last_sequence: 3,
                                phase: ExecutionPhase::Completed as i32,
                                reason: String::new(),
                            },
                        )),
                    },
                },
                TimestampMs::new(3),
                &correlation,
            )
            .expect("snapshot is consumed");
        bridge
            .consume(
                GrpcNodeEvent::NodeMessage {
                    node_id: "dog-a".to_string(),
                    session_id: "session-new".to_string(),
                    message: crate::grpc::v0_2::NodeMessage {
                        message: Some(NodePayload::ExecutionEvent(
                            crate::grpc::v0_2::ExecutionEvent {
                                session_id: "session-new".to_string(),
                                execution_id: "execution-1".to_string(),
                                sequence: 2,
                                phase: ExecutionPhase::Started as i32,
                                reason: String::new(),
                            },
                        )),
                    },
                },
                TimestampMs::new(4),
                &correlation,
            )
            .expect("stale event is ignored");
        assert_eq!(
            bridge.execution_status("execution-1"),
            Some(RemoteExecutionStatus::Completed)
        );
    }

    /// A terminal execution fact is immutable even when a conflicting fact has a higher sequence.
    #[test]
    fn terminal_execution_status_rejects_later_conflict() {
        let mut bridge = IntegrationRuntimeBridge::new(
            ControlPlane::new(),
            InMemorySharedNodeState::new(),
            InMemoryEventLog::new(),
            GrpcNodeRouter::default(),
        );
        let correlation = CorrelationId::new("terminal-test").expect("correlation valid");
        bridge
            .consume(
                execution_snapshot("dog-a", "execution-terminal", 3, ExecutionPhase::Completed),
                TimestampMs::new(3),
                &correlation,
            )
            .expect("terminal snapshot is consumed");
        bridge
            .consume(
                execution_snapshot("dog-a", "execution-terminal", 4, ExecutionPhase::Completed),
                TimestampMs::new(4),
                &correlation,
            )
            .expect("same terminal status is idempotent");

        assert!(matches!(
            bridge.consume(
                execution_snapshot("dog-a", "execution-terminal", 5, ExecutionPhase::Failed),
                TimestampMs::new(5),
                &correlation,
            ),
            Err(IntegrationRuntimeError::Protocol(reason)) if reason.contains("immutable")
        ));
        assert_eq!(
            bridge.execution_status("execution-terminal"),
            Some(RemoteExecutionStatus::Completed)
        );
    }

    /// A high-sequence fact from another node cannot fence the execution owner's later fact.
    #[test]
    fn wrong_node_execution_fact_does_not_poison_sequence() {
        let mut bridge = IntegrationRuntimeBridge::new(
            ControlPlane::new(),
            InMemorySharedNodeState::new(),
            InMemoryEventLog::new(),
            GrpcNodeRouter::default(),
        );
        let correlation = CorrelationId::new("wrong-node-test").expect("correlation valid");
        bridge
            .consume(
                execution_snapshot("dog-a", "execution-owned", 1, ExecutionPhase::Started),
                TimestampMs::new(1),
                &correlation,
            )
            .expect("owner snapshot is consumed");

        assert!(matches!(
            bridge.consume(
                execution_snapshot("dog-b", "execution-owned", 100, ExecutionPhase::Failed),
                TimestampMs::new(2),
                &correlation,
            ),
            Err(IntegrationRuntimeError::Protocol(reason)) if reason.contains("owner")
        ));
        bridge
            .consume(
                execution_snapshot("dog-a", "execution-owned", 2, ExecutionPhase::Completed),
                TimestampMs::new(3),
                &correlation,
            )
            .expect("owner's next fact remains admissible");
        assert_eq!(
            bridge.execution_status("execution-owned"),
            Some(RemoteExecutionStatus::Completed)
        );
    }

    /// An invalid phase cannot advance the execution sequence before validation succeeds.
    #[test]
    fn invalid_execution_phase_does_not_poison_sequence() {
        let mut bridge = IntegrationRuntimeBridge::new(
            ControlPlane::new(),
            InMemorySharedNodeState::new(),
            InMemoryEventLog::new(),
            GrpcNodeRouter::default(),
        );
        let correlation = CorrelationId::new("invalid-phase-test").expect("correlation valid");
        assert!(matches!(
            bridge.consume(
                execution_snapshot_with_phase("dog-a", "execution-phase", 100, i32::MAX),
                TimestampMs::new(1),
                &correlation,
            ),
            Err(IntegrationRuntimeError::Protocol(reason)) if reason.contains("phase")
        ));
        bridge
            .consume(
                execution_snapshot("dog-a", "execution-phase", 1, ExecutionPhase::Started),
                TimestampMs::new(2),
                &correlation,
            )
            .expect("valid lower sequence is accepted after invalid phase");
        assert_eq!(
            bridge.execution_status("execution-phase"),
            Some(RemoteExecutionStatus::Running)
        );
    }

    /// Unknown execution is persisted and queued for reconciliation without a terminal outcome.
    #[test]
    fn unknown_execution_emits_recovery_evidence() {
        let command = ExecutionCommand::new(
            domain::MissionId::new("mission").expect("mission valid"),
            domain::TaskId::new("task").expect("task valid"),
            domain::ExecutionGroupId::new("group").expect("group valid"),
            domain::RoleId::new("role").expect("role valid"),
            NodeId::new("dog-a").expect("node valid"),
            domain::ExecutionIntent::new(
                CapabilityContractRef::new("compute", "noop", "v1").expect("contract valid"),
                BTreeMap::new(),
            )
            .expect("intent valid"),
            CorrelationId::new("unknown-test").expect("correlation valid"),
        );
        let mut bridge = IntegrationRuntimeBridge::new(
            ControlPlane::new(),
            InMemorySharedNodeState::new(),
            InMemoryEventLog::new(),
            GrpcNodeRouter::default(),
        );
        bridge
            .runtime
            .record_dispatched("execution-unknown".to_string(), command, Vec::new())
            .expect("dispatch records");

        bridge
            .consume(
                execution_snapshot("dog-a", "execution-unknown", 1, ExecutionPhase::Unknown),
                TimestampMs::new(1),
                &CorrelationId::new("unknown-test").expect("correlation valid"),
            )
            .expect("unknown fact is accepted");

        assert!(bridge.terminal_task_outcomes().is_empty());
        assert!(matches!(
            bridge.take_runtime_events().as_slice(),
            [ExecutionEvent::RecoveryRequired { .. }]
        ));
        assert!(bridge.events.contains_payload(|payload| matches!(
            payload,
            EventPayload::RuntimeExecutionRecoveryRequired { execution_id, .. }
                if execution_id == "execution-unknown"
        )));
    }

    /// A reconnect snapshot without a current Runtime command cannot be silently re-dispatched.
    #[test]
    fn observed_reconnect_execution_requires_reconciliation_before_route() {
        let mut bridge = IntegrationRuntimeBridge::new(
            ControlPlane::new(),
            InMemorySharedNodeState::new(),
            InMemoryEventLog::new(),
            GrpcNodeRouter::default(),
        );
        let correlation = CorrelationId::new("reconnect-command-test").expect("correlation valid");
        bridge
            .consume(
                GrpcNodeEvent::NodeMessage {
                    node_id: "dog-a".to_string(),
                    session_id: "session-new".to_string(),
                    message: crate::grpc::v0_2::NodeMessage {
                        message: Some(NodePayload::ExecutionSnapshot(
                            crate::grpc::v0_2::ExecutionSnapshot {
                                session_id: "session-new".to_string(),
                                execution_id: "execution-1".to_string(),
                                last_sequence: 1,
                                phase: ExecutionPhase::Started as i32,
                                reason: String::new(),
                            },
                        )),
                    },
                },
                TimestampMs::new(1),
                &correlation,
            )
            .expect("snapshot is consumed");
        let command = ExecutionCommand::new(
            domain::MissionId::new("mission").expect("mission valid"),
            domain::TaskId::new("task").expect("task valid"),
            domain::ExecutionGroupId::new("group").expect("group valid"),
            domain::RoleId::new("role").expect("role valid"),
            NodeId::new("dog-a").expect("node valid"),
            domain::ExecutionIntent::new(
                CapabilityContractRef::new("compute", "noop", "v1").expect("contract valid"),
                BTreeMap::new(),
            )
            .expect("intent valid"),
            correlation,
        );
        assert!(matches!(
            bridge.execute("execution-1".to_string(), command, Vec::new()),
            Err(IntegrationRuntimeError::Protocol(reason)) if reason.contains("reconciliation")
        ));
    }

    /// Restore clears process-local authority and never implicitly re-dispatches a command.
    #[test]
    fn checkpoint_restore_is_conservative_across_process_boundary() {
        let mut bridge = IntegrationRuntimeBridge::new(
            ControlPlane::new(),
            InMemorySharedNodeState::new(),
            InMemoryEventLog::new(),
            GrpcNodeRouter::default(),
        );
        let correlation = CorrelationId::new("checkpoint-test").expect("correlation valid");
        bridge
            .consume(
                GrpcNodeEvent::Registered {
                    session_id: "session-old".to_string(),
                    lease_id: "lease-old".to_string(),
                    registration: NodeRegistration {
                        node_id: "dog-a".to_string(),
                        local_systems: vec![LocalSystemDescriptor {
                            id: "motion".to_string(),
                            runtime: Some(WireRuntime {
                                name: "local-motion".to_string(),
                                version: "1".to_string(),
                            }),
                            metadata: Default::default(),
                        }],
                        capabilities: Vec::new(),
                        sensors: Vec::new(),
                        resources: Vec::new(),
                        metadata: Default::default(),
                        node_contract_version: "roboguide.node.v0.2".to_string(),
                    },
                },
                TimestampMs::new(100),
                &correlation,
            )
            .expect("registration consumed");
        let node_id = NodeId::new("dog-a").expect("node valid");
        let command = ExecutionCommand::new(
            domain::MissionId::new("mission").expect("mission valid"),
            domain::TaskId::new("task").expect("task valid"),
            domain::ExecutionGroupId::new("group").expect("group valid"),
            domain::RoleId::new("role").expect("role valid"),
            node_id.clone(),
            domain::ExecutionIntent::new(
                CapabilityContractRef::new("compute", "noop", "v1").expect("contract valid"),
                BTreeMap::new(),
            )
            .expect("intent valid"),
            correlation,
        );
        bridge
            .runtime
            .record_dispatched("execution-1".to_string(), command.clone(), Vec::new())
            .expect("execution dispatch records");
        bridge
            .runtime
            .observe_execution(
                "execution-1",
                node_id.clone(),
                4,
                ExecutionStatus::Running,
                "",
            )
            .expect("running fact records");

        let checkpoint = bridge.checkpoint_json().expect("checkpoint serializes");
        let mut restored = IntegrationRuntimeBridge::restore_from_checkpoint(
            &checkpoint,
            InMemoryEventLog::new(),
            GrpcNodeRouter::default(),
            TimestampMs::new(7),
        )
        .expect("checkpoint restores");

        assert!(restored.control().node_lease(&node_id).is_none());
        let snapshot = restored.state().node(&node_id).expect("node fact restored");
        assert_eq!(snapshot.reported_status_received_at(), TimestampMs::new(7));
        assert_eq!(
            snapshot.liveness().liveness(),
            domain::NodeLiveness::Unreachable
        );
        assert_eq!(snapshot.liveness().observed_at(), TimestampMs::new(7));
        assert_eq!(
            restored.execution_status("execution-1"),
            Some(RemoteExecutionStatus::Unknown)
        );
        assert!(matches!(
            restored.execute("execution-1".to_string(), command, Vec::new()),
            Err(IntegrationRuntimeError::Protocol(reason)) if reason.contains("controller restart")
        ));
    }

    /// Group aggregation ignores superseded execution facts for the same role.
    #[test]
    fn group_lifecycle_uses_current_role_execution() {
        let now = TimestampMs::new(0);
        let correlation = CorrelationId::new("current-execution-test").expect("correlation valid");
        let contract =
            CapabilityContractRef::new("mobility", "move", "v1").expect("contract valid");
        let registration = domain::NodeRegistration::new_with_contracts(
            NodeId::new("node-a").expect("node valid"),
            LocalRuntime::new("runtime", "1").expect("runtime valid"),
            NodeContractVersion::v0_1(),
            vec![Capability::new(CapabilityKind::Mobility, true)],
            vec![contract.clone()],
            Vec::new(),
        );
        let mut control = ControlPlane::new();
        let mut state = InMemorySharedNodeState::new();
        let mut events = InMemoryEventLog::new();
        control
            .register_node(
                &mut state,
                registration,
                NodeStatus::new(NodeHealth::Online, now),
                now,
                &correlation,
                &mut events,
            )
            .expect("node registers");
        let role_id = domain::RoleId::new("carrier").expect("role valid");
        let requirement = domain::TaskRequirement::new(
            domain::MissionId::new("mission-a").expect("mission valid"),
            domain::TaskId::new("task-a").expect("task valid"),
            vec![domain::RoleRequirement::new_with_actor_and_contract(
                role_id.clone(),
                domain::ActorId::new("carrier").expect("actor valid"),
                CapabilityKind::Mobility,
                contract.clone(),
                None,
            )],
        )
        .expect("requirement valid");
        let candidates = control
            .match_capabilities(&state, &requirement, now, &correlation, &mut events)
            .expect("matching succeeds");
        let proposal = control
            .propose(
                &state,
                &requirement,
                &candidates,
                vec![domain::RoleAssignment::new(
                    role_id.clone(),
                    NodeId::new("node-a").expect("node valid"),
                    Vec::new(),
                )],
                now,
                &correlation,
                &mut events,
            )
            .expect("proposal succeeds");
        let committed = control
            .commit(&proposal, now, &correlation, &mut events)
            .expect("commit succeeds");
        let group_id = domain::ExecutionGroupId::new("group-a").expect("group valid");
        control
            .create_group(group_id.clone(), &committed, now, &correlation, &mut events)
            .expect("group binds");
        control
            .activate_group(&group_id, now, &correlation, &mut events)
            .expect("group activates");
        let intent = domain::ExecutionIntent::new(contract, BTreeMap::new()).expect("intent valid");
        let command = ExecutionCommand::new(
            domain::MissionId::new("mission-a").expect("mission valid"),
            domain::TaskId::new("task-a").expect("task valid"),
            group_id.clone(),
            role_id.clone(),
            NodeId::new("node-a").expect("node valid"),
            intent,
            correlation.clone(),
        );
        let mut bridge =
            IntegrationRuntimeBridge::new(control, state, events, GrpcNodeRouter::default());
        bridge
            .runtime
            .record_dispatched("execution-old".to_string(), command.clone(), Vec::new())
            .expect("old execution records");
        bridge
            .runtime
            .observe_execution(
                "execution-old",
                command.node_id().clone(),
                1,
                ExecutionStatus::Failed,
                "old failure",
            )
            .expect("old failure records");
        bridge
            .runtime
            .record_dispatched("execution-current".to_string(), command.clone(), Vec::new())
            .expect("current execution records");
        bridge
            .runtime
            .observe_execution(
                "execution-current",
                command.node_id().clone(),
                1,
                ExecutionStatus::Running,
                "",
            )
            .expect("current running fact records");

        assert!(bridge.terminal_task_outcomes().is_empty());
        assert_eq!(
            bridge
                .control()
                .group(&group_id)
                .expect("group exists")
                .lifecycle(),
            control::GroupLifecycle::Active
        );
        bridge
            .runtime
            .observe_execution(
                "execution-current",
                command.node_id().clone(),
                2,
                ExecutionStatus::Completed,
                "",
            )
            .expect("current completion records");
        assert!(bridge.terminal_task_outcomes().is_empty());
        assert_eq!(
            bridge
                .control()
                .group(&group_id)
                .expect("group exists")
                .lifecycle(),
            control::GroupLifecycle::Active
        );
    }

    /// Builds one reconnect snapshot event with a validated execution phase.
    fn execution_snapshot(
        node_id: &str,
        execution_id: &str,
        sequence: u64,
        phase: ExecutionPhase,
    ) -> GrpcNodeEvent {
        execution_snapshot_with_phase(node_id, execution_id, sequence, phase as i32)
    }

    /// Builds one reconnect snapshot event with an arbitrary raw phase value.
    fn execution_snapshot_with_phase(
        node_id: &str,
        execution_id: &str,
        sequence: u64,
        phase: i32,
    ) -> GrpcNodeEvent {
        GrpcNodeEvent::NodeMessage {
            node_id: node_id.to_string(),
            session_id: "session-test".to_string(),
            message: crate::grpc::v0_2::NodeMessage {
                message: Some(NodePayload::ExecutionSnapshot(
                    crate::grpc::v0_2::ExecutionSnapshot {
                        session_id: "session-test".to_string(),
                        execution_id: execution_id.to_string(),
                        last_sequence: sequence,
                        phase,
                        reason: String::new(),
                    },
                )),
            },
        }
    }
}
