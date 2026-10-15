//! Offline durable isolation, rollback, scope and generation-bound resume checks.

use super::*;

/// Builds a single independent synthetic Task without deployment or hardware assumptions.
fn plan(mission: &str) -> domain::MissionPlan {
    let contract = serde_json::json!({"namespace": "compute", "name": "work", "version": "v1"});
    decode_mission_plan(&serde_json::json!({
        "schema_version": "roboguide.mission-plan/v0.3",
        "mission": {"id": mission, "objective": "synthetic work"},
        "contexts": [{"id": "context", "roles": [{"id": "worker", "actor": "worker"}], "relations": []}],
        "tasks": [{"id": "task", "description": "perform synthetic work", "context_id": "context", "depends_on": [],
            "roles": [{"id": "worker", "actor": "worker", "capability": "compute", "contract": contract,
                "context_role": "worker", "resource_scope": "task", "resource_kind": "compute",
                "execution": {"capability_contract": contract, "parameters": {}}}]}]
    }).to_string()).unwrap()
}

/// Creates two legal accepted Missions sharing only the existing Controller authority.
fn fixture(log: &state::SqliteEventLog) -> ControllerState {
    let mut controller = ControllerState {
        mission_admissions: BTreeMap::new(),
        memory_imports: BTreeMap::new(),
        memory_preparation: Default::default(),
        safety_stops: BTreeMap::new(),
        bridge: IntegrationRuntimeBridge::new(
            control::ControlPlane::new(),
            state::InMemorySharedNodeState::new(),
            log.clone(),
            integration::GrpcNodeRouter::default(),
        ),
        orchestrator: MissionOrchestrator::new(),
        verifier_seen: BTreeSet::new(),
        verifier_artifact_store: None,
        verifier_source_digest: None,
    };
    for mission in ["mission-a", "mission-b"] {
        controller
            .orchestrator
            .submit(
                plan(mission),
                domain::ExecutionGroupId::new(format!("group-{mission}")).unwrap(),
                controller.bridge.control_mut(),
                domain::TimestampMs::new(1),
                &domain::CorrelationId::new("isolation-setup").unwrap(),
                &mut log.clone(),
            )
            .unwrap();
    }
    controller
}

