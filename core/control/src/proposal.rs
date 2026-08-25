//! Assignment proposal model and validation boundary.

use crate::{CandidateSet, ControlError, ControlPlane};
use domain::{
    CorrelationId, EventPayload, RoleAssignment, TaskId, TaskRef, TaskRequirement, TimestampMs,
};
use ports::{EventSink, SharedNodeStateReader};
use std::collections::BTreeSet;

/// A Scheduler selection accepted for validation but not yet committed.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AssignmentProposal {
    /// Mission-scoped task represented by this proposal.
    task_ref: TaskRef,
    /// Proposed node and resource assignments by role.
    assignments: Vec<RoleAssignment>,
}

impl AssignmentProposal {
    /// Creates a proposal after Control validates its role assignments.
    fn new(task_ref: TaskRef, assignments: Vec<RoleAssignment>) -> Self {
        Self {
            task_ref,
            assignments,
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
            if assignment.resource_ids().iter().any(|resource_id| {
                !node
                    .registration()
                    .owns_resource(resource_id, role.resource_kind())
            }) {
                return Err(ControlError::InvalidProposal(format!(
                    "role {} references a resource it does not own",
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

        let proposal = AssignmentProposal::new(requirement.task_ref().clone(), assignments);
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
