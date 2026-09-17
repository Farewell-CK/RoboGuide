//! Capability candidate models and matching policy.

use crate::{ControlError, ControlPlane};
use domain::{
    CapabilityRequirement, CorrelationId, EventPayload, MissionPlan, NodeId, OperationRef, RoleId,
    TaskId, TaskRef, TaskRequirement, TimestampMs,
};
use ports::{EventSink, SharedNodeStateReader};
use std::collections::BTreeMap;

/// Candidate node identifiers for one task role.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RoleCandidates {
    /// Role for which the candidate nodes were produced.
    role_id: RoleId,
    /// Nodes that can satisfy the role in deterministic order.
    node_ids: Vec<NodeId>,
}

impl RoleCandidates {
    /// Creates a deterministic candidate list for one role.
    pub fn new(role_id: RoleId, node_ids: Vec<NodeId>) -> Self {
        Self { role_id, node_ids }
    }

    /// Returns the role being matched.
    pub fn role_id(&self) -> &RoleId {
        &self.role_id
    }

    /// Returns candidate nodes in stable registration order.
    pub fn node_ids(&self) -> &[NodeId] {
        &self.node_ids
    }
}

/// The complete Candidate Set produced by Capability Matching.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CandidateSet {
    /// Mission-scoped task for which matching was performed.
    task_ref: TaskRef,
    /// Candidate nodes grouped by required role.
    roles: Vec<RoleCandidates>,
    /// Exact semantic operation associated with each normalized Mission role.
    role_operations: BTreeMap<RoleId, OperationRef>,
    /// Mission actor behind each role, when the role declares one.
    role_actors: BTreeMap<RoleId, domain::ActorId>,
    /// Context-scoped actor groups requiring distinct physical entities.
    distinct_actor_groups: Vec<std::collections::BTreeSet<domain::ActorId>>,
    /// Current physical entity routed by each candidate Node.
    candidate_entities: BTreeMap<NodeId, domain::PhysicalEntityId>,
}

impl CandidateSet {
    /// Creates a candidate set for one task.
    pub fn new(task_ref: TaskRef, roles: Vec<RoleCandidates>) -> Self {
        Self {
            task_ref,
            roles,
            role_operations: BTreeMap::new(),
            role_actors: BTreeMap::new(),
            distinct_actor_groups: Vec::new(),
            candidate_entities: BTreeMap::new(),
        }
    }

    /// Attaches normalized role operations after operation-aware matching succeeds.
    fn with_role_operations(mut self, role_operations: BTreeMap<RoleId, OperationRef>) -> Self {
        self.role_operations = role_operations;
        self
    }

    /// Attaches mission actor binding metadata for scheduler cardinality.
    pub(crate) fn with_actor_binding_metadata(
        mut self,
        role_actors: BTreeMap<RoleId, domain::ActorId>,
        distinct_actor_groups: Vec<std::collections::BTreeSet<domain::ActorId>>,
        candidate_entities: BTreeMap<NodeId, domain::PhysicalEntityId>,
    ) -> Self {
        self.role_actors = role_actors;
        self.distinct_actor_groups = distinct_actor_groups;
        self.candidate_entities = candidate_entities;
        self
    }

    /// Returns the mission actor behind one role, when declared.
    pub fn actor_for_role(&self, role_id: &RoleId) -> Option<&domain::ActorId> {
        self.role_actors.get(role_id)
    }

    /// Returns the distinct-occupancy actor groups carried by this set.
    pub fn distinct_actor_groups(&self) -> &[std::collections::BTreeSet<domain::ActorId>] {
        &self.distinct_actor_groups
    }

    /// Returns the physical entity routed by one candidate Node in this decision snapshot.
    pub fn physical_entity_for_node(&self, node_id: &NodeId) -> Option<&domain::PhysicalEntityId> {
        self.candidate_entities.get(node_id)
    }

    /// Returns the complete mission-scoped task identity.
    pub const fn task_ref(&self) -> &TaskRef {
        &self.task_ref
    }

    /// Returns the matched task identity.
    pub const fn task_id(&self) -> &TaskId {
        self.task_ref.task_id()
    }

    /// Returns role-level candidates.
    pub fn roles(&self) -> &[RoleCandidates] {
        &self.roles
    }

    /// Returns candidates for one role, if that role was included.
    pub fn for_role(&self, role_id: &RoleId) -> Option<&RoleCandidates> {
        self.roles.iter().find(|role| role.role_id() == role_id)
    }

    /// Returns the exact operation whose support was checked for one normalized Mission role.
    pub fn operation_for_role(&self, role_id: &RoleId) -> Option<&OperationRef> {
        self.role_operations.get(role_id)
    }

    /// Returns all operation-support constraints carried into Proposal and Commit validation.
    pub(crate) const fn role_operations(&self) -> &BTreeMap<RoleId, OperationRef> {
        &self.role_operations
    }
}

