//! Mission Intelligence context declarations shared with orchestration and Control.

use crate::{
    ActorId, ContextRoleId, CoordinationContextId, CoordinationMechanism, DomainError,
    ExecutionCouplingMode, ExecutionRelationSpec, GroupSharedViewSpec, PeerChannelSpec,
    ResourceBindingScope, RoleId,
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
    /// Directional execution-time constraints scoped to this semantic Context.
    relations: Vec<ExecutionRelationSpec>,
    /// Default execution coupling mode for TaskExecutions in this Context.
    #[serde(default)]
    coupling_mode: ExecutionCouplingMode,
    /// Optional selective group-scoped state/spatial view declaration.
    #[serde(default)]
    shared_view: Option<GroupSharedViewSpec>,
    /// Optional transport-neutral direct peer channel declaration.
    #[serde(default)]
    peer_channel: Option<PeerChannelSpec>,
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
        Ok(Self {
            context_id,
            roles,
            relations: Vec::new(),
            coupling_mode: ExecutionCouplingMode::Independent,
            shared_view: None,
            peer_channel: None,
        })
    }

    /// Creates a Context with Mission-owned execution-time relation specifications.
    pub fn new_with_relations(
        context_id: CoordinationContextId,
        roles: Vec<ContextRole>,
        relations: Vec<ExecutionRelationSpec>,
    ) -> Result<Self, DomainError> {
        let mut context = Self::new(context_id, roles)?;
        let relation_ids = relations
            .iter()
            .map(ExecutionRelationSpec::relation_id)
            .collect::<BTreeSet<_>>();
        if relation_ids.len() != relations.len() {
            return Err(DomainError::InvalidMissionPlan {
                reason: format!(
                    "context {} has duplicate execution relations",
                    context.context_id
                ),
            });
        }
        context.relations = relations;
        Ok(context)
    }

    /// Creates a Context with coupling mode, shared view, peer channel, and relations.
    pub fn new_with_coordination(
        context_id: CoordinationContextId,
        roles: Vec<ContextRole>,
        relations: Vec<ExecutionRelationSpec>,
        coupling_mode: ExecutionCouplingMode,
        shared_view: Option<GroupSharedViewSpec>,
        peer_channel: Option<PeerChannelSpec>,
    ) -> Result<Self, DomainError> {
        let mut context = Self::new_with_relations(context_id, roles, relations)?;
        if let Some(view) = &shared_view {
            view.validate()?;
            for binding in view.bindings() {
                if context.role(binding.context_role_id()).is_none() {
                    return Err(DomainError::InvalidMissionPlan {
                        reason: format!(
                            "context {} shared view references unknown ContextRole {}",
                            context.context_id,
                            binding.context_role_id()
                        ),
                    });
                }
            }
        }
        if let Some(channel) = &peer_channel
            && (channel.profile_id.trim().is_empty() || channel.message_schema.trim().is_empty())
        {
            return Err(DomainError::InvalidMissionPlan {
                reason: format!(
                    "context {} peer channel contains a blank identity",
                    context.context_id
                ),
            });
        }
        context.coupling_mode = coupling_mode;
        context.shared_view = shared_view;
        context.peer_channel = peer_channel;
        context.validate_mechanisms_for(coupling_mode)?;
        Ok(context)
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

    /// Returns execution-time relation specifications in declaration order.
    pub fn relations(&self) -> &[ExecutionRelationSpec] {
        &self.relations
    }

    /// Returns the default execution coupling mode for this Context.
    pub const fn coupling_mode(&self) -> ExecutionCouplingMode {
        self.coupling_mode
    }

    /// Returns the optional group-scoped shared view declaration.
    pub const fn shared_view(&self) -> Option<&GroupSharedViewSpec> {
        self.shared_view.as_ref()
    }

    /// Returns the optional direct peer channel declaration.
    pub const fn peer_channel(&self) -> Option<&PeerChannelSpec> {
        self.peer_channel.as_ref()
    }

    /// Validates that this Context declares every static mechanism required by a mode.
    pub fn validate_mechanisms_for(&self, mode: ExecutionCouplingMode) -> Result<(), DomainError> {
        if mode.requires(CoordinationMechanism::GroupSharedState) && self.shared_view.is_none() {
            return Err(DomainError::InvalidMissionPlan {
                reason: format!(
                    "context {} mode {mode:?} requires a Group shared view",
                    self.context_id
                ),
            });
        }
        if mode.requires(CoordinationMechanism::RelationEvidence) && self.relations.is_empty() {
            return Err(DomainError::InvalidMissionPlan {
                reason: format!(
                    "context {} mode {mode:?} requires at least one execution relation",
                    self.context_id
                ),
            });
        }
        if mode.requires(CoordinationMechanism::DirectPeerChannel) && self.peer_channel.is_none() {
            return Err(DomainError::InvalidMissionPlan {
                reason: format!(
                    "context {} mode {mode:?} requires a direct peer channel declaration",
                    self.context_id
                ),
            });
        }
        if mode.requires(CoordinationMechanism::DirectPeerChannel) && self.roles.len() < 2 {
            return Err(DomainError::InvalidMissionPlan {
                reason: format!(
                    "context {} direct peer channel requires at least two ContextRoles",
                    self.context_id
                ),
            });
        }
        Ok(())
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
    /// Optional Task-level coupling mode overriding its Context default.
    #[serde(default)]
    coupling_mode_override: Option<ExecutionCouplingMode>,
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
            coupling_mode_override: None,
        }
    }

    /// Creates a Task continuity declaration with an explicit coupling mode override.
    pub const fn new_with_coupling_mode(
        context_id: CoordinationContextId,
        context_roles: BTreeMap<RoleId, ContextRoleId>,
        resource_scopes: BTreeMap<RoleId, ResourceBindingScope>,
        coupling_mode_override: Option<ExecutionCouplingMode>,
    ) -> Self {
        Self {
            context_id,
            context_roles,
            resource_scopes,
            coupling_mode_override,
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

    /// Returns the Task-level coupling mode override, when declared.
    pub const fn coupling_mode_override(&self) -> Option<ExecutionCouplingMode> {
        self.coupling_mode_override
    }
}
