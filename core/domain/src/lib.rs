#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! Domain values shared by DEAIOS control, runtime, and node adapters.
//!
//! This crate intentionally contains no transport, serialization, SDK, or
//! simulator dependency. It defines the first internal Node Contract shape.

use std::fmt::{Display, Formatter};

/// Errors raised when a domain value violates an invariant.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DomainError {
    /// An identifier or runtime label was empty.
    EmptyValue {
        /// The kind of value that was empty.
        kind: &'static str,
    },
    /// A lease duration or timestamp range could not be represented safely.
    InvalidDuration {
        /// The duration or range that violated a domain invariant.
        kind: &'static str,
    },
    /// An operation attempted to use a lease after its expiry instant.
    LeaseExpired {
        /// The kind of lease operation that was rejected.
        kind: &'static str,
    },
}

impl Display for DomainError {
    /// Formats a domain invariant violation for logs and test failures.
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::EmptyValue { kind } => write!(formatter, "{kind} must not be empty"),
            Self::InvalidDuration { kind } => write!(formatter, "invalid {kind} duration"),
            Self::LeaseExpired { kind } => write!(formatter, "{kind} lease has expired"),
        }
    }
}

impl std::error::Error for DomainError {}

/// Defines a validated, strongly typed identifier with a stable text form.
macro_rules! define_identifier {
    ($name:ident, $doc:literal, $kind:literal) => {
        #[doc = $doc]
        #[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash)]
        pub struct $name(String);

        impl $name {
            #[doc = concat!("Creates a validated ", $kind, " identifier.")]
            pub fn new(value: impl Into<String>) -> Result<Self, DomainError> {
                let value = value.into();
                if value.trim().is_empty() {
                    return Err(DomainError::EmptyValue { kind: $kind });
                }
                Ok(Self(value))
            }

            #[doc = concat!("Returns the ", $kind, " identifier as text.")]
            pub fn as_str(&self) -> &str {
                &self.0
            }
        }

        impl Display for $name {
            /// Writes the stable text form of this identifier.
            fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
                formatter.write_str(&self.0)
            }
        }
    };
}

define_identifier!(
    MissionId,
    "Identifies a mission supplied to DEAIOS.",
    "mission"
);
define_identifier!(TaskId, "Identifies a task within a mission.", "task");
define_identifier!(NodeId, "Identifies a logical execution node.", "node");
define_identifier!(
    RoleId,
    "Identifies a responsibility inside an execution group.",
    "role"
);
define_identifier!(ResourceId, "Identifies a reservable resource.", "resource");
define_identifier!(
    ExecutionGroupId,
    "Identifies a dynamic execution group.",
    "execution group"
);
define_identifier!(EventId, "Identifies one immutable event record.", "event");
define_identifier!(
    CorrelationId,
    "Identifies one end-to-end operation trace.",
    "correlation"
);
define_identifier!(LeaseId, "Identifies a renewable node lease.", "lease");

/// A monotonic timestamp used by deterministic core tests and runtime adapters.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct TimestampMs(u64);

impl TimestampMs {
    /// Creates a timestamp from elapsed milliseconds.
    pub const fn new(milliseconds: u64) -> Self {
        Self(milliseconds)
    }

    /// Returns elapsed milliseconds represented by this timestamp.
    pub const fn as_millis(self) -> u64 {
        self.0
    }
}

/// A renewable time-bound authority for a node to remain schedulable.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NodeLease {
    /// Stable identity of the lease instance.
    lease_id: LeaseId,
    /// Node that owns the lease.
    node_id: NodeId,
    /// Time at which this lease interval began.
    issued_at: TimestampMs,
    /// Time after which the lease cannot authorize scheduling.
    expires_at: TimestampMs,
}

impl NodeLease {
    /// Creates a lease with a strictly positive duration.
    pub fn new(
        lease_id: LeaseId,
        node_id: NodeId,
        issued_at: TimestampMs,
        duration_ms: u64,
    ) -> Result<Self, DomainError> {
        if duration_ms == 0 {
            return Err(DomainError::InvalidDuration { kind: "node lease" });
        }
        let expires_at = issued_at
            .as_millis()
            .checked_add(duration_ms)
            .ok_or(DomainError::InvalidDuration { kind: "node lease" })?;
        Ok(Self {
            lease_id,
            node_id,
            issued_at,
            expires_at: TimestampMs::new(expires_at),
        })
    }

