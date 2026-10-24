//! Actual SQL/FD/CAS receive tests; declared target data below is no election, transport or device proof.

use super::*;
use crate::ReplicaMember;
use openraft::{CommittedLeaderId, LogId, Membership, StoredMembership};
use std::collections::{BTreeMap, BTreeSet};
use std::os::fd::AsRawFd;
use std::sync::atomic::{AtomicBool, Ordering};

/// Publishes a real owned source file and fresh original v4 receiver, with no Controller or Nodes.
fn fixture() -> (
    tempfile::TempDir,
    artifact_store::FileSystemArtifactStore,
    ReplicaContentStore,
    ArtifactContentKey,
    Vec<u8>,
) {
    let directory = tempfile::tempdir().unwrap();
    let source = artifact_store::FileSystemArtifactStore::new_with_archive_references(
        directory.path().join("source"),
    )
    .unwrap();
    let bytes = (0..CHUNK_BYTES * 3 + 7)
        .map(|index| (index % 251) as u8)
        .collect::<Vec<_>>();
    let checksum = artifact_store::digest_bytes(&bytes);
    let mut upload = source
        .begin_upload("actual-original-stream-source")
        .unwrap();
    upload.write_chunk(&bytes).unwrap();
    upload.finalize(&checksum, bytes.len() as u64).unwrap();
    let key = ArtifactContentKey {
        source_owner: source.owner_id().unwrap().into(),
        content_digest: checksum,
        byte_size: bytes.len() as u64,
    };
    let receiver = ReplicaContentStore::create_with_streams(
        &directory.path().join("receiver"),
        &"a".repeat(64),
        1,
    )
    .unwrap();
    (directory, source, receiver, key, bytes)
}

/// Builds labelled retention context over the actual receiver; it is not a real committed membership.
fn target(receiver: &ReplicaContentStore, key: &ArtifactContentKey) -> ContentQuorumTarget {
    let member = ReplicaMember::with_content_store(
        "127.0.0.1:32317",
        receiver.identity().store_owner.clone(),
    )
    .unwrap();
    ContentQuorumTarget {
        profile_identity: receiver.identity().profile_identity.clone(),
        epoch: crate::ControllerEpoch {
            term: 1,
            replica_id: 1,
        },
        revision: 1,
        checkpoint_digest: "b".repeat(64),
        batch_digest: "c".repeat(64),
        membership: StoredMembership::new(
            Some(LogId::new(CommittedLeaderId::new(1, 1), 1)),
            Membership::new(vec![BTreeSet::from([1])], BTreeMap::from([(1, member)])),
        ),
        member_stores: BTreeMap::from([(1, receiver.identity().store_owner.clone())]),
        inputs: vec![key.clone()],
    }
}

/// Explicitly upgrades only the fixture's drained original Root4, retaining its real storage identity.
fn reclamation(receiver: ReplicaContentStore) -> ReplicaContentStore {
    let root = receiver.root().to_owned();
    let identity = receiver.identity().clone();
    drop(receiver);
    drop(
        ReplicaContentStore::upgrade_snapshot_receives(
            &root,
            &identity.profile_identity,
            identity.member,
            None,
        )
        .unwrap(),
    );
    ReplicaContentStore::upgrade_reclamation(
        &root,
        &identity.profile_identity,
        identity.member,
        None,
    )
    .unwrap()
}

