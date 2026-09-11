//! Deterministic bounded joint scheduling policy implementation.

use super::*;

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
        self.schedule_task_with_snapshot_and_estimate(
            state,
            requirement,
            candidates,
            snapshot,
            TaskSchedulingContext::new(mission_accepted_at, now, None),
        )
    }

    /// Jointly schedules with explicit attributed duration evidence outside the MissionPlan.
    pub fn schedule_task_with_snapshot_and_estimate<S: SharedNodeStateReader>(
        &self,
        state: &S,
        requirement: &TaskRequirement,
        candidates: &CandidateSet,
        snapshot: &SchedulingSnapshot,
        context: TaskSchedulingContext<'_>,
    ) -> Result<TaskSchedulingOutcome, SchedulerError> {
        validate_candidate_set(requirement, candidates)?;
        let explicit_estimate =
            validate_duration_estimate(requirement, context.duration_estimate, context.now)?;
        let duration_ms = explicit_estimate
            .map(TaskDurationEstimate::duration_ms)
            .or_else(|| requirement.timing().estimated_duration_ms());
        let timing = requirement.timing();
        let earliest = checked_timestamp_add(
            context.mission_accepted_at,
            timing.earliest_start_offset_ms(),
        )?;
        let earliest = TimestampMs::new(earliest.as_millis().max(context.now.as_millis()));
        let latest = timing
            .latest_start_offset_ms()
            .map(|offset| checked_timestamp_add(context.mission_accepted_at, offset))
            .transpose()
            .map_err(|_| SchedulerError::InvalidTimeWindow)?;
        let completion_deadline = timing
            .completion_deadline_offset_ms()
            .map(|offset| checked_timestamp_add(context.mission_accepted_at, offset))
            .transpose()
            .map_err(|_| SchedulerError::InvalidTimeWindow)?;
        let completion_start_deadline = completion_deadline.map(|deadline| {
            duration_ms.map_or(deadline, |duration| {
                TimestampMs::new(deadline.as_millis() - duration)
            })
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
        if duration_ms.is_some() {
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
            let ends_at = duration_ms
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
                if starts_at > context.now && ends_at.is_none() {
                    return Ok(TaskSchedulingOutcome::Deferred);
                }
                let decision = TaskSchedulingDecision {
                    task_ref: requirement.task_ref().clone(),
                    selections,
                    starts_at,
                    ends_at,
                    latest_activation_at,
                    snapshot_version: snapshot.version(),
                    expansions,
                    duration_estimate: explicit_estimate.cloned(),
                };
                return Ok(if starts_at <= context.now {
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

/// Validates task identity and receive-time freshness before scheduling consumes an estimate.
fn validate_duration_estimate<'a>(
    requirement: &TaskRequirement,
    estimate: Option<&'a TaskDurationEstimate>,
    now: TimestampMs,
) -> Result<Option<&'a TaskDurationEstimate>, SchedulerError> {
    let Some(estimate) = estimate else {
        return Ok(None);
    };
    if estimate.task_ref() != requirement.task_ref() {
        return Err(SchedulerError::InvalidDurationEstimate(
            "evidence belongs to another Task".to_string(),
        ));
    }
    if !estimate.is_fresh_at(now) {
        return Err(SchedulerError::InvalidDurationEstimate(
            "evidence is stale or from the future".to_string(),
        ));
    }
    Ok(Some(estimate))
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
