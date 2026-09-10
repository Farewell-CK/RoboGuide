//! Integration checkpoint and protocol-ingestion tests.

use super::*;

/// Relation registration emits durable evidence and survives checkpoint validation.
#[test]
fn relation_registration_round_trips_through_integration_checkpoint() {
    let plan = related_single_task_plan();
    let group_id = domain::ExecutionGroupId::new("group-relation").expect("group id is valid");
    let correlation = CorrelationId::new("relation-registration").expect("correlation valid");
    let mut control = ControlPlane::new();
    control
        .create_mission_group(
            group_id.clone(),
            &plan,
            TimestampMs::new(5),
            &correlation,
            &mut InMemoryEventLog::new(),
        )
        .expect("Mission Group is created before Runtime relation registration");
    let mut bridge = IntegrationRuntimeBridge::new(
        control,
        InMemorySharedNodeState::new(),
        InMemoryEventLog::new(),
        GrpcNodeRouter::default(),
    );
    bridge
        .register_execution_relations(&plan, &group_id, TimestampMs::new(10), &correlation)
        .expect("relation registers");
    assert!(bridge.events.contains_payload(|payload| matches!(
        payload,
        EventPayload::ExecutionRelationRegistered { relation_id, .. }
            if relation_id.as_str() == "safety-guards-navigation"
    )));
    assert_eq!(
        bridge.relation_snapshots(&group_id)[0].state(),
        domain::ExecutionRelationState::Dormant
    );

    let checkpoint = bridge.checkpoint_json().expect("checkpoint serializes");
    let restored = IntegrationRuntimeBridge::restore_from_checkpoint(
        &checkpoint,
        InMemoryEventLog::new(),
        GrpcNodeRouter::default(),
        TimestampMs::new(20),
    )
    .expect("checkpoint restores");
    restored
        .validate_execution_relations(&plan, &group_id)
        .expect("restored relation registry matches MissionPlan");
    assert_eq!(restored.relation_snapshots(&group_id).len(), 1);
}

/// The v11 checkpoint migrates with empty newly introduced command and attempt evidence.
#[test]
fn v11_checkpoint_migrates_missing_command_and_attempt_evidence() {
    let bridge = IntegrationRuntimeBridge::new(
        ControlPlane::new(),
        InMemorySharedNodeState::new(),
        InMemoryEventLog::new(),
        GrpcNodeRouter::default(),
    );
    let mut checkpoint: serde_json::Value = serde_json::from_str(
        &bridge
            .checkpoint_json()
            .expect("current checkpoint serializes"),
    )
    .expect("current checkpoint is JSON");
    checkpoint["schema"] = serde_json::json!(PREVIOUS_CONTROLLER_CHECKPOINT_SCHEMA);
    let runtime = checkpoint["runtime"]
        .as_object_mut()
        .expect("Runtime checkpoint is an object");
    runtime.remove("attempt_generations");
    runtime.remove("dispatch_outbox");
    runtime.remove("cancellation_intents");

    let restored = IntegrationRuntimeBridge::restore_from_checkpoint(
        &checkpoint.to_string(),
        InMemoryEventLog::new(),
        GrpcNodeRouter::default(),
        TimestampMs::new(1),
    )
    .expect("previous checkpoint migrates");
    assert!(
        restored
            .peer_channel_snapshots(
                &domain::ExecutionGroupId::new("unused").expect("group id is valid")
            )
            .is_empty()
    );
}

/// Integration cannot create a relation registry beside an absent Control Group.
#[test]
fn relation_registration_requires_existing_control_group() {
    let plan = related_single_task_plan();
    let mut bridge = IntegrationRuntimeBridge::new(
        ControlPlane::new(),
        InMemorySharedNodeState::new(),
        InMemoryEventLog::new(),
        GrpcNodeRouter::default(),
    );
    let result = bridge.register_execution_relations(
        &plan,
        &domain::ExecutionGroupId::new("group-absent").expect("group id is valid"),
        TimestampMs::new(10),
        &CorrelationId::new("relation-registration").expect("correlation valid"),
    );
    assert!(matches!(
        result,
        Err(IntegrationRuntimeError::Protocol(reason))
            if reason.contains("existing Mission-level Group")
    ));
}

