//! Original-goal gate unit fixtures; these establish no process, hardware or physical safety evidence.

use super::*;

/// Builds one logical unit Task by retaining the maintained published fixture's original first operation.
fn plan() -> MissionPlan {
    let mut document: serde_json::Value = serde_json::from_str(include_str!(
        "../../../../../scenarios/phase1-mission-v0.2/mission-plan.json"
    ))
    .unwrap();
    document["tasks"].as_array_mut().unwrap().truncate(1);
    decode_mission_plan(&document.to_string()).unwrap()
}

/// Installs an independently enrolled logical unit source; no transport or device facts are created.
pub(super) fn install_gate(
    control: &mut ControlPlane,
    orchestrator: &mut MissionOrchestrator,
    plan: &MissionPlan,
) -> (
    domain::SemanticVerifierRegistry,
    domain::MissionGoalContract,
) {
    control
        .register_mission_tenant(plan.goal().mission_id().clone(), "unit-tenant")
        .unwrap();
    let registry:domain::SemanticVerifierRegistry=serde_json::from_value(serde_json::json!({
        "schema_version":"roboguide.semantic-verifier-registry/v0.1","registry_id":"unit-goals","revision":1,
        "sources":{"goal":{"source_id":"goal","revision":1,"tenant_id":"unit-tenant","producer_principals":["independent-producer"],
            "verifier":{"namespace":"observation","name":"original-goal","version":"v1"},"predicates":["original objective measured"],
            "max_age_ms":1000,"enabled":true,"continuous":false,"stop_timeout_ms":1000,"require_artifact":false}}
    })).unwrap();
    control
        .accept_semantic_verifier_registry(registry.clone(), 0)
        .unwrap();
    let contract = control
        .freeze_mission_goal(
            plan.goal(),
            domain::VerifierSatisfactionSpec::new(
                CapabilityContractRef::new("observation", "original-goal", "v1").unwrap(),
                "original objective measured",
                1000,
            )
            .unwrap(),
            format!("sha256:{}", "a".repeat(64)),
            "unit-tenant",
        )
        .unwrap();
    orchestrator
        .install_mission_goal_gate(plan.goal().mission_id(), control)
        .unwrap();
    (registry, contract)
}

