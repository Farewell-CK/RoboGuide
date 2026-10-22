//! Actual filesystem layout, original-inode migration and conservative restart tests.

use super::*;
use std::fs;
use tempfile::tempdir;

/// Real same-owner GC upgrade keeps bytes/inode, while old v3 and readers cannot ignore its new journal.
#[test]
fn original_source_gc_format_fences_old_writers_without_replacing_bytes() {
    let directory = tempdir().unwrap();
    let root = directory.path();
    let original = FileSystemArtifactStore::new_with_archive_references(root).unwrap();
    let owner = original.owner_id().unwrap().to_string();
    let bytes = b"original source content";
    let digest = digest_bytes(bytes);
    let mut upload = original.begin_upload("original-source").unwrap();
    upload.write_chunk(bytes).unwrap();
    let saved = upload.finalize(&digest, bytes.len() as u64).unwrap();
    drop(upload);
    drop(original);
    let before = fs::metadata(saved.path()).unwrap();
    let current = FileSystemArtifactStore::new_with_reclamation(root).unwrap();
    assert_eq!(current.owner_id(), Some(owner.as_str()));
    assert_eq!(current.storage_schema(), ARTIFACT_GC_LAYOUT_SCHEMA);
    let after = current
        .verify_artifact(&digest, bytes.len() as u64)
        .unwrap();
    assert_eq!(fs::read(after.path()).unwrap(), bytes);
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        assert_eq!(before.ino(), fs::metadata(after.path()).unwrap().ino());
    }
    drop(current);
    assert!(FileSystemArtifactStore::new_with_archive_references(root).is_err());
    let readonly = ArtifactReadOnlyStore::open(root).unwrap();
    assert_eq!(readonly.storage_schema(), ARTIFACT_GC_LAYOUT_SCHEMA);
    assert_eq!(
        readonly
            .open_artifact(&digest)
            .unwrap()
            .metadata()
            .unwrap()
            .len(),
        bytes.len() as u64
    );
}

/// A lost accepted GC journal refuses restart and never reinitialises an empty replacement authority.
#[test]
fn missing_original_gc_sql_never_becomes_empty_authority() {
    let directory = tempdir().unwrap();
    let root = directory.path();
    let current = FileSystemArtifactStore::new_with_reclamation(root).unwrap();
    let owner = fs::read(root.join(ARTIFACT_OWNER_FILE)).unwrap();
    drop(current);
    fs::remove_file(root.join("reclamation-v1/journal.sqlite3")).unwrap();
    assert!(FileSystemArtifactStore::new_with_reclamation(root).is_err());
    assert!(ArtifactReadOnlyStore::open(root).is_err());
    assert!(!root.join("reclamation-v1/journal.sqlite3").exists());
    assert!(!root.join(".reclamation-upgrade.json").exists());
    assert_eq!(fs::read(root.join(ARTIFACT_OWNER_FILE)).unwrap(), owner);
}

/// Publishes actual bytes once and closes every legacy writer before explicit migration.
#[test]
fn retained_migration_preserves_blob_inode_bytes_and_original_identity() {
    let directory = tempdir().unwrap();
    let root = directory.path();
    let legacy = FileSystemArtifactStore::new(root).unwrap();
    assert!(legacy.owner_id().is_none());
    let digest = digest_bytes(b"actual retained Artifact content");
    let mut upload = legacy.begin_upload("original").unwrap();
    upload
        .write_chunk(b"actual retained Artifact content")
        .unwrap();
    let before = upload.finalize(&digest, 32).unwrap();
    #[cfg(unix)]
    let inode = {
        use std::os::unix::fs::MetadataExt;
        fs::metadata(before.path()).unwrap().ino()
    };
    drop(upload);
    drop(legacy);
    let retained = FileSystemArtifactStore::new_with_retention(root).unwrap();
    let owner = retained.owner_id().unwrap().to_owned();
    let after = retained.verify_artifact(&digest, 32).unwrap();
    assert_eq!(
        fs::read(after.path()).unwrap(),
        b"actual retained Artifact content"
    );
    assert!(root.join("blobs").is_file());
    assert!(root.join(RETAINED_BLOB_DIRECTORY).is_dir());
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        assert_eq!(fs::metadata(after.path()).unwrap().ino(), inode);
    }
    assert!(!before.path().exists());
    drop(retained);
    assert!(FileSystemArtifactStore::new(root).is_err());
    let reopened = FileSystemArtifactStore::new_with_retention(root).unwrap();
    assert_eq!(reopened.owner_id(), Some(owner.as_str()));
    assert_eq!(
        fs::read(reopened.verify_artifact(&digest, 32).unwrap().path()).unwrap(),
        b"actual retained Artifact content"
    );
}

