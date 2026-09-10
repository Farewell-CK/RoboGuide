//! Runtime-owned live state for Mission execution coordination relations.

use crate::{ExecutionEvent, ExecutionStatus, RuntimeExecutionManager};
use domain::{
    ExecutionCouplingMode, ExecutionGroupId, ExecutionRelationId, ExecutionRelationKind,
    ExecutionRelationSpec, ExecutionRelationState, ExecutionRelationType,
    LocalizationVerificationEvidence, MapRevisionSelector, MissionId, NodeId, RoleId, TaskRef,
    TimestampMs,
};
use std::collections::{BTreeMap, BTreeSet};

mod manager;

pub(crate) use manager::restore_relation_maps;

/// Stable Runtime key for one Mission relation inside its Execution Group.
pub(crate) type RelationKey = (ExecutionGroupId, ExecutionRelationId);

/// Strong map/frame evidence attached to one current logical execution attempt.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct SharedSpatialEvidence {
    /// Mission-level Group containing the execution.
    pub(crate) group_id: ExecutionGroupId,
    /// Mission-scoped Task represented by the execution.
    pub(crate) task_ref: TaskRef,
    /// Task-local Role represented by the execution.
    pub(crate) role_id: RoleId,
    /// Current execution attempt identity that produced the evidence.
    pub(crate) execution_id: String,
    /// Node that produced the evidence; placement is evidence, not relation identity.
    pub(crate) node_id: NodeId,
    /// Strongly verified immutable map revision.
    pub(crate) selector: MapRevisionSelector,
    /// Common frame observed by the Local EAIOS.
    pub(crate) frame_id: String,
    /// RoboGuide-local receive time for evidence inspection.
    pub(crate) received_at: TimestampMs,
}

impl SharedSpatialEvidence {
    /// Converts an existing strong localization evidence record into Runtime evidence.
    pub fn from_localization(
        evidence: &LocalizationVerificationEvidence,
        received_at: TimestampMs,
    ) -> Self {
        Self {
            group_id: evidence.group_id().clone(),
            task_ref: evidence.task_ref().clone(),
            role_id: evidence.role_id().clone(),
            execution_id: evidence.execution_id().to_string(),
            node_id: evidence.node_id().clone(),
            selector: evidence.artifact().selector().clone(),
            frame_id: evidence.frames().map().to_string(),
            received_at,
        }
    }

    /// Returns the logical Group/Task/Role slot represented by this evidence.
    pub(crate) fn slot(&self) -> (ExecutionGroupId, TaskRef, RoleId) {
        (
            self.group_id.clone(),
            self.task_ref.clone(),
            self.role_id.clone(),
        )
    }

    /// Returns the Mission-level Group containing the logical execution slot.
    pub const fn group_id(&self) -> &ExecutionGroupId {
        &self.group_id
    }

    /// Returns the Mission-scoped Task containing the logical execution slot.
    pub const fn task_ref(&self) -> &TaskRef {
        &self.task_ref
    }

    /// Returns the Task-local Role containing the logical execution slot.
    pub const fn role_id(&self) -> &RoleId {
        &self.role_id
    }

    /// Returns the current execution attempt identity that produced this evidence.
    pub fn execution_id(&self) -> &str {
        &self.execution_id
    }

    /// Returns the physical Node that produced this evidence.
    pub const fn node_id(&self) -> &NodeId {
        &self.node_id
    }

    /// Returns the strongly verified map revision.
    pub const fn selector(&self) -> &MapRevisionSelector {
        &self.selector
    }

    /// Returns the map frame observed by the Local EAIOS.
    pub fn frame_id(&self) -> &str {
        &self.frame_id
    }

    /// Returns the RoboGuide-local evidence receive time.
    pub const fn received_at(&self) -> TimestampMs {
        self.received_at
    }
}

/// Accepted relation with Mission and Group identity applied to both logical endpoints.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct RuntimeExecutionRelation {
    /// Mission-level Group in which both endpoint executions run.
    pub(crate) group_id: ExecutionGroupId,
    /// Stable relation identity from the accepted MissionPlan.
    pub(crate) relation_id: ExecutionRelationId,
    /// Source Task logical identity.
    pub(crate) source_task_ref: TaskRef,
    /// Source Role logical identity.
    pub(crate) source_role_id: RoleId,
    /// Target Task logical identity.
    pub(crate) target_task_ref: TaskRef,
    /// Target Role logical identity.
    pub(crate) target_role_id: RoleId,
    /// Closed v0.1 relation behavior.
    pub(crate) kind: ExecutionRelationKind,
    /// Typed relation descriptor retained across restart and rebind.
    #[serde(default)]
    pub(crate) relation_type: ExecutionRelationType,
    /// Effective coupling mode of the constrained Task execution.
    #[serde(default)]
    pub(crate) coupling_mode: ExecutionCouplingMode,
}

