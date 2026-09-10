//! Transport-neutral live execution contexts for committed distributed work.

use crate::coordination::{
    CoordinationKey, RuntimeCoordinationContext, RuntimePeerChannel, restore_coordination_maps,
};
use crate::relation::{
    RelationFenceCheckpoint, RelationKey, RelationProofCheckpoint, RelationStateCheckpoint,
    RuntimeExecutionRelation, SharedSpatialEvidence, restore_relation_maps,
};
use domain::{ExecutionCommand, ExecutionGroupId, NodeId, ResourceId, RoleId, TaskRef};
use std::collections::{BTreeMap, BTreeSet};
use std::fmt::{Display, Formatter};

mod checkpoint;
mod dispatch;
mod observation;

use observation::{normalized_resources, validate_checkpoint};

/// Logical execution slot that may be occupied by multiple physical attempts over time.
pub type ExecutionSlot = (ExecutionGroupId, TaskRef, RoleId);

/// Durable state for one Controller-to-Node dispatch intent.
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct DispatchIntent {
    /// Stable physical attempt identity carried by the Node Protocol execution field.
    pub execution_id: String,
    /// Immutable command prepared for delivery.
    pub command: ExecutionCommand,
    /// Stable sorted resources covered by the commitment.
    pub resource_ids: Vec<ResourceId>,
    /// Number of delivery attempts made by the Controller process.
    pub delivery_attempts: u32,
    /// Whether Node journal acceptance has been proven by a receipt or execution fact.
    pub delivered: bool,
}

impl DispatchIntent {
    /// Returns the deterministic command identity for this immutable Execute intent.
    pub fn command_id(&self) -> String {
        format!("dispatch-{}", self.execution_id)
    }
}

/// Serializable generation allocated to one logical Group/Task/Role slot.
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
struct AttemptGenerationCheckpoint {
    /// Group owning the logical slot.
    group_id: ExecutionGroupId,
    /// Mission-scoped Task owning the logical slot.
    task_ref: TaskRef,
    /// Role occupying the logical slot.
    role_id: RoleId,
    /// Last allocated physical attempt generation.
    generation: u64,
}

/// Serializable dispatch intent retained in the Runtime checkpoint.
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
struct DispatchIntentCheckpoint {
    /// Stable physical attempt identity.
    execution_id: String,
    /// Immutable command prepared for delivery.
    command: ExecutionCommand,
    /// Committed resources covered by the intent.
    resource_ids: Vec<ResourceId>,
    /// Number of Router delivery attempts.
    delivery_attempts: u32,
    /// Whether Node journal acceptance was proven by a receipt or execution fact.
    delivered: bool,
}

/// Runtime lifecycle of one stable role execution identity.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum ExecutionStatus {
    /// The command was routed but no Node acceptance fact has arrived.
    Dispatched,
    /// The Node accepted the command.
    Accepted,
    /// The Node reports active local execution.
    Running,
    /// The Node completed the command.
    Completed,
    /// The Node failed the command.
    Failed,
    /// The Node cancelled the command.
    Cancelled,
    /// Runtime cannot safely determine the physical execution state.
    Unknown,
}

impl ExecutionStatus {
    /// Returns whether no later execution fact may change this status.
    pub const fn is_terminal(self) -> bool {
        matches!(self, Self::Completed | Self::Failed | Self::Cancelled)
    }

    /// Returns whether this fact proves that local execution was entered.
    const fn proves_activation(self) -> bool {
        !matches!(self, Self::Dispatched | Self::Unknown)
    }
}

/// Terminal local-execution result reduced from every currently bound Role.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ObservedTaskExecutionResult {
    /// Every supplied current Role execution reached a successful terminal state.
    ExecutionCompleted,
    /// At least one supplied current role execution failed, cancelled, or became unknown.
    Failed,
}

