//! Real loopback Node Protocol command delivery; peers report facts but run no physical work.

use super::*;
use integration::grpc::v0_4::{self as wire, server_message::Message as ServerPayload};
use tokio::sync::mpsc;
use tokio_stream::wrappers::{TcpListenerStream, UnboundedReceiverStream};

/// One negotiated formal peer, with independent durable-command and execution observations.
struct Peer {
    /// Stream accepted by the real Node Protocol server.
    sender: mpsc::UnboundedSender<wire::NodeMessage>,
    /// Actual Controller commands and acknowledgements.
    incoming: tonic::Streaming<wire::ServerMessage>,
    /// Server-issued session fence.
    session_id: String,
    /// Server-issued lease used by normal heartbeats.
    lease_id: String,
}

/// Owns real HTTP/application state, gRPC transport and explicitly bounded synthetic peers.
struct TransportFixture {
    /// The actual scheduling, reservations and checkpoint implementation.
    fixture: Fixture,
    /// Validated facts awaiting Controller durable acceptance.
    receiver: mpsc::UnboundedReceiver<integration::GrpcNodeEventDelivery>,
    /// Scoped server stopped when the test ends, including a failed assertion.
    server: tokio::task::JoinHandle<Result<(), tonic::transport::Error>>,
    /// Original-owner protocol connections.
    peers: Vec<Peer>,
}

impl Drop for TransportFixture {
    /// Prevents a test-only listener surviving its evidence scope.
    fn drop(&mut self) {
        self.server.abort();
    }
}

/// Projects the fixture's exact deployment metadata through the current Node Contract.
fn wire_registration(index: usize) -> wire::NodeRegistration {
    let registration = registration(index, true, 2);
    wire::NodeRegistration {
        node_id: format!("node-{index}"),
        local_systems: registration
            .local_systems()
            .iter()
            .map(|owner| wire::LocalSystemDescriptor {
                id: owner.id().to_string(),
                runtime: Some(wire::LocalRuntime {
                    name: "fixture".into(),
                    version: "1".into(),
                }),
                metadata: owner
                    .metadata()
                    .iter()
                    .map(|(key, value)| (key.clone(), value.clone()))
                    .collect(),
            })
            .collect(),
        node_contract_version: wire::NODE_CONTRACT_VERSION.into(),
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
            capacity: 2,
            local_system_id: "worker-runtime".into(),
            metadata: Default::default(),
        }],
        ..Default::default()
    }
}

