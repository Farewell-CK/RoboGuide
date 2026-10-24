//! Original durable Snapshot receive prerequisites; content-ready and parent application stay separate.

use super::copy::digest;
use super::*;
use crate::ContentQuorumTarget;
use openraft::SnapshotMeta;
use rusqlite::OptionalExtension;
use std::collections::BTreeSet;
use std::os::unix::fs::MetadataExt;

/// Complete bounded original header and all receipts, independent of aggregate SQL storage bounds.
const MAX_RECEIVE_RECORD: usize = 32 * 1024 * 1024;

#[cfg(test)]
#[path = "snapshot_receive_tests.rs"]
mod tests;

/// Complete authenticated source scope, independently of a serialized readiness flag.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SnapshotContentHeader {
    /// Closed original receive interpretation.
    pub schema: String,
    /// Actual source member on the original mutually authenticated connection.
    pub source_member: u64,
    /// Exact source Root owner in the original routing membership.
    pub source_store_owner: String,
    /// Exact actual source certificate leaf on that connection.
    pub source_leaf: String,
    /// Complete original Snapshot pointers and identity, not the receiver's application state.
    pub meta: SnapshotMeta<u64, crate::ReplicaMember>,
    /// Exact original complete metadata payload SHA-256, independently checked on install.
    pub payload_digest: String,
    /// Current original authority plus exact historical Snapshot candidate and qualified inputs.
    pub target: ContentQuorumTarget,
}

/// Immutable original receiver issue, distinct from source and any future restored backing.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Intent {
    /// Exact deterministic original source/receiver/Snapshot operation.
    command_id: String,
    /// Actual original managed receiving store.
    receiver: ReplicaContentIdentity,
    /// Complete original source, Snapshot and input scope.
    header: SnapshotContentHeader,
    /// Original actual receiver kernel/process budget, sampled before queue admission.
    budget: ContentBudget,
}

/// Original complete byte verification; neither a parent Commit nor a new semantic permission.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SnapshotContentReady {
    /// Complete original issue identity, including its original clock.
    pub intent_digest: String,
    /// Exact original byte receipts independently verified against actual files.
    pub inputs: Vec<ReplicaContentReceipt>,
    /// Actual per-input verification device/inode in this original issue's backing, separate from old receipts.
    pub verified_files: Vec<(u64, u64)>,
    /// Actual completion within the original receiving kernel budget.
    pub completed_ms: u64,
}

/// Immutable retained operation: original Unknown, byte readiness and retirement are separate facts.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Record {
    /// Complete first issue before any new source read or receiver payload effect.
    intent: Intent,
    /// Original actual full byte verification; absent after uncertain interruption.
    ready: Option<SnapshotContentReady>,
    /// Exact independently observed parent publication proof; absent while retained.
    retirement: Option<String>,
}

/// Readonly evidence; parsing this DTO never creates receive, application or read authority.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SnapshotContentStatus {
    /// Closed readonly observation interpretation.
    pub schema: String,
    /// Exact original command, unchanged on retry or restart.
    pub command_id: String,
    /// Complete original issue identity.
    pub intent_digest: String,
    /// Exact original receiver/header/budget and outcomes without normalization.
    pub original: serde_json::Value,
    /// Original actual byte-ready receipt; it does not prove Snapshot application.
    pub ready: Option<SnapshotContentReady>,
    /// Exact parent-based retirement, separate from original byte completion.
    pub retirement: Option<String>,
}

/// Fixes one source/receiver/Snapshot ID; epoch and retries cannot replace original uncertainty.
pub(crate) fn operation(header: &SnapshotContentHeader, receiver: u64) -> Result<String, String> {
    Ok(format!(
        "snapshot-receive:{}",
        digest(&(
            &header.target.profile_identity,
            header.source_member,
            receiver,
            &header.meta.snapshot_id,
            &header.payload_digest,
        ))?
    ))
}

