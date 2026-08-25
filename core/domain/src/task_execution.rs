//! Task execution units hosted by a Mission-level Execution Group.

use crate::{CoordinationContextId, RoleAssignment, TaskRef};

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

    /// Returns a copy with a new lifecycle, preserving identity and bindings.
    pub fn with_lifecycle(&self, lifecycle: TaskExecutionLifecycle) -> Self {
        Self {
            task_ref: self.task_ref.clone(),
            context_id: self.context_id.clone(),
            lifecycle,
            assignments: self.assignments.clone(),
        }
    }
}
