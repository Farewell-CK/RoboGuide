//! Actual SQL tests for byte-exact migration, temporal identity, transactional loss and old writers.

use super::*;
use domain::*;

/// Opens an owned fixture with the real process writer lease and selected durable generation.
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

/// Retains exact time/epoch metadata while changing only the requested source and value.
fn observation(node: &str, sequence: u64, epoch: &str, value: serde_json::Value) -> EventPayload {
    EventPayload::StateRecordObserved {
        record: StateRecord::new_with_source_epoch(
            StateObjectRef::new(StateObjectClass::Node, "fixture", node).unwrap(),
            StateSemantic::Reported,
            StateSource::Node {
                node_id: NodeId::new(node).unwrap(),
                local_system_id: LocalSystemId::new("sensor").unwrap(),
            },
            "result",
            "fixture/v1",
            value,
            Some(TimestampMs::new(sequence)),
            TimestampMs::new(sequence + 1),
            1000,
            None,
            Some(epoch.into()),
            sequence,
        )
        .unwrap(),
    }
}

/// Uses normal append and surfaces failures before a test can mistake an empty write for success.
fn put(log: &mut SqliteEventLog, sequence: u64, payload: EventPayload) {
    log.append(
        TimestampMs::new(sequence),
        &CorrelationId::new("fixture").unwrap(),
        None,
        payload,
    );
    assert!(log.take_error().unwrap().is_none());
}

/// Body count is independent of repeated observations; each original temporal envelope remains distinct.
#[test]
fn repeated_values_share_one_body_but_keep_time_epoch_and_sequence() {
    let root = tempfile::tempdir().unwrap();
    let path = root.path().join("state.sqlite3");
    let mut log = open(&path, ControllerStorageFormat::V3).unwrap();
    let value = serde_json::json!({"text":"中文\\quotes\"\n", "payload":"x".repeat(4096)});
    log.begin_batch().unwrap();
    for i in 1..=100 {
        put(
            &mut log,
            i,
            observation("one", i, if i < 50 { "old" } else { "new" }, value.clone()),
        );
    }
    log.save_checkpoint("fixture/v1", "{}").unwrap();
    log.commit_batch().unwrap();
    let events = log.events().unwrap();
    assert_eq!(events.len(), 100);
    for (index, row) in events.iter().enumerate() {
        let i = index as u64 + 1;
        assert_eq!(
            row.payload_json,
            serde_json::to_string(&observation(
                "one",
                i,
                if i < 50 { "old" } else { "new" },
                value.clone()
            ))
            .unwrap()
        );
    }
    let connection = log.connection.lock().unwrap();
    assert_eq!(
        connection
            .query_row("SELECT COUNT(*) FROM telemetry_bodies", [], |r| r
                .get::<_, u64>(0))
            .unwrap(),
        1
    );
    let physical: u64 = connection
        .query_row("SELECT SUM(byte_size) FROM telemetry_bodies", [], |r| {
            r.get(0)
        })
        .unwrap();
    assert!(physical * 10 < events.iter().map(|e| e.payload_json.len() as u64).sum());
    drop(connection);
    drop(log);
    let log = open(&path, ControllerStorageFormat::V3).unwrap();
    assert_eq!(log.events().unwrap(), events);
}

/// Distinct sources never acquire a shared provenance identity from equal byte content.
#[test]
fn bodies_are_scoped_to_original_source() {
    let root = tempfile::tempdir().unwrap();
    let mut log = open(
        &root.path().join("state.sqlite3"),
        ControllerStorageFormat::V3,
    )
    .unwrap();
    put(
        &mut log,
        1,
        observation("one", 1, "epoch", serde_json::json!({"v":1})),
    );
    put(
        &mut log,
        2,
        observation("two", 2, "epoch", serde_json::json!({"v":1})),
    );
    assert_eq!(
        log.connection
            .lock()
            .unwrap()
            .query_row("SELECT COUNT(*) FROM telemetry_bodies", [], |r| r
                .get::<_, u64>(0))
            .unwrap(),
        2
    );
}

