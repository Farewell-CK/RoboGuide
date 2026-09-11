//! Peer-channel, relation, and reconnect State tests.

use super::*;

/// Peer readiness is admitted only from each committed role's registered Local EAIOS owner.
#[test]
fn peer_readiness_is_owner_checked_expires_and_restores_fenced() {
    let source =
        include_str!("../../../../../scenarios/execution-relations-v0.1/mission-plan.json");
    let mut document: serde_json::Value =
        serde_json::from_str(source).expect("relation fixture is JSON");
    document["schema_version"] = serde_json::json!(domain::MISSION_PLAN_SCHEMA_V0_4);
    document["contexts"][0]["coupling_mode"] = serde_json::json!("tightly-coupled-cooperation");
    document["contexts"][0]["shared_view"] = serde_json::json!({
        "bindings": [
            {"context_role_id": "guide", "field": "execution"},
            {"context_role_id": "safety", "field": "execution"}
        ],
        "include_freshness": false
    });
    document["contexts"][0]["peer_channel"] = serde_json::json!({
        "profile_id": "guidance-peer",
        "message_schema": "guidance/v1"
    });
    let plan = crate::decode_mission_plan(&document.to_string()).expect("v0.4 plan validates");
    let mission_id = plan.goal().mission_id().clone();
    let group_id = domain::ExecutionGroupId::new("group-peer").expect("group id is valid");
    let context_id = plan.contexts()[0].context_id().clone();
    let correlation = CorrelationId::new("peer-readiness-test").expect("correlation is valid");
    let mut bridge = IntegrationRuntimeBridge::new(
        ControlPlane::new(),
        InMemorySharedNodeState::new(),
        InMemoryEventLog::new(),
        GrpcNodeRouter::default(),
    );
    let registration =
        |node: &str,
         local_system: &str,
         contract: &str,
         kind: &str,
         resources: Vec<integration::grpc::v0_4::Resource>| NodeRegistration {
            node_id: node.to_string(),
            local_systems: vec![
                LocalSystemDescriptor {
                    id: local_system.to_string(),
                    runtime: Some(WireRuntime {
                        name: format!("{local_system}-runtime"),
                        version: "1".to_string(),
                    }),
                    metadata: Default::default(),
                },
                LocalSystemDescriptor {
                    id: "decoy".to_string(),
                    runtime: Some(WireRuntime {
                        name: "decoy-runtime".to_string(),
                        version: "1".to_string(),
                    }),
                    metadata: Default::default(),
                },
            ],
            capabilities: vec![WireCapability {
                kind: kind.to_string(),
                available: true,
                contracts: vec![contract.to_string()],
                local_system_id: local_system.to_string(),
            }],
            sensors: Vec::new(),
            resources,
            metadata: Default::default(),
            node_contract_version: "roboguide.node.v0.3".to_string(),
            state_exports: Vec::new(),
            memory_providers: Vec::new(),
            capability_profiles: Vec::new(),
            operation_support: Vec::new(),
        };
    for (node, lease, local_system, contract, kind, resources) in [
        (
            "cane-a",
            "lease-cane",
            "safety",
            "safety.observe@v1",
            "observation",
            Vec::new(),
        ),
        (
            "dog-a",
            "lease-dog",
            "motion",
            "mobility.navigate@v1",
            "mobility",
            vec![integration::grpc::v0_4::Resource {
                id: "guide-space".to_string(),
                kind: "space".to_string(),
                capacity: 1,
                metadata: Default::default(),
                local_system_id: "motion".to_string(),
            }],
        ),
    ] {
        bridge
            .consume(
                GrpcNodeEvent::Registered {
                    session_id: format!("session-{node}"),
                    lease_id: lease.to_string(),
                    registration: registration(node, local_system, contract, kind, resources),
                },
                TimestampMs::new(1),
                &correlation,
            )
            .expect("node registration is accepted");
        bridge
            .consume(
                GrpcNodeEvent::NodeMessage {
                    node_id: node.to_string(),
                    session_id: format!("session-{node}"),
                    message: integration::grpc::v0_4::NodeMessage {
                        message: Some(NodePayload::Heartbeat(integration::grpc::v0_4::Heartbeat {
                            session_id: format!("session-{node}"),
                            lease_id: lease.to_string(),
                            sequence: 1,
                            status: Some(integration::grpc::v0_4::NodeStatus {
                                health: "online".to_string(),
                                detail: String::new(),
                            }),
                        })),
                    },
                },
                TimestampMs::new(2),
                &correlation,
            )
            .expect("online heartbeat is accepted");
    }
    let mut orchestrator = crate::MissionOrchestrator::new();
    {
        let (control, events) = (&mut bridge.control, &mut bridge.events);
        orchestrator
            .submit(
                plan.clone(),
                group_id.clone(),
                control,
                TimestampMs::new(3),
                &correlation,
                events,
            )
            .expect("Mission is accepted");
    }
    bridge
        .register_execution_relations(&plan, &group_id, TimestampMs::new(3), &correlation)
        .expect("coordination declarations register");
    for task_ref in orchestrator.ready_tasks(&mission_id, bridge.control()) {
        let state = bridge.state().clone();
        let (control, events) = (&mut bridge.control, &mut bridge.events);
        orchestrator
            .prepare_task(
                &mission_id,
                &task_ref,
                &state,
                control,
                TimestampMs::new(4),
                &correlation,
                events,
            )
            .expect("ready Task binds");
    }

    let readiness =
        |node: &str, local_system: &str, role: &str, sequence: u64| GrpcNodeEvent::NodeMessage {
            node_id: node.to_string(),
            session_id: format!("session-{node}"),
            message: integration::grpc::v0_4::NodeMessage {
                message: Some(NodePayload::PeerChannelReadiness(
                    integration::grpc::v0_4::PeerChannelReadiness {
                        session_id: format!("session-{node}"),
                        sequence,
                        group_id: group_id.as_str().to_string(),
                        context_id: context_id.as_str().to_string(),
                        context_role_id: role.to_string(),
                        channel_instance_id: "channel-guidance-1".to_string(),
                        profile_id: "guidance-peer".to_string(),
                        message_schema: "guidance/v1".to_string(),
                        ready: true,
                        valid_for_ms: 20,
                        local_system_id: local_system.to_string(),
                    },
                )),
            },
        };
    let mut wrong_session = readiness("dog-a", "motion", "guide", 2);
    let GrpcNodeEvent::NodeMessage { message, .. } = &mut wrong_session else {
        unreachable!("readiness fixture is a Node message");
    };
    let Some(NodePayload::PeerChannelReadiness(payload)) = &mut message.message else {
        unreachable!("readiness fixture contains peer evidence");
    };
    payload.session_id = "session-other".to_string();
    assert!(matches!(
        bridge.consume(wrong_session, TimestampMs::new(9), &correlation),
        Err(IntegrationRuntimeError::Protocol(reason))
            if reason.contains("session does not match")
    ));
    let mut wrong_context = readiness("dog-a", "motion", "guide", 2);
    let GrpcNodeEvent::NodeMessage { message, .. } = &mut wrong_context else {
        unreachable!("readiness fixture is a Node message");
    };
    let Some(NodePayload::PeerChannelReadiness(payload)) = &mut message.message else {
        unreachable!("readiness fixture contains peer evidence");
    };
    payload.context_id = "different-context".to_string();
    assert!(matches!(
        bridge.consume(wrong_context, TimestampMs::new(9), &correlation),
        Err(IntegrationRuntimeError::Protocol(reason))
            if reason.contains("does not own the ContextRole binding")
    ));
    assert!(matches!(
        bridge.consume(
            readiness("dog-a", "decoy", "guide", 2),
            TimestampMs::new(10),
            &correlation,
        ),
        Err(IntegrationRuntimeError::Protocol(reason))
            if reason.contains("does not own the ContextRole binding")
    ));
    bridge
        .consume(
            readiness("cane-a", "safety", "safety", 2),
            TimestampMs::new(10),
            &correlation,
        )
        .expect("safety endpoint acknowledgement is admitted");
    bridge
        .consume(
            readiness("dog-a", "motion", "guide", 3),
            TimestampMs::new(11),
            &correlation,
        )
        .expect("guide endpoint acknowledgement is admitted");
    assert_eq!(
        bridge.peer_channel_snapshots(&group_id)[0].lifecycle(),
        runtime::PeerChannelLifecycle::Ready
    );
    assert_eq!(
        bridge
            .events
            .records()
            .iter()
            .filter(|event| matches!(
                event.payload(),
                EventPayload::PeerChannelReadinessObserved { .. }
            ))
            .count(),
        2
    );

    bridge
        .runtime
        .refresh_peer_channel_deadlines(TimestampMs::new(31));
    assert_eq!(
        bridge.peer_channel_snapshots(&group_id)[0].lifecycle(),
        runtime::PeerChannelLifecycle::Fenced
    );
    bridge
        .consume(
            readiness("cane-a", "safety", "safety", 3),
            TimestampMs::new(32),
            &correlation,
        )
        .expect("safety endpoint renews");
    assert_eq!(
        bridge.peer_channel_snapshots(&group_id)[0].lifecycle(),
        runtime::PeerChannelLifecycle::Fenced
    );
    bridge
        .consume(
            readiness("dog-a", "motion", "guide", 4),
            TimestampMs::new(33),
            &correlation,
        )
        .expect("guide endpoint renews");
    assert_eq!(
        bridge.peer_channel_snapshots(&group_id)[0].lifecycle(),
        runtime::PeerChannelLifecycle::Ready
    );

    bridge
        .consume(
            GrpcNodeEvent::NodeMessage {
                node_id: "dog-a".to_string(),
                session_id: "session-dog-a".to_string(),
                message: integration::grpc::v0_4::NodeMessage {
                    message: Some(NodePayload::RegistrationUpdate(
                        integration::grpc::v0_4::RegistrationUpdate {
                            session_id: "session-dog-a".to_string(),
                            sequence: 5,
                            registration: Some(registration(
                                "dog-a",
                                "motion",
                                "mobility.navigate@v1",
                                "mobility",
                                vec![integration::grpc::v0_4::Resource {
                                    id: "guide-space".to_string(),
                                    kind: "space".to_string(),
                                    capacity: 1,
                                    metadata: Default::default(),
                                    local_system_id: "motion".to_string(),
                                }],
                            )),
                        },
                    )),
                },
            },
            TimestampMs::new(34),
            &correlation,
        )
        .expect("complete registration update is accepted");
    assert_eq!(
        bridge.peer_channel_snapshots(&group_id)[0].lifecycle(),
        runtime::PeerChannelLifecycle::Fenced
    );
    assert_eq!(
        bridge.peer_channel_snapshots(&group_id)[0]
            .readiness()
            .len(),
        1
    );
    bridge
        .consume(
            readiness("dog-a", "motion", "guide", 6),
            TimestampMs::new(35),
            &correlation,
        )
        .expect("affected endpoint reproves readiness after registration change");
    assert_eq!(
        bridge.peer_channel_snapshots(&group_id)[0].lifecycle(),
        runtime::PeerChannelLifecycle::Ready
    );

    let restored = IntegrationRuntimeBridge::restore_from_checkpoint(
        &bridge.checkpoint_json().expect("checkpoint serializes"),
        InMemoryEventLog::new(),
        GrpcNodeRouter::default(),
        TimestampMs::new(40),
    )
    .expect("checkpoint restores conservatively");
    let channel = &restored.peer_channel_snapshots(&group_id)[0];
    assert_eq!(channel.lifecycle(), runtime::PeerChannelLifecycle::Fenced);
    assert!(channel.readiness().is_empty());
}

