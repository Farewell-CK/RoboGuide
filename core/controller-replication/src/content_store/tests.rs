//! Actual storage/lease counterexamples; labelled pin DTOs confer no content or deployment facts.

use super::*;

/// An actual complete close receipt cannot be substituted or lent another recovery admission binding.
#[test]
fn recovery_completion_requires_original_root_sql_receipt_and_authority() {
    let directory = tempfile::tempdir().unwrap();
    let profile = "a".repeat(64);
    let store =
        ReplicaContentStore::create_with_streams(&directory.path().join("root"), &profile, 1)
            .unwrap();
    let source_directory = directory.path().join("source");
    std::fs::create_dir(&source_directory).unwrap();
    let (_log, source, key) = crate::content_application_tests::original(&source_directory);
    store
        .copy_from_artifact(
            &source,
            &key,
            "actual-recovery-source",
            "a".repeat(64).as_str(),
            30000,
        )
        .unwrap();
    let authority = "b".repeat(64);
    let mut reader = store
        .open_reader(&key, "actual-recovery-reader", &authority, 30000)
        .unwrap();
    std::io::copy(&mut reader, &mut std::io::sink()).unwrap();
    let receipt = reader.finish().unwrap();
    store.require_reader_receipt(&receipt, &authority).unwrap();
    assert!(
        store
            .require_reader_receipt(&receipt, &"c".repeat(64))
            .is_err()
    );
    let mut forged = receipt.clone();
    forged.file_inode += 1;
    assert!(store.require_reader_receipt(&forged, &authority).is_err());
    assert_eq!(
        store
            .reader_status(&receipt.reader_id)
            .unwrap()
            .unwrap()
            .receipt,
        Some(receipt)
    );
}
use std::collections::BTreeSet;
use std::os::unix::fs::PermissionsExt;

/// Actual SQLite contention during Snapshot Drop must not stop an independent TCP client on one worker.
#[tokio::test(flavor = "current_thread")]
async fn actual_snapshot_release_keeps_single_network_worker_runnable() {
    use tokio::io::{AsyncReadExt as _, AsyncWriteExt as _};
    let directory = tempfile::tempdir().unwrap();
    let payload = std::fs::read(Path::new(env!("CARGO_MANIFEST_DIR")).join("Cargo.toml")).unwrap();
    let (source, key) = source(&directory.path().join("source"), &payload);
    let root = directory.path().join("content");
    let content = ReplicaContentStore::create(&root, &"a".repeat(64), 7).unwrap();
    content
        .copy_from_artifact(&source, &key, "actual-copy", &"b".repeat(64), 30_000)
        .unwrap();
    let lease = content
        .retain_reader_scope(
            "actual-snapshot-scope",
            std::slice::from_ref(&key),
            &"c".repeat(64),
        )
        .unwrap();
    let snapshot =
        crate::ReplicaSnapshotData::retained(b"explicit local metadata fixture".to_vec(), lease);
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let server = tokio::spawn(async move {
        let (mut stream, _) = listener.accept().await.unwrap();
        let mut request = [0; 4];
        stream.read_exact(&mut request).await.unwrap();
        assert_eq!(&request, b"ping");
        stream.write_all(b"actual worker reply").await
    });
    let (ready, started) = tokio::sync::oneshot::channel();
    let (release, held) = std::sync::mpsc::channel();
    let database = root.join("content.sqlite3");
    let writer = std::thread::spawn(move || {
        let database = Connection::open(database).unwrap();
        database.execute_batch("BEGIN EXCLUSIVE").unwrap();
        ready.send(()).unwrap();
        let _ = held.recv_timeout(std::time::Duration::from_secs(3));
        database.execute_batch("ROLLBACK").unwrap();
    });
    started.await.unwrap();
    let ready = directory.path().join("client-ready");
    let go = directory.path().join("client-go");
    let client = std::process::Command::new("python3")
        .args([
            "-c",
            r#"import json,socket,sys,time
from pathlib import Path
Path(sys.argv[2]).write_text('ready')
deadline=time.monotonic()+5
while not Path(sys.argv[3]).exists():
    assert time.monotonic()<deadline, 'original client start signal unavailable'
    time.sleep(.001)
started=time.monotonic()
with socket.create_connection(('127.0.0.1',int(sys.argv[1])),timeout=.3) as connection:
    connection.sendall(b'ping')
    reply=b''
    expected=b'actual worker reply'
    while len(reply)<len(expected):
        block=connection.recv(len(expected)-len(reply))
        assert block, 'actual server closed before full response'
        reply+=block
    assert reply==expected
print(json.dumps({'actual_tcp_seconds':time.monotonic()-started}))
"#,
        ])
        .arg(address.port().to_string())
        .arg(&ready)
        .arg(&go)
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::piped())
        .spawn()
        .unwrap();
    let deadline = tokio::time::Instant::now() + std::time::Duration::from_secs(5);
    while !ready.exists() {
        assert!(tokio::time::Instant::now() < deadline);
        tokio::time::sleep(std::time::Duration::from_millis(5)).await;
    }
    std::fs::write(go, b"start actual request").unwrap();
    let dropped = std::time::Instant::now();
    drop(snapshot);
    let drop_elapsed = dropped.elapsed();
    let observed = tokio::task::spawn_blocking(move || client.wait_with_output())
        .await
        .unwrap()
        .unwrap();
    release.send(()).unwrap();
    writer.join().unwrap();
    let reply = server.await.unwrap();
    eprintln!(
        "actual Snapshot drop elapsed={drop_elapsed:?}; client wait={:?}; stdout={}; stderr={}",
        observed.status.code(),
        String::from_utf8_lossy(&observed.stdout),
        String::from_utf8_lossy(&observed.stderr)
    );
    assert!(observed.status.success());
    reply.unwrap();
    let deadline = tokio::time::Instant::now() + std::time::Duration::from_secs(3);
    loop {
        let current = content.clone();
        let input = key.clone();
        let pins = tokio::task::spawn_blocking(move || current.active_references(&input))
            .await
            .unwrap()
            .unwrap();
        if !pins
            .iter()
            .any(|pin| pin.kind == ReplicaContentReferenceKind::Reader)
        {
            break;
        }
        assert!(
            tokio::time::Instant::now() < deadline,
            "actual original closure receipt remains unavailable"
        );
        tokio::time::sleep(std::time::Duration::from_millis(5)).await;
    }
}

