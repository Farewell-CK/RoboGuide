//! Runtime clock implementations for deterministic tests and real process execution.

use domain::TimestampMs;
use ports::Clock;
use std::time::Instant;

/// Provides the current runtime timestamp through a fixed value.
#[derive(Debug, Clone, Copy)]
pub struct FixedClock {
    /// Timestamp returned by every clock read.
    timestamp: TimestampMs,
}

impl FixedClock {
    /// Creates a clock that always returns the supplied timestamp.
    pub const fn new(timestamp: TimestampMs) -> Self {
        Self { timestamp }
    }
}

impl Clock for FixedClock {
    /// Returns the configured fixed timestamp.
    fn now(&self) -> TimestampMs {
        self.timestamp
    }
}

/// A process-local monotonic clock suitable for live Runtime receive-time evidence.
#[derive(Debug)]
pub struct SystemMonotonicClock {
    /// Process-local origin used only for elapsed-time comparison.
    origin: Instant,
}

impl SystemMonotonicClock {
    /// Starts a process-local monotonic time domain at zero.
    pub fn new() -> Self {
        Self {
            origin: Instant::now(),
        }
    }
}

impl Default for SystemMonotonicClock {
    /// Starts a fresh process-local monotonic time domain.
    fn default() -> Self {
        Self::new()
    }
}

impl Clock for SystemMonotonicClock {
    /// Returns elapsed process-local milliseconds, saturating beyond `u64` range.
    fn now(&self) -> TimestampMs {
        let elapsed = self.origin.elapsed().as_millis();
        TimestampMs::new(u64::try_from(elapsed).unwrap_or(u64::MAX))
    }
}
