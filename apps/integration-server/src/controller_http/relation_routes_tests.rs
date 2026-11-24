//! Controller HTTP tests for shared-view queries and relation fence reconciliation.

use super::*;
use integration::grpc::v0_4::{
    Capability as WireCapability, ExecutionEvent as WireExecutionEvent,
    ExecutionPhase as WireExecutionPhase, Heartbeat as WireHeartbeat, LocalRuntime as WireRuntime,
    LocalSystemDescriptor, NodeMessage, NodeRegistration, NodeStatus, Resource as WireResource,
    StateExportDescriptor, StateObjectClass, StateObservation, StateObservationBatch,
    StateSemantic, node_message::Message as NodePayload,
};

/// Parses the JSON body of a raw HTTP/1.1 test response along with its status line.
fn parse_http_json(response: &str) -> (&str, serde_json::Value) {
    let (head, body) = response
        .split_once("\r\n\r\n")
        .expect("HTTP response has header/body boundary");
    let status = head.lines().next().expect("HTTP response has status line");
    let json = serde_json::from_str(body).expect("HTTP response body is valid JSON");
    (status, json)
}

/// Builds a valid v0.4 ConcurrentCooperation MissionPlan with shared_view and requires-active.
fn concurrent_cooperation_plan(mission_id: &str) -> serde_json::Value {
    let source = include_str!("../../../../scenarios/execution-relations-v0.1/mission-plan.json");
    let mut document: serde_json::Value =
        serde_json::from_str(source).expect("relation fixture is JSON");
    document["schema_version"] = serde_json::json!(domain::MISSION_PLAN_SCHEMA_V0_4);
    document["mission"]["id"] = serde_json::json!(mission_id);
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
            {
                "context_role_id": "safety",
                "field": "execution"
            },
            {
                "context_role_id": "guide",
                "field": "execution"
            }
        ],
        "include_freshness": true
    });
    document
}

