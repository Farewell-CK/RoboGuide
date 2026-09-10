//! Rebuildable Spatial Memory catalog projection.
//!
//! This projection stores manifest and replica metadata only.  Artifact bytes remain in the
//! content-addressed blob store, and no projection method starts execution, selects an active map,
//! or transfers ownership between Missions.

use domain::{
    EventPayload, EventRecord, MapArtifactManifest, MapReplicaSnapshot, MapReplicaStatus,
    MapRevisionSelector, MapRevisionSnapshot, MapRevisionStatus, NodeId, TimestampMs,
};
use ports::{MapCatalogError, MapCatalogReader, MapCatalogWriter};
use std::collections::BTreeMap;

/// Event-replay projection of immutable map revisions and node-local replicas.
#[derive(Debug, Clone, Default)]
pub struct MapCatalogProjection {
    /// Global map revision metadata indexed in deterministic selector order.
    revisions: BTreeMap<MapRevisionSelector, MapRevisionSnapshot>,
    /// Per-node replica metadata indexed by selector and node identity.
    replicas: BTreeMap<MapRevisionSelector, BTreeMap<NodeId, MapReplicaSnapshot>>,
}

/// One node-local replica projection update applied at a RoboGuide receive time.
struct ReplicaUpdate<'a> {
    /// Incoming replica lifecycle.
    status: MapReplicaStatus,
    /// Mission associated with the evidence.
    mission_id: &'a domain::MissionId,
    /// RoboGuide-local receive time used for projection ordering.
    observed_at: TimestampMs,
    /// Optional terminal rejection diagnostic.
    rejection_reason: Option<String>,
    /// Optional complete strong localization evidence.
    localization_evidence: Option<domain::LocalizationVerificationEvidence>,
}

impl MapCatalogProjection {
    /// Creates an empty catalog projection.
    pub const fn new() -> Self {
        Self {
            revisions: BTreeMap::new(),
            replicas: BTreeMap::new(),
        }
    }

    /// Returns the number of known immutable revisions.
    pub fn revision_count(&self) -> usize {
        self.revisions.len()
    }

    /// Returns all revision snapshots in deterministic selector order.
    pub fn revision_snapshots(&self) -> Vec<MapRevisionSnapshot> {
        self.revisions.values().cloned().collect()
    }

    /// Returns all replica snapshots in deterministic selector and node order.
    pub fn replica_snapshots(&self) -> Vec<MapReplicaSnapshot> {
        self.replicas
            .values()
            .flat_map(|replicas| replicas.values().cloned())
            .collect()
    }

    /// Removes all projected metadata so a caller can replay the evidence log from the beginning.
    pub fn clear(&mut self) {
        self.revisions.clear();
        self.replicas.clear();
    }

    /// Rebuilds a catalog projection by replaying events in their supplied order.
    ///
    /// Unrelated Control, Runtime, and Node events are ignored.  A conflicting map event stops
    /// the rebuild and leaves the partially built projection discarded by the returned error.
    pub fn from_events<I>(events: I) -> Result<Self, MapCatalogError>
    where
        I: IntoIterator<Item = EventRecord>,
    {
        let mut projection = Self::new();
        projection.apply_events(events)?;
        Ok(projection)
    }

    /// Replays events into this projection without mutating it when an error is encountered.
    pub fn apply_events<I>(&mut self, events: I) -> Result<(), MapCatalogError>
    where
        I: IntoIterator<Item = EventRecord>,
    {
        let mut candidate = self.clone();
        for event in events {
            candidate.apply_event(&event)?;
        }
        *self = candidate;
        Ok(())
    }

    /// Applies a manifest declaration while preserving an already-published revision.
    fn declare_manifest(
        &mut self,
        manifest: &MapArtifactManifest,
        status: MapRevisionStatus,
    ) -> Result<(), MapCatalogError> {
        let selector = manifest.selector().clone();
        if let Some(existing) = self.revisions.get(&selector) {
            if existing.manifest() != manifest {
                return Err(MapCatalogError::RevisionConflict(format!(
                    "manifest for {selector} differs from the existing immutable revision"
                )));
            }
            if existing.status() == MapRevisionStatus::Published {
                return Ok(());
            }
            self.revisions
                .insert(selector, existing.with_status(status));
            return Ok(());
        }
        self.revisions
            .insert(selector, MapRevisionSnapshot::new(manifest.clone(), status));
        Ok(())
    }

    /// Confirms a replica event references the exact immutable manifest in the catalog.
    fn validate_replica_manifest(
        &self,
        manifest: &MapArtifactManifest,
    ) -> Result<(), MapCatalogError> {
        let selector = manifest.selector();
        let existing = self
            .revisions
            .get(selector)
            .ok_or_else(|| MapCatalogError::UnknownRevision(selector.clone()))?;
        if existing.manifest() != manifest {
            return Err(MapCatalogError::RevisionConflict(format!(
                "replica event for {selector} carries a conflicting manifest"
            )));
        }
        if existing.status() != MapRevisionStatus::Published {
            return Err(MapCatalogError::InvalidReplicaTransition(format!(
                "map revision {selector} is not published"
            )));
        }
        Ok(())
    }

