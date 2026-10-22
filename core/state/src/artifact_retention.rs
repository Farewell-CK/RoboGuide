//! Replay of original Catalog/lineage ownership; it never infers a read grant or deleted bytes.

use domain::{
    ArtifactCatalogAdoption, ArtifactCatalogReference, EventPayload, EventRecord, MemorySelector,
};
use std::collections::BTreeMap;

/// Checks exact stored strings and complete original envelopes before a current Catalog can replay adoption.
pub(crate) fn validate_adoption_sources(records: &[crate::PersistedEvent]) -> Result<(), String> {
    let mut originals = BTreeMap::new();
    for row in records {
        let payload: EventPayload =
            serde_json::from_str(&row.payload_json).map_err(|error| error.to_string())?;
        match payload {
            EventPayload::MemoryManifestPublished { manifest } => {
                originals
                    .entry(manifest.selector().clone())
                    .or_insert_with(|| row.clone());
            }
            EventPayload::MapArtifactPublished { manifest } => {
                let selector = MemorySelector::new(
                    domain::MemoryId::new(manifest.selector().map_id().as_str())
                        .map_err(|error| error.to_string())?,
                    domain::MemoryRevisionId::new(manifest.selector().revision_id().as_str())
                        .map_err(|error| error.to_string())?,
                );
                originals.entry(selector).or_insert_with(|| row.clone());
            }
            EventPayload::ArtifactCatalogLegacyAdopted { receipt } => {
                let original = originals
                    .get(&receipt.record.selector)
                    .ok_or("legacy adoption lost its original stored publication")?;
                let actual = domain::LegacyCatalogPublication {
                    sequence: original.sequence,
                    event_id: original.event_id.clone(),
                    timestamp_ms: original.timestamp_ms,
                    correlation_id: original.correlation_id.clone(),
                    causation_id: original.causation_id.clone(),
                    payload_schema: original.payload_schema.clone(),
                    payload_json: original.payload_json.clone(),
                };
                if receipt.publication != actual {
                    return Err(
                        "legacy adoption changes exact original stored publication bytes/envelope"
                            .into(),
                    );
                }
            }
            _ => {}
        }
    }
    Ok(())
}

/// Independent original publications, including released history and equal hashes in distinct owners.
#[derive(Debug, Clone, Default)]
pub struct ArtifactRetentionProjection {
    /// Immutable original publication records keyed by exact selector.
    records: BTreeMap<MemorySelector, ArtifactCatalogReference>,
    /// Complete explicit adoption receipts, separate from legacy unknown publisher/tenant fields.
    adoptions: BTreeMap<MemorySelector, ArtifactCatalogAdoption>,
    /// Actual original publication evidence, preserved independently of later adoption receipts.
    publications: BTreeMap<MemorySelector, EventRecord>,
    /// An acquisition following an old publication requires its complete independent adoption receipt.
    pending_adoptions: std::collections::BTreeSet<MemorySelector>,
}

impl ArtifactRetentionProjection {
    /// Rebuilds original ownership without renewing time or reviving retired publications.
    pub fn from_events(events: impl IntoIterator<Item = EventRecord>) -> Result<Self, String> {
        let mut result = Self::default();
        for event in events {
            match event.payload() {
                EventPayload::ArtifactCatalogReferenceTransition { record } => {
                    result.apply(record)?
                }
                EventPayload::ArtifactCatalogLegacyAdopted { receipt } => {
                    result.apply_adoption(receipt)?
                }
                _ => result.observe_publication(&event)?,
            }
        }
        result.validate_complete()?;
        Ok(result)
    }

    /// Rejects acquired legacy pins without their mandatory same-transaction original adoption receipt.
    pub fn validate_complete(&self) -> Result<(), String> {
        if !self.pending_adoptions.is_empty() {
            return Err(
                "legacy Catalog reference lacks its mandatory original adoption receipt".into(),
            );
        }
        Ok(())
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
        if !self.records.contains_key(&record.selector)
            && self.publications.contains_key(&record.selector)
        {
            self.pending_adoptions.insert(record.selector.clone());
        }
        self.records.insert(record.selector.clone(), record.clone());
        Ok(())
    }

    /// Returns original immutable authority, including exact retirement history when present.
    pub fn record(&self, selector: &MemorySelector) -> Option<&ArtifactCatalogReference> {
        self.records.get(selector)
    }

    /// Retains exact current adoption evidence without modifying the old publication or granting use.
    pub fn apply_adoption(&mut self, receipt: &ArtifactCatalogAdoption) -> Result<(), String> {
        receipt.validate()?;
        let selector = &receipt.record.selector;
        let original = self
            .publications
            .get(selector)
            .ok_or("legacy Catalog adoption lost its original publication")?;
        let payload: EventPayload = serde_json::from_str(&receipt.publication.payload_json)
            .map_err(|error| error.to_string())?;
        if original.event_id().as_str() != receipt.publication.event_id
            || original.timestamp().as_millis() != receipt.publication.timestamp_ms
            || original.correlation_id().as_str() != receipt.publication.correlation_id
            || original.causation_id().map(|id| id.as_str())
                != receipt.publication.causation_id.as_deref()
            || original.payload() != &payload
        {
            return Err("legacy adoption changed its actual original publication evidence".into());
        }
        if self
            .adoptions
            .values()
            .any(|previous| previous.command_id == receipt.command_id && previous != receipt)
        {
            return Err(
                "legacy Catalog adoption command already has different original input".into(),
            );
        }
        if self.records.get(selector) != Some(&receipt.record) {
            return Err(
                "legacy Catalog adoption lacks its atomically committed original reference".into(),
            );
        }
        if let Some(previous) = self.adoptions.get(selector) {
            if previous != receipt {
                return Err("legacy Catalog adoption changes its original receipt".into());
            }
        } else {
            self.adoptions.insert(selector.clone(), receipt.clone());
        }
        self.pending_adoptions.remove(selector);
        Ok(())
    }

    /// Remembers actual immutable publication history before another operation may adopt it.
    pub fn observe_publication(&mut self, event: &EventRecord) -> Result<(), String> {
        let selector = match event.payload() {
            EventPayload::MemoryManifestPublished { manifest } => manifest.selector().clone(),
            EventPayload::MapArtifactPublished { manifest } => domain::MemorySelector::new(
                domain::MemoryId::new(manifest.selector().map_id().as_str())
                    .map_err(|error| error.to_string())?,
                domain::MemoryRevisionId::new(manifest.selector().revision_id().as_str())
                    .map_err(|error| error.to_string())?,
            ),
            _ => return Ok(()),
        };
        self.publications
            .entry(selector)
            .or_insert_with(|| event.clone());
        Ok(())
    }

    /// Reads only the exact original receipt; retired records do not regain their first current authority.
    pub fn adoption(&self, selector: &MemorySelector) -> Option<&ArtifactCatalogAdoption> {
        self.adoptions.get(selector)
    }

    /// Checks original command reuse across the complete retained adoption history.
    pub fn adoption_command(&self, command: &str) -> Option<&ArtifactCatalogAdoption> {
        self.adoptions
            .values()
            .find(|receipt| receipt.command_id == command)
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
