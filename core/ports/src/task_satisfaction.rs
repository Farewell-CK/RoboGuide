//! Ports for independently attributed Task satisfaction evidence.

use domain::{TaskRef, TaskSatisfactionEvidence, TimestampMs};
use std::fmt::{Display, Formatter};

/// Failures exposed by the Task satisfaction evidence projection.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TaskSatisfactionStateError {
    /// Older receive-time evidence attempted to replace a newer exact-source verdict.
    StaleEvidence {
        /// Task whose verifier evidence was stale.
        task_ref: TaskRef,
        /// Current RoboGuide-local ordering time.
        current_received_at: TimestampMs,
        /// Rejected RoboGuide-local ordering time.
        incoming_received_at: TimestampMs,
    },
    /// Equal receive time carried a different immutable verdict for the same exact source.
    ConflictingEvidence(TaskRef),
}

impl Display for TaskSatisfactionStateError {
    /// Formats stable evidence-ingestion diagnostics without declaring Task lifecycle.
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::StaleEvidence {
                task_ref,
                current_received_at,
                incoming_received_at,
            } => write!(
                formatter,
                "stale satisfaction evidence for {task_ref}: current={}ms, incoming={}ms",
                current_received_at.as_millis(),
                incoming_received_at.as_millis()
            ),
            Self::ConflictingEvidence(task_ref) => {
                write!(
                    formatter,
                    "conflicting satisfaction evidence for {task_ref}"
                )
            }
        }
    }
}

impl std::error::Error for TaskSatisfactionStateError {}

/// Read access to independently attributed verifier evidence in deterministic source order.
pub trait TaskSatisfactionEvidenceReader {
    /// Returns all latest exact-source verdicts for one Mission-scoped Task.
    fn task_satisfaction_evidence(&self, task_ref: &TaskRef) -> Vec<&TaskSatisfactionEvidence>;
}

/// Write access for normalized verifier facts without Task lifecycle authority.
pub trait TaskSatisfactionEvidenceWriter {
    /// Records one verdict according to exact-source RoboGuide receive ordering.
    fn record_task_satisfaction_evidence(
        &mut self,
        evidence: TaskSatisfactionEvidence,
    ) -> Result<(), TaskSatisfactionStateError>;
}
