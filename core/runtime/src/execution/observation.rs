//! Execution fact reduction and unavailable-node observation handling.

use super::*;

impl RuntimeExecutionManager {
    /// Converts each nonterminal attempt on an unavailable Node into recovery evidence.
    pub fn observe_node_unavailable(
        &mut self,
        node_id: &NodeId,
        reason: impl Into<String>,
    ) -> Vec<ExecutionEvent> {
        let reason = reason.into();
        let current_attempts = self
            .active_executions
            .values()
            .cloned()
            .collect::<BTreeSet<_>>();
        let execution_ids = self
            .execution_nodes
            .iter()
            .filter(|(execution_id, owner)| {
                current_attempts.contains(execution_id.as_str())
                    && *owner == node_id
                    && self
                        .execution_status(execution_id.as_str())
                        .is_some_and(|status| {
                            !status.is_terminal() && status != ExecutionStatus::Unknown
                        })
            })
            .map(|(execution_id, _)| execution_id.clone())
            .collect::<Vec<_>>();
        execution_ids
            .into_iter()
            .flat_map(|execution_id| {
                self.execution_status
                    .insert(execution_id.clone(), ExecutionStatus::Unknown);
                let context = self
                    .executions
                    .get(&execution_id)
                    .map(|execution| execution.command.clone());
                let slot = context.as_ref().map(|command| {
                    (
                        command.group_id().clone(),
                        command.task_ref().clone(),
                        command.role_id().clone(),
                    )
                });
                let mut events = vec![ExecutionEvent::RecoveryRequired {
                    context,
                    execution_id,
                    node_id: node_id.clone(),
                    reason: reason.clone(),
                }];
                if let Some(slot) = slot {
                    events.extend(self.refresh_relations_for_slot(&slot));
                }
                events
            })
            .collect()
    }

    /// Exposes immutable attempt contexts and their status for audit without changing active slots.
    pub fn attempt_history(&self) -> Vec<ExecutionAttemptSnapshot> {
        self.executions
            .iter()
            .filter_map(|(execution_id, context)| {
                self.execution_status(execution_id)
                    .map(|status| ExecutionAttemptSnapshot {
                        execution_id: execution_id.clone(),
                        command: context.command.clone(),
                        status,
                    })
            })
            .collect()
    }

    /// Returns current logical-slot commands whose physical attempt remains ambiguous.
    pub fn current_unknown_attempts(&self) -> Vec<ExecutionCommand> {
        self.active_executions
            .values()
            .filter(|execution_id| {
                self.execution_status(execution_id) == Some(ExecutionStatus::Unknown)
            })
            .filter_map(|execution_id| {
                self.executions
                    .get(execution_id)
                    .map(|context| context.command.clone())
            })
            .collect()
    }

