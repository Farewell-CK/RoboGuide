use super::*;

/// A rejected execution cancellation closes its transaction and leaves the writer usable.
#[tokio::test]
async fn unknown_execution_cancel_rolls_back_transaction() {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    let directory = tempfile::tempdir().expect("temporary directory exists");
    let event_log = state::SqliteEventLog::open(directory.path().join("events.sqlite3"))
        .expect("event log opens");
    let controller = Arc::new(Mutex::new(ControllerState {
        bridge: IntegrationRuntimeBridge::new(
            control::ControlPlane::new(),
            state::InMemorySharedNodeState::new(),
            event_log.clone(),
            integration::GrpcNodeRouter::default(),
        ),
        orchestrator: MissionOrchestrator::new(),
    }));
    let gate = Arc::new(Mutex::new(()));
    let clock = runtime::SystemMonotonicClock::new();
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("listener binds");
    let address = listener.local_addr().expect("listener has address");
    let server = async {
        let (mut stream, _) = listener.accept().await.expect("request connects");
        handle_http_connection(&mut stream, &controller, &event_log, &gate, &clock)
            .await
            .expect("rejection is a valid HTTP response");
    };
    let client = async {
        let mut stream = tokio::net::TcpStream::connect(address)
            .await
            .expect("client connects");
        stream
                .write_all(b"POST /v1/executions/missing/cancel HTTP/1.1\r\nHost: localhost\r\nContent-Length: 0\r\n\r\n")
                .await
                .expect("request writes");
        let mut response = String::new();
        stream
            .read_to_string(&mut response)
            .await
            .expect("response reads");
        assert!(response.starts_with("HTTP/1.1 409 Conflict"));
    };
    tokio::time::timeout(Duration::from_secs(5), async {
        tokio::join!(server, client)
    })
    .await
    .expect("HTTP rejection completes");
    event_log
        .begin_batch()
        .expect("rejected cancellation left no open transaction");
    event_log
        .rollback_batch()
        .expect("subsequent transaction rolls back");
    assert!(
        event_log
            .load_checkpoint()
            .expect("checkpoint reads")
            .is_none()
    );
}

/// Builds one actor-free Mission whose bound role may be rebound between eligible Nodes.
fn recovery_driver_plan() -> domain::MissionPlan {
    let mission_id = domain::MissionId::new("mission-recovery-driver").expect("mission valid");
    let role_id = domain::RoleId::new("worker").expect("role valid");
    let requirement = domain::TaskRequirement::new(
        mission_id.clone(),
        domain::TaskId::new("work").expect("task valid"),
        vec![domain::RoleRequirement::new(
            role_id.clone(),
            domain::CapabilityKind::Compute,
            Some(domain::ResourceKind::Compute),
        )],
    )
    .expect("requirement valid");
    let intent = domain::ExecutionIntent::new(
        domain::CapabilityContractRef::new("compute", "work", "v1").expect("contract valid"),
        std::collections::BTreeMap::new(),
    )
    .expect("intent valid");
    let context_id = domain::CoordinationContextId::new("recovery-context").expect("context valid");
    let task = domain::PlannedTask::new(
        "exercise committed recovery resumption",
        requirement,
        std::collections::BTreeMap::from([(role_id, intent)]),
        Vec::new(),
        domain::TaskContinuity::new(
            context_id.clone(),
            std::collections::BTreeMap::new(),
            std::collections::BTreeMap::new(),
        ),
    )
    .expect("task valid");
    domain::MissionPlan::new(
        domain::MissionGoal::new(mission_id.clone(), "recover committed replacement")
            .expect("goal valid"),
        domain::TaskGraph::new(mission_id, vec![task]).expect("graph valid"),
        vec![domain::CoordinationContext::new(context_id, Vec::new()).expect("context valid")],
    )
    .expect("plan valid")
}

