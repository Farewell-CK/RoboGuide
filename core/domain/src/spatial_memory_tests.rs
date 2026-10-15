//! Spatial Memory domain invariant tests.

use super::*;
use crate::{
    MEMORY_AUTHORIZATION_SCHEMA, MEMORY_AUTHORIZATION_SCHEMA_V0_1, MemoryAccessOperation,
    MemoryAccessPolicy, MemoryAuthorizationRegistry, TaskId, map_manifest_digest,
    map_memory_manifest, memory_manifest_digest,
};

/// Constructs complete software provenance for schema checks, without asserting physical evidence.
fn provider_map() -> MapArtifactManifest {
    let mission = MissionId::new("file-computation").unwrap();
    MapArtifactManifest::new_with_format(
        artifact(),
        "application/octet-stream",
        "observed-file-tiles",
        "v0.1",
        NodeId::new("configured-compute").unwrap(),
        Some(LocalSystemId::new("files").unwrap()),
        mission.clone(),
        Some("original-build-attempt".into()),
        Some(TaskRef::new(mission, TaskId::new("build").unwrap())),
        "source-frame",
        "declared-file-frame",
        SpatialAnchorId::new("source-anchor").unwrap(),
        Some(0.05),
        TimestampMs::new(42),
        None,
    )
    .unwrap()
    .with_producer_provider("configured-provider", "source-content/v1")
    .unwrap()
}

/// Spatial-only metadata cannot be stripped through a generic projection with identical CAS bytes.
#[test]
fn typed_map_policy_binds_complete_metadata_and_refuses_projection_only_access() {
    let map = provider_map();
    let projection = map_memory_manifest(&map).unwrap();
    let policy = MemoryAccessPolicy {
        policy_id: "original-map".into(),
        selector: projection.selector().clone(),
        manifest_digest: memory_manifest_digest(&projection).unwrap(),
        typed_map_manifest_digest: Some(map_manifest_digest(&map).unwrap()),
        tenant_id: "local".into(),
        principals: std::collections::BTreeMap::from([(
            "reader".into(),
            std::collections::BTreeSet::from([MemoryAccessOperation::Read]),
        )]),
        groups: vec![],
        revoked: false,
    };
    let registry = MemoryAuthorizationRegistry {
        schema_version: MEMORY_AUTHORIZATION_SCHEMA.into(),
        registry_id: "map-authority".into(),
        revision: 1,
        policies: std::collections::BTreeMap::from([(policy.key(), policy)]),
    };
    registry.validate().unwrap();
    registry.typed_map_policy(&map, "local").unwrap();
    assert!(registry.policy(&projection, "local").is_err());
    assert!(registry.typed_map_policy(&map, "foreign").is_err());
    for (field, value) in [
        ("root_frame", serde_json::json!("changed-frame")),
        (
            "coordinate_convention",
            serde_json::json!("changed-convention"),
        ),
        (
            "format",
            serde_json::json!({"name":"changed-format", "version":"v1"}),
        ),
        ("resolution_meters", serde_json::json!(0.125)),
        ("parent_revision_id", serde_json::json!("parent")),
        (
            "spatial_anchor",
            serde_json::json!({"id":"changed-anchor", "kind":"fixed"}),
        ),
    ] {
        let mut value_map = serde_json::to_value(&map).unwrap();
        value_map[field] = value;
        let changed: MapArtifactManifest =
            serde_json::from_value(value_map).unwrap_or_else(|error| panic!("{field}: {error}"));
        assert_eq!(
            map_memory_manifest(&changed).unwrap(),
            projection,
            "{field}"
        );
        assert_ne!(
            map_manifest_digest(&changed).unwrap(),
            map_manifest_digest(&map).unwrap()
        );
        assert!(
            registry.typed_map_policy(&changed, "local").is_err(),
            "{field}"
        );
    }
    let mut next = registry.clone();
    next.revision += 1;
    let key = next.policies.keys().next().unwrap().clone();
    next.policies
        .get_mut(&key)
        .unwrap()
        .typed_map_manifest_digest = None;
    assert!(registry.validates_successor(&next).is_err());
    next.policies
        .get_mut(&key)
        .unwrap()
        .typed_map_manifest_digest = Some(format!("sha256:{}", "b".repeat(64)));
    assert!(registry.validates_successor(&next).is_err());
    next = registry.clone();
    next.revision += 1;
    next.schema_version = MEMORY_AUTHORIZATION_SCHEMA_V0_1.into();
    assert!(next.validate().is_err());
    next.policies
        .get_mut(&key)
        .unwrap()
        .typed_map_manifest_digest = None;
    assert!(registry.validates_successor(&next).is_err());
    let legacy = next;
    let mut narrowed = legacy.clone();
    narrowed.revision += 1;
    narrowed.schema_version = MEMORY_AUTHORIZATION_SCHEMA.into();
    narrowed
        .policies
        .get_mut(&key)
        .unwrap()
        .typed_map_manifest_digest = Some(map_manifest_digest(&map).unwrap());
    legacy.validates_successor(&narrowed).unwrap();
    assert!(legacy.typed_map_policy(&map, "local").is_err());
    let restored: MemoryAuthorizationRegistry =
        serde_json::from_str(&serde_json::to_string(&narrowed).unwrap()).unwrap();
    restored.typed_map_policy(&map, "local").unwrap();
}

/// Old metadata stays old; a new provider cannot be inferred or substituted during deserialization.
#[test]
fn provider_map_requires_complete_original_provenance_and_rejects_legacy_claims() {
    let map = provider_map();
    let value = serde_json::to_value(&map).unwrap();
    assert_eq!(value["schema"], SPATIAL_MEMORY_SCHEMA_V0_2);
    assert_eq!(
        serde_json::from_value::<MapArtifactManifest>(value.clone()).unwrap(),
        map
    );
    for field in [
        "producer_provider_id",
        "payload_schema",
        "producer_local_system_id",
        "source_task_ref",
        "source_execution_id",
    ] {
        let mut missing = value.clone();
        missing.as_object_mut().unwrap().remove(field);
        assert!(
            serde_json::from_value::<MapArtifactManifest>(missing).is_err(),
            "missing original {field}"
        );
    }
    let mut legacy = value.clone();
    legacy["schema"] = serde_json::json!(SPATIAL_MEMORY_SCHEMA_V0_1);
    assert!(serde_json::from_value::<MapArtifactManifest>(legacy.clone()).is_err());
    for field in ["producer_provider_id", "payload_schema"] {
        legacy.as_object_mut().unwrap().remove(field);
    }
    let historical: MapArtifactManifest = serde_json::from_value(legacy).unwrap();
    assert_eq!(historical.producer_provider_id(), None);
    assert_eq!(historical.payload_schema(), None);
    assert!(
        map.clone()
            .with_producer_provider("other-provider", "source-content/v1")
            .is_err()
    );
    let copied = map.with_artifact(map.artifact().clone()).unwrap();
    assert_eq!(copied.producer_provider_id(), Some("configured-provider"));
}

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
    let map: MapId =
        serde_json::from_str("\"Map_9.release:one-two\"").expect("path-safe map id deserializes");
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
    let prefixed =
        ContentDigest::new(format!("sha256:{}", "b".repeat(64))).expect("prefixed digest is valid");
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
