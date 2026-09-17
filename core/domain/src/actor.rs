//! Mission-scoped logical actors and Control-owned physical bindings.

use crate::{
    ActorId, ContextRoleId, CoordinationContext, CoordinationContextId, MissionId, NodeId,
    PhysicalEntityId, PhysicalEntityRegistryId,
};
use std::collections::{BTreeMap, BTreeSet};

/// Declares one logical participant whose identity can remain continuous across Mission Tasks.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct MissionActor {
    /// Stable mission-local identity.
    id: ActorId,
    /// Optional immutable reference to an admitted deployment physical entity.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    physical_entity: Option<PhysicalEntityId>,
}

impl MissionActor {
    /// Creates a logical actor whose physical entity is selected later by Control.
    pub const fn new(id: ActorId) -> Self {
        Self {
            id,
            physical_entity: None,
        }
    }

    /// Creates an actor grounded to one admitted deployment physical entity.
    pub const fn new_grounded(id: ActorId, physical_entity: PhysicalEntityId) -> Self {
        Self {
            id,
            physical_entity: Some(physical_entity),
        }
    }

    /// Returns the actor identity.
    pub const fn id(&self) -> &ActorId {
        &self.id
    }

    /// Returns the immutable physical grounding, when one was admitted.
    pub const fn physical_entity(&self) -> Option<&PhysicalEntityId> {
        self.physical_entity.as_ref()
    }

    /// Returns whether this logical actor names a specific physical entity.
    pub const fn is_grounded(&self) -> bool {
        self.physical_entity.is_some()
    }
}

/// One Context-scoped normalized distinct-entity constraint over logical actors.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct ContextPhysicalBindingConstraint {
    /// Context whose collaboration lifetime scopes the constraint.
    context_id: CoordinationContextId,
    /// ContextRoles that must resolve to pairwise distinct physical entities.
    context_role_ids: BTreeSet<ContextRoleId>,
    /// Logical Actors reached through the ContextRole declarations.
    actor_ids: BTreeSet<ActorId>,
}

impl ContextPhysicalBindingConstraint {
    /// Returns the Context owning this constraint.
    pub const fn context_id(&self) -> &CoordinationContextId {
        &self.context_id
    }

    /// Returns the exact ContextRoles covered by this constraint.
    pub const fn context_role_ids(&self) -> &BTreeSet<ContextRoleId> {
        &self.context_role_ids
    }

    /// Returns the logical Actors reached by the constrained ContextRoles.
    pub const fn actor_ids(&self) -> &BTreeSet<ActorId> {
        &self.actor_ids
    }
}

/// Durable Control-facing binding semantics extracted from one accepted MissionPlan.
#[derive(Debug, Clone, Default, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct MissionBindingSemantics {
    /// Actor-declared physical grounding.
    actor_grounding: BTreeMap<ActorId, PhysicalEntityId>,
    /// Context-scoped distinct physical entity constraints.
    context_constraints: Vec<ContextPhysicalBindingConstraint>,
}

impl MissionBindingSemantics {
    /// Collects immutable grounding and Context constraints from one accepted MissionPlan.
    pub fn collect(actors: &[MissionActor], contexts: &[CoordinationContext]) -> Self {
        let actor_grounding = actors
            .iter()
            .filter_map(|actor| {
                actor
                    .physical_entity()
                    .cloned()
                    .map(|entity| (actor.id().clone(), entity))
            })
            .collect();
        let context_constraints = contexts
            .iter()
            .flat_map(|context| {
                context.executor_constraints().iter().map(|constraint| {
                    let actor_ids = constraint
                        .context_role_ids()
                        .iter()
                        .map(|role_id| {
                            context
                                .role(role_id)
                                .expect("MissionPlan validates executor constraint roles")
                                .actor_id()
                                .clone()
                        })
                        .collect();
                    ContextPhysicalBindingConstraint {
                        context_id: context.context_id().clone(),
                        context_role_ids: constraint.context_role_ids().clone(),
                        actor_ids,
                    }
                })
            })
            .collect();
        Self {
            actor_grounding,
            context_constraints,
        }
    }

