//! Source-preserving Belief and independent verifier projections behind current Control registries.

use super::*;

impl<E: EventSink + Clone> IntegrationRuntimeBridge<E> {
    /// Derives changed current Beliefs from raw admitted source records inside the owning transaction.
    pub fn refresh_beliefs(
        &mut self,
        now: TimestampMs,
        correlation: &CorrelationId,
    ) -> Result<(), IntegrationRuntimeError> {
        let Some(registry) = self.control.belief_policy_registry().cloned() else {
            return Ok(());
        };
        let raw = self
            .state_records
            .records()
            .into_iter()
            .filter(|record| self.control.belief_source_is_current(record, now))
            .collect::<Vec<_>>();
        for policy in registry.policies.values() {
            let previous = self.state_records.records().into_iter().find(|record| {
                record.key().source()
                    == &StateSource::RoboGuide {
                        component: format!("belief/{}/{}", policy.tenant_id, policy.policy_id),
                    }
                    && record.key().object() == &policy.admission.object
                    && record.key().channel_id() == "belief"
            });
            if let Some(record) = state::derive_belief(policy, &raw, previous.as_ref(), now)
                .map_err(IntegrationRuntimeError::Protocol)?
            {
                self.state_records
                    .record_state(record.clone())
                    .map_err(|error| IntegrationRuntimeError::Protocol(error.to_string()))?;
                self.events.append(
                    now,
                    correlation,
                    None,
                    EventPayload::StateRecordObserved { record },
                );
            }
        }
        Ok(())
    }

    /// Projects only an original independently authenticated receipt already retained by Control.
    pub fn project_semantic_verifier(
        &mut self,
        receipt: &domain::SemanticVerifierReceipt,
        correlation: &CorrelationId,
    ) -> Result<(), IntegrationRuntimeError> {
        if !self
            .control
            .semantic_verifier_receipts()
            .iter()
            .any(|original| original == receipt)
        {
            return Err(IntegrationRuntimeError::Protocol(
                "verifier State projection lacks original admission".into(),
            ));
        }
        let input = &receipt.evidence;
        let object = StateObjectRef::new(
            StateObjectClass::RoboGuide,
            "task-satisfaction",
            serde_json::to_string(&input.task_ref)
                .map_err(|error| IntegrationRuntimeError::Protocol(error.to_string()))?,
        )?;
        let record = StateRecord::new(
            object,
            StateSemantic::Observed,
            StateSource::RoboGuide {
                component: format!("verifier/{}/{}", input.tenant_id, input.source_id),
            },
            "semantic-verdict",
            "roboguide.semantic-verifier-state/v0.1",
            serde_json::json!({"task_ref":input.task_ref,"verdict":input.verdict,"finality":input.finality,"evidence_id":input.evidence_id,
                "source_revision":input.source_revision,"plan_digest":input.plan_digest,"attempts":input.attempts,
                "predicate":input.predicate,"artifact_digest":input.artifact_digest}),
            Some(TimestampMs::new(input.source_observed_at_ms)),
            TimestampMs::new(receipt.received_at_ms),
            input.valid_for_ms,
            None,
            input.sequence,
        )?;
        self.state_records
            .record_state(record.clone())
            .map_err(|error| IntegrationRuntimeError::Protocol(error.to_string()))?;
        self.events.append(
            TimestampMs::new(receipt.received_at_ms),
            correlation,
            None,
            EventPayload::StateRecordObserved { record },
        );
        Ok(())
    }

    /// Fences an old verifier projection when Runtime has replaced any covered physical attempt.
    pub(super) fn verifier_state_is_current(&self, record: &StateRecord) -> bool {
        let StateSource::RoboGuide { component } = record.key().source() else {
            return true;
        };
        if !component.starts_with("verifier/") {
            return true;
        }
        let Some(task) = record
            .value()
            .get("task_ref")
            .and_then(|value| serde_json::from_value::<domain::TaskRef>(value.clone()).ok())
        else {
            return false;
        };
        let Some(attempts) = record.value().get("attempts").and_then(|value| {
            serde_json::from_value::<Vec<domain::VerifierAttemptRef>>(value.clone()).ok()
        }) else {
            return false;
        };
        let group = self.control.group_ids().into_iter().find(|group| {
            self.control
                .group(group)
                .is_some_and(|group| group.task_execution(&task).is_some())
        });
        group.is_some_and(|group| {
            !attempts.is_empty()
                && attempts.iter().all(|attempt| {
                    self.runtime
                        .current_attempt_id(&group, &task, &attempt.role_id)
                        == Some(attempt.attempt_id.as_str())
                })
        })
    }
}
