//! Explicit recovery cancellation purpose and time/count fences before Control may release.

use super::*;
use domain::TimestampMs;

/// Durable authorization to stop one current physical attempt before replacing its logical Role.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct RecoveryStopIntent {
    /// Physical attempt whose exact owner and command remain in Runtime.
    pub execution_id: String,
    /// Controller-local authorization time.
    pub requested_at_ms: u64,
    /// Exclusive local deadline covering stop and replacement admission.
    pub deadline_ms: u64,
    /// Fixed timeout retained for idempotent repeated command validation.
    pub timeout_ms: u64,
    /// Immutable per-logical-Role replacement ceiling.
    pub max_replacements: u32,
    /// Original receive time of real current-owner Cancelled evidence, never a receipt.
    pub confirmed_at_ms: Option<u64>,
    /// Whether Control has already partially released the confirmed stopped binding.
    pub release_authorized: bool,
    /// An explicit ordinary or Mission cancel overrides replacement without erasing history.
    pub aborted: bool,
}

/// Read-only recovery phase; a requested Cancel never proves physical stop.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize)]
pub enum RecoveryStopDisposition {
    /// No explicit recovery command was admitted for this attempt.
    NotRequested,
    /// Awaiting actual cancellation from the current physical owner.
    AwaitingStop,
    /// Real stop is confirmed; Control has not yet released this binding.
    StopConfirmed,
    /// Control released the stopped Role; replacement remains pending.
    ReplacementPending,
    /// The time budget expired and cannot be renewed by repeating the command.
    BudgetExpired,
    /// Ordinary or Mission cancellation disabled replacement.
    Aborted,
    /// A new physical attempt replaced this retained attempt.
    Superseded,
    /// The original attempt completed or failed instead of being stopped.
    OriginalTerminal,
}

/// Serializable budget survives new physical attempts and process restart.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub(super) struct RecoveryBudget {
    /// Logical slot owning the count, independent of any physical attempt.
    pub(super) slot: ExecutionSlot,
    /// First authorization fixes the ceiling; later commands cannot silently enlarge it.
    pub(super) limit: u32,
    /// Number of distinct physical attempts authorized for stop-and-replace.
    pub(super) used: u32,
}

impl RuntimeExecutionManager {
    /// Identifies a replacement from retained stop/release history, never from Node identity.
    ///
    /// This read-only query survives checkpoint restore and also covers same-owner retries.
    /// It does not grant permission or infer that a prepared Execute has reached the Node.
    pub fn is_stopped_recovery_replacement(&self, execution_id: &str) -> bool {
        let Some(replacement) = self.executions.get(execution_id) else {
            return false;
        };
        self.recovery_stops.values().any(|intent| {
            intent.execution_id != execution_id
                && intent.release_authorized
                && self
                    .executions
                    .get(&intent.execution_id)
                    .is_some_and(|original| {
                        original.command.group_id() == replacement.command.group_id()
                            && original.command.task_ref() == replacement.command.task_ref()
                            && original.command.role_id() == replacement.command.role_id()
                    })
        })
    }

