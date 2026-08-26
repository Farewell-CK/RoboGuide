//! Shared resource coordination, reservation authority, and normal Commit.

use crate::{AssignmentProposal, ControlError, ControlPlane};
use domain::{
    AllocationOwner, CorrelationId, EventPayload, ExecutionGroupId, ResourceBindingScope,
    RoleAssignment, RoleId, TaskId, TaskRef, TimestampMs,
};
use ports::EventSink;

/// A proposal whose resources are now system-recognized commitments.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CommittedPlan {
    /// Mission-scoped task represented by this committed plan.
    task_ref: TaskRef,
    /// Resource-checked assignments accepted by coordination.
    assignments: Vec<RoleAssignment>,
}

impl CommittedPlan {
    /// Creates a committed plan after reservation checks succeed.
    pub(crate) fn new(task_ref: TaskRef, assignments: Vec<RoleAssignment>) -> Self {
        Self {
            task_ref,
            assignments,
        }
    }

    /// Returns the complete mission-scoped task identity.
    pub const fn task_ref(&self) -> &TaskRef {
        &self.task_ref
    }

    /// Returns the committed task identity.
    pub const fn task_id(&self) -> &TaskId {
        self.task_ref.task_id()
    }

    /// Returns committed role assignments.
    pub fn assignments(&self) -> &[RoleAssignment] {
        &self.assignments
    }
}

/// The task and role that currently hold a resource commitment.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub(crate) struct Reservation {
    /// Mission-scoped task currently holding the resource.
    pub(crate) task_ref: TaskRef,
    /// Role currently holding the resource.
    pub(crate) role_id: RoleId,
    /// Group currently owning the binding after creation, if any.
    pub(crate) group_id: Option<ExecutionGroupId>,
    /// Lifetime of the reservation inside its Mission-level Group.
    #[serde(default)]
    pub(crate) scope: ResourceBindingScope,
    /// Explicit Task or Context ownership authority.
    pub(crate) owner: AllocationOwner,
}

impl ControlPlane {
    /// Commits all proposal resources atomically from the Control view.
    pub fn commit<E: EventSink>(
        &mut self,
        proposal: &AssignmentProposal,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<CommittedPlan, ControlError> {
        for assignment in proposal.assignments() {
            for resource_id in assignment.resource_ids() {
                if let Some(reservation) = self.reservations.get(resource_id) {
                    return Err(ControlError::ResourceConflict {
                        resource_id: resource_id.clone(),
                        owner_task_ref: reservation.task_ref.clone(),
                        owner_role_id: reservation.role_id.clone(),
                    });
                }
            }
        }

        for assignment in proposal.assignments() {
            for resource_id in assignment.resource_ids() {
                self.reservations.insert(
                    resource_id.clone(),
                    Reservation {
                        task_ref: proposal.task_ref().clone(),
                        role_id: assignment.role_id().clone(),
                        group_id: None,
                        scope: ResourceBindingScope::Task,
                        owner: AllocationOwner::Task(proposal.task_ref().clone()),
                    },
                );
            }
        }

        let plan = CommittedPlan::new(proposal.task_ref().clone(), proposal.assignments().to_vec());
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::PlanCommitted {
                task_ref: proposal.task_ref().clone(),
            },
        );
        Ok(plan)
    }

    /// Commits a ready Task proposal using its declared Task or Context resource ownership.
    pub fn commit_for_group<E: EventSink>(
        &mut self,
        group_id: &ExecutionGroupId,
        proposal: &AssignmentProposal,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<CommittedPlan, ControlError> {
        let group = self
            .groups
            .get(group_id)
            .ok_or_else(|| ControlError::UnknownGroup(group_id.clone()))?;
        let execution = group.task_execution(proposal.task_ref()).ok_or_else(|| {
            ControlError::InvalidProposal("Task is absent from the Mission Group".to_string())
        })?;
        if execution.lifecycle() != domain::TaskExecutionLifecycle::Ready {
            return Err(ControlError::InvalidProposal(
                "only a ready Task can commit resources".to_string(),
            ));
        }
        validate_task_assignments(execution, proposal.assignments())?;
        let mut owners = Vec::new();
        for assignment in proposal.assignments() {
            let scope = *execution
                .role_scopes()
                .get(assignment.role_id())
                .expect("task assignment roles validated above");
            let owner = match scope {
                ResourceBindingScope::Task => AllocationOwner::Task(proposal.task_ref().clone()),
                ResourceBindingScope::Context => AllocationOwner::Context {
                    mission_id: proposal.task_ref().mission_id().clone(),
                    context_id: execution.context_id().clone(),
                    context_role_id: execution
                        .context_role(assignment.role_id())
                        .cloned()
                        .ok_or_else(|| {
                            ControlError::InvalidProposal(
                                "Context-scoped role has no ContextRole".to_string(),
                            )
                        })?,
                },
            };
            owners.push((assignment, scope, owner));
        }
        for (assignment, _, owner) in &owners {
            for resource_id in assignment.resource_ids() {
                if let Some(reservation) = self.reservations.get(resource_id)
                    && (&reservation.owner != owner
                        || reservation.group_id.as_ref() != Some(group_id))
                {
                    return Err(ControlError::ResourceConflict {
                        resource_id: resource_id.clone(),
                        owner_task_ref: reservation.task_ref.clone(),
                        owner_role_id: reservation.role_id.clone(),
                    });
                }
            }
        }
        for (assignment, scope, owner) in owners {
            for resource_id in assignment.resource_ids() {
                self.reservations
                    .entry(resource_id.clone())
                    .or_insert_with(|| Reservation {
                        task_ref: proposal.task_ref().clone(),
                        role_id: assignment.role_id().clone(),
                        group_id: None,
                        scope,
                        owner: owner.clone(),
                    });
            }
        }
        let plan = CommittedPlan::new(proposal.task_ref().clone(), proposal.assignments().to_vec());
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::PlanCommitted {
                task_ref: proposal.task_ref().clone(),
            },
        );
        Ok(plan)
    }
}

/// Rejects incomplete, duplicate, or unknown role assignments before reservation mutation.
fn validate_task_assignments(
    execution: &domain::TaskExecution,
    assignments: &[RoleAssignment],
) -> Result<(), ControlError> {
    let expected = execution
        .role_scopes()
        .keys()
        .collect::<std::collections::BTreeSet<_>>();
    let actual = assignments
        .iter()
        .map(RoleAssignment::role_id)
        .collect::<std::collections::BTreeSet<_>>();
    if expected != actual {
        return Err(ControlError::InvalidProposal(
            "committed assignments must exactly cover TaskExecution roles".to_string(),
        ));
    }
    if assignments.len() != actual.len() {
        return Err(ControlError::InvalidProposal(
            "committed assignments contain duplicate roles".to_string(),
        ));
    }
    Ok(())
}
