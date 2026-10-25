//! Synthetic policy/selection regressions; these create no deployment or device facts.

use super::*;
use domain::{
    ConflictReducer, DecisionEvidencePolicy, DecisionKind, DecisionPolicyRegistry,
    SchedulingCostFailure, SchedulingCostPolicy, SchedulingCostTerm, SchedulingMetric,
    StateObjectClass, StateObjectRef, StateRecord, StateSemantic, StateSource,
};

/// Builds explicit source admission; every test Node name belongs only to the offline test fixture.
fn cost(node: &str, higher: bool) -> SchedulingCostPolicy {
    let mut policy = DecisionEvidencePolicy::new(
        format!("cost-{node}"),
        1,
        StateObjectRef::new(StateObjectClass::Node, "scheduling", node).unwrap(),
        "cost",
        BTreeSet::from([StateSemantic::Observed]),
        BTreeSet::from([StateSource::Node {
            node_id: NodeId::new(node).unwrap(),
            local_system_id: domain::LocalSystemId::new("actual-source").unwrap(),
        }]),
        20,
        None,
        BTreeSet::from([DecisionKind::Schedule]),
        ConflictReducer::RequireEquivalent,
    )
    .unwrap();
    policy.payload_schema = Some(domain::SCHEDULING_METRICS_SCHEMA.into());
    SchedulingCostPolicy {
        tenant_id: "local".into(),
        enabled: true,
        node_id: NodeId::new(node).unwrap(),
        policy,
        terms: vec![SchedulingCostTerm {
            metric: if higher {
                SchedulingMetric::GpuMemoryBytes
            } else {
                SchedulingMetric::LoadMillionths
            },
            weight: 1,
            units_per_cost: 1,
            maximum: 10_000_000,
            prefer_higher: higher,
        }],
    }
}

/// Creates one immutable attributed observation at a known virtual receive clock and source epoch.
fn observed(
    policy: &SchedulingCostPolicy,
    load: Option<u64>,
    gpu: Option<u64>,
    epoch: &str,
) -> StateRecord {
    StateRecord::new_with_source_epoch(policy.policy.object.clone(), StateSemantic::Observed,
        policy.policy.allowed_sources.iter().next().unwrap().clone(), "cost", domain::SCHEDULING_METRICS_SCHEMA,
        serde_json::json!({"schema_version":domain::SCHEDULING_METRICS_SCHEMA,"queue_delay_ms":null,"travel_distance_um":null,
            "traffic_delay_ms":null,"load_millionths":load,"gpu_memory_bytes":gpu}), None, TimestampMs::new(10), 20,
        None, Some(epoch.into()), 1).unwrap()
}

/// Installs explicit policies and virtual Node sessions without invoking any provider or device.
fn fixture(
    policies: Vec<SchedulingCostPolicy>,
) -> (ControlPlane, InMemorySharedNodeState, domain::MissionId) {
    let mut control = ControlPlane::new();
    let mut state = InMemorySharedNodeState::new();
    let mission = domain::MissionId::new("cost-unit-mission").unwrap();
    control
        .register_mission_tenant(mission.clone(), "local")
        .unwrap();
    for policy in &policies {
        control
            .register_node(
                &mut state,
                registration(
                    policy.node_id.as_str(),
                    CapabilityKind::Compute,
                    &format!("{}-slot", policy.node_id),
                ),
                NodeStatus::new(NodeHealth::Online, TimestampMs::new(10)),
                TimestampMs::new(10),
                &correlation(),
                &mut RecordingEvents::default(),
            )
            .unwrap();
        control
            .register_node_session(&policy.node_id, "unit-original-session", 15000)
            .unwrap();
    }
    control
        .accept_decision_policy_registry(
            DecisionPolicyRegistry {
                schema_version: "roboguide.decision-policy-registry/v0.2".into(),
                registry_id: "unit-cost-registry".into(),
                revision: 1,
                gates: BTreeMap::new(),
                scheduling_costs: policies
                    .into_iter()
                    .map(|policy| (policy.policy.policy_id.clone(), policy))
                    .collect(),
            },
            0,
        )
        .unwrap();
    (control, state, mission)
}

