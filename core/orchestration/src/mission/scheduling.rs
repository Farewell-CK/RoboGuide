//! Ready-Task scheduling and committed task preparation.

use super::super::*;
use super::timing::{append_scheduling_created, append_scheduling_deferred};

impl MissionOrchestrator {
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
        self.prepare_task_with_optional_duration_estimate(
            mission_id,
            task_ref,
            state,
            control,
            timestamp,
            correlation_id,
            events,
            None,
        )
    }

    /// Prepares one Ready Task using fresh source-aware duration evidence for time placement.
    #[allow(clippy::too_many_arguments)]
    pub fn prepare_task_with_duration_estimate<S: SharedNodeStateReader, E: EventSink>(
        &mut self,
        mission_id: &MissionId,
        task_ref: &TaskRef,
        state: &S,
        control: &mut ControlPlane,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
        duration_estimate: &domain::TaskDurationEstimate,
    ) -> Result<domain::TaskExecution, OrchestrationError> {
        self.prepare_task_with_optional_duration_estimate(
            mission_id,
            task_ref,
            state,
            control,
            timestamp,
            correlation_id,
            events,
            Some(duration_estimate),
        )
    }

    /// Implements shared preparation while keeping duration evidence outside MissionPlan.
    #[allow(clippy::too_many_arguments)]
    fn prepare_task_with_optional_duration_estimate<S: SharedNodeStateReader, E: EventSink>(
        &mut self,
        mission_id: &MissionId,
        task_ref: &TaskRef,
        state: &S,
        control: &mut ControlPlane,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
        duration_estimate: Option<&domain::TaskDurationEstimate>,
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
            let outcome = match scheduler.schedule_task_with_snapshot_and_estimate(
                state,
                &requirement,
                &candidates,
                &snapshot,
                control::TaskSchedulingContext::new(accepted_at, timestamp, duration_estimate),
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
}
