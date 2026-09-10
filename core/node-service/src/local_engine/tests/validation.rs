//! Local integration catalog rejection tests.

use super::*;

/// Artifact bindings reject traversal before any local workflow can use a path.
#[test]
fn rejects_spatial_artifact_path_traversal() {
    let directory = tempfile::tempdir().expect("temporary directory exists");
    let descriptor = directory.path().join("local.pb");
    std::fs::write(&descriptor, b"descriptor fixture").expect("descriptor writes");
    let mut config = valid_config(descriptor);
    config.schema = CONFIG_SCHEMA_V0_3.to_string();
    config.artifacts = Some(ArtifactServiceConfig {
        endpoint: "http://127.0.0.1:8090".to_string(),
        cache_directory: PathBuf::from("artifact-cache"),
        max_artifact_bytes: 1024,
        chunk_size_bytes: 64,
        connect_timeout_ms: 5_000,
        read_timeout_ms: 30_000,
        input_bindings: vec![ArtifactInputBindingConfig {
            id: "lab-map-input".to_string(),
            map_id: "lab-map".to_string(),
            revision_id: "r1".to_string(),
            content_digest: None,
            target_path: PathBuf::from("../escape.map"),
        }],
        output_bindings: Vec::new(),
    });
    assert!(matches!(
        CompiledLocalCatalog::compile(config, directory.path()),
        Err(CatalogError::Validation { field, .. })
            if field == "artifacts.input_bindings.target_path"
    ));
}

/// Artifact bindings reject paths that resolve to the cache root itself.
#[test]
fn rejects_empty_or_current_directory_artifact_paths() {
    let directory = tempfile::tempdir().expect("temporary directory exists");
    let descriptor = directory.path().join("local.pb");
    std::fs::write(&descriptor, b"descriptor fixture").expect("descriptor writes");
    for invalid_path in [PathBuf::new(), PathBuf::from(".")] {
        let mut config = valid_config(descriptor.clone());
        config.schema = CONFIG_SCHEMA_V0_3.to_string();
        config.artifacts = Some(ArtifactServiceConfig {
            endpoint: "http://127.0.0.1:8090".to_string(),
            cache_directory: PathBuf::from("artifact-cache"),
            max_artifact_bytes: 1024,
            chunk_size_bytes: 64,
            connect_timeout_ms: 5_000,
            read_timeout_ms: 30_000,
            input_bindings: vec![ArtifactInputBindingConfig {
                id: "lab-map-input".to_string(),
                map_id: "lab-map".to_string(),
                revision_id: "r1".to_string(),
                content_digest: None,
                target_path: invalid_path,
            }],
            output_bindings: Vec::new(),
        });
        assert!(matches!(
            CompiledLocalCatalog::compile(config, directory.path()),
            Err(CatalogError::Validation { field, .. })
                if field == "artifacts.input_bindings.target_path"
        ));
    }
}

/// Duplicate capability contracts fail rather than creating ambiguous owners.
#[test]
fn rejects_duplicate_capability_owner() {
    let directory = tempfile::tempdir().expect("temporary directory exists");
    let descriptor = directory.path().join("local.pb");
    std::fs::write(&descriptor, b"descriptor fixture").expect("descriptor writes");
    let mut config = valid_config(descriptor);
    let mut duplicate = config.capabilities[0].clone();
    duplicate.owner = "perception".to_string();
    config.capabilities.push(duplicate);
    assert!(matches!(
        CompiledLocalCatalog::compile(config, directory.path()),
        Err(CatalogError::Validation { field, .. }) if field == "capabilities.contract"
    ));
}

