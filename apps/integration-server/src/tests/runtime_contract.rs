//! Production terminal receipt timing drives exact-Mission holds without injected transition errors.

use super::*;
use domain::TimestampMs;
use integration::grpc::v0_4::{ExecutionPhase, NodeMessage, node_message::Message as NodePayload};

/// Declares a real completion deadline while retaining the compatibility execution-report contract.
pub(super) fn plan(mission: &str, deadline: Option<u64>) -> domain::MissionPlan {
    let operation = serde_json::json!({"namespace":"compute", "name":"work", "version":"v1"});
    decode_mission_plan(&serde_json::json!({
        "schema_version":"roboguide.mission-plan/v0.6",
        "mission":{"id":mission, "objective":"bounded synthetic work"},
        "contexts":[{"id":"context", "roles":[{"id":"worker", "actor":"worker"}], "relations":[]}],
        "tasks":[{"id":"task", "description":"work complete", "context_id":"context", "depends_on":[],
            "timing":{"earliest_start_offset_ms":0, "latest_start_offset_ms":null, "completion_deadline_offset_ms":deadline, "estimated_duration_ms":5},
            "satisfaction":{"basis":"execution-report", "verifier":null},
            "roles":[{"id":"worker", "actor":"worker", "capability":"compute", "contract":operation,
                "context_role":"worker", "resource_scope":"task", "resources":[{"kind":"compute", "units":1}],
                "execution":{"capability_contract":operation, "parameters":{}}}]}]
    }).to_string()).unwrap()
}

/// Holds two actually scheduled Missions and their first physical attempts in an isolated database.
struct Fixture {
    /// Owns only this test's SQLite directory.
    _directory: tempfile::TempDir,
    /// Production Controller state, including Runtime and Control authorities.
    controller: Arc<Mutex<ControllerState>>,
    /// Real event/checkpoint transaction store.
    events: state::SqliteEventLog,
    /// Exact current attempt contexts produced by normal dispatch.
    attempts: Vec<runtime::ExecutionAttemptSnapshot>,
}

impl Fixture {
    /// Accepts, matches, commits and binds A/B before admitting their original Started facts.
    fn new() -> Self {
        Self::with_storage(state::CheckpointStoragePolicy::Full)
    }

    /// Exercises the identical application authorities through either exact storage representation.
    fn with_storage(policy: state::CheckpointStoragePolicy) -> Self {
        let directory = tempfile::tempdir().unwrap();
        let events = state::SqliteEventLog::open_with_checkpoint_policy(
            directory.path().join("events.sqlite3"),
            policy,
        )
        .unwrap();
        let correlation = domain::CorrelationId::new("runtime-contract-setup").unwrap();
        let mut control = control::ControlPlane::new();
        let mut state = state::InMemorySharedNodeState::new();
        for index in 0..2 {
            control
                .register_node(
                    &mut state,
                    recovery_driver_node(&format!("worker-{index}"), &format!("cpu-{index}")),
                    domain::NodeStatus::new(domain::NodeHealth::Online, TimestampMs::new(1)),
                    TimestampMs::new(1),
                    &correlation,
                    &mut events.clone(),
                )
                .unwrap();
        }
        let mut orchestrator = MissionOrchestrator::new();
        for (mission, deadline) in [("mission-a", Some(8)), ("mission-b", None)] {
            orchestrator
                .submit(
                    plan(mission, deadline),
                    domain::ExecutionGroupId::new(format!("group-{mission}")).unwrap(),
                    &mut control,
                    TimestampMs::new(1),
                    &correlation,
                    &mut events.clone(),
                )
                .unwrap();
        }
        let mut controller = ControllerState {
            bridge: IntegrationRuntimeBridge::new(
                control,
                state,
                events.clone(),
                integration::GrpcNodeRouter::default(),
            ),
            orchestrator,
            mission_admissions: BTreeMap::new(),
            memory_imports: BTreeMap::new(),
            memory_preparation: Default::default(),
            safety_stops: BTreeMap::new(),
            verifier_seen: BTreeSet::new(),
            verifier_artifact_store: None,
            verifier_source_digest: None,
        };
        events.begin_batch().unwrap();
        drive_ready_tasks(
            &mut controller,
            TimestampMs::new(2),
            &correlation,
            &mut events.clone(),
        )
        .unwrap();
        events.commit_batch().unwrap();
        let attempts = controller.bridge.attempt_history();
        assert_eq!(attempts.len(), 2);
        let fixture = Self {
            _directory: directory,
            controller: Arc::new(Mutex::new(controller)),
            events,
            attempts,
        };
        for index in 0..2 {
            fixture.fact(index, 1, ExecutionPhase::Started, 3, 3);
        }
        fixture
    }