    /// Records an explicitly repeat-authorized cancellation without freeing Control resources.
    pub fn request_recovery_stop(
        &mut self,
        execution_id: &str,
        expected_node: &NodeId,
        now: TimestampMs,
        timeout_ms: u64,
        max_replacements: u32,
    ) -> Result<(), ExecutionRuntimeError> {
        let context = self.executions.get(execution_id).ok_or_else(|| {
            ExecutionRuntimeError::ReconciliationRequired("recovery execution is unknown".into())
        })?;
        let command = &context.command;
        let slot = (
            command.group_id().clone(),
            command.task_ref().clone(),
            command.role_id().clone(),
        );
        if command.node_id() != expected_node
            || self.active_executions.get(&slot).map(String::as_str) != Some(execution_id)
        {
            return Err(ExecutionRuntimeError::NodeOwnership(
                "recovery command references a stale attempt or wrong owner".into(),
            ));
        }
        if self.group_recovery_holds_dispatch(command.group_id(), now) {
            return Err(ExecutionRuntimeError::ReconciliationRequired(
                "an exact-set Group recovery already owns this recovery purpose".into(),
            ));
        }
        let support = command.recovery_support().ok_or_else(|| {
            ExecutionRuntimeError::ReconciliationRequired(
                "physical attempt has no dispatch-time execution recovery declaration".into(),
            )
        })?;
        if support.support.operation != *command.intent().operation()
            || !support.support.supports_role_retry()
        {
            return Err(ExecutionRuntimeError::ReconciliationRequired(
                "Role recovery requires isolated execution stop and context-preserving repetition"
                    .into(),
            ));
        }
        if let Some(intent) = self.recovery_stops.get(execution_id) {
            return if !intent.aborted
                && intent.timeout_ms == timeout_ms
                && intent.max_replacements == max_replacements
            {
                Ok(())
            } else {
                Err(ExecutionRuntimeError::ExecutionConflict(
                    "recovery command cannot change its original budget".into(),
                ))
            };
        }
        if self
            .execution_status(execution_id)
            .is_some_and(ExecutionStatus::is_terminal)
            || timeout_ms == 0
            || timeout_ms > 3_600_000
            || !(1..=16).contains(&max_replacements)
        {
            return Err(ExecutionRuntimeError::ReconciliationRequired(
                "terminal execution or invalid recovery budget".into(),
            ));
        }
        let deadline_ms = now.as_millis().checked_add(timeout_ms).ok_or_else(|| {
            ExecutionRuntimeError::ReconciliationRequired("recovery deadline overflow".into())
        })?;
        let budget = self
            .recovery_budgets
            .entry(slot.clone())
            .or_insert(RecoveryBudget {
                slot,
                limit: max_replacements,
                used: 0,
            });
        if budget.limit != max_replacements || budget.used >= budget.limit {
            return Err(ExecutionRuntimeError::ReconciliationRequired(
                "logical Role recovery budget is exhausted or changed".into(),
            ));
        }
        budget.used += 1;
        self.recovery_stops.insert(
            execution_id.into(),
            RecoveryStopIntent {
                execution_id: execution_id.into(),
                requested_at_ms: now.as_millis(),
                deadline_ms,
                timeout_ms,
                max_replacements,
                confirmed_at_ms: None,
                release_authorized: false,
                aborted: false,
            },
        );
        self.cancellation_intents.insert(execution_id.into());
        Ok(())
    }

    /// Binds actual Cancelled reduction to its original receive time without comparing Node clocks.
    pub fn confirm_recovery_stop(&mut self, execution_id: &str, received_at: TimestampMs) {
        if self.execution_status(execution_id) == Some(ExecutionStatus::Cancelled)
            && let Some(intent) = self.recovery_stops.get_mut(execution_id)
            && intent.confirmed_at_ms.is_none()
            && received_at.as_millis() >= intent.requested_at_ms
        {
            intent.confirmed_at_ms = Some(received_at.as_millis());
        }
    }

    /// Returns original stop authorization and confirmation, including expired history.
    pub fn recovery_stop(&self, execution_id: &str) -> Option<&RecoveryStopIntent> {
        self.recovery_stops.get(execution_id)
    }

    /// Reports recovery independently of Task, Mission and local execution outcomes.
    pub fn recovery_stop_disposition(
        &self,
        execution_id: &str,
        now: TimestampMs,
    ) -> RecoveryStopDisposition {
        let Some(intent) = self.recovery_stops.get(execution_id) else {
            return RecoveryStopDisposition::NotRequested;
        };
        let command = &self.executions[execution_id].command;
        if self.current_attempt_id(command.group_id(), command.task_ref(), command.role_id())
            != Some(execution_id)
        {
            return RecoveryStopDisposition::Superseded;
        }
        if intent.aborted {
            return RecoveryStopDisposition::Aborted;
        }
        if matches!(
            self.execution_status(execution_id),
            Some(ExecutionStatus::Completed | ExecutionStatus::Failed)
        ) {
            return RecoveryStopDisposition::OriginalTerminal;
        }
        if now.as_millis() >= intent.deadline_ms {
            return RecoveryStopDisposition::BudgetExpired;
        }
        if intent.release_authorized {
            return RecoveryStopDisposition::ReplacementPending;
        }
        if intent.confirmed_at_ms.is_some() {
            return RecoveryStopDisposition::StopConfirmed;
        }
        RecoveryStopDisposition::AwaitingStop
    }