/// Validates actual identity bindings without converting them into a fresh permission.
fn validate_header(
    header: &SnapshotContentHeader,
    receiver: &ReplicaContentIdentity,
) -> Result<(), String> {
    header.target.validate()?;
    if header.schema != "roboguide.snapshot-content-header/v0.1"
        || !crate::content::hex_digest(&header.payload_digest)
        || header.meta.snapshot_id.is_empty()
        || header.meta.snapshot_id.len() > 512
        || header.meta.last_log_id.is_none()
        || header.source_member != header.target.epoch.replica_id
        || header.meta.snapshot_id
            != format!(
                "{}-{}-{}",
                header.meta.last_log_id.map_or(0, |id| id.index),
                header.target.revision,
                header.payload_digest
            )
        || header.source_member == receiver.member
        || header.target.profile_identity != receiver.profile_identity
        || header
            .source_leaf
            .strip_prefix("sha256:")
            .is_none_or(|leaf| !crate::content::hex_digest(leaf))
        || header.target.member_stores.get(&header.source_member)
            != Some(&header.source_store_owner)
        || !header.target.membership.nodes().any(|(id, data)| {
            *id == receiver.member
                && data.content_store_owner.as_ref() == Some(&receiver.store_owner)
        })
    {
        return Err("original Snapshot source/receiver/metadata identity differs".into());
    }
    Ok(())
}

/// Retains this receive's exact independent pin rather than borrowing a stream or Snapshot publisher.
fn references(intent: &Intent) -> Result<Vec<ReplicaContentReference>, String> {
    let authority = digest(intent)?;
    intent
        .header
        .target
        .inputs
        .iter()
        .map(|content| {
            Ok(ReplicaContentReference {
                reference_id: format!(
                    "snapshot-receive:{}:{}",
                    digest(&intent.command_id)?,
                    digest(content)?
                ),
                kind: ReplicaContentReferenceKind::SnapshotReceive,
                content: content.clone(),
                authority_digest: authority.clone(),
                generation: 1,
            })
        })
        .collect()
}

/// Reads one bounded exact original operation; absence does not erase a mandatory head.
fn record(sql: &Connection, id: &str) -> Result<Option<Record>, String> {
    let length: Option<usize> = sql.query_row(
        "SELECT length(CAST(record AS BLOB)) FROM content_snapshot_receives WHERE operation_id=?1",
        [id], |row| row.get(0),
    ).optional().map_err(|e| e.to_string())?;
    if length.is_some_and(|length| length > MAX_RECEIVE_RECORD) {
        return Err("original Snapshot receive exceeds its record bound".into());
    }
    let raw: Option<String> = sql
        .query_row(
            "SELECT record FROM content_snapshot_receives WHERE operation_id=?1",
            [id],
            |row| row.get(0),
        )
        .optional()
        .map_err(|e| e.to_string())?;
    raw.map(|raw| serde_json::from_str(&raw).map_err(|e| e.to_string()))
        .transpose()
}

/// Checks size before writing; an over-bound record never becomes an absent original operation.
fn encode(original: &Record) -> Result<String, String> {
    let raw = serde_json::to_string(original).map_err(|e| e.to_string())?;
    if raw.len() > MAX_RECEIVE_RECORD {
        return Err("original Snapshot receive exceeds its record bound".into());
    }
    Ok(raw)
}

/// Resolves the actual retained byte receipt without treating its history as current file availability.
fn object_receipt(
    sql: &Connection,
    content: &ArtifactContentKey,
) -> Result<ReplicaContentReceipt, String> {
    let raw: String = sql
        .query_row(
            "SELECT record FROM content_objects WHERE content_key=?1",
            [digest(content)?],
            |row| row.get(0),
        )
        .map_err(|e| e.to_string())?;
    if raw.len() > 16384 {
        return Err("original Snapshot object receipt exceeds its bound".into());
    }
    serde_json::from_str(&raw).map_err(|e| e.to_string())
}

/// Preserves exact immutable original issue/outcomes instead of manufacturing a new deadline.
fn status(original: &Record) -> Result<SnapshotContentStatus, String> {
    Ok(SnapshotContentStatus {
        schema: "roboguide.snapshot-content-status/v0.1".into(),
        command_id: original.intent.command_id.clone(),
        intent_digest: digest(&original.intent)?,
        original: serde_json::to_value(original).map_err(|e| e.to_string())?,
        ready: original.ready.clone(),
        retirement: original.retirement.clone(),
    })
}

