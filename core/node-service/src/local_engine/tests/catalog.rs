//! Local integration catalog compilation and mapping tests.

use super::*;

/// Catalog compiles multiple local systems and all generic driver configurations.
#[test]
fn compiles_multi_system_generic_catalog() {
    let directory = tempfile::tempdir().expect("temporary directory exists");
    let descriptor = directory.path().join("local.pb");
    std::fs::write(&descriptor, b"descriptor fixture").expect("descriptor writes");
    let catalog = CompiledLocalCatalog::compile(valid_config(descriptor), directory.path())
        .expect("catalog compiles");
    assert_eq!(catalog.local_systems().len(), 2);
    assert_eq!(catalog.connections().len(), 3);
    assert_eq!(catalog.resources()["base"].owner(), "motion");
    assert_eq!(catalog.sensors()["front-camera"].owner(), "perception");
    assert_eq!(
        catalog.capabilities()["mobility.reach_region@v1"].owner(),
        "motion"
    );
    assert_eq!(
        catalog.capabilities()["mobility.reach_region@v1"].artifact_operation(),
        None
    );
}

/// v0.4 requires and compiles one fixed readiness observation per exact contract.
#[test]
fn requires_exact_capability_readiness_in_v0_4() {
    let directory = tempfile::tempdir().expect("temporary directory exists");
    let descriptor = directory.path().join("local.pb");
    std::fs::write(&descriptor, b"descriptor fixture").expect("descriptor writes");
    let mut config = valid_config(descriptor);
    config.schema = CONFIG_SCHEMA_V0_4.to_string();
    assert!(matches!(
        CompiledLocalCatalog::compile(config.clone(), directory.path()),
        Err(CatalogError::Validation { field, .. })
            if field == "capabilities.mobility.reach_region@v1.readiness"
    ));
    config.capabilities[0].readiness = Some(CapabilityReadinessConfig {
        step: WorkflowStepConfig {
            id: "read-readiness".to_string(),
            connection: "motion-http".to_string(),
            operation: LocalOperationConfig::Http {
                method: "GET".to_string(),
                path: "/capabilities/reach-region".to_string(),
            },
            request: RequestMappingConfig::default(),
        },
        state_pointer: "/state".to_string(),
        detail_pointer: Some("/detail".to_string()),
        ready: vec!["READY".to_string()],
        unavailable: vec!["UNAVAILABLE".to_string()],
        case_sensitive: false,
    });

    let mut legacy = config.clone();
    legacy.schema = CONFIG_SCHEMA_V0_3.to_string();
    assert!(matches!(
        CompiledLocalCatalog::compile(legacy, directory.path()),
        Err(CatalogError::Validation { field, .. })
            if field == "capabilities.mobility.reach_region@v1.readiness"
    ));

    let catalog = CompiledLocalCatalog::compile(config, directory.path())
        .expect("v0.4 readiness observation compiles");
    assert!(
        catalog.capabilities()["mobility.reach_region@v1"]
            .readiness()
            .is_some()
    );
}

