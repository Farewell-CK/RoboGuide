//! Deterministic bounded joint policy for Capability, Compute, Space, and Time.
//!
//! The policy consumes Candidate Sets produced by Capability Matching and
//! returns selection evidence. It does not re-evaluate eligibility, validate
//! proposals, inspect reservations, commit resources, or mutate Groups/State.

use super::{CandidateSet, RecoveryCandidateSet};
use domain::{
    CorrelationId, EventPayload, ExecutionGroupId, NodeId, ResourceId, RoleAssignment, RoleId,
    RoleRequirement, TaskDurationEstimate, TaskRef, TaskRequirement, TimestampMs,
};
use ports::{EventSink, SharedNodeStateReader};
use std::collections::{BTreeMap, BTreeSet};
use std::fmt::{Display, Formatter};

pub(crate) mod capacity;
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
    /// Supplied duration evidence was stale or belonged to another Task.
    InvalidDurationEstimate(String),
    /// Supplied load/cost evidence is malformed, stale, or internally inconsistent.
    InvalidCostEvidence(String),
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
            Self::InvalidDurationEstimate(reason) => {
                write!(formatter, "invalid task duration estimate: {reason}")
            }
            Self::InvalidCostEvidence(reason) => {
                write!(formatter, "invalid scheduler cost evidence: {reason}")
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
    /// Exact typed resource semantics, present only for quantitative selections.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    resource_requirements: Vec<domain::ResourceRequirement>,
    /// Original selected capacities, parallel to typed requirements and never current facts.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    resource_capacities: Vec<u32>,
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
            resource_requirements: Vec::new(),
            resource_capacities: Vec::new(),
        }
    }

    /// Freezes explicit shared semantics and original selected capacities without changing legacy bytes.
    fn with_resources(
        mut self,
        role: &RoleRequirement,
        registration: &domain::NodeRegistration,
    ) -> Self {
        if role
            .resource_requirements()
            .iter()
            .any(|demand| demand.allocation_mode() == domain::ResourceAllocationMode::Divisible)
        {
            self.resource_requirements = role.resource_requirements().to_vec();
            self.resource_capacities = self
                .resource_ids
                .iter()
                .map(|id| {
                    registration
                        .resources()
                        .iter()
                        .find(|resource| resource.id() == id)
                        .expect("selected resource exists")
                        .capacity()
                })
                .collect();
        }
        self
    }

    /// Returns original typed demands; historical exclusive selections carry no new authority.
    pub fn resource_requirements(&self) -> &[domain::ResourceRequirement] {
        &self.resource_requirements
    }

    /// Returns original source capacities for offline history validation, never fresh capacity.
    pub fn resource_capacities(&self) -> &[u32] {
        &self.resource_capacities
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

/// Non-authoritative temporal context supplied to one Task scheduling decision.
#[derive(Debug, Clone, Copy)]
pub struct TaskSchedulingContext<'a> {
    /// RoboGuide-local time at which Orchestration accepted the Mission.
    mission_accepted_at: TimestampMs,
    /// RoboGuide-local time at which this scheduling decision is evaluated.
    now: TimestampMs,
    /// Optional source-aware duration evidence admitted for this decision.
    duration_estimate: Option<&'a TaskDurationEstimate>,
}

impl<'a> TaskSchedulingContext<'a> {
    /// Creates one immutable scheduling context without granting estimate authority.
    pub const fn new(
        mission_accepted_at: TimestampMs,
        now: TimestampMs,
        duration_estimate: Option<&'a TaskDurationEstimate>,
    ) -> Self {
        Self {
            mission_accepted_at,
            now,
            duration_estimate,
        }
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
    /// Explicit attributed duration evidence consumed by this decision, when supplied.
    #[serde(default)]
    duration_estimate: Option<TaskDurationEstimate>,
    /// Fresh cost evidence retained as planning provenance; it never grants resource authority.
    #[serde(default)]
    cost_evidence: Vec<SchedulingCostEvidence>,
    /// Original authorized current State costs and explicit unavailable results.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    policy_cost_evidence: Vec<domain::SchedulingCostAssessment>,
}

impl TaskSchedulingDecision {
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