/// Builds one eligible node for the recovery-driver fixture.
fn recovery_driver_node(node_id: &str, resource_id: &str) -> domain::NodeRegistration {
    domain::NodeRegistration::new_with_contracts(
        domain::NodeId::new(node_id).expect("node valid"),
        domain::LocalRuntime::new("fixture", "1").expect("runtime valid"),
        domain::NodeContractVersion::v0_4(),
        vec![domain::Capability::new(
            domain::CapabilityKind::Compute,
            true,
        )],
        Vec::new(),
        vec![
            domain::Resource::new(
                domain::ResourceId::new(resource_id).expect("resource valid"),
                domain::ResourceKind::Compute,
                1,
            )
            .expect("resource valid"),
        ],
    )
}

/// State query filters are exact and can exclude only records explicitly marked stale.
#[test]
fn state_query_filters_semantics_sources_and_staleness() {
    let fresh = state_view_record(
        "world",
        "hazard",
        "crossing-a",
        "observed",
        "node:cane-a/safety",
        "hazards",
        serde_json::json!({"present": false}),
        Some(false),
    );
    let stale = state_view_record(
        "world",
        "hazard",
        "crossing-a",
        "reported",
        "node:dog-a/navigation",
        "hazards",
        serde_json::json!({"present": true}),
        Some(true),
    );
    let query =
        parse_query("object_class=world&object_type=hazard&semantic=observed&include_stale=false");

    assert!(state_record_matches(&fresh, &query));
    assert!(!state_record_matches(&stale, &query));
    assert!(!state_record_matches(
        &stale,
        &parse_query("include_stale=false")
    ));
    assert!(state_record_matches(
        &stale,
        &parse_query("include_stale=true")
    ));
}

/// Empty inventory still carries a versioned advisory snapshot rather than an error.
#[test]
fn inventory_snapshot_is_versioned_and_empty_before_registration() {
    let value = inventory_json(
        &state::InMemorySharedNodeState::new(),
        domain::TimestampMs::new(42),
    );
    assert_eq!(value["schema_version"], "roboguide.inventory/v0.1");
    assert_eq!(value["observed_at_ms"], 42);
    assert_eq!(value["nodes"], serde_json::json!([]));
}

/// Nonempty inventory preserves observation times and normalizes capability/resource kinds.
#[test]
fn inventory_snapshot_projects_registered_planning_facts() {
    let registration = domain::NodeRegistration::new_with_contracts(
        domain::NodeId::new("dog-a").expect("node id is valid"),
        domain::LocalRuntime::new("local-runtime", "1").expect("runtime is valid"),
        domain::NodeContractVersion::v0_2(),
        vec![domain::Capability::new(
            domain::CapabilityKind::Transport,
            true,
        )],
        vec![
            domain::CapabilityContractRef::new("mobility", "move", "v1")
                .expect("contract is valid"),
        ],
        vec![
            domain::Resource::new(
                domain::ResourceId::new("space-a").expect("resource id is valid"),
                domain::ResourceKind::Space,
                2,
            )
            .expect("resource is valid"),
        ],
    );
    let snapshot = domain::NodeStateSnapshot::new(
        registration,
        domain::NodeStatus::new(domain::NodeHealth::Degraded, domain::TimestampMs::new(7)),
        domain::TimestampMs::new(8),
        domain::NodeLivenessObservation::new(
            domain::NodeLiveness::Reachable,
            domain::TimestampMs::new(9),
        ),
    );
    let mut shared_state = state::InMemorySharedNodeState::new();
    ports::SharedNodeStateWriter::record_node(&mut shared_state, snapshot)
        .expect("snapshot is accepted");

    let value = inventory_json(&shared_state, domain::TimestampMs::new(10));

    assert_eq!(value["nodes"][0]["reported_health"], "Degraded");
    assert_eq!(value["nodes"][0]["source_observed_at_ms"], 7);
    assert_eq!(value["nodes"][0]["received_at_ms"], 8);
    assert_eq!(value["nodes"][0]["liveness_observed_at_ms"], 9);
    assert_eq!(value["nodes"][0]["capabilities"][0]["kind"], "transport");
    assert_eq!(value["nodes"][0]["contracts"][0], "mobility.move@v1");
    assert_eq!(value["nodes"][0]["resources"][0]["kind"], "space");
    assert_eq!(value["nodes"][0]["resources"][0]["capacity"], 2);
}

