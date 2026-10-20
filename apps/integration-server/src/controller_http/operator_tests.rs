//! Offline production-handler and real-mTLS authorization tests, with no process-global role override.

use super::*;
use tokio::io::{AsyncReadExt, AsyncWriteExt};

#[path = "plan_validation_tests.rs"]
mod plan_validation_tests;

#[path = "write_recovery_tests.rs"]
mod write_recovery_tests;

/// Metrics-only identity is explicitly global yet cannot read business projections or any mutation.
#[tokio::test]
async fn metrics_reader_cannot_read_business_or_acquire_mutation_authority() {
    let directory = tempfile::tempdir().unwrap();
    let log = state::SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
    let controller = controller(&log);
    let principal = OperatorPrincipal {
        tenants: Some(BTreeSet::from(["*".into()])),
        subject: Some("monitoring-only".into()),
        role: Some("metrics-reader".into()),
        ..Default::default()
    };
    for path in ["/metrics", "/healthz", "/readyz"] {
        let response = route(
            &controller,
            &log,
            &principal,
            &request("GET", path, b"", None),
        )
        .await;
        assert!(response.starts_with("HTTP/1.1 200"), "{path}: {response}");
    }
    for path in [
        "/v1/inventory",
        "/v1/authority-status",
        "/v1/events",
        "/v1/execution-attempts",
        "/v1/node-identities",
        "/v1/state/records",
        "/v1/missions/unknown",
    ] {
        let response = route(
            &controller,
            &log,
            &principal,
            &request("GET", path, b"", None),
        )
        .await;
        assert!(response.starts_with("HTTP/1.1 403"), "{path}: {response}");
    }
    for path in [
        "/v1/missions",
        "/v1/authority/recover",
        "/v1/node-identities/commands",
        "/v1/missions/validate-plan",
    ] {
        let response = route(
            &controller,
            &log,
            &principal,
            &request("POST", path, b"{}", None),
        )
        .await;
        assert!(response.starts_with("HTTP/1.1 403"), "{path}: {response}");
    }
    assert!(
        route(
            &controller,
            &log,
            &principal,
            &request("GET", "/metrics", b"", Some("recovery-admin"))
        )
        .await
        .starts_with("HTTP/1.1 401")
    );
    assert!(log.events().unwrap().is_empty());
    assert_eq!(log.control_command_count().unwrap(), 0);
    let mut before: ServerCheckpoint =
        serde_json::from_str(&server_checkpoint_json(&controller.lock().unwrap()).unwrap())
            .unwrap();
    before.schema = PRE_OBSERVABILITY_SERVER_CHECKPOINT_SCHEMA.into();
    let mut integration: serde_json::Value =
        serde_json::from_str(&before.integration_json).unwrap();
    integration["control"]["operator_authorization"] =
        serde_json::json!({"grants":{"leaf":{"role":"metrics-reader"}}});
    before.integration_json = integration.to_string();
    assert!(application::validate_isolation_checkpoint_schema(&before).is_err());
}

/// Modern retained wrappers cannot quietly replace the original terminal verifier after restore.
#[test]
fn integrated_legacy_wrappers_retain_original_verifier_configuration_fence() {
    let directory = tempfile::tempdir().unwrap();
    let log = state::SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
    let controller = controller(&log);
    let mut saved: ServerCheckpoint =
        serde_json::from_str(&server_checkpoint_json(&controller.lock().unwrap()).unwrap())
            .unwrap();
    saved.verifier_source_digest = Some("original-source-digest".into());
    for schema in [
        SERVER_CHECKPOINT_SCHEMA,
        PRE_OBSERVABILITY_SERVER_CHECKPOINT_SCHEMA,
        PRE_INTEGRATION_SERVER_CHECKPOINT_SCHEMA,
        PRE_MEMORY_HANDOFF_SERVER_CHECKPOINT_SCHEMA,
        PRE_MEMORY_PREPARATION_SERVER_CHECKPOINT_SCHEMA,
        PRE_MEMORY_SERVER_CHECKPOINT_SCHEMA,
    ] {
        saved.schema = schema.into();
        assert!(
            validate_restored_verifier_source(&saved, Some("original-source-digest")).is_ok(),
            "{schema}"
        );
        assert!(
            validate_restored_verifier_source(&saved, Some("replacement-source-digest")).is_err(),
            "{schema}"
        );
        assert!(
            validate_restored_verifier_source(&saved, None).is_err(),
            "{schema}"
        );
    }
}

/// Creates an empty Controller using the existing application and SQLite authority only.
fn controller(log: &state::SqliteEventLog) -> Arc<Mutex<ControllerState>> {
    Arc::new(Mutex::new(ControllerState {
        bridge: IntegrationRuntimeBridge::new(
            control::ControlPlane::new(),
            state::InMemorySharedNodeState::new(),
            log.clone(),
            integration::GrpcNodeRouter::default(),
        ),
        orchestrator: MissionOrchestrator::new(),
        mission_admissions: BTreeMap::new(),
        memory_imports: BTreeMap::new(),
        memory_preparation: Default::default(),
        memory_use: Default::default(),
        memory_handoffs: BTreeMap::new(),
        safety_stops: BTreeMap::new(),
        verifier_seen: BTreeSet::new(),
        verifier_artifact_store: None,
        verifier_source_digest: None,
    }))
}

