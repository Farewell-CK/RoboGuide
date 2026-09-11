//! Mission execution, Context reuse, and cancellation lifecycle tests.

use super::*;
use ports::{TaskSatisfactionEvidenceReader, TaskSatisfactionEvidenceWriter};
use state::InMemoryTaskSatisfactionState;

/// Builds a registration with the exact contracts and resources used by the Phase 1 fixture.
pub(super) fn registration(
    node_id: &str,
    capabilities: Vec<Capability>,
    contracts: Vec<(CapabilityContractRef, CapabilityKind)>,
    resources: Vec<(ResourceId, ResourceKind)>,
) -> NodeRegistration {
    let local_system_id = LocalSystemId::new("test-system").expect("system id valid");
    let capability_owners = contracts
        .iter()
        .map(|(contract, _)| (contract.clone(), local_system_id.clone()))
        .collect();
    let capability_kinds = contracts.iter().cloned().collect();
    let capability_readiness = contracts
        .iter()
        .map(|(contract, _)| (contract.clone(), true))
        .collect();
    let resources = resources
        .into_iter()
        .map(|(resource_id, kind)| Resource::new(resource_id, kind, 1).expect("resource valid"))
        .collect::<Vec<_>>();
    let resource_owners = resources
        .iter()
        .map(|resource| (resource.id().clone(), local_system_id.clone()))
        .collect();
    NodeRegistration::new_with_local_systems_and_readiness(
        NodeId::new(node_id).expect("node id valid"),
        vec![LocalSystemDescriptor::new(
            local_system_id,
            LocalRuntime::new("phase1-test", "0.1.0").expect("runtime valid"),
            BTreeMap::new(),
        )],
        NodeContractVersion::v0_1(),
        capabilities,
        capability_owners,
        capability_kinds,
        capability_readiness,
        Vec::new(),
        resources,
        resource_owners,
    )
    .expect("exact test registration is valid")
}

