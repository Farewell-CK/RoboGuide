//! Proven Mission-local rollback/hold boundaries; shared and unclassified failures remain fatal.

use crate::*;
use sha2::{Digest as _, Sha256};

/// Converts one typed Orchestration rejection into stable breaker evidence.
fn typed_failure_evidence(
    error: &(dyn std::error::Error + Send + Sync + 'static),
) -> Option<(orchestration::MissionFailureClass, String)> {
    let orchestration = error.downcast_ref::<OrchestrationError>()?;
    let (class, identity) = match orchestration {
        OrchestrationError::InvalidContract(reason) => (
            orchestration::MissionFailureClass::InvalidContract,
            reason.clone(),
        ),
        OrchestrationError::RuntimeContractViolation {
            task_ref,
            violation,
        } => (
            orchestration::MissionFailureClass::RuntimeContractViolation,
            format!("{task_ref}:{violation:?}"),
        ),
        _ => return None,
    };
    Some((
        class,
        format!("sha256:{:x}", Sha256::digest(identity.as_bytes())),
    ))
}

/// Revalidates trusted authority before any local-error classification or explicit continuation.
pub(crate) fn validate_isolation_authority(
    controller: &ControllerState,
    now: domain::TimestampMs,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    controller.bridge.control().allocation_snapshot(now)?;
    controller
        .orchestrator
        .validate_control_authority(controller.bridge.control())?;
    controller
        .bridge
        .control()
        .validate_physical_entity_registry_on_restore()?;
    Ok(())
}

/// Runs one exact Mission's derived work with both memory and SQL rollback on conclusive local failure.
pub(crate) fn run_mission_transition(
    controller: &mut ControllerState,
    mission: &domain::MissionId,
    source: orchestration::MissionIsolationSource,
    now: domain::TimestampMs,
    correlation: &domain::CorrelationId,
    events: &mut state::SqliteEventLog,
    operation: impl FnOnce(
        &mut ControllerState,
        &mut state::SqliteEventLog,
    ) -> Result<(), Box<dyn std::error::Error + Send + Sync>>,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    if controller
        .orchestrator
        .execution(mission)
        .is_some_and(|execution| execution.isolation().is_some())
    {
        return Ok(());
    }
    // Global baselines are validated only when an error is about to be classified as local.
    // A successful transition follows the existing outer transaction validation/commit contract.
    let cloning = StageObservation::start(
        controller.bridge.diagnostics(),
        DiagnosticKind::Other,
        DiagnosticStage::Clone,
        None,
    );
    let mut candidate = controller.clone();
    drop(cloning);
    let result = events
        .clone()
        .with_transition_savepoint(|| operation(&mut candidate, events))?;
    if let Some(error) = events.take_error()? {
        return Err(state::SqliteEventLogError::Codec(format!(
            "Mission transition event sink failed: {error}"
        ))
        .into());
    }
    match result {
        Ok(()) => {
            *controller = candidate;
            Ok(())
        }
        Err(error)
            if matches!(
                error.downcast_ref::<OrchestrationError>(),
                Some(OrchestrationError::InvalidContract(_))
            ) || matches!(
                error.downcast_ref::<OrchestrationError>(),
                Some(OrchestrationError::RuntimeContractViolation { task_ref, .. })
                    if source == orchestration::MissionIsolationSource::TaskOutcome
                        && task_ref.mission_id() == mission
            ) =>
        {
            validate_isolation_authority(controller, now)?;
            let execution = controller
                .orchestrator
                .execution(mission)
                .ok_or("unproven Mission transition scope")?;
            controller
                .bridge
                .control()
                .validate_mission_group_plan(execution.group_id(), execution.plan())?;
            if let Some(OrchestrationError::RuntimeContractViolation {
                task_ref,
                violation,
            }) = error.downcast_ref::<OrchestrationError>()
            {
                if !execution
                    .plan()
                    .task_graph()
                    .tasks()
                    .iter()
                    .any(|task| task.requirement().task_ref() == task_ref)
                {
                    return Err("runtime contract rejection references an unproven Task".into());
                }
                use ports::EventSink;
                events.append(
                    now,
                    correlation,
                    None,
                    domain::EventPayload::MissionExecutionContractRejected {
                        task_ref: task_ref.clone(),
                        violation: violation.clone(),
                    },
                );
            }
            let (failure_class, failure_fingerprint) = typed_failure_evidence(error.as_ref())
                .ok_or("unproven Mission local failure classification")?;
            controller.orchestrator.isolate_execution_with_failure(
                mission,
                source,
                failure_class,
                failure_fingerprint,
                now,
                correlation,
                events,
            )?;
            Ok(())
        }
        Err(error) => Err(error),
    }
}

/// Checks current bound Nodes and exact attempt ownership without inventing stop or retry permission.
pub(crate) fn revalidate_mission_resume(
    controller: &ControllerState,
    mission_id: &domain::MissionId,
    now: domain::TimestampMs,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    validate_isolation_authority(controller, now)?;
    if safety_stop::blocks_release(controller, mission_id) {
        return Err(
            "Mission resume requires confirmed stop evidence for every retained attempt".into(),
        );
    }
    let execution = controller
        .orchestrator
        .execution(mission_id)
        .ok_or("unknown Mission")?;
    orchestration::SupportedMechanismProfile::current().validate(execution.plan())?;
    controller.bridge.control().validate_bound_mission_nodes(
        controller.bridge.state(),
        execution.plan(),
        execution.group_id(),
        now,
    )?;
    let group = controller
        .bridge
        .control()
        .group(execution.group_id())
        .ok_or("missing Mission Group")?;
    for task in group.task_executions() {
        for assignment in task.assignments() {
            if let Some(attempt) = controller.bridge.current_task_attempt_id(
                execution.group_id(),
                task.task_ref(),
                assignment.role_id(),
            ) && !controller.bridge.current_attempt_matches_binding(
                execution.group_id(),
                task.task_ref(),
                assignment.role_id(),
                assignment.node_id(),
            ) {
                return Err(
                    format!("current attempt {attempt} differs from retained binding").into(),
                );
            }
        }
    }
    Ok(())
}

/// Clears an exact hold and reconstructs its pending work only on an application-owned private candidate.
#[cfg(test)]
pub(crate) fn resume_mission_candidate(
    controller: &mut ControllerState,
    mission_id: &domain::MissionId,
    generation: u64,
    now: domain::TimestampMs,
    correlation: &domain::CorrelationId,
    events: &mut state::SqliteEventLog,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    resume_mission_candidate_with_policy(
        controller,
        mission_id,
        generation,
        now,
        correlation,
        events,
        false,
        "local-test",
        "explicit test resume",
    )
}

/// Clears a crash-loop quarantine only when an operator explicitly opts into that action.
#[allow(clippy::too_many_arguments)]
pub(crate) fn resume_mission_candidate_with_policy(
    controller: &mut ControllerState,
    mission_id: &domain::MissionId,
    generation: u64,
    now: domain::TimestampMs,
    correlation: &domain::CorrelationId,
    events: &mut state::SqliteEventLog,
    acknowledge_crash_loop: bool,
    operator_role: &str,
    operator_reason: &str,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    revalidate_mission_resume(controller, mission_id, now)?;
    if acknowledge_crash_loop {
        controller.orchestrator.clear_crash_loop_quarantine(
            mission_id,
            generation,
            now,
            correlation,
            events,
            operator_role.to_string(),
            operator_reason.to_string(),
        )?;
    }
    controller.orchestrator.resume_isolated_execution(
        mission_id,
        generation,
        now,
        correlation,
        events,
    )?;
    super::outcomes::apply_resumed_outcomes(controller, mission_id, now, correlation, events)?;
    super::dispatch::drive_ready_mission(controller, mission_id, now, correlation, events)?;
    if controller
        .orchestrator
        .execution(mission_id)
        .is_some_and(|execution| execution.isolation().is_some())
    {
        return Err("Mission remains isolated after explicit revalidation".into());
    }
    Ok(())
}

/// Delivers only already-persisted Execute intents allowed by current Orchestration holds.
pub(crate) fn flush_unisolated_dispatch(
    controller: &mut ControllerState,
    clock: &impl Clock,
) -> Result<usize, IntegrationRuntimeError> {
    let held = controller
        .orchestrator
        .mission_ids()
        .into_iter()
        .filter_map(|mission| {
            controller
                .orchestrator
                .execution(&mission)
                .filter(|execution| {
                    execution.isolation().is_some()
                        || execution.is_crash_loop_quarantined()
                        || !super::semantic_verifier::delivery_permitted(
                            controller,
                            execution.group_id(),
                        )
                })
                .map(|execution| execution.group_id().clone())
        })
        .collect::<BTreeSet<_>>();
    controller
        .bridge
        .flush_dispatch_outbox_excluding_groups(clock, &held)
}

/// Flushes Cancel independently while a persisted Orchestration hold fences new Execute delivery.
pub(crate) fn flush_unisolated_commands(
    controller: &mut ControllerState,
    clock: &impl Clock,
) -> Result<usize, IntegrationRuntimeError> {
    let dispatch = flush_unisolated_dispatch(controller, clock);
    let cancel = controller
        .bridge
        .flush_cancellation_outbox_with_clock(clock);
    dispatch.and_then(|sent| cancel.map(|cancelled| sent.saturating_add(cancelled)))
}

/// Rejects old wrappers carrying hold/generation fields that an older binary would silently ignore.
pub(crate) fn validate_isolation_checkpoint_schema(saved: &ServerCheckpoint) -> Result<(), String> {
    if saved.schema != SERVER_CHECKPOINT_SCHEMA
        && saved
            .safety_stops
            .values()
            .any(|purpose| purpose.uses_process_exit_contract())
    {
        return Err("legacy wrapper contains process-exit stop authority".into());
    }
    let missions: Vec<serde_json::Value> =
        serde_json::from_str(&saved.orchestration_json).map_err(|error| error.to_string())?;
    let integration: serde_json::Value =
        serde_json::from_str(&saved.integration_json).map_err(|error| error.to_string())?;
    if !matches!(
        saved.schema.as_str(),
        SERVER_CHECKPOINT_SCHEMA | PRE_PROCESS_STOP_SERVER_CHECKPOINT_SCHEMA
    ) && saved
        .memory_imports
        .values()
        .any(|intent| intent.reconciliation.is_some())
    {
        return Err("legacy wrapper contains Memory reconciliation authority".into());
    }
    if !matches!(
        saved.schema.as_str(),
        SERVER_CHECKPOINT_SCHEMA
            | PRE_PROCESS_STOP_SERVER_CHECKPOINT_SCHEMA
            | PRE_MEMORY_SERVER_CHECKPOINT_SCHEMA
    ) && integration.get("control").is_some_and(|control| {
        control
            .get("decision_policy_registry")
            .is_some_and(|registry| !registry.is_null())
            || control
                .get("semantic_verifier_registry")
                .is_some_and(|registry| !registry.is_null())
            || control
                .get("belief_policy_registry")
                .is_some_and(|registry| !registry.is_null())
            || control
                .get("belief_policy_registry")
                .is_some_and(|registry| !registry.is_null())
            || [
                "semantic_verifier_history",
                "semantic_verifier_receipts",
                "mission_verifier_sources",
                "semantic_verifier_watches",
                "registry_command_receipts",
                "belief_policy_history",
            ]
            .iter()
            .any(|key| {
                control
                    .get(key)
                    .and_then(serde_json::Value::as_object)
                    .is_some_and(|map| !map.is_empty())
            })
            || control
                .get("decision_policy_history")
                .and_then(serde_json::Value::as_object)
                .is_some_and(|history| !history.is_empty())
    }) {
        return Err("legacy wrapper contains State policy authority".into());
    }
    if !matches!(
        saved.schema.as_str(),
        SERVER_CHECKPOINT_SCHEMA
            | PRE_PROCESS_STOP_SERVER_CHECKPOINT_SCHEMA
            | PRE_MEMORY_SERVER_CHECKPOINT_SCHEMA
            | PRE_POLICY_SERVER_CHECKPOINT_SCHEMA
    ) && integration.get("control").is_some_and(|control| {
        ["node_identity_registry", "operator_authorization"]
            .iter()
            .any(|key| {
                control
                    .get(key)
                    .is_some_and(|authority| !authority.is_null())
            })
            || ["mission_tenants", "tenant_migrations"].iter().any(|key| {
                control
                    .get(key)
                    .and_then(serde_json::Value::as_object)
                    .is_some_and(|authority| !authority.is_empty())
            })
    }) {
        return Err("legacy wrapper contains identity/tenant lifecycle authority".into());
    }
    if !matches!(
        saved.schema.as_str(),
        SERVER_CHECKPOINT_SCHEMA
            | PRE_PROCESS_STOP_SERVER_CHECKPOINT_SCHEMA
            | PRE_MEMORY_SERVER_CHECKPOINT_SCHEMA
            | PRE_POLICY_SERVER_CHECKPOINT_SCHEMA
            | PRE_IDENTITY_SERVER_CHECKPOINT_SCHEMA
    ) && (missions.iter().any(|mission| {
        mission
            .get("isolation")
            .and_then(|hold| hold.get("source"))
            .and_then(serde_json::Value::as_str)
            == Some("SafetyStop")
    }) || saved
        .safety_stops
        .values()
        .any(|purpose| purpose.is_current_authority()))
    {
        return Err("legacy wrapper contains typed stop authority".into());
    }
    if matches!(
        saved.schema.as_str(),
        SERVER_CHECKPOINT_SCHEMA
            | PRE_PROCESS_STOP_SERVER_CHECKPOINT_SCHEMA
            | PRE_MEMORY_SERVER_CHECKPOINT_SCHEMA
            | PRE_POLICY_SERVER_CHECKPOINT_SCHEMA
            | PRE_IDENTITY_SERVER_CHECKPOINT_SCHEMA
            | PRE_STOP_AUTHORITY_SERVER_CHECKPOINT_SCHEMA
            | PRE_JOURNAL_SERVER_CHECKPOINT_SCHEMA
    ) {
        if missions.iter().any(|mission| {
            mission.get("failure_state").is_none()
                || mission
                    .get("failure_state")
                    .filter(|failure| !failure.is_null())
                    .is_some_and(|failure| failure.get("recent_failed_at").is_none())
                || mission
                    .get("isolation")
                    .filter(|hold| !hold.is_null())
                    .is_some_and(|hold| {
                        hold.get("failure")
                            .and_then(|failure| failure.get("recent_failed_at"))
                            .is_none()
                    })
        }) {
            return Err("current controller wrapper misses breaker window authority".into());
        }
    } else if missions.iter().any(|mission| {
        mission
            .get("failure_state")
            .and_then(|failure| failure.get("recent_failed_at"))
            .is_some()
            || mission
                .get("isolation")
                .and_then(|hold| hold.get("failure"))
                .and_then(|failure| failure.get("recent_failed_at"))
                .is_some()
    }) {
        return Err("legacy wrapper contains new breaker window authority".into());
    }
    if !matches!(
        saved.schema.as_str(),
        SERVER_CHECKPOINT_SCHEMA
            | PRE_PROCESS_STOP_SERVER_CHECKPOINT_SCHEMA
            | PRE_MEMORY_SERVER_CHECKPOINT_SCHEMA
            | PRE_POLICY_SERVER_CHECKPOINT_SCHEMA
            | PRE_IDENTITY_SERVER_CHECKPOINT_SCHEMA
            | PRE_STOP_AUTHORITY_SERVER_CHECKPOINT_SCHEMA
            | PRE_JOURNAL_SERVER_CHECKPOINT_SCHEMA
            | PRE_BREAKER_SERVER_CHECKPOINT_SCHEMA
    ) && missions.iter().any(|mission| {
        mission
            .get("failure_state")
            .is_some_and(|failure| !failure.is_null())
            || mission
                .get("isolation")
                .and_then(|hold| hold.get("failure"))
                .is_some()
    }) {
        return Err("pre-breaker wrapper contains typed failure authority".into());
    }
    if matches!(
        saved.schema.as_str(),
        SERVER_CHECKPOINT_SCHEMA
            | PRE_PROCESS_STOP_SERVER_CHECKPOINT_SCHEMA
            | PRE_MEMORY_SERVER_CHECKPOINT_SCHEMA
            | PRE_POLICY_SERVER_CHECKPOINT_SCHEMA
            | PRE_IDENTITY_SERVER_CHECKPOINT_SCHEMA
            | PRE_STOP_AUTHORITY_SERVER_CHECKPOINT_SCHEMA
            | PRE_JOURNAL_SERVER_CHECKPOINT_SCHEMA
            | PRE_BREAKER_SERVER_CHECKPOINT_SCHEMA
            | PRE_REPLICATION_SERVER_CHECKPOINT_SCHEMA
    ) {
        if missions
            .iter()
            .any(|mission| mission.get("dispatch_policy").is_none())
        {
            return Err("current controller wrapper misses dispatch policy authority".into());
        }
        return Ok(());
    }
    if missions
        .iter()
        .any(|mission| mission.get("dispatch_policy").is_some())
    {
        return Err("legacy controller wrapper contains new dispatch policy authority".into());
    }
    if saved.schema == PRE_DISPATCH_SERVER_CHECKPOINT_SCHEMA {
        return Ok(());
    }
    let integration: serde_json::Value =
        serde_json::from_str(&saved.integration_json).map_err(|error| error.to_string())?;
    if integration
        .get("schema")
        .and_then(serde_json::Value::as_str)
        == Some(orchestration::CONTROLLER_CHECKPOINT_SCHEMA)
    {
        return Err("legacy wrapper contains new terminal receive-time authority".into());
    }
    if saved.schema == PRE_TIMING_SERVER_CHECKPOINT_SCHEMA {
        return Ok(());
    }
    if missions.iter().any(|mission| {
        mission.get("isolation").is_some_and(|hold| !hold.is_null())
            || mission
                .get("isolation_generation")
                .and_then(serde_json::Value::as_u64)
                .is_some_and(|generation| generation != 0)
    }) {
        return Err("legacy controller wrapper contains isolation authority".into());
    }
    Ok(())
}
