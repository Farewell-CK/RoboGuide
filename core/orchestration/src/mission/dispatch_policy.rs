//! Durable logical service ordering and deployment admission bounds, independent of resource authority.

use super::super::*;
use serde::{Deserialize, Serialize};

/// Maximum logical Task progression attempts in one application input or timer.
pub const DISPATCH_TURN_LIMIT: usize = 32;
/// Logical service turns per additional waiting-priority level.
const AGING_QUANTUM: u64 = 8;
/// Finite aging advantage exceeds the entire explicit priority range.
const MAX_AGE_PRIORITY: u64 = 512;

/// Deployment-owned admission bounds; existing physical ownership is never revoked by these limits.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct MissionAdmissionLimits {
    /// Maximum nonterminal Mission count.
    missions: usize,
    /// Maximum declared Task/Role slots across those Missions.
    role_slots: usize,
}

impl MissionAdmissionLimits {
    /// Rejects unusable deployment bounds before any Mission acceptance.
    pub fn try_new(missions: usize, role_slots: usize) -> Result<Self, OrchestrationError> {
        if missions == 0 || role_slots == 0 {
            return Err(OrchestrationError::InvalidContract(
                "Mission admission limits must be positive".into(),
            ));
        }
        Ok(Self {
            missions,
            role_slots,
        })
    }
}

impl Default for MissionAdmissionLimits {
    /// Uses conservative bounded admission without changing per-resource Control capacity.
    fn default() -> Self {
        Self {
            missions: 128,
            role_slots: 4096,
        }
    }
}

/// Evidence of fair logical service; it never grants a resource, executor or physical stop.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MissionDispatchState {
    /// Operator/deployment priority, larger values receiving earlier logical turns.
    priority: u8,
    /// Latest service ordinal, retained across checkpoint restore.
    last_service: u64,
    /// First currently queued ordinal per Task; service consumes its accumulated advantage.
    waiting: BTreeMap<TaskId, u64>,
}

impl Default for MissionDispatchState {
    /// Legacy plans receive normal priority and fresh logical ordering without a fabricated clock.
    fn default() -> Self {
        Self {
            priority: 128,
            last_service: 0,
            waiting: BTreeMap::new(),
        }
    }
}

impl MissionDispatchState {
    /// Returns the persisted priority without implying a Control commitment.
    pub const fn priority(&self) -> u8 {
        self.priority
    }

    /// Returns the last consumed logical service ordinal.
    pub const fn last_service_ordinal(&self) -> u64 {
        self.last_service
    }
}

impl MissionOrchestrator {
    /// Freezes current deployment admission input; limits constrain only future new Missions.
    pub fn set_admission_limits(&mut self, limits: MissionAdmissionLimits) {
        self.admission_limits = limits;
    }

    /// Rejects a prospective new plan before Group creation while counting holds and cancellation.
    pub fn validate_admission_capacity(
        &self,
        plan: &MissionPlan,
    ) -> Result<(), OrchestrationError> {
        SupportedMechanismProfile::current().validate(plan)?;
        if self.executions.contains_key(plan.goal().mission_id()) {
            return Ok(());
        }
        let live = self.executions.values().filter(|execution| {
            matches!(
                execution.lifecycle(),
                MissionExecutionLifecycle::Accepted
                    | MissionExecutionLifecycle::Running
                    | MissionExecutionLifecycle::Cancelling
            )
        });
        let mut missions = 1_usize;
        let mut slots = declared_slots(plan)?;
        for execution in live {
            missions = missions
                .checked_add(1)
                .ok_or_else(|| OrchestrationError::Mission("admission count overflow".into()))?;
            slots = slots
                .checked_add(declared_slots(execution.plan())?)
                .ok_or_else(|| OrchestrationError::Mission("admission slots overflow".into()))?;
        }
        if missions > self.admission_limits.missions || slots > self.admission_limits.role_slots {
            return Err(OrchestrationError::AdmissionCapacityExceeded);
        }
        Ok(())
    }

    /// Updates only logical priority; callers persist the candidate/event before exposing this decision.
    pub fn set_mission_priority<E: EventSink>(
        &mut self,
        mission: &MissionId,
        priority: u8,
        now: TimestampMs,
        correlation: &CorrelationId,
        events: &mut E,
    ) -> Result<(), OrchestrationError> {
        let execution = self
            .executions
            .get_mut(mission)
            .ok_or_else(|| OrchestrationError::Mission("unknown Mission policy".into()))?;
        if !matches!(
            execution.lifecycle(),
            MissionExecutionLifecycle::Accepted
                | MissionExecutionLifecycle::Running
                | MissionExecutionLifecycle::Cancelling
        ) {
            return Err(OrchestrationError::Mission(
                "terminal Mission policy cannot be changed".into(),
            ));
        }
        if execution.dispatch_policy.priority != priority {
            execution.dispatch_policy.priority = priority;
            events.append(
                now,
                correlation,
                None,
                domain::EventPayload::MissionSchedulingPriorityChanged {
                    mission_id: mission.clone(),
                    priority,
                },
            );
        }
        Ok(())
    }

