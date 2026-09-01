//! Filesystem content-addressed storage for immutable execution artifacts.
//!
//! This crate is the concrete infrastructure implementation of the
//! transport-neutral `ports::ArtifactBlobStore` port. It stores opaque bytes
//! addressed by a validated SHA-256 digest and does not interpret map manifests,
//! execution state, or ownership decisions. Catalog and lifecycle policy stay in
//! the State and Control planes respectively.

use sha2::{Digest, Sha256};
use std::fmt::{Display, Formatter};
use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Write};
#[cfg(unix)]
use std::os::unix::fs::OpenOptionsExt;
use std::path::{Path, PathBuf};
use std::sync::Arc;

/// Prefix used by the canonical digest representation.
const DIGEST_PREFIX: &str = "sha256:";
/// Number of hexadecimal characters in a SHA-256 digest.
const DIGEST_HEX_LENGTH: usize = 64;
/// Directory holding incomplete upload files.
const STAGING_DIRECTORY: &str = "staging";
/// Directory holding finalized content-addressed blobs.
const BLOB_DIRECTORY: &str = "blobs";
/// Persistent advisory-lock file fencing writers that share one artifact root.
const WRITER_LOCK_FILE: &str = ".writer.lock";

/// Filesystem-backed content-addressed artifact store.
///
/// The store uses a staging file for each upload and moves it into a digest
/// derived path only after the caller's expected digest and byte count match.
/// Existing valid blobs are reused, making retries idempotent.
#[derive(Debug, Clone)]
pub struct FileSystemArtifactStore {
    /// Root directory containing the staging and blob trees.
    root: PathBuf,
    /// Exclusive lease shared by every clone and in-progress upload from this store.
    _writer_lock: Arc<File>,
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
    upload_id: String,
    /// Temporary path retained until successful finalization or abort.
    staging_path: PathBuf,
    /// Open staging file while the upload is active.
    file: Option<File>,
    /// Incremental SHA-256 state over all accepted bytes.
    hasher: Sha256,
    /// Number of bytes accepted by [`ArtifactUpload::write_chunk`].
    size: u64,
    /// Store used to resolve the final content-addressed destination.
    store: FileSystemArtifactStore,
    /// Lifecycle state used to reject writes after terminal transitions.
    state: UploadState,
}

/// Terminal state of an artifact upload handle.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum UploadState {
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
    digest: String,
    /// Number of bytes in the stored blob.
    size: u64,
    /// Absolute content-addressed path beneath the validated store root.
    path: PathBuf,
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

impl FileSystemArtifactStore {
    /// Creates and exclusively leases a store root before initializing its managed directories.
    ///
    /// The returned store holds the writer lease for the lifetime of all its clones and uploads.
    /// A second initializer for the same canonical root fails without inspecting or cleaning
    /// staging files owned by the active writer.
    pub fn new(root: impl Into<PathBuf>) -> Result<Self, ArtifactStoreError> {
        let root = std::path::absolute(root.into())
            .map_err(|error| io_error("resolve artifact root", error))?;
        create_directory(&root, "create artifact root")?;
        let root = root
            .canonicalize()
            .map_err(|error| io_error("canonicalize artifact root", error))?;
        let writer_lock = acquire_writer_lock(&root)?;
        create_directory(
            &root.join(STAGING_DIRECTORY),
            "create artifact staging directory",
        )?;
        create_directory(&root.join(BLOB_DIRECTORY), "create artifact blob directory")?;
        cleanup_abandoned_staging(&root.join(STAGING_DIRECTORY))?;
        Ok(Self {
            root,
            _writer_lock: writer_lock,
        })
    }

    /// Returns the configured root directory.
    pub fn root(&self) -> &Path {
        &self.root
    }