/// Advisory inventory excludes an exact contract whose latest readiness fact is false.
#[test]
fn inventory_snapshot_excludes_unavailable_exact_contracts() {
    let system_id = domain::LocalSystemId::new("mapping").expect("system id is valid");
    let contract = domain::CapabilityContractRef::new("spatial.map", "localize", "v0")
        .expect("contract is valid");
    let registration = domain::NodeRegistration::new_with_local_systems_and_readiness(
        domain::NodeId::new("dog-b").expect("node id is valid"),
        vec![domain::LocalSystemDescriptor::new(
            system_id.clone(),
            domain::LocalRuntime::new("mapping", "1").expect("runtime is valid"),
            std::collections::BTreeMap::new(),
        )],
        domain::NodeContractVersion::v0_2(),
        vec![domain::Capability::new(
            domain::CapabilityKind::Compute,
            false,
        )],
        std::collections::BTreeMap::from([(contract.clone(), system_id)]),
        std::collections::BTreeMap::from([(contract.clone(), domain::CapabilityKind::Compute)]),
        std::collections::BTreeMap::from([(contract, false)]),
        Vec::new(),
        Vec::new(),
        std::collections::BTreeMap::new(),
    )
    .expect("registration is valid");
    let snapshot = domain::NodeStateSnapshot::new(
        registration,
        domain::NodeStatus::new(domain::NodeHealth::Online, domain::TimestampMs::new(1)),
        domain::TimestampMs::new(2),
        domain::NodeLivenessObservation::new(
            domain::NodeLiveness::Reachable,
            domain::TimestampMs::new(2),
        ),
    );
    let mut shared_state = state::InMemorySharedNodeState::new();
    ports::SharedNodeStateWriter::record_node(&mut shared_state, snapshot)
        .expect("snapshot is accepted");

    let value = inventory_json(&shared_state, domain::TimestampMs::new(3));

    assert_eq!(value["nodes"][0]["reported_health"], "Online");
    assert_eq!(value["nodes"][0]["contracts"], serde_json::json!([]));
}

/// The control HTTP reader reconstructs a Mission request split across arbitrary TCP writes.
#[tokio::test]
async fn control_http_reader_accepts_fragmented_body() {
    use tokio::io::AsyncWriteExt;
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("test listener binds");
    let address = listener.local_addr().expect("test listener has address");
    let reader = tokio::spawn(async move {
        let (mut stream, _) = listener.accept().await.expect("test request connects");
        read_control_http_request(&mut stream).await
    });
    let mut client = tokio::net::TcpStream::connect(address)
        .await
        .expect("test client connects");
    let body = br#"{"schema":"roboguide.mission-plan/v0.2"}"#;
    let header = format!(
        "POST /v1/missions HTTP/1.1\r\nHost: localhost\r\nContent-Length: {}\r\n\r\n",
        body.len()
    );
    client
        .write_all(&header.as_bytes()[..19])
        .await
        .expect("first header fragment writes");
    tokio::task::yield_now().await;
    client
        .write_all(&header.as_bytes()[19..])
        .await
        .expect("second header fragment writes");
    client
        .write_all(&body[..7])
        .await
        .expect("first body fragment writes");
    tokio::task::yield_now().await;
    client
        .write_all(&body[7..])
        .await
        .expect("second body fragment writes");

    let request = reader
        .await
        .expect("reader task joins")
        .expect("fragmented request is valid");
    assert_eq!(request.method, "POST");
    assert_eq!(request.target, "/v1/missions");
    assert_eq!(request.body, body);
}

