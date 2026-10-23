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
use sha2::{Digest as _, Sha256};
use std::collections::BTreeSet;
use std::path::Path;
use std::sync::{Arc, Mutex};

#[path = "controller_storage.rs"]
mod controller_storage;
pub use controller_storage::ControllerStorageFormat;
#[path = "checkpoint_store.rs"]
mod checkpoint_store;
use checkpoint_store::StoredCheckpoint;
#[path = "application_replication.rs"]
mod application_replication;
#[path = "replica_recovery.rs"]
mod replica_recovery;
pub use replica_recovery::{
    CompleteReplicaState, REPLICA_RECOVERY_PROJECTION_SCHEMA, ReplicaArchiveAbort,
    ReplicaArchiveManifest, ReplicaArchiveOperation, ReplicaRecoveryProjection,
};
#[path = "archive.rs"]
mod archive;
#[path = "archive_abort.rs"]
mod archive_abort;
#[path = "archive_operation.rs"]
mod archive_operation;
pub use archive_abort::{
    ArchiveAbortIntent, ArchiveAbortReceipt, ArchiveAbortRequest, ArchiveAbortSink,
    ArchiveCopierFence, ArchiveLeaseIdentity,
};
#[path = "authority_journal.rs"]
mod authority_journal;
#[path = "catalog_archive.rs"]
mod catalog_archive;
pub use archive_operation::{ArchiveContent, ArchiveIntent, ArchiveRequest};
#[path = "timer_transaction.rs"]
mod timer_transaction;
#[path = "write_availability.rs"]
mod write_availability;
pub use application_replication::{
    APPLICATION_COMMIT_SCHEMA, ApplicationBatchReplicator, ApplicationCommitBatch,
    checkpoint_identity, validate_persisted_event,
};
pub use archive::{
    ArchiveManifest, ArchiveSink, FileArchiveSink, MAX_ARCHIVE_BYTES, MAX_ARCHIVE_EVENTS,
};
pub use checkpoint_store::{CheckpointStoragePolicy, CheckpointWriteStats};
pub use write_availability::{
    AuthorityWriteStatus, WriteFailure, WriteRecoveryCommand, WriteRecoveryReceipt,
};

/// Previous JSON payload codec retained for Mission/Execution evidence compatibility.
const EVENT_PAYLOAD_SCHEMA_V2: &str = "domain.EventPayload.json/v2";

/// JSON payload codec including Distributed Spatial Memory evidence variants.
const EVENT_PAYLOAD_SCHEMA_V3: &str = "domain.EventPayload.json/v3";

/// JSON payload codec including strong localization verification evidence.
const EVENT_PAYLOAD_SCHEMA_V4: &str = "domain.EventPayload.json/v4";

/// JSON payload codec including execution coordination relation evidence.
const EVENT_PAYLOAD_SCHEMA_V5: &str = "domain.EventPayload.json/v5";

/// JSON payload codec including source-aware State and generic Memory evidence.
const EVENT_PAYLOAD_SCHEMA_V6: &str = "domain.EventPayload.json/v6";

/// JSON payload codec including provider-qualified generic Memory replica identity.
const EVENT_PAYLOAD_SCHEMA_V7: &str = "domain.EventPayload.json/v7";

/// Previous JSON payload codec including typed execution relation and coupling evidence.
const EVENT_PAYLOAD_SCHEMA_V8: &str = "domain.EventPayload.json/v8";

/// Previous JSON payload codec including identified peer-channel readiness evidence.
const EVENT_PAYLOAD_SCHEMA_V9: &str = "domain.EventPayload.json/v9";

/// Previous JSON payload codec including Control scheduling reservation evidence.
const EVENT_PAYLOAD_SCHEMA_V10: &str = "domain.EventPayload.json/v10";

/// Current JSON payload codec distinguishing Task execution completion from satisfaction.
const EVENT_PAYLOAD_SCHEMA_V11: &str = "domain.EventPayload.json/v11";

/// Current JSON payload codec including attributed final verifier verdicts.
const EVENT_PAYLOAD_SCHEMA_V12: &str = "domain.EventPayload.json/v12";

/// Event codec including exact-session continuous liveness acceptance, distinct from health.
const EVENT_PAYLOAD_SCHEMA_V13: &str = "domain.EventPayload.json/v13";
/// Event codec including Mission execution isolate/resume dispositions.
const EVENT_PAYLOAD_SCHEMA_V14: &str = "domain.EventPayload.json/v14";
/// Codec including exact-Task production runtime contract rejection evidence.
const EVENT_PAYLOAD_SCHEMA_V15: &str = "domain.EventPayload.json/v15";
/// Codec including operator/deployment Mission priority decisions.
const EVENT_PAYLOAD_SCHEMA_V16: &str = "domain.EventPayload.json/v16";
/// Codec including explicit generation-bound future planning preemption.
const EVENT_PAYLOAD_SCHEMA_V17: &str = "domain.EventPayload.json/v17";
/// Codec including explicit operator acknowledgement of a Mission crash-loop quarantine.
const EVENT_PAYLOAD_SCHEMA_V18: &str = "domain.EventPayload.json/v18";
/// Codec fencing bounded rolling-window failure and quarantine-review evidence.
const EVENT_PAYLOAD_SCHEMA_V19: &str = "domain.EventPayload.json/v19";
/// Codec distinguishing semantic Task satisfaction rejection from local execution failure.
const EVENT_PAYLOAD_SCHEMA_V20: &str = "domain.EventPayload.json/v20";
/// Codec retaining explicit authority consumer policy and complete original State evidence.
const EVENT_PAYLOAD_SCHEMA_V21: &str = "domain.EventPayload.json/v21";
/// Codec retaining explicit original-provider Memory command and query admission evidence.
const EVENT_PAYLOAD_SCHEMA_V22: &str = "domain.EventPayload.json/v22";
/// Codec retaining durable actual-byte upload admission, preparation and ownership receipts.
const EVENT_PAYLOAD_SCHEMA_V23: &str = "domain.EventPayload.json/v23";
/// Codec fencing exact registered producer provenance in typed-map evidence.
const EVENT_PAYLOAD_SCHEMA_V24: &str = "domain.EventPayload.json/v24";
/// Codec retaining complete stopped-Group handoff lineage and application transitions.
/// The Controller wrapper separately fences zero-event authority revisions.
const EVENT_PAYLOAD_SCHEMA_V27: &str = "domain.EventPayload.json/v27";
/// Codec retaining an immutable accepted runtime plan revision and original satisfied Task lineage.
const EVENT_PAYLOAD_SCHEMA_V28: &str = "domain.EventPayload.json/v28";
/// Codec retaining original Artifact Catalog/lineage content ownership and irreversible retirement.
const EVENT_PAYLOAD_SCHEMA_V29: &str = "domain.EventPayload.json/v29";
/// Explicit original legacy-Catalog adoption with complete current administrative evidence.
const EVENT_PAYLOAD_SCHEMA_V30: &str = "domain.EventPayload.json/v30";
/// Explicit independent completed-upload retirement authority, never a changed upload phase.
const EVENT_PAYLOAD_SCHEMA_V31: &str = "domain.EventPayload.json/v31";
/// Codec including independent original-revoke observation evidence.
const EVENT_PAYLOAD_SCHEMA_V26: &str = "domain.EventPayload.json/v26";
/// Complete provider-qualified typed-map byte-import evidence.
const EVENT_PAYLOAD_SCHEMA_V25: &str = "domain.EventPayload.json/v25";

