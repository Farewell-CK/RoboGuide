//! Node Service execution routing and restart tests.

use super::*;

/// Control-bound command reaches the local engine and terminates only on a local terminal fact.
#[tokio::test]
async fn control_bound_command_round_trips_through_generic_engine() {
    use control::{BoundedJointScheduler, ControlPlane};
    use domain::{
        ActorId, Capability, CapabilityContractRef, CapabilityKind, CorrelationId,
        ExecutionGroupId, ExecutionIntent, ExecutionValue, LocalRuntime, MissionId,
        NodeContractVersion, NodeHealth, NodeId, NodeRegistration as DomainRegistration,
        NodeStatus as DomainStatus, Resource as DomainResource, ResourceId, ResourceKind, RoleId,
        RoleRequirement, TaskId, TaskRequirement, TimestampMs,
    };
    use integration::GrpcIntegrationService;
    use integration::grpc::v0_4::CanonicalInvocation;
    use integration::grpc::v0_4::robo_guide_node_protocol_server::RoboGuideNodeProtocolServer;
    use orchestration::{IntegrationRuntimeBridge, RemoteExecutionStatus};
    use state::InMemorySharedNodeState;
    use testkit::InMemoryEventLog;

    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("listener binds");
    let address = listener.local_addr().expect("listener address");
    let incoming = tokio_stream::wrappers::TcpListenerStream::new(listener);
    let (events, mut event_receiver) = tokio::sync::mpsc::unbounded_channel();
    let (grpc_service, router) = GrpcIntegrationService::new(events);
    let server = tokio::spawn(async move {
        tonic::transport::Server::builder()
            .add_service(RoboGuideNodeProtocolServer::new(grpc_service))
            .serve_with_incoming(incoming)
            .await
    });
    let terminal = Arc::new(AtomicBool::new(false));
    let state_dir = tempfile::tempdir().expect("state directory exists");
    let engine = crate::LocalIntegrationEngine::new(
        gated_catalog(format!("http://{address}"), state_dir.path().to_path_buf()),
        vec![Arc::new(GatedDriver {
            completed: Arc::clone(&terminal),
        }) as Arc<dyn LocalDriver>],
    )
    .expect("engine initializes");
    let node = NodeService::new(engine.clone());
    let node_task = tokio::spawn(async move { node.run_session().await });

    let now = TimestampMs::new(0);
    let correlation = CorrelationId::new("generic-loop-test").expect("correlation valid");
    let contract =
        CapabilityContractRef::new("mobility", "reach_region", "v1").expect("contract valid");
    let registration = DomainRegistration::new_with_contracts(
        NodeId::new("dog-a").expect("node valid"),
        LocalRuntime::new("configured-runtime", "1").expect("runtime valid"),
        NodeContractVersion::new(integration::grpc::v0_4::NODE_CONTRACT_VERSION)
            .expect("contract version valid"),
        vec![Capability::new(CapabilityKind::Mobility, true)],
        vec![contract.clone()],
        vec![
            DomainResource::new(
                ResourceId::new("base").expect("resource ID is valid"),
                ResourceKind::Space,
                1,
            )
            .expect("resource is valid"),
        ],
    );
    let mut control = ControlPlane::new();
    let mut state = InMemorySharedNodeState::new();
    let mut log = InMemoryEventLog::new();
    control
        .register_node(
            &mut state,
            registration,
            DomainStatus::new(NodeHealth::Online, now),
            now,
            &correlation,
            &mut log,
        )
        .expect("node registered");
    let role_id = RoleId::new("carrier").expect("role valid");
    let requirement = TaskRequirement::new(
        MissionId::new("mission-a").expect("mission valid"),
        TaskId::new("task-a").expect("task valid"),
        vec![RoleRequirement::new_with_actor_and_contract(
            role_id.clone(),
            ActorId::new("carrier").expect("actor valid"),
            CapabilityKind::Mobility,
            contract.clone(),
            Some(ResourceKind::Space),
        )],
    )
    .expect("requirement valid");
    let intent = ExecutionIntent::new(
        contract,
        BTreeMap::from([(
            "region_id".to_string(),
            ExecutionValue::String("library".to_string()),
        )]),
    )
    .expect("intent valid");
    let mission_plan = single_task_plan(requirement.clone(), intent.clone());
    let group_id = ExecutionGroupId::new("group-a").expect("group valid");
    control
        .create_mission_group(group_id.clone(), &mission_plan, now, &correlation, &mut log)
        .expect("Mission Group registers");
    control
        .ready_task_execution(
            &group_id,
            requirement.task_ref(),
            now,
            &correlation,
            &mut log,
        )
        .expect("Task becomes ready");
    let candidates = control
        .match_capabilities(&state, &requirement, now, &correlation, &mut log)
        .expect("matching succeeds");
    let decision = BoundedJointScheduler::new()
        .schedule_task(
            &state,
            &requirement,
            &candidates,
            now,
            &correlation,
            &mut log,
        )
        .expect("scheduling succeeds");
    let proposal = control
        .propose(
            &state,
            &requirement,
            &candidates,
            decision.proposed_assignments(),
            now,
            &correlation,
            &mut log,
        )
        .expect("proposal succeeds");
    let committed = control
        .commit(&proposal, now, &correlation, &mut log)
        .expect("commit succeeds");
    control
        .bind_task_execution_with_requirement(
            &group_id,
            &committed,
            &requirement,
            now,
            &correlation,
            &mut log,
        )
        .expect("Task bound");
    control
        .activate_task_execution(
            &group_id,
            requirement.task_ref(),
            now,
            &correlation,
            &mut log,
        )
        .expect("Task activates");

    let mut bridge = IntegrationRuntimeBridge::new(control, state, log, router);
    let registered = tokio::time::timeout(std::time::Duration::from_secs(2), event_receiver.recv())
        .await
        .expect("registration arrives")
        .expect("registration exists");
    let (registered, registration_completion) = registered.into_parts();
    bridge
        .consume(registered, TimestampMs::new(1), &correlation)
        .expect("registration consumed");
    registration_completion.accept();
    let heartbeat = tokio::time::timeout(std::time::Duration::from_secs(2), event_receiver.recv())
        .await
        .expect("post-registration heartbeat arrives")
        .expect("heartbeat exists");
    let (heartbeat, heartbeat_completion) = heartbeat.into_parts();
    bridge
        .consume(heartbeat, TimestampMs::new(2), &correlation)
        .expect("heartbeat consumed after route activation");
    heartbeat_completion.accept();
    let command = bridge
        .execute_task_bound(
            "execution-e2e".to_string(),
            &group_id,
            requirement.task_ref(),
            &role_id,
            intent,
            TimestampMs::new(3),
            correlation.clone(),
        )
        .expect("bound command prepares");
    bridge
        .flush_command_outboxes()
        .expect("persisted command routes");
    assert_eq!(command.node_id().as_str(), "dog-a");
    while bridge.execution_status("execution-e2e") != Some(RemoteExecutionStatus::Running) {
        let event = tokio::time::timeout(std::time::Duration::from_secs(2), event_receiver.recv())
            .await
            .expect("running event arrives")
            .expect("running event exists");
        let (event, completion) = event.into_parts();
        bridge
            .consume(event, TimestampMs::new(3), &correlation)
            .expect("running event consumed");
        completion.accept();
    }
    assert_ne!(
        bridge.execution_status("execution-e2e"),
        Some(RemoteExecutionStatus::Completed)
    );
    let competing = CanonicalInvocation {
        mission_id: "mission-a".to_string(),
        task_id: "task-b".to_string(),
        group_id: "group-b".to_string(),
        role_id: "carrier".to_string(),
        capability_contract: "mobility.reach_region@v1".to_string(),
        parameters: Default::default(),
    };
    assert!(matches!(
        engine.execute(
            "execution-competing".to_string(),
            competing.clone(),
            vec!["base".to_string()]
        ),
        Err(crate::EngineError::LocalLockConflict { .. })
    ));
    terminal.store(true, Ordering::SeqCst);
    while bridge.execution_status("execution-e2e") != Some(RemoteExecutionStatus::Completed) {
        let event = tokio::time::timeout(std::time::Duration::from_secs(2), event_receiver.recv())
            .await
            .expect("terminal event arrives")
            .expect("terminal event exists");
        let (event, completion) = event.into_parts();
        bridge
            .consume(event, TimestampMs::new(4), &correlation)
            .expect("terminal event consumed");
        completion.accept();
    }
    assert_eq!(
        engine
            .execute(
                "execution-competing".to_string(),
                competing,
                vec!["base".to_string()]
            )
            .expect("terminal execution releases local locks"),
        crate::ExecuteDisposition::Started
    );
    node_task.abort();
    server.abort();
}

