//! Original owned-child content operations; infrastructure evidence never proves device continuity.

use crate::content_store::{ContentBudget, StreamPermission};
use crate::store::{read_meta, write_meta};
use crate::*;
use rusqlite::{Connection, OptionalExtension, TransactionBehavior, params};
use sha2::{Digest as _, Sha256};

#[path = "controller_ingress/client.rs"]
mod client;
#[path = "controller_ingress/content_read.rs"]
mod content_read;
#[path = "controller_ingress/store.rs"]
mod journal;
#[cfg(test)]
#[path = "controller_ingress/tests.rs"]
mod tests;
#[path = "controller_ingress/wire.rs"]
mod wire;

pub use client::{ControllerContentClient, ControllerContentClientConfig, ControllerContentRead};
pub use wire::ControllerIngressServer;

/// Mandatory original journal marker, including a fresh zero-operation parent.
const SCHEMA_KEY: &str = "controller_content_ingress_schema";
/// Mandatory complete original operation inventory, never inferred from surviving rows.
const HEAD_KEY: &str = "controller_content_ingress_ids";
/// Exact new local journal interpretation.
const JOURNAL_SCHEMA: &str = "roboguide.controller-content-ingress/v0.1";
/// Retained uncertain operations never disappear to make capacity for new effects.
const MAX_OPERATIONS: usize = 1024;
/// Original full candidate bound, separate from independent bounded payload streams.
const MAX_RECORD: usize = 256 * 1024 * 1024;

/// Complete original operation, retained before source bytes or application Commit.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct IngressIntent {
    /// Explicit first-admission interpretation.
    schema: String,
    /// Original explicit command; duplicates never create another stream or Commit.
    command_id: String,
    /// Independently matched original owned Child identity.
    process: ControllerProcessIdentity,
    /// Actual captured leaf from the original mutual-TLS connection.
    peer_leaf: String,
    /// Complete immutable original semantic/recovery/content candidate.
    candidate: Box<ContentApplicationBatch>,
    /// Actual original committed membership and complete qualified input set.
    target: ContentQuorumTarget,
    /// Actual receiver kernel budget sampled before any parent-writer queue.
    budget: ContentBudget,
}

/// Original complete quorum result; a later observation never replaces this first outcome.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct IngressReceipt {
    /// Original successful metadata Commit result.
    commit: CommitReceipt,
    /// Actual same metadata/full-byte voters in every original current/joint group.
    complete_members: BTreeSet<u64>,
    /// Actual original same-kernel completion time.
    completed_ms: u64,
}

/// Retained original uncertainty is independent of a child connection or a surviving partial file.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct IngressRecord {
    /// Exact complete first admission.
    intent: IngressIntent,
    /// Independent indexed hash of that complete original intent.
    request_digest: String,
    /// Original successful complete quorum, absent while uncertain.
    receipt: Option<IngressReceipt>,
}

/// Read-only complete original operation evidence; this DTO cannot authorize another attempt.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ControllerIngressStatus {
    /// Explicit original observation interpretation.
    pub schema: String,
    /// Original immutable command identity.
    pub command_id: String,
    /// Exact original persisted request digest.
    pub request_digest: String,
    /// Complete original first record, including its original budget and nullable result.
    pub original: serde_json::Value,
}

/// New first admission is the only operation which can create source or application effects.
enum IngressAdmission {
    /// Newly durable original candidate before any Ready or payload source opening.
    New(Box<IngressRecord>),
    /// Exact original evidence only, including Unknown and expired first budgets.
    Existing(Box<ControllerIngressStatus>),
}

/// Actual accepted Unix/TLS evidence, never deserialized from caller-provided PID/permission fields.
#[derive(Clone)]
struct ControllerPeer {
    /// Actual kernel peer PID on the accepted original Unix socket.
    pid: u32,
    /// Original expected spawn identity supplied separately from the kernel PID.
    instance: String,
    /// Original issuing epoch, never rebound after leadership changes.
    epoch: ControllerEpoch,
    /// Captured successful original mutual-TLS handshake.
    connection: crate::tls::VerifiedTlsConnection,
}

