//! Durable pre-execution contexts, once-only observations and irreversible revocation fences.

use super::*;
use domain::{MemoryControlCommand, MemoryControlKind, MemoryControlReceipt, MemoryPreparation};
use serde::{Deserialize, Serialize};

/// Bounded current-host observation; no source clock or wall-clock comparison is involved.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReceiverClockObservation {
    /// Actual Linux host boot identity, independently of the Node process lifetime.
    pub boot_id: String,
    /// Actual Linux time namespace; one boot may contain several independent uptime offsets.
    pub time_namespace: String,
    /// Conservative lower bound of the local boot-relative receive time.
    pub lower_ms: u64,
    /// Quantization uncertainty in the actual kernel observation.
    pub uncertainty_ms: u64,
}

impl ReceiverClockObservation {
    /// Reads the actual Linux boot clock; unavailable platforms fail closed.
    pub fn read() -> Result<Self, JournalError> {
        let boot = std::fs::read_to_string("/proc/sys/kernel/random/boot_id")?;
        let uptime = std::fs::read_to_string("/proc/uptime")?;
        let boot_id = boot.trim().to_string();
        let time_namespace = std::fs::read_link("/proc/self/ns/time")?
            .to_str()
            .filter(|value| value.starts_with("time:[") && value.ends_with(']'))
            .ok_or_else(|| JournalError::Corrupt("host time namespace is unavailable".into()))?
            .to_string();
        if boot_id.len() != 36 || !boot_id.bytes().all(|b| b.is_ascii_hexdigit() || b == b'-') {
            return Err(JournalError::Corrupt(
                "host boot identity is unavailable".into(),
            ));
        }
        let value = uptime
            .split_whitespace()
            .next()
            .ok_or_else(|| JournalError::Corrupt("host boot clock is unavailable".into()))?;
        let (whole, fraction) = value
            .split_once('.')
            .ok_or_else(|| JournalError::Corrupt("host boot clock has no precision".into()))?;
        if !(1..=3).contains(&fraction.len()) || !fraction.bytes().all(|b| b.is_ascii_digit()) {
            return Err(JournalError::Corrupt(
                "unsupported host boot clock precision".into(),
            ));
        }
        let seconds = whole
            .parse::<u64>()
            .map_err(|_| JournalError::Corrupt("invalid host boot clock".into()))?;
        let fractional = fraction
            .parse::<u64>()
            .map_err(|_| JournalError::Corrupt("invalid host boot clock fraction".into()))?;
        let uncertainty_ms = 10_u64.pow(3 - fraction.len() as u32);
        let lower_ms = seconds
            .checked_mul(1000)
            .and_then(|value| value.checked_add(fractional * uncertainty_ms))
            .ok_or_else(|| JournalError::Corrupt("host boot clock overflow".into()))?;
        Ok(Self {
            boot_id,
            time_namespace,
            lower_ms,
            uncertainty_ms,
        })
    }
}

/// Original receiver-relative deadline; restart/reconnect never changes this record.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MemoryReceiverBudget {
    /// Original actual host boot, never inferred from Node identity or a storage path.
    pub boot_id: String,
    /// Missing historical namespace is unavailable; it is never inferred from the current process.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub time_namespace: Option<String>,
    /// Original lower receive time preserves quantization conservatively.
    pub received_lower_ms: u64,
    /// Exclusive original boot-relative deadline.
    pub deadline_lower_ms: u64,
}

impl MemoryReceiverBudget {
    /// Freezes a bounded original budget; current uncertainty cannot extend the permitted interval.
    pub fn admit(now: &ReceiverClockObservation, valid_for_ms: u64) -> Result<Self, JournalError> {
        if now.boot_id.is_empty()
            || now.time_namespace.is_empty()
            || now.time_namespace.len() > 128
            || !(1..=1000).contains(&now.uncertainty_ms)
            || !(1..=120_000).contains(&valid_for_ms)
            || valid_for_ms <= now.uncertainty_ms
        {
            return Err(JournalError::InvalidSpec(
                "original Memory receive budget is unavailable".into(),
            ));
        }
        let deadline_lower_ms = now
            .lower_ms
            .checked_add(valid_for_ms)
            .ok_or_else(|| JournalError::InvalidSpec("Memory receive deadline overflow".into()))?;
        Ok(Self {
            boot_id: now.boot_id.clone(),
            time_namespace: Some(now.time_namespace.clone()),
            received_lower_ms: now.lower_ms,
            deadline_lower_ms,
        })
    }

