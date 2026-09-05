//! Runtime-owned live state for Mission execution coordination relations.

use crate::{ExecutionEvent, ExecutionStatus, RuntimeExecutionManager};
use domain::{
    ExecutionCouplingMode, ExecutionGroupId, ExecutionRelationId, ExecutionRelationKind,
    ExecutionRelationSpec, ExecutionRelationState, ExecutionRelationType,
    LocalizationVerificationEvidence, MapRevisionSelector, MissionId, NodeId, RoleId, TaskRef,
    TimestampMs,
};
use std::collections::{BTreeMap, BTreeSet};

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

impl RuntimeExecutionManager {
    /// Returns whether evidence names the current logical execution attempt.
    pub fn shared_spatial_evidence_matches_current_execution(
        &self,
        evidence: &SharedSpatialEvidence,
    ) -> bool {
        self.active_executions.get(&evidence.slot()) == Some(&evidence.execution_id)
    }

    /// Returns whether evidence names the current attempt and its current physical owner.
    pub fn shared_spatial_evidence_targets_current_attempt(
        &self,
        evidence: &SharedSpatialEvidence,
    ) -> bool {
        let slot = evidence.slot();
        self.active_executions.get(&slot) == Some(&evidence.execution_id)
            && self.execution_nodes.get(&evidence.execution_id) == Some(&evidence.node_id)
    }

    /// Records strong localization evidence for one current logical execution attempt.
    pub fn observe_shared_spatial_evidence(
        &mut self,
        evidence: SharedSpatialEvidence,
    ) -> Result<Vec<ExecutionEvent>, crate::ExecutionRuntimeError> {
        let slot = evidence.slot();
        if self.active_executions.get(&slot) != Some(&evidence.execution_id) {
            return Err(crate::ExecutionRuntimeError::ReconciliationRequired(
                "localization evidence does not belong to the current execution attempt"
                    .to_string(),
            ));
        }
        if self.execution_nodes.get(&evidence.execution_id) != Some(&evidence.node_id) {
            return Err(crate::ExecutionRuntimeError::NodeOwnership(
                "localization evidence node differs from execution owner".to_string(),
            ));
        }
        if let Some(current) = self.spatial_evidence.get(&slot) {
            if evidence.received_at < current.received_at {
                return Err(crate::ExecutionRuntimeError::ReconciliationRequired(
                    "older localization evidence cannot replace current evidence".to_string(),
                ));
            }
            if evidence.received_at == current.received_at {
                return if current == &evidence {
                    Ok(Vec::new())
                } else {
                    Err(crate::ExecutionRuntimeError::ExecutionConflict(
                        evidence.execution_id,
                    ))
                };
            }
        }
        self.spatial_evidence.insert(slot.clone(), evidence);
        Ok(self.refresh_relations_for_slot(&slot))
    }

    /// Returns strong spatial evidence for one current logical execution slot.
    pub fn shared_spatial_evidence(
        &self,
        group_id: &ExecutionGroupId,
        task_ref: &TaskRef,
        role_id: &RoleId,
    ) -> Option<&SharedSpatialEvidence> {
        self.current_spatial_evidence(&(group_id.clone(), task_ref.clone(), role_id.clone()))
    }

    /// Registers exact Mission relation specifications without selecting physical endpoints.
    pub fn register_relations(
        &mut self,
        group_id: &ExecutionGroupId,
        mission_id: &MissionId,
        specifications: &[ExecutionRelationSpec],
    ) -> Result<Vec<ExecutionEvent>, crate::ExecutionRuntimeError> {
        self.register_relations_with_modes(group_id, mission_id, specifications, &BTreeMap::new())
    }

    /// Registers Mission relations with effective Task coupling modes.
    pub fn register_relations_with_modes(
        &mut self,
        group_id: &ExecutionGroupId,
        mission_id: &MissionId,
        specifications: &[ExecutionRelationSpec],
        task_modes: &BTreeMap<domain::TaskId, ExecutionCouplingMode>,
    ) -> Result<Vec<ExecutionEvent>, crate::ExecutionRuntimeError> {
        let mut events = Vec::new();
        for specification in specifications {
            let relation = RuntimeExecutionRelation {
                group_id: group_id.clone(),
                relation_id: specification.relation_id().clone(),
                source_task_ref: TaskRef::new(
                    mission_id.clone(),
                    specification.source().task_id().clone(),
                ),
                source_role_id: specification.source().role_id().clone(),
                target_task_ref: TaskRef::new(
                    mission_id.clone(),
                    specification.target().task_id().clone(),
                ),
                target_role_id: specification.target().role_id().clone(),
                kind: specification.kind(),
                relation_type: specification.relation_type().clone(),
                coupling_mode: task_modes
                    .get(specification.target().task_id())
                    .copied()
                    .unwrap_or_default(),
            };
            let key = relation.key();
            if let Some(existing) = self.relations.get(&key) {
                if existing != &relation {
                    return Err(crate::ExecutionRuntimeError::ExecutionConflict(format!(
                        "relation {} in Group {}",
                        specification.relation_id(),
                        group_id
                    )));
                }
                continue;
            }
            self.relations.insert(key.clone(), relation.clone());
            self.relation_states
                .insert(key, ExecutionRelationState::Dormant);
            events.push(ExecutionEvent::RelationRegistered { relation });
        }
        Ok(events)
    }

