//! Runtime ambiguity assessment and role recovery orchestration.

use crate::*;
/// Applies Runtime-owned lifecycle transitions without giving Integration Control authority.
pub(crate) fn apply_runtime_events(
    controller: &mut ControllerState,
    timestamp: domain::TimestampMs,
    correlation_id: &domain::CorrelationId,
    events: &mut state::SqliteEventLog,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    for event in controller.bridge.take_runtime_events() {
        match event {
            runtime::ExecutionEvent::TaskActivated { group_id, task_ref } => {
                let lifecycles = controller
                    .bridge
                    .control()
                    .group(&group_id)
                    .and_then(|group| {
                        group
                            .task_execution(&task_ref)
                            .map(|task| (group.lifecycle(), task.lifecycle()))
                    });
                if lifecycles.is_some_and(|(_, task)| task == domain::TaskExecutionLifecycle::Ready)
                {
                    controller.bridge.control_mut().activate_task_execution(
                        &group_id,
                        &task_ref,
                        timestamp,
                        correlation_id,
                        events,
                    )?;
                } else if lifecycles.is_some_and(|(group, task)| {
                    group == control::GroupLifecycle::Adapted
                        && task == domain::TaskExecutionLifecycle::Active
                }) {
                    controller.bridge.control_mut().activate_group(
                        &group_id,
                        timestamp,
                        correlation_id,
                        events,
                    )?;
                }
            }
            runtime::ExecutionEvent::RecoveryRequired {
                context: Some(command),
                ..
            } => apply_recovery_required(controller, &command, timestamp, correlation_id, events)?,
            runtime::ExecutionEvent::RoleCompleted { .. }
            | runtime::ExecutionEvent::RoleFailed { .. }
            | runtime::ExecutionEvent::RecoveryRequired { context: None, .. }
            | runtime::ExecutionEvent::RelationRegistered { .. }
            | runtime::ExecutionEvent::RelationStateChanged { .. }
            | runtime::ExecutionEvent::RelationReconciliationRequired { .. } => {}
        }
    }
    Ok(())
}

/// Runs the existing Control-owned recovery pipeline for one Runtime-ambiguous role.
pub(crate) fn apply_recovery_required(
    controller: &mut ControllerState,
    command: &domain::ExecutionCommand,
    timestamp: domain::TimestampMs,
    correlation_id: &domain::CorrelationId,
    events: &mut state::SqliteEventLog,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    if controller
        .orchestrator
        .execution(command.mission_id())
        .is_some_and(|execution| {
            execution.lifecycle() == orchestration::MissionExecutionLifecycle::Cancelling
        })
    {
        return Ok(());
    }
    let Some(group) = controller.bridge.control().group(command.group_id()) else {
        return Ok(());
    };
    if !matches!(
        group.lifecycle(),
        control::GroupLifecycle::Bound
            | control::GroupLifecycle::Active
            | control::GroupLifecycle::Adapted
    ) {
        return Ok(());
    }
    let assignments = group
        .task_execution(command.task_ref())
        .map(|task| task.assignments())
        .unwrap_or_else(|| group.assignments());
    if !assignments.iter().any(|assignment| {
        assignment.role_id() == command.role_id() && assignment.node_id() == command.node_id()
    }) {
        // Control may have rebound while Runtime waits for replacement coordination readiness.
        return Ok(());
    }
    controller.bridge.control_mut().begin_execution_recovery(
        command.group_id(),
        command.task_ref(),
        command.role_id(),
        command.node_id(),
        timestamp,
        correlation_id,
        events,
    )?;
    Ok(())
}

/// Feeds current Runtime ambiguity into the single-role Control recovery slice over later ticks.
pub(crate) fn begin_current_ambiguity_recoveries(
    controller: &mut ControllerState,
    timestamp: domain::TimestampMs,
    correlation_id: &domain::CorrelationId,
    events: &mut state::SqliteEventLog,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    for command in controller.bridge.current_unknown_attempts() {
        apply_recovery_required(controller, &command, timestamp, correlation_id, events)?;
    }
    Ok(())
}

/// Retries Control-owned pending recovery when later Node evidence provides a candidate.
pub(crate) fn resume_pending_recoveries(
    controller: &mut ControllerState,
    timestamp: domain::TimestampMs,
    correlation_id: &domain::CorrelationId,
    events: &mut state::SqliteEventLog,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let pending = controller.bridge.control().pending_role_recoveries();
    for need in pending {
        if controller
            .orchestrator
            .execution(need.task_ref().mission_id())
            .is_some_and(|execution| {
                execution.lifecycle() == orchestration::MissionExecutionLifecycle::Cancelling
            })
        {
            continue;
        }
        resume_role_recovery(controller, &need, timestamp, correlation_id, events)?;
    }
    Ok(())
}

/// Drives one already-unbound Control role through Match, Schedule, Commit, and Rebind.
pub(crate) fn resume_role_recovery(
    controller: &mut ControllerState,
    need: &control::RoleRecoveryNeed,
    timestamp: domain::TimestampMs,
    correlation_id: &domain::CorrelationId,
    events: &mut state::SqliteEventLog,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let committed = controller
        .bridge
        .control()
        .pending_recovery_commitment_for_task(need.group_id(), need.task_ref(), need.role_id())
        .cloned();
    if let Some(committed) = committed {
        controller.bridge.control_mut().rebind_role(
            &committed,
            timestamp,
            correlation_id,
            events,
        )?;
        return Ok(());
    }
    let (requirement, operation) = controller
        .orchestrator
        .execution(need.task_ref().mission_id())
        .and_then(|execution| {
            execution
                .plan()
                .task_graph()
                .tasks()
                .iter()
                .find(|task| task.requirement().task_ref() == need.task_ref())
        })
        .and_then(|task| {
            task.execution_intent(need.role_id())
                .map(|intent| (task.requirement().clone(), intent.operation().clone()))
        })
        .ok_or_else(|| "pending recovery has no accepted Task requirement".to_string())?;
    let state = controller.bridge.state().clone();
    let candidates = controller
        .bridge
        .control()
        .match_recovery_candidates_for_operation(
            &state,
            need,
            &requirement,
            &operation,
            timestamp,
            correlation_id,
            events,
        )?;
    let scheduler = control::BoundedJointScheduler::new();
    let scheduling_snapshot = controller.bridge.control().scheduling_snapshot(timestamp);
    let selection = scheduler.schedule_recovery(
        &state,
        &requirement,
        &candidates,
        &scheduling_snapshot,
        timestamp,
        correlation_id,
        events,
    )?;
    let control::RecoverySchedulingOutcome::Selected(selection) = selection else {
        return Ok(());
    };
    let proposal = controller.bridge.control().propose_role_recovery(
        &state,
        &candidates,
        &requirement,
        selection.replacement_node_id().clone(),
        selection.resource_ids().to_vec(),
        timestamp,
        correlation_id,
        events,
    )?;
    let committed = controller.bridge.control_mut().commit_role_recovery(
        &state,
        &requirement,
        &proposal,
        timestamp,
        correlation_id,
        events,
    )?;
    controller
        .bridge
        .control_mut()
        .rebind_role(&committed, timestamp, correlation_id, events)?;
    Ok(())
}