/// Canonical Runtime event produced after reducing one Node execution fact.
#[derive(Debug, Clone, PartialEq)]
pub enum ExecutionEvent {
    /// The first authoritative Node fact activated a committed Task or its rebound attempt.
    TaskActivated {
        /// Mission-level Group containing the Task.
        group_id: ExecutionGroupId,
        /// Mission-scoped Task that entered execution.
        task_ref: TaskRef,
    },
    /// One role execution completed successfully.
    RoleCompleted {
        /// Committed command associated with the role execution.
        command: ExecutionCommand,
    },
    /// One role execution reached a failed or cancelled terminal state.
    RoleFailed {
        /// Committed command associated with the role execution.
        command: ExecutionCommand,
        /// Node-provided diagnostic detail.
        reason: String,
    },
    /// Runtime cannot safely continue this execution without Control reconciliation.
    RecoveryRequired {
        /// Stable execution identity requiring reconciliation.
        execution_id: String,
        /// Node that reported or owns the ambiguous execution.
        node_id: NodeId,
        /// Committed execution context when Runtime previously dispatched the command.
        context: Option<ExecutionCommand>,
        /// Runtime continuity failure.
        reason: String,
    },
    /// A Mission-owned execution coordination relation entered the live Runtime registry.
    RelationRegistered {
        /// Relation with Mission and Group identity applied to its logical endpoints.
        relation: RuntimeExecutionRelation,
    },
    /// Current endpoint execution facts changed a live relation state.
    RelationStateChanged {
        /// Relation whose state changed.
        relation: RuntimeExecutionRelation,
        /// Previous Runtime-derived state.
        previous: domain::ExecutionRelationState,
        /// New Runtime-derived state.
        current: domain::ExecutionRelationState,
        /// Current source attempt, when dispatched.
        source_execution_id: Option<String>,
        /// Current target attempt, when dispatched.
        target_execution_id: Option<String>,
    },
    /// A relation violation or ambiguity fenced target progression for reconciliation.
    RelationReconciliationRequired {
        /// Relation requiring coordination policy.
        relation: RuntimeExecutionRelation,
        /// Violated or unknown live state.
        state: domain::ExecutionRelationState,
        /// Current source attempt, when dispatched.
        source_execution_id: Option<String>,
        /// Current target attempt, when dispatched.
        target_execution_id: Option<String>,
        /// Stable Runtime diagnostic.
        reason: String,
    },
}

/// One Runtime-owned live context for a Control-committed role execution.
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct ExecutionContext {
    /// Canonical command containing Group, Task, role, Node, and intent identities.
    command: ExecutionCommand,
    /// Stable sorted resources covered by the Control commitment.
    resource_ids: Vec<ResourceId>,
}

/// Immutable audit view of one physical attempt and its latest reduced status.
#[derive(Debug, Clone, PartialEq)]
pub struct ExecutionAttemptSnapshot {
    /// Stable attempt identity used by command receipts and ordered facts.
    execution_id: String,
    /// Logical execution identity and physical Node selected for this attempt.
    command: ExecutionCommand,
    /// Latest Runtime-reduced lifecycle status.
    status: ExecutionStatus,
}

impl ExecutionAttemptSnapshot {
    /// Returns the physical attempt identity.
    pub fn execution_id(&self) -> &str {
        &self.execution_id
    }

    /// Returns the immutable logical context and physical owner.
    pub const fn command(&self) -> &ExecutionCommand {
        &self.command
    }

    /// Returns the latest reduced attempt status.
    pub const fn status(&self) -> ExecutionStatus {
        self.status
    }
}

impl ExecutionContext {
    /// Returns the canonical committed execution command.
    pub const fn command(&self) -> &ExecutionCommand {
        &self.command
    }

    /// Returns the committed resources in stable identity order.
    pub fn resource_ids(&self) -> &[ResourceId] {
        &self.resource_ids
    }
}

/// Serializable active Group/Task/Role to execution identity association.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
struct ActiveExecutionCheckpoint {
    /// Group owning the execution.
    group_id: ExecutionGroupId,
    /// Task owning the role execution.
    task_ref: TaskRef,
    /// Role owning the execution.
    role_id: RoleId,
    /// Stable execution identity currently associated with the role.
    execution_id: String,
}

/// Transport-neutral durable Runtime projection embedded in the controller checkpoint.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct RuntimeExecutionCheckpoint {
    /// Runtime contexts retained for reconciliation and terminal fact conversion.
    executions: BTreeMap<String, ExecutionContext>,
    /// Latest accepted status by execution identity.
    execution_status: BTreeMap<String, ExecutionStatus>,
    /// Latest accepted execution-local sequence by execution identity.
    execution_sequences: BTreeMap<String, u64>,
    /// Stable Node ownership observed for every execution identity.
    execution_nodes: BTreeMap<String, NodeId>,
    /// Current execution identity for every Group Task role.
    active_executions: Vec<ActiveExecutionCheckpoint>,
    /// Accepted relation specifications with resolved logical endpoint identity.
    relations: Vec<RuntimeExecutionRelation>,
    /// Latest reduced relation states encoded without composite JSON object keys.
    relation_states: Vec<RelationStateCheckpoint>,
    /// Latched relation reconciliation fences.
    relation_fences: Vec<RelationFenceCheckpoint>,
    /// Target attempts proven to have run under a satisfied relation.
    relation_proofs: Vec<RelationProofCheckpoint>,
    /// Mission-owned coordination Context declarations.
    #[serde(default)]
    coordination_contexts: Vec<RuntimeCoordinationContext>,
    /// Direct peer channel descriptors, lifecycle, and endpoint acknowledgements.
    #[serde(default)]
    peer_channels: Vec<RuntimePeerChannel>,
    /// Strong localization evidence retained for current logical attempts.
    #[serde(default)]
    spatial_evidence: Vec<SharedSpatialEvidence>,
    /// Last physical attempt generation allocated for each logical slot.
    #[serde(default)]
    attempt_generations: Vec<AttemptGenerationCheckpoint>,
    /// Durable Execute intents waiting for or retaining delivery evidence.
    #[serde(default)]
    dispatch_outbox: Vec<DispatchIntentCheckpoint>,
    /// Attempts whose cancellation must survive Controller restart and be retried until terminal.
    #[serde(default)]
    cancellation_intents: BTreeSet<String>,
}

