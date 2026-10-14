//! Transport boundary regression tests use certificate digests only, without claiming TLS or hardware.

use super::*;
use std::time::Duration;

/// Owns a route with a real response channel and a deployment lifecycle registry.
fn router() -> (
    GrpcNodeRouter,
    mpsc::UnboundedReceiver<Result<ServerMessage, Status>>,
    crate::NodeIdentityRegistry,
) {
    let router = GrpcNodeRouter::default();
    let mut registry = crate::NodeIdentityRegistry::new("test-registry");
    registry
        .register("node", "tenant", "domain", "a".repeat(64), 0)
        .unwrap();
    *router.identity_policy.lock().unwrap() =
        NodeIdentityPolicy::CertificateLifecycle(registry.clone());
    router
        .identity_peers
        .lock()
        .unwrap()
        .insert("session".into(), "a".repeat(64));
    let (sender, receiver) = mpsc::unbounded_channel();
    router.sessions.lock().unwrap().insert(
        "node".into(),
        RoutedSession {
            certificate_expires_at_ms: None,
            liveness_evidence: None,
            activated_at: None,
            session_id: "session".into(),
            sender,
            lease_id: "lease".into(),
            last_heartbeat: std::time::Instant::now(),
            lease_duration: Duration::from_secs(60),
            node_contract_version: NODE_CONTRACT_VERSION.into(),
            management_sequence: 0,
            state_export_ids: BTreeSet::new(),
            active: true,
            supports_execution_session: true,
            supports_memory_import: true,
        },
    );
    (router, receiver, registry)
}

/// A live session loses both ingress and actual command routing as soon as committed revocation publishes.
#[test]
fn revocation_fences_existing_session_ingress_and_cancel_send() {
    let (router, mut receiver, mut registry) = router();
    router
        .cancel("node", "cancel-1".into(), "execution".into())
        .unwrap();
    assert!(receiver.try_recv().is_ok());
    registry.revoke("node", 1).unwrap();
    router.publish_identity_registry(registry).unwrap();
    assert_eq!(
        router
            .cancel("node", "cancel-2".into(), "execution".into())
            .unwrap_err()
            .code(),
        tonic::Code::Unauthenticated
    );
    assert!(receiver.try_recv().is_err());
    let message = NodeMessage {
        message: Some(NodePayload::Heartbeat(crate::grpc::v0_4::Heartbeat {
            session_id: "session".into(),
            lease_id: "lease".into(),
            sequence: 1,
            status: Some(crate::grpc::v0_4::NodeStatus {
                health: "online".into(),
                detail: String::new(),
            }),
        })),
    };
    assert_eq!(
        accept_current_message(&router, "node", "session", &message)
            .unwrap_err()
            .code(),
        tonic::Code::Unauthenticated
    );
    assert!(!router.session_is_current("node", "session").unwrap());
    assert!(route_is_expired(&router, "node", "session").unwrap());
}

/// Rotation remains bounded by the injected receive clock, even while the old stream stays connected.
#[test]
fn existing_old_leaf_expires_at_the_original_overlap_fence() {
    let (router, _receiver, mut registry) = router();
    registry
        .prepare_rotation("node", 1, "b".repeat(64))
        .unwrap();
    registry.activate_rotation("node", 2, 10, 10).unwrap();
    router.publish_identity_registry(registry).unwrap();
    *router.identity_clock.lock().unwrap() = Some(Arc::new(testkit::VirtualClock::new(
        domain::TimestampMs::new(19),
    )));
    assert!(router.validate_identity("node", "session").is_ok());
    *router.identity_clock.lock().unwrap() = Some(Arc::new(testkit::VirtualClock::new(
        domain::TimestampMs::new(20),
    )));
    assert!(router.validate_identity("node", "session").is_err());
    router
        .identity_peers
        .lock()
        .unwrap()
        .insert("session".into(), "b".repeat(64));
    assert!(router.validate_identity("node", "session").is_ok());
}

/// Obtains the public leaf digest using owned offline OpenSSL output; private key bytes stay local.
fn certificate_digest(leaf: &testkit::TestCertificate) -> String {
    let output = std::process::Command::new("openssl")
        .args(["x509", "-in"])
        .arg(&leaf.cert_path)
        .args(["-outform", "DER"])
        .output()
        .unwrap();
    assert!(output.status.success());
    format!("{:x}", Sha256::digest(output.stdout))
}

