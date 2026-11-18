//! Identity eligibility and checkpoint tests; certificate bytes and device behavior are out of scope.

use super::*;

/// Builds a static-profile Control with a real committed reservation and authenticated session.
fn static_committed_control() -> (ControlPlane, InMemorySharedNodeState) {
    let mut control = ControlPlane::new();
    let mut state = InMemorySharedNodeState::new();
    let mut events = TestEvents;
    control
        .register_node(
            &mut state,
            registration("node", CapabilityKind::Compute, "slot"),
            NodeStatus::new(NodeHealth::Online, TimestampMs::new(1)),
            TimestampMs::new(1),
            &correlation(),
            &mut events,
        )
        .unwrap();
    control
        .register_authenticated_node_session(
            &NodeId::new("node").unwrap(),
            "original-session",
            1000,
            Some("a".repeat(64)),
            Some(10000),
        )
        .unwrap();
    let task = requirement("running", "worker", CapabilityKind::Compute);
    control
        .register_mission_tenant(task.mission_id().clone(), "local")
        .unwrap();
    let matched = control
        .match_capabilities(
            &state,
            &task,
            TimestampMs::new(2),
            &correlation(),
            &mut events,
        )
        .unwrap();
    let proposal = control
        .propose(
            &state,
            &task,
            &matched,
            vec![RoleAssignment::new(
                RoleId::new("worker").unwrap(),
                NodeId::new("node").unwrap(),
                vec![ResourceId::new("slot").unwrap()],
            )],
            TimestampMs::new(2),
            &correlation(),
            &mut events,
        )
        .unwrap();
    control
        .commit_with_state(
            &state,
            &proposal,
            TimestampMs::new(2),
            &correlation(),
            &mut events,
        )
        .unwrap();
    (control, state)
}

/// Adoption retains reservation and session authority, and refuses a historical cross-tenant rewrite.
#[test]
fn static_adoption_preserves_committed_ownership_and_rejects_foreign_tenant() {
    let (mut control, state) = static_committed_control();
    let before = serde_json::to_value(control.checkpoint()).unwrap();
    let allocation = control.allocation_snapshot(TimestampMs::new(3)).unwrap();
    assert!(!allocation.allocations().is_empty());
    let mut command = domain::NodeIdentityAdoptionCommand {
        schema: domain::NODE_IDENTITY_ADOPTION_SCHEMA.into(),
        command_id: "adopt".into(),
        registry_id: "deployment".into(),
        expected_bindings: std::collections::BTreeMap::from([("node".into(), "a".repeat(64))]),
        owners: std::collections::BTreeMap::from([(
            "node".into(),
            domain::NodeIdentityAdoptionOwner {
                tenant_id: "foreign".into(),
                domain_id: "domain".into(),
                expires_at_ms: 0,
            },
        )]),
    };
    assert!(
        control
            .adopt_node_identity_registry(&state, &command, TimestampMs::new(3), "admin")
            .is_err()
    );
    assert_eq!(serde_json::to_value(control.checkpoint()).unwrap(), before);
    command.owners.get_mut("node").unwrap().tenant_id = "local".into();
    let receipt = control
        .adopt_node_identity_registry(&state, &command, TimestampMs::new(3), "admin")
        .unwrap();
    assert_eq!(
        control.current_node_session(&NodeId::new("node").unwrap()),
        Some("original-session")
    );
    assert_eq!(
        control.allocation_snapshot(TimestampMs::new(3)).unwrap(),
        allocation
    );
    assert_eq!(
        control
            .adopt_node_identity_registry(&state, &command, TimestampMs::new(900), "other-admin")
            .unwrap(),
        receipt
    );
    let restored = ControlPlane::restore(control.checkpoint()).unwrap();
    assert_eq!(
        restored.node_identity_registry(),
        control.node_identity_registry()
    );
    assert_eq!(
        restored.allocation_snapshot(TimestampMs::new(3)).unwrap(),
        allocation
    );
}

