//! Capability candidate models and matching policy.

use crate::{ControlError, ControlPlane};
use domain::{
    CapabilityContractRef, CapabilityKind, CorrelationId, EventPayload, MissionPlan, NodeId,
    RoleId, TaskId, TaskRef, TaskRequirement, TimestampMs,
};
use ports::{EventSink, SharedNodeStateReader};

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
}

impl CandidateSet {
    /// Creates a candidate set for one task.
    pub fn new(task_ref: TaskRef, roles: Vec<RoleCandidates>) -> Self {
        Self { task_ref, roles }
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
        let actor_requirements = mission.actor_requirements();
        let mut candidates = self.match_capabilities_with_actor_bindings(
            state,
            requirement,
            timestamp,
            correlation_id,
            events,
        )?;
        for role in requirement.roles() {
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
                    requirements.iter().all(|(capability, contract)| {
                        node_supports_contract(snapshot.registration(), *capability, contract)
                    })
                })
            });
            if role_candidates.node_ids.is_empty() {
                return Err(ControlError::NoCandidate(role.role_id().clone()));
            }
        }
        Ok(candidates)
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

/// Checks one node's advertised capability and exact contract pair.
fn node_supports_contract(
    registration: &domain::NodeRegistration,
    capability: CapabilityKind,
    contract: &CapabilityContractRef,
) -> bool {
    registration
        .capabilities()
        .iter()
        .any(|item| item.kind() == capability && item.is_available())
        && registration
            .supported_contracts()
            .iter()
            .any(|supported| supported == contract)
}