    /// Returns the physical entity one actor is grounded to.
    pub fn grounding(&self, actor: &ActorId) -> Option<&PhysicalEntityId> {
        self.actor_grounding.get(actor)
    }

    /// Returns every Context-scoped physical binding constraint.
    pub fn context_constraints(&self) -> &[ContextPhysicalBindingConstraint] {
        &self.context_constraints
    }

    /// Returns actors that must remain physically distinct from one actor in one Context.
    pub fn distinct_peers(
        &self,
        context_id: &CoordinationContextId,
        actor_id: &ActorId,
    ) -> BTreeSet<ActorId> {
        let mut peers = BTreeSet::new();
        for constraint in &self.context_constraints {
            if constraint.context_id() == context_id && constraint.actor_ids().contains(actor_id) {
                peers.extend(constraint.actor_ids().iter().cloned());
            }
        }
        peers.remove(actor_id);
        peers
    }

    /// Returns distinct-entity actor groups for one Context.
    pub fn distinct_actor_groups(
        &self,
        context_id: &CoordinationContextId,
    ) -> Vec<BTreeSet<ActorId>> {
        self.context_constraints
            .iter()
            .filter(|constraint| constraint.context_id() == context_id)
            .map(|constraint| constraint.actor_ids().clone())
            .collect()
    }

    /// Returns whether this Mission requires physical entity resolution.
    pub fn requires_physical_entities(&self) -> bool {
        !self.actor_grounding.is_empty() || !self.context_constraints.is_empty()
    }

    /// Iterates every grounded actor and its exact physical entity.
    pub fn grounded_actors(&self) -> impl Iterator<Item = (&ActorId, &PhysicalEntityId)> {
        self.actor_grounding.iter()
    }
}

/// Control-owned binding of one Mission actor to a physical entity and its routing Node.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct ActorBinding {
    /// Mission namespace for the binding authority key.
    mission_id: MissionId,
    /// Logical actor being bound.
    actor_id: ActorId,
    /// Concrete routing Node selected after commitment and binding.
    node_id: NodeId,
    /// Physical executor selected under v0.8 semantics; absent for legacy bindings.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    physical_entity_id: Option<PhysicalEntityId>,
    /// Registry whose topology authorized the physical binding.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    registry_id: Option<PhysicalEntityRegistryId>,
    /// Exact registry revision used at bind time.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    registry_revision: Option<u64>,
}

impl ActorBinding {
    /// Creates a legacy Node-only actor binding for compatibility MissionPlans.
    pub const fn new(mission_id: MissionId, actor_id: ActorId, node_id: NodeId) -> Self {
        Self {
            mission_id,
            actor_id,
            node_id,
            physical_entity_id: None,
            registry_id: None,
            registry_revision: None,
        }
    }

    /// Creates a physical actor binding authorized by one deployment registry revision.
    pub const fn new_physical(
        mission_id: MissionId,
        actor_id: ActorId,
        node_id: NodeId,
        physical_entity_id: PhysicalEntityId,
        registry_id: PhysicalEntityRegistryId,
        registry_revision: u64,
    ) -> Self {
        Self {
            mission_id,
            actor_id,
            node_id,
            physical_entity_id: Some(physical_entity_id),
            registry_id: Some(registry_id),
            registry_revision: Some(registry_revision),
        }
    }

    /// Returns the mission namespace.
    pub const fn mission_id(&self) -> &MissionId {
        &self.mission_id
    }

    /// Returns the logical actor.
    pub const fn actor_id(&self) -> &ActorId {
        &self.actor_id
    }

    /// Returns the current routing Node.
    pub const fn node_id(&self) -> &NodeId {
        &self.node_id
    }

    /// Returns the selected physical entity for a v0.8 binding.
    pub const fn physical_entity_id(&self) -> Option<&PhysicalEntityId> {
        self.physical_entity_id.as_ref()
    }

    /// Returns the registry identity that authorized the physical binding.
    pub const fn registry_id(&self) -> Option<&PhysicalEntityRegistryId> {
        self.registry_id.as_ref()
    }

    /// Returns the registry revision that authorized the physical binding.
    pub const fn registry_revision(&self) -> Option<u64> {
        self.registry_revision
    }
}
