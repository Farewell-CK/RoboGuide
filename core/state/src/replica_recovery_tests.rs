//! Actual SQLite/Catalog recovery boundaries; metadata tests never grant quorum or device facts.

use super::*;
use artifact_store::FileSystemArtifactStore;
use domain::*;

/// Creates original actual SQL/CAS identities and one metadata-only publication, without any Nodes.
fn source() -> (tempfile::TempDir, SqliteEventLog, FileSystemArtifactStore) {
    let directory = tempfile::tempdir().unwrap();
    let log = SqliteEventLog::open(directory.path().join("source.sqlite3")).unwrap();
    let cas =
        FileSystemArtifactStore::new_with_archive_references(directory.path().join("source-cas"))
            .unwrap();
    let manifest = MemoryArtifactManifest::new(
        MemorySelector::new(
            MemoryId::new("original-parser-metadata").unwrap(),
            MemoryRevisionId::new("original-one").unwrap(),
        ),
        MemoryKind::Artifact,
        "actual-sql-test-provider",
        MemoryOwner::RoboGuide {
            component: "actual-sql-validation".into(),
        },
        MemoryScope::Global,
        MemoryVisibility::Discoverable,
        "opaque/v1",
        "application/octet-stream",
        None,
        None,
        None,
        None,
        TimestampMs::new(7),
    )
    .unwrap();
    log.begin_batch().unwrap();
    let mut writer = log.clone();
    writer.append(
        TimestampMs::new(7),
        &CorrelationId::new("original-source-publication").unwrap(),
        None,
        EventPayload::MemoryManifestPublished { manifest },
    );
    assert!(log.take_error().unwrap().is_none());
    let checkpoint = "{\"original_raw_number\":1.00,\"state\":\"opaque-test-only\"}";
    log.save_checkpoint_with_command(
        crate::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA,
        checkpoint,
        ControlCommandJournalEntry {
            command_id: "original-source-command".into(),
            causation_id: None,
            authority_revision: 0,
            controller_epoch: None,
            plan_digest: None,
            input_digest: format!("sha256:{:x}", Sha256::digest(b"original-input")),
            ownership_digest: None,
            resulting_state_digest: format!("sha256:{:x}", Sha256::digest(checkpoint.as_bytes())),
            checkpoint_event_sequence: 1,
            checkpoint_schema: crate::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA.into(),
        },
    )
    .unwrap();
    log.commit_batch().unwrap();
    (directory, log, cas)
}

/// Reads exact original event/command SQL rows with no Model, Controller or use-permission inference.
fn rows(log: &SqliteEventLog) -> (Vec<PersistedEvent>, Vec<ControlCommandJournalEntry>) {
    let connection = log.connection.lock().unwrap();
    let mut statement = connection.prepare("SELECT sequence,event_id,timestamp_ms,correlation_id,causation_id,payload_schema,payload_json FROM events ORDER BY sequence").unwrap();
    let events = statement
        .query_map([], event_from_row)
        .unwrap()
        .collect::<Result<Vec<_>, _>>()
        .unwrap();
    (
        events,
        super::super::authority_journal::since(&connection, 0).unwrap(),
    )
}

/// Builds a real retained original copy intent; no publication, receipt or content availability is claimed.
fn pending(
    log: &SqliteEventLog,
    cas: &FileSystemArtifactStore,
    destination: &std::path::Path,
) -> ArchiveIntent {
    let (events, _) = rows(log);
    let checkpoint = log.load_checkpoint().unwrap().unwrap();
    let mut segment = serde_json::to_vec(&events[0]).unwrap();
    segment.push(b'\n');
    let manifest = ArchiveManifest {
        schema: "roboguide.event-archive/v2".into(),
        start_sequence: 1,
        end_sequence: 1,
        event_count: 1,
        segment_digest: format!("sha256:{:x}", Sha256::digest(segment)),
        checkpoint_sequence: 1,
        checkpoint_digest: Some(checkpoint_identity(&checkpoint)),
    };
    let intent = super::super::archive_operation::ArchiveIntent::new(
        ArchiveRequest {
            command_id: "original-unpublished-copy".into(),
            principal: "actual-sql-test-admin".into(),
            tenant: "actual-sql-test-tenant".into(),
            source_owner: cas.owner_id().unwrap().into(),
            authority_id: log.archive_authority_id().unwrap(),
            destination: destination.to_str().unwrap().into(),
            budget_ms: 30000,
        },
        manifest,
        checkpoint,
        &events,
        &events,
    )
    .unwrap();
    super::super::archive_operation::admit(&log.connection.lock().unwrap(), &intent).unwrap();
    intent
}