/// A complete final owner and old-path fence remain mandatory even when all content survives.
#[test]
fn missing_original_identity_never_bootstraps_a_replacement() {
    let directory = tempdir().unwrap();
    let root = directory.path();
    let store = FileSystemArtifactStore::new_with_retention(root).unwrap();
    drop(store);
    let original = fs::read(root.join("blobs")).unwrap();
    fs::remove_file(root.join(ARTIFACT_OWNER_FILE)).unwrap();
    assert!(FileSystemArtifactStore::new_with_retention(root).is_err());
    assert!(!root.join(ARTIFACT_OWNER_FILE).exists());
    assert!(!root.join(".retention-intent.json").exists());
    assert_eq!(fs::read(root.join("blobs")).unwrap(), original);
}

/// Each original migration window resumes the same nonce and actual moved directory.
#[test]
fn interrupted_migration_resumes_original_intent_without_replacing_identity() {
    for window in ["intent", "renamed", "fenced"] {
        let directory = tempdir().unwrap();
        let root = directory.path();
        let store = FileSystemArtifactStore::new_with_retention(root).unwrap();
        let owner = store.owner_id().unwrap().to_owned();
        drop(store);
        fs::rename(
            root.join(ARTIFACT_OWNER_FILE),
            root.join(".retention-intent.json"),
        )
        .unwrap();
        if window != "fenced" {
            fs::remove_file(root.join("blobs")).unwrap();
        }
        if window == "intent" {
            fs::rename(root.join(RETAINED_BLOB_DIRECTORY), root.join("blobs")).unwrap();
        }
        assert!(FileSystemArtifactStore::new(root).is_err());
        let recovered = FileSystemArtifactStore::new_with_retention(root).unwrap();
        assert_eq!(recovered.owner_id(), Some(owner.as_str()));
        assert!(root.join("blobs").is_file());
        assert!(!root.join(".retention-intent.json").exists());
    }
}

/// An unrelated directory cannot be substituted for the original interrupted rename.
#[test]
fn changed_original_migration_inode_is_refused_without_consuming_intent() {
    let directory = tempdir().unwrap();
    let root = directory.path();
    let store = FileSystemArtifactStore::new_with_retention(root).unwrap();
    drop(store);
    fs::rename(
        root.join(ARTIFACT_OWNER_FILE),
        root.join(".retention-intent.json"),
    )
    .unwrap();
    fs::rename(
        root.join(RETAINED_BLOB_DIRECTORY),
        root.join("original-objects"),
    )
    .unwrap();
    fs::create_dir(root.join(RETAINED_BLOB_DIRECTORY)).unwrap();
    assert!(FileSystemArtifactStore::new_with_retention(root).is_err());
    assert!(root.join(".retention-intent.json").is_file());
    assert!(!root.join(ARTIFACT_OWNER_FILE).exists());
    assert!(root.join("original-objects").is_dir());
}

/// Conflicting fence metadata cannot be accepted as an original backend identity.
#[test]
fn foreign_fence_owner_is_refused_even_for_an_empty_content_tree() {
    let first = tempdir().unwrap();
    let second = tempdir().unwrap();
    let left = FileSystemArtifactStore::new_with_retention(first.path()).unwrap();
    let right = FileSystemArtifactStore::new_with_retention(second.path()).unwrap();
    assert_ne!(left.owner_id(), right.owner_id());
    drop(left);
    drop(right);
    fs::copy(second.path().join("blobs"), first.path().join("blobs")).unwrap();
    assert!(FileSystemArtifactStore::new_with_retention(first.path()).is_err());
}

/// A symlink cannot lend the identity of a separately owned CAS root.
#[test]
#[cfg(unix)]
fn owner_symlink_is_refused_without_following_another_root() {
    use std::os::unix::fs::symlink;
    let first = tempdir().unwrap();
    let second = tempdir().unwrap();
    let left = FileSystemArtifactStore::new_with_retention(first.path()).unwrap();
    let right = FileSystemArtifactStore::new_with_retention(second.path()).unwrap();
    drop(left);
    drop(right);
    let original = fs::read(second.path().join(ARTIFACT_OWNER_FILE)).unwrap();
    fs::remove_file(first.path().join(ARTIFACT_OWNER_FILE)).unwrap();
    symlink(
        second.path().join(ARTIFACT_OWNER_FILE),
        first.path().join(ARTIFACT_OWNER_FILE),
    )
    .unwrap();
    assert!(FileSystemArtifactStore::new_with_retention(first.path()).is_err());
    assert_eq!(
        fs::read(second.path().join(ARTIFACT_OWNER_FILE)).unwrap(),
        original
    );
}