/// Real policy use ranks larger available memory lower, while null never becomes free GPU capacity.
#[test]
fn cost_ordering_keeps_unavailable_and_original_evidence() {
    let policies = vec![
        cost("unit-a", true),
        cost("unit-b", true),
        cost("unit-c", true),
    ];
    let (control, nodes, mission) = fixture(policies.clone());
    let records = policies
        .iter()
        .zip([Some(10), Some(100), None])
        .map(|(policy, gpu)| observed(policy, Some(0), gpu, "unit-original-session"))
        .collect();
    let state = state::DecisionStateSnapshot::new(nodes, records);
    let receipts = control.scheduling_cost_assessments(&state, &mission, TimestampMs::new(11));
    assert_eq!(receipts.len(), 3);
    assert_eq!(
        receipts[2].failure,
        Some(SchedulingCostFailure::MetricUnavailable)
    );
    let snapshot = SchedulingSnapshot::empty()
        .with_policy_cost_evidence(receipts.clone())
        .unwrap();
    assert!(
        snapshot.candidate_cost(&policies[1].node_id, TimestampMs::new(11))
            < snapshot.candidate_cost(&policies[0].node_id, TimestampMs::new(11))
    );
    assert_eq!(
        snapshot.candidate_cost(&policies[2].node_id, TimestampMs::new(11)),
        u128::MAX
    );
    assert_eq!(
        snapshot.candidate_cost(&policies[1].node_id, TimestampMs::new(30)),
        u128::MAX
    );
    let mut changed = receipts.clone();
    changed[0].score = Some(0);
    assert!(
        SchedulingSnapshot::empty()
            .with_policy_cost_evidence(changed)
            .is_err()
    );
    let task = requirement_for_mission(
        mission.as_str(),
        "unit-work",
        "worker",
        CapabilityKind::Compute,
    );
    let candidates = control
        .match_capabilities(
            &state,
            &task,
            TimestampMs::new(11),
            &correlation(),
            &mut RecordingEvents::default(),
        )
        .unwrap();
    let outcome = BoundedJointScheduler::new()
        .schedule_task_with_snapshot(
            &state,
            &task,
            &candidates,
            &snapshot,
            TimestampMs::new(11),
            TimestampMs::new(11),
        )
        .unwrap();
    let TaskSchedulingOutcome::SelectedNow(decision) = outcome else {
        panic!("current unit selection expected")
    };
    assert_eq!(decision.selections()[0].node_id(), &policies[1].node_id);
    assert_eq!(decision.policy_cost_evidence(), receipts);
    let original = serde_json::to_value(control.checkpoint()).unwrap();
    control
        .validate_scheduling_cost_history(&mission, &receipts)
        .unwrap();
    assert_eq!(
        serde_json::to_value(control.checkpoint()).unwrap(),
        original
    );
}

/// Stale, old-session, conflicting-source and foreign-tenant observations retain explicit unknown.
#[test]
fn cost_sources_cannot_borrow_sessions_tenants_or_receive_freshness() {
    let policy = cost("unit-a", false);
    let (control, nodes, mission) = fixture(vec![policy.clone()]);
    for (epoch, now) in [("old-session", 11), ("unit-original-session", 30)] {
        let state = state::DecisionStateSnapshot::new(
            nodes.clone(),
            vec![observed(&policy, Some(1), None, epoch)],
        );
        let receipts = control.scheduling_cost_assessments(&state, &mission, TimestampMs::new(now));
        assert_eq!(receipts[0].score, None);
        assert_ne!(
            receipts[0].disposition,
            domain::EvidenceDisposition::Accepted
        );
    }
    let foreign = domain::MissionId::new("unknown-tenant").unwrap();
    assert!(
        control
            .scheduling_cost_assessments(&nodes, &foreign, TimestampMs::new(11))
            .is_empty()
    );
    let mut restored = ControlPlane::restore(control.checkpoint()).unwrap();
    let state = state::DecisionStateSnapshot::new(
        nodes.clone(),
        vec![observed(&policy, Some(1), None, "unit-original-session")],
    );
    assert_eq!(
        restored.scheduling_cost_assessments(&state, &mission, TimestampMs::new(11))[0].score,
        None
    );
    let mut disabled = control.decision_policy_registry().unwrap().clone();
    disabled.revision = 2;
    let policy = disabled.scheduling_costs.values_mut().next().unwrap();
    policy.enabled = false;
    policy.policy.revision = 2;
    restored
        .accept_decision_policy_registry(disabled, 1)
        .unwrap();
    assert!(
        restored
            .scheduling_cost_assessments(&state, &mission, TimestampMs::new(11))
            .is_empty()
    );
}

