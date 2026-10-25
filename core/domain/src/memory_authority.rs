//! Exact Control-owned membership identity used by scoped Memory authorization.

use crate::{
    ExecutionGroupId, LocalSystemId, MapArtifactManifest, MemoryArtifactManifest, MemoryKind,
    MemoryProviderDescriptor, MemorySelector,
};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};

/// Current explicit content policy registry schema, independent of immutable manifest versions.
pub const MEMORY_AUTHORIZATION_SCHEMA: &str = "roboguide.memory-authorization/v0.2";
/// Historical registry without a complete typed-map source binding.
pub const MEMORY_AUTHORIZATION_SCHEMA_V0_1: &str = "roboguide.memory-authorization/v0.1";

/// Closed Memory operations; discovery never silently grants content consumption or replication.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MemoryAccessOperation {
    /// Discover permitted catalog metadata, without reading content.
    Discover,
    /// Read immutable content under its original semantic scope.
    Read,
    /// Import into an exact current consumer provider.
    Import,
    /// Export content from an explicitly authorized current owner.
    Export,
    /// Create a distinct immutable child under an explicit exchange/handoff purpose.
    Handoff,
    /// Publish a versioned active-reference proposal; this is not localization proof.
    Activate,
}

/// Provider contract filter inside one explicitly granted current Group consumer policy.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MemoryConsumerPolicy {
    /// Exact provider identity when constrained; None permits compatible registered providers.
    pub provider_id: Option<String>,
    /// Exact Local EAIOS owner when constrained, independently of the Node identity.
    pub local_system_id: Option<LocalSystemId>,
    /// Actual required provider kind.
    pub kind: MemoryKind,
    /// Exact registered payload schema, never inferred from file suffix or a device name.
    pub payload_schema: String,
    /// Exact registered media contract.
    pub media_type: String,
}

impl MemoryConsumerPolicy {
    /// Checks a current provider declaration; membership and tenant remain separate Control gates.
    pub fn matches(&self, provider: &MemoryProviderDescriptor) -> bool {
        self.provider_id
            .as_deref()
            .is_none_or(|id| id == provider.provider_id())
            && self
                .local_system_id
                .as_ref()
                .is_none_or(|id| id == provider.local_system_id())
            && self.kind == provider.kind()
            && self.payload_schema == provider.payload_schema()
            && self.media_type == provider.media_type()
    }

    /// Refuses empty or wildcard contracts before granting a dynamic participant policy.
    pub fn validate(&self) -> Result<(), String> {
        for text in [
            Some(self.payload_schema.as_str()),
            Some(self.media_type.as_str()),
            self.provider_id.as_deref(),
            self.local_system_id.as_ref().map(LocalSystemId::as_str),
        ]
        .into_iter()
        .flatten()
        {
            validate_text(text, 256)?;
        }
        Ok(())
    }
}

/// Exact logical Group allowed to consume a resource through currently bound compatible providers.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MemoryGroupPolicy {
    /// Control-owned Group; this field neither creates a member nor chooses a Node.
    pub group_id: ExecutionGroupId,
    /// Explicit subset of content operations for this Group.
    pub operations: BTreeSet<MemoryAccessOperation>,
    /// At most sixteen independently specified receiving provider contracts.
    pub providers: Vec<MemoryConsumerPolicy>,
}

/// Resource policy bound to one immutable manifest and its original authenticated tenant.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MemoryAccessPolicy {
    /// Stable policy identity retained across revocation and configuration updates.
    pub policy_id: String,
    /// Exact original semantic/content selector.
    pub selector: MemorySelector,
    /// Complete immutable manifest digest; it does not substitute for content SHA-256.
    pub manifest_digest: String,
    /// Complete typed-map metadata when this resource originated in the Spatial catalog.
    /// An absent historical value cannot authorize a typed-map read or generic import.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub typed_map_manifest_digest: Option<String>,
    /// Original trusted namespace; a later Node reenrollment cannot reassign old content.
    pub tenant_id: String,
    /// Explicit certificate-derived human/service principals and their operation subsets.
    pub principals: BTreeMap<String, BTreeSet<MemoryAccessOperation>>,
    /// Current participant/provider policies; arbitrary inventory is never membership.
    pub groups: Vec<MemoryGroupPolicy>,
    /// Irreversible content authorization revocation for this selector.
    pub revoked: bool,
}

