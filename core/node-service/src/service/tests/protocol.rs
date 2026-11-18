//! Node protocol registration, health, and observation tests.

use super::*;

/// Node health reports configured local-system unavailability instead of process liveness.
#[tokio::test]
async fn node_health_reports_local_runtime_unavailability() {
    let state_dir = tempfile::tempdir().expect("state directory exists");
    let engine = crate::LocalIntegrationEngine::new(
        gated_catalog(
            "http://127.0.0.1:50051".to_string(),
            state_dir.path().to_path_buf(),
        ),
        vec![Arc::new(OfflineHealthDriver) as Arc<dyn LocalDriver>],
    )
    .expect("engine initializes");
    let status = engine.status().await;
    assert_eq!(status.health, "offline");
    assert!(status.detail.contains("local runtime is unavailable"));
}

/// Exact readiness changes registration availability without changing healthy process state.
#[tokio::test]
async fn capability_readiness_is_observed_independently_from_health() {
    let state_dir = tempfile::tempdir().expect("state directory exists");
    let ready = Arc::new(AtomicBool::new(false));
    let engine = crate::LocalIntegrationEngine::new(
        gated_catalog_with_artifacts(
            "http://127.0.0.1:50051".to_string(),
            state_dir.path().to_path_buf(),
            None,
            true,
        ),
        vec![Arc::new(GatedDriver::new(ready.clone())) as Arc<dyn LocalDriver>],
    )
    .expect("engine initializes");

    let unavailable = engine.observe().await;
    assert_eq!(unavailable.status().health, "online");
    let unavailable_registration = registration_from_observation(engine.catalog(), &unavailable);
    assert!(!unavailable_registration.capabilities[0].available);

    ready.store(true, Ordering::SeqCst);
    let available = engine.observe().await;
    assert_eq!(available.status().health, "online");
    let available_registration = registration_from_observation(engine.catalog(), &available);
    assert!(available_registration.capabilities[0].available);

    let mut sequence = 0;
    let mut previous = readiness_snapshot(&unavailable);
    let changed = management_messages(
        "session-a",
        "lease-a",
        &mut sequence,
        engine.catalog(),
        &available,
        &mut previous,
    );
    assert_eq!(changed.len(), 2);
    assert!(matches!(
        changed[0].message,
        Some(NodePayload::RegistrationUpdate(RegistrationUpdate {
            sequence: 1,
            ..
        }))
    ));
    assert!(matches!(
        changed[1].message,
        Some(NodePayload::Heartbeat(Heartbeat { sequence: 2, .. }))
    ));
    let unchanged = management_messages(
        "session-a",
        "lease-a",
        &mut sequence,
        engine.catalog(),
        &available,
        &mut previous,
    );
    assert!(matches!(
        unchanged.as_slice(),
        [NodeMessage {
            message: Some(NodePayload::Heartbeat(Heartbeat { sequence: 3, .. }))
        }]
    ));
}

/// Node Service attaches session ordering without interpreting Local EAIOS channel semantics.
#[test]
fn peer_readiness_fact_maps_to_identified_protocol_message() {
    let message = peer_readiness_message(
        "session-peer",
        7,
        crate::LocalPeerChannelReadiness {
            group_id: "group-a".to_string(),
            context_id: "guidance".to_string(),
            context_role_id: "dog".to_string(),
            local_system_id: "motion".to_string(),
            channel_instance_id: "channel-a".to_string(),
            profile_id: "guidance-peer".to_string(),
            message_schema: "guidance/v1".to_string(),
            ready: true,
            valid_for_ms: 5_000,
        },
    );

    assert!(matches!(
        message.message,
        Some(NodePayload::PeerChannelReadiness(PeerChannelReadiness {
            session_id,
            sequence: 7,
            context_role_id,
            ready: true,
            ..
        })) if session_id == "session-peer" && context_role_id == "dog"
    ));
}

