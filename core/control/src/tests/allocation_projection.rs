/// Normal Commit projects a resource as committed without Group ownership.
#[test]
fn allocation_projection_normal_commit_is_committed() {
    let node = registration("node-a", CapabilityKind::Transport, "space-a");
    let node_id = node.node_id().clone();
    let resource_id = ResourceId::new("space-a").expect("test resource id must be valid");
    let role_id = RoleId::new("transport").expect("test role id must be valid");
    let task = requirement("task-allocation", "transport", CapabilityKind::Transport);
    let timestamp = TimestampMs::new(0);
    let correlation_id = correlation();
    let mut control = ControlPlane::new();
    let mut state = InMemorySharedNodeState::new();
    let mut events = TestEvents;
    control
        .register_node(
            &mut state,
            node,
            NodeStatus::new(NodeHealth::Online, timestamp),
            timestamp,
            &correlation_id,
            &mut events,
        )
        .expect("test node registration should succeed");
    let candidates = control
        .match_capabilities(&state, &task, timestamp, &correlation_id, &mut events)
        .expect("test task should match");
    let proposal = control
        .propose(
            &state,
            &task,
            &candidates,
            vec![RoleAssignment::new(
                role_id.clone(),
                node_id,
                vec![resource_id.clone()],
            )],
            timestamp,
            &correlation_id,
            &mut events,
        )
        .expect("test proposal should validate");
    control
        .commit(&proposal, timestamp, &correlation_id, &mut events)
        .expect("test proposal should commit");

    let snapshot = control
        .allocation_snapshot(TimestampMs::new(1))
        .expect("committed allocation should project");
    assert_eq!(snapshot.allocations().len(), 1);
    let allocation = &snapshot.allocations()[0];
    assert_eq!(allocation.resource_id(), &resource_id);
    assert_eq!(allocation.task_ref(), task.task_ref());
    assert_eq!(allocation.role_id(), &role_id);
    assert_eq!(allocation.group_id(), None);
    assert_eq!(allocation.phase(), domain::AllocationPhase::Committed);
}

/// Group creation transitions the same projected resource from Committed to Bound.
#[test]
fn allocation_projection_group_bind_is_bound() {
    let fixture = recovery_fixture(true);
    let snapshot = fixture
        .control
        .allocation_snapshot(TimestampMs::new(1))
        .expect("bound Group allocation should project");
    let transport = snapshot
        .allocations()
        .iter()
        .find(|allocation| allocation.resource_id() == &fixture.space_a)
        .expect("transport allocation should exist");
    assert_eq!(transport.phase(), domain::AllocationPhase::Bound);
    assert_eq!(transport.group_id(), Some(&fixture.group_id));
    assert_eq!(transport.task_ref(), &fixture.task_ref);
    assert_eq!(transport.role_id(), &fixture.transport_role);
}

/// Partial release removes only the affected allocation and preserves compute binding.
#[test]
fn allocation_projection_partial_release_preserves_unaffected_binding() {
    let mut fixture = recovery_fixture(true);
    block_fixture(&mut fixture);
    fixture
        .control
        .release_role_binding(
            &fixture.group_id,
            &fixture.transport_role,
            TimestampMs::new(1),
            &fixture.correlation_id,
            &mut fixture.events,
        )
        .expect("transport binding should partially release");

    let snapshot = fixture
        .control
        .allocation_snapshot(TimestampMs::new(2))
        .expect("partial release should project");
    assert!(snapshot
        .allocations()
        .iter()
        .all(|allocation| allocation.resource_id() != &fixture.space_a));
    let compute = snapshot
        .allocations()
        .iter()
        .find(|allocation| allocation.resource_id() == &fixture.compute_c)
        .expect("compute allocation should remain");
    assert_eq!(compute.phase(), domain::AllocationPhase::Bound);
    assert_eq!(compute.group_id(), Some(&fixture.group_id));
    assert_eq!(compute.task_ref(), &fixture.task_ref);
    assert_eq!(compute.role_id(), &fixture.compute_role);
}

