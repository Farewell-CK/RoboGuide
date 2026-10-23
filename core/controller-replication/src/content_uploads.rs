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