    /// Returns the lease identity.
    pub fn lease_id(&self) -> &LeaseId {
        &self.lease_id
    }

    /// Returns the node authorized by this lease.
    pub fn node_id(&self) -> &NodeId {
        &self.node_id
    }

    /// Returns when the lease was issued or last renewed.
    pub const fn issued_at(&self) -> TimestampMs {
        self.issued_at
    }

    /// Returns the first timestamp at which the lease is no longer active.
    pub const fn expires_at(&self) -> TimestampMs {
        self.expires_at
    }

    /// Returns whether the lease is active at the supplied monotonic time.
    pub const fn is_active_at(&self, now: TimestampMs) -> bool {
        now.as_millis() < self.expires_at.as_millis()
    }

    /// Renews an active lease without changing its identity or owning node.
    pub fn renew(&self, now: TimestampMs, duration_ms: u64) -> Result<Self, DomainError> {
        if !self.is_active_at(now) {
            return Err(DomainError::LeaseExpired { kind: "node" });
        }
        Self::new(
            self.lease_id.clone(),
            self.node_id.clone(),
            now,
            duration_ms,
        )
    }
}

/// Identifies the local runtime implementation behind a node adapter.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LocalRuntime {
    /// Human-readable name of the local EAIOS implementation.
    name: String,
    /// Version reported by the local runtime implementation.
    version: String,
}

impl LocalRuntime {
    /// Creates a validated local runtime descriptor for an EAIOS or equivalent.
    pub fn new(name: impl Into<String>, version: impl Into<String>) -> Result<Self, DomainError> {
        let name = name.into();
        let version = version.into();
        if name.trim().is_empty() {
            return Err(DomainError::EmptyValue {
                kind: "runtime name",
            });
        }
        if version.trim().is_empty() {
            return Err(DomainError::EmptyValue {
                kind: "runtime version",
            });
        }
        Ok(Self { name, version })
    }

    /// Returns the local runtime implementation name.
    pub fn name(&self) -> &str {
        &self.name
    }

    /// Returns the local runtime implementation version.
    pub fn version(&self) -> &str {
        &self.version
    }
}

/// Capability categories understood by the first DEAIOS slice.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum CapabilityKind {
    /// Ability to move through the shared physical space.
    Mobility,
    /// Ability to carry or transport a task payload.
    Transport,
    /// Ability to execute compute work.
    Compute,
    /// Ability to produce observations about the world or node state.
    Observation,
}

/// One capability advertised by a node.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Capability {
    /// Capability category exposed by the node.
    kind: CapabilityKind,
    /// Whether control may currently schedule this capability.
    available: bool,
}

impl Capability {
    /// Creates a capability with an explicit initial availability state.
    pub const fn new(kind: CapabilityKind, available: bool) -> Self {
        Self { kind, available }
    }

    /// Returns the category of this capability.
    pub const fn kind(&self) -> CapabilityKind {
        self.kind
    }

    /// Returns whether the capability may currently be scheduled.
    pub const fn is_available(&self) -> bool {
        self.available
    }
}

/// Resource categories that may participate in a proposal or commitment.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub enum ResourceKind {
    /// A shared physical region, lane, or corridor.
    Space,
    /// A bounded compute allocation.
    Compute,
    /// A time window or temporal execution slot.
    Time,
}

/// A resource advertised by a node.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Resource {
    /// Stable identity of the reservable resource.
    id: ResourceId,
    /// Resource category used during coordination.
    kind: ResourceKind,
    /// Capacity available for the resource's category.
    capacity: u32,
}

impl Resource {
    /// Creates a resource with a positive capacity.
    pub fn new(id: ResourceId, kind: ResourceKind, capacity: u32) -> Result<Self, DomainError> {
        if capacity == 0 {
            return Err(DomainError::EmptyValue {
                kind: "resource capacity",
            });
        }
        Ok(Self { id, kind, capacity })
    }

