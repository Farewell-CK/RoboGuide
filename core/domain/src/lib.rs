#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! Domain values shared by DEAIOS control, runtime, and node adapters.
//!
//! This crate intentionally contains no transport, serialization, SDK, or
//! simulator dependency. It defines the first internal Node Contract shape.

use std::collections::{BTreeMap, BTreeSet};
use std::fmt::{Display, Formatter};

/// Version identifier for the first cross-language Mission Plan contract.
pub const MISSION_PLAN_SCHEMA_V0: &str = "roboguide.mission-plan/v0";

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
    /// A Mission Plan or Task Graph violated a structural invariant.
    InvalidMissionPlan {
        /// Stable diagnostic reason suitable for adapter and test evidence.
        reason: String,
    },
}

impl Display for DomainError {
    /// Formats a domain invariant violation for logs and test failures.
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::EmptyValue { kind } => write!(formatter, "{kind} must not be empty"),
            Self::InvalidDuration { kind } => write!(formatter, "invalid {kind} duration"),
            Self::LeaseExpired { kind } => write!(formatter, "{kind} lease has expired"),
            Self::InvalidMissionPlan { reason } => {
                write!(formatter, "invalid mission plan: {reason}")
            }
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

/// Uniquely identifies a mission-scoped task across concurrent missions.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash)]
pub struct TaskRef {
    /// Mission that owns the task namespace.
    mission_id: MissionId,
    /// Task identity scoped by the owning mission.
    task_id: TaskId,
}

impl TaskRef {
    /// Creates an unambiguous task reference from its mission and local task identity.
    pub const fn new(mission_id: MissionId, task_id: TaskId) -> Self {
        Self {
            mission_id,
            task_id,
        }
    }

    /// Returns the mission that owns this task.
    pub const fn mission_id(&self) -> &MissionId {
        &self.mission_id
    }

    /// Returns the task identity within its mission namespace.
    pub const fn task_id(&self) -> &TaskId {
        &self.task_id
    }
}

impl Display for TaskRef {
    /// Formats a task reference without collapsing its mission namespace.
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        write!(formatter, "{}/{}", self.mission_id, self.task_id)
    }
}

/// A millisecond clock reading whose comparison domain is defined by its containing field.
///
/// Readings from independent source clocks and RoboGuide clocks are not
/// directly comparable merely because they share this representation.
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
    /// RoboGuide-local time at which this lease interval began.
    issued_at: TimestampMs,
    /// RoboGuide-local time after which the lease cannot authorize scheduling.
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

    /// Returns whether the lease is active at the supplied RoboGuide-local time.
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
    /// Mission-scoped task whose execution requirements are being described.
    task_ref: TaskRef,
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
            task_ref: TaskRef::new(mission_id, task_id),
            roles,
        })
    }

    /// Returns the complete mission-scoped task identity.
    pub const fn task_ref(&self) -> &TaskRef {
        &self.task_ref
    }

    /// Returns the mission identity.
    pub const fn mission_id(&self) -> &MissionId {
        self.task_ref.mission_id()
    }

    /// Returns the task identity.
    pub const fn task_id(&self) -> &TaskId {
        self.task_ref.task_id()
    }

    /// Returns all role requirements in declaration order.
    pub fn roles(&self) -> &[RoleRequirement] {
        &self.roles
    }
}

/// A user-visible mission objective before global scheduling decisions.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MissionGoal {
    /// Stable mission identity shared by every task in the graph.
    mission_id: MissionId,
    /// Outcome Mission Intelligence must decompose without selecting nodes.
    objective: String,
}

impl MissionGoal {
    /// Creates a mission goal with a nonblank objective.
    pub fn new(mission_id: MissionId, objective: impl Into<String>) -> Result<Self, DomainError> {
        let objective = objective.into();
        if objective.trim().is_empty() {
            return Err(DomainError::EmptyValue {
                kind: "mission objective",
            });
        }
        Ok(Self {
            mission_id,
            objective,
        })
    }

    /// Returns the stable mission identity.
    pub fn mission_id(&self) -> &MissionId {
        &self.mission_id
    }

    /// Returns the requested user-visible outcome.
    pub fn objective(&self) -> &str {
        &self.objective
    }
}

/// One Task Graph node with dependencies and role-level execution requirements.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PlannedTask {
    /// Human-readable task outcome used for review and diagnostics.
    description: String,
    /// Task requirements consumed by Control capability matching.
    requirement: TaskRequirement,
    /// Tasks that must complete before this task becomes ready.
    dependencies: Vec<TaskId>,
}

