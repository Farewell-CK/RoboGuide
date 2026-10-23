//! Exact actual Raft parent observations for the independent member-local producer journal.

use super::*;
use crate::content_store::{ProducerAdmission, ProducerPermit};

#[cfg(test)]
#[path = "store_producer_tests.rs"]
mod tests;

/// Mandatory member-local heads never travel in a Snapshot or default after source loss.
pub(super) const HEADS_KEY: &str = "content_producer_heads";

/// One exact current local retention responsibility and its actual parent record identity.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct ProducerHead {
    /// Actual member-local original operation, independent of any sending member's operation.
    pub operation_id: String,
    /// Complete actual current parent row identity, checked before any absence/retirement decision.
    pub record_digest: String,
}

/// Mandatory original local heads; a legitimately empty current view still has this complete row.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct ProducerHeads {
    /// Closed local source semantics.
    schema: String,
    /// Exact current local accepted-view producer, absent only before complete authority exists.
    pub view: Option<ProducerHead>,
    /// Exact current local Snapshot publisher/installer, absent only before a Snapshot exists.
    pub snapshot: Option<ProducerHead>,
}

impl Default for ProducerHeads {
    /// Initializes only the new version-fenced parent database before any actual authority exists.
    fn default() -> Self {
        Self {
            schema: "roboguide.content-producer-heads/v0.1".into(),
            view: None,
            snapshot: None,
        }
    }
}

/// Hashes the actual serialized view row, including its original nullable encoding.
pub(super) fn view_record_digest(
    content: &Option<AcceptedContentState>,
) -> Result<String, StorageError<u64>> {
    Ok(format!(
        "{:x}",
        Sha256::digest(serde_json::to_vec(content).map_err(io_error)?)
    ))
}

/// Binds exact actual Snapshot pointers and bytes without borrowing the sender's operational identity.
pub(super) fn snapshot_record_digest(
    meta: &SnapshotMeta<u64, BasicNode>,
    data: &[u8],
) -> Result<String, StorageError<u64>> {
    Ok(format!(
        "{:x}",
        Sha256::digest(
            serde_json::to_vec(&(meta, format!("{:x}", Sha256::digest(data)))).map_err(io_error)?
        )
    ))
}

/// Requires a complete original head and exact actual current parent rows; absence is never inferred.
pub(super) fn read_heads(connection: &Connection) -> Result<ProducerHeads, StorageError<u64>> {
    let heads: ProducerHeads = read_meta(connection, HEADS_KEY)?
        .ok_or_else(|| io_error("mandatory original local producer heads are missing"))?;
    if heads.schema != "roboguide.content-producer-heads/v0.1" {
        return Err(io_error("original local producer head schema differs"));
    }
    for head in [&heads.view, &heads.snapshot].into_iter().flatten() {
        if !head.operation_id.starts_with("producer:")
            || head.operation_id.len() != 73
            || !crate::content::hex_digest(&head.operation_id[9..])
            || !crate::content::hex_digest(&head.record_digest)
        {
            return Err(io_error(
                "original local producer head identity is incomplete",
            ));
        }
    }
    let view: Option<AcceptedContentState> = read_meta(connection, "content_state")?
        .ok_or_else(|| io_error("mandatory original complete content state is missing"))?;
    if heads.view.is_some() != view.is_some()
        || heads.view.as_ref().is_some_and(|head| {
            !view_record_digest(&view).is_ok_and(|digest| digest == head.record_digest)
        })
    {
        return Err(io_error(
            "original current view producer head is missing or changed",
        ));
    }
    let snapshot: Option<(String, Vec<u8>)> = connection
        .query_row(
            "SELECT meta,data FROM replica_v1_snapshot WHERE singleton=1",
            [],
            |row| Ok((row.get(0)?, row.get(1)?)),
        )
        .optional()
        .map_err(io_error)?;
    match (&heads.snapshot, snapshot) {
        (None, None) => {}
        (Some(head), Some((meta, data))) => {
            let meta: SnapshotMeta<u64, BasicNode> =
                serde_json::from_str(&meta).map_err(io_error)?;
            if snapshot_record_digest(&meta, &data)? != head.record_digest {
                return Err(io_error(
                    "original current Snapshot producer head changed its actual record",
                ));
            }
        }
        _ => {
            return Err(io_error(
                "original current Snapshot producer head is missing or changed",
            ));
        }
    }
    Ok(heads)
}

