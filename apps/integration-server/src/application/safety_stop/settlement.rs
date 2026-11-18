//! Exact-attempt cancellation settlement; none of these records proves device stopping.

use super::*;

/// Original task evidence produced by its registered Local EAIOS.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct TaskEffectsWitness {
    /// Source-local session fixed before dispatch.
    pub(super) native_session_id: String,
    /// Source-local original request fixed before dispatch.
    pub(super) native_task_id: String,
    /// Closed witness category; this version supports only no-effects.
    pub(super) disposition: String,
    /// Digest of retained source evidence, not a natural-language goal.
    pub(super) evidence_digest: String,
}

/// Explicit administrator assertion over an exact immutable stop record.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct ReconciliationRequest {
    /// Closed request contract.
    schema_version: String,
    /// Stable, bounded administrative command identity.
    command_id: String,
    /// Exact original stop record, including scope and original deadline.
    expected_record_digest: String,
    /// Original Runtime generation; never inferred from current configuration.
    execution_generation: u64,
    /// Explicit operator confirmation; defaults and text alone cannot authorize release.
    confirmed: bool,
    /// no-effects or stopped; neither creates automatic continuation authority.
    disposition: String,
    /// Human-readable basis retained with the original record.
    attestation: String,
    /// Bounded original evidence or references assessed by this administrator.
    evidence: serde_json::Value,
}

impl ReconciliationRequest {
    /// Rejects unknown fields, oversized evidence and ambiguous administrative intent.
    pub(crate) fn parse(raw: &[u8]) -> Result<Self, String> {
        if raw.len() > 8192 {
            return Err("reconciliation request exceeds 8 KiB".into());
        }
        let value: Self = serde_json::from_slice(raw).map_err(|error| error.to_string())?;
        value.validate()?;
        Ok(value)
    }

    /// Rechecks persisted request bounds without manufacturing authority on restore.
    fn validate(&self) -> Result<(), String> {
        if self.schema_version != "roboguide.stop-reconciliation/v0.1"
            || self.command_id.trim().is_empty()
            || self.command_id.len() > 128
            || !is_digest(&self.expected_record_digest)
            || self.execution_generation == 0
            || !self.confirmed
            || !matches!(self.disposition.as_str(), "no-effects" | "stopped")
            || self.attestation.trim().len() < 16
            || self.attestation.len() > 4096
            || self
                .evidence
                .as_object()
                .is_none_or(|object| object.is_empty())
            || serde_json::to_vec(self)
                .map_err(|error| error.to_string())?
                .len()
                > 8192
        {
            return Err("invalid explicit stop reconciliation".into());
        }
        Ok(())
    }
}

/// Independent bases for cancel-only resource settlement.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "kebab-case", deny_unknown_fields)]
enum SettlementBasis {
    /// Current-session original task never dispatched an effect.
    AutomaticNoEffects {
        /// Complete original State evidence, retained through projection replacement.
        proof: Box<SafetyObservationProof>,
        /// Original durable Cancel receipt receive time.
        accepted_at: TimestampMs,
        /// Original Cancelled terminal receive time.
        terminal_at: TimestampMs,
    },
    /// An explicitly authenticated administrator accepts responsibility for cancellation closure.
    Manual {
        /// Identity is supplied by authentication, never by the request body.
        principal: String,
        /// Complete idempotent command and operator evidence.
        request: ReconciliationRequest,
    },
}

/// Optional closed field fences every prior deny-unknown-fields purpose decoder.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct StopSettlement {
    /// Explicit nested generation; the physical stop record remains unchanged.
    schema_version: String,
    /// Original immutable physical record, without changing its phase or clocks.
    original_record_digest: String,
    /// Evidence completion time, or the administrator decision time; never physical confirmation.
    settled_at: TimestampMs,
    /// Exact original basis and authenticated responsibility.
    basis: SettlementBasis,
}

