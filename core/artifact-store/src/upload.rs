//! Staged artifact upload lifecycle and finalized metadata access.

use crate::digest::{digest_hasher, normalize_digest};
use crate::path_validation::{
    create_directory, io_error, remove_staging, require_regular_file, seal_blob, sync_directory,
    validate_directory_tree,
};
use crate::types::{ArtifactStoreError, ArtifactUpload, StoredArtifact, UploadState};
use sha2::Digest;
use std::fs;
use std::io::{self, Write};
use std::path::Path;

impl ArtifactUpload {
    /// Returns the caller-supplied upload identifier.
    pub fn upload_id(&self) -> &str {
        &self.upload_id
    }

    /// Returns the number of bytes accepted so far.
    pub fn size(&self) -> u64 {
        self.size
    }

    /// Appends one bounded chunk to the staging file and updates its digest.
    pub fn write_chunk(&mut self, chunk: &[u8]) -> Result<(), ArtifactStoreError> {
        self.ensure_active()?;
        let incoming =
            u64::try_from(chunk.len()).map_err(|_| ArtifactStoreError::SizeMismatch {
                expected: u64::MAX,
                actual: self.size,
            })?;
        let next_size =
            self.size
                .checked_add(incoming)
                .ok_or(ArtifactStoreError::SizeMismatch {
                    expected: u64::MAX,
                    actual: self.size,
                })?;
        let file = self
            .file
            .as_mut()
            .ok_or_else(|| ArtifactStoreError::UploadClosed {
                upload_id: self.upload_id.clone(),
            })?;
        file.write_all(chunk)
            .map_err(|error| io_error("write artifact chunk", error))?;
        self.hasher.update(chunk);
        self.size = next_size;
        Ok(())
    }

    /// Flushes, validates, and atomically commits this upload to its digest path.
    pub fn finalize(
        &mut self,
        expected_digest: &str,
        expected_size: u64,
    ) -> Result<StoredArtifact, ArtifactStoreError> {
        self.ensure_active()?;
        let expected_digest = normalize_digest(expected_digest)?;
        if self.size != expected_size {
            return Err(ArtifactStoreError::SizeMismatch {
                expected: expected_size,
                actual: self.size,
            });
        }
        let actual_digest = digest_hasher(&self.hasher);
        if actual_digest != expected_digest {
            return Err(ArtifactStoreError::DigestMismatch {
                expected: expected_digest,
                actual: actual_digest,
            });
        }

        let destination = self.store.blob_path(&expected_digest);
        create_directory(
            destination
                .parent()
                .expect("digest blob path always has a parent"),
            "create artifact digest directory",
        )?;
        let destination_exists = match fs::symlink_metadata(&destination) {
            Ok(_) => true,
            Err(error) if error.kind() == io::ErrorKind::NotFound => false,
            Err(error) => return Err(io_error("inspect artifact destination", error)),
        };
        if destination_exists {
            if self
                .store
                .existing_matches(&destination, &expected_digest, expected_size)?
            {
                seal_blob(&destination)?;
                sync_directory(
                    destination
                        .parent()
                        .expect("digest blob path always has a parent"),
                    "sync deduplicated artifact directory",
                )?;
                self.close_staging()?;
                self.state = UploadState::Finalized;
                return Ok(StoredArtifact {
                    digest: expected_digest,
                    size: expected_size,
                    path: destination,
                });
            }
            return Err(ArtifactStoreError::ArtifactConflict {
                digest: expected_digest,
            });
        }

        if let Some(file) = self.file.as_ref() {
            file.sync_all()
                .map_err(|error| io_error("sync artifact upload", error))?;
        }
        validate_directory_tree(
            self.staging_path
                .parent()
                .expect("staging path always has a parent"),
            false,
            "validate artifact staging directory",
        )?;
        require_regular_file(&self.staging_path, "inspect artifact staging file")?;
        let file = self
            .file
            .take()
            .ok_or_else(|| ArtifactStoreError::UploadClosed {
                upload_id: self.upload_id.clone(),
            })?;
        drop(file);
        // A hard link publishes the already-written inode without replacing an
        // existing destination.  The subsequent unlink only removes the
        // staging name, so concurrent finalizers become either one publisher
        // or a deduplicated retry rather than an overwrite race.
        match fs::hard_link(&self.staging_path, &destination) {
            Ok(()) => {
                seal_blob(&destination)?;
                sync_directory(
                    destination
                        .parent()
                        .expect("digest blob path always has a parent"),
                    "sync finalized artifact directory",
                )?;
                remove_staging(&self.staging_path)?;
                self.state = UploadState::Finalized;
                Ok(StoredArtifact {
                    digest: expected_digest,
                    size: expected_size,
                    path: destination,
                })
            }
            Err(error) if error.kind() == io::ErrorKind::AlreadyExists => {
                if self
                    .store
                    .existing_matches(&destination, &expected_digest, expected_size)?
                {
                    seal_blob(&destination)?;
                    sync_directory(
                        destination
                            .parent()
                            .expect("digest blob path always has a parent"),
                        "sync concurrently finalized artifact directory",
                    )?;
                    remove_staging(&self.staging_path)?;
                    self.state = UploadState::Finalized;
                    Ok(StoredArtifact {
                        digest: expected_digest,
                        size: expected_size,
                        path: destination,
                    })
                } else {
                    Err(ArtifactStoreError::ArtifactConflict {
                        digest: expected_digest,
                    })
                }
            }
            Err(error) => Err(io_error("finalize artifact upload", error)),
        }
    }

    /// Removes the staging file and marks this upload aborted.
    pub fn abort(&mut self) -> Result<(), ArtifactStoreError> {
        if self.state == UploadState::Finalized {
            return Ok(());
        }
        self.file.take();
        remove_staging(&self.staging_path)?;
        self.state = UploadState::Aborted;
        Ok(())
    }

    /// Rejects operations after finalization or abort.
    fn ensure_active(&self) -> Result<(), ArtifactStoreError> {
        if self.state == UploadState::Active {
            Ok(())
        } else {
            Err(ArtifactStoreError::UploadClosed {
                upload_id: self.upload_id.clone(),
            })
        }
    }

    /// Closes and removes a deduplicated staging file.
    fn close_staging(&mut self) -> Result<(), ArtifactStoreError> {
        self.file.take();
        remove_staging(&self.staging_path)
    }
}

impl Drop for ArtifactUpload {
    /// Cleans an abandoned staging file without affecting finalized blobs.
    fn drop(&mut self) {
        if self.state == UploadState::Active {
            self.file.take();
            let _ = remove_staging(&self.staging_path);
        }
    }
}

impl StoredArtifact {
    /// Returns the canonical `sha256:<lowercase hex>` digest.
    pub fn digest(&self) -> &str {
        &self.digest
    }

    /// Returns the immutable artifact byte count.
    pub const fn size(&self) -> u64 {
        self.size
    }

    /// Returns the content-addressed filesystem path.
    pub fn path(&self) -> &Path {
        &self.path
    }
}