/// Recovery Commit projects replacement resources as pending beside unaffected Bound resources.
#[test]
fn allocation_projection_recovery_commit_is_pending() {
    let mut fixture = recovery_fixture(true);
    let need = begin_detected_transport_recovery(&mut fixture);
    let candidates =
        match_fixture_recovery_candidates(&mut fixture, &need, TimestampMs::new(3));
    let proposal = propose_fixture_node_b(&mut fixture, &candidates, TimestampMs::new(4));
    commit_fixture_node_b(&mut fixture, &proposal, TimestampMs::new(5));

    let snapshot = fixture
        .control
        .allocation_snapshot(TimestampMs::new(6))
        .expect("recovery commitment should project");
    let replacement = snapshot
        .allocations()
        .iter()
        .find(|allocation| allocation.resource_id() == &fixture.space_b)
        .expect("replacement allocation should exist");
    assert_eq!(
        replacement.phase(),
        domain::AllocationPhase::RecoveryPending
    );
    assert_eq!(replacement.group_id(), Some(&fixture.group_id));
    let compute = snapshot
        .allocations()
        .iter()
        .find(|allocation| allocation.resource_id() == &fixture.compute_c)
        .expect("compute allocation should remain");
    assert_eq!(compute.phase(), domain::AllocationPhase::Bound);
    assert!(fixture
        .control
        .group(&fixture.group_id)
        .expect("Group should remain")
        .is_role_unbound(&fixture.transport_role));
}

/// Recovery Rebind consumes pending commitment and projects replacement as Bound.
#[test]
fn allocation_projection_rebind_transitions_pending_to_bound() {
    let mut fixture = recovery_fixture(true);
    let need = begin_detected_transport_recovery(&mut fixture);
    let candidates =
        match_fixture_recovery_candidates(&mut fixture, &need, TimestampMs::new(3));
    let proposal = propose_fixture_node_b(&mut fixture, &candidates, TimestampMs::new(4));
    let committed = commit_fixture_node_b(&mut fixture, &proposal, TimestampMs::new(5));
    fixture
        .control
        .rebind_role(
            &committed,
            TimestampMs::new(6),
            &fixture.correlation_id,
            &mut fixture.events,
        )
        .expect("committed replacement should rebind");

    let snapshot = fixture
        .control
        .allocation_snapshot(TimestampMs::new(7))
        .expect("rebound allocation should project");
    let replacement = snapshot
        .allocations()
        .iter()
        .find(|allocation| allocation.resource_id() == &fixture.space_b)
        .expect("replacement allocation should exist");
    assert_eq!(replacement.phase(), domain::AllocationPhase::Bound);
    assert_eq!(replacement.group_id(), Some(&fixture.group_id));
}

/// Recovery Abort removes replacement projection while preserving unaffected resources.
#[test]
fn allocation_projection_abort_removes_replacement() {
    let mut fixture = recovery_fixture(true);
    let need = begin_detected_transport_recovery(&mut fixture);
    let candidates =
        match_fixture_recovery_candidates(&mut fixture, &need, TimestampMs::new(3));
    let proposal = propose_fixture_node_b(&mut fixture, &candidates, TimestampMs::new(4));
    let committed = commit_fixture_node_b(&mut fixture, &proposal, TimestampMs::new(5));
    fixture
        .control
        .abort_role_recovery_commitment(
            &committed,
            TimestampMs::new(6),
            &fixture.correlation_id,
            &mut fixture.events,
        )
        .expect("replacement commitment should abort");

    let snapshot = fixture
        .control
        .allocation_snapshot(TimestampMs::new(7))
        .expect("aborted allocation should project");
    assert!(snapshot
        .allocations()
        .iter()
        .all(|allocation| allocation.resource_id() != &fixture.space_b));
    assert!(snapshot
        .allocations()
        .iter()
        .any(|allocation| allocation.resource_id() == &fixture.compute_c));
}