/// All Task facts remain insufficient; stale, superseded and revoked goal measurements cannot complete.
#[test]
fn independent_goal_gate_requires_latest_fresh_original_source_and_retains_completion_history() {
    let plan = plan();
    let mission = plan.goal().mission_id().clone();
    let group = ExecutionGroupId::new("goal-unit-group").unwrap();
    let task = plan.task_graph().tasks()[0]
        .requirement()
        .task_ref()
        .clone();
    let mut control = ControlPlane::new();
    let mut orchestrator = MissionOrchestrator::new();
    let mut events = InMemoryEventLog::new();
    let trace = CorrelationId::new("original-goal-unit").unwrap();
    let mut nodes = InMemorySharedNodeState::new();
    control
        .register_node(
            &mut nodes,
            lifecycle::registration(
                "unit-edge",
                vec![Capability::new(CapabilityKind::Compute, true)],
                vec![(
                    CapabilityContractRef::new("compute", "prepare", "v1").unwrap(),
                    CapabilityKind::Compute,
                )],
                vec![(
                    ResourceId::new("unit-compute").unwrap(),
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
            group.clone(),
            &mut control,
            TimestampMs::new(1),
            &trace,
            &mut events,
        )
        .unwrap();
    let (registry, contract) = install_gate(&mut control, &mut orchestrator, &plan);
    orchestrator
        .prepare_task(
            &mission,
            &task,
            &nodes,
            &mut control,
            TimestampMs::new(2),
            &trace,
            &mut events,
        )
        .unwrap();
    control
        .activate_task_execution(&group, &task, TimestampMs::new(3), &trace, &mut events)
        .unwrap();
    orchestrator
        .record_task_execution_completed(
            &mission,
            &task,
            &mut control,
            TimestampMs::new(4),
            &trace,
            &mut events,
        )
        .unwrap();
    orchestrator
        .satisfy_task_from_execution_report(
            &mission,
            &task,
            &mut control,
            TimestampMs::new(5),
            &trace,
            &mut events,
        )
        .unwrap();
    assert_eq!(
        orchestrator.execution(&mission).unwrap().lifecycle(),
        MissionExecutionLifecycle::Running
    );
    assert_eq!(
        control
            .group(&group)
            .unwrap()
            .task_execution(&task)
            .unwrap()
            .lifecycle(),
        TaskExecutionLifecycle::Completed
    );
    let input = domain::MissionGoalEvidence {
        schema_version: "roboguide.mission-goal-evidence/v0.1".into(),
        evidence_id: "original-positive".into(),
        mission_id: mission.clone(),
        tenant_id: "unit-tenant".into(),
        contract_digest: contract.digest,
        source_id: "goal".into(),
        source_revision: 1,
        plan_revision: 0,
        group_id: group.clone(),
        plan_digest: mission_plan_digest(&plan),
        attempts: Vec::new(),
        predicate: "original objective measured".into(),
        verdict: domain::SemanticVerdict::Satisfied,
        finality: domain::VerifierFinality::Final,
        source_observed_at_ms: 123456,
        sequence: 1,
        valid_for_ms: 100,
        artifact_digest: None,
        artifact_size_bytes: None,
    };
    assert!(
        control
            .admit_mission_goal_evidence(
                input.clone(),
                "candidate-producer",
                &format!("sha256:{}", "c".repeat(64)),
                TimestampMs::new(10)
            )
            .is_err()
    );
    let receipt = control
        .admit_mission_goal_evidence(
            input.clone(),
            "independent-producer",
            &format!("sha256:{}", "c".repeat(64)),
            TimestampMs::new(10),
        )
        .unwrap();
    assert_eq!(
        control
            .admit_mission_goal_evidence(
                input.clone(),
                "independent-producer",
                &format!("sha256:{}", "c".repeat(64)),
                TimestampMs::new(90)
            )
            .unwrap()
            .received_at,
        TimestampMs::new(10)
    );
    assert!(
        !orchestrator
            .complete_mission_with_goal(
                &mission,
                &receipt.input_digest,
                &mut control,
                TimestampMs::new(110),
                &trace,
                &mut events
            )
            .unwrap()
    );
    let mut unknown = input.clone();
    unknown.evidence_id = "later-unknown".into();
    unknown.sequence = 2;
    unknown.verdict = domain::SemanticVerdict::Unknown;
    control
        .admit_mission_goal_evidence(
            unknown,
            "independent-producer",
            &format!("sha256:{}", "c".repeat(64)),
            TimestampMs::new(11),
        )
        .unwrap();
    assert!(
        !orchestrator
            .complete_mission_with_goal(
                &mission,
                &receipt.input_digest,
                &mut control,
                TimestampMs::new(12),
                &trace,
                &mut events
            )
            .unwrap()
    );
    let mut fresh = input;
    fresh.evidence_id = "later-positive".into();
    fresh.sequence = 3;
    let fresh = control
        .admit_mission_goal_evidence(
            fresh,
            "independent-producer",
            &format!("sha256:{}", "c".repeat(64)),
            TimestampMs::new(13),
        )
        .unwrap();
    let mut disabled = registry;
    disabled.revision = 2;
    disabled.sources.get_mut("goal").unwrap().revision = 2;
    disabled.sources.get_mut("goal").unwrap().enabled = false;
    let mut revoked = control.clone();
    revoked
        .accept_semantic_verifier_registry(disabled.clone(), 1)
        .unwrap();
    assert!(
        !orchestrator
            .clone()
            .complete_mission_with_goal(
                &mission,
                &fresh.input_digest,
                &mut revoked,
                TimestampMs::new(14),
                &trace,
                &mut events
            )
            .unwrap()
    );
    assert!(
        orchestrator
            .complete_mission_with_goal(
                &mission,
                &fresh.input_digest,
                &mut control,
                TimestampMs::new(14),
                &trace,
                &mut events
            )
            .unwrap()
    );
    control
        .accept_semantic_verifier_registry(disabled, 1)
        .unwrap();
    let restored_control = ControlPlane::restore(control.checkpoint()).unwrap();
    let restored =
        MissionOrchestrator::restore_json(&orchestrator.checkpoint_json().unwrap()).unwrap();
    restored
        .validate_control_authority(&restored_control)
        .unwrap();
    assert_eq!(
        restored.execution(&mission).unwrap().goal_receipt_digest(),
        Some(fresh.input_digest.as_str())
    );
    assert_eq!(
        restored.execution(&mission).unwrap().lifecycle(),
        MissionExecutionLifecycle::Completed
    );
    let mut forged: serde_json::Value =
        serde_json::from_str(&orchestrator.checkpoint_json().unwrap()).unwrap();
    forged[0]["goal_completed_at"] = 1000.into();
    let forged = MissionOrchestrator::restore_json(&forged.to_string()).unwrap();
    assert!(
        forged
            .validate_control_authority(&restored_control)
            .is_err()
    );
}
