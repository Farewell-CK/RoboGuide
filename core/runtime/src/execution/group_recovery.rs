//! Exact-set Group stop purposes and retained-binding continuation fences.

use super::*;
use domain::TimestampMs;

/// Explicit expected owner and repeat permission for one member of the current physical set.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GroupRecoveryMember {
    /// Current source physical attempt, never a logical Actor or adapter handle.
    pub execution_id: String,
    /// Expected current Node; the command cannot request migration.
    pub expected_node_id: NodeId,
    /// Permission to repeat the intact invocation and its already produced effects.
    pub repeat_authorized: bool,
}

/// One durable immutable authorization; whole-Group resources remain Control-owned.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GroupRecoveryStopIntent {
    /// Operator command identity, unique within its Group history.
    pub recovery_id: String,
    /// Mission-level Group whose full current physical set is frozen.
    pub group_id: ExecutionGroupId,
    /// Exact sorted original members and independent repeat permissions.
    pub members: Vec<GroupRecoveryMember>,
    /// Members already Completed at authorization; they must never be repeated.
    pub preserved_completed: BTreeSet<String>,
    /// Controller-local first authorization time, retained on restart and command retry.
    pub requested_at_ms: u64,
    /// Exclusive stop and continuation-admission deadline; never renewed.
    pub deadline_ms: u64,
    /// Original bounded interval for idempotent request comparison.
    pub timeout_ms: u64,
    /// Fixed per-Group authorization ceiling, including historical rounds.
    pub max_replacements: u32,
    /// Original receive times for actual current-owner Cancelled or natural Completed facts.
    pub confirmed_at_ms: BTreeMap<String, u64>,
    /// Complete Cancelled-source to fresh-attempt association, prepared atomically after Commit.
    pub replacements: BTreeMap<String, String>,
    /// Original atomic preparation time; absent until the complete new set is published.
    pub prepared_at_ms: Option<u64>,
    /// First receive time of an actual new-attempt admission/outcome; never a command receipt.
    pub admitted_at_ms: BTreeMap<String, u64>,
    /// Ordinary cancellation overrides continuation without erasing physical facts.
    pub aborted: bool,
}

/// Read-only Group phase; neither admission nor a receipt proves physical stopping.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize)]
pub enum GroupRecoveryDisposition {
    /// No explicit Group authorization exists.
    NotRequested,
    /// Some original current attempt lacks actual terminal stopping evidence.
    AwaitingStop,
    /// Every original member stopped; resources remain reserved pending continuation Commit.
    StopConfirmed,
    /// Complete fresh attempts exist but durable Node admission is not yet observed for all.
    ContinuationPrepared,
    /// Every fresh attempt reached local admission or completion; no further retry is automatic.
    Continued,
    /// Every original member naturally Completed, so there is nothing to repeat.
    OriginalCompleted,
    /// A real Failed outcome prevents original-world continuation.
    OriginalFailed,
    /// Stop or continuation-admission budget expired without renewal.
    BudgetExpired,
    /// Explicit ordinary cancellation disabled continuation.
    Aborted,
}

/// Produces a typed Runtime refusal without making application policy parse diagnostics.
fn refused(reason: &str) -> ExecutionRuntimeError {
    ExecutionRuntimeError::ReconciliationRequired(reason.into())
}

impl RuntimeExecutionCheckpoint {
    /// Lets versioned compositions reject new permissions hidden in a pre-Group schema.
    pub fn has_group_recovery_history(&self) -> bool {
        !self.group_recoveries.is_empty()
    }
}

impl RuntimeExecutionManager {
    /// Returns the latest bounded authorization history for a Group.
    pub fn group_recovery(&self, group_id: &ExecutionGroupId) -> Option<&GroupRecoveryStopIntent> {
        self.group_recoveries
            .iter()
            .rev()
            .find(|intent| &intent.group_id == group_id)
    }

    /// Finds one immutable command identity before comparing it with current replacement attempts.
    pub fn group_recovery_by_id(
        &self,
        group_id: &ExecutionGroupId,
        recovery_id: &str,
    ) -> Option<&GroupRecoveryStopIntent> {
        self.group_recoveries
            .iter()
            .find(|intent| &intent.group_id == group_id && intent.recovery_id == recovery_id)
    }

