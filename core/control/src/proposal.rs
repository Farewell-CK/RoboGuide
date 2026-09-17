//! Assignment proposal model and validation boundary.

use crate::{CandidateSet, ControlError, ControlPlane};
use domain::{
    CorrelationId, EventPayload, OperationRef, RoleAssignment, RoleId, RoleRequirement, TaskId,
    TaskRef, TaskRequirement, TimestampMs,
};
use ports::{EventSink, SharedNodeStateReader};
use std::collections::{BTreeMap, BTreeSet};

/// A Scheduler selection accepted for validation but not yet committed.
#[derive(Debug, Clone, PartialEq)]
pub struct AssignmentProposal {
    /// Mission-scoped task represented by this proposal.
    task_ref: TaskRef,
    /// Proposed node and resource assignments by role.
    assignments: Vec<RoleAssignment>,
    /// Exact operations revalidated for normalized Mission roles.
    role_operations: BTreeMap<RoleId, OperationRef>,
    /// Exact Role requirements revalidated before operation-aware Commit.
    role_requirements: BTreeMap<RoleId, RoleRequirement>,
    /// Physical entity selected for each role under v0.8 semantics.
    role_physical_entities: BTreeMap<RoleId, domain::PhysicalEntityId>,
}

impl AssignmentProposal {
    /// Creates a proposal after Control validates its role assignments.
    fn new(
        task_ref: TaskRef,
        assignments: Vec<RoleAssignment>,
        role_operations: BTreeMap<RoleId, OperationRef>,
        role_requirements: BTreeMap<RoleId, RoleRequirement>,
        role_physical_entities: BTreeMap<RoleId, domain::PhysicalEntityId>,
    ) -> Self {
        Self {
            task_ref,
            assignments,
            role_operations,
            role_requirements,
            role_physical_entities,
        }
    }

    /// Returns the complete mission-scoped task identity.
    pub const fn task_ref(&self) -> &TaskRef {
        &self.task_ref
    }

    /// Returns the proposed task identity.
    pub const fn task_id(&self) -> &TaskId {
        self.task_ref.task_id()
    }

    /// Returns all proposed role assignments.
    pub fn assignments(&self) -> &[RoleAssignment] {
        &self.assignments
    }

    /// Returns the exact normalized operation associated with one proposed role.
    pub fn operation_for_role(&self, role_id: &RoleId) -> Option<&OperationRef> {
        self.role_operations.get(role_id)
    }

    /// Returns the exact Role requirement retained for operation-aware Commit validation.
    pub(crate) fn requirement_for_role(&self, role_id: &RoleId) -> Option<&RoleRequirement> {
        self.role_requirements.get(role_id)
    }

    /// Returns whether this normalized proposal requires operation-aware Commit validation.
    pub(crate) fn requires_operation_validation(&self) -> bool {
        !self.role_operations.is_empty()
    }

    /// Returns the physical entity selected for one Role, when v0.8 semantics require one.
    pub(crate) fn physical_entity_for_role(
        &self,
        role_id: &RoleId,
    ) -> Option<&domain::PhysicalEntityId> {
        self.role_physical_entities.get(role_id)
    }

    /// Returns every exact Role-to-physical-entity selection retained by the proposal.
    pub(crate) const fn role_physical_entities(
        &self,
    ) -> &BTreeMap<RoleId, domain::PhysicalEntityId> {
        &self.role_physical_entities
    }
}

