//! Deployment-authorized current State costs; planning observations never grant allocation authority.

use crate::{ControlError, ControlPlane};
use domain::{
    DecisionKind, MissionId, SchedulingCostAssessment, SchedulingCostFailure, SchedulingMetrics,
    TimestampMs,
};
use ports::SharedNodeStateReader;

impl ControlPlane {
    /// Evaluates every configured tenant cost source once under the original current Node session.
    pub fn scheduling_cost_assessments<S: SharedNodeStateReader>(
        &self,
        state: &S,
        mission: &MissionId,
        now: TimestampMs,
    ) -> Vec<SchedulingCostAssessment> {
        let Some(registry) = self.decision_policy_registry() else {
            return Vec::new();
        };
        let Some(tenant) = self.mission_tenant(mission) else {
            return Vec::new();
        };
        let records = state.decision_records();
        let registry_digest = registry.digest().expect("validated registry serializes");
        registry
            .scheduling_costs
            .values()
            .filter(|policy| policy.enabled && policy.tenant_id == tenant)
            .map(|policy| {
                let current = state.node(&policy.node_id).is_some()
                    && self.node_tenant_allowed(mission, &policy.node_id);
                let records = records
                    .iter()
                    .filter(|record| {
                        current
                            && record.key().source().node_id() == Some(&policy.node_id)
                            && record.source_epoch().is_some_and(|epoch| {
                                self.source_session_is_current(&policy.node_id, epoch, now)
                            })
                    })
                    .cloned()
                    .collect::<Vec<_>>();
                let admitted = policy
                    .policy
                    .evaluate(&records, DecisionKind::Schedule, now);
                let scored = admitted.record.as_ref().map(|record| {
                    serde_json::from_value::<SchedulingMetrics>(record.value().clone())
                        .map_err(|_| SchedulingCostFailure::InvalidPayload)
                        .and_then(|metrics| policy.score(&metrics))
                });
                SchedulingCostAssessment {
                    registry_digest: registry_digest.clone(),
                    policy: policy.clone(),
                    disposition: admitted.disposition,
                    record: admitted.record,
                    score: scored
                        .as_ref()
                        .and_then(|score| score.as_ref().ok().copied()),
                    failure: scored.and_then(Result::err),
                    decided_at: now,
                }
            })
            .collect()
    }

    /// Appends original planning observations through the existing durable State policy audit codec.
    pub fn audit_scheduling_costs<E: ports::EventSink>(
        &self,
        task: &domain::TaskRef,
        evidence: &[SchedulingCostAssessment],
        now: TimestampMs,
        correlation: &domain::CorrelationId,
        events: &mut E,
    ) -> Result<(), ControlError> {
        self.validate_scheduling_cost_history(task.mission_id(), evidence)?;
        if evidence.iter().any(|receipt| receipt.decided_at != now) {
            return Err(ControlError::InvalidProposal(
                "cost audit cannot relabel original decision time".into(),
            ));
        }
        for receipt in evidence {
            events.append(
                now,
                correlation,
                None,
                domain::EventPayload::StatePolicyDecision {
                    task_ref: task.clone(),
                    receipts: vec![domain::DecisionEvidenceReceipt {
                        registry_digest: receipt.registry_digest.clone(),
                        policy_id: receipt.policy.policy.policy_id.clone(),
                        policy_revision: receipt.policy.policy.revision,
                        decision: DecisionKind::Schedule,
                        node_id: receipt.policy.node_id.clone(),
                        disposition: receipt.disposition,
                        condition_passed: receipt.score.is_some(),
                        record: receipt.record.clone(),
                        decided_at: receipt.decided_at,
                    }],
                },
            );
        }
        Ok(())
    }

    /// Verifies retained planning evidence against its original policy generation without renewing it.
    pub(crate) fn validate_scheduling_cost_history(
        &self,
        mission: &MissionId,
        evidence: &[SchedulingCostAssessment],
    ) -> Result<(), ControlError> {
        let mut candidates = std::collections::BTreeSet::new();
        for receipt in evidence {
            receipt.validate().map_err(ControlError::InvalidProposal)?;
            let registry = self
                .decision_policy_history
                .values()
                .find(|registry| {
                    registry.digest().ok().as_deref() == Some(&receipt.registry_digest)
                })
                .ok_or_else(|| {
                    ControlError::InvalidProposal(
                        "scheduling cost original policy history is absent".into(),
                    )
                })?;
            if !candidates.insert(&receipt.policy.node_id)
                || self.mission_tenant(mission) != Some(receipt.policy.tenant_id.as_str())
                || registry
                    .scheduling_costs
                    .get(&receipt.policy.policy.policy_id)
                    != Some(&receipt.policy)
            {
                return Err(ControlError::InvalidProposal(
                    "scheduling cost original attribution conflicts".into(),
                ));
            }
        }
        Ok(())
    }
}