    /// Freezes the complete current set before creating any cancellation or consuming a budget.
    pub fn request_group_recovery_stop(
        &mut self,
        group_id: &ExecutionGroupId,
        recovery_id: &str,
        mut members: Vec<GroupRecoveryMember>,
        now: TimestampMs,
        timeout_ms: u64,
        max_replacements: u32,
    ) -> Result<(), ExecutionRuntimeError> {
        members.sort_by(|left, right| left.execution_id.cmp(&right.execution_id));
        if let Some(existing) = self.group_recovery_by_id(group_id, recovery_id) {
            return if existing.members == members
                && existing.timeout_ms == timeout_ms
                && existing.max_replacements == max_replacements
            {
                Ok(())
            } else {
                Err(refused("Group recovery command identity cannot change"))
            };
        }
        if recovery_id.trim().is_empty()
            || recovery_id.len() > 256
            || members.is_empty()
            || members.len() > 32
            || timeout_ms == 0
            || timeout_ms > 3_600_000
            || !(1..=16).contains(&max_replacements)
        {
            return Err(refused(
                "Group recovery requires bounded identity, set and policy",
            ));
        }
        let rounds: Vec<_> = self
            .group_recoveries
            .iter()
            .filter(|intent| &intent.group_id == group_id)
            .collect();
        if rounds.len() >= max_replacements as usize
            || rounds
                .iter()
                .any(|intent| intent.max_replacements != max_replacements)
            || self.group_recovery_holds_dispatch(group_id, now)
        {
            return Err(refused(
                "Group recovery history is pending, exhausted or changed",
            ));
        }
        let current = self.active_attempts_for_group(group_id);
        let ids: BTreeSet<_> = members
            .iter()
            .map(|member| member.execution_id.clone())
            .collect();
        if ids.len() != members.len() || ids != current.iter().map(|(id, _)| id.clone()).collect() {
            return Err(refused(
                "Group recovery must cover every exact current attempt once",
            ));
        }
        let mut preserved = BTreeSet::new();
        let mut shared_session = None;
        for member in &members {
            let context = self
                .executions
                .get(&member.execution_id)
                .ok_or_else(|| refused("unknown Group member"))?;
            let command = &context.command;
            let session = command.session().ok_or_else(|| {
                refused("Group continuation requires immutable execution session")
            })?;
            session
                .validate_slot(
                    command.mission_id(),
                    group_id,
                    command.task_id(),
                    command.role_id(),
                )
                .map_err(|_| refused("invalid Group execution session"))?;
            if command.group_id() != group_id
                || command.node_id() != &member.expected_node_id
                || session.slots.len() > 32
                || session.slots.iter().any(|slot| !slot.independent)
                || command.recovery_support().is_none_or(|declaration| {
                    declaration.support.operation != *command.intent().operation()
                        || !declaration.support.supports_group_continuation()
                })
                || self.recovery_stop(&member.execution_id).is_some()
                || shared_session
                    .as_ref()
                    .is_some_and(|original| *original != session)
            {
                return Err(refused(
                    "Group continuation requires exact session and group-stop owner support",
                ));
            }
            shared_session = Some(session);
            match self.execution_status(&member.execution_id) {
                Some(ExecutionStatus::Completed) => {
                    preserved.insert(member.execution_id.clone());
                }
                Some(ExecutionStatus::Failed | ExecutionStatus::Cancelled) | None => {
                    return Err(refused(
                        "Group source already failed or was cancelled without this recovery purpose",
                    ));
                }
                Some(_) if !member.repeat_authorized => {
                    return Err(refused(
                        "every affected operation needs explicit repeat authorization",
                    ));
                }
                Some(_) => {}
            }
        }
        if preserved.len() == members.len() {
            return Err(refused("no live Group operation requires recovery"));
        }
        let deadline_ms = now
            .as_millis()
            .checked_add(timeout_ms)
            .ok_or_else(|| refused("Group deadline overflow"))?;
        for member in &members {
            if !preserved.contains(&member.execution_id) {
                self.cancellation_intents
                    .insert(member.execution_id.clone());
            }
        }
        self.group_recoveries.push(GroupRecoveryStopIntent {
            recovery_id: recovery_id.into(),
            group_id: group_id.clone(),
            members,
            preserved_completed: preserved,
            requested_at_ms: now.as_millis(),
            deadline_ms,
            timeout_ms,
            max_replacements,
            confirmed_at_ms: BTreeMap::new(),
            replacements: BTreeMap::new(),
            prepared_at_ms: None,
            admitted_at_ms: BTreeMap::new(),
            aborted: false,
        });
        Ok(())
    }

