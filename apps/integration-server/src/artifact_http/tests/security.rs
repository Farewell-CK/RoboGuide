//! Actual TLS identity/resource isolation against the production Artifact HTTP listener.

use super::super::server::{load_artifact_tls, load_certificates, load_private_key};
use super::*;

/// Sends one framed request over a distinct certificate, accepting a rejected unmapped identity.
async fn call(
    address: std::net::SocketAddr,
    pki: &testkit::TestPki,
    identity: &str,
    request: &str,
) -> String {
    let mut roots = rustls::RootCertStore::empty();
    for certificate in load_certificates(&pki.ca_cert_path).unwrap() {
        roots.add(certificate).unwrap();
    }
    let leaf = &pki.identities[identity];
    let config = rustls::ClientConfig::builder()
        .with_root_certificates(roots)
        .with_client_auth_cert(
            load_certificates(&leaf.cert_path).unwrap(),
            load_private_key(&leaf.key_path).unwrap(),
        )
        .unwrap();
    let connector = tokio_rustls::TlsConnector::from(Arc::new(config));
    let socket = tokio::net::TcpStream::connect(address).await.unwrap();
    let mut stream = connector
        .connect(
            rustls::pki_types::ServerName::try_from("localhost").unwrap(),
            socket,
        )
        .await
        .unwrap();
    let _ = stream.write_all(request.as_bytes()).await;
    let mut response = String::new();
    let _ = stream.read_to_string(&mut response).await;
    response
}

/// Role headers cannot bypass certificate grants, resource ACLs or upload ownership.
#[tokio::test]
async fn artifact_mtls_enforces_node_resource_and_upload_isolation() {
    let directory = tempfile::tempdir().unwrap();
    let pki = testkit::TestPki::new(
        &directory.path().join("pki"),
        &["server", "viewer", "writer", "other", "unmapped"],
    )
    .unwrap();
    let fingerprint = |name: &str| {
        format!(
            "sha256:{:x}",
            Sha256::digest(load_certificates(&pki.identities[name].cert_path).unwrap()[0].as_ref())
        )
    };
    let writer = fingerprint("writer");
    let roles = serde_json::json!({
        fingerprint("viewer"):"viewer",
        writer.clone():{"role":"writer","node_id":"node-a","provider_ids":["provider-a"],"memory_ids":["memory-a"]},
        fingerprint("other"):{"role":"writer","node_id":"node-b","provider_ids":["provider-b"]}
    });
    let roles_path = directory.path().join("roles.json");
    std::fs::write(&roles_path, serde_json::to_vec(&roles).unwrap()).unwrap();
    let leaf = &pki.identities["server"];
    let profile = load_artifact_tls(
        &leaf.cert_path,
        &leaf.key_path,
        &pki.ca_cert_path,
        &roles_path,
    )
    .unwrap();
    let log = SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
    log.begin_batch().unwrap();
    log.save_checkpoint(TEST_CHECKPOINT_SCHEMA, TEST_CHECKPOINT_JSON)
        .unwrap();
    log.commit_batch().unwrap();
    let catalog = ArtifactCatalog::replay_with_gate(&log, Arc::new(Mutex::new(()))).unwrap();
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let task = tokio::spawn(serve_artifact_http(
        listener,
        FileSystemArtifactStore::new(directory.path().join("cas")).unwrap(),
        catalog.clone(),
        Arc::new(AllowTestMemoryAdmission),
        Arc::new(IgnoreTestLocalizationEvidence),
        Some(profile),
    ));
    let result=timeout(Duration::from_secs(10),async {
        let denied=call(address,&pki,"viewer","POST /v1/artifact-uploads HTTP/1.1\r\nHost: localhost\r\nX-RoboGuide-Role: admin\r\nContent-Length: 2\r\n\r\n{}").await;
        assert!(denied.starts_with("HTTP/1.1 403"));
        let upload_id=format!("cert-{}-one",writer.trim_start_matches("sha256:"));
        let body=serde_json::json!({"upload_id":upload_id}).to_string();
        let created=call(address,&pki,"writer",&format!("POST /v1/artifact-uploads HTTP/1.1\r\nHost: localhost\r\nContent-Length: {}\r\n\r\n{body}",body.len())).await;
        assert!(created.starts_with("HTTP/1.1 201"),"{created}");
        let stolen=call(address,&pki,"other",&format!("DELETE /v1/artifact-uploads/{upload_id} HTTP/1.1\r\nHost: localhost\r\nContent-Length: 0\r\n\r\n")).await;
        assert!(stolen.starts_with("HTTP/1.1 403"));
        let forged=call(address,&pki,"writer","POST /v1/memories/memory-a/revisions/r1 HTTP/1.1\r\nHost: localhost\r\nX-RoboGuide-Node-Id: node-b\r\nX-RoboGuide-Session-Id: forged-session\r\nContent-Length: 2\r\n\r\n{}").await;
        assert!(forged.starts_with("HTTP/1.1 403"));
        let listing=call(address,&pki,"viewer","GET /v1/memories HTTP/1.1\r\nHost: localhost\r\n\r\n").await;
        assert!(listing.starts_with("HTTP/1.1 403"));
        let unmapped=call(address,&pki,"unmapped","GET /metrics HTTP/1.1\r\nHost: localhost\r\n\r\n").await;
        assert!(unmapped.is_empty());
        assert!(catalog.memories().unwrap().is_empty());
    }).await;
    task.abort();
    let _ = task.await;
    result.unwrap();
    let sql = rusqlite::Connection::open(directory.path().join("events.sqlite3")).unwrap();
    let denied:u64=sql.query_row("SELECT COUNT(*) FROM service_access_audit WHERE principal=?1 AND decision='route_denied'",[writer],|row|row.get(0)).unwrap();
    assert_eq!(denied, 1);
}
