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

/// Terminal Task result reduced from the current execution of every bound role.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ObservedTaskResult {
    /// Every supplied current role execution completed successfully.
    Succeeded,
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

impl RuntimeExecutionManager {
    /// Creates an empty live execution authority.
    pub const fn new() -> Self {
        Self {
            executions: BTreeMap::new(),
            execution_status: BTreeMap::new(),
            execution_sequences: BTreeMap::new(),
            execution_nodes: BTreeMap::new(),
            active_executions: BTreeMap::new(),
            restored_executions: BTreeSet::new(),
            activated_tasks: BTreeSet::new(),
            reactivation_attempts: BTreeSet::new(),
            relations: BTreeMap::new(),
            relation_states: BTreeMap::new(),
            relation_fences: BTreeSet::new(),
            relation_proofs: BTreeMap::new(),
            coordination_contexts: BTreeMap::new(),
            peer_channels: BTreeMap::new(),
            spatial_evidence: BTreeMap::new(),
            attempt_generations: BTreeMap::new(),
            dispatch_outbox: BTreeMap::new(),
            cancellation_intents: BTreeSet::new(),
        }
    }

    /// Returns a durable transport-neutral Runtime projection.
    pub fn checkpoint(&self) -> RuntimeExecutionCheckpoint {
        RuntimeExecutionCheckpoint {
            cancellation_intents: self.cancellation_intents.clone(),
            executions: self.executions.clone(),
            execution_status: self.execution_status.clone(),
            execution_sequences: self.execution_sequences.clone(),
            execution_nodes: self.execution_nodes.clone(),
            active_executions: self
                .active_executions
                .iter()
                .map(
                    |((group_id, task_ref, role_id), execution_id)| ActiveExecutionCheckpoint {
                        group_id: group_id.clone(),
                        task_ref: task_ref.clone(),
                        role_id: role_id.clone(),
                        execution_id: execution_id.clone(),
                    },
                )
                .collect(),
            relations: self.relations.values().cloned().collect(),
            relation_states: self
                .relation_states
                .iter()
                .map(|((group_id, relation_id), state)| RelationStateCheckpoint {
                    group_id: group_id.clone(),
                    relation_id: relation_id.clone(),
                    state: *state,
                })
                .collect(),
            relation_fences: self
                .relation_fences
                .iter()
                .map(|(group_id, relation_id)| RelationFenceCheckpoint {
                    group_id: group_id.clone(),
                    relation_id: relation_id.clone(),
                })
                .collect(),
            relation_proofs: self
                .relation_proofs
                .iter()
                .map(
                    |((group_id, relation_id), target_execution_id)| RelationProofCheckpoint {
                        group_id: group_id.clone(),
                        relation_id: relation_id.clone(),
                        target_execution_id: target_execution_id.clone(),
                    },
                )
                .collect(),
            coordination_contexts: self.coordination_contexts.values().cloned().collect(),
            peer_channels: self.peer_channels.values().cloned().collect(),
            spatial_evidence: self.spatial_evidence.values().cloned().collect(),
            attempt_generations: self
                .attempt_generations
                .iter()
                .map(
                    |((group_id, task_ref, role_id), generation)| AttemptGenerationCheckpoint {
                        group_id: group_id.clone(),
                        task_ref: task_ref.clone(),
                        role_id: role_id.clone(),
                        generation: *generation,
                    },
                )
                .collect(),
            dispatch_outbox: self
                .dispatch_outbox
                .values()
                .map(|intent| DispatchIntentCheckpoint {
                    execution_id: intent.execution_id.clone(),
                    command: intent.command.clone(),
                    resource_ids: intent.resource_ids.clone(),
                    delivery_attempts: intent.delivery_attempts,
                    delivered: intent.delivered,
                })
                .collect(),
        }
    }

    /// Restores a checkpoint conservatively without granting replay authority.
    pub fn restore(checkpoint: RuntimeExecutionCheckpoint) -> Result<Self, ExecutionRuntimeError> {
        validate_checkpoint(&checkpoint)?;
        let (relations, relation_states, relation_fences, relation_proofs) = restore_relation_maps(
            checkpoint.relations.clone(),
            checkpoint.relation_states.clone(),
            checkpoint.relation_fences.clone(),
            checkpoint.relation_proofs.clone(),
        )?;
        let (coordination_contexts, mut peer_channels) = restore_coordination_maps(
            checkpoint.coordination_contexts.clone(),
            checkpoint.peer_channels.clone(),
        )?;
        let mut spatial_evidence = BTreeMap::new();
        for evidence in checkpoint.spatial_evidence {
            if evidence.execution_id.trim().is_empty() || evidence.frame_id.trim().is_empty() {
                return Err(ExecutionRuntimeError::InvalidCheckpoint(
                    "checkpoint contains invalid shared spatial evidence".to_string(),
                ));
            }
            if spatial_evidence.insert(evidence.slot(), evidence).is_some() {
                return Err(ExecutionRuntimeError::InvalidCheckpoint(
                    "checkpoint contains duplicate shared spatial evidence".to_string(),
                ));
            }
        }
        for channel in peer_channels.values_mut() {
            if channel.lifecycle() == crate::PeerChannelLifecycle::Ready {
                channel.lifecycle = crate::PeerChannelLifecycle::Fenced;
            }
            channel.readiness.clear();
        }
        let restored_executions = checkpoint.executions.keys().cloned().collect();
        let activated_tasks = checkpoint
            .active_executions
            .iter()
            .filter(|active| {
                checkpoint
                    .execution_status
                    .get(&active.execution_id)
                    .is_some_and(|status| status.proves_activation())
            })
            .map(|active| (active.group_id.clone(), active.task_ref.clone()))
            .collect();
        let mut active_executions = BTreeMap::new();
        for active in checkpoint.active_executions {
            let key = (active.group_id, active.task_ref, active.role_id);
            if active_executions.insert(key, active.execution_id).is_some() {
                return Err(ExecutionRuntimeError::InvalidCheckpoint(
                    "checkpoint contains duplicate active Group Task role".to_string(),
                ));
            }
        }
        let execution_status = checkpoint
            .execution_status
            .into_iter()
            .map(|(execution_id, status)| {
                let restored_status = if status.is_terminal() {
                    status
                } else {
                    ExecutionStatus::Unknown
                };
                (execution_id, restored_status)
            })
            .collect();
        for (slot, evidence) in &spatial_evidence {
            if active_executions.get(slot) != Some(&evidence.execution_id)
                || checkpoint.execution_nodes.get(&evidence.execution_id) != Some(&evidence.node_id)
            {
                return Err(ExecutionRuntimeError::InvalidCheckpoint(
                    "checkpoint shared spatial evidence is not owned by the current attempt"
                        .to_string(),
                ));
            }
        }
        let mut restored = Self {
            cancellation_intents: checkpoint.cancellation_intents,
            executions: checkpoint.executions,
            execution_status,
            execution_sequences: checkpoint.execution_sequences,
            execution_nodes: checkpoint.execution_nodes,
            active_executions,
            restored_executions,
            activated_tasks,
            reactivation_attempts: BTreeSet::new(),
            relations,
            relation_states,
            relation_fences,
            relation_proofs,
            coordination_contexts,
            peer_channels,
            spatial_evidence,
            attempt_generations: checkpoint
                .attempt_generations
                .into_iter()
                .map(|entry| {
                    (
                        (entry.group_id, entry.task_ref, entry.role_id),
                        entry.generation,
                    )
                })
                .collect(),
            dispatch_outbox: checkpoint
                .dispatch_outbox
                .into_iter()
                .map(|intent| {
                    (
                        intent.execution_id.clone(),
                        DispatchIntent {
                            execution_id: intent.execution_id,
                            command: intent.command,
                            resource_ids: intent.resource_ids,
                            delivery_attempts: intent.delivery_attempts,
                            delivered: intent.delivered,
                        },
                    )
                })
                .collect(),
        };
        for active in restored.active_executions.keys() {
            restored
                .attempt_generations
                .entry(active.clone())
                .or_insert(1);
        }
        restored.refresh_all_relations_after_restore();
        Ok(restored)
    }