/// State samples split deterministically across both record-count and byte limits.
#[test]
fn state_observation_batches_respect_protocol_bounds() {
    let count_limited = (0..65)
        .map(|index| crate::StateExportFact {
            export_id: format!("state-{index:02}"),
            value: serde_json::json!(true),
            source_observed_at_ms: None,
            confidence_millionths: None,
        })
        .collect();
    let batches = state_observation_batches(count_limited);
    assert_eq!(batches.iter().map(Vec::len).collect::<Vec<_>>(), [64, 1]);

    let byte_limited = (0..9)
        .map(|index| crate::StateExportFact {
            export_id: format!("payload-{index}"),
            value: serde_json::json!("x".repeat(60 * 1024)),
            source_observed_at_ms: Some(index),
            confidence_millionths: Some(500_000),
        })
        .collect();
    let batches = state_observation_batches(byte_limited);
    assert_eq!(batches.iter().map(Vec::len).collect::<Vec<_>>(), [8, 1]);
    assert!(batches.iter().all(|batch| {
        batch
            .iter()
            .map(|observation| observation.json_value.len())
            .sum::<usize>()
            <= MAX_STATE_BATCH_BYTES
    }));
}

/// Oversized public facts fail closed before transport if a caller bypasses local mapping.
#[test]
fn state_observation_batches_drop_oversized_fact() {
    let batches = state_observation_batches(vec![crate::StateExportFact {
        export_id: "oversized".to_string(),
        value: serde_json::json!("x".repeat(domain::MAX_STATE_PAYLOAD_BYTES)),
        source_observed_at_ms: None,
        confidence_millionths: None,
    }]);
    assert!(batches.is_empty());
}

/// Resource IDs are an unordered semantic set for execution identity.
#[tokio::test]
async fn execution_identity_canonicalizes_resource_order() {
    let state_dir = tempfile::tempdir().expect("state directory exists");
    let engine = crate::LocalIntegrationEngine::new(
        gated_catalog(
            "http://127.0.0.1:50051".to_string(),
            state_dir.path().to_path_buf(),
        ),
        vec![Arc::new(GatedDriver::new(Arc::new(AtomicBool::new(false)))) as Arc<dyn LocalDriver>],
    )
    .expect("engine initializes");
    let invocation = integration::grpc::v0_4::CanonicalInvocation {
        resource_grants_json: String::new(),
        mission_id: "mission-a".to_string(),
        task_id: "task-a".to_string(),
        group_id: "group-a".to_string(),
        role_id: "carrier".to_string(),
        capability_contract: "mobility.reach_region@v1".to_string(),
        ..Default::default()
    };
    assert_eq!(
        engine
            .execute(
                "resource-order".to_string(),
                invocation.clone(),
                vec!["aux".to_string(), "base".to_string()]
            )
            .expect("first dispatch starts"),
        crate::ExecuteDisposition::Started
    );
    assert!(matches!(
        engine
            .execute(
                "resource-order".to_string(),
                invocation,
                vec!["base".to_string(), "aux".to_string()]
            )
            .expect("same resource set is idempotent"),
        crate::ExecuteDisposition::DispatchPending
    ));
}

/// A transport timeout fences physical ambiguity and never dispatches the same identity twice.
#[tokio::test]
async fn transport_timeout_requires_reconciliation_without_replay() {
    let state_dir = tempfile::tempdir().expect("state directory exists");
    let calls = Arc::new(AtomicUsize::new(0));
    let engine = crate::LocalIntegrationEngine::new(
        gated_catalog(
            "http://127.0.0.1:50051".to_string(),
            state_dir.path().to_path_buf(),
        ),
        vec![Arc::new(TimeoutDriver {
            calls: Arc::clone(&calls),
        }) as Arc<dyn LocalDriver>],
    )
    .expect("engine initializes");
    let invocation = integration::grpc::v0_4::CanonicalInvocation {
        resource_grants_json: String::new(),
        mission_id: "mission-a".to_string(),
        task_id: "task-timeout".to_string(),
        group_id: "group-a".to_string(),
        role_id: "carrier".to_string(),
        capability_contract: "mobility.reach_region@v1".to_string(),
        ..Default::default()
    };
    let mut events = engine.subscribe();
    assert_eq!(
        engine
            .execute(
                "timeout-execution".to_string(),
                invocation.clone(),
                vec!["base".to_string()],
            )
            .expect("timeout dispatch starts"),
        crate::ExecuteDisposition::Started
    );
    let event = tokio::time::timeout(std::time::Duration::from_secs(2), events.recv())
        .await
        .expect("timeout evidence arrives")
        .expect("timeout evidence exists");
    assert_eq!(
        event.phase,
        integration::grpc::v0_4::ExecutionPhase::Unknown
    );
    let journal = engine.owned_journal();
    assert_eq!(
        journal
            .get("timeout-execution")
            .expect("record reads")
            .expect("record exists")
            .status(),
        crate::JournalStatus::ReconciliationRequired
    );
    assert_eq!(calls.load(Ordering::SeqCst), 1);
    assert!(matches!(
        engine.execute(
            "timeout-execution".to_string(),
            invocation,
            vec!["base".to_string()],
        ),
        Ok(crate::ExecuteDisposition::Existing(_))
    ));
    assert_eq!(
        calls.load(Ordering::SeqCst),
        1,
        "repeating an ambiguous identity must not redispatch"
    );
}

