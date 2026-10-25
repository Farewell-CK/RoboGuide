//! Actual original application CAS adapter for online replicated Archive metadata.

use artifact_store::FileSystemArtifactStore;
use sha2::{Digest, Sha256};
use std::sync::Arc;

/// Owned original CAS and current actual request witness, retained through the final SQL decision.
pub(crate) struct Source {
    /// Actual source writer; cloning retains the original managed root lease.
    store: FileSystemArtifactStore,
    /// Actual current original authorization, never a body-supplied readiness flag.
    permission: Arc<dyn Fn() -> Result<(), String> + Send + Sync>,
}

impl Source {
    /// Connects the existing source without creating another owner or copying historical payloads.
    pub(crate) fn new(
        store: FileSystemArtifactStore,
        permission: Arc<dyn Fn() -> Result<(), String> + Send + Sync>,
    ) -> Self {
        Self { store, permission }
    }
}

impl state::CoordinatedArchiveSink for Source {
    /// Rechecks the original live writer and current actual caller.
    fn authorize(&self) -> Result<(), String> {
        self.store.writer_identity().map_err(|e| e.to_string())?;
        (self.permission)()
    }
    /// Writes only the exact first-admitted metadata package to actual fsynced CAS storage.
    fn publish(
        &self,
        intent: &state::CoordinatedArchiveIntent,
        bytes: &[u8],
    ) -> Result<(), String> {
        self.authorize()?;
        intent.require_budget()?;
        let writer = self.store.writer_identity().map_err(|e| e.to_string())?;
        if writer.owner_id != intent.package.source_owner
            || bytes.len() as u64 != intent.package.byte_size
            || format!("sha256:{:x}", Sha256::digest(bytes)) != intent.package.content_digest
        {
            return Err("original Archive source/package changed before publication".into());
        }
        let id = format!(
            "archive-{:x}",
            Sha256::digest(state::coordinated_archive_bytes(&(
                intent.authority_id.as_str(),
                intent.request.command_id.as_str(),
                intent.package.content_digest.as_str()
            ))?)
        );
        let mut upload = self.store.begin_upload(id).map_err(|e| e.to_string())?;
        for chunk in bytes.chunks(1024 * 1024) {
            intent.require_budget()?;
            self.authorize()?;
            upload.write_chunk(chunk).map_err(|e| e.to_string())?;
        }
        intent.require_budget()?;
        self.authorize()?;
        upload
            .finalize(&intent.package.content_digest, intent.package.byte_size)
            .map_err(|e| e.to_string())?;
        self.verify(&intent.package)
    }
    /// Independently reads complete finalized bytes under the same actual source owner.
    fn verify(&self, key: &state::ArchiveContentKey) -> Result<(), String> {
        self.authorize()?;
        if self.store.owner_id() != Some(key.source_owner.as_str()) {
            return Err("original Archive CAS owner differs".into());
        }
        self.store
            .verify_artifact(&key.content_digest, key.byte_size)
            .map_err(|e| e.to_string())?;
        self.authorize()
    }
}
