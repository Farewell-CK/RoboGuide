//! Recovery scheduling and Decision-to-Commit boundary tests.

use super::*;

/// Recovery scheduling consumes the same calendar and avoids a future resource commitment.
#[test]
fn recovery_scheduler_respects_future_calendar_occupancy() {
    let mut state = InMemorySharedNodeState::new();
    for (node_id, resource_id) in [("node-b", "space-b"), ("node-c", "space-c")] {
        record_node(
            &mut state,
            registration(
                node_id,
                CapabilityKind::Transport,
                vec![(resource_id, ResourceKind::Space)],
            ),
        )
        .expect("test node snapshot should be accepted");
    }
    let role_id = RoleId::new("transport").expect("role id valid");
    let task = requirement(
        "mission-recovery-calendar",
        "task-a",
        vec![RoleRequirement::new(
            role_id.clone(),
            CapabilityKind::Transport,
            Some(ResourceKind::Space),
        )],
    );
    let candidates = RecoveryCandidateSet::new(
        ExecutionGroupId::new("group-a").expect("group id valid"),
        task.task_ref().clone(),
        role_id,
        NodeId::new("node-a").expect("previous node valid"),
        vec![
            NodeId::new("node-b").expect("candidate node valid"),
            NodeId::new("node-c").expect("candidate node valid"),
        ],
    );
    let snapshot = SchedulingSnapshot::new(
        1,
        vec![SchedulingOccupancy::new(
            ResourceId::new("space-b").expect("resource id valid"),
            TimestampMs::new(10),
            Some(TimestampMs::new(20)),
        )],
    );
    let mut events = TestEvents::default();

    let RecoverySchedulingOutcome::Selected(decision) = BoundedJointScheduler::new()
        .schedule_recovery(
            &state,
            &task,
            &candidates,
            &snapshot,
            TimestampMs::new(0),
            &correlation(),
            &mut events,
        )
        .expect("recovery scheduling succeeds")
    else {
        panic!("an unreserved recovery candidate should be selected");
    };

    assert_eq!(decision.replacement_node_id().as_str(), "node-c");
    assert_eq!(decision.resource_ids()[0].as_str(), "space-c");
}

/// Empty recovery candidates return NoSelection without Group or authority mutation.
#[test]
fn recovery_scheduler_empty_candidates_return_no_selection() {
    let state = InMemorySharedNodeState::new();
    let role_id = RoleId::new("transport").expect("test role id must be valid");
    let task = requirement(
        "mission-a",
        "task-a",
        vec![RoleRequirement::new(
            role_id.clone(),
            CapabilityKind::Transport,
            Some(ResourceKind::Space),
        )],
    );
    let candidates = RecoveryCandidateSet::new(
        ExecutionGroupId::new("group-a").expect("test group id must be valid"),
        task.task_ref().clone(),
        role_id,
        NodeId::new("node-a").expect("test node id must be valid"),
        vec![],
    );
    let control = ControlPlane::new();
    let mut events = TestEvents::default();
    let outcome = BoundedJointScheduler::new()
        .schedule_recovery(
            &state,
            &task,
            &candidates,
            &SchedulingSnapshot::empty(),
            TimestampMs::new(0),
            &correlation(),
            &mut events,
        )
        .expect("empty recovery candidates are not an error");

    assert_eq!(outcome, RecoverySchedulingOutcome::NoSelection);
    assert!(control.reservations.is_empty());
    assert!(control.groups.is_empty());
    assert!(control.pending_recovery_commitments.is_empty());
    assert!(matches!(
        events.payloads.last(),
        Some(EventPayload::RecoverySchedulingNoSelection { .. })
    ));
}

