//! Original unpublished Archive cleanup: independent immutable intent, actual copier fence and receipt.

use super::archive_operation::{ArchiveIntent, existing, kernel};
use super::*;

/// Explicit cleanup authority generation; old writers cannot ignore original abort receipts.
pub(super) const STORAGE_SCHEMA: &str = "roboguide.archive-operations/v0.2";
/// Maximum one bounded cleanup record, independent of the original retained complete copy intent.
const MAX_RECORD_BYTES: usize = 16 * 1024;

/// Actual authenticated explicit cleanup command; none of its fields grant another copy permit.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ArchiveAbortRequest {
    /// Original idempotent cleanup command, distinct from the frozen copy ID.
    pub command_id: String,
    /// Exact original copy command whose retained source pin is in question.
    pub original_command_id: String,
    /// Complete canonical original copy intent, not merely an event-segment hash.
    pub original_intent_digest: String,
    /// Actual current authenticated administrator, independently checked by the composition writer.
    pub principal: String,
    /// Actual current administrator scope; cleanup cannot invent a replacement Mission tenant.
    pub tenant: String,
    /// Original bounded cleanup duration, separate from the unchanged original copy budget.
    pub budget_ms: u64,
}

/// Cleanup admission retained before attempting the actual original writers or tombstone I/O.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ArchiveAbortIntent {
    /// Closed cleanup semantics, separate from copy, process stop and device safety contracts.
    pub schema: String,
    /// Complete actual first command; another retry cannot change caller, inputs or duration.
    pub request: ArchiveAbortRequest,
    /// Actual original source CAS software owner, never another store with equal content hashes.
    pub source_owner: String,
    /// Exact original Controller SQL authority identity retained by the source CAS inventory.
    pub authority_id: String,
    /// Original declared archive root; relocation requires its own explicit supported recovery contract.
    pub destination: String,
    /// Actual original cleanup kernel boot, independent of the copy's older kernel budget.
    pub boot_id: String,
    /// Actual original cleanup time namespace, never a remote or wall-clock fallback.
    pub time_namespace: String,
    /// First cleanup receive/admission kernel sample.
    pub issued_ms: u64,
    /// Original cleanup deadline; querying or retrying never renews it.
    pub deadline_ms: u64,
}

/// Actual writer descriptor identity supplied by the composition holding that OS lease.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ArchiveLeaseIdentity {
    /// Actual device read from the held original descriptor.
    pub device: u64,
    /// Actual nonzero inode; a newly named lock file does not substitute for the held descriptor.
    pub inode: u64,
}

/// Independent current software-copier fence, never a declaration that a Node or device stopped.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ArchiveCopierFence {
    /// Exact original managed local-copier interpretation.
    pub schema: String,
    /// Actual original source owner checked under its original writer handle.
    pub source_owner: String,
    /// Actual source writer file description, retained through the authority Commit.
    pub source_writer: ArchiveLeaseIdentity,
    /// Actual exclusive Archive root writer, independently held through Commit.
    pub archive_writer: ArchiveLeaseIdentity,
    /// Actual current Controller PID, never a request-body assertion.
    pub process_id: u32,
    /// Actual current process incarnation from the kernel stat record.
    pub process_start_ticks: u64,
    /// Actual cleanup boot identity used to prevent a different kernel from renewing effects.
    pub boot_id: String,
    /// Actual cleanup time namespace.
    pub time_namespace: String,
    /// Actual original effect observation, within the first cleanup budget.
    pub observed_ms: u64,
}

/// Exact original tombstone proof; source-copy history and receiving references remain separate.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ArchiveAbortReceipt {
    /// Explicit receipt meaning, independent of lifecycle terminal strings.
    pub schema: String,
    /// Complete first cleanup intent identity, binding the actual original copy intent.
    pub abort_intent_digest: String,
    /// Complete exact original durable tombstone bytes, verified before source-pin retirement.
    pub tombstone_digest: String,
    /// Actual original software writer fence which preceded tombstone publication.
    pub fence: ArchiveCopierFence,
}