/// Creates only the new explicit drained generation's complete mandatory empty inventories.
pub(super) fn initialize(sql: &Connection, restored: bool) -> Result<(), String> {
    sql.execute_batch("CREATE TABLE content_snapshot_receives(operation_id TEXT PRIMARY KEY,record TEXT NOT NULL)").map_err(|e| e.to_string())?;
    layout::write_head(sql, "snapshot_receive_ids", &BTreeSet::new())?;
    sql.execute(
        "INSERT INTO metadata(key,value) VALUES ('backing_mode',?1)",
        [if restored { "restored" } else { "original" }],
    )
    .map_err(|e| e.to_string())?;
    Ok(())
}

/// Checks every original row, byte proof and independently retained pin behind the mandatory head.
pub(super) fn validate_records(
    sql: &Connection,
    receiver: &ReplicaContentIdentity,
) -> Result<(), String> {
    let mut pins = BTreeSet::new();
    let operations = layout::head(sql, "snapshot_receive_ids")?;
    if operations.len() > 4096 {
        return Err("Snapshot receive history exceeds its original bound".into());
    }
    for id in operations {
        let original = record(sql, &id)?.ok_or("original Snapshot receive head lost its record")?;
        let intent = &original.intent;
        validate_header(&intent.header, receiver)?;
        intent.budget.validate()?;
        if intent.receiver != *receiver
            || operation(&intent.header, receiver.member)? != intent.command_id
            || digest(&intent.command_id)? != id
        {
            return Err("original Snapshot receive command/receiver changed".into());
        }
        if let Some(ready) = &original.ready {
            if ready.intent_digest != digest(intent)?
                || !intent.budget.validates_completion(ready.completed_ms)
                || ready.verified_files.len() != ready.inputs.len()
                || ready.verified_files.iter().any(|(_, inode)| *inode == 0)
                || ready
                    .inputs
                    .iter()
                    .map(|receipt| &receipt.content)
                    .collect::<Vec<_>>()
                    != intent.header.target.inputs.iter().collect::<Vec<_>>()
                || ready.inputs.iter().any(|receipt| {
                    receipt.store_owner != receiver.store_owner
                        || receipt.member != receiver.member
                        || receipt.profile_identity != receiver.profile_identity
                        || receipt.file_inode == 0
                        || !crate::content::hex_digest(&receipt.request_digest)
                })
            {
                return Err("original Snapshot byte-ready receipt differs from its exact issue/inputs/budget".into());
            }
            for receipt in &ready.inputs {
                if receipt.schema == super::streams::RECEIPT_SCHEMA {
                    super::streams::validate_object_receipt(sql, receipt)?;
                } else {
                    super::copy::validate_original_receipt(sql, receipt)?;
                }
                if original.retirement.is_some() {
                    continue;
                }
                if object_receipt(sql, &receipt.content)? != *receipt {
                    return Err(
                        "original Snapshot ready receipt changed its actual object origin".into(),
                    );
                }
            }
        }
        if original
            .retirement
            .as_ref()
            .is_some_and(|proof| original.ready.is_none() || !crate::content::hex_digest(proof))
        {
            return Err(
                "original Snapshot receive retirement lacks its original byte proof".into(),
            );
        }
        for expected in references(intent)? {
            let pin = reference_record(sql, &expected.reference_id)?
                .ok_or("original Snapshot receive lost its independent pin")?;
            if pin.reference != expected
                || pin.active != original.retirement.is_none()
                || pin.release_digest != original.retirement
            {
                return Err("original Snapshot receive pin/source/retirement differs".into());
            }
            pins.insert(expected.reference_id);
        }
    }
    for id in layout::head(sql, "reference_ids")? {
        let pin =
            reference_record(sql, &id)?.ok_or("original Snapshot receive reference disappeared")?;
        if pin.reference.kind == ReplicaContentReferenceKind::SnapshotReceive && !pins.contains(&id)
        {
            return Err("original Snapshot receive pin lost its complete operation journal".into());
        }
    }
    Ok(())
}

