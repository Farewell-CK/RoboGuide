//! Actual Controller writer lifetime and explicitly negotiated durable storage fence.

#[cfg(test)]
#[path = "controller_storage_tests.rs"]
mod tests;

use super::*;
use std::fs::{File, OpenOptions};
use std::ops::{Deref, DerefMut};
use std::os::unix::fs::{MetadataExt, OpenOptionsExt};
use std::path::{Path, PathBuf};

/// Explicit startup storage contract, independent of application and Node authority.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ControllerStorageFormat {
    /// Original SQL layout; dual leases do not supply a persistent older-binary fence.
    Legacy,
    /// Mandatory original tables and exact read-only legacy views fence earlier writers.
    V1,
}

/// Actual SQL connection drops before these original writer descriptors are unlocked.
pub(super) struct ManagedConnection {
    /// All statements borrow this connection under the shared EventLog mutex.
    connection: Connection,
    /// Field order retains both leases through the actual SQLite close.
    _writer: Option<ControllerWriter>,
}

impl ManagedConnection {
    /// Moves the actual connection and its actual startup leases into one shared lifetime.
    pub(super) fn new(connection: Connection, writer: Option<ControllerWriter>) -> Self {
        Self {
            connection,
            _writer: writer,
        }
    }
}

impl Deref for ManagedConnection {
    type Target = Connection;

    /// Borrows the original connection; the private wrapper cannot release its writer early.
    fn deref(&self) -> &Connection {
        &self.connection
    }
}

impl DerefMut for ManagedConnection {
    /// Borrows the same actual mutable connection for existing atomic transactions.
    fn deref_mut(&mut self) -> &mut Connection {
        &mut self.connection
    }
}

/// Both actual process-exclusive descriptors; caller JSON and arbitrary Arc values are not leases.
pub(super) struct ControllerWriter {
    /// Canonical original name also excludes older maintained path-lock writers.
    sibling: File,
    /// Actual database inode also excludes independent hardlink names.
    database: File,
    /// Canonical path opened by the SQLite connection.
    path: PathBuf,
}

impl ControllerWriter {
    /// Acquires regular descriptors before SQLite, retaining original paths and actual inode identity.
    pub(super) fn acquire(path: &Path) -> Result<Self, SqliteEventLogError> {
        let path = if path.exists() {
            path.canonicalize()
        } else {
            let name = path
                .file_name()
                .ok_or_else(|| invalid("Controller SQL leaf is absent"))?;
            path.parent()
                .filter(|parent| !parent.as_os_str().is_empty())
                .unwrap_or(Path::new("."))
                .canonicalize()
                .map(|parent| parent.join(name))
        }
        .map_err(io_error)?;
        let mut sibling = path.as_os_str().to_os_string();
        sibling.push(".writer.lock");
        let sibling = regular(Path::new(&sibling))?;
        sibling.try_lock().map_err(|error| {
            invalid(&format!(
                "Controller original sibling writer is active: {error}"
            ))
        })?;
        let database = regular(&path)?;
        database.try_lock().map_err(|error| {
            invalid(&format!(
                "Controller actual SQL inode writer is active: {error}"
            ))
        })?;
        let writer = Self {
            sibling,
            database,
            path,
        };
        writer.verify()?;
        Ok(writer)
    }

    /// Supplies only the canonical name whose actual held descriptor was verified.
    pub(super) fn path(&self) -> &Path {
        &self.path
    }

    /// Refuses descriptor/path replacement before or after actual SQLite opening and migration.
    pub(super) fn verify(&self) -> Result<(), SqliteEventLogError> {
        let held = self.database.metadata().map_err(io_error)?;
        let current = std::fs::symlink_metadata(&self.path).map_err(io_error)?;
        if !current.is_file() || (held.dev(), held.ino()) != (current.dev(), current.ino()) {
            return Err(invalid(
                "Controller original SQL descriptor/path identity changed",
            ));
        }
        Ok(())
    }
}

impl Drop for ControllerWriter {
    /// Explicit unlock follows actual connection close, independent of inherited nonwriter descriptors.
    fn drop(&mut self) {
        let _ = self.database.unlock();
        let _ = self.sibling.unlock();
    }
}

