//! Complete selected-capacity admission races; virtual test snapshots are never device evidence.

use super::*;

/// Creates one configured test registration with a selected pool and an adequate alternative.
fn node(selected: u32) -> NodeRegistration {
    resource_race_node(vec![
        Resource::new(
            ResourceId::new("selected").unwrap(),
            ResourceKind::Compute,
            selected,
        )
        .unwrap(),
        Resource::new(
            ResourceId::new("alternative").unwrap(),
            ResourceKind::Compute,
            4,
        )
        .unwrap(),
    ])
}

/// Prepares an actual Control commitment in a normalized Group using only offline test values.
fn fixture() -> (
    ControlPlane,
    InMemorySharedNodeState,
    RecordingEvents,
    MissionPlan,
    ExecutionGroupId,
    CommittedPlan,
) {
    let mut control = ControlPlane::new();
    let mut state = InMemorySharedNodeState::new();
    let mut events = RecordingEvents::default();
    let now = TimestampMs::new(0);
    let mission = domain::MissionId::new("capacity-unit-mission").unwrap();
    let role = RoleId::new("worker").unwrap();
    let contract = CapabilityContractRef::new("compute", "work", "v1").unwrap();
    control
        .register_node(
            &mut state,
            node(4),
            NodeStatus::new(NodeHealth::Online, now),
            now,
            &correlation(),
            &mut events,
        )
        .unwrap();
    let requirement = TaskRequirement::new(
        mission.clone(),
        TaskId::new("work").unwrap(),
        vec![
            RoleRequirement::new_scheduled(
                role.clone(),
                None,
                CapabilityKind::Compute,
                Some(contract.clone()),
                vec![ResourceRequirement::new(ResourceKind::Compute, 2).unwrap()],
            )
            .unwrap(),
        ],
    )
    .unwrap();
    let context = domain::CoordinationContextId::new("unit-context").unwrap();
    let task = PlannedTask::new(
        "work",
        requirement.clone(),
        BTreeMap::from([(
            role.clone(),
            ExecutionIntent::new(contract, BTreeMap::new()).unwrap(),
        )]),
        Vec::new(),
        domain::TaskContinuity::new(context.clone(), BTreeMap::new(), BTreeMap::new()),
    )
    .unwrap();
    let plan = MissionPlan::new(
        MissionGoal::new(mission.clone(), "work").unwrap(),
        TaskGraph::new(mission, vec![task]).unwrap(),
        vec![domain::CoordinationContext::new(context, Vec::new()).unwrap()],
    )
    .unwrap();
    let group = ExecutionGroupId::new("capacity-unit-group").unwrap();
    create_ready_physical_group(&mut control, &plan, &requirement, &group, &mut events);
    let candidates = control
        .match_capabilities_for_mission(
            &state,
            &plan,
            &requirement,
            now,
            &correlation(),
            &mut events,
        )
        .unwrap();
    let proposal = control
        .propose(
            &state,
            &requirement,
            &candidates,
            vec![RoleAssignment::new(
                role,
                NodeId::new("resource-node").unwrap(),
                vec![ResourceId::new("selected").unwrap()],
            )],
            now,
            &correlation(),
            &mut events,
        )
        .unwrap();
    let committed = control
        .commit_for_group_with_state(&state, &group, &proposal, now, &correlation(), &mut events)
        .unwrap();
    (control, state, events, plan, group, committed)
}

/// A retained original demand prevents in-place shrink while permitting a genuinely adequate reduction.
#[test]
fn live_registration_update_preserves_committed_capacity_atomically() {
    let (mut control, mut state, mut events, _, _, _) = fixture();
    let checkpoint = serde_json::to_value(control.checkpoint()).unwrap();
    let count = events.records.len();
    assert!(
        control
            .update_node_registration(
                &mut state,
                node(1),
                TimestampMs::new(1),
                &correlation(),
                &mut events
            )
            .is_err()
    );
    assert_eq!(
        serde_json::to_value(control.checkpoint()).unwrap(),
        checkpoint
    );
    assert_eq!(events.records.len(), count);
    assert!(
        state
            .node(&NodeId::new("resource-node").unwrap())
            .unwrap()
            .registration()
            .resources()
            .iter()
            .any(|resource| resource.id().as_str() == "selected" && resource.capacity() == 4)
    );
    control
        .update_node_registration(
            &mut state,
            node(2),
            TimestampMs::new(1),
            &correlation(),
            &mut events,
        )
        .unwrap();
}

/// Bind cannot substitute an adequate unselected pool after the selected declaration changed.
#[test]
fn bind_checks_exact_current_selected_capacity_and_keeps_commitment_on_refusal() {
    let (mut control, mut state, mut events, _, group, committed) = fixture();
    let snapshot = state
        .node(&NodeId::new("resource-node").unwrap())
        .unwrap()
        .clone();
    state
        .record_node(domain::NodeStateSnapshot::new(
            node(1),
            snapshot.reported_status(),
            TimestampMs::new(1),
            snapshot.liveness(),
        ))
        .unwrap();
    let checkpoint = serde_json::to_value(control.checkpoint()).unwrap();
    let count = events.records.len();
    assert!(matches!(
        control.bind_task_execution_with_state(
            &state,
            &group,
            &committed,
            TimestampMs::new(1),
            &correlation(),
            &mut events
        ),
        Err(ControlError::AssignmentUnavailable(_))
    ));
    assert_eq!(
        serde_json::to_value(control.checkpoint()).unwrap(),
        checkpoint
    );
    assert_eq!(events.records.len(), count);
}

