//! Control-owned future reservation calendar and versioned Scheduler snapshots.

use crate::{
    CandidateSet, ControlError, ControlPlane, ExecutionGroup, SchedulingOccupancy,
    SchedulingRoleConstraint, SchedulingSnapshot, TaskSchedulingDecision,
};
use domain::{ExecutionGroupId, MissionId, ResourceBindingScope, ResourceId, TaskRef, TimestampMs};
use ports::SharedNodeStateReader;
use std::collections::BTreeSet;

/// Lifecycle of one future scheduling commitment before physical execution begins.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum SchedulingReservationPhase {
    /// The Ready Task owns a future interval but has no physical attempt.
    Scheduled,
    /// The Task has activated and the interval remains planning evidence until terminal release.
    Activated,
    /// The reservation was invalidated and remains observable until replanning replaces it.
    Invalidated,
}

/// Durable Control commitment for one Ready Task's future joint decision.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct ScheduledTaskReservation {
    /// Mission-level Group that will own the Task binding.
    group_id: ExecutionGroupId,
    /// Complete deterministic joint decision retained for activation revalidation.
    decision: TaskSchedulingDecision,
    /// Current future-reservation lifecycle.
    phase: SchedulingReservationPhase,
    /// Stable explanation when the reservation was invalidated.
    reason: String,
}

impl ScheduledTaskReservation {
    /// Creates a live future reservation from one current Scheduler decision.
    fn new(group_id: ExecutionGroupId, decision: TaskSchedulingDecision) -> Self {
        Self {
            group_id,
            decision,
            phase: SchedulingReservationPhase::Scheduled,
            reason: String::new(),
        }
    }

    /// Returns the Group that will own the Task.
    pub const fn group_id(&self) -> &ExecutionGroupId {
        &self.group_id
    }

    /// Returns the mission-scoped scheduled Task.
    pub const fn task_ref(&self) -> &TaskRef {
        self.decision.task_ref()
    }

    /// Returns the immutable joint decision retained for activation.
    pub const fn decision(&self) -> &TaskSchedulingDecision {
        &self.decision
    }

    /// Returns the future reservation lifecycle.
    pub const fn phase(&self) -> SchedulingReservationPhase {
        self.phase
    }

    /// Returns the invalidation explanation, when present.
    pub fn reason(&self) -> &str {
        &self.reason
    }
}

impl ControlPlane {
    /// Validates restored calendar identity, interval, lifecycle, and exclusivity invariants.
    pub(crate) fn validate_scheduling_checkpoint_authority(&self) -> Result<(), ControlError> {
        if !self.scheduled_tasks.is_empty() && self.calendar_version == 0 {
            return Err(ControlError::InvalidProposal(
                "checkpoint scheduling reservations require a nonzero calendar version".to_string(),
            ));
        }
        let reservations = self.scheduled_tasks.values().collect::<Vec<_>>();
        for (index, reservation) in reservations.iter().enumerate() {
            let decision = reservation.decision();
            let Some(ends_at) = decision.ends_at() else {
                return Err(ControlError::InvalidProposal(
                    "checkpoint scheduling reservation has no bounded end".to_string(),
                ));
            };
            if ends_at <= decision.starts_at()
                || decision
                    .latest_activation_at()
                    .is_some_and(|latest| latest < decision.starts_at())
                || decision.snapshot_version() > self.calendar_version
            {
                return Err(ControlError::InvalidProposal(
                    "checkpoint scheduling reservation has an invalid interval or generation"
                        .to_string(),
                ));
            }
            let group = self
                .groups
                .get(reservation.group_id())
                .expect("restore validated Group");
            if group.mission_id() != decision.task_ref().mission_id() {
                return Err(ControlError::InvalidProposal(
                    "checkpoint scheduling reservation crosses Mission authority".to_string(),
                ));
            }
            let task = group
                .task_execution(decision.task_ref())
                .expect("restore validated Task");
            validate_decision_against_group(group, decision)?;
            if !task.assignments().is_empty()
                && task.assignments() != decision.proposed_assignments()
            {
                return Err(ControlError::InvalidProposal(
                    "checkpoint scheduling decision differs from committed Task bindings"
                        .to_string(),
                ));
            }
            if reservation.phase() != SchedulingReservationPhase::Scheduled {
                continue;
            }
            for later in reservations
                .iter()
                .skip(index + 1)
                .filter(|later| later.phase() == SchedulingReservationPhase::Scheduled)
            {
                if !intervals_overlap(
                    decision.starts_at(),
                    decision.ends_at(),
                    later.decision().starts_at(),
                    later.decision().ends_at(),
                ) {
                    continue;
                }
                let left = decision
                    .selections()
                    .iter()
                    .flat_map(|selection| selection.resource_ids());
                let right = later
                    .decision()
                    .selections()
                    .iter()
                    .flat_map(|selection| selection.resource_ids())
                    .collect::<BTreeSet<_>>();
                if left
                    .into_iter()
                    .any(|resource_id| right.contains(resource_id))
                {
                    return Err(ControlError::InvalidProposal(
                        "checkpoint contains overlapping future resource reservations".to_string(),
                    ));
                }
            }
        }
        Ok(())
    }

