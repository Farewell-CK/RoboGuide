#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! Mission execution authority over the complete MissionPlan and its long-lived Group.

use control::{
    BoundedJointScheduler, ControlError, ControlPlane, GroupLifecycle, SchedulingReservationPhase,
    TaskSchedulingOutcome,
};
use domain::{
    CorrelationId, ExecutionGroupId, MissionId, MissionPlan, ResourceBindingScope, ResourceId,
    TaskExecutionLifecycle, TaskId, TaskRef, TimestampMs,
};
use ports::{EventSink, SharedNodeStateReader};
use std::collections::{BTreeMap, BTreeSet};
use std::fmt::{Display, Formatter};

mod integration_bridge;
mod mechanism_profile;
mod mission_contract;

pub use integration_bridge::{
    CONTROLLER_CHECKPOINT_SCHEMA, GroupSharedViewEntry, GroupSharedViewSnapshot,
    GroupSpatialVerification, GroupViewFreshness, IntegrationRuntimeBridge,
    IntegrationRuntimeError, ObservedTaskOutcome, ObservedTaskResult, RemoteExecutionStatus,
};
pub use mechanism_profile::SupportedMechanismProfile;
pub use mission_contract::decode_mission_plan;

/// Mission execution lifecycle owned by orchestration rather than Runtime.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum MissionExecutionLifecycle {
    /// The complete plan is accepted and its Group has been created.
    Accepted,
    /// At least one Task is Ready, Active, Blocked, or completed while later Tasks remain.
    Running,
    /// An explicit cancellation was durably requested; active attempts are draining.
    Cancelling,
    /// Every Task in the accepted plan completed and the Group was released.
    Completed,
    /// Mission policy declared a final failure and released the Group.
    Failed,
    /// An explicit cancellation terminated the Mission and released the Group.
    Cancelled,
}

/// One accepted MissionPlan and its single default Phase 1 Execution Group.
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct MissionExecution {
    /// Complete immutable plan used for DAG and completion decisions.
    plan: MissionPlan,
    /// Mission-level runtime coordination context.
    group_id: ExecutionGroupId,
    /// Current orchestration-owned lifecycle.
    lifecycle: MissionExecutionLifecycle,
    /// RoboGuide-local acceptance time anchoring relative scheduling constraints.
    accepted_at: TimestampMs,
    /// Last durable scheduling deferral reason per Task, used to suppress duplicate evidence.
    #[serde(default)]
    scheduling_deferrals: BTreeMap<TaskId, String>,
}

impl MissionExecution {
    /// Returns the complete accepted MissionPlan.
    pub const fn plan(&self) -> &MissionPlan {
        &self.plan
    }

    /// Returns the Mission-level Execution Group identity.
    pub const fn group_id(&self) -> &ExecutionGroupId {
        &self.group_id
    }

    /// Returns the orchestration-owned Mission lifecycle.
    pub const fn lifecycle(&self) -> MissionExecutionLifecycle {
        self.lifecycle
    }

    /// Returns the durable Mission acceptance time used by task timing offsets.
    pub const fn accepted_at(&self) -> TimestampMs {
        self.accepted_at
    }
}

/// Errors raised when Mission orchestration invariants are violated.
#[derive(Debug)]
pub enum OrchestrationError {
    /// Control rejected a lifecycle or ownership transition.
    Control(ControlError),
    /// A Mission identity was absent or reused.
    Mission(String),
}

impl Display for OrchestrationError {
    /// Formats a stable orchestration diagnostic.
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Control(error) => write!(formatter, "control rejected orchestration: {error}"),
            Self::Mission(reason) => formatter.write_str(reason),
        }
    }
}

impl std::error::Error for OrchestrationError {}

impl From<ControlError> for OrchestrationError {
    /// Preserves Control diagnostics across the orchestration boundary.
    fn from(value: ControlError) -> Self {
        Self::Control(value)
    }
}

/// Deterministic Phase 1 Mission execution authority.
#[derive(Debug, Default, Clone)]
pub struct MissionOrchestrator {
    /// Accepted Missions keyed independently from Task-local identity.
    executions: BTreeMap<MissionId, MissionExecution>,
}

impl MissionOrchestrator {
    /// Creates an empty Mission execution authority.
    pub const fn new() -> Self {
        Self {
            executions: BTreeMap::new(),
        }
    }

    /// Serializes accepted Mission plans and orchestration lifecycle for process recovery.
    pub fn checkpoint_json(&self) -> Result<String, OrchestrationError> {
        let executions = self
            .executions
            .values()
            .map(|execution| {
                serde_json::json!({
                    "plan": mission_plan_json(execution.plan()),
                    "group_id": execution.group_id().as_str(),
                    "lifecycle": execution.lifecycle(),
                    "accepted_at_ms": execution.accepted_at().as_millis(),
                    "scheduling_deferrals": execution.scheduling_deferrals.iter().map(
                        |(task_id, reason)| serde_json::json!({
                            "task_id": task_id.as_str(),
                            "reason": reason,
                        })
                    ).collect::<Vec<_>>(),
                })
            })
            .collect::<Vec<_>>();
        serde_json::to_string(&executions).map_err(|error| {
            OrchestrationError::Mission(format!(
                "cannot serialize orchestration checkpoint: {error}"
            ))
        })
    }

    /// Restores accepted Mission plans and rejects malformed orchestration evidence.
    pub fn restore_json(json: &str) -> Result<Self, OrchestrationError> {
        let executions: Vec<serde_json::Value> = serde_json::from_str(json).map_err(|error| {
            OrchestrationError::Mission(format!("cannot restore orchestration checkpoint: {error}"))
        })?;
        let mut restored = Self::new();
        for value in executions {
            let plan_value = value.get("plan").ok_or_else(|| {
                OrchestrationError::Mission("checkpoint misses MissionPlan".to_string())
            })?;
            let plan =
                decode_mission_plan(&serde_json::to_string(plan_value).map_err(|error| {
                    OrchestrationError::Mission(format!("invalid MissionPlan checkpoint: {error}"))
                })?)?;
            SupportedMechanismProfile::current().validate(&plan)?;
            let group_id = ExecutionGroupId::new(
                value
                    .get("group_id")
                    .and_then(serde_json::Value::as_str)
                    .ok_or_else(|| {
                        OrchestrationError::Mission("checkpoint misses Group identity".to_string())
                    })?,
            )
            .map_err(|error| OrchestrationError::Mission(error.to_string()))?;
            let lifecycle: MissionExecutionLifecycle =
                serde_json::from_value(value.get("lifecycle").cloned().ok_or_else(|| {
                    OrchestrationError::Mission("checkpoint misses Mission lifecycle".to_string())
                })?)
                .map_err(|error| {
                    OrchestrationError::Mission(format!("invalid Mission lifecycle: {error}"))
                })?;
            let accepted_at = TimestampMs::new(
                value
                    .get("accepted_at_ms")
                    .and_then(serde_json::Value::as_u64)
                    .unwrap_or(0),
            );
            validate_plan_timing_anchor(&plan, accepted_at)?;
            let mut scheduling_deferrals = BTreeMap::new();
            let deferral_values = value
                .get("scheduling_deferrals")
                .map(|value| {
                    value.as_array().ok_or_else(|| {
                        OrchestrationError::Mission(
                            "checkpoint scheduling deferrals must be an array".to_string(),
                        )
                    })
                })
                .transpose()?
                .cloned()
                .unwrap_or_default();
            for deferral in deferral_values {
                let task_id = TaskId::new(
                    deferral
                        .get("task_id")
                        .and_then(serde_json::Value::as_str)
                        .ok_or_else(|| {
                            OrchestrationError::Mission(
                                "checkpoint scheduling deferral misses Task identity".to_string(),
                            )
                        })?,
                )
                .map_err(|error| OrchestrationError::Mission(error.to_string()))?;
                let reason = deferral
                    .get("reason")
                    .and_then(serde_json::Value::as_str)
                    .filter(|reason| !reason.is_empty())
                    .ok_or_else(|| {
                        OrchestrationError::Mission(
                            "checkpoint scheduling deferral misses reason".to_string(),
                        )
                    })?
                    .to_string();
                if !plan
                    .task_graph()
                    .tasks()
                    .iter()
                    .any(|task| task.requirement().task_ref().task_id() == &task_id)
                    || scheduling_deferrals.insert(task_id, reason).is_some()
                {
                    return Err(OrchestrationError::Mission(
                        "checkpoint scheduling deferral references an unknown or duplicate Task"
                            .to_string(),
                    ));
                }
            }
            let execution = MissionExecution {
                plan,
                group_id,
                lifecycle,
                accepted_at,
                scheduling_deferrals,
            };
            let mission_id = execution.plan.goal().mission_id().clone();
            if restored.executions.insert(mission_id, execution).is_some() {
                return Err(OrchestrationError::Mission(
                    "orchestration checkpoint contains duplicate Mission".to_string(),
                ));
            }
        }
        Ok(restored)
    }

