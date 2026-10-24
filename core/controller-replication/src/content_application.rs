//! Complete original application metadata/content identities; only actual adapters grant durability.

use crate::*;
use domain::{ArtifactUploadPhase, CorrelationId, EventId, EventPayload, EventRecord, TimestampMs};
use serde::{Deserialize, Serialize};
use sha2::{Digest as _, Sha256};

#[path = "content_initialization.rs"]
mod initialization;
pub use initialization::{ContentInitializationAnchor, ContentInitializationPolicy};

/// Explicit new private application envelope; ordinary semantic v2 bytes keep their original meaning.
pub const CONTENT_APPLICATION_SCHEMA: &str = "roboguide.application-commit/v3";
/// Complete original archived-source initialization, never accepted by ordinary incremental ports.
pub const CONTENT_INITIALIZATION_SCHEMA: &str = "roboguide.application-initialization/v0.1";

/// Actual original application CAS writer, distinct from every receiving member's content backend.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ApplicationArtifactWriter {
    /// Actual opened original application CAS identity, never the receiver's identity.
    pub owner: String,
    /// Actual original application writer lease device.
    pub device: u64,
    /// Actual original application writer lease inode.
    pub inode: u64,
}

/// Exact metadata/content candidate; parsing or validation alone never proves current bytes or quorum.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ContentApplicationBatch {
    /// Explicit private v3 meaning, rejected by metadata-only profiles.
    pub schema: String,
    /// Exact original immutable cluster/application profile.
    pub profile_identity: String,
    /// Actual issuing original Controller epoch, independently checked by the receiving adapter.
    pub epoch: ControllerEpoch,
    /// Actual original source writer supplied by the managed application composition.
    pub source_writer: ApplicationArtifactWriter,
    /// Original semantic candidate, with unchanged v2 checkpoint/hash/journal semantics.
    pub batch: state::ApplicationCommitBatch,
    /// Complete original independent State/Catalog/Archive recovery.
    pub recovery: state::ReplicaRecoveryProjection,
    /// Immutable exact origin for every protected raw event whose original fields contain bare bytes.
    pub origins: BTreeMap<String, ArtifactContentKey>,
    /// Exact complete current required content, retaining independent owners for equal hashes.
    pub inputs: Vec<ArtifactContentKey>,
    /// Mandatory exact original complete-source digest only in the explicit initialization envelope.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub initialization_digest: Option<String>,
    /// Immutable original initialization lineage on subsequent parent9 candidates only.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub source_initialization: Option<ContentInitializationAnchor>,
}

/// Complete accepted v0.4 auxiliary view; local reader/copy/process authority is deliberately excluded.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AcceptedContentState {
    /// Exact original accepted complete private envelope identity.
    pub batch_digest: String,
    /// Complete exact original accepted envelope, retaining the independently recomputable batch digest.
    /// This grants no new permission and contains no member-local copy/reader/process authority.
    pub candidate: Box<ContentApplicationBatch>,
    /// Original applied initialization anchor; historical metadata grants no current byte/use fact.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub source_initialization: Option<ContentInitializationAnchor>,
}

impl std::ops::Deref for AcceptedContentState {
    type Target = ContentApplicationBatch;
    /// Exposes exact original fields without storing a second diverging auxiliary authority map.
    fn deref(&self) -> &Self::Target {
        &self.candidate
    }
}

/// Reads only a raw event's declared byte identity; actual store origins must be captured separately.
fn bare_content(payload: &EventPayload) -> Option<(String, u64)> {
    match payload {
        EventPayload::ArtifactUploadTransition { record } => {
            record.content_digest.clone().zip(record.byte_size)
        }
        EventPayload::MemoryManifestPublished { manifest } => manifest.artifact().map(|artifact| {
            (
                artifact.content_digest().as_str().into(),
                artifact.byte_size(),
            )
        }),
        EventPayload::MapArtifactPublished { manifest } => Some((
            manifest.artifact().content_digest().as_str().into(),
            manifest.artifact().byte_size(),
        )),
        EventPayload::SemanticVerifierEvidenceAdmitted { receipt } => receipt
            .evidence
            .artifact_digest
            .clone()
            .zip(receipt.evidence.artifact_size_bytes),
        EventPayload::MissionGoalEvidenceAdmitted { receipt } => receipt
            .evidence
            .artifact_digest
            .clone()
            .zip(receipt.evidence.artifact_size_bytes),
        _ => None,
    }
}