    /// Returns explicit source-aware duration evidence retained with the decision.
    pub const fn duration_estimate(&self) -> Option<&TaskDurationEstimate> {
        self.duration_estimate.as_ref()
    }

    /// Returns the exact fresh cost evidence used only to order candidate exploration.
    pub fn cost_evidence(&self) -> &[SchedulingCostEvidence] {
        &self.cost_evidence
    }

    /// Returns complete original policy-based cost observations, including unknown/stale outcomes.
    pub fn policy_cost_evidence(&self) -> &[domain::SchedulingCostAssessment] {
        &self.policy_cost_evidence
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
    /// Original policy-cost observations used for this recovery selection.
    policy_cost_evidence: Vec<domain::SchedulingCostAssessment>,
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
            policy_cost_evidence: Vec::new(),
        }
    }

    /// Attaches exact planning-only source evidence after bounded recovery selection.
    fn with_policy_cost_evidence(
        mut self,
        evidence: Vec<domain::SchedulingCostAssessment>,
    ) -> Self {
        self.policy_cost_evidence = evidence;
        self
    }

    /// Returns original source assessments without claiming Commit or physical recovery.
    pub fn policy_cost_evidence(&self) -> &[domain::SchedulingCostAssessment] {
        &self.policy_cost_evidence
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
    /// Positive units for shared occupancy; absence retains whole-resource exclusion.
    pub(crate) units: Option<u32>,
    /// Typed original Context borrower axis; absence is the actual physical resource axis.
    pub(crate) owner_axis: Option<String>,
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
            units: None,
            owner_axis: None,
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
    /// Explicit Context parent limits and their independently counted borrower axes.
    pub(crate) context_capacities: BTreeMap<RoleId, capacity::ContextCapacity>,
    /// Existing ContextRole bindings that this Task must reuse exactly.
    pub(crate) role_constraints: BTreeMap<RoleId, SchedulingRoleConstraint>,
    /// Fresh source-aware cost evidence, keyed by candidate Node.
    cost_evidence: BTreeMap<NodeId, SchedulingCostEvidence>,
    /// Exact deployment-authorized observations, including unavailable evidence.
    policy_cost_evidence: BTreeMap<NodeId, domain::SchedulingCostAssessment>,
}

/// Bounded source-aware scheduling cost evidence used only for deterministic candidate ordering.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SchedulingCostEvidence {
    /// Candidate Node receiving this evidence.
    pub node_id: NodeId,
    /// Stable provider/source identity.
    pub source_id: String,
    /// Controller receive time of this evidence.
    pub received_at: TimestampMs,
    /// Receive-relative evidence lifetime.
    pub valid_for_ms: u64,
    /// Queue delay observed by the source.
    pub queue_delay_ms: u64,
    /// Travel estimate in bounded micrometers.
    pub travel_distance_um: u64,
    /// Traffic delay estimate in milliseconds.
    pub traffic_delay_ms: u64,
    /// Source load in millionths of capacity.
    pub load_millionths: u32,
    /// Available GPU memory in bytes, when the source observed one.
    pub gpu_memory_bytes: Option<u64>,
}

impl SchedulingCostEvidence {
    /// Validates bounded source identity and receive-relative metrics without using wall clocks.
    pub fn validate_at(&self, now: TimestampMs) -> Result<(), SchedulerError> {
        if self.source_id.trim().is_empty()
            || self.source_id.len() > 256
            || self.valid_for_ms == 0
            || self.valid_for_ms > 3_600_000
            || self.load_millionths > 1_000_000
            || self.queue_delay_ms > 86_400_000
            || self.travel_distance_um > 10_000_000_000
            || self.traffic_delay_ms > 86_400_000
            || self.gpu_memory_bytes.is_some_and(|value| value > 1 << 50)
            || now.as_millis().saturating_sub(self.received_at.as_millis()) > self.valid_for_ms
            || self.received_at.as_millis() > now.as_millis()
        {
            return Err(SchedulerError::InvalidCostEvidence(
                "source, freshness or metric bounds are invalid".into(),
            ));
        }
        Ok(())
    }