/// One event row retained by the durable evidence store.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
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
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct PersistedCheckpoint {
    /// Event sequence represented by this projection.
    pub event_sequence: u64,
    /// Versioned checkpoint schema marker.
    pub schema: String,
    /// Serialized complete controller projection.
    pub checkpoint_json: String,
}

/// One idempotent Control authority mutation recorded beside its checkpoint.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct ControlCommandJournalEntry {
    /// Stable caller-provided mutation identity.
    pub command_id: String,
    /// Immediate causal command or event identity.
    pub causation_id: Option<String>,
    /// Monotonic authority revision represented by this mutation.
    pub authority_revision: u64,
    /// Controller epoch that issued the mutation, when a replicated profile is active.
    pub controller_epoch: Option<String>,
    /// Accepted MissionPlan digest, when the mutation is plan-bound.
    pub plan_digest: Option<String>,
    /// Canonical digest of the command input.
    pub input_digest: String,
    /// Resource/ownership digest before the mutation, when available.
    pub ownership_digest: Option<String>,
    /// Canonical digest of the resulting authority snapshot.
    pub resulting_state_digest: String,
    /// Event sequence represented by the accompanying checkpoint.
    pub checkpoint_event_sequence: u64,
    /// Checkpoint schema persisted with the mutation.
    pub checkpoint_schema: String,
}

impl ControlCommandJournalEntry {
    /// Build a conservative checkpoint-bound journal entry for legacy save callers.
    fn checkpoint(schema: &str, checkpoint_json: &str, event_sequence: u64) -> Self {
        let digest = format!("sha256:{:x}", Sha256::digest(checkpoint_json.as_bytes()));
        Self {
            command_id: format!("checkpoint:{event_sequence}:{digest}"),
            causation_id: None,
            authority_revision: event_sequence,
            controller_epoch: None,
            plan_digest: None,
            input_digest: digest.clone(),
            ownership_digest: None,
            resulting_state_digest: digest,
            checkpoint_event_sequence: event_sequence,
            checkpoint_schema: schema.to_string(),
        }
    }
}

/// Failures returned by the SQLite evidence store.
#[derive(Debug)]
pub enum SqliteEventLogError {
    /// Explicit maintenance policy rejected a cutoff before any rows were removed.
    ArchivePolicy(String),
    /// The durable archive sink could not publish or verify the exact immutable segment.
    ArchiveSink(String),
    /// Required replica epoch/quorum acceptance was unavailable or rejected this batch.
    Replication(String),
    /// The application latched a prior write failure and requires explicit recovery.
    WriteDisabled(String),
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
            Self::ArchivePolicy(error) => {
                write!(formatter, "event archive policy rejected: {error}")
            }
            Self::ArchiveSink(error) => write!(formatter, "event archive sink failed: {error}"),
            Self::Replication(error) => write!(
                formatter,
                "controller replication acceptance failed: {error}"
            ),
            Self::WriteDisabled(error) => write!(formatter, "authority writes disabled: {error}"),
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
    /// Opt-in application availability policy, shared by every clone of the actual store.
    write_availability: Arc<Mutex<write_availability::WriteAvailability>>,
    /// Optional quorum acceptance port, installed before any Controller authority uses the store.
    replicator: Option<Arc<dyn ApplicationBatchReplicator>>,
    /// Optional bounded checkpoint-write observations; failure never changes storage authority.
    diagnostics: ports::DiagnosticHandle,
    /// Startup-owned storage representation, independent of application authority.
    checkpoint_policy: CheckpointStoragePolicy,
    /// Last complete committed snapshot; shared copies never duplicate its byte buffer.
    checkpoint_cache: Arc<Mutex<Option<StoredCheckpoint>>>,
    /// Original locally committed journal high-water, including revisions with no domain events.
    committed_authority_revision: Arc<Mutex<u64>>,
    /// Candidate cache installed only after the existing SQLite transaction commits.
    pending_checkpoint_cache: Arc<Mutex<Option<StoredCheckpoint>>>,
    /// Serialized access to one SQLite connection.
    connection: Arc<Mutex<controller_storage::ManagedConnection>>,
    /// Actual private mutation target; the legacy events view remains read-only in v1.
    events_table: &'static str,
    /// Process-local event counter used for deterministic event identities.
    next_event_number: Arc<Mutex<u64>>,
    /// Start sequence retained while an application-level event batch is open.
    batch_start_number: Arc<Mutex<Option<u64>>>,
    /// Same actual SQLite connection's write count immediately after the original BEGIN IMMEDIATE.
    batch_start_changes: Arc<Mutex<Option<u64>>>,
    /// Actual connection write/data-version witness from the last complete checkpoint Commit only.
    committed_sql_witness: Arc<Mutex<Option<(u64, u64)>>>,
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
    /// Runs one already-scoped application transition in a nested event/sequence savepoint.
    ///
    /// The caller separately owns its private memory candidate and discards it on error. This
    /// method neither decides error scope nor commits the enclosing application batch. It must
    /// be serialized by the existing writer gate and must not be nested recursively.
    pub fn with_transition_savepoint<T, E>(
        &self,
        operation: impl FnOnce() -> Result<T, E>,
    ) -> Result<Result<T, E>, SqliteEventLogError> {
        self.with_savepoint("roboguide_mission_transition", operation)
    }

