//! Transport-neutral ports for the discoverable generic Memory catalog.

use domain::{MemoryArtifactManifest, MemoryReplicaSnapshot, MemorySelector};
use std::fmt::{Display, Formatter};

/// Failures raised while applying immutable Memory catalog evidence.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum MemoryCatalogError {
    /// A selector was reused for different immutable metadata.
    RevisionConflict(String),
    /// Replica evidence referenced an unknown manifest.
    UnknownRevision(MemorySelector),
    /// A replica lifecycle attempted a non-monotonic transition.
    InvalidReplicaTransition(String),
    /// The supplied event was unrelated to generic Memory.
    UnsupportedEvent,
}

impl Display for MemoryCatalogError {
    /// Formats a deterministic Memory catalog failure.
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::RevisionConflict(reason) => {
                write!(formatter, "memory revision conflict: {reason}")
            }
            Self::UnknownRevision(selector) => {
                write!(formatter, "unknown memory revision {selector}")
            }
            Self::InvalidReplicaTransition(reason) => {
                write!(formatter, "invalid memory replica transition: {reason}")
            }
            Self::UnsupportedEvent => formatter.write_str("event is not generic Memory evidence"),
        }
    }
}

impl std::error::Error for MemoryCatalogError {}

/// Read-only discovery of generic Memory metadata and replica evidence.
pub trait MemoryCatalogReader {
    /// Returns one immutable manifest by selector.
    fn memory(&self, selector: &MemorySelector) -> Option<MemoryArtifactManifest>;

    /// Returns every known manifest in deterministic selector order.
    fn memories(&self) -> Vec<MemoryArtifactManifest>;

    /// Returns node-local replicas for one selector in deterministic node order.
    fn memory_replicas(&self, selector: &MemorySelector) -> Vec<MemoryReplicaSnapshot>;
}

/// Event-sourced write boundary for generic Memory catalog metadata.
pub trait MemoryCatalogWriter {
    /// Applies one immutable evidence event to the catalog.
    fn apply_memory_event(&mut self, event: &domain::EventRecord)
    -> Result<(), MemoryCatalogError>;

    /// Applies one payload at an explicit RoboGuide receive time.
    fn apply_memory_payload(
        &mut self,
        timestamp: domain::TimestampMs,
        payload: &domain::EventPayload,
    ) -> Result<(), MemoryCatalogError>;
}