/// Ambiguous pre-handle dispatches retain local locks after process restart.
#[tokio::test]
async fn reconciliation_required_execution_fences_local_resources_after_restart() {
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
            .prepare_dispatch("ambiguous", &spec)
            .expect("dispatch is prepared"),
        crate::PrepareDispatch::Start(_)
    ));
    journal
        .authorize_local_dispatch("ambiguous")
        .expect("local dispatch is authorized");
    drop(journal);

    let engine = crate::LocalIntegrationEngine::new(
        catalog,
        vec![Arc::new(GatedDriver {
            completed: Arc::new(AtomicBool::new(false)),
        }) as Arc<dyn LocalDriver>],
    )
    .expect("engine reopens journal");
    engine.recover().expect("ambiguous execution is fenced");
    let competing = integration::grpc::v0_4::CanonicalInvocation {
        mission_id: "mission-a".to_string(),
        task_id: "task-a".to_string(),
        group_id: "group-a".to_string(),
        role_id: "carrier".to_string(),
        capability_contract: "mobility.reach_region@v1".to_string(),
        ..Default::default()
    };
    assert!(matches!(
        engine.execute(
            "new-execution".to_string(),
            competing,
            vec!["base".to_string()]
        ),
        Err(crate::EngineError::LocalLockConflict { .. })
    ));
}

