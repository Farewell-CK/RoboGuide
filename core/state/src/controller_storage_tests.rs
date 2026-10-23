//! Actual filesystem, SQLite and inherited-descriptor checks for the runnable Controller writer.

use super::*;
use std::io::Write;
use std::process::{Command, Stdio};

/// Uses the actual Controller constructor; no caller-supplied boolean is a writer receipt.
fn owned(
    path: &Path,
    format: ControllerStorageFormat,
) -> Result<SqliteEventLog, SqliteEventLogError> {
    SqliteEventLog::open_controller_with_checkpoint_policy(
        path,
        CheckpointStoragePolicy::Full,
        format,
    )
}

/// Different canonical names cannot own the same inode, even after the first EventLog clone drops.
#[test]
fn hardlink_and_last_clone_retain_actual_sql_writer() {
    let root = tempfile::tempdir().unwrap();
    let source = root.path().join("source.sqlite3");
    let alias = root.path().join("alias.sqlite3");
    let original = owned(&source, ControllerStorageFormat::Legacy).unwrap();
    std::fs::hard_link(&source, &alias).unwrap();
    let clone = original.clone();
    drop(original);
    assert!(
        owned(&alias, ControllerStorageFormat::Legacy)
            .unwrap_err()
            .to_string()
            .contains("inode writer is active")
    );
    drop(clone);
    assert!(owned(&alias, ControllerStorageFormat::Legacy).is_ok());
}

/// A live legacy connection without flock must drain before actual generation migration or effects.
#[test]
fn native_drain_refuses_live_legacy_before_schema_changes() {
    let root = tempfile::tempdir().unwrap();
    let source = root.path().join("source.sqlite3");
    let alias = root.path().join("alias.sqlite3");
    let legacy = SqliteEventLog::open(&source).unwrap();
    std::fs::hard_link(&source, &alias).unwrap();
    assert!(owned(&alias, ControllerStorageFormat::V1).is_err());
    assert!(!controller_storage::is_fenced(&legacy.connection.lock().unwrap()).unwrap());
    drop(legacy);
    assert!(owned(&alias, ControllerStorageFormat::V1).is_ok());
}

/// An accepted old-writer fence permits normal restart beside an independent read-only observer.
#[test]
fn accepted_generation_reopens_with_an_independent_readonly_observer() {
    let root = tempfile::tempdir().unwrap();
    let path = root.path().join("current.sqlite3");
    drop(owned(&path, ControllerStorageFormat::V1).unwrap());
    let observer =
        Connection::open_with_flags(&path, rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY).unwrap();
    observer
        .query_row("SELECT COUNT(*) FROM events", [], |row| {
            row.get::<_, u64>(0)
        })
        .unwrap();
    let current = owned(&path, ControllerStorageFormat::V1).unwrap();
    assert!(current.is_empty().unwrap());
    drop(current);
    assert_eq!(
        observer
            .query_row("SELECT COUNT(*) FROM events", [], |row| row
                .get::<_, u64>(0))
            .unwrap(),
        0
    );
}

/// Zero-event generations fence ordinary/old startup without manufacturing an authority checkpoint.
#[test]
fn empty_generation_is_durable_and_missing_marker_never_bootstraps() {
    let root = tempfile::tempdir().unwrap();
    let path = root.path().join("empty.sqlite3");
    let log = owned(&path, ControllerStorageFormat::V1).unwrap();
    assert!(log.load_checkpoint().unwrap().is_none());
    assert!(log.is_empty().unwrap());
    drop(log);
    assert!(SqliteEventLog::open(&path).is_err());
    assert!(owned(&path, ControllerStorageFormat::Legacy).is_err());
    let connection = Connection::open(&path).unwrap();
    assert!(
        connection
            .execute_batch(
                "CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp_ms,event_id)"
            )
            .is_err()
    );
    connection
        .execute("DELETE FROM controller_storage_format", [])
        .unwrap();
    drop(connection);
    assert!(owned(&path, ControllerStorageFormat::V1).is_err());
    assert!(
        Connection::open(&path)
            .unwrap()
            .query_row(
                "SELECT COUNT(*) FROM controller_storage_format",
                [],
                |row| row.get::<_, u64>(0)
            )
            .unwrap()
            == 0
    );
}

