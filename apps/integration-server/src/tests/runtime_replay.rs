//! Parallel Task facts must respect Group recovery fences at the application boundary.

use super::*;
use domain::{CorrelationId, ExecutionGroupId, TaskExecutionLifecycle, TimestampMs};
use integration::grpc::v0_4::{ExecutionPhase, NodeMessage, node_message::Message as NodePayload};
use sha2::{Digest, Sha256};

/// Decodes a canonical v0.7 Mission so production checkpoint normalization is also exercised.
fn parallel_plan() -> domain::MissionPlan {
    parallel_plan_with_verifier(false)
}

/// Builds the same physical topology with an optional independent final verifier.
fn parallel_plan_with_verifier(verifier_first_task: bool) -> domain::MissionPlan {
    let operation = serde_json::json!({"namespace": "compute", "name": "work", "version": "v1"});
    let mut tasks = ["a", "b"].map(|id| serde_json::json!({
        "id": id, "description": "independent work", "depends_on": [],
        "context_id": "parallel-context", "coupling_mode": "independent",
        "timing": {"earliest_start_offset_ms": 0, "latest_start_offset_ms": null, "completion_deadline_offset_ms": null},
        "satisfaction": {"expected_effect": "work completed", "basis": "execution-report", "verifier": null},
        "roles": [{
            "id": "worker", "context_role": id, "resource_scope": "task",
            "requirements": {
                "capabilities": [{"contract": operation, "constraints": []}],
                "resources": [{"kind": "compute", "units": 1}]
            },
            "execution_intent": {"operation": operation, "objective": "independent work", "parameters": {}}
        }]
    }));
    if verifier_first_task {
        tasks[0]["satisfaction"] = serde_json::json!({
            "expected_effect": "goal-predicate", "basis": "verifier-evidence",
            "verifier": {
                "contract": {"namespace": "observation", "name": "verify", "version": "v1"},
                "predicate": "goal-predicate", "max_evidence_age_ms": 5000
            }
        });
    }
    orchestration::decode_mission_plan(&serde_json::json!({
        "schema_version": "roboguide.mission-plan/v0.7",
        "mission": {"id": "parallel-mission", "objective": "parallel work", "actors": [{"id": "a"}, {"id": "b"}]},
        "contexts": [{"id": "parallel-context", "roles": [{"id": "a", "actor": "a"}, {"id": "b", "actor": "b"}],
            "relations": [], "coupling_mode": "independent"}],
        "tasks": tasks
    }).to_string()).expect("canonical MissionPlan decodes")
}

/// Owns two independent bound Tasks, real application authorities, and their durable event log.
struct ParallelMission {
    /// Keeps the isolated SQLite event database alive.
    _directory: tempfile::TempDir,
    /// Production Control/Runtime/Orchestration composition.
    controller: ControllerState,
    /// Shared event and checkpoint persistence.
    events: state::SqliteEventLog,
    /// Correlation used for deterministic fact application.
    correlation: CorrelationId,
    /// Mission-level Group shared by both independent Tasks.
    group_id: ExecutionGroupId,
    /// Physical attempts produced by the real scheduling and dispatch path.
    attempts: Vec<runtime::ExecutionAttemptSnapshot>,
}

impl ParallelMission {
    /// Runs Submit -> Match -> Schedule -> Proposal -> Commit -> Bind -> Runtime preparation.
    fn new() -> Self {
        Self::new_with_verifier(false)
    }

    /// Runs the same real application path with one Task awaiting external evidence.
    fn new_with_verifier(verifier_first_task: bool) -> Self {
        let directory = tempfile::tempdir().expect("isolated controller directory");
        let events = state::SqliteEventLog::open(directory.path().join("events.sqlite3"))
            .expect("event log opens");
        let correlation = CorrelationId::new("parallel-replay").expect("correlation");
        let group_id = ExecutionGroupId::new("parallel-group").expect("Group");
        let plan = if verifier_first_task {
            parallel_plan_with_verifier(true)
        } else {
            parallel_plan()
        };
        let mut control = control::ControlPlane::new();
        let mut state = state::InMemorySharedNodeState::new();
        for (node, resource) in [("node-a", "cpu-a"), ("node-b", "cpu-b")] {
            control
                .register_node(
                    &mut state,
                    recovery_driver_node(node, resource),
                    domain::NodeStatus::new(domain::NodeHealth::Online, TimestampMs::new(1)),
                    TimestampMs::new(1),
                    &correlation,
                    &mut events.clone(),
                )
                .expect("register Node");
        }
        let mut orchestrator = MissionOrchestrator::new();
        orchestrator
            .submit(
                plan,
                group_id.clone(),
                &mut control,
                TimestampMs::new(2),
                &correlation,
                &mut events.clone(),
            )
            .expect("submit Mission");
        let mut controller = ControllerState {
            bridge: IntegrationRuntimeBridge::new(
                control,
                state,
                events.clone(),
                integration::GrpcNodeRouter::default(),
            ),
            orchestrator,
            verifier_seen: BTreeSet::new(),
            verifier_source_digest: None,
        };
        drive_ready_tasks(
            &mut controller,
            TimestampMs::new(3),
            &correlation,
            &mut events.clone(),
        )
        .expect("both Tasks prepare dispatch");
        let attempts = controller.bridge.attempt_history();
        assert_eq!(attempts.len(), 2);
        assert_ne!(
            attempts[0].command().node_id(),
            attempts[1].command().node_id()
        );
        Self {
            _directory: directory,
            controller,
            events,
            correlation,
            group_id,
            attempts,
        }
    }

