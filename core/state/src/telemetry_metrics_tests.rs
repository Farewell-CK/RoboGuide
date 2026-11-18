//! Actual SQLite tests for transactional original counters, hot gauges and corruption refusal.

use super::*;
use domain::*;

/// Creates original metadata for counter-unit fixtures without manufacturing a live Controller.
fn metrics_connection(floor: Option<&str>) -> Connection {
    let connection = Connection::open_in_memory().unwrap();
    connection
        .execute_batch("CREATE TABLE event_log_metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        .unwrap();
    if let Some(floor) = floor {
        connection
            .execute(
                "INSERT INTO event_log_metadata VALUES('history_floor',?1)",
                [floor],
            )
            .unwrap();
    }
    connection
}

/// Opens the real v3 event log and original application writer in an isolated owned directory.
fn open(path: &Path) -> SqliteEventLog {
    SqliteEventLog::open_controller_with_checkpoint_policy(
        path,
        CheckpointStoragePolicy::Full,
        ControllerStorageFormat::V3,
    )
    .unwrap()
}

/// Produces one actual original observation with a source-scoped immutable repeated JSON value.
fn observation(source: &str, sequence: u64) -> EventPayload {
    EventPayload::StateRecordObserved {
        record: StateRecord::new_with_source_epoch(
            StateObjectRef::new(StateObjectClass::Node, "metric-fixture", source).unwrap(),
            StateSemantic::Reported,
            StateSource::Node {
                node_id: NodeId::new(source).unwrap(),
                local_system_id: LocalSystemId::new("metric-source").unwrap(),
            },
            "value",
            "metric-fixture/v1",
            serde_json::json!({"payload":"r".repeat(4096)}),
            Some(TimestampMs::new(sequence)),
            TimestampMs::new(sequence + 1),
            1000,
            None,
            Some("metric-epoch".into()),
            sequence,
        )
        .unwrap(),
    }
}

/// Appends via the real storage body insertion rather than calling the metrics writer directly.
fn append(log: &mut SqliteEventLog, source: &str, sequence: u64) {
    log.append(
        TimestampMs::new(sequence),
        &CorrelationId::new("metric-fixture").unwrap(),
        None,
        observation(source, sequence),
    );
    assert!(log.take_error().unwrap().is_none());
}

/// Repeated business content reduces body bytes without erasing any independently committed event.
#[test]
fn thirty_observations_keep_original_cumulative_counts_and_scoped_body_gauges() {
    let root = tempfile::tempdir().unwrap();
    let path = root.path().join("metrics.sqlite3");
    let mut log = open(&path);
    assert_eq!(
        log.telemetry_storage_metrics().unwrap().counters,
        TelemetryStorageCounters::default()
    );
    log.begin_batch().unwrap();
    for sequence in 1..=30 {
        append(&mut log, "source-one", sequence);
    }
    assert!(log.telemetry_storage_metrics().is_err());
    log.save_checkpoint("metric-fixture/v1", "{}").unwrap();
    log.commit_batch().unwrap();
    let snapshot = log.telemetry_storage_metrics().unwrap();
    let expected_bytes = serde_json::to_vec(&serde_json::json!({"payload":"r".repeat(4096)}))
        .unwrap()
        .len() as u64;
    assert_eq!(snapshot.counters.appended_events_total, 30);
    assert_eq!(snapshot.counters.accounting_start_event_sequence, 1);
    assert_eq!(snapshot.counters.observation_events_total, 30);
    assert_eq!(
        snapshot.counters.logical_value_bytes_total,
        expected_bytes * 30
    );
    assert_eq!(snapshot.counters.body_creation_misses_total, 1);
    assert_eq!(snapshot.counters.body_reuse_hits_total, 29);
    assert_eq!(
        snapshot.counters.created_body_value_bytes_total,
        expected_bytes
    );
    assert_eq!(snapshot.counters.hot_events, 30);
    assert_eq!(snapshot.counters.hot_observations, 30);
    assert_eq!(snapshot.counters.hot_bodies, 1);
    assert_eq!(snapshot.counters.hot_body_value_bytes, expected_bytes);
    assert_eq!(
        snapshot.sqlite_allocated_bytes,
        snapshot.sqlite_page_count * snapshot.sqlite_page_size_bytes
    );
    assert!(snapshot.sqlite_reusable_bytes <= snapshot.sqlite_allocated_bytes);
    assert!(
        snapshot.counters.created_body_value_bytes_total * 10
            < snapshot.counters.logical_value_bytes_total
    );

    log.begin_batch().unwrap();
    append(&mut log, "source-two", 31);
    log.save_checkpoint("metric-fixture/v1", "{\"new\":true}")
        .unwrap();
    log.commit_batch().unwrap();
    let final_counters = log.telemetry_storage_metrics().unwrap().counters;
    assert_eq!(final_counters.hot_bodies, 2);
    assert_eq!(final_counters.body_creation_misses_total, 2);
    assert_eq!(final_counters.body_reuse_hits_total, 29);
    assert_eq!(final_counters.last_appended_event_sequence, 31);
}

/// SQL rollback removes both storage effects and all original counter increments together.
#[test]
fn rollback_and_process_close_never_publish_uncommitted_metrics() {
    let root = tempfile::tempdir().unwrap();
    let path = root.path().join("rollback.sqlite3");
    let mut log = open(&path);
    let original = log.telemetry_storage_metrics().unwrap().counters;
    log.begin_batch().unwrap();
    append(&mut log, "source-one", 1);
    log.rollback_batch().unwrap();
    assert_eq!(log.telemetry_storage_metrics().unwrap().counters, original);
    log.begin_batch().unwrap();
    append(&mut log, "source-one", 1);
    drop(log);
    assert_eq!(
        open(&path).telemetry_storage_metrics().unwrap().counters,
        original
    );
}

/// Publication-authorized hot deletion decrements only current gauges, preserving all original totals.
#[test]
fn retirement_preserves_cumulative_history_and_refuses_underflow() {
    let connection = metrics_connection(None);
    connection.execute_batch("BEGIN IMMEDIATE").unwrap();
    initialize(&connection).unwrap();
    record_append(&connection, 1, Some(4096), true).unwrap();
    record_append(&connection, 2, Some(4096), false).unwrap();
    record_append(&connection, 3, None, false).unwrap();
    connection.execute_batch("COMMIT;BEGIN IMMEDIATE").unwrap();
    let before = read(&connection).unwrap();
    record_retirement(&connection, 2, 2, 1, 4096).unwrap();
    let after = read(&connection).unwrap();
    assert_eq!(after.appended_events_total, before.appended_events_total);
    assert_eq!(
        after.logical_value_bytes_total,
        before.logical_value_bytes_total
    );
    assert_eq!(
        after.created_body_value_bytes_total,
        before.created_body_value_bytes_total
    );
    assert_eq!(after.hot_events, 1);
    assert_eq!(after.hot_observations, 0);
    assert_eq!(after.hot_bodies, 0);
    assert_eq!(after.hot_body_value_bytes, 0);
    assert!(record_retirement(&connection, 2, 0, 0, 0).is_err());
    assert_eq!(read(&connection).unwrap(), after);
    connection.execute_batch("ROLLBACK").unwrap();
    assert_eq!(read(&connection).unwrap(), before);
}

/// A missing head, different generation or accidental checksum corruption never becomes zero metrics.
#[test]
fn missing_corrupt_or_unknown_metrics_are_refused() {
    for mutation in [
        "DELETE FROM telemetry_storage_metrics",
        "UPDATE telemetry_storage_metrics SET digest='sha256:broken'",
        "UPDATE telemetry_storage_metrics SET data_json='{}'",
        "UPDATE telemetry_storage_metrics SET schema='unknown-generation'",
    ] {
        let connection = metrics_connection(None);
        connection.execute_batch("BEGIN IMMEDIATE").unwrap();
        initialize(&connection).unwrap();
        connection.execute_batch("COMMIT").unwrap();
        connection.execute_batch(mutation).unwrap();
        assert!(
            read(&connection).is_err(),
            "mutation was accepted: {mutation}"
        );
    }
}

/// A read has no transaction or application authority mutation and writers cannot autocommit counters.
#[test]
fn read_is_observational_and_mutations_require_original_transaction() {
    let connection = metrics_connection(None);
    assert!(initialize(&connection).is_err());
    connection.execute_batch("BEGIN IMMEDIATE").unwrap();
    initialize(&connection).unwrap();
    connection.execute_batch("COMMIT").unwrap();
    let changes = connection.total_changes();
    assert_eq!(
        read(&connection).unwrap(),
        TelemetryStorageCounters::default()
    );
    snapshot(&connection).unwrap();
    assert_eq!(connection.total_changes(), changes);
    assert!(record_append(&connection, 1, Some(4096), true).is_err());
    assert!(record_retirement(&connection, 0, 0, 0, 0).is_err());
}

/// A nested rejected input rolls back its own counters while the accepted outer batch can commit.
#[test]
fn rejected_input_savepoint_does_not_inflate_outer_commit_counters() {
    let root = tempfile::tempdir().unwrap();
    let mut log = open(&root.path().join("savepoint.sqlite3"));
    log.begin_batch().unwrap();
    append(&mut log, "accepted", 1);
    let mut candidate = log.clone();
    let result = log
        .with_input_savepoint(|| {
            append(&mut candidate, "rejected", 2);
            Err::<(), _>("fixture rejected input")
        })
        .unwrap();
    assert!(result.is_err());
    log.save_checkpoint("metric-fixture/v1", "{}").unwrap();
    log.commit_batch().unwrap();
    let counters = log.telemetry_storage_metrics().unwrap().counters;
    assert_eq!(counters.appended_events_total, 1);
    assert_eq!(counters.body_creation_misses_total, 1);
    assert_eq!(counters.last_appended_event_sequence, 1);
}

/// Original migrations account actual source rows instead of estimating history from file size.
#[test]
fn legacy_v1_and_v2_migrations_measure_actual_original_values() {
    for format in [
        ControllerStorageFormat::Legacy,
        ControllerStorageFormat::V1,
        ControllerStorageFormat::V2,
    ] {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("migration.sqlite3");
        let mut source = SqliteEventLog::open_controller_with_checkpoint_policy(
            &path,
            CheckpointStoragePolicy::Full,
            format,
        )
        .unwrap();
        source.begin_batch().unwrap();
        for sequence in 1..=30 {
            append(&mut source, "source-one", sequence);
        }
        source.save_checkpoint("metric-fixture/v1", "{}").unwrap();
        source.commit_batch().unwrap();
        drop(source);
        let migrated = open(&path).telemetry_storage_metrics().unwrap();
        assert_eq!(migrated.counters.appended_events_total, 30);
        assert_eq!(migrated.counters.accounting_start_event_sequence, 1);
        assert_eq!(migrated.counters.body_creation_misses_total, 1);
        assert_eq!(migrated.counters.body_reuse_hits_total, 29);
        assert_eq!(migrated.counters.hot_bodies, 1);
    }
}

/// An already archived pre-v3 prefix has no invented byte history and subsequent sequence is exact.
#[test]
fn measured_span_can_start_after_old_archive_but_cannot_skip_later_appends() {
    let connection = metrics_connection(Some("40"));
    connection.execute_batch("BEGIN IMMEDIATE").unwrap();
    initialize(&connection).unwrap();
    let empty = read(&connection).unwrap();
    assert_eq!(empty.accounting_start_event_sequence, 41);
    assert_eq!(empty.last_appended_event_sequence, 40);
    assert_eq!(empty.appended_events_total, 0);
    record_append(&connection, 41, Some(1), true).unwrap();
    let original = read(&connection).unwrap();
    assert_eq!(original.accounting_start_event_sequence, 41);
    assert_eq!(original.appended_events_total, 1);
    assert!(record_append(&connection, 43, Some(1), false).is_err());
    assert!(record_append(&connection, 41, Some(1), false).is_err());
    assert_eq!(read(&connection).unwrap(), original);
    record_append(&connection, 42, Some(1), false).unwrap();
    assert_eq!(read(&connection).unwrap().appended_events_total, 2);
}

/// Corrupt accounting cannot be hidden by replacing both a JSON field and its plain checksum.
#[test]
fn counter_relations_sequence_gaps_and_signed_overflow_are_refused() {
    let connection = metrics_connection(None);
    connection.execute_batch("BEGIN IMMEDIATE").unwrap();
    initialize(&connection).unwrap();
    record_append(&connection, 1, Some(4096), true).unwrap();
    let original = read(&connection).unwrap();
    for field in [
        "accounting_start_event_sequence",
        "body_reuse_hits_total",
        "hot_events",
        "hot_observations",
        "last_appended_event_sequence",
    ] {
        let mut changed = serde_json::to_value(&original).unwrap();
        changed[field] = serde_json::json!(0);
        if field == "body_reuse_hits_total" {
            changed[field] = serde_json::json!(1);
        }
        let data = serde_json::to_string(&changed).unwrap();
        connection
            .execute(
                "UPDATE telemetry_storage_metrics SET data_json=?1,digest=?2",
                params![data, digest(&data)],
            )
            .unwrap();
        assert!(read(&connection).is_err(), "accepted altered {field}");
    }
    let mut changed = serde_json::to_value(&original).unwrap();
    changed["logical_value_bytes_total"] = serde_json::json!(u64::MAX);
    let data = serde_json::to_string(&changed).unwrap();
    connection
        .execute(
            "UPDATE telemetry_storage_metrics SET data_json=?1,digest=?2",
            params![data, digest(&data)],
        )
        .unwrap();
    assert!(read(&connection).is_err());
}

/// Retirement rolls back with its original SQL transaction and a later body recreation is a new miss.
#[test]
fn retired_body_recreation_never_erases_cumulative_writes_or_invents_a_reuse_hit() {
    let connection = metrics_connection(None);
    connection.execute_batch("BEGIN IMMEDIATE").unwrap();
    initialize(&connection).unwrap();
    record_append(&connection, 1, Some(4096), true).unwrap();
    record_retirement(&connection, 1, 1, 1, 4096).unwrap();
    let retired = read(&connection).unwrap();
    assert_eq!(retired.hot_events, 0);
    assert_eq!(retired.logical_value_bytes_total, 4096);
    assert_eq!(retired.last_appended_event_sequence, 1);
    record_append(&connection, 2, Some(4096), true).unwrap();
    let recreated = read(&connection).unwrap();
    assert_eq!(recreated.body_creation_misses_total, 2);
    assert_eq!(recreated.body_reuse_hits_total, 0);
    assert_eq!(recreated.logical_value_bytes_total, 8192);
    assert_eq!(recreated.created_body_value_bytes_total, 8192);
    assert_eq!(recreated.hot_body_value_bytes, 4096);
}

/// Already archived origins never accept malformed floor metadata as an invented empty baseline.
#[test]
fn original_archive_floor_must_be_canonical_and_within_sql_sequence_bounds() {
    for floor in ["broken", "-1", "+1", "01", "9223372036854775807"] {
        let connection = metrics_connection(Some(floor));
        connection.execute_batch("BEGIN IMMEDIATE").unwrap();
        assert!(
            initialize(&connection).is_err(),
            "accepted original floor {floor}"
        );
        connection.execute_batch("ROLLBACK").unwrap();
        let retained: u64 = connection.query_row(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='telemetry_storage_metrics'", [], |row| row.get(0),
        ).unwrap();
        assert_eq!(retained, 0);
    }
}

/// Real prefix deletion retains bodies still referenced by later observations and rolls back exactly.
#[test]
fn actual_prefix_retirement_counts_removed_rows_and_only_last_body_references() {
    let root = tempfile::tempdir().unwrap();
    let mut log = open(&root.path().join("actual-retirement.sqlite3"));
    log.begin_batch().unwrap();
    append(&mut log, "shared", 1);
    append(&mut log, "shared", 2);
    append(&mut log, "other", 3);
    log.save_checkpoint("metric-fixture/v1", "{}").unwrap();
    log.commit_batch().unwrap();
    let before = log.telemetry_storage_metrics().unwrap().counters;
    log.begin_batch().unwrap();
    {
        let connection = log.connection.lock().unwrap();
        assert_eq!(
            super::super::telemetry_storage::retire_prefix(&connection, 0, 1).unwrap(),
            1
        );
        let first = read(&connection).unwrap();
        assert_eq!(first.hot_observations, 2);
        assert_eq!(first.hot_bodies, 2);
        assert_eq!(first.hot_body_value_bytes, before.hot_body_value_bytes);
        assert_eq!(
            super::super::telemetry_storage::retire_prefix(&connection, 1, 2).unwrap(),
            1
        );
        let second = read(&connection).unwrap();
        assert_eq!(second.hot_observations, 1);
        assert_eq!(second.hot_bodies, 1);
        assert_eq!(second.appended_events_total, before.appended_events_total);
        assert_eq!(
            second.body_creation_misses_total,
            before.body_creation_misses_total
        );
    }
    log.rollback_batch().unwrap();
    assert_eq!(log.telemetry_storage_metrics().unwrap().counters, before);
    assert_eq!(log.events().unwrap().len(), 3);
}