/// Every supported prior generation migrates atomically and reconstructs exact original strings.
#[test]
fn each_old_generation_preserves_original_raw_bytes() {
    for format in [
        ControllerStorageFormat::Legacy,
        ControllerStorageFormat::V1,
        ControllerStorageFormat::V2,
    ] {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("state.sqlite3");
        let mut log = open(&path, format).unwrap();
        put(
            &mut log,
            1,
            observation("one", 1, "epoch", serde_json::json!({"n":1.25,"text":"λ"})),
        );
        let table = if format == ControllerStorageFormat::Legacy {
            "events"
        } else {
            "controller_v1_events"
        };
        let compact = log.events().unwrap()[0].payload_json.clone();
        // Whitespace and exponent spellings are actual historical bytes and must survive.
        let raw = compact.replace("1.25", "1.2500e0").replacen("{", "{ ", 1);
        log.connection
            .lock()
            .unwrap()
            .execute(&format!("UPDATE {table} SET payload_json=?1"), [raw])
            .unwrap();
        let before = log.events().unwrap();
        drop(log);
        let current = open(&path, ControllerStorageFormat::V3).unwrap();
        assert_eq!(current.events().unwrap(), before);
        drop(current);
        assert!(open(&path, ControllerStorageFormat::V2).is_err());
        assert!(SqliteEventLog::open(&path).is_err());
    }
}

/// Missing bodies, corrupt bytes and mutated reconstruction metadata produce errors, never null State.
#[test]
fn damaged_body_or_reference_is_refused() {
    for mutation in [
        "DELETE FROM telemetry_bodies",
        "UPDATE telemetry_bodies SET value_json='{}'",
        "UPDATE controller_v3_events SET payload_prefix='foreign'",
        "UPDATE controller_v3_events SET body_id='unknown'",
    ] {
        let root = tempfile::tempdir().unwrap();
        let mut log = open(
            &root.path().join("state.sqlite3"),
            ControllerStorageFormat::V3,
        )
        .unwrap();
        put(
            &mut log,
            1,
            observation("one", 1, "epoch", serde_json::json!({"v":1})),
        );
        log.connection
            .lock()
            .unwrap()
            .execute_batch(mutation)
            .unwrap();
        assert!(log.events().is_err(), "{mutation}");
    }
}

/// A rolled back transaction leaves neither an observation nor an orphaned immutable body.
#[test]
fn rollback_removes_private_body_and_reference_together() {
    let root = tempfile::tempdir().unwrap();
    let path = root.path().join("state.sqlite3");
    let mut log = open(&path, ControllerStorageFormat::V3).unwrap();
    log.begin_batch().unwrap();
    put(
        &mut log,
        1,
        observation("one", 1, "epoch", serde_json::json!({"v":1})),
    );
    log.rollback_batch().unwrap();
    assert!(log.events().unwrap().is_empty());
    assert_eq!(
        log.connection
            .lock()
            .unwrap()
            .query_row("SELECT COUNT(*) FROM telemetry_bodies", [], |r| r
                .get::<_, u64>(0))
            .unwrap(),
        0
    );
    log.begin_batch().unwrap();
    put(
        &mut log,
        1,
        observation("one", 1, "epoch", serde_json::json!({"v":1})),
    );
    drop(log);
    assert!(
        open(&path, ControllerStorageFormat::V3)
            .unwrap()
            .events()
            .unwrap()
            .is_empty()
    );
}