/// Released Group leaves no projected allocation while another Mission remains unchanged.
#[test]
fn allocation_projection_release_is_multi_mission_isolated() {
    let mut fixture = recovery_fixture(true);
    let (node_c, space_c) = register_transport_replacement(
        &mut fixture,
        "node-c",
        "space-c",
        TimestampMs::new(1),
    );
    let mission_b_task = requirement_for_mission(
        "mission-b",
        "task-b",
        "transport-b",
        CapabilityKind::Transport,
    );
    let candidates_b = fixture
        .control
        .match_capabilities(
            &fixture.state,
            &mission_b_task,
            TimestampMs::new(1),
            &fixture.correlation_id,
            &mut fixture.events,
        )
        .expect("Mission B should match");
    let proposal_b = fixture
        .control
        .propose(
            &fixture.state,
            &mission_b_task,
            &candidates_b,
            vec![RoleAssignment::new(
                RoleId::new("transport-b").expect("test role id must be valid"),
                node_c,
                vec![space_c.clone()],
            )],
            TimestampMs::new(1),
            &fixture.correlation_id,
            &mut fixture.events,
        )
        .expect("Mission B proposal should validate");
    fixture
        .control
        .commit(
            &proposal_b,
            TimestampMs::new(1),
            &fixture.correlation_id,
            &mut fixture.events,
        )
        .expect("Mission B should commit");
    let before = fixture
        .control
        .allocation_snapshot(TimestampMs::new(2))
        .expect("multi-Mission allocations should project");
    let mission_b_before = before
        .allocations()
        .iter()
        .find(|allocation| allocation.resource_id() == &space_c)
        .expect("Mission B allocation should exist")
        .clone();
    fixture
        .control
        .complete_group(
            &fixture.group_id,
            TimestampMs::new(2),
            &fixture.correlation_id,
            &mut fixture.events,
        )
        .expect("Mission A should complete");
    fixture
        .control
        .release_group(
            &fixture.group_id,
            TimestampMs::new(3),
            &fixture.correlation_id,
            &mut fixture.events,
        )
        .expect("Mission A should release");

    let after = fixture
        .control
        .allocation_snapshot(TimestampMs::new(4))
        .expect("released allocation should project");
    assert!(after.allocations().iter().all(|allocation| {
        allocation.group_id() != Some(&fixture.group_id)
    }));
    assert_eq!(
        after
            .allocations()
            .iter()
            .find(|allocation| allocation.resource_id() == &space_c),
        Some(&mission_b_before)
    );
}

/// Projection rejects a Group reservation absent from active and pending ownership structures.
#[test]
fn allocation_projection_rejects_orphan_reservation() {
    let mut fixture = recovery_fixture(true);
    let orphan = ResourceId::new("orphan-resource").expect("test resource id must be valid");
    fixture.control.reservations.insert(
        orphan,
        Reservation {
            task_ref: fixture.task_ref.clone(),
            role_id: fixture.transport_role.clone(),
            group_id: Some(fixture.group_id.clone()),
            scope: domain::ResourceBindingScope::Task,
        },
    );

    assert!(matches!(
        fixture.control.allocation_snapshot(TimestampMs::new(1)),
        Err(ControlError::AllocationInvariant(_))
    ));
}

/// Projection lag and State replacement never change Control reservation authority.
#[test]
fn allocation_projection_lag_and_state_mutation_do_not_change_authority() {
    let node = registration("node-a", CapabilityKind::Transport, "space-a");
    let node_id = node.node_id().clone();
    let resource_id = ResourceId::new("space-a").expect("test resource id must be valid");
    let role_id = RoleId::new("transport").expect("test role id must be valid");
    let task = requirement("task-lag", "transport", CapabilityKind::Transport);
    let timestamp = TimestampMs::new(0);
    let correlation_id = correlation();
    let mut control = ControlPlane::new();
    let mut node_state = InMemorySharedNodeState::new();
    let mut allocation_state = InMemoryAllocationState::new();
    let mut events = TestEvents;
    control
        .register_node(
            &mut node_state,
            node,
            NodeStatus::new(NodeHealth::Online, timestamp),
            timestamp,
            &correlation_id,
            &mut events,
        )
        .expect("test node should register");
    let candidates = control
        .match_capabilities(
            &node_state,
            &task,
            timestamp,
            &correlation_id,
            &mut events,
        )
        .expect("test task should match");
    let proposal = control
        .propose(
            &node_state,
            &task,
            &candidates,
            vec![RoleAssignment::new(
                role_id,
                node_id,
                vec![resource_id.clone()],
            )],
            timestamp,
            &correlation_id,
            &mut events,
        )
        .expect("test proposal should validate");
    control
        .commit(&proposal, timestamp, &correlation_id, &mut events)
        .expect("Control authority should commit independently of State projection");

    assert!(allocation_state.allocations().is_empty());
    assert!(control.reservations.contains_key(&resource_id));
    allocation_state
        .replace_allocation_view(
            control
                .allocation_snapshot(TimestampMs::new(10))
                .expect("Control allocation should project"),
        )
        .expect("State should accept refreshed projection");
    assert!(allocation_state.allocation(&resource_id).is_some());

    allocation_state
        .replace_allocation_view(domain::AllocationViewSnapshot::new(
            TimestampMs::new(20),
            vec![],
        ))
        .expect("State may independently receive a later empty projection in this test");
    assert!(allocation_state.allocation(&resource_id).is_none());
    assert!(control.reservations.contains_key(&resource_id));
}
