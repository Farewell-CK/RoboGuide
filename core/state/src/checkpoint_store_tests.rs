//! Exact byte reconstruction, transaction cache isolation and corrupt-chain startup rejection.

use super::*;
use ports::EventSink;

/// Produces stable structured UTF-8 bytes with insertion shifts and sparse changing evidence.
fn snapshot(revision: u64) -> String {
    serde_json::json!({
        "source": "原始证据不会在重启后变成 fresh",
        "records": (0..128).map(|index| serde_json::json!({
            "id": format!("record-{index:04}"),
            "data": format!("{}{}", index, "immutable-original-source-".repeat(80)),
            "revision": if index == 17 { revision } else { 0 },
        })).collect::<Vec<_>>(),
    })
    .to_string()
}

/// Writes through the public atomic API and returns the actual attempted representation size.
fn save(log: &SqliteEventLog, schema: &str, text: &str) -> CheckpointWriteStats {
    log.begin_batch().unwrap();
    log.save_checkpoint(schema, text).unwrap();
    let stats = log.checkpoint_write_stats().unwrap();
    log.commit_batch().unwrap();
    stats
}

/// New chains and readonly inspection reconstruct exact UTF-8 bytes across shifted sparse changes.
#[test]
fn incremental_checkpoint_round_trip_and_readonly_inspection() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("events.sqlite3");
    let log =
        SqliteEventLog::open_with_checkpoint_policy(&path, CheckpointStoragePolicy::Incremental)
            .unwrap();
    let first = snapshot(0);
    assert!(!save(&log, "checkpoint/v1", &first).delta);
    for revision in [1, 99, 100, 1000] {
        let next = snapshot(revision);
        let stats = save(&log, "checkpoint/v1", &next);
        assert!(stats.delta);
        assert!(stats.encoded_bytes < next.len() as u64 / 10);
        assert_eq!(
            log.load_checkpoint().unwrap().unwrap().checkpoint_json,
            next
        );
        assert_eq!(
            SqliteEventLog::read_checkpoint(&path)
                .unwrap()
                .unwrap()
                .checkpoint_json,
            next
        );
    }
    let latest = log.load_checkpoint().unwrap().unwrap();
    drop(log);
    let reopened = SqliteEventLog::open(&path).unwrap();
    assert_eq!(reopened.load_checkpoint().unwrap().unwrap(), latest);
}

/// A legacy full checkpoint migrates inside its first delta transaction, including unchanged event tails.
#[test]
fn legacy_migration_and_full_mode_preserve_the_downgrade_fence() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("events.sqlite3");
    let legacy = SqliteEventLog::open(&path).unwrap();
    save(&legacy, "checkpoint/v1", &snapshot(0));
    drop(legacy);
    let log =
        SqliteEventLog::open_with_checkpoint_policy(&path, CheckpointStoragePolicy::Incremental)
            .unwrap();
    assert!(save(&log, "checkpoint/v1", &snapshot(1)).delta);
    let sql = Connection::open(&path).unwrap();
    assert_eq!(
        sql.query_row("SELECT schema FROM controller_checkpoint", [], |row| row
            .get::<_, String>(
            0
        ))
        .unwrap(),
        CHAIN_SCHEMA
    );
    assert_eq!(log.load_checkpoint().unwrap().unwrap().event_sequence, 0);
    drop(log);
    let full = SqliteEventLog::open(&path).unwrap();
    assert!(!save(&full, "checkpoint/v2", &snapshot(2)).delta);
    assert_eq!(
        full.load_checkpoint().unwrap().unwrap().schema,
        "checkpoint/v2"
    );
    assert_eq!(
        sql.query_row("SELECT schema FROM controller_checkpoint", [], |row| row
            .get::<_, String>(
            0
        ))
        .unwrap(),
        CHAIN_SCHEMA
    );
    assert_eq!(
        sql.query_row(
            "SELECT COUNT(*) FROM controller_checkpoint_deltas",
            [],
            |row| row.get::<_, u64>(0)
        )
        .unwrap(),
        0
    );
}

