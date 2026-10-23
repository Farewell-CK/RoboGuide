//! FULL-synchronous SQLite Raft vote/log/state storage and bounded exact snapshot transfer.

// OpenRaft requires its concrete StorageError at every storage boundary; retain typed upstream failures.
#![allow(clippy::result_large_err)]

#[path = "store_adoption.rs"]
mod adoption;
#[path = "store_apply.rs"]
mod apply;
#[path = "store_content.rs"]
mod content;
#[path = "store_network.rs"]
mod network;
#[path = "store_producers.rs"]
mod producers;
#[path = "store_snapshot.rs"]
mod snapshot;
#[path = "store_work.rs"]
mod work;

pub use adoption::ReplicaContentParentAdoptionStatus;

use crate::*;
use openraft::storage::{LogFlushed, RaftLogStorage, RaftStateMachine};
use openraft::{
    Entry, EntryPayload, LogId, LogState, RaftLogReader, RaftSnapshotBuilder, Snapshot,
    SnapshotMeta, StorageError, StorageIOError, StoredMembership, Vote,
};
use rusqlite::{Connection, OptionalExtension, TransactionBehavior, params};
use serde::de::DeserializeOwned;
use sha2::{Digest as _, Sha256};
use std::fs::File;
use std::ops::{Bound, RangeBounds};
use std::sync::Mutex;

/// Bound on accepted historical metadata, separate from opaque Artifact/Memory content.
const MAX_HISTORY_BYTES: u64 = 64 * 1024 * 1024;
/// Bound on one complete replica snapshot including escaped checkpoint/history metadata.
const MAX_SNAPSHOT_BYTES: usize = 256 * 1024 * 1024;
/// Storage format version whose table views fence pre-format writers.
const REPLICA_STORAGE_VERSION: i64 = 3;
/// Closed storage marker persisted beside the complete consensus tables.
const REPLICA_STORAGE_SCHEMA: &str = "roboguide.replica-storage/v0.3";
/// Explicit complete-content generation; metadata-only binaries refuse it even at zero operations.
const CONTENT_STORAGE_VERSION: i64 = 4;
/// Closed original-recovery/content interpretation, separate from the unchanged domain wrapper.
const CONTENT_STORAGE_SCHEMA: &str = "roboguide.replica-storage/v0.4";
/// Explicit original-producer generation; missing local source heads never mean no responsibility.
const PRODUCER_STORAGE_VERSION: i64 = 5;
/// Member-local producer-head interpretation, independent of source Snapshot payload metadata.
const PRODUCER_STORAGE_SCHEMA: &str = "roboguide.replica-storage/v0.5";
/// Explicit original-parent adoption requires complete retained member-local command history.
const ADOPTED_STORAGE_VERSION: i64 = 6;
/// Parent-local adoption journal interpretation, never imported through a Raft Snapshot.
const ADOPTED_STORAGE_SCHEMA: &str = "roboguide.replica-storage/v0.6";
/// Explicit network/member-bound generation; old direct storage cannot acquire it implicitly.
const NETWORK_CONTENT_VERSION: i64 = 7;
/// Closed parent interpretation for actual consensus-bound streaming roots.
const NETWORK_CONTENT_SCHEMA: &str = "roboguide.replica-storage/v0.7";
/// Mandatory original local network-generation marker, independently of an empty membership.
const NETWORK_CONTENT_KEY: &str = "network_content_membership_schema";
/// Explicit original-child generation; member-only binaries cannot acquire its local journal.
pub(crate) const CONTROLLER_CONTENT_VERSION: i64 = 8;
/// Closed original child journal interpretation, independent of Raft/domain codecs.
pub(crate) const CONTROLLER_CONTENT_SCHEMA: &str = "roboguide.replica-storage/v0.8";

/// Explicit local authority generation, never inferred from an existing file or a new table.
#[derive(Clone, Copy, PartialEq, Eq)]
enum NetworkContentMode {
    /// Original metadata or local-only content constructor.
    None,
    /// Original core network parent7.
    Member,
    /// Explicit owned-child parent8.
    Controller,
}
/// Legacy names retained only as read-only views after the atomic format migration.
const LEGACY_TABLES: [&str; 5] = [
    "replica_meta",
    "replica_commands",
    "replica_log",
    "replica_events",
    "replica_snapshot",
];
/// Current table names used by every writer in this binary.
const STORAGE_TABLES: [&str; 5] = [
    "replica_v1_meta",
    "replica_v1_commands",
    "replica_v1_log",
    "replica_v1_events",
    "replica_v1_snapshot",
];
/// Immutable legacy operation value retained only for historical inspection.
const LEGACY_LEADER_TRANSFER_META_KEY: &str = "leader_transfer";
/// Version-fenced member-local operation history, never distributed by a Raft snapshot.
const LEADER_TRANSFER_HISTORY_META_KEY: &str = "leader_transfers_v2";
/// Maximum number of original operations; capacity never discards old IDs.
const MAX_LEADER_TRANSFERS: usize = 512;

/// Complete quorum-applied evidence for inactive materialization, not a second live Control map.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReplicaView {
    /// Highest accepted application revision.
    pub revision: u64,
    /// Exact complete accepted authority snapshot.
    pub checkpoint: Option<state::PersistedCheckpoint>,
    /// Immutable retained event metadata tail; a complete checkpoint covers any compacted prefix.
    pub events: Vec<state::PersistedEvent>,
    /// Complete retained command evidence, independent of domain-event compaction.
    #[serde(default)]
    pub commands: Vec<state::ControlCommandJournalEntry>,
    /// Optional durable prefix anchor when old metadata rows were compacted.
    #[serde(default)]
    pub history_anchor: Option<HistoryAnchor>,
    /// Complete v0.4 auxiliary state; legacy metadata-only snapshots never invent this authority.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub content: Option<AcceptedContentState>,
}

/// Verifiable identity of the compacted application-history prefix.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HistoryAnchor {
    /// Last event sequence represented by the complete checkpoint but no longer retained as a row.
    pub base_sequence: u64,
    /// Digest of the compacted prefix chain, never a replacement for the checkpoint.
    pub base_digest: String,
    /// Checkpoint digest captured when this anchor was written.
    pub checkpoint_digest: String,
    /// Versioned anchor codec.
    pub schema: String,
}

/// Versioned codec marker for a retained event-history prefix anchor.
const HISTORY_ANCHOR_SCHEMA: &str = "roboguide.replica-history-anchor/v1";

/// Small accepted-application identity used to resolve a lost Commit response.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AcceptedApplicationIdentity {
    /// Latest accepted application revision.
    pub revision: u64,
    /// Complete checkpoint event sequence represented by the accepted projection.
    pub event_sequence: u64,
    /// Digest of the complete accepted checkpoint, when one exists.
    pub checkpoint_digest: Option<String>,
}

impl ReplicaView {
    /// Checks exact declared schema/profile and retained data before a composition opens a target writer.
    /// Passing validation does not establish a quorum, ownership or current content availability.
    pub fn validate_for_materialization(
        &self,
        application_schema: &str,
        profile: &str,
    ) -> Result<(), String> {
        self.validate(application_schema, profile)
            .map_err(|error| error.to_string())
    }

    /// Validates bounded retained history and exact codecs on reopen and before snapshot installation.
    pub(crate) fn validate(
        &self,
        application_schema: &str,
        profile: &str,
    ) -> Result<(), StorageError<u64>> {
        match &self.checkpoint {
            Some(checkpoint)
                if checkpoint.schema == application_schema
                    && checkpoint.checkpoint_json.len() <= 64 * 1024 * 1024
                    && self.revision > 0 => {}
            None if self.events.is_empty() && self.revision == 0 => {}
            _ => {
                return Err(io_error(
                    "replica view schema/revision/checkpoint tail differs",
                ));
            }
        }
        let mut bytes = 0_u64;
        if let Some(head) = self.commands.last() {
            head.validate_checkpoint(
                self.checkpoint
                    .as_ref()
                    .ok_or_else(|| io_error("commands lack checkpoint"))?,
            )
            .map_err(io_error)?;
        } else if self.checkpoint.as_ref().is_some_and(|checkpoint| {
            state::checkpoint_requires_authority_journal(&checkpoint.schema)
        }) {
            return Err(io_error("replica snapshot lost required command history"));
        }
        for pair in self.commands.windows(2) {
            if pair[0].authority_revision.checked_add(1) != Some(pair[1].authority_revision) {
                return Err(io_error("replica command history is discontinuous"));
            }
        }
        let command_bytes = serde_json::to_vec(&self.commands).map_err(io_error)?.len();
        if command_bytes > MAX_HISTORY_BYTES as usize {
            return Err(io_error("replica command history exceeds snapshot bounds"));
        }
        let mut identities = std::collections::BTreeSet::new();
        let first_sequence = self.events.first().map_or(0, |event| event.sequence);
        if let Some(anchor) = &self.history_anchor {
            if anchor.schema != HISTORY_ANCHOR_SCHEMA
                || anchor.base_sequence == 0
                || (!self.events.is_empty()
                    && first_sequence != anchor.base_sequence.saturating_add(1))
                || (self.events.is_empty()
                    && self.checkpoint.as_ref().is_some_and(|checkpoint| {
                        anchor.base_sequence != checkpoint.event_sequence
                    }))
                || self
                    .checkpoint
                    .as_ref()
                    .is_none_or(|checkpoint| anchor.base_sequence > checkpoint.event_sequence)
                || anchor.base_digest.trim().is_empty()
                || anchor.checkpoint_digest
                    != self
                        .checkpoint
                        .as_ref()
                        .map(state::checkpoint_identity)
                        .unwrap_or_default()
            {
                return Err(io_error("replica history anchor differs"));
            }
        } else if first_sequence > 1 && self.content.is_none() {
            return Err(io_error(
                "replica history has a missing prefix without an anchor",
            ));
        } else if self.content.is_none()
            && self
                .checkpoint
                .as_ref()
                .is_some_and(|checkpoint| checkpoint.event_sequence > 0 && self.events.is_empty())
        {
            return Err(io_error(
                "replica checkpoint has no retained history anchor",
            ));
        }
        for (index, event) in self.events.iter().enumerate() {
            let expected = if first_sequence == 0 {
                1
            } else {
                first_sequence + index as u64
            };
            if event.sequence != expected
                || !identities.insert(&event.event_id)
                || self
                    .checkpoint
                    .as_ref()
                    .is_none_or(|checkpoint| event.sequence > checkpoint.event_sequence)
            {
                return Err(io_error("replica history identity/sequence differs"));
            }
            state::validate_persisted_event(event).map_err(io_error)?;
            bytes = bytes
                .checked_add(serde_json::to_vec(event).map_err(io_error)?.len() as u64)
                .ok_or_else(|| io_error("replica history size overflow"))?;
            if bytes > MAX_HISTORY_BYTES {
                return Err(io_error("replica history exceeds bounds"));
            }
        }
        if let Some(content) = &self.content {
            if self.history_anchor.is_some()
                || !crate::content::hex_digest(&content.batch_digest)
                || content.candidate.identity().map_err(io_error)? != content.batch_digest
                || content.candidate.schema != CONTENT_APPLICATION_SCHEMA
                || content.candidate.profile_identity != profile
                || content.candidate.batch.checkpoint
                    != *self
                        .checkpoint
                        .as_ref()
                        .ok_or_else(|| io_error("complete original candidate lacks checkpoint"))?
                || content.epoch.term == 0
                || content.epoch.replica_id == 0
                || content.source_writer.inode == 0
            {
                return Err(io_error(
                    "complete original replica view has invalid auxiliary identity/metadata compaction",
                ));
            }
            content.candidate.batch.validate().map_err(io_error)?;
            content
                .recovery
                .validate_for(
                    &self.events,
                    self.checkpoint.as_ref().ok_or_else(|| {
                        io_error("complete original replica view lacks checkpoint")
                    })?,
                )
                .map_err(io_error)?;
            if crate::content_application::required_inputs(
                &content.recovery,
                &self.events,
                &content.origins,
            )
            .map_err(io_error)?
                != content.inputs
            {
                return Err(io_error(
                    "complete original replica view lost exact qualified inputs",
                ));
            }
        }
        Ok(())
    }
}

