//! Authority consumer tests use attributed software records, never physical device evidence.

use super::*;
use domain::{
    ConflictReducer, DecisionEvidencePolicy, DecisionGate, DecisionGateTarget, DecisionKind,
    DecisionPolicyRegistry, StateCondition, StateObjectClass, StateObjectRef, StateRecord,
    StateSemantic, StateSource,
};

/// Builds a declared source and typed condition without hardcoding any production Node or workflow.
fn registry(node: &str) -> DecisionPolicyRegistry {
    let source = StateSource::Node {
        node_id: NodeId::new(node).unwrap(),
        local_system_id: domain::LocalSystemId::new("software-observer").unwrap(),
    };
    let mut policy = DecisionEvidencePolicy::new(
        "hazard-clear",
        1,
        StateObjectRef::new(StateObjectClass::World, "hazard", "declared-zone").unwrap(),
        "hazard",
        BTreeSet::from([StateSemantic::Observed]),
        BTreeSet::from([source]),
        20,
        None,
        BTreeSet::from([
            DecisionKind::Match,
            DecisionKind::Commit,
            DecisionKind::Rebind,
        ]),
        ConflictReducer::RequireEquivalent,
    )
    .unwrap();
    policy.payload_schema = Some("software.hazard/v1".into());
    DecisionPolicyRegistry {
        schema_version: "roboguide.decision-policy-registry/v0.1".into(),
        registry_id: "deployment-policy".into(),
        revision: 1,
        scheduling_costs: BTreeMap::new(),
        gates: BTreeMap::from([(
            "hazard-clear".into(),
            DecisionGate {
                active_stop_timeout_ms: None,
                tenant_id: "local".into(),
                enabled: true,
                policy,
                target: DecisionGateTarget::default(),
                candidate_source_only: true,
                require_current_session: false,
                condition: StateCondition::Boolean {
                    pointer: "/clear".into(),
                    value: true,
                },
            },
        )]),
    }
}

/// Creates independent original receive-time evidence under the legacy software-source profile.
fn observed(node: &str, clear: bool, at: u64) -> StateRecord {
    StateRecord::new(
        StateObjectRef::new(StateObjectClass::World, "hazard", "declared-zone").unwrap(),
        StateSemantic::Observed,
        StateSource::Node {
            node_id: NodeId::new(node).unwrap(),
            local_system_id: domain::LocalSystemId::new("software-observer").unwrap(),
        },
        "hazard",
        "software.hazard/v1",
        serde_json::json!({"clear":clear}),
        None,
        TimestampMs::new(at),
        20,
        None,
        at,
    )
    .unwrap()
}

