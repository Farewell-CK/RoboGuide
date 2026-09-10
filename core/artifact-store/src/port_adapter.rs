//! Transport-neutral artifact port adapters over the filesystem store.

use crate::digest::DIGEST_PREFIX;
use crate::types::{ArtifactStoreError, ArtifactUpload, FileSystemArtifactStore};
use std::fs::File;
use std::io::Read;

/// Adapts a filesystem upload to the transport-neutral blob writer port.
struct PortArtifactUpload {
    /// Filesystem upload handle receiving bounded chunks.
    upload: ArtifactUpload,
}

/// Adapts a filesystem file to the transport-neutral blob reader port.
struct PortArtifactReader {
    /// Immutable artifact file.
    file: File,
    /// Stable byte length captured before streaming.
    length: u64,
}

impl ports::ArtifactBlobWriter for PortArtifactUpload {
    /// Appends a bounded chunk to the filesystem staging file.
    fn write_chunk(&mut self, chunk: &[u8]) -> Result<(), ports::ArtifactStoreError> {
        self.upload.write_chunk(chunk).map_err(port_error)
    }

    /// Verifies and atomically finalizes the filesystem upload.
    fn finalize(
        &mut self,
        expected_digest: &domain::ContentDigest,
        expected_size: u64,
    ) -> Result<(), ports::ArtifactStoreError> {
        let digest = expected_digest.as_str().to_string();
        self.upload
            .finalize(&digest, expected_size)
            .map(|_| ())
            .map_err(port_error)
    }

    /// Removes the filesystem staging file.
    fn abort(&mut self) -> Result<(), ports::ArtifactStoreError> {
        self.upload.abort().map_err(port_error)
    }
}

impl ports::ArtifactBlobReader for PortArtifactReader {
    /// Returns the immutable artifact byte length.
    fn content_length(&self) -> u64 {
        self.length
    }

    /// Reads the next bounded chunk from the immutable artifact.
    fn read_chunk(&mut self, buffer: &mut [u8]) -> Result<usize, ports::ArtifactStoreError> {
        self.file
            .read(buffer)
            .map_err(|error| ports::ArtifactStoreError::Backend(format!("read artifact: {error}")))
    }
}

impl ports::ArtifactBlobStore for FileSystemArtifactStore {
    /// Starts a filesystem-backed temporary upload through the generic port.
    fn begin_upload(
        &mut self,
        upload_id: &str,
    ) -> Result<Box<dyn ports::ArtifactBlobWriter>, ports::ArtifactStoreError> {
        let upload = FileSystemArtifactStore::begin_upload(self, upload_id).map_err(port_error)?;
        Ok(Box::new(PortArtifactUpload { upload }))
    }

    /// Opens a content-addressed filesystem blob through the generic port.
    fn open_blob(
        &self,
        digest: &domain::ContentDigest,
    ) -> Result<Box<dyn ports::ArtifactBlobReader>, ports::ArtifactStoreError> {
        let text = digest.as_str().to_string();
        let file = self.open_artifact(&text).map_err(port_error)?;
        let length = file
            .metadata()
            .map_err(|error| ports::ArtifactStoreError::Backend(format!("stat artifact: {error}")))?
            .len();
        Ok(Box::new(PortArtifactReader { file, length }))
    }
}

/// Converts store-local failures into the transport-neutral port error.
fn port_error(error: ArtifactStoreError) -> ports::ArtifactStoreError {
    match error {
        ArtifactStoreError::InvalidUploadId { .. } => ports::ArtifactStoreError::InvalidUploadId,
        ArtifactStoreError::ArtifactNotFound { digest } => {
            match domain::ContentDigest::new(digest.strip_prefix(DIGEST_PREFIX).unwrap_or(&digest))
            {
                Ok(digest) => ports::ArtifactStoreError::NotFound(digest),
                Err(error) => ports::ArtifactStoreError::Backend(error.to_string()),
            }
        }
        ArtifactStoreError::DigestMismatch { expected, actual } => {
            let expected = domain::ContentDigest::new(
                expected.strip_prefix(DIGEST_PREFIX).unwrap_or(&expected),
            );
            let actual =
                domain::ContentDigest::new(actual.strip_prefix(DIGEST_PREFIX).unwrap_or(&actual));
            match (expected, actual) {
                (Ok(expected), Ok(actual)) => {
                    ports::ArtifactStoreError::DigestMismatch { expected, actual }
                }
                (Err(error), _) | (_, Err(error)) => {
                    ports::ArtifactStoreError::Backend(error.to_string())
                }
            }
        }
        ArtifactStoreError::SizeMismatch { expected, actual } => {
            ports::ArtifactStoreError::SizeMismatch { expected, actual }
        }
        ArtifactStoreError::ArtifactConflict { digest } => {
            ports::ArtifactStoreError::Conflict(digest)
        }
        other => ports::ArtifactStoreError::Backend(other.to_string()),
    }
}
