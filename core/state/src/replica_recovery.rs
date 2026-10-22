//! Exact original auxiliary State recovery; no new Archive identity or lost history is synthesized.

use super::*;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

#[cfg(test)]
#[path = "replica_recovery_tests.rs"]
mod tests;

/// Private application v3 projection codec; ordinary application v2 never implicitly includes it.
pub const REPLICA_RECOVERY_PROJECTION_SCHEMA: &str = "roboguide.application-recovery/v0.1";
/// Bound before loading complete original recovery rows; exhaustion refuses admission.
const MAX_BYTES: usize = 128 * 1024 * 1024;
/// Bound on the complete original unarchived tail and independent recovery rows.
const MAX_ROWS: usize = 16_384;
/// Complete allowed source metadata, excluding member-local leases and active process authority.
const METADATA: [&str; 6] = [
    "archive_authority_id",
    "archive_operations_schema",
    "archive_manifest",
    "archive_timestamp_floor",
    "history_floor",
    "catalog_archive_checkpoint",
];

/// Complete original copy record, preserving raw nested JSON and its immutable receipt/disposition.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReplicaArchiveOperation {
    /// Exact original row key.
    pub command_id: String,
    /// Original raw intent JSON; nested checkpoint/float bytes are never reserialized.
    pub intent_json: String,
    /// Original pending/compacted disposition, not inferred from time or process exit.
    pub state: String,
    /// Exact original publication proof, absent while uncertain.
    pub receipt_json: Option<String>,
}

/// Complete original separate unpublished-copy cleanup, without granting a new cleanup effect.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReplicaArchiveAbort {
    /// Exact original cleanup key.
    pub command_id: String,
    /// Original copy identity whose pin may be retired by its existing proof only.
    pub original_command_id: String,
    /// Original raw cleanup command/budget/kernel evidence.
    pub intent_json: String,
    /// Exact original actual tombstone/writer receipt, never filled after restore.
    pub receipt_json: Option<String>,
}

/// Original manifest row retains its actual SQL keys as well as its full raw bytes.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReplicaArchiveManifest {
    /// Original complete segment start.
    pub start_sequence: u64,
    /// Original complete segment end.
    pub end_sequence: u64,
    /// Raw original published manifest; this is not a new publication receipt.
    pub manifest_json: String,
}

/// Original State auxiliary authority required beside complete events/checkpoint/commands.
/// It contains no actual member-local supervisor, child-exit or content-reader authority.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReplicaRecoveryProjection {
    /// Exact complete projection meaning.
    pub schema: String,
    /// Exact original closed metadata, including mandatory zero-operation authority.
    pub metadata: BTreeMap<String, String>,
    /// Complete ordered original copy journal, including pending/Unknown rows.
    pub archive_operations: Vec<ReplicaArchiveOperation>,
    /// Complete original v0.2 cleanup journal, including unresolved commands.
    pub archive_aborts: Vec<ReplicaArchiveAbort>,
    /// Original protected prefix table; absence and an accepted empty table remain distinct.
    pub catalog_archive_events: Option<Vec<PersistedEvent>>,
    /// Original publication history table; absence never becomes invented coverage.
    pub archive_manifests: Option<Vec<ReplicaArchiveManifest>>,
}

/// Complete original source state for quiescent/transaction-bound capture and inactive materialization.
/// This contains original authority/evidence only; it does not claim a quorum or current byte placement.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CompleteReplicaState {
    /// Closed complete-source interpretation, never a live Controller or recovery permission.
    pub schema: String,
    /// Exact original current domain checkpoint, without changing its wrapper or JSON bytes.
    pub checkpoint: PersistedCheckpoint,
    /// Complete original unarchived event tail, without independent metadata compaction.
    pub events: Vec<PersistedEvent>,
    /// Complete original retained authority journal, including zero-event revisions.
    pub commands: Vec<ControlCommandJournalEntry>,
    /// Complete original auxiliary Catalog/Archive authority and uncertain operations.
    pub recovery: ReplicaRecoveryProjection,
}

