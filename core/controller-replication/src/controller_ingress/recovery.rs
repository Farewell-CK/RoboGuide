//! Explicit Prepared recovery binds original metadata, actual child/writer and nonrenewing budgets.

use super::*;
use domain::{ArtifactUploadPhase, ArtifactUploadRecovery, EventPayload};

/// Returns canonical nested evidence without changing the original structured values.
fn canonical(value: &impl Serialize) -> Result<String, String> {
    serde_json::to_string(&serde_json::to_value(value).map_err(|e| e.to_string())?)
        .map_err(|e| e.to_string())
}

/// Reads complete bounded original Catalog history, including its protected archived prefix.
fn rows(view: &ReplicaView) -> Result<Vec<state::PersistedEvent>, String> {
    let mut rows = view
        .content
        .as_ref()
        .and_then(|c| c.recovery.catalog_archive_events.clone())
        .unwrap_or_default();
    rows.extend_from_slice(&view.events);
    if rows.len() > 32768 {
        return Err("original recovery history exceeds its bound".into());
    }
    Ok(rows)
}

/// Selects the exact latest original Prepared upload and its actual immutable qualified source.
fn prepared(
    view: &ReplicaView,
    original: &domain::ArtifactUploadRecord,
) -> Result<ArtifactContentKey, String> {
    let content = view
        .content
        .as_ref()
        .ok_or("original complete content view absent")?;
    let mut latest = None;
    for row in rows(view)? {
        let payload: EventPayload =
            serde_json::from_str(&row.payload_json).map_err(|e| e.to_string())?;
        match payload {
            EventPayload::ArtifactUploadTransition { record }
                if record.upload_id == original.upload_id =>
            {
                latest = Some((record, content.origins.get(&row.event_id).cloned()));
            }
            EventPayload::ArtifactUploadRetired { receipt }
                if receipt.upload.upload_id == original.upload_id =>
            {
                return Err("original recovery upload was retired".into());
            }
            _ => {}
        }
    }
    match latest {
        Some((record, Some(key)))
            if record == *original
                && record.phase == ArtifactUploadPhase::Prepared
                && content.inputs.binary_search(&key).is_ok() =>
        {
            Ok(key)
        }
        _ => Err("recovery requires its exact current original Prepared source".into()),
    }
}

/// Decodes retained original evidence without renewing the budget or borrowing another incarnation.
pub(crate) fn evidence(
    record: &ArtifactUploadRecovery,
) -> Result<(ControllerProcessIdentity, ContentBudget), String> {
    record.validate()?;
    let process: ControllerProcessIdentity =
        serde_json::from_str(&record.controller_process_json).map_err(|e| e.to_string())?;
    let budget: ContentBudget =
        serde_json::from_str(&record.kernel_budget_json).map_err(|e| e.to_string())?;
    crate::supervision::validate_process(&process)?;
    budget.require_process(&process)?;
    if budget.duration_ms != record.budget_ms {
        return Err("original recovery budget differs from its explicit declaration".into());
    }
    if let Some(done) = &record.completion {
        let receipt: crate::content_store::ReplicaContentReaderReceipt =
            serde_json::from_str(&done.reader_receipt_json).map_err(|e| e.to_string())?;
        if receipt.reader_id != record.reader_id
            || !receipt.complete
            || receipt.retention_only
            || receipt.file_inode == 0
            || receipt.content != source_key(record)?
            || receipt.bytes_read != receipt.content.byte_size
            || receipt.observed_digest != receipt.content.content_digest
        {
            return Err("recovery lacks its exact complete original payload reader receipt".into());
        }
    }
    Ok((process, budget))
}

/// Reconstructs only the frozen qualified input; metadata alone never grants current permission.
pub(super) fn source_key(record: &ArtifactUploadRecovery) -> Result<ArtifactContentKey, String> {
    let key = ArtifactContentKey {
        source_owner: record.source_owner_id.clone(),
        content_digest: record
            .upload
            .content_digest
            .clone()
            .ok_or("original Prepared digest absent")?,
        byte_size: record
            .upload
            .byte_size
            .ok_or("original Prepared size absent")?,
    };
    key.validate()?;
    Ok(key)
}

/// Resolves only a current committed pending recovery and validates its immutable original source.
pub(crate) fn pending(view: &ReplicaView, command: &str) -> Result<ArtifactUploadRecovery, String> {
    let mut latest = None;
    for row in rows(view)? {
        if let EventPayload::ArtifactUploadRecoveryTransition { record } =
            serde_json::from_str::<EventPayload>(&row.payload_json).map_err(|e| e.to_string())?
            && record.command_id == command
        {
            latest = Some(*record);
        }
    }
    let record = latest.ok_or("original recovery intent is not committed")?;
    if record.completion.is_some() || prepared(view, &record.upload)? != source_key(&record)? {
        return Err("original recovery is complete or its source changed".into());
    }
    evidence(&record)?;
    Ok(record)
}

