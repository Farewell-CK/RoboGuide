//! Renewable Control authority for node scheduling eligibility.

use crate::{DomainError, LeaseId, NodeId, TimestampMs};

/// A renewable time-bound authority for a node to remain schedulable.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct NodeLease {
    /// Stable identity of the lease instance.
    lease_id: LeaseId,
    /// Node that owns the lease.
    node_id: NodeId,
    /// RoboGuide-local time at which this lease interval began.
    issued_at: TimestampMs,
    /// RoboGuide-local time after which the lease cannot authorize scheduling.
    expires_at: TimestampMs,
}

impl NodeLease {
    /// Creates a lease with a strictly positive duration.
    pub fn new(
        lease_id: LeaseId,
        node_id: NodeId,
        issued_at: TimestampMs,
        duration_ms: u64,
    ) -> Result<Self, DomainError> {
        if duration_ms == 0 {
            return Err(DomainError::InvalidDuration { kind: "node lease" });
        }
        let expires_at = issued_at
            .as_millis()
            .checked_add(duration_ms)
            .ok_or(DomainError::InvalidDuration { kind: "node lease" })?;
        Ok(Self {
            lease_id,
            node_id,
            issued_at,
            expires_at: TimestampMs::new(expires_at),
        })
    }

    /// Returns the lease identity.
    pub fn lease_id(&self) -> &LeaseId {
        &self.lease_id
    }

    /// Returns the node authorized by this lease.
    pub fn node_id(&self) -> &NodeId {
        &self.node_id
    }

    /// Returns when the lease was issued or last renewed.
    pub const fn issued_at(&self) -> TimestampMs {
        self.issued_at
    }

    /// Returns the first timestamp at which the lease is no longer active.
    pub const fn expires_at(&self) -> TimestampMs {
        self.expires_at
    }

    /// Returns whether the lease is active at the supplied RoboGuide-local time.
    pub const fn is_active_at(&self, now: TimestampMs) -> bool {
        now.as_millis() < self.expires_at.as_millis()
    }

    /// Renews an active lease without changing its identity or owning node.
    pub fn renew(&self, now: TimestampMs, duration_ms: u64) -> Result<Self, DomainError> {
        if !self.is_active_at(now) {
            return Err(DomainError::LeaseExpired { kind: "node" });
        }
        Self::new(
            self.lease_id.clone(),
            self.node_id.clone(),
            now,
            duration_ms,
        )
    }
}