/// Registers both safety-observer and navigator nodes online with exact contracts and exports.
fn register_coop_nodes(
    controller: &Arc<Mutex<ControllerState>>,
    session_suffix: &str,
    now_ms: u64,
) {
    let correlation =
        domain::CorrelationId::new(format!("register-coop-nodes-{session_suffix}")).unwrap();
    let cane_session = format!("session-cane-{session_suffix}");
    let cane_lease = format!("lease-cane-{session_suffix}");
    let dog_session = format!("session-dog-{session_suffix}");
    let dog_lease = format!("lease-dog-{session_suffix}");
    let mut live = controller.lock().unwrap();
    live.bridge
        .consume(
            GrpcNodeEvent::Registered {
                certificate_digest: None,
                certificate_expires_at_ms: None,
                session_id: cane_session.clone(),
                lease_id: cane_lease.clone(),
                registration: NodeRegistration {
                    node_id: "cane-a".into(),
                    local_systems: vec![LocalSystemDescriptor {
                        id: "safety".into(),
                        runtime: Some(WireRuntime {
                            name: "safety-runtime".into(),
                            version: "1".into(),
                        }),
                        metadata: Default::default(),
                    }],
                    capabilities: vec![WireCapability {
                        kind: "observation".into(),
                        available: true,
                        contracts: vec!["safety.observe@v1".into()],
                        local_system_id: "safety".into(),
                    }],
                    sensors: Vec::new(),
                    resources: Vec::new(),
                    metadata: Default::default(),
                    node_contract_version: "roboguide.node.v0.3".into(),
                    state_exports: vec![StateExportDescriptor {
                        export_id: "safety-pose".into(),
                        local_system_id: "safety".into(),
                        object_class: StateObjectClass::Node as i32,
                        object_type: "pose".into(),
                        object_id: "cane-a".into(),
                        semantic: StateSemantic::Reported as i32,
                        payload_schema: "roboguide.pose/v1".into(),
                        valid_for_ms: 60_000,
                    }],
                    memory_providers: Vec::new(),
                    capability_profiles: Vec::new(),
                    operation_support: Vec::new(),
                },
            },
            domain::TimestampMs::new(now_ms),
            &correlation,
        )
        .unwrap();
    live.bridge
        .consume(
            GrpcNodeEvent::NodeMessage {
                node_id: "cane-a".into(),
                session_id: cane_session.clone(),
                message: NodeMessage {
                    message: Some(NodePayload::Heartbeat(WireHeartbeat {
                        session_id: cane_session,
                        lease_id: cane_lease,
                        sequence: 1,
                        status: Some(NodeStatus {
                            health: "online".into(),
                            detail: String::new(),
                        }),
                    })),
                },
            },
            domain::TimestampMs::new(now_ms + 1),
            &correlation,
        )
        .unwrap();
    live.bridge
        .consume(
            GrpcNodeEvent::Registered {
                certificate_digest: None,
                certificate_expires_at_ms: None,
                session_id: dog_session.clone(),
                lease_id: dog_lease.clone(),
                registration: NodeRegistration {
                    node_id: "dog-a".into(),
                    local_systems: vec![LocalSystemDescriptor {
                        id: "nav".into(),
                        runtime: Some(WireRuntime {
                            name: "nav-runtime".into(),
                            version: "1".into(),
                        }),
                        metadata: Default::default(),
                    }],
                    capabilities: vec![WireCapability {
                        kind: "mobility".into(),
                        available: true,
                        contracts: vec!["mobility.navigate@v1".into()],
                        local_system_id: "nav".into(),
                    }],
                    sensors: Vec::new(),
                    resources: vec![WireResource {
                        id: "corridor-1".into(),
                        kind: "space".into(),
                        capacity: 1,
                        allocation_mode: String::new(),
                        resource_class: String::new(),
                        metadata: Default::default(),
                        local_system_id: "nav".into(),
                    }],
                    metadata: Default::default(),
                    node_contract_version: "roboguide.node.v0.3".into(),
                    state_exports: Vec::new(),
                    memory_providers: Vec::new(),
                    capability_profiles: Vec::new(),
                    operation_support: Vec::new(),
                },
            },
            domain::TimestampMs::new(now_ms),
            &correlation,
        )
        .unwrap();
    live.bridge
        .consume(
            GrpcNodeEvent::NodeMessage {
                node_id: "dog-a".into(),
                session_id: dog_session.clone(),
                message: NodeMessage {
                    message: Some(NodePayload::Heartbeat(WireHeartbeat {
                        session_id: dog_session,
                        lease_id: dog_lease,
                        sequence: 1,
                        status: Some(NodeStatus {
                            health: "online".into(),
                            detail: String::new(),
                        }),
                    })),
                },
            },
            domain::TimestampMs::new(now_ms + 1),
            &correlation,
        )
        .unwrap();
}

/// Builds a tenant-scoped operator principal with the given role.
fn scoped_principal(role: &str, tenant: &str) -> OperatorPrincipal {
    OperatorPrincipal {
        tenants: Some(BTreeSet::from([tenant.into()])),
        subject: Some(format!("{role}-{tenant}")),
        role: Some(role.into()),
        ..Default::default()
    }
}

/// Returns a current attempt execution id for one logical task in a running Mission.
fn current_attempt_id(
    controller: &Arc<Mutex<ControllerState>>,
    mission_id: &str,
    task_id: &str,
) -> String {
    let live = controller.lock().unwrap();
    let mission = domain::MissionId::new(mission_id).unwrap();
    let execution = live.orchestrator.execution(&mission).unwrap();
    let group_id = execution.group_id();
    live.bridge
        .attempt_history()
        .into_iter()
        .rev()
        .find(|attempt| {
            attempt.command().group_id() == group_id
                && attempt.command().task_ref().task_id().as_str() == task_id
        })
        .expect("task has dispatched attempt")
        .execution_id()
        .to_string()
}

