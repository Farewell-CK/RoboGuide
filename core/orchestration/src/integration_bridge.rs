//! Composition bridge from formal Node Protocol facts into Runtime/Control/State semantics.

use control::ControlPlane;
use domain::{
    Capability, CapabilityContractRef, CapabilityKind, CorrelationId, EventPayload,
    ExecutionCommand, ExecutionCouplingMode, ExecutionValue, LeaseId, LocalRuntime,
    LocalSystemDescriptor, LocalSystemId, MemoryKind, MemoryProviderDescriptor, MemoryScopeLimit,
    MemoryVisibility, NodeContractVersion, NodeEvent, NodeHealth, NodeHeartbeat, NodeId, NodeLease,
    NodeStatus, Resource, ResourceId, ResourceKind, SensorDescriptor, SensorId,
    StateExportDescriptor, StateObjectClass, StateObjectRef, StateRecord, StateSemantic,
    StateSource, TimestampMs,
};
use integration::grpc::v0_3::node_message::Message as NodePayload;
use integration::grpc::v0_3::{CanonicalInvocation, ExecutionPhase, NodeRegistration, ScalarValue};
use integration::{GrpcNodeEvent, GrpcNodeRouter};
use ports::{
    EventSink, SharedNodeStateReader, SharedNodeStateWriter, StateRecordReader, StateRecordWriter,
};
use runtime::{
    ExecutionEvent, ExecutionStatus, PeerChannelReadinessEvidence, RuntimeExecutionCheckpoint,
    RuntimeExecutionManager, RuntimeRelationSnapshot, SharedSpatialEvidence,
};
use state::{InMemorySharedNodeState, StateRecordProjection};
use std::collections::{BTreeMap, BTreeSet, VecDeque};
use std::fmt::{Display, Formatter};

/// Schema marker for the complete Integration/Control/State controller checkpoint.
///
/// Version 11 preserves source receive time and adds current-attempt coordination evidence.
pub const CONTROLLER_CHECKPOINT_SCHEMA: &str = "roboguide.controller-checkpoint/v11";

/// Immediately previous checkpoint accepted for one-step migration.
const PREVIOUS_CONTROLLER_CHECKPOINT_SCHEMA: &str = "roboguide.controller-checkpoint/v10";

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

/// Read-only Group-scoped view assembled from existing State evidence.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GroupSharedViewSnapshot {
    /// Mission-level Group that owns the view.
    group_id: domain::ExecutionGroupId,
    /// Coordination Context selecting the exposed member fields.
    context_id: domain::CoordinationContextId,
    /// Optional common map/frame interpretation.
    spatial_reference: Option<domain::SharedSpatialReference>,
    /// One result per logical Task/Role and declared field/schema binding.
    entries: Vec<GroupSharedViewEntry>,
}

/// Freshness classification for one Group view binding.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum GroupViewFreshness {
    /// Current State evidence remains inside its receive-relative validity window.
    Fresh,
    /// State evidence exists but its validity window has elapsed.
    Stale,
    /// No matching State evidence exists for the currently bound member.
    Unknown,
}

/// Strong localization status of one member against the Context's shared map/frame.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum GroupSpatialVerification {
    /// Current-attempt evidence proves the declared map revision and frame.
    Verified,
    /// No current-attempt strong localization evidence is available.
    Unknown,
    /// Current-attempt evidence names a different map revision or frame.
    Mismatched,
}

/// Read-only evidence for one logical Group member field.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GroupSharedViewEntry {
    /// Mission-scoped Task containing the logical member.
    task_ref: domain::TaskRef,
    /// Task-local Role occupying the logical member slot.
    role_id: domain::RoleId,
    /// Current bound Node supplying evidence.
    node_id: NodeId,
    /// Typed semantic field.
    field: domain::GroupViewField,
    /// Exact State payload schema selected by the Context, absent for Runtime execution status.
    payload_schema: Option<String>,
    /// Exact registered State export selected by the Context, absent for Runtime execution status.
    state_export_id: Option<String>,
    /// Latest independently attributed State evidence, when present.
    record: Option<StateRecord>,
    /// Current Runtime-owned execution status for an Execution field.
    execution_status: Option<RemoteExecutionStatus>,
    /// Receive-time freshness result, or Unknown when no record exists.
    freshness: Option<GroupViewFreshness>,
    /// Current-attempt strong localization evidence for the member, when available.
    spatial_evidence: Option<SharedSpatialEvidence>,
    /// Comparison with the Context's declared map/frame, when one is declared.
    spatial_verification: Option<GroupSpatialVerification>,
}

impl GroupSharedViewEntry {
    /// Returns the logical Task member.
    pub const fn task_ref(&self) -> &domain::TaskRef {
        &self.task_ref
    }

    /// Returns the logical Role member.
    pub const fn role_id(&self) -> &domain::RoleId {
        &self.role_id
    }

    /// Returns the current physical placement as evidence, not relation identity.
    pub const fn node_id(&self) -> &NodeId {
        &self.node_id
    }

    /// Returns the typed semantic field.
    pub const fn field(&self) -> domain::GroupViewField {
        self.field
    }

    /// Returns the selected State payload schema.
    pub fn payload_schema(&self) -> Option<&str> {
        self.payload_schema.as_deref()
    }

    /// Returns the exact node-wide State export identity.
    pub fn state_export_id(&self) -> Option<&str> {
        self.state_export_id.as_deref()
    }

    /// Returns the latest attributed State record when one exists.
    pub const fn record(&self) -> Option<&StateRecord> {
        self.record.as_ref()
    }

    /// Returns current Runtime status for an Execution field, when an attempt exists.
    pub const fn execution_status(&self) -> Option<RemoteExecutionStatus> {
        self.execution_status
    }

    /// Returns receive-time freshness when the Context requested it, including Unknown.
    pub const fn freshness(&self) -> Option<GroupViewFreshness> {
        self.freshness
    }

    /// Returns strong localization evidence tied to the current physical attempt.
    pub const fn spatial_evidence(&self) -> Option<&SharedSpatialEvidence> {
        self.spatial_evidence.as_ref()
    }

    /// Returns whether current evidence proves the declared shared spatial reference.
    pub const fn spatial_verification(&self) -> Option<GroupSpatialVerification> {
        self.spatial_verification
    }
}

impl GroupSharedViewSnapshot {
    /// Returns the owning Group.
    pub const fn group_id(&self) -> &domain::ExecutionGroupId {
        &self.group_id
    }

    /// Returns the declaring Context.
    pub const fn context_id(&self) -> &domain::CoordinationContextId {
        &self.context_id
    }

    /// Returns the optional shared spatial interpretation.
    pub const fn spatial_reference(&self) -> Option<&domain::SharedSpatialReference> {
        self.spatial_reference.as_ref()
    }

