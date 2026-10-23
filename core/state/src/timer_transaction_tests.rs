//! Actual SQL no-op boundaries; synthetic replication counting is never a quorum receipt.

use super::*;

/// Durable audit-only requests preserve the exact authority snapshot without new authority commands.
#[test]
fn actual_durable_audit_does_not_force_an_unchanged_authority_commit() {
    let directory = tempfile::tempdir().unwrap();
    let log = SqliteEventLog::open(directory.path().join("audit.sqlite3")).unwrap();
    let raw = "{\"original\":true}";
    log.begin_batch().unwrap();
    save(&log, "original-command", raw);
    let original = log.control_command_journal().unwrap();
    for _ in 0..4 {
        log.record_service_access(
            "artifact",
            "original-principal",
            "GET",
            "/original",
            "allowed",
        )
        .unwrap();
        log.begin_batch().unwrap();
        assert!(
            log.rollback_unchanged_timer_batch("timer-unit/v1", raw)
                .unwrap()
        );
    }
    assert_eq!(log.control_command_journal().unwrap(), original);
    assert_eq!(
        log.connection
            .lock()
            .unwrap()
            .query_row("SELECT COUNT(*) FROM service_access_audit", [], |r| r
                .get::<_, u64>(0))
            .unwrap(),
        4
    );
}

/// Same-connection DDL does not increment total_changes but still invalidates the authority witness.
#[test]
fn audit_cannot_conceal_same_connection_schema_changes() {
    let directory = tempfile::tempdir().unwrap();
    let log = SqliteEventLog::open(directory.path().join("schema.sqlite3")).unwrap();
    let raw = "{\"original\":true}";
    log.begin_batch().unwrap();
    save(&log, "original-command", raw);
    log.connection
        .lock()
        .unwrap()
        .execute_batch("CREATE TABLE unaccounted_schema(original TEXT)")
        .unwrap();
    log.record_service_access(
        "artifact",
        "original-principal",
        "GET",
        "/original",
        "allowed",
    )
    .unwrap();
    log.begin_batch().unwrap();
    assert!(
        !log.rollback_unchanged_timer_batch("timer-unit/v1", raw)
            .unwrap()
    );
    log.rollback_batch().unwrap();
}

/// Trigger effects cannot borrow the audit-only exception, even after the trigger was fully committed.
#[test]
fn audit_trigger_extra_authority_write_invalidates_unchanged_witness() {
    let directory = tempfile::tempdir().unwrap();
    let log = SqliteEventLog::open(directory.path().join("trigger.sqlite3")).unwrap();
    log.connection.lock().unwrap().execute_batch("CREATE TRIGGER actual_audit_side_effect AFTER INSERT ON service_access_audit BEGIN INSERT OR REPLACE INTO event_log_metadata(key,value) VALUES('unaccounted-effect','original'); END").unwrap();
    let raw = "{\"original\":true}";
    log.begin_batch().unwrap();
    save(&log, "original-command", raw);
    log.record_service_access(
        "artifact",
        "original-principal",
        "GET",
        "/original",
        "allowed",
    )
    .unwrap();
    log.begin_batch().unwrap();
    assert!(
        !log.rollback_unchanged_timer_batch("timer-unit/v1", raw)
            .unwrap()
    );
    log.rollback_batch().unwrap();
}

/// An audit INSERT never conceals either a previous own mutation or an external committed change.
#[test]
fn audit_does_not_hide_prior_unaccounted_or_external_changes() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("original.sqlite3");
    let log = SqliteEventLog::open(&path).unwrap();
    let raw = "{\"original\":true}";
    log.begin_batch().unwrap();
    save(&log, "original-command", raw);
    log.connection
        .lock()
        .unwrap()
        .execute(
            "INSERT INTO event_log_metadata(key,value) VALUES('unaccounted-own','change')",
            [],
        )
        .unwrap();
    log.record_service_access(
        "artifact",
        "original-principal",
        "GET",
        "/original",
        "allowed",
    )
    .unwrap();
    log.begin_batch().unwrap();
    assert!(
        !log.rollback_unchanged_timer_batch("timer-unit/v1", raw)
            .unwrap()
    );
    save(&log, "record-original-change", raw);
    let external = Connection::open(&path).unwrap();
    external
        .execute(
            "INSERT INTO event_log_metadata(key,value) VALUES('unaccounted-external','change')",
            [],
        )
        .unwrap();
    log.record_service_access(
        "artifact",
        "original-principal",
        "GET",
        "/original",
        "allowed",
    )
    .unwrap();
    log.begin_batch().unwrap();
    assert!(
        !log.rollback_unchanged_timer_batch("timer-unit/v1", raw)
            .unwrap()
    );
    log.rollback_batch().unwrap();
}