    /// Returns a conservative remainder only on the original host boot and nondecreasing clock.
    pub fn remaining_ms(&self, now: &ReceiverClockObservation) -> u64 {
        if now.boot_id != self.boot_id
            || self.time_namespace.as_deref() != Some(now.time_namespace.as_str())
            || now.time_namespace.is_empty()
            || now.lower_ms < self.received_lower_ms
            || !(1..=1000).contains(&now.uncertainty_ms)
            || self
                .deadline_lower_ms
                .saturating_sub(self.received_lower_ms)
                > 120_000
        {
            return 0;
        }
        self.deadline_lower_ms
            .saturating_sub(now.lower_ms.saturating_add(now.uncertainty_ms))
    }
}

/// Original durable context and its independent current revocation fence.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct JournalMemoryPreparation {
    /// Original actual local operation/provider configuration, frozen before any import effect.
    pub configuration_digest: String,
    /// Original complete immutable metadata and source authority.
    pub preparation: MemoryPreparation,
    /// Original boot-relative budget, retained through process restart.
    pub budget: MemoryReceiverBudget,
    /// Irreversible revocation; imported bytes and historical receipts are retained.
    pub revoked: bool,
}

/// Outcome of reserving one exact observation identity before its actual backend reads.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum BeginMemoryObservation {
    /// The exact once-only query is newly durable and may read its original backend.
    Start,
    /// The original query already completed; the receipt is historical and must not renew its TTL.
    Existing(MemoryControlReceipt),
    /// The original query was admitted but interrupted; retry cannot issue a new read for this ID.
    Interrupted,
}

