//! Actual SQL/filesystem counterexamples for permanent GC fences and original Unknown outcomes.

use super::*;
use std::fs;
use std::os::unix::fs::{MetadataExt, PermissionsExt, symlink};

/// Creates real bytes with one completed, explicitly retired copy and no invented process evidence.
fn retired(
    temporary: &Path,
    payload: &[u8],
) -> (
    ReplicaContentStore,
    artifact_store::FileSystemArtifactStore,
    ArtifactContentKey,
) {
    let (source, key) = tests::source(&temporary.join("source"), payload);
    let store =
        ReplicaContentStore::create_with_reclamation(&temporary.join("target"), &"a".repeat(64), 9)
            .unwrap();
    store
        .copy_from_artifact(&source, &key, "original-copy", &"b".repeat(64), 30_000)
        .unwrap();
    let pin = store.active_references(&key).unwrap().pop().unwrap();
    store.release_reference(&pin, &"c".repeat(64)).unwrap();
    (store, source, key)
}

/// A real retained reader independently prevents deletion until its actual scope is closed.
#[test]
fn live_reader_scope_blocks_gc_independently_of_retired_copy() {
    let temporary = tempfile::tempdir().unwrap();
    let (store, _source, key) = retired(temporary.path(), b"independent reader bytes");
    let reader = store
        .retain_reader_scope(
            "original-reader",
            std::slice::from_ref(&key),
            &"d".repeat(64),
        )
        .unwrap();
    let blocked = store
        .collect_unreferenced_content("while-reader-live", 30_000)
        .unwrap();
    assert_eq!(blocked.candidates, 0);
    store.verify_content(&key).unwrap();
    drop(reader);
    assert_eq!(
        store
            .collect_unreferenced_content("after-real-close", 30_000)
            .unwrap()
            .deleted,
        1
    );
}

/// Actual corrupt bytes leave original Unknown beside a later independent GC, never a repaired old result.
#[test]
fn corrupt_file_gc_keeps_first_unknown_and_same_id_has_no_effect() {
    let temporary = tempfile::tempdir().unwrap();
    let payload = b"original immutable GC input";
    let (store, _source, key) = retired(temporary.path(), payload);
    let path = copy::object_path(store.root(), &key);
    fs::set_permissions(&path, fs::Permissions::from_mode(0o600)).unwrap();
    fs::write(&path, b"actual wrong bytes").unwrap();
    assert!(
        store
            .collect_unreferenced_content("failed-gc", 30_000)
            .is_err()
    );
    let original = store.gc_status("failed-gc").unwrap().unwrap();
    assert_eq!(
        (original.candidates, original.deleted, original.unknown),
        (1, 0, 1)
    );
    fs::write(&path, payload).unwrap();
    assert_eq!(
        store
            .collect_unreferenced_content("failed-gc", 30_000)
            .unwrap(),
        original
    );
    assert!(path.exists());
    assert!(store.verify_content(&key).is_err());
    let root = store.root().to_owned();
    drop(store);
    let reopened =
        ReplicaContentStore::open_with_reclamation(&root, &"a".repeat(64), 9, None).unwrap();
    assert_eq!(
        reopened.gc_status("failed-gc").unwrap(),
        Some(original.clone())
    );
    assert_eq!(
        reopened
            .collect_unreferenced_content("independent-gc", 30_000)
            .unwrap()
            .deleted,
        1
    );
    assert_eq!(reopened.gc_status("failed-gc").unwrap(), Some(original));
}

/// Recreated real bytes cannot lend new copy, read, retention or current availability after a tombstone.
#[test]
fn recreated_tombstoned_bytes_refuse_new_acquisition() {
    let temporary = tempfile::tempdir().unwrap();
    let payload = b"recreated bytes stay unavailable";
    let (store, source, key) = retired(temporary.path(), payload);
    let original = store.copy_status("original-copy").unwrap().unwrap();
    store
        .collect_unreferenced_content("first-gc", 30_000)
        .unwrap();
    let path = copy::object_path(store.root(), &key);
    fs::write(&path, payload).unwrap();
    assert!(store.verify_content(&key).is_err());
    assert!(
        store
            .copy_from_artifact(&source, &key, "new-copy", &"b".repeat(64), 30_000)
            .is_err()
    );
    assert!(
        store
            .open_reader(&key, "new-reader", &"d".repeat(64), 30_000)
            .is_err()
    );
    assert!(
        store
            .retain_reader_scope("new-scope", std::slice::from_ref(&key), &"d".repeat(64))
            .is_err()
    );
    assert_eq!(
        store
            .copy_from_artifact(&source, &key, "original-copy", &"b".repeat(64), 30_000)
            .unwrap(),
        original
    );
    assert_eq!(
        store
            .collect_unreferenced_content("first-gc", 30_000)
            .unwrap()
            .deleted,
        1
    );
    assert!(path.exists());
    let independent = store
        .collect_unreferenced_content("remove-recreated", 30_000)
        .unwrap();
    assert_eq!((independent.deleted, independent.missing), (1, 0));
}

