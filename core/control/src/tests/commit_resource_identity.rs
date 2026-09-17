/// Builds a Node with a compute resource whose declaration can change before commitment.
fn resource_race_node(resources: Vec<Resource>) -> NodeRegistration {
    NodeRegistration::new_with_contracts(
        NodeId::new("resource-node").unwrap(),
        domain::LocalRuntime::new("test", "1").unwrap(),
        domain::NodeContractVersion::v0_1(),
        vec![Capability::new(CapabilityKind::Compute, true)],
        vec![CapabilityContractRef::new("compute", "work", "v1").unwrap()],
        resources,
    )
}

/// Rejects stale selected resources atomically even when equivalent current resources exist.
fn assert_resource_commit_rejected(updated_resources: Vec<Resource>) {
    let mut control = ControlPlane::new();
    let mut state = InMemorySharedNodeState::new();
    let mut events = RecordingEvents::default();
    let now = TimestampMs::new(0);
    let correlation = CorrelationId::new("resource-commit-race").unwrap();
    let old = Resource::new(
        ResourceId::new("resource-old").unwrap(),
        ResourceKind::Compute,
        2,
    )
    .unwrap();
    let stable = Resource::new(
        ResourceId::new("resource-stable").unwrap(),
        ResourceKind::Space,
        1,
    )
    .unwrap();
    control
        .register_node(
            &mut state,
            resource_race_node(vec![stable.clone(), old]),
            NodeStatus::new(NodeHealth::Online, now),
            now,
            &correlation,
            &mut events,
        )
        .unwrap();
    let mission_id = MissionId::new("resource-race").unwrap();
    let role = RoleId::new("worker").unwrap();
    let contract = CapabilityContractRef::new("compute", "work", "v1").unwrap();
    let requirement = TaskRequirement::new(
        mission_id.clone(),
        TaskId::new("task").unwrap(),
        vec![
            RoleRequirement::new_scheduled(
                role.clone(),
                None,
                CapabilityKind::Compute,
                Some(contract.clone()),
                vec![
                    ResourceRequirement::new(ResourceKind::Space, 1).unwrap(),
                    ResourceRequirement::new(ResourceKind::Compute, 2).unwrap(),
                ],
            )
            .unwrap(),
        ],
    )
    .unwrap();
    let context = domain::CoordinationContextId::new("context").unwrap();
    let task = PlannedTask::new(
        "work",
        requirement.clone(),
        BTreeMap::from([(
            role,
            ExecutionIntent::new(contract, BTreeMap::new()).unwrap(),
        )]),
        Vec::new(),
        domain::TaskContinuity::new(context.clone(), BTreeMap::new(), BTreeMap::new()),
    )
    .unwrap();
    let plan = MissionPlan::new(
        MissionGoal::new(mission_id.clone(), "work").unwrap(),
        TaskGraph::new(mission_id, vec![task]).unwrap(),
        vec![domain::CoordinationContext::new(context, Vec::new()).unwrap()],
    )
    .unwrap();
    let group = ExecutionGroupId::new("resource-group").unwrap();
    create_ready_physical_group(&mut control, &plan, &requirement, &group, &mut events);
    let candidates = control
        .match_capabilities_for_mission(&state, &plan, &requirement, now, &correlation, &mut events)
        .unwrap();
    let decision = BoundedJointScheduler::new()
        .schedule_task(
            &state,
            &requirement,
            &candidates,
            now,
            &correlation,
            &mut events,
        )
        .unwrap();
    let proposal = control
        .propose(
            &state,
            &requirement,
            &candidates,
            decision.proposed_assignments(),
            now,
            &correlation,
            &mut events,
        )
        .unwrap();
    let mut resources = vec![stable];
    resources.extend(updated_resources);
    control
        .update_node_registration(
            &mut state,
            resource_race_node(resources),
            now,
            &correlation,
            &mut events,
        )
        .unwrap();
    for group_commit in [false, true] {
        let before = events.records.len();
        let result = if group_commit {
            control.commit_for_group_with_state(
                &state,
                &group,
                &proposal,
                now,
                &correlation,
                &mut events,
            )
        } else {
            control.commit_with_state(&state, &proposal, now, &correlation, &mut events)
        };
        assert!(
            matches!(result, Err(ControlError::AssignmentUnavailable(_))),
            "{result:?}"
        );
        assert!(
            control.reservations.is_empty(),
            "even the earlier stable resource must remain unreserved"
        );
        assert_eq!(
            events.records.len(),
            before,
            "rejection cannot emit PlanCommitted"
        );
        assert!(
            control
                .group(&group)
                .unwrap()
                .task_execution(requirement.task_ref())
                .unwrap()
                .assignments()
                .is_empty()
        );
    }
}