impl ExecutionJournal {
    /// Freezes validated immutable metadata once, but rereads cancellation/revocation for each chunk.
    /// The returned guard neither caches current permission nor renews the original receive budget.
    pub(crate) fn memory_acquisition_guard(
        self: &std::sync::Arc<Self>,
        original: &JournalMemoryPreparation,
    ) -> Result<impl Fn() -> Result<(), JournalError> + Send + Sync + 'static, JournalError> {
        let journal = std::sync::Arc::clone(self);
        let id = original.preparation.preparation_id.clone();
        let execution = original.preparation.execution_id.clone();
        let digest = original
            .preparation
            .digest()
            .map_err(JournalError::InvalidSpec)?;
        let encoded = serde_json::to_string(&original.preparation)?;
        let configuration = original.configuration_digest.clone();
        let budget = original.budget.clone();
        let encoded_budget = serde_json::to_string(&budget)?;
        let guard = move || {
            let connection = journal.lock_connection()?;
            let current: bool = connection
                .prepare_cached(
                    "SELECT EXISTS(SELECT 1 FROM memory_preparations p WHERE preparation_id=?1
                 AND execution_id=?2 AND preparation_digest=?3 AND preparation_json=?4
                 AND configuration_digest=?5 AND budget_json=?6
                 AND NOT EXISTS(SELECT 1 FROM memory_context_revocations r
                                WHERE r.preparation_id=p.preparation_id)
                 AND NOT EXISTS(SELECT 1 FROM cancellation_intents c
                                WHERE c.execution_id=p.execution_id))",
                )?
                .query_row(
                    params![
                        id,
                        execution,
                        digest,
                        encoded,
                        configuration,
                        encoded_budget
                    ],
                    |row| row.get(0),
                )?;
            if !current || budget.remaining_ms(&ReceiverClockObservation::read()?) == 0 {
                return Err(JournalError::InvalidSpec(
                    "original Memory acquisition is expired, revoked, cancelled or changed".into(),
                ));
            }
            Ok(())
        };
        guard()?;
        Ok(guard)
    }

    /// Reads one immutable context without granting execution, import or current content availability.
    pub fn memory_preparation(
        &self,
        id: &str,
    ) -> Result<Option<JournalMemoryPreparation>, JournalError> {
        let connection = self.lock_connection()?;
        read_preparation(&connection, id)
    }

    /// Persists original context before any import; duplicate admission preserves the original budget.
    pub fn prepare_memory_context(
        &self,
        preparation: &MemoryPreparation,
        now: &ReceiverClockObservation,
        valid_for_ms: u64,
        configuration_digest: &str,
    ) -> Result<JournalMemoryPreparation, JournalError> {
        preparation.validate().map_err(JournalError::InvalidSpec)?;
        if !configuration_digest
            .strip_prefix("sha256:")
            .is_some_and(|value| {
                value.len() == 64
                    && value
                        .bytes()
                        .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
            })
        {
            return Err(JournalError::InvalidSpec(
                "original Node configuration digest is invalid".into(),
            ));
        }
        let digest = preparation.digest().map_err(JournalError::InvalidSpec)?;
        let json = serde_json::to_string(preparation)?;
        if json.len() > 256 * 1024 {
            return Err(JournalError::InvalidSpec(
                "Memory context exceeds its bound".into(),
            ));
        }
        let mut connection = self.lock_connection()?;
        let tx = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let revoked: bool = tx.query_row(
            "SELECT EXISTS(SELECT 1 FROM memory_context_revocations WHERE preparation_id=?1)",
            [&preparation.preparation_id],
            |row| row.get(0),
        )?;
        if revoked {
            return Err(JournalError::InvalidSpec(
                "Memory context was irreversibly revoked".into(),
            ));
        }
        if let Some(original) = read_preparation(&tx, &preparation.preparation_id)? {
            if original.preparation != *preparation
                || original.configuration_digest != configuration_digest
            {
                return Err(JournalError::InvalidSpec(
                    "Memory context identity conflicts with its original admission".into(),
                ));
            }
            return Ok(original);
        }
        let (count, bytes): (u64, u64) = tx.query_row(
            "SELECT COUNT(*),COALESCE(SUM(length(preparation_json)),0) FROM memory_preparations",
            [],
            |row| Ok((row.get(0)?, row.get(1)?)),
        )?;
        if count >= 4096 || bytes.saturating_add(json.len() as u64) > 64 * 1024 * 1024 {
            return Err(JournalError::InvalidSpec(
                "Memory context journal is full".into(),
            ));
        }
        let budget = MemoryReceiverBudget::admit(now, valid_for_ms.min(preparation.valid_for_ms))?;
        tx.execute("INSERT INTO memory_preparations(preparation_id,execution_id,preparation_digest,preparation_json,budget_json,configuration_digest) VALUES (?1,?2,?3,?4,?5,?6)",
            params![preparation.preparation_id,preparation.execution_id,digest,json,serde_json::to_string(&budget)?,configuration_digest])?;
        tx.commit()?;
        Ok(JournalMemoryPreparation {
            configuration_digest: configuration_digest.into(),
            preparation: preparation.clone(),
            budget,
            revoked: false,
        })
    }

    /// Retains a tombstone even when revoke beats prepare, preventing delayed-message resurrection.
    pub fn revoke_memory_context(&self, id: &str, digest: &str) -> Result<(), JournalError> {
        let mut connection = self.lock_connection()?;
        let tx = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let original: Option<String> = tx
            .query_row(
                "SELECT preparation_digest FROM memory_context_revocations WHERE preparation_id=?1",
                [id],
                |row| row.get(0),
            )
            .optional()?;
        if original.as_deref().is_some_and(|old| old != digest) {
            return Err(JournalError::InvalidSpec(
                "Memory revoke identity conflicts".into(),
            ));
        }
        if let Some(context) = read_preparation(&tx, id)?
            && context
                .preparation
                .digest()
                .map_err(JournalError::InvalidSpec)?
                != digest
        {
            return Err(JournalError::InvalidSpec(
                "Memory revoke names another original context".into(),
            ));
        }
        let count: u64 = tx.query_row(
            "SELECT COUNT(*) FROM memory_context_revocations",
            [],
            |row| row.get(0),
        )?;
        if original.is_none() && count >= 4096 {
            return Err(JournalError::InvalidSpec(
                "Memory revocation journal is full".into(),
            ));
        }
        tx.execute("INSERT OR IGNORE INTO memory_context_revocations(preparation_id,preparation_digest) VALUES (?1,?2)",params![id,digest])?;
        tx.commit()?;
        Ok(())
    }

    /// Commits once-only observation admission before reads; interrupted IDs can never re-read.
    pub fn begin_memory_observation(
        &self,
        command: &MemoryControlCommand,
    ) -> Result<BeginMemoryObservation, JournalError> {
        command.validate().map_err(JournalError::InvalidSpec)?;
        let mut connection = self.lock_connection()?;
        let tx = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let original: Option<(String, Option<String>)> = tx
            .query_row(
                "SELECT command_json,receipt_json FROM memory_control_receipts WHERE command_id=?1",
                [command.command_id()],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .optional()?;
        let json = serde_json::to_string(command)?;
        if let Some((content, receipt)) = original {
            if content != json {
                return Err(JournalError::InvalidSpec(
                    "Memory control command identity conflicts".into(),
                ));
            }
            return receipt
                .map(|value| {
                    serde_json::from_str(&value)
                        .map(BeginMemoryObservation::Existing)
                        .map_err(JournalError::from)
                })
                .unwrap_or(Ok(BeginMemoryObservation::Interrupted));
        }
        if command.operation == MemoryControlKind::Probe {
            let context = read_preparation(&tx, &command.preparation_id)?.ok_or_else(|| {
                JournalError::InvalidSpec("probe lacks original durable context".into())
            })?;
            if context
                .preparation
                .digest()
                .map_err(JournalError::InvalidSpec)?
                != command.preparation_digest
            {
                return Err(JournalError::InvalidSpec(
                    "probe replaced original context metadata".into(),
                ));
            }
            let highest: u64 = tx.query_row("SELECT COALESCE(MAX(generation),0) FROM memory_control_receipts WHERE preparation_id=?1 AND operation='probe'", [&command.preparation_id], |row|row.get(0))?;
            if command.generation <= highest {
                return Err(JournalError::InvalidSpec(
                    "an older never-admitted Memory probe cannot acquire new observations".into(),
                ));
            }
        }
        let (count, bytes): (u64, u64) = tx.query_row(
            "SELECT COUNT(*),COALESCE(SUM(length(command_json)+COALESCE(length(receipt_json),0)),0) FROM memory_control_receipts",
            [], |row| Ok((row.get(0)?,row.get(1)?)),
        )?;
        if count >= 4096 * 18 || bytes.saturating_add(json.len() as u64) > 128 * 1024 * 1024 {
            return Err(JournalError::InvalidSpec(
                "Memory control journal is full".into(),
            ));
        }
        let operation = match command.operation {
            MemoryControlKind::Prepare => "prepare",
            MemoryControlKind::Probe => "probe",
            MemoryControlKind::Revoke => "revoke",
        };
        tx.execute("INSERT INTO memory_control_receipts(command_id,preparation_id,operation,generation,command_json) VALUES (?1,?2,?3,?4,?5)",params![command.command_id(),command.preparation_id,operation,command.generation,json])?;
        tx.commit()?;
        Ok(BeginMemoryObservation::Start)
    }

    /// Atomically retains one original receipt; replay cannot change its original outcome or timing.
    pub fn record_memory_control_receipt(
        &self,
        command: &MemoryControlCommand,
        receipt: &MemoryControlReceipt,
    ) -> Result<MemoryControlReceipt, JournalError> {
        if receipt.command_id != command.command_id()
            || receipt.preparation_id != command.preparation_id
            || receipt.preparation_digest != command.preparation_digest
            || receipt.observations.len() > 8
            || receipt.reason.len() > 1024
        {
            return Err(JournalError::InvalidSpec(
                "Memory control receipt differs from its original command".into(),
            ));
        }
        let mut connection = self.lock_connection()?;
        let tx = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let (content, existing): (String, Option<String>) = tx.query_row(
            "SELECT command_json,receipt_json FROM memory_control_receipts WHERE command_id=?1",
            [command.command_id()],
            |row| Ok((row.get(0)?, row.get(1)?)),
        )?;
        if content != serde_json::to_string(command)? {
            return Err(JournalError::InvalidSpec(
                "Memory receipt command identity conflicts".into(),
            ));
        }
        if let Some(existing) = existing {
            let existing: MemoryControlReceipt = serde_json::from_str(&existing)?;
            if existing != *receipt {
                return Err(JournalError::InvalidSpec(
                    "original Memory control receipt is immutable".into(),
                ));
            }
            return Ok(existing);
        }
        let encoded = serde_json::to_string(receipt)?;
        if encoded.len() > 128 * 1024 {
            return Err(JournalError::InvalidSpec(
                "Memory control receipt exceeds its bound".into(),
            ));
        }
        tx.execute(
            "UPDATE memory_control_receipts SET receipt_json=?1 WHERE command_id=?2",
            params![encoded, command.command_id()],
        )?;
        tx.commit()?;
        Ok(receipt.clone())
    }
}

