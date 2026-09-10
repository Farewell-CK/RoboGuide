//! Runtime dispatch, cancellation, and bound-command orchestration.

use super::*;

impl<E: EventSink + Clone> IntegrationRuntimeBridge<E> {
    /// Prepares an existing Runtime command for durable application-level outbox delivery.
    ///
    /// This bridge deliberately performs no network side effect. The application must checkpoint
    /// the returned Runtime state first and then call [`Self::flush_dispatch_outbox`].
    pub fn execute(
        &mut self,
        execution_id: String,
        command: ExecutionCommand,
        mut resource_ids: Vec<ResourceId>,
    ) -> Result<(), IntegrationRuntimeError> {
        let dispatch = self
            .runtime
            .validate_dispatch(&execution_id, &command, &resource_ids)
            .map_err(|error| IntegrationRuntimeError::Protocol(error.to_string()))?;
        if dispatch == runtime::DispatchDecision::AlreadyRouted {
            return Ok(());
        }
        resource_ids.sort();
        resource_ids.dedup();
        self.runtime
            .prepare_dispatch(execution_id.clone(), command.clone(), resource_ids.clone())
            .map_err(|error| IntegrationRuntimeError::Protocol(error.to_string()))?;
        Ok(())
    }

    /// Delivers one already persisted Execute intent through the current Node route.
    pub(super) fn flush_dispatch_intent(
        &mut self,
        execution_id: &str,
        command: &ExecutionCommand,
        resource_ids: &mut Vec<ResourceId>,
    ) -> Result<(), IntegrationRuntimeError> {
        resource_ids.sort();
        resource_ids.dedup();
        let route_result = self.router.execute(
            command.node_id().as_str(),
            format!("dispatch-{execution_id}"),
            execution_id.to_string(),
            invocation_from_command(command),
            resource_ids
                .iter()
                .map(|resource_id| resource_id.as_str().to_string())
                .collect(),
        );
        self.runtime.record_dispatch_attempt(execution_id);
        if let Err(error) = route_result {
            return Err(error.into());
        }
        Ok(())
    }

    /// Delivers every persisted Execute intent that is still waiting for a route.
    pub fn flush_dispatch_outbox(&mut self) -> Result<usize, IntegrationRuntimeError> {
        let intents = self.runtime.pending_dispatch_intents();
        let mut delivered = 0;
        let mut first_error = None;
        for intent in intents {
            let mut resources = intent.resource_ids.clone();
            match self.flush_dispatch_intent(&intent.execution_id, &intent.command, &mut resources)
            {
                Ok(()) => delivered += 1,
                Err(error) if first_error.is_none() => first_error = Some(error),
                Err(_) => {}
            }
        }
        first_error.map_or(Ok(delivered), Err)
    }

    /// Persists cancellation intent for every retained nonterminal physical attempt in one Group.
    pub fn request_group_cancellation(
        &mut self,
        group_id: &domain::ExecutionGroupId,
    ) -> Result<usize, IntegrationRuntimeError> {
        let attempts = self.runtime.attempts_for_group(group_id);
        for (execution_id, status) in &attempts {
            if !status.is_terminal() {
                self.runtime
                    .request_cancellation(execution_id)
                    .map_err(|error| IntegrationRuntimeError::Protocol(error.to_string()))?;
            }
        }
        Ok(attempts
            .into_iter()
            .filter(|(_, status)| !status.is_terminal())
            .count())
    }

    /// Delivers every durable cancellation whose physical attempt remains nonterminal.
    pub fn flush_cancellation_outbox(&self) -> Result<usize, IntegrationRuntimeError> {
        let pending = self.runtime.pending_cancellations();
        let mut delivered = 0;
        let mut first_error = None;
        for (execution_id, node_id) in &pending {
            match self.router.cancel(
                node_id.as_str(),
                format!("cancel-{execution_id}"),
                execution_id.clone(),
            ) {
                Ok(()) => delivered += 1,
                Err(error) if first_error.is_none() => first_error = Some(error.into()),
                Err(_) => {}
            }
        }
        first_error.map_or(Ok(delivered), Err)
    }

