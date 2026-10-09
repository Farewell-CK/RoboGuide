//! Assigned-node unavailability assessment and recovery orchestration.
//!
//! This module detects divergence between active Group assignments and Shared
//! Node State. It performs role-scoped matching, but never selects replacement
//! nodes; callers make a bounded Scheduler choice before Control validates a
//! proposal, commits resources, and rebinds the existing Group.

use crate::{ControlError, ExecutionGroup};
use domain::{
    ExecutionGroupId, NodeId, NodeStateSnapshot, OperationRef, ResourceId, RoleId, RoleRequirement,
    TaskRef, TaskRequirement,
};
use std::collections::BTreeSet;

/// Explicit operator authority for replacing one failed Mission Actor with another physical entity.
///
/// Ordinary role recovery never creates this value. Control issues it only after validating the
/// exact blocked role, existing Actor binding, current deployment registry and bounded evidence.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct ActorTakeoverAuthorization {
    /// Existing blocked Group whose role may consume this one-shot authority.
    pub(super) group_id: ExecutionGroupId,
    /// Exact Task containing the unbound Actor role.
    pub(super) task_ref: TaskRef,
    /// Exact role whose assignment may move.
    pub(super) role_id: RoleId,
    /// Logical Actor retaining Mission semantics across the physical takeover.
    pub(super) actor_id: domain::ActorId,
    /// Previously authoritative routing Node.
    pub(super) previous_node_id: NodeId,
    /// Previously authoritative physical entity.
    pub(super) previous_entity_id: domain::PhysicalEntityId,
    /// Operator-selected standby routing Node.
    pub(super) replacement_node_id: NodeId,
    /// Operator-selected standby physical entity.
    pub(super) replacement_entity_id: domain::PhysicalEntityId,
    /// Deployment registry identity used for both entities.
    pub(super) registry_id: domain::PhysicalEntityRegistryId,
    /// Exact current registry revision used to authorize the takeover.
    pub(super) registry_revision: u64,
    /// Digest of external stop/takeover evidence; bytes remain outside Control.
    pub(super) evidence_digest: String,
}

impl ActorTakeoverAuthorization {
    /// Returns the blocked Group covered by this authorization.
    pub const fn group_id(&self) -> &ExecutionGroupId { &self.group_id }
    /// Returns the exact Task covered by this authorization.
    pub const fn task_ref(&self) -> &TaskRef { &self.task_ref }
    /// Returns the exact role covered by this authorization.
    pub const fn role_id(&self) -> &RoleId { &self.role_id }
    /// Returns the logical Actor whose physical executor may change.
    pub const fn actor_id(&self) -> &domain::ActorId { &self.actor_id }
    /// Returns the old authoritative Node.
    pub const fn previous_node_id(&self) -> &NodeId { &self.previous_node_id }
    /// Returns the old physical entity.
    pub const fn previous_entity_id(&self) -> &domain::PhysicalEntityId { &self.previous_entity_id }
    /// Returns the selected standby Node.
    pub const fn replacement_node_id(&self) -> &NodeId { &self.replacement_node_id }
    /// Returns the selected standby physical entity.
    pub const fn replacement_entity_id(&self) -> &domain::PhysicalEntityId { &self.replacement_entity_id }
    /// Returns the deployment registry identity.
    pub const fn registry_id(&self) -> &domain::PhysicalEntityRegistryId { &self.registry_id }
    /// Returns the exact deployment registry revision.
    pub const fn registry_revision(&self) -> u64 { self.registry_revision }
    /// Returns the external evidence digest.
    pub fn evidence_digest(&self) -> &str { &self.evidence_digest }
}

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
    /// Only the stopped-owner matching entry point may retain the original eligible Node.
    pub(super) stopped_owner_allowed: bool,
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
    /// Exact semantic operation constrained by normalized Mission recovery, when available.
    operation: Option<OperationRef>,
    /// Explicit cross-entity authority carried from Match to Proposal.
    pub(super) actor_takeover: Option<ActorTakeoverAuthorization>,
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
            operation: None,
            actor_takeover: None,
            stopped_owner_allowed: false,
        }
    }

    /// Creates a role-scoped candidate set whose nodes support the exact semantic operation.
    pub(super) fn new_with_operation(
        group_id: ExecutionGroupId,
        task_ref: TaskRef,
        role_id: RoleId,
        previous_node_id: NodeId,
        candidate_node_ids: Vec<NodeId>,
        operation: OperationRef,
    ) -> Self {
        Self {
            group_id,
            task_ref,
            role_id,
            previous_node_id,
            candidate_node_ids,
            operation: Some(operation),
            actor_takeover: None,
            stopped_owner_allowed: false,
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

    /// Returns the exact operation checked for normalized Mission recovery.
    pub const fn operation(&self) -> Option<&OperationRef> {
        self.operation.as_ref()
    }

    /// Returns explicit cross-entity authority when matching was takeover-scoped.
    pub const fn actor_takeover(&self) -> Option<&ActorTakeoverAuthorization> {
        self.actor_takeover.as_ref()
    }
}

/// Replacement assignment supplied by an external scheduler/coordination boundary.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RecoveryAssignmentProposal {
    /// Provenance of an explicit stopped-owner candidate set, not inferred from Node identity.
    pub(super) stopped_owner_allowed: bool,
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
    /// Exact semantic operation checked during Match and Proposal, when available.
    operation: Option<OperationRef>,
    /// Explicit cross-entity authority, absent for ordinary and same-owner recovery.
    pub(super) actor_takeover: Option<ActorTakeoverAuthorization>,
}

