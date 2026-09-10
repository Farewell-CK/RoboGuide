//! Filesystem artifact store tests.

use super::*;
use crate::digest::DIGEST_PREFIX;
use crate::path_validation::create_directory;
use crate::store::{BLOB_DIRECTORY, STAGING_DIRECTORY};
use std::fs;
use std::io::Read;
use tempfile::tempdir;

/// Returns a deterministic digest for the bytes used by storage tests.
fn sample_digest() -> String {
    digest_bytes(b"hello spatial memory")
}

/// Builds a temporary store for one isolated test.
fn store() -> (tempfile::TempDir, FileSystemArtifactStore) {
    let directory = tempdir().expect("temporary directory must exist");
    let store = FileSystemArtifactStore::new(directory.path()).expect("store must initialize");
    (directory, store)
}

/// Uploads chunks, validates metadata, and reads the immutable blob back.
#[test]
fn finalizes_chunked_upload_and_streams_read() {
    let (_directory, store) = store();
    let digest = sample_digest();
    let mut upload = store.begin_upload("upload-1").expect("upload must start");
    upload
        .write_chunk(b"hello ")
        .expect("first chunk must write");
    upload
        .write_chunk(b"spatial memory")
        .expect("second chunk must write");
    let artifact = upload
        .finalize(&digest, 20)
        .expect("matching upload must finalize");
    assert_eq!(artifact.digest(), digest);
    assert_eq!(artifact.size(), 20);
    assert!(store.contains(&digest).expect("contains must succeed"));
    let mut file = store.open_artifact(&digest).expect("blob must open");
    let mut bytes = Vec::new();
    file.read_to_end(&mut bytes).expect("blob must read");
    assert_eq!(bytes, b"hello spatial memory");
    assert!(
        fs::metadata(artifact.path())
            .expect("blob metadata must read")
            .permissions()
            .readonly()
    );
}

/// Rejects mismatched metadata while retaining the staging file for explicit abort.
#[test]
fn mismatch_retains_upload_until_abort() {
    let (_directory, store) = store();
    let mut upload = store
        .begin_upload("upload-mismatch")
        .expect("upload must start");
    upload.write_chunk(b"payload").expect("chunk must write");
    let result = upload.finalize(&sample_digest(), 7);
    assert!(matches!(
        result,
        Err(ArtifactStoreError::DigestMismatch { .. })
    ));
    let staging = store
        .root()
        .join(STAGING_DIRECTORY)
        .join("upload-mismatch.partial");
    assert!(staging.exists());
    upload.abort().expect("abort must remove staging");
    assert!(!staging.exists());
}

/// Reuses an existing valid blob and removes a retried staging file.
#[test]
fn identical_retry_is_deduplicated() {
    let (_directory, store) = store();
    let digest = sample_digest();
    let mut first = store.begin_upload("upload-first").expect("first upload");
    first
        .write_chunk(b"hello spatial memory")
        .expect("first bytes");
    let original = first.finalize(&digest, 20).expect("first finalize");
    let mut retry = store.begin_upload("upload-retry").expect("retry upload");
    retry
        .write_chunk(b"hello spatial memory")
        .expect("retry bytes");
    let duplicate = retry.finalize(&digest, 20).expect("retry finalize");
    assert_eq!(duplicate, original);
    assert!(
        !store
            .root()
            .join(STAGING_DIRECTORY)
            .join("upload-retry.partial")
            .exists()
    );
}

/// Refuses to treat a tampered digest path as a valid deduplicated blob.
#[test]
fn conflicting_existing_blob_is_rejected() {
    let (_directory, store) = store();
    let digest = sample_digest();
    let mut first = store.begin_upload("upload-first").expect("first upload");
    first
        .write_chunk(b"hello spatial memory")
        .expect("first bytes");
    let artifact = first.finalize(&digest, 20).expect("first finalize");
    fs::remove_file(artifact.path()).expect("test removes sealed blob");
    fs::write(artifact.path(), b"tampered").expect("test must replace blob");
    let mut retry = store.begin_upload("upload-conflict").expect("retry upload");
    retry
        .write_chunk(b"hello spatial memory")
        .expect("retry bytes");
    let result = retry.finalize(&digest, 20);
    assert!(matches!(
        result,
        Err(ArtifactStoreError::ArtifactConflict { .. })
    ));
    retry.abort().expect("conflicting staging must abort");
}