    /// Validates whether one committed execution needs a new Integration route.
    pub fn validate_dispatch(
        &self,
        execution_id: &str,
        command: &ExecutionCommand,
        resource_ids: &[ResourceId],
    ) -> Result<DispatchDecision, ExecutionRuntimeError> {
        if self.restored_executions.contains(execution_id) {
            return Err(ExecutionRuntimeError::ReconciliationRequired(
                "execution was restored after controller restart; reconciliation is required before routing"
                    .to_string(),
            ));
        }
        if self.execution_status.contains_key(execution_id)
            && !self.executions.contains_key(execution_id)
        {
            return Err(ExecutionRuntimeError::ReconciliationRequired(
                "execution was observed during reconnect; reconciliation is required before routing"
                    .to_string(),
            ));
        }
        let resources = normalized_resources(resource_ids);
        if let Some(existing) = self.executions.get(execution_id) {
            if existing.command != *command || existing.resource_ids != resources {
                return Err(ExecutionRuntimeError::ExecutionConflict(
                    execution_id.to_string(),
                ));
            }
            return Ok(DispatchDecision::AlreadyRouted);
        }
        Ok(DispatchDecision::Route)
    }

    /// Allocates an unused physical attempt identity, rejecting exhausted generation counters.
    pub fn allocate_attempt_id(
        &mut self,
        group_id: &ExecutionGroupId,
        task_ref: &TaskRef,
        role_id: &RoleId,
    ) -> Result<String, ExecutionRuntimeError> {
        let slot = (group_id.clone(), task_ref.clone(), role_id.clone());
        let mut generation = self.attempt_generations.get(&slot).copied().unwrap_or(0);
        loop {
            generation = generation.checked_add(1).ok_or_else(|| {
                ExecutionRuntimeError::ReconciliationRequired(
                    "physical attempt generation is exhausted".to_string(),
                )
            })?;
            let execution_id = format!(
                "attempt-{}:{}-{}:{}-{}:{}-{}",
                group_id.as_str().len(),
                group_id,
                task_ref.task_id().as_str().len(),
                task_ref.task_id(),
                role_id.as_str().len(),
                role_id,
                generation
            );
            if !self.executions.contains_key(&execution_id)
                && !self.dispatch_outbox.contains_key(&execution_id)
            {
                self.attempt_generations.insert(slot, generation);
                return Ok(execution_id);
            }
        }
    }

    /// Returns all Execute intents that have not yet been durably delivered.
    pub fn pending_dispatch_intents(&self) -> Vec<DispatchIntent> {
        self.dispatch_outbox
            .values()
            .filter(|intent| {
                !intent.delivered
                    && !self.cancellation_intents.contains(&intent.execution_id)
                    && self.execution_status(&intent.execution_id)
                        == Some(ExecutionStatus::Dispatched)
            })
            .cloned()
            .collect()
    }

    /// Marks one intent acknowledged after Node journal acceptance has been proven.
    pub fn mark_dispatch_delivered(
        &mut self,
        execution_id: &str,
    ) -> Result<(), ExecutionRuntimeError> {
        let intent = self.dispatch_outbox.get_mut(execution_id).ok_or_else(|| {
            ExecutionRuntimeError::ReconciliationRequired(format!(
                "dispatch intent {execution_id} is absent"
            ))
        })?;
        intent.delivered = true;
        Ok(())
    }