impl ControllerContentClient {
    /// Matches the last local authority against a fresh complete quorum view before protected status.
    pub fn verify_committed_checkpoint(&self, expected: &str) -> Result<(), String> {
        if !crate::content::hex_digest(expected) {
            return Err("original committed checkpoint identity is invalid".into());
        }
        let view = self.view()?;
        if view
            .checkpoint
            .as_ref()
            .map(state::checkpoint_identity)
            .as_deref()
            != Some(expected)
        {
            return Err("local committed authority differs from the current complete quorum; startup replay required".into());
        }
        Ok(())
    }

    /// Freezes actual source/writer/process/kernel evidence before the application commits its intent.
    pub fn prepare_upload_recovery(
        &self,
        mut record: ArtifactUploadRecovery,
        duration_ms: u64,
    ) -> Result<ArtifactUploadRecovery, String> {
        let clock = ContentBudget::issue(duration_ms)?;
        let process = self.check()?;
        let view = self.view()?;
        let key = prepared(&view, &record.upload)?;
        let writer = self.source.writer_identity().map_err(|e| e.to_string())?;
        record.reader_id = format!("child-read:{}", crate::supervision::new_process_instance()?);
        record.source_owner_id = key.source_owner;
        record.destination_owner_id = writer.owner_id;
        record.destination_device = writer.lease_device;
        record.destination_inode = writer.lease_inode;
        record.controller_process_json = canonical(&process)?;
        record.kernel_budget_json = canonical(&clock)?;
        evidence(&record)?;
        clock.check()?;
        Ok(record)
    }

    /// Rechecks original child/writer and original kernel time before each recovery effect or Commit.
    pub fn check_upload_recovery(&self, record: &ArtifactUploadRecovery) -> Result<(), String> {
        let (process, clock) = evidence(record)?;
        clock.check()?;
        if process.pid != std::process::id()
            || process.instance_id != self.config.instance
            || process.epoch != self.config.epoch
        {
            return Err("original recovery belongs to another Controller incarnation".into());
        }
        let actual = self.source.writer_identity().map_err(|e| e.to_string())?;
        if (
            actual.owner_id.as_str(),
            actual.lease_device,
            actual.lease_inode,
        ) != (
            record.destination_owner_id.as_str(),
            record.destination_device,
            record.destination_inode,
        ) {
            return Err("original recovery destination writer was replaced".into());
        }
        Ok(())
    }
}

/// Checks new authority events against actual original child/current source and actual Root SQL receipt.
pub(super) fn validate_new_candidate(
    node: &ReplicaNode,
    candidate: &ContentApplicationBatch,
    process: &ControllerProcessIdentity,
    view: &ReplicaView,
) -> Result<Vec<ContentBudget>, String> {
    let mut current = BTreeMap::new();
    for row in rows(view)? {
        if let EventPayload::ArtifactUploadRecoveryTransition { record } =
            serde_json::from_str::<EventPayload>(&row.payload_json).map_err(|e| e.to_string())?
        {
            current.insert(record.command_id.clone(), *record);
        }
    }
    let mut clocks = Vec::new();
    for row in &candidate.batch.events {
        let payload: EventPayload =
            serde_json::from_str(&row.payload_json).map_err(|e| e.to_string())?;
        let record = match &payload {
            EventPayload::ArtifactUploadRecoveryTransition { record } => Some(record.as_ref()),
            EventPayload::ArtifactUploadTransition { record } => current
                .values()
                .find(|r| r.replacement_admission.upload_id == record.upload_id),
            _ => None,
        };
        if let Some(record) = record {
            let (original, clock) = evidence(record)?;
            clock.check()?;
            if original != *process
                || source_key(record)? != prepared(view, &record.upload)?
                || (
                    candidate.source_writer.owner.as_str(),
                    candidate.source_writer.device,
                    candidate.source_writer.inode,
                ) != (
                    record.destination_owner_id.as_str(),
                    record.destination_device,
                    record.destination_inode,
                )
            {
                return Err("recovery authority changed its actual child/source/writer".into());
            }
            if let EventPayload::ArtifactUploadRecoveryTransition { record } = &payload
                && let Some(done) = &record.completion
            {
                let mut first = (**record).clone();
                first.completion = None;
                if pending(view, &record.command_id)? != first {
                    return Err("recovery completion changed its original committed intent".into());
                }
                let receipt =
                    serde_json::from_str(&done.reader_receipt_json).map_err(|e| e.to_string())?;
                node.store
                    .network_content_store()?
                    .require_reader_receipt(&receipt, &done.reader_authority_digest)?;
            }
            clocks.push(clock);
        }
    }
    Ok(clocks)
}