/// Publication verification rejects same-size corruption at a finalized digest path.
#[test]
fn publication_verification_rehashes_finalized_blob() {
    let (_directory, store) = store();
    let digest = sample_digest();
    let mut upload = store.begin_upload("upload-final").expect("upload starts");
    upload
        .write_chunk(b"hello spatial memory")
        .expect("bytes write");
    let artifact = upload.finalize(&digest, 20).expect("upload finalizes");
    fs::remove_file(artifact.path()).expect("test removes sealed blob");
    fs::write(artifact.path(), b"xxxxxxxxxxxxxxxxxxxx")
        .expect("blob is replaced with corruption in test");

    assert!(matches!(
        store.verify_artifact(&digest, 20),
        Err(ArtifactStoreError::ArtifactConflict { .. })
    ));
}

/// Rejects traversal-shaped upload identifiers and malformed digests.
#[test]
fn validates_paths_and_digests() {
    let (_directory, store) = store();
    assert!(matches!(
        store.begin_upload("../escape"),
        Err(ArtifactStoreError::InvalidUploadId { .. })
    ));
    assert!(matches!(
        normalize_digest("sha256:bad"),
        Err(ArtifactStoreError::InvalidDigest { .. })
    ));
    let uppercase = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA";
    assert!(normalize_digest(uppercase).is_err());
    assert_eq!(
        normalize_digest(&format!("sha256:{}", "a".repeat(64)))
            .expect("canonical digest is accepted"),
        format!("sha256:{}", "a".repeat(64))
    );
}

/// Drops an unfinished upload and cleans its temporary file.
#[test]
fn dropping_upload_cleans_staging() {
    let (_directory, store) = store();
    {
        let mut upload = store
            .begin_upload("upload-drop")
            .expect("upload must start");
        upload.write_chunk(b"orphan").expect("chunk must write");
    }
    assert!(
        !store
            .root()
            .join(STAGING_DIRECTORY)
            .join("upload-drop.partial")
            .exists()
    );
}

/// Reopening a store removes unrecoverable partial files but preserves unrelated entries.
#[test]
fn initialization_cleans_abandoned_partial_uploads() {
    let directory = tempdir().expect("temporary directory must exist");
    let staging = directory.path().join(STAGING_DIRECTORY);
    fs::create_dir_all(&staging).expect("staging directory must exist");
    let abandoned = staging.join("crashed-upload.partial");
    let unrelated = staging.join("operator-note");
    fs::write(&abandoned, b"incomplete bytes").expect("partial file must be written");
    fs::write(&unrelated, b"preserve").expect("unrelated file must be written");

    let _store = FileSystemArtifactStore::new(directory.path()).expect("store must clean staging");

    assert!(!abandoned.exists());
    assert_eq!(
        fs::read(unrelated).expect("unrelated staging entry remains readable"),
        b"preserve"
    );
}

/// A competing initializer fails before cleanup and cannot delete the active writer's upload.
#[test]
fn writer_lock_fences_competing_initializer_before_staging_cleanup() {
    let directory = tempdir().expect("temporary directory must exist");
    let store = FileSystemArtifactStore::new(directory.path())
        .expect("first writer must acquire the artifact root");
    let mut upload = store
        .begin_upload("active-upload")
        .expect("first writer must start an upload");
    upload
        .write_chunk(b"in-flight bytes")
        .expect("first writer must stage bytes");
    let staging_path = store
        .root()
        .join(STAGING_DIRECTORY)
        .join("active-upload.partial");

    let error =
        FileSystemArtifactStore::new(directory.path()).expect_err("second writer must be fenced");

    assert!(
        error
            .to_string()
            .contains("is already owned by another writer"),
        "unexpected writer-lock error: {error}"
    );
    assert_eq!(
        fs::read(&staging_path).expect("active staging bytes must remain readable"),
        b"in-flight bytes"
    );

    drop(upload);
    drop(store);
    FileSystemArtifactStore::new(directory.path())
        .expect("artifact root must be available after the first writer exits");
}

