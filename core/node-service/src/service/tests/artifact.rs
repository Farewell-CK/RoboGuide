//! Node Service artifact recovery tests.

use super::*;

/// A pending remote artifact finalization stays fenced until an exact Execute retry authorizes it.
#[tokio::test]
async fn artifact_finalization_marker_blocks_implicit_restart_resume() {
    let state_dir = tempfile::tempdir().expect("state directory exists");
    let catalog = gated_catalog(
        "http://127.0.0.1:50051".to_string(),
        state_dir.path().to_path_buf(),
    );
    let journal_path = crate::journal_path(state_dir.path());
    let journal = crate::ExecutionJournal::open(&journal_path).expect("journal opens");
    let invocation = serde_json::json!({
        "mission_id": "mission-a",
        "task_id": "task-a",
        "group_id": "group-a",
        "role_id": "carrier",
        "capability_contract": "mobility.reach_region@v1",
        "parameters": {},
        "resource_ids": ["base"],
    });
    let workflow_digest = crate::engine::workflow_digest(
        &catalog,
        &catalog.capabilities()["mobility.reach_region@v1"],
        &invocation,
    )
    .expect("workflow identity computes");
    let spec = crate::ExecutionSpec::new(
        serde_json::to_vec(&invocation).expect("invocation serializes"),
        workflow_digest,
        vec!["base".to_string()],
    )
    .expect("execution spec is valid");
    assert!(matches!(
        journal
            .prepare_dispatch("artifact-ambiguous", &spec)
            .expect("dispatch is prepared"),
        crate::PrepareDispatch::Start(_)
    ));
    journal
        .authorize_local_dispatch("artifact-ambiguous")
        .expect("local dispatch is authorized");
    journal
        .record_local_handle("artifact-ambiguous", "local-1")
        .expect("local handle persists");
    journal
        .prepare_artifact_finalization(
            "artifact-ambiguous",
            crate::ArtifactFinalizationKind::Publish,
        )
        .expect("artifact finalization marker persists");
    drop(journal);

    let engine = crate::LocalIntegrationEngine::new(
        catalog,
        vec![Arc::new(GatedDriver::new(Arc::new(AtomicBool::new(false)))) as Arc<dyn LocalDriver>],
    )
    .expect("engine reopens journal");
    let mut events = engine.subscribe();
    engine
        .recover()
        .expect("ambiguous artifact finalization remains fenced");
    assert_eq!(
        crate::ExecutionJournal::open(&journal_path)
            .expect("audit journal opens")
            .get("artifact-ambiguous")
            .expect("record reads")
            .expect("record exists")
            .status(),
        crate::JournalStatus::ReconciliationRequired
    );
    assert!(
        tokio::time::timeout(std::time::Duration::from_millis(100), events.recv())
            .await
            .is_err(),
        "restart must not status-poll or retry remote finalization before explicit Execute"
    );
}