    /// Accepts a complete plan, creates its one default Group, and exposes initial Ready Tasks.
    pub fn submit<E: EventSink>(
        &mut self,
        plan: MissionPlan,
        group_id: ExecutionGroupId,
        control: &mut ControlPlane,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<&MissionExecution, OrchestrationError> {
        SupportedMechanismProfile::current().validate(&plan)?;
        let mission_id = plan.goal().mission_id().clone();
        if self.executions.contains_key(&mission_id) {
            let existing = self
                .executions
                .get(&mission_id)
                .expect("contains_key proves existing Mission authority");
            if existing.plan() == &plan && existing.group_id() == &group_id {
                return Ok(existing);
            }
            return Err(OrchestrationError::Mission(format!(
                "Mission {mission_id} already exists with a different plan or Group"
            )));
        }
        validate_plan_timing_anchor(&plan, timestamp)?;
        control.create_mission_group(group_id.clone(), &plan, timestamp, correlation_id, events)?;
        self.executions.insert(
            mission_id.clone(),
            MissionExecution {
                plan,
                group_id,
                lifecycle: MissionExecutionLifecycle::Accepted,
                accepted_at: timestamp,
                scheduling_deferrals: BTreeMap::new(),
            },
        );
        self.refresh_ready(&mission_id, control, timestamp, correlation_id, events)?;
        self.executions
            .get(&mission_id)
            .ok_or_else(|| OrchestrationError::Mission("accepted Mission disappeared".to_string()))
    }

    /// Returns one accepted Mission execution.
    pub fn execution(&self, mission_id: &MissionId) -> Option<&MissionExecution> {
        self.executions.get(mission_id)
    }

    /// Returns all accepted Mission identities in deterministic order.
    pub fn mission_ids(&self) -> Vec<MissionId> {
        self.executions.keys().cloned().collect()
    }

    /// Validates restored Mission authority against Control before execution traffic is accepted.
    pub fn validate_control_authority(
        &self,
        control: &ControlPlane,
    ) -> Result<(), OrchestrationError> {
        let orchestration_groups = self
            .executions
            .values()
            .map(MissionExecution::group_id)
            .collect::<BTreeSet<_>>();
        for (mission_id, execution) in &self.executions {
            control.validate_mission_group_plan(execution.group_id(), execution.plan())?;
            let group = control
                .group(execution.group_id())
                .expect("Control plan validation requires the Group");
            let lifecycle_is_aligned = match execution.lifecycle() {
                MissionExecutionLifecycle::Accepted | MissionExecutionLifecycle::Running => {
                    matches!(
                        group.lifecycle(),
                        GroupLifecycle::Bound
                            | GroupLifecycle::Active
                            | GroupLifecycle::Adapted
                            | GroupLifecycle::Blocked
                    )
                }
                MissionExecutionLifecycle::Cancelling => {
                    matches!(
                        group.lifecycle(),
                        GroupLifecycle::Bound
                            | GroupLifecycle::Active
                            | GroupLifecycle::Adapted
                            | GroupLifecycle::Blocked
                    )
                }
                MissionExecutionLifecycle::Completed => {
                    group.lifecycle() == GroupLifecycle::Released
                        && group
                            .task_executions()
                            .all(|task| task.lifecycle() == TaskExecutionLifecycle::Completed)
                }
                MissionExecutionLifecycle::Failed | MissionExecutionLifecycle::Cancelled => {
                    group.lifecycle() == GroupLifecycle::Released
                }
            };
            if !lifecycle_is_aligned {
                return Err(OrchestrationError::Mission(format!(
                    "restored Mission {mission_id} lifecycle disagrees with its Execution Group"
                )));
            }
            for reservation in control
                .scheduled_tasks()
                .filter(|reservation| reservation.group_id() == execution.group_id())
            {
                let requirement = execution
                    .plan()
                    .task_graph()
                    .tasks()
                    .iter()
                    .find(|task| task.requirement().task_ref() == reservation.task_ref())
                    .map(|task| task.requirement())
                    .ok_or_else(|| {
                        OrchestrationError::Mission(
                            "restored scheduling reservation is absent from MissionPlan"
                                .to_string(),
                        )
                    })?;
                let decision = reservation.decision();
                let earliest_at = checked_plan_timestamp(
                    execution.accepted_at(),
                    requirement.timing().earliest_start_offset_ms(),
                    decision.task_ref(),
                )?;
                let expected_end = requirement
                    .timing()
                    .estimated_duration_ms()
                    .map(|duration| {
                        checked_plan_timestamp(decision.starts_at(), duration, decision.task_ref())
                    })
                    .transpose()?;
                let expected_latest = plan_latest_activation_at(
                    requirement.timing(),
                    execution.accepted_at(),
                    decision.task_ref(),
                )?;
                if decision.starts_at() < earliest_at
                    || decision.ends_at() != expected_end
                    || decision.latest_activation_at() != expected_latest
                    || expected_latest.is_some_and(|latest| decision.starts_at() > latest)
                {
                    return Err(OrchestrationError::Mission(format!(
                        "restored scheduling decision for {} disagrees with accepted Task timing",
                        decision.task_ref()
                    )));
                }
            }
        }
        for group_id in control.group_ids() {
            let group = control
                .group(&group_id)
                .expect("Control returned its own Group identity");
            if group.task_executions().next().is_some() && !orchestration_groups.contains(&group_id)
            {
                return Err(OrchestrationError::Mission(format!(
                    "restored Mission-level Group {group_id} has no orchestration authority"
                )));
            }
        }
        Ok(())
    }

    /// Returns all unbound Ready Task identities in deterministic plan order.
    pub fn ready_tasks(&self, mission_id: &MissionId, control: &ControlPlane) -> Vec<TaskRef> {
        let Some(execution) = self.executions.get(mission_id) else {
            return Vec::new();
        };
        if execution.lifecycle() == MissionExecutionLifecycle::Cancelling {
            return Vec::new();
        }
        let Some(group) = control.group(execution.group_id()) else {
            return Vec::new();
        };
        self.dispatchable_tasks(mission_id, control)
            .into_iter()
            .filter(|task_ref| {
                group
                    .task_execution(task_ref)
                    .is_some_and(|task| task.assignments().is_empty())
            })
            .collect()
    }

    /// Returns Ready Tasks including committed bindings waiting for Runtime coordination.
    pub fn dispatchable_tasks(
        &self,
        mission_id: &MissionId,
        control: &ControlPlane,
    ) -> Vec<TaskRef> {
        let Some(execution) = self.executions.get(mission_id) else {
            return Vec::new();
        };
        if execution.lifecycle() != MissionExecutionLifecycle::Running {
            return Vec::new();
        }
        let Some(group) = control.group(execution.group_id()) else {
            return Vec::new();
        };
        execution
            .plan()
            .task_graph()
            .tasks()
            .iter()
            .filter_map(|task| {
                let task_ref = task.requirement().task_ref();
                group
                    .task_execution(task_ref)
                    .filter(|task_execution| {
                        task_execution.lifecycle() == TaskExecutionLifecycle::Ready
                            && execution
                                .scheduling_deferrals
                                .get(task_ref.task_id())
                                .is_none_or(|reason| reason != "window-missed")
                    })
                    .map(|_| task_ref.clone())
            })
            .collect()
    }

    /// Emits a Task deferral only when its durable reason differs from prior evidence.
    fn record_scheduling_deferral<E: EventSink>(
        &mut self,
        mission_id: &MissionId,
        task_ref: &TaskRef,
        reason: &str,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<(), OrchestrationError> {
        let execution = self
            .executions
            .get_mut(mission_id)
            .ok_or_else(|| OrchestrationError::Mission(format!("unknown Mission {mission_id}")))?;
        if execution
            .scheduling_deferrals
            .get(task_ref.task_id())
            .is_some_and(|recorded| recorded == reason)
        {
            return Ok(());
        }
        execution
            .scheduling_deferrals
            .insert(task_ref.task_id().clone(), reason.to_string());
        append_scheduling_deferred(events, task_ref, reason, timestamp, correlation_id);
        Ok(())
    }

    /// Clears obsolete deferral evidence after the Task obtains a feasible decision.
    fn clear_scheduling_deferral(&mut self, mission_id: &MissionId, task_ref: &TaskRef) {
        if let Some(execution) = self.executions.get_mut(mission_id) {
            execution.scheduling_deferrals.remove(task_ref.task_id());
        }
    }

    /// Drives one unbound Ready Task through Match, Schedule, Propose, Commit, and Bind.
    #[allow(clippy::too_many_arguments)]
    pub fn prepare_task<S: SharedNodeStateReader, E: EventSink>(
        &mut self,
        mission_id: &MissionId,
        task_ref: &TaskRef,
        state: &S,
        control: &mut ControlPlane,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<domain::TaskExecution, OrchestrationError> {
        let (plan, requirement, group_id, accepted_at, window_already_missed) = {
            let execution = self.executions.get(mission_id).ok_or_else(|| {
                OrchestrationError::Mission(format!("unknown Mission {mission_id}"))
            })?;
            let requirement = execution
                .plan()
                .task_graph()
                .tasks()
                .iter()
                .find(|task| task.requirement().task_ref() == task_ref)
                .map(|task| task.requirement().clone())
                .ok_or_else(|| {
                    OrchestrationError::Mission(
                        "Task is absent from the accepted MissionPlan".to_string(),
                    )
                })?;
            (
                execution.plan().clone(),
                requirement,
                execution.group_id().clone(),
                execution.accepted_at(),
                execution
                    .scheduling_deferrals
                    .get(task_ref.task_id())
                    .is_some_and(|reason| reason == "window-missed"),
            )
        };
        if window_already_missed {
            return Err(OrchestrationError::Mission(
                "joint scheduling window missed".to_string(),
            ));
        }
        let candidates = control.match_capabilities_for_mission(
            state,
            &plan,
            &requirement,
            timestamp,
            correlation_id,
            events,
        )?;
        let mut had_scheduled_decision = control.scheduled_task(task_ref).is_some();
        let mut was_scheduled = had_scheduled_decision;
        let reusable_decision = match control.scheduled_task(task_ref).cloned() {
            Some(reservation) if reservation.phase() == SchedulingReservationPhase::Invalidated => {
                control.consume_scheduled_task(task_ref);
                events.append(
                    timestamp,
                    correlation_id,
                    None,
                    domain::EventPayload::SchedulingReservationReleased {
                        group_id: group_id.clone(),
                        task_ref: task_ref.clone(),
                        reason: "Reservation invalidated for replanning".to_string(),
                    },
                );
                had_scheduled_decision = false;
                was_scheduled = false;
                None
            }
            Some(reservation) if reservation.decision().starts_at() > timestamp => {
                return control
                    .group(&group_id)
                    .and_then(|group| group.task_execution(task_ref))
                    .cloned()
                    .ok_or_else(|| {
                        OrchestrationError::Mission(
                            "scheduled TaskExecution disappeared".to_string(),
                        )
                    });
            }
            Some(reservation)
                if reservation
                    .decision()
                    .ends_at()
                    .is_some_and(|ends_at| timestamp >= ends_at)
                    || reservation
                        .decision()
                        .latest_activation_at()
                        .is_some_and(|latest| timestamp > latest) =>
            {
                let window_missed = reservation
                    .decision()
                    .latest_activation_at()
                    .is_some_and(|latest| timestamp > latest);
                let reason = if window_missed {
                    "Scheduling activation window missed"
                } else {
                    "Scheduling interval expired before activation"
                };
                control.invalidate_scheduled_task(task_ref, reason);
                events.append(
                    timestamp,
                    correlation_id,
                    None,
                    domain::EventPayload::SchedulingReservationInvalidated {
                        group_id: group_id.clone(),
                        task_ref: task_ref.clone(),
                        reason: reason.to_string(),
                    },
                );
                control.consume_scheduled_task(task_ref);
                events.append(
                    timestamp,
                    correlation_id,
                    None,
                    domain::EventPayload::SchedulingReservationReleased {
                        group_id: group_id.clone(),
                        task_ref: task_ref.clone(),
                        reason: "Expired reservation released for replanning".to_string(),
                    },
                );
                had_scheduled_decision = false;
                was_scheduled = false;
                if window_missed {
                    self.record_scheduling_deferral(
                        mission_id,
                        task_ref,
                        "window-missed",
                        timestamp,
                        correlation_id,
                        events,
                    )?;
                    return Err(OrchestrationError::Mission(
                        "joint scheduling window missed".to_string(),
                    ));
                }
                None
            }
            Some(reservation) => Some(reservation.decision().clone()),
            None => None,
        };
        let decision = if let Some(decision) = reusable_decision {
            decision
        } else {
            let snapshot = control.scheduling_snapshot_for_task(timestamp, &group_id, task_ref)?;
            let scheduler = BoundedJointScheduler::new();
            let outcome = match scheduler.schedule_task_with_snapshot(
                state,
                &requirement,
                &candidates,
                &snapshot,
                accepted_at,
                timestamp,
            ) {
                Ok(outcome) => outcome,
                Err(control::SchedulerError::SearchLimited) => {
                    self.record_scheduling_deferral(
                        mission_id,
                        task_ref,
                        "search-limited",
                        timestamp,
                        correlation_id,
                        events,
                    )?;
                    return Err(OrchestrationError::Mission(
                        "joint scheduling deferred: search budget exhausted".to_string(),
                    ));
                }
                Err(control::SchedulerError::InvalidTimeWindow) => {
                    self.record_scheduling_deferral(
                        mission_id,
                        task_ref,
                        "invalid-time-window",
                        timestamp,
                        correlation_id,
                        events,
                    )?;
                    return Err(OrchestrationError::Mission(
                        "joint scheduling deferred: invalid time window".to_string(),
                    ));
                }
                Err(error) => return Err(OrchestrationError::Mission(error.to_string())),
            };
            match outcome {
                TaskSchedulingOutcome::SelectedNow(decision) => {
                    self.clear_scheduling_deferral(mission_id, task_ref);
                    if decision.ends_at().is_some() {
                        control.reserve_scheduled_task(
                            state,
                            &candidates,
                            group_id.clone(),
                            decision.clone(),
                            timestamp,
                        )?;
                        append_scheduling_created(
                            events,
                            &group_id,
                            &decision,
                            timestamp,
                            correlation_id,
                        );
                        was_scheduled = true;
                    }
                    decision
                }
                TaskSchedulingOutcome::SelectedFuture(decision) => {
                    self.clear_scheduling_deferral(mission_id, task_ref);
                    events.append(
                        timestamp,
                        correlation_id,
                        None,
                        domain::EventPayload::TaskSchedulingSelected {
                            task_ref: task_ref.clone(),
                            assignments: decision.proposed_assignments(),
                        },
                    );
                    control.reserve_scheduled_task(
                        state,
                        &candidates,
                        group_id.clone(),
                        decision.clone(),
                        timestamp,
                    )?;
                    append_scheduling_created(
                        events,
                        &group_id,
                        &decision,
                        timestamp,
                        correlation_id,
                    );
                    return control
                        .group(&group_id)
                        .and_then(|group| group.task_execution(task_ref))
                        .cloned()
                        .ok_or_else(|| {
                            OrchestrationError::Mission(
                                "scheduled TaskExecution disappeared".to_string(),
                            )
                        });
                }
                TaskSchedulingOutcome::Deferred => {
                    self.record_scheduling_deferral(
                        mission_id,
                        task_ref,
                        "no-feasible-interval",
                        timestamp,
                        correlation_id,
                        events,
                    )?;
                    return Err(OrchestrationError::Mission(
                        "joint scheduling deferred: no feasible interval".to_string(),
                    ));
                }
                TaskSchedulingOutcome::WindowMissed => {
                    self.record_scheduling_deferral(
                        mission_id,
                        task_ref,
                        "window-missed",
                        timestamp,
                        correlation_id,
                        events,
                    )?;
                    return Err(OrchestrationError::Mission(
                        "joint scheduling window missed".to_string(),
                    ));
                }
            }
        };
        if !had_scheduled_decision {
            events.append(
                timestamp,
                correlation_id,
                None,
                domain::EventPayload::TaskSchedulingSelected {
                    task_ref: task_ref.clone(),
                    assignments: decision.proposed_assignments(),
                },
            );
        }
        let proposal = match control.propose(
            state,
            &requirement,
            &candidates,
            decision.proposed_assignments(),
            timestamp,
            correlation_id,
            events,
        ) {
            Ok(proposal) => proposal,
            Err(error) if was_scheduled => {
                let reason = error.to_string();
                control.invalidate_scheduled_task(task_ref, reason.clone());
                events.append(
                    timestamp,
                    correlation_id,
                    None,
                    domain::EventPayload::SchedulingReservationInvalidated {
                        group_id: group_id.clone(),
                        task_ref: task_ref.clone(),
                        reason,
                    },
                );
                return Err(OrchestrationError::Mission(
                    "joint scheduling deferred after activation revalidation".to_string(),
                ));
            }
            Err(error) => return Err(error.into()),
        };
        let committed =
            match control.commit_for_group(&group_id, &proposal, timestamp, correlation_id, events)
            {
                Ok(committed) => committed,
                Err(error) if was_scheduled => {
                    let reason = error.to_string();
                    control.invalidate_scheduled_task(task_ref, reason.clone());
                    events.append(
                        timestamp,
                        correlation_id,
                        None,
                        domain::EventPayload::SchedulingReservationInvalidated {
                            group_id: group_id.clone(),
                            task_ref: task_ref.clone(),
                            reason,
                        },
                    );
                    return Err(OrchestrationError::Mission(
                        "joint scheduling deferred after activation conflict".to_string(),
                    ));
                }
                Err(error) => return Err(error.into()),
            };
        control.bind_task_execution_with_requirement(
            &group_id,
            &committed,
            &requirement,
            timestamp,
            correlation_id,
            events,
        )?;
        control
            .group(&group_id)
            .and_then(|group| group.task_execution(task_ref))
            .cloned()
            .ok_or_else(|| OrchestrationError::Mission("TaskExecution disappeared".to_string()))
    }

    /// Applies a successful Runtime Task outcome and explicitly evaluates the complete DAG.
    pub fn task_succeeded<E: EventSink>(
        &mut self,
        mission_id: &MissionId,
        task_ref: &TaskRef,
        control: &mut ControlPlane,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<(), OrchestrationError> {
        let group_id = self.group_for_task(mission_id, task_ref)?.clone();
        let task_resources = control
            .group(&group_id)
            .and_then(|group| group.task_execution(task_ref))
            .ok_or_else(|| OrchestrationError::Mission("TaskExecution is absent".to_string()))?
            .assignments()
            .iter()
            .flat_map(|assignment| assignment.resource_ids())
            .filter(|resource_id| {
                control
                    .group(&group_id)
                    .and_then(|group| group.task_execution(task_ref))
                    .is_some_and(|task| {
                        task.binding_scope(resource_id) == ResourceBindingScope::Task
                    })
            })
            .cloned()
            .collect::<Vec<ResourceId>>();
        control.complete_task_execution(&group_id, task_ref, timestamp, correlation_id, events)?;
        control.release_task_bindings(
            &group_id,
            task_ref,
            &task_resources,
            timestamp,
            correlation_id,
            events,
        )?;
        if control.scheduled_task(task_ref).is_some() {
            control.consume_scheduled_task(task_ref);
            events.append(
                timestamp,
                correlation_id,
                None,
                domain::EventPayload::SchedulingReservationReleased {
                    group_id: group_id.clone(),
                    task_ref: task_ref.clone(),
                    reason: "Task completed".to_string(),
                },
            );
        }
        self.refresh_ready(mission_id, control, timestamp, correlation_id, events)?;
        if self.plan_is_complete(mission_id, control)? {
            self.complete_mission(mission_id, control, timestamp, correlation_id, events)?;
        }
        Ok(())
    }

    /// Applies a final Task failure selected by Mission policy and releases the Group.
    #[allow(clippy::too_many_arguments)]
    pub fn task_failed<E: EventSink>(
        &mut self,
        mission_id: &MissionId,
        task_ref: &TaskRef,
        reason: impl Into<String>,
        control: &mut ControlPlane,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<(), OrchestrationError> {
        let group_id = self.group_for_task(mission_id, task_ref)?.clone();
        release_mission_scheduling(
            control,
            mission_id,
            "Mission failed",
            timestamp,
            correlation_id,
            events,
        );
        control.fail_task_execution(&group_id, task_ref, timestamp, correlation_id, events)?;
        if control.group(&group_id).is_some_and(|group| {
            matches!(
                group.lifecycle(),
                GroupLifecycle::Bound | GroupLifecycle::Active | GroupLifecycle::Adapted
            )
        }) {
            control.block_group(&group_id, reason.into(), timestamp, correlation_id, events)?;
        }
        control.fail_group(
            &group_id,
            "Mission execution policy declared final failure",
            timestamp,
            correlation_id,
            events,
        )?;
        control.release_group(&group_id, timestamp, correlation_id, events)?;
        self.executions
            .get_mut(mission_id)
            .expect("Mission validated above")
            .lifecycle = MissionExecutionLifecycle::Failed;
        Ok(())
    }

    /// Applies an explicit Mission cancellation and releases its long-lived Group.
    pub fn cancel<E: EventSink>(
        &mut self,
        mission_id: &MissionId,
        control: &mut ControlPlane,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<(), OrchestrationError> {
        let execution = self
            .executions
            .get(mission_id)
            .ok_or_else(|| OrchestrationError::Mission(format!("unknown Mission {mission_id}")))?;
        if matches!(
            execution.lifecycle(),
            MissionExecutionLifecycle::Completed
                | MissionExecutionLifecycle::Failed
                | MissionExecutionLifecycle::Cancelled
        ) {
            return Err(OrchestrationError::Mission(
                "Mission is already terminal".to_string(),
            ));
        }
        let group_id = execution.group_id().clone();
        release_mission_scheduling(
            control,
            mission_id,
            "Mission cancelled",
            timestamp,
            correlation_id,
            events,
        );
        let group_is_blocked = control
            .group(&group_id)
            .ok_or_else(|| OrchestrationError::Mission("Mission Group is absent".to_string()))?
            .lifecycle()
            == GroupLifecycle::Blocked;
        if !group_is_blocked {
            control.block_group(
                &group_id,
                "Mission cancellation requested",
                timestamp,
                correlation_id,
                events,
            )?;
        }
        control.fail_group(
            &group_id,
            "Mission cancelled",
            timestamp,
            correlation_id,
            events,
        )?;
        for context in execution.plan().contexts() {
            control.release_context_bindings(
                &group_id,
                context.context_id(),
                timestamp,
                correlation_id,
                events,
            )?;
        }
        control.release_group(&group_id, timestamp, correlation_id, events)?;
        self.executions
            .get_mut(mission_id)
            .expect("Mission validated above")
            .lifecycle = MissionExecutionLifecycle::Cancelled;
        Ok(())
    }

    /// Persists a cancellation request while retaining the Group for in-flight attempts.
    pub fn request_cancel<E: EventSink>(
        &mut self,
        mission_id: &MissionId,
        control: &mut ControlPlane,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<(), OrchestrationError> {
        let execution = self
            .executions
            .get(mission_id)
            .ok_or_else(|| OrchestrationError::Mission(format!("unknown Mission {mission_id}")))?;
        if matches!(
            execution.lifecycle(),
            MissionExecutionLifecycle::Completed
                | MissionExecutionLifecycle::Failed
                | MissionExecutionLifecycle::Cancelled
        ) {
            return Err(OrchestrationError::Mission(
                "Mission is already terminal".to_string(),
            ));
        }
        let group_id = execution.group_id().clone();
        release_mission_scheduling(
            control,
            mission_id,
            "Mission cancellation requested",
            timestamp,
            correlation_id,
            events,
        );
        if control
            .group(&group_id)
            .is_some_and(|group| group.lifecycle() != GroupLifecycle::Blocked)
        {
            control.block_group(
                &group_id,
                "Mission cancellation requested",
                timestamp,
                correlation_id,
                events,
            )?;
        }
        self.executions
            .get_mut(mission_id)
            .expect("Mission validated above")
            .lifecycle = MissionExecutionLifecycle::Cancelling;
        Ok(())
    }

    /// Finalizes a durable cancellation once no physical attempt remains active.
    pub fn finalize_cancel<E: EventSink>(
        &mut self,
        mission_id: &MissionId,
        control: &mut ControlPlane,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<(), OrchestrationError> {
        let execution = self
            .executions
            .get(mission_id)
            .ok_or_else(|| OrchestrationError::Mission(format!("unknown Mission {mission_id}")))?;
        if execution.lifecycle() != MissionExecutionLifecycle::Cancelling {
            return Err(OrchestrationError::Mission(
                "Mission is not cancelling".to_string(),
            ));
        }
        let group_id = execution.group_id().clone();
        control.fail_group(
            &group_id,
            "Mission cancelled",
            timestamp,
            correlation_id,
            events,
        )?;
        for context in execution.plan().contexts() {
            control.release_context_bindings(
                &group_id,
                context.context_id(),
                timestamp,
                correlation_id,
                events,
            )?;
        }
        control.release_group(&group_id, timestamp, correlation_id, events)?;
        self.executions
            .get_mut(mission_id)
            .expect("Mission validated above")
            .lifecycle = MissionExecutionLifecycle::Cancelled;
        Ok(())
    }

    /// Marks newly dependency-satisfied Tasks Ready using the complete immutable plan.
    fn refresh_ready<E: EventSink>(
        &mut self,
        mission_id: &MissionId,
        control: &mut ControlPlane,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<(), OrchestrationError> {
        let execution = self
            .executions
            .get(mission_id)
            .ok_or_else(|| OrchestrationError::Mission(format!("unknown Mission {mission_id}")))?;
        let group_id = execution.group_id().clone();
        let completed = control
            .group(&group_id)
            .ok_or_else(|| OrchestrationError::Mission("Mission Group is absent".to_string()))?
            .task_executions()
            .filter(|task| task.lifecycle() == TaskExecutionLifecycle::Completed)
            .map(|task| task.task_ref().task_id().clone())
            .collect::<BTreeSet<TaskId>>();
        let ready = execution
            .plan()
            .task_graph()
            .ready_tasks(&completed)
            .into_iter()
            .map(|task| task.requirement().task_ref().clone())
            .collect::<Vec<_>>();
        for task_ref in ready {
            let is_pending = control
                .group(&group_id)
                .and_then(|group| group.task_execution(&task_ref))
                .is_some_and(|task| task.lifecycle() == TaskExecutionLifecycle::Pending);
            if is_pending {
                control.ready_task_execution(
                    &group_id,
                    &task_ref,
                    timestamp,
                    correlation_id,
                    events,
                )?;
            }
        }
        self.executions
            .get_mut(mission_id)
            .expect("Mission validated above")
            .lifecycle = MissionExecutionLifecycle::Running;
        Ok(())
    }

    /// Returns the Group only when the Task belongs to the accepted complete plan.
    fn group_for_task(
        &self,
        mission_id: &MissionId,
        task_ref: &TaskRef,
    ) -> Result<&ExecutionGroupId, OrchestrationError> {
        let execution = self
            .executions
            .get(mission_id)
            .ok_or_else(|| OrchestrationError::Mission(format!("unknown Mission {mission_id}")))?;
        if task_ref.mission_id() != mission_id
            || !execution
                .plan()
                .task_graph()
                .tasks()
                .iter()
                .any(|task| task.requirement().task_ref() == task_ref)
        {
            return Err(OrchestrationError::Mission(
                "Task is absent from the accepted MissionPlan".to_string(),
            ));
        }
        Ok(execution.group_id())
    }

    /// Evaluates Mission completion against every Task in the accepted plan.
    fn plan_is_complete(
        &self,
        mission_id: &MissionId,
        control: &ControlPlane,
    ) -> Result<bool, OrchestrationError> {
        let execution = self
            .executions
            .get(mission_id)
            .ok_or_else(|| OrchestrationError::Mission(format!("unknown Mission {mission_id}")))?;
        let group = control
            .group(execution.group_id())
            .ok_or_else(|| OrchestrationError::Mission("Mission Group is absent".to_string()))?;
        Ok(execution.plan().task_graph().tasks().iter().all(|planned| {
            group
                .task_execution(planned.requirement().task_ref())
                .is_some_and(|task| task.lifecycle() == TaskExecutionLifecycle::Completed)
        }))
    }

    /// Ends Context bindings and releases the Group after explicit full-plan completion.
    fn complete_mission<E: EventSink>(
        &mut self,
        mission_id: &MissionId,
        control: &mut ControlPlane,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<(), OrchestrationError> {
        let execution = self
            .executions
            .get(mission_id)
            .ok_or_else(|| OrchestrationError::Mission(format!("unknown Mission {mission_id}")))?
            .clone();
        for context in execution.plan().contexts() {
            control.release_context_bindings(
                execution.group_id(),
                context.context_id(),
                timestamp,
                correlation_id,
                events,
            )?;
        }
        control.complete_group(execution.group_id(), timestamp, correlation_id, events)?;
        control.release_group(execution.group_id(), timestamp, correlation_id, events)?;
        self.executions
            .get_mut(mission_id)
            .expect("Mission validated above")
            .lifecycle = MissionExecutionLifecycle::Completed;
        Ok(())
    }
}

/// Validates that every relative Task time remains representable after acceptance anchoring.
fn validate_plan_timing_anchor(
    plan: &MissionPlan,
    accepted_at: TimestampMs,
) -> Result<(), OrchestrationError> {
    for task in plan.task_graph().tasks() {
        let timing = task.requirement().timing();
        let offsets = [
            Some(timing.earliest_start_offset_ms()),
            timing.latest_start_offset_ms(),
            timing.completion_deadline_offset_ms(),
        ];
        if offsets
            .into_iter()
            .flatten()
            .any(|offset| accepted_at.as_millis().checked_add(offset).is_none())
        {
            return Err(OrchestrationError::Mission(format!(
                "Task {} timing exceeds Controller timestamp range",
                task.requirement().task_ref()
            )));
        }
        if timing.estimated_duration_ms().is_some_and(|duration| {
            accepted_at
                .as_millis()
                .checked_add(timing.earliest_start_offset_ms())
                .and_then(|earliest| earliest.checked_add(duration))
                .is_none()
        }) {
            return Err(OrchestrationError::Mission(format!(
                "Task {} duration exceeds Controller timestamp range",
                task.requirement().task_ref()
            )));
        }
    }
    Ok(())
}

/// Adds one Task timing offset to a Controller timestamp with a stable Mission diagnostic.
fn checked_plan_timestamp(
    timestamp: TimestampMs,
    offset_ms: u64,
    task_ref: &TaskRef,
) -> Result<TimestampMs, OrchestrationError> {
    timestamp
        .as_millis()
        .checked_add(offset_ms)
        .map(TimestampMs::new)
        .ok_or_else(|| {
            OrchestrationError::Mission(format!(
                "Task {task_ref} timing exceeds Controller timestamp range"
            ))
        })
}

/// Recomputes the inclusive activation bound from one accepted Task timing declaration.
fn plan_latest_activation_at(
    timing: &domain::TaskTiming,
    accepted_at: TimestampMs,
    task_ref: &TaskRef,
) -> Result<Option<TimestampMs>, OrchestrationError> {
    let latest_start = timing
        .latest_start_offset_ms()
        .map(|offset| checked_plan_timestamp(accepted_at, offset, task_ref))
        .transpose()?;
    let completion_start = timing
        .completion_deadline_offset_ms()
        .map(|offset| checked_plan_timestamp(accepted_at, offset, task_ref))
        .transpose()?
        .map(|deadline| {
            TimestampMs::new(
                deadline.as_millis()
                    - timing
                        .estimated_duration_ms()
                        .expect("TaskTiming requires duration with completion deadline"),
            )
        });
    Ok(match (latest_start, completion_start) {
        (Some(latest), Some(completion)) => Some(latest.min(completion)),
        (Some(latest), None) => Some(latest),
        (None, Some(completion)) => Some(completion),
        (None, None) => None,
    })
}

/// Emits durable creation evidence after Control admits one bounded scheduling interval.
fn append_scheduling_created<E: EventSink>(
    events: &mut E,
    group_id: &ExecutionGroupId,
    decision: &control::TaskSchedulingDecision,
    timestamp: TimestampMs,
    correlation_id: &CorrelationId,
) {
    let ends_at = decision
        .ends_at()
        .expect("Control admits scheduling reservations only with a bounded end");
    events.append(
        timestamp,
        correlation_id,
        None,
        domain::EventPayload::SchedulingReservationCreated {
            group_id: group_id.clone(),
            task_ref: decision.task_ref().clone(),
            starts_at: decision.starts_at(),
            ends_at,
            snapshot_version: decision.snapshot_version(),
        },
    );
}

/// Emits one nonterminal Ready-Task scheduling outcome for operator inspection.
fn append_scheduling_deferred<E: EventSink>(
    events: &mut E,
    task_ref: &TaskRef,
    reason: &str,
    timestamp: TimestampMs,
    correlation_id: &CorrelationId,
) {
    events.append(
        timestamp,
        correlation_id,
        None,
        domain::EventPayload::TaskSchedulingDeferred {
            task_ref: task_ref.clone(),
            reason: reason.to_string(),
        },
    );
}

/// Releases every Mission calendar record and emits one event per removed interval.
fn release_mission_scheduling<E: EventSink>(
    control: &mut ControlPlane,
    mission_id: &MissionId,
    reason: &str,
    timestamp: TimestampMs,
    correlation_id: &CorrelationId,
    events: &mut E,
) {
    let released = control
        .scheduled_tasks()
        .filter(|reservation| reservation.task_ref().mission_id() == mission_id)
        .map(|reservation| {
            (
                reservation.group_id().clone(),
                reservation.task_ref().clone(),
            )
        })
        .collect::<Vec<_>>();
    control.cancel_scheduled_mission(mission_id);
    for (group_id, task_ref) in released {
        events.append(
            timestamp,
            correlation_id,
            None,
            domain::EventPayload::SchedulingReservationReleased {
                group_id,
                task_ref,
                reason: reason.to_string(),
            },
        );
    }
}

/// Serializes a validated domain MissionPlan into the v0.5 wire shape without typed JSON map keys.
fn mission_plan_json(plan: &MissionPlan) -> serde_json::Value {
    let contexts = plan
        .contexts()
        .iter()
        .map(|context| {
            let mut value = serde_json::json!({
                "id": context.context_id().as_str(),
                "roles": context.roles().iter().map(|role| serde_json::json!({
                    "id": role.context_role_id().as_str(),
                    "actor": role.actor_id().as_str(),
                })).collect::<Vec<_>>(),
                "coupling_mode": coupling_mode_name(context.coupling_mode()),
                "relations": context.relations().iter().map(relation_json).collect::<Vec<_>>(),
            });
            let object = value
                .as_object_mut()
                .expect("coordination Context JSON is an object");
            if let Some(view) = context.shared_view() {
                object.insert("shared_view".to_string(), shared_view_json(view));
            }
            if let Some(channel) = context.peer_channel() {
                object.insert("peer_channel".to_string(), peer_channel_json(channel));
            }
            value
        })
        .collect::<Vec<_>>();
    let tasks = plan
        .task_graph()
        .tasks()
        .iter()
        .map(|task| {
            let roles = task
                .requirement()
                .roles()
                .iter()
                .map(|role| {
                    let intent = task
                        .execution_intent(role.role_id())
                        .expect("validated MissionPlan role intent");
                    let contract = role
                        .required_contract()
                        .unwrap_or_else(|| intent.capability_contract());
                    let scope = match task.continuity().resource_scope(role.role_id()) {
                        domain::ResourceBindingScope::Task => "task",
                        domain::ResourceBindingScope::Context => "context",
                    };
                    serde_json::json!({
                        "id": role.role_id().as_str(),
                        "actor": role.actor_id().map(|actor| actor.as_str()).unwrap_or("anonymous"),
                        "capability": format!("{:?}", role.capability()).to_lowercase(),
                        "contract": contract_json(contract),
                        "resources": role.resource_requirements().iter().map(|resource| serde_json::json!({
                            "kind": format!("{:?}", resource.kind()).to_lowercase(),
                            "units": resource.units(),
                        })).collect::<Vec<_>>(),
                        "context_role": task.continuity().context_role(role.role_id()).map(|id| id.as_str()),
                        "resource_scope": scope,
                        "execution": {
                            "capability_contract": contract_json(intent.capability_contract()),
                            "parameters": intent.parameters().iter().map(|(key, value)| (key.clone(), execution_value_json(value))).collect::<serde_json::Map<_,_>>(),
                        }
                    })
                })
                .collect::<Vec<_>>();
            let mut value = serde_json::json!({
                "id": task.task_id().as_str(),
                "description": task.description(),
                "context_id": task.continuity().context_id().as_str(),
                "depends_on": task.dependencies().iter().map(|id| id.as_str()).collect::<Vec<_>>(),
                "roles": roles,
                "timing": {
                    "earliest_start_offset_ms": task.requirement().timing().earliest_start_offset_ms(),
                    "latest_start_offset_ms": task.requirement().timing().latest_start_offset_ms(),
                    "completion_deadline_offset_ms": task.requirement().timing().completion_deadline_offset_ms(),
                    "estimated_duration_ms": task.requirement().timing().estimated_duration_ms(),
                },
            });
            if let Some(mode) = task.continuity().coupling_mode_override() {
                value
                    .as_object_mut()
                    .expect("Task JSON is an object")
                    .insert(
                        "coupling_mode".to_string(),
                        serde_json::json!(coupling_mode_name(mode)),
                    );
            }
            value
        })
        .collect::<Vec<_>>();
    serde_json::json!({
        "schema_version": domain::MISSION_PLAN_SCHEMA_V0_5,
        "mission": {"id": plan.goal().mission_id().as_str(), "objective": plan.goal().objective()},
        "contexts": contexts,
        "tasks": tasks,
    })
}

/// Serializes one typed relation while retaining its closed family and reserved fields.
fn relation_json(relation: &domain::ExecutionRelationSpec) -> serde_json::Value {
    let mut value = serde_json::json!({
        "id": relation.relation_id().as_str(),
        "kind": relation_kind_name(relation.kind()),
        "source": {"task_id": relation.source().task_id().as_str(), "role_id": relation.source().role_id().as_str()},
        "target": {"task_id": relation.target().task_id().as_str(), "role_id": relation.target().role_id().as_str()},
    });
    let object = value.as_object_mut().expect("relation JSON is an object");
    match relation.relation_type() {
        domain::ExecutionRelationType::GroupMemberState { state_key }
        | domain::ExecutionRelationType::StateRequirement { state_key, .. }
        | domain::ExecutionRelationType::FreshnessRequirement { state_key, .. } => {
            object.insert("state_key".to_string(), serde_json::json!(state_key));
        }
        domain::ExecutionRelationType::SharedSpatialReference { reference } => {
            object.insert("reference".to_string(), spatial_reference_json(reference));
        }
        domain::ExecutionRelationType::RelativePose { frame_id }
        | domain::ExecutionRelationType::RelativeDistance { frame_id } => {
            object.insert("frame_id".to_string(), serde_json::json!(frame_id));
        }
        domain::ExecutionRelationType::RequiresActive => {}
    }
    if let domain::ExecutionRelationType::StateRequirement { requirement, .. } =
        relation.relation_type()
    {
        object.insert(
            "requirement".to_string(),
            serde_json::json!(match requirement {
                domain::RelationStateRequirement::Available => "available",
                domain::RelationStateRequirement::Unavailable => "unavailable",
            }),
        );
    }
    if let domain::ExecutionRelationType::FreshnessRequirement { policy, .. } =
        relation.relation_type()
    {
        object.insert("policy_id".to_string(), serde_json::json!(policy.policy_id));
    }
    value
}

/// Serializes one Context coupling mode with its stable wire spelling.
fn coupling_mode_name(mode: domain::ExecutionCouplingMode) -> &'static str {
    match mode {
        domain::ExecutionCouplingMode::Independent => "independent",
        domain::ExecutionCouplingMode::SequentialHandoff => "sequential-handoff",
        domain::ExecutionCouplingMode::ConcurrentCooperation => "concurrent-cooperation",
        domain::ExecutionCouplingMode::TightlyCoupledCooperation => "tightly-coupled-cooperation",
    }
}

/// Serializes a selective Group shared view declaration.
fn shared_view_json(view: &domain::GroupSharedViewSpec) -> serde_json::Value {
    let mut value = serde_json::json!({
        "bindings": view.bindings().iter().map(group_view_binding_json).collect::<Vec<_>>(),
        "include_freshness": view.include_freshness(),
    });
    if let Some(reference) = view.spatial_reference() {
        value
            .as_object_mut()
            .expect("Group shared view JSON is an object")
            .insert(
                "spatial_reference".to_string(),
                spatial_reference_json(reference),
            );
    }
    value
}

/// Serializes one State-backed or Runtime-backed Group view binding.
fn group_view_binding_json(binding: &domain::GroupViewBinding) -> serde_json::Value {
    let mut value = serde_json::json!({
        "context_role_id": binding.context_role_id().as_str(),
        "field": group_view_field_name(binding.field()),
    });
    let object = value
        .as_object_mut()
        .expect("Group view binding JSON is an object");
    if let Some(state_export_id) = binding.state_export_id() {
        object.insert(
            "state_export_id".to_string(),
            serde_json::json!(state_export_id),
        );
    }
    if let Some(payload_schema) = binding.payload_schema() {
        object.insert(
            "payload_schema".to_string(),
            serde_json::json!(payload_schema),
        );
    }
    value
}

/// Returns the stable wire spelling for a Group view field.
fn group_view_field_name(field: domain::GroupViewField) -> &'static str {
    match field {
        domain::GroupViewField::Pose => "pose",
        domain::GroupViewField::Velocity => "velocity",
        domain::GroupViewField::Execution => "execution",
    }
}

/// Serializes a shared map/frame reference.
fn spatial_reference_json(reference: &domain::SharedSpatialReference) -> serde_json::Value {
    serde_json::json!({
        "map_id": reference.selector().map_id().as_str(),
        "revision_id": reference.selector().revision_id().as_str(),
        "frame_id": reference.frame_id(),
    })
}

/// Serializes a transport-neutral peer channel descriptor.
fn peer_channel_json(channel: &domain::PeerChannelSpec) -> serde_json::Value {
    serde_json::json!({"profile_id": channel.profile_id, "message_schema": channel.message_schema})
}

/// Returns the stable wire spelling for one relation family.
fn relation_kind_name(kind: domain::ExecutionRelationKind) -> &'static str {
    match kind {
        domain::ExecutionRelationKind::RequiresActive => "requires-active",
        domain::ExecutionRelationKind::GroupMemberState => "group-member-state",
        domain::ExecutionRelationKind::SharedSpatialReference => "shared-spatial-reference",
        domain::ExecutionRelationKind::RelativePose => "relative-pose",
        domain::ExecutionRelationKind::RelativeDistance => "relative-distance",
        domain::ExecutionRelationKind::StateRequirement => "state-requirement",
        domain::ExecutionRelationKind::FreshnessRequirement => "freshness-requirement",
    }
}

/// Serializes one canonical capability contract into contract JSON.
fn contract_json(contract: &domain::CapabilityContractRef) -> serde_json::Value {
    serde_json::json!({"namespace": contract.namespace(), "name": contract.name(), "version": contract.version()})
}

/// Serializes one scalar execution value without introducing adapter-specific types.
fn execution_value_json(value: &domain::ExecutionValue) -> serde_json::Value {
    match value {
        domain::ExecutionValue::Bool(value) => serde_json::Value::Bool(*value),
        domain::ExecutionValue::Integer(value) => serde_json::json!(value),
        domain::ExecutionValue::Float(value) => serde_json::json!(value),
        domain::ExecutionValue::String(value) => serde_json::Value::String(value.clone()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use domain::{
        Capability, CapabilityContractRef, CapabilityKind, CoordinationContextId, LocalRuntime,
        LocalSystemDescriptor, LocalSystemId, NodeContractVersion, NodeHealth, NodeId,
        NodeRegistration, NodeStatus, Resource, ResourceId, ResourceKind,
    };
    use state::InMemorySharedNodeState;
    use testkit::InMemoryEventLog;

    /// Legacy MissionPlan v0.3 decodes one Node-independent relation and normalizes to v0.5.
    #[test]
    fn execution_relation_fixture_decodes_logical_endpoints() {
        let source = include_str!("../../../scenarios/execution-relations-v0.1/mission-plan.json");
        let plan = decode_mission_plan(source).expect("relation fixture should validate");
        let relation = &plan.contexts()[0].relations()[0];
        assert_eq!(relation.relation_id().as_str(), "safety-guards-navigation");
        assert_eq!(relation.source().task_id().as_str(), "observe-safety");
        assert_eq!(relation.target().role_id().as_str(), "navigator");
        assert_eq!(
            relation.kind(),
            domain::ExecutionRelationKind::RequiresActive
        );
    }

    /// Relation endpoints must exist in one Context and remain concurrently runnable in the DAG.
    #[test]
    fn execution_relation_rejects_unknown_or_dag_ordered_endpoints() {
        let source = include_str!("../../../scenarios/execution-relations-v0.1/mission-plan.json");
        let mut unknown: serde_json::Value = serde_json::from_str(source).expect("fixture is JSON");
        unknown["contexts"][0]["relations"][0]["source"]["role_id"] =
            serde_json::json!("missing-role");
        assert!(
            decode_mission_plan(&unknown.to_string())
                .expect_err("unknown relation role must fail")
                .to_string()
                .contains("unknown Role")
        );

        let mut ordered: serde_json::Value = serde_json::from_str(source).expect("fixture is JSON");
        ordered["tasks"][1]["depends_on"] = serde_json::json!(["observe-safety"]);
        assert!(
            decode_mission_plan(&ordered.to_string())
                .expect_err("DAG-ordered relation must fail")
                .to_string()
                .contains("ordered by the DAG")
        );
    }

    /// v0.4 preserves coupling declarations and typed relation metadata at the JSON boundary.
    #[test]
    fn v0_4_decodes_coupling_and_typed_relation() {
        let source = include_str!("../../../scenarios/execution-relations-v0.1/mission-plan.json");
        let mut document: serde_json::Value =
            serde_json::from_str(source).expect("fixture is JSON");
        document["schema_version"] = serde_json::json!(domain::MISSION_PLAN_SCHEMA_V0_4);
        document["contexts"][0]["coupling_mode"] = serde_json::json!("concurrent-cooperation");
        document["contexts"][0]["shared_view"] = serde_json::json!({
            "bindings": [
                {
                    "context_role_id": "safety",
                    "field": "pose",
                    "state_export_id": "safety-pose",
                    "payload_schema": "roboguide.pose/v1"
                },
                {"context_role_id": "guide", "field": "execution"}
            ],
            "include_freshness": true,
            "spatial_reference": {"map_id": "campus", "revision_id": "r1", "frame_id": "map"}
        });
        document["contexts"][0]["relations"][0] = serde_json::json!({
            "id": "safety-guards-navigation",
            "kind": "state-requirement",
            "state_key": "hazard",
            "requirement": "available",
            "source": {"task_id": "observe-safety", "role_id": "safety-observer"},
            "target": {"task_id": "navigate", "role_id": "navigator"}
        });
        let plan = decode_mission_plan(&document.to_string()).expect("v0.4 plan should validate");
        assert_eq!(
            plan.contexts()[0].coupling_mode(),
            domain::ExecutionCouplingMode::ConcurrentCooperation
        );
        assert!(plan.contexts()[0].shared_view().is_some());
        assert!(matches!(
            plan.contexts()[0].relations()[0].relation_type(),
            domain::ExecutionRelationType::StateRequirement { .. }
        ));

        let encoded = mission_plan_json(&plan);
        let execution_binding = &encoded["contexts"][0]["shared_view"]["bindings"][1];
        assert!(execution_binding.get("state_export_id").is_none());
        assert!(execution_binding.get("payload_schema").is_none());
        let decoded = decode_mission_plan(&encoded.to_string())
            .expect("canonical v0.4 MissionPlan should round trip");
        assert_eq!(decoded, plan);
    }

    /// Implementation preflight rejects future typed syntax before Control creates a Group.
    #[test]
    fn unsupported_relation_never_reaches_control_authority() {
        let source = include_str!("../../../scenarios/execution-relations-v0.1/mission-plan.json");
        let mut document: serde_json::Value =
            serde_json::from_str(source).expect("fixture is JSON");
        document["schema_version"] = serde_json::json!(domain::MISSION_PLAN_SCHEMA_V0_4);
        document["contexts"][0]["coupling_mode"] = serde_json::json!("concurrent-cooperation");
        document["contexts"][0]["shared_view"] = serde_json::json!({
            "bindings": [{"context_role_id": "guide", "field": "execution"}],
            "include_freshness": false
        });
        document["contexts"][0]["relations"][0] = serde_json::json!({
            "id": "relative-guidance",
            "kind": "relative-pose",
            "frame_id": "map",
            "source": {"task_id": "observe-safety", "role_id": "safety-observer"},
            "target": {"task_id": "navigate", "role_id": "navigator"}
        });
        let plan = decode_mission_plan(&document.to_string()).expect("contract syntax is valid");
        let mut orchestrator = MissionOrchestrator::new();
        let mut control = ControlPlane::new();
        let mut events = InMemoryEventLog::new();
        let result = orchestrator.submit(
            plan,
            ExecutionGroupId::new("group-unsupported").expect("group id is valid"),
            &mut control,
            TimestampMs::new(1),
            &CorrelationId::new("unsupported-profile").expect("correlation is valid"),
            &mut events,
        );

        assert!(matches!(
            result,
            Err(OrchestrationError::Mission(reason))
                if reason.contains("valid contract syntax but is not executable")
        ));
        assert!(control.group_ids().is_empty());
        assert!(events.records().is_empty());
    }

    /// v0.4 rejects a Task mode override whose Context lacks its static mechanisms.
    #[test]
    fn v0_4_rejects_unbacked_task_coupling_mode() {
        let source = include_str!("../../../scenarios/execution-relations-v0.1/mission-plan.json");
        let mut document: serde_json::Value =
            serde_json::from_str(source).expect("fixture is JSON");
        document["schema_version"] = serde_json::json!(domain::MISSION_PLAN_SCHEMA_V0_4);
        document["tasks"][1]["coupling_mode"] = serde_json::json!("tightly-coupled-cooperation");

        assert!(
            decode_mission_plan(&document.to_string())
                .expect_err("unbacked Task mode must fail acceptance")
                .to_string()
                .contains("requires a Group shared view")
        );
    }

    /// The Phase 1 fixture decodes into a four-Task DAG and preserves Context continuity metadata.
    #[test]
    fn phase1_fixture_contains_complete_dag_and_context() {
        let source = include_str!("../../../scenarios/phase1-mission-v0.3/mission-plan.json");
        let plan = decode_mission_plan(source).expect("Phase 1 MissionPlan should validate");
        assert_eq!(plan.schema_version(), domain::MISSION_PLAN_SCHEMA_V0_5);
        assert_eq!(plan.contexts().len(), 1);
        assert_eq!(plan.task_graph().tasks().len(), 4);
        assert_eq!(
            plan.task_graph().tasks()[1]
                .continuity()
                .resource_scope(plan.task_graph().tasks()[1].requirement().roles()[0].role_id()),
            domain::ResourceBindingScope::Context
        );
    }

    /// An exact submission retry returns existing authority without creating a second Group.
    #[test]
    fn exact_mission_submission_retry_is_idempotent() {
        let source = include_str!("../../../scenarios/phase1-mission-v0.3/mission-plan.json");
        let plan = decode_mission_plan(source).expect("Phase 1 MissionPlan should validate");
        let group_id = ExecutionGroupId::new("group-idempotent").expect("group id valid");
        let correlation = CorrelationId::new("idempotent-submit").expect("trace valid");
        let mut control = ControlPlane::new();
        let mut orchestrator = MissionOrchestrator::new();
        let mut events = InMemoryEventLog::new();
        orchestrator
            .submit(
                plan.clone(),
                group_id.clone(),
                &mut control,
                TimestampMs::new(1),
                &correlation,
                &mut events,
            )
            .expect("first submission creates authority");
        let event_count = events.records().len();

        let repeated = orchestrator
            .submit(
                plan,
                group_id,
                &mut control,
                TimestampMs::new(2),
                &correlation,
                &mut events,
            )
            .expect("exact retry returns existing authority");

        assert_eq!(repeated.lifecycle(), MissionExecutionLifecycle::Running);
        assert_eq!(orchestrator.mission_ids().len(), 1);
        assert_eq!(events.records().len(), event_count);
    }

    /// Restored orchestration rejects missing Groups, truncated DAGs, and lifecycle disagreement.
    #[test]
    fn restored_orchestration_cross_checks_control_authority() {
        let source = include_str!("../../../scenarios/phase1-mission-v0.3/mission-plan.json");
        let plan = decode_mission_plan(source).expect("Phase 1 MissionPlan should validate");
        let mission_id = plan.goal().mission_id().clone();
        let group_id = ExecutionGroupId::new("group-restore-authority").expect("group id valid");
        let correlation = CorrelationId::new("restore-authority-test").expect("trace valid");
        let mut control = ControlPlane::new();
        let mut events = InMemoryEventLog::new();
        control
            .create_mission_group(
                group_id.clone(),
                &plan,
                TimestampMs::new(1),
                &correlation,
                &mut events,
            )
            .expect("orphan fixture Group is created");
        assert!(
            MissionOrchestrator::new()
                .validate_control_authority(&control)
                .expect_err("orphan Mission Group must fail closed")
                .to_string()
                .contains("no orchestration authority")
        );
        let mut control = ControlPlane::new();
        let mut orchestrator = MissionOrchestrator::new();
        orchestrator
            .submit(
                plan,
                group_id,
                &mut control,
                TimestampMs::new(1),
                &correlation,
                &mut events,
            )
            .expect("Mission authority is created");
        orchestrator
            .validate_control_authority(&control)
            .expect("matching projections validate");

        let checkpoint = orchestrator
            .checkpoint_json()
            .expect("orchestration checkpoint serializes");
        let mut missing_group: serde_json::Value =
            serde_json::from_str(&checkpoint).expect("checkpoint is JSON");
        missing_group[0]["group_id"] = serde_json::json!("group-other");
        let restored = MissionOrchestrator::restore_json(&missing_group.to_string())
            .expect("syntactically valid checkpoint restores before authority validation");
        assert!(matches!(
            restored.validate_control_authority(&control),
            Err(OrchestrationError::Control(ControlError::UnknownGroup(_)))
        ));

        let mut truncated_dag: serde_json::Value =
            serde_json::from_str(&checkpoint).expect("checkpoint is JSON");
        truncated_dag[0]["plan"]["tasks"]
            .as_array_mut()
            .expect("tasks remain an array")
            .pop();
        let restored = MissionOrchestrator::restore_json(&truncated_dag.to_string())
            .expect("shorter valid DAG restores before authority validation");
        assert!(
            restored
                .validate_control_authority(&control)
                .expect_err("Control must reject a truncated restored DAG")
                .to_string()
                .contains("Task DAG differs")
        );

        let mut false_terminal: serde_json::Value =
            serde_json::from_str(&checkpoint).expect("checkpoint is JSON");
        false_terminal[0]["lifecycle"] = serde_json::json!("Completed");
        let restored = MissionOrchestrator::restore_json(&false_terminal.to_string())
            .expect("known lifecycle restores before authority validation");
        assert!(
            restored
                .validate_control_authority(&control)
                .expect_err("unreleased Group must reject a completed Mission projection")
                .to_string()
                .contains(&mission_id.to_string())
        );
    }

    /// Both directions of the Spatial Memory experiment remain valid legacy plans.
    #[test]
    fn distributed_spatial_memory_fixtures_decode_in_both_directions() {
        let fixtures = [
            include_str!(
                "../../../scenarios/distributed-spatial-memory-v0.1/mission-a-build-publish.json"
            ),
            include_str!(
                "../../../scenarios/distributed-spatial-memory-v0.1/mission-b-import-verify.json"
            ),
            include_str!(
                "../../../scenarios/distributed-spatial-memory-v0.1/mission-b-build-publish.json"
            ),
            include_str!(
                "../../../scenarios/distributed-spatial-memory-v0.1/mission-a-import-verify.json"
            ),
        ];
        for fixture in fixtures {
            let plan =
                decode_mission_plan(fixture).expect("Spatial Memory fixture should validate");
            assert_eq!(plan.schema_version(), domain::MISSION_PLAN_SCHEMA_V0_5);
            assert_eq!(plan.contexts().len(), 1);
            assert_eq!(plan.task_graph().tasks().len(), 2);
        }
    }

    /// The four Spatial Memory fixtures bind to two distinct physical nodes under Control policy.
    #[test]
    fn distributed_spatial_memory_actor_placement_drives_two_node_assignments() {
        let fixtures = [
            (
                include_str!(
                    "../../../scenarios/distributed-spatial-memory-v0.1/mission-a-build-publish.json"
                ),
                "robot-dog-a",
                "dog-a",
            ),
            (
                include_str!(
                    "../../../scenarios/distributed-spatial-memory-v0.1/mission-b-import-verify.json"
                ),
                "robot-dog-b",
                "dog-b",
            ),
            (
                include_str!(
                    "../../../scenarios/distributed-spatial-memory-v0.1/mission-b-build-publish.json"
                ),
                "robot-dog-b",
                "dog-b",
            ),
            (
                include_str!(
                    "../../../scenarios/distributed-spatial-memory-v0.1/mission-a-import-verify.json"
                ),
                "robot-dog-a",
                "dog-a",
            ),
        ];
        let contracts = [
            (
                CapabilityContractRef::new("spatial.map", "build", "v0").expect("contract valid"),
                CapabilityKind::Compute,
            ),
            (
                CapabilityContractRef::new("spatial.map", "publish", "v0").expect("contract valid"),
                CapabilityKind::Compute,
            ),
            (
                CapabilityContractRef::new("spatial.map", "import", "v0").expect("contract valid"),
                CapabilityKind::Compute,
            ),
            (
                CapabilityContractRef::new("spatial.localization", "verify", "v0")
                    .expect("contract valid"),
                CapabilityKind::Observation,
            ),
        ];
        for (fixture, actor, expected_node) in fixtures {
            let plan = decode_mission_plan(fixture).expect("Spatial Memory fixture validates");
            let mission_id = plan.goal().mission_id().clone();
            let expected_node = NodeId::new(expected_node).expect("node id valid");
            let timestamp = TimestampMs::new(1);
            let correlation =
                CorrelationId::new(format!("placement-{mission_id}")).expect("correlation valid");
            let mut control = ControlPlane::new();
            let mut state = InMemorySharedNodeState::new();
            let mut events = InMemoryEventLog::new();
            for (node_id, resource_id) in [("dog-a", "compute-a"), ("dog-b", "compute-b")] {
                control
                    .register_node(
                        &mut state,
                        registration(
                            node_id,
                            vec![
                                Capability::new(CapabilityKind::Compute, true),
                                Capability::new(CapabilityKind::Observation, true),
                            ],
                            contracts.to_vec(),
                            vec![(
                                ResourceId::new(resource_id).expect("resource id valid"),
                                ResourceKind::Compute,
                            )],
                        ),
                        NodeStatus::new(NodeHealth::Online, timestamp),
                        timestamp,
                        &correlation,
                        &mut events,
                    )
                    .expect("symmetric Spatial node registers");
            }
            control
                .set_actor_node_constraint(
                    mission_id.clone(),
                    domain::ActorId::new(actor).expect("actor id valid"),
                    expected_node.clone(),
                )
                .expect("fixture placement constraint accepted");
            let group_id =
                ExecutionGroupId::new(format!("group-{mission_id}")).expect("group id valid");
            let mut orchestrator = MissionOrchestrator::new();
            orchestrator
                .submit(
                    plan,
                    group_id,
                    &mut control,
                    timestamp,
                    &correlation,
                    &mut events,
                )
                .expect("fixture Mission accepted");
            let ready = orchestrator.ready_tasks(&mission_id, &control);
            assert_eq!(ready.len(), 1);
            let task = orchestrator
                .prepare_task(
                    &mission_id,
                    &ready[0],
                    &state,
                    &mut control,
                    TimestampMs::new(2),
                    &correlation,
                    &mut events,
                )
                .expect("first Spatial Task binds");
            assert_eq!(task.assignments().len(), 1);
            assert_eq!(task.assignments()[0].node_id(), &expected_node);
        }
    }

    /// Malformed or legacy MissionPlan documents are rejected before Control receives them.
    #[test]
    fn legacy_plan_schema_is_rejected() {
        let error = decode_mission_plan(
            r#"{"schema_version":"roboguide.mission-plan/v0.1","mission":{"id":"m","objective":"x"},"contexts":[],"tasks":[]}"#,
        )
        .expect_err("legacy plan must be rejected");
        assert!(error.to_string().contains("unsupported MissionPlan schema"));
    }

    /// MissionPlan v0.2 remains a relation-free compatibility input during v0.5 migration.
    #[test]
    fn v0_2_plan_decodes_without_execution_relations() {
        let source = include_str!("../../../scenarios/phase1-mission-v0.2/mission-plan.json");
        let plan = decode_mission_plan(source).expect("v0.2 compatibility input should decode");
        assert_eq!(plan.schema_version(), domain::MISSION_PLAN_SCHEMA_V0_5);
        assert!(
            plan.contexts()
                .iter()
                .all(|context| context.relations().is_empty())
        );
    }

    /// Current scheduling fixtures add bounded timing and replace legacy resource_kind fields.
    fn scheduled_phase1_plan() -> MissionPlan {
        let source = include_str!("../../../scenarios/phase1-mission-v0.3/mission-plan.json");
        let mut document: serde_json::Value =
            serde_json::from_str(source).expect("fixture is JSON");
        document["schema_version"] = serde_json::json!(domain::MISSION_PLAN_SCHEMA_V0_5);
        for task in document["tasks"]
            .as_array_mut()
            .expect("fixture tasks are an array")
        {
            task["timing"] = serde_json::json!({
                "earliest_start_offset_ms": 100,
                "latest_start_offset_ms": 200,
                "completion_deadline_offset_ms": 300,
                "estimated_duration_ms": 50,
            });
            for role in task["roles"]
                .as_array_mut()
                .expect("fixture roles are an array")
            {
                let resource_kind = role
                    .as_object_mut()
                    .expect("fixture role is an object")
                    .remove("resource_kind")
                    .expect("fixture role declares resource_kind");
                role["resources"] = if resource_kind.is_null() {
                    serde_json::json!([])
                } else {
                    serde_json::json!([{"kind": resource_kind, "units": 1}])
                };
            }
        }
        decode_mission_plan(&document.to_string()).expect("v0.5 scheduling plan should decode")
    }

    /// MissionPlan v0.5 rejects the removed resource_kind field even when its value is null.
    #[test]
    fn v0_5_rejects_explicit_legacy_resource_field() {
        let plan = scheduled_phase1_plan();
        let mut document = mission_plan_json(&plan);
        document["tasks"][0]["roles"][0]["resource_kind"] = serde_json::Value::Null;
        assert!(
            decode_mission_plan(&document.to_string())
                .expect_err("v0.5 must not mix resource contracts")
                .to_string()
                .contains("must use resources instead of resource_kind")
        );
    }

    /// MissionPlan v0.5 requires every nullable timing key to be present explicitly.
    #[test]
    fn v0_5_rejects_missing_nullable_timing_key() {
        let plan = scheduled_phase1_plan();
        let mut document = mission_plan_json(&plan);
        document["tasks"][0]["timing"]
            .as_object_mut()
            .expect("timing is an object")
            .remove("estimated_duration_ms");
        let error = decode_mission_plan(&document.to_string())
            .expect_err("v0.5 nullable timing keys remain required");
        assert!(
            error.to_string().contains("estimated_duration_ms"),
            "unexpected timing diagnostic: {error}"
        );
    }

    /// Mission acceptance rejects relative timing that overflows its Controller-time anchor.
    #[test]
    fn mission_acceptance_rejects_unrepresentable_absolute_timing() {
        let mut document = mission_plan_json(&scheduled_phase1_plan());
        document["tasks"][0]["timing"] = serde_json::json!({
            "earliest_start_offset_ms": u64::MAX - 100,
            "latest_start_offset_ms": u64::MAX - 100,
            "completion_deadline_offset_ms": u64::MAX - 50,
            "estimated_duration_ms": 50,
        });
        let plan = decode_mission_plan(&document.to_string()).expect("relative timing is valid");
        let group_id = ExecutionGroupId::new("group-overflow").expect("group id valid");
        let mut existing_orchestrator = MissionOrchestrator::new();
        let mut existing_control = ControlPlane::new();
        let mut existing_events = InMemoryEventLog::new();
        existing_orchestrator
            .submit(
                plan.clone(),
                group_id.clone(),
                &mut existing_control,
                TimestampMs::new(0),
                &CorrelationId::new("timing-first-submit").expect("correlation id valid"),
                &mut existing_events,
            )
            .expect("initially representable timing is accepted");
        existing_orchestrator
            .submit(
                plan.clone(),
                group_id.clone(),
                &mut existing_control,
                TimestampMs::new(101),
                &CorrelationId::new("timing-idempotent-retry").expect("correlation id valid"),
                &mut existing_events,
            )
            .expect("idempotent retry retains the original acceptance anchor");

        let mut orchestrator = MissionOrchestrator::new();
        let mut control = ControlPlane::new();
        let mut events = InMemoryEventLog::new();

        let error = orchestrator
            .submit(
                plan,
                group_id.clone(),
                &mut control,
                TimestampMs::new(101),
                &CorrelationId::new("timing-overflow").expect("correlation id valid"),
                &mut events,
            )
            .expect_err("absolute timing overflow must fail before Group creation");

        assert!(error.to_string().contains("timestamp range"));
        assert!(control.group(&group_id).is_none());
        assert!(events.records().is_empty());
    }

    /// A future Task remains Ready, survives checkpoints, activates only when due, and releases.
    #[test]
    fn future_scheduling_reservation_runs_through_control_lifecycle() {
        let plan = scheduled_phase1_plan();
        let mission_id = plan.goal().mission_id().clone();
        let first = plan.task_graph().tasks()[0]
            .requirement()
            .task_ref()
            .clone();
        let first_role = &plan.task_graph().tasks()[0].requirement().roles()[0];
        let first_capability = first_role.capability();
        let actor_contracts = plan
            .actor_requirements()
            .get(first_role.actor_id().expect("fixture role has an actor"))
            .expect("fixture actor has requirements")
            .iter()
            .map(|(kind, contract)| (contract.clone(), *kind))
            .collect::<Vec<_>>();
        let actor_capabilities = actor_contracts
            .iter()
            .map(|(_, kind)| Capability::new(*kind, true))
            .collect::<Vec<_>>();
        let group_id = ExecutionGroupId::new("group-scheduled").expect("group id valid");
        let correlation = CorrelationId::new("scheduled-flow").expect("correlation valid");
        let mut control = ControlPlane::new();
        let mut state = InMemorySharedNodeState::new();
        let mut events = InMemoryEventLog::new();
        control
            .register_node(
                &mut state,
                registration(
                    "node-compute",
                    actor_capabilities,
                    actor_contracts,
                    vec![(
                        ResourceId::new("compute-a").expect("resource id valid"),
                        ResourceKind::Compute,
                    )],
                ),
                NodeStatus::new(NodeHealth::Online, TimestampMs::new(10)),
                TimestampMs::new(10),
                &correlation,
                &mut events,
            )
            .expect("node registers");
        let mut orchestrator = MissionOrchestrator::new();
        orchestrator
            .submit(
                plan,
                group_id.clone(),
                &mut control,
                TimestampMs::new(10),
                &correlation,
                &mut events,
            )
            .expect("Mission is accepted");

        let waiting = orchestrator
            .prepare_task(
                &mission_id,
                &first,
                &state,
                &mut control,
                TimestampMs::new(20),
                &correlation,
                &mut events,
            )
            .expect("future interval is admitted");
        assert!(waiting.assignments().is_empty());
        let scheduled = control.scheduled_task(&first).expect("reservation exists");
        assert_eq!(scheduled.decision().starts_at(), TimestampMs::new(110));
        assert_eq!(scheduled.decision().ends_at(), Some(TimestampMs::new(160)));
        assert_eq!(
            scheduled.decision().latest_activation_at(),
            Some(TimestampMs::new(210))
        );
        assert_eq!(scheduled.phase(), SchedulingReservationPhase::Scheduled);
        assert!(
            control
                .activate_task_execution(
                    &group_id,
                    &first,
                    TimestampMs::new(100),
                    &correlation,
                    &mut events,
                )
                .expect_err("future Task cannot activate before its reserved start")
                .to_string()
                .contains("before its reserved start")
        );
        assert!(
            control
                .activate_task_execution(
                    &group_id,
                    &first,
                    TimestampMs::new(110),
                    &correlation,
                    &mut events,
                )
                .expect_err("unbound Task cannot activate when its interval becomes due")
                .to_string()
                .contains("committed assignments")
        );
        assert_eq!(
            control
                .group(&group_id)
                .and_then(|group| group.task_execution(&first))
                .expect("scheduled Task remains registered")
                .lifecycle(),
            TaskExecutionLifecycle::Ready
        );

        let restored = ControlPlane::restore(control.checkpoint()).expect("calendar restores");
        assert_eq!(
            restored
                .scheduled_task(&first)
                .expect("restored reservation exists")
                .decision()
                .starts_at(),
            TimestampMs::new(110)
        );
        let restored_orchestrator = MissionOrchestrator::restore_json(
            &orchestrator
                .checkpoint_json()
                .expect("orchestration checkpoint serializes"),
        )
        .expect("orchestration checkpoint restores");
        assert_eq!(
            restored_orchestrator
                .execution(&mission_id)
                .expect("Mission restores")
                .accepted_at(),
            TimestampMs::new(10)
        );
        let mut forged_timing_checkpoint =
            serde_json::to_value(control.checkpoint()).expect("Control checkpoint serializes");
        forged_timing_checkpoint["scheduled_tasks"][0]["decision"]["latest_activation_at"] =
            serde_json::json!(999);
        let forged_timing_control = ControlPlane::restore(
            serde_json::from_value(forged_timing_checkpoint)
                .expect("forged timing checkpoint remains structurally decodable"),
        )
        .expect("Control-only structure cannot reconstruct Mission-relative timing");
        assert!(
            restored_orchestrator
                .validate_control_authority(&forged_timing_control)
                .expect_err("joint restore must reject widened scheduling timing")
                .to_string()
                .contains("accepted Task timing")
        );
        let competing_requirement = domain::TaskRequirement::new(
            MissionId::new("mission-competing").expect("mission id valid"),
            TaskId::new("task-competing").expect("task id valid"),
            vec![domain::RoleRequirement::new(
                domain::RoleId::new("worker").expect("role id valid"),
                first_capability,
                Some(ResourceKind::Compute),
            )],
        )
        .expect("competing requirement valid");
        let competing_candidates = control
            .match_capabilities(
                &state,
                &competing_requirement,
                TimestampMs::new(30),
                &correlation,
                &mut events,
            )
            .expect("competing task matches the same resource");
        let competing_decision = BoundedJointScheduler::new()
            .schedule_task(
                &state,
                &competing_requirement,
                &competing_candidates,
                TimestampMs::new(30),
                &correlation,
                &mut events,
            )
            .expect("non-authoritative competing selection succeeds");
        let competing_proposal = control
            .propose(
                &state,
                &competing_requirement,
                &competing_candidates,
                competing_decision.proposed_assignments(),
                TimestampMs::new(30),
                &correlation,
                &mut events,
            )
            .expect("competing proposal remains non-authoritative");
        assert!(matches!(
            control.commit(
                &competing_proposal,
                TimestampMs::new(30),
                &correlation,
                &mut events,
            ),
            Err(ControlError::ResourceConflict { owner_task_ref, .. }) if owner_task_ref == first
        ));

        let mut missed_control = control.clone();
        let mut missed_orchestrator = orchestrator.clone();
        let mut missed_events = events.clone();
        for timestamp in [211, 212] {
            let error = missed_orchestrator
                .prepare_task(
                    &mission_id,
                    &first,
                    &state,
                    &mut missed_control,
                    TimestampMs::new(timestamp),
                    &correlation,
                    &mut missed_events,
                )
                .expect_err("missed activation window remains nonterminal and unbound");
            assert!(error.to_string().contains("window missed"));
        }
        assert!(
            missed_orchestrator
                .dispatchable_tasks(&mission_id, &missed_control)
                .is_empty(),
            "window-missed Task remains Ready but leaves the automatic timer queue"
        );
        let deferred_count = missed_events
            .records()
            .iter()
            .filter(|record| {
                matches!(
                    record.payload(),
                    domain::EventPayload::TaskSchedulingDeferred { task_ref, reason }
                        if task_ref == &first && reason == "window-missed"
                )
            })
            .count();
        assert_eq!(deferred_count, 1);
        let mut missed_restored = MissionOrchestrator::restore_json(
            &missed_orchestrator
                .checkpoint_json()
                .expect("missed scheduling state serializes"),
        )
        .expect("missed scheduling state restores");
        missed_restored
            .prepare_task(
                &mission_id,
                &first,
                &state,
                &mut missed_control,
                TimestampMs::new(213),
                &correlation,
                &mut missed_events,
            )
            .expect_err("restored missed window remains deferred");
        assert_eq!(
            missed_events
                .records()
                .iter()
                .filter(|record| matches!(
                    record.payload(),
                    domain::EventPayload::TaskSchedulingDeferred { task_ref, reason }
                        if task_ref == &first && reason == "window-missed"
                ))
                .count(),
            1
        );
        let mut cancelled_control = control.clone();
        let mut cancelled_orchestrator = orchestrator.clone();
        let mut cancellation_events = events.clone();
        cancelled_orchestrator
            .request_cancel(
                &mission_id,
                &mut cancelled_control,
                TimestampMs::new(30),
                &correlation,
                &mut cancellation_events,
            )
            .expect("cancellation releases future intervals");
        assert!(cancelled_control.scheduled_task(&first).is_none());
        assert!(cancellation_events.contains_payload(|payload| matches!(
            payload,
            domain::EventPayload::SchedulingReservationReleased { task_ref, reason, .. }
                if task_ref == &first && reason == "Mission cancellation requested"
        )));

        let bound = orchestrator
            .prepare_task(
                &mission_id,
                &first,
                &state,
                &mut control,
                TimestampMs::new(110),
                &correlation,
                &mut events,
            )
            .expect("due interval commits and binds");
        assert_eq!(bound.assignments().len(), 1);
        let mut forged_checkpoint =
            serde_json::to_value(control.checkpoint()).expect("Control checkpoint serializes");
        forged_checkpoint["scheduled_tasks"][0]["decision"]["selections"][0]["resource_ids"][0] =
            serde_json::json!("forged-resource");
        let forged_checkpoint = serde_json::from_value(forged_checkpoint)
            .expect("forged checkpoint remains structurally decodable");
        assert!(
            ControlPlane::restore(forged_checkpoint)
                .expect_err("scheduled decision must match committed binding")
                .to_string()
                .contains("committed Task bindings")
        );
        assert_eq!(
            control
                .scheduled_task(&first)
                .expect("interval retained")
                .phase(),
            SchedulingReservationPhase::Scheduled
        );
        assert!(
            control
                .activate_task_execution(
                    &group_id,
                    &first,
                    TimestampMs::new(160),
                    &correlation,
                    &mut events,
                )
                .expect_err("bound Task cannot activate after the reserved interval")
                .to_string()
                .contains("after its reserved interval")
        );
        control
            .activate_task_execution(
                &group_id,
                &first,
                TimestampMs::new(111),
                &correlation,
                &mut events,
            )
            .expect("bound Task activates");
        assert_eq!(
            control
                .scheduled_task(&first)
                .expect("active interval retained")
                .phase(),
            SchedulingReservationPhase::Activated
        );
        assert_eq!(
            control
                .scheduling_snapshot(TimestampMs::new(120))
                .occupancies()[0]
                .ends_at(),
            Some(TimestampMs::new(160))
        );
        assert_eq!(
            control
                .scheduling_snapshot(TimestampMs::new(161))
                .occupancies()[0]
                .ends_at(),
            None
        );
        orchestrator
            .task_succeeded(
                &mission_id,
                &first,
                &mut control,
                TimestampMs::new(150),
                &correlation,
                &mut events,
            )
            .expect("terminal Task releases its calendar interval");
        assert!(control.scheduled_task(&first).is_none());
        assert!(events.contains_payload(|payload| matches!(
            payload,
            domain::EventPayload::SchedulingReservationReleased { task_ref, .. }
                if task_ref == &first
        )));
    }

    /// Builds a registration with the exact contracts and resources used by the Phase 1 fixture.
    fn registration(
        node_id: &str,
        capabilities: Vec<Capability>,
        contracts: Vec<(CapabilityContractRef, CapabilityKind)>,
        resources: Vec<(ResourceId, ResourceKind)>,
    ) -> NodeRegistration {
        let local_system_id = LocalSystemId::new("test-system").expect("system id valid");
        let capability_owners = contracts
            .iter()
            .map(|(contract, _)| (contract.clone(), local_system_id.clone()))
            .collect();
        let capability_kinds = contracts.iter().cloned().collect();
        let capability_readiness = contracts
            .iter()
            .map(|(contract, _)| (contract.clone(), true))
            .collect();
        let resources = resources
            .into_iter()
            .map(|(resource_id, kind)| Resource::new(resource_id, kind, 1).expect("resource valid"))
            .collect::<Vec<_>>();
        let resource_owners = resources
            .iter()
            .map(|resource| (resource.id().clone(), local_system_id.clone()))
            .collect();
        NodeRegistration::new_with_local_systems_and_readiness(
            NodeId::new(node_id).expect("node id valid"),
            vec![LocalSystemDescriptor::new(
                local_system_id,
                LocalRuntime::new("phase1-test", "0.1.0").expect("runtime valid"),
                BTreeMap::new(),
            )],
            NodeContractVersion::v0_1(),
            capabilities,
            capability_owners,
            capability_kinds,
            capability_readiness,
            Vec::new(),
            resources,
            resource_owners,
        )
        .expect("exact test registration is valid")
    }

    /// Verifies the complete Phase 1 DAG, Context continuity, and explicit Group completion.
    #[test]
    fn phase1_execution_reuses_context_binding_until_mission_completion() {
        let source = include_str!("../../../scenarios/phase1-mission-v0.2/mission-plan.json");
        let plan = decode_mission_plan(source).expect("fixture should decode");
        let mission_id = plan.goal().mission_id().clone();
        let group_id = ExecutionGroupId::new("group-phase1-test").expect("group id valid");
        let timestamp = TimestampMs::new(1);
        let correlation = CorrelationId::new("phase1-orchestration-test").expect("trace valid");
        let mut control = ControlPlane::new();
        let mut state = InMemorySharedNodeState::new();
        let mut events = InMemoryEventLog::new();
        let compute_prepare =
            CapabilityContractRef::new("compute", "prepare", "v1").expect("contract valid");
        let compute_verify =
            CapabilityContractRef::new("observation", "verify", "v1").expect("contract valid");
        let move_contract =
            CapabilityContractRef::new("mobility", "move", "v1").expect("contract valid");
        for node in [
            registration(
                "edge",
                vec![
                    Capability::new(CapabilityKind::Compute, true),
                    Capability::new(CapabilityKind::Observation, true),
                ],
                vec![
                    (compute_prepare.clone(), CapabilityKind::Compute),
                    (compute_verify.clone(), CapabilityKind::Observation),
                ],
                vec![(
                    ResourceId::new("edge-compute").expect("resource id valid"),
                    ResourceKind::Compute,
                )],
            ),
            registration(
                "carrier",
                vec![Capability::new(CapabilityKind::Transport, true)],
                vec![(move_contract.clone(), CapabilityKind::Transport)],
                vec![(
                    ResourceId::new("carrier-space").expect("resource id valid"),
                    ResourceKind::Space,
                )],
            ),
        ] {
            control
                .register_node(
                    &mut state,
                    node,
                    NodeStatus::new(NodeHealth::Online, timestamp),
                    timestamp,
                    &correlation,
                    &mut events,
                )
                .expect("test node registers");
        }
        let mut orchestrator = MissionOrchestrator::new();
        orchestrator
            .submit(
                plan.clone(),
                group_id.clone(),
                &mut control,
                timestamp,
                &correlation,
                &mut events,
            )
            .expect("Mission should be accepted");
        assert_eq!(orchestrator.ready_tasks(&mission_id, &control).len(), 1);
        let task = |index: usize| {
            plan.task_graph().tasks()[index]
                .requirement()
                .task_ref()
                .clone()
        };
        for index in [0_usize, 1, 2, 3] {
            let task_ref = task(index);
            orchestrator
                .prepare_task(
                    &mission_id,
                    &task_ref,
                    &state,
                    &mut control,
                    TimestampMs::new(2 + index as u64),
                    &correlation,
                    &mut events,
                )
                .expect("ready Task should bind");
            control
                .activate_task_execution(
                    &group_id,
                    &task_ref,
                    TimestampMs::new(3 + index as u64),
                    &correlation,
                    &mut events,
                )
                .expect("test Runtime transition should activate the bound Task");
            orchestrator
                .task_succeeded(
                    &mission_id,
                    &task_ref,
                    &mut control,
                    TimestampMs::new(10 + index as u64),
                    &correlation,
                    &mut events,
                )
                .expect("Task outcome should advance the DAG");
            if index < 2 {
                assert_eq!(
                    orchestrator
                        .execution(&mission_id)
                        .expect("Mission exists")
                        .lifecycle(),
                    MissionExecutionLifecycle::Running
                );
            }
            if index == 1 {
                assert!(
                    control
                        .group(&group_id)
                        .expect("Group retained between Tasks")
                        .context_binding(
                            &CoordinationContextId::new("delivery-context")
                                .expect("context id valid"),
                            &domain::ContextRoleId::new("carrier").expect("role id valid"),
                        )
                        .is_some()
                );
            }
        }
        assert_eq!(
            orchestrator
                .execution(&mission_id)
                .expect("Mission exists")
                .lifecycle(),
            MissionExecutionLifecycle::Completed
        );
        assert_eq!(
            control
                .group(&group_id)
                .expect("released Group retained as history")
                .lifecycle(),
            GroupLifecycle::Released
        );
        assert!(
            control
                .allocation_snapshot(TimestampMs::new(20))
                .expect("projection valid")
                .allocations()
                .is_empty()
        );
    }

    /// Cancellation remains valid after Control has already blocked the Mission Group.
    #[test]
    fn blocked_mission_can_be_cancelled_and_released() {
        let source = include_str!("../../../scenarios/phase1-mission-v0.2/mission-plan.json");
        let plan = decode_mission_plan(source).expect("fixture should decode");
        let mission_id = plan.goal().mission_id().clone();
        let group_id = ExecutionGroupId::new("group-cancel-blocked").expect("group id valid");
        let correlation = CorrelationId::new("cancel-blocked-test").expect("trace valid");
        let mut control = ControlPlane::new();
        let mut events = InMemoryEventLog::new();
        let mut orchestrator = MissionOrchestrator::new();
        orchestrator
            .submit(
                plan,
                group_id.clone(),
                &mut control,
                TimestampMs::new(1),
                &correlation,
                &mut events,
            )
            .expect("Mission should be accepted");
        control
            .block_group(
                &group_id,
                "node recovery required",
                TimestampMs::new(2),
                &correlation,
                &mut events,
            )
            .expect("Group should become blocked");
        orchestrator
            .cancel(
                &mission_id,
                &mut control,
                TimestampMs::new(3),
                &correlation,
                &mut events,
            )
            .expect("Blocked Mission should cancel");
        assert_eq!(
            orchestrator
                .execution(&mission_id)
                .expect("Mission retained")
                .lifecycle(),
            MissionExecutionLifecycle::Cancelled
        );
        assert_eq!(
            control
                .group(&group_id)
                .expect("Group retained")
                .lifecycle(),
            GroupLifecycle::Released
        );
    }

    /// A cancellation request survives checkpoint restore before terminal attempt evidence arrives.
    #[test]
    fn cancelling_mission_restores_and_waits_for_explicit_finalization() {
        let source = include_str!("../../../scenarios/phase1-mission-v0.2/mission-plan.json");
        let plan = decode_mission_plan(source).expect("fixture should decode");
        let mission_id = plan.goal().mission_id().clone();
        let group_id = ExecutionGroupId::new("group-cancel-durable").expect("group id valid");
        let correlation = CorrelationId::new("cancel-durable-test").expect("trace valid");
        let mut control = ControlPlane::new();
        let mut events = InMemoryEventLog::new();
        let mut orchestrator = MissionOrchestrator::new();
        orchestrator
            .submit(
                plan,
                group_id.clone(),
                &mut control,
                TimestampMs::new(1),
                &correlation,
                &mut events,
            )
            .expect("Mission should be accepted");
        orchestrator
            .request_cancel(
                &mission_id,
                &mut control,
                TimestampMs::new(2),
                &correlation,
                &mut events,
            )
            .expect("cancellation request should persist");

        let checkpoint = orchestrator
            .checkpoint_json()
            .expect("cancelling Mission serializes");
        let mut restored =
            MissionOrchestrator::restore_json(&checkpoint).expect("cancelling Mission restores");
        restored
            .validate_control_authority(&control)
            .expect("Cancelling Mission retains a blocked Group");
        assert_eq!(
            restored
                .execution(&mission_id)
                .expect("Mission retained")
                .lifecycle(),
            MissionExecutionLifecycle::Cancelling
        );
        assert!(
            restored
                .dispatchable_tasks(&mission_id, &control)
                .is_empty()
        );

        restored
            .finalize_cancel(
                &mission_id,
                &mut control,
                TimestampMs::new(3),
                &correlation,
                &mut events,
            )
            .expect("terminal attempt evidence permits finalization");
        assert_eq!(
            restored
                .execution(&mission_id)
                .expect("Mission retained")
                .lifecycle(),
            MissionExecutionLifecycle::Cancelled
        );
        assert_eq!(
            control
                .group(&group_id)
                .expect("Group retained as history")
                .lifecycle(),
            GroupLifecycle::Released
        );
    }
}
