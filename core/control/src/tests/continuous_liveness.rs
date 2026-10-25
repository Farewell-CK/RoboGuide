//! Continuous lease evidence with explicit silence, replay and process-restart fences.

use super::*;

/// Builds one current process session whose reported health retains its original receive time.
fn fixture() -> (
    ControlPlane,
    InMemorySharedNodeState,
    RecordingEvents,
    domain::NodeSessionLivenessEvidence,
) {
    let mut control = ControlPlane::new();
    let mut state = InMemorySharedNodeState::new();
    let mut events = RecordingEvents::default();
    let registration = registration(
        "continuous-node",
        CapabilityKind::Compute,
        "continuous-resource",
    );
    let node = registration.node_id().clone();
    let lease = NodeLease::new(
        LeaseId::new("continuous-lease").unwrap(),
        node.clone(),
        TimestampMs::new(0),
        15_000,
    )
    .unwrap();
    control
        .register_node_with_lease(
            &mut state,
            registration,
            NodeStatus::new(NodeHealth::Online, TimestampMs::new(99_999)),
            lease,
            TimestampMs::new(0),
            &trace(),
            &mut events,
        )
        .unwrap();
    control
        .register_node_session(&node, "continuous-session", 15_000)
        .unwrap();
    let evidence = domain::NodeSessionLivenessEvidence {
        node_id: node,
        session_id: "continuous-session".into(),
        lease_id: LeaseId::new("continuous-lease").unwrap(),
        activated_at: TimestampMs::new(0),
        first_sequence: 1,
        last_sequence: 5,
        first_received_at: TimestampMs::new(1_000),
        last_received_at: TimestampMs::new(21_000),
        maximum_gap_ms: 5_000,
    };
    (control, state, events, evidence)
}

/// Continuous, delayed evidence extends lease while old health is stale and no heartbeat is ACKed.
#[test]
fn continuous_ingress_renews_control_without_refreshing_health() {
    let (mut control, mut state, mut events, evidence) = fixture();
    assert!(
        control
            .accept_session_liveness(
                &state,
                &evidence,
                TimestampMs::new(22_000),
                &trace(),
                &mut events
            )
            .unwrap()
    );
    assert!(
        control
            .expire_leases(&mut state, TimestampMs::new(22_000), &trace(), &mut events)
            .unwrap()
            .is_empty()
    );
    assert_eq!(
        control.node_lease(&evidence.node_id).unwrap().expires_at(),
        TimestampMs::new(36_000)
    );
    assert_eq!(
        state
            .node(&evidence.node_id)
            .unwrap()
            .reported_status_received_at(),
        TimestampMs::new(0)
    );
    assert_eq!(
        state
            .node(&evidence.node_id)
            .unwrap()
            .reported_status()
            .observed_at(),
        TimestampMs::new(99_999),
        "source clock is unrelated to local liveness"
    );
    control
        .accept_received_heartbeat(
            &mut state,
            NodeHeartbeat::new(
                evidence.node_id.clone(),
                evidence.lease_id.clone(),
                NodeStatus::new(NodeHealth::Online, TimestampMs::new(999_999)),
            ),
            TimestampMs::new(1_000),
            TimestampMs::new(22_000),
            15_000,
            &trace(),
            &mut events,
        )
        .unwrap();
    assert_eq!(
        control.node_lease(&evidence.node_id).unwrap().expires_at(),
        TimestampMs::new(36_000),
        "queued health cannot regress continuous lease"
    );
    assert_eq!(
        state
            .node(&evidence.node_id)
            .unwrap()
            .reported_status_received_at(),
        TimestampMs::new(1_000)
    );
    let requirement = requirement("health-stale", "worker", CapabilityKind::Compute);
    assert!(matches!(
        control.match_capabilities(
            &state,
            &requirement,
            TimestampMs::new(22_000),
            &trace(),
            &mut events
        ),
        Err(ControlError::NoCandidate(_))
    ));
}