/// A completed real stream can retire while a live independent reader still protects its actual bytes.
#[test]
fn root7_retired_stream_keeps_independent_reader_and_original_receive_history() {
    let (_directory, source, receiver, key, _bytes) = fixture();
    let receiver = reclamation(receiver);
    let target = target(&receiver, &key);
    let original = receiver
        .copy_artifact_stream(&source, target.clone(), &key, "original-gc-stream", 30_000)
        .unwrap();
    let reader = receiver
        .retain_reader_scope(
            "independent-reader",
            std::slice::from_ref(&key),
            &"d".repeat(64),
        )
        .unwrap();
    let retired = receiver
        .retire_stream("original-gc-stream", 30_000)
        .unwrap();
    assert_eq!(retired["original"], original.original);
    assert!(retired["process_end"].is_null());
    assert_eq!(
        receiver
            .collect_unreferenced_content("reader-blocked", 30_000)
            .unwrap()
            .candidates,
        0
    );
    drop(reader);
    assert_eq!(
        receiver
            .collect_unreferenced_content("original-stream-unlink", 30_000)
            .unwrap()
            .deleted,
        1
    );
    assert_eq!(
        receiver.stream_status("original-gc-stream").unwrap(),
        Some(original)
    );
    assert_eq!(
        receiver
            .retire_stream("original-gc-stream", 30_000)
            .unwrap(),
        retired
    );
    assert!(
        receiver
            .copy_artifact_stream(&source, target, &key, "forbidden-new-stream", 30_000)
            .is_err()
    );
}

/// An actual live writer cannot be retired; its later actual Drop closes only that original Unknown.
#[test]
fn root7_live_stream_retirement_refuses_until_actual_worker_close() {
    let (_directory, source, receiver, key, _bytes) = fixture();
    let receiver = reclamation(receiver);
    let (origin, permission) = origin(&source, Arc::new(AtomicBool::new(true)));
    let admission = receiver
        .admit_stream(
            "held-original-stream",
            target(&receiver, &key),
            key.clone(),
            origin,
            ContentBudget::issue(30_000).unwrap(),
            permission,
        )
        .unwrap();
    let StreamAdmission::New(worker) = admission else {
        panic!("fresh original worker required")
    };
    let original = receiver
        .stream_status("held-original-stream")
        .unwrap()
        .unwrap();
    assert!(
        receiver
            .retire_stream("held-original-stream", 30_000)
            .is_err()
    );
    assert_eq!(
        receiver.stream_status("held-original-stream").unwrap(),
        Some(original)
    );
    drop(worker);
    let closed = receiver
        .stream_status("held-original-stream")
        .unwrap()
        .unwrap();
    assert!(closed.receipt.is_none());
    let retired = receiver
        .retire_stream("held-original-stream", 30_000)
        .unwrap();
    assert_eq!(retired["original"], closed.original);
    assert!(receiver.active_references(&key).unwrap().is_empty());
    assert!(
        receiver
            .stream_status("held-original-stream")
            .unwrap()
            .unwrap()
            .receipt
            .is_none()
    );
}

/// Retains the actual source writer in a live permission; an optional test fence grants no network authority.
fn origin(
    source: &artifact_store::FileSystemArtifactStore,
    permitted: Arc<AtomicBool>,
) -> (StreamOrigin, StreamPermission) {
    let actual = source.writer_identity().unwrap();
    let writer = ApplicationArtifactWriter {
        owner: actual.owner_id,
        device: actual.lease_device,
        inode: actual.lease_inode,
    };
    let held = source.clone();
    let expected = writer.clone();
    let permission: StreamPermission = Arc::new(move || {
        let current = held.writer_identity().map_err(|error| error.to_string())?;
        if !permitted.load(Ordering::SeqCst)
            || (current.owner_id, current.lease_device, current.lease_inode)
                != (expected.owner.clone(), expected.device, expected.inode)
        {
            return Err("explicit original source permission unavailable".into());
        }
        Ok(())
    });
    (StreamOrigin::Artifact { writer }, permission)
}

/// Admits exactly one actual original write worker; duplicate outcomes cannot masquerade as permits.
fn admit(
    receiver: &ReplicaContentStore,
    source: &artifact_store::FileSystemArtifactStore,
    key: &ArtifactContentKey,
    command: &str,
    permitted: Arc<AtomicBool>,
) -> Box<writer::StreamWriter> {
    let (origin, permission) = origin(source, permitted);
    match receiver
        .admit_stream(
            command,
            target(receiver, key),
            key.clone(),
            origin,
            ContentBudget::issue(30000).unwrap(),
            permission,
        )
        .unwrap()
    {
        StreamAdmission::New(worker) => worker,
        StreamAdmission::Existing(_) => {
            panic!("new original fixture unexpectedly returned old evidence")
        }
    }
}