/// Isolated port records original calls without representing an infrastructure deployment.
#[derive(Default)]
struct CountedPort {
    /// Complete candidates actually passed to the synthetic metadata port.
    batches: Mutex<Vec<ApplicationCommitBatch>>,
}

impl ApplicationBatchReplicator for CountedPort {
    /// Supplies an explicit test-only identity without claiming current quorum.
    fn profile_identity(&self) -> &str {
        "synthetic-timer-counter"
    }
    /// The unit fixture admits SQL work; native process gates independently check actual quorum.
    fn assert_write_authority(&self) -> Result<(), String> {
        Ok(())
    }
    /// Validates and retains the actual supplied candidate for independent call-count assertions.
    fn confirm_batch(&self, batch: ApplicationCommitBatch) -> Result<(), String> {
        batch.validate().map_err(|error| error.to_string())?;
        self.batches.lock().unwrap().push(batch);
        Ok(())
    }
}

/// Persists one explicit original command, preserving same-checkpoint external command identities.
fn save(log: &SqliteEventLog, id: &str, raw: &str) {
    let mut command = ControlCommandJournalEntry::checkpoint(
        "timer-unit/v1",
        raw,
        log.current_batch_event_sequence().unwrap(),
    );
    command.command_id = id.into();
    log.save_checkpoint_with_command("timer-unit/v1", raw, command)
        .unwrap();
    log.commit_batch().unwrap();
}

/// Only exact untouched transactions disappear; explicit commands and raw-byte changes retain history.
#[test]
fn actual_unchanged_timer_never_replicates_or_erases_explicit_zero_event_commands() {
    let directory = tempfile::tempdir().unwrap();
    let port = Arc::new(CountedPort::default());
    let log = SqliteEventLog::open(directory.path().join("original.sqlite3"))
        .unwrap()
        .with_replicator(port.clone());
    let raw = r#"{"value":1.00,"permission":"unchanged"}"#;
    log.begin_batch().unwrap();
    assert!(
        !log.rollback_unchanged_timer_batch("timer-unit/v1", raw)
            .unwrap()
    );
    save(&log, "first", raw);
    let original = log.control_command_journal().unwrap();
    for _ in 0..8 {
        log.begin_batch().unwrap();
        assert!(
            log.rollback_unchanged_timer_batch("timer-unit/v1", raw)
                .unwrap()
        );
        assert_eq!(log.control_command_journal().unwrap(), original);
        assert_eq!(port.batches.lock().unwrap().len(), 1);
    }
    log.begin_batch().unwrap();
    save(&log, "explicit-external-same-checkpoint", raw);
    assert_eq!(log.control_command_journal().unwrap().len(), 2);
    assert_eq!(port.batches.lock().unwrap().len(), 2);
    let changed = r#"{"value":1.0,"permission":"unchanged"}"#;
    log.begin_batch().unwrap();
    assert!(
        !log.rollback_unchanged_timer_batch("timer-unit/v1", changed)
            .unwrap()
    );
    save(&log, "exact-raw-byte-change", changed);
    assert_eq!(port.batches.lock().unwrap().len(), 3);
    assert!(
        port.batches
            .lock()
            .unwrap()
            .iter()
            .all(|batch| batch.events.is_empty())
    );
    assert_eq!(
        log.load_checkpoint().unwrap().unwrap().checkpoint_json,
        changed
    );
}

/// Actual writes remain significant even after their savepoint rolls back to identical visible values.
#[test]
fn actual_sql_and_rolled_back_writes_cannot_be_hidden_by_equal_checkpoint_bytes() {
    let directory = tempfile::tempdir().unwrap();
    let log = SqliteEventLog::open(directory.path().join("original.sqlite3")).unwrap();
    let raw = r#"{"original":"state"}"#;
    log.begin_batch().unwrap();
    save(&log, "first", raw);
    log.begin_batch().unwrap();
    log.connection.lock().unwrap().execute(
        "INSERT INTO event_log_metadata(key,value) VALUES ('actual-zero-event-change','original')", [],
    ).unwrap();
    assert!(
        !log.rollback_unchanged_timer_batch("timer-unit/v1", raw)
            .unwrap()
    );
    save(&log, "actual-auxiliary-sql-change", raw);
    assert_eq!(
        log.connection
            .lock()
            .unwrap()
            .query_row(
                "SELECT value FROM event_log_metadata WHERE key='actual-zero-event-change'",
                [],
                |row| row.get::<_, String>(0),
            )
            .unwrap(),
        "original"
    );
    log.begin_batch().unwrap();
    log.connection.lock().unwrap().execute_batch(
        "SAVEPOINT actual_original; UPDATE event_log_metadata SET value='changed' WHERE key='actual-zero-event-change'; ROLLBACK TO actual_original; RELEASE actual_original",
    ).unwrap();
    assert!(
        !log.rollback_unchanged_timer_batch("timer-unit/v1", raw)
            .unwrap()
    );
    log.rollback_batch().unwrap();
    log.begin_batch().unwrap();
    assert!(
        !log.rollback_unchanged_timer_batch("timer-unit/v1", raw)
            .unwrap()
    );
    save(&log, "actual-complete-after-rollback", raw);
    log.begin_batch().unwrap();
    assert!(
        log.rollback_unchanged_timer_batch("timer-unit/v1", raw)
            .unwrap()
    );
    assert_eq!(log.control_command_journal().unwrap().len(), 3);
}