    /// Injects ordered Node facts through the same bridge and lifecycle handlers as the server.
    fn fact(&mut self, index: usize, sequence: u64, phase: ExecutionPhase) {
        let attempt = &self.attempts[index];
        self.controller
            .bridge
            .consume(
                integration::GrpcNodeEvent::NodeMessage {
                    node_id: attempt.command().node_id().to_string(),
                    session_id: "session".into(),
                    message: NodeMessage {
                        message: Some(NodePayload::ExecutionSnapshot(
                            integration::grpc::v0_4::ExecutionSnapshot {
                                session_id: "session".into(),
                                execution_id: attempt.execution_id().to_string(),
                                last_sequence: sequence,
                                phase: phase as i32,
                                reason: if phase == ExecutionPhase::Unknown {
                                    "local outcome ambiguous".into()
                                } else {
                                    String::new()
                                },
                            },
                        )),
                    },
                },
                TimestampMs::new(10),
                &self.correlation,
            )
            .expect("fact persists in Runtime");
        apply_runtime_events(
            &mut self.controller,
            TimestampMs::new(10),
            &self.correlation,
            &mut self.events.clone(),
        )
        .expect("fact lifecycle handling is not fatal");
        self.apply_outcomes();
    }

    /// Applies eligible outcomes and checkpoints them exactly as the application transaction does.
    fn apply_outcomes(&mut self) {
        self.events
            .begin_batch()
            .expect("outcome transaction begins");
        apply_runtime_outcomes(
            &mut self.controller,
            TimestampMs::new(10),
            &self.correlation,
            &mut self.events.clone(),
        )
        .expect("terminal outcome handling is not fatal");
        self.events
            .save_checkpoint(
                SERVER_CHECKPOINT_SCHEMA,
                &server_checkpoint_json(&self.controller).expect("checkpoint serializes"),
            )
            .expect("application remains checkpointable");
        self.events
            .commit_batch()
            .expect("outcome transaction commits");
    }

    /// Returns the authoritative Task lifecycle for one original logical slot.
    fn task_lifecycle(&self, index: usize) -> TaskExecutionLifecycle {
        self.controller
            .bridge
            .control()
            .group(&self.group_id)
            .expect("Group exists")
            .task_execution(self.attempts[index].command().task_ref())
            .expect("Task exists")
            .lifecycle()
    }
}

/// Signs one finite JSON document using the configured verifier codec.
fn signed_verifier(mut body: serde_json::Value) -> serde_json::Value {
    let encoded = serde_json::to_vec(&body).expect("verifier fixture serializes");
    body["digest"] = format!("sha256:{:x}", Sha256::digest(encoded)).into();
    body
}

