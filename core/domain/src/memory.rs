//! Discoverable immutable Memory metadata shared above heterogeneous local stores.
//!
//! Manifests expose ownership, scope, provenance, and optional content-addressed bytes. They do
//! not require local implementations to share a database or storage format.

use crate::{
    ContentDigest, DomainError, ExecutionGroupId, LocalSystemId, MissionId, NodeId, TaskRef,
    TimestampMs,
};
use serde::{Deserialize, Deserializer, Serialize};
use std::fmt::{Display, Formatter};

/// Version identifier for the first generic Memory catalog manifest.
pub const MEMORY_MANIFEST_SCHEMA_V0_1: &str = "roboguide.memory-manifest/v0.1";

/// Classifies a durable or discoverable Memory item by its primary use.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MemoryKind {
    /// Execution history, decisions, or lifecycle evidence.
    Execution,
    /// Maps, landmarks, spatial models, or localization assets.
    Spatial,
    /// Named concepts, scene descriptions, or domain knowledge.
    Semantic,
    /// Reusable outcomes and lessons from previous work.
    Experience,
    /// Opaque files or bundles that do not fit a stronger kind.
    Artifact,
}

/// Limits where a Memory item's meaning is intended to be consumed.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(
    tag = "kind",
    content = "execution_group_id",
    rename_all = "snake_case"
)]
pub enum MemoryScope {
    /// Meaning is local to the owning node and is discoverable only by explicit policy.
    Local,
    /// Meaning is shared among participants of one execution group.
    ExecutionGroup(ExecutionGroupId),
    /// Meaning is not restricted to one current execution group.
    Global,
}

/// Declares whether catalog discovery also permits content exchange.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MemoryVisibility {
    /// Metadata may be discovered but content is not offered for exchange.
    Discoverable,
    /// Metadata and immutable content may be exchanged through the Artifact data plane.
    Exchangeable,
}

/// Identifies a logical Memory item independent of its immutable revisions.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize)]
pub struct MemoryId(String);

impl MemoryId {
    /// Creates a path-safe logical Memory identifier.
    pub fn new(value: impl Into<String>) -> Result<Self, DomainError> {
        let value = value.into();
        validate_identifier(&value, "memory id")?;
        Ok(Self(value))
    }

    /// Returns the stable logical identity.
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl<'de> Deserialize<'de> for MemoryId {
    /// Deserializes through the path-safe constructor invariant.
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        Self::new(String::deserialize(deserializer)?).map_err(serde::de::Error::custom)
    }
}

impl Display for MemoryId {
    /// Writes the stable logical identity.
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(&self.0)
    }
}

/// Identifies one immutable revision of a logical Memory item.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize)]
pub struct MemoryRevisionId(String);

impl MemoryRevisionId {
    /// Creates a path-safe immutable revision identity.
    pub fn new(value: impl Into<String>) -> Result<Self, DomainError> {
        let value = value.into();
        validate_identifier(&value, "memory revision id")?;
        Ok(Self(value))
    }

    /// Returns the stable immutable revision identity.
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl<'de> Deserialize<'de> for MemoryRevisionId {
    /// Deserializes through the path-safe constructor invariant.
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        Self::new(String::deserialize(deserializer)?).map_err(serde::de::Error::custom)
    }
}

impl Display for MemoryRevisionId {
    /// Writes the stable immutable revision identity.
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(&self.0)
    }
}

/// Selects one immutable Memory revision.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
pub struct MemorySelector {
    /// Logical Memory identity.
    memory_id: MemoryId,
    /// Immutable revision identity.
    revision_id: MemoryRevisionId,
}

impl MemorySelector {
    /// Creates a logical item and immutable revision selector.
    pub const fn new(memory_id: MemoryId, revision_id: MemoryRevisionId) -> Self {
        Self {
            memory_id,
            revision_id,
        }
    }

