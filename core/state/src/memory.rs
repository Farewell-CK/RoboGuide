//! Rebuildable catalog projection for generic Memory manifests and placement evidence.

use domain::{
    EventPayload, EventRecord, MemoryArtifactManifest, MemoryReplicaSnapshot, MemoryReplicaStatus,
    MemorySelector, NodeId, TimestampMs,
};
use ports::{MemoryCatalogError, MemoryCatalogReader, MemoryCatalogWriter};
use std::collections::BTreeMap;

/// Generic Memory metadata projection; immutable content remains in Artifact storage.
///
/// Manifest scope and visibility remain semantic policy. Replica snapshots are accepted evidence
/// of provider-local placement and do not change either manifest property.
#[derive(Debug, Clone, Default)]
pub struct MemoryCatalogProjection {
    /// Immutable manifests in deterministic selector order.
    manifests: BTreeMap<MemorySelector, MemoryArtifactManifest>,
    /// Per-provider exchange evidence in deterministic selector, node, and provider order.
    replicas: BTreeMap<MemorySelector, BTreeMap<(NodeId, String), MemoryReplicaSnapshot>>,
}

impl MemoryCatalogProjection {
    /// Creates an empty generic Memory catalog.
    pub const fn new() -> Self {
        Self {
            manifests: BTreeMap::new(),
            replicas: BTreeMap::new(),
        }
    }

    /// Rebuilds the projection from shared evidence, ignoring unrelated events.
    pub fn from_events<I>(events: I) -> Result<Self, MemoryCatalogError>
    where
        I: IntoIterator<Item = EventRecord>,
    {
        let mut projection = Self::new();
        for event in events {
            if matches!(
                event.payload(),
                EventPayload::MemoryManifestPublished { .. }
                    | EventPayload::MemoryArtifactStaged { .. }
                    | EventPayload::MemoryArtifactImported { .. }
                    | EventPayload::MemoryArtifactRejected { .. }
            ) {
                projection.apply_memory_event(&event)?;
            }
        }
        Ok(projection)
    }

    /// Applies an immutable manifest idempotently.
    fn publish(&mut self, manifest: &MemoryArtifactManifest) -> Result<(), MemoryCatalogError> {
        manifest
            .validate()
            .map_err(|error| MemoryCatalogError::RevisionConflict(error.to_string()))?;
        if let Some(current) = self.manifests.get(manifest.selector()) {
            if current != manifest {
                return Err(MemoryCatalogError::RevisionConflict(format!(
                    "manifest for {} differs from existing immutable metadata",
                    manifest.selector()
                )));
            }
            return Ok(());
        }
        self.manifests
            .insert(manifest.selector().clone(), manifest.clone());
        Ok(())
    }

    /// Applies one provider-local replica update after validating manifest identity and ordering.
    fn set_replica(
        &mut self,
        manifest: &MemoryArtifactManifest,
        node_id: &NodeId,
        consumer_provider_id: &str,
        status: MemoryReplicaStatus,
        observed_at: TimestampMs,
        rejection_reason: Option<String>,
    ) -> Result<(), MemoryCatalogError> {
        let current_manifest = self
            .manifests
            .get(manifest.selector())
            .ok_or_else(|| MemoryCatalogError::UnknownRevision(manifest.selector().clone()))?;
        if current_manifest != manifest {
            return Err(MemoryCatalogError::RevisionConflict(format!(
                "replica for {} carries different immutable metadata",
                manifest.selector()
            )));
        }
        if current_manifest.visibility() != domain::MemoryVisibility::Exchangeable
            || current_manifest.artifact().is_none()
        {
            return Err(MemoryCatalogError::InvalidReplicaTransition(format!(
                "non-exchangeable memory {} cannot produce replica evidence",
                manifest.selector()
            )));
        }
        let identity = (node_id.clone(), consumer_provider_id.to_string());
        if let Some(current) = self
            .replicas
            .get(manifest.selector())
            .and_then(|replicas| replicas.get(&identity))
        {
            if observed_at < current.observed_at() {
                return Err(MemoryCatalogError::InvalidReplicaTransition(format!(
                    "older replica evidence for node {node_id} provider {consumer_provider_id}"
                )));
            }
            if !valid_transition(current.status(), status) {
                return Err(MemoryCatalogError::InvalidReplicaTransition(format!(
                    "cannot move node {node_id} provider {consumer_provider_id} from {:?} to {status:?}",
                    current.status()
                )));
            }
        } else if !matches!(
            status,
            MemoryReplicaStatus::Staged | MemoryReplicaStatus::Rejected
        ) {
            return Err(MemoryCatalogError::InvalidReplicaTransition(format!(
                "node {node_id} provider {consumer_provider_id} must stage {} before import",
                manifest.selector()
            )));
        }
        let snapshot = MemoryReplicaSnapshot::new(
            manifest.selector().clone(),
            node_id.clone(),
            consumer_provider_id,
            status,
            observed_at,
            rejection_reason,
        )
        .map_err(|error| MemoryCatalogError::InvalidReplicaTransition(error.to_string()))?;
        self.replicas
            .entry(manifest.selector().clone())
            .or_default()
            .insert(identity, snapshot);
        Ok(())
    }
}