/// A real mutual-TLS session carries the actual leaf attribution and is fenced by committed revocation.
#[tokio::test]
async fn actual_mtls_session_is_rejected_after_live_revocation() {
    use crate::grpc::v0_4 as wire;
    let _ = rustls::crypto::ring::default_provider().install_default();
    let directory = tempfile_for_test();
    let pki = testkit::TestPki::new(&directory, &["server", "node"]).unwrap();
    let leaf = &pki.identities["node"];
    let expected_digest = certificate_digest(leaf);
    let mut registry = crate::NodeIdentityRegistry::new("tls-registry");
    registry
        .register("node", "tenant", "domain", expected_digest.clone(), 0)
        .unwrap();
    let (events, mut application) = mpsc::unbounded_channel();
    let (service, router) =
        GrpcIntegrationService::new_with_policy(events, NodeProtocolPolicy::default());
    *router.identity_policy.lock().unwrap() =
        NodeIdentityPolicy::require_lifecycle(registry.clone()).unwrap();
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let server_leaf = &pki.identities["server"];
    let tls = tonic::transport::ServerTlsConfig::new()
        .identity(tonic::transport::Identity::from_pem(
            std::fs::read(&server_leaf.cert_path).unwrap(),
            std::fs::read(&server_leaf.key_path).unwrap(),
        ))
        .client_ca_root(tonic::transport::Certificate::from_pem(
            std::fs::read(&pki.ca_cert_path).unwrap(),
        ));
    let server = tokio::spawn(
        tonic::transport::Server::builder()
            .tls_config(tls)
            .unwrap()
            .add_service(
                wire::robo_guide_node_protocol_server::RoboGuideNodeProtocolServer::new(service),
            )
            .serve_with_incoming(tokio_stream::wrappers::TcpListenerStream::new(listener)),
    );
    let channel = tonic::transport::Endpoint::from_shared(format!("https://{address}"))
        .unwrap()
        .connect_timeout(Duration::from_secs(2))
        .tls_config(
            tonic::transport::ClientTlsConfig::new()
                .domain_name("localhost")
                .ca_certificate(tonic::transport::Certificate::from_pem(
                    std::fs::read(&pki.ca_cert_path).unwrap(),
                ))
                .identity(tonic::transport::Identity::from_pem(
                    std::fs::read(&leaf.cert_path).unwrap(),
                    std::fs::read(&leaf.key_path).unwrap(),
                )),
        )
        .unwrap()
        .connect()
        .await
        .unwrap();
    let mut client =
        wire::robo_guide_node_protocol_client::RoboGuideNodeProtocolClient::new(channel);
    let (sender, receiver) = mpsc::unbounded_channel();
    let mut incoming = client
        .node_session(tokio_stream::wrappers::UnboundedReceiverStream::new(
            receiver,
        ))
        .await
        .unwrap()
        .into_inner();
    sender
        .send(wire::NodeMessage {
            message: Some(NodePayload::Hello(wire::Hello {
                node_id: "node".into(),
                protocol_versions: vec![PROTOCOL_VERSION.into()],
                node_contract_versions: vec![LEGACY_NODE_CONTRACT_VERSION.into()],
            })),
        })
        .unwrap();
    assert!(matches!(
        incoming.message().await.unwrap().unwrap().message,
        Some(ServerPayload::Welcome(_))
    ));
    sender
        .send(wire::NodeMessage {
            message: Some(NodePayload::Register(wire::Register {
                registration: Some(wire::NodeRegistration {
                    node_id: "node".into(),
                    node_contract_version: LEGACY_NODE_CONTRACT_VERSION.into(),
                    local_systems: vec![wire::LocalSystemDescriptor {
                        id: "local".into(),
                        runtime: Some(wire::LocalRuntime {
                            name: "identity-only-test".into(),
                            version: "1".into(),
                        }),
                        metadata: Default::default(),
                    }],
                    ..Default::default()
                }),
            })),
        })
        .unwrap();
    let delivery = tokio::time::timeout(Duration::from_secs(2), application.recv())
        .await
        .unwrap()
        .unwrap();
    let (event, completion) = delivery.into_parts();
    let GrpcNodeEvent::Registered {
        certificate_digest,
        certificate_expires_at_ms,
        ..
    } = event
    else {
        panic!("registration expected");
    };
    assert_eq!(
        certificate_digest.as_deref(),
        Some(expected_digest.as_str())
    );
    assert!(certificate_expires_at_ms.is_some_and(|expiry| expiry > identity_wall_time()));
    completion.accept();
    let Some(ServerPayload::Registered(registered)) =
        incoming.message().await.unwrap().unwrap().message
    else {
        panic!("registered expected");
    };
    registry.revoke("node", 1).unwrap();
    router.publish_identity_registry(registry).unwrap();
    sender
        .send(wire::NodeMessage {
            message: Some(NodePayload::Heartbeat(wire::Heartbeat {
                session_id: registered.session_id,
                lease_id: registered.lease_id,
                sequence: 1,
                status: Some(wire::NodeStatus {
                    health: "online".into(),
                    detail: String::new(),
                }),
            })),
        })
        .unwrap();
    let outcome = tokio::time::timeout(Duration::from_secs(2), incoming.message())
        .await
        .unwrap();
    assert!(outcome.is_err() || outcome.as_ref().is_ok_and(|message| message.is_none()));
    assert!(
        router
            .cancel("node", "post-revoke".into(), "none".into())
            .is_err()
    );
    server.abort();
    std::fs::remove_dir_all(directory).unwrap();
}

/// Owns an isolated directory for the actual certificate handshake test.
fn tempfile_for_test() -> std::path::PathBuf {
    let path = std::env::temp_dir().join(format!(
        "roboguide-identity-tls-{}-{}",
        std::process::id(),
        std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    ));
    std::fs::create_dir(&path).unwrap();
    path
}
