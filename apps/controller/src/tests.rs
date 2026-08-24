use super::*;
use control::ControlError;
use domain::{EventPayload, NodeEvent, RoleAssignment, TaskRef};

/// Builds a canonical no-parameter intent for command-routing assertions.
fn test_intent(namespace: &str, name: &str) -> ExecutionIntent {
    ExecutionIntent::new(
        CapabilityContractRef::new(namespace, name, "v1").expect("test operation must be valid"),
        BTreeMap::new(),
    )
    .expect("test intent must be valid")
}

/// Builds a two-role task used to exercise concurrent mission isolation.
fn multi_mission_requirement(mission: &str, task: &str) -> TaskRequirement {
    TaskRequirement::new(
        MissionId::new(mission).expect("mission identifier should be valid"),
        TaskId::new(task).expect("task identifier should be valid"),
        vec![
            RoleRequirement::new(
                RoleId::new("transport").expect("role identifier should be valid"),
                CapabilityKind::Transport,
                Some(ResourceKind::Space),
            ),
            RoleRequirement::new(
                RoleId::new("compute").expect("role identifier should be valid"),
                CapabilityKind::Compute,
                Some(ResourceKind::Compute),
            ),
        ],
    )
    .expect("test requirement should be valid")
}

/// Extracts the mission-scoped task identity carried by a task-level event.
fn event_task_ref(payload: &EventPayload) -> Option<&TaskRef> {
    match payload {
        EventPayload::CandidatesMatched { task_ref }
        | EventPayload::TaskSchedulingSelected { task_ref, .. }
        | EventPayload::ProposalCreated { task_ref }
        | EventPayload::PlanCommitted { task_ref }
        | EventPayload::ExecutionGroupBound { task_ref, .. }
        | EventPayload::MissionActorBound { task_ref, .. }
        | EventPayload::ExecutionGroupActivated { task_ref, .. }
        | EventPayload::ReconciliationRoleRecoveryRequired { task_ref, .. }
        | EventPayload::RecoveryCandidatesMatched { task_ref, .. }
        | EventPayload::RecoverySchedulingSelected { task_ref, .. }
        | EventPayload::RecoverySchedulingNoSelection { task_ref, .. }
        | EventPayload::RecoveryAssignmentProposed { task_ref, .. }
        | EventPayload::RecoveryAssignmentCommitted { task_ref, .. }
        | EventPayload::RecoveryAssignmentAborted { task_ref, .. }
        | EventPayload::RecoveryRebound { task_ref, .. }
        | EventPayload::ExecutionGroupCompleted { task_ref, .. }
        | EventPayload::ExecutionGroupBlocked { task_ref, .. }
        | EventPayload::ExecutionGroupRoleBindingReleased { task_ref, .. }
        | EventPayload::ExecutionGroupFailed { task_ref, .. }
        | EventPayload::ExecutionGroupReleased { task_ref, .. }
        | EventPayload::NodeObservation(NodeEvent::TaskCompleted { task_ref, .. })
        | EventPayload::NodeObservation(NodeEvent::TaskFailed { task_ref, .. }) => Some(task_ref),
        EventPayload::NodeRegistered { .. }
        | EventPayload::NodeHeartbeatAccepted { .. }
        | EventPayload::NodeLeaseExpired { .. }
        | EventPayload::NodeObservation(NodeEvent::SafeStopped { .. }) => None,
    }
}