    /// Records a received real terminal fact, never a Cancel command or acknowledgement.
    pub fn confirm_group_recovery_stop(&mut self, execution_id: &str, received_at: TimestampMs) {
        if !matches!(
            self.execution_status(execution_id),
            Some(ExecutionStatus::Cancelled | ExecutionStatus::Completed)
        ) {
            return;
        }
        for intent in &mut self.group_recoveries {
            if !intent.preserved_completed.contains(execution_id)
                && intent
                    .members
                    .iter()
                    .any(|member| member.execution_id == execution_id)
                && received_at.as_millis() >= intent.requested_at_ms
            {
                intent
                    .confirmed_at_ms
                    .entry(execution_id.into())
                    .or_insert(received_at.as_millis());
            }
        }
    }

    /// Retains first actual replacement admission time, including late facts without renewing budget.
    pub fn confirm_group_continuation_admission(
        &mut self,
        execution_id: &str,
        received_at: TimestampMs,
    ) {
        if !matches!(
            self.execution_status(execution_id),
            Some(
                ExecutionStatus::Accepted
                    | ExecutionStatus::Running
                    | ExecutionStatus::Completed
                    | ExecutionStatus::Failed
                    | ExecutionStatus::Cancelled
            )
        ) {
            return;
        }
        for intent in &mut self.group_recoveries {
            if intent.replacements.values().any(|id| id == execution_id)
                && intent
                    .prepared_at_ms
                    .is_some_and(|time| received_at.as_millis() >= time)
            {
                intent
                    .admitted_at_ms
                    .entry(execution_id.into())
                    .or_insert(received_at.as_millis());
            }
        }
    }

    /// Classifies actual whole-set facts and original deadlines without altering ownership.
    pub fn group_recovery_disposition(
        &self,
        group_id: &ExecutionGroupId,
        now: TimestampMs,
    ) -> GroupRecoveryDisposition {
        let Some(intent) = self.group_recovery(group_id) else {
            return GroupRecoveryDisposition::NotRequested;
        };
        if intent.aborted {
            return GroupRecoveryDisposition::Aborted;
        }
        if !intent.replacements.is_empty() {
            if intent.replacements.values().all(|id| {
                intent
                    .admitted_at_ms
                    .get(id)
                    .is_some_and(|time| *time < intent.deadline_ms && *time <= now.as_millis())
            }) {
                return GroupRecoveryDisposition::Continued;
            }
            return if now.as_millis() >= intent.deadline_ms {
                GroupRecoveryDisposition::BudgetExpired
            } else {
                GroupRecoveryDisposition::ContinuationPrepared
            };
        }
        if intent.members.iter().any(|member| {
            self.execution_status(&member.execution_id) == Some(ExecutionStatus::Failed)
        }) {
            return GroupRecoveryDisposition::OriginalFailed;
        }
        if intent.members.iter().all(|member| {
            self.execution_status(&member.execution_id) == Some(ExecutionStatus::Completed)
        }) {
            return GroupRecoveryDisposition::OriginalCompleted;
        }
        if now.as_millis() >= intent.deadline_ms {
            return GroupRecoveryDisposition::BudgetExpired;
        }
        if intent.members.iter().all(|member| {
            let command = &self.executions[&member.execution_id].command;
            self.current_attempt_id(command.group_id(), command.task_ref(), command.role_id())
                == Some(member.execution_id.as_str())
                && (intent.preserved_completed.contains(&member.execution_id)
                    || (matches!(
                        self.execution_status(&member.execution_id),
                        Some(ExecutionStatus::Cancelled | ExecutionStatus::Completed)
                    ) && intent
                        .confirmed_at_ms
                        .get(&member.execution_id)
                        .is_some_and(|time| {
                            *time < intent.deadline_ms && *time <= now.as_millis()
                        })))
        }) {
            GroupRecoveryDisposition::StopConfirmed
        } else {
            GroupRecoveryDisposition::AwaitingStop
        }
    }

