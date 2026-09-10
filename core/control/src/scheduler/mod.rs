//! Deterministic bounded joint policy for Capability, Compute, Space, and Time.
//!
//! The policy consumes Candidate Sets produced by Capability Matching and
//! returns selection evidence. It does not re-evaluate eligibility, validate
//! proposals, inspect reservations, commit resources, or mutate Groups/State.

use super::{CandidateSet, RecoveryCandidateSet};
use domain::{
    CorrelationId, EventPayload, ExecutionGroupId, NodeId, ResourceId, RoleAssignment, RoleId,
    RoleRequirement, TaskRef, TaskRequirement, TimestampMs,
};
use ports::{EventSink, SharedNodeStateReader};
use std::collections::{BTreeMap, BTreeSet};
use std::fmt::{Display, Formatter};

mod policy;

pub use policy::BoundedJointScheduler;

/// Failures produced while forming a deterministic scheduling decision.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SchedulerError {
    /// Candidate context did not match the supplied task or role requirements.
    InvalidCandidateSet(String),
    /// A Candidate Set referenced a node absent from Shared Node State.
    UnknownCandidate(NodeId),
    /// No candidate could provide an unused declared resource for one role.
    NoFeasibleSelection(RoleId),
    /// The bounded search exhausted its configured expansion budget.
    SearchLimited,
    /// Time arithmetic could not represent the requested scheduling window.
    InvalidTimeWindow,
}

impl Display for SchedulerError {
    /// Formats a scheduler boundary failure without claiming recovery exhaustion.
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::InvalidCandidateSet(reason) => {
                write!(formatter, "invalid scheduler candidate set: {reason}")
            }
            Self::UnknownCandidate(node_id) => {
                write!(
                    formatter,
                    "scheduler candidate {node_id} is absent from Shared State"
                )
            }
            Self::NoFeasibleSelection(role_id) => {
                write!(
                    formatter,
                    "no feasible deterministic selection for role {role_id}"
                )
            }
            Self::SearchLimited => formatter.write_str("joint scheduler search budget exhausted"),
            Self::InvalidTimeWindow => {
                formatter.write_str("joint scheduler time window is invalid")
            }
        }
    }
}

impl std::error::Error for SchedulerError {}

/// One Scheduler-selected role, node, and non-authoritative resource suggestion.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct RoleSchedulingSelection {
    /// Role receiving the selection.
    role_id: RoleId,
    /// Candidate node selected by the bounded joint policy.
    node_id: NodeId,
    /// Declared resources suggested for later proposal validation.
    resource_ids: Vec<ResourceId>,
    /// Minimum capacity required from each selected resource.
    resource_units: Vec<u32>,
}

impl RoleSchedulingSelection {
    /// Creates one internal role selection from the shared policy primitive.
    fn new(
        role_id: RoleId,
        node_id: NodeId,
        resource_ids: Vec<ResourceId>,
        resource_units: Vec<u32>,
    ) -> Self {
        Self {
            role_id,
            node_id,
            resource_ids,
            resource_units,
        }
    }

    /// Returns the selected role.
    pub const fn role_id(&self) -> &RoleId {
        &self.role_id
    }

    /// Returns the selected candidate node.
    pub const fn node_id(&self) -> &NodeId {
        &self.node_id
    }

    /// Returns non-authoritative proposed resource IDs.
    pub fn resource_ids(&self) -> &[ResourceId] {
        &self.resource_ids
    }

    /// Returns required units parallel to [`Self::resource_ids`].
    pub fn resource_units(&self) -> &[u32] {
        &self.resource_units
    }

    /// Converts selection evidence into the existing proposal-validation input type.
    fn to_role_assignment(&self) -> RoleAssignment {
        RoleAssignment::new(
            self.role_id.clone(),
            self.node_id.clone(),
            self.resource_ids.clone(),
        )
    }
}

/// Complete normal-task selection evidence produced before proposal validation.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct TaskSchedulingDecision {
    /// Mission-scoped task represented by the decision.
    task_ref: TaskRef,
    /// Role selections in TaskRequirement declaration order.
    selections: Vec<RoleSchedulingSelection>,
    /// Absolute Controller receive-time start selected by the scheduler.
    starts_at: TimestampMs,
    /// Planning-only end of the selected interval when duration is known.
    ends_at: Option<TimestampMs>,
    /// Inclusive latest Controller time at which this decision may activate.
    latest_activation_at: Option<TimestampMs>,
    /// Snapshot version on which this non-authoritative decision was based.
    snapshot_version: u64,
    /// Number of bounded search candidates examined.
    expansions: u32,
}