    /// Applies a durable Node command receipt to an immutable dispatch intent.
    pub fn observe_dispatch_receipt(
        &mut self,
        execution_id: &str,
        command_id: &str,
        node_id: &NodeId,
        persisted: bool,
        reason: impl Into<String>,
    ) -> Result<Vec<ExecutionEvent>, ExecutionRuntimeError> {
        let intent = self.dispatch_outbox.get(execution_id).ok_or_else(|| {
            ExecutionRuntimeError::ReconciliationRequired(format!(
                "dispatch intent {execution_id} is absent"
            ))
        })?;
        if intent.command_id() != command_id {
            return Err(ExecutionRuntimeError::ExecutionConflict(
                execution_id.to_string(),
            ));
        }
        if intent.command.node_id() != node_id {
            return Err(ExecutionRuntimeError::NodeOwnership(
                "command receipt node differs from execution owner".to_string(),
            ));
        }
        if persisted {
            self.mark_dispatch_delivered(execution_id)?;
            return Ok(Vec::new());
        }
        if self
            .execution_status(execution_id)
            .is_some_and(ExecutionStatus::is_terminal)
        {
            return Ok(Vec::new());
        }
        if self.execution_status(execution_id) == Some(ExecutionStatus::Unknown) {
            return Ok(Vec::new());
        }
        let reason = reason.into();
        // A rejected duplicate can describe conflicting input, not the outcome of physical work.
        self.execution_status
            .insert(execution_id.to_string(), ExecutionStatus::Unknown);
        let command = self
            .executions
            .get(execution_id)
            .map(|execution| execution.command.clone())
            .ok_or_else(|| {
                ExecutionRuntimeError::ReconciliationRequired(format!(
                    "execution context {execution_id} is absent"
                ))
            })?;
        let slot = (
            command.group_id().clone(),
            command.task_ref().clone(),
            command.role_id().clone(),
        );
        if self.active_executions.get(&slot).map(String::as_str) != Some(execution_id) {
            return Ok(Vec::new());
        }
        let mut events = vec![ExecutionEvent::RecoveryRequired {
            execution_id: execution_id.to_string(),
            node_id: command.node_id().clone(),
            context: Some(command),
            reason,
        }];
        events.extend(self.refresh_relations_for_slot(&slot));
        Ok(events)
    }

    /// Reduces a Cancel receipt without treating command admission as terminal execution evidence.
    pub fn observe_cancellation_receipt(
        &mut self,
        execution_id: &str,
        command_id: &str,
        node_id: &NodeId,
        persisted: bool,
        reason: impl Into<String>,
    ) -> Result<Vec<ExecutionEvent>, ExecutionRuntimeError> {
        if command_id != format!("cancel-{execution_id}")
            || !self.cancellation_intents.contains(execution_id)
        {
            return Err(ExecutionRuntimeError::ReconciliationRequired(format!(
                "cancellation intent {execution_id} is absent or conflicts with its receipt"
            )));
        }
        if self.cancellation_node(execution_id) != Some(node_id) {
            return Err(ExecutionRuntimeError::NodeOwnership(
                "cancellation receipt node differs from execution owner".to_string(),
            ));
        }
        if persisted
            || self
                .execution_status(execution_id)
                .is_some_and(ExecutionStatus::is_terminal)
            || self.execution_status(execution_id) == Some(ExecutionStatus::Unknown)
        {
            return Ok(Vec::new());
        }
        self.execution_status
            .insert(execution_id.to_string(), ExecutionStatus::Unknown);
        let command = self
            .executions
            .get(execution_id)
            .map(|context| context.command.clone())
            .ok_or_else(|| {
                ExecutionRuntimeError::ReconciliationRequired(format!(
                    "execution context {execution_id} is absent"
                ))
            })?;
        let slot = (
            command.group_id().clone(),
            command.task_ref().clone(),
            command.role_id().clone(),
        );
        if self.active_executions.get(&slot).map(String::as_str) != Some(execution_id) {
            return Ok(Vec::new());
        }
        let mut events = vec![ExecutionEvent::RecoveryRequired {
            execution_id: execution_id.to_string(),
            node_id: node_id.clone(),
            context: Some(command),
            reason: reason.into(),
        }];
        events.extend(self.refresh_relations_for_slot(&slot));
        Ok(events)
    }

    /// Records one Router delivery attempt while retaining the intent until Node acknowledgement.
    pub fn record_dispatch_attempt(&mut self, execution_id: &str) {
        if let Some(intent) = self.dispatch_outbox.get_mut(execution_id) {
            intent.delivery_attempts = intent.delivery_attempts.saturating_add(1);
        }
    }

    /// Prepares one immutable Execute intent before any network side effect occurs.
    pub fn prepare_dispatch(
        &mut self,
        execution_id: String,
        command: ExecutionCommand,
        resource_ids: Vec<ResourceId>,
    ) -> Result<DispatchDecision, ExecutionRuntimeError> {
        let decision = self.validate_dispatch(&execution_id, &command, &resource_ids)?;
        if decision == DispatchDecision::AlreadyRouted {
            return Ok(decision);
        }
        let resources = normalized_resources(&resource_ids);
        let slot = (
            command.group_id().clone(),
            command.task_ref().clone(),
            command.role_id().clone(),
        );
        self.executions.insert(
            execution_id.clone(),
            ExecutionContext {
                command: command.clone(),
                resource_ids: resources.clone(),
            },
        );
        self.execution_nodes
            .insert(execution_id.clone(), command.node_id().clone());
        let previous = self
            .active_executions
            .insert(slot.clone(), execution_id.clone());
        if previous
            .as_ref()
            .is_some_and(|current| current != &execution_id)
        {
            self.spatial_evidence.remove(&slot);
            if let Some(previous) = previous {
                self.reactivation_attempts.remove(&previous);
            }
            self.reactivation_attempts.insert(execution_id.clone());
        }
        self.execution_status
            .insert(execution_id.clone(), ExecutionStatus::Dispatched);
        self.dispatch_outbox.insert(
            execution_id.clone(),
            DispatchIntent {
                execution_id,
                command,
                resource_ids: resources,
                delivery_attempts: 0,
                delivered: false,
            },
        );
        Ok(DispatchDecision::Route)
    }