    /// Starts an upload using a caller-provided, path-safe temporary identifier.
    pub fn begin_upload(
        &self,
        upload_id: impl Into<String>,
    ) -> Result<ArtifactUpload, ArtifactStoreError> {
        let upload_id = validate_upload_id(upload_id.into())?;
        let staging_directory = self.root.join(STAGING_DIRECTORY);
        validate_directory_tree(
            &staging_directory,
            false,
            "validate artifact staging directory",
        )?;
        let staging_path = staging_directory.join(format!("{upload_id}.partial"));
        match fs::symlink_metadata(&staging_path) {
            Ok(metadata) if metadata.file_type().is_symlink() => {
                return Err(unsafe_path_error(
                    "begin upload",
                    "staging path cannot be a symbolic link",
                ));
            }
            Ok(_) => return Err(ArtifactStoreError::UploadAlreadyExists { upload_id }),
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Err(error) => return Err(io_error("inspect artifact staging path", error)),
        }
        let mut options = OpenOptions::new();
        options.create_new(true).write(true).read(true);
        add_no_follow_flag(&mut options);
        let file = options.open(&staging_path).map_err(|error| {
            if error.kind() == io::ErrorKind::AlreadyExists {
                ArtifactStoreError::UploadAlreadyExists {
                    upload_id: upload_id.clone(),
                }
            } else {
                io_error("begin upload", error)
            }
        })?;
        if !file
            .metadata()
            .map_err(|error| io_error("inspect artifact upload", error))?
            .is_file()
        {
            return Err(unsafe_path_error(
                "begin upload",
                "staging path must be a regular file",
            ));
        }
        Ok(ArtifactUpload {
            upload_id,
            staging_path,
            file: Some(file),
            hasher: Sha256::new(),
            size: 0,
            store: self.clone(),
            state: UploadState::Active,
        })
    }

    /// Opens a finalized artifact for streaming reads by digest.
    pub fn open_artifact(&self, digest: &str) -> Result<File, ArtifactStoreError> {
        let digest = normalize_digest(digest)?;
        let path = self.blob_path(&digest);
        validate_blob_parent(&path, true)?;
        match fs::symlink_metadata(&path) {
            Err(error) if error.kind() == io::ErrorKind::NotFound => {
                return Err(ArtifactStoreError::ArtifactNotFound { digest });
            }
            Err(error) => return Err(io_error("inspect artifact", error)),
            Ok(metadata) if metadata.file_type().is_symlink() || !metadata.is_file() => {
                return Err(unsafe_path_error(
                    "open artifact",
                    "artifact path must be a regular file and cannot be a symbolic link",
                ));
            }
            Ok(_) => {}
        }
        open_regular_file(&path, "open artifact").map_err(|error| match error {
            ArtifactStoreError::Io { source, .. } if source.kind() == io::ErrorKind::NotFound => {
                ArtifactStoreError::ArtifactNotFound { digest }
            }
            other => other,
        })
    }

    /// Copies a finalized artifact to a caller-owned writer without buffering it whole.
    pub fn copy_artifact<W: Write>(
        &self,
        digest: &str,
        writer: &mut W,
    ) -> Result<u64, ArtifactStoreError> {
        let mut file = self.open_artifact(digest)?;
        io::copy(&mut file, writer).map_err(|error| io_error("read artifact", error))
    }

    /// Reports whether a digest path currently exists as a regular file.
    pub fn contains(&self, digest: &str) -> Result<bool, ArtifactStoreError> {
        let digest = normalize_digest(digest)?;
        let path = self.blob_path(&digest);
        validate_blob_parent(&path, true)?;
        match fs::symlink_metadata(path) {
            Ok(metadata) if metadata.file_type().is_symlink() => Err(unsafe_path_error(
                "inspect artifact",
                "artifact path cannot be a symbolic link",
            )),
            Ok(metadata) => Ok(metadata.is_file()),
            Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(false),
            Err(error) => Err(io_error("inspect artifact", error)),
        }
    }

    /// Verifies that a finalized digest path still contains the declared bytes and size.
    ///
    /// Catalog publication calls this boundary immediately before recording `Published`, so local
    /// corruption cannot create manifest evidence for bytes that no longer match their CAS name.
    pub fn verify_artifact(
        &self,
        digest: &str,
        expected_size: u64,
    ) -> Result<StoredArtifact, ArtifactStoreError> {
        let digest = normalize_digest(digest)?;
        let path = self.blob_path(&digest);
        if !self.existing_matches(&path, &digest, expected_size)? {
            return match fs::symlink_metadata(&path) {
                Err(error) if error.kind() == io::ErrorKind::NotFound => {
                    Err(ArtifactStoreError::ArtifactNotFound { digest })
                }
                Err(error) => Err(io_error("inspect artifact for publication", error)),
                Ok(_) => Err(ArtifactStoreError::ArtifactConflict { digest }),
            };
        }
        seal_blob(&path)?;
        Ok(StoredArtifact {
            digest,
            size: expected_size,
            path,
        })
    }

