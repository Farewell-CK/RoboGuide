use super::*;

/// The generic Memory facade retains the fixture consumed by Mission Grounding.
#[test]
fn memory_catalog_matches_mission_grounding_contract_fixture() {
    let fixture: serde_json::Value = serde_json::from_str(include_str!(
        "../../../../../contracts/mission/grounding-context-v0.1/fixtures/memory-catalog.json"
    ))
    .expect("shared Memory catalog fixture is JSON");
    let manifest = MemoryArtifactManifest::new(
        MemorySelector::new(
            MemoryId::new("semantic-front-desk").expect("Memory id is valid"),
            MemoryRevisionId::new("r1").expect("revision id is valid"),
        ),
        MemoryKind::Semantic,
        "semantic-memory",
        MemoryOwner::RoboGuide {
            component: "semantic-index".to_string(),
        },
        MemoryScope::Global,
        MemoryVisibility::Discoverable,
        "roboguide.semantic-place/v0.1",
        "application/json",
        None,
        None,
        None,
        None,
        TimestampMs::new(15),
    )
    .expect("shared fixture manifest is valid");

    assert_eq!(fixture["schema"], "roboguide.memory-catalog/v0.1");
    assert_eq!(
        fixture["memories"][0],
        serde_json::to_value(manifest).expect("manifest serializes")
    );
}

/// All five Memory kinds publish through one catalog while their bytes remain in CAS.
#[tokio::test]
async fn generic_memory_catalog_publishes_all_kinds_and_tracks_selective_exchange() {
    let directory = tempfile::tempdir().expect("temporary directory exists");
    let store =
        FileSystemArtifactStore::new(directory.path().join("artifacts")).expect("CAS initializes");
    let bytes = b"shared-memory-artifact";
    let digest = digest_bytes(bytes);
    let mut upload = store.begin_upload("memory-fixture").expect("upload begins");
    upload.write_chunk(bytes).expect("artifact bytes write");
    upload
        .finalize(&digest, bytes.len() as u64)
        .expect("artifact finalizes");
    let event_log =
        SqliteEventLog::open(directory.path().join("events.sqlite3")).expect("event log opens");
    seed_controller_checkpoint(&event_log);
    let catalog = ArtifactCatalog::replay_with_gate(&event_log, Arc::new(Mutex::new(())))
        .expect("catalog replays");
    let uploads = test_uploads();
    let kinds = [
        ("execution-a", MemoryKind::Execution),
        ("spatial-a", MemoryKind::Spatial),
        ("semantic-a", MemoryKind::Semantic),
        ("experience-a", MemoryKind::Experience),
        ("artifact-a", MemoryKind::Artifact),
    ];

    for (id, kind) in kinds {
        let manifest = fixture_memory_manifest(id, kind, &digest, bytes.len() as u64);
        let body = serde_json::to_vec(&manifest).expect("manifest serializes");
        let path = format!("/v1/memories/{id}/revisions/r1");
        response_body(
            &request_once(
                &store,
                &catalog,
                &uploads,
                raw_request("POST", &path, &body, true),
            )
            .await,
            "201 Created",
        );
    }

    let execution = fixture_memory_manifest(
        "execution-a",
        MemoryKind::Execution,
        &digest,
        bytes.len() as u64,
    );
    let replica = serde_json::to_vec(&serde_json::json!({
        "manifest": execution.clone(),
        "node_id": "dog-b",
        "consumer_provider_id": "execution-consumer",
        "status": "staged",
    }))
    .expect("replica request serializes");
    response_body(
        &request_once(
            &store,
            &catalog,
            &uploads,
            raw_request(
                "POST",
                "/v1/memories/execution-a/revisions/r1/replicas",
                &replica,
                true,
            ),
        )
        .await,
        "202 Accepted",
    );
    response_body(
        &request_once(
            &store,
            &catalog,
            &uploads,
            raw_request(
                "POST",
                "/v1/memories/execution-a/revisions/r1/replicas",
                &replica,
                true,
            ),
        )
        .await,
        "202 Accepted",
    );
    let second_replica = serde_json::to_vec(&serde_json::json!({
        "manifest": execution.clone(),
        "node_id": "dog-b",
        "consumer_provider_id": "secondary-consumer",
        "status": "staged",
    }))
    .expect("second provider replica request serializes");
    response_body(
        &request_once(
            &store,
            &catalog,
            &uploads,
            raw_request(
                "POST",
                "/v1/memories/execution-a/revisions/r1/replicas",
                &second_replica,
                true,
            ),
        )
        .await,
        "202 Accepted",
    );

    let list = request_once(
        &store,
        &catalog,
        &uploads,
        raw_request("GET", "/v1/memories", &[], false),
    )
    .await;
    let list: serde_json::Value = serde_json::from_slice(&response_body(&list, "200 OK"))
        .expect("Memory catalog response is JSON");
    assert_eq!(list["memories"].as_array().map(Vec::len), Some(5));

    let detail = request_once(
        &store,
        &catalog,
        &uploads,
        raw_request("GET", "/v1/memories/execution-a/revisions/r1", &[], false),
    )
    .await;
    let detail: serde_json::Value = serde_json::from_slice(&response_body(&detail, "200 OK"))
        .expect("Memory detail response is JSON");
    assert_eq!(detail["replicas"].as_array().map(Vec::len), Some(2));
    assert_eq!(
        detail["replicas"][0]["consumer_provider_id"],
        "execution-consumer"
    );
    assert_eq!(detail["replicas"][0]["status"], "staged");
    assert_eq!(
        detail["replicas"][1]["consumer_provider_id"],
        "secondary-consumer"
    );
    let replayed = ArtifactCatalog::replay_with_gate(&event_log, Arc::new(Mutex::new(())))
        .expect("provider-qualified replicas replay");
    let replicas = replayed
        .memory_replicas(execution.selector())
        .expect("replayed replicas are readable");
    assert_eq!(replicas.len(), 2);
    assert_eq!(replicas[0].consumer_provider_id(), "execution-consumer");
    assert_eq!(replicas[1].consumer_provider_id(), "secondary-consumer");
}