/// The first vertical slice must preserve completed work and recover by rebinding.
#[test]
fn mvp_slice_recovers_after_node_failure() {
    let events = super::run_mvp_slice().expect("deterministic MVP slice should pass");
    assert_eq!(events.len(), 25);
    assert!(matches!(
        events[0].payload(),
        EventPayload::NodeRegistered { .. }
    ));
    assert!(matches!(
        events[1].payload(),
        EventPayload::NodeRegistered { .. }
    ));
    assert!(matches!(
        events[2].payload(),
        EventPayload::NodeRegistered { .. }
    ));
    assert!(matches!(
        events[3].payload(),
        EventPayload::CandidatesMatched { .. }
    ));
    assert!(matches!(
        events[4].payload(),
        EventPayload::TaskSchedulingSelected { .. }
    ));
    assert!(matches!(
        events[5].payload(),
        EventPayload::ProposalCreated { .. }
    ));
    assert!(matches!(
        events[6].payload(),
        EventPayload::PlanCommitted { .. }
    ));
    assert!(matches!(
        events[7].payload(),
        EventPayload::ExecutionGroupBound { .. }
    ));
    assert!(matches!(
        events[8].payload(),
        EventPayload::MissionActorBound { .. }
    ));
    assert!(matches!(
        events[9].payload(),
        EventPayload::MissionActorBound { .. }
    ));
    assert!(matches!(
        events[10].payload(),
        EventPayload::ExecutionGroupActivated { .. }
    ));
    assert!(matches!(
        events[11].payload(),
        EventPayload::NodeObservation(domain::NodeEvent::TaskCompleted { .. })
    ));
    assert!(matches!(
        events[12].payload(),
        EventPayload::NodeObservation(domain::NodeEvent::TaskFailed { node_id, .. })
            if node_id.as_str() == "node-a"
    ));
    assert!(matches!(
        events[13].payload(),
        EventPayload::ReconciliationRoleRecoveryRequired { role_id, node_id, .. }
            if role_id.as_str() == "primary-transport" && node_id.as_str() == "node-a"
    ));
    assert!(matches!(
        events[14].payload(),
        EventPayload::ExecutionGroupBlocked { .. }
    ));
    assert!(matches!(
        events[15].payload(),
        EventPayload::ExecutionGroupRoleBindingReleased { role_id, .. }
            if role_id.as_str() == "primary-transport"
    ));
    assert!(matches!(
        events[16].payload(),
        EventPayload::RecoveryCandidatesMatched { candidate_node_ids, .. }
            if candidate_node_ids.iter().any(|node_id| node_id.as_str() == "node-b")
    ));
    assert!(matches!(
        events[17].payload(),
        EventPayload::RecoverySchedulingSelected { replacement_node_id, .. }
            if replacement_node_id.as_str() == "node-b"
    ));
    assert!(matches!(
        events[18].payload(),
        EventPayload::RecoveryAssignmentProposed { replacement_node_id, .. }
            if replacement_node_id.as_str() == "node-b"
    ));
    assert!(matches!(
        events[19].payload(),
        EventPayload::RecoveryAssignmentCommitted { replacement_node_id, .. }
            if replacement_node_id.as_str() == "node-b"
    ));
    assert!(matches!(
        events[20].payload(),
        EventPayload::RecoveryRebound { from_node, to_node, .. }
            if from_node.as_str() == "node-a" && to_node.as_str() == "node-b"
    ));
    assert!(matches!(
        events[21].payload(),
        EventPayload::ExecutionGroupActivated { .. }
    ));
    assert!(matches!(
        events[22].payload(),
        EventPayload::NodeObservation(domain::NodeEvent::TaskCompleted { node_id, .. })
            if node_id.as_str() == "node-b"
    ));
    assert!(matches!(
        events[23].payload(),
        EventPayload::ExecutionGroupCompleted { .. }
    ));
    assert!(matches!(
        events[24].payload(),
        EventPayload::ExecutionGroupReleased { .. }
    ));
}

