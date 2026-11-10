//! Two-phase publication interleaving with actual SQLite appends and original command receipts.

use super::*;
use std::collections::BTreeMap;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};

#[path = "retention_archive_tests.rs"]
mod retention_archive;
#[path = "retention_reference_compaction_tests.rs"]
mod source_compaction;

/// Independent sink fixture stores exact immutable publication identity and records entry counts.
#[derive(Default)]
struct Sink {
    /// Exact original proof for each already published operation.
    published: Mutex<BTreeMap<String, String>>,
    /// Exact original segment bytes remain readable after hot body reclamation.
    segments: Mutex<BTreeMap<String, Vec<u8>>>,
    /// First-copy effect count; observing completed operations must not increase it.
    copies: AtomicUsize,
    /// Injected publication failure before any proof can be produced.
    fail: AtomicBool,
    /// Final permission may be revoked while the writer is released.
    revoked: AtomicBool,
    /// Counts permission observations to model revocation during the final SQL work.
    permission_checks: AtomicUsize,
    /// Nonzero check ordinal at which the independent deployment authority is revoked.
    revoke_on_check: AtomicUsize,
}

impl ArchiveSink for Sink {
    /// The fixture never accepts event-only copying as a complete content operation.
    fn append_segment(
        &self,
        _manifest: &ArchiveManifest,
        _bytes: &[u8],
    ) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
        Err("event-only copying is unavailable".into())
    }

    /// Publishes the exact frozen prefix and checkpoint independently of the current live writer.
    fn append_catalog_segment(
        &self,
        manifest: &ArchiveManifest,
        bytes: &[u8],
        _catalog: &[PersistedEvent],
        checkpoint: &PersistedCheckpoint,
        intent: &super::super::super::archive_operation::ArchiveIntent,
    ) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
        self.copies.fetch_add(1, Ordering::SeqCst);
        if self.fail.load(Ordering::SeqCst) {
            return Err("injected publication failure".into());
        }
        assert_eq!(manifest, &intent.manifest);
        assert_eq!(checkpoint, &intent.checkpoint);
        assert_eq!(
            manifest.segment_digest,
            format!("sha256:{:x}", Sha256::digest(bytes))
        );
        self.published.lock().unwrap().insert(
            intent.request.command_id.clone(),
            manifest.segment_digest.clone(),
        );
        self.segments
            .lock()
            .unwrap()
            .insert(intent.request.command_id.clone(), bytes.to_vec());
        Ok(())
    }

    /// Returns only a previously published exact proof; it never copies during a retry.
    fn inspect_catalog_segment(
        &self,
        intent: &super::super::super::archive_operation::ArchiveIntent,
    ) -> Result<String, Box<dyn std::error::Error + Send + Sync>> {
        self.published
            .lock()
            .unwrap()
            .get(&intent.request.command_id)
            .cloned()
            .ok_or_else(|| "original publication unavailable".into())
    }

    /// Models the final current administrator permission check under the reacquired writer.
    fn require_commit_permission(&self) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
        let check = self.permission_checks.fetch_add(1, Ordering::SeqCst) + 1;
        if self.revoke_on_check.load(Ordering::SeqCst) == check {
            return Err("independent permission expired before final Commit".into());
        }
        if self.revoked.load(Ordering::SeqCst) {
            Err("current authority revoked".into())
        } else {
            Ok(())
        }
    }
}

/// Permission valid at finalization entry can expire before Commit; all SQL effects then roll back.
#[test]
fn authority_is_rechecked_after_sql_work_immediately_before_commit() {
    let root = tempfile::tempdir().unwrap();
    let log = SqliteEventLog::open(root.path().join("state.sqlite3")).unwrap();
    append(&log, 1);
    let command = request(&log, root.path());
    let prepared = log.prepare_content_archive(1, command.clone()).unwrap();
    let sink = Sink::default();
    let published = SqliteEventLog::publish_prepared_content_archive(prepared, &sink).unwrap();
    sink.revoke_on_check.store(2, Ordering::SeqCst);
    assert!(log.finish_content_archive(published, &sink).is_err());
    assert_eq!(sink.permission_checks.load(Ordering::SeqCst), 2);
    assert_eq!(log.history_floor().unwrap(), 0);
    assert_eq!(log.len().unwrap(), 1);
    assert!(
        log.archive_operation(&command.command_id)
            .unwrap()
            .unwrap()
            .1
            .is_none()
    );
    sink.revoke_on_check.store(0, Ordering::SeqCst);
    let retry = log.prepare_content_archive(1, command).unwrap();
    let published = SqliteEventLog::publish_prepared_content_archive(retry, &sink).unwrap();
    log.finish_content_archive(published, &sink).unwrap();
    assert_eq!(sink.copies.load(Ordering::SeqCst), 1);
}

