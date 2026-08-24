    /// Blocked preserves the Group and every binding until recovery acts explicitly.
    #[test]
    fn blocked_does_not_release_whole_group() {
        let mut fixture = recovery_fixture(true);
        block_fixture(&mut fixture);

        let group = fixture
            .control
            .group(&fixture.group_id)
            .expect("blocked Group should remain");
        assert_eq!(group.group_id(), &fixture.group_id);
        assert_eq!(group.task_ref(), &fixture.task_ref);
        assert_eq!(group.lifecycle(), GroupLifecycle::Blocked);
        assert!(group.assignments().iter().any(|assignment| {
            assignment.role_id() == &fixture.compute_role
                && assignment.resource_ids() == std::slice::from_ref(&fixture.compute_c)
        }));
        assert_eq!(
            fixture
                .control
                .reservations
                .get(&fixture.compute_c)
                .and_then(|reservation| reservation.group_id.as_ref()),
            Some(&fixture.group_id)
        );
        assert!(
            !fixture
                .events
                .records
                .iter()
                .any(|(_, payload)| matches!(payload, EventPayload::ExecutionGroupReleased { .. }))
        );
    }

    /// Partial release removes only the failed role's assignment and reservations.
    #[test]
    fn partial_release_preserves_unaffected_bindings() {
        let mut fixture = recovery_fixture(true);
        block_fixture(&mut fixture);
        fixture
            .control
            .release_role_binding(
                &fixture.group_id,
                &fixture.transport_role,
                TimestampMs::new(2),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("failed transport binding should release");

        let group = fixture
            .control
            .group(&fixture.group_id)
            .expect("partially released Group should remain");
        assert_eq!(group.lifecycle(), GroupLifecycle::Blocked);
        assert!(group.is_role_unbound(&fixture.transport_role));
        assert!(!fixture.control.reservations.contains_key(&fixture.space_a));
        assert!(
            fixture
                .control
                .reservations
                .contains_key(&fixture.compute_c)
        );
        assert_eq!(group.assignments().len(), 1);
        assert_eq!(group.assignments()[0].role_id(), &fixture.compute_role);
        assert!(
            fixture
                .events
                .records
                .iter()
                .any(|(correlation_id, payload)| {
                    correlation_id == &fixture.correlation_id
                        && matches!(
                            payload,
                            EventPayload::ExecutionGroupRoleBindingReleased {
                                group_id,
                                task_ref,
                                role_id,
                                resource_ids,
                                ..
                            } if group_id == &fixture.group_id
                                && task_ref == &fixture.task_ref
                                && role_id == &fixture.transport_role
                                && resource_ids == std::slice::from_ref(&fixture.space_a)
                        )
                })
        );
    }

    /// Partial release is legal only after the Group explicitly becomes Blocked.
    #[test]
    fn partial_release_requires_blocked_lifecycle() {
        let mut fixture = recovery_fixture(true);
        assert!(matches!(
            fixture.control.release_role_binding(
                &fixture.group_id,
                &fixture.transport_role,
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
            .expect("blocked Group should recover to Adapted");
        assert!(matches!(
            fixture.control.release_role_binding(
                &fixture.group_id,
                &fixture.compute_role,
                TimestampMs::new(7),
                &fixture.correlation_id,
                &mut fixture.events,
            ),
            Err(ControlError::InvalidLifecycle(GroupLifecycle::Adapted))
        ));
    }

    /// A blocked Group rebinds only the failed role and reactivates in place.
    #[test]
    fn blocked_group_recovers_through_adapted_and_active() {
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
            .expect("released role should rebind to Node B");

        let adapted = fixture
            .control
            .group(&fixture.group_id)
            .expect("adapted Group should retain identity");
        assert_eq!(adapted.group_id(), &fixture.group_id);
        assert_eq!(adapted.task_ref(), &fixture.task_ref);
        assert_eq!(adapted.lifecycle(), GroupLifecycle::Adapted);
        assert!(!adapted.is_role_unbound(&fixture.transport_role));
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
        assert!(
            fixture
                .control
                .reservations
                .contains_key(&fixture.compute_c)
        );
        assert!(fixture.control.reservations.contains_key(&fixture.space_b));
        fixture
            .control
            .activate_group(
                &fixture.group_id,
                TimestampMs::new(7),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("fully rebound Group should reactivate");
        assert_eq!(
            fixture
                .control
                .group(&fixture.group_id)
                .expect("reactivated Group should remain")
                .lifecycle(),
            GroupLifecycle::Active
        );
        assert!(
            fixture
                .events
                .records
                .iter()
                .any(|(correlation_id, payload)| {
                    correlation_id == &fixture.correlation_id
                        && matches!(
                            payload,
                            EventPayload::RecoveryRebound {
                                group_id,
                                task_ref,
                                role_id,
                                from_node,
                                to_node,
                            } if group_id == &fixture.group_id
                                && task_ref == &fixture.task_ref
                                && role_id == &fixture.transport_role
                                && from_node == &fixture.node_a_id
                                && to_node == &fixture.node_b_id
                        )
                })
        );
    }

    /// Exhausted recovery explicitly enters Failed before whole-group release.
    #[test]
    fn recovery_exhausted_transitions_through_failed() {
        let mut fixture = recovery_fixture(false);
        block_fixture(&mut fixture);
        fixture
            .control
            .release_role_binding(
                &fixture.group_id,
                &fixture.transport_role,
                TimestampMs::new(2),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("failed transport binding should release");
        fixture
            .state
            .record_node_health(NodeHealthObservation::new(
                fixture.node_a_id.clone(),
                NodeStatus::new(NodeHealth::Offline, TimestampMs::new(3)),
                TimestampMs::new(3),
            ))
            .expect("offline observation should update Shared State");
        assert!(matches!(
            fixture.control.match_capabilities(
                &fixture.state,
                &fixture.requirement,
                TimestampMs::new(3),
                &fixture.correlation_id,
                &mut fixture.events,
            ),
            Err(ControlError::NoCandidate(role_id)) if role_id == fixture.transport_role
        ));
        fixture
            .control
            .fail_group(
                &fixture.group_id,
                "no replacement candidate",
                TimestampMs::new(4),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("blocked Group should explicitly fail");
        assert_eq!(
            fixture
                .control
                .group(&fixture.group_id)
                .expect("failed Group should remain observable")
                .lifecycle(),
            GroupLifecycle::Failed
        );
        assert!(fixture.events.records.iter().any(|(_, payload)| matches!(
            payload,
            EventPayload::ExecutionGroupFailed { group_id, task_ref, .. }
                if group_id == &fixture.group_id && task_ref == &fixture.task_ref
        )));
        fixture
            .control
            .release_group(
                &fixture.group_id,
                TimestampMs::new(5),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("failed Group should release remaining bindings");
        assert_eq!(
            fixture
                .control
                .group(&fixture.group_id)
                .expect("released Group should remain observable")
                .lifecycle(),
            GroupLifecycle::Released
        );
        assert!(!fixture.control.reservations.contains_key(&fixture.space_a));
        assert!(
            !fixture
                .control
                .reservations
                .contains_key(&fixture.compute_c)
        );
    }

    /// Completed releases every remaining reservation and emits whole-group evidence.
    #[test]
    fn completed_group_releases_all_bindings() {
        let mut fixture = recovery_fixture(true);
        fixture
            .control
            .complete_group(
                &fixture.group_id,
                TimestampMs::new(1),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("active Group should complete");
        fixture
            .control
            .release_group(
                &fixture.group_id,
                TimestampMs::new(2),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("completed Group should release");

        let group = fixture
            .control
            .group(&fixture.group_id)
            .expect("released Group should remain observable");
        assert_eq!(group.lifecycle(), GroupLifecycle::Released);
        assert!(group.assignments().is_empty());
        assert!(!fixture.control.reservations.contains_key(&fixture.space_a));
        assert!(
            !fixture
                .control
                .reservations
                .contains_key(&fixture.compute_c)
        );
        assert!(fixture.events.records.iter().any(|(_, payload)| matches!(
            payload,
            EventPayload::ExecutionGroupReleased { group_id, task_ref, resource_ids }
                if group_id == &fixture.group_id
                    && task_ref == &fixture.task_ref
                    && resource_ids.contains(&fixture.space_a)
                    && resource_ids.contains(&fixture.compute_c)
        )));
    }

    /// Blocked rejects direct whole-group release and retains all reservations.
    #[test]
    fn blocked_group_cannot_release_directly() {
        let mut fixture = recovery_fixture(true);
        block_fixture(&mut fixture);
        assert!(matches!(
            fixture.control.release_group(
                &fixture.group_id,
                TimestampMs::new(2),
                &fixture.correlation_id,
                &mut fixture.events,
            ),
            Err(ControlError::InvalidLifecycle(GroupLifecycle::Blocked))
        ));
        assert!(fixture.control.reservations.contains_key(&fixture.space_a));
        assert!(
            fixture
                .control
                .reservations
                .contains_key(&fixture.compute_c)
        );
        assert!(
            !fixture
                .events
                .records
                .iter()
                .any(|(_, payload)| matches!(payload, EventPayload::ExecutionGroupReleased { .. }))
        );
    }
