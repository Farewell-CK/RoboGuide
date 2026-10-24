//! Actual local SQL/filesystem staging invariants; these tests create no deployment/Node facts.

use super::*;
use std::fs;
use std::os::unix::fs::MetadataExt;

/// Creates one actual original copy and a reserved legacy-style hard link for cleanup contracts.
fn copy_with_link(temporary: &Path) -> (ReplicaContentStore, ArtifactContentKey, PathBuf) {
    let (source, key) = tests::source(&temporary.join("source"), b"actual retained CAS bytes");
    let root =
        ReplicaContentStore::create_with_staging_gc(&temporary.join("target"), &"a".repeat(64), 9)
            .unwrap();
    root.copy_from_artifact(&source, &key, "original-copy", &"b".repeat(64), 30_000)
        .unwrap();
    assert!(
        fs::read_dir(root.root().join("staging-v1"))
            .unwrap()
            .next()
            .is_none()
    );
    let staging = root
        .root()
        .join("staging-v1")
        .join(copy::digest(&"original-copy").unwrap());
    fs::hard_link(copy::object_path(root.root(), &key), &staging).unwrap();
    (root, key, staging)
}

/// Removing a retired private hard link preserves live CAS readers and never grants finalized GC.
#[test]
fn root8_staging_unlink_preserves_independent_current_cas_and_original_copy() {
    let temporary = tempfile::tempdir().unwrap();
    let (root, key, staging) = copy_with_link(temporary.path());
    let target = ReplicaStagingTarget::Copy {
        command_id: "original-copy".into(),
    };
    let original = root.copy_status("original-copy").unwrap().unwrap();
    assert!(
        root.collect_retired_staging("active-refusal", vec![target.clone()], 30_000)
            .is_err()
    );
    assert!(root.staging_gc_status("active-refusal").unwrap().is_none());
    assert!(staging.exists());
    let pin = root.active_references(&key).unwrap().pop().unwrap();
    root.release_reference(&pin, &"c".repeat(64)).unwrap();
    let reader = root
        .retain_reader_scope(
            "independent-reader",
            std::slice::from_ref(&key),
            &"d".repeat(64),
        )
        .unwrap();
    let outcome = root
        .collect_retired_staging("original-cleanup", vec![target.clone()], 30_000)
        .unwrap();
    assert_eq!(
        (outcome.deleted, outcome.missing, outcome.unknown),
        (1, 0, 0)
    );
    assert!(!staging.exists());
    root.verify_content(&key).unwrap();
    assert_eq!(root.copy_status("original-copy").unwrap(), Some(original));
    assert_eq!(
        root.collect_unreferenced_content("reader-still-held", 30_000)
            .unwrap()
            .candidates,
        0
    );
    fs::hard_link(copy::object_path(root.root(), &key), &staging).unwrap();
    assert_eq!(
        root.collect_retired_staging("original-cleanup", vec![target.clone()], 30_000)
            .unwrap(),
        outcome
    );
    assert!(
        staging.exists(),
        "same-ID observation unlinked a recreated name"
    );
    assert!(
        root.collect_retired_staging("original-cleanup", vec![target.clone()], 31_000)
            .is_err()
    );
    root.collect_retired_staging("independent-cleanup", vec![target.clone()], 30_000)
        .unwrap();
    let missing = root
        .collect_retired_staging("independent-absence", vec![target], 30_000)
        .unwrap();
    assert_eq!(
        (missing.deleted, missing.missing, missing.unknown),
        (0, 1, 0)
    );
    drop(reader);
    assert_eq!(
        root.collect_unreferenced_content("final-cas-gc", 30_000)
            .unwrap()
            .deleted,
        1
    );
    assert_eq!(
        root.staging_gc_status("original-cleanup").unwrap(),
        Some(outcome)
    );
}

/// Explicit drained Root7 upgrade retains original identities/inodes and rejects every old generation.
#[test]
fn root8_upgrade_fences_old_writers_before_any_staging_command() {
    let temporary = tempfile::tempdir().unwrap();
    let path = temporary.path().join("target");
    let profile = "a".repeat(64);
    let old = ReplicaContentStore::create_with_reclamation(&path, &profile, 9).unwrap();
    let identity = old.identity().clone();
    let inode = fs::metadata(path.join("content.sqlite3")).unwrap().ino();
    assert!(ReplicaContentStore::upgrade_staging_gc(&path, &profile, 9, None).is_err());
    drop(old);
    let upgraded = ReplicaContentStore::upgrade_staging_gc(&path, &profile, 9, None).unwrap();
    assert_eq!(upgraded.identity(), &identity);
    assert_eq!(
        fs::metadata(path.join("content.sqlite3")).unwrap().ino(),
        inode
    );
    assert!(upgraded.staging_gc_status("not-issued").unwrap().is_none());
    drop(upgraded);
    assert!(ReplicaContentStore::open_with_reclamation(&path, &profile, 9, None).is_err());
    assert!(ReplicaContentStore::open_with_snapshot_receives(&path, &profile, 9, None).is_err());
    assert!(ReplicaContentStore::open_with_streams(&path, &profile, 9).is_err());
    ReplicaContentStore::open_with_staging_gc(&path, &profile, 9, None).unwrap();
}

/// All selected original sources must be valid before any command or private-file effect is admitted.
#[test]
fn missing_source_and_missing_item_inventory_never_bootstrap_cleanup() {
    let temporary = tempfile::tempdir().unwrap();
    let (root, key, staging) = copy_with_link(temporary.path());
    let pin = root.active_references(&key).unwrap().pop().unwrap();
    root.release_reference(&pin, &"c".repeat(64)).unwrap();
    assert!(
        root.collect_retired_staging(
            "mixed-refusal",
            vec![
                ReplicaStagingTarget::Copy {
                    command_id: "original-copy".into()
                },
                ReplicaStagingTarget::Copy {
                    command_id: "missing-source".into()
                },
            ],
            30_000
        )
        .is_err()
    );
    assert!(staging.exists());
    assert!(root.staging_gc_status("mixed-refusal").unwrap().is_none());
    let path = root.root().to_owned();
    drop(root);
    let sql = Connection::open(path.join("content.sqlite3")).unwrap();
    sql.execute("DELETE FROM metadata WHERE key='staging_gc_item_ids'", [])
        .unwrap();
    drop(sql);
    assert!(ReplicaContentStore::open_with_staging_gc(&path, &"a".repeat(64), 9, None).is_err());
    assert!(staging.exists());
}
