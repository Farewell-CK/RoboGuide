//! Actual SQL migration, original evidence, rollback and fast-recovery regressions.

use super::*;
use domain::*;

/// Opens only a test-owned database using the selected real storage contract.
fn open(
    path: &Path,
    format: ControllerStorageFormat,
) -> Result<SqliteEventLog, SqliteEventLogError> {
    SqliteEventLog::open_controller_with_checkpoint_policy(
        path,
        CheckpointStoragePolicy::Full,
        format,
    )
}

/// Builds one unrelated original heartbeat without inventing Catalog evidence.
fn heartbeat() -> EventPayload {
    EventPayload::NodeHeartbeatAccepted {
        node_id: NodeId::new("fixture").unwrap(),
        lease_id: LeaseId::new("fixture-lease").unwrap(),
    }
}

/// Builds a protected original publication with complete source semantics.
fn publication() -> EventPayload {
    EventPayload::MemoryManifestPublished {
        manifest: MemoryArtifactManifest::new(
            MemorySelector::new(
                MemoryId::new("original").unwrap(),
                MemoryRevisionId::new("v1").unwrap(),
            ),
            MemoryKind::Artifact,
            "provider",
            MemoryOwner::RoboGuide {
                component: "fixture".into(),
            },
            MemoryScope::Global,
            MemoryVisibility::Discoverable,
            "opaque/v1",
            "application/octet-stream",
            None,
            None,
            None,
            None,
            TimestampMs::new(2),
        )
        .unwrap(),
    }
}

/// Appends through the actual transactional EventSink and surfaces any write refusal.
fn put(log: &mut SqliteEventLog, time: u64, payload: EventPayload) {
    log.append(
        TimestampMs::new(time),
        &CorrelationId::new("fixture").unwrap(),
        None,
        payload,
    );
    assert!(log.take_error().unwrap().is_none());
}

/// Large unrelated history remains byte-for-byte in storage while normal recovery reads only its projection.
#[test]
fn migration_retains_history_and_restart_reads_only_protected_sources() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("state.sqlite3");
    let mut old = open(&path, ControllerStorageFormat::Legacy).unwrap();
    old.begin_batch().unwrap();
    for i in 1..=1000 {
        put(&mut old, i, heartbeat());
    }
    put(&mut old, 2, publication());
    old.save_checkpoint("fixture.original/v1", "{}").unwrap();
    old.commit_batch().unwrap();
    let original = old.events().unwrap();
    drop(old);
    let mut current = open(&path, ControllerStorageFormat::V2).unwrap();
    assert_eq!(current.events().unwrap(), original);
    let recovered = current.catalog_recovery_events().unwrap();
    assert_eq!(recovered.scanned_events, 1);
    assert_eq!(recovered.timestamp_high_water, TimestampMs::new(1000));
    assert_eq!(recovered.events[0].payload(), &publication());
    put(&mut current, 2000, heartbeat());
    drop(current);
    assert!(open(&path, ControllerStorageFormat::Legacy).is_err());
    assert!(open(&path, ControllerStorageFormat::V1).is_err());
    assert!(SqliteEventLog::open(&path).is_err());
    let current = open(&path, ControllerStorageFormat::V2).unwrap();
    assert_eq!(current.catalog_recovery_events().unwrap().scanned_events, 1);
    assert_eq!(
        current
            .catalog_recovery_events()
            .unwrap()
            .timestamp_high_water,
        TimestampMs::new(2000)
    );
    assert_eq!(current.events().unwrap().len(), 1002);
    assert!(SqliteEventLog::read_complete_replica_state(&path).is_err());
}

