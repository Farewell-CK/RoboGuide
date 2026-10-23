//! Keeps an explicitly abandoned upload's exact historical owner across Controller failover.

use crate::*;
use domain::{ArtifactUploadPhase, ArtifactUploadRecord, EventPayload};

/// Resolves only the original latest complete upload; a digest match cannot select another owner.
fn current_upload(
    previous: &ReplicaView,
    id: &str,
) -> Result<Option<(ArtifactUploadRecord, Option<ArtifactContentKey>)>, String> {
    let Some(content) = &previous.content else {
        return Ok(None);
    };
    let prefix = content
        .recovery
        .catalog_archive_events
        .as_deref()
        .unwrap_or_default();
    if prefix.len().saturating_add(previous.events.len()) > 32_768 {
        return Err("original upload release history exceeds its scan bound".into());
    }
    let mut latest = None;
    for row in prefix.iter().chain(&previous.events) {
        let payload: EventPayload =
            serde_json::from_str(&row.payload_json).map_err(|e| e.to_string())?;
        if let EventPayload::ArtifactUploadTransition { record } = payload
            && record.upload_id == id
        {
            latest = Some((record, content.origins.get(&row.event_id).cloned()));
        }
    }
    Ok(latest)
}

/// Inherits only a complete immutable Prepared release; new bytes/Completed still need the real writer.
pub(crate) fn abandoned_origin(
    previous: &ReplicaView,
    payload: &EventPayload,
) -> Result<Option<ArtifactContentKey>, String> {
    let EventPayload::ArtifactUploadTransition { record } = payload else {
        return Ok(None);
    };
    if record.phase != ArtifactUploadPhase::Abandoned || record.content_digest.is_none() {
        return Ok(None);
    }
    let Some((original, origin)) = current_upload(previous, &record.upload_id)? else {
        return Ok(None);
    };
    if original.phase == ArtifactUploadPhase::Started {
        return Ok(None);
    }
    if !matches!(
        original.phase,
        ArtifactUploadPhase::Prepared | ArtifactUploadPhase::Abandoned
    ) {
        return Err("upload release cannot replace original Completed ownership".into());
    }
    original.validate()?;
    original.validates_successor(record)?;
    let origin = origin.ok_or("original upload release has no qualified source")?;
    origin.validate()?;
    if Some(&origin.content_digest) != original.content_digest.as_ref()
        || Some(origin.byte_size) != original.byte_size
    {
        return Err("original upload release source differs from its complete byte proof".into());
    }
    Ok(Some(origin))
}

/// Resolves only an exact current unretired upload; equal hashes and other consumers cannot lend it rights.
pub(crate) fn retained_upload(
    view: &ReplicaView,
    original: &ArtifactUploadRecord,
) -> Result<ArtifactContentKey, String> {
    original.validate()?;
    let content = view
        .content
        .as_ref()
        .ok_or("complete original upload view unavailable")?;
    let Some((record, Some(key))) = current_upload(view, &original.upload_id)? else {
        return Err("complete original upload source unavailable".into());
    };
    if record != *original
        || record.phase != ArtifactUploadPhase::Completed
        || Some(&key.content_digest) != record.content_digest.as_ref()
        || Some(key.byte_size) != record.byte_size
        || content.inputs.binary_search(&key).is_err()
    {
        return Err("retirement differs from its exact current Completed source".into());
    }
    for row in content
        .recovery
        .catalog_archive_events
        .as_deref()
        .unwrap_or_default()
        .iter()
        .chain(&view.events)
    {
        if let EventPayload::ArtifactUploadRetired { receipt } =
            serde_json::from_str(&row.payload_json).map_err(|e| e.to_string())?
            && receipt.upload.upload_id == original.upload_id
        {
            return Err("original upload is already retired".into());
        }
    }
    key.validate()?;
    Ok(key)
}

impl ControllerContentClient {
    /// Obtains fresh complete-quorum metadata for the exact original Completed upload, not a caller key.
    pub fn completed_upload_source(
        &self,
        original: &ArtifactUploadRecord,
    ) -> Result<ArtifactContentKey, String> {
        retained_upload(&self.view()?, original)
    }
}