/// Verifies the complete Phase 1 DAG, Context continuity, and explicit Group completion.
#[test]
fn phase1_execution_reuses_context_binding_until_mission_completion() {
    let source = include_str!("../../../../../scenarios/phase1-mission-v0.2/mission-plan.json");
    let plan = decode_mission_plan(source).expect("fixture should decode");
    let mission_id = plan.goal().mission_id().clone();
    let group_id = ExecutionGroupId::new("group-phase1-test").expect("group id valid");
    let timestamp = TimestampMs::new(1);
    let correlation = CorrelationId::new("phase1-orchestration-test").expect("trace valid");
    let mut control = ControlPlane::new();
    let mut state = InMemorySharedNodeState::new();
    let mut events = InMemoryEventLog::new();
    let compute_prepare =
        CapabilityContractRef::new("compute", "prepare", "v1").expect("contract valid");
    let compute_verify =
        CapabilityContractRef::new("observation", "verify", "v1").expect("contract valid");
    let move_contract =
        CapabilityContractRef::new("mobility", "move", "v1").expect("contract valid");
    for node in [
        registration(
            "edge",
            vec![
                Capability::new(CapabilityKind::Compute, true),
                Capability::new(CapabilityKind::Observation, true),
            ],
            vec![
                (compute_prepare.clone(), CapabilityKind::Compute),
                (compute_verify.clone(), CapabilityKind::Observation),
            ],
            vec![(
                ResourceId::new("edge-compute").expect("resource id valid"),
                ResourceKind::Compute,
            )],
        ),
        registration(
            "carrier",
            vec![Capability::new(CapabilityKind::Transport, true)],
            vec![(move_contract.clone(), CapabilityKind::Transport)],
            vec![(
                ResourceId::new("carrier-space").expect("resource id valid"),
                ResourceKind::Space,
            )],
        ),
    ] {
        control
            .register_node(
                &mut state,
                node,
                NodeStatus::new(NodeHealth::Online, timestamp),
                timestamp,
                &correlation,
                &mut events,
            )
            .expect("test node registers");
    }
    let mut orchestrator = MissionOrchestrator::new();
    orchestrator
        .submit(
            plan.clone(),
            group_id.clone(),
            &mut control,
            timestamp,
            &correlation,
            &mut events,
        )
        .expect("Mission should be accepted");
    assert_eq!(orchestrator.ready_tasks(&mission_id, &control).len(), 1);
    let task = |index: usize| {
        plan.task_graph().tasks()[index]
            .requirement()
            .task_ref()
            .clone()
    };
    for index in [0_usize, 1, 2, 3] {
        let task_ref = task(index);
        orchestrator
            .prepare_task(
                &mission_id,
                &task_ref,
                &state,
                &mut control,
                TimestampMs::new(2 + index as u64),
                &correlation,
                &mut events,
            )
            .expect("ready Task should bind");
        control
            .activate_task_execution(
                &group_id,
                &task_ref,
                TimestampMs::new(3 + index as u64),
                &correlation,
                &mut events,
            )
            .expect("test Runtime transition should activate the bound Task");
        let allocations_before_completion = control
            .allocation_snapshot(TimestampMs::new(9 + index as u64))
            .expect("active Task allocations are valid")
            .allocations()
            .to_vec();
        orchestrator
            .record_task_execution_completed(
                &mission_id,
                &task_ref,
                &mut control,
                TimestampMs::new(10 + index as u64),
                &correlation,
                &mut events,
            )
            .expect("Task execution outcome should be recorded");
        assert_eq!(
            control
                .group(&group_id)
                .and_then(|group| group.task_execution(&task_ref))
                .expect("Task remains in the Group")
                .lifecycle(),
            TaskExecutionLifecycle::AwaitingSatisfaction
        );
        assert_eq!(
            control
                .allocation_snapshot(TimestampMs::new(10 + index as u64))
                .expect("execution-complete allocations remain valid")
                .allocations(),
            allocations_before_completion,
            "execution completion must not change resource ownership"
        );
        assert!(events.contains_payload(|payload| matches!(
            payload,
            domain::EventPayload::TaskExecutionCompleted { task_ref: event_task, .. }
                if event_task == &task_ref
        )));
        assert!(!events.contains_payload(|payload| matches!(
            payload,
            domain::EventPayload::TaskSatisfied { task_ref: event_task, .. }
                if event_task == &task_ref
        )));
        let restored_control = ControlPlane::restore(control.checkpoint())
            .expect("AwaitingSatisfaction Control authority should restore");
        let restored_orchestrator = MissionOrchestrator::restore_json(
            &orchestrator
                .checkpoint_json()
                .expect("AwaitingSatisfaction orchestration should serialize"),
        )
        .expect("AwaitingSatisfaction orchestration should restore");
        restored_orchestrator
            .validate_control_authority(&restored_control)
            .expect("restored satisfaction boundary should retain aligned authority");
        if index + 1 < plan.task_graph().tasks().len() {
            let next_task_ref = task(index + 1);
            assert_eq!(
                control
                    .group(&group_id)
                    .and_then(|group| group.task_execution(&next_task_ref))
                    .expect("dependent Task remains registered")
                    .lifecycle(),
                TaskExecutionLifecycle::Pending,
                "execution completion must not unlock the next DAG Task"
            );
        }
        orchestrator
            .satisfy_task_from_execution_report(
                &mission_id,
                &task_ref,
                &mut control,
                TimestampMs::new(10 + index as u64),
                &correlation,
                &mut events,
            )
            .expect("Task satisfaction should advance the DAG");
        assert!(events.contains_payload(|payload| matches!(
            payload,
            domain::EventPayload::TaskSatisfied {
                task_ref: event_task,
                basis: domain::TaskSatisfactionBasis::ExecutionReport,
                ..
            } if event_task == &task_ref
        )));
        if index < 2 {
            assert_eq!(
                orchestrator
                    .execution(&mission_id)
                    .expect("Mission exists")
                    .lifecycle(),
                MissionExecutionLifecycle::Running
            );
        }
        if index == 1 {
            assert!(
                control
                    .group(&group_id)
                    .expect("Group retained between Tasks")
                    .context_binding(
                        &CoordinationContextId::new("delivery-context").expect("context id valid"),
                        &domain::ContextRoleId::new("carrier").expect("role id valid"),
                    )
                    .is_some()
            );
        }
    }
    assert_eq!(
        orchestrator
            .execution(&mission_id)
            .expect("Mission exists")
            .lifecycle(),
        MissionExecutionLifecycle::Completed
    );
    assert_eq!(
        control
            .group(&group_id)
            .expect("released Group retained as history")
            .lifecycle(),
        GroupLifecycle::Released
    );
    assert!(
        control
            .allocation_snapshot(TimestampMs::new(20))
            .expect("projection valid")
            .allocations()
            .is_empty()
    );
}