impl CompleteReplicaState {
    /// Validates original complete rows and bounded command/checkpoint coverage before import or I/O.
    pub fn validate(&self) -> Result<(), SqliteEventLogError> {
        if self.schema != "roboguide.complete-replica-state/v0.1"
            || self.commands.is_empty()
            || self.commands.len() > MAX_ROWS
            || self.checkpoint.checkpoint_json.len() > 64 * 1024 * 1024
        {
            return Err(SqliteEventLogError::Codec(
                "complete original replica state lacks bounded authority".into(),
            ));
        }
        self.recovery.validate_for(&self.events, &self.checkpoint)?;
        let mut ids = std::collections::BTreeSet::new();
        for command in &self.commands {
            if !ids.insert(&command.command_id) {
                return Err(SqliteEventLogError::Codec(
                    "complete original replica command IDs conflict".into(),
                ));
            }
        }
        if self.commands.windows(2).any(|rows| {
            rows[0].authority_revision.checked_add(1) != Some(rows[1].authority_revision)
        }) {
            return Err(SqliteEventLogError::Codec(
                "complete original replica command coverage has a gap".into(),
            ));
        }
        self.commands
            .last()
            .expect("checked nonempty")
            .validate_checkpoint(&self.checkpoint)?;
        if serde_json::to_vec(self)
            .map_err(|error| SqliteEventLogError::Codec(error.to_string()))?
            .len()
            > 256 * 1024 * 1024
        {
            return Err(SqliteEventLogError::Codec(
                "complete original replica state exceeds its transfer bound".into(),
            ));
        }
        Ok(())
    }

    /// Hashes complete original metadata without asserting any content, process or hardware observation.
    pub fn identity(&self) -> Result<String, SqliteEventLogError> {
        self.validate()?;
        let bytes = serde_json::to_vec(
            &serde_json::to_value(self)
                .map_err(|error| SqliteEventLogError::Codec(error.to_string()))?,
        )
        .map_err(|error| SqliteEventLogError::Codec(error.to_string()))?;
        Ok(format!("sha256:{:x}", Sha256::digest(bytes)))
    }
}

/// Captures all original source rows under one actual transaction/writer, never cached partial tables.
pub(super) fn complete(
    connection: &Connection,
) -> Result<CompleteReplicaState, SqliteEventLogError> {
    table_bound(connection, "events", "length(CAST(payload_json AS BLOB))")?;
    table_bound(
        connection,
        "control_command_journal",
        "length(CAST(command_id AS BLOB))+length(CAST(resulting_state_digest AS BLOB))",
    )?;
    let checkpoint = checkpoint_store::read(connection)?.ok_or_else(|| {
        SqliteEventLogError::Codec("original replica source has no checkpoint".into())
    })?;
    let mut statement = connection.prepare("SELECT sequence,event_id,timestamp_ms,correlation_id,causation_id,payload_schema,payload_json FROM events ORDER BY sequence")?;
    let events = statement
        .query_map([], event_from_row)?
        .collect::<Result<Vec<_>, _>>()?;
    let original = CompleteReplicaState {
        schema: "roboguide.complete-replica-state/v0.1".into(),
        checkpoint: (*checkpoint.snapshot).clone(),
        events,
        commands: super::authority_journal::since(connection, 0)?,
        recovery: export(connection)?,
    };
    original.validate()?;
    Ok(original)
}

/// Checks one original table's materialization bound before loading actual source row payloads.
fn table_bound(
    connection: &Connection,
    table: &str,
    bytes: &str,
) -> Result<(), SqliteEventLogError> {
    let (count, size): (u64, u64) = connection.query_row(
        &format!("SELECT COUNT(*),COALESCE(SUM({bytes}),0) FROM {table}"),
        [],
        |row| Ok((row.get(0)?, row.get(1)?)),
    )?;
    if count > MAX_ROWS as u64 || size > MAX_BYTES as u64 {
        return Err(SqliteEventLogError::Codec(
            "original replica recovery table exceeds its complete scan bound".into(),
        ));
    }
    Ok(())
}

/// Observes exact original table presence; missing mandatory authority is never initialized here.
fn exists(connection: &Connection, name: &str) -> Result<bool, SqliteEventLogError> {
    Ok(connection.query_row(
        "SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type='table' AND name=?1)",
        [name],
        |row| row.get(0),
    )?)
}

