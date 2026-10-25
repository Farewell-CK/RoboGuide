//! Runtime ambiguity assessment and role recovery orchestration.

use crate::*;

/// Projects the unified controller recovery state without granting any automatic recovery authority.
pub(crate) fn recovery_view(
    controller: &ControllerState,
    now: domain::TimestampMs,
) -> serde_json::Value {
    let unknown_attempts = controller.bridge.current_unknown_attempts();
    let groups = controller.bridge.group_recovery_ids();
    let mut pending_groups = Vec::new();
    let mut manual_groups = Vec::new();
    for group in &groups {
        let active = controller
            .bridge
            .control()
            .group(group)
            .is_some_and(|group| group.lifecycle() != control::GroupLifecycle::Released);
        if !active {
            continue;
        }
        match controller.bridge.group_recovery_disposition(group, now) {
            runtime::GroupRecoveryDisposition::AwaitingStop
            | runtime::GroupRecoveryDisposition::StopConfirmed
            | runtime::GroupRecoveryDisposition::ContinuationPrepared => {
                pending_groups.push(group.clone())
            }
            runtime::GroupRecoveryDisposition::BudgetExpired
            | runtime::GroupRecoveryDisposition::OriginalFailed => {
                manual_groups.push(group.clone())
            }
            _ => {}
        }
    }
    let held_missions = controller
        .orchestrator
        .mission_ids()
        .into_iter()
        .filter(|mission| {
            controller
                .orchestrator
                .execution(mission)
                .is_some_and(|execution| {
                    matches!(
                        execution.lifecycle(),
                        orchestration::MissionExecutionLifecycle::Accepted
                            | orchestration::MissionExecutionLifecycle::Running
                            | orchestration::MissionExecutionLifecycle::Cancelling
                    ) && (execution.isolation().is_some()
                        || execution.is_crash_loop_quarantined()
                        || super::plan_revision::blocks_mission(controller, mission))
                })
        })
        .map(|mission| mission.as_str().to_string())
        .collect::<Vec<_>>();
    let state = if !unknown_attempts.is_empty() || !pending_groups.is_empty() {
        "ReconciliationRequired"
    } else if !held_missions.is_empty() || !manual_groups.is_empty() {
        "ManualIntervention"
    } else {
        "Ready"
    };
    serde_json::json!({
        "schema_version": "roboguide.controller-recovery-view/v0.2",
        "state": state,
        "observed_at_ms": now.as_millis(),
        "unknown_attempts": unknown_attempts.iter().map(|command| serde_json::json!({
            "execution_id": controller.bridge.current_task_attempt_id(command.group_id(),command.task_ref(),command.role_id()),
            "correlation_id": command.correlation_id().as_str(),
            "mission_id": command.mission_id().as_str(),
            "group_id": command.group_id().as_str(),
            "task_ref": command.task_ref().to_string(),
            "role_id": command.role_id().as_str(),
            "node_id": command.node_id().as_str(),
        })).collect::<Vec<_>>(),
        "groups_requiring_recovery": pending_groups.iter().map(|group| group.as_str()).collect::<Vec<_>>(),
        "groups_requiring_intervention": manual_groups.iter().map(|group| group.as_str()).collect::<Vec<_>>(),
        "group_recovery_history": groups.iter().map(|group|serde_json::json!({"group_id":group.as_str(),"disposition":controller.bridge.group_recovery_disposition(group,now)})).collect::<Vec<_>>(),
        "held_missions": held_missions,
        "sequence": [
            "restore_checkpoint",
            "clear_process_local_session_anchors",
            "require_node_registration_and_status",
            "retain_unknown_physical_attempts",
            "reconcile_control_commitments",
            "require_operator_approval_for_ambiguous_resume",
            "ready"
        ],
        "sequence_is_guidance": true,
        "automatic_execute_replay": false,
        "automatic_resource_release": false,
        "automatic_node_replacement": false,
    })
}
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
                            .filter(|task| {
                                // A late old-attempt fact is still evidence, but cannot activate
                                // a released or differently rebound logical Task.
                                task.assignments().len() == task.role_scopes().len()
                                    && !task.assignments().is_empty()
                                    && task.assignments().iter().all(|assignment| {
                                        task.role_scopes().contains_key(assignment.role_id())
                                            && controller.bridge.current_attempt_matches_binding(
                                                &group_id,
                                                &task_ref,
                                                assignment.role_id(),
                                                assignment.node_id(),
                                            )
                                    })
                            })
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
            | runtime::ExecutionEvent::RelationStateChanged { .. } => {}
            runtime::ExecutionEvent::RelationReconciliationRequired { relation, .. } => {
                safety_stop::admit(
                    controller,
                    relation.group_id(),
                    relation.target_task_ref(),
                    relation.target_role_id(),
                    timestamp,
                    10_000,
                )
                .map_err(|error| format!("relation stop admission failed: {error}"))?;
            }
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
        .is_none_or(|execution| {
            execution.group_id() != command.group_id()
                || execution
                    .plan()
                    .task_graph()
                    .task(command.task_ref().task_id())
                    .is_none()
        })
    {
        return Ok(());
    }
    if super::plan_revision::observe_trigger(
        controller,
        command.mission_id(),
        super::plan_revision::Trigger::RuntimeUnknown,
        timestamp,
    )? {
        return Ok(());
    }
    if controller
        .orchestrator
        .execution(command.mission_id())
        .is_some_and(|execution| {
            execution.lifecycle() == orchestration::MissionExecutionLifecycle::Cancelling
                || execution.isolation().is_some()
                || super::plan_revision::blocks_mission(controller, command.mission_id())
        })
    {
        return Ok(());
    }
    if !controller.bridge.recovery_release_ready(command, timestamp) {
        // Unknown, heartbeat loss and Cancel receipts do not prove physical stop.
        return Ok(());
    }
    if let Some(execution) = controller
        .bridge
        .current_task_attempt_id(command.group_id(), command.task_ref(), command.role_id())
        .map(str::to_owned)
    {
        super::memory_use::revoke_attempt(controller, &execution, timestamp)?;
        if super::memory_use::blocks_attempt_release(controller, &execution) {
            return Ok(());
        }
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
    controller.bridge.mark_recovery_release(command);
    Ok(())
}