/// A persisted local handle resumes status polling after restart without releasing its locks.
#[tokio::test]
async fn handle_bearing_dispatch_resumes_status_only_recovery_after_restart() {
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
            .prepare_dispatch("handle-recovered", &spec)
            .expect("dispatch is prepared"),
        crate::PrepareDispatch::Start(_)
    ));
    journal
        .authorize_local_dispatch("handle-recovered")
        .expect("local dispatch is authorized");
    journal
        .record_local_handle("handle-recovered", "local-1")
        .expect("local handle persists");
    drop(journal);

    let engine = crate::LocalIntegrationEngine::new(
        catalog,
        vec![Arc::new(GatedDriver {
            completed: Arc::new(AtomicBool::new(false)),
        }) as Arc<dyn LocalDriver>],
    )
    .expect("engine reopens journal");
    let mut events = engine.subscribe();
    engine.recover().expect("known handle resumes polling");
    let event = tokio::time::timeout(std::time::Duration::from_secs(2), events.recv())
        .await
        .expect("running fact arrives")
        .expect("running fact exists");
    assert_eq!(event.execution_id, "handle-recovered");
    assert_eq!(
        event.phase,
        integration::grpc::v0_4::ExecutionPhase::Started
    );

    let competing = integration::grpc::v0_4::CanonicalInvocation {
        mission_id: "mission-a".to_string(),
        task_id: "task-b".to_string(),
        group_id: "group-a".to_string(),
        role_id: "carrier".to_string(),
        capability_contract: "mobility.reach_region@v1".to_string(),
        ..Default::default()
    };
    assert!(matches!(
        engine.execute(
            "new-execution".to_string(),
            competing,
            vec!["base".to_string()]
        ),
        Err(crate::EngineError::LocalLockConflict { .. })
    ));
}
