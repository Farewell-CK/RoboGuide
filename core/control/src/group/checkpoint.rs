//! Execution Group checkpoint authority validation.

use super::*;

impl ControlPlane {
    /// Validates checkpointed Group role metadata against bindings and recovery authority.
    pub(crate) fn validate_group_checkpoint_authority(&self) -> Result<(), ControlError> {
        for group in self.groups.values() {
            if group.task_ref.mission_id() != &group.mission_id {
                return Err(ControlError::InvalidProposal(
                    "checkpoint Group task belongs to another Mission".to_string(),
                ));
            }
            if !group.task_executions.is_empty() {
                self.validate_mission_group_checkpoint(group)?;
            } else {
                self.validate_legacy_group_checkpoint(group)?;
            }
        }
        for commitment in self.pending_recovery_commitments.values() {
            let group = self.groups.get(commitment.group_id()).ok_or_else(|| {
                ControlError::InvalidProposal(
                    "checkpoint recovery commitment references an unknown Group".to_string(),
                )
            })?;
            let role = group.role_requirement(commitment.task_ref(), commitment.role_id());
            if !group.task_executions.is_empty() && role.is_none() {
                return Err(ControlError::InvalidProposal(
                    "checkpoint recovery commitment lacks authoritative role metadata".to_string(),
                ));
            }
            if let Some(actor_id) = role.and_then(RoleRequirement::actor_id)
                && self
                    .actor_authority_node(commitment.task_ref().mission_id(), actor_id)
                    .is_none_or(|node_id| node_id != commitment.replacement_node_id())
            {
                return Err(ControlError::InvalidProposal(
                    "checkpoint recovery commitment violates Actor authority".to_string(),
                ));
            }
        }
        Ok(())
    }

    /// Validates complete Task/Role coverage and Actor assignments for one Mission-level Group.
    fn validate_mission_group_checkpoint(
        &self,
        group: &ExecutionGroup,
    ) -> Result<(), ControlError> {
        if !group.assignments.is_empty() || !group.unbound_roles.is_empty() {
            return Err(ControlError::InvalidProposal(
                "Mission-level Group checkpoint contains legacy role state".to_string(),
            ));
        }
        for (task_ref, execution) in &group.task_executions {
            if task_ref != execution.task_ref() || task_ref.mission_id() != &group.mission_id {
                return Err(ControlError::InvalidProposal(
                    "checkpoint TaskExecution identity differs from its Group key".to_string(),
                ));
            }
            let expected_roles = execution
                .role_scopes()
                .keys()
                .collect::<std::collections::BTreeSet<_>>();
            if execution
                .context_roles()
                .keys()
                .any(|role_id| !expected_roles.contains(role_id))
            {
                return Err(ControlError::InvalidProposal(
                    "checkpoint TaskExecution continuity references an unknown role".to_string(),
                ));
            }
            let authoritative_roles = group
                .role_requirements
                .iter()
                .filter(|((requirement_task, _), _)| requirement_task == task_ref)
                .map(|((_, role_id), _)| role_id)
                .collect::<std::collections::BTreeSet<_>>();
            if expected_roles != authoritative_roles {
                return Err(ControlError::InvalidProposal(
                    "checkpoint TaskExecution lacks exact authoritative role metadata".to_string(),
                ));
            }
            let mut represented_roles = std::collections::BTreeSet::new();
            for assignment in execution.assignments() {
                if !represented_roles.insert(assignment.role_id())
                    || !authoritative_roles.contains(assignment.role_id())
                {
                    return Err(ControlError::InvalidProposal(
                        "checkpoint TaskExecution contains an unknown or duplicate assignment"
                            .to_string(),
                    ));
                }
                self.validate_checkpoint_actor_assignment(
                    group,
                    task_ref,
                    assignment.role_id(),
                    assignment.node_id(),
                )?;
            }
            for ((unbound_task, role_id), unbound) in &group.task_unbound_roles {
                if unbound_task == task_ref {
                    if !represented_roles.insert(role_id) || !authoritative_roles.contains(role_id)
                    {
                        return Err(ControlError::InvalidProposal(
                            "checkpoint Task recovery contains an unknown or duplicate role"
                                .to_string(),
                        ));
                    }
                    self.validate_checkpoint_actor_assignment(
                        group,
                        task_ref,
                        role_id,
                        &unbound.previous_node_id,
                    )?;
                }
            }
            self.validate_task_checkpoint_coverage(
                group,
                execution,
                &authoritative_roles,
                &represented_roles,
            )?;
        }
        for ((task_ref, role_id), requirement) in &group.role_requirements {
            if requirement.role_id() != role_id
                || group.task_execution(task_ref).is_none()
                || task_ref.mission_id() != &group.mission_id
            {
                return Err(ControlError::InvalidProposal(
                    "checkpoint Group contains orphan authoritative role metadata".to_string(),
                ));
            }
        }
        for (task_ref, role_id) in group.task_unbound_roles.keys() {
            if group.role_requirement(task_ref, role_id).is_none() {
                return Err(ControlError::InvalidProposal(
                    "checkpoint Group contains orphan Task recovery state".to_string(),
                ));
            }
        }
        Ok(())
    }

