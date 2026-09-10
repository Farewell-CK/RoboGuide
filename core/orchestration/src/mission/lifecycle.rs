//! Runtime outcome, cancellation, readiness, and Mission completion transitions.

use super::super::*;
use super::timing::release_mission_scheduling;

impl MissionOrchestrator {
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
    pub(super) fn refresh_ready<E: EventSink>(
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