impl TaskSchedulingDecision {
    /// Creates a complete normal-task decision from deterministic role selections.
    fn new(
        task_ref: TaskRef,
        selections: Vec<RoleSchedulingSelection>,
        starts_at: TimestampMs,
        ends_at: Option<TimestampMs>,
        latest_activation_at: Option<TimestampMs>,
        snapshot_version: u64,
        expansions: u32,
    ) -> Self {
        Self {
            task_ref,
            selections,
            starts_at,
            ends_at,
            latest_activation_at,
            snapshot_version,
            expansions,
        }
    }

    /// Returns the mission-scoped task represented by this decision.
    pub const fn task_ref(&self) -> &TaskRef {
        &self.task_ref
    }

    /// Returns role selections in requirement declaration order.
    pub fn selections(&self) -> &[RoleSchedulingSelection] {
        &self.selections
    }

    /// Returns the selected absolute start time.
    pub const fn starts_at(&self) -> TimestampMs {
        self.starts_at
    }

    /// Returns the planning-only end time when estimated duration was declared.
    pub const fn ends_at(&self) -> Option<TimestampMs> {
        self.ends_at
    }

    /// Returns the inclusive latest time at which the selected decision may activate.
    pub const fn latest_activation_at(&self) -> Option<TimestampMs> {
        self.latest_activation_at
    }

    /// Returns the Control calendar version used to form this decision.
    pub const fn snapshot_version(&self) -> u64 {
        self.snapshot_version
    }

    /// Returns the number of candidate combinations examined.
    pub const fn expansions(&self) -> u32 {
        self.expansions
    }

    /// Builds fresh proposal-validation inputs without granting proposal authority.
    pub fn proposed_assignments(&self) -> Vec<RoleAssignment> {
        self.selections
            .iter()
            .map(RoleSchedulingSelection::to_role_assignment)
            .collect()
    }
}

/// Role-scoped recovery selection evidence produced before recovery proposal validation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RecoverySchedulingDecision {
    /// Existing Group awaiting recovery.
    group_id: ExecutionGroupId,
    /// Mission-scoped task retained by the Group.
    task_ref: TaskRef,
    /// Single unbound role selected in this decision.
    role_id: RoleId,
    /// Failed node excluded by Recovery Matching.
    previous_node_id: NodeId,
    /// Candidate replacement selected by the bounded deterministic policy.
    replacement_node_id: NodeId,
    /// Non-authoritative resources suggested for proposal validation.
    resource_ids: Vec<ResourceId>,
}

impl RecoverySchedulingDecision {
    /// Creates one recovery decision from the shared role-selection primitive.
    fn new(
        group_id: ExecutionGroupId,
        task_ref: TaskRef,
        role_id: RoleId,
        previous_node_id: NodeId,
        replacement_node_id: NodeId,
        resource_ids: Vec<ResourceId>,
    ) -> Self {
        Self {
            group_id,
            task_ref,
            role_id,
            previous_node_id,
            replacement_node_id,
            resource_ids,
        }
    }

    /// Returns the existing Group awaiting the replacement.
    pub const fn group_id(&self) -> &ExecutionGroupId {
        &self.group_id
    }

    /// Returns the mission-scoped task retained by the Group.
    pub const fn task_ref(&self) -> &TaskRef {
        &self.task_ref
    }

    /// Returns the single recovery role represented by this decision.
    pub const fn role_id(&self) -> &RoleId {
        &self.role_id
    }

    /// Returns the failed node excluded from selection.
    pub const fn previous_node_id(&self) -> &NodeId {
        &self.previous_node_id
    }

    /// Returns the Scheduler-selected replacement node.
    pub const fn replacement_node_id(&self) -> &NodeId {
        &self.replacement_node_id
    }

    /// Returns non-authoritative proposed replacement resources.
    pub fn resource_ids(&self) -> &[ResourceId] {
        &self.resource_ids
    }
}

/// Recovery scheduling result that preserves empty candidates as non-terminal pending work.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RecoverySchedulingOutcome {
    /// No supplied candidate can currently form a deterministic selection.
    NoSelection,
    /// The bounded policy selected one recovery candidate and resources.
    Selected(RecoverySchedulingDecision),
}

/// One existing Control commitment visible to scheduling without granting mutation authority.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SchedulingOccupancy {
    /// Resource whose capacity is occupied.
    resource_id: ResourceId,
    /// Inclusive start of the occupied interval.
    starts_at: TimestampMs,
    /// Exclusive planning end; `None` means physical occupancy has no proven end.
    ends_at: Option<TimestampMs>,
}

