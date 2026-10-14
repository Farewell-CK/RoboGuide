//! Real configured provider import, receipt replay and interrupted-side-effect fences.

use super::*;
use domain::{
    ContentDigest, MemoryArtifactManifest, MemoryArtifactRef, MemoryId,
    MemoryKind as DomainMemoryKind, MemoryOwner, MemoryRevisionId, MemoryScope, MemorySelector,
    MemoryVisibility,
};
use sha2::Digest as _;
use tokio::io::{AsyncReadExt, AsyncWriteExt};

/// Counts the configured local import operation; no robot, simulator or model is involved.
struct MemoryDriver(Arc<AtomicUsize>);

impl LocalDriver for MemoryDriver {
    /// Uses only the fixture's declarative HTTP connections.
    fn kind(&self) -> DriverKind {
        DriverKind::Http
    }
    /// Produces one original workflow response while retaining the actual side-effect count.
    fn invoke<'a>(&'a self, request: &'a CompiledDriverRequest) -> BoxDriverFuture<'a> {
        Box::pin(async move {
            if let CompiledDriverRequest::Http { path, .. } = request
                && path == "/memory/import"
            {
                self.0.fetch_add(1, Ordering::SeqCst);
            }
            let (sender, receiver) = tokio::sync::mpsc::channel(1);
            sender
                .send(Ok(DriverEvent {
                    sequence: 1,
                    payload: serde_json::json!({"state":"COMPLETED"}),
                    terminal: true,
                }))
                .await
                .unwrap();
            Ok(DriverResponse { events: receiver })
        })
    }
}

/// Supplies immutable CAS bytes and receives real provider-qualified staging/import reports.
async fn cas(listener: tokio::net::TcpListener) {
    loop {
        let (mut stream, _) = listener.accept().await.unwrap();
        let mut bytes = Vec::new();
        let mut chunk = [0u8; 4096];
        let header_end = loop {
            let count = stream.read(&mut chunk).await.unwrap();
            if count == 0 {
                return;
            }
            bytes.extend_from_slice(&chunk[..count]);
            if let Some(end) = bytes.windows(4).position(|value| value == b"\r\n\r\n") {
                break end + 4;
            }
            assert!(bytes.len() < 65536);
        };
        let headers = String::from_utf8_lossy(&bytes[..header_end]);
        let get = headers.starts_with("GET ");
        let length = headers
            .lines()
            .find_map(|line| {
                line.to_ascii_lowercase()
                    .strip_prefix("content-length:")
                    .map(|length| length.trim().parse::<usize>().unwrap())
            })
            .unwrap_or(0);
        while bytes.len() < header_end + length {
            let count = stream.read(&mut chunk).await.unwrap();
            assert!(count > 0);
            bytes.extend_from_slice(&chunk[..count]);
        }
        let body = if get { "memory-bytes" } else { "{}" };
        stream.write_all(format!("HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",body.len()).as_bytes()).await.unwrap();
    }
}

/// Recomputes only the transport envelope when the current Controller epoch changes.
fn sign(command: &mut integration::grpc::v0_4::MemoryImport) {
    command.authorization_digest = integration::memory_import_authorization_digest(
        &command.import_id,
        &command.command_id,
        &command.consumer_provider_id,
        &command.manifest_json,
        &command.mission_id,
        &command.group_id,
        &command.task_id,
        &command.role_id,
        &command.execution_id,
        &command.controller_epoch,
    );
}

