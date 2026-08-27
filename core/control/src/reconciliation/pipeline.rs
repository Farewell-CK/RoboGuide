//! Reconciliation assessment and recovery authority transitions.

use super::model::{
    CommittedRecoveryAssignment, ReconciliationAssessment, RecoveryAssignmentProposal,
    RecoveryCandidateSet, RecoveryOutcome, RoleRecoveryNeed, recovery_commitment_key,
    recovery_role, validate_recovery_resources,
};
use crate::{ControlError, ControlPlane, GroupLifecycle, coordination::Reservation};
use domain::{
    CorrelationId, EventPayload, ExecutionGroupId, NodeId, ResourceId, RoleId, TaskRequirement,
    TimestampMs,
};
use ports::{EventSink, SharedNodeStateReader};

impl ControlPlane {
    /// Returns the authoritative pending commitment for one Group role, if present.
    pub fn pending_recovery_commitment(
        &self,
        group_id: &ExecutionGroupId,
        role_id: &RoleId,
    ) -> Option<&CommittedRecoveryAssignment> {
        self.pending_recovery_commitments
            .iter()
            .find(|((pending_group, _, pending_role), _)| {
                pending_group == group_id && pending_role == role_id
            })
            .map(|(_, commitment)| commitment)
    }

    /// Returns the authoritative pending commitment for one Group Task role.
    pub fn pending_recovery_commitment_for_task(
        &self,
        group_id: &ExecutionGroupId,
        task_ref: &domain::TaskRef,
        role_id: &RoleId,
    ) -> Option<&CommittedRecoveryAssignment> {
        self.pending_recovery_commitments
            .get(&recovery_commitment_key(group_id, task_ref, role_id))
    }

    /// Verifies that a commitment handle is the current Control-owned pending value.
    pub(crate) fn validate_pending_recovery_commitment(
        &self,
        committed: &CommittedRecoveryAssignment,
    ) -> Result<(), ControlError> {
        let pending = self
            .pending_recovery_commitment_for_task(
                committed.group_id(),
                committed.task_ref(),
                committed.role_id(),
            )
            .ok_or_else(|| ControlError::PendingRecoveryCommitmentNotFound {
                group_id: committed.group_id().clone(),
                role_id: committed.role_id().clone(),
            })?;
        if pending != committed {
            return Err(ControlError::PendingRecoveryCommitmentMismatch {
                group_id: committed.group_id().clone(),
                role_id: committed.role_id().clone(),
            });
        }
        Ok(())
    }

    /// Verifies that every committed resource remains owned by its Group and role.
    pub(crate) fn validate_recovery_commitment_reservations(
        &self,
        committed: &CommittedRecoveryAssignment,
    ) -> Result<(), ControlError> {
        for resource_id in committed.committed_resource_ids() {
            let reservation = self.reservations.get(resource_id).ok_or_else(|| {
                ControlError::InvalidProposal(format!(
                    "pending recovery resource {resource_id} has no reservation"
                ))
            })?;
            if reservation.task_ref != *committed.task_ref()
                || reservation.role_id != *committed.role_id()
                || reservation.group_id.as_ref() != Some(committed.group_id())
            {
                return Err(ControlError::InvalidProposal(format!(
                    "resource {resource_id} is not owned by the pending recovery commitment"
                )));
            }
        }
        Ok(())
    }