impl TransportFixture {
    /// Negotiates real routes and heartbeats without starting Node Service, a Provider or Habitat.
    async fn new() -> Self {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let (sender, receiver) = mpsc::unbounded_channel();
        let (service, router) = integration::GrpcIntegrationService::new(sender);
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
        let fixture = Fixture::with_router(2, true, false, router);
        let mut scope = Self {
            fixture,
            receiver,
            server,
            peers: Vec::new(),
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
                        node_id: format!("node-{index}"),
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
                        registration: Some(wire_registration(index)),
                    })),
                })
                .unwrap();
            scope.accept_next(5).await;
            let Some(ServerPayload::Registered(registered)) =
                incoming.message().await.unwrap().unwrap().message
            else {
                panic!("expected durable registration");
            };
            scope.peers.push(Peer {
                sender,
                incoming,
                session_id: registered.session_id,
                lease_id: registered.lease_id,
            });
            let peer = &scope.peers[index];
            let heartbeat = NodePayload::Heartbeat(wire::Heartbeat {
                session_id: peer.session_id.clone(),
                lease_id: peer.lease_id.clone(),
                sequence: 1,
                status: Some(wire::NodeStatus {
                    health: "online".into(),
                    detail: String::new(),
                }),
            });
            scope.send_fact(index, heartbeat, 1, 6).await;
        }
        scope.fixture.authorize_at(10);
        scope.persist();
        scope
    }

    /// Persists explicit authorization before any command is delivered.
    fn persist(&self) {
        let checkpoint = server_checkpoint_json(&self.fixture.controller.lock().unwrap()).unwrap();
        self.fixture.events.begin_batch().unwrap();
        self.fixture
            .events
            .save_checkpoint(SERVER_CHECKPOINT_SCHEMA, &checkpoint)
            .unwrap();
        self.fixture.events.commit_batch().unwrap();
    }

    /// Accepts a real transport fact only after existing application authorities and DB Commit.
    async fn accept_next(&mut self, now: u64) {
        let delivery = tokio::time::timeout(Duration::from_secs(2), self.receiver.recv())
            .await
            .unwrap()
            .unwrap();
        let (event, completion) = delivery.into_parts();
        self.fixture.events.begin_batch().unwrap();
        let mut live = self.fixture.controller.lock().unwrap();
        let mut candidate = live.clone();
        candidate
            .bridge
            .consume(event, TimestampMs::new(now), &self.fixture.correlation)
            .unwrap();
        apply_runtime_events(
            &mut candidate,
            TimestampMs::new(now),
            &self.fixture.correlation,
            &mut self.fixture.events.clone(),
        )
        .unwrap();
        self.fixture
            .events
            .save_checkpoint(
                SERVER_CHECKPOINT_SCHEMA,
                &server_checkpoint_json(&candidate).unwrap(),
            )
            .unwrap();
        self.fixture.events.commit_batch().unwrap();
        *live = candidate;
        drop(live);
        completion.accept();
    }

    /// Sends one fact with its real session fence and waits for durable application Ack.
    async fn send_fact(&mut self, index: usize, payload: NodePayload, sequence: u64, now: u64) {
        self.peers[index]
            .sender
            .send(wire::NodeMessage {
                message: Some(payload),
            })
            .unwrap();
        self.accept_next(now).await;
        let message = self.peers[index].incoming.message().await.unwrap().unwrap();
        assert!(matches!(message.message,Some(ServerPayload::Ack(ack)) if ack.sequence==sequence));
    }

    /// Gets the actual Cancel and proves its durable receipt is separate from physical stopping.
    async fn receive_cancel_receipts(&mut self) {
        for index in 0..2 {
            let message = self.peers[index].incoming.message().await.unwrap().unwrap();
            let Some(ServerPayload::Cancel(cancel)) = message.message else {
                panic!("expected actual Cancel");
            };
            let original = self
                .fixture
                .originals
                .iter()
                .find(|attempt| attempt.command().node_id().as_str() == format!("node-{index}"))
                .unwrap();
            assert_eq!(cancel.execution_id, original.execution_id());
            let receipt = NodePayload::CommandReceipt(wire::CommandReceipt {
                session_id: self.peers[index].session_id.clone(),
                sequence: 2,
                command_id: cancel.command_id,
                execution_id: cancel.execution_id,
                kind: wire::CommandKind::CommandCancel as i32,
                status: wire::CommandReceiptStatus::CommandPersisted as i32,
                reason: "test durable journal receipt".into(),
            });
            self.send_fact(index, receipt, 2, 12).await;
        }
        assert_eq!(
            self.fixture
                .controller
                .lock()
                .unwrap()
                .bridge
                .group_recovery_disposition(&self.fixture.group_id, TimestampMs::new(13)),
            runtime::GroupRecoveryDisposition::AwaitingStop
        );
        assert_eq!(
            self.fixture
                .controller
                .lock()
                .unwrap()
                .bridge
                .attempt_history()
                .len(),
            2
        );
    }

    /// Reports all actual stopped original attempts over the formal protocol, not receipt inference.
    async fn stop_all(&mut self) {
        for index in 0..2 {
            let original = self
                .fixture
                .originals
                .iter()
                .find(|attempt| attempt.command().node_id().as_str() == format!("node-{index}"))
                .unwrap();
            let payload = NodePayload::ExecutionSnapshot(wire::ExecutionSnapshot {
                session_id: self.peers[index].session_id.clone(),
                execution_id: original.execution_id().into(),
                last_sequence: 2,
                phase: ExecutionPhase::Cancelled as i32,
                reason: "actual test-owner stop".into(),
            });
            self.send_fact(index, payload, 2, 20 + index as u64).await;
        }
    }

    /// Verifies the new wire invocation, session and resources remain exactly originally committed.
    async fn receive_replacements(&mut self) {
        for index in 0..2 {
            let message = self.peers[index].incoming.message().await.unwrap().unwrap();
            let Some(ServerPayload::Execute(execute)) = message.message else {
                panic!("expected actual new Execute");
            };
            let original = self
                .fixture
                .originals
                .iter()
                .find(|attempt| attempt.command().node_id().as_str() == format!("node-{index}"))
                .unwrap();
            assert_ne!(execute.execution_id, original.execution_id());
            assert_eq!(execute.resource_ids, vec![format!("cpu-{index}")]);
            assert_eq!(
                execute.execution_session_json,
                serde_json::to_string(original.command().session().unwrap()).unwrap()
            );
            let invocation = execute.invocation.unwrap();
            assert_eq!(
                invocation.mission_id,
                original.command().mission_id().as_str()
            );
            assert_eq!(invocation.task_id, original.command().task_id().as_str());
            assert_eq!(
                invocation.intent.unwrap().objective,
                original.command().intent().objective()
            );
            let payload = NodePayload::ExecutionSnapshot(wire::ExecutionSnapshot {
                session_id: self.peers[index].session_id.clone(),
                execution_id: execute.execution_id,
                last_sequence: 1,
                phase: ExecutionPhase::Accepted as i32,
                reason: "actual test-owner invocation admission".into(),
            });
            self.send_fact(index, payload, 1, 25).await;
        }
        assert_eq!(
            self.fixture
                .controller
                .lock()
                .unwrap()
                .bridge
                .group_recovery_disposition(&self.fixture.group_id, TimestampMs::new(26)),
            runtime::GroupRecoveryDisposition::Continued
        );
    }
}

