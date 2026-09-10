//! Dispatch, receipt, cancellation, and attempt-query operations.

use super::*;

impl RuntimeExecutionManager {
    /// Validates whether one committed execution needs a new Integration route.
    pub fn validate_dispatch(
        &self,
        execution_id: &str,
        command: &ExecutionCommand,
        resource_ids: &[ResourceId],
    ) -> Result<DispatchDecision, ExecutionRuntimeError> {
        if self.restored_executions.contains(execution_id) {
            return Err(ExecutionRuntimeError::ReconciliationRequired(
                "execution was restored after controller restart; reconciliation is required before routing"
                    .to_string(),
            ));
        }
        if self.execution_status.contains_key(execution_id)
            && !self.executions.contains_key(execution_id)
        {
            return Err(ExecutionRuntimeError::ReconciliationRequired(
                "execution was observed during reconnect; reconciliation is required before routing"
                    .to_string(),
            ));
        }
        let resources = normalized_resources(resource_ids);
        if let Some(existing) = self.executions.get(execution_id) {
            if existing.command != *command || existing.resource_ids != resources {
                return Err(ExecutionRuntimeError::ExecutionConflict(
                    execution_id.to_string(),
                ));
            }
            return Ok(DispatchDecision::AlreadyRouted);
        }
        Ok(DispatchDecision::Route)
    }

    /// Allocates an unused physical attempt identity, rejecting exhausted generation counters.
    pub fn allocate_attempt_id(
        &mut self,
        group_id: &ExecutionGroupId,
        task_ref: &TaskRef,
        role_id: &RoleId,
    ) -> Result<String, ExecutionRuntimeError> {
        let slot = (group_id.clone(), task_ref.clone(), role_id.clone());
        let mut generation = self.attempt_generations.get(&slot).copied().unwrap_or(0);
        loop {
            generation = generation.checked_add(1).ok_or_else(|| {
                ExecutionRuntimeError::ReconciliationRequired(
                    "physical attempt generation is exhausted".to_string(),
                )
            })?;
            let execution_id = format!(
                "attempt-{}:{}-{}:{}-{}:{}-{}",
                group_id.as_str().len(),
                group_id,
                task_ref.task_id().as_str().len(),
                task_ref.task_id(),
                role_id.as_str().len(),
                role_id,
                generation
            );
            if !self.executions.contains_key(&execution_id)
                && !self.dispatch_outbox.contains_key(&execution_id)
            {
                self.attempt_generations.insert(slot, generation);
                return Ok(execution_id);
            }
        }
    }

    /// Returns all Execute intents that have not yet been durably delivered.
    pub fn pending_dispatch_intents(&self) -> Vec<DispatchIntent> {
        self.dispatch_outbox
            .values()
            .filter(|intent| {
                !intent.delivered
                    && !self.cancellation_intents.contains(&intent.execution_id)
                    && self.execution_status(&intent.execution_id)
                        == Some(ExecutionStatus::Dispatched)
            })
            .cloned()
            .collect()
    }

    /// Marks one intent acknowledged after Node journal acceptance has been proven.
    pub fn mark_dispatch_delivered(
        &mut self,
        execution_id: &str,
    ) -> Result<(), ExecutionRuntimeError> {
        let intent = self.dispatch_outbox.get_mut(execution_id).ok_or_else(|| {
            ExecutionRuntimeError::ReconciliationRequired(format!(
                "dispatch intent {execution_id} is absent"
            ))
        })?;
        intent.delivered = true;
        Ok(())
    }

    /// Applies a durable Node command receipt to an immutable dispatch intent.
    pub fn observe_dispatch_receipt(
        &mut self,
        execution_id: &str,
        command_id: &str,
        node_id: &NodeId,
        persisted: bool,
        reason: impl Into<String>,
    ) -> Result<Vec<ExecutionEvent>, ExecutionRuntimeError> {
        let intent = self.dispatch_outbox.get(execution_id).ok_or_else(|| {
            ExecutionRuntimeError::ReconciliationRequired(format!(
                "dispatch intent {execution_id} is absent"
            ))
        })?;
        if intent.command_id() != command_id {
            return Err(ExecutionRuntimeError::ExecutionConflict(
                execution_id.to_string(),
            ));
        }
        if intent.command.node_id() != node_id {
            return Err(ExecutionRuntimeError::NodeOwnership(
                "command receipt node differs from execution owner".to_string(),
            ));
        }
        if persisted {
            self.mark_dispatch_delivered(execution_id)?;
            return Ok(Vec::new());
        }
        if self
            .execution_status(execution_id)
            .is_some_and(ExecutionStatus::is_terminal)
        {
            return Ok(Vec::new());
        }
        if self.execution_status(execution_id) == Some(ExecutionStatus::Unknown) {
            return Ok(Vec::new());
        }
        let reason = reason.into();
        // A rejected duplicate can describe conflicting input, not the outcome of physical work.
        self.execution_status
            .insert(execution_id.to_string(), ExecutionStatus::Unknown);
        let command = self
            .executions
            .get(execution_id)
            .map(|execution| execution.command.clone())
            .ok_or_else(|| {
                ExecutionRuntimeError::ReconciliationRequired(format!(
                    "execution context {execution_id} is absent"
                ))
            })?;
        let slot = (
            command.group_id().clone(),
            command.task_ref().clone(),
            command.role_id().clone(),
        );
        if self.active_executions.get(&slot).map(String::as_str) != Some(execution_id) {
            return Ok(Vec::new());
        }
        let mut events = vec![ExecutionEvent::RecoveryRequired {
            execution_id: execution_id.to_string(),
            node_id: command.node_id().clone(),
            context: Some(command),
            reason,
        }];
        events.extend(self.refresh_relations_for_slot(&slot));
        Ok(events)
    }