/// Encodes one bounded HTTP request, including a possibly forged role hint.
fn request(method: &str, path: &str, body: &[u8], hint: Option<&str>) -> Vec<u8> {
    let header = hint
        .map(|role| format!("X-RoboGuide-Operator-Role: {role}\r\n"))
        .unwrap_or_default();
    let mut bytes = format!(
        "{method} {path} HTTP/1.1\r\nHost: localhost\r\nContent-Length: {}\r\n{header}\r\n",
        body.len()
    )
    .into_bytes();
    bytes.extend(body);
    bytes
}

/// Runs the production request handler with an explicit transport principal over a duplex connection.
async fn route(
    controller: &Arc<Mutex<ControllerState>>,
    log: &state::SqliteEventLog,
    principal: &OperatorPrincipal,
    bytes: &[u8],
) -> String {
    route_with_sources(controller, log, principal, bytes, None, None).await
}

/// Exercises the same HTTP handler with explicit immutable deployment/verifier sources.
async fn route_with_sources(
    controller: &Arc<Mutex<ControllerState>>,
    log: &state::SqliteEventLog,
    principal: &OperatorPrincipal,
    bytes: &[u8],
    deployment: Option<&DeploymentFeasibility>,
    feed: Option<&TaskVerifierFeed>,
) -> String {
    let (mut client, mut server) = tokio::io::duplex(128 * 1024);
    let gate = Arc::new(Mutex::new(()));
    let clock = runtime::SystemMonotonicClock::new();
    let serve = async {
        handle_http_connection_with_principal(
            &mut server,
            controller,
            log,
            &gate,
            &clock,
            deployment,
            feed,
            principal,
        )
        .await
        .unwrap();
        drop(server);
    };
    let call = async {
        client.write_all(bytes).await.unwrap();
        let mut response = String::new();
        client.read_to_string(&mut response).await.unwrap();
        response
    };
    tokio::time::timeout(Duration::from_secs(5), async {
        tokio::join!(serve, call).1
    })
    .await
    .unwrap()
}

/// Every mutation route rejects a mapped viewer before SQLite, Control, Runtime or Orchestration changes.
#[tokio::test]
async fn viewer_cannot_mutate_and_headers_cannot_elevate_authority() {
    let directory = tempfile::tempdir().unwrap();
    let log = state::SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
    let controller = controller(&log);
    let before = server_checkpoint_json(&controller.lock().unwrap()).unwrap();
    let viewer = OperatorPrincipal {
        certificate: None,
        tenants: None,
        subject: Some("test:viewer".into()),
        role: Some("viewer".into()),
        mtls: false,
    };
    let plan = include_bytes!("../../../../scenarios/phase1-mission-v0.3/mission-plan.json");
    for path in [
        "/v1/missions",
        "/v1/node-identities/commands",
        "/v1/missions/a/cancel",
        "/v1/missions/a/resume",
        "/v1/missions/a/review",
        "/v1/missions/a/scheduling-policy",
        "/v1/missions/a/scheduling-preemption",
        "/v1/groups/a/recover",
        "/v1/executions/a/recover",
        "/v1/executions/a/cancel",
        "/v1/maintenance/archive",
    ] {
        let response = route(
            &controller,
            &log,
            &viewer,
            &request("POST", path, plan, None),
        )
        .await;
        assert!(response.starts_with("HTTP/1.1 403"), "{path}: {response}");
    }
    for principal in [viewer, OperatorPrincipal::default()] {
        assert!(
            route(
                &controller,
                &log,
                &principal,
                &request(
                    "POST",
                    "/v1/maintenance/archive",
                    b"{}",
                    Some("recovery-admin")
                )
            )
            .await
            .starts_with("HTTP/1.1 401")
        );
    }
    assert_eq!(
        server_checkpoint_json(&controller.lock().unwrap()).unwrap(),
        before
    );
    assert_eq!(log.len().unwrap(), 0);
    assert!(log.load_checkpoint().unwrap().is_none());
    let operator = OperatorPrincipal {
        certificate: None,
        tenants: None,
        subject: Some("test:operator".into()),
        role: Some("operator".into()),
        mtls: false,
    };
    assert!(
        route(
            &controller,
            &log,
            &operator,
            &request("POST", "/v1/groups/a/recover", b"{}", None)
        )
        .await
        .starts_with("HTTP/1.1 403")
    );
    assert!(
        route(
            &controller,
            &log,
            &operator,
            &request(
                "POST",
                "/v1/missions/a/resume",
                br#"{"isolation_generation":1,"acknowledge_crash_loop":true,"reason":"verified"}"#,
                None
            )
        )
        .await
        .starts_with("HTTP/1.1 403")
    );
    let admin = OperatorPrincipal {
        certificate: None,
        tenants: None,
        subject: Some("test:recovery-admin".into()),
        role: Some(RECOVERY_ADMIN_ROLE.into()),
        mtls: false,
    };
    assert!(
        route(
            &controller,
            &log,
            &admin,
            &request(
                "POST",
                "/v1/missions/a/resume",
                br#"{"isolation_generation":1,"acknowledge_crash_loop":true}"#,
                None
            )
        )
        .await
        .starts_with("HTTP/1.1 400")
    );
    assert!(
        route(
            &controller,
            &log,
            &operator,
            &request("POST", "/v1/missions", plan, None)
        )
        .await
        .starts_with("HTTP/1.1 202")
    );
}

