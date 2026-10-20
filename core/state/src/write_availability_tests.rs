//! Actual SQLite failures and unchanged-checkpoint recovery, independent of physical evidence.

use super::*;

/// Opens a real store and commits original source/deadline bytes before injecting storage faults.
fn original_store(
    directory: &tempfile::TempDir,
    policy: CheckpointStoragePolicy,
) -> SqliteEventLog {
    let log = SqliteEventLog::open_with_checkpoint_policy(
        directory.path().join("events.sqlite3"),
        policy,
    )
    .unwrap()
    .with_write_failure_fence("original-process")
    .unwrap();
    log.begin_batch().unwrap();
    log.save_checkpoint(
        "test.checkpoint/v1",
        r#"{"received_at":10,"deadline":20,"value":42}"#,
    )
    .unwrap();
    log.commit_batch().unwrap();
    log
}

/// Freezes exactly the status returned by the failed original process, without a new budget.
fn recovery_command(log: &SqliteEventLog, id: &str) -> WriteRecoveryCommand {
    let status = log.write_status().unwrap();
    WriteRecoveryCommand {
        schema_version: "roboguide.authority-write-recovery/v0.1".into(),
        command_id: id.into(),
        process_id: status.process_id.unwrap(),
        generation: status.generation,
        checkpoint_identity: status.checkpoint_identity.unwrap(),
        authority_revision: status.authority_revision,
    }
}