/// Matching and Commit read supplied evidence; conflicting/current negative evidence cannot release ownership.
#[test]
fn source_policy_is_consumed_by_matching_commit_and_original_audit() {
    let node = "configured-compute";
    let mut control = ControlPlane::new();
    let mut nodes = InMemorySharedNodeState::new();
    let mut events = RecordingEvents::default();
    control
        .register_node(
            &mut nodes,
            registration(node, CapabilityKind::Compute, "configured-space"),
            NodeStatus::new(NodeHealth::Online, TimestampMs::new(1)),
            TimestampMs::new(1),
            &correlation(),
            &mut events,
        )
        .unwrap();
    control
        .accept_decision_policy_registry(registry(node), 0)
        .unwrap();
    let task = requirement("policy-task", "worker", CapabilityKind::Compute);
    assert!(
        control
            .match_capabilities(
                &nodes,
                &task,
                TimestampMs::new(2),
                &correlation(),
                &mut events
            )
            .is_err()
    );
    let original = observed(node, true, 1);
    let state = state::DecisionStateSnapshot::new(nodes.clone(), vec![original.clone()]);
    let candidates = control
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
            &candidates,
            vec![RoleAssignment::new(
                RoleId::new("worker").unwrap(),
                NodeId::new(node).unwrap(),
                vec![ResourceId::new("configured-space").unwrap()],
            )],
            TimestampMs::new(2),
            &correlation(),
            &mut events,
        )
        .unwrap();
    let changed = state::DecisionStateSnapshot::new(nodes.clone(), vec![observed(node, false, 2)]);
    assert!(
        control
            .commit_with_state(
                &changed,
                &proposal,
                TimestampMs::new(3),
                &correlation(),
                &mut events
            )
            .is_err()
    );
    assert!(control.reservations.is_empty());
    control
        .commit_with_state(
            &state,
            &proposal,
            TimestampMs::new(2),
            &correlation(),
            &mut events,
        )
        .unwrap();
    let receipt = events
        .records
        .iter()
        .find_map(|(_, event)| match event {
            EventPayload::StatePolicyDecision { receipts, .. }
                if receipts
                    .iter()
                    .any(|receipt| receipt.decision == DecisionKind::Commit) =>
            {
                Some(receipts)
            }
            _ => None,
        })
        .unwrap();
    assert_eq!(receipt.len(), 2);
    assert!(
        receipt
            .iter()
            .all(|receipt| receipt.record.as_ref() == Some(&original) && receipt.condition_passed)
    );
    let restored = ControlPlane::restore(control.checkpoint()).unwrap();
    assert_eq!(
        restored.decision_policy_registry(),
        control.decision_policy_registry()
    );
    assert!(
        !restored.evaluate_decision_gates(
            &state,
            &NodeId::new(node).unwrap(),
            &task.roles()[0],
            None,
            DecisionKind::Commit,
            TimestampMs::new(21)
        )[0]
        .condition_passed
    );
    assert_eq!(restored.reservations.len(), 1);
    let foreign = state::DecisionStateSnapshot::new(nodes, vec![observed("other-source", true, 1)]);
    assert!(
        !control.evaluate_decision_gates(
            &foreign,
            &NodeId::new(node).unwrap(),
            &task.roles()[0],
            None,
            DecisionKind::Commit,
            TimestampMs::new(2)
        )[0]
        .condition_passed
    );
}

/// Policy interpretation cannot change under one revision or vanish from retained authority history.
#[test]
fn policy_revision_and_disabled_history_do_not_reset_on_restore() {
    let mut control = ControlPlane::new();
    let original = registry("declared-source");
    control
        .accept_decision_policy_registry(original.clone(), 0)
        .unwrap();
    let mut changed = original.clone();
    changed.gates.get_mut("hazard-clear").unwrap().enabled = false;
    assert!(
        control
            .accept_decision_policy_registry(changed.clone(), 1)
            .is_err()
    );
    changed.revision = 2;
    changed
        .gates
        .get_mut("hazard-clear")
        .unwrap()
        .policy
        .revision = 2;
    control
        .accept_decision_policy_registry(changed.clone(), 1)
        .unwrap();
    let mut restored = ControlPlane::restore(control.checkpoint()).unwrap();
    assert!(
        restored
            .accept_decision_policy_registry(original, 2)
            .is_err()
    );
    assert_eq!(restored.decision_policy_registry(), Some(&changed));
    assert_eq!(restored.decision_policy_history.len(), 2);
}

/// Continuous monitoring is explicit, bounded and cannot be hidden in the original registry generations.
#[test]
fn active_hazard_policy_requires_new_schema_and_readiness_without_downgrade() {
    let mut original = registry("configured-software-source");
    original
        .gates
        .get_mut("hazard-clear")
        .unwrap()
        .active_stop_timeout_ms = Some(100);
    assert!(original.validate().is_err());
    original.schema_version = "roboguide.decision-policy-registry/v0.3".into();
    assert!(original.validate().is_err());
    original
        .gates
        .get_mut("hazard-clear")
        .unwrap()
        .policy
        .allowed_decisions
        .insert(DecisionKind::Readiness);
    original.validate().unwrap();
    let mut next = original.clone();
    next.schema_version = "roboguide.decision-policy-registry/v0.2".into();
    next.revision += 1;
    next.gates
        .get_mut("hazard-clear")
        .unwrap()
        .active_stop_timeout_ms = None;
    next.gates.get_mut("hazard-clear").unwrap().policy.revision += 1;
    assert!(original.validates_successor(&next).is_err());
    for timeout in [0, 3_600_001] {
        let mut invalid = original.clone();
        invalid
            .gates
            .get_mut("hazard-clear")
            .unwrap()
            .active_stop_timeout_ms = Some(timeout);
        assert!(invalid.validate().is_err());
    }
}
