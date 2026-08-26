//! Mission Intelligence context declarations shared with orchestration and Control.

use crate::{
    ActorId, ContextRoleId, CoordinationContextId, DomainError, ResourceBindingScope, RoleId,
};
use std::collections::{BTreeMap, BTreeSet};

/// One semantic actor role that remains continuous across Tasks in a Context.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct ContextRole {
    /// Stable role identity inside the Context.
    context_role_id: ContextRoleId,
    /// Mission actor represented by this continuous role.
    actor_id: ActorId,
}

impl ContextRole {
    /// Creates a ContextRole association without selecting a runtime node.
    pub const fn new(context_role_id: ContextRoleId, actor_id: ActorId) -> Self {
        Self {
            context_role_id,
            actor_id,
        }
    }

    /// Returns the stable ContextRole identity.
    pub const fn context_role_id(&self) -> &ContextRoleId {
        &self.context_role_id
    }

    /// Returns the Mission actor represented by this role.
    pub const fn actor_id(&self) -> &ActorId {
        &self.actor_id
    }
}

/// One Mission Intelligence semantic context spanning one or more Tasks.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct CoordinationContext {
    /// Stable context identity within the Mission Plan.
    context_id: CoordinationContextId,
    /// Continuous actor roles declared by Mission Intelligence.
    roles: Vec<ContextRole>,
}

impl CoordinationContext {
    /// Creates a context while rejecting duplicate ContextRole identities.
    pub fn new(
        context_id: CoordinationContextId,
        roles: Vec<ContextRole>,
    ) -> Result<Self, DomainError> {
        let role_ids = roles
            .iter()
            .map(ContextRole::context_role_id)
            .collect::<BTreeSet<_>>();
        if role_ids.len() != roles.len() {
            return Err(DomainError::InvalidMissionPlan {
                reason: format!("context {context_id} has duplicate context roles"),
            });
        }
        Ok(Self { context_id, roles })
    }

    /// Returns this context identity.
    pub const fn context_id(&self) -> &CoordinationContextId {
        &self.context_id
    }

    /// Returns ContextRoles in declaration order.
    pub fn roles(&self) -> &[ContextRole] {
        &self.roles
    }

    /// Returns one ContextRole declaration when it belongs to this context.
    pub fn role(&self, role_id: &ContextRoleId) -> Option<&ContextRole> {
        self.roles
            .iter()
            .find(|role| role.context_role_id() == role_id)
    }
}

/// Mission Intelligence continuity declarations attached to one planned Task.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct TaskContinuity {
    /// Semantic Context containing this Task.
    context_id: CoordinationContextId,
    /// Mapping from Task role to persistent ContextRole.
    context_roles: BTreeMap<RoleId, ContextRoleId>,
    /// Resource lifetime declared per Task role.
    resource_scopes: BTreeMap<RoleId, ResourceBindingScope>,
}

impl TaskContinuity {
    /// Creates a Task continuity declaration for later MissionPlan validation.
    pub const fn new(
        context_id: CoordinationContextId,
        context_roles: BTreeMap<RoleId, ContextRoleId>,
        resource_scopes: BTreeMap<RoleId, ResourceBindingScope>,
    ) -> Self {
        Self {
            context_id,
            context_roles,
            resource_scopes,
        }
    }

    /// Returns the semantic Context containing this Task.
    pub const fn context_id(&self) -> &CoordinationContextId {
        &self.context_id
    }

    /// Returns the persistent ContextRole associated with a Task role.
    pub fn context_role(&self, role_id: &RoleId) -> Option<&ContextRoleId> {
        self.context_roles.get(role_id)
    }

    /// Returns all Task-role to ContextRole mappings.
    pub const fn context_roles(&self) -> &BTreeMap<RoleId, ContextRoleId> {
        &self.context_roles
    }

    /// Returns the declared resource lifetime for a Task role.
    pub fn resource_scope(&self, role_id: &RoleId) -> ResourceBindingScope {
        self.resource_scopes
            .get(role_id)
            .copied()
            .unwrap_or(ResourceBindingScope::Task)
    }

    /// Returns every explicit role-level resource lifetime.
    pub const fn resource_scopes(&self) -> &BTreeMap<RoleId, ResourceBindingScope> {
        &self.resource_scopes
    }
}