/// A real save failure fences every clone; actual unchanged writes must Commit before clearing it.
#[test]
fn actual_sql_failure_requires_an_exact_durable_recovery_probe() {
    for policy in [
        CheckpointStoragePolicy::Full,
        CheckpointStoragePolicy::Incremental,
    ] {
        let directory = tempfile::tempdir().unwrap();
        let log = original_store(&directory, policy);
        let original = log.committed_checkpoint().unwrap();
        let connection = log.connection.lock().unwrap();
        connection.execute_batch("CREATE TRIGGER fail_checkpoint BEFORE INSERT ON controller_checkpoint BEGIN SELECT RAISE(ABORT,'actual checkpoint fault'); END;
            CREATE TRIGGER fail_chain BEFORE INSERT ON controller_checkpoint_head BEGIN SELECT RAISE(ABORT,'actual checkpoint fault'); END").unwrap();
        drop(connection);
        log.begin_batch().unwrap();
        assert!(
            log.save_checkpoint("test.checkpoint/v1", r#"{"value":99}"#)
                .is_err()
        );
        log.rollback_batch().unwrap();
        let status = log.write_status().unwrap();
        assert!(!status.write_enabled);
        assert!(!status.failure.as_ref().unwrap().restart_required);
        assert!(log.clone().begin_batch().is_err());
        assert!(log.readiness().is_err());
        assert!(
            log.record_service_access("artifact", "reader", "GET", "/bytes", "allow")
                .is_err()
        );
        let command = recovery_command(&log, "repair-original");
        assert!(log.recover_writes(&command, "original-admin").is_err());
        assert_eq!(log.write_status().unwrap(), status);
        assert_eq!(log.control_command_count().unwrap(), 1);
        log.connection
            .lock()
            .unwrap()
            .execute_batch("DROP TRIGGER fail_checkpoint; DROP TRIGGER fail_chain")
            .unwrap();
        let receipt = log.recover_writes(&command, "original-admin").unwrap();
        assert_eq!(receipt.authority_revision, 2);
        assert!(log.write_status().unwrap().write_enabled);
        assert_eq!(log.control_command_count().unwrap(), 2);
        assert_eq!(log.load_checkpoint().unwrap(), original);
        assert_eq!(log.committed_checkpoint().unwrap(), original);
        assert_eq!(log.latest_sequence().unwrap(), 0);
        assert_eq!(
            log.recover_writes(&command, "original-admin").unwrap(),
            receipt
        );
        assert_eq!(log.control_command_count().unwrap(), 2);
        let mut changed = command.clone();
        changed.generation += 1;
        assert!(log.recover_writes(&changed, "original-admin").is_err());
        assert!(log.recover_writes(&command, "different-admin").is_err());
        log.readiness().unwrap();
    }
}

/// A historical success remains observable but cannot clear a subsequent process-local failure.
#[test]
fn stale_receipts_never_clear_a_new_generation_or_process() {
    let directory = tempfile::tempdir().unwrap();
    let log = original_store(&directory, CheckpointStoragePolicy::Full);
    log.disable_writes("original storage failure", false)
        .unwrap();
    let command = recovery_command(&log, "original-repair");
    let receipt = log.recover_writes(&command, "admin").unwrap();
    log.disable_writes("second failure", false).unwrap();
    let second = log.write_status().unwrap();
    assert_eq!(second.generation, 2);
    assert_eq!(log.recover_writes(&command, "admin").unwrap(), receipt);
    assert_eq!(log.write_status().unwrap(), second);
    drop(log);
    let restored = SqliteEventLog::open(directory.path().join("events.sqlite3"))
        .unwrap()
        .with_write_failure_fence("new-process")
        .unwrap();
    restored
        .disable_writes("new process failure", false)
        .unwrap();
    let current = restored.write_status().unwrap();
    assert_eq!(restored.recover_writes(&command, "admin").unwrap(), receipt);
    assert_eq!(restored.write_status().unwrap(), current);
    let mut replacement = command;
    replacement.command_id = "new-command-with-old-purpose".into();
    assert!(restored.recover_writes(&replacement, "admin").is_err());
}

/// Same snapshot bytes cannot hide a separately advanced zero-event authority journal head.
#[test]
fn changed_authority_high_water_and_untracked_sql_are_refused() {
    let directory = tempfile::tempdir().unwrap();
    let log = original_store(&directory, CheckpointStoragePolicy::Full);
    log.disable_writes("original fault", false).unwrap();
    let command = recovery_command(&log, "repair");
    log.connection
        .lock()
        .unwrap()
        .execute_batch("BEGIN IMMEDIATE")
        .unwrap();
    assert!(log.recover_writes(&command, "admin").is_err());
    log.connection
        .lock()
        .unwrap()
        .execute_batch("ROLLBACK")
        .unwrap();
    log.connection
        .lock()
        .unwrap()
        .execute_batch(
            "UPDATE control_command_journal SET authority_revision=2;
        UPDATE event_log_metadata SET value='2' WHERE key='journal_committed_revision'",
        )
        .unwrap();
    assert!(log.load_checkpoint().is_ok());
    assert!(log.recover_writes(&command, "admin").is_err());
    assert!(!log.write_status().unwrap().write_enabled);
}

/// Explicit unclassified/shared errors require startup repair and preserve their first evidence.
#[test]
fn first_failure_and_restart_requirement_are_not_replaced_by_retries() {
    let directory = tempfile::tempdir().unwrap();
    let log = original_store(&directory, CheckpointStoragePolicy::Full);
    log.disable_writes("shared invariant failure", true)
        .unwrap();
    let original = log.write_status().unwrap();
    log.disable_writes("later rejection", false).unwrap();
    assert_eq!(log.write_status().unwrap(), original);
    assert!(original.failure.unwrap().restart_required);
    assert!(
        log.recover_writes(&recovery_command(&log, "repair"), "admin")
            .is_err()
    );
    assert_eq!(log.control_command_count().unwrap(), 1);
}

/// SQL failure at the final Commit preparation rolls back both checkpoint and command evidence.
#[test]
fn actual_commit_failure_does_not_publish_its_checkpoint_candidate() {
    let directory = tempfile::tempdir().unwrap();
    let log = original_store(&directory, CheckpointStoragePolicy::Full);
    let original = log.committed_checkpoint().unwrap();
    log.connection.lock().unwrap().execute_batch("CREATE TRIGGER fail_commit BEFORE INSERT ON event_log_metadata WHEN NEW.key='journal_committed_revision' BEGIN SELECT RAISE(ABORT,'actual commit fault'); END").unwrap();
    log.begin_batch().unwrap();
    log.save_checkpoint("test.checkpoint/v1", r#"{"value":99}"#)
        .unwrap();
    assert!(log.commit_batch().is_err());
    log.rollback_batch().unwrap();
    assert_eq!(log.load_checkpoint().unwrap(), original);
    assert_eq!(log.committed_checkpoint().unwrap(), original);
    assert!(!log.write_status().unwrap().write_enabled);
    assert_eq!(log.write_status().unwrap().authority_revision, 1);
}

/// A failed append retained by EventSink is cleared only by the successfully committed probe.
#[test]
fn recovered_append_does_not_replay_its_old_process_error() {
    let directory = tempfile::tempdir().unwrap();
    let mut log = original_store(&directory, CheckpointStoragePolicy::Full);
    log.connection.lock().unwrap().execute_batch("CREATE TRIGGER fail_event BEFORE INSERT ON events BEGIN SELECT RAISE(ABORT,'actual append fault'); END").unwrap();
    log.begin_batch().unwrap();
    log.append(
        TimestampMs::new(10),
        &CorrelationId::new("original").unwrap(),
        None,
        EventPayload::ExecutionGroupBlocked {
            group_id: domain::ExecutionGroupId::new("original-group").unwrap(),
            task_ref: domain::TaskRef::new(
                domain::MissionId::new("original-mission").unwrap(),
                domain::TaskId::new("original-task").unwrap(),
            ),
            reason: "unit event, never device evidence".into(),
        },
    );
    log.rollback_batch().unwrap();
    assert!(log.last_error.lock().unwrap().is_some());
    log.connection
        .lock()
        .unwrap()
        .execute_batch("DROP TRIGGER fail_event")
        .unwrap();
    log.recover_writes(&recovery_command(&log, "repair-append"), "admin")
        .unwrap();
    assert!(log.take_error().unwrap().is_none());
    assert!(log.events().unwrap().is_empty());
}

/// Unit-only unreachable replica exercises denial; it never supplies successful quorum evidence.
struct UnavailableReplica;

impl ApplicationBatchReplicator for UnavailableReplica {
    /// Identifies only the negative port fixture, never a running cluster.
    fn profile_identity(&self) -> &str {
        "unit-unavailable-replica"
    }
    /// Always refuses epoch/quorum authority.
    fn assert_write_authority(&self) -> Result<(), String> {
        Err("quorum unavailable".into())
    }
    /// No candidate is accepted by this negative fixture.
    fn confirm_batch(&self, _batch: ApplicationCommitBatch) -> Result<(), String> {
        Err("quorum unavailable".into())
    }
}

/// A local unchanged snapshot cannot authorize private reads or recovery of uncertain replica state.
#[test]
fn unavailable_replica_denies_content_and_requires_startup_replay() {
    let directory = tempfile::tempdir().unwrap();
    let log = original_store(&directory, CheckpointStoragePolicy::Full)
        .with_replicator(Arc::new(UnavailableReplica));
    assert!(log.require_read_authority().is_err());
    assert!(log.begin_batch().is_err());
    assert!(
        log.write_status()
            .unwrap()
            .failure
            .unwrap()
            .restart_required
    );
    assert!(
        log.recover_writes(&recovery_command(&log, "repair-replica"), "admin")
            .is_err()
    );
    assert_eq!(log.control_command_count().unwrap(), 1);
}
