//! Public filesystem artifact-store handles and errors.

use sha2::Sha256;
use std::fmt::{Display, Formatter};
use std::fs::File;
use std::io;
use std::path::PathBuf;
use std::sync::Arc;

/// Filesystem-backed content-addressed artifact store.
///
/// The store stages uploads and publishes them only after digest and size
/// validation. Existing valid blobs are reused, making retries idempotent.
#[derive(Debug, Clone)]
pub struct FileSystemArtifactStore {
    /// Root directory containing the staging and blob trees.
    pub(crate) root: PathBuf,
    /// Exclusive lease shared by every clone and in-progress upload from this store.
    pub(crate) _writer_lock: Arc<File>,
}

impl PartialEq for FileSystemArtifactStore {
    /// Compares store identity by its canonical artifact root rather than its lock descriptor.
    fn eq(&self, other: &Self) -> bool {
        self.root == other.root
    }
}

impl Eq for FileSystemArtifactStore {}

/// An in-progress artifact upload that accepts bounded chunks.
///
/// The upload owns its temporary file and hasher.  Dropping an unfinished
/// upload removes its staging file, while callers can explicitly call
/// [`ArtifactUpload::abort`] when they want to make that transition visible.
pub struct ArtifactUpload {
    /// Caller-supplied identifier used only for the temporary filename.
    pub(crate) upload_id: String,
    /// Temporary path retained until successful finalization or abort.
    pub(crate) staging_path: PathBuf,
    /// Open staging file while the upload is active.
    pub(crate) file: Option<File>,
    /// Incremental SHA-256 state over all accepted bytes.
    pub(crate) hasher: Sha256,
    /// Number of bytes accepted by [`ArtifactUpload::write_chunk`].
    pub(crate) size: u64,
    /// Store used to resolve the final content-addressed destination.
    pub(crate) store: FileSystemArtifactStore,
    /// Lifecycle state used to reject writes after terminal transitions.
    pub(crate) state: UploadState,
}

/// Terminal state of an artifact upload handle.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum UploadState {
    /// The temporary file accepts more chunks.
    Active,
    /// The temporary file was atomically committed or deduplicated.
    Finalized,
    /// The temporary file was explicitly or implicitly removed.
    Aborted,
}

/// Immutable metadata returned after an artifact has been finalized.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StoredArtifact {
    /// Canonical lowercase SHA-256 hexadecimal content digest.
    pub(crate) digest: String,
    /// Number of bytes in the stored blob.
    pub(crate) size: u64,
    /// Absolute content-addressed path beneath the validated store root.
    pub(crate) path: PathBuf,
}

/// Failures raised by the filesystem artifact store.
#[derive(Debug)]
pub enum ArtifactStoreError {
    /// The supplied digest was not a SHA-256 value in accepted form.
    InvalidDigest {
        /// Original digest text supplied by the caller.
        value: String,
    },
    /// The upload identifier could not safely be used as one filename.
    InvalidUploadId {
        /// Original upload identifier supplied by the caller.
        value: String,
    },
    /// A filesystem operation failed.
    Io {
        /// Short operation name useful in logs and evidence.
        operation: &'static str,
        /// Underlying operating-system error.
        source: io::Error,
    },
    /// Another active upload already uses the requested identifier.
    UploadAlreadyExists {
        /// Conflicting upload identifier.
        upload_id: String,
    },
    /// The requested upload identifier has no active staging file.
    UploadNotFound {
        /// Missing upload identifier.
        upload_id: String,
    },
    /// An operation was attempted after a terminal upload transition.
    UploadClosed {
        /// Upload whose handle is no longer writable.
        upload_id: String,
    },
    /// The computed digest differed from the expected digest.
    DigestMismatch {
        /// Digest declared by the caller.
        expected: String,
        /// Digest calculated over staged bytes.
        actual: String,
    },
    /// The computed byte count differed from the expected byte count.
    SizeMismatch {
        /// Byte count declared by the caller.
        expected: u64,
        /// Byte count calculated over staged bytes.
        actual: u64,
    },
    /// A digest path already exists but its bytes do not match the requested blob.
    ArtifactConflict {
        /// Canonical digest whose destination is inconsistent.
        digest: String,
    },
    /// No finalized blob exists for the requested digest.
    ArtifactNotFound {
        /// Canonical digest that was not present.
        digest: String,
    },
}

impl Display for ArtifactStoreError {
    /// Formats a stable diagnostic without exposing filesystem-specific paths.
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::InvalidDigest { value } => write!(formatter, "invalid SHA-256 digest {value:?}"),
            Self::InvalidUploadId { value } => {
                write!(formatter, "invalid artifact upload id {value:?}")
            }
            Self::Io { operation, source } => {
                write!(formatter, "artifact {operation} failed: {source}")
            }
            Self::UploadAlreadyExists { upload_id } => {
                write!(formatter, "artifact upload {upload_id} already exists")
            }
            Self::UploadNotFound { upload_id } => {
                write!(formatter, "artifact upload {upload_id} was not found")
            }
            Self::UploadClosed { upload_id } => {
                write!(formatter, "artifact upload {upload_id} is closed")
            }
            Self::DigestMismatch { expected, actual } => {
                write!(
                    formatter,
                    "artifact digest mismatch: expected {expected}, actual {actual}"
                )
            }
            Self::SizeMismatch { expected, actual } => {
                write!(
                    formatter,
                    "artifact size mismatch: expected {expected}, actual {actual}"
                )
            }
            Self::ArtifactConflict { digest } => {
                write!(
                    formatter,
                    "artifact destination conflicts with digest {digest}"
                )
            }
            Self::ArtifactNotFound { digest } => {
                write!(formatter, "artifact {digest} was not found")
            }
        }
    }
}

impl std::error::Error for ArtifactStoreError {
    /// Returns the underlying filesystem error where one exists.
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Io { source, .. } => Some(source),
            _ => None,
        }
    }
}
