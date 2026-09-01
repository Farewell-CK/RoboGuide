//! Mission-owned execution coordination relation specifications and observable states.

use crate::{DomainError, ExecutionRelationId, RoleId, TaskId};

/// One logical Task/Role execution slot independent of Node placement and physical attempts.
#[derive(
    Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, serde::Serialize, serde::Deserialize,
)]
pub struct PlannedExecutionRef {
    /// Task containing the logical role execution.
    task_id: TaskId,
    /// Role whose current attempt occupies the logical slot.
    role_id: RoleId,
}

impl PlannedExecutionRef {
    /// Creates one logical endpoint without selecting a Node or execution attempt.
    pub const fn new(task_id: TaskId, role_id: RoleId) -> Self {
        Self { task_id, role_id }
    }

    /// Returns the Task containing this logical execution slot.
    pub const fn task_id(&self) -> &TaskId {
        &self.task_id
    }

    /// Returns the Role occupying this logical execution slot.
    pub const fn role_id(&self) -> &RoleId {
        &self.role_id
    }
}

/// Versioned relation semantics understood by Runtime.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum ExecutionRelationKind {
    /// The source must remain Accepted/Running whenever the target is Accepted/Running.
    RequiresActive,
}

/// Mission-owned specification for one directional execution-time constraint.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct ExecutionRelationSpec {
    /// Stable relation identity within one Mission.
    relation_id: ExecutionRelationId,
    /// Logical execution whose active state supplies the condition.
    source: PlannedExecutionRef,
    /// Logical execution constrained by the source condition.
    target: PlannedExecutionRef,
    /// Closed v0.1 relation behavior.
    kind: ExecutionRelationKind,
}

impl ExecutionRelationSpec {
    /// Creates a directional relation and rejects a self-referential endpoint pair.
    pub fn new(
        relation_id: ExecutionRelationId,
        source: PlannedExecutionRef,
        target: PlannedExecutionRef,
        kind: ExecutionRelationKind,
    ) -> Result<Self, DomainError> {
        if source == target {
            return Err(DomainError::InvalidMissionPlan {
                reason: format!("execution relation {relation_id} references itself"),
            });
        }
        Ok(Self {
            relation_id,
            source,
            target,
            kind,
        })
    }

    /// Returns the stable Mission-scoped relation identity.
    pub const fn relation_id(&self) -> &ExecutionRelationId {
        &self.relation_id
    }

    /// Returns the logical condition-provider endpoint.
    pub const fn source(&self) -> &PlannedExecutionRef {
        &self.source
    }

    /// Returns the logical constrained endpoint.
    pub const fn target(&self) -> &PlannedExecutionRef {
        &self.target
    }

    /// Returns the closed relation behavior.
    pub const fn kind(&self) -> ExecutionRelationKind {
        self.kind
    }
}

/// Runtime-derived state of one accepted execution relation.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum ExecutionRelationState {
    /// The target is not currently active, so no live constraint window exists.
    Dormant,
    /// The target is active while the source is dispatched but not yet proven active.
    Pending,
    /// Both current endpoints are Accepted or Running.
    Satisfied,
    /// The target is active while the source is terminal.
    Violated,
    /// The target is active while the source physical state is ambiguous.
    Unknown,
}

impl ExecutionRelationState {
    /// Returns whether this state requires explicit reconciliation before target success.
    pub const fn requires_reconciliation(self) -> bool {
        matches!(self, Self::Violated | Self::Unknown)
    }
}