/// A new Node session may restart its management sequence in the same receive millisecond.
#[test]
fn state_observation_reconnect_epoch_accepts_reset_sequence() {
    let router = GrpcNodeRouter::default();
    let mut bridge = IntegrationRuntimeBridge::new(
        ControlPlane::new(),
        InMemorySharedNodeState::new(),
        InMemoryEventLog::new(),
        router,
    );
    let correlation = CorrelationId::new("state-reconnect").expect("correlation is valid");
    bridge
        .consume(
            GrpcNodeEvent::Registered {
                session_id: "session-old".to_string(),
                lease_id: "lease-old".to_string(),
                registration: NodeRegistration {
                    node_id: "cane-a".to_string(),
                    local_systems: vec![LocalSystemDescriptor {
                        id: "safety".to_string(),
                        runtime: Some(WireRuntime {
                            name: "safety-runtime".to_string(),
                            version: "1".to_string(),
                        }),
                        metadata: Default::default(),
                    }],
                    capabilities: Vec::new(),
                    sensors: Vec::new(),
                    resources: Vec::new(),
                    metadata: Default::default(),
                    node_contract_version: "roboguide.node.v0.3".to_string(),
                    state_exports: vec![integration::grpc::v0_4::StateExportDescriptor {
                        export_id: "hazard-state".to_string(),
                        local_system_id: "safety".to_string(),
                        object_class: integration::grpc::v0_4::StateObjectClass::World as i32,
                        object_type: "hazard".to_string(),
                        object_id: "crossing-a".to_string(),
                        semantic: integration::grpc::v0_4::StateSemantic::Observed as i32,
                        payload_schema: "example.hazard/v1".to_string(),
                        valid_for_ms: 1_000,
                    }],
                    memory_providers: Vec::new(),
                    capability_profiles: Vec::new(),
                    operation_support: Vec::new(),
                },
            },
            TimestampMs::new(1),
            &correlation,
        )
        .expect("registration is accepted");
    for (session_id, sequence, present) in [("session-old", 99, false), ("session-new", 1, true)] {
        bridge
            .consume(
                GrpcNodeEvent::NodeMessage {
                    node_id: "cane-a".to_string(),
                    session_id: session_id.to_string(),
                    message: integration::grpc::v0_4::NodeMessage {
                        message: Some(NodePayload::StateObservationBatch(
                            integration::grpc::v0_4::StateObservationBatch {
                                session_id: session_id.to_string(),
                                sequence,
                                observations: vec![integration::grpc::v0_4::StateObservation {
                                    export_id: "hazard-state".to_string(),
                                    json_value: serde_json::to_vec(
                                        &serde_json::json!({"present": present}),
                                    )
                                    .expect("State value serializes"),
                                    has_source_observed_at: false,
                                    source_observed_at_ms: 0,
                                    has_confidence: false,
                                    confidence_millionths: 0,
                                }],
                            },
                        )),
                    },
                },
                TimestampMs::new(2),
                &correlation,
            )
            .expect("current session State observation is accepted");
    }

    let record = bridge
        .state_records()
        .records()
        .into_iter()
        .next()
        .expect("one latest State record remains");
    assert_eq!(record.source_epoch(), Some("session-new"));
    assert_eq!(record.sequence(), 1);
    assert_eq!(record.value(), &serde_json::json!({"present": true}));
}

/// Parses hierarchical canonical contracts with the same last-dot rule as Node Config.
#[test]
fn canonical_contract_parser_round_trips_hierarchical_namespace() {
    let contract = parse_contract("spatial.map.build@v0").expect("contract parses");
    assert_eq!(contract.namespace(), "spatial.map");
    assert_eq!(contract.name(), "build");
    assert_eq!(contract.to_string(), "spatial.map.build@v0");
}
