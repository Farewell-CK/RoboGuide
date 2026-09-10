//! Quantitative resource requirements and advertised resource values.

use crate::{DomainError, ResourceId};

/// Resource categories that may participate in a proposal or commitment.
#[derive(
    Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, serde::Serialize, serde::Deserialize,
)]
pub enum ResourceKind {
    /// A shared physical region, lane, or corridor.
    Space,
    /// A bounded compute allocation.
    Compute,
    /// A time window or temporal execution slot.
    Time,
}

/// One quantitative resource demand considered jointly with capability and time.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct ResourceRequirement {
    /// Resource category required by the Role.
    pub(crate) kind: ResourceKind,
    /// Minimum declared capacity required from one selected resource.
    pub(crate) units: u32,
}

impl ResourceRequirement {
    /// Creates a positive quantitative resource requirement.
    pub fn new(kind: ResourceKind, units: u32) -> Result<Self, DomainError> {
        if units == 0 {
            return Err(DomainError::EmptyValue {
                kind: "resource requirement units",
            });
        }
        Ok(Self { kind, units })
    }

    /// Returns the required resource category.
    pub const fn kind(&self) -> ResourceKind {
        self.kind
    }

    /// Returns the minimum declared capacity required from one resource.
    pub const fn units(&self) -> u32 {
        self.units
    }
}

/// A resource advertised by a node.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct Resource {
    /// Stable identity of the reservable resource.
    id: ResourceId,
    /// Resource category used during coordination.
    kind: ResourceKind,
    /// Capacity available for the resource's category.
    capacity: u32,
}

impl Resource {
    /// Creates a resource with a positive capacity.
    pub fn new(id: ResourceId, kind: ResourceKind, capacity: u32) -> Result<Self, DomainError> {
        if capacity == 0 {
            return Err(DomainError::EmptyValue {
                kind: "resource capacity",
            });
        }
        Ok(Self { id, kind, capacity })
    }

    /// Returns the resource identity.
    pub fn id(&self) -> &ResourceId {
        &self.id
    }

    /// Returns the resource category.
    pub const fn kind(&self) -> ResourceKind {
        self.kind
    }

    /// Returns the advertised capacity.
    pub const fn capacity(&self) -> u32 {
        self.capacity
    }
}
