//! Member-owned original-content storage; local byte proof never grants application or quorum authority.

mod close_queue;
mod copy;
mod layout;
mod pins;
mod producers;
mod reader;
mod time;
pub use close_queue::ReplicaSnapshotCloseStatus;
pub use copy::{ReplicaContentCopyStatus, ReplicaContentReceipt};
pub use pins::ReplicaContentReadLease;
pub(crate) use producers::{ProducerAdmission, ProducerClock, ProducerPermit};
pub use producers::{
    ReplicaContentProducerKind, ReplicaContentProducerSource, ReplicaContentProducerStatus,
};
pub use reader::{ReplicaContentReader, ReplicaContentReaderReceipt, ReplicaContentReaderStatus};
pub(crate) use time::ContentBudget;
pub use time::ContentProcessEndObservation;
#[cfg(test)]
mod tests;

use crate::ArtifactContentKey;
use rusqlite::{Connection, params};
use serde::{Deserialize, Serialize};
use std::fs::File;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};

/// Explicit member-local content authority, separate from both application CAS and Raft metadata.
pub const REPLICA_CONTENT_STORAGE_SCHEMA: &str = "roboguide.replica-content-storage/v0.1";
/// Explicit reader-scope generation; original backend IDs remain unchanged across a drained upgrade.
pub const REPLICA_CONTENT_LAYOUT_SCHEMA: &str = "roboguide.replica-content-layout/v0.2";
/// Independent opt-in original producer journal, fenced from original copy/reader-only writers.
pub const REPLICA_CONTENT_PRODUCER_LAYOUT_SCHEMA: &str = "roboguide.replica-content-layout/v0.3";

/// Explicit storage interpretation; old maintenance entry points never acquire new producer authority.
pub(super) enum ContentOpenMode {
    /// Original copy/reader generation only.
    Reader,
    /// Explicit original v1 to reader v2 transition only.
    ReaderUpgrade,
    /// Already accepted complete producer journal only.
    Producers,
    /// Explicit fully drained reader v2 to producer v3 transition.
    ProducerUpgrade,
}
/// Bound on retained reference IDs; exhaustion never discards original ownership.
const MAX_REFERENCES: usize = 65_536;

/// Original software storage identity, never a hardware, Node or user-consumption identity.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReplicaContentIdentity {
    /// Exact storage interpretation, fenced from ordinary Artifact constructors.
    pub schema: String,
    /// Actual original configured infrastructure cluster/profile.
    pub profile_identity: String,
    /// Actual configured infrastructure member, not a robot selector.
    pub member: u64,
    /// Actual kernel-random software backend identity, fixed before content effects.
    pub store_owner: String,
    /// Original directory device, retained as local provenance.
    pub root_device: u64,
    /// Original actual directory inode; relocation requires explicit verified restore.
    pub root_inode: u64,
    /// Original actual metadata SQL device, independently guarded against hard-link writers.
    pub database_device: u64,
    /// Original actual metadata inode, never silently replaced on open.
    pub database_inode: u64,
}

/// Independent original producers retain their own pins over identical content.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum ReplicaContentReferenceKind {
    /// Original inbound copy intent precedes any source byte read or staging write.
    Receive,
    /// Accepted log history retains its own original inputs.
    Log,
    /// Exact published/current Raft Snapshot retains its complete original inputs.
    Snapshot,
    /// Original actual read/stream lifetime remains independent of Snapshot replacement.
    Reader,
    /// Current accepted complete view keeps its independently needed content.
    View,
}

/// Complete original local pin; this preserves bytes and grants no semantic read/use permission.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReplicaContentReference {
    /// Immutable original producer operation identity.
    pub reference_id: String,
    /// Exact independent producer kind.
    pub kind: ReplicaContentReferenceKind,
    /// Full original Artifact identity; a receiving owner never substitutes for it.
    pub content: ArtifactContentKey,
    /// Complete original producer/command identity, independently of readable labels.
    pub authority_digest: String,
    /// Fixed original generation; re-acquisition never revives a retired row.
    pub generation: u64,
}

/// Complete persisted local pin transition; release history remains immutable.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct ReferenceRecord {
    /// Original complete source retained after release.
    reference: ReplicaContentReference,
    /// Independent current ownership, never inferred from producer age or elapsed time.
    active: bool,
    /// Exact original release proof; absent while current ownership remains unresolved.
    release_digest: Option<String>,
}

/// Shared actual root/SQLite writer ownership; every managed reader/clone retains it.
struct ContentInner {
    /// Actual original root closure queue, shared by every clone and bounded by retained reader rows.
    snapshot_closes: Arc<close_queue::SnapshotCloseQueue>,
    /// Actual original root, never a discovered device path.
    root: PathBuf,
    /// Actual immutable owner and physical local backing identities.
    identity: ReplicaContentIdentity,
    /// Serialized durable metadata; payloads remain outside State/Raft authority checkpoints.
    connection: Mutex<Option<Connection>>,
    /// Actual original root lease, independently released after the final managed clone.
    writer: File,
    /// Actual metadata-inode lease; a hard-link alias cannot create another writer.
    database: File,
    /// Exact admitted local format; ordinary APIs cannot reinterpret a live connection as another profile.
    producers: bool,
}

