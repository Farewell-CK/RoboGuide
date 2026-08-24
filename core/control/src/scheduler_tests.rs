use super::*;
use crate::{ControlError, ControlPlane, RoleCandidates};
use domain::{
    Capability, CapabilityKind, EventId, LocalRuntime, MissionId, NodeHealth, NodeLiveness,
    NodeLivenessObservation, NodeRegistration, NodeStateSnapshot, NodeStatus, Resource,
    ResourceKind, RoleRequirement, TaskId,
};
use ports::{SharedNodeStateWriter, SharedStateError};
use state::InMemorySharedNodeState;

/// Captures scheduler evidence without introducing transport or persistence.
#[derive(Default)]
struct TestEvents {
    /// Payloads appended by scheduling and downstream validation.
    payloads: Vec<EventPayload>,
}

impl EventSink for TestEvents {
    /// Appends one deterministic payload while ignoring generated record metadata.
    fn append(
        &mut self,
        _timestamp: TimestampMs,
        _correlation_id: &CorrelationId,
        _causation_id: Option<&EventId>,
        payload: EventPayload,
    ) {
        self.payloads.push(payload);
    }
}

/// Builds one node registration with explicit capability and resource declarations.
fn registration(
    node_id: &str,
    capability: CapabilityKind,
    resources: Vec<(&str, ResourceKind)>,
) -> NodeRegistration {
    NodeRegistration::new(
        NodeId::new(node_id).expect("test node id must be valid"),
        LocalRuntime::new("scheduler-test-runtime", "0.1.0").expect("test runtime must be valid"),
        vec![Capability::new(capability, true)],
        resources
            .into_iter()
            .map(|(resource_id, kind)| {
                Resource::new(
                    ResourceId::new(resource_id).expect("test resource id must be valid"),
                    kind,
                    1,
                )
                .expect("test resource must be valid")
            })
            .collect(),
    )
}

/// Records one healthy reachable node snapshot for scheduler-only tests.
fn record_node(
    state: &mut InMemorySharedNodeState,
    registration: NodeRegistration,
) -> Result<(), SharedStateError> {
    state.record_node(NodeStateSnapshot::new(
        registration,
        NodeStatus::new(NodeHealth::Online, TimestampMs::new(0)),
        TimestampMs::new(0),
        NodeLivenessObservation::new(NodeLiveness::Reachable, TimestampMs::new(0)),
    ))
}

/// Builds one TaskRequirement with the supplied roles in declaration order.
fn requirement(mission: &str, task: &str, roles: Vec<RoleRequirement>) -> TaskRequirement {
    TaskRequirement::new(
        MissionId::new(mission).expect("test mission id must be valid"),
        TaskId::new(task).expect("test task id must be valid"),
        roles,
    )
    .expect("test requirement must be valid")
}

/// Creates the common deterministic scheduler correlation identity.
fn correlation() -> CorrelationId {
    CorrelationId::new("scheduler-test-trace").expect("test correlation id must be valid")
}

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
    let scheduler = DeterministicBootstrapScheduler::new();
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
    let decision = DeterministicBootstrapScheduler::new()
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
    let decision = DeterministicBootstrapScheduler::new()
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
    let decision = DeterministicBootstrapScheduler::new()
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
    let decision = DeterministicBootstrapScheduler::new()
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

/// Resource reuse constraints return a typed error when no simple selection is feasible.
#[test]
fn scheduler_reports_no_feasible_selection_without_backtracking() {
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
        DeterministicBootstrapScheduler::new().schedule_task(
            &state,
            &task,
            &candidates,
            TimestampMs::new(0),
            &correlation(),
            &mut events,
        ),
        Err(SchedulerError::NoFeasibleSelection(second_role))
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
    let scheduler = DeterministicBootstrapScheduler::new();
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
    let outcome = DeterministicBootstrapScheduler::new()
        .schedule_recovery(
            &state,
            &task,
            &candidates,
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
    let scheduler = DeterministicBootstrapScheduler::new();
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