    /// Returns one deterministic result per member and declared binding.
    pub fn entries(&self) -> &[GroupSharedViewEntry] {
        &self.entries
    }
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
#[derive(Clone)]
pub struct IntegrationRuntimeBridge<E: Clone> {
    /// Existing Control authority for node leases and registration.
    control: ControlPlane,
    /// Shared Node State updated by remote facts.
    state: InMemorySharedNodeState,
    /// Source-aware State records kept separate from typed authority projections.
    state_records: StateRecordProjection,
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
    /// Independently attributed State channels, preserving their original receive times.
    #[serde(default)]
    state_records: Vec<StateRecord>,
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

impl<E: EventSink + Clone> IntegrationRuntimeBridge<E> {
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
            state_records: StateRecordProjection::new(),
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
            state_records: self.state_records.snapshots(),
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
        if !matches!(
            checkpoint.schema.as_str(),
            CONTROLLER_CHECKPOINT_SCHEMA | PREVIOUS_CONTROLLER_CHECKPOINT_SCHEMA
        ) {
            return Err(IntegrationRuntimeError::Checkpoint(format!(
                "unsupported controller checkpoint schema {}",
                checkpoint.schema
            )));
        }
        let control = ControlPlane::restore(checkpoint.control)?;
        let state = InMemorySharedNodeState::restore(checkpoint.nodes, restored_at)
            .map_err(IntegrationRuntimeError::Checkpoint)?;
        let state_records = StateRecordProjection::restore(checkpoint.state_records)
            .map_err(|error| IntegrationRuntimeError::Checkpoint(error.to_string()))?;
        let runtime = RuntimeExecutionManager::restore(checkpoint.runtime)
            .map_err(|error| IntegrationRuntimeError::Checkpoint(error.to_string()))?;
        Ok(Self {
            control,
            state,
            state_records,
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
        self.runtime.refresh_peer_channel_deadlines(received_at);
        match event {
            GrpcNodeEvent::Registered {
                lease_id,
                registration,
                ..
            } => {
                let registration = registration_from_wire(registration)?;
                let node_id = registration.node_id().clone();
                let lease = NodeLease::new(
                    LeaseId::new(lease_id)?,
                    node_id.clone(),
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
                self.runtime.fence_peer_channels_for_node(&node_id);
            }
            GrpcNodeEvent::NodeMessage {
                node_id,
                session_id,
                message,
            } => {
                // Direct composition callers may replay a protocol fact without a live gRPC
                // route.  When a route exists, however, the session fence is authoritative and
                // stale or expired sessions must be ignored.
                let current = if self
                    .router
                    .has_session(&node_id)
                    .map_err(|error| IntegrationRuntimeError::Protocol(error.to_string()))?
                {
                    self.router
                        .session_is_current(&node_id, &session_id)
                        .map_err(|error| IntegrationRuntimeError::Protocol(error.to_string()))?
                } else {
                    true
                };
                if !current {
                    return Ok(());
                }
                match message.message {
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
                        // A complete registration snapshot may change the LocalSystem that owns
                        // a committed capability. Require fresh endpoint proof under the new
                        // snapshot instead of retaining readiness admitted under the old one.
                        let node_id = NodeId::new(&node_id)?;
                        self.runtime.fence_peer_channels_for_node(&node_id);
                    }
                    Some(NodePayload::StateObservationBatch(batch)) => {
                        self.consume_state_observations(
                            &node_id,
                            &session_id,
                            batch.sequence,
                            batch.observations,
                            received_at,
                            correlation_id,
                        )?;
                    }
                    Some(NodePayload::PeerChannelReadiness(readiness)) => {
                        self.consume_peer_channel_readiness(
                            &node_id,
                            &session_id,
                            readiness,
                            received_at,
                            correlation_id,
                        )?;
                    }
                    _ => {}
                }
            }
            GrpcNodeEvent::Unavailable {
                node_id,
                session_id: _,
            } => {
                // A delayed disconnect from an old route must never make a newer session
                // appear unreachable.
                if self
                    .router
                    .has_session(&node_id)
                    .map_err(|error| IntegrationRuntimeError::Protocol(error.to_string()))?
                {
                    return Ok(());
                }
                let node_id_value = NodeId::new(&node_id)?;
                self.state.record_node_liveness(
                    &node_id_value,
                    domain::NodeLivenessObservation::new(
                        domain::NodeLiveness::Unreachable,
                        received_at,
                    ),
                )?;
                self.runtime.fence_peer_channels_for_node(&node_id_value);
            }
        }
        Ok(())
    }

    /// Validates an identified Local EAIOS acknowledgement against current Group ownership.
    fn consume_peer_channel_readiness(
        &mut self,
        node_id: &str,
        session_id: &str,
        readiness: integration::grpc::v0_3::PeerChannelReadiness,
        received_at: TimestampMs,
        correlation_id: &CorrelationId,
    ) -> Result<(), IntegrationRuntimeError> {
        if readiness.session_id != session_id {
            return Err(IntegrationRuntimeError::Protocol(
                "peer readiness session does not match the admitted Node route".to_string(),
            ));
        }
        let group_id = domain::ExecutionGroupId::new(readiness.group_id)
            .map_err(|error| IntegrationRuntimeError::Protocol(error.to_string()))?;
        let context_id = domain::CoordinationContextId::new(readiness.context_id)
            .map_err(|error| IntegrationRuntimeError::Protocol(error.to_string()))?;
        let context_role_id = domain::ContextRoleId::new(readiness.context_role_id)
            .map_err(|error| IntegrationRuntimeError::Protocol(error.to_string()))?;
        let local_system_id = LocalSystemId::new(readiness.local_system_id)
            .map_err(|error| IntegrationRuntimeError::Protocol(error.to_string()))?;
        let node_id = NodeId::new(node_id)
            .map_err(|error| IntegrationRuntimeError::Protocol(error.to_string()))?;
        if readiness.valid_for_ms == 0 || readiness.valid_for_ms > 60_000 {
            return Err(IntegrationRuntimeError::Protocol(
                "peer readiness validity must be between 1 and 60000 milliseconds".to_string(),
            ));
        }
        let registration = self
            .state
            .node(&node_id)
            .map(domain::NodeStateSnapshot::registration)
            .ok_or_else(|| {
                IntegrationRuntimeError::Protocol(
                    "peer readiness Node has no current registration".to_string(),
                )
            })?;
        let group = self.control.group(&group_id).ok_or_else(|| {
            IntegrationRuntimeError::Protocol("peer readiness Group is unknown".to_string())
        })?;
        let owns_role = group.task_executions().any(|execution| {
            execution.context_id() == &context_id
                && matches!(
                    execution.lifecycle(),
                    domain::TaskExecutionLifecycle::Ready | domain::TaskExecutionLifecycle::Active
                )
                && execution.assignments().iter().any(|assignment| {
                    assignment.node_id() == &node_id
                        && execution.context_role(assignment.role_id()) == Some(&context_role_id)
                        && group
                            .role_requirement(execution.task_ref(), assignment.role_id())
                            .and_then(domain::RoleRequirement::required_contract)
                            .and_then(|contract| registration.capability_owner(contract))
                            == Some(&local_system_id)
                })
        });
        if !owns_role {
            return Err(IntegrationRuntimeError::Protocol(
                "peer readiness Node/Local EAIOS does not own the ContextRole binding".to_string(),
            ));
        }
        let evidence = PeerChannelReadinessEvidence {
            group_id,
            context_id,
            context_role_id,
            node_id,
            local_system_id,
            session_id: session_id.to_string(),
            channel_instance_id: readiness.channel_instance_id,
            profile_id: readiness.profile_id,
            message_schema: readiness.message_schema,
            sequence: readiness.sequence,
            received_at,
            expires_at: TimestampMs::new(
                received_at
                    .as_millis()
                    .checked_add(readiness.valid_for_ms)
                    .ok_or_else(|| {
                        IntegrationRuntimeError::Protocol(
                            "peer readiness deadline overflowed".to_string(),
                        )
                    })?,
            ),
            ready: readiness.ready,
        };
        self.runtime
            .observe_peer_channel_readiness(evidence.clone())
            .map_err(|error| IntegrationRuntimeError::Protocol(error.to_string()))?;
        self.events.append(
            received_at,
            correlation_id,
            None,
            EventPayload::PeerChannelReadinessObserved {
                group_id: evidence.group_id,
                context_id: evidence.context_id,
                context_role_id: evidence.context_role_id,
                node_id: evidence.node_id,
                local_system_id: evidence.local_system_id,
                session_id: evidence.session_id,
                channel_instance_id: evidence.channel_instance_id,
                profile_id: evidence.profile_id,
                message_schema: evidence.message_schema,
                sequence: evidence.sequence,
                expires_at: evidence.expires_at,
                ready: evidence.ready,
            },
        );
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

    /// Builds and routes a command for a legacy single-Task Group role.
    ///
    /// Mission-level Groups must use [`Self::execute_task_bound`] so Integration never guesses a
    /// Task identity from the compatibility `ExecutionGroup::task_ref` field.
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
        if group.task_executions().next().is_some() {
            return Err(IntegrationRuntimeError::Protocol(
                "Mission-level Group dispatch requires an explicit TaskRef".to_string(),
            ));
        }
        let task_ref = group.task_ref().clone();
        self.execute_task_bound(
            execution_id,
            group_id,
            &task_ref,
            role_id,
            intent,
            TimestampMs::new(0),
            correlation_id,
        )
    }

    /// Builds and routes a command for one specific Task execution inside a Group.
    #[allow(clippy::too_many_arguments)]
    pub fn execute_task_bound(
        &mut self,
        execution_id: String,
        group_id: &domain::ExecutionGroupId,
        task_ref: &domain::TaskRef,
        role_id: &domain::RoleId,
        intent: domain::ExecutionIntent,
        now: TimestampMs,
        correlation_id: CorrelationId,
    ) -> Result<ExecutionCommand, IntegrationRuntimeError> {
        self.runtime.refresh_peer_channel_deadlines(now);
        let group = self.control.group(group_id).ok_or_else(|| {
            IntegrationRuntimeError::Protocol("execution group is unknown".to_string())
        })?;
        if !matches!(
            group.lifecycle(),
            control::GroupLifecycle::Bound | control::GroupLifecycle::Active
        ) {
            return Err(IntegrationRuntimeError::Protocol(
                "execution group is not bound".to_string(),
            ));
        }
        let (node_id, resource_ids) = if group.task_executions().next().is_none() {
            if task_ref != group.task_ref() {
                return Err(IntegrationRuntimeError::Protocol(
                    "legacy Group dispatch TaskRef does not match the Group".to_string(),
                ));
            }
            let assignment = group
                .assignments()
                .iter()
                .find(|assignment| assignment.role_id() == role_id)
                .ok_or_else(|| {
                    IntegrationRuntimeError::Protocol("group role is not bound".to_string())
                })?;
            (
                assignment.node_id().clone(),
                assignment.resource_ids().to_vec(),
            )
        } else {
            let execution = group.task_execution(task_ref).ok_or_else(|| {
                IntegrationRuntimeError::Protocol(
                    "TaskExecution is absent from the Mission-level Group".to_string(),
                )
            })?;
            let coordination_readiness = self.runtime.coordination_readiness_for_mode(
                group_id,
                execution.context_id(),
                execution.coupling_mode(),
            );
            let coordination_unavailable = match coordination_readiness {
                Some(runtime::CoordinationReadiness::Ready) => false,
                Some(_) => true,
                None => execution.coupling_mode() != domain::ExecutionCouplingMode::Independent,
            };
            if coordination_unavailable {
                return Err(IntegrationRuntimeError::Protocol(
                    "TaskExecution coordination mechanisms are not ready".to_string(),
                ));
            }
            if !matches!(
                execution.lifecycle(),
                domain::TaskExecutionLifecycle::Ready | domain::TaskExecutionLifecycle::Active
            ) {
                return Err(IntegrationRuntimeError::Protocol(
                    "TaskExecution is not dispatchable".to_string(),
                ));
            }
            if !task_assignments_are_complete(execution) {
                return Err(IntegrationRuntimeError::Protocol(
                    "TaskExecution bindings are incomplete".to_string(),
                ));
            }
            let assignment = execution
                .assignments()
                .iter()
                .find(|assignment| assignment.role_id() == role_id)
                .ok_or_else(|| {
                    IntegrationRuntimeError::Protocol("group role is not bound".to_string())
                })?;
            (
                assignment.node_id().clone(),
                assignment.resource_ids().to_vec(),
            )
        };
        let command = ExecutionCommand::new(
            task_ref.mission_id().clone(),
            task_ref.task_id().clone(),
            group_id.clone(),
            role_id.clone(),
            node_id,
            intent,
            correlation_id,
        );
        self.execute(execution_id, command.clone(), resource_ids)?;
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
    /// Returns independently attributed State records without cross-source fusion.
    pub const fn state_records(&self) -> &StateRecordProjection {
        &self.state_records
    }
    /// Returns current remote execution status.
    pub fn execution_status(&self, execution_id: &str) -> Option<RemoteExecutionStatus> {
        self.runtime
            .execution_status(execution_id)
            .map(remote_status)
    }

    /// Installs Mission-owned relation specifications into the sole Runtime live registry.
    pub fn register_execution_relations(
        &mut self,
        plan: &domain::MissionPlan,
        group_id: &domain::ExecutionGroupId,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
    ) -> Result<(), IntegrationRuntimeError> {
        crate::SupportedMechanismProfile::current()
            .validate(plan)
            .map_err(|error| IntegrationRuntimeError::Protocol(error.to_string()))?;
        let group = self.control.group(group_id).ok_or_else(|| {
            IntegrationRuntimeError::Protocol(
                "execution relations require an existing Mission-level Group".to_string(),
            )
        })?;
        if group.mission_id() != plan.goal().mission_id() {
            return Err(IntegrationRuntimeError::Protocol(
                "execution relation plan differs from Group Mission".to_string(),
            ));
        }
        let specifications = plan
            .contexts()
            .iter()
            .flat_map(domain::CoordinationContext::relations)
            .cloned()
            .collect::<Vec<_>>();
        let mut candidate_runtime = self.runtime.clone();
        for context in plan.contexts() {
            candidate_runtime
                .register_coordination_context(group_id, context)
                .map_err(|error| IntegrationRuntimeError::Protocol(error.to_string()))?;
        }
        let task_modes = effective_task_coupling_modes(plan);
        let runtime_events = candidate_runtime
            .register_relations_with_modes(
                group_id,
                plan.goal().mission_id(),
                &specifications,
                &task_modes,
            )
            .map_err(|error| IntegrationRuntimeError::Protocol(error.to_string()))?;
        self.runtime = candidate_runtime;
        for event in runtime_events {
            append_runtime_evidence(&mut self.events, &event, timestamp, correlation_id);
            self.runtime_events.push_back(event);
        }
        Ok(())
    }

    /// Confirms restored Runtime relation authority exactly matches an accepted MissionPlan.
    pub fn validate_execution_relations(
        &self,
        plan: &domain::MissionPlan,
        group_id: &domain::ExecutionGroupId,
    ) -> Result<(), IntegrationRuntimeError> {
        let group = self.control.group(group_id).ok_or_else(|| {
            IntegrationRuntimeError::Checkpoint(
                "execution relation Group is absent from Control authority".to_string(),
            )
        })?;
        if group.mission_id() != plan.goal().mission_id() {
            return Err(IntegrationRuntimeError::Checkpoint(
                "execution relation plan differs from restored Group Mission".to_string(),
            ));
        }
        let specifications = plan
            .contexts()
            .iter()
            .flat_map(domain::CoordinationContext::relations)
            .cloned()
            .collect::<Vec<_>>();
        let task_modes = effective_task_coupling_modes(plan);
        self.runtime
            .validate_coordination_contexts(group_id, plan.contexts())
            .map_err(|error| IntegrationRuntimeError::Checkpoint(error.to_string()))?;
        self.runtime
            .validate_relations_with_modes(
                group_id,
                plan.goal().mission_id(),
                &specifications,
                &task_modes,
            )
            .map_err(|error| IntegrationRuntimeError::Checkpoint(error.to_string()))
    }

    /// Returns observable live relation snapshots for one Mission-level Group.
    pub fn relation_snapshots(
        &self,
        group_id: &domain::ExecutionGroupId,
    ) -> Vec<RuntimeRelationSnapshot> {
        self.runtime.relation_snapshots(group_id)
    }

    /// Applies durable strong localization evidence to the current Runtime execution attempt.
    pub fn observe_localization_evidence(
        &mut self,
        evidence: &domain::LocalizationVerificationEvidence,
        received_at: TimestampMs,
        correlation_id: &CorrelationId,
    ) -> Result<(), IntegrationRuntimeError> {
        let spatial_evidence = SharedSpatialEvidence::from_localization(evidence, received_at);
        // Spatial Memory verification is useful outside an active execution relation. In that
        // case the durable catalog remains the authority and Runtime simply has no live slot to
        // update; evidence for a matching current slot is still validated strictly below.
        if !self
            .runtime
            .shared_spatial_evidence_matches_current_execution(&spatial_evidence)
        {
            return Ok(());
        }
        let runtime_events = self
            .runtime
            .observe_shared_spatial_evidence(spatial_evidence)
            .map_err(|error| IntegrationRuntimeError::Protocol(error.to_string()))?;
        for event in runtime_events {
            append_runtime_evidence(&mut self.events, &event, received_at, correlation_id);
            self.runtime_events.push_back(event);
        }
        Ok(())
    }

    /// Rehydrates current-attempt localization evidence without re-emitting durable events.
    ///
    /// Historical evidence for an older physical attempt remains in Spatial Memory but is not
    /// admitted to the current Runtime slot.
    pub fn restore_localization_evidence(
        &mut self,
        evidence: &domain::LocalizationVerificationEvidence,
        received_at: TimestampMs,
    ) -> Result<bool, IntegrationRuntimeError> {
        let evidence = SharedSpatialEvidence::from_localization(evidence, received_at);
        if !self
            .runtime
            .shared_spatial_evidence_targets_current_attempt(&evidence)
        {
            return Ok(false);
        }
        let changed = self.runtime.shared_spatial_evidence(
            evidence.group_id(),
            evidence.task_ref(),
            evidence.role_id(),
        ) != Some(&evidence);
        let _ = self
            .runtime
            .observe_shared_spatial_evidence(evidence)
            .map_err(|error| IntegrationRuntimeError::Checkpoint(error.to_string()))?;
        Ok(changed)
    }

    /// Returns transport-neutral direct peer channel lifecycle snapshots for one Group.
    pub fn peer_channel_snapshots(
        &self,
        group_id: &domain::ExecutionGroupId,
    ) -> Vec<runtime::RuntimePeerChannel> {
        self.runtime.peer_channels(group_id)
    }

    /// Returns peer channel snapshots with expired evidence conservatively shown as fenced.
    pub fn peer_channel_snapshots_at(
        &self,
        group_id: &domain::ExecutionGroupId,
        now: TimestampMs,
    ) -> Vec<runtime::RuntimePeerChannel> {
        self.runtime.peer_channels_at(group_id, now)
    }

    /// Returns whether one Context's declared coordination mechanisms are ready.
    pub fn coordination_readiness(
        &self,
        group_id: &domain::ExecutionGroupId,
        context_id: &domain::CoordinationContextId,
    ) -> Option<runtime::CoordinationReadiness> {
        self.runtime.coordination_readiness(group_id, context_id)
    }

    /// Builds a selective read-only Group view from State records and current bindings.
    pub fn group_shared_view(
        &self,
        plan: &domain::MissionPlan,
        group_id: &domain::ExecutionGroupId,
        context_id: &domain::CoordinationContextId,
        now: TimestampMs,
    ) -> Result<GroupSharedViewSnapshot, IntegrationRuntimeError> {
        let context = plan
            .contexts()
            .iter()
            .find(|context| context.context_id() == context_id)
            .ok_or_else(|| {
                IntegrationRuntimeError::Protocol("coordination Context is unknown".to_string())
            })?;
        let view = context.shared_view().ok_or_else(|| {
            IntegrationRuntimeError::Protocol("coordination Context has no shared view".to_string())
        })?;
        let group = self.control.group(group_id).ok_or_else(|| {
            IntegrationRuntimeError::Protocol("execution group is unknown".to_string())
        })?;
        if group.mission_id() != plan.goal().mission_id() {
            return Err(IntegrationRuntimeError::Protocol(
                "shared view plan differs from Group Mission".to_string(),
            ));
        }
        let context_tasks = plan
            .task_graph()
            .tasks()
            .iter()
            .filter(|task| task.continuity().context_id() == context_id)
            .map(|task| task.task_id())
            .collect::<BTreeSet<_>>();
        let state_records = self.state_records.records();
        let mut entries = Vec::new();
        for execution in group
            .task_executions()
            .filter(|execution| context_tasks.contains(execution.task_ref().task_id()))
        {
            for assignment in execution.assignments() {
                for binding in view.bindings() {
                    if execution.context_role(assignment.role_id())
                        != Some(binding.context_role_id())
                    {
                        continue;
                    }
                    let (record, execution_status, freshness) = match binding.field() {
                        domain::GroupViewField::Execution => (
                            None,
                            self.runtime
                                .current_execution_status(
                                    group_id,
                                    execution.task_ref(),
                                    assignment.role_id(),
                                )
                                .map(remote_status),
                            None,
                        ),
                        domain::GroupViewField::Pose | domain::GroupViewField::Velocity => {
                            let record = latest_group_record(
                                &state_records,
                                assignment.node_id(),
                                binding
                                    .state_export_id()
                                    .expect("validated spatial binding has State export"),
                                binding
                                    .payload_schema()
                                    .expect("validated spatial binding has payload schema"),
                            );
                            let freshness = view.include_freshness().then(|| match &record {
                                Some(record) if record.is_stale_at(now) => {
                                    GroupViewFreshness::Stale
                                }
                                Some(_) => GroupViewFreshness::Fresh,
                                None => GroupViewFreshness::Unknown,
                            });
                            (record, None, freshness)
                        }
                    };
                    let spatial_evidence = view.spatial_reference().and_then(|_| {
                        self.runtime
                            .shared_spatial_evidence(
                                group_id,
                                execution.task_ref(),
                                assignment.role_id(),
                            )
                            .cloned()
                    });
                    let spatial_verification = view.spatial_reference().map(|reference| {
                        spatial_evidence.as_ref().map_or(
                            GroupSpatialVerification::Unknown,
                            |evidence| {
                                if evidence.selector() == reference.selector()
                                    && evidence.frame_id() == reference.frame_id()
                                {
                                    GroupSpatialVerification::Verified
                                } else {
                                    GroupSpatialVerification::Mismatched
                                }
                            },
                        )
                    });
                    entries.push(GroupSharedViewEntry {
                        task_ref: execution.task_ref().clone(),
                        role_id: assignment.role_id().clone(),
                        node_id: assignment.node_id().clone(),
                        field: binding.field(),
                        payload_schema: binding.payload_schema().map(str::to_string),
                        state_export_id: binding.state_export_id().map(str::to_string),
                        record,
                        execution_status,
                        freshness,
                        spatial_evidence,
                        spatial_verification,
                    });
                }
            }
        }
        Ok(GroupSharedViewSnapshot {
            group_id: group_id.clone(),
            context_id: context_id.clone(),
            spatial_reference: view.spatial_reference().cloned(),
            entries,
        })
    }

    /// Fences a direct peer channel while preserving its logical Context identity.
    pub fn fence_peer_channel(
        &mut self,
        group_id: &domain::ExecutionGroupId,
        context_id: &domain::CoordinationContextId,
    ) -> Result<(), IntegrationRuntimeError> {
        self.runtime
            .fence_peer_channel(group_id, context_id)
            .map_err(|error| IntegrationRuntimeError::Protocol(error.to_string()))
    }

    /// Closes Runtime peer descriptors after application orchestration ends their Group scope.
    pub fn close_group_peer_channels(&mut self, group_id: &domain::ExecutionGroupId) {
        self.runtime.close_peer_channels_for_group(group_id);
    }

    /// Applies an explicit Control recovery acknowledgement to one satisfied relation.
    pub fn acknowledge_relation_reconciliation(
        &mut self,
        group_id: &domain::ExecutionGroupId,
        relation_id: &domain::ExecutionRelationId,
    ) -> Result<(), IntegrationRuntimeError> {
        self.runtime
            .acknowledge_relation_reconciliation(group_id, relation_id)
            .map_err(|error| IntegrationRuntimeError::Protocol(error.to_string()))
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
                    && task_assignments_are_complete(task)
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

    /// Converts one accepted protocol batch into an atomic State projection update and evidence.
    fn consume_state_observations(
        &mut self,
        node_id: &str,
        session_id: &str,
        sequence: u64,
        observations: Vec<integration::grpc::v0_3::StateObservation>,
        received_at: TimestampMs,
        correlation_id: &CorrelationId,
    ) -> Result<(), IntegrationRuntimeError> {
        let node_id = NodeId::new(node_id)?;
        let registration = self
            .state
            .node(&node_id)
            .ok_or_else(|| {
                IntegrationRuntimeError::Protocol(
                    "State observations require a registered node".to_string(),
                )
            })?
            .registration();
        let exports = registration
            .state_exports()
            .iter()
            .map(|export| (export.export_id(), export))
            .collect::<BTreeMap<_, _>>();
        let records = observations
            .into_iter()
            .map(|observation| {
                let export = exports.get(observation.export_id.as_str()).ok_or_else(|| {
                    IntegrationRuntimeError::Protocol(format!(
                        "State observation references undeclared export {}",
                        observation.export_id
                    ))
                })?;
                let value = serde_json::from_slice(&observation.json_value).map_err(|error| {
                    IntegrationRuntimeError::Protocol(format!(
                        "State observation JSON is invalid: {error}"
                    ))
                })?;
                StateRecord::new_with_source_epoch(
                    export.object().clone(),
                    export.semantic(),
                    StateSource::Node {
                        node_id: node_id.clone(),
                        local_system_id: export.local_system_id().clone(),
                    },
                    export.export_id(),
                    export.payload_schema(),
                    value,
                    observation
                        .has_source_observed_at
                        .then(|| TimestampMs::new(observation.source_observed_at_ms)),
                    received_at,
                    export.valid_for_ms(),
                    observation
                        .has_confidence
                        .then_some(observation.confidence_millionths),
                    Some(session_id.to_string()),
                    sequence,
                )
                .map_err(Into::into)
            })
            .collect::<Result<Vec<_>, IntegrationRuntimeError>>()?;
        let mut candidate = self.state_records.clone();
        for record in &records {
            candidate.record_state(record.clone())?;
        }
        self.state_records = candidate;
        for record in records {
            self.events.append(
                received_at,
                correlation_id,
                None,
                EventPayload::StateRecordObserved { record },
            );
        }
        Ok(())
    }
}

/// Returns whether committed assignments exactly and uniquely cover a TaskExecution's roles.
fn task_assignments_are_complete(execution: &domain::TaskExecution) -> bool {
    let expected_roles = execution.role_scopes().keys().collect::<BTreeSet<_>>();
    let assigned_roles = execution
        .assignments()
        .iter()
        .map(|assignment| assignment.role_id())
        .collect::<BTreeSet<_>>();
    expected_roles == assigned_roles && execution.assignments().len() == assigned_roles.len()
}

/// Computes each Task's effective coupling mode from its Context default and override.
fn effective_task_coupling_modes(
    plan: &domain::MissionPlan,
) -> BTreeMap<domain::TaskId, ExecutionCouplingMode> {
    plan.task_graph()
        .tasks()
        .iter()
        .map(|task| {
            let continuity = task.continuity();
            let mode = plan
                .contexts()
                .iter()
                .find(|context| context.context_id() == continuity.context_id())
                .map(|context| {
                    continuity
                        .coupling_mode_override()
                        .unwrap_or_else(|| context.coupling_mode())
                })
                .unwrap_or_default();
            (task.task_id().clone(), mode)
        })
        .collect()
}

/// Selects the latest evidence for one exact node export and payload schema.
fn latest_group_record(
    records: &[StateRecord],
    node_id: &NodeId,
    state_export_id: &str,
    payload_schema: &str,
) -> Option<StateRecord> {
    records
        .iter()
        .filter(|record| {
            record.key().source().node_id() == Some(node_id)
                && record.key().channel_id() == state_export_id
                && record.payload_schema() == payload_schema
        })
        .max_by_key(|record| (record.received_at(), record.sequence()))
        .cloned()
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
        ExecutionEvent::RelationRegistered { relation } => {
            EventPayload::ExecutionRelationRegistered {
                group_id: relation.group_id().clone(),
                relation_id: relation.relation_id().clone(),
                source_task_ref: relation.source_task_ref().clone(),
                source_role_id: relation.source_role_id().clone(),
                target_task_ref: relation.target_task_ref().clone(),
                target_role_id: relation.target_role_id().clone(),
                kind: relation.kind(),
                relation_type: relation.relation_type().clone(),
                coupling_mode: relation.coupling_mode(),
            }
        }
        ExecutionEvent::RelationStateChanged {
            relation,
            previous,
            current,
            source_execution_id,
            target_execution_id,
        } => EventPayload::ExecutionRelationStateChanged {
            group_id: relation.group_id().clone(),
            relation_id: relation.relation_id().clone(),
            previous: *previous,
            current: *current,
            source_execution_id: source_execution_id.clone(),
            target_execution_id: target_execution_id.clone(),
            relation_type: relation.relation_type().clone(),
            coupling_mode: relation.coupling_mode(),
        },
        ExecutionEvent::RelationReconciliationRequired {
            relation,
            state,
            source_execution_id,
            target_execution_id,
            reason,
        } => EventPayload::ExecutionRelationReconciliationRequired {
            group_id: relation.group_id().clone(),
            relation_id: relation.relation_id().clone(),
            state: *state,
            source_task_ref: relation.source_task_ref().clone(),
            source_role_id: relation.source_role_id().clone(),
            target_task_ref: relation.target_task_ref().clone(),
            target_role_id: relation.target_role_id().clone(),
            source_execution_id: source_execution_id.clone(),
            target_execution_id: target_execution_id.clone(),
            reason: reason.clone(),
            relation_type: relation.relation_type().clone(),
            coupling_mode: relation.coupling_mode(),
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
    let state_exports = wire
        .state_exports
        .iter()
        .map(state_export_from_wire)
        .collect::<Result<Vec<_>, _>>()?;
    let memory_providers = wire
        .memory_providers
        .iter()
        .map(memory_provider_from_wire)
        .collect::<Result<Vec<_>, _>>()?;
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
    let mut contract_kinds = BTreeMap::new();
    let mut capability_readiness = BTreeMap::new();
    for capability in wire.capabilities {
        let kind = capability_kind(&capability.kind)?;
        capability_kinds
            .entry(kind)
            .and_modify(|available| *available |= capability.available)
            .or_insert(capability.available);
        let owner = LocalSystemId::new(capability.local_system_id)?;
        for contract in capability.contracts {
            let contract = parse_contract(&contract)?;
            if capability_owners
                .insert(contract.clone(), owner.clone())
                .is_some()
            {
                return Err(IntegrationRuntimeError::Protocol(
                    "canonical capability has multiple owners".to_string(),
                ));
            }
            contract_kinds.insert(contract.clone(), kind);
            capability_readiness.insert(contract, capability.available);
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
    Ok(
        domain::NodeRegistration::new_with_local_systems_and_readiness(
            NodeId::new(wire.node_id)?,
            local_systems,
            NodeContractVersion::new(wire.node_contract_version)?,
            capabilities,
            capability_owners,
            contract_kinds,
            capability_readiness,
            sensors,
            resources,
            resource_owners,
        )?
        .with_state_memory_exports(state_exports, memory_providers)?,
    )
}

/// Converts one wire State export through the node-owned authority invariants.
fn state_export_from_wire(
    wire: &integration::grpc::v0_3::StateExportDescriptor,
) -> Result<StateExportDescriptor, IntegrationRuntimeError> {
    let object_class = match integration::grpc::v0_3::StateObjectClass::try_from(wire.object_class)
    {
        Ok(integration::grpc::v0_3::StateObjectClass::Node) => StateObjectClass::Node,
        Ok(integration::grpc::v0_3::StateObjectClass::World) => StateObjectClass::World,
        Ok(integration::grpc::v0_3::StateObjectClass::Roboguide) => StateObjectClass::RoboGuide,
        _ => {
            return Err(IntegrationRuntimeError::Protocol(
                "unknown State object class".to_string(),
            ));
        }
    };
    let semantic = match integration::grpc::v0_3::StateSemantic::try_from(wire.semantic) {
        Ok(integration::grpc::v0_3::StateSemantic::Reported) => StateSemantic::Reported,
        Ok(integration::grpc::v0_3::StateSemantic::Observed) => StateSemantic::Observed,
        Ok(integration::grpc::v0_3::StateSemantic::Desired) => StateSemantic::Desired,
        Ok(integration::grpc::v0_3::StateSemantic::Committed) => StateSemantic::Committed,
        Ok(integration::grpc::v0_3::StateSemantic::Derived) => StateSemantic::Derived,
        Ok(integration::grpc::v0_3::StateSemantic::Belief) => StateSemantic::Belief,
        _ => {
            return Err(IntegrationRuntimeError::Protocol(
                "unknown State semantic".to_string(),
            ));
        }
    };
    Ok(StateExportDescriptor::new(
        wire.export_id.clone(),
        LocalSystemId::new(wire.local_system_id.clone())?,
        StateObjectRef::new(
            object_class,
            wire.object_type.clone(),
            wire.object_id.clone(),
        )?,
        semantic,
        wire.payload_schema.clone(),
        wire.valid_for_ms,
    )?)
}

/// Converts one wire Memory provider without creating a new storage authority.
fn memory_provider_from_wire(
    wire: &integration::grpc::v0_3::MemoryProviderDescriptor,
) -> Result<MemoryProviderDescriptor, IntegrationRuntimeError> {
    let kind = match integration::grpc::v0_3::MemoryKind::try_from(wire.kind) {
        Ok(integration::grpc::v0_3::MemoryKind::Execution) => MemoryKind::Execution,
        Ok(integration::grpc::v0_3::MemoryKind::Spatial) => MemoryKind::Spatial,
        Ok(integration::grpc::v0_3::MemoryKind::Semantic) => MemoryKind::Semantic,
        Ok(integration::grpc::v0_3::MemoryKind::Experience) => MemoryKind::Experience,
        Ok(integration::grpc::v0_3::MemoryKind::Artifact) => MemoryKind::Artifact,
        _ => {
            return Err(IntegrationRuntimeError::Protocol(
                "unknown Memory kind".to_string(),
            ));
        }
    };
    let scope = match integration::grpc::v0_3::MemoryScopeKind::try_from(wire.scope) {
        Ok(integration::grpc::v0_3::MemoryScopeKind::Local) => MemoryScopeLimit::Local,
        Ok(integration::grpc::v0_3::MemoryScopeKind::ExecutionGroup) => {
            return Err(IntegrationRuntimeError::Protocol(
                "Memory provider scope cannot contain an execution Group identity".to_string(),
            ));
        }
        Ok(integration::grpc::v0_3::MemoryScopeKind::Global) => MemoryScopeLimit::Global,
        _ => {
            return Err(IntegrationRuntimeError::Protocol(
                "unknown Memory scope".to_string(),
            ));
        }
    };
    let visibility = match integration::grpc::v0_3::MemoryVisibility::try_from(wire.visibility) {
        Ok(integration::grpc::v0_3::MemoryVisibility::Discoverable) => {
            MemoryVisibility::Discoverable
        }
        Ok(integration::grpc::v0_3::MemoryVisibility::Exchangeable) => {
            MemoryVisibility::Exchangeable
        }
        _ => {
            return Err(IntegrationRuntimeError::Protocol(
                "unknown Memory visibility".to_string(),
            ));
        }
    };
    Ok(MemoryProviderDescriptor::new(
        wire.provider_id.clone(),
        LocalSystemId::new(wire.local_system_id.clone())?,
        kind,
        scope,
        visibility,
        wire.payload_schema.clone(),
        wire.media_type.clone(),
    )?)
}

/// Converts current protocol health into Domain health.
fn status_from_wire(
    status: Option<&integration::grpc::v0_3::NodeStatus>,
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
    use integration::grpc::v0_3::scalar_value::Value;
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
    /// Source-aware State rejected an ordering or conflict invariant.
    StateRecord(ports::StateRecordError),
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
            Self::StateRecord(error) => error.fmt(f),
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
impl From<ports::StateRecordError> for IntegrationRuntimeError {
    /// Preserves source-aware State projection failures at the composition boundary.
    fn from(value: ports::StateRecordError) -> Self {
        Self::StateRecord(value)
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
    use integration::grpc::v0_3::{
        Capability as WireCapability, LocalRuntime as WireRuntime, LocalSystemDescriptor,
    };
    use ports::{SharedNodeStateReader, StateRecordReader};
    use testkit::InMemoryEventLog;

    /// Builds one complete MissionPlan so integration tests use the production Group authority.
    fn single_task_plan(
        requirement: domain::TaskRequirement,
        intent: domain::ExecutionIntent,
    ) -> domain::MissionPlan {
        let mission_id = requirement.mission_id().clone();
        let intents = requirement
            .roles()
            .iter()
            .map(|role| (role.role_id().clone(), intent.clone()))
            .collect();
        let context_id = domain::CoordinationContextId::new("integration-test-context")
            .expect("context identity is valid");
        let task = domain::PlannedTask::new(
            "exercise integration runtime",
            requirement,
            intents,
            Vec::new(),
            domain::TaskContinuity::new(context_id.clone(), BTreeMap::new(), BTreeMap::new()),
        )
        .expect("test Task is valid");
        domain::MissionPlan::new(
            domain::MissionGoal::new(mission_id.clone(), "exercise integration runtime")
                .expect("test Mission goal is valid"),
            domain::TaskGraph::new(mission_id, vec![task]).expect("test Task Graph is valid"),
            vec![
                domain::CoordinationContext::new(context_id, Vec::new())
                    .expect("test Context is valid"),
            ],
        )
        .expect("test MissionPlan is valid")
    }

    /// Builds one same-Task two-Role plan with a Node-independent execution relation.
    fn related_single_task_plan() -> domain::MissionPlan {
        let mission_id = domain::MissionId::new("mission-relation").expect("mission id is valid");
        let task_id = domain::TaskId::new("guidance").expect("task id is valid");
        let source_role = domain::RoleId::new("safety-observer").expect("role id is valid");
        let target_role = domain::RoleId::new("navigator").expect("role id is valid");
        let requirement = domain::TaskRequirement::new(
            mission_id.clone(),
            task_id.clone(),
            vec![
                domain::RoleRequirement::new(
                    source_role.clone(),
                    CapabilityKind::Observation,
                    None,
                ),
                domain::RoleRequirement::new(target_role.clone(), CapabilityKind::Mobility, None),
            ],
        )
        .expect("requirement is valid");
        let intent = domain::ExecutionIntent::new(
            CapabilityContractRef::new("test", "execute", "v1").expect("contract is valid"),
            BTreeMap::new(),
        )
        .expect("intent is valid");
        let context_id =
            domain::CoordinationContextId::new("guidance-context").expect("context id is valid");
        let task = domain::PlannedTask::new(
            "exercise relation integration",
            requirement,
            BTreeMap::from([
                (source_role.clone(), intent.clone()),
                (target_role.clone(), intent),
            ]),
            Vec::new(),
            domain::TaskContinuity::new(context_id.clone(), BTreeMap::new(), BTreeMap::new()),
        )
        .expect("task is valid");
        let relation = domain::ExecutionRelationSpec::new(
            domain::ExecutionRelationId::new("safety-guards-navigation")
                .expect("relation id is valid"),
            domain::PlannedExecutionRef::new(task_id.clone(), source_role),
            domain::PlannedExecutionRef::new(task_id, target_role),
            domain::ExecutionRelationKind::RequiresActive,
        )
        .expect("relation is valid");
        domain::MissionPlan::new(
            domain::MissionGoal::new(mission_id.clone(), "exercise relation integration")
                .expect("goal is valid"),
            domain::TaskGraph::new(mission_id, vec![task]).expect("graph is valid"),
            vec![
                domain::CoordinationContext::new_with_relations(
                    context_id,
                    Vec::new(),
                    vec![relation],
                )
                .expect("context is valid"),
            ],
        )
        .expect("relation plan is valid")
    }

    /// Relation registration emits durable evidence and survives checkpoint validation.
    #[test]
    fn relation_registration_round_trips_through_integration_checkpoint() {
        let plan = related_single_task_plan();
        let group_id = domain::ExecutionGroupId::new("group-relation").expect("group id is valid");
        let correlation = CorrelationId::new("relation-registration").expect("correlation valid");
        let mut control = ControlPlane::new();
        control
            .create_mission_group(
                group_id.clone(),
                &plan,
                TimestampMs::new(5),
                &correlation,
                &mut InMemoryEventLog::new(),
            )
            .expect("Mission Group is created before Runtime relation registration");
        let mut bridge = IntegrationRuntimeBridge::new(
            control,
            InMemorySharedNodeState::new(),
            InMemoryEventLog::new(),
            GrpcNodeRouter::default(),
        );
        bridge
            .register_execution_relations(&plan, &group_id, TimestampMs::new(10), &correlation)
            .expect("relation registers");
        assert!(bridge.events.contains_payload(|payload| matches!(
            payload,
            EventPayload::ExecutionRelationRegistered { relation_id, .. }
                if relation_id.as_str() == "safety-guards-navigation"
        )));
        assert_eq!(
            bridge.relation_snapshots(&group_id)[0].state(),
            domain::ExecutionRelationState::Dormant
        );

        let checkpoint = bridge.checkpoint_json().expect("checkpoint serializes");
        let restored = IntegrationRuntimeBridge::restore_from_checkpoint(
            &checkpoint,
            InMemoryEventLog::new(),
            GrpcNodeRouter::default(),
            TimestampMs::new(20),
        )
        .expect("checkpoint restores");
        restored
            .validate_execution_relations(&plan, &group_id)
            .expect("restored relation registry matches MissionPlan");
        assert_eq!(restored.relation_snapshots(&group_id).len(), 1);
    }

    /// The v10 checkpoint migrates with empty newly introduced live coordination evidence.
    #[test]
    fn v10_checkpoint_migrates_missing_live_coordination_evidence() {
        let bridge = IntegrationRuntimeBridge::new(
            ControlPlane::new(),
            InMemorySharedNodeState::new(),
            InMemoryEventLog::new(),
            GrpcNodeRouter::default(),
        );
        let mut checkpoint: serde_json::Value = serde_json::from_str(
            &bridge
                .checkpoint_json()
                .expect("current checkpoint serializes"),
        )
        .expect("current checkpoint is JSON");
        checkpoint["schema"] = serde_json::json!(PREVIOUS_CONTROLLER_CHECKPOINT_SCHEMA);
        let runtime = checkpoint["runtime"]
            .as_object_mut()
            .expect("Runtime checkpoint is an object");
        runtime.remove("spatial_evidence");

        let restored = IntegrationRuntimeBridge::restore_from_checkpoint(
            &checkpoint.to_string(),
            InMemoryEventLog::new(),
            GrpcNodeRouter::default(),
            TimestampMs::new(1),
        )
        .expect("previous checkpoint migrates");
        assert!(
            restored
                .peer_channel_snapshots(
                    &domain::ExecutionGroupId::new("unused").expect("group id is valid")
                )
                .is_empty()
        );
    }

    /// Integration cannot create a relation registry beside an absent Control Group.
    #[test]
    fn relation_registration_requires_existing_control_group() {
        let plan = related_single_task_plan();
        let mut bridge = IntegrationRuntimeBridge::new(
            ControlPlane::new(),
            InMemorySharedNodeState::new(),
            InMemoryEventLog::new(),
            GrpcNodeRouter::default(),
        );
        let result = bridge.register_execution_relations(
            &plan,
            &domain::ExecutionGroupId::new("group-absent").expect("group id is valid"),
            TimestampMs::new(10),
            &CorrelationId::new("relation-registration").expect("correlation valid"),
        );
        assert!(matches!(
            result,
            Err(IntegrationRuntimeError::Protocol(reason))
                if reason.contains("existing Mission-level Group")
        ));
    }

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
                        node_contract_version: "roboguide.node.v0.3".to_string(),
                        state_exports: Vec::new(),
                        memory_providers: Vec::new(),
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
                    message: integration::grpc::v0_3::NodeMessage {
                        message: Some(NodePayload::Heartbeat(integration::grpc::v0_3::Heartbeat {
                            session_id: "session-1".to_string(),
                            lease_id: "lease-1".to_string(),
                            sequence: 1,
                            status: Some(integration::grpc::v0_3::NodeStatus {
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
                    message: integration::grpc::v0_3::NodeMessage {
                        message: Some(NodePayload::RegistrationUpdate(
                            integration::grpc::v0_3::RegistrationUpdate {
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
                                    node_contract_version: "roboguide.node.v0.3".to_string(),
                                    state_exports: Vec::new(),
                                    memory_providers: Vec::new(),
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
        bridge
            .checkpoint_json()
            .expect("registered node owner maps must checkpoint");
    }

    /// State observations persist independently and do not mutate health or Control authority.
    #[test]
    fn state_observations_are_evidence_without_health_or_control_side_effects() {
        let router = GrpcNodeRouter::default();
        let mut bridge = IntegrationRuntimeBridge::new(
            ControlPlane::new(),
            InMemorySharedNodeState::new(),
            InMemoryEventLog::new(),
            router,
        );
        let correlation = CorrelationId::new("state-observation").expect("correlation is valid");
        bridge
            .consume(
                GrpcNodeEvent::Registered {
                    session_id: "session-state".to_string(),
                    lease_id: "lease-state".to_string(),
                    registration: NodeRegistration {
                        node_id: "cane-a".to_string(),
                        local_systems: vec![LocalSystemDescriptor {
                            id: "safety".to_string(),
                            runtime: Some(WireRuntime {
                                name: "safety-runtime".to_string(),
                                version: "1".to_string(),
                            }),
                            metadata: Default::default(),
                        }],
                        capabilities: Vec::new(),
                        sensors: Vec::new(),
                        resources: Vec::new(),
                        metadata: Default::default(),
                        node_contract_version: "roboguide.node.v0.3".to_string(),
                        state_exports: vec![integration::grpc::v0_3::StateExportDescriptor {
                            export_id: "hazard-state".to_string(),
                            local_system_id: "safety".to_string(),
                            object_class: integration::grpc::v0_3::StateObjectClass::World as i32,
                            object_type: "hazard".to_string(),
                            object_id: "crossing-a".to_string(),
                            semantic: integration::grpc::v0_3::StateSemantic::Observed as i32,
                            payload_schema: "example.hazard/v1".to_string(),
                            valid_for_ms: 1_000,
                        }],
                        memory_providers: Vec::new(),
                    },
                },
                TimestampMs::new(1),
                &correlation,
            )
            .expect("registration is accepted");
        let node_id = NodeId::new("cane-a").expect("node id is valid");
        let lease_before = bridge
            .control()
            .node_lease(&node_id)
            .expect("registration creates a Control lease")
            .clone();

        bridge
            .consume(
                GrpcNodeEvent::NodeMessage {
                    node_id: "cane-a".to_string(),
                    session_id: "session-state".to_string(),
                    message: integration::grpc::v0_3::NodeMessage {
                        message: Some(NodePayload::StateObservationBatch(
                            integration::grpc::v0_3::StateObservationBatch {
                                session_id: "session-state".to_string(),
                                sequence: 1,
                                observations: vec![integration::grpc::v0_3::StateObservation {
                                    export_id: "hazard-state".to_string(),
                                    json_value: br#"{"present":true}"#.to_vec(),
                                    has_source_observed_at: true,
                                    source_observed_at_ms: 500_000,
                                    has_confidence: true,
                                    confidence_millionths: 950_000,
                                }],
                            },
                        )),
                    },
                },
                TimestampMs::new(2),
                &correlation,
            )
            .expect("State observation is accepted as evidence");

        assert_eq!(bridge.state_records().records().len(), 1);
        let record = &bridge.state_records().records()[0];
        assert_eq!(record.received_at(), TimestampMs::new(2));
        assert_eq!(record.source_observed_at(), Some(TimestampMs::new(500_000)));
        assert_eq!(
            bridge
                .state()
                .node(&node_id)
                .expect("registered node remains visible")
                .reported_status()
                .health(),
            NodeHealth::Offline
        );
        assert_eq!(
            bridge
                .control()
                .node_lease(&node_id)
                .expect("Control lease remains present"),
            &lease_before
        );
        assert!(bridge.events.contains_payload(|payload| matches!(
            payload,
            EventPayload::StateRecordObserved { record }
                if record.key().channel_id() == "hazard-state"
        )));

        let checkpoint = bridge.checkpoint_json().expect("State record checkpoints");
        let restored = IntegrationRuntimeBridge::restore_from_checkpoint(
            &checkpoint,
            InMemoryEventLog::new(),
            GrpcNodeRouter::default(),
            TimestampMs::new(2_000),
        )
        .expect("State record restores");
        let restored_record = &restored.state_records().records()[0];
        assert_eq!(restored_record.received_at(), record.received_at());
        assert_eq!(
            restored_record.source_observed_at(),
            record.source_observed_at()
        );
        assert!(restored_record.is_stale_at(TimestampMs::new(2_000)));
    }

    /// A Group view selects exact authorized exports and exposes receive-time freshness states.
    #[test]
    fn group_shared_view_uses_exact_export_schema_and_freshness() {
        let source = include_str!("../../../scenarios/execution-relations-v0.1/mission-plan.json");
        let mut document: serde_json::Value =
            serde_json::from_str(source).expect("relation fixture is JSON");
        document["schema_version"] = serde_json::json!(domain::MISSION_PLAN_SCHEMA_V0_4);
        document["contexts"][0]["coupling_mode"] = serde_json::json!("concurrent-cooperation");
        document["contexts"][0]["shared_view"] = serde_json::json!({
            "spatial_reference": {
                "map_id": "campus",
                "revision_id": "r1",
                "frame_id": "map"
            },
            "bindings": [
                {
                    "context_role_id": "safety",
                    "field": "pose",
                    "state_export_id": "safety-pose",
                    "payload_schema": "roboguide.pose/v1"
                },
                {"context_role_id": "safety", "field": "execution"}
            ],
            "include_freshness": true
        });
        let plan = crate::decode_mission_plan(&document.to_string()).expect("v0.4 plan validates");
        let context_id = plan.contexts()[0].context_id().clone();
        let task = &plan.task_graph().tasks()[0];
        let requirement = task.requirement();
        let role_id = requirement.roles()[0].role_id().clone();
        let group_id = domain::ExecutionGroupId::new("group-view").expect("group id is valid");
        let correlation = CorrelationId::new("group-view-test").expect("correlation is valid");
        let mut bridge = IntegrationRuntimeBridge::new(
            ControlPlane::new(),
            InMemorySharedNodeState::new(),
            InMemoryEventLog::new(),
            GrpcNodeRouter::default(),
        );
        bridge
            .consume(
                GrpcNodeEvent::Registered {
                    session_id: "session-view".to_string(),
                    lease_id: "lease-view".to_string(),
                    registration: NodeRegistration {
                        node_id: "cane-a".to_string(),
                        local_systems: vec![LocalSystemDescriptor {
                            id: "safety".to_string(),
                            runtime: Some(WireRuntime {
                                name: "safety-runtime".to_string(),
                                version: "1".to_string(),
                            }),
                            metadata: Default::default(),
                        }],
                        capabilities: vec![WireCapability {
                            kind: "observation".to_string(),
                            available: true,
                            contracts: vec!["safety.observe@v1".to_string()],
                            local_system_id: "safety".to_string(),
                        }],
                        sensors: Vec::new(),
                        resources: Vec::new(),
                        metadata: Default::default(),
                        node_contract_version: "roboguide.node.v0.3".to_string(),
                        state_exports: ["safety-pose", "pose-shadow"]
                            .into_iter()
                            .map(|export_id| integration::grpc::v0_3::StateExportDescriptor {
                                export_id: export_id.to_string(),
                                local_system_id: "safety".to_string(),
                                object_class: integration::grpc::v0_3::StateObjectClass::Node
                                    as i32,
                                object_type: "pose".to_string(),
                                object_id: "cane-a".to_string(),
                                semantic: integration::grpc::v0_3::StateSemantic::Reported as i32,
                                payload_schema: "roboguide.pose/v1".to_string(),
                                valid_for_ms: 100,
                            })
                            .collect(),
                        memory_providers: Vec::new(),
                    },
                },
                TimestampMs::new(1),
                &correlation,
            )
            .expect("node registration is accepted");
        bridge
            .consume(
                GrpcNodeEvent::NodeMessage {
                    node_id: "cane-a".to_string(),
                    session_id: "session-view".to_string(),
                    message: integration::grpc::v0_3::NodeMessage {
                        message: Some(NodePayload::Heartbeat(integration::grpc::v0_3::Heartbeat {
                            session_id: "session-view".to_string(),
                            lease_id: "lease-view".to_string(),
                            sequence: 1,
                            status: Some(integration::grpc::v0_3::NodeStatus {
                                health: "online".to_string(),
                                detail: String::new(),
                            }),
                        })),
                    },
                },
                TimestampMs::new(2),
                &correlation,
            )
            .expect("online heartbeat is accepted");
        bridge
            .control
            .create_mission_group(
                group_id.clone(),
                &plan,
                TimestampMs::new(3),
                &correlation,
                &mut bridge.events,
            )
            .expect("Mission Group is created");
        bridge
            .control
            .ready_task_execution(
                &group_id,
                requirement.task_ref(),
                TimestampMs::new(3),
                &correlation,
                &mut bridge.events,
            )
            .expect("Task becomes ready");
        let candidates = bridge
            .control
            .match_capabilities(
                &bridge.state,
                requirement,
                TimestampMs::new(3),
                &correlation,
                &mut bridge.events,
            )
            .expect("node matches exact observation contract");
        let proposal = bridge
            .control
            .propose(
                &bridge.state,
                requirement,
                &candidates,
                vec![domain::RoleAssignment::new(
                    role_id,
                    NodeId::new("cane-a").expect("node id is valid"),
                    Vec::new(),
                )],
                TimestampMs::new(3),
                &correlation,
                &mut bridge.events,
            )
            .expect("zero-resource proposal is valid");
        let committed = bridge
            .control
            .commit(
                &proposal,
                TimestampMs::new(3),
                &correlation,
                &mut bridge.events,
            )
            .expect("proposal commits");
        bridge
            .control
            .bind_task_execution_with_requirement(
                &group_id,
                &committed,
                requirement,
                TimestampMs::new(3),
                &correlation,
                &mut bridge.events,
            )
            .expect("Task binds to the State-producing node");

        let missing_coordination = bridge.execute_task_bound(
            "execution-without-coordination".to_string(),
            &group_id,
            requirement.task_ref(),
            requirement.roles()[0].role_id(),
            task.execution_intent(requirement.roles()[0].role_id())
                .expect("plan contains role intent")
                .clone(),
            TimestampMs::new(4),
            correlation.clone(),
        );
        assert!(matches!(
            missing_coordination,
            Err(IntegrationRuntimeError::Protocol(reason))
                if reason.contains("coordination mechanisms are not ready")
        ));

        let unknown = bridge
            .group_shared_view(&plan, &group_id, &context_id, TimestampMs::new(4))
            .expect("Group view is readable before evidence arrives");
        assert_eq!(unknown.entries().len(), 2);
        assert_eq!(
            unknown.entries()[0].freshness(),
            Some(GroupViewFreshness::Unknown)
        );
        assert!(unknown.entries()[0].record().is_none());
        assert_eq!(
            unknown.entries()[0].spatial_verification(),
            Some(GroupSpatialVerification::Unknown)
        );
        assert_eq!(
            unknown.entries()[1].field(),
            domain::GroupViewField::Execution
        );
        assert_eq!(unknown.entries()[1].execution_status(), None);
        assert_eq!(unknown.entries()[1].state_export_id(), None);

        bridge
            .consume(
                GrpcNodeEvent::NodeMessage {
                    node_id: "cane-a".to_string(),
                    session_id: "session-view".to_string(),
                    message: integration::grpc::v0_3::NodeMessage {
                        message: Some(NodePayload::StateObservationBatch(
                            integration::grpc::v0_3::StateObservationBatch {
                                session_id: "session-view".to_string(),
                                sequence: 2,
                                observations: vec![
                                    integration::grpc::v0_3::StateObservation {
                                        export_id: "pose-shadow".to_string(),
                                        json_value: br#"{"x":999}"#.to_vec(),
                                        has_source_observed_at: false,
                                        source_observed_at_ms: 0,
                                        has_confidence: false,
                                        confidence_millionths: 0,
                                    },
                                    integration::grpc::v0_3::StateObservation {
                                        export_id: "safety-pose".to_string(),
                                        json_value: br#"{"x":1}"#.to_vec(),
                                        has_source_observed_at: false,
                                        source_observed_at_ms: 0,
                                        has_confidence: false,
                                        confidence_millionths: 0,
                                    },
                                ],
                            },
                        )),
                    },
                },
                TimestampMs::new(10),
                &correlation,
            )
            .expect("State evidence is accepted");

        let fresh = bridge
            .group_shared_view(&plan, &group_id, &context_id, TimestampMs::new(50))
            .expect("fresh view is readable");
        assert_eq!(fresh.entries().len(), 2);
        assert_eq!(fresh.entries()[0].state_export_id(), Some("safety-pose"));
        assert_eq!(
            fresh.entries()[0].freshness(),
            Some(GroupViewFreshness::Fresh)
        );
        assert_eq!(
            fresh.entries()[0]
                .record()
                .expect("selected evidence exists")
                .value(),
            &serde_json::json!({"x": 1})
        );
        let command = ExecutionCommand::new(
            requirement.mission_id().clone(),
            requirement.task_id().clone(),
            group_id.clone(),
            requirement.roles()[0].role_id().clone(),
            NodeId::new("cane-a").expect("node id is valid"),
            task.execution_intent(requirement.roles()[0].role_id())
                .expect("plan contains role intent")
                .clone(),
            correlation.clone(),
        );
        bridge
            .runtime
            .record_dispatched("execution-view".to_string(), command, Vec::new())
            .expect("Runtime dispatch is recorded");
        let evidence: domain::LocalizationVerificationEvidence =
            serde_json::from_value(serde_json::json!({
                "schema": domain::LOCALIZATION_EVIDENCE_SCHEMA_V0_1,
                "map_id": "campus",
                "revision_id": "r1",
                "content_digest": format!("sha256:{}", "a".repeat(64)),
                "byte_size": 1,
                "mission_id": requirement.mission_id().as_str(),
                "task_id": requirement.task_id().as_str(),
                "group_id": group_id.as_str(),
                "role_id": requirement.roles()[0].role_id().as_str(),
                "node_id": "cane-a",
                "execution_id": "execution-view",
                "local_attempt_id": "local-view",
                "active_local_map_id": "campus-r1",
                "mode": "localization",
                "pose_quality": {
                    "metric": "translation_stddev",
                    "value": "0.05",
                    "threshold": "0.10",
                    "unit": "m",
                    "comparison": "at_most"
                },
                "frames": {"map": "map", "odom": "odom", "base": "base_link"},
                "anchor_id": "campus-origin",
                "source_observed_at_ms": 49
            }))
            .expect("strong localization evidence validates");
        bridge
            .observe_localization_evidence(&evidence, TimestampMs::new(50), &correlation)
            .expect("current-attempt localization evidence is accepted");
        let execution_view = bridge
            .group_shared_view(&plan, &group_id, &context_id, TimestampMs::new(50))
            .expect("Runtime execution view is readable");
        assert_eq!(
            execution_view.entries()[1].execution_status(),
            Some(RemoteExecutionStatus::Accepted)
        );
        assert_eq!(
            execution_view.entries()[0].spatial_verification(),
            Some(GroupSpatialVerification::Verified)
        );
        assert_eq!(
            execution_view.entries()[0]
                .spatial_evidence()
                .map(SharedSpatialEvidence::execution_id),
            Some("execution-view")
        );
        let stale = bridge
            .group_shared_view(&plan, &group_id, &context_id, TimestampMs::new(111))
            .expect("stale evidence remains inspectable");
        assert_eq!(
            stale.entries()[0].freshness(),
            Some(GroupViewFreshness::Stale)
        );
    }

    /// Peer readiness is admitted only from each committed role's registered Local EAIOS owner.
    #[test]
    fn peer_readiness_is_owner_checked_expires_and_restores_fenced() {
        let source = include_str!("../../../scenarios/execution-relations-v0.1/mission-plan.json");
        let mut document: serde_json::Value =
            serde_json::from_str(source).expect("relation fixture is JSON");
        document["schema_version"] = serde_json::json!(domain::MISSION_PLAN_SCHEMA_V0_4);
        document["contexts"][0]["coupling_mode"] = serde_json::json!("tightly-coupled-cooperation");
        document["contexts"][0]["shared_view"] = serde_json::json!({
            "bindings": [
                {"context_role_id": "guide", "field": "execution"},
                {"context_role_id": "safety", "field": "execution"}
            ],
            "include_freshness": false
        });
        document["contexts"][0]["peer_channel"] = serde_json::json!({
            "profile_id": "guidance-peer",
            "message_schema": "guidance/v1"
        });
        let plan = crate::decode_mission_plan(&document.to_string()).expect("v0.4 plan validates");
        let mission_id = plan.goal().mission_id().clone();
        let group_id = domain::ExecutionGroupId::new("group-peer").expect("group id is valid");
        let context_id = plan.contexts()[0].context_id().clone();
        let correlation = CorrelationId::new("peer-readiness-test").expect("correlation is valid");
        let mut bridge = IntegrationRuntimeBridge::new(
            ControlPlane::new(),
            InMemorySharedNodeState::new(),
            InMemoryEventLog::new(),
            GrpcNodeRouter::default(),
        );
        let registration =
            |node: &str,
             local_system: &str,
             contract: &str,
             kind: &str,
             resources: Vec<integration::grpc::v0_3::Resource>| NodeRegistration {
                node_id: node.to_string(),
                local_systems: vec![
                    LocalSystemDescriptor {
                        id: local_system.to_string(),
                        runtime: Some(WireRuntime {
                            name: format!("{local_system}-runtime"),
                            version: "1".to_string(),
                        }),
                        metadata: Default::default(),
                    },
                    LocalSystemDescriptor {
                        id: "decoy".to_string(),
                        runtime: Some(WireRuntime {
                            name: "decoy-runtime".to_string(),
                            version: "1".to_string(),
                        }),
                        metadata: Default::default(),
                    },
                ],
                capabilities: vec![WireCapability {
                    kind: kind.to_string(),
                    available: true,
                    contracts: vec![contract.to_string()],
                    local_system_id: local_system.to_string(),
                }],
                sensors: Vec::new(),
                resources,
                metadata: Default::default(),
                node_contract_version: "roboguide.node.v0.3".to_string(),
                state_exports: Vec::new(),
                memory_providers: Vec::new(),
            };
        for (node, lease, local_system, contract, kind, resources) in [
            (
                "cane-a",
                "lease-cane",
                "safety",
                "safety.observe@v1",
                "observation",
                Vec::new(),
            ),
            (
                "dog-a",
                "lease-dog",
                "motion",
                "mobility.navigate@v1",
                "mobility",
                vec![integration::grpc::v0_3::Resource {
                    id: "guide-space".to_string(),
                    kind: "space".to_string(),
                    capacity: 1,
                    metadata: Default::default(),
                    local_system_id: "motion".to_string(),
                }],
            ),
        ] {
            bridge
                .consume(
                    GrpcNodeEvent::Registered {
                        session_id: format!("session-{node}"),
                        lease_id: lease.to_string(),
                        registration: registration(node, local_system, contract, kind, resources),
                    },
                    TimestampMs::new(1),
                    &correlation,
                )
                .expect("node registration is accepted");
            bridge
                .consume(
                    GrpcNodeEvent::NodeMessage {
                        node_id: node.to_string(),
                        session_id: format!("session-{node}"),
                        message: integration::grpc::v0_3::NodeMessage {
                            message: Some(NodePayload::Heartbeat(
                                integration::grpc::v0_3::Heartbeat {
                                    session_id: format!("session-{node}"),
                                    lease_id: lease.to_string(),
                                    sequence: 1,
                                    status: Some(integration::grpc::v0_3::NodeStatus {
                                        health: "online".to_string(),
                                        detail: String::new(),
                                    }),
                                },
                            )),
                        },
                    },
                    TimestampMs::new(2),
                    &correlation,
                )
                .expect("online heartbeat is accepted");
        }
        let mut orchestrator = crate::MissionOrchestrator::new();
        {
            let (control, events) = (&mut bridge.control, &mut bridge.events);
            orchestrator
                .submit(
                    plan.clone(),
                    group_id.clone(),
                    control,
                    TimestampMs::new(3),
                    &correlation,
                    events,
                )
                .expect("Mission is accepted");
        }
        bridge
            .register_execution_relations(&plan, &group_id, TimestampMs::new(3), &correlation)
            .expect("coordination declarations register");
        for task_ref in orchestrator.ready_tasks(&mission_id, bridge.control()) {
            let state = bridge.state().clone();
            let (control, events) = (&mut bridge.control, &mut bridge.events);
            orchestrator
                .prepare_task(
                    &mission_id,
                    &task_ref,
                    &state,
                    control,
                    TimestampMs::new(4),
                    &correlation,
                    events,
                )
                .expect("ready Task binds");
        }

        let readiness = |node: &str, local_system: &str, role: &str, sequence: u64| {
            GrpcNodeEvent::NodeMessage {
                node_id: node.to_string(),
                session_id: format!("session-{node}"),
                message: integration::grpc::v0_3::NodeMessage {
                    message: Some(NodePayload::PeerChannelReadiness(
                        integration::grpc::v0_3::PeerChannelReadiness {
                            session_id: format!("session-{node}"),
                            sequence,
                            group_id: group_id.as_str().to_string(),
                            context_id: context_id.as_str().to_string(),
                            context_role_id: role.to_string(),
                            channel_instance_id: "channel-guidance-1".to_string(),
                            profile_id: "guidance-peer".to_string(),
                            message_schema: "guidance/v1".to_string(),
                            ready: true,
                            valid_for_ms: 20,
                            local_system_id: local_system.to_string(),
                        },
                    )),
                },
            }
        };
        let mut wrong_session = readiness("dog-a", "motion", "guide", 2);
        let GrpcNodeEvent::NodeMessage { message, .. } = &mut wrong_session else {
            unreachable!("readiness fixture is a Node message");
        };
        let Some(NodePayload::PeerChannelReadiness(payload)) = &mut message.message else {
            unreachable!("readiness fixture contains peer evidence");
        };
        payload.session_id = "session-other".to_string();
        assert!(matches!(
            bridge.consume(wrong_session, TimestampMs::new(9), &correlation),
            Err(IntegrationRuntimeError::Protocol(reason))
                if reason.contains("session does not match")
        ));
        let mut wrong_context = readiness("dog-a", "motion", "guide", 2);
        let GrpcNodeEvent::NodeMessage { message, .. } = &mut wrong_context else {
            unreachable!("readiness fixture is a Node message");
        };
        let Some(NodePayload::PeerChannelReadiness(payload)) = &mut message.message else {
            unreachable!("readiness fixture contains peer evidence");
        };
        payload.context_id = "different-context".to_string();
        assert!(matches!(
            bridge.consume(wrong_context, TimestampMs::new(9), &correlation),
            Err(IntegrationRuntimeError::Protocol(reason))
                if reason.contains("does not own the ContextRole binding")
        ));
        assert!(matches!(
            bridge.consume(
                readiness("dog-a", "decoy", "guide", 2),
                TimestampMs::new(10),
                &correlation,
            ),
            Err(IntegrationRuntimeError::Protocol(reason))
                if reason.contains("does not own the ContextRole binding")
        ));
        bridge
            .consume(
                readiness("cane-a", "safety", "safety", 2),
                TimestampMs::new(10),
                &correlation,
            )
            .expect("safety endpoint acknowledgement is admitted");
        bridge
            .consume(
                readiness("dog-a", "motion", "guide", 3),
                TimestampMs::new(11),
                &correlation,
            )
            .expect("guide endpoint acknowledgement is admitted");
        assert_eq!(
            bridge.peer_channel_snapshots(&group_id)[0].lifecycle(),
            runtime::PeerChannelLifecycle::Ready
        );
        assert_eq!(
            bridge
                .events
                .records()
                .iter()
                .filter(|event| matches!(
                    event.payload(),
                    EventPayload::PeerChannelReadinessObserved { .. }
                ))
                .count(),
            2
        );

        bridge
            .runtime
            .refresh_peer_channel_deadlines(TimestampMs::new(31));
        assert_eq!(
            bridge.peer_channel_snapshots(&group_id)[0].lifecycle(),
            runtime::PeerChannelLifecycle::Fenced
        );
        bridge
            .consume(
                readiness("cane-a", "safety", "safety", 3),
                TimestampMs::new(32),
                &correlation,
            )
            .expect("safety endpoint renews");
        assert_eq!(
            bridge.peer_channel_snapshots(&group_id)[0].lifecycle(),
            runtime::PeerChannelLifecycle::Fenced
        );
        bridge
            .consume(
                readiness("dog-a", "motion", "guide", 4),
                TimestampMs::new(33),
                &correlation,
            )
            .expect("guide endpoint renews");
        assert_eq!(
            bridge.peer_channel_snapshots(&group_id)[0].lifecycle(),
            runtime::PeerChannelLifecycle::Ready
        );

        bridge
            .consume(
                GrpcNodeEvent::NodeMessage {
                    node_id: "dog-a".to_string(),
                    session_id: "session-dog-a".to_string(),
                    message: integration::grpc::v0_3::NodeMessage {
                        message: Some(NodePayload::RegistrationUpdate(
                            integration::grpc::v0_3::RegistrationUpdate {
                                session_id: "session-dog-a".to_string(),
                                sequence: 5,
                                registration: Some(registration(
                                    "dog-a",
                                    "motion",
                                    "mobility.navigate@v1",
                                    "mobility",
                                    vec![integration::grpc::v0_3::Resource {
                                        id: "guide-space".to_string(),
                                        kind: "space".to_string(),
                                        capacity: 1,
                                        metadata: Default::default(),
                                        local_system_id: "motion".to_string(),
                                    }],
                                )),
                            },
                        )),
                    },
                },
                TimestampMs::new(34),
                &correlation,
            )
            .expect("complete registration update is accepted");
        assert_eq!(
            bridge.peer_channel_snapshots(&group_id)[0].lifecycle(),
            runtime::PeerChannelLifecycle::Fenced
        );
        assert_eq!(
            bridge.peer_channel_snapshots(&group_id)[0]
                .readiness()
                .len(),
            1
        );
        bridge
            .consume(
                readiness("dog-a", "motion", "guide", 6),
                TimestampMs::new(35),
                &correlation,
            )
            .expect("affected endpoint reproves readiness after registration change");
        assert_eq!(
            bridge.peer_channel_snapshots(&group_id)[0].lifecycle(),
            runtime::PeerChannelLifecycle::Ready
        );

        let restored = IntegrationRuntimeBridge::restore_from_checkpoint(
            &bridge.checkpoint_json().expect("checkpoint serializes"),
            InMemoryEventLog::new(),
            GrpcNodeRouter::default(),
            TimestampMs::new(40),
        )
        .expect("checkpoint restores conservatively");
        let channel = &restored.peer_channel_snapshots(&group_id)[0];
        assert_eq!(channel.lifecycle(), runtime::PeerChannelLifecycle::Fenced);
        assert!(channel.readiness().is_empty());
    }

    /// A new Node session may restart its management sequence in the same receive millisecond.
    #[test]
    fn state_observation_reconnect_epoch_accepts_reset_sequence() {
        let router = GrpcNodeRouter::default();
        let mut bridge = IntegrationRuntimeBridge::new(
            ControlPlane::new(),
            InMemorySharedNodeState::new(),
            InMemoryEventLog::new(),
            router,
        );
        let correlation = CorrelationId::new("state-reconnect").expect("correlation is valid");
        bridge
            .consume(
                GrpcNodeEvent::Registered {
                    session_id: "session-old".to_string(),
                    lease_id: "lease-old".to_string(),
                    registration: NodeRegistration {
                        node_id: "cane-a".to_string(),
                        local_systems: vec![LocalSystemDescriptor {
                            id: "safety".to_string(),
                            runtime: Some(WireRuntime {
                                name: "safety-runtime".to_string(),
                                version: "1".to_string(),
                            }),
                            metadata: Default::default(),
                        }],
                        capabilities: Vec::new(),
                        sensors: Vec::new(),
                        resources: Vec::new(),
                        metadata: Default::default(),
                        node_contract_version: "roboguide.node.v0.3".to_string(),
                        state_exports: vec![integration::grpc::v0_3::StateExportDescriptor {
                            export_id: "hazard-state".to_string(),
                            local_system_id: "safety".to_string(),
                            object_class: integration::grpc::v0_3::StateObjectClass::World as i32,
                            object_type: "hazard".to_string(),
                            object_id: "crossing-a".to_string(),
                            semantic: integration::grpc::v0_3::StateSemantic::Observed as i32,
                            payload_schema: "example.hazard/v1".to_string(),
                            valid_for_ms: 1_000,
                        }],
                        memory_providers: Vec::new(),
                    },
                },
                TimestampMs::new(1),
                &correlation,
            )
            .expect("registration is accepted");
        for (session_id, sequence, present) in
            [("session-old", 99, false), ("session-new", 1, true)]
        {
            bridge
                .consume(
                    GrpcNodeEvent::NodeMessage {
                        node_id: "cane-a".to_string(),
                        session_id: session_id.to_string(),
                        message: integration::grpc::v0_3::NodeMessage {
                            message: Some(NodePayload::StateObservationBatch(
                                integration::grpc::v0_3::StateObservationBatch {
                                    session_id: session_id.to_string(),
                                    sequence,
                                    observations: vec![integration::grpc::v0_3::StateObservation {
                                        export_id: "hazard-state".to_string(),
                                        json_value: serde_json::to_vec(
                                            &serde_json::json!({"present": present}),
                                        )
                                        .expect("State value serializes"),
                                        has_source_observed_at: false,
                                        source_observed_at_ms: 0,
                                        has_confidence: false,
                                        confidence_millionths: 0,
                                    }],
                                },
                            )),
                        },
                    },
                    TimestampMs::new(2),
                    &correlation,
                )
                .expect("current session State observation is accepted");
        }

        let record = bridge
            .state_records()
            .records()
            .into_iter()
            .next()
            .expect("one latest State record remains");
        assert_eq!(record.source_epoch(), Some("session-new"));
        assert_eq!(record.sequence(), 1);
        assert_eq!(record.value(), &serde_json::json!({"present": true}));
    }

    /// Parses hierarchical canonical contracts with the same last-dot rule as Node Config.
    #[test]
    fn canonical_contract_parser_round_trips_hierarchical_namespace() {
        let contract = parse_contract("spatial.map.build@v0").expect("contract parses");
        assert_eq!(contract.namespace(), "spatial.map");
        assert_eq!(contract.name(), "build");
        assert_eq!(contract.to_string(), "spatial.map.build@v0");
    }

    /// Wire conversion cannot reintroduce a live Group identity into static provider metadata.
    #[test]
    fn memory_provider_conversion_rejects_execution_group_scope() {
        let wire = integration::grpc::v0_3::MemoryProviderDescriptor {
            provider_id: "experience".to_string(),
            local_system_id: "memory".to_string(),
            kind: integration::grpc::v0_3::MemoryKind::Experience as i32,
            scope: integration::grpc::v0_3::MemoryScopeKind::ExecutionGroup as i32,
            execution_group_id: "group-a".to_string(),
            visibility: integration::grpc::v0_3::MemoryVisibility::Discoverable as i32,
            payload_schema: "example.experience/v1".to_string(),
            media_type: "application/json".to_string(),
        };

        assert!(matches!(
            memory_provider_from_wire(&wire),
            Err(IntegrationRuntimeError::Protocol(reason))
                if reason.contains("cannot contain an execution Group")
        ));
    }

    /// Conversion preserves exact readiness when sibling contracts share a coarse kind.
    #[test]
    fn registration_conversion_preserves_per_contract_readiness() {
        let registration = registration_from_wire(NodeRegistration {
            node_id: "dog-a".to_string(),
            local_systems: vec![LocalSystemDescriptor {
                id: "mapping".to_string(),
                runtime: Some(WireRuntime {
                    name: "mapping-runtime".to_string(),
                    version: "1".to_string(),
                }),
                metadata: Default::default(),
            }],
            capabilities: vec![
                WireCapability {
                    kind: "compute".to_string(),
                    available: true,
                    contracts: vec!["spatial.map.build@v0".to_string()],
                    local_system_id: "mapping".to_string(),
                },
                WireCapability {
                    kind: "compute".to_string(),
                    available: false,
                    contracts: vec!["spatial.map.localize@v0".to_string()],
                    local_system_id: "mapping".to_string(),
                },
                WireCapability {
                    kind: "observation".to_string(),
                    available: true,
                    contracts: vec!["spatial.localization.observe@v0".to_string()],
                    local_system_id: "mapping".to_string(),
                },
            ],
            sensors: Vec::new(),
            resources: Vec::new(),
            metadata: Default::default(),
            node_contract_version: "roboguide.node.v0.3".to_string(),
            state_exports: Vec::new(),
            memory_providers: Vec::new(),
        })
        .expect("registration converts");
        let build = parse_contract("spatial.map.build@v0").expect("build contract parses");
        let localize = parse_contract("spatial.map.localize@v0").expect("localize contract parses");
        let observe =
            parse_contract("spatial.localization.observe@v0").expect("observation contract parses");

        assert!(registration.contract_is_available(&build));
        assert!(!registration.contract_is_available(&localize));
        assert!(registration.contract_is_available_for_kind(&observe, CapabilityKind::Observation));
        assert!(!registration.contract_is_available_for_kind(&observe, CapabilityKind::Compute));
        assert!(
            registration
                .capabilities()
                .iter()
                .any(|capability| capability.kind() == CapabilityKind::Compute
                    && capability.is_available())
        );
    }

    /// A RegistrationUpdate changes only later matching decisions for the exact contract.
    #[test]
    fn readiness_update_changes_later_control_matching() {
        let wire_registration = |available: bool| NodeRegistration {
            node_id: "dog-a".to_string(),
            local_systems: vec![LocalSystemDescriptor {
                id: "mapping".to_string(),
                runtime: Some(WireRuntime {
                    name: "mapping-runtime".to_string(),
                    version: "1".to_string(),
                }),
                metadata: Default::default(),
            }],
            capabilities: vec![WireCapability {
                kind: "observation".to_string(),
                available,
                contracts: vec!["spatial.localization.verify@v0".to_string()],
                local_system_id: "mapping".to_string(),
            }],
            sensors: Vec::new(),
            resources: Vec::new(),
            metadata: Default::default(),
            node_contract_version: "roboguide.node.v0.3".to_string(),
            state_exports: Vec::new(),
            memory_providers: Vec::new(),
        };
        let mut bridge = IntegrationRuntimeBridge::new(
            ControlPlane::new(),
            InMemorySharedNodeState::new(),
            InMemoryEventLog::new(),
            GrpcNodeRouter::default(),
        );
        let correlation = CorrelationId::new("readiness-update").expect("correlation is valid");
        bridge
            .consume(
                GrpcNodeEvent::Registered {
                    session_id: "session-a".to_string(),
                    lease_id: "lease-a".to_string(),
                    registration: wire_registration(false),
                },
                TimestampMs::new(0),
                &correlation,
            )
            .expect("registration is consumed");
        bridge
            .consume(
                GrpcNodeEvent::NodeMessage {
                    node_id: "dog-a".to_string(),
                    session_id: "session-a".to_string(),
                    message: integration::grpc::v0_3::NodeMessage {
                        message: Some(NodePayload::Heartbeat(integration::grpc::v0_3::Heartbeat {
                            session_id: "session-a".to_string(),
                            lease_id: "lease-a".to_string(),
                            sequence: 1,
                            status: Some(integration::grpc::v0_3::NodeStatus {
                                health: "online".to_string(),
                                detail: String::new(),
                            }),
                        })),
                    },
                },
                TimestampMs::new(1),
                &correlation,
            )
            .expect("heartbeat is consumed");
        let requirement = domain::TaskRequirement::new(
            domain::MissionId::new("mission-readiness").expect("mission id is valid"),
            domain::TaskId::new("verify-map").expect("task id is valid"),
            vec![domain::RoleRequirement::new_with_actor_and_contract(
                domain::RoleId::new("localizer").expect("role id is valid"),
                domain::ActorId::new("robot").expect("actor id is valid"),
                CapabilityKind::Observation,
                parse_contract("spatial.localization.verify@v0").expect("contract is valid"),
                None,
            )],
        )
        .expect("requirement is valid");
        let mut decision_events = InMemoryEventLog::new();
        assert!(matches!(
            bridge.control().match_capabilities(
                bridge.state(),
                &requirement,
                TimestampMs::new(1),
                &correlation,
                &mut decision_events,
            ),
            Err(control::ControlError::NoCandidate(_))
        ));

        bridge
            .consume(
                GrpcNodeEvent::NodeMessage {
                    node_id: "dog-a".to_string(),
                    session_id: "session-a".to_string(),
                    message: integration::grpc::v0_3::NodeMessage {
                        message: Some(NodePayload::RegistrationUpdate(
                            integration::grpc::v0_3::RegistrationUpdate {
                                session_id: "session-a".to_string(),
                                sequence: 2,
                                registration: Some(wire_registration(true)),
                            },
                        )),
                    },
                },
                TimestampMs::new(2),
                &correlation,
            )
            .expect("readiness update is consumed");
        let candidates = bridge
            .control()
            .match_capabilities(
                bridge.state(),
                &requirement,
                TimestampMs::new(2),
                &correlation,
                &mut decision_events,
            )
            .expect("ready contract matches");
        assert_eq!(candidates.roles()[0].node_ids().len(), 1);
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
                    message: integration::grpc::v0_3::NodeMessage {
                        message: Some(NodePayload::ExecutionSnapshot(
                            integration::grpc::v0_3::ExecutionSnapshot {
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
                    message: integration::grpc::v0_3::NodeMessage {
                        message: Some(NodePayload::ExecutionEvent(
                            integration::grpc::v0_3::ExecutionEvent {
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
                    message: integration::grpc::v0_3::NodeMessage {
                        message: Some(NodePayload::ExecutionSnapshot(
                            integration::grpc::v0_3::ExecutionSnapshot {
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
                        node_contract_version: "roboguide.node.v0.3".to_string(),
                        state_exports: Vec::new(),
                        memory_providers: Vec::new(),
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

    /// Group aggregation and dispatch validation follow the current TaskExecution.
    #[test]
    fn mission_dispatch_and_outcomes_use_current_task_execution() {
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
        let intent = domain::ExecutionIntent::new(contract, BTreeMap::new()).expect("intent valid");
        let mission_plan = single_task_plan(requirement.clone(), intent.clone());
        let group_id = domain::ExecutionGroupId::new("group-a").expect("group valid");
        control
            .create_mission_group(
                group_id.clone(),
                &mission_plan,
                now,
                &correlation,
                &mut events,
            )
            .expect("Mission Group registers");
        control
            .ready_task_execution(
                &group_id,
                requirement.task_ref(),
                now,
                &correlation,
                &mut events,
            )
            .expect("Task becomes ready");
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
        control
            .bind_task_execution_with_requirement(
                &group_id,
                &committed,
                &requirement,
                now,
                &correlation,
                &mut events,
            )
            .expect("Task binds");
        control
            .activate_task_execution(
                &group_id,
                requirement.task_ref(),
                now,
                &correlation,
                &mut events,
            )
            .expect("Task activates");
        let command = ExecutionCommand::new(
            domain::MissionId::new("mission-a").expect("mission valid"),
            domain::TaskId::new("task-a").expect("task valid"),
            group_id.clone(),
            role_id.clone(),
            NodeId::new("node-a").expect("node valid"),
            intent.clone(),
            correlation.clone(),
        );
        let mut bridge =
            IntegrationRuntimeBridge::new(control, state, events, GrpcNodeRouter::default());
        assert!(matches!(
            bridge.execute_bound(
                "execution-ambiguous".to_string(),
                &group_id,
                &role_id,
                intent.clone(),
                correlation.clone(),
            ),
            Err(IntegrationRuntimeError::Protocol(reason)) if reason.contains("explicit TaskRef")
        ));
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
        let outcomes = bridge.terminal_task_outcomes();
        assert_eq!(outcomes.len(), 1);
        assert_eq!(outcomes[0].group_id(), &group_id);
        assert_eq!(outcomes[0].task_ref(), requirement.task_ref());
        assert_eq!(outcomes[0].result(), ObservedTaskResult::Succeeded);
        assert_eq!(
            bridge
                .control()
                .group(&group_id)
                .expect("group exists")
                .lifecycle(),
            control::GroupLifecycle::Active
        );
        {
            let (control, events) = (&mut bridge.control, &mut bridge.events);
            control
                .complete_task_execution(
                    &group_id,
                    requirement.task_ref(),
                    now,
                    &correlation,
                    events,
                )
                .expect("Task completion records");
        }
        assert!(matches!(
            bridge.execute_task_bound(
                "execution-after-completion".to_string(),
                &group_id,
                requirement.task_ref(),
                &role_id,
                intent,
                now,
                correlation,
            ),
            Err(IntegrationRuntimeError::Protocol(reason)) if reason.contains("not dispatchable")
        ));
    }

    /// Partial recovery cannot dispatch or complete a multi-role Task from its surviving role.
    #[test]
    fn incomplete_multi_role_task_does_not_dispatch_or_report_terminal_outcome() {
        let now = TimestampMs::new(0);
        let correlation = CorrelationId::new("partial-multi-role-test").expect("correlation valid");
        let contract =
            CapabilityContractRef::new("mobility", "move", "v1").expect("contract valid");
        let node_id = NodeId::new("node-a").expect("node valid");
        let registration = domain::NodeRegistration::new_with_contracts(
            node_id.clone(),
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
        let retained_role = domain::RoleId::new("carrier").expect("role valid");
        let released_role = domain::RoleId::new("observer").expect("role valid");
        let requirement = domain::TaskRequirement::new(
            domain::MissionId::new("mission-multi").expect("mission valid"),
            domain::TaskId::new("task-multi").expect("task valid"),
            vec![
                domain::RoleRequirement::new_with_actor_and_contract(
                    retained_role.clone(),
                    domain::ActorId::new("carrier").expect("actor valid"),
                    CapabilityKind::Mobility,
                    contract.clone(),
                    None,
                ),
                domain::RoleRequirement::new_with_actor_and_contract(
                    released_role.clone(),
                    domain::ActorId::new("observer").expect("actor valid"),
                    CapabilityKind::Mobility,
                    contract.clone(),
                    None,
                ),
            ],
        )
        .expect("requirement valid");
        let intent = domain::ExecutionIntent::new(contract, BTreeMap::new()).expect("intent valid");
        let mission_plan = single_task_plan(requirement.clone(), intent.clone());
        let group_id = domain::ExecutionGroupId::new("group-multi").expect("group valid");
        control
            .create_mission_group(
                group_id.clone(),
                &mission_plan,
                now,
                &correlation,
                &mut events,
            )
            .expect("Mission Group registers");
        control
            .ready_task_execution(
                &group_id,
                requirement.task_ref(),
                now,
                &correlation,
                &mut events,
            )
            .expect("Task becomes ready");
        let candidates = control
            .match_capabilities(&state, &requirement, now, &correlation, &mut events)
            .expect("matching succeeds");
        let proposal = control
            .propose(
                &state,
                &requirement,
                &candidates,
                vec![
                    domain::RoleAssignment::new(retained_role.clone(), node_id.clone(), Vec::new()),
                    domain::RoleAssignment::new(released_role.clone(), node_id.clone(), Vec::new()),
                ],
                now,
                &correlation,
                &mut events,
            )
            .expect("proposal succeeds");
        let committed = control
            .commit(&proposal, now, &correlation, &mut events)
            .expect("commit succeeds");
        control
            .bind_task_execution_with_requirement(
                &group_id,
                &committed,
                &requirement,
                now,
                &correlation,
                &mut events,
            )
            .expect("Task binds");
        control
            .activate_task_execution(
                &group_id,
                requirement.task_ref(),
                now,
                &correlation,
                &mut events,
            )
            .expect("Task activates");
        control
            .block_group(
                &group_id,
                "observer unavailable",
                now,
                &correlation,
                &mut events,
            )
            .expect("Group blocks for recovery");
        control
            .release_task_role_binding(
                &group_id,
                requirement.task_ref(),
                &released_role,
                now,
                &correlation,
                &mut events,
            )
            .expect("failed role releases");
        let execution = control
            .group(&group_id)
            .and_then(|group| group.task_execution(requirement.task_ref()))
            .expect("Task remains registered");
        assert_eq!(
            execution.lifecycle(),
            domain::TaskExecutionLifecycle::Active
        );
        assert!(!task_assignments_are_complete(execution));

        let command = ExecutionCommand::new(
            requirement.mission_id().clone(),
            requirement.task_id().clone(),
            group_id.clone(),
            retained_role.clone(),
            node_id.clone(),
            intent.clone(),
            correlation.clone(),
        );
        let mut bridge =
            IntegrationRuntimeBridge::new(control, state, events, GrpcNodeRouter::default());
        bridge
            .runtime
            .record_dispatched("execution-retained".to_string(), command, Vec::new())
            .expect("retained role execution records");
        bridge
            .runtime
            .observe_execution(
                "execution-retained",
                node_id,
                1,
                ExecutionStatus::Completed,
                "",
            )
            .expect("retained role completion records");

        assert!(bridge.terminal_task_outcomes().is_empty());
        assert!(matches!(
            bridge.execute_task_bound(
                "execution-during-recovery".to_string(),
                &group_id,
                requirement.task_ref(),
                &retained_role,
                intent,
                now,
                correlation,
            ),
            Err(IntegrationRuntimeError::Protocol(reason)) if reason.contains("not bound")
        ));
        assert_eq!(bridge.execution_status("execution-during-recovery"), None);
    }

    /// Assignment completeness requires exact role coverage without duplicate role entries.
    #[test]
    fn task_assignment_completeness_rejects_missing_and_duplicate_roles() {
        let first_role = domain::RoleId::new("first").expect("role valid");
        let second_role = domain::RoleId::new("second").expect("role valid");
        let node_id = NodeId::new("node-a").expect("node valid");
        let execution = domain::TaskExecution::new(
            domain::TaskRef::new(
                domain::MissionId::new("mission-coverage").expect("mission valid"),
                domain::TaskId::new("task-coverage").expect("task valid"),
            ),
            domain::CoordinationContextId::new("context-coverage").expect("context valid"),
            BTreeMap::new(),
            BTreeMap::from([
                (first_role.clone(), domain::ResourceBindingScope::Task),
                (second_role.clone(), domain::ResourceBindingScope::Task),
            ]),
        );
        let first_assignment =
            domain::RoleAssignment::new(first_role.clone(), node_id.clone(), Vec::new());
        let second_assignment =
            domain::RoleAssignment::new(second_role, node_id.clone(), Vec::new());

        assert!(task_assignments_are_complete(&execution.with_assignments(
            vec![first_assignment.clone(), second_assignment]
        )));
        assert!(!task_assignments_are_complete(
            &execution.with_assignments(vec![first_assignment.clone()])
        ));
        assert!(!task_assignments_are_complete(&execution.with_assignments(
            vec![
                first_assignment.clone(),
                domain::RoleAssignment::new(first_role, node_id, Vec::new()),
            ]
        )));
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
            message: integration::grpc::v0_3::NodeMessage {
                message: Some(NodePayload::ExecutionSnapshot(
                    integration::grpc::v0_3::ExecutionSnapshot {
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