    /// Returns the logical Memory identity.
    pub const fn memory_id(&self) -> &MemoryId {
        &self.memory_id
    }

    /// Returns the immutable revision identity.
    pub const fn revision_id(&self) -> &MemoryRevisionId {
        &self.revision_id
    }
}

impl Display for MemorySelector {
    /// Writes the stable item/revision identity.
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        write!(formatter, "{}/{}", self.memory_id, self.revision_id)
    }
}

/// Attributes Memory to its local owner or a named RoboGuide producer.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "owner", rename_all = "snake_case")]
pub enum MemoryOwner {
    /// A configured local system retains semantic ownership.
    Node {
        /// Node through which the owner is discoverable.
        node_id: NodeId,
        /// Actual local-system owner.
        local_system_id: LocalSystemId,
    },
    /// A named RoboGuide component owns the derived item.
    RoboGuide {
        /// Stable component or projector identity.
        component: String,
    },
}

/// Resolved immutable bytes offered through the Artifact data plane.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct MemoryArtifactRef {
    /// Content-addressed immutable bytes.
    content_digest: ContentDigest,
    /// Exact byte size covered by the digest.
    byte_size: u64,
}

impl MemoryArtifactRef {
    /// Creates a generic immutable artifact reference.
    pub const fn new(content_digest: ContentDigest, byte_size: u64) -> Self {
        Self {
            content_digest,
            byte_size,
        }
    }

    /// Returns the immutable digest.
    pub const fn content_digest(&self) -> &ContentDigest {
        &self.content_digest
    }

    /// Returns the exact artifact byte size.
    pub const fn byte_size(&self) -> u64 {
        self.byte_size
    }
}

/// Declares one Memory catalog provider exposed by a local system.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct MemoryProviderDescriptor {
    /// Node-wide provider identity.
    provider_id: String,
    /// Local system that owns discovery and content policy.
    local_system_id: LocalSystemId,
    /// Memory kind exposed by this provider declaration.
    kind: MemoryKind,
    /// Default semantic sharing scope for provider outputs.
    scope: MemoryScope,
    /// Maximum discovery/exchange visibility offered by the provider.
    visibility: MemoryVisibility,
    /// Versioned manifest payload schema produced or consumed by this provider.
    payload_schema: String,
    /// Media type of content artifacts when present.
    media_type: String,
}