impl ReplicaContentStore {
    /// Lists only unresolved byte-ready prerequisites for independent parent-based cleanup, without fresh issue clocks.
    pub(crate) fn pending_snapshot_publications(
        &self,
    ) -> Result<Vec<(String, SnapshotContentHeader)>, String> {
        if !self.inner.snapshot_receives {
            return Ok(Vec::new());
        }
        let guard = self.inner.connection.lock().map_err(|e| e.to_string())?;
        let sql = guard.as_ref().ok_or("Snapshot receive SQL is closed")?;
        self.validate_connection(sql)?;
        let mut pending = Vec::new();
        for id in layout::head(sql, "snapshot_receive_ids")? {
            let row = record(sql, &id)?.ok_or("Snapshot receive head lost its original record")?;
            if row.ready.is_some() && row.retirement.is_none() {
                pending.push((row.intent.command_id, row.intent.header));
            }
        }
        Ok(pending)
    }
    /// Requires exact completed byte prerequisites for the actual complete metadata being installed.
    #[allow(clippy::too_many_arguments)]
    pub(crate) fn require_snapshot_prerequisites(
        &self,
        meta: &SnapshotMeta<u64, crate::ReplicaMember>,
        payload: &str,
        revision: u64,
        checkpoint: &str,
        batch: &str,
        inputs: &[ArtifactContentKey],
    ) -> Result<String, String> {
        let guard = self.inner.connection.lock().map_err(|e| e.to_string())?;
        let sql = guard
            .as_ref()
            .ok_or("original Snapshot receive SQL is closed")?;
        self.validate_connection(sql)?;
        let mut selected = None;
        for id in layout::head(sql, "snapshot_receive_ids")? {
            let original =
                record(sql, &id)?.ok_or("original Snapshot receive head lost its row")?;
            let header = &original.intent.header;
            if header.meta != *meta || header.payload_digest != payload {
                continue;
            }
            if header.target.revision != revision
                || header.target.checkpoint_digest != checkpoint
                || header.target.batch_digest != batch
                || header.target.inputs != inputs
                || original.ready.is_none()
            {
                return Err(
                    "actual Snapshot metadata differs from its original complete prerequisite"
                        .into(),
                );
            }
            original.intent.budget.check()?;
            if original.retirement.is_some() {
                return Err("original Snapshot receive is already retired".into());
            }
            if selected.replace(original.intent.command_id).is_some() {
                return Err("actual Snapshot has conflicting original receive scopes".into());
            }
        }
        selected.ok_or("actual Snapshot has no original complete byte prerequisite".into())
    }

    /// Retires only after trusted actual parent publication; independent original publisher pins remain retained.
    pub(crate) fn retire_snapshot_prerequisites(
        &self,
        command: &str,
        proof: &str,
        publication: impl FnOnce() -> Result<(), String>,
    ) -> Result<(), String> {
        if !crate::content::hex_digest(proof) {
            return Err("Snapshot retirement lacks exact parent proof".into());
        }
        let mut guard = self.inner.connection.lock().map_err(|e| e.to_string())?;
        let sql = guard
            .as_mut()
            .ok_or("original Snapshot receiving SQL is closed")?;
        self.validate_connection(sql)?;
        let id = digest(&command)?;
        let mut original =
            record(sql, &id)?.ok_or("original Snapshot receiving row disappeared")?;
        if original.retirement.as_deref() == Some(proof) {
            return Ok(());
        }
        if original.retirement.is_some() || original.ready.is_none() {
            return Err("Snapshot retirement lacks its original complete readiness".into());
        }
        publication()?;
        original.retirement = Some(proof.into());
        let transaction = sql.transaction().map_err(|e| e.to_string())?;
        for expected in references(&original.intent)? {
            let mut pin = reference_record(&transaction, &expected.reference_id)?
                .ok_or("original Snapshot pin disappeared")?;
            if pin.reference != expected || !pin.active || pin.release_digest.is_some() {
                return Err("original Snapshot pin changed before retirement".into());
            }
            pin.active = false;
            pin.release_digest = Some(proof.into());
            transaction
                .execute(
                    "UPDATE content_references SET record=?1 WHERE reference_id=?2",
                    params![
                        serde_json::to_string(&pin).map_err(|e| e.to_string())?,
                        expected.reference_id
                    ],
                )
                .map_err(|e| e.to_string())?;
        }
        transaction
            .execute(
                "UPDATE content_snapshot_receives SET record=?1 WHERE operation_id=?2",
                params![encode(&original)?, id],
            )
            .map_err(|e| e.to_string())?;
        layout::within_budget(&transaction)?;
        transaction.commit().map_err(|e| e.to_string())?;
        Ok(())
    }
    /// Reports only the actual explicitly opened generation, never serialized authority or readiness.
    pub(crate) fn snapshot_receives_enabled(&self) -> bool {
        self.inner.snapshot_receives
    }

