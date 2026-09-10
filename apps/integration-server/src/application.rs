//! Durable application transitions over Control, Runtime, and Mission authorities.

use super::*;
/// Persists all time-driven application transitions before exposing their new live projection.
pub(super) fn drive_application_timer(
    controller: &Arc<Mutex<ControllerState>>,
    event_log: &state::SqliteEventLog,
    event_write_gate: &Arc<Mutex<()>>,
    now: domain::TimestampMs,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let _write_guard = event_write_gate
        .lock()
        .map_err(|_| "event-log write gate is poisoned")?;
    event_log.begin_batch()?;
    let transition = (|| {
        let live = controller
            .lock()
            .map_err(|_| "controller lock is poisoned")?;
        let mut candidate = live.clone();
        drop(live);
        let correlation = domain::CorrelationId::new("application-timer")?;
        candidate.bridge.tick(now, &correlation)?;
        let mut events = event_log.clone();
        apply_runtime_events(&mut candidate, now, &correlation, &mut events)?;
        begin_current_ambiguity_recoveries(&mut candidate, now, &correlation, &mut events)?;
        resume_pending_recoveries(&mut candidate, now, &correlation, &mut events)?;
        apply_pending_cancellations(&mut candidate, now, &correlation, &mut events)?;
        apply_runtime_outcomes(&mut candidate, now, &correlation, &mut events)?;
        drive_ready_tasks(&mut candidate, now, &correlation, &mut events)?;
        drive_rebound_attempts(&mut candidate, now, &correlation)?;
        if let Some(error) = event_log.take_error()? {
            return Err(format!("application timer event sink failed: {error}").into());
        }
        let checkpoint = server_checkpoint_json(&candidate)?;
        event_log.save_checkpoint(SERVER_CHECKPOINT_SCHEMA, &checkpoint)?;
        event_log.commit_batch()?;
        let mut live = controller
            .lock()
            .map_err(|_| "controller lock is poisoned")?;
        *live = candidate;
        if let Err(error) = live.bridge.flush_command_outboxes() {
            eprintln!("durable command outbox delivery deferred: {error}");
        }
        Ok::<(), Box<dyn std::error::Error + Send + Sync>>(())
    })();
    if let Err(error) = transition {
        let _ = event_log.rollback_batch();
        return Err(error);
    }
    Ok(())
}

/// Loads and validates deployment-owned actor placement constraints from JSON.
pub(super) fn load_actor_placement_file(
    path: &Path,
) -> Result<Vec<control::ActorNodeConstraint>, Box<dyn std::error::Error>> {
    let content = std::fs::read_to_string(path)?;
    let file: ActorPlacementFile = serde_json::from_str(&content)?;
    if file.schema != ACTOR_PLACEMENT_SCHEMA {
        return Err(format!(
            "actor placement file {} uses unsupported schema {}",
            path.display(),
            file.schema
        )
        .into());
    }
    file.constraints
        .into_iter()
        .map(|entry| {
            Ok(control::ActorNodeConstraint::new(
                domain::MissionId::new(entry.mission_id)?,
                domain::ActorId::new(entry.actor_id)?,
                domain::NodeId::new(entry.node_id)?,
            ))
        })
        .collect()
}

/// Requires a configured deployment policy to cover exactly the submitted Mission actors.
///
/// An empty Control placement set preserves generic matching. Once a placement file has installed
/// any constraints, strict coverage prevents a misspelled Mission or Actor from silently falling
/// back to deterministic unconstrained matching.
pub(super) fn validate_actor_placement_coverage(
    control: &control::ControlPlane,
    plan: &domain::MissionPlan,
) -> Result<(), String> {
    let configured = control.actor_node_constraints().collect::<Vec<_>>();
    if configured.is_empty() {
        return Ok(());
    }
    let mission_id = plan.goal().mission_id();
    let expected = plan
        .task_graph()
        .tasks()
        .iter()
        .flat_map(|task| task.requirement().roles())
        .filter_map(domain::RoleRequirement::actor_id)
        .cloned()
        .collect::<BTreeSet<_>>();
    let declared = configured
        .into_iter()
        .filter(|constraint| constraint.mission_id() == mission_id)
        .map(|constraint| constraint.actor_id().clone())
        .collect::<BTreeSet<_>>();
    if declared == expected {
        return Ok(());
    }
    let missing = expected
        .difference(&declared)
        .map(ToString::to_string)
        .collect::<Vec<_>>();
    let unknown = declared
        .difference(&expected)
        .map(ToString::to_string)
        .collect::<Vec<_>>();
    Err(format!(
        "strict actor placement coverage failed for Mission {mission_id}; missing actors [{}], unknown actors [{}]",
        missing.join(", "),
        unknown.join(", ")
    ))
}