/// Cancelling an observation cannot cancel queued actual closures or release their original root leases.
#[test]
fn actual_queued_snapshot_work_retains_original_root_after_waiter_cancellation() {
    let runtime = tokio::runtime::Builder::new_current_thread()
        .enable_all()
        .max_blocking_threads(1)
        .build()
        .unwrap();
    runtime.block_on(async {
        let directory = tempfile::tempdir().unwrap();
        let (source, key) = source(
            &directory.path().join("source"),
            b"actual scope cancellation bytes",
        );
        let root = directory.path().join("content");
        let profile = "d".repeat(64);
        let content = ReplicaContentStore::create(&root, &profile, 7).unwrap();
        content
            .copy_from_artifact(&source, &key, "original-copy", &"a".repeat(64), 30_000)
            .unwrap();
        let identity = content.identity().clone();
        let first = content
            .retain_reader_scope(
                "first-original",
                std::slice::from_ref(&key),
                &"b".repeat(64),
            )
            .unwrap();
        let second = content
            .retain_reader_scope(
                "second-original",
                std::slice::from_ref(&key),
                &"c".repeat(64),
            )
            .unwrap();
        let ids = [
            format!("first-original:{}", copy::digest(&key).unwrap()),
            format!("second-original:{}", copy::digest(&key).unwrap()),
        ];
        let originals = ids
            .iter()
            .map(|id| content.reader_status(id).unwrap().unwrap())
            .collect::<Vec<_>>();
        let (ready, started) = tokio::sync::oneshot::channel();
        let (release, held) = std::sync::mpsc::channel();
        let blocker = tokio::task::spawn_blocking(move || {
            ready.send(()).unwrap();
            held.recv_timeout(std::time::Duration::from_secs(5))
                .unwrap();
        });
        started.await.unwrap();
        drop(crate::ReplicaSnapshotData::retained(vec![], first));
        drop(crate::ReplicaSnapshotData::retained(vec![], second));
        let observed = content.snapshot_close_status();
        assert_eq!(observed.pending_readers, 2);
        assert!(observed.worker_active);
        assert!(
            tokio::time::timeout(
                std::time::Duration::from_millis(20),
                content.wait_for_snapshot_closures(std::time::Duration::from_secs(2))
            )
            .await
            .is_err()
        );
        assert_eq!(content.snapshot_close_status().pending_readers, 2);
        for id in &ids {
            assert!(
                content
                    .reader_status(id)
                    .unwrap()
                    .unwrap()
                    .receipt
                    .is_none()
            );
        }
        let queue = content.inner.snapshot_closes.clone();
        drop(content);
        assert!(ReplicaContentStore::open(&root, &profile, 7).is_err());
        release.send(()).unwrap();
        blocker.await.unwrap();
        let deadline = tokio::time::Instant::now() + std::time::Duration::from_secs(3);
        while queue.status().worker_active {
            assert!(tokio::time::Instant::now() < deadline);
            tokio::time::sleep(std::time::Duration::from_millis(5)).await;
        }
        let reopened = ReplicaContentStore::open(&root, &profile, 7).unwrap();
        assert_eq!(reopened.identity(), &identity);
        for (id, before) in ids.iter().zip(&originals) {
            let after = reopened.reader_status(id).unwrap().unwrap();
            assert_eq!(after.request_digest, before.request_digest);
            let receipt = after.receipt.unwrap();
            assert!(receipt.retention_only);
            assert!(!receipt.complete);
            assert_eq!(
                (receipt.file_device, receipt.file_inode, receipt.bytes_read),
                (0, 0, 0)
            );
        }
        assert!(
            !reopened
                .active_references(&key)
                .unwrap()
                .iter()
                .any(|pin| pin.kind == ReplicaContentReferenceKind::Reader)
        );
    });
}