    /// Delivers durable execution and cancellation intents after their checkpoint commits.
    pub fn flush_command_outboxes(&mut self) -> Result<usize, IntegrationRuntimeError> {
        let dispatches = self.flush_dispatch_outbox();
        let cancellations = self.flush_cancellation_outbox();
        match (dispatches, cancellations) {
            (Ok(dispatches), Ok(cancellations)) => Ok(dispatches.saturating_add(cancellations)),
            (Err(error), _) | (_, Err(error)) => Err(error),
        }
    }

    /// Builds and prepares a command for a legacy single-Task Group role.
    ///
    /// Mission-level Groups must use [`Self::execute_task_bound`] so Integration never guesses a
    /// Task identity from the compatibility `ExecutionGroup::task_ref` field.
    pub fn execute_bound(
        &mut self,
        execution_id: String,
        group_id: &domain::ExecutionGroupId,
        role_id: &domain::RoleId,
        intent: domain::ExecutionIntent,
        correlation_id: CorrelationId,
    ) -> Result<ExecutionCommand, IntegrationRuntimeError> {
        let group = self.control.group(group_id).ok_or_else(|| {
            IntegrationRuntimeError::Protocol("execution group is unknown".to_string())
        })?;
        if group.task_executions().next().is_some() {
            return Err(IntegrationRuntimeError::Protocol(
                "Mission-level Group dispatch requires an explicit TaskRef".to_string(),
            ));
        }
        let task_ref = group.task_ref().clone();
        self.execute_task_bound(
            execution_id,
            group_id,
            &task_ref,
            role_id,
            intent,
            TimestampMs::new(0),
            correlation_id,
        )
    }

    /// Builds and prepares a command for one specific Task execution inside a Group.
    #[allow(clippy::too_many_arguments)]
    pub fn execute_task_bound(
        &mut self,
        execution_id: String,
        group_id: &domain::ExecutionGroupId,
        task_ref: &domain::TaskRef,
        role_id: &domain::RoleId,
        intent: domain::ExecutionIntent,
        now: TimestampMs,
        correlation_id: CorrelationId,
    ) -> Result<ExecutionCommand, IntegrationRuntimeError> {
        self.prepare_task_bound(
            execution_id,
            group_id,
            task_ref,
            role_id,
            intent,
            now,
            correlation_id,
        )
    }

    /// Prepares one Task-bound dispatch intent without producing a network side effect.
    #[allow(clippy::too_many_arguments)]
    pub fn prepare_task_bound(
        &mut self,
        execution_id: String,
        group_id: &domain::ExecutionGroupId,
        task_ref: &domain::TaskRef,
        role_id: &domain::RoleId,
        intent: domain::ExecutionIntent,
        now: TimestampMs,
        correlation_id: CorrelationId,
    ) -> Result<ExecutionCommand, IntegrationRuntimeError> {
        self.runtime.refresh_peer_channel_deadlines(now);
        let group = self.control.group(group_id).ok_or_else(|| {
            IntegrationRuntimeError::Protocol("execution group is unknown".to_string())
        })?;
        if !matches!(
            group.lifecycle(),
            control::GroupLifecycle::Bound
                | control::GroupLifecycle::Active
                | control::GroupLifecycle::Adapted
        ) {
            return Err(IntegrationRuntimeError::Protocol(
                "execution group is not bound".to_string(),
            ));
        }
        let (node_id, resource_ids) = if group.task_executions().next().is_none() {
            if task_ref != group.task_ref() {
                return Err(IntegrationRuntimeError::Protocol(
                    "legacy Group dispatch TaskRef does not match the Group".to_string(),
                ));
            }
            let assignment = group
                .assignments()
                .iter()
                .find(|assignment| assignment.role_id() == role_id)
                .ok_or_else(|| {
                    IntegrationRuntimeError::Protocol("group role is not bound".to_string())
                })?;
            (
                assignment.node_id().clone(),
                assignment.resource_ids().to_vec(),
            )
        } else {
            let execution = group.task_execution(task_ref).ok_or_else(|| {
                IntegrationRuntimeError::Protocol(
                    "TaskExecution is absent from the Mission-level Group".to_string(),
                )
            })?;
            let coordination_readiness = self.runtime.coordination_readiness_for_mode(
                group_id,
                execution.context_id(),
                execution.coupling_mode(),
            );
            let coordination_unavailable = match coordination_readiness {
                Some(runtime::CoordinationReadiness::Ready) => false,
                Some(_) => true,
                None => execution.coupling_mode() != domain::ExecutionCouplingMode::Independent,
            };
            if coordination_unavailable {
                return Err(IntegrationRuntimeError::Protocol(
                    "TaskExecution coordination mechanisms are not ready".to_string(),
                ));
            }
            if !matches!(
                execution.lifecycle(),
                domain::TaskExecutionLifecycle::Ready | domain::TaskExecutionLifecycle::Active
            ) {
                return Err(IntegrationRuntimeError::Protocol(
                    "TaskExecution is not dispatchable".to_string(),
                ));
            }
            if !task_assignments_are_complete(execution) {
                return Err(IntegrationRuntimeError::Protocol(
                    "TaskExecution bindings are incomplete".to_string(),
                ));
            }
            let assignment = execution
                .assignments()
                .iter()
                .find(|assignment| assignment.role_id() == role_id)
                .ok_or_else(|| {
                    IntegrationRuntimeError::Protocol("group role is not bound".to_string())
                })?;
            (
                assignment.node_id().clone(),
                assignment.resource_ids().to_vec(),
            )
        };
        let command = ExecutionCommand::new(
            task_ref.mission_id().clone(),
            task_ref.task_id().clone(),
            group_id.clone(),
            role_id.clone(),
            node_id,
            intent,
            correlation_id,
        );
        self.runtime
            .prepare_dispatch(execution_id, command.clone(), resource_ids)
            .map_err(|error| IntegrationRuntimeError::Protocol(error.to_string()))?;
        Ok(command)
    }