/// The control HTTP reader rejects an EOF before the declared body is complete.
#[tokio::test]
async fn control_http_reader_rejects_truncated_body() {
    use tokio::io::AsyncWriteExt;
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
        .await
        .expect("test listener binds");
    let address = listener.local_addr().expect("test listener has address");
    let reader = tokio::spawn(async move {
        let (mut stream, _) = listener.accept().await.expect("test request connects");
        read_control_http_request(&mut stream).await
    });
    let mut client = tokio::net::TcpStream::connect(address)
        .await
        .expect("test client connects");
    client
        .write_all(b"POST /v1/missions HTTP/1.1\r\nHost: localhost\r\nContent-Length: 10\r\n\r\n{}")
        .await
        .expect("truncated request writes");
    client.shutdown().await.expect("client write side closes");

    let error = reader
        .await
        .expect("reader task joins")
        .expect_err("truncated body is rejected");
    assert!(error.contains("ended before Content-Length"));
}

/// The controller database writer lease excludes peers and becomes available after release.
#[test]
fn event_log_writer_lock_is_process_exclusive() {
    let directory = tempfile::tempdir().expect("temporary directory exists");
    let event_path = directory.path().join("controller.sqlite3");
    let first = acquire_event_log_writer_lock(&event_path).expect("first writer acquires");

    assert!(acquire_event_log_writer_lock(&event_path).is_err());
    drop(first);
    acquire_event_log_writer_lock(&event_path).expect("released writer lock is reacquired");
}

/// The versioned placement fixture decodes into typed Control constraints.
#[test]
fn actor_placement_file_loads_typed_constraints() {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../scenarios/distributed-spatial-memory-v0.1/actor-placement.json");
    let constraints = load_actor_placement_file(&path).expect("placement fixture loads");
    assert_eq!(constraints.len(), 4);
    assert!(constraints.iter().all(|constraint| {
        matches!(
            (
                constraint.actor_id().as_str(),
                constraint.node_id().as_str()
            ),
            ("robot-dog-a", "dog-a") | ("robot-dog-b", "dog-b")
        )
    }));
}

/// Placement files with an unknown schema fail before the server starts accepting traffic.
#[test]
fn actor_placement_file_rejects_unknown_schema() {
    let directory = tempfile::tempdir().expect("temporary directory exists");
    let path = directory.path().join("placement.json");
    std::fs::write(
        &path,
        r#"{"schema":"roboguide.actor-placement/v9","constraints":[]}"#,
    )
    .expect("placement fixture writes");
    let error = load_actor_placement_file(&path).expect_err("unknown schema is rejected");
    assert!(error.to_string().contains("unsupported schema"));
}

/// The experiment placement file exactly covers every Actor in all four submitted Missions.
#[test]
fn actor_placement_fixture_has_strict_four_mission_coverage() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../scenarios/distributed-spatial-memory-v0.1");
    let constraints = load_actor_placement_file(&root.join("actor-placement.json"))
        .expect("placement fixture loads");
    let mut control = control::ControlPlane::new();
    for constraint in constraints {
        control
            .set_actor_node_constraint(
                constraint.mission_id().clone(),
                constraint.actor_id().clone(),
                constraint.node_id().clone(),
            )
            .expect("fixture constraints are mutually consistent");
    }
    for file_name in [
        "mission-a-build-publish.json",
        "mission-a-import-verify.json",
        "mission-b-build-publish.json",
        "mission-b-import-verify.json",
    ] {
        let source = std::fs::read_to_string(root.join(file_name))
            .expect("Mission fixture remains readable");
        let plan = decode_mission_plan(&source).expect("Mission fixture remains valid");
        validate_actor_placement_coverage(&control, &plan)
            .expect("placement exactly covers Mission actors");
    }
}

