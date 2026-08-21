//! Assigned-node unavailability assessment and recovery orchestration.
//!
//! This module detects divergence between active Group assignments and Shared
//! Node State. It never selects replacement nodes; callers supply a bootstrap
//! proposal representing the existing scheduling and coordination boundary.

use super::{ControlError, ControlPlane, GroupLifecycle, RoleRequirementView};
use domain::{
    CorrelationId, EventPayload, ExecutionGroupId, NodeId, ResourceId, RoleId, TaskRef,
    TaskRequirement, TimestampMs,
};
use ports::{EventSink, SharedNodeStateReader};

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
    fn new(
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

/// Replacement assignment supplied by an external scheduler/coordination boundary.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RecoveryAssignmentProposal {
    /// Group whose unbound role should receive the replacement.
    group_id: ExecutionGroupId,
    /// Mission-scoped task expected to own the Group.
    task_ref: TaskRef,
    /// Unbound role receiving the replacement assignment.
    role_id: RoleId,
    /// Replacement node selected outside reconciliation.
    replacement_node_id: NodeId,
    /// Replacement resources proposed and coordinated by the caller.
    replacement_resource_ids: Vec<ResourceId>,
}

impl RecoveryAssignmentProposal {
    /// Creates an externally selected bootstrap recovery assignment proposal.
    pub const fn new(
        group_id: ExecutionGroupId,
        task_ref: TaskRef,
        role_id: RoleId,
        replacement_node_id: NodeId,
        replacement_resource_ids: Vec<ResourceId>,
    ) -> Self {
        Self {
            group_id,
            task_ref,
            role_id,
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

    /// Returns the externally selected replacement node.
    pub const fn replacement_node_id(&self) -> &NodeId {
        &self.replacement_node_id
    }

    /// Returns replacement resources proposed for the role.
    pub fn replacement_resource_ids(&self) -> &[ResourceId] {
        &self.replacement_resource_ids
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
    /// A validated proposal rebound the role and left the Group Adapted.
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

impl ControlPlane {
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

    /// Validates and applies one externally selected replacement to an unbound role.
    pub fn apply_role_recovery<S: SharedNodeStateReader, E: EventSink>(
        &mut self,
        state: &S,
        requirement: &TaskRequirement,
        proposal: &RecoveryAssignmentProposal,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<RecoveryOutcome, ControlError> {
        if proposal.task_ref() != requirement.task_ref() {
            return Err(ControlError::InvalidProposal(
                "recovery proposal belongs to another task".to_string(),
            ));
        }
        let group = self
            .groups
            .get(proposal.group_id())
            .ok_or_else(|| ControlError::UnknownGroup(proposal.group_id().clone()))?;
        if group.lifecycle != GroupLifecycle::Blocked {
            return Err(ControlError::InvalidLifecycle(group.lifecycle));
        }
        if group.task_ref != *proposal.task_ref() || !group.is_role_unbound(proposal.role_id()) {
            return Err(ControlError::InvalidProposal(
                "recovery proposal does not match the blocked group role".to_string(),
            ));
        }
        let from_node = group
            .unbound_roles
            .get(proposal.role_id())
            .map(|binding| binding.previous_node_id.clone())
            .ok_or_else(|| {
                ControlError::InvalidProposal("recovery role is not unbound".to_string())
            })?;
        let role = requirement
            .roles()
            .iter()
            .find(|role| role.role_id() == proposal.role_id())
            .ok_or_else(|| {
                ControlError::InvalidProposal(format!(
                    "task requirement has no recovery role {}",
                    proposal.role_id()
                ))
            })?
            .clone();

        self.rebind_role(
            state,
            proposal.group_id(),
            &RoleRequirementView::new(role),
            proposal.replacement_node_id().clone(),
            proposal.replacement_resource_ids().to_vec(),
            timestamp,
            correlation_id,
            events,
        )?;
        Ok(RecoveryOutcome::Recovered {
            group_id: proposal.group_id().clone(),
            task_ref: proposal.task_ref().clone(),
            role_id: proposal.role_id().clone(),
            from_node,
            to_node: proposal.replacement_node_id().clone(),
        })
    }
}
