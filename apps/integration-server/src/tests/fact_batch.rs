//! Real protocol batch acceptance, independent rejection, commit failures and deterministic traces.

use super::*;
use integration::grpc::v0_4::{
    self as wire, node_message::Message as NodePayload, server_message::Message as ServerPayload,
};
use std::sync::atomic::{AtomicU64, Ordering};
use tokio::sync::mpsc;
use tokio_stream::wrappers::{TcpListenerStream, UnboundedReceiverStream};

/// One shared Controller receive clock, advanced explicitly by the deterministic fixture.
#[derive(Default)]
struct TestClock(AtomicU64);

impl Clock for TestClock {
    /// Returns only the fixture's local clock; peer clocks never participate.
    fn now(&self) -> domain::TimestampMs {
        domain::TimestampMs::new(self.0.load(Ordering::Relaxed))
    }
}

/// One original formal session with its actual commands and acceptance responses.
struct Peer {
    /// Input to the real gRPC reader.
    sender: mpsc::UnboundedSender<wire::NodeMessage>,
    /// Output from the real transport, never synthesized by a test callback.
    incoming: tonic::Streaming<wire::ServerMessage>,
    /// Negotiated session identity.
    session: String,
    /// Negotiated lease identity.
    lease: String,
}

/// Owns only this test's peers, Controller, writer gate and SQLite file.
struct Fixture {
    /// Scoped database directory, retained until all readers stop.
    directory: tempfile::TempDir,
    /// Actual application state.
    controller: Arc<Mutex<ControllerState>>,
    /// Actual event/checkpoint transaction store.
    events: state::SqliteEventLog,
    /// Existing writer serialization.
    gate: Arc<Mutex<()>>,
    /// Formal exact-session router.
    router: integration::GrpcNodeRouter,
    /// Shared ingress and decision clock.
    clock: Arc<TestClock>,
    /// Validated application inputs, with original receive metadata/completions.
    receiver: mpsc::UnboundedReceiver<integration::GrpcNodeEventDelivery>,
    /// Independently negotiated physical-owner proxies; no work executes.
    peers: Vec<Peer>,
    /// Test-owned server stopped on every exit.
    server: tokio::task::JoinHandle<Result<(), tonic::transport::Error>>,
}

impl Drop for Fixture {
    /// Stops the fixture's listener without touching any shared process.
    fn drop(&mut self) {
        self.server.abort();
    }
}

/// Declares exact node-local resource IDs through the current Node contract.
fn registration(index: usize) -> wire::NodeRegistration {
    wire::NodeRegistration {
        node_id: format!("worker-{index}"),
        node_contract_version: wire::NODE_CONTRACT_VERSION.into(),
        local_systems: vec![wire::LocalSystemDescriptor {
            id: "worker-runtime".into(),
            runtime: Some(wire::LocalRuntime {
                name: "fixture".into(),
                version: "1".into(),
            }),
            metadata: Default::default(),
        }],
        capability_profiles: vec![wire::CapabilityProfile {
            contract: "compute.work@v1".into(),
            kind: "compute".into(),
            local_system_id: "worker-runtime".into(),
            ready: true,
            attributes: Default::default(),
        }],
        operation_support: vec![wire::OperationSupport {
            operation: Some(wire::OperationRef {
                namespace: "compute".into(),
                name: "work".into(),
                version: "v1".into(),
            }),
            local_system_id: "worker-runtime".into(),
        }],
        resources: vec![wire::Resource {
            id: format!("cpu-{index}"),
            kind: "compute".into(),
            capacity: 1,
            local_system_id: "worker-runtime".into(),
            metadata: Default::default(),
        }],
        ..Default::default()
    }
}