    /// Fences new Ready Tasks while the frozen Group still awaits a full stop or admission set.
    pub fn group_recovery_holds_dispatch(
        &self,
        group_id: &ExecutionGroupId,
        now: TimestampMs,
    ) -> bool {
        matches!(
            self.group_recovery_disposition(group_id, now),
            GroupRecoveryDisposition::AwaitingStop
                | GroupRecoveryDisposition::StopConfirmed
                | GroupRecoveryDisposition::ContinuationPrepared
                | GroupRecoveryDisposition::BudgetExpired
        )
    }

    /// Returns Cancelled commands only after every member's current stop fact is confirmed.
    pub fn group_continuation_sources(
        &self,
        group_id: &ExecutionGroupId,
        now: TimestampMs,
    ) -> Option<Vec<(String, ExecutionCommand)>> {
        if self.group_recovery_disposition(group_id, now) != GroupRecoveryDisposition::StopConfirmed
        {
            return None;
        }
        Some(
            self.group_recovery(group_id)?
                .members
                .iter()
                .filter(|member| {
                    self.execution_status(&member.execution_id) == Some(ExecutionStatus::Cancelled)
                })
                .map(|member| {
                    (
                        member.execution_id.clone(),
                        self.executions[&member.execution_id].command.clone(),
                    )
                })
                .collect(),
        )
    }

    /// Publishes the complete association only after Control Commit and atomic Runtime preparation.
    pub fn record_group_continuation(
        &mut self,
        group_id: &ExecutionGroupId,
        now: TimestampMs,
        replacements: BTreeMap<String, String>,
    ) -> Result<(), ExecutionRuntimeError> {
        let intent = self
            .group_recovery(group_id)
            .ok_or_else(|| refused("Group stop purpose missing"))?;
        if intent.aborted
            || !intent.replacements.is_empty()
            || now.as_millis() >= intent.deadline_ms
            || !intent.members.iter().all(|member| {
                intent.preserved_completed.contains(&member.execution_id)
                    || (matches!(
                        self.execution_status(&member.execution_id),
                        Some(ExecutionStatus::Cancelled | ExecutionStatus::Completed)
                    ) && intent
                        .confirmed_at_ms
                        .get(&member.execution_id)
                        .is_some_and(|time| *time < intent.deadline_ms && *time <= now.as_millis()))
            })
        {
            return Err(refused("Group stop barrier is not satisfied"));
        }
        let expected: BTreeSet<_> = intent
            .members
            .iter()
            .map(|member| {
                replacements
                    .get(&member.execution_id)
                    .unwrap_or(&member.execution_id)
                    .clone()
            })
            .collect();
        if expected
            != self
                .active_attempts_for_group(group_id)
                .iter()
                .map(|(id, _)| id.clone())
                .collect()
        {
            return Err(refused(
                "Group current set changed during continuation preparation",
            ));
        }
        let sources: Vec<_> = intent
            .members
            .iter()
            .filter(|member| {
                self.execution_status(&member.execution_id) == Some(ExecutionStatus::Cancelled)
            })
            .map(|member| {
                (
                    member.execution_id.clone(),
                    self.executions[&member.execution_id].command.clone(),
                )
            })
            .collect();
        if replacements.keys().cloned().collect::<BTreeSet<_>>()
            != sources.iter().map(|(id, _)| id.clone()).collect()
            || replacements.values().collect::<BTreeSet<_>>().len() != replacements.len()
            || replacements.is_empty()
        {
            return Err(refused(
                "Group continuation must prepare every cancelled member exactly once",
            ));
        }
        for (source, command) in &sources {
            let replacement = &replacements[source];
            let actual = self
                .executions
                .get(replacement)
                .ok_or_else(|| refused("Group replacement not prepared"))?;
            if actual.command.node_id() != command.node_id()
                || actual.command.intent() != command.intent()
                || actual.command.session() != command.session()
                || actual.command.group_id() != command.group_id()
                || actual.command.task_ref() != command.task_ref()
                || actual.command.role_id() != command.role_id()
                || actual.command.recovery_support() != command.recovery_support()
                || actual.resource_ids != self.executions[source].resource_ids
                || self.execution_status(replacement) != Some(ExecutionStatus::Dispatched)
            {
                return Err(refused(
                    "Group replacement changed original binding or invocation",
                ));
            }
        }
        let intent = self
            .group_recoveries
            .iter_mut()
            .rev()
            .find(|intent| &intent.group_id == group_id)
            .expect("validated current recovery");
        intent.replacements = replacements;
        intent.prepared_at_ms = Some(now.as_millis());
        Ok(())
    }

