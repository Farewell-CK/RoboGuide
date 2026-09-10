//! Local catalog Memory provider compilation tests.

use super::*;

/// v0.6 compiles the Node ledger/handoff root and all fixed EAIOS Memory workflows.
#[test]
fn compiles_v0_6_memory_provider_workflows() {
    let directory = tempfile::tempdir().expect("temporary directory exists");
    let mut config = valid_config(directory.path().join("unused.pb"));
    config.schema = CONFIG_SCHEMA_V0_6.to_string();
    add_readiness(&mut config);
    let workflow = |id: &str, path: &str| MemoryWorkflowConfig {
        steps: vec![WorkflowStepConfig {
            id: id.to_string(),
            connection: "motion-http".to_string(),
            operation: LocalOperationConfig::Http {
                method: "POST".to_string(),
                path: path.to_string(),
            },
            request: RequestMappingConfig::default(),
        }],
        manifests_pointer: None,
        artifact_path_pointer: None,
    };
    config.memory_providers = vec![MemoryProviderConfig {
        id: "maps".to_string(),
        owner: "motion".to_string(),
        kind: "spatial".to_string(),
        scope: "global".to_string(),
        visibility: "exchangeable".to_string(),
        payload_schema: "example.map/v1".to_string(),
        media_type: "application/octet-stream".to_string(),
        storage_directory: Some(PathBuf::from("maps")),
        discover: Some(MemoryWorkflowConfig {
            manifests_pointer: Some("/steps/discover/manifests".to_string()),
            ..workflow("discover", "/memory/discover")
        }),
        export: Some(MemoryWorkflowConfig {
            artifact_path_pointer: Some("/steps/export/path".to_string()),
            ..workflow("export", "/memory/export")
        }),
        import: Some(workflow("import", "/memory/import")),
    }];
    let catalog = CompiledLocalCatalog::compile(config, directory.path())
        .expect("v0.6 provider workflows compile");
    let provider = &catalog.memory_providers()["maps"];
    assert!(provider.discover().is_some());
    assert!(provider.export().is_some());
    assert!(provider.import().is_some());
    assert_eq!(
        provider.storage_directory(),
        directory.path().join("state/maps")
    );
}

/// v0.5 remains metadata-only and rejects executable provider workflow declarations.
#[test]
fn v0_5_rejects_memory_provider_workflows() {
    let directory = tempfile::tempdir().expect("temporary directory exists");
    let mut config = valid_config(directory.path().join("unused.pb"));
    config.schema = CONFIG_SCHEMA_V0_5.to_string();
    add_readiness(&mut config);
    config.memory_providers = vec![MemoryProviderConfig {
        id: "lessons".to_string(),
        owner: "motion".to_string(),
        kind: "experience".to_string(),
        scope: "global".to_string(),
        visibility: "discoverable".to_string(),
        payload_schema: "example.experience/v1".to_string(),
        media_type: "application/json".to_string(),
        storage_directory: None,
        discover: Some(MemoryWorkflowConfig {
            steps: vec![WorkflowStepConfig {
                id: "discover".to_string(),
                connection: "motion-http".to_string(),
                operation: LocalOperationConfig::Http {
                    method: "GET".to_string(),
                    path: "/memory".to_string(),
                },
                request: RequestMappingConfig::default(),
            }],
            manifests_pointer: Some("/steps/discover/manifests".to_string()),
            artifact_path_pointer: None,
        }),
        export: None,
        import: None,
    }];
    assert!(matches!(
        CompiledLocalCatalog::compile(config, directory.path()),
        Err(CatalogError::Validation { field, .. }) if field == "memory_providers.lessons"
    ));
}

/// Provider-local backends cannot overlap and thereby cross semantic ownership boundaries.
#[test]
fn rejects_nested_memory_provider_storage_roots() {
    let directory = tempfile::tempdir().expect("temporary directory exists");
    let mut config = valid_config(directory.path().join("unused.pb"));
    config.schema = CONFIG_SCHEMA_V0_6.to_string();
    add_readiness(&mut config);
    let provider = |id: &str, storage: &str| MemoryProviderConfig {
        id: id.to_string(),
        owner: "motion".to_string(),
        kind: "semantic".to_string(),
        scope: "global".to_string(),
        visibility: "discoverable".to_string(),
        payload_schema: "example.semantic/v1".to_string(),
        media_type: "application/json".to_string(),
        storage_directory: Some(PathBuf::from(storage)),
        discover: None,
        export: None,
        import: None,
    };
    config.memory_providers = vec![
        provider("semantic-a", "memory"),
        provider("semantic-b", "memory/nested"),
    ];

    assert!(matches!(
        CompiledLocalCatalog::compile(config, directory.path()),
        Err(CatalogError::Validation { field, .. })
            if field == "memory_providers.semantic-b.storage_directory"
    ));
}