    /// Returns stable relation snapshots for one Group.
    pub fn relation_snapshots(&self, group_id: &ExecutionGroupId) -> Vec<RuntimeRelationSnapshot> {
        self.relations
            .iter()
            .filter(|((candidate, _), _)| candidate == group_id)
            .map(|(key, relation)| RuntimeRelationSnapshot {
                relation: relation.clone(),
                state: self
                    .relation_states
                    .get(key)
                    .copied()
                    .unwrap_or(ExecutionRelationState::Dormant),
                reconciliation_required: self.relation_fences.contains(key),
                source_execution_id: self.active_executions.get(&relation.source_key()).cloned(),
                target_execution_id: self.active_executions.get(&relation.target_key()).cloned(),
            })
            .collect()
    }

    /// Acknowledges Control reconciliation for a relation whose endpoints are coordinated again.
    ///
    /// Relation fences are intentionally latched across later observations.  Recovery or rebind
    /// code must explicitly acknowledge the repaired relation before target progression is
    /// permitted again.
    pub fn acknowledge_relation_reconciliation(
        &mut self,
        group_id: &ExecutionGroupId,
        relation_id: &ExecutionRelationId,
    ) -> Result<(), crate::ExecutionRuntimeError> {
        let key = (group_id.clone(), relation_id.clone());
        if !self.relations.contains_key(&key) {
            return Err(crate::ExecutionRuntimeError::ReconciliationRequired(
                format!("unknown execution relation {relation_id} in Group {group_id}"),
            ));
        }
        let satisfied = self.relations.get(&key).is_some_and(|relation| {
            self.derive_relation_state(relation) == ExecutionRelationState::Satisfied
        });
        if !satisfied {
            return Err(crate::ExecutionRuntimeError::ReconciliationRequired(
                format!("execution relation {relation_id} is not satisfied"),
            ));
        }
        self.relation_fences.remove(&key);
        Ok(())
    }

    /// Confirms Runtime retained exactly the relations in one restored MissionPlan.
    pub fn validate_relations(
        &self,
        group_id: &ExecutionGroupId,
        mission_id: &MissionId,
        specifications: &[ExecutionRelationSpec],
    ) -> Result<(), crate::ExecutionRuntimeError> {
        self.validate_relations_with_modes(group_id, mission_id, specifications, &BTreeMap::new())
    }

    /// Confirms restored relations including effective Task coupling modes.
    pub fn validate_relations_with_modes(
        &self,
        group_id: &ExecutionGroupId,
        mission_id: &MissionId,
        specifications: &[ExecutionRelationSpec],
        task_modes: &BTreeMap<domain::TaskId, ExecutionCouplingMode>,
    ) -> Result<(), crate::ExecutionRuntimeError> {
        let expected = specifications
            .iter()
            .map(|specification| RuntimeExecutionRelation {
                group_id: group_id.clone(),
                relation_id: specification.relation_id().clone(),
                source_task_ref: TaskRef::new(
                    mission_id.clone(),
                    specification.source().task_id().clone(),
                ),
                source_role_id: specification.source().role_id().clone(),
                target_task_ref: TaskRef::new(
                    mission_id.clone(),
                    specification.target().task_id().clone(),
                ),
                target_role_id: specification.target().role_id().clone(),
                kind: specification.kind(),
                relation_type: specification.relation_type().clone(),
                coupling_mode: task_modes
                    .get(specification.target().task_id())
                    .copied()
                    .unwrap_or_default(),
            })
            .map(|relation| (relation.key(), relation))
            .collect::<BTreeMap<_, _>>();
        let actual = self
            .relations
            .iter()
            .filter(|((candidate, _), _)| candidate == group_id)
            .map(|(key, relation)| (key.clone(), relation.clone()))
            .collect::<BTreeMap<_, _>>();
        if expected != actual {
            return Err(crate::ExecutionRuntimeError::InvalidCheckpoint(format!(
                "Runtime relations differ from Mission {mission_id}"
            )));
        }
        Ok(())
    }

