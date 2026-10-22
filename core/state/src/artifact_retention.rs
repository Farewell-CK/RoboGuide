//! Replay of original Catalog/lineage ownership; it never infers a read grant or deleted bytes.

use domain::{ArtifactCatalogReference, EventPayload, EventRecord, MemorySelector};
use std::collections::BTreeMap;

/// Independent original publications, including released history and equal hashes in distinct owners.
#[derive(Debug, Clone, Default)]
pub struct ArtifactRetentionProjection {
    /// Immutable original publication records keyed by exact selector.
    records: BTreeMap<MemorySelector, ArtifactCatalogReference>,
}

impl ArtifactRetentionProjection {
    /// Rebuilds original ownership without renewing time or reviving retired publications.
    pub fn from_events(events: impl IntoIterator<Item = EventRecord>) -> Result<Self, String> {
        let mut result = Self::default();
        for event in events {
            if let EventPayload::ArtifactCatalogReferenceTransition { record } = event.payload() {
                result.apply(record)?;
            }
        }
        Ok(result)
    }

    /// Applies one exact event or rejects a source mutation, orphan retirement or resurrection.
    pub fn apply(&mut self, record: &ArtifactCatalogReference) -> Result<(), String> {
        record.validate()?;
        if let Some(retirement) = &record.retirement
            && self.records.values().any(|original| {
                original.selector != record.selector
                    && original
                        .retirement
                        .as_ref()
                        .is_some_and(|proof| proof.command_id == retirement.command_id)
            })
        {
            return Err(
                "Artifact Catalog retirement command already names another publication".into(),
            );
        }
        if let Some(original) = self.records.get(&record.selector) {
            original.validates_successor(record)?;
        } else if record.retirement.is_some() {
            return Err("Artifact Catalog retirement has no original acquisition".into());
        }
        self.records.insert(record.selector.clone(), record.clone());
        Ok(())
    }

    /// Returns original immutable authority, including exact retirement history when present.
    pub fn record(&self, selector: &MemorySelector) -> Option<&ArtifactCatalogReference> {
        self.records.get(selector)
    }

    /// Reports original publication retirement without dropping historical semantic policy.
    pub fn is_retired(&self, selector: &MemorySelector) -> bool {
        self.record(selector)
            .is_some_and(|record| record.retirement.is_some())
    }

    /// Enumerates every original record in deterministic order for bounded composition paging.
    pub fn records(&self) -> impl Iterator<Item = &ArtifactCatalogReference> {
        self.records.values()
    }

    /// Checks one actual owner independently of another store carrying the same digest.
    pub fn retains(&self, owner: &str, digest: &str) -> bool {
        self.records.values().any(|record| {
            record.retirement.is_none()
                && record.dependencies.iter().any(|dependency| {
                    dependency.store_owner_id == owner && dependency.content_digest == digest
                })
        })
    }
}
