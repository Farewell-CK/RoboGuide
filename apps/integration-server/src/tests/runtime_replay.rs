//! Parallel Task facts must respect Group recovery fences at the application boundary.

use super::*;
use domain::{CorrelationId, ExecutionGroupId, TaskExecutionLifecycle, TimestampMs};
use integration::grpc::v0_4::{ExecutionPhase, NodeMessage, node_message::Message as NodePayload};

/// Decodes a canonical v0.7 Mission so production checkpoint normalization is also exercised.
fn parallel_plan() -> domain::MissionPlan {
    let operation = serde_json::json!({"namespace": "compute", "name": "work", "version": "v1"});
    let tasks = ["a", "b"].map(|id| serde_json::json!({
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
        let directory = tempfile::tempdir().expect("isolated controller directory");
        let events = state::SqliteEventLog::open(directory.path().join("events.sqlite3"))
            .expect("event log opens");
        let correlation = CorrelationId::new("parallel-replay").expect("correlation");
        let group_id = ExecutionGroupId::new("parallel-group").expect("Group");
        let plan = parallel_plan();
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
