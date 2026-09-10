use super::*;

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
    let mission_plan_a = single_task_mission_plan(requirement_a.clone());
    let mission_plan_b = single_task_mission_plan(requirement_b.clone());
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
    let scheduler = BoundedJointScheduler::new();
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
    for (group_id, mission_plan, requirement, trace) in [
        (&group_a, &mission_plan_a, &requirement_a, &trace_a),
        (&group_b, &mission_plan_b, &requirement_b, &trace_b),
    ] {
        control
            .create_mission_group(group_id.clone(), mission_plan, started_at, trace, &mut log)
            .expect("Mission Group creation should succeed");
        control
            .ready_task_execution(
                group_id,
                requirement.task_ref(),
                started_at,
                trace,
                &mut log,
            )
            .expect("initial Task should become ready");
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
        .bind_task_execution_with_requirement(
            &group_a,
            &plan_a,
            &requirement_a,
            started_at,
            &trace_a,
            &mut log,
        )
        .expect("Mission A Task bind should succeed");
    control
        .activate_task_execution(
            &group_a,
            requirement_a.task_ref(),
            started_at,
            &trace_a,
            &mut log,
        )
        .expect("Mission A Task activation should succeed");

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
        .bind_task_execution_with_requirement(
            &group_b,
            &plan_b,
            &requirement_b,
            started_at,
            &trace_b,
            &mut log,
        )
        .expect("Mission B Task bind should succeed");
    control
        .activate_task_execution(
            &group_b,
            requirement_b.task_ref(),
            started_at,
            &trace_b,
            &mut log,
        )
        .expect("Mission B Task activation should succeed");
    let group_b_bindings = control
        .group(&group_b)
        .expect("Mission B group should exist")
        .task_execution(requirement_b.task_ref())
        .expect("Mission B Task should exist")
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
    let scheduling_snapshot = control.scheduling_snapshot(TimestampMs::new(1));
    let recovery_scheduling = scheduler
        .schedule_recovery(
            &state,
            &requirement_a,
            &recovery_candidates,
            &scheduling_snapshot,
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
            .task_execution(requirement_b.task_ref())
            .expect("Mission B Task should exist")
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
    for (group_id, task_ref, trace) in [
        (&group_a, &task_ref_a, &trace_a),
        (&group_b, &task_ref_b, &trace_b),
    ] {
        let task_resources = control
            .group(group_id)
            .and_then(|group| group.task_execution(task_ref))
            .expect("TaskExecution should remain in its Mission Group")
            .assignments()
            .iter()
            .flat_map(|assignment| assignment.resource_ids())
            .filter(|resource_id| {
                control
                    .group(group_id)
                    .and_then(|group| group.task_execution(task_ref))
                    .is_some_and(|execution| {
                        execution.binding_scope(resource_id) == domain::ResourceBindingScope::Task
                    })
            })
            .cloned()
            .collect::<Vec<_>>();
        control
            .complete_task_execution(group_id, task_ref, TimestampMs::new(2), trace, &mut log)
            .expect("Task should complete before its Mission Group");
        control
            .release_task_bindings(
                group_id,
                task_ref,
                &task_resources,
                TimestampMs::new(2),
                trace,
                &mut log,
            )
            .expect("Task-scoped bindings should release after Task completion");
    }
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
        EventPayload::TaskExecutionBindingsReleased { group_id, task_ref, resource_ids }
            if group_id == &group_a && task_ref == &task_ref_a
                && resource_ids.contains(&space_b) && resource_ids.contains(&compute_c)
    )));
    assert!(events.iter().any(|event| matches!(
        event.payload(),
        EventPayload::ExecutionGroupReleased { group_id, task_ref, resource_ids }
            if group_id == &group_a && task_ref == &task_ref_a && resource_ids.is_empty()
    )));
    assert!(events.iter().any(|event| matches!(
        event.payload(),
        EventPayload::ExecutionGroupReleased { group_id, task_ref, .. }
            if group_id == &group_b && task_ref == &task_ref_b
    )));
}