/// An ordinary synchronous caller still obtains the original durable retention-only close on Drop.
#[test]
fn actual_snapshot_release_without_tokio_preserves_synchronous_close_contract() {
    let directory = tempfile::tempdir().unwrap();
    let (_source, key) = source(
        &directory.path().join("source"),
        b"actual synchronous scope bytes",
    );
    let content =
        ReplicaContentStore::create(&directory.path().join("content"), &"d".repeat(64), 7).unwrap();
    let reader = content
        .retain_reader_scope(
            "original-sync-snapshot",
            std::slice::from_ref(&key),
            &"a".repeat(64),
        )
        .unwrap();
    let id = format!("original-sync-snapshot:{}", copy::digest(&key).unwrap());
    let original = content.reader_status(&id).unwrap().unwrap();
    drop(crate::ReplicaSnapshotData::retained(vec![], reader));
    let closed = content.reader_status(&id).unwrap().unwrap();
    assert_eq!(closed.request_digest, original.request_digest);
    assert!(closed.receipt.as_ref().unwrap().retention_only);
    assert!(!closed.receipt.unwrap().complete);
    assert_eq!(content.snapshot_close_status().pending_readers, 0);
    assert!(
        !content
            .active_references(&key)
            .unwrap()
            .iter()
            .any(|pin| pin.kind == ReplicaContentReferenceKind::Reader)
    );
}

