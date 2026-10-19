//! Terminal Runtime outcome and Mission cancellation application.

use crate::*;
/// Hands terminal Runtime facts to Mission orchestration without Runtime-owned completion.
pub(crate) fn apply_runtime_outcomes(
    controller: &mut ControllerState,
    timestamp: domain::TimestampMs,
    correlation_id: &domain::CorrelationId,
    events: &mut state::SqliteEventLog,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    apply_selected_outcomes(controller, None, timestamp, correlation_id, events)?;
    apply_pending_execution_satisfaction(controller, None, timestamp, correlation_id, events)
}

/// Reconstructs durable pending outcomes for only the explicitly resumed Mission.
pub(crate) fn apply_resumed_outcomes(
    controller: &mut ControllerState,
    mission: &domain::MissionId,
    timestamp: domain::TimestampMs,
    correlation_id: &domain::CorrelationId,
    events: &mut state::SqliteEventLog,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    apply_selected_outcomes(controller, Some(mission), timestamp, correlation_id, events)?;
    apply_pending_execution_satisfaction(
        controller,
        Some(mission),
        timestamp,
        correlation_id,
        events,
    )
}

/// Selects derived outcomes without changing their Runtime source or exact attempt fences.
fn apply_selected_outcomes(
    controller: &mut ControllerState,
    only_mission: Option<&domain::MissionId>,
    timestamp: domain::TimestampMs,
    correlation_id: &domain::CorrelationId,
    events: &mut state::SqliteEventLog,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let outcomes = controller.bridge.terminal_task_execution_outcomes();
    for outcome in outcomes {
        let mission_id = outcome.task_ref().mission_id().clone();
        if only_mission.is_some_and(|selected| selected != &mission_id) {
            continue;
        }
        if safety_stop::blocks_release(controller, &mission_id) {
            continue;
        }
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
        if matches!(outcome.result(), ObservedTaskExecutionResult::Failed) {
            let group_id = controller
                .orchestrator
                .execution(&mission_id)
                .ok_or("terminal outcome Mission is absent")?
                .group_id()
                .clone();
            if safety_stop::has_live_group_attempts(controller, &group_id) {
                safety_stop::admit_group_stop(controller, &group_id, timestamp, 10_000)?;
                continue;
            }
        }
        // Only terminal failure may close a SafetyStop hold after every retained stop
        // is confirmed. This is not a resume and never dispatches more work.
        if matches!(outcome.result(), ObservedTaskExecutionResult::Failed)
            && controller
                .orchestrator
                .execution(&mission_id)
                .is_some_and(|execution| {
                    execution.isolation().is_some_and(|hold| {
                        hold.source == orchestration::MissionIsolationSource::SafetyStop
                    }) && controller
                        .bridge
                        .group_attempts_terminal(execution.group_id())
                })
        {
            controller.orchestrator.task_failed(
                &mission_id,
                outcome.task_ref(),
                "original execution failure; complete current Group stop confirmed",
                controller.bridge.control_mut(),
                timestamp,
                correlation_id,
                events,
            )?;
            close_terminal_mission_coordination(controller, &mission_id);
            continue;
        }
        run_mission_transition(
            controller,
            &mission_id,
            orchestration::MissionIsolationSource::TaskOutcome,
            timestamp,
            correlation_id,
            events,
            |controller, events| {
                let ControllerState {
                    bridge,
                    orchestrator,
                    ..
                } = controller;
                match outcome.result() {
                    ObservedTaskExecutionResult::ExecutionCompleted => {
                        orchestrator.validate_runtime_task_completion(
                            &mission_id,
                            outcome.task_ref(),
                            outcome.earliest_completion_received_at(),
                            outcome.completion_received_at(),
                        )?;
                        orchestrator.record_task_execution_completed(
                            &mission_id,
                            outcome.task_ref(),
                            bridge.control_mut(),
                            timestamp,
                            correlation_id,
                            events,
                        )?;
                    }
                    ObservedTaskExecutionResult::Failed => orchestrator.task_failed(
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
                Ok(())
            },
        )?;
    }
    Ok(())
}

/// Reconsiders durable local completions against current satisfaction evidence without replaying execution.
fn apply_pending_execution_satisfaction(
    controller: &mut ControllerState,
    only_mission: Option<&domain::MissionId>,
    timestamp: domain::TimestampMs,
    correlation: &domain::CorrelationId,
    events: &mut state::SqliteEventLog,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let mut pending = Vec::new();
    for mission in controller.orchestrator.mission_ids() {
        if only_mission.is_some_and(|only| only != &mission)
            || safety_stop::blocks_release(controller, &mission)
        {
            continue;
        }
        let Some(execution) = controller.orchestrator.execution(&mission) else {
            continue;
        };
        if execution.isolation().is_some() {
            continue;
        }
        let Some(group) = controller.bridge.control().group(execution.group_id()) else {
            continue;
        };
        for task in group.task_executions() {
            if task.lifecycle() == domain::TaskExecutionLifecycle::AwaitingSatisfaction
                && should_apply_execution_report(
                    controller
                        .orchestrator
                        .task_satisfaction_basis(&mission, task.task_ref()),
                )
                && super::semantic_verifier::task_policy_allows(
                    controller,
                    task.task_ref(),
                    domain::DecisionKind::Satisfaction,
                    timestamp,
                )
            {
                pending.push((mission.clone(), task.task_ref().clone()));
            }
        }
    }
    for (mission, task) in pending {
        run_mission_transition(
            controller,
            &mission,
            orchestration::MissionIsolationSource::TaskOutcome,
            timestamp,
            correlation,
            events,
            |controller, events| {
                super::semantic_verifier::audit_satisfaction(
                    controller,
                    &task,
                    timestamp,
                    correlation,
                    events,
                );
                controller.orchestrator.satisfy_task_from_execution_report(
                    &mission,
                    &task,
                    controller.bridge.control_mut(),
                    timestamp,
                    correlation,
                    events,
                )?;
                close_terminal_mission_coordination(controller, &mission);
                Ok(())
            },
        )?;
    }
    Ok(())
}

/// Returns whether local execution completion is sufficient for the accepted Task policy.
fn should_apply_execution_report(basis: Option<&domain::TaskSatisfactionBasis>) -> bool {
    basis.is_some_and(|basis| matches!(basis, domain::TaskSatisfactionBasis::ExecutionReport))
}

/// Finalizes cancellation only after every retained physical attempt becomes terminal.
pub(crate) fn apply_pending_cancellations(
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
                    .group_attempts_terminal(execution.group_id())
                && !safety_stop::blocks_release(controller, &mission_id))
            .then_some(mission_id)
        })
        .collect::<Vec<_>>();
    for mission_id in ready {
        let ControllerState {
            bridge,
            orchestrator,
            ..
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
pub(crate) fn close_terminal_mission_coordination(
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

#[cfg(test)]
mod tests {
    use super::*;

    /// Execution-report Tasks can advance directly after local execution completes.
    #[test]
    fn execution_report_basis_is_applied_by_runtime_outcome_path() {
        assert!(should_apply_execution_report(Some(
            &domain::TaskSatisfactionBasis::ExecutionReport
        )));
    }

    /// Verifier-backed Tasks remain awaiting evidence after local execution completes.
    #[test]
    fn verifier_basis_remains_awaiting_external_evidence() {
        let verifier = domain::CapabilityContractRef::new("observation", "verify", "v1")
            .expect("verifier contract is valid");
        let specification = domain::VerifierSatisfactionSpec::new(verifier, "object is present", 1)
            .expect("verifier specification is valid");
        assert!(!should_apply_execution_report(Some(
            &domain::TaskSatisfactionBasis::VerifierEvidence(specification)
        )));
    }
}