/// An interrupted output freeze blocks status polling and exact Execute replay after restart.
#[tokio::test]
async fn artifact_preparation_marker_blocks_mutable_source_reread_after_restart() {
    let state_dir = tempfile::tempdir().expect("state directory exists");
    let catalog = gated_catalog(
        "http://127.0.0.1:50051".to_string(),
        state_dir.path().to_path_buf(),
    );
    let invocation_json = serde_json::json!({
        "mission_id": "mission-a",
        "task_id": "build-map",
        "group_id": "group-a",
        "role_id": "mapper",
        "capability_contract": "mobility.reach_region@v1",
        "parameters": {},
        "resource_ids": ["base"],
    });
    let workflow_digest = crate::engine::workflow_digest(
        &catalog,
        &catalog.capabilities()["mobility.reach_region@v1"],
        &invocation_json,
    )
    .expect("workflow identity computes");
    let journal_path = crate::journal_path(state_dir.path());
    let journal = crate::ExecutionJournal::open(&journal_path).expect("journal opens");
    let spec = crate::ExecutionSpec::new(
        serde_json::to_vec(&invocation_json).expect("invocation serializes"),
        workflow_digest,
        vec!["base".to_string()],
    )
    .expect("execution spec is valid");
    journal
        .prepare_dispatch("build-recovery", &spec)
        .expect("dispatch prepares");
    journal
        .authorize_local_dispatch("build-recovery")
        .expect("local dispatch is authorized");
    journal
        .record_local_handle("build-recovery", "local-build")
        .expect("local handle persists");
    journal
        .record_status(
            "build-recovery",
            1,
            crate::JournalStatus::Running,
            "local map builder completed",
        )
        .expect("running fact persists");
    assert_eq!(
        journal
            .prepare_artifact_freeze("build-recovery", "lab-r1-output")
            .expect("first mutable-source read is granted"),
        crate::PrepareArtifactFreeze::Start
    );
    std::fs::write(state_dir.path().join("simulated-frozen-map"), b"map-v1")
        .expect("simulated immutable snapshot writes");
    drop(journal);

    let engine = crate::LocalIntegrationEngine::new(
        catalog,
        vec![Arc::new(GatedDriver::new(Arc::new(AtomicBool::new(true)))) as Arc<dyn LocalDriver>],
    )
    .expect("engine reopens journal");
    let mut events = engine.subscribe();
    engine
        .recover()
        .expect("interrupted artifact preparation remains fenced");
    assert!(
        tokio::time::timeout(std::time::Duration::from_millis(100), events.recv())
            .await
            .is_err(),
        "restart must not status-poll a completed local execution and freeze the source again"
    );

    let invocation = integration::grpc::v0_4::CanonicalInvocation {
        mission_id: "mission-a".to_string(),
        task_id: "build-map".to_string(),
        group_id: "group-a".to_string(),
        role_id: "mapper".to_string(),
        capability_contract: "mobility.reach_region@v1".to_string(),
        ..Default::default()
    };
    assert!(matches!(
        engine
            .execute(
                "build-recovery".to_string(),
                invocation,
                vec!["base".to_string()]
            )
            .expect("exact retry returns durable state"),
        crate::ExecuteDisposition::Existing(_)
    ));
    assert!(
        tokio::time::timeout(std::time::Duration::from_millis(100), events.recv())
            .await
            .is_err(),
        "an exact Execute retry must not grant another source read"
    );
    let audit = crate::ExecutionJournal::open(&journal_path).expect("audit journal opens");
    assert_eq!(
        audit
            .get("build-recovery")
            .expect("execution reads")
            .expect("execution exists")
            .status(),
        crate::JournalStatus::ReconciliationRequired
    );
    assert_eq!(
        audit
            .artifact_preparation("build-recovery")
            .expect("preparation marker reads"),
        Some("lab-r1-output".to_string())
    );
}

