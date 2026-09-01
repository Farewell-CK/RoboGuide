#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! Durable evidence storage for the State & Memory Plane bootstrap.
//!
//! The durable slice stores the immutable event envelope and a versioned JSON payload, plus an
//! atomically committed controller projection checkpoint. Applying events as a complete
//! event-sourced projection remains a separate contract.

use domain::{CorrelationId, EventId, EventPayload, EventRecord, TimestampMs};
use ports::EventSink;
use rusqlite::{Connection, OptionalExtension, TransactionBehavior, params};
use std::collections::BTreeSet;
use std::path::Path;
use std::sync::{Arc, Mutex};

/// Previous JSON payload codec retained for Mission/Execution evidence compatibility.
const EVENT_PAYLOAD_SCHEMA_V2: &str = "domain.EventPayload.json/v2";

/// Current JSON payload codec including Distributed Spatial Memory evidence variants.
const EVENT_PAYLOAD_SCHEMA_V3: &str = "domain.EventPayload.json/v3";

/// JSON payload codec including strong localization verification evidence.
const EVENT_PAYLOAD_SCHEMA_V4: &str = "domain.EventPayload.json/v4";

/// Current JSON payload codec including execution coordination relation evidence.
const EVENT_PAYLOAD_SCHEMA_V5: &str = "domain.EventPayload.json/v5";

/// One event row retained by the durable evidence store.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PersistedEvent {
    /// Monotonic append sequence used for replay ordering and pagination.
    pub sequence: u64,
    /// Stable event identity.
    pub event_id: String,
    /// RoboGuide-local event timestamp in milliseconds.
    pub timestamp_ms: u64,
    /// Correlation identity shared by the operation.
    pub correlation_id: String,
    /// Optional immediate causal event identity.
    pub causation_id: Option<String>,
    /// Rust/domain schema marker for the stored payload representation.
    pub payload_schema: String,
    /// Versioned JSON payload representation.
    pub payload_json: String,
}

/// One atomically persisted controller projection checkpoint.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PersistedCheckpoint {
    /// Event sequence represented by this projection.
    pub event_sequence: u64,
    /// Versioned checkpoint schema marker.
    pub schema: String,
    /// Serialized complete controller projection.
    pub checkpoint_json: String,
}

/// Failures returned by the SQLite evidence store.
#[derive(Debug)]
pub enum SqliteEventLogError {
    /// SQLite returned an operational or schema error.
    Sqlite(rusqlite::Error),
    /// The event log mutex was poisoned by a previous panic.
    LockPoisoned,
    /// A persisted event could not be decoded into the versioned domain envelope.
    Codec(String),
}

impl std::fmt::Display for SqliteEventLogError {
    /// Formats a durable evidence failure.
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Sqlite(error) => write!(formatter, "event log SQLite failure: {error}"),
            Self::LockPoisoned => formatter.write_str("event log lock is poisoned"),
            Self::Codec(error) => write!(formatter, "event log payload codec failure: {error}"),
        }
    }
}

impl std::error::Error for SqliteEventLogError {}

impl From<rusqlite::Error> for SqliteEventLogError {
    /// Converts a SQLite failure into the event-log error boundary.
    fn from(value: rusqlite::Error) -> Self {
        Self::Sqlite(value)
    }
}

/// SQLite WAL-backed immutable event envelope store.
#[derive(Clone)]
pub struct SqliteEventLog {
    /// Serialized access to one SQLite connection.
    connection: Arc<Mutex<Connection>>,
    /// Process-local event counter used for deterministic event identities.
    next_event_number: Arc<Mutex<u64>>,
    /// Start sequence retained while an application-level event batch is open.
    batch_start_number: Arc<Mutex<Option<u64>>>,
    /// Last durable write failure observed by the EventSink boundary.
    last_error: Arc<Mutex<Option<String>>>,
}

impl std::fmt::Debug for SqliteEventLog {
    /// Hides the SQLite connection while retaining useful type diagnostics.
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("SqliteEventLog")
            .finish_non_exhaustive()
    }
}

