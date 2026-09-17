//! Deployment-owned physical executor identities and routing snapshots.

use crate::{DomainError, NodeId, PhysicalEntityId, PhysicalEntityRegistryId};
use std::collections::{BTreeMap, BTreeSet};

/// Current executable routing profile for physical entities.
///
/// This is an explicit Node Protocol limitation, not Mission semantics. A future profile may
/// support entity-addressed invocation without changing actor or distinctness semantics.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum PhysicalEntityRoutingProfile {
    /// Every routable Node exposes exactly one independently bindable physical entity.
    OneRoutableEntityPerNode,
}

/// One current deployment association between a physical entity and its routing Node.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct PhysicalEntityRegistration {
    /// Stable deployment-owned physical identity.
    entity_id: PhysicalEntityId,
    /// Node currently authorized to route execution to the entity.
    node_id: NodeId,
}

impl PhysicalEntityRegistration {
    /// Creates one deployment association without changing Mission or Control bindings.
    pub const fn new(entity_id: PhysicalEntityId, node_id: NodeId) -> Self {
        Self { entity_id, node_id }
    }

    /// Returns the physical entity identity.
    pub const fn entity_id(&self) -> &PhysicalEntityId {
        &self.entity_id
    }

    /// Returns the Node currently routing this physical entity.
    pub const fn node_id(&self) -> &NodeId {
        &self.node_id
    }
}

/// One immutable deployment topology snapshot installed into Control composition.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct PhysicalEntityRegistrySnapshot {
    /// Stable registry identity across revisions.
    registry_id: PhysicalEntityRegistryId,
    /// Monotonic deployment-owned revision.
    revision: u64,
    /// Explicit routing limitation under which this snapshot is valid.
    routing_profile: PhysicalEntityRoutingProfile,
    /// Entity associations in stable identity order.
    entities: BTreeMap<PhysicalEntityId, PhysicalEntityRegistration>,
}

impl PhysicalEntityRegistrySnapshot {
    /// Validates one complete deployment snapshot before it can constrain Control decisions.
    pub fn new(
        registry_id: PhysicalEntityRegistryId,
        revision: u64,
        routing_profile: PhysicalEntityRoutingProfile,
        registrations: Vec<PhysicalEntityRegistration>,
    ) -> Result<Self, DomainError> {
        let mut entities = BTreeMap::new();
        let mut nodes = BTreeSet::new();
        for registration in registrations {
            if !nodes.insert(registration.node_id().clone()) {
                return Err(DomainError::InvalidPhysicalEntityRegistry {
                    reason: format!(
                        "physical entity routing profile {routing_profile:?} cannot route multiple entities through node {}",
                        registration.node_id()
                    ),
                });
            }
            if entities
                .insert(registration.entity_id().clone(), registration)
                .is_some()
            {
                return Err(DomainError::InvalidPhysicalEntityRegistry {
                    reason: "physical entity registry contains duplicate identities".to_string(),
                });
            }
        }
        Ok(Self {
            registry_id,
            revision,
            routing_profile,
            entities,
        })
    }

    /// Returns the stable registry identity.
    pub const fn registry_id(&self) -> &PhysicalEntityRegistryId {
        &self.registry_id
    }

    /// Returns the deployment revision represented by this snapshot.
    pub const fn revision(&self) -> u64 {
        self.revision
    }

    /// Returns the executable routing profile.
    pub const fn routing_profile(&self) -> PhysicalEntityRoutingProfile {
        self.routing_profile
    }

    /// Resolves one stable physical identity in this exact revision.
    pub fn entity(&self, entity_id: &PhysicalEntityId) -> Option<&PhysicalEntityRegistration> {
        self.entities.get(entity_id)
    }

    /// Resolves the single routable physical entity currently associated with a Node.
    pub fn entity_for_node(&self, node_id: &NodeId) -> Option<&PhysicalEntityRegistration> {
        self.entities
            .values()
            .find(|registration| registration.node_id() == node_id)
    }

    /// Iterates every association in deterministic physical-entity order.
    pub fn entities(&self) -> impl Iterator<Item = &PhysicalEntityRegistration> {
        self.entities.values()
    }
}