/// Shared registry updates retain original authority and reject downgrade, duplicate candidates and reinterpretation.
#[test]
fn cost_policy_versions_and_numeric_bounds_are_closed() {
    let (control, _, _) = fixture(vec![cost("unit-a", false)]);
    let original = control.decision_policy_registry().unwrap().clone();
    let mut next = original.clone();
    next.schema_version = "roboguide.decision-policy-registry/v0.1".into();
    next.revision = 2;
    next.scheduling_costs.clear();
    assert!(original.validates_successor(&next).is_err());
    next = original.clone();
    next.revision = 2;
    next.scheduling_costs.values_mut().next().unwrap().terms[0].prefer_higher = true;
    assert!(original.validates_successor(&next).is_err());
    let mut duplicate = cost("unit-a", false);
    duplicate.policy.policy_id = "different-id".into();
    next.scheduling_costs
        .insert("different-id".into(), duplicate);
    assert!(next.validate().is_err());
    let mut invalid = cost("unit-a", false);
    invalid.terms[0].maximum = 1 << 50;
    invalid.terms[0].weight = 1_000_000;
    assert!(invalid.validate().is_err());
    let mut legacy = original.clone();
    legacy.schema_version = "roboguide.decision-policy-registry/v0.1".into();
    legacy.scheduling_costs.clear();
    assert!(
        serde_json::to_value(&legacy)
            .unwrap()
            .get("scheduling_costs")
            .is_none()
    );
}

/// Every actual selection audit preserves source receive time and exact original normalization history.
#[test]
fn cost_audit_is_durable_original_evidence_without_resource_claims() {
    let policy = cost("unit-a", false);
    let (control, nodes, mission) = fixture(vec![policy.clone()]);
    let state = state::DecisionStateSnapshot::new(
        nodes,
        vec![observed(&policy, Some(100), None, "unit-original-session")],
    );
    let evidence = control.scheduling_cost_assessments(&state, &mission, TimestampMs::new(11));
    let task = TaskRef::new(mission.clone(), domain::TaskId::new("unit-task").unwrap());
    let mut events = RecordingEvents::default();
    control
        .audit_scheduling_costs(
            &task,
            &evidence,
            TimestampMs::new(11),
            &correlation(),
            &mut events,
        )
        .unwrap();
    assert_eq!(events.records.len(), 1);
    assert!(
        matches!(&events.records[0].1,EventPayload::StatePolicyDecision{receipts,..} if receipts[0].decision==DecisionKind::Schedule && receipts[0].record.as_ref().unwrap().received_at()==TimestampMs::new(10))
    );
    assert!(
        control
            .allocation_snapshot(TimestampMs::new(11))
            .unwrap()
            .allocations()
            .is_empty()
    );
    let mut corrupt = evidence.clone();
    corrupt[0].registry_digest = "sha256:".to_string() + &"0".repeat(64);
    assert!(
        control
            .audit_scheduling_costs(
                &task,
                &corrupt,
                TimestampMs::new(11),
                &correlation(),
                &mut events
            )
            .is_err()
    );
    assert_eq!(events.records.len(), 1);
}