    /// Persists first complete original issue and all independent pins before any receiving payload work.
    pub(crate) fn prepare_snapshot_receive(
        &self,
        header: SnapshotContentHeader,
        budget: ContentBudget,
        permission: StreamPermission,
    ) -> Result<SnapshotContentStatus, String> {
        if !self.inner.snapshot_receives {
            return Err("Snapshot receive requires explicit Root6".into());
        }
        validate_header(&header, self.identity())?;
        let command_id = operation(&header, self.identity().member)?;
        let id = digest(&command_id)?;
        let mut guard = self.inner.connection.lock().map_err(|e| e.to_string())?;
        let sql = guard
            .as_mut()
            .ok_or("original Snapshot receiving SQL is closed")?;
        self.validate_connection(sql)?;
        if let Some(original) = record(sql, &id)? {
            if original.intent.header != header {
                return Err("Snapshot retry changed its original complete header".into());
            }
            return status(&original);
        }
        for input in &header.target.inputs {
            super::gc::require_live(sql, input)?;
        }
        permission()?;
        budget.check()?;
        let original = Record {
            intent: Intent {
                command_id,
                receiver: self.identity().clone(),
                header,
                budget,
            },
            ready: None,
            retirement: None,
        };
        let transaction = sql.transaction().map_err(|e| e.to_string())?;
        let mut operations = layout::head(&transaction, "snapshot_receive_ids")?;
        if operations.len() >= 4096 {
            return Err("Snapshot receiving history reached its fixed bound".into());
        }
        let mut pins = layout::head(&transaction, "reference_ids")?;
        for reference in references(&original.intent)? {
            if !pins.insert(reference.reference_id.clone()) || pins.len() > MAX_REFERENCES {
                return Err(
                    "Snapshot receive pin collides or exceeds original inventory bound".into(),
                );
            }
            transaction
                .execute(
                    "INSERT INTO content_references(reference_id,record) VALUES (?1,?2)",
                    params![
                        reference.reference_id,
                        serde_json::to_string(&ReferenceRecord {
                            reference: reference.clone(),
                            active: true,
                            release_digest: None
                        })
                        .map_err(|e| e.to_string())?
                    ],
                )
                .map_err(|e| e.to_string())?;
        }
        transaction
            .execute(
                "INSERT INTO content_snapshot_receives(operation_id,record) VALUES (?1,?2)",
                params![id, encode(&original)?],
            )
            .map_err(|e| e.to_string())?;
        operations.insert(id);
        layout::write_head(&transaction, "snapshot_receive_ids", &operations)?;
        layout::write_head(&transaction, "reference_ids", &pins)?;
        layout::within_budget(&transaction)?;
        original.intent.budget.check()?;
        permission()?;
        transaction.commit().map_err(|e| e.to_string())?;
        status(&original)
    }

    /// Observes the exact retained original state without refreshing its budget or repeating a transfer.
    pub fn snapshot_receive_status(
        &self,
        command: &str,
    ) -> Result<Option<SnapshotContentStatus>, String> {
        if !self.inner.snapshot_receives {
            return Err("Snapshot receiving observation requires Root6".into());
        }
        let guard = self.inner.connection.lock().map_err(|e| e.to_string())?;
        let sql = guard
            .as_ref()
            .ok_or("original Snapshot receive SQL is closed")?;
        self.validate_connection(sql)?;
        record(sql, &digest(&command)?)?
            .as_ref()
            .map(status)
            .transpose()
    }

