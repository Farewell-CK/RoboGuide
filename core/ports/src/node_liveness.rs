//! Transport-neutral Node heartbeat and lease policy.

use std::fmt::{Display, Formatter};
use std::time::Duration;

/// Default interval between Node heartbeat messages.
pub const DEFAULT_NODE_HEARTBEAT_INTERVAL_MS: u64 = 5_000;

/// Default duration for the transport and Control Node lease.
pub const DEFAULT_NODE_LEASE_DURATION_MS: u64 = 15_000;

/// Immutable policy shared by the Node transport and Controller application.
///
/// The policy carries only lease transport values. Reported health freshness, State TTLs, and
/// peer readiness validity remain independent evidence policies owned by their respective
/// authorities.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct NodeLeasePolicy {
    heartbeat_interval_ms: u64,
    lease_duration_ms: u64,
}

impl NodeLeasePolicy {
    /// Constructs a policy after validating its lease safety relationship.
    pub const fn try_new(
        heartbeat_interval_ms: u64,
        lease_duration_ms: u64,
    ) -> Result<Self, NodeLeasePolicyError> {
        if heartbeat_interval_ms == 0 || lease_duration_ms == 0 {
            return Err(NodeLeasePolicyError::NonPositiveDuration);
        }
        let minimum_lease = match heartbeat_interval_ms.checked_mul(2) {
            Some(value) => value,
            None => return Err(NodeLeasePolicyError::DurationOverflow),
        };
        if lease_duration_ms < minimum_lease {
            return Err(NodeLeasePolicyError::LeaseTooShort {
                heartbeat_interval_ms,
                lease_duration_ms,
            });
        }
        Ok(Self {
            heartbeat_interval_ms,
            lease_duration_ms,
        })
    }

    /// Returns the interval advertised to a Node between heartbeat messages.
    pub const fn heartbeat_interval_ms(self) -> u64 {
        self.heartbeat_interval_ms
    }

    /// Returns the lease duration used by transport route fencing and Control renewal.
    pub const fn lease_duration_ms(self) -> u64 {
        self.lease_duration_ms
    }

    /// Returns the heartbeat interval as a process-local transport duration.
    pub const fn heartbeat_interval(self) -> Duration {
        Duration::from_millis(self.heartbeat_interval_ms)
    }

    /// Returns the lease duration as a process-local transport duration.
    pub const fn lease_duration(self) -> Duration {
        Duration::from_millis(self.lease_duration_ms)
    }
}

impl Default for NodeLeasePolicy {
    /// Creates the stable 5-second heartbeat and 15-second lease policy.
    fn default() -> Self {
        Self::try_new(
            DEFAULT_NODE_HEARTBEAT_INTERVAL_MS,
            DEFAULT_NODE_LEASE_DURATION_MS,
        )
        .expect("default Node lease policy is valid")
    }
}

/// Validation failures for one Node heartbeat/lease policy.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NodeLeasePolicyError {
    /// Either duration was zero.
    NonPositiveDuration,
    /// Doubling the heartbeat interval overflowed `u64`.
    DurationOverflow,
    /// The lease did not cover at least two heartbeat intervals.
    LeaseTooShort {
        /// Configured heartbeat interval.
        heartbeat_interval_ms: u64,
        /// Configured lease duration.
        lease_duration_ms: u64,
    },
}

impl Display for NodeLeasePolicyError {
    /// Formats a stable startup configuration diagnostic.
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::NonPositiveDuration => {
                write!(
                    formatter,
                    "Node heartbeat and lease durations must be positive"
                )
            }
            Self::DurationOverflow => {
                write!(
                    formatter,
                    "Node heartbeat duration overflows lease validation"
                )
            }
            Self::LeaseTooShort {
                heartbeat_interval_ms,
                lease_duration_ms,
            } => write!(
                formatter,
                "Node lease duration {lease_duration_ms}ms must be at least twice heartbeat interval {heartbeat_interval_ms}ms"
            ),
        }
    }
}

impl std::error::Error for NodeLeasePolicyError {}

#[cfg(test)]
mod tests {
    use super::*;

    /// The default policy preserves the existing wire values.
    #[test]
    fn default_values_remain_compatible() {
        let policy = NodeLeasePolicy::default();
        assert_eq!(policy.heartbeat_interval_ms(), 5_000);
        assert_eq!(policy.lease_duration_ms(), 15_000);
    }

    /// Invalid durations fail closed before they reach a transport or Control authority.
    #[test]
    fn validation_rejects_invalid_relationships() {
        assert_eq!(
            NodeLeasePolicy::try_new(0, 1),
            Err(NodeLeasePolicyError::NonPositiveDuration)
        );
        assert_eq!(
            NodeLeasePolicy::try_new(6_000, 11_999),
            Err(NodeLeasePolicyError::LeaseTooShort {
                heartbeat_interval_ms: 6_000,
                lease_duration_ms: 11_999,
            })
        );
        assert_eq!(
            NodeLeasePolicy::try_new(u64::MAX, u64::MAX),
            Err(NodeLeasePolicyError::DurationOverflow)
        );
    }
}
