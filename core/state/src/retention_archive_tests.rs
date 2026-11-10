//! Committed-only policy observations and actual lossless hot relocation through concurrent writes.

use super::*;

/// Opens a real leased v3 Controller database so independent WAL readers share its original inode.
fn fixture(root: &Path) -> SqliteEventLog {
    SqliteEventLog::open_controller_with_checkpoint_policy(
        root.join("state.sqlite3"),
        CheckpointStoragePolicy::Full,
        ControllerStorageFormat::V3,
    )
    .unwrap()
}

/// Persists an explicit policy using the actual SQL batch and checkpoint/journal contract.
fn policy_update(log: &SqliteEventLog, id: &str, revision: u64, mode: RetentionMode, commit: bool) {
    log.begin_batch().unwrap();
    log.update_retention_policy(
        &RetentionPolicyCommand {
            command_id: id.into(),
            expected_revision: revision,
            policy: RetentionPolicy {
                mode,
                hot_window_ms: 10,
                ..RetentionPolicy::default()
            },
        },
        10,
    )
    .unwrap();
    if commit {
        log.save_checkpoint(
            crate::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA,
            &format!("{{\"policy\":\"{id}\"}}"),
        )
        .unwrap();
        log.commit_batch().unwrap();
    }
}

/// Evaluation and Disabled modes never persist an original archive intent or publish any files.
#[test]
fn evaluate_and_disabled_modes_do_not_prepare_effects() {
    let root = tempfile::tempdir().unwrap();
    let log = fixture(root.path());
    append(&log, 1);
    let status = log
        .retention_archive_status(TimestampMs::new(100_000_000))
        .unwrap();
    assert_eq!(
        status.expiry_reference_coverage,
        ArchiveExpiryCoverage::Unknown
    );
    assert!(
        log.prepare_retention_content_archive(
            TimestampMs::new(100_000_000),
            0,
            request(&log, root.path())
        )
        .unwrap()
        .is_none()
    );
    assert!(
        log.archive_operation("prepared-original")
            .unwrap()
            .is_none()
    );
    policy_update(&log, "disabled", 0, RetentionMode::Disabled, true);
    assert!(
        log.prepare_retention_content_archive(
            TimestampMs::new(100_000_000),
            1,
            request(&log, root.path())
        )
        .unwrap()
        .is_none()
    );
    assert!(
        log.archive_operation("prepared-original")
            .unwrap()
            .is_none()
    );
    assert!(!root.path().join("archive").exists());
}

/// The independent policy observer sees committed Enabled through an unrelated uncommitted batch.
#[test]
fn policy_witness_ignores_uncommitted_state_and_refuses_committed_revision_change() {
    let root = tempfile::tempdir().unwrap();
    let log = fixture(root.path());
    append(&log, 1);
    policy_update(&log, "enabled", 0, RetentionMode::Enabled, true);
    let prepared = log
        .prepare_retention_content_archive(TimestampMs::new(100), 1, request(&log, root.path()))
        .unwrap()
        .unwrap();
    let witness = prepared.policy_witness();
    assert_eq!(witness.expected_revision(), 1);
    log.begin_batch().unwrap();
    log.clone().append(
        TimestampMs::new(2),
        &CorrelationId::new("uncommitted-node-fact").unwrap(),
        None,
        EventPayload::NodeHeartbeatAccepted {
            node_id: domain::NodeId::new("node").unwrap(),
            lease_id: domain::LeaseId::new("lease").unwrap(),
        },
    );
    assert!(witness.require_current().is_ok());
    log.rollback_batch().unwrap();
    policy_update(
        &log,
        "uncommitted-disable",
        1,
        RetentionMode::Disabled,
        false,
    );
    assert!(witness.require_current().is_ok());
    log.rollback_batch().unwrap();
    assert!(witness.require_current().is_ok());
    policy_update(&log, "committed-disable", 1, RetentionMode::Disabled, true);
    assert!(witness.require_current().is_err());
    assert_eq!(log.history_floor().unwrap(), 0);
}

/// Full original authority evidence can relocate while newer state commits without expiry claims.
#[test]
fn enabled_hot_relocation_preserves_newer_checkpoint_and_original_history() {
    let root = tempfile::tempdir().unwrap();
    let log = fixture(root.path());
    append_observation(&log, 1, "original");
    policy_update(&log, "enabled", 0, RetentionMode::Enabled, true);
    let prepared = log
        .prepare_retention_content_archive(TimestampMs::new(2000), 1, request(&log, root.path()))
        .unwrap()
        .unwrap();
    assert_eq!(prepared.status().event_count, 1);
    assert_eq!(
        prepared.status().expiry_reference_coverage,
        ArchiveExpiryCoverage::Unknown
    );
    let witness = prepared.policy_witness();
    let sink = Sink::default();
    let published = SqliteEventLog::publish_retention_content_archive(prepared, &sink).unwrap();
    append_observation(&log, 2, "newer");
    assert!(witness.require_current().is_ok());
    let current = log.load_checkpoint().unwrap();
    log.finish_retention_content_archive(published, &sink, TimestampMs::new(2001))
        .unwrap();
    assert_eq!(log.history_floor().unwrap(), 1);
    assert_eq!(log.events().unwrap()[0].sequence, 2);
    assert_eq!(log.load_checkpoint().unwrap(), current);
    let page = log.prepare_event_history_page(None, 100).unwrap();
    assert_eq!(
        SqliteEventLog::read_prepared_event_history_page(page, &sink)
            .unwrap()
            .events[0]
            .sequence,
        1
    );
}

