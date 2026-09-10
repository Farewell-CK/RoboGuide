//! Mission scheduling and future reservation tests.

use super::lifecycle::registration;
use super::*;

/// Current scheduling fixtures add bounded timing and replace legacy resource_kind fields.
fn scheduled_phase1_plan() -> MissionPlan {
    let source = include_str!("../../../../../scenarios/phase1-mission-v0.3/mission-plan.json");
    let mut document: serde_json::Value = serde_json::from_str(source).expect("fixture is JSON");
    document["schema_version"] = serde_json::json!(domain::MISSION_PLAN_SCHEMA_V0_5);
    for task in document["tasks"]
        .as_array_mut()
        .expect("fixture tasks are an array")
    {
        task["timing"] = serde_json::json!({
            "earliest_start_offset_ms": 100,
            "latest_start_offset_ms": 200,
            "completion_deadline_offset_ms": 300,
            "estimated_duration_ms": 50,
        });
        for role in task["roles"]
            .as_array_mut()
            .expect("fixture roles are an array")
        {
            let resource_kind = role
                .as_object_mut()
                .expect("fixture role is an object")
                .remove("resource_kind")
                .expect("fixture role declares resource_kind");
            role["resources"] = if resource_kind.is_null() {
                serde_json::json!([])
            } else {
                serde_json::json!([{"kind": resource_kind, "units": 1}])
            };
        }
    }
    decode_mission_plan(&document.to_string()).expect("v0.5 scheduling plan should decode")
}

/// MissionPlan v0.5 rejects the removed resource_kind field even when its value is null.
#[test]
fn v0_5_rejects_explicit_legacy_resource_field() {
    let plan = scheduled_phase1_plan();
    let mut document = mission_plan_json(&plan);
    document["tasks"][0]["roles"][0]["resource_kind"] = serde_json::Value::Null;
    assert!(
        decode_mission_plan(&document.to_string())
            .expect_err("v0.5 must not mix resource contracts")
            .to_string()
            .contains("must use resources instead of resource_kind")
    );
}

/// MissionPlan v0.5 requires every nullable timing key to be present explicitly.
#[test]
fn v0_5_rejects_missing_nullable_timing_key() {
    let plan = scheduled_phase1_plan();
    let mut document = mission_plan_json(&plan);
    document["tasks"][0]["timing"]
        .as_object_mut()
        .expect("timing is an object")
        .remove("estimated_duration_ms");
    let error = decode_mission_plan(&document.to_string())
        .expect_err("v0.5 nullable timing keys remain required");
    assert!(
        error.to_string().contains("estimated_duration_ms"),
        "unexpected timing diagnostic: {error}"
    );
}

/// Mission acceptance rejects relative timing that overflows its Controller-time anchor.
#[test]
fn mission_acceptance_rejects_unrepresentable_absolute_timing() {
    let mut document = mission_plan_json(&scheduled_phase1_plan());
    document["tasks"][0]["timing"] = serde_json::json!({
        "earliest_start_offset_ms": u64::MAX - 100,
        "latest_start_offset_ms": u64::MAX - 100,
        "completion_deadline_offset_ms": u64::MAX - 50,
        "estimated_duration_ms": 50,
    });
    let plan = decode_mission_plan(&document.to_string()).expect("relative timing is valid");
    let group_id = ExecutionGroupId::new("group-overflow").expect("group id valid");
    let mut existing_orchestrator = MissionOrchestrator::new();
    let mut existing_control = ControlPlane::new();
    let mut existing_events = InMemoryEventLog::new();
    existing_orchestrator
        .submit(
            plan.clone(),
            group_id.clone(),
            &mut existing_control,
            TimestampMs::new(0),
            &CorrelationId::new("timing-first-submit").expect("correlation id valid"),
            &mut existing_events,
        )
        .expect("initially representable timing is accepted");
    existing_orchestrator
        .submit(
            plan.clone(),
            group_id.clone(),
            &mut existing_control,
            TimestampMs::new(101),
            &CorrelationId::new("timing-idempotent-retry").expect("correlation id valid"),
            &mut existing_events,
        )
        .expect("idempotent retry retains the original acceptance anchor");

    let mut orchestrator = MissionOrchestrator::new();
    let mut control = ControlPlane::new();
    let mut events = InMemoryEventLog::new();

    let error = orchestrator
        .submit(
            plan,
            group_id.clone(),
            &mut control,
            TimestampMs::new(101),
            &CorrelationId::new("timing-overflow").expect("correlation id valid"),
            &mut events,
        )
        .expect_err("absolute timing overflow must fail before Group creation");

    assert!(error.to_string().contains("timestamp range"));
    assert!(control.group(&group_id).is_none());
    assert!(events.records().is_empty());
}

