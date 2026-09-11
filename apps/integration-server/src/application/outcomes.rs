//! Terminal Runtime outcome and Mission cancellation application.

use crate::*;
/// Hands terminal Runtime facts to Mission orchestration without Runtime-owned completion.
pub(crate) fn apply_runtime_outcomes(
    controller: &mut ControllerState,
    timestamp: domain::TimestampMs,
    correlation_id: &domain::CorrelationId,
    events: &mut state::SqliteEventLog,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let outcomes = controller.bridge.terminal_task_execution_outcomes();
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
            ObservedTaskExecutionResult::ExecutionCompleted => {
                orchestrator.record_task_execution_completed(
                    &mission_id,
                    outcome.task_ref(),
                    bridge.control_mut(),
                    timestamp,
                    correlation_id,
                    events,
                )?;
                if should_apply_execution_report(
                    orchestrator.task_satisfaction_basis(&mission_id, outcome.task_ref()),
                ) {
                    orchestrator.satisfy_task_from_execution_report(
                        &mission_id,
                        outcome.task_ref(),
                        bridge.control_mut(),
                        timestamp,
                        correlation_id,
                        events,
                    )?;
                }
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