/// A completed provider import replays receipts across epochs; interrupted new imports never repeat.
#[tokio::test]
async fn memory_provider_import_is_exact_once_across_receipt_loss_and_unknown() {
    let directory = tempfile::tempdir().unwrap();
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let endpoint = format!("http://{}", listener.local_addr().unwrap());
    let server = tokio::spawn(cas(listener));
    let mut config: NodeServiceConfig =
        toml::from_str(include_str!("../../../../../config/node.toml")).unwrap();
    config.state_directory = directory.path().join("state");
    config.artifacts.as_mut().unwrap().endpoint = endpoint;
    config.artifacts.as_mut().unwrap().cache_directory = directory.path().join("cache");
    let provider = &mut config.memory_providers[0];
    provider.scope = "global".into();
    provider.visibility = "exchangeable".into();
    provider.storage_directory = Some(std::path::PathBuf::from("memory"));
    provider.import = Some(crate::MemoryWorkflowConfig {
        steps: vec![WorkflowStepConfig {
            id: "import".into(),
            connection: "local-http".into(),
            operation: LocalOperationConfig::Http {
                method: "POST".into(),
                path: "/memory/import".into(),
            },
            request: RequestMappingConfig::default(),
        }],
        manifests_pointer: None,
        artifact_path_pointer: None,
    });
    let catalog = crate::CompiledLocalCatalog::compile(config, directory.path()).unwrap();
    let count = Arc::new(AtomicUsize::new(0));
    let engine = LocalIntegrationEngine::new(
        catalog,
        vec![Arc::new(MemoryDriver(Arc::clone(&count))) as Arc<dyn LocalDriver>],
    )
    .unwrap();
    let journal =
        crate::ExecutionJournal::open(crate::journal_path(&directory.path().join("state")))
            .unwrap();
    let spec=crate::ExecutionSpec::new(serde_json::to_vec(&serde_json::json!({"mission_id":"mission-a","group_id":"group-a","task_id":"task-a","role_id":"role-a"})).unwrap(),"immutable-workflow",Vec::<String>::new()).unwrap();
    journal.prepare_dispatch("execution-a", &spec).unwrap();
    journal.authorize_local_dispatch("execution-a").unwrap();
    journal
        .record_local_handle("execution-a", "handle-a")
        .unwrap();
    let manifest = MemoryArtifactManifest::new(
        MemorySelector::new(
            MemoryId::new("memory-a").unwrap(),
            MemoryRevisionId::new("r1").unwrap(),
        ),
        DomainMemoryKind::Artifact,
        "local-artifacts",
        MemoryOwner::Node {
            node_id: domain::NodeId::new("generic-node").unwrap(),
            local_system_id: domain::LocalSystemId::new("local-runtime").unwrap(),
        },
        MemoryScope::Global,
        MemoryVisibility::Exchangeable,
        "roboguide.example.local-artifact/v0.1",
        "application/octet-stream",
        Some(MemoryArtifactRef::new(
            ContentDigest::new(format!(
                "sha256:{:x}",
                sha2::Sha256::digest(b"memory-bytes")
            ))
            .unwrap(),
            12,
        )),
        None,
        None,
        None,
        domain::TimestampMs::new(1),
    )
    .unwrap();
    let mut command = integration::grpc::v0_4::MemoryImport {
        session_id: "session-a".into(),
        import_id: "import-a".into(),
        command_id: "memory-import-import-a".into(),
        consumer_provider_id: "local-artifacts".into(),
        manifest_json: serde_json::to_string(&manifest).unwrap(),
        mission_id: "mission-a".into(),
        group_id: "group-a".into(),
        task_id: "task-a".into(),
        role_id: "role-a".into(),
        execution_id: "execution-a".into(),
        controller_epoch: serde_json::json!({"term":1,"replica_id":1}).to_string(),
        authorization_digest: String::new(),
    };
    sign(&mut command);
    let service = NodeService::new(engine.clone());
    let (sender, mut receiver) = mpsc::unbounded_channel();
    let sequence = Arc::new(tokio::sync::Mutex::new(0));
    service
        .handle_memory_import(command.clone(), "session-a", &sender, &sequence)
        .await
        .unwrap();
    assert!(
        matches!(receiver.recv().await.unwrap().message,Some(NodePayload::MemoryImportReceipt(receipt)) if receipt.status==MemoryImportReceiptStatus::MemoryImportPersisted as i32)
    );
    assert!(
        matches!(receiver.recv().await.unwrap().message,Some(NodePayload::MemoryImportReceipt(receipt)) if receipt.status==MemoryImportReceiptStatus::MemoryImportCompleted as i32)
    );
    command.controller_epoch = serde_json::json!({"term":2,"replica_id":2}).to_string();
    sign(&mut command);
    service
        .handle_memory_import(command.clone(), "session-a", &sender, &sequence)
        .await
        .unwrap();
    assert!(
        matches!(receiver.recv().await.unwrap().message,Some(NodePayload::MemoryImportReceipt(receipt)) if receipt.status==MemoryImportReceiptStatus::MemoryImportCompleted as i32)
    );
    assert_eq!(count.load(Ordering::SeqCst), 1);
    command.import_id = "interrupted".into();
    command.command_id = "memory-import-interrupted".into();
    let mut manifest_json: serde_json::Value =
        serde_json::from_str(&command.manifest_json).unwrap();
    manifest_json["selector"]["revision_id"] = "r2".into();
    command.manifest_json = manifest_json.to_string();
    sign(&mut command);
    let identity = engine.memory_import_identity(&command).unwrap();
    engine
        .prepare_memory_import(&command.import_id, &command.command_id, &identity)
        .unwrap();
    drop(MemoryImportGuard {
        engine: engine.clone(),
        import_id: command.import_id.clone(),
        command_id: command.command_id.clone(),
        digest: identity,
    });
    service
        .handle_memory_import(command, "session-a", &sender, &sequence)
        .await
        .unwrap();
    assert!(
        matches!(receiver.recv().await.unwrap().message,Some(NodePayload::MemoryImportReceipt(receipt)) if receipt.status==MemoryImportReceiptStatus::MemoryImportUnknown as i32)
    );
    assert_eq!(count.load(Ordering::SeqCst), 1);
    server.abort();
    let _ = server.await;
}