/// Node A publishes bytes and Node B's EAIOS workflow remains the imported-storage authority.
#[tokio::test]
async fn local_eaios_memory_workflow_cross_node_exchange_is_integrated() {
    use node_service::{
        BoxDriverFuture, CompiledDriverRequest, DriverKind, DriverResponse, LocalDriver,
        LocalIntegrationEngine, MemoryQuery, NodeServiceConfig,
    };

    /// Fake heterogeneous Local EAIOS driver used by Node B's configured import workflow.
    struct NoopHttpDriver {
        /// Counts actual provider import calls.
        imports: Arc<std::sync::atomic::AtomicUsize>,
    }

    impl LocalDriver for NoopHttpDriver {
        /// Identifies the configured local HTTP route family.
        fn kind(&self) -> DriverKind {
            DriverKind::Http
        }

        /// Returns one terminal response if a workflow is unexpectedly exercised.
        fn invoke<'a>(&'a self, request: &'a CompiledDriverRequest) -> BoxDriverFuture<'a> {
            if let CompiledDriverRequest::Http { path, body, .. } = request
                && path == "/memory/import"
            {
                assert_eq!(body["manifest"]["selector"]["memory_id"], "provider-map-a");
                assert!(body["staged_path"].as_str().is_some());
                self.imports
                    .fetch_add(1, std::sync::atomic::Ordering::SeqCst);
            }
            Box::pin(async {
                let (sender, receiver) = tokio::sync::mpsc::channel(1);
                sender
                    .send(Ok(node_service::DriverEvent {
                        sequence: 1,
                        payload: serde_json::json!({"state": "READY", "run_id": "noop"}),
                        terminal: true,
                    }))
                    .await
                    .expect("test receiver remains available");
                Ok(DriverResponse { events: receiver })
            })
        }
    }

    let directory = tempfile::tempdir().expect("temporary directory exists");
    let store =
        FileSystemArtifactStore::new(directory.path().join("cas")).expect("CAS initializes");
    let event_log =
        SqliteEventLog::open(directory.path().join("events.sqlite3")).expect("event log opens");
    seed_controller_checkpoint(&event_log);
    let catalog = ArtifactCatalog::replay_with_gate(&event_log, Arc::new(Mutex::new(())))
        .expect("catalog replays");
    let listener = TcpListener::bind("127.0.0.1:0")
        .await
        .expect("artifact listener binds");
    let address = listener.local_addr().expect("listener has address");
    let server = tokio::spawn(serve_artifact_http(
        listener,
        store.clone(),
        catalog.clone(),
        Arc::new(AllowTestMemoryAdmission),
        Arc::new(IgnoreTestLocalizationEvidence),
    ));
    let endpoint = format!("http://{address}");
    let client = node_service::ArtifactClient::new(&endpoint, 4, 1024, 1_000, 1_000)
        .expect("Artifact client config is valid");
    let bytes = b"cross-node-spatial-memory";
    let source = directory.path().join("node-a-map.bin");
    std::fs::write(&source, bytes).expect("Node A source writes");
    let digest = digest_bytes(bytes);
    let manifest = fixture_memory_manifest(
        "provider-map-a",
        MemoryKind::Spatial,
        &digest,
        bytes.len() as u64,
    );
    let wrong_reference = MemoryArtifactRef::new(
        ContentDigest::new("f".repeat(64)).expect("wrong digest is structurally valid"),
        bytes.len() as u64,
    );
    assert!(matches!(
        client.upload_memory_file(&source, &wrong_reference).await,
        Err(node_service::ArtifactError::DigestMismatch { .. })
    ));
    assert!(
        !store
            .contains(&digest)
            .expect("failed preflight must leave no CAS blob")
    );
    client
        .upload_memory_file(
            &source,
            manifest
                .artifact()
                .expect("exchangeable manifest has bytes"),
        )
        .await
        .expect("Node A uploads CAS bytes");
    client
        .publish_memory_manifest(
            &manifest,
            &NodeId::new("dog-a").expect("Node A id is valid"),
            "session-a",
        )
        .await
        .expect("Node A publishes Memory metadata");

    let config: NodeServiceConfig = serde_json::from_value(serde_json::json!({
            "schema": "roboguide.node-config/v0.6",
            "node_id": "dog-b",
            "server_endpoint": "http://127.0.0.1:1",
            "state_directory": directory.path().join("node-b-state"),
            "local_systems": [{
                "id": "memory",
                "runtime_name": "fixture-memory",
                "runtime_version": "1",
                "health": {
                    "step": {"id": "health", "connection": "local", "operation": {"kind": "http", "method": "GET", "path": "/health"}},
                    "state_pointer": "/state", "online": ["READY"], "degraded": ["DEGRADED"], "offline": ["OFFLINE"]
                }
            }],
            "connections": [{"driver": "http", "id": "local", "local_system": "memory", "endpoint": "http://127.0.0.1:2", "timeout_ms": 1000}],
            "capabilities": [{
                "contract": "memory.import@v1", "kind": "compute", "owner": "memory",
                "readiness": {"step": {"id": "ready", "connection": "local", "operation": {"kind": "http", "method": "GET", "path": "/ready"}}, "state_pointer": "/state", "ready": ["READY"], "unavailable": ["OFFLINE"]},
                "workflow": {
                    "execute": [{"id": "execute", "connection": "local", "operation": {"kind": "http", "method": "POST", "path": "/execute"}}],
                    "status": [{"id": "status", "connection": "local", "operation": {"kind": "http", "method": "GET", "path": "/status"}}],
                    "cancel": [{"id": "cancel", "connection": "local", "operation": {"kind": "http", "method": "POST", "path": "/cancel"}}],
                    "local_handle": {"kind": "pointer", "pointer": "/steps/execute/run_id"},
                    "execution_state": {"state_pointer": "/steps/status/state", "accepted": ["ACCEPTED"], "running": ["RUNNING"], "completed": ["COMPLETED"], "failed": ["FAILED"], "cancelled": ["CANCELLED"]}
                }
            }],
            "artifacts": {"endpoint": endpoint, "cache_directory": "artifact-cache", "max_artifact_bytes": 1024, "chunk_size_bytes": 4, "connect_timeout_ms": 1000, "read_timeout_ms": 1000},
            "memory_providers": [{
                "id": "local-spatial", "owner": "memory", "kind": "spatial", "scope": "global", "visibility": "exchangeable", "payload_schema": "example.memory/v1", "media_type": "application/octet-stream", "storage_directory": "memory-index",
                "import": {"steps": [{
                    "id": "import-memory", "connection": "local", "operation": {"kind": "http", "method": "POST", "path": "/memory/import"},
                    "request": {"base": {}, "bindings": [
                        {"target": "/manifest", "value": {"kind": "pointer", "pointer": "/invocation/memory_manifest"}},
                        {"target": "/staged_path", "value": {"kind": "pointer", "pointer": "/invocation/staged_artifact"}}
                    ]}
                }]}
            }]
        }))
        .expect("Node B config decodes");
    let compiled = node_service::CompiledLocalCatalog::compile(config, directory.path())
        .expect("Node B catalog compiles");
    let node_ledger_root = compiled.memory_providers()["local-spatial"]
        .storage_directory()
        .to_path_buf();
    let imports = Arc::new(std::sync::atomic::AtomicUsize::new(0));
    let engine = LocalIntegrationEngine::new(
        compiled,
        vec![Arc::new(NoopHttpDriver {
            imports: Arc::clone(&imports),
        }) as Arc<dyn LocalDriver>],
    )
    .expect("Node B engine starts");
    let incompatible = fixture_memory_manifest(
        "semantic-memory-a",
        MemoryKind::Semantic,
        &digest,
        bytes.len() as u64,
    );
    assert!(matches!(
        engine
            .exchange_memory(
                "local-spatial",
                &incompatible,
                "session-b",
                serde_json::json!({}),
            )
            .await,
        Err(node_service::EngineError::Configuration(_))
    ));
    assert_eq!(imports.load(std::sync::atomic::Ordering::SeqCst), 0);
    engine
        .exchange_memory(
            "local-spatial",
            &manifest,
            "session-b",
            serde_json::json!({}),
        )
        .await
        .expect("Node B selectively imports verified bytes");
    let staged = engine
        .artifact_stager()
        .expect("Artifact data plane is configured")
        .memory_cache_path(manifest.selector());
    assert_eq!(std::fs::read(&staged).expect("staged bytes read"), bytes);
    assert_eq!(imports.load(std::sync::atomic::Ordering::SeqCst), 1);
    assert!(node_ledger_root.join("manifests.jsonl").is_file());
    assert!(
        !node_ledger_root
            .join(format!("{}.blob", digest.replace(':', "_")))
            .exists(),
        "real EAIOS workflow imports must not create a second Node-ledger payload copy"
    );
    std::fs::remove_file(&staged).expect("test removes transfer cache after local import");
    engine
        .exchange_memory(
            "local-spatial",
            &manifest,
            "session-b",
            serde_json::json!({}),
        )
        .await
        .expect("same selective import is retry-idempotent");
    assert_eq!(imports.load(std::sync::atomic::Ordering::SeqCst), 1);
    assert!(
        !staged.exists(),
        "provider-owned import retry must not reconstruct the transfer cache"
    );
    assert_eq!(
        engine
            .discover_memories(
                "local-spatial",
                &MemoryQuery::default(),
                serde_json::json!({}),
            )
            .await
            .expect("Node B local index discovers import"),
        vec![manifest.clone()]
    );
    assert_eq!(
        catalog
            .memory_replicas(manifest.selector())
            .expect("replica evidence reads")[0]
            .status(),
        domain::MemoryReplicaStatus::Imported
    );
    server.abort();
}