/// An actual SQL failure retains only its original Unknown pin while independent scope work completes.
#[tokio::test(flavor = "current_thread")]
async fn actual_snapshot_close_failure_preserves_unknown_and_independent_peer_receipt() {
    let directory = tempfile::tempdir().unwrap();
    let (source, key) = source(
        &directory.path().join("source"),
        b"actual independent snapshot scopes",
    );
    let root = directory.path().join("content");
    let content = ReplicaContentStore::create(&root, &"d".repeat(64), 7).unwrap();
    let peer =
        ReplicaContentStore::create(&directory.path().join("independent"), &"d".repeat(64), 8)
            .unwrap();
    for store in [&content, &peer] {
        store
            .copy_from_artifact(&source, &key, "actual-copy", &"a".repeat(64), 30_000)
            .unwrap();
    }
    let first = content
        .retain_reader_scope(
            "failed-original",
            std::slice::from_ref(&key),
            &"b".repeat(64),
        )
        .unwrap();
    let second = peer
        .retain_reader_scope(
            "independent-original",
            std::slice::from_ref(&key),
            &"c".repeat(64),
        )
        .unwrap();
    let first_id = format!("failed-original:{}", copy::digest(&key).unwrap());
    let second_id = format!("independent-original:{}", copy::digest(&key).unwrap());
    let before = content.reader_status(&first_id).unwrap().unwrap();
    let lock = Connection::open(root.join("content.sqlite3")).unwrap();
    lock.execute_batch("BEGIN EXCLUSIVE").unwrap();
    drop(crate::ReplicaSnapshotData::retained(vec![], first));
    drop(crate::ReplicaSnapshotData::retained(vec![], second));
    let observed = content
        .wait_for_snapshot_closures(std::time::Duration::from_secs(3))
        .await
        .unwrap();
    let independent = peer
        .wait_for_snapshot_closures(std::time::Duration::from_secs(3))
        .await
        .unwrap();
    lock.execute_batch("ROLLBACK").unwrap();
    drop(lock);
    assert_eq!(observed.publication_errors_total, 1);
    assert_eq!(observed.abandoned_readers_total, 0);
    assert_eq!(independent.publication_errors_total, 0);
    assert_eq!(content.reader_status(&first_id).unwrap().unwrap(), before);
    assert!(
        peer.reader_status(&second_id)
            .unwrap()
            .unwrap()
            .receipt
            .is_some()
    );
    assert_eq!(
        content
            .active_references(&key)
            .unwrap()
            .iter()
            .filter(|pin| pin.kind == ReplicaContentReferenceKind::Reader)
            .count(),
        1
    );
    assert!(
        !peer
            .active_references(&key)
            .unwrap()
            .iter()
            .any(|pin| pin.kind == ReplicaContentReferenceKind::Reader)
    );
    assert!(content.retire_ended_reader(&first_id).is_err());
}

/// Declares one synthetic retention obligation, without claiming that any payload exists.
fn reference(id: &str, kind: ReplicaContentReferenceKind) -> ReplicaContentReference {
    ReplicaContentReference {
        reference_id: id.into(),
        kind,
        content: ArtifactContentKey {
            source_owner: format!("artifact-store:{}", "a".repeat(64)),
            content_digest: format!("sha256:{}", "b".repeat(64)),
            byte_size: 10,
        },
        authority_digest: "c".repeat(64),
        generation: 1,
    }
}

/// Independent view ownership survives Snapshot retirement and all real managed clone lifetimes.
#[test]
fn independent_view_pin_and_original_lease_survive_snapshot_replacement() {
    let temporary = tempfile::tempdir().unwrap();
    let root = temporary.path().join("member-content");
    let profile = "d".repeat(64);
    let store = ReplicaContentStore::create(&root, &profile, 7).unwrap();
    let identity = store.identity().clone();
    let snapshot = reference("original-snapshot", ReplicaContentReferenceKind::Snapshot);
    let reader = reference("original-view", ReplicaContentReferenceKind::View);
    assert!(store.acquire_reference(&snapshot).unwrap());
    assert!(store.acquire_reference(&reader).unwrap());
    store.release_reference(&snapshot, &"e".repeat(64)).unwrap();
    assert_eq!(
        store.active_references(&reader.content).unwrap(),
        vec![reader.clone()]
    );
    let retained = store.clone();
    drop(store);
    assert!(ReplicaContentStore::open(&root, &profile, 7).is_err());
    drop(retained);
    let reopened = ReplicaContentStore::open(&root, &profile, 7).unwrap();
    assert_eq!(reopened.identity(), &identity);
    assert!(!reopened.acquire_reference(&snapshot).unwrap());
    assert_eq!(
        reopened.active_references(&reader.content).unwrap(),
        vec![reader.clone()]
    );
    reopened
        .release_reference(&reader, &"f".repeat(64))
        .unwrap();
    reopened
        .release_reference(&reader, &"f".repeat(64))
        .unwrap();
    assert!(
        reopened
            .active_references(&reader.content)
            .unwrap()
            .is_empty()
    );
    assert!(
        reopened
            .release_reference(&reader, &"e".repeat(64))
            .is_err()
    );
}

