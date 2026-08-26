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
    /// Resource lifetime declared for each Task role before binding.
    #[serde(with = "role_scope_serde")]
    role_scopes: BTreeMap<RoleId, ResourceBindingScope>,
    /// ContextRole continuity identity for each Task role.
    #[serde(with = "role_context_serde")]
    context_roles: BTreeMap<RoleId, ContextRoleId>,
}

/// Encodes role-to-scope maps as JSON arrays without relying on typed object keys.
mod role_scope_serde {
    use super::{ResourceBindingScope, RoleId};
    use serde::{Deserialize, Deserializer, Serialize, Serializer};
    use std::collections::BTreeMap;

    /// Serializes role resource scopes as typed records.
    pub fn serialize<S: Serializer>(
        values: &BTreeMap<RoleId, ResourceBindingScope>,
        serializer: S,
    ) -> Result<S::Ok, S::Error> {
        values.iter().collect::<Vec<_>>().serialize(serializer)
    }

    /// Restores role resource scopes and rejects duplicate roles.
    pub fn deserialize<'de, D: Deserializer<'de>>(
        deserializer: D,
    ) -> Result<BTreeMap<RoleId, ResourceBindingScope>, D::Error> {
        let entries: Vec<(RoleId, ResourceBindingScope)> = Vec::deserialize(deserializer)?;
        let mut values = BTreeMap::new();
        for (role_id, scope) in entries {
            if values.insert(role_id, scope).is_some() {
                return Err(serde::de::Error::custom("duplicate role scope"));
            }
        }
        Ok(values)
    }
}

/// Encodes role-to-ContextRole maps as JSON arrays without typed object keys.
mod role_context_serde {
    use super::{ContextRoleId, RoleId};
    use serde::{Deserialize, Deserializer, Serialize, Serializer};
    use std::collections::BTreeMap;

    /// Serializes role ContextRole mappings as typed records.
    pub fn serialize<S: Serializer>(
        values: &BTreeMap<RoleId, ContextRoleId>,
        serializer: S,
    ) -> Result<S::Ok, S::Error> {
        values.iter().collect::<Vec<_>>().serialize(serializer)
    }

    /// Restores role ContextRole mappings and rejects duplicate roles.
    pub fn deserialize<'de, D: Deserializer<'de>>(
        deserializer: D,
    ) -> Result<BTreeMap<RoleId, ContextRoleId>, D::Error> {
        let entries: Vec<(RoleId, ContextRoleId)> = Vec::deserialize(deserializer)?;
        let mut values = BTreeMap::new();
        for (role_id, context_role_id) in entries {
            if values.insert(role_id, context_role_id).is_some() {
                return Err(serde::de::Error::custom("duplicate role ContextRole"));
            }
        }
        Ok(values)
    }
}

impl TaskExecution {
    /// Creates a pending Task execution without changing Group or resource authority.
    pub fn new(
        task_ref: TaskRef,
        context_id: CoordinationContextId,
        context_roles: BTreeMap<RoleId, ContextRoleId>,
        role_scopes: BTreeMap<RoleId, ResourceBindingScope>,
    ) -> Self {
        Self {
            task_ref,
            context_id,
            lifecycle: TaskExecutionLifecycle::Pending,
            role_scopes,
            context_roles,
            assignments: Vec::new(),
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
        self.assignments
            .iter()
            .find(|assignment| assignment.resource_ids().contains(resource_id))
            .map(|assignment| self.role_scope(assignment.role_id()))
            .unwrap_or(ResourceBindingScope::Task)
    }

    /// Returns the declared resource lifetime for one Task role.
    pub fn role_scope(&self, role_id: &RoleId) -> ResourceBindingScope {
        self.role_scopes
            .get(role_id)
            .copied()
            .unwrap_or(ResourceBindingScope::Task)
    }

    /// Returns all explicit role-level resource lifetimes.
    pub const fn role_scopes(&self) -> &BTreeMap<RoleId, ResourceBindingScope> {
        &self.role_scopes
    }

    /// Returns the ContextRole continuity identity for one Task role, when declared.
    pub fn context_role(&self, role_id: &RoleId) -> Option<&ContextRoleId> {
        self.context_roles.get(role_id)
    }

    /// Returns a copy with the supplied committed role bindings.
    pub fn with_assignments(&self, assignments: Vec<RoleAssignment>) -> Self {
        let mut copy = self.clone();
        copy.assignments = assignments;
        copy
    }

    /// Returns a copy with a new lifecycle, preserving identity and bindings.
    pub fn with_lifecycle(&self, lifecycle: TaskExecutionLifecycle) -> Self {
        Self {
            task_ref: self.task_ref.clone(),
            context_id: self.context_id.clone(),
            lifecycle,
            role_scopes: self.role_scopes.clone(),
            context_roles: self.context_roles.clone(),
            assignments: self.assignments.clone(),
        }
    }
}
