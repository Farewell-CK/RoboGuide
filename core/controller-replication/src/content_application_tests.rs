//! Actual SQLite/CAS/managed-Snapshot contracts; direct Raft storage apply is not a network quorum.

use super::*;
use domain::*;
use openraft::storage::RaftStateMachine;
use openraft::{CommittedLeaderId, Entry, EntryPayload, LogId, RaftSnapshotBuilder};
use ports::EventSink;
use sha2::{Digest as _, Sha256};

/// Creates real source CAS bytes, durable original upload history and original modern State authority.
pub(crate) fn original(
    root: &Path,
) -> (
    state::SqliteEventLog,
    artifact_store::FileSystemArtifactStore,
    ArtifactContentKey,
) {
    original_payload(root, b"actual original online-content storage test bytes")
}

/// Publishes the caller's real bounded software bytes while retaining the original upload/SQL contract.
pub(crate) fn original_payload(
    root: &Path,
    payload: &[u8],
) -> (
    state::SqliteEventLog,
    artifact_store::FileSystemArtifactStore,
    ArtifactContentKey,
) {
    original_upload(root, payload, true)
}

/// Creates actual CAS/SQL fixtures with an explicit logical Prepared or Completed outcome.
fn original_upload(
    root: &Path,
    payload: &[u8],
    completed: bool,
) -> (
    state::SqliteEventLog,
    artifact_store::FileSystemArtifactStore,
    ArtifactContentKey,
) {
    let cas = artifact_store::FileSystemArtifactStore::new_with_archive_references(
        root.join("source-cas"),
    )
    .unwrap();
    let digest = artifact_store::digest_bytes(payload);
    let mut upload = cas.begin_upload("actual-original-upload").unwrap();
    upload.write_chunk(payload).unwrap();
    upload.finalize(&digest, payload.len() as u64).unwrap();
    let key = ArtifactContentKey {
        source_owner: cas.owner_id().unwrap().into(),
        content_digest: digest,
        byte_size: payload.len() as u64,
    };
    let log = state::SqliteEventLog::open(root.join("source.sqlite3")).unwrap();
    let mut record = ArtifactUploadRecord {
        upload_id: "actual-original-upload".into(),
        principal_id: "explicit-storage-contract-principal".into(),
        tenant_id: "explicit-storage-contract-tenant".into(),
        original_certificate: "loopback-contract-fixture".into(),
        admitted_at_ms: 1,
        deadline_ms: 30000,
        phase: ArtifactUploadPhase::Started,
        content_digest: None,
        byte_size: None,
        prepared_at_ms: None,
        completed_at_ms: None,
    };
    log.begin_batch().unwrap();
    let mut writer = log.clone();
    writer.append(
        TimestampMs::new(1),
        &CorrelationId::new("actual-original-upload").unwrap(),
        None,
        EventPayload::ArtifactUploadTransition {
            record: record.clone(),
        },
    );
    record.phase = ArtifactUploadPhase::Prepared;
    record.content_digest = Some(key.content_digest.clone());
    record.byte_size = Some(key.byte_size);
    record.prepared_at_ms = Some(2);
    writer.append(
        TimestampMs::new(2),
        &CorrelationId::new("actual-original-upload").unwrap(),
        None,
        EventPayload::ArtifactUploadTransition {
            record: record.clone(),
        },
    );
    if completed {
        record.phase = ArtifactUploadPhase::Completed;
        record.completed_at_ms = Some(3);
        writer.append(
            TimestampMs::new(3),
            &CorrelationId::new("actual-original-upload").unwrap(),
            None,
            EventPayload::ArtifactUploadTransition { record },
        );
    }
    save(
        &log,
        "original-initial-command",
        "{\"original\":1}",
        if completed { 3 } else { 2 },
    );
    log.commit_batch().unwrap();
    (log, cas, key)
}