impl super::super::super::archive_history::EventHistoryArchiveReader for Sink {
    /// Reads only an already published exact original segment without consulting hot value bodies.
    fn read_published_segment(
        &self,
        manifest: &ArchiveManifest,
        original_intent: Option<&super::super::super::archive_operation::ArchiveIntent>,
        original_receipt: Option<&str>,
    ) -> Result<Vec<u8>, String> {
        let intent = original_intent.ok_or("original content intent is missing")?;
        let published = self.published.lock().unwrap();
        let receipt = published
            .get(&intent.request.command_id)
            .ok_or("publication is missing")?;
        if original_receipt != Some(receipt.as_str()) || manifest != &intent.manifest {
            return Err("original archive receipt or manifest differs".into());
        }
        self.segments
            .lock()
            .unwrap()
            .get(&intent.request.command_id)
            .cloned()
            .ok_or_else(|| "original segment bytes are missing".into())
    }
}

/// Commits one real original event and a distinguishable complete authority checkpoint.
fn append(log: &SqliteEventLog, sequence: u64) {
    log.begin_batch().unwrap();
    log.clone().append(
        TimestampMs::new(sequence),
        &CorrelationId::new(format!("correlation-{sequence}")).unwrap(),
        None,
        EventPayload::ExecutionGroupBlocked {
            group_id: domain::ExecutionGroupId::new("group").unwrap(),
            task_ref: domain::TaskRef::new(
                domain::MissionId::new("mission").unwrap(),
                domain::TaskId::new("task").unwrap(),
            ),
            reason: format!("original-{sequence}"),
        },
    );
    assert!(log.take_error().unwrap().is_none());
    log.save_checkpoint(
        crate::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA,
        &format!("{{\"current\":{sequence}}}"),
    )
    .unwrap();
    log.commit_batch().unwrap();
}

/// Builds one explicit same-authority original content maintenance command.
fn request(
    log: &SqliteEventLog,
    root: &Path,
) -> super::super::super::archive_operation::ArchiveRequest {
    super::super::super::archive_operation::ArchiveRequest {
        command_id: "prepared-original".into(),
        principal: "admin".into(),
        tenant: "*".into(),
        source_owner: format!("artifact-store:{}", "a".repeat(64)),
        destination: root.join("archive").to_string_lossy().into(),
        authority_id: log.archive_authority_id().unwrap(),
        budget_ms: 60_000,
    }
}

/// New original events/checkpoint may commit during publication and remain intact after prefix GC.
#[test]
fn concurrent_new_commit_survives_prepared_prefix_finalization() {
    let root = tempfile::tempdir().unwrap();
    let log = SqliteEventLog::open(root.path().join("state.sqlite3")).unwrap();
    append(&log, 1);
    let command = request(&log, root.path());
    let prepared = log.prepare_content_archive(1, command.clone()).unwrap();
    let frozen_checkpoint = prepared.intent().checkpoint.clone();
    append(&log, 2);
    let newest = log.load_checkpoint().unwrap();
    assert_ne!(newest.as_ref().unwrap(), &frozen_checkpoint);
    let sink = Sink::default();
    let published = SqliteEventLog::publish_prepared_content_archive(prepared, &sink).unwrap();
    append(&log, 3);
    let newest = log.load_checkpoint().unwrap();
    log.finish_content_archive(published, &sink).unwrap();
    assert_eq!(log.history_floor().unwrap(), 1);
    assert_eq!(
        log.events()
            .unwrap()
            .iter()
            .map(|event| event.sequence)
            .collect::<Vec<_>>(),
        vec![2, 3]
    );
    assert_eq!(log.load_checkpoint().unwrap(), newest);
    assert_eq!(
        log.archive_operation(&command.command_id)
            .unwrap()
            .unwrap()
            .0
            .checkpoint,
        frozen_checkpoint
    );
    let retry = log.prepare_content_archive(1, command).unwrap();
    let published = SqliteEventLog::publish_prepared_content_archive(retry, &sink).unwrap();
    log.finish_content_archive(published, &sink).unwrap();
    assert_eq!(sink.copies.load(Ordering::SeqCst), 1);
    assert_eq!(log.load_checkpoint().unwrap(), newest);
}

