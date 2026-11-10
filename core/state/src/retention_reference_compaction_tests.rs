//! Exact TTL, protected dependency and rollback checks for bounded original source-index retirement.

use super::*;

/// Counts original retained source identities, independent of business-body deduplication.
fn records(log: &SqliteEventLog) -> u64 {
    log.connection
        .lock()
        .unwrap()
        .query_row(
            "SELECT COUNT(*) FROM retention_reference_records",
            [],
            |row| row.get(0),
        )
        .unwrap()
}

/// Persists a typed decision that consumes an exact original source or records an explicit gap.
fn append_decision(log: &SqliteEventLog, sequence: u64, record: domain::StateRecord) {
    let payload = EventPayload::StatePolicyDecision {
        task_ref: domain::TaskRef::new(
            domain::MissionId::new("mission").unwrap(),
            domain::TaskId::new("task").unwrap(),
        ),
        receipts: vec![domain::DecisionEvidenceReceipt {
            registry_digest: format!("sha256:{}", "a".repeat(64)),
            policy_id: "original-policy".into(),
            policy_revision: 1,
            decision: domain::DecisionKind::Commit,
            node_id: domain::NodeId::new("node").unwrap(),
            disposition: domain::EvidenceDisposition::Accepted,
            condition_passed: true,
            record: Some(record),
            decided_at: TimestampMs::new(sequence),
        }],
    };
    log.begin_batch().unwrap();
    log.clone().append(
        TimestampMs::new(sequence),
        &CorrelationId::new("original-decision").unwrap(),
        None,
        payload,
    );
    assert!(log.take_error().unwrap().is_none());
    log.save_checkpoint(
        crate::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA,
        &format!("{{\"current\":{sequence}}}"),
    )
    .unwrap();
    log.commit_batch().unwrap();
}

/// Reads the exact typed source envelope for constructing a real protected decision dependency.
fn original_record(log: &SqliteEventLog) -> domain::StateRecord {
    let payload: EventPayload =
        serde_json::from_str(&log.events().unwrap()[0].payload_json).unwrap();
    let EventPayload::StateRecordObserved { record } = payload else {
        panic!("fixture source is not an observation")
    };
    record
}