/// A tenant administrator cannot replace a complete deployment authority for another namespace.
#[tokio::test]
async fn tenant_administrator_cannot_read_or_replace_global_source_and_content_authority() {
    let directory = tempfile::tempdir().unwrap();
    let log = state::SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
    let controller = controller(&log);
    let principal = OperatorPrincipal {
        certificate: None,
        tenants: Some(BTreeSet::from(["tenant-a".into()])),
        subject: Some("scoped-admin".into()),
        role: Some("recovery-admin".into()),
        mtls: false,
    };
    let before = server_checkpoint_json(&controller.lock().unwrap()).unwrap();
    for path in [
        "/v1/state-policies",
        "/v1/belief-policies",
        "/v1/verifier-sources",
        "/v1/memory-authorizations",
        "/v1/artifact-authorizations",
    ] {
        for method in ["GET", "POST"] {
            let response = route(
                &controller,
                &log,
                &principal,
                &request(method, path, b"{}", None),
            )
            .await;
            assert!(
                response.starts_with("HTTP/1.1 403"),
                "{method} {path}: {response}"
            );
        }
    }
    assert_eq!(
        server_checkpoint_json(&controller.lock().unwrap()).unwrap(),
        before
    );
}

/// Review distinguishes a SQL outage from contract rejection, rolls back clearing, and can be retried once.
#[tokio::test]
async fn review_sql_failure_preserves_quarantine_and_next_review_succeeds() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("events.sqlite3");
    let log = state::SqliteEventLog::open(&path).unwrap();
    let controller = controller(&log);
    let plan = decode_mission_plan(include_str!(
        "../../../../scenarios/phase1-mission-v0.3/mission-plan.json"
    ))
    .unwrap();
    let mission = plan.goal().mission_id().clone();
    let correlation = domain::CorrelationId::new("review-test").unwrap();
    {
        let mut live = controller.lock().unwrap();
        let ControllerState {
            bridge,
            orchestrator,
            ..
        } = &mut *live;
        orchestrator
            .submit(
                plan,
                domain::ExecutionGroupId::new("review-group").unwrap(),
                bridge.control_mut(),
                domain::TimestampMs::new(1),
                &correlation,
                &mut log.clone(),
            )
            .unwrap();
        for timestamp in [10, 20, 30] {
            orchestrator
                .isolate_execution_with_failure(
                    &mission,
                    orchestration::MissionIsolationSource::ReadyDispatch,
                    orchestration::MissionFailureClass::InvalidContract,
                    "stable".into(),
                    domain::TimestampMs::new(timestamp),
                    &correlation,
                    &mut log.clone(),
                )
                .unwrap();
            if timestamp < 30 {
                let generation = orchestrator
                    .execution(&mission)
                    .unwrap()
                    .isolation()
                    .unwrap()
                    .generation;
                orchestrator
                    .resume_isolated_execution(
                        &mission,
                        generation,
                        domain::TimestampMs::new(timestamp + 1),
                        &correlation,
                        &mut log.clone(),
                    )
                    .unwrap();
            }
        }
        let before = server_checkpoint_json(&live).unwrap();
        log.begin_batch().unwrap();
        log.save_checkpoint(SERVER_CHECKPOINT_SCHEMA, &before)
            .unwrap();
        log.commit_batch().unwrap();
    }
    let before = server_checkpoint_json(&controller.lock().unwrap()).unwrap();
    let sequence = log.latest_sequence().unwrap();
    let sql = rusqlite::Connection::open(&path).unwrap();
    sql.execute_batch("CREATE TRIGGER reject_review_checkpoint BEFORE INSERT ON controller_checkpoint BEGIN SELECT RAISE(ABORT,'review checkpoint outage'); END").unwrap();
    let admin = OperatorPrincipal {
        certificate: None,
        tenants: None,
        subject: Some("test:recovery-admin".into()),
        role: Some(RECOVERY_ADMIN_ROLE.into()),
        mtls: false,
    };
    let bytes = request(
        "POST",
        &format!("/v1/missions/{mission}/review"),
        br#"{"isolation_generation":3,"reason":"checked retained authority"}"#,
        None,
    );
    assert!(
        route(&controller, &log, &admin, &bytes)
            .await
            .starts_with("HTTP/1.1 503")
    );
    assert_eq!(
        server_checkpoint_json(&controller.lock().unwrap()).unwrap(),
        before
    );
    assert_eq!(
        log.load_checkpoint().unwrap().unwrap().checkpoint_json,
        before
    );
    assert_eq!(log.latest_sequence().unwrap(), sequence);
    sql.execute_batch("DROP TRIGGER reject_review_checkpoint")
        .unwrap();
    assert!(
        route(&controller, &log, &admin, &bytes)
            .await
            .starts_with("HTTP/1.1 200")
    );
    let live = controller.lock().unwrap();
    let execution = live.orchestrator.execution(&mission).unwrap();
    assert!(!execution.is_crash_loop_quarantined());
    assert!(execution.isolation().is_none());
    assert!(live.bridge.attempt_history().is_empty());
    assert_eq!(
        log.decoded_events()
            .unwrap()
            .iter()
            .filter(|event| matches!(
                event.payload(),
                domain::EventPayload::MissionExecutionQuarantineCleared {
                    failure_count: 3,
                    ..
                }
            ))
            .count(),
        1
    );
    assert!(
        log.events()
            .unwrap()
            .iter()
            .any(|event| event.payload_schema == "domain.EventPayload.json/v19")
    );
}