/// A queued command's stale receive time cannot authorize expired health and lease at decision.
#[test]
fn queued_decision_must_recheck_expired_health_and_lease_without_renewal() {
    let (mut control, mut state, mut events, mut evidence) = fixture();
    evidence.last_sequence = 2;
    evidence.first_received_at = TimestampMs::new(1_000);
    evidence.last_received_at = TimestampMs::new(2_000);
    evidence.maximum_gap_ms = 1_000;
    control
        .accept_session_liveness(
            &state,
            &evidence,
            TimestampMs::new(2_000),
            &trace(),
            &mut events,
        )
        .unwrap();
    control
        .accept_received_heartbeat(
            &mut state,
            NodeHeartbeat::new(
                evidence.node_id.clone(),
                evidence.lease_id.clone(),
                NodeStatus::new(NodeHealth::Online, TimestampMs::new(99_999)),
            ),
            TimestampMs::new(2_000),
            TimestampMs::new(2_000),
            15_000,
            &trace(),
            &mut events,
        )
        .unwrap();
    let required = requirement("queued-work", "worker", CapabilityKind::Compute);
    assert!(
        control
            .match_capabilities(
                &state,
                &required,
                TimestampMs::new(1_500),
                &trace(),
                &mut events,
            )
            .is_ok()
    );
    assert!(matches!(
        control.match_capabilities(
            &state,
            &required,
            TimestampMs::new(20_000),
            &trace(),
            &mut events,
        ),
        Err(ControlError::NoCandidate(_))
    ));
    assert_eq!(
        control.node_lease(&evidence.node_id).unwrap().issued_at(),
        TimestampMs::new(2_000)
    );
    assert_eq!(
        state
            .node(&evidence.node_id)
            .unwrap()
            .reported_status_received_at(),
        TimestampMs::new(2_000)
    );
}

/// Gap/identity/sequence/future/replay/expiry/route-loss proofs cannot refresh any authority.
#[test]
fn continuity_rejections_and_expiry_are_atomic() {
    let (mut control, mut state, mut events, evidence) = fixture();
    let mut cases = Vec::new();
    let mut wrong = evidence.clone();
    wrong.session_id = "old-session".into();
    cases.push(wrong);
    let mut wrong = evidence.clone();
    wrong.lease_id = LeaseId::new("wrong").unwrap();
    cases.push(wrong);
    let mut wrong = evidence.clone();
    wrong.maximum_gap_ms = 15_000;
    cases.push(wrong);
    let mut wrong = evidence.clone();
    wrong.first_sequence = 6;
    cases.push(wrong);
    let mut wrong = evidence.clone();
    wrong.last_received_at = TimestampMs::new(23_000);
    cases.push(wrong);
    let mut wrong = evidence.clone();
    wrong.first_received_at = TimestampMs::new(15_000);
    cases.push(wrong);
    for wrong in cases {
        let lease = control.node_lease(&evidence.node_id).unwrap().clone();
        let checkpoint = serde_json::to_value(control.checkpoint()).unwrap();
        let count = events.records.len();
        assert!(
            !control
                .accept_session_liveness(
                    &state,
                    &wrong,
                    TimestampMs::new(22_000),
                    &trace(),
                    &mut events
                )
                .unwrap()
        );
        assert_eq!(control.node_lease(&evidence.node_id), Some(&lease));
        assert_eq!(
            serde_json::to_value(control.checkpoint()).unwrap(),
            checkpoint
        );
        assert_eq!(events.records.len(), count);
    }
    assert_eq!(
        control
            .expire_leases(&mut state, TimestampMs::new(15_000), &trace(), &mut events)
            .unwrap(),
        vec![evidence.node_id.clone()]
    );
    assert!(
        !control
            .accept_session_liveness(
                &state,
                &evidence,
                TimestampMs::new(22_000),
                &trace(),
                &mut events
            )
            .unwrap(),
        "committed expiry cannot be healed by a late summary"
    );
    assert!(
        control
            .register_node_session(&evidence.node_id, "continuous-session", 15_000)
            .is_err(),
        "anchor API cannot clear the expiry fence"
    );
    let (mut control, state, mut events, evidence) = fixture();
    control.fence_node_session(&evidence.node_id);
    assert!(
        !control
            .accept_session_liveness(
                &state,
                &evidence,
                TimestampMs::new(22_000),
                &trace(),
                &mut events
            )
            .unwrap()
    );
}

/// Admission is monotonic and restart clears both lease/session without renewing old source evidence.
#[test]
fn session_evidence_watermark_and_restart_are_fenced() {
    let (mut control, state, mut events, evidence) = fixture();
    assert!(
        control
            .accept_session_liveness(
                &state,
                &evidence,
                TimestampMs::new(22_000),
                &trace(),
                &mut events
            )
            .unwrap()
    );
    let count = events.records.len();
    assert!(
        !control
            .accept_session_liveness(
                &state,
                &evidence,
                TimestampMs::new(22_000),
                &trace(),
                &mut events
            )
            .unwrap()
    );
    assert_eq!(events.records.len(), count);
    let mut restored = ControlPlane::restore(control.checkpoint()).unwrap();
    assert!(restored.node_lease(&evidence.node_id).is_none());
    assert!(
        !restored
            .accept_session_liveness(
                &state,
                &evidence,
                TimestampMs::new(22_000),
                &trace(),
                &mut events
            )
            .unwrap()
    );
}
