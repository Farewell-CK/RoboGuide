//! Loopback-only validation of configured wire timing and receive metadata; no Node execution.

use super::*;
use crate::grpc::v0_4 as wire;
use ports::{DiagnosticHandle, DiagnosticObserver, DiagnosticRecord};
use std::sync::atomic::AtomicU64;

/// Injected shared process time whose advance does not require a sleep or remote clock.
struct TestClock {
    /// Current controlled receive/decision timestamp.
    now: AtomicU64,
}

impl ports::Clock for TestClock {
    /// Returns the single local time domain used by metadata observation.
    fn now(&self) -> domain::TimestampMs {
        domain::TimestampMs::new(self.now.load(Ordering::Relaxed))
    }
}

/// Deliberately discards all observations to prove retention is unnecessary for protocol success.
#[derive(Default)]
struct DiscardingObserver {
    /// Number of attempted diagnostic records.
    attempts: AtomicU64,
}

impl DiagnosticObserver for DiscardingObserver {
    /// Drops the diagnostic without an I/O call, response or authority result.
    fn try_record(&self, _record: DiagnosticRecord) {
        self.attempts.fetch_add(1, Ordering::Relaxed);
    }
}

/// Reads one bounded loopback server response; timeout means a failed test rather than waiting forever.
async fn response(
    inbound: &mut tonic::Streaming<wire::ServerMessage>,
) -> wire::server_message::Message {
    tokio::time::timeout(std::time::Duration::from_secs(2), inbound.message())
        .await
        .unwrap()
        .unwrap()
        .unwrap()
        .message
        .unwrap()
}

/// Actual Welcome, route and application metadata use the frozen custom policy without early ACK.
#[tokio::test]
async fn custom_policy_and_original_ingress_survive_application_delay() {
    let (events, mut application) = mpsc::unbounded_channel();
    let clock = Arc::new(TestClock {
        now: AtomicU64::new(7),
    });
    let observer = Arc::new(DiscardingObserver::default());
    let policy =
        NodeProtocolPolicy::try_new(NodeLeasePolicy::try_new(7_000, 22_000).unwrap(), 1_000)
            .unwrap();
    let (service, router) = GrpcIntegrationService::new_with_observer(
        events,
        policy,
        clock.clone(),
        DiagnosticHandle::new(observer.clone()),
    );
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let server = tokio::spawn(
        tonic::transport::Server::builder()
            .add_service(
                wire::robo_guide_node_protocol_server::RoboGuideNodeProtocolServer::new(service),
            )
            .serve_with_incoming(tokio_stream::wrappers::TcpListenerStream::new(listener)),
    );
    let mut client = wire::robo_guide_node_protocol_client::RoboGuideNodeProtocolClient::connect(
        format!("http://{address}"),
    )
    .await
    .unwrap();
    let (sender, receiver) = mpsc::unbounded_channel();
    let mut inbound = client
        .node_session(UnboundedReceiverStream::new(receiver))
        .await
        .unwrap()
        .into_inner();
    sender
        .send(wire::NodeMessage {
            message: Some(NodePayload::Hello(wire::Hello {
                node_id: "observed-node".into(),
                protocol_versions: vec![PROTOCOL_VERSION.into()],
                node_contract_versions: vec![LEGACY_NODE_CONTRACT_VERSION.into()],
            })),
        })
        .unwrap();
    let wire::server_message::Message::Welcome(welcome) = response(&mut inbound).await else {
        panic!("Welcome is required");
    };
    assert_eq!(
        (welcome.heartbeat_interval_ms, welcome.lease_duration_ms),
        (7_000, 22_000)
    );
    sender
        .send(wire::NodeMessage {
            message: Some(NodePayload::Register(wire::Register {
                registration: Some(wire::NodeRegistration {
                    node_id: "observed-node".into(),
                    node_contract_version: LEGACY_NODE_CONTRACT_VERSION.into(),
                    local_systems: vec![wire::LocalSystemDescriptor {
                        id: "synthetic".into(),
                        runtime: Some(wire::LocalRuntime {
                            name: "fake".into(),
                            version: "1".into(),
                        }),
                        metadata: Default::default(),
                    }],
                    capabilities: vec![wire::Capability {
                        kind: "compute".into(),
                        available: true,
                        contracts: vec!["compute.work@v1".into()],
                        local_system_id: "synthetic".into(),
                    }],
                    ..Default::default()
                }),
            })),
        })
        .unwrap();
    let delivery = tokio::time::timeout(std::time::Duration::from_secs(2), application.recv())
        .await
        .unwrap()
        .unwrap();
    clock.now.store(100, Ordering::Relaxed);
    let (event, metadata, completion) = delivery.into_observed_parts();
    let GrpcNodeEvent::Registered {
        session_id,
        lease_id,
        ..
    } = event
    else {
        panic!("registration must reach application");
    };
    assert_eq!(metadata.unwrap().received_at, domain::TimestampMs::new(7));
    assert!(
        !router
            .session_is_current("observed-node", &session_id)
            .unwrap(),
        "pending application acceptance cannot activate a route"
    );
    assert_eq!(
        router.sessions.lock().unwrap()["observed-node"].lease_duration,
        std::time::Duration::from_millis(22_000)
    );
    completion.accept();
    assert!(matches!(
        response(&mut inbound).await,
        wire::server_message::Message::Registered(_)
    ));
    sender
        .send(wire::NodeMessage {
            message: Some(NodePayload::Heartbeat(wire::Heartbeat {
                session_id: session_id.clone(),
                lease_id,
                sequence: 1,
                status: Some(wire::NodeStatus {
                    health: "online".into(),
                    detail: String::new(),
                }),
            })),
        })
        .unwrap();
    let delivery = tokio::time::timeout(std::time::Duration::from_secs(2), application.recv())
        .await
        .unwrap()
        .unwrap();
    clock.now.store(500, Ordering::Relaxed);
    let (_, metadata, completion) = delivery.into_observed_parts();
    let metadata = metadata.unwrap();
    assert_eq!(metadata.received_at, domain::TimestampMs::new(100));
    assert_eq!(metadata.identity.unwrap().session_id, session_id);
    assert!(
        tokio::time::timeout(std::time::Duration::ZERO, inbound.message())
            .await
            .is_err(),
        "diagnostic observation cannot ACK a pending fact"
    );
    completion.accept();
    assert!(matches!(
        response(&mut inbound).await,
        wire::server_message::Message::Ack(wire::Ack { sequence: 1 })
    ));
    assert!(observer.attempts.load(Ordering::Relaxed) > 0);
    drop(sender);
    drop(inbound);
    drop(client);
    server.abort();
    let _ = server.await;
}