impl MemoryAccessPolicy {
    /// Returns a path-safe, unambiguous selector key independent of an authorization principal.
    pub fn key(&self) -> String {
        format!(
            "{}/{}",
            self.selector.memory_id(),
            self.selector.revision_id()
        )
    }

    /// Requires bounded exact source contracts and duplicate-free Group policy.
    pub fn validate(&self) -> Result<(), String> {
        validate_text(&self.policy_id, 128)?;
        validate_text(&self.tenant_id, 128)?;
        validate_digest(&self.manifest_digest)?;
        if let Some(digest) = &self.typed_map_manifest_digest {
            validate_digest(digest)?;
        }
        if self.principals.len() > 128
            || self.groups.len() > 128
            || (!self.revoked && self.principals.is_empty() && self.groups.is_empty())
        {
            return Err(
                "Memory resource policy exceeds its bound or grants no explicit consumer".into(),
            );
        }
        for (principal, operations) in &self.principals {
            validate_text(principal, 256)?;
            if operations.is_empty() {
                return Err("Memory principal operations must be explicit".into());
            }
        }
        let mut groups = BTreeSet::new();
        for group in &self.groups {
            validate_text(group.group_id.as_str(), 256)?;
            if !groups.insert(&group.group_id)
                || group.operations.is_empty()
                || group.providers.is_empty()
                || group.providers.len() > 16
            {
                return Err("Memory Group consumers must be unique and have bounded explicit provider/operation contracts".into());
            }
            for provider in &group.providers {
                provider.validate()?;
            }
            if group
                .providers
                .iter()
                .enumerate()
                .any(|(i, p)| group.providers[..i].contains(p))
            {
                return Err("duplicate Memory consumer provider contract".into());
            }
        }
        Ok(())
    }
}

/// Control-owned versioned resource policies; removal, tenant reassignment and rollback fail closed.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MemoryAuthorizationRegistry {
    /// Required closed schema identity.
    pub schema_version: String,
    /// Original immutable authority identity.
    pub registry_id: String,
    /// Strictly monotonic full-registry policy revision.
    pub revision: u64,
    /// Exact selector key to its complete immutable-source policy.
    pub policies: BTreeMap<String, MemoryAccessPolicy>,
}

impl MemoryAuthorizationRegistry {
    /// Validates bounded policy inputs without interpreting any payload as authorization.
    pub fn validate(&self) -> Result<(), String> {
        validate_text(&self.registry_id, 128)?;
        if !matches!(
            self.schema_version.as_str(),
            MEMORY_AUTHORIZATION_SCHEMA | MEMORY_AUTHORIZATION_SCHEMA_V0_1
        ) || self.revision == 0
            || self.policies.len() > 4096
        {
            return Err("Memory authorization schema, revision or policy count is invalid".into());
        }
        let mut ids = BTreeSet::new();
        for (key, policy) in &self.policies {
            policy.validate()?;
            if self.schema_version == MEMORY_AUTHORIZATION_SCHEMA_V0_1
                && policy.typed_map_manifest_digest.is_some()
            {
                return Err("legacy Memory registry cannot contain typed-map authority".into());
            }
            if key != &policy.key() || !ids.insert(&policy.policy_id) {
                return Err("Memory authorization policy identity/key is ambiguous".into());
            }
        }
        Ok(())
    }

