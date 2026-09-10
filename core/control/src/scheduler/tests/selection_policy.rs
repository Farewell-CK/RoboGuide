//! Deterministic normal and recovery selection-policy tests.

use super::*;

/// Stable node ordering produces the same decision across repeated calls.
#[test]
fn normal_scheduler_is_stable_and_repeatable() {
    let mut state = InMemorySharedNodeState::new();
    for node_id in ["node-c", "node-a", "node-b"] {
        record_node(
            &mut state,
            registration(
                node_id,
                CapabilityKind::Transport,
                vec![(
                    match node_id {
                        "node-a" => "space-a",
                        "node-b" => "space-b",
                        _ => "space-c",
                    },
                    ResourceKind::Space,
                )],
            ),
        )
        .expect("test node snapshot should be accepted");
    }
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
    let candidates = CandidateSet::new(
        task.task_ref().clone(),
        vec![RoleCandidates::new(
            role_id,
            vec![
                NodeId::new("node-c").expect("test node id must be valid"),
                NodeId::new("node-a").expect("test node id must be valid"),
                NodeId::new("node-b").expect("test node id must be valid"),
            ],
        )],
    );
    let scheduler = BoundedJointScheduler::new();
    let mut events = TestEvents::default();
    let first = scheduler
        .schedule_task(
            &state,
            &task,
            &candidates,
            TimestampMs::new(0),
            &correlation(),
            &mut events,
        )
        .expect("first deterministic decision should succeed");
    let second = scheduler
        .schedule_task(
            &state,
            &task,
            &candidates,
            TimestampMs::new(0),
            &correlation(),
            &mut events,
        )
        .expect("second deterministic decision should succeed");

    assert_eq!(first, second);
    assert_eq!(first.selections()[0].node_id().as_str(), "node-a");
    assert_eq!(first.selections()[0].resource_ids()[0].as_str(), "space-a");
}