/// Reads retained original authority and its independent revoke-before-prepare fence.
pub(super) fn read_preparation(
    connection: &Connection,
    id: &str,
) -> Result<Option<JournalMemoryPreparation>, JournalError> {
    let row: Option<(String,String,String,bool,String)> = connection.query_row(
        "SELECT preparation_json,budget_json,preparation_digest,EXISTS(SELECT 1 FROM memory_context_revocations r WHERE r.preparation_id=p.preparation_id),configuration_digest FROM memory_preparations p WHERE preparation_id=?1",[id],
        |row|Ok((row.get(0)?,row.get(1)?,row.get(2)?,row.get(3)?,row.get(4)?))).optional()?;
    row.map(|(json, budget, digest, revoked, configuration_digest)| {
        let preparation: MemoryPreparation = serde_json::from_str(&json)?;
        preparation.validate().map_err(JournalError::Corrupt)?;
        if preparation.preparation_id != id
            || preparation.digest().map_err(JournalError::InvalidSpec)? != digest
        {
            return Err(JournalError::Corrupt(
                "stored Memory preparation identity differs".into(),
            ));
        }
        Ok(JournalMemoryPreparation {
            configuration_digest,
            preparation,
            budget: serde_json::from_str(&budget)?,
            revoked,
        })
    })
    .transpose()
}
