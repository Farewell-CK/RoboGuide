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
        status: MapReplicaStatus,
        mission_id: &domain::MissionId,
        observed_at: TimestampMs,
        rejection_reason: Option<String>,
    ) -> Result<(), MapCatalogError> {
        if let Some(existing) = self
            .replicas
            .get(selector)
            .and_then(|replicas| replicas.get(node_id))
        {
            if observed_at < existing.observed_at() {
                return Err(MapCatalogError::InvalidReplicaTransition(format!(
                    "older observation for node {node_id}"
                )));
            }
            if is_redundant_lower_replica_evidence(existing.status(), status) {
                return Ok(());
            }
            if !is_valid_replica_transition(existing.status(), status) {
                return Err(MapCatalogError::InvalidReplicaTransition(format!(
                    "cannot move node {node_id} from {:?} to {:?}",
                    existing.status(),
                    status
                )));
            }
        } else if !matches!(
            status,
            MapReplicaStatus::Staged | MapReplicaStatus::Rejected
        ) {
            return Err(MapCatalogError::InvalidReplicaTransition(format!(
                "node {node_id} must stage map revision {selector} before reporting {status:?}"
            )));
        }
        self.replicas.entry(selector.clone()).or_default().insert(
            node_id.clone(),
            MapReplicaSnapshot::new(
                selector.clone(),
                node_id.clone(),
                status,
                mission_id.clone(),
                observed_at,
                rejection_reason,
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
                    MapReplicaStatus::Staged,
                    mission_id,
                    timestamp,
                    None,
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
                    MapReplicaStatus::Imported,
                    mission_id,
                    timestamp,
                    None,
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
                    MapReplicaStatus::Verified,
                    mission_id,
                    timestamp,
                    None,
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
                    MapReplicaStatus::Rejected,
                    mission_id,
                    timestamp,
                    Some(reason.clone()),
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
mod tests {
    use super::*;
    use domain::{
        ContentDigest, EventId, EventPayload, MapArtifactRef, MapId, MapRevisionId,
        MapRevisionSelector, MissionId, NodeId, SpatialAnchorId,
    };

    /// Builds one valid manifest used by projection transition tests.
    fn manifest() -> MapArtifactManifest {
        MapArtifactManifest::new(
            MapArtifactRef::new(
                MapRevisionSelector::new(
                    MapId::new("warehouse").expect("map id is valid"),
                    MapRevisionId::new("r1").expect("revision id is valid"),
                ),
                ContentDigest::new(
                    "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                )
                .expect("digest is valid"),
                10,
            ),
            "application/octet-stream",
            "grid-v1",
            NodeId::new("dog-a").expect("node id is valid"),
            None,
            MissionId::new("mission-build").expect("mission id is valid"),
            Some("exec-build-1".to_string()),
            None,
            "map",
            "enu",
            SpatialAnchorId::new("warehouse-origin").expect("anchor is valid"),
            Some(0.05),
            TimestampMs::new(10),
            None,
        )
        .expect("manifest is valid")
    }

    /// Builds one immutable event envelope for a catalog payload.
    fn event(payload: EventPayload, timestamp: u64) -> EventRecord {
        EventRecord::new(
            EventId::new(format!("event-{timestamp}")).expect("event id is valid"),
            TimestampMs::new(timestamp),
            domain::CorrelationId::new("catalog-test").expect("correlation is valid"),
            None,
            payload,
        )
    }

    /// Published revisions and replica transitions survive deterministic event replay.
    #[test]
    fn projection_tracks_publish_import_and_verify() {
        let manifest = manifest();
        let selector = manifest.selector().clone();
        let node = NodeId::new("dog-b").expect("node id is valid");
        let mission = MissionId::new("mission-import").expect("mission id is valid");
        let mut projection = MapCatalogProjection::new();
        projection
            .apply_event(&event(
                EventPayload::MapArtifactDeclared {
                    manifest: manifest.clone(),
                },
                1,
            ))
            .expect("declaration applies");
        assert_eq!(
            projection
                .revision(&selector)
                .expect("revision exists")
                .status(),
            MapRevisionStatus::Declared
        );
        projection
            .apply_event(&event(
                EventPayload::MapArtifactPublished {
                    manifest: manifest.clone(),
                },
                2,
            ))
            .expect("publication applies");
        projection
            .apply_event(&event(
                EventPayload::MapArtifactStaged {
                    manifest: manifest.clone(),
                    node_id: node.clone(),
                    mission_id: mission.clone(),
                },
                3,
            ))
            .expect("staging applies");
        projection
            .apply_event(&event(
                EventPayload::MapArtifactImported {
                    manifest: manifest.clone(),
                    node_id: node.clone(),
                    mission_id: mission.clone(),
                },
                4,
            ))
            .expect("import applies");
        projection
            .apply_event(&event(
                EventPayload::MapLocalizationVerified {
                    artifact: manifest.artifact().clone(),
                    node_id: node,
                    mission_id: mission,
                    anchor_id: manifest.anchor_id().clone(),
                },
                5,
            ))
            .expect("verification applies");
        assert_eq!(
            projection
                .revision(&selector)
                .expect("revision exists")
                .status(),
            MapRevisionStatus::Published
        );
        assert_eq!(
            projection.replicas(&selector)[0].status(),
            MapReplicaStatus::Verified
        );
    }

    /// Conflicting immutable manifests are rejected for one map/revision selector.
    #[test]
    fn projection_rejects_conflicting_manifest() {
        let first = manifest();
        let second = first
            .with_artifact(MapArtifactRef::new(
                first.selector().clone(),
                ContentDigest::new(
                    "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
                )
                .expect("digest is valid"),
                first.artifact().byte_size(),
            ))
            .expect("replacement artifact is valid");
        let mut projection = MapCatalogProjection::new();
        projection
            .apply_event(&event(
                EventPayload::MapArtifactPublished { manifest: first },
                1,
            ))
            .expect("first publication applies");
        assert!(matches!(
            projection.apply_event(&event(
                EventPayload::MapArtifactPublished { manifest: second },
                2,
            )),
            Err(MapCatalogError::RevisionConflict(_))
        ));
    }

    /// A verification event cannot move an imported replica to a different anchor.
    #[test]
    fn projection_rejects_anchor_mismatch() {
        let manifest = manifest();
        let mut projection = MapCatalogProjection::new();
        projection
            .apply_event(&event(
                EventPayload::MapArtifactPublished {
                    manifest: manifest.clone(),
                },
                1,
            ))
            .expect("publication applies");
        let node = NodeId::new("dog-b").expect("node id is valid");
        let mission = MissionId::new("mission-import").expect("mission id is valid");
        projection
            .apply_event(&event(
                EventPayload::MapArtifactStaged {
                    manifest: manifest.clone(),
                    node_id: node.clone(),
                    mission_id: mission.clone(),
                },
                2,
            ))
            .expect("staging applies");
        projection
            .apply_event(&event(
                EventPayload::MapArtifactImported {
                    manifest: manifest.clone(),
                    node_id: node.clone(),
                    mission_id: mission.clone(),
                },
                3,
            ))
            .expect("import applies");
        assert!(matches!(
            projection.apply_event(&event(
                EventPayload::MapLocalizationVerified {
                    artifact: manifest.artifact().clone(),
                    node_id: node,
                    mission_id: mission,
                    anchor_id: SpatialAnchorId::new("other-origin").expect("anchor is valid"),
                },
                4,
            )),
            Err(MapCatalogError::InvalidReplicaTransition(_))
        ));
    }

    /// Imported evidence requires an earlier staged fact for the same node and revision.
    #[test]
    fn projection_rejects_import_without_staging() {
        let manifest = manifest();
        let mut projection = MapCatalogProjection::new();
        projection
            .apply_event(&event(
                EventPayload::MapArtifactPublished {
                    manifest: manifest.clone(),
                },
                1,
            ))
            .expect("publication applies");
        assert!(matches!(
            projection.apply_event(&event(
                EventPayload::MapArtifactImported {
                    manifest,
                    node_id: NodeId::new("dog-b").expect("node id is valid"),
                    mission_id: MissionId::new("mission-import").expect("mission id is valid"),
                },
                2,
            )),
            Err(MapCatalogError::InvalidReplicaTransition(_))
        ));
        assert!(projection.replicas.is_empty());
    }

    /// A later Mission may restage a verified immutable replica without regressing State.
    #[test]
    fn projection_keeps_verified_replica_on_cross_mission_restage() {
        let manifest = manifest();
        let selector = manifest.selector().clone();
        let node = NodeId::new("dog-b").expect("node id is valid");
        let first_mission = MissionId::new("mission-import-1").expect("mission id is valid");
        let second_mission = MissionId::new("mission-import-2").expect("mission id is valid");
        let mut projection = MapCatalogProjection::new();
        for (timestamp, payload) in [
            (
                1,
                EventPayload::MapArtifactPublished {
                    manifest: manifest.clone(),
                },
            ),
            (
                2,
                EventPayload::MapArtifactStaged {
                    manifest: manifest.clone(),
                    node_id: node.clone(),
                    mission_id: first_mission.clone(),
                },
            ),
            (
                3,
                EventPayload::MapArtifactImported {
                    manifest: manifest.clone(),
                    node_id: node.clone(),
                    mission_id: first_mission.clone(),
                },
            ),
            (
                4,
                EventPayload::MapLocalizationVerified {
                    artifact: manifest.artifact().clone(),
                    node_id: node.clone(),
                    mission_id: first_mission,
                    anchor_id: manifest.anchor_id().clone(),
                },
            ),
            (
                5,
                EventPayload::MapArtifactStaged {
                    manifest,
                    node_id: node,
                    mission_id: second_mission,
                },
            ),
        ] {
            projection
                .apply_event(&event(payload, timestamp))
                .expect("cross-Mission evidence applies");
        }
        assert_eq!(
            projection.replicas(&selector)[0].status(),
            MapReplicaStatus::Verified
        );
    }

    /// Replaying the shared evidence log ignores unrelated Control and Runtime events.
    #[test]
    fn projection_ignores_unrelated_events() {
        let mut projection = MapCatalogProjection::new();
        projection
            .apply_event(&event(
                EventPayload::ExecutionGroupBlocked {
                    group_id: domain::ExecutionGroupId::new("group-a").expect("group id is valid"),
                    task_ref: domain::TaskRef::new(
                        MissionId::new("mission-a").expect("mission id is valid"),
                        domain::TaskId::new("task-a").expect("task id is valid"),
                    ),
                    reason: "waiting for recovery".to_string(),
                },
                1,
            ))
            .expect("unrelated event is ignored");
        assert_eq!(projection.revision_count(), 0);
    }
}