/// Recognizes only the new task-owned no-effects schema and its complete original witness.
pub(super) fn is_no_effects(proof: &SafetyObservationProof) -> bool {
    let Some(witness) = &proof.observation.task_effects else {
        return false;
    };
    proof.record.payload_schema() == domain::TASK_EFFECT_OBSERVATION_SCHEMA
        && proof.observation.assurance == domain::SafetyAssurance::NoEffects
        && proof.observation.process_exit.is_none()
        && proof.observation.terminal_state.as_deref() == Some("CANCELLED")
        && witness.disposition == "no-effects"
        && !witness.native_session_id.trim().is_empty()
        && witness.native_session_id.len() <= 128
        && !witness.native_task_id.trim().is_empty()
        && witness.native_task_id.len() <= 128
        && is_digest(&witness.evidence_digest)
}

/// Settles a timely original cancel without claiming that any device was stopped.
pub(super) fn settle_no_effects(
    purpose: &mut SafetyStopPurpose,
    proof: SafetyObservationProof,
    terminal: TimestampMs,
) -> Result<(), String> {
    let accepted = purpose
        .accepted_at
        .ok_or("no-effects cancellation lacks durable receipt")?;
    let value = StopSettlement {
        schema_version: "roboguide.stop-settlement/v0.1".into(),
        original_record_digest: digest(&purpose.record)?,
        settled_at: accepted.max(terminal).max(proof.received_at),
        basis: SettlementBasis::AutomaticNoEffects {
            proof: Box::new(proof),
            accepted_at: accepted,
            terminal_at: terminal,
        },
    };
    validate_settlement(purpose, &value)?;
    purpose.settlement = Some(value);
    Ok(())
}

/// Validates exact retained evidence without refreshing a session or the original deadline.
fn validate_settlement(purpose: &SafetyStopPurpose, value: &StopSettlement) -> Result<(), String> {
    let identity = &purpose.record.identity;
    if value.schema_version != "roboguide.stop-settlement/v0.1"
        || value.original_record_digest != digest(&purpose.record)?
        || value.settled_at < purpose.record.requested_at
        || purpose.record.phase.confirms_stop()
    {
        return Err("settlement differs from original stop history".into());
    }
    match &value.basis {
        SettlementBasis::Manual { principal, request } => {
            request.validate()?;
            if principal.trim().is_empty()
                || principal.len() > 256
                || purpose.record.phase != SafetyStopPhase::ManualEscalation
                || request.expected_record_digest != value.original_record_digest
                || request.execution_generation != identity.execution_generation
            {
                return Err("manual settlement lacks original scope or principal".into());
            }
        }
        SettlementBasis::AutomaticNoEffects {
            proof,
            accepted_at,
            terminal_at,
        } => {
            let support = purpose
                .support
                .as_ref()
                .ok_or("no-effects support missing")?;
            let policy = purpose.policy.as_ref().ok_or("no-effects policy missing")?;
            let batch: SafeStateBatch = serde_json::from_value(proof.record.value().clone())
                .map_err(|error| error.to_string())?;
            let matching: Vec<_> = batch
                .observations
                .iter()
                .filter(|item| item.execution_id == identity.execution_id)
                .collect();
            if !is_no_effects(proof)
                || !proof.observation.safe_state
                || purpose.record.phase == SafetyStopPhase::ManualEscalation
                || support.assurance != domain::SafetyAssurance::NoEffects
                || batch.observations.len() > 256
                || matching.len() != 1
                || matching[0] != &proof.observation
                || purpose.session_id.as_deref() != Some(proof.source_epoch.as_str())
                || proof.record.source_epoch() != Some(proof.source_epoch.as_str())
                || proof.observation.execution_id != identity.execution_id
                || proof.observation.execution_generation != identity.execution_generation
                || proof.observation.command_id.as_deref() != Some(purpose.command_id.as_str())
                || purpose
                    .receipt_digest
                    .as_ref()
                    .is_none_or(|value| !is_digest(value))
                || purpose.accepted_at != Some(*accepted_at)
                || proof.key != *proof.record.key()
                || proof.received_at != proof.record.received_at()
                || proof.sequence != proof.record.sequence()
                || proof.valid_for_ms != proof.record.valid_for_ms()
                || proof.confidence_millionths != proof.record.confidence_millionths()
                || proof.record_digest != digest(&proof.record)?
                || [*accepted_at, *terminal_at, proof.received_at]
                    .iter()
                    .any(|time| {
                        *time < purpose.record.requested_at || *time >= purpose.record.deadline
                    })
                || value.settled_at != (*accepted_at).max(*terminal_at).max(proof.received_at)
                || policy
                    .evaluate(
                        std::slice::from_ref(&proof.record),
                        state::DecisionKind::Readiness,
                        value.settled_at,
                    )
                    .disposition
                    != state::EvidenceDisposition::Accepted
            {
                return Err("no-effects settlement evidence is inconsistent".into());
            }
        }
    }
    Ok(())
}