/// Real direct storage checks inherited origin and owned Snapshot pins; it claims no network quorum.
#[tokio::test]
async fn cross_owner_prepared_release_retains_origin_and_independent_root_pins() {
    let directory = tempfile::tempdir().unwrap();
    let (log, cas, key) =
        original_upload(directory.path(), b"original Prepared release bytes", false);
    let profile = "a".repeat(64);
    let root = ReplicaContentStore::create(&directory.path().join("root"), &profile, 1).unwrap();
    let mut store = SqliteRaftStore::open_with_content(
        &directory.path().join("replica.sqlite3"),
        &profile,
        state::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA,
        root.clone(),
    )
    .unwrap();
    let first = candidate(
        &directory.path().join("source.sqlite3"),
        &cas,
        &ReplicaView::default(),
        &profile,
    );
    assert!(apply(&mut store, first.clone(), 1).await.accepted);
    root.copy_from_artifact(
        &cas,
        &key,
        "original-copy",
        &first.identity().unwrap(),
        30000,
    )
    .unwrap();
    let held_snapshot = store.build_snapshot().await.unwrap();
    let previous = store.accepted_view().unwrap();
    let EventPayload::ArtifactUploadTransition { mut record } =
        serde_json::from_str(&previous.events.last().unwrap().payload_json).unwrap()
    else {
        panic!("fixture expected original Prepared");
    };
    record.phase = ArtifactUploadPhase::Abandoned;
    log.begin_batch().unwrap();
    log.clone().append(
        TimestampMs::new(4),
        &CorrelationId::new("explicit-original-release").unwrap(),
        None,
        EventPayload::ArtifactUploadTransition {
            record: record.clone(),
        },
    );
    save(&log, "explicit-original-release", "{\"original\":2}", 3);
    log.commit_batch().unwrap();
    let destination = artifact_store::FileSystemArtifactStore::new_with_archive_references(
        directory.path().join("destination"),
    )
    .unwrap();
    let release = candidate(
        &directory.path().join("source.sqlite3"),
        &destination,
        &previous,
        &profile,
    );
    assert_ne!(release.source_writer.owner, key.source_owner);
    assert_eq!(
        release.origins.get(&release.batch.events[0].event_id),
        Some(&key)
    );
    assert!(release.inputs.is_empty());
    let mut archived = previous.clone();
    archived
        .content
        .as_mut()
        .unwrap()
        .candidate
        .recovery
        .catalog_archive_events = Some(std::mem::take(&mut archived.events));
    assert_eq!(
        crate::content_uploads::abandoned_origin(
            &archived,
            &EventPayload::ArtifactUploadTransition {
                record: record.clone()
            },
        )
        .unwrap(),
        Some(key.clone()),
    );
    let mut relabelled = release.clone();
    relabelled
        .origins
        .get_mut(&release.batch.events[0].event_id)
        .unwrap()
        .source_owner = destination.owner_id().unwrap().into();
    assert!(relabelled.validate_against(&previous, &profile).is_err());
    let mut changed = release.clone();
    record.deadline_ms += 1;
    changed.batch.events[0].payload_json =
        serde_json::to_string(&EventPayload::ArtifactUploadTransition {
            record: record.clone(),
        })
        .unwrap();
    assert!(changed.validate_against(&previous, &profile).is_err());
    record.deadline_ms -= 1;
    record.phase = ArtifactUploadPhase::Completed;
    record.completed_at_ms = Some(4);
    changed.batch.events[0].payload_json =
        serde_json::to_string(&EventPayload::ArtifactUploadTransition { record }).unwrap();
    assert!(changed.validate_against(&previous, &profile).is_err());
    assert!(apply(&mut store, release, 2).await.accepted);
    let view = store.accepted_view().unwrap();
    assert!(view.content.as_ref().unwrap().inputs.is_empty());
    assert!(
        root.active_references(&key)
            .unwrap()
            .iter()
            .any(|pin| pin.kind == ReplicaContentReferenceKind::Snapshot)
    );
    root.verify_content(&key).unwrap();
    drop(held_snapshot);
    assert!(
        view.content
            .as_ref()
            .unwrap()
            .published_content(&view.events, &key.content_digest)
            .is_err()
    );
}

