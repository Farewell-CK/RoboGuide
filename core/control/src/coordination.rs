//! Shared resource coordination, reservation authority, and normal Commit.

use crate::{AssignmentProposal, ControlError, ControlPlane};
use domain::{
    CorrelationId, EventPayload, ExecutionGroupId, RoleAssignment, RoleId, TaskId, TaskRef,
    TimestampMs,
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
#[derive(Debug, Clone)]
pub(crate) struct Reservation {
    /// Mission-scoped task currently holding the resource.
    pub(crate) task_ref: TaskRef,
    /// Role currently holding the resource.
    pub(crate) role_id: RoleId,
    /// Group currently owning the binding after creation, if any.
    pub(crate) group_id: Option<ExecutionGroupId>,
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
}
