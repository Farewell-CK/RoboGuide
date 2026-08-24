//! Assigned-node unavailability assessment and recovery orchestration.
//!
//! This module detects divergence between active Group assignments and Shared
//! Node State. It performs role-scoped matching, but never selects replacement
//! nodes; callers make a bootstrap scheduler choice before Control validates a
//! proposal, commits resources, and rebinds the existing Group.

use super::{ControlError, ControlPlane, GroupLifecycle, Reservation};
use domain::{
    CorrelationId, EventPayload, ExecutionGroupId, NodeId, NodeStateSnapshot, ResourceId, RoleId,
    RoleRequirement, TaskRef, TaskRequirement, TimestampMs,
};
use ports::{EventSink, SharedNodeStateReader};
use std::collections::BTreeSet;

/// One assigned role whose current node can no longer satisfy Control eligibility.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RoleRecoveryNeed {
    /// Active Group containing the unavailable assignment.
    group_id: ExecutionGroupId,
    /// Mission-scoped task owned by the Group.
    task_ref: TaskRef,
    /// Role whose current binding requires replacement.
    role_id: RoleId,
    /// Node currently bound to the unavailable role.
    current_node_id: NodeId,
}

impl RoleRecoveryNeed {
    /// Creates a recovery need from a detected assigned-node mismatch.
    pub(crate) fn new(
        group_id: ExecutionGroupId,
        task_ref: TaskRef,
        role_id: RoleId,
        current_node_id: NodeId,
    ) -> Self {
        Self {
            group_id,
            task_ref,
            role_id,
            current_node_id,
        }
    }

    /// Returns the Group requiring reconciliation.
    pub const fn group_id(&self) -> &ExecutionGroupId {
        &self.group_id
    }

    /// Returns the mission-scoped task retained throughout recovery.
    pub const fn task_ref(&self) -> &TaskRef {
        &self.task_ref
    }

    /// Returns the role requiring a replacement binding.
    pub const fn role_id(&self) -> &RoleId {
        &self.role_id
    }

    /// Returns the currently assigned node that became unavailable.
    pub const fn current_node_id(&self) -> &NodeId {
        &self.current_node_id
    }
}

/// Read-only result of comparing one active Group with Shared Node State.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ReconciliationAssessment {
    /// Every current assignment remains eligible; no mutation is needed.
    NoAction,
    /// Exactly one assigned role requires recovery in this slice.
    RoleRecoveryRequired(RoleRecoveryNeed),
}

/// Eligible nodes for replacing one unbound role without rematching unaffected roles.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RecoveryCandidateSet {
    /// Blocked Group whose role is being rematched.
    group_id: ExecutionGroupId,
    /// Mission-scoped task retained by the Group.
    task_ref: TaskRef,
    /// Unbound role requiring a replacement.
    role_id: RoleId,
    /// Failed node excluded from replacement candidates.
    previous_node_id: NodeId,
    /// Currently eligible nodes in deterministic identity order.
    candidate_node_ids: Vec<NodeId>,
}

impl RecoveryCandidateSet {
    /// Creates a role-scoped candidate set after Control eligibility evaluation.
    pub(crate) fn new(
        group_id: ExecutionGroupId,
        task_ref: TaskRef,
        role_id: RoleId,
        previous_node_id: NodeId,
        candidate_node_ids: Vec<NodeId>,
    ) -> Self {
        Self {
            group_id,
            task_ref,
            role_id,
            previous_node_id,
            candidate_node_ids,
        }
    }

    /// Returns the existing Group awaiting recovery.
    pub const fn group_id(&self) -> &ExecutionGroupId {
        &self.group_id
    }

    /// Returns the mission-scoped task retained by the Group.
    pub const fn task_ref(&self) -> &TaskRef {
        &self.task_ref
    }

    /// Returns the single role represented by this recovery match.
    pub const fn role_id(&self) -> &RoleId {
        &self.role_id
    }

    /// Returns the failed node excluded from this candidate set.
    pub const fn previous_node_id(&self) -> &NodeId {
        &self.previous_node_id
    }

    /// Returns eligible replacement nodes in deterministic order.
    pub fn candidate_node_ids(&self) -> &[NodeId] {
        &self.candidate_node_ids
    }