impl PlannedTask {
    /// Creates a task while rejecting blank descriptions and duplicate dependencies.
    pub fn new(
        description: impl Into<String>,
        requirement: TaskRequirement,
        dependencies: Vec<TaskId>,
    ) -> Result<Self, DomainError> {
        let description = description.into();
        if description.trim().is_empty() {
            return Err(DomainError::EmptyValue {
                kind: "task description",
            });
        }
        let unique_dependencies: BTreeSet<&TaskId> = dependencies.iter().collect();
        if unique_dependencies.len() != dependencies.len() {
            return Err(DomainError::InvalidMissionPlan {
                reason: format!("task {} has duplicate dependencies", requirement.task_id()),
            });
        }
        if dependencies
            .iter()
            .any(|dependency| dependency == requirement.task_id())
        {
            return Err(DomainError::InvalidMissionPlan {
                reason: format!("task {} depends on itself", requirement.task_id()),
            });
        }
        Ok(Self {
            description,
            requirement,
            dependencies,
        })
    }

    /// Returns the task identity carried by its execution requirements.
    pub fn task_id(&self) -> &TaskId {
        self.requirement.task_id()
    }

    /// Returns the human-readable task outcome.
    pub fn description(&self) -> &str {
        &self.description
    }

    /// Returns the role-level requirements consumed by Control.
    pub const fn requirement(&self) -> &TaskRequirement {
        &self.requirement
    }

    /// Returns prerequisite task identities in declaration order.
    pub fn dependencies(&self) -> &[TaskId] {
        &self.dependencies
    }
}

/// A validated acyclic Task Graph owned by one mission.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TaskGraph {
    /// Mission that owns every task in this graph.
    mission_id: MissionId,
    /// Tasks retained in planner declaration order.
    tasks: Vec<PlannedTask>,
}

impl TaskGraph {
    /// Creates a graph and rejects identity mismatch, unknown dependencies, or cycles.
    pub fn new(mission_id: MissionId, tasks: Vec<PlannedTask>) -> Result<Self, DomainError> {
        if tasks.is_empty() {
            return Err(DomainError::InvalidMissionPlan {
                reason: "task graph must not be empty".to_string(),
            });
        }
        let mut dependencies = BTreeMap::<TaskId, BTreeSet<TaskId>>::new();
        for task in &tasks {
            if task.requirement().mission_id() != &mission_id {
                return Err(DomainError::InvalidMissionPlan {
                    reason: format!("task {} belongs to another mission", task.task_id()),
                });
            }
            if dependencies
                .insert(
                    task.task_id().clone(),
                    task.dependencies().iter().cloned().collect(),
                )
                .is_some()
            {
                return Err(DomainError::InvalidMissionPlan {
                    reason: format!("duplicate task id {}", task.task_id()),
                });
            }
        }
        let known_tasks: BTreeSet<TaskId> = dependencies.keys().cloned().collect();
        for (task_id, prerequisites) in &dependencies {
            if let Some(unknown) = prerequisites
                .iter()
                .find(|dependency| !known_tasks.contains(*dependency))
            {
                return Err(DomainError::InvalidMissionPlan {
                    reason: format!("task {task_id} depends on unknown task {unknown}"),
                });
            }
        }
        let mut remaining = dependencies;
        while !remaining.is_empty() {
            let ready: BTreeSet<TaskId> = remaining
                .iter()
                .filter(|(_, prerequisites)| prerequisites.is_empty())
                .map(|(task_id, _)| task_id.clone())
                .collect();
            if ready.is_empty() {
                return Err(DomainError::InvalidMissionPlan {
                    reason: "task graph contains a cycle".to_string(),
                });
            }
            remaining.retain(|task_id, _| !ready.contains(task_id));
            for prerequisites in remaining.values_mut() {
                prerequisites.retain(|dependency| !ready.contains(dependency));
            }
        }
        Ok(Self { mission_id, tasks })
    }

    /// Returns the mission that owns this graph.
    pub fn mission_id(&self) -> &MissionId {
        &self.mission_id
    }

    /// Returns tasks in planner declaration order.
    pub fn tasks(&self) -> &[PlannedTask] {
        &self.tasks
    }

    /// Returns tasks whose dependencies are all present in the completed set.
    pub fn ready_tasks(&self, completed: &BTreeSet<TaskId>) -> Vec<&PlannedTask> {
        self.tasks
            .iter()
            .filter(|task| {
                !completed.contains(task.task_id())
                    && task
                        .dependencies()
                        .iter()
                        .all(|dependency| completed.contains(dependency))
            })
            .collect()
    }
}

/// A versioned Mission Intelligence result accepted by the DEAIOS core.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MissionPlan {
    /// User-visible goal preserved across planning and recovery.
    goal: MissionGoal,
    /// Validated task decomposition and execution requirements.
    task_graph: TaskGraph,
}

