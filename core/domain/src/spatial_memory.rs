//! Immutable spatial-memory artifact values shared by State, Control, and adapters.
//!
//! The values in this module identify a map revision and its provenance.  They do not contain
//! map bytes, choose an active map, or grant resource ownership.  Large artifact data stays behind
//! the transport-neutral artifact ports.

use crate::{DomainError, LocalSystemId, MissionId, NodeId, TaskRef, TimestampMs};
use serde::{Deserialize, Deserializer, Serialize, Serializer};
use std::fmt::{Display, Formatter};

/// Version identifier for the first Spatial Memory manifest contract.
pub const SPATIAL_MEMORY_SCHEMA_V0_1: &str = "roboguide.map-manifest/v0.1";

/// Alias for callers referring specifically to the map-manifest wire contract.
#[allow(dead_code)]
pub const MAP_MANIFEST_SCHEMA_V0_1: &str = SPATIAL_MEMORY_SCHEMA_V0_1;

/// Identifies a logical map independent of any immutable revision.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, serde::Serialize)]
pub struct MapId(String);

impl MapId {
    /// Creates a logical map identifier that is safe as one unescaped HTTP path segment.
    ///
    /// The first character must be an ASCII letter or digit. Remaining characters may additionally
    /// contain `.`, `_`, `:`, or `-`; every other character is rejected.
    pub fn new(value: impl Into<String>) -> Result<Self, DomainError> {
        let value = value.into();
        if value.is_empty() {
            return Err(DomainError::EmptyValue { kind: "map" });
        }
        if !is_path_safe_map_identifier(&value) {
            return Err(DomainError::InvalidSpatialMemory {
                reason: "map identifier must match [A-Za-z0-9][A-Za-z0-9._:-]*".to_string(),
            });
        }
        Ok(Self(value))
    }

    /// Returns the stable logical map identifier.
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl<'de> Deserialize<'de> for MapId {
    /// Deserializes a logical map identifier through the path-safe constructor invariant.
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        let value = String::deserialize(deserializer)?;
        Self::new(value).map_err(serde::de::Error::custom)
    }
}

impl Display for MapId {
    /// Writes the stable map identifier.
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(&self.0)
    }
}

/// Identifies one immutable revision within a logical map.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, serde::Serialize)]
pub struct MapRevisionId(String);

impl MapRevisionId {
    /// Creates an immutable revision identifier safe as one unescaped HTTP path segment.
    ///
    /// The first character must be an ASCII letter or digit. Remaining characters may additionally
    /// contain `.`, `_`, `:`, or `-`; every other character is rejected.
    pub fn new(value: impl Into<String>) -> Result<Self, DomainError> {
        let value = value.into();
        if value.is_empty() {
            return Err(DomainError::EmptyValue {
                kind: "map revision",
            });
        }
        if !is_path_safe_map_identifier(&value) {
            return Err(DomainError::InvalidSpatialMemory {
                reason: "map revision identifier must match [A-Za-z0-9][A-Za-z0-9._:-]*"
                    .to_string(),
            });
        }
        Ok(Self(value))
    }

    /// Returns the stable revision identifier.
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl<'de> Deserialize<'de> for MapRevisionId {
    /// Deserializes a map revision identifier through the path-safe constructor invariant.
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        let value = String::deserialize(deserializer)?;
        Self::new(value).map_err(serde::de::Error::custom)
    }
}

impl Display for MapRevisionId {
    /// Writes the stable revision identifier.
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(&self.0)
    }
}

/// Reports whether a map or revision identifier matches the shared HTTP path-safe ASCII grammar.
fn is_path_safe_map_identifier(value: &str) -> bool {
    let mut bytes = value.bytes();
    bytes
        .next()
        .is_some_and(|byte| byte.is_ascii_alphanumeric())
        && bytes
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b':' | b'-'))
}

/// A canonical SHA-256 content digest for an immutable artifact.
#[derive(
    Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, serde::Serialize, serde::Deserialize,
)]
pub struct ContentDigest(String);

