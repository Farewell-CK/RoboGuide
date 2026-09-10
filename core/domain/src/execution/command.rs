//! Canonical role-scoped command routed to one selected node.

use crate::{CorrelationId, ExecutionGroupId, MissionId, NodeId, RoleId, TaskId, TaskRef};

use super::ExecutionIntent;

/// A command sent through the runtime to a local node.
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct ExecutionCommand {
    /// Mission-scoped task whose role is being invoked.
    task_ref: TaskRef,
    /// Execution group that owns the role lifecycle.
    group_id: ExecutionGroupId,
    /// Role being invoked on the node.
    role_id: RoleId,
    /// Node that receives the command.
    node_id: NodeId,
    /// Canonical operation and parameters requested from the local EAIOS.
    intent: ExecutionIntent,
    /// Correlation identity for the command and its observations.
    correlation_id: CorrelationId,
}

impl ExecutionCommand {
    /// Creates a role-scoped command without exposing transport details.
    pub const fn new(
        mission_id: MissionId,
        task_id: TaskId,
        group_id: ExecutionGroupId,
        role_id: RoleId,
        node_id: NodeId,
        intent: ExecutionIntent,
        correlation_id: CorrelationId,
    ) -> Self {
        Self {
            task_ref: TaskRef::new(mission_id, task_id),
            group_id,
            role_id,
            node_id,
            intent,
            correlation_id,
        }
    }

    /// Returns the complete mission-scoped task identity.
    pub const fn task_ref(&self) -> &TaskRef {
        &self.task_ref
    }

    /// Returns the mission targeted by this command.
    pub const fn mission_id(&self) -> &MissionId {
        self.task_ref.mission_id()
    }

    /// Returns the task targeted by this command.
    pub const fn task_id(&self) -> &TaskId {
        self.task_ref.task_id()
    }

    /// Returns the execution group targeted by this command.
    pub fn group_id(&self) -> &ExecutionGroupId {
        &self.group_id
    }

    /// Returns the role targeted by this command.
    pub fn role_id(&self) -> &RoleId {
        &self.role_id
    }

    /// Returns the node receiving this command.
    pub fn node_id(&self) -> &NodeId {
        &self.node_id
    }

    /// Returns the canonical capability contract request for the local EAIOS adapter.
    pub const fn intent(&self) -> &ExecutionIntent {
        &self.intent
    }

    /// Returns the operation correlation identity.
    pub fn correlation_id(&self) -> &CorrelationId {
        &self.correlation_id
    }
}