/// Strict placement rejects a misspelled Actor instead of falling back to generic matching.
#[test]
fn actor_placement_strict_coverage_rejects_typo() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../scenarios/distributed-spatial-memory-v0.1");
    let source = std::fs::read_to_string(root.join("mission-a-build-publish.json"))
        .expect("Mission fixture remains readable");
    let plan = decode_mission_plan(&source).expect("Mission fixture remains valid");
    let mut control = control::ControlPlane::new();
    control
        .set_actor_node_constraint(
            plan.goal().mission_id().clone(),
            domain::ActorId::new("robot-dog-typo").expect("typo remains syntactically valid"),
            domain::NodeId::new("dog-a").expect("node id is valid"),
        )
        .expect("syntactic configuration loads before plan validation");

    let error = validate_actor_placement_coverage(&control, &plan)
        .expect_err("unknown and missing Actors must fail closed");
    assert!(error.contains("missing actors [robot-dog-a]"));
    assert!(error.contains("unknown actors [robot-dog-typo]"));
}

/// Expected Actor and scheduling deferrals never stop the process-wide application timer.
#[test]
fn expected_dispatch_deferrals_do_not_fail_server() {
    let error = orchestration::OrchestrationError::Control(
        control::ControlError::ActorBindingRequiresReconciliation {
            mission_id: domain::MissionId::new("mission").expect("mission id is valid"),
            actor_id: domain::ActorId::new("actor").expect("actor id is valid"),
            node_id: domain::NodeId::new("node").expect("node id is valid"),
        },
    );
    assert!(deferred_dispatch(&error));
    assert!(deferred_dispatch(&OrchestrationError::Mission(
        "joint scheduling deferred: invalid time window".to_string(),
    )));
    assert!(deferred_dispatch(&OrchestrationError::Mission(
        "joint scheduling window missed".to_string(),
    )));
}