/// Controller rejection prevents `Registered` and removes the pending command route.
#[tokio::test]
async fn controller_registration_rejection_is_visible_to_node() {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("listener binds");
    let address = listener.local_addr().expect("listener address");
    let incoming = tokio_stream::wrappers::TcpListenerStream::new(listener);
    let (events, mut event_receiver) = tokio::sync::mpsc::unbounded_channel();
    let (grpc_service, router) = integration::GrpcIntegrationService::new(events);
    let server = tokio::spawn(async move {
        tonic::transport::Server::builder()
                .add_service(
                    integration::grpc::v0_4::robo_guide_node_protocol_server::RoboGuideNodeProtocolServer::new(
                        grpc_service,
                    ),
                )
                .serve_with_incoming(incoming)
                .await
    });
    let state_dir = tempfile::tempdir().expect("state directory exists");
    let terminal = Arc::new(AtomicBool::new(false));
    let engine = crate::LocalIntegrationEngine::new(
        gated_catalog(format!("http://{address}"), state_dir.path().to_path_buf()),
        vec![Arc::new(GatedDriver::new(terminal)) as Arc<dyn LocalDriver>],
    )
    .expect("engine initializes");
    let node_task = tokio::spawn(async move { NodeService::new(engine).run_session().await });

    let delivery = tokio::time::timeout(std::time::Duration::from_secs(2), event_receiver.recv())
        .await
        .expect("registration arrives")
        .expect("registration delivery exists");
    let (event, completion) = delivery.into_parts();
    assert!(matches!(
        event,
        integration::GrpcNodeEvent::Registered { .. }
    ));
    completion.reject("global resource conflict");

    let error = tokio::time::timeout(std::time::Duration::from_secs(2), node_task)
        .await
        .expect("Node observes rejection")
        .expect("Node task joins")
        .expect_err("registration does not succeed");
    assert!(matches!(
        error,
        NodeServiceError::Status(status)
            if status.code() == tonic::Code::FailedPrecondition
                && status.message().contains("global resource conflict")
    ));
    assert_eq!(
        router
            .cancel(
                "dog-a",
                "cancel-execution-rejected".to_string(),
                "execution-rejected".to_string(),
            )
            .expect_err("rejected registration has no route")
            .code(),
        tonic::Code::Unavailable
    );
    server.abort();
}

