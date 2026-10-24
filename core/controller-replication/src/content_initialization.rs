//! Explicit original compacted-source proposal validation; callers still require parent9 and quorum.

use super::*;

/// Immutable source initialization lineage, distinct from current availability and source effects.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ContentInitializationAnchor {
    /// Closed original lineage interpretation.
    pub schema: String,
    /// Complete original source metadata, never recomputed from a later checkpoint.
    pub source_state_digest: String,
    /// Exact original accepted initialization candidate.
    pub candidate_digest: String,
    /// Actual original source CAS writer, preserved as historical evidence after restore.
    pub source_writer: ApplicationArtifactWriter,
    /// Original issuing infrastructure epoch, never a current leadership grant.
    pub source_epoch: ControllerEpoch,
    /// Exact original compacted prefix, without synthetic event rows.
    pub history_floor: u64,
    /// Exact complete original source checkpoint digest.
    pub checkpoint_digest: String,
    /// Complete original source sequence, including its actual hot tail.
    pub event_sequence: u64,
    /// Original last source authority revision, independent of Replica application revisions.
    pub authority_revision: u64,
}

impl ContentInitializationAnchor {
    /// Checks bounded historical lineage shape without conferring current process or content facts.
    pub fn validate(&self) -> Result<(), String> {
        if self.schema != "roboguide.content-initialization-anchor/v0.1"
            || self
                .source_state_digest
                .strip_prefix("sha256:")
                .is_none_or(|value| !crate::content::hex_digest(value))
            || !crate::content::hex_digest(&self.candidate_digest)
            || !crate::content::hex_digest(&self.checkpoint_digest)
            || self
                .source_writer
                .owner
                .strip_prefix("artifact-store:")
                .is_none_or(|value| !crate::content::hex_digest(value))
            || self.source_writer.inode == 0
            || self.source_epoch.term == 0
            || self.source_epoch.replica_id == 0
            || self.history_floor == 0
            || self.history_floor > self.event_sequence
            || self.authority_revision == 0
        {
            return Err("original content initialization anchor has invalid source lineage".into());
        }
        Ok(())
    }

    /// Derives historical metadata only from the complete validated initial proposal.
    pub(crate) fn from_candidate(candidate: &ContentApplicationBatch) -> Result<Self, String> {
        candidate.validate_context_batch()?;
        if candidate.schema != CONTENT_INITIALIZATION_SCHEMA {
            return Err(
                "initialization anchor requires an exact original initialization candidate".into(),
            );
        }
        let result = Self {
            schema: "roboguide.content-initialization-anchor/v0.1".into(),
            source_state_digest: candidate
                .initialization_digest
                .clone()
                .ok_or("initialization source digest missing")?,
            candidate_digest: candidate.identity()?,
            source_writer: candidate.source_writer.clone(),
            source_epoch: candidate.epoch,
            history_floor: candidate
                .recovery
                .history_floor()
                .map_err(|error| error.to_string())?,
            checkpoint_digest: state::checkpoint_identity(&candidate.batch.checkpoint),
            event_sequence: candidate.batch.checkpoint.event_sequence,
            authority_revision: candidate
                .batch
                .commands
                .last()
                .ok_or("initialization source command missing")?
                .authority_revision,
        };
        result.validate()?;
        Ok(result)
    }
}

/// Closed deployment-pinned source policy; local paths never become remote filesystem authority.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ContentInitializationPolicy {
    /// Complete explicit policy interpretation.
    pub schema: String,
    /// Exact complete original Controller source metadata digest.
    pub source_state_digest: String,
    /// Original managed Artifact owner, independently matched against original Archive evidence.
    pub source_owner: String,
    /// Declared original infrastructure source member, never an embodied Node.
    pub source_member: u64,
    /// One immutable original source operation; restarts cannot mint another effect identity.
    pub command_id: String,
    /// Original first admission duration, including actual queue/source/content/quorum work.
    pub budget_ms: u64,
}

impl ContentInitializationPolicy {
    /// Matches the retained original initialization, without mistaking it for current writer identity.
    pub fn require_anchor(&self, anchor: &ContentInitializationAnchor) -> Result<(), String> {
        self.validate()?;
        anchor.validate()?;
        if anchor.source_state_digest != self.source_state_digest
            || anchor.source_writer.owner != self.source_owner
            || anchor.source_epoch.replica_id != self.source_member
        {
            return Err("retained initialization differs from its original declared source".into());
        }
        Ok(())
    }

    /// Rejects incomplete or unbounded declarations before any source file or effect is admitted.
    pub fn validate(&self) -> Result<(), String> {
        if self.schema != "roboguide.content-initialization-policy/v0.1"
            || self
                .source_state_digest
                .strip_prefix("sha256:")
                .is_none_or(|value| !crate::content::hex_digest(value))
            || self
                .source_owner
                .strip_prefix("artifact-store:")
                .is_none_or(|value| !crate::content::hex_digest(value))
            || self.source_member == 0
            || self.command_id.trim().is_empty()
            || self.command_id.len() > 128
            || self.command_id.chars().any(char::is_control)
            || !(100..=300_000).contains(&self.budget_ms)
        {
            return Err(
                "original content initialization policy has invalid source/operation bounds".into(),
            );
        }
        Ok(())
    }