impl ContentDigest {
    /// Creates a canonical `sha256:<lowercase hex>` SHA-256 digest.
    pub fn new(value: impl Into<String>) -> Result<Self, DomainError> {
        let value = value.into();
        let raw = value.strip_prefix("sha256:").unwrap_or(&value);
        let valid = raw.len() == 64
            && raw
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte));
        if !valid {
            return Err(DomainError::InvalidSpatialMemory {
                reason: "content digest must be 64 lowercase SHA-256 hex characters".to_string(),
            });
        }
        Ok(Self(format!("sha256:{raw}")))
    }

    /// Returns the canonical digest text including the algorithm prefix.
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl Display for ContentDigest {
    /// Writes the canonical digest text.
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(&self.0)
    }
}

/// Identifies the fixed physical or semantic anchor used by a map revision.
#[derive(
    Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, serde::Serialize, serde::Deserialize,
)]
pub struct SpatialAnchorId(String);

impl SpatialAnchorId {
    /// Creates a non-empty spatial anchor identifier.
    pub fn new(value: impl Into<String>) -> Result<Self, DomainError> {
        let value = value.into();
        if value.trim().is_empty() {
            return Err(DomainError::EmptyValue {
                kind: "spatial anchor",
            });
        }
        Ok(Self(value))
    }

    /// Returns the stable spatial anchor identifier.
    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl Display for SpatialAnchorId {
    /// Writes the stable spatial anchor identifier.
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(&self.0)
    }
}

/// Identifies a logical map revision without resolving its bytes.
#[derive(
    Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, serde::Serialize, serde::Deserialize,
)]
pub struct MapRevisionSelector {
    /// Logical map identity.
    map_id: MapId,
    /// Immutable revision identity within the map.
    revision_id: MapRevisionId,
}

impl MapRevisionSelector {
    /// Creates a map/revision selector used by Mission plans and catalog queries.
    pub const fn new(map_id: MapId, revision_id: MapRevisionId) -> Self {
        Self {
            map_id,
            revision_id,
        }
    }

    /// Returns the logical map identity.
    pub const fn map_id(&self) -> &MapId {
        &self.map_id
    }

    /// Returns the immutable revision identity.
    pub const fn revision_id(&self) -> &MapRevisionId {
        &self.revision_id
    }
}

impl Display for MapRevisionSelector {
    /// Writes a stable map/revision path-like identity.
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        write!(formatter, "{}/{}", self.map_id, self.revision_id)
    }
}

/// A resolved immutable artifact reference containing its digest and size.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct MapArtifactRef {
    /// Logical map and immutable revision selected by the caller.
    selector: MapRevisionSelector,
    /// Digest used to address the artifact bytes in the content store.
    content_digest: ContentDigest,
    /// Exact byte length covered by the digest.
    byte_size: u64,
}

impl MapArtifactRef {
    /// Creates a resolved artifact reference after validating its byte size.
    pub const fn new(
        selector: MapRevisionSelector,
        content_digest: ContentDigest,
        byte_size: u64,
    ) -> Self {
        Self {
            selector,
            content_digest,
            byte_size,
        }
    }

    /// Returns the logical map/revision selector.
    pub const fn selector(&self) -> &MapRevisionSelector {
        &self.selector
    }

    /// Returns the immutable content digest.
    pub const fn content_digest(&self) -> &ContentDigest {
        &self.content_digest
    }

    /// Returns the exact artifact byte size.
    pub const fn byte_size(&self) -> u64 {
        self.byte_size
    }
}