    /// Returns whether no replacement is currently eligible.
    pub fn is_empty(&self) -> bool {
        self.candidate_node_ids.is_empty()
    }
}

/// Replacement assignment supplied by an external scheduler/coordination boundary.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RecoveryAssignmentProposal {
    /// Group whose unbound role should receive the replacement.
    group_id: ExecutionGroupId,
    /// Mission-scoped task expected to own the Group.
    task_ref: TaskRef,
    /// Unbound role receiving the replacement assignment.
    role_id: RoleId,
    /// Failed node that this proposal must replace.
    previous_node_id: NodeId,
    /// Replacement node selected outside reconciliation.
    replacement_node_id: NodeId,
    /// Replacement resources proposed by the caller but not yet committed.
    replacement_resource_ids: Vec<ResourceId>,
}

impl RecoveryAssignmentProposal {
    /// Creates a proposal only after candidate and resource validation succeeds.
    fn new(
        group_id: ExecutionGroupId,
        task_ref: TaskRef,
        role_id: RoleId,
        previous_node_id: NodeId,
        replacement_node_id: NodeId,
        replacement_resource_ids: Vec<ResourceId>,
    ) -> Self {
        Self {
            group_id,
            task_ref,
            role_id,
            previous_node_id,
            replacement_node_id,
            replacement_resource_ids,
        }
    }

    /// Returns the Group targeted by this proposal.
    pub const fn group_id(&self) -> &ExecutionGroupId {
        &self.group_id
    }

    /// Returns the mission-scoped task asserted by this proposal.
    pub const fn task_ref(&self) -> &TaskRef {
        &self.task_ref
    }

    /// Returns the role targeted by this proposal.
    pub const fn role_id(&self) -> &RoleId {
        &self.role_id
    }

    /// Returns the failed node expected to be replaced.
    pub const fn previous_node_id(&self) -> &NodeId {
        &self.previous_node_id
    }

    /// Returns the externally selected replacement node.
    pub const fn replacement_node_id(&self) -> &NodeId {
        &self.replacement_node_id
    }

    /// Returns replacement resources proposed for the role.
    pub fn replacement_resource_ids(&self) -> &[ResourceId] {
        &self.replacement_resource_ids
    }
}

/// Replacement assignment whose resources are committed to the existing Group.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CommittedRecoveryAssignment {
    /// Existing Group that owns the replacement commitment.
    group_id: ExecutionGroupId,
    /// Mission-scoped task that owns the commitment.
    task_ref: TaskRef,
    /// Unbound role receiving the committed replacement.
    role_id: RoleId,
    /// Failed node being replaced.
    previous_node_id: NodeId,
    /// Replacement node covered by the commitment.
    replacement_node_id: NodeId,
    /// Resources atomically reserved for the replacement role.
    committed_resource_ids: Vec<ResourceId>,
}

impl CommittedRecoveryAssignment {
    /// Creates an internal commitment after all coordination checks succeed.
    pub(crate) const fn new(
        group_id: ExecutionGroupId,
        task_ref: TaskRef,
        role_id: RoleId,
        previous_node_id: NodeId,
        replacement_node_id: NodeId,
        committed_resource_ids: Vec<ResourceId>,
    ) -> Self {
        Self {
            group_id,
            task_ref,
            role_id,
            previous_node_id,
            replacement_node_id,
            committed_resource_ids,
        }
    }

    /// Returns the existing Group that owns this commitment.
    pub const fn group_id(&self) -> &ExecutionGroupId {
        &self.group_id
    }

    /// Returns the mission-scoped task that owns this commitment.
    pub const fn task_ref(&self) -> &TaskRef {
        &self.task_ref
    }

    /// Returns the role receiving the committed replacement.
    pub const fn role_id(&self) -> &RoleId {
        &self.role_id
    }

    /// Returns the failed node being replaced.
    pub const fn previous_node_id(&self) -> &NodeId {
        &self.previous_node_id
    }

    /// Returns the committed replacement node.
    pub const fn replacement_node_id(&self) -> &NodeId {
        &self.replacement_node_id
    }

    /// Returns resources committed to the existing Group and role.
    pub fn committed_resource_ids(&self) -> &[ResourceId] {
        &self.committed_resource_ids
    }
}