    /// Rolls back one input's events/sequence independently of its nested Mission transitions.
    pub fn with_input_savepoint<T, E>(
        &self,
        operation: impl FnOnce() -> Result<T, E>,
    ) -> Result<Result<T, E>, SqliteEventLogError> {
        self.with_savepoint("roboguide_input_transition", operation)
    }

    /// Uses one internal static savepoint name; callers serialize distinct nonrecursive scopes.
    fn with_savepoint<T, E>(
        &self,
        name: &str,
        operation: impl FnOnce() -> Result<T, E>,
    ) -> Result<Result<T, E>, SqliteEventLogError> {
        let batch = self
            .batch_start_number
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        if batch.is_none() {
            return Err(SqliteEventLogError::Codec(
                "transition savepoint requires an open application batch".into(),
            ));
        }
        drop(batch);
        let pending_checkpoint = self
            .pending_checkpoint_cache
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?
            .clone();
        let start = *self
            .next_event_number
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        self.connection
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?
            .execute_batch(&format!("SAVEPOINT {name}"))?;
        let result = operation();
        if result.is_err() {
            self.connection
                .lock()
                .map_err(|_| SqliteEventLogError::LockPoisoned)?
                .execute_batch(&format!("ROLLBACK TO {name}; RELEASE {name}"))?;
            *self
                .next_event_number
                .lock()
                .map_err(|_| SqliteEventLogError::LockPoisoned)? = start;
            *self
                .pending_checkpoint_cache
                .lock()
                .map_err(|_| SqliteEventLogError::LockPoisoned)? = pending_checkpoint;
        } else {
            self.connection
                .lock()
                .map_err(|_| SqliteEventLogError::LockPoisoned)?
                .execute_batch(&format!("RELEASE {name}"))?;
        }
        Ok(result)
    }
    /// Opens a WAL database and creates the immutable evidence schema if needed.
    pub fn open(path: impl AsRef<Path>) -> Result<Self, SqliteEventLogError> {
        Self::open_with_checkpoint_policy(path, CheckpointStoragePolicy::Full)
    }

    /// Opens the same atomic evidence store with an explicit bounded checkpoint representation.
    pub fn open_with_checkpoint_policy(
        path: impl AsRef<Path>,
        checkpoint_policy: CheckpointStoragePolicy,
    ) -> Result<Self, SqliteEventLogError> {
        Self::open_inner(path.as_ref(), checkpoint_policy, None)
    }

    /// Opens the runnable Controller with actual path/inode leases retained by every SQL clone.
    /// Explicit v1 upgrades only after native old-connection drain; legacy does not gain a durable fence.
    pub fn open_controller_with_checkpoint_policy(
        path: impl AsRef<Path>,
        checkpoint_policy: CheckpointStoragePolicy,
        format: ControllerStorageFormat,
    ) -> Result<Self, SqliteEventLogError> {
        Self::open_inner(path.as_ref(), checkpoint_policy, Some(format))
    }