    /// Allocates the next physical attempt identity for one committed logical role slot.
    pub fn allocate_task_attempt_id(
        &mut self,
        group_id: &domain::ExecutionGroupId,
        task_ref: &domain::TaskRef,
        role_id: &domain::RoleId,
    ) -> Result<String, IntegrationRuntimeError> {
        self.runtime
            .allocate_attempt_id(group_id, task_ref, role_id)
            .map_err(|error| IntegrationRuntimeError::Protocol(error.to_string()))
    }

    /// Records one cancellation for application checkpointing before network delivery.
    pub fn cancel(&mut self, execution_id: &str) -> Result<(), IntegrationRuntimeError> {
        self.runtime
            .request_cancellation(execution_id)
            .map_err(|error| IntegrationRuntimeError::Protocol(error.to_string()))
    }

    /// Returns current Control authority.
    pub const fn control(&self) -> &ControlPlane {
        &self.control
    }
    /// Returns mutable Control authority for composition-level Group lifecycle.
    pub const fn control_mut(&mut self) -> &mut ControlPlane {
        &mut self.control
    }
    /// Returns current Shared Node State.
    pub const fn state(&self) -> &InMemorySharedNodeState {
        &self.state
    }
    /// Returns mutable Shared Node State for composition-level Control calls.
    pub const fn state_mut(&mut self) -> &mut InMemorySharedNodeState {
        &mut self.state
    }
    /// Returns independently attributed State records without cross-source fusion.
    pub const fn state_records(&self) -> &StateRecordProjection {
        &self.state_records
    }
    /// Returns current remote execution status.
    pub fn execution_status(&self, execution_id: &str) -> Option<RemoteExecutionStatus> {
        self.runtime
            .execution_status(execution_id)
            .map(remote_status)
    }

    /// Returns every retained physical attempt in deterministic identity order.
    pub fn attempt_history(&self) -> Vec<runtime::ExecutionAttemptSnapshot> {
        self.runtime.attempt_history()
    }

    /// Returns exact current commands whose physical attempt still requires reconciliation.
    pub fn current_unknown_attempts(&self) -> Vec<ExecutionCommand> {
        self.runtime.current_unknown_attempts()
    }

    /// Returns whether all retained physical attempts in one Group are terminal.
    pub fn group_attempts_terminal(&self, group_id: &domain::ExecutionGroupId) -> bool {
        self.runtime
            .attempts_for_group(group_id)
            .into_iter()
            .all(|(_, status)| status.is_terminal())
    }

    /// Returns whether the current physical attempt already represents this exact binding.
    pub fn current_attempt_matches_binding(
        &self,
        group_id: &domain::ExecutionGroupId,
        task_ref: &domain::TaskRef,
        role_id: &domain::RoleId,
        node_id: &domain::NodeId,
    ) -> bool {
        self.runtime
            .current_attempt_matches_node(group_id, task_ref, role_id, node_id)
    }
}