/// Publishes source and verdict fixtures with the exact Runtime attempt identities.
fn verifier_feed_for(
    fixture: &mut ParallelMission,
    satisfied: bool,
    stale_attempt: bool,
) -> TaskVerifierFeed {
    let source = signed_verifier(serde_json::json!({
        "schema_version": "roboguide.task-verifier-source/v0.1",
        "source_id": "test-official-goal",
        "source_revision": format!("sha256:{}", "a".repeat(64)),
        "identity": {"run_id": "run-1", "episode_id": "episode-1", "scene_id": "scene-1",
            "dataset_revision": "dataset-1", "dataset_sha256": "b".repeat(64)},
        "verifier": {"namespace": "observation", "name": "verify", "version": "v1"},
        "supported_predicates": ["goal-predicate"],
        "verdict_finality": "terminal"
    }));
    let source_path = fixture._directory.path().join("source.json");
    let verdict_path = fixture._directory.path().join("verdict.json");
    std::fs::write(&source_path, source.to_string()).expect("source written");
    let tasks = fixture
        .attempts
        .iter()
        .map(|attempt| {
            serde_json::json!({
                "mission_id": attempt.command().mission_id().as_str(),
                "task_id": attempt.command().task_id().as_str(),
                "attempts": [{
                    "role_id": attempt.command().role_id().as_str(),
                    "attempt_id": if stale_attempt && attempt.command().task_id().as_str() == "a" {
                        "old-attempt"
                    } else {attempt.execution_id()}
                }]
            })
        })
        .collect::<Vec<_>>();
    let verdict = signed_verifier(serde_json::json!({
        "schema_version": "roboguide.task-verifier-verdict/v0.1",
        "source_digest": source["digest"],
        "source_id": "test-official-goal",
        "verifier": source["verifier"],
        "predicate": "goal-predicate", "source_observed_at_ms": 9,
        "satisfied": satisfied,
        "tasks": tasks
    }));
    std::fs::write(&verdict_path, verdict.to_string()).expect("verdict written");
    let feed = TaskVerifierFeed::load(&source_path, verdict_path).expect("source loads");
    fixture.controller.verifier_source_digest = Some(feed.source_digest().to_string());
    feed
}

/// A real Runtime completion remains AwaitingSatisfaction until current-attempt evidence arrives.
#[test]
fn current_positive_verifier_verdict_completes_mission_durably() {
    let mut fixture = ParallelMission::new_with_verifier(true);
    for index in 0..2 {
        fixture.fact(index, 1, ExecutionPhase::Accepted);
        fixture.fact(index, 2, ExecutionPhase::Completed);
    }
    let verified_index = fixture
        .attempts
        .iter()
        .position(|attempt| attempt.command().task_id().as_str() == "a")
        .expect("verified Task has an attempt");
    assert_eq!(
        fixture.task_lifecycle(verified_index),
        TaskExecutionLifecycle::AwaitingSatisfaction
    );
    let feed = verifier_feed_for(&mut fixture, true, false);
    let verdict = feed
        .read_verdict()
        .expect("verdict parses")
        .expect("verdict exists");
    validate_task_verifier(&fixture.controller, &feed, &verdict)
        .expect("both current physical attempts match");
    fixture.events.begin_batch().expect("transaction opens");
    apply_task_verifier(
        &mut fixture.controller,
        &feed,
        &verdict,
        TimestampMs::new(12),
        &fixture.correlation,
        &mut fixture.events.clone(),
    )
    .expect("positive verifier closes Task");
    let checkpoint = server_checkpoint_json(&fixture.controller).expect("checkpoint serializes");
    fixture
        .events
        .save_checkpoint(SERVER_CHECKPOINT_SCHEMA, &checkpoint)
        .expect("checkpoint persists");
    fixture.events.commit_batch().expect("transaction commits");
    assert_eq!(
        fixture.task_lifecycle(verified_index),
        TaskExecutionLifecycle::Completed
    );
    assert_eq!(fixture.controller.verifier_seen.len(), 1);
    assert_eq!(
        fixture
            .controller
            .orchestrator
            .execution(fixture.attempts[verified_index].command().mission_id())
            .expect("Mission")
            .lifecycle(),
        orchestration::MissionExecutionLifecycle::Completed
    );
    let restored: ServerCheckpoint = serde_json::from_str(&checkpoint).expect("checkpoint decodes");
    assert_eq!(restored.verifier_seen, fixture.controller.verifier_seen);
    assert_eq!(
        restored.verifier_source_digest,
        fixture.controller.verifier_source_digest
    );
    let events = fixture
        .events
        .events_page(None, 500)
        .expect("events readable");
    assert!(
        events
            .iter()
            .any(|event| event.payload_json.contains("TaskVerifierVerdictObserved"))
    );
    assert!(
        events
            .iter()
            .any(|event| event.payload_json.contains("TaskSatisfied"))
    );
    let prior_sequence = fixture.events.latest_sequence().expect("sequence readable");
    validate_task_verifier(&fixture.controller, &feed, &verdict)
        .expect("durably consumed verdict is an idempotent replay");
    fixture
        .events
        .begin_batch()
        .expect("replay transaction opens");
    apply_task_verifier(
        &mut fixture.controller,
        &feed,
        &verdict,
        TimestampMs::new(13),
        &fixture.correlation,
        &mut fixture.events.clone(),
    )
    .expect("replay is a no-op");
    fixture
        .events
        .commit_batch()
        .expect("replay transaction commits");
    assert_eq!(
        fixture.events.latest_sequence().expect("sequence readable"),
        prior_sequence
    );
}