/// Writes one original exact authority command and checkpoint inside the already-open actual SQL batch.
fn save(log: &state::SqliteEventLog, id: &str, json: &str, sequence: u64) {
    assert!(log.take_error().unwrap().is_none());
    log.save_checkpoint_with_command(
        state::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA,
        json,
        state::ControlCommandJournalEntry {
            command_id: id.into(),
            causation_id: None,
            authority_revision: 0,
            controller_epoch: None,
            plan_digest: None,
            input_digest: format!("sha256:{:x}", Sha256::digest(id.as_bytes())),
            ownership_digest: None,
            resulting_state_digest: format!("sha256:{:x}", Sha256::digest(json.as_bytes())),
            checkpoint_event_sequence: sequence,
            checkpoint_schema: state::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA.into(),
        },
    )
    .unwrap();
}

/// Captures actual current source rows and constructs only the exact original candidate delta.
pub(crate) fn candidate(
    path: &Path,
    cas: &artifact_store::FileSystemArtifactStore,
    previous: &ReplicaView,
    profile: &str,
) -> ContentApplicationBatch {
    let current = state::SqliteEventLog::read_complete_replica_state(path).unwrap();
    let last = previous
        .commands
        .last()
        .map_or(0, |command| command.authority_revision);
    let sequence = previous
        .checkpoint
        .as_ref()
        .map_or(0, |checkpoint| checkpoint.event_sequence);
    let batch = state::ApplicationCommitBatch {
        schema: state::APPLICATION_COMMIT_SCHEMA.into(),
        previous_event_sequence: sequence,
        previous_checkpoint_digest: previous.checkpoint.as_ref().map(state::checkpoint_identity),
        checkpoint: current.checkpoint,
        events: current
            .events
            .into_iter()
            .filter(|event| event.sequence > sequence)
            .collect(),
        commands: current
            .commands
            .into_iter()
            .filter(|command| command.authority_revision > last)
            .collect(),
    };
    ContentApplicationBatch::from_source(
        profile,
        ControllerEpoch {
            term: 1,
            replica_id: 1,
        },
        cas,
        previous,
        batch,
        current.recovery,
    )
    .unwrap()
}

/// Enters the actual maintained Raft storage trait directly, without claiming a real election/quorum.
async fn apply(
    store: &mut SqliteRaftStore,
    candidate: ContentApplicationBatch,
    index: u64,
) -> CommitReceipt {
    store
        .apply([Entry {
            log_id: LogId::new(CommittedLeaderId::new(1, 1), index),
            payload: EntryPayload::Normal(ReplicaCommand::ContentApplication {
                content_application: Box::new(candidate),
            }),
        }])
        .await
        .unwrap()
        .pop()
        .unwrap()
}