impl SqliteEventLog {
    /// Opens a WAL database and creates the immutable evidence schema if needed.
    pub fn open(path: impl AsRef<Path>) -> Result<Self, SqliteEventLogError> {
        let connection = Connection::open(path)?;
        connection.pragma_update(None, "journal_mode", "WAL")?;
        connection.pragma_update(None, "synchronous", "FULL")?;
        connection.execute_batch(
            "CREATE TABLE IF NOT EXISTS events (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT UNIQUE NOT NULL,
                timestamp_ms INTEGER NOT NULL,
                correlation_id TEXT NOT NULL,
                causation_id TEXT,
                payload_schema TEXT NOT NULL,
                payload_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp_ms, event_id);
            CREATE INDEX IF NOT EXISTS idx_events_correlation ON events(correlation_id, event_id);
            CREATE TABLE IF NOT EXISTS event_log_metadata (
                key TEXT PRIMARY KEY NOT NULL,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS controller_checkpoint (
                singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
                event_sequence INTEGER NOT NULL,
                schema TEXT NOT NULL,
                checkpoint_json TEXT NOT NULL
            );",
        )?;
        migrate_event_schema(&connection)?;
        let metadata_event_number = connection
            .query_row(
                "SELECT value FROM event_log_metadata WHERE key = 'next_event_number'",
                [],
                |row| row.get::<_, String>(0),
            )
            .optional()?
            .and_then(|value| value.parse::<u64>().ok())
            .unwrap_or(0);
        let max_sequence = connection
            .query_row("SELECT COALESCE(MAX(sequence), 0) FROM events", [], |row| {
                row.get::<_, u64>(0)
            })
            .unwrap_or(0);
        let next_event_number = metadata_event_number.max(max_sequence);
        Ok(Self {
            connection: Arc::new(Mutex::new(connection)),
            next_event_number: Arc::new(Mutex::new(next_event_number)),
            batch_start_number: Arc::new(Mutex::new(None)),
            last_error: Arc::new(Mutex::new(None)),
        })
    }

    /// Returns durable events in monotonic append-sequence order.
    pub fn events(&self) -> Result<Vec<PersistedEvent>, SqliteEventLogError> {
        self.ensure_no_open_batch()?;
        let connection = self
            .connection
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        let mut statement = connection.prepare(
            "SELECT sequence, event_id, timestamp_ms, correlation_id, causation_id,
                    payload_schema, payload_json FROM events ORDER BY sequence",
        )?;
        let rows = statement.query_map([], |row| {
            Ok(PersistedEvent {
                sequence: row.get(0)?,
                event_id: row.get(1)?,
                timestamp_ms: row.get(2)?,
                correlation_id: row.get(3)?,
                causation_id: row.get(4)?,
                payload_schema: row.get(5)?,
                payload_json: row.get(6)?,
            })
        })?;
        rows.collect::<Result<Vec<_>, _>>().map_err(Into::into)
    }

    /// Returns a bounded event page after an optional append sequence.
    pub fn events_page(
        &self,
        after_sequence: Option<u64>,
        limit: usize,
    ) -> Result<Vec<PersistedEvent>, SqliteEventLogError> {
        self.ensure_no_open_batch()?;
        let limit = limit.clamp(1, 1_000) as u64;
        let connection = self
            .connection
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        let mut statement = if after_sequence.is_some() {
            connection.prepare(
                "SELECT sequence, event_id, timestamp_ms, correlation_id, causation_id,
                        payload_schema, payload_json FROM events
                 WHERE sequence > ?1 ORDER BY sequence LIMIT ?2",
            )?
        } else {
            connection.prepare(
                "SELECT sequence, event_id, timestamp_ms, correlation_id, causation_id,
                        payload_schema, payload_json FROM events
                 ORDER BY sequence LIMIT ?1",
            )?
        };
        let rows = if let Some(after_sequence) = after_sequence {
            statement.query_map(params![after_sequence, limit], event_from_row)?
        } else {
            statement.query_map(params![limit], event_from_row)?
        };
        rows.collect::<Result<Vec<_>, _>>().map_err(Into::into)
    }

    /// Returns the number of durable events currently retained.
    pub fn len(&self) -> Result<usize, SqliteEventLogError> {
        self.ensure_no_open_batch()?;
        let connection = self
            .connection
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        let count = connection.query_row("SELECT COUNT(*) FROM events", [], |row| {
            row.get::<_, i64>(0)
        })?;
        usize::try_from(count).map_err(|_| {
            SqliteEventLogError::Sqlite(rusqlite::Error::IntegralValueOutOfRange(0, count))
        })
    }

    /// Returns whether the durable event table currently has no rows.
    pub fn is_empty(&self) -> Result<bool, SqliteEventLogError> {
        Ok(self.len()? == 0)
    }

    /// Returns the latest durable event sequence, or zero for an empty log.
    pub fn latest_sequence(&self) -> Result<u64, SqliteEventLogError> {
        self.ensure_no_open_batch()?;
        let connection = self
            .connection
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        connection
            .query_row("SELECT COALESCE(MAX(sequence), 0) FROM events", [], |row| {
                row.get(0)
            })
            .map_err(Into::into)
    }

