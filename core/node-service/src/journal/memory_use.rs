//! Sole Node-side durable use intervals, irreversible fences and once-admitted backend effects.

use super::*;
use domain::{MemoryUseControl, MemoryUseOperation, MemoryUseReceipt, MemoryUseStatus};
use serde_json::Value;

/// Original command and receive budget; process restart cannot renew either.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct JournalMemoryUseCommand {
    /// Exact authenticated original control content.
    pub command: MemoryUseControl,
    /// Original same-kernel/time-namespace receive budget, including bounded revoke reconciliation.
    pub budget: MemoryReceiverBudget,
    /// Original aggregate outcome, absent if admission/receipt was interrupted.
    pub receipt: Option<MemoryUseReceipt>,
}

/// Original provider request retained before its one possible write effect.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct JournalMemoryUseEffect {
    /// Exact complete private-profile body, never reconstructed with a newer clock or configuration.
    pub request: Value,
    /// Reserved readonly reconciliation attempts, including interrupted queries.
    pub query_count: u32,
    /// Immutable exact provider reply, absent while original outcome remains unknown.
    pub receipt: Option<Value>,
}

/// Separates immutable physical/configuration ownership from contiguous policy/membership intervals.
fn purpose(command: &MemoryUseControl) -> Result<String, JournalError> {
    let mut value = serde_json::to_value(command)?;
    let object = value
        .as_object_mut()
        .expect("control serializes as an object");
    for key in [
        "command_id",
        "operation",
        "generation",
        "valid_for_ms",
        "registry_revision",
        "membership",
    ] {
        object.remove(key);
    }
    Ok(serde_json::to_string(&value)?)
}

impl ExecutionJournal {
    /// Reads retained original authority without acquiring permission or refreshing receive time.
    pub fn memory_use_command(
        &self,
        id: &str,
    ) -> Result<Option<JournalMemoryUseCommand>, JournalError> {
        let connection = self.lock_connection()?;
        read_command(&connection, id)
    }