impl MissionPlan {
    /// Creates a plan only when the goal and Task Graph share one mission identity.
    pub fn new(goal: MissionGoal, task_graph: TaskGraph) -> Result<Self, DomainError> {
        if goal.mission_id() != task_graph.mission_id() {
            return Err(DomainError::InvalidMissionPlan {
                reason: "goal and task graph mission ids differ".to_string(),
            });
        }
        Ok(Self { goal, task_graph })
    }

    /// Returns the versioned adapter contract represented by this domain shape.
    pub const fn schema_version(&self) -> &'static str {
        MISSION_PLAN_SCHEMA_V0
    }

    /// Returns the original mission goal.
    pub const fn goal(&self) -> &MissionGoal {
        &self.goal
    }

    /// Returns the validated Task Graph.
    pub const fn task_graph(&self) -> &TaskGraph {
        &self.task_graph
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
    /// Source-local time at which the Local EAIOS observed this health.
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

    /// Returns when the source observed this health in its own local time domain.
    pub const fn observed_at(self) -> TimestampMs {
        self.observed_at
    }
}

/// A normalized health observation reported by one local EAIOS or adapter.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NodeHealthObservation {
    /// Node whose local health was observed.
    node_id: NodeId,
    /// Latest timestamped health explicitly reported by the local system.
    status: NodeStatus,
    /// RoboGuide-local time at which this observation was received and normalized.
    received_at: TimestampMs,
}

impl NodeHealthObservation {
    /// Creates a transport-neutral node health observation.
    pub const fn new(node_id: NodeId, status: NodeStatus, received_at: TimestampMs) -> Self {
        Self {
            node_id,
            status,
            received_at,
        }
    }

    /// Returns the node that produced the health observation.
    pub const fn node_id(&self) -> &NodeId {
        &self.node_id
    }

    /// Returns the timestamped health reported by the local system.
    pub const fn status(&self) -> NodeStatus {
        self.status
    }

    /// Returns when RoboGuide received this observation in its local time domain.
    pub const fn received_at(&self) -> TimestampMs {
        self.received_at
    }
}

/// Minimal system-observed reachability of one node.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NodeLiveness {
    /// RoboGuide successfully observed or reached the node.
    Reachable,
    /// RoboGuide can no longer establish current reachability.
    Unreachable,
}

/// A timestamped liveness fact derived by RoboGuide rather than the local EAIOS.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct NodeLivenessObservation {
    /// Current minimal reachability classification.
    liveness: NodeLiveness,
    /// RoboGuide-local time at which it observed this liveness.
    observed_at: TimestampMs,
}

impl NodeLivenessObservation {
    /// Creates a timestamped system-observed liveness fact.
    pub const fn new(liveness: NodeLiveness, observed_at: TimestampMs) -> Self {
        Self {
            liveness,
            observed_at,
        }
    }

    /// Returns the observed reachability classification.
    pub const fn liveness(self) -> NodeLiveness {
        self.liveness
    }

    /// Returns when RoboGuide observed this liveness.
    pub const fn observed_at(self) -> TimestampMs {
        self.observed_at
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

/// The latest shared registration, reported health, and liveness facts for one node.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NodeStateSnapshot {
    /// Local runtime, capability, and resource facts advertised by the node.
    registration: NodeRegistration,
    /// Latest accepted health explicitly reported by the local EAIOS.
    reported_status: NodeStatus,
    /// RoboGuide-local receive time of the latest reported health.
    reported_status_received_at: TimestampMs,
    /// Latest reachability fact observed by RoboGuide.
    liveness: NodeLivenessObservation,
}

impl NodeStateSnapshot {
    /// Creates a shared node snapshot from transport-neutral domain facts.
    pub const fn new(
        registration: NodeRegistration,
        reported_status: NodeStatus,
        reported_status_received_at: TimestampMs,
        liveness: NodeLivenessObservation,
    ) -> Self {
        Self {
            registration,
            reported_status,
            reported_status_received_at,
            liveness,
        }
    }

    /// Returns the node identity represented by this snapshot.
    pub fn node_id(&self) -> &NodeId {
        self.registration.node_id()
    }

    /// Returns the node's latest advertised runtime, capabilities, and resources.
    pub const fn registration(&self) -> &NodeRegistration {
        &self.registration
    }

    /// Returns the latest health explicitly reported by the local EAIOS.
    pub const fn reported_status(&self) -> NodeStatus {
        self.reported_status
    }

    /// Returns when RoboGuide received the latest reported health.
    pub const fn reported_status_received_at(&self) -> TimestampMs {
        self.reported_status_received_at
    }

