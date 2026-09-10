//! Node-local replica evidence for immutable spatial map revisions.

use crate::{MapReplicaStatus, MapRevisionSelector, MissionId, NodeId, TimestampMs};

/// Rebuildable metadata for one node replica.
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct MapReplicaSnapshot {
    /// Logical map/revision represented by this replica.
    selector: MapRevisionSelector,
    /// Node holding the replica.
    node_id: NodeId,
    /// Current replica lifecycle.
    status: MapReplicaStatus,
    /// Mission that requested or reported this replica operation.
    mission_id: MissionId,
    /// Last RoboGuide-local observation time.
    observed_at: TimestampMs,
    /// Optional rejection diagnostic retained as evidence.
    rejection_reason: Option<String>,
    /// Strong localization evidence, absent for legacy smoke-only verification.
    #[serde(default)]
    localization_evidence: Option<crate::LocalizationVerificationEvidence>,
}

impl MapReplicaSnapshot {
    /// Creates a node replica metadata snapshot.
    pub const fn new(
        selector: MapRevisionSelector,
        node_id: NodeId,
        status: MapReplicaStatus,
        mission_id: MissionId,
        observed_at: TimestampMs,
        rejection_reason: Option<String>,
        localization_evidence: Option<crate::LocalizationVerificationEvidence>,
    ) -> Self {
        Self {
            selector,
            node_id,
            status,
            mission_id,
            observed_at,
            rejection_reason,
            localization_evidence,
        }
    }

    /// Returns the map/revision selector.
    pub const fn selector(&self) -> &MapRevisionSelector {
        &self.selector
    }

    /// Returns the node holding this replica.
    pub const fn node_id(&self) -> &NodeId {
        &self.node_id
    }

    /// Returns the replica lifecycle.
    pub const fn status(&self) -> MapReplicaStatus {
        self.status
    }

    /// Returns the Mission associated with this replica observation.
    pub const fn mission_id(&self) -> &MissionId {
        &self.mission_id
    }

    /// Returns when this replica status was observed locally.
    pub const fn observed_at(&self) -> TimestampMs {
        self.observed_at
    }

    /// Returns a rejection diagnostic, when the replica was rejected.
    pub fn rejection_reason(&self) -> Option<&str> {
        self.rejection_reason.as_deref()
    }

    /// Returns strong localization evidence when the replica passed the v0.1 contract.
    pub const fn localization_evidence(&self) -> Option<&crate::LocalizationVerificationEvidence> {
        self.localization_evidence.as_ref()
    }

    /// Returns whether this replica has strong evidence rather than a legacy smoke fact.
    pub const fn is_strongly_verified(&self) -> bool {
        self.localization_evidence.is_some()
    }
}
