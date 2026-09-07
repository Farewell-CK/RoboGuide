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

/// Stateless bounded joint Scheduler over supplied candidates and Control calendar snapshots.
#[derive(Debug, Clone, Copy)]
pub struct BoundedJointScheduler {
    /// Maximum candidate combinations examined by one decision.
    max_expansions: u32,
}

impl Default for BoundedJointScheduler {
    /// Creates the same bounded policy as [`Self::new`].
    fn default() -> Self {
        Self::new()
    }
}

impl BoundedJointScheduler {
    /// Creates the default deterministic bounded joint policy.
    pub const fn new() -> Self {
        Self {
            max_expansions: DEFAULT_SEARCH_EXPANSIONS,
        }
    }

    /// Creates a deterministic policy with an explicit positive expansion budget.
    pub const fn with_max_expansions(max_expansions: u32) -> Self {
        Self {
            max_expansions: if max_expansions == 0 {
                1
            } else {
                max_expansions
            },
        }
    }

    /// Selects every normal task role without validating or committing a proposal.
    pub fn schedule_task<S: SharedNodeStateReader, E: EventSink>(
        &self,
        state: &S,
        requirement: &TaskRequirement,
        candidates: &CandidateSet,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<TaskSchedulingDecision, SchedulerError> {
        if candidates.task_ref() != requirement.task_ref() {
            return Err(SchedulerError::InvalidCandidateSet(
                "normal candidates belong to another task".to_string(),
            ));
        }
        let outcome = self.schedule_task_with_snapshot(
            state,
            requirement,
            candidates,
            &SchedulingSnapshot::empty(),
            timestamp,
            timestamp,
        )?;
        let decision = match outcome {
            TaskSchedulingOutcome::SelectedNow(decision)
            | TaskSchedulingOutcome::SelectedFuture(decision) => decision,
            TaskSchedulingOutcome::Deferred | TaskSchedulingOutcome::WindowMissed => {
                return Err(SchedulerError::NoFeasibleSelection(
                    requirement.roles()[0].role_id().clone(),
                ));
            }
        };
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::TaskSchedulingSelected {
                task_ref: requirement.task_ref().clone(),
                assignments: decision.proposed_assignments(),
            },
        );
        Ok(decision)
    }

