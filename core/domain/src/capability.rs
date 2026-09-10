//! Coarse node capability and local runtime declarations.

use crate::DomainError;

/// Identifies the local runtime implementation behind a node adapter.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct LocalRuntime {
    /// Human-readable name of the local EAIOS implementation.
    name: String,
    /// Version reported by the local runtime implementation.
    version: String,
}

impl LocalRuntime {
    /// Creates a validated local runtime descriptor for an EAIOS or equivalent.
    pub fn new(name: impl Into<String>, version: impl Into<String>) -> Result<Self, DomainError> {
        let name = name.into();
        let version = version.into();
        if name.trim().is_empty() {
            return Err(DomainError::EmptyValue {
                kind: "runtime name",
            });
        }
        if version.trim().is_empty() {
            return Err(DomainError::EmptyValue {
                kind: "runtime version",
            });
        }
        Ok(Self { name, version })
    }

    /// Returns the local runtime implementation name.
    pub fn name(&self) -> &str {
        &self.name
    }

    /// Returns the local runtime implementation version.
    pub fn version(&self) -> &str {
        &self.version
    }
}

/// Capability categories understood by the first DEAIOS slice.
#[derive(
    Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, serde::Serialize, serde::Deserialize,
)]
pub enum CapabilityKind {
    /// Ability to move through the shared physical space.
    Mobility,
    /// Ability to carry or transport a task payload.
    Transport,
    /// Ability to execute compute work.
    Compute,
    /// Ability to produce observations about the world or node state.
    Observation,
}

/// One capability advertised by a node.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct Capability {
    /// Capability category exposed by the node.
    kind: CapabilityKind,
    /// Whether control may currently schedule this capability.
    available: bool,
}

impl Capability {
    /// Creates a capability with an explicit initial availability state.
    pub const fn new(kind: CapabilityKind, available: bool) -> Self {
        Self { kind, available }
    }

    /// Returns the category of this capability.
    pub const fn kind(&self) -> CapabilityKind {
        self.kind
    }

    /// Returns whether the capability may currently be scheduled.
    pub const fn is_available(&self) -> bool {
        self.available
    }
}