/// Verifier-backed Tasks remain incomplete until fresh positive State evidence is applied.
#[test]
fn verifier_evidence_is_distinct_from_local_execution_completion() {
    let source = include_str!("../../../../../scenarios/mission-front-half-v0.7/mission-plan.json");
    let plan = decode_mission_plan(source).expect("normalized fixture should decode");
    let mission_id = plan.goal().mission_id().clone();
    let task_ref = plan.task_graph().tasks()[0]
        .requirement()
        .task_ref()
        .clone();
    let group_id = ExecutionGroupId::new("group-verifier-test").expect("group id valid");
    let correlation = CorrelationId::new("verifier-satisfaction-test").expect("trace valid");
    let relocate = CapabilityContractRef::new("object", "relocate", "v1").expect("contract valid");
    let mut attributes = BTreeMap::new();
    attributes.insert(
        relocate.clone(),
        BTreeMap::from([(
            "max-payload-grams".to_string(),
            domain::ExecutionValue::Integer(5_000),
        )]),
    );
    let node = registration(
        "carrier",
        vec![Capability::new(CapabilityKind::Transport, true)],
        vec![(relocate, CapabilityKind::Transport)],
        vec![(
            ResourceId::new("carrier-space").expect("resource id valid"),
            ResourceKind::Space,
        )],
    )
    .with_capability_attributes(attributes)
    .expect("capability attributes valid");
    let mut control = ControlPlane::new();
    let mut node_state = InMemorySharedNodeState::new();
    let mut evidence_state = InMemoryTaskSatisfactionState::new();
    let mut events = InMemoryEventLog::new();
    control
        .register_node(
            &mut node_state,
            node,
            NodeStatus::new(NodeHealth::Online, TimestampMs::new(1)),
            TimestampMs::new(1),
            &correlation,
            &mut events,
        )
        .expect("test node registers");
    let mut orchestrator = MissionOrchestrator::new();
    orchestrator
        .submit(
            plan,
            group_id.clone(),
            &mut control,
            TimestampMs::new(1),
            &correlation,
            &mut events,
        )
        .expect("Mission accepts");
    orchestrator
        .prepare_task(
            &mission_id,
            &task_ref,
            &node_state,
            &mut control,
            TimestampMs::new(2),
            &correlation,
            &mut events,
        )
        .expect("Task binds");
    control
        .activate_task_execution(
            &group_id,
            &task_ref,
            TimestampMs::new(3),
            &correlation,
            &mut events,
        )
        .expect("Task activates");
    orchestrator
        .record_task_execution_completed(
            &mission_id,
            &task_ref,
            &mut control,
            TimestampMs::new(4),
            &correlation,
            &mut events,
        )
        .expect("local execution completion records");
    assert!(
        orchestrator
            .satisfy_task_from_execution_report(
                &mission_id,
                &task_ref,
                &mut control,
                TimestampMs::new(5),
                &correlation,
                &mut events,
            )
            .is_err()
    );

    let verifier =
        CapabilityContractRef::new("observation", "verify", "v1").expect("verifier contract valid");
    let source = domain::StateSource::Node {
        node_id: NodeId::new("camera-a").expect("node id valid"),
        local_system_id: LocalSystemId::new("semantic-verifier").expect("system id valid"),
    };
    let stale = domain::TaskSatisfactionEvidence::new(
        task_ref.clone(),
        verifier.clone(),
        "object first-aid-kit-2f is in reception-1f",
        source.clone(),
        TimestampMs::new(99_000),
        TimestampMs::new(10),
        true,
    )
    .expect("stale evidence shape valid");
    evidence_state
        .record_task_satisfaction_evidence(stale)
        .expect("State records verifier evidence");
    assert!(
        orchestrator
            .satisfy_task_from_verifier(
                &mission_id,
                evidence_state.task_satisfaction_evidence(&task_ref)[0],
                &mut control,
                TimestampMs::new(5_011),
                &correlation,
                &mut events,
            )
            .is_err()
    );

    let fresh = domain::TaskSatisfactionEvidence::new(
        task_ref.clone(),
        verifier,
        "object first-aid-kit-2f is in reception-1f",
        source,
        TimestampMs::new(1),
        TimestampMs::new(5_012),
        true,
    )
    .expect("fresh evidence valid");
    evidence_state
        .record_task_satisfaction_evidence(fresh)
        .expect("new verifier evidence replaces old source channel");
    orchestrator
        .satisfy_task_from_verifier(
            &mission_id,
            evidence_state.task_satisfaction_evidence(&task_ref)[0],
            &mut control,
            TimestampMs::new(5_012),
            &correlation,
            &mut events,
        )
        .expect("fresh positive evidence satisfies Task");

    assert_eq!(
        orchestrator
            .execution(&mission_id)
            .expect("Mission retained")
            .lifecycle(),
        MissionExecutionLifecycle::Completed
    );
    assert_eq!(
        control
            .group(&group_id)
            .expect("Group retained as history")
            .lifecycle(),
        GroupLifecycle::Released
    );
}