    /// Jointly selects every Role, resource set, and earliest feasible time interval.
    pub fn schedule_task_with_snapshot<S: SharedNodeStateReader>(
        &self,
        state: &S,
        requirement: &TaskRequirement,
        candidates: &CandidateSet,
        snapshot: &SchedulingSnapshot,
        mission_accepted_at: TimestampMs,
        now: TimestampMs,
    ) -> Result<TaskSchedulingOutcome, SchedulerError> {
        validate_candidate_set(requirement, candidates)?;
        let timing = requirement.timing();
        let earliest =
            checked_timestamp_add(mission_accepted_at, timing.earliest_start_offset_ms())?;
        let earliest = TimestampMs::new(earliest.as_millis().max(now.as_millis()));
        let latest = timing
            .latest_start_offset_ms()
            .map(|offset| checked_timestamp_add(mission_accepted_at, offset))
            .transpose()
            .map_err(|_| SchedulerError::InvalidTimeWindow)?;
        let completion_deadline = timing
            .completion_deadline_offset_ms()
            .map(|offset| checked_timestamp_add(mission_accepted_at, offset))
            .transpose()
            .map_err(|_| SchedulerError::InvalidTimeWindow)?;
        let completion_start_deadline = completion_deadline.map(|deadline| {
            let duration = timing
                .estimated_duration_ms()
                .expect("TaskTiming requires duration with completion deadline");
            TimestampMs::new(deadline.as_millis() - duration)
        });
        let latest_activation_at = match (latest, completion_start_deadline) {
            (Some(latest), Some(completion)) => Some(latest.min(completion)),
            (Some(latest), None) => Some(latest),
            (None, Some(completion)) => Some(completion),
            (None, None) => None,
        };
        if latest_activation_at.is_some_and(|latest| earliest > latest) {
            return Ok(TaskSchedulingOutcome::WindowMissed);
        }
        let mut starts = vec![earliest];
        if timing.estimated_duration_ms().is_some() {
            starts.extend(
                snapshot
                    .occupancies()
                    .iter()
                    .filter_map(SchedulingOccupancy::ends_at),
            );
        }
        starts.sort();
        starts.dedup();
        let mut expansions = 0;
        for starts_at in starts.into_iter().filter(|start| *start >= earliest) {
            if latest_activation_at.is_some_and(|latest| starts_at > latest) {
                break;
            }
            let ends_at = timing
                .estimated_duration_ms()
                .map(|duration| checked_timestamp_add(starts_at, duration))
                .transpose()
                .map_err(|_| SchedulerError::InvalidTimeWindow)?;
            if let Some(deadline) = completion_deadline
                && ends_at.unwrap_or(starts_at) > deadline
            {
                continue;
            }
            let mut selections = Vec::new();
            let mut selected = BTreeSet::new();
            if search_roles(
                state,
                requirement,
                candidates,
                snapshot,
                starts_at,
                ends_at,
                0,
                &mut selections,
                &mut selected,
                &mut expansions,
                self.max_expansions,
            )? {
                if starts_at > now && ends_at.is_none() {
                    return Ok(TaskSchedulingOutcome::Deferred);
                }
                let decision = TaskSchedulingDecision::new(
                    requirement.task_ref().clone(),
                    selections,
                    starts_at,
                    ends_at,
                    latest_activation_at,
                    snapshot.version(),
                    expansions,
                );
                return Ok(if starts_at <= now {
                    TaskSchedulingOutcome::SelectedNow(decision)
                } else {
                    TaskSchedulingOutcome::SelectedFuture(decision)
                });
            }
        }
        Ok(TaskSchedulingOutcome::Deferred)
    }

    /// Selects only the role represented by a Recovery Candidate Set.
    #[allow(clippy::too_many_arguments)]
    pub fn schedule_recovery<S: SharedNodeStateReader, E: EventSink>(
        &self,
        state: &S,
        requirement: &TaskRequirement,
        candidates: &RecoveryCandidateSet,
        snapshot: &SchedulingSnapshot,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<RecoverySchedulingOutcome, SchedulerError> {
        if candidates.task_ref() != requirement.task_ref() {
            return Err(SchedulerError::InvalidCandidateSet(
                "recovery candidates belong to another task".to_string(),
            ));
        }
        let role = requirement
            .roles()
            .iter()
            .find(|role| role.role_id() == candidates.role_id())
            .ok_or_else(|| {
                SchedulerError::InvalidCandidateSet(format!(
                    "task requirement omits recovery role {}",
                    candidates.role_id()
                ))
            })?;
        let selection = select_role(
            state,
            role,
            candidates.candidate_node_ids(),
            snapshot,
            timestamp,
            &BTreeSet::new(),
        )?;
        let Some(selection) = selection else {
            events.append(
                timestamp,
                correlation_id,
                None,
                EventPayload::RecoverySchedulingNoSelection {
                    group_id: candidates.group_id().clone(),
                    task_ref: candidates.task_ref().clone(),
                    role_id: candidates.role_id().clone(),
                },
            );
            return Ok(RecoverySchedulingOutcome::NoSelection);
        };
        let decision = RecoverySchedulingDecision::new(
            candidates.group_id().clone(),
            candidates.task_ref().clone(),
            candidates.role_id().clone(),
            candidates.previous_node_id().clone(),
            selection.node_id().clone(),
            selection.resource_ids().to_vec(),
        );
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::RecoverySchedulingSelected {
                group_id: candidates.group_id().clone(),
                task_ref: candidates.task_ref().clone(),
                role_id: candidates.role_id().clone(),
                previous_node_id: candidates.previous_node_id().clone(),
                replacement_node_id: decision.replacement_node_id().clone(),
                resource_ids: decision.resource_ids().to_vec(),
            },
        );
        Ok(RecoverySchedulingOutcome::Selected(decision))
    }
}