/// Builds an actual TLS client with this fixture CA and one separately issued leaf.
fn client(pki: &testkit::TestPki, identity: &str) -> tokio_rustls::TlsConnector {
    let mut roots = rustls::RootCertStore::empty();
    for certificate in operator_certificates(&pki.ca_cert_path).unwrap() {
        roots.add(certificate).unwrap();
    }
    let leaf = &pki.identities[identity];
    let config = rustls::ClientConfig::builder()
        .with_root_certificates(roots)
        .with_client_auth_cert(
            operator_certificates(&leaf.cert_path).unwrap(),
            operator_private_key(&leaf.key_path).unwrap(),
        )
        .unwrap();
    tokio_rustls::TlsConnector::from(Arc::new(config))
}

/// Real handshakes bind the mapped leaf to its role; a CA-valid unmapped leaf receives no application access.
#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn actual_mtls_viewer_and_unmapped_certificate_cannot_submit() {
    let directory = tempfile::tempdir().unwrap();
    let pki = testkit::TestPki::new(
        &directory.path().join("pki"),
        &["server", "viewer", "unmapped"],
    )
    .unwrap();
    let viewer_certificate = operator_certificates(&pki.identities["viewer"].cert_path)
        .unwrap()
        .remove(0);
    let digest = format!("sha256:{:x}", Sha256::digest(viewer_certificate.as_ref()));
    let roles_path = directory.path().join("roles.json");
    std::fs::write(
        &roles_path,
        serde_json::to_vec(&BTreeMap::from([(digest, "viewer")])).unwrap(),
    )
    .unwrap();
    let server_leaf = &pki.identities["server"];
    let profile = load_operator_tls(
        &server_leaf.cert_path,
        &server_leaf.key_path,
        &pki.ca_cert_path,
        &roles_path,
    )
    .unwrap();
    let log = state::SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
    let controller = controller(&log);
    for identity in ["viewer", "unmapped"] {
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        let connector = client(&pki, identity);
        let gate = Arc::new(Mutex::new(()));
        let clock = runtime::SystemMonotonicClock::new();
        let serve = async {
            let (stream, _) = listener.accept().await.unwrap();
            let mut stream = profile.acceptor.accept(stream).await.unwrap();
            match profile.principal(&stream) {
                Ok(principal) => {
                    handle_http_connection_with_principal(
                        &mut stream,
                        &controller,
                        &log,
                        &gate,
                        &clock,
                        None,
                        None,
                        &principal,
                    )
                    .await
                    .unwrap();
                    stream.shutdown().await.unwrap();
                    true
                }
                Err(_) => {
                    stream.shutdown().await.unwrap();
                    false
                }
            }
        };
        let call = async {
            let stream = tokio::net::TcpStream::connect(address).await.unwrap();
            let mut stream = connector
                .connect(
                    rustls::pki_types::ServerName::try_from("localhost").unwrap(),
                    stream,
                )
                .await
                .unwrap();
            let _ = stream
                .write_all(&request(
                    "POST",
                    "/v1/missions",
                    include_bytes!("../../../../scenarios/phase1-mission-v0.3/mission-plan.json"),
                    None,
                ))
                .await;
            let mut response = String::new();
            let _ = stream.read_to_string(&mut response).await;
            response
        };
        let (mapped, response) =
            tokio::time::timeout(Duration::from_secs(8), async { tokio::join!(serve, call) })
                .await
                .unwrap();
        assert_eq!(mapped, identity == "viewer");
        if mapped {
            assert!(response.starts_with("HTTP/1.1 403"), "{response}");
        } else {
            assert!(!response.contains("202 Accepted"));
        }
    }
    assert_eq!(log.len().unwrap(), 0);
    assert!(
        controller
            .lock()
            .unwrap()
            .orchestrator
            .mission_ids()
            .is_empty()
    );
}