/// Freezes complete existing original auxiliary rows under the caller's actual original SQL writer.
pub(super) fn export(
    connection: &Connection,
) -> Result<ReplicaRecoveryProjection, SqliteEventLogError> {
    super::archive_operation::require_storage(connection)?;
    super::archive_abort::validate_storage(connection)?;
    table_bound(
        connection,
        "event_log_metadata",
        "length(CAST(value AS BLOB))",
    )?;
    let mut statement =
        connection.prepare("SELECT key,value FROM event_log_metadata ORDER BY key")?;
    let mut metadata = BTreeMap::new();
    for row in statement.query_map([], |row| {
        Ok((row.get::<_, String>(0)?, row.get::<_, String>(1)?))
    })? {
        let (key, value) = row?;
        if METADATA.contains(&key.as_str()) {
            metadata.insert(key, value);
        }
    }
    table_bound(
        connection,
        "event_archive_operations",
        "length(CAST(intent_json AS BLOB))+COALESCE(length(CAST(receipt_json AS BLOB)),0)",
    )?;
    let mut statement = connection.prepare("SELECT command_id,intent_json,state,receipt_json FROM event_archive_operations ORDER BY command_id")?;
    let archive_operations = statement
        .query_map([], |row| {
            Ok(ReplicaArchiveOperation {
                command_id: row.get(0)?,
                intent_json: row.get(1)?,
                state: row.get(2)?,
                receipt_json: row.get(3)?,
            })
        })?
        .collect::<Result<Vec<_>, _>>()?;
    table_bound(
        connection,
        "event_archive_aborts",
        "length(CAST(intent_json AS BLOB))+COALESCE(length(CAST(receipt_json AS BLOB)),0)",
    )?;
    let mut statement = connection.prepare("SELECT command_id,original_command_id,intent_json,receipt_json FROM event_archive_aborts ORDER BY command_id")?;
    let archive_aborts = statement
        .query_map([], |row| {
            Ok(ReplicaArchiveAbort {
                command_id: row.get(0)?,
                original_command_id: row.get(1)?,
                intent_json: row.get(2)?,
                receipt_json: row.get(3)?,
            })
        })?
        .collect::<Result<Vec<_>, _>>()?;
    let catalog_archive_events = if exists(connection, "catalog_archive_events")? {
        Some(super::catalog_archive::read(connection)?)
    } else {
        super::catalog_archive::read(connection)?;
        None
    };
    let archive_manifests = if exists(connection, "event_archive_manifests")? {
        table_bound(
            connection,
            "event_archive_manifests",
            "length(CAST(manifest_json AS BLOB))",
        )?;
        let mut statement = connection.prepare("SELECT start_sequence,end_sequence,manifest_json FROM event_archive_manifests ORDER BY start_sequence")?;
        Some(
            statement
                .query_map([], |row| {
                    Ok(ReplicaArchiveManifest {
                        start_sequence: row.get(0)?,
                        end_sequence: row.get(1)?,
                        manifest_json: row.get(2)?,
                    })
                })?
                .collect::<Result<Vec<_>, _>>()?,
        )
    } else {
        None
    };
    let original = ReplicaRecoveryProjection {
        schema: REPLICA_RECOVERY_PROJECTION_SCHEMA.into(),
        metadata,
        archive_operations,
        archive_aborts,
        catalog_archive_events,
        archive_manifests,
    };
    original.validate_shape()?;
    Ok(original)
}

