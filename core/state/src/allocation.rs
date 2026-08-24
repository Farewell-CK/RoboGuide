//! Deterministic in-memory storage for normalized Allocation View snapshots.

use domain::{AllocationViewSnapshot, ResourceAllocation, ResourceId, TimestampMs};
use ports::{AllocationStateError, AllocationStateReader, AllocationStateWriter};
use std::collections::BTreeMap;

/// Latest non-authoritative Allocation View indexed by stable ResourceId.
#[derive(Debug, Default)]
pub struct InMemoryAllocationState {
    /// Current normalized records in deterministic resource order.
    allocations: BTreeMap<ResourceId, ResourceAllocation>,
    /// RoboGuide-local time of the current complete projection.
    projected_at: Option<TimestampMs>,
}

impl InMemoryAllocationState {
    /// Creates an empty, uninitialized Allocation View.
    pub const fn new() -> Self {
        Self {
            allocations: BTreeMap::new(),
            projected_at: None,
        }
    }
}

impl AllocationStateReader for InMemoryAllocationState {
    /// Returns one projected allocation by resource identity.
    fn allocation(&self, resource_id: &ResourceId) -> Option<&ResourceAllocation> {
        self.allocations.get(resource_id)
    }

    /// Returns all current records in deterministic ResourceId order.
    fn allocations(&self) -> Vec<&ResourceAllocation> {
        self.allocations.values().collect()
    }

    /// Returns the current snapshot projection time.
    fn snapshot_projected_at(&self) -> Option<TimestampMs> {
        self.projected_at
    }
}

impl AllocationStateWriter for InMemoryAllocationState {
    /// Atomically replaces the complete projection after timestamp and duplicate validation.
    fn replace_allocation_view(
        &mut self,
        snapshot: AllocationViewSnapshot,
    ) -> Result<(), AllocationStateError> {
        if self
            .projected_at
            .is_some_and(|current| snapshot.projected_at() < current)
        {
            return Err(AllocationStateError::StaleProjection {
                current_projected_at: self.projected_at.expect("projection time must exist"),
                incoming_projected_at: snapshot.projected_at(),
            });
        }
        let mut replacement = BTreeMap::new();
        for allocation in snapshot.allocations() {
            if replacement
                .insert(allocation.resource_id().clone(), allocation.clone())
                .is_some()
            {
                return Err(AllocationStateError::DuplicateResource(
                    allocation.resource_id().clone(),
                ));
            }
        }
        self.allocations = replacement;
        self.projected_at = Some(snapshot.projected_at());
        Ok(())
    }
}

#[cfg(test)]
#[path = "allocation_tests.rs"]
mod tests;