/// Losing a mandatory row/head refuses open without bootstrapping replacement ownership.
#[test]
fn deleted_original_reference_row_never_becomes_an_empty_store() {
    let temporary = tempfile::tempdir().unwrap();
    let root = temporary.path().join("member-content");
    let profile = "d".repeat(64);
    let store = ReplicaContentStore::create(&root, &profile, 7).unwrap();
    let original = reference("original-snapshot", ReplicaContentReferenceKind::Snapshot);
    store.acquire_reference(&original).unwrap();
    drop(store);
    let connection = Connection::open(root.join("content.sqlite3")).unwrap();
    connection
        .execute("DELETE FROM content_references", [])
        .unwrap();
    drop(connection);
    assert!(
        ReplicaContentStore::open(&root, &profile, 7)
            .err()
            .unwrap()
            .contains("mandatory head")
    );
    assert!(ReplicaContentStore::create(&root, &profile, 7).is_err());
    let connection = Connection::open(root.join("content.sqlite3")).unwrap();
    assert_eq!(
        layout::head(&connection, "reference_ids").unwrap(),
        BTreeSet::from([original.reference_id])
    );
}

/// Actual metadata-inode ownership closes the independently named hard-link root writer gap.
#[test]
fn actual_database_inode_lease_refuses_a_hardlink_alias() {
    let temporary = tempfile::tempdir().unwrap();
    let root = temporary.path().join("member-content");
    let profile = "d".repeat(64);
    let store = ReplicaContentStore::create(&root, &profile, 7).unwrap();
    let alias = temporary.path().join("alias");
    std::fs::create_dir(&alias).unwrap();
    File::create(alias.join(".writer.lock")).unwrap();
    std::fs::hard_link(root.join("content.sqlite3"), alias.join("content.sqlite3")).unwrap();
    assert!(
        ReplicaContentStore::open(&alias, &profile, 7)
            .err()
            .unwrap()
            .contains("still active")
    );
    drop(store);
}

/// Both default and retained Artifact writers refuse the actual private infrastructure format.
#[test]
fn ordinary_artifact_constructors_cannot_write_the_content_authority() {
    let temporary = tempfile::tempdir().unwrap();
    let root = temporary.path().join("member-content");
    let profile = "d".repeat(64);
    drop(ReplicaContentStore::create(&root, &profile, 7).unwrap());
    let before = std::fs::read(root.join(".retention-owner.json")).unwrap();
    assert!(artifact_store::FileSystemArtifactStore::new(&root).is_err());
    assert!(artifact_store::FileSystemArtifactStore::new_with_reclamation(&root).is_err());
    assert_eq!(
        std::fs::read(root.join(".retention-owner.json")).unwrap(),
        before
    );
    assert!(ReplicaContentStore::open(&root, &profile, 7).is_ok());
}

/// Creates actual immutable source bytes using the maintained Artifact writer, never Node facts.
pub(super) fn source(
    root: &Path,
    payload: &[u8],
) -> (artifact_store::FileSystemArtifactStore, ArtifactContentKey) {
    let store = artifact_store::FileSystemArtifactStore::new_with_archive_references(root).unwrap();
    let mut upload = store.begin_upload("actual-source-copy").unwrap();
    upload.write_chunk(payload).unwrap();
    let digest = artifact_store::digest_bytes(payload);
    upload.finalize(&digest, payload.len() as u64).unwrap();
    let key = ArtifactContentKey {
        source_owner: store.owner_id().unwrap().into(),
        content_digest: digest,
        byte_size: payload.len() as u64,
    };
    (store, key)
}