/// An injected typed failure proves rollback/hold mechanics, not a production error's classification.
#[test]
fn local_failure_rolls_back_candidate_and_events_then_isolates_only_a() {
    let directory = tempfile::tempdir().unwrap();
    let log = state::SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
    let mut controller = fixture(&log);
    let a = domain::MissionId::new("mission-a").unwrap();
    let b = domain::MissionId::new("mission-b").unwrap();
    let group = controller
        .orchestrator
        .execution(&a)
        .unwrap()
        .group_id()
        .clone();
    let baseline_control = serde_json::to_value(controller.bridge.control().checkpoint()).unwrap();
    let first_sequence = log.latest_sequence().unwrap();
    log.begin_batch().unwrap();
    run_mission_transition(
        &mut controller,
        &a,
        orchestration::MissionIsolationSource::ReadyDispatch,
        domain::TimestampMs::new(2),
        &domain::CorrelationId::new("isolate-a").unwrap(),
        &mut log.clone(),
        |candidate, events| {
            candidate.bridge.control_mut().block_group(
                &group,
                "failed private mutation",
                domain::TimestampMs::new(2),
                &domain::CorrelationId::new("discard-me").unwrap(),
                events,
            )?;
            Err(OrchestrationError::InvalidContract(
                "synthetic exact-Mission contract failure".into(),
            )
            .into())
        },
    )
    .unwrap();
    assert_eq!(
        serde_json::to_value(controller.bridge.control().checkpoint()).unwrap(),
        baseline_control
    );
    assert!(
        controller
            .orchestrator
            .execution(&a)
            .unwrap()
            .isolation()
            .is_some()
    );
    assert!(
        controller
            .orchestrator
            .execution(&b)
            .unwrap()
            .isolation()
            .is_none()
    );
    assert!(
        controller
            .orchestrator
            .dispatchable_tasks(&a, controller.bridge.control())
            .is_empty()
    );
    let task = plan("mission-b").task_graph().tasks()[0]
        .requirement()
        .task_ref()
        .clone();
    let b_group = controller
        .orchestrator
        .execution(&b)
        .unwrap()
        .group_id()
        .clone();
    let registration = recovery_driver_node("synthetic-worker", "synthetic-cpu");
    let mut state = controller.bridge.state().clone();
    controller
        .bridge
        .control_mut()
        .register_node(
            &mut state,
            registration,
            domain::NodeStatus::new(domain::NodeHealth::Online, domain::TimestampMs::new(2)),
            domain::TimestampMs::new(2),
            &domain::CorrelationId::new("b-progress").unwrap(),
            &mut log.clone(),
        )
        .unwrap();
    *controller.bridge.state_mut() = state.clone();
    controller
        .orchestrator
        .prepare_task(
            &b,
            &task,
            &state,
            controller.bridge.control_mut(),
            domain::TimestampMs::new(2),
            &domain::CorrelationId::new("b-progress").unwrap(),
            &mut log.clone(),
        )
        .unwrap();
    controller
        .bridge
        .control_mut()
        .activate_task_execution(
            &b_group,
            &task,
            domain::TimestampMs::new(2),
            &domain::CorrelationId::new("b-progress").unwrap(),
            &mut log.clone(),
        )
        .unwrap();
    controller
        .orchestrator
        .record_task_execution_completed(
            &b,
            &task,
            controller.bridge.control_mut(),
            domain::TimestampMs::new(3),
            &domain::CorrelationId::new("b-complete").unwrap(),
            &mut log.clone(),
        )
        .unwrap();
    controller
        .orchestrator
        .satisfy_task_from_execution_report(
            &b,
            &task,
            controller.bridge.control_mut(),
            domain::TimestampMs::new(3),
            &domain::CorrelationId::new("b-complete").unwrap(),
            &mut log.clone(),
        )
        .unwrap();
    assert_eq!(
        controller.orchestrator.execution(&b).unwrap().lifecycle(),
        orchestration::MissionExecutionLifecycle::Completed
    );
    let checkpoint = server_checkpoint_json(&controller).unwrap();
    log.save_checkpoint(SERVER_CHECKPOINT_SCHEMA, &checkpoint)
        .unwrap();
    log.commit_batch().unwrap();
    let events = log.events().unwrap();
    assert_eq!(
        events[first_sequence as usize].sequence,
        first_sequence + 1,
        "failed transition sequence was rolled back"
    );
    assert!(
        events
            .iter()
            .all(|event| event.correlation_id != "discard-me")
    );
    let saved: ServerCheckpoint = serde_json::from_str(&checkpoint).unwrap();
    let restored = MissionOrchestrator::restore_json(&saved.orchestration_json).unwrap();
    assert_eq!(
        restored
            .execution(&a)
            .unwrap()
            .isolation()
            .unwrap()
            .generation,
        1
    );
    assert_eq!(
        restored.execution(&b).unwrap().lifecycle(),
        orchestration::MissionExecutionLifecycle::Completed
    );
}

/// Unclassified Control errors preserve the global fail-closed path and do not create a hold.
#[test]
fn shared_or_unclassified_failure_is_not_quarantined() {
    let directory = tempfile::tempdir().unwrap();
    let log = state::SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
    let mut controller = fixture(&log);
    let a = domain::MissionId::new("mission-a").unwrap();
    let before = server_checkpoint_json(&controller).unwrap();
    log.begin_batch().unwrap();
    let result = run_mission_transition(
        &mut controller,
        &a,
        orchestration::MissionIsolationSource::ReadyDispatch,
        domain::TimestampMs::new(2),
        &domain::CorrelationId::new("global-fail").unwrap(),
        &mut log.clone(),
        |_candidate, _events| {
            Err(
                OrchestrationError::Control(control::ControlError::InvalidProposal(
                    "ownership cannot be proved".into(),
                ))
                .into(),
            )
        },
    );
    assert!(result.is_err());
    assert_eq!(server_checkpoint_json(&controller).unwrap(), before);
    log.rollback_batch().unwrap();
}

