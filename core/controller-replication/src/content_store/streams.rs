//! Original unique bounded receive scopes; data receipts never grant semantic use or quorum authority.

use super::copy::digest;
use super::*;
use crate::{ApplicationArtifactWriter, ContentQuorumTarget, ControllerProcessIdentity};
use rusqlite::OptionalExtension;
use std::collections::BTreeSet;
use std::io::Read;

#[path = "stream_writer.rs"]
mod writer;
pub(crate) use writer::{StreamAdmission, StreamPermission};

impl ReplicaContentStore {
    /// Copies real owned Artifact bytes after durable admission; this is a blocking storage API.
    /// Target data is retention context, never proof of live consensus or a network permission.
    pub fn copy_artifact_stream(
        &self,
        source: &artifact_store::FileSystemArtifactStore,
        target: ContentQuorumTarget,
        content: &ArtifactContentKey,
        command: &str,
        duration_ms: u64,
    ) -> Result<ReplicaContentStreamStatus, String> {
        let budget = ContentBudget::issue(duration_ms)?;
        self.copy_artifact_stream_at_budget(
            source,
            target,
            content,
            command,
            budget,
            Arc::new(|| Ok(())),
        )
    }

    /// Retains an actual pre-queue clock and trusted live adapter permission during local source staging.
    pub(crate) fn copy_artifact_stream_at_budget(
        &self,
        source: &artifact_store::FileSystemArtifactStore,
        target: ContentQuorumTarget,
        content: &ArtifactContentKey,
        command: &str,
        budget: ContentBudget,
        source_permission: StreamPermission,
    ) -> Result<ReplicaContentStreamStatus, String> {
        budget.check()?;
        source_permission()?;
        let actual = source
            .writer_identity()
            .map_err(|error| error.to_string())?;
        let writer = ApplicationArtifactWriter {
            owner: actual.owner_id,
            device: actual.lease_device,
            inode: actual.lease_inode,
        };
        let expected = writer.clone();
        let held = source.clone();
        let permission: StreamPermission = Arc::new(move || {
            source_permission()?;
            let current = held.writer_identity().map_err(|error| error.to_string())?;
            if (current.owner_id, current.lease_device, current.lease_inode)
                != (expected.owner.clone(), expected.device, expected.inode)
            {
                return Err("original Artifact stream source writer changed".into());
            }
            Ok(())
        });
        let admission = self.admit_stream(
            command,
            target,
            content.clone(),
            StreamOrigin::Artifact { writer },
            budget,
            permission,
        )?;
        let mut receiver = match admission {
            StreamAdmission::Existing(original) => return Ok(*original),
            StreamAdmission::New(receiver) => receiver,
        };
        receiver.check_before_source_read()?;
        let mut input = source
            .open_replication_source(&content.content_digest, content.byte_size)
            .map_err(|error| error.to_string())?;
        let mut buffer = [0u8; CHUNK_BYTES];
        let mut offset = 0u64;
        loop {
            receiver.check_before_source_read()?;
            let count = input.read(&mut buffer).map_err(|error| error.to_string())?;
            if count == 0 {
                break;
            }
            receiver.write_chunk(offset, &buffer[..count])?;
            offset = offset
                .checked_add(count as u64)
                .ok_or("original stream source offset overflow")?;
        }
        drop(input);
        receiver.finish()
    }
}

#[cfg(test)]
#[path = "stream_tests.rs"]
mod tests;

/// Exact new byte-receipt interpretation; legacy copy receipts retain their original schema.
pub(super) const RECEIPT_SCHEMA: &str = "roboguide.replica-content-stream-receipt/v0.1";
/// Per-original receive bound, independent of aggregate metadata, semantic resources or quotas.
const MAX_STREAM_BYTES: u64 = 1024 * 1024 * 1024;
/// Retained IDs never disappear to make room for an uncertain old operation.
const MAX_STREAMS: usize = 4096;
/// Fixed actual byte work chunk; no payload-sized buffer is allocated from a remote length.
pub(crate) const CHUNK_BYTES: usize = 64 * 1024;

