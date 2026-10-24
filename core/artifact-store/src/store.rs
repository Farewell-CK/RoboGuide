//! Filesystem store initialization, lookup, and verification.

use crate::digest::{DIGEST_PREFIX, digest_reader, normalize_digest};
use crate::path_validation::{
    add_no_follow_flag, cleanup_staging_except, create_directory, io_error, open_regular_file,
    seal_blob, sync_directory, unsafe_path_error, validate_blob_parent, validate_directory_tree,
    validate_upload_id,
};
use crate::types::{
    ArtifactStoreError, ArtifactUpload, ArtifactWriterLease, FileSystemArtifactStore,
    StoredArtifact, UploadState,
};
use sha2::{Digest, Sha256};
use std::collections::BTreeSet;
use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};
use std::sync::Arc;

/// Directory holding incomplete upload files.
pub(crate) const STAGING_DIRECTORY: &str = "staging";
/// Directory holding finalized content-addressed blobs.
pub(crate) const BLOB_DIRECTORY: &str = "blobs";
/// Persistent advisory-lock file fencing writers that share one artifact root.
const WRITER_LOCK_FILE: &str = ".writer.lock";

impl FileSystemArtifactStore {
    /// Leases an existing complete retained source without creating, repairing or upgrading any file.
    /// The original layout and journals must already be valid before this handle can read payloads.
    pub fn open_existing_managed(root: &Path) -> Result<Self, ArtifactStoreError> {
        validate_directory_tree(root, false, "open existing Artifact source")?;
        let root = root
            .canonicalize()
            .map_err(|error| io_error("resolve existing Artifact source", error))?;
        let file = open_regular_file(
            &root.join(WRITER_LOCK_FILE),
            "open existing Artifact writer lease",
        )?;
        file.try_lock()
            .map_err(|error| io_error("lease existing Artifact source", io::Error::other(error)))?;
        let writer_lock = Arc::new(ArtifactWriterLease {
            file,
            prepared_sources: std::sync::Mutex::new(std::collections::BTreeMap::new()),
        });
        let (owner, schema) = crate::layout::inspect_retained(&root)?;
        let storage_version = match schema {
            crate::ARTIFACT_LAYOUT_SCHEMA => 1,
            crate::ARTIFACT_REFERENCE_LAYOUT_SCHEMA => 2,
            crate::ARTIFACT_ARCHIVE_LAYOUT_SCHEMA => 3,
            crate::ARTIFACT_GC_LAYOUT_SCHEMA => 5,
            _ => {
                return Err(unsafe_path_error(
                    "open existing Artifact source",
                    "unsupported original layout",
                ));
            }
        };
        Ok(Self {
            root,
            blob_directory: crate::RETAINED_BLOB_DIRECTORY,
            owner_id: Some(owner),
            storage_version,
            _writer_lock: writer_lock,
        })
    }

    /// Creates and exclusively leases a store root before initializing its managed directories.
    ///
    /// The returned store holds the writer lease for the lifetime of all its clones and uploads.
    /// A second initializer for the same canonical root fails without inspecting or cleaning
    /// staging files owned by the active writer.
    pub fn new(root: impl Into<PathBuf>) -> Result<Self, ArtifactStoreError> {
        Self::open_layout(root.into(), 0)
    }

    /// Opens the explicit retained layout, preserving original bytes and fencing older writers.
    /// A partial migration resumes its original nonce; missing or conflicting identity refuses.
    pub fn new_with_retention(root: impl Into<PathBuf>) -> Result<Self, ArtifactStoreError> {
        Self::open_layout(root.into(), 1)
    }

    /// Opens explicit same-owner storage v2 with mandatory original maintenance-reference history.
    pub fn new_with_reference_journal(
        root: impl Into<PathBuf>,
    ) -> Result<Self, ArtifactStoreError> {
        Self::open_layout(root.into(), 2)
    }

    /// Opens same-owner v3, which inventories all original Controller consumers before reference work.
    pub fn new_with_archive_references(
        root: impl Into<PathBuf>,
    ) -> Result<Self, ArtifactStoreError> {
        Self::open_layout(root.into(), 3)
    }

    /// Opens explicit source storage v5, retaining all original authorities and irreversible GC fences.
    pub fn new_with_reclamation(root: impl Into<PathBuf>) -> Result<Self, ArtifactStoreError> {
        Self::open_layout(root.into(), 5)
    }

