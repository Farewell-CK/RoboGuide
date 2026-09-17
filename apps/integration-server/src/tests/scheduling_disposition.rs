//! Application scheduling shortages must preserve durable progress and unrelated Missions.

use super::*;
use domain::{CorrelationId, ExecutionGroupId, TaskExecutionLifecycle, TimestampMs};
use integration::grpc::v0_4::{ExecutionPhase, NodeMessage, node_message::Message as NodePayload};
use orchestration::{SchedulingDeferral, SchedulingDisposition};

/// Builds two sequential Roles whose Actors must remain physically distinct across Tasks.
fn distinct_sequential_plan() -> domain::MissionPlan {
    let operation = serde_json::json!({"namespace": "compute", "name": "work", "version": "v1"});
    let tasks = ["a", "b"].map(|id| serde_json::json!({
        "id": id, "description": "distinct participation",
        "depends_on": if id == "a" { Vec::<&str>::new() } else { vec!["a"] },
        "context_id": "distinct-context", "coupling_mode": "independent",
        "timing": {"earliest_start_offset_ms": 0, "latest_start_offset_ms": null, "completion_deadline_offset_ms": null},
        "satisfaction": {"expected_effect": "work completed", "basis": "execution-report", "verifier": null},
        "roles": [{
            "id": "worker", "context_role": id, "resource_scope": "task",
            "requirements": {"capabilities": [{"contract": operation, "constraints": []}], "resources": []},
            "execution_intent": {"operation": operation, "objective": "distinct participation", "parameters": {}}
        }]
    }));
    decode_mission_plan(&serde_json::json!({
        "schema_version": "roboguide.mission-plan/v0.8",
        "mission": {"id": "distinct-mission", "objective": "distinct work", "actors": [{"id": "a"}, {"id": "b"}]},
        "contexts": [{"id": "distinct-context", "roles": [{"id": "a", "actor": "a"}, {"id": "b", "actor": "b"}],
            "relations": [], "coupling_mode": "independent",
            "executor_constraints": [{"kind": "distinct-physical-entities", "context_roles": ["a", "b"]}]}],
        "tasks": tasks
    }).to_string()).expect("canonical distinct Mission decodes")
}

/// Keeps the unrelated Mission canonical so the production checkpoint can round trip it.
fn unrelated_plan() -> domain::MissionPlan {
    let operation = serde_json::json!({"namespace": "compute", "name": "work", "version": "v1"});
    decode_mission_plan(&serde_json::json!({
        "schema_version": "roboguide.mission-plan/v0.7",
        "mission": {"id": "other-mission", "objective": "unrelated work", "actors": [{"id": "worker"}]},
        "contexts": [{"id": "other-context", "roles": [{"id": "worker", "actor": "worker"}], "relations": [], "coupling_mode": "independent"}],
        "tasks": [{"id": "other-task", "description": "unrelated work", "depends_on": [], "context_id": "other-context",
            "timing": {"earliest_start_offset_ms": 0, "latest_start_offset_ms": null, "completion_deadline_offset_ms": null},
            "satisfaction": {"expected_effect": "work completed", "basis": "execution-report", "verifier": null},
            "roles": [{"id": "worker", "context_role": "worker", "resource_scope": "task",
                "requirements": {"capabilities": [{"contract": operation, "constraints": []}], "resources": []},
                "execution_intent": {"operation": operation, "objective": "unrelated work", "parameters": {}}}]
        }]
    }).to_string()).expect("v0.7 Mission decodes")
}

/// Supplies current deployment topology without creating bindings or resources.
fn registry(revision: u64, entries: &[(&str, &str)]) -> domain::PhysicalEntityRegistrySnapshot {
    domain::PhysicalEntityRegistrySnapshot::new(
        domain::PhysicalEntityRegistryId::new("deployment").unwrap(),
        revision,
        domain::PhysicalEntityRoutingProfile::OneRoutableEntityPerNode,
        entries
            .iter()
            .map(|(entity, node)| {
                domain::PhysicalEntityRegistration::new(
                    domain::PhysicalEntityId::new(*entity).unwrap(),
                    domain::NodeId::new(*node).unwrap(),
                )
            })
            .collect(),
    )
    .expect("registry valid")
}