/// A final negative official verdict fails the Task without claiming benchmark success.
#[test]
fn current_negative_verifier_verdict_fails_mission() {
    let mut fixture = ParallelMission::new_with_verifier(true);
    for index in 0..2 {
        fixture.fact(index, 1, ExecutionPhase::Accepted);
        fixture.fact(index, 2, ExecutionPhase::Completed);
    }
    let feed = verifier_feed_for(&mut fixture, false, false);
    let verdict = feed
        .read_verdict()
        .expect("verdict parses")
        .expect("verdict exists");
    validate_task_verifier(&fixture.controller, &feed, &verdict).expect("current attempts match");
    fixture.events.begin_batch().expect("transaction opens");
    apply_task_verifier(
        &mut fixture.controller,
        &feed,
        &verdict,
        TimestampMs::new(12),
        &fixture.correlation,
        &mut fixture.events.clone(),
    )
    .expect("final negative verifier fails Task");
    fixture.events.commit_batch().expect("transaction commits");
    assert_eq!(
        fixture
            .controller
            .orchestrator
            .execution(fixture.attempts[0].command().mission_id())
            .expect("Mission")
            .lifecycle(),
        orchestration::MissionExecutionLifecycle::Failed
    );
    assert!(
        !fixture
            .events
            .events_page(None, 500)
            .expect("events readable")
            .iter()
            .any(|event| event.payload_json.contains("TaskSatisfied")
                && event.payload_json.contains("\"task_id\":\"a\""))
    );
}

/// A late final-world verdict cannot reopen a Group released after physical failure.
#[test]
fn late_verifier_after_runtime_failure_keeps_timer_alive() {
    for satisfied in [false, true] {
        let mut fixture = ParallelMission::new_with_verifier(true);
        for index in 0..2 {
            fixture.fact(index, 1, ExecutionPhase::Accepted);
        }
        let verified_index = fixture
            .attempts
            .iter()
            .position(|attempt| attempt.command().task_id().as_str() == "a")
            .expect("verified Task has an attempt");
        fixture.fact(verified_index, 2, ExecutionPhase::Completed);
        fixture.fact(1 - verified_index, 2, ExecutionPhase::Failed);
        assert_eq!(
            fixture
                .controller
                .bridge
                .control()
                .group(&fixture.group_id)
                .expect("Group remains inspectable")
                .lifecycle(),
            control::GroupLifecycle::Released
        );
        let feed = verifier_feed_for(&mut fixture, satisfied, false);
        let sequence_before = fixture.events.latest_sequence().expect("events readable");
        let controller = Arc::new(Mutex::new(fixture.controller.clone()));
        let gate = Arc::new(Mutex::new(()));
        drive_application_timer(
            &controller,
            &fixture.events,
            &gate,
            TimestampMs::new(12),
            Some(&feed),
        )
        .expect("late verdict is irrelevant to a terminal Mission");
        let observed = controller.lock().expect("Controller remains live");
        assert_eq!(
            observed
                .orchestrator
                .execution(fixture.attempts[verified_index].command().mission_id())
                .expect("Mission remains inspectable")
                .lifecycle(),
            orchestration::MissionExecutionLifecycle::Failed
        );
        assert!(observed.verifier_seen.is_empty());
        assert_eq!(
            fixture.events.latest_sequence().expect("events readable"),
            sequence_before
        );
    }
}

/// A signed artifact from a superseded physical attempt remains invalid.
#[test]
fn stale_physical_attempt_verdict_is_rejected_before_mutation() {
    let mut fixture = ParallelMission::new_with_verifier(true);
    for index in 0..2 {
        fixture.fact(index, 1, ExecutionPhase::Accepted);
        fixture.fact(index, 2, ExecutionPhase::Completed);
    }
    let feed = verifier_feed_for(&mut fixture, true, true);
    let verdict = feed
        .read_verdict()
        .expect("verdict parses")
        .expect("verdict exists");
    assert!(validate_task_verifier(&fixture.controller, &feed, &verdict).is_err());
    assert!(fixture.controller.verifier_seen.is_empty());
    assert!(fixture.attempts.iter().any(|attempt| {
        fixture
            .controller
            .bridge
            .control()
            .group(&fixture.group_id)
            .and_then(|group| group.task_execution(attempt.command().task_ref()))
            .is_some_and(|task| task.lifecycle() == TaskExecutionLifecycle::AwaitingSatisfaction)
    }));
}