    /// Identifies source cancellation purpose or a prepared replacement for bridge delivery fences.
    pub fn group_recovery_for_attempt(
        &self,
        execution_id: &str,
    ) -> Option<&GroupRecoveryStopIntent> {
        self.group_recoveries.iter().rev().find(|intent| {
            intent
                .members
                .iter()
                .any(|member| member.execution_id == execution_id)
                || intent.replacements.values().any(|id| id == execution_id)
        })
    }

    /// Confirms the entire prepared round still names the exact current logical-slot attempts.
    pub fn group_continuation_set_is_current(&self, group_id: &ExecutionGroupId) -> bool {
        let Some(intent) = self.group_recovery(group_id) else {
            return false;
        };
        if intent.aborted || intent.replacements.is_empty() {
            return false;
        }
        let expected: BTreeSet<_> = intent
            .members
            .iter()
            .map(|member| {
                intent
                    .replacements
                    .get(&member.execution_id)
                    .unwrap_or(&member.execution_id)
                    .clone()
            })
            .collect();
        expected
            == self
                .active_attempts_for_group(group_id)
                .iter()
                .map(|(id, _)| id.clone())
                .collect()
    }

    /// Retains Cancelled physical history without translating an authorized pause into Task failure.
    pub(super) fn group_recovery_preserves_cancelled(&self, execution_id: &str) -> bool {
        self.group_recovery_for_attempt(execution_id)
            .is_some_and(|intent| {
                !intent.aborted
                    && intent
                        .members
                        .iter()
                        .any(|member| member.execution_id == execution_id)
            })
    }

    /// Ordinary or Mission cancellation disables the complete related continuation round.
    pub(super) fn abort_group_recovery_for_attempt(&mut self, execution_id: &str) {
        for intent in &mut self.group_recoveries {
            if intent
                .members
                .iter()
                .any(|member| member.execution_id == execution_id)
                || intent.replacements.values().any(|id| id == execution_id)
            {
                intent.aborted = true;
            }
        }
    }