/// Actual bounded source reads produce exact bytes/SQL receipt; same-ID replay preserves first evidence.
#[test]
fn actual_artifact_stream_hash_receipt_reopen_and_duplicate_preserve_original_evidence() {
    let (directory, source, receiver, key, _bytes) = fixture();
    let context = target(&receiver, &key);
    let original = receiver
        .copy_artifact_stream(
            &source,
            context.clone(),
            &key,
            "actual-original-stream",
            30000,
        )
        .unwrap();
    assert!(original.receipt.is_some());
    assert!(original.original["worker_closed_ms"].is_number());
    receiver.verify_content(&key).unwrap();
    assert_eq!(receiver.active_references(&key).unwrap().len(), 1);
    assert_eq!(
        receiver.stream_status("actual-original-stream").unwrap(),
        Some(original.clone())
    );
    let actual_source = source.open_artifact(&key.content_digest).unwrap();
    let source_path =
        std::fs::read_link(format!("/proc/self/fd/{}", actual_source.as_raw_fd())).unwrap();
    drop(actual_source);
    let hidden = source_path.with_extension("original-held");
    std::fs::rename(&source_path, &hidden).unwrap();
    assert!(source.open_artifact(&key.content_digest).is_err());
    assert_eq!(
        receiver
            .copy_artifact_stream(
                &source,
                context.clone(),
                &key,
                "actual-original-stream",
                30000
            )
            .unwrap(),
        original
    );
    std::fs::rename(hidden, source_path).unwrap();
    let identity = receiver.identity().clone();
    drop(receiver);
    let receiver = ReplicaContentStore::open_with_streams(
        &directory.path().join("receiver"),
        &"a".repeat(64),
        1,
    )
    .unwrap();
    assert_eq!(receiver.identity(), &identity);
    assert_eq!(
        receiver.stream_status("actual-original-stream").unwrap(),
        Some(original.clone())
    );
    receiver.verify_content(&key).unwrap();
    assert_eq!(
        receiver
            .copy_artifact_stream(&source, context, &key, "actual-original-stream", 30000)
            .unwrap(),
        original
    );
}

/// A failed original chunk irreversibly fences that writer; no ignored error can publish a receipt.
#[test]
fn actual_chunk_order_failure_poison_and_scope_close_leave_original_unknown() {
    let (_directory, source, receiver, key, bytes) = fixture();
    let mut worker = admit(
        &receiver,
        &source,
        &key,
        "actual-order-failure",
        Arc::new(AtomicBool::new(true)),
    );
    assert!(worker.write_chunk(1, &bytes[..10]).is_err());
    assert!(
        worker
            .write_chunk(0, &bytes[..10])
            .unwrap_err()
            .contains("earlier failure")
    );
    assert!(worker.finish().unwrap_err().contains("earlier failure"));
    let original = receiver
        .stream_status("actual-order-failure")
        .unwrap()
        .unwrap();
    assert!(original.receipt.is_none());
    assert!(original.original["worker_closed_ms"].is_number());
    assert_eq!(receiver.active_references(&key).unwrap().len(), 1);
    assert!(receiver.verify_content(&key).is_err());
    assert_eq!(
        receiver
            .copy_artifact_stream(
                &source,
                target(&receiver, &key),
                &key,
                "actual-order-failure",
                30000
            )
            .unwrap(),
        original
    );
}