/// Current wrappers require breaker authority, and resumed histories cannot be relabeled as an older schema.
#[test]
fn breaker_wrapper_fences_omission_and_downgrade_after_resume() {
    let directory = tempfile::tempdir().unwrap();
    let log = state::SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
    let mut controller = fixture(&log);
    let mission = domain::MissionId::new("mission-a").unwrap();
    let correlation = domain::CorrelationId::new("breaker-schema").unwrap();
    controller
        .orchestrator
        .isolate_execution(
            &mission,
            orchestration::MissionIsolationSource::ReadyDispatch,
            domain::TimestampMs::new(2),
            &correlation,
            &mut log.clone(),
        )
        .unwrap();
    controller
        .orchestrator
        .resume_isolated_execution(
            &mission,
            1,
            domain::TimestampMs::new(3),
            &correlation,
            &mut log.clone(),
        )
        .unwrap();
    let saved: ServerCheckpoint =
        serde_json::from_str(&server_checkpoint_json(&controller).unwrap()).unwrap();
    validate_isolation_checkpoint_schema(&saved).unwrap();
    for field in ["failure_state", "recent_failed_at"] {
        let mut changed = saved.clone();
        let mut missions: Vec<serde_json::Value> =
            serde_json::from_str(&changed.orchestration_json).unwrap();
        if field == "failure_state" {
            missions[0].as_object_mut().unwrap().remove(field);
        } else {
            missions[0]["failure_state"]
                .as_object_mut()
                .unwrap()
                .remove(field);
        }
        changed.orchestration_json = serde_json::to_string(&missions).unwrap();
        assert!(validate_isolation_checkpoint_schema(&changed).is_err());
    }
    let mut downgraded = saved.clone();
    historical_membership_wrapper_fixture(&mut downgraded);
    downgraded.schema = PRE_BREAKER_SERVER_CHECKPOINT_SCHEMA.into();
    assert!(validate_isolation_checkpoint_schema(&downgraded).is_err());
    // Actual v25 input has counter evidence but no rolling samples; migration retains unavailable samples.
    let mut missions: Vec<serde_json::Value> =
        serde_json::from_str(&downgraded.orchestration_json).unwrap();
    missions[0]["failure_state"]
        .as_object_mut()
        .unwrap()
        .remove("recent_failed_at");
    downgraded.orchestration_json = serde_json::to_string(&missions).unwrap();
    validate_isolation_checkpoint_schema(&downgraded).unwrap();
    let restored = MissionOrchestrator::restore_json(&downgraded.orchestration_json).unwrap();
    assert_eq!(
        restored
            .execution(&mission)
            .unwrap()
            .failure_state()
            .unwrap()
            .recent_failed_at,
        None
    );
}

/// Resume must consume exactly the live hold and old wrapper schemas cannot launder that authority.
#[test]
fn explicit_resume_is_generation_bound_and_checkpoint_fenced() {
    let directory = tempfile::tempdir().unwrap();
    let log = state::SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
    let mut controller = fixture(&log);
    let a = domain::MissionId::new("mission-a").unwrap();
    controller
        .orchestrator
        .isolate_execution(
            &a,
            orchestration::MissionIsolationSource::ReadyDispatch,
            domain::TimestampMs::new(2),
            &domain::CorrelationId::new("hold").unwrap(),
            &mut log.clone(),
        )
        .unwrap();
    let checkpoint = server_checkpoint_json(&controller).unwrap();
    let mut saved: ServerCheckpoint = serde_json::from_str(&checkpoint).unwrap();
    validate_isolation_checkpoint_schema(&saved).unwrap();
    saved.schema = PRE_ISOLATION_SERVER_CHECKPOINT_SCHEMA.into();
    assert!(validate_isolation_checkpoint_schema(&saved).is_err());
    log.begin_batch().unwrap();
    let mut candidate = controller.clone();
    assert!(
        resume_mission_candidate(
            &mut candidate,
            &a,
            2,
            domain::TimestampMs::new(3),
            &domain::CorrelationId::new("wrong-generation").unwrap(),
            &mut log.clone()
        )
        .is_err()
    );
    assert_eq!(server_checkpoint_json(&candidate).unwrap(), checkpoint);
    resume_mission_candidate(
        &mut candidate,
        &a,
        1,
        domain::TimestampMs::new(3),
        &domain::CorrelationId::new("resume").unwrap(),
        &mut log.clone(),
    )
    .unwrap();
    assert!(
        candidate
            .orchestrator
            .execution(&a)
            .unwrap()
            .isolation()
            .is_none()
    );
    assert!(
        resume_mission_candidate(
            &mut candidate,
            &a,
            1,
            domain::TimestampMs::new(3),
            &domain::CorrelationId::new("replay").unwrap(),
            &mut log.clone()
        )
        .is_err()
    );
    log.rollback_batch().unwrap();
    assert_eq!(
        controller
            .orchestrator
            .execution(&a)
            .unwrap()
            .isolation()
            .unwrap()
            .generation,
        1
    );
}