    /// Reduces a Cancel receipt without treating command admission as terminal execution evidence.
    pub fn observe_cancellation_receipt(
        &mut self,
        execution_id: &str,
        command_id: &str,
        node_id: &NodeId,
        persisted: bool,
        reason: impl Into<String>,
    ) -> Result<Vec<ExecutionEvent>, ExecutionRuntimeError> {
        if command_id != format!("cancel-{execution_id}")
            || !self.cancellation_intents.contains(execution_id)
        {
            return Err(ExecutionRuntimeError::ReconciliationRequired(format!(
                "cancellation intent {execution_id} is absent or conflicts with its receipt"
            )));
        }
        if self.cancellation_node(execution_id) != Some(node_id) {
            return Err(ExecutionRuntimeError::NodeOwnership(
                "cancellation receipt node differs from execution owner".to_string(),
            ));
        }
        if persisted
            || self
                .execution_status(execution_id)
                .is_some_and(ExecutionStatus::is_terminal)
            || self.execution_status(execution_id) == Some(ExecutionStatus::Unknown)
        {
            return Ok(Vec::new());
        }
        self.execution_status
            .insert(execution_id.to_string(), ExecutionStatus::Unknown);
        let command = self
            .executions
            .get(execution_id)
            .map(|context| context.command.clone())
            .ok_or_else(|| {
                ExecutionRuntimeError::ReconciliationRequired(format!(
                    "execution context {execution_id} is absent"
                ))
            })?;
        let slot = (
            command.group_id().clone(),
            command.task_ref().clone(),
            command.role_id().clone(),
        );
        if self.active_executions.get(&slot).map(String::as_str) != Some(execution_id) {
            return Ok(Vec::new());
        }
        let mut events = vec![ExecutionEvent::RecoveryRequired {
            execution_id: execution_id.to_string(),
            node_id: node_id.clone(),
            context: Some(command),
            reason: reason.into(),
        }];
        events.extend(self.refresh_relations_for_slot(&slot));
        Ok(events)
    }

    /// Records one Router delivery attempt while retaining the intent until Node acknowledgement.
    pub fn record_dispatch_attempt(&mut self, execution_id: &str) {
        if let Some(intent) = self.dispatch_outbox.get_mut(execution_id) {
            intent.delivery_attempts = intent.delivery_attempts.saturating_add(1);
        }
    }

    /// Prepares one immutable Execute intent before any network side effect occurs.
    pub fn prepare_dispatch(
        &mut self,
        execution_id: String,
        command: ExecutionCommand,
        resource_ids: Vec<ResourceId>,
    ) -> Result<DispatchDecision, ExecutionRuntimeError> {
        let decision = self.validate_dispatch(&execution_id, &command, &resource_ids)?;
        if decision == DispatchDecision::AlreadyRouted {
            return Ok(decision);
        }
        let resources = normalized_resources(&resource_ids);
        let slot = (
            command.group_id().clone(),
            command.task_ref().clone(),
            command.role_id().clone(),
        );
        self.executions.insert(
            execution_id.clone(),
            ExecutionContext {
                command: command.clone(),
                resource_ids: resources.clone(),
            },
        );
        self.execution_nodes
            .insert(execution_id.clone(), command.node_id().clone());
        let previous = self
            .active_executions
            .insert(slot.clone(), execution_id.clone());
        if previous
            .as_ref()
            .is_some_and(|current| current != &execution_id)
        {
            self.spatial_evidence.remove(&slot);
            if let Some(previous) = previous {
                self.reactivation_attempts.remove(&previous);
            }
            self.reactivation_attempts.insert(execution_id.clone());
        }
        self.execution_status
            .insert(execution_id.clone(), ExecutionStatus::Dispatched);
        self.dispatch_outbox.insert(
            execution_id.clone(),
            DispatchIntent {
                execution_id,
                command,
                resource_ids: resources,
                delivery_attempts: 0,
                delivered: false,
            },
        );
        Ok(DispatchDecision::Route)
    }