    /// Loads the last committed controller projection checkpoint, if present.
    pub fn load_checkpoint(&self) -> Result<Option<PersistedCheckpoint>, SqliteEventLogError> {
        self.ensure_no_open_batch()?;
        let connection = self
            .connection
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        connection
            .query_row(
                "SELECT event_sequence, schema, checkpoint_json
                 FROM controller_checkpoint WHERE singleton = 1",
                [],
                |row| {
                    Ok(PersistedCheckpoint {
                        event_sequence: row.get(0)?,
                        schema: row.get(1)?,
                        checkpoint_json: row.get(2)?,
                    })
                },
            )
            .optional()
            .map_err(Into::into)
    }

    /// Saves a controller projection inside the currently open event batch.
    pub fn save_checkpoint(
        &self,
        schema: &str,
        checkpoint_json: &str,
    ) -> Result<(), SqliteEventLogError> {
        let batch_start = self
            .batch_start_number
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        if batch_start.is_none() {
            return Err(SqliteEventLogError::Codec(
                "controller checkpoint requires an open event batch".to_string(),
            ));
        }
        let event_sequence = *self
            .next_event_number
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        let connection = self
            .connection
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        connection.execute(
            "INSERT INTO controller_checkpoint(singleton, event_sequence, schema, checkpoint_json)
             VALUES (1, ?1, ?2, ?3)
             ON CONFLICT(singleton) DO UPDATE SET
                 event_sequence = excluded.event_sequence,
                 schema = excluded.schema,
                 checkpoint_json = excluded.checkpoint_json",
            params![event_sequence, schema, checkpoint_json],
        )?;
        Ok(())
    }

    /// Returns and clears the most recent append failure, if one occurred.
    pub fn take_error(&self) -> Result<Option<String>, SqliteEventLogError> {
        let mut error = self
            .last_error
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        Ok(error.take())
    }

    /// Begins an application-level SQLite event batch.
    pub fn begin_batch(&self) -> Result<(), SqliteEventLogError> {
        let mut batch_start = self
            .batch_start_number
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        if batch_start.is_some() {
            return Err(SqliteEventLogError::Codec(
                "event batch is already open".to_string(),
            ));
        }
        let counter = self
            .next_event_number
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        let connection = self
            .connection
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        connection.execute_batch("BEGIN IMMEDIATE")?;
        *batch_start = Some(*counter);
        Ok(())
    }

    /// Commits the current application-level event batch.
    pub fn commit_batch(&self) -> Result<(), SqliteEventLogError> {
        let mut batch_start = self
            .batch_start_number
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        if batch_start.is_none() {
            return Err(SqliteEventLogError::Codec(
                "event batch is not open".to_string(),
            ));
        }
        let connection = self
            .connection
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        connection.execute_batch("COMMIT")?;
        *batch_start = None;
        Ok(())
    }

    /// Rolls back the current application-level event batch and restores its sequence counter.
    pub fn rollback_batch(&self) -> Result<(), SqliteEventLogError> {
        let mut batch_start = self
            .batch_start_number
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        let Some(start_number) = *batch_start else {
            return Err(SqliteEventLogError::Codec(
                "event batch is not open".to_string(),
            ));
        };
        let connection = self
            .connection
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        connection.execute_batch("ROLLBACK")?;
        *batch_start = None;
        let mut counter = self
            .next_event_number
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        *counter = start_number;
        Ok(())
    }

    /// Decodes all persisted JSON payloads into immutable domain event records.
    pub fn decoded_events(&self) -> Result<Vec<EventRecord>, SqliteEventLogError> {
        self.events()?
            .into_iter()
            .map(|event| {
                let event_id = EventId::new(event.event_id)
                    .map_err(|error| SqliteEventLogError::Codec(error.to_string()))?;
                let correlation_id = CorrelationId::new(event.correlation_id)
                    .map_err(|error| SqliteEventLogError::Codec(error.to_string()))?;
                let causation_id = event
                    .causation_id
                    .map(|value| {
                        EventId::new(value)
                            .map_err(|error| SqliteEventLogError::Codec(error.to_string()))
                    })
                    .transpose()?;
                payload_schema_version(&event.payload_schema)?;
                let payload: EventPayload =
                    serde_json::from_str(&event.payload_json).map_err(|error| {
                        SqliteEventLogError::Codec(format!(
                            "cannot decode {} payload: {error}",
                            event.payload_schema
                        ))
                    })?;
                validate_payload_schema(&event.payload_schema, &payload)?;
                Ok(EventRecord::new(
                    event_id,
                    TimestampMs::new(event.timestamp_ms),
                    correlation_id,
                    causation_id,
                    payload,
                ))
            })
            .collect()
    }

    /// Rejects reads that could otherwise observe rows inside an open connection transaction.
    fn ensure_no_open_batch(&self) -> Result<(), SqliteEventLogError> {
        let batch_start = self
            .batch_start_number
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        if batch_start.is_some() {
            return Err(SqliteEventLogError::Codec(
                "event batch is still open".to_string(),
            ));
        }
        Ok(())
    }
}