impl Fixture {
    /// Negotiates two pending/accepted routes using the same production fact driver under test.
    async fn new() -> Self {
        let directory = tempfile::tempdir().unwrap();
        let events = state::SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
        let clock = Arc::new(TestClock(AtomicU64::new(1)));
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let (sender, receiver) = mpsc::unbounded_channel();
        let (service, router) = integration::GrpcIntegrationService::new_with_observer(
            sender,
            NodeProtocolPolicy::default(),
            clock.clone(),
            ports::DiagnosticHandle::default(),
        );
        let server = tokio::spawn(async move {
            tonic::transport::Server::builder()
                .add_service(
                    wire::robo_guide_node_protocol_server::RoboGuideNodeProtocolServer::new(
                        service,
                    ),
                )
                .serve_with_incoming(TcpListenerStream::new(listener))
                .await
        });
        let controller = Arc::new(Mutex::new(ControllerState {
            bridge: IntegrationRuntimeBridge::new(
                control::ControlPlane::new(),
                state::InMemorySharedNodeState::new(),
                events.clone(),
                router.clone(),
            ),
            orchestrator: MissionOrchestrator::new(),
            mission_admissions: BTreeMap::new(),
            verifier_seen: BTreeSet::new(),
            verifier_source_digest: None,
        }));
        let mut fixture = Self {
            directory,
            controller,
            events,
            gate: Arc::new(Mutex::new(())),
            router,
            clock,
            receiver,
            peers: Vec::new(),
            server,
        };
        for index in 0..2 {
            let mut client =
                wire::robo_guide_node_protocol_client::RoboGuideNodeProtocolClient::connect(
                    format!("http://{address}"),
                )
                .await
                .unwrap();
            let (sender, receiver) = mpsc::unbounded_channel();
            sender
                .send(wire::NodeMessage {
                    message: Some(NodePayload::Hello(wire::Hello {
                        node_id: format!("worker-{index}"),
                        protocol_versions: vec![wire::PROTOCOL_VERSION.into()],
                        node_contract_versions: vec![wire::NODE_CONTRACT_VERSION.into()],
                    })),
                })
                .unwrap();
            let mut incoming = client
                .node_session(UnboundedReceiverStream::new(receiver))
                .await
                .unwrap()
                .into_inner();
            assert!(matches!(
                incoming.message().await.unwrap().unwrap().message,
                Some(ServerPayload::Welcome(_))
            ));
            sender
                .send(wire::NodeMessage {
                    message: Some(NodePayload::Register(wire::Register {
                        registration: Some(registration(index)),
                    })),
                })
                .unwrap();
            let delivery = fixture.next().await;
            assert!(fact_is_barrier(delivery.event()));
            assert!(
                tokio::time::timeout(Duration::ZERO, incoming.message())
                    .await
                    .is_err()
            );
            let (deliveries, barrier) =
                collect_fact_batch(delivery, &mut fixture.receiver, 8, Duration::from_millis(5))
                    .await;
            assert_eq!(deliveries.len(), 1);
            assert!(barrier.is_none());
            fixture.run(deliveries).unwrap();
            let Some(ServerPayload::Registered(registered)) =
                incoming.message().await.unwrap().unwrap().message
            else {
                panic!("durable Registered expected")
            };
            fixture.peers.push(Peer {
                sender,
                incoming,
                session: registered.session_id,
                lease: registered.lease_id,
            });
        }
        fixture
    }

    /// Waits only for one already transmitted fact, with a bounded test timeout.
    async fn next(&mut self) -> integration::GrpcNodeEventDelivery {
        tokio::time::timeout(Duration::from_secs(2), self.receiver.recv())
            .await
            .unwrap()
            .unwrap()
    }