/// Complete metadata apply cannot publish a Snapshot with missing bytes; independent live readers survive replacement.
#[tokio::test]
async fn actual_snapshot_inputs_and_independent_reader_lifetime_survive_replacement_and_reopen() {
    let directory = tempfile::tempdir().unwrap();
    let (log, cas, key) = original(directory.path());
    let profile = "a".repeat(64);
    let root = directory.path().join("member-content");
    let content = ReplicaContentStore::create(&root, &profile, 1).unwrap();
    let database = directory.path().join("replica.sqlite3");
    let mut store = SqliteRaftStore::open_with_content(
        &database,
        &profile,
        state::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA,
        content.clone(),
    )
    .unwrap();
    let first = candidate(
        &directory.path().join("source.sqlite3"),
        &cas,
        &ReplicaView::default(),
        &profile,
    );
    assert_eq!(first.inputs, vec![key.clone()]);
    assert!(apply(&mut store, first.clone(), 1).await.accepted);
    assert!(store.verify_accepted_content().is_err());
    assert!(store.build_snapshot().await.is_err());
    content
        .copy_from_artifact(
            &cas,
            &key,
            "actual-content-copy",
            &first.identity().unwrap(),
            30000,
        )
        .unwrap();
    store.verify_accepted_content().unwrap();
    let first_snapshot = store.build_snapshot().await.unwrap();
    let extra_reader = store.get_current_snapshot().await.unwrap().unwrap();
    let live_readers = content
        .active_references(&key)
        .unwrap()
        .into_iter()
        .filter(|pin| pin.kind == ReplicaContentReferenceKind::Reader)
        .collect::<Vec<_>>();
    assert_eq!(live_readers.len(), 2);
    let previous = store.accepted_view().unwrap();
    log.begin_batch().unwrap();
    save(&log, "original-next-command", "{\"original\":2}", 3);
    log.commit_batch().unwrap();
    let second = candidate(
        &directory.path().join("source.sqlite3"),
        &cas,
        &previous,
        &profile,
    );
    assert!(apply(&mut store, second, 2).await.accepted);
    let second_snapshot = store.build_snapshot().await.unwrap();
    assert_ne!(
        first_snapshot.meta.snapshot_id,
        second_snapshot.meta.snapshot_id
    );
    let pins = content.active_references(&key).unwrap();
    assert_eq!(
        pins.iter()
            .filter(|pin| pin.kind == ReplicaContentReferenceKind::Snapshot)
            .count(),
        1
    );
    for reader in &live_readers {
        assert!(pins.contains(reader));
    }
    drop(first_snapshot);
    let closure = content
        .wait_for_snapshot_closures(Duration::from_secs(3))
        .await
        .unwrap();
    assert_eq!(closure.publication_errors_total, 0);
    assert_eq!(
        content
            .active_references(&key)
            .unwrap()
            .iter()
            .filter(|pin| pin.kind == ReplicaContentReferenceKind::Reader)
            .count(),
        2
    );
    drop(extra_reader);
    drop(second_snapshot);
    let closure = content
        .wait_for_snapshot_closures(Duration::from_secs(3))
        .await
        .unwrap();
    assert_eq!(closure.publication_errors_total, 0);
    drop(store);
    drop(content);
    let content = ReplicaContentStore::open(&root, &profile, 1).unwrap();
    let store = SqliteRaftStore::open_with_content(
        &database,
        &profile,
        state::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA,
        content.clone(),
    )
    .unwrap();
    store.verify_accepted_content().unwrap();
    assert!(
        content
            .active_references(&key)
            .unwrap()
            .iter()
            .all(|pin| pin.kind != ReplicaContentReferenceKind::Reader)
    );
    assert!(
        SqliteRaftStore::open(
            &database,
            &profile,
            state::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA
        )
        .is_err()
    );
}

/// The explicit new storage generation refuses both hidden legacy commands and missing original content heads.
#[tokio::test]
async fn actual_complete_storage_refuses_legacy_commit_and_missing_mandatory_head() {
    let directory = tempfile::tempdir().unwrap();
    let (_log, cas, _key) = original(directory.path());
    let profile = "b".repeat(64);
    let root = directory.path().join("member-content");
    let content = ReplicaContentStore::create(&root, &profile, 1).unwrap();
    let database = directory.path().join("replica.sqlite3");
    let mut store = SqliteRaftStore::open_with_content(
        &database,
        &profile,
        state::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA,
        content.clone(),
    )
    .unwrap();
    let candidate = candidate(
        &directory.path().join("source.sqlite3"),
        &cas,
        &ReplicaView::default(),
        &profile,
    );
    let reply = store
        .apply([Entry {
            log_id: LogId::new(CommittedLeaderId::new(1, 1), 1),
            payload: EntryPayload::Normal(candidate.batch.into()),
        }])
        .await
        .unwrap();
    assert!(!reply[0].accepted);
    assert!(store.accepted_view().unwrap().checkpoint.is_none());
    drop(store);
    let connection = rusqlite::Connection::open(&database).unwrap();
    connection
        .execute("DELETE FROM replica_v1_meta WHERE key='content_state'", [])
        .unwrap();
    drop(connection);
    assert!(
        SqliteRaftStore::open_with_content(
            &database,
            &profile,
            state::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA,
            content
        )
        .err()
        .unwrap()
        .contains("root/head")
    );
}