/// Rechecks settlement against durable original Runtime history after cold restoration.
pub(super) fn validate_restored_settlement(
    purpose: &SafetyStopPurpose,
    attempt: &runtime::ExecutionAttemptSnapshot,
    controller: &ControllerState,
) -> Result<(), String> {
    if let Some(value) = &purpose.settlement {
        validate_settlement(purpose, value)?;
        if attempt.status() != runtime::ExecutionStatus::Cancelled
            || attempt.command().execution_generation()
                != Some(purpose.record.identity.execution_generation)
            || !controller
                .orchestrator
                .execution(purpose.record.identity.task_ref.mission_id())
                .is_some_and(|mission| {
                    matches!(
                        mission.lifecycle(),
                        orchestration::MissionExecutionLifecycle::Cancelling
                            | orchestration::MissionExecutionLifecycle::Cancelled
                    )
                })
        {
            return Err("settled attempt is not the original cancelled generation".into());
        }
        if let SettlementBasis::AutomaticNoEffects { terminal_at, .. } = &value.basis
            && controller
                .bridge
                .terminal_received_at(attempt.execution_id())
                != Some(*terminal_at)
        {
            return Err("settlement terminal time differs from original Runtime ingress".into());
        }
    }
    Ok(())
}

/// Records one administrator's cancel-only decision under the normal application transaction.
pub(crate) fn reconcile_manual(
    controller: &mut ControllerState,
    execution_id: &str,
    request: ReconciliationRequest,
    principal: &str,
    now: TimestampMs,
) -> Result<serde_json::Value, String> {
    request.validate()?;
    if principal.trim().is_empty() || principal.len() > 256 {
        return Err("authenticated administrator identity required".into());
    }
    let purpose = controller
        .safety_stops
        .get(execution_id)
        .ok_or("unknown stop purpose")?;
    if let Some(value) = &purpose.settlement {
        return match &value.basis {
            SettlementBasis::Manual {
                principal: original,
                request: command,
            } if original == principal && command == &request => {
                serde_json::to_value(value).map_err(|error| error.to_string())
            }
            _ => Err("original stop already has a different settlement".into()),
        };
    }
    if controller.safety_stops.values().filter_map(|purpose| purpose.settlement.as_ref()).any(|value| matches!(&value.basis, SettlementBasis::Manual { request: command, .. } if command.command_id == request.command_id)) {
        return Err("reconciliation command identity already used".into());
    }
    let identity = &purpose.record.identity;
    if controller.bridge.execution_status(execution_id)
        != Some(orchestration::RemoteExecutionStatus::Cancelled)
        || controller.bridge.current_task_attempt_id(
            &identity.group_id,
            &identity.task_ref,
            &identity.role_id,
        ) != Some(execution_id)
        || controller.bridge.current_attempt_generation(
            &identity.group_id,
            &identity.task_ref,
            &identity.role_id,
        ) != Some(identity.execution_generation)
        || !controller
            .orchestrator
            .execution(identity.task_ref.mission_id())
            .is_some_and(|mission| {
                mission.lifecycle() == orchestration::MissionExecutionLifecycle::Cancelling
            })
    {
        return Err(
            "reconciliation requires the current cancelled attempt of a cancelling Mission".into(),
        );
    }
    let value = StopSettlement {
        schema_version: "roboguide.stop-settlement/v0.1".into(),
        original_record_digest: request.expected_record_digest.clone(),
        settled_at: now,
        basis: SettlementBasis::Manual {
            principal: principal.into(),
            request,
        },
    };
    validate_settlement(purpose, &value)?;
    let result = serde_json::to_value(&value).map_err(|error| error.to_string())?;
    controller
        .safety_stops
        .get_mut(execution_id)
        .ok_or("stop disappeared")?
        .settlement = Some(value);
    Ok(result)
}