/// Validates exact CandidateSet coverage before search begins.
fn validate_candidate_set(
    requirement: &TaskRequirement,
    candidates: &CandidateSet,
) -> Result<(), SchedulerError> {
    if candidates.task_ref() != requirement.task_ref() {
        return Err(SchedulerError::InvalidCandidateSet(
            "normal candidates belong to another task".to_string(),
        ));
    }
    let candidate_roles = candidates
        .roles()
        .iter()
        .map(|role| role.role_id())
        .collect::<BTreeSet<_>>();
    let required_roles = requirement
        .roles()
        .iter()
        .map(RoleRequirement::role_id)
        .collect::<BTreeSet<_>>();
    if candidate_roles != required_roles || candidate_roles.len() != candidates.roles().len() {
        return Err(SchedulerError::InvalidCandidateSet(
            "normal candidates must exactly and uniquely cover Task roles".to_string(),
        ));
    }
    Ok(())
}

/// Performs deterministic bounded backtracking across all Role and resource combinations.
#[allow(clippy::too_many_arguments)]
fn search_roles<S: SharedNodeStateReader>(
    state: &S,
    requirement: &TaskRequirement,
    candidates: &CandidateSet,
    snapshot: &SchedulingSnapshot,
    starts_at: TimestampMs,
    ends_at: Option<TimestampMs>,
    role_index: usize,
    selections: &mut Vec<RoleSchedulingSelection>,
    selected_resources: &mut BTreeSet<ResourceId>,
    expansions: &mut u32,
    limit: u32,
) -> Result<bool, SchedulerError> {
    if role_index == requirement.roles().len() {
        return Ok(true);
    }
    let role = &requirement.roles()[role_index];
    let role_candidates = candidates
        .for_role(role.role_id())
        .expect("validated above");
    let mut nodes = role_candidates.node_ids().to_vec();
    nodes.sort();
    nodes.dedup();
    for node_id in nodes {
        let constraint = snapshot.role_constraint(role.role_id());
        if constraint.is_some_and(|constraint| constraint.node_id() != &node_id) {
            continue;
        }
        let node = state
            .node(&node_id)
            .ok_or_else(|| SchedulerError::UnknownCandidate(node_id.clone()))?;
        let choices = resource_choices(
            node.registration(),
            role,
            snapshot,
            starts_at,
            ends_at,
            selected_resources,
            constraint,
        );
        for (resource_ids, resource_units) in choices {
            *expansions = expansions
                .checked_add(1)
                .ok_or(SchedulerError::SearchLimited)?;
            if *expansions > limit {
                return Err(SchedulerError::SearchLimited);
            }
            selected_resources.extend(resource_ids.iter().cloned());
            selections.push(RoleSchedulingSelection::new(
                role.role_id().clone(),
                node_id.clone(),
                resource_ids.clone(),
                resource_units,
            ));
            if search_roles(
                state,
                requirement,
                candidates,
                snapshot,
                starts_at,
                ends_at,
                role_index + 1,
                selections,
                selected_resources,
                expansions,
                limit,
            )? {
                return Ok(true);
            }
            selections.pop();
            for resource_id in resource_ids {
                selected_resources.remove(&resource_id);
            }
        }
    }
    Ok(false)
}

