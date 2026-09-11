//! Deterministic projection of independently attributed Task satisfaction evidence.

use domain::{CapabilityContractRef, StateSource, TaskRef, TaskSatisfactionEvidence};
use ports::{
    TaskSatisfactionEvidenceReader, TaskSatisfactionEvidenceWriter, TaskSatisfactionStateError,
};
use std::cmp::Ordering;
use std::collections::BTreeMap;

/// Exact evidence identity that prevents implicit cross-verifier or cross-source fusion.
type EvidenceKey = (TaskRef, CapabilityContractRef, String, StateSource);

/// Latest verifier verdict per exact Task, contract, predicate, and producer.
#[derive(Debug, Default)]
pub struct InMemoryTaskSatisfactionState {
    /// Independently attributed verdicts in deterministic identity order.
    evidence: BTreeMap<EvidenceKey, TaskSatisfactionEvidence>,
}

impl InMemoryTaskSatisfactionState {
    /// Creates an empty Task satisfaction evidence projection.
    pub const fn new() -> Self {
        Self {
            evidence: BTreeMap::new(),
        }
    }
}

impl TaskSatisfactionEvidenceReader for InMemoryTaskSatisfactionState {
    /// Returns current exact-source evidence without selecting or fusing a verdict.
    fn task_satisfaction_evidence(&self, task_ref: &TaskRef) -> Vec<&TaskSatisfactionEvidence> {
        self.evidence
            .values()
            .filter(|evidence| evidence.task_ref() == task_ref)
            .collect()
    }
}

impl TaskSatisfactionEvidenceWriter for InMemoryTaskSatisfactionState {
    /// Applies receive-time ordering independently for each verifier source.
    fn record_task_satisfaction_evidence(
        &mut self,
        evidence: TaskSatisfactionEvidence,
    ) -> Result<(), TaskSatisfactionStateError> {
        let key = (
            evidence.task_ref().clone(),
            evidence.verifier().clone(),
            evidence.predicate().to_string(),
            evidence.source().clone(),
        );
        if let Some(current) = self.evidence.get(&key) {
            match evidence.received_at().cmp(&current.received_at()) {
                Ordering::Less => {
                    return Err(TaskSatisfactionStateError::StaleEvidence {
                        task_ref: evidence.task_ref().clone(),
                        current_received_at: current.received_at(),
                        incoming_received_at: evidence.received_at(),
                    });
                }
                Ordering::Equal if current != &evidence => {
                    return Err(TaskSatisfactionStateError::ConflictingEvidence(
                        evidence.task_ref().clone(),
                    ));
                }
                Ordering::Equal => return Ok(()),
                Ordering::Greater => {}
            }
        }
        self.evidence.insert(key, evidence);
        Ok(())
    }
}

#[cfg(test)]
#[path = "task_satisfaction_tests.rs"]
mod tests;