/// Two independent Nodes finish normally through production outcome and satisfaction handling.
#[test]
fn parallel_task_snapshots_complete_one_mission_without_recovery() {
    let mut fixture = ParallelMission::new();
    fixture.fact(0, 1, ExecutionPhase::Accepted);
    fixture.fact(1, 1, ExecutionPhase::Accepted);
    fixture.fact(1, 2, ExecutionPhase::Completed);
    fixture.fact(0, 2, ExecutionPhase::Completed);
    assert_eq!(fixture.task_lifecycle(0), TaskExecutionLifecycle::Completed);
    assert_eq!(fixture.task_lifecycle(1), TaskExecutionLifecycle::Completed);
    assert_eq!(
        fixture
            .controller
            .orchestrator
            .execution(fixture.attempts[0].command().mission_id())
            .expect("Mission")
            .lifecycle(),
        orchestration::MissionExecutionLifecycle::Completed
    );
    assert!(
        fixture
            .controller
            .bridge
            .control()
            .pending_role_recoveries()
            .is_empty()
    );
}

/// A sibling completion while recovery blocks the Group is retained and safely deferred.
#[test]
fn blocked_parallel_group_retains_late_completion_without_fatal_transition() {
    let mut fixture = ParallelMission::new();
    fixture.fact(0, 1, ExecutionPhase::Accepted);
    fixture.fact(1, 1, ExecutionPhase::Accepted);
    fixture.fact(0, 2, ExecutionPhase::Unknown);
    let control_before =
        serde_json::to_value(fixture.controller.bridge.control().checkpoint()).expect("checkpoint");
    fixture.fact(0, 3, ExecutionPhase::Completed);
    fixture.fact(1, 2, ExecutionPhase::Completed);
    assert_eq!(
        serde_json::to_value(fixture.controller.bridge.control().checkpoint()).expect("checkpoint"),
        control_before
    );
    assert_eq!(
        fixture
            .controller
            .bridge
            .control()
            .group(&fixture.group_id)
            .expect("Group")
            .lifecycle(),
        control::GroupLifecycle::Blocked
    );
    assert_eq!(fixture.task_lifecycle(1), TaskExecutionLifecycle::Active);
    for attempt in &fixture.attempts {
        assert_eq!(
            fixture
                .controller
                .bridge
                .execution_status(attempt.execution_id()),
            Some(orchestration::RemoteExecutionStatus::Completed)
        );
    }
    assert!(
        fixture
            .controller
            .bridge
            .terminal_task_execution_outcomes()
            .is_empty()
    );
}

/// A terminal fact arriving before acceptance must not activate a role already released by recovery.
#[test]
fn late_first_terminal_fact_does_not_activate_unbound_task() {
    let mut fixture = ParallelMission::new();
    fixture.fact(0, 1, ExecutionPhase::Unknown);
    let control_before =
        serde_json::to_value(fixture.controller.bridge.control().checkpoint()).expect("checkpoint");
    drive_ready_tasks(
        &mut fixture.controller,
        TimestampMs::new(10),
        &fixture.correlation,
        &mut fixture.events.clone(),
    )
    .expect("normal dispatch defers recovery-owned Task");
    assert_eq!(
        serde_json::to_value(fixture.controller.bridge.control().checkpoint()).expect("checkpoint"),
        control_before
    );
    assert_eq!(fixture.controller.bridge.attempt_history().len(), 2);
    fixture.fact(0, 2, ExecutionPhase::Completed);
    assert_eq!(fixture.task_lifecycle(0), TaskExecutionLifecycle::Ready);
    assert_eq!(
        fixture
            .controller
            .bridge
            .control()
            .group(&fixture.group_id)
            .expect("Group")
            .lifecycle(),
        control::GroupLifecycle::Blocked
    );
}

/// A failed sibling fact is retained without converting a Blocked Group through an illegal path.
#[test]
fn blocked_parallel_group_defers_sibling_failure() {
    let mut fixture = ParallelMission::new();
    fixture.fact(0, 1, ExecutionPhase::Accepted);
    fixture.fact(1, 1, ExecutionPhase::Accepted);
    fixture.fact(0, 2, ExecutionPhase::Unknown);
    fixture.fact(1, 2, ExecutionPhase::Failed);
    assert_eq!(fixture.task_lifecycle(1), TaskExecutionLifecycle::Active);
    assert_eq!(
        fixture
            .controller
            .bridge
            .execution_status(fixture.attempts[1].execution_id()),
        Some(orchestration::RemoteExecutionStatus::Failed)
    );
    assert_eq!(
        fixture
            .controller
            .orchestrator
            .execution(fixture.attempts[0].command().mission_id())
            .expect("Mission")
            .lifecycle(),
        orchestration::MissionExecutionLifecycle::Running
    );
}
