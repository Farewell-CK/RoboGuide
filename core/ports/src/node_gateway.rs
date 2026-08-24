//! Transport-neutral integration boundary for local EAIOS and vendor runtimes.

use domain::{ExecutionCommand, NodeEvent, NodeId, NodeRegistration, NodeStatus};
use std::fmt::{Display, Formatter};

/// Classifies failures without exposing HTTP, ROS, SDK, or other transport details.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum NodeGatewayErrorKind {
    /// The gateway did not respond within its adapter-local deadline.
    Timeout,
    /// The gateway or its backend could not currently be reached.
    Unavailable,
    /// The integration peer returned malformed or incompatible contract data.
    Protocol,
    /// The local EAIOS rejected execution under its own authority or safety policy.
    Rejected,
}

/// Errors returned by a local EAIOS or vendor adapter.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NodeGatewayError {
    /// Node whose local adapter rejected or failed the operation.
    node_id: NodeId,
    /// Transport-neutral failure classification.
    kind: NodeGatewayErrorKind,
    /// Stable diagnostic reason supplied by the adapter.
    reason: String,
}

impl NodeGatewayError {
    /// Creates an adapter error with an explicit transport-neutral classification.
    pub fn new(node_id: NodeId, kind: NodeGatewayErrorKind, reason: impl Into<String>) -> Self {
        Self {
            node_id,
            kind,
            reason: reason.into(),
        }
    }

    /// Returns the node that produced the adapter error.
    pub fn node_id(&self) -> &NodeId {
        &self.node_id
    }

    /// Returns the transport-neutral failure classification.
    pub const fn kind(&self) -> NodeGatewayErrorKind {
        self.kind
    }

    /// Returns the adapter-provided failure reason.
    pub fn reason(&self) -> &str {
        &self.reason
    }
}

impl Display for NodeGatewayError {
    /// Formats the adapter failure for logs and escalation evidence.
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        write!(
            formatter,
            "node {} {:?}: {}",
            self.node_id, self.kind, self.reason
        )
    }
}

impl std::error::Error for NodeGatewayError {}

/// The minimum Local EAIOS, vendor runtime, adapter, or bridge contract required by Runtime.
pub trait NodeGateway {
    /// Returns the immutable registration advertised by this node.
    fn registration(&self) -> &NodeRegistration;

    /// Returns local health with a source-local timestamp or an adapter failure.
    fn status(&self) -> Result<NodeStatus, NodeGatewayError>;

    /// Executes one role-scoped intent using local autonomy and safety rules.
    fn execute(&mut self, command: &ExecutionCommand) -> Result<NodeEvent, NodeGatewayError>;
}
