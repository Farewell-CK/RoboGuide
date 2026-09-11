//! Source-aware planning duration evidence kept outside Mission-authored constraints.

use crate::{DomainError, StateSource, TaskRef, TimestampMs};

/// One task-wide duration estimate supplied to scheduling by an attributed evidence source.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct TaskDurationEstimate {
    /// Exact Mission-scoped Task to which the estimate applies.
    task_ref: TaskRef,
    /// Positive estimated execution duration.
    duration_ms: u64,
    /// Node or RoboGuide component that produced the estimate.
    source: StateSource,
    /// RoboGuide-local receive time used for freshness.
    received_at: TimestampMs,
    /// Maximum accepted age in the RoboGuide-local time domain.
    valid_for_ms: u64,
}

impl TaskDurationEstimate {
    /// Creates task-wide planning evidence while rejecting zero duration or validity.
    pub fn new(
        task_ref: TaskRef,
        duration_ms: u64,
        source: StateSource,
        received_at: TimestampMs,
        valid_for_ms: u64,
    ) -> Result<Self, DomainError> {
        if duration_ms == 0 {
            return Err(DomainError::InvalidDuration {
                kind: "task duration estimate",
            });
        }
        if valid_for_ms == 0 {
            return Err(DomainError::InvalidDuration {
                kind: "task duration estimate validity",
            });
        }
        Ok(Self {
            task_ref,
            duration_ms,
            source,
            received_at,
            valid_for_ms,
        })
    }

    /// Returns the exact Task described by this estimate.
    pub const fn task_ref(&self) -> &TaskRef {
        &self.task_ref
    }

    /// Returns the positive planning duration in milliseconds.
    pub const fn duration_ms(&self) -> u64 {
        self.duration_ms
    }

    /// Returns the attributed producer without granting it scheduling authority.
    pub const fn source(&self) -> &StateSource {
        &self.source
    }

    /// Returns the RoboGuide-local receive time used for age checks.
    pub const fn received_at(&self) -> TimestampMs {
        self.received_at
    }

    /// Returns the maximum accepted receive-time age.
    pub const fn valid_for_ms(&self) -> u64 {
        self.valid_for_ms
    }

    /// Reports whether the estimate is current in the supplied RoboGuide-local time domain.
    pub fn is_fresh_at(&self, now: TimestampMs) -> bool {
        now.as_millis()
            .checked_sub(self.received_at.as_millis())
            .is_some_and(|age| age <= self.valid_for_ms)
    }
}