    /// Re-evaluates every relation touching one logical execution slot.
    pub(crate) fn refresh_relations_for_slot(
        &mut self,
        slot: &(ExecutionGroupId, TaskRef, RoleId),
    ) -> Vec<ExecutionEvent> {
        let keys = self
            .relations
            .iter()
            .filter(|(_, relation)| {
                &relation.source_key() == slot || &relation.target_key() == slot
            })
            .map(|(key, _)| key.clone())
            .collect::<Vec<_>>();
        keys.into_iter()
            .flat_map(|key| self.refresh_relation(&key))
            .collect()
    }

    /// Re-evaluates all restored relations without emitting process-local transitions.
    pub(crate) fn refresh_all_relations_after_restore(&mut self) {
        let keys = self.relations.keys().cloned().collect::<Vec<_>>();
        for key in keys {
            let _ = self.refresh_relation(&key);
        }
    }

    /// Returns whether every relation targeting this Task permits successful progression.
    pub(crate) fn relations_allow_task_success(
        &self,
        group_id: &ExecutionGroupId,
        task_ref: &TaskRef,
    ) -> bool {
        self.relations.iter().all(|(key, relation)| {
            if relation.group_id() != group_id || relation.target_task_ref() != task_ref {
                return true;
            }
            let Some(target_execution_id) = self.active_executions.get(&relation.target_key())
            else {
                return false;
            };
            !self.relation_fences.contains(key)
                && self.relation_proofs.get(key) == Some(target_execution_id)
        })
    }

    /// Computes and records one relation transition from current execution attempts.
    fn refresh_relation(&mut self, key: &RelationKey) -> Vec<ExecutionEvent> {
        let relation = self
            .relations
            .get(key)
            .expect("relation key came from Runtime registry")
            .clone();
        let current = self.derive_relation_state(&relation);
        let previous = self
            .relation_states
            .get(key)
            .copied()
            .unwrap_or(ExecutionRelationState::Dormant);
        let source_execution_id = self.active_executions.get(&relation.source_key()).cloned();
        let target_execution_id = self.active_executions.get(&relation.target_key()).cloned();
        if current == ExecutionRelationState::Satisfied {
            if let Some(target_execution_id) = &target_execution_id {
                self.relation_proofs
                    .insert(key.clone(), target_execution_id.clone());
            }
        } else if current.requires_reconciliation() {
            self.relation_fences.insert(key.clone());
        }
        self.relation_states.insert(key.clone(), current);
        if current == previous {
            return Vec::new();
        }
        let mut events = vec![ExecutionEvent::RelationStateChanged {
            relation: relation.clone(),
            previous,
            current,
            source_execution_id: source_execution_id.clone(),
            target_execution_id: target_execution_id.clone(),
        }];
        if current.requires_reconciliation() {
            let reason = relation_reconciliation_reason(&relation, current);
            events.push(ExecutionEvent::RelationReconciliationRequired {
                relation,
                state: current,
                source_execution_id,
                target_execution_id,
                reason,
            });
        }
        events
    }