    /// Exposes current physical occupancy and future commitments as one immutable Scheduler input.
    pub fn scheduling_snapshot(&self, now: TimestampMs) -> SchedulingSnapshot {
        self.scheduling_snapshot_excluding(now, &BTreeSet::new())
    }

    /// Projects occupancy plus exact reusable ContextRole bindings for one Ready Task.
    pub fn scheduling_snapshot_for_task(
        &self,
        now: TimestampMs,
        group_id: &ExecutionGroupId,
        task_ref: &TaskRef,
    ) -> Result<SchedulingSnapshot, ControlError> {
        let group = self
            .groups
            .get(group_id)
            .ok_or_else(|| ControlError::UnknownGroup(group_id.clone()))?;
        let task = group.task_execution(task_ref).ok_or_else(|| {
            ControlError::InvalidProposal("Task is absent from the Mission Group".to_string())
        })?;
        let mut reusable = BTreeSet::new();
        let mut constraints = Vec::new();
        for (role, context_role) in task.context_roles() {
            if task.role_scope(role) != ResourceBindingScope::Context {
                continue;
            }
            let Some(binding) = group.context_binding(task.context_id(), context_role) else {
                continue;
            };
            reusable.extend(binding.assignment().resource_ids().iter().cloned());
            constraints.push(SchedulingRoleConstraint::new(
                role.clone(),
                binding.assignment().node_id().clone(),
                binding.assignment().resource_ids().to_vec(),
            ));
        }
        Ok(self
            .scheduling_snapshot_excluding(now, &reusable)
            .with_role_constraints(constraints))
    }

    /// Builds one snapshot while treating exact same-Context resources as reusable.
    fn scheduling_snapshot_excluding(
        &self,
        now: TimestampMs,
        reusable: &BTreeSet<ResourceId>,
    ) -> SchedulingSnapshot {
        let mut occupancies =
            self.reservations
                .iter()
                .filter(|(resource_id, _)| !reusable.contains(*resource_id))
                .map(|(resource_id, physical)| {
                    let planned =
                        self.scheduled_tasks
                            .get(&physical.task_ref)
                            .filter(|reservation| {
                                reservation.phase == SchedulingReservationPhase::Activated
                                    && reservation.decision.selections().iter().any(|selection| {
                                        selection.resource_ids().contains(resource_id)
                                    })
                            });
                    match planned.and_then(|reservation| reservation.decision.ends_at()) {
                        Some(planned_end) if planned_end > now => SchedulingOccupancy::new(
                            resource_id.clone(),
                            planned
                                .expect("planned end came from reservation")
                                .decision
                                .starts_at(),
                            Some(planned_end),
                        ),
                        _ => SchedulingOccupancy::new(resource_id.clone(), now, None),
                    }
                })
                .collect::<Vec<_>>();
        for reservation in self
            .scheduled_tasks
            .values()
            .filter(|reservation| reservation.phase == SchedulingReservationPhase::Scheduled)
        {
            for selection in reservation.decision.selections() {
                occupancies.extend(selection.resource_ids().iter().cloned().map(|resource_id| {
                    SchedulingOccupancy::new(
                        resource_id,
                        reservation.decision.starts_at(),
                        reservation.decision.ends_at(),
                    )
                }));
            }
        }
        SchedulingSnapshot::new(self.calendar_version, occupancies)
    }