/// Closed snapshot body preserves log/membership pointers and source identity atomically.
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct StoreSnapshot {
    /// Startup-frozen cluster/profile identity.
    profile: String,
    /// Last quorum-applied log, including blank/membership entries.
    last_applied: Option<LogId<u64>>,
    /// Exact accepted consensus membership.
    membership: StoredMembership<u64, ReplicaMember>,
    /// Exact complete accepted application metadata.
    view: ReplicaView,
    /// Optional v0.3 infrastructure trust; absent in unchanged legacy snapshot bytes.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    certificate_trust: Option<CertificateTrust>,
    /// Legacy snapshot compatibility only; never imported into member-local authority.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    leader_transfer: Option<LeaderTransferRecord>,
}

/// One serialized durable connection implements distinct Raft storage responsibility traits.
#[derive(Clone)]
pub struct SqliteRaftStore {
    /// Atomic SQLite storage shared by log and state-machine traits.
    pub(crate) connection: Arc<Mutex<Connection>>,
    /// Lifetime process writer lease; a second owner cannot fork local consensus history.
    _writer: Arc<ReplicaWriterLease>,
    /// Frozen network/application identity.
    profile: String,
    /// Exact admitted opaque application schema.
    application_schema: String,
    /// Actual member-local original content ownership; absent for unchanged metadata-only stores.
    content: Option<ReplicaContentStore>,
    /// Exact explicitly admitted network generation, never inferred from a stream-capable root alone.
    network_content: bool,
    /// Explicit local original-child journal; false never permits application ingress.
    pub(crate) controller_content: bool,
    /// Actual original queue admission on a managed worker clone; it is never serialized or renewed.
    work_clock: Option<crate::content_store::ProducerClock>,
    /// Bounds actual running plus queued heavy work per original store, retained inside each worker.
    storage_jobs: Arc<tokio::sync::Semaphore>,
    /// Bounds actual small consensus SQL jobs separately from eight heavy producer jobs.
    consensus_jobs: Arc<tokio::sync::Semaphore>,
}

/// Explicit application lease, independent of descriptors briefly inherited by child processes.
pub(crate) struct ReplicaWriterLease {
    /// Actual canonical SQL path, independently inode-checked by original readonly observers.
    path: std::path::PathBuf,
    /// Shared open-file description locked while at least one application Store owns it.
    file: File,
    /// Open description for the actual SQLite inode, closing the hard-link alias gap.
    database: File,
}

impl ReplicaWriterLease {
    /// Samples the actual held original SQL descriptor; no caller path, inode label or PID supplies it.
    pub(crate) fn identity(&self) -> Result<(u64, u64), String> {
        use std::os::unix::fs::MetadataExt;
        let actual = self
            .database
            .metadata()
            .map_err(|error| error.to_string())?;
        if !actual.is_file() {
            return Err("original parent SQL lease is not an actual regular descriptor".into());
        }
        Ok((actual.dev(), actual.ino()))
    }
}

impl Drop for ReplicaWriterLease {
    /// Releases the lock at the final application owner, even if a child inherited a descriptor.
    fn drop(&mut self) {
        let _ = self.database.unlock();
        let _ = self.file.unlock();
    }
}

/// Returns the SQLite schema object kind without interpreting missing authority as an empty store.
fn schema_object_type(connection: &Connection, name: &str) -> Result<Option<String>, String> {
    connection
        .query_row(
            "SELECT type FROM sqlite_master WHERE name=?1",
            [name],
            |row| row.get(0),
        )
        .optional()
        .map_err(|error| error.to_string())
}