/// Writes only a complete existing projection into an inactive transaction, never creating source facts.
pub(super) fn install(
    connection: &Connection,
    original: &ReplicaRecoveryProjection,
) -> Result<(), SqliteEventLogError> {
    original.validate_shape()?;
    let previous: Option<String> = connection
        .query_row(
            "SELECT value FROM event_log_metadata WHERE key='archive_authority_id'",
            [],
            |row| row.get(0),
        )
        .optional()?;
    if previous
        .as_ref()
        .is_some_and(|previous| Some(previous) != original.metadata.get("archive_authority_id"))
    {
        return Err(SqliteEventLogError::Codec(
            "replica recovery cannot replace another original Archive authority".into(),
        ));
    }
    connection.execute_batch("CREATE TABLE IF NOT EXISTS event_archive_operations(command_id TEXT PRIMARY KEY NOT NULL,intent_json TEXT NOT NULL,state TEXT NOT NULL,receipt_json TEXT); CREATE TABLE IF NOT EXISTS event_archive_aborts(command_id TEXT PRIMARY KEY NOT NULL,original_command_id TEXT NOT NULL,intent_json TEXT NOT NULL,receipt_json TEXT); DELETE FROM event_archive_aborts; DELETE FROM event_archive_operations;")?;
    for name in METADATA {
        connection.execute("DELETE FROM event_log_metadata WHERE key=?1", [name])?;
    }
    for (key, value) in &original.metadata {
        connection.execute(
            "INSERT INTO event_log_metadata VALUES (?1,?2)",
            params![key, value],
        )?;
    }
    for row in &original.archive_operations {
        connection.execute(
            "INSERT INTO event_archive_operations VALUES (?1,?2,?3,?4)",
            params![row.command_id, row.intent_json, row.state, row.receipt_json],
        )?;
    }
    for row in &original.archive_aborts {
        connection.execute(
            "INSERT INTO event_archive_aborts VALUES (?1,?2,?3,?4)",
            params![
                row.command_id,
                row.original_command_id,
                row.intent_json,
                row.receipt_json
            ],
        )?;
    }
    connection.execute_batch("DROP TABLE IF EXISTS catalog_archive_events; DROP TABLE IF EXISTS event_archive_manifests;")?;
    if let Some(rows) = &original.catalog_archive_events {
        connection.execute_batch("CREATE TABLE catalog_archive_events(sequence INTEGER PRIMARY KEY NOT NULL,data TEXT NOT NULL)")?;
        for row in rows {
            connection.execute(
                "INSERT INTO catalog_archive_events VALUES (?1,?2)",
                params![
                    row.sequence,
                    serde_json::to_string(row)
                        .map_err(|error| SqliteEventLogError::Codec(error.to_string()))?
                ],
            )?;
        }
    }
    if let Some(rows) = &original.archive_manifests {
        connection.execute_batch("CREATE TABLE event_archive_manifests(start_sequence INTEGER PRIMARY KEY, end_sequence INTEGER UNIQUE NOT NULL, manifest_json TEXT NOT NULL)")?;
        for row in rows {
            connection.execute(
                "INSERT INTO event_archive_manifests VALUES (?1,?2,?3)",
                params![row.start_sequence, row.end_sequence, row.manifest_json],
            )?;
        }
    }
    Ok(())
}

impl ReplicaRecoveryProjection {
    /// Requires original retained auxiliary history to remain an immutable prefix of a later view.
    /// New original commands/actual receipts may extend history; Unknown never becomes absent.
    pub fn follows(&self, previous: &Self) -> Result<(), SqliteEventLogError> {
        self.validate_shape()?;
        previous.validate_shape()?;
        if self.metadata.get("archive_authority_id")
            != previous.metadata.get("archive_authority_id")
            || self.history_floor()? < previous.history_floor()?
        {
            return Err(SqliteEventLogError::Codec(
                "later replica recovery changed original authority/floor".into(),
            ));
        }
        for old in &previous.archive_operations {
            let current = self
                .archive_operations
                .iter()
                .find(|current| current.command_id == old.command_id)
                .ok_or_else(|| {
                    SqliteEventLogError::Codec(
                        "later replica recovery lost an original copy intent".into(),
                    )
                })?;
            if current.intent_json != old.intent_json
                || (old.receipt_json.is_some() && current != old)
                || (old.state == "compacted" && current.state != old.state)
            {
                return Err(SqliteEventLogError::Codec(
                    "later replica recovery rewrote original copy evidence".into(),
                ));
            }
        }
        for old in &previous.archive_aborts {
            let current = self
                .archive_aborts
                .iter()
                .find(|current| current.command_id == old.command_id)
                .ok_or_else(|| {
                    SqliteEventLogError::Codec(
                        "later replica recovery lost an original cleanup intent".into(),
                    )
                })?;
            if current.original_command_id != old.original_command_id
                || current.intent_json != old.intent_json
                || (old.receipt_json.is_some() && current != old)
            {
                return Err(SqliteEventLogError::Codec(
                    "later replica recovery rewrote original cleanup evidence".into(),
                ));
            }
        }
        if previous.catalog_archive_events.as_ref().is_some_and(|old| {
            self.catalog_archive_events
                .as_ref()
                .is_none_or(|current| !current.starts_with(old))
        }) || previous.archive_manifests.as_ref().is_some_and(|old| {
            self.archive_manifests
                .as_ref()
                .is_none_or(|current| !current.starts_with(old))
        }) {
            return Err(SqliteEventLogError::Codec(
                "later replica recovery lost original Catalog/manifest history".into(),
            ));
        }
        if self.history_floor()? == previous.history_floor()? {
            for key in [
                "archive_manifest",
                "archive_timestamp_floor",
                "catalog_archive_checkpoint",
            ] {
                if self.metadata.get(key) != previous.metadata.get(key) {
                    return Err(SqliteEventLogError::Codec(
                        "unchanged source floor has different original Archive coverage".into(),
                    ));
                }
            }
            if self.catalog_archive_events != previous.catalog_archive_events
                || self.archive_manifests != previous.archive_manifests
            {
                return Err(SqliteEventLogError::Codec(
                    "unchanged source floor changed original Catalog/manifest rows".into(),
                ));
            }
        }
        Ok(())
    }