/// Removing the chosen resource invalidates a previously valid Proposal.
#[test]
fn commit_rejects_removed_selected_resource() {
    assert_resource_commit_rejected(Vec::new());
}

/// An equivalent replacement ID cannot satisfy a commitment for the withdrawn ID.
#[test]
fn commit_rejects_equivalent_replacement_resource_id() {
    assert_resource_commit_rejected(vec![
        Resource::new(
            ResourceId::new("resource-new").unwrap(),
            ResourceKind::Compute,
            2,
        )
        .unwrap(),
    ]);
}

/// Keeping another sufficient resource cannot hide insufficient selected capacity.
#[test]
fn commit_rejects_reduced_selected_resource_capacity() {
    assert_resource_commit_rejected(vec![
        Resource::new(
            ResourceId::new("resource-old").unwrap(),
            ResourceKind::Compute,
            1,
        )
        .unwrap(),
        Resource::new(
            ResourceId::new("resource-new").unwrap(),
            ResourceKind::Compute,
            2,
        )
        .unwrap(),
    ]);
}

/// Keeping another resource of the original kind cannot hide a selected kind change.
#[test]
fn commit_rejects_changed_selected_resource_kind() {
    assert_resource_commit_rejected(vec![
        Resource::new(
            ResourceId::new("resource-old").unwrap(),
            ResourceKind::Space,
            2,
        )
        .unwrap(),
        Resource::new(
            ResourceId::new("resource-new").unwrap(),
            ResourceKind::Compute,
            2,
        )
        .unwrap(),
    ]);
}

/// State-free compatibility Commit cannot bypass concrete resource revalidation.
#[test]
fn resource_proposal_requires_current_state_even_without_operation_metadata() {
    let mut fixture = recovery_fixture(true);
    let requirement = TaskRequirement::new(
        MissionId::new("other").unwrap(),
        TaskId::new("task").unwrap(),
        vec![RoleRequirement::new(
            RoleId::new("role").unwrap(),
            CapabilityKind::Transport,
            Some(ResourceKind::Space),
        )],
    )
    .unwrap();
    let candidates = fixture
        .control
        .match_capabilities(
            &fixture.state,
            &requirement,
            TimestampMs::new(0),
            &fixture.correlation_id,
            &mut fixture.events,
        )
        .unwrap();
    let proposal = fixture
        .control
        .propose(
            &fixture.state,
            &requirement,
            &candidates,
            vec![RoleAssignment::new(
                RoleId::new("role").unwrap(),
                fixture.node_b_id.clone(),
                vec![fixture.space_b.clone()],
            )],
            TimestampMs::new(0),
            &fixture.correlation_id,
            &mut fixture.events,
        )
        .unwrap();
    assert!(matches!(
        fixture.control.commit(
            &proposal,
            TimestampMs::new(0),
            &fixture.correlation_id,
            &mut fixture.events
        ),
        Err(ControlError::InvalidProposal(_))
    ));
    assert!(matches!(
        fixture.control.commit_for_group(
            &fixture.group_id,
            &proposal,
            TimestampMs::new(0),
            &fixture.correlation_id,
            &mut fixture.events
        ),
        Err(ControlError::InvalidProposal(_))
    ));
}
