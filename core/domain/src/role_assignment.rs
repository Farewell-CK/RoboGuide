//! Proposed or committed role-to-node and resource assignment value.

use crate::{NodeId, ResourceId, RoleId};

/// A node's proposed assignment for one execution-group role.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct RoleAssignment {
    /// Role receiving the assignment.
    role_id: RoleId,
    /// Node selected to execute the role.
    node_id: NodeId,
    /// Resources proposed for the role's execution.
    resource_ids: Vec<ResourceId>,
}

impl RoleAssignment {
    /// Creates a role assignment before proposal validation.
    pub const fn new(role_id: RoleId, node_id: NodeId, resource_ids: Vec<ResourceId>) -> Self {
        Self {
            role_id,
            node_id,
            resource_ids,
        }
    }

    /// Returns the assigned role.
    pub fn role_id(&self) -> &RoleId {
        &self.role_id
    }

    /// Returns the assigned node.
    pub fn node_id(&self) -> &NodeId {
        &self.node_id
    }

    /// Returns resources reserved for this role.
    pub fn resource_ids(&self) -> &[ResourceId] {
        &self.resource_ids
    }
}