impl ControlPlane {
    /// Matches a task while reusing an existing mission actor binding as a singleton candidate.
    pub fn match_capabilities_with_actor_bindings<S: SharedNodeStateReader, E: EventSink>(
        &self,
        state: &S,
        requirement: &TaskRequirement,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<CandidateSet, ControlError> {
        let mut roles = Vec::with_capacity(requirement.roles().len());
        for role in requirement.roles() {
            if let Some(actor_id) = role.actor_id()
                && let Some(binding) = self.actor_binding(requirement.mission_id(), actor_id)
            {
                if !self.node_is_eligible_for_role(state, binding.node_id(), role, timestamp) {
                    return Err(ControlError::ActorBindingRequiresReconciliation {
                        mission_id: requirement.mission_id().clone(),
                        actor_id: actor_id.clone(),
                        node_id: binding.node_id().clone(),
                    });
                }
                roles.push(RoleCandidates::new(
                    role.role_id().clone(),
                    vec![binding.node_id().clone()],
                ));
                continue;
            }
            if let Some(actor_id) = role.actor_id()
                && let Some(entity_id) = self
                    .mission_binding_semantics(requirement.mission_id())
                    .and_then(|semantics| semantics.grounding(actor_id))
                && self.physical_entity_node(entity_id).is_none()
            {
                return Err(ControlError::ActorGroundingUnresolved {
                    mission_id: requirement.mission_id().clone(),
                    actor_id: actor_id.clone(),
                    entity_id: entity_id.clone(),
                });
            }
            if let Some(actor_id) = role.actor_id()
                && let Some(node_id) = self.grounded_actor_node(requirement.mission_id(), actor_id)
            {
                if !self.node_is_eligible_for_role(state, &node_id, role, timestamp) {
                    return Err(ControlError::ActorPlacementConstraintUnsatisfied {
                        mission_id: requirement.mission_id().clone(),
                        actor_id: actor_id.clone(),
                        node_id,
                    });
                }
                roles.push(RoleCandidates::new(role.role_id().clone(), vec![node_id]));
                continue;
            }
            if let Some(actor_id) = role.actor_id()
                && let Some(constraint) =
                    self.actor_node_constraint(requirement.mission_id(), actor_id)
            {
                if !self.node_is_eligible_for_role(state, constraint.node_id(), role, timestamp) {
                    return Err(ControlError::ActorPlacementConstraintUnsatisfied {
                        mission_id: requirement.mission_id().clone(),
                        actor_id: actor_id.clone(),
                        node_id: constraint.node_id().clone(),
                    });
                }
                roles.push(RoleCandidates::new(
                    role.role_id().clone(),
                    vec![constraint.node_id().clone()],
                ));
                continue;
            }
            let node_ids = state
                .nodes()
                .into_iter()
                .filter(|snapshot| {
                    self.node_is_eligible_for_role(state, snapshot.node_id(), role, timestamp)
                })
                .map(|snapshot| snapshot.node_id().clone())
                .collect::<Vec<_>>();
            if node_ids.is_empty() {
                return Err(ControlError::NoCandidate(role.role_id().clone()));
            }
            roles.push(RoleCandidates::new(role.role_id().clone(), node_ids));
        }
        let candidates = CandidateSet::new(requirement.task_ref().clone(), roles);
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::CandidatesMatched {
                task_ref: requirement.task_ref().clone(),
            },
        );
        Ok(candidates)
    }