/// Revalidates every durable Mission after checkpoint recovery and placement replacement.
///
/// This runs before the server accepts traffic or persists a replacement placement policy, so a
/// typo or incomplete policy cannot silently change the Actor authority of an existing Mission.
pub(super) fn validate_restored_actor_placement_coverage(
    control: &control::ControlPlane,
    orchestrator: &MissionOrchestrator,
) -> Result<(), String> {
    for mission_id in orchestrator.mission_ids() {
        let execution = orchestrator.execution(&mission_id).ok_or_else(|| {
            format!("restored Mission {mission_id} disappeared during placement validation")
        })?;
        validate_actor_placement_coverage(control, execution.plan())
            .map_err(|error| format!("restored Mission placement is invalid: {error}"))?;
    }
    Ok(())
}

/// Acquires the process-wide single-writer lease for one controller event database.
///
/// The returned file must remain alive for the server lifetime. A second server using the same
/// database fails before it can replay a stale projection or append a conflicting event sequence.
pub(super) fn acquire_event_log_writer_lock(
    event_path: &Path,
) -> Result<std::fs::File, std::io::Error> {
    let lock_path = event_log_lock_path(event_path)?;
    let file = std::fs::OpenOptions::new()
        .create(true)
        .truncate(false)
        .read(true)
        .write(true)
        .open(&lock_path)?;
    file.try_lock().map_err(|error| {
        std::io::Error::other(format!(
            "controller database {} is already owned by another Integration Server: {error}",
            event_path.display()
        ))
    })?;
    Ok(file)
}

/// Returns a canonical sibling lock path so relative and symlink aliases share one lease.
pub(super) fn event_log_lock_path(event_path: &Path) -> Result<PathBuf, std::io::Error> {
    let canonical_event_path = if event_path.exists() {
        event_path.canonicalize()?
    } else {
        let file_name = event_path.file_name().ok_or_else(|| {
            std::io::Error::new(
                std::io::ErrorKind::InvalidInput,
                "controller database path must name a file",
            )
        })?;
        let parent = event_path
            .parent()
            .filter(|parent| !parent.as_os_str().is_empty())
            .unwrap_or_else(|| Path::new("."));
        parent.canonicalize()?.join(file_name)
    };
    let mut lock_path = canonical_event_path.as_os_str().to_os_string();
    lock_path.push(".writer.lock");
    Ok(PathBuf::from(lock_path))
}