/// Actual storage/composition boundary; a parser or caller-declared success cannot implement it implicitly.
pub trait ArchiveAbortSink {
    /// Fences the managed original copier and publishes its original unpublished-operation tombstone.
    fn abort_unpublished(
        &self,
        original: &ArchiveIntent,
        intent: &ArchiveAbortIntent,
    ) -> Result<ArchiveAbortReceipt, String>;
    /// Reconciles only original tombstone evidence without copying or renewing the cleanup budget.
    fn observe_abort(
        &self,
        original: &ArchiveIntent,
        intent: &ArchiveAbortIntent,
    ) -> Result<ArchiveAbortReceipt, String>;
    /// Rechecks the actual current transport witness while the original application writer is held.
    fn require_commit_permission(&self) -> Result<(), String>;
}

/// Hashes canonical complete original typed evidence without rewriting nested checkpoint strings.
pub(super) fn identity<T: serde::Serialize + ?Sized>(
    value: &T,
) -> Result<String, SqliteEventLogError> {
    let value = serde_json::to_value(value)
        .map_err(|error| SqliteEventLogError::Codec(error.to_string()))?;
    let bytes = serde_json::to_vec(&value)
        .map_err(|error| SqliteEventLogError::Codec(error.to_string()))?;
    Ok(format!("sha256:{:x}", Sha256::digest(bytes)))
}

/// Checks exact canonical software proof identity without accepting a filename alias.
fn digest(value: &str) -> bool {
    value.strip_prefix("sha256:").is_some_and(|hex| {
        hex.len() == 64
            && hex
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    })
}

impl ArchiveAbortIntent {
    /// Validates complete first attribution and the same nonrenewable cleanup budget.
    pub fn validate(&self, original: &ArchiveIntent) -> Result<(), String> {
        let text = |value: &str, max: usize| {
            !value.trim().is_empty() && value.len() <= max && !value.chars().any(char::is_control)
        };
        if self.schema != "roboguide.archive-abort-intent/v0.1"
            || !text(&self.request.command_id, 128)
            || !text(&self.request.original_command_id, 128)
            || !text(&self.request.principal, 256)
            || !text(&self.request.tenant, 128)
            || self.request.command_id == self.request.original_command_id
            || !digest(&self.request.original_intent_digest)
            || self.request.original_command_id != original.request.command_id
            || identity(original).map_err(|error| error.to_string())?
                != self.request.original_intent_digest
            || self.source_owner != original.request.source_owner
            || self.authority_id != original.request.authority_id
            || self.destination != original.request.destination
            || self.request.tenant != original.request.tenant
            || self.request.budget_ms == 0
            || self.request.budget_ms > 30_000
            || self.deadline_ms.checked_sub(self.issued_ms) != Some(self.request.budget_ms)
            || self.boot_id.len() != 36
            || !text(&self.time_namespace, 128)
        {
            return Err("original Archive cleanup attribution, input or budget is invalid".into());
        }
        Ok(())
    }

    /// Constructs the first actual cleanup budget while preserving every original copy field.
    fn new(
        request: ArchiveAbortRequest,
        original: &ArchiveIntent,
    ) -> Result<Self, SqliteEventLogError> {
        let (boot_id, time_namespace, issued_ms) = kernel()?;
        let deadline_ms = issued_ms.checked_add(request.budget_ms).ok_or_else(|| {
            SqliteEventLogError::ArchivePolicy("original Archive cleanup budget overflow".into())
        })?;
        let intent = Self {
            schema: "roboguide.archive-abort-intent/v0.1".into(),
            request,
            source_owner: original.request.source_owner.clone(),
            authority_id: original.request.authority_id.clone(),
            destination: original.request.destination.clone(),
            boot_id,
            time_namespace,
            issued_ms,
            deadline_ms,
        };
        intent
            .validate(original)
            .map_err(SqliteEventLogError::ArchivePolicy)?;
        Ok(intent)
    }

    /// Supplies exact original identity for an actual receiving tombstone and read-only observation.
    pub fn digest(&self) -> Result<String, String> {
        identity(self).map_err(|error| error.to_string())
    }