/// A failed legacy migration cannot leave a half-renamed table or a committed new generation.
#[test]
fn invalid_source_rolls_back_all_format_changes() {
    let root = tempfile::tempdir().unwrap();
    let path = root.path().join("state.sqlite3");
    let mut log = open(&path, ControllerStorageFormat::Legacy).unwrap();
    put(
        &mut log,
        1,
        observation("one", 1, "epoch", serde_json::json!({"v":1})),
    );
    log.connection
        .lock()
        .unwrap()
        .execute("UPDATE events SET payload_json='broken'", [])
        .unwrap();
    drop(log);
    assert!(open(&path, ControllerStorageFormat::V3).is_err());
    let c = Connection::open(path).unwrap();
    assert_eq!(
        c.pragma_query_value(None, "user_version", |r| r.get::<_, u64>(0))
            .unwrap(),
        0
    );
    assert_eq!(
        c.query_row(
            "SELECT COUNT(*) FROM sqlite_master WHERE name='telemetry_bodies'",
            [],
            |r| r.get::<_, u64>(0)
        )
        .unwrap(),
        0
    );
}

/// Producer type persists separately from an arbitrary command ID and cannot be reassigned on retry.
#[test]
fn journal_classes_preserve_original_receipts_and_rollback() {
    let root = tempfile::tempdir().unwrap();
    let path = root.path().join("state.sqlite3");
    let log = open(&path, ControllerStorageFormat::V3).unwrap();
    for (id, class) in [
        ("looks-like-a-timer", CommandJournalClass::External),
        ("unrelated-id", CommandJournalClass::NodeFacts),
        ("user-looking-id", CommandJournalClass::Timer),
    ] {
        let mut command = ControlCommandJournalEntry::checkpoint("fixture/v1", "{}", 0);
        command.command_id = id.into();
        log.begin_batch().unwrap();
        log.save_checkpoint_with_class("fixture/v1", "{}", command.clone(), class)
            .unwrap();
        log.commit_batch().unwrap();
        let before = log.control_command_journal().unwrap();
        log.begin_batch().unwrap();
        log.save_checkpoint_with_class("fixture/v1", "{}", command.clone(), class)
            .unwrap();
        log.commit_batch().unwrap();
        assert_eq!(before, log.control_command_journal().unwrap());
        log.begin_batch().unwrap();
        let other = if class == CommandJournalClass::External {
            CommandJournalClass::Timer
        } else {
            CommandJournalClass::External
        };
        assert!(
            log.save_checkpoint_with_class("fixture/v1", "{}", command, other)
                .is_err()
        );
        log.rollback_batch().unwrap();
        assert_eq!(before, log.control_command_journal().unwrap());
        let actual: String = log
            .connection
            .lock()
            .unwrap()
            .query_row(
                "SELECT kind FROM command_journal_classes WHERE command_id=?1",
                [id],
                |row| row.get(0),
            )
            .unwrap();
        assert_eq!(actual, class.as_str());
    }
    drop(log);
    let log = open(&path, ControllerStorageFormat::V3).unwrap();
    assert_eq!(log.control_command_journal().unwrap().len(), 3);
    log.begin_batch().unwrap();
    let mut command = ControlCommandJournalEntry::checkpoint("fixture/v1", "{}", 0);
    command.command_id = "uncommitted-class".into();
    log.save_checkpoint_with_class("fixture/v1", "{}", command, CommandJournalClass::Timer)
        .unwrap();
    log.rollback_batch().unwrap();
    assert_eq!(
        log.connection
            .lock()
            .unwrap()
            .query_row("SELECT COUNT(*) FROM command_journal_classes", [], |row| {
                row.get::<_, u64>(0)
            })
            .unwrap(),
        3
    );
}

/// Unclassified migrated rows remain externally protected even when their ID resembles internal work.
#[test]
fn migration_cannot_reclassify_historical_commands() {
    let root = tempfile::tempdir().unwrap();
    let path = root.path().join("state.sqlite3");
    let log = open(&path, ControllerStorageFormat::V2).unwrap();
    let mut command = ControlCommandJournalEntry::checkpoint("fixture/v1", "{}", 0);
    command.command_id = "timer:old-unknown-producer".into();
    log.begin_batch().unwrap();
    log.save_checkpoint_with_command("fixture/v1", "{}", command.clone())
        .unwrap();
    log.commit_batch().unwrap();
    let original = log.control_command_journal().unwrap();
    drop(log);
    let log = open(&path, ControllerStorageFormat::V3).unwrap();
    log.begin_batch().unwrap();
    assert!(
        log.save_checkpoint_with_class("fixture/v1", "{}", command, CommandJournalClass::Timer)
            .is_err()
    );
    log.rollback_batch().unwrap();
    assert_eq!(log.control_command_journal().unwrap(), original);
}

