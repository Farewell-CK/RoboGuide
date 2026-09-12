use super::server::{handle_connection, start_upload};
use super::*;
use artifact_store::digest_bytes;
use domain::{
    ContentDigest, LocalSystemId, MapArtifactRef, MapId, MapReplicaStatus, MapRevisionId,
    MemoryArtifactRef, MemoryId, MemoryKind, MemoryOwner, MemoryRevisionId, MemoryScope,
    MemorySelector, MemoryVisibility, MissionId, SpatialAnchorId,
};
use ports::MapCatalogReader;

/// Opaque checkpoint schema used to prove Spatial Memory never rewrites controller content.
const TEST_CHECKPOINT_SCHEMA: &str = "roboguide.test-controller/v1";
/// Opaque checkpoint body carried forward by artifact-only event batches.
const TEST_CHECKPOINT_JSON: &str = r#"{"controller":"unchanged"}"#;

/// Test-only admission that isolates catalog behavior from Controller registration fixtures.
struct AllowTestMemoryAdmission;

/// Test observer that leaves Runtime integration to dedicated composition tests.
struct IgnoreTestLocalizationEvidence;

impl LocalizationEvidenceObserver for IgnoreTestLocalizationEvidence {
    /// Accepts already-validated strong evidence without adding another test authority.
    fn observe(
        &self,
        _evidence: &domain::LocalizationVerificationEvidence,
        _received_at: TimestampMs,
    ) -> Result<(), String> {
        Ok(())
    }
}

impl MemoryProviderAdmission for AllowTestMemoryAdmission {
    /// Accepts fixture manifests whose provider semantics are tested separately.
    fn admit_manifest(&self, _manifest: &MemoryArtifactManifest) -> Result<(), String> {
        Ok(())
    }

    /// Accepts fixture replica nodes whose transition semantics are tested by the projection.
    fn admit_replica(
        &self,
        _node_id: &NodeId,
        _consumer_provider_id: &str,
        _manifest: &MemoryArtifactManifest,
    ) -> Result<(), String> {
        Ok(())
    }

    /// Accepts absent fixture sessions because transport behavior is tested independently.
    fn admit_publisher(
        &self,
        _publisher: Option<&MemoryPublicationIdentity>,
        _expected_node_id: &NodeId,
    ) -> Result<(), String> {
        Ok(())
    }

    /// Accepts fixture typed evidence whose Runtime authority is tested separately.
    fn admit_localization_evidence(
        &self,
        _evidence: &domain::LocalizationVerificationEvidence,
    ) -> Result<(), String> {
        Ok(())
    }
}

/// Test-only admission that proves HTTP publication fails before catalog mutation.
struct DenyTestMemoryAdmission;

impl MemoryProviderAdmission for DenyTestMemoryAdmission {
    /// Rejects every fixture manifest as undeclared.
    fn admit_manifest(&self, _manifest: &MemoryArtifactManifest) -> Result<(), String> {
        Err("fixture provider is not registered".to_string())
    }

    /// Rejects every fixture replica reporter as unregistered.
    fn admit_replica(
        &self,
        _node_id: &NodeId,
        _consumer_provider_id: &str,
        _manifest: &MemoryArtifactManifest,
    ) -> Result<(), String> {
        Err("fixture replica node is not registered".to_string())
    }

    /// Rejects every fixture session as non-current.
    fn admit_publisher(
        &self,
        _publisher: Option<&MemoryPublicationIdentity>,
        _expected_node_id: &NodeId,
    ) -> Result<(), String> {
        Err("fixture publisher session is not current".to_string())
    }

    /// Rejects typed evidence together with all other fixture admissions.
    fn admit_localization_evidence(
        &self,
        _evidence: &domain::LocalizationVerificationEvidence,
    ) -> Result<(), String> {
        Err("fixture execution provenance is not current".to_string())
    }
}

/// Test-only admission that requires one exact current owner session.
struct FixtureSessionMemoryAdmission;

impl MemoryProviderAdmission for FixtureSessionMemoryAdmission {
    /// Accepts provider semantics so this fixture isolates publisher fencing.
    fn admit_manifest(&self, _manifest: &MemoryArtifactManifest) -> Result<(), String> {
        Ok(())
    }

    /// Requires the exact consumer provider independently of producer ownership.
    fn admit_replica(
        &self,
        _node_id: &NodeId,
        consumer_provider_id: &str,
        _manifest: &MemoryArtifactManifest,
    ) -> Result<(), String> {
        (consumer_provider_id == "fixture-consumer")
            .then_some(())
            .ok_or_else(|| "fixture consumer provider is incompatible".to_string())
    }

    /// Requires the semantic owner and fixture current session to match exactly.
    fn admit_publisher(
        &self,
        publisher: Option<&MemoryPublicationIdentity>,
        expected_node_id: &NodeId,
    ) -> Result<(), String> {
        let publisher = publisher.ok_or_else(|| "fixture publisher is missing".to_string())?;
        if publisher.node_id() != expected_node_id {
            return Err("fixture publisher does not own the Memory".to_string());
        }
        if publisher.session_id() != "session-current" {
            return Err("fixture publisher session is stale".to_string());
        }
        Ok(())
    }