impl MemoryProviderDescriptor {
    /// Creates a provider declaration with explicit local ownership and policy.
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        provider_id: impl Into<String>,
        local_system_id: LocalSystemId,
        kind: MemoryKind,
        scope: MemoryScope,
        visibility: MemoryVisibility,
        payload_schema: impl Into<String>,
        media_type: impl Into<String>,
    ) -> Result<Self, DomainError> {
        let provider_id = provider_id.into();
        let payload_schema = payload_schema.into();
        let media_type = media_type.into();
        validate_identifier(&provider_id, "memory provider id")?;
        reject_blank(&payload_schema, "memory payload schema")?;
        reject_blank(&media_type, "memory media type")?;
        Ok(Self {
            provider_id,
            local_system_id,
            kind,
            scope,
            visibility,
            payload_schema,
            media_type,
        })
    }

    /// Returns the node-wide provider identity.
    pub fn provider_id(&self) -> &str {
        &self.provider_id
    }

    /// Returns the local system that owns the provider.
    pub const fn local_system_id(&self) -> &LocalSystemId {
        &self.local_system_id
    }

    /// Returns the provider's Memory kind.
    pub const fn kind(&self) -> MemoryKind {
        self.kind
    }

    /// Returns the provider's default scope.
    pub const fn scope(&self) -> &MemoryScope {
        &self.scope
    }

    /// Returns the maximum offered visibility.
    pub const fn visibility(&self) -> MemoryVisibility {
        self.visibility
    }

    /// Returns the provider payload schema.
    pub fn payload_schema(&self) -> &str {
        &self.payload_schema
    }

    /// Returns the provider content media type.
    pub fn media_type(&self) -> &str {
        &self.media_type
    }

    /// Validates that one Node-owned manifest stays within this provider declaration.
    pub fn admit_manifest(&self, manifest: &MemoryArtifactManifest) -> Result<(), DomainError> {
        let MemoryOwner::Node {
            local_system_id, ..
        } = manifest.owner()
        else {
            return Err(invalid_memory(
                "node provider cannot admit RoboGuide-owned Memory",
            ));
        };
        if manifest.provider_id() != self.provider_id()
            || local_system_id != self.local_system_id()
            || manifest.kind() != self.kind()
            || manifest.payload_schema() != self.payload_schema()
            || manifest.media_type() != self.media_type()
        {
            return Err(invalid_memory(
                "Memory manifest does not match its registered provider owner/kind/schema/media type",
            ));
        }
        let scope_allowed = match self.scope() {
            MemoryScope::Local => matches!(manifest.scope(), MemoryScope::Local),
            MemoryScope::ExecutionGroup(provider_group) => match manifest.scope() {
                MemoryScope::Local => true,
                MemoryScope::ExecutionGroup(manifest_group) => manifest_group == provider_group,
                MemoryScope::Global => false,
            },
            MemoryScope::Global => true,
        };
        if !scope_allowed {
            return Err(invalid_memory(
                "Memory manifest exceeds its registered provider scope",
            ));
        }
        if self.visibility() == MemoryVisibility::Discoverable
            && manifest.visibility() == MemoryVisibility::Exchangeable
        {
            return Err(invalid_memory(
                "Memory manifest exceeds its registered provider visibility",
            ));
        }
        Ok(())
    }
}

/// Immutable generic Memory metadata retained in the discoverable catalog.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct MemoryArtifactManifest {
    /// Manifest envelope schema.
    schema: String,
    /// Logical item and immutable revision.
    selector: MemorySelector,
    /// Primary Memory kind.
    kind: MemoryKind,
    /// Provider identity declared by the owning node registration or RoboGuide component.
    provider_id: String,
    /// Semantic owner; discovery never transfers ownership.
    owner: MemoryOwner,
    /// Intended sharing scope.
    scope: MemoryScope,
    /// Discovery and exchange policy.
    visibility: MemoryVisibility,
    /// Versioned schema interpreting the Memory content or metadata.
    payload_schema: String,
    /// Media type of artifact content when present.
    media_type: String,
    /// Optional content-addressed immutable bytes.
    artifact: Option<MemoryArtifactRef>,
    /// Optional Mission provenance.
    source_mission_id: Option<MissionId>,
    /// Optional logical execution provenance.
    source_execution_id: Option<String>,
    /// Optional Task provenance.
    source_task_ref: Option<TaskRef>,
    /// RoboGuide-local manifest creation time.
    created_at: TimestampMs,
}

