//! Actual scoped prepared byte sources; these capabilities never publish CAS or grant semantic use.

use crate::path_validation::{io_error, open_regular_file, unsafe_path_error};
use crate::{ArtifactStoreError, ArtifactUpload, FileSystemArtifactStore, normalize_digest};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::fs::File;
use std::io::{self, Read};
#[cfg(unix)]
use std::os::unix::fs::{FileExt, MetadataExt};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Weak};

/// Maximum actual unended prepared FD scopes per original writer, including revoked live readers.
const MAX_PREPARED_SOURCES: usize = 128;
/// Actual source work remains bounded independently of the caller's output buffer size.
const READ_CHUNK: usize = 64 * 1024;

#[cfg(test)]
#[path = "prepared_source_tests.rs"]
mod tests;

/// Original actual immutable FD source, retained until every guard/reader really ends.
#[derive(Debug)]
pub(crate) struct PreparedSourceState {
    /// Original exact uploader, never replaced with another caller-selected source.
    upload_id: String,
    /// Original complete actual payload digest.
    digest: String,
    /// Original exact staged byte count.
    size: u64,
    /// Actual readonly original FD; independent read_at positions never rewind the uploader.
    file: File,
    /// Original actual file device/inode, separate from physical equipment identity.
    identity: (u64, u64),
    /// Irreversible original scoped read revocation; reader closure remains independent.
    revoked: AtomicBool,
}

/// Unique original live source guard; serialized data and process restart cannot reconstruct it.
pub struct PreparedArtifactSourceGuard {
    /// Actual original capability, never an Arc clone exposed as a new source grant.
    state: Arc<PreparedSourceState>,
    /// Retains the original actual writer until this guard and every reader have ended.
    _store: FileSystemArtifactStore,
}

impl Drop for PreparedArtifactSourceGuard {
    /// Revokes reads without announcing actual FD closure or dropping another live reader's freeze.
    fn drop(&mut self) {
        self.state.revoked.store(true, Ordering::SeqCst);
    }
}

/// Distinct actual source backing; public finalized CAS lookup keeps its original behavior.
enum SourceBacking {
    /// An actually finalized opened immutable file.
    Finalized(File),
    /// One actual registered original prepared source.
    Prepared(Arc<PreparedSourceState>),
}

/// Actual bounded owned source reader, retaining original writer ownership through FD closure.
pub struct ArtifactReplicationSource {
    /// Actual FD lifetime ends before the final store lease field is released.
    backing: SourceBacking,
    /// Actual original managed writer retained independently of the caller's Store handle.
    store: FileSystemArtifactStore,
    /// Exact original source hash, not a new manifest or owner alias.
    digest: String,
    /// Exact original required byte count, including valid empty sources.
    size: u64,
    /// Independent original reader position, never shared with the uploader or another reader.
    position: u64,
}

impl FileSystemArtifactStore {
    /// Rejects reuse of an uploader while its original actual source FD/readers remain retained.
    pub(crate) fn require_unfrozen_upload(&self, id: &str) -> Result<(), ArtifactStoreError> {
        let registry = self._writer_lock.prepared_sources.lock().map_err(|_| {
            unsafe_path_error(
                "inspect prepared source",
                "original source registry is unavailable",
            )
        })?;
        if registry.get(id).and_then(Weak::upgrade).is_some() {
            return Err(ArtifactStoreError::UploadSourceActive {
                upload_id: id.into(),
            });
        }
        Ok(())
    }