/// Original source evidence; only trusted actual adapters may construct an admitted live scope.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "kebab-case", deny_unknown_fields)]
pub(crate) enum StreamOrigin {
    /// Actual filesystem-owner source, held by its original managed writer for local copy.
    Artifact {
        /// Independently sampled original source writer, never a caller-selected replacement owner.
        writer: ApplicationArtifactWriter,
    },
    /// Actual authenticated source infrastructure member and its exact committed software root.
    Replica {
        /// Actual connection member, independent of an embodied Node ID.
        member: u64,
        /// Exact source member root in this original target membership.
        store_owner: String,
        /// Actual verified peer leaf on the original connection.
        peer_leaf: String,
    },
    /// Actual original owned Controller ingress, distinct from a peer root's forwarding permission.
    Controller {
        /// Actual registered original process; PID labels supplied by HTTP never prove this identity.
        process: ControllerProcessIdentity,
        /// Actual original Artifact writer retained by the sending process.
        writer: ApplicationArtifactWriter,
        /// Original actual source member TLS leaf.
        peer_leaf: String,
    },
}

/// Immutable first receive intent, persisted before a staging FD or any source payload read.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct StreamIntent {
    /// Closed original operation interpretation.
    schema: String,
    /// Exact original command; duplicate IDs never produce another writer.
    command_id: String,
    /// Actual original receiving root; a copied descriptor cannot lend its ownership.
    receiver: ReplicaContentIdentity,
    /// Complete original metadata/membership/qualified input target, not a success boolean.
    target: ContentQuorumTarget,
    /// One exact original qualified input within the complete target.
    content: ArtifactContentKey,
    /// Actual source evidence supplied only by its trusted live adapter.
    origin: StreamOrigin,
    /// Actual first receiver process/kernel issue and immutable effect duration.
    budget: ContentBudget,
}

/// Retained original receive result; actual scope end never invents publication.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct StreamRecord {
    /// Complete immutable first admission.
    intent: StreamIntent,
    /// Complete exact first input identity, also fenced in an indexed original SQL column.
    request_digest: String,
    /// Actual original durable full-byte publication, absent while uncertain.
    receipt: Option<ReplicaContentReceipt>,
    /// Actual original worker FD scope closed, independently of its effect budget.
    worker_closed_ms: Option<u64>,
}

/// Complete readonly original observation; it never admits I/O or renews an original deadline.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReplicaContentStreamStatus {
    /// Closed local observation interpretation.
    pub schema: String,
    /// Exact original command, including after an uncertain interruption.
    pub command_id: String,
    /// Complete immutable first-admission digest.
    pub request_digest: String,
    /// Original full intent and actual receipt/scope evidence; no fields are reconstructed on read.
    pub original: serde_json::Value,
    /// Original publication, independently of current availability or semantic permission.
    pub receipt: Option<ReplicaContentReceipt>,
}

/// Checks an actual authenticated peer's complete original observation against this exact request.
pub(crate) fn verify_stream_observation(
    observed: &ReplicaContentStreamStatus,
    target: &ContentQuorumTarget,
    content: &ArtifactContentKey,
    command: &str,
    receiver: u64,
    source: &StreamOrigin,
) -> Result<(), String> {
    let original: StreamRecord =
        serde_json::from_value(observed.original.clone()).map_err(|error| error.to_string())?;
    if &original.intent.origin != source
        || !matches!(source, StreamOrigin::Replica { member, .. }
        if target.membership.voter_ids().any(|voter| voter == *member))
    {
        return Err("actual peer stream observation changed its original source kind".into());
    }
    verify_original_stream_observation(
        observed,
        target,
        content,
        command,
        receiver,
        &original.intent.origin,
    )
}