/// Parses one supported event payload codec marker before inspecting its JSON body.
fn payload_schema_version(schema: &str) -> Result<u8, SqliteEventLogError> {
    match schema {
        EVENT_PAYLOAD_SCHEMA_V2 => Ok(2),
        EVENT_PAYLOAD_SCHEMA_V3 => Ok(3),
        EVENT_PAYLOAD_SCHEMA_V4 => Ok(4),
        EVENT_PAYLOAD_SCHEMA_V5 => Ok(5),
        _ => Err(SqliteEventLogError::Codec(format!(
            "unsupported event payload schema {schema}"
        ))),
    }
}

/// Rejects payload variants introduced after the persisted codec marker.
fn validate_payload_schema(
    schema: &str,
    payload: &EventPayload,
) -> Result<(), SqliteEventLogError> {
    let version = payload_schema_version(schema)?;
    let minimum_version = match payload {
        EventPayload::ExecutionRelationRegistered { .. }
        | EventPayload::ExecutionRelationStateChanged { .. }
        | EventPayload::ExecutionRelationReconciliationRequired { .. } => 5,
        EventPayload::MapLocalizationEvidenceRecorded { .. } => 4,
        EventPayload::MapArtifactDeclared { .. }
        | EventPayload::MapArtifactPublished { .. }
        | EventPayload::MapArtifactStaged { .. }
        | EventPayload::MapArtifactImported { .. }
        | EventPayload::MapLocalizationVerified { .. }
        | EventPayload::MapArtifactRejected { .. } => 3,
        _ => 2,
    };
    if version < minimum_version {
        return Err(SqliteEventLogError::Codec(format!(
            "event payload requires schema v{minimum_version}, but row is marked v{version}"
        )));
    }
    Ok(())
}

impl EventSink for SqliteEventLog {
    /// Appends one immutable event envelope in the same SQLite transaction as its sequence update.
    fn append(
        &mut self,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        causation_id: Option<&EventId>,
        payload: EventPayload,
    ) {
        let result = self.append_inner(timestamp, correlation_id, causation_id, payload);
        if let Err(error) = result
            && let Ok(mut last_error) = self.last_error.lock()
        {
            *last_error = Some(error);
        }
    }
}

impl SqliteEventLog {
    /// Performs one append and returns a diagnostic instead of panicking on storage failure.
    fn append_inner(
        &self,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        causation_id: Option<&EventId>,
        payload: EventPayload,
    ) -> Result<(), String> {
        let batch_start = self
            .batch_start_number
            .lock()
            .map_err(|_| "event batch lock is poisoned".to_string())?;
        let mut counter = self
            .next_event_number
            .lock()
            .map_err(|_| "event counter lock is poisoned".to_string())?;
        *counter += 1;
        let sequence = *counter;
        let event_id =
            EventId::new(format!("event-{sequence}")).map_err(|error| error.to_string())?;
        let record = EventRecord::new(
            event_id,
            timestamp,
            correlation_id.clone(),
            causation_id.cloned(),
            payload,
        );
        let payload_json =
            serde_json::to_string(record.payload()).map_err(|error| error.to_string())?;
        let mut connection = self
            .connection
            .lock()
            .map_err(|_| "event connection lock is poisoned".to_string())?;
        let insert = |connection: &Connection| {
            connection.execute(
                "INSERT INTO events
                 (sequence, event_id, timestamp_ms, correlation_id, causation_id, payload_schema, payload_json)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
                params![
                    sequence,
                    record.event_id().as_str(),
                    record.timestamp().as_millis(),
                    record.correlation_id().as_str(),
                    record.causation_id().map(|id| id.as_str()),
                    EVENT_PAYLOAD_SCHEMA_V5,
                    payload_json,
                ],
            )
        };
        let update_counter = |connection: &Connection| {
            connection.execute(
                "INSERT INTO event_log_metadata(key, value) VALUES ('next_event_number', ?1)
                 ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                params![sequence.to_string()],
            )
        };
        if batch_start.is_some() {
            insert(&connection).map_err(|error| error.to_string())?;
            update_counter(&connection).map_err(|error| error.to_string())?;
            Ok(())
        } else {
            let transaction = connection
                .transaction_with_behavior(TransactionBehavior::Immediate)
                .map_err(|error| error.to_string())?;
            insert(&transaction).map_err(|error| error.to_string())?;
            update_counter(&transaction).map_err(|error| error.to_string())?;
            transaction.commit().map_err(|error| error.to_string())
        }
    }
}

/// Reads one event row using the canonical sequence ordering.
fn event_from_row(row: &rusqlite::Row<'_>) -> rusqlite::Result<PersistedEvent> {
    Ok(PersistedEvent {
        sequence: row.get(0)?,
        event_id: row.get(1)?,
        timestamp_ms: row.get(2)?,
        correlation_id: row.get(3)?,
        causation_id: row.get(4)?,
        payload_schema: row.get(5)?,
        payload_json: row.get(6)?,
    })
}

