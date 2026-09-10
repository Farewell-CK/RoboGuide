//! Mission contract and fixture acceptance tests.

use super::lifecycle::registration;
use super::*;

/// Legacy MissionPlan v0.3 decodes one Node-independent relation and normalizes to v0.5.
#[test]
fn execution_relation_fixture_decodes_logical_endpoints() {
    let source =
        include_str!("../../../../../scenarios/execution-relations-v0.1/mission-plan.json");
    let plan = decode_mission_plan(source).expect("relation fixture should validate");
    let relation = &plan.contexts()[0].relations()[0];
    assert_eq!(relation.relation_id().as_str(), "safety-guards-navigation");
    assert_eq!(relation.source().task_id().as_str(), "observe-safety");
    assert_eq!(relation.target().role_id().as_str(), "navigator");
    assert_eq!(
        relation.kind(),
        domain::ExecutionRelationKind::RequiresActive
    );
}

/// Relation endpoints must exist in one Context and remain concurrently runnable in the DAG.
#[test]
fn execution_relation_rejects_unknown_or_dag_ordered_endpoints() {
    let source =
        include_str!("../../../../../scenarios/execution-relations-v0.1/mission-plan.json");
    let mut unknown: serde_json::Value = serde_json::from_str(source).expect("fixture is JSON");
    unknown["contexts"][0]["relations"][0]["source"]["role_id"] = serde_json::json!("missing-role");
    assert!(
        decode_mission_plan(&unknown.to_string())
            .expect_err("unknown relation role must fail")
            .to_string()
            .contains("unknown Role")
    );

    let mut ordered: serde_json::Value = serde_json::from_str(source).expect("fixture is JSON");
    ordered["tasks"][1]["depends_on"] = serde_json::json!(["observe-safety"]);
    assert!(
        decode_mission_plan(&ordered.to_string())
            .expect_err("DAG-ordered relation must fail")
            .to_string()
            .contains("ordered by the DAG")
    );
}

/// v0.4 preserves coupling declarations and typed relation metadata at the JSON boundary.
#[test]
fn v0_4_decodes_coupling_and_typed_relation() {
    let source =
        include_str!("../../../../../scenarios/execution-relations-v0.1/mission-plan.json");
    let mut document: serde_json::Value = serde_json::from_str(source).expect("fixture is JSON");
    document["schema_version"] = serde_json::json!(domain::MISSION_PLAN_SCHEMA_V0_4);
    document["contexts"][0]["coupling_mode"] = serde_json::json!("concurrent-cooperation");
    document["contexts"][0]["shared_view"] = serde_json::json!({
        "bindings": [
            {
                "context_role_id": "safety",
                "field": "pose",
                "state_export_id": "safety-pose",
                "payload_schema": "roboguide.pose/v1"
            },
            {"context_role_id": "guide", "field": "execution"}
        ],
        "include_freshness": true,
        "spatial_reference": {"map_id": "campus", "revision_id": "r1", "frame_id": "map"}
    });
    document["contexts"][0]["relations"][0] = serde_json::json!({
        "id": "safety-guards-navigation",
        "kind": "state-requirement",
        "state_key": "hazard",
        "requirement": "available",
        "source": {"task_id": "observe-safety", "role_id": "safety-observer"},
        "target": {"task_id": "navigate", "role_id": "navigator"}
    });
    let plan = decode_mission_plan(&document.to_string()).expect("v0.4 plan should validate");
    assert_eq!(
        plan.contexts()[0].coupling_mode(),
        domain::ExecutionCouplingMode::ConcurrentCooperation
    );
    assert!(plan.contexts()[0].shared_view().is_some());
    assert!(matches!(
        plan.contexts()[0].relations()[0].relation_type(),
        domain::ExecutionRelationType::StateRequirement { .. }
    ));

    let encoded = mission_plan_json(&plan);
    let execution_binding = &encoded["contexts"][0]["shared_view"]["bindings"][1];
    assert!(execution_binding.get("state_export_id").is_none());
    assert!(execution_binding.get("payload_schema").is_none());
    let decoded = decode_mission_plan(&encoded.to_string())
        .expect("canonical v0.4 MissionPlan should round trip");
    assert_eq!(decoded, plan);
}