/// Current source revocation after admission cannot be bypassed by restoring permission on that worker.
#[test]
fn actual_permission_rejection_fences_future_chunks_without_renewing_original_intent() {
    let (_directory, source, receiver, key, bytes) = fixture();
    let permitted = Arc::new(AtomicBool::new(true));
    let mut worker = admit(
        &receiver,
        &source,
        &key,
        "actual-permission-fence",
        permitted.clone(),
    );
    let before = receiver
        .stream_status("actual-permission-fence")
        .unwrap()
        .unwrap();
    permitted.store(false, Ordering::SeqCst);
    assert!(
        worker
            .write_chunk(0, &bytes[..10])
            .unwrap_err()
            .contains("permission unavailable")
    );
    permitted.store(true, Ordering::SeqCst);
    assert!(
        worker
            .write_chunk(0, &bytes[..10])
            .unwrap_err()
            .contains("earlier failure")
    );
    drop(worker);
    let after = receiver
        .stream_status("actual-permission-fence")
        .unwrap()
        .unwrap();
    assert_eq!(after.original["intent"], before.original["intent"]);
    assert!(after.receipt.is_none());
}

/// Real parent-independent SQLite contention after actual file publication retains Unknown and exact bytes.
#[test]
fn actual_stream_sql_failure_after_file_publication_preserves_unknown_beside_distinct_repair() {
    let (_directory, source, receiver, key, bytes) = fixture();
    let mut worker = admit(
        &receiver,
        &source,
        &key,
        "actual-sql-unknown",
        Arc::new(AtomicBool::new(true)),
    );
    for (index, chunk) in bytes.chunks(CHUNK_BYTES).enumerate() {
        worker
            .write_chunk((index * CHUNK_BYTES) as u64, chunk)
            .unwrap();
    }
    let fault = Connection::open(receiver.root().join("content.sqlite3")).unwrap();
    fault.execute_batch("BEGIN IMMEDIATE").unwrap();
    assert!(worker.finish().unwrap_err().contains("locked"));
    fault.execute_batch("ROLLBACK").unwrap();
    drop(fault);
    let original = receiver
        .stream_status("actual-sql-unknown")
        .unwrap()
        .unwrap();
    assert!(original.receipt.is_none());
    assert!(original.original["worker_closed_ms"].is_null());
    assert!(super::super::copy::object_path(receiver.root(), &key).is_file());
    assert!(receiver.verify_content(&key).is_err());
    let repaired = receiver
        .copy_artifact_stream(
            &source,
            target(&receiver, &key),
            &key,
            "actual-independent-repair",
            30000,
        )
        .unwrap();
    assert!(repaired.receipt.is_some());
    receiver.verify_content(&key).unwrap();
    assert_eq!(
        receiver.stream_status("actual-sql-unknown").unwrap(),
        Some(original)
    );
}

/// Actual worker capacity refuses a new intent while original readonly duplicate observation remains available.
#[test]
fn actual_stream_capacity_precedes_new_intent_and_never_blocks_original_observation() {
    let (_directory, source, receiver, key, _bytes) = fixture();
    let workers = (0..8)
        .map(|index| {
            admit(
                &receiver,
                &source,
                &key,
                &format!("actual-held-{index}"),
                Arc::new(AtomicBool::new(true)),
            )
        })
        .collect::<Vec<_>>();
    assert!(
        receiver
            .copy_artifact_stream(
                &source,
                target(&receiver, &key),
                &key,
                "actual-over-capacity",
                30000
            )
            .unwrap_err()
            .contains("capacity is exhausted")
    );
    assert!(
        receiver
            .stream_status("actual-over-capacity")
            .unwrap()
            .is_none()
    );
    assert!(
        receiver
            .copy_artifact_stream(
                &source,
                target(&receiver, &key),
                &key,
                "actual-held-0",
                30000
            )
            .unwrap()
            .receipt
            .is_none()
    );
    drop(workers);
    for index in 0..8 {
        assert!(
            receiver
                .stream_status(&format!("actual-held-{index}"))
                .unwrap()
                .unwrap()
                .original["worker_closed_ms"]
                .is_number()
        );
    }
}