impl Drop for ContentInner {
    /// Ends only this actual storage ownership; retained Unknown and pins remain on disk.
    fn drop(&mut self) {
        let connection = self
            .connection
            .get_mut()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        if let Some(connection) = connection.take()
            && let Err((connection, _)) = connection.close()
        {
            drop(connection);
        }
        let _ = self.database.unlock();
        let _ = self.writer.unlock();
    }
}

/// Original member-local content store; constructing a handle is not a content/quorum receipt.
#[derive(Clone)]
pub struct ReplicaContentStore {
    /// All actual managed readers and producers share this same original lifetime.
    inner: Arc<ContentInner>,
}

impl ReplicaContentStore {
    /// Initializes only a genuinely empty declared root; interrupted/existing authority is not replaced.
    pub fn create(root: &Path, profile_identity: &str, member: u64) -> Result<Self, String> {
        layout::create(root, profile_identity, member, false)
    }

    /// Requires the complete original root, database and mandatory heads before any observation.
    pub fn open(root: &Path, profile_identity: &str, member: u64) -> Result<Self, String> {
        layout::open(root, profile_identity, member, ContentOpenMode::Reader)
    }

    /// Explicitly drains old writers and fences the reader-scope generation without rewriting history.
    pub fn upgrade_reader_scopes(
        root: &Path,
        profile_identity: &str,
        member: u64,
    ) -> Result<Self, String> {
        layout::open(
            root,
            profile_identity,
            member,
            ContentOpenMode::ReaderUpgrade,
        )
    }

    /// Creates only a fresh explicitly selected original producer journal, separate from old reader profiles.
    pub fn create_with_producers(
        root: &Path,
        profile_identity: &str,
        member: u64,
    ) -> Result<Self, String> {
        layout::create(root, profile_identity, member, true)
    }

    /// Opens only the complete original producer profile; missing journals never become empty authority.
    pub fn open_with_producers(
        root: &Path,
        profile_identity: &str,
        member: u64,
    ) -> Result<Self, String> {
        layout::open(root, profile_identity, member, ContentOpenMode::Producers)
    }

    /// Explicitly drains the original v2 writers and retains all legacy raw references as unavailable history.
    pub fn upgrade_producer_journal(
        root: &Path,
        profile_identity: &str,
        member: u64,
    ) -> Result<Self, String> {
        layout::open(
            root,
            profile_identity,
            member,
            ContentOpenMode::ProducerUpgrade,
        )
    }

    /// Validates only this handle's original admitted SQL generation before any observation or mutation.
    fn validate_connection(&self, connection: &Connection) -> Result<(), String> {
        if self.inner.producers {
            layout::validate_version(connection, self.identity(), 3)
        } else {
            layout::validate(connection, self.identity())
        }
    }

    /// Reports only the actual opened software store; no availability or device fact is created.
    pub fn identity(&self) -> &ReplicaContentIdentity {
        &self.inner.identity
    }

    /// Reports the actual owned storage root without granting file or semantic-consumer authority.
    pub fn root(&self) -> &Path {
        &self.inner.root
    }

    /// Durably acquires one exact original pin before a producer may perform content effects.
    /// Existing released pins remain released; changed source/generation never borrows an old ID.
    pub fn acquire_reference(&self, reference: &ReplicaContentReference) -> Result<bool, String> {
        validate_reference(reference)?;
        if self.inner.producers
            && matches!(
                reference.kind,
                ReplicaContentReferenceKind::Log
                    | ReplicaContentReferenceKind::View
                    | ReplicaContentReferenceKind::Snapshot
            )
        {
            return Err(
                "original managed producer pins require their complete intent transaction".into(),
            );
        }
        if matches!(
            reference.kind,
            ReplicaContentReferenceKind::Receive | ReplicaContentReferenceKind::Reader
        ) {
            return Err(
                "receive/reader pins require the actual original operation transaction".into(),
            );
        }
        let mut guard = self
            .inner
            .connection
            .lock()
            .map_err(|error| error.to_string())?;
        let connection = guard
            .as_mut()
            .ok_or("original content connection is closed")?;
        self.validate_connection(connection)?;
        let transaction = connection
            .transaction()
            .map_err(|error| error.to_string())?;
        let previous = reference_record(&transaction, &reference.reference_id)?;
        if let Some(previous) = previous {
            if previous.reference != *reference {
                return Err(
                    "original content reference ID belongs to another immutable source".into(),
                );
            }
            return Ok(previous.active);
        }
        let mut heads = layout::head(&transaction, "reference_ids")?;
        if heads.len() >= MAX_REFERENCES {
            return Err("original content reference history reached its fixed bound".into());
        }
        let record = ReferenceRecord {
            reference: reference.clone(),
            active: true,
            release_digest: None,
        };
        transaction
            .execute(
                "INSERT INTO content_references(reference_id,record) VALUES (?1,?2)",
                params![
                    reference.reference_id,
                    serde_json::to_string(&record).map_err(|error| error.to_string())?
                ],
            )
            .map_err(|error| error.to_string())?;
        heads.insert(reference.reference_id.clone());
        layout::write_head(&transaction, "reference_ids", &heads)?;
        layout::within_budget(&transaction)?;
        transaction.commit().map_err(|error| error.to_string())?;
        Ok(true)
    }