impl MemoryArtifactManifest {
    /// Creates a validated generic Memory manifest without embedding content bytes.
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        selector: MemorySelector,
        kind: MemoryKind,
        provider_id: impl Into<String>,
        owner: MemoryOwner,
        scope: MemoryScope,
        visibility: MemoryVisibility,
        payload_schema: impl Into<String>,
        media_type: impl Into<String>,
        artifact: Option<MemoryArtifactRef>,
        source_mission_id: Option<MissionId>,
        source_execution_id: Option<String>,
        source_task_ref: Option<TaskRef>,
        created_at: TimestampMs,
    ) -> Result<Self, DomainError> {
        let provider_id = provider_id.into();
        let payload_schema = payload_schema.into();
        let media_type = media_type.into();
        validate_identifier(&provider_id, "memory provider id")?;
        reject_blank(&payload_schema, "memory payload schema")?;
        reject_blank(&media_type, "memory media type")?;
        if let MemoryOwner::RoboGuide { component } = &owner {
            validate_identifier(component, "memory owner component")?;
        }
        if visibility == MemoryVisibility::Exchangeable && artifact.is_none() {
            return Err(invalid_memory(
                "exchangeable memory requires a content-addressed artifact reference",
            ));
        }
        if source_execution_id
            .as_ref()
            .is_some_and(|value| value.trim().is_empty())
        {
            return Err(invalid_memory("source execution id must not be blank"));
        }
        if let (Some(mission_id), Some(task_ref)) = (&source_mission_id, &source_task_ref)
            && task_ref.mission_id() != mission_id
        {
            return Err(invalid_memory(
                "source task mission must match source mission provenance",
            ));
        }
        Ok(Self {
            schema: MEMORY_MANIFEST_SCHEMA_V0_1.to_string(),
            selector,
            kind,
            provider_id,
            owner,
            scope,
            visibility,
            payload_schema,
            media_type,
            artifact,
            source_mission_id,
            source_execution_id,
            source_task_ref,
            created_at,
        })
    }

    /// Rechecks all invariants after transport deserialization.
    pub fn validate(&self) -> Result<(), DomainError> {
        if self.schema != MEMORY_MANIFEST_SCHEMA_V0_1 {
            return Err(invalid_memory("unsupported memory manifest schema"));
        }
        Self::new(
            self.selector.clone(),
            self.kind,
            self.provider_id.clone(),
            self.owner.clone(),
            self.scope.clone(),
            self.visibility,
            self.payload_schema.clone(),
            self.media_type.clone(),
            self.artifact.clone(),
            self.source_mission_id.clone(),
            self.source_execution_id.clone(),
            self.source_task_ref.clone(),
            self.created_at,
        )?;
        Ok(())
    }

    /// Returns the logical item/revision selector.
    pub const fn selector(&self) -> &MemorySelector {
        &self.selector
    }

    /// Returns the primary Memory kind.
    pub const fn kind(&self) -> MemoryKind {
        self.kind
    }

    /// Returns the declared provider identity.
    pub fn provider_id(&self) -> &str {
        &self.provider_id
    }

    /// Returns the semantic owner.
    pub const fn owner(&self) -> &MemoryOwner {
        &self.owner
    }

    /// Returns the sharing scope.
    pub const fn scope(&self) -> &MemoryScope {
        &self.scope
    }

    /// Returns the discovery/exchange policy.
    pub const fn visibility(&self) -> MemoryVisibility {
        self.visibility
    }

    /// Returns the payload schema.
    pub fn payload_schema(&self) -> &str {
        &self.payload_schema
    }

    /// Returns the artifact media type.
    pub fn media_type(&self) -> &str {
        &self.media_type
    }

    /// Returns immutable content metadata when the owner offers bytes for exchange.
    pub const fn artifact(&self) -> Option<&MemoryArtifactRef> {
        self.artifact.as_ref()
    }

    /// Returns the optional Mission provenance.
    pub const fn source_mission_id(&self) -> Option<&MissionId> {
        self.source_mission_id.as_ref()
    }

    /// Returns the optional logical execution provenance.
    pub fn source_execution_id(&self) -> Option<&str> {
        self.source_execution_id.as_deref()
    }

    /// Returns the optional Task provenance.
    pub const fn source_task_ref(&self) -> Option<&TaskRef> {
        self.source_task_ref.as_ref()
    }

    /// Returns the RoboGuide-local manifest creation time.
    pub const fn created_at(&self) -> TimestampMs {
        self.created_at
    }
}

/// Node-local lifecycle of one selectively exchanged Memory artifact.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum MemoryReplicaStatus {
    /// Content has been staged in a node-local cache with digest verification.
    Staged,
    /// Content has been imported into the local provider's heterogeneous store.
    Imported,
    /// The node rejected staging or import and retained a diagnostic.
    Rejected,
}