/// A durable raw completion received during a hold retains Control ownership until explicit resume.
#[test]
fn held_terminal_fact_is_retained_without_release_or_execute_replay() {
    use integration::grpc::v0_4::ExecutionPhase;
    let mut fixture = super::stop_recovery::Fixture::new();
    let mission = fixture.attempt.command().mission_id().clone();
    let allocations = fixture.allocations();
    fixture
        .controller
        .orchestrator
        .isolate_execution(
            &mission,
            orchestration::MissionIsolationSource::TaskOutcome,
            domain::TimestampMs::new(5),
            &fixture.correlation,
            &mut fixture.events.clone(),
        )
        .unwrap();
    fixture.fact(2, ExecutionPhase::Completed, 6);
    fixture.events.begin_batch().unwrap();
    apply_runtime_outcomes(
        &mut fixture.controller,
        domain::TimestampMs::new(6),
        &fixture.correlation,
        &mut fixture.events.clone(),
    )
    .unwrap();
    assert_eq!(
        fixture.allocations().allocations(),
        allocations.allocations()
    );
    assert_eq!(
        fixture
            .controller
            .bridge
            .terminal_task_execution_outcomes()
            .len(),
        1
    );
    assert_eq!(fixture.controller.bridge.attempt_history().len(), 1);
    let checkpoint = server_checkpoint_json(&fixture.controller).unwrap();
    fixture
        .events
        .save_checkpoint(SERVER_CHECKPOINT_SCHEMA, &checkpoint)
        .unwrap();
    fixture.events.commit_batch().unwrap();
    let saved: ServerCheckpoint = serde_json::from_str(&checkpoint).unwrap();
    let restored = ControllerState {
        bridge: IntegrationRuntimeBridge::restore_from_checkpoint(
            &saved.integration_json,
            fixture.events.clone(),
            integration::GrpcNodeRouter::default(),
            domain::TimestampMs::new(7),
        )
        .unwrap(),
        orchestrator: MissionOrchestrator::restore_json(&saved.orchestration_json).unwrap(),
        mission_admissions: saved.mission_admissions,
        memory_imports: saved.memory_imports,
        memory_preparation: saved.memory_preparation,
        safety_stops: saved.safety_stops,
        verifier_seen: saved.verifier_seen,
        verifier_artifact_store: None,
        verifier_source_digest: saved.verifier_source_digest,
    };
    assert!(
        restored
            .orchestrator
            .execution(&mission)
            .unwrap()
            .isolation()
            .is_some()
    );
    assert_eq!(restored.bridge.terminal_task_execution_outcomes().len(), 1);
    assert!(
        revalidate_mission_resume(&restored, &mission, domain::TimestampMs::new(7)).is_err(),
        "restart cannot revive leases for resume"
    );
    fixture.events.begin_batch().unwrap();
    resume_mission_candidate(
        &mut fixture.controller,
        &mission,
        1,
        domain::TimestampMs::new(7),
        &fixture.correlation,
        &mut fixture.events.clone(),
    )
    .unwrap();
    assert_eq!(
        fixture
            .controller
            .orchestrator
            .execution(&mission)
            .unwrap()
            .lifecycle(),
        orchestration::MissionExecutionLifecycle::Completed
    );
    assert_eq!(fixture.controller.bridge.attempt_history().len(), 1);
    fixture.events.commit_batch().unwrap();
}