/// Creates or atomically migrates the complete Replica table set and its old-writer read-only fence.
fn prepare_storage_format(
    connection: &mut Connection,
    profile: &str,
    application_schema: &str,
    content_identity: Option<&ReplicaContentIdentity>,
    producers: bool,
    adoption: bool,
    network_mode: NetworkContentMode,
) -> Result<(), String> {
    let network_content = network_mode != NetworkContentMode::None;
    let controller_content = network_mode == NetworkContentMode::Controller;
    let version: i64 = connection
        .query_row("PRAGMA user_version", [], |row| row.get(0))
        .map_err(|error| error.to_string())?;
    let marker = schema_object_type(connection, "replica_storage_format")?;
    let target_version = if controller_content {
        CONTROLLER_CONTENT_VERSION
    } else if network_content {
        NETWORK_CONTENT_VERSION
    } else if producers {
        if version == ADOPTED_STORAGE_VERSION {
            ADOPTED_STORAGE_VERSION
        } else {
            PRODUCER_STORAGE_VERSION
        }
    } else if content_identity.is_some() {
        CONTENT_STORAGE_VERSION
    } else {
        REPLICA_STORAGE_VERSION
    };
    if (content_identity.is_some() && version != 0 && version != target_version)
        || (content_identity.is_none()
            && matches!(
                version,
                CONTENT_STORAGE_VERSION
                    | PRODUCER_STORAGE_VERSION
                    | ADOPTED_STORAGE_VERSION
                    | NETWORK_CONTENT_VERSION
                    | CONTROLLER_CONTENT_VERSION
            ))
    {
        return Err("complete-content Replica storage requires explicit original upgrade; metadata-only open/automatic migration refused".into());
    }
    let current = STORAGE_TABLES
        .iter()
        .map(|name| schema_object_type(connection, name))
        .collect::<Result<Vec<_>, _>>()?;
    let legacy = LEGACY_TABLES
        .iter()
        .map(|name| schema_object_type(connection, name))
        .collect::<Result<Vec<_>, _>>()?;
    let triggers: u64 = connection
        .query_row(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger'",
            [],
            |row| row.get(0),
        )
        .map_err(|error| error.to_string())?;
    if triggers != 0 {
        return Err("Replica storage contains unrecognized writer triggers".into());
    }
    if matches!(
        version,
        1 | 2
            | REPLICA_STORAGE_VERSION
            | CONTENT_STORAGE_VERSION
            | PRODUCER_STORAGE_VERSION
            | ADOPTED_STORAGE_VERSION
            | NETWORK_CONTENT_VERSION
            | CONTROLLER_CONTENT_VERSION
    ) && marker.as_deref() == Some("table")
    {
        if current.iter().any(|kind| kind.as_deref() != Some("table"))
            || legacy.iter().any(|kind| kind.as_deref() != Some("view"))
        {
            return Err("Replica storage v1 tables/views are incomplete".into());
        }
        let stored: (i64, String) = connection
            .query_row(
                "SELECT version,schema FROM replica_storage_format",
                [],
                |row| Ok((row.get(0)?, row.get(1)?)),
            )
            .map_err(|error| error.to_string())?;
        let expected_schema = if version == 1 {
            "roboguide.replica-storage/v0.1"
        } else if version == 2 {
            "roboguide.replica-storage/v0.2"
        } else if version == CONTROLLER_CONTENT_VERSION {
            CONTROLLER_CONTENT_SCHEMA
        } else if version == NETWORK_CONTENT_VERSION {
            NETWORK_CONTENT_SCHEMA
        } else if version == ADOPTED_STORAGE_VERSION {
            ADOPTED_STORAGE_SCHEMA
        } else if version == PRODUCER_STORAGE_VERSION {
            PRODUCER_STORAGE_SCHEMA
        } else if version == CONTENT_STORAGE_VERSION {
            CONTENT_STORAGE_SCHEMA
        } else {
            REPLICA_STORAGE_SCHEMA
        };
        if stored != (version, expected_schema.to_string())
            && !(adoption
                && version == CONTENT_STORAGE_VERSION
                && stored == (version, adoption::PENDING_SCHEMA.into()))
        {
            return Err("Replica storage marker differs".into());
        }
        let identity: Option<(String, String)> =
            read_meta(connection, "identity").map_err(|error| error.to_string())?;
        if identity != Some((profile.to_string(), application_schema.to_string())) {
            return Err("stored replica cluster/application identity is absent or differs".into());
        }
        if let Some(expected) = content_identity {
            let original: ReplicaContentIdentity = read_meta(connection, "content_root_identity")
                .map_err(|error| error.to_string())?
                .ok_or("original member-local content root identity is missing")?;
            if original != *expected
                || read_meta::<Option<AcceptedContentState>>(connection, "content_state")
                    .map_err(|error| error.to_string())?
                    .is_none()
            {
                return Err(
                    "original complete-content Replica root/head is absent or changed".into(),
                );
            }
        } else if read_meta::<serde_json::Value>(connection, "content_root_identity")
            .map_err(|error| error.to_string())?
            .is_some()
            || read_meta::<serde_json::Value>(connection, "content_state")
                .map_err(|error| error.to_string())?
                .is_some()
        {
            return Err(
                "metadata-only Replica storage contains hidden complete-content authority".into(),
            );
        }
        let object_count: u64 = connection
            .query_row(
                "SELECT COUNT(*) FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%'",
                [],
                |row| row.get(0),
            )
            .map_err(|error| error.to_string())?;
        if object_count != if controller_content { 12 } else { 11 } {
            return Err("Replica storage contains unknown tables/views".into());
        }
        for (legacy_name, storage_name) in LEGACY_TABLES.iter().zip(STORAGE_TABLES) {
            let sql: String = connection
                .query_row(
                    "SELECT sql FROM sqlite_master WHERE name=?1 AND type='view'",
                    [legacy_name],
                    |row| row.get(0),
                )
                .map_err(|error| error.to_string())?;
            let expected = format!("CREATE VIEW {legacy_name} AS SELECT * FROM {storage_name}");
            if sql.split_whitespace().collect::<Vec<_>>()
                != expected.split_whitespace().collect::<Vec<_>>()
            {
                return Err("Replica storage readonly view differs".into());
            }
        }
        if version < REPLICA_STORAGE_VERSION {
            let legacy: Option<LeaderTransferRecord> =
                read_meta(connection, LEGACY_LEADER_TRANSFER_META_KEY)
                    .map_err(|error| error.to_string())?;
            let originals = if version == 1 {
                legacy.into_iter().collect::<Vec<_>>()
            } else {
                read_meta::<Vec<LeaderTransferRecord>>(connection, LEADER_TRANSFER_HISTORY_META_KEY)
                    .map_err(|error| error.to_string())?
                    .ok_or("original v2 transfer history is absent")?
            };
            let original_history = originals.clone();
            if originals.len() > MAX_LEADER_TRANSFERS {
                return Err("original legacy transfer history exceeds its bound".into());
            }
            let history = originals
                .into_iter()
                .rev()
                .take(1)
                .map(|mut record| {
                    record.state = "unknown".into();
                    record.confirmed_epoch = None;
                    record
                })
                .collect::<Vec<_>>();
            for record in &history {
                validate_leader_transfer(record)?;
            }
            let journal: String = connection
                .query_row("PRAGMA journal_mode=DELETE", [], |row| row.get(0))
                .map_err(|error| format!("Replica v1 connections are not quiescent: {error}"))?;
            if journal != "delete" {
                return Err("Replica v1 connections are not quiescent".into());
            }
            connection
                .pragma_update(None, "synchronous", "FULL")
                .map_err(|error| error.to_string())?;
            let transaction = connection
                .transaction_with_behavior(TransactionBehavior::Exclusive)
                .map_err(|error| error.to_string())?;
            transaction.execute_batch(
                "DROP TABLE replica_storage_format;
                 CREATE TABLE replica_storage_format(version INTEGER PRIMARY KEY CHECK(version=3),schema TEXT NOT NULL);
                 INSERT INTO replica_storage_format VALUES (3,'roboguide.replica-storage/v0.3');
                 PRAGMA user_version=3;"
            ).map_err(|error| error.to_string())?;
            write_meta(
                &transaction,
                "legacy_transfer_history_v2",
                &original_history,
            )
            .map_err(|error| error.to_string())?;
            let legacy_application =
                read_meta::<state::PersistedCheckpoint>(&transaction, "checkpoint")
                    .map_err(|error| error.to_string())?
                    .is_some();
            write_meta(
                &transaction,
                crate::supervision::LEGACY_CONTINUITY_KEY,
                &legacy_application,
            )
            .map_err(|error| error.to_string())?;
            write_meta(&transaction, crate::supervision::SUPERVISION_KEY, &false)
                .map_err(|error| error.to_string())?;
            write_meta(
                &transaction,
                crate::supervision::PROCESS_HISTORY_KEY,
                &Vec::<crate::supervision::ControllerProcessRecord>::new(),
            )
            .map_err(|error| error.to_string())?;
            write_meta(&transaction, LEADER_TRANSFER_HISTORY_META_KEY, &history)
                .map_err(|error| error.to_string())?;
            transaction.commit().map_err(|error| error.to_string())?;
        }
        if producers {
            producers::read_heads(connection).map_err(|error| error.to_string())?;
        } else if read_meta::<serde_json::Value>(connection, producers::HEADS_KEY)
            .map_err(|error| error.to_string())?
            .is_some()
        {
            return Err("old Replica storage contains hidden original producer authority".into());
        }
        let network_marker: Option<String> =
            read_meta(connection, NETWORK_CONTENT_KEY).map_err(|error| error.to_string())?;
        if network_content {
            if network_marker.as_deref() != Some(NETWORK_CONTENT_SCHEMA) {
                return Err(
                    "mandatory original network content marker is missing or changed".into(),
                );
            }
        } else if network_marker.is_some() {
            return Err("old Replica storage contains hidden network content authority".into());
        }
        crate::controller_ingress::validate_storage(connection, controller_content)?;
        adoption::validate_history(
            connection,
            version,
            profile,
            application_schema,
            content_identity,
        )?;
        read_leader_transfer_history(connection)?;
        SqliteRaftStore::validate_controller_supervision(connection)?;
        return Ok(());
    }
    if version != 0 || marker.is_some() {
        return Err("unknown or incomplete Replica storage format".into());
    }
    let has_legacy = legacy.iter().any(Option::is_some);
    if content_identity.is_some() && has_legacy {
        return Err("legacy Replica contents require an explicit complete original upgrade".into());
    }
    let mut objects = connection
        .prepare("SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%'")
        .map_err(|error| error.to_string())?
        .query_map([], |row| row.get::<_, String>(0))
        .map_err(|error| error.to_string())?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|error| error.to_string())?;
    objects.sort();
    let mut expected = if has_legacy {
        LEGACY_TABLES
            .iter()
            .map(|name| name.to_string())
            .collect::<Vec<_>>()
    } else {
        Vec::new()
    };
    expected.sort();
    if objects != expected {
        return Err("legacy Replica database contains incomplete or unknown tables/views".into());
    }
    if has_legacy {
        let identity: (String, String) = connection
            .query_row(
                "SELECT value FROM replica_meta WHERE key='identity'",
                [],
                |row| row.get::<_, String>(0),
            )
            .map_err(|error| error.to_string())
            .and_then(|encoded| {
                serde_json::from_str(&encoded).map_err(|error| error.to_string())
            })?;
        if identity != (profile.to_string(), application_schema.to_string()) {
            return Err("stored replica cluster/application identity differs".into());
        }
    }
    // A pre-inode-lock process can still hold the original database through another basename.
    // SQLite itself must drain all old connections before any format fence reaches the main file.
    let journal: String = connection
        .query_row("PRAGMA journal_mode=DELETE", [], |row| row.get(0))
        .map_err(|error| format!("legacy Replica connections are not quiescent: {error}"))?;
    if journal != "delete" {
        return Err("legacy Replica connections are not quiescent".into());
    }
    connection
        .pragma_update(None, "synchronous", "FULL")
        .map_err(|error| error.to_string())?;
    let transaction = connection
        .transaction_with_behavior(TransactionBehavior::Exclusive)
        .map_err(|error| error.to_string())?;
    if has_legacy {
        if legacy.iter().any(|kind| kind.as_deref() != Some("table"))
            || current.iter().any(Option::is_some)
        {
            return Err("legacy Replica storage tables are incomplete".into());
        }
        for (legacy_name, storage_name) in LEGACY_TABLES.iter().zip(STORAGE_TABLES) {
            transaction
                .execute(
                    &format!("ALTER TABLE {legacy_name} RENAME TO {storage_name}"),
                    [],
                )
                .map_err(|error| error.to_string())?;
        }
    } else if current.iter().any(Option::is_some) {
        return Err("Replica storage has unmarked current tables".into());
    } else {
        transaction
            .execute_batch(
                "CREATE TABLE replica_v1_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
                 CREATE TABLE replica_v1_commands(revision INTEGER PRIMARY KEY,command_id TEXT UNIQUE NOT NULL,data TEXT NOT NULL);
                 CREATE TABLE replica_v1_log(log_index INTEGER PRIMARY KEY,data TEXT NOT NULL);
                 CREATE TABLE replica_v1_events(sequence INTEGER PRIMARY KEY,event_id TEXT UNIQUE NOT NULL,data TEXT NOT NULL,digest TEXT NOT NULL);
                 CREATE TABLE replica_v1_snapshot(singleton INTEGER PRIMARY KEY CHECK(singleton=1),meta TEXT NOT NULL,data BLOB NOT NULL);",
            )
            .map_err(|error| error.to_string())?;
    }
    transaction
        .execute_batch(
            &format!("CREATE TABLE replica_storage_format(version INTEGER PRIMARY KEY CHECK(version={target_version}),schema TEXT NOT NULL);
             INSERT INTO replica_storage_format(version,schema) VALUES ({target_version},'{}');
             CREATE VIEW replica_meta AS SELECT * FROM replica_v1_meta;
             CREATE VIEW replica_commands AS SELECT * FROM replica_v1_commands;
             CREATE VIEW replica_log AS SELECT * FROM replica_v1_log;
             CREATE VIEW replica_events AS SELECT * FROM replica_v1_events;
             CREATE VIEW replica_snapshot AS SELECT * FROM replica_v1_snapshot;
             PRAGMA user_version={target_version};",if controller_content{CONTROLLER_CONTENT_SCHEMA}else if network_content{NETWORK_CONTENT_SCHEMA}else if producers{PRODUCER_STORAGE_SCHEMA}else if content_identity.is_some(){CONTENT_STORAGE_SCHEMA}else{REPLICA_STORAGE_SCHEMA}),
        )
        .map_err(|error| error.to_string())?;
    write_meta(
        &transaction,
        LEADER_TRANSFER_HISTORY_META_KEY,
        &Vec::<LeaderTransferRecord>::new(),
    )
    .map_err(|error| error.to_string())?;
    write_meta(&transaction, crate::supervision::SUPERVISION_KEY, &false)
        .map_err(|error| error.to_string())?;
    let legacy_application = has_legacy
        && read_meta::<state::PersistedCheckpoint>(&transaction, "checkpoint")
            .map_err(|error| error.to_string())?
            .is_some();
    write_meta(
        &transaction,
        crate::supervision::LEGACY_CONTINUITY_KEY,
        &legacy_application,
    )
    .map_err(|error| error.to_string())?;
    write_meta(
        &transaction,
        crate::supervision::PROCESS_HISTORY_KEY,
        &Vec::<crate::supervision::ControllerProcessRecord>::new(),
    )
    .map_err(|error| error.to_string())?;
    if !has_legacy {
        write_meta(&transaction, "identity", &(profile, application_schema))
            .map_err(|error| error.to_string())?;
    }
    if let Some(identity) = content_identity {
        write_meta(&transaction, "content_root_identity", identity)
            .map_err(|error| error.to_string())?;
        write_meta(
            &transaction,
            "content_state",
            &Option::<AcceptedContentState>::None,
        )
        .map_err(|error| error.to_string())?;
    }
    if producers {
        write_meta(
            &transaction,
            producers::HEADS_KEY,
            &producers::ProducerHeads::default(),
        )
        .map_err(|error| error.to_string())?;
    }
    if network_content {
        write_meta(&transaction, NETWORK_CONTENT_KEY, &NETWORK_CONTENT_SCHEMA)
            .map_err(|error| error.to_string())?;
    }
    if controller_content {
        crate::controller_ingress::initialize_storage(&transaction)?;
    }
    transaction.commit().map_err(|error| error.to_string())
}

