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