    /// Runs the actual extracted production driver; it owns every completion channel.
    fn run(
        &self,
        deliveries: Vec<integration::GrpcNodeEventDelivery>,
    ) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
        NodeFactDriver {
            controller: &self.controller,
            events: &self.events,
            write_gate: &self.gate,
            router: &self.router,
            clock: self.clock.as_ref(),
            diagnostics: &ports::DiagnosticHandle::default(),
        }
        .run(deliveries)
    }

    /// Queues a heartbeat through its negotiated original peer and retains its ingress metadata.
    async fn heartbeat(
        &mut self,
        index: usize,
        sequence: u64,
        now: u64,
    ) -> integration::GrpcNodeEventDelivery {
        self.clock.0.store(now, Ordering::Relaxed);
        let peer = &self.peers[index];
        peer.sender
            .send(wire::NodeMessage {
                message: Some(NodePayload::Heartbeat(wire::Heartbeat {
                    session_id: peer.session.clone(),
                    lease_id: peer.lease.clone(),
                    sequence,
                    status: Some(wire::NodeStatus {
                        health: "online".into(),
                        detail: String::new(),
                    }),
                })),
            })
            .unwrap();
        self.next().await
    }

    /// Checks actual durable sequence acknowledgements, preserving any preceding command evidence.
    async fn ack(&mut self, index: usize, sequence: u64) {
        loop {
            let message =
                tokio::time::timeout(Duration::from_secs(2), self.peers[index].incoming.message())
                    .await
                    .unwrap()
                    .unwrap()
                    .unwrap();
            match message.message {
                Some(ServerPayload::Ack(ack)) => {
                    assert_eq!(ack.sequence, sequence);
                    return;
                }
                Some(ServerPayload::Execute(_)) => {}
                other => panic!("unexpected protocol output {other:?}"),
            }
        }
    }

    /// Normalizes only transport-generated nonce identities before comparing exact authority traces.
    fn normalized(&self, text: &str) -> String {
        let mut output = text.to_string();
        for (index, peer) in self.peers.iter().enumerate() {
            output = output.replace(&peer.session, &format!("session-{index}"));
            output = output.replace(&peer.lease, &format!("lease-{index}"));
        }
        output
    }

    /// Accepts two real resource-bearing Missions without dispatching or bypassing Control.
    fn submit(&self, deadline: Option<u64>) {
        let mut live = self.controller.lock().unwrap();
        self.events.begin_batch().unwrap();
        for (name, deadline) in [("mission-a", deadline), ("mission-b", None)] {
            let ControllerState {
                orchestrator,
                bridge,
                ..
            } = &mut *live;
            orchestrator
                .submit(
                    super::runtime_contract::plan(name, deadline),
                    domain::ExecutionGroupId::new(format!("group-{name}")).unwrap(),
                    bridge.control_mut(),
                    domain::TimestampMs::new(1),
                    &domain::CorrelationId::new("batch-plans").unwrap(),
                    &mut self.events.clone(),
                )
                .unwrap();
        }
        self.events
            .save_checkpoint(
                SERVER_CHECKPOINT_SCHEMA,
                &server_checkpoint_json(&live).unwrap(),
            )
            .unwrap();
        self.events.commit_batch().unwrap();
    }

    /// Sends only an original-owner lifecycle observation; this helper never executes work.
    async fn execution(
        &mut self,
        index: usize,
        execution_id: &str,
        sequence: u64,
        phase: wire::ExecutionPhase,
        now: u64,
    ) -> integration::GrpcNodeEventDelivery {
        self.clock.0.store(now, Ordering::Relaxed);
        let peer = &self.peers[index];
        peer.sender
            .send(wire::NodeMessage {
                message: Some(NodePayload::ExecutionSnapshot(wire::ExecutionSnapshot {
                    session_id: peer.session.clone(),
                    execution_id: execution_id.into(),
                    last_sequence: sequence,
                    phase: phase as i32,
                    reason: "batch owner fact".into(),
                })),
            })
            .unwrap();
        self.next().await
    }

    /// Activates normal Match/Commit/Bind and admits actual owner Started facts before outcomes.
    async fn start(&mut self) -> Vec<runtime::ExecutionAttemptSnapshot> {
        let first = self.heartbeat(0, 1, 3).await;
        let second = self.heartbeat(1, 1, 3).await;
        self.run(vec![first, second]).unwrap();
        self.ack(0, 1).await;
        self.ack(1, 1).await;
        let attempts = self.controller.lock().unwrap().bridge.attempt_history();
        assert_eq!(attempts.len(), 2);
        let first = self
            .execution(
                0,
                attempts[0].execution_id(),
                1,
                wire::ExecutionPhase::Started,
                4,
            )
            .await;
        let second = self
            .execution(
                1,
                attempts[1].execution_id(),
                1,
                wire::ExecutionPhase::Started,
                4,
            )
            .await;
        self.run(vec![first, second]).unwrap();
        self.ack(0, 1).await;
        self.ack(1, 1).await;
        attempts
    }
}