/// Remote local endpoints and absent descriptor sets fail before runtime.
#[test]
fn rejects_remote_endpoint_and_missing_descriptor() {
    let directory = tempfile::tempdir().expect("temporary directory exists");
    let missing = directory.path().join("missing.pb");
    let mut remote = valid_config(missing.clone());
    if let ConnectionConfig::Http { endpoint, .. } = &mut remote.connections[0] {
        *endpoint = "http://198.51.100.7:8100".to_string();
    }
    assert!(matches!(
        CompiledLocalCatalog::compile(remote, directory.path()),
        Err(CatalogError::Validation { field, .. })
            if field == "connections.motion-http.endpoint"
    ));
    let mut missing_descriptor = valid_config(missing.clone());
    if let ConnectionConfig::Grpc {
        descriptor_set,
        reflection,
        ..
    } = &mut missing_descriptor.connections[1]
    {
        *descriptor_set = Some(missing.clone());
        *reflection = false;
    }
    assert!(matches!(
        CompiledLocalCatalog::compile(missing_descriptor, directory.path()),
        Err(CatalogError::Validation { field, .. }) if field.contains("descriptor_set")
    ));
}

/// Descriptor-backed gRPC routes are checked offline for service, method, and stream shape.
#[test]
fn validates_descriptor_backed_grpc_routes_offline() {
    use prost::Message;
    use prost_types::{
        DescriptorProto, FileDescriptorProto, FileDescriptorSet, MethodDescriptorProto,
        ServiceDescriptorProto,
    };

    let descriptor_set = FileDescriptorSet {
        file: vec![FileDescriptorProto {
            name: Some("navigation.proto".to_string()),
            package: Some("local".to_string()),
            syntax: Some("proto3".to_string()),
            message_type: vec![
                DescriptorProto {
                    name: Some("Request".to_string()),
                    ..DescriptorProto::default()
                },
                DescriptorProto {
                    name: Some("Response".to_string()),
                    ..DescriptorProto::default()
                },
            ],
            service: vec![ServiceDescriptorProto {
                name: Some("Navigation".to_string()),
                method: vec![MethodDescriptorProto {
                    name: Some("GetStatus".to_string()),
                    input_type: Some(".local.Request".to_string()),
                    output_type: Some(".local.Response".to_string()),
                    ..MethodDescriptorProto::default()
                }],
                ..ServiceDescriptorProto::default()
            }],
            ..FileDescriptorProto::default()
        }],
    }
    .encode_to_vec();
    let directory = tempfile::tempdir().expect("temporary directory exists");
    let descriptor_path = directory.path().join("navigation.pb");
    std::fs::write(&descriptor_path, descriptor_set).expect("descriptor writes");
    let mut config = valid_config(descriptor_path.clone());
    if let ConnectionConfig::Grpc {
        descriptor_set,
        reflection,
        ..
    } = &mut config.connections[1]
    {
        *descriptor_set = Some(descriptor_path.clone());
        *reflection = false;
    }
    CompiledLocalCatalog::compile(config.clone(), directory.path())
        .expect("descriptor-backed route compiles");

    if let LocalOperationConfig::GrpcUnary { method, .. } =
        &mut config.capabilities[0].workflow.status[0].operation
    {
        *method = "Missing".to_string();
    }
    assert!(matches!(
        CompiledLocalCatalog::compile(config, directory.path()),
        Err(CatalogError::Validation { field, reason })
            if field == "workflow.read-state.operation"
                && reason.contains("absent from the descriptor set")
    ));
}

/// Runtime invocation values populate only request data and cannot alter fixed routing.
#[test]
fn rendered_request_preserves_fixed_endpoint_and_method() {
    let directory = tempfile::tempdir().expect("temporary directory exists");
    let descriptor = directory.path().join("local.pb");
    std::fs::write(&descriptor, b"descriptor fixture").expect("descriptor writes");
    let mut config = valid_config(descriptor);
    config.capabilities[0].workflow.execute[0].request = RequestMappingConfig {
        base: serde_json::json!({}),
        bindings: vec![RequestBindingConfig {
            target: "/region".to_string(),
            value: ValueExpressionConfig::Pointer {
                pointer: "/invocation/parameters/region".to_string(),
            },
        }],
    };
    let catalog =
        CompiledLocalCatalog::compile(config, directory.path()).expect("catalog compiles");
    let context = WorkflowContext::new(serde_json::json!({
        "parameters": {
            "region": "http://remote.example/replace-route"
        }
    }));
    let request = catalog.capabilities()["mobility.reach_region@v1"]
        .workflow()
        .execute()[0]
        .render(&catalog, &context)
        .expect("request renders");
    match request {
        CompiledDriverRequest::Http {
            endpoint,
            method,
            path,
            body,
            ..
        } => {
            assert_eq!(endpoint, "http://127.0.0.1:8100");
            assert_eq!(method, "POST");
            assert_eq!(path, "/navigation/reach");
            assert_eq!(body["region"], "http://remote.example/replace-route");
        }
        _ => panic!("HTTP workflow renders an HTTP request"),
    }
}