/// Applies Runtime-owned lifecycle transitions without giving Integration Control authority.
pub(super) fn apply_runtime_events(
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
pub(super) fn apply_recovery_required(
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
pub(super) fn begin_current_ambiguity_recoveries(
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
pub(super) fn resume_pending_recoveries(
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
pub(super) fn resume_role_recovery(
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
    let requirement = controller
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
        .map(|task| task.requirement().clone())
        .ok_or_else(|| "pending recovery has no accepted Task requirement".to_string())?;
    let state = controller.bridge.state().clone();
    let candidates = controller.bridge.control().match_recovery_candidates(
        &state,
        need,
        &requirement,
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

/// Serializes Integration and Mission orchestration into one versioned durable wrapper.
pub(super) fn server_checkpoint_json(
    controller: &ControllerState,
) -> Result<String, Box<dyn std::error::Error + Send + Sync>> {
    let integration_json = controller
        .bridge
        .checkpoint_json()
        .map_err(|error| format!("integration checkpoint failure: {error}"))?;
    let integration_value: serde_json::Value = serde_json::from_str(&integration_json)
        .map_err(|error| format!("integration checkpoint JSON failure: {error}"))?;
    if integration_value
        .get("schema")
        .and_then(serde_json::Value::as_str)
        != Some(INTEGRATION_CHECKPOINT_SCHEMA)
    {
        return Err("Integration checkpoint schema changed unexpectedly".into());
    }
    let orchestration_json = controller
        .orchestrator
        .checkpoint_json()
        .map_err(|error| format!("orchestration checkpoint failure: {error}"))?;
    serde_json::to_string(&ServerCheckpoint {
        schema: SERVER_CHECKPOINT_SCHEMA.to_string(),
        integration_json,
        orchestration_json,
    })
    .map_err(|error| format!("controller checkpoint wrapper failure: {error}").into())
}

/// Hands terminal Runtime facts to Mission orchestration without Runtime-owned completion.
pub(super) fn apply_runtime_outcomes(
    controller: &mut ControllerState,
    timestamp: domain::TimestampMs,
    correlation_id: &domain::CorrelationId,
    events: &mut state::SqliteEventLog,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let outcomes = controller.bridge.terminal_task_outcomes();
    for outcome in outcomes {
        let mission_id = outcome.task_ref().mission_id().clone();
        if controller
            .orchestrator
            .execution(&mission_id)
            .is_some_and(|execution| {
                matches!(
                    execution.lifecycle(),
                    orchestration::MissionExecutionLifecycle::Completed
                        | orchestration::MissionExecutionLifecycle::Failed
                        | orchestration::MissionExecutionLifecycle::Cancelling
                        | orchestration::MissionExecutionLifecycle::Cancelled
                )
            })
        {
            continue;
        }
        let ControllerState {
            bridge,
            orchestrator,
        } = controller;
        match outcome.result() {
            ObservedTaskResult::Succeeded => orchestrator.task_succeeded(
                &mission_id,
                outcome.task_ref(),
                bridge.control_mut(),
                timestamp,
                correlation_id,
                events,
            )?,
            ObservedTaskResult::Failed => orchestrator.task_failed(
                &mission_id,
                outcome.task_ref(),
                "Runtime observed a terminal role failure",
                bridge.control_mut(),
                timestamp,
                correlation_id,
                events,
            )?,
        }
        close_terminal_mission_coordination(controller, &mission_id);
    }
    Ok(())
}

/// Finalizes cancellation only after every retained physical attempt becomes terminal.
pub(super) fn apply_pending_cancellations(
    controller: &mut ControllerState,
    timestamp: domain::TimestampMs,
    correlation_id: &domain::CorrelationId,
    events: &mut state::SqliteEventLog,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let ready = controller
        .orchestrator
        .mission_ids()
        .into_iter()
        .filter_map(|mission_id| {
            let execution = controller.orchestrator.execution(&mission_id)?;
            (execution.lifecycle() == orchestration::MissionExecutionLifecycle::Cancelling
                && controller
                    .bridge
                    .group_attempts_terminal(execution.group_id()))
            .then_some(mission_id)
        })
        .collect::<Vec<_>>();
    for mission_id in ready {
        let ControllerState {
            bridge,
            orchestrator,
        } = controller;
        orchestrator.finalize_cancel(
            &mission_id,
            bridge.control_mut(),
            timestamp,
            correlation_id,
            events,
        )?;
        close_terminal_mission_coordination(controller, &mission_id);
    }
    Ok(())
}

/// Closes live peer descriptors once Mission orchestration has made the Group terminal.
pub(super) fn close_terminal_mission_coordination(
    controller: &mut ControllerState,
    mission_id: &domain::MissionId,
) {
    let group_id = controller
        .orchestrator
        .execution(mission_id)
        .filter(|execution| {
            matches!(
                execution.lifecycle(),
                orchestration::MissionExecutionLifecycle::Completed
                    | orchestration::MissionExecutionLifecycle::Failed
                    | orchestration::MissionExecutionLifecycle::Cancelled
            )
        })
        .map(|execution| execution.group_id().clone());
    if let Some(group_id) = group_id {
        controller.bridge.close_group_peer_channels(&group_id);
    }
}

/// Drives dependency-ready Tasks through Control binding and Runtime dispatch.
pub(super) fn drive_ready_tasks(
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
pub(super) fn drive_rebound_attempts(
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
pub(super) fn coordination_dispatch_deferred(error: &IntegrationRuntimeError) -> bool {
    matches!(
        error,
        IntegrationRuntimeError::Protocol(reason)
            if reason == "TaskExecution coordination mechanisms are not ready"
    )
}

/// Identifies temporary scheduling failures that should leave a Task Ready for retry.
pub(super) fn deferred_dispatch(error: &OrchestrationError) -> bool {
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

/// Returns one stable Mission contract spelling for a Runtime relation family.
pub(super) fn relation_kind_name(kind: domain::ExecutionRelationKind) -> &'static str {
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