impl SqliteRaftStore {
    /// Freezes one actual original apply's complete view responsibility before entering its SQL writer.
    pub(super) fn retain_view_inputs(
        &self,
        entry: &Entry<ControllerRaftConfig>,
    ) -> Result<Option<ProducerAdmission>, StorageError<u64>> {
        let EntryPayload::Normal(ReplicaCommand::ContentApplication {
            content_application: candidate,
        }) = &entry.payload
        else {
            return Ok(None);
        };
        if !self.has_producer_journal() {
            return Ok(None);
        }
        self.begin_content_producer(
            ReplicaContentProducerSource {
                kind: ReplicaContentProducerKind::View,
                epoch: candidate.epoch,
                position: serde_json::to_string(&entry.log_id).map_err(io_error)?,
                authority_digest: candidate.identity().map_err(io_error)?,
                parent_record_digest: view_record_digest(&Some(
                    candidate.accepted_state().map_err(io_error)?,
                ))?,
                checkpoint_digest: Some(state::checkpoint_identity(&candidate.batch.checkpoint)),
            },
            &candidate.inputs,
        )
    }

    /// Records publication only for exact heads still owned by these actual scopes after parent Commit.
    pub(super) fn complete_view_producers(
        &self,
        connection: &Connection,
        permits: Vec<ProducerPermit>,
    ) -> Result<(), StorageError<u64>> {
        self.validate_content_producer_heads(connection)
            .map_err(io_error)?;
        if permits.is_empty() {
            return Ok(());
        }
        let heads = read_heads(connection)?;
        for permit in permits {
            if heads
                .view
                .as_ref()
                .is_some_and(|head| head.operation_id == permit.operation_id())
            {
                let original = self
                    .content
                    .as_ref()
                    .ok_or_else(|| io_error("original content backend is missing"))?
                    .producer_status(permit.operation_id())
                    .map_err(io_error)?
                    .ok_or_else(|| io_error("original view producer journal disappeared"))?;
                let (present, proof) = self.producer_is_referenced(connection, &original)?;
                if !present {
                    return Err(io_error("actual original view publication is absent"));
                }
                self.publish_content_producer(Some(Box::new(permit)), &proof)?;
            }
        }
        self.reconcile_content_producers_locked(connection)
            .map_err(io_error)?;
        Ok(())
    }

    /// Selects only the explicitly opened producer profile; old reader/copy stores keep their old meaning.
    pub(super) fn has_producer_journal(&self) -> bool {
        self.content
            .as_ref()
            .is_some_and(ReplicaContentStore::producer_journal_enabled)
    }

    /// Freezes a complete actual parent operation before any file/SQL effect; duplicate IDs are readonly.
    pub(super) fn begin_content_producer(
        &self,
        source: ReplicaContentProducerSource,
        inputs: &[ArtifactContentKey],
    ) -> Result<Option<ProducerAdmission>, StorageError<u64>> {
        if !self.has_producer_journal() {
            return Ok(None);
        }
        let id = format!(
            "producer:{:x}",
            Sha256::digest(
                serde_json::to_vec(&serde_json::to_value(&source).map_err(io_error)?)
                    .map_err(io_error)?
            )
        );
        self.begin_content_producer_for(&id, source, inputs)
    }