/// Refuses application effects while an original source transfer remains pending or uncertain.
pub(crate) fn require_controller_source_unfenced(
    connection: &Connection,
    epoch: ControllerEpoch,
) -> Result<(), String> {
    if read_leader_transfer_history(connection)?
        .last()
        .is_some_and(|record| {
            record.source_epoch.replica_id == epoch.replica_id
                && !matches!(record.state.as_str(), "completed" | "rejected")
        })
    {
        return Err("original Controller source is fenced by its pending transfer".into());
    }
    Ok(())
}

/// Allows only explicit operation transitions; terminal outcomes and original identity are immutable.
fn leader_transfer_transition_allowed(original: &str, next: &str) -> bool {
    original == next
        || matches!(
            (original, next),
            ("prepared", "accepted" | "unknown" | "completed")
                | ("accepted", "target-elected" | "unknown" | "completed")
                | ("target-elected", "unknown" | "completed")
                | ("unknown", "completed")
        )
}

/// Reads the complete bounded local history without silently replacing missing authority.
fn read_leader_transfer_history(
    connection: &Connection,
) -> Result<Vec<LeaderTransferRecord>, String> {
    let history: Vec<LeaderTransferRecord> =
        read_meta(connection, LEADER_TRANSFER_HISTORY_META_KEY)
            .map_err(|error| error.to_string())?
            .ok_or("Replica transfer history is absent")?;
    if history.len() > MAX_LEADER_TRANSFERS {
        return Err("Replica transfer history exceeds its original bound".into());
    }
    let mut ids = BTreeSet::new();
    let mut pending = 0;
    for record in &history {
        validate_leader_transfer(record)?;
        if !ids.insert(&record.transfer_id) {
            return Err("Replica transfer history repeats an original operation".into());
        }
        pending += usize::from(!matches!(record.state.as_str(), "completed" | "rejected"));
    }
    if pending > 1 {
        return Err("Replica transfer history contains overlapping uncertain operations".into());
    }
    Ok(history)
}

/// Validates bounded handoff identity and state before it can become persistent authority.
fn validate_leader_transfer(record: &LeaderTransferRecord) -> Result<(), String> {
    if record.transfer_id.trim().is_empty() || record.transfer_id.len() > 128 {
        return Err("leader transfer identity is empty or unbounded".into());
    }
    if record.target == 0 || record.source_epoch.replica_id == 0 || record.source_epoch.term == 0 {
        return Err("leader transfer original epoch/member is unavailable".into());
    }
    if record.target == record.source_epoch.replica_id {
        return Err("leader transfer target is the source replica".into());
    }
    if !matches!(
        record.state.as_str(),
        "prepared" | "accepted" | "target-elected" | "completed" | "rejected" | "unknown"
    ) {
        return Err("leader transfer state is unknown".into());
    }
    if let Some(proof) = &record.source_drain {
        proof.validate(record.source_epoch)?;
        match (proof, &record.source_process) {
            (ControllerDrainProof::ProcessExit { outcome }, Some(process))
                if &outcome.process == process => {}
            (ControllerDrainProof::NoApplicationProcess { .. }, None) => {}
            _ => return Err("original transfer process differs from retained drain proof".into()),
        }
    }
    if record.state == "completed" {
        if record.source_drain.is_none() {
            return Err("completed transfer lacks original source drain".into());
        }
        if !record.confirmed_epoch.is_some_and(|epoch| {
            epoch.replica_id == record.target && epoch.term > record.source_epoch.term
        }) {
            return Err(
                "leader transfer completion requires an exact newer target quorum epoch".into(),
            );
        }
    } else if record.confirmed_epoch.is_some() {
        return Err("pending leader transfer cannot claim a completed quorum proof".into());
    }
    Ok(())
}

/// Binds exact snapshot bytes and applied pointers, detecting corruption before any installation.
fn snapshot_identity(applied: Option<LogId<u64>>, revision: u64, bytes: &[u8]) -> String {
    format!(
        "{}-{revision}-{:x}",
        applied.map_or(0, |id| id.index),
        Sha256::digest(bytes)
    )
}

/// Maps persistence failures into the upstream fail-closed storage boundary.
pub(super) fn io_error(error: impl std::fmt::Display) -> StorageError<u64> {
    StorageIOError::write_state_machine(&std::io::Error::other(error.to_string())).into()
}

/// Reads one versioned JSON metadata value without replacing malformed authority with defaults.
pub(crate) fn read_meta<T: DeserializeOwned>(
    connection: &Connection,
    key: &str,
) -> Result<Option<T>, StorageError<u64>> {
    let encoded: Option<String> = connection
        .query_row(
            "SELECT value FROM replica_v1_meta WHERE key=?1",
            [key],
            |row| row.get(0),
        )
        .optional()
        .map_err(io_error)?;
    encoded
        .map(|encoded| serde_json::from_str(&encoded).map_err(io_error))
        .transpose()
}