/// The application driver consumes a restored commitment before considering a new proposal.
#[test]
fn recovery_driver_rebinds_existing_commitment_first() {
    let directory = tempfile::tempdir().expect("temporary directory exists");
    let event_log = state::SqliteEventLog::open(directory.path().join("events.sqlite3"))
        .expect("event log opens");
    let correlation =
        domain::CorrelationId::new("committed-recovery-resume").expect("correlation valid");
    let plan = recovery_driver_plan();
    let mission_id = plan.goal().mission_id().clone();
    let requirement = plan.task_graph().tasks()[0].requirement().clone();
    let task_ref = requirement.task_ref().clone();
    let role_id = requirement.roles()[0].role_id().clone();
    let group_id = domain::ExecutionGroupId::new("group-recovery-driver").expect("group valid");
    let node_a = domain::NodeId::new("node-a").expect("node valid");
    let node_b = domain::NodeId::new("node-b").expect("node valid");
    let mut state = state::InMemorySharedNodeState::new();
    let mut control = control::ControlPlane::new();
    for (node, resource) in [("node-a", "cpu-a"), ("node-b", "cpu-b")] {
        control
            .register_node(
                &mut state,
                recovery_driver_node(node, resource),
                domain::NodeStatus::new(domain::NodeHealth::Online, domain::TimestampMs::new(1)),
                domain::TimestampMs::new(1),
                &correlation,
                &mut event_log.clone(),
            )
            .expect("fixture node registers");
    }
    let mut orchestrator = MissionOrchestrator::new();
    orchestrator
        .submit(
            plan,
            group_id.clone(),
            &mut control,
            domain::TimestampMs::new(2),
            &correlation,
            &mut event_log.clone(),
        )
        .expect("Mission submits");
    orchestrator
        .prepare_task(
            &mission_id,
            &task_ref,
            &state,
            &mut control,
            domain::TimestampMs::new(3),
            &correlation,
            &mut event_log.clone(),
        )
        .expect("Task binds deterministically");
    control
        .activate_task_execution(
            &group_id,
            &task_ref,
            domain::TimestampMs::new(4),
            &correlation,
            &mut event_log.clone(),
        )
        .expect("Task activates");
    let need = control
        .begin_execution_recovery(
            &group_id,
            &task_ref,
            &role_id,
            &node_a,
            domain::TimestampMs::new(5),
            &correlation,
            &mut event_log.clone(),
        )
        .expect("Runtime ambiguity begins recovery");
    let candidates = control
        .match_recovery_candidates(
            &state,
            &need,
            &requirement,
            domain::TimestampMs::new(6),
            &correlation,
            &mut event_log.clone(),
        )
        .expect("replacement candidates match");
    let proposal = control
        .propose_role_recovery(
            &state,
            &candidates,
            &requirement,
            node_b.clone(),
            vec![domain::ResourceId::new("cpu-b").expect("resource valid")],
            domain::TimestampMs::new(7),
            &correlation,
            &mut event_log.clone(),
        )
        .expect("replacement proposes");
    control
        .commit_role_recovery(
            &state,
            &requirement,
            &proposal,
            domain::TimestampMs::new(8),
            &correlation,
            &mut event_log.clone(),
        )
        .expect("replacement commits without rebind");
    let mut controller = ControllerState {
        bridge: IntegrationRuntimeBridge::new(
            control,
            state,
            event_log.clone(),
            integration::GrpcNodeRouter::default(),
        ),
        orchestrator,
    };

    resume_role_recovery(
        &mut controller,
        &need,
        domain::TimestampMs::new(9),
        &correlation,
        &mut event_log.clone(),
    )
    .expect("existing commitment rebinds without a second Commit");

    assert!(
        controller
            .bridge
            .control()
            .pending_recovery_commitment_for_task(&group_id, &task_ref, &role_id)
            .is_none()
    );
    let assignment = controller
        .bridge
        .control()
        .group(&group_id)
        .and_then(|group| group.task_execution(&task_ref))
        .and_then(|task| task.assignments().first())
        .expect("rebound assignment remains");
    assert_eq!(assignment.node_id(), &node_b);
    let old_command = domain::ExecutionCommand::new(
        task_ref.mission_id().clone(),
        task_ref.task_id().clone(),
        group_id.clone(),
        role_id.clone(),
        node_a,
        domain::ExecutionIntent::new(
            domain::CapabilityContractRef::new("compute", "work", "v1").expect("contract valid"),
            std::collections::BTreeMap::new(),
        )
        .expect("intent valid"),
        correlation.clone(),
    );
    apply_recovery_required(
        &mut controller,
        &old_command,
        domain::TimestampMs::new(10),
        &correlation,
        &mut event_log.clone(),
    )
    .expect("old ambiguity cannot invalidate a rebound role awaiting dispatch");
    assert_eq!(
        controller
            .bridge
            .control()
            .group(&group_id)
            .expect("group remains")
            .lifecycle(),
        control::GroupLifecycle::Adapted
    );
}

/// Startup rejects a replacement placement policy that does not cover a restored Mission.
#[test]
fn restored_mission_is_revalidated_before_server_start() {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../scenarios/distributed-spatial-memory-v0.1");
    let source = std::fs::read_to_string(root.join("mission-a-build-publish.json"))
        .expect("Mission fixture remains readable");
    let plan_value: serde_json::Value =
        serde_json::from_str(&source).expect("Mission fixture remains JSON");
    let orchestrator = MissionOrchestrator::restore_json(
        &serde_json::json!([{
            "plan": plan_value,
            "group_id": "group-restored-map-a",
            "lifecycle": "Accepted"
        }])
        .to_string(),
    )
    .expect("orchestration checkpoint restores");
    let mut control = control::ControlPlane::new();
    let mission_id = orchestrator.mission_ids()[0].clone();
    control
        .set_actor_node_constraint(
            mission_id,
            domain::ActorId::new("robot-dog-typo").expect("typo remains syntactically valid"),
            domain::NodeId::new("dog-a").expect("node identity is valid"),
        )
        .expect("syntactic replacement policy loads");

    let error = validate_restored_actor_placement_coverage(&control, &orchestrator)
        .expect_err("restored Mission coverage must fail closed");
    assert!(error.contains("restored Mission placement is invalid"));
    assert!(error.contains("missing actors [robot-dog-a]"));
}
