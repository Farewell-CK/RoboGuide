//! Shared resource coordination, reservation authority, and normal Commit.

use crate::{AssignmentProposal, ControlError, ControlPlane};
use domain::{
    AllocationOwner, CorrelationId, EventPayload, ExecutionGroupId, ResourceBindingScope,
    RoleAssignment, RoleId, TaskId, TaskRef, TimestampMs,
};
use ports::{EventSink, SharedNodeStateReader};

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
    /// Commits a compatibility proposal that carries no independent operation constraint.
    pub fn commit<E: EventSink>(
        &mut self,
        proposal: &AssignmentProposal,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<CommittedPlan, ControlError> {
        if proposal.requires_operation_validation() {
            return Err(ControlError::InvalidProposal(
                "normalized Mission proposal requires operation-aware Commit".to_string(),
            ));
        }
        self.commit_validated(proposal, timestamp, correlation_id, events)
    }

    /// Revalidates current operation support before atomically committing normal resources.
    pub fn commit_with_state<S: SharedNodeStateReader, E: EventSink>(
        &mut self,
        state: &S,
        proposal: &AssignmentProposal,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<CommittedPlan, ControlError> {
        self.validate_current_assignment_support(state, proposal, timestamp)?;
        self.commit_validated(proposal, timestamp, correlation_id, events)
    }

    /// Applies normal reservation authority after all operation constraints are validated.
    fn commit_validated<E: EventSink>(
        &mut self,
        proposal: &AssignmentProposal,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<CommittedPlan, ControlError> {
        if self.scheduled_task(proposal.task_ref()).is_some() {
            return Err(ControlError::InvalidProposal(
                "a scheduled Task must commit through its owning Execution Group".to_string(),
            ));
        }
        for assignment in proposal.assignments() {
            for resource_id in assignment.resource_ids() {
                if let Some(reservation) = self.reservations.get(resource_id) {
                    return Err(ControlError::ResourceConflict {
                        resource_id: resource_id.clone(),
                        owner_task_ref: reservation.task_ref.clone(),
                        owner_role_id: reservation.role_id.clone(),
                    });
                }
                if let Some((owner_task_ref, owner_role_id)) =
                    self.scheduled_resource_conflict(resource_id, proposal.task_ref())
                {
                    return Err(ControlError::ResourceConflict {
                        resource_id: resource_id.clone(),
                        owner_task_ref,
                        owner_role_id,
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

    /// Commits a compatibility ready-Task proposal without an independent operation constraint.
    pub fn commit_for_group<E: EventSink>(
        &mut self,
        group_id: &ExecutionGroupId,
        proposal: &AssignmentProposal,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<CommittedPlan, ControlError> {
        if proposal.requires_operation_validation() {
            return Err(ControlError::InvalidProposal(
                "normalized Mission proposal requires operation-aware Group Commit".to_string(),
            ));
        }
        self.commit_for_group_validated(group_id, proposal, timestamp, correlation_id, events)
    }

    /// Revalidates operation support before committing a normalized ready Task to its Group.
    #[allow(clippy::too_many_arguments)]
    pub fn commit_for_group_with_state<S: SharedNodeStateReader, E: EventSink>(
        &mut self,
        state: &S,
        group_id: &ExecutionGroupId,
        proposal: &AssignmentProposal,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<CommittedPlan, ControlError> {
        self.validate_current_assignment_support(state, proposal, timestamp)?;
        self.commit_for_group_validated(group_id, proposal, timestamp, correlation_id, events)
    }

    /// Applies Group-scoped reservation authority after operation validation succeeds.
    fn commit_for_group_validated<E: EventSink>(
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
        if let Some(scheduled) = self.scheduled_task(proposal.task_ref())
            && (scheduled.group_id() != group_id
                || scheduled.phase() != crate::SchedulingReservationPhase::Scheduled
                || scheduled.decision().starts_at() > timestamp
                || scheduled
                    .decision()
                    .ends_at()
                    .is_some_and(|ends_at| timestamp >= ends_at)
                || scheduled
                    .decision()
                    .latest_activation_at()
                    .is_some_and(|latest| timestamp > latest)
                || scheduled.decision().proposed_assignments() != proposal.assignments())
        {
            return Err(ControlError::InvalidProposal(
                "scheduled Task commit does not match its live Group decision and activation window"
                    .to_string(),
            ));
        }
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
                if let Some((owner_task_ref, owner_role_id)) =
                    self.scheduled_resource_conflict(resource_id, proposal.task_ref())
                {
                    return Err(ControlError::ResourceConflict {
                        resource_id: resource_id.clone(),
                        owner_task_ref,
                        owner_role_id,
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

impl ControlPlane {
    /// Revalidates capability, health, lease, and operation support before Commit mutation.
    fn validate_current_assignment_support<S: SharedNodeStateReader>(
        &self,
        state: &S,
        proposal: &AssignmentProposal,
        timestamp: TimestampMs,
    ) -> Result<(), ControlError> {
        for assignment in proposal.assignments() {
            let Some(operation) = proposal.operation_for_role(assignment.role_id()) else {
                continue;
            };
            let requirement = proposal
                .requirement_for_role(assignment.role_id())
                .ok_or_else(|| {
                    ControlError::InvalidProposal(format!(
                        "proposal lacks requirement for role {}",
                        assignment.role_id()
                    ))
                })?;
            if !self.node_is_eligible_for_role_operation(
                state,
                assignment.node_id(),
                requirement,
                operation,
                timestamp,
            ) {
                return Err(ControlError::InvalidProposal(format!(
                    "node {} no longer satisfies capability and operation {} for role {}",
                    assignment.node_id(),
                    operation,
                    assignment.role_id()
                )));
            }
        }
        Ok(())
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
