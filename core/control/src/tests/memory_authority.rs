//! Adversarial authority model checks, with no device identity or physical completion claims.

use super::*;
use domain::{MemoryAccessOperation as Op, MemoryKind, MemoryScope, MemoryVisibility};

/// Builds immutable byte metadata and an actual compatible declaration for authorization inputs.
fn content() -> (
    domain::MemoryArtifactManifest,
    domain::MemoryProviderDescriptor,
) {
    let manifest = domain::MemoryArtifactManifest::new(
        domain::MemorySelector::new(
            domain::MemoryId::new("content").unwrap(),
            domain::MemoryRevisionId::new("revision").unwrap(),
        ),
        MemoryKind::Artifact,
        "producer",
        domain::MemoryOwner::Node {
            node_id: NodeId::new("source").unwrap(),
            local_system_id: domain::LocalSystemId::new("source-local-system").unwrap(),
        },
        MemoryScope::ExecutionGroup(ExecutionGroupId::new("group").unwrap()),
        MemoryVisibility::Exchangeable,
        "opaque/v1",
        "application/octet-stream",
        Some(domain::MemoryArtifactRef::new(
            domain::ContentDigest::new(format!("sha256:{}", "a".repeat(64))).unwrap(),
            1,
        )),
        None,
        None,
        None,
        TimestampMs::new(1),
    )
    .unwrap();
    let provider = domain::MemoryProviderDescriptor::new(
        "consumer",
        domain::LocalSystemId::new("local-system").unwrap(),
        MemoryKind::Artifact,
        domain::MemoryScopeLimit::Global,
        MemoryVisibility::Exchangeable,
        "opaque/v1",
        "application/octet-stream",
    )
    .unwrap();
    (manifest, provider)
}

/// Constructs a pure binding model; production binding and Runtime attempts have separate pipeline tests.
fn authority() -> (
    ControlPlane,
    domain::MemoryArtifactManifest,
    domain::MemoryProviderDescriptor,
    TaskRef,
    RoleId,
    NodeId,
) {
    let mut control = ControlPlane::new();
    let mission = MissionId::new("mission").unwrap();
    let task = TaskRef::new(mission.clone(), TaskId::new("task").unwrap());
    let role = RoleId::new("role").unwrap();
    let node = NodeId::new("arbitrary-current-member").unwrap();
    let mut group = crate::ExecutionGroup::new_mission(
        ExecutionGroupId::new("group").unwrap(),
        mission,
        task.clone(),
    );
    let execution = domain::TaskExecution::new(
        task.clone(),
        domain::CoordinationContextId::new("context").unwrap(),
        BTreeMap::new(),
        BTreeMap::new(),
    )
    .with_assignments(vec![RoleAssignment::new(
        role.clone(),
        node.clone(),
        vec![],
    )]);
    group.task_executions.insert(task.clone(), execution);
    group.refresh_membership().unwrap();
    control.groups.insert(group.group_id().clone(), group);
    let (manifest, provider) = content();
    let policy = domain::MemoryAccessPolicy {
        policy_id: "resource".into(),
        selector: manifest.selector().clone(),
        manifest_digest: domain::memory_manifest_digest(&manifest).unwrap(),
        typed_map_manifest_digest: None,
        tenant_id: "local".into(),
        principals: BTreeMap::new(),
        groups: vec![domain::MemoryGroupPolicy {
            group_id: ExecutionGroupId::new("group").unwrap(),
            operations: BTreeSet::from([Op::Discover, Op::Read, Op::Import]),
            providers: vec![domain::MemoryConsumerPolicy {
                provider_id: Some("consumer".into()),
                local_system_id: Some(provider.local_system_id().clone()),
                kind: MemoryKind::Artifact,
                payload_schema: "opaque/v1".into(),
                media_type: "application/octet-stream".into(),
            }],
        }],
        revoked: false,
    };
    let registry = domain::MemoryAuthorizationRegistry {
        schema_version: domain::MEMORY_AUTHORIZATION_SCHEMA.into(),
        registry_id: "authority".into(),
        revision: 1,
        policies: BTreeMap::from([(policy.key(), policy)]),
    };
    control
        .accept_memory_authorization_registry(registry, 0)
        .unwrap();
    (control, manifest, provider, task, role, node)
}