/// Actual copied bytes, immutable command receipts and independent receiving pins survive restart.
#[test]
fn actual_source_copy_is_durable_and_original_retry_has_no_new_copy() {
    let temporary = tempfile::tempdir().unwrap();
    let payload = b"actual immutable original Artifact content";
    let (source, key) = source(&temporary.path().join("source"), payload);
    let root = temporary.path().join("target");
    let profile = "d".repeat(64);
    let store = ReplicaContentStore::create(&root, &profile, 7).unwrap();
    let status = store
        .copy_from_artifact(&source, &key, "original-copy", &"e".repeat(64), 30_000)
        .unwrap();
    assert!(status.receipt.is_some());
    store.verify_content(&key).unwrap();
    let pins = store.active_references(&key).unwrap();
    assert_eq!(pins.len(), 1);
    assert_eq!(pins[0].kind, ReplicaContentReferenceKind::Receive);
    assert_eq!(
        store
            .copy_from_artifact(&source, &key, "original-copy", &"e".repeat(64), 30_000)
            .unwrap(),
        status
    );
    drop(store);
    let reopened = ReplicaContentStore::open(&root, &profile, 7).unwrap();
    assert_eq!(reopened.copy_status("original-copy").unwrap(), Some(status));
    reopened.verify_content(&key).unwrap();
    assert_eq!(reopened.active_references(&key).unwrap(), pins);
}

/// Actual retired content is unlinked only after a durable GC intent; same-ID retry never unlinks again.
#[test]
fn actual_replica_gc_requires_retired_reference_and_preserves_object_history() {
    let temporary = tempfile::tempdir().unwrap();
    let payload = b"actual retired Replica GC bytes";
    let (source, key) = source(&temporary.path().join("source"), payload);
    let root = temporary.path().join("target");
    let profile = "a".repeat(64);
    let store = ReplicaContentStore::create_with_reclamation(&root, &profile, 9).unwrap();
    let copied = store
        .copy_from_artifact(&source, &key, "gc-original-copy", &"a".repeat(64), 30_000)
        .unwrap();
    let active = store.active_references(&key).unwrap();
    assert_eq!(active.len(), 1);
    let blocked = store
        .collect_unreferenced_content("gc-blocked", 30_000)
        .unwrap();
    assert_eq!(blocked.candidates, 0);
    store.verify_content(&key).unwrap();
    store
        .release_reference(&active[0], &"b".repeat(64))
        .unwrap();
    let path = copy::object_path(&root, &key);
    assert!(path.exists());
    let deleted = store
        .collect_unreferenced_content("gc-retired", 30_000)
        .unwrap();
    assert_eq!(deleted.candidates, 1);
    assert_eq!(deleted.deleted, 1);
    assert_eq!(deleted.unknown, 0);
    assert!(!path.exists());
    assert!(store.verify_content(&key).is_err());
    assert_eq!(store.copy_status("gc-original-copy").unwrap(), Some(copied));
    assert_eq!(
        store
            .collect_unreferenced_content("gc-retired", 30_000)
            .unwrap(),
        deleted
    );
    assert_eq!(
        store.gc_status("gc-retired").unwrap(),
        Some(deleted.clone())
    );
    drop(store);
    assert!(ReplicaContentStore::open_with_streams(&root, &profile, 9).is_err());
    assert!(ReplicaContentStore::open_with_snapshot_receives(&root, &profile, 9, None).is_err());
    let reopened = ReplicaContentStore::open_with_reclamation(&root, &profile, 9, None).unwrap();
    assert_eq!(reopened.gc_status("gc-retired").unwrap(), Some(deleted));
}

