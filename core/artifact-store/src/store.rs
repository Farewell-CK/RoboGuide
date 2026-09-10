//! Filesystem store initialization, lookup, and verification.

use crate::digest::{DIGEST_PREFIX, digest_reader, normalize_digest};
use crate::path_validation::{
    add_no_follow_flag, cleanup_abandoned_staging, create_directory, io_error, open_regular_file,
    seal_blob, sync_directory, unsafe_path_error, validate_blob_parent, validate_directory_tree,
    validate_upload_id,
};
use crate::types::{
    ArtifactStoreError, ArtifactUpload, FileSystemArtifactStore, StoredArtifact, UploadState,
};
use sha2::{Digest, Sha256};
use std::fs::{self, File, OpenOptions};
use std::io::{self, Write};
use std::path::{Path, PathBuf};
use std::sync::Arc;

/// Directory holding incomplete upload files.
pub(crate) const STAGING_DIRECTORY: &str = "staging";
/// Directory holding finalized content-addressed blobs.
pub(crate) const BLOB_DIRECTORY: &str = "blobs";
/// Persistent advisory-lock file fencing writers that share one artifact root.
const WRITER_LOCK_FILE: &str = ".writer.lock";

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
    pub(crate) fn blob_path(&self, digest: &str) -> PathBuf {
        let hex = digest.strip_prefix(DIGEST_PREFIX).unwrap_or(digest);
        self.root
            .join(BLOB_DIRECTORY)
            .join("sha256")
            .join(&hex[..2])
            .join(hex)
    }

    /// Verifies an existing digest path before treating a retry as deduplicated.
    pub(crate) fn existing_matches(
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