    /// Accepts fixture execution provenance so the test isolates exact session fencing.
    fn admit_localization_evidence(
        &self,
        _evidence: &domain::LocalizationVerificationEvidence,
    ) -> Result<(), String> {
        Ok(())
    }
}

/// Test-only admission that accepts the owner session but fences stale attempt provenance.
struct DenyLocalizationAttemptAdmission;

impl MemoryProviderAdmission for DenyLocalizationAttemptAdmission {
    /// Accepts provider semantics so this fixture isolates typed execution provenance.
    fn admit_manifest(&self, _manifest: &MemoryArtifactManifest) -> Result<(), String> {
        Ok(())
    }

    /// Accepts replica semantics so this fixture isolates typed execution provenance.
    fn admit_replica(
        &self,
        _node_id: &NodeId,
        _consumer_provider_id: &str,
        _manifest: &MemoryArtifactManifest,
    ) -> Result<(), String> {
        Ok(())
    }

    /// Reuses the exact current-session fixture boundary.
    fn admit_publisher(
        &self,
        publisher: Option<&MemoryPublicationIdentity>,
        expected_node_id: &NodeId,
    ) -> Result<(), String> {
        FixtureSessionMemoryAdmission.admit_publisher(publisher, expected_node_id)
    }

    /// Rejects evidence that represents a superseded physical attempt.
    fn admit_localization_evidence(
        &self,
        _evidence: &domain::LocalizationVerificationEvidence,
    ) -> Result<(), String> {
        Err("fixture execution provenance is stale".to_string())
    }
}

/// Creates one production-shaped empty upload registry for HTTP request tests.
fn test_uploads() -> Uploads {
    Arc::new(Mutex::new(UploadRegistry::production()))
}

/// Persists a sequence-zero controller checkpoint for an otherwise empty test log.
fn seed_controller_checkpoint(event_log: &SqliteEventLog) {
    event_log.begin_batch().expect("checkpoint batch starts");
    event_log
        .save_checkpoint(TEST_CHECKPOINT_SCHEMA, TEST_CHECKPOINT_JSON)
        .expect("checkpoint saves");
    event_log.commit_batch().expect("checkpoint batch commits");
}

/// Builds one valid immutable manifest for the HTTP round-trip fixture.
fn fixture_manifest(digest: &str, byte_size: u64) -> MapArtifactManifest {
    let selector = MapRevisionSelector::new(
        MapId::new("map-a").expect("map id is valid"),
        MapRevisionId::new("r1").expect("revision id is valid"),
    );
    let artifact = MapArtifactRef::new(
        selector,
        ContentDigest::new(digest).expect("digest is valid"),
        byte_size,
    );
    MapArtifactManifest::new(
        artifact,
        "application/octet-stream",
        "grid-v1",
        NodeId::new("dog-a").expect("node id is valid"),
        None,
        MissionId::new("mission-a").expect("mission id is valid"),
        Some("execution-a".to_string()),
        None,
        "map",
        "enu",
        SpatialAnchorId::new("anchor-lab").expect("anchor is valid"),
        Some(0.05),
        TimestampMs::new(1),
        None,
    )
    .expect("manifest is valid")
}

/// Builds one generic exchangeable Memory manifest backed by a finalized CAS artifact.
fn fixture_memory_manifest(
    id: &str,
    kind: MemoryKind,
    digest: &str,
    byte_size: u64,
) -> MemoryArtifactManifest {
    MemoryArtifactManifest::new(
        MemorySelector::new(
            MemoryId::new(id).expect("Memory id is valid"),
            MemoryRevisionId::new("r1").expect("Memory revision is valid"),
        ),
        kind,
        "fixture-provider",
        MemoryOwner::Node {
            node_id: NodeId::new("dog-a").expect("node id is valid"),
            local_system_id: LocalSystemId::new("memory").expect("system id is valid"),
        },
        MemoryScope::Global,
        MemoryVisibility::Exchangeable,
        "example.memory/v1",
        "application/octet-stream",
        Some(MemoryArtifactRef::new(
            ContentDigest::new(digest).expect("digest is valid"),
            byte_size,
        )),
        None,
        None,
        None,
        TimestampMs::new(1),
    )
    .expect("Memory manifest is valid")
}

/// Builds one raw HTTP request with an optional body and explicit content length.
fn raw_request(method: &str, path: &str, body: &[u8], include_length: bool) -> Vec<u8> {
    let length = if include_length {
        format!("Content-Length: {}\r\n", body.len())
    } else {
        String::new()
    };
    let header =
        format!("{method} {path} HTTP/1.1\r\nHost: localhost\r\n{length}Connection: close\r\n\r\n");
    let mut request = header.into_bytes();
    request.extend_from_slice(body);
    request
}