    /// Preserves historical ownership and revocation while allowing explicit current grants to tighten.
    pub fn validates_successor(&self, next: &Self) -> Result<(), String> {
        next.validate()?;
        if self.registry_id != next.registry_id
            || self.schema_version == MEMORY_AUTHORIZATION_SCHEMA
                && next.schema_version != MEMORY_AUTHORIZATION_SCHEMA
            || next.revision < self.revision
            || next.revision == self.revision && next != self
        {
            return Err("Memory authorization identity/revision cannot change or roll back".into());
        }
        for (key, previous) in &self.policies {
            let current = next
                .policies
                .get(key)
                .ok_or("Memory policies must be retained and revoked rather than removed")?;
            if previous.policy_id != current.policy_id
                || previous.tenant_id != current.tenant_id
                || previous.manifest_digest != current.manifest_digest
                || previous
                    .typed_map_manifest_digest
                    .as_ref()
                    .is_some_and(|digest| {
                        current.typed_map_manifest_digest.as_ref() != Some(digest)
                    })
                || previous.revoked && !current.revoked
            {
                return Err(
                    "original Memory tenant, manifest identity and revocation are immutable".into(),
                );
            }
        }
        Ok(())
    }

    /// Returns one complete policy only for the exact immutable currently presented manifest.
    pub fn policy(
        &self,
        manifest: &MemoryArtifactManifest,
        tenant: &str,
    ) -> Result<&MemoryAccessPolicy, String> {
        let policy = self.exact_policy(manifest, tenant)?;
        if policy.typed_map_manifest_digest.is_some() {
            return Err("typed-map resource requires its complete original manifest".into());
        }
        Ok(policy)
    }

    /// Requires both the generic discovery projection and complete original Spatial metadata.
    pub fn typed_map_policy(
        &self,
        map: &MapArtifactManifest,
        tenant: &str,
    ) -> Result<&MemoryAccessPolicy, String> {
        let policy = self.exact_policy(&map_memory_manifest(map)?, tenant)?;
        if policy.typed_map_manifest_digest.as_deref() != Some(map_manifest_digest(map)?.as_str()) {
            return Err(
                "typed-map policy lacks the exact original complete manifest digest".into(),
            );
        }
        Ok(policy)
    }

    /// Resolves common immutable identity without allowing a caller to discard typed provenance.
    fn exact_policy(
        &self,
        manifest: &MemoryArtifactManifest,
        tenant: &str,
    ) -> Result<&MemoryAccessPolicy, String> {
        let key = format!(
            "{}/{}",
            manifest.selector().memory_id(),
            manifest.selector().revision_id()
        );
        let policy = self
            .policies
            .get(&key)
            .ok_or("Memory resource has no explicit authorization policy")?;
        if policy.revoked
            || policy.tenant_id != tenant
            || policy.manifest_digest != memory_manifest_digest(manifest)?
        {
            return Err(
                "Memory policy is revoked, foreign or names another immutable manifest".into(),
            );
        }
        Ok(policy)
    }
}

/// Hashes complete validated metadata, independently of the artifact content digest.
pub fn memory_manifest_digest(manifest: &MemoryArtifactManifest) -> Result<String, String> {
    manifest.validate().map_err(|e| e.to_string())?;
    metadata_digest(manifest)
}

/// Hashes every field of the immutable typed-map manifest, including frame, anchor and format.
pub fn map_manifest_digest(manifest: &MapArtifactManifest) -> Result<String, String> {
    metadata_digest(manifest)
}

/// Builds the semantic discovery projection without mistaking it for complete Spatial provenance.
pub fn map_memory_manifest(map: &MapArtifactManifest) -> Result<MemoryArtifactManifest, String> {
    let local_system_id = map
        .producer_local_system_id()
        .ok_or("typed-map original LocalSystem is unavailable")?;
    MemoryArtifactManifest::new(
        MemorySelector::new(
            crate::MemoryId::new(map.selector().map_id().as_str()).map_err(|e| e.to_string())?,
            crate::MemoryRevisionId::new(map.selector().revision_id().as_str())
                .map_err(|e| e.to_string())?,
        ),
        MemoryKind::Spatial,
        map.producer_provider_id().unwrap_or("typed-map-catalog"),
        crate::MemoryOwner::Node {
            node_id: map.producer_node_id().clone(),
            local_system_id: local_system_id.clone(),
        },
        crate::MemoryScope::Global,
        crate::MemoryVisibility::Exchangeable,
        map.payload_schema()
            .unwrap_or(crate::SPATIAL_MEMORY_SCHEMA_V0_1),
        map.media_type(),
        Some(crate::MemoryArtifactRef::new(
            map.artifact().content_digest().clone(),
            map.artifact().byte_size(),
        )),
        Some(map.source_mission_id().clone()),
        map.source_execution_id().map(str::to_string),
        map.source_task_ref().cloned(),
        map.created_at(),
    )
    .map_err(|e| e.to_string())
}