    /// Commits original receive time/head before backend effects; revoke may precede any grant.
    pub fn admit_memory_use_control(
        &self,
        command: &MemoryUseControl,
        now: &ReceiverClockObservation,
        remaining_budget_ms: u64,
    ) -> Result<JournalMemoryUseCommand, JournalError> {
        command.validate().map_err(JournalError::InvalidSpec)?;
        let mut connection = self.lock_connection()?;
        let tx = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        if let Some(original) = read_command(&tx, &command.command_id)? {
            if original.command != *command {
                return Err(JournalError::InvalidSpec(
                    "original Memory-use command changed".into(),
                ));
            }
            return Ok(original);
        }
        let head: Option<(String,u64,bool)> = tx.query_row(
            "SELECT purpose_json,highest_generation,revoked FROM memory_use_heads WHERE lease_id=?1",
            [&command.lease_id], |row| Ok((row.get(0)?,row.get(1)?,row.get(2)?)),
        ).optional()?;
        let identity = purpose(command)?;
        if head.as_ref().is_some_and(|row| row.0 != identity) {
            return Err(JournalError::InvalidSpec(
                "Memory-use original purpose/configuration changed".into(),
            ));
        }
        let budget_ms = match command.operation {
            MemoryUseOperation::Grant => {
                if remaining_budget_ms == 0 || remaining_budget_ms > command.valid_for_ms {
                    return Err(JournalError::InvalidSpec(
                        "Memory-use original grant budget is unavailable".into(),
                    ));
                }
                let context = memory_preparation::read_preparation(&tx, &command.preparation_id)?
                    .ok_or_else(|| {
                    JournalError::InvalidSpec("Memory-use has no original prepared context".into())
                })?;
                command
                    .validate_preparation(&context.preparation)
                    .map_err(JournalError::InvalidSpec)?;
                if context.revoked
                    || context.configuration_digest != command.configuration_digest
                    || context
                        .preparation
                        .inputs
                        .iter()
                        .any(|input| !input.consumer.supports_use_lease())
                {
                    return Err(JournalError::InvalidSpec(
                        "Memory-use original context is revoked or lacks explicit support".into(),
                    ));
                }
                if let Some((_, highest, revoked)) = &head {
                    if *revoked || *highest == 0 {
                        return Err(JournalError::InvalidSpec(
                            "Memory-use original interval was revoked".into(),
                        ));
                    }
                    let previous_id = format!("memory-use-grant-{}-{highest}", command.lease_id);
                    let previous = read_command(&tx, &previous_id)?.ok_or_else(|| {
                        JournalError::Corrupt("Memory-use head lost its original command".into())
                    })?;
                    if command.generation != highest.saturating_add(1)
                        || previous.budget.remaining_ms(now) == 0
                        || previous
                            .receipt
                            .as_ref()
                            .is_none_or(|receipt| receipt.status != MemoryUseStatus::Granted)
                        || command.registry_revision < previous.command.registry_revision
                        || command.membership.generation < previous.command.membership.generation
                        || (command.membership.generation == previous.command.membership.generation
                            && command.membership != previous.command.membership)
                    {
                        return Err(JournalError::InvalidSpec("Memory-use renewal is revoked, expired, ambiguous, discontinuous or stale".into()));
                    }
                } else if command.generation != 1 || context.budget.remaining_ms(now) == 0 {
                    return Err(JournalError::InvalidSpec(
                        "first Memory-use interval lacks its original live preparation".into(),
                    ));
                }
                remaining_budget_ms
            }
            MemoryUseOperation::Revoke => 10_000,
        };
        let budget = MemoryReceiverBudget::admit(now, budget_ms)?;
        if head.is_none() {
            let count: u64 = tx.query_row("SELECT COUNT(*) FROM memory_use_heads", [], |row| {
                row.get(0)
            })?;
            if count >= 4096 {
                return Err(JournalError::InvalidSpec(
                    "Memory-use authority is full".into(),
                ));
            }
            tx.execute(
                "INSERT INTO memory_use_heads VALUES (?1,?2,?3,?4)",
                params![
                    command.lease_id,
                    identity,
                    command.generation,
                    command.operation == MemoryUseOperation::Revoke
                ],
            )?;
        } else if command.operation == MemoryUseOperation::Revoke {
            tx.execute(
                "UPDATE memory_use_heads SET revoked=1 WHERE lease_id=?1",
                [&command.lease_id],
            )?;
        } else {
            tx.execute(
                "UPDATE memory_use_heads SET highest_generation=?1 WHERE lease_id=?2",
                params![command.generation, command.lease_id],
            )?;
        }
        tx.execute("INSERT INTO memory_use_commands(command_id,lease_id,generation,command_json,budget_json) VALUES (?1,?2,?3,?4,?5)",
            params![command.command_id,command.lease_id,command.generation,serde_json::to_string(command)?,serde_json::to_string(&budget)?])?;
        if command.operation == MemoryUseOperation::Grant {
            tx.execute("DELETE FROM memory_use_commands WHERE lease_id=?1 AND generation>0 AND generation<?2", params![command.lease_id,command.generation.saturating_sub(3)])?;
        }
        tx.commit()?;
        Ok(JournalMemoryUseCommand {
            command: command.clone(),
            budget,
            receipt: None,
        })
    }