    /// Compares one active Group's desired assignments with current Shared Node State.
    pub fn assess_group<S: SharedNodeStateReader, E: EventSink>(
        &self,
        state: &S,
        group_id: &ExecutionGroupId,
        requirement: &TaskRequirement,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<ReconciliationAssessment, ControlError> {
        let group = self
            .groups
            .get(group_id)
            .ok_or_else(|| ControlError::UnknownGroup(group_id.clone()))?;
        if group.lifecycle != GroupLifecycle::Active {
            return Err(ControlError::InvalidLifecycle(group.lifecycle));
        }
        if group.task_ref != *requirement.task_ref()
            && group.task_execution(requirement.task_ref()).is_none()
        {
            return Err(ControlError::InvalidProposal(
                "reconciliation requirement belongs to another task".to_string(),
            ));
        }

        let mut unavailable = Vec::new();
        let assignments = group
            .task_execution(requirement.task_ref())
            .map(|execution| execution.assignments())
            .unwrap_or_else(|| group.assignments.as_slice());
        for assignment in assignments {
            let role = recovery_role(
                group,
                requirement,
                requirement.task_ref(),
                assignment.role_id(),
            )?;
            if !self.node_is_eligible_for_role(state, assignment.node_id(), &role, timestamp) {
                unavailable.push((assignment.role_id().clone(), assignment.node_id().clone()));
            }
        }

        match unavailable.as_slice() {
            [] => Ok(ReconciliationAssessment::NoAction),
            [(role_id, node_id)] => {
                let need = RoleRecoveryNeed::new(
                    group_id.clone(),
                    requirement.task_ref().clone(),
                    role_id.clone(),
                    node_id.clone(),
                );
                events.append(
                    timestamp,
                    correlation_id,
                    None,
                    EventPayload::ReconciliationRoleRecoveryRequired {
                        group_id: group_id.clone(),
                        task_ref: requirement.task_ref().clone(),
                        role_id: role_id.clone(),
                        node_id: node_id.clone(),
                    },
                );
                Ok(ReconciliationAssessment::RoleRecoveryRequired(need))
            }
            _ => Err(ControlError::InvalidProposal(
                "multiple unavailable assigned roles are outside reconciliation slice v0.1"
                    .to_string(),
            )),
        }
    }

    /// Blocks the assessed Group and partially releases only the affected role binding.
    pub fn begin_role_recovery<E: EventSink>(
        &mut self,
        need: &RoleRecoveryNeed,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<RecoveryOutcome, ControlError> {
        let group = self
            .groups
            .get(need.group_id())
            .ok_or_else(|| ControlError::UnknownGroup(need.group_id().clone()))?;
        if group.lifecycle != GroupLifecycle::Active {
            return Err(ControlError::InvalidLifecycle(group.lifecycle));
        }
        let assignments = group
            .task_execution(need.task_ref())
            .map(|execution| execution.assignments())
            .unwrap_or_else(|| group.assignments.as_slice());
        if (group.task_ref != *need.task_ref() && group.task_execution(need.task_ref()).is_none())
            || !assignments.iter().any(|assignment| {
                assignment.role_id() == need.role_id()
                    && assignment.node_id() == need.current_node_id()
            })
        {
            return Err(ControlError::InvalidProposal(
                "recovery need no longer matches the active group assignment".to_string(),
            ));
        }
        let task_scoped = group.task_execution(need.task_ref()).is_some();

        self.block_group(
            need.group_id(),
            format!(
                "assigned node {} is unavailable for role {}",
                need.current_node_id(),
                need.role_id()
            ),
            timestamp,
            correlation_id,
            events,
        )?;
        if task_scoped {
            self.release_task_role_binding(
                need.group_id(),
                need.task_ref(),
                need.role_id(),
                timestamp,
                correlation_id,
                events,
            )?;
        } else {
            self.release_role_binding(
                need.group_id(),
                need.role_id(),
                timestamp,
                correlation_id,
                events,
            )?;
        }
        Ok(RecoveryOutcome::Pending {
            group_id: need.group_id().clone(),
            task_ref: need.task_ref().clone(),
            role_id: need.role_id().clone(),
        })
    }

    /// Matches only the unbound recovery role against current Control eligibility.
    pub fn match_recovery_candidates<S: SharedNodeStateReader, E: EventSink>(
        &self,
        state: &S,
        need: &RoleRecoveryNeed,
        requirement: &TaskRequirement,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<RecoveryCandidateSet, ControlError> {
        let group = self
            .groups
            .get(need.group_id())
            .ok_or_else(|| ControlError::UnknownGroup(need.group_id().clone()))?;
        let role = recovery_role(group, requirement, need.task_ref(), need.role_id())?;
        if group.lifecycle != GroupLifecycle::Blocked {
            return Err(ControlError::InvalidLifecycle(group.lifecycle));
        }
        let previous_node = group
            .unbound_roles
            .get(need.role_id())
            .or_else(|| {
                group
                    .task_unbound_roles
                    .get(&(need.task_ref().clone(), need.role_id().clone()))
            })
            .map(|binding| binding.previous_node_id.clone())
            .ok_or_else(|| {
                ControlError::InvalidProposal("recovery role is not unbound".to_string())
            })?;
        if (group.task_ref != *need.task_ref() && group.task_execution(need.task_ref()).is_none())
            || previous_node != *need.current_node_id()
        {
            return Err(ControlError::InvalidProposal(
                "recovery need no longer matches the blocked group role".to_string(),
            ));
        }

        let actor_authority_node = role
            .actor_id()
            .and_then(|actor_id| self.actor_authority_node(requirement.mission_id(), actor_id))
            .cloned();
        let candidate_node_ids = state
            .nodes()
            .into_iter()
            .filter(|snapshot| snapshot.node_id() != need.current_node_id())
            .filter(|snapshot| {
                actor_authority_node
                    .as_ref()
                    .is_none_or(|node_id| snapshot.node_id() == node_id)
            })
            .filter(|snapshot| {
                self.node_is_eligible_for_role(state, snapshot.node_id(), &role, timestamp)
            })
            .map(|snapshot| snapshot.node_id().clone())
            .collect::<Vec<_>>();
        let candidates = RecoveryCandidateSet::new(
            need.group_id().clone(),
            need.task_ref().clone(),
            need.role_id().clone(),
            need.current_node_id().clone(),
            candidate_node_ids.clone(),
        );
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::RecoveryCandidatesMatched {
                group_id: need.group_id().clone(),
                task_ref: need.task_ref().clone(),
                role_id: need.role_id().clone(),
                candidate_node_ids,
            },
        );
        Ok(candidates)
    }

    /// Validates an external scheduler choice without reserving resources or binding the Group.
    #[allow(clippy::too_many_arguments)]
    pub fn propose_role_recovery<S: SharedNodeStateReader, E: EventSink>(
        &self,
        state: &S,
        candidates: &RecoveryCandidateSet,
        requirement: &TaskRequirement,
        selected_node_id: NodeId,
        replacement_resource_ids: Vec<ResourceId>,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<RecoveryAssignmentProposal, ControlError> {
        let group = self
            .groups
            .get(candidates.group_id())
            .ok_or_else(|| ControlError::UnknownGroup(candidates.group_id().clone()))?;
        let role = recovery_role(
            group,
            requirement,
            candidates.task_ref(),
            candidates.role_id(),
        )?;
        if group.lifecycle != GroupLifecycle::Blocked {
            return Err(ControlError::InvalidLifecycle(group.lifecycle));
        }
        if (group.task_ref != *candidates.task_ref()
            && group.task_execution(candidates.task_ref()).is_none())
            || (!group.is_role_unbound(candidates.role_id())
                && !group.is_task_role_unbound(candidates.task_ref(), candidates.role_id()))
        {
            return Err(ControlError::InvalidProposal(
                "recovery candidates do not match the blocked group role".to_string(),
            ));
        }
        if selected_node_id == *candidates.previous_node_id()
            || !candidates.candidate_node_ids().contains(&selected_node_id)
        {
            return Err(ControlError::InvalidProposal(format!(
                "scheduler-selected node {selected_node_id} is not a recovery candidate"
            )));
        }
        let node = state
            .node(&selected_node_id)
            .ok_or_else(|| ControlError::UnknownNode(selected_node_id.clone()))?;
        validate_recovery_resources(node, &role, &replacement_resource_ids)?;

        let proposal = RecoveryAssignmentProposal::new(
            candidates.group_id().clone(),
            candidates.task_ref().clone(),
            candidates.role_id().clone(),
            candidates.previous_node_id().clone(),
            selected_node_id.clone(),
            replacement_resource_ids.clone(),
        );
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::RecoveryAssignmentProposed {
                group_id: candidates.group_id().clone(),
                task_ref: candidates.task_ref().clone(),
                role_id: candidates.role_id().clone(),
                replacement_node_id: selected_node_id,
                resource_ids: replacement_resource_ids,
            },
        );
        Ok(proposal)
    }

    /// Revalidates and atomically commits replacement resources to the existing Group.
    pub fn commit_role_recovery<S: SharedNodeStateReader, E: EventSink>(
        &mut self,
        state: &S,
        requirement: &TaskRequirement,
        proposal: &RecoveryAssignmentProposal,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<CommittedRecoveryAssignment, ControlError> {
        let group = self
            .groups
            .get(proposal.group_id())
            .ok_or_else(|| ControlError::UnknownGroup(proposal.group_id().clone()))?;
        let role = recovery_role(group, requirement, proposal.task_ref(), proposal.role_id())?;
        if group.lifecycle != GroupLifecycle::Blocked {
            return Err(ControlError::InvalidLifecycle(group.lifecycle));
        }
        let previous_node = group
            .unbound_roles
            .get(proposal.role_id())
            .or_else(|| {
                group
                    .task_unbound_roles
                    .get(&(proposal.task_ref().clone(), proposal.role_id().clone()))
            })
            .map(|binding| binding.previous_node_id.clone())
            .ok_or_else(|| {
                ControlError::InvalidProposal("recovery role is not unbound".to_string())
            })?;
        if (group.task_ref != *proposal.task_ref()
            && group.task_execution(proposal.task_ref()).is_none())
            || !group.is_role_unbound(proposal.role_id())
                && !group.is_task_role_unbound(proposal.task_ref(), proposal.role_id())
            || previous_node != *proposal.previous_node_id()
            || proposal.replacement_node_id() == proposal.previous_node_id()
        {
            return Err(ControlError::InvalidProposal(
                "recovery proposal no longer matches the blocked group role".to_string(),
            ));
        }
        if let Some(actor_id) = role.actor_id()
            && let Some(authority_node) =
                self.actor_authority_node(requirement.mission_id(), actor_id)
            && proposal.replacement_node_id() != authority_node
        {
            return Err(ControlError::InvalidProposal(format!(
                "recovery replacement {} violates actor {actor_id} authority on {authority_node}; explicit Actor rebind is required",
                proposal.replacement_node_id()
            )));
        }
        let commitment_key =
            recovery_commitment_key(proposal.group_id(), proposal.task_ref(), proposal.role_id());
        if self
            .pending_recovery_commitments
            .contains_key(&commitment_key)
        {
            return Err(ControlError::PendingRecoveryCommitmentExists {
                group_id: proposal.group_id().clone(),
                role_id: proposal.role_id().clone(),
            });
        }
        let replacement = state
            .node(proposal.replacement_node_id())
            .ok_or_else(|| ControlError::UnknownNode(proposal.replacement_node_id().clone()))?;
        if !self.node_is_eligible_for_role(state, proposal.replacement_node_id(), &role, timestamp)
        {
            return Err(ControlError::InvalidProposal(format!(
                "replacement node {} is no longer eligible for role {}",
                proposal.replacement_node_id(),
                proposal.role_id()
            )));
        }
        validate_recovery_resources(replacement, &role, proposal.replacement_resource_ids())?;
        for resource_id in proposal.replacement_resource_ids() {
            if let Some(reservation) = self.reservations.get(resource_id) {
                return Err(ControlError::ResourceConflict {
                    resource_id: resource_id.clone(),
                    owner_task_ref: reservation.task_ref.clone(),
                    owner_role_id: reservation.role_id.clone(),
                });
            }
        }

        let committed = CommittedRecoveryAssignment::new(
            proposal.group_id().clone(),
            proposal.task_ref().clone(),
            proposal.role_id().clone(),
            previous_node,
            proposal.replacement_node_id().clone(),
            proposal.replacement_resource_ids().to_vec(),
        );
        for resource_id in proposal.replacement_resource_ids() {
            self.reservations.insert(
                resource_id.clone(),
                Reservation {
                    task_ref: proposal.task_ref().clone(),
                    role_id: proposal.role_id().clone(),
                    group_id: Some(proposal.group_id().clone()),
                    scope: domain::ResourceBindingScope::Task,
                    owner: domain::AllocationOwner::Task(proposal.task_ref().clone()),
                },
            );
        }
        self.pending_recovery_commitments
            .insert(commitment_key, committed.clone());
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::RecoveryAssignmentCommitted {
                group_id: proposal.group_id().clone(),
                task_ref: proposal.task_ref().clone(),
                role_id: proposal.role_id().clone(),
                replacement_node_id: proposal.replacement_node_id().clone(),
                resource_ids: proposal.replacement_resource_ids().to_vec(),
            },
        );
        Ok(committed)
    }

    /// Aborts one authoritative pending commitment and releases only its resources.
    pub fn abort_role_recovery_commitment<E: EventSink>(
        &mut self,
        committed: &CommittedRecoveryAssignment,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<(), ControlError> {
        self.validate_pending_recovery_commitment(committed)?;
        let group = self
            .groups
            .get(committed.group_id())
            .ok_or_else(|| ControlError::UnknownGroup(committed.group_id().clone()))?;
        if group.lifecycle != GroupLifecycle::Blocked {
            return Err(ControlError::InvalidLifecycle(group.lifecycle));
        }
        if (group.task_ref != *committed.task_ref()
            && group.task_execution(committed.task_ref()).is_none())
            || (!group.is_role_unbound(committed.role_id())
                && !group.is_task_role_unbound(committed.task_ref(), committed.role_id()))
        {
            return Err(ControlError::InvalidProposal(
                "pending recovery commitment does not match the blocked Group".to_string(),
            ));
        }
        self.validate_recovery_commitment_reservations(committed)?;

        for resource_id in committed.committed_resource_ids() {
            self.reservations.remove(resource_id);
        }
        self.pending_recovery_commitments
            .remove(&recovery_commitment_key(
                committed.group_id(),
                committed.task_ref(),
                committed.role_id(),
            ));
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::RecoveryAssignmentAborted {
                group_id: committed.group_id().clone(),
                task_ref: committed.task_ref().clone(),
                role_id: committed.role_id().clone(),
                replacement_node_id: committed.replacement_node_id().clone(),
                resource_ids: committed.committed_resource_ids().to_vec(),
            },
        );
        Ok(())
    }
}
