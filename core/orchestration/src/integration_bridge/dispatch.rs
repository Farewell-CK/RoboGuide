//! Runtime dispatch, cancellation, and bound-command orchestration.

use super::*;

/// Technical deployment support is distinct from explicit repeat permission and physical stop.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize)]
pub enum RecoverySupportDisposition {
    /// No declaration was frozen for this attempt; newer registration cannot upgrade it.
    NotDeclared,
    /// A declaration cannot be used with a different canonical operation.
    InvalidDeclaration,
    /// Cancelling one invocation may affect another physical execution.
    StopNotIsolated,
    /// The local deployment cannot preserve context for a new attempt after stopping.
    ContinuationUnsupported,
    /// Current exact owner/operation support is missing or differs from the original snapshot.
    RegistrationChanged,
    /// Technical support matches; recovery still needs explicit authorization and actual stop.
    Supported,
}

/// Read-only source-attributed support view, never a release or redispatch authorization.
#[derive(Debug, Clone, serde::Serialize)]
pub struct RecoveryDeploymentSupport {
    /// Dispatch-time declaration retained with the exact physical attempt.
    pub original: Option<domain::ExecutionRecoverySupport>,
    /// Latest exact operation-owner declaration; absent facts remain unknown.
    pub current: Option<domain::ExecutionRecoverySupport>,
    /// Stable support classification; applications never parse diagnostic strings.
    pub disposition: RecoverySupportDisposition,
}

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
        let session_json = command
            .session()
            .map(serde_json::to_string)
            .transpose()
            .map_err(|error| IntegrationRuntimeError::Protocol(error.to_string()))?
            .unwrap_or_default();
        let route_result = self.router.execute_with_session(
            command.node_id().as_str(),
            format!("dispatch-{execution_id}"),
            execution_id.to_string(),
            invocation_from_command(command),
            resource_ids
                .iter()
                .map(|resource_id| resource_id.as_str().to_string())
                .collect(),
            session_json,
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
            if self
                .runtime
                .is_stopped_recovery_replacement(&intent.execution_id)
                && !self.execution_recovery_supported(&intent.command)
            {
                continue;
            }
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
            if !status.is_terminal() || self.runtime.recovery_stop(execution_id).is_some() {
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
            if self
                .runtime
                .recovery_stop(execution_id)
                .is_some_and(|intent| !intent.aborted)
                && !self
                    .runtime
                    .attempt_history()
                    .into_iter()
                    .find(|attempt| attempt.execution_id() == execution_id)
                    .is_some_and(|attempt| self.execution_recovery_supported(attempt.command()))
            {
                continue;
            }
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
        self.prepare_task_bound_with_session(
            execution_id,
            group_id,
            task_ref,
            role_id,
            intent,
            None,
            now,
            correlation_id,
        )
    }

    /// Persists one Task-bound command with accepted-plan session topology.
    #[allow(clippy::too_many_arguments)]
    pub fn prepare_task_bound_with_session(
        &mut self,
        execution_id: String,
        group_id: &domain::ExecutionGroupId,
        task_ref: &domain::TaskRef,
        role_id: &domain::RoleId,
        intent: domain::ExecutionIntent,
        session: Option<domain::ExecutionSessionDescriptor>,
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
                return Err(IntegrationRuntimeError::CoordinationNotReady);
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
        if let Some(ref session) = session {
            session
                .validate_slot(task_ref.mission_id(), group_id, task_ref.task_id(), role_id)
                .map_err(IntegrationRuntimeError::Protocol)?;
        }
        let recovery_support = self.state.node(&node_id).and_then(|snapshot| {
            snapshot
                .registration()
                .execution_recovery_support(intent.operation())
        });
        let mut command = ExecutionCommand::new(
            task_ref.mission_id().clone(),
            task_ref.task_id().clone(),
            group_id.clone(),
            role_id.clone(),
            node_id,
            intent,
            correlation_id,
        );
        if let Some(session) = session {
            command = command.with_session(session);
        }
        if let Some(support) = recovery_support {
            command = command.with_recovery_support(support);
        }
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

    /// Authorizes an exact current Role stop; physical and resource ownership remain unchanged.
    pub fn request_execution_recovery(
        &mut self,
        execution_id: &str,
        expected_node: &NodeId,
        now: TimestampMs,
        timeout_ms: u64,
        max_replacements: u32,
    ) -> Result<(), IntegrationRuntimeError> {
        let command = self
            .runtime
            .attempt_history()
            .into_iter()
            .find(|attempt| attempt.execution_id() == execution_id)
            .map(|attempt| attempt.command().clone())
            .ok_or_else(|| {
                IntegrationRuntimeError::Protocol("unknown recovery execution".into())
            })?;
        let bound = self
            .control
            .group(command.group_id())
            .and_then(|group| group.task_execution(command.task_ref()))
            .is_some_and(|task| {
                task.assignments().iter().any(|assignment| {
                    assignment.role_id() == command.role_id()
                        && assignment.node_id() == expected_node
                })
            });
        if !bound {
            return Err(IntegrationRuntimeError::Protocol(
                "recovery owner is not the current Control binding".into(),
            ));
        }
        if !self.execution_recovery_supported(&command) {
            return Err(IntegrationRuntimeError::Protocol(
                "recovery requires unchanged dispatch-time owner support for isolated stop and context-preserving repetition".into(),
            ));
        }
        self.runtime
            .request_recovery_stop(
                execution_id,
                expected_node,
                now,
                timeout_ms,
                max_replacements,
            )
            .map_err(|error| IntegrationRuntimeError::Protocol(error.to_string()))
    }

    /// Returns the original stop authorization, never synthetic physical-stop evidence.
    pub fn execution_recovery_stop(
        &self,
        execution_id: &str,
    ) -> Option<&runtime::RecoveryStopIntent> {
        self.runtime.recovery_stop(execution_id)
    }

    /// Reads recovery phase without changing a budget, ownership or command delivery.
    pub fn execution_recovery_disposition(
        &self,
        execution_id: &str,
        now: TimestampMs,
    ) -> runtime::RecoveryStopDisposition {
        self.runtime.recovery_stop_disposition(execution_id, now)
    }

    /// Lists only current attempts with timely real stop evidence awaiting Control release.
    pub fn stopped_recovery_commands(&self, now: TimestampMs) -> Vec<ExecutionCommand> {
        self.runtime
            .stopped_recovery_commands(now)
            .into_iter()
            .filter(|command| self.execution_recovery_supported(command))
            .collect()
    }

    /// Verifies the current exact stopped command before Control can partially release it.
    pub fn recovery_stop_ready(&self, command: &ExecutionCommand, now: TimestampMs) -> bool {
        self.runtime.recovery_stop_ready(command, now) && self.execution_recovery_supported(command)
    }

    /// Requires original exact operation/owner facts and the same current registration declaration.
    pub fn execution_recovery_supported(&self, command: &ExecutionCommand) -> bool {
        self.recovery_deployment_support(command).disposition
            == RecoverySupportDisposition::Supported
    }

    /// Compares original and current declarations without changing a budget, plan or local state.
    pub fn recovery_deployment_support(
        &self,
        command: &ExecutionCommand,
    ) -> RecoveryDeploymentSupport {
        let original = command.recovery_support().cloned();
        let current = self.state.node(command.node_id()).and_then(|snapshot| {
            snapshot
                .registration()
                .execution_recovery_support(command.intent().operation())
        });
        let disposition = match original.as_ref() {
            None => RecoverySupportDisposition::NotDeclared,
            Some(declaration) if declaration.support.operation != *command.intent().operation() => {
                RecoverySupportDisposition::InvalidDeclaration
            }
            Some(declaration)
                if declaration.support.stop_scope != domain::ExecutionStopScope::Execution =>
            {
                RecoverySupportDisposition::StopNotIsolated
            }
            Some(declaration)
                if declaration.support.continuation
                    != domain::ExecutionContinuation::RepeatAfterStop =>
            {
                RecoverySupportDisposition::ContinuationUnsupported
            }
            Some(declaration) if current.as_ref() != Some(declaration) => {
                RecoverySupportDisposition::RegistrationChanged
            }
            Some(_) => RecoverySupportDisposition::Supported,
        };
        RecoveryDeploymentSupport {
            original,
            current,
            disposition,
        }
    }

    /// Allows the initial partial release once; a same-owner Rebind must not release it again.
    pub fn recovery_release_ready(&self, command: &ExecutionCommand, now: TimestampMs) -> bool {
        self.recovery_stop_ready(command, now)
            && self
                .runtime
                .current_attempt_id(command.group_id(), command.task_ref(), command.role_id())
                .and_then(|id| self.runtime.recovery_stop(id))
                .is_some_and(|intent| !intent.release_authorized)
    }

    /// Records that the sole Control authority released this stopped binding once.
    pub fn mark_recovery_release(&mut self, command: &ExecutionCommand) {
        self.runtime.mark_recovery_release(command);
    }

    /// Requires the same timely stopped attempt before pending recovery can Commit or Rebind.
    pub fn recovery_release_permitted(
        &self,
        need: &control::RoleRecoveryNeed,
        now: TimestampMs,
    ) -> bool {
        self.runtime
            .current_attempt_id(need.group_id(), need.task_ref(), need.role_id())
            .and_then(|id| self.runtime.recovery_stop(id))
            .is_some_and(|intent| {
                intent.release_authorized
                    && self
                        .runtime
                        .attempt_history()
                        .into_iter()
                        .find(|attempt| {
                            attempt.execution_id() == intent.execution_id
                                && attempt.command().node_id() == need.current_node_id()
                        })
                        .is_some_and(|attempt| self.recovery_stop_ready(attempt.command(), now))
            })
    }

    /// Identifies expired or aborted released recovery so Control may abort only its pending Commit.
    pub fn recovery_release_expired(
        &self,
        need: &control::RoleRecoveryNeed,
        now: TimestampMs,
    ) -> bool {
        self.runtime
            .current_attempt_id(need.group_id(), need.task_ref(), need.role_id())
            .and_then(|id| self.runtime.recovery_stop(id))
            .is_some_and(|intent| {
                intent.release_authorized
                    && (intent.aborted || now.as_millis() >= intent.deadline_ms)
            })
    }

    /// Fences already released recovery after an operation-owner declaration changes or disappears.
    pub fn recovery_release_invalidated(&self, need: &control::RoleRecoveryNeed) -> bool {
        self.runtime
            .current_attempt_id(need.group_id(), need.task_ref(), need.role_id())
            .and_then(|id| self.runtime.recovery_stop(id))
            .is_some_and(|intent| {
                intent.release_authorized
                    && self
                        .runtime
                        .attempt_history()
                        .into_iter()
                        .find(|attempt| attempt.execution_id() == intent.execution_id)
                        .is_some_and(|attempt| {
                            !self.execution_recovery_supported(attempt.command())
                        })
            })
    }

    /// Requires the same stopped attempt and valid explicit budget before preparing a replacement.
    pub fn recovery_attempt_permitted(
        &self,
        group_id: &domain::ExecutionGroupId,
        task_ref: &domain::TaskRef,
        role_id: &domain::RoleId,
        now: TimestampMs,
    ) -> bool {
        self.runtime
            .current_attempt_id(group_id, task_ref, role_id)
            .and_then(|id| self.runtime.recovery_stop(id))
            .is_some_and(|intent| {
                intent.release_authorized
                    && self
                        .runtime
                        .attempt_history()
                        .into_iter()
                        .find(|attempt| attempt.execution_id() == intent.execution_id)
                        .is_some_and(|attempt| self.recovery_stop_ready(attempt.command(), now))
            })
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

    /// Returns the current physical attempt for one exact committed Task Role slot.
    pub fn current_task_attempt_id(
        &self,
        group_id: &domain::ExecutionGroupId,
        task_ref: &domain::TaskRef,
        role_id: &domain::RoleId,
    ) -> Option<&str> {
        self.runtime.current_attempt_id(group_id, task_ref, role_id)
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