/// A future Task remains Ready, survives checkpoints, activates only when due, and releases.
#[test]
fn future_scheduling_reservation_runs_through_control_lifecycle() {
    let plan = scheduled_phase1_plan();
    let mission_id = plan.goal().mission_id().clone();
    let first = plan.task_graph().tasks()[0]
        .requirement()
        .task_ref()
        .clone();
    let first_role = &plan.task_graph().tasks()[0].requirement().roles()[0];
    let first_capability = first_role.capability();
    let actor_contracts = plan
        .actor_requirements()
        .get(first_role.actor_id().expect("fixture role has an actor"))
        .expect("fixture actor has requirements")
        .iter()
        .map(|(kind, contract)| (contract.clone(), *kind))
        .collect::<Vec<_>>();
    let actor_capabilities = actor_contracts
        .iter()
        .map(|(_, kind)| Capability::new(*kind, true))
        .collect::<Vec<_>>();
    let group_id = ExecutionGroupId::new("group-scheduled").expect("group id valid");
    let correlation = CorrelationId::new("scheduled-flow").expect("correlation valid");
    let mut control = ControlPlane::new();
    let mut state = InMemorySharedNodeState::new();
    let mut events = InMemoryEventLog::new();
    control
        .register_node(
            &mut state,
            registration(
                "node-compute",
                actor_capabilities,
                actor_contracts,
                vec![(
                    ResourceId::new("compute-a").expect("resource id valid"),
                    ResourceKind::Compute,
                )],
            ),
            NodeStatus::new(NodeHealth::Online, TimestampMs::new(10)),
            TimestampMs::new(10),
            &correlation,
            &mut events,
        )
        .expect("node registers");
    let mut orchestrator = MissionOrchestrator::new();
    orchestrator
        .submit(
            plan,
            group_id.clone(),
            &mut control,
            TimestampMs::new(10),
            &correlation,
            &mut events,
        )
        .expect("Mission is accepted");

    let waiting = orchestrator
        .prepare_task(
            &mission_id,
            &first,
            &state,
            &mut control,
            TimestampMs::new(20),
            &correlation,
            &mut events,
        )
        .expect("future interval is admitted");
    assert!(waiting.assignments().is_empty());
    let scheduled = control.scheduled_task(&first).expect("reservation exists");
    assert_eq!(scheduled.decision().starts_at(), TimestampMs::new(110));
    assert_eq!(scheduled.decision().ends_at(), Some(TimestampMs::new(160)));
    assert_eq!(
        scheduled.decision().latest_activation_at(),
        Some(TimestampMs::new(210))
    );
    assert_eq!(scheduled.phase(), SchedulingReservationPhase::Scheduled);
    assert!(
        control
            .activate_task_execution(
                &group_id,
                &first,
                TimestampMs::new(100),
                &correlation,
                &mut events,
            )
            .expect_err("future Task cannot activate before its reserved start")
            .to_string()
            .contains("before its reserved start")
    );
    assert!(
        control
            .activate_task_execution(
                &group_id,
                &first,
                TimestampMs::new(110),
                &correlation,
                &mut events,
            )
            .expect_err("unbound Task cannot activate when its interval becomes due")
            .to_string()
            .contains("committed assignments")
    );
    assert_eq!(
        control
            .group(&group_id)
            .and_then(|group| group.task_execution(&first))
            .expect("scheduled Task remains registered")
            .lifecycle(),
        TaskExecutionLifecycle::Ready
    );

    let restored = ControlPlane::restore(control.checkpoint()).expect("calendar restores");
    assert_eq!(
        restored
            .scheduled_task(&first)
            .expect("restored reservation exists")
            .decision()
            .starts_at(),
        TimestampMs::new(110)
    );
    let restored_orchestrator = MissionOrchestrator::restore_json(
        &orchestrator
            .checkpoint_json()
            .expect("orchestration checkpoint serializes"),
    )
    .expect("orchestration checkpoint restores");
    assert_eq!(
        restored_orchestrator
            .execution(&mission_id)
            .expect("Mission restores")
            .accepted_at(),
        TimestampMs::new(10)
    );
    let mut forged_timing_checkpoint =
        serde_json::to_value(control.checkpoint()).expect("Control checkpoint serializes");
    forged_timing_checkpoint["scheduled_tasks"][0]["decision"]["latest_activation_at"] =
        serde_json::json!(999);
    let forged_timing_control = ControlPlane::restore(
        serde_json::from_value(forged_timing_checkpoint)
            .expect("forged timing checkpoint remains structurally decodable"),
    )
    .expect("Control-only structure cannot reconstruct Mission-relative timing");
    assert!(
        restored_orchestrator
            .validate_control_authority(&forged_timing_control)
            .expect_err("joint restore must reject widened scheduling timing")
            .to_string()
            .contains("accepted Task timing")
    );
    let competing_requirement = domain::TaskRequirement::new(
        MissionId::new("mission-competing").expect("mission id valid"),
        TaskId::new("task-competing").expect("task id valid"),
        vec![domain::RoleRequirement::new(
            domain::RoleId::new("worker").expect("role id valid"),
            first_capability,
            Some(ResourceKind::Compute),
        )],
    )
    .expect("competing requirement valid");
    let competing_candidates = control
        .match_capabilities(
            &state,
            &competing_requirement,
            TimestampMs::new(30),
            &correlation,
            &mut events,
        )
        .expect("competing task matches the same resource");
    let competing_decision = BoundedJointScheduler::new()
        .schedule_task(
            &state,
            &competing_requirement,
            &competing_candidates,
            TimestampMs::new(30),
            &correlation,
            &mut events,
        )
        .expect("non-authoritative competing selection succeeds");
    let competing_proposal = control
        .propose(
            &state,
            &competing_requirement,
            &competing_candidates,
            competing_decision.proposed_assignments(),
            TimestampMs::new(30),
            &correlation,
            &mut events,
        )
        .expect("competing proposal remains non-authoritative");
    assert!(matches!(
        control.commit(
            &competing_proposal,
            TimestampMs::new(30),
            &correlation,
            &mut events,
        ),
        Err(ControlError::ResourceConflict { owner_task_ref, .. }) if owner_task_ref == first
    ));

    let mut missed_control = control.clone();
    let mut missed_orchestrator = orchestrator.clone();
    let mut missed_events = events.clone();
    for timestamp in [211, 212] {
        let error = missed_orchestrator
            .prepare_task(
                &mission_id,
                &first,
                &state,
                &mut missed_control,
                TimestampMs::new(timestamp),
                &correlation,
                &mut missed_events,
            )
            .expect_err("missed activation window remains nonterminal and unbound");
        assert!(error.to_string().contains("window missed"));
    }
    assert!(
        missed_orchestrator
            .dispatchable_tasks(&mission_id, &missed_control)
            .is_empty(),
        "window-missed Task remains Ready but leaves the automatic timer queue"
    );
    let deferred_count = missed_events
        .records()
        .iter()
        .filter(|record| {
            matches!(
                record.payload(),
                domain::EventPayload::TaskSchedulingDeferred { task_ref, reason }
                    if task_ref == &first && reason == "window-missed"
            )
        })
        .count();
    assert_eq!(deferred_count, 1);
    let mut missed_restored = MissionOrchestrator::restore_json(
        &missed_orchestrator
            .checkpoint_json()
            .expect("missed scheduling state serializes"),
    )
    .expect("missed scheduling state restores");
    missed_restored
        .prepare_task(
            &mission_id,
            &first,
            &state,
            &mut missed_control,
            TimestampMs::new(213),
            &correlation,
            &mut missed_events,
        )
        .expect_err("restored missed window remains deferred");
    assert_eq!(
        missed_events
            .records()
            .iter()
            .filter(|record| matches!(
                record.payload(),
                domain::EventPayload::TaskSchedulingDeferred { task_ref, reason }
                    if task_ref == &first && reason == "window-missed"
            ))
            .count(),
        1
    );
    let mut cancelled_control = control.clone();
    let mut cancelled_orchestrator = orchestrator.clone();
    let mut cancellation_events = events.clone();
    cancelled_orchestrator
        .request_cancel(
            &mission_id,
            &mut cancelled_control,
            TimestampMs::new(30),
            &correlation,
            &mut cancellation_events,
        )
        .expect("cancellation releases future intervals");
    assert!(cancelled_control.scheduled_task(&first).is_none());
    assert!(cancellation_events.contains_payload(|payload| matches!(
        payload,
        domain::EventPayload::SchedulingReservationReleased { task_ref, reason, .. }
            if task_ref == &first && reason == "Mission cancellation requested"
    )));

    let bound = orchestrator
        .prepare_task(
            &mission_id,
            &first,
            &state,
            &mut control,
            TimestampMs::new(110),
            &correlation,
            &mut events,
        )
        .expect("due interval commits and binds");
    assert_eq!(bound.assignments().len(), 1);
    let mut forged_checkpoint =
        serde_json::to_value(control.checkpoint()).expect("Control checkpoint serializes");
    forged_checkpoint["scheduled_tasks"][0]["decision"]["selections"][0]["resource_ids"][0] =
        serde_json::json!("forged-resource");
    let forged_checkpoint = serde_json::from_value(forged_checkpoint)
        .expect("forged checkpoint remains structurally decodable");
    assert!(
        ControlPlane::restore(forged_checkpoint)
            .expect_err("scheduled decision must match committed binding")
            .to_string()
            .contains("committed Task bindings")
    );
    assert_eq!(
        control
            .scheduled_task(&first)
            .expect("interval retained")
            .phase(),
        SchedulingReservationPhase::Scheduled
    );
    assert!(
        control
            .activate_task_execution(
                &group_id,
                &first,
                TimestampMs::new(160),
                &correlation,
                &mut events,
            )
            .expect_err("bound Task cannot activate after the reserved interval")
            .to_string()
            .contains("after its reserved interval")
    );
    control
        .activate_task_execution(
            &group_id,
            &first,
            TimestampMs::new(111),
            &correlation,
            &mut events,
        )
        .expect("bound Task activates");
    assert_eq!(
        control
            .scheduled_task(&first)
            .expect("active interval retained")
            .phase(),
        SchedulingReservationPhase::Activated
    );
    assert_eq!(
        control
            .scheduling_snapshot(TimestampMs::new(120))
            .occupancies()[0]
            .ends_at(),
        Some(TimestampMs::new(160))
    );
    assert_eq!(
        control
            .scheduling_snapshot(TimestampMs::new(161))
            .occupancies()[0]
            .ends_at(),
        None
    );
    orchestrator
        .task_succeeded(
            &mission_id,
            &first,
            &mut control,
            TimestampMs::new(150),
            &correlation,
            &mut events,
        )
        .expect("terminal Task releases its calendar interval");
    assert!(control.scheduled_task(&first).is_none());
    assert!(events.contains_payload(|payload| matches!(
        payload,
        domain::EventPayload::SchedulingReservationReleased { task_ref, .. }
            if task_ref == &first
    )));
}
