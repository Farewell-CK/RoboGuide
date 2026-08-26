//! Projection of authoritative Control reservations into an observable Allocation View.

use crate::{ControlError, ControlPlane};
use domain::{
    AllocationPhase, AllocationViewSnapshot, ExecutionGroupId, ResourceAllocation, ResourceId,
    RoleId, TaskRef, TimestampMs,
};
use std::collections::BTreeMap;

/// Alias retained for callers that want to name allocation-specific Control failures.
pub type AllocationProjectionError = ControlError;

/// Expected ownership for one Group-scoped resource before projection.
#[derive(Debug, Clone, PartialEq, Eq)]
struct ExpectedOwnership {
    /// Task expected to own the reservation.
    task_ref: TaskRef,
    /// Role expected to own the reservation.
    role_id: RoleId,
}

impl ControlPlane {
    /// Builds a complete read-only projection from current Control allocation authority.
    pub fn allocation_snapshot(
        &self,
        projected_at: TimestampMs,
    ) -> Result<AllocationViewSnapshot, ControlError> {
        let active = self.active_allocation_ownership()?;
        let pending = self.pending_allocation_ownership()?;
        let mut allocations = Vec::with_capacity(self.reservations.len());

        for (resource_id, reservation) in &self.reservations {
            let (phase, group_id) = match &reservation.group_id {
                None => (AllocationPhase::Committed, None),
                Some(group_id) => {
                    let group = self.groups.get(group_id).ok_or_else(|| {
                        ControlError::AllocationInvariant(format!(
                            "reservation {resource_id} references unknown group {group_id}"
                        ))
                    })?;
                    let task_known = group.task_ref() == &reservation.task_ref
                        || group.task_execution(&reservation.task_ref).is_some();
                    if !task_known {
                        return Err(ControlError::AllocationInvariant(format!(
                            "reservation {resource_id} task differs from group {group_id}"
                        )));
                    }
                    let key = (group_id.clone(), resource_id.clone());
                    let active_owner = active.get(&key);
                    let pending_owner = pending.get(&key);
                    match (active_owner, pending_owner) {
                        (Some(owner), None) => {
                            validate_expected_owner(resource_id, reservation, owner)?;
                            (AllocationPhase::Bound, Some(group_id.clone()))
                        }
                        (None, Some(owner)) => {
                            validate_expected_owner(resource_id, reservation, owner)?;
                            (AllocationPhase::RecoveryPending, Some(group_id.clone()))
                        }
                        (Some(_), Some(_)) => {
                            return Err(ControlError::AllocationInvariant(format!(
                                "reservation {resource_id} is both bound and recovery pending"
                            )));
                        }
                        (None, None) => {
                            return Err(ControlError::AllocationInvariant(format!(
                                "reservation {resource_id} is orphaned from group {group_id}"
                            )));
                        }
                    }
                }
            };
            allocations.push(ResourceAllocation::new(
                resource_id.clone(),
                reservation.task_ref.clone(),
                reservation.role_id.clone(),
                group_id,
                phase,
            ));
        }

        self.ensure_expected_resources_have_reservations(&active)?;
        self.ensure_expected_resources_have_reservations(&pending)?;
        Ok(AllocationViewSnapshot::new(projected_at, allocations))
    }

    /// Collects resources currently present in Execution Group assignments.
    fn active_allocation_ownership(
        &self,
    ) -> Result<BTreeMap<(ExecutionGroupId, ResourceId), ExpectedOwnership>, ControlError> {
        let mut ownership = BTreeMap::new();
        for (group_id, group) in &self.groups {
            for assignment in group.assignments() {
                for resource_id in assignment.resource_ids() {
                    let key = (group_id.clone(), resource_id.clone());
                    if ownership
                        .insert(
                            key,
                            ExpectedOwnership {
                                task_ref: group.task_ref().clone(),
                                role_id: assignment.role_id().clone(),
                            },
                        )
                        .is_some()
                    {
                        return Err(ControlError::AllocationInvariant(format!(
                            "group {group_id} binds resource {resource_id} more than once"
                        )));
                    }
                }
            }
            for execution in group.task_executions() {
                for assignment in execution.assignments() {
                    for resource_id in assignment.resource_ids() {
                        let key = (group_id.clone(), resource_id.clone());
                        if ownership
                            .insert(
                                key,
                                ExpectedOwnership {
                                    task_ref: execution.task_ref().clone(),
                                    role_id: assignment.role_id().clone(),
                                },
                            )
                            .is_some()
                        {
                            return Err(ControlError::AllocationInvariant(format!(
                                "group {group_id} binds resource {resource_id} more than once"
                            )));
                        }
                    }
                }
            }
        }
        Ok(ownership)
    }

    /// Collects resources committed for recovery but not yet consumed by Rebind.
    fn pending_allocation_ownership(
        &self,
    ) -> Result<BTreeMap<(ExecutionGroupId, ResourceId), ExpectedOwnership>, ControlError> {
        let mut ownership = BTreeMap::new();
        for ((group_id, task_ref, role_id), commitment) in &self.pending_recovery_commitments {
            if commitment.group_id() != group_id
                || commitment.task_ref() != task_ref
                || commitment.role_id() != role_id
            {
                return Err(ControlError::AllocationInvariant(format!(
                    "pending commitment key differs from group {group_id}, role {role_id}"
                )));
            }
            let group = self.groups.get(group_id).ok_or_else(|| {
                ControlError::AllocationInvariant(format!(
                    "pending commitment references unknown group {group_id}"
                ))
            })?;
            if group.task_ref() != commitment.task_ref()
                && group.task_execution(commitment.task_ref()).is_none()
            {
                return Err(ControlError::AllocationInvariant(format!(
                    "pending commitment task differs from group {group_id}"
                )));
            }
            for resource_id in commitment.committed_resource_ids() {
                let key = (group_id.clone(), resource_id.clone());
                if ownership
                    .insert(
                        key,
                        ExpectedOwnership {
                            task_ref: commitment.task_ref().clone(),
                            role_id: role_id.clone(),
                        },
                    )
                    .is_some()
                {
                    return Err(ControlError::AllocationInvariant(format!(
                        "pending commitments duplicate resource {resource_id}"
                    )));
                }
            }
        }
        Ok(ownership)
    }

    /// Ensures every expected Group resource has an authoritative reservation.
    fn ensure_expected_resources_have_reservations(
        &self,
        ownership: &BTreeMap<(ExecutionGroupId, ResourceId), ExpectedOwnership>,
    ) -> Result<(), ControlError> {
        for ((group_id, resource_id), owner) in ownership {
            let reservation = self.reservations.get(resource_id).ok_or_else(|| {
                ControlError::AllocationInvariant(format!(
                    "group {group_id} resource {resource_id} lacks reservation authority"
                ))
            })?;
            if reservation.group_id.as_ref() != Some(group_id) {
                return Err(ControlError::AllocationInvariant(format!(
                    "resource {resource_id} reservation lacks group {group_id} ownership"
                )));
            }
            validate_expected_owner(resource_id, reservation, owner)?;
        }
        Ok(())
    }
}

/// Verifies reservation task/role ownership against normalized expected ownership.
fn validate_expected_owner(
    resource_id: &ResourceId,
    reservation: &crate::coordination::Reservation,
    expected: &ExpectedOwnership,
) -> Result<(), ControlError> {
    if reservation.task_ref != expected.task_ref || reservation.role_id != expected.role_id {
        return Err(ControlError::AllocationInvariant(format!(
            "resource {resource_id} reservation task/role ownership is inconsistent"
        )));
    }
    Ok(())
}