    /// Derives one v0.1 relation state without mutating execution or Control authority.
    fn derive_relation_state(&self, relation: &RuntimeExecutionRelation) -> ExecutionRelationState {
        let target = self.current_status(&relation.target_key());
        match target {
            Some(ExecutionStatus::Accepted | ExecutionStatus::Running) => {}
            Some(ExecutionStatus::Unknown) => return ExecutionRelationState::Unknown,
            Some(ExecutionStatus::Completed) => {
                let target_execution_id = self.active_executions.get(&relation.target_key());
                return if target_execution_id.is_some_and(|execution_id| {
                    self.relation_proofs.get(&relation.key()) == Some(execution_id)
                }) {
                    ExecutionRelationState::Dormant
                } else {
                    ExecutionRelationState::Unknown
                };
            }
            Some(ExecutionStatus::Failed | ExecutionStatus::Cancelled) => {
                return ExecutionRelationState::Dormant;
            }
            Some(ExecutionStatus::Dispatched) | None => return ExecutionRelationState::Dormant,
        }
        match relation.kind() {
            ExecutionRelationKind::RequiresActive => {
                match self.current_status(&relation.source_key()) {
                    Some(ExecutionStatus::Accepted | ExecutionStatus::Running) => {
                        ExecutionRelationState::Satisfied
                    }
                    Some(ExecutionStatus::Unknown) => ExecutionRelationState::Unknown,
                    Some(
                        ExecutionStatus::Completed
                        | ExecutionStatus::Failed
                        | ExecutionStatus::Cancelled,
                    ) => ExecutionRelationState::Violated,
                    Some(ExecutionStatus::Dispatched) | None => ExecutionRelationState::Pending,
                }
            }
            ExecutionRelationKind::SharedSpatialReference => {
                match self.current_status(&relation.source_key()) {
                    Some(ExecutionStatus::Accepted | ExecutionStatus::Running) => {}
                    Some(ExecutionStatus::Unknown) => return ExecutionRelationState::Unknown,
                    Some(
                        ExecutionStatus::Completed
                        | ExecutionStatus::Failed
                        | ExecutionStatus::Cancelled,
                    ) => return ExecutionRelationState::Violated,
                    Some(ExecutionStatus::Dispatched) | None => {
                        return self.missing_spatial_evidence_state(relation);
                    }
                }
                let ExecutionRelationType::SharedSpatialReference { reference } =
                    relation.relation_type()
                else {
                    return ExecutionRelationState::Unknown;
                };
                let Some(source) = self.current_spatial_evidence(&relation.source_key()) else {
                    return self.missing_spatial_evidence_state(relation);
                };
                let Some(target) = self.current_spatial_evidence(&relation.target_key()) else {
                    return self.missing_spatial_evidence_state(relation);
                };
                if source.selector != *reference.selector()
                    || target.selector != *reference.selector()
                    || source.frame_id != reference.frame_id()
                    || target.frame_id != reference.frame_id()
                {
                    ExecutionRelationState::Violated
                } else {
                    ExecutionRelationState::Satisfied
                }
            }
            ExecutionRelationKind::GroupMemberState
            | ExecutionRelationKind::RelativePose
            | ExecutionRelationKind::RelativeDistance
            | ExecutionRelationKind::StateRequirement
            | ExecutionRelationKind::FreshnessRequirement => ExecutionRelationState::Unknown,
        }
    }

    /// Distinguishes initial proof collection from loss of previously established coordination.
    fn missing_spatial_evidence_state(
        &self,
        relation: &RuntimeExecutionRelation,
    ) -> ExecutionRelationState {
        if self.relation_proofs.contains_key(&relation.key()) {
            ExecutionRelationState::Unknown
        } else {
            ExecutionRelationState::Pending
        }
    }

    /// Resolves the status of the current attempt occupying one logical slot.
    fn current_status(
        &self,
        slot: &(ExecutionGroupId, TaskRef, RoleId),
    ) -> Option<ExecutionStatus> {
        self.active_executions
            .get(slot)
            .and_then(|execution_id| self.execution_status.get(execution_id))
            .copied()
    }

    /// Returns evidence only when it belongs to the current attempt and physical owner.
    fn current_spatial_evidence(
        &self,
        slot: &(ExecutionGroupId, TaskRef, RoleId),
    ) -> Option<&SharedSpatialEvidence> {
        let evidence = self.spatial_evidence.get(slot)?;
        let execution_id = self.active_executions.get(slot)?;
        (execution_id == &evidence.execution_id
            && self.execution_nodes.get(execution_id) == Some(&evidence.node_id))
        .then_some(evidence)
    }
}

/// Returns a typed reconciliation diagnostic without embedding a control algorithm.
fn relation_reconciliation_reason(
    relation: &RuntimeExecutionRelation,
    state: ExecutionRelationState,
) -> String {
    match (relation.kind(), state) {
        (ExecutionRelationKind::RequiresActive, ExecutionRelationState::Violated) => {
            "required source execution is terminal while target remains active".to_string()
        }
        (ExecutionRelationKind::SharedSpatialReference, ExecutionRelationState::Violated) => {
            "current endpoint localization evidence differs from the required shared spatial reference"
                .to_string()
        }
        (ExecutionRelationKind::SharedSpatialReference, ExecutionRelationState::Unknown) => {
            "current endpoint localization evidence cannot prove the required shared spatial reference"
                .to_string()
        }
        (_, ExecutionRelationState::Unknown) => {
            "execution relation cannot prove current physical coordination".to_string()
        }
        (_, ExecutionRelationState::Violated) => {
            "execution relation evidence violates its typed requirement".to_string()
        }
        _ => unreachable!("only violation states request reconciliation"),
    }
}

/// Builds relation Runtime maps while rejecting duplicate or orphan checkpoint entries.
#[allow(clippy::type_complexity)]
pub(crate) fn restore_relation_maps(
    relations: Vec<RuntimeExecutionRelation>,
    states: Vec<RelationStateCheckpoint>,
    fences: Vec<RelationFenceCheckpoint>,
    proofs: Vec<RelationProofCheckpoint>,
) -> Result<
    (
        BTreeMap<RelationKey, RuntimeExecutionRelation>,
        BTreeMap<RelationKey, ExecutionRelationState>,
        BTreeSet<RelationKey>,
        BTreeMap<RelationKey, String>,
    ),
    crate::ExecutionRuntimeError,