/// A committed certificate revocation changes real Matching/Commit eligibility while retaining ownership.
#[test]
fn identity_revocation_fences_matching_and_restored_authority() {
    let mut control = ControlPlane::new();
    let mut state = InMemorySharedNodeState::new();
    let mut events = TestEvents;
    let correlation = CorrelationId::new("identity-revocation").unwrap();
    let mut registry = domain::NodeIdentityRegistry::new("deployment");
    registry
        .register("node", "tenant", "domain", "a".repeat(64), 0)
        .unwrap();
    control.bootstrap_node_identity_registry(registry).unwrap();
    let node = registration("node", CapabilityKind::Compute, "cpu");
    control
        .register_node(
            &mut state,
            node,
            NodeStatus::new(NodeHealth::Online, TimestampMs::new(1)),
            TimestampMs::new(1),
            &correlation,
            &mut events,
        )
        .unwrap();
    control
        .register_authenticated_node_session(
            &NodeId::new("node").unwrap(),
            "authenticated-session",
            10_000,
            Some("a".repeat(64)),
            Some(100_000),
        )
        .unwrap();
    let requirement = RoleRequirement::new(
        RoleId::new("worker").unwrap(),
        CapabilityKind::Compute,
        None,
    );
    assert!(control.node_is_eligible_for_role(
        &state,
        &NodeId::new("node").unwrap(),
        &requirement,
        TimestampMs::new(2)
    ));
    let before = control.allocation_snapshot(TimestampMs::new(2)).unwrap();
    let command = domain::IdentityCommand {
        command_id: "revoke-node".into(),
        node_id: "node".into(),
        expected_revision: 1,
        action: domain::IdentityAction::Revoke,
    };
    control
        .apply_node_identity_command(&command, TimestampMs::new(2), "admin")
        .unwrap();
    assert!(!control.node_is_eligible_for_role(
        &state,
        &NodeId::new("node").unwrap(),
        &requirement,
        TimestampMs::new(2)
    ));
    assert_eq!(
        control.node_role_exclusion(
            &state,
            &NodeId::new("node").unwrap(),
            &requirement,
            TimestampMs::new(2)
        ),
        Some(CandidateExclusionReason::IdentityUnavailable)
    );
    assert_eq!(
        control.allocation_snapshot(TimestampMs::new(2)).unwrap(),
        before
    );
    let restored = ControlPlane::restore(control.checkpoint()).unwrap();
    assert_eq!(
        restored.node_identity_registry(),
        control.node_identity_registry()
    );
}

/// Tenant-filtered Matching, Proposal and Commit use the same immutable registry authority.
#[test]
fn tenants_cannot_propose_or_commit_foreign_nodes_and_expiry_retains_ownership() {
    let mut control = ControlPlane::new();
    let mut state = InMemorySharedNodeState::new();
    let mut events = TestEvents;
    let mut registry = domain::NodeIdentityRegistry::new("tenant-registry");
    for (node, tenant, digest) in [("arbitrary-z", "a", "a"), ("arbitrary-first", "b", "b")] {
        registry
            .register(node, tenant, "domain", digest.repeat(64), 0)
            .unwrap();
    }
    control.bootstrap_node_identity_registry(registry).unwrap();
    for (node, digest) in [("arbitrary-first", "b"), ("arbitrary-z", "a")] {
        control
            .register_node(
                &mut state,
                registration(node, CapabilityKind::Compute, &format!("space-{node}")),
                NodeStatus::new(NodeHealth::Online, TimestampMs::new(1)),
                TimestampMs::new(1),
                &correlation(),
                &mut events,
            )
            .unwrap();
        control
            .register_authenticated_node_session(
                &NodeId::new(node).unwrap(),
                node,
                1000,
                Some(digest.repeat(64)),
                Some(100),
            )
            .unwrap();
    }
    let task = requirement("tenant-task", "worker", CapabilityKind::Compute);
    control
        .register_mission_tenant(task.mission_id().clone(), "a")
        .unwrap();
    let matched = control
        .match_capabilities(
            &state,
            &task,
            TimestampMs::new(2),
            &correlation(),
            &mut events,
        )
        .unwrap();
    assert_eq!(
        matched
            .for_role(&RoleId::new("worker").unwrap())
            .unwrap()
            .node_ids(),
        &[NodeId::new("arbitrary-z").unwrap()]
    );
    let foreign = RoleAssignment::new(
        RoleId::new("worker").unwrap(),
        NodeId::new("arbitrary-first").unwrap(),
        vec![ResourceId::new("space-arbitrary-first").unwrap()],
    );
    assert!(
        control
            .propose(
                &state,
                &task,
                &matched,
                vec![foreign],
                TimestampMs::new(2),
                &correlation(),
                &mut events
            )
            .is_err()
    );
    let proposal = control
        .propose(
            &state,
            &task,
            &matched,
            vec![RoleAssignment::new(
                RoleId::new("worker").unwrap(),
                NodeId::new("arbitrary-z").unwrap(),
                vec![ResourceId::new("space-arbitrary-z").unwrap()],
            )],
            TimestampMs::new(2),
            &correlation(),
            &mut events,
        )
        .unwrap();
    control
        .commit_with_state(
            &state,
            &proposal,
            TimestampMs::new(2),
            &correlation(),
            &mut events,
        )
        .unwrap();
    let before = control.allocation_snapshot(TimestampMs::new(2)).unwrap();
    assert!(!before.allocations().is_empty());
    assert!(!control.node_is_eligible_for_role(
        &state,
        &NodeId::new("arbitrary-z").unwrap(),
        &task.roles()[0],
        TimestampMs::new(100)
    ));
    assert_eq!(
        control.allocation_snapshot(TimestampMs::new(2)).unwrap(),
        before
    );
    let restored = ControlPlane::restore(control.checkpoint()).unwrap();
    assert_eq!(restored.mission_tenant(task.mission_id()), Some("a"));
    assert!(
        control
            .register_mission_tenant(task.mission_id().clone(), "b")
            .is_err()
    );
}