/// Opens a no-follow regular leaf without truncating any original authority bytes.
fn regular(path: &Path) -> Result<File, SqliteEventLogError> {
    let file = OpenOptions::new()
        .create(true)
        .truncate(false)
        .read(true)
        .write(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .open(path)
        .map_err(io_error)?;
    if !file.metadata().map_err(io_error)?.is_file() {
        return Err(invalid("Controller writer requires an actual regular file"));
    }
    Ok(file)
}

/// Converts filesystem failures without silently creating replacement authority.
fn io_error(error: std::io::Error) -> SqliteEventLogError {
    invalid(&format!("Controller writer file failure: {error}"))
}

/// Returns a bounded storage-contract refusal with no application state transition.
fn invalid(message: &str) -> SqliteEventLogError {
    SqliteEventLogError::Codec(message.into())
}

/// Reads an existing schema object without treating a missing mandatory object as an empty database.
fn object(
    connection: &Connection,
    name: &str,
) -> Result<Option<(String, String)>, SqliteEventLogError> {
    Ok(connection
        .query_row(
            "SELECT type,sql FROM sqlite_master WHERE name=?1",
            [name],
            |row| Ok((row.get(0)?, row.get(1)?)),
        )
        .optional()?)
}

/// Recognizes any existing fenced generation before a legacy opener performs SQL effects.
pub(super) fn is_fenced(connection: &Connection) -> Result<bool, SqliteEventLogError> {
    let version: u64 = connection.query_row("PRAGMA user_version", [], |row| row.get(0))?;
    Ok(version != 0
        || object(connection, "controller_storage_format")?.is_some()
        || object(connection, "controller_v1_events")?.is_some()
        || object(connection, "events")?.is_some_and(|(kind, _)| kind != "table"))
}

/// Drains actual older SQLite connections, including writers with another hardlink name.
pub(super) fn drain(connection: &mut Connection) -> Result<(), SqliteEventLogError> {
    connection.busy_timeout(std::time::Duration::from_millis(250))?;
    let mode: String = connection
        .query_row("PRAGMA journal_mode=DELETE", [], |row| row.get(0))
        .map_err(|error| {
            invalid(&format!(
                "Controller original SQLite connections are not quiescent: {error}"
            ))
        })?;
    if !mode.eq_ignore_ascii_case("delete") {
        return Err(invalid(
            "Controller original SQLite journal did not enter DELETE",
        ));
    }
    connection
        .transaction_with_behavior(TransactionBehavior::Exclusive)?
        .commit()?;
    Ok(())
}

/// Atomically fences old startup paths while retaining every original event row and application field.
pub(super) fn upgrade(connection: &mut Connection) -> Result<(), SqliteEventLogError> {
    let transaction = connection.transaction_with_behavior(TransactionBehavior::Exclusive)?;
    transaction.execute_batch("ALTER TABLE events RENAME TO controller_v1_events;
        DROP INDEX IF EXISTS idx_events_timestamp;
        DROP INDEX IF EXISTS idx_events_correlation;
        DROP INDEX IF EXISTS idx_events_sequence;
        DROP INDEX IF EXISTS idx_artifact_reference_selector;
        CREATE VIEW events AS SELECT * FROM controller_v1_events;
        CREATE TABLE controller_storage_format (version INTEGER PRIMARY KEY CHECK(version=1), schema TEXT NOT NULL);
        INSERT INTO controller_storage_format VALUES (1,'roboguide.controller-storage/v1');
        PRAGMA user_version=1;
        CREATE INDEX controller_v1_events_timestamp ON controller_v1_events(timestamp_ms,event_id);
        CREATE INDEX controller_v1_events_correlation ON controller_v1_events(correlation_id,event_id);
        CREATE INDEX controller_v1_events_sequence ON controller_v1_events(sequence);
        CREATE INDEX controller_v1_artifact_reference_selector ON controller_v1_events (
          json_extract(payload_json, '$.ArtifactCatalogReferenceTransition.record.selector.memory_id'),
          json_extract(payload_json, '$.ArtifactCatalogReferenceTransition.record.selector.revision_id'),sequence
        ) WHERE payload_schema='domain.EventPayload.json/v29';")?;
    validate(&transaction)?;
    transaction.commit()?;
    Ok(())
}

/// Requires the complete durable generation before replay or writes; missing evidence never bootstraps.
pub(super) fn validate(connection: &Connection) -> Result<(), SqliteEventLogError> {
    let version: u64 = connection.query_row("PRAGMA user_version", [], |row| row.get(0))?;
    if version != 1
        || object(connection, "events")?
            != Some((
                "view".into(),
                "CREATE VIEW events AS SELECT * FROM controller_v1_events".into(),
            ))
    {
        return Err(invalid(
            "Controller storage v1 mandatory version/view differs",
        ));
    }
    for name in [
        "controller_storage_format",
        "controller_v1_events",
        "event_log_metadata",
        "controller_checkpoint",
        "controller_checkpoint_head",
        "controller_checkpoint_deltas",
        "control_command_journal",
        "service_access_audit",
    ] {
        if object(connection, name)?.is_none_or(|(kind, _)| kind != "table") {
            return Err(invalid(
                "Controller storage v1 mandatory original table is absent",
            ));
        }
    }
    let rows: Vec<(u64, String)> = connection
        .prepare("SELECT version,schema FROM controller_storage_format LIMIT 2")?
        .query_map([], |row| Ok((row.get(0)?, row.get(1)?)))?
        .collect::<Result<_, _>>()?;
    let forbidden: u64 = connection.query_row("SELECT COUNT(*) FROM sqlite_master WHERE type='trigger' OR name IN ('idx_events_timestamp','idx_events_correlation','idx_events_sequence','idx_artifact_reference_selector')",[],|row|row.get(0))?;
    if rows != vec![(1, "roboguide.controller-storage/v1".into())] || forbidden != 0 {
        return Err(invalid(
            "Controller storage v1 marker or read-only old-writer fence differs",
        ));
    }
    let columns = super::table_columns(connection, "controller_v1_events")?;
    if ![
        "sequence",
        "event_id",
        "timestamp_ms",
        "correlation_id",
        "causation_id",
        "payload_schema",
        "payload_json",
    ]
    .into_iter()
    .all(|name| columns.contains(name))
    {
        return Err(invalid(
            "Controller storage v1 original event columns are incomplete",
        ));
    }
    Ok(())
}
