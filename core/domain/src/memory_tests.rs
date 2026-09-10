//! Generic Memory domain invariant tests.

use super::*;

/// Local scope and discoverable visibility are independent and need no shared bytes.
#[test]
fn local_discoverable_memory_can_be_metadata_only() {
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
    assert_eq!(manifest.scope(), &MemoryScope::Local);
    assert_eq!(manifest.visibility(), MemoryVisibility::Discoverable);
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
            local_system_id: LocalSystemId::new("memory").expect("local system id should be valid"),
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
        MemoryScopeLimit::Global,
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
        MemoryScopeLimit::Global,
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

/// Global provider scope admits a live Group without embedding that Group in static config.
#[test]
fn provider_scope_is_not_a_static_group_binding() {
    let provider = MemoryProviderDescriptor::new(
        "experience-provider",
        LocalSystemId::new("memory").expect("local system id should be valid"),
        MemoryKind::Experience,
        MemoryScopeLimit::Global,
        MemoryVisibility::Discoverable,
        "example.experience/v1",
        "application/json",
    )
    .expect("provider should be valid");
    let manifest = MemoryArtifactManifest::new(
        MemorySelector::new(
            MemoryId::new("lesson-a").expect("memory id should be valid"),
            MemoryRevisionId::new("r1").expect("revision id should be valid"),
        ),
        MemoryKind::Experience,
        "experience-provider",
        MemoryOwner::Node {
            node_id: NodeId::new("dog-a").expect("node id should be valid"),
            local_system_id: LocalSystemId::new("memory").expect("local system id should be valid"),
        },
        MemoryScope::ExecutionGroup(
            ExecutionGroupId::new("live-group").expect("group id should be valid"),
        ),
        MemoryVisibility::Discoverable,
        "example.experience/v1",
        "application/json",
        None,
        None,
        None,
        None,
        TimestampMs::new(1),
    )
    .expect("manifest should be valid");

    assert_eq!(provider.max_scope(), MemoryScopeLimit::Global);
    provider
        .admit_manifest(&manifest)
        .expect("any live group remains within the provider upper bound");
}

/// Existing checkpoints normalize a historical provider Group scope without retaining its id.
#[test]
fn provider_scope_migrates_historical_execution_group_encoding() {
    let scope: MemoryScopeLimit = serde_json::from_value(serde_json::json!({
        "kind": "execution_group",
        "execution_group_id": "legacy-group"
    }))
    .expect("historical provider scope should migrate");

    assert_eq!(scope, MemoryScopeLimit::Local);
    assert_eq!(
        serde_json::to_value(scope).expect("current scope should serialize"),
        serde_json::json!({"kind": "local"})
    );
}

/// Consumer admission checks exchange semantics without claiming remote producer ownership.
#[test]
fn provider_admits_compatible_remote_import() {
    let provider = MemoryProviderDescriptor::new(
        "consumer",
        LocalSystemId::new("memory-b").expect("local system id should be valid"),
        MemoryKind::Experience,
        MemoryScopeLimit::Global,
        MemoryVisibility::Exchangeable,
        "example.experience/v1",
        "application/json",
    )
    .expect("provider should be valid");
    let manifest = MemoryArtifactManifest::new(
        MemorySelector::new(
            MemoryId::new("lesson-a").expect("memory id should be valid"),
            MemoryRevisionId::new("r1").expect("revision id should be valid"),
        ),
        MemoryKind::Experience,
        "producer",
        MemoryOwner::Node {
            node_id: NodeId::new("dog-a").expect("node id should be valid"),
            local_system_id: LocalSystemId::new("memory-a")
                .expect("local system id should be valid"),
        },
        MemoryScope::Global,
        MemoryVisibility::Exchangeable,
        "example.experience/v1",
        "application/json",
        Some(MemoryArtifactRef::new(
            ContentDigest::new("a".repeat(64)).expect("digest should be valid"),
            1,
        )),
        None,
        None,
        None,
        TimestampMs::new(1),
    )
    .expect("manifest should be valid");

    provider
        .admit_import(
            &manifest,
            &NodeId::new("dog-b").expect("consumer node id should be valid"),
        )
        .expect("compatible remote memory should be admitted");
}

/// Local scope cannot cross the producer Node boundary even through an exchangeable provider.
#[test]
fn provider_rejects_cross_node_local_import() {
    let provider = MemoryProviderDescriptor::new(
        "consumer",
        LocalSystemId::new("memory-b").expect("local system id should be valid"),
        MemoryKind::Experience,
        MemoryScopeLimit::Global,
        MemoryVisibility::Exchangeable,
        "example.experience/v1",
        "application/json",
    )
    .expect("provider should be valid");
    let manifest = MemoryArtifactManifest::new(
        MemorySelector::new(
            MemoryId::new("local-lesson").expect("memory id should be valid"),
            MemoryRevisionId::new("r1").expect("revision id should be valid"),
        ),
        MemoryKind::Experience,
        "producer",
        MemoryOwner::Node {
            node_id: NodeId::new("dog-a").expect("producer node id should be valid"),
            local_system_id: LocalSystemId::new("memory-a")
                .expect("local system id should be valid"),
        },
        MemoryScope::Local,
        MemoryVisibility::Exchangeable,
        "example.experience/v1",
        "application/json",
        Some(MemoryArtifactRef::new(
            ContentDigest::new("a".repeat(64)).expect("digest should be valid"),
            1,
        )),
        None,
        None,
        None,
        TimestampMs::new(1),
    )
    .expect("manifest should be valid");

    assert!(matches!(
        provider.admit_import(
            &manifest,
            &NodeId::new("dog-b").expect("consumer node id should be valid")
        ),
        Err(DomainError::InvalidMemory { .. })
    ));
}

/// Local exchange may change provider placement while remaining on the semantic owner Node.
#[test]
fn provider_admits_same_node_local_import_into_another_provider() {
    let owner_node = NodeId::new("dog-a").expect("owner node id should be valid");
    let provider = MemoryProviderDescriptor::new(
        "consumer",
        LocalSystemId::new("memory-b").expect("local system id should be valid"),
        MemoryKind::Experience,
        MemoryScopeLimit::Global,
        MemoryVisibility::Exchangeable,
        "example.experience/v1",
        "application/json",
    )
    .expect("provider should be valid");
    let manifest = MemoryArtifactManifest::new(
        MemorySelector::new(
            MemoryId::new("local-lesson").expect("memory id should be valid"),
            MemoryRevisionId::new("r1").expect("revision id should be valid"),
        ),
        MemoryKind::Experience,
        "producer",
        MemoryOwner::Node {
            node_id: owner_node.clone(),
            local_system_id: LocalSystemId::new("memory-a")
                .expect("local system id should be valid"),
        },
        MemoryScope::Local,
        MemoryVisibility::Exchangeable,
        "example.experience/v1",
        "application/json",
        Some(MemoryArtifactRef::new(
            ContentDigest::new("a".repeat(64)).expect("digest should be valid"),
            1,
        )),
        None,
        None,
        None,
        TimestampMs::new(1),
    )
    .expect("manifest should be valid");

    provider
        .admit_import(&manifest, &owner_node)
        .expect("another provider on the owner Node may import Local Memory");
}
