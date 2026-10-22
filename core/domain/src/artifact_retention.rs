//! Original Catalog/lineage retention authority, independent of semantic read permission.

use crate::MemorySelector;
use serde::{Deserialize, Serialize};

/// Closed role of one actual CAS dependency in an original publication.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ArtifactDependencyKind {
    /// Bytes directly named by the published immutable manifest.
    Catalog,
    /// Original parent bytes independently retained by this child publication.
    Lineage,
}

/// Exact canonical original Spatial metadata, independent of its reduced discovery representation.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ArtifactTypedMapProvenance {
    /// Original Rust canonical JSON bytes encoded as UTF-8 text, including original float spelling.
    pub canonical_json: String,
    /// SHA-256 over exactly these bytes, not a cross-language floating-point reserialization.
    pub manifest_digest: String,
}

/// One original content owner; equal hashes in other stores never discharge its responsibility.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ArtifactContentDependency {
    /// Direct or immutable-parent purpose, never an access grant.
    pub kind: ArtifactDependencyKind,
    /// Exact original software CAS identity supplied by its actual writer-owned storage.
    pub store_owner_id: String,
    /// Original semantic provider, distinct from the actual Controller CAS owner.
    pub provider_id: String,
    /// Exact dependency's logical revision, including a different parent when applicable.
    pub selector: MemorySelector,
    /// Canonical digest of the complete generic metadata, not only its blob alias.
    pub manifest_digest: String,
    /// Complete original Spatial metadata when this dependency names an actual typed map.
    pub typed_map: Option<ArtifactTypedMapProvenance>,
    /// Hash of the actual complete opaque bytes.
    pub content_digest: String,
    /// Exact original byte count.
    pub byte_size: u64,
}

/// Exact explicit retirement; deadlines, process exits and terminal Tasks cannot manufacture it.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ArtifactCatalogRetirement {
    /// Original authenticated idempotent retirement command.
    pub command_id: String,
    /// Original stable administrative principal, independently rechecked at Commit.
    pub principal_id: String,
    /// Complete frozen application-use/revoke evidence at the retirement decision.
    pub proof_digest: String,
    /// First actual Controller retirement time, unchanged by observation or retry.
    pub retired_at_ms: u64,
}

/// One immutable publication's complete original CAS dependencies and irreversible retirement.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ArtifactCatalogReference {
    /// Closed interpretation, distinct from manifests and Local EAIOS use contracts.
    pub schema: String,
    /// Logical publication whose direct and parent dependencies share one lifetime.
    pub selector: MemorySelector,
    /// Original publication tenant, never reconstructed from a blob digest.
    pub tenant_id: String,
    /// Original stable authenticated publisher or application delegation owner.
    pub principal_id: String,
    /// Complete manifest and original authority purpose, not a current replacement identity.
    pub authority_digest: String,
    /// Original immutable publication generation; retired identities are never reused.
    pub generation: u64,
    /// First Controller publication receive time, never renewed on replay.
    pub admitted_at_ms: u64,
    /// Bounded original direct and parent ownership requirements.
    pub dependencies: Vec<ArtifactContentDependency>,
    /// Present only after exact application-use proof and explicit retirement Commit.
    pub retirement: Option<ArtifactCatalogRetirement>,
}

/// Checks an exact bounded software identity without interpreting wildcards as permissions.
fn identity(value: &str, maximum: usize) -> bool {
    !value.trim().is_empty()
        && value.len() <= maximum
        && !value.chars().any(char::is_control)
        && !value.contains('*')
}

/// Accepts only canonical lowercase complete SHA-256 identities.
fn digest(value: &str) -> bool {
    value.strip_prefix("sha256:").is_some_and(|hex| {
        hex.len() == 64
            && hex
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    })
}