/// Explicit stopped exchanges require independent child policy, immutable provenance and both epochs.
/// This pure authorization model supplies neither a stop certificate nor receiving byte evidence.
#[test]
fn stopped_group_exchange_requires_exact_both_memberships_and_immutable_child() {
    let (mut control, parent, _, _, _, _) = authority();
    let target_id = ExecutionGroupId::new("target").unwrap();
    let target_mission = MissionId::new("target-mission").unwrap();
    let target = crate::ExecutionGroup::new_mission(
        target_id.clone(),
        target_mission.clone(),
        TaskRef::new(target_mission, TaskId::new("receive").unwrap()),
    );
    control.groups.insert(target_id.clone(), target);
    let mut encoded = serde_json::to_value(&parent).unwrap();
    encoded["selector"]["revision_id"] = "child".into();
    encoded["scope"]["execution_group_id"] = "target".into();
    let child: domain::MemoryArtifactManifest = serde_json::from_value(encoded).unwrap();
    let mut registry = control.memory_authorization_registry().unwrap().clone();
    let parent_key = format!(
        "{}/{}",
        parent.selector().memory_id(),
        parent.selector().revision_id()
    );
    registry
        .policies
        .get_mut(&parent_key)
        .unwrap()
        .principals
        .insert("explicit-exchange".into(), BTreeSet::from([Op::Handoff]));
    let mut policy = registry.policies[&parent_key].clone();
    policy.policy_id = "child-policy".into();
    policy.selector = child.selector().clone();
    policy.manifest_digest = domain::memory_manifest_digest(&child).unwrap();
    policy.principals.clear();
    policy.groups[0].group_id = target_id.clone();
    registry.policies.insert(policy.key(), policy);
    registry.revision = 2;
    control
        .accept_memory_authorization_registry(registry, 1)
        .unwrap();
    let source = ExecutionGroupId::new("group").unwrap();
    let source_version = control
        .group(&source)
        .unwrap()
        .membership_version()
        .unwrap()
        .clone();
    let target_version = control
        .group(&target_id)
        .unwrap()
        .membership_version()
        .unwrap()
        .clone();
    assert!(
        control
            .authorize_stopped_memory_handoff(
                &parent,
                &child,
                &source,
                &source_version,
                &target_version,
                "local",
                "explicit-exchange"
            )
            .is_ok()
    );
    assert!(
        control
            .authorize_memory_handoff(&parent, &child, "local", "explicit-exchange")
            .is_err()
    );
    for (tenant, principal) in [("other", "explicit-exchange"), ("local", "reader")] {
        assert!(
            control
                .authorize_stopped_memory_handoff(
                    &parent,
                    &child,
                    &source,
                    &source_version,
                    &target_version,
                    tenant,
                    principal
                )
                .is_err()
        );
    }
    let mut wrong = target_version.clone();
    wrong.generation += 1;
    assert!(
        control
            .authorize_stopped_memory_handoff(
                &parent,
                &child,
                &source,
                &source_version,
                &wrong,
                "local",
                "explicit-exchange"
            )
            .is_err()
    );
    for (field, value) in [
        ("provider_id", serde_json::json!("replacement")),
        ("source_execution_id", serde_json::json!("replacement")),
        (
            "artifact",
            serde_json::json!({"content_digest":format!("sha256:{}","f".repeat(64)),"byte_size":1}),
        ),
    ] {
        let mut encoded = serde_json::to_value(&child).unwrap();
        encoded[field] = value;
        let changed = serde_json::from_value(encoded).unwrap();
        assert!(
            control
                .authorize_stopped_memory_handoff(
                    &parent,
                    &changed,
                    &source,
                    &source_version,
                    &target_version,
                    "local",
                    "explicit-exchange"
                )
                .is_err()
        );
    }
}