    /// Checks only retained current grant facts; old receipts, restart and newer clocks grant no time.
    pub fn current_memory_use(
        &self,
        preparation_id: &str,
        now: &ReceiverClockObservation,
    ) -> Result<JournalMemoryUseCommand, JournalError> {
        let connection = self.lock_connection()?;
        let lease = domain::memory_use_lease_id(preparation_id);
        let head: Option<(u64, bool)> = connection
            .query_row(
                "SELECT highest_generation,revoked FROM memory_use_heads WHERE lease_id=?1",
                [&lease],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .optional()?;
        let (generation, revoked) = head.ok_or_else(|| {
            JournalError::InvalidSpec("current Memory-use permission is absent".into())
        })?;
        let context_revoked: bool = connection.query_row(
            "SELECT EXISTS(SELECT 1 FROM memory_context_revocations WHERE preparation_id=?1)",
            [preparation_id],
            |row| row.get(0),
        )?;
        if revoked || context_revoked || generation == 0 {
            return Err(JournalError::InvalidSpec(
                "current Memory-use permission was revoked".into(),
            ));
        }
        let command = read_command(
            &connection,
            &format!("memory-use-grant-{lease}-{generation}"),
        )?
        .ok_or_else(|| JournalError::Corrupt("Memory-use head lost its current command".into()))?;
        if command.budget.remaining_ms(now) == 0
            || command
                .receipt
                .as_ref()
                .is_none_or(|receipt| receipt.status != MemoryUseStatus::Granted)
        {
            return Err(JournalError::InvalidSpec(
                "current Memory-use permission is expired or ambiguous".into(),
            ));
        }
        let context = memory_preparation::read_preparation(&connection, preparation_id)?
            .ok_or_else(|| {
                JournalError::Corrupt("Memory-use lost its original prepared source".into())
            })?;
        command
            .command
            .validate_preparation(&context.preparation)
            .map_err(JournalError::Corrupt)?;
        if context.configuration_digest != command.command.configuration_digest {
            return Err(JournalError::Corrupt(
                "Memory-use original configuration changed".into(),
            ));
        }
        let receipt = command.receipt.as_ref().expect("checked current receipt");
        receipt
            .validate(&command.command, &context.preparation)
            .map_err(JournalError::Corrupt)?;
        validate_effect_coverage(&connection, receipt)?;
        Ok(command)
    }

    /// Retains the exact request and admits at most one possible POST, including interrupted effects.
    pub fn prepare_memory_use_effect(
        &self,
        command_id: &str,
        provider_id: &str,
        request: &Value,
        now: &ReceiverClockObservation,
    ) -> Result<(bool, JournalMemoryUseEffect), JournalError> {
        let mut connection = self.lock_connection()?;
        let tx = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let control = read_command(&tx, command_id)?.ok_or_else(|| {
            JournalError::InvalidSpec("Memory-use effect lacks durable control intent".into())
        })?;
        if let Some(original) = read_effect(&tx, command_id, provider_id)? {
            if original.request != *request {
                return Err(JournalError::InvalidSpec(
                    "original Memory-use backend request changed".into(),
                ));
            }
            return Ok((false, original));
        }
        if control.budget.remaining_ms(now) == 0 {
            return Err(JournalError::InvalidSpec(
                "original Memory-use effect budget expired".into(),
            ));
        }
        let context = memory_preparation::read_preparation(&tx, &control.command.preparation_id)?
            .ok_or_else(|| {
            JournalError::InvalidSpec("Memory-use effect lost original context".into())
        })?;
        if !context
            .preparation
            .inputs
            .iter()
            .any(|input| input.consumer.provider_id() == provider_id)
        {
            return Err(JournalError::InvalidSpec(
                "Memory-use effect names an unselected provider".into(),
            ));
        }
        if control.command.operation == MemoryUseOperation::Grant {
            let revoked: bool = tx.query_row(
                "SELECT revoked FROM memory_use_heads WHERE lease_id=?1",
                [&control.command.lease_id],
                |row| row.get(0),
            )?;
            if revoked || context.revoked {
                return Err(JournalError::InvalidSpec(
                    "Memory-use effect was revoked before issue".into(),
                ));
            }
        }
        let json = serde_json::to_string(request)?;
        let bytes: u64 = tx.query_row("SELECT COALESCE(SUM(length(request_json)+COALESCE(length(receipt_json),0)),0) FROM memory_use_effects",[],|row|row.get(0))?;
        if json.len() > 600 * 1024 || bytes.saturating_add(json.len() as u64) > 256 * 1024 * 1024 {
            return Err(JournalError::InvalidSpec(
                "Memory-use retained backend metadata is full".into(),
            ));
        }
        tx.execute(
            "INSERT INTO memory_use_effects(command_id,provider_id,request_json) VALUES (?1,?2,?3)",
            params![command_id, provider_id, json],
        )?;
        tx.commit()?;
        Ok((
            true,
            JournalMemoryUseEffect {
                request: request.clone(),
                query_count: 0,
                receipt: None,
            },
        ))
    }

    /// Reserves a bounded readonly attempt before I/O; crash/retry cannot reset its count or budget.
    pub fn begin_memory_use_query(
        &self,
        command_id: &str,
        provider_id: &str,
    ) -> Result<(), JournalError> {
        let now = ReceiverClockObservation::read()?;
        let mut connection = self.lock_connection()?;
        let tx = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let command = read_command(&tx, command_id)?.ok_or_else(|| {
            JournalError::InvalidSpec("Memory-use query has no original command".into())
        })?;
        if command.budget.remaining_ms(&now) == 0 {
            return Err(JournalError::InvalidSpec(
                "original Memory-use query budget expired".into(),
            ));
        }
        let changed = tx.execute("UPDATE memory_use_effects SET query_count=query_count+1 WHERE command_id=?1 AND provider_id=?2 AND query_count<3",params![command_id,provider_id])?;
        if changed != 1 {
            return Err(JournalError::InvalidSpec(
                "original Memory-use query budget is unavailable".into(),
            ));
        }
        tx.commit()?;
        Ok(())
    }

    /// Reserves an automatic revoke query with persistent backoff inside the original ten seconds.
    pub fn begin_memory_use_revoke_query(
        &self,
        command_id: &str,
        provider_id: &str,
    ) -> Result<bool, JournalError> {
        let now = ReceiverClockObservation::read()?;
        let mut connection = self.lock_connection()?;
        let tx = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let command = read_command(&tx, command_id)?.ok_or_else(|| {
            JournalError::InvalidSpec("revoke query has no original command".into())
        })?;
        let effect = read_effect(&tx, command_id, provider_id)?.ok_or_else(|| {
            JournalError::InvalidSpec("revoke query has no original effect".into())
        })?;
        if command.budget.remaining_ms(&now) == 0 || effect.query_count >= 3 {
            return Ok(false);
        }
        let last:Option<u64>=tx.query_row("SELECT last_lower_ms FROM memory_use_auto_query_times WHERE command_id=?1 AND provider_id=?2",params![command_id,provider_id],|row|row.get(0)).optional()?;
        let delay = match effect.query_count {
            0 => 250,
            1 => 1000,
            _ => 2000,
        };
        if now.lower_ms
            < last
                .unwrap_or(command.budget.received_lower_ms)
                .saturating_add(delay)
        {
            return Ok(false);
        }
        tx.execute("UPDATE memory_use_effects SET query_count=query_count+1 WHERE command_id=?1 AND provider_id=?2 AND query_count<3",params![command_id,provider_id])?;
        tx.execute("INSERT INTO memory_use_auto_query_times VALUES (?1,?2,?3) ON CONFLICT(command_id,provider_id) DO UPDATE SET last_lower_ms=excluded.last_lower_ms",params![command_id,provider_id,now.lower_ms])?;
        tx.commit()?;
        Ok(true)
    }

    /// Persists one exact already-validated private reply without changing command admission time.
    pub fn record_memory_use_effect_receipt(
        &self,
        command_id: &str,
        provider_id: &str,
        receipt: &Value,
    ) -> Result<(), JournalError> {
        let json = serde_json::to_string(receipt)?;
        if json.len() > 16 * 1024 || !receipt.is_object() {
            return Err(JournalError::InvalidSpec(
                "Memory-use backend receipt exceeds its bound".into(),
            ));
        }
        let mut connection = self.lock_connection()?;
        let tx = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let original = read_effect(&tx, command_id, provider_id)?.ok_or_else(|| {
            JournalError::InvalidSpec("backend receipt has no original intent".into())
        })?;
        if original.receipt.as_ref().is_some_and(|old| old != receipt) {
            return Err(JournalError::InvalidSpec(
                "original Memory-use backend receipt changed".into(),
            ));
        }
        tx.execute(
            "UPDATE memory_use_effects SET receipt_json=?1 WHERE command_id=?2 AND provider_id=?3",
            params![json, command_id, provider_id],
        )?;
        tx.commit()?;
        Ok(())
    }

    /// Commits a single original aggregate outcome; replay never replaces ambiguity or its clock.
    pub fn record_memory_use_receipt(
        &self,
        receipt: &MemoryUseReceipt,
    ) -> Result<MemoryUseReceipt, JournalError> {
        let mut connection = self.lock_connection()?;
        let tx = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let original = read_command(&tx, &receipt.command_id)?.ok_or_else(|| {
            JournalError::InvalidSpec("Memory-use receipt has no original command".into())
        })?;
        let context = memory_preparation::read_preparation(&tx, &original.command.preparation_id)?
            .ok_or_else(|| {
                JournalError::InvalidSpec("Memory-use receipt lost its original context".into())
            })?;
        receipt
            .validate(&original.command, &context.preparation)
            .map_err(JournalError::InvalidSpec)?;
        if receipt.remaining_budget_ms
            > original
                .budget
                .deadline_lower_ms
                .saturating_sub(original.budget.received_lower_ms)
        {
            return Err(JournalError::InvalidSpec(
                "Memory-use receipt renewed the original received budget".into(),
            ));
        }
        validate_effect_coverage(&tx, receipt)?;
        if let Some(old) = original.receipt {
            if old != *receipt {
                return Err(JournalError::InvalidSpec(
                    "original Memory-use receipt changed".into(),
                ));
            }
            return Ok(old);
        }
        tx.execute(
            "UPDATE memory_use_commands SET receipt_json=?1 WHERE command_id=?2",
            params![serde_json::to_string(receipt)?, receipt.command_id],
        )?;
        tx.commit()?;
        Ok(receipt.clone())
    }
}

/// Requires declared provider proof to be retained under its original once-issued effect.
fn validate_effect_coverage(
    connection: &Connection,
    receipt: &MemoryUseReceipt,
) -> Result<(), JournalError> {
    for provider in &receipt.providers {
        let effect = read_effect(connection, &receipt.command_id, &provider.provider_id)?
            .ok_or_else(|| {
                JournalError::Corrupt(
                    "Memory-use provider receipt has no original effect intent".into(),
                )
            })?;
        if effect.receipt.as_ref() != Some(&provider.original_receipt) {
            return Err(JournalError::Corrupt(
                "Memory-use provider proof changed from its original retained reply".into(),
            ));
        }
    }
    Ok(())
}

/// Reads exact serialized original command/budget and rejects malformed or substituted identities.
pub(super) fn read_command(
    connection: &Connection,
    id: &str,
) -> Result<Option<JournalMemoryUseCommand>, JournalError> {
    let row:Option<(String,String,Option<String>)>=connection.query_row("SELECT command_json,budget_json,receipt_json FROM memory_use_commands WHERE command_id=?1",[id],|row|Ok((row.get(0)?,row.get(1)?,row.get(2)?))).optional()?;
    let Some((command, budget, receipt)) = row else {
        return Ok(None);
    };
    let command: MemoryUseControl = serde_json::from_str(&command)?;
    command.validate().map_err(JournalError::InvalidSpec)?;
    if command.command_id != id {
        return Err(JournalError::Corrupt(
            "Memory-use stored command identity changed".into(),
        ));
    }
    Ok(Some(JournalMemoryUseCommand {
        command,
        budget: serde_json::from_str(&budget)?,
        receipt: receipt
            .map(|value| serde_json::from_str(&value))
            .transpose()?,
    }))
}

/// Reads immutable backend request/evidence and its durable bounded readonly-query count.
pub(super) fn read_effect(
    connection: &Connection,
    command: &str,
    provider: &str,
) -> Result<Option<JournalMemoryUseEffect>, JournalError> {
    let row:Option<(String,u32,Option<String>)>=connection.query_row("SELECT request_json,query_count,receipt_json FROM memory_use_effects WHERE command_id=?1 AND provider_id=?2",params![command,provider],|row|Ok((row.get(0)?,row.get(1)?,row.get(2)?))).optional()?;
    row.map(|(request, query_count, receipt)| {
        Ok(JournalMemoryUseEffect {
            request: serde_json::from_str(&request)?,
            query_count,
            receipt: receipt
                .map(|value| serde_json::from_str(&value))
                .transpose()?,
        })
    })
    .transpose()
}
