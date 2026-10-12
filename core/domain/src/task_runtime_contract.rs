//! Exact Task-local completion admission violations without physical-stop or resource authority.

use crate::TimestampMs;

/// Closed evidence classification for an accepted Task's Controller-observed completion timing.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(tag = "kind", rename_all = "kebab-case")]
pub enum TaskRuntimeContractViolation {
    /// Historical terminal facts have no original Controller receive-time evidence.
    CompletionTimeUnavailable,
    /// Completion was first received before the immutable Mission acceptance anchor.
    CompletionBeforeAcceptance {
        /// Original first terminal receive timestamp, never processing time.
        received_at: TimestampMs,
        /// Immutable Mission acceptance anchor.
        accepted_at: TimestampMs,
    },
    /// The last current Role completion was first received after the accepted deadline.
    CompletionDeadlineExceeded {
        /// Latest original first terminal receive timestamp among the current Roles.
        received_at: TimestampMs,
        /// Absolute deadline derived from the accepted Mission timing.
        deadline: TimestampMs,
    },
}