/// Checks a trusted adapter's exact original source; Controller and Replica origins cannot substitute.
pub(crate) fn verify_original_stream_observation(
    observed: &ReplicaContentStreamStatus,
    target: &ContentQuorumTarget,
    content: &ArtifactContentKey,
    command: &str,
    receiver: u64,
    origin: &StreamOrigin,
) -> Result<(), String> {
    let original: StreamRecord =
        serde_json::from_value(observed.original.clone()).map_err(|error| error.to_string())?;
    let intent = &original.intent;
    validate_intent(intent, &intent.receiver)?;
    if observed.schema != "roboguide.replica-content-stream-status/v0.1"
        || observed.command_id != command
        || intent.command_id != command
        || intent.target != *target
        || intent.content != *content
        || intent.receiver.member != receiver
        || intent.receiver.root_inode == 0
        || intent.receiver.database_inode == 0
        || original.request_digest != digest(intent)?
        || observed.request_digest != original.request_digest
        || observed.receipt != original.receipt
        || intent.origin != *origin
    {
        return Err("actual peer stream observation changed its original request/receiver".into());
    }
    if let Some(receipt) = &original.receipt
        && (receipt.schema != RECEIPT_SCHEMA
            || receipt.request_digest != original.request_digest
            || receipt.command_id != command
            || receipt.content != *content
            || receipt.member != receiver
            || receipt.store_owner != intent.receiver.store_owner
            || receipt.profile_identity != target.profile_identity
            || receipt.file_inode == 0
            || !intent.budget.validates_completion(receipt.completed_ms)
            || original
                .worker_closed_ms
                .is_none_or(|closed| closed < receipt.completed_ms))
    {
        return Err(
            "actual peer stream receipt differs from its original byte/budget evidence".into(),
        );
    }
    Ok(())
}

/// Initializes only the explicit fresh/upgrading v4 journal in its original SQL transaction.
pub(super) fn initialize(connection: &Connection) -> Result<(), String> {
    connection.execute_batch("CREATE TABLE content_streams(stream_id TEXT PRIMARY KEY,request_digest TEXT NOT NULL,record TEXT NOT NULL)").map_err(|error| error.to_string())?;
    layout::write_head(connection, "stream_ids", &BTreeSet::new())
}

/// Derives the path-safe original receive identity without interpreting its textual command as a path.
fn operation_id(command: &str) -> Result<String, String> {
    digest(&command)
}

/// Observes exactly retained original evidence; this never samples a replacement completion time.
fn status(original: &StreamRecord) -> Result<ReplicaContentStreamStatus, String> {
    Ok(ReplicaContentStreamStatus {
        schema: "roboguide.replica-content-stream-status/v0.1".into(),
        command_id: original.intent.command_id.clone(),
        request_digest: original.request_digest.clone(),
        original: serde_json::to_value(original).map_err(|error| error.to_string())?,
        receipt: original.receipt.clone(),
    })
}

/// Retains this exact stream's independent pin, never reusing a local copy or reader reference.
fn reference(intent: &StreamIntent) -> Result<ReplicaContentReference, String> {
    Ok(ReplicaContentReference {
        reference_id: format!("stream:{}", operation_id(&intent.command_id)?),
        kind: ReplicaContentReferenceKind::Stream,
        content: intent.content.clone(),
        authority_digest: intent.target.batch_digest.clone(),
        generation: 1,
    })
}

/// Reads only an existing complete original record; loss never means another stream may execute.
fn record(connection: &Connection, id: &str) -> Result<Option<StreamRecord>, String> {
    let length: Option<u64> = connection
        .query_row(
            "SELECT length(CAST(record AS BLOB)) FROM content_streams WHERE stream_id=?1",
            [id],
            |row| row.get(0),
        )
        .optional()
        .map_err(|error| error.to_string())?;
    if length.is_some_and(|length| length > 8 * 1024 * 1024) {
        return Err("original stream record exceeds its fixed bound".into());
    }
    let row: Option<(String, String)> = connection
        .query_row(
            "SELECT request_digest,record FROM content_streams WHERE stream_id=?1",
            [id],
            |row| Ok((row.get(0)?, row.get(1)?)),
        )
        .optional()
        .map_err(|error| error.to_string())?;
    row.map(|(expected, raw)| {
        let value: StreamRecord = serde_json::from_str(&raw).map_err(|error| error.to_string())?;
        if value.request_digest != expected
            || value.request_digest != digest(&value.intent)?
            || operation_id(&value.intent.command_id)? != id
        {
            return Err("original stream record lost its exact immutable SQL identity".into());
        }
        Ok(value)
    })
    .transpose()
}