/// Migrates the evidence table from the pre-sequence/debug payload shape.
fn migrate_event_schema(connection: &Connection) -> Result<(), SqliteEventLogError> {
    let columns = table_columns(connection, "events")?;
    if !columns.contains("sequence") {
        connection.execute("ALTER TABLE events ADD COLUMN sequence INTEGER", [])?;
        connection.execute(
            "UPDATE events SET sequence = rowid WHERE sequence IS NULL",
            [],
        )?;
    }
    if !columns.contains("payload_json") {
        if !columns.contains("payload_debug") {
            return Err(SqliteEventLogError::Codec(
                "events table has neither payload_json nor payload_debug".to_string(),
            ));
        }
        connection.execute("ALTER TABLE events ADD COLUMN payload_json TEXT", [])?;
        connection.execute("UPDATE events SET payload_json = payload_debug", [])?;
    }
    if !columns.contains("payload_schema") {
        connection.execute("ALTER TABLE events ADD COLUMN payload_schema TEXT", [])?;
        connection.execute(
            "UPDATE events SET payload_schema = 'domain.EventPayload.debug/v0' WHERE payload_schema IS NULL",
            [],
        )?;
    }
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_events_sequence ON events(sequence)",
        [],
    )?;
    Ok(())
}

/// Returns the columns currently present in a SQLite table.
fn table_columns(
    connection: &Connection,
    table: &str,
) -> Result<BTreeSet<String>, SqliteEventLogError> {
    let mut statement = connection.prepare(&format!("PRAGMA table_info({table})"))?;
    let rows = statement.query_map([], |row| row.get::<_, String>(1))?;
    rows.collect::<Result<BTreeSet<_>, _>>().map_err(Into::into)
}

#[cfg(test)]
mod tests {
    use super::*;
    use domain::{
        ContentDigest, CorrelationId, EventPayload, ExecutionGroupId, LocalizationFrames,
        LocalizationVerificationEvidence, MapArtifactManifest, MapArtifactRef, MapId,
        MapRevisionId, MapRevisionSelector, MissionId, NodeId, PoseQualityComparison,
        PoseQualityEvidence, RoleId, SpatialAnchorId, TaskId, TaskRef, TimestampMs,
    };
    use tempfile::tempdir;

    /// Builds one valid map manifest shared by payload-version compatibility tests.
    fn manifest() -> MapArtifactManifest {
        MapArtifactManifest::new(
            MapArtifactRef::new(
                MapRevisionSelector::new(
                    MapId::new("warehouse").expect("map id is valid"),
                    MapRevisionId::new("r1").expect("revision id is valid"),
                ),
                ContentDigest::new(format!("sha256:{}", "a".repeat(64))).expect("digest is valid"),
                10,
            ),
            "application/octet-stream",
            "grid-v1",
            NodeId::new("dog-a").expect("node id is valid"),
            None,
            MissionId::new("mission-build").expect("mission id is valid"),
            Some("execution-build".to_string()),
            None,
            "map",
            "enu",
            SpatialAnchorId::new("warehouse-origin").expect("anchor is valid"),
            Some(0.05),
            TimestampMs::new(10),
            None,
        )
        .expect("manifest is valid")
    }

    /// Builds one valid v4-only localization evidence payload.
    fn localization_evidence_payload() -> EventPayload {
        let manifest = manifest();
        let mission_id = MissionId::new("mission-localize").expect("mission id is valid");
        let evidence = LocalizationVerificationEvidence::new(
            manifest.artifact().clone(),
            mission_id.clone(),
            TaskRef::new(
                mission_id,
                TaskId::new("verify-map").expect("task id is valid"),
            ),
            ExecutionGroupId::new("group-localize").expect("group id is valid"),
            RoleId::new("localizer").expect("role id is valid"),
            NodeId::new("dog-b").expect("node id is valid"),
            "execution-verify",
            "attempt-verify",
            "warehouse-local",
            "localization",
            PoseQualityEvidence::new(
                "translation_stddev",
                "0.08",
                "0.10",
                "m",
                PoseQualityComparison::AtMost,
            )
            .expect("pose quality is valid"),
            LocalizationFrames::new("map", "odom", "base_link").expect("frames are valid"),
            manifest.anchor_id().clone(),
            TimestampMs::new(20),
        )
        .expect("localization evidence is valid");
        EventPayload::MapLocalizationEvidenceRecorded { evidence }
    }