    /// Enforces lifecycle-specific assignment coverage for one restored TaskExecution.
    fn validate_task_checkpoint_coverage(
        &self,
        group: &ExecutionGroup,
        execution: &TaskExecution,
        authoritative_roles: &std::collections::BTreeSet<&RoleId>,
        represented_roles: &std::collections::BTreeSet<&RoleId>,
    ) -> Result<(), ControlError> {
        if group.lifecycle == GroupLifecycle::Released {
            if !represented_roles.is_empty() {
                return Err(ControlError::InvalidProposal(
                    "Released Mission Group retains TaskExecution bindings".to_string(),
                ));
            }
            return Ok(());
        }
        let exact = represented_roles == authoritative_roles;
        let empty = represented_roles.is_empty();
        let has_task_recovery = group
            .task_unbound_roles
            .keys()
            .any(|(task_ref, _)| task_ref == execution.task_ref());
        let valid = match execution.lifecycle() {
            TaskExecutionLifecycle::Pending => empty,
            // A Ready Task is either waiting for its first Commit or has a complete committed
            // binding that has not been activated yet.
            TaskExecutionLifecycle::Ready => !has_task_recovery && (empty || exact),
            // Active/Blocked execution must account for every role, whether bound or awaiting
            // the externally decided recovery rebind.
            TaskExecutionLifecycle::Active | TaskExecutionLifecycle::Blocked => exact,
            // Successful local execution retains its exact bindings until Orchestration accepts
            // Task satisfaction and performs Task-scoped release.
            TaskExecutionLifecycle::AwaitingSatisfaction => !has_task_recovery && exact,
            // Terminal Task history may retain Context-scoped role assignments until Context or
            // Group release, so role coverage is intentionally not required here.
            TaskExecutionLifecycle::Completed => !has_task_recovery,
            TaskExecutionLifecycle::Failed | TaskExecutionLifecycle::Cancelled => exact,
        };
        if !valid {
            return Err(ControlError::InvalidProposal(format!(
                "checkpoint TaskExecution {} has invalid assignment coverage for {:?}",
                execution.task_ref(),
                execution.lifecycle()
            )));
        }
        Ok(())
    }

    /// Validates the optional metadata retained by a legacy single-Task Group.
    fn validate_legacy_group_checkpoint(&self, group: &ExecutionGroup) -> Result<(), ControlError> {
        for ((task_ref, role_id), requirement) in &group.role_requirements {
            if task_ref != &group.task_ref || role_id != requirement.role_id() {
                return Err(ControlError::InvalidProposal(
                    "legacy Group checkpoint contains mismatched role metadata".to_string(),
                ));
            }
        }
        for assignment in &group.assignments {
            if group.role_requirements.is_empty() {
                continue;
            }
            self.validate_checkpoint_actor_assignment(
                group,
                &group.task_ref,
                assignment.role_id(),
                assignment.node_id(),
            )?;
        }
        for (role_id, unbound) in &group.unbound_roles {
            if !group.role_requirements.is_empty() {
                self.validate_checkpoint_actor_assignment(
                    group,
                    &group.task_ref,
                    role_id,
                    &unbound.previous_node_id,
                )?;
            }
        }
        Ok(())
    }

    /// Confirms one current or released assignment remains on its authoritative Actor node.
    fn validate_checkpoint_actor_assignment(
        &self,
        group: &ExecutionGroup,
        task_ref: &TaskRef,
        role_id: &RoleId,
        node_id: &NodeId,
    ) -> Result<(), ControlError> {
        let role = group.role_requirement(task_ref, role_id).ok_or_else(|| {
            ControlError::InvalidProposal(
                "checkpoint assignment lacks authoritative role metadata".to_string(),
            )
        })?;
        let Some(actor_id) = role.actor_id() else {
            return Ok(());
        };
        if self
            .actor_authority_node(task_ref.mission_id(), actor_id)
            .is_none_or(|authority_node| authority_node != node_id)
        {
            return Err(ControlError::InvalidProposal(
                "checkpoint assignment violates Actor binding or placement authority".to_string(),
            ));
        }
        Ok(())
    }
}