    /// Initializes only the selected storage profile, then binds leases to the actual shared connection.
    fn open_inner(
        path: &Path,
        checkpoint_policy: CheckpointStoragePolicy,
        format: Option<ControllerStorageFormat>,
    ) -> Result<Self, SqliteEventLogError> {
        let writer = format
            .map(|_| controller_storage::ControllerWriter::acquire(path))
            .transpose()?;
        let actual_path = writer.as_ref().map_or(path, |writer| writer.path());
        let mut connection = Connection::open(actual_path)?;
        if writer.is_some() {
            connection.busy_timeout(std::time::Duration::from_millis(250))?;
        }
        let fenced = controller_storage::is_fenced(&connection)?;
        if fenced && format != Some(ControllerStorageFormat::V1) {
            return Err(SqliteEventLogError::Codec(
                "Controller storage v1 requires the actual managed v1 writer".into(),
            ));
        }
        if fenced {
            controller_storage::validate(&connection)?;
        }
        if let Some(writer) = &writer {
            writer.verify()?;
            if !fenced {
                controller_storage::drain(&mut connection)?;
            }
            writer.verify()?;
        }
        connection.pragma_update(None, "synchronous", "FULL")?;
        if fenced {
            controller_storage::validate(&connection)?;
        } else {
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
            );
            CREATE TABLE IF NOT EXISTS control_command_journal (
                command_id TEXT PRIMARY KEY NOT NULL,
                causation_id TEXT,
                authority_revision INTEGER NOT NULL,
                controller_epoch TEXT,
                plan_digest TEXT,
                input_digest TEXT NOT NULL,
                ownership_digest TEXT,
                resulting_state_digest TEXT NOT NULL,
                checkpoint_event_sequence INTEGER NOT NULL,
                checkpoint_schema TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_command_authority_revision ON control_command_journal(authority_revision);
            CREATE TABLE IF NOT EXISTS service_access_audit (
                sequence INTEGER PRIMARY KEY, service TEXT NOT NULL, principal TEXT NOT NULL,
                method TEXT NOT NULL, path TEXT NOT NULL, decision TEXT NOT NULL
            );",
        )?;
            checkpoint_store::initialize(&connection)?;
            migrate_event_schema(&connection)?;
        }
        let checkpoint_cache = checkpoint_store::read(&connection)?;
        if !fenced {
            connection.execute_batch(
            "CREATE INDEX IF NOT EXISTS idx_artifact_reference_selector ON events (
                json_extract(payload_json, '$.ArtifactCatalogReferenceTransition.record.selector.memory_id'),
                json_extract(payload_json, '$.ArtifactCatalogReferenceTransition.record.selector.revision_id'),
                sequence
             ) WHERE payload_schema = 'domain.EventPayload.json/v29';",
        )?;
            if format == Some(ControllerStorageFormat::V1) {
                controller_storage::upgrade(&mut connection)?;
            }
        }
        if let Some(writer) = &writer {
            writer.verify()?;
        }
        connection.pragma_update(None, "journal_mode", "WAL")?;
        connection.busy_timeout(std::time::Duration::from_secs(5))?;
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
        let checkpoint_sequence = checkpoint_cache
            .as_ref()
            .map_or(0, |stored| stored.snapshot.event_sequence);
        let next_event_number = metadata_event_number
            .max(max_sequence)
            .max(checkpoint_sequence);
        let committed_authority_revision = authority_journal::revision(&connection)?;
        Ok(Self {
            write_availability: Arc::new(Mutex::new(Default::default())),
            replicator: None,
            diagnostics: ports::DiagnosticHandle::default(),
            checkpoint_policy,
            checkpoint_cache: Arc::new(Mutex::new(checkpoint_cache)),
            committed_authority_revision: Arc::new(Mutex::new(committed_authority_revision)),
            pending_checkpoint_cache: Arc::new(Mutex::new(None)),
            connection: Arc::new(Mutex::new(controller_storage::ManagedConnection::new(
                connection, writer,
            ))),
            events_table: if format == Some(ControllerStorageFormat::V1) {
                "controller_v1_events"
            } else {
                "events"
            },
            next_event_number: Arc::new(Mutex::new(next_event_number)),
            batch_start_number: Arc::new(Mutex::new(None)),
            batch_start_changes: Arc::new(Mutex::new(None)),
            committed_sql_witness: Arc::new(Mutex::new(None)),
            last_error: Arc::new(Mutex::new(None)),
        })
    }

    /// Installs checkpoint-write observations before the composition shares this store with authorities.
    pub fn with_diagnostics(mut self, diagnostics: ports::DiagnosticHandle) -> Self {
        self.diagnostics = diagnostics;
        self
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
        let max_sequence: u64 =
            connection.query_row("SELECT COALESCE(MAX(sequence), 0) FROM events", [], |row| {
                row.get(0)
            })?;
        let checkpoint_sequence =
            checkpoint_store::read(&connection)?.map_or(0, |stored| stored.snapshot.event_sequence);
        Ok(max_sequence.max(checkpoint_sequence))
    }

    /// Returns the durable receive-time high-water, including events removed by verified archive maintenance.
    pub fn latest_timestamp(&self) -> Result<TimestampMs, SqliteEventLogError> {
        self.ensure_no_open_batch()?;
        let connection = self
            .connection
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        let timestamp = connection.query_row(
            "SELECT COALESCE(MAX(timestamp_ms), 0) FROM events",
            [],
            |row| row.get::<_, u64>(0),
        )?;
        Ok(TimestampMs::new(
            timestamp.max(archive::read_archive_timestamp_floor(&connection)?),
        ))
    }

    /// Loads the last committed controller projection checkpoint, if present.
    pub fn load_checkpoint(&self) -> Result<Option<PersistedCheckpoint>, SqliteEventLogError> {
        self.ensure_no_open_batch()?;
        let connection = self
            .connection
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        let checkpoint =
            checkpoint_store::read(&connection)?.map(|stored| (*stored.snapshot).clone());
        authority_journal::validate(&connection, checkpoint.as_ref())?;
        Ok(checkpoint)
    }

    /// Reconstructs an existing checkpoint through a readonly connection without changing the database.
    pub fn read_checkpoint(
        path: impl AsRef<Path>,
    ) -> Result<Option<PersistedCheckpoint>, SqliteEventLogError> {
        let connection =
            Connection::open_with_flags(path, rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY)?;
        checkpoint_store::read(&connection)
            .map(|stored| stored.map(|stored| (*stored.snapshot).clone()))
    }

    /// Observes actual encoded write bytes; this metadata never grants persistence acceptance.
    pub fn checkpoint_write_stats(&self) -> Result<CheckpointWriteStats, SqliteEventLogError> {
        if let Some(pending) = self
            .pending_checkpoint_cache
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?
            .as_ref()
        {
            return Ok(pending.write_stats);
        }
        Ok(self
            .checkpoint_cache
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?
            .as_ref()
            .map_or(CheckpointWriteStats::default(), |checkpoint| {
                checkpoint.write_stats
            }))
    }

    /// Returns the event sequence represented by the next checkpoint in the open batch.
    pub fn current_batch_event_sequence(&self) -> Result<u64, SqliteEventLogError> {
        if self
            .batch_start_number
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?
            .is_none()
        {
            return Err(SqliteEventLogError::Codec(
                "current batch event sequence requires an open event batch".to_string(),
            ));
        }
        self.next_event_number
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)
            .map(|sequence| *sequence)
    }

    /// Saves a controller projection inside the currently open event batch.
    pub fn save_checkpoint(
        &self,
        schema: &str,
        checkpoint_json: &str,
    ) -> Result<(), SqliteEventLogError> {
        let event_sequence = *self
            .next_event_number
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        self.save_checkpoint_with_command(
            schema,
            checkpoint_json,
            ControlCommandJournalEntry::checkpoint(schema, checkpoint_json, event_sequence),
        )
    }

    /// Saves a checkpoint and an idempotent Control authority journal entry in one batch.
    pub fn save_checkpoint_with_command(
        &self,
        schema: &str,
        checkpoint_json: &str,
        command: ControlCommandJournalEntry,
    ) -> Result<(), SqliteEventLogError> {
        self.require_writes_enabled()?;
        self.observe_write_result(self.save_checkpoint_command_inner(
            schema,
            checkpoint_json,
            command,
        ))
    }

    /// Writes one already-admitted checkpoint candidate inside the original application transaction.
    fn save_checkpoint_command_inner(
        &self,
        schema: &str,
        checkpoint_json: &str,
        mut command: ControlCommandJournalEntry,
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
        if command.checkpoint_event_sequence != event_sequence {
            return Err(SqliteEventLogError::Codec(
                "Control command journal sequence does not match checkpoint".to_string(),
            ));
        }
        if command.checkpoint_schema != schema {
            return Err(SqliteEventLogError::Codec(
                "Control command journal schema does not match checkpoint".to_string(),
            ));
        }
        if command.command_id.trim().is_empty() || command.input_digest.trim().is_empty() {
            return Err(SqliteEventLogError::Codec(
                "Control command journal identity and input digest must be nonblank".to_string(),
            ));
        }
        let previous = self
            .pending_checkpoint_cache
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?
            .clone()
            .or(self
                .checkpoint_cache
                .lock()
                .map_err(|_| SqliteEventLogError::LockPoisoned)?
                .clone());
        let connection = self
            .connection
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        command.authority_revision =
            match authority_journal::existing(&connection, &command.command_id)? {
                Some(prior) => prior.authority_revision,
                None => authority_journal::revision(&connection)?
                    .checked_add(1)
                    .ok_or_else(|| {
                        SqliteEventLogError::Codec("Control authority revision exhausted".into())
                    })?,
            };
        command.controller_epoch = self
            .replicator
            .as_ref()
            .and_then(|port| port.controller_epoch())
            .or_else(|| Some("standalone".into()));
        command.validate_checkpoint(&PersistedCheckpoint {
            event_sequence,
            schema: schema.into(),
            checkpoint_json: checkpoint_json.into(),
        })?;
        authority_journal::insert(&connection, &command)?;
        let candidate = checkpoint_store::write(
            &connection,
            previous.as_ref(),
            self.checkpoint_policy,
            schema,
            checkpoint_json,
            event_sequence,
        )?;
        self.diagnostics.value(
            ports::DiagnosticKind::Other,
            ports::DiagnosticStage::CheckpointWriteBytes,
            candidate.write_stats.encoded_bytes,
            None,
        );
        self.diagnostics.value(
            ports::DiagnosticKind::Other,
            ports::DiagnosticStage::CheckpointDelta,
            u64::from(candidate.write_stats.delta),
            None,
        );
        *self
            .pending_checkpoint_cache
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)? = Some(candidate);
        Ok(())
    }

    /// Returns Control authority journal entries in authority revision order.
    pub fn control_command_journal(
        &self,
    ) -> Result<Vec<ControlCommandJournalEntry>, SqliteEventLogError> {
        self.ensure_no_open_batch()?;
        let connection = self
            .connection
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        let mut statement = connection.prepare(
            "SELECT command_id, causation_id, authority_revision, controller_epoch, plan_digest,
                    input_digest, ownership_digest, resulting_state_digest,
                    checkpoint_event_sequence, checkpoint_schema
             FROM control_command_journal ORDER BY authority_revision, command_id",
        )?;
        let rows = statement.query_map([], |row| {
            Ok(ControlCommandJournalEntry {
                command_id: row.get(0)?,
                causation_id: row.get(1)?,
                authority_revision: row.get(2)?,
                controller_epoch: row.get(3)?,
                plan_digest: row.get(4)?,
                input_digest: row.get(5)?,
                ownership_digest: row.get(6)?,
                resulting_state_digest: row.get(7)?,
                checkpoint_event_sequence: row.get(8)?,
                checkpoint_schema: row.get(9)?,
            })
        })?;
        rows.collect::<Result<Vec<_>, _>>().map_err(Into::into)
    }

    /// Reads aggregate cardinality without materializing historical command documents.
    pub fn control_command_count(&self) -> Result<u64, SqliteEventLogError> {
        self.ensure_no_open_batch()?;
        let connection = self
            .connection
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        Ok(
            connection.query_row("SELECT COUNT(*) FROM control_command_journal", [], |row| {
                row.get(0)
            })?,
        )
    }

    /// Durably records bounded service admission metadata; bodies and secrets never enter this log.
    pub fn record_service_access(
        &self,
        service: &str,
        principal: &str,
        method: &str,
        path: &str,
        decision: &str,
    ) -> Result<(), SqliteEventLogError> {
        self.require_writes_enabled()?;
        self.observe_write_result(
            self.record_service_access_inner(service, principal, method, path, decision),
        )
    }

    /// Appends admission evidence only while the embedding application's actual writer is held.
    fn record_service_access_inner(
        &self,
        service: &str,
        principal: &str,
        method: &str,
        path: &str,
        decision: &str,
    ) -> Result<(), SqliteEventLogError> {
        self.require_read_authority()?;
        self.ensure_no_open_batch()?;
        if [service, principal, method, path, decision]
            .iter()
            .any(|field| field.is_empty() || field.len() > 1024)
        {
            return Err(SqliteEventLogError::Codec(
                "invalid service audit metadata".into(),
            ));
        }
        let connection = self
            .connection
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        connection.execute("INSERT INTO service_access_audit(service,principal,method,path,decision) VALUES (?1,?2,?3,?4,?5)", params![service,principal,method,path,decision])?;
        Ok(())
    }

    /// Verifies local authority coherence and the current quorum/epoch before advertising writes.
    pub fn readiness(&self) -> Result<(), SqliteEventLogError> {
        self.require_writes_enabled()?;
        self.ensure_no_open_batch()?;
        if let Some(replicator) = &self.replicator {
            replicator
                .assert_write_authority()
                .map_err(SqliteEventLogError::Replication)?;
        }
        self.load_checkpoint()?;
        let connection = self
            .connection
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        connection.execute_batch("BEGIN IMMEDIATE; ROLLBACK")?;
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
        self.require_writes_enabled()?;
        self.observe_write_result(self.begin_batch_inner())
    }

    /// Opens only the real application SQL transaction after the availability check.
    fn begin_batch_inner(&self) -> Result<(), SqliteEventLogError> {
        if let Some(replicator) = &self.replicator {
            replicator
                .assert_write_authority()
                .map_err(SqliteEventLogError::Replication)?;
        }
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
        *self
            .batch_start_changes
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)? = Some(connection.total_changes());
        *batch_start = Some(*counter);
        Ok(())
    }

    /// Commits the current application-level event batch.
    pub fn commit_batch(&self) -> Result<(), SqliteEventLogError> {
        self.require_writes_enabled()?;
        self.observe_write_result(self.commit_batch_inner())
    }

    /// Completes replica acceptance and local Commit without publishing an uncommitted cache.
    fn commit_batch_inner(&self) -> Result<(), SqliteEventLogError> {
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
        if let Some(replicator) = &self.replicator {
            let target = self
                .pending_checkpoint_cache
                .lock()
                .map_err(|_| SqliteEventLogError::LockPoisoned)?
                .as_ref()
                .map(|stored| (*stored.snapshot).clone())
                .ok_or_else(|| {
                    SqliteEventLogError::Replication(
                        "replicated batch lacks its complete checkpoint".into(),
                    )
                })?;
            let previous = self
                .checkpoint_cache
                .lock()
                .map_err(|_| SqliteEventLogError::LockPoisoned)?
                .clone();
            let mut statement = connection.prepare(
                "SELECT sequence, event_id, timestamp_ms, correlation_id, causation_id, payload_schema, payload_json
                 FROM events WHERE sequence > ?1 ORDER BY sequence",
            )?;
            let events = statement
                .query_map([batch_start.expect("batch checked above")], event_from_row)?
                .collect::<Result<Vec<_>, _>>()?;
            let committed_revision = connection
                .query_row(
                    "SELECT value FROM event_log_metadata WHERE key='journal_committed_revision'",
                    [],
                    |row| row.get::<_, String>(0),
                )
                .optional()?
                .map(|value| {
                    value
                        .parse::<u64>()
                        .map_err(|error| SqliteEventLogError::Codec(error.to_string()))
                })
                .transpose()?
                .unwrap_or(0);
            let commands = authority_journal::since(&connection, committed_revision)?;
            let batch = ApplicationCommitBatch {
                schema: APPLICATION_COMMIT_SCHEMA.into(),
                previous_event_sequence: previous
                    .as_ref()
                    .map_or(0, |stored| stored.snapshot.event_sequence),
                previous_checkpoint_digest: previous
                    .as_ref()
                    .map(|stored| checkpoint_identity(&stored.snapshot)),
                checkpoint: target,
                events,
                commands,
            };
            batch.validate()?;
            if replicator.requires_recovery_projection() {
                let recovery = replica_recovery::export(&connection)?;
                replicator
                    .confirm_recovery_batch(batch, recovery)
                    .map_err(SqliteEventLogError::Replication)?;
            } else {
                replicator
                    .confirm_batch(batch)
                    .map_err(SqliteEventLogError::Replication)?;
            }
        }
        let authority_revision = authority_journal::revision(&connection)?;
        connection.execute("INSERT INTO event_log_metadata(key,value) VALUES ('journal_committed_revision',?1) ON CONFLICT(key) DO UPDATE SET value=excluded.value", [authority_revision.to_string()])?;
        // Sample before releasing the actual SQLite writer, so a later foreign Commit cannot be adopted.
        let sql_witness = (
            connection.total_changes(),
            connection.query_row("PRAGMA data_version", [], |row| row.get::<_, u64>(0))?,
        );
        connection.execute_batch("COMMIT")?;
        *self
            .committed_authority_revision
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)? = authority_revision;
        let complete_checkpoint = if let Some(candidate) = self
            .pending_checkpoint_cache
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?
            .take()
        {
            *self
                .checkpoint_cache
                .lock()
                .map_err(|_| SqliteEventLogError::LockPoisoned)? = Some(candidate);
            true
        } else {
            false
        };
        *self
            .committed_sql_witness
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)? =
            complete_checkpoint.then_some(sql_witness);
        *batch_start = None;
        *self
            .batch_start_changes
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)? = None;
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
        *self
            .pending_checkpoint_cache
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)? = None;
        *batch_start = None;
        *self
            .batch_start_changes
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)? = None;
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
        drop(batch_start);
        if !self
            .connection
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?
            .is_autocommit()
        {
            return Err(SqliteEventLogError::Codec(
                "event store has an untracked transaction; recovery required".into(),
            ));
        }
        Ok(())
    }

    /// Reads one original committed publication head through its maintained exact-selector index.
    /// The caller installs it only after application Commit; open private batches remain invisible.
    pub fn artifact_catalog_reference(
        &self,
        selector: &domain::MemorySelector,
    ) -> Result<Option<domain::ArtifactCatalogReference>, SqliteEventLogError> {
        self.ensure_no_open_batch()?;
        let connection = self
            .connection
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        let mut raw: Option<String> = connection.query_row(
            "SELECT payload_json FROM events
             WHERE payload_schema = 'domain.EventPayload.json/v29'
               AND json_extract(payload_json, '$.ArtifactCatalogReferenceTransition.record.selector.memory_id') = ?1
               AND json_extract(payload_json, '$.ArtifactCatalogReferenceTransition.record.selector.revision_id') = ?2
             ORDER BY sequence DESC LIMIT 1",
            params![selector.memory_id().as_str(), selector.revision_id().as_str()],
            |row| row.get(0),
        ).optional()?;
        if raw.is_none() {
            for event in catalog_archive::read(&connection)?.into_iter().rev() {
                let payload: EventPayload = serde_json::from_str(&event.payload_json)
                    .map_err(|error| SqliteEventLogError::Codec(error.to_string()))?;
                if matches!(&payload,EventPayload::ArtifactCatalogReferenceTransition{record} if &record.selector==selector)
                {
                    raw = Some(event.payload_json);
                    break;
                }
            }
        }
        raw.map(|raw| {
            let payload: EventPayload = serde_json::from_str(&raw)
                .map_err(|error| SqliteEventLogError::Codec(error.to_string()))?;
            validate_payload_schema(EVENT_PAYLOAD_SCHEMA_V29, &payload)?;
            match payload {
                EventPayload::ArtifactCatalogReferenceTransition { record }
                    if &record.selector == selector =>
                {
                    Ok(record)
                }
                _ => Err(SqliteEventLogError::Codec(
                    "original Artifact reference index returned a different purpose".into(),
                )),
            }
        })
        .transpose()
    }
}