impl MemoryCatalogReader for MemoryCatalogProjection {
    /// Returns one owned immutable manifest.
    fn memory(&self, selector: &MemorySelector) -> Option<MemoryArtifactManifest> {
        self.manifests.get(selector).cloned()
    }

    /// Returns every manifest in deterministic selector order.
    fn memories(&self) -> Vec<MemoryArtifactManifest> {
        self.manifests.values().cloned().collect()
    }

    /// Returns provider-local replicas in deterministic node/provider order.
    fn memory_replicas(&self, selector: &MemorySelector) -> Vec<MemoryReplicaSnapshot> {
        self.replicas
            .get(selector)
            .map(|replicas| replicas.values().cloned().collect())
            .unwrap_or_default()
    }
}

impl MemoryCatalogWriter for MemoryCatalogProjection {
    /// Applies one immutable evidence event.
    fn apply_memory_event(&mut self, event: &EventRecord) -> Result<(), MemoryCatalogError> {
        self.apply_memory_payload(event.timestamp(), event.payload())
    }

    /// Applies one Memory payload at an explicit RoboGuide receive time.
    fn apply_memory_payload(
        &mut self,
        timestamp: TimestampMs,
        payload: &EventPayload,
    ) -> Result<(), MemoryCatalogError> {
        match payload {
            EventPayload::MemoryManifestPublished { manifest } => self.publish(manifest),
            EventPayload::MemoryArtifactStaged {
                manifest,
                node_id,
                consumer_provider_id,
            } => self.set_replica(
                manifest,
                node_id,
                consumer_provider_id,
                MemoryReplicaStatus::Staged,
                timestamp,
                None,
            ),
            EventPayload::MemoryArtifactImported {
                manifest,
                node_id,
                consumer_provider_id,
            } => self.set_replica(
                manifest,
                node_id,
                consumer_provider_id,
                MemoryReplicaStatus::Imported,
                timestamp,
                None,
            ),
            EventPayload::MemoryArtifactRejected {
                manifest,
                node_id,
                consumer_provider_id,
                reason,
            } => self.set_replica(
                manifest,
                node_id,
                consumer_provider_id,
                MemoryReplicaStatus::Rejected,
                timestamp,
                Some(reason.clone()),
            ),
            _ => Err(MemoryCatalogError::UnsupportedEvent),
        }
    }
}

