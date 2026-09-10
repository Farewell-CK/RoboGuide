//! Execution Group lifecycle and role-level recovery binding transitions.

use super::*;

impl ControlPlane {
    /// Creates a legacy single-Task Group and establishes first-use Actor bindings.
    ///
    /// New Mission execution must use [`Self::create_mission_group`] and bind TaskExecutions.
    #[cfg(test)]
    pub(crate) fn create_group_with_actor_bindings<E: EventSink>(
        &mut self,
        group_id: ExecutionGroupId,
        plan: &CommittedPlan,
        requirement: &TaskRequirement,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<ExecutionGroup, ControlError> {
        if plan.task_ref() != requirement.task_ref() {
            return Err(ControlError::InvalidProposal(
                "task requirement does not match committed plan".to_string(),
            ));
        }
        let mut actor_nodes = std::collections::BTreeMap::<ActorId, NodeId>::new();
        for role in requirement.roles() {
            let Some(actor_id) = role.actor_id() else {
                continue;
            };
            let assignment = plan
                .assignments()
                .iter()
                .find(|assignment| assignment.role_id() == role.role_id())
                .ok_or_else(|| {
                    ControlError::InvalidProposal(format!("missing role {}", role.role_id()))
                })?;
            if let Some(existing) = self.actor_binding(requirement.mission_id(), actor_id) {
                if existing.node_id() != assignment.node_id() {
                    return Err(ControlError::InvalidProposal(
                        "mission actor is already bound to another node".to_string(),
                    ));
                }
            } else if let Some(constraint) =
                self.actor_node_constraint(requirement.mission_id(), actor_id)
                && constraint.node_id() != assignment.node_id()
            {
                return Err(ControlError::InvalidProposal(
                    "actor assignment violates deployment placement constraint".to_string(),
                ));
            } else {
                let previous = actor_nodes.insert(actor_id.clone(), assignment.node_id().clone());
                if previous.is_some_and(|node| node != *assignment.node_id()) {
                    return Err(ControlError::InvalidProposal(
                        "one mission actor cannot bind multiple nodes in one Group".to_string(),
                    ));
                }
            }
        }
        self.create_group(group_id.clone(), plan, timestamp, correlation_id, events)?;
        let group = self
            .groups
            .get_mut(&group_id)
            .expect("Group was inserted by create_group");
        for role in requirement.roles() {
            group.role_requirements.insert(
                (requirement.task_ref().clone(), role.role_id().clone()),
                role.clone(),
            );
        }
        for (actor_id, node_id) in actor_nodes {
            self.record_actor_binding(
                requirement.mission_id().clone(),
                actor_id.clone(),
                node_id.clone(),
            )?;
            events.append(
                timestamp,
                correlation_id,
                None,
                EventPayload::MissionActorBound {
                    mission_id: requirement.mission_id().clone(),
                    actor_id,
                    node_id,
                    task_ref: requirement.task_ref().clone(),
                    group_id: group_id.clone(),
                },
            );
        }
        self.groups
            .get(&group_id)
            .cloned()
            .ok_or(ControlError::UnknownGroup(group_id))
    }

    /// Creates and binds a legacy single-Task Execution Group from a committed plan.
    ///
    /// New Mission execution must use [`Self::create_mission_group`] and bind TaskExecutions.
    #[cfg(test)]
    pub(crate) fn create_group<E: EventSink>(
        &mut self,
        group_id: ExecutionGroupId,
        plan: &CommittedPlan,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<ExecutionGroup, ControlError> {
        if self.groups.contains_key(&group_id) {
            return Err(ControlError::InvalidProposal(
                "execution group identity already exists".to_string(),
            ));
        }
        for assignment in plan.assignments() {
            for resource_id in assignment.resource_ids() {
                let reservation = self.reservations.get(resource_id).ok_or_else(|| {
                    ControlError::InvalidProposal(format!(
                        "committed resource {resource_id} has no reservation"
                    ))
                })?;
                if reservation.task_ref != *plan.task_ref()
                    || reservation.role_id != *assignment.role_id()
                    || reservation.group_id.is_some()
                {
                    return Err(ControlError::InvalidProposal(format!(
                        "committed resource {resource_id} cannot bind to group {group_id}"
                    )));
                }
            }
        }
        for assignment in plan.assignments() {
            for resource_id in assignment.resource_ids() {
                if let Some(reservation) = self.reservations.get_mut(resource_id) {
                    reservation.group_id = Some(group_id.clone());
                }
            }
        }
        let group = ExecutionGroup::new(group_id.clone(), plan);
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::ExecutionGroupBound {
                group_id: group_id.clone(),
                task_ref: plan.task_ref().clone(),
            },
        );
        self.groups.insert(group_id, group.clone());
        Ok(group)
    }

    /// Activates a newly bound or fully rebound group before role invocation.
    pub fn activate_group<E: EventSink>(
        &mut self,
        group_id: &ExecutionGroupId,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<(), ControlError> {
        let group = self
            .groups
            .get_mut(group_id)
            .ok_or_else(|| ControlError::UnknownGroup(group_id.clone()))?;
        if !matches!(
            group.lifecycle,
            GroupLifecycle::Bound | GroupLifecycle::Adapted
        ) {
            return Err(ControlError::InvalidLifecycle(group.lifecycle));
        }
        if !group.unbound_roles.is_empty() || !group.task_unbound_roles.is_empty() {
            return Err(ControlError::InvalidProposal(
                "execution group still has unbound roles".to_string(),
            ));
        }
        if !group.task_executions.is_empty()
            && !group.task_executions.values().any(|execution| {
                matches!(
                    execution.lifecycle(),
                    TaskExecutionLifecycle::Active
                        | TaskExecutionLifecycle::AwaitingSatisfaction
                        | TaskExecutionLifecycle::Completed
                )
            })
        {
            return Err(ControlError::InvalidProposal(
                "Mission Group requires an explicitly activated TaskExecution".to_string(),
            ));
        }
        group.lifecycle = GroupLifecycle::Active;
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::ExecutionGroupActivated {
                group_id: group_id.clone(),
                task_ref: group.task_ref.clone(),
            },
        );
        Ok(())
    }

    /// Releases only one role's current member and resource binding for recovery.
    pub fn release_role_binding<E: EventSink>(
        &mut self,
        group_id: &ExecutionGroupId,
        role_id: &RoleId,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<(), ControlError> {
        let group = self
            .groups
            .get(group_id)
            .ok_or_else(|| ControlError::UnknownGroup(group_id.clone()))?;
        if group.lifecycle != GroupLifecycle::Blocked {
            return Err(ControlError::InvalidLifecycle(group.lifecycle));
        }
        let assignment_index = group
            .assignments
            .iter()
            .position(|assignment| assignment.role_id() == role_id)
            .ok_or_else(|| {
                ControlError::InvalidProposal(format!(
                    "group has no active binding for role {role_id}"
                ))
            })?;
        let assignment = &group.assignments[assignment_index];
        let task_ref = group.task_ref.clone();
        let node_id = assignment.node_id().clone();
        let resource_ids = assignment.resource_ids().to_vec();
        for resource_id in &resource_ids {
            let reservation = self.reservations.get(resource_id).ok_or_else(|| {
                ControlError::InvalidProposal(format!(
                    "group {group_id} binding {resource_id} has no reservation"
                ))
            })?;
            if reservation.task_ref != task_ref
                || reservation.role_id != *role_id
                || reservation.group_id.as_ref() != Some(group_id)
            {
                return Err(ControlError::InvalidProposal(format!(
                    "group {group_id} does not own role reservation {resource_id}"
                )));
            }
        }
        for resource_id in &resource_ids {
            self.reservations.remove(resource_id);
        }
        let group = self
            .groups
            .get_mut(group_id)
            .ok_or_else(|| ControlError::UnknownGroup(group_id.clone()))?;
        group.assignments.remove(assignment_index);
        group.unbound_roles.insert(
            role_id.clone(),
            UnboundRole {
                previous_node_id: node_id.clone(),
                assignment_index,
            },
        );
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::ExecutionGroupRoleBindingReleased {
                group_id: group_id.clone(),
                task_ref,
                role_id: role_id.clone(),
                node_id,
                resource_ids,
            },
        );
        Ok(())
    }

    /// Releases one Task-local role binding without affecting sibling Task executions.
    pub fn release_task_role_binding<E: EventSink>(
        &mut self,
        group_id: &ExecutionGroupId,
        task_ref: &TaskRef,
        role_id: &RoleId,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<(), ControlError> {
        let group = self
            .groups
            .get(group_id)
            .ok_or_else(|| ControlError::UnknownGroup(group_id.clone()))?;
        if group.lifecycle != GroupLifecycle::Blocked {
            return Err(ControlError::InvalidLifecycle(group.lifecycle));
        }
        let execution = group.task_execution(task_ref).ok_or_else(|| {
            ControlError::InvalidProposal("Task execution is not registered".to_string())
        })?;
        let assignment_index = execution
            .assignments()
            .iter()
            .position(|assignment| assignment.role_id() == role_id)
            .ok_or_else(|| ControlError::InvalidProposal(format!("Task has no role {role_id}")))?;
        let assignment = &execution.assignments()[assignment_index];
        let node_id = assignment.node_id().clone();
        let resource_ids = assignment.resource_ids().to_vec();
        for resource_id in &resource_ids {
            let reservation = self.reservations.get(resource_id).ok_or_else(|| {
                ControlError::InvalidProposal(format!(
                    "Task binding {resource_id} has no reservation"
                ))
            })?;
            if reservation.task_ref != *task_ref
                || reservation.role_id != *role_id
                || reservation.group_id.as_ref() != Some(group_id)
            {
                return Err(ControlError::InvalidProposal(format!(
                    "Task {task_ref} does not own role reservation {resource_id}"
                )));
            }
            if reservation.scope == ResourceBindingScope::Context {
                return Err(ControlError::InvalidProposal(
                    "Context-scoped role bindings must end with their Context before recovery release"
                        .to_string(),
                ));
            }
        }
        for resource_id in &resource_ids {
            if self
                .reservations
                .get(resource_id)
                .is_some_and(|reservation| reservation.scope == ResourceBindingScope::Task)
            {
                self.reservations.remove(resource_id);
            }
        }
        let group = self
            .groups
            .get_mut(group_id)
            .expect("group validated above");
        let execution = group
            .task_executions
            .get_mut(task_ref)
            .expect("Task validated above");
        let mut assignments = execution.assignments().to_vec();
        assignments.remove(assignment_index);
        *execution = execution.with_assignments(assignments);
        group.task_unbound_roles.insert(
            (task_ref.clone(), role_id.clone()),
            UnboundRole {
                previous_node_id: node_id.clone(),
                assignment_index,
            },
        );
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::ExecutionGroupRoleBindingReleased {
                group_id: group_id.clone(),
                task_ref: task_ref.clone(),
                role_id: role_id.clone(),
                node_id,
                resource_ids,
            },
        );
        Ok(())
    }

    /// Rebinds one blocked role using resources already committed by coordination.
    pub fn rebind_role<E: EventSink>(
        &mut self,
        committed: &CommittedRecoveryAssignment,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<RecoveryOutcome, ControlError> {
        let group = self
            .groups
            .get(committed.group_id())
            .ok_or_else(|| ControlError::UnknownGroup(committed.group_id().clone()))?;
        if group.lifecycle != GroupLifecycle::Blocked {
            return Err(ControlError::InvalidLifecycle(group.lifecycle));
        }
        if group.task_ref != *committed.task_ref()
            && group.task_execution(committed.task_ref()).is_none()
        {
            return Err(ControlError::InvalidProposal(
                "committed recovery belongs to another task".to_string(),
            ));
        }
        self.validate_pending_recovery_commitment(committed)?;
        let unbound_role = group
            .unbound_roles
            .get(committed.role_id())
            .cloned()
            .or_else(|| {
                group
                    .task_unbound_roles
                    .get(&(committed.task_ref().clone(), committed.role_id().clone()))
                    .cloned()
            })
            .ok_or_else(|| {
                ControlError::InvalidProposal(format!(
                    "role {} is not unbound for committed rebind",
                    committed.role_id()
                ))
            })?;
        let previous_node = unbound_role.previous_node_id.clone();
        let assignment_index = unbound_role.assignment_index;
        if previous_node != *committed.previous_node_id()
            || committed.replacement_node_id() == committed.previous_node_id()
        {
            return Err(ControlError::InvalidProposal(
                "committed recovery does not match the released role binding".to_string(),
            ));
        }
        self.validate_recovery_commitment_reservations(committed)?;
        let replacement_assignment = RoleAssignment::new(
            committed.role_id().clone(),
            committed.replacement_node_id().clone(),
            committed.committed_resource_ids().to_vec(),
        );
        let group = self
            .groups
            .get_mut(committed.group_id())
            .ok_or_else(|| ControlError::UnknownGroup(committed.group_id().clone()))?;
        if group.task_execution(committed.task_ref()).is_some() {
            let execution = group
                .task_executions
                .get_mut(committed.task_ref())
                .expect("Task validated above");
            let insertion_index = assignment_index.min(execution.assignments().len());
            let mut assignments = execution.assignments().to_vec();
            assignments.insert(insertion_index, replacement_assignment);
            *execution = execution.with_assignments(assignments);
            group
                .task_unbound_roles
                .remove(&(committed.task_ref().clone(), committed.role_id().clone()));
        } else {
            let insertion_index = assignment_index.min(group.assignments.len());
            group
                .assignments
                .insert(insertion_index, replacement_assignment);
            group.unbound_roles.remove(committed.role_id());
        }
        group.lifecycle = GroupLifecycle::Adapted;
        self.pending_recovery_commitments.remove(&(
            committed.group_id().clone(),
            committed.task_ref().clone(),
            committed.role_id().clone(),
        ));
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::RecoveryRebound {
                group_id: committed.group_id().clone(),
                task_ref: committed.task_ref().clone(),
                role_id: committed.role_id().clone(),
                from_node: previous_node.clone(),
                to_node: committed.replacement_node_id().clone(),
            },
        );
        Ok(RecoveryOutcome::Recovered {
            group_id: committed.group_id().clone(),
            task_ref: committed.task_ref().clone(),
            role_id: committed.role_id().clone(),
            from_node: previous_node,
            to_node: committed.replacement_node_id().clone(),
        })
    }

    /// Marks a group complete after every registered TaskExecution succeeds.
    pub fn complete_group<E: EventSink>(
        &mut self,
        group_id: &ExecutionGroupId,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<(), ControlError> {
        let group = self
            .groups
            .get_mut(group_id)
            .ok_or_else(|| ControlError::UnknownGroup(group_id.clone()))?;
        if !matches!(
            group.lifecycle,
            GroupLifecycle::Active | GroupLifecycle::Adapted
        ) {
            return Err(ControlError::InvalidLifecycle(group.lifecycle));
        }
        if !group.unbound_roles.is_empty() || !group.task_unbound_roles.is_empty() {
            return Err(ControlError::InvalidProposal(
                "execution group still has unbound roles".to_string(),
            ));
        }
        if let Some(incomplete) = group
            .task_executions
            .values()
            .find(|execution| execution.lifecycle() != TaskExecutionLifecycle::Completed)
        {
            return Err(ControlError::InvalidProposal(format!(
                "TaskExecution {} is not completed",
                incomplete.task_ref()
            )));
        }
        group.lifecycle = GroupLifecycle::Completed;
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::ExecutionGroupCompleted {
                group_id: group_id.clone(),
                task_ref: group.task_ref.clone(),
            },
        );
        Ok(())
    }

    /// Marks a group blocked until reconciliation restores progress or declares failure.
    pub fn block_group<E: EventSink>(
        &mut self,
        group_id: &ExecutionGroupId,
        reason: impl Into<String>,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<(), ControlError> {
        let group = self
            .groups
            .get_mut(group_id)
            .ok_or_else(|| ControlError::UnknownGroup(group_id.clone()))?;
        if !matches!(
            group.lifecycle,
            GroupLifecycle::Bound | GroupLifecycle::Active | GroupLifecycle::Adapted
        ) {
            return Err(ControlError::InvalidLifecycle(group.lifecycle));
        }
        group.lifecycle = GroupLifecycle::Blocked;
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::ExecutionGroupBlocked {
                group_id: group_id.clone(),
                task_ref: group.task_ref.clone(),
                reason: reason.into(),
            },
        );
        Ok(())
    }

    /// Marks a blocked group terminally failed after recovery is explicitly exhausted.
    pub fn fail_group<E: EventSink>(
        &mut self,
        group_id: &ExecutionGroupId,
        reason: impl Into<String>,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<(), ControlError> {
        let group = self
            .groups
            .get_mut(group_id)
            .ok_or_else(|| ControlError::UnknownGroup(group_id.clone()))?;
        if group.lifecycle != GroupLifecycle::Blocked {
            return Err(ControlError::InvalidLifecycle(group.lifecycle));
        }
        group.lifecycle = GroupLifecycle::Failed;
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::ExecutionGroupFailed {
                group_id: group_id.clone(),
                task_ref: group.task_ref.clone(),
                reason: reason.into(),
            },
        );
        Ok(())
    }

    /// Releases every reservation and pending commitment owned by a terminal Group.
    pub fn release_group<E: EventSink>(
        &mut self,
        group_id: &ExecutionGroupId,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<(), ControlError> {
        let group = self
            .groups
            .get(group_id)
            .ok_or_else(|| ControlError::UnknownGroup(group_id.clone()))?;
        if !matches!(
            group.lifecycle,
            GroupLifecycle::Completed | GroupLifecycle::Failed
        ) {
            return Err(ControlError::InvalidLifecycle(group.lifecycle));
        }
        let task_ref = group.task_ref.clone();
        let mut expected_resources = BTreeMap::<ResourceId, RoleId>::new();
        for assignment in &group.assignments {
            for resource_id in assignment.resource_ids() {
                if expected_resources
                    .insert(resource_id.clone(), assignment.role_id().clone())
                    .is_some()
                {
                    return Err(ControlError::InvalidProposal(format!(
                        "group {group_id} has duplicate active resource {resource_id}"
                    )));
                }
            }
        }
        for execution in group.task_executions.values() {
            for assignment in execution.assignments() {
                for resource_id in assignment.resource_ids() {
                    if execution.binding_scope(resource_id) == ResourceBindingScope::Context {
                        continue;
                    }
                    if expected_resources
                        .insert(resource_id.clone(), assignment.role_id().clone())
                        .is_some()
                    {
                        return Err(ControlError::InvalidProposal(format!(
                            "group {group_id} has duplicate active resource {resource_id}"
                        )));
                    }
                }
            }
        }
        for binding in group.context_bindings.values() {
            for resource_id in binding.assignment().resource_ids() {
                if expected_resources
                    .insert(resource_id.clone(), binding.assignment().role_id().clone())
                    .is_some()
                {
                    return Err(ControlError::InvalidProposal(format!(
                        "group {group_id} has duplicate context resource {resource_id}"
                    )));
                }
            }
        }
        let pending_keys = self
            .pending_recovery_commitments
            .iter()
            .filter(|((pending_group_id, _, _), _)| pending_group_id == group_id)
            .map(|(key, committed)| {
                if committed.group_id() != group_id
                    || (committed.task_ref() != &task_ref
                        && group.task_execution(committed.task_ref()).is_none())
                    || committed.task_ref() != &key.1
                    || committed.role_id() != &key.2
                {
                    return Err(ControlError::InvalidProposal(format!(
                        "group {group_id} has inconsistent pending recovery ownership"
                    )));
                }
                for resource_id in committed.committed_resource_ids() {
                    if expected_resources
                        .insert(resource_id.clone(), committed.role_id().clone())
                        .is_some()
                    {
                        return Err(ControlError::InvalidProposal(format!(
                            "group {group_id} has duplicate committed resource {resource_id}"
                        )));
                    }
                }
                Ok(key.clone())
            })
            .collect::<Result<Vec<_>, ControlError>>()?;

        for (resource_id, role_id) in &expected_resources {
            let reservation = self.reservations.get(resource_id).ok_or_else(|| {
                ControlError::InvalidProposal(format!(
                    "group {group_id} ownership {resource_id} has no reservation"
                ))
            })?;
            let task_owned = reservation.task_ref == task_ref
                || group.task_execution(&reservation.task_ref).is_some()
                || matches!(reservation.owner, domain::AllocationOwner::Context { .. });
            if !task_owned
                || reservation.role_id != *role_id
                || reservation.group_id.as_ref() != Some(group_id)
            {
                return Err(ControlError::InvalidProposal(format!(
                    "group {group_id} has mismatched reservation {resource_id}"
                )));
            }
        }
        let resource_ids = self
            .reservations
            .iter()
            .filter(|(_, reservation)| reservation.group_id.as_ref() == Some(group_id))
            .map(|(resource_id, reservation)| {
                let task_owned = reservation.task_ref == task_ref
                    || group.task_execution(&reservation.task_ref).is_some()
                    || matches!(reservation.owner, domain::AllocationOwner::Context { .. });
                if !task_owned || expected_resources.get(resource_id) != Some(&reservation.role_id)
                {
                    return Err(ControlError::InvalidProposal(format!(
                        "group {group_id} has orphan reservation {resource_id}"
                    )));
                }
                Ok(resource_id.clone())
            })
            .collect::<Result<Vec<_>, ControlError>>()?;

        for resource_id in &resource_ids {
            self.reservations.remove(resource_id);
        }
        for key in pending_keys {
            self.pending_recovery_commitments.remove(&key);
        }
        let group = self
            .groups
            .get_mut(group_id)
            .ok_or_else(|| ControlError::UnknownGroup(group_id.clone()))?;
        group.assignments.clear();
        group.context_bindings.clear();
        group.unbound_roles.clear();
        group.task_unbound_roles.clear();
        for execution in group.task_executions.values_mut() {
            // Terminal Group release removes live Task bindings from the durable projection;
            // historical binding events remain available in the event log.
            *execution = execution.with_assignments(Vec::new());
        }
        group.lifecycle = GroupLifecycle::Released;
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::ExecutionGroupReleased {
                group_id: group_id.clone(),
                task_ref,
                resource_ids,
            },
        );
        Ok(())
    }

    /// Returns the current group snapshot for assertions and adapters.
    pub fn group(&self, group_id: &ExecutionGroupId) -> Option<&ExecutionGroup> {
        self.groups.get(group_id)
    }
}