impl RuntimeExecutionRelation {
    /// Returns the Group containing the relation endpoints.
    pub const fn group_id(&self) -> &ExecutionGroupId {
        &self.group_id
    }

    /// Returns the Mission-owned relation identity.
    pub const fn relation_id(&self) -> &ExecutionRelationId {
        &self.relation_id
    }

    /// Returns the logical source Task.
    pub const fn source_task_ref(&self) -> &TaskRef {
        &self.source_task_ref
    }

    /// Returns the logical source Role.
    pub const fn source_role_id(&self) -> &RoleId {
        &self.source_role_id
    }

    /// Returns the logical constrained Task.
    pub const fn target_task_ref(&self) -> &TaskRef {
        &self.target_task_ref
    }

    /// Returns the logical constrained Role.
    pub const fn target_role_id(&self) -> &RoleId {
        &self.target_role_id
    }

    /// Returns the closed relation behavior.
    pub const fn kind(&self) -> ExecutionRelationKind {
        self.kind
    }

    /// Returns the typed relation descriptor.
    pub const fn relation_type(&self) -> &ExecutionRelationType {
        &self.relation_type
    }

    /// Returns the constrained Task execution's effective coupling mode.
    pub const fn coupling_mode(&self) -> ExecutionCouplingMode {
        self.coupling_mode
    }

    /// Returns the Runtime map key for this relation.
    pub(crate) fn key(&self) -> RelationKey {
        (self.group_id.clone(), self.relation_id.clone())
    }

    /// Returns the source logical execution slot.
    pub(crate) fn source_key(&self) -> (ExecutionGroupId, TaskRef, RoleId) {
        (
            self.group_id.clone(),
            self.source_task_ref.clone(),
            self.source_role_id.clone(),
        )
    }

    /// Returns the target logical execution slot.
    pub(crate) fn target_key(&self) -> (ExecutionGroupId, TaskRef, RoleId) {
        (
            self.group_id.clone(),
            self.target_task_ref.clone(),
            self.target_role_id.clone(),
        )
    }
}

/// JSON-safe checkpoint entry for one relation state.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub(crate) struct RelationStateCheckpoint {
    /// Group portion of the relation key.
    pub(crate) group_id: ExecutionGroupId,
    /// Relation identity portion of the key.
    pub(crate) relation_id: ExecutionRelationId,
    /// Last reduced live state.
    pub(crate) state: ExecutionRelationState,
}

/// JSON-safe checkpoint entry for one latched reconciliation fence.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub(crate) struct RelationFenceCheckpoint {
    /// Group portion of the relation key.
    pub(crate) group_id: ExecutionGroupId,
    /// Relation identity portion of the key.
    pub(crate) relation_id: ExecutionRelationId,
}

/// JSON-safe proof that one target attempt was observed under a satisfied relation.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub(crate) struct RelationProofCheckpoint {
    /// Group portion of the relation key.
    pub(crate) group_id: ExecutionGroupId,
    /// Relation identity portion of the key.
    pub(crate) relation_id: ExecutionRelationId,
    /// Target attempt observed while this relation was satisfied.
    pub(crate) target_execution_id: String,
}

/// Observable Runtime snapshot for one execution coordination relation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RuntimeRelationSnapshot {
    /// Accepted relation specification with resolved Mission/Group identity.
    relation: RuntimeExecutionRelation,
    /// Current Runtime-derived relation state.
    state: ExecutionRelationState,
    /// Whether a previous violation or unknown state still fences target progression.
    reconciliation_required: bool,
    /// Current source execution attempt, when dispatched.
    source_execution_id: Option<String>,
    /// Current target execution attempt, when dispatched.
    target_execution_id: Option<String>,
}

impl RuntimeRelationSnapshot {
    /// Returns the accepted relation specification.
    pub const fn relation(&self) -> &RuntimeExecutionRelation {
        &self.relation
    }

    /// Returns the current relation state.
    pub const fn state(&self) -> ExecutionRelationState {
        self.state
    }

    /// Returns whether target progression remains fenced for reconciliation.
    pub const fn reconciliation_required(&self) -> bool {
        self.reconciliation_required
    }

    /// Returns the current source attempt identity, when available.
    pub fn source_execution_id(&self) -> Option<&str> {
        self.source_execution_id.as_deref()
    }

    /// Returns the current target attempt identity, when available.
    pub fn target_execution_id(&self) -> Option<&str> {
        self.target_execution_id.as_deref()
    }
}

#[cfg(test)]
#[path = "tests.rs"]
mod tests;