    /// Applies a node-local replica status while enforcing monotonic lifecycle transitions.
    fn set_replica(
        &mut self,
        selector: &MapRevisionSelector,
        node_id: &NodeId,
        update: ReplicaUpdate<'_>,
    ) -> Result<(), MapCatalogError> {
        if let Some(existing) = self
            .replicas
            .get(selector)
            .and_then(|replicas| replicas.get(node_id))
        {
            if let (Some(current), Some(incoming)) = (
                existing.localization_evidence(),
                update.localization_evidence.as_ref(),
            ) && current.execution_id() == incoming.execution_id()
                && current != incoming
            {
                return Err(MapCatalogError::InvalidReplicaTransition(format!(
                    "execution {} reported conflicting localization evidence",
                    incoming.execution_id()
                )));
            }
            if update.observed_at < existing.observed_at() {
                return Err(MapCatalogError::InvalidReplicaTransition(format!(
                    "older observation for node {node_id}"
                )));
            }
            if is_redundant_lower_replica_evidence(existing.status(), update.status) {
                return Ok(());
            }
            if !is_valid_replica_transition(existing.status(), update.status) {
                return Err(MapCatalogError::InvalidReplicaTransition(format!(
                    "cannot move node {node_id} from {:?} to {:?}",
                    existing.status(),
                    update.status
                )));
            }
        } else if !matches!(
            update.status,
            MapReplicaStatus::Staged | MapReplicaStatus::Rejected
        ) {
            return Err(MapCatalogError::InvalidReplicaTransition(format!(
                "node {node_id} must stage map revision {selector} before reporting {:?}",
                update.status
            )));
        }
        let localization_evidence = update.localization_evidence.or_else(|| {
            self.replicas
                .get(selector)
                .and_then(|replicas| replicas.get(node_id))
                .and_then(MapReplicaSnapshot::localization_evidence)
                .cloned()
        });
        self.replicas.entry(selector.clone()).or_default().insert(
            node_id.clone(),
            MapReplicaSnapshot::new(
                selector.clone(),
                node_id.clone(),
                update.status,
                update.mission_id.clone(),
                update.observed_at,
                update.rejection_reason,
                localization_evidence,
            ),
        );
        Ok(())
    }

    /// Applies one map-specific event using its event-record timestamp.
    fn apply_map_payload(
        &mut self,
        timestamp: TimestampMs,
        payload: &EventPayload,
    ) -> Result<(), MapCatalogError> {
        match payload {
            EventPayload::MapArtifactDeclared { manifest } => {
                self.declare_manifest(manifest, MapRevisionStatus::Declared)
            }
            EventPayload::MapArtifactPublished { manifest } => {
                self.declare_manifest(manifest, MapRevisionStatus::Published)
            }
            EventPayload::MapArtifactStaged {
                manifest,
                node_id,
                mission_id,
            } => {
                self.validate_replica_manifest(manifest)?;
                self.set_replica(
                    manifest.selector(),
                    node_id,
                    ReplicaUpdate {
                        status: MapReplicaStatus::Staged,
                        mission_id,
                        observed_at: timestamp,
                        rejection_reason: None,
                        localization_evidence: None,
                    },
                )
            }
            EventPayload::MapArtifactImported {
                manifest,
                node_id,
                mission_id,
            } => {
                self.validate_replica_manifest(manifest)?;
                self.set_replica(
                    manifest.selector(),
                    node_id,
                    ReplicaUpdate {
                        status: MapReplicaStatus::Imported,
                        mission_id,
                        observed_at: timestamp,
                        rejection_reason: None,
                        localization_evidence: None,
                    },
                )
            }
            EventPayload::MapLocalizationVerified {
                artifact,
                node_id,
                mission_id,
                anchor_id,
            } => {
                let revision = self
                    .revisions
                    .get(artifact.selector())
                    .ok_or_else(|| MapCatalogError::UnknownRevision(artifact.selector().clone()))?;
                if revision.manifest().artifact() != artifact {
                    return Err(MapCatalogError::RevisionConflict(format!(
                        "verification event for {} carries a conflicting artifact reference",
                        artifact.selector()
                    )));
                }
                if revision.manifest().anchor_id() != anchor_id {
                    return Err(MapCatalogError::InvalidReplicaTransition(format!(
                        "verification anchor for {} does not match the manifest",
                        artifact.selector()
                    )));
                }
                self.set_replica(
                    artifact.selector(),
                    node_id,
                    ReplicaUpdate {
                        status: MapReplicaStatus::Verified,
                        mission_id,
                        observed_at: timestamp,
                        rejection_reason: None,
                        localization_evidence: None,
                    },
                )
            }
            EventPayload::MapLocalizationEvidenceRecorded { evidence } => {
                let revision = self
                    .revisions
                    .get(evidence.artifact().selector())
                    .ok_or_else(|| {
                        MapCatalogError::UnknownRevision(evidence.artifact().selector().clone())
                    })?;
                if revision.manifest().artifact() != evidence.artifact() {
                    return Err(MapCatalogError::RevisionConflict(format!(
                        "localization evidence for {} carries a conflicting artifact reference",
                        evidence.artifact().selector()
                    )));
                }
                if revision.manifest().anchor_id() != evidence.anchor_id() {
                    return Err(MapCatalogError::InvalidReplicaTransition(format!(
                        "localization evidence anchor for {} does not match the manifest",
                        evidence.artifact().selector()
                    )));
                }
                self.set_replica(
                    evidence.artifact().selector(),
                    evidence.node_id(),
                    ReplicaUpdate {
                        status: MapReplicaStatus::Verified,
                        mission_id: evidence.mission_id(),
                        observed_at: timestamp,
                        rejection_reason: None,
                        localization_evidence: Some(evidence.clone()),
                    },
                )
            }
            EventPayload::MapArtifactRejected {
                artifact,
                node_id,
                mission_id,
                reason,
            } => {
                let revision = self
                    .revisions
                    .get(artifact.selector())
                    .ok_or_else(|| MapCatalogError::UnknownRevision(artifact.selector().clone()))?;
                if revision.manifest().artifact() != artifact {
                    return Err(MapCatalogError::RevisionConflict(format!(
                        "rejection event for {} carries a conflicting artifact reference",
                        artifact.selector()
                    )));
                }
                self.set_replica(
                    artifact.selector(),
                    node_id,
                    ReplicaUpdate {
                        status: MapReplicaStatus::Rejected,
                        mission_id,
                        observed_at: timestamp,
                        rejection_reason: Some(reason.clone()),
                        localization_evidence: None,
                    },
                )
            }
            // A catalog projection is replayed from the shared evidence log. Unrelated control,
            // runtime, and node events are intentionally ignored rather than treated as replay
            // failures.
            _ => Ok(()),
        }
    }
}