/// Hashes complete original serialized evidence, preserving the same bytes through readonly retry.
fn identity(value: &impl Serialize) -> Result<String, String> {
    Ok(format!(
        "{:x}",
        Sha256::digest(serde_json::to_vec(value).map_err(|error| error.to_string())?)
    ))
}

/// Exposes retained original evidence without inventing a completion time or refreshing a budget.
fn status(record: &IngressRecord) -> Result<ControllerIngressStatus, String> {
    Ok(ControllerIngressStatus {
        schema: "roboguide.controller-content-ingress-status/v0.1".into(),
        command_id: record.intent.command_id.clone(),
        request_digest: record.request_digest.clone(),
        original: serde_json::to_value(record).map_err(|error| error.to_string())?,
    })
}

/// Verifies retained shape without asking an expired original intent to become live again.
fn validate_record(record: &IngressRecord) -> Result<(), String> {
    let intent = &record.intent;
    crate::supervision::validate_process(&intent.process)?;
    intent.budget.validate()?;
    intent.target.validate()?;
    intent
        .candidate
        .batch
        .validate()
        .map_err(|error| error.to_string())?;
    if intent.schema != JOURNAL_SCHEMA
        || intent.command_id.is_empty()
        || intent.command_id.len() > 128
        || intent.command_id.chars().any(char::is_control)
        || intent.process.epoch != intent.candidate.epoch
        || intent.target.epoch != intent.process.epoch
        || intent.target.batch_digest != intent.candidate.identity()?
        || intent.target.checkpoint_digest
            != state::checkpoint_identity(&intent.candidate.batch.checkpoint)
        || intent.target.inputs != intent.candidate.inputs
        || intent.target.profile_identity != intent.candidate.profile_identity
        || !intent
            .peer_leaf
            .strip_prefix("sha256:")
            .is_some_and(crate::content::hex_digest)
        || record.request_digest != identity(intent)?
    {
        return Err("original Controller ingress identity/candidate/target changed".into());
    }
    if let Some(receipt) = &record.receipt
        && (!receipt.commit.accepted
            || receipt.commit.revision != intent.target.revision
            || receipt.commit.checkpoint_digest.as_deref()
                != Some(intent.target.checkpoint_digest.as_str())
            || !intent.budget.validates_completion(receipt.completed_ms)
            || receipt
                .complete_members
                .iter()
                .any(|member| !intent.target.member_stores.contains_key(member))
            || intent
                .target
                .membership
                .membership()
                .get_joint_config()
                .iter()
                .any(|voters| {
                    voters.intersection(&receipt.complete_members).count() <= voters.len() / 2
                }))
    {
        return Err("original Controller ingress quorum receipt is incomplete or changed".into());
    }
    Ok(())
}

/// Creates only the mandatory empty new-generation table and head under original format transaction.
pub(crate) fn initialize_storage(connection: &Connection) -> Result<(), String> {
    connection.execute_batch("CREATE TABLE controller_content_ingress(command_id TEXT PRIMARY KEY,request_digest TEXT NOT NULL,record TEXT NOT NULL);")
        .map_err(|error|error.to_string())?;
    write_meta(connection, SCHEMA_KEY, &JOURNAL_SCHEMA).map_err(|error| error.to_string())?;
    write_meta(connection, HEAD_KEY, &BTreeSet::<String>::new()).map_err(|error| error.to_string())
}

/// Requires mandatory original schema/head without treating a missing row as an empty history.
pub(crate) fn require_marker(connection: &Connection) -> Result<BTreeSet<String>, String> {
    if read_meta::<String>(connection, SCHEMA_KEY)
        .map_err(|error| error.to_string())?
        .as_deref()
        != Some(JOURNAL_SCHEMA)
    {
        return Err("mandatory original Controller ingress schema is missing or changed".into());
    }
    let ids: BTreeSet<String> = read_meta(connection, HEAD_KEY)
        .map_err(|error| error.to_string())?
        .ok_or("mandatory original Controller ingress inventory is absent")?;
    if ids.len() > MAX_OPERATIONS
        || ids
            .iter()
            .any(|id| id.is_empty() || id.len() > 128 || id.chars().any(char::is_control))
    {
        return Err("original Controller ingress inventory exceeds its bound or changed".into());
    }
    Ok(ids)
}