/// Exact binding/provider and original scope are required even when a caller knows every digest.
#[test]
fn memory_content_requires_current_exact_member_and_provider() {
    let (control, manifest, provider, task, role, node) = authority();
    let group = ExecutionGroupId::new("group").unwrap();
    let granted = control
        .authorize_memory_consumer(
            &manifest,
            "local",
            &group,
            &task,
            &role,
            &node,
            &provider,
            Op::Read,
            None,
        )
        .unwrap();
    assert_eq!(
        Some(&granted),
        control.group(&group).unwrap().membership_version()
    );
    assert!(
        control
            .authorize_memory_consumer(
                &manifest,
                "foreign-tenant",
                &group,
                &task,
                &role,
                &node,
                &provider,
                Op::Read,
                None
            )
            .is_err()
    );
    assert!(
        control
            .authorize_memory_consumer(
                &manifest,
                "local",
                &group,
                &task,
                &role,
                &NodeId::new("not-a-member").unwrap(),
                &provider,
                Op::Read,
                None
            )
            .is_err()
    );
    let other = domain::MemoryProviderDescriptor::new(
        "another-consumer",
        provider.local_system_id().clone(),
        MemoryKind::Artifact,
        domain::MemoryScopeLimit::Global,
        MemoryVisibility::Exchangeable,
        "opaque/v1",
        "application/octet-stream",
    )
    .unwrap();
    assert!(
        control
            .authorize_memory_consumer(
                &manifest,
                "local",
                &group,
                &task,
                &role,
                &node,
                &other,
                Op::Read,
                None
            )
            .is_err()
    );
    assert!(
        control
            .authorize_memory_principal(&manifest, "local", "admin", Op::Read)
            .is_err()
    );
}

/// Leaving and later returning to the same Node cannot reactivate an earlier participant credential.
#[test]
fn memory_member_generation_rejects_return_to_previous_owner() {
    let (mut control, manifest, provider, task, role, node) = authority();
    let group_id = ExecutionGroupId::new("group").unwrap();
    let original = control
        .authorize_memory_consumer(
            &manifest,
            "local",
            &group_id,
            &task,
            &role,
            &node,
            &provider,
            Op::Import,
            None,
        )
        .unwrap();
    for owner in [NodeId::new("replacement").unwrap(), node.clone()] {
        let group = control.groups.get_mut(&group_id).unwrap();
        let execution = group.task_executions[&task].with_assignments(vec![RoleAssignment::new(
            role.clone(),
            owner,
            vec![],
        )]);
        group.task_executions.insert(task.clone(), execution);
        group.refresh_membership().unwrap();
    }
    let current = control
        .authorize_memory_consumer(
            &manifest,
            "local",
            &group_id,
            &task,
            &role,
            &node,
            &provider,
            Op::Import,
            None,
        )
        .unwrap();
    assert_eq!(current.bindings_digest, original.bindings_digest);
    assert!(current.generation > original.generation);
    assert!(
        control
            .authorize_memory_consumer(
                &manifest,
                "local",
                &group_id,
                &task,
                &role,
                &node,
                &provider,
                Op::Import,
                Some(&original)
            )
            .is_err()
    );
}

/// Scope handoff requires an explicit principal grant and can only narrow a Group source.
#[test]
fn memory_handoff_requires_explicit_policy_and_rejects_cross_group_broadening() {
    let (mut control, parent, _provider, _task, _role, _node) = authority();
    let child = domain::MemoryArtifactManifest::new(
        domain::MemorySelector::new(
            domain::MemoryId::new("content-child").unwrap(),
            domain::MemoryRevisionId::new("revision").unwrap(),
        ),
        parent.kind(),
        parent.provider_id(),
        parent.owner().clone(),
        MemoryScope::Local,
        MemoryVisibility::Exchangeable,
        parent.payload_schema(),
        parent.media_type(),
        parent.artifact().cloned(),
        None,
        None,
        None,
        TimestampMs::new(2),
    )
    .unwrap();
    assert!(
        control
            .authorize_memory_handoff(&parent, &child, "local", "admin")
            .is_err()
    );

    let mut registry = control.memory_authorization_registry().unwrap().clone();
    registry.revision = 2;
    registry
        .policies
        .values_mut()
        .next()
        .unwrap()
        .principals
        .insert("admin".into(), BTreeSet::from([Op::Handoff]));
    control
        .accept_memory_authorization_registry(registry, 1)
        .unwrap();
    control
        .authorize_memory_handoff(&parent, &child, "local", "admin")
        .expect("explicit Group handoff grant permits narrowing to Local");

    let mut foreign = child.clone();
    let foreign_scope = ExecutionGroupId::new("foreign-group").unwrap();
    let value = serde_json::to_value(&foreign).unwrap();
    let mut value = value;
    value["scope"] = serde_json::json!({
        "kind": "execution_group",
        "execution_group_id": foreign_scope.as_str()
    });
    foreign = serde_json::from_value(value).unwrap();
    assert!(
        control
            .authorize_memory_handoff(&parent, &foreign, "local", "admin")
            .is_err()
    );
}