    /// Admits original receive metadata through the real reducer and commits raw evidence before the timer.
    fn fact(
        &self,
        index: usize,
        sequence: u64,
        phase: ExecutionPhase,
        received: u64,
        decision: u64,
    ) {
        let attempt = &self.attempts[index];
        let mut live = self.controller.lock().unwrap();
        let mut candidate = live.clone();
        let correlation = domain::CorrelationId::new("runtime-contract-fact").unwrap();
        self.events.begin_batch().unwrap();
        candidate
            .bridge
            .consume_received(
                integration::GrpcNodeEvent::NodeMessage {
                    node_id: attempt.command().node_id().to_string(),
                    session_id: "runtime-contract-session".into(),
                    message: NodeMessage {
                        message: Some(NodePayload::ExecutionSnapshot(
                            integration::grpc::v0_4::ExecutionSnapshot {
                                session_id: "runtime-contract-session".into(),
                                execution_id: attempt.execution_id().into(),
                                last_sequence: sequence,
                                phase: phase as i32,
                                reason: "synthetic owner fact".into(),
                            },
                        )),
                    },
                },
                TimestampMs::new(received),
                TimestampMs::new(decision),
                &correlation,
            )
            .unwrap();
        apply_runtime_events(
            &mut candidate,
            TimestampMs::new(decision),
            &correlation,
            &mut self.events.clone(),
        )
        .unwrap();
        self.events
            .save_checkpoint(
                SERVER_CHECKPOINT_SCHEMA,
                &server_checkpoint_json(&candidate).unwrap(),
            )
            .unwrap();
        self.events.commit_batch().unwrap();
        *live = candidate;
    }

    /// Drives the actual durable timer; no callback manufactures a contract error.
    fn tick(&self, now: u64) {
        drive_application_timer(
            &self.controller,
            &self.events,
            &Arc::new(Mutex::new(())),
            TimestampMs::new(now),
            None,
        )
        .unwrap();
    }

