//! Production logical fairness under actual Control competition and durable HTTP/SQL policy boundaries.

use super::*;
use domain::{CorrelationId, ExecutionGroupId, MissionId, TimestampMs};

/// Cluster authority cannot be restored standalone, under another profile or through an older wrapper.
#[test]
fn checkpoint_replication_profile_cannot_be_bypassed() {
    let fixture = Fixture::new(1);
    let mut saved: ServerCheckpoint = serde_json::from_str(
        &fixture
            .events
            .load_checkpoint()
            .unwrap()
            .unwrap()
            .checkpoint_json,
    )
    .unwrap();
    validate_replication_checkpoint(&saved, None).unwrap();
    assert!(validate_replication_checkpoint(&saved, Some("cluster")).is_err());
    saved.replication_profile = Some("cluster".into());
    validate_replication_checkpoint(&saved, Some("cluster")).unwrap();
    assert!(validate_replication_checkpoint(&saved, None).is_err());
    assert!(validate_replication_checkpoint(&saved, Some("different")).is_err());
    saved.schema = PRE_REPLICATION_SERVER_CHECKPOINT_SCHEMA.into();
    assert!(validate_replication_checkpoint(&saved, Some("cluster")).is_err());
}

/// Declares independent normalized roots, retaining canonical intent and Task-scoped resources.
fn plan_body(mission: &str, count: usize) -> String {
    let operation = serde_json::json!({"namespace":"compute","name":"work","version":"v1"});
    serde_json::json!({
        "schema_version":"roboguide.mission-plan/v0.7",
        "mission":{"id":mission,"objective":"independent work","actors":(0..count).map(|index|
            serde_json::json!({"id":format!("actor-{index}")})).collect::<Vec<_>>()},
        "contexts":[{"id":"context","roles":(0..count).map(|index|
            serde_json::json!({"id":format!("participant-{index}"),"actor":format!("actor-{index}")})).collect::<Vec<_>>(),
            "relations":[],"coupling_mode":"independent"}],
        "tasks":(0..count).map(|index|serde_json::json!({
            "id":format!("task-{index}"),"description":"work complete","context_id":"context","depends_on":[],
            "timing":{"earliest_start_offset_ms":0,"latest_start_offset_ms":null,"completion_deadline_offset_ms":null},
            "satisfaction":{"expected_effect":"work complete","basis":"execution-report","verifier":null},
            "roles":[{"id":"worker","context_role":format!("participant-{index}"),"resource_scope":"task",
                "requirements":{"capabilities":[{"contract":operation,"constraints":[]}],"resources":[{"kind":"compute","units":1}]},
                "execution_intent":{"operation":operation,"objective":"independent work","parameters":{}}}]
        })).collect::<Vec<_>>(),
    }).to_string()
}

/// Two exclusive resources and competing A/B plans use real Control, Orchestration and SQLite.
struct Fixture {
    /// Test-owned database directory.
    directory: tempfile::TempDir,
    /// Actual application projection.
    controller: Arc<Mutex<ControllerState>>,
    /// Actual durable store.
    events: state::SqliteEventLog,
}

impl Fixture {
    /// Registers eligible providers and accepts plans without preparing or sending physical commands.
    fn new(roots: usize) -> Self {
        Self::with_plans(
            decode_mission_plan(&plan_body("a", roots)).unwrap(),
            decode_mission_plan(&plan_body("b", 1)).unwrap(),
        )
    }

    /// Changes only the accepted logical topology while preserving the same resource competition.
    fn with_plans(a: domain::MissionPlan, b: domain::MissionPlan) -> Self {
        let directory = tempfile::tempdir().unwrap();
        let events = state::SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
        let mut control = control::ControlPlane::new();
        let mut state = state::InMemorySharedNodeState::new();
        for index in 0..2 {
            control
                .register_node(
                    &mut state,
                    recovery_driver_node(&format!("worker-{index}"), &format!("cpu-{index}")),
                    domain::NodeStatus::new(domain::NodeHealth::Online, TimestampMs::new(1)),
                    TimestampMs::new(1),
                    &CorrelationId::new("policy-nodes").unwrap(),
                    &mut events.clone(),
                )
                .unwrap();
        }
        let mut orchestrator = MissionOrchestrator::new();
        for plan in [a, b] {
            let name = plan.goal().mission_id().as_str();
            orchestrator
                .submit(
                    plan.clone(),
                    ExecutionGroupId::new(format!("group-{name}")).unwrap(),
                    &mut control,
                    TimestampMs::new(1),
                    &CorrelationId::new("policy-plans").unwrap(),
                    &mut events.clone(),
                )
                .unwrap();
        }
        let controller = ControllerState {
            bridge: IntegrationRuntimeBridge::new(
                control,
                state,
                events.clone(),
                integration::GrpcNodeRouter::default(),
            ),
            orchestrator,
            mission_admissions: BTreeMap::new(),
            verifier_seen: BTreeSet::new(),
            verifier_source_digest: None,
        };
        events.begin_batch().unwrap();
        events
            .save_checkpoint(
                SERVER_CHECKPOINT_SCHEMA,
                &server_checkpoint_json(&controller).unwrap(),
            )
            .unwrap();
        events.commit_batch().unwrap();
        Self {
            directory,
            controller: Arc::new(Mutex::new(controller)),
            events,
        }
    }