    /// Rejects corrupt history and fabricated cross-set replacements before restoring permission.
    pub(super) fn validate_group_recoveries(&self) -> Result<(), ExecutionRuntimeError> {
        let mut rounds: BTreeMap<ExecutionGroupId, Vec<&GroupRecoveryStopIntent>> = BTreeMap::new();
        for intent in &self.group_recoveries {
            let valid = (|| {
                if intent.recovery_id.trim().is_empty()
                    || intent.recovery_id.len() > 256
                    || intent.members.is_empty()
                    || intent.members.len() > 32
                    || intent.timeout_ms == 0
                    || intent.timeout_ms > 3_600_000
                    || !(1..=16).contains(&intent.max_replacements)
                    || intent.requested_at_ms.checked_add(intent.timeout_ms)
                        != Some(intent.deadline_ms)
                {
                    return false;
                }
                let ids: BTreeSet<_> = intent
                    .members
                    .iter()
                    .map(|member| member.execution_id.clone())
                    .collect();
                if ids.len() != intent.members.len()
                    || !intent.preserved_completed.is_subset(&ids)
                    || !intent.confirmed_at_ms.keys().all(|id| ids.contains(id))
                {
                    return false;
                }
                if intent.replacements.is_empty() {
                    if intent.prepared_at_ms.is_some() || !intent.admitted_at_ms.is_empty() {
                        return false;
                    }
                } else {
                    let Some(prepared) = intent.prepared_at_ms else {
                        return false;
                    };
                    if prepared < intent.requested_at_ms
                        || prepared >= intent.deadline_ms
                        || intent.confirmed_at_ms.values().any(|time| *time > prepared)
                        || intent.admitted_at_ms.iter().any(|(id, time)| {
                            *time < prepared
                                || !intent.replacements.values().any(|value| value == id)
                        })
                    {
                        return false;
                    }
                }
                let mut session = None;
                for member in &intent.members {
                    let Some(context) = self.executions.get(&member.execution_id) else {
                        return false;
                    };
                    let command = &context.command;
                    if command.group_id() != &intent.group_id
                        || command.node_id() != &member.expected_node_id
                        || command.session().is_none()
                        || session.is_some_and(|s| Some(s) != command.session())
                        || command.session().is_some_and(|session| {
                            session.slots.len() > 32
                                || session.slots.iter().any(|slot| !slot.independent)
                                || session
                                    .validate_slot(
                                        command.mission_id(),
                                        &intent.group_id,
                                        command.task_id(),
                                        command.role_id(),
                                    )
                                    .is_err()
                        })
                        || command.recovery_support().is_none_or(|support| {
                            support.support.operation != *command.intent().operation()
                                || !support.support.supports_group_continuation()
                        })
                    {
                        return false;
                    }
                    session = command.session();
                    if intent.preserved_completed.contains(&member.execution_id) {
                        if self.execution_status(&member.execution_id)
                            != Some(ExecutionStatus::Completed)
                        {
                            return false;
                        }
                    } else if !member.repeat_authorized
                        || !self.cancellation_intents.contains(&member.execution_id)
                    {
                        return false;
                    }
                    if intent
                        .confirmed_at_ms
                        .get(&member.execution_id)
                        .is_some_and(|time| {
                            *time < intent.requested_at_ms
                                || !matches!(
                                    self.execution_status(&member.execution_id),
                                    Some(ExecutionStatus::Cancelled | ExecutionStatus::Completed)
                                )
                        })
                    {
                        return false;
                    }
                }
                if !intent.replacements.is_empty() {
                    let cancelled: BTreeSet<_> = ids
                        .iter()
                        .filter(|id| self.execution_status(id) == Some(ExecutionStatus::Cancelled))
                        .cloned()
                        .collect();
                    if intent.replacements.keys().cloned().collect::<BTreeSet<_>>() != cancelled
                        || intent.replacements.values().collect::<BTreeSet<_>>().len()
                            != intent.replacements.len()
                        || !ids.iter().all(|id| {
                            intent.preserved_completed.contains(id)
                                || intent
                                    .confirmed_at_ms
                                    .get(id)
                                    .is_some_and(|time| *time < intent.deadline_ms)
                        })
                    {
                        return false;
                    }
                    for (source, replacement) in &intent.replacements {
                        let Some(actual) = self.executions.get(replacement) else {
                            return false;
                        };
                        let original = &self.executions[source];
                        if actual.command.node_id() != original.command.node_id()
                            || actual.command.group_id() != &intent.group_id
                            || actual.command.task_ref() != original.command.task_ref()
                            || actual.command.role_id() != original.command.role_id()
                            || actual.command.intent() != original.command.intent()
                            || actual.command.session() != original.command.session()
                            || actual.command.recovery_support()
                                != original.command.recovery_support()
                            || actual.resource_ids != original.resource_ids
                            || ids.contains(replacement)
                        {
                            return false;
                        }
                    }
                }
                true
            })();
            let history = rounds.entry(intent.group_id.clone()).or_default();
            if !valid
                || history.len() >= intent.max_replacements as usize
                || history.iter().any(|previous| {
                    previous.recovery_id == intent.recovery_id
                        || previous.max_replacements != intent.max_replacements
                })
            {
                return Err(ExecutionRuntimeError::InvalidCheckpoint(
                    "invalid Group recovery history".into(),
                ));
            }
            history.push(intent);
        }
        Ok(())
    }
}