/// v0.6 maps only fixed-owner, bounded, read-only peer-channel observations.
#[test]
fn compiles_and_maps_peer_channel_observer() {
    let directory = tempfile::tempdir().expect("temporary directory exists");
    let descriptor = directory.path().join("local.pb");
    std::fs::write(&descriptor, b"descriptor fixture").expect("descriptor writes");
    let mut config = valid_config(descriptor);
    config.schema = CONFIG_SCHEMA_V0_6.to_string();
    add_readiness(&mut config);
    config.peer_channel_observers = vec![PeerChannelObserverConfig {
        id: "motion-peers".to_string(),
        owner: "motion".to_string(),
        interval_ms: 1_000,
        valid_for_ms: 3_000,
        step: WorkflowStepConfig {
            id: "observe-peers".to_string(),
            connection: "motion-http".to_string(),
            operation: LocalOperationConfig::Http {
                method: "GET".to_string(),
                path: "/coordination/peers".to_string(),
            },
            request: RequestMappingConfig::default(),
        },
        channels_pointer: "/channels".to_string(),
    }];

    let catalog =
        CompiledLocalCatalog::compile(config, directory.path()).expect("peer observer compiles");
    let observer = &catalog.peer_channel_observers()["motion-peers"];
    let mut context = WorkflowContext::new(serde_json::json!({}));
    context
        .record_step(
            "observe-peers",
            serde_json::json!({
                "channels": [{
                    "group_id": "group-a",
                    "context_id": "guidance",
                    "context_role_id": "dog",
                    "local_system_id": "untrusted-response-owner",
                    "channel_instance_id": "peer-session-a",
                    "profile_id": "guidance-peer",
                    "message_schema": "guidance/v1",
                    "ready": true
                }]
            }),
        )
        .expect("response records");
    let facts = observer.map(&context).expect("response maps");

    assert!(matches!(
        facts.as_slice(),
        [PeerChannelReadinessFact {
            group_id,
            local_system_id,
            valid_for_ms: 3_000,
            ready: true,
            ..
        }] if group_id == "group-a" && local_system_id == "motion"
    ));

    let mut duplicate_context = WorkflowContext::new(serde_json::json!({}));
    let duplicate = serde_json::json!({
        "group_id": "group-a",
        "context_id": "guidance",
        "context_role_id": "dog",
        "channel_instance_id": "peer-session-a",
        "profile_id": "guidance-peer",
        "message_schema": "guidance/v1",
        "ready": true
    });
    duplicate_context
        .record_step(
            "observe-peers",
            serde_json::json!({"channels": [duplicate.clone(), duplicate]}),
        )
        .expect("duplicate response records");
    assert!(matches!(
        observer.map(&duplicate_context),
        Err(MappingError::InvalidFunctionArguments(reason))
            if reason.contains("duplicate logical endpoint")
    ));
}

/// Peer observers reject non-read-only routes and unusable renewal lifetimes at startup.
#[test]
fn rejects_unsafe_peer_channel_observers() {
    let directory = tempfile::tempdir().expect("temporary directory exists");
    let descriptor = directory.path().join("local.pb");
    std::fs::write(&descriptor, b"descriptor fixture").expect("descriptor writes");
    let mut config = valid_config(descriptor);
    config.schema = CONFIG_SCHEMA_V0_6.to_string();
    add_readiness(&mut config);
    config.peer_channel_observers = vec![PeerChannelObserverConfig {
        id: "motion-peers".to_string(),
        owner: "motion".to_string(),
        interval_ms: 1_000,
        valid_for_ms: 500,
        step: WorkflowStepConfig {
            id: "observe-peers".to_string(),
            connection: "motion-http".to_string(),
            operation: LocalOperationConfig::Http {
                method: "POST".to_string(),
                path: "/coordination/peers".to_string(),
            },
            request: RequestMappingConfig::default(),
        },
        channels_pointer: "/channels".to_string(),
    }];

    assert!(matches!(
        CompiledLocalCatalog::compile(config.clone(), directory.path()),
        Err(CatalogError::Validation { field, .. })
            if field == "peer_channel_observers.motion-peers.valid_for_ms"
    ));
    config.peer_channel_observers[0].valid_for_ms = 3_000;
    assert!(matches!(
        CompiledLocalCatalog::compile(config, directory.path()),
        Err(CatalogError::Validation { field, reason })
            if field == "peer_channel_observers.motion-peers.step.operation"
                && reason.contains("must use GET")
    ));
}

/// Artifact operations are typed in v0.3 and rejected under the v0.2 schema.
#[test]
fn gates_typed_capability_artifact_operations_by_schema() {
    let directory = tempfile::tempdir().expect("temporary directory exists");
    let descriptor = directory.path().join("local.pb");
    std::fs::write(&descriptor, b"descriptor fixture").expect("descriptor writes");
    for operation in [
        ArtifactOperationConfig::PrepareOutput,
        ArtifactOperationConfig::Publish,
        ArtifactOperationConfig::Import,
        ArtifactOperationConfig::Verify,
    ] {
        let mut config = valid_config(descriptor.clone());
        config.capabilities[0].artifact_operation = Some(operation);
        assert!(matches!(
            CompiledLocalCatalog::compile(config.clone(), directory.path()),
            Err(CatalogError::Validation { field, .. })
                if field == "capabilities.mobility.reach_region@v1.artifact_operation"
        ));

        config.schema = CONFIG_SCHEMA_V0_3.to_string();
        let catalog = CompiledLocalCatalog::compile(config, directory.path())
            .expect("v0.3 artifact operation compiles");
        assert_eq!(
            catalog.capabilities()["mobility.reach_region@v1"].artifact_operation(),
            Some(operation)
        );
    }
}