    /// Requires the exact original source proposal; current bytes/permission/quorum remain separate.
    pub fn require_candidate(&self, candidate: &ContentApplicationBatch) -> Result<(), String> {
        self.validate()?;
        candidate.validate_context_batch()?;
        if candidate.schema != CONTENT_INITIALIZATION_SCHEMA
            || candidate.initialization_digest.as_ref() != Some(&self.source_state_digest)
            || candidate.source_writer.owner != self.source_owner
            || candidate.epoch.replica_id != self.source_member
        {
            return Err(
                "original initialization differs from its immutable declared source".into(),
            );
        }
        Ok(())
    }
}

impl ReplicaView {
    /// Recognizes only an actually empty application, independently of infrastructure membership/votes.
    pub(crate) fn is_empty_application(&self) -> bool {
        self.revision == 0
            && self.checkpoint.is_none()
            && self.events.is_empty()
            && self.commands.is_empty()
            && self.history_anchor.is_none()
            && self.content.is_none()
    }
}

impl ContentApplicationBatch {
    /// Preserves an exact historical initialization anchor without retaining obsolete byte pins.
    pub(crate) fn initialization_anchor(
        &self,
    ) -> Result<Option<ContentInitializationAnchor>, String> {
        if self.schema == CONTENT_INITIALIZATION_SCHEMA {
            ContentInitializationAnchor::from_candidate(self).map(Some)
        } else {
            if let Some(anchor) = &self.source_initialization {
                anchor.validate()?;
            }
            Ok(self.source_initialization.clone())
        }
    }

    /// Checks the explicitly selected batch codec; shape validation confers no writer or byte authority.
    pub(crate) fn validate_context_batch(&self) -> Result<(), String> {
        match (self.schema.as_str(), self.initialization_digest.as_deref()) {
            (CONTENT_APPLICATION_SCHEMA, None) => {
                if let Some(anchor) = &self.source_initialization {
                    anchor.validate()?;
                }
                self.batch.validate().map_err(|error| error.to_string())
            }
            (CONTENT_INITIALIZATION_SCHEMA, Some(expected)) => {
                if self.source_initialization.is_some() {
                    return Err(
                        "initial snapshot cannot borrow prior initialization lineage".into(),
                    );
                }
                self.validate_initial_archive_owner()?;
                let actual = self
                    .batch
                    .validate_content_initialization(&self.recovery)
                    .map_err(|error| error.to_string())?;
                if expected != actual {
                    return Err(
                        "original initialization changed its complete source metadata digest"
                            .into(),
                    );
                }
                Ok(())
            }
            _ => Err(
                "original content envelope has an unsupported initialization/codec interpretation"
                    .into(),
            ),
        }
    }

    /// Binds archived origins only to their retained source owner, including decoded requests.
    fn validate_initial_archive_owner(&self) -> Result<(), String> {
        if self.recovery.archive_operations.is_empty() {
            return Err(
                "original archived source has no retained original CAS ownership evidence".into(),
            );
        }
        for operation in &self.recovery.archive_operations {
            let intent: state::ArchiveIntent =
                serde_json::from_str(&operation.intent_json).map_err(|error| error.to_string())?;
            if intent.request.source_owner != self.source_writer.owner {
                return Err(
                    "original archived source differs from its retained Archive CAS owner".into(),
                );
            }
        }
        Ok(())
    }

    /// Captures only a complete original archived source; this readonly proposal is no quorum/use proof.
    pub fn from_archived_source(
        profile_identity: &str,
        epoch: ControllerEpoch,
        source: &artifact_store::FileSystemArtifactStore,
        previous: &ReplicaView,
        original: state::CompleteReplicaState,
    ) -> Result<Self, String> {
        if !previous.is_empty_application() {
            return Err(
                "original archived-source capture requires an empty receiver application".into(),
            );
        }
        let initialization_digest = original.identity().map_err(|error| error.to_string())?;
        let writer = source
            .writer_identity()
            .map_err(|error| error.to_string())?;
        let source_writer = ApplicationArtifactWriter {
            owner: writer.owner_id,
            device: writer.lease_device,
            inode: writer.lease_inode,
        };
        let mut origins = BTreeMap::new();
        for row in original
            .recovery
            .catalog_archive_events
            .iter()
            .flatten()
            .chain(original.events.iter())
        {
            let payload: EventPayload =
                serde_json::from_str(&row.payload_json).map_err(|error| error.to_string())?;
            if let Some((content_digest, byte_size)) = bare_content(&payload)
                && origins
                    .insert(
                        row.event_id.clone(),
                        ArtifactContentKey {
                            source_owner: source_writer.owner.clone(),
                            content_digest,
                            byte_size,
                        },
                    )
                    .is_some()
            {
                return Err("original archived source repeats a protected event identity".into());
            }
        }
        let inputs = required_inputs(&original.recovery, &original.events, &origins)?;
        let result = Self {
            schema: CONTENT_INITIALIZATION_SCHEMA.into(),
            profile_identity: profile_identity.into(),
            epoch,
            source_writer,
            batch: state::ApplicationCommitBatch {
                schema: state::APPLICATION_CONTENT_INITIALIZATION_SCHEMA.into(),
                previous_event_sequence: 0,
                previous_checkpoint_digest: None,
                checkpoint: original.checkpoint,
                events: original.events,
                commands: original.commands,
            },
            recovery: original.recovery,
            origins,
            inputs,
            initialization_digest: Some(initialization_digest),
            source_initialization: None,
        };
        result.validate_against(previous, profile_identity)?;
        Ok(result)
    }
}