/// Delivers one ordered Node snapshot without directly changing Task satisfaction.
fn execution_fact(
    controller: &mut ControllerState,
    attempt: &runtime::ExecutionAttemptSnapshot,
    sequence: u64,
    phase: ExecutionPhase,
) {
    controller
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
                            reason: String::new(),
                        },
                    )),
                },
            },
            TimestampMs::new(4),
            &CorrelationId::new("cardinality-facts").unwrap(),
        )
        .expect("Runtime accepts ordered evidence");
}

/// A cardinality shortage survives the real timer transaction, then resumes after deployment grows.
#[test]
fn distinct_cardinality_defers_without_stopping_other_missions_and_retries() {
    let directory = tempfile::tempdir().unwrap();
    let mut events = state::SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
    let mut control = control::ControlPlane::new();
    let mut state = state::InMemorySharedNodeState::new();
    let correlation = CorrelationId::new("cardinality").unwrap();
    control
        .register_node(
            &mut state,
            recovery_driver_node("node-a", "cpu-a"),
            domain::NodeStatus::new(domain::NodeHealth::Online, TimestampMs::new(1)),
            TimestampMs::new(1),
            &correlation,
            &mut events,
        )
        .unwrap();
    control
        .install_physical_entity_registry(registry(1, &[("entity-x", "node-a")]))
        .unwrap();
    let plan = distinct_sequential_plan();
    let mission = plan.goal().mission_id().clone();
    let first = plan.task_graph().tasks()[0]
        .requirement()
        .task_ref()
        .clone();
    let second = plan.task_graph().tasks()[1]
        .requirement()
        .task_ref()
        .clone();
    let group_id = ExecutionGroupId::new("distinct-group").unwrap();
    let other = unrelated_plan();
    let other_id = other.goal().mission_id().clone();
    let mut orchestrator = MissionOrchestrator::new();
    for (plan, group) in [
        (plan, group_id.clone()),
        (other, ExecutionGroupId::new("other-group").unwrap()),
    ] {
        orchestrator
            .submit(
                plan,
                group,
                &mut control,
                TimestampMs::new(2),
                &correlation,
                &mut events,
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
    };
    drive_ready_tasks(
        &mut controller,
        TimestampMs::new(3),
        &correlation,
        &mut events,
    )
    .unwrap();
    let attempts = controller.bridge.attempt_history();
    assert_eq!(attempts.len(), 2);
    let first_attempt = attempts
        .iter()
        .find(|attempt| attempt.command().task_ref() == &first)
        .unwrap();
    let other_attempt = attempts
        .iter()
        .find(|attempt| attempt.command().task_ref().mission_id() == &other_id)
        .unwrap();
    execution_fact(&mut controller, first_attempt, 1, ExecutionPhase::Accepted);
    execution_fact(&mut controller, first_attempt, 2, ExecutionPhase::Completed);
    execution_fact(&mut controller, other_attempt, 1, ExecutionPhase::Accepted);
    let controller = Arc::new(Mutex::new(controller));
    let gate = Arc::new(Mutex::new(()));
    drive_application_timer(&controller, &events, &gate, TimestampMs::new(5))
        .expect("cardinality shortage must not reach the server fatal channel");
    {
        let live = controller.lock().unwrap();
        let group = live.bridge.control().group(&group_id).unwrap();
        assert_eq!(
            group.task_execution(&first).unwrap().lifecycle(),
            TaskExecutionLifecycle::Completed
        );
        let waiting = group.task_execution(&second).unwrap();
        assert_eq!(waiting.lifecycle(), TaskExecutionLifecycle::Ready);
        assert!(waiting.assignments().is_empty());
        assert!(
            live.bridge
                .control()
                .actor_binding(&mission, &domain::ActorId::new("b").unwrap())
                .is_none()
        );
        assert_eq!(
            live.orchestrator
                .execution(&mission)
                .unwrap()
                .scheduling_deferral(second.task_id()),
            Some(SchedulingDeferral::DistinctEntitiesUnavailable)
        );
        let restored =
            MissionOrchestrator::restore_json(&live.orchestrator.checkpoint_json().unwrap())
                .unwrap();
        assert_eq!(
            restored
                .execution(&mission)
                .unwrap()
                .scheduling_deferral(second.task_id()),
            Some(SchedulingDeferral::DistinctEntitiesUnavailable)
        );
    }
    assert!(events.load_checkpoint().unwrap().is_some());
    execution_fact(
        &mut controller.lock().unwrap(),
        other_attempt,
        2,
        ExecutionPhase::Completed,
    );
    drive_application_timer(&controller, &events, &gate, TimestampMs::new(6)).unwrap();
    assert_eq!(
        controller
            .lock()
            .unwrap()
            .orchestrator
            .execution(&other_id)
            .unwrap()
            .lifecycle(),
        orchestration::MissionExecutionLifecycle::Completed
    );
    let deferrals = events
        .decoded_events()
        .unwrap()
        .into_iter()
        .filter(|event| {
            matches!(event.payload(),
                domain::EventPayload::TaskSchedulingDeferred { task_ref, reason }
                    if task_ref == &second && reason == "distinct-entities-unavailable"
            )
        })
        .count();
    assert_eq!(
        deferrals, 1,
        "repeated timers must deduplicate shortage evidence"
    );
    {
        let mut live = controller.lock().unwrap();
        let mut state = live.bridge.state().clone();
        live.bridge
            .control_mut()
            .register_node(
                &mut state,
                recovery_driver_node("node-b", "cpu-b"),
                domain::NodeStatus::new(domain::NodeHealth::Online, TimestampMs::new(7)),
                TimestampMs::new(7),
                &correlation,
                &mut events,
            )
            .unwrap();
        *live.bridge.state_mut() = state;
        live.bridge
            .control_mut()
            .install_physical_entity_registry(registry(
                2,
                &[("entity-x", "node-a"), ("entity-y", "node-b")],
            ))
            .unwrap();
    }
    drive_application_timer(&controller, &events, &gate, TimestampMs::new(8)).unwrap();
    let mut live = controller.lock().unwrap();
    let resumed = live
        .bridge
        .control()
        .group(&group_id)
        .unwrap()
        .task_execution(&second)
        .unwrap();
    assert_eq!(resumed.assignments().len(), 1);
    assert_eq!(resumed.assignments()[0].node_id().as_str(), "node-b");
    assert_eq!(
        live.orchestrator
            .execution(&mission)
            .unwrap()
            .scheduling_deferral(second.task_id()),
        None
    );
    let second_attempt = live
        .bridge
        .attempt_history()
        .into_iter()
        .find(|attempt| attempt.command().task_ref() == &second)
        .unwrap();
    execution_fact(&mut live, &second_attempt, 1, ExecutionPhase::Accepted);
    execution_fact(&mut live, &second_attempt, 2, ExecutionPhase::Completed);
    drop(live);
    drive_application_timer(&controller, &events, &gate, TimestampMs::new(9)).unwrap();
    assert_eq!(
        controller
            .lock()
            .unwrap()
            .orchestrator
            .execution(&mission)
            .unwrap()
            .lifecycle(),
        orchestration::MissionExecutionLifecycle::Completed
    );
}

/// Only typed waiting conditions are nonfatal; diagnostic wording cannot change disposition.
#[test]
fn scheduling_disposition_distinguishes_waiting_contracts_and_internal_faults() {
    let no_candidate = OrchestrationError::Control(control::ControlError::NoCandidate(
        domain::RoleId::new("role").unwrap(),
    ));
    assert_eq!(
        no_candidate.scheduling_disposition(),
        SchedulingDisposition::Deferred(SchedulingDeferral::NoCandidate)
    );
    let reconcile =
        OrchestrationError::Control(control::ControlError::ActorBindingRequiresReconciliation {
            mission_id: domain::MissionId::new("mission").unwrap(),
            actor_id: domain::ActorId::new("actor").unwrap(),
            node_id: domain::NodeId::new("node").unwrap(),
        });
    assert_eq!(
        reconcile.scheduling_disposition(),
        SchedulingDisposition::ReconciliationRequired
    );
    assert_eq!(
        decode_mission_plan("{}")
            .unwrap_err()
            .scheduling_disposition(),
        SchedulingDisposition::InvalidContract
    );
    assert_eq!(
        OrchestrationError::Mission("no feasible interval; joint scheduling deferred".into())
            .scheduling_disposition(),
        SchedulingDisposition::InternalFailure
    );
    assert_eq!(
        OrchestrationError::Control(control::ControlError::AllocationInvariant(
            "deferred".into()
        ))
        .scheduling_disposition(),
        SchedulingDisposition::InternalFailure
    );
}