    /// Returns the latest system-observed liveness fact.
    pub const fn liveness(&self) -> NodeLivenessObservation {
        self.liveness
    }
}

/// The result reported by a local node after receiving an execution command.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum NodeEvent {
    /// The local node completed the assigned role.
    TaskCompleted {
        /// Node that executed the role.
        node_id: NodeId,
        /// Mission-scoped task that was executed.
        task_ref: TaskRef,
        /// Execution group containing the role.
        group_id: ExecutionGroupId,
        /// Role that completed.
        role_id: RoleId,
    },
    /// The local node rejected or failed the assigned role.
    TaskFailed {
        /// Node that attempted the role.
        node_id: NodeId,
        /// Mission-scoped task that failed.
        task_ref: TaskRef,
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
    /// Mission-scoped task whose role is being invoked.
    task_ref: TaskRef,
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
            task_ref: TaskRef::new(mission_id, task_id),
            group_id,
            role_id,
            node_id,
            correlation_id,
        }
    }

    /// Returns the complete mission-scoped task identity.
    pub const fn task_ref(&self) -> &TaskRef {
        &self.task_ref
    }

    /// Returns the mission targeted by this command.
    pub const fn mission_id(&self) -> &MissionId {
        self.task_ref.mission_id()
    }

    /// Returns the task targeted by this command.
    pub const fn task_id(&self) -> &TaskId {
        self.task_ref.task_id()
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
        /// Mission-scoped task for which candidates were produced.
        task_ref: TaskRef,
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
    /// An execution group began executing its bound roles.
    ExecutionGroupActivated {
        /// Activated group identity.
        group_id: ExecutionGroupId,
        /// Mission-scoped task executed by the group.
        task_ref: TaskRef,
    },
    /// A node emitted an execution observation.
    NodeObservation(NodeEvent),
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

#[cfg(test)]
mod tests {
    use super::*;

    /// Builds one valid task with no dependencies for graph invariant tests.
    fn task(mission_id: &MissionId, task_id: &str, dependencies: Vec<TaskId>) -> PlannedTask {
        let task_id = TaskId::new(task_id).expect("test task id must be valid");
        let role = RoleRequirement::new(
            RoleId::new(format!("role-{task_id}")).expect("test role id must be valid"),
            CapabilityKind::Transport,
            Some(ResourceKind::Space),
        );
        let requirement = TaskRequirement::new(mission_id.clone(), task_id, vec![role])
            .expect("test requirement must be valid");
        PlannedTask::new("transport payload", requirement, dependencies)
            .expect("test task must be valid")
    }

    /// Acyclic dependencies expose only tasks whose prerequisites have completed.
    #[test]
    fn task_graph_returns_ready_tasks() {
        let mission_id = MissionId::new("mission-ready").expect("test mission id must be valid");
        let first = task(&mission_id, "task-first", vec![]);
        let first_id = first.task_id().clone();
        let second = task(&mission_id, "task-second", vec![first_id.clone()]);
        let graph = TaskGraph::new(mission_id, vec![first, second])
            .expect("acyclic test graph must be valid");

        let initially_ready = graph.ready_tasks(&BTreeSet::new());
        assert_eq!(initially_ready.len(), 1);
        assert_eq!(initially_ready[0].task_id(), &first_id);

        let completed = BTreeSet::from([first_id]);
        let ready_after_first = graph.ready_tasks(&completed);
        assert_eq!(ready_after_first.len(), 1);
        assert_eq!(ready_after_first[0].task_id().as_str(), "task-second");
    }

    /// A cyclic Task Graph is rejected before Control can consume any requirement.
    #[test]
    fn task_graph_rejects_cycle() {
        let mission_id = MissionId::new("mission-cycle").expect("test mission id must be valid");
        let first = task(
            &mission_id,
            "task-first",
            vec![TaskId::new("task-second").expect("test dependency id must be valid")],
        );
        let second = task(
            &mission_id,
            "task-second",
            vec![TaskId::new("task-first").expect("test dependency id must be valid")],
        );

        assert!(matches!(
            TaskGraph::new(mission_id, vec![first, second]),
            Err(DomainError::InvalidMissionPlan { reason }) if reason.contains("cycle")
        ));
    }

    /// A plan cannot combine a goal and Task Graph from different missions.
    #[test]
    fn mission_plan_rejects_identity_mismatch() {
        let goal_mission =
            MissionId::new("mission-goal").expect("test goal mission id must be valid");
        let graph_mission =
            MissionId::new("mission-graph").expect("test graph mission id must be valid");
        let goal = MissionGoal::new(goal_mission, "deliver payload")
            .expect("test mission goal must be valid");
        let graph = TaskGraph::new(
            graph_mission.clone(),
            vec![task(&graph_mission, "task-deliver", vec![])],
        )
        .expect("test task graph must be valid");

        assert!(matches!(
            MissionPlan::new(goal, graph),
            Err(DomainError::InvalidMissionPlan { .. })
        ));
    }
}