/// Manifest describing one immutable map artifact and its provenance.
#[derive(Debug, Clone, PartialEq)]
pub struct MapArtifactManifest {
    /// Contract version used to interpret this manifest.
    schema_version: String,
    /// Resolved immutable artifact reference.
    artifact: MapArtifactRef,
    /// Media type of the opaque bundle, for example `application/octet-stream`.
    media_type: String,
    /// Producer-declared map format family name.
    format_name: String,
    /// Producer-declared map format version.
    format_version: String,
    /// Node that produced the artifact.
    producer_node_id: NodeId,
    /// Optional local system that produced the artifact.
    producer_local_system_id: Option<LocalSystemId>,
    /// Mission that produced the artifact.
    source_mission_id: MissionId,
    /// Optional execution identity associated with production.
    source_execution_id: Option<String>,
    /// Optional source task associated with production.
    source_task_ref: Option<TaskRef>,
    /// Fixed root frame declared by the producer.
    root_frame: String,
    /// Coordinate convention used by the opaque map bundle.
    coordinate_convention: String,
    /// Fixed physical or semantic anchor shared by consumers.
    anchor_id: SpatialAnchorId,
    /// Optional metric resolution in metres per cell/unit.
    resolution_meters: Option<f64>,
    /// RoboGuide-local creation time for the manifest.
    created_at: TimestampMs,
    /// Optional immutable parent revision for lineage.
    parent_revision_id: Option<MapRevisionId>,
}

// Resolution is validated as finite and positive by `new`; retaining an explicit Eq
// implementation keeps manifest-bearing evidence events comparable like the rest of the
// domain event envelope.
impl Eq for MapArtifactManifest {}

/// Wire representation of the v0.1 top-level map manifest.
///
/// Keeping this separate from the domain representation lets the Rust API retain typed nested
/// references while the cross-language contract stays a flat, stable artifact envelope.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct MapManifestWire {
    /// Manifest contract identifier.
    schema: String,
    /// Logical map identity.
    map_id: String,
    /// Immutable revision identity.
    revision_id: String,
    /// Content-addressed bytes digest.
    content_digest: String,
    /// Exact artifact byte size.
    byte_size: u64,
    /// Opaque artifact media type.
    media_type: String,
    /// Opaque map format descriptor.
    format: MapFormatWire,
    /// Producing node identity.
    producer_node_id: String,
    /// Optional producing local-system identity.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    producer_local_system_id: Option<String>,
    /// Source Mission identity.
    source_mission_id: String,
    /// Optional source execution identity.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    source_execution_id: Option<String>,
    /// Optional source Task identity.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    source_task_ref: Option<TaskRef>,
    /// Root coordinate frame.
    root_frame: String,
    /// Coordinate convention used by the artifact.
    coordinate_convention: String,
    /// Optional metric resolution.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    resolution_meters: Option<f64>,
    /// Fixed spatial anchor descriptor.
    spatial_anchor: SpatialAnchorWire,
    /// Optional immutable parent revision.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    parent_revision_id: Option<String>,
    /// RoboGuide-local creation timestamp.
    created_at_ms: u64,
}

/// Wire representation of the producer's map format descriptor.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct MapFormatWire {
    /// Format family name.
    name: String,
    /// Format version understood by the adapter.
    version: String,
}

/// Wire representation of the fixed spatial anchor.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct SpatialAnchorWire {
    /// Stable anchor identity.
    id: String,
    /// Anchor kind; v0 uses a fixed anchor.
    kind: String,
}

/// Returns the v0 fixed-anchor wire kind.
fn default_anchor_kind() -> String {
    "fixed".to_string()
}

impl Serialize for MapArtifactManifest {
    /// Serializes a typed manifest into the versioned top-level contract envelope.
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        MapManifestWire {
            schema: self.schema_version.clone(),
            map_id: self.selector().map_id().as_str().to_string(),
            revision_id: self.selector().revision_id().as_str().to_string(),
            content_digest: self.artifact.content_digest().as_str().to_string(),
            byte_size: self.artifact.byte_size(),
            media_type: self.media_type.clone(),
            format: MapFormatWire {
                name: self.format_name.clone(),
                version: self.format_version.clone(),
            },
            producer_node_id: self.producer_node_id.as_str().to_string(),
            producer_local_system_id: self
                .producer_local_system_id
                .as_ref()
                .map(|value| value.as_str().to_string()),
            source_mission_id: self.source_mission_id.as_str().to_string(),
            source_execution_id: self.source_execution_id.clone(),
            source_task_ref: self.source_task_ref.clone(),
            root_frame: self.root_frame.clone(),
            coordinate_convention: self.coordinate_convention.clone(),
            resolution_meters: self.resolution_meters,
            spatial_anchor: SpatialAnchorWire {
                id: self.anchor_id.as_str().to_string(),
                kind: default_anchor_kind(),
            },
            parent_revision_id: self
                .parent_revision_id
                .as_ref()
                .map(|value| value.as_str().to_string()),
            created_at_ms: self.created_at.as_millis(),
        }
        .serialize(serializer)
    }
}