/// Cancellation remains valid after Control has already blocked the Mission Group.
#[test]
fn blocked_mission_can_be_cancelled_and_released() {
    let source = include_str!("../../../../../scenarios/phase1-mission-v0.2/mission-plan.json");
    let plan = decode_mission_plan(source).expect("fixture should decode");
    let mission_id = plan.goal().mission_id().clone();
    let group_id = ExecutionGroupId::new("group-cancel-blocked").expect("group id valid");
    let correlation = CorrelationId::new("cancel-blocked-test").expect("trace valid");
    let mut control = ControlPlane::new();
    let mut events = InMemoryEventLog::new();
    let mut orchestrator = MissionOrchestrator::new();
    orchestrator
        .submit(
            plan,
            group_id.clone(),
            &mut control,
            TimestampMs::new(1),
            &correlation,
            &mut events,
        )
        .expect("Mission should be accepted");
    control
        .block_group(
            &group_id,
            "node recovery required",
            TimestampMs::new(2),
            &correlation,
            &mut events,
        )
        .expect("Group should become blocked");
    orchestrator
        .cancel(
            &mission_id,
            &mut control,
            TimestampMs::new(3),
            &correlation,
            &mut events,
        )
        .expect("Blocked Mission should cancel");
    assert_eq!(
        orchestrator
            .execution(&mission_id)
            .expect("Mission retained")
            .lifecycle(),
        MissionExecutionLifecycle::Cancelled
    );
    assert_eq!(
        control
            .group(&group_id)
            .expect("Group retained")
            .lifecycle(),
        GroupLifecycle::Released
    );
}

/// A cancellation request survives checkpoint restore before terminal attempt evidence arrives.
#[test]
fn cancelling_mission_restores_and_waits_for_explicit_finalization() {
    let source = include_str!("../../../../../scenarios/phase1-mission-v0.2/mission-plan.json");
    let plan = decode_mission_plan(source).expect("fixture should decode");
    let mission_id = plan.goal().mission_id().clone();
    let group_id = ExecutionGroupId::new("group-cancel-durable").expect("group id valid");
    let correlation = CorrelationId::new("cancel-durable-test").expect("trace valid");
    let mut control = ControlPlane::new();
    let mut events = InMemoryEventLog::new();
    let mut orchestrator = MissionOrchestrator::new();
    orchestrator
        .submit(
            plan,
            group_id.clone(),
            &mut control,
            TimestampMs::new(1),
            &correlation,
            &mut events,
        )
        .expect("Mission should be accepted");
    orchestrator
        .request_cancel(
            &mission_id,
            &mut control,
            TimestampMs::new(2),
            &correlation,
            &mut events,
        )
        .expect("cancellation request should persist");

    let checkpoint = orchestrator
        .checkpoint_json()
        .expect("cancelling Mission serializes");
    let mut restored =
        MissionOrchestrator::restore_json(&checkpoint).expect("cancelling Mission restores");
    restored
        .validate_control_authority(&control)
        .expect("Cancelling Mission retains a blocked Group");
    assert_eq!(
        restored
            .execution(&mission_id)
            .expect("Mission retained")
            .lifecycle(),
        MissionExecutionLifecycle::Cancelling
    );
    assert!(
        restored
            .dispatchable_tasks(&mission_id, &control)
            .is_empty()
    );

    restored
        .finalize_cancel(
            &mission_id,
            &mut control,
            TimestampMs::new(3),
            &correlation,
            &mut events,
        )
        .expect("terminal attempt evidence permits finalization");
    assert_eq!(
        restored
            .execution(&mission_id)
            .expect("Mission retained")
            .lifecycle(),
        MissionExecutionLifecycle::Cancelled
    );
    assert_eq!(
        control
            .group(&group_id)
            .expect("Group retained as history")
            .lifecycle(),
        GroupLifecycle::Released
    );
}