/// Generic provider configuration cannot claim the strongly verified Spatial map schema.
#[test]
fn rejects_typed_spatial_schema_in_generic_memory_provider() {
    let directory = tempfile::tempdir().expect("temporary directory exists");
    let mut config = valid_config(directory.path().join("unused.pb"));
    config.schema = CONFIG_SCHEMA_V0_6.to_string();
    add_readiness(&mut config);
    config.memory_providers = vec![MemoryProviderConfig {
        id: "maps".to_string(),
        owner: "motion".to_string(),
        kind: "spatial".to_string(),
        scope: "global".to_string(),
        visibility: "exchangeable".to_string(),
        payload_schema: domain::SPATIAL_MEMORY_SCHEMA_V0_1.to_string(),
        media_type: "application/octet-stream".to_string(),
        storage_directory: None,
        discover: None,
        export: None,
        import: None,
    }];

    assert!(matches!(
        CompiledLocalCatalog::compile(config, directory.path()),
        Err(CatalogError::Validation { field, .. })
            if field == "memory_providers.maps.payload_schema"
    ));
}

/// The reference backend is local, idempotent, and queryable without central replication.
#[test]
fn filesystem_memory_ledger_round_trips_jsonl_metadata() {
    use crate::{FilesystemMemoryLedger, LocalMemoryLedger, MemoryQuery};
    use domain::{
        ContentDigest, LocalSystemId, MemoryArtifactManifest, MemoryArtifactRef, MemoryId,
        MemoryKind, MemoryOwner, MemoryRevisionId, MemoryScope, MemorySelector, MemoryVisibility,
        NodeId, TimestampMs,
    };

    let directory = tempfile::tempdir().expect("temporary directory exists");
    let descriptor = CompiledMemoryProvider {
        id: "maps".to_string(),
        owner: "maps".to_string(),
        kind: "spatial".to_string(),
        scope: "global".to_string(),
        visibility: "exchangeable".to_string(),
        payload_schema: "example.map/v1".to_string(),
        media_type: "application/octet-stream".to_string(),
        operational: true,
        storage_directory: directory.path().join("provider"),
        discover: None,
        export: None,
        import: None,
    };
    let ledger = FilesystemMemoryLedger::open(descriptor.clone()).expect("ledger opens");
    let manifest = MemoryArtifactManifest::new(
        MemorySelector::new(
            MemoryId::new("map-a").expect("memory id is valid"),
            MemoryRevisionId::new("r1").expect("revision id is valid"),
        ),
        MemoryKind::Spatial,
        "maps",
        MemoryOwner::Node {
            node_id: NodeId::new("node-a").expect("node id is valid"),
            local_system_id: LocalSystemId::new("maps").expect("system id is valid"),
        },
        MemoryScope::Global,
        MemoryVisibility::Exchangeable,
        "example.map/v1",
        "application/octet-stream",
        Some(MemoryArtifactRef::new(
            ContentDigest::new("a".repeat(64)).expect("digest is valid"),
            1,
        )),
        None,
        None,
        None,
        TimestampMs::new(1),
    )
    .expect("manifest is valid");
    ledger.record_export(&manifest).expect("manifest records");
    ledger
        .record_export(&manifest)
        .expect("same record is idempotent");
    let found = ledger
        .discover_recorded(&MemoryQuery {
            selector: Some(manifest.selector().clone()),
            kind: Some(MemoryKind::Spatial),
            scope: Some(MemoryScope::Global),
            provider_id: Some("maps".to_string()),
            payload_schema: Some("example.map/v1".to_string()),
            owner: Some(manifest.owner().clone()),
        })
        .expect("recorded manifest discovers");
    assert_eq!(found, vec![manifest.clone()]);
    std::fs::remove_file(descriptor.storage_directory().join("manifests.jsonl"))
        .expect("derived index removes");
    drop(ledger);
    let ledger_root = descriptor.storage_directory().to_path_buf();
    let reopened = FilesystemMemoryLedger::open(descriptor).expect("ledger reopens");
    assert_eq!(
        reopened
            .discover_recorded(&MemoryQuery::default())
            .expect("index rebuilds from ledger objects"),
        vec![manifest]
    );

    let reference_manifest = MemoryArtifactManifest::new(
        MemorySelector::new(
            MemoryId::new("map-b").expect("memory id is valid"),
            MemoryRevisionId::new("r1").expect("revision id is valid"),
        ),
        MemoryKind::Spatial,
        "remote-maps",
        MemoryOwner::Node {
            node_id: NodeId::new("node-b").expect("node id is valid"),
            local_system_id: LocalSystemId::new("maps").expect("system id is valid"),
        },
        MemoryScope::Global,
        MemoryVisibility::Exchangeable,
        "example.map/v1",
        "application/octet-stream",
        Some(MemoryArtifactRef::new(
            ContentDigest::new("b".repeat(64)).expect("digest is valid"),
            1,
        )),
        None,
        None,
        None,
        TimestampMs::new(2),
    )
    .expect("reference manifest is valid");
    assert!(matches!(
        reopened.record_import(&reference_manifest, None),
        Err(crate::MemoryLedgerError::Configuration(_))
    ));
    let staged = directory.path().join("staged-map.bin");
    std::fs::write(&staged, b"b").expect("staged reference bytes write");
    reopened
        .record_import(&reference_manifest, Some(&staged))
        .expect("workflow-free reference backend imports bytes");
    let reference_blob = format!(
        "{}.blob",
        reference_manifest
            .artifact()
            .expect("reference manifest carries bytes")
            .content_digest()
            .as_str()
            .replace(':', "_")
    );
    assert_eq!(
        std::fs::read(ledger_root.join(reference_blob)).expect("reference bytes remain available"),
        b"b"
    );
}