    /// Builds one v5-only execution relation registration payload.
    fn execution_relation_payload() -> EventPayload {
        let mission_id = MissionId::new("mission-relation").expect("mission id is valid");
        EventPayload::ExecutionRelationRegistered {
            group_id: ExecutionGroupId::new("group-relation").expect("group id is valid"),
            relation_id: domain::ExecutionRelationId::new("safety-guards-navigation")
                .expect("relation id is valid"),
            source_task_ref: TaskRef::new(
                mission_id.clone(),
                TaskId::new("observe-safety").expect("task id is valid"),
            ),
            source_role_id: RoleId::new("safety-observer").expect("role id is valid"),
            target_task_ref: TaskRef::new(
                mission_id,
                TaskId::new("navigate").expect("task id is valid"),
            ),
            target_role_id: RoleId::new("navigator").expect("role id is valid"),
            kind: domain::ExecutionRelationKind::RequiresActive,
        }
    }

    /// WAL storage survives reopening and preserves causal envelope fields.
    #[test]
    fn sqlite_event_log_survives_reopen() {
        let directory = tempdir().expect("temporary directory should exist");
        let path = directory.path().join("events.sqlite3");
        let correlation = CorrelationId::new("test-correlation").expect("correlation valid");
        let mut log = SqliteEventLog::open(&path).expect("event log opens");
        log.append(
            TimestampMs::new(10),
            &correlation,
            None,
            EventPayload::ExecutionGroupBlocked {
                group_id: domain::ExecutionGroupId::new("group-a").expect("id valid"),
                task_ref: domain::TaskRef::new(
                    domain::MissionId::new("mission-a").expect("id valid"),
                    domain::TaskId::new("task-a").expect("id valid"),
                ),
                reason: "test".to_string(),
            },
        );
        drop(log);
        let reopened = SqliteEventLog::open(&path).expect("event log reopens");
        let events = reopened.events().expect("events are readable");
        assert_eq!(events.len(), 1);
        assert_eq!(events[0].event_id, "event-1");
        assert_eq!(events[0].correlation_id, "test-correlation");
        assert_eq!(events[0].payload_schema, EVENT_PAYLOAD_SCHEMA_V5);
        let payload: EventPayload =
            serde_json::from_str(&events[0].payload_json).expect("payload codec is readable");
        assert!(matches!(
            payload,
            EventPayload::ExecutionGroupBlocked { .. }
        ));
        assert_eq!(reopened.decoded_events().expect("events decode").len(), 1);
    }

    /// The current decoder retains the previous v2 JSON path after v3 Spatial variants ship.
    #[test]
    fn event_decoder_retains_v2_payload_compatibility() {
        let directory = tempdir().expect("temporary directory should exist");
        let path = directory.path().join("events-v2.sqlite3");
        let correlation = CorrelationId::new("v2-compatibility").expect("correlation valid");
        let mut log = SqliteEventLog::open(&path).expect("event log opens");
        log.append(
            TimestampMs::new(10),
            &correlation,
            None,
            EventPayload::ExecutionGroupBlocked {
                group_id: domain::ExecutionGroupId::new("group-v2").expect("id valid"),
                task_ref: domain::TaskRef::new(
                    domain::MissionId::new("mission-v2").expect("id valid"),
                    domain::TaskId::new("task-v2").expect("id valid"),
                ),
                reason: "compatibility".to_string(),
            },
        );
        log.connection
            .lock()
            .expect("event connection lock is available")
            .execute(
                "UPDATE events SET payload_schema = ?1 WHERE sequence = 1",
                [EVENT_PAYLOAD_SCHEMA_V2],
            )
            .expect("fixture marker changes to v2");

        let decoded = log.decoded_events().expect("v2 payload remains readable");
        assert!(matches!(
            decoded[0].payload(),
            EventPayload::ExecutionGroupBlocked { reason, .. } if reason == "compatibility"
        ));
    }

    /// A v2 marker cannot masquerade a Spatial Memory variant introduced by codec v3.
    #[test]
    fn event_decoder_rejects_spatial_payload_under_v2_marker() {
        let directory = tempdir().expect("temporary directory should exist");
        let path = directory.path().join("events-spatial-v2.sqlite3");
        let correlation = CorrelationId::new("spatial-version").expect("correlation valid");
        let mut log = SqliteEventLog::open(&path).expect("event log opens");
        log.append(
            TimestampMs::new(10),
            &correlation,
            None,
            EventPayload::MapArtifactDeclared {
                manifest: manifest(),
            },
        );
        log.connection
            .lock()
            .expect("event connection lock is available")
            .execute(
                "UPDATE events SET payload_schema = ?1 WHERE sequence = 1",
                [EVENT_PAYLOAD_SCHEMA_V2],
            )
            .expect("fixture marker changes to v2");

        assert!(matches!(
            log.decoded_events(),
            Err(SqliteEventLogError::Codec(reason)) if reason.contains("requires schema v3")
        ));
    }

