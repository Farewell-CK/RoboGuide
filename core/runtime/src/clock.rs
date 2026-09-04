//! Runtime clock implementations for deterministic tests and real process execution.

use domain::TimestampMs;
use ports::Clock;
use std::sync::Mutex;
use std::time::{Instant, SystemTime, UNIX_EPOCH};

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
    /// Wall-clock epoch captured at process start for restart-stable receive timestamps.
    wall_origin_ms: u64,
    /// Last returned value, preventing small wall-clock regressions within one process.
    high_water_ms: Mutex<u64>,
}

impl SystemMonotonicClock {
    /// Starts a process-local monotonic time domain at zero.
    pub fn new() -> Self {
        let wall_origin_ms = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|duration| u64::try_from(duration.as_millis()).unwrap_or(u64::MAX))
            .unwrap_or(0);
        Self {
            origin: Instant::now(),
            wall_origin_ms,
            high_water_ms: Mutex::new(wall_origin_ms),
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
        let monotonic = self
            .wall_origin_ms
            .saturating_add(u64::try_from(elapsed).unwrap_or(u64::MAX));
        let mut high_water = self
            .high_water_ms
            .lock()
            .expect("SystemMonotonicClock high-water mutex is not poisoned");
        *high_water = (*high_water).max(monotonic);
        TimestampMs::new(*high_water)
    }
}