/// Pending commands and events cannot disappear even when their checkpoint text is identical.
#[test]
fn actual_pending_command_and_event_each_refuse_timer_elision() {
    let directory = tempfile::tempdir().unwrap();
    let mut log = SqliteEventLog::open(directory.path().join("original.sqlite3")).unwrap();
    let raw = r#"{"original":"state"}"#;
    log.begin_batch().unwrap();
    save(&log, "first", raw);
    log.begin_batch().unwrap();
    let mut command = ControlCommandJournalEntry::checkpoint("timer-unit/v1", raw, 0);
    command.command_id = "actual-pending-command".into();
    log.save_checkpoint_with_command("timer-unit/v1", raw, command)
        .unwrap();
    assert!(
        !log.rollback_unchanged_timer_batch("timer-unit/v1", raw)
            .unwrap()
    );
    log.commit_batch().unwrap();
    log.begin_batch().unwrap();
    log.append(
        TimestampMs::new(7),
        &CorrelationId::new("actual-event").unwrap(),
        None,
        EventPayload::MissionSchedulingPriorityChanged {
            mission_id: domain::MissionId::new("unit-mission").unwrap(),
            priority: 7,
        },
    );
    assert!(log.take_error().unwrap().is_none());
    assert!(
        !log.rollback_unchanged_timer_batch("timer-unit/v1", raw)
            .unwrap()
    );
    save(&log, "actual-event-change", raw);
    assert_eq!(log.events().unwrap().len(), 1);
    assert_eq!(log.control_command_journal().unwrap().len(), 3);
}

/// Auxiliary writes before BEGIN and commits from another actual connection must reach a full Commit.
#[test]
fn actual_prior_auxiliary_and_external_sql_changes_cannot_borrow_an_old_checkpoint_witness() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("original.sqlite3");
    let log = SqliteEventLog::open(&path).unwrap();
    let raw = r#"{"original":"state"}"#;
    log.begin_batch().unwrap();
    save(&log, "first", raw);
    log.connection
        .lock()
        .unwrap()
        .execute(
            "INSERT INTO event_log_metadata(key,value) VALUES ('actual-auxiliary','before-begin')",
            [],
        )
        .unwrap();
    log.begin_batch().unwrap();
    assert!(
        !log.rollback_unchanged_timer_batch("timer-unit/v1", raw)
            .unwrap()
    );
    save(&log, "actual-prior-auxiliary", raw);
    let external = Connection::open(&path).unwrap();
    external
        .execute(
            "UPDATE event_log_metadata SET value='external-commit' WHERE key='actual-auxiliary'",
            [],
        )
        .unwrap();
    drop(external);
    log.begin_batch().unwrap();
    assert!(
        !log.rollback_unchanged_timer_batch("timer-unit/v1", raw)
            .unwrap()
    );
    save(&log, "actual-external-commit", raw);
    log.begin_batch().unwrap();
    assert!(
        log.rollback_unchanged_timer_batch("timer-unit/v1", raw)
            .unwrap()
    );
    assert_eq!(log.control_command_journal().unwrap().len(), 3);
    drop(log);
    let restarted = SqliteEventLog::open(&path).unwrap();
    restarted.begin_batch().unwrap();
    assert!(
        !restarted
            .rollback_unchanged_timer_batch("timer-unit/v1", raw)
            .unwrap(),
        "restart invented a previously confirmed complete auxiliary baseline"
    );
    save(&restarted, "actual-restart-complete-checkpoint", raw);
    restarted.begin_batch().unwrap();
    assert!(
        restarted
            .rollback_unchanged_timer_batch("timer-unit/v1", raw)
            .unwrap()
    );
}