/// Bad original telemetry makes the entire migration roll back, including the physical version fence.
#[test]
fn invalid_legacy_source_rolls_back_new_tables_and_version() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("state.sqlite3");
    let mut old = open(&path, ControllerStorageFormat::Legacy).unwrap();
    put(&mut old, 1, heartbeat());
    old.connection
        .lock()
        .unwrap()
        .execute("UPDATE events SET payload_json='bad-json'", [])
        .unwrap();
    drop(old);
    assert!(open(&path, ControllerStorageFormat::V2).is_err());
    let conn = Connection::open(&path).unwrap();
    assert_eq!(
        conn.pragma_query_value(None, "user_version", |r| r.get::<_, u64>(0))
            .unwrap(),
        0
    );
    assert_eq!(
        conn.query_row(
            "SELECT COUNT(*) FROM sqlite_master WHERE name LIKE 'catalog_recovery_%'",
            [],
            |r| r.get::<_, u64>(0)
        )
        .unwrap(),
        0
    );
    assert_eq!(
        conn.query_row("SELECT payload_json FROM events", [], |r| r
            .get::<_, String>(0))
            .unwrap(),
        "bad-json"
    );
}

/// Migration cannot certify coverage over a missing ordinary telemetry row.
#[test]
fn missing_original_tail_row_refuses_migration() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("state.sqlite3");
    let mut log = open(&path, ControllerStorageFormat::Legacy).unwrap();
    for sequence in 1..=3 {
        put(&mut log, sequence, heartbeat());
    }
    log.connection
        .lock()
        .unwrap()
        .execute("DELETE FROM events WHERE sequence=2", [])
        .unwrap();
    drop(log);
    assert!(open(&path, ControllerStorageFormat::V2).is_err());
    let conn = Connection::open(path).unwrap();
    assert_eq!(
        conn.pragma_query_value(None, "user_version", |row| row.get::<_, u64>(0))
            .unwrap(),
        0
    );
}

/// Interrupted or explicitly rolled-back batches never advance projection coverage or preserve private rows.
#[test]
fn batch_rollback_restores_coverage_and_protected_rows() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("state.sqlite3");
    let mut log = open(&path, ControllerStorageFormat::V2).unwrap();
    put(&mut log, 1, heartbeat());
    log.begin_batch().unwrap();
    put(&mut log, 2, publication());
    assert!(log.catalog_recovery_events().is_err());
    log.rollback_batch().unwrap();
    assert_eq!(log.catalog_recovery_events().unwrap().scanned_events, 0);
    assert_eq!(log.latest_sequence().unwrap(), 1);
    log.begin_batch().unwrap();
    put(&mut log, 3, publication());
    drop(log); // Actual SQLite close rolls back an uncommitted transaction.
    let log = open(&path, ControllerStorageFormat::V2).unwrap();
    assert_eq!(log.catalog_recovery_events().unwrap().scanned_events, 0);
    assert_eq!(log.latest_sequence().unwrap(), 1);
}

/// The new writer rolls back the original event when the projection cannot be committed with it.
#[test]
fn actual_projection_sql_failure_does_not_commit_source_alone() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("state.sqlite3");
    let mut log = open(&path, ControllerStorageFormat::V2).unwrap();
    log.connection.lock().unwrap().execute_batch("CREATE TRIGGER deny_catalog_head BEFORE UPDATE ON catalog_recovery_head BEGIN SELECT RAISE(ABORT,'fixture failure'); END;").unwrap();
    log.append(
        TimestampMs::new(1),
        &CorrelationId::new("fixture").unwrap(),
        None,
        heartbeat(),
    );
    assert!(log.take_error().unwrap().is_some());
    let connection = log.connection.lock().unwrap();
    assert_eq!(
        connection
            .query_row("SELECT COUNT(*) FROM events", [], |r| r.get::<_, u64>(0))
            .unwrap(),
        0
    );
    assert_eq!(head(&connection).unwrap().through_sequence, 0);
}