/// Builds one generic Memory mutation carrying its framework-level Node session identity.
fn raw_memory_request(path: &str, body: &[u8], node_id: &str, session_id: &str) -> Vec<u8> {
    let header = format!(
        "POST {path} HTTP/1.1\r\nHost: localhost\r\nContent-Length: {}\r\nX-RoboGuide-Node-Id: {node_id}\r\nX-RoboGuide-Session-Id: {session_id}\r\nConnection: close\r\n\r\n",
        body.len()
    );
    let mut request = header.into_bytes();
    request.extend_from_slice(body);
    request
}

/// Builds a request whose declared length may exceed its bytes to exercise disconnect cleanup.
fn raw_request_with_declared_length(
    method: &str,
    path: &str,
    body: &[u8],
    declared_length: u64,
) -> Vec<u8> {
    let header = format!(
        "{method} {path} HTTP/1.1\r\nHost: localhost\r\nContent-Length: {declared_length}\r\nConnection: close\r\n\r\n"
    );
    let mut request = header.into_bytes();
    request.extend_from_slice(body);
    request
}

/// Sends one request through a one-shot local listener and returns the raw response.
async fn request_once(
    store: &FileSystemArtifactStore,
    catalog: &ArtifactCatalog,
    uploads: &Uploads,
    request: Vec<u8>,
) -> Vec<u8> {
    request_once_with_admission(
        store,
        catalog,
        uploads,
        request,
        Arc::new(AllowTestMemoryAdmission),
    )
    .await
}

/// Sends one request with caller-selected Memory admission behavior.
async fn request_once_with_admission(
    store: &FileSystemArtifactStore,
    catalog: &ArtifactCatalog,
    uploads: &Uploads,
    request: Vec<u8>,
    memory_admission: Arc<dyn MemoryProviderAdmission>,
) -> Vec<u8> {
    let listener = TcpListener::bind("127.0.0.1:0")
        .await
        .expect("test listener binds");
    let address = listener.local_addr().expect("test listener has address");
    let server_store = store.clone();
    let server_catalog = catalog.clone();
    let server_uploads = uploads.clone();
    let server = tokio::spawn(async move {
        let (mut stream, _) = listener.accept().await.expect("test request accepts");
        if let Err(error) = handle_connection(
            &mut stream,
            &server_store,
            &server_catalog,
            &server_uploads,
            memory_admission.as_ref(),
            &IgnoreTestLocalizationEvidence,
        )
        .await
        {
            let _ = write_json(&mut stream, error.status(), &error.body()).await;
        }
    });
    let mut client = TcpStream::connect(address)
        .await
        .expect("test client connects");
    client
        .write_all(&request)
        .await
        .expect("test request writes");
    client
        .shutdown()
        .await
        .expect("test client shuts down write");
    let mut response = Vec::new();
    client
        .read_to_end(&mut response)
        .await
        .expect("test response reads");
    server.await.expect("test server joins");
    response
}

/// Sends caller-selected TCP fragments through a one-shot listener and returns the response.
async fn fragmented_request_once(
    store: &FileSystemArtifactStore,
    catalog: &ArtifactCatalog,
    uploads: &Uploads,
    fragments: &[&[u8]],
) -> Vec<u8> {
    let listener = TcpListener::bind("127.0.0.1:0")
        .await
        .expect("test listener binds");
    let address = listener.local_addr().expect("test listener has address");
    let server_store = store.clone();
    let server_catalog = catalog.clone();
    let server_uploads = uploads.clone();
    let memory_admission = Arc::new(AllowTestMemoryAdmission);
    let server = tokio::spawn(async move {
        let (mut stream, _) = listener.accept().await.expect("test request accepts");
        if let Err(error) = handle_connection(
            &mut stream,
            &server_store,
            &server_catalog,
            &server_uploads,
            memory_admission.as_ref(),
            &IgnoreTestLocalizationEvidence,
        )
        .await
        {
            let _ = write_json(&mut stream, error.status(), &error.body()).await;
        }
    });
    let mut client = TcpStream::connect(address)
        .await
        .expect("test client connects");
    for fragment in fragments {
        client
            .write_all(fragment)
            .await
            .expect("test fragment writes");
        tokio::task::yield_now().await;
    }
    client
        .shutdown()
        .await
        .expect("test client shuts down write");
    let mut response = Vec::new();
    client
        .read_to_end(&mut response)
        .await
        .expect("test response reads");
    server.await.expect("test server joins");
    response
}

/// Checks a response status line and returns its body bytes.
fn response_body(response: &[u8], status: &str) -> Vec<u8> {
    let separator = response
        .windows(4)
        .position(|window| window == b"\r\n\r\n")
        .expect("response has headers");
    let header = std::str::from_utf8(&response[..separator]).expect("response header is UTF-8");
    assert!(
        header.starts_with(&format!("HTTP/1.1 {status}")),
        "{header}"
    );
    response[separator + 4..].to_vec()
}

mod memory_catalog;
mod spatial_catalog;
mod upload_protocol;
