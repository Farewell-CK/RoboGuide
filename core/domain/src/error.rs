//! Domain-wide invariant failures shared by transport-neutral values.

use std::fmt::{Display, Formatter};

/// Errors raised when a domain value violates an invariant.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DomainError {
    /// An identifier or runtime label was empty.
    EmptyValue {
        /// The kind of value that was empty.
        kind: &'static str,
    },
    /// A lease duration or timestamp range could not be represented safely.
    InvalidDuration {
        /// The duration or range that violated a domain invariant.
        kind: &'static str,
    },
    /// An operation attempted to use a lease after its expiry instant.
    LeaseExpired {
        /// The kind of lease operation that was rejected.
        kind: &'static str,
    },
    /// A Mission Plan or Task Graph violated a structural invariant.
    InvalidMissionPlan {
        /// Stable diagnostic reason suitable for adapter and test evidence.
        reason: String,
    },
    /// A Spatial Memory value or catalog transition violated an invariant.
    InvalidSpatialMemory {
        /// Stable diagnostic reason suitable for State and adapter evidence.
        reason: String,
    },
    /// A State record or export declaration violated its semantic contract.
    InvalidState {
        /// Stable diagnostic reason suitable for adapter and API evidence.
        reason: String,
    },
    /// A Memory manifest, provider, or replica violated its semantic contract.
    InvalidMemory {
        /// Stable diagnostic reason suitable for catalog and adapter evidence.
        reason: String,
    },
}

impl Display for DomainError {
    /// Formats a domain invariant violation for logs and test failures.
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::EmptyValue { kind } => write!(formatter, "{kind} must not be empty"),
            Self::InvalidDuration { kind } => write!(formatter, "invalid {kind} duration"),
            Self::LeaseExpired { kind } => write!(formatter, "{kind} lease has expired"),
            Self::InvalidMissionPlan { reason } => {
                write!(formatter, "invalid mission plan: {reason}")
            }
            Self::InvalidSpatialMemory { reason } => {
                write!(formatter, "invalid spatial memory value: {reason}")
            }
            Self::InvalidState { reason } => write!(formatter, "invalid state value: {reason}"),
            Self::InvalidMemory { reason } => write!(formatter, "invalid memory value: {reason}"),
        }
    }
}

impl std::error::Error for DomainError {}