/// Runtime health ingestion immediately changes the next Control decision.
#[test]
fn runtime_health_observation_changes_control_matching() {
    let timestamp = TimestampMs::new(0);
    let observed_offline_at = TimestampMs::new(10);
    let correlation_id =
        CorrelationId::new("runtime-state-trace").expect("correlation id should be valid");
    let node = build_registration(
        "node-observed",
        "vendor-runtime",
        vec![Capability::new(CapabilityKind::Transport, true)],
        vec![
            Resource::new(
                ResourceId::new("space-observed").expect("resource id should be valid"),
                ResourceKind::Space,
                1,
            )
            .expect("resource should be valid"),
        ],
    )
    .expect("node registration should be valid");
    let node_id = node.node_id().clone();
    let requirement = TaskRequirement::new(
        MissionId::new("mission-observation").expect("mission id should be valid"),
        TaskId::new("task-01").expect("task id should be valid"),
        vec![RoleRequirement::new(
            RoleId::new("transport").expect("role id should be valid"),
            CapabilityKind::Transport,
            Some(ResourceKind::Space),
        )],
    )
    .expect("task requirement should be valid");
    let mut control = ControlPlane::new();
    let mut state = InMemorySharedNodeState::new();
    let mut log = SharedEventLog::new();
    control
        .register_node(
            &mut state,
            node.clone(),
            NodeStatus::new(NodeHealth::Online, timestamp),
            timestamp,
            &correlation_id,
            &mut log,
        )
        .expect("node admission should succeed");
    control
        .match_capabilities(&state, &requirement, timestamp, &correlation_id, &mut log)
        .expect("initial online observation should be eligible");

    let mut runtime = Runtime::new(VirtualClock::new(observed_offline_at), log.clone());
    runtime
        .register_node(Box::new(FakeNode::new(node).with_status(NodeStatus::new(
            NodeHealth::Offline,
            observed_offline_at,
        ))))
        .expect("fake EAIOS adapter registration should succeed");
    runtime
        .observe_node_status(&node_id, &mut state)
        .expect("Runtime should ingest local health");

    assert!(matches!(
        control.match_capabilities(
            &state,
            &requirement,
            observed_offline_at,
            &correlation_id,
            &mut log,
        ),
        Err(ControlError::NoCandidate(role_id)) if role_id.as_str() == "transport"
    ));
}

/// Independent source clock values do not affect Control receive-time freshness.
#[test]
fn runtime_source_clock_does_not_affect_control_freshness() {
    let admitted_at = TimestampMs::new(0);
    let runtime_received_at = TimestampMs::new(10);
    let correlation_id =
        CorrelationId::new("clock-domain-trace").expect("correlation id should be valid");
    let node = build_registration(
        "node-clock-domain",
        "vendor-runtime",
        vec![Capability::new(CapabilityKind::Transport, true)],
        vec![
            Resource::new(
                ResourceId::new("space-clock-domain").expect("resource id should be valid"),
                ResourceKind::Space,
                1,
            )
            .expect("resource should be valid"),
        ],
    )
    .expect("node registration should be valid");
    let node_id = node.node_id().clone();
    let requirement = TaskRequirement::new(
        MissionId::new("mission-clock-domain").expect("mission id should be valid"),
        TaskId::new("task-01").expect("task id should be valid"),
        vec![RoleRequirement::new(
            RoleId::new("transport").expect("role id should be valid"),
            CapabilityKind::Transport,
            Some(ResourceKind::Space),
        )],
    )
    .expect("task requirement should be valid");
    let mut control = ControlPlane::with_status_ttl(20);
    let mut state = InMemorySharedNodeState::new();
    let mut log = SharedEventLog::new();
    control
        .register_node(
            &mut state,
            node.clone(),
            NodeStatus::new(NodeHealth::Online, TimestampMs::new(1)),
            admitted_at,
            &correlation_id,
            &mut log,
        )
        .expect("node admission should succeed");
    let mut runtime = Runtime::new(VirtualClock::new(runtime_received_at), log.clone());
    runtime
        .register_node(Box::new(FakeNode::new(node).with_status(NodeStatus::new(
            NodeHealth::Online,
            TimestampMs::new(500_000),
        ))))
        .expect("fake EAIOS adapter registration should succeed");
    runtime
        .observe_node_status(&node_id, &mut state)
        .expect("Runtime should record source and receive times separately");

    control
        .match_capabilities(
            &state,
            &requirement,
            TimestampMs::new(20),
            &correlation_id,
            &mut log,
        )
        .expect("receive time age 10 should remain eligible");
}