    /// Returns the path used for one normalized digest.
    fn blob_path(&self, digest: &str) -> PathBuf {
        let hex = digest.strip_prefix(DIGEST_PREFIX).unwrap_or(digest);
        self.root
            .join(BLOB_DIRECTORY)
            .join("sha256")
            .join(&hex[..2])
            .join(hex)
    }

    /// Verifies an existing digest path before treating a retry as deduplicated.
    fn existing_matches(
        &self,
        path: &Path,
        expected_digest: &str,
        expected_size: u64,
    ) -> Result<bool, ArtifactStoreError> {
        validate_blob_parent(path, true)?;
        let metadata = match fs::symlink_metadata(path) {
            Ok(metadata) => metadata,
            Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(false),
            Err(error) => return Err(io_error("inspect existing artifact", error)),
        };
        if metadata.file_type().is_symlink()
            || !metadata.is_file()
            || metadata.len() != expected_size
        {
            return Ok(false);
        }
        let mut file = open_regular_file(path, "verify existing artifact")?;
        let actual = digest_reader(&mut file)?;
        Ok(actual == expected_digest)
    }
}

/// Opens and exclusively locks the persistent writer lease file beneath one canonical root.
///
/// The lock attempt is non-blocking so composition roots fail startup clearly when another
/// process already owns the CAS. The returned descriptor must remain open for the writer lifetime.
fn acquire_writer_lock(root: &Path) -> Result<Arc<File>, ArtifactStoreError> {
    validate_directory_tree(root, false, "validate artifact root for writer lock")?;
    let lock_path = root.join(WRITER_LOCK_FILE);
    match fs::symlink_metadata(&lock_path) {
        Ok(metadata) if metadata.file_type().is_symlink() || !metadata.is_file() => {
            return Err(unsafe_path_error(
                "acquire artifact root writer lock",
                "artifact writer lock path must be a regular file and cannot be a symbolic link",
            ));
        }
        Ok(_) => {}
        Err(error) if error.kind() == io::ErrorKind::NotFound => {}
        Err(error) => return Err(io_error("inspect artifact root writer lock", error)),
    }
    let mut options = OpenOptions::new();
    options.create(true).truncate(false).read(true).write(true);
    add_no_follow_flag(&mut options);
    let file = options
        .open(&lock_path)
        .map_err(|error| io_error("open artifact root writer lock", error))?;
    if !file
        .metadata()
        .map_err(|error| io_error("inspect artifact root writer lock", error))?
        .is_file()
    {
        return Err(unsafe_path_error(
            "acquire artifact root writer lock",
            "artifact writer lock path must resolve to a regular file",
        ));
    }
    file.try_lock().map_err(|error| {
        io_error(
            "acquire artifact root writer lock",
            io::Error::other(format!(
                "artifact root {} is already owned by another writer: {error}",
                root.display()
            )),
        )
    })?;
    sync_directory(root, "sync artifact writer lock")?;
    Ok(Arc::new(file))
}

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

/// Normalizes accepted digest forms to canonical `sha256:<lowercase hex>` text.
pub fn normalize_digest(value: &str) -> Result<String, ArtifactStoreError> {
    let raw = value.strip_prefix(DIGEST_PREFIX).unwrap_or(value);
    if raw.len() != DIGEST_HEX_LENGTH || !raw.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err(ArtifactStoreError::InvalidDigest {
            value: value.to_string(),
        });
    }
    let lowercase = raw.to_ascii_lowercase();
    if raw != lowercase {
        return Err(ArtifactStoreError::InvalidDigest {
            value: value.to_string(),
        });
    }
    Ok(format!("{DIGEST_PREFIX}{lowercase}"))
}

/// Calculates a canonical SHA-256 digest for an in-memory chunk sequence.
pub fn digest_bytes(bytes: &[u8]) -> String {
    let digest = Sha256::digest(bytes);
    format!("{DIGEST_PREFIX}{digest:x}")
}

/// Calculates a canonical SHA-256 digest while reading all bytes from a source.
pub fn digest_reader<R: Read>(reader: &mut R) -> Result<String, ArtifactStoreError> {
    let mut hasher = Sha256::new();
    let mut buffer = [0_u8; 64 * 1024];
    loop {
        let count = reader
            .read(&mut buffer)
            .map_err(|error| io_error("digest artifact", error))?;
        if count == 0 {
            break;
        }
        hasher.update(&buffer[..count]);
    }
    Ok(digest_hasher(&hasher))
}