/// An explicit drained format upgrade preserves old rows/identity; old constructors cannot acquire v4.
#[test]
fn actual_original_stream_upgrade_preserves_history_and_refuses_lost_mandatory_head() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("root");
    let original = ReplicaContentStore::create_with_producers(&path, &"a".repeat(64), 1).unwrap();
    let identity = original.identity().clone();
    assert!(ReplicaContentStore::upgrade_stream_journal(&path, &"a".repeat(64), 1).is_err());
    drop(original);
    let upgraded = ReplicaContentStore::upgrade_stream_journal(&path, &"a".repeat(64), 1).unwrap();
    assert_eq!(upgraded.identity(), &identity);
    drop(upgraded);
    assert!(ReplicaContentStore::open_with_producers(&path, &"a".repeat(64), 1).is_err());
    let sql = Connection::open(path.join("content.sqlite3")).unwrap();
    sql.execute("DELETE FROM metadata WHERE key='stream_ids'", [])
        .unwrap();
    drop(sql);
    assert!(ReplicaContentStore::open_with_streams(&path, &"a".repeat(64), 1).is_err());
}

/// Exact byte count does not substitute for actual digest; a failed final hash never publishes metadata.
#[test]
fn actual_stream_wrong_hash_cannot_publish_or_erase_the_original_partial_file() {
    let (_directory, source, receiver, key, bytes) = fixture();
    let mut worker = admit(
        &receiver,
        &source,
        &key,
        "actual-wrong-hash",
        Arc::new(AtomicBool::new(true)),
    );
    let mut corrupted = bytes;
    corrupted[0] ^= 1;
    for (index, chunk) in corrupted.chunks(CHUNK_BYTES).enumerate() {
        worker
            .write_chunk((index * CHUNK_BYTES) as u64, chunk)
            .unwrap();
    }
    assert!(worker.finish().unwrap_err().contains("size/hash differs"));
    let original = receiver
        .stream_status("actual-wrong-hash")
        .unwrap()
        .unwrap();
    assert!(original.receipt.is_none());
    assert!(original.original["worker_closed_ms"].is_number());
    assert!(
        receiver
            .root()
            .join("staging-v1")
            .join(format!(
                "stream-{}",
                operation_id("actual-wrong-hash").unwrap()
            ))
            .is_file()
    );
    assert!(receiver.verify_content(&key).is_err());
}

/// Actual Prepared bytes can repair an independent recipient while public source CAS stays absent.
#[test]
fn actual_original_prepared_source_stream_preserves_unknown_and_never_publishes_source_cas() {
    let (_directory, source, receiver, _key, mut bytes) = fixture();
    bytes.extend(b"actual-unpublished-prepared-input");
    let key = ArtifactContentKey {
        source_owner: source.owner_id().unwrap().into(),
        content_digest: artifact_store::digest_bytes(&bytes),
        byte_size: bytes.len() as u64,
    };
    let mut upload = source
        .begin_upload("actual-original-unpublished-source")
        .unwrap();
    upload.write_chunk(&bytes).unwrap();
    let target = target(&receiver, &key);
    assert!(
        receiver
            .copy_artifact_stream(
                &source,
                target.clone(),
                &key,
                "actual-before-source-guard",
                30000
            )
            .is_err()
    );
    let unknown = receiver
        .stream_status("actual-before-source-guard")
        .unwrap()
        .unwrap();
    let guard = upload
        .prepared_replication_source(&key.content_digest, key.byte_size)
        .unwrap();
    assert!(source.open_artifact(&key.content_digest).is_err());
    assert_eq!(
        receiver
            .copy_artifact_stream(
                &source,
                target.clone(),
                &key,
                "actual-before-source-guard",
                30000
            )
            .unwrap(),
        unknown
    );
    let copied = receiver
        .copy_artifact_stream(
            &source,
            target,
            &key,
            "actual-distinct-prepared-copy",
            30000,
        )
        .unwrap();
    assert!(copied.receipt.is_some());
    receiver.verify_content(&key).unwrap();
    assert!(source.open_artifact(&key.content_digest).is_err());
    assert_eq!(
        receiver
            .stream_status("actual-before-source-guard")
            .unwrap(),
        Some(unknown)
    );
    drop(guard);
}
