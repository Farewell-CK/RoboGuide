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
use state::InMemorySharedNodeState;
use std::collections::{BTreeMap, BTreeSet};
use std::fmt::{Display, Formatter};

/// Schema marker for the complete Integration/Control/State controller checkpoint.
pub const CONTROLLER_CHECKPOINT_SCHEMA: &str = "roboguide.controller-checkpoint/v2";

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
    executions: BTreeMap<String, RoutedExecution>,
    /// Latest execution lifecycle facts.
    execution_status: BTreeMap<String, RemoteExecutionStatus>,
    /// Last accepted execution-local sequence across sessions and snapshot replay.
    execution_sequences: BTreeMap<String, u64>,
    /// Node that first reported or received each stable execution identity.
    execution_nodes: BTreeMap<String, NodeId>,
    /// Current authoritative execution identity for each Group role.
    active_executions: BTreeMap<(domain::ExecutionGroupId, domain::RoleId), String>,
    /// Commands recovered from durable state that must never be implicitly sent again.
    restored_executions: BTreeSet<String>,
}

/// One Runtime command and the Control-committed resources for its bound role.
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
struct RoutedExecution {
    /// Existing canonical Runtime command.
    command: ExecutionCommand,
    /// Stable sorted committed resource identities.
    resource_ids: Vec<ResourceId>,
}