/// Parses one supported event payload codec marker before inspecting its JSON body.
fn payload_schema_version(schema: &str) -> Result<u8, SqliteEventLogError> {
    match schema {
        EVENT_PAYLOAD_SCHEMA_V2 => Ok(2),
        EVENT_PAYLOAD_SCHEMA_V3 => Ok(3),
        EVENT_PAYLOAD_SCHEMA_V4 => Ok(4),
        EVENT_PAYLOAD_SCHEMA_V5 => Ok(5),
        EVENT_PAYLOAD_SCHEMA_V6 => Ok(6),
        EVENT_PAYLOAD_SCHEMA_V7 => Ok(7),
        EVENT_PAYLOAD_SCHEMA_V8 => Ok(8),
        EVENT_PAYLOAD_SCHEMA_V9 => Ok(9),
        EVENT_PAYLOAD_SCHEMA_V10 => Ok(10),
        EVENT_PAYLOAD_SCHEMA_V11 => Ok(11),
        EVENT_PAYLOAD_SCHEMA_V12 => Ok(12),
        EVENT_PAYLOAD_SCHEMA_V13 => Ok(13),
        EVENT_PAYLOAD_SCHEMA_V14 => Ok(14),
        EVENT_PAYLOAD_SCHEMA_V15 => Ok(15),
        EVENT_PAYLOAD_SCHEMA_V16 => Ok(16),
        EVENT_PAYLOAD_SCHEMA_V17 => Ok(17),
        EVENT_PAYLOAD_SCHEMA_V18 => Ok(18),
        EVENT_PAYLOAD_SCHEMA_V19 => Ok(19),
        EVENT_PAYLOAD_SCHEMA_V20 => Ok(20),
        EVENT_PAYLOAD_SCHEMA_V21 => Ok(21),
        EVENT_PAYLOAD_SCHEMA_V22 => Ok(22),
        EVENT_PAYLOAD_SCHEMA_V23 => Ok(23),
        EVENT_PAYLOAD_SCHEMA_V24 => Ok(24),
        EVENT_PAYLOAD_SCHEMA_V25 => Ok(25),
        EVENT_PAYLOAD_SCHEMA_V26 => Ok(26),
        EVENT_PAYLOAD_SCHEMA_V27 => Ok(27),
        EVENT_PAYLOAD_SCHEMA_V28 => Ok(28),
        EVENT_PAYLOAD_SCHEMA_V29 => Ok(29),
        EVENT_PAYLOAD_SCHEMA_V30 => Ok(30),
        EVENT_PAYLOAD_SCHEMA_V31 => Ok(31),
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
        EventPayload::ArtifactUploadRetired { receipt } => {
            receipt.validate().map_err(SqliteEventLogError::Codec)?;
            31
        }
        EventPayload::ArtifactCatalogLegacyAdopted { receipt } => {
            receipt.validate().map_err(SqliteEventLogError::Codec)?;
            30
        }
        EventPayload::ArtifactCatalogReferenceTransition { record } => {
            record.validate().map_err(SqliteEventLogError::Codec)?;
            29
        }
        EventPayload::MissionPlanRevisionCommitted { .. }
        | EventPayload::MissionGoalEvidenceAdmitted { .. } => 28,
        EventPayload::MemoryHandoffTransition { .. } => 27,
        EventPayload::MemoryUseReconciliationEvidence { .. } => 26,
        EventPayload::MapMemoryReplicaRecorded { .. } => 25,
        _ if provider_map_payload(payload) => 24,
        EventPayload::ArtifactUploadTransition { .. } => 23,
        EventPayload::MemoryCommandEvidence { .. } => 22,
        EventPayload::StatePolicyDecision { .. }
        | EventPayload::SemanticVerifierEvidenceAdmitted { .. }
        | EventPayload::SemanticVerifierIncident { .. } => 21,
        EventPayload::MissionExecutionIsolated {
            failure_window: Some(_),
            ..
        }
        | EventPayload::MissionExecutionQuarantineCleared {
            failure_window: Some(_),
            ..
        } => 19,
        EventPayload::SchedulingReservationPreempted { .. } => 17,
        EventPayload::MissionExecutionQuarantineCleared { .. } => 18,
        EventPayload::MissionSchedulingPriorityChanged { .. } => 16,
        EventPayload::MissionExecutionContractRejected { .. } => 15,
        EventPayload::MissionExecutionIsolated { .. }
        | EventPayload::MissionExecutionResumed { .. } => 14,
        EventPayload::NodeSessionLivenessAccepted { .. } => 13,
        EventPayload::MemoryArtifactStaged {
            consumer_provider_id,
            ..
        }
        | EventPayload::MemoryArtifactImported {
            consumer_provider_id,
            ..
        }
        | EventPayload::MemoryArtifactRejected {
            consumer_provider_id,
            ..
        } => {
            if consumer_provider_id == domain::LEGACY_MEMORY_CONSUMER_PROVIDER_ID {
                if version >= 7 {
                    return Err(SqliteEventLogError::Codec(
                        "event payload schema v7 requires Memory consumer provider identity"
                            .to_string(),
                    ));
                }
                6
            } else {
                7
            }
        }
        EventPayload::TaskSchedulingDeferred { .. }
        | EventPayload::SchedulingReservationCreated { .. }
        | EventPayload::SchedulingReservationActivated { .. }
        | EventPayload::SchedulingReservationInvalidated { .. }
        | EventPayload::SchedulingReservationReleased { .. } => 10,
        EventPayload::TaskSatisfied { .. } => 11,
        EventPayload::TaskSatisfactionRejected { .. } => 20,
        EventPayload::TaskVerifierVerdictObserved { .. } => 12,
        EventPayload::StateRecordObserved { .. } | EventPayload::MemoryManifestPublished { .. } => {
            6
        }
        EventPayload::ExecutionRelationRegistered {
            relation_type,
            coupling_mode,
            ..
        }
        | EventPayload::ExecutionRelationStateChanged {
            relation_type,
            coupling_mode,
            ..
        }
        | EventPayload::ExecutionRelationReconciliationRequired {
            relation_type,
            coupling_mode,
            ..
        } => {
            if *relation_type == domain::ExecutionRelationType::RequiresActive
                && *coupling_mode == domain::ExecutionCouplingMode::Independent
            {
                5
            } else {
                8
            }
        }
        EventPayload::MapLocalizationEvidenceRecorded { .. } => 4,
        EventPayload::PeerChannelReadinessObserved { .. } => 9,
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
        if let Err(error) = result {
            let _ = self.disable_writes(&error, false);
            if let Ok(mut last_error) = self.last_error.lock() {
                *last_error = Some(error);
            }
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
        self.require_writes_enabled()
            .map_err(|error| error.to_string())?;
        let batch_start = self
            .batch_start_number
            .lock()
            .map_err(|_| "event batch lock is poisoned".to_string())?;
        if self.replicator.is_some() && batch_start.is_none() {
            return Err("replicated events require an application checkpoint transaction".into());
        }
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
        let payload_schema = payload_schema_for_payload(record.payload());
        let insert = |connection: &Connection| {
            connection.execute(
                &format!("INSERT INTO {}
                 (sequence, event_id, timestamp_ms, correlation_id, causation_id, payload_schema, payload_json)
                 VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)", self.events_table),
                params![
                    sequence,
                    record.event_id().as_str(),
                    record.timestamp().as_millis(),
                    record.correlation_id().as_str(),
                    record.causation_id().map(|id| id.as_str()),
                    payload_schema,
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

/// Selects the newest codec marker only for payloads introduced by that codec.
fn payload_schema_for_payload(payload: &EventPayload) -> &'static str {
    match payload {
        EventPayload::ArtifactCatalogLegacyAdopted { .. } => EVENT_PAYLOAD_SCHEMA_V30,
        EventPayload::ArtifactUploadRetired { .. } => EVENT_PAYLOAD_SCHEMA_V31,
        EventPayload::ArtifactCatalogReferenceTransition { .. } => EVENT_PAYLOAD_SCHEMA_V29,
        EventPayload::MissionPlanRevisionCommitted { .. }
        | EventPayload::MissionGoalEvidenceAdmitted { .. } => EVENT_PAYLOAD_SCHEMA_V28,
        EventPayload::MemoryHandoffTransition { .. } => EVENT_PAYLOAD_SCHEMA_V27,
        EventPayload::MemoryUseReconciliationEvidence { .. } => EVENT_PAYLOAD_SCHEMA_V26,
        EventPayload::MapMemoryReplicaRecorded { .. } => EVENT_PAYLOAD_SCHEMA_V25,
        _ if provider_map_payload(payload) => EVENT_PAYLOAD_SCHEMA_V24,
        EventPayload::ArtifactUploadTransition { .. } => EVENT_PAYLOAD_SCHEMA_V23,
        EventPayload::MemoryCommandEvidence { .. } => EVENT_PAYLOAD_SCHEMA_V22,
        EventPayload::StatePolicyDecision { .. }
        | EventPayload::SemanticVerifierEvidenceAdmitted { .. }
        | EventPayload::SemanticVerifierIncident { .. } => EVENT_PAYLOAD_SCHEMA_V21,
        EventPayload::MissionExecutionQuarantineCleared { .. }
        | EventPayload::MissionExecutionIsolated { .. } => EVENT_PAYLOAD_SCHEMA_V19,
        EventPayload::TaskSatisfactionRejected { .. } => EVENT_PAYLOAD_SCHEMA_V20,
        _ => EVENT_PAYLOAD_SCHEMA_V17,
    }
}

/// Identifies manifest-bearing map evidence without upgrading historical v0.1 records.
fn provider_map_payload(payload: &EventPayload) -> bool {
    match payload {
        EventPayload::MapArtifactDeclared { manifest }
        | EventPayload::MapArtifactPublished { manifest }
        | EventPayload::MapArtifactStaged { manifest, .. }
        | EventPayload::MapArtifactImported { manifest, .. } => {
            manifest.producer_provider_id().is_some()
        }
        _ => false,
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
#[path = "event_log_tests.rs"]
mod tests;