impl SchedulingOccupancy {
    /// Creates one immutable scheduling occupancy interval.
    pub const fn new(
        resource_id: ResourceId,
        starts_at: TimestampMs,
        ends_at: Option<TimestampMs>,
    ) -> Self {
        Self {
            resource_id,
            starts_at,
            ends_at,
        }
    }

    /// Returns the occupied resource identity.
    pub const fn resource_id(&self) -> &ResourceId {
        &self.resource_id
    }

    /// Returns the occupied interval start.
    pub const fn starts_at(&self) -> TimestampMs {
        self.starts_at
    }

    /// Returns the exclusive planning end, if known.
    pub const fn ends_at(&self) -> Option<TimestampMs> {
        self.ends_at
    }
}

/// Immutable Control calendar input consumed by the stateless Scheduler.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SchedulingSnapshot {
    /// Monotonic Control calendar version.
    version: u64,
    /// Existing active and future resource intervals.
    occupancies: Vec<SchedulingOccupancy>,
    /// Existing ContextRole bindings that this Task must reuse exactly.
    role_constraints: BTreeMap<RoleId, SchedulingRoleConstraint>,
}

impl SchedulingSnapshot {
    /// Creates an immutable snapshot in deterministic occupancy order.
    pub fn new(version: u64, mut occupancies: Vec<SchedulingOccupancy>) -> Self {
        occupancies.sort_by(|left, right| {
            (left.resource_id(), left.starts_at(), left.ends_at()).cmp(&(
                right.resource_id(),
                right.starts_at(),
                right.ends_at(),
            ))
        });
        Self {
            version,
            occupancies,
            role_constraints: BTreeMap::new(),
        }
    }

    /// Returns the empty initial Control calendar.
    pub const fn empty() -> Self {
        Self {
            version: 0,
            occupancies: Vec::new(),
            role_constraints: BTreeMap::new(),
        }
    }

    /// Attaches exact Control-owned ContextRole bindings for one Task decision.
    pub fn with_role_constraints(
        mut self,
        constraints: impl IntoIterator<Item = SchedulingRoleConstraint>,
    ) -> Self {
        self.role_constraints = constraints
            .into_iter()
            .map(|constraint| (constraint.role_id().clone(), constraint))
            .collect();
        self
    }

    /// Returns the Control version represented by this snapshot.
    pub const fn version(&self) -> u64 {
        self.version
    }

    /// Returns all immutable occupied intervals.
    pub fn occupancies(&self) -> &[SchedulingOccupancy] {
        &self.occupancies
    }

    /// Returns an exact reusable binding constraint for one Task-local Role.
    pub fn role_constraint(&self, role_id: &RoleId) -> Option<&SchedulingRoleConstraint> {
        self.role_constraints.get(role_id)
    }
}

/// Exact Context-owned node/resource binding that a later Task must reuse.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SchedulingRoleConstraint {
    /// Task-local Role receiving the existing Context binding.
    role_id: RoleId,
    /// Existing physical Node owner.
    node_id: NodeId,
    /// Existing resource identities retained by the Context.
    resource_ids: Vec<ResourceId>,
}

impl SchedulingRoleConstraint {
    /// Creates one exact reuse constraint projected from Control authority.
    pub const fn new(role_id: RoleId, node_id: NodeId, resource_ids: Vec<ResourceId>) -> Self {
        Self {
            role_id,
            node_id,
            resource_ids,
        }
    }

    /// Returns the Task-local Role constrained by the binding.
    pub const fn role_id(&self) -> &RoleId {
        &self.role_id
    }

    /// Returns the existing Node owner that must be reused.
    pub const fn node_id(&self) -> &NodeId {
        &self.node_id
    }

    /// Returns the existing Context-owned resources that must be reused.
    pub fn resource_ids(&self) -> &[ResourceId] {
        &self.resource_ids
    }
}

/// Result of scheduling one Ready Task against current and future occupancy.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TaskSchedulingOutcome {
    /// The Task can commit and activate in the current application tick.
    SelectedNow(TaskSchedulingDecision),
    /// The Task has a future Control reservation candidate.
    SelectedFuture(TaskSchedulingDecision),
    /// The current calendar contains no feasible interval within the declared window.
    Deferred,
    /// The latest start or completion deadline has already been missed.
    WindowMissed,
}

/// Default bounded expansion count for deterministic joint search.
pub const DEFAULT_SEARCH_EXPANSIONS: u32 = 10_000;

#[cfg(test)]
#[path = "tests/mod.rs"]
mod tests;