    /// A v3 marker cannot masquerade strong localization evidence introduced by codec v4.
    #[test]
    fn event_decoder_rejects_strong_evidence_under_v3_marker() {
        let directory = tempdir().expect("temporary directory should exist");
        let path = directory.path().join("events-evidence-v3.sqlite3");
        let correlation = CorrelationId::new("evidence-version").expect("correlation valid");
        let mut log = SqliteEventLog::open(&path).expect("event log opens");
        log.append(
            TimestampMs::new(20),
            &correlation,
            None,
            localization_evidence_payload(),
        );
        log.connection
            .lock()
            .expect("event connection lock is available")
            .execute(
                "UPDATE events SET payload_schema = ?1 WHERE sequence = 1",
                [EVENT_PAYLOAD_SCHEMA_V3],
            )
            .expect("fixture marker changes to v3");

        assert!(matches!(
            log.decoded_events(),
            Err(SqliteEventLogError::Codec(reason)) if reason.contains("requires schema v4")
        ));
    }

    /// A v4 marker cannot masquerade relation evidence introduced by codec v5.
    #[test]
    fn event_decoder_rejects_relation_payload_under_v4_marker() {
        let directory = tempdir().expect("temporary directory should exist");
        let path = directory.path().join("events-relation-v4.sqlite3");
        let correlation = CorrelationId::new("relation-version").expect("correlation valid");
        let mut log = SqliteEventLog::open(&path).expect("event log opens");
        log.append(
            TimestampMs::new(30),
            &correlation,
            None,
            execution_relation_payload(),
        );
        log.connection
            .lock()
            .expect("event connection lock is available")
            .execute(
                "UPDATE events SET payload_schema = ?1 WHERE sequence = 1",
                [EVENT_PAYLOAD_SCHEMA_V4],
            )
            .expect("fixture marker changes to v4");

        assert!(matches!(
            log.decoded_events(),
            Err(SqliteEventLogError::Codec(reason)) if reason.contains("requires schema v5")
        ));
    }

    /// Append sequence, rather than lexical event identity, defines stable order and paging.
    #[test]
    fn event_sequence_orders_double_digit_ids_and_pages() {
        let directory = tempdir().expect("temporary directory should exist");
        let path = directory.path().join("events.sqlite3");
        let correlation = CorrelationId::new("sequence-test").expect("correlation valid");
        let mut log = SqliteEventLog::open(&path).expect("event log opens");
        for _ in 0..12 {
            log.append(
                TimestampMs::new(10),
                &correlation,
                None,
                EventPayload::ExecutionGroupBlocked {
                    group_id: domain::ExecutionGroupId::new("group-a").expect("id valid"),
                    task_ref: domain::TaskRef::new(
                        domain::MissionId::new("mission-a").expect("id valid"),
                        domain::TaskId::new("task-a").expect("id valid"),
                    ),
                    reason: "test".to_string(),
                },
            );
        }
        let events = log.events().expect("events are readable");
        assert_eq!(events[9].event_id, "event-10");
        assert_eq!(events[10].event_id, "event-11");
        let page = log.events_page(Some(10), 2).expect("page is readable");
        assert_eq!(
            page.iter().map(|event| event.sequence).collect::<Vec<_>>(),
            [11, 12]
        );
    }