/// Complete original identity, pending intent, Catalog bytes and command/checkpoint survive real reopen.
#[test]
fn complete_original_state_projection_materializes_without_inventing_archive_authority() {
    let (directory, log, cas) = source();
    let intent = pending(&log, &cas, &directory.path().join("unpublished-archive"));
    let original = log.replica_recovery_projection().unwrap();
    let (events, commands) = rows(&log);
    let checkpoint = log.load_checkpoint().unwrap().unwrap();
    original.validate_for(&events, &checkpoint).unwrap();
    let target_path = directory.path().join("promoted.sqlite3");
    let target = SqliteEventLog::open(&target_path).unwrap();
    target
        .install_replica_authority_with_recovery(&events, &checkpoint, &commands, &original)
        .unwrap();
    assert_eq!(
        target.archive_authority_id().unwrap(),
        log.archive_authority_id().unwrap()
    );
    assert_eq!(
        target
            .archive_operation("original-unpublished-copy")
            .unwrap(),
        Some((intent, None))
    );
    assert_eq!(target.replica_recovery_projection().unwrap(), original);
    assert_eq!(target.catalog_replay_stored_events().unwrap(), events);
    drop(target);
    let reopened = SqliteEventLog::open(&target_path).unwrap();
    assert_eq!(reopened.load_checkpoint().unwrap(), Some(checkpoint));
    assert_eq!(
        reopened
            .replica_recovery_projection()
            .unwrap()
            .identity()
            .unwrap(),
        original.identity().unwrap()
    );
    assert_eq!(rows(&reopened).1, commands);
}

/// Current v2 materialization refuses before writes, rather than persisting an unreopenable checkpoint.
#[test]
fn metadata_only_modern_import_refuses_without_creating_replacement_journal() {
    let (directory, log, _cas) = source();
    let (events, commands) = rows(&log);
    let target = SqliteEventLog::open(directory.path().join("refused.sqlite3")).unwrap();
    let error = target
        .install_replica_authority(
            &events,
            &log.load_checkpoint().unwrap().unwrap(),
            None,
            &commands,
        )
        .unwrap_err()
        .to_string();
    assert!(error.contains("original complete recovery projection"));
    assert!(target.load_checkpoint().unwrap().is_none());
    assert!(rows(&target).0.is_empty());
    assert!(target.archive_authority_id().is_err());
}

/// A valid stale projection cannot erase a later original Unknown copy while reinstalling the same head.
#[test]
fn actual_reinstallation_preserves_newer_original_unknown_and_rolls_back_on_stale_input() {
    let (directory, log, cas) = source();
    let original = log.replica_recovery_projection().unwrap();
    let (events, commands) = rows(&log);
    let checkpoint = log.load_checkpoint().unwrap().unwrap();
    let intent = pending(&log, &cas, &directory.path().join("original-pending-copy"));
    let before = log.replica_recovery_projection().unwrap();
    let error = log
        .install_replica_authority_with_recovery(&events, &checkpoint, &commands, &original)
        .unwrap_err()
        .to_string();
    assert!(error.contains("lost an original copy intent"), "{error}");
    assert_eq!(log.replica_recovery_projection().unwrap(), before);
    assert_eq!(
        log.archive_operation(&intent.request.command_id).unwrap(),
        Some((intent, None))
    );
    assert_eq!(rows(&log), (events.clone(), commands.clone()));
    log.install_replica_authority_with_recovery(&events, &checkpoint, &commands, &before)
        .unwrap();
    assert_eq!(log.replica_recovery_projection().unwrap(), before);
}