/// Recreates only the exact typed original envelope for existing retention reducers.
fn event(row: &state::PersistedEvent, payload: EventPayload) -> Result<EventRecord, String> {
    Ok(EventRecord::new(
        EventId::new(row.event_id.clone()).map_err(|error| error.to_string())?,
        TimestampMs::new(row.timestamp_ms),
        CorrelationId::new(row.correlation_id.clone()).map_err(|error| error.to_string())?,
        row.causation_id
            .clone()
            .map(EventId::new)
            .transpose()
            .map_err(|error| error.to_string())?,
        payload,
    ))
}

/// Replays complete original namespace obligations without treating a bare digest as another owner's copy.
pub(crate) fn required_inputs(
    recovery: &state::ReplicaRecoveryProjection,
    events: &[state::PersistedEvent],
    origins: &BTreeMap<String, ArtifactContentKey>,
) -> Result<Vec<ArtifactContentKey>, String> {
    let mut rows = recovery.catalog_archive_events.clone().unwrap_or_default();
    rows.extend(events.iter().cloned());
    if rows.len() > 32_768 || origins.len() > 16_384 {
        return Err("complete original content lineage exceeds its bound".into());
    }
    let mut referenced_origins = BTreeSet::new();
    let mut retained = state::ArtifactRetentionProjection::default();
    let mut publications = BTreeMap::new();
    let mut uploads = BTreeMap::new();
    let mut upload_origins = BTreeMap::new();
    let mut retired_uploads = BTreeMap::new();
    let mut recoveries = BTreeMap::new();
    let mut required = BTreeSet::new();
    for row in rows {
        state::validate_persisted_event(&row).map_err(|error| error.to_string())?;
        let payload: EventPayload =
            serde_json::from_str(&row.payload_json).map_err(|error| error.to_string())?;
        let origin = if let Some((digest, size)) = bare_content(&payload) {
            let origin = origins
                .get(&row.event_id)
                .ok_or("original bare content has no original event/store binding")?;
            origin.validate()?;
            if origin.content_digest != digest || origin.byte_size != size {
                return Err("original event content changed its complete digest/size".into());
            }
            referenced_origins.insert(row.event_id.clone());
            Some(origin.clone())
        } else {
            None
        };
        retained.observe_publication(&event(&row, payload.clone())?)?;
        match payload {
            EventPayload::ArtifactUploadTransition { record } => {
                if let Some(origin) = origin {
                    if upload_origins
                        .get(&record.upload_id)
                        .is_some_and(|previous| previous != &origin)
                    {
                        return Err("original upload workflow cannot change its frozen source content namespace".into());
                    }
                    upload_origins.insert(record.upload_id.clone(), origin);
                }
                uploads.insert(record.upload_id.clone(), record);
            }
            EventPayload::ArtifactUploadRecoveryTransition { record } => {
                state::validate_upload_recovery(&uploads, &recoveries, &record)?;
                crate::controller_ingress::recovery::evidence(&record)?;
                if upload_origins.get(&record.upload.upload_id).is_none_or(
                    |key: &ArtifactContentKey| key.source_owner != record.source_owner_id,
                ) {
                    return Err("recovery changed its exact original qualified source".into());
                }
                if let Some(done) = &record.completion
                    && upload_origins.get(&done.replacement.upload_id).is_none_or(
                        |key: &ArtifactContentKey| key.source_owner != record.destination_owner_id,
                    )
                {
                    return Err(
                        "recovery changed its independent completed replacement owner".into(),
                    );
                }
                recoveries.insert(record.command_id.clone(), *record);
            }
            EventPayload::ArtifactUploadRetired { receipt } => {
                state::validate_upload_retirement(&uploads, &retired_uploads, &receipt)?;
                if upload_origins
                    .get(&receipt.upload.upload_id)
                    .is_none_or(|origin| origin.source_owner != receipt.store_owner_id)
                {
                    return Err(
                        "original upload retirement changed its actual source namespace".into(),
                    );
                }
                retired_uploads.insert(receipt.upload.upload_id.clone(), receipt);
            }
            EventPayload::ArtifactCatalogReferenceTransition { record } => {
                retained.apply(&record)?
            }
            EventPayload::ArtifactCatalogLegacyAdopted { receipt } => {
                retained.apply_adoption(&receipt)?
            }
            EventPayload::MemoryManifestPublished { manifest } => {
                publications.insert(manifest.selector().clone(), origin);
            }
            EventPayload::MapArtifactPublished { manifest } => {
                if let Ok(generic) = domain::map_memory_manifest(&manifest) {
                    publications.insert(generic.selector().clone(), origin);
                } else if let Some(origin) = origin {
                    required.insert(origin);
                }
            }
            EventPayload::SemanticVerifierEvidenceAdmitted { .. }
            | EventPayload::MissionGoalEvidenceAdmitted { .. } => {
                if let Some(origin) = origin {
                    required.insert(origin);
                }
            }
            _ => {}
        }
    }
    if referenced_origins != origins.keys().cloned().collect() {
        return Err("original content lineage contains absent/extra event bindings".into());
    }
    for record in uploads.values() {
        if matches!(
            record.phase,
            ArtifactUploadPhase::Prepared | ArtifactUploadPhase::Completed
        ) && !retired_uploads.contains_key(&record.upload_id)
        {
            required.insert(
                upload_origins
                    .get(&record.upload_id)
                    .ok_or("original retained upload lost its complete source")?
                    .clone(),
            );
        }
    }
    for (selector, origin) in publications {
        if !retained.is_retired(&selector)
            && let Some(origin) = origin
        {
            required.insert(origin);
        }
    }
    for record in retained
        .records()
        .filter(|record| record.retirement.is_none())
    {
        for dependency in &record.dependencies {
            required.insert(ArtifactContentKey {
                source_owner: dependency.store_owner_id.clone(),
                content_digest: dependency.content_digest.clone(),
                byte_size: dependency.byte_size,
            });
        }
    }
    let mut aborted = BTreeSet::new();
    for row in &recovery.archive_aborts {
        if row.receipt_json.is_some() {
            let intent: state::ArchiveAbortIntent =
                serde_json::from_str(&row.intent_json).map_err(|error| error.to_string())?;
            aborted.insert(intent.request.original_command_id);
        }
    }
    for row in &recovery.archive_operations {
        if row.receipt_json.is_none() && !aborted.contains(&row.command_id) {
            let intent: state::ArchiveIntent =
                serde_json::from_str(&row.intent_json).map_err(|error| error.to_string())?;
            for input in intent.inputs {
                required.insert(ArtifactContentKey {
                    source_owner: intent.request.source_owner.clone(),
                    content_digest: input.content_digest,
                    byte_size: input.byte_size,
                });
            }
        }
    }
    let required = required.into_iter().collect::<Vec<_>>();
    if required.len() > 16_384
        || required.windows(2).any(|keys| {
            keys[0].source_owner == keys[1].source_owner
                && keys[0].content_digest == keys[1].content_digest
        })
    {
        return Err("original complete content requirements conflict or exceed bounds".into());
    }
    Ok(required)
}