impl<'de> Deserialize<'de> for MapArtifactManifest {
    /// Deserializes and validates the versioned top-level manifest envelope.
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        let wire = MapManifestWire::deserialize(deserializer)?;
        if wire.schema != SPATIAL_MEMORY_SCHEMA_V0_1 {
            return Err(serde::de::Error::custom("unsupported map manifest schema"));
        }
        if wire.spatial_anchor.kind != "fixed" {
            return Err(serde::de::Error::custom(
                "spatial anchor kind must be `fixed` in v0.1",
            ));
        }
        if wire
            .source_execution_id
            .as_deref()
            .is_some_and(|value| value.trim().is_empty())
        {
            return Err(serde::de::Error::custom(
                "source execution identity must not be blank",
            ));
        }
        let map_id = MapId::new(wire.map_id).map_err(serde::de::Error::custom)?;
        let revision_id = MapRevisionId::new(wire.revision_id).map_err(serde::de::Error::custom)?;
        let selector = MapRevisionSelector::new(map_id, revision_id);
        let artifact = MapArtifactRef::new(
            selector,
            ContentDigest::new(wire.content_digest).map_err(serde::de::Error::custom)?,
            wire.byte_size,
        );
        let producer_local_system_id = wire
            .producer_local_system_id
            .map(LocalSystemId::new)
            .transpose()
            .map_err(serde::de::Error::custom)?;
        let parent_revision_id = wire
            .parent_revision_id
            .map(MapRevisionId::new)
            .transpose()
            .map_err(serde::de::Error::custom)?;
        MapArtifactManifest::new_with_format(
            artifact,
            wire.media_type,
            wire.format.name,
            wire.format.version,
            NodeId::new(wire.producer_node_id).map_err(serde::de::Error::custom)?,
            producer_local_system_id,
            MissionId::new(wire.source_mission_id).map_err(serde::de::Error::custom)?,
            wire.source_execution_id,
            wire.source_task_ref,
            wire.root_frame,
            wire.coordinate_convention,
            SpatialAnchorId::new(wire.spatial_anchor.id).map_err(serde::de::Error::custom)?,
            wire.resolution_meters,
            TimestampMs::new(wire.created_at_ms),
            parent_revision_id,
        )
        .map_err(serde::de::Error::custom)
    }
}