    /// Drives the actual private-candidate timer transaction.
    fn tick(&self) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
        drive_application_timer(
            &self.controller,
            &self.events,
            &Arc::new(Mutex::new(())),
            TimestampMs::new(2),
            None,
        )
    }

    /// Exchanges one bounded request through the same production handler as deployment operators.
    async fn post(&self, path: &str, body: &str) -> String {
        use tokio::io::{AsyncReadExt, AsyncWriteExt};
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let gate = Arc::new(Mutex::new(()));
        let clock = runtime::FixedClock::new(TimestampMs::new(2));
        let server = async {
            let (mut stream, _) = listener.accept().await.unwrap();
            handle_http_connection(
                &mut stream,
                &self.controller,
                &self.events,
                &gate,
                &clock,
                None,
                None,
            )
            .await
            .unwrap();
        };
        let client = async {
            let mut stream = tokio::net::TcpStream::connect(address).await.unwrap();
            stream.write_all(format!("POST {path} HTTP/1.1\r\nHost: localhost\r\nContent-Length: {}\r\n\r\n{body}", body.len()).as_bytes()).await.unwrap();
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
}

/// A's many roots cannot reserve both Nodes before B obtains its fair first-round opportunity.
#[test]
fn competing_missions_get_resources_in_round_order() {
    let fixture = Fixture::new(4);
    fixture.tick().unwrap();
    let live = fixture.controller.lock().unwrap();
    let attempts = live.bridge.attempt_history();
    assert_eq!(attempts.len(), 2);
    assert_eq!(
        attempts
            .iter()
            .map(|attempt| attempt.command().mission_id().as_str())
            .collect::<BTreeSet<_>>(),
        BTreeSet::from(["a", "b"])
    );
    assert_eq!(
        live.bridge
            .control()
            .allocation_snapshot(TimestampMs::new(2))
            .unwrap()
            .allocations()
            .len(),
        2
    );
    assert_eq!(
        live.orchestrator
            .execution(&MissionId::new("b").unwrap())
            .unwrap()
            .dispatch_policy()
            .last_service_ordinal(),
        2
    );
}

/// Per-input work is bounded and later turns preserve both original bindings and unserved queue history.
#[test]
fn many_roots_are_bounded_per_timer_and_do_not_duplicate_attempts() {
    let fixture = Fixture::new(40);
    fixture.tick().unwrap();
    let live = fixture.controller.lock().unwrap();
    assert_eq!(
        live.orchestrator
            .mission_ids()
            .iter()
            .map(|id| live
                .orchestrator
                .execution(id)
                .unwrap()
                .dispatch_policy()
                .last_service_ordinal())
            .max(),
        Some(32)
    );
    assert_eq!(live.bridge.attempt_history().len(), 2);
    let saved: ServerCheckpoint = serde_json::from_str(
        &fixture
            .events
            .load_checkpoint()
            .unwrap()
            .unwrap()
            .checkpoint_json,
    )
    .unwrap();
    let restored = MissionOrchestrator::restore_json(&saved.orchestration_json).unwrap();
    assert_eq!(
        restored.checkpoint_json().unwrap(),
        live.orchestrator.checkpoint_json().unwrap()
    );
    drop(live);
    fixture.tick().unwrap();
    assert_eq!(
        fixture
            .controller
            .lock()
            .unwrap()
            .bridge
            .attempt_history()
            .len(),
        2
    );
}

/// A real checkpoint failure rolls back scheduler history along with every Control/Runtime candidate.
#[test]
fn failed_timer_does_not_consume_fairness_or_resources() {
    let fixture = Fixture::new(4);
    let before = fixture.events.load_checkpoint().unwrap().unwrap();
    let trace = fixture.events.events().unwrap();
    ConnectionFixture::reject_checkpoint(&fixture);
    assert!(fixture.tick().is_err());
    assert_eq!(fixture.events.load_checkpoint().unwrap().unwrap(), before);
    assert_eq!(fixture.events.events().unwrap(), trace);
    assert_eq!(
        server_checkpoint_json(&fixture.controller.lock().unwrap()).unwrap(),
        before.checkpoint_json
    );
}

/// SQLite fault setup retains the connection only for its actual scoped trigger write.
struct ConnectionFixture;

impl ConnectionFixture {
    /// Injects a real durable write failure rather than manufacturing a policy callback error.
    fn reject_checkpoint(fixture: &Fixture) {
        rusqlite::Connection::open(fixture.directory.path().join("events.sqlite3")).unwrap()
            .execute_batch("CREATE TRIGGER reject_checkpoint BEFORE INSERT ON controller_checkpoint BEGIN SELECT RAISE(ABORT, 'policy checkpoint failure'); END").unwrap();
    }
}

/// Policy mutation is durable; invalid input, admission capacity and SQL rejection preserve all authorities.
#[tokio::test]
async fn priority_and_admission_http_preserve_durable_boundaries() {
    let fixture = Fixture::new(4);
    let before = fixture.events.load_checkpoint().unwrap().unwrap();
    for body in [
        r#"{"priority":256}"#,
        r#"{"priority":1,"node":"worker-0"}"#,
        "{}",
    ] {
        assert!(
            fixture
                .post("/v1/missions/b/scheduling-policy", body)
                .await
                .starts_with("HTTP/1.1 400")
        );
        assert_eq!(fixture.events.load_checkpoint().unwrap().unwrap(), before);
    }
    assert!(
        fixture
            .post("/v1/missions/b/scheduling-policy", r#"{"priority":255}"#)
            .await
            .starts_with("HTTP/1.1 200")
    );
    assert_eq!(
        fixture
            .controller
            .lock()
            .unwrap()
            .orchestrator
            .execution(&MissionId::new("b").unwrap())
            .unwrap()
            .dispatch_policy()
            .priority(),
        255
    );
    let durable = fixture.events.load_checkpoint().unwrap().unwrap();
    let trace = fixture.events.events().unwrap();
    fixture
        .controller
        .lock()
        .unwrap()
        .orchestrator
        .set_admission_limits(orchestration::MissionAdmissionLimits::try_new(2, 5).unwrap());
    assert!(
        fixture
            .post("/v1/missions", &plan_body("c", 1))
            .await
            .starts_with("HTTP/1.1 429")
    );
    assert_eq!(fixture.events.load_checkpoint().unwrap().unwrap(), durable);
    assert_eq!(fixture.events.events().unwrap(), trace);
    ConnectionFixture::reject_checkpoint(&fixture);
    assert!(
        fixture
            .post("/v1/missions/b/scheduling-policy", r#"{"priority":1}"#)
            .await
            .starts_with("HTTP/1.1 503")
    );
    assert_eq!(fixture.events.load_checkpoint().unwrap().unwrap(), durable);
    assert_eq!(fixture.events.events().unwrap(), trace);
    assert_eq!(
        fixture
            .controller
            .lock()
            .unwrap()
            .orchestrator
            .execution(&MissionId::new("b").unwrap())
            .unwrap()
            .dispatch_policy()
            .priority(),
        255
    );
}

/// Higher priority receives the first eligible Node while later round turns still preserve ownership.
#[tokio::test]
async fn explicit_priority_changes_real_control_dispatch_order() {
    let fixture = Fixture::new(4);
    assert!(
        fixture
            .post("/v1/missions/b/scheduling-policy", r#"{"priority":255}"#)
            .await
            .starts_with("HTTP/1.1 200")
    );
    fixture.tick().unwrap();
    let live = fixture.controller.lock().unwrap();
    let b = MissionId::new("b").unwrap();
    assert_eq!(
        live.orchestrator
            .execution(&b)
            .unwrap()
            .dispatch_policy()
            .last_service_ordinal(),
        1
    );
    let attempt = live
        .bridge
        .attempt_history()
        .into_iter()
        .find(|attempt| attempt.command().mission_id() == &b)
        .unwrap();
    assert_eq!(attempt.command().node_id().as_str(), "worker-0");
    assert_eq!(
        live.bridge
            .control()
            .allocation_snapshot(TimestampMs::new(2))
            .unwrap()
            .allocations()
            .len(),
        2
    );
}

/// A previous wrapper cannot hide a new policy, and current authority cannot omit its ledger.
#[test]
fn checkpoint_schema_fences_policy_downgrade_and_omission() {
    let fixture = Fixture::new(4);
    let mut saved: ServerCheckpoint = serde_json::from_str(
        &fixture
            .events
            .load_checkpoint()
            .unwrap()
            .unwrap()
            .checkpoint_json,
    )
    .unwrap();
    validate_isolation_checkpoint_schema(&saved).unwrap();
    saved.schema = PRE_DISPATCH_SERVER_CHECKPOINT_SCHEMA.into();
    assert!(validate_isolation_checkpoint_schema(&saved).is_err());
    let mut missions: Vec<serde_json::Value> =
        serde_json::from_str(&saved.orchestration_json).unwrap();
    for mission in &mut missions {
        mission.as_object_mut().unwrap().remove("dispatch_policy");
    }
    saved.orchestration_json = serde_json::to_string(&missions).unwrap();
    validate_isolation_checkpoint_schema(&saved).unwrap();
    saved.schema = SERVER_CHECKPOINT_SCHEMA.into();
    assert!(validate_isolation_checkpoint_schema(&saved).is_err());
}

/// A scarce-resource rejection retains B's age so a stream of sequential two-Role Tasks cannot starve it.
#[test]
fn resource_shortage_retains_age_before_a_new_gang_task() {
    use integration::grpc::v0_4::{
        ExecutionPhase, NodeMessage, node_message::Message as NodePayload,
    };
    let mut a: serde_json::Value = serde_json::from_str(&plan_body("a", 2)).unwrap();
    let mut left = a["tasks"][0]["roles"][0].clone();
    let mut right = a["tasks"][1]["roles"][0].clone();
    left["id"] = serde_json::json!("left");
    right["id"] = serde_json::json!("right");
    a["tasks"][0]["roles"] = serde_json::json!([left, right]);
    a["tasks"][1]["roles"] = a["tasks"][0]["roles"].clone();
    a["tasks"][1]["depends_on"] = serde_json::json!(["task-0"]);
    let fixture = Fixture::with_plans(
        decode_mission_plan(&a.to_string()).unwrap(),
        decode_mission_plan(&plan_body("b", 1)).unwrap(),
    );
    fixture.tick().unwrap();
    let mut live = fixture.controller.lock().unwrap();
    let attempts = live.bridge.attempt_history();
    assert_eq!(attempts.len(), 2);
    assert!(
        attempts
            .iter()
            .all(|attempt| attempt.command().mission_id().as_str() == "a")
    );
    fixture.events.begin_batch().unwrap();
    let mut candidate = live.clone();
    let correlation = CorrelationId::new("gang-owner-facts").unwrap();
    for (sequence, phase, time) in [
        (1, ExecutionPhase::Started, 3),
        (2, ExecutionPhase::Completed, 4),
    ] {
        for attempt in &attempts {
            candidate
                .bridge
                .consume_received(
                    integration::GrpcNodeEvent::NodeMessage {
                        node_id: attempt.command().node_id().to_string(),
                        session_id: "policy-session".into(),
                        message: NodeMessage {
                            message: Some(NodePayload::ExecutionSnapshot(
                                integration::grpc::v0_4::ExecutionSnapshot {
                                    session_id: "policy-session".into(),
                                    execution_id: attempt.execution_id().into(),
                                    last_sequence: sequence,
                                    phase: phase as i32,
                                    reason: "original owner outcome".into(),
                                },
                            )),
                        },
                    },
                    TimestampMs::new(time),
                    TimestampMs::new(time),
                    &correlation,
                )
                .unwrap();
        }
        apply_runtime_events(
            &mut candidate,
            TimestampMs::new(time),
            &correlation,
            &mut fixture.events.clone(),
        )
        .unwrap();
    }
    apply_runtime_outcomes(
        &mut candidate,
        TimestampMs::new(4),
        &correlation,
        &mut fixture.events.clone(),
    )
    .unwrap();
    drive_ready_tasks(
        &mut candidate,
        TimestampMs::new(4),
        &correlation,
        &mut fixture.events.clone(),
    )
    .unwrap();
    fixture
        .events
        .save_checkpoint(
            SERVER_CHECKPOINT_SCHEMA,
            &server_checkpoint_json(&candidate).unwrap(),
        )
        .unwrap();
    fixture.events.commit_batch().unwrap();
    *live = candidate;
    let pending = live
        .bridge
        .attempt_history()
        .into_iter()
        .filter(|attempt| attempt.status() == runtime::ExecutionStatus::Dispatched)
        .collect::<Vec<_>>();
    assert_eq!(pending.len(), 1);
    assert_eq!(pending[0].command().mission_id().as_str(), "b");
    assert_eq!(
        live.bridge
            .control()
            .allocation_snapshot(TimestampMs::new(4))
            .unwrap()
            .allocations()
            .len(),
        1
    );
}