    /// Rejects unbounded/incomplete projection shape before SQL allocation or namespace replacement.
    fn validate_shape(&self) -> Result<(), SqliteEventLogError> {
        if self.schema != REPLICA_RECOVERY_PROJECTION_SCHEMA
            || self
                .metadata
                .keys()
                .any(|key| !METADATA.contains(&key.as_str()))
            || self
                .metadata
                .get("archive_operations_schema")
                .map(String::as_str)
                != Some("roboguide.archive-operations/v0.2")
            || self
                .metadata
                .get("archive_authority_id")
                .is_none_or(|owner| {
                    owner.strip_prefix("controller-store:").is_none_or(|value| {
                        value.len() != 64
                            || !value
                                .bytes()
                                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
                    })
                })
            || self.archive_operations.len() > 4096
            || self.archive_aborts.len() > 4096
            || self
                .archive_operations
                .windows(2)
                .any(|rows| rows[0].command_id >= rows[1].command_id)
            || self
                .archive_aborts
                .windows(2)
                .any(|rows| rows[0].command_id >= rows[1].command_id)
            || self
                .catalog_archive_events
                .as_ref()
                .is_some_and(|rows| rows.len() > MAX_ROWS)
            || self
                .archive_manifests
                .as_ref()
                .is_some_and(|rows| rows.len() > MAX_ROWS)
        {
            return Err(SqliteEventLogError::Codec(
                "original replica recovery schema/mandatory identity/row order differs".into(),
            ));
        }
        let raw = serde_json::to_vec(self)
            .map_err(|error| SqliteEventLogError::Codec(error.to_string()))?;
        if raw.len() > MAX_BYTES {
            return Err(SqliteEventLogError::Codec(
                "complete original replica recovery exceeds its byte bound".into(),
            ));
        }
        Ok(())
    }

    /// Returns the actual original source floor, never a new Raft metadata compaction boundary.
    pub fn history_floor(&self) -> Result<u64, SqliteEventLogError> {
        self.metadata
            .get("history_floor")
            .map(|value| {
                value
                    .parse::<u64>()
                    .map_err(|error| SqliteEventLogError::Codec(error.to_string()))
            })
            .transpose()
            .map(|value| value.unwrap_or(0))
    }

    /// Hashes the complete original projection, preserving nested raw intent and manifest bytes.
    pub fn identity(&self) -> Result<String, SqliteEventLogError> {
        self.validate_shape()?;
        let raw = serde_json::to_vec(
            &serde_json::to_value(self)
                .map_err(|error| SqliteEventLogError::Codec(error.to_string()))?,
        )
        .map_err(|error| SqliteEventLogError::Codec(error.to_string()))?;
        Ok(format!("sha256:{:x}", Sha256::digest(raw)))
    }