impl ContentApplicationBatch {
    /// Selects only currently published retained bytes; replicated Prepared inputs remain private.
    pub(crate) fn published_content(
        &self,
        events: &[state::PersistedEvent],
        digest: &str,
    ) -> Result<ArtifactContentKey, String> {
        let mut rows = self
            .recovery
            .catalog_archive_events
            .clone()
            .unwrap_or_default();
        rows.extend_from_slice(events);
        if rows.len() > 32768 {
            return Err("published content scan exceeds original bound".into());
        }
        let mut uploads = BTreeMap::new();
        let mut retired = BTreeSet::new();
        let mut published = BTreeMap::new();
        let mut references = state::ArtifactRetentionProjection::default();
        let mut eligible = BTreeSet::new();
        for row in rows {
            let payload: EventPayload =
                serde_json::from_str(&row.payload_json).map_err(|error| error.to_string())?;
            let origin = self.origins.get(&row.event_id).cloned();
            references.observe_publication(&event(&row, payload.clone())?)?;
            match payload {
                EventPayload::ArtifactUploadTransition { record } => {
                    uploads.insert(record.upload_id.clone(), (record, origin));
                }
                EventPayload::ArtifactUploadRetired { receipt } => {
                    retired.insert(receipt.upload.upload_id);
                }
                EventPayload::MemoryManifestPublished { manifest } => {
                    published.insert(manifest.selector().clone(), origin);
                }
                EventPayload::MapArtifactPublished { manifest } => {
                    if let Ok(generic) = domain::map_memory_manifest(&manifest) {
                        published.insert(generic.selector().clone(), origin);
                    } else if let Some(origin) = origin {
                        eligible.insert(origin);
                    }
                }
                EventPayload::ArtifactCatalogReferenceTransition { record } => {
                    references.apply(&record)?
                }
                EventPayload::ArtifactCatalogLegacyAdopted { receipt } => {
                    references.apply_adoption(&receipt)?
                }
                EventPayload::SemanticVerifierEvidenceAdmitted { .. }
                | EventPayload::MissionGoalEvidenceAdmitted { .. } => {
                    if let Some(origin) = origin {
                        eligible.insert(origin);
                    }
                }
                _ => {}
            }
        }
        for (id, (record, origin)) in uploads {
            if record.phase == ArtifactUploadPhase::Completed
                && !retired.contains(&id)
                && let Some(origin) = origin
            {
                eligible.insert(origin);
            }
        }
        for (selector, origin) in published {
            if !references.is_retired(&selector)
                && let Some(origin) = origin
            {
                eligible.insert(origin);
            }
        }
        for record in references
            .records()
            .filter(|record| record.retirement.is_none())
        {
            for dep in &record.dependencies {
                eligible.insert(ArtifactContentKey {
                    source_owner: dep.store_owner_id.clone(),
                    content_digest: dep.content_digest.clone(),
                    byte_size: dep.byte_size,
                });
            }
        }
        let found = eligible
            .into_iter()
            .filter(|key| key.content_digest == digest && self.inputs.binary_search(key).is_ok())
            .collect::<Vec<_>>();
        if found.len() != 1 {
            return Err(
                "published qualified content is unavailable or has ambiguous original owners"
                    .into(),
            );
        }
        Ok(found[0].clone())
    }
    /// Constructs immutable current-writer bindings only for new original rows; history is never relabelled.
    /// This actual-file-store constructor grants no availability, semantic use or quorum receipt.
    pub fn from_source(
        profile_identity: &str,
        epoch: ControllerEpoch,
        source: &artifact_store::FileSystemArtifactStore,
        previous: &ReplicaView,
        batch: state::ApplicationCommitBatch,
        recovery: state::ReplicaRecoveryProjection,
    ) -> Result<Self, String> {
        let writer = source
            .writer_identity()
            .map_err(|error| error.to_string())?;
        let source_writer = ApplicationArtifactWriter {
            owner: writer.owner_id,
            device: writer.lease_device,
            inode: writer.lease_inode,
        };
        let mut origins = previous
            .content
            .as_ref()
            .map(|content| content.origins.clone())
            .unwrap_or_default();
        for row in &batch.events {
            let payload: EventPayload =
                serde_json::from_str(&row.payload_json).map_err(|error| error.to_string())?;
            if let Some((content_digest, byte_size)) = bare_content(&payload)
                && origins
                    .insert(
                        row.event_id.clone(),
                        crate::content_uploads::abandoned_origin(previous, &payload)?.unwrap_or(
                            ArtifactContentKey {
                                source_owner: source_writer.owner.clone(),
                                content_digest,
                                byte_size,
                            },
                        ),
                    )
                    .is_some()
            {
                return Err("original new event ID already has retained content ownership".into());
            }
        }
        let mut events = previous.events.clone();
        events.extend(batch.events.clone());
        recovery
            .validate_for(&events, &batch.checkpoint)
            .map_err(|error| error.to_string())?;
        let inputs = required_inputs(&recovery, &events, &origins)?;
        let result = Self {
            schema: CONTENT_APPLICATION_SCHEMA.into(),
            profile_identity: profile_identity.into(),
            epoch,
            source_writer,
            batch,
            recovery,
            origins,
            inputs,
            initialization_digest: None,
            source_initialization: previous
                .content
                .as_ref()
                .and_then(|state| state.source_initialization.clone()),
        };
        result.validate_against(previous, profile_identity)?;
        Ok(result)
    }