/// Real source corruption leaves the original pin/Unknown intact and never restarts the old copy.
#[test]
fn failed_original_byte_copy_stays_unknown_beside_independent_repair() {
    let temporary = tempfile::tempdir().unwrap();
    let payload = b"actual original bytes";
    let (source, key) = source(&temporary.path().join("source"), payload);
    let path = source
        .root()
        .join("objects-v1/sha256")
        .join(&key.content_digest[7..9])
        .join(&key.content_digest[7..]);
    std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600)).unwrap();
    std::fs::write(&path, b"wrong original bytes!").unwrap();
    let root = temporary.path().join("target");
    let profile = "d".repeat(64);
    let store = ReplicaContentStore::create(&root, &profile, 7).unwrap();
    assert!(
        store
            .copy_from_artifact(&source, &key, "original-copy", &"e".repeat(64), 30_000)
            .is_err()
    );
    let original = store.copy_status("original-copy").unwrap().unwrap();
    assert!(original.receipt.is_none());
    let pin = store.active_references(&key).unwrap().pop().unwrap();
    assert!(store.release_reference(&pin, &"f".repeat(64)).is_err());
    std::fs::write(path, payload).unwrap();
    assert_eq!(
        store
            .copy_from_artifact(&source, &key, "original-copy", &"e".repeat(64), 30_000)
            .unwrap(),
        original
    );
    assert!(store.verify_content(&key).is_err());
    assert!(
        store
            .copy_from_artifact(
                &source,
                &key,
                "independent-storage-repair",
                &"e".repeat(64),
                30_000
            )
            .unwrap()
            .receipt
            .is_some()
    );
    assert_eq!(store.copy_status("original-copy").unwrap(), Some(original));
    store.verify_content(&key).unwrap();
    assert_eq!(store.active_references(&key).unwrap().len(), 2);
}

/// Expired original reading never becomes complete evidence even when its bytes previously matched.
#[test]
fn actual_expired_reader_can_close_without_renewing_its_original_read_budget() {
    use std::io::Read;
    let temporary = tempfile::tempdir().unwrap();
    let (source, key) = source(
        &temporary.path().join("source"),
        b"actual nonrenewing read budget",
    );
    let store =
        ReplicaContentStore::create(&temporary.path().join("target"), &"d".repeat(64), 7).unwrap();
    store
        .copy_from_artifact(&source, &key, "original-copy", &"e".repeat(64), 30_000)
        .unwrap();
    let mut reader = store
        .open_reader(&key, "bounded-reader", &"c".repeat(64), 1000)
        .unwrap();
    let mut observed = Vec::new();
    reader.read_to_end(&mut observed).unwrap();
    std::thread::sleep(std::time::Duration::from_millis(1100));
    assert!(reader.read(&mut [0_u8; 1]).is_err());
    let closed = reader.finish().unwrap();
    assert_eq!(closed.observed_digest, key.content_digest);
    assert_eq!(closed.bytes_read, key.byte_size);
    assert!(!closed.complete);
    assert_eq!(store.active_references(&key).unwrap().len(), 1);
    assert_eq!(
        store
            .reader_status("bounded-reader")
            .unwrap()
            .unwrap()
            .receipt,
        Some(closed)
    );
}

/// Equal payload hashes in different original backends retain distinct actual namespaces and pins.
#[test]
fn actual_equal_bytes_do_not_merge_independent_original_owners() {
    let temporary = tempfile::tempdir().unwrap();
    let payload = b"equal bytes under independent owners";
    let (first, first_key) = source(&temporary.path().join("first-source"), payload);
    let (second, second_key) = source(&temporary.path().join("second-source"), payload);
    assert_ne!(first_key.source_owner, second_key.source_owner);
    assert_eq!(first_key.content_digest, second_key.content_digest);
    let root = temporary.path().join("target");
    let store = ReplicaContentStore::create(&root, &"d".repeat(64), 7).unwrap();
    store
        .copy_from_artifact(&first, &first_key, "first-copy", &"e".repeat(64), 30_000)
        .unwrap();
    store
        .copy_from_artifact(&second, &second_key, "second-copy", &"f".repeat(64), 30_000)
        .unwrap();
    store.verify_content(&first_key).unwrap();
    store.verify_content(&second_key).unwrap();
    assert_ne!(
        store.active_references(&first_key).unwrap(),
        store.active_references(&second_key).unwrap()
    );
    let first_path = root
        .join("content-v1")
        .join(&first_key.source_owner[15..])
        .join(&first_key.content_digest[7..]);
    std::fs::set_permissions(&first_path, std::fs::Permissions::from_mode(0o600)).unwrap();
    std::fs::write(first_path, b"corrupted first owner").unwrap();
    assert!(store.verify_content(&first_key).is_err());
    store.verify_content(&second_key).unwrap();
}