    /// Matches a task while constraining every first-use actor to nodes capable of its whole plan.
    pub fn match_capabilities_for_mission<S: SharedNodeStateReader, E: EventSink>(
        &self,
        state: &S,
        mission: &MissionPlan,
        requirement: &TaskRequirement,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<CandidateSet, ControlError> {
        if mission.goal().mission_id() != requirement.mission_id() {
            return Err(ControlError::InvalidProposal(
                "mission plan and task requirement belong to different missions".to_string(),
            ));
        }
        let task = mission
            .task_graph()
            .tasks()
            .iter()
            .find(|task| task.requirement().task_ref() == requirement.task_ref())
            .ok_or_else(|| {
                ControlError::InvalidProposal(
                    "task requirement is absent from the accepted MissionPlan".to_string(),
                )
            })?;
        let role_operations = requirement
            .roles()
            .iter()
            .map(|role| {
                task.execution_intent(role.role_id())
                    .map(|intent| (role.role_id().clone(), intent.operation().clone()))
                    .ok_or_else(|| {
                        ControlError::InvalidProposal(format!(
                            "MissionPlan lacks ExecutionIntent for role {}",
                            role.role_id()
                        ))
                    })
            })
            .collect::<Result<BTreeMap<_, _>, _>>()?;
        let actor_requirements = mission.actor_requirements();
        let context_id = task.continuity().context_id();
        let mut candidates = self.match_capabilities_with_actor_bindings(
            state,
            requirement,
            timestamp,
            correlation_id,
            events,
        )?;
        for role in requirement.roles() {
            let operation = role_operations
                .get(role.role_id())
                .expect("every normalized role operation was collected above");
            let role_candidates = candidates
                .roles
                .iter_mut()
                .find(|candidate| candidate.role_id() == role.role_id())
                .expect("candidate exists");
            role_candidates.node_ids.retain(|node_id| {
                self.node_is_eligible_for_role_operation(state, node_id, role, operation, timestamp)
            });
            if role_candidates.node_ids.is_empty() {
                return Err(ControlError::NoCandidate(role.role_id().clone()));
            }
            if let Some(actor_id) = role.actor_id() {
                self.retain_distinct_binding_candidates(
                    requirement.mission_id(),
                    context_id,
                    actor_id,
                    &mut role_candidates.node_ids,
                )?;
            }
            if role.actor_id().is_none()
                || self
                    .actor_binding(requirement.mission_id(), role.actor_id().expect("checked"))
                    .is_some()
            {
                continue;
            }
            let actor = role.actor_id().expect("checked");
            let requirements = actor_requirements.get(actor).ok_or_else(|| {
                ControlError::InvalidProposal(format!("actor {actor} is absent from MissionPlan"))
            })?;
            let role_candidates = candidates
                .roles
                .iter_mut()
                .find(|candidate| candidate.role_id() == role.role_id())
                .expect("candidate exists");
            role_candidates.node_ids.retain(|node_id| {
                state.node(node_id).is_some_and(|snapshot| {
                    requirements.iter().all(|requirement| {
                        node_supports_requirement(snapshot.registration(), requirement)
                    })
                })
            });
            if role_candidates.node_ids.is_empty() {
                return Err(ControlError::NoCandidate(role.role_id().clone()));
            }
        }
        let role_actors = requirement
            .roles()
            .iter()
            .filter_map(|role| {
                role.actor_id()
                    .map(|actor| (role.role_id().clone(), actor.clone()))
            })
            .collect::<BTreeMap<_, _>>();
        let binding_semantics = self.mission_binding_semantics(requirement.mission_id());
        let distinct_actor_groups = binding_semantics
            .map(|semantics| semantics.distinct_actor_groups(context_id))
            .unwrap_or_default();
        // Deployment topology supplies evidence only after the Mission opts into physical
        // binding. Carry the same applicability used by Commit and Bind into scheduling.
        let candidate_entities: BTreeMap<NodeId, domain::PhysicalEntityId> = binding_semantics
            .filter(|semantics| semantics.requires_physical_entities())
            .and(self.physical_entity_registry())
            .map(|registry| {
                candidates
                    .roles()
                    .iter()
                    .flat_map(RoleCandidates::node_ids)
                    .filter_map(|node_id| {
                        registry
                            .entity_for_node(node_id)
                            .map(|entry| (node_id.clone(), entry.entity_id().clone()))
                    })
                    .collect()
            })
            .unwrap_or_default();
        if !distinct_actor_groups.is_empty()
            && candidates
                .roles()
                .iter()
                .flat_map(RoleCandidates::node_ids)
                .any(|node_id| !candidate_entities.contains_key(node_id))
        {
            return Err(ControlError::InvalidProposal(
                "physical entity registry does not cover every constrained candidate Node"
                    .to_string(),
            ));
        }
        Ok(candidates
            .with_role_operations(role_operations)
            .with_actor_binding_metadata(role_actors, distinct_actor_groups, candidate_entities))
    }

    /// Matches every task role against currently eligible node facts.
    pub fn match_capabilities<S: SharedNodeStateReader, E: EventSink>(
        &self,
        state: &S,
        requirement: &TaskRequirement,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<CandidateSet, ControlError> {
        let mut roles = Vec::with_capacity(requirement.roles().len());
        for role in requirement.roles() {
            let node_ids = state
                .nodes()
                .into_iter()
                .filter(|snapshot| {
                    self.node_is_eligible_for_role(state, snapshot.node_id(), role, timestamp)
                })
                .map(|snapshot| snapshot.node_id().clone())
                .collect::<Vec<_>>();
            if node_ids.is_empty() {
                return Err(ControlError::NoCandidate(role.role_id().clone()));
            }
            roles.push(RoleCandidates::new(role.role_id().clone(), node_ids));
        }

        let candidates = CandidateSet::new(requirement.task_ref().clone(), roles);
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::CandidatesMatched {
                task_ref: requirement.task_ref().clone(),
            },
        );
        Ok(candidates)
    }
}

/// Checks one node's exact capability readiness and feasibility envelope.
fn node_supports_requirement(
    registration: &domain::NodeRegistration,
    requirement: &CapabilityRequirement,
) -> bool {
    registration.capability_requirement_is_available(requirement)
}