/// Implementation preflight rejects future typed syntax before Control creates a Group.
#[test]
fn unsupported_relation_never_reaches_control_authority() {
    let source =
        include_str!("../../../../../scenarios/execution-relations-v0.1/mission-plan.json");
    let mut document: serde_json::Value = serde_json::from_str(source).expect("fixture is JSON");
    document["schema_version"] = serde_json::json!(domain::MISSION_PLAN_SCHEMA_V0_4);
    document["contexts"][0]["coupling_mode"] = serde_json::json!("concurrent-cooperation");
    document["contexts"][0]["shared_view"] = serde_json::json!({
        "bindings": [{"context_role_id": "guide", "field": "execution"}],
        "include_freshness": false
    });
    document["contexts"][0]["relations"][0] = serde_json::json!({
        "id": "relative-guidance",
        "kind": "relative-pose",
        "frame_id": "map",
        "source": {"task_id": "observe-safety", "role_id": "safety-observer"},
        "target": {"task_id": "navigate", "role_id": "navigator"}
    });
    let plan = decode_mission_plan(&document.to_string()).expect("contract syntax is valid");
    let mut orchestrator = MissionOrchestrator::new();
    let mut control = ControlPlane::new();
    let mut events = InMemoryEventLog::new();
    let result = orchestrator.submit(
        plan,
        ExecutionGroupId::new("group-unsupported").expect("group id is valid"),
        &mut control,
        TimestampMs::new(1),
        &CorrelationId::new("unsupported-profile").expect("correlation is valid"),
        &mut events,
    );

    assert!(matches!(
        result,
        Err(OrchestrationError::Mission(reason))
            if reason.contains("valid contract syntax but is not executable")
    ));
    assert!(control.group_ids().is_empty());
    assert!(events.records().is_empty());
}

/// v0.4 rejects a Task mode override whose Context lacks its static mechanisms.
#[test]
fn v0_4_rejects_unbacked_task_coupling_mode() {
    let source =
        include_str!("../../../../../scenarios/execution-relations-v0.1/mission-plan.json");
    let mut document: serde_json::Value = serde_json::from_str(source).expect("fixture is JSON");
    document["schema_version"] = serde_json::json!(domain::MISSION_PLAN_SCHEMA_V0_4);
    document["tasks"][1]["coupling_mode"] = serde_json::json!("tightly-coupled-cooperation");

    assert!(
        decode_mission_plan(&document.to_string())
            .expect_err("unbacked Task mode must fail acceptance")
            .to_string()
            .contains("requires a Group shared view")
    );
}

/// The Phase 1 fixture decodes into a four-Task DAG and preserves Context continuity metadata.
#[test]
fn phase1_fixture_contains_complete_dag_and_context() {
    let source = include_str!("../../../../../scenarios/phase1-mission-v0.3/mission-plan.json");
    let plan = decode_mission_plan(source).expect("Phase 1 MissionPlan should validate");
    assert_eq!(plan.schema_version(), domain::MISSION_PLAN_SCHEMA_V0_5);
    assert_eq!(plan.contexts().len(), 1);
    assert_eq!(plan.task_graph().tasks().len(), 4);
    assert_eq!(
        plan.task_graph().tasks()[1]
            .continuity()
            .resource_scope(plan.task_graph().tasks()[1].requirement().roles()[0].role_id()),
        domain::ResourceBindingScope::Context
    );
}

/// An exact submission retry returns existing authority without creating a second Group.
#[test]
fn exact_mission_submission_retry_is_idempotent() {
    let source = include_str!("../../../../../scenarios/phase1-mission-v0.3/mission-plan.json");
    let plan = decode_mission_plan(source).expect("Phase 1 MissionPlan should validate");
    let group_id = ExecutionGroupId::new("group-idempotent").expect("group id valid");
    let correlation = CorrelationId::new("idempotent-submit").expect("trace valid");
    let mut control = ControlPlane::new();
    let mut orchestrator = MissionOrchestrator::new();
    let mut events = InMemoryEventLog::new();
    orchestrator
        .submit(
            plan.clone(),
            group_id.clone(),
            &mut control,
            TimestampMs::new(1),
            &correlation,
            &mut events,
        )
        .expect("first submission creates authority");
    let event_count = events.records().len();

    let repeated = orchestrator
        .submit(
            plan,
            group_id,
            &mut control,
            TimestampMs::new(2),
            &correlation,
            &mut events,
        )
        .expect("exact retry returns existing authority");

    assert_eq!(repeated.lifecycle(), MissionExecutionLifecycle::Running);
    assert_eq!(orchestrator.mission_ids().len(), 1);
    assert_eq!(events.records().len(), event_count);
}