/// Verifies the exact complete local command inventory, independent of Raft Snapshot installation.
pub(crate) fn validate_storage(connection: &Connection, enabled: bool) -> Result<(), String> {
    let table: Option<String> = connection
        .query_row(
            "SELECT type FROM sqlite_master WHERE name='controller_content_ingress'",
            [],
            |row| row.get(0),
        )
        .optional()
        .map_err(|error| error.to_string())?;
    if !enabled {
        if table.is_some()
            || read_meta::<serde_json::Value>(connection, SCHEMA_KEY)
                .map_err(|error| error.to_string())?
                .is_some()
            || read_meta::<serde_json::Value>(connection, HEAD_KEY)
                .map_err(|error| error.to_string())?
                .is_some()
        {
            return Err(
                "old Replica storage contains hidden original Controller ingress authority".into(),
            );
        }
        return Ok(());
    }
    if table.as_deref() != Some("table") {
        return Err("mandatory original Controller ingress table is missing or changed".into());
    }
    let ids = require_marker(connection)?;
    let actual = connection
        .prepare("SELECT command_id FROM controller_content_ingress LIMIT 1025")
        .map_err(|error| error.to_string())?
        .query_map([], |row| row.get::<_, String>(0))
        .map_err(|error| error.to_string())?
        .collect::<Result<BTreeSet<_>, _>>()
        .map_err(|error| error.to_string())?;
    if ids != actual {
        return Err("original Controller ingress inventory differs from retained commands".into());
    }
    for id in ids {
        let original = journal::record(connection, &id)?
            .ok_or("original Controller ingress command is absent")?;
        crate::supervision::require_retained_process(connection, &original.intent.process)?;
    }
    Ok(())
}

/// Confirms actual empty original ingress authority only for fresh certificate genesis.
pub(crate) fn empty_storage(connection: &Connection) -> Result<bool, String> {
    validate_storage(connection, true)?;
    Ok(require_marker(connection)?.is_empty())
}

impl ReplicaNode {
    /// Rechecks captured original TLS, real owned PID and current authority without parent/root inversion.
    fn require_controller_peer(
        &self,
        peer: &ControllerPeer,
    ) -> Result<ControllerProcessIdentity, String> {
        if !self.store.controller_content
            || self.current_epoch() != Some(peer.epoch)
            || peer.epoch.replica_id != self.config.replica_id
        {
            return Err("original owned Controller content epoch is unavailable".into());
        }
        self.store.read_network_snapshot(|connection| {
            let trust: CertificateTrust = read_meta(connection, "certificate_trust")
                .map_err(|error| error.to_string())?
                .ok_or("original Controller TLS authority absent")?;
            trust.validate()?;
            peer.connection.authorize_with_trust(
                self.config.replica_id,
                self.config.replica_id,
                &trust,
            )?;
            crate::store::require_controller_source_unfenced(connection, peer.epoch)?;
            crate::supervision::require_original_peer(
                connection,
                &self.process_instance,
                peer.epoch,
                &peer.instance,
                peer.pid,
            )
        })
    }

    /// Reads one actual original operation without granting a new effect or renewing its first budget.
    pub fn controller_ingress_status(
        &self,
        command: &str,
    ) -> Result<Option<ControllerIngressStatus>, String> {
        if !self.store.controller_content {
            return Err("original Controller ingress requires explicit parent8".into());
        }
        self.store.read_network_snapshot(|connection| {
            let ids = require_marker(connection)?;
            let record = journal::record(connection, command)?;
            if ids.contains(command) != record.is_some() {
                return Err("original Controller ingress command inventory changed".into());
            }
            record.as_ref().map(status).transpose()
        })
    }
}
