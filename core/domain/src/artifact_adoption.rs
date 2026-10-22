//! Explicit current retention adoption, preserving raw historical publication and absent attribution.

use crate::*;
use serde::{Deserialize, Serialize};
use sha2::{Digest as _, Sha256};

/// Exact original persisted envelope; raw payload bytes are not reconstructed from a projection.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LegacyCatalogPublication {
    /// Original monotonically allocated durable event sequence.
    pub sequence: u64,
    /// Original immutable event identity.
    pub event_id: String,
    /// Original Controller receive timestamp, never migration time.
    pub timestamp_ms: u64,
    /// Original causality scope.
    pub correlation_id: String,
    /// Original optional predecessor, never synthesized during adoption.
    pub causation_id: Option<String>,
    /// Original codec generation of this publication.
    pub payload_schema: String,
    /// Exact original bounded serialized payload.
    pub payload_json: String,
}

/// Actual current authenticated administrator evidence, distinct from the unknown old publisher.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ArtifactAdoptionAuthority {
    /// Actual current stable subject.
    pub principal_id: String,
    /// Current retention tenant; this does not rewrite the legacy publication's attribution.
    pub tenant_id: String,
    /// Actual current TLS leaf, or the explicitly supported raw loopback identity.
    pub certificate_digest: Option<String>,
    /// Complete current grant/registry interpretation in canonical JSON, without private keys.
    pub canonical_grant_json: String,
}

/// One original explicit adoption committed atomically with its exact new retention reference.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ArtifactCatalogAdoption {
    /// Explicit current-adoption meaning, never historical publication or use authority.
    pub schema: String,
    /// Original administrator operation identity; another selector cannot reuse it.
    pub command_id: String,
    /// Complete original raw evidence whose metadata cannot be replaced by the caller.
    pub publication: LegacyCatalogPublication,
    /// Current actual adopting administrator, independent of the missing historical one.
    pub authority: ArtifactAdoptionAuthority,
    /// Historical publisher identity is explicitly unavailable in this old publication contract.
    pub historical_publisher: Option<String>,
    /// Historical tenant identity is explicitly unavailable rather than inferred from current grants.
    pub historical_tenant: Option<String>,
    /// Actual new same-owner direct/typed-parent retention, granting no read or execution right.
    pub record: ArtifactCatalogReference,
}

/// Canonical complete evidence digest, preserving strings containing original raw payload bytes.
pub fn artifact_adoption_digest(value: &impl Serialize) -> Result<String, String> {
    let value = serde_json::to_value(value).map_err(|error| error.to_string())?;
    let raw = serde_json::to_vec(&value).map_err(|error| error.to_string())?;
    Ok(format!("sha256:{:x}", Sha256::digest(raw)))
}

impl LegacyCatalogPublication {
    /// Accepts only an exact original publication envelope and returns its complete current DTO view.
    pub fn manifest(
        &self,
    ) -> Result<(MemoryArtifactManifest, Option<MapArtifactManifest>), String> {
        EventId::new(&self.event_id).map_err(|error| error.to_string())?;
        CorrelationId::new(&self.correlation_id).map_err(|error| error.to_string())?;
        if let Some(cause) = &self.causation_id {
            EventId::new(cause).map_err(|error| error.to_string())?;
        }
        let version = self
            .payload_schema
            .strip_prefix("domain.EventPayload.json/v")
            .and_then(|value| value.parse::<u32>().ok());
        if self.sequence == 0
            || self.payload_json.len() > 128 * 1024
            || !version.is_some_and(|version| (1..=30).contains(&version))
        {
            return Err("legacy Catalog original envelope is invalid or exceeds bounds".into());
        }
        let payload: EventPayload =
            serde_json::from_str(&self.payload_json).map_err(|error| error.to_string())?;
        match payload {
            EventPayload::MemoryManifestPublished { manifest } => Ok((manifest, None)),
            EventPayload::MapArtifactPublished { manifest } => {
                Ok((map_memory_manifest(&manifest)?, Some(manifest)))
            }
            _ => Err("legacy adoption requires the exact original publication event".into()),
        }
    }
}