    /// Validates complete original metadata lineage; real adapters still authenticate epoch/bytes/membership.
    pub fn validate_against(&self, previous: &ReplicaView, profile: &str) -> Result<(), String> {
        self.validate_context_batch()?;
        let initializing = self.schema == CONTENT_INITIALIZATION_SCHEMA;
        if initializing && !previous.is_empty_application() {
            return Err(
                "original content initialization requires an empty receiver application".into(),
            );
        }
        if !initializing
            && self.source_initialization.as_ref()
                != previous
                    .content
                    .as_ref()
                    .and_then(|state| state.source_initialization.as_ref())
        {
            return Err(
                "later content candidate changed or lost original initialization lineage".into(),
            );
        }
        if self.profile_identity != profile
            || !crate::content::hex_digest(profile)
            || self.epoch.term == 0
            || self.epoch.replica_id == 0
            || self.source_writer.inode == 0
            || self
                .source_writer
                .owner
                .strip_prefix("artifact-store:")
                .is_none_or(|value| !crate::content::hex_digest(value))
            || self.batch.previous_event_sequence
                != previous
                    .checkpoint
                    .as_ref()
                    .map_or(0, |checkpoint| checkpoint.event_sequence)
            || self.batch.previous_checkpoint_digest
                != previous.checkpoint.as_ref().map(state::checkpoint_identity)
            || (previous.revision > 0 && previous.content.is_none())
        {
            return Err(
                "complete private application identity/predecessor/original source differs".into(),
            );
        }
        let old_floor = previous
            .content
            .as_ref()
            .map(|content| content.recovery.history_floor())
            .transpose()
            .map_err(|error| error.to_string())?
            .unwrap_or(0);
        if self
            .recovery
            .history_floor()
            .map_err(|error| error.to_string())?
            != old_floor
            && !initializing
        {
            return Err(
                "original Archive floor advancement requires coordinated complete maintenance"
                    .into(),
            );
        }
        if previous.content.as_ref().is_some_and(|content| {
            content
                .origins
                .iter()
                .any(|(id, origin)| self.origins.get(id) != Some(origin))
        }) {
            return Err("new candidate changed original historical event ownership".into());
        }
        let existing_origins = previous.content.as_ref().map(|content| &content.origins);
        let mut new_origins = BTreeMap::new();
        for row in self
            .recovery
            .catalog_archive_events
            .iter()
            .flatten()
            .filter(|_| initializing)
            .chain(self.batch.events.iter())
        {
            let payload: EventPayload =
                serde_json::from_str(&row.payload_json).map_err(|e| e.to_string())?;
            if let Some((content_digest, byte_size)) = bare_content(&payload) {
                let origin = crate::content_uploads::abandoned_origin(previous, &payload)?
                    .unwrap_or(ArtifactContentKey {
                        source_owner: self.source_writer.owner.clone(),
                        content_digest,
                        byte_size,
                    });
                if new_origins.insert(row.event_id.clone(), origin).is_some() {
                    return Err(
                        "original source initialization contains a duplicate event identity".into(),
                    );
                }
            }
        }
        if self.origins.iter().any(|(id, origin)| {
            existing_origins.is_none_or(|original| !original.contains_key(id))
                && new_origins.get(id) != Some(origin)
        }) {
            return Err(
                "new bare-content origin was not produced by the actual original source writer"
                    .into(),
            );
        }
        if let Some(content) = &previous.content {
            self.recovery
                .follows(&content.recovery)
                .map_err(|error| error.to_string())?;
        }
        let mut events = previous.events.clone();
        events.extend(self.batch.events.clone());
        self.recovery
            .validate_for(&events, &self.batch.checkpoint)
            .map_err(|error| error.to_string())?;
        if required_inputs(&self.recovery, &events, &self.origins)? != self.inputs {
            return Err("complete private application dropped or changed original content".into());
        }
        if serde_json::to_vec(self)
            .map_err(|error| error.to_string())?
            .len()
            > 256 * 1024 * 1024
        {
            return Err("complete private application exceeds its bound".into());
        }
        Ok(())
    }

    /// Hashes the complete original envelope; no checkpoint-only identity can stand in for this target.
    pub fn identity(&self) -> Result<String, String> {
        let value = serde_json::to_value(self).map_err(|error| error.to_string())?;
        Ok(format!(
            "{:x}",
            Sha256::digest(serde_json::to_vec(&value).map_err(|error| error.to_string())?)
        ))
    }

    /// Retains original accepted metadata separately from member-local receiver/reader receipts.
    pub fn accepted_state(&self) -> Result<AcceptedContentState, String> {
        Ok(AcceptedContentState {
            batch_digest: self.identity()?,
            candidate: Box::new(self.clone()),
            source_initialization: self.initialization_anchor()?,
        })
    }
}
