//! TaskExecution and Context binding lifecycle transitions inside a Group.

use super::*;

impl ControlPlane {
    /// Releases all Context-scoped resources when their Intelligence Context ends.
    pub fn release_context_bindings<E: EventSink>(
        &mut self,
        group_id: &ExecutionGroupId,
        context_id: &CoordinationContextId,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<Vec<ResourceId>, ControlError> {
        let group = self
            .groups
            .get(group_id)
            .ok_or_else(|| ControlError::UnknownGroup(group_id.clone()))?
            .clone();
        let mut resources = group
            .context_bindings()
            .filter(|binding| binding.context_id() == context_id)
            .flat_map(|binding| binding.assignment().resource_ids().iter().cloned())
            .collect::<Vec<_>>();
        resources.sort();
        resources.dedup();
        for resource_id in &resources {
            let reservation = self.reservations.get(resource_id).ok_or_else(|| {
                ControlError::InvalidProposal(format!(
                    "Context resource {resource_id} has no reservation"
                ))
            })?;
            if reservation.group_id.as_ref() != Some(group_id)
                || reservation.scope != ResourceBindingScope::Context
            {
                return Err(ControlError::InvalidProposal(format!(
                    "Context does not own resource {resource_id}"
                )));
            }
        }
        for resource_id in &resources {
            self.reservations.remove(resource_id);
        }
        let group_mut = self
            .groups
            .get_mut(group_id)
            .expect("group validated above");
        group_mut
            .context_bindings
            .retain(|_, binding| binding.context_id() != context_id);
        for execution in group_mut.task_executions.values_mut() {
            if execution.context_id() != context_id {
                continue;
            }
            let remaining = execution
                .assignments()
                .iter()
                .map(|assignment| {
                    RoleAssignment::new(
                        assignment.role_id().clone(),
                        assignment.node_id().clone(),
                        assignment
                            .resource_ids()
                            .iter()
                            .filter(|resource_id| !resources.contains(resource_id))
                            .cloned()
                            .collect(),
                    )
                })
                .collect();
            *execution = execution.with_assignments(remaining);
        }
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::ContextBindingsReleased {
                group_id: group_id.clone(),
                context_id: context_id.clone(),
                resource_ids: resources.clone(),
            },
        );
        Ok(resources)
    }

    /// Activates a registered Task while retaining the Mission-level Group.
    pub fn activate_task_execution<E: EventSink>(
        &mut self,
        group_id: &ExecutionGroupId,
        task_ref: &TaskRef,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<(), ControlError> {
        let activate_schedule = match self.scheduled_task(task_ref) {
            Some(reservation) if reservation.phase() == SchedulingReservationPhase::Invalidated => {
                return Err(ControlError::InvalidProposal(
                    "invalidated scheduling reservation cannot activate".to_string(),
                ));
            }
            Some(reservation) if reservation.decision().starts_at() > timestamp => {
                return Err(ControlError::InvalidProposal(
                    "scheduled Task cannot activate before its reserved start".to_string(),
                ));
            }
            Some(reservation)
                if reservation
                    .decision()
                    .ends_at()
                    .is_some_and(|ends_at| timestamp >= ends_at) =>
            {
                return Err(ControlError::InvalidProposal(
                    "scheduled Task cannot activate after its reserved interval".to_string(),
                ));
            }
            Some(reservation)
                if reservation
                    .decision()
                    .latest_activation_at()
                    .is_some_and(|latest| timestamp > latest) =>
            {
                return Err(ControlError::InvalidProposal(
                    "scheduled Task cannot activate after its allowed start window".to_string(),
                ));
            }
            Some(reservation) => reservation.phase() == SchedulingReservationPhase::Scheduled,
            None => false,
        };
        let group = self
            .groups
            .get(group_id)
            .ok_or_else(|| ControlError::UnknownGroup(group_id.clone()))?;
        let execution = group
            .task_executions
            .get(task_ref)
            .ok_or_else(|| ControlError::InvalidProposal("unknown Task execution".to_string()))?;
        let expected_roles = execution.role_scopes().keys().collect::<BTreeSet<_>>();
        let assigned_roles = execution
            .assignments()
            .iter()
            .map(RoleAssignment::role_id)
            .collect::<BTreeSet<_>>();
        if expected_roles != assigned_roles || execution.assignments().len() != assigned_roles.len()
        {
            return Err(ControlError::InvalidProposal(
                "Task execution requires committed assignments for every role before activation"
                    .to_string(),
            ));
        }
        let group = self
            .groups
            .get_mut(group_id)
            .ok_or_else(|| ControlError::UnknownGroup(group_id.clone()))?;
        let execution = group
            .task_executions
            .get(task_ref)
            .ok_or_else(|| ControlError::InvalidProposal("unknown Task execution".to_string()))?;
        if !matches!(execution.lifecycle(), TaskExecutionLifecycle::Ready) {
            return Err(ControlError::InvalidProposal(
                "Task execution is not ready to activate".to_string(),
            ));
        }
        group.task_executions.insert(
            task_ref.clone(),
            execution.with_lifecycle(TaskExecutionLifecycle::Active),
        );
        if matches!(
            group.lifecycle,
            GroupLifecycle::Bound | GroupLifecycle::Adapted
        ) {
            group.lifecycle = GroupLifecycle::Active;
        }
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::TaskExecutionActivated {
                group_id: group_id.clone(),
                task_ref: task_ref.clone(),
            },
        );
        if activate_schedule {
            self.activate_scheduled_task(task_ref);
            events.append(
                timestamp,
                correlation_id,
                None,
                EventPayload::SchedulingReservationActivated {
                    group_id: group_id.clone(),
                    task_ref: task_ref.clone(),
                },
            );
        }
        Ok(())
    }