/// End-to-end command transport preserves the stop barrier and exact retained invocation bindings.
#[tokio::test]
async fn actual_grpc_receipts_do_not_stop_a_group_and_only_full_facts_enable_execute() {
    tokio::time::timeout(Duration::from_secs(10), async {
        let mut scope = TransportFixture::new().await;
        let ownership = scope.fixture.reservations();
        assert_eq!(
            scope
                .fixture
                .controller
                .lock()
                .unwrap()
                .bridge
                .flush_command_outboxes_at(TimestampMs::new(11))
                .unwrap(),
            2
        );
        scope.receive_cancel_receipts().await;
        scope.stop_all().await;
        scope.fixture.tick(22).unwrap();
        assert_eq!(scope.fixture.reservations(), ownership);
        scope.receive_replacements().await;
    })
    .await
    .unwrap();
}

/// One clock for preparation and a fresh post-Commit clock fence a deadline crossed during storage.
struct CommitCrossingClock {
    /// First read prepares within budget; the next read is exactly at the original deadline.
    reads: std::sync::atomic::AtomicUsize,
    /// First read that reaches expiry, including the initial transition-time read.
    first_expired_read: usize,
}

impl Clock for CommitCrossingClock {
    /// Models a slow checkpoint without sleeping or changing the recovery budget.
    fn now(&self) -> TimestampMs {
        let index = self.reads.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
        TimestampMs::new(if index == 0 {
            22
        } else if index < self.first_expired_read {
            25
        } else {
            110
        })
    }
}

/// Budget expiry between persistence and delivery never sends fresh Execute to negotiated peers.
#[tokio::test]
async fn slow_commit_does_not_deliver_expired_continuation() {
    tokio::time::timeout(Duration::from_secs(10), async {
        let mut scope = TransportFixture::new().await;
        scope.stop_all().await;
        let clock = CommitCrossingClock {
            reads: std::sync::atomic::AtomicUsize::new(0),
            first_expired_read: 1,
        };
        drive_application_timer_with_clock(
            &scope.fixture.controller,
            &scope.fixture.events,
            &Arc::new(Mutex::new(())),
            &clock,
            None,
        )
        .unwrap();
        assert_eq!(
            scope
                .fixture
                .controller
                .lock()
                .unwrap()
                .bridge
                .attempt_history()
                .len(),
            4
        );
        assert_eq!(
            scope
                .fixture
                .controller
                .lock()
                .unwrap()
                .bridge
                .group_recovery_disposition(&scope.fixture.group_id, TimestampMs::new(110)),
            runtime::GroupRecoveryDisposition::BudgetExpired
        );
        for peer in &mut scope.peers {
            assert!(
                tokio::time::timeout(Duration::from_millis(40), peer.incoming.message())
                    .await
                    .is_err()
            );
        }
    })
    .await
    .unwrap();
}

/// Network sends are asynchronous; every command in the prepared set gets its own deadline check.
#[tokio::test]
async fn expiry_between_two_execute_sends_fences_the_remaining_outbox() {
    tokio::time::timeout(Duration::from_secs(10), async {
        let mut scope = TransportFixture::new().await;
        scope.stop_all().await;
        let ownership = scope.fixture.reservations();
        let clock = CommitCrossingClock {
            reads: std::sync::atomic::AtomicUsize::new(0),
            first_expired_read: 2,
        };
        drive_application_timer_with_clock(
            &scope.fixture.controller,
            &scope.fixture.events,
            &Arc::new(Mutex::new(())),
            &clock,
            None,
        )
        .unwrap();
        assert!(matches!(
            scope.peers[0]
                .incoming
                .message()
                .await
                .unwrap()
                .unwrap()
                .message,
            Some(ServerPayload::Execute(_))
        ));
        assert!(
            tokio::time::timeout(Duration::from_millis(40), scope.peers[1].incoming.message())
                .await
                .is_err()
        );
        assert_eq!(scope.fixture.reservations(), ownership);
        assert_eq!(
            scope
                .fixture
                .controller
                .lock()
                .unwrap()
                .bridge
                .group_recovery_disposition(&scope.fixture.group_id, TimestampMs::new(110)),
            runtime::GroupRecoveryDisposition::BudgetExpired
        );
    })
    .await
    .unwrap();
}
