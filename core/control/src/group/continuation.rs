//! Same-owner Group continuation revalidates retained commitments without partial release.

use super::*;
use domain::{ExecutionCommand, ExecutionCouplingMode};
use ports::SharedNodeStateReader;

/// Non-authoritative choice to keep the original bindings of exactly the stopped logical slots.
#[derive(Debug, Clone, PartialEq)]
pub struct GroupContinuationProposal {
    /// Original Mission-level Group, unchanged by continuation.
    group_id: ExecutionGroupId,
    /// Explicit bounded recovery round supplied by the composition.
    recovery_id: String,
    /// Exact frozen original commands; no replacement node selector is exposed.
    commands: Vec<ExecutionCommand>,
    /// Original assignments used to reject intervening resource or binding changes.
    assignments: Vec<(TaskRef, RoleAssignment)>,
}

/// Control revalidation result; it neither creates new reservations nor grants repeat permission.
#[derive(Debug, Clone)]
pub struct CommittedGroupContinuation {
    /// Fully revalidated proposal, consumed by the composition's atomic new-attempt preparation.
    proposal: GroupContinuationProposal,
}

impl CommittedGroupContinuation {
    /// Returns only the original immutable commands covered by this Control decision.
    pub fn commands(&self) -> &[ExecutionCommand] {
        &self.proposal.commands
    }
}

impl ControlPlane {
    /// Reports current continuation admission without events, mutation or a new commitment.
    pub fn group_continuation_admission<S: SharedNodeStateReader>(
        &self,
        state: &S,
        group_id: &ExecutionGroupId,
        commands: &[ExecutionCommand],
        now: TimestampMs,
    ) -> Result<(), ControlError> {
        self.validate_group_continuation(state, group_id, commands, now)
            .map(|_| ())
    }

    /// Proposes retained binding reuse after the composition proves the entire Group stopped.
    ///
    /// This preserves all reservations, checks current eligibility, and never picks another Node.
    #[allow(clippy::too_many_arguments)]
    pub fn propose_group_continuation<S: SharedNodeStateReader, E: EventSink>(
        &self,
        state: &S,
        group_id: &ExecutionGroupId,
        recovery_id: &str,
        commands: Vec<ExecutionCommand>,
        now: TimestampMs,
        correlation: &CorrelationId,
        events: &mut E,
    ) -> Result<GroupContinuationProposal, ControlError> {
        let assignments = self.validate_group_continuation(state, group_id, &commands, now)?;
        let proposal = GroupContinuationProposal {
            group_id: group_id.clone(),
            recovery_id: recovery_id.into(),
            commands,
            assignments,
        };
        events.append(
            now,
            correlation,
            None,
            EventPayload::GroupContinuationProposed {
                group_id: group_id.clone(),
                recovery_id: recovery_id.into(),
                task_roles: proposal
                    .commands
                    .iter()
                    .map(|command| (command.task_ref().clone(), command.role_id().clone()))
                    .collect(),
            },
        );
        Ok(proposal)
    }

    /// Commits unchanged binding reuse only after rechecking the complete current State and owners.
    pub fn commit_group_continuation<S: SharedNodeStateReader, E: EventSink>(
        &mut self,
        state: &S,
        proposal: &GroupContinuationProposal,
        now: TimestampMs,
        correlation: &CorrelationId,
        events: &mut E,
    ) -> Result<CommittedGroupContinuation, ControlError> {
        let assignments =
            self.validate_group_continuation(state, &proposal.group_id, &proposal.commands, now)?;
        if assignments != proposal.assignments {
            return Err(ControlError::InvalidProposal(
                "retained Group bindings changed after proposal".into(),
            ));
        }
        events.append(
            now,
            correlation,
            None,
            EventPayload::GroupContinuationCommitted {
                group_id: proposal.group_id.clone(),
                recovery_id: proposal.recovery_id.clone(),
                assignments,
            },
        );
        Ok(CommittedGroupContinuation {
            proposal: proposal.clone(),
        })
    }

