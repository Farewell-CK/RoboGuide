//! Timestamp and task-relative scheduling constraints.

use crate::DomainError;

/// A millisecond clock reading whose comparison domain is defined by its containing field.
///
/// Readings from independent source clocks and RoboGuide clocks are not
/// directly comparable merely because they share this representation.
#[derive(
    Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, serde::Serialize, serde::Deserialize,
)]
pub struct TimestampMs(u64);

impl TimestampMs {
    /// Creates a timestamp from elapsed milliseconds.
    pub const fn new(milliseconds: u64) -> Self {
        Self(milliseconds)
    }

    /// Returns elapsed milliseconds represented by this timestamp.
    pub const fn as_millis(self) -> u64 {
        self.0
    }
}

/// Relative scheduling constraints anchored to Mission acceptance time.
#[derive(Debug, Clone, PartialEq, Eq, Default, serde::Serialize, serde::Deserialize)]
pub struct TaskTiming {
    /// Earliest permitted start offset from Mission acceptance.
    earliest_start_offset_ms: u64,
    /// Latest permitted start offset, when bounded.
    latest_start_offset_ms: Option<u64>,
    /// Completion deadline offset, when declared.
    completion_deadline_offset_ms: Option<u64>,
    /// Estimated execution duration used only for planning future occupancy.
    estimated_duration_ms: Option<u64>,
}

impl TaskTiming {
    /// Creates validated relative time constraints without granting completion authority.
    pub fn new(
        earliest_start_offset_ms: u64,
        latest_start_offset_ms: Option<u64>,
        completion_deadline_offset_ms: Option<u64>,
        estimated_duration_ms: Option<u64>,
    ) -> Result<Self, DomainError> {
        if latest_start_offset_ms.is_some_and(|latest| latest < earliest_start_offset_ms) {
            return Err(DomainError::InvalidDuration {
                kind: "task start window",
            });
        }
        if estimated_duration_ms == Some(0) {
            return Err(DomainError::InvalidDuration {
                kind: "task estimated duration",
            });
        }
        if estimated_duration_ms
            .is_some_and(|duration| earliest_start_offset_ms.checked_add(duration).is_none())
        {
            return Err(DomainError::InvalidDuration {
                kind: "task estimated duration overflow",
            });
        }
        if let Some(deadline) = completion_deadline_offset_ms {
            let duration = estimated_duration_ms.ok_or(DomainError::InvalidDuration {
                kind: "task completion deadline without estimated duration",
            })?;
            let earliest_completion = earliest_start_offset_ms.checked_add(duration).ok_or(
                DomainError::InvalidDuration {
                    kind: "task completion deadline overflow",
                },
            )?;
            if deadline < earliest_completion {
                return Err(DomainError::InvalidDuration {
                    kind: "task completion deadline",
                });
            }
        }
        Ok(Self {
            earliest_start_offset_ms,
            latest_start_offset_ms,
            completion_deadline_offset_ms,
            estimated_duration_ms,
        })
    }

    /// Creates Mission-authored constraints without requiring a Planner duration estimate.
    pub fn new_constraints(
        earliest_start_offset_ms: u64,
        latest_start_offset_ms: Option<u64>,
        completion_deadline_offset_ms: Option<u64>,
    ) -> Result<Self, DomainError> {
        if latest_start_offset_ms.is_some_and(|latest| latest < earliest_start_offset_ms) {
            return Err(DomainError::InvalidDuration {
                kind: "task start window",
            });
        }
        if completion_deadline_offset_ms.is_some_and(|deadline| deadline < earliest_start_offset_ms)
        {
            return Err(DomainError::InvalidDuration {
                kind: "task completion deadline",
            });
        }
        Ok(Self {
            earliest_start_offset_ms,
            latest_start_offset_ms,
            completion_deadline_offset_ms,
            estimated_duration_ms: None,
        })
    }

    /// Returns the earliest start offset from Mission acceptance.
    pub const fn earliest_start_offset_ms(&self) -> u64 {
        self.earliest_start_offset_ms
    }

    /// Returns the latest start offset, when bounded.
    pub const fn latest_start_offset_ms(&self) -> Option<u64> {
        self.latest_start_offset_ms
    }

    /// Returns the completion deadline offset, when declared.
    pub const fn completion_deadline_offset_ms(&self) -> Option<u64> {
        self.completion_deadline_offset_ms
    }

    /// Returns the planning-only estimated duration.
    pub const fn estimated_duration_ms(&self) -> Option<u64> {
        self.estimated_duration_ms
    }
}
