#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! Transport-neutral ports owned by the DEAIOS core.

use domain::{
    CorrelationId, EventId, EventPayload, ExecutionCommand, NodeEvent, NodeId, NodeRegistration,
    NodeStatus, TimestampMs,
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

    /// Returns the latest local health snapshot.
    fn status(&self) -> NodeStatus;

    /// Executes one role-scoped command using local autonomy and safety rules.
    fn execute(&mut self, command: &ExecutionCommand) -> Result<NodeEvent, NodeGatewayError>;
}
