//! Terminal observations returned by a local execution node.

use crate::{ExecutionGroupId, NodeId, RoleId, TaskRef};

/// The result reported by a local node after receiving an execution command.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum NodeEvent {
    /// The local node completed the assigned role.
    TaskCompleted {
        /// Node that executed the role.
        node_id: NodeId,
        /// Mission-scoped task that was executed.
        task_ref: TaskRef,
        /// Execution group containing the role.
        group_id: ExecutionGroupId,
        /// Role that completed.
        role_id: RoleId,
    },
    /// The local node rejected or failed the assigned role.
    TaskFailed {
        /// Node that attempted the role.
        node_id: NodeId,
        /// Mission-scoped task that failed.
        task_ref: TaskRef,
        /// Execution group containing the role.
        group_id: ExecutionGroupId,
        /// Role that failed.
        role_id: RoleId,
        /// Stable human-readable failure reason.
        reason: String,
    },
    /// The local node entered a safety stop.
    SafeStopped {
        /// Node that stopped.
        node_id: NodeId,
        /// Reason reported by local safety.
        reason: String,
    },
}