/// A real reader keeps its own FD/leases across Snapshot retirement and reports the actual read bytes.
#[test]
fn actual_managed_reader_owns_its_close_proof_independently_of_snapshot() {
    use std::io::Read;
    let temporary = tempfile::tempdir().unwrap();
    let payload = b"actual original managed reader payload";
    let (source, key) = source(&temporary.path().join("source"), payload);
    let root = temporary.path().join("target");
    let profile = "d".repeat(64);
    let store = ReplicaContentStore::create(&root, &profile, 7).unwrap();
    store
        .copy_from_artifact(&source, &key, "original-copy", &"e".repeat(64), 30_000)
        .unwrap();
    let snapshot = ReplicaContentReference {
        content: key.clone(),
        ..reference("original-snapshot", ReplicaContentReferenceKind::Snapshot)
    };
    store.acquire_reference(&snapshot).unwrap();
    let mut reader = store
        .open_reader(&key, "actual-reader", &"c".repeat(64), 30_000)
        .unwrap();
    let reader_pin = store
        .active_references(&key)
        .unwrap()
        .into_iter()
        .find(|pin| pin.kind == ReplicaContentReferenceKind::Reader)
        .unwrap();
    assert!(
        store
            .release_reference(&reader_pin, &"f".repeat(64))
            .is_err()
    );
    store.release_reference(&snapshot, &"f".repeat(64)).unwrap();
    assert_eq!(store.active_references(&key).unwrap().len(), 2);
    drop(store);
    assert!(ReplicaContentStore::open(&root, &profile, 7).is_err());
    let mut observed = Vec::new();
    reader.read_to_end(&mut observed).unwrap();
    assert_eq!(observed, payload);
    let receipt = reader.finish().unwrap();
    assert!(receipt.complete);
    let store = ReplicaContentStore::open(&root, &profile, 7).unwrap();
    assert_eq!(store.active_references(&key).unwrap().len(), 1);
    assert_eq!(
        store
            .reader_status("actual-reader")
            .unwrap()
            .unwrap()
            .receipt,
        Some(receipt)
    );
    assert!(
        store
            .open_reader(&key, "actual-reader", &"c".repeat(64), 30_000)
            .is_err()
    );
}

/// Interrupted closure keeps Unknown and its pin; a still-live original process cannot claim drain.
#[test]
fn failed_close_sql_and_drop_never_fabricate_original_reader_end() {
    use std::io::Read;
    let temporary = tempfile::tempdir().unwrap();
    let (source, key) = source(
        &temporary.path().join("source"),
        b"actual bounded partial read",
    );
    let root = temporary.path().join("target");
    let store = ReplicaContentStore::create(&root, &"d".repeat(64), 7).unwrap();
    store
        .copy_from_artifact(&source, &key, "original-copy", &"e".repeat(64), 30_000)
        .unwrap();
    let mut reader = store
        .open_reader(&key, "partial-reader", &"c".repeat(64), 30_000)
        .unwrap();
    let mut prefix = [0_u8; 4];
    reader.read_exact(&mut prefix).unwrap();
    assert_eq!(&prefix, b"actu");
    store
        .inner
        .connection
        .lock()
        .unwrap()
        .as_ref()
        .unwrap()
        .execute_batch("PRAGMA query_only=ON")
        .unwrap();
    assert!(reader.finish().is_err());
    store
        .inner
        .connection
        .lock()
        .unwrap()
        .as_ref()
        .unwrap()
        .execute_batch("PRAGMA query_only=OFF")
        .unwrap();
    let original = store.reader_status("partial-reader").unwrap().unwrap();
    assert!(original.receipt.is_none() && original.process_end.is_none());
    assert!(
        store
            .retire_ended_reader("partial-reader")
            .unwrap_err()
            .contains("still present")
    );
    assert_eq!(
        store.reader_status("partial-reader").unwrap(),
        Some(original)
    );
    let partial = store
        .open_reader(&key, "explicit-other-reader", &"c".repeat(64), 30_000)
        .unwrap()
        .finish()
        .unwrap();
    assert_eq!(partial.bytes_read, 0);
    assert!(!partial.complete);
    assert_eq!(store.active_references(&key).unwrap().len(), 2);
}
