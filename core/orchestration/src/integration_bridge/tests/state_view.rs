//! Shared State and localization admission tests.

use super::*;

/// A Group view selects exact authorized exports and exposes receive-time freshness states.
#[test]
fn group_shared_view_uses_exact_export_schema_and_freshness() {
    let source =
        include_str!("../../../../../scenarios/execution-relations-v0.1/mission-plan.json");
    let mut document: serde_json::Value =
        serde_json::from_str(source).expect("relation fixture is JSON");
    document["schema_version"] = serde_json::json!(domain::MISSION_PLAN_SCHEMA_V0_4);
    document["contexts"][0]["coupling_mode"] = serde_json::json!("concurrent-cooperation");
    document["contexts"][0]["shared_view"] = serde_json::json!({
        "spatial_reference": {
            "map_id": "campus",
            "revision_id": "r1",
            "frame_id": "map"
        },
        "bindings": [
            {
                "context_role_id": "safety",
                "field": "pose",
                "state_export_id": "safety-pose",
                "payload_schema": "roboguide.pose/v1"
            },
            {"context_role_id": "safety", "field": "execution"}
        ],
        "include_freshness": true
    });
    let plan = crate::decode_mission_plan(&document.to_string()).expect("v0.4 plan validates");
    let context_id = plan.contexts()[0].context_id().clone();
    let task = &plan.task_graph().tasks()[0];
    let requirement = task.requirement();
    let role_id = requirement.roles()[0].role_id().clone();
    let group_id = domain::ExecutionGroupId::new("group-view").expect("group id is valid");
    let correlation = CorrelationId::new("group-view-test").expect("correlation is valid");
    let mut bridge = IntegrationRuntimeBridge::new(
        ControlPlane::new(),
        InMemorySharedNodeState::new(),
        InMemoryEventLog::new(),
        GrpcNodeRouter::default(),
    );
    bridge
        .consume(
            GrpcNodeEvent::Registered {
                session_id: "session-view".to_string(),
                lease_id: "lease-view".to_string(),
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
                    capabilities: vec![WireCapability {
                        kind: "observation".to_string(),
                        available: true,
                        contracts: vec!["safety.observe@v1".to_string()],
                        local_system_id: "safety".to_string(),
                    }],
                    sensors: Vec::new(),
                    resources: Vec::new(),
                    metadata: Default::default(),
                    node_contract_version: "roboguide.node.v0.3".to_string(),
                    state_exports: ["safety-pose", "pose-shadow"]
                        .into_iter()
                        .map(|export_id| integration::grpc::v0_4::StateExportDescriptor {
                            export_id: export_id.to_string(),
                            local_system_id: "safety".to_string(),
                            object_class: integration::grpc::v0_4::StateObjectClass::Node as i32,
                            object_type: "pose".to_string(),
                            object_id: "cane-a".to_string(),
                            semantic: integration::grpc::v0_4::StateSemantic::Reported as i32,
                            payload_schema: "roboguide.pose/v1".to_string(),
                            valid_for_ms: 100,
                        })
                        .collect(),
                    memory_providers: Vec::new(),
                    capability_profiles: Vec::new(),
                },
            },
            TimestampMs::new(1),
            &correlation,
        )
        .expect("node registration is accepted");
    bridge
        .consume(
            GrpcNodeEvent::NodeMessage {
                node_id: "cane-a".to_string(),
                session_id: "session-view".to_string(),
                message: integration::grpc::v0_4::NodeMessage {
                    message: Some(NodePayload::Heartbeat(integration::grpc::v0_4::Heartbeat {
                        session_id: "session-view".to_string(),
                        lease_id: "lease-view".to_string(),
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
    bridge
        .control
        .create_mission_group(
            group_id.clone(),
            &plan,
            TimestampMs::new(3),
            &correlation,
            &mut bridge.events,
        )
        .expect("Mission Group is created");
    bridge
        .control
        .ready_task_execution(
            &group_id,
            requirement.task_ref(),
            TimestampMs::new(3),
            &correlation,
            &mut bridge.events,
        )
        .expect("Task becomes ready");
    let candidates = bridge
        .control
        .match_capabilities(
            &bridge.state,
            requirement,
            TimestampMs::new(3),
            &correlation,
            &mut bridge.events,
        )
        .expect("node matches exact observation contract");
    let proposal = bridge
        .control
        .propose(
            &bridge.state,
            requirement,
            &candidates,
            vec![domain::RoleAssignment::new(
                role_id,
                NodeId::new("cane-a").expect("node id is valid"),
                Vec::new(),
            )],
            TimestampMs::new(3),
            &correlation,
            &mut bridge.events,
        )
        .expect("zero-resource proposal is valid");
    let committed = bridge
        .control
        .commit(
            &proposal,
            TimestampMs::new(3),
            &correlation,
            &mut bridge.events,
        )
        .expect("proposal commits");
    bridge
        .control
        .bind_task_execution_with_requirement(
            &group_id,
            &committed,
            requirement,
            TimestampMs::new(3),
            &correlation,
            &mut bridge.events,
        )
        .expect("Task binds to the State-producing node");

    let missing_coordination = bridge.execute_task_bound(
        "execution-without-coordination".to_string(),
        &group_id,
        requirement.task_ref(),
        requirement.roles()[0].role_id(),
        task.execution_intent(requirement.roles()[0].role_id())
            .expect("plan contains role intent")
            .clone(),
        TimestampMs::new(4),
        correlation.clone(),
    );
    assert!(matches!(
        missing_coordination,
        Err(IntegrationRuntimeError::Protocol(reason))
            if reason.contains("coordination mechanisms are not ready")
    ));

    let unknown = bridge
        .group_shared_view(&plan, &group_id, &context_id, TimestampMs::new(4))
        .expect("Group view is readable before evidence arrives");
    assert_eq!(unknown.entries().len(), 2);
    assert_eq!(
        unknown.entries()[0].freshness(),
        Some(GroupViewFreshness::Unknown)
    );
    assert!(unknown.entries()[0].record().is_none());
    assert_eq!(
        unknown.entries()[0].spatial_verification(),
        Some(GroupSpatialVerification::Unknown)
    );
    assert_eq!(
        unknown.entries()[1].field(),
        domain::GroupViewField::Execution
    );
    assert_eq!(unknown.entries()[1].execution_status(), None);
    assert_eq!(unknown.entries()[1].state_export_id(), None);

    bridge
        .consume(
            GrpcNodeEvent::NodeMessage {
                node_id: "cane-a".to_string(),
                session_id: "session-view".to_string(),
                message: integration::grpc::v0_4::NodeMessage {
                    message: Some(NodePayload::StateObservationBatch(
                        integration::grpc::v0_4::StateObservationBatch {
                            session_id: "session-view".to_string(),
                            sequence: 2,
                            observations: vec![
                                integration::grpc::v0_4::StateObservation {
                                    export_id: "pose-shadow".to_string(),
                                    json_value: br#"{"x":999}"#.to_vec(),
                                    has_source_observed_at: false,
                                    source_observed_at_ms: 0,
                                    has_confidence: false,
                                    confidence_millionths: 0,
                                },
                                integration::grpc::v0_4::StateObservation {
                                    export_id: "safety-pose".to_string(),
                                    json_value: br#"{"x":1}"#.to_vec(),
                                    has_source_observed_at: false,
                                    source_observed_at_ms: 0,
                                    has_confidence: false,
                                    confidence_millionths: 0,
                                },
                            ],
                        },
                    )),
                },
            },
            TimestampMs::new(10),
            &correlation,
        )
        .expect("State evidence is accepted");

    let fresh = bridge
        .group_shared_view(&plan, &group_id, &context_id, TimestampMs::new(50))
        .expect("fresh view is readable");
    assert_eq!(fresh.entries().len(), 2);
    assert_eq!(fresh.entries()[0].state_export_id(), Some("safety-pose"));
    assert_eq!(
        fresh.entries()[0].freshness(),
        Some(GroupViewFreshness::Fresh)
    );
    assert_eq!(
        fresh.entries()[0]
            .record()
            .expect("selected evidence exists")
            .value(),
        &serde_json::json!({"x": 1})
    );
    let command = ExecutionCommand::new(
        requirement.mission_id().clone(),
        requirement.task_id().clone(),
        group_id.clone(),
        requirement.roles()[0].role_id().clone(),
        NodeId::new("cane-a").expect("node id is valid"),
        task.execution_intent(requirement.roles()[0].role_id())
            .expect("plan contains role intent")
            .clone(),
        correlation.clone(),
    );
    bridge
        .runtime
        .record_dispatched("execution-view".to_string(), command, Vec::new())
        .expect("Runtime dispatch is recorded");
    let evidence: domain::LocalizationVerificationEvidence =
        serde_json::from_value(serde_json::json!({
            "schema": domain::LOCALIZATION_EVIDENCE_SCHEMA_V0_1,
            "map_id": "campus",
            "revision_id": "r1",
            "content_digest": format!("sha256:{}", "a".repeat(64)),
            "byte_size": 1,
            "mission_id": requirement.mission_id().as_str(),
            "task_id": requirement.task_id().as_str(),
            "group_id": group_id.as_str(),
            "role_id": requirement.roles()[0].role_id().as_str(),
            "node_id": "cane-a",
            "execution_id": "execution-view",
            "local_attempt_id": "local-view",
            "active_local_map_id": "campus-r1",
            "mode": "localization",
            "pose_quality": {
                "metric": "translation_stddev",
                "value": "0.05",
                "threshold": "0.10",
                "unit": "m",
                "comparison": "at_most"
            },
            "frames": {"map": "map", "odom": "odom", "base": "base_link"},
            "anchor_id": "campus-origin",
            "source_observed_at_ms": 49
        }))
        .expect("strong localization evidence validates");
    bridge
        .observe_localization_evidence(&evidence, TimestampMs::new(50), &correlation)
        .expect("current-attempt localization evidence is accepted");
    let execution_view = bridge
        .group_shared_view(&plan, &group_id, &context_id, TimestampMs::new(50))
        .expect("Runtime execution view is readable");
    assert_eq!(
        execution_view.entries()[1].execution_status(),
        Some(RemoteExecutionStatus::Accepted)
    );
    assert_eq!(
        execution_view.entries()[0].spatial_verification(),
        Some(GroupSpatialVerification::Verified)
    );
    assert_eq!(
        execution_view.entries()[0]
            .spatial_evidence()
            .map(SharedSpatialEvidence::execution_id),
        Some("execution-view")
    );
    let stale = bridge
        .group_shared_view(&plan, &group_id, &context_id, TimestampMs::new(111))
        .expect("stale evidence remains inspectable");
    assert_eq!(
        stale.entries()[0].freshness(),
        Some(GroupViewFreshness::Stale)
    );
}

/// Current-attempt admission rejects superseded typed evidence before durable catalog append.
#[test]
fn localization_admission_rejects_superseded_attempt_provenance() {
    let mut bridge = IntegrationRuntimeBridge::new(
        ControlPlane::new(),
        InMemorySharedNodeState::new(),
        InMemoryEventLog::new(),
        GrpcNodeRouter::default(),
    );
    let correlation = CorrelationId::new("stale-localization-test").expect("correlation valid");
    let command = ExecutionCommand::new(
        domain::MissionId::new("mission-map").expect("mission valid"),
        domain::TaskId::new("localize").expect("task valid"),
        domain::ExecutionGroupId::new("group-map").expect("group valid"),
        domain::RoleId::new("localizer").expect("role valid"),
        NodeId::new("dog-a").expect("node valid"),
        domain::ExecutionIntent::new(
            CapabilityContractRef::new("spatial.map", "localize", "v0").expect("contract valid"),
            BTreeMap::new(),
        )
        .expect("intent valid"),
        correlation.clone(),
    );
    bridge
        .runtime
        .prepare_dispatch("attempt-old".to_string(), command.clone(), Vec::new())
        .expect("old attempt prepares");
    let replacement = ExecutionCommand::new(
        command.mission_id().clone(),
        command.task_ref().task_id().clone(),
        command.group_id().clone(),
        command.role_id().clone(),
        NodeId::new("dog-b").expect("node valid"),
        command.intent().clone(),
        correlation.clone(),
    );
    bridge
        .runtime
        .prepare_dispatch("attempt-new".to_string(), replacement, Vec::new())
        .expect("replacement attempt prepares");
    let evidence: domain::LocalizationVerificationEvidence =
        serde_json::from_value(serde_json::json!({
            "schema": domain::LOCALIZATION_EVIDENCE_SCHEMA_V0_1,
            "map_id": "campus",
            "revision_id": "r1",
            "content_digest": format!("sha256:{}", "a".repeat(64)),
            "byte_size": 1,
            "mission_id": command.mission_id(),
            "task_id": command.task_ref().task_id(),
            "group_id": command.group_id(),
            "role_id": command.role_id(),
            "node_id": command.node_id(),
            "execution_id": "attempt-old",
            "local_attempt_id": "local-old",
            "active_local_map_id": "campus-r1",
            "mode": "localization",
            "pose_quality": {
                "metric": "translation_stddev",
                "value": "0.05",
                "threshold": "0.10",
                "unit": "m",
                "comparison": "at_most"
            },
            "frames": {"map": "map", "odom": "odom", "base": "base_link"},
            "anchor_id": "campus-origin",
            "source_observed_at_ms": 49
        }))
        .expect("strong localization evidence validates");

    assert!(!bridge.localization_evidence_is_current(&evidence));
    bridge
        .observe_localization_evidence(&evidence, TimestampMs::new(50), &correlation)
        .expect("catalog replay may ignore historical evidence without mutating Runtime");
    assert!(
        bridge
            .runtime
            .shared_spatial_evidence(command.group_id(), command.task_ref(), command.role_id(),)
            .is_none()
    );
}