    /// Reduces one ordered Node execution fact into canonical Runtime events.
    pub fn observe_execution(
        &mut self,
        execution_id: &str,
        node_id: NodeId,
        sequence: u64,
        status: ExecutionStatus,
        reason: impl Into<String>,
    ) -> Result<Vec<ExecutionEvent>, ExecutionRuntimeError> {
        if self
            .execution_nodes
            .get(execution_id)
            .is_some_and(|expected| expected != &node_id)
        {
            return Err(ExecutionRuntimeError::NodeOwnership(
                "execution fact node differs from execution owner".to_string(),
            ));
        }
        if self
            .execution_sequences
            .get(execution_id)
            .is_some_and(|current| sequence <= *current)
        {
            return Ok(Vec::new());
        }
        if let Some(current) = self.execution_status.get(execution_id)
            && current.is_terminal()
        {
            if *current == status {
                return Ok(Vec::new());
            }
            return Err(ExecutionRuntimeError::TerminalConflict(
                "terminal execution status is immutable".to_string(),
            ));
        }
        if let Some(execution) = self.executions.get(execution_id)
            && execution.command.node_id() != &node_id
        {
            return Err(ExecutionRuntimeError::NodeOwnership(
                "execution fact node differs from dispatched command".to_string(),
            ));
        }
        self.execution_sequences
            .insert(execution_id.to_string(), sequence);
        self.execution_nodes
            .entry(execution_id.to_string())
            .or_insert(node_id);
        self.execution_status
            .insert(execution_id.to_string(), status);
        if let Some(intent) = self.dispatch_outbox.get_mut(execution_id) {
            intent.delivered = true;
        }

        let reason = reason.into();
        let Some(execution) = self.executions.get(execution_id) else {
            return Ok(if status == ExecutionStatus::Unknown {
                vec![ExecutionEvent::RecoveryRequired {
                    execution_id: execution_id.to_string(),
                    node_id: self
                        .execution_nodes
                        .get(execution_id)
                        .cloned()
                        .expect("execution owner recorded above"),
                    context: None,
                    reason,
                }]
            } else {
                Vec::new()
            });
        };
        let command = execution.command.clone();
        let task_key = (command.group_id().clone(), command.task_ref().clone());
        let execution_role = (
            command.group_id().clone(),
            command.task_ref().clone(),
            command.role_id().clone(),
        );
        let is_current_attempt = self
            .active_executions
            .get(&execution_role)
            .is_some_and(|current| current == execution_id);
        let mut events = Vec::new();
        let replacement_activated = is_current_attempt
            && status.proves_activation()
            && self.reactivation_attempts.remove(execution_id);
        if is_current_attempt
            && status.proves_activation()
            && (self.activated_tasks.insert(task_key.clone()) || replacement_activated)
        {
            events.push(ExecutionEvent::TaskActivated {
                group_id: task_key.0,
                task_ref: task_key.1,
            });
        }
        if is_current_attempt {
            match status {
                ExecutionStatus::Completed => {
                    events.push(ExecutionEvent::RoleCompleted { command });
                }
                ExecutionStatus::Failed | ExecutionStatus::Cancelled => {
                    events.push(ExecutionEvent::RoleFailed { command, reason });
                }
                ExecutionStatus::Unknown => events.push(ExecutionEvent::RecoveryRequired {
                    execution_id: execution_id.to_string(),
                    node_id: command.node_id().clone(),
                    context: Some(command),
                    reason,
                }),
                ExecutionStatus::Dispatched
                | ExecutionStatus::Accepted
                | ExecutionStatus::Running => {}
            }
            events.extend(self.refresh_relations_for_slot(&execution_role));
        }
        Ok(events)
    }

    /// Reduces current role execution facts into one Task terminal result when available.
    pub fn task_result<'a>(
        &self,
        group_id: &ExecutionGroupId,
        task_ref: &TaskRef,
        role_ids: impl IntoIterator<Item = &'a RoleId>,
    ) -> Option<ObservedTaskResult> {
        let mut saw_role = false;
        let mut all_completed = true;
        for role_id in role_ids {
            saw_role = true;
            let status = self
                .active_executions
                .get(&(group_id.clone(), task_ref.clone(), role_id.clone()))
                .and_then(|execution_id| self.execution_status.get(execution_id))
                .copied();
            match status {
                Some(ExecutionStatus::Completed) => {}
                Some(ExecutionStatus::Failed | ExecutionStatus::Cancelled) => {
                    return Some(ObservedTaskResult::Failed);
                }
                Some(ExecutionStatus::Unknown) => return None,
                _ => all_completed = false,
            }
        }
        (saw_role && all_completed && self.relations_allow_task_success(group_id, task_ref))
            .then_some(ObservedTaskResult::Succeeded)
    }
}

/// Returns stable, duplicate-free committed resource identities.
pub(super) fn normalized_resources(resource_ids: &[ResourceId]) -> Vec<ResourceId> {
    let mut resources = resource_ids.to_vec();
    resources.sort();
    resources.dedup();
    resources
}