    /// Captures the original frozen receive scope before a worker; it never creates or renews an issue.
    pub(crate) fn snapshot_receive_scope(
        &self,
        command: &str,
        header: &SnapshotContentHeader,
    ) -> Result<ContentBudget, String> {
        let guard = self.inner.connection.lock().map_err(|e| e.to_string())?;
        let sql = guard
            .as_ref()
            .ok_or("original Snapshot receiving SQL is closed")?;
        self.validate_connection(sql)?;
        if !self.inner.snapshot_receives {
            return Err("Snapshot receiving requires Root6".into());
        }
        let original = record(sql, &digest(&command)?)?
            .ok_or("original Snapshot receiving issue is absent")?;
        if original.intent.command_id != command
            || original.intent.header != *header
            || original.retirement.is_some()
        {
            return Err("original Snapshot receiving scope changed or retired".into());
        }
        original.intent.budget.check()?;
        Ok(original.intent.budget)
    }

    /// Verifies all actual files within the original budget before atomically publishing byte readiness.
    pub(crate) fn complete_snapshot_receive(
        &self,
        command: &str,
        permission: StreamPermission,
    ) -> Result<SnapshotContentStatus, String> {
        let id = digest(&command)?;
        let original = {
            let guard = self.inner.connection.lock().map_err(|e| e.to_string())?;
            let sql = guard
                .as_ref()
                .ok_or("original Snapshot receiving SQL is closed")?;
            self.validate_connection(sql)?;
            if !self.inner.snapshot_receives {
                return Err("Snapshot receiving requires Root6".into());
            }
            record(sql, &id)?.ok_or("original Snapshot receiving issue is absent")?
        };
        if original.ready.is_some() || original.retirement.is_some() {
            return status(&original);
        }
        let mut receipts = Vec::new();
        let mut verified_files = Vec::new();
        for key in &original.intent.header.target.inputs {
            original.intent.budget.check()?;
            permission()?;
            let receipt = {
                let guard = self.inner.connection.lock().map_err(|e| e.to_string())?;
                object_receipt(
                    guard.as_ref().ok_or("original Snapshot SQL is closed")?,
                    key,
                )?
            };
            let file =
                copy::verify_file_with_check(&copy::object_path(self.root(), key), key, || {
                    original.intent.budget.check()?;
                    permission()
                })?;
            let metadata = file.metadata().map_err(|e| e.to_string())?;
            if self.inner.restore_binding.is_none()
                && (metadata.dev(), metadata.ino()) != (receipt.file_device, receipt.file_inode)
            {
                return Err(
                    "Snapshot receiving byte receipt differs from its actual original inode".into(),
                );
            }
            verified_files.push((metadata.dev(), metadata.ino()));
            receipts.push(receipt);
        }
        let mut guard = self.inner.connection.lock().map_err(|e| e.to_string())?;
        let sql = guard
            .as_mut()
            .ok_or("original Snapshot receiving SQL is closed")?;
        self.validate_connection(sql)?;
        let mut current = record(sql, &id)?.ok_or("original Snapshot receiving row disappeared")?;
        if current.intent != original.intent {
            return Err("original Snapshot receive issue changed during verification".into());
        }
        if current.ready.is_some() || current.retirement.is_some() {
            return status(&current);
        }
        original.intent.budget.check()?;
        permission()?;
        for receipt in &receipts {
            if object_receipt(sql, &receipt.content)? != *receipt {
                return Err("Snapshot input receipt changed during verification".into());
            }
        }
        current.ready = Some(SnapshotContentReady {
            intent_digest: digest(&original.intent)?,
            inputs: receipts,
            verified_files,
            completed_ms: original.intent.budget.check()?,
        });
        let transaction = sql.transaction().map_err(|e| e.to_string())?;
        transaction
            .execute(
                "UPDATE content_snapshot_receives SET record=?1 WHERE operation_id=?2",
                params![encode(&current)?, id],
            )
            .map_err(|e| e.to_string())?;
        layout::within_budget(&transaction)?;
        original.intent.budget.check()?;
        permission()?;
        transaction.commit().map_err(|e| e.to_string())?;
        status(&current)
    }
}