/// Whether a validated dispatch must be sent through Integration.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DispatchDecision {
    /// The execution identity is new and must be routed exactly once.
    Route,
    /// The exact same execution context was already routed.
    AlreadyRouted,
}

/// Runtime-owned execution continuity failures.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ExecutionRuntimeError {
    /// One stable execution ID was reused for different immutable inputs.
    ExecutionConflict(String),
    /// Runtime fenced an execution until reconciliation explicitly resolves ambiguity.
    ReconciliationRequired(String),
    /// A Node fact came from a different Node than the stable execution owner.
    NodeOwnership(String),
    /// A terminal fact attempted to change an immutable terminal status.
    TerminalConflict(String),
    /// A checkpoint violated Runtime cross-map invariants.
    InvalidCheckpoint(String),
}

impl Display for ExecutionRuntimeError {
    /// Formats one stable execution continuity diagnostic.
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::ExecutionConflict(id) => {
                write!(
                    formatter,
                    "execution identity {id} was reused with different inputs"
                )
            }
            Self::ReconciliationRequired(reason)
            | Self::NodeOwnership(reason)
            | Self::TerminalConflict(reason)
            | Self::InvalidCheckpoint(reason) => formatter.write_str(reason),
        }
    }
}

impl std::error::Error for ExecutionRuntimeError {}

/// Live Runtime authority for stable distributed execution identities and facts.
#[derive(Debug, Default, Clone)]
pub struct RuntimeExecutionManager {
    /// Dispatched committed execution contexts.
    pub(crate) executions: BTreeMap<String, ExecutionContext>,
    /// Latest execution lifecycle facts.
    pub(crate) execution_status: BTreeMap<String, ExecutionStatus>,
    /// Last accepted execution-local sequence across sessions and snapshot replay.
    execution_sequences: BTreeMap<String, u64>,
    /// Node that first reported or received each stable execution identity.
    pub(crate) execution_nodes: BTreeMap<String, NodeId>,
    /// Current authoritative execution identity for each Group Task role.
    pub(crate) active_executions: BTreeMap<(ExecutionGroupId, TaskRef, RoleId), String>,
    /// Commands restored from durable state that must never be implicitly sent again.
    restored_executions: BTreeSet<String>,
    /// Tasks for which Runtime already emitted an activation transition.
    activated_tasks: BTreeSet<(ExecutionGroupId, TaskRef)>,
    /// Replacement attempts that must reactivate an Adapted Group after Node acceptance.
    reactivation_attempts: BTreeSet<String>,
    /// Mission-owned relation specifications resolved to Group/Task/Role logical slots.
    pub(crate) relations: BTreeMap<RelationKey, RuntimeExecutionRelation>,
    /// Current Runtime-derived state for every accepted relation.
    pub(crate) relation_states: BTreeMap<RelationKey, domain::ExecutionRelationState>,
    /// Violated or unknown relations that still fence target progression.
    pub(crate) relation_fences: BTreeSet<RelationKey>,
    /// Current target attempts observed at least once under a satisfied relation.
    pub(crate) relation_proofs: BTreeMap<RelationKey, String>,
    /// Mission-owned coordination mechanism declarations by Group and Context.
    pub(crate) coordination_contexts: BTreeMap<CoordinationKey, RuntimeCoordinationContext>,
    /// Direct Local EAIOS peer channel lifecycle without owning transport traffic.
    pub(crate) peer_channels: BTreeMap<CoordinationKey, RuntimePeerChannel>,
    /// Strong map/frame evidence by current Group Task role slot.
    pub(crate) spatial_evidence:
        BTreeMap<(ExecutionGroupId, TaskRef, RoleId), SharedSpatialEvidence>,
    /// Last physical attempt generation allocated for each logical slot.
    attempt_generations: BTreeMap<ExecutionSlot, u64>,
    /// Durable Controller-to-Node Execute intents.
    dispatch_outbox: BTreeMap<String, DispatchIntent>,
    /// Durable cancellation requests, retaining physical ambiguity until terminal evidence.
    cancellation_intents: BTreeSet<String>,
}

#[cfg(test)]
#[path = "tests.rs"]
mod tests;
