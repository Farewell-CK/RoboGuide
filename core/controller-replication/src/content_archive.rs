//! Exact coordinated Archive successors; ordinary application candidates cannot delete history.

use crate::content_application::CONTENT_ARCHIVE_APPLICATION_SCHEMA;
use crate::{ContentApplicationBatch, ControllerEpoch, ReplicaView};
use sha2::{Digest, Sha256};

/// Exact current application writer evidence, preserving the actual epoch's original JSON spelling.
fn epoch_identity(profile: &str, epoch: ControllerEpoch) -> Result<String, String> {
    Ok(format!(
        "{profile}:{}",
        serde_json::to_string(&epoch).map_err(|e| e.to_string())?
    ))
}

/// Reconstructs complete predecessor metadata; it grants no local writer or byte permission.
fn original(previous: &ReplicaView) -> Result<state::CompleteReplicaState, String> {
    let content = previous
        .content
        .as_ref()
        .ok_or("coordinated Archive requires an accepted original complete view")?;
    Ok(state::CompleteReplicaState {
        schema: "roboguide.complete-replica-state/v0.1".into(),
        checkpoint: previous
            .checkpoint
            .clone()
            .ok_or("coordinated Archive original checkpoint is unavailable")?,
        events: previous.events.clone(),
        commands: previous.commands.clone(),
        recovery: content.recovery.clone(),
    })
}

impl ContentApplicationBatch {
    /// Derives only this original positive maintenance decision's clock; historical rows grant none.
    /// Explicit abandonment reduces rights and therefore does not consume the expired copy budget.
    pub(crate) fn archive_effects(
        &self,
        previous: &ReplicaView,
    ) -> Result<Vec<state::CoordinatedArchiveIntent>, String> {
        if self.schema != CONTENT_ARCHIVE_APPLICATION_SCHEMA {
            return Ok(Vec::new());
        }
        self.validate_archive_transition(previous)?;
        let old = previous
            .content
            .as_ref()
            .and_then(|c| c.recovery.coordinated_archives.as_ref());
        Ok(self
            .recovery
            .coordinated_archives
            .iter()
            .flatten()
            .filter(|row| {
                row.abort.is_none()
                    && old.and_then(|rows| {
                        rows.iter()
                            .find(|r| r.intent.request.command_id == row.intent.request.command_id)
                    }) != Some(*row)
            })
            .map(|r| r.intent.clone())
            .collect())
    }

    /// Validates one exact original admission/completion/abandonment against the full predecessor.
    /// The actual live adapter independently requires the original child, CAS bytes and full quorum.
    pub(crate) fn validate_archive_transition(&self, previous: &ReplicaView) -> Result<(), String> {
        if self.schema != CONTENT_ARCHIVE_APPLICATION_SCHEMA {
            if self.recovery.coordinated_archives.is_some() {
                return Err(
                    "old complete-content envelope cannot hide coordinated Archive authority"
                        .into(),
                );
            }
            return Ok(());
        }
        let rows = self
            .recovery
            .coordinated_archives
            .as_ref()
            .ok_or("complete-content v4 requires its mandatory coordinated Archive journal")?;
        let private = rows
            .iter()
            .map(|r| r.intent.package.content_digest.as_str())
            .collect::<std::collections::BTreeSet<_>>();
        if self.origins.iter().any(|(id, key)| {
            private.contains(key.content_digest.as_str())
                && previous
                    .content
                    .as_ref()
                    .is_none_or(|c| !c.origins.contains_key(id))
        }) {
            return Err(
                "private original Archive metadata cannot become public Artifact or Memory content"
                    .into(),
            );
        }
        let old = previous
            .content
            .as_ref()
            .and_then(|c| c.recovery.coordinated_archives.as_ref());
        let changed = rows
            .iter()
            .filter(|r| {
                old.and_then(|rows| {
                    rows.iter()
                        .find(|o| o.intent.request.command_id == r.intent.request.command_id)
                }) != Some(*r)
            })
            .collect::<Vec<_>>();
        if changed.is_empty() {
            if old.is_none()
                || self.recovery.history_floor().map_err(|e| e.to_string())?
                    != previous
                        .content
                        .as_ref()
                        .ok_or("original complete predecessor is unavailable")?
                        .recovery
                        .history_floor()
                        .map_err(|e| e.to_string())?
            {
                return Err(
                    "coordinated Archive floor cannot change without one original decision".into(),
                );
            }
            return Ok(());
        }
        if changed.len() != 1
            || !self.batch.events.is_empty()
            || !self.batch.commands.is_empty()
            || previous.checkpoint.as_ref() != Some(&self.batch.checkpoint)
        {
            return Err(
                "coordinated Archive maintenance cannot hide another application mutation".into(),
            );
        }
        let record = changed[0];
        record.validate()?;
        let i = &record.intent;
        let prior = old.and_then(|rows| {
            rows.iter()
                .find(|o| o.intent.request.command_id == i.request.command_id)
        });
        let original = original(previous)?;
        if let Some(prior) = prior {
            record.follows(prior)?;
            if prior.completed_ms.is_some() || prior.abort.is_some() {
                return Err("completed or abandoned original Archive cannot execute again".into());
            }
            if record.completed_ms.is_some() {
                if i.issuing_epoch != epoch_identity(&self.profile_identity, self.epoch)?
                    || i.package.source_owner != self.source_writer.owner
                    || i.manifest.checkpoint_digest.as_deref()
                        != Some(state::checkpoint_identity(&self.batch.checkpoint).as_str())
                {
                    return Err("original Archive completion lost its original writer, source or checkpoint".into());
                }
            } else if record.abort.as_ref().is_none_or(|a| {
                a.issuing_epoch
                    != epoch_identity(&self.profile_identity, self.epoch).unwrap_or_default()
            }) {
                return Err(
                    "coordinated Archive successor lacks a current explicit abandonment".into(),
                );
            }
        } else {
            if record.completed_ms.is_some()
                || record.abort.is_some()
                || i.issuing_epoch != epoch_identity(&self.profile_identity, self.epoch)?
                || i.package.source_owner != self.source_writer.owner
            {
                return Err(
                    "coordinated Archive publication preceded its durable original admission"
                        .into(),
                );
            }
            let inputs = previous
                .content
                .as_ref()
                .ok_or("original qualified content is unavailable")?
                .inputs
                .iter()
                .map(|k| state::ArchiveContentKey {
                    source_owner: k.source_owner.clone(),
                    content_digest: k.content_digest.clone(),
                    byte_size: k.byte_size,
                })
                .collect();
            let package =
                state::coordinated_archive_package(&original, i.request.end_sequence, inputs)?;
            let bytes = state::coordinated_archive_bytes(&package)?;
            let catalog = state::coordinated_archive_bytes(&package.catalog)?;
            if i.authority_id != package.authority_id
                || i.manifest != package.manifest
                || i.package.byte_size != bytes.len() as u64
                || i.package.content_digest != format!("sha256:{:x}", Sha256::digest(&bytes))
                || i.catalog_digest != format!("sha256:{:x}", Sha256::digest(catalog))
                || i.recovery_digest != package.recovery_digest
            {
                return Err(
                    "coordinated Archive original package, cutoff or qualified inputs differ"
                        .into(),
                );
            }
        }
        if state::coordinated_archive_projection(&original, record)? != self.recovery {
            return Err(
                "coordinated Archive maintenance changed unrelated recovery evidence".into(),
            );
        }
        Ok(())
    }
}