/// Native TTL equality retires expired metadata while a still-fresh source stays protected.
#[test]
fn expired_unreferenced_sources_retire_and_fresh_sources_remain() {
    let root = tempfile::tempdir().unwrap();
    let path = root.path().join("state.sqlite3");
    let log = SqliteEventLog::open_controller_with_checkpoint_policy(
        &path,
        CheckpointStoragePolicy::Full,
        ControllerStorageFormat::V3,
    )
    .unwrap();
    append_observation(&log, 1, "old");
    append_observation(&log, 2, "current");
    let sink = Sink::default();
    let command = request(&log, root.path());
    let prepared = log.prepare_content_archive(2, command.clone()).unwrap();
    let published = SqliteEventLog::publish_prepared_content_archive(prepared, &sink).unwrap();
    log.finish_content_archive_at(published, &sink, TimestampMs::new(1001))
        .unwrap();
    assert_eq!(records(&log), 1);
    let kept: u64 = log
        .connection
        .lock()
        .unwrap()
        .query_row(
            "SELECT source_sequence FROM retention_reference_records",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(kept, 2);
    let proof = log.decision_retention_references(1, 2).unwrap();
    assert!(proof.pins.is_empty());
    let key = format!(
        "retention_source_compaction:{:x}",
        Sha256::digest(command.command_id.as_bytes())
    );
    let audit: String = log
        .connection
        .lock()
        .unwrap()
        .query_row(
            "SELECT value FROM event_log_metadata WHERE key=?1",
            [key],
            |row| row.get(0),
        )
        .unwrap();
    let audit: serde_json::Value = serde_json::from_str(&audit).unwrap();
    assert_eq!(audit["outcome"]["retired_records"], 1);
    assert_eq!(audit["outcome"]["retained_fresh_records"], 1);
    drop(log);
    let reopened = SqliteEventLog::open_controller_with_checkpoint_policy(
        &path,
        CheckpointStoragePolicy::Full,
        ControllerStorageFormat::V3,
    )
    .unwrap();
    assert_eq!(records(&reopened), 1);
    let page = reopened.prepare_event_history_page(None, 100).unwrap();
    assert_eq!(
        SqliteEventLog::read_prepared_event_history_page(page, &sink)
            .unwrap()
            .events
            .len(),
        2
    );
}

/// Protected original decision sources retain their identity even after native expiry and archival.
#[test]
fn expired_original_decision_input_is_never_retired() {
    let root = tempfile::tempdir().unwrap();
    let log = SqliteEventLog::open_controller_with_checkpoint_policy(
        root.path().join("state.sqlite3"),
        CheckpointStoragePolicy::Full,
        ControllerStorageFormat::V3,
    )
    .unwrap();
    append_observation(&log, 1, "protected");
    append_decision(&log, 2, original_record(&log));
    let sink = Sink::default();
    let prepared = log
        .prepare_content_archive(2, request(&log, root.path()))
        .unwrap();
    let published = SqliteEventLog::publish_prepared_content_archive(prepared, &sink).unwrap();
    log.finish_content_archive_at(published, &sink, TimestampMs::new(5000))
        .unwrap();
    assert_eq!(records(&log), 1);
    assert_eq!(
        log.decision_retention_references(1, 2).unwrap().pins[0].start_sequence,
        1
    );
}

/// A previous unresolved dependency withholds retirement of otherwise expired unrelated sources.
#[test]
fn unresolved_decision_source_blocks_all_source_compaction() {
    let root = tempfile::tempdir().unwrap();
    let log = SqliteEventLog::open_controller_with_checkpoint_policy(
        root.path().join("state.sqlite3"),
        CheckpointStoragePolicy::Full,
        ControllerStorageFormat::V3,
    )
    .unwrap();
    append_observation(&log, 1, "kept");
    let original = original_record(&log);
    let missing = domain::StateRecord::new_with_source_epoch(
        original.key().object().clone(),
        original.key().semantic(),
        original.key().source().clone(),
        original.key().channel_id(),
        original.payload_schema(),
        original.value().clone(),
        original.source_observed_at(),
        original.received_at(),
        original.valid_for_ms(),
        None,
        Some("different-epoch".into()),
        original.sequence(),
    )
    .unwrap();
    append_decision(&log, 2, missing);
    let sink = Sink::default();
    let prepared = log
        .prepare_content_archive(2, request(&log, root.path()))
        .unwrap();
    let published = SqliteEventLog::publish_prepared_content_archive(prepared, &sink).unwrap();
    log.finish_content_archive_at(published, &sink, TimestampMs::new(5000))
        .unwrap();
    assert_eq!(records(&log), 1);
    assert_eq!(
        log.decision_retention_references(1, 2)
            .unwrap()
            .unresolved_decision_sequences,
        vec![2]
    );
}

/// Clock regression rejects finalization and SQL commit failure restores body/source/head together.
#[test]
fn clock_regression_and_failed_commit_cannot_retire_original_sources() {
    let root = tempfile::tempdir().unwrap();
    let log = SqliteEventLog::open_controller_with_checkpoint_policy(
        root.path().join("state.sqlite3"),
        CheckpointStoragePolicy::Full,
        ControllerStorageFormat::V3,
    )
    .unwrap();
    append_observation(&log, 1, "kept");
    log.begin_batch().unwrap();
    log.observe_retention_clock(5000).unwrap();
    log.commit_batch().unwrap();
    let command = request(&log, root.path());
    let sink = Sink::default();
    let prepared = log.prepare_content_archive(1, command.clone()).unwrap();
    let published = SqliteEventLog::publish_prepared_content_archive(prepared, &sink).unwrap();
    assert!(
        log.finish_content_archive_at(published, &sink, TimestampMs::new(4999))
            .is_err()
    );
    assert_eq!((records(&log), bodies(&log), log.len().unwrap()), (1, 1, 1));
    let prepared = log.prepare_content_archive(1, command.clone()).unwrap();
    let published = SqliteEventLog::publish_prepared_content_archive(prepared, &sink).unwrap();
    log.connection.lock().unwrap().execute_batch("PRAGMA foreign_keys=ON; CREATE TABLE source_commit_fault(original_sequence INTEGER REFERENCES controller_v3_events(sequence) DEFERRABLE INITIALLY DEFERRED); INSERT INTO source_commit_fault VALUES(1)").unwrap();
    assert!(
        log.finish_content_archive_at(published, &sink, TimestampMs::new(6000))
            .is_err()
    );
    assert_eq!((records(&log), bodies(&log), log.len().unwrap()), (1, 1, 1));
    assert_eq!(log.retention_policy().unwrap().clock_high_water_ms, 5000);
    assert_eq!(
        log.decision_retention_references(1, 1)
            .unwrap()
            .indexed_through_sequence,
        1
    );
}

/// A corrupt protected index is refused by the independent snapshot before any source can retire.
#[test]
fn corrupt_reference_before_outside_writer_proof_preserves_all_hot_evidence() {
    let root = tempfile::tempdir().unwrap();
    let log = SqliteEventLog::open_controller_with_checkpoint_policy(
        root.path().join("state.sqlite3"),
        CheckpointStoragePolicy::Full,
        ControllerStorageFormat::V3,
    )
    .unwrap();
    append_observation(&log, 1, "protected");
    append_decision(&log, 2, original_record(&log));
    let prepared = log
        .prepare_content_archive(2, request(&log, root.path()))
        .unwrap();
    log.connection
        .lock()
        .unwrap()
        .execute("DELETE FROM retention_reference_edges", [])
        .unwrap();
    let sink = Sink::default();
    assert!(SqliteEventLog::publish_prepared_content_archive(prepared, &sink).is_err());
    assert_eq!(records(&log), 1);
    assert_eq!(log.history_floor().unwrap(), 0);
    assert_eq!(log.len().unwrap(), 2);
}

/// Once verified, original protection survives an accidentally missing mutable edge at finalization.
#[test]
fn removed_protected_edge_after_proof_never_unpins_its_original_source() {
    let root = tempfile::tempdir().unwrap();
    let log = SqliteEventLog::open_controller_with_checkpoint_policy(
        root.path().join("state.sqlite3"),
        CheckpointStoragePolicy::Full,
        ControllerStorageFormat::V3,
    )
    .unwrap();
    append_observation(&log, 1, "protected");
    append_decision(&log, 2, original_record(&log));
    let prepared = log
        .prepare_content_archive(2, request(&log, root.path()))
        .unwrap();
    let sink = Sink::default();
    let published = SqliteEventLog::publish_prepared_content_archive(prepared, &sink).unwrap();
    log.connection
        .lock()
        .unwrap()
        .execute("DELETE FROM retention_reference_edges", [])
        .unwrap();
    log.finish_content_archive_at(published, &sink, TimestampMs::new(5000))
        .unwrap();
    assert_eq!(records(&log), 1);
    assert_eq!(log.history_floor().unwrap(), 2);
    // The derived index corruption remains detectable by its unchanged complete-audit contract.
    assert!(log.decision_retention_references(1, 2).is_err());
}

/// New real decisions change the protected head and withhold source retirement without blocking archive.
#[test]
fn new_decision_after_outside_writer_proof_skips_source_gc() {
    let root = tempfile::tempdir().unwrap();
    let log = SqliteEventLog::open_controller_with_checkpoint_policy(
        root.path().join("state.sqlite3"),
        CheckpointStoragePolicy::Full,
        ControllerStorageFormat::V3,
    )
    .unwrap();
    append_observation(&log, 1, "now-protected");
    let record = original_record(&log);
    let command = request(&log, root.path());
    let prepared = log.prepare_content_archive(1, command.clone()).unwrap();
    let sink = Sink::default();
    let published = SqliteEventLog::publish_prepared_content_archive(prepared, &sink).unwrap();
    append_decision(&log, 2, record);
    let newest = log.load_checkpoint().unwrap();
    log.finish_content_archive_at(published, &sink, TimestampMs::new(5000))
        .unwrap();
    assert_eq!(records(&log), 1);
    assert_eq!(log.history_floor().unwrap(), 1);
    assert_eq!(log.load_checkpoint().unwrap(), newest);
    assert_eq!(log.events().unwrap()[0].sequence, 2);
    let key = format!(
        "retention_source_compaction:{:x}",
        Sha256::digest(command.command_id.as_bytes())
    );
    let audit: String = log
        .connection
        .lock()
        .unwrap()
        .query_row(
            "SELECT value FROM event_log_metadata WHERE key=?1",
            [key],
            |row| row.get(0),
        )
        .unwrap();
    assert!(audit.contains("protected dependencies changed"));
}

/// Ordinary source appends may advance coverage while unchanged protection still authorizes old expiry.
#[test]
fn new_ordinary_append_preserves_tail_while_old_verified_source_retires() {
    let root = tempfile::tempdir().unwrap();
    let log = SqliteEventLog::open_controller_with_checkpoint_policy(
        root.path().join("state.sqlite3"),
        CheckpointStoragePolicy::Full,
        ControllerStorageFormat::V3,
    )
    .unwrap();
    append_observation(&log, 1, "old");
    let prepared = log
        .prepare_content_archive(1, request(&log, root.path()))
        .unwrap();
    let sink = Sink::default();
    let published = SqliteEventLog::publish_prepared_content_archive(prepared, &sink).unwrap();
    append_observation(&log, 2, "new");
    log.finish_content_archive_at(published, &sink, TimestampMs::new(5000))
        .unwrap();
    assert_eq!(records(&log), 1);
    let retained: u64 = log
        .connection
        .lock()
        .unwrap()
        .query_row(
            "SELECT source_sequence FROM retention_reference_records",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(retained, 2);
    assert_eq!(log.events().unwrap()[0].sequence, 2);
}

/// Missing original verification authority withholds index retirement while complete bytes still archive.
#[test]
fn missing_outside_writer_proof_never_authorizes_source_index_gc() {
    let root = tempfile::tempdir().unwrap();
    let log = SqliteEventLog::open_controller_with_checkpoint_policy(
        root.path().join("state.sqlite3"),
        CheckpointStoragePolicy::Full,
        ControllerStorageFormat::V3,
    )
    .unwrap();
    append_observation(&log, 1, "keep-metadata");
    let prepared = log
        .prepare_content_archive(1, request(&log, root.path()))
        .unwrap();
    let sink = Sink::default();
    let mut published = SqliteEventLog::publish_prepared_content_archive(prepared, &sink).unwrap();
    published.source_retirement_proof = None;
    log.finish_content_archive_at(published, &sink, TimestampMs::new(5000))
        .unwrap();
    assert_eq!(records(&log), 1);
    assert_eq!(log.history_floor().unwrap(), 1);
    assert_eq!(bodies(&log), 0);
}