    /// Records one successfully routed committed execution context.
    pub fn record_dispatched(
        &mut self,
        execution_id: String,
        command: ExecutionCommand,
        resource_ids: Vec<ResourceId>,
    ) -> Result<(), ExecutionRuntimeError> {
        self.prepare_dispatch(execution_id.clone(), command, resource_ids)?;
        self.mark_dispatch_delivered(&execution_id)?;
        Ok(())
    }

    /// Returns the current Node target for cancellation of one known execution.
    pub fn cancellation_node(&self, execution_id: &str) -> Option<&NodeId> {
        self.executions
            .get(execution_id)
            .map(|execution| execution.command.node_id())
    }

    /// Returns the committed resource identities for one prepared execution attempt.
    pub fn execution_resources(&self, execution_id: &str) -> Option<Vec<ResourceId>> {
        self.executions
            .get(execution_id)
            .map(|execution| execution.resource_ids.clone())
    }

    /// Returns the latest accepted Runtime status for one execution identity.
    pub fn execution_status(&self, execution_id: &str) -> Option<ExecutionStatus> {
        self.execution_status.get(execution_id).copied()
    }

    /// Returns the status of the current attempt occupying one logical Group Task role.
    pub fn current_execution_status(
        &self,
        group_id: &ExecutionGroupId,
        task_ref: &TaskRef,
        role_id: &RoleId,
    ) -> Option<ExecutionStatus> {
        self.active_executions
            .get(&(group_id.clone(), task_ref.clone(), role_id.clone()))
            .and_then(|execution_id| self.execution_status.get(execution_id))
            .copied()
    }

    /// Returns current physical attempts for one Group, including terminal evidence.
    pub fn active_attempts_for_group(
        &self,
        group_id: &ExecutionGroupId,
    ) -> Vec<(String, ExecutionStatus)> {
        self.active_executions
            .iter()
            .filter(|((current_group, _, _), _)| current_group == group_id)
            .filter_map(|(_, execution_id)| {
                self.execution_status
                    .get(execution_id)
                    .copied()
                    .map(|status| (execution_id.clone(), status))
            })
            .collect()
    }

    /// Returns every retained physical attempt for one Group, including superseded history.
    pub fn attempts_for_group(
        &self,
        group_id: &ExecutionGroupId,
    ) -> Vec<(String, ExecutionStatus)> {
        self.executions
            .iter()
            .filter(|(_, context)| context.command.group_id() == group_id)
            .filter_map(|(execution_id, _)| {
                self.execution_status(execution_id)
                    .map(|status| (execution_id.clone(), status))
            })
            .collect()
    }

    /// Returns the physical attempt occupying an exact logical slot, including terminal history.
    pub fn current_attempt_id(
        &self,
        group_id: &ExecutionGroupId,
        task_ref: &TaskRef,
        role_id: &RoleId,
    ) -> Option<&str> {
        self.active_executions
            .get(&(group_id.clone(), task_ref.clone(), role_id.clone()))
            .map(String::as_str)
    }

    /// Returns whether the current logical-slot attempt targets one exact committed Node.
    pub fn current_attempt_matches_node(
        &self,
        group_id: &ExecutionGroupId,
        task_ref: &TaskRef,
        role_id: &RoleId,
        node_id: &NodeId,
    ) -> bool {
        self.current_attempt_id(group_id, task_ref, role_id)
            .and_then(|execution_id| self.executions.get(execution_id))
            .is_some_and(|context| context.command.node_id() == node_id)
    }

    /// Records cancellation before delivery; Unknown remains pending until physical evidence resolves it.
    pub fn request_cancellation(
        &mut self,
        execution_id: &str,
    ) -> Result<(), ExecutionRuntimeError> {
        if !self.executions.contains_key(execution_id) {
            return Err(ExecutionRuntimeError::ReconciliationRequired(
                "unknown cancellation attempt".to_string(),
            ));
        }
        self.cancellation_intents.insert(execution_id.to_string());
        Ok(())
    }

    /// Returns cancellations that still need terminal physical evidence, even after a receipt.
    pub fn pending_cancellations(&self) -> Vec<(String, NodeId)> {
        self.cancellation_intents
            .iter()
            .filter(|id| {
                !self
                    .execution_status(id)
                    .is_some_and(ExecutionStatus::is_terminal)
            })
            .filter_map(|id| {
                self.cancellation_node(id)
                    .map(|node| (id.clone(), node.clone()))
            })
            .collect()
    }

    /// Converts each nonterminal attempt on an unavailable Node into recovery evidence.
    pub fn observe_node_unavailable(
        &mut self,
        node_id: &NodeId,
        reason: impl Into<String>,
    ) -> Vec<ExecutionEvent> {
        let reason = reason.into();
        let current_attempts = self
            .active_executions
            .values()
            .cloned()
            .collect::<BTreeSet<_>>();
        let execution_ids = self
            .execution_nodes
            .iter()
            .filter(|(execution_id, owner)| {
                current_attempts.contains(execution_id.as_str())
                    && *owner == node_id
                    && self
                        .execution_status(execution_id.as_str())
                        .is_some_and(|status| {
                            !status.is_terminal() && status != ExecutionStatus::Unknown
                        })
            })
            .map(|(execution_id, _)| execution_id.clone())
            .collect::<Vec<_>>();
        execution_ids
            .into_iter()
            .flat_map(|execution_id| {
                self.execution_status
                    .insert(execution_id.clone(), ExecutionStatus::Unknown);
                let context = self
                    .executions
                    .get(&execution_id)
                    .map(|execution| execution.command.clone());
                let slot = context.as_ref().map(|command| {
                    (
                        command.group_id().clone(),
                        command.task_ref().clone(),
                        command.role_id().clone(),
                    )
                });
                let mut events = vec![ExecutionEvent::RecoveryRequired {
                    context,
                    execution_id,
                    node_id: node_id.clone(),
                    reason: reason.clone(),
                }];
                if let Some(slot) = slot {
                    events.extend(self.refresh_relations_for_slot(&slot));
                }
                events
            })
            .collect()
    }