/// Registration and heartbeat facts enter existing Control lease authority and Shared State.
#[test]
fn integration_facts_update_control_and_shared_state() {
    let router = GrpcNodeRouter::default();
    let mut bridge = IntegrationRuntimeBridge::new(
        ControlPlane::new(),
        InMemorySharedNodeState::new(),
        InMemoryEventLog::new(),
        router,
    );
    let correlation = CorrelationId::new("integration-test").expect("correlation valid");
    bridge
        .consume(
            GrpcNodeEvent::Registered {
                session_id: "session-1".to_string(),
                lease_id: "lease-1".to_string(),
                registration: NodeRegistration {
                    node_id: "dog-a".to_string(),
                    local_systems: vec![LocalSystemDescriptor {
                        id: "motion".to_string(),
                        runtime: Some(WireRuntime {
                            name: "local-motion".to_string(),
                            version: "1".to_string(),
                        }),
                        metadata: Default::default(),
                    }],
                    capabilities: vec![WireCapability {
                        kind: "mobility".to_string(),
                        available: true,
                        contracts: vec!["mobility.reach_region@v1".to_string()],
                        local_system_id: "motion".to_string(),
                    }],
                    sensors: Vec::new(),
                    resources: Vec::new(),
                    metadata: std::collections::HashMap::new(),
                    node_contract_version: "roboguide.node.v0.3".to_string(),
                    state_exports: Vec::new(),
                    memory_providers: Vec::new(),
                },
            },
            TimestampMs::new(0),
            &correlation,
        )
        .expect("registration consumed");
    let node_id = NodeId::new("dog-a").expect("node id valid");
    assert!(bridge.control().node_lease(&node_id).is_some());
    assert_eq!(
        bridge
            .state()
            .node(&node_id)
            .expect("node visible")
            .reported_status()
            .health(),
        NodeHealth::Offline
    );
    bridge
        .consume(
            GrpcNodeEvent::NodeMessage {
                node_id: "dog-a".to_string(),
                session_id: "session-1".to_string(),
                message: integration::grpc::v0_4::NodeMessage {
                    message: Some(NodePayload::Heartbeat(integration::grpc::v0_4::Heartbeat {
                        session_id: "session-1".to_string(),
                        lease_id: "lease-1".to_string(),
                        sequence: 1,
                        status: Some(integration::grpc::v0_4::NodeStatus {
                            health: "degraded".to_string(),
                            detail: String::new(),
                        }),
                    })),
                },
            },
            TimestampMs::new(1),
            &correlation,
        )
        .expect("heartbeat consumed");
    assert_eq!(
        bridge
            .state()
            .node(&node_id)
            .expect("node visible")
            .reported_status()
            .health(),
        NodeHealth::Degraded
    );
    bridge
        .consume(
            GrpcNodeEvent::NodeMessage {
                node_id: "dog-a".to_string(),
                session_id: "session-1".to_string(),
                message: integration::grpc::v0_4::NodeMessage {
                    message: Some(NodePayload::RegistrationUpdate(
                        integration::grpc::v0_4::RegistrationUpdate {
                            session_id: "session-1".to_string(),
                            sequence: 2,
                            registration: Some(NodeRegistration {
                                node_id: "dog-a".to_string(),
                                local_systems: vec![LocalSystemDescriptor {
                                    id: "motion".to_string(),
                                    runtime: Some(WireRuntime {
                                        name: "local-motion".to_string(),
                                        version: "2".to_string(),
                                    }),
                                    metadata: Default::default(),
                                }],
                                capabilities: vec![WireCapability {
                                    kind: "mobility".to_string(),
                                    available: true,
                                    contracts: vec!["mobility.reach_region@v1".to_string()],
                                    local_system_id: "motion".to_string(),
                                }],
                                sensors: Vec::new(),
                                resources: Vec::new(),
                                metadata: std::collections::HashMap::new(),
                                node_contract_version: "roboguide.node.v0.3".to_string(),
                                state_exports: Vec::new(),
                                memory_providers: Vec::new(),
                            }),
                        },
                    )),
                },
            },
            TimestampMs::new(2),
            &correlation,
        )
        .expect("registration update is consumed");
    let updated = bridge.state().node(&node_id).expect("updated node visible");
    assert_eq!(updated.reported_status().health(), NodeHealth::Degraded);
    assert_eq!(updated.reported_status_received_at(), TimestampMs::new(1));
    assert_eq!(updated.liveness().observed_at(), TimestampMs::new(1));
    assert_eq!(updated.registration().local_runtime().version(), "2");
    bridge
        .checkpoint_json()
        .expect("registered node owner maps must checkpoint");
}