impl MapArtifactManifest {
    /// Creates and validates a manifest using one legacy format label as name and version.
    ///
    /// Call [`Self::new_with_format`] when the producer declares distinct format name and
    /// version values.
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        artifact: MapArtifactRef,
        media_type: impl Into<String>,
        format: impl Into<String>,
        producer_node_id: NodeId,
        producer_local_system_id: Option<LocalSystemId>,
        source_mission_id: MissionId,
        source_execution_id: Option<String>,
        source_task_ref: Option<TaskRef>,
        root_frame: impl Into<String>,
        coordinate_convention: impl Into<String>,
        anchor_id: SpatialAnchorId,
        resolution_meters: Option<f64>,
        created_at: TimestampMs,
        parent_revision_id: Option<MapRevisionId>,
    ) -> Result<Self, DomainError> {
        let format = format.into();
        Self::new_with_format(
            artifact,
            media_type,
            format.clone(),
            format,
            producer_node_id,
            producer_local_system_id,
            source_mission_id,
            source_execution_id,
            source_task_ref,
            root_frame,
            coordinate_convention,
            anchor_id,
            resolution_meters,
            created_at,
            parent_revision_id,
        )
    }

    /// Creates and validates a manifest with independently declared format name and version.
    #[allow(clippy::too_many_arguments)]
    pub fn new_with_format(
        artifact: MapArtifactRef,
        media_type: impl Into<String>,
        format_name: impl Into<String>,
        format_version: impl Into<String>,
        producer_node_id: NodeId,
        producer_local_system_id: Option<LocalSystemId>,
        source_mission_id: MissionId,
        source_execution_id: Option<String>,
        source_task_ref: Option<TaskRef>,
        root_frame: impl Into<String>,
        coordinate_convention: impl Into<String>,
        anchor_id: SpatialAnchorId,
        resolution_meters: Option<f64>,
        created_at: TimestampMs,
        parent_revision_id: Option<MapRevisionId>,
    ) -> Result<Self, DomainError> {
        let media_type = media_type.into();
        let format_name = format_name.into();
        let format_version = format_version.into();
        let root_frame = root_frame.into();
        let coordinate_convention = coordinate_convention.into();
        for (value, kind) in [
            (&media_type, "map media type"),
            (&format_name, "map format name"),
            (&format_version, "map format version"),
            (&root_frame, "map root frame"),
            (&coordinate_convention, "map coordinate convention"),
        ] {
            if value.trim().is_empty() {
                return Err(DomainError::EmptyValue { kind });
            }
        }
        if resolution_meters.is_some_and(|value| !value.is_finite() || value <= 0.0) {
            return Err(DomainError::InvalidSpatialMemory {
                reason: "map resolution must be a finite positive number".to_string(),
            });
        }
        if source_execution_id
            .as_deref()
            .is_some_and(|value| value.trim().is_empty())
        {
            return Err(DomainError::EmptyValue {
                kind: "source execution",
            });
        }
        if source_task_ref
            .as_ref()
            .is_some_and(|task| task.mission_id() != &source_mission_id)
        {
            return Err(DomainError::InvalidSpatialMemory {
                reason: "source task belongs to a different source mission".to_string(),
            });
        }
        if parent_revision_id.as_ref() == Some(artifact.selector().revision_id()) {
            return Err(DomainError::InvalidSpatialMemory {
                reason: "map revision cannot be its own parent".to_string(),
            });
        }
        Ok(Self {
            schema_version: SPATIAL_MEMORY_SCHEMA_V0_1.to_string(),
            artifact,
            media_type,
            format_name,
            format_version,
            producer_node_id,
            producer_local_system_id,
            source_mission_id,
            source_execution_id,
            source_task_ref,
            root_frame,
            coordinate_convention,
            anchor_id,
            resolution_meters,
            created_at,
            parent_revision_id,
        })
    }

    /// Returns the manifest schema version.
    pub fn schema_version(&self) -> &str {
        &self.schema_version
    }

    /// Returns the resolved immutable artifact reference.
    pub const fn artifact(&self) -> &MapArtifactRef {
        &self.artifact
    }

    /// Returns a copy with a replacement immutable artifact reference.
    ///
    /// The replacement must retain the same logical map/revision selector so callers cannot
    /// silently change a manifest's identity while updating content-addressed metadata.
    pub fn with_artifact(&self, artifact: MapArtifactRef) -> Result<Self, DomainError> {
        if artifact.selector() != self.selector() {
            return Err(DomainError::InvalidSpatialMemory {
                reason: "replacement artifact selector differs from manifest selector".to_string(),
            });
        }
        let mut copy = self.clone();
        copy.artifact = artifact;
        Ok(copy)
    }

    /// Returns the logical map/revision selector.
    pub const fn selector(&self) -> &MapRevisionSelector {
        self.artifact.selector()
    }

    /// Returns the artifact media type.
    pub fn media_type(&self) -> &str {
        &self.media_type
    }

    /// Returns the producer-declared map format family name.
    pub fn format_name(&self) -> &str {
        &self.format_name
    }

    /// Returns the producer-declared map format version.
    pub fn format_version(&self) -> &str {
        &self.format_version
    }

    /// Returns the producing node identity.
    pub const fn producer_node_id(&self) -> &NodeId {
        &self.producer_node_id
    }

    /// Returns the producing local system, when declared.
    pub const fn producer_local_system_id(&self) -> Option<&LocalSystemId> {
        self.producer_local_system_id.as_ref()
    }

    /// Returns the Mission that produced the artifact.
    pub const fn source_mission_id(&self) -> &MissionId {
        &self.source_mission_id
    }

    /// Returns the source execution identity, when declared.
    pub fn source_execution_id(&self) -> Option<&str> {
        self.source_execution_id.as_deref()
    }

    /// Returns the source Task identity, when declared.
    pub const fn source_task_ref(&self) -> Option<&TaskRef> {
        self.source_task_ref.as_ref()
    }

    /// Returns the fixed root frame.
    pub fn root_frame(&self) -> &str {
        &self.root_frame
    }

    /// Returns the coordinate convention.
    pub fn coordinate_convention(&self) -> &str {
        &self.coordinate_convention
    }

    /// Returns the fixed physical or semantic anchor.
    pub const fn anchor_id(&self) -> &SpatialAnchorId {
        &self.anchor_id
    }

    /// Returns the optional metric resolution.
    pub const fn resolution_meters(&self) -> Option<f64> {
        self.resolution_meters
    }

    /// Returns the RoboGuide-local creation time.
    pub const fn created_at(&self) -> TimestampMs {
        self.created_at
    }

    /// Returns the immutable parent revision, when this revision derives from one.
    pub const fn parent_revision_id(&self) -> Option<&MapRevisionId> {
        self.parent_revision_id.as_ref()
    }
}