    /// Binds one actual explicit storage invocation ID to immutable complete source semantics.
    pub(super) fn begin_content_producer_for(
        &self,
        id: &str,
        source: ReplicaContentProducerSource,
        inputs: &[ArtifactContentKey],
    ) -> Result<Option<ProducerAdmission>, StorageError<u64>> {
        let Some(content) = self
            .content
            .as_ref()
            .filter(|content| content.producer_journal_enabled())
        else {
            return Ok(None);
        };
        content
            .admit_producer(
                id,
                source,
                self._writer.clone(),
                inputs,
                self.work_clock
                    .as_ref()
                    .map_or(300_000, crate::content_store::ProducerClock::duration_ms),
                self.work_clock.as_ref(),
            )
            .map(Some)
            .map_err(io_error)
    }

    /// Publishes only after the caller's exact actual parent transaction/file has completed durably.
    pub(super) fn publish_content_producer(
        &self,
        permit: Option<Box<ProducerPermit>>,
        proof: &str,
    ) -> Result<(), StorageError<u64>> {
        if let Some(permit) = permit {
            permit.published(proof).map_err(io_error)?;
        }
        Ok(())
    }

    /// Reads all this actual SQL descriptor's original operations before any recovery/source decision.
    pub(super) fn parent_producers(
        &self,
    ) -> Result<Vec<ReplicaContentProducerStatus>, StorageError<u64>> {
        let Some(content) = self
            .content
            .as_ref()
            .filter(|content| content.producer_journal_enabled())
        else {
            return Ok(Vec::new());
        };
        content
            .producers_for_parent(&self._writer)
            .map_err(io_error)
    }

    /// Observes exact original parent log/view/Snapshot metadata; it never performs original work again.
    pub(super) fn producer_is_referenced(
        &self,
        connection: &Connection,
        original: &ReplicaContentProducerStatus,
    ) -> Result<(bool, String), StorageError<u64>> {
        let source: ReplicaContentProducerSource = serde_json::from_value(
            original
                .original
                .get("intent")
                .and_then(|value| value.get("source"))
                .cloned()
                .ok_or_else(|| {
                    io_error("original producer observation lacks its complete typed source")
                })?,
        )
        .map_err(io_error)?;
        let (referenced, evidence) = match source.kind {
            ReplicaContentProducerKind::Log => {
                let id: LogId<u64> = serde_json::from_str(&source.position).map_err(io_error)?;
                let actual: Option<String> = connection
                    .query_row(
                        "SELECT data FROM replica_v1_log WHERE log_index=?1",
                        [id.index],
                        |row| row.get(0),
                    )
                    .optional()
                    .map_err(io_error)?;
                let present = actual.as_ref().is_some_and(|raw| {
                    format!("{:x}", Sha256::digest(raw.as_bytes())) == source.parent_record_digest
                });
                (
                    present,
                    serde_json::to_value((&id, &actual)).map_err(io_error)?,
                )
            }
            ReplicaContentProducerKind::View => {
                let view = read_view(connection)?;
                let heads = read_heads(connection)?;
                let present = heads.view.as_ref().is_some_and(|head| {
                    head.operation_id == original.operation_id
                        && head.record_digest == source.parent_record_digest
                });
                (
                    present,
                    serde_json::to_value((
                        &heads.view,
                        &view.revision,
                        &view.checkpoint,
                        &view.content,
                    ))
                    .map_err(io_error)?,
                )
            }
            ReplicaContentProducerKind::SnapshotBuild
            | ReplicaContentProducerKind::SnapshotInstall => {
                let current: Option<(String, Vec<u8>)> = connection
                    .query_row(
                        "SELECT meta,data FROM replica_v1_snapshot WHERE singleton=1",
                        [],
                        |row| Ok((row.get(0)?, row.get(1)?)),
                    )
                    .optional()
                    .map_err(io_error)?;
                let local = read_heads(connection)?.snapshot;
                let present = local.as_ref().is_some_and(|head| {
                    head.operation_id == original.operation_id
                        && head.record_digest == source.parent_record_digest
                });
                (
                    present,
                    serde_json::to_value((
                        local,
                        current
                            .as_ref()
                            .map(|(meta, data)| (meta, format!("{:x}", Sha256::digest(data)))),
                    ))
                    .map_err(io_error)?,
                )
            }
        };
        let proof = format!(
            "{:x}",
            Sha256::digest(
                serde_json::to_vec(
                    &serde_json::to_value((
                        &self.profile,
                        original.intent_digest.as_str(),
                        &source,
                        referenced,
                        evidence
                    ))
                    .map_err(io_error)?
                )
                .map_err(io_error)?
            )
        );
        Ok((referenced, proof))
    }