/// Current operation and health evidence must still support the retained executor on explicit resume.
#[test]
fn resume_rejects_expired_health_and_changed_operation() {
    let fixture = super::stop_recovery::Fixture::new();
    let mission = fixture.attempt.command().mission_id();
    assert!(
        revalidate_mission_resume(&fixture.controller, mission, domain::TimestampMs::new(5))
            .is_ok()
    );
    assert!(
        revalidate_mission_resume(
            &fixture.controller,
            mission,
            domain::TimestampMs::new(30_000)
        )
        .is_err()
    );
    for health in [domain::NodeHealth::Online, domain::NodeHealth::Offline] {
        let mut candidate = fixture.controller.clone();
        let mut state = candidate.bridge.state().clone();
        let node = fixture.attempt.command().node_id().as_str();
        let resource = if node == "node-a" { "cpu-a" } else { "cpu-b" };
        let registration = if health == domain::NodeHealth::Online {
            recovery_driver_node_with_support(node, resource, None)
                .with_operation_support(Vec::new())
                .unwrap()
        } else {
            recovery_driver_node(node, resource)
        };
        candidate
            .bridge
            .control_mut()
            .register_node(
                &mut state,
                registration,
                domain::NodeStatus::new(health, domain::TimestampMs::new(5)),
                domain::TimestampMs::new(5),
                &fixture.correlation,
                &mut fixture.events.clone(),
            )
            .unwrap();
        *candidate.bridge.state_mut() = state;
        assert!(
            revalidate_mission_resume(&candidate, mission, domain::TimestampMs::new(6)).is_err()
        );
    }
}

/// An EventSink failure cannot become a Mission hold even if the callback returns InvalidContract.
#[test]
fn sqlite_failure_is_not_downgraded_to_isolation() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("events.sqlite3");
    let log = state::SqliteEventLog::open(&path).unwrap();
    let mut controller = fixture(&log);
    let a = domain::MissionId::new("mission-a").unwrap();
    let group = controller
        .orchestrator
        .execution(&a)
        .unwrap()
        .group_id()
        .clone();
    let before = server_checkpoint_json(&controller).unwrap();
    rusqlite::Connection::open(path).unwrap().execute_batch("CREATE TRIGGER reject_events BEFORE INSERT ON events BEGIN SELECT RAISE(ABORT, 'injected write failure'); END").unwrap();
    log.begin_batch().unwrap();
    assert!(
        run_mission_transition(
            &mut controller,
            &a,
            orchestration::MissionIsolationSource::ReadyDispatch,
            domain::TimestampMs::new(2),
            &domain::CorrelationId::new("sink-failure").unwrap(),
            &mut log.clone(),
            |candidate, events| {
                candidate.bridge.control_mut().block_group(
                    &group,
                    "private mutation",
                    domain::TimestampMs::new(2),
                    &domain::CorrelationId::new("discard-me").unwrap(),
                    events,
                )?;
                Err(OrchestrationError::InvalidContract("local-looking error".into()).into())
            }
        )
        .is_err()
    );
    assert_eq!(server_checkpoint_json(&controller).unwrap(), before);
    log.rollback_batch().unwrap();
}

/// Uses the exact resume path while sharing the production HTTP transport with admission checks.
async fn resume_request(
    controller: &Arc<Mutex<ControllerState>>,
    events: &state::SqliteEventLog,
    body: &str,
) -> String {
    http_request(controller, events, "/v1/missions/mission-a/resume", body).await
}

/// Exchanges one bounded POST through the production HTTP handler without injecting transition errors.
async fn http_request(
    controller: &Arc<Mutex<ControllerState>>,
    events: &state::SqliteEventLog,
    path: &str,
    body: &str,
) -> String {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let gate = Arc::new(Mutex::new(()));
    let clock = runtime::FixedClock::new(domain::TimestampMs::new(3));
    let server = async {
        let (mut stream, _) = listener.accept().await.unwrap();
        handle_http_connection(&mut stream, controller, events, &gate, &clock, None, None)
            .await
            .unwrap();
    };
    let client = async {
        let mut stream = tokio::net::TcpStream::connect(address).await.unwrap();
        let request = format!(
            "POST {path} HTTP/1.1\r\nHost: localhost\r\nContent-Length: {}\r\n\r\n{body}",
            body.len()
        );
        stream.write_all(request.as_bytes()).await.unwrap();
        let mut response = String::new();
        stream.read_to_string(&mut response).await.unwrap();
        response
    };
    tokio::time::timeout(Duration::from_secs(5), async {
        let (_, response) = tokio::join!(server, client);
        response
    })
    .await
    .unwrap()
}