    /// Refuses new cleanup effects after first expiry, regression, namespace change or kernel replacement.
    pub fn require_budget(&self) -> Result<(), String> {
        let (boot, namespace, now) = kernel().map_err(|error| error.to_string())?;
        if boot != self.boot_id
            || namespace != self.time_namespace
            || now < self.issued_ms
            || now.saturating_add(10) >= self.deadline_ms
        {
            return Err("original Archive cleanup budget is expired or unavailable".into());
        }
        Ok(())
    }
}

impl ArchiveAbortReceipt {
    /// Rejects a declared stop flag, changed original input or evidence outside its first cleanup budget.
    pub fn validate(&self, intent: &ArchiveAbortIntent) -> Result<(), String> {
        if self.schema != "roboguide.archive-abort-receipt/v0.1"
            || self.abort_intent_digest != intent.digest()?
            || !digest(&self.tombstone_digest)
            || self.fence.schema != "roboguide.original-local-archive-copier-fence/v0.1"
            || self.fence.source_owner != intent.source_owner
            || self.fence.source_writer.inode == 0
            || self.fence.archive_writer.inode == 0
            || self.fence.process_id == 0
            || self.fence.process_start_ticks == 0
            || self.fence.boot_id != intent.boot_id
            || self.fence.time_namespace != intent.time_namespace
            || self.fence.observed_ms < intent.issued_ms
            || self.fence.observed_ms.saturating_add(10) >= intent.deadline_ms
        {
            return Err(
                "original Archive abort lacks exact actual copier-fence/tombstone evidence".into(),
            );
        }
        Ok(())
    }
}

/// Requires current explicit storage before cleanup can acquire, observe or retire any original pin.
pub(super) fn require_storage(connection: &Connection) -> Result<(), SqliteEventLogError> {
    let marker: Option<String> = connection
        .query_row(
            "SELECT value FROM event_log_metadata WHERE key='archive_operations_schema'",
            [],
            |row| row.get(0),
        )
        .optional()?;
    if !matches!(
        marker.as_deref(),
        Some(STORAGE_SCHEMA) | Some(COORDINATED_ARCHIVE_STORAGE_SCHEMA)
    ) {
        return Err(SqliteEventLogError::ArchivePolicy(
            "original Archive cleanup requires explicit operations v0.2".into(),
        ));
    }
    let (count,bytes):(u64,u64)=connection.query_row("SELECT COUNT(*),COALESCE(SUM(length(CAST(intent_json AS BLOB))+COALESCE(length(CAST(receipt_json AS BLOB)),0)),0) FROM event_archive_aborts",[],|row|Ok((row.get(0)?,row.get(1)?)))?;
    if count > 4096 || bytes > 64 * 1024 * 1024 {
        return Err(SqliteEventLogError::Codec(
            "original Archive cleanup history exceeds bounds".into(),
        ));
    }
    Ok(())
}

/// Observes one exact immutable cleanup admission, including its separate unknown receipt.
fn read(
    connection: &Connection,
    id: &str,
) -> Result<Option<(ArchiveAbortIntent, Option<ArchiveAbortReceipt>)>, SqliteEventLogError> {
    require_storage(connection)?;
    let row: Option<(String, Option<String>)> = connection
        .query_row(
            "SELECT intent_json,receipt_json FROM event_archive_aborts WHERE command_id=?1",
            [id],
            |row| Ok((row.get(0)?, row.get(1)?)),
        )
        .optional()?;
    row.map(|(raw, receipt)| {
        if raw.len() > MAX_RECORD_BYTES
            || receipt
                .as_ref()
                .is_some_and(|value| value.len() > MAX_RECORD_BYTES)
        {
            return Err(SqliteEventLogError::Codec(
                "original Archive cleanup record exceeds bounds".into(),
            ));
        }
        let intent: ArchiveAbortIntent = serde_json::from_str(&raw)
            .map_err(|error| SqliteEventLogError::Codec(error.to_string()))?;
        if intent.request.command_id != id {
            return Err(SqliteEventLogError::Codec(
                "original Archive cleanup key changed".into(),
            ));
        }
        let (original, publication) = existing(connection, &intent.request.original_command_id)?
            .ok_or_else(|| {
                SqliteEventLogError::Codec("original Archive cleanup lost its copy intent".into())
            })?;
        if publication.is_some() {
            return Err(SqliteEventLogError::Codec(
                "original Archive cleanup conflicts with a committed publication".into(),
            ));
        }
        intent
            .validate(&original)
            .map_err(SqliteEventLogError::Codec)?;
        let receipt: Option<ArchiveAbortReceipt> = receipt
            .map(|raw| {
                serde_json::from_str(&raw)
                    .map_err(|error| SqliteEventLogError::Codec(error.to_string()))
            })
            .transpose()?;
        if let Some(receipt) = &receipt {
            receipt
                .validate(&intent)
                .map_err(SqliteEventLogError::Codec)?;
        }
        Ok((intent, receipt))
    })
    .transpose()
}