/// Writes one metadata value in its caller's current atomic transaction.
pub(crate) fn write_meta<T: Serialize>(
    connection: &Connection,
    key: &str,
    value: &T,
) -> Result<(), StorageError<u64>> {
    let encoded = serde_json::to_string(value).map_err(io_error)?;
    connection.execute("INSERT INTO replica_v1_meta(key,value) VALUES (?1,?2) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        params![key, encoded]).map_err(io_error)?;
    Ok(())
}

/// Reads exact accepted checkpoint/history while one connection guard provides a consistent view.
fn read_view(connection: &Connection) -> Result<ReplicaView, StorageError<u64>> {
    let mut statement = connection
        .prepare("SELECT sequence,event_id,data,digest FROM replica_v1_events ORDER BY sequence")
        .map_err(io_error)?;
    let rows = statement
        .query_map([], |row| {
            Ok((
                row.get::<_, u64>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, String>(3)?,
            ))
        })
        .map_err(io_error)?;
    let events = rows
        .map(|row| {
            let (sequence, event_id, data, digest) = row.map_err(io_error)?;
            let event: state::PersistedEvent = serde_json::from_str(&data).map_err(io_error)?;
            if event.sequence != sequence
                || event.event_id != event_id
                || digest != format!("{:x}", Sha256::digest(data.as_bytes()))
            {
                return Err(io_error("replica event envelope/digest differs"));
            }
            Ok(event)
        })
        .collect::<Result<Vec<_>, _>>()?;
    let checkpoint: Option<state::PersistedCheckpoint> = read_meta(connection, "checkpoint")?;
    let history_anchor: Option<HistoryAnchor> = read_meta(connection, "history_anchor")?;
    let digest: Option<String> = read_meta(connection, "checkpoint_digest")?;
    if checkpoint.as_ref().map(state::checkpoint_identity) != digest {
        return Err(io_error("replica checkpoint digest differs"));
    }
    Ok(ReplicaView {
        revision: read_meta(connection, "revision")?.unwrap_or(0),
        checkpoint,
        events,
        history_anchor,
        commands: read_commands(connection)?,
        content: read_meta(connection, "content_state")?.flatten(),
    })
}

/// Reads immutable command authority in revision order for snapshot transfer and promotion.
fn read_commands(
    connection: &Connection,
) -> Result<Vec<state::ControlCommandJournalEntry>, StorageError<u64>> {
    let mut statement = connection
        .prepare("SELECT data FROM replica_v1_commands ORDER BY revision")
        .map_err(io_error)?;
    statement
        .query_map([], |row| row.get::<_, String>(0))
        .map_err(io_error)?
        .map(|row| serde_json::from_str(&row.map_err(io_error)?).map_err(io_error))
        .collect()
}

impl SqliteRaftStore {
    /// Initializes explicit v0.3 bootstrap only before any consensus/application history exists.
    pub(crate) fn initialize_certificate_trust(
        &self,
        bootstrap: &BTreeMap<u64, String>,
    ) -> Result<(), String> {
        let mut connection = self.connection.lock().map_err(|error| error.to_string())?;
        let transaction = connection
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .map_err(|error| error.to_string())?;
        if let Some(trust) = read_meta::<CertificateTrust>(&transaction, "certificate_trust")
            .map_err(|error| error.to_string())?
        {
            trust.validate()?;
            if trust.bootstrap != *bootstrap {
                return Err("original certificate bootstrap differs".into());
            }
        } else {
            let logs: u64 = transaction
                .query_row("SELECT COUNT(*) FROM replica_v1_log", [], |row| row.get(0))
                .map_err(|error| error.to_string())?;
            let authority: u64 = transaction
                .query_row(
                    "SELECT COUNT(*) FROM replica_v1_meta WHERE key NOT IN ('identity','leader_transfers_v2','controller_supervision_v1','controller_processes_v1','legacy_controller_continuity_unknown')",
                    [],
                    |row| row.get(0),
                )
                .map_err(|error| error.to_string())?;
            let allowed_network_genesis = self.network_genesis(&transaction)?;
            if logs != 0
                || authority
                    != if allowed_network_genesis {
                        if self.controller_content { 6 } else { 4 }
                    } else {
                        0
                    }
                || !read_leader_transfer_history(&transaction)?.is_empty()
            {
                return Err(
                    "existing replica history lacks required original certificate authority".into(),
                );
            }
            write_meta(
                &transaction,
                "certificate_trust",
                &CertificateTrust::initial(bootstrap.clone())?,
            )
            .map_err(|error| error.to_string())?;
        }
        transaction.commit().map_err(|error| error.to_string())
    }

    /// Reads only complete committed infrastructure identity; no bootstrap fallback or mutation.
    pub fn certificate_trust(&self) -> Result<Option<CertificateTrust>, String> {
        let connection = self.connection.lock().map_err(|error| error.to_string())?;
        let trust: Option<CertificateTrust> =
            read_meta(&connection, "certificate_trust").map_err(|error| error.to_string())?;
        if let Some(trust) = &trust {
            trust.validate()?;
        }
        Ok(trust)
    }

    /// Reads an original live WAL-aware local trust database without obtaining a second writer.
    pub(crate) fn readonly_certificate_trust(
        path: &Path,
        profile: &str,
        bootstrap: &BTreeMap<u64, String>,
    ) -> Result<CertificateTrust, String> {
        let connection = Connection::open_with_flags(
            path,
            rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY | rusqlite::OpenFlags::SQLITE_OPEN_NO_MUTEX,
        )
        .map_err(|error| error.to_string())?;
        connection
            .busy_timeout(Duration::from_millis(100))
            .map_err(|error| error.to_string())?;
        let identity: Option<(String, String)> =
            read_meta(&connection, "identity").map_err(|error| error.to_string())?;
        if identity
            .as_ref()
            .is_none_or(|identity| identity.0 != profile)
        {
            return Err("readonly trust database profile differs".into());
        }
        let value: String = connection
            .query_row(
                "SELECT value FROM replica_v1_meta WHERE key='certificate_trust'",
                [],
                |row| row.get(0),
            )
            .map_err(|error| error.to_string())?;
        if value.len() > 4 * 1024 * 1024 {
            return Err("certificate trust exceeds explicit read bounds".into());
        }
        let trust: CertificateTrust =
            serde_json::from_str(&value).map_err(|error| error.to_string())?;
        trust.validate()?;
        if trust.bootstrap != *bootstrap {
            return Err("readonly original certificate bootstrap differs".into());
        }
        Ok(trust)
    }
    /// Opens a private consensus database and rejects a conflicting stored cluster/schema profile.
    pub fn open(path: &Path, profile: &str, application_schema: &str) -> Result<Self, String> {
        Self::open_inner(
            path,
            profile,
            application_schema,
            None,
            false,
            NetworkContentMode::None,
        )
    }

    /// Opens only an explicit complete-content generation with its exact actual member-local backend.
    /// Existing v0.3 history requires a separate original drain/upgrade, never an implicit conversion.
    pub fn open_with_content(
        path: &Path,
        profile: &str,
        application_schema: &str,
        content: ReplicaContentStore,
    ) -> Result<Self, String> {
        if content.identity().profile_identity != profile
            || application_schema != state::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA
        {
            return Err("complete-content Replica requires its actual original profile/current application schema".into());
        }
        Self::open_inner(
            path,
            profile,
            application_schema,
            Some(content),
            false,
            NetworkContentMode::None,
        )
    }

    /// Opens only an explicit original network parent and actual Root4, refusing all legacy conversion.
    pub fn open_with_network_content(
        path: &Path,
        profile: &str,
        application_schema: &str,
        content: ReplicaContentStore,
    ) -> Result<Self, String> {
        if !content.streams_enabled()
            || content.identity().profile_identity != profile
            || application_schema != state::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA
        {
            return Err(
                "network content parent requires its actual original Root4/profile/schema".into(),
            );
        }
        Self::open_inner(
            path,
            profile,
            application_schema,
            Some(content),
            false,
            NetworkContentMode::Member,
        )
    }

    /// Opens only explicit parent8 with actual original Root4; parent7 and old writers refuse it.
    pub fn open_with_controller_content(
        path: &Path,
        profile: &str,
        application_schema: &str,
        content: ReplicaContentStore,
    ) -> Result<Self, String> {
        if !content.streams_enabled()
            || content.identity().profile_identity != profile
            || application_schema != state::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA
        {
            return Err(
                "owned Controller parent requires actual original Root4/profile/schema".into(),
            );
        }
        Self::open_inner(
            path,
            profile,
            application_schema,
            Some(content),
            false,
            NetworkContentMode::Controller,
        )
    }

    /// Shares original actual SQLite/root lease setup without lending complete authority to old profiles.
    fn open_inner(
        path: &Path,
        profile: &str,
        application_schema: &str,
        content: Option<ReplicaContentStore>,
        adoption: bool,
        network_mode: NetworkContentMode,
    ) -> Result<Self, String> {
        let network_content = network_mode != NetworkContentMode::None;
        let canonical = if path.exists() {
            path.canonicalize().map_err(|error| error.to_string())?
        } else {
            let parent = path
                .parent()
                .filter(|parent| !parent.as_os_str().is_empty())
                .unwrap_or_else(|| Path::new("."));
            parent
                .canonicalize()
                .map_err(|error| error.to_string())?
                .join(
                    path.file_name()
                        .ok_or("replica database path has no filename")?,
                )
        };
        let mut lease = canonical.as_os_str().to_os_string();
        lease.push(".writer.lock");
        let mut options = std::fs::OpenOptions::new();
        options.read(true).write(true).create(true).truncate(false);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            options.custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK);
        }
        let writer = options
            .open(Path::new(&lease))
            .map_err(|error| error.to_string())?;
        if !writer
            .metadata()
            .map_err(|error| error.to_string())?
            .is_file()
        {
            return Err("replica lease must be a regular file".into());
        }
        writer
            .try_lock()
            .map_err(|error| format!("replica database already owned: {error}"))?;
        let database = options
            .open(&canonical)
            .map_err(|error| error.to_string())?;
        if !database
            .metadata()
            .map_err(|error| error.to_string())?
            .is_file()
        {
            return Err("replica database must be a regular file".into());
        }
        database
            .try_lock()
            .map_err(|error| format!("replica database inode already owned: {error}"))?;
        let writer = ReplicaWriterLease {
            path: canonical.clone(),
            file: writer,
            database,
        };
        let mut connection = Connection::open(&canonical).map_err(|error| error.to_string())?;
        connection
            .busy_timeout(Duration::from_millis(100))
            .map_err(|error| error.to_string())?;
        let version: i64 = connection
            .query_row("PRAGMA user_version", [], |row| row.get(0))
            .map_err(|error| error.to_string())?;
        if adoption && !matches!(version, CONTENT_STORAGE_VERSION | ADOPTED_STORAGE_VERSION) {
            return Err("explicit original-parent adoption requires existing storage v4 or its original adopted v6".into());
        }
        if adoption {
            adoption::validate_scan_bounds(&connection)?;
        }
        prepare_storage_format(
            &mut connection,
            profile,
            application_schema,
            content.as_ref().map(ReplicaContentStore::identity),
            !(adoption && version == CONTENT_STORAGE_VERSION)
                && content
                    .as_ref()
                    .is_some_and(ReplicaContentStore::producer_journal_enabled),
            adoption,
            network_mode,
        )?;
        if adoption {
            let journal: String = connection
                .query_row("PRAGMA journal_mode=DELETE", [], |row| row.get(0))
                .map_err(|error| {
                    format!("original adoption SQL connections are not quiescent: {error}")
                })?;
            if journal != "delete" {
                return Err("original adoption SQL connections are not quiescent".into());
            }
            connection
                .transaction_with_behavior(TransactionBehavior::Exclusive)
                .map_err(|error| {
                    format!("original adoption SQL connections are not quiescent: {error}")
                })?
                .commit()
                .map_err(|error| error.to_string())?;
        } else {
            connection
                .pragma_update(None, "journal_mode", "WAL")
                .map_err(|error| error.to_string())?;
        }
        connection
            .pragma_update(None, "synchronous", "FULL")
            .map_err(|error| error.to_string())?;
        let store = Self {
            connection: Arc::new(Mutex::new(connection)),
            _writer: Arc::new(writer),
            profile: profile.into(),
            application_schema: application_schema.into(),
            content,
            network_content,
            controller_content: network_mode == NetworkContentMode::Controller,
            work_clock: None,
            storage_jobs: Arc::new(tokio::sync::Semaphore::new(8)),
            consensus_jobs: Arc::new(tokio::sync::Semaphore::new(16)),
        };
        let view = store.accepted_view()?;
        view.validate(application_schema, profile)
            .map_err(|error| error.to_string())?;
        {
            let connection = store.connection.lock().map_err(|error| error.to_string())?;
            if let Some(membership) =
                read_meta::<StoredMembership<u64, ReplicaMember>>(&connection, "membership")
                    .map_err(|error| error.to_string())?
            {
                store.validate_content_membership(membership.membership())?;
            }
        }
        if store.content.is_none() && view.content.is_some()
            || store.content.is_some() && view.checkpoint.is_some() && view.content.is_none()
        {
            return Err("Replica storage generation lacks its exact complete-content view".into());
        }
        if !(adoption && version == CONTENT_STORAGE_VERSION) {
            let connection = store.connection.lock().map_err(|error| error.to_string())?;
            store.validate_content_producer_heads(&connection)?;
            adoption::validate_root_records(&store, &connection)?;
        }
        Ok(store)
    }

    /// Reads the most recent local operation, retaining all original IDs in the same store.
    pub fn leader_transfer_record(&self) -> Result<Option<LeaderTransferRecord>, String> {
        let connection = self.connection.lock().map_err(|error| error.to_string())?;
        Ok(read_leader_transfer_history(&connection)?.last().cloned())
    }

    /// Reads one original operation without creating another effect or changing the latest hold.
    pub fn leader_transfer_by_id(
        &self,
        transfer_id: &str,
    ) -> Result<Option<LeaderTransferRecord>, String> {
        let connection = self.connection.lock().map_err(|error| error.to_string())?;
        let current = read_leader_transfer_history(&connection)?
            .into_iter()
            .find(|record| record.transfer_id == transfer_id);
        if current.is_some() {
            return Ok(current);
        }
        let legacy: Vec<LeaderTransferRecord> =
            read_meta(&connection, "legacy_transfer_history_v2")
                .map_err(|error| error.to_string())?
                .unwrap_or_default();
        if legacy.len() > MAX_LEADER_TRANSFERS {
            return Err("original legacy transfer history exceeds its bound".into());
        }
        Ok(legacy
            .into_iter()
            .find(|record| record.transfer_id == transfer_id)
            .map(|mut record| {
                record.state = "unknown".into();
                record.confirmed_epoch = None;
                record
            }))
    }

    /// Commits a permitted original operation transition without discarding prior operations.
    pub fn persist_leader_transfer(&self, record: &LeaderTransferRecord) -> Result<(), String> {
        validate_leader_transfer(record)?;
        let mut connection = self.connection.lock().map_err(|error| error.to_string())?;
        let transaction = connection
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .map_err(|error| error.to_string())?;
        let mut history = read_leader_transfer_history(&transaction)?;
        if let Some(current) = history
            .iter_mut()
            .find(|current| current.transfer_id == record.transfer_id)
        {
            if current.source_epoch != record.source_epoch
                || current.target != record.target
                || current.required_last_log_index != record.required_last_log_index
                || current.source_process != record.source_process
                || (current.source_drain.is_some() && current.source_drain != record.source_drain)
                || !leader_transfer_transition_allowed(&current.state, &record.state)
                || (current.confirmed_epoch.is_some()
                    && current.confirmed_epoch != record.confirmed_epoch)
            {
                return Err("leader transfer identity or state regressed".into());
            }
            *current = record.clone();
        } else {
            let legacy: Vec<LeaderTransferRecord> =
                read_meta(&transaction, "legacy_transfer_history_v2")
                    .map_err(|error| error.to_string())?
                    .unwrap_or_default();
            if legacy
                .iter()
                .any(|original| original.transfer_id == record.transfer_id)
            {
                return Err("original legacy transfer ID cannot authorize another effect".into());
            }
            if history
                .iter()
                .any(|current| !matches!(current.state.as_str(), "completed" | "rejected"))
            {
                return Err("another leader transfer requires reconciliation".into());
            }
            if history.len().saturating_add(legacy.len()) >= MAX_LEADER_TRANSFERS {
                return Err(
                    "leader transfer history is full; original IDs cannot be discarded".into(),
                );
            }
            history.push(record.clone());
        }
        write_meta(&transaction, LEADER_TRANSFER_HISTORY_META_KEY, &history)
            .map_err(|error| error.to_string())?;
        transaction.commit().map_err(|error| error.to_string())
    }

    /// Duplicates the actual lease descriptor for a bounded inherited-descriptor regression test.
    #[cfg(test)]
    pub(crate) fn writer_descriptor_for_test(&self) -> std::io::Result<File> {
        self._writer.file.try_clone()
    }

    /// Reads the complete already-applied view without evaluating Control or Runtime transitions.
    pub fn accepted_view(&self) -> Result<ReplicaView, String> {
        let connection = self
            .connection
            .lock()
            .map_err(|_| "replica store lock is poisoned")?;
        read_view(&connection).map_err(|error| error.to_string())
    }

    /// Persists exact local log records using the same original intent/SQL/flush path as OpenRaft.
    /// This storage method grants no current quorum, Controller child, Task or device authority.
    pub fn persist_log_entries(
        &self,
        entries: &[Entry<ControllerRaftConfig>],
    ) -> Result<(), StorageError<u64>> {
        for entry in entries {
            if let EntryPayload::Membership(membership) = &entry.payload {
                self.validate_content_membership(membership)
                    .map_err(io_error)?;
            }
        }
        if self.has_producer_journal() && self.work_clock.is_none() {
            let (store, _capacity) = self.original_storage_work()?;
            return store.persist_log_entries(entries);
        }
        let admissions = entries
            .iter()
            .map(|entry| self.retain_log_inputs(entry))
            .collect::<Result<Vec<_>, _>>()?;
        let mut published = Vec::new();
        let mut connection = self.connection.lock().map_err(io_error)?;
        let transaction = connection.transaction().map_err(io_error)?;
        for (entry, admission) in entries.iter().zip(admissions) {
            let encoded = serde_json::to_string(&entry).map_err(io_error)?;
            match admission {
                Some(crate::content_store::ProducerAdmission::New(permit)) => {
                    permit.check().map_err(io_error)?;
                    published.push(permit);
                }
                Some(crate::content_store::ProducerAdmission::Existing(original)) => {
                    let actual: Option<String> = transaction
                        .query_row(
                            "SELECT data FROM replica_v1_log WHERE log_index=?1",
                            [entry.log_id.index],
                            |row| row.get(0),
                        )
                        .optional()
                        .map_err(io_error)?;
                    if !matches!(original.disposition.as_str(), "published" | "unknown")
                        || actual.as_deref() != Some(encoded.as_str())
                    {
                        return Err(io_error(
                            "original log producer is retained; unknown/retired append cannot execute again",
                        ));
                    }
                    continue;
                }
                None => {}
            }
            transaction.execute("INSERT INTO replica_v1_log(log_index,data) VALUES (?1,?2) ON CONFLICT(log_index) DO UPDATE SET data=excluded.data",
                    params![entry.log_id.index,encoded]).map_err(io_error)?;
        }
        transaction.commit().map_err(io_error)?;
        for permit in published {
            let source = permit.source();
            let position: LogId<u64> = serde_json::from_str(&source.position).map_err(io_error)?;
            let raw: String = connection
                .query_row(
                    "SELECT data FROM replica_v1_log WHERE log_index=?1",
                    [position.index],
                    |row| row.get(0),
                )
                .map_err(io_error)?;
            if format!("{:x}", Sha256::digest(raw.as_bytes())) != source.parent_record_digest {
                return Err(io_error(
                    "original log publication differs from its complete source",
                ));
            }
            let proof = format!(
                "{:x}",
                Sha256::digest(
                    serde_json::to_vec(&(
                        self.profile.as_str(),
                        permit.operation_id(),
                        &position,
                        &raw
                    ))
                    .map_err(io_error)?
                )
            );
            self.publish_content_producer(Some(permit), &proof)?;
        }
        Ok(())
    }

    /// Reads the applied application ordinal without decoding complete history on each supervisor tick.
    pub fn accepted_revision(&self) -> Result<u64, String> {
        let connection = self
            .connection
            .lock()
            .map_err(|_| "replica store lock is poisoned")?;
        read_meta(&connection, "revision")
            .map(|revision| revision.unwrap_or(0))
            .map_err(|error| error.to_string())
    }

    /// Reads only the accepted application identity without decoding the retained event history.
    pub fn accepted_application_identity(&self) -> Result<AcceptedApplicationIdentity, String> {
        let connection = self
            .connection
            .lock()
            .map_err(|_| "replica store lock is poisoned")?;
        let checkpoint: Option<state::PersistedCheckpoint> =
            read_meta(&connection, "checkpoint").map_err(|error| error.to_string())?;
        let revision: u64 = read_meta(&connection, "revision")
            .map_err(|error| error.to_string())?
            .unwrap_or(0);
        Ok(AcceptedApplicationIdentity {
            revision,
            event_sequence: checkpoint.as_ref().map_or(0, |value| value.event_sequence),
            checkpoint_digest: checkpoint.as_ref().map(state::checkpoint_identity),
        })
    }

    /// Accepts only an exact predecessor/tail under the startup-owned application schema.
    fn apply_batch(
        &self,
        connection: &Connection,
        batch: &state::ApplicationCommitBatch,
    ) -> Result<CommitReceipt, StorageError<u64>> {
        let previous: Option<state::PersistedCheckpoint> = read_meta(connection, "checkpoint")?;
        let revision: u64 = read_meta(connection, "revision")?.unwrap_or(0);
        let current_digest = previous.as_ref().map(state::checkpoint_identity);
        let command_revision: u64 = connection
            .query_row(
                "SELECT COALESCE(MAX(revision),0) FROM replica_v1_commands",
                [],
                |row| row.get(0),
            )
            .map_err(io_error)?;
        let rejection = if let Err(error) = batch.validate() {
            Some(error.to_string())
        } else if batch.checkpoint.schema != self.application_schema {
            Some("replica application schema differs".into())
        } else if batch.commands.first().is_some_and(|command| {
            command_revision.checked_add(1) != Some(command.authority_revision)
        }) {
            Some("replica command predecessor differs".into())
        } else if previous
            .as_ref()
            .map_or(0, |checkpoint| checkpoint.event_sequence)
            != batch.previous_event_sequence
            || current_digest != batch.previous_checkpoint_digest
        {
            Some("replica predecessor identity differs".into())
        } else {
            None
        };
        if let Some(rejection) = rejection {
            return Ok(CommitReceipt {
                accepted: false,
                revision,
                checkpoint_digest: current_digest,
                rejection: Some(rejection),
                certificate: None,
            });
        }
        let encoded = batch
            .events
            .iter()
            .map(serde_json::to_string)
            .collect::<Result<Vec<_>, _>>()
            .map_err(io_error)?;
        let additional: u64 = encoded.iter().map(|event| event.len() as u64).sum();
        let bytes: u64 = connection
            .query_row(
                "SELECT COALESCE(SUM(length(CAST(data AS BLOB))),0) FROM replica_v1_events",
                [],
                |row| row.get(0),
            )
            .map_err(io_error)?;
        if bytes
            .checked_add(additional)
            .is_none_or(|bytes| bytes > MAX_HISTORY_BYTES)
        {
            if self.content.is_some() {
                return Ok(CommitReceipt { accepted:false,revision,checkpoint_digest:current_digest,rejection:Some("complete original Replica history requires coordinated Archive before capacity".into()),certificate:None });
            }
            compact_prefix(
                connection,
                batch.checkpoint.event_sequence,
                &state::checkpoint_identity(&batch.checkpoint),
                bytes.saturating_add(additional),
                None,
            )?;
        }
        for (event, encoded) in batch.events.iter().zip(encoded) {
            connection
                .execute(
                    "INSERT INTO replica_v1_events(sequence,event_id,data,digest) VALUES (?1,?2,?3,?4)",
                    params![event.sequence, event.event_id, encoded, format!("{:x}",Sha256::digest(encoded.as_bytes()))],
                )
                .map_err(io_error)?;
        }
        for command in &batch.commands {
            connection
                .execute(
                    "INSERT INTO replica_v1_commands VALUES (?1,?2,?3)",
                    params![
                        command.authority_revision,
                        command.command_id,
                        serde_json::to_string(command).map_err(io_error)?
                    ],
                )
                .map_err(io_error)?;
        }
        let revision = revision
            .checked_add(1)
            .ok_or_else(|| io_error("application revision exhausted"))?;
        write_meta(connection, "checkpoint", &batch.checkpoint)?;
        write_meta(
            connection,
            "checkpoint_digest",
            &state::checkpoint_identity(&batch.checkpoint),
        )?;
        if let Some(mut anchor) = read_meta::<HistoryAnchor>(connection, "history_anchor")? {
            anchor.checkpoint_digest = state::checkpoint_identity(&batch.checkpoint);
            write_meta(connection, "history_anchor", &anchor)?;
        }
        write_meta(connection, "revision", &revision)?;
        Ok(CommitReceipt {
            accepted: true,
            revision,
            checkpoint_digest: Some(state::checkpoint_identity(&batch.checkpoint)),
            rejection: None,
            certificate: None,
        })
    }

    /// Compacts old accepted rows while preserving a complete checkpoint and a verifiable anchor.
    pub fn compact_history(&self, retain_events: usize) -> Result<HistoryAnchor, String> {
        if self.content.is_some() {
            return Err("complete-content history requires original coordinated Archive; independent prefix deletion refused".into());
        }
        let mut connection = self
            .connection
            .lock()
            .map_err(|_| "replica store lock is poisoned")?;
        let transaction = connection
            .transaction_with_behavior(TransactionBehavior::Immediate)
            .map_err(|error| error.to_string())?;
        let checkpoint: state::PersistedCheckpoint = read_meta(&transaction, "checkpoint")
            .map_err(|error| error.to_string())?
            .ok_or_else(|| "cannot compact an empty replica checkpoint".to_string())?;
        let bytes: u64 = transaction
            .query_row(
                "SELECT COALESCE(SUM(length(CAST(data AS BLOB))),0) FROM replica_v1_events",
                [],
                |row| row.get(0),
            )
            .map_err(|error| error.to_string())?;
        let mut anchor = compact_prefix(
            &transaction,
            checkpoint.event_sequence,
            &state::checkpoint_identity(&checkpoint),
            bytes,
            Some(retain_events),
        )
        .map_err(|error| error.to_string())?;
        anchor.checkpoint_digest = state::checkpoint_identity(&checkpoint);
        write_meta(&transaction, "history_anchor", &anchor).map_err(|error| error.to_string())?;
        transaction.commit().map_err(|error| error.to_string())?;
        Ok(anchor)
    }
}