    /// Retains an original Controller store as a consumer before its Catalog/Archive producer effects.
    pub fn bind_reference_authority(&self, authority_id: &str) -> Result<(), ArtifactStoreError> {
        if !matches!(self.storage_version, 3 | 5) {
            return Err(unsafe_path_error(
                "bind Artifact reference authority",
                "explicit storage v3 is required",
            ));
        }
        crate::layout::bind_authority(&self.root, authority_id)
    }

    /// Acquires the actual writer before any format check, migration, or managed-directory effect.
    fn open_layout(root: PathBuf, version: u8) -> Result<Self, ArtifactStoreError> {
        let root =
            std::path::absolute(root).map_err(|error| io_error("resolve artifact root", error))?;
        create_directory(&root, "create artifact root")?;
        let root = root
            .canonicalize()
            .map_err(|error| io_error("canonicalize artifact root", error))?;
        let writer_lock = acquire_writer_lock(&root)?;
        create_directory(
            &root.join(STAGING_DIRECTORY),
            "create artifact staging directory",
        )?;
        let layout = if version == 5 {
            crate::layout::open_reclamation(&root)?
        } else if version == 3 {
            crate::layout::open_authorities(&root)?
        } else if version == 2 {
            crate::layout::open_references(&root)?
        } else {
            crate::layout::open(&root, version == 1)?
        };
        Ok(Self {
            root,
            blob_directory: layout.blob_directory,
            owner_id: layout.owner_id,
            storage_version: version,
            _writer_lock: writer_lock,
        })
    }

    /// Returns the configured root directory.
    pub fn root(&self) -> &Path {
        &self.root
    }

    /// Returns the original software backend identity without granting content access or use.
    pub fn owner_id(&self) -> Option<&str> {
        self.owner_id.as_deref()
    }

    /// Observes the actual held original writer descriptor and refuses a replaced path or CAS identity.
    pub fn writer_identity(&self) -> Result<crate::ArtifactWriterIdentity, ArtifactStoreError> {
        #[cfg(unix)]
        {
            use std::os::unix::fs::MetadataExt;
            let owner = self.owner_id().ok_or_else(|| {
                unsafe_path_error(
                    "observe original Artifact writer",
                    "retained original owner is unavailable",
                )
            })?;
            let (stored, _) = crate::layout::inspect_retained(&self.root)?;
            if stored != owner {
                return Err(unsafe_path_error(
                    "observe original Artifact writer",
                    "original owner changed",
                ));
            }
            let actual = self
                ._writer_lock
                .file
                .metadata()
                .map_err(|error| io_error("observe original writer descriptor", error))?;
            let named = fs::symlink_metadata(self.root.join(WRITER_LOCK_FILE))
                .map_err(|error| io_error("observe original writer path", error))?;
            if !actual.is_file()
                || !named.file_type().is_file()
                || (actual.dev(), actual.ino()) != (named.dev(), named.ino())
                || actual.ino() == 0
            {
                return Err(unsafe_path_error(
                    "observe original Artifact writer",
                    "original locked writer was replaced",
                ));
            }
            Ok(crate::ArtifactWriterIdentity {
                owner_id: owner.into(),
                lease_device: actual.dev(),
                lease_inode: actual.ino(),
            })
        }
        #[cfg(not(unix))]
        Err(unsafe_path_error(
            "observe original Artifact writer",
            "an actual supported writer-lease profile is required",
        ))
    }