/// Failed original publication leaves all events and its original pending intent available for audit.
#[test]
fn failed_publish_never_truncates_and_retry_does_not_recopy() {
    let root = tempfile::tempdir().unwrap();
    let log = SqliteEventLog::open(root.path().join("state.sqlite3")).unwrap();
    append(&log, 1);
    let command = request(&log, root.path());
    let original_checkpoint = log.load_checkpoint().unwrap();
    let prepared = log.prepare_content_archive(1, command.clone()).unwrap();
    let sink = Sink::default();
    sink.fail.store(true, Ordering::SeqCst);
    assert!(SqliteEventLog::publish_prepared_content_archive(prepared, &sink).is_err());
    assert_eq!(log.history_floor().unwrap(), 0);
    assert_eq!(log.len().unwrap(), 1);
    assert_eq!(log.load_checkpoint().unwrap(), original_checkpoint);
    sink.fail.store(false, Ordering::SeqCst);
    let retry = log.prepare_content_archive(1, command.clone()).unwrap();
    assert!(SqliteEventLog::publish_prepared_content_archive(retry, &sink).is_err());
    assert_eq!(sink.copies.load(Ordering::SeqCst), 1);
    assert!(
        log.archive_operation(&command.command_id)
            .unwrap()
            .unwrap()
            .1
            .is_none()
    );
}

/// Permission revocation or original prefix modification between phases cannot authorize truncation.
#[test]
fn changed_prefix_and_revoked_permission_refuse_final_commit() {
    for tamper in [false, true] {
        let root = tempfile::tempdir().unwrap();
        let log = SqliteEventLog::open(root.path().join("state.sqlite3")).unwrap();
        append(&log, 1);
        let prepared = log
            .prepare_content_archive(1, request(&log, root.path()))
            .unwrap();
        let sink = Sink::default();
        let published = SqliteEventLog::publish_prepared_content_archive(prepared, &sink).unwrap();
        if tamper {
            log.connection
                .lock()
                .unwrap()
                .execute(
                    "UPDATE events SET correlation_id='tampered' WHERE sequence=1",
                    [],
                )
                .unwrap();
        } else {
            sink.revoked.store(true, Ordering::SeqCst);
        }
        assert!(log.finish_content_archive(published, &sink).is_err());
        assert_eq!(log.history_floor().unwrap(), 0);
        assert_eq!(log.len().unwrap(), 1);
    }
}

/// A stale prepared token cannot finalize a different pending operation or alter the original request.
#[test]
fn changed_command_identity_and_missing_pending_ownership_are_refused() {
    let root = tempfile::tempdir().unwrap();
    let log = SqliteEventLog::open(root.path().join("state.sqlite3")).unwrap();
    append(&log, 1);
    let command = request(&log, root.path());
    let prepared = log.prepare_content_archive(1, command.clone()).unwrap();
    let mut conflicting = command.clone();
    conflicting.budget_ms += 1;
    assert!(log.prepare_content_archive(1, conflicting).is_err());
    let sink = Sink::default();
    let published = SqliteEventLog::publish_prepared_content_archive(prepared, &sink).unwrap();
    log.connection
        .lock()
        .unwrap()
        .execute(
            "DELETE FROM event_archive_operations WHERE command_id=?1",
            [command.command_id],
        )
        .unwrap();
    assert!(log.finish_content_archive(published, &sink).is_err());
    assert_eq!(log.history_floor().unwrap(), 0);
    assert_eq!(log.len().unwrap(), 1);
}