/// Builds an empty application whose registry and transport use the same explicit lifecycle profile.
fn identity_controller(log: &state::SqliteEventLog) -> Arc<Mutex<ControllerState>> {
    let registry = domain::NodeIdentityRegistry::new("test-deployment");
    let (events, _receiver) = tokio::sync::mpsc::unbounded_channel();
    let (_service, router) = integration::GrpcIntegrationService::new_with_observer_and_identity(
        events,
        ports::NodeProtocolPolicy::default(),
        Arc::new(runtime::SystemMonotonicClock::new()),
        ports::DiagnosticHandle::default(),
        integration::NodeIdentityPolicy::require_lifecycle(registry.clone()).unwrap(),
    );
    let mut control = control::ControlPlane::new();
    control.bootstrap_node_identity_registry(registry).unwrap();
    Arc::new(Mutex::new(ControllerState {
        bridge: IntegrationRuntimeBridge::new(
            control,
            state::InMemorySharedNodeState::new(),
            log.clone(),
            router,
        ),
        orchestrator: MissionOrchestrator::new(),
        mission_admissions: BTreeMap::new(),
        memory_imports: BTreeMap::new(),
        memory_preparation: Default::default(),
        memory_use: Default::default(),
        memory_handoffs: BTreeMap::new(),
        safety_stops: BTreeMap::new(),
        verifier_seen: BTreeSet::new(),
        verifier_artifact_store: None,
        verifier_source_digest: None,
    }))
}

/// Public admin mutations persist revision/dedup/audit evidence and never synthesize a registered device.
#[tokio::test]
async fn node_identity_http_commands_are_authorized_durable_and_do_not_invent_nodes() {
    let directory = tempfile::tempdir().unwrap();
    let log = state::SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
    let controller = identity_controller(&log);
    let admin = OperatorPrincipal {
        certificate: None,
        tenants: None,
        subject: Some("sha256:admin-leaf".into()),
        role: Some(RECOVERY_ADMIN_ROLE.into()),
        mtls: false,
    };
    let operator = OperatorPrincipal {
        certificate: None,
        tenants: None,
        subject: Some("sha256:operator-leaf".into()),
        role: Some("operator".into()),
        mtls: false,
    };
    let register = domain::IdentityCommand {
        command_id: "initial-enrolment".into(),
        node_id: "deployment-node".into(),
        expected_revision: 0,
        action: domain::IdentityAction::Register {
            tenant_id: "tenant".into(),
            domain_id: "domain".into(),
            digest: "a".repeat(64),
            expires_at_ms: 0,
        },
    };
    let body = serde_json::to_vec(&register).unwrap();
    let request_bytes = request("POST", "/v1/node-identities/commands", &body, None);
    assert!(
        route(&controller, &log, &operator, &request_bytes)
            .await
            .starts_with("HTTP/1.1 403")
    );
    assert!(log.load_checkpoint().unwrap().is_none());
    let first = route(&controller, &log, &admin, &request_bytes).await;
    assert!(first.starts_with("HTTP/1.1 200"), "{first}");
    assert!(first.contains("sha256:admin-leaf"));
    let second = route(&controller, &log, &admin, &request_bytes).await;
    assert_eq!(
        first.split("\r\n\r\n").nth(1),
        second.split("\r\n\r\n").nth(1)
    );
    let registry = controller
        .lock()
        .unwrap()
        .bridge
        .control()
        .node_identity_registry()
        .unwrap()
        .clone();
    assert_eq!(registry.records["deployment-node"].revision, 1);
    assert_eq!(registry.commands.len(), 1);
    assert_eq!(controller.lock().unwrap().bridge.state().nodes().len(), 0);
    let saved: ServerCheckpoint =
        serde_json::from_str(&log.load_checkpoint().unwrap().unwrap().checkpoint_json).unwrap();
    let bridge = IntegrationRuntimeBridge::restore_from_checkpoint(
        &saved.integration_json,
        log.clone(),
        integration::GrpcNodeRouter::default(),
        domain::TimestampMs::new(1),
    )
    .unwrap();
    assert_eq!(bridge.control().node_identity_registry(), Some(&registry));
    let mut legacy = saved;
    legacy.schema = PRE_IDENTITY_SERVER_CHECKPOINT_SCHEMA.into();
    assert!(application::validate_isolation_checkpoint_schema(&legacy).is_err());
    let revoke = domain::IdentityCommand {
        command_id: "revoke".into(),
        node_id: register.node_id,
        expected_revision: 1,
        action: domain::IdentityAction::Revoke,
    };
    assert!(
        route(
            &controller,
            &log,
            &admin,
            &request(
                "POST",
                "/v1/node-identities/commands",
                &serde_json::to_vec(&revoke).unwrap(),
                None
            )
        )
        .await
        .starts_with("HTTP/1.1 200")
    );
    assert_eq!(
        controller
            .lock()
            .unwrap()
            .bridge
            .control()
            .node_identity_registry()
            .unwrap()
            .records["deployment-node"]
            .phase,
        domain::IdentityPhase::Revoked
    );
}

