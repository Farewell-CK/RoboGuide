//! Versioned execution request and observation response DTOs.

use super::WireExecutionIntent;
use crate::http::HttpAdapterError;
use domain::{
    ExecutionCommand, ExecutionGroupId, MissionId, NODE_CONTRACT_VERSION_V0_1, NodeEvent, NodeId,
    RoleId, TaskId, TaskRef,
};
use serde::{Deserialize, Serialize};

/// HTTP representation of a mission-scoped task identity.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct WireTaskRef {
    /// Mission owning the task namespace.
    mission_id: String,
    /// Task identity scoped by the mission.
    task_id: String,
}

/// Versioned HTTP invocation request.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct WireExecutionRequest {
    /// Semantic Node Contract version.
    schema_version: String,
    /// Mission-scoped task identity.
    task_ref: WireTaskRef,
    /// Existing execution group identity.
    group_id: String,
    /// Role being invoked.
    role_id: String,
    /// Target node identity.
    node_id: String,
    /// End-to-end correlation identity.
    correlation_id: String,
    /// Canonical operation and transport-neutral scalar parameters.
    intent: WireExecutionIntent,
}

impl WireExecutionRequest {
    /// Copies a transport-neutral command into the versioned HTTP request shape.
    pub(crate) fn from_command(command: &ExecutionCommand) -> Self {
        Self {
            schema_version: NODE_CONTRACT_VERSION_V0_1.to_string(),
            task_ref: WireTaskRef {
                mission_id: command.mission_id().as_str().to_string(),
                task_id: command.task_id().as_str().to_string(),
            },
            group_id: command.group_id().as_str().to_string(),
            role_id: command.role_id().as_str().to_string(),
            node_id: command.node_id().as_str().to_string(),
            correlation_id: command.correlation_id().as_str().to_string(),
            intent: command.intent().into(),
        }
    }
}

/// Versioned HTTP execution result envelope.
#[derive(Debug, Serialize, Deserialize)]
pub(crate) struct WireExecutionResponse {
    /// Semantic Node Contract version.
    schema_version: String,
    /// Local execution result.
    #[serde(flatten)]
    event: WireNodeEvent,
}

/// Tagged HTTP representation of currently supported synchronous node observations.
#[derive(Debug, Serialize, Deserialize)]
#[serde(tag = "event", rename_all = "snake_case", deny_unknown_fields)]
enum WireNodeEvent {
    /// Local EAIOS completed the assigned operation.
    TaskCompleted {
        /// Reporting node identity.
        node_id: String,
        /// Mission-scoped task identity.
        task_ref: WireTaskRef,
        /// Execution group identity.
        group_id: String,
        /// Completed role identity.
        role_id: String,
    },
    /// Local EAIOS failed or rejected the assigned operation.
    TaskFailed {
        /// Reporting node identity.
        node_id: String,
        /// Mission-scoped task identity.
        task_ref: WireTaskRef,
        /// Execution group identity.
        group_id: String,
        /// Failed role identity.
        role_id: String,
        /// Stable local diagnostic reason.
        reason: String,
    },
    /// Local safety stopped the node independently of global Control.
    SafeStopped {
        /// Reporting node identity.
        node_id: String,
        /// Stable local safety reason.
        reason: String,
    },
}

impl WireExecutionResponse {
    /// Converts a wire observation and rejects identity drift from the invoked command.
    pub(crate) fn into_domain(
        self,
        command: &ExecutionCommand,
    ) -> Result<NodeEvent, HttpAdapterError> {
        if self.schema_version != NODE_CONTRACT_VERSION_V0_1 {
            return Err(HttpAdapterError::protocol(format!(
                "unsupported node contract {}",
                self.schema_version
            )));
        }
        match self.event {
            WireNodeEvent::TaskCompleted {
                node_id,
                task_ref,
                group_id,
                role_id,
            } => {
                let (node_id, task_ref, group_id, role_id) =
                    validate_context(node_id, task_ref, group_id, role_id, command)?;
                Ok(NodeEvent::TaskCompleted {
                    node_id,
                    task_ref,
                    group_id,
                    role_id,
                })
            }
            WireNodeEvent::TaskFailed {
                node_id,
                task_ref,
                group_id,
                role_id,
                reason,
            } => {
                let (node_id, task_ref, group_id, role_id) =
                    validate_context(node_id, task_ref, group_id, role_id, command)?;
                Ok(NodeEvent::TaskFailed {
                    node_id,
                    task_ref,
                    group_id,
                    role_id,
                    reason,
                })
            }
            WireNodeEvent::SafeStopped { node_id, reason } => {
                let node_id = node_id_from_wire(node_id)?;
                if &node_id != command.node_id() {
                    return Err(HttpAdapterError::protocol(format!(
                        "event node {node_id} does not match command node {}",
                        command.node_id()
                    )));
                }
                Ok(NodeEvent::SafeStopped { node_id, reason })
            }
        }
    }
}

/// Validates all role-scoped response identities against the invoked command.
fn validate_context(
    node_id: String,
    task_ref: WireTaskRef,
    group_id: String,
    role_id: String,
    command: &ExecutionCommand,
) -> Result<(NodeId, TaskRef, ExecutionGroupId, RoleId), HttpAdapterError> {
    let node_id = node_id_from_wire(node_id)?;
    let task_ref = TaskRef::new(
        MissionId::new(task_ref.mission_id)
            .map_err(|error| HttpAdapterError::protocol(error.to_string()))?,
        TaskId::new(task_ref.task_id)
            .map_err(|error| HttpAdapterError::protocol(error.to_string()))?,
    );
    let group_id = ExecutionGroupId::new(group_id)
        .map_err(|error| HttpAdapterError::protocol(error.to_string()))?;
    let role_id =
        RoleId::new(role_id).map_err(|error| HttpAdapterError::protocol(error.to_string()))?;
    if &node_id != command.node_id()
        || &task_ref != command.task_ref()
        || &group_id != command.group_id()
        || &role_id != command.role_id()
    {
        return Err(HttpAdapterError::protocol(
            "execution event identity does not match command context",
        ));
    }
    Ok((node_id, task_ref, group_id, role_id))
}

/// Parses one wire node identity with adapter-local diagnostics.
fn node_id_from_wire(value: String) -> Result<NodeId, HttpAdapterError> {
    NodeId::new(value).map_err(|error| HttpAdapterError::protocol(error.to_string()))
}
