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
        vec![Arc::new(GatedDriver {
            completed: ready.clone(),
        }) as Arc<dyn LocalDriver>],
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
        vec![Arc::new(GatedDriver {
            completed: Arc::new(AtomicBool::new(false)),
        }) as Arc<dyn LocalDriver>],
    )
    .expect("engine initializes");
    let invocation = integration::grpc::v0_4::CanonicalInvocation {
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
        crate::ExecuteDisposition::Existing(_)
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
    let journal =
        crate::ExecutionJournal::open(crate::journal_path(engine.catalog().state_directory()))
            .expect("journal opens");
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
        vec![Arc::new(GatedDriver {
            completed: terminal,
        }) as Arc<dyn LocalDriver>],
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
