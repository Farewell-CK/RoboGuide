//! Custom startup-policy behavior through actual registration, renewal, freshness and restore.

use super::*;

/// Builds a compatibility wire registration with truthful local capability ownership.
fn registration() -> integration::grpc::v0_4::NodeRegistration {
    integration::grpc::v0_4::NodeRegistration {
        node_id: "lease-node".into(),
        node_contract_version: "roboguide.node.v0.3".into(),
        local_systems: vec![LocalSystemDescriptor {
            id: "motion".into(),
            runtime: Some(WireRuntime {
                name: "fake".into(),
                version: "1".into(),
            }),
            metadata: Default::default(),
        }],
        capabilities: vec![WireCapability {
            kind: "mobility".into(),
            available: true,
            contracts: vec!["mobility.navigate@v1".into()],
            local_system_id: "motion".into(),
        }],
        ..Default::default()
    }
}

/// Registers one fresh process-local lease through the same bridge entry used by production.
fn register(bridge: &mut IntegrationRuntimeBridge<InMemoryEventLog>, at: u64, lease: &str) {
    bridge
        .consume(
            GrpcNodeEvent::Registered {
                session_id: "policy-session".into(),
                lease_id: lease.into(),
                registration: registration(),
            },
            TimestampMs::new(at),
            &CorrelationId::new("policy").unwrap(),
        )
        .unwrap();
}

/// Nondefault policy must govern both registration and renewal while health freshness stays separate.
#[test]
fn custom_policy_covers_registration_renewal_and_restore() {
    let policy = ports::NodeLeasePolicy::try_new(7_000, 22_000).unwrap();
    let mut bridge = IntegrationRuntimeBridge::new_with_lease_policy(
        ControlPlane::new(),
        InMemorySharedNodeState::new(),
        InMemoryEventLog::new(),
        GrpcNodeRouter::default(),
        policy,
    );
    let node = NodeId::new("lease-node").unwrap();
    let correlation = CorrelationId::new("policy").unwrap();
    register(&mut bridge, 10, "first-lease");
    assert_eq!(
        bridge.control().node_lease(&node).unwrap().expires_at(),
        TimestampMs::new(22_010)
    );
    bridge
        .consume(
            GrpcNodeEvent::NodeMessage {
                node_id: "lease-node".into(),
                session_id: "policy-session".into(),
                message: integration::grpc::v0_4::NodeMessage {
                    message: Some(NodePayload::Heartbeat(integration::grpc::v0_4::Heartbeat {
                        session_id: "policy-session".into(),
                        lease_id: "first-lease".into(),
                        sequence: 1,
                        status: Some(integration::grpc::v0_4::NodeStatus {
                            health: "online".into(),
                            detail: String::new(),
                        }),
                    })),
                },
            },
            TimestampMs::new(20),
            &correlation,
        )
        .unwrap();
    assert_eq!(
        bridge.control().node_lease(&node).unwrap().expires_at(),
        TimestampMs::new(22_020)
    );
    let requirement = domain::TaskRequirement::new(
        domain::MissionId::new("policy").unwrap(),
        domain::TaskId::new("policy-task").unwrap(),
        vec![domain::RoleRequirement::new(
            domain::RoleId::new("mover").unwrap(),
            CapabilityKind::Mobility,
            None,
        )],
    )
    .unwrap();
    let fresh = bridge
        .control()
        .match_capabilities(
            bridge.state(),
            &requirement,
            TimestampMs::new(20),
            &correlation,
            &mut InMemoryEventLog::new(),
        )
        .unwrap();
    assert_eq!(
        fresh
            .for_role(requirement.roles()[0].role_id())
            .unwrap()
            .node_ids(),
        std::slice::from_ref(&node)
    );
    let candidates = bridge.control().match_capabilities(
        bridge.state(),
        &requirement,
        TimestampMs::new(6_020),
        &correlation,
        &mut InMemoryEventLog::new(),
    );
    assert!(
        matches!(candidates, Err(control::ControlError::NoCandidate(_))),
        "long lease does not extend the 5s health freshness policy"
    );
    let checkpoint = bridge.checkpoint_json().unwrap();
    let mut restored = IntegrationRuntimeBridge::restore_from_checkpoint_with_lease_policy(
        &checkpoint,
        InMemoryEventLog::new(),
        GrpcNodeRouter::default(),
        TimestampMs::new(30_000),
        policy,
    )
    .unwrap();
    assert!(restored.control().node_lease(&node).is_none());
    assert_eq!(
        restored
            .state()
            .node(&node)
            .unwrap()
            .reported_status_received_at(),
        TimestampMs::new(20)
    );
    register(&mut restored, 30_001, "new-lease");
    assert_eq!(
        restored.control().node_lease(&node).unwrap().expires_at(),
        TimestampMs::new(52_001)
    );
    assert_eq!(
        restored.control().node_lease(&node).unwrap().lease_id(),
        &domain::LeaseId::new("new-lease").unwrap()
    );
}