    /// Exposes immutable attempt contexts and their status for audit without changing active slots.
    pub fn attempt_history(&self) -> Vec<ExecutionAttemptSnapshot> {
        self.executions
            .iter()
            .filter_map(|(execution_id, context)| {
                self.execution_status(execution_id)
                    .map(|status| ExecutionAttemptSnapshot {
                        execution_id: execution_id.clone(),
                        command: context.command.clone(),
                        status,
                    })
            })
            .collect()
    }

    /// Returns current logical-slot commands whose physical attempt remains ambiguous.
    pub fn current_unknown_attempts(&self) -> Vec<ExecutionCommand> {
        self.active_executions
            .values()
            .filter(|execution_id| {
                self.execution_status(execution_id) == Some(ExecutionStatus::Unknown)
            })
            .filter_map(|execution_id| {
                self.executions
                    .get(execution_id)
                    .map(|context| context.command.clone())
            })
            .collect()
    }

    /// Reduces one ordered Node execution fact into canonical Runtime events.
    pub fn observe_execution(
        &mut self,
        execution_id: &str,
        node_id: NodeId,
        sequence: u64,
        status: ExecutionStatus,
        reason: impl Into<String>,
    ) -> Result<Vec<ExecutionEvent>, ExecutionRuntimeError> {
        if self
            .execution_nodes
            .get(execution_id)
            .is_some_and(|expected| expected != &node_id)
        {
            return Err(ExecutionRuntimeError::NodeOwnership(
                "execution fact node differs from execution owner".to_string(),
            ));
        }
        if self
            .execution_sequences
            .get(execution_id)
            .is_some_and(|current| sequence <= *current)
        {
            return Ok(Vec::new());
        }
        if let Some(current) = self.execution_status.get(execution_id)
            && current.is_terminal()
        {
            if *current == status {
                return Ok(Vec::new());
            }
            return Err(ExecutionRuntimeError::TerminalConflict(
                "terminal execution status is immutable".to_string(),
            ));
        }
        if let Some(execution) = self.executions.get(execution_id)
            && execution.command.node_id() != &node_id
        {
            return Err(ExecutionRuntimeError::NodeOwnership(
                "execution fact node differs from dispatched command".to_string(),
            ));
        }
        self.execution_sequences
            .insert(execution_id.to_string(), sequence);
        self.execution_nodes
            .entry(execution_id.to_string())
            .or_insert(node_id);
        self.execution_status
            .insert(execution_id.to_string(), status);
        if let Some(intent) = self.dispatch_outbox.get_mut(execution_id) {
            intent.delivered = true;
        }

        let reason = reason.into();
        let Some(execution) = self.executions.get(execution_id) else {
            return Ok(if status == ExecutionStatus::Unknown {
                vec![ExecutionEvent::RecoveryRequired {
                    execution_id: execution_id.to_string(),
                    node_id: self
                        .execution_nodes
                        .get(execution_id)
                        .cloned()
                        .expect("execution owner recorded above"),
                    context: None,
                    reason,
                }]
            } else {
                Vec::new()
            });
        };
        let command = execution.command.clone();
        let task_key = (command.group_id().clone(), command.task_ref().clone());
        let execution_role = (
            command.group_id().clone(),
            command.task_ref().clone(),
            command.role_id().clone(),
        );
        let is_current_attempt = self
            .active_executions
            .get(&execution_role)
            .is_some_and(|current| current == execution_id);
        let mut events = Vec::new();
        let replacement_activated = is_current_attempt
            && status.proves_activation()
            && self.reactivation_attempts.remove(execution_id);
        if is_current_attempt
            && status.proves_activation()
            && (self.activated_tasks.insert(task_key.clone()) || replacement_activated)
        {
            events.push(ExecutionEvent::TaskActivated {
                group_id: task_key.0,
                task_ref: task_key.1,
            });
        }
        if is_current_attempt {
            match status {
                ExecutionStatus::Completed => {
                    events.push(ExecutionEvent::RoleCompleted { command });
                }
                ExecutionStatus::Failed | ExecutionStatus::Cancelled => {
                    events.push(ExecutionEvent::RoleFailed { command, reason });
                }
                ExecutionStatus::Unknown => events.push(ExecutionEvent::RecoveryRequired {
                    execution_id: execution_id.to_string(),
                    node_id: command.node_id().clone(),
                    context: Some(command),
                    reason,
                }),
                ExecutionStatus::Dispatched
                | ExecutionStatus::Accepted
                | ExecutionStatus::Running => {}
            }
            events.extend(self.refresh_relations_for_slot(&execution_role));
        }
        Ok(events)
    }

    /// Reduces current role execution facts into one Task terminal result when available.
    pub fn task_result<'a>(
        &self,
        group_id: &ExecutionGroupId,
        task_ref: &TaskRef,
        role_ids: impl IntoIterator<Item = &'a RoleId>,
    ) -> Option<ObservedTaskResult> {
        let mut saw_role = false;
        let mut all_completed = true;
        for role_id in role_ids {
            saw_role = true;
            let status = self
                .active_executions
                .get(&(group_id.clone(), task_ref.clone(), role_id.clone()))
                .and_then(|execution_id| self.execution_status.get(execution_id))
                .copied();
            match status {
                Some(ExecutionStatus::Completed) => {}
                Some(ExecutionStatus::Failed | ExecutionStatus::Cancelled) => {
                    return Some(ObservedTaskResult::Failed);
                }
                Some(ExecutionStatus::Unknown) => return None,
                _ => all_completed = false,
            }
        }
        (saw_role && all_completed && self.relations_allow_task_success(group_id, task_ref))
            .then_some(ObservedTaskResult::Succeeded)
    }
}

/// Returns stable, duplicate-free committed resource identities.
fn normalized_resources(resource_ids: &[ResourceId]) -> Vec<ResourceId> {
    let mut resources = resource_ids.to_vec();
    resources.sort();
    resources.dedup();
    resources
}