/// 1/N keep exact event/checkpoint authority for the same ordered inputs and original receive times.
#[tokio::test]
async fn single_and_multi_fact_batches_have_equivalent_durable_traces() {
    let mut one = Fixture::new().await;
    let mut many = Fixture::new().await;
    for sequence in 1..=3 {
        for (index, fixture) in [&mut one, &mut many].into_iter().enumerate() {
            let first = fixture.heartbeat(0, sequence, 2 + sequence).await;
            let second = fixture.heartbeat(1, sequence, 2 + sequence).await;
            for peer in &mut fixture.peers {
                assert!(
                    tokio::time::timeout(Duration::ZERO, peer.incoming.message())
                        .await
                        .is_err(),
                    "queued facts cannot Ack before Commit"
                );
            }
            if index == 0 {
                fixture.run(vec![first]).unwrap();
                fixture.run(vec![second]).unwrap();
            } else {
                fixture.run(vec![first, second]).unwrap();
            }
            fixture.ack(0, sequence).await;
            fixture.ack(1, sequence).await;
        }
    }
    assert_eq!(
        one.normalized(
            &one.events
                .load_checkpoint()
                .unwrap()
                .unwrap()
                .checkpoint_json
        ),
        many.normalized(
            &many
                .events
                .load_checkpoint()
                .unwrap()
                .unwrap()
                .checkpoint_json
        )
    );
    let trace = |fixture: &Fixture| {
        fixture
            .events
            .events()
            .unwrap()
            .into_iter()
            .map(|event| {
                (
                    event.sequence,
                    event.timestamp_ms,
                    event.payload_schema,
                    fixture.normalized(&event.payload_json),
                )
            })
            .collect::<Vec<_>>()
    };
    assert_eq!(trace(&one), trace(&many));
}

/// Failed checkpoint writes leave both original input completions unavailable and live state unchanged.
#[tokio::test]
async fn checkpoint_failure_rolls_back_all_inputs_without_ack() {
    let mut fixture = Fixture::new().await;
    fixture.submit(None);
    let before = fixture.events.load_checkpoint().unwrap().unwrap();
    let trace = fixture.events.events().unwrap();
    let sql = rusqlite::Connection::open(fixture.directory.path().join("events.sqlite3")).unwrap();
    sql.execute_batch("CREATE TRIGGER reject_checkpoint BEFORE INSERT ON controller_checkpoint BEGIN SELECT RAISE(ABORT, 'batch checkpoint failure'); END").unwrap();
    let first = fixture.heartbeat(0, 1, 3).await;
    let second = fixture.heartbeat(1, 1, 3).await;
    assert!(fixture.run(vec![first, second]).is_err());
    assert_eq!(fixture.events.load_checkpoint().unwrap().unwrap(), before);
    assert_eq!(fixture.events.events().unwrap(), trace);
    assert_eq!(
        server_checkpoint_json(&fixture.controller.lock().unwrap()).unwrap(),
        before.checkpoint_json
    );
    assert!(
        fixture
            .controller
            .lock()
            .unwrap()
            .bridge
            .attempt_history()
            .is_empty(),
        "uncommitted Execute candidates cannot become live or be delivered"
    );
    for peer in &mut fixture.peers {
        let error = tokio::time::timeout(Duration::from_secs(2), peer.incoming.message())
            .await
            .unwrap()
            .unwrap_err();
        assert_eq!(error.code(), tonic::Code::Unavailable);
    }
}

/// An ownership rejection rolls back only its input and fences that exact route before outbox flush.
#[tokio::test]
async fn rejected_owner_fact_does_not_discard_another_sessions_accepted_fact() {
    let mut fixture = Fixture::new().await;
    fixture.submit(None);
    let attempts = fixture.start().await;
    let first = fixture
        .execution(
            1,
            attempts[0].execution_id(),
            2,
            wire::ExecutionPhase::Completed,
            5,
        )
        .await;
    let second = fixture.heartbeat(0, 2, 5).await;
    let before = fixture.events.latest_sequence().unwrap();
    fixture.run(vec![first, second]).unwrap();
    fixture.ack(0, 2).await;
    let error = tokio::time::timeout(Duration::from_secs(2), fixture.peers[1].incoming.message())
        .await
        .unwrap()
        .unwrap_err();
    assert_eq!(error.code(), tonic::Code::FailedPrecondition);
    assert!(fixture.events.latest_sequence().unwrap() > before);
    assert_eq!(
        fixture
            .controller
            .lock()
            .unwrap()
            .bridge
            .execution_status(attempts[0].execution_id()),
        Some(orchestration::RemoteExecutionStatus::Running)
    );
    assert!(
        !fixture
            .router
            .session_is_current("worker-1", &fixture.peers[1].session)
            .unwrap()
    );
    assert!(
        fixture
            .router
            .session_is_current("worker-0", &fixture.peers[0].session)
            .unwrap()
    );
}

