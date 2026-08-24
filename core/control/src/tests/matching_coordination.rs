    /// A schedulable node can produce a proposal and a committed plan.
    #[test]
    fn normal_path_matches_proposes_and_commits() {
        let node = registration("node-a", CapabilityKind::Transport, "space-a");
        let node_id = node.node_id().clone();
        let resource_id = ResourceId::new("space-a").expect("test resource id must be valid");
        let role_id = RoleId::new("transport").expect("test role id must be valid");
        let task = requirement("task-normal", "transport", CapabilityKind::Transport);
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
        let stored = state
            .node(&node_id)
            .expect("registration should be readable from Shared State");
        assert_eq!(stored.registration().capabilities().len(), 1);
        assert_eq!(stored.registration().resources().len(), 1);
        assert_eq!(stored.reported_status().health(), NodeHealth::Online);
        let candidates = control
            .match_capabilities(&state, &task, timestamp, &correlation_id, &mut events)
            .expect("online node should match");
        assert_eq!(
            candidates
                .for_role(&role_id)
                .expect("role candidates should exist")
                .node_ids(),
            std::slice::from_ref(&node_id)
        );

        let proposal = control
            .propose(
                &state,
                &task,
                &candidates,
                vec![RoleAssignment::new(role_id, node_id, vec![resource_id])],
                timestamp,
                &correlation_id,
                &mut events,
            )
            .expect("candidate assignment should produce a proposal");
        let plan = control
            .commit(&proposal, timestamp, &correlation_id, &mut events)
            .expect("unreserved resource should commit");
        assert_eq!(plan.assignments().len(), 1);
    }

    /// Matching reads heterogeneous node capability facts from Shared State.
    #[test]
    fn matching_reads_shared_state_capability_facts() {
        let node_a = registration_with_resource_kind(
            "node-a",
            CapabilityKind::Transport,
            "space-a",
            ResourceKind::Space,
        );
        let node_b = registration_with_resource_kind(
            "node-b",
            CapabilityKind::Compute,
            "compute-b",
            ResourceKind::Compute,
        );
        let requirement = TaskRequirement::new(
            domain::MissionId::new("mission-shared-state")
                .expect("test mission id should be valid"),
            TaskId::new("task-01").expect("test task id should be valid"),
            vec![
                RoleRequirement::new(
                    RoleId::new("transport").expect("test role id should be valid"),
                    CapabilityKind::Transport,
                    Some(ResourceKind::Space),
                ),
                RoleRequirement::new(
                    RoleId::new("compute").expect("test role id should be valid"),
                    CapabilityKind::Compute,
                    Some(ResourceKind::Compute),
                ),
            ],
        )
        .expect("test requirement should be valid");
        let timestamp = TimestampMs::new(0);
        let correlation_id = correlation();
        let mut control = ControlPlane::new();
        let mut state = InMemorySharedNodeState::new();
        let mut events = TestEvents;
        for node in [node_a, node_b] {
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
        }

        let candidates = control
            .match_capabilities(
                &state,
                &requirement,
                timestamp,
                &correlation_id,
                &mut events,
            )
            .expect("heterogeneous state facts should satisfy both roles");
        assert_eq!(
            candidates
                .for_role(&RoleId::new("transport").expect("test role id should be valid"))
                .expect("transport candidates should exist")
                .node_ids()[0]
                .as_str(),
            "node-a"
        );
        assert_eq!(
            candidates
                .for_role(&RoleId::new("compute").expect("test role id should be valid"))
                .expect("compute candidates should exist")
                .node_ids()[0]
                .as_str(),
            "node-b"
        );
    }

    /// Concurrent missions share node facts while retaining distinct TaskRefs.
    #[test]
    fn multi_mission_matching_shares_state_without_identity_collision() {
        let node = registration("node-shared", CapabilityKind::Transport, "space-shared");
        let node_id = node.node_id().clone();
        let task_a = requirement_for_mission(
            "mission-a",
            "task-01",
            "transport",
            CapabilityKind::Transport,
        );
        let task_b = requirement_for_mission(
            "mission-b",
            "task-01",
            "transport",
            CapabilityKind::Transport,
        );
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
            .expect("shared node registration should succeed");

        let candidates_a = control
            .match_capabilities(&state, &task_a, timestamp, &correlation_id, &mut events)
            .expect("Mission A should read Shared State");
        let candidates_b = control
            .match_capabilities(&state, &task_b, timestamp, &correlation_id, &mut events)
            .expect("Mission B should read the same Shared State");
        assert_eq!(state.nodes().len(), 1);
        assert_ne!(candidates_a.task_ref(), candidates_b.task_ref());
        assert_eq!(
            candidates_a.roles()[0].node_ids(),
            std::slice::from_ref(&node_id)
        );
        assert_eq!(
            candidates_a.roles()[0].node_ids(),
            candidates_b.roles()[0].node_ids()
        );

        state
            .record_node_health(NodeHealthObservation::new(
                node_id.clone(),
                NodeStatus::new(NodeHealth::Offline, TimestampMs::new(1)),
                TimestampMs::new(1),
            ))
            .expect("shared health update should be accepted");
        for task in [&task_a, &task_b] {
            assert!(matches!(
                control.match_capabilities(
                    &state,
                    task,
                    TimestampMs::new(1),
                    &correlation_id,
                    &mut events,
                ),
                Err(ControlError::NoCandidate(_))
            ));
        }
    }

    /// Mission-scoped TaskRefs prevent identical local TaskIds from colliding.
    #[test]
    fn mission_scoped_task_identity_survives_control_chain() {
        let node_a = registration("node-a", CapabilityKind::Transport, "space-a");
        let node_b = registration("node-b", CapabilityKind::Transport, "space-b");
        let node_a_id = node_a.node_id().clone();
        let node_b_id = node_b.node_id().clone();
        let resource_a = ResourceId::new("space-a").expect("test resource id must be valid");
        let resource_b = ResourceId::new("space-b").expect("test resource id must be valid");
        let role_id = RoleId::new("transport").expect("test role id must be valid");
        let task_a = requirement_for_mission(
            "mission-a",
            "task-01",
            "transport",
            CapabilityKind::Transport,
        );
        let task_b = requirement_for_mission(
            "mission-b",
            "task-01",
            "transport",
            CapabilityKind::Transport,
        );
        let group_a = ExecutionGroupId::new("group-a").expect("test group id must be valid");
        let group_b = ExecutionGroupId::new("group-b").expect("test group id must be valid");
        let timestamp = TimestampMs::new(0);
        let correlation_id = correlation();
        let mut control = ControlPlane::new();
        let mut state = InMemorySharedNodeState::new();
        let mut events = TestEvents;
        for node in [node_a, node_b] {
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
        }

        let candidates_a = control
            .match_capabilities(&state, &task_a, timestamp, &correlation_id, &mut events)
            .expect("Mission A task should match");
        let candidates_b = control
            .match_capabilities(&state, &task_b, timestamp, &correlation_id, &mut events)
            .expect("Mission B task should match");
        let proposal_a = control
            .propose(
                &state,
                &task_a,
                &candidates_a,
                vec![RoleAssignment::new(
                    role_id.clone(),
                    node_a_id,
                    vec![resource_a.clone()],
                )],
                timestamp,
                &correlation_id,
                &mut events,
            )
            .expect("Mission A proposal should succeed");
        let proposal_b = control
            .propose(
                &state,
                &task_b,
                &candidates_b,
                vec![RoleAssignment::new(
                    role_id,
                    node_b_id,
                    vec![resource_b.clone()],
                )],
                timestamp,
                &correlation_id,
                &mut events,
            )
            .expect("Mission B proposal should succeed");
        let plan_a = control
            .commit(&proposal_a, timestamp, &correlation_id, &mut events)
            .expect("Mission A plan should commit");
        let plan_b = control
            .commit(&proposal_b, timestamp, &correlation_id, &mut events)
            .expect("Mission B plan should commit");
        control
            .create_group(
                group_a.clone(),
                &plan_a,
                timestamp,
                &correlation_id,
                &mut events,
            )
            .expect("Mission A group should bind");
        control
            .create_group(
                group_b.clone(),
                &plan_b,
                timestamp,
                &correlation_id,
                &mut events,
            )
            .expect("Mission B group should bind");

        assert_eq!(task_a.task_id(), task_b.task_id());
        assert_ne!(task_a.task_ref(), task_b.task_ref());
        assert_eq!(candidates_a.task_ref(), task_a.task_ref());
        assert_eq!(candidates_b.task_ref(), task_b.task_ref());
        assert_eq!(proposal_a.task_ref(), task_a.task_ref());
        assert_eq!(proposal_b.task_ref(), task_b.task_ref());
        assert_eq!(plan_a.task_ref(), task_a.task_ref());
        assert_eq!(plan_b.task_ref(), task_b.task_ref());
        assert_eq!(
            control
                .group(&group_a)
                .expect("Mission A group must exist")
                .task_ref(),
            task_a.task_ref()
        );
        assert_eq!(
            control
                .group(&group_b)
                .expect("Mission B group must exist")
                .task_ref(),
            task_b.task_ref()
        );
        let reservation_a = control
            .reservations
            .get(&resource_a)
            .expect("Mission A reservation must exist");
        let reservation_b = control
            .reservations
            .get(&resource_b)
            .expect("Mission B reservation must exist");
        assert_eq!(&reservation_a.task_ref, task_a.task_ref());
        assert_eq!(&reservation_b.task_ref, task_b.task_ref());
        assert_eq!(reservation_a.group_id.as_ref(), Some(&group_a));
        assert_eq!(reservation_b.group_id.as_ref(), Some(&group_b));
    }

    /// A node without the required capability is rejected during matching.
    #[test]
    fn matching_rejects_missing_capability() {
        let node = registration("node-a", CapabilityKind::Mobility, "space-a");
        let task = requirement("task-rejected", "transport", CapabilityKind::Transport);
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

        assert!(matches!(
            control.match_capabilities(
                &state,
                &task,
                timestamp,
                &correlation_id,
                &mut events,
            ),
            Err(ControlError::NoCandidate(role)) if role.as_str() == "transport"
        ));
    }

    /// A second commit cannot take a resource already held by another task.
    #[test]
    fn commit_rejects_resource_conflict() {
        let node = registration("node-a", CapabilityKind::Transport, "space-a");
        let node_id = node.node_id().clone();
        let resource_id = ResourceId::new("space-a").expect("test resource id must be valid");
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

        let first_task = requirement("task-first", "transport-first", CapabilityKind::Transport);
        let first_candidates = control
            .match_capabilities(&state, &first_task, timestamp, &correlation_id, &mut events)
            .expect("first task should match");
        let first_proposal = control
            .propose(
                &state,
                &first_task,
                &first_candidates,
                vec![RoleAssignment::new(
                    RoleId::new("transport-first").expect("test role id must be valid"),
                    node_id.clone(),
                    vec![resource_id.clone()],
                )],
                timestamp,
                &correlation_id,
                &mut events,
            )
            .expect("first proposal should be valid");
        control
            .commit(&first_proposal, timestamp, &correlation_id, &mut events)
            .expect("first proposal should commit");

        let second_task = requirement("task-second", "transport-second", CapabilityKind::Transport);
        let second_candidates = control
            .match_capabilities(
                &state,
                &second_task,
                timestamp,
                &correlation_id,
                &mut events,
            )
            .expect("second task can match before commit");
        let second_proposal = control
            .propose(
                &state,
                &second_task,
                &second_candidates,
                vec![RoleAssignment::new(
                    RoleId::new("transport-second").expect("test role id must be valid"),
                    node_id,
                    vec![resource_id.clone()],
                )],
                timestamp,
                &correlation_id,
                &mut events,
            )
            .expect("second proposal should be valid before reservation");

        assert!(matches!(
            control.commit(&second_proposal, timestamp, &correlation_id, &mut events),
            Err(ControlError::ResourceConflict { resource_id: conflict, .. })
                if conflict == resource_id
        ));
    }

    /// Terminal lifecycle states reject reactivation and release enables resource reuse.
    #[test]
    fn lifecycle_guards_terminal_states_and_release_frees_resources() {
        let node = registration(
            "node-lifecycle",
            CapabilityKind::Transport,
            "space-lifecycle",
        );
        let node_id = node.node_id().clone();
        let resource_id =
            ResourceId::new("space-lifecycle").expect("test resource id must be valid");
        let role_id = RoleId::new("transport").expect("test role id must be valid");
        let first_task = requirement_for_mission(
            "mission-lifecycle-a",
            "task-01",
            "transport",
            CapabilityKind::Transport,
        );
        let first_group =
            ExecutionGroupId::new("group-lifecycle-a").expect("test group id must be valid");
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
        let first_candidates = control
            .match_capabilities(&state, &first_task, timestamp, &correlation_id, &mut events)
            .expect("first task should match");
        let first_proposal = control
            .propose(
                &state,
                &first_task,
                &first_candidates,
                vec![RoleAssignment::new(
                    role_id.clone(),
                    node_id.clone(),
                    vec![resource_id.clone()],
                )],
                timestamp,
                &correlation_id,
                &mut events,
            )
            .expect("first proposal should be valid");
        let first_plan = control
            .commit(&first_proposal, timestamp, &correlation_id, &mut events)
            .expect("first proposal should commit");
        control
            .create_group(
                first_group.clone(),
                &first_plan,
                timestamp,
                &correlation_id,
                &mut events,
            )
            .expect("first group should bind");
        assert!(matches!(
            control.complete_group(
                &first_group,
                TimestampMs::new(1),
                &correlation_id,
                &mut events,
            ),
            Err(ControlError::InvalidLifecycle(GroupLifecycle::Bound))
        ));
        control
            .activate_group(
                &first_group,
                TimestampMs::new(2),
                &correlation_id,
                &mut events,
            )
            .expect("bound group should activate");
        control
            .complete_group(
                &first_group,
                TimestampMs::new(3),
                &correlation_id,
                &mut events,
            )
            .expect("active group should complete");
        assert!(matches!(
            control.activate_group(
                &first_group,
                TimestampMs::new(4),
                &correlation_id,
                &mut events,
            ),
            Err(ControlError::InvalidLifecycle(GroupLifecycle::Completed))
        ));
        control
            .release_group(
                &first_group,
                TimestampMs::new(5),
                &correlation_id,
                &mut events,
            )
            .expect("completed group should release");
        assert!(!control.reservations.contains_key(&resource_id));
        assert!(
            control
                .group(&first_group)
                .expect("released group should remain observable")
                .assignments()
                .is_empty()
        );
        assert!(matches!(
            control.activate_group(
                &first_group,
                TimestampMs::new(6),
                &correlation_id,
                &mut events,
            ),
            Err(ControlError::InvalidLifecycle(GroupLifecycle::Released))
        ));

        let second_task = requirement_for_mission(
            "mission-lifecycle-b",
            "task-01",
            "transport",
            CapabilityKind::Transport,
        );
        let second_group =
            ExecutionGroupId::new("group-lifecycle-b").expect("test group id must be valid");
        let second_candidates = control
            .match_capabilities(
                &state,
                &second_task,
                TimestampMs::new(6),
                &correlation_id,
                &mut events,
            )
            .expect("second task should match");
        let second_proposal = control
            .propose(
                &state,
                &second_task,
                &second_candidates,
                vec![RoleAssignment::new(
                    role_id,
                    node_id,
                    vec![resource_id.clone()],
                )],
                TimestampMs::new(6),
                &correlation_id,
                &mut events,
            )
            .expect("released resource should be proposed again");
        let second_plan = control
            .commit(
                &second_proposal,
                TimestampMs::new(6),
                &correlation_id,
                &mut events,
            )
            .expect("released resource should commit again");
        control
            .create_group(
                second_group.clone(),
                &second_plan,
                TimestampMs::new(6),
                &correlation_id,
                &mut events,
            )
            .expect("second group should bind");
        control
            .activate_group(
                &second_group,
                TimestampMs::new(7),
                &correlation_id,
                &mut events,
            )
            .expect("second group should activate");
        control
            .block_group(
                &second_group,
                "no safe continuation",
                TimestampMs::new(8),
                &correlation_id,
                &mut events,
            )
            .expect("active group may become blocked");
        assert!(matches!(
            control.complete_group(
                &second_group,
                TimestampMs::new(9),
                &correlation_id,
                &mut events,
            ),
            Err(ControlError::InvalidLifecycle(GroupLifecycle::Blocked))
        ));
        assert!(matches!(
            control.release_group(
                &second_group,
                TimestampMs::new(10),
                &correlation_id,
                &mut events,
            ),
            Err(ControlError::InvalidLifecycle(GroupLifecycle::Blocked))
        ));
        control
            .fail_group(
                &second_group,
                "recovery exhausted",
                TimestampMs::new(11),
                &correlation_id,
                &mut events,
            )
            .expect("blocked group should explicitly fail");
        control
            .release_group(
                &second_group,
                TimestampMs::new(12),
                &correlation_id,
                &mut events,
            )
            .expect("failed group should release");
        assert_eq!(
            control
                .group(&second_group)
                .expect("released failed group should remain observable")
                .lifecycle(),
            GroupLifecycle::Released
        );
        assert!(!control.reservations.contains_key(&resource_id));
    }
