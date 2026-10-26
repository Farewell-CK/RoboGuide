//! Process-local exact-session continuous heartbeat evidence; no physical-stop authority.

use crate::{LeaseId, NodeId, NodeStatus, TimestampMs};

/// Continuous, wire-validated receive evidence projected by one current Integration session.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NodeSessionLivenessEvidence {
    /// Exact Node owning the established session.
    pub node_id: NodeId,
    /// Boot-unique current session identity, validated against Control's registration anchor.
    pub session_id: String,
    /// Exact lease issued for that session.
    pub lease_id: LeaseId,
    /// Activation time in the shared RoboGuide-local Clock domain.
    pub activated_at: TimestampMs,
    /// Sequence of the first admitted heartbeat, not an assumed contiguous wire ordinal.
    pub first_sequence: u64,
    /// Sequence of the most recently admitted heartbeat.
    pub last_sequence: u64,
    /// Original receive time of the first heartbeat.
    pub first_received_at: TimestampMs,
    /// Original receive time of the latest heartbeat.
    pub last_received_at: TimestampMs,
    /// Maximum elapsed milliseconds from activation/each heartbeat to its successor.
    pub maximum_gap_ms: u64,
    /// Actual latest health payload only while its classification stayed unchanged in this session.
    /// None remains liveness-only evidence and cannot refresh any reported health.
    pub reported_health: Option<NodeStatus>,
}
