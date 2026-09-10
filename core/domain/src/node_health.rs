//! Local reported health and RoboGuide-observed liveness evidence.

use crate::{LeaseId, NodeId, TimestampMs};

/// The health state a node reports to the distributed system.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum NodeHealth {
    /// The node is available for normal scheduling and execution.
    Online,
    /// The node may execute work but has degraded evidence or capacity.
    Degraded,
    /// The node cannot receive new work.
    Offline,
    /// The node has entered a local safety stop.
    SafeStopped,
}

impl NodeHealth {
    /// Returns whether this health state may be considered by matching.
    pub const fn is_schedulable(self) -> bool {
        matches!(self, Self::Online | Self::Degraded)
    }
}

/// A timestamped health snapshot for a node.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct NodeStatus {
    /// Most recent health classification reported by the node.
    health: NodeHealth,
    /// Source-local time at which the Local EAIOS observed this health.
    observed_at: TimestampMs,
}

/// A health-bearing heartbeat sent by a local EAIOS to DEAIOS.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NodeHeartbeat {
    /// Node sending the heartbeat.
    node_id: NodeId,
    /// Lease the node claims to renew.
    lease_id: LeaseId,
    /// Latest health snapshot observed by the node.
    status: NodeStatus,
}

impl NodeHeartbeat {
    /// Creates a heartbeat for one node and lease.
    pub const fn new(node_id: NodeId, lease_id: LeaseId, status: NodeStatus) -> Self {
        Self {
            node_id,
            lease_id,
            status,
        }
    }

    /// Returns the node sending this heartbeat.
    pub fn node_id(&self) -> &NodeId {
        &self.node_id
    }

    /// Returns the lease being renewed.
    pub fn lease_id(&self) -> &LeaseId {
        &self.lease_id
    }

    /// Returns the health snapshot carried by this heartbeat.
    pub const fn status(&self) -> NodeStatus {
        self.status
    }
}

impl NodeStatus {
    /// Creates a health snapshot with its observation time.
    pub const fn new(health: NodeHealth, observed_at: TimestampMs) -> Self {
        Self {
            health,
            observed_at,
        }
    }

    /// Returns the reported health state.
    pub const fn health(self) -> NodeHealth {
        self.health
    }

    /// Returns when the source observed this health in its own local time domain.
    pub const fn observed_at(self) -> TimestampMs {
        self.observed_at
    }
}

/// A normalized health observation reported by one local EAIOS or adapter.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NodeHealthObservation {
    /// Node whose local health was observed.
    node_id: NodeId,
    /// Latest timestamped health explicitly reported by the local system.
    status: NodeStatus,
    /// RoboGuide-local time at which this observation was received and normalized.
    received_at: TimestampMs,
}

impl NodeHealthObservation {
    /// Creates a transport-neutral node health observation.
    pub const fn new(node_id: NodeId, status: NodeStatus, received_at: TimestampMs) -> Self {
        Self {
            node_id,
            status,
            received_at,
        }
    }

    /// Returns the node that produced the health observation.
    pub const fn node_id(&self) -> &NodeId {
        &self.node_id
    }

    /// Returns the timestamped health reported by the local system.
    pub const fn status(&self) -> NodeStatus {
        self.status
    }

    /// Returns when RoboGuide received this observation in its local time domain.
    pub const fn received_at(&self) -> TimestampMs {
        self.received_at
    }
}

/// Minimal system-observed reachability of one node.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum NodeLiveness {
    /// RoboGuide successfully observed or reached the node.
    Reachable,
    /// RoboGuide can no longer establish current reachability.
    Unreachable,
}

/// A timestamped liveness fact derived by RoboGuide rather than the local EAIOS.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct NodeLivenessObservation {
    /// Current minimal reachability classification.
    liveness: NodeLiveness,
    /// RoboGuide-local time at which it observed this liveness.
    observed_at: TimestampMs,
}

impl NodeLivenessObservation {
    /// Creates a timestamped system-observed liveness fact.
    pub const fn new(liveness: NodeLiveness, observed_at: TimestampMs) -> Self {
        Self {
            liveness,
            observed_at,
        }
    }

    /// Returns the observed reachability classification.
    pub const fn liveness(self) -> NodeLiveness {
        self.liveness
    }

    /// Returns when RoboGuide observed this liveness.
    pub const fn observed_at(self) -> TimestampMs {
        self.observed_at
    }
}
