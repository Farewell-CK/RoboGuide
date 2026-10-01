//! Physical-stop recovery exercises real Control commitments, ordered Node facts and HTTP.

use super::*;
use domain::{NodeHealth, TimestampMs};
use integration::grpc::v0_4::{ExecutionPhase, NodeMessage, node_message::Message as NodePayload};

/// Keeps a running resource-bearing Mission and isolated persistence for recovery tests.
struct Fixture {
    /// Keeps the isolated SQLite event database alive.
    _directory: tempfile::TempDir,
    /// Original application authority, with one independently bound role.
    controller: ControllerState,
    /// Shared durable event and checkpoint sink.
    events: state::SqliteEventLog,
    /// Original physical attempt selected by actual scheduling.
    attempt: runtime::ExecutionAttemptSnapshot,
    /// Stable evidence correlation.
    correlation: domain::CorrelationId,
}

impl Fixture {
    /// Runs actual Submit -> Match -> Schedule -> Commit -> Bind and an original Started fact.
    fn new() -> Self {
        let directory = tempfile::tempdir().expect("isolated directory");
        let events =
            state::SqliteEventLog::open(directory.path().join("events.sqlite3")).expect("opens");
        let correlation = domain::CorrelationId::new("confirmed-stop-test").expect("correlation");
        let mut control = control::ControlPlane::new();
        let mut state = state::InMemorySharedNodeState::new();
        for (node, resource) in [("node-a", "cpu-a"), ("node-b", "cpu-b")] {
            control
                .register_node(
                    &mut state,
                    recovery_driver_node(node, resource),
                    domain::NodeStatus::new(NodeHealth::Online, TimestampMs::new(1)),
                    TimestampMs::new(1),
                    &correlation,
                    &mut events.clone(),
                )
                .expect("register");
        }
        let mut orchestrator = MissionOrchestrator::new();
        let contract = serde_json::json!({"namespace": "compute", "name": "work", "version": "v1"});
        let plan = decode_mission_plan(&serde_json::json!({
            "schema_version": "roboguide.mission-plan/v0.3",
            "mission": {"id": "mission-recovery-driver", "objective": "recover safely"},
            "contexts": [{"id": "recovery-context", "roles": [{"id": "worker", "actor": "worker"}], "relations": []}],
            "tasks": [{"id": "work", "description": "independent work", "context_id": "recovery-context", "depends_on": [],
                "roles": [{"id": "worker", "actor": "worker", "capability": "compute", "contract": contract,
                    "context_role": "worker", "resource_scope": "task", "resource_kind": "compute",
                    "execution": {"capability_contract": contract, "parameters": {}}}]}]
        }).to_string()).expect("canonical plan with serializable continuity");
        orchestrator
            .submit(
                plan,
                domain::ExecutionGroupId::new("stop-group").expect("group"),
                &mut control,
                TimestampMs::new(2),
                &correlation,
                &mut events.clone(),
            )
            .expect("submit");
        let mut controller = ControllerState {
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
        drive_ready_tasks(
            &mut controller,
            TimestampMs::new(3),
            &correlation,
            &mut events.clone(),
        )
        .expect("dispatch prepares");
        let attempt = controller
            .bridge
            .attempt_history()
            .into_iter()
            .next()
            .expect("attempt");
        let mut fixture = Self {
            _directory: directory,
            controller,
            events,
            attempt,
            correlation,
        };
        fixture.fact(1, ExecutionPhase::Started, 4);
        fixture
    }

    /// Uses the real Node fact reducer and application activation path, without any robot call.
    fn fact(&mut self, sequence: u64, phase: ExecutionPhase, received: u64) {
        self.controller
            .bridge
            .consume(
                integration::GrpcNodeEvent::NodeMessage {
                    node_id: self.attempt.command().node_id().to_string(),
                    session_id: "s1".into(),
                    message: NodeMessage {
                        message: Some(NodePayload::ExecutionSnapshot(
                            integration::grpc::v0_4::ExecutionSnapshot {
                                session_id: "s1".into(),
                                execution_id: self.attempt.execution_id().into(),
                                last_sequence: sequence,
                                phase: phase as i32,
                                reason: "actual local result".into(),
                            },
                        )),
                    },
                },
                TimestampMs::new(received),
                &self.correlation,
            )
            .expect("owner fact reduces");
        apply_runtime_events(
            &mut self.controller,
            TimestampMs::new(received),
            &self.correlation,
            &mut self.events.clone(),
        )
        .expect("events apply");
    }

    /// Grants bounded explicit repetition; this alone cannot free a single resource.
    fn authorize(&mut self, timeout: u64) {
        self.controller
            .bridge
            .request_execution_recovery(
                self.attempt.execution_id(),
                self.attempt.command().node_id(),
                TimestampMs::new(5),
                timeout,
                2,
            )
            .expect("authorization");
    }

    /// Applies only the same application recovery stages used inside a durable timer transaction.
    fn recover_tick(&mut self, now: u64) {
        begin_current_ambiguity_recoveries(
            &mut self.controller,
            TimestampMs::new(now),
            &self.correlation,
            &mut self.events.clone(),
        )
        .expect("begin");
        resume_pending_recoveries(
            &mut self.controller,
            TimestampMs::new(now),
            &self.correlation,
            &mut self.events.clone(),
        )
        .expect("resume");
        drive_rebound_attempts(
            &mut self.controller,
            TimestampMs::new(now),
            &self.correlation,
        )
        .expect("replacement preparation");
    }

    /// Returns a complete comparable projection of Control resource ownership.
    fn allocations(&self) -> domain::AllocationViewSnapshot {
        self.controller
            .bridge
            .control()
            .allocation_snapshot(TimestampMs::new(10))
            .expect("allocation invariants")
    }
}

/// Unknown without stop authorization retains the binding; a genuine late completion can finish.
#[test]
fn unknown_does_not_release_or_repeat_a_running_operation() {
    let mut fixture = Fixture::new();
    let allocations = fixture.allocations();
    fixture.fact(2, ExecutionPhase::Unknown, 5);
    fixture.recover_tick(6);
    assert_eq!(fixture.allocations(), allocations);
    assert!(
        fixture
            .controller
            .bridge
            .control()
            .pending_role_recoveries()
            .is_empty()
    );
    assert_eq!(fixture.controller.bridge.attempt_history().len(), 1);
    fixture.fact(3, ExecutionPhase::Completed, 7);
    apply_runtime_outcomes(
        &mut fixture.controller,
        TimestampMs::new(7),
        &fixture.correlation,
        &mut fixture.events.clone(),
    )
    .expect("normal completion");
    assert_eq!(
        fixture
            .controller
            .orchestrator
            .execution(fixture.attempt.command().mission_id())
            .expect("Mission")
            .lifecycle(),
        orchestration::MissionExecutionLifecycle::Completed
    );
}

/// A real stop permits a new attempt even when the eligible replacement is the same Node.
#[test]
fn confirmed_stop_creates_one_new_attempt_with_intact_operation() {
    let mut fixture = Fixture::new();
    fixture.authorize(100);
    let before = fixture.allocations();
    fixture.recover_tick(6);
    assert_eq!(fixture.allocations(), before);
    fixture.fact(2, ExecutionPhase::Cancelled, 7);
    assert_eq!(
        fixture
            .controller
            .bridge
            .execution_recovery_stop(fixture.attempt.execution_id())
            .expect("intent")
            .confirmed_at_ms,
        Some(7)
    );
    fixture.recover_tick(8);
    let attempts = fixture.controller.bridge.attempt_history();
    assert_eq!(
        attempts.len(),
        2,
        "pending={:?}; group={:?}; events={:?}",
        fixture
            .controller
            .bridge
            .control()
            .pending_role_recoveries(),
        fixture
            .controller
            .bridge
            .control()
            .group(fixture.attempt.command().group_id()),
        fixture.events.events_page(None, 100).expect("trace")
    );
    let replacement = attempts
        .iter()
        .find(|item| item.execution_id() != fixture.attempt.execution_id())
        .expect("new attempt");
    assert_eq!(
        replacement.command().intent(),
        fixture.attempt.command().intent()
    );
    assert_eq!(
        replacement.command().task_ref(),
        fixture.attempt.command().task_ref()
    );
    assert_eq!(
        fixture
            .controller
            .bridge
            .execution_status(fixture.attempt.execution_id()),
        Some(orchestration::RemoteExecutionStatus::Cancelled)
    );
    fixture.recover_tick(9);
    assert_eq!(fixture.controller.bridge.attempt_history().len(), 2);
    assert!(
        fixture
            .controller
            .bridge
            .control()
            .pending_role_recoveries()
            .is_empty()
    );
    assert_eq!(
        fixture
            .controller
            .orchestrator
            .execution(fixture.attempt.command().mission_id())
            .expect("Mission")
            .lifecycle(),
        orchestration::MissionExecutionLifecycle::Running
    );
}

/// Stop timeout preserves physical ownership and cannot be disguised as an execution failure.
#[test]
fn stop_timeout_keeps_resources_and_exposes_budget_expiry() {
    let mut fixture = Fixture::new();
    fixture.authorize(2);
    let allocations = fixture.allocations();
    fixture.fact(2, ExecutionPhase::Unknown, 6);
    fixture.recover_tick(8);
    assert_eq!(fixture.allocations(), allocations);
    assert_eq!(
        fixture
            .controller
            .bridge
            .execution_recovery_disposition(fixture.attempt.execution_id(), TimestampMs::new(8)),
        runtime::RecoveryStopDisposition::BudgetExpired
    );
    fixture.fact(3, ExecutionPhase::Cancelled, 9);
    fixture.recover_tick(10);
    assert_eq!(fixture.allocations(), allocations);
    assert_eq!(fixture.controller.bridge.attempt_history().len(), 1);
    assert_eq!(
        fixture
            .controller
            .orchestrator
            .execution(fixture.attempt.command().mission_id())
            .expect("Mission")
            .lifecycle(),
        orchestration::MissionExecutionLifecycle::Running
    );
}

/// Current capacity absence stays explicitly pending after safe stop, without fabricated success.
/// Group cancellation overrides replacement even after the physical stop already arrived.
#[test]
fn group_cancel_aborts_confirmed_recovery_without_releasing_it_as_a_retry() {
    let mut fixture = Fixture::new();
    fixture.authorize(100);
    fixture.fact(2, ExecutionPhase::Cancelled, 7);
    fixture
        .controller
        .bridge
        .request_group_cancellation(fixture.attempt.command().group_id())
        .expect("group cancellation");
    assert_eq!(
        fixture
            .controller
            .bridge
            .execution_recovery_disposition(fixture.attempt.execution_id(), TimestampMs::new(8)),
        runtime::RecoveryStopDisposition::Aborted
    );
    let allocations = fixture.allocations();
    fixture.recover_tick(8);
    assert_eq!(fixture.allocations(), allocations);
    assert_eq!(fixture.controller.bridge.attempt_history().len(), 1);
}

/// Current capacity absence stays explicitly pending after safe stop, without fabricated success.
#[test]
fn stopped_role_with_no_available_replacement_stays_pending() {
    let mut fixture = Fixture::new();
    fixture.authorize(100);
    let mut state = fixture.controller.bridge.state().clone();
    for node in ["node-a", "node-b"] {
        fixture
            .controller
            .bridge
            .control_mut()
            .register_node(
                &mut state,
                recovery_driver_node(node, if node == "node-a" { "cpu-a" } else { "cpu-b" }),
                domain::NodeStatus::new(NodeHealth::Offline, TimestampMs::new(6)),
                TimestampMs::new(6),
                &fixture.correlation,
                &mut fixture.events.clone(),
            )
            .expect("offline registration");
    }
    // The application must use the actual State projection, not this local copy.
    *fixture.controller.bridge.state_mut() = state;
    fixture.fact(2, ExecutionPhase::Cancelled, 7);
    fixture.recover_tick(8);
    assert_eq!(
        fixture
            .controller
            .bridge
            .control()
            .pending_role_recoveries()
            .len(),
        1
    );
    assert_eq!(fixture.controller.bridge.attempt_history().len(), 1);
    assert_eq!(
        fixture
            .controller
            .bridge
            .execution_recovery_disposition(fixture.attempt.execution_id(), TimestampMs::new(8)),
        runtime::RecoveryStopDisposition::ReplacementPending
    );
}

/// Prepares a resource-bearing pending Commit under actual stop proof.
fn committed_fixture() -> (Fixture, control::RoleRecoveryNeed) {
    let mut fixture = Fixture::new();
    fixture.authorize(100);
    fixture.fact(2, ExecutionPhase::Cancelled, 7);
    begin_current_ambiguity_recoveries(
        &mut fixture.controller,
        TimestampMs::new(8),
        &fixture.correlation,
        &mut fixture.events.clone(),
    )
    .expect("safe partial release");
    let need = fixture
        .controller
        .bridge
        .control()
        .pending_role_recoveries()
        .into_iter()
        .next()
        .expect("pending");
    let requirement = fixture
        .controller
        .orchestrator
        .execution(fixture.attempt.command().mission_id())
        .expect("Mission")
        .plan()
        .task_graph()
        .tasks()[0]
        .requirement()
        .clone();
    let state = fixture.controller.bridge.state().clone();
    let ordinary = fixture
        .controller
        .bridge
        .control()
        .match_recovery_candidates_for_operation(
            &state,
            &need,
            &requirement,
            fixture.attempt.command().intent().operation(),
            TimestampMs::new(9),
            &fixture.correlation,
            &mut fixture.events.clone(),
        )
        .expect("ordinary matching");
    assert!(
        ordinary.is_empty(),
        "ordinary replacement excludes the bound Actor owner"
    );
    assert!(
        fixture
            .controller
            .bridge
            .control()
            .propose_role_recovery(
                &state,
                &ordinary,
                &requirement,
                domain::NodeId::new("node-a").expect("node"),
                vec![domain::ResourceId::new("cpu-a").expect("resource")],
                TimestampMs::new(9),
                &fixture.correlation,
                &mut fixture.events.clone(),
            )
            .is_err(),
        "ordinary candidates cannot silently authorize an owner retry"
    );
    let candidates = fixture
        .controller
        .bridge
        .control()
        .match_stopped_recovery_candidates_for_operation(
            &state,
            &need,
            &requirement,
            fixture.attempt.command().intent().operation(),
            TimestampMs::new(9),
            &fixture.correlation,
            &mut fixture.events.clone(),
        )
        .expect("match");
    assert_eq!(
        candidates.candidate_node_ids(),
        &[domain::NodeId::new("node-a").expect("bound owner")]
    );
    let proposal = fixture
        .controller
        .bridge
        .control()
        .propose_role_recovery(
            &state,
            &candidates,
            &requirement,
            domain::NodeId::new("node-a").expect("node"),
            vec![domain::ResourceId::new("cpu-a").expect("resource")],
            TimestampMs::new(9),
            &fixture.correlation,
            &mut fixture.events.clone(),
        )
        .expect("propose");
    fixture
        .controller
        .bridge
        .control_mut()
        .commit_role_recovery(
            &state,
            &requirement,
            &proposal,
            TimestampMs::new(9),
            &fixture.correlation,
            &mut fixture.events.clone(),
        )
        .expect("commit");
    (fixture, need)
}

/// Reuses a committed replacement only with original stop proof, preserved through restart.
pub(super) fn committed_recovery_resumes() {
    let (mut fixture, need) = committed_fixture();
    let checkpoint = fixture
        .controller
        .bridge
        .checkpoint_json()
        .expect("checkpoint");
    fixture.controller.bridge = IntegrationRuntimeBridge::restore_from_checkpoint(
        &checkpoint,
        fixture.events.clone(),
        integration::GrpcNodeRouter::default(),
        TimestampMs::new(10),
    )
    .expect("restore");
    resume_role_recovery(
        &mut fixture.controller,
        &need,
        TimestampMs::new(10),
        &fixture.correlation,
        &mut fixture.events.clone(),
    )
    .expect("existing commitment rebinds");
    assert!(
        fixture
            .controller
            .bridge
            .control()
            .pending_recovery_commitment_for_task(need.group_id(), need.task_ref(), need.role_id())
            .is_none()
    );
    assert_eq!(
        fixture
            .controller
            .bridge
            .control()
            .group(need.group_id())
            .expect("group")
            .task_execution(need.task_ref())
            .expect("task")
            .assignments()[0]
            .node_id()
            .as_str(),
        "node-a"
    );
    apply_recovery_required(
        &mut fixture.controller,
        fixture.attempt.command(),
        TimestampMs::new(11),
        &fixture.correlation,
        &mut fixture.events.clone(),
    )
    .expect("old stopped binding cannot release replacement");
    assert_eq!(
        fixture
            .controller
            .bridge
            .control()
            .group(need.group_id())
            .expect("group")
            .lifecycle(),
        control::GroupLifecycle::Adapted
    );
}

/// A timed-out pending Commit aborts replacement resources without starting a new attempt.
#[test]
fn expired_recovery_aborts_pending_commit_without_rebind() {
    let (mut fixture, need) = committed_fixture();
    resume_role_recovery(
        &mut fixture.controller,
        &need,
        TimestampMs::new(105),
        &fixture.correlation,
        &mut fixture.events.clone(),
    )
    .expect("expired commitment aborts");
    assert!(
        fixture
            .controller
            .bridge
            .control()
            .pending_recovery_commitment_for_task(need.group_id(), need.task_ref(), need.role_id())
            .is_none()
    );
    assert!(fixture.allocations().allocations().is_empty());
    assert_eq!(fixture.controller.bridge.attempt_history().len(), 1);
    assert_eq!(
        fixture
            .controller
            .bridge
            .control()
            .pending_role_recoveries()
            .len(),
        1
    );
}

/// Closed recovery input and readonly views travel through the production HTTP handler.
#[tokio::test]
async fn recovery_http_requires_authorization_and_preserves_stop_fence() {
    let fixture = Fixture::new();
    let execution = fixture.attempt.execution_id().to_string();
    let events = fixture.events.clone();
    let controller = Arc::new(Mutex::new(fixture.controller));
    let path = format!("/v1/executions/{execution}/recover");
    let mut body = serde_json::json!({"schema_version": "roboguide.execution-recovery-command/v0.1", "expected_node_id": fixture.attempt.command().node_id(), "repeat_authorized": false, "timeout_ms": 1000, "max_replacements": 2});
    assert!(
        admission::request(
            &controller,
            &events,
            "POST",
            &path,
            body.to_string().as_bytes()
        )
        .await
        .0
        .contains("400")
    );
    body["repeat_authorized"] = true.into();
    body["expected_node_id"] = "wrong-node".into();
    assert!(
        admission::request(
            &controller,
            &events,
            "POST",
            &path,
            body.to_string().as_bytes()
        )
        .await
        .0
        .contains("409")
    );
    body["expected_node_id"] = fixture.attempt.command().node_id().as_str().into();
    let (status, response) = admission::request(
        &controller,
        &events,
        "POST",
        &path,
        body.to_string().as_bytes(),
    )
    .await;
    assert!(status.contains("202"), "{status}: {response}");
    assert_eq!(response["status"], "recovery_stop_requested");
    assert_eq!(
        response["stop_intent"]["confirmed_at_ms"],
        serde_json::Value::Null
    );
    let read = format!("/v1/executions/{execution}/recovery");
    assert_eq!(
        admission::request(&controller, &events, "GET", &read, b"")
            .await
            .1["disposition"],
        "AwaitingStop"
    );
    for value in ["garbage", "0", "86400001"] {
        let path = format!("/v1/executions/{execution}/progress?stall_after_ms={value}");
        assert!(
            admission::request(&controller, &events, "GET", &path, b"")
                .await
                .0
                .contains("400")
        );
    }
    let path = format!("/v1/executions/{execution}/progress");
    assert_eq!(
        admission::request(&controller, &events, "GET", &path, b"")
            .await
            .1["disposition"],
        "Unknown"
    );
    assert!(
        controller
            .lock()
            .expect("lock")
            .bridge
            .control()
            .pending_role_recoveries()
            .is_empty()
    );
    assert!(events.load_checkpoint().expect("load").is_some());
}