/// Removes the oldest rows only after the complete checkpoint is already in the same transaction.
fn compact_prefix(
    connection: &Connection,
    checkpoint_sequence: u64,
    checkpoint_digest: &str,
    current_bytes: u64,
    retain_events: Option<usize>,
) -> Result<HistoryAnchor, StorageError<u64>> {
    let current_anchor: Option<HistoryAnchor> = read_meta(connection, "history_anchor")?;
    let mut statement = connection
        .prepare("SELECT sequence,event_id,data,digest FROM replica_v1_events ORDER BY sequence")
        .map_err(io_error)?;
    let rows = statement
        .query_map([], |row| {
            Ok((
                row.get::<_, u64>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, String>(3)?,
            ))
        })
        .map_err(io_error)?;
    let mut remove = Vec::new();
    let mut remaining = current_bytes;
    let target = if let Some(retain_events) = retain_events {
        let count = connection
            .query_row("SELECT COUNT(*) FROM replica_v1_events", [], |row| {
                row.get::<_, u64>(0)
            })
            .map_err(io_error)?;
        count.saturating_sub(retain_events as u64)
    } else {
        MAX_HISTORY_BYTES.saturating_mul(3) / 4
    };
    let mut removed_count = 0_u64;
    for row in rows {
        let (sequence, event_id, data, digest) = row.map_err(io_error)?;
        if (retain_events.is_some() && removed_count >= target)
            || (retain_events.is_none() && remaining <= target)
        {
            break;
        }
        remaining = remaining.saturating_sub(data.len() as u64);
        remove.push((sequence, event_id, data, digest));
        removed_count = removed_count.saturating_add(1);
    }
    if remove.is_empty() {
        return Err(io_error("replica history cannot fit one accepted batch"));
    }
    if retain_events.is_none() && remaining > MAX_HISTORY_BYTES {
        return Err(io_error(
            "one accepted replica batch exceeds history bounds",
        ));
    }
    let last = remove.last().expect("remove is nonempty");
    drop(statement);
    let mut chain = Sha256::new();
    if let Some(anchor) = &current_anchor {
        chain.update(anchor.base_digest.as_bytes());
    }
    for (_, _, _, digest) in &remove {
        chain.update(digest.as_bytes());
    }
    let anchor = HistoryAnchor {
        base_sequence: last.0,
        base_digest: format!("{:x}", chain.finalize()),
        checkpoint_digest: checkpoint_digest.to_string(),
        schema: HISTORY_ANCHOR_SCHEMA.to_string(),
    };
    for (sequence, _, _, _) in &remove {
        connection
            .execute(
                "DELETE FROM replica_v1_events WHERE sequence=?1",
                [sequence],
            )
            .map_err(io_error)?;
    }
    write_meta(connection, "history_anchor", &anchor)?;
    if last.0 > checkpoint_sequence {
        return Err(io_error("replica compaction removed the checkpoint tail"));
    }
    Ok(anchor)
}