impl ControlPlane {
    /// Validates Scheduler assignments without committing resources.
    #[allow(clippy::too_many_arguments)]
    pub fn propose<S: SharedNodeStateReader, E: EventSink>(
        &self,
        state: &S,
        requirement: &TaskRequirement,
        candidates: &CandidateSet,
        assignments: Vec<RoleAssignment>,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<AssignmentProposal, ControlError> {
        if candidates.task_ref() != requirement.task_ref() {
            return Err(ControlError::InvalidProposal(
                "candidate set belongs to another task".to_string(),
            ));
        }
        if assignments.len() != requirement.roles().len() {
            return Err(ControlError::InvalidProposal(
                "proposal must assign every role exactly once".to_string(),
            ));
        }

        let mut proposed_resources = BTreeSet::new();
        for role in requirement.roles() {
            let assignment = assignments
                .iter()
                .find(|assignment| assignment.role_id() == role.role_id())
                .ok_or_else(|| {
                    ControlError::InvalidProposal(format!("missing role {}", role.role_id()))
                })?;
            let role_candidates = candidates.for_role(role.role_id()).ok_or_else(|| {
                ControlError::InvalidProposal(format!(
                    "missing candidates for role {}",
                    role.role_id()
                ))
            })?;
            if !role_candidates.node_ids().contains(assignment.node_id()) {
                return Err(ControlError::InvalidProposal(format!(
                    "node {} is not a candidate for role {}",
                    assignment.node_id(),
                    role.role_id()
                )));
            }
            let node = state
                .node(assignment.node_id())
                .ok_or_else(|| ControlError::UnknownNode(assignment.node_id().clone()))?;
            if !self.node_is_eligible_for_role(state, assignment.node_id(), role, timestamp) {
                return Err(ControlError::InvalidProposal(format!(
                    "node {} is no longer eligible for role {}",
                    assignment.node_id(),
                    role.role_id()
                )));
            }
            if let Some(operation) = candidates.operation_for_role(role.role_id())
                && !node.registration().supports_operation(operation)
            {
                return Err(ControlError::InvalidProposal(format!(
                    "node {} no longer supports operation {} for role {}",
                    assignment.node_id(),
                    operation,
                    role.role_id()
                )));
            }
            let requirements = role.resource_requirements();
            if assignment.resource_ids().len() != requirements.len()
                || assignment
                    .resource_ids()
                    .iter()
                    .zip(requirements.iter())
                    .any(|(resource_id, required)| {
                        !node.registration().resources().iter().any(|resource| {
                            resource.id() == resource_id
                                && resource.kind() == required.kind()
                                && resource.capacity() >= required.units()
                        })
                    })
            {
                return Err(ControlError::InvalidProposal(format!(
                    "role {} resources do not exactly satisfy its declared kinds and capacities",
                    role.role_id()
                )));
            }
            for resource_id in assignment.resource_ids() {
                if !proposed_resources.insert(resource_id) {
                    return Err(ControlError::InvalidProposal(format!(
                        "resource {resource_id} is assigned more than once"
                    )));
                }
            }
        }

        for group in candidates.distinct_actor_groups() {
            let mut occupied = BTreeMap::new();
            for assignment in &assignments {
                let Some(actor_id) = candidates.actor_for_role(assignment.role_id()) else {
                    continue;
                };
                if !group.contains(actor_id) {
                    continue;
                }
                let entity_id = candidates
                    .physical_entity_for_node(assignment.node_id())
                    .ok_or_else(|| {
                        ControlError::InvalidProposal(format!(
                            "constrained role {} has no physical entity selection",
                            assignment.role_id()
                        ))
                    })?;
                if let Some(previous_actor) = occupied.insert(entity_id.clone(), actor_id.clone()) {
                    return Err(ControlError::InvalidProposal(format!(
                        "actors {previous_actor} and {actor_id} select the same physical entity {entity_id}"
                    )));
                }
            }
        }

        let role_physical_entities = assignments
            .iter()
            .filter_map(|assignment| {
                candidates
                    .physical_entity_for_node(assignment.node_id())
                    .cloned()
                    .map(|entity_id| (assignment.role_id().clone(), entity_id))
            })
            .collect();
        let proposal = AssignmentProposal::new(
            requirement.task_ref().clone(),
            assignments,
            candidates.role_operations().clone(),
            requirement
                .roles()
                .iter()
                .cloned()
                .map(|role| (role.role_id().clone(), role))
                .collect(),
            role_physical_entities,
        );
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::ProposalCreated {
                task_ref: requirement.task_ref().clone(),
            },
        );
        Ok(proposal)
    }
}