/// Exchangeable Memory publication fails closed when referenced CAS bytes are absent.
#[tokio::test]
async fn generic_memory_publication_requires_existing_verified_cas_bytes() {
    let directory = tempfile::tempdir().expect("temporary directory exists");
    let store =
        FileSystemArtifactStore::new(directory.path().join("artifacts")).expect("CAS initializes");
    let event_log =
        SqliteEventLog::open(directory.path().join("events.sqlite3")).expect("event log opens");
    seed_controller_checkpoint(&event_log);
    let catalog = ArtifactCatalog::replay_with_gate(&event_log, Arc::new(Mutex::new(())))
        .expect("catalog replays");
    let manifest = fixture_memory_manifest(
        "missing-a",
        MemoryKind::Artifact,
        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        12,
    );
    let body = serde_json::to_vec(&manifest).expect("manifest serializes");

    response_body(
        &request_once(
            &store,
            &catalog,
            &test_uploads(),
            raw_request("POST", "/v1/memories/missing-a/revisions/r1", &body, true),
        )
        .await,
        "404 Not Found",
    );
    assert!(
        catalog
            .memories()
            .expect("catalog remains readable")
            .is_empty()
    );
}

/// HTTP publication cannot bypass the composition-owned provider admission port.
#[tokio::test]
async fn generic_memory_publication_requires_admitted_provider() {
    let directory = tempfile::tempdir().expect("temporary directory exists");
    let store =
        FileSystemArtifactStore::new(directory.path().join("artifacts")).expect("CAS initializes");
    let bytes = b"memory-provider-admission";
    let digest = digest_bytes(bytes);
    let mut upload = store
        .begin_upload("admission-fixture")
        .expect("upload begins");
    upload.write_chunk(bytes).expect("artifact bytes write");
    upload
        .finalize(&digest, bytes.len() as u64)
        .expect("artifact finalizes");
    let event_log =
        SqliteEventLog::open(directory.path().join("events.sqlite3")).expect("event log opens");
    seed_controller_checkpoint(&event_log);
    let catalog = ArtifactCatalog::replay_with_gate(&event_log, Arc::new(Mutex::new(())))
        .expect("catalog replays");
    let manifest = fixture_memory_manifest(
        "experience-a",
        MemoryKind::Experience,
        &digest,
        bytes.len() as u64,
    );
    let body = serde_json::to_vec(&manifest).expect("manifest serializes");

    response_body(
        &request_once_with_admission(
            &store,
            &catalog,
            &test_uploads(),
            raw_request(
                "POST",
                "/v1/memories/experience-a/revisions/r1",
                &body,
                true,
            ),
            Arc::new(DenyTestMemoryAdmission),
        )
        .await,
        "403 Forbidden",
    );
    assert!(
        catalog
            .memories()
            .expect("catalog remains readable")
            .is_empty()
    );
}