/// Returns whether a replica lifecycle transition is monotonic.
fn valid_transition(current: MemoryReplicaStatus, incoming: MemoryReplicaStatus) -> bool {
    matches!(
        (current, incoming),
        (MemoryReplicaStatus::Staged, MemoryReplicaStatus::Staged)
            | (MemoryReplicaStatus::Staged, MemoryReplicaStatus::Imported)
            | (MemoryReplicaStatus::Staged, MemoryReplicaStatus::Rejected)
            | (MemoryReplicaStatus::Imported, MemoryReplicaStatus::Imported)
            | (MemoryReplicaStatus::Rejected, MemoryReplicaStatus::Rejected)
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use domain::{
        ContentDigest, LocalSystemId, MemoryArtifactRef, MemoryId, MemoryKind, MemoryOwner,
        MemoryRevisionId, MemoryVisibility,
    };

    /// Builds one exchangeable generic Memory manifest.
    fn manifest() -> MemoryArtifactManifest {
        MemoryArtifactManifest::new(
            MemorySelector::new(
                MemoryId::new("run-log").expect("memory id should be valid"),
                MemoryRevisionId::new("r1").expect("revision should be valid"),
            ),
            MemoryKind::Execution,
            "execution-journal",
            MemoryOwner::Node {
                node_id: NodeId::new("dog-a").expect("node should be valid"),
                local_system_id: LocalSystemId::new("motion").expect("system should be valid"),
            },
            domain::MemoryScope::Global,
            MemoryVisibility::Exchangeable,
            "example.execution-memory/v1",
            "application/json",
            Some(MemoryArtifactRef::new(
                ContentDigest::new(
                    "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                )
                .expect("digest should be valid"),
                20,
            )),
            None,
            None,
            None,
            TimestampMs::new(1),
        )
        .expect("manifest should be valid")
    }

    /// Manifest publication and selective exchange evidence replay deterministically.
    #[test]
    fn projection_tracks_publish_stage_and_import() {
        let manifest = manifest();
        let node_id = NodeId::new("dog-b").expect("node should be valid");
        let mut projection = MemoryCatalogProjection::new();
        projection
            .apply_memory_payload(
                TimestampMs::new(1),
                &EventPayload::MemoryManifestPublished {
                    manifest: manifest.clone(),
                },
            )
            .expect("manifest should publish");
        projection
            .apply_memory_payload(
                TimestampMs::new(2),
                &EventPayload::MemoryArtifactStaged {
                    manifest: manifest.clone(),
                    node_id: node_id.clone(),
                    consumer_provider_id: "execution-consumer".to_string(),
                },
            )
            .expect("staging should apply");
        projection
            .apply_memory_payload(
                TimestampMs::new(3),
                &EventPayload::MemoryArtifactImported {
                    manifest: manifest.clone(),
                    node_id,
                    consumer_provider_id: "execution-consumer".to_string(),
                },
            )
            .expect("import should apply");
        assert_eq!(projection.memories(), vec![manifest.clone()]);
        assert_eq!(
            projection.memory_replicas(manifest.selector())[0].status(),
            MemoryReplicaStatus::Imported
        );
        assert_eq!(
            projection.memory_replicas(manifest.selector())[0].consumer_provider_id(),
            "execution-consumer"
        );
    }

    /// Import cannot appear before staging evidence.
    #[test]
    fn projection_rejects_import_without_stage() {
        let manifest = manifest();
        let mut projection = MemoryCatalogProjection::new();
        projection
            .apply_memory_payload(
                TimestampMs::new(1),
                &EventPayload::MemoryManifestPublished {
                    manifest: manifest.clone(),
                },
            )
            .expect("manifest should publish");
        assert!(matches!(
            projection.apply_memory_payload(
                TimestampMs::new(2),
                &EventPayload::MemoryArtifactImported {
                    manifest,
                    node_id: NodeId::new("dog-b").expect("node should be valid"),
                    consumer_provider_id: "execution-consumer".to_string(),
                },
            ),
            Err(MemoryCatalogError::InvalidReplicaTransition(_))
        ));
    }

    /// A later failed attempt cannot erase durable evidence that the replica was imported.
    #[test]
    fn projection_rejects_imported_to_rejected_regression() {
        let manifest = manifest();
        let node_id = NodeId::new("dog-b").expect("node should be valid");
        let mut projection = MemoryCatalogProjection::new();
        for (timestamp, payload) in [
            (
                1,
                EventPayload::MemoryManifestPublished {
                    manifest: manifest.clone(),
                },
            ),
            (
                2,
                EventPayload::MemoryArtifactStaged {
                    manifest: manifest.clone(),
                    node_id: node_id.clone(),
                    consumer_provider_id: "execution-consumer".to_string(),
                },
            ),
            (
                3,
                EventPayload::MemoryArtifactImported {
                    manifest: manifest.clone(),
                    node_id: node_id.clone(),
                    consumer_provider_id: "execution-consumer".to_string(),
                },
            ),
        ] {
            projection
                .apply_memory_payload(TimestampMs::new(timestamp), &payload)
                .expect("setup transition should apply");
        }
        assert!(matches!(
            projection.apply_memory_payload(
                TimestampMs::new(4),
                &EventPayload::MemoryArtifactRejected {
                    manifest,
                    node_id,
                    consumer_provider_id: "execution-consumer".to_string(),
                    reason: "later request was invalid".to_string(),
                },
            ),
            Err(MemoryCatalogError::InvalidReplicaTransition(_))
        ));
    }

    /// Discoverable metadata cannot become exchange evidence merely by carrying an artifact ref.
    #[test]
    fn projection_rejects_replica_for_discoverable_manifest() {
        let exchangeable = manifest();
        let discoverable = MemoryArtifactManifest::new(
            exchangeable.selector().clone(),
            exchangeable.kind(),
            exchangeable.provider_id(),
            exchangeable.owner().clone(),
            exchangeable.scope().clone(),
            MemoryVisibility::Discoverable,
            exchangeable.payload_schema(),
            exchangeable.media_type(),
            exchangeable.artifact().cloned(),
            exchangeable.source_mission_id().cloned(),
            exchangeable.source_execution_id().map(str::to_string),
            exchangeable.source_task_ref().cloned(),
            exchangeable.created_at(),
        )
        .expect("discoverable manifest remains valid metadata");
        let mut projection = MemoryCatalogProjection::new();
        projection
            .apply_memory_payload(
                TimestampMs::new(1),
                &EventPayload::MemoryManifestPublished {
                    manifest: discoverable.clone(),
                },
            )
            .expect("discoverable manifest publishes");

        assert!(matches!(
            projection.apply_memory_payload(
                TimestampMs::new(2),
                &EventPayload::MemoryArtifactStaged {
                    manifest: discoverable,
                    node_id: NodeId::new("dog-b").expect("node id should be valid"),
                    consumer_provider_id: "execution-consumer".to_string(),
                },
            ),
            Err(MemoryCatalogError::InvalidReplicaTransition(_))
        ));
    }

    /// Two providers on one Node retain independent lifecycle and deterministic identity.
    #[test]
    fn projection_isolates_replicas_by_consumer_provider() {
        let manifest = manifest();
        let node_id = NodeId::new("dog-b").expect("node should be valid");
        let mut projection = MemoryCatalogProjection::new();
        projection
            .apply_memory_payload(
                TimestampMs::new(1),
                &EventPayload::MemoryManifestPublished {
                    manifest: manifest.clone(),
                },
            )
            .expect("manifest should publish");
        for (timestamp, provider_id) in [(2, "archive-a"), (3, "archive-b")] {
            projection
                .apply_memory_payload(
                    TimestampMs::new(timestamp),
                    &EventPayload::MemoryArtifactStaged {
                        manifest: manifest.clone(),
                        node_id: node_id.clone(),
                        consumer_provider_id: provider_id.to_string(),
                    },
                )
                .expect("each provider should stage independently");
        }
        projection
            .apply_memory_payload(
                TimestampMs::new(4),
                &EventPayload::MemoryArtifactImported {
                    manifest: manifest.clone(),
                    node_id,
                    consumer_provider_id: "archive-a".to_string(),
                },
            )
            .expect("one provider should import independently");
        projection
            .apply_memory_payload(
                TimestampMs::new(5),
                &EventPayload::MemoryArtifactRejected {
                    manifest: manifest.clone(),
                    node_id: NodeId::new("dog-b").expect("node should be valid"),
                    consumer_provider_id: "archive-b".to_string(),
                    reason: "provider-local rejection".to_string(),
                },
            )
            .expect("the other provider should reject independently");

        let replicas = projection.memory_replicas(manifest.selector());
        assert_eq!(replicas.len(), 2);
        assert_eq!(replicas[0].consumer_provider_id(), "archive-a");
        assert_eq!(replicas[0].status(), MemoryReplicaStatus::Imported);
        assert_eq!(replicas[1].consumer_provider_id(), "archive-b");
        assert_eq!(replicas[1].status(), MemoryReplicaStatus::Rejected);
        assert_eq!(
            replicas[1].rejection_reason(),
            Some("provider-local rejection")
        );
    }
}