    /// Retires only the complete original reference with its original immutable producer proof.
    /// This trusted storage primitive performs no content unlink and grants no remote permission.
    pub fn release_reference(
        &self,
        reference: &ReplicaContentReference,
        release_digest: &str,
    ) -> Result<(), String> {
        validate_reference(reference)?;
        if self.inner.producers
            && matches!(
                reference.kind,
                ReplicaContentReferenceKind::Log
                    | ReplicaContentReferenceKind::View
                    | ReplicaContentReferenceKind::Snapshot
            )
        {
            return Err(
                "original producer release requires its own publication/retirement journal".into(),
            );
        }
        if !super::content::hex_digest(release_digest) {
            return Err("original content release lacks its exact proof digest".into());
        }
        let mut guard = self
            .inner
            .connection
            .lock()
            .map_err(|error| error.to_string())?;
        let connection = guard
            .as_mut()
            .ok_or("original content connection is closed")?;
        self.validate_connection(connection)?;
        let transaction = connection
            .transaction()
            .map_err(|error| error.to_string())?;
        let mut record = reference_record(&transaction, &reference.reference_id)?
            .ok_or("original content reference is unavailable")?;
        if record.reference != *reference
            || record
                .release_digest
                .as_deref()
                .is_some_and(|original| original != release_digest)
        {
            return Err("original content reference release changed its source/proof".into());
        }
        if !record.active {
            return Ok(());
        }
        copy::release_allowed(&transaction, reference)?;
        if reference.kind == ReplicaContentReferenceKind::Reader {
            return Err(
                "reader release requires actual original handle close or process-end observation"
                    .into(),
            );
        }
        record.active = false;
        record.release_digest = Some(release_digest.into());
        transaction
            .execute(
                "UPDATE content_references SET record=?2 WHERE reference_id=?1",
                params![
                    reference.reference_id,
                    serde_json::to_string(&record).map_err(|error| error.to_string())?
                ],
            )
            .map_err(|error| error.to_string())?;
        layout::within_budget(&transaction)?;
        transaction.commit().map_err(|error| error.to_string())?;
        Ok(())
    }

    /// Reads independent original owners without releasing or renewing any producer's pin.
    pub fn active_references(
        &self,
        content: &ArtifactContentKey,
    ) -> Result<Vec<ReplicaContentReference>, String> {
        content.validate()?;
        let guard = self
            .inner
            .connection
            .lock()
            .map_err(|error| error.to_string())?;
        let connection = guard
            .as_ref()
            .ok_or("original content connection is closed")?;
        self.validate_connection(connection)?;
        let mut result = Vec::new();
        for id in layout::head(connection, "reference_ids")? {
            let record = reference_record(connection, &id)?
                .ok_or("original content reference head lost its row")?;
            if record.active && record.reference.content == *content {
                result.push(record.reference);
            }
        }
        Ok(result)
    }
}

/// Checks the complete immutable pin independently of future transport/application adapters.
fn validate_reference(reference: &ReplicaContentReference) -> Result<(), String> {
    reference.content.validate()?;
    if reference.reference_id.trim().is_empty()
        || reference.reference_id.len() > 512
        || reference.generation != 1
        || !super::content::hex_digest(&reference.authority_digest)
    {
        return Err("invalid original content reference source/generation".into());
    }
    Ok(())
}

/// Reads one bounded complete original record; missing rows never become an empty active set.
fn reference_record(connection: &Connection, id: &str) -> Result<Option<ReferenceRecord>, String> {
    use rusqlite::OptionalExtension;
    let raw: Option<String> = connection
        .query_row(
            "SELECT record FROM content_references WHERE reference_id=?",
            [id],
            |row| row.get(0),
        )
        .optional()
        .map_err(|error| error.to_string())?;
    raw.map(|raw| {
        if raw.len() > 4096 {
            return Err("original content reference exceeds its bound".into());
        }
        let value: ReferenceRecord =
            serde_json::from_str(&raw).map_err(|error| error.to_string())?;
        validate_reference(&value.reference)?;
        if value.reference.reference_id != id
            || value.active != value.release_digest.is_none()
            || value
                .release_digest
                .as_deref()
                .is_some_and(|value| !super::content::hex_digest(value))
        {
            return Err("original content reference state/source/proof conflicts".into());
        }
        Ok(value)
    })
    .transpose()
}