/// Catalog archival capacity depends on protected evidence, never on unrelated telemetry count.
#[test]
fn large_telemetry_history_does_not_block_catalog_preparation() {
    for format in [ControllerStorageFormat::Legacy, ControllerStorageFormat::V3] {
        let root = tempfile::tempdir().unwrap();
        let mut log = open(&root.path().join("state.sqlite3"), format).unwrap();
        log.begin_batch().unwrap();
        for sequence in 1..=16_400 {
            put(
                &mut log,
                sequence,
                observation("one", sequence, "epoch", serde_json::json!({"v":1})),
            );
        }
        log.save_checkpoint("fixture/v1", "{}").unwrap();
        log.commit_batch().unwrap();
        assert!(
            super::super::catalog_archive::current(&log.connection.lock().unwrap())
                .unwrap()
                .is_empty()
        );
    }
}

/// Inline control evidence uses the same byte integrity boundary as reconstructed telemetry.
#[test]
fn inline_event_corruption_is_refused() {
    let root = tempfile::tempdir().unwrap();
    let mut log = open(
        &root.path().join("state.sqlite3"),
        ControllerStorageFormat::V3,
    )
    .unwrap();
    put(
        &mut log,
        1,
        EventPayload::NodeHeartbeatAccepted {
            node_id: NodeId::new("one").unwrap(),
            lease_id: LeaseId::new("lease").unwrap(),
        },
    );
    log.connection
        .lock()
        .unwrap()
        .execute("UPDATE controller_v3_events SET payload_json='{}'", [])
        .unwrap();
    assert!(log.events().is_err());
}

/// Stripping a marker cannot turn reserved v3 authority into a fresh writable legacy database.
#[test]
fn unmarked_reserved_generation_is_never_bootstrapped_as_legacy() {
    for name in [
        "controller_v3_events",
        "telemetry_bodies",
        "catalog_recovery_head",
        "retention_policy",
        "retention_reference_head",
    ] {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("state.sqlite3");
        let connection = Connection::open(&path).unwrap();
        connection
            .execute_batch(&format!(
                "CREATE TABLE {name}(original TEXT); INSERT INTO {name} VALUES('preserved')"
            ))
            .unwrap();
        drop(connection);
        assert!(SqliteEventLog::open(&path).is_err(), "{name}");
        let connection = Connection::open(&path).unwrap();
        assert_eq!(
            connection
                .query_row(
                    "SELECT COUNT(*) FROM sqlite_master WHERE name='events'",
                    [],
                    |row| row.get::<_, u64>(0)
                )
                .unwrap(),
            0
        );
        assert_eq!(
            connection
                .query_row(&format!("SELECT original FROM {name}"), [], |row| row
                    .get::<_, String>(0))
                .unwrap(),
            "preserved"
        );
    }
}

/// Startup does not scan ordinary derived GC rows; explicit audits still refuse an incomplete index.
#[test]
fn derived_reference_audit_is_separate_from_original_state_recovery() {
    let root = tempfile::tempdir().unwrap();
    let path = root.path().join("state.sqlite3");
    let mut log = open(&path, ControllerStorageFormat::V3).unwrap();
    put(
        &mut log,
        1,
        observation("one", 1, "epoch", serde_json::json!({"v":1})),
    );
    assert!(log.audit_retention_reference_index().is_ok());
    drop(log);
    let connection = Connection::open(&path).unwrap();
    connection
        .execute("DELETE FROM retention_reference_records", [])
        .unwrap();
    drop(connection);
    let log = open(&path, ControllerStorageFormat::V3).unwrap();
    assert_eq!(log.events().unwrap().len(), 1);
    assert!(log.audit_retention_reference_index().is_err());
    assert!(log.decision_retention_references(1, 1).is_err());
}
