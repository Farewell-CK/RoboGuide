    /// A Group role cannot own two simultaneous pending recovery commitments.
    #[test]
    fn second_recovery_commit_for_same_group_role_is_rejected() {
        let mut fixture = recovery_fixture(true);
        let need = begin_detected_transport_recovery(&mut fixture);
        let candidates =
            match_fixture_recovery_candidates(&mut fixture, &need, TimestampMs::new(3));
        let proposal_b = propose_fixture_node_b(&mut fixture, &candidates, TimestampMs::new(4));
        let committed_b = commit_fixture_node_b(&mut fixture, &proposal_b, TimestampMs::new(5));
        let (node_c, space_c) =
            register_transport_replacement(&mut fixture, "node-c", "space-c", TimestampMs::new(6));
        let candidates_c =
            match_fixture_recovery_candidates(&mut fixture, &need, TimestampMs::new(6));
        let proposal_c = fixture
            .control
            .propose_role_recovery(
                &fixture.state,
                &candidates_c,
                &fixture.requirement,
                node_c,
                vec![space_c.clone()],
                TimestampMs::new(6),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("a second non-authoritative proposal may be created");

        assert!(matches!(
            fixture.control.commit_role_recovery(
                &fixture.state,
                &fixture.requirement,
                &proposal_c,
                TimestampMs::new(7),
                &fixture.correlation_id,
                &mut fixture.events,
            ),
            Err(ControlError::PendingRecoveryCommitmentExists { .. })
        ));
        assert_eq!(
            fixture
                .control
                .pending_recovery_commitment(&fixture.group_id, &fixture.transport_role),
            Some(&committed_b)
        );
        assert!(fixture.control.reservations.contains_key(&fixture.space_b));
        assert!(!fixture.control.reservations.contains_key(&space_c));
    }

    /// Abort releases only replacement resources and invalidates the old commitment handle.
    #[test]
    fn abort_recovery_commitment_preserves_group_and_rejects_stale_handle() {
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
            .expect("pending Node B commitment should abort");

        assert!(
            fixture
                .control
                .pending_recovery_commitment(&fixture.group_id, &fixture.transport_role)
                .is_none()
        );
        assert!(!fixture.control.reservations.contains_key(&fixture.space_b));
        assert!(
            fixture
                .control
                .reservations
                .contains_key(&fixture.compute_c)
        );
        let group = fixture
            .control
            .group(&fixture.group_id)
            .expect("aborted recovery Group should remain");
        assert_eq!(group.lifecycle(), GroupLifecycle::Blocked);
        assert!(group.is_role_unbound(&fixture.transport_role));
        assert_eq!(group.assignments().len(), 1);
        assert_eq!(group.assignments()[0].role_id(), &fixture.compute_role);
        assert!(matches!(
            fixture.control.rebind_role(
                &committed,
                TimestampMs::new(7),
                &fixture.correlation_id,
                &mut fixture.events,
            ),
            Err(ControlError::PendingRecoveryCommitmentNotFound { .. })
        ));
        assert!(fixture.events.records.iter().any(|(_, payload)| matches!(
            payload,
            EventPayload::RecoveryAssignmentAborted { group_id, role_id, resource_ids, .. }
                if group_id == &fixture.group_id
                    && role_id == &fixture.transport_role
                    && resource_ids == std::slice::from_ref(&fixture.space_b)
        )));
    }

    /// Abort returns recovery to Pending and permits a new Node C commitment.
    #[test]
    fn abort_permits_new_recovery_commitment() {
        let mut fixture = recovery_fixture(true);
        let need = begin_detected_transport_recovery(&mut fixture);
        let candidates_b =
            match_fixture_recovery_candidates(&mut fixture, &need, TimestampMs::new(3));
        let proposal_b = propose_fixture_node_b(&mut fixture, &candidates_b, TimestampMs::new(4));
        let committed_b = commit_fixture_node_b(&mut fixture, &proposal_b, TimestampMs::new(5));
        fixture
            .control
            .abort_role_recovery_commitment(
                &committed_b,
                TimestampMs::new(6),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("Node B attempt should abort");
        let (node_c, space_c) =
            register_transport_replacement(&mut fixture, "node-c", "space-c", TimestampMs::new(7));
        let candidates_c =
            match_fixture_recovery_candidates(&mut fixture, &need, TimestampMs::new(7));
        let proposal_c = fixture
            .control
            .propose_role_recovery(
                &fixture.state,
                &candidates_c,
                &fixture.requirement,
                node_c.clone(),
                vec![space_c.clone()],
                TimestampMs::new(8),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("bootstrap scheduler should propose Node C");
        let committed_c = fixture
            .control
            .commit_role_recovery(
                &fixture.state,
                &fixture.requirement,
                &proposal_c,
                TimestampMs::new(9),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("Node C attempt should commit after Abort");

        assert_eq!(committed_c.replacement_node_id(), &node_c);
        assert_eq!(
            fixture
                .control
                .pending_recovery_commitment(&fixture.group_id, &fixture.transport_role),
            Some(&committed_c)
        );
        assert!(!fixture.control.reservations.contains_key(&fixture.space_b));
        assert!(fixture.control.reservations.contains_key(&space_c));
        assert!(matches!(
            fixture.control.rebind_role(
                &committed_b,
                TimestampMs::new(10),
                &fixture.correlation_id,
                &mut fixture.events,
            ),
            Err(ControlError::PendingRecoveryCommitmentMismatch { .. })
        ));
    }

    /// Failed terminal release cleans committed-but-not-bound resources and pending authority.
    #[test]
    fn failed_group_release_cleans_pending_recovery_commitment() {
        let mut fixture = recovery_fixture(true);
        let need = begin_detected_transport_recovery(&mut fixture);
        let candidates =
            match_fixture_recovery_candidates(&mut fixture, &need, TimestampMs::new(3));
        let proposal = propose_fixture_node_b(&mut fixture, &candidates, TimestampMs::new(4));
        let committed = commit_fixture_node_b(&mut fixture, &proposal, TimestampMs::new(5));
        fixture
            .control
            .fail_group(
                &fixture.group_id,
                "recovery explicitly exhausted",
                TimestampMs::new(6),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("Blocked Group should explicitly fail");
        fixture
            .control
            .release_group(
                &fixture.group_id,
                TimestampMs::new(7),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("Failed Group should release all ownership");

        assert_eq!(
            fixture
                .control
                .group(&fixture.group_id)
                .expect("Released Group should remain observable")
                .lifecycle(),
            GroupLifecycle::Released
        );
        assert!(
            fixture
                .control
                .pending_recovery_commitment(&fixture.group_id, &fixture.transport_role)
                .is_none()
        );
        assert!(!fixture.control.reservations.contains_key(&fixture.space_b));
        assert!(
            !fixture
                .control
                .reservations
                .contains_key(&fixture.compute_c)
        );
        assert!(
            !fixture
                .control
                .reservations
                .values()
                .any(|reservation| { reservation.group_id.as_ref() == Some(&fixture.group_id) })
        );
        assert!(matches!(
            fixture.control.rebind_role(
                &committed,
                TimestampMs::new(8),
                &fixture.correlation_id,
                &mut fixture.events,
            ),
            Err(ControlError::InvalidLifecycle(GroupLifecycle::Released))
        ));
    }

    /// Terminal cleanup for Mission A cannot remove an active Mission B reservation.
    #[test]
    fn pending_cleanup_is_multi_mission_isolated() {
        let mut fixture = recovery_fixture(true);
        let need = begin_detected_transport_recovery(&mut fixture);
        let candidates =
            match_fixture_recovery_candidates(&mut fixture, &need, TimestampMs::new(3));
        let proposal = propose_fixture_node_b(&mut fixture, &candidates, TimestampMs::new(4));
        let committed = commit_fixture_node_b(&mut fixture, &proposal, TimestampMs::new(5));
        let (node_c, space_c) =
            register_transport_replacement(&mut fixture, "node-c", "space-c", TimestampMs::new(6));
        let mission_b_task = requirement_for_mission(
            "mission-b",
            "task-b",
            "transport-b",
            CapabilityKind::Transport,
        );
        let mission_b_candidates = fixture
            .control
            .match_capabilities(
                &fixture.state,
                &mission_b_task,
                TimestampMs::new(6),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("Mission B should match Node C");
        let mission_b_proposal = fixture
            .control
            .propose(
                &fixture.state,
                &mission_b_task,
                &mission_b_candidates,
                vec![RoleAssignment::new(
                    RoleId::new("transport-b").expect("test role id must be valid"),
                    node_c,
                    vec![space_c.clone()],
                )],
                TimestampMs::new(6),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("Mission B proposal should succeed");
        let mission_b_plan = fixture
            .control
            .commit(
                &mission_b_proposal,
                TimestampMs::new(6),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("Mission B resource should commit");
        let group_b = ExecutionGroupId::new("group-b").expect("test group id must be valid");
        fixture
            .control
            .create_group(
                group_b.clone(),
                &mission_b_plan,
                TimestampMs::new(6),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("Mission B Group should bind");
        fixture
            .control
            .activate_group(
                &group_b,
                TimestampMs::new(6),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("Mission B Group should activate");

        fixture
            .control
            .abort_role_recovery_commitment(
                &committed,
                TimestampMs::new(7),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("Mission A pending commitment should abort");
        fixture
            .control
            .fail_group(
                &fixture.group_id,
                "Mission A recovery exhausted",
                TimestampMs::new(8),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("Mission A should fail explicitly");
        fixture
            .control
            .release_group(
                &fixture.group_id,
                TimestampMs::new(9),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("Mission A should release");

        assert_eq!(
            fixture
                .control
                .group(&group_b)
                .expect("Mission B Group should remain")
                .lifecycle(),
            GroupLifecycle::Active
        );
        assert_eq!(
            fixture
                .control
                .reservations
                .get(&space_c)
                .and_then(|reservation| reservation.group_id.as_ref()),
            Some(&group_b)
        );
    }

    /// Abort validates every resource before mutating any pending ownership.
    #[test]
    fn multi_resource_abort_is_atomic_on_ownership_mismatch() {
        let mut fixture = recovery_fixture(true);
        let need = begin_detected_transport_recovery(&mut fixture);
        let candidates =
            match_fixture_recovery_candidates(&mut fixture, &need, TimestampMs::new(3));
        let proposal = fixture
            .control
            .propose_role_recovery(
                &fixture.state,
                &candidates,
                &fixture.requirement,
                fixture.node_b_id.clone(),
                vec![fixture.space_b.clone(), fixture.space_b_secondary.clone()],
                TimestampMs::new(4),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("multi-resource proposal should validate");
        let committed = fixture
            .control
            .commit_role_recovery(
                &fixture.state,
                &fixture.requirement,
                &proposal,
                TimestampMs::new(5),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("multi-resource proposal should commit");
        fixture
            .control
            .reservations
            .get_mut(&fixture.space_b_secondary)
            .expect("secondary reservation should exist")
            .role_id = RoleId::new("other-role").expect("test role id must be valid");

        assert!(matches!(
            fixture.control.abort_role_recovery_commitment(
                &committed,
                TimestampMs::new(6),
                &fixture.correlation_id,
                &mut fixture.events,
            ),
            Err(ControlError::InvalidProposal(_))
        ));
        assert!(fixture.control.reservations.contains_key(&fixture.space_b));
        assert!(
            fixture
                .control
                .reservations
                .contains_key(&fixture.space_b_secondary)
        );
        assert_eq!(
            fixture
                .control
                .pending_recovery_commitment(&fixture.group_id, &fixture.transport_role),
            Some(&committed)
        );
    }

    /// Roles without resources still require authoritative pending commitment consumption.
    #[test]
    fn zero_resource_recovery_commitment_is_tracked_and_consumed() {
        let node_a = registration("node-zero-a", CapabilityKind::Observation, "space-zero-a");
        let node_b = registration("node-zero-b", CapabilityKind::Observation, "space-zero-b");
        let node_a_id = node_a.node_id().clone();
        let node_b_id = node_b.node_id().clone();
        let mission_id =
            domain::MissionId::new("mission-zero").expect("test mission id must be valid");
        let role_id = RoleId::new("observe").expect("test role id must be valid");
        let requirement = TaskRequirement::new(
            mission_id,
            TaskId::new("task-zero").expect("test task id must be valid"),
            vec![RoleRequirement::new(
                role_id.clone(),
                CapabilityKind::Observation,
                None,
            )],
        )
        .expect("zero-resource requirement should be valid");
        let group_id = ExecutionGroupId::new("group-zero").expect("test group id must be valid");
        let correlation_id = correlation();
        let timestamp = TimestampMs::new(0);
        let mut control = ControlPlane::new();
        let mut state = InMemorySharedNodeState::new();
        let mut events = RecordingEvents::default();
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
                .expect("zero-resource node should register");
        }
        let candidates = control
            .match_capabilities(
                &state,
                &requirement,
                timestamp,
                &correlation_id,
                &mut events,
            )
            .expect("zero-resource task should match");
        let proposal = control
            .propose(
                &state,
                &requirement,
                &candidates,
                vec![RoleAssignment::new(
                    role_id.clone(),
                    node_a_id.clone(),
                    vec![],
                )],
                timestamp,
                &correlation_id,
                &mut events,
            )
            .expect("zero-resource proposal should validate");
        let plan = control
            .commit(&proposal, timestamp, &correlation_id, &mut events)
            .expect("zero-resource proposal should commit");
        control
            .create_group(
                group_id.clone(),
                &plan,
                timestamp,
                &correlation_id,
                &mut events,
            )
            .expect("zero-resource Group should bind");
        control
            .activate_group(&group_id, timestamp, &correlation_id, &mut events)
            .expect("zero-resource Group should activate");
        state
            .record_node_liveness(
                &node_a_id,
                NodeLivenessObservation::new(NodeLiveness::Unreachable, TimestampMs::new(1)),
            )
            .expect("source node should become unreachable");
        let assessment = control
            .assess_group(
                &state,
                &group_id,
                &requirement,
                TimestampMs::new(1),
                &correlation_id,
                &mut events,
            )
            .expect("zero-resource Group assessment should succeed");
        let ReconciliationAssessment::RoleRecoveryRequired(need) = assessment else {
            panic!("zero-resource role should require recovery");
        };
        control
            .begin_role_recovery(&need, TimestampMs::new(2), &correlation_id, &mut events)
            .expect("zero-resource recovery should begin");
        let recovery_candidates = control
            .match_recovery_candidates(
                &state,
                &need,
                &requirement,
                TimestampMs::new(3),
                &correlation_id,
                &mut events,
            )
            .expect("zero-resource role should rematch");
        let recovery_proposal = control
            .propose_role_recovery(
                &state,
                &recovery_candidates,
                &requirement,
                node_b_id.clone(),
                vec![],
                TimestampMs::new(4),
                &correlation_id,
                &mut events,
            )
            .expect("zero-resource replacement should be proposed");
        let committed = control
            .commit_role_recovery(
                &state,
                &requirement,
                &recovery_proposal,
                TimestampMs::new(5),
                &correlation_id,
                &mut events,
            )
            .expect("zero-resource replacement should commit");
        assert!(committed.committed_resource_ids().is_empty());
        assert_eq!(
            control.pending_recovery_commitment(&group_id, &role_id),
            Some(&committed)
        );
        control
            .rebind_role(
                &committed,
                TimestampMs::new(6),
                &correlation_id,
                &mut events,
            )
            .expect("zero-resource commitment should be consumed by rebind");
        assert!(
            control
                .pending_recovery_commitment(&group_id, &role_id)
                .is_none()
        );
        let group = control
            .group(&group_id)
            .expect("zero-resource Group should remain");
        assert_eq!(group.lifecycle(), GroupLifecycle::Adapted);
        assert_eq!(group.assignments()[0].node_id(), &node_b_id);
        assert!(group.assignments()[0].resource_ids().is_empty());
    }

    /// Missing replacement input leaves the Group pending rather than Failed or Released.
    #[test]
    fn reconciliation_without_replacement_remains_pending() {
        let mut fixture = recovery_fixture(false);
        let need = begin_detected_transport_recovery(&mut fixture);
        let candidates =
            match_fixture_recovery_candidates(&mut fixture, &need, TimestampMs::new(3));

        assert!(candidates.is_empty());
        let group = fixture
            .control
            .group(&fixture.group_id)
            .expect("pending Group should remain");
        assert_eq!(group.lifecycle(), GroupLifecycle::Blocked);
        assert!(group.is_role_unbound(&fixture.transport_role));
        assert!(!fixture.events.records.iter().any(|(_, payload)| matches!(
            payload,
            EventPayload::ExecutionGroupFailed { .. } | EventPayload::ExecutionGroupReleased { .. }
        )));
    }

    /// Reconciliation uses receive-time freshness from the shared eligibility predicate.
    #[test]
    fn reconciliation_detects_stale_assignment_with_large_source_time() {
        let mut fixture = recovery_fixture(true);
        fixture
            .state
            .record_node_health(NodeHealthObservation::new(
                fixture.node_a_id.clone(),
                NodeStatus::new(NodeHealth::Online, TimestampMs::new(1_000_000)),
                TimestampMs::new(0),
            ))
            .expect("equal receive time should preserve source evidence");
        fixture
            .state
            .record_node_health(NodeHealthObservation::new(
                fixture.edge_c_id.clone(),
                NodeStatus::new(NodeHealth::Online, TimestampMs::new(1)),
                TimestampMs::new(5_001),
            ))
            .expect("compute health should remain fresh");

        let need = assess_transport_recovery(&mut fixture, TimestampMs::new(5_001));
        assert_eq!(need.role_id(), &fixture.transport_role);
        assert_eq!(need.current_node_id(), &fixture.node_a_id);
        assert_eq!(
            fixture
                .control
                .group(&fixture.group_id)
                .expect("assessment must not mutate Group")
                .lifecycle(),
            GroupLifecycle::Active
        );
    }

    /// Lease-derived Unreachable state triggers the same shared eligibility policy.
    #[test]
    fn reconciliation_detects_lease_expired_assignment() {
        let mut fixture = recovery_fixture(false);
        fixture
            .control
            .accept_heartbeat(
                &mut fixture.state,
                NodeHeartbeat::new(
                    fixture.edge_c_id.clone(),
                    LeaseId::new("lease-edge-c").expect("test lease id should be valid"),
                    NodeStatus::new(NodeHealth::Online, TimestampMs::new(10)),
                ),
                TimestampMs::new(DEFAULT_NODE_LEASE_TTL_MS - 1),
                DEFAULT_NODE_LEASE_TTL_MS,
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("compute lease should renew before expiry");
        fixture
            .control
            .expire_leases(
                &mut fixture.state,
                TimestampMs::new(DEFAULT_NODE_LEASE_TTL_MS),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("expired transport lease should update liveness");

        let need =
            assess_transport_recovery(&mut fixture, TimestampMs::new(DEFAULT_NODE_LEASE_TTL_MS));
        assert_eq!(need.role_id(), &fixture.transport_role);
        assert_eq!(need.current_node_id(), &fixture.node_a_id);
    }