/// Feeds one execution-phase fact through the production event, outcome, and checkpoint sequence.
#[allow(clippy::too_many_arguments)]
fn observe_execution_phase(
    controller: &Arc<Mutex<ControllerState>>,
    log: &state::SqliteEventLog,
    node_id: &str,
    session_id: &str,
    execution_id: &str,
    sequence: u64,
    phase: WireExecutionPhase,
    now_ms: u64,
) {
    let correlation = domain::CorrelationId::new(format!("obs-{execution_id}-{sequence}")).unwrap();
    log.begin_batch().unwrap();
    {
        let mut live = controller.lock().unwrap();
        live.bridge
            .consume(
                GrpcNodeEvent::NodeMessage {
                    node_id: node_id.into(),
                    session_id: session_id.into(),
                    message: NodeMessage {
                        message: Some(NodePayload::ExecutionEvent(WireExecutionEvent {
                            session_id: session_id.into(),
                            execution_id: execution_id.into(),
                            sequence,
                            phase: phase as i32,
                            reason: String::new(),
                        })),
                    },
                },
                domain::TimestampMs::new(now_ms),
                &correlation,
            )
            .unwrap();
        let mut events = log.clone();
        application::apply_runtime_events(
            &mut live,
            domain::TimestampMs::new(now_ms),
            &correlation,
            &mut events,
        )
        .unwrap();
        application::apply_runtime_outcomes(
            &mut live,
            domain::TimestampMs::new(now_ms),
            &correlation,
            &mut events,
        )
        .unwrap();
        drive_ready_tasks(
            &mut live,
            domain::TimestampMs::new(now_ms),
            &correlation,
            &mut events,
        )
        .unwrap();
        let checkpoint = server_checkpoint_json(&live).unwrap();
        save_controller_checkpoint(log, &checkpoint).unwrap();
    }
    log.commit_batch().unwrap();
}

/// Rebuilds a fresh ControllerState from the persisted SQLite checkpoint, matching server restart.
fn restore_controller_from_log(
    log: &state::SqliteEventLog,
    now_ms: u64,
) -> Arc<Mutex<ControllerState>> {
    let saved: ServerCheckpoint =
        serde_json::from_str(&log.load_checkpoint().unwrap().unwrap().checkpoint_json).unwrap();
    let bridge = IntegrationRuntimeBridge::restore_from_checkpoint(
        &saved.integration_json,
        log.clone(),
        integration::GrpcNodeRouter::default(),
        domain::TimestampMs::new(now_ms),
    )
    .unwrap();
    let orchestrator = MissionOrchestrator::restore_json(&saved.orchestration_json).unwrap();
    Arc::new(Mutex::new(ControllerState {
        published_deployments: Default::default(),
        federation: Default::default(),
        bridge,
        orchestrator,
        mission_admissions: BTreeMap::new(),
        memory_imports: BTreeMap::new(),
        memory_preparation: Default::default(),
        memory_use: Default::default(),
        memory_handoffs: BTreeMap::new(),
        runtime_revisions: Default::default(),
        hazards: application::hazards::HazardState::default(),
        safety_stops: BTreeMap::new(),
        verifier_seen: BTreeSet::new(),
        verifier_artifact_store: None,
        verifier_source_digest: None,
    }))
}