    /// Opens only actual finalized bytes or a live explicitly scoped prepared source owned by this writer.
    /// This internal data-source API does not change public CAS availability, manifests or permissions.
    pub fn open_replication_source(
        &self,
        digest: &str,
        size: u64,
    ) -> Result<ArtifactReplicationSource, ArtifactStoreError> {
        let digest = normalize_digest(digest)?;
        self.writer_identity()?;
        self.require_live_content(&digest)?;
        let backing = match self.open_artifact(&digest) {
            Ok(file) => {
                let actual = file
                    .metadata()
                    .map_err(|error| io_error("stat replication source", error))?
                    .len();
                if actual != size {
                    return Err(ArtifactStoreError::SizeMismatch {
                        expected: size,
                        actual,
                    });
                }
                SourceBacking::Finalized(file)
            }
            Err(ArtifactStoreError::ArtifactNotFound { .. }) => {
                let registry = self._writer_lock.prepared_sources.lock().map_err(|_| {
                    unsafe_path_error(
                        "open prepared source",
                        "original source registry is unavailable",
                    )
                })?;
                let state = registry
                    .iter()
                    .filter_map(|(id, state)| {
                        state.upgrade().filter(|state| state.upload_id == *id)
                    })
                    .find(|state| {
                        state.digest == digest
                            && state.size == size
                            && !state.revoked.load(Ordering::SeqCst)
                    })
                    .ok_or_else(|| ArtifactStoreError::ArtifactNotFound {
                        digest: digest.clone(),
                    })?;
                SourceBacking::Prepared(state)
            }
            Err(error) => return Err(error),
        };
        Ok(ArtifactReplicationSource {
            backing,
            store: self.clone(),
            digest,
            size,
            position: 0,
        })
    }
}

impl ArtifactUpload {
    /// Fsyncs exact original bytes, freezes append and registers one actual original scoped read source.
    pub fn prepared_replication_source(
        &mut self,
        digest: &str,
        size: u64,
    ) -> Result<PreparedArtifactSourceGuard, ArtifactStoreError> {
        let writer = self.store._writer_lock.clone();
        let mut registry = writer.prepared_sources.lock().map_err(|_| {
            unsafe_path_error(
                "register prepared source",
                "original source registry is unavailable",
            )
        })?;
        if registry
            .get(&self.upload_id)
            .and_then(Weak::upgrade)
            .is_some()
        {
            return Err(ArtifactStoreError::UploadSourceActive {
                upload_id: self.upload_id.clone(),
            });
        }
        self.prepare_finalize(digest, size)?;
        let digest = normalize_digest(digest)?;
        self.store.writer_identity()?;
        let file = open_regular_file(&self.staging_path, "open actual prepared source")?;
        let actual = file
            .metadata()
            .map_err(|error| io_error("stat actual prepared source", error))?;
        let original = self
            .file
            .as_ref()
            .ok_or_else(|| ArtifactStoreError::UploadClosed {
                upload_id: self.upload_id.clone(),
            })?
            .metadata()
            .map_err(|error| io_error("stat original uploader FD", error))?;
        #[cfg(unix)]
        let identity = {
            if (actual.dev(), actual.ino()) != (original.dev(), original.ino())
                || actual.len() != size
            {
                return Err(unsafe_path_error(
                    "register prepared source",
                    "actual original file identity or size changed",
                ));
            }
            (actual.dev(), actual.ino())
        };
        #[cfg(not(unix))]
        let identity = {
            return Err(unsafe_path_error(
                "register prepared source",
                "actual Unix FD identity is required",
            ));
        };
        let mut hasher = Sha256::new();
        let mut offset = 0;
        let mut chunk = [0; READ_CHUNK];
        while offset < size {
            #[cfg(unix)]
            let count = file
                .read_at(&mut chunk, offset)
                .map_err(|error| io_error("hash actual prepared source", error))?;
            #[cfg(not(unix))]
            let count = 0;
            if count == 0 {
                return Err(unsafe_path_error(
                    "hash actual prepared source",
                    "original source ended before its declared size",
                ));
            }
            hasher.update(&chunk[..count]);
            offset += count as u64;
        }
        let actual_digest = crate::digest::digest_hasher(&hasher);
        if actual_digest != digest {
            return Err(ArtifactStoreError::DigestMismatch {
                expected: digest,
                actual: actual_digest,
            });
        }
        if file
            .metadata()
            .map_err(|error| io_error("recheck actual prepared source", error))?
            .len()
            != size
        {
            return Err(unsafe_path_error(
                "register prepared source",
                "actual source size changed during verification",
            ));
        }
        registry.retain(|_, state| state.strong_count() != 0);
        if registry.len() >= MAX_PREPARED_SOURCES {
            return Err(unsafe_path_error(
                "register prepared source",
                "original prepared source capacity is exhausted",
            ));
        }
        let state = Arc::new(PreparedSourceState {
            upload_id: self.upload_id.clone(),
            digest,
            size,
            file,
            identity,
            revoked: AtomicBool::new(false),
        });
        self.prepared_source = Arc::downgrade(&state);
        registry.insert(self.upload_id.clone(), Arc::downgrade(&state));
        Ok(PreparedArtifactSourceGuard {
            state,
            _store: self.store.clone(),
        })
    }