/// Enumerates deterministic resource combinations for one node and Role.
fn resource_choices(
    registration: &domain::NodeRegistration,
    role: &RoleRequirement,
    snapshot: &SchedulingSnapshot,
    starts_at: TimestampMs,
    ends_at: Option<TimestampMs>,
    selected_resources: &BTreeSet<ResourceId>,
    constraint: Option<&SchedulingRoleConstraint>,
) -> Vec<(Vec<ResourceId>, Vec<u32>)> {
    let demands = role.resource_requirements();
    if demands.is_empty() {
        return vec![(Vec::new(), Vec::new())];
    }
    if constraint.is_some_and(|constraint| constraint.resource_ids().len() != demands.len()) {
        return Vec::new();
    }
    let mut combinations = vec![(Vec::new(), Vec::new())];
    for demand in demands.iter() {
        let mut resources = registration
            .resources()
            .iter()
            .filter(|resource| {
                resource.kind() == demand.kind() && resource.capacity() >= demand.units()
            })
            .filter(|resource| {
                constraint
                    .is_none_or(|constraint| constraint.resource_ids().contains(resource.id()))
            })
            .filter(|resource| !selected_resources.contains(resource.id()))
            .filter(|resource| resource_available(snapshot, resource.id(), starts_at, ends_at))
            .collect::<Vec<_>>();
        resources.sort_by_key(|resource| resource.id());
        let mut next = Vec::new();
        for (ids, units) in combinations {
            for resource in &resources {
                if ids.contains(resource.id()) {
                    continue;
                }
                let mut next_ids = ids.clone();
                let mut next_units = units.clone();
                next_ids.push(resource.id().clone());
                next_units.push(demand.units());
                next.push((next_ids, next_units));
            }
        }
        combinations = next;
    }
    if let Some(constraint) = constraint {
        combinations.retain(|(ids, _)| ids == constraint.resource_ids());
    }
    combinations
}

/// Tests half-open interval availability against active and future commitments.
fn resource_available(
    snapshot: &SchedulingSnapshot,
    resource_id: &ResourceId,
    starts_at: TimestampMs,
    ends_at: Option<TimestampMs>,
) -> bool {
    snapshot
        .occupancies()
        .iter()
        .filter(|item| item.resource_id() == resource_id)
        .all(|item| match (ends_at, item.ends_at()) {
            (Some(end), Some(existing_end)) => end <= item.starts_at() || existing_end <= starts_at,
            (Some(end), None) => end <= item.starts_at(),
            (None, Some(existing_end)) => existing_end <= starts_at,
            (None, None) => false,
        })
}

/// Adds one duration to a timestamp without saturating a scheduling boundary.
fn checked_timestamp_add(
    timestamp: TimestampMs,
    duration_ms: u64,
) -> Result<TimestampMs, SchedulerError> {
    timestamp
        .as_millis()
        .checked_add(duration_ms)
        .map(TimestampMs::new)
        .ok_or(SchedulerError::InvalidTimeWindow)
}

/// Applies the shared stable-first node/resource policy to one role and Candidate Set.
fn select_role<S: SharedNodeStateReader>(
    state: &S,
    role: &RoleRequirement,
    candidate_node_ids: &[NodeId],
    snapshot: &SchedulingSnapshot,
    starts_at: TimestampMs,
    selected_resources: &BTreeSet<ResourceId>,
) -> Result<Option<RoleSchedulingSelection>, SchedulerError> {
    let mut stable_candidates = candidate_node_ids.to_vec();
    stable_candidates.sort();
    stable_candidates.dedup();
    for node_id in stable_candidates {
        let node = state
            .node(&node_id)
            .ok_or_else(|| SchedulerError::UnknownCandidate(node_id.clone()))?;
        let Some((resource_ids, resource_units)) = resource_choices(
            node.registration(),
            role,
            snapshot,
            starts_at,
            None,
            selected_resources,
            None,
        )
        .into_iter()
        .next() else {
            continue;
        };
        return Ok(Some(RoleSchedulingSelection::new(
            role.role_id().clone(),
            node_id,
            resource_ids,
            resource_units,
        )));
    }
    Ok(None)
}

#[cfg(test)]
#[path = "scheduler_tests.rs"]
mod tests;