/// Permission changes after publication keep the original pending archive and all hot evidence.
#[test]
fn policy_revocation_before_finalization_refuses_truncation() {
    let root = tempfile::tempdir().unwrap();
    let log = fixture(root.path());
    append(&log, 1);
    policy_update(&log, "enabled", 0, RetentionMode::Enabled, true);
    let prepared = log
        .prepare_retention_content_archive(TimestampMs::new(100), 1, request(&log, root.path()))
        .unwrap()
        .unwrap();
    let sink = Sink::default();
    let published = SqliteEventLog::publish_retention_content_archive(prepared, &sink).unwrap();
    policy_update(&log, "disable", 1, RetentionMode::Disabled, true);
    assert!(
        log.finish_retention_content_archive(published, &sink, TimestampMs::new(101))
            .is_err()
    );
    assert_eq!(log.history_floor().unwrap(), 0);
    assert_eq!(log.len().unwrap(), 1);
    assert!(
        log.archive_operation("prepared-original")
            .unwrap()
            .unwrap()
            .1
            .is_none()
    );
}

/// Replacing the canonical database leaf or committed storage generation invalidates old witnesses.
#[test]
fn original_inode_and_storage_generation_cannot_be_substituted() {
    for replace_path in [false, true] {
        let root = tempfile::tempdir().unwrap();
        let log = fixture(root.path());
        append(&log, 1);
        policy_update(&log, "enabled", 0, RetentionMode::Enabled, true);
        let prepared = log
            .prepare_retention_content_archive(TimestampMs::new(100), 1, request(&log, root.path()))
            .unwrap()
            .unwrap();
        let witness = prepared.policy_witness();
        if replace_path {
            std::fs::rename(
                root.path().join("state.sqlite3"),
                root.path().join("original.sqlite3"),
            )
            .unwrap();
            std::fs::write(root.path().join("state.sqlite3"), b"replacement").unwrap();
        } else {
            log.connection
                .lock()
                .unwrap()
                .execute_batch("PRAGMA user_version=4")
                .unwrap();
        }
        assert!(witness.require_current().is_err());
    }
}

/// Prefix assessment uses the exact policy row/byte hard bounds and never skips a fresh first row.
#[test]
fn hot_age_policy_revision_and_batch_limits_remain_original() {
    let root = tempfile::tempdir().unwrap();
    let log = fixture(root.path());
    append(&log, 1);
    append(&log, 2);
    log.begin_batch().unwrap();
    log.update_retention_policy(
        &RetentionPolicyCommand {
            command_id: "one-event".into(),
            expected_revision: 0,
            policy: RetentionPolicy {
                mode: RetentionMode::Enabled,
                hot_window_ms: 10,
                max_events: 1,
                ..RetentionPolicy::default()
            },
        },
        2,
    )
    .unwrap();
    log.save_checkpoint(
        crate::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA,
        "{\"policy\":1}",
    )
    .unwrap();
    log.commit_batch().unwrap();
    assert!(
        log.prepare_retention_content_archive(TimestampMs::new(100), 0, request(&log, root.path()))
            .is_err()
    );
    assert!(
        log.prepare_retention_content_archive(TimestampMs::new(11), 1, request(&log, root.path()))
            .unwrap()
            .is_none()
    );
    let prepared = log
        .prepare_retention_content_archive(TimestampMs::new(100), 1, request(&log, root.path()))
        .unwrap()
        .unwrap();
    assert_eq!(prepared.status().event_count, 1);
    assert_eq!(prepared.status().end_sequence, Some(1));
}

/// A failed final SQL decision retains the original cutoff even when more history ages meanwhile.
#[test]
fn published_pending_retry_never_selects_a_new_wider_cutoff() {
    let root = tempfile::tempdir().unwrap();
    let log = fixture(root.path());
    append(&log, 1);
    policy_update(&log, "enabled", 0, RetentionMode::Enabled, true);
    let command = request(&log, root.path());
    let prepared = log
        .prepare_retention_content_archive(TimestampMs::new(100), 1, command.clone())
        .unwrap()
        .unwrap();
    let original = prepared.intent().clone();
    let sink = Sink::default();
    let published = SqliteEventLog::publish_retention_content_archive(prepared, &sink).unwrap();
    log.connection.lock().unwrap().execute_batch("CREATE TRIGGER refuse_final BEFORE INSERT ON event_log_metadata WHEN NEW.key='history_floor' BEGIN SELECT RAISE(ABORT,'injected final failure'); END").unwrap();
    assert!(
        log.finish_retention_content_archive(published, &sink, TimestampMs::new(101))
            .is_err()
    );
    log.connection
        .lock()
        .unwrap()
        .execute_batch("DROP TRIGGER refuse_final")
        .unwrap();
    append(&log, 2);
    assert_eq!(
        log.retention_archive_status(TimestampMs::new(200))
            .unwrap()
            .end_sequence,
        Some(2)
    );
    let retry = log
        .prepare_retention_content_archive(TimestampMs::new(200), 1, command)
        .unwrap()
        .unwrap();
    assert_eq!(retry.intent(), &original);
    assert_eq!(retry.status().end_sequence, Some(1));
    let published = SqliteEventLog::publish_retention_content_archive(retry, &sink).unwrap();
    log.finish_retention_content_archive(published, &sink, TimestampMs::new(201))
        .unwrap();
    assert_eq!(sink.copies.load(std::sync::atomic::Ordering::SeqCst), 1);
    assert_eq!(log.history_floor().unwrap(), 1);
    assert_eq!(log.events().unwrap()[0].sequence, 2);
}