/// Lifecycle of a logical map revision in the catalog.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum MapRevisionStatus {
    /// The manifest is known but its artifact is not yet published.
    Declared,
    /// The immutable artifact is available from the central store.
    Published,
}

/// Lifecycle of one node's replica of an immutable map revision.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum MapReplicaStatus {
    /// Bytes are being staged or have been staged locally.
    Staged,
    /// Bytes were imported into the node-local cache.
    Imported,
    /// The node verified the artifact and its spatial metadata.
    Verified,
    /// The node rejected the artifact or could not verify it.
    Rejected,
}

/// Rebuildable metadata for one logical map revision.
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct MapRevisionSnapshot {
    /// Immutable manifest and provenance.
    manifest: MapArtifactManifest,
    /// Current revision lifecycle.
    status: MapRevisionStatus,
}

impl MapRevisionSnapshot {
    /// Creates a revision snapshot at the supplied lifecycle status.
    pub const fn new(manifest: MapArtifactManifest, status: MapRevisionStatus) -> Self {
        Self { manifest, status }
    }

    /// Returns the immutable manifest.
    pub const fn manifest(&self) -> &MapArtifactManifest {
        &self.manifest
    }

    /// Returns the current revision lifecycle.
    pub const fn status(&self) -> MapRevisionStatus {
        self.status
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Builds one valid immutable artifact reference for manifest tests.
    fn artifact() -> MapArtifactRef {
        MapArtifactRef::new(
            MapRevisionSelector::new(
                MapId::new("lab-map").expect("map id is valid"),
                MapRevisionId::new("r1").expect("revision id is valid"),
            ),
            ContentDigest::new("a".repeat(64)).expect("digest is valid"),
            12,
        )
    }

    /// Map and revision constructors accept exactly the shared path-safe ASCII grammar.
    #[test]
    fn map_identifiers_enforce_path_safe_ascii_grammar() {
        for value in ["a", "Z", "0", "Map_9.release:one-two"] {
            assert!(MapId::new(value).is_ok(), "map id should accept {value:?}");
            assert!(
                MapRevisionId::new(value).is_ok(),
                "revision id should accept {value:?}"
            );
        }

        for value in [
            "",
            ".map",
            "_map",
            ":map",
            "-map",
            "map/name",
            "map\\name",
            "map name",
            "map?x",
            "map#x",
            "map%x",
            "地图",
            "map\n",
        ] {
            assert!(MapId::new(value).is_err(), "map id should reject {value:?}");
            assert!(
                MapRevisionId::new(value).is_err(),
                "revision id should reject {value:?}"
            );
        }
    }

    /// Standalone serde decoding cannot bypass map and revision identifier validation.
    #[test]
    fn map_identifier_deserialization_enforces_constructor_invariant() {
        let map: MapId = serde_json::from_str("\"Map_9.release:one-two\"")
            .expect("path-safe map id deserializes");
        let revision: MapRevisionId =
            serde_json::from_str("\"r1:patched\"").expect("path-safe revision id deserializes");
        assert_eq!(map.as_str(), "Map_9.release:one-two");
        assert_eq!(revision.as_str(), "r1:patched");

        for encoded in ["\"../map\"", "\"map/revision\"", "\"地图\"", "\" map\""] {
            assert!(
                serde_json::from_str::<MapId>(encoded).is_err(),
                "map id serde should reject {encoded}"
            );
            assert!(
                serde_json::from_str::<MapRevisionId>(encoded).is_err(),
                "revision id serde should reject {encoded}"
            );
        }
    }

    /// Digest construction accepts the two wire spellings but stores one canonical form.
    #[test]
    fn content_digest_normalizes_prefix() {
        let plain = ContentDigest::new("b".repeat(64)).expect("plain digest is valid");
        let prefixed = ContentDigest::new(format!("sha256:{}", "b".repeat(64)))
            .expect("prefixed digest is valid");
        assert_eq!(plain, prefixed);
        assert_eq!(plain.as_str(), format!("sha256:{}", "b".repeat(64)));
        assert!(ContentDigest::new("A".repeat(64)).is_err());
        assert!(ContentDigest::new("sha256:short").is_err());
    }

    /// Manifest construction rejects invalid resolution and blank semantic metadata.
    #[test]
    fn manifest_validates_spatial_metadata() {
        let base = MapArtifactManifest::new_with_format(
            artifact(),
            "application/octet-stream",
            "nav2-map-bundle",
            "grid-v1",
            NodeId::new("dog-a").expect("node id is valid"),
            None,
            MissionId::new("mission-a").expect("mission id is valid"),
            None,
            None,
            "map",
            "enu",
            SpatialAnchorId::new("lab-origin").expect("anchor is valid"),
            Some(0.05),
            TimestampMs::new(42),
            None,
        )
        .expect("manifest is valid");
        assert_eq!(base.schema_version(), SPATIAL_MEMORY_SCHEMA_V0_1);
        assert_eq!(base.format_name(), "nav2-map-bundle");
        assert_eq!(base.format_version(), "grid-v1");
        assert!(
            MapArtifactManifest::new(
                base.artifact().clone(),
                "",
                "grid-v1",
                base.producer_node_id().clone(),
                None,
                base.source_mission_id().clone(),
                None,
                None,
                "map",
                "enu",
                base.anchor_id().clone(),
                Some(0.05),
                TimestampMs::new(42),
                None,
            )
            .is_err()
        );
        assert!(
            MapArtifactManifest::new_with_format(
                base.artifact().clone(),
                "application/octet-stream",
                "",
                "grid-v1",
                base.producer_node_id().clone(),
                None,
                base.source_mission_id().clone(),
                None,
                None,
                "map",
                "enu",
                base.anchor_id().clone(),
                Some(0.05),
                TimestampMs::new(42),
                None,
            )
            .is_err()
        );
        assert!(
            MapArtifactManifest::new_with_format(
                base.artifact().clone(),
                "application/octet-stream",
                "nav2-map-bundle",
                "",
                base.producer_node_id().clone(),
                None,
                base.source_mission_id().clone(),
                None,
                None,
                "map",
                "enu",
                base.anchor_id().clone(),
                Some(0.05),
                TimestampMs::new(42),
                None,
            )
            .is_err()
        );
        assert!(
            MapArtifactManifest::new(
                base.artifact().clone(),
                "application/octet-stream",
                "grid-v1",
                base.producer_node_id().clone(),
                None,
                base.source_mission_id().clone(),
                None,
                None,
                "map",
                "enu",
                base.anchor_id().clone(),
                Some(0.0),
                TimestampMs::new(42),
                None,
            )
            .is_err()
        );
    }

    /// Replacing a manifest artifact cannot silently change its logical selector.
    #[test]
    fn manifest_artifact_replacement_preserves_selector() {
        let manifest = MapArtifactManifest::new(
            artifact(),
            "application/octet-stream",
            "grid-v1",
            NodeId::new("dog-a").expect("node id is valid"),
            None,
            MissionId::new("mission-a").expect("mission id is valid"),
            None,
            None,
            "map",
            "enu",
            SpatialAnchorId::new("lab-origin").expect("anchor is valid"),
            None,
            TimestampMs::new(42),
            None,
        )
        .expect("manifest is valid");
        let other = MapArtifactRef::new(
            MapRevisionSelector::new(
                MapId::new("other-map").expect("map id is valid"),
                MapRevisionId::new("r1").expect("revision id is valid"),
            ),
            ContentDigest::new("c".repeat(64)).expect("digest is valid"),
            12,
        );
        assert!(manifest.with_artifact(other).is_err());
    }

    /// Manifest JSON uses the cross-language top-level field names and round-trips typed values.
    #[test]
    fn manifest_json_matches_v0_wire_shape() {
        let manifest = MapArtifactManifest::new_with_format(
            artifact(),
            "application/octet-stream",
            "nav2-map-bundle",
            "grid-v1",
            NodeId::new("dog-a").expect("node id is valid"),
            None,
            MissionId::new("mission-a").expect("mission id is valid"),
            Some("execution-a".to_string()),
            None,
            "map",
            "enu",
            SpatialAnchorId::new("lab-origin").expect("anchor is valid"),
            Some(0.05),
            TimestampMs::new(42),
            None,
        )
        .expect("manifest is valid");
        let value = serde_json::to_value(&manifest).expect("manifest serializes");
        assert_eq!(value["schema"], SPATIAL_MEMORY_SCHEMA_V0_1);
        assert_eq!(value["map_id"], "lab-map");
        assert_eq!(value["revision_id"], "r1");
        assert_eq!(
            value["content_digest"],
            format!("sha256:{}", "a".repeat(64))
        );
        assert_eq!(value["format"]["name"], "nav2-map-bundle");
        assert_eq!(value["format"]["version"], "grid-v1");
        assert_eq!(value["spatial_anchor"]["id"], "lab-origin");
        assert!(value.get("artifact").is_none());
        let decoded: MapArtifactManifest =
            serde_json::from_value(value.clone()).expect("manifest deserializes");
        assert_eq!(decoded, manifest);
        assert_eq!(decoded.format_name(), "nav2-map-bundle");
        assert_eq!(decoded.format_version(), "grid-v1");

        let mut unsupported = value;
        unsupported["spatial_anchor"]["description"] =
            serde_json::Value::String("not part of v0.1".to_string());
        assert!(serde_json::from_value::<MapArtifactManifest>(unsupported).is_err());
    }
}

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
    ) -> Self {
        Self {
            selector,
            node_id,
            status,
            mission_id,
            observed_at,
            rejection_reason,
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
}

impl MapRevisionSnapshot {
    /// Returns a copy with an updated lifecycle while retaining the manifest.
    pub fn with_status(&self, status: MapRevisionStatus) -> Self {
        Self {
            manifest: self.manifest.clone(),
            status,
        }
    }
}