/// A batch containing only rejected facts still fences every offending session after rollback.
#[tokio::test]
async fn all_rejected_facts_fence_every_session_without_commit() {
    let mut fixture = Fixture::new().await;
    fixture.submit(None);
    let attempts = fixture.start().await;
    let first = fixture
        .execution(
            1,
            attempts[0].execution_id(),
            2,
            wire::ExecutionPhase::Completed,
            5,
        )
        .await;
    let second = fixture
        .execution(
            0,
            attempts[1].execution_id(),
            2,
            wire::ExecutionPhase::Completed,
            5,
        )
        .await;
    let before = fixture.events.latest_sequence().unwrap();
    fixture.run(vec![first, second]).unwrap();
    assert_eq!(fixture.events.latest_sequence().unwrap(), before);
    assert!(
        !fixture
            .router
            .session_is_current("worker-0", &fixture.peers[0].session)
            .unwrap()
    );
    assert!(
        !fixture
            .router
            .session_is_current("worker-1", &fixture.peers[1].session)
            .unwrap()
    );
}

/// Real runtime violation holds A inside the nested Mission/input savepoints while B finishes.
#[tokio::test]
async fn deadline_isolation_nests_with_input_savepoints_without_losing_raw_facts() {
    let mut fixture = Fixture::new().await;
    fixture.submit(Some(8));
    let attempts = fixture.start().await;
    let first = fixture
        .execution(
            0,
            attempts[0].execution_id(),
            2,
            wire::ExecutionPhase::Completed,
            10,
        )
        .await;
    let second = fixture
        .execution(
            1,
            attempts[1].execution_id(),
            2,
            wire::ExecutionPhase::Completed,
            11,
        )
        .await;
    fixture.clock.0.store(12, Ordering::Relaxed);
    fixture.run(vec![first, second]).unwrap();
    fixture.ack(0, 2).await;
    fixture.ack(1, 2).await;
    let live = fixture.controller.lock().unwrap();
    assert!(
        live.orchestrator
            .execution(&domain::MissionId::new("mission-a").unwrap())
            .unwrap()
            .isolation()
            .is_some()
    );
    assert_eq!(
        live.orchestrator
            .execution(&domain::MissionId::new("mission-b").unwrap())
            .unwrap()
            .lifecycle(),
        orchestration::MissionExecutionLifecycle::Completed
    );
    assert_eq!(
        live.bridge.execution_status(attempts[0].execution_id()),
        Some(orchestration::RemoteExecutionStatus::Completed)
    );
    assert_eq!(
        live.bridge
            .control()
            .allocation_snapshot(domain::TimestampMs::new(12))
            .unwrap()
            .allocations()
            .len(),
        1
    );
}

/// A bounded collector admits a second original session without changing its receive metadata.
#[tokio::test]
async fn bounded_window_collects_real_sessions_before_durable_acceptance() {
    let mut fixture = Fixture::new().await;
    let first = fixture.heartbeat(0, 1, 3).await;
    let peer = &fixture.peers[1];
    peer.sender
        .send(wire::NodeMessage {
            message: Some(NodePayload::Heartbeat(wire::Heartbeat {
                session_id: peer.session.clone(),
                lease_id: peer.lease.clone(),
                sequence: 1,
                status: Some(wire::NodeStatus {
                    health: "online".into(),
                    detail: String::new(),
                }),
            })),
        })
        .unwrap();
    let (deliveries, barrier) =
        collect_fact_batch(first, &mut fixture.receiver, 8, Duration::from_millis(5)).await;
    assert_eq!(deliveries.len(), 2);
    assert!(barrier.is_none());
    fixture.run(deliveries).unwrap();
    fixture.ack(0, 1).await;
    fixture.ack(1, 1).await;
}

/// A registration update remains the next independent transaction when it follows ordinary input.
#[tokio::test]
async fn collector_preserves_registration_update_barrier_order() {
    let mut fixture = Fixture::new().await;
    let first = fixture.heartbeat(0, 1, 3).await;
    let peer = &fixture.peers[1];
    peer.sender
        .send(wire::NodeMessage {
            message: Some(NodePayload::RegistrationUpdate(wire::RegistrationUpdate {
                session_id: peer.session.clone(),
                sequence: 1,
                registration: Some(registration(1)),
            })),
        })
        .unwrap();
    let (deliveries, barrier) =
        collect_fact_batch(first, &mut fixture.receiver, 8, Duration::from_millis(5)).await;
    assert_eq!(deliveries.len(), 1);
    let barrier = barrier.expect("update retained for the next transaction");
    assert!(fact_is_barrier(barrier.event()));
    fixture.run(deliveries).unwrap();
    fixture.ack(0, 1).await;
    fixture.run(vec![barrier]).unwrap();
    fixture.ack(1, 1).await;
}