    /// Validates original rows using the maintained SQL/codecs beside a complete original event tail.
    /// This temporary SQL validation creates no live store, publication, process or use permission.
    pub fn validate_for(
        &self,
        events: &[PersistedEvent],
        checkpoint: &PersistedCheckpoint,
    ) -> Result<(), SqliteEventLogError> {
        self.validate_shape()?;
        let floor = self.history_floor()?;
        if checkpoint.schema != crate::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA
            || events.len() > MAX_ROWS
            || floor.checked_add(events.len() as u64) != Some(checkpoint.event_sequence)
        {
            return Err(SqliteEventLogError::Codec(
                "original replica recovery lacks its complete unarchived event tail".into(),
            ));
        }
        let connection = Connection::open_in_memory()?;
        connection.execute_batch("CREATE TABLE event_log_metadata(key TEXT PRIMARY KEY NOT NULL,value TEXT NOT NULL); CREATE TABLE events(sequence INTEGER PRIMARY KEY,event_id TEXT UNIQUE NOT NULL,timestamp_ms INTEGER NOT NULL,correlation_id TEXT NOT NULL,causation_id TEXT,payload_schema TEXT NOT NULL,payload_json TEXT NOT NULL)")?;
        install(&connection, self)?;
        for (index, event) in events.iter().enumerate() {
            if event.sequence != floor + index as u64 + 1 {
                return Err(SqliteEventLogError::Codec(
                    "original replica recovery event tail has a gap".into(),
                ));
            }
            validate_persisted_event(event)?;
            connection.execute(
                "INSERT INTO events VALUES (?1,?2,?3,?4,?5,?6,?7)",
                params![
                    event.sequence,
                    event.event_id,
                    event.timestamp_ms,
                    event.correlation_id,
                    event.causation_id,
                    event.payload_schema,
                    event.payload_json
                ],
            )?;
        }
        super::archive_operation::require_storage(&connection)?;
        super::archive_abort::validate_storage(&connection)?;
        for row in &self.archive_operations {
            let (intent, _) = super::archive_operation::existing(&connection, &row.command_id)?
                .ok_or_else(|| {
                    SqliteEventLogError::Codec("original Archive recovery lost its row".into())
                })?;
            if Some(&intent.request.authority_id) != self.metadata.get("archive_authority_id") {
                return Err(SqliteEventLogError::Codec(
                    "original Archive recovery changed source authority".into(),
                ));
            }
        }
        super::catalog_archive::current(&connection)?;
        let timestamp = super::archive::read_archive_timestamp_floor(&connection)?;
        let mut through = 0;
        for row in self.archive_manifests.iter().flatten() {
            let manifest: ArchiveManifest = serde_json::from_str(&row.manifest_json)
                .map_err(|error| SqliteEventLogError::Codec(error.to_string()))?;
            super::archive::validate_manifest(&manifest).map_err(SqliteEventLogError::Codec)?;
            if manifest.start_sequence != row.start_sequence
                || manifest.end_sequence != row.end_sequence
                || row.start_sequence != through + 1
                || manifest.checkpoint_sequence > checkpoint.event_sequence
            {
                return Err(SqliteEventLogError::Codec(
                    "original Archive manifest recovery lost its contiguous prefix".into(),
                ));
            }
            through = row.end_sequence;
        }
        if through != floor
            || (floor == 0 && timestamp != 0)
            || self.metadata.get("archive_manifest")
                != self
                    .archive_manifests
                    .as_ref()
                    .and_then(|rows| rows.last())
                    .map(|row| &row.manifest_json)
        {
            return Err(SqliteEventLogError::Codec(
                "original Archive manifest/floor recovery differs".into(),
            ));
        }
        Ok(())
    }
}

impl SqliteEventLog {
    /// Captures complete original committed metadata in one actual SQLite snapshot, with no writes.
    /// External quiescent tools still own all actual original writer leases before using this capture.
    pub fn read_complete_replica_state(
        path: impl AsRef<std::path::Path>,
    ) -> Result<CompleteReplicaState, SqliteEventLogError> {
        let mut connection =
            Connection::open_with_flags(path, rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY)?;
        let transaction = connection.transaction()?;
        let original = complete(&transaction)?;
        transaction.commit()?;
        Ok(original)
    }

    /// Materializes one exact complete source after the composition has independently drained writers.
    pub fn install_complete_replica_state(
        &self,
        original: &CompleteReplicaState,
    ) -> Result<(), SqliteEventLogError> {
        original.validate()?;
        self.install_replica_authority_with_recovery(
            &original.events,
            &original.checkpoint,
            &original.commands,
            &original.recovery,
        )
    }

    /// Exports original committed recovery rows without starting a copy or synthesizing an identity.
    pub fn replica_recovery_projection(
        &self,
    ) -> Result<ReplicaRecoveryProjection, SqliteEventLogError> {
        self.ensure_no_open_batch()?;
        let connection = self
            .connection
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        export(&connection)
    }
}