/// Validates immutable shape without converting an expired record into a current permission.
fn validate_intent(intent: &StreamIntent, identity: &ReplicaContentIdentity) -> Result<(), String> {
    intent.budget.validate()?;
    intent.content.validate()?;
    intent.target.validate()?;
    if intent.schema != "roboguide.replica-content-stream/v0.1"
        || intent.receiver != *identity
        || intent.command_id.is_empty()
        || intent.command_id.len() > 128
        || intent.command_id.chars().any(char::is_control)
        || intent.content.byte_size > MAX_STREAM_BYTES
        || intent.target.profile_identity != identity.profile_identity
        || !intent.target.membership.nodes().any(|(member, data)| {
            *member == identity.member
                && data.content_store_owner.as_ref() == Some(&identity.store_owner)
        })
        || intent.target.inputs.binary_search(&intent.content).is_err()
    {
        return Err("original stream target/receiver/qualified input differs".into());
    }
    let writer = match &intent.origin {
        StreamOrigin::Artifact { writer } => Some(writer),
        StreamOrigin::Controller {
            process,
            writer,
            peer_leaf,
        } => {
            crate::supervision::validate_process(process)?;
            if process.epoch != intent.target.epoch
                || process.epoch.replica_id != identity.member
                || !peer_leaf
                    .strip_prefix("sha256:")
                    .is_some_and(crate::content::hex_digest)
            {
                return Err("original stream Controller/source connection evidence differs".into());
            }
            Some(writer)
        }
        StreamOrigin::Replica {
            member,
            store_owner,
            peer_leaf,
        } => {
            let source = intent
                .target
                .membership
                .nodes()
                .find_map(|(id, node)| (id == member).then_some(node));
            if source.and_then(|source| source.content_store_owner.as_ref()) != Some(store_owner)
                || !peer_leaf
                    .strip_prefix("sha256:")
                    .is_some_and(crate::content::hex_digest)
            {
                return Err(
                    "original stream source differs from its committed member/connection".into(),
                );
            }
            None
        }
    };
    if let Some(writer) = writer
        && (writer.owner != intent.content.source_owner || writer.inode == 0)
    {
        return Err("original stream Artifact writer/source owner differs".into());
    }
    Ok(())
}

/// Requires the original completed stream behind a generic qualified object receipt.
pub(super) fn validate_object_receipt(
    connection: &Connection,
    receipt: &ReplicaContentReceipt,
) -> Result<(), String> {
    let original = record(connection, &operation_id(&receipt.command_id)?)?
        .ok_or("original stream object lost its receive authority")?;
    if original.receipt.as_ref() != Some(receipt) {
        return Err("original stream object differs from its actual receive receipt".into());
    }
    Ok(())
}

/// Validates the full mandatory original inventory, source, independent pins and receipt budgets.
pub(super) fn validate_records(
    connection: &Connection,
    identity: &ReplicaContentIdentity,
) -> Result<(), String> {
    let heads = layout::head(connection, "stream_ids")?;
    if heads.len() > MAX_STREAMS {
        return Err("original stream history exceeds its fixed bound".into());
    }
    let mut pins = BTreeSet::new();
    for id in heads {
        let original =
            record(connection, &id)?.ok_or("original stream head lost its complete record")?;
        let intent = &original.intent;
        validate_intent(intent, identity)?;
        let expected = reference(intent)?;
        let pin = reference_record(connection, &expected.reference_id)?
            .ok_or("original stream lost its independent receive pin")?;
        if pin.reference != expected || !pin.active || pin.release_digest.is_some() {
            return Err("original stream pin lost its retained responsibility".into());
        }
        if original
            .worker_closed_ms
            .is_some_and(|closed| !intent.budget.validates_close(closed))
        {
            return Err("original stream scope closure changed its actual original clock".into());
        }
        if let Some(receipt) = &original.receipt
            && (receipt.schema != RECEIPT_SCHEMA
                || receipt.command_id != intent.command_id
                || receipt.request_digest != original.request_digest
                || receipt.content != intent.content
                || receipt.store_owner != identity.store_owner
                || receipt.member != identity.member
                || receipt.profile_identity != identity.profile_identity
                || receipt.file_inode == 0
                || original
                    .worker_closed_ms
                    .is_none_or(|closed| closed < receipt.completed_ms)
                || !intent.budget.validates_completion(receipt.completed_ms))
        {
            return Err(
                "original stream receipt lost its exact byte/source/budget evidence".into(),
            );
        }
        pins.insert(expected.reference_id);
    }
    for id in layout::head(connection, "reference_ids")? {
        let pin =
            reference_record(connection, &id)?.ok_or("original stream reference disappeared")?;
        if pin.reference.kind == ReplicaContentReferenceKind::Stream && !pins.contains(&id) {
            return Err("original stream receive pin lost its journal authority".into());
        }
    }
    Ok(())
}