    /// Completes one Task and leaves its parent Group alive for later Tasks.
    pub fn complete_task_execution<E: EventSink>(
        &mut self,
        group_id: &ExecutionGroupId,
        task_ref: &TaskRef,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<(), ControlError> {
        let group = self
            .groups
            .get_mut(group_id)
            .ok_or_else(|| ControlError::UnknownGroup(group_id.clone()))?;
        let execution = group
            .task_executions
            .get(task_ref)
            .ok_or_else(|| ControlError::InvalidProposal("unknown Task execution".to_string()))?;
        if !matches!(execution.lifecycle(), TaskExecutionLifecycle::Active) {
            return Err(ControlError::InvalidProposal(
                "Task execution is not active".to_string(),
            ));
        }
        group.task_executions.insert(
            task_ref.clone(),
            execution.with_lifecycle(TaskExecutionLifecycle::Completed),
        );
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::TaskExecutionCompleted {
                group_id: group_id.clone(),
                task_ref: task_ref.clone(),
            },
        );
        Ok(())
    }

    /// Marks one active Task failed while retaining the parent Group for recovery policy.
    pub fn fail_task_execution<E: EventSink>(
        &mut self,
        group_id: &ExecutionGroupId,
        task_ref: &TaskRef,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<(), ControlError> {
        let group = self
            .groups
            .get_mut(group_id)
            .ok_or_else(|| ControlError::UnknownGroup(group_id.clone()))?;
        let execution = group
            .task_executions
            .get(task_ref)
            .ok_or_else(|| ControlError::InvalidProposal("unknown Task execution".to_string()))?;
        if !matches!(
            execution.lifecycle(),
            TaskExecutionLifecycle::Active | TaskExecutionLifecycle::Blocked
        ) {
            return Err(ControlError::InvalidProposal(
                "only an active or blocked Task can fail".to_string(),
            ));
        }
        group.task_executions.insert(
            task_ref.clone(),
            execution.with_lifecycle(TaskExecutionLifecycle::Failed),
        );
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::TaskExecutionFailed {
                group_id: group_id.clone(),
                task_ref: task_ref.clone(),
            },
        );
        Ok(())
    }

    /// Releases only the supplied temporary Task resources and retains the parent Group.
    pub fn release_task_bindings<E: EventSink>(
        &mut self,
        group_id: &ExecutionGroupId,
        task_ref: &TaskRef,
        resource_ids: &[ResourceId],
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<(), ControlError> {
        let group = self
            .groups
            .get(group_id)
            .ok_or_else(|| ControlError::UnknownGroup(group_id.clone()))?;
        let execution = group
            .task_execution(task_ref)
            .ok_or_else(|| ControlError::InvalidProposal("unknown Task execution".to_string()))?;
        if !matches!(
            execution.lifecycle(),
            TaskExecutionLifecycle::Completed
                | TaskExecutionLifecycle::Failed
                | TaskExecutionLifecycle::Cancelled
        ) {
            return Err(ControlError::InvalidProposal(
                "Task bindings can only be released after terminal completion".to_string(),
            ));
        }
        let expected = execution
            .assignments()
            .iter()
            .flat_map(|assignment| assignment.resource_ids())
            .filter(|resource_id| {
                execution.binding_scope(resource_id) == ResourceBindingScope::Task
            })
            .cloned()
            .collect::<Vec<_>>();
        let mut expected_sorted = expected;
        expected_sorted.sort();
        let mut supplied = resource_ids.to_vec();
        supplied.sort();
        supplied.dedup();
        if supplied != expected_sorted {
            return Err(ControlError::InvalidProposal(
                "released resources must exactly match Task-scoped bindings".to_string(),
            ));
        }
        for resource_id in &expected_sorted {
            let reservation = self.reservations.get(resource_id).ok_or_else(|| {
                ControlError::InvalidProposal(format!(
                    "Task resource {resource_id} has no reservation"
                ))
            })?;
            if reservation.group_id.as_ref() != Some(group_id)
                || reservation.task_ref != *task_ref
                || reservation.scope != ResourceBindingScope::Task
            {
                return Err(ControlError::InvalidProposal(format!(
                    "Task does not own resource {resource_id}"
                )));
            }
        }
        for resource_id in &expected_sorted {
            self.reservations.remove(resource_id);
        }
        // Keep Context-scoped assignments visible on the TaskExecution. Their reservation
        // belongs to the Context and must survive the Task terminal transition.
        let remaining = execution
            .assignments()
            .iter()
            .filter(|assignment| {
                execution.role_scope(assignment.role_id()) == ResourceBindingScope::Context
            })
            .cloned()
            .collect::<Vec<_>>();
        let group = self
            .groups
            .get_mut(group_id)
            .expect("group validated above");
        let execution = group
            .task_executions
            .get(task_ref)
            .expect("Task validated above");
        group
            .task_executions
            .insert(task_ref.clone(), execution.with_assignments(remaining));
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::TaskExecutionBindingsReleased {
                group_id: group_id.clone(),
                task_ref: task_ref.clone(),
                resource_ids: expected_sorted,
            },
        );
        Ok(())
    }
}