/// Feeds current Runtime ambiguity into the single-role Control recovery slice over later ticks.
pub(crate) fn begin_current_ambiguity_recoveries(
    controller: &mut ControllerState,
    timestamp: domain::TimestampMs,
    correlation_id: &domain::CorrelationId,
    events: &mut state::SqliteEventLog,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    for group_id in controller.bridge.group_recovery_ids() {
        if !matches!(
            controller
                .bridge
                .group_recovery_disposition(&group_id, timestamp),
            runtime::GroupRecoveryDisposition::AwaitingStop
                | runtime::GroupRecoveryDisposition::StopConfirmed
                | runtime::GroupRecoveryDisposition::ContinuationPrepared
        ) {
            continue;
        }
        let mission = controller
            .orchestrator
            .mission_ids()
            .into_iter()
            .find(|mission_id| {
                controller
                    .orchestrator
                    .execution(mission_id)
                    .is_some_and(|mission| {
                        mission.group_id() == &group_id
                            && mission.lifecycle()
                                == orchestration::MissionExecutionLifecycle::Running
                            && mission.isolation().is_none()
                            && !super::plan_revision::blocks_mission(controller, mission_id)
                    })
            });
        if let Some(mission) = mission {
            if super::memory_use::blocks_release(controller, &mission) {
                super::memory_use::revoke_group(
                    controller,
                    &group_id,
                    "Group continuation requested",
                    timestamp,
                )?;
                continue;
            }
            controller
                .bridge
                .prepare_group_continuation(&group_id, timestamp, correlation_id)?;
        }
    }
    for command in controller.bridge.current_unknown_attempts() {
        apply_recovery_required(controller, &command, timestamp, correlation_id, events)?;
    }
    for command in controller.bridge.stopped_recovery_commands(timestamp) {
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
                    || execution.isolation().is_some()
                    || super::plan_revision::blocks_mission(
                        controller,
                        need.task_ref().mission_id(),
                    )
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
    if controller.bridge.recovery_release_expired(need, timestamp)
        || controller.bridge.recovery_release_invalidated(need)
    {
        if let Some(committed) = controller
            .bridge
            .control()
            .pending_recovery_commitment_for_task(need.group_id(), need.task_ref(), need.role_id())
            .cloned()
        {
            controller
                .bridge
                .control_mut()
                .abort_role_recovery_commitment(&committed, timestamp, correlation_id, events)?;
        }
        return Ok(());
    }
    if !controller
        .bridge
        .recovery_release_permitted(need, timestamp)
    {
        return Ok(());
    }
    if controller
        .bridge
        .current_task_attempt_id(need.group_id(), need.task_ref(), need.role_id())
        .is_some_and(|execution| super::memory_use::blocks_attempt_release(controller, execution))
    {
        return Ok(());
    }
    let committed = controller
        .bridge
        .control()
        .pending_recovery_commitment_for_task(need.group_id(), need.task_ref(), need.role_id())
        .cloned();
    let state = controller.bridge.decision_state();
    if let Some(committed) = committed {
        let supported = state
            .node(committed.replacement_node_id())
            .is_some_and(|node| {
                committed.operation().is_some_and(|operation| {
                    node.registration()
                        .execution_recovery_support(operation)
                        .is_some_and(|declaration| declaration.support.supports_role_retry())
                })
            });
        if !supported {
            controller
                .bridge
                .control_mut()
                .abort_role_recovery_commitment(&committed, timestamp, correlation_id, events)?;
            return Ok(());
        }
        let rebind = controller.bridge.control_mut().rebind_role_with_state(
            &state,
            &committed,
            timestamp,
            correlation_id,
            events,
        );
        match rebind {
            Ok(_) | Err(control::ControlError::AssignmentUnavailable(_)) => {}
            Err(error) => return Err(error.into()),
        }
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
    let candidates = controller
        .bridge
        .control()
        .match_stopped_recovery_candidates_for_operation(
            &state,
            need,
            &requirement,
            &operation,
            timestamp,
            correlation_id,
            events,
        )?;
    let scheduler = control::BoundedJointScheduler::new();
    let scheduling_snapshot = controller
        .bridge
        .control()
        .scheduling_snapshot(timestamp)
        .with_policy_cost_evidence(controller.bridge.control().scheduling_cost_assessments(
            &state,
            need.task_ref().mission_id(),
            timestamp,
        ))?;
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
    controller.bridge.control().audit_scheduling_costs(
        need.task_ref(),
        selection.policy_cost_evidence(),
        timestamp,
        correlation_id,
        events,
    )?;
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
    controller.bridge.control_mut().rebind_role_with_state(
        &state,
        &committed,
        timestamp,
        correlation_id,
        events,
    )?;
    Ok(())
}