    /// Reports the explicitly opened format without interpreting storage identity as permission.
    pub fn storage_schema(&self) -> &'static str {
        if self.storage_version == 5 {
            crate::layout::ARTIFACT_GC_LAYOUT_SCHEMA
        } else if self.storage_version == 3 {
            crate::layout::ARTIFACT_ARCHIVE_LAYOUT_SCHEMA
        } else if self.storage_version == 2 {
            crate::layout::ARTIFACT_REFERENCE_LAYOUT_SCHEMA
        } else if self.storage_version == 1 {
            crate::layout::ARTIFACT_LAYOUT_SCHEMA
        } else {
            "legacy"
        }
    }

    /// Starts an upload using a caller-provided, path-safe temporary identifier.
    pub fn begin_upload(
        &self,
        upload_id: impl Into<String>,
    ) -> Result<ArtifactUpload, ArtifactStoreError> {
        let upload_id = validate_upload_id(upload_id.into())?;
        self.require_unfrozen_upload(&upload_id)?;
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
            prepared_source: std::sync::Weak::new(),
            upload_id,
            staging_path,
            file: Some(file),
            hasher: Sha256::new(),
            size: 0,
            store: self.clone(),
            state: UploadState::Active,
        })
    }

    /// Reopens one durable staging file after a process restart and rebuilds its hash state.
    ///
    /// The caller must have recovered the same upload identity from the authoritative event log;
    /// a filename alone never grants ownership or publication authority.
    pub fn resume_upload(
        &self,
        upload_id: impl Into<String>,
    ) -> Result<ArtifactUpload, ArtifactStoreError> {
        let upload_id = validate_upload_id(upload_id.into())?;
        self.require_unfrozen_upload(&upload_id)?;
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
                    "resume artifact upload",
                    "staging path cannot be a symbolic link",
                ));
            }
            Ok(metadata) if !metadata.is_file() => {
                return Err(unsafe_path_error(
                    "resume artifact upload",
                    "staging path must be a regular file",
                ));
            }
            Ok(_) => {}
            Err(error) if error.kind() == io::ErrorKind::NotFound => {
                return Err(ArtifactStoreError::UploadNotFound { upload_id });
            }
            Err(error) => return Err(io_error("inspect artifact staging path", error)),
        };
        let mut options = OpenOptions::new();
        options.read(true).write(true);
        add_no_follow_flag(&mut options);
        let mut file = options
            .open(&staging_path)
            .map_err(|error| io_error("resume artifact upload", error))?;
        let metadata = file
            .metadata()
            .map_err(|error| io_error("inspect resumed artifact upload", error))?;
        if !metadata.is_file() {
            return Err(unsafe_path_error(
                "resume artifact upload",
                "staging path must resolve to a regular file",
            ));
        }
        let mut hasher = Sha256::new();
        let mut buffer = [0_u8; 64 * 1024];
        loop {
            let count = file
                .read(&mut buffer)
                .map_err(|error| io_error("hash resumed artifact upload", error))?;
            if count == 0 {
                break;
            }
            hasher.update(&buffer[..count]);
        }
        file.seek(SeekFrom::End(0))
            .map_err(|error| io_error("position resumed artifact upload", error))?;
        Ok(ArtifactUpload {
            prepared_source: std::sync::Weak::new(),
            upload_id,
            staging_path,
            file: Some(file),
            hasher,
            size: metadata.len(),
            store: self.clone(),
            state: UploadState::Active,
        })
    }

    /// Removes staging files not named by a durable Started/Prepared upload record.
    pub fn cleanup_staging_except(
        &self,
        preserve_upload_ids: &BTreeSet<String>,
    ) -> Result<(), ArtifactStoreError> {
        cleanup_staging_except(&self.root.join(STAGING_DIRECTORY), preserve_upload_ids)
    }

    /// Opens a finalized artifact for streaming reads by digest.
    pub fn open_artifact(&self, digest: &str) -> Result<File, ArtifactStoreError> {
        let digest = normalize_digest(digest)?;
        self.require_live_content(&digest)?;
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
        match self.require_live_content(&digest) {
            Err(ArtifactStoreError::ArtifactNotFound { .. }) => return Ok(false),
            result => result?,
        }
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
        self.require_live_content(&digest)?;
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
            .join(self.blob_directory)
            .join("sha256")
            .join(&hex[..2])
            .join(hex)
    }

    /// Checks mandatory original GC authority before any read, deduplication or new publication effect.
    pub(crate) fn require_live_content(&self, digest: &str) -> Result<(), ArtifactStoreError> {
        if self.storage_version == 5 {
            crate::layout::require_live_content(&self.root, digest)?;
        }
        Ok(())
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
fn acquire_writer_lock(root: &Path) -> Result<Arc<ArtifactWriterLease>, ArtifactStoreError> {
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
    Ok(Arc::new(ArtifactWriterLease {
        file,
        prepared_sources: std::sync::Mutex::new(std::collections::BTreeMap::new()),
    }))
}