/// v0.3 compiles static artifact bindings without changing local workflow ownership.
#[test]
fn compiles_spatial_artifact_bindings() {
    let directory = tempfile::tempdir().expect("temporary directory exists");
    let descriptor = directory.path().join("local.pb");
    std::fs::write(&descriptor, b"descriptor fixture").expect("descriptor writes");
    let mut config = valid_config(descriptor);
    config.artifacts = Some(ArtifactServiceConfig {
        endpoint: "http://127.0.0.1:8090".to_string(),
        cache_directory: PathBuf::from("artifact-cache"),
        max_artifact_bytes: 1024,
        chunk_size_bytes: 64,
        connect_timeout_ms: 1_234,
        read_timeout_ms: 5_678,
        input_bindings: vec![ArtifactInputBindingConfig {
            id: "lab-map-input".to_string(),
            map_id: "lab-map".to_string(),
            revision_id: "r1".to_string(),
            content_digest: Some(format!("sha256:{}", "a".repeat(64))),
            target_path: PathBuf::from("inputs/lab.map"),
        }],
        output_bindings: vec![ArtifactOutputBindingConfig {
            id: "lab-map-output".to_string(),
            map_id: "lab-map".to_string(),
            revision_id: "r2".to_string(),
            source_path: PathBuf::from("outputs/lab.map"),
            media_type: "application/octet-stream".to_string(),
            format_name: "nav2-map-bundle".to_string(),
            format_version: "bundle-v1".to_string(),
            root_frame: "map".to_string(),
            coordinate_convention: "enu".to_string(),
            spatial_anchor_id: "lab-origin".to_string(),
            resolution_meters: Some(0.05),
        }],
    });
    assert!(matches!(
        CompiledLocalCatalog::compile(config.clone(), directory.path()),
        Err(CatalogError::Validation { field, .. }) if field == "artifacts"
    ));
    config.schema = CONFIG_SCHEMA_V0_3.to_string();
    let mut overlapping = config.clone();
    overlapping
        .artifacts
        .as_mut()
        .expect("artifacts are present")
        .output_bindings[0]
        .source_path = PathBuf::from("inputs/lab.map");
    assert!(matches!(
        CompiledLocalCatalog::compile(overlapping, directory.path()),
        Err(CatalogError::Validation { field, .. })
            if field == "artifacts.output_bindings.source_path"
    ));
    let mut invalid_selector = config.clone();
    invalid_selector
        .artifacts
        .as_mut()
        .expect("artifacts are present")
        .input_bindings[0]
        .map_id = "../lab-map".to_string();
    assert!(matches!(
        CompiledLocalCatalog::compile(invalid_selector, directory.path()),
        Err(CatalogError::Validation { field, .. })
            if field == "artifacts.input_bindings.map_id"
    ));
    let mut duplicate_binding = config.clone();
    duplicate_binding
        .artifacts
        .as_mut()
        .expect("artifacts are present")
        .output_bindings[0]
        .id = "lab-map-input".to_string();
    assert!(matches!(
        CompiledLocalCatalog::compile(duplicate_binding, directory.path()),
        Err(CatalogError::Validation { field, .. })
            if field == "artifacts.output_bindings.id"
    ));
    for (field, connect_timeout_ms, read_timeout_ms) in [
        ("artifacts.connect_timeout_ms", 0, 5_678),
        ("artifacts.read_timeout_ms", 1_234, 0),
    ] {
        let mut invalid = config.clone();
        let artifacts = invalid.artifacts.as_mut().expect("artifacts are present");
        artifacts.connect_timeout_ms = connect_timeout_ms;
        artifacts.read_timeout_ms = read_timeout_ms;
        assert!(matches!(
            CompiledLocalCatalog::compile(invalid, directory.path()),
            Err(CatalogError::Validation { field: actual, .. }) if actual == field
        ));
    }
    let catalog =
        CompiledLocalCatalog::compile(config, directory.path()).expect("v0.3 catalog compiles");
    let artifacts = catalog.artifact_service().expect("artifacts are present");
    assert_eq!(artifacts.input_bindings().len(), 1);
    assert_eq!(artifacts.output_bindings().len(), 1);
    assert!(artifacts.cache_directory().ends_with("artifact-cache"));
    assert_eq!(artifacts.connect_timeout_ms(), 1_234);
    assert_eq!(artifacts.read_timeout_ms(), 5_678);
}
