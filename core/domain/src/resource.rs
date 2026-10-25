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

/// Explicit allocation meaning; historical requirements retain whole-resource exclusion.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ResourceAllocationMode {
    /// Reserve the whole resource after checking its minimum declared capacity.
    #[default]
    Exclusive,
    /// Reserve exactly the declared positive number of Compute units.
    Divisible,
}

impl ResourceAllocationMode {
    /// Omits the historical default from serialized original plans and registration evidence.
    pub const fn is_exclusive(&self) -> bool {
        matches!(self, Self::Exclusive)
    }
}

/// One quantitative resource demand considered jointly with capability and time.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct ResourceRequirement {
    /// Resource category required by the Role.
    pub(crate) kind: ResourceKind,
    /// Minimum declared capacity required from one selected resource.
    pub(crate) units: u32,
    /// Explicit shared Compute admission, absent in historical canonical bytes.
    #[serde(default, skip_serializing_if = "ResourceAllocationMode::is_exclusive")]
    pub(crate) allocation_mode: ResourceAllocationMode,
    /// Versioned semantic unit class, required only for explicit quantitative allocation.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub(crate) resource_class: Option<String>,
}

impl ResourceRequirement {
    /// Creates a positive quantitative resource requirement.
    pub fn new(kind: ResourceKind, units: u32) -> Result<Self, DomainError> {
        if units == 0 {
            return Err(DomainError::EmptyValue {
                kind: "resource requirement units",
            });
        }
        Ok(Self {
            kind,
            units,
            allocation_mode: ResourceAllocationMode::Exclusive,
            resource_class: None,
        })
    }

    /// Creates an explicit positive Compute allocation without changing old minimum-unit meaning.
    pub fn new_divisible(units: u32) -> Result<Self, DomainError> {
        let mut value = Self::new(ResourceKind::Compute, units)?;
        value.allocation_mode = ResourceAllocationMode::Divisible;
        value.resource_class = Some("compute.units/v1".into());
        Ok(value)
    }

    /// Sets a deployment/operation-defined unit class without embedding a resource or device ID.
    pub fn with_resource_class(mut self, class: impl Into<String>) -> Result<Self, DomainError> {
        let class = class.into();
        if self.allocation_mode != ResourceAllocationMode::Divisible
            || class.trim().is_empty()
            || class.len() > 128
        {
            return Err(DomainError::EmptyValue {
                kind: "quantitative resource class",
            });
        }
        self.resource_class = Some(class);
        Ok(self)
    }

    /// Returns the exact semantic unit class; absence never supplies a guessed shared dimension.
    pub fn resource_class(&self) -> Option<&str> {
        self.resource_class.as_deref()
    }

    /// Returns the exact declared allocation interpretation.
    pub const fn allocation_mode(&self) -> ResourceAllocationMode {
        self.allocation_mode
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
    /// Explicit shared-unit contract; historical advertisements remain exclusive.
    #[serde(default, skip_serializing_if = "ResourceAllocationMode::is_exclusive")]
    allocation_mode: ResourceAllocationMode,
    /// Explicit semantic unit class; legacy whole-resource exclusion has none.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    resource_class: Option<String>,
}

impl Resource {
    /// Creates a resource with a positive capacity.
    pub fn new(id: ResourceId, kind: ResourceKind, capacity: u32) -> Result<Self, DomainError> {
        if capacity == 0 {
            return Err(DomainError::EmptyValue {
                kind: "resource capacity",
            });
        }
        Ok(Self {
            id,
            kind,
            capacity,
            allocation_mode: ResourceAllocationMode::Exclusive,
            resource_class: None,
        })
    }

    /// Declares one actual configured Compute pool; this does not construct another resource identity.
    pub fn new_divisible(id: ResourceId, capacity: u32) -> Result<Self, DomainError> {
        let mut value = Self::new(id, ResourceKind::Compute, capacity)?;
        value.allocation_mode = ResourceAllocationMode::Divisible;
        value.resource_class = Some("compute.units/v1".into());
        Ok(value)
    }

    /// Binds the declared pool to one explicit semantic unit class.
    pub fn with_resource_class(mut self, class: impl Into<String>) -> Result<Self, DomainError> {
        let class = class.into();
        if self.allocation_mode != ResourceAllocationMode::Divisible
            || class.trim().is_empty()
            || class.len() > 128
        {
            return Err(DomainError::EmptyValue {
                kind: "quantitative resource class",
            });
        }
        self.resource_class = Some(class);
        Ok(self)
    }

    /// Returns the original declared unit interpretation.
    pub fn resource_class(&self) -> Option<&str> {
        self.resource_class.as_deref()
    }

    /// Returns the declared allocation contract independently of capacity measurements.
    pub const fn allocation_mode(&self) -> ResourceAllocationMode {
        self.allocation_mode
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