    /// Refuses a different finalization while any actual original guard or reader still freezes bytes.
    pub(crate) fn require_prepared_finalize(
        &self,
        sources: &BTreeMap<String, Weak<PreparedSourceState>>,
        digest: &str,
        size: u64,
    ) -> Result<(), ArtifactStoreError> {
        let Some(state) = sources.get(&self.upload_id).and_then(Weak::upgrade) else {
            return Ok(());
        };
        if state.digest != digest || state.size != size {
            return Err(unsafe_path_error(
                "finalize prepared source",
                "finalization differs from the frozen original source",
            ));
        }
        let named = std::fs::symlink_metadata(&self.staging_path)
            .map_err(|error| io_error("inspect original prepared staging path", error))?;
        let actual = self
            .file
            .as_ref()
            .ok_or_else(|| ArtifactStoreError::UploadClosed {
                upload_id: self.upload_id.clone(),
            })?
            .metadata()
            .map_err(|error| io_error("inspect original prepared upload FD", error))?;
        #[cfg(unix)]
        if !named.file_type().is_file()
            || (named.dev(), named.ino()) != state.identity
            || (actual.dev(), actual.ino()) != state.identity
            || actual.len() != state.size
        {
            return Err(unsafe_path_error(
                "finalize prepared source",
                "actual original staging file was replaced or resized",
            ));
        }
        Ok(())
    }

    /// Revokes only this original source; actual outstanding reader FDs retain their own lifetimes.
    pub(crate) fn revoke_prepared_source(&self) {
        if let Some(state) = self.prepared_source.upgrade() {
            state.revoked.store(true, Ordering::SeqCst);
        }
        if let Ok(registry) = self.store._writer_lock.prepared_sources.lock()
            && let Some(state) = registry.get(&self.upload_id).and_then(Weak::upgrade)
        {
            state.revoked.store(true, Ordering::SeqCst);
        }
    }
}

impl Read for ArtifactReplicationSource {
    /// Reads one bounded actual chunk under the original source guard/writer/file/GC fences.
    fn read(&mut self, output: &mut [u8]) -> io::Result<usize> {
        let checked = (|| -> Result<usize, ArtifactStoreError> {
            self.store.writer_identity()?;
            self.store.require_live_content(&self.digest)?;
            let limit = output
                .len()
                .min(READ_CHUNK)
                .min(self.size.saturating_sub(self.position) as usize);
            let count = match &mut self.backing {
                SourceBacking::Finalized(file) => file
                    .read(&mut output[..limit])
                    .map_err(|error| io_error("read finalized replication source", error))?,
                SourceBacking::Prepared(state) => {
                    if state.revoked.load(Ordering::SeqCst) {
                        return Err(unsafe_path_error(
                            "read prepared source",
                            "original source was revoked",
                        ));
                    }
                    let actual = state
                        .file
                        .metadata()
                        .map_err(|error| io_error("stat prepared source FD", error))?;
                    #[cfg(unix)]
                    if (actual.dev(), actual.ino()) != state.identity || actual.len() != state.size
                    {
                        return Err(unsafe_path_error(
                            "read prepared source",
                            "original source file changed",
                        ));
                    }
                    #[cfg(unix)]
                    let count = state
                        .file
                        .read_at(&mut output[..limit], self.position)
                        .map_err(|error| io_error("read original prepared source", error))?;
                    #[cfg(not(unix))]
                    let count = {
                        return Err(unsafe_path_error(
                            "read prepared source",
                            "actual Unix FD reads required",
                        ));
                    };
                    if state.revoked.load(Ordering::SeqCst) {
                        return Err(unsafe_path_error(
                            "read prepared source",
                            "original source was revoked during read",
                        ));
                    }
                    count
                }
            };
            if limit > 0 && count == 0 {
                return Err(unsafe_path_error(
                    "read replication source",
                    "original source ended before its exact size",
                ));
            }
            self.position += count as u64;
            Ok(count)
        })();
        checked.map_err(io::Error::other)
    }
}