    /// Atomically records one future decision if its Scheduler snapshot is still current.
    pub fn reserve_scheduled_task<S: SharedNodeStateReader>(
        &mut self,
        state: &S,
        candidates: &CandidateSet,
        group_id: ExecutionGroupId,
        decision: TaskSchedulingDecision,
        now: TimestampMs,
    ) -> Result<&ScheduledTaskReservation, ControlError> {
        if decision.snapshot_version() != self.calendar_version {
            return Err(ControlError::InvalidProposal(
                "Scheduler decision uses a stale Control calendar snapshot".to_string(),
            ));
        }
        if self.scheduled_tasks.contains_key(decision.task_ref()) {
            return Err(ControlError::InvalidProposal(
                "Task already has a Control scheduling reservation".to_string(),
            ));
        }
        if decision
            .ends_at()
            .is_none_or(|ends_at| ends_at <= decision.starts_at())
        {
            return Err(ControlError::InvalidProposal(
                "scheduling reservation requires a positive bounded interval".to_string(),
            ));
        }
        if decision
            .latest_activation_at()
            .is_some_and(|latest| latest < decision.starts_at())
        {
            return Err(ControlError::InvalidProposal(
                "scheduling reservation starts after its activation deadline".to_string(),
            ));
        }
        if decision.starts_at() < now {
            return Err(ControlError::InvalidProposal(
                "scheduling reservation starts before current Controller time".to_string(),
            ));
        }
        let group = self
            .groups
            .get(&group_id)
            .ok_or_else(|| ControlError::UnknownGroup(group_id.clone()))?;
        let task = group.task_execution(decision.task_ref()).ok_or_else(|| {
            ControlError::InvalidProposal("scheduled Task is absent from its Group".to_string())
        })?;
        if group.mission_id() != decision.task_ref().mission_id()
            || task.lifecycle() != domain::TaskExecutionLifecycle::Ready
        {
            return Err(ControlError::InvalidProposal(
                "only a Ready Task in the same Mission Group can reserve a scheduling interval"
                    .to_string(),
            ));
        }
        validate_decision_against_group(group, &decision)?;
        if candidates.task_ref() != decision.task_ref()
            || decision.selections().iter().any(|selection| {
                candidates
                    .for_role(selection.role_id())
                    .is_none_or(|role| !role.node_ids().contains(selection.node_id()))
            })
        {
            return Err(ControlError::InvalidProposal(
                "scheduling decision is outside its current Candidate Set".to_string(),
            ));
        }
        for selection in decision.selections() {
            let requirement = group
                .role_requirement(decision.task_ref(), selection.role_id())
                .expect("decision structure was validated above");
            if !self.node_is_eligible_for_role(state, selection.node_id(), requirement, now) {
                return Err(ControlError::InvalidProposal(format!(
                    "scheduled node {} is no longer eligible for role {}",
                    selection.node_id(),
                    selection.role_id()
                )));
            }
            let node = state
                .node(selection.node_id())
                .ok_or_else(|| ControlError::UnknownNode(selection.node_id().clone()))?;
            if selection
                .resource_ids()
                .iter()
                .zip(requirement.resource_requirements().iter())
                .any(|(resource_id, required)| {
                    !node.registration().resources().iter().any(|resource| {
                        resource.id() == resource_id
                            && resource.kind() == required.kind()
                            && resource.capacity() >= required.units()
                    })
                })
            {
                return Err(ControlError::InvalidProposal(format!(
                    "scheduled role {} resources no longer satisfy node ownership or capacity",
                    selection.role_id()
                )));
            }
        }
        let next_version = self.calendar_version.checked_add(1).ok_or_else(|| {
            ControlError::AllocationInvariant("Control calendar version overflow".to_string())
        })?;
        let snapshot = self.scheduling_snapshot_for_task(now, &group_id, decision.task_ref())?;
        for selection in decision.selections() {
            for resource_id in selection.resource_ids() {
                if snapshot.occupancies().iter().any(|occupancy| {
                    occupancy.resource_id() == resource_id
                        && intervals_overlap(
                            decision.starts_at(),
                            decision.ends_at(),
                            occupancy.starts_at(),
                            occupancy.ends_at(),
                        )
                }) {
                    return Err(ControlError::InvalidProposal(format!(
                        "resource {resource_id} conflicts with current Control calendar"
                    )));
                }
            }
        }
        let task_ref = decision.task_ref().clone();
        self.scheduled_tasks.insert(
            task_ref.clone(),
            ScheduledTaskReservation::new(group_id, decision),
        );
        self.calendar_version = next_version;
        self.scheduled_tasks.get(&task_ref).ok_or_else(|| {
            ControlError::AllocationInvariant("scheduled Task disappeared".to_string())
        })
    }

    /// Returns the current future reservation for one Task.
    pub fn scheduled_task(&self, task_ref: &TaskRef) -> Option<&ScheduledTaskReservation> {
        self.scheduled_tasks.get(task_ref)
    }

    /// Marks one scheduled interval active after Runtime confirms Task activation.
    pub(crate) fn activate_scheduled_task(&mut self, task_ref: &TaskRef) {
        if let Some(reservation) = self.scheduled_tasks.get_mut(task_ref)
            && reservation.phase == SchedulingReservationPhase::Scheduled
        {
            reservation.phase = SchedulingReservationPhase::Activated;
            self.calendar_version = self.calendar_version.saturating_add(1);
        }
    }

