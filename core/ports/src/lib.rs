#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! Transport-neutral ports owned by the DEAIOS core.

mod allocation;

pub use allocation::{AllocationStateError, AllocationStateReader, AllocationStateWriter};

use domain::{
    CorrelationId, EventId, EventPayload, ExecutionCommand, NodeEvent, NodeHealthObservation,
    NodeId, NodeLivenessObservation, NodeRegistration, NodeStateSnapshot, NodeStatus, TimestampMs,
};
use std::fmt::{Display, Formatter};

/// A monotonic time source injectable into core tests and runtime adapters.
pub trait Clock {
    /// Returns the current monotonic timestamp.
    fn now(&self) -> TimestampMs;
}

/// The event sink used to make cross-module behavior inspectable.
pub trait EventSink {
    /// Appends one immutable event to the configured evidence sink.
    fn append(
        &mut self,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        causation_id: Option<&EventId>,
        payload: EventPayload,
    );
}

/// Failures exposed by the transport-neutral Shared Node State contract.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SharedStateError {
    /// A health observation referenced a node absent from Shared State.
    UnknownNode(NodeId),
    /// An observation was older than the latest accepted node observation.
    StaleObservation {
        /// Node whose observation was rejected.
        node_id: NodeId,
        /// RoboGuide-local ordering time already represented by Shared State.
        current_ordering_time: TimestampMs,
        /// Older RoboGuide-local ordering time that was rejected.
        incoming_ordering_time: TimestampMs,
    },
}

impl Display for SharedStateError {
    /// Formats a stable Shared State failure for control and adapter diagnostics.
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::UnknownNode(node_id) => write!(formatter, "shared state has no node {node_id}"),
            Self::StaleObservation {
                node_id,
                current_ordering_time,
                incoming_ordering_time,
            } => write!(
                formatter,
                "stale observation for node {node_id}: current={}ms, incoming={}ms",
                current_ordering_time.as_millis(),
                incoming_ordering_time.as_millis()
            ),
        }
    }
}

impl std::error::Error for SharedStateError {}

/// Read access to current cross-mission Shared Node State facts.
pub trait SharedNodeStateReader {
    /// Returns the latest snapshot for one node, if it is currently known.
    fn node(&self, node_id: &NodeId) -> Option<&NodeStateSnapshot>;

    /// Returns all current node snapshots in deterministic node-identity order.
    fn nodes(&self) -> Vec<&NodeStateSnapshot>;
}

/// Write access for accepted registration and health observations.
pub trait SharedNodeStateWriter {
    /// Records a node snapshot unless it would replace newer health evidence.
    fn record_node(&mut self, snapshot: NodeStateSnapshot) -> Result<(), SharedStateError>;

    /// Atomically records local health and its successful-receipt reachability evidence.
    fn record_node_health(
        &mut self,
        observation: NodeHealthObservation,
    ) -> Result<(), SharedStateError>;

    /// Records a non-older RoboGuide-derived liveness observation.
    fn record_node_liveness(
        &mut self,
        node_id: &NodeId,
        observation: NodeLivenessObservation,
    ) -> Result<(), SharedStateError>;
}

/// Errors returned by a local EAIOS or vendor adapter.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NodeGatewayError {
    /// Node whose local adapter rejected or failed the command.
    node_id: NodeId,
    /// Stable diagnostic reason supplied by the adapter.
    reason: String,
}

impl NodeGatewayError {
    /// Creates an adapter error with a node identity and diagnostic reason.
    pub fn new(node_id: NodeId, reason: impl Into<String>) -> Self {
        Self {
            node_id,
            reason: reason.into(),
        }
    }

    /// Returns the node that produced the adapter error.
    pub fn node_id(&self) -> &NodeId {
        &self.node_id
    }

    /// Returns the adapter-provided failure reason.
    pub fn reason(&self) -> &str {
        &self.reason
    }
}

impl Display for NodeGatewayError {
    /// Formats the adapter failure for logs and escalation evidence.
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        write!(formatter, "node {}: {}", self.node_id, self.reason)
    }
}

impl std::error::Error for NodeGatewayError {}

/// The minimum local runtime contract required by DEAIOS Runtime.
pub trait NodeGateway {
    /// Returns the immutable registration advertised by this node.
    fn registration(&self) -> &NodeRegistration;

    /// Returns local health with a source-local observation timestamp.
    fn status(&self) -> NodeStatus;

    /// Executes one role-scoped command using local autonomy and safety rules.
    fn execute(&mut self, command: &ExecutionCommand) -> Result<NodeEvent, NodeGatewayError>;
}
