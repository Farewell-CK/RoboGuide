//! Logical fairness, persisted aging and admission capacity use actual accepted plans and Groups.

use super::*;

/// Builds independent normalized roots with exact resource contracts and logical Actors.
pub(crate) fn plan(mission: &str, count: usize) -> MissionPlan {
    let operation = serde_json::json!({"namespace":"compute","name":"work","version":"v1"});
    decode_mission_plan(&serde_json::json!({
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
    }).to_string()).unwrap()
}

/// Accepts exact plans through normal Group creation with no fabricated execution or resource claim.
fn fixture() -> (MissionOrchestrator, ControlPlane, InMemoryEventLog) {
    let mut orchestrator = MissionOrchestrator::new();
    let mut control = ControlPlane::new();
    let mut events = InMemoryEventLog::new();
    for mission in ["a", "b"] {
        orchestrator
            .submit(
                plan(mission, 2),
                ExecutionGroupId::new(format!("group-{mission}")).unwrap(),
                &mut control,
                TimestampMs::new(1),
                &CorrelationId::new("policy-setup").unwrap(),
                &mut events,
            )
            .unwrap();
    }
    (orchestrator, control, events)
}

/// One Mission's many roots cannot consume a second round turn before another Mission's first turn.
#[test]
fn round_fairness_and_checkpoint_restore_preserve_service_history() {
    let (mut orchestrator, control, _) = fixture();
    let mut attempted = BTreeSet::new();
    let mut served = BTreeSet::new();
    let first = orchestrator
        .next_dispatch_task(&control, TimestampMs::new(2), &attempted, &served)
        .unwrap()
        .unwrap();
    assert_eq!(first.mission_id().as_str(), "a");
    orchestrator.record_dispatch_turn(&first).unwrap();
    attempted.insert(first.clone());
    served.insert(first.mission_id().clone());
    let checkpoint = orchestrator.checkpoint_json().unwrap();
    let mut restored = MissionOrchestrator::restore_json(&checkpoint).unwrap();
    let second = restored
        .next_dispatch_task(&control, TimestampMs::new(2), &attempted, &served)
        .unwrap()
        .unwrap();
    assert_eq!(second.mission_id().as_str(), "b");
    restored.record_dispatch_turn(&second).unwrap();
    assert_eq!(
        restored
            .execution(second.mission_id())
            .unwrap()
            .dispatch_policy()
            .last_service_ordinal(),
        2
    );
    assert!(
        control
            .allocation_snapshot(TimestampMs::new(2))
            .unwrap()
            .allocations()
            .is_empty()
    );
}

/// A continuous high-priority stream eventually yields a logical turn to an older low-priority Task.
#[test]
fn persisted_aging_prevents_fixed_priority_starvation() {
    let (mut orchestrator, control, mut events) = fixture();
    orchestrator
        .set_mission_priority(
            &MissionId::new("a").unwrap(),
            255,
            TimestampMs::new(2),
            &CorrelationId::new("priority").unwrap(),
            &mut events,
        )
        .unwrap();
    orchestrator
        .set_mission_priority(
            &MissionId::new("b").unwrap(),
            0,
            TimestampMs::new(2),
            &CorrelationId::new("priority").unwrap(),
            &mut events,
        )
        .unwrap();
    let mut reached = false;
    for ordinal in 0..2050 {
        let next = orchestrator
            .next_dispatch_task(
                &control,
                TimestampMs::new(3),
                &BTreeSet::new(),
                &BTreeSet::new(),
            )
            .unwrap()
            .unwrap();
        if next.mission_id().as_str() == "b" {
            reached = true;
            break;
        }
        orchestrator.record_dispatch_turn(&next).unwrap();
        if ordinal == 1024 {
            orchestrator =
                MissionOrchestrator::restore_json(&orchestrator.checkpoint_json().unwrap())
                    .unwrap();
        }
    }
    assert!(
        reached,
        "immutable waiting age must overcome the complete explicit priority range"
    );
    assert!(
        control
            .allocation_snapshot(TimestampMs::new(3))
            .unwrap()
            .allocations()
            .is_empty()
    );
}

/// Live held/cancelling Missions count against admission while exact retries remain idempotent.
#[test]
fn admission_rejection_precedes_group_mutation_and_preserves_exact_retry() {
    let (mut orchestrator, mut control, mut events) = fixture();
    orchestrator.set_admission_limits(MissionAdmissionLimits::try_new(2, 4).unwrap());
    orchestrator
        .isolate_execution(
            &MissionId::new("a").unwrap(),
            MissionIsolationSource::ReadyDispatch,
            TimestampMs::new(2),
            &CorrelationId::new("hold").unwrap(),
            &mut events,
        )
        .unwrap();
    orchestrator
        .request_cancel(
            &MissionId::new("b").unwrap(),
            &mut control,
            TimestampMs::new(2),
            &CorrelationId::new("cancel").unwrap(),
            &mut events,
        )
        .unwrap();
    let before = serde_json::to_string(&control.checkpoint()).unwrap();
    let trace = events.records().len();
    assert!(matches!(
        orchestrator.submit(
            plan("c", 1),
            ExecutionGroupId::new("group-c").unwrap(),
            &mut control,
            TimestampMs::new(2),
            &CorrelationId::new("quota").unwrap(),
            &mut events
        ),
        Err(OrchestrationError::AdmissionCapacityExceeded)
    ));
    assert_eq!(
        serde_json::to_string(&control.checkpoint()).unwrap(),
        before
    );
    assert_eq!(events.records().len(), trace);
    orchestrator
        .submit(
            plan("a", 2),
            ExecutionGroupId::new("group-a").unwrap(),
            &mut control,
            TimestampMs::new(2),
            &CorrelationId::new("quota").unwrap(),
            &mut events,
        )
        .unwrap();
    assert_eq!(events.records().len(), trace);
}