    /// Removes one reservation after terminal release or invalidation handoff.
    pub fn consume_scheduled_task(&mut self, task_ref: &TaskRef) {
        if self.scheduled_tasks.remove(task_ref).is_some() {
            self.calendar_version = self.calendar_version.saturating_add(1);
        }
    }

    /// Invalidates one future reservation while retaining its diagnostic for replanning.
    pub fn invalidate_scheduled_task(&mut self, task_ref: &TaskRef, reason: impl Into<String>) {
        if let Some(reservation) = self.scheduled_tasks.get_mut(task_ref)
            && reservation.phase != SchedulingReservationPhase::Activated
        {
            reservation.phase = SchedulingReservationPhase::Invalidated;
            reservation.reason = reason.into();
            self.calendar_version = self.calendar_version.saturating_add(1);
        }
    }

    /// Removes future reservations for a cancelled or terminal Mission.
    pub fn cancel_scheduled_mission(&mut self, mission_id: &MissionId) -> usize {
        let before = self.scheduled_tasks.len();
        self.scheduled_tasks
            .retain(|task_ref, _| task_ref.mission_id() != mission_id);
        let removed = before.saturating_sub(self.scheduled_tasks.len());
        if removed > 0 {
            self.calendar_version = self.calendar_version.saturating_add(1);
        }
        removed
    }

    /// Returns every future reservation in deterministic Task identity order.
    pub fn scheduled_tasks(&self) -> impl Iterator<Item = &ScheduledTaskReservation> {
        self.scheduled_tasks.values()
    }

    /// Returns the future reservation owner that blocks a physical commit for another Task.
    pub(crate) fn scheduled_resource_conflict(
        &self,
        resource_id: &ResourceId,
        committing_task: &TaskRef,
    ) -> Option<(TaskRef, domain::RoleId)> {
        self.scheduled_tasks
            .values()
            .filter(|reservation| reservation.phase == SchedulingReservationPhase::Scheduled)
            .filter(|reservation| reservation.task_ref() != committing_task)
            .find_map(|reservation| {
                reservation
                    .decision
                    .selections()
                    .iter()
                    .find(|selection| selection.resource_ids().contains(resource_id))
                    .map(|selection| (reservation.task_ref().clone(), selection.role_id().clone()))
            })
    }
}

/// Validates Scheduler evidence against Control-owned Task and Role requirements.
fn validate_decision_against_group(
    group: &ExecutionGroup,
    decision: &TaskSchedulingDecision,
) -> Result<(), ControlError> {
    let task = group.task_execution(decision.task_ref()).ok_or_else(|| {
        ControlError::InvalidProposal(
            "scheduling decision references a Task outside its Group".to_string(),
        )
    })?;
    let selected_roles = decision
        .selections()
        .iter()
        .map(|selection| selection.role_id())
        .collect::<BTreeSet<_>>();
    let required_roles = task.role_scopes().keys().collect::<BTreeSet<_>>();
    let mut selected_resources = BTreeSet::new();
    if selected_roles != required_roles
        || selected_roles.len() != decision.selections().len()
        || decision.selections().iter().any(|selection| {
            let Some(requirement) =
                group.role_requirement(decision.task_ref(), selection.role_id())
            else {
                return true;
            };
            let resources = requirement.resource_requirements();
            selection.resource_ids().len() != resources.len()
                || selection.resource_units().len() != resources.len()
                || selection
                    .resource_units()
                    .iter()
                    .zip(resources.iter())
                    .any(|(selected, required)| *selected != required.units())
                || selection
                    .resource_ids()
                    .iter()
                    .any(|resource_id| !selected_resources.insert(resource_id))
        })
    {
        return Err(ControlError::InvalidProposal(
            "scheduling decision differs from authoritative Task requirements".to_string(),
        ));
    }
    Ok(())
}

/// Returns whether two half-open intervals overlap, treating no end as physically unresolved.
fn intervals_overlap(
    left_start: TimestampMs,
    left_end: Option<TimestampMs>,
    right_start: TimestampMs,
    right_end: Option<TimestampMs>,
) -> bool {
    match (left_end, right_end) {
        (Some(left_end), Some(right_end)) => left_start < right_end && right_start < left_end,
        (Some(left_end), None) => left_end > right_start,
        (None, Some(right_end)) => right_end > left_start,
        (None, None) => true,
    }
}