/// The exact original event/checkpoint survives migration; v1 appends use the private mutation table.
#[test]
fn upgrade_preserves_raw_rows_and_private_appends() {
    let root = tempfile::tempdir().unwrap();
    let path = root.path().join("source.sqlite3");
    let mut legacy = SqliteEventLog::open(&path).unwrap();
    legacy.begin_batch().unwrap();
    let payload = EventPayload::ExecutionGroupBlocked {
        group_id: domain::ExecutionGroupId::new("original").unwrap(),
        task_ref: domain::TaskRef::new(
            domain::MissionId::new("original").unwrap(),
            domain::TaskId::new("original").unwrap(),
        ),
        reason: "original source".into(),
    };
    legacy.append(
        TimestampMs::new(1),
        &CorrelationId::new("original").unwrap(),
        None,
        payload.clone(),
    );
    legacy
        .save_checkpoint("example.original/v1", "{\"original\":true}")
        .unwrap();
    legacy.commit_batch().unwrap();
    let original = legacy.events().unwrap();
    let checkpoint = legacy.load_checkpoint().unwrap();
    drop(legacy);
    let mut current = owned(&path, ControllerStorageFormat::V1).unwrap();
    assert_eq!(current.events().unwrap(), original);
    assert_eq!(current.load_checkpoint().unwrap(), checkpoint);
    current.append(
        TimestampMs::new(2),
        &CorrelationId::new("next").unwrap(),
        None,
        payload,
    );
    assert_eq!(current.events().unwrap().len(), 2);
    assert_eq!(current.events().unwrap()[0], original[0]);
    assert!(
        current
            .connection
            .lock()
            .unwrap()
            .execute("DELETE FROM events", [])
            .is_err()
    );
}

/// A failed complete generation check cannot publish a partially renamed or writable legacy layout.
#[test]
fn failed_generation_validation_rolls_back_the_complete_format_transaction() {
    let root = tempfile::tempdir().unwrap();
    let path = root.path().join("original.sqlite3");
    let legacy = SqliteEventLog::open(&path).unwrap();
    legacy.connection.lock().unwrap().execute_batch("CREATE TRIGGER original_fault BEFORE INSERT ON events BEGIN SELECT RAISE(ABORT,'original injected SQL failure'); END").unwrap();
    drop(legacy);
    assert!(owned(&path, ControllerStorageFormat::V1).is_err());
    let connection = Connection::open(&path).unwrap();
    assert_eq!(
        connection
            .query_row("PRAGMA user_version", [], |row| row.get::<_, u64>(0))
            .unwrap(),
        0
    );
    assert_eq!(
        connection
            .query_row(
                "SELECT type FROM sqlite_master WHERE name='events'",
                [],
                |row| row.get::<_, String>(0)
            )
            .unwrap(),
        "table"
    );
    assert!(
        controller_storage::object(&connection, "controller_storage_format")
            .unwrap()
            .is_none()
    );
    assert!(
        controller_storage::object(&connection, "controller_v1_events")
            .unwrap()
            .is_none()
    );
    assert!(
        controller_storage::object(&connection, "original_fault")
            .unwrap()
            .is_some()
    );
}

/// A real nonwriter child retaining both descriptors cannot extend the closed SQL connection's lease.
#[test]
fn inherited_child_descriptors_do_not_extend_writer_authority() {
    let root = tempfile::tempdir().unwrap();
    let path = root.path().join("source.sqlite3");
    let original = owned(&path, ControllerStorageFormat::Legacy).unwrap();
    let guard = original.connection.lock().unwrap();
    let writer = guard._writer.as_ref().unwrap();
    let mut child = Command::new("python3")
        .args(["-c", "import sys; sys.stdin.buffer.read(1)"])
        .stdin(Stdio::piped())
        .stdout(Stdio::from(writer.database.try_clone().unwrap()))
        .stderr(Stdio::from(writer.sibling.try_clone().unwrap()))
        .spawn()
        .unwrap();
    drop(guard);
    drop(original);
    assert!(child.try_wait().unwrap().is_none());
    let replacement = owned(&path, ControllerStorageFormat::Legacy).unwrap();
    child.stdin.as_mut().unwrap().write_all(b"x").unwrap();
    assert!(child.wait().unwrap().success());
    drop(replacement);
}
