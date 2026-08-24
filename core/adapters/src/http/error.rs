//! Errors produced while establishing or decoding the HTTP reference adapter.

use ports::NodeGatewayErrorKind;
use std::fmt::{Display, Formatter};

/// Failures before a transport-neutral NodeGateway operation can be returned.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum HttpAdapterError {
    /// The underlying HTTP transport failed.
    Transport {
        /// Transport-neutral classification propagated to Runtime after connection.
        kind: NodeGatewayErrorKind,
        /// Stable diagnostic reason.
        reason: String,
    },
    /// The remote payload violated the versioned wire contract.
    Protocol {
        /// Stable payload or compatibility diagnostic.
        reason: String,
    },
}

impl HttpAdapterError {
    /// Creates one protocol failure without leaking serde types to callers.
    pub(crate) fn protocol(reason: impl Into<String>) -> Self {
        Self::Protocol {
            reason: reason.into(),
        }
    }
}

impl Display for HttpAdapterError {
    /// Formats stable adapter diagnostics for smoke applications and logs.
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Transport { kind, reason } => write!(formatter, "HTTP {kind:?}: {reason}"),
            Self::Protocol { reason } => write!(formatter, "HTTP contract violation: {reason}"),
        }
    }
}

impl std::error::Error for HttpAdapterError {}