/// Shared-view route returns live freshness, spatial verification, and enforces tenant/role scope.
#[tokio::test]
async fn shared_view_route_returns_freshness_spatial_verification_and_enforces_scope() {
    let directory = tempfile::tempdir().unwrap();
    let log = state::SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
    let controller = controller(&log);
    let now_ms = runtime::SystemMonotonicClock::new().now().as_millis();
    register_coop_nodes(&controller, "s1", now_ms);

    let global_admin = scoped_principal(RECOVERY_ADMIN_ROLE, "*");
    let plan = concurrent_cooperation_plan("mission-shared-view-http");
    let submit = route(
        &controller,
        &log,
        &global_admin,
        &request(
            "POST",
            "/v1/missions",
            serde_json::to_vec(&plan).unwrap().as_slice(),
            None,
        ),
    )
    .await;
    let (status, _) = parse_http_json(&submit);
    assert!(status.starts_with("HTTP/1.1 202"), "{submit}");

    let source_exec = current_attempt_id(&controller, "mission-shared-view-http", "observe-safety");
    let target_exec = current_attempt_id(&controller, "mission-shared-view-http", "navigate");
    observe_execution_phase(
        &controller,
        &log,
        "cane-a",
        "session-cane-s1",
        &source_exec,
        2,
        WireExecutionPhase::Started,
        now_ms + 5,
    );
    observe_execution_phase(
        &controller,
        &log,
        "dog-a",
        "session-dog-s1",
        &target_exec,
        2,
        WireExecutionPhase::Started,
        now_ms + 6,
    );

    // Missing context_id returns 400; unknown context returns 404.
    let missing_ctx = route(
        &controller,
        &log,
        &global_admin,
        &request(
            "GET",
            "/v1/missions/mission-shared-view-http/shared-view",
            b"",
            None,
        ),
    )
    .await;
    assert!(missing_ctx.starts_with("HTTP/1.1 400"), "{missing_ctx}");

    let unknown_ctx = route(
        &controller,
        &log,
        &global_admin,
        &request(
            "GET",
            "/v1/missions/mission-shared-view-http/shared-view?context_id=no-such-context",
            b"",
            None,
        ),
    )
    .await;
    assert!(unknown_ctx.starts_with("HTTP/1.1 404"), "{unknown_ctx}");

    // Before state observation arrives, freshness and spatial verification are Unknown.
    let before = route(
        &controller,
        &log,
        &scoped_principal("viewer", "*"),
        &request(
            "GET",
            "/v1/missions/mission-shared-view-http/shared-view?context_id=guidance-context",
            b"",
            None,
        ),
    )
    .await;
    let (status, body) = parse_http_json(&before);
    assert!(status.starts_with("HTTP/1.1 200"), "{before}");
    assert_eq!(body["schema_version"], "roboguide.group-shared-view/v0.1");
    assert_eq!(body["spatial_reference"]["map_id"], "campus");
    assert_eq!(body["spatial_reference"]["revision_id"], "r1");
    assert_eq!(body["spatial_reference"]["frame_id"], "map");
    let entries = body["entries"].as_array().unwrap();
    assert_eq!(entries.len(), 3);
    let pose_before = entries
        .iter()
        .find(|entry| entry["field"] == "pose")
        .unwrap();
    assert_eq!(pose_before["freshness"], "Unknown");
    assert!(pose_before["record"].is_null());
    assert_eq!(pose_before["spatial_verification"], "Unknown");
    assert!(
        entries
            .iter()
            .filter(|entry| entry["field"] == "execution")
            .all(|entry| entry["execution_status"] == "Running")
    );

    // Feed a fresh StateObservationBatch and strong localization evidence for cane-a.
    {
        let mut live = controller.lock().unwrap();
        let obs_now = runtime::SystemMonotonicClock::new().now();
        let correlation = domain::CorrelationId::new("obs-shared-view").unwrap();
        live.bridge
            .consume(
                GrpcNodeEvent::NodeMessage {
                    node_id: "cane-a".into(),
                    session_id: "session-cane-s1".into(),
                    message: NodeMessage {
                        message: Some(NodePayload::StateObservationBatch(StateObservationBatch {
                            session_id: "session-cane-s1".into(),
                            sequence: 3,
                            observations: vec![StateObservation {
                                export_id: "safety-pose".into(),
                                json_value: br#"{"x":1.25,"y":-0.5}"#.to_vec(),
                                has_source_observed_at: true,
                                source_observed_at_ms: obs_now.as_millis(),
                                has_confidence: true,
                                confidence_millionths: 950_000,
                            }],
                        })),
                    },
                },
                obs_now,
                &correlation,
            )
            .unwrap();
        let mission = domain::MissionId::new("mission-shared-view-http").unwrap();
        let group_id = live
            .orchestrator
            .execution(&mission)
            .unwrap()
            .group_id()
            .clone();
        let localization: domain::LocalizationVerificationEvidence =
            serde_json::from_value(serde_json::json!({
                "schema": domain::LOCALIZATION_EVIDENCE_SCHEMA_V0_1,
                "map_id": "campus",
                "revision_id": "r1",
                "content_digest": format!("sha256:{}", "a".repeat(64)),
                "byte_size": 1,
                "mission_id": "mission-shared-view-http",
                "task_id": "observe-safety",
                "group_id": group_id.as_str(),
                "role_id": "safety-observer",
                "node_id": "cane-a",
                "execution_id": source_exec,
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
                "source_observed_at_ms": obs_now.as_millis()
            }))
            .expect("strong localization evidence validates");
        live.bridge
            .observe_localization_evidence(&localization, obs_now, &correlation)
            .unwrap();
    }

    let after = route(
        &controller,
        &log,
        &scoped_principal("viewer", "*"),
        &request(
            "GET",
            "/v1/missions/mission-shared-view-http/shared-view?context_id=guidance-context",
            b"",
            None,
        ),
    )
    .await;
    let (status, body) = parse_http_json(&after);
    assert!(status.starts_with("HTTP/1.1 200"), "{after}");
    let pose_after = body["entries"]
        .as_array()
        .unwrap()
        .iter()
        .find(|entry| entry["field"] == "pose")
        .unwrap();
    assert_eq!(pose_after["freshness"], "Fresh");
    assert_eq!(pose_after["record"]["value"]["x"], 1.25);
    assert_eq!(pose_after["spatial_verification"], "Verified");
    assert_eq!(pose_after["spatial_evidence"]["execution_id"], source_exec);

    // Cross-tenant viewer and metrics-reader are rejected with 403.
    let wrong_tenant = route(
        &controller,
        &log,
        &scoped_principal("viewer", "other-tenant"),
        &request(
            "GET",
            "/v1/missions/mission-shared-view-http/shared-view?context_id=guidance-context",
            b"",
            None,
        ),
    )
    .await;
    assert!(wrong_tenant.starts_with("HTTP/1.1 403"), "{wrong_tenant}");

    let metrics_only = route(
        &controller,
        &log,
        &scoped_principal("metrics-reader", "*"),
        &request(
            "GET",
            "/v1/missions/mission-shared-view-http/shared-view?context_id=guidance-context",
            b"",
            None,
        ),
    )
    .await;
    assert!(metrics_only.starts_with("HTTP/1.1 403"), "{metrics_only}");
}