/// Validates all bounded original cleanup records before startup or another copy can consume retirement.
pub(super) fn validate_storage(connection: &Connection) -> Result<(), SqliteEventLogError> {
    require_storage(connection)?;
    let mut statement = connection.prepare(
        "SELECT command_id,original_command_id FROM event_archive_aborts ORDER BY command_id",
    )?;
    let rows = statement
        .query_map([], |row| {
            Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
        })?
        .collect::<Result<Vec<_>, _>>()?;
    drop(statement);
    let mut completed = std::collections::BTreeSet::new();
    for (id, original_id) in rows {
        let (intent, receipt) = read(connection, &id)?.ok_or_else(|| {
            SqliteEventLogError::Codec("original Archive cleanup row disappeared".into())
        })?;
        if intent.request.original_command_id != original_id {
            return Err(SqliteEventLogError::Codec(
                "original Archive cleanup source key changed".into(),
            ));
        }
        if receipt.is_some() && !completed.insert(original_id) {
            return Err(SqliteEventLogError::Codec(
                "original Archive pin has conflicting complete cleanup commands".into(),
            ));
        }
    }
    Ok(())
}

/// Consumes only a complete original cleanup receipt, retaining every uncertain source pin.
pub(super) fn completed_for(
    connection: &Connection,
    original_id: &str,
) -> Result<bool, SqliteEventLogError> {
    require_storage(connection)?;
    let mut statement=connection.prepare("SELECT command_id FROM event_archive_aborts WHERE original_command_id=?1 ORDER BY command_id")?;
    let ids = statement
        .query_map([original_id], |row| row.get::<_, String>(0))?
        .collect::<Result<Vec<_>, _>>()?;
    drop(statement);
    let mut completed = false;
    for id in ids {
        completed |= read(connection, &id)?.is_some_and(|(_, receipt)| receipt.is_some());
    }
    Ok(completed)
}

impl SqliteEventLog {
    /// Reports only exact validated original cleanup evidence, never a clock-derived stop or release.
    pub fn archive_is_aborted(&self, original_id: &str) -> Result<bool, SqliteEventLogError> {
        self.ensure_no_open_batch()?;
        let connection = self
            .connection
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        completed_for(&connection, original_id)
    }
    /// Reads original cleanup evidence without executing a copy, a cleanup or any device command.
    pub fn archive_abort_operation(
        &self,
        id: &str,
    ) -> Result<Option<(ArchiveAbortIntent, Option<ArchiveAbortReceipt>)>, SqliteEventLogError>
    {
        self.ensure_no_open_batch()?;
        let connection = self
            .connection
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        read(&connection, id)
    }

    /// Holds no SQL mutex during actual writer fencing/tombstone I/O; the caller retains application writer.
    pub fn abort_archive<S: ArchiveAbortSink>(
        &self,
        request: ArchiveAbortRequest,
        sink: &S,
    ) -> Result<ArchiveAbortReceipt, SqliteEventLogError> {
        self.require_writes_enabled()?;
        let result = self.abort_archive_inner(request, sink);
        self.observe_write_result(result)
    }