/// A failed checkpoint commits neither live lifecycle authority nor its shared transport projection.
#[tokio::test]
async fn node_identity_sql_failure_preserves_the_original_registry() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("events.sqlite3");
    let log = state::SqliteEventLog::open(&path).unwrap();
    let controller = identity_controller(&log);
    let before = server_checkpoint_json(&controller.lock().unwrap()).unwrap();
    rusqlite::Connection::open(&path).unwrap().execute_batch("CREATE TRIGGER reject_identity BEFORE INSERT ON controller_checkpoint BEGIN SELECT RAISE(ABORT,'injected identity checkpoint outage'); END").unwrap();
    let admin = OperatorPrincipal {
        certificate: None,
        tenants: None,
        subject: Some("admin".into()),
        role: Some(RECOVERY_ADMIN_ROLE.into()),
        mtls: false,
    };
    let command = domain::IdentityCommand {
        command_id: "enrol".into(),
        node_id: "node".into(),
        expected_revision: 0,
        action: domain::IdentityAction::Register {
            tenant_id: "tenant".into(),
            domain_id: "domain".into(),
            digest: "a".repeat(64),
            expires_at_ms: 0,
        },
    };
    let response = route(
        &controller,
        &log,
        &admin,
        &request(
            "POST",
            "/v1/node-identities/commands",
            &serde_json::to_vec(&command).unwrap(),
            None,
        ),
    )
    .await;
    assert!(response.starts_with("HTTP/1.1 409"));
    assert_eq!(
        before,
        server_checkpoint_json(&controller.lock().unwrap()).unwrap()
    );
    assert_eq!(log.control_command_count().unwrap(), 0);
    log.begin_batch().unwrap();
    log.rollback_batch().unwrap();
}

/// Public tenant projections and mutations consume durable ownership, including trusted legacy migration.
#[tokio::test]
async fn tenant_http_scope_and_legacy_migration_preserve_plan_and_ownership() {
    let directory = tempfile::tempdir().unwrap();
    let log = state::SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
    let controller = controller(&log);
    let plan = decode_mission_plan(
        std::str::from_utf8(include_bytes!(
            "../../../../scenarios/phase1-mission-v0.3/mission-plan.json"
        ))
        .unwrap(),
    )
    .unwrap();
    let mission = plan.goal().mission_id().clone();
    {
        let mut live = controller.lock().unwrap();
        let ControllerState {
            bridge,
            orchestrator,
            ..
        } = &mut *live;
        orchestrator
            .submit(
                plan,
                domain::ExecutionGroupId::new("legacy-tenant-group").unwrap(),
                bridge.control_mut(),
                domain::TimestampMs::new(1),
                &domain::CorrelationId::new("legacy").unwrap(),
                &mut log.clone(),
            )
            .unwrap();
    }
    let admin = OperatorPrincipal {
        certificate: None,
        tenants: Some(BTreeSet::from(["*".into()])),
        subject: Some("test:admin".into()),
        role: Some(RECOVERY_ADMIN_ROLE.into()),
        mtls: false,
    };
    let scoped = OperatorPrincipal {
        certificate: None,
        tenants: Some(BTreeSet::from(["a".into()])),
        subject: Some("test:tenant-a".into()),
        role: Some("operator".into()),
        mtls: false,
    };
    assert!(
        route(
            &controller,
            &log,
            &scoped,
            &request("GET", &format!("/v1/missions/{mission}/tenant"), b"", None)
        )
        .await
        .starts_with("HTTP/1.1 403")
    );
    let digest = application::tenant::source_digest(&controller.lock().unwrap(), &mission).unwrap();
    let command = control::MissionTenantMigration {
        command_id: "migrate-legacy".into(),
        mission_id: mission.clone(),
        tenant_id: "a".into(),
        source_plan_digest: digest,
    };
    let bytes = serde_json::to_vec(&command).unwrap();
    let first = route(
        &controller,
        &log,
        &admin,
        &request("POST", "/v1/tenant-migrations", &bytes, None),
    )
    .await;
    assert!(first.starts_with("HTTP/1.1 200"), "{first}");
    assert!(first.contains("execution_repeated\":false"));
    let replay = route(
        &controller,
        &log,
        &admin,
        &request("POST", "/v1/tenant-migrations", &bytes, None),
    )
    .await;
    assert_eq!(
        first.split("\r\n\r\n").nth(1),
        replay.split("\r\n\r\n").nth(1)
    );
    assert!(
        route(
            &controller,
            &log,
            &scoped,
            &request("GET", &format!("/v1/missions/{mission}/tenant"), b"", None)
        )
        .await
        .starts_with("HTTP/1.1 200")
    );
    let mut forged = request("GET", &format!("/v1/missions/{mission}/tenant"), b"", None);
    let end = forged.len() - 2;
    forged.splice(end..end, b"X-RoboGuide-Tenant: b\r\n".iter().copied());
    assert!(
        route(&controller, &log, &scoped, &forged)
            .await
            .starts_with("HTTP/1.1 403")
    );
    let foreign = OperatorPrincipal {
        tenants: Some(BTreeSet::from(["b".into()])),
        ..scoped.clone()
    };
    for path in [
        format!("/v1/missions/{mission}/cancel"),
        "/v1/groups/legacy-tenant-group/recover".into(),
    ] {
        assert!(
            route(
                &controller,
                &log,
                &foreign,
                &request("POST", &path, b"{}", None)
            )
            .await
            .starts_with("HTTP/1.1 403")
        );
    }
    assert!(
        controller
            .lock()
            .unwrap()
            .bridge
            .attempt_history()
            .is_empty()
    );
    let saved: ServerCheckpoint =
        serde_json::from_str(&log.load_checkpoint().unwrap().unwrap().checkpoint_json).unwrap();
    let restored = IntegrationRuntimeBridge::restore_from_checkpoint(
        &saved.integration_json,
        log.clone(),
        integration::GrpcNodeRouter::default(),
        domain::TimestampMs::new(1),
    )
    .unwrap();
    assert_eq!(restored.control().mission_tenant(&mission), Some("a"));
}