/// Checks a scoped alias against its complete immutable root without creating map provenance.
/// Only selector, receiving Group and monotonic creation time may differ from the root projection.
pub fn validate_scoped_map_manifest(
    map: &MapArtifactManifest,
    manifest: &MemoryArtifactManifest,
    group: &ExecutionGroupId,
) -> Result<(), String> {
    let original = map_memory_manifest(map)?;
    manifest.validate().map_err(|error| error.to_string())?;
    if manifest.scope() != &crate::MemoryScope::ExecutionGroup(group.clone())
        || manifest.selector() == original.selector()
        || manifest.created_at() < original.created_at()
    {
        return Err(
            "scoped typed-map selector, exact receiving Group or original time differs".into(),
        );
    }
    let original = serde_json::to_value(original).map_err(|error| error.to_string())?;
    let mut scoped = serde_json::to_value(manifest).map_err(|error| error.to_string())?;
    for field in ["selector", "scope", "created_at"] {
        scoped[field] = original[field].clone();
    }
    if scoped != original {
        return Err(
            "scoped typed-map changes original owner, bytes, contracts or production provenance"
                .into(),
        );
    }
    Ok(())
}

/// Uses deterministic JSON object ordering for complete cross-language metadata digests.
pub(crate) fn metadata_digest(manifest: &impl Serialize) -> Result<String, String> {
    use sha2::{Digest as _, Sha256};
    let canonical = serde_json::to_value(manifest).map_err(|e| e.to_string())?;
    Ok(format!(
        "sha256:{:x}",
        Sha256::digest(serde_json::to_vec(&canonical).map_err(|e| e.to_string())?)
    ))
}

/// Rejects identifiers or contracts whose apparent wildcard would silently broaden authority.
pub(crate) fn validate_text(value: &str, max: usize) -> Result<(), String> {
    if value.trim().is_empty()
        || value.trim() != value
        || value.len() > max
        || value.contains('*')
        || value.chars().any(char::is_control)
    {
        return Err("Memory authority requires bounded exact identities and contracts".into());
    }
    Ok(())
}

/// Requires an explicit lowercase SHA-256 identity for immutable metadata and scope versions.
pub(crate) fn validate_digest(value: &str) -> Result<(), String> {
    if !value.strip_prefix("sha256:").is_some_and(|h| {
        h.len() == 64
            && h.bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    }) {
        return Err("Memory authority digest must be exact SHA-256".into());
    }
    Ok(())
}

/// Monotonic membership generation paired with a complete canonical physical-binding digest.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct GroupMembershipVersion {
    /// Control-owned generation; zero is reserved for unavailable historical evidence.
    pub generation: u64,
    /// SHA-256 over the exact current Group, logical slots, Nodes and retained resources.
    pub bindings_digest: String,
}

impl GroupMembershipVersion {
    /// Refuses unavailable generations or ambiguous digest algorithms before consuming authority.
    pub fn validate(&self) -> Result<(), String> {
        if self.generation == 0
            || !self
                .bindings_digest
                .strip_prefix("sha256:")
                .is_some_and(|hex| {
                    hex.len() == 64
                        && hex
                            .bytes()
                            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
                })
        {
            return Err(
                "Group membership requires a positive generation and exact SHA-256 digest".into(),
            );
        }
        Ok(())
    }
}
