//! Ready and rebound Task dispatch orchestration.

use crate::*;
/// Drives dependency-ready Tasks through Control binding and Runtime dispatch.
pub(crate) fn drive_ready_tasks(
    controller: &mut ControllerState,
    timestamp: domain::TimestampMs,
    correlation_id: &domain::CorrelationId,
    events: &mut state::SqliteEventLog,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    for mission_id in controller.orchestrator.mission_ids() {
        'ready: for task_ref in controller
            .orchestrator
            .dispatchable_tasks(&mission_id, controller.bridge.control())
        {
            let current = controller
                .bridge
                .control()
                .group(
                    controller
                        .orchestrator
                        .execution(&mission_id)
                        .ok_or_else(|| "Mission disappeared during Task dispatch".to_string())?
                        .group_id(),
                )
                .and_then(|group| group.task_execution(&task_ref))
                .cloned()
                .ok_or_else(|| "Ready TaskExecution disappeared".to_string())?;
            let prepared = if current.assignments().is_empty() {
                let state = controller.bridge.state().clone();
                let ControllerState {
                    bridge,
                    orchestrator,
                } = controller;
                match orchestrator.prepare_task(
                    &mission_id,
                    &task_ref,
                    &state,
                    bridge.control_mut(),
                    timestamp,
                    correlation_id,
                    events,
                ) {
                    Ok(prepared) => prepared,
                    Err(error) if deferred_dispatch(&error) => continue 'ready,
                    Err(error) => return Err(error.into()),
                }
            } else {
                current
            };
            let execution = controller
                .orchestrator
                .execution(&mission_id)
                .ok_or_else(|| "Mission disappeared during Task dispatch".to_string())?;
            let group_id = execution.group_id().clone();
            let planned = execution
                .plan()
                .task_graph()
                .tasks()
                .iter()
                .find(|task| task.requirement().task_ref() == &task_ref)
                .ok_or_else(|| "Task disappeared from MissionPlan during dispatch".to_string())?;
            let intents = prepared
                .assignments()
                .iter()
                .map(|assignment| {
                    planned
                        .execution_intent(assignment.role_id())
                        .cloned()
                        .map(|intent| {
                            (
                                assignment.role_id().clone(),
                                assignment.node_id().clone(),
                                intent,
                            )
                        })
                        .ok_or_else(|| {
                            format!("Task role {} has no ExecutionIntent", assignment.role_id())
                        })
                })
                .collect::<Result<Vec<_>, _>>()?;
            for (role_id, node_id, intent) in intents {
                if controller
                    .bridge
                    .current_attempt_matches_binding(&group_id, &task_ref, &role_id, &node_id)
                {
                    continue;
                }
                let execution_id = controller
                    .bridge
                    .allocate_task_attempt_id(&group_id, &task_ref, &role_id)?;
                let dispatched = controller.bridge.prepare_task_bound(
                    execution_id,
                    &group_id,
                    &task_ref,
                    &role_id,
                    intent,
                    timestamp,
                    correlation_id.clone(),
                );
                match dispatched {
                    Ok(_) => {}
                    Err(error) if coordination_dispatch_deferred(&error) => continue 'ready,
                    Err(error) => return Err(error.into()),
                }
            }
        }
    }
    Ok(())
}

/// Creates a fresh physical attempt after Control has rebound an active logical Role.
pub(crate) fn drive_rebound_attempts(
    controller: &mut ControllerState,
    timestamp: domain::TimestampMs,
    correlation_id: &domain::CorrelationId,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let mut pending = Vec::new();
    for mission_id in controller.orchestrator.mission_ids() {
        let Some(execution) = controller.orchestrator.execution(&mission_id) else {
            continue;
        };
        let Some(group) = controller.bridge.control().group(execution.group_id()) else {
            continue;
        };
        if group.lifecycle() != control::GroupLifecycle::Adapted {
            continue;
        }
        for task_execution in group
            .task_executions()
            .filter(|task| task.lifecycle() == domain::TaskExecutionLifecycle::Active)
        {
            let Some(planned) = execution
                .plan()
                .task_graph()
                .tasks()
                .iter()
                .find(|task| task.requirement().task_ref() == task_execution.task_ref())
            else {
                continue;
            };
            for assignment in task_execution.assignments() {
                if controller.bridge.current_attempt_matches_binding(
                    group.group_id(),
                    task_execution.task_ref(),
                    assignment.role_id(),
                    assignment.node_id(),
                ) {
                    continue;
                }
                let intent = planned
                    .execution_intent(assignment.role_id())
                    .cloned()
                    .ok_or_else(|| {
                        format!(
                            "rebound role {} has no ExecutionIntent",
                            assignment.role_id()
                        )
                    })?;
                pending.push((
                    group.group_id().clone(),
                    task_execution.task_ref().clone(),
                    assignment.role_id().clone(),
                    intent,
                ));
            }
        }
    }
    for (group_id, task_ref, role_id, intent) in pending {
        let execution_id = controller
            .bridge
            .allocate_task_attempt_id(&group_id, &task_ref, &role_id)?;
        match controller.bridge.prepare_task_bound(
            execution_id,
            &group_id,
            &task_ref,
            &role_id,
            intent,
            timestamp,
            correlation_id.clone(),
        ) {
            Ok(_) => {}
            Err(error) if coordination_dispatch_deferred(&error) => {}
            Err(error) => return Err(error.into()),
        }
    }
    Ok(())
}

/// Identifies a bound Task waiting for declared Runtime coordination evidence.
fn coordination_dispatch_deferred(error: &IntegrationRuntimeError) -> bool {
    matches!(
        error,
        IntegrationRuntimeError::Protocol(reason)
            if reason == "TaskExecution coordination mechanisms are not ready"
    )
}

/// Identifies temporary scheduling failures that should leave a Task Ready for retry.
pub(crate) fn deferred_dispatch(error: &OrchestrationError) -> bool {
    matches!(
        error,
        OrchestrationError::Control(control::ControlError::NoCandidate(_))
    ) || matches!(
        error,
        OrchestrationError::Mission(reason)
            if reason.contains("no feasible")
                || reason.contains("joint scheduling deferred")
                || reason.contains("joint scheduling window missed")
    ) || matches!(
        error,
        OrchestrationError::Control(
            control::ControlError::ActorPlacementConstraintUnsatisfied { .. }
        )
    ) || matches!(
        error,
        OrchestrationError::Control(
            control::ControlError::ActorBindingRequiresReconciliation { .. }
        )
    )
}