/// Serializable Group-role execution identity without a composite JSON map key.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
struct ActiveExecutionCheckpoint {
    /// Group owning the execution.
    group_id: domain::ExecutionGroupId,
    /// Role owning the execution.
    role_id: domain::RoleId,
    /// Stable execution identity currently associated with the role.
    execution_id: String,
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
    /// Runtime command contexts retained for reconciliation and terminal fact conversion.
    executions: BTreeMap<String, RoutedExecution>,
    /// Latest accepted remote status by execution identity.
    execution_status: BTreeMap<String, RemoteExecutionStatus>,
    /// Latest accepted node-local sequence by execution identity.
    execution_sequences: BTreeMap<String, u64>,
    /// Stable node ownership observed for each execution identity.
    execution_nodes: BTreeMap<String, NodeId>,
    /// Current execution identity for every Group role.
    active_executions: Vec<ActiveExecutionCheckpoint>,
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
            executions: BTreeMap::new(),
            execution_status: BTreeMap::new(),
            execution_sequences: BTreeMap::new(),
            execution_nodes: BTreeMap::new(),
            active_executions: BTreeMap::new(),
            restored_executions: BTreeSet::new(),
        }
    }

    /// Serializes a versioned checkpoint of Control, Shared State, and Runtime projections.
    pub fn checkpoint_json(&self) -> Result<String, IntegrationRuntimeError> {
        let checkpoint = ControllerCheckpoint {
            schema: CONTROLLER_CHECKPOINT_SCHEMA.to_string(),
            control: self.control.checkpoint(),
            nodes: self.state.snapshots(),
            executions: self.executions.clone(),
            execution_status: self.execution_status.clone(),
            execution_sequences: self.execution_sequences.clone(),
            execution_nodes: self.execution_nodes.clone(),
            active_executions: self
                .active_executions
                .iter()
                .map(
                    |((group_id, role_id), execution_id)| ActiveExecutionCheckpoint {
                        group_id: group_id.clone(),
                        role_id: role_id.clone(),
                        execution_id: execution_id.clone(),
                    },
                )
                .collect(),
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
        validate_execution_checkpoint(&checkpoint)?;
        let control = ControlPlane::restore(checkpoint.control)?;
        let state = InMemorySharedNodeState::restore(checkpoint.nodes, restored_at)
            .map_err(IntegrationRuntimeError::Checkpoint)?;
        let mut active_executions = BTreeMap::new();
        for active in checkpoint.active_executions {
            let key = (active.group_id, active.role_id);
            if active_executions.insert(key, active.execution_id).is_some() {
                return Err(IntegrationRuntimeError::Checkpoint(
                    "checkpoint contains duplicate active Group role".to_string(),
                ));
            }
        }
        let restored_executions = checkpoint.executions.keys().cloned().collect();
        let execution_status = checkpoint
            .execution_status
            .into_iter()
            .map(|(execution_id, status)| {
                let restored_status = if status.is_terminal() {
                    status
                } else {
                    RemoteExecutionStatus::Unknown
                };
                (execution_id, restored_status)
            })
            .collect();
        Ok(Self {
            control,
            state,
            events,
            router,
            executions: checkpoint.executions,
            execution_status,
            execution_sequences: checkpoint.execution_sequences,
            execution_nodes: checkpoint.execution_nodes,
            active_executions,
            restored_executions,
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
        if self.restored_executions.contains(&execution_id) {
            return Err(IntegrationRuntimeError::Protocol(
                "execution was restored after controller restart; reconciliation is required before routing"
                    .to_string(),
            ));
        }
        if self.execution_status.contains_key(&execution_id)
            && !self.executions.contains_key(&execution_id)
        {
            return Err(IntegrationRuntimeError::Protocol(
                "execution was observed during reconnect; reconciliation is required before routing"
                    .to_string(),
            ));
        }
        resource_ids.sort();
        resource_ids.dedup();
        if let Some(existing) = self.executions.get(&execution_id) {
            if existing.command != command || existing.resource_ids != resource_ids {
                return Err(IntegrationRuntimeError::ExecutionConflict(execution_id));
            }
            return Ok(());
        }
        self.router.execute(
            command.node_id().as_str(),
            execution_id.clone(),
            invocation_from_command(&command),
            resource_ids
                .iter()
                .map(|resource_id| resource_id.as_str().to_string())
                .collect(),
        )?;
        let node_id = command.node_id().clone();
        let execution_role = (command.group_id().clone(), command.role_id().clone());
        self.executions.insert(
            execution_id.clone(),
            RoutedExecution {
                command,
                resource_ids,
            },
        );
        self.execution_nodes.insert(execution_id.clone(), node_id);
        self.active_executions
            .insert(execution_role, execution_id.clone());
        self.execution_status
            .insert(execution_id, RemoteExecutionStatus::Accepted);
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
        let assignment = group
            .assignments()
            .iter()
            .find(|assignment| assignment.role_id() == role_id)
            .ok_or_else(|| {
                IntegrationRuntimeError::Protocol("group role is not bound".to_string())
            })?;
        let command = ExecutionCommand::new(
            group.task_ref().mission_id().clone(),
            group.task_id().clone(),
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
        let command = self
            .executions
            .get(execution_id)
            .ok_or_else(|| IntegrationRuntimeError::Protocol("unknown execution id".to_string()))?;
        self.router
            .cancel(command.command.node_id().as_str(), execution_id.to_string())
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
        self.execution_status.get(execution_id).copied()
    }

    /// Advances Group lifecycle from terminal execution facts already accepted by Runtime.
    pub fn advance_group_lifecycle(
        &mut self,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
    ) -> Result<(), IntegrationRuntimeError> {
        let group_ids = self.control.group_ids();
        for group_id in group_ids {
            let Some(group) = self.control.group(&group_id) else {
                continue;
            };
            if !matches!(
                group.lifecycle(),
                control::GroupLifecycle::Active | control::GroupLifecycle::Adapted
            ) {
                continue;
            }
            // Mission-level Groups aggregate TaskExecution lifecycles explicitly. The legacy
            // single-task role scan must never release such a Group when one Task completes.
            if group.task_executions().next().is_some() {
                continue;
            }
            let roles = group
                .assignments()
                .iter()
                .map(|assignment| assignment.role_id().clone())
                .collect::<Vec<_>>();
            let mut all_completed = true;
            let mut failed_role = None;
            for role_id in roles {
                let status = self
                    .active_executions
                    .get(&(group_id.clone(), role_id.clone()))
                    .and_then(|execution_id| self.execution_status.get(execution_id))
                    .copied();
                match status {
                    Some(RemoteExecutionStatus::Completed) => {}
                    Some(
                        RemoteExecutionStatus::Failed
                        | RemoteExecutionStatus::Cancelled
                        | RemoteExecutionStatus::Unknown,
                    ) => {
                        failed_role = Some(role_id);
                        break;
                    }
                    _ => all_completed = false,
                }
            }
            if let Some(role_id) = failed_role {
                self.control.block_group(
                    &group_id,
                    format!("execution for role {role_id} failed or is unknown"),
                    timestamp,
                    correlation_id,
                    &mut self.events,
                )?;
            } else if all_completed {
                self.control.complete_group(
                    &group_id,
                    timestamp,
                    correlation_id,
                    &mut self.events,
                )?;
                self.control.release_group(
                    &group_id,
                    timestamp,
                    correlation_id,
                    &mut self.events,
                )?;
            }
        }
        Ok(())
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
        if self
            .execution_nodes
            .get(fact.execution_id)
            .is_some_and(|expected| expected != &node_id)
        {
            return Err(IntegrationRuntimeError::Protocol(
                "execution fact node differs from execution owner".to_string(),
            ));
        }
        if self
            .execution_sequences
            .get(fact.execution_id)
            .is_some_and(|current| fact.sequence <= *current)
        {
            return Ok(());
        }
        let status = match phase {
            ExecutionPhase::Accepted => RemoteExecutionStatus::Accepted,
            ExecutionPhase::Started => RemoteExecutionStatus::Running,
            ExecutionPhase::Completed => RemoteExecutionStatus::Completed,
            ExecutionPhase::Failed => RemoteExecutionStatus::Failed,
            ExecutionPhase::Cancelled => RemoteExecutionStatus::Cancelled,
            ExecutionPhase::Unknown | ExecutionPhase::Unspecified => RemoteExecutionStatus::Unknown,
        };
        if let Some(current) = self.execution_status.get(fact.execution_id)
            && current.is_terminal()
        {
            if *current == status {
                return Ok(());
            }
            return Err(IntegrationRuntimeError::Protocol(
                "terminal execution status is immutable".to_string(),
            ));
        }
        if let Some(execution) = self.executions.get(fact.execution_id)
            && execution.command.node_id() != &node_id
        {
            return Err(IntegrationRuntimeError::Protocol(
                "execution fact node differs from dispatched command".to_string(),
            ));
        }
        self.execution_sequences
            .insert(fact.execution_id.to_string(), fact.sequence);
        self.execution_nodes
            .entry(fact.execution_id.to_string())
            .or_insert(node_id);
        self.execution_status
            .insert(fact.execution_id.to_string(), status);
        let Some(execution) = self.executions.get(fact.execution_id) else {
            return Ok(());
        };
        let command = &execution.command;
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
                reason: fact.reason.to_string(),
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

impl RemoteExecutionStatus {
    /// Returns whether no later execution lifecycle fact may change this status.
    const fn is_terminal(self) -> bool {
        matches!(self, Self::Completed | Self::Failed | Self::Cancelled)
    }
}

/// Validates cross-map Runtime checkpoint invariants before constructing live state.
fn validate_execution_checkpoint(
    checkpoint: &ControllerCheckpoint,
) -> Result<(), IntegrationRuntimeError> {
    for (execution_id, routed) in &checkpoint.executions {
        if execution_id.is_empty() {
            return Err(IntegrationRuntimeError::Checkpoint(
                "checkpoint contains an empty execution id".to_string(),
            ));
        }
        if checkpoint.execution_nodes.get(execution_id) != Some(routed.command.node_id()) {
            return Err(IntegrationRuntimeError::Checkpoint(format!(
                "execution {execution_id} has inconsistent node ownership"
            )));
        }
        if !checkpoint.execution_status.contains_key(execution_id) {
            return Err(IntegrationRuntimeError::Checkpoint(format!(
                "execution {execution_id} has no status"
            )));
        }
    }
    for active in &checkpoint.active_executions {
        let Some(routed) = checkpoint.executions.get(&active.execution_id) else {
            return Err(IntegrationRuntimeError::Checkpoint(format!(
                "active execution {} has no routed command",
                active.execution_id
            )));
        };
        if routed.command.group_id() != &active.group_id
            || routed.command.role_id() != &active.role_id
        {
            return Err(IntegrationRuntimeError::Checkpoint(format!(
                "active execution {} differs from its command Group role",
                active.execution_id
            )));
        }
    }
    for execution_id in checkpoint.execution_sequences.keys() {
        if !checkpoint.execution_status.contains_key(execution_id) {
            return Err(IntegrationRuntimeError::Checkpoint(format!(
                "execution sequence {execution_id} has no status"
            )));
        }
    }
    Ok(())
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
        assert_eq!(
            bridge.execution_sequences.get("execution-terminal"),
            Some(&3)
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
        assert_eq!(bridge.execution_sequences.get("execution-owned"), Some(&2));
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
        assert_eq!(bridge.execution_sequences.get("execution-phase"), Some(&1));
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
        bridge.executions.insert(
            "execution-1".to_string(),
            RoutedExecution {
                command: command.clone(),
                resource_ids: Vec::new(),
            },
        );
        bridge
            .execution_status
            .insert("execution-1".to_string(), RemoteExecutionStatus::Running);
        bridge
            .execution_sequences
            .insert("execution-1".to_string(), 4);
        bridge
            .execution_nodes
            .insert("execution-1".to_string(), node_id.clone());
        bridge.active_executions.insert(
            (command.group_id().clone(), command.role_id().clone()),
            "execution-1".to_string(),
        );

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
        for execution_id in ["execution-old", "execution-current"] {
            bridge.executions.insert(
                execution_id.to_string(),
                RoutedExecution {
                    command: command.clone(),
                    resource_ids: Vec::new(),
                },
            );
        }
        bridge
            .execution_status
            .insert("execution-old".to_string(), RemoteExecutionStatus::Failed);
        bridge.execution_status.insert(
            "execution-current".to_string(),
            RemoteExecutionStatus::Running,
        );
        bridge
            .active_executions
            .insert((group_id.clone(), role_id), "execution-current".to_string());

        bridge
            .advance_group_lifecycle(TimestampMs::new(1), &correlation)
            .expect("current running execution does not block group");
        assert_eq!(
            bridge
                .control()
                .group(&group_id)
                .expect("group exists")
                .lifecycle(),
            control::GroupLifecycle::Active
        );
        bridge.execution_status.insert(
            "execution-current".to_string(),
            RemoteExecutionStatus::Completed,
        );
        bridge
            .advance_group_lifecycle(TimestampMs::new(2), &correlation)
            .expect("current completion releases group");
        assert_eq!(
            bridge
                .control()
                .group(&group_id)
                .expect("group exists")
                .lifecycle(),
            control::GroupLifecycle::Released
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
