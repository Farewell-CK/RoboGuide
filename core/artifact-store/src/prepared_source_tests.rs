//! Actual prepared FD/append/revocation/capacity contracts; no device or execution facts are created.

use super::*;
use crate::digest_bytes;

/// Publishes no CAS while creating actual owned staged bytes under a retained original writer.
fn staged() -> (
    tempfile::TempDir,
    FileSystemArtifactStore,
    ArtifactUpload,
    Vec<u8>,
    String,
) {
    let directory = tempfile::tempdir().unwrap();
    let store =
        FileSystemArtifactStore::new_with_archive_references(directory.path().join("cas")).unwrap();
    let mut upload = store.begin_upload("actual-original-prepared").unwrap();
    let data = (0..READ_CHUNK * 2 + 19)
        .map(|value| (value % 251) as u8)
        .collect::<Vec<_>>();
    upload.write_chunk(&data).unwrap();
    let digest = digest_bytes(&data);
    (directory, store, upload, data, digest)
}

/// Original independent read_at positions never rewind the uploader; revocation never pretends FD exit.
#[test]
fn actual_prepared_sources_are_scoped_and_keep_public_cas_unavailable() {
    let (_directory, store, mut upload, data, digest) = staged();
    assert!(
        store
            .open_replication_source(&digest, data.len() as u64)
            .is_err()
    );
    let guard = upload
        .prepared_replication_source(&digest, data.len() as u64)
        .unwrap();
    assert!(store.open_artifact(&digest).is_err());
    let mut first = store
        .open_replication_source(&digest, data.len() as u64)
        .unwrap();
    let mut second = store
        .open_replication_source(&digest, data.len() as u64)
        .unwrap();
    let mut prefix = [0; 37];
    first.read_exact(&mut prefix).unwrap();
    assert_eq!(&prefix, &data[..37]);
    let mut all = Vec::new();
    second.read_to_end(&mut all).unwrap();
    assert_eq!(all, data);
    let mut rest = Vec::new();
    first.read_to_end(&mut rest).unwrap();
    assert_eq!(rest, data[37..]);
    assert!(matches!(
        upload.write_chunk(b"blocked"),
        Err(ArtifactStoreError::UploadSourceActive { .. })
    ));
    drop(guard);
    assert!(first.read(&mut prefix).is_err());
    assert!(
        store
            .open_replication_source(&digest, data.len() as u64)
            .is_err()
    );
    assert!(upload.write_chunk(b"still-blocked").is_err());
    drop(first);
    drop(second);
    upload.write_chunk(b"actual-new-tail").unwrap();
    let mut expected = data;
    expected.extend(b"actual-new-tail");
    let digest = digest_bytes(&expected);
    upload.finalize(&digest, expected.len() as u64).unwrap();
    store
        .verify_artifact(&digest, expected.len() as u64)
        .unwrap();
}

/// Actual publication keeps the original read FD stable; final CAS access has its separate lifetime.
#[test]
fn actual_prepared_finalize_preserves_bytes_and_finalized_lookup_is_independent() {
    let (_directory, store, mut upload, data, digest) = staged();
    let guard = upload
        .prepared_replication_source(&digest, data.len() as u64)
        .unwrap();
    let mut original = store
        .open_replication_source(&digest, data.len() as u64)
        .unwrap();
    upload.finalize(&digest, data.len() as u64).unwrap();
    let mut all = Vec::new();
    original.read_to_end(&mut all).unwrap();
    assert_eq!(all, data);
    drop(guard);
    assert!(original.read(&mut [0; 1]).is_err());
    let mut final_source = store
        .open_replication_source(&digest, data.len() as u64)
        .unwrap();
    let mut actual = Vec::new();
    final_source.read_to_end(&mut actual).unwrap();
    assert_eq!(actual, data);
}

/// Actual abort/drop revoke readers and retain their original writer leases until every reader ends.
#[test]
fn actual_prepared_reader_retains_original_writer_after_upload_and_guard_close() {
    let (directory, store, mut upload, data, digest) = staged();
    let owner = store.owner_id().unwrap().to_string();
    let guard = upload
        .prepared_replication_source(&digest, data.len() as u64)
        .unwrap();
    let mut reader = store
        .open_replication_source(&digest, data.len() as u64)
        .unwrap();
    upload.abort().unwrap();
    drop(upload);
    drop(guard);
    drop(store);
    assert!(reader.read(&mut [0; 1]).is_err());
    assert!(
        FileSystemArtifactStore::new_with_archive_references(directory.path().join("cas")).is_err()
    );
    drop(reader);
    let reopened =
        FileSystemArtifactStore::new_with_archive_references(directory.path().join("cas")).unwrap();
    assert_eq!(reopened.owner_id(), Some(owner.as_str()));
}