/// Scheduler decision remains non-authoritative and Commit can reject it after Proposal.
#[test]
fn scheduler_decision_and_proposal_do_not_override_commit_conflict() {
    let registration = registration(
        "node-a",
        CapabilityKind::Transport,
        vec![("space-a", ResourceKind::Space)],
    );
    let node_id = registration.node_id().clone();
    let resource_id = ResourceId::new("space-a").expect("test resource id must be valid");
    let role_a = RoleId::new("transport-a").expect("test role id must be valid");
    let role_b = RoleId::new("transport-b").expect("test role id must be valid");
    let task_a = requirement(
        "mission-a",
        "task-a",
        vec![RoleRequirement::new(
            role_a.clone(),
            CapabilityKind::Transport,
            Some(ResourceKind::Space),
        )],
    );
    let task_b = requirement(
        "mission-b",
        "task-b",
        vec![RoleRequirement::new(
            role_b.clone(),
            CapabilityKind::Transport,
            Some(ResourceKind::Space),
        )],
    );
    let mut control = ControlPlane::new();
    let mut state = InMemorySharedNodeState::new();
    let mut events = TestEvents::default();
    control
        .register_node(
            &mut state,
            registration,
            NodeStatus::new(NodeHealth::Online, TimestampMs::new(0)),
            TimestampMs::new(0),
            &correlation(),
            &mut events,
        )
        .expect("test node should register");
    let candidates_a = control
        .match_capabilities(
            &state,
            &task_a,
            TimestampMs::new(0),
            &correlation(),
            &mut events,
        )
        .expect("Mission A should match");
    let candidates_b = control
        .match_capabilities(
            &state,
            &task_b,
            TimestampMs::new(0),
            &correlation(),
            &mut events,
        )
        .expect("Mission B should match");
    let state_before_scheduling = state
        .node(&node_id)
        .expect("scheduler node should remain in State")
        .clone();
    let scheduler = BoundedJointScheduler::new();
    let decision_a = scheduler
        .schedule_task(
            &state,
            &task_a,
            &candidates_a,
            TimestampMs::new(0),
            &correlation(),
            &mut events,
        )
        .expect("Mission A decision should succeed");
    let decision_b = scheduler
        .schedule_task(
            &state,
            &task_b,
            &candidates_b,
            TimestampMs::new(0),
            &correlation(),
            &mut events,
        )
        .expect("Mission B decision should succeed");
    assert!(control.reservations.is_empty());
    assert!(control.groups.is_empty());
    assert!(control.pending_recovery_commitments.is_empty());
    assert_eq!(decision_a.task_ref(), task_a.task_ref());
    assert_eq!(decision_b.task_ref(), task_b.task_ref());
    assert_eq!(
        decision_a.selections()[0].node_id(),
        decision_b.selections()[0].node_id()
    );
    assert_eq!(
        state.node(&node_id).expect("State must remain unchanged"),
        &state_before_scheduling
    );
    let proposal_a = control
        .propose(
            &state,
            &task_a,
            &candidates_a,
            decision_a.proposed_assignments(),
            TimestampMs::new(0),
            &correlation(),
            &mut events,
        )
        .expect("Mission A proposal should validate");
    let proposal_b = control
        .propose(
            &state,
            &task_b,
            &candidates_b,
            decision_b.proposed_assignments(),
            TimestampMs::new(0),
            &correlation(),
            &mut events,
        )
        .expect("Mission B proposal should validate");
    assert!(control.reservations.is_empty());
    control
        .commit(
            &proposal_b,
            TimestampMs::new(0),
            &correlation(),
            &mut events,
        )
        .expect("Mission B should commit first");

    assert!(matches!(
        control.commit(
            &proposal_a,
            TimestampMs::new(0),
            &correlation(),
            &mut events,
        ),
        Err(ControlError::ResourceConflict { resource_id: conflict, .. })
            if conflict == resource_id
    ));
    let reservation = control
        .reservations
        .get(&resource_id)
        .expect("Mission B reservation should remain");
    assert_eq!(reservation.role_id, role_b);
    assert_eq!(reservation.task_ref, *task_b.task_ref());
    assert_eq!(node_id.as_str(), "node-a");
    assert_eq!(role_a.as_str(), "transport-a");
}
