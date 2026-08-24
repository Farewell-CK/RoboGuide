    /// A healthy active Group produces NoAction without lifecycle mutation or events.
    #[test]
    fn reconciliation_healthy_active_group_requires_no_action() {
        let mut fixture = recovery_fixture(true);
        let event_count = fixture.events.records.len();
        let assessment = fixture
            .control
            .assess_group(
                &fixture.state,
                &fixture.group_id,
                &fixture.requirement,
                TimestampMs::new(1),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("healthy Group assessment should succeed");

        assert_eq!(assessment, ReconciliationAssessment::NoAction);
        assert_eq!(fixture.events.records.len(), event_count);
        assert_eq!(
            fixture
                .control
                .group(&fixture.group_id)
                .expect("healthy Group should remain")
                .lifecycle(),
            GroupLifecycle::Active
        );
    }

    /// Detection identifies one unavailable assignment without modifying the Group.
    #[test]
    fn reconciliation_detects_unreachable_assignment_without_mutation() {
        let mut fixture = recovery_fixture(true);
        let original_assignments = fixture
            .control
            .group(&fixture.group_id)
            .expect("active Group should exist")
            .assignments()
            .to_vec();
        mark_transport_unreachable(&mut fixture, TimestampMs::new(1));
        let need = assess_transport_recovery(&mut fixture, TimestampMs::new(1));

        assert_eq!(need.group_id(), &fixture.group_id);
        assert_eq!(need.task_ref(), &fixture.task_ref);
        assert_eq!(need.role_id(), &fixture.transport_role);
        assert_eq!(need.current_node_id(), &fixture.node_a_id);
        let group = fixture
            .control
            .group(&fixture.group_id)
            .expect("assessment must retain the Group");
        assert_eq!(group.lifecycle(), GroupLifecycle::Active);
        assert_eq!(group.assignments(), original_assignments.as_slice());
        assert!(fixture.events.records.iter().any(|(_, payload)| matches!(
            payload,
            EventPayload::ReconciliationRoleRecoveryRequired {
                group_id,
                task_ref,
                role_id,
                node_id,
            } if group_id == &fixture.group_id
                && task_ref == &fixture.task_ref
                && role_id == &fixture.transport_role
                && node_id == &fixture.node_a_id
        )));
    }

    /// Beginning recovery blocks the Group and releases only the affected role.
    #[test]
    fn reconciliation_begin_recovery_preserves_unaffected_binding() {
        let mut fixture = recovery_fixture(true);
        let original_compute = fixture
            .control
            .group(&fixture.group_id)
            .expect("active Group should exist")
            .assignments()
            .iter()
            .find(|assignment| assignment.role_id() == &fixture.compute_role)
            .expect("compute assignment should exist")
            .clone();
        mark_transport_unreachable(&mut fixture, TimestampMs::new(1));
        let need = assess_transport_recovery(&mut fixture, TimestampMs::new(1));
        let outcome = fixture
            .control
            .begin_role_recovery(
                &need,
                TimestampMs::new(2),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("recovery should block and partially release transport");

        assert!(matches!(
            outcome,
            RecoveryOutcome::Pending { ref group_id, ref task_ref, ref role_id }
                if group_id == &fixture.group_id
                    && task_ref == &fixture.task_ref
                    && role_id == &fixture.transport_role
        ));
        let group = fixture
            .control
            .group(&fixture.group_id)
            .expect("blocked Group should remain");
        assert_eq!(group.group_id(), &fixture.group_id);
        assert_eq!(group.task_ref(), &fixture.task_ref);
        assert_eq!(group.lifecycle(), GroupLifecycle::Blocked);
        assert!(group.is_role_unbound(&fixture.transport_role));
        assert_eq!(
            group
                .assignments()
                .iter()
                .find(|assignment| assignment.role_id() == &fixture.compute_role),
            Some(&original_compute)
        );
        assert!(!fixture.control.reservations.contains_key(&fixture.space_a));
        assert!(
            fixture
                .control
                .reservations
                .contains_key(&fixture.compute_c)
        );
    }

    /// The complete role-scoped pipeline commits before rebinding and preserves Group context.
    #[test]
    fn recovery_pipeline_commits_then_rebinds_external_choice() {
        let mut fixture = recovery_fixture(true);
        let original_compute = fixture
            .control
            .group(&fixture.group_id)
            .expect("active Group should exist")
            .assignments()
            .iter()
            .find(|assignment| assignment.role_id() == &fixture.compute_role)
            .expect("compute assignment should exist")
            .clone();
        let need = begin_detected_transport_recovery(&mut fixture);
        let candidates =
            match_fixture_recovery_candidates(&mut fixture, &need, TimestampMs::new(3));
        assert_eq!(candidates.role_id(), &fixture.transport_role);
        assert_eq!(
            candidates.candidate_node_ids(),
            std::slice::from_ref(&fixture.node_b_id)
        );
        assert!(!candidates.candidate_node_ids().contains(&fixture.node_a_id));
        let proposal = propose_fixture_node_b(&mut fixture, &candidates, TimestampMs::new(4));
        assert!(!fixture.control.reservations.contains_key(&fixture.space_b));
        assert_eq!(
            fixture
                .control
                .group(&fixture.group_id)
                .expect("proposal must not bind the Group")
                .lifecycle(),
            GroupLifecycle::Blocked
        );
        let committed = commit_fixture_node_b(&mut fixture, &proposal, TimestampMs::new(5));
        assert_eq!(committed.group_id(), &fixture.group_id);
        assert_eq!(committed.task_ref(), &fixture.task_ref);
        assert_eq!(committed.role_id(), &fixture.transport_role);
        assert_eq!(
            fixture
                .control
                .pending_recovery_commitment(&fixture.group_id, &fixture.transport_role),
            Some(&committed)
        );
        assert_eq!(
            fixture
                .control
                .reservations
                .get(&fixture.space_b)
                .and_then(|reservation| reservation.group_id.as_ref()),
            Some(&fixture.group_id)
        );
        let committed_but_unbound = fixture
            .control
            .group(&fixture.group_id)
            .expect("committed Group should remain observable");
        assert_eq!(committed_but_unbound.lifecycle(), GroupLifecycle::Blocked);
        assert!(committed_but_unbound.is_role_unbound(&fixture.transport_role));

        let outcome = fixture
            .control
            .rebind_role(
                &committed,
                TimestampMs::new(6),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("committed replacement should rebind transport");
        assert!(
            fixture
                .control
                .pending_recovery_commitment(&fixture.group_id, &fixture.transport_role)
                .is_none()
        );
        assert_eq!(
            fixture
                .control
                .reservations
                .get(&fixture.space_b)
                .and_then(|reservation| reservation.group_id.as_ref()),
            Some(&fixture.group_id)
        );

        assert!(matches!(
            outcome,
            RecoveryOutcome::Recovered { ref group_id, ref role_id, ref from_node, ref to_node, .. }
                if group_id == &fixture.group_id
                    && role_id == &fixture.transport_role
                    && from_node == &fixture.node_a_id
                    && to_node == &fixture.node_b_id
        ));
        let adapted = fixture
            .control
            .group(&fixture.group_id)
            .expect("adapted Group should remain");
        assert_eq!(adapted.lifecycle(), GroupLifecycle::Adapted);
        assert_eq!(adapted.task_ref(), &fixture.task_ref);
        assert_eq!(
            adapted
                .assignments()
                .iter()
                .find(|assignment| assignment.role_id() == &fixture.compute_role),
            Some(&original_compute)
        );
        assert!(adapted.assignments().iter().any(|assignment| {
            assignment.role_id() == &fixture.transport_role
                && assignment.node_id() == &fixture.node_b_id
                && assignment.resource_ids() == std::slice::from_ref(&fixture.space_b)
        }));
        fixture
            .control
            .activate_group(
                &fixture.group_id,
                TimestampMs::new(7),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("adapted Group should reactivate");
        assert_eq!(
            fixture
                .control
                .group(&fixture.group_id)
                .expect("reactivated Group should remain")
                .lifecycle(),
            GroupLifecycle::Active
        );
    }

    /// A replacement that becomes unavailable after proposal is rejected at commit.
    #[test]
    fn recovery_commit_rejects_replacement_that_became_unavailable() {
        let mut fixture = recovery_fixture(true);
        let need = begin_detected_transport_recovery(&mut fixture);
        let candidates =
            match_fixture_recovery_candidates(&mut fixture, &need, TimestampMs::new(3));
        let proposal = propose_fixture_node_b(&mut fixture, &candidates, TimestampMs::new(4));
        fixture
            .state
            .record_node_liveness(
                &fixture.node_b_id,
                NodeLivenessObservation::new(NodeLiveness::Unreachable, TimestampMs::new(5)),
            )
            .expect("replacement liveness observation should be accepted");

        assert!(matches!(
            fixture.control.commit_role_recovery(
                &fixture.state,
                &fixture.requirement,
                &proposal,
                TimestampMs::new(5),
                &fixture.correlation_id,
                &mut fixture.events,
            ),
            Err(ControlError::InvalidProposal(_))
        ));
        let group = fixture
            .control
            .group(&fixture.group_id)
            .expect("pending Group should remain");
        assert_eq!(group.lifecycle(), GroupLifecycle::Blocked);
        assert!(group.is_role_unbound(&fixture.transport_role));
        assert!(!fixture.control.reservations.contains_key(&fixture.space_b));
        assert!(
            fixture
                .control
                .reservations
                .contains_key(&fixture.compute_c)
        );
    }

    /// Scheduler choices outside the role-scoped Candidate Set cannot become proposals.
    #[test]
    fn recovery_proposal_requires_candidate_membership() {
        let mut fixture = recovery_fixture(true);
        let need = begin_detected_transport_recovery(&mut fixture);
        let candidates =
            match_fixture_recovery_candidates(&mut fixture, &need, TimestampMs::new(3));
        let node_c = NodeId::new("node-c").expect("test node id must be valid");

        assert!(matches!(
            fixture.control.propose_role_recovery(
                &fixture.state,
                &candidates,
                &fixture.requirement,
                node_c,
                vec![fixture.space_b.clone()],
                TimestampMs::new(4),
                &fixture.correlation_id,
                &mut fixture.events,
            ),
            Err(ControlError::InvalidProposal(_))
        ));
        assert!(!fixture.control.reservations.contains_key(&fixture.space_b));
        assert_eq!(
            fixture
                .control
                .group(&fixture.group_id)
                .expect("invalid proposal must not mutate Group")
                .lifecycle(),
            GroupLifecycle::Blocked
        );
    }

    /// Resource conflict after proposal leaves every recovery resource uncommitted atomically.
    #[test]
    fn recovery_commit_conflict_is_atomic_and_multi_mission_isolated() {
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
            .expect("both Node B resources should be valid at proposal time");
        assert!(!fixture.control.reservations.contains_key(&fixture.space_b));
        assert!(
            !fixture
                .control
                .reservations
                .contains_key(&fixture.space_b_secondary)
        );

        let mission_b_task = requirement_for_mission(
            "mission-b",
            "task-resource-owner",
            "transport-b",
            CapabilityKind::Transport,
        );
        let mission_b_candidates = fixture
            .control
            .match_capabilities(
                &fixture.state,
                &mission_b_task,
                TimestampMs::new(5),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("Mission B should match Node B");
        let mission_b_proposal = fixture
            .control
            .propose(
                &fixture.state,
                &mission_b_task,
                &mission_b_candidates,
                vec![RoleAssignment::new(
                    RoleId::new("transport-b").expect("test role id must be valid"),
                    fixture.node_b_id.clone(),
                    vec![fixture.space_b_secondary.clone()],
                )],
                TimestampMs::new(5),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("Mission B proposal should remain independent");
        fixture
            .control
            .commit(
                &mission_b_proposal,
                TimestampMs::new(5),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("Mission B should reserve the secondary resource");

        assert!(matches!(
            fixture.control.commit_role_recovery(
                &fixture.state,
                &fixture.requirement,
                &proposal,
                TimestampMs::new(6),
                &fixture.correlation_id,
                &mut fixture.events,
            ),
            Err(ControlError::ResourceConflict { resource_id, .. })
                if resource_id == fixture.space_b_secondary
        ));
        assert!(!fixture.control.reservations.contains_key(&fixture.space_b));
        let mission_b_reservation = fixture
            .control
            .reservations
            .get(&fixture.space_b_secondary)
            .expect("Mission B reservation must remain");
        assert_eq!(&mission_b_reservation.task_ref, mission_b_task.task_ref());
        assert!(mission_b_reservation.group_id.is_none());
        let group_a = fixture
            .control
            .group(&fixture.group_id)
            .expect("Mission A Group should remain pending");
        assert_eq!(group_a.lifecycle(), GroupLifecycle::Blocked);
        assert!(group_a.is_role_unbound(&fixture.transport_role));
        assert!(
            fixture
                .control
                .reservations
                .contains_key(&fixture.compute_c)
        );
    }

    /// Rebind rejects a commitment-shaped value when reservation authority has no commitment.
    #[test]
    fn recovery_rebind_without_reservation_commit_is_rejected() {
        let mut fixture = recovery_fixture(true);
        begin_detected_transport_recovery(&mut fixture);
        let uncommitted = CommittedRecoveryAssignment::new(
            fixture.group_id.clone(),
            fixture.task_ref.clone(),
            fixture.transport_role.clone(),
            fixture.node_a_id.clone(),
            fixture.node_b_id.clone(),
            vec![fixture.space_b.clone()],
        );

        assert!(matches!(
            fixture.control.rebind_role(
                &uncommitted,
                TimestampMs::new(3),
                &fixture.correlation_id,
                &mut fixture.events,
            ),
            Err(ControlError::PendingRecoveryCommitmentNotFound { .. })
        ));
        let group = fixture
            .control
            .group(&fixture.group_id)
            .expect("uncommitted rebind must retain Group");
        assert_eq!(group.lifecycle(), GroupLifecycle::Blocked);
        assert!(group.is_role_unbound(&fixture.transport_role));
        assert!(!fixture.control.reservations.contains_key(&fixture.space_b));
    }

    /// Committed rebind is legal only for a Blocked Group with an unbound role.
    #[test]
    fn recovery_rebind_requires_blocked_lifecycle() {
        let mut fixture = recovery_fixture(true);
        let fake_commitment = CommittedRecoveryAssignment::new(
            fixture.group_id.clone(),
            fixture.task_ref.clone(),
            fixture.transport_role.clone(),
            fixture.node_a_id.clone(),
            fixture.node_b_id.clone(),
            vec![fixture.space_b.clone()],
        );
        assert!(matches!(
            fixture.control.rebind_role(
                &fake_commitment,
                TimestampMs::new(1),
                &fixture.correlation_id,
                &mut fixture.events,
            ),
            Err(ControlError::InvalidLifecycle(GroupLifecycle::Active))
        ));

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
            .expect("Blocked committed recovery should rebind");
        assert!(matches!(
            fixture.control.rebind_role(
                &committed,
                TimestampMs::new(7),
                &fixture.correlation_id,
                &mut fixture.events,
            ),
            Err(ControlError::InvalidLifecycle(GroupLifecycle::Adapted))
        ));
    }