    /// Records one successfully routed committed execution context.
    pub fn record_dispatched(
        &mut self,
        execution_id: String,
        command: ExecutionCommand,
        resource_ids: Vec<ResourceId>,
    ) -> Result<(), ExecutionRuntimeError> {
        self.prepare_dispatch(execution_id.clone(), command, resource_ids)?;
        self.mark_dispatch_delivered(&execution_id)?;
        Ok(())
    }

    /// Returns the current Node target for cancellation of one known execution.
    pub fn cancellation_node(&self, execution_id: &str) -> Option<&NodeId> {
        self.executions
            .get(execution_id)
            .map(|execution| execution.command.node_id())
    }

    /// Returns the committed resource identities for one prepared execution attempt.
    pub fn execution_resources(&self, execution_id: &str) -> Option<Vec<ResourceId>> {
        self.executions
            .get(execution_id)
            .map(|execution| execution.resource_ids.clone())
    }

    /// Returns the latest accepted Runtime status for one execution identity.
    pub fn execution_status(&self, execution_id: &str) -> Option<ExecutionStatus> {
        self.execution_status.get(execution_id).copied()
    }

    /// Returns the status of the current attempt occupying one logical Group Task role.
    pub fn current_execution_status(
        &self,
        group_id: &ExecutionGroupId,
        task_ref: &TaskRef,
        role_id: &RoleId,
    ) -> Option<ExecutionStatus> {
        self.active_executions
            .get(&(group_id.clone(), task_ref.clone(), role_id.clone()))
            .and_then(|execution_id| self.execution_status.get(execution_id))
            .copied()
    }

    /// Returns current physical attempts for one Group, including terminal evidence.
    pub fn active_attempts_for_group(
        &self,
        group_id: &ExecutionGroupId,
    ) -> Vec<(String, ExecutionStatus)> {
        self.active_executions
            .iter()
            .filter(|((current_group, _, _), _)| current_group == group_id)
            .filter_map(|(_, execution_id)| {
                self.execution_status
                    .get(execution_id)
                    .copied()
                    .map(|status| (execution_id.clone(), status))
            })
            .collect()
    }

    /// Returns every retained physical attempt for one Group, including superseded history.
    pub fn attempts_for_group(
        &self,
        group_id: &ExecutionGroupId,
    ) -> Vec<(String, ExecutionStatus)> {
        self.executions
            .iter()
            .filter(|(_, context)| context.command.group_id() == group_id)
            .filter_map(|(execution_id, _)| {
                self.execution_status(execution_id)
                    .map(|status| (execution_id.clone(), status))
            })
            .collect()
    }

    /// Returns the physical attempt occupying an exact logical slot, including terminal history.
    pub fn current_attempt_id(
        &self,
        group_id: &ExecutionGroupId,
        task_ref: &TaskRef,
        role_id: &RoleId,
    ) -> Option<&str> {
        self.active_executions
            .get(&(group_id.clone(), task_ref.clone(), role_id.clone()))
            .map(String::as_str)
    }

    /// Returns whether the current logical-slot attempt targets one exact committed Node.
    pub fn current_attempt_matches_node(
        &self,
        group_id: &ExecutionGroupId,
        task_ref: &TaskRef,
        role_id: &RoleId,
        node_id: &NodeId,
    ) -> bool {
        self.current_attempt_id(group_id, task_ref, role_id)
            .and_then(|execution_id| self.executions.get(execution_id))
            .is_some_and(|context| context.command.node_id() == node_id)
    }

    /// Records cancellation before delivery; Unknown remains pending until physical evidence resolves it.
    pub fn request_cancellation(
        &mut self,
        execution_id: &str,
    ) -> Result<(), ExecutionRuntimeError> {
        if !self.executions.contains_key(execution_id) {
            return Err(ExecutionRuntimeError::ReconciliationRequired(
                "unknown cancellation attempt".to_string(),
            ));
        }
        self.cancellation_intents.insert(execution_id.to_string());
        Ok(())
    }

    /// Returns cancellations that still need terminal physical evidence, even after a receipt.
    pub fn pending_cancellations(&self) -> Vec<(String, NodeId)> {
        self.cancellation_intents
            .iter()
            .filter(|id| {
                !self
                    .execution_status(id)
                    .is_some_and(ExecutionStatus::is_terminal)
            })
            .filter_map(|id| {
                self.cancellation_node(id)
                    .map(|node| (id.clone(), node.clone()))
            })
            .collect()
    }
}