/// Real contract errors reject before execution; they never manufacture a hold or stop existing Missions.
#[tokio::test]
async fn admission_contract_rejection_preserves_existing_missions_and_timer_dispatch() {
    let directory = tempfile::tempdir().unwrap();
    let log = state::SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
    let mut live = fixture(&log);
    let correlation = domain::CorrelationId::new("admission-boundary").unwrap();
    let mut state = live.bridge.state().clone();
    for index in 0..2 {
        live.bridge
            .control_mut()
            .register_node(
                &mut state,
                recovery_driver_node(&format!("worker-{index}"), &format!("cpu-{index}")),
                domain::NodeStatus::new(domain::NodeHealth::Online, domain::TimestampMs::new(2)),
                domain::TimestampMs::new(2),
                &correlation,
                &mut log.clone(),
            )
            .unwrap();
    }
    *live.bridge.state_mut() = state;
    let before = server_checkpoint_json(&live).unwrap();
    log.begin_batch().unwrap();
    log.save_checkpoint(SERVER_CHECKPOINT_SCHEMA, &before)
        .unwrap();
    log.commit_batch().unwrap();
    let sequence = log.latest_sequence().unwrap();
    let controller = Arc::new(Mutex::new(live));

    let mut unsupported: serde_json::Value = serde_json::from_str(include_str!(
        "../../../../scenarios/execution-relations-v0.1/mission-plan.json"
    ))
    .unwrap();
    unsupported["schema_version"] = serde_json::json!(domain::MISSION_PLAN_SCHEMA_V0_4);
    unsupported["contexts"][0]["coupling_mode"] = serde_json::json!("concurrent-cooperation");
    unsupported["contexts"][0]["shared_view"] = serde_json::json!({
        "bindings": [{"context_role_id":"guide", "field":"execution"}],
        "include_freshness":false
    });
    unsupported["contexts"][0]["relations"][0] = serde_json::json!({
        "id":"relative-guidance", "kind":"relative-pose", "frame_id":"map",
        "source":{"task_id":"observe-safety", "role_id":"safety-observer"},
        "target":{"task_id":"navigate", "role_id":"navigator"}
    });
    let rejected_plan = decode_mission_plan(&unsupported.to_string()).unwrap();
    assert!(matches!(
        orchestration::SupportedMechanismProfile::current().validate(&rejected_plan),
        Err(OrchestrationError::InvalidContract(_))
    ));
    for (body, status) in [("{".to_string(), "400"), (unsupported.to_string(), "409")] {
        let response = http_request(&controller, &log, "/v1/missions", &body).await;
        assert!(
            response.starts_with(&format!("HTTP/1.1 {status}")),
            "{response}"
        );
        assert_eq!(
            server_checkpoint_json(&controller.lock().unwrap()).unwrap(),
            before
        );
        assert_eq!(log.latest_sequence().unwrap(), sequence);
        assert_eq!(
            log.load_checkpoint().unwrap().unwrap().checkpoint_json,
            before
        );
    }
    drive_application_timer(
        &controller,
        &log,
        &Arc::new(Mutex::new(())),
        domain::TimestampMs::new(4),
        None,
    )
    .unwrap();
    let live = controller.lock().unwrap();
    assert!(
        live.orchestrator
            .execution(rejected_plan.goal().mission_id())
            .is_none()
    );
    assert_eq!(live.bridge.attempt_history().len(), 2);
    for mission_id in live.orchestrator.mission_ids() {
        assert!(
            live.orchestrator
                .execution(&mission_id)
                .unwrap()
                .isolation()
                .is_none()
        );
        assert!(
            live.bridge
                .attempt_history()
                .iter()
                .any(|attempt| attempt.command().mission_id() == &mission_id)
        );
    }
}