/// Lost rows, changed original source, wrong digest and old-writer appends cannot be silently accepted.
#[test]
fn damaged_projection_and_source_are_refused_on_reopen() {
    for mutation in [
        "DELETE FROM catalog_recovery_events",
        "DELETE FROM controller_v1_events",
        "DROP TABLE catalog_recovery_head",
        "UPDATE catalog_recovery_head SET digest='wrong'",
        "UPDATE controller_v1_events SET event_id='foreign-source' WHERE sequence=1",
        "INSERT INTO controller_v1_events SELECT 2,'extra',timestamp_ms,correlation_id,causation_id,payload_schema,payload_json FROM controller_v1_events",
    ] {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("state.sqlite3");
        let mut log = open(&path, ControllerStorageFormat::V2).unwrap();
        put(&mut log, 1, publication());
        drop(log);
        let conn = Connection::open(&path).unwrap();
        conn.execute_batch(mutation).unwrap();
        drop(conn);
        assert!(
            open(&path, ControllerStorageFormat::V2).is_err(),
            "{mutation}"
        );
    }
}

/// A pre-existing v1 writer upgrades only through explicit v2 selection and preserves complete originals.
#[test]
fn v1_upgrade_keeps_exact_original_publications() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("state.sqlite3");
    let mut log = open(&path, ControllerStorageFormat::V1).unwrap();
    put(&mut log, 1, publication());
    let original = log.events().unwrap();
    drop(log);
    let log = open(&path, ControllerStorageFormat::V2).unwrap();
    assert_eq!(log.events().unwrap(), original);
    assert_eq!(
        log.catalog_recovery_events().unwrap().events[0].payload(),
        &publication()
    );
}

/// A broken original v1 checkpoint/journal cannot leave a committed v2 fence behind.
#[test]
fn invalid_original_checkpoint_refuses_migration_before_new_version() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("state.sqlite3");
    let log = open(&path, ControllerStorageFormat::V1).unwrap();
    log.begin_batch().unwrap();
    log.save_checkpoint(crate::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA, "{}")
        .unwrap();
    log.commit_batch().unwrap();
    log.connection
        .lock()
        .unwrap()
        .execute(
            "UPDATE control_command_journal SET resulting_state_digest='broken'",
            [],
        )
        .unwrap();
    drop(log);
    assert!(open(&path, ControllerStorageFormat::V2).is_err());
    let conn = Connection::open(path).unwrap();
    assert_eq!(
        conn.pragma_query_value(None, "user_version", |row| row.get::<_, u64>(0))
            .unwrap(),
        1
    );
    assert_eq!(
        conn.query_row(
            "SELECT COUNT(*) FROM sqlite_master WHERE name LIKE 'catalog_recovery_%'",
            [],
            |row| row.get::<_, u64>(0)
        )
        .unwrap(),
        0
    );
}

/// Fast recovery trusts the committed coverage, while an explicit full audit still detects damaged old telemetry.
#[test]
fn full_audit_remains_available_after_covered_telemetry_corruption() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("state.sqlite3");
    let mut log = open(&path, ControllerStorageFormat::V2).unwrap();
    put(&mut log, 1, heartbeat());
    put(&mut log, 2, publication());
    log.connection
        .lock()
        .unwrap()
        .execute(
            "UPDATE controller_v1_events SET payload_json='bad-json' WHERE sequence=1",
            [],
        )
        .unwrap();
    assert_eq!(log.catalog_recovery_events().unwrap().scanned_events, 1);
    assert!(log.catalog_replay_events().is_err());
}

/// A committed protected tail survives reopening with its original timestamps and sequence order.
#[test]
fn protected_append_commits_with_source_and_survives_reopen() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("state.sqlite3");
    let mut log = open(&path, ControllerStorageFormat::V2).unwrap();
    log.begin_batch().unwrap();
    put(&mut log, 2, publication());
    put(&mut log, 3, heartbeat());
    log.commit_batch().unwrap();
    let original = log.events().unwrap();
    drop(log);
    let log = open(&path, ControllerStorageFormat::V2).unwrap();
    assert_eq!(log.events().unwrap(), original);
    let recovered = log.catalog_recovery_events().unwrap();
    assert_eq!(recovered.events.len(), 1);
    assert_eq!(recovered.events[0].timestamp(), TimestampMs::new(2));
    assert_eq!(recovered.timestamp_high_water, TimestampMs::new(3));
}