impl ArtifactCatalogReference {
    /// Rejects incomplete owners, ambiguous dependency purposes and invented release evidence.
    pub fn validate(&self) -> Result<(), String> {
        crate::MemoryId::new(self.selector.memory_id().as_str())
            .map_err(|error| error.to_string())?;
        crate::MemoryRevisionId::new(self.selector.revision_id().as_str())
            .map_err(|error| error.to_string())?;
        if self.schema != "roboguide.artifact-catalog-reference/v0.1"
            || !identity(&self.tenant_id, 128)
            || !identity(&self.principal_id, 256)
            || !digest(&self.authority_digest)
            || self.generation != 1
            || self.dependencies.len() > 65
        {
            return Err("Artifact Catalog reference original identity is invalid".into());
        }
        let mut seen = std::collections::BTreeSet::new();
        let mut direct = 0;
        for dependency in &self.dependencies {
            crate::MemoryId::new(dependency.selector.memory_id().as_str())
                .map_err(|error| error.to_string())?;
            crate::MemoryRevisionId::new(dependency.selector.revision_id().as_str())
                .map_err(|error| error.to_string())?;
            let owner = dependency
                .store_owner_id
                .strip_prefix("artifact-store:")
                .unwrap_or_default();
            if owner.len() != 64
                || !owner
                    .bytes()
                    .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
                || !identity(&dependency.provider_id, 256)
                || !digest(&dependency.manifest_digest)
                || !digest(&dependency.content_digest)
                || dependency.byte_size > 4 * 1024 * 1024 * 1024
                || !seen.insert((dependency.kind, dependency.selector.clone()))
                || (dependency.kind == ArtifactDependencyKind::Lineage
                    && dependency.selector == self.selector)
            {
                return Err(
                    "Artifact Catalog dependency original content identity is invalid".into(),
                );
            }
            if dependency.kind == ArtifactDependencyKind::Catalog {
                direct += 1;
                if dependency.selector != self.selector {
                    return Err(
                        "Artifact Catalog direct dependency has a different selector".into(),
                    );
                }
            }
            if let Some(typed) = &dependency.typed_map {
                if typed.canonical_json.len() > 65_536 {
                    return Err("Artifact original typed-map metadata exceeds its bound".into());
                }
                let map: crate::MapArtifactManifest = serde_json::from_str(&typed.canonical_json)
                    .map_err(|error| error.to_string())?;
                let canonical = serde_json::to_string(
                    &serde_json::to_value(&map).map_err(|error| error.to_string())?,
                )
                .map_err(|error| error.to_string())?;
                if canonical != typed.canonical_json
                    || crate::map_manifest_digest(&map)? != typed.manifest_digest
                    || crate::memory_manifest_digest(&crate::map_memory_manifest(&map)?)?
                        != dependency.manifest_digest
                    || map.selector().map_id().as_str() != dependency.selector.memory_id().as_str()
                    || map.selector().revision_id().as_str()
                        != dependency.selector.revision_id().as_str()
                    || map.artifact().content_digest().as_str() != dependency.content_digest
                    || map.artifact().byte_size() != dependency.byte_size
                    || map.producer_provider_id().unwrap_or("typed-map-catalog")
                        != dependency.provider_id
                {
                    return Err(
                        "Artifact reference lost complete original Spatial provenance".into(),
                    );
                }
            }
        }
        if direct > 1 {
            return Err("Artifact Catalog reference repeats its direct dependency".into());
        }
        if let Some(retirement) = &self.retirement
            && (!identity(&retirement.command_id, 128)
                || !identity(&retirement.principal_id, 256)
                || !digest(&retirement.proof_digest)
                || retirement.retired_at_ms < self.admitted_at_ms)
        {
            return Err("Artifact Catalog retirement lacks original proof/time".into());
        }
        Ok(())
    }

    /// Allows only exact replay or one irreversible retirement of the unchanged original record.
    pub fn validates_successor(&self, next: &Self) -> Result<(), String> {
        self.validate()?;
        next.validate()?;
        if self == next {
            return Ok(());
        }
        let mut original = next.clone();
        original.retirement = None;
        if self.retirement.is_some() || next.retirement.is_none() || &original != self {
            return Err(
                "Artifact Catalog reference changed its original authority or revived".into(),
            );
        }
        Ok(())
    }
}