/// A replacement file cannot borrow the original uploader's size/hash or obtain a registered source.
#[test]
fn actual_prepared_source_refuses_replaced_staging_inode_and_wrong_original_size() {
    let (_directory, store, mut upload, data, digest) = staged();
    assert!(
        upload
            .prepared_replication_source(&digest, data.len() as u64 + 1)
            .is_err()
    );
    assert!(
        upload
            .prepared_replication_source(&digest_bytes(b"other"), data.len() as u64)
            .is_err()
    );
    let path = upload.staging_path.clone();
    let original = path.with_extension("original-held");
    std::fs::rename(&path, &original).unwrap();
    std::fs::write(&path, &data).unwrap();
    assert!(
        upload
            .prepared_replication_source(&digest, data.len() as u64)
            .is_err()
    );
    assert!(
        store
            .open_replication_source(&digest, data.len() as u64)
            .is_err()
    );
    std::fs::remove_file(&path).unwrap();
    std::fs::rename(original, path).unwrap();
    assert!(
        upload
            .prepared_replication_source(&digest, data.len() as u64)
            .is_ok()
    );
}

/// An earlier resumed handle cannot write during the scope or lend its stale hash to publication.
#[test]
fn actual_prepared_source_checks_file_bytes_and_serializes_original_resumed_handles() {
    use std::io::{Seek, SeekFrom, Write};
    let (_directory, store, mut upload, data, digest) = staged();
    let mut resumed = store.resume_upload(upload.upload_id()).unwrap();
    let guard = upload
        .prepared_replication_source(&digest, data.len() as u64)
        .unwrap();
    assert!(resumed.write_chunk(b"changed").is_err());
    assert!(store.resume_upload(upload.upload_id()).is_err());
    assert!(store.begin_upload(upload.upload_id()).is_err());
    assert!(
        resumed
            .finalize(&digest_bytes(b"other"), data.len() as u64)
            .is_err()
    );
    drop(guard);
    let file = resumed.file.as_mut().unwrap();
    file.seek(SeekFrom::Start(0)).unwrap();
    file.write_all(&vec![17; data.len()]).unwrap();
    assert!(matches!(
        upload.prepared_replication_source(&digest, data.len() as u64),
        Err(ArtifactStoreError::DigestMismatch { .. })
    ));
    file.seek(SeekFrom::Start(0)).unwrap();
    file.write_all(&data).unwrap();
    let guard = upload
        .prepared_replication_source(&digest, data.len() as u64)
        .unwrap();
    resumed.finalize(&digest, data.len() as u64).unwrap();
    store.verify_artifact(&digest, data.len() as u64).unwrap();
    drop(guard);
}

/// Revoked-but-live actual readers still consume the original bounded scope capacity.
#[test]
fn actual_prepared_capacity_cannot_evict_revoked_readers_or_live_original_sources() {
    let directory = tempfile::tempdir().unwrap();
    let store =
        FileSystemArtifactStore::new_with_archive_references(directory.path().join("cas")).unwrap();
    let mut uploads = Vec::new();
    let mut guards = Vec::new();
    let mut readers = Vec::new();
    for index in 0..MAX_PREPARED_SOURCES {
        let mut upload = store
            .begin_upload(format!("actual-original-{index}"))
            .unwrap();
        let bytes = index.to_le_bytes();
        let digest = digest_bytes(&bytes);
        upload.write_chunk(&bytes).unwrap();
        guards.push(
            upload
                .prepared_replication_source(&digest, bytes.len() as u64)
                .unwrap(),
        );
        readers.push(
            store
                .open_replication_source(&digest, bytes.len() as u64)
                .unwrap(),
        );
        uploads.push(upload);
    }
    drop(guards);
    let mut extra = store.begin_upload("actual-extra-original").unwrap();
    extra.write_chunk(b"extra").unwrap();
    let digest = digest_bytes(b"extra");
    assert!(
        extra
            .prepared_replication_source(&digest, 5)
            .err()
            .unwrap()
            .to_string()
            .contains("capacity")
    );
    drop(readers.pop());
    let guard = extra.prepared_replication_source(&digest, 5).unwrap();
    assert_eq!(
        store._writer_lock.prepared_sources.lock().unwrap().len(),
        MAX_PREPARED_SOURCES
    );
    drop(guard);
    drop(readers);
    drop(uploads);
}