/// The public endpoint distinguishes rejection from persistence failure and publishes only after commit.
#[tokio::test]
async fn resume_http_rejection_and_sql_failure_preserve_durable_hold() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("events.sqlite3");
    let log = state::SqliteEventLog::open(&path).unwrap();
    let mut controller = fixture(&log);
    controller
        .orchestrator
        .isolate_execution(
            &domain::MissionId::new("mission-a").unwrap(),
            orchestration::MissionIsolationSource::ReadyDispatch,
            domain::TimestampMs::new(2),
            &domain::CorrelationId::new("hold").unwrap(),
            &mut log.clone(),
        )
        .unwrap();
    let baseline = server_checkpoint_json(&controller).unwrap();
    log.begin_batch().unwrap();
    log.save_checkpoint(SERVER_CHECKPOINT_SCHEMA, &baseline)
        .unwrap();
    log.commit_batch().unwrap();
    let controller = Arc::new(Mutex::new(controller));
    assert!(
        resume_request(&controller, &log, "{")
            .await
            .starts_with("HTTP/1.1 400")
    );
    assert!(
        resume_request(&controller, &log, r#"{"isolation_generation":2}"#)
            .await
            .starts_with("HTTP/1.1 409")
    );
    let sql = rusqlite::Connection::open(path).unwrap();
    sql.execute_batch("CREATE TRIGGER reject_checkpoint BEFORE INSERT ON controller_checkpoint BEGIN SELECT RAISE(ABORT, 'injected checkpoint failure'); END").unwrap();
    assert!(
        resume_request(&controller, &log, r#"{"isolation_generation":1}"#)
            .await
            .starts_with("HTTP/1.1 503")
    );
    assert_eq!(
        server_checkpoint_json(&controller.lock().unwrap()).unwrap(),
        baseline
    );
    assert_eq!(
        log.load_checkpoint().unwrap().unwrap().checkpoint_json,
        baseline
    );
    sql.execute_batch("DROP TRIGGER reject_checkpoint").unwrap();
    assert!(
        resume_request(&controller, &log, r#"{"isolation_generation":1}"#)
            .await
            .starts_with("HTTP/1.1 200")
    );
    assert!(
        resume_request(&controller, &log, r#"{"isolation_generation":1}"#)
            .await
            .starts_with("HTTP/1.1 409")
    );
    assert!(log.decoded_events().unwrap().iter().any(|event| matches!(
        event.payload(),
        domain::EventPayload::MissionExecutionResumed { generation: 1, .. }
    )));
}

/// A hold suppresses pending Execute but retains cancel draining and actual terminal-stop authority.
#[test]
fn isolated_mission_cancel_retains_ownership_until_terminal_fact() {
    use integration::grpc::v0_4::ExecutionPhase;
    let mut fixture = super::stop_recovery::Fixture::new();
    let mission = fixture.attempt.command().mission_id().clone();
    let group = fixture.attempt.command().group_id().clone();
    let allocations = fixture.allocations();
    fixture
        .controller
        .orchestrator
        .isolate_execution(
            &mission,
            orchestration::MissionIsolationSource::ReadyDispatch,
            domain::TimestampMs::new(5),
            &fixture.correlation,
            &mut fixture.events.clone(),
        )
        .unwrap();
    assert_eq!(
        flush_unisolated_dispatch(
            &mut fixture.controller,
            &runtime::FixedClock::new(domain::TimestampMs::new(5))
        )
        .unwrap(),
        0
    );
    fixture
        .controller
        .orchestrator
        .request_cancel(
            &mission,
            fixture.controller.bridge.control_mut(),
            domain::TimestampMs::new(6),
            &fixture.correlation,
            &mut fixture.events.clone(),
        )
        .unwrap();
    fixture
        .controller
        .bridge
        .request_group_cancellation(&group)
        .unwrap();
    assert_eq!(
        fixture.allocations().allocations(),
        allocations.allocations()
    );
    fixture.fact(2, ExecutionPhase::Cancelled, 7);
    apply_pending_cancellations(
        &mut fixture.controller,
        domain::TimestampMs::new(7),
        &fixture.correlation,
        &mut fixture.events.clone(),
    )
    .unwrap();
    assert_eq!(
        fixture
            .controller
            .orchestrator
            .execution(&mission)
            .unwrap()
            .lifecycle(),
        orchestration::MissionExecutionLifecycle::Cancelled
    );
    assert!(
        fixture
            .controller
            .orchestrator
            .execution(&mission)
            .unwrap()
            .isolation()
            .is_none()
    );
}
