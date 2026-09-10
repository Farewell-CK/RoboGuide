//! Execution Relation registration, evidence reduction, and checkpoint validation.

use super::*;

impl RuntimeExecutionManager {
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