    /// Requires current attempt, real cancellation and an unexpired replacement budget.
    pub fn recovery_stop_ready(&self, command: &ExecutionCommand, now: TimestampMs) -> bool {
        self.current_attempt_id(command.group_id(), command.task_ref(), command.role_id())
            .and_then(|id| self.recovery_stops.get(id))
            .is_some_and(|intent| {
                !intent.aborted
                    && self.execution_status(&intent.execution_id)
                        == Some(ExecutionStatus::Cancelled)
                    && self
                        .executions
                        .get(&intent.execution_id)
                        .is_some_and(|context| {
                            context.command == *command
                                && command.recovery_support().is_some_and(|declaration| {
                                    declaration.support.operation == *command.intent().operation()
                                        && declaration.support.supports_role_retry()
                                })
                        })
                    && intent
                        .confirmed_at_ms
                        .is_some_and(|time| time < intent.deadline_ms)
                    && intent
                        .confirmed_at_ms
                        .is_some_and(|time| now.as_millis() >= time)
                    && now.as_millis() < intent.deadline_ms
            })
    }

    /// Returns current stopped attempts awaiting the single Control-owned partial release.
    pub fn stopped_recovery_commands(&self, now: TimestampMs) -> Vec<ExecutionCommand> {
        self.recovery_stops
            .values()
            .filter(|intent| !intent.release_authorized)
            .filter_map(|intent| {
                self.executions
                    .get(&intent.execution_id)
                    .map(|context| &context.command)
            })
            .filter(|command| self.recovery_stop_ready(command, now))
            .cloned()
            .collect()
    }

    /// Marks a successful Control partial release; repeated timer ticks cannot release twice.
    pub fn mark_recovery_release(&mut self, command: &ExecutionCommand) {
        if let Some(id) = self
            .current_attempt_id(command.group_id(), command.task_ref(), command.role_id())
            .map(str::to_owned)
            && let Some(intent) = self.recovery_stops.get_mut(&id)
        {
            intent.release_authorized = true;
        }
    }

    /// Validates stop purposes and budgets before restoring any replacement permission.
    pub(super) fn validate_recovery_stops(&self) -> Result<(), ExecutionRuntimeError> {
        for (id, intent) in &self.recovery_stops {
            let Some(context) = self.executions.get(id) else {
                return Err(ExecutionRuntimeError::InvalidCheckpoint(
                    "recovery stop has no execution".into(),
                ));
            };
            let slot = (
                context.command.group_id().clone(),
                context.command.task_ref().clone(),
                context.command.role_id().clone(),
            );
            if id != &intent.execution_id
                || intent.timeout_ms == 0
                || intent.timeout_ms > 3_600_000
                || intent.requested_at_ms.checked_add(intent.timeout_ms) != Some(intent.deadline_ms)
                || !self.cancellation_intents.contains(id)
                || !self.recovery_budgets.get(&slot).is_some_and(|budget| {
                    budget.limit == intent.max_replacements
                        && budget.used > 0
                        && budget.used <= budget.limit
                })
                || intent.confirmed_at_ms.is_some_and(|time| {
                    time < intent.requested_at_ms
                        || self.execution_status(id) != Some(ExecutionStatus::Cancelled)
                })
                || (intent.release_authorized
                    && !intent
                        .confirmed_at_ms
                        .is_some_and(|time| time < intent.deadline_ms))
            {
                return Err(ExecutionRuntimeError::InvalidCheckpoint(
                    "invalid recovery stop authorization or confirmation".into(),
                ));
            }
        }
        for (slot, budget) in &self.recovery_budgets {
            if slot != &budget.slot
                || !(1..=16).contains(&budget.limit)
                || budget.used == 0
                || budget.used > budget.limit
                || self
                    .recovery_stops
                    .values()
                    .filter(|intent| {
                        self.executions
                            .get(&intent.execution_id)
                            .is_some_and(|context| {
                                (
                                    context.command.group_id(),
                                    context.command.task_ref(),
                                    context.command.role_id(),
                                ) == (&slot.0, &slot.1, &slot.2)
                            })
                    })
                    .count()
                    != budget.used as usize
            {
                return Err(ExecutionRuntimeError::InvalidCheckpoint(
                    "invalid recovery budget history".into(),
                ));
            }
        }
        Ok(())
    }
}
