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
        domain::NodeContractVersion::v0_1(),
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

/// Builds one node registration with explicit resource capacities.
fn registration_with_capacity(
    node_id: &str,
    capability: CapabilityKind,
    resources: Vec<(&str, ResourceKind, u32)>,
) -> NodeRegistration {
    NodeRegistration::new(
        NodeId::new(node_id).expect("test node id must be valid"),
        LocalRuntime::new("scheduler-test-runtime", "0.1.0").expect("test runtime must be valid"),
        domain::NodeContractVersion::v0_1(),
        vec![Capability::new(capability, true)],
        resources
            .into_iter()
            .map(|(resource_id, kind, capacity)| {
                Resource::new(
                    ResourceId::new(resource_id).expect("test resource id must be valid"),
                    kind,
                    capacity,
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

/// Builds one v0.2 Role with explicit quantitative resource requirements.
fn scheduled_role(
    role_id: &str,
    capability: CapabilityKind,
    resources: Vec<(ResourceKind, u32)>,
) -> RoleRequirement {
    RoleRequirement::new_scheduled(
        RoleId::new(role_id).expect("test role id must be valid"),
        None,
        capability,
        None,
        resources
            .into_iter()
            .map(|(kind, units)| {
                domain::ResourceRequirement::new(kind, units)
                    .expect("test resource requirement must be valid")
            })
            .collect(),
    )
    .expect("scheduled role must be valid")
}

/// Joint scheduling rejects Candidate Sets with duplicate or surplus Role entries.
#[test]
fn joint_scheduler_requires_exact_unique_candidate_roles() {
    let role = scheduled_role("compute", CapabilityKind::Compute, vec![]);
    let task = requirement("mission-exact", "task-exact", vec![role.clone()]);
    let node_id = NodeId::new("node-a").expect("node id valid");
    for roles in [
        vec![
            RoleCandidates::new(role.role_id().clone(), vec![node_id.clone()]),
            RoleCandidates::new(role.role_id().clone(), vec![node_id.clone()]),
        ],
        vec![
            RoleCandidates::new(role.role_id().clone(), vec![node_id.clone()]),
            RoleCandidates::new(
                RoleId::new("surplus").expect("role id valid"),
                vec![node_id.clone()],
            ),
        ],
    ] {
        let candidates = CandidateSet::new(task.task_ref().clone(), roles);
        assert!(matches!(
            BoundedJointScheduler::new().schedule_task_with_snapshot(
                &InMemorySharedNodeState::new(),
                &task,
                &candidates,
                &SchedulingSnapshot::empty(),
                TimestampMs::new(0),
                TimestampMs::new(0),
            ),
            Err(SchedulerError::InvalidCandidateSet(reason))
                if reason == "normal candidates must exactly and uniquely cover Task roles"
        ));
    }
}

/// Default construction retains the production search budget instead of disabling search.
#[test]
fn joint_scheduler_default_matches_new_policy() {
    let mut state = InMemorySharedNodeState::new();
    record_node(
        &mut state,
        registration("node-a", CapabilityKind::Compute, vec![]),
    )
    .expect("node records");
    let role = scheduled_role("compute", CapabilityKind::Compute, vec![]);
    let task = requirement("mission-default", "task-default", vec![role.clone()]);
    let candidates = CandidateSet::new(
        task.task_ref().clone(),
        vec![RoleCandidates::new(
            role.role_id().clone(),
            vec![NodeId::new("node-a").expect("node id valid")],
        )],
    );

    assert!(matches!(
        BoundedJointScheduler::default().schedule_task_with_snapshot(
            &state,
            &task,
            &candidates,
            &SchedulingSnapshot::empty(),
            TimestampMs::new(0),
            TimestampMs::new(0),
        ),
        Ok(TaskSchedulingOutcome::SelectedNow(_))
    ));
}

/// Joint search backtracks across Roles instead of accepting a greedy dead end.
#[test]
fn joint_scheduler_finds_cross_role_resource_assignment() {
    let mut state = InMemorySharedNodeState::new();
    record_node(
        &mut state,
        registration(
            "node-a",
            CapabilityKind::Compute,
            vec![("compute-a", ResourceKind::Compute)],
        ),
    )
    .expect("node-a records");
    record_node(
        &mut state,
        registration(
            "node-b",
            CapabilityKind::Compute,
            vec![("compute-b", ResourceKind::Compute)],
        ),
    )
    .expect("node-b records");
    let task = requirement(
        "mission-joint",
        "task-joint",
        vec![
            scheduled_role(
                "flexible",
                CapabilityKind::Compute,
                vec![(ResourceKind::Compute, 1)],
            ),
            scheduled_role(
                "fixed",
                CapabilityKind::Compute,
                vec![(ResourceKind::Compute, 1)],
            ),
        ],
    );
    let candidates = CandidateSet::new(
        task.task_ref().clone(),
        vec![
            RoleCandidates::new(
                RoleId::new("flexible").expect("role valid"),
                vec![
                    NodeId::new("node-a").expect("node valid"),
                    NodeId::new("node-b").expect("node valid"),
                ],
            ),
            RoleCandidates::new(
                RoleId::new("fixed").expect("role valid"),
                vec![NodeId::new("node-a").expect("node valid")],
            ),
        ],
    );

    let outcome = BoundedJointScheduler::new()
        .schedule_task_with_snapshot(
            &state,
            &task,
            &candidates,
            &SchedulingSnapshot::empty(),
            TimestampMs::new(0),
            TimestampMs::new(0),
        )
        .expect("joint search succeeds");
    let TaskSchedulingOutcome::SelectedNow(decision) = outcome else {
        panic!("task should be immediately schedulable");
    };
    assert_eq!(decision.selections()[0].node_id().as_str(), "node-b");
    assert_eq!(decision.selections()[1].node_id().as_str(), "node-a");
}

/// A busy resource moves a bounded-duration Ready Task to the first release boundary.
#[test]
fn joint_scheduler_selects_future_interval() {
    let mut state = InMemorySharedNodeState::new();
    record_node(
        &mut state,
        registration(
            "node-a",
            CapabilityKind::Compute,
            vec![("compute-a", ResourceKind::Compute)],
        ),
    )
    .expect("node records");
    let role = scheduled_role(
        "worker",
        CapabilityKind::Compute,
        vec![(ResourceKind::Compute, 1)],
    );
    let task = TaskRequirement::new_scheduled(
        MissionId::new("mission-future").expect("mission valid"),
        TaskId::new("task-future").expect("task valid"),
        vec![role.clone()],
        domain::TaskTiming::new(0, Some(20), Some(20), Some(5)).expect("timing valid"),
    )
    .expect("task valid");
    let candidates = CandidateSet::new(
        task.task_ref().clone(),
        vec![RoleCandidates::new(
            role.role_id().clone(),
            vec![NodeId::new("node-a").expect("node valid")],
        )],
    );
    let snapshot = SchedulingSnapshot::new(
        7,
        vec![SchedulingOccupancy::new(
            ResourceId::new("compute-a").expect("resource valid"),
            TimestampMs::new(0),
            Some(TimestampMs::new(10)),
        )],
    );

    let outcome = BoundedJointScheduler::new()
        .schedule_task_with_snapshot(
            &state,
            &task,
            &candidates,
            &snapshot,
            TimestampMs::new(0),
            TimestampMs::new(0),
        )
        .expect("future search succeeds");
    let TaskSchedulingOutcome::SelectedFuture(decision) = outcome else {
        panic!("busy resource should produce a future interval");
    };
    assert_eq!(decision.starts_at(), TimestampMs::new(10));
    assert_eq!(decision.ends_at(), Some(TimestampMs::new(15)));
    assert_eq!(decision.latest_activation_at(), Some(TimestampMs::new(15)));
    assert_eq!(decision.snapshot_version(), 7);
}

/// One Role can jointly require multiple exclusive resource categories and minimum capacities.
#[test]
fn joint_scheduler_selects_all_declared_resource_dimensions() {
    let mut state = InMemorySharedNodeState::new();
    record_node(
        &mut state,
        registration_with_capacity(
            "node-a",
            CapabilityKind::Compute,
            vec![
                ("compute-small", ResourceKind::Compute, 1),
                ("compute-large", ResourceKind::Compute, 4),
                ("space-a", ResourceKind::Space, 2),
            ],
        ),
    )
    .expect("node records");
    let role = scheduled_role(
        "worker",
        CapabilityKind::Compute,
        vec![(ResourceKind::Compute, 3), (ResourceKind::Space, 2)],
    );
    let task = requirement("mission-sized", "task-sized", vec![role.clone()]);
    let candidates = CandidateSet::new(
        task.task_ref().clone(),
        vec![RoleCandidates::new(
            role.role_id().clone(),
            vec![NodeId::new("node-a").expect("node valid")],
        )],
    );

    let TaskSchedulingOutcome::SelectedNow(decision) = BoundedJointScheduler::new()
        .schedule_task_with_snapshot(
            &state,
            &task,
            &candidates,
            &SchedulingSnapshot::empty(),
            TimestampMs::new(0),
            TimestampMs::new(0),
        )
        .expect("joint sizing succeeds")
    else {
        panic!("sized Task should be immediately selected");
    };
    assert_eq!(
        decision.selections()[0]
            .resource_ids()
            .iter()
            .map(ResourceId::as_str)
            .collect::<Vec<_>>(),
        vec!["compute-large", "space-a"]
    );
    assert_eq!(decision.selections()[0].resource_units(), &[3, 2]);
}

/// The configured bound is one budget for the whole joint decision, not each time candidate.
#[test]
fn joint_scheduler_reports_search_budget_exhaustion() {
    let mut state = InMemorySharedNodeState::new();
    for (node, resource) in [("node-a", "compute-a"), ("node-b", "compute-b")] {
        record_node(
            &mut state,
            registration(
                node,
                CapabilityKind::Compute,
                vec![(resource, ResourceKind::Compute)],
            ),
        )
        .expect("node records");
    }
    let flexible = scheduled_role(
        "flexible",
        CapabilityKind::Compute,
        vec![(ResourceKind::Compute, 1)],
    );
    let fixed = scheduled_role(
        "fixed",
        CapabilityKind::Compute,
        vec![(ResourceKind::Compute, 1)],
    );
    let task = requirement(
        "mission-budget",
        "task-budget",
        vec![flexible.clone(), fixed.clone()],
    );
    let candidates = CandidateSet::new(
        task.task_ref().clone(),
        vec![
            RoleCandidates::new(
                flexible.role_id().clone(),
                vec![
                    NodeId::new("node-a").expect("node valid"),
                    NodeId::new("node-b").expect("node valid"),
                ],
            ),
            RoleCandidates::new(
                fixed.role_id().clone(),
                vec![NodeId::new("node-a").expect("node valid")],
            ),
        ],
    );

    assert_eq!(
        BoundedJointScheduler::with_max_expansions(1).schedule_task_with_snapshot(
            &state,
            &task,
            &candidates,
            &SchedulingSnapshot::empty(),
            TimestampMs::new(0),
            TimestampMs::new(0),
        ),
        Err(SchedulerError::SearchLimited)
    );
}

/// Missed start windows and unbounded future occupancy remain explicit non-selections.
#[test]
fn joint_scheduler_distinguishes_window_miss_and_unbounded_future() {
    let mut state = InMemorySharedNodeState::new();
    record_node(
        &mut state,
        registration("node-a", CapabilityKind::Compute, Vec::new()),
    )
    .expect("node records");
    let role = scheduled_role("worker", CapabilityKind::Compute, Vec::new());
    let candidates_for = |task: &TaskRequirement| {
        CandidateSet::new(
            task.task_ref().clone(),
            vec![RoleCandidates::new(
                role.role_id().clone(),
                vec![NodeId::new("node-a").expect("node valid")],
            )],
        )
    };
    let missed = TaskRequirement::new_scheduled(
        MissionId::new("mission-window").expect("mission valid"),
        TaskId::new("missed").expect("task valid"),
        vec![role.clone()],
        domain::TaskTiming::new(0, Some(5), None, Some(1)).expect("timing valid"),
    )
    .expect("task valid");
    assert_eq!(
        BoundedJointScheduler::new()
            .schedule_task_with_snapshot(
                &state,
                &missed,
                &candidates_for(&missed),
                &SchedulingSnapshot::empty(),
                TimestampMs::new(0),
                TimestampMs::new(6),
            )
            .expect("window result is valid"),
        TaskSchedulingOutcome::WindowMissed
    );

    let deadline_missed = TaskRequirement::new_scheduled(
        MissionId::new("mission-window").expect("mission valid"),
        TaskId::new("deadline-missed").expect("task valid"),
        vec![role.clone()],
        domain::TaskTiming::new(0, None, Some(10), Some(5)).expect("timing valid"),
    )
    .expect("task valid");
    assert_eq!(
        BoundedJointScheduler::new()
            .schedule_task_with_snapshot(
                &state,
                &deadline_missed,
                &candidates_for(&deadline_missed),
                &SchedulingSnapshot::empty(),
                TimestampMs::new(0),
                TimestampMs::new(6),
            )
            .expect("completion deadline result is valid"),
        TaskSchedulingOutcome::WindowMissed
    );

    let unbounded = TaskRequirement::new_scheduled(
        MissionId::new("mission-window").expect("mission valid"),
        TaskId::new("unbounded").expect("task valid"),
        vec![role.clone()],
        domain::TaskTiming::new(10, None, None, None).expect("timing valid"),
    )
    .expect("task valid");
    assert_eq!(
        BoundedJointScheduler::new()
            .schedule_task_with_snapshot(
                &state,
                &unbounded,
                &candidates_for(&unbounded),
                &SchedulingSnapshot::empty(),
                TimestampMs::new(0),
                TimestampMs::new(0),
            )
            .expect("unbounded future result is valid"),
        TaskSchedulingOutcome::Deferred
    );
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
