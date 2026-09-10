//! Immutable cross-module event evidence and trace envelope.

use crate::*;

/// A serializable-in-spirit event payload before a transport is selected.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum EventPayload {
    /// A source-aware State record was durably accepted for projection.
    StateRecordObserved {
        /// Bounded record retaining source, semantic, receive time, and freshness.
        record: StateRecord,
    },
    /// A generic immutable Memory manifest became discoverable.
    MemoryManifestPublished {
        /// Immutable metadata; content bytes remain in the Artifact data plane.
        manifest: MemoryArtifactManifest,
    },
    /// A node staged one generic Memory artifact after digest verification.
    MemoryArtifactStaged {
        /// Immutable Memory revision staged by the node.
        manifest: MemoryArtifactManifest,
        /// Node that owns the local staging cache.
        node_id: NodeId,
        /// Exact node-local provider receiving the staged revision.
        #[serde(default = "legacy_memory_consumer_provider_id")]
        consumer_provider_id: String,
    },
    /// A node imported one generic Memory artifact into a local heterogeneous store.
    MemoryArtifactImported {
        /// Immutable Memory revision imported by the node.
        manifest: MemoryArtifactManifest,
        /// Node that owns the local imported representation.
        node_id: NodeId,
        /// Exact node-local provider owning the imported representation.
        #[serde(default = "legacy_memory_consumer_provider_id")]
        consumer_provider_id: String,
    },
    /// A node rejected generic Memory staging or import.
    MemoryArtifactRejected {
        /// Immutable Memory revision involved in the failed exchange.
        manifest: MemoryArtifactManifest,
        /// Node that rejected the operation.
        node_id: NodeId,
        /// Exact node-local provider that rejected the operation.
        #[serde(default = "legacy_memory_consumer_provider_id")]
        consumer_provider_id: String,
        /// Stable diagnostic retained as evidence.
        reason: String,
    },
    /// A map revision manifest was declared before its bytes were published.
    MapArtifactDeclared {
        /// Immutable map manifest retained by the catalog.
        manifest: MapArtifactManifest,
    },
    /// An immutable map artifact became available from the central artifact store.
    MapArtifactPublished {
        /// Immutable map manifest retained by the catalog.
        manifest: MapArtifactManifest,
    },
    /// A node began staging an immutable map artifact locally.
    MapArtifactStaged {
        /// Immutable map manifest being staged.
        manifest: MapArtifactManifest,
        /// Node that is staging the artifact.
        node_id: NodeId,
        /// Mission that requested the staging operation.
        mission_id: MissionId,
    },
    /// A node imported an immutable map artifact into its local cache.
    MapArtifactImported {
        /// Immutable map manifest imported by the node.
        manifest: MapArtifactManifest,
        /// Node that imported the artifact.
        node_id: NodeId,
        /// Mission that requested the import operation.
        mission_id: MissionId,
    },
    /// A node verified an imported artifact and its declared spatial metadata.
    MapLocalizationVerified {
        /// Resolved immutable artifact reference that was verified.
        artifact: MapArtifactRef,
        /// Node that performed the verification.
        node_id: NodeId,
        /// Mission that requested the verification operation.
        mission_id: MissionId,
        /// Anchor used by the localization check.
        anchor_id: SpatialAnchorId,
    },
    /// A node produced complete strong localization verification evidence.
    MapLocalizationEvidenceRecorded {
        /// Canonical evidence bound to artifact and execution identity.
        evidence: LocalizationVerificationEvidence,
    },
    /// A node rejected an artifact or could not verify its spatial metadata.
    MapArtifactRejected {
        /// Resolved immutable artifact reference that was rejected.
        artifact: MapArtifactRef,
        /// Node that rejected the artifact.
        node_id: NodeId,
        /// Mission that requested the import or verification operation.
        mission_id: MissionId,
        /// Stable diagnostic retained as evidence.
        reason: String,
    },
    /// A node registration became visible to control.
    NodeRegistered {
        /// Registered node identity.
        node_id: NodeId,
        /// Lease issued to the registered node.
        lease_id: LeaseId,
    },
    /// A node heartbeat was accepted and its lease renewed.
    NodeHeartbeatAccepted {
        /// Node whose heartbeat was accepted.
        node_id: NodeId,
        /// Lease renewed by the heartbeat.
        lease_id: LeaseId,
    },
    /// A node lease expired and the node became unschedulable.
    NodeLeaseExpired {
        /// Node whose lease expired.
        node_id: NodeId,
        /// Expired lease identity.
        lease_id: LeaseId,
    },
    /// Matching produced role candidates.
    CandidatesMatched {
        /// Mission-scoped task for which candidates were produced.
        task_ref: TaskRef,
    },
    /// The bounded joint Scheduler selected all normal Task assignments.
    TaskSchedulingSelected {
        /// Mission-scoped task represented by the selection decision.
        task_ref: TaskRef,
        /// Selected role, node, and proposed resource mappings.
        assignments: Vec<RoleAssignment>,
    },
    /// A Ready Task had no admissible bounded joint decision in the current tick.
    TaskSchedulingDeferred {
        /// Mission-scoped Task that remains Ready.
        task_ref: TaskRef,
        /// Stable scheduler outcome category, not a terminal Task failure.
        reason: String,
    },
    /// Control durably admitted a future or active planning interval.
    SchedulingReservationCreated {
        /// Mission-level Group that owns the scheduling commitment.
        group_id: ExecutionGroupId,
        /// Mission-scoped Task receiving the interval.
        task_ref: TaskRef,
        /// Inclusive Controller receive-time interval start.
        starts_at: TimestampMs,
        /// Exclusive planning end required for calendar admission.
        ends_at: TimestampMs,
        /// Control calendar generation used by the Scheduler decision.
        snapshot_version: u64,
    },
    /// A due scheduling reservation became active after Proposal, Commit, and Bind.
    SchedulingReservationActivated {
        /// Mission-level Group owning the physical commitment.
        group_id: ExecutionGroupId,
        /// Mission-scoped Task that activated.
        task_ref: TaskRef,
    },
    /// Activation revalidation invalidated a scheduling reservation.
    SchedulingReservationInvalidated {
        /// Mission-level Group retaining the Ready Task.
        group_id: ExecutionGroupId,
        /// Mission-scoped Task requiring a later scheduling pass.
        task_ref: TaskRef,
        /// Stable revalidation or conflict diagnostic.
        reason: String,
    },
    /// A terminal Task or Mission released its scheduling interval.
    SchedulingReservationReleased {
        /// Mission-level Group that owned the interval.
        group_id: ExecutionGroupId,
        /// Mission-scoped Task whose interval was removed.
        task_ref: TaskRef,
        /// Stable terminal release reason.
        reason: String,
    },
    /// A scheduler proposal was accepted for validation.
    ProposalCreated {
        /// Mission-scoped task represented by the proposal.
        task_ref: TaskRef,
    },
    /// Resource coordination committed a proposal.
    PlanCommitted {
        /// Mission-scoped task represented by the committed plan.
        task_ref: TaskRef,
    },
    /// An execution group was created and bound.
    ExecutionGroupBound {
        /// Group identity.
        group_id: ExecutionGroupId,
        /// Mission-scoped task assigned to the group.
        task_ref: TaskRef,
    },
    /// A Mission-level Execution Group was created for long-lived multi-Task execution.
    ExecutionGroupCreated {
        /// Group identity.
        group_id: ExecutionGroupId,
        /// Mission owning the Group.
        mission_id: MissionId,
    },
    /// A Task became an execution unit inside an existing Mission-level Group.
    TaskExecutionRegistered {
        /// Group hosting the Task execution.
        group_id: ExecutionGroupId,
        /// Mission-scoped Task identity.
        task_ref: TaskRef,
        /// Mission Intelligence context referenced by the Task.
        context_id: CoordinationContextId,
    },
    /// A registered Task became eligible after its DAG dependencies were satisfied.
    TaskExecutionReady {
        /// Group hosting the Task execution.
        group_id: ExecutionGroupId,
        /// Task that became ready.
        task_ref: TaskRef,
    },
    /// A Task execution became active inside its existing Group.
    TaskExecutionActivated {
        /// Group hosting the Task execution.
        group_id: ExecutionGroupId,
        /// Task that became active.
        task_ref: TaskRef,
    },
    /// Every current Role execution reported success, pending Task satisfaction evaluation.
    TaskExecutionCompleted {
        /// Group retaining the Mission execution context.
        group_id: ExecutionGroupId,
        /// Task whose local execution aggregate completed.
        task_ref: TaskRef,
    },
    /// Orchestration accepted the declared evidence basis as Task semantic satisfaction.
    TaskSatisfied {
        /// Group retaining the Mission execution context.
        group_id: ExecutionGroupId,
        /// Task whose human-readable outcome became satisfied.
        task_ref: TaskRef,
        /// Mission-declared evidence basis accepted for this transition.
        basis: TaskSatisfactionBasis,
    },
    /// A Task execution reached an unrecoverable failure state.
    TaskExecutionFailed {
        /// Group hosting the failed Task.
        group_id: ExecutionGroupId,
        /// Task that failed.
        task_ref: TaskRef,
    },
    /// Temporary Task bindings were released while the parent Group remained alive.
    TaskExecutionBindingsReleased {
        /// Group retaining unaffected members and Context bindings.
        group_id: ExecutionGroupId,
        /// Task whose temporary bindings were released.
        task_ref: TaskRef,
        /// Resources released for this Task only.
        resource_ids: Vec<ResourceId>,
    },
    /// Context-scoped bindings were released when a Mission Intelligence Context ended.
    ContextBindingsReleased {
        /// Group retaining the Mission execution context.
        group_id: ExecutionGroupId,
        /// Context whose continuous resources ended.
        context_id: CoordinationContextId,
        /// Resources released for the Context.
        resource_ids: Vec<ResourceId>,
    },
    /// A mission actor became authoritative only after its task Group was bound.
    MissionActorBound {
        /// Mission namespace for the actor binding.
        mission_id: MissionId,
        /// Logical actor that gained continuity authority.
        actor_id: ActorId,
        /// Concrete node selected for the actor.
        node_id: NodeId,
        /// Task whose successful Group bind established the authority.
        task_ref: TaskRef,
        /// Group bind that established the authority.
        group_id: ExecutionGroupId,
    },
    /// An execution group began executing its bound roles.
    ExecutionGroupActivated {
        /// Activated group identity.
        group_id: ExecutionGroupId,
        /// Mission-scoped task executed by the group.
        task_ref: TaskRef,
    },
    /// Reconciliation detected one assigned role whose node is no longer eligible.
    ReconciliationRoleRecoveryRequired {
        /// Active group containing the unavailable assignment.
        group_id: ExecutionGroupId,
        /// Mission-scoped task that owns the group.
        task_ref: TaskRef,
        /// Role whose current assignment requires recovery.
        role_id: RoleId,
        /// Currently assigned node that became unavailable.
        node_id: NodeId,
    },
    /// Role-scoped matching produced currently eligible recovery candidates.
    RecoveryCandidatesMatched {
        /// Blocked Group waiting for a replacement.
        group_id: ExecutionGroupId,
        /// Mission-scoped task retained by the Group.
        task_ref: TaskRef,
        /// Unbound role being rematched.
        role_id: RoleId,
        /// Eligible candidates in deterministic node order.
        candidate_node_ids: Vec<NodeId>,
    },
    /// The bounded deterministic Scheduler selected one recovery replacement.
    RecoverySchedulingSelected {
        /// Blocked Group awaiting the selected replacement.
        group_id: ExecutionGroupId,
        /// Mission-scoped task retained by the Group.
        task_ref: TaskRef,
        /// Unbound role being scheduled.
        role_id: RoleId,
        /// Failed node excluded from selection.
        previous_node_id: NodeId,
        /// Scheduler-selected replacement node.
        replacement_node_id: NodeId,
        /// Deterministically proposed resource IDs.
        resource_ids: Vec<ResourceId>,
    },
    /// Recovery scheduling found no feasible candidate in the supplied Candidate Set.
    RecoverySchedulingNoSelection {
        /// Blocked Group that remains pending.
        group_id: ExecutionGroupId,
        /// Mission-scoped task retained by the Group.
        task_ref: TaskRef,
        /// Unbound role that remains without selection.
        role_id: RoleId,
    },
    /// An external scheduler choice passed recovery proposal validation.
    RecoveryAssignmentProposed {
        /// Group targeted by the non-committed proposal.
        group_id: ExecutionGroupId,
        /// Mission-scoped task retained by the Group.
        task_ref: TaskRef,
        /// Role proposed for reassignment.
        role_id: RoleId,
        /// Scheduler-selected replacement node.
        replacement_node_id: NodeId,
        /// Proposed resources, not yet reserved.
        resource_ids: Vec<ResourceId>,
    },
    /// Shared Resource Coordination committed one replacement assignment.
    RecoveryAssignmentCommitted {
        /// Existing Group that owns the replacement commitment.
        group_id: ExecutionGroupId,
        /// Mission-scoped task that owns the commitment.
        task_ref: TaskRef,
        /// Role receiving the committed replacement.
        role_id: RoleId,
        /// Replacement node covered by the commitment.
        replacement_node_id: NodeId,
        /// Resources atomically reserved for the existing Group.
        resource_ids: Vec<ResourceId>,
    },
    /// A committed-but-not-bound recovery assignment was explicitly aborted.
    RecoveryAssignmentAborted {
        /// Group that owned the pending commitment.
        group_id: ExecutionGroupId,
        /// Mission-scoped task that owned the pending commitment.
        task_ref: TaskRef,
        /// Unbound role whose replacement attempt was aborted.
        role_id: RoleId,
        /// Replacement node no longer intended for rebind.
        replacement_node_id: NodeId,
        /// Replacement resources released by the abort.
        resource_ids: Vec<ResourceId>,
    },
    /// A node emitted an execution observation.
    NodeObservation(NodeEvent),
    /// Runtime detected an execution whose physical outcome requires reconciliation.
    RuntimeExecutionRecoveryRequired {
        /// Stable cross-session execution identity.
        execution_id: String,
        /// Node that reported or owns the ambiguous execution.
        node_id: NodeId,
        /// Committed Group identity when Runtime knows the execution context.
        group_id: Option<ExecutionGroupId>,
        /// Committed Task identity when Runtime knows the execution context.
        task_ref: Option<TaskRef>,
        /// Committed role identity when Runtime knows the execution context.
        role_id: Option<RoleId>,
        /// Diagnostic explaining why execution cannot safely continue.
        reason: String,
    },
    /// Runtime registered one Mission-owned execution coordination relation.
    ExecutionRelationRegistered {
        /// Mission-level Group containing both logical endpoints.
        group_id: ExecutionGroupId,
        /// Stable relation identity from the accepted MissionPlan.
        relation_id: ExecutionRelationId,
        /// Logical condition-provider Task.
        source_task_ref: TaskRef,
        /// Logical condition-provider Role.
        source_role_id: RoleId,
        /// Logical constrained Task.
        target_task_ref: TaskRef,
        /// Logical constrained Role.
        target_role_id: RoleId,
        /// Closed relation behavior.
        kind: ExecutionRelationKind,
        /// Typed relation contract retained for replay and evidence inspection.
        #[serde(default)]
        relation_type: ExecutionRelationType,
        /// Effective coupling mode of the constrained Task execution scope.
        #[serde(default)]
        coupling_mode: ExecutionCouplingMode,
    },
    /// Runtime execution facts changed the observable state of a relation.
    ExecutionRelationStateChanged {
        /// Mission-level Group containing both logical endpoints.
        group_id: ExecutionGroupId,
        /// Stable relation identity.
        relation_id: ExecutionRelationId,
        /// Previous Runtime-derived state.
        previous: ExecutionRelationState,
        /// New Runtime-derived state.
        current: ExecutionRelationState,
        /// Current source attempt, when dispatched.
        source_execution_id: Option<String>,
        /// Current target attempt, when dispatched.
        target_execution_id: Option<String>,
        /// Typed relation contract retained for replay and evidence inspection.
        #[serde(default)]
        relation_type: ExecutionRelationType,
        /// Effective coupling mode of the constrained Task execution scope.
        #[serde(default)]
        coupling_mode: ExecutionCouplingMode,
    },
    /// A relation violation or ambiguity fenced target progression for reconciliation.
    ExecutionRelationReconciliationRequired {
        /// Mission-level Group containing both logical endpoints.
        group_id: ExecutionGroupId,
        /// Stable relation identity.
        relation_id: ExecutionRelationId,
        /// Violated or unknown Runtime-derived state.
        state: ExecutionRelationState,
        /// Logical condition-provider Task.
        source_task_ref: TaskRef,
        /// Logical condition-provider Role.
        source_role_id: RoleId,
        /// Logical constrained Task.
        target_task_ref: TaskRef,
        /// Logical constrained Role.
        target_role_id: RoleId,
        /// Current source attempt, when dispatched.
        source_execution_id: Option<String>,
        /// Current target attempt, when dispatched.
        target_execution_id: Option<String>,
        /// Stable Runtime diagnostic.
        reason: String,
        /// Typed relation contract retained for replay and evidence inspection.
        #[serde(default)]
        relation_type: ExecutionRelationType,
        /// Effective coupling mode of the constrained Task execution scope.
        #[serde(default)]
        coupling_mode: ExecutionCouplingMode,
    },
    /// One admitted Local EAIOS peer-channel readiness acknowledgement.
    PeerChannelReadinessObserved {
        /// Mission-level Group containing the coordination Context.
        group_id: ExecutionGroupId,
        /// Coordination Context declaring the peer channel.
        context_id: CoordinationContextId,
        /// Logical ContextRole represented by the endpoint.
        context_role_id: ContextRoleId,
        /// Current physical Node carrying the endpoint.
        node_id: NodeId,
        /// Registered Local EAIOS owning the endpoint capability.
        local_system_id: LocalSystemId,
        /// Current Node Protocol session that supplied the fact.
        session_id: String,
        /// Shared channel instance agreed by Local EAIOS peers.
        channel_instance_id: String,
        /// Transport-neutral channel profile confirmed by the endpoint.
        profile_id: String,
        /// Transport-neutral message schema confirmed by the endpoint.
        message_schema: String,
        /// Node-management sequence admitted for this acknowledgement.
        sequence: u64,
        /// RoboGuide-local receive-relative evidence deadline.
        expires_at: TimestampMs,
        /// Whether the Local EAIOS currently confirms readiness.
        ready: bool,
    },
    /// A role was rebound after a recoverable failure.
    RecoveryRebound {
        /// Group being adapted.
        group_id: ExecutionGroupId,
        /// Mission-scoped task whose role was rebound.
        task_ref: TaskRef,
        /// Role being replaced.
        role_id: RoleId,
        /// Previous node.
        from_node: NodeId,
        /// Replacement node.
        to_node: NodeId,
    },
    /// The group completed all assigned roles.
    ExecutionGroupCompleted {
        /// Completed group identity.
        group_id: ExecutionGroupId,
        /// Mission-scoped task completed by the group.
        task_ref: TaskRef,
    },
    /// The current Group configuration cannot progress without reconciliation.
    ExecutionGroupBlocked {
        /// Blocked group identity.
        group_id: ExecutionGroupId,
        /// Mission-scoped task that could not continue.
        task_ref: TaskRef,
        /// Reason for escalation.
        reason: String,
    },
    /// One failed role released only its current member and resource binding.
    ExecutionGroupRoleBindingReleased {
        /// Group retaining its identity and unaffected bindings.
        group_id: ExecutionGroupId,
        /// Mission-scoped task that owns the group.
        task_ref: TaskRef,
        /// Role whose failed binding was released.
        role_id: RoleId,
        /// Node formerly bound to the role.
        node_id: NodeId,
        /// Resource reservations released only for this role.
        resource_ids: Vec<ResourceId>,
    },
    /// Recovery was explicitly exhausted and the group became terminally failed.
    ExecutionGroupFailed {
        /// Failed group identity.
        group_id: ExecutionGroupId,
        /// Mission-scoped task the group could not complete.
        task_ref: TaskRef,
        /// Explicit reason recovery could not continue.
        reason: String,
    },
    /// A terminal group released all current role and resource bindings.
    ExecutionGroupReleased {
        /// Released group identity.
        group_id: ExecutionGroupId,
        /// Mission-scoped task formerly owned by the group.
        task_ref: TaskRef,
        /// Resource reservations released in deterministic assignment order.
        resource_ids: Vec<ResourceId>,
    },
}