/// Typed-map handoff requires the complete original Spatial manifest instead of its generic projection.
#[test]
fn typed_map_handoff_rechecks_complete_manifest_policy_and_scope() {
    let (mut control, _, _, _, _, _) = authority();
    let map: domain::MapArtifactManifest = serde_json::from_value(serde_json::json!({
        "schema": "roboguide.map-manifest/v0.2",
        "map_id": "map",
        "revision_id": "source",
        "content_digest": format!("sha256:{}", "a".repeat(64)),
        "byte_size": 1,
        "media_type": "application/vnd.roboguide.spatial-map",
        "format": {"name": "occupancy", "version": "v1"},
        "producer_node_id": "source",
        "producer_local_system_id": "spatial-eaios",
        "producer_provider_id": "map-provider",
        "payload_schema": "roboguide.spatial-file-map/v1",
        "source_mission_id": "mission",
        "source_execution_id": "execution",
        "source_task_ref": {"mission_id": "mission", "task_id": "map-task"},
        "root_frame": "map",
        "coordinate_convention": "enu",
        "resolution_meters": 0.05,
        "spatial_anchor": {"id": "anchor", "kind": "fixed"},
        "parent_revision_id": null,
        "created_at_ms": 1
    }))
    .unwrap();
    let parent = domain::map_memory_manifest(&map).unwrap();
    let mut child_value = serde_json::to_value(&parent).unwrap();
    child_value["selector"]["revision_id"] = "child".into();
    child_value["scope"] = serde_json::json!({"kind": "local"});
    let child: domain::MemoryArtifactManifest = serde_json::from_value(child_value).unwrap();
    let policy = domain::MemoryAccessPolicy {
        policy_id: "typed-map".into(),
        selector: parent.selector().clone(),
        manifest_digest: domain::memory_manifest_digest(&parent).unwrap(),
        typed_map_manifest_digest: Some(domain::map_manifest_digest(&map).unwrap()),
        tenant_id: "local".into(),
        principals: BTreeMap::from([("handoff".into(), BTreeSet::from([Op::Handoff]))]),
        groups: Vec::new(),
        revoked: false,
    };
    let mut registry = control.memory_authorization_registry().unwrap().clone();
    registry.revision = 2;
    registry.policies.insert(policy.key(), policy);
    control
        .accept_memory_authorization_registry(registry, 1)
        .unwrap();
    control
        .authorize_typed_map_handoff(&map, &child, "local", "handoff")
        .unwrap();
    let mut changed = map.clone();
    let encoded = serde_json::to_value(&changed).unwrap();
    changed = serde_json::from_value({
        let mut value = encoded;
        value["root_frame"] = "odom".into();
        value
    })
    .unwrap();
    assert!(
        control
            .authorize_typed_map_handoff(&changed, &child, "local", "handoff")
            .is_err()
    );
}