/// Cancel submission does not affect state projection until status reports cancellation.
#[test]
fn maps_cancelled_only_from_status_fact() {
    let directory = tempfile::tempdir().expect("temporary directory exists");
    let descriptor = directory.path().join("local.pb");
    std::fs::write(&descriptor, b"descriptor fixture").expect("descriptor writes");
    let catalog = CompiledLocalCatalog::compile(valid_config(descriptor), directory.path())
        .expect("catalog compiles");
    let workflow = catalog.capabilities()["mobility.reach_region@v1"].workflow();
    let mut context = WorkflowContext::new(serde_json::json!({}));
    context
        .record_step("request-cancel", serde_json::json!({ "accepted": true }))
        .expect("cancel response records");
    assert!(workflow.map_execution_state(&context).is_err());
    context
        .record_step(
            "read-state",
            serde_json::json!({ "state": "CANCELED", "detail": "stopped" }),
        )
        .expect("status records");
    assert_eq!(
        workflow.map_execution_state(&context).expect("state maps"),
        MappedExecutionFact {
            phase: MappedExecutionPhase::Cancelled,
            reason: Some("stopped".to_string()),
        }
    );
}

/// Unknown local status values fail closed instead of being treated as terminal success.
#[test]
fn rejects_unknown_execution_state_mapping() {
    let directory = tempfile::tempdir().expect("temporary directory exists");
    let descriptor = directory.path().join("local.pb");
    std::fs::write(&descriptor, b"descriptor fixture").expect("descriptor writes");
    let catalog = CompiledLocalCatalog::compile(valid_config(descriptor), directory.path())
        .expect("catalog compiles");
    let workflow = catalog.capabilities()["mobility.reach_region@v1"].workflow();
    let mut context = WorkflowContext::new(serde_json::json!({}));
    context
        .record_step(
            "read-state",
            serde_json::json!({ "state": "VENDOR_UNKNOWN" }),
        )
        .expect("status records");
    assert!(matches!(
        workflow.map_execution_state(&context),
        Err(MappingError::MissingSource(reason))
            if reason.contains("unmapped state")
    ));
}

/// Startup validation rejects handles and requests that depend on unavailable facts.
#[test]
fn rejects_unavailable_workflow_sources() {
    let directory = tempfile::tempdir().expect("temporary directory exists");
    let descriptor = directory.path().join("local.pb");
    std::fs::write(&descriptor, b"descriptor fixture").expect("descriptor writes");
    let mut invalid_handle = valid_config(descriptor.clone());
    invalid_handle.capabilities[0].workflow.local_handle = ValueExpressionConfig::Constant {
        value: serde_json::json!("shared-handle"),
    };
    assert!(matches!(
        CompiledLocalCatalog::compile(invalid_handle, directory.path()),
        Err(CatalogError::Validation { field, .. }) if field == "workflow.local_handle"
    ));

    let mut future_step = valid_config(descriptor);
    future_step.capabilities[0].workflow.execute[0].request = RequestMappingConfig {
        base: serde_json::json!({}),
        bindings: vec![RequestBindingConfig {
            target: "/value".to_string(),
            value: ValueExpressionConfig::Pointer {
                pointer: "/steps/read-state/value".to_string(),
            },
        }],
    };
    assert!(matches!(
        CompiledLocalCatalog::compile(future_step, directory.path()),
        Err(CatalogError::Validation { field, .. }) if field.contains("workflow.execute")
    ));
}
