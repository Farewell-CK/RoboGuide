//! Exact-set Group recovery composition; Runtime facts precede retained Control commitment reuse.

use super::*;
use runtime::{GroupRecoveryDisposition, GroupRecoveryMember, GroupRecoveryStopIntent};

impl<E: EventSink + Clone> IntegrationRuntimeBridge<E> {
    /// Admits an explicitly repeat-authorized full current set, without releasing any resource.
    pub fn request_group_recovery(
        &mut self,
        group_id: &domain::ExecutionGroupId,
        recovery_id: &str,
        members: Vec<GroupRecoveryMember>,
        now: TimestampMs,
        timeout_ms: u64,
        max_replacements: u32,
    ) -> Result<(), IntegrationRuntimeError> {
        if self
            .runtime
            .group_recovery_by_id(group_id, recovery_id)
            .is_none()
        {
            let group = self.control.group(group_id).ok_or_else(|| {
                IntegrationRuntimeError::Protocol("unknown recovery Group".into())
            })?;
            for member in &members {
                let command = self
                    .runtime
                    .execution_command(&member.execution_id)
                    .ok_or_else(|| {
                        IntegrationRuntimeError::Protocol("unknown Group recovery attempt".into())
                    })?;
                if !self.group_continuation_support_matches(command)
                    || command.group_id() != group_id
                    || self.runtime.current_attempt_id(
                        group_id,
                        command.task_ref(),
                        command.role_id(),
                    ) != Some(member.execution_id.as_str())
                    || group.task_execution(command.task_ref()).is_none_or(|task| {
                        task.coupling_mode() != domain::ExecutionCouplingMode::Independent
                    })
                    || (self.runtime.execution_status(&member.execution_id)
                        != Some(runtime::ExecutionStatus::Completed)
                        && group.task_execution(command.task_ref()).is_none_or(|task| {
                            !task.assignments().iter().any(|assignment| {
                                assignment.role_id() == command.role_id()
                                    && assignment.node_id() == &member.expected_node_id
                            })
                        }))
                {
                    return Err(IntegrationRuntimeError::Protocol("Group recovery requires exact current binding and unchanged group-stop continuation support".into()));
                }
            }
        }
        self.runtime
            .request_group_recovery_stop(
                group_id,
                recovery_id,
                members,
                now,
                timeout_ms,
                max_replacements,
            )
            .map_err(|error| IntegrationRuntimeError::Protocol(error.to_string()))
    }

    /// Returns the original budget and full physical set, never synthetic stop evidence.
    pub fn group_recovery(
        &self,
        group_id: &domain::ExecutionGroupId,
    ) -> Option<&GroupRecoveryStopIntent> {
        self.runtime.group_recovery(group_id)
    }

    /// Reads a phase from actual Runtime evidence while retaining original receive-time budgets.
    pub fn group_recovery_disposition(
        &self,
        group_id: &domain::ExecutionGroupId,
        now: TimestampMs,
    ) -> GroupRecoveryDisposition {
        self.runtime.group_recovery_disposition(group_id, now)
    }

    /// Fences additional Ready Task dispatch while the current exact set awaits stop or admission.
    pub fn group_recovery_holds_dispatch(
        &self,
        group_id: &domain::ExecutionGroupId,
        now: TimestampMs,
    ) -> bool {
        self.runtime.group_recovery_holds_dispatch(group_id, now)
    }

    /// Requires the same frozen operation owner and exact current registration profile.
    fn group_continuation_support_matches(&self, command: &ExecutionCommand) -> bool {
        command.recovery_support().is_some_and(|original| {
            original.support.operation == *command.intent().operation()
                && original.support.supports_group_continuation()
                && self.state.node(command.node_id()).is_some_and(|node| {
                    let registration = node.registration();
                    registration
                        .execution_recovery_support(command.intent().operation())
                        .as_ref()
                        == Some(original)
                        && registration.local_systems().iter().any(|system| {
                            system.id() == &original.local_system_id
                                && system
                                    .metadata()
                                    .get(integration::EXECUTION_SESSION_METADATA_KEY)
                                    .map(String::as_str)
                                    == Some(integration::EXECUTION_SESSION_METADATA_VALUE)
                        })
                })
        })
    }

    /// Reports missing/changed deployment support independently of original stop evidence.
    pub fn group_recovery_support_unchanged(&self, group_id: &domain::ExecutionGroupId) -> bool {
        self.runtime.group_recovery(group_id).is_some_and(|intent| {
            intent.members.iter().all(|member| {
                self.runtime
                    .execution_command(&member.execution_id)
                    .is_some_and(|command| self.group_continuation_support_matches(command))
            })
        })
    }