/// Typed-map stopped handoff keeps both Group generations and the target generic policy gate.
#[test]
fn stopped_typed_map_handoff_requires_source_and_target_memberships() {
    let (mut control, _, _, _, _, _) = authority();
    let mission = MissionId::new("mission").unwrap();
    let target_id = ExecutionGroupId::new("map-target").unwrap();
    let target = crate::ExecutionGroup::new_mission(
        target_id.clone(),
        mission.clone(),
        TaskRef::new(mission.clone(), TaskId::new("receive").unwrap()),
    );
    control.groups.insert(target_id.clone(), target);
    let map: domain::MapArtifactManifest = serde_json::from_value(serde_json::json!({
        "schema": "roboguide.map-manifest/v0.2",
        "map_id": "map-stopped",
        "revision_id": "source",
        "content_digest": format!("sha256:{}", "a".repeat(64)),
        "byte_size": 1,
        "media_type": "application/vnd.roboguide.spatial-map",
        "format": {"name": "occupancy", "version": "v1"},
        "producer_node_id": "source",
        "producer_local_system_id": "spatial-eaios",
        "producer_provider_id": "map-provider",
        "payload_schema": "roboguide.spatial-file-map/v1",
        "source_mission_id": "mission",
        "source_execution_id": "execution",
        "source_task_ref": {"mission_id": "mission", "task_id": "map-task"},
        "root_frame": "map",
        "coordinate_convention": "enu",
        "resolution_meters": 0.05,
        "spatial_anchor": {"id": "anchor", "kind": "fixed"},
        "parent_revision_id": null,
        "created_at_ms": 1
    }))
    .unwrap();
    let parent = domain::map_memory_manifest(&map).unwrap();
    let mut child_value = serde_json::to_value(&parent).unwrap();
    child_value["selector"]["revision_id"] = "child".into();
    child_value["scope"] = serde_json::json!({
        "kind": "execution_group",
        "execution_group_id": target_id.as_str()
    });
    let child: domain::MemoryArtifactManifest = serde_json::from_value(child_value).unwrap();
    let provider = domain::MemoryConsumerPolicy {
        provider_id: Some("map-provider".into()),
        local_system_id: Some(domain::LocalSystemId::new("spatial-eaios").unwrap()),
        kind: MemoryKind::Spatial,
        payload_schema: "roboguide.spatial-file-map/v1".into(),
        media_type: "application/vnd.roboguide.spatial-map".into(),
    };
    let mut registry = control.memory_authorization_registry().unwrap().clone();
    let source_policy = domain::MemoryAccessPolicy {
        policy_id: "typed-stopped-source".into(),
        selector: parent.selector().clone(),
        manifest_digest: domain::memory_manifest_digest(&parent).unwrap(),
        typed_map_manifest_digest: Some(domain::map_manifest_digest(&map).unwrap()),
        tenant_id: "local".into(),
        principals: BTreeMap::from([("handoff".into(), BTreeSet::from([Op::Handoff]))]),
        groups: Vec::new(),
        revoked: false,
    };
    let target_policy = domain::MemoryAccessPolicy {
        policy_id: "typed-stopped-child".into(),
        selector: child.selector().clone(),
        manifest_digest: domain::memory_manifest_digest(&child).unwrap(),
        typed_map_manifest_digest: None,
        tenant_id: "local".into(),
        principals: BTreeMap::new(),
        groups: vec![domain::MemoryGroupPolicy {
            group_id: target_id.clone(),
            operations: BTreeSet::from([Op::Read, Op::Import]),
            providers: vec![provider],
        }],
        revoked: false,
    };
    registry.policies.insert(source_policy.key(), source_policy);
    registry.policies.insert(target_policy.key(), target_policy);
    registry.revision = 2;
    control
        .accept_memory_authorization_registry(registry, 1)
        .unwrap();
    let source = ExecutionGroupId::new("group").unwrap();
    let source_membership = control
        .group(&source)
        .unwrap()
        .membership_version()
        .unwrap()
        .clone();
    let target_membership = control
        .group(&target_id)
        .unwrap()
        .membership_version()
        .unwrap()
        .clone();
    control
        .authorize_stopped_typed_map_handoff(
            &map,
            &child,
            &source,
            &source_membership,
            &target_membership,
            "local",
            "handoff",
        )
        .unwrap();
    let mut stale = target_membership;
    stale.generation += 1;
    assert!(
        control
            .authorize_stopped_typed_map_handoff(
                &map,
                &child,
                &source,
                &source_membership,
                &stale,
                "local",
                "handoff",
            )
            .is_err()
    );
}

/// Revocation changes content eligibility without changing any Group lifecycle, binding or reservation.
#[test]
fn memory_revocation_retains_ownership_and_original_manifest_identity() {
    let (mut control, manifest, provider, task, role, node) = authority();
    let group = ExecutionGroupId::new("group").unwrap();
    let before = control.group(&group).unwrap().clone();
    let mut registry = control.memory_authorization_registry().unwrap().clone();
    registry.revision = 2;
    registry.policies.values_mut().next().unwrap().revoked = true;
    control
        .accept_memory_authorization_registry(registry.clone(), 1)
        .unwrap();
    assert!(
        control
            .authorize_memory_consumer(
                &manifest,
                "local",
                &group,
                &task,
                &role,
                &node,
                &provider,
                Op::Import,
                None
            )
            .is_err()
    );
    assert_eq!(control.group(&group), Some(&before));
    registry.revision = 3;
    registry.policies.values_mut().next().unwrap().revoked = false;
    assert!(
        control
            .accept_memory_authorization_registry(registry, 2)
            .is_err()
    );
    let (mut empty, _, _, _, _, _) = authority();
    empty.groups.clear();
    let restored = ControlPlane::restore(empty.checkpoint()).unwrap();
    assert_eq!(
        restored.memory_authorization_registry(),
        empty.memory_authorization_registry()
    );
    let mut invalid = serde_json::to_value(empty.checkpoint()).unwrap();
    invalid["memory_authorization_history"] = serde_json::json!({});
    assert!(ControlPlane::restore(serde_json::from_value(invalid).unwrap()).is_err());
}