/// Restored orchestration rejects missing Groups, truncated DAGs, and lifecycle disagreement.
#[test]
fn restored_orchestration_cross_checks_control_authority() {
    let source = include_str!("../../../../../scenarios/phase1-mission-v0.3/mission-plan.json");
    let plan = decode_mission_plan(source).expect("Phase 1 MissionPlan should validate");
    let mission_id = plan.goal().mission_id().clone();
    let group_id = ExecutionGroupId::new("group-restore-authority").expect("group id valid");
    let correlation = CorrelationId::new("restore-authority-test").expect("trace valid");
    let mut control = ControlPlane::new();
    let mut events = InMemoryEventLog::new();
    control
        .create_mission_group(
            group_id.clone(),
            &plan,
            TimestampMs::new(1),
            &correlation,
            &mut events,
        )
        .expect("orphan fixture Group is created");
    assert!(
        MissionOrchestrator::new()
            .validate_control_authority(&control)
            .expect_err("orphan Mission Group must fail closed")
            .to_string()
            .contains("no orchestration authority")
    );
    let mut control = ControlPlane::new();
    let mut orchestrator = MissionOrchestrator::new();
    orchestrator
        .submit(
            plan,
            group_id,
            &mut control,
            TimestampMs::new(1),
            &correlation,
            &mut events,
        )
        .expect("Mission authority is created");
    orchestrator
        .validate_control_authority(&control)
        .expect("matching projections validate");

    let checkpoint = orchestrator
        .checkpoint_json()
        .expect("orchestration checkpoint serializes");
    let mut missing_group: serde_json::Value =
        serde_json::from_str(&checkpoint).expect("checkpoint is JSON");
    missing_group[0]["group_id"] = serde_json::json!("group-other");
    let restored = MissionOrchestrator::restore_json(&missing_group.to_string())
        .expect("syntactically valid checkpoint restores before authority validation");
    assert!(matches!(
        restored.validate_control_authority(&control),
        Err(OrchestrationError::Control(ControlError::UnknownGroup(_)))
    ));

    let mut truncated_dag: serde_json::Value =
        serde_json::from_str(&checkpoint).expect("checkpoint is JSON");
    truncated_dag[0]["plan"]["tasks"]
        .as_array_mut()
        .expect("tasks remain an array")
        .pop();
    let restored = MissionOrchestrator::restore_json(&truncated_dag.to_string())
        .expect("shorter valid DAG restores before authority validation");
    assert!(
        restored
            .validate_control_authority(&control)
            .expect_err("Control must reject a truncated restored DAG")
            .to_string()
            .contains("Task DAG differs")
    );

    let mut false_terminal: serde_json::Value =
        serde_json::from_str(&checkpoint).expect("checkpoint is JSON");
    false_terminal[0]["lifecycle"] = serde_json::json!("Completed");
    let restored = MissionOrchestrator::restore_json(&false_terminal.to_string())
        .expect("known lifecycle restores before authority validation");
    assert!(
        restored
            .validate_control_authority(&control)
            .expect_err("unreleased Group must reject a completed Mission projection")
            .to_string()
            .contains(&mission_id.to_string())
    );
}

/// Both directions of the Spatial Memory experiment remain valid legacy plans.
#[test]
fn distributed_spatial_memory_fixtures_decode_in_both_directions() {
    let fixtures = [
        include_str!(
            "../../../../../scenarios/distributed-spatial-memory-v0.1/mission-a-build-publish.json"
        ),
        include_str!(
            "../../../../../scenarios/distributed-spatial-memory-v0.1/mission-b-import-verify.json"
        ),
        include_str!(
            "../../../../../scenarios/distributed-spatial-memory-v0.1/mission-b-build-publish.json"
        ),
        include_str!(
            "../../../../../scenarios/distributed-spatial-memory-v0.1/mission-a-import-verify.json"
        ),
    ];
    for fixture in fixtures {
        let plan = decode_mission_plan(fixture).expect("Spatial Memory fixture should validate");
        assert_eq!(plan.schema_version(), domain::MISSION_PLAN_SCHEMA_V0_5);
        assert_eq!(plan.contexts().len(), 1);
        assert_eq!(plan.task_graph().tasks().len(), 2);
    }
}