    /// Admits first cleanup intent, observes uncertain original effects and atomically retains exact receipt.
    fn abort_archive_inner<S: ArchiveAbortSink>(
        &self,
        request: ArchiveAbortRequest,
        sink: &S,
    ) -> Result<ArchiveAbortReceipt, SqliteEventLogError> {
        self.ensure_no_open_batch()?;
        if self.replicator.is_some() {
            return Err(SqliteEventLogError::ArchivePolicy(
                "Archive cleanup requires its coordinated replica maintenance protocol".into(),
            ));
        }
        let connection = self
            .connection
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        require_storage(&connection)?;
        let (original, publication) = existing(&connection, &request.original_command_id)?
            .ok_or_else(|| {
                SqliteEventLogError::ArchivePolicy("original Archive copy command is absent".into())
            })?;
        if publication.is_some() {
            return Err(SqliteEventLogError::ArchivePolicy(
                "published Archive requires original publication reconciliation, never abort"
                    .into(),
            ));
        }
        let previous = read(&connection, &request.command_id)?;
        if !previous
            .as_ref()
            .is_some_and(|(_, receipt)| receipt.is_some())
            && completed_for(&connection, &request.original_command_id)?
        {
            return Err(SqliteEventLogError::ArchivePolicy(
                "another original cleanup already confirmed source-pin retirement".into(),
            ));
        }
        let intent = if let Some((intent, _)) = &previous {
            if intent.request != request {
                return Err(SqliteEventLogError::ArchivePolicy(
                    "original Archive cleanup command input changed".into(),
                ));
            }
            intent.clone()
        } else {
            let count: u64 =
                connection.query_row("SELECT COUNT(*) FROM event_archive_aborts", [], |row| {
                    row.get(0)
                })?;
            if count >= 4096 || completed_for(&connection, &request.original_command_id)? {
                return Err(SqliteEventLogError::ArchivePolicy(
                    "original Archive is already aborted or cleanup history is full".into(),
                ));
            }
            let intent = ArchiveAbortIntent::new(request, &original)?;
            let raw = serde_json::to_string(&intent)
                .map_err(|error| SqliteEventLogError::Codec(error.to_string()))?;
            if raw.len() > MAX_RECORD_BYTES {
                return Err(SqliteEventLogError::ArchivePolicy(
                    "original Archive cleanup intent exceeds bound".into(),
                ));
            }
            connection.execute("INSERT INTO event_archive_aborts(command_id,original_command_id,intent_json,receipt_json) VALUES (?1,?2,?3,NULL)",params![intent.request.command_id,intent.request.original_command_id,raw])?;
            intent
        };
        drop(connection);
        let receipt = if previous.is_some() {
            sink.observe_abort(&original, &intent)
        } else {
            sink.abort_unpublished(&original, &intent)
        }
        .map_err(SqliteEventLogError::ArchiveSink)?;
        receipt
            .validate(&intent)
            .map_err(SqliteEventLogError::Codec)?;
        if let Some((_, Some(original))) = &previous {
            if original != &receipt {
                return Err(SqliteEventLogError::Codec(
                    "original Archive cleanup receipt changed".into(),
                ));
            }
            return Ok(receipt);
        }
        sink.require_commit_permission()
            .map_err(SqliteEventLogError::ArchiveSink)?;
        self.require_writes_enabled()?;
        let mut connection = self
            .connection
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        if read(&connection, &intent.request.command_id)?.as_ref() != Some(&(intent.clone(), None))
            || existing(&connection, &intent.request.original_command_id)?.as_ref()
                != Some(&(original, None))
            || completed_for(&connection, &intent.request.original_command_id)?
        {
            return Err(SqliteEventLogError::ArchivePolicy(
                "original Archive cleanup ownership changed before Commit".into(),
            ));
        }
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let raw = serde_json::to_string(&receipt)
            .map_err(|error| SqliteEventLogError::Codec(error.to_string()))?;
        let changed=transaction.execute("UPDATE event_archive_aborts SET receipt_json=?1 WHERE command_id=?2 AND receipt_json IS NULL",params![raw,intent.request.command_id])?;
        if changed != 1 {
            return Err(SqliteEventLogError::Codec(
                "original Archive cleanup receipt lost ownership".into(),
            ));
        }
        transaction.commit()?;
        Ok(receipt)
    }
}

#[cfg(test)]
#[path = "archive_abort_tests.rs"]
mod tests;
