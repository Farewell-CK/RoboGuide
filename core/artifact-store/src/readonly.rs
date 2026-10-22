//! Original retained-owner observation under an existing shared lease, with no storage effects.

use crate::path_validation::{
    io_error, open_regular_file, validate_blob_parent, validate_directory_tree,
};
use crate::{ArtifactStoreError, RETAINED_BLOB_DIRECTORY, normalize_digest};
use std::fs::File;
use std::path::{Path, PathBuf};

/// Read-only retained CAS handle; opening never creates a root, lock, owner, journal or blob.
pub struct ArtifactReadOnlyStore {
    /// Actual previously accepted canonical software root.
    root: PathBuf,
    /// Original immutable actual software backend identity.
    owner_id: String,
    /// Previously accepted retained format, without compatibility normalization.
    schema: &'static str,
    /// Shared original root lease; all managed writers refuse while this observation lives.
    _lease: File,
}

impl ArtifactReadOnlyStore {
    /// Requires an existing retained root and lock before inspecting its complete original identity.
    pub fn open(root: &Path) -> Result<Self, ArtifactStoreError> {
        validate_directory_tree(root, false, "inspect readonly Artifact root")?;
        let lease = open_regular_file(
            &root.join(".writer.lock"),
            "open original readonly Artifact lease",
        )?;
        lease.try_lock_shared().map_err(|error| {
            io_error(
                "acquire original readonly Artifact lease",
                std::io::Error::other(error),
            )
        })?;
        let (owner_id, schema) = crate::layout::inspect_retained(root)?;
        Ok(Self {
            root: root.to_path_buf(),
            owner_id,
            schema,
            _lease: lease,
        })
    }

    /// Reports only the stored original software owner, never a tenant read or use grant.
    pub fn owner_id(&self) -> &str {
        &self.owner_id
    }

    /// Reports the exact previously accepted format, never an upgraded replacement.
    pub fn storage_schema(&self) -> &'static str {
        self.schema
    }

    /// Opens only one actual regular CAS file after exact path validation; callers bound all reads.
    pub fn open_artifact(&self, digest: &str) -> Result<File, ArtifactStoreError> {
        let digest = normalize_digest(digest)?;
        if self.schema == crate::ARTIFACT_GC_LAYOUT_SCHEMA {
            crate::layout::require_live_content(&self.root, &digest)?;
        }
        let hex = &digest[7..];
        let path = self
            .root
            .join(RETAINED_BLOB_DIRECTORY)
            .join("sha256")
            .join(&hex[..2])
            .join(hex);
        validate_blob_parent(&path, false)?;
        open_regular_file(&path, "observe original Artifact bytes")
    }
}

impl Drop for ArtifactReadOnlyStore {
    /// Ends only this original observation lease without publishing or altering content evidence.
    fn drop(&mut self) {
        let _ = self._lease.unlock();
    }
}
