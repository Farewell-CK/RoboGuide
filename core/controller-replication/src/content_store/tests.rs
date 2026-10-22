//! Actual storage/lease counterexamples; labelled pin DTOs confer no content or deployment facts.

use super::*;
use std::collections::BTreeSet;
use std::os::unix::fs::PermissionsExt;

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
fn source(
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