impl RecoveryAssignmentProposal {
    /// Creates a proposal only after candidate and resource validation succeeds.
    pub(super) fn new(
        group_id: ExecutionGroupId,
        task_ref: TaskRef,
        role_id: RoleId,
        previous_node_id: NodeId,
        replacement_node_id: NodeId,
        replacement_resource_ids: Vec<ResourceId>,
        operation: Option<OperationRef>,
    ) -> Self {
        Self {
            group_id,
            task_ref,
            role_id,
            previous_node_id,
            replacement_node_id,
            replacement_resource_ids,
            operation,
            actor_takeover: None,
            stopped_owner_allowed: false,
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

    /// Returns the exact operation that Commit must revalidate, when available.
    pub const fn operation(&self) -> Option<&OperationRef> {
        self.operation.as_ref()
    }

    /// Returns explicit cross-entity authority when this proposal is a takeover.
    pub const fn actor_takeover(&self) -> Option<&ActorTakeoverAuthorization> {
        self.actor_takeover.as_ref()
    }
}

/// Replacement assignment whose resources are committed to the existing Group.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct CommittedRecoveryAssignment {
    /// Preserves stopped-owner policy across Commit/restore/Rebind; legacy handles default false.
    #[serde(default)]
    pub(super) stopped_owner_allowed: bool,
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
    /// Exact semantic operation covered by this commitment, when available.
    #[serde(default)]
    operation: Option<OperationRef>,
    /// Durable cross-entity authority consumed atomically by Rebind.
    #[serde(default)]
    pub(super) actor_takeover: Option<ActorTakeoverAuthorization>,
}

impl CommittedRecoveryAssignment {
    /// Creates an internal commitment after all coordination checks succeed.
    #[cfg(test)]
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
            operation: None,
            actor_takeover: None,
            stopped_owner_allowed: false,
        }
    }

    /// Creates an operation-aware commitment after coordination validation succeeds.
    #[allow(clippy::too_many_arguments)]
    pub(super) const fn new_with_operation(
        group_id: ExecutionGroupId,
        task_ref: TaskRef,
        role_id: RoleId,
        previous_node_id: NodeId,
        replacement_node_id: NodeId,
        committed_resource_ids: Vec<ResourceId>,
        operation: Option<OperationRef>,
    ) -> Self {
        Self {
            group_id,
            task_ref,
            role_id,
            previous_node_id,
            replacement_node_id,
            committed_resource_ids,
            operation,
            actor_takeover: None,
            stopped_owner_allowed: false,
        }
    }

    /// Returns whether explicit stopped-owner Matching permitted reuse of the original Node.
    pub(crate) const fn stopped_owner_allowed(&self) -> bool {
        self.stopped_owner_allowed
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

    /// Returns the semantic operation covered by this commitment, when available.
    pub const fn operation(&self) -> Option<&OperationRef> {
        self.operation.as_ref()
    }

    /// Returns durable cross-entity authority when this commitment is a takeover.
    pub const fn actor_takeover(&self) -> Option<&ActorTakeoverAuthorization> {
        self.actor_takeover.as_ref()
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
pub(super) fn recovery_role(
    group: &ExecutionGroup,
    requirement: &TaskRequirement,
    task_ref: &TaskRef,
    role_id: &RoleId,
) -> Result<RoleRequirement, ControlError> {
    if requirement.task_ref() != task_ref {
        return Err(ControlError::InvalidProposal(
            "recovery context belongs to another task".to_string(),
        ));
    }
    let supplied = requirement
        .roles()
        .iter()
        .find(|role| role.role_id() == role_id)
        .ok_or_else(|| {
            ControlError::InvalidProposal(format!(
                "task requirement has no recovery role {role_id}"
            ))
        })?;
    let Some(authoritative) = group.role_requirement(task_ref, role_id) else {
        return Ok(supplied.clone());
    };
    if authoritative != supplied {
        return Err(ControlError::InvalidProposal(
            "recovery requirement differs from authoritative Execution Group role metadata"
                .to_string(),
        ));
    }
    Ok(authoritative.clone())
}

/// Validates proposed resources against one node and role without reserving them.
pub(crate) fn validate_recovery_resources(
    node: &NodeStateSnapshot,
    role: &RoleRequirement,
    resource_ids: &[ResourceId],
) -> Result<(), ControlError> {
    let requirements = role.resource_requirements();
    if resource_ids.len() != requirements.len() {
        return Err(ControlError::InvalidProposal(format!(
            "recovery role {} resources do not exactly cover its requirements",
            role.role_id()
        )));
    }
    let unique_resources = resource_ids.iter().collect::<BTreeSet<_>>();
    if unique_resources.len() != resource_ids.len() {
        return Err(ControlError::InvalidProposal(
            "recovery proposal contains duplicate resources".to_string(),
        ));
    }
    if resource_ids
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
            "recovery role {} resources do not satisfy replacement kind/capacity",
            role.role_id()
        )));
    }
    Ok(())
}

/// Builds the deterministic authority key for one Group role commitment.
pub(super) fn recovery_commitment_key(
    group_id: &ExecutionGroupId,
    task_ref: &TaskRef,
    role_id: &RoleId,
) -> (ExecutionGroupId, TaskRef, RoleId) {
    (group_id.clone(), task_ref.clone(), role_id.clone())
}