/// One immutable event with trace identities.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct EventRecord {
    /// Stable identity of this immutable event.
    event_id: EventId,
    /// Monotonic time at which the event was recorded.
    timestamp: TimestampMs,
    /// Operation trace identity shared by related events.
    correlation_id: CorrelationId,
    /// Immediate preceding event that caused this record, when known.
    causation_id: Option<EventId>,
    /// Domain observation or lifecycle transition represented by the event.
    payload: EventPayload,
}

impl EventRecord {
    /// Creates an immutable event record.
    pub const fn new(
        event_id: EventId,
        timestamp: TimestampMs,
        correlation_id: CorrelationId,
        causation_id: Option<EventId>,
        payload: EventPayload,
    ) -> Self {
        Self {
            event_id,
            timestamp,
            correlation_id,
            causation_id,
            payload,
        }
    }

    /// Returns the event identity.
    pub fn event_id(&self) -> &EventId {
        &self.event_id
    }

    /// Returns the event timestamp.
    pub const fn timestamp(&self) -> TimestampMs {
        self.timestamp
    }

    /// Returns the operation correlation identity.
    pub fn correlation_id(&self) -> &CorrelationId {
        &self.correlation_id
    }

    /// Returns the optional causation event identity.
    pub fn causation_id(&self) -> Option<&EventId> {
        self.causation_id.as_ref()
    }

    /// Returns the immutable event payload.
    pub fn payload(&self) -> &EventPayload {
        &self.payload
    }
}