/// Actual SQL rollback retains complete old command authority when incoming data omits its prefix.
#[test]
fn actual_reinstallation_cannot_remove_original_authority_commands() {
    let (_directory, log, _cas) = source();
    let original = log.replica_recovery_projection().unwrap();
    let (events, commands) = rows(&log);
    let checkpoint = log.load_checkpoint().unwrap().unwrap();
    let error = log
        .install_replica_authority_with_recovery(&events, &checkpoint, &[], &original)
        .unwrap_err()
        .to_string();
    assert!(error.contains("discard original commands"), "{error}");
    assert_eq!(rows(&log), (events, commands));
    assert_eq!(log.load_checkpoint().unwrap(), Some(checkpoint));
    assert_eq!(log.replica_recovery_projection().unwrap(), original);
}

/// Missing mandatory identity/table and a broken original tail cannot become accepted restored authority.
#[test]
fn incomplete_original_auxiliary_authority_and_metadata_tail_are_refused() {
    let (directory, log, _cas) = source();
    let checkpoint = log.load_checkpoint().unwrap().unwrap();
    let (events, commands) = rows(&log);
    let original = log.replica_recovery_projection().unwrap();
    let mut missing = original.clone();
    missing.metadata.remove("archive_authority_id");
    let target = SqliteEventLog::open(directory.path().join("incomplete.sqlite3")).unwrap();
    assert!(
        target
            .install_replica_authority_with_recovery(&events, &checkpoint, &commands, &missing)
            .is_err()
    );
    assert!(
        original
            .validate_for(&[], &checkpoint)
            .unwrap_err()
            .to_string()
            .contains("complete unarchived event tail")
    );
    log.connection
        .lock()
        .unwrap()
        .execute_batch("DROP TABLE event_archive_aborts")
        .unwrap();
    assert!(log.replica_recovery_projection().is_err());
    assert!(target.load_checkpoint().unwrap().is_none());
    assert!(target.archive_authority_id().is_err());
}

/// Another actual original source ID cannot replace an accepted local auxiliary authority.
#[test]
fn another_original_source_id_cannot_replace_the_existing_state_store() {
    let (_directory, source, _source_cas) = source();
    let (_other_directory, target, _target_cas) = super::tests::source();
    let before = target.replica_recovery_projection().unwrap();
    let previous = target.load_checkpoint().unwrap();
    let (events, commands) = rows(&source);
    let error = target
        .install_replica_authority_with_recovery(
            &events,
            &source.load_checkpoint().unwrap().unwrap(),
            &commands,
            &source.replica_recovery_projection().unwrap(),
        )
        .unwrap_err()
        .to_string();
    assert!(error.contains("another original Archive authority"));
    assert_eq!(target.replica_recovery_projection().unwrap(), before);
    assert_eq!(target.load_checkpoint().unwrap(), previous);
}

/// Explicit contract fixture for the State port, never an actual quorum or physical execution proof.
struct FullPort {
    /// Actual original SQL inspected independently while the source private writer is held.
    source_path: std::path::PathBuf,
    /// Actual committed source checkpoint before the simulated lost reply.
    previous: PersistedCheckpoint,
    /// Complete actual source event history retained independently of the candidate delta.
    events: Vec<PersistedEvent>,
    /// Complete admitted candidate/projection captured before the source rollback.
    captured: Mutex<Option<(ApplicationCommitBatch, ReplicaRecoveryProjection)>>,
}