/// Store initialization rejects a configured root symlink without creating entries outside it.
#[cfg(unix)]
#[test]
fn rejects_symlinked_store_root() {
    use std::os::unix::fs::symlink;

    let directory = tempdir().expect("temporary directory must exist");
    let outside = directory.path().join("outside");
    fs::create_dir(&outside).expect("outside directory must exist");
    let linked_root = directory.path().join("artifact-root");
    symlink(&outside, &linked_root).expect("root symlink must be created");

    assert!(FileSystemArtifactStore::new(&linked_root).is_err());
    assert!(!outside.join(STAGING_DIRECTORY).exists());
    assert!(!outside.join(BLOB_DIRECTORY).exists());
}

/// Upload creation rejects a staging directory replaced by a symlink after initialization.
#[cfg(unix)]
#[test]
fn rejects_symlinked_staging_directory() {
    use std::os::unix::fs::symlink;

    let (directory, store) = store();
    let outside = directory.path().join("outside");
    fs::create_dir(&outside).expect("outside directory must exist");
    let staging = store.root().join(STAGING_DIRECTORY);
    fs::remove_dir(&staging).expect("empty staging directory must be removable");
    symlink(&outside, &staging).expect("staging symlink must be created");

    assert!(store.begin_upload("escape").is_err());
    assert!(!outside.join("escape.partial").exists());
}

/// Finalization rejects a symlinked digest directory and leaves the external tree untouched.
#[cfg(unix)]
#[test]
fn rejects_symlinked_digest_directory() {
    use std::os::unix::fs::symlink;

    let (directory, store) = store();
    let outside = directory.path().join("outside");
    fs::create_dir(&outside).expect("outside directory must exist");
    let algorithm_directory = store.root().join(BLOB_DIRECTORY).join("sha256");
    symlink(&outside, &algorithm_directory).expect("digest directory symlink must be created");
    let digest = sample_digest();
    let digest_hex = digest
        .strip_prefix(DIGEST_PREFIX)
        .expect("sample digest has canonical prefix");
    let mut upload = store
        .begin_upload("symlinked-digest")
        .expect("upload must start");
    upload
        .write_chunk(b"hello spatial memory")
        .expect("sample bytes must write");

    assert!(upload.finalize(&digest, 20).is_err());
    assert!(!outside.join(&digest_hex[..2]).exists());
    upload.abort().expect("rejected upload must abort");
}

/// Read and verification operations reject a blob leaf symlink without reading its target.
#[cfg(unix)]
#[test]
fn rejects_symlinked_blob_leaf() {
    use std::os::unix::fs::symlink;

    let (directory, store) = store();
    let digest = sample_digest();
    let blob_path = store.blob_path(&digest);
    create_directory(
        blob_path.parent().expect("blob path has parent"),
        "create test digest directory",
    )
    .expect("digest directory must be created");
    let outside = directory.path().join("outside.blob");
    fs::write(&outside, b"hello spatial memory").expect("external blob must be written");
    symlink(&outside, &blob_path).expect("blob symlink must be created");

    assert!(store.open_artifact(&digest).is_err());
    assert!(store.contains(&digest).is_err());
    assert!(matches!(
        store.verify_artifact(&digest, 20),
        Err(ArtifactStoreError::ArtifactConflict { .. })
    ));
    assert_eq!(
        fs::read(&outside).expect("external blob remains readable"),
        b"hello spatial memory"
    );
}

/// Initialization and finalization reject regular files used as directory components.
#[test]
fn rejects_non_directory_components() {
    let directory = tempdir().expect("temporary directory must exist");
    let file_root = directory.path().join("file-root");
    fs::write(&file_root, b"not a directory").expect("file root must be written");
    assert!(FileSystemArtifactStore::new(&file_root).is_err());

    let store_root = directory.path().join("store");
    let store = FileSystemArtifactStore::new(&store_root).expect("store must initialize");
    fs::write(
        store.root().join(BLOB_DIRECTORY).join("sha256"),
        b"not a directory",
    )
    .expect("non-directory digest component must be written");
    let digest = sample_digest();
    let mut upload = store
        .begin_upload("non-directory")
        .expect("upload must start");
    upload
        .write_chunk(b"hello spatial memory")
        .expect("sample bytes must write");

    assert!(upload.finalize(&digest, 20).is_err());
    upload.abort().expect("rejected upload must abort");
}
