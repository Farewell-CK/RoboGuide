//! Ports for replacing and reading a non-authoritative Allocation View.

use domain::{AllocationViewSnapshot, ResourceAllocation, ResourceId, TimestampMs};
use std::fmt::{Display, Formatter};

/// Failures returned by an Allocation State projection implementation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum AllocationStateError {
    /// A snapshot older than the current projection was rejected.
    StaleProjection {
        /// Current RoboGuide-local projection time.
        current_projected_at: TimestampMs,
        /// Older incoming projection time.
        incoming_projected_at: TimestampMs,
    },
    /// A snapshot contained duplicate records for one resource.
    DuplicateResource(ResourceId),
}

impl Display for AllocationStateError {
    /// Formats a stable projection storage failure.
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::StaleProjection {
                current_projected_at,
                incoming_projected_at,
            } => write!(
                formatter,
                "stale allocation projection: current={}ms, incoming={}ms",
                current_projected_at.as_millis(),
                incoming_projected_at.as_millis()
            ),
            Self::DuplicateResource(resource_id) => {
                write!(formatter, "duplicate allocation resource {resource_id}")
            }
        }
    }
}

impl std::error::Error for AllocationStateError {}

/// Read access to the latest normalized Allocation View projection.
pub trait AllocationStateReader {
    /// Returns one projected allocation by resource identity.
    fn allocation(&self, resource_id: &ResourceId) -> Option<&ResourceAllocation>;

    /// Returns all projected allocations in deterministic ResourceId order.
    fn allocations(&self) -> Vec<&ResourceAllocation>;

    /// Returns when the current view was projected, if initialized.
    fn snapshot_projected_at(&self) -> Option<TimestampMs>;
}

/// Whole-view replacement boundary for Control-generated Allocation snapshots.
pub trait AllocationStateWriter {
    /// Replaces the complete view unless the snapshot is stale or malformed.
    fn replace_allocation_view(
        &mut self,
        snapshot: AllocationViewSnapshot,
    ) -> Result<(), AllocationStateError>;
}
