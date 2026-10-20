//! Recorded-plan revision invariants; these unit checks create no transport or device evidence.

use super::*;

/// Renames only future logical Task identities and their DAG edges, keeping original semantic contracts.
fn replacement(plan: &MissionPlan, suffix: &str) -> MissionPlan {
    let mut document = mission_plan_json(plan);
    for task in document["tasks"].as_array_mut().unwrap() {
        let id = task["id"].as_str().unwrap().to_string();
        task["id"] = format!("{id}-{suffix}").into();
        for dependency in task["depends_on"].as_array_mut().unwrap() {
            *dependency = format!("{}-{suffix}", dependency.as_str().unwrap()).into();
        }
    }
    decode_mission_plan(&document.to_string()).unwrap()
}

/// A stopped zero-attempt configuration is replaced atomically, preserving the original timing/receipt.
#[test]
fn runtime_revision_keeps_original_group_plan_and_timing_through_restore() {
    let plan = decode_mission_plan(include_str!(
        "../../../../../scenarios/phase1-mission-v0.2/mission-plan.json"
    ))
    .unwrap();
    let mission = plan.goal().mission_id().clone();
    let mut orchestrator = MissionOrchestrator::new();
    let mut control = ControlPlane::new();
    let mut events = InMemoryEventLog::new();
    let trace = CorrelationId::new("revision-unit").unwrap();
    let first = ExecutionGroupId::new("revision-original").unwrap();
    orchestrator
        .submit(
            plan.clone(),
            first.clone(),
            &mut control,
            TimestampMs::new(100),
            &trace,
            &mut events,
        )
        .unwrap();
    goal::install_gate(&mut control, &mut orchestrator, &plan);
    let target = replacement(&plan, "replacement");
    let second = ExecutionGroupId::new("revision-next").unwrap();
    let before = serde_json::to_value(control.checkpoint()).unwrap();
    assert!(
        orchestrator
            .commit_plan_revision(
                &mission,
                target.clone(),
                second.clone(),
                "switch-original",
                &format!("sha256:{}", "a".repeat(64)),
                &mut control,
                TimestampMs::new(200),
                &trace,
                &mut events
            )
            .is_err()
    );
    assert_eq!(serde_json::to_value(control.checkpoint()).unwrap(), before);
    orchestrator
        .hold_for_safety(&mission, TimestampMs::new(180), &trace, &mut events)
        .unwrap();
    control
        .block_group(
            &first,
            "revision original hold",
            TimestampMs::new(180),
            &trace,
            &mut events,
        )
        .unwrap();
    let version = orchestrator
        .commit_plan_revision(
            &mission,
            target.clone(),
            second.clone(),
            "switch-original",
            &format!("sha256:{}", "a".repeat(64)),
            &mut control,
            TimestampMs::new(200),
            &trace,
            &mut events,
        )
        .unwrap();
    assert!(version.completed_tasks.is_empty());
    assert_eq!(
        control.group(&first).unwrap().lifecycle(),
        GroupLifecycle::Released
    );
    assert_eq!(
        control.group(&first).unwrap().superseded_by(),
        Some(&second)
    );
    assert_eq!(orchestrator.execution(&mission).unwrap().plan(), &target);
    assert_eq!(
        orchestrator.execution(&mission).unwrap().accepted_at(),
        TimestampMs::new(100)
    );
    assert_eq!(
        orchestrator
            .execution(&mission)
            .unwrap()
            .original_group_id(),
        &first
    );
    let restored_control = ControlPlane::restore(control.checkpoint()).unwrap();
    let restored =
        MissionOrchestrator::restore_json(&orchestrator.checkpoint_json().unwrap()).unwrap();
    restored
        .validate_control_authority(&restored_control)
        .unwrap();
    assert_eq!(restored.execution(&mission).unwrap().plan_revision(), 1);
    let mut forged: serde_json::Value =
        serde_json::from_str(&orchestrator.checkpoint_json().unwrap()).unwrap();
    forged[0]["plan_revisions"][0]["completed_tasks"] =
        serde_json::json!([plan.task_graph().tasks()[0].task_id().as_str()]);
    assert!(MissionOrchestrator::restore_json(&forged.to_string()).is_err());
}