impl RaftLogReader<ControllerRaftConfig> for SqliteRaftStore {
    /// Returns consecutive stored entries in the requested inclusive/exclusive range.
    async fn try_get_log_entries<R>(
        &mut self,
        range: R,
    ) -> Result<Vec<Entry<ControllerRaftConfig>>, StorageError<u64>>
    where
        R: RangeBounds<u64> + Clone + std::fmt::Debug + Send,
    {
        let start = match range.start_bound() {
            Bound::Included(value) => *value,
            Bound::Excluded(value) => match value.checked_add(1) {
                Some(value) => value,
                None => return Ok(Vec::new()),
            },
            Bound::Unbounded => 0,
        };
        let end = match range.end_bound() {
            Bound::Included(value) => *value,
            Bound::Excluded(value) => match value.checked_sub(1) {
                Some(value) => value,
                None => return Ok(Vec::new()),
            },
            Bound::Unbounded => i64::MAX as u64,
        };
        if start > end || start > i64::MAX as u64 {
            return Ok(Vec::new());
        }
        self.run_consensus_work(move|store|{
        let connection = store.connection.lock().map_err(io_error)?;
        let mut statement = connection.prepare("SELECT data FROM replica_v1_log WHERE log_index>=?1 AND log_index<=?2 ORDER BY log_index").map_err(io_error)?;
        let rows = statement
            .query_map(params![start, end.min(i64::MAX as u64)], |row| {
                row.get::<_, String>(0)
            })
            .map_err(io_error)?;
        rows.map(|row| serde_json::from_str(&row.map_err(io_error)?).map_err(io_error))
            .collect()
        }).await
    }
}