/// Legacy policy omission is compatible; fabricated waiting slots, future ordinals or priorities reject.
#[test]
fn restored_policy_rejects_unproven_history_and_accepts_legacy_defaults() {
    let (orchestrator, _, _) = fixture();
    let original: Vec<serde_json::Value> =
        serde_json::from_str(&orchestrator.checkpoint_json().unwrap()).unwrap();
    let mut legacy = original.clone();
    for mission in &mut legacy {
        mission.as_object_mut().unwrap().remove("dispatch_policy");
    }
    let restored =
        MissionOrchestrator::restore_json(&serde_json::to_string(&legacy).unwrap()).unwrap();
    assert_eq!(
        restored
            .execution(&MissionId::new("a").unwrap())
            .unwrap()
            .dispatch_policy()
            .priority(),
        128
    );
    for change in [
        serde_json::json!({"priority":256,"last_service":0,"waiting":{}}),
        serde_json::json!({"priority":128,"last_service":0,"waiting":{"missing-task":0}}),
        serde_json::json!({"priority":128,"last_service":0,"waiting":{"task-0":1}}),
    ] {
        let mut bad = original.clone();
        bad[0]["dispatch_policy"] = change;
        assert!(MissionOrchestrator::restore_json(&serde_json::to_string(&bad).unwrap()).is_err());
    }
}

/// A committed due interval keeps its first activation opportunity ahead of an unreserved higher priority.
#[test]
fn due_calendar_intervals_precede_unreserved_priority() {
    let future = super::scheduling::scheduled_phase1_plan();
    let mission = future.goal().mission_id().clone();
    let task = future.task_graph().tasks()[0]
        .requirement()
        .task_ref()
        .clone();
    let role = &future.task_graph().tasks()[0].requirement().roles()[0];
    let contracts = future
        .actor_requirements()
        .get(role.actor_id().unwrap())
        .unwrap()
        .iter()
        .map(|requirement| (requirement.contract().clone(), role.capability().unwrap()))
        .collect::<Vec<_>>();
    let capabilities = contracts
        .iter()
        .map(|(_, kind)| Capability::new(*kind, true))
        .collect::<Vec<_>>();
    let mut control = ControlPlane::new();
    let mut state = InMemorySharedNodeState::new();
    let mut events = InMemoryEventLog::new();
    let correlation = CorrelationId::new("due-policy").unwrap();
    control
        .register_node(
            &mut state,
            super::lifecycle::registration(
                "future-node",
                capabilities,
                contracts,
                vec![(
                    ResourceId::new("future-cpu").unwrap(),
                    ResourceKind::Compute,
                )],
            ),
            NodeStatus::new(NodeHealth::Online, TimestampMs::new(10)),
            TimestampMs::new(10),
            &correlation,
            &mut events,
        )
        .unwrap();
    let mut orchestrator = MissionOrchestrator::new();
    orchestrator
        .submit(
            future,
            ExecutionGroupId::new("future-group").unwrap(),
            &mut control,
            TimestampMs::new(10),
            &correlation,
            &mut events,
        )
        .unwrap();
    orchestrator
        .prepare_task(
            &mission,
            &task,
            &state,
            &mut control,
            TimestampMs::new(20),
            &correlation,
            &mut events,
        )
        .unwrap();
    orchestrator
        .submit(
            plan("higher", 1),
            ExecutionGroupId::new("higher-group").unwrap(),
            &mut control,
            TimestampMs::new(20),
            &correlation,
            &mut events,
        )
        .unwrap();
    orchestrator
        .set_mission_priority(&mission, 0, TimestampMs::new(20), &correlation, &mut events)
        .unwrap();
    orchestrator
        .set_mission_priority(
            &MissionId::new("higher").unwrap(),
            255,
            TimestampMs::new(20),
            &correlation,
            &mut events,
        )
        .unwrap();
    let due = control
        .scheduled_task(&task)
        .unwrap()
        .decision()
        .starts_at();
    assert_eq!(
        orchestrator
            .next_dispatch_task(&control, due, &BTreeSet::new(), &BTreeSet::new())
            .unwrap(),
        Some(task)
    );
}