impl MapCatalogReader for MapCatalogProjection {
    /// Returns a cloned revision snapshot so callers cannot mutate State directly.
    fn revision(&self, selector: &MapRevisionSelector) -> Option<MapRevisionSnapshot> {
        self.revisions.get(selector).cloned()
    }

    /// Returns cloned replicas in deterministic node order.
    fn replicas(&self, selector: &MapRevisionSelector) -> Vec<MapReplicaSnapshot> {
        self.replicas
            .get(selector)
            .map(|replicas| replicas.values().cloned().collect())
            .unwrap_or_default()
    }

    /// Returns cloned revisions in deterministic selector order.
    fn revisions(&self) -> Vec<MapRevisionSnapshot> {
        self.revision_snapshots()
    }
}

impl MapCatalogWriter for MapCatalogProjection {
    /// Applies one immutable event envelope to the catalog projection.
    fn apply_event(&mut self, event: &EventRecord) -> Result<(), MapCatalogError> {
        self.apply_map_payload(event.timestamp(), event.payload())
    }

    /// Applies a map payload with an explicit RoboGuide-local timestamp.
    fn apply_payload(
        &mut self,
        timestamp: TimestampMs,
        payload: &EventPayload,
    ) -> Result<(), MapCatalogError> {
        self.apply_map_payload(timestamp, payload)
    }
}

/// Checks whether a replica status transition preserves evidence ordering and monotonicity.
fn is_valid_replica_transition(current: MapReplicaStatus, incoming: MapReplicaStatus) -> bool {
    match (current, incoming) {
        (MapReplicaStatus::Staged, MapReplicaStatus::Staged)
        | (MapReplicaStatus::Staged, MapReplicaStatus::Imported)
        | (MapReplicaStatus::Staged, MapReplicaStatus::Rejected)
        | (MapReplicaStatus::Imported, MapReplicaStatus::Imported)
        | (MapReplicaStatus::Imported, MapReplicaStatus::Verified)
        | (MapReplicaStatus::Imported, MapReplicaStatus::Rejected)
        | (MapReplicaStatus::Verified, MapReplicaStatus::Verified)
        | (MapReplicaStatus::Rejected, MapReplicaStatus::Rejected) => true,
        // A verified or rejected replica is terminal for this v0 projection. Lower evidence
        // from a later Mission is handled as an idempotent no-op before this predicate.
        _ => false,
    }
}

/// Returns whether later evidence repeats an already-proven lower replica phase.
fn is_redundant_lower_replica_evidence(
    current: MapReplicaStatus,
    incoming: MapReplicaStatus,
) -> bool {
    matches!(
        (current, incoming),
        (MapReplicaStatus::Imported, MapReplicaStatus::Staged)
            | (MapReplicaStatus::Verified, MapReplicaStatus::Staged)
            | (MapReplicaStatus::Verified, MapReplicaStatus::Imported)
    )
}

#[cfg(test)]
#[path = "spatial_memory_tests.rs"]
mod tests;
