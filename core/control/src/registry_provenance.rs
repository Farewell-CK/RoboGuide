//! Durable anti-rollback evidence without a restorable entity-to-Node routing table.

use crate::ControlError;
use domain::{
    ActorBinding, PhysicalEntityRegistryId, PhysicalEntityRegistrySnapshot,
    PhysicalEntityRoutingProfile,
};
use sha2::{Digest, Sha256};

/// Highest successfully admitted registry revision and exact immutable content identity.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub(crate) struct RegistryProvenance {
    /// Deployment authority whose revision sequence must remain continuous.
    registry_id: PhysicalEntityRegistryId,
    /// Highest admitted revision, independent of older Actor bind-time revisions.
    highest_revision: u64,
    /// SHA-256 over canonical profile and entity/Node associations; never routing data.
    content_digest: [u8; 32],
}

impl RegistryProvenance {
    /// Hashes typed fields in entity order with explicit framing, independent of JSON formatting.
    pub(crate) fn from_snapshot(snapshot: &PhysicalEntityRegistrySnapshot) -> Self {
        let mut digest = Sha256::new();
        digest.update(b"roboguide.registry-provenance/v1\0");
        match snapshot.routing_profile() {
            PhysicalEntityRoutingProfile::OneRoutableEntityPerNode => digest.update([1]),
        }
        for entry in snapshot.entities() {
            for value in [entry.entity_id().as_str(), entry.node_id().as_str()] {
                digest.update((value.len() as u64).to_be_bytes());
                digest.update(value.as_bytes());
            }
        }
        Self {
            registry_id: snapshot.registry_id().clone(),
            highest_revision: snapshot.revision(),
            content_digest: digest.finalize().into(),
        }
    }

    /// Rejects identity changes, lower revisions, and equivocation at the highest revision.
    pub(crate) fn validate_successor(&self, candidate: &Self) -> Result<(), ControlError> {
        let reason = if self.registry_id != candidate.registry_id {
            Some("physical entity registry identity changed")
        } else if candidate.highest_revision < self.highest_revision {
            Some("physical entity registry revision moved backwards")
        } else if candidate.highest_revision == self.highest_revision
            && candidate.content_digest != self.content_digest
        {
            Some("physical entity registry changed without a new revision")
        } else {
            None
        };
        reason.map_or(Ok(()), |reason| {
            Err(ControlError::InvalidProposal(reason.to_string()))
        })
    }

    /// Cross-checks historical binding provenance without treating it as current topology.
    pub(crate) fn validate_binding(&self, binding: &ActorBinding) -> Result<(), ControlError> {
        if binding.physical_entity_id().is_some()
            && (binding.registry_id() != Some(&self.registry_id)
                || binding
                    .registry_revision()
                    .is_none_or(|revision| revision > self.highest_revision))
        {
            return Err(ControlError::InvalidProposal(
                "checkpoint Actor binding exceeds registry anti-rollback provenance".to_string(),
            ));
        }
        Ok(())
    }
}
