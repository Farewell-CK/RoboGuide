//! Conversion of closed Mission wire enumerations into Domain values.

use super::{CapabilityDocument, ResourceDocument, ScopeDocument};
use domain::{CapabilityKind, ResourceBindingScope, ResourceKind};

/// Maps the contract capability enumeration into Domain.
pub(super) const fn capability_from_document(capability: CapabilityDocument) -> CapabilityKind {
    match capability {
        CapabilityDocument::Mobility => CapabilityKind::Mobility,
        CapabilityDocument::Transport => CapabilityKind::Transport,
        CapabilityDocument::Compute => CapabilityKind::Compute,
        CapabilityDocument::Observation => CapabilityKind::Observation,
    }
}

/// Maps the contract resource enumeration into Domain.
pub(super) const fn resource_from_document(resource: ResourceDocument) -> ResourceKind {
    match resource {
        ResourceDocument::Space => ResourceKind::Space,
        ResourceDocument::Compute => ResourceKind::Compute,
        ResourceDocument::Time => ResourceKind::Time,
    }
}

/// Maps the contract lifetime enumeration into Domain.
pub(super) const fn scope_from_document(scope: ScopeDocument) -> ResourceBindingScope {
    match scope {
        ScopeDocument::Task => ResourceBindingScope::Task,
        ScopeDocument::Context => ResourceBindingScope::Context,
    }
}