/// Result of mutating a Group through the explicit recovery orchestration steps.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RecoveryOutcome {
    /// The Group is Blocked with one role unbound and awaits a proposal.
    Pending {
        /// Group waiting for an external replacement proposal.
        group_id: ExecutionGroupId,
        /// Mission-scoped task retained by the Group.
        task_ref: TaskRef,
        /// Unbound role waiting for replacement.
        role_id: RoleId,
    },
    /// A committed replacement rebound the role and left the Group Adapted.
    Recovered {
        /// Group restored without changing its identity.
        group_id: ExecutionGroupId,
        /// Mission-scoped task retained by the Group.
        task_ref: TaskRef,
        /// Role that received a replacement binding.
        role_id: RoleId,
        /// Former node whose binding failed.
        from_node: NodeId,
        /// Validated replacement node.
        to_node: NodeId,
    },
}

/// Resolves one role from the task identity carried by recovery context.
fn recovery_role<'a>(
    requirement: &'a TaskRequirement,
    task_ref: &TaskRef,
    role_id: &RoleId,
) -> Result<&'a RoleRequirement, ControlError> {
    if requirement.task_ref() != task_ref {
        return Err(ControlError::InvalidProposal(
            "recovery context belongs to another task".to_string(),
        ));
    }
    requirement
        .roles()
        .iter()
        .find(|role| role.role_id() == role_id)
        .ok_or_else(|| {
            ControlError::InvalidProposal(format!(
                "task requirement has no recovery role {role_id}"
            ))
        })
}

/// Validates proposed resources against one node and role without reserving them.
fn validate_recovery_resources(
    node: &NodeStateSnapshot,
    role: &RoleRequirement,
    resource_ids: &[ResourceId],
) -> Result<(), ControlError> {
    if role.resource_kind().is_some() && resource_ids.is_empty() {
        return Err(ControlError::InvalidProposal(format!(
            "recovery role {} requires a resource binding",
            role.role_id()
        )));
    }
    let unique_resources = resource_ids.iter().collect::<BTreeSet<_>>();
    if unique_resources.len() != resource_ids.len() {
        return Err(ControlError::InvalidProposal(
            "recovery proposal contains duplicate resources".to_string(),
        ));
    }
    if resource_ids.iter().any(|resource_id| {
        !node
            .registration()
            .owns_resource(resource_id, role.resource_kind())
    }) {
        return Err(ControlError::InvalidProposal(format!(
            "recovery role {} references a resource not owned by its replacement node",
            role.role_id()
        )));
    }
    Ok(())
}

/// Builds the deterministic authority key for one Group role commitment.
fn recovery_commitment_key(
    group_id: &ExecutionGroupId,
    role_id: &RoleId,
) -> (ExecutionGroupId, RoleId) {
    (group_id.clone(), role_id.clone())
}

impl ControlPlane {
    /// Returns the authoritative pending commitment for one Group role, if present.
    pub fn pending_recovery_commitment(
        &self,
        group_id: &ExecutionGroupId,
        role_id: &RoleId,
    ) -> Option<&CommittedRecoveryAssignment> {
        self.pending_recovery_commitments
            .get(&recovery_commitment_key(group_id, role_id))
    }

