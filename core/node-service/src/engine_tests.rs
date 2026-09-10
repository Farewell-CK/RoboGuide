//! Local integration engine directive and Memory boundary tests.

use super::*;

/// Builds the canonical JSON shape used by the durable execution journal.
fn invocation(parameters: serde_json::Value) -> serde_json::Value {
    serde_json::json!({
        "mission_id": "mission-a",
        "task_id": "task-a",
        "group_id": "group-a",
        "role_id": "role-a",
        "capability_contract": "spatial.map.import@v0",
        "parameters": parameters,
        "resource_ids": []
    })
}

/// Explicit operations select exactly one side of a static artifact binding.
#[test]
fn parses_explicit_artifact_operations() {
    for (wire, expected) in [
        ("prepare-output", ArtifactOperation::PrepareOutput),
        ("publish", ArtifactOperation::Publish),
        ("import", ArtifactOperation::Import),
        ("verify", ArtifactOperation::Verify),
    ] {
        let value = invocation(serde_json::json!({
            "artifact_slot": "lab-r1",
            "artifact_operation": wire,
            "map_id": "lab",
            "revision_id": "r1",
            "spatial_anchor_id": "lab-origin"
        }));
        let directive = artifact_directive(&value, Some(expected))
            .expect("directive parses")
            .expect("directive exists");
        assert_eq!(directive.slot, "lab-r1");
        assert_eq!(directive.operation, expected);
    }
}

/// A slot alone cannot implicitly activate both input and output behavior.
#[test]
fn rejects_implicit_or_incomplete_artifact_directives() {
    let slot_only = invocation(serde_json::json!({"artifact_slot": "lab-r1"}));
    assert!(matches!(
        artifact_directive(&slot_only, Some(ArtifactOperation::Import)),
        Err(EngineError::Protocol(_))
    ));
    let operation_only = invocation(serde_json::json!({"artifact_operation": "import"}));
    assert!(matches!(
        artifact_directive(&operation_only, Some(ArtifactOperation::Import)),
        Err(EngineError::Protocol(_))
    ));
    let unknown = invocation(serde_json::json!({
        "artifact_slot": "lab-r1",
        "artifact_operation": "auto",
        "map_id": "lab",
        "revision_id": "r1",
        "spatial_anchor_id": "lab-origin"
    }));
    assert!(matches!(
        artifact_directive(&unknown, Some(ArtifactOperation::Import)),
        Err(EngineError::Protocol(_))
    ));
    let wrong_operation = invocation(serde_json::json!({
        "artifact_slot": "lab-r1",
        "artifact_operation": "publish",
        "map_id": "lab",
        "revision_id": "r1",
        "spatial_anchor_id": "lab-origin"
    }));
    assert!(matches!(
        artifact_directive(&wrong_operation, Some(ArtifactOperation::Import)),
        Err(EngineError::Protocol(_))
    ));
}

/// Canonical selectors cannot silently diverge from deployment-owned binding metadata.
#[test]
fn validates_artifact_selector_against_static_binding() {
    let value = invocation(serde_json::json!({
        "map_id": "lab",
        "revision_id": "r1",
        "artifact_slot": "lab-r1",
        "artifact_operation": "import",
        "spatial_anchor_id": "lab-origin"
    }));
    let directive = artifact_directive(&value, Some(ArtifactOperation::Import))
        .expect("directive parses")
        .expect("directive exists");
    validate_artifact_binding_reference(directive, "lab", "r1", None)
        .expect("matching selector is accepted");
    assert!(matches!(
        validate_artifact_binding_reference(directive, "other", "r1", None),
        Err(EngineError::Protocol(_))
    ));
    assert!(matches!(
        validate_artifact_binding_reference(directive, "lab", "r1", Some("different-origin")),
        Err(EngineError::Protocol(_))
    ));
}

/// An execution without artifact parameters remains a normal generic workflow.
#[test]
fn leaves_non_artifact_invocations_unchanged() {
    let value = invocation(serde_json::json!({"distance": 1}));
    assert_eq!(artifact_directive(&value, None).expect("parses"), None);
}

/// Capabilities without an artifact contract reject every artifact-shaped parameter.
#[test]
fn rejects_artifact_parameters_for_generic_capability() {
    let value = invocation(serde_json::json!({"map_id": "lab"}));
    assert!(matches!(
        artifact_directive(&value, None),
        Err(EngineError::Protocol(_))
    ));
}

/// Transient artifact HTTP statuses retain the recovery fence after physical completion.
#[test]
fn classifies_retryable_artifact_statuses_as_ambiguous() {
    for status in [408, 425, 429, 500, 503, 599] {
        let error = EngineError::Artifact(ArtifactError::Status {
            status: reqwest::StatusCode::from_u16(status).expect("status is valid"),
            endpoint: "http://artifact.test".to_string(),
        });
        assert!(!artifact_error_is_deterministic(&error));
    }
}