/// Retain the latest sample per export without dropping other exports.
#[test]
fn coalesced_state_outbox_overwrites_stale_snapshots_by_export_id() {
    let outbox = CoalescedStateOutbox::default();
    assert!(!outbox.has_pending());
    outbox.publish(vec![
        crate::StateExportFact {
            export_id: "chassis".to_string(),
            value: serde_json::json!({"seq": 1}),
            source_observed_at_ms: Some(10),
            confidence_millionths: None,
        },
        crate::StateExportFact {
            export_id: "battery".to_string(),
            value: serde_json::json!({"pct": 90}),
            source_observed_at_ms: Some(11),
            confidence_millionths: None,
        },
    ]);
    outbox.publish(vec![crate::StateExportFact {
        export_id: "chassis".to_string(),
        value: serde_json::json!({"seq": 2}),
        source_observed_at_ms: Some(20),
        confidence_millionths: None,
    }]);
    assert!(outbox.has_pending());
    let taken = outbox.take_all();
    assert!(!outbox.has_pending());
    assert_eq!(taken.len(), 2);
    let by_id: BTreeMap<_, _> = taken
        .into_iter()
        .map(|fact| (fact.export_id, fact.value))
        .collect();
    assert_eq!(by_id["chassis"], serde_json::json!({"seq": 2}));
    assert_eq!(by_id["battery"], serde_json::json!({"pct": 90}));
}

/// Derive the stall window from the negotiated lease and heartbeat interval.
#[test]
fn inbound_liveness_timeout_respects_welcome_lease_and_floor() {
    let mut welcome = integration::grpc::v0_4::Welcome::default();
    assert_eq!(inbound_liveness_timeout(&welcome), None);

    welcome.heartbeat_interval_ms = 1_000;
    welcome.lease_duration_ms = 15_000;
    assert_eq!(
        inbound_liveness_timeout(&welcome),
        Some(Duration::from_secs(15))
    );

    welcome.heartbeat_interval_ms = 2_000;
    welcome.lease_duration_ms = 4_000;
    assert_eq!(
        inbound_liveness_timeout(&welcome),
        Some(Duration::from_secs(6))
    );
}