/// Relation reconciliation enforces RBAC, blocks completion while fenced, clears when satisfied, and persists.
#[tokio::test]
async fn relation_reconcile_route_enforces_fence_clears_when_satisfied_and_persists_checkpoint() {
    let directory = tempfile::tempdir().unwrap();
    let log = state::SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
    let controller = controller(&log);
    let now_ms = runtime::SystemMonotonicClock::new().now().as_millis();
    register_coop_nodes(&controller, "s1", now_ms);

    let global_admin = scoped_principal(RECOVERY_ADMIN_ROLE, "*");
    let mission_id = "mission-relation-reconcile-http";
    let plan = concurrent_cooperation_plan(mission_id);
    let submit = route(
        &controller,
        &log,
        &global_admin,
        &request(
            "POST",
            "/v1/missions",
            serde_json::to_vec(&plan).unwrap().as_slice(),
            None,
        ),
    )
    .await;
    assert!(submit.starts_with("HTTP/1.1 202"), "{submit}");
    let now_ms = runtime::SystemMonotonicClock::new().now().as_millis() + 100;

    let source_exec = current_attempt_id(&controller, mission_id, "observe-safety");
    let target_exec = current_attempt_id(&controller, mission_id, "navigate");
    observe_execution_phase(
        &controller,
        &log,
        "cane-a",
        "session-cane-s1",
        &source_exec,
        2,
        WireExecutionPhase::Started,
        now_ms + 5,
    );
    observe_execution_phase(
        &controller,
        &log,
        "dog-a",
        "session-dog-s1",
        &target_exec,
        2,
        WireExecutionPhase::Started,
        now_ms + 6,
    );

    // Simulate a Controller restart from checkpoint: nonterminal attempts become Unknown and
    // refresh_all_relations_after_restore latches relation_fences (reconciliation_required: true).
    let controller = restore_controller_from_log(&log, now_ms + 10);
    register_coop_nodes(&controller, "s2", now_ms + 11);

    let mission_view = route(
        &controller,
        &log,
        &global_admin,
        &request("GET", &format!("/v1/missions/{mission_id}"), b"", None),
    )
    .await;
    let (_, view_json) = parse_http_json(&mission_view);
    assert_eq!(view_json["relations"][0]["state"], "Unknown");
    assert_eq!(view_json["relations"][0]["reconciliation_required"], true);

    let reconcile_path =
        format!("/v1/missions/{mission_id}/relations/safety-guards-navigation/reconcile");

    // Operator role (level 1) and cross-tenant recovery-admin are denied with 403.
    let operator_denied = route(
        &controller,
        &log,
        &scoped_principal("operator", "*"),
        &request("POST", &reconcile_path, b"{}", None),
    )
    .await;
    assert!(
        operator_denied.starts_with("HTTP/1.1 403"),
        "{operator_denied}"
    );
    let tenant_denied = route(
        &controller,
        &log,
        &scoped_principal(RECOVERY_ADMIN_ROLE, "other-tenant"),
        &request("POST", &reconcile_path, b"{}", None),
    )
    .await;
    assert!(tenant_denied.starts_with("HTTP/1.1 403"), "{tenant_denied}");

    // While relation is Unknown (not Satisfied), reconcile returns 409 Conflict and preserves fence.
    let rejected = route(
        &controller,
        &log,
        &global_admin,
        &request("POST", &reconcile_path, b"{}", None),
    )
    .await;
    assert!(rejected.starts_with("HTTP/1.1 409"), "{rejected}");

    // Both nodes report Running on the new session -> relation becomes Satisfied, yet fence stays latched.
    observe_execution_phase(
        &controller,
        &log,
        "cane-a",
        "session-cane-s2",
        &source_exec,
        3,
        WireExecutionPhase::Started,
        now_ms + 15,
    );
    observe_execution_phase(
        &controller,
        &log,
        "dog-a",
        "session-dog-s2",
        &target_exec,
        3,
        WireExecutionPhase::Started,
        now_ms + 16,
    );

    let latched_view = route(
        &controller,
        &log,
        &global_admin,
        &request("GET", &format!("/v1/missions/{mission_id}"), b"", None),
    )
    .await;
    let (_, latched_json) = parse_http_json(&latched_view);
    assert_eq!(latched_json["relations"][0]["state"], "Satisfied");
    assert_eq!(
        latched_json["relations"][0]["reconciliation_required"],
        true
    );

    // Pre-reconcile checkpoint persists the latched fence.
    let pre_saved: ServerCheckpoint =
        serde_json::from_str(&log.load_checkpoint().unwrap().unwrap().checkpoint_json).unwrap();
    let pre_integration: serde_json::Value =
        serde_json::from_str(&pre_saved.integration_json).unwrap();
    assert_eq!(
        pre_integration["runtime"]["relation_fences"]
            .as_array()
            .unwrap()
            .len(),
        1
    );

    // On an isolated clone, verify target completion is BLOCKED while the fence is latched.
    {
        let mut fenced_clone = controller.lock().unwrap().clone();
        let clone_dir = tempfile::tempdir().unwrap();
        let mut clone_log =
            state::SqliteEventLog::open(clone_dir.path().join("clone.sqlite3")).unwrap();
        let corr = domain::CorrelationId::new("fenced-block-check").unwrap();
        fenced_clone
            .bridge
            .consume(
                GrpcNodeEvent::NodeMessage {
                    node_id: "dog-a".into(),
                    session_id: "session-dog-s2".into(),
                    message: NodeMessage {
                        message: Some(NodePayload::ExecutionEvent(WireExecutionEvent {
                            session_id: "session-dog-s2".into(),
                            execution_id: target_exec.clone(),
                            sequence: 4,
                            phase: WireExecutionPhase::Completed as i32,
                            reason: String::new(),
                        })),
                    },
                },
                domain::TimestampMs::new(now_ms + 18),
                &corr,
            )
            .unwrap();
        clone_log.begin_batch().unwrap();
        application::apply_runtime_events(
            &mut fenced_clone,
            domain::TimestampMs::new(now_ms + 18),
            &corr,
            &mut clone_log,
        )
        .unwrap();
        application::apply_runtime_outcomes(
            &mut fenced_clone,
            domain::TimestampMs::new(now_ms + 18),
            &corr,
            &mut clone_log,
        )
        .unwrap();
        clone_log.commit_batch().unwrap();
        let mission = domain::MissionId::new(mission_id).unwrap();
        let group_id = fenced_clone
            .orchestrator
            .execution(&mission)
            .unwrap()
            .group_id()
            .clone();
        assert_eq!(
            fenced_clone
                .orchestrator
                .execution(&mission)
                .unwrap()
                .lifecycle(),
            orchestration::MissionExecutionLifecycle::Running,
            "latched relation fence must block target task completion"
        );
        let nav_status = fenced_clone
            .bridge
            .control()
            .group(&group_id)
            .unwrap()
            .task_executions()
            .find(|task| task.task_ref().task_id().as_str() == "navigate")
            .unwrap()
            .lifecycle();
        assert_eq!(nav_status, domain::TaskExecutionLifecycle::Active);
    }

    // Invalid JSON body returns 400; unknown relation returns 404.
    let bad_body = route(
        &controller,
        &log,
        &global_admin,
        &request("POST", &reconcile_path, b"not-json", None),
    )
    .await;
    assert!(bad_body.starts_with("HTTP/1.1 400"), "{bad_body}");

    let unknown_rel = route(
        &controller,
        &log,
        &global_admin,
        &request(
            "POST",
            &format!("/v1/missions/{mission_id}/relations/no-such-rel/reconcile"),
            b"{}",
            None,
        ),
    )
    .await;
    assert!(unknown_rel.starts_with("HTTP/1.1 404"), "{unknown_rel}");

    // Explicitly reconcile via the new route -> clears fence and persists checkpoint.
    let reconciled = route(
        &controller,
        &log,
        &global_admin,
        &request(
            "POST",
            &reconcile_path,
            br#"{"reason":"operator verified guard resumed"}"#,
            None,
        ),
    )
    .await;
    let (status, rec_json) = parse_http_json(&reconciled);
    assert!(status.starts_with("HTTP/1.1 200"), "{reconciled}");
    assert_eq!(rec_json["status"], "reconciled");
    assert_eq!(rec_json["relation"]["state"], "Satisfied");
    assert_eq!(rec_json["relation"]["reconciliation_required"], false);

    // Persisted checkpoint now has an empty relation_fences list.
    let post_saved: ServerCheckpoint =
        serde_json::from_str(&log.load_checkpoint().unwrap().unwrap().checkpoint_json).unwrap();
    let post_integration: serde_json::Value =
        serde_json::from_str(&post_saved.integration_json).unwrap();
    assert!(
        post_integration["runtime"]["relation_fences"]
            .as_array()
            .unwrap()
            .is_empty()
    );

    // After fence clearance, target & source completions cleanly finalize the Mission to Completed.
    observe_execution_phase(
        &controller,
        &log,
        "dog-a",
        "session-dog-s2",
        &target_exec,
        4,
        WireExecutionPhase::Completed,
        now_ms + 25,
    );
    observe_execution_phase(
        &controller,
        &log,
        "cane-a",
        "session-cane-s2",
        &source_exec,
        4,
        WireExecutionPhase::Completed,
        now_ms + 26,
    );

    let done_view = route(
        &controller,
        &log,
        &global_admin,
        &request("GET", &format!("/v1/missions/{mission_id}"), b"", None),
    )
    .await;
    let (_, done_json) = parse_http_json(&done_view);
    assert_eq!(done_json["status"], "Completed");
    assert_eq!(done_json["relations"][0]["reconciliation_required"], false);

    // Restore from persisted SQLite checkpoint and confirm Completed & unfenced consistency.
    let restored = restore_controller_from_log(&log, now_ms + 30);
    let live = restored.lock().unwrap();
    let mission = domain::MissionId::new(mission_id).unwrap();
    let restored_exec = live.orchestrator.execution(&mission).unwrap();
    assert_eq!(
        restored_exec.lifecycle(),
        orchestration::MissionExecutionLifecycle::Completed
    );
    assert!(!live.bridge.relation_snapshots(restored_exec.group_id())[0].reconciliation_required());
}