/// Conclusive client rejections remain deterministic artifact completion failures.
#[test]
fn classifies_nonretryable_artifact_statuses_as_deterministic() {
    for status in [400, 404, 409, 422] {
        let error = EngineError::Artifact(ArtifactError::Status {
            status: reqwest::StatusCode::from_u16(status).expect("status is valid"),
            endpoint: "http://artifact.test".to_string(),
        });
        assert!(artifact_error_is_deterministic(&error));
    }
}

/// ExecutionGroup discovery uses the logical live group identity, not a Node binding.
#[test]
fn memory_query_requires_matching_live_execution_group() {
    let group = domain::ExecutionGroupId::new("group-a").expect("group id is valid");
    let query = MemoryQuery {
        scope: Some(domain::MemoryScope::ExecutionGroup(group)),
        ..MemoryQuery::default()
    };
    assert!(validate_memory_query_scope(&query, &invocation(serde_json::json!({}))).is_ok());
    assert!(matches!(
        validate_memory_query_scope(&query, &serde_json::json!({"group_id": "different-group"})),
        Err(EngineError::Configuration(_))
    ));
}

/// Unfiltered discovery cannot leak Group-scoped metadata outside its logical live context.
#[test]
fn memory_discovery_hides_other_execution_groups() {
    let manifest = MemoryArtifactManifest::new(
        domain::MemorySelector::new(
            domain::MemoryId::new("lesson-a").expect("memory id is valid"),
            domain::MemoryRevisionId::new("r1").expect("revision id is valid"),
        ),
        domain::MemoryKind::Experience,
        "lessons",
        domain::MemoryOwner::Node {
            node_id: NodeId::new("dog-a").expect("node id is valid"),
            local_system_id: LocalSystemId::new("memory").expect("local system id is valid"),
        },
        domain::MemoryScope::ExecutionGroup(
            domain::ExecutionGroupId::new("group-a").expect("group id is valid"),
        ),
        domain::MemoryVisibility::Discoverable,
        "example.experience/v1",
        "application/json",
        None,
        None,
        None,
        None,
        TimestampMs::new(1),
    )
    .expect("manifest is valid");

    assert!(!memory_scope_visible_in_context(
        &manifest,
        &serde_json::json!({})
    ));
    assert!(memory_scope_visible_in_context(
        &manifest,
        &serde_json::json!({"group_id": "group-a"})
    ));
    assert!(!memory_scope_visible_in_context(
        &manifest,
        &serde_json::json!({"group_id": "group-b"})
    ));
}

/// Discovery acceptance returns only provider-supplied publish-eligible manifests.
#[test]
fn provider_discovery_does_not_promote_unreturned_memory() {
    let manifest = MemoryArtifactManifest::new(
        domain::MemorySelector::new(
            domain::MemoryId::new("provider-memory").expect("memory id is valid"),
            domain::MemoryRevisionId::new("r1").expect("revision id is valid"),
        ),
        domain::MemoryKind::Experience,
        "provider",
        domain::MemoryOwner::Node {
            node_id: NodeId::new("dog-a").expect("node id is valid"),
            local_system_id: LocalSystemId::new("memory").expect("local system id is valid"),
        },
        domain::MemoryScope::Global,
        domain::MemoryVisibility::Discoverable,
        "example.experience/v1",
        "application/json",
        None,
        None,
        None,
        None,
        TimestampMs::new(1),
    )
    .expect("manifest is valid");
    let accepted = accept_provider_discovery(
        vec![manifest.clone()],
        &MemoryQuery::default(),
        &serde_json::json!({}),
    )
    .expect("provider response is accepted");
    assert_eq!(accepted, vec![manifest]);
    assert!(
        accept_provider_discovery(Vec::new(), &MemoryQuery::default(), &serde_json::json!({}),)
            .expect("empty provider response is accepted")
            .is_empty()
    );
}

/// An EAIOS response cannot select bytes outside its configured Node handoff root.
#[test]
fn memory_export_path_is_confined_to_node_handoff_root() {
    let directory = tempfile::tempdir().expect("temporary directory exists");
    let handoff_root = directory.path().join("handoff");
    std::fs::create_dir_all(&handoff_root).expect("handoff root creates");
    std::fs::write(handoff_root.join("map.bin"), b"map").expect("EAIOS output writes");
    assert_eq!(
        resolve_memory_export_path(&handoff_root, "map.bin")
            .expect("handoff-relative output resolves"),
        std::fs::canonicalize(handoff_root.join("map.bin")).expect("output canonicalizes")
    );
    assert!(matches!(
        resolve_memory_export_path(&handoff_root, "../outside.bin"),
        Err(EngineError::Memory(_))
    ));
}

/// Ambiguous Local EAIOS transport failure remains retryable instead of terminal rejection.
#[test]
fn memory_import_transport_failure_is_not_conclusive_rejection() {
    assert!(!memory_import_error_is_deterministic(&EngineError::Driver(
        crate::DriverError::Transport("connection closed".to_string())
    )));
    assert!(memory_import_error_is_deterministic(
        &EngineError::Configuration("invalid provider mapping".to_string())
    ));
}
