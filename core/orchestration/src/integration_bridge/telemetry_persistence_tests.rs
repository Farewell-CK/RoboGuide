//! Production ingestion, visibility, replay and policy-fence regressions for telemetry sampling.

use super::*;
use integration::grpc::v0_4 as wire;

/// Registers one ordinary export and one safety export through the production reducer.
fn fixture() -> IntegrationRuntimeBridge<testkit::InMemoryEventLog> {
    let mut bridge = IntegrationRuntimeBridge::new(
        ControlPlane::new(),
        InMemorySharedNodeState::new(),
        testkit::InMemoryEventLog::new(),
        GrpcNodeRouter::default(),
    );
    bridge.set_telemetry_persistence(
        TelemetryPersistencePolicy::from_json(
            br#"{"schemas":{"example.diagnostics/v1":["observed_at_ns"]}}"#,
        )
        .unwrap(),
    );
    bridge
        .consume(
            GrpcNodeEvent::Registered {
                certificate_digest: None,
                certificate_expires_at_ms: None,
                session_id: "session".into(),
                lease_id: "lease".into(),
                registration: wire::NodeRegistration {
                    node_id: "worker".into(),
                    node_contract_version: "roboguide.node.v0.3".into(),
                    local_systems: vec![wire::LocalSystemDescriptor {
                        id: "observer".into(),
                        runtime: Some(wire::LocalRuntime {
                            name: "test".into(),
                            version: "1".into(),
                        }),
                        ..Default::default()
                    }],
                    state_exports: [
                        ("diag", "example.diagnostics/v1"),
                        ("pilot-task-safety", domain::SAFE_STATE_OBSERVATION_SCHEMA),
                    ]
                    .into_iter()
                    .map(|(id, schema)| wire::StateExportDescriptor {
                        export_id: id.into(),
                        local_system_id: "observer".into(),
                        object_class: wire::StateObjectClass::Node as i32,
                        object_type: "diagnostic".into(),
                        object_id: "worker".into(),
                        semantic: if id == "diag" {
                            wire::StateSemantic::Reported
                        } else {
                            wire::StateSemantic::Observed
                        } as i32,
                        payload_schema: schema.into(),
                        valid_for_ms: 1500,
                    })
                    .collect(),
                    ..Default::default()
                },
            },
            TimestampMs::new(1),
            &CorrelationId::new("sampling-test").unwrap(),
        )
        .unwrap();
    bridge
}

/// Delivers one source-clocked telemetry sample through the admitted Node session.
fn sample(
    bridge: &mut IntegrationRuntimeBridge<testkit::InMemoryEventLog>,
    channel: &str,
    time: u64,
    value: u64,
) {
    bridge.consume(GrpcNodeEvent::NodeMessage {
        node_id: "worker".into(),
        session_id: "session".into(),
        message: wire::NodeMessage { message: Some(wire::node_message::Message::StateObservationBatch(
            wire::StateObservationBatch {
                session_id: "session".into(),
                sequence: time,
                observations: vec![wire::StateObservation {
                    export_id: channel.into(),
                    json_value: serde_json::to_vec(&serde_json::json!({"reading":value,"sample":{"observed_at_ns":time}})).unwrap(),
                    has_source_observed_at: true,
                    source_observed_at_ms: time,
                    ..Default::default()
                }],
            },
        )) },
    }, TimestampMs::new(time), &CorrelationId::new("sampling-test").unwrap()).unwrap();
}