/// Actual Snapshot metadata cannot install until that independent receiver durably has all original bytes.
#[tokio::test]
async fn actual_snapshot_install_checks_receiver_bytes_and_preserves_full_original_state() {
    let directory = tempfile::tempdir().unwrap();
    let (_log, cas, key) = original(directory.path());
    let profile = "c".repeat(64);
    let source =
        ReplicaContentStore::create(&directory.path().join("first-content"), &profile, 1).unwrap();
    let mut first = SqliteRaftStore::open_with_content(
        &directory.path().join("first.sqlite3"),
        &profile,
        state::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA,
        source.clone(),
    )
    .unwrap();
    let candidate = candidate(
        &directory.path().join("source.sqlite3"),
        &cas,
        &ReplicaView::default(),
        &profile,
    );
    source
        .copy_from_artifact(
            &cas,
            &key,
            "first-copy",
            &candidate.identity().unwrap(),
            30000,
        )
        .unwrap();
    assert!(apply(&mut first, candidate, 1).await.accepted);
    let snapshot = first.build_snapshot().await.unwrap();
    let meta = snapshot.meta;
    let bytes = snapshot.snapshot.into_inner();
    let target =
        ReplicaContentStore::create(&directory.path().join("second-content"), &profile, 2).unwrap();
    let mut second = SqliteRaftStore::open_with_content(
        &directory.path().join("second.sqlite3"),
        &profile,
        state::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA,
        target.clone(),
    )
    .unwrap();
    assert!(
        second
            .install_snapshot(&meta, Box::new(ReplicaSnapshotData::new(bytes.clone())))
            .await
            .is_err()
    );
    assert!(second.accepted_view().unwrap().checkpoint.is_none());
    target
        .copy_from_artifact(&cas, &key, "second-copy", &"e".repeat(64), 30000)
        .unwrap();
    second
        .install_snapshot(&meta, Box::new(ReplicaSnapshotData::new(bytes)))
        .await
        .unwrap();
    second.verify_accepted_content().unwrap();
    let restored = second.accepted_view().unwrap();
    let original = first.accepted_view().unwrap();
    assert_eq!(restored.content, original.content);
    assert_eq!(restored.events, original.events);
    assert_eq!(restored.commands, original.commands);
    let materialized =
        state::SqliteEventLog::open(directory.path().join("materialized.sqlite3")).unwrap();
    materialized
        .install_replica_authority_with_recovery(
            &restored.events,
            restored.checkpoint.as_ref().unwrap(),
            &restored.commands,
            &restored.content.unwrap().recovery,
        )
        .unwrap();
    assert_eq!(
        materialized.archive_authority_id().unwrap(),
        original.content.unwrap().recovery.metadata["archive_authority_id"]
    );
    assert_eq!(
        target
            .active_references(&key)
            .unwrap()
            .iter()
            .filter(|pin| pin.kind == ReplicaContentReferenceKind::Snapshot)
            .count(),
        1
    );
    assert_eq!(
        target
            .active_references(&key)
            .unwrap()
            .iter()
            .filter(|pin| pin.kind == ReplicaContentReferenceKind::View)
            .count(),
        1
    );
}

/// Original required-set lineage refuses omission, foreign ownership and false old-command inheritance.
#[test]
fn original_complete_content_input_and_history_bindings_cannot_be_relabelled() {
    let directory = tempfile::tempdir().unwrap();
    let (_log, cas, _key) = original(directory.path());
    let profile = "d".repeat(64);
    let previous = ReplicaView::default();
    let candidate = candidate(
        &directory.path().join("source.sqlite3"),
        &cas,
        &previous,
        &profile,
    );
    let mut missing = candidate.clone();
    missing.inputs.clear();
    assert!(missing.validate_against(&previous, &profile).is_err());
    let mut foreign = candidate.clone();
    foreign
        .origins
        .values_mut()
        .for_each(|origin| origin.source_owner = format!("artifact-store:{}", "e".repeat(64)));
    assert!(
        foreign
            .validate_against(&previous, &profile)
            .unwrap_err()
            .contains("actual original source writer")
    );
    let mut omitted = candidate.clone();
    omitted.origins.pop_first();
    assert!(omitted.validate_against(&previous, &profile).is_err());
}