    /// Exchanges an explicit resume through the real HTTP handler with a consistent local clock.
    async fn resume(&self, now: u64) -> String {
        use tokio::io::{AsyncReadExt, AsyncWriteExt};
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let gate = Arc::new(Mutex::new(()));
        let clock = runtime::FixedClock::new(TimestampMs::new(now));
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
            let body = r#"{"isolation_generation":1}"#;
            stream.write_all(format!(
                "POST /v1/missions/mission-a/resume HTTP/1.1\r\nHost: localhost\r\nContent-Length: {}\r\n\r\n{body}",
                body.len(),
            ).as_bytes()).await.unwrap();
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

/// A production late completion holds A and its ownership while B completes and survives durable restore.
#[test]
fn late_completion_isolates_only_its_mission_through_production_timer() {
    let fixture = Fixture::new();
    fixture.fact(0, 2, ExecutionPhase::Completed, 10, 10);
    fixture.fact(1, 2, ExecutionPhase::Completed, 11, 11);
    fixture.tick(12);
    let live = fixture.controller.lock().unwrap();
    let a = domain::MissionId::new("mission-a").unwrap();
    let b = domain::MissionId::new("mission-b").unwrap();
    assert!(
        live.orchestrator
            .execution(&a)
            .unwrap()
            .isolation()
            .is_some()
    );
    assert_eq!(
        live.orchestrator.execution(&b).unwrap().lifecycle(),
        orchestration::MissionExecutionLifecycle::Completed
    );
    assert_eq!(
        live.bridge
            .execution_status(fixture.attempts[0].execution_id()),
        Some(orchestration::RemoteExecutionStatus::Completed)
    );
    let allocations = live
        .bridge
        .control()
        .allocation_snapshot(TimestampMs::new(12))
        .unwrap();
    assert_eq!(allocations.allocations().len(), 1);
    let checkpoint = fixture.events.load_checkpoint().unwrap().unwrap();
    let saved: ServerCheckpoint = serde_json::from_str(&checkpoint.checkpoint_json).unwrap();
    let restored = MissionOrchestrator::restore_json(&saved.orchestration_json).unwrap();
    assert!(restored.execution(&a).unwrap().isolation().is_some());
    drop(live);
    assert!(fixture.events.decoded_events().unwrap().iter().any(|event| matches!(
        event.payload(),
        domain::EventPayload::MissionExecutionContractRejected {
            task_ref,
            violation:domain::TaskRuntimeContractViolation::CompletionDeadlineExceeded { received_at, deadline }
        } if task_ref.mission_id() == &a && received_at.as_millis() == 10 && deadline.as_millis() == 9
    )));
    fixture.tick(20);
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

/// Processing delay and a replay cannot turn an on-time original completion into a late one.
#[test]
fn queued_completion_keeps_original_receive_time_and_does_not_isolate() {
    let fixture = Fixture::new();
    fixture.fact(0, 2, ExecutionPhase::Completed, 8, 20);
    fixture.fact(0, 3, ExecutionPhase::Completed, 21, 21);
    fixture.fact(1, 2, ExecutionPhase::Completed, 22, 22);
    fixture.tick(23);
    let live = fixture.controller.lock().unwrap();
    for mission in live.orchestrator.mission_ids() {
        assert!(
            live.orchestrator
                .execution(&mission)
                .unwrap()
                .isolation()
                .is_none()
        );
        assert_eq!(
            live.orchestrator.execution(&mission).unwrap().lifecycle(),
            orchestration::MissionExecutionLifecycle::Completed
        );
    }
    let integration: serde_json::Value =
        serde_json::from_str(&live.bridge.checkpoint_json().unwrap()).unwrap();
    assert_eq!(
        integration["runtime"]["terminal_received_at"][fixture.attempts[0].execution_id()],
        8
    );
}

/// Explicit resume cannot renew an immutable completion deadline or remove its durable hold.
#[tokio::test]
async fn deadline_resume_conflict_preserves_checkpoint_and_ownership() {
    let fixture = Fixture::new();
    fixture.fact(0, 2, ExecutionPhase::Completed, 10, 10);
    fixture.fact(1, 2, ExecutionPhase::Completed, 11, 11);
    fixture.tick(12);
    let before = fixture.events.load_checkpoint().unwrap().unwrap();
    let trace = fixture.events.events().unwrap();
    assert!(fixture.resume(13).await.starts_with("HTTP/1.1 409"));
    assert_eq!(fixture.events.load_checkpoint().unwrap().unwrap(), before);
    assert_eq!(fixture.events.events().unwrap(), trace);
    assert_eq!(
        server_checkpoint_json(&fixture.controller.lock().unwrap()).unwrap(),
        before.checkpoint_json
    );
}

/// Missing historical receive-time authority remains unknown and holds only the deadline Mission.
#[test]
fn legacy_completion_time_is_not_invented_after_restore() {
    let fixture = Fixture::new();
    fixture.fact(0, 2, ExecutionPhase::Completed, 8, 8);
    fixture.fact(1, 2, ExecutionPhase::Completed, 9, 9);
    let mut live = fixture.controller.lock().unwrap();
    let mut checkpoint: serde_json::Value =
        serde_json::from_str(&live.bridge.checkpoint_json().unwrap()).unwrap();
    checkpoint["runtime"]["terminal_received_at"]
        .as_object_mut()
        .unwrap()
        .remove(fixture.attempts[0].execution_id());
    live.bridge = IntegrationRuntimeBridge::restore_from_checkpoint(
        &checkpoint.to_string(),
        fixture.events.clone(),
        integration::GrpcNodeRouter::default(),
        TimestampMs::new(10),
    )
    .unwrap();
    drop(live);
    fixture.tick(11);
    assert!(fixture.events.decoded_events().unwrap().iter().any(|event| matches!(
        event.payload(), domain::EventPayload::MissionExecutionContractRejected {
            task_ref, violation: domain::TaskRuntimeContractViolation::CompletionTimeUnavailable,
        } if task_ref.mission_id().as_str() == "mission-a"
    )));
    assert_eq!(
        fixture
            .controller
            .lock()
            .unwrap()
            .orchestrator
            .execution(&domain::MissionId::new("mission-b").unwrap())
            .unwrap()
            .lifecycle(),
        orchestration::MissionExecutionLifecycle::Completed
    );
}

/// An actual SQL event failure aborts the whole timer transaction instead of disguising it as a hold.
#[test]
fn production_deadline_sql_failure_remains_global_and_atomic() {
    let fixture = Fixture::new();
    fixture.fact(0, 2, ExecutionPhase::Completed, 10, 10);
    fixture.fact(1, 2, ExecutionPhase::Completed, 11, 11);
    let before = fixture.events.load_checkpoint().unwrap().unwrap();
    let trace = fixture.events.events().unwrap();
    let sql = rusqlite::Connection::open(fixture._directory.path().join("events.sqlite3")).unwrap();
    sql.execute_batch("CREATE TRIGGER reject_events BEFORE INSERT ON events BEGIN SELECT RAISE(ABORT, 'contract evidence failure'); END").unwrap();
    assert!(
        drive_application_timer(
            &fixture.controller,
            &fixture.events,
            &Arc::new(Mutex::new(())),
            TimestampMs::new(12),
            None
        )
        .is_err()
    );
    assert_eq!(fixture.events.load_checkpoint().unwrap().unwrap(), before);
    assert_eq!(fixture.events.events().unwrap(), trace);
    assert_eq!(
        server_checkpoint_json(&fixture.controller.lock().unwrap()).unwrap(),
        before.checkpoint_json
    );
    sql.execute_batch("DROP TRIGGER reject_events").unwrap();
    fixture.tick(13);
    assert!(
        fixture
            .controller
            .lock()
            .unwrap()
            .orchestrator
            .execution(&domain::MissionId::new("mission-a").unwrap())
            .unwrap()
            .isolation()
            .is_some()
    );
}

/// Delta reconstruction preserves holds, physical ownership and nonterminal Unknown/no-replay restore.
#[test]
fn incremental_restore_retains_held_evidence_and_fences_nonterminal_attempts() {
    let fixture = Fixture::with_storage(state::CheckpointStoragePolicy::Incremental);
    fixture.fact(0, 2, ExecutionPhase::Completed, 10, 10);
    fixture.tick(12);
    let saved: ServerCheckpoint = serde_json::from_str(
        &fixture
            .events
            .load_checkpoint()
            .unwrap()
            .unwrap()
            .checkpoint_json,
    )
    .unwrap();
    let mut bridge = IntegrationRuntimeBridge::restore_from_checkpoint(
        &saved.integration_json,
        fixture.events.clone(),
        integration::GrpcNodeRouter::default(),
        TimestampMs::new(13),
    )
    .unwrap();
    let orchestrator = MissionOrchestrator::restore_json(&saved.orchestration_json).unwrap();
    assert!(
        orchestrator
            .execution(&domain::MissionId::new("mission-a").unwrap())
            .unwrap()
            .isolation()
            .is_some()
    );
    assert_eq!(
        bridge.execution_status(fixture.attempts[0].execution_id()),
        Some(orchestration::RemoteExecutionStatus::Completed)
    );
    assert_eq!(
        bridge.execution_status(fixture.attempts[1].execution_id()),
        Some(orchestration::RemoteExecutionStatus::Unknown)
    );
    assert_eq!(
        bridge
            .control()
            .allocation_snapshot(TimestampMs::new(13))
            .unwrap()
            .allocations()
            .len(),
        2
    );
    assert_eq!(
        bridge
            .flush_dispatch_outbox_with_clock(&runtime::FixedClock::new(TimestampMs::new(13)))
            .unwrap(),
        0
    );
    assert_eq!(bridge.attempt_history().len(), 2);
}

/// Rejected review of one immutable outcome remains idempotent; it is not a new execution failure.
#[tokio::test]
async fn repeated_deadline_resume_does_not_count_outcome_replay() {
    let fixture = Fixture::new();
    fixture.fact(0, 2, ExecutionPhase::Completed, 10, 10);
    fixture.fact(1, 2, ExecutionPhase::Completed, 11, 11);
    fixture.tick(12);
    for now in [13, 14, 15] {
        let response = fixture.resume(now).await;
        let live = fixture.controller.lock().unwrap();
        let execution = live
            .orchestrator
            .execution(&domain::MissionId::new("mission-a").unwrap())
            .unwrap();
        println!(
            "resume_at={now}, response={}, generation={}, failure_count={}, quarantined={}",
            response.lines().next().unwrap(),
            execution.isolation().unwrap().generation,
            execution.failure_state().unwrap().count,
            execution.is_crash_loop_quarantined()
        );
    }
    let live = fixture.controller.lock().unwrap();
    let execution = live
        .orchestrator
        .execution(&domain::MissionId::new("mission-a").unwrap())
        .unwrap();
    assert!(!execution.is_crash_loop_quarantined());
    assert_eq!(execution.failure_state().unwrap().count, 1);
    assert_eq!(execution.isolation().unwrap().generation, 1);
}

/// Independent review counterexample: the recovery view must use the current physical attempt identity.
#[test]
fn recovery_view_reports_exact_current_attempt_identity() {
    let fixture = Fixture::new();
    fixture.fact(0, 2, ExecutionPhase::Unknown, 7, 7);
    let live = fixture.controller.lock().unwrap();
    let view = recovery_view(&live, TimestampMs::new(7));
    let actual_id = fixture.attempts[0].execution_id();
    let reported_id = view["unknown_attempts"][0]["execution_id"]
        .as_str()
        .unwrap();
    println!("actual_execution_id={actual_id}, reported_execution_id={reported_id}");
    assert_eq!(reported_id, actual_id);
}