/// Rebuildable evidence of one node-local Memory replica.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct MemoryReplicaSnapshot {
    /// Immutable Memory revision represented by the replica.
    selector: MemorySelector,
    /// Node that owns the local replica.
    node_id: NodeId,
    /// Latest monotonic replica status.
    status: MemoryReplicaStatus,
    /// RoboGuide-local receive time of the latest evidence.
    observed_at: TimestampMs,
    /// Optional rejection reason retained as evidence.
    rejection_reason: Option<String>,
}

impl MemoryReplicaSnapshot {
    /// Creates a replica snapshot projected from accepted evidence.
    pub const fn new(
        selector: MemorySelector,
        node_id: NodeId,
        status: MemoryReplicaStatus,
        observed_at: TimestampMs,
        rejection_reason: Option<String>,
    ) -> Self {
        Self {
            selector,
            node_id,
            status,
            observed_at,
            rejection_reason,
        }
    }

    /// Returns the immutable Memory selector.
    pub const fn selector(&self) -> &MemorySelector {
        &self.selector
    }

    /// Returns the node that owns the replica.
    pub const fn node_id(&self) -> &NodeId {
        &self.node_id
    }

    /// Returns the latest replica lifecycle.
    pub const fn status(&self) -> MemoryReplicaStatus {
        self.status
    }

    /// Returns the RoboGuide-local evidence time.
    pub const fn observed_at(&self) -> TimestampMs {
        self.observed_at
    }

    /// Returns the terminal rejection diagnostic when present.
    pub fn rejection_reason(&self) -> Option<&str> {
        self.rejection_reason.as_deref()
    }
}

/// Validates a path-safe catalog/provider identity.
fn validate_identifier(value: &str, kind: &str) -> Result<(), DomainError> {
    let mut bytes = value.bytes();
    let valid = bytes
        .next()
        .is_some_and(|byte| byte.is_ascii_alphanumeric())
        && bytes
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b':' | b'-'));
    if !valid {
        return Err(invalid_memory(format!(
            "{kind} must match [A-Za-z0-9][A-Za-z0-9._:-]*"
        )));
    }
    Ok(())
}

/// Rejects one blank Memory metadata value.
fn reject_blank(value: &str, kind: &str) -> Result<(), DomainError> {
    if value.trim().is_empty() {
        return Err(invalid_memory(format!("{kind} must not be empty")));
    }
    Ok(())
}