/// Generic Memory publication is fenced to the current session of its semantic owner Node.
#[tokio::test]
async fn generic_memory_publication_requires_current_owner_session() {
    let directory = tempfile::tempdir().expect("temporary directory exists");
    let store =
        FileSystemArtifactStore::new(directory.path().join("artifacts")).expect("CAS initializes");
    let bytes = b"memory-publisher-session";
    let digest = digest_bytes(bytes);
    let mut upload = store
        .begin_upload("session-fixture")
        .expect("upload begins");
    upload.write_chunk(bytes).expect("artifact bytes write");
    upload
        .finalize(&digest, bytes.len() as u64)
        .expect("artifact finalizes");
    let event_log =
        SqliteEventLog::open(directory.path().join("events.sqlite3")).expect("event log opens");
    seed_controller_checkpoint(&event_log);
    let catalog = ArtifactCatalog::replay_with_gate(&event_log, Arc::new(Mutex::new(())))
        .expect("catalog replays");
    let manifest = fixture_memory_manifest(
        "session-memory",
        MemoryKind::Experience,
        &digest,
        bytes.len() as u64,
    );
    let body = serde_json::to_vec(&manifest).expect("manifest serializes");
    let path = "/v1/memories/session-memory/revisions/r1";
    let admission: Arc<dyn MemoryProviderAdmission> = Arc::new(FixtureSessionMemoryAdmission);

    for request in [
        raw_request("POST", path, &body, true),
        raw_memory_request(path, &body, "dog-b", "session-current"),
        raw_memory_request(path, &body, "dog-a", "session-old"),
    ] {
        response_body(
            &request_once_with_admission(
                &store,
                &catalog,
                &test_uploads(),
                request,
                Arc::clone(&admission),
            )
            .await,
            "403 Forbidden",
        );
        assert!(
            catalog
                .memories()
                .expect("catalog remains readable")
                .is_empty()
        );
    }

    response_body(
        &request_once_with_admission(
            &store,
            &catalog,
            &test_uploads(),
            raw_memory_request(path, &body, "dog-a", "session-current"),
            admission,
        )
        .await,
        "201 Created",
    );
    assert_eq!(catalog.memories().expect("catalog is readable").len(), 1);

    let replica = serde_json::to_vec(&serde_json::json!({
        "manifest": manifest,
        "node_id": "dog-b",
        "consumer_provider_id": "fixture-consumer",
        "status": "staged",
    }))
    .expect("replica request serializes");
    let replica_path = "/v1/memories/session-memory/revisions/r1/replicas";
    response_body(
        &request_once_with_admission(
            &store,
            &catalog,
            &test_uploads(),
            raw_memory_request(replica_path, &replica, "dog-a", "session-current"),
            Arc::new(FixtureSessionMemoryAdmission),
        )
        .await,
        "403 Forbidden",
    );
    assert!(
        catalog
            .memory_replicas(manifest.selector())
            .expect("replicas remain readable")
            .is_empty()
    );
    let wrong_provider_replica = serde_json::to_vec(&serde_json::json!({
        "manifest": manifest,
        "node_id": "dog-b",
        "consumer_provider_id": "wrong-provider",
        "status": "staged",
    }))
    .expect("replica request serializes");
    response_body(
        &request_once_with_admission(
            &store,
            &catalog,
            &test_uploads(),
            raw_memory_request(
                replica_path,
                &wrong_provider_replica,
                "dog-b",
                "session-current",
            ),
            Arc::new(FixtureSessionMemoryAdmission),
        )
        .await,
        "403 Forbidden",
    );
    response_body(
        &request_once_with_admission(
            &store,
            &catalog,
            &test_uploads(),
            raw_memory_request(replica_path, &replica, "dog-b", "session-current"),
            Arc::new(FixtureSessionMemoryAdmission),
        )
        .await,
        "202 Accepted",
    );
    assert_eq!(
        catalog
            .memory_replicas(manifest.selector())
            .expect("replicas are readable")
            .len(),
        1
    );
}
