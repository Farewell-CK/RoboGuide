//! Mission-scoped logical actor values independent of concrete node deployment.

use crate::{ActorId, MissionId, NodeId};

/// Declares one logical participant in a mission without selecting a node.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MissionActor {
    /// Stable mission-local identity.
    id: ActorId,
}

impl MissionActor {
    /// Creates a logical actor declaration.
    pub const fn new(id: ActorId) -> Self {
        Self { id }
    }

    /// Returns the actor identity.
    pub const fn id(&self) -> &ActorId {
        &self.id
    }
}

/// Control-owned binding of one mission actor to one concrete node.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct ActorBinding {
    /// Mission namespace for the binding authority key.
    mission_id: MissionId,
    /// Logical actor being bound.
    actor_id: ActorId,
    /// Concrete node selected after commitment and binding.
    node_id: NodeId,
}

impl ActorBinding {
    /// Creates a committed actor binding and rejects no deployment details in the domain value.
    pub const fn new(mission_id: MissionId, actor_id: ActorId, node_id: NodeId) -> Self {
        Self {
            mission_id,
            actor_id,
            node_id,
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

    /// Returns the concrete bound node.
    pub const fn node_id(&self) -> &NodeId {
        &self.node_id
    }
}