/// Actual Completed history allows the original key; pure Prepared and ambiguous-owner DTOs refuse.
#[test]
fn published_root_reads_require_completed_or_published_original_content() {
    let directory = tempfile::tempdir().unwrap();
    let (_log, cas, key) = original(directory.path());
    let candidate = candidate(
        &directory.path().join("source.sqlite3"),
        &cas,
        &ReplicaView::default(),
        &"d".repeat(64),
    );
    assert_eq!(
        candidate
            .published_content(&candidate.batch.events, &key.content_digest)
            .unwrap(),
        key
    );
    let prepared_events = &candidate.batch.events[..2];
    assert!(
        candidate
            .published_content(prepared_events, &key.content_digest)
            .is_err()
    );
    let mut ambiguous = candidate.clone();
    let mut completed = ambiguous.batch.events[2].clone();
    completed.event_id = "synthetic-other-owner-same-bytes".into();
    let mut payload: EventPayload = serde_json::from_str(&completed.payload_json).unwrap();
    let EventPayload::ArtifactUploadTransition { record } = &mut payload else {
        panic!("fixture expected actual upload");
    };
    record.upload_id = "synthetic-distinct-completed-upload".into();
    completed.payload_json = serde_json::to_string(&payload).unwrap();
    let other = ArtifactContentKey {
        source_owner: format!("artifact-store:{}", "f".repeat(64)),
        ..key.clone()
    };
    ambiguous
        .origins
        .insert(completed.event_id.clone(), other.clone());
    ambiguous.inputs.push(other);
    ambiguous.inputs.sort();
    ambiguous.batch.events.push(completed);
    assert!(
        ambiguous
            .published_content(&ambiguous.batch.events, &key.content_digest)
            .unwrap_err()
            .contains("ambiguous")
    );
}

/// Metadata counterexamples never grant quorum: retirement needs the exact unretired original upload.
#[test]
fn completed_retirement_cannot_borrow_equal_hash_owner_or_retired_catalog_retention() {
    let directory = tempfile::tempdir().unwrap();
    let (_log, cas, key) = original(directory.path());
    let batch = candidate(
        &directory.path().join("source.sqlite3"),
        &cas,
        &ReplicaView::default(),
        &"d".repeat(64),
    );
    let mut view = ReplicaView {
        content: Some(batch.accepted_state().unwrap()),
        events: batch.batch.events.clone(),
        ..ReplicaView::default()
    };
    let EventPayload::ArtifactUploadTransition { record } =
        serde_json::from_str(&view.events.last().unwrap().payload_json).unwrap()
    else {
        panic!("expected original Completed");
    };
    assert_eq!(
        crate::content_uploads::retained_upload(&view, &record).unwrap(),
        key
    );
    let mut changed = record.clone();
    changed.deadline_ms += 1;
    assert!(crate::content_uploads::retained_upload(&view, &changed).is_err());
    let mut other = view.clone();
    other.content.as_mut().unwrap().candidate.inputs[0].source_owner =
        format!("artifact-store:{}", "f".repeat(64));
    assert!(crate::content_uploads::retained_upload(&other, &record).is_err());
    let mut prepared = view.clone();
    prepared.events.pop();
    assert!(crate::content_uploads::retained_upload(&prepared, &record).is_err());
    let mut archived = view.clone();
    archived
        .content
        .as_mut()
        .unwrap()
        .candidate
        .recovery
        .catalog_archive_events = Some(std::mem::take(&mut archived.events));
    assert_eq!(
        crate::content_uploads::retained_upload(&archived, &record).unwrap(),
        key
    );
    let receipt = ArtifactUploadRetirement {
        schema: "roboguide.artifact-upload-retirement/v0.1".into(),
        command_id: "contract-retirement".into(),
        store_owner_id: key.source_owner.clone(),
        upload: record.clone(),
        principal_id: record.principal_id.clone(),
        tenant_id: record.tenant_id.clone(),
        certificate_digest: None,
        canonical_grant_json: "{}".into(),
        admitted_at_ms: 4,
        reason: "explicit contract release".into(),
    };
    receipt.validate().unwrap();
    let mut row = view.events.last().unwrap().clone();
    row.event_id = "contract-retirement".into();
    row.sequence += 1;
    row.timestamp_ms = 4;
    row.payload_schema = "domain.EventPayload.json/v31".into();
    row.payload_json =
        serde_json::to_string(&EventPayload::ArtifactUploadRetired { receipt }).unwrap();
    view.events.push(row);
    assert!(view.content.as_ref().unwrap().inputs.contains(&key));
    assert!(crate::content_uploads::retained_upload(&view, &record).is_err());
}