> {
    let mut relation_map = BTreeMap::new();
    for relation in relations {
        if relation.source_task_ref.mission_id() != relation.target_task_ref.mission_id() {
            return Err(crate::ExecutionRuntimeError::InvalidCheckpoint(
                "relation endpoints belong to different Missions".to_string(),
            ));
        }
        if relation.kind != relation.relation_type.kind() {
            return Err(crate::ExecutionRuntimeError::InvalidCheckpoint(
                "checkpoint relation kind disagrees with typed relation".to_string(),
            ));
        }
        domain::ExecutionRelationSpec::new_typed(
            relation.relation_id.clone(),
            domain::PlannedExecutionRef::new(
                relation.source_task_ref.task_id().clone(),
                relation.source_role_id.clone(),
            ),
            domain::PlannedExecutionRef::new(
                relation.target_task_ref.task_id().clone(),
                relation.target_role_id.clone(),
            ),
            relation.relation_type.clone(),
        )
        .map_err(|error| {
            crate::ExecutionRuntimeError::InvalidCheckpoint(format!(
                "checkpoint relation contract is invalid: {error}"
            ))
        })?;
        if relation_map.insert(relation.key(), relation).is_some() {
            return Err(crate::ExecutionRuntimeError::InvalidCheckpoint(
                "checkpoint contains duplicate execution relation".to_string(),
            ));
        }
    }
    let mut state_map = BTreeMap::new();
    for state in states {
        let key = (state.group_id, state.relation_id);
        if !relation_map.contains_key(&key) || state_map.insert(key, state.state).is_some() {
            return Err(crate::ExecutionRuntimeError::InvalidCheckpoint(
                "checkpoint contains duplicate or orphan relation state".to_string(),
            ));
        }
    }
    if state_map.len() != relation_map.len() {
        return Err(crate::ExecutionRuntimeError::InvalidCheckpoint(
            "checkpoint relation state coverage is incomplete".to_string(),
        ));
    }
    let mut fence_set = BTreeSet::new();
    for fence in fences {
        let key = (fence.group_id, fence.relation_id);
        if !relation_map.contains_key(&key) || !fence_set.insert(key) {
            return Err(crate::ExecutionRuntimeError::InvalidCheckpoint(
                "checkpoint contains duplicate or orphan relation fence".to_string(),
            ));
        }
    }
    let mut proof_map = BTreeMap::new();
    for proof in proofs {
        let key = (proof.group_id, proof.relation_id);
        if !relation_map.contains_key(&key)
            || proof.target_execution_id.trim().is_empty()
            || proof_map.insert(key, proof.target_execution_id).is_some()
        {
            return Err(crate::ExecutionRuntimeError::InvalidCheckpoint(
                "checkpoint contains invalid relation satisfaction proof".to_string(),
            ));
        }
    }
    Ok((relation_map, state_map, fence_set, proof_map))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{ExecutionRuntimeError, ObservedTaskResult};
    use domain::{
        CapabilityContractRef, CorrelationId, ExecutionCommand, ExecutionIntent,
        ExecutionRelationSpec, ExecutionRelationType, MapId, MapRevisionId, NodeId,
        PlannedExecutionRef, SharedSpatialReference, TaskId,
    };

    /// Builds one committed command for a logical relation endpoint.
    fn command(task_id: &str, role_id: &str, node_id: &str) -> ExecutionCommand {
        ExecutionCommand::new(
            MissionId::new("mission-relation").expect("mission valid"),
            TaskId::new(task_id).expect("task valid"),
            ExecutionGroupId::new("group-relation").expect("group valid"),
            RoleId::new(role_id).expect("role valid"),
            NodeId::new(node_id).expect("node valid"),
            ExecutionIntent::new(
                CapabilityContractRef::new("test", "execute", "v1").expect("contract valid"),
                BTreeMap::new(),
            )
            .expect("intent valid"),
            CorrelationId::new("relation-test").expect("correlation valid"),
        )
    }

    /// Builds the v0.1 relation used by Runtime state-reduction tests.
    fn relation() -> ExecutionRelationSpec {
        ExecutionRelationSpec::new(
            ExecutionRelationId::new("safety-guards-navigation").expect("relation valid"),
            PlannedExecutionRef::new(
                TaskId::new("observe").expect("task valid"),
                RoleId::new("safety").expect("role valid"),
            ),
            PlannedExecutionRef::new(
                TaskId::new("navigate").expect("task valid"),
                RoleId::new("navigator").expect("role valid"),
            ),
            ExecutionRelationKind::RequiresActive,
        )
        .expect("relation valid")
    }

    /// Builds a typed shared-map/frame relation over the same logical endpoint pair.
    fn spatial_relation() -> ExecutionRelationSpec {
        ExecutionRelationSpec::new_typed(
            ExecutionRelationId::new("shared-localization").expect("relation valid"),
            PlannedExecutionRef::new(
                TaskId::new("observe").expect("task valid"),
                RoleId::new("safety").expect("role valid"),
            ),
            PlannedExecutionRef::new(
                TaskId::new("navigate").expect("task valid"),
                RoleId::new("navigator").expect("role valid"),
            ),
            ExecutionRelationType::SharedSpatialReference {
                reference: SharedSpatialReference::new(selector("r1"), "map")
                    .expect("spatial reference valid"),
            },
        )
        .expect("typed relation valid")
    }

    /// Builds one immutable map selector for relation evidence tests.
    fn selector(revision: &str) -> MapRevisionSelector {
        MapRevisionSelector::new(
            MapId::new("building-a").expect("map id valid"),
            MapRevisionId::new(revision).expect("revision id valid"),
        )
    }

    /// Builds strong spatial evidence for one dispatched command and attempt.
    fn spatial_evidence(
        command: &ExecutionCommand,
        execution_id: &str,
        revision: &str,
        frame_id: &str,
        received_at: u64,
    ) -> SharedSpatialEvidence {
        SharedSpatialEvidence {
            group_id: command.group_id().clone(),
            task_ref: command.task_ref().clone(),
            role_id: command.role_id().clone(),
            execution_id: execution_id.to_string(),
            node_id: command.node_id().clone(),
            selector: selector(revision),
            frame_id: frame_id.to_string(),
            received_at: TimestampMs::new(received_at),
        }
    }

    /// Strong map/frame evidence drives Pending, Satisfied, and Violated relation states.
    #[test]
    fn shared_spatial_relation_reduces_current_attempt_evidence() {
        let mut runtime = RuntimeExecutionManager::new();
        let group_id = ExecutionGroupId::new("group-relation").expect("group valid");
        runtime
            .register_relations(
                &group_id,
                &MissionId::new("mission-relation").expect("mission valid"),
                &[spatial_relation()],
            )
            .expect("relation registers");
        let source = command("observe", "safety", "cane-a");
        let target = command("navigate", "navigator", "dog-a");
        runtime
            .record_dispatched("attempt-source".to_string(), source.clone(), Vec::new())
            .expect("source dispatch records");
        runtime
            .record_dispatched("attempt-target".to_string(), target.clone(), Vec::new())
            .expect("target dispatch records");
        runtime
            .observe_execution(
                "attempt-target",
                target.node_id().clone(),
                1,
                ExecutionStatus::Running,
                "",
            )
            .expect("target running records");
        assert_eq!(
            runtime.relation_snapshots(&group_id)[0].state(),
            ExecutionRelationState::Pending
        );
        assert!(!runtime.relation_snapshots(&group_id)[0].reconciliation_required());
        runtime
            .observe_execution(
                "attempt-source",
                source.node_id().clone(),
                1,
                ExecutionStatus::Running,
                "",
            )
            .expect("source running records");

        runtime
            .observe_shared_spatial_evidence(spatial_evidence(
                &source,
                "attempt-source",
                "r1",
                "map",
                10,
            ))
            .expect("source localization records");
        let satisfied = runtime
            .observe_shared_spatial_evidence(spatial_evidence(
                &target,
                "attempt-target",
                "r1",
                "map",
                11,
            ))
            .expect("target localization records");
        assert!(satisfied.iter().any(|event| matches!(
            event,
            ExecutionEvent::RelationStateChanged {
                current: ExecutionRelationState::Satisfied,
                ..
            }
        )));
        assert!(!runtime.relation_snapshots(&group_id)[0].reconciliation_required());

        let violated = runtime
            .observe_shared_spatial_evidence(spatial_evidence(
                &source,
                "attempt-source",
                "r2",
                "map",
                12,
            ))
            .expect("newer conflicting localization records");
        assert!(violated.iter().any(|event| matches!(
            event,
            ExecutionEvent::RelationReconciliationRequired {
                state: ExecutionRelationState::Violated,
                ..
            }
        )));
    }

    /// Rebind removes prior-attempt spatial evidence and rejects its later delivery.
    #[test]
    fn shared_spatial_evidence_follows_rebind_attempt_identity() {
        let mut runtime = RuntimeExecutionManager::new();
        let source = command("observe", "safety", "cane-a");
        runtime
            .record_dispatched("attempt-source-1".to_string(), source.clone(), Vec::new())
            .expect("first source dispatch records");
        runtime
            .observe_shared_spatial_evidence(spatial_evidence(
                &source,
                "attempt-source-1",
                "r1",
                "map",
                10,
            ))
            .expect("first attempt evidence records");
        let replacement = command("observe", "safety", "cane-b");
        runtime
            .record_dispatched(
                "attempt-source-2".to_string(),
                replacement.clone(),
                Vec::new(),
            )
            .expect("replacement dispatch records");

        assert!(
            runtime
                .shared_spatial_evidence(
                    replacement.group_id(),
                    replacement.task_ref(),
                    replacement.role_id()
                )
                .is_none()
        );
        assert!(matches!(
            runtime.observe_shared_spatial_evidence(spatial_evidence(
                &source,
                "attempt-source-1",
                "r1",
                "map",
                11,
            )),
            Err(ExecutionRuntimeError::ReconciliationRequired(_))
        ));
    }

    /// A relation follows the current logical-slot attempt and ignores old-attempt late facts.
    #[test]
    fn relation_tracks_rebind_without_node_identity() {
        let mut runtime = RuntimeExecutionManager::new();
        let group_id = ExecutionGroupId::new("group-relation").expect("group valid");
        let mission_id = MissionId::new("mission-relation").expect("mission valid");
        runtime
            .register_relations(&group_id, &mission_id, &[relation()])
            .expect("relation registers");
        let source_old = command("observe", "safety", "cane-a");
        let target = command("navigate", "navigator", "dog-a");
        runtime
            .record_dispatched(
                "attempt-source-1".to_string(),
                source_old.clone(),
                Vec::new(),
            )
            .expect("source dispatch records");
        runtime
            .record_dispatched("attempt-target-1".to_string(), target.clone(), Vec::new())
            .expect("target dispatch records");

        let pending = runtime
            .observe_execution(
                "attempt-target-1",
                target.node_id().clone(),
                1,
                ExecutionStatus::Accepted,
                "",
            )
            .expect("target acceptance records");
        assert!(pending.iter().any(|event| matches!(
            event,
            ExecutionEvent::RelationStateChanged {
                current: ExecutionRelationState::Pending,
                ..
            }
        )));
        runtime
            .observe_execution(
                "attempt-source-1",
                source_old.node_id().clone(),
                1,
                ExecutionStatus::Running,
                "",
            )
            .expect("source running records");
        assert_eq!(
            runtime.relation_snapshots(&group_id)[0].state(),
            ExecutionRelationState::Satisfied
        );

        let unknown = runtime
            .observe_execution(
                "attempt-source-1",
                source_old.node_id().clone(),
                2,
                ExecutionStatus::Unknown,
                "source connection lost",
            )
            .expect("source ambiguity records");
        assert!(unknown.iter().any(|event| matches!(
            event,
            ExecutionEvent::RelationReconciliationRequired {
                state: ExecutionRelationState::Unknown,
                ..
            }
        )));

        let source_new = command("observe", "safety", "cane-b");
        runtime
            .record_dispatched(
                "attempt-source-2".to_string(),
                source_new.clone(),
                Vec::new(),
            )
            .expect("replacement attempt dispatches");
        runtime
            .observe_execution(
                "attempt-source-2",
                source_new.node_id().clone(),
                1,
                ExecutionStatus::Accepted,
                "",
            )
            .expect("replacement acceptance records");
        let snapshot = &runtime.relation_snapshots(&group_id)[0];
        assert_eq!(snapshot.state(), ExecutionRelationState::Satisfied);
        assert!(snapshot.reconciliation_required());
        assert_eq!(snapshot.source_execution_id(), Some("attempt-source-2"));

        runtime
            .acknowledge_relation_reconciliation(
                &group_id,
                &ExecutionRelationId::new("safety-guards-navigation").expect("relation valid"),
            )
            .expect("Control recovery explicitly acknowledges the repaired relation");
        assert!(!runtime.relation_snapshots(&group_id)[0].reconciliation_required());

        let late_old_fact = runtime
            .observe_execution(
                "attempt-source-1",
                source_old.node_id().clone(),
                3,
                ExecutionStatus::Running,
                "late old-attempt fact",
            )
            .expect("late old attempt remains valid history");
        assert!(
            !late_old_fact
                .iter()
                .any(|event| matches!(event, ExecutionEvent::RelationStateChanged { .. }))
        );
        assert_eq!(
            runtime.relation_snapshots(&group_id)[0].source_execution_id(),
            Some("attempt-source-2")
        );

        runtime
            .observe_execution(
                "attempt-target-1",
                target.node_id().clone(),
                2,
                ExecutionStatus::Completed,
                "",
            )
            .expect("target completion records");
        assert_eq!(
            runtime.task_result(
                &group_id,
                target.task_ref(),
                std::iter::once(target.role_id())
            ),
            Some(ObservedTaskResult::Succeeded)
        );
    }

    /// Restart turns a previously satisfied nonterminal relation into Unknown and fences success.
    #[test]
    fn relation_restore_is_conservative() {
        let mut runtime = RuntimeExecutionManager::new();
        let group_id = ExecutionGroupId::new("group-relation").expect("group valid");
        runtime
            .register_relations(
                &group_id,
                &MissionId::new("mission-relation").expect("mission valid"),
                &[relation()],
            )
            .expect("relation registers");
        let source = command("observe", "safety", "cane-a");
        let target = command("navigate", "navigator", "dog-a");
        runtime
            .record_dispatched("attempt-source".to_string(), source.clone(), Vec::new())
            .expect("source dispatch records");
        runtime
            .record_dispatched("attempt-target".to_string(), target.clone(), Vec::new())
            .expect("target dispatch records");
        runtime
            .observe_execution(
                "attempt-source",
                source.node_id().clone(),
                1,
                ExecutionStatus::Running,
                "",
            )
            .expect("source running records");
        runtime
            .observe_execution(
                "attempt-target",
                target.node_id().clone(),
                1,
                ExecutionStatus::Running,
                "",
            )
            .expect("target running records");

        let restored = RuntimeExecutionManager::restore(runtime.checkpoint())
            .expect("relation checkpoint restores");
        let snapshot = &restored.relation_snapshots(&group_id)[0];
        assert_eq!(snapshot.state(), ExecutionRelationState::Unknown);
        assert!(snapshot.reconciliation_required());
        assert!(matches!(
            restored.validate_dispatch("attempt-target", &target, &[]),
            Err(ExecutionRuntimeError::ReconciliationRequired(_))
        ));
    }

    /// A target cannot complete successfully without evidence that its source became active.
    #[test]
    fn target_completion_requires_relation_satisfaction_proof() {
        let mut runtime = RuntimeExecutionManager::new();
        let group_id = ExecutionGroupId::new("group-relation").expect("group valid");
        runtime
            .register_relations(
                &group_id,
                &MissionId::new("mission-relation").expect("mission valid"),
                &[relation()],
            )
            .expect("relation registers");
        let target = command("navigate", "navigator", "dog-a");
        runtime
            .record_dispatched("attempt-target".to_string(), target.clone(), Vec::new())
            .expect("target dispatch records");
        runtime
            .observe_execution(
                "attempt-target",
                target.node_id().clone(),
                1,
                ExecutionStatus::Accepted,
                "",
            )
            .expect("target acceptance records");
        let completion = runtime
            .observe_execution(
                "attempt-target",
                target.node_id().clone(),
                2,
                ExecutionStatus::Completed,
                "",
            )
            .expect("target completion records");
        assert!(completion.iter().any(|event| matches!(
            event,
            ExecutionEvent::RelationReconciliationRequired {
                state: ExecutionRelationState::Unknown,
                ..
            }
        )));
        assert_eq!(
            runtime.task_result(
                &group_id,
                target.task_ref(),
                std::iter::once(target.role_id())
            ),
            None
        );
    }

    /// A failed target ends the relation window without manufacturing a second ambiguity.
    #[test]
    fn target_failure_remains_a_task_failure_without_relation_unknown() {
        let mut runtime = RuntimeExecutionManager::new();
        let group_id = ExecutionGroupId::new("group-relation").expect("group valid");
        runtime
            .register_relations(
                &group_id,
                &MissionId::new("mission-relation").expect("mission valid"),
                &[relation()],
            )
            .expect("relation registers");
        let target = command("navigate", "navigator", "dog-a");
        runtime
            .record_dispatched("attempt-target".to_string(), target.clone(), Vec::new())
            .expect("target dispatch records");
        runtime
            .observe_execution(
                "attempt-target",
                target.node_id().clone(),
                1,
                ExecutionStatus::Accepted,
                "",
            )
            .expect("target acceptance records");
        let failure = runtime
            .observe_execution(
                "attempt-target",
                target.node_id().clone(),
                2,
                ExecutionStatus::Failed,
                "local execution failed",
            )
            .expect("target failure records");
        assert!(
            !failure.iter().any(|event| matches!(
                event,
                ExecutionEvent::RelationReconciliationRequired { .. }
            ))
        );
        assert_eq!(
            runtime.relation_snapshots(&group_id)[0].state(),
            ExecutionRelationState::Dormant
        );
        assert_eq!(
            runtime.task_result(
                &group_id,
                target.task_ref(),
                std::iter::once(target.role_id())
            ),
            Some(ObservedTaskResult::Failed)
        );
    }
}