/// Actual Control satisfaction is inherited exactly and never returned to readiness under another version.
#[test]
fn revision_inherits_completed_task_and_rejects_renamed_repetition() {
    let plan = decode_mission_plan(include_str!(
        "../../../../../scenarios/phase1-mission-v0.2/mission-plan.json"
    ))
    .unwrap();
    let mission = plan.goal().mission_id().clone();
    let first = plan.task_graph().tasks()[0]
        .requirement()
        .task_ref()
        .clone();
    let source = ExecutionGroupId::new("completed-source-unit").unwrap();
    let target_group = ExecutionGroupId::new("completed-target-unit").unwrap();
    let mut control = ControlPlane::new();
    let mut orchestrator = MissionOrchestrator::new();
    let mut nodes = InMemorySharedNodeState::new();
    let trace = CorrelationId::new("completed-revision-unit").unwrap();
    let mut events = InMemoryEventLog::new();
    control
        .register_node(
            &mut nodes,
            lifecycle::registration(
                "unit-compute",
                vec![
                    Capability::new(CapabilityKind::Compute, true),
                    Capability::new(CapabilityKind::Observation, true),
                ],
                vec![
                    (
                        CapabilityContractRef::new("compute", "prepare", "v1").unwrap(),
                        CapabilityKind::Compute,
                    ),
                    (
                        CapabilityContractRef::new("observation", "verify", "v1").unwrap(),
                        CapabilityKind::Observation,
                    ),
                ],
                vec![(
                    ResourceId::new("unit-resource").unwrap(),
                    ResourceKind::Compute,
                )],
            ),
            NodeStatus::new(NodeHealth::Online, TimestampMs::new(1)),
            TimestampMs::new(1),
            &trace,
            &mut events,
        )
        .unwrap();
    orchestrator
        .submit(
            plan.clone(),
            source.clone(),
            &mut control,
            TimestampMs::new(1),
            &trace,
            &mut events,
        )
        .unwrap();
    goal::install_gate(&mut control, &mut orchestrator, &plan);
    orchestrator
        .prepare_task(
            &mission,
            &first,
            &nodes,
            &mut control,
            TimestampMs::new(2),
            &trace,
            &mut events,
        )
        .unwrap();
    control
        .activate_task_execution(&source, &first, TimestampMs::new(3), &trace, &mut events)
        .unwrap();
    orchestrator
        .record_task_execution_completed(
            &mission,
            &first,
            &mut control,
            TimestampMs::new(4),
            &trace,
            &mut events,
        )
        .unwrap();
    orchestrator
        .satisfy_task_from_execution_report(
            &mission,
            &first,
            &mut control,
            TimestampMs::new(5),
            &trace,
            &mut events,
        )
        .unwrap();
    let mut document = mission_plan_json(&plan);
    let first_id = first.task_id().as_str();
    for task in document["tasks"].as_array_mut().unwrap() {
        let id = task["id"].as_str().unwrap();
        if id == first_id {
            continue;
        }
        task["id"] = format!("{id}-replacement").into();
        for dependency in task["depends_on"].as_array_mut().unwrap() {
            if dependency.as_str() != Some(first_id) {
                *dependency = format!("{}-replacement", dependency.as_str().unwrap()).into();
            }
        }
    }
    let target = decode_mission_plan(&document.to_string()).unwrap();
    let completed = orchestrator
        .validate_plan_revision(&mission, &target, &control)
        .unwrap();
    assert_eq!(completed, BTreeSet::from([first.task_id().clone()]));
    let mut repeat = document.clone();
    let mut renamed = repeat["tasks"][0].clone();
    renamed["id"] = "renamed-side-effect".into();
    repeat["tasks"].as_array_mut().unwrap().push(renamed);
    let repeat = decode_mission_plan(&repeat.to_string()).unwrap();
    assert!(
        orchestrator
            .validate_plan_revision(&mission, &repeat, &control)
            .is_err()
    );
    orchestrator
        .hold_for_safety(&mission, TimestampMs::new(6), &trace, &mut events)
        .unwrap();
    control
        .block_group(
            &source,
            "unit original hold",
            TimestampMs::new(6),
            &trace,
            &mut events,
        )
        .unwrap();
    let version = orchestrator
        .commit_plan_revision(
            &mission,
            target,
            target_group.clone(),
            "completed-switch-unit",
            &format!("sha256:{}", "b".repeat(64)),
            &mut control,
            TimestampMs::new(7),
            &trace,
            &mut events,
        )
        .unwrap();
    assert_eq!(version.completed_tasks, completed);
    assert_eq!(
        control
            .group(&source)
            .unwrap()
            .task_execution(&first)
            .unwrap()
            .lifecycle(),
        TaskExecutionLifecycle::Completed
    );
    assert_eq!(
        control
            .group(&target_group)
            .unwrap()
            .task_execution(&first)
            .unwrap()
            .lifecycle(),
        TaskExecutionLifecycle::Completed
    );
    assert!(
        orchestrator
            .ready_tasks(&mission, &control)
            .iter()
            .all(|task| task != &first)
    );
    let restored =
        MissionOrchestrator::restore_json(&orchestrator.checkpoint_json().unwrap()).unwrap();
    let restored_control = ControlPlane::restore(control.checkpoint()).unwrap();
    restored
        .validate_control_authority(&restored_control)
        .unwrap();
}