/// Validates cross-map Runtime checkpoint invariants before constructing live state.
pub(super) fn validate_checkpoint(
    checkpoint: &RuntimeExecutionCheckpoint,
) -> Result<(), ExecutionRuntimeError> {
    let mut attempt_slots = BTreeSet::new();
    for attempt in &checkpoint.attempt_generations {
        let slot = (
            attempt.group_id.clone(),
            attempt.task_ref.clone(),
            attempt.role_id.clone(),
        );
        if attempt.generation == 0 || !attempt_slots.insert(slot) {
            return Err(ExecutionRuntimeError::InvalidCheckpoint(
                "checkpoint contains an invalid or duplicate attempt generation".to_string(),
            ));
        }
    }
    let mut dispatch_ids = BTreeSet::new();
    for intent in &checkpoint.dispatch_outbox {
        let Some(context) = checkpoint.executions.get(&intent.execution_id) else {
            return Err(ExecutionRuntimeError::InvalidCheckpoint(format!(
                "dispatch intent {} has no execution context",
                intent.execution_id
            )));
        };
        if intent.execution_id.trim().is_empty()
            || !dispatch_ids.insert(intent.execution_id.clone())
            || context.command != intent.command
            || context.resource_ids != normalized_resources(&intent.resource_ids)
        {
            return Err(ExecutionRuntimeError::InvalidCheckpoint(format!(
                "dispatch intent {} differs from its immutable execution context",
                intent.execution_id
            )));
        }
    }
    if checkpoint
        .cancellation_intents
        .iter()
        .any(|execution_id| !checkpoint.executions.contains_key(execution_id))
    {
        return Err(ExecutionRuntimeError::InvalidCheckpoint(
            "checkpoint cancellation references an unknown execution".to_string(),
        ));
    }
    for (execution_id, context) in &checkpoint.executions {
        if execution_id.is_empty() {
            return Err(ExecutionRuntimeError::InvalidCheckpoint(
                "checkpoint contains an empty execution id".to_string(),
            ));
        }
        if checkpoint.execution_nodes.get(execution_id) != Some(context.command.node_id()) {
            return Err(ExecutionRuntimeError::InvalidCheckpoint(format!(
                "execution {execution_id} has inconsistent node ownership"
            )));
        }
        if !checkpoint.execution_status.contains_key(execution_id) {
            return Err(ExecutionRuntimeError::InvalidCheckpoint(format!(
                "execution {execution_id} has no status"
            )));
        }
    }
    for active in &checkpoint.active_executions {
        let Some(context) = checkpoint.executions.get(&active.execution_id) else {
            return Err(ExecutionRuntimeError::InvalidCheckpoint(format!(
                "active execution {} has no context",
                active.execution_id
            )));
        };
        if context.command.group_id() != &active.group_id
            || context.command.task_ref() != &active.task_ref
            || context.command.role_id() != &active.role_id
        {
            return Err(ExecutionRuntimeError::InvalidCheckpoint(format!(
                "active execution {} differs from its command Group Task role",
                active.execution_id
            )));
        }
    }
    for execution_id in checkpoint.execution_sequences.keys() {
        if !checkpoint.execution_status.contains_key(execution_id) {
            return Err(ExecutionRuntimeError::InvalidCheckpoint(format!(
                "execution sequence {execution_id} has no status"
            )));
        }
    }
    for proof in &checkpoint.relation_proofs {
        let Some(relation) = checkpoint.relations.iter().find(|relation| {
            relation.group_id() == &proof.group_id && relation.relation_id() == &proof.relation_id
        }) else {
            continue;
        };
        let Some(context) = checkpoint.executions.get(&proof.target_execution_id) else {
            return Err(ExecutionRuntimeError::InvalidCheckpoint(format!(
                "relation {} proof references unknown target execution {}",
                proof.relation_id, proof.target_execution_id
            )));
        };
        if context.command.group_id() != relation.group_id()
            || context.command.task_ref() != relation.target_task_ref()
            || context.command.role_id() != relation.target_role_id()
        {
            return Err(ExecutionRuntimeError::InvalidCheckpoint(format!(
                "relation {} proof does not reference its target logical slot",
                proof.relation_id
            )));
        }
    }
    Ok(())
}