/// Nested rollback restores both SQL representation and the transaction-pending complete cache.
#[test]
fn nested_savepoint_checkpoint_cache_rollback_is_exact() {
    let directory = tempfile::tempdir().unwrap();
    let log = SqliteEventLog::open_with_checkpoint_policy(
        directory.path().join("events.sqlite3"),
        CheckpointStoragePolicy::Incremental,
    )
    .unwrap();
    save(&log, "checkpoint/v1", &snapshot(0));
    let expected = snapshot(1);
    log.begin_batch().unwrap();
    log.save_checkpoint("checkpoint/v1", &expected).unwrap();
    assert!(
        log.with_input_savepoint(|| {
            log.save_checkpoint("checkpoint/v1", &snapshot(2)).unwrap();
            assert!(
                log.with_transition_savepoint(|| {
                    log.save_checkpoint("checkpoint/v1", &snapshot(3)).unwrap();
                    Err::<(), _>("Mission rollback")
                })
                .unwrap()
                .is_err()
            );
            Err::<(), _>("input rollback")
        })
        .unwrap()
        .is_err()
    );
    log.commit_batch().unwrap();
    assert_eq!(
        log.load_checkpoint().unwrap().unwrap().checkpoint_json,
        expected
    );
    log.begin_batch().unwrap();
    log.save_checkpoint("checkpoint/v1", &snapshot(4)).unwrap();
    log.rollback_batch().unwrap();
    save(&log, "checkpoint/v1", &snapshot(5));
    assert_eq!(
        log.load_checkpoint().unwrap().unwrap().checkpoint_json,
        snapshot(5)
    );
}

/// Evidence and delta rows roll back together and later commits keep the exact restored sequence.
#[test]
fn incremental_checkpoint_and_events_commit_atomically() {
    let directory = tempfile::tempdir().unwrap();
    let log = SqliteEventLog::open_with_checkpoint_policy(
        directory.path().join("events.sqlite3"),
        CheckpointStoragePolicy::Incremental,
    )
    .unwrap();
    save(&log, "checkpoint/v1", &snapshot(0));
    for commit in [false, true] {
        log.begin_batch().unwrap();
        log.clone().append(
            TimestampMs::new(1),
            &CorrelationId::new("atomic-delta").unwrap(),
            None,
            EventPayload::ExecutionGroupBlocked {
                group_id: domain::ExecutionGroupId::new("group").unwrap(),
                task_ref: domain::TaskRef::new(
                    domain::MissionId::new("mission").unwrap(),
                    domain::TaskId::new("task").unwrap(),
                ),
                reason: "retained physical ambiguity".into(),
            },
        );
        log.save_checkpoint("checkpoint/v1", &snapshot(1)).unwrap();
        if commit {
            log.commit_batch().unwrap();
        } else {
            log.rollback_batch().unwrap();
        }
        assert_eq!(
            log.load_checkpoint().unwrap().unwrap().event_sequence,
            u64::from(commit)
        );
        assert_eq!(log.latest_sequence().unwrap(), u64::from(commit));
    }
    assert_eq!(log.events().unwrap()[0].event_id, "event-1");
}

/// Compaction failure cannot publish a new baseline, erase its predecessor or retain a candidate cache.
#[test]
fn compaction_sql_failure_restores_prior_chain() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("events.sqlite3");
    let log =
        SqliteEventLog::open_with_checkpoint_policy(&path, CheckpointStoragePolicy::Incremental)
            .unwrap();
    save(&log, "checkpoint/v1", &snapshot(0));
    save(&log, "checkpoint/v1", &snapshot(1));
    let before = log.load_checkpoint().unwrap().unwrap();
    let sql = Connection::open(path).unwrap();
    sql.execute_batch("CREATE TRIGGER reject_compaction BEFORE DELETE ON controller_checkpoint_deltas BEGIN SELECT RAISE(ABORT, 'compaction failure'); END").unwrap();
    log.begin_batch().unwrap();
    assert!(log.save_checkpoint("checkpoint/v2", &snapshot(2)).is_err());
    log.rollback_batch().unwrap();
    assert_eq!(log.load_checkpoint().unwrap().unwrap(), before);
    sql.execute_batch("DROP TRIGGER reject_compaction").unwrap();
    save(&log, "checkpoint/v1", &snapshot(3));
    assert_eq!(
        log.load_checkpoint().unwrap().unwrap().checkpoint_json,
        snapshot(3)
    );
}