    /// Exposes the current resource/eligibility refusal without changing any binding or event log.
    pub fn group_continuation_blocker(
        &self,
        group_id: &domain::ExecutionGroupId,
        now: TimestampMs,
    ) -> Option<String> {
        let intent = self.runtime.group_recovery(group_id)?;
        if !self.group_recovery_support_unchanged(group_id) {
            return Some("group-stop continuation registration changed or disappeared".into());
        }
        let commands = if intent.replacements.is_empty() {
            self.runtime
                .group_continuation_sources(group_id, now)?
                .into_iter()
                .map(|(_, command)| command)
                .collect()
        } else {
            self.pending_group_continuation_commands(intent)
        };
        if commands.is_empty() {
            return None;
        }
        self.control
            .group_continuation_admission(&self.state, group_id, &commands, now)
            .err()
            .map(|error| error.to_string())
    }

    /// Prepares all Cancelled slots after actual full stop proof and retained Control Commit.
    ///
    /// Preparation is atomic in the candidate bridge; application checkpointing precedes delivery.
    pub fn prepare_group_continuation(
        &mut self,
        group_id: &domain::ExecutionGroupId,
        now: TimestampMs,
        correlation: &CorrelationId,
    ) -> Result<bool, IntegrationRuntimeError> {
        let Some(sources) = self.runtime.group_continuation_sources(group_id, now) else {
            return Ok(false);
        };
        if self.group_continuation_blocker(group_id, now).is_some() {
            return Ok(false);
        }
        let recovery_id = self
            .runtime
            .group_recovery(group_id)
            .expect("validated purpose")
            .recovery_id
            .clone();
        let mut candidate = self.clone();
        let commands = sources.iter().map(|(_, command)| command.clone()).collect();
        let proposal = candidate.control.propose_group_continuation(
            &candidate.state,
            group_id,
            &recovery_id,
            commands,
            now,
            correlation,
            &mut candidate.events,
        )?;
        let committed = candidate.control.commit_group_continuation(
            &candidate.state,
            &proposal,
            now,
            correlation,
            &mut candidate.events,
        )?;
        let mut replacements = BTreeMap::new();
        for ((source_id, _), command) in sources.iter().zip(committed.commands()) {
            let new_id = candidate
                .runtime
                .allocate_attempt_id(group_id, command.task_ref(), command.role_id())
                .map_err(|error| IntegrationRuntimeError::Protocol(error.to_string()))?;
            candidate.prepare_task_bound_with_session(
                new_id.clone(),
                group_id,
                command.task_ref(),
                command.role_id(),
                command.intent().clone(),
                command.session().cloned(),
                now,
                correlation.clone(),
            )?;
            replacements.insert(source_id.clone(), new_id);
        }
        candidate
            .runtime
            .record_group_continuation(group_id, now, replacements)
            .map_err(|error| IntegrationRuntimeError::Protocol(error.to_string()))?;
        *self = candidate;
        Ok(true)
    }

    /// Lists the latest explicit Group purposes for timer-driven evidence reconciliation only.
    pub fn group_recovery_ids(&self) -> Vec<domain::ExecutionGroupId> {
        self.control
            .group_ids()
            .into_iter()
            .filter(|group| self.runtime.group_recovery(group).is_some())
            .collect()
    }

    /// Rechecks the original round before any Group cancellation or fresh Execute is delivered.
    pub(super) fn group_recovery_delivery_permitted(
        &self,
        execution_id: &str,
        now: Option<TimestampMs>,
        cancellation: bool,
    ) -> bool {
        let Some(intent) = self.runtime.group_recovery_for_attempt(execution_id) else {
            return true;
        };
        if intent.aborted {
            return cancellation;
        }
        let Some(now) = now else {
            return false;
        };
        if now.as_millis() >= intent.deadline_ms
            || !self.group_recovery_support_unchanged(&intent.group_id)
        {
            return false;
        }
        if !cancellation
            && intent
                .members
                .iter()
                .any(|member| member.execution_id == execution_id)
        {
            return false;
        }
        if intent.replacements.values().any(|id| id == execution_id) {
            if !self
                .runtime
                .group_continuation_set_is_current(&intent.group_id)
            {
                return false;
            }
            let commands = self.pending_group_continuation_commands(intent);
            return self
                .control
                .group_continuation_admission(&self.state, &intent.group_id, &commands, now)
                .is_ok();
        }
        true
    }

    /// A newly Completed peer may follow normal satisfaction release; still-active slots retain ownership.
    fn pending_group_continuation_commands(
        &self,
        intent: &GroupRecoveryStopIntent,
    ) -> Vec<ExecutionCommand> {
        intent
            .replacements
            .iter()
            .filter(|(_, replacement)| {
                self.runtime.execution_status(replacement)
                    != Some(runtime::ExecutionStatus::Completed)
            })
            .filter_map(|(source, _)| self.runtime.execution_command(source).cloned())
            .collect()
    }
}