    /// Reconciles only complete original parents under this actual writer, preserving unknown outcomes.
    pub fn reconcile_content_producers(&self) -> Result<Vec<ReplicaContentProducerStatus>, String> {
        if !self.has_producer_journal() {
            return Err(
                "original producer reconciliation requires its explicit original profile".into(),
            );
        }
        let connection = self.connection.lock().map_err(|error| error.to_string())?;
        self.reconcile_content_producers_locked(&connection)
    }

    /// Reads bounded complete original local producer history without admitting, renewing or reconciling work.
    pub fn content_producer_history(&self) -> Result<Vec<ReplicaContentProducerStatus>, String> {
        if !self.has_producer_journal() {
            return Err("original producer history requires its explicit original profile".into());
        }
        let connection = self.connection.lock().map_err(|error| error.to_string())?;
        self.validate_content_producer_heads(&connection)?;
        self.parent_producers().map_err(|error| error.to_string())
    }

    /// Uses the already-held actual parent writer after its transaction, without recursive acquisition.
    pub(super) fn reconcile_content_producers_locked(
        &self,
        connection: &Connection,
    ) -> Result<Vec<ReplicaContentProducerStatus>, String> {
        self.validate_content_producer_heads(connection)?;
        let originals = self.parent_producers().map_err(|error| error.to_string())?;
        let content = self
            .content
            .as_ref()
            .ok_or("metadata-only Replica has no original producer journal")?;
        let mut result = Vec::new();
        for original in originals {
            let (referenced, proof) = self
                .producer_is_referenced(connection, &original)
                .map_err(|error| error.to_string())?;
            result.push(content.reconcile_producer(
                &original.operation_id,
                &self._writer,
                referenced,
                &proof,
            )?);
        }
        Ok(result)
    }

    /// Verifies actual original head membership in this exact original root/SQL journal before release.
    pub(super) fn validate_content_producer_heads(
        &self,
        connection: &Connection,
    ) -> Result<(), String> {
        if !self.has_producer_journal() {
            return Ok(());
        }
        let heads = read_heads(connection).map_err(|error| error.to_string())?;
        let originals = self.parent_producers().map_err(|error| error.to_string())?;
        for (head, kinds) in [
            (&heads.view, &[ReplicaContentProducerKind::View][..]),
            (
                &heads.snapshot,
                &[
                    ReplicaContentProducerKind::SnapshotBuild,
                    ReplicaContentProducerKind::SnapshotInstall,
                ][..],
            ),
        ] {
            if let Some(head) = head {
                let original = originals
                    .iter()
                    .find(|original| original.operation_id == head.operation_id)
                    .ok_or("original current producer head lost its exact root journal")?;
                let source: ReplicaContentProducerSource =
                    serde_json::from_value(original.original["intent"]["source"].clone())
                        .map_err(|error| error.to_string())?;
                if !kinds.contains(&source.kind)
                    || source.parent_record_digest != head.record_digest
                    || !matches!(original.disposition.as_str(), "unknown" | "published")
                {
                    return Err(
                        "original current producer head points to another or retired operation"
                            .into(),
                    );
                }
            }
        }
        Ok(())
    }
}