/// Concurrent missions must isolate recovery, lifecycle, resources, and traces.
#[test]
fn concurrent_missions_rebind_and_release_independently() {
    let started_at = TimestampMs::new(0);
    let setup_trace =
        CorrelationId::new("trace-setup").expect("correlation identifier should be valid");
    let trace_a =
        CorrelationId::new("trace-mission-a").expect("correlation identifier should be valid");
    let trace_b =
        CorrelationId::new("trace-mission-b").expect("correlation identifier should be valid");
    let trace_c =
        CorrelationId::new("trace-mission-c").expect("correlation identifier should be valid");
    let group_a = ExecutionGroupId::new("group-a").expect("group identifier should be valid");
    let group_b = ExecutionGroupId::new("group-b").expect("group identifier should be valid");
    let requirement_a = multi_mission_requirement("mission-a", "task-01");
    let requirement_b = multi_mission_requirement("mission-b", "task-01");
    let task_ref_a = requirement_a.task_ref().clone();
    let task_ref_b = requirement_b.task_ref().clone();
    let transport_role = RoleId::new("transport").expect("role identifier should be valid");
    let compute_role = RoleId::new("compute").expect("role identifier should be valid");

    let space_a = ResourceId::new("space-a").expect("resource identifier should be valid");
    let space_b = ResourceId::new("space-b").expect("resource identifier should be valid");
    let space_d = ResourceId::new("space-d").expect("resource identifier should be valid");
    let compute_c = ResourceId::new("compute-c").expect("resource identifier should be valid");
    let compute_e = ResourceId::new("compute-e").expect("resource identifier should be valid");

    let node_a = build_registration(
        "node-a",
        "vendor-runtime-a",
        vec![Capability::new(CapabilityKind::Transport, true)],
        vec![
            Resource::new(space_a.clone(), ResourceKind::Space, 1)
                .expect("resource should be valid"),
        ],
    )
    .expect("node registration should be valid");
    let node_b = build_registration(
        "node-b",
        "vendor-runtime-b",
        vec![Capability::new(CapabilityKind::Transport, true)],
        vec![
            Resource::new(space_b.clone(), ResourceKind::Space, 1)
                .expect("resource should be valid"),
        ],
    )
    .expect("node registration should be valid");
    let node_d = build_registration(
        "node-d",
        "vendor-runtime-d",
        vec![Capability::new(CapabilityKind::Transport, true)],
        vec![
            Resource::new(space_d.clone(), ResourceKind::Space, 1)
                .expect("resource should be valid"),
        ],
    )
    .expect("node registration should be valid");
    let edge_c = build_registration(
        "edge-c",
        "vendor-runtime-c",
        vec![Capability::new(CapabilityKind::Compute, true)],
        vec![
            Resource::new(compute_c.clone(), ResourceKind::Compute, 1)
                .expect("resource should be valid"),
        ],
    )
    .expect("node registration should be valid");
    let edge_e = build_registration(
        "edge-e",
        "vendor-runtime-e",
        vec![Capability::new(CapabilityKind::Compute, true)],
        vec![
            Resource::new(compute_e.clone(), ResourceKind::Compute, 1)
                .expect("resource should be valid"),
        ],
    )
    .expect("node registration should be valid");

    let mut control = ControlPlane::new();
    let scheduler = DeterministicBootstrapScheduler::new();
    let mut state = InMemorySharedNodeState::new();
    let mut log = SharedEventLog::new();
    for registration in [&node_a, &node_b, &node_d, &edge_c, &edge_e] {
        control
            .register_node(
                &mut state,
                registration.clone(),
                NodeStatus::new(NodeHealth::Online, started_at),
                started_at,
                &setup_trace,
                &mut log,
            )
            .expect("node registration should succeed");
    }

    let candidates_a = control
        .match_capabilities(&state, &requirement_a, started_at, &trace_a, &mut log)
        .expect("Mission A matching should succeed");
    let proposal_a = control
        .propose(
            &state,
            &requirement_a,
            &candidates_a,
            vec![
                RoleAssignment::new(
                    transport_role.clone(),
                    node_a.node_id().clone(),
                    vec![space_a],
                ),
                RoleAssignment::new(
                    compute_role.clone(),
                    edge_c.node_id().clone(),
                    vec![compute_c.clone()],
                ),
            ],
            started_at,
            &trace_a,
            &mut log,
        )
        .expect("Mission A proposal should succeed");
    let plan_a = control
        .commit(&proposal_a, started_at, &trace_a, &mut log)
        .expect("Mission A commit should succeed");
    control
        .create_group(group_a.clone(), &plan_a, started_at, &trace_a, &mut log)
        .expect("Mission A group creation should succeed");
    control
        .activate_group(&group_a, started_at, &trace_a, &mut log)
        .expect("Mission A activation should succeed");

    let candidates_b = control
        .match_capabilities(&state, &requirement_b, started_at, &trace_b, &mut log)
        .expect("Mission B matching should succeed");
    let proposal_b = control
        .propose(
            &state,
            &requirement_b,
            &candidates_b,
            vec![
                RoleAssignment::new(
                    transport_role.clone(),
                    node_d.node_id().clone(),
                    vec![space_d],
                ),
                RoleAssignment::new(
                    compute_role.clone(),
                    edge_e.node_id().clone(),
                    vec![compute_e],
                ),
            ],
            started_at,
            &trace_b,
            &mut log,
        )
        .expect("Mission B proposal should succeed");
    let plan_b = control
        .commit(&proposal_b, started_at, &trace_b, &mut log)
        .expect("Mission B commit should succeed");
    control
        .create_group(group_b.clone(), &plan_b, started_at, &trace_b, &mut log)
        .expect("Mission B group creation should succeed");
    control
        .activate_group(&group_b, started_at, &trace_b, &mut log)
        .expect("Mission B activation should succeed");
    let group_b_bindings = control
        .group(&group_b)
        .expect("Mission B group should exist")
        .assignments()
        .to_vec();

    let mut runtime = Runtime::new(VirtualClock::new(started_at), log.clone());
    runtime
        .register_node(Box::new(FakeNode::new(node_a.clone()).with_failure_mode(
            FailureMode::FailNextAndReportStatus {
                reason: "transport unavailable".to_string(),
                status: NodeStatus::new(NodeHealth::Offline, TimestampMs::new(1)),
            },
        )))
        .expect("Node A runtime registration should succeed");
    for registration in [
        node_b.clone(),
        node_d.clone(),
        edge_c.clone(),
        edge_e.clone(),
    ] {
        runtime
            .register_node(Box::new(FakeNode::new(registration)))
            .expect("runtime registration should succeed");
    }

    runtime
        .execute(&ExecutionCommand::new(
            requirement_a.mission_id().clone(),
            requirement_a.task_id().clone(),
            group_a.clone(),
            compute_role.clone(),
            edge_c.node_id().clone(),
            test_intent("compute", "infer"),
            trace_a.clone(),
        ))
        .expect("Mission A compute should complete");
    runtime
        .execute(&ExecutionCommand::new(
            requirement_b.mission_id().clone(),
            requirement_b.task_id().clone(),
            group_b.clone(),
            compute_role.clone(),
            edge_e.node_id().clone(),
            test_intent("compute", "infer"),
            trace_b.clone(),
        ))
        .expect("Mission B compute should complete");
    let failure = runtime
        .execute(&ExecutionCommand::new(
            requirement_a.mission_id().clone(),
            requirement_a.task_id().clone(),
            group_a.clone(),
            transport_role.clone(),
            node_a.node_id().clone(),
            test_intent("mobility", "move"),
            trace_a.clone(),
        ))
        .expect("failure injection should return an observation");
    assert!(
        matches!(failure, NodeEvent::TaskFailed { ref task_ref, .. } if task_ref == &task_ref_a)
    );

    runtime
        .observe_node_status(node_a.node_id(), &mut state)
        .expect("Runtime should ingest Node A unavailability");
    let assessment_a = control
        .assess_group(
            &state,
            &group_a,
            &requirement_a,
            TimestampMs::new(1),
            &trace_a,
            &mut log,
        )
        .expect("Mission A reconciliation assessment should succeed");
    let ReconciliationAssessment::RoleRecoveryRequired(need) = assessment_a else {
        panic!("Mission A should require transport recovery");
    };
    let assessment_b = control
        .assess_group(
            &state,
            &group_b,
            &requirement_b,
            TimestampMs::new(1),
            &trace_b,
            &mut log,
        )
        .expect("Mission B reconciliation assessment should succeed");
    assert_eq!(assessment_b, ReconciliationAssessment::NoAction);
    control
        .begin_role_recovery(&need, TimestampMs::new(1), &trace_a, &mut log)
        .expect("Mission A should begin only transport recovery");
    let recovery_candidates = control
        .match_recovery_candidates(
            &state,
            &need,
            &requirement_a,
            TimestampMs::new(1),
            &trace_a,
            &mut log,
        )
        .expect("Mission A transport recovery matching should succeed");
    let recovery_scheduling = scheduler
        .schedule_recovery(
            &state,
            &requirement_a,
            &recovery_candidates,
            TimestampMs::new(1),
            &trace_a,
            &mut log,
        )
        .expect("bootstrap Scheduler should evaluate recovery candidates");
    let RecoverySchedulingOutcome::Selected(recovery_decision) = recovery_scheduling else {
        panic!("Mission A should have a deterministic replacement selection");
    };
    let replacement_node_id = recovery_decision.replacement_node_id().clone();
    let recovery_proposal = control
        .propose_role_recovery(
            &state,
            &recovery_candidates,
            &requirement_a,
            replacement_node_id.clone(),
            recovery_decision.resource_ids().to_vec(),
            TimestampMs::new(1),
            &trace_a,
            &mut log,
        )
        .expect("bootstrap scheduler should propose Node B");
    let committed_recovery = control
        .commit_role_recovery(
            &state,
            &requirement_a,
            &recovery_proposal,
            TimestampMs::new(1),
            &trace_a,
            &mut log,
        )
        .expect("Mission A replacement resources should commit");
    control
        .rebind_role(&committed_recovery, TimestampMs::new(1), &trace_a, &mut log)
        .expect("Mission A committed replacement should rebind");
    assert_eq!(
        control
            .group(&group_a)
            .expect("Mission A group should exist")
            .lifecycle(),
        GroupLifecycle::Adapted
    );
    control
        .activate_group(&group_a, TimestampMs::new(1), &trace_a, &mut log)
        .expect("Mission A recovered group should reactivate");
    assert_eq!(
        control
            .group(&group_a)
            .expect("Mission A group should exist")
            .lifecycle(),
        GroupLifecycle::Active
    );
    assert_eq!(
        control
            .group(&group_b)
            .expect("Mission B group should exist")
            .lifecycle(),
        GroupLifecycle::Active
    );
    assert_eq!(
        control
            .group(&group_b)
            .expect("Mission B group should exist")
            .assignments(),
        group_b_bindings.as_slice()
    );

    runtime
        .execute(&ExecutionCommand::new(
            requirement_a.mission_id().clone(),
            requirement_a.task_id().clone(),
            group_a.clone(),
            transport_role.clone(),
            replacement_node_id,
            test_intent("mobility", "move"),
            trace_a.clone(),
        ))
        .expect("Mission A replacement transport should complete");
    runtime
        .execute(&ExecutionCommand::new(
            requirement_b.mission_id().clone(),
            requirement_b.task_id().clone(),
            group_b.clone(),
            transport_role.clone(),
            node_d.node_id().clone(),
            test_intent("mobility", "move"),
            trace_b.clone(),
        ))
        .expect("Mission B transport should complete");
    control
        .complete_group(&group_a, TimestampMs::new(2), &trace_a, &mut log)
        .expect("Mission A should complete");
    control
        .complete_group(&group_b, TimestampMs::new(2), &trace_b, &mut log)
        .expect("Mission B should complete");
    control
        .release_group(&group_a, TimestampMs::new(3), &trace_a, &mut log)
        .expect("Mission A should release resources");
    control
        .release_group(&group_b, TimestampMs::new(3), &trace_b, &mut log)
        .expect("Mission B should release resources");
    assert_eq!(
        control
            .group(&group_a)
            .expect("Mission A group should exist")
            .lifecycle(),
        GroupLifecycle::Released
    );
    assert_eq!(
        control
            .group(&group_b)
            .expect("Mission B group should exist")
            .lifecycle(),
        GroupLifecycle::Released
    );

    let requirement_c = multi_mission_requirement("mission-c", "task-02");
    let candidates_c = control
        .match_capabilities(
            &state,
            &requirement_c,
            TimestampMs::new(4),
            &trace_c,
            &mut log,
        )
        .expect("Mission C matching should succeed");
    let proposal_c = control
        .propose(
            &state,
            &requirement_c,
            &candidates_c,
            vec![
                RoleAssignment::new(
                    transport_role,
                    node_b.node_id().clone(),
                    vec![space_b.clone()],
                ),
                RoleAssignment::new(
                    compute_role,
                    edge_c.node_id().clone(),
                    vec![compute_c.clone()],
                ),
            ],
            TimestampMs::new(4),
            &trace_c,
            &mut log,
        )
        .expect("Mission C proposal should reuse released resources");
    control
        .commit(&proposal_c, TimestampMs::new(4), &trace_c, &mut log)
        .expect("Mission C commit should reserve released resources");

    let events = log.snapshot();
    for event in &events {
        match event_task_ref(event.payload()) {
            Some(task_ref) if task_ref == &task_ref_a => {
                assert_eq!(event.correlation_id(), &trace_a);
            }
            Some(task_ref) if task_ref == &task_ref_b => {
                assert_eq!(event.correlation_id(), &trace_b);
            }
            _ => {}
        }
    }
    for task_ref in [&task_ref_a, &task_ref_b] {
        assert!(events.iter().any(|event| matches!(
            event.payload(),
            EventPayload::CandidatesMatched { task_ref: event_task_ref }
                if event_task_ref == task_ref
        )));
        assert!(events.iter().any(|event| matches!(
            event.payload(),
            EventPayload::ProposalCreated { task_ref: event_task_ref }
                if event_task_ref == task_ref
        )));
        assert!(events.iter().any(|event| matches!(
            event.payload(),
            EventPayload::PlanCommitted { task_ref: event_task_ref }
                if event_task_ref == task_ref
        )));
        assert!(events.iter().any(|event| matches!(
            event.payload(),
            EventPayload::ExecutionGroupBound { task_ref: event_task_ref, .. }
                if event_task_ref == task_ref
        )));
    }
    let recovery_events = events
        .iter()
        .filter(|event| matches!(event.payload(), EventPayload::RecoveryRebound { .. }))
        .collect::<Vec<_>>();
    assert_eq!(recovery_events.len(), 1);
    assert!(matches!(
        recovery_events[0].payload(),
        EventPayload::RecoveryRebound { group_id, task_ref, .. }
            if group_id == &group_a && task_ref == &task_ref_a
    ));
    assert!(events.iter().any(|event| matches!(
        event.payload(),
        EventPayload::ExecutionGroupCompleted { group_id, task_ref }
            if group_id == &group_a && task_ref == &task_ref_a
    )));
    assert!(events.iter().any(|event| matches!(
        event.payload(),
        EventPayload::ExecutionGroupCompleted { group_id, task_ref }
            if group_id == &group_b && task_ref == &task_ref_b
    )));
    assert!(events.iter().any(|event| matches!(
        event.payload(),
        EventPayload::ExecutionGroupReleased { group_id, task_ref, resource_ids }
            if group_id == &group_a && task_ref == &task_ref_a
                && resource_ids.contains(&space_b) && resource_ids.contains(&compute_c)
    )));
    assert!(events.iter().any(|event| matches!(
        event.payload(),
        EventPayload::ExecutionGroupReleased { group_id, task_ref, .. }
            if group_id == &group_b && task_ref == &task_ref_b
    )));
}