/// State observations persist independently and do not mutate health or Control authority.
#[test]
fn state_observations_are_evidence_without_health_or_control_side_effects() {
    let router = GrpcNodeRouter::default();
    let mut bridge = IntegrationRuntimeBridge::new(
        ControlPlane::new(),
        InMemorySharedNodeState::new(),
        InMemoryEventLog::new(),
        router,
    );
    let correlation = CorrelationId::new("state-observation").expect("correlation is valid");
    bridge
        .consume(
            GrpcNodeEvent::Registered {
                session_id: "session-state".to_string(),
                lease_id: "lease-state".to_string(),
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
                },
            },
            TimestampMs::new(1),
            &correlation,
        )
        .expect("registration is accepted");
    let node_id = NodeId::new("cane-a").expect("node id is valid");
    let lease_before = bridge
        .control()
        .node_lease(&node_id)
        .expect("registration creates a Control lease")
        .clone();

    bridge
        .consume(
            GrpcNodeEvent::NodeMessage {
                node_id: "cane-a".to_string(),
                session_id: "session-state".to_string(),
                message: integration::grpc::v0_4::NodeMessage {
                    message: Some(NodePayload::StateObservationBatch(
                        integration::grpc::v0_4::StateObservationBatch {
                            session_id: "session-state".to_string(),
                            sequence: 1,
                            observations: vec![integration::grpc::v0_4::StateObservation {
                                export_id: "hazard-state".to_string(),
                                json_value: br#"{"present":true}"#.to_vec(),
                                has_source_observed_at: true,
                                source_observed_at_ms: 500_000,
                                has_confidence: true,
                                confidence_millionths: 950_000,
                            }],
                        },
                    )),
                },
            },
            TimestampMs::new(2),
            &correlation,
        )
        .expect("State observation is accepted as evidence");

    assert_eq!(bridge.state_records().records().len(), 1);
    let record = &bridge.state_records().records()[0];
    assert_eq!(record.received_at(), TimestampMs::new(2));
    assert_eq!(record.source_observed_at(), Some(TimestampMs::new(500_000)));
    assert_eq!(
        bridge
            .state()
            .node(&node_id)
            .expect("registered node remains visible")
            .reported_status()
            .health(),
        NodeHealth::Offline
    );
    assert_eq!(
        bridge
            .control()
            .node_lease(&node_id)
            .expect("Control lease remains present"),
        &lease_before
    );
    assert!(bridge.events.contains_payload(|payload| matches!(
        payload,
        EventPayload::StateRecordObserved { record }
            if record.key().channel_id() == "hazard-state"
    )));

    let checkpoint = bridge.checkpoint_json().expect("State record checkpoints");
    let restored = IntegrationRuntimeBridge::restore_from_checkpoint(
        &checkpoint,
        InMemoryEventLog::new(),
        GrpcNodeRouter::default(),
        TimestampMs::new(2_000),
    )
    .expect("State record restores");
    let restored_record = &restored.state_records().records()[0];
    assert_eq!(restored_record.received_at(), record.received_at());
    assert_eq!(
        restored_record.source_observed_at(),
        record.source_observed_at()
    );
    assert!(restored_record.is_stale_at(TimestampMs::new(2_000)));
}