/// Final SQL commit failure preserves the original pending publication and permits a later retry.
#[test]
fn failed_final_commit_preserves_prefix_and_original_receipt_identity() {
    let root = tempfile::tempdir().unwrap();
    let log = SqliteEventLog::open(root.path().join("state.sqlite3")).unwrap();
    append(&log, 1);
    let command = request(&log, root.path());
    let prepared = log.prepare_content_archive(1, command.clone()).unwrap();
    let sink = Sink::default();
    let published = SqliteEventLog::publish_prepared_content_archive(prepared, &sink).unwrap();
    log.connection.lock().unwrap().execute_batch("PRAGMA foreign_keys=ON; CREATE TABLE prepared_commit_fault(original_sequence INTEGER REFERENCES events(sequence) DEFERRABLE INITIALLY DEFERRED); INSERT INTO prepared_commit_fault VALUES(1)").unwrap();
    assert!(log.finish_content_archive(published, &sink).is_err());
    assert_eq!(log.history_floor().unwrap(), 0);
    assert_eq!(log.len().unwrap(), 1);
    assert!(
        log.archive_operation(&command.command_id)
            .unwrap()
            .unwrap()
            .1
            .is_none()
    );
    log.connection
        .lock()
        .unwrap()
        .execute("DELETE FROM prepared_commit_fault", [])
        .unwrap();
    let prepared = log.prepare_content_archive(1, command).unwrap();
    let published = SqliteEventLog::publish_prepared_content_archive(prepared, &sink).unwrap();
    log.finish_content_archive(published, &sink).unwrap();
    assert_eq!(log.history_floor().unwrap(), 1);
    assert_eq!(sink.copies.load(Ordering::SeqCst), 1);
}

/// Explicit v3 retains original projection/index coverage and newer authority across cold reopening.
#[test]
fn v3_prepared_archive_preserves_newer_checkpoint_and_reference_coverage() {
    let root = tempfile::tempdir().unwrap();
    let path = root.path().join("state.sqlite3");
    let log = SqliteEventLog::open_controller_with_checkpoint_policy(
        &path,
        CheckpointStoragePolicy::Full,
        ControllerStorageFormat::V3,
    )
    .unwrap();
    append(&log, 1);
    let prepared = log
        .prepare_content_archive(1, request(&log, root.path()))
        .unwrap();
    let sink = Sink::default();
    let published = SqliteEventLog::publish_prepared_content_archive(prepared, &sink).unwrap();
    append(&log, 2);
    let current = log.load_checkpoint().unwrap();
    log.finish_content_archive(published, &sink).unwrap();
    assert_eq!(
        log.decision_retention_references(1, 2)
            .unwrap()
            .indexed_through_sequence,
        2
    );
    drop(log);
    let reopened = SqliteEventLog::open_controller_with_checkpoint_policy(
        &path,
        CheckpointStoragePolicy::Full,
        ControllerStorageFormat::V3,
    )
    .unwrap();
    assert_eq!(reopened.load_checkpoint().unwrap(), current);
    assert_eq!(reopened.history_floor().unwrap(), 1);
    assert_eq!(reopened.events().unwrap()[0].sequence, 2);
}

/// Commits a new source observation while retaining the same exact source-scoped body across samples.
fn append_observation(log: &SqliteEventLog, sequence: u64, value: &str) {
    let record = domain::StateRecord::new_with_source_epoch(
        domain::StateObjectRef::new(domain::StateObjectClass::Node, "fixture", "node").unwrap(),
        domain::StateSemantic::Reported,
        domain::StateSource::Node {
            node_id: domain::NodeId::new("node").unwrap(),
            local_system_id: domain::LocalSystemId::new("sensor").unwrap(),
        },
        "result",
        "fixture/v1",
        serde_json::json!({"body": value}),
        Some(TimestampMs::new(sequence)),
        TimestampMs::new(sequence),
        1_000,
        None,
        Some("source-epoch".into()),
        sequence,
    )
    .unwrap();
    log.begin_batch().unwrap();
    log.clone().append(
        TimestampMs::new(sequence),
        &CorrelationId::new("body-gc").unwrap(),
        None,
        EventPayload::StateRecordObserved { record },
    );
    assert!(log.take_error().unwrap().is_none());
    log.save_checkpoint(
        crate::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA,
        &format!("{{\"current\":{sequence}}}"),
    )
    .unwrap();
    log.commit_batch().unwrap();
}

/// Counts only immutable hot bodies; archive bytes and reference-index metadata are independent.
fn bodies(log: &SqliteEventLog) -> u64 {
    log.connection
        .lock()
        .unwrap()
        .query_row("SELECT COUNT(*) FROM telemetry_bodies", [], |row| {
            row.get(0)
        })
        .unwrap()
}