impl RaftLogStorage<ControllerRaftConfig> for SqliteRaftStore {
    type LogReader = Self;

    /// Exposes the exact last-present/purged pointers independently of applied state.
    async fn get_log_state(&mut self) -> Result<LogState<ControllerRaftConfig>, StorageError<u64>> {
        self.run_consensus_work(move |store| {
            let connection = store.connection.lock().map_err(io_error)?;
            let purged: Option<LogId<u64>> = read_meta(&connection, "purged")?;
            let last: Option<String> = connection
                .query_row(
                    "SELECT data FROM replica_v1_log ORDER BY log_index DESC LIMIT 1",
                    [],
                    |row| row.get(0),
                )
                .optional()
                .map_err(io_error)?;
            let last = last
                .map(|entry| {
                    serde_json::from_str::<Entry<ControllerRaftConfig>>(&entry).map_err(io_error)
                })
                .transpose()?;
            Ok(LogState {
                last_purged_log_id: purged,
                last_log_id: last.map(|entry| entry.log_id).or(purged),
            })
        })
        .await
    }

    /// Shares the same serialized persistent log reader.
    async fn get_log_reader(&mut self) -> Self::LogReader {
        self.clone()
    }

    /// Persists each vote under SQLite FULL before returning to the consensus protocol.
    async fn save_vote(&mut self, vote: &Vote<u64>) -> Result<(), StorageError<u64>> {
        let vote = *vote;
        self.run_consensus_work(move |store| {
            let connection = store.connection.lock().map_err(io_error)?;
            write_meta(&connection, "vote", &vote)
        })
        .await
    }

    /// Returns only the durable vote; malformed values fail closed.
    async fn read_vote(&mut self) -> Result<Option<Vote<u64>>, StorageError<u64>> {
        self.run_consensus_work(move |store| {
            let connection = store.connection.lock().map_err(io_error)?;
            read_meta(&connection, "vote")
        })
        .await
    }

    /// Persists the exact committed log pointer independently of application projections.
    async fn save_committed(
        &mut self,
        committed: Option<LogId<u64>>,
    ) -> Result<(), StorageError<u64>> {
        self.run_consensus_work(move |store| {
            let connection = store.connection.lock().map_err(io_error)?;
            match committed {
                Some(committed) => write_meta(&connection, "committed", &committed),
                None => {
                    connection
                        .execute("DELETE FROM replica_v1_meta WHERE key='committed'", [])
                        .map_err(io_error)?;
                    Ok(())
                }
            }
        })
        .await
    }

    /// Reads the last durable consensus commitment.
    async fn read_committed(&mut self) -> Result<Option<LogId<u64>>, StorageError<u64>> {
        self.run_consensus_work(move |store| {
            let connection = store.connection.lock().map_err(io_error)?;
            read_meta(&connection, "committed")
        })
        .await
    }

    /// Appends ordered logs atomically and reports flush only after the durable Commit.
    async fn append<I>(
        &mut self,
        entries: I,
        callback: LogFlushed<ControllerRaftConfig>,
    ) -> Result<(), StorageError<u64>>
    where
        I: IntoIterator<Item = Entry<ControllerRaftConfig>> + Send,
        I::IntoIter: Send,
    {
        let entries = entries.into_iter().collect::<Vec<_>>();
        if self.has_producer_journal() {
            return self
                .run_storage_work(move |store| {
                    let result = store.persist_log_entries(&entries);
                    callback.log_io_completed(
                        result
                            .as_ref()
                            .map(|_| ())
                            .map_err(|error| std::io::Error::other(error.to_string())),
                    );
                    result
                })
                .await;
        }
        let result = self.persist_log_entries(&entries);
        callback.log_io_completed(
            result
                .as_ref()
                .map(|_| ())
                .map_err(|error: &StorageError<u64>| std::io::Error::other(error.to_string())),
        );
        result
    }

    /// Removes only the suffix specified by the upstream consensus engine.
    async fn truncate(&mut self, log_id: LogId<u64>) -> Result<(), StorageError<u64>> {
        self.run_consensus_work(move |store| {
            let connection = store.connection.lock().map_err(io_error)?;
            let pins = store.removed_log_pins(&connection, log_id.index, true)?;
            connection
                .execute(
                    "DELETE FROM replica_v1_log WHERE log_index>=?1",
                    [log_id.index],
                )
                .map_err(io_error)?;
            let proof = format!(
                "{:x}",
                Sha256::digest(
                    serde_json::to_vec(&("original-log-suffix-removed", log_id))
                        .map_err(io_error)?
                )
            );
            store.release_content(&pins, &proof)?;
            if store.has_producer_journal() {
                store
                    .reconcile_content_producers_locked(&connection)
                    .map_err(io_error)?;
            }
            Ok(())
        })
        .await
    }

    /// Deletes a compacted prefix and advances its durable pointer in one transaction.
    async fn purge(&mut self, log_id: LogId<u64>) -> Result<(), StorageError<u64>> {
        self.run_consensus_work(move |store| {
            let mut connection = store.connection.lock().map_err(io_error)?;
            let pins = store.removed_log_pins(&connection, log_id.index, false)?;
            let transaction = connection.transaction().map_err(io_error)?;
            transaction
                .execute(
                    "DELETE FROM replica_v1_log WHERE log_index<=?1",
                    [log_id.index],
                )
                .map_err(io_error)?;
            write_meta(&transaction, "purged", &log_id)?;
            transaction.commit().map_err(io_error)?;
            let proof = format!(
                "{:x}",
                Sha256::digest(
                    serde_json::to_vec(&("original-log-prefix-purged", log_id))
                        .map_err(io_error)?
                )
            );
            store.release_content(&pins, &proof)?;
            if store.has_producer_journal() {
                store
                    .reconcile_content_producers_locked(&connection)
                    .map_err(io_error)?;
            }
            Ok(())
        })
        .await
    }
}

impl RaftStateMachine<ControllerRaftConfig> for SqliteRaftStore {
    type SnapshotBuilder = Self;

    /// Returns only pointers and membership already persisted by quorum apply.
    async fn applied_state(
        &mut self,
    ) -> Result<(Option<LogId<u64>>, StoredMembership<u64, ReplicaMember>), StorageError<u64>> {
        self.run_consensus_work(move |store| {
            let connection = store.connection.lock().map_err(io_error)?;
            Ok((
                read_meta(&connection, "applied")?,
                read_meta(&connection, "membership")?.unwrap_or_default(),
            ))
        })
        .await
    }

    /// Persists consensus apply and exact accepted history before returning any application receipt.
    async fn apply<I>(&mut self, entries: I) -> Result<Vec<CommitReceipt>, StorageError<u64>>
    where
        I: IntoIterator<Item = Entry<ControllerRaftConfig>> + Send,
        I::IntoIter: Send,
    {
        let entries = entries.into_iter().collect::<Vec<_>>();
        if self.has_producer_journal() {
            return self
                .run_storage_work(move |store| store.apply_entries(entries))
                .await;
        }
        self.apply_entries(entries)
    }

    /// Provides the same serialized storage as a stable snapshot builder.
    async fn get_snapshot_builder(&mut self) -> Self::SnapshotBuilder {
        self.clone()
    }

    /// Creates a fresh bounded receive cursor; RPC chunking remains OpenRaft-owned.
    async fn begin_receiving_snapshot(
        &mut self,
    ) -> Result<Box<ReplicaSnapshotData>, StorageError<u64>> {
        Ok(Box::new(ReplicaSnapshotData::new(Vec::new())))
    }

    /// Installs exact accepted metadata and consensus pointers atomically, preserving the last snapshot.
    async fn install_snapshot(
        &mut self,
        meta: &SnapshotMeta<u64, ReplicaMember>,
        snapshot: Box<ReplicaSnapshotData>,
    ) -> Result<(), StorageError<u64>> {
        if self.has_producer_journal() {
            let meta = meta.clone();
            return self
                .run_storage_work(move |store| {
                    store.install_snapshot_record(&meta, snapshot.into_inner(), None)
                })
                .await;
        }
        self.install_snapshot_record(meta, snapshot.into_inner(), None)
    }

    /// Returns the exact persistent last snapshot for upstream streaming or recovery.
    async fn get_current_snapshot(
        &mut self,
    ) -> Result<Option<Snapshot<ControllerRaftConfig>>, StorageError<u64>> {
        if self.has_producer_journal() {
            return self
                .run_storage_work(|store| store.current_snapshot_record())
                .await;
        }
        self.current_snapshot_record()
    }
}

impl RaftSnapshotBuilder<ControllerRaftConfig> for SqliteRaftStore {
    /// Builds one consistent complete snapshot and persists it before exposing the stream handle.
    async fn build_snapshot(
        &mut self,
    ) -> Result<Snapshot<ControllerRaftConfig>, StorageError<u64>> {
        if self.has_producer_journal() {
            return self
                .run_storage_work(|store| store.build_snapshot_record(None))
                .await;
        }
        self.build_snapshot_record(None)
    }
}