/// Only actual NotFound becomes missing; a symlink is rejected before any GC intent or unlink.
#[test]
fn gc_distinguishes_actual_absence_from_symlink() {
    let temporary = tempfile::tempdir().unwrap();
    let payload = b"symlink target must remain intact";
    let (store, _source, key) = retired(temporary.path(), payload);
    let path = copy::object_path(store.root(), &key);
    let foreign = temporary.path().join("foreign");
    fs::write(&foreign, payload).unwrap();
    fs::remove_file(&path).unwrap();
    symlink(&foreign, &path).unwrap();
    assert!(
        store
            .collect_unreferenced_content("symlink-gc", 30_000)
            .is_err()
    );
    assert_eq!(store.gc_status("symlink-gc").unwrap(), None);
    assert_eq!(fs::read(&foreign).unwrap(), payload);
    fs::remove_file(&path).unwrap();
    let absent = store
        .collect_unreferenced_content("actual-absence", 30_000)
        .unwrap();
    assert_eq!((absent.deleted, absent.missing, absent.unknown), (0, 1, 0));
}

/// Explicit drained upgrade preserves the original SQL inode/history and fences every old entry point.
#[test]
fn drained_root6_gc_upgrade_preserves_original_identity_and_receipt() {
    let temporary = tempfile::tempdir().unwrap();
    let root = temporary.path().join("target");
    let profile = "a".repeat(64);
    let store = ReplicaContentStore::create_with_snapshot_receives(&root, &profile, 9).unwrap();
    let identity = store.identity().clone();
    let inode = fs::metadata(root.join("content.sqlite3")).unwrap().ino();
    assert!(
        store
            .collect_unreferenced_content("old-root-gc", 30_000)
            .is_err()
    );
    assert!(ReplicaContentStore::upgrade_reclamation(&root, &profile, 9, None).is_err());
    drop(store);
    let upgraded = ReplicaContentStore::upgrade_reclamation(&root, &profile, 9, None).unwrap();
    assert_eq!(upgraded.identity(), &identity);
    assert_eq!(
        fs::metadata(root.join("content.sqlite3")).unwrap().ino(),
        inode
    );
    drop(upgraded);
    assert!(ReplicaContentStore::open_with_snapshot_receives(&root, &profile, 9, None).is_err());
    assert!(ReplicaContentStore::open_with_streams(&root, &profile, 9).is_err());
    assert!(ReplicaContentStore::open_with_producers(&root, &profile, 9).is_err());
    assert!(ReplicaContentStore::open(&root, &profile, 9).is_err());
    ReplicaContentStore::open_with_reclamation(&root, &profile, 9, None).unwrap();
}

/// Lost mandatory tombstones never bootstrap replacement GC authority or alter the retained bytes.
#[test]
fn missing_gc_inventory_refuses_restart_without_repair() {
    let temporary = tempfile::tempdir().unwrap();
    let (store, _source, key) = retired(temporary.path(), b"retained despite missing GC head");
    let root = store.root().to_owned();
    let path = copy::object_path(&root, &key);
    drop(store);
    let sql = Connection::open(root.join("content.sqlite3")).unwrap();
    sql.execute("DELETE FROM metadata WHERE key='tombstone_keys'", [])
        .unwrap();
    drop(sql);
    assert!(ReplicaContentStore::open_with_reclamation(&root, &"a".repeat(64), 9, None).is_err());
    assert!(path.exists());
    let sql = Connection::open(root.join("content.sqlite3")).unwrap();
    assert_eq!(
        sql.query_row::<usize, _, _>(
            "SELECT COUNT(*) FROM metadata WHERE key='tombstone_keys'",
            [],
            |row| row.get(0)
        )
        .unwrap(),
        0
    );
}

/// A later corrupt item preserves the earlier actual unlink receipt and its own original Unknown.
#[test]
fn multi_item_gc_preserves_partial_progress_and_independent_followup() {
    let temporary = tempfile::tempdir().unwrap();
    let root = temporary.path().join("target");
    let store = ReplicaContentStore::create_with_reclamation(&root, &"a".repeat(64), 9).unwrap();
    let mut inputs = Vec::new();
    for (index, payload) in [
        b"first actual qualified GC item".as_slice(),
        b"second actual qualified GC item".as_slice(),
    ]
    .into_iter()
    .enumerate()
    {
        let (source, key) =
            tests::source(&temporary.path().join(format!("source-{index}")), payload);
        store
            .copy_from_artifact(
                &source,
                &key,
                &format!("original-copy-{index}"),
                &"b".repeat(64),
                30_000,
            )
            .unwrap();
        let pin = store.active_references(&key).unwrap().pop().unwrap();
        store.release_reference(&pin, &"c".repeat(64)).unwrap();
        inputs.push((key, payload.to_vec()));
    }
    inputs.sort_by(|left, right| left.0.cmp(&right.0));
    let first = copy::object_path(&root, &inputs[0].0);
    let second = copy::object_path(&root, &inputs[1].0);
    fs::set_permissions(&second, fs::Permissions::from_mode(0o600)).unwrap();
    fs::write(&second, b"actual corruption after the completed copy").unwrap();
    assert!(
        store
            .collect_unreferenced_content("partial-original", 30_000)
            .is_err()
    );
    let original = store.gc_status("partial-original").unwrap().unwrap();
    assert_eq!(
        (original.candidates, original.deleted, original.unknown),
        (2, 1, 1)
    );
    assert!(!first.exists() && second.exists());
    fs::write(&second, &inputs[1].1).unwrap();
    assert_eq!(
        store
            .collect_unreferenced_content("partial-original", 30_000)
            .unwrap(),
        original
    );
    assert!(second.exists());
    let independent = store
        .collect_unreferenced_content("independent-followup", 30_000)
        .unwrap();
    assert_eq!(
        (
            independent.deleted,
            independent.missing,
            independent.unknown
        ),
        (1, 1, 0)
    );
    assert_eq!(store.gc_status("partial-original").unwrap(), Some(original));
}