/// Prefix-only bodies retire, shared hot bodies remain, and archived originals survive cold reopen.
#[test]
fn v3_reclaims_only_frozen_prefix_bodies_without_remaining_hot_references() {
    let root = tempfile::tempdir().unwrap();
    let path = root.path().join("state.sqlite3");
    let log = SqliteEventLog::open_controller_with_checkpoint_policy(
        &path,
        CheckpointStoragePolicy::Full,
        ControllerStorageFormat::V3,
    )
    .unwrap();
    append_observation(&log, 1, "prefix-only");
    append_observation(&log, 2, "shared");
    let original = log.events().unwrap();
    let prepared = log
        .prepare_content_archive(2, request(&log, root.path()))
        .unwrap();
    let sink = Sink::default();
    let published = SqliteEventLog::publish_prepared_content_archive(prepared, &sink).unwrap();
    append_observation(&log, 3, "shared");
    assert_eq!(bodies(&log), 2);
    log.finish_content_archive(published, &sink).unwrap();
    assert_eq!(bodies(&log), 1);
    assert_eq!(log.events().unwrap()[0].sequence, 3);
    assert!(log.events().unwrap()[0].payload_json.contains("shared"));
    drop(log);
    let reopened = SqliteEventLog::open_controller_with_checkpoint_policy(
        &path,
        CheckpointStoragePolicy::Full,
        ControllerStorageFormat::V3,
    )
    .unwrap();
    assert_eq!(bodies(&reopened), 1);
    let prepared = reopened.prepare_event_history_page(None, 100).unwrap();
    let archived = SqliteEventLog::read_prepared_event_history_page(prepared, &sink).unwrap();
    assert_eq!(archived.events, original);
    assert_eq!(archived.next_after_sequence, 2);
    let prepared = reopened.prepare_event_history_page(Some(2), 100).unwrap();
    assert_eq!(
        SqliteEventLog::read_prepared_event_history_page(prepared, &sink)
            .unwrap()
            .events[0]
            .sequence,
        3
    );
    // Source-identity metadata remains for original consumption proof; its growth is a separate
    // governance gap until checkpoint/decision/archive reference coverage authorizes retirement.
    let indexed: u64 = reopened
        .connection
        .lock()
        .unwrap()
        .query_row(
            "SELECT COUNT(*) FROM retention_reference_records",
            [],
            |row| row.get(0),
        )
        .unwrap();
    assert_eq!(indexed, 3);
}

/// A failed final SQL COMMIT rolls immutable-body deletion back with its original event reference.
#[test]
fn v3_body_retirement_rolls_back_on_final_commit_failure() {
    let root = tempfile::tempdir().unwrap();
    let log = SqliteEventLog::open_controller_with_checkpoint_policy(
        root.path().join("state.sqlite3"),
        CheckpointStoragePolicy::Full,
        ControllerStorageFormat::V3,
    )
    .unwrap();
    append_observation(&log, 1, "original");
    let command = request(&log, root.path());
    let prepared = log.prepare_content_archive(1, command.clone()).unwrap();
    let sink = Sink::default();
    let published = SqliteEventLog::publish_prepared_content_archive(prepared, &sink).unwrap();
    log.connection.lock().unwrap().execute_batch("PRAGMA foreign_keys=ON; CREATE TABLE body_commit_fault(original_sequence INTEGER REFERENCES controller_v3_events(sequence) DEFERRABLE INITIALLY DEFERRED); INSERT INTO body_commit_fault VALUES(1)").unwrap();
    assert!(log.finish_content_archive(published, &sink).is_err());
    assert_eq!(bodies(&log), 1);
    assert_eq!(log.len().unwrap(), 1);
    assert!(log.events().unwrap()[0].payload_json.contains("original"));
    log.connection
        .lock()
        .unwrap()
        .execute("DELETE FROM body_commit_fault", [])
        .unwrap();
    let prepared = log.prepare_content_archive(1, command).unwrap();
    let published = SqliteEventLog::publish_prepared_content_archive(prepared, &sink).unwrap();
    log.finish_content_archive(published, &sink).unwrap();
    assert_eq!(bodies(&log), 0);
    assert_eq!(log.len().unwrap(), 0);
    assert_eq!(sink.copies.load(Ordering::SeqCst), 1);
}
