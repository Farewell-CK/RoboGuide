//! Original independent Mission-goal completion gates for runtime replanning.

use super::super::*;
use sha2::{Digest as _, Sha256};

/// Binds the original accepted goal bytes independently of a candidate's decomposition.
fn goal_digest(goal: &domain::MissionGoal) -> String {
    format!(
        "sha256:{:x}",
        Sha256::digest(
            serde_json::to_vec(&serde_json::to_value(goal).expect("typed goal"))
                .expect("typed goal JSON")
        )
    )
}

impl MissionOrchestrator {
    /// Installs only the original Control-frozen goal; a later candidate cannot remove or replace it.
    pub fn install_mission_goal_gate(
        &mut self,
        mission: &MissionId,
        control: &ControlPlane,
    ) -> Result<(), OrchestrationError> {
        let contract = control.mission_goal_contract(mission).ok_or_else(|| {
            OrchestrationError::Mission("Mission has no original independent goal contract".into())
        })?;
        let execution = self
            .executions
            .get_mut(mission)
            .ok_or_else(|| OrchestrationError::Mission("unknown goal Mission".into()))?;
        if !matches!(
            execution.lifecycle,
            MissionExecutionLifecycle::Accepted | MissionExecutionLifecycle::Running
        ) || execution
            .goal_gate
            .as_ref()
            .is_some_and(|digest| digest != &contract.digest)
            || contract.original_goal_digest != goal_digest(execution.plan.goal())
        {
            return Err(OrchestrationError::Mission(
                "original Mission goal gate cannot change".into(),
            ));
        }
        execution.goal_gate = Some(contract.digest.clone());
        Ok(())
    }

    /// Completes only with an exact current final-positive Control receipt after every actual Task satisfies.
    pub fn complete_mission_with_goal<E: EventSink>(
        &mut self,
        mission: &MissionId,
        receipt_digest: &str,
        control: &mut ControlPlane,
        now: TimestampMs,
        correlation: &CorrelationId,
        events: &mut E,
    ) -> Result<bool, OrchestrationError> {
        let execution = self
            .executions
            .get(mission)
            .ok_or_else(|| OrchestrationError::Mission("unknown goal Mission".into()))?;
        if execution.goal_gate.as_ref()
            != control
                .mission_goal_contract(mission)
                .map(|contract| &contract.digest)
            || execution.goal_gate.is_none()
            || execution.isolation.is_some()
            || execution.lifecycle != MissionExecutionLifecycle::Running
            || !self.tasks_are_satisfied(mission, control)?
            || !control.mission_goal_confirmed(
                mission,
                execution.plan_revision(),
                execution.group_id(),
                &mission_plan_digest(execution.plan()),
                receipt_digest,
                now,
            )
        {
            return Ok(false);
        }
        let group = execution.group_id.clone();
        if control
            .group(&group)
            .is_some_and(|group| group.lifecycle() == GroupLifecycle::Bound)
        {
            control.activate_group(&group, now, correlation, events)?;
        }
        self.complete_mission(mission, control, now, correlation, events)?;
        let completed = self
            .executions
            .get_mut(mission)
            .expect("completed original Mission");
        completed.goal_receipt = Some(receipt_digest.into());
        completed.goal_completed_at = Some(now);
        Ok(true)
    }
}

/// Rechecks the original accepted goal and historical completion without renewing or undoing evidence.
pub(super) fn validate_goal_authority(
    execution: &MissionExecution,
    control: &ControlPlane,
) -> Result<(), OrchestrationError> {
    let contract = control.mission_goal_contract(execution.plan.goal().mission_id());
    if execution.goal_gate.as_deref() != contract.map(|contract| contract.digest.as_str())
        || contract.is_some_and(|contract| {
            contract.original_goal_digest != goal_digest(execution.plan.goal())
        })
    {
        return Err(OrchestrationError::Mission(
            "original Mission goal/source contract differs from accepted semantics".into(),
        ));
    }
    if execution.goal_gate.is_some() && execution.lifecycle == MissionExecutionLifecycle::Completed
    {
        let receipt = execution
            .goal_receipt
            .as_deref()
            .and_then(|digest| control.mission_goal_receipt(digest))
            .ok_or_else(|| {
                OrchestrationError::Mission(
                    "completed Mission lacks its original independent goal receipt".into(),
                )
            })?;
        let completed_at = execution.goal_completed_at.ok_or_else(|| {
            OrchestrationError::Mission(
                "completed Mission lacks its original goal decision time".into(),
            )
        })?;
        let input = &receipt.evidence;
        if input.mission_id != *execution.plan.goal().mission_id()
            || input.plan_revision != execution.plan_revision()
            || input.group_id != execution.group_id
            || input.plan_digest != mission_plan_digest(&execution.plan)
            || input.verdict != domain::SemanticVerdict::Satisfied
            || input.finality != domain::VerifierFinality::Final
            || completed_at
                .as_millis()
                .checked_sub(receipt.received_at.as_millis())
                .is_none_or(|age| age >= input.valid_for_ms)
        {
            return Err(OrchestrationError::Mission(
                "historical Mission goal completion proof is inconsistent".into(),
            ));
        }
    } else if execution.goal_receipt.is_some() || execution.goal_completed_at.is_some() {
        return Err(OrchestrationError::Mission(
            "non-completed Mission carries false goal completion authority".into(),
        ));
    }
    Ok(())
}