/// Hard source violation latches the relation fence, engages SafetyStop isolation, and rejects reconcile.
#[tokio::test]
async fn relation_violation_engages_safety_hold_and_rejects_reconcile() {
    let directory = tempfile::tempdir().unwrap();
    let log = state::SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
    let controller = controller(&log);
    let now_ms = runtime::SystemMonotonicClock::new().now().as_millis();
    register_coop_nodes(&controller, "s1", now_ms);

    let global_admin = scoped_principal(RECOVERY_ADMIN_ROLE, "*");
    let mission_id = "mission-relation-violated-http";
    let plan = concurrent_cooperation_plan(mission_id);
    let submit = route(
        &controller,
        &log,
        &global_admin,
        &request(
            "POST",
            "/v1/missions",
            serde_json::to_vec(&plan).unwrap().as_slice(),
            None,
        ),
    )
    .await;
    assert!(submit.starts_with("HTTP/1.1 202"), "{submit}");
    let now_ms = runtime::SystemMonotonicClock::new().now().as_millis() + 100;

    let source_exec = current_attempt_id(&controller, mission_id, "observe-safety");
    let target_exec = current_attempt_id(&controller, mission_id, "navigate");
    observe_execution_phase(
        &controller,
        &log,
        "cane-a",
        "session-cane-s1",
        &source_exec,
        2,
        WireExecutionPhase::Started,
        now_ms + 5,
    );
    observe_execution_phase(
        &controller,
        &log,
        "dog-a",
        "session-dog-s1",
        &target_exec,
        2,
        WireExecutionPhase::Started,
        now_ms + 6,
    );

    // Source ends early while target is still Running -> Violated + fence latched + SafetyStop hold.
    observe_execution_phase(
        &controller,
        &log,
        "cane-a",
        "session-cane-s1",
        &source_exec,
        3,
        WireExecutionPhase::Completed,
        now_ms + 10,
    );

    let view = route(
        &controller,
        &log,
        &global_admin,
        &request("GET", &format!("/v1/missions/{mission_id}"), b"", None),
    )
    .await;
    let (_, view_json) = parse_http_json(&view);
    assert_eq!(view_json["relations"][0]["state"], "Violated");
    assert_eq!(view_json["relations"][0]["reconciliation_required"], true);
    assert_eq!(view_json["isolation"]["source"], "SafetyStop");

    // Reconcile is strictly rejected while violated and safety-isolated.
    let rejected = route(
        &controller,
        &log,
        &global_admin,
        &request(
            "POST",
            &format!("/v1/missions/{mission_id}/relations/safety-guards-navigation/reconcile"),
            b"{}",
            None,
        ),
    )
    .await;
    assert!(rejected.starts_with("HTTP/1.1 409"), "{rejected}");
}