    /// Returns the resource identity.
    pub fn id(&self) -> &ResourceId {
        &self.id
    }

    /// Returns the resource category.
    pub const fn kind(&self) -> ResourceKind {
        self.kind
    }

    /// Returns the advertised capacity.
    pub const fn capacity(&self) -> u32 {
        self.capacity
    }
}

/// A role and the capability/resource facts required to perform it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RoleRequirement {
    /// Responsibility identity required by the task.
    role_id: RoleId,
    /// Capability category needed to perform the role.
    capability: CapabilityKind,
    /// Optional resource category that must be bound to the role.
    resource_kind: Option<ResourceKind>,
}

impl RoleRequirement {
    /// Creates a role requirement for task matching.
    pub const fn new(
        role_id: RoleId,
        capability: CapabilityKind,
        resource_kind: Option<ResourceKind>,
    ) -> Self {
        Self {
            role_id,
            capability,
            resource_kind,
        }
    }

    /// Returns the role identity.
    pub fn role_id(&self) -> &RoleId {
        &self.role_id
    }

    /// Returns the required capability.
    pub const fn capability(&self) -> CapabilityKind {
        self.capability
    }

    /// Returns the optional resource category required by this role.
    pub const fn resource_kind(&self) -> Option<ResourceKind> {
        self.resource_kind
    }
}

/// A mission task's role-level execution requirements.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TaskRequirement {
    /// Mission containing the task.
    mission_id: MissionId,
    /// Task whose execution requirements are being described.
    task_id: TaskId,
    /// Role requirements in the task's declared order.
    roles: Vec<RoleRequirement>,
}

impl TaskRequirement {
    /// Creates a task requirement with at least one role.
    pub fn new(
        mission_id: MissionId,
        task_id: TaskId,
        roles: Vec<RoleRequirement>,
    ) -> Result<Self, DomainError> {
        if roles.is_empty() {
            return Err(DomainError::EmptyValue { kind: "task roles" });
        }
        Ok(Self {
            mission_id,
            task_id,
            roles,
        })
    }

    /// Returns the mission identity.
    pub fn mission_id(&self) -> &MissionId {
        &self.mission_id
    }

    /// Returns the task identity.
    pub fn task_id(&self) -> &TaskId {
        &self.task_id
    }

    /// Returns all role requirements in declaration order.
    pub fn roles(&self) -> &[RoleRequirement] {
        &self.roles
    }
}

/// A node's proposed assignment for one execution-group role.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RoleAssignment {
    /// Role receiving the assignment.
    role_id: RoleId,
    /// Node selected to execute the role.
    node_id: NodeId,
    /// Resources proposed for the role's execution.
    resource_ids: Vec<ResourceId>,
}

impl RoleAssignment {
    /// Creates a role assignment before proposal validation.
    pub const fn new(role_id: RoleId, node_id: NodeId, resource_ids: Vec<ResourceId>) -> Self {
        Self {
            role_id,
            node_id,
            resource_ids,
        }
    }

    /// Returns the assigned role.
    pub fn role_id(&self) -> &RoleId {
        &self.role_id
    }

    /// Returns the assigned node.
    pub fn node_id(&self) -> &NodeId {
        &self.node_id
    }

    /// Returns resources reserved for this role.
    pub fn resource_ids(&self) -> &[ResourceId] {
        &self.resource_ids
    }
}

/// The health state a node reports to the distributed system.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NodeHealth {
    /// The node is available for normal scheduling and execution.
    Online,
    /// The node may execute work but has degraded evidence or capacity.
    Degraded,
    /// The node cannot receive new work.
    Offline,
    /// The node has entered a local safety stop.
    SafeStopped,
}

impl NodeHealth {
    /// Returns whether this health state may be considered by matching.
    pub const fn is_schedulable(self) -> bool {
        matches!(self, Self::Online | Self::Degraded)
    }
}

/// A timestamped health snapshot for a node.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct NodeStatus {
    /// Most recent health classification reported by the node.
    health: NodeHealth,
    /// Monotonic time at which the health was observed.
    observed_at: TimestampMs,
}