    /// Computes one deterministic integer score; lower is preferred and ties use NodeId.
    pub fn score(&self) -> u128 {
        u128::from(self.queue_delay_ms) * 1_000
            + u128::from(self.travel_distance_um)
            + u128::from(self.traffic_delay_ms) * 1_000
            + u128::from(self.load_millionths)
            + u128::from(((1_u64 << 50) - self.gpu_memory_bytes.unwrap_or(0)) / (1024 * 1024))
    }
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
            context_capacities: BTreeMap::new(),
            cost_evidence: BTreeMap::new(),
            policy_cost_evidence: BTreeMap::new(),
        }
    }

    /// Returns the empty initial Control calendar.
    pub const fn empty() -> Self {
        Self {
            version: 0,
            occupancies: Vec::new(),
            role_constraints: BTreeMap::new(),
            context_capacities: BTreeMap::new(),
            cost_evidence: BTreeMap::new(),
            policy_cost_evidence: BTreeMap::new(),
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

    /// Adds fresh evidence in deterministic Node order; stale evidence is rejected before use.
    pub fn try_with_cost_evidence(
        mut self,
        now: TimestampMs,
        evidence: impl IntoIterator<Item = SchedulingCostEvidence>,
    ) -> Result<Self, SchedulerError> {
        for item in evidence {
            item.validate_at(now)?;
            if self
                .cost_evidence
                .insert(item.node_id.clone(), item)
                .is_some()
            {
                return Err(SchedulerError::InvalidCostEvidence(
                    "duplicate Node cost evidence".into(),
                ));
            }
        }
        Ok(self)
    }

    /// Adds complete policy observations without substituting unavailable values or changing eligibility.
    pub fn with_policy_cost_evidence(
        mut self,
        evidence: Vec<domain::SchedulingCostAssessment>,
    ) -> Result<Self, SchedulerError> {
        for receipt in evidence {
            receipt
                .validate()
                .map_err(SchedulerError::InvalidCostEvidence)?;
            if self
                .policy_cost_evidence
                .insert(receipt.policy.node_id.clone(), receipt)
                .is_some()
            {
                return Err(SchedulerError::InvalidCostEvidence(
                    "duplicate candidate cost policy".into(),
                ));
            }
        }
        Ok(self)
    }

    /// Returns original policy-based observations in deterministic candidate order.
    pub fn policy_cost_evidence(&self) -> impl Iterator<Item = &domain::SchedulingCostAssessment> {
        self.policy_cost_evidence.values()
    }

    /// Ranks measured costs before unknown; explicit unknown policy never borrows compatibility scores.
    pub fn candidate_cost(&self, node: &NodeId, now: TimestampMs) -> u128 {
        if let Some(receipt) = self.policy_cost_evidence.get(node) {
            return receipt
                .record
                .as_ref()
                .filter(|record| {
                    receipt.decided_at <= now
                        && !record.is_stale_at(now)
                        && now
                            .as_millis()
                            .saturating_sub(record.received_at().as_millis())
                            <= receipt.policy.policy.max_age_ms
                })
                .and(receipt.score)
                .map_or(u128::MAX, u128::from);
        }
        self.cost_evidence
            .get(node)
            .filter(|cost| cost.validate_at(now).is_ok())
            .map_or(u128::MAX, SchedulingCostEvidence::score)
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

    /// Returns fresh cost evidence for one candidate, if supplied.
    pub fn cost_for_node(&self, node_id: &NodeId) -> Option<&SchedulingCostEvidence> {
        self.cost_evidence.get(node_id)
    }

    /// Returns all cost evidence retained in the snapshot for audit provenance.
    pub fn cost_evidence(&self) -> impl Iterator<Item = &SchedulingCostEvidence> {
        self.cost_evidence.values()
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
