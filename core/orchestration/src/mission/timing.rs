//! Mission-relative scheduling time validation and event evidence.

use super::super::*;

/// Validates that every relative Task time remains representable after acceptance anchoring.
pub(super) fn validate_plan_timing_anchor(
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
pub(super) fn checked_plan_timestamp(
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
pub(super) fn plan_latest_activation_at(
    timing: &domain::TaskTiming,
    accepted_at: TimestampMs,
    task_ref: &TaskRef,
    duration_ms: Option<u64>,
) -> Result<Option<TimestampMs>, OrchestrationError> {
    let latest_start = timing
        .latest_start_offset_ms()
        .map(|offset| checked_plan_timestamp(accepted_at, offset, task_ref))
        .transpose()?;
    let completion_deadline = timing
        .completion_deadline_offset_ms()
        .map(|offset| checked_plan_timestamp(accepted_at, offset, task_ref))
        .transpose()?;
    let completion_start = match (completion_deadline, duration_ms) {
        (Some(deadline), Some(duration)) => Some(TimestampMs::new(
            deadline.as_millis().checked_sub(duration).ok_or(
                OrchestrationError::SchedulingDeferred(SchedulingDeferral::WindowMissed),
            )?,
        )),
        (deadline, None) => deadline,
        (None, Some(_)) => None,
    };
    let latest = match (latest_start, completion_start) {
        (Some(latest), Some(completion)) => Some(latest.min(completion)),
        (Some(latest), None) => Some(latest),
        (None, Some(completion)) => Some(completion),
        (None, None) => None,
    };
    let earliest =
        checked_plan_timestamp(accepted_at, timing.earliest_start_offset_ms(), task_ref)?;
    if latest.is_some_and(|latest| latest < earliest) {
        return Err(OrchestrationError::SchedulingDeferred(
            SchedulingDeferral::WindowMissed,
        ));
    }
    Ok(latest)
}

/// Emits durable creation evidence after Control admits one bounded scheduling interval.
pub(super) fn append_scheduling_created<E: EventSink>(
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
pub(super) fn append_scheduling_deferred<E: EventSink>(
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
pub(super) fn release_mission_scheduling<E: EventSink>(
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

#[cfg(test)]
mod tests {
    use super::*;

    /// Restore-time timing reconstruction cannot panic or wrap for an impossible estimate.
    #[test]
    fn duration_activation_bounds_are_checked_during_reconstruction() {
        let task = TaskRef::new(
            MissionId::new("timing").unwrap(),
            TaskId::new("task").unwrap(),
        );
        for (accepted, earliest, deadline, duration, expected) in [
            (0, 0, 50, Some(49), Some(1)),
            (0, 0, 50, Some(50), Some(0)),
            (0, 0, 50, Some(51), None),
            (100, 0, 50, Some(51), None),
            (100, 10, 50, Some(41), None),
            (0, 0, 0, Some(1), None),
            (0, 0, 0, Some(0), Some(0)),
            (0, 0, 0, None, Some(0)),
            (0, 0, 50, Some(u64::MAX), None),
            (0, 0, u64::MAX, Some(u64::MAX), Some(0)),
            (u64::MAX - 50, 0, 50, Some(50), Some(u64::MAX - 50)),
        ] {
            let timing =
                domain::TaskTiming::new_constraints(earliest, None, Some(deadline)).unwrap();
            let result =
                plan_latest_activation_at(&timing, TimestampMs::new(accepted), &task, duration);
            match expected {
                Some(latest) => assert_eq!(result.unwrap(), Some(TimestampMs::new(latest))),
                None => assert!(matches!(
                    result,
                    Err(OrchestrationError::SchedulingDeferred(
                        SchedulingDeferral::WindowMissed
                    ))
                )),
            }
        }
        let timing = domain::TaskTiming::new_constraints(0, None, Some(1)).unwrap();
        assert!(
            plan_latest_activation_at(&timing, TimestampMs::new(u64::MAX), &task, Some(1)).is_err()
        );
    }
}