/// Builds a stable Memory invariant error.
fn invalid_memory(reason: impl Into<String>) -> DomainError {
    DomainError::InvalidMemory {
        reason: reason.into(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Discoverable metadata may remain local without advertising bytes.
    #[test]
    fn discoverable_memory_can_be_metadata_only() {
        let manifest = MemoryArtifactManifest::new(
            MemorySelector::new(
                MemoryId::new("experience-a").expect("memory id should be valid"),
                MemoryRevisionId::new("rev-1").expect("revision should be valid"),
            ),
            MemoryKind::Experience,
            "lessons",
            MemoryOwner::Node {
                node_id: NodeId::new("dog-a").expect("node should be valid"),
                local_system_id: LocalSystemId::new("planner").expect("system should be valid"),
            },
            MemoryScope::Local,
            MemoryVisibility::Discoverable,
            "example.experience/v1",
            "application/json",
            None,
            None,
            None,
            None,
            TimestampMs::new(1),
        )
        .expect("metadata-only manifest should be valid");
        assert!(manifest.artifact().is_none());
    }

    /// Exchangeable Memory always resolves through immutable Artifact bytes.
    #[test]
    fn exchangeable_memory_requires_artifact() {
        let result = MemoryArtifactManifest::new(
            MemorySelector::new(
                MemoryId::new("semantic-a").expect("memory id should be valid"),
                MemoryRevisionId::new("rev-1").expect("revision should be valid"),
            ),
            MemoryKind::Semantic,
            "scene-model",
            MemoryOwner::RoboGuide {
                component: "semantic-projector".to_string(),
            },
            MemoryScope::Global,
            MemoryVisibility::Exchangeable,
            "example.scene/v1",
            "application/json",
            None,
            None,
            None,
            None,
            TimestampMs::new(1),
        );
        assert!(matches!(result, Err(DomainError::InvalidMemory { .. })));
    }

    /// RoboGuide ownership and Task provenance cannot carry ambiguous blank or mixed identities.
    #[test]
    fn manifest_rejects_invalid_owner_and_cross_mission_provenance() {
        let selector = MemorySelector::new(
            MemoryId::new("semantic-a").expect("memory id should be valid"),
            MemoryRevisionId::new("rev-1").expect("revision should be valid"),
        );
        let blank_owner = MemoryArtifactManifest::new(
            selector.clone(),
            MemoryKind::Semantic,
            "scene-model",
            MemoryOwner::RoboGuide {
                component: " ".to_string(),
            },
            MemoryScope::Global,
            MemoryVisibility::Discoverable,
            "example.scene/v1",
            "application/json",
            None,
            None,
            None,
            None,
            TimestampMs::new(1),
        );
        assert!(matches!(
            blank_owner,
            Err(DomainError::InvalidMemory { .. })
        ));

        let cross_mission = MemoryArtifactManifest::new(
            selector,
            MemoryKind::Execution,
            "execution-journal",
            MemoryOwner::RoboGuide {
                component: "runtime".to_string(),
            },
            MemoryScope::Global,
            MemoryVisibility::Discoverable,
            "example.execution/v1",
            "application/json",
            None,
            Some(MissionId::new("mission-a").expect("mission id should be valid")),
            None,
            Some(TaskRef::new(
                MissionId::new("mission-b").expect("mission id should be valid"),
                crate::TaskId::new("task-a").expect("task id should be valid"),
            )),
            TimestampMs::new(1),
        );
        assert!(matches!(
            cross_mission,
            Err(DomainError::InvalidMemory { .. })
        ));
    }

    /// Provider admission enforces exact ownership and maximum visibility.
    #[test]
    fn manifest_must_stay_within_provider_contract() {
        let manifest = MemoryArtifactManifest::new(
            MemorySelector::new(
                MemoryId::new("experience-a").expect("memory id should be valid"),
                MemoryRevisionId::new("r1").expect("revision id should be valid"),
            ),
            MemoryKind::Experience,
            "experience-provider",
            MemoryOwner::Node {
                node_id: NodeId::new("dog-a").expect("node id should be valid"),
                local_system_id: LocalSystemId::new("memory")
                    .expect("local system id should be valid"),
            },
            MemoryScope::Global,
            MemoryVisibility::Exchangeable,
            "example.experience/v1",
            "application/json",
            Some(MemoryArtifactRef::new(
                ContentDigest::new("a".repeat(64)).expect("digest should be valid"),
                12,
            )),
            None,
            None,
            None,
            TimestampMs::new(1),
        )
        .expect("manifest should be valid");
        let provider = MemoryProviderDescriptor::new(
            "experience-provider",
            LocalSystemId::new("memory").expect("local system id should be valid"),
            MemoryKind::Experience,
            MemoryScope::Global,
            MemoryVisibility::Exchangeable,
            "example.experience/v1",
            "application/json",
        )
        .expect("provider should be valid");
        provider
            .admit_manifest(&manifest)
            .expect("exact provider should admit manifest");

        let discoverable = MemoryProviderDescriptor::new(
            "experience-provider",
            LocalSystemId::new("memory").expect("local system id should be valid"),
            MemoryKind::Experience,
            MemoryScope::Global,
            MemoryVisibility::Discoverable,
            "example.experience/v1",
            "application/json",
        )
        .expect("provider should be valid");
        assert!(matches!(
            discoverable.admit_manifest(&manifest),
            Err(DomainError::InvalidMemory { .. })
        ));
    }
}