/// Deployment revision changes are durable and a request parsed after revocation cannot retain its old grant.
#[tokio::test]
async fn operator_authorization_fences_captured_principal_and_restart_rollback() {
    let directory = tempfile::tempdir().unwrap();
    let log = state::SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
    let controller = controller(&log);
    let path = directory.path().join("authorization.json");
    let digest = format!("sha256:{}", "a".repeat(64));
    let mut profile = domain::OperatorAuthorization {
        schema_version: "roboguide.operator-authorization/v0.1".into(),
        registry_id: "deployment".into(),
        revision: 1,
        grants: BTreeMap::from([(
            digest.clone(),
            domain::OperatorCertificateGrant {
                principal_id: "stable-operator".into(),
                role: RECOVERY_ADMIN_ROLE.into(),
                tenant_ids: BTreeSet::from(["*".into()]),
                not_before_ms: 0,
                expires_at_ms: 0,
                revoked: false,
            },
        )]),
    };
    std::fs::write(&path, serde_json::to_vec(&profile).unwrap()).unwrap();
    let principal = OperatorPrincipal {
        certificate: Some(OperatorCertificateContext {
            digest: digest.clone(),
            expires_at_ms: u64::MAX,
            path: path.clone(),
        }),
        tenants: Some(BTreeSet::from(["*".into()])),
        subject: Some(digest.clone()),
        role: Some(RECOVERY_ADMIN_ROLE.into()),
        mtls: true,
    };
    let first = route(
        &controller,
        &log,
        &principal,
        &request("GET", "/v1/node-identities", b"", None),
    )
    .await;
    assert!(first.starts_with("HTTP/1.1 200"), "{first}");
    assert_eq!(
        controller
            .lock()
            .unwrap()
            .bridge
            .control()
            .operator_authorization(),
        Some(&profile)
    );
    profile.revision = 2;
    profile.grants.get_mut(&digest).unwrap().revoked = true;
    std::fs::write(&path, serde_json::to_vec(&profile).unwrap()).unwrap();
    let revoked = route(
        &controller,
        &log,
        &principal,
        &request("GET", "/v1/node-identities", b"", None),
    )
    .await;
    assert!(revoked.starts_with("HTTP/1.1 401"), "{revoked}");
    let saved: ServerCheckpoint =
        serde_json::from_str(&log.load_checkpoint().unwrap().unwrap().checkpoint_json).unwrap();
    let mut restored = IntegrationRuntimeBridge::restore_from_checkpoint(
        &saved.integration_json,
        log.clone(),
        integration::GrpcNodeRouter::default(),
        domain::TimestampMs::new(1),
    )
    .unwrap();
    profile.revision = 1;
    profile.grants.get_mut(&digest).unwrap().revoked = false;
    assert!(
        restored
            .control_mut()
            .accept_operator_authorization(Some(profile.clone()))
            .is_err()
    );
    std::fs::write(&path, serde_json::to_vec(&profile).unwrap()).unwrap();
    assert!(
        route(
            &controller,
            &log,
            &principal,
            &request("GET", "/v1/node-identities", b"", None)
        )
        .await
        .starts_with("HTTP/1.1 401")
    );
}