    /// Selects one exact Ready logical Task using persistent waiting age, round fairness and due windows.
    pub fn next_dispatch_task(
        &mut self,
        control: &ControlPlane,
        now: TimestampMs,
        attempted: &BTreeSet<TaskRef>,
        served_round: &BTreeSet<MissionId>,
    ) -> Result<Option<TaskRef>, OrchestrationError> {
        let ordinal = self
            .executions
            .values()
            .map(|execution| execution.dispatch_policy.last_service)
            .max()
            .unwrap_or(0);
        let mut candidates = Vec::new();
        let due_tasks = control
            .scheduled_tasks()
            .filter(|reservation| {
                reservation.phase() == SchedulingReservationPhase::Scheduled
                    && reservation.decision().starts_at() <= now
            })
            .map(|reservation| {
                (
                    reservation.task_ref().clone(),
                    reservation.decision().starts_at(),
                )
            })
            .collect::<BTreeMap<_, _>>();
        for mission in self.mission_ids() {
            let ready = self.dispatchable_tasks(&mission, control);
            let policy = &mut self
                .executions
                .get_mut(&mission)
                .expect("known Mission")
                .dispatch_policy;
            let ready_ids = ready
                .iter()
                .map(TaskRef::task_id)
                .cloned()
                .collect::<BTreeSet<_>>();
            policy.waiting.retain(|task, _| ready_ids.contains(task));
            for task in &ready {
                policy
                    .waiting
                    .entry(task.task_id().clone())
                    .or_insert(ordinal);
            }
            for (plan_order, task) in ready.into_iter().enumerate() {
                if attempted.contains(&task) {
                    continue;
                }
                let queued = policy.waiting[task.task_id()];
                let age = ordinal.checked_sub(queued).ok_or_else(|| {
                    OrchestrationError::Mission("dispatch ordinal regressed".into())
                })?;
                let priority =
                    u64::from(policy.priority) + (age / AGING_QUANTUM).min(MAX_AGE_PRIORITY);
                let due = due_tasks.get(&task).copied();
                if served_round.contains(&mission) && due.is_none() {
                    continue;
                }
                candidates.push((
                    due,
                    priority,
                    queued,
                    policy.last_service,
                    mission.clone(),
                    plan_order,
                    task,
                ));
            }
        }
        candidates.sort_by(|left, right| {
            match (left.0, right.0) {
                (Some(a), Some(b)) => a.cmp(&b),
                (Some(_), None) => std::cmp::Ordering::Less,
                (None, Some(_)) => std::cmp::Ordering::Greater,
                (None, None) => std::cmp::Ordering::Equal,
            }
            .then_with(|| right.1.cmp(&left.1))
            .then_with(|| left.2.cmp(&right.2))
            .then_with(|| left.3.cmp(&right.3))
            .then_with(|| left.4.cmp(&right.4))
            .then_with(|| left.5.cmp(&right.5))
        });
        Ok(candidates.into_iter().next().map(|candidate| candidate.6))
    }

    /// Consumes one logical service turn on the same private candidate as its Task progression.
    pub fn record_dispatch_turn(&mut self, task: &TaskRef) -> Result<(), OrchestrationError> {
        self.record_dispatch_attempt(task, true)
    }

    /// Records an attempted turn while retaining waiting age until Control or Runtime grants useful work.
    pub fn record_dispatch_attempt(
        &mut self,
        task: &TaskRef,
        granted: bool,
    ) -> Result<(), OrchestrationError> {
        let next = self
            .executions
            .values()
            .map(|execution| execution.dispatch_policy.last_service)
            .max()
            .unwrap_or(0)
            .checked_add(1)
            .ok_or_else(|| {
                OrchestrationError::Mission("dispatch service ordinal exhausted".into())
            })?;
        let execution = self
            .executions
            .get_mut(task.mission_id())
            .ok_or_else(|| OrchestrationError::Mission("unknown dispatch Mission".into()))?;
        if !execution
            .plan
            .task_graph()
            .tasks()
            .iter()
            .any(|planned| planned.requirement().task_ref() == task)
        {
            return Err(OrchestrationError::Mission(
                "dispatch Task is absent from the accepted plan".into(),
            ));
        }
        execution.dispatch_policy.last_service = next;
        if granted {
            execution
                .dispatch_policy
                .waiting
                .insert(task.task_id().clone(), next);
        } else {
            execution
                .dispatch_policy
                .waiting
                .entry(task.task_id().clone())
                .or_insert(next - 1);
        }
        Ok(())
    }
}

/// Counts immutable declared logical slots; it is not a divisible resource quota.
fn declared_slots(plan: &MissionPlan) -> Result<usize, OrchestrationError> {
    plan.task_graph()
        .tasks()
        .iter()
        .try_fold(0_usize, |sum, task| {
            sum.checked_add(task.requirement().roles().len())
        })
        .ok_or_else(|| OrchestrationError::Mission("declared role slots overflow".into()))
}

/// Rejects fabricated waiting ordinals and Task identities before accepting restored policy history.
pub(super) fn validate_restored_policy(
    orchestrator: &MissionOrchestrator,
) -> Result<(), OrchestrationError> {
    let maximum = orchestrator
        .executions
        .values()
        .map(|execution| execution.dispatch_policy.last_service)
        .max()
        .unwrap_or(0);
    let mut service_ordinals = BTreeSet::new();
    for execution in orchestrator.executions.values() {
        if execution.dispatch_policy.last_service != 0
            && !service_ordinals.insert(execution.dispatch_policy.last_service)
        {
            return Err(OrchestrationError::Mission(
                "duplicate dispatch service ordinal".into(),
            ));
        }
        for (task, ordinal) in &execution.dispatch_policy.waiting {
            if *ordinal > maximum
                || !execution
                    .plan
                    .task_graph()
                    .tasks()
                    .iter()
                    .any(|planned| planned.task_id() == task)
            {
                return Err(OrchestrationError::Mission(
                    "invalid dispatch policy checkpoint history".into(),
                ));
            }
        }
    }
    Ok(())
}