/// Validates cross-map Runtime checkpoint invariants before constructing live state.
fn validate_checkpoint(
    checkpoint: &RuntimeExecutionCheckpoint,
) -> Result<(), ExecutionRuntimeError> {
    let mut attempt_slots = BTreeSet::new();
    for attempt in &checkpoint.attempt_generations {
        let slot = (
            attempt.group_id.clone(),
            attempt.task_ref.clone(),
            attempt.role_id.clone(),
        );
        if attempt.generation == 0 || !attempt_slots.insert(slot) {
            return Err(ExecutionRuntimeError::InvalidCheckpoint(
                "checkpoint contains an invalid or duplicate attempt generation".to_string(),
            ));
        }
    }
    let mut dispatch_ids = BTreeSet::new();
    for intent in &checkpoint.dispatch_outbox {
        let Some(context) = checkpoint.executions.get(&intent.execution_id) else {
            return Err(ExecutionRuntimeError::InvalidCheckpoint(format!(
                "dispatch intent {} has no execution context",
                intent.execution_id
            )));
        };
        if intent.execution_id.trim().is_empty()
            || !dispatch_ids.insert(intent.execution_id.clone())
            || context.command != intent.command
            || context.resource_ids != normalized_resources(&intent.resource_ids)
        {
            return Err(ExecutionRuntimeError::InvalidCheckpoint(format!(
                "dispatch intent {} differs from its immutable execution context",
                intent.execution_id
            )));
        }
    }
    if checkpoint
        .cancellation_intents
        .iter()
        .any(|execution_id| !checkpoint.executions.contains_key(execution_id))
    {
        return Err(ExecutionRuntimeError::InvalidCheckpoint(
            "checkpoint cancellation references an unknown execution".to_string(),
        ));
    }
    for (execution_id, context) in &checkpoint.executions {
        if execution_id.is_empty() {
            return Err(ExecutionRuntimeError::InvalidCheckpoint(
                "checkpoint contains an empty execution id".to_string(),
            ));
        }
        if checkpoint.execution_nodes.get(execution_id) != Some(context.command.node_id()) {
            return Err(ExecutionRuntimeError::InvalidCheckpoint(format!(
                "execution {execution_id} has inconsistent node ownership"
            )));
        }
        if !checkpoint.execution_status.contains_key(execution_id) {
            return Err(ExecutionRuntimeError::InvalidCheckpoint(format!(
                "execution {execution_id} has no status"
            )));
        }
    }
    for active in &checkpoint.active_executions {
        let Some(context) = checkpoint.executions.get(&active.execution_id) else {
            return Err(ExecutionRuntimeError::InvalidCheckpoint(format!(
                "active execution {} has no context",
                active.execution_id
            )));
        };
        if context.command.group_id() != &active.group_id
            || context.command.task_ref() != &active.task_ref
            || context.command.role_id() != &active.role_id
        {
            return Err(ExecutionRuntimeError::InvalidCheckpoint(format!(
                "active execution {} differs from its command Group Task role",
                active.execution_id
            )));
        }
    }
    for execution_id in checkpoint.execution_sequences.keys() {
        if !checkpoint.execution_status.contains_key(execution_id) {
            return Err(ExecutionRuntimeError::InvalidCheckpoint(format!(
                "execution sequence {execution_id} has no status"
            )));
        }
    }
    for proof in &checkpoint.relation_proofs {
        let Some(relation) = checkpoint.relations.iter().find(|relation| {
            relation.group_id() == &proof.group_id && relation.relation_id() == &proof.relation_id
        }) else {
            continue;
        };
        let Some(context) = checkpoint.executions.get(&proof.target_execution_id) else {
            return Err(ExecutionRuntimeError::InvalidCheckpoint(format!(
                "relation {} proof references unknown target execution {}",
                proof.relation_id, proof.target_execution_id
            )));
        };
        if context.command.group_id() != relation.group_id()
            || context.command.task_ref() != relation.target_task_ref()
            || context.command.role_id() != relation.target_role_id()
        {
            return Err(ExecutionRuntimeError::InvalidCheckpoint(format!(
                "relation {} proof does not reference its target logical slot",
                proof.relation_id
            )));
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use domain::{
        CapabilityContractRef, CorrelationId, ExecutionIntent, ExecutionRelationId,
        ExecutionRelationKind, ExecutionRelationSpec, MissionId, PlannedExecutionRef, TaskId,
    };

    /// Builds one deterministic command for Runtime registry tests.
    fn command() -> ExecutionCommand {
        command_for("task-a", "carrier", "node-a")
    }

    /// Builds one deterministic command for an exact logical execution slot.
    fn command_for(task_id: &str, role_id: &str, node_id: &str) -> ExecutionCommand {
        ExecutionCommand::new(
            MissionId::new("mission-a").expect("mission valid"),
            TaskId::new(task_id).expect("task valid"),
            ExecutionGroupId::new("group-a").expect("group valid"),
            RoleId::new(role_id).expect("role valid"),
            NodeId::new(node_id).expect("node valid"),
            ExecutionIntent::new(
                CapabilityContractRef::new("mobility", "move", "v1").expect("contract valid"),
                BTreeMap::new(),
            )
            .expect("intent valid"),
            CorrelationId::new("runtime-test").expect("correlation valid"),
        )
    }

    /// Persists dispatch intent and fences automatic replay after Controller restart.
    #[test]
    fn dispatch_outbox_survives_restore_and_receipt_is_idempotent() {
        let mut runtime = RuntimeExecutionManager::new();
        let command = command();
        runtime
            .prepare_dispatch("attempt-1".to_string(), command.clone(), Vec::new())
            .expect("intent prepares");
        assert_eq!(runtime.pending_dispatch_intents().len(), 1);

        let mut restored = RuntimeExecutionManager::restore(runtime.checkpoint())
            .expect("unacknowledged dispatch restores");
        assert!(
            restored.pending_dispatch_intents().is_empty(),
            "restored physical ambiguity requires recovery rather than implicit replay"
        );
        let events = restored
            .observe_dispatch_receipt(
                "attempt-1",
                "dispatch-attempt-1",
                command.node_id(),
                true,
                "",
            )
            .expect("receipt is accepted");
        assert!(events.is_empty());
        assert!(restored.pending_dispatch_intents().is_empty());
        assert!(
            restored
                .observe_dispatch_receipt(
                    "attempt-1",
                    "dispatch-attempt-1",
                    command.node_id(),
                    true,
                    "",
                )
                .is_ok()
        );
        assert!(matches!(
            restored.observe_dispatch_receipt(
                "attempt-1",
                "dispatch-attempt-1",
                &NodeId::new("wrong-node").expect("node valid"),
                true,
                "",
            ),
            Err(ExecutionRuntimeError::NodeOwnership(_))
        ));
    }

    /// Durable cancellation suppresses Execute and survives until terminal Node evidence.
    #[test]
    fn cancellation_intent_survives_restart_and_terminal_fact_clears_delivery() {
        let mut runtime = RuntimeExecutionManager::new();
        let command = command();
        runtime
            .prepare_dispatch("attempt-1".to_string(), command.clone(), Vec::new())
            .expect("intent prepares");
        runtime
            .request_cancellation("attempt-1")
            .expect("cancellation records");
        assert!(runtime.pending_dispatch_intents().is_empty());

        let mut restored =
            RuntimeExecutionManager::restore(runtime.checkpoint()).expect("cancellation restores");
        assert_eq!(restored.pending_cancellations().len(), 1);
        restored
            .observe_cancellation_receipt(
                "attempt-1",
                "cancel-attempt-1",
                command.node_id(),
                true,
                "",
            )
            .expect("Cancel receipt matches durable intent");
        assert_eq!(
            restored.execution_status("attempt-1"),
            Some(ExecutionStatus::Unknown),
            "Cancel receipt must not synthesize a terminal lifecycle fact"
        );
        restored
            .observe_execution(
                "attempt-1",
                command.node_id().clone(),
                1,
                ExecutionStatus::Cancelled,
                "cancelled before local dispatch",
            )
            .expect("terminal cancellation records");
        assert!(restored.pending_cancellations().is_empty());
    }

    /// Allocated physical attempt identities advance while the logical slot remains stable.
    #[test]
    fn attempt_identity_advances_per_logical_slot() {
        let mut runtime = RuntimeExecutionManager::new();
        let group = ExecutionGroupId::new("group-a").expect("group valid");
        let task = TaskRef::new(
            MissionId::new("mission-a").expect("mission valid"),
            TaskId::new("task-a").expect("task valid"),
        );
        let role = RoleId::new("carrier").expect("role valid");
        assert_eq!(
            runtime
                .allocate_attempt_id(&group, &task, &role)
                .expect("first attempt allocates"),
            "attempt-7:group-a-6:task-a-7:carrier-1"
        );
        assert_eq!(
            runtime
                .allocate_attempt_id(&group, &task, &role)
                .expect("second attempt allocates"),
            "attempt-7:group-a-6:task-a-7:carrier-2"
        );
    }

    /// Allocation skips a retained attempt identity when migrating pre-generation history.
    #[test]
    fn attempt_identity_does_not_collide_with_retained_history() {
        let mut runtime = RuntimeExecutionManager::new();
        let command = command();
        runtime
            .prepare_dispatch(
                "attempt-7:group-a-6:task-a-7:carrier-1".to_string(),
                command.clone(),
                Vec::new(),
            )
            .expect("historical attempt prepares");

        assert_eq!(
            runtime
                .allocate_attempt_id(command.group_id(), command.task_ref(), command.role_id())
                .expect("unused attempt allocates"),
            "attempt-7:group-a-6:task-a-7:carrier-2"
        );
    }

    /// Node loss fences one physical attempt once while a replacement retains immutable history.
    #[test]
    fn node_loss_emits_one_recovery_event_and_retains_attempt_history() {
        let mut runtime = RuntimeExecutionManager::new();
        let original = command();
        runtime
            .prepare_dispatch("attempt-1".to_string(), original.clone(), Vec::new())
            .expect("original attempt prepares");
        runtime
            .observe_execution(
                "attempt-1",
                original.node_id().clone(),
                1,
                ExecutionStatus::Running,
                "started",
            )
            .expect("running fact records");

        let recovery = runtime.observe_node_unavailable(original.node_id(), "route lost");
        assert!(matches!(
            recovery.as_slice(),
            [ExecutionEvent::RecoveryRequired { execution_id, .. }] if execution_id == "attempt-1"
        ));
        assert!(
            runtime
                .observe_node_unavailable(original.node_id(), "duplicate route loss")
                .is_empty()
        );

        let replacement = command_for("task-a", "carrier", "node-b");
        runtime
            .prepare_dispatch("attempt-2".to_string(), replacement, Vec::new())
            .expect("replacement attempt prepares");
        assert_eq!(
            runtime.current_attempt_id(
                original.group_id(),
                original.task_ref(),
                original.role_id()
            ),
            Some("attempt-2")
        );
        let history = runtime.attempt_history();
        assert_eq!(history.len(), 2);
        assert_eq!(runtime.attempts_for_group(original.group_id()).len(), 2);
        assert!(history.iter().any(|attempt| {
            attempt.execution_id() == "attempt-1" && attempt.status() == ExecutionStatus::Unknown
        }));
        assert!(history.iter().any(|attempt| {
            attempt.execution_id() == "attempt-2" && attempt.status() == ExecutionStatus::Dispatched
        }));
        assert!(
            runtime
                .observe_node_unavailable(original.node_id(), "historical route loss")
                .is_empty()
        );
        assert!(
            runtime
                .observe_execution(
                    "attempt-1",
                    original.node_id().clone(),
                    2,
                    ExecutionStatus::Cancelled,
                    "late historical terminal fact",
                )
                .expect("historical terminal fact is retained")
                .is_empty()
        );
        let reactivated = runtime
            .observe_execution(
                "attempt-2",
                NodeId::new("node-b").expect("node valid"),
                1,
                ExecutionStatus::Accepted,
                "replacement accepted",
            )
            .expect("replacement acceptance records");
        assert!(matches!(
            reactivated.as_slice(),
            [ExecutionEvent::TaskActivated { .. }]
        ));
    }

    /// Node acceptance activates a Task exactly once and terminal facts reduce its result.
    #[test]
    fn runtime_drives_activation_and_terminal_result() {
        let mut runtime = RuntimeExecutionManager::new();
        let command = command();
        runtime
            .record_dispatched("execution-a".to_string(), command.clone(), Vec::new())
            .expect("dispatch records");

        let activated = runtime
            .observe_execution(
                "execution-a",
                command.node_id().clone(),
                1,
                ExecutionStatus::Accepted,
                "",
            )
            .expect("acceptance records");
        assert!(matches!(
            activated.as_slice(),
            [ExecutionEvent::TaskActivated { .. }]
        ));
        let repeated = runtime
            .observe_execution(
                "execution-a",
                command.node_id().clone(),
                2,
                ExecutionStatus::Running,
                "",
            )
            .expect("running records");
        assert!(repeated.is_empty());
        runtime
            .observe_execution(
                "execution-a",
                command.node_id().clone(),
                3,
                ExecutionStatus::Completed,
                "",
            )
            .expect("completion records");
        assert_eq!(
            runtime.task_result(
                command.group_id(),
                command.task_ref(),
                std::iter::once(command.role_id())
            ),
            Some(ObservedTaskResult::Succeeded)
        );
    }

    /// Restore fences command replay and converts nonterminal state to Unknown.
    #[test]
    fn restore_requires_reconciliation_before_replay() {
        let mut runtime = RuntimeExecutionManager::new();
        let command = command();
        runtime
            .record_dispatched("execution-a".to_string(), command.clone(), Vec::new())
            .expect("dispatch records");
        runtime
            .observe_execution(
                "execution-a",
                command.node_id().clone(),
                1,
                ExecutionStatus::Running,
                "",
            )
            .expect("running records");

        let restored =
            RuntimeExecutionManager::restore(runtime.checkpoint()).expect("checkpoint restores");
        assert_eq!(
            restored.execution_status("execution-a"),
            Some(ExecutionStatus::Unknown)
        );
        assert!(matches!(
            restored.validate_dispatch("execution-a", &command, &[]),
            Err(ExecutionRuntimeError::ReconciliationRequired(_))
        ));
        assert_eq!(
            restored.task_result(
                command.group_id(),
                command.task_ref(),
                std::iter::once(command.role_id())
            ),
            None,
            "unknown physical state must remain recovery-pending"
        );
    }

    /// Restore rejects a satisfaction proof attached to a non-target execution attempt.
    #[test]
    fn restore_rejects_relation_proof_for_wrong_logical_slot() {
        let mut runtime = RuntimeExecutionManager::new();
        let group_id = ExecutionGroupId::new("group-a").expect("group valid");
        runtime
            .register_relations(
                &group_id,
                &MissionId::new("mission-a").expect("mission valid"),
                &[ExecutionRelationSpec::new(
                    ExecutionRelationId::new("source-guards-target").expect("relation valid"),
                    PlannedExecutionRef::new(
                        TaskId::new("source-task").expect("task valid"),
                        RoleId::new("source-role").expect("role valid"),
                    ),
                    PlannedExecutionRef::new(
                        TaskId::new("task-a").expect("task valid"),
                        RoleId::new("carrier").expect("role valid"),
                    ),
                    ExecutionRelationKind::RequiresActive,
                )
                .expect("relation valid")],
            )
            .expect("relation registers");
        let source = command_for("source-task", "source-role", "node-source");
        let target = command();
        runtime
            .record_dispatched("source-execution".to_string(), source.clone(), Vec::new())
            .expect("source dispatch records");
        runtime
            .record_dispatched("target-execution".to_string(), target.clone(), Vec::new())
            .expect("target dispatch records");
        runtime
            .observe_execution(
                "source-execution",
                source.node_id().clone(),
                1,
                ExecutionStatus::Running,
                "",
            )
            .expect("source running records");
        runtime
            .observe_execution(
                "target-execution",
                target.node_id().clone(),
                1,
                ExecutionStatus::Running,
                "",
            )
            .expect("target running records");

        let mut checkpoint = runtime.checkpoint();
        checkpoint.relation_proofs[0].target_execution_id = "source-execution".to_string();
        assert!(matches!(
            RuntimeExecutionManager::restore(checkpoint),
            Err(ExecutionRuntimeError::InvalidCheckpoint(reason))
                if reason.contains("target logical slot")
        ));
    }

    /// Unknown execution produces recovery evidence without becoming a terminal Task failure.
    #[test]
    fn unknown_execution_requires_reconciliation_without_task_failure() {
        let mut runtime = RuntimeExecutionManager::new();
        let command = command();
        runtime
            .record_dispatched("execution-a".to_string(), command.clone(), Vec::new())
            .expect("dispatch records");

        let events = runtime
            .observe_execution(
                "execution-a",
                command.node_id().clone(),
                1,
                ExecutionStatus::Unknown,
                "physical outcome is ambiguous",
            )
            .expect("unknown fact records");

        assert!(matches!(
            events.as_slice(),
            [ExecutionEvent::RecoveryRequired {
                context: Some(_),
                ..
            }]
        ));
        assert_eq!(
            runtime.task_result(
                command.group_id(),
                command.task_ref(),
                std::iter::once(command.role_id())
            ),
            None
        );

        let mut restored =
            RuntimeExecutionManager::restore(runtime.checkpoint()).expect("checkpoint restores");
        let accepted = restored
            .observe_execution(
                "execution-a",
                command.node_id().clone(),
                2,
                ExecutionStatus::Accepted,
                "",
            )
            .expect("accepted fact records after recovery");
        assert!(matches!(
            accepted.as_slice(),
            [ExecutionEvent::TaskActivated { .. }]
        ));
    }
}
