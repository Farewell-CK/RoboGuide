//! Task execution units hosted by a Mission-level Execution Group.

use crate::{
    ContextRoleId, CoordinationContextId, ResourceBindingScope, ResourceId, RoleAssignment, RoleId,
    TaskRef,
};
use std::collections::BTreeMap;

/// Lifecycle of one Task execution inside a long-lived Execution Group.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum TaskExecutionLifecycle {
    /// The Task exists but its DAG dependencies are not yet satisfied.
    Pending,
    /// The Task is eligible to be committed and started.
    Ready,
    /// At least one role of the Task is executing.
    Active,
    /// The Task cannot progress until reconciliation succeeds.
    Blocked,
    /// All Task roles completed successfully.
    Completed,
    /// The Task reached an unrecoverable failure.
    Failed,
    /// The Task was explicitly cancelled.
    Cancelled,
}

/// One Task execution unit retained by its parent Mission-level Group.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct TaskExecution {
    /// Mission-scoped Task identity.
    task_ref: TaskRef,
    /// Mission Intelligence context referenced by this Task.
    context_id: CoordinationContextId,
    /// Current Task execution lifecycle.
    lifecycle: TaskExecutionLifecycle,
    /// Current Task-local role bindings.
    assignments: Vec<RoleAssignment>,
    /// Lifetime declared for each resource binding.
    binding_scopes: BTreeMap<ResourceId, ResourceBindingScope>,
    /// Optional ContextRole continuity identity for each Task role.
    context_roles: BTreeMap<RoleId, ContextRoleId>,
}

impl TaskExecution {
    /// Creates a pending Task execution without changing Group or resource authority.
    pub fn new(
        task_ref: TaskRef,
        context_id: CoordinationContextId,
        assignments: Vec<RoleAssignment>,
    ) -> Self {
        Self {
            task_ref,
            context_id,
            lifecycle: TaskExecutionLifecycle::Pending,
            binding_scopes: assignments
                .iter()
                .flat_map(|assignment| assignment.resource_ids().iter().cloned())
                .map(|resource_id| (resource_id, ResourceBindingScope::Task))
                .collect(),
            context_roles: BTreeMap::new(),
            assignments,
        }
    }

    /// Returns the Task identity represented by this execution unit.
    pub const fn task_ref(&self) -> &TaskRef {
        &self.task_ref
    }

    /// Returns the Mission Intelligence context referenced by this Task.
    pub const fn context_id(&self) -> &CoordinationContextId {
        &self.context_id
    }

    /// Returns the current Task execution lifecycle.
    pub const fn lifecycle(&self) -> TaskExecutionLifecycle {
        self.lifecycle
    }

    /// Returns current Task-local role bindings.
    pub fn assignments(&self) -> &[RoleAssignment] {
        &self.assignments
    }

    /// Returns the declared lifetime of one bound resource.
    pub fn binding_scope(&self, resource_id: &ResourceId) -> ResourceBindingScope {
        self.binding_scopes
            .get(resource_id)
            .copied()
            .unwrap_or(ResourceBindingScope::Task)
    }

    /// Returns all resource lifetimes in deterministic resource order.
    pub const fn binding_scopes(&self) -> &BTreeMap<ResourceId, ResourceBindingScope> {
        &self.binding_scopes
    }

    /// Returns the ContextRole continuity identity for one Task role, when declared.
    pub fn context_role(&self, role_id: &RoleId) -> Option<&ContextRoleId> {
        self.context_roles.get(role_id)
    }

    /// Returns a copy associating one Task role with a persistent ContextRole.
    pub fn with_context_role(&self, role_id: RoleId, context_role_id: ContextRoleId) -> Self {
        let mut copy = self.clone();
        if copy
            .assignments
            .iter()
            .any(|assignment| assignment.role_id() == &role_id)
        {
            copy.context_roles.insert(role_id, context_role_id);
        }
        copy
    }

    /// Returns a copy with one resource lifetime changed before execution begins.
    pub fn with_binding_scope(&self, resource_id: ResourceId, scope: ResourceBindingScope) -> Self {
        let mut copy = self.clone();
        if copy
            .assignments
            .iter()
            .any(|assignment| assignment.resource_ids().contains(&resource_id))
        {
            copy.binding_scopes.insert(resource_id, scope);
        }
        copy
    }

    /// Returns a copy with the supplied role bindings retained and missing resource scopes removed.
    pub fn with_assignments(&self, assignments: Vec<RoleAssignment>) -> Self {
        let mut copy = self.clone();
        copy.assignments = assignments;
        copy.binding_scopes.retain(|resource_id, _| {
            copy.assignments
                .iter()
                .any(|assignment| assignment.resource_ids().contains(resource_id))
        });
        copy.context_roles.retain(|role_id, _| {
            copy.assignments
                .iter()
                .any(|assignment| assignment.role_id() == role_id)
        });
        copy
    }

    /// Returns a copy with a new lifecycle, preserving identity and bindings.
    pub fn with_lifecycle(&self, lifecycle: TaskExecutionLifecycle) -> Self {
        Self {
            task_ref: self.task_ref.clone(),
            context_id: self.context_id.clone(),
            lifecycle,
            binding_scopes: self.binding_scopes.clone(),
            context_roles: self.context_roles.clone(),
            assignments: self.assignments.clone(),
        }
    }
}