/// A parsed administrator command waits on the real writer while revocation commits ahead of it.
#[test]
fn queued_operator_command_rechecks_transport_authority_before_any_mutation() {
    for change in ["revoked", "role", "tenant", "validity", "unchanged"] {
        let directory = tempfile::tempdir().unwrap();
        let log = state::SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
        let controller = controller(&log);
        let gate = Arc::new(Mutex::new(()));
        let path = directory.path().join("operator.json");
        let digest = format!("sha256:{}", "b".repeat(64));
        let mut profile = domain::OperatorAuthorization {
            schema_version: "roboguide.operator-authorization/v0.1".into(),
            registry_id: "queued-authority".into(),
            revision: 1,
            grants: BTreeMap::from([(
                digest.clone(),
                domain::OperatorCertificateGrant {
                    principal_id: "original-stable-admin".into(),
                    role: RECOVERY_ADMIN_ROLE.into(),
                    tenant_ids: BTreeSet::from(["*".into()]),
                    not_before_ms: 0,
                    expires_at_ms: 0,
                    revoked: false,
                },
            )]),
        };
        std::fs::write(&path, serde_json::to_vec(&profile).unwrap()).unwrap();
        synchronize_authority(&controller, &log, &gate, Some(profile.clone())).unwrap();
        let principal = OperatorPrincipal {
            certificate: Some(OperatorCertificateContext {
                digest: digest.clone(),
                expires_at_ms: u64::MAX,
                path: path.clone(),
            }),
            tenants: Some(BTreeSet::from(["*".into()])),
            subject: Some("original-stable-admin".into()),
            role: Some(RECOVERY_ADMIN_ROLE.into()),
            mtls: true,
        };
        let metadata = ControlHttpRequest {
            method: "POST".into(),
            target: "/v1/memory-authorizations".into(),
            headers: BTreeMap::new(),
            body: Vec::new(),
        };
        let request_gate = Arc::new(AuthorizedRequestGate::new(
            gate.clone(),
            controller.clone(),
            principal,
            &metadata,
            None,
            domain::TimestampMs::new(10),
        ));
        let body = serde_json::to_vec(&serde_json::json!({
            "command_id":"queued-original-command", "expected_revision":0,
            "registry":{"schema_version":domain::MEMORY_AUTHORIZATION_SCHEMA,
                        "registry_id":"queued-memory", "revision":1, "policies":{}}
        }))
        .unwrap();
        let writer = gate.lock().unwrap();
        let barrier = Arc::new(std::sync::Barrier::new(2));
        let pending = {
            let controller = controller.clone();
            let log = log.clone();
            let barrier = barrier.clone();
            std::thread::spawn(move || {
                barrier.wait();
                application::state_policy::submit_memory(
                    &controller,
                    &log,
                    &request_gate,
                    &body,
                    "original-stable-admin",
                    domain::TimestampMs::new(10),
                )
            })
        };
        barrier.wait();
        profile.revision += 1;
        let grant = profile.grants.get_mut(&digest).unwrap();
        match change {
            "revoked" => grant.revoked = true,
            "role" => {
                grant.role = "viewer".into();
                grant.tenant_ids = BTreeSet::from(["a".into()]);
            }
            "tenant" => grant.tenant_ids = BTreeSet::from(["a".into()]),
            "validity" => grant.expires_at_ms = 5,
            "unchanged" => {}
            _ => unreachable!(),
        }
        std::fs::write(&path, serde_json::to_vec(&profile).unwrap()).unwrap();
        let before = {
            let mut live = controller.lock().unwrap();
            let mut candidate = live.clone();
            candidate
                .bridge
                .control_mut()
                .accept_operator_authorization(Some(profile))
                .unwrap();
            let checkpoint = server_checkpoint_json(&candidate).unwrap();
            log.begin_batch().unwrap();
            save_controller_checkpoint(&log, &checkpoint).unwrap();
            log.commit_batch().unwrap();
            *live = candidate;
            checkpoint
        };
        let committed = log.load_checkpoint().unwrap().unwrap();
        let count = log.len().unwrap();
        drop(writer);
        let result = pending.join().unwrap();
        if change == "unchanged" {
            assert!(result.is_ok(), "{result:?}");
            assert!(
                controller
                    .lock()
                    .unwrap()
                    .bridge
                    .control()
                    .memory_authorization_registry()
                    .is_some()
            );
        } else {
            assert!(
                result.is_err(),
                "stale {change} command committed: {result:?}"
            );
            assert_eq!(
                server_checkpoint_json(&controller.lock().unwrap()).unwrap(),
                before
            );
            assert_eq!(
                log.load_checkpoint().unwrap().unwrap().checkpoint_json,
                committed.checkpoint_json
            );
            assert_eq!(log.len().unwrap(), count);
        }
    }
}