/// Readiness rechecks the actual bound pool, preventing already committed dispatch from borrowing alternatives.
#[test]
fn bound_dispatch_rechecks_selected_capacity_without_releasing_original_owner() {
    let (mut control, mut state, mut events, plan, group, committed) = fixture();
    control
        .bind_task_execution_with_state(
            &state,
            &group,
            &committed,
            TimestampMs::new(0),
            &correlation(),
            &mut events,
        )
        .unwrap();
    let task = &plan.task_graph().tasks()[0];
    let command = domain::ExecutionCommand::new(
        plan.goal().mission_id().clone(),
        task.requirement().task_id().clone(),
        group,
        RoleId::new("worker").unwrap(),
        NodeId::new("resource-node").unwrap(),
        task.execution_intent(&RoleId::new("worker").unwrap())
            .unwrap()
            .clone(),
        correlation(),
    );
    assert!(control.bound_command_decision_allowed(
        &state,
        &command,
        domain::DecisionKind::Readiness,
        TimestampMs::new(0)
    ));
    let snapshot = state.node(command.node_id()).unwrap().clone();
    state
        .record_node(domain::NodeStateSnapshot::new(
            node(1),
            snapshot.reported_status(),
            TimestampMs::new(1),
            snapshot.liveness(),
        ))
        .unwrap();
    let checkpoint = serde_json::to_value(control.checkpoint()).unwrap();
    assert!(!control.bound_command_decision_allowed(
        &state,
        &command,
        domain::DecisionKind::Readiness,
        TimestampMs::new(1)
    ));
    assert_eq!(
        serde_json::to_value(control.checkpoint()).unwrap(),
        checkpoint
    );
}

/// Fresh registration after process restore cannot erase the original retained demand or renew ownership.
#[test]
fn restored_control_refuses_under_capacity_even_without_previous_live_snapshot() {
    let (control, _, _, _, _, _) = fixture();
    let mut control = ControlPlane::restore(control.checkpoint()).unwrap();
    let mut state = InMemorySharedNodeState::new();
    let mut events = RecordingEvents::default();
    let before = serde_json::to_value(control.checkpoint()).unwrap();
    assert!(
        control
            .register_node(
                &mut state,
                node(1),
                NodeStatus::new(NodeHealth::Online, TimestampMs::new(1)),
                TimestampMs::new(1),
                &correlation(),
                &mut events
            )
            .is_err()
    );
    assert!(state.nodes().is_empty());
    assert!(events.records.is_empty());
    assert_eq!(serde_json::to_value(control.checkpoint()).unwrap(), before);
}

/// Recovery Rebind cannot consume the original claim through a different adequate replacement resource.
#[test]
fn recovery_rebind_checks_selected_resources_before_consuming_the_pending_claim() {
    let mut fixture = recovery_fixture(true);
    let need = begin_detected_transport_recovery(&mut fixture);
    let candidates = match_fixture_recovery_candidates(&mut fixture, &need, TimestampMs::new(3));
    let proposed = propose_fixture_node_b(&mut fixture, &candidates, TimestampMs::new(4));
    let committed = commit_fixture_node_b(&mut fixture, &proposed, TimestampMs::new(5));
    let role = fixture
        .requirement
        .roles()
        .iter()
        .find(|role| role.role_id() == &fixture.transport_role)
        .unwrap()
        .clone();
    fixture
        .control
        .groups
        .get_mut(&fixture.group_id)
        .unwrap()
        .role_requirements
        .insert(
            (fixture.task_ref.clone(), fixture.transport_role.clone()),
            role,
        );
    let old = fixture.state.node(&fixture.node_b_id).unwrap().clone();
    let changed = registration(
        fixture.node_b_id.as_str(),
        CapabilityKind::Transport,
        "unselected-alternative",
    );
    fixture
        .state
        .record_node(domain::NodeStateSnapshot::new(
            changed,
            old.reported_status(),
            TimestampMs::new(6),
            old.liveness(),
        ))
        .unwrap();
    let checkpoint = serde_json::to_value(fixture.control.checkpoint()).unwrap();
    let count = fixture.events.records.len();
    assert!(matches!(
        fixture.control.rebind_role_with_state(
            &fixture.state,
            &committed,
            TimestampMs::new(6),
            &fixture.correlation_id,
            &mut fixture.events
        ),
        Err(ControlError::AssignmentUnavailable(_))
    ));
    assert_eq!(
        serde_json::to_value(fixture.control.checkpoint()).unwrap(),
        checkpoint
    );
    assert_eq!(fixture.events.records.len(), count);
}