/// Exact input-finalization retry stays fenced when durable staged bytes are unavailable.
#[tokio::test]
async fn artifact_input_retry_reproves_local_bytes_before_replica_evidence() {
    use domain::{
        ContentDigest, MapArtifactManifest, MapArtifactRef, MapId, MapRevisionId,
        MapRevisionSelector, MissionId, NodeId, SpatialAnchorId, TimestampMs,
    };
    use integration::grpc::v0_4::scalar_value::Value as Scalar;
    use integration::grpc::v0_4::{CanonicalInvocation, ExecutionPhase, ScalarValue};
    use sha2::Digest;
    use std::collections::HashMap;
    use std::sync::atomic::AtomicUsize;
    use tokio::io::{AsyncReadExt, AsyncWriteExt};

    let digest = format!("sha256:{:x}", sha2::Sha256::digest(b"maps"));
    let manifest = MapArtifactManifest::new(
        MapArtifactRef::new(
            MapRevisionSelector::new(
                MapId::new("lab").expect("map id"),
                MapRevisionId::new("r1").expect("revision id"),
            ),
            ContentDigest::new(digest).expect("digest"),
            4,
        ),
        "application/octet-stream",
        "grid-v1",
        NodeId::new("dog-a").expect("node id"),
        None,
        MissionId::new("mission-build").expect("source mission id"),
        Some("build-execution".to_string()),
        None,
        "map",
        "enu",
        SpatialAnchorId::new("lab-origin").expect("anchor id"),
        Some(0.05),
        TimestampMs::new(7),
        None,
    )
    .expect("manifest is valid");
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("artifact listener binds");
    let address = listener.local_addr().expect("artifact address reads");
    let replica_writes = Arc::new(AtomicUsize::new(0));
    let server_writes = Arc::clone(&replica_writes);
    let body = serde_json::to_vec(&serde_json::json!({
        "status": "published",
        "manifest": manifest,
    }))
    .expect("manifest response serializes");
    let artifact_server = tokio::spawn(async move {
        loop {
            let (mut socket, _) = listener.accept().await.expect("artifact request accepts");
            let mut request = Vec::new();
            loop {
                let mut chunk = [0_u8; 1024];
                let length = socket.read(&mut chunk).await.expect("request reads");
                if length == 0 {
                    break;
                }
                request.extend_from_slice(&chunk[..length]);
                if request.windows(4).any(|window| window == b"\r\n\r\n") {
                    break;
                }
            }
            let request = String::from_utf8_lossy(&request);
            let response_body = if request.starts_with("GET /v1/maps/lab/revisions/r1 HTTP/") {
                body.clone()
            } else {
                server_writes.fetch_add(1, Ordering::SeqCst);
                b"{}".to_vec()
            };
            let response = format!(
                "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n",
                response_body.len()
            );
            socket
                .write_all(response.as_bytes())
                .await
                .expect("response headers write");
            socket
                .write_all(&response_body)
                .await
                .expect("response body writes");
            socket.shutdown().await.expect("response shuts down");
        }
    });

    let state_dir = tempfile::tempdir().expect("state directory exists");
    let catalog = gated_catalog_with_artifacts(
        "http://127.0.0.1:50051".to_string(),
        state_dir.path().to_path_buf(),
        Some(format!("http://{address}")),
        false,
    );
    let parameters = HashMap::from([
        (
            "artifact_operation".to_string(),
            ScalarValue {
                value: Some(Scalar::StringValue("import".to_string())),
            },
        ),
        (
            "artifact_slot".to_string(),
            ScalarValue {
                value: Some(Scalar::StringValue("lab-r1-input".to_string())),
            },
        ),
        (
            "map_id".to_string(),
            ScalarValue {
                value: Some(Scalar::StringValue("lab".to_string())),
            },
        ),
        (
            "revision_id".to_string(),
            ScalarValue {
                value: Some(Scalar::StringValue("r1".to_string())),
            },
        ),
        (
            "spatial_anchor_id".to_string(),
            ScalarValue {
                value: Some(Scalar::StringValue("lab-origin".to_string())),
            },
        ),
    ]);
    let invocation = CanonicalInvocation {
        mission_id: "mission-consume".to_string(),
        task_id: "import-map".to_string(),
        group_id: "group-consume".to_string(),
        role_id: "consumer".to_string(),
        capability_contract: "mobility.reach_region@v1".to_string(),
        parameters,
        intent: None,
    };
    let invocation_json = serde_json::json!({
        "mission_id": invocation.mission_id,
        "task_id": invocation.task_id,
        "group_id": invocation.group_id,
        "role_id": invocation.role_id,
        "capability_contract": invocation.capability_contract,
        "parameters": {
            "artifact_operation": "import",
            "artifact_slot": "lab-r1-input",
            "map_id": "lab",
            "revision_id": "r1",
            "spatial_anchor_id": "lab-origin",
        },
        "resource_ids": ["base"],
    });
    let workflow_digest = crate::engine::workflow_digest(
        &catalog,
        &catalog.capabilities()["mobility.reach_region@v1"],
        &invocation_json,
    )
    .expect("workflow identity computes");
    let journal_path = crate::journal_path(state_dir.path());
    let journal = crate::ExecutionJournal::open(&journal_path).expect("journal opens");
    let spec = crate::ExecutionSpec::new(
        serde_json::to_vec(&invocation_json).expect("invocation serializes"),
        workflow_digest,
        vec!["base".to_string()],
    )
    .expect("execution spec is valid");
    journal
        .prepare_dispatch("import-recovery", &spec)
        .expect("dispatch prepares");
    journal
        .authorize_local_dispatch("import-recovery")
        .expect("local dispatch is authorized");
    journal
        .record_local_handle("import-recovery", "local-1")
        .expect("local handle persists");
    journal
        .record_status(
            "import-recovery",
            1,
            crate::JournalStatus::Running,
            "local import completed",
        )
        .expect("running fact persists");
    journal
        .prepare_artifact_finalization("import-recovery", crate::ArtifactFinalizationKind::Import)
        .expect("finalization marker persists");
    drop(journal);

    let engine = crate::LocalIntegrationEngine::new(
        catalog,
        vec![Arc::new(GatedDriver::new(Arc::new(AtomicBool::new(false)))) as Arc<dyn LocalDriver>],
    )
    .expect("engine reopens journal");
    engine.recover().expect("pending finalization is fenced");
    let mut events = engine.subscribe();
    assert!(matches!(
        engine
            .execute(
                "import-recovery".to_string(),
                invocation,
                vec!["base".to_string()]
            )
            .expect("exact retry is accepted"),
        crate::ExecuteDisposition::Existing(_)
    ));
    let event = tokio::time::timeout(std::time::Duration::from_secs(2), events.recv())
        .await
        .expect("recovery fence event arrives")
        .expect("recovery fence event exists");
    assert_eq!(event.phase, ExecutionPhase::Unknown);
    let audit = crate::ExecutionJournal::open(&journal_path).expect("audit journal opens");
    assert_eq!(
        audit
            .get("import-recovery")
            .expect("execution reads")
            .expect("execution exists")
            .status(),
        crate::JournalStatus::ReconciliationRequired
    );
    assert_eq!(
        audit
            .artifact_finalization("import-recovery")
            .expect("marker reads"),
        Some(crate::ArtifactFinalizationKind::Import)
    );
    assert_eq!(replica_writes.load(Ordering::SeqCst), 0);
    artifact_server.abort();
}
