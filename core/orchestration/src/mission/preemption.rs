//! Explicit higher-priority future planning handoff, without physical work or waiting-age mutation.

use super::super::*;

/// Transport-neutral request identifies planning scope and exact Control calendar generation.
#[derive(Debug, Clone)]
pub struct FutureSchedulingPreemption {
    /// Mission whose current logical priority must be strictly greater.
    pub beneficiary: MissionId,
    /// Exact scheduled future Task, never a physical executor selector.
    pub victim: TaskRef,
    /// Calendar view the requester reviewed.
    pub expected_calendar_version: u64,
}

impl MissionOrchestrator {
    /// Validates current Mission policy and lets Control release only the exact uncommitted future interval.
    pub fn preempt_future_schedule<E: ports::EventSink>(
        &self,
        request: &FutureSchedulingPreemption,
        control: &mut ControlPlane,
        now: TimestampMs,
        correlation: &CorrelationId,
        events: &mut E,
    ) -> Result<(), OrchestrationError> {
        let beneficiary = self.execution(&request.beneficiary).ok_or_else(|| {
            OrchestrationError::Mission("preemption beneficiary is not admitted".into())
        })?;
        let victim = self.execution(request.victim.mission_id()).ok_or_else(|| {
            OrchestrationError::Mission("preemption victim is not admitted".into())
        })?;
        if request.beneficiary == *request.victim.mission_id()
            || beneficiary.lifecycle() != MissionExecutionLifecycle::Running
            || victim.lifecycle() != MissionExecutionLifecycle::Running
            || beneficiary.isolation().is_some()
            || victim.isolation().is_some()
            || beneficiary.dispatch_policy().priority() <= victim.dispatch_policy().priority()
            || self.ready_tasks(&request.beneficiary, control).is_empty()
            || !self
                .ready_tasks(request.victim.mission_id(), control)
                .contains(&request.victim)
        {
            return Err(OrchestrationError::Mission(
                "preemption requires higher current priority and non-held Ready Missions".into(),
            ));
        }
        let removed =
            control.preempt_future_task(&request.victim, request.expected_calendar_version, now)?;
        events.append(
            now,
            correlation,
            None,
            domain::EventPayload::SchedulingReservationPreempted {
                group_id: removed.group_id().clone(),
                task_ref: request.victim.clone(),
                beneficiary_mission_id: request.beneficiary.clone(),
                calendar_version: request.expected_calendar_version,
            },
        );
        Ok(())
    }
}
