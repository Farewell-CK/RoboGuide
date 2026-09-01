//! Runtime-owned live state for Mission execution coordination relations.

use crate::{ExecutionEvent, ExecutionStatus, RuntimeExecutionManager};
use domain::{
    ExecutionGroupId, ExecutionRelationId, ExecutionRelationKind, ExecutionRelationSpec,
    ExecutionRelationState, MissionId, RoleId, TaskRef,
};
use std::collections::{BTreeMap, BTreeSet};

/// Stable Runtime key for one Mission relation inside its Execution Group.
pub(crate) type RelationKey = (ExecutionGroupId, ExecutionRelationId);

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
    /// Target attempt that was observed while its source was active.
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
    /// Registers exact Mission relation specifications without selecting physical endpoints.
    pub fn register_relations(
        &mut self,
        group_id: &ExecutionGroupId,
        mission_id: &MissionId,
        specifications: &[ExecutionRelationSpec],
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

    /// Confirms Runtime retained exactly the relations in one restored MissionPlan.
    pub fn validate_relations(
        &self,
        group_id: &ExecutionGroupId,
        mission_id: &MissionId,
        specifications: &[ExecutionRelationSpec],
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
            self.relation_fences.remove(key);
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
            events.push(ExecutionEvent::RelationReconciliationRequired {
                relation,
                state: current,
                source_execution_id,
                target_execution_id,
                reason: match current {
                    ExecutionRelationState::Violated => {
                        "required source execution is terminal while target remains active"
                            .to_string()
                    }
                    ExecutionRelationState::Unknown => {
                        "execution relation cannot prove current physical coordination".to_string()
                    }
                    _ => unreachable!("only violation states request reconciliation"),
                },
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
        ExecutionRelationSpec, NodeId, PlannedExecutionRef, TaskId,
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
        assert!(!snapshot.reconciliation_required());
        assert_eq!(snapshot.source_execution_id(), Some("attempt-source-2"));

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