    /// The schema migration preserves legacy rows while assigning replay sequence values.
    #[test]
    fn legacy_debug_payload_schema_migrates() {
        let directory = tempdir().expect("temporary directory should exist");
        let path = directory.path().join("legacy.sqlite3");
        let connection = rusqlite::Connection::open(&path).expect("legacy database opens");
        connection
            .execute_batch(
                "CREATE TABLE events (
                    event_id TEXT PRIMARY KEY NOT NULL,
                    timestamp_ms INTEGER NOT NULL,
                    correlation_id TEXT NOT NULL,
                    causation_id TEXT,
                    payload_schema TEXT NOT NULL,
                    payload_debug TEXT NOT NULL
                );
                INSERT INTO events VALUES ('event-1', 10, 'legacy', NULL,
                    'domain.EventPayload.debug/v0', 'legacy-payload');",
            )
            .expect("legacy schema is created");
        drop(connection);
        let log = SqliteEventLog::open(&path).expect("legacy schema migrates");
        let events = log.events().expect("migrated events are readable");
        assert_eq!(events[0].sequence, 1);
        assert_eq!(events[0].payload_json, "legacy-payload");
    }

    /// A rolled-back event batch leaves no rows and reuses the uncommitted sequence.
    #[test]
    fn event_batch_rolls_back_rows_and_sequence() {
        let directory = tempdir().expect("temporary directory should exist");
        let path = directory.path().join("batch.sqlite3");
        let correlation = CorrelationId::new("batch-test").expect("correlation valid");
        let mut log = SqliteEventLog::open(&path).expect("event log opens");
        log.begin_batch().expect("batch begins");
        log.append(
            TimestampMs::new(10),
            &correlation,
            None,
            EventPayload::ExecutionGroupBlocked {
                group_id: domain::ExecutionGroupId::new("group-a").expect("id valid"),
                task_ref: domain::TaskRef::new(
                    domain::MissionId::new("mission-a").expect("id valid"),
                    domain::TaskId::new("task-a").expect("id valid"),
                ),
                reason: "rollback".to_string(),
            },
        );
        log.rollback_batch().expect("batch rolls back");
        assert!(log.is_empty().expect("event log is readable"));
        log.append(
            TimestampMs::new(20),
            &correlation,
            None,
            EventPayload::ExecutionGroupBlocked {
                group_id: domain::ExecutionGroupId::new("group-a").expect("id valid"),
                task_ref: domain::TaskRef::new(
                    domain::MissionId::new("mission-a").expect("id valid"),
                    domain::TaskId::new("task-a").expect("id valid"),
                ),
                reason: "committed".to_string(),
            },
        );
        let events = log.events().expect("events are readable");
        assert_eq!(events[0].sequence, 1);
        assert_eq!(events[0].event_id, "event-1");
    }

    /// A committed event batch makes all appended rows visible together.
    #[test]
    fn event_batch_commits_multiple_events() {
        let directory = tempdir().expect("temporary directory should exist");
        let path = directory.path().join("batch.sqlite3");
        let correlation = CorrelationId::new("batch-test").expect("correlation valid");
        let mut log = SqliteEventLog::open(&path).expect("event log opens");
        log.begin_batch().expect("batch begins");
        assert!(matches!(
            log.events(),
            Err(SqliteEventLogError::Codec(reason)) if reason.contains("still open")
        ));
        for reason in ["first", "second"] {
            log.append(
                TimestampMs::new(10),
                &correlation,
                None,
                EventPayload::ExecutionGroupBlocked {
                    group_id: domain::ExecutionGroupId::new("group-a").expect("id valid"),
                    task_ref: domain::TaskRef::new(
                        domain::MissionId::new("mission-a").expect("id valid"),
                        domain::TaskId::new("task-a").expect("id valid"),
                    ),
                    reason: reason.to_string(),
                },
            );
        }
        log.commit_batch().expect("batch commits");
        assert_eq!(log.len().expect("event log is readable"), 2);
    }

    /// Checkpoint data commits with its event sequence and survives reopening.
    #[test]
    fn controller_checkpoint_commits_with_event_batch() {
        let directory = tempdir().expect("temporary directory should exist");
        let path = directory.path().join("checkpoint.sqlite3");
        let correlation = CorrelationId::new("checkpoint-test").expect("correlation valid");
        let mut log = SqliteEventLog::open(&path).expect("event log opens");
        log.begin_batch().expect("batch begins");
        log.append(
            TimestampMs::new(10),
            &correlation,
            None,
            EventPayload::ExecutionGroupBlocked {
                group_id: domain::ExecutionGroupId::new("group-a").expect("id valid"),
                task_ref: domain::TaskRef::new(
                    domain::MissionId::new("mission-a").expect("id valid"),
                    domain::TaskId::new("task-a").expect("id valid"),
                ),
                reason: "checkpoint".to_string(),
            },
        );
        log.save_checkpoint("checkpoint/v1", r#"{"state":"ready"}"#)
            .expect("checkpoint saves");
        log.commit_batch().expect("batch commits");
        drop(log);

        let reopened = SqliteEventLog::open(&path).expect("event log reopens");
        let checkpoint = reopened
            .load_checkpoint()
            .expect("checkpoint is readable")
            .expect("checkpoint exists");
        assert_eq!(checkpoint.event_sequence, 1);
        assert_eq!(checkpoint.schema, "checkpoint/v1");
        assert_eq!(checkpoint.checkpoint_json, r#"{"state":"ready"}"#);
        assert_eq!(reopened.latest_sequence().expect("sequence readable"), 1);
    }

    /// Rolling back a batch also rolls back its checkpoint replacement.
    #[test]
    fn controller_checkpoint_rolls_back_with_event_batch() {
        let directory = tempdir().expect("temporary directory should exist");
        let path = directory.path().join("checkpoint.sqlite3");
        let log = SqliteEventLog::open(&path).expect("event log opens");
        log.begin_batch().expect("batch begins");
        log.save_checkpoint("checkpoint/v1", "uncommitted")
            .expect("checkpoint saves in transaction");
        log.rollback_batch().expect("batch rolls back");
        assert!(
            log.load_checkpoint()
                .expect("checkpoint query succeeds")
                .is_none()
        );
    }
}