    /// Checks exact independent slots, original physical Actor authority, and every reserved resource.
    fn validate_group_continuation<S: SharedNodeStateReader>(
        &self,
        state: &S,
        group_id: &ExecutionGroupId,
        commands: &[ExecutionCommand],
        now: TimestampMs,
    ) -> Result<Vec<(TaskRef, RoleAssignment)>, ControlError> {
        let invalid = |reason: &str| ControlError::InvalidProposal(reason.into());
        let group = self
            .groups
            .get(group_id)
            .ok_or_else(|| ControlError::UnknownGroup(group_id.clone()))?;
        if !matches!(
            group.lifecycle,
            GroupLifecycle::Bound | GroupLifecycle::Active | GroupLifecycle::Adapted
        ) || commands.is_empty()
            || commands.len() > 32
        {
            return Err(invalid(
                "retained Group continuation requires a live complete bounded choice",
            ));
        }
        let mut slots = BTreeSet::new();
        let mut assignments = Vec::new();
        for command in commands {
            if command.group_id() != group_id
                || command.mission_id() != group.mission_id()
                || !slots.insert((command.task_ref().clone(), command.role_id().clone()))
            {
                return Err(invalid(
                    "continuation command duplicates a slot or crosses Group identity",
                ));
            }
            let task = group
                .task_execution(command.task_ref())
                .ok_or_else(|| invalid("continuation Task is absent"))?;
            let role = group
                .role_requirement(command.task_ref(), command.role_id())
                .ok_or_else(|| invalid("continuation Role is absent"))?;
            if !matches!(
                task.lifecycle(),
                TaskExecutionLifecycle::Ready | TaskExecutionLifecycle::Active
            ) || task.coupling_mode() != ExecutionCouplingMode::Independent
                || !self.node_is_eligible_for_role_operation(
                    state,
                    command.node_id(),
                    role,
                    command.intent().operation(),
                    now,
                )
            {
                return Err(invalid(
                    "continuation requires current eligibility and independent Task execution",
                ));
            }
            let assignment = task
                .assignments()
                .iter()
                .find(|assignment| {
                    assignment.role_id() == command.role_id()
                        && assignment.node_id() == command.node_id()
                })
                .ok_or_else(|| invalid("continuation cannot change the original owner"))?;
            let node = state
                .node(command.node_id())
                .ok_or_else(|| invalid("continuation Node is absent"))?;
            if command.recovery_support().is_none_or(|original| {
                original.support.operation != *command.intent().operation()
                    || !original.support.supports_group_continuation()
                    || node
                        .registration()
                        .execution_recovery_support(command.intent().operation())
                        .as_ref()
                        != Some(original)
            }) {
                return Err(invalid(
                    "continuation owner recovery support changed or is unsupported",
                ));
            }
            if let Some(actor_id) = role.actor_id() {
                let bound = self
                    .actor_binding(command.mission_id(), actor_id)
                    .ok_or_else(|| invalid("continuation Actor has no binding"))?;
                let physical = self.validate_actor_binding_intent(
                    command.mission_id(),
                    actor_id,
                    command.node_id(),
                )?;
                if bound.node_id() != command.node_id()
                    || physical.as_ref().is_some_and(|(entity, registry, _)| {
                        bound.physical_entity_id() != Some(entity)
                            || bound.registry_id() != Some(registry)
                    })
                {
                    return Err(invalid("continuation Actor physical identity changed"));
                }
            }
            crate::reconciliation::validate_recovery_resources(
                node,
                role,
                assignment.resource_ids(),
            )?;
            for resource in assignment.resource_ids() {
                let reservation = self
                    .reservations
                    .get(resource)
                    .ok_or_else(|| invalid("retained continuation resource was released"))?;
                let owner = match reservation.scope {
                    ResourceBindingScope::Task => {
                        reservation.task_ref == *command.task_ref()
                            && reservation.role_id == *command.role_id()
                    }
                    ResourceBindingScope::Context => group.context_bindings().any(|binding| {
                        binding.context_id() == task.context_id()
                            && task.context_roles().get(command.role_id())
                                == Some(binding.context_role_id())
                            && binding.assignment().node_id() == command.node_id()
                            && binding.assignment().resource_ids().contains(resource)
                            && &reservation.task_ref == binding.origin_task_ref()
                            && &reservation.role_id == binding.assignment().role_id()
                    }),
                };
                if reservation.group_id.as_ref() != Some(group_id) || !owner {
                    return Err(invalid("retained continuation resource ownership changed"));
                }
            }
            assignments.push((command.task_ref().clone(), assignment.clone()));
        }
        Ok(assignments)
    }
}