/// Long same-event chains compact within bounds and every intermediate result remains exactly readable.
#[test]
fn bounded_chain_compacts_without_losing_any_complete_result() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("events.sqlite3");
    let log =
        SqliteEventLog::open_with_checkpoint_policy(&path, CheckpointStoragePolicy::Incremental)
            .unwrap();
    let sql = Connection::open(path).unwrap();
    let mut full = 0;
    for revision in 0..40 {
        let next = snapshot(revision);
        full += usize::from(!save(&log, "checkpoint/v1", &next).delta);
        assert_eq!(
            log.load_checkpoint().unwrap().unwrap().checkpoint_json,
            next
        );
        assert!(
            sql.query_row(
                "SELECT COUNT(*) FROM controller_checkpoint_deltas",
                [],
                |row| row.get::<_, usize>(0)
            )
            .unwrap()
                <= MAX_DELTAS
        );
    }
    assert!(full >= 2);
}

/// Corruption of identity, sequencing, source bytes or reconstruction instructions fails closed on reopen.
#[test]
fn corrupt_checkpoint_chains_cannot_be_restored() {
    for corruption in [
        "DELETE FROM controller_checkpoint_deltas WHERE revision = 2",
        "UPDATE controller_checkpoint_deltas SET previous_revision = 0",
        "UPDATE controller_checkpoint_deltas SET schema = 'wrong/v1'",
        "UPDATE controller_checkpoint_deltas SET base_digest = 'wrong'",
        "UPDATE controller_checkpoint_deltas SET digest = 'wrong'",
        "UPDATE controller_checkpoint_head SET event_sequence = 99",
        "UPDATE controller_checkpoint_head SET revision = 99",
        "UPDATE controller_checkpoint SET checkpoint_json = checkpoint_json || 'x'",
        "UPDATE controller_checkpoint_deltas SET delta_json = '{\"codec\":\"unknown\",\"length\":0,\"pieces\":[]}'",
        "UPDATE controller_checkpoint_deltas SET delta_json = '{\"codec\":\"roboguide.checkpoint-byte-delta/v1\",\"length\":1,\"pieces\":[{\"kind\":\"copy\",\"offset\":99999999,\"length\":1}]}'",
    ] {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("events.sqlite3");
        let log = SqliteEventLog::open_with_checkpoint_policy(
            &path,
            CheckpointStoragePolicy::Incremental,
        )
        .unwrap();
        save(&log, "checkpoint/v1", &snapshot(0));
        assert!(save(&log, "checkpoint/v1", &snapshot(1)).delta);
        drop(log);
        Connection::open(&path)
            .unwrap()
            .execute_batch(corruption)
            .unwrap();
        assert!(
            SqliteEventLog::read_checkpoint(&path).is_err(),
            "{corruption}"
        );
        assert!(SqliteEventLog::open(&path).is_err(), "{corruption}");
    }
}

/// Sparse changes at several positions exercise shifted middle copies as well as exact prefix/suffix reuse.
#[test]
fn multiple_changed_fields_keep_exact_middle_reconstruction() {
    let directory = tempfile::tempdir().unwrap();
    let log = SqliteEventLog::open_with_checkpoint_policy(
        directory.path().join("events.sqlite3"),
        CheckpointStoragePolicy::Incremental,
    )
    .unwrap();
    let original = snapshot(1);
    save(&log, "checkpoint/v1", &original);
    let mut value: serde_json::Value = serde_json::from_str(&original).unwrap();
    value["records"][5]["revision"] = serde_json::json!(123456);
    value["records"][91]["data"] = serde_json::json!("不同长度的原始来源".repeat(32));
    value["records"][100]["revision"] = serde_json::json!(789);
    let next = value.to_string();
    let stats = save(&log, "checkpoint/v1", &next);
    assert!(stats.delta);
    assert_eq!(
        log.load_checkpoint().unwrap().unwrap().checkpoint_json,
        next
    );
}