/// Validates an upload identifier against the staging filename grammar.
fn validate_upload_id(value: String) -> Result<String, ArtifactStoreError> {
    let valid = !value.is_empty()
        && value.len() <= 128
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_' | b'.'));
    if valid {
        Ok(value)
    } else {
        Err(ArtifactStoreError::InvalidUploadId { value })
    }
}

/// Converts a SHA-256 state into the canonical digest string.
fn digest_hasher(hasher: &Sha256) -> String {
    let digest = hasher.clone().finalize();
    format!("{DIGEST_PREFIX}{digest:x}")
}

/// Creates a directory tree while rejecting symlink and non-directory components.
///
/// Each new entry is durably recorded before later artifact publication can depend on it.
fn create_directory(path: &Path, operation: &'static str) -> Result<(), ArtifactStoreError> {
    let path = std::path::absolute(path).map_err(|error| io_error(operation, error))?;
    let mut current = PathBuf::new();
    for component in path.components() {
        current.push(component.as_os_str());
        match fs::symlink_metadata(&current) {
            Ok(metadata) => validate_directory_component(&metadata, operation)?,
            Err(error) if error.kind() == io::ErrorKind::NotFound => {
                match fs::create_dir(&current) {
                    Ok(()) => {
                        let parent = current
                            .parent()
                            .filter(|parent| !parent.as_os_str().is_empty())
                            .unwrap_or_else(|| Path::new("."));
                        sync_directory(parent, "sync artifact parent directory")?;
                        sync_directory(&current, "sync created artifact directory")?;
                    }
                    Err(error) if error.kind() == io::ErrorKind::AlreadyExists => {
                        let metadata = fs::symlink_metadata(&current)
                            .map_err(|error| io_error(operation, error))?;
                        validate_directory_component(&metadata, operation)?;
                    }
                    Err(error) => return Err(io_error(operation, error)),
                }
            }
            Err(error) => return Err(io_error(operation, error)),
        }
    }
    Ok(())
}

/// Validates all existing path components without following symbolic links.
///
/// When `allow_missing` is true, the first absent component and its descendants are accepted so
/// read-only lookup can report an absent digest without creating directories.
fn validate_directory_tree(
    path: &Path,
    allow_missing: bool,
    operation: &'static str,
) -> Result<(), ArtifactStoreError> {
    let path = std::path::absolute(path).map_err(|error| io_error(operation, error))?;
    let mut current = PathBuf::new();
    let mut missing = false;
    for component in path.components() {
        current.push(component.as_os_str());
        if missing {
            continue;
        }
        match fs::symlink_metadata(&current) {
            Ok(metadata) => validate_directory_component(&metadata, operation)?,
            Err(error) if error.kind() == io::ErrorKind::NotFound && allow_missing => {
                missing = true;
            }
            Err(error) => return Err(io_error(operation, error)),
        }
    }
    Ok(())
}

/// Rejects one path component unless it is a real directory rather than a symbolic link.
fn validate_directory_component(
    metadata: &fs::Metadata,
    operation: &'static str,
) -> Result<(), ArtifactStoreError> {
    if metadata.file_type().is_symlink() || !metadata.is_dir() {
        return Err(unsafe_path_error(
            operation,
            "artifact directory component must be a directory and cannot be a symbolic link",
        ));
    }
    Ok(())
}

/// Validates the parent directory of one digest-derived blob path.
fn validate_blob_parent(path: &Path, allow_missing: bool) -> Result<(), ArtifactStoreError> {
    let parent = path.parent().expect("digest blob path always has a parent");
    validate_directory_tree(parent, allow_missing, "validate artifact blob directory")
}

/// Rejects an absent, symbolic-link, or non-regular artifact file path.
fn require_regular_file(path: &Path, operation: &'static str) -> Result<(), ArtifactStoreError> {
    let metadata = fs::symlink_metadata(path).map_err(|error| io_error(operation, error))?;
    if metadata.file_type().is_symlink() || !metadata.is_file() {
        return Err(unsafe_path_error(
            operation,
            "artifact path must be a regular file and cannot be a symbolic link",
        ));
    }
    Ok(())
}