    /// Verifies that a commitment handle is the current Control-owned pending value.
    pub(crate) fn validate_pending_recovery_commitment(
        &self,
        committed: &CommittedRecoveryAssignment,
    ) -> Result<(), ControlError> {
        let pending = self
            .pending_recovery_commitment(committed.group_id(), committed.role_id())
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
        if group.task_ref != *requirement.task_ref() {
            return Err(ControlError::InvalidProposal(
                "reconciliation requirement belongs to another task".to_string(),
            ));
        }

        let mut unavailable = Vec::new();
        for assignment in &group.assignments {
            let role = requirement
                .roles()
                .iter()
                .find(|role| role.role_id() == assignment.role_id())
                .ok_or_else(|| {
                    ControlError::InvalidProposal(format!(
                        "task requirement has no group role {}",
                        assignment.role_id()
                    ))
                })?;
            if !self.node_is_eligible_for_role(state, assignment.node_id(), role, timestamp) {
                unavailable.push((assignment.role_id().clone(), assignment.node_id().clone()));
            }
        }

        match unavailable.as_slice() {
            [] => Ok(ReconciliationAssessment::NoAction),
            [(role_id, node_id)] => {
                let need = RoleRecoveryNeed::new(
                    group_id.clone(),
                    group.task_ref.clone(),
                    role_id.clone(),
                    node_id.clone(),
                );
                events.append(
                    timestamp,
                    correlation_id,
                    None,
                    EventPayload::ReconciliationRoleRecoveryRequired {
                        group_id: group_id.clone(),
                        task_ref: group.task_ref.clone(),
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
        if group.task_ref != *need.task_ref()
            || !group.assignments.iter().any(|assignment| {
                assignment.role_id() == need.role_id()
                    && assignment.node_id() == need.current_node_id()
            })
        {
            return Err(ControlError::InvalidProposal(
                "recovery need no longer matches the active group assignment".to_string(),
            ));
        }

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
        self.release_role_binding(
            need.group_id(),
            need.role_id(),
            timestamp,
            correlation_id,
            events,
        )?;
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
        let role = recovery_role(requirement, need.task_ref(), need.role_id())?;
        let group = self
            .groups
            .get(need.group_id())
            .ok_or_else(|| ControlError::UnknownGroup(need.group_id().clone()))?;
        if group.lifecycle != GroupLifecycle::Blocked {
            return Err(ControlError::InvalidLifecycle(group.lifecycle));
        }
        let previous_node = group
            .unbound_roles
            .get(need.role_id())
            .map(|binding| binding.previous_node_id.clone())
            .ok_or_else(|| {
                ControlError::InvalidProposal("recovery role is not unbound".to_string())
            })?;
        if group.task_ref != *need.task_ref() || previous_node != *need.current_node_id() {
            return Err(ControlError::InvalidProposal(
                "recovery need no longer matches the blocked group role".to_string(),
            ));
        }

        let candidate_node_ids = state
            .nodes()
            .into_iter()
            .filter(|snapshot| snapshot.node_id() != need.current_node_id())
            .filter(|snapshot| {
                self.node_is_eligible_for_role(state, snapshot.node_id(), role, timestamp)
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
        let role = recovery_role(requirement, candidates.task_ref(), candidates.role_id())?;
        let group = self
            .groups
            .get(candidates.group_id())
            .ok_or_else(|| ControlError::UnknownGroup(candidates.group_id().clone()))?;
        if group.lifecycle != GroupLifecycle::Blocked {
            return Err(ControlError::InvalidLifecycle(group.lifecycle));
        }
        if group.task_ref != *candidates.task_ref() || !group.is_role_unbound(candidates.role_id())
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
        validate_recovery_resources(node, role, &replacement_resource_ids)?;

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
        let role = recovery_role(requirement, proposal.task_ref(), proposal.role_id())?;
        let group = self
            .groups
            .get(proposal.group_id())
            .ok_or_else(|| ControlError::UnknownGroup(proposal.group_id().clone()))?;
        if group.lifecycle != GroupLifecycle::Blocked {
            return Err(ControlError::InvalidLifecycle(group.lifecycle));
        }
        let previous_node = group
            .unbound_roles
            .get(proposal.role_id())
            .map(|binding| binding.previous_node_id.clone())
            .ok_or_else(|| {
                ControlError::InvalidProposal("recovery role is not unbound".to_string())
            })?;
        if group.task_ref != *proposal.task_ref()
            || previous_node != *proposal.previous_node_id()
            || proposal.replacement_node_id() == proposal.previous_node_id()
        {
            return Err(ControlError::InvalidProposal(
                "recovery proposal no longer matches the blocked group role".to_string(),
            ));
        }
        let commitment_key = recovery_commitment_key(proposal.group_id(), proposal.role_id());
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
        if !self.node_is_eligible_for_role(state, proposal.replacement_node_id(), role, timestamp) {
            return Err(ControlError::InvalidProposal(format!(
                "replacement node {} is no longer eligible for role {}",
                proposal.replacement_node_id(),
                proposal.role_id()
            )));
        }
        validate_recovery_resources(replacement, role, proposal.replacement_resource_ids())?;
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
        if group.task_ref != *committed.task_ref() || !group.is_role_unbound(committed.role_id()) {
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