/// Actual certificate expiry, stable rotation and Node registry revocation are independent gates.
#[test]
fn artifact_certificate_authority_preserves_stable_principal_and_node_lifecycle() {
    let mut control = ControlPlane::new();
    let leaf = format!("sha256:{}", "b".repeat(64));
    let next = format!("sha256:{}", "c".repeat(64));
    let mut nodes = domain::NodeIdentityRegistry::new("nodes");
    nodes
        .register("publisher", "tenant", "domain", "b".repeat(64), 0)
        .unwrap();
    control.bootstrap_node_identity_registry(nodes).unwrap();
    let grant = domain::ArtifactCertificateGrant {
        principal_id: "stable-provider".into(),
        tenant_id: "tenant".into(),
        role: domain::ArtifactAccessRole::Writer,
        node_id: Some(NodeId::new("publisher").unwrap()),
        provider_ids: BTreeSet::from(["provider".into()]),
        memory_ids: BTreeSet::new(),
        map_ids: BTreeSet::new(),
        artifact_digests: BTreeSet::new(),
        not_before_ms: 0,
        expires_at_ms: 0,
        revoked: false,
    };
    let mut registry = domain::ArtifactAuthorizationRegistry {
        schema_version: "roboguide.artifact-authorization/v0.1".into(),
        registry_id: "artifact".into(),
        revision: 1,
        grants: BTreeMap::from([(leaf.clone(), grant.clone())]),
    };
    control
        .accept_artifact_authorization_registry(registry.clone(), 0)
        .unwrap();
    assert!(
        control
            .authorize_artifact_certificate(&leaf, 10, TimestampMs::new(1))
            .is_err()
    );
    let mut state = InMemorySharedNodeState::new();
    let mut events = TestEvents;
    control
        .register_node(
            &mut state,
            registration("publisher", CapabilityKind::Compute, "cpu"),
            NodeStatus::new(NodeHealth::Online, TimestampMs::new(0)),
            TimestampMs::new(0),
            &correlation(),
            &mut events,
        )
        .unwrap();
    control
        .register_authenticated_node_session(
            &NodeId::new("publisher").unwrap(),
            "admitted-session",
            1000,
            Some("b".repeat(64)),
            Some(100),
        )
        .unwrap();
    assert_eq!(
        control
            .authorize_artifact_certificate(&leaf, 10, TimestampMs::new(1))
            .unwrap()
            .principal_id,
        "stable-provider"
    );
    assert!(
        control
            .authorize_artifact_certificate(&leaf, 10, TimestampMs::new(10))
            .is_err()
    );
    registry.revision = 2;
    registry.grants.insert(next.clone(), grant);
    let restored_without_session = ControlPlane::restore(control.checkpoint()).unwrap();
    assert!(
        restored_without_session
            .authorize_artifact_certificate(&leaf, 100, TimestampMs::new(2))
            .is_err()
    );
    registry.grants.get_mut(&leaf).unwrap().revoked = true;
    control
        .accept_artifact_authorization_registry(registry.clone(), 1)
        .unwrap();
    assert!(
        control
            .authorize_artifact_certificate(&leaf, 100, TimestampMs::new(2))
            .is_err()
    );
    // Enrolling a data-plane leaf never reenrolls the Node transport or grants device continuity.
    assert!(
        control
            .authorize_artifact_certificate(&next, 100, TimestampMs::new(2))
            .is_err()
    );
    let restored = ControlPlane::restore(control.checkpoint()).unwrap();
    assert_eq!(restored.artifact_authorization_registry(), Some(&registry));
    registry.revision = 3;
    registry.grants.get_mut(&leaf).unwrap().revoked = false;
    assert!(
        control
            .accept_artifact_authorization_registry(registry, 2)
            .is_err()
    );
}