/// Opens a regular artifact file without following a symbolic-link leaf on Unix.
fn open_regular_file(path: &Path, operation: &'static str) -> Result<File, ArtifactStoreError> {
    let mut options = OpenOptions::new();
    options.read(true);
    add_no_follow_flag(&mut options);
    let file = options
        .open(path)
        .map_err(|error| io_error(operation, error))?;
    if !file
        .metadata()
        .map_err(|error| io_error(operation, error))?
        .is_file()
    {
        return Err(unsafe_path_error(
            operation,
            "artifact path must resolve to a regular file",
        ));
    }
    Ok(file)
}

/// Adds the Unix no-follow flag while leaving non-Unix path checks to `symlink_metadata`.
fn add_no_follow_flag(options: &mut OpenOptions) {
    #[cfg(unix)]
    options.custom_flags(libc::O_NOFOLLOW);
    #[cfg(not(unix))]
    let _ = options;
}

/// Removes a staging path while treating an already absent path as success.
fn remove_staging(path: &Path) -> Result<(), ArtifactStoreError> {
    if let Some(parent) = path.parent() {
        validate_directory_tree(parent, false, "validate artifact staging directory")?;
    }
    match fs::remove_file(path) {
        Ok(()) => {
            if let Some(parent) = path.parent() {
                sync_directory(parent, "sync artifact staging removal")?;
            }
            Ok(())
        }
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
        Err(error) => Err(io_error("remove artifact staging file", error)),
    }
}

/// Removes incomplete upload files left behind by a previous store process.
///
/// Upload handles are intentionally not recoverable because their incremental hash state is
/// process-local. Initialization therefore removes only `.partial` leaves from the validated
/// staging directory and refuses to recurse through an unexpected directory entry.
fn cleanup_abandoned_staging(staging_directory: &Path) -> Result<(), ArtifactStoreError> {
    validate_directory_tree(
        staging_directory,
        false,
        "validate artifact staging directory",
    )?;
    let entries = fs::read_dir(staging_directory)
        .map_err(|error| io_error("scan artifact staging directory", error))?;
    for entry in entries {
        let entry = entry.map_err(|error| io_error("read artifact staging entry", error))?;
        let file_name = entry.file_name();
        if !file_name.to_string_lossy().ends_with(".partial") {
            continue;
        }
        let metadata = fs::symlink_metadata(entry.path())
            .map_err(|error| io_error("inspect abandoned artifact upload", error))?;
        if metadata.is_dir() && !metadata.file_type().is_symlink() {
            return Err(unsafe_path_error(
                "clean abandoned artifact upload",
                "staging partial path cannot be a directory",
            ));
        }
        remove_staging(&entry.path())?;
    }
    Ok(())
}

/// Removes write permission from one verified finalized blob.
fn seal_blob(path: &Path) -> Result<(), ArtifactStoreError> {
    validate_blob_parent(path, false)?;
    require_regular_file(path, "inspect artifact for sealing")?;
    let file = open_regular_file(path, "open artifact for sealing")?;
    let mut permissions = file
        .metadata()
        .map_err(|error| io_error("inspect finalized artifact permissions", error))?
        .permissions();
    permissions.set_readonly(true);
    file.set_permissions(permissions)
        .map_err(|error| io_error("seal finalized artifact", error))?;
    file.sync_all()
        .map_err(|error| io_error("sync sealed artifact", error))
}

/// Flushes one directory so acknowledged link creation or removal survives power loss.
fn sync_directory(path: &Path, operation: &'static str) -> Result<(), ArtifactStoreError> {
    validate_directory_tree(path, false, operation)?;
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    options.custom_flags(libc::O_NOFOLLOW | libc::O_DIRECTORY);
    let directory = options
        .open(path)
        .map_err(|error| io_error(operation, error))?;
    directory
        .sync_all()
        .map_err(|error| io_error(operation, error))
}

/// Builds a stable invalid-data error for a filesystem entry that violates CAS confinement.
fn unsafe_path_error(operation: &'static str, reason: &'static str) -> ArtifactStoreError {
    io_error(
        operation,
        io::Error::new(io::ErrorKind::InvalidData, reason),
    )
}

/// Wraps one operating-system error with a stable artifact operation label.
fn io_error(operation: &'static str, source: io::Error) -> ArtifactStoreError {
    ArtifactStoreError::Io { operation, source }
}

#[cfg(test)]
mod tests {
    use super::*;
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

        let _store =
            FileSystemArtifactStore::new(directory.path()).expect("store must clean staging");

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

        let error = FileSystemArtifactStore::new(directory.path())
            .expect_err("second writer must be fenced");

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
}