impl ArtifactCatalogAdoption {
    /// Binds actual current authority and complete original publication without inventing old ownership.
    pub fn validate(&self) -> Result<(), String> {
        let (manifest, typed) = self.publication.manifest()?;
        self.record.validate()?;
        let bounded = |value: &str, maximum: usize| {
            !value.trim().is_empty()
                && value.len() <= maximum
                && !value.chars().any(char::is_control)
                && !value.contains('*')
        };
        if self.schema != "roboguide.artifact-catalog-adoption/v0.1"
            || !bounded(&self.command_id, 128)
            || !bounded(&self.authority.principal_id, 256)
            || !bounded(&self.authority.tenant_id, 128)
            || self.historical_publisher.is_some()
            || self.historical_tenant.is_some()
            || self.record.retirement.is_some()
            || self.record.selector != *manifest.selector()
            || self.record.principal_id != self.authority.principal_id
            || self.record.tenant_id != self.authority.tenant_id
            || self.record.admitted_at_ms < self.publication.timestamp_ms
            || self.authority.canonical_grant_json.len() > 64 * 1024
            || self.record.authority_digest
                != artifact_adoption_digest(&(
                    &self.command_id,
                    &self.publication,
                    &self.authority,
                ))?
        {
            return Err(
                "legacy Catalog adoption changes original input or invents historical authority"
                    .into(),
            );
        }
        let grant: serde_json::Value = serde_json::from_str(&self.authority.canonical_grant_json)
            .map_err(|error| error.to_string())?;
        if !grant.is_object()
            || serde_json::to_string(&grant).map_err(|error| error.to_string())?
                != self.authority.canonical_grant_json
        {
            return Err(
                "legacy Catalog adoption current grant evidence is not complete canonical JSON"
                    .into(),
            );
        }
        if let Some(leaf) = &self.authority.certificate_digest {
            let hex = leaf.strip_prefix("sha256:").unwrap_or_default();
            if hex.len() != 64
                || !hex
                    .bytes()
                    .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
            {
                return Err("legacy adoption lacks its actual current TLS leaf identity".into());
            }
        }
        let direct = self
            .record
            .dependencies
            .iter()
            .filter(|entry| entry.kind == ArtifactDependencyKind::Catalog)
            .collect::<Vec<_>>();
        match manifest.artifact() {
            Some(artifact) if direct.len() == 1 => {
                let entry = direct[0];
                if entry.provider_id != manifest.provider_id()
                    || entry.manifest_digest != memory_manifest_digest(&manifest)?
                    || entry.content_digest != artifact.content_digest().as_str()
                    || entry.byte_size != artifact.byte_size()
                    || entry
                        .typed_map
                        .as_ref()
                        .map(|original| original.manifest_digest.clone())
                        != typed.as_ref().map(map_manifest_digest).transpose()?
                {
                    return Err("legacy adoption lost original complete metadata/content".into());
                }
            }
            None if direct.is_empty() => {}
            _ => return Err("legacy adoption lacks its exact original direct dependency".into()),
        }
        let parents = self
            .record
            .dependencies
            .iter()
            .filter(|entry| entry.kind == ArtifactDependencyKind::Lineage)
            .collect::<Vec<_>>();
        match typed.as_ref().and_then(|map| {
            map.parent_revision_id()
                .map(|revision| (map.selector().map_id(), revision))
        }) {
            Some((map, revision))
                if parents.len() == 1
                    && parents[0].selector.memory_id().as_str() == map.as_str()
                    && parents[0].selector.revision_id().as_str() == revision.as_str() => {}
            None if parents.is_empty() => {}
            _ => return Err("legacy adoption changes the original typed-parent dependency".into()),
        }
        Ok(())
    }
}