/// A health-bearing heartbeat sent by a local EAIOS to DEAIOS.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NodeHeartbeat {
    /// Node sending the heartbeat.
    node_id: NodeId,
    /// Lease the node claims to renew.
    lease_id: LeaseId,
    /// Latest health snapshot observed by the node.
    status: NodeStatus,
}

impl NodeHeartbeat {
    /// Creates a heartbeat for one node and lease.
    pub const fn new(node_id: NodeId, lease_id: LeaseId, status: NodeStatus) -> Self {
        Self {
            node_id,
            lease_id,
            status,
        }
    }

    /// Returns the node sending this heartbeat.
    pub fn node_id(&self) -> &NodeId {
        &self.node_id
    }

    /// Returns the lease being renewed.
    pub fn lease_id(&self) -> &LeaseId {
        &self.lease_id
    }

    /// Returns the health snapshot carried by this heartbeat.
    pub const fn status(&self) -> NodeStatus {
        self.status
    }
}

impl NodeStatus {
    /// Creates a health snapshot with its observation time.
    pub const fn new(health: NodeHealth, observed_at: TimestampMs) -> Self {
        Self {
            health,
            observed_at,
        }
    }

    /// Returns the reported health state.
    pub const fn health(self) -> NodeHealth {
        self.health
    }

    /// Returns when this snapshot was observed.
    pub const fn observed_at(self) -> TimestampMs {
        self.observed_at
    }

    /// Returns whether the snapshot is within the supplied freshness window.
    ///
    /// A snapshot observed in the future is treated as fresh so deterministic
    /// callers cannot fail solely because their clocks moved in opposite order.
    pub const fn is_fresh_at(self, now: TimestampMs, max_age_ms: u64) -> bool {
        now.as_millis().saturating_sub(self.observed_at.as_millis()) <= max_age_ms
    }
}

/// The local runtime and resources a node exposes to DEAIOS.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NodeRegistration {
    /// Logical node identity exposed to DEAIOS.
    node_id: NodeId,
    /// Local EAIOS or equivalent runtime descriptor.
    local_runtime: LocalRuntime,
    /// Capabilities currently advertised by the node.
    capabilities: Vec<Capability>,
    /// Resources currently advertised by the node.
    resources: Vec<Resource>,
}

impl NodeRegistration {
    /// Creates a node registration used by matching and adapter negotiation.
    pub fn new(
        node_id: NodeId,
        local_runtime: LocalRuntime,
        capabilities: Vec<Capability>,
        resources: Vec<Resource>,
    ) -> Self {
        Self {
            node_id,
            local_runtime,
            capabilities,
            resources,
        }
    }

    /// Returns the logical node identity.
    pub fn node_id(&self) -> &NodeId {
        &self.node_id
    }

    /// Returns the local EAIOS or equivalent runtime descriptor.
    pub fn local_runtime(&self) -> &LocalRuntime {
        &self.local_runtime
    }

    /// Returns the node's advertised capabilities.
    pub fn capabilities(&self) -> &[Capability] {
        &self.capabilities
    }

    /// Returns the node's advertised resources.
    pub fn resources(&self) -> &[Resource] {
        &self.resources
    }

    /// Checks whether this registration can satisfy one role requirement.
    pub fn supports_role(&self, requirement: &RoleRequirement) -> bool {
        let has_capability = self.capabilities.iter().any(|capability| {
            capability.kind() == requirement.capability() && capability.is_available()
        });
        let has_resource = requirement.resource_kind().is_none_or(|kind| {
            self.resources
                .iter()
                .any(|resource| resource.kind() == kind && resource.capacity() > 0)
        });
        has_capability && has_resource
    }

    /// Returns all resource identifiers of the requested category.
    pub fn resource_ids_of_kind(&self, kind: ResourceKind) -> Vec<ResourceId> {
        self.resources
            .iter()
            .filter(|resource| resource.kind() == kind)
            .map(|resource| resource.id().clone())
            .collect()
    }