impl ApplicationBatchReplicator for FullPort {
    /// Declares only a labelled unit-test port, without creating cluster authority.
    fn profile_identity(&self) -> &str {
        "explicit-state-port-contract-fixture"
    }
    /// Admits only this controlled unit-test transition; no transport/quorum observation is claimed.
    fn assert_write_authority(&self) -> Result<(), String> {
        Ok(())
    }
    /// An explicit full-recovery port must never receive an ordinary metadata-only candidate.
    fn confirm_batch(&self, _batch: ApplicationCommitBatch) -> Result<(), String> {
        panic!("negotiated full State recovery fell back to v2")
    }
    /// Selects only the explicit original recovery projection port.
    fn requires_recovery_projection(&self) -> bool {
        true
    }
    /// Inspects actual old committed SQL, captures exact source data, then simulates a lost response.
    fn confirm_recovery_batch(
        &self,
        batch: ApplicationCommitBatch,
        recovery: ReplicaRecoveryProjection,
    ) -> Result<(), String> {
        assert_eq!(
            SqliteEventLog::read_checkpoint(&self.source_path).unwrap(),
            Some(self.previous.clone())
        );
        recovery
            .validate_for(&self.events, &batch.checkpoint)
            .unwrap();
        *self.captured.lock().unwrap() = Some((batch, recovery));
        Err("explicit contract fixture lost reply after capturing exact candidate".into())
    }
}

/// Actual private SQL rollback cannot erase the original projection delivered through the full port.
#[test]
fn original_projection_reaches_the_negotiated_port_before_actual_source_commit() {
    let (directory, log, _cas) = source();
    let previous = log.load_checkpoint().unwrap().unwrap();
    let (events, mut commands) = rows(&log);
    let port = Arc::new(FullPort {
        source_path: directory.path().join("source.sqlite3"),
        previous: previous.clone(),
        events: events.clone(),
        captured: Mutex::new(None),
    });
    let log = log.with_replicator(port.clone());
    log.begin_batch().unwrap();
    let json = "{\"retained_after_lost_reply\":true}";
    let mut command = commands[0].clone();
    command.command_id = "exact-next-source-command".into();
    command.authority_revision = 0;
    command.resulting_state_digest = format!("sha256:{:x}", Sha256::digest(json.as_bytes()));
    log.save_checkpoint_with_command(crate::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA, json, command)
        .unwrap();
    assert!(
        log.commit_batch()
            .unwrap_err()
            .to_string()
            .contains("lost reply")
    );
    log.rollback_batch().unwrap();
    assert_eq!(log.load_checkpoint().unwrap(), Some(previous));
    let (batch, original) = port.captured.lock().unwrap().clone().unwrap();
    commands.extend(batch.commands);
    let target_path = directory.path().join("captured-promoted.sqlite3");
    let target = SqliteEventLog::open(&target_path).unwrap();
    target
        .install_replica_authority_with_recovery(&events, &batch.checkpoint, &commands, &original)
        .unwrap();
    drop(target);
    let target = SqliteEventLog::open(&target_path).unwrap();
    assert_eq!(target.load_checkpoint().unwrap(), Some(batch.checkpoint));
    assert_eq!(target.replica_recovery_projection().unwrap(), original);
}

/// An actual receiver SQL trigger rolls back original ID, all rows and checkpoint in one transaction.
#[test]
fn actual_receiver_sql_failure_preserves_the_entire_prior_empty_authority() {
    let (directory, log, _cas) = source();
    let (events, commands) = rows(&log);
    let target = SqliteEventLog::open(directory.path().join("sql-fault.sqlite3")).unwrap();
    target.connection.lock().unwrap().execute_batch("CREATE TRIGGER actual_recovery_fault BEFORE INSERT ON event_log_metadata WHEN NEW.key='archive_operations_schema' BEGIN SELECT RAISE(ABORT,'actual original recovery SQL fault'); END").unwrap();
    let error = target
        .install_replica_authority_with_recovery(
            &events,
            &log.load_checkpoint().unwrap().unwrap(),
            &commands,
            &log.replica_recovery_projection().unwrap(),
        )
        .unwrap_err()
        .to_string();
    assert!(error.contains("actual original recovery SQL fault"));
    assert!(target.load_checkpoint().unwrap().is_none());
    assert!(target.archive_authority_id().is_err());
    assert!(rows(&target).0.is_empty());
    assert!(rows(&target).1.is_empty());
}