/// Nodes absent from CandidateSet are never selected even when present in State.
#[test]
fn scheduler_never_bypasses_candidate_set() {
    let mut state = InMemorySharedNodeState::new();
    for (node_id, resource_id) in [("node-a", "space-a"), ("node-b", "space-b")] {
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
    let candidates = CandidateSet::new(
        task.task_ref().clone(),
        vec![RoleCandidates::new(
            role_id,
            vec![NodeId::new("node-b").expect("test node id must be valid")],
        )],
    );
    let mut events = TestEvents::default();
    let decision = BoundedJointScheduler::new()
        .schedule_task(
            &state,
            &task,
            &candidates,
            TimestampMs::new(0),
            &correlation(),
            &mut events,
        )
        .expect("candidate-only decision should succeed");

    assert_eq!(decision.selections()[0].node_id().as_str(), "node-b");
}

/// Resource choice is stable and selects only one declared resource of the required kind.
#[test]
fn scheduler_selects_stable_minimal_resource() {
    let mut state = InMemorySharedNodeState::new();
    record_node(
        &mut state,
        registration(
            "node-a",
            CapabilityKind::Transport,
            vec![
                ("space-z", ResourceKind::Space),
                ("space-a", ResourceKind::Space),
                ("space-b", ResourceKind::Space),
            ],
        ),
    )
    .expect("test node snapshot should be accepted");
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
    let candidates = CandidateSet::new(
        task.task_ref().clone(),
        vec![RoleCandidates::new(
            role_id,
            vec![NodeId::new("node-a").expect("test node id must be valid")],
        )],
    );
    let mut events = TestEvents::default();
    let decision = BoundedJointScheduler::new()
        .schedule_task(
            &state,
            &task,
            &candidates,
            TimestampMs::new(0),
            &correlation(),
            &mut events,
        )
        .expect("resource decision should succeed");

    assert_eq!(decision.selections()[0].resource_ids().len(), 1);
    assert_eq!(
        decision.selections()[0].resource_ids()[0].as_str(),
        "space-a"
    );
}

/// A role without ResourceKind produces an empty resource suggestion.
#[test]
fn scheduler_supports_resource_free_role() {
    let mut state = InMemorySharedNodeState::new();
    record_node(
        &mut state,
        registration("node-a", CapabilityKind::Observation, vec![]),
    )
    .expect("test node snapshot should be accepted");
    let role_id = RoleId::new("observe").expect("test role id must be valid");
    let task = requirement(
        "mission-a",
        "task-a",
        vec![RoleRequirement::new(
            role_id.clone(),
            CapabilityKind::Observation,
            None,
        )],
    );
    let candidates = CandidateSet::new(
        task.task_ref().clone(),
        vec![RoleCandidates::new(
            role_id,
            vec![NodeId::new("node-a").expect("test node id must be valid")],
        )],
    );
    let mut events = TestEvents::default();
    let decision = BoundedJointScheduler::new()
        .schedule_task(
            &state,
            &task,
            &candidates,
            TimestampMs::new(0),
            &correlation(),
            &mut events,
        )
        .expect("resource-free decision should succeed");

    assert!(decision.selections()[0].resource_ids().is_empty());
}

/// Multi-role scheduling avoids duplicate exclusive resources without backtracking.
#[test]
fn scheduler_avoids_duplicate_resource_within_decision() {
    let mut state = InMemorySharedNodeState::new();
    for (node_id, resource_id) in [("node-a", "space-a"), ("node-b", "space-b")] {
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
    let first_role = RoleId::new("transport-a").expect("test role id must be valid");
    let second_role = RoleId::new("transport-b").expect("test role id must be valid");
    let task = requirement(
        "mission-a",
        "task-a",
        vec![
            RoleRequirement::new(
                first_role.clone(),
                CapabilityKind::Transport,
                Some(ResourceKind::Space),
            ),
            RoleRequirement::new(
                second_role.clone(),
                CapabilityKind::Transport,
                Some(ResourceKind::Space),
            ),
        ],
    );
    let candidate_nodes = vec![
        NodeId::new("node-a").expect("test node id must be valid"),
        NodeId::new("node-b").expect("test node id must be valid"),
    ];
    let candidates = CandidateSet::new(
        task.task_ref().clone(),
        vec![
            RoleCandidates::new(first_role, candidate_nodes.clone()),
            RoleCandidates::new(second_role, candidate_nodes),
        ],
    );
    let mut events = TestEvents::default();
    let decision = BoundedJointScheduler::new()
        .schedule_task(
            &state,
            &task,
            &candidates,
            TimestampMs::new(0),
            &correlation(),
            &mut events,
        )
        .expect("multi-role decision should succeed");

    assert_eq!(decision.selections()[0].node_id().as_str(), "node-a");
    assert_eq!(decision.selections()[1].node_id().as_str(), "node-b");
    assert_ne!(
        decision.selections()[0].resource_ids(),
        decision.selections()[1].resource_ids()
    );
}

/// Resource reuse constraints return a typed error when the complete Task is infeasible.
#[test]
fn scheduler_reports_no_feasible_joint_selection() {
    let mut state = InMemorySharedNodeState::new();
    record_node(
        &mut state,
        registration(
            "node-a",
            CapabilityKind::Transport,
            vec![("space-a", ResourceKind::Space)],
        ),
    )
    .expect("test node snapshot should be accepted");
    let first_role = RoleId::new("transport-a").expect("test role id must be valid");
    let second_role = RoleId::new("transport-b").expect("test role id must be valid");
    let task = requirement(
        "mission-a",
        "task-a",
        vec![
            RoleRequirement::new(
                first_role.clone(),
                CapabilityKind::Transport,
                Some(ResourceKind::Space),
            ),
            RoleRequirement::new(
                second_role.clone(),
                CapabilityKind::Transport,
                Some(ResourceKind::Space),
            ),
        ],
    );
    let only_node = vec![NodeId::new("node-a").expect("test node id must be valid")];
    let candidates = CandidateSet::new(
        task.task_ref().clone(),
        vec![
            RoleCandidates::new(first_role, only_node.clone()),
            RoleCandidates::new(second_role.clone(), only_node),
        ],
    );
    let mut events = TestEvents::default();

    assert_eq!(
        BoundedJointScheduler::new().schedule_task(
            &state,
            &task,
            &candidates,
            TimestampMs::new(0),
            &correlation(),
            &mut events,
        ),
        Err(SchedulerError::NoFeasibleSelection(
            RoleId::new("transport-a").expect("test role id must be valid")
        ))
    );
}

/// Normal and recovery scheduling share the same stable role-selection policy.
#[test]
fn normal_and_recovery_scheduling_are_policy_consistent() {
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
    let node_b = NodeId::new("node-b").expect("test node id must be valid");
    let node_c = NodeId::new("node-c").expect("test node id must be valid");
    let normal_candidates = CandidateSet::new(
        task.task_ref().clone(),
        vec![RoleCandidates::new(
            role_id.clone(),
            vec![node_c.clone(), node_b.clone()],
        )],
    );
    let recovery_candidates = RecoveryCandidateSet::new(
        ExecutionGroupId::new("group-a").expect("test group id must be valid"),
        task.task_ref().clone(),
        role_id,
        NodeId::new("node-a").expect("test node id must be valid"),
        vec![node_c, node_b],
    );
    let scheduler = BoundedJointScheduler::new();
    let mut events = TestEvents::default();
    let normal = scheduler
        .schedule_task(
            &state,
            &task,
            &normal_candidates,
            TimestampMs::new(0),
            &correlation(),
            &mut events,
        )
        .expect("normal decision should succeed");
    let recovery = scheduler
        .schedule_recovery(
            &state,
            &task,
            &recovery_candidates,
            &SchedulingSnapshot::empty(),
            TimestampMs::new(0),
            &correlation(),
            &mut events,
        )
        .expect("recovery decision should succeed");
    let RecoverySchedulingOutcome::Selected(recovery) = recovery else {
        panic!("recovery candidates should produce a selection");
    };

    assert_eq!(
        normal.selections()[0].node_id(),
        recovery.replacement_node_id()
    );
    assert_eq!(
        normal.selections()[0].resource_ids(),
        recovery.resource_ids()
    );
}