/// An unchanged sample refreshes only the live view. Change and interval retain the original sample.
#[test]
fn changed_or_due_telemetry_persists_without_staling_the_live_view() {
    let mut bridge = fixture();
    sample(&mut bridge, "diag", 2, 1);
    let before = bridge.checkpoint_json().unwrap();
    sample(&mut bridge, "diag", 3, 1);
    assert_eq!(bridge.checkpoint_json().unwrap(), before);
    assert_eq!(
        bridge.state_records().records()[0].source_observed_at(),
        Some(TimestampMs::new(3))
    );
    assert!(bridge.decision_evidence_records().is_empty());
    let restored = IntegrationRuntimeBridge::restore_from_checkpoint(
        &before,
        testkit::InMemoryEventLog::new(),
        GrpcNodeRouter::default(),
        TimestampMs::new(4),
    )
    .unwrap();
    assert_eq!(
        restored.state_records().records()[0].received_at(),
        TimestampMs::new(2)
    );
    sample(&mut bridge, "diag", 5, 2);
    assert_eq!(
        bridge.checkpoint_state_records()[0].received_at(),
        TimestampMs::new(5)
    );
    sample(&mut bridge, "diag", 30_004, 2);
    assert_eq!(
        bridge.checkpoint_state_records()[0].received_at(),
        TimestampMs::new(5)
    );
    sample(&mut bridge, "diag", 30_005, 2);
    assert_eq!(
        bridge.checkpoint_state_records()[0].received_at(),
        TimestampMs::new(30_005)
    );
    assert_eq!(
        bridge
            .event_sink()
            .records()
            .iter()
            .filter(|event| matches!(event.payload(), EventPayload::StateRecordObserved { .. }))
            .count(),
        3
    );
}

/// The safety codec is forbidden in the filter and retains every admitted original sample.
#[test]
fn safe_state_observations_bypass_periodic_sampling() {
    assert!(
        TelemetryPersistencePolicy::from_json(
            br#"{"schemas":{"roboguide.safe-state-observation/v0.2":[]}}"#,
        )
        .is_err()
    );
    let mut bridge = fixture();
    sample(&mut bridge, "pilot-task-safety", 2, 1);
    sample(&mut bridge, "pilot-task-safety", 3, 1);
    assert_eq!(
        bridge.decision_evidence_records()[0].received_at(),
        TimestampMs::new(3)
    );
    assert_eq!(
        bridge
            .event_sink()
            .records()
            .iter()
            .filter(|event| matches!(event.payload(), EventPayload::StateRecordObserved { .. }))
            .count(),
        2
    );
}

/// Once a registry names a channel, its next sample must be durably available to its gate.
#[test]
fn decision_registry_protects_even_a_configured_diagnostics_schema() {
    let mut bridge = fixture();
    sample(&mut bridge, "diag", 2, 1);
    sample(&mut bridge, "diag", 3, 1);
    let previous = bridge.checkpoint_state_records().remove(0);
    let mut policy = domain::DecisionEvidencePolicy::new(
        "diagnostic-example",
        1,
        previous.key().object().clone(),
        "diag",
        BTreeSet::from([StateSemantic::Reported]),
        BTreeSet::from([previous.key().source().clone()]),
        1500,
        None,
        BTreeSet::from([domain::DecisionKind::Readiness]),
        domain::ConflictReducer::RequireEquivalent,
    )
    .unwrap();
    policy.payload_schema = Some(previous.payload_schema().into());
    bridge
        .control_mut()
        .accept_decision_policy_registry(
            domain::DecisionPolicyRegistry {
                schema_version: "roboguide.decision-policy-registry/v0.1".into(),
                registry_id: "telemetry-test".into(),
                revision: 1,
                scheduling_costs: BTreeMap::new(),
                gates: BTreeMap::from([(
                    "diagnostic-example".into(),
                    domain::DecisionGate {
                        tenant_id: "local".into(),
                        active_stop_timeout_ms: None,
                        enabled: true,
                        policy,
                        target: domain::DecisionGateTarget::default(),
                        candidate_source_only: true,
                        require_current_session: false,
                        condition: domain::StateCondition::Boolean {
                            pointer: "/ready".into(),
                            value: true,
                        },
                    },
                )]),
            },
            0,
        )
        .unwrap();
    assert!(bridge.decision_evidence_records().is_empty());
    sample(&mut bridge, "diag", 4, 1);
    assert_eq!(
        bridge.decision_evidence_records()[0].received_at(),
        TimestampMs::new(4)
    );
    assert_eq!(
        bridge.checkpoint_state_records()[0].received_at(),
        TimestampMs::new(4)
    );
}