    /// Checks whether a resource belongs to this node and has the requested kind.
    pub fn owns_resource(&self, resource_id: &ResourceId, kind: Option<ResourceKind>) -> bool {
        self.resources.iter().any(|resource| {
            resource.id() == resource_id && kind.is_none_or(|expected| resource.kind() == expected)
        })
    }
}

/// The result reported by a local node after receiving an execution command.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum NodeEvent {
    /// The local node completed the assigned role.
    TaskCompleted {
        /// Node that executed the role.
        node_id: NodeId,
        /// Task that was executed.
        task_id: TaskId,
        /// Execution group containing the role.
        group_id: ExecutionGroupId,
        /// Role that completed.
        role_id: RoleId,
    },
    /// The local node rejected or failed the assigned role.
    TaskFailed {
        /// Node that attempted the role.
        node_id: NodeId,
        /// Task that failed.
        task_id: TaskId,
        /// Execution group containing the role.
        group_id: ExecutionGroupId,
        /// Role that failed.
        role_id: RoleId,
        /// Stable human-readable failure reason.
        reason: String,
    },
    /// The local node entered a safety stop.
    SafeStopped {
        /// Node that stopped.
        node_id: NodeId,
        /// Reason reported by local safety.
        reason: String,
    },
}

/// A command sent through the runtime to a local node.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ExecutionCommand {
    /// Mission containing the commanded task.
    mission_id: MissionId,
    /// Task whose role is being invoked.
    task_id: TaskId,
    /// Execution group that owns the role lifecycle.
    group_id: ExecutionGroupId,
    /// Role being invoked on the node.
    role_id: RoleId,
    /// Node that receives the command.
    node_id: NodeId,
    /// Correlation identity for the command and its observations.
    correlation_id: CorrelationId,
}

impl ExecutionCommand {
    /// Creates a role-scoped command without exposing transport details.
    pub const fn new(
        mission_id: MissionId,
        task_id: TaskId,
        group_id: ExecutionGroupId,
        role_id: RoleId,
        node_id: NodeId,
        correlation_id: CorrelationId,
    ) -> Self {
        Self {
            mission_id,
            task_id,
            group_id,
            role_id,
            node_id,
            correlation_id,
        }
    }

    /// Returns the mission targeted by this command.
    pub fn mission_id(&self) -> &MissionId {
        &self.mission_id
    }

    /// Returns the task targeted by this command.
    pub fn task_id(&self) -> &TaskId {
        &self.task_id
    }

    /// Returns the execution group targeted by this command.
    pub fn group_id(&self) -> &ExecutionGroupId {
        &self.group_id
    }

    /// Returns the role targeted by this command.
    pub fn role_id(&self) -> &RoleId {
        &self.role_id
    }

    /// Returns the node receiving this command.
    pub fn node_id(&self) -> &NodeId {
        &self.node_id
    }

    /// Returns the operation correlation identity.
    pub fn correlation_id(&self) -> &CorrelationId {
        &self.correlation_id
    }
}

/// A serializable-in-spirit event payload before a transport is selected.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum EventPayload {
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
        /// Task for which candidates were produced.
        task_id: TaskId,
    },
    /// A scheduler proposal was accepted for validation.
    ProposalCreated {
        /// Task represented by the proposal.
        task_id: TaskId,
    },
    /// Resource coordination committed a proposal.
    PlanCommitted {
        /// Task represented by the committed plan.
        task_id: TaskId,
    },
    /// An execution group was created and bound.
    ExecutionGroupBound {
        /// Group identity.
        group_id: ExecutionGroupId,
        /// Task assigned to the group.
        task_id: TaskId,
    },
    /// A node emitted an execution observation.
    NodeObservation(NodeEvent),
    /// A role was rebound after a recoverable failure.
    RecoveryRebound {
        /// Group being adapted.
        group_id: ExecutionGroupId,
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
    },
    /// The group could not recover safely.
    ExecutionGroupBlocked {
        /// Blocked group identity.
        group_id: ExecutionGroupId,
        /// Reason for escalation.
        reason: String,
    },
}

/// One immutable event with trace identities.
#[derive(Debug, Clone, PartialEq, Eq)]
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