/// The four Spatial Memory fixtures bind to two distinct physical nodes under Control policy.
#[test]
fn distributed_spatial_memory_actor_placement_drives_two_node_assignments() {
    let fixtures = [
        (
            include_str!(
                "../../../../../scenarios/distributed-spatial-memory-v0.1/mission-a-build-publish.json"
            ),
            "robot-dog-a",
            "dog-a",
        ),
        (
            include_str!(
                "../../../../../scenarios/distributed-spatial-memory-v0.1/mission-b-import-verify.json"
            ),
            "robot-dog-b",
            "dog-b",
        ),
        (
            include_str!(
                "../../../../../scenarios/distributed-spatial-memory-v0.1/mission-b-build-publish.json"
            ),
            "robot-dog-b",
            "dog-b",
        ),
        (
            include_str!(
                "../../../../../scenarios/distributed-spatial-memory-v0.1/mission-a-import-verify.json"
            ),
            "robot-dog-a",
            "dog-a",
        ),
    ];
    let contracts = [
        (
            CapabilityContractRef::new("spatial.map", "build", "v0").expect("contract valid"),
            CapabilityKind::Compute,
        ),
        (
            CapabilityContractRef::new("spatial.map", "publish", "v0").expect("contract valid"),
            CapabilityKind::Compute,
        ),
        (
            CapabilityContractRef::new("spatial.map", "import", "v0").expect("contract valid"),
            CapabilityKind::Compute,
        ),
        (
            CapabilityContractRef::new("spatial.localization", "verify", "v0")
                .expect("contract valid"),
            CapabilityKind::Observation,
        ),
    ];
    for (fixture, actor, expected_node) in fixtures {
        let plan = decode_mission_plan(fixture).expect("Spatial Memory fixture validates");
        let mission_id = plan.goal().mission_id().clone();
        let expected_node = NodeId::new(expected_node).expect("node id valid");
        let timestamp = TimestampMs::new(1);
        let correlation =
            CorrelationId::new(format!("placement-{mission_id}")).expect("correlation valid");
        let mut control = ControlPlane::new();
        let mut state = InMemorySharedNodeState::new();
        let mut events = InMemoryEventLog::new();
        for (node_id, resource_id) in [("dog-a", "compute-a"), ("dog-b", "compute-b")] {
            control
                .register_node(
                    &mut state,
                    registration(
                        node_id,
                        vec![
                            Capability::new(CapabilityKind::Compute, true),
                            Capability::new(CapabilityKind::Observation, true),
                        ],
                        contracts.to_vec(),
                        vec![(
                            ResourceId::new(resource_id).expect("resource id valid"),
                            ResourceKind::Compute,
                        )],
                    ),
                    NodeStatus::new(NodeHealth::Online, timestamp),
                    timestamp,
                    &correlation,
                    &mut events,
                )
                .expect("symmetric Spatial node registers");
        }
        control
            .set_actor_node_constraint(
                mission_id.clone(),
                domain::ActorId::new(actor).expect("actor id valid"),
                expected_node.clone(),
            )
            .expect("fixture placement constraint accepted");
        let group_id =
            ExecutionGroupId::new(format!("group-{mission_id}")).expect("group id valid");
        let mut orchestrator = MissionOrchestrator::new();
        orchestrator
            .submit(
                plan,
                group_id,
                &mut control,
                timestamp,
                &correlation,
                &mut events,
            )
            .expect("fixture Mission accepted");
        let ready = orchestrator.ready_tasks(&mission_id, &control);
        assert_eq!(ready.len(), 1);
        let task = orchestrator
            .prepare_task(
                &mission_id,
                &ready[0],
                &state,
                &mut control,
                TimestampMs::new(2),
                &correlation,
                &mut events,
            )
            .expect("first Spatial Task binds");
        assert_eq!(task.assignments().len(), 1);
        assert_eq!(task.assignments()[0].node_id(), &expected_node);
    }
}

/// Malformed or legacy MissionPlan documents are rejected before Control receives them.
#[test]
fn legacy_plan_schema_is_rejected() {
    let error = decode_mission_plan(
            r#"{"schema_version":"roboguide.mission-plan/v0.1","mission":{"id":"m","objective":"x"},"contexts":[],"tasks":[]}"#,
        )
        .expect_err("legacy plan must be rejected");
    assert!(error.to_string().contains("unsupported MissionPlan schema"));
}

/// MissionPlan v0.2 remains a relation-free compatibility input during v0.5 migration.
#[test]
fn v0_2_plan_decodes_without_execution_relations() {
    let source = include_str!("../../../../../scenarios/phase1-mission-v0.2/mission-plan.json");
    let plan = decode_mission_plan(source).expect("v0.2 compatibility input should decode");
    assert_eq!(plan.schema_version(), domain::MISSION_PLAN_SCHEMA_V0_5);
    assert!(
        plan.contexts()
            .iter()
            .all(|context| context.relations().is_empty())
    );
}