/// Under wire backpressure, send priority frames before queued State snapshots.
#[tokio::test]
async fn outbound_pump_prioritizes_control_frames_ahead_of_coalesced_state() {
    let (priority_tx, priority_rx) = mpsc::unbounded_channel();
    let (wire_tx, mut wire_rx) = mpsc::channel(1);
    let state_outbox = Arc::new(CoalescedStateOutbox::default());
    let sequence = Arc::new(tokio::sync::Mutex::new(0_u64));
    let session_id = Arc::new(std::sync::Mutex::new(Some("session-1".to_string())));
    let pump = spawn_outbound_pump(
        priority_rx,
        wire_tx,
        Arc::clone(&state_outbox),
        Arc::clone(&sequence),
        session_id,
    );

    priority_tx
        .send(NodeMessage {
            message: Some(NodePayload::Heartbeat(Heartbeat {
                session_id: "session-1".to_string(),
                lease_id: "lease-1".to_string(),
                sequence: 1,
                ..Default::default()
            })),
        })
        .expect("initial heartbeat queues");
    tokio::task::yield_now().await;

    for sample in 1..=10 {
        state_outbox.publish(vec![crate::StateExportFact {
            export_id: "robot-state".to_string(),
            value: serde_json::json!({"sample": sample}),
            source_observed_at_ms: Some(sample),
            confidence_millionths: None,
        }]);
    }
    {
        let mut guard = sequence.lock().await;
        *guard = 2;
        priority_tx
            .send(NodeMessage {
                message: Some(NodePayload::Heartbeat(Heartbeat {
                    session_id: "session-1".to_string(),
                    lease_id: "lease-1".to_string(),
                    sequence: *guard,
                    ..Default::default()
                })),
            })
            .expect("priority heartbeat queues");
    }

    let first = tokio::time::timeout(Duration::from_secs(2), wire_rx.recv())
        .await
        .expect("first frame arrives")
        .expect("stream open");
    assert!(matches!(
        first.message,
        Some(NodePayload::Heartbeat(Heartbeat { sequence: 1, .. }))
    ));

    let second = tokio::time::timeout(Duration::from_secs(2), wire_rx.recv())
        .await
        .expect("priority frame overtakes queued state")
        .expect("stream open");
    assert!(matches!(
        second.message,
        Some(NodePayload::Heartbeat(Heartbeat { sequence: 2, .. }))
    ));

    let third = tokio::time::timeout(Duration::from_secs(2), wire_rx.recv())
        .await
        .expect("coalesced state arrives")
        .expect("stream open");
    let Some(NodePayload::StateObservationBatch(batch)) = third.message else {
        panic!("expected coalesced StateObservationBatch");
    };
    assert_eq!(batch.sequence, 3);
    assert_eq!(batch.observations.len(), 1);
    assert_eq!(batch.observations[0].json_value, br#"{"sample":10}"#);
    pump.abort();
}

/// Silent mock server simulating a blackholed half-open roam link after Registered.
struct SilentSessionServer;

#[tonic::async_trait]
impl integration::grpc::v0_4::robo_guide_node_protocol_server::RoboGuideNodeProtocol
    for SilentSessionServer
{
    type NodeSessionStream = std::pin::Pin<
        Box<
            dyn tokio_stream::Stream<
                    Item = Result<integration::grpc::v0_4::ServerMessage, tonic::Status>,
                > + Send,
        >,
    >;

    /// Register the test session, then hold its inbound stream open without acknowledgements.
    async fn node_session(
        &self,
        request: tonic::Request<tonic::Streaming<integration::grpc::v0_4::NodeMessage>>,
    ) -> Result<tonic::Response<Self::NodeSessionStream>, tonic::Status> {
        let mut inbound = request.into_inner();
        let (tx, rx) = mpsc::channel(8);
        tokio::spawn(async move {
            let hello = inbound.message().await.ok().flatten();
            let contract = match hello.and_then(|m| m.message) {
                Some(NodePayload::Hello(h)) => h
                    .node_contract_versions
                    .into_iter()
                    .next()
                    .unwrap_or_else(|| {
                        integration::grpc::v0_4::LEGACY_NODE_CONTRACT_VERSION.to_string()
                    }),
                _ => integration::grpc::v0_4::LEGACY_NODE_CONTRACT_VERSION.to_string(),
            };
            let _ = tx
                .send(Ok(ServerMessage {
                    message: Some(ServerPayload::Welcome(integration::grpc::v0_4::Welcome {
                        server_version: "test-server".to_string(),
                        selected_protocol_version: PROTOCOL_VERSION.to_string(),
                        selected_node_contract_version: contract,
                        heartbeat_interval_ms: 50,
                        lease_duration_ms: 150,
                    })),
                }))
                .await;
            let _ = inbound.message().await;
            let _ = tx
                .send(Ok(ServerMessage {
                    message: Some(ServerPayload::Registered(
                        integration::grpc::v0_4::Registered {
                            session_id: "silent-session".to_string(),
                            lease_id: "silent-lease".to_string(),
                        },
                    )),
                }))
                .await;
            while let Ok(Some(_)) = inbound.message().await {}
        });
        Ok(tonic::Response::new(Box::pin(ReceiverStream::new(rx))))
    }
}

/// End an unresponsive registered session so the outer loop can reconnect.
#[tokio::test]
async fn stalled_inbound_stream_closes_session_for_fast_reconnect() {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("listener binds");
    let address = listener.local_addr().expect("listener address");
    let incoming = tokio_stream::wrappers::TcpListenerStream::new(listener);
    let server = tokio::spawn(async move {
        tonic::transport::Server::builder()
            .add_service(
                integration::grpc::v0_4::robo_guide_node_protocol_server::RoboGuideNodeProtocolServer::new(
                    SilentSessionServer,
                ),
            )
            .serve_with_incoming(incoming)
            .await
    });
    let state_dir = tempfile::tempdir().expect("state directory exists");
    let engine = crate::LocalIntegrationEngine::new(
        gated_catalog(format!("http://{address}"), state_dir.path().to_path_buf()),
        vec![Arc::new(GatedDriver::new(Arc::new(AtomicBool::new(false)))) as Arc<dyn LocalDriver>],
    )
    .expect("engine initializes");
    let error = tokio::time::timeout(
        Duration::from_secs(2),
        NodeService::new(engine).run_session(),
    )
    .await
    .expect("stalled session must terminate within lease window")
    .expect_err("stalled session returns reconnectable error");
    assert!(
        matches!(error, NodeServiceError::Protocol(ref msg) if msg.contains("stalled")),
        "unexpected error: {error}"
    );
    server.abort();
}
