//! Public HTTP verifier consumption, durable deadlines and real CAS bytes; no hardware execution claims.

use super::*;
use domain::TimestampMs;
use tokio::io::{AsyncReadExt, AsyncWriteExt};

/// Owns isolated real SQLite/CAS stores and existing Controller authorities for protocol tests.
struct Fixture {
    /// Keeps temporary files alive for every production-handler request.
    directory: tempfile::TempDir,
    /// Actual durable event/checkpoint store.
    log: state::SqliteEventLog,
    /// Actual Controller composition, not a second execution authority.
    controller: Arc<Mutex<ControllerState>>,
    /// Shared application serialization gate.
    gate: Arc<crate::application::ApplicationWriter>,
}

impl Fixture {
    /// Registers one explicitly software-only test contract before exercising the production HTTP routes.
    fn new() -> Self {
        let directory = tempfile::tempdir().unwrap();
        let log = state::SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
        let store =
            artifact_store::FileSystemArtifactStore::new(directory.path().join("cas")).unwrap();
        let mut control = control::ControlPlane::new();
        let mut nodes = state::InMemorySharedNodeState::new();
        control
            .register_node(
                &mut nodes,
                recovery_driver_node("software-worker", "software-cpu"),
                domain::NodeStatus::new(domain::NodeHealth::Online, TimestampMs::new(1)),
                TimestampMs::new(1),
                &domain::CorrelationId::new("software-contract").unwrap(),
                &mut log.clone(),
            )
            .unwrap();
        let controller = Arc::new(Mutex::new(ControllerState {
            federation: Default::default(),
            mission_admissions: BTreeMap::new(),
            memory_imports: BTreeMap::new(),
            memory_preparation: Default::default(),
            memory_use: Default::default(),
            memory_handoffs: BTreeMap::new(),
            runtime_revisions: Default::default(),
            hazards: application::hazards::HazardState::default(),
            safety_stops: BTreeMap::new(),
            bridge: IntegrationRuntimeBridge::new(
                control,
                nodes,
                log.clone(),
                integration::GrpcNodeRouter::default(),
            ),
            orchestrator: MissionOrchestrator::new(),
            verifier_seen: BTreeSet::new(),
            verifier_source_digest: None,
            verifier_artifact_store: Some(store),
        }));
        Self {
            directory,
            log,
            controller,
            gate: Arc::new(crate::application::ApplicationWriter::default()),
        }
    }

    /// Exchanges a real framed HTTP request with the production authority handler over bounded duplex IO.
    async fn request(
        &self,
        path: &str,
        body: &serde_json::Value,
        admin: bool,
        at: u64,
    ) -> (u16, serde_json::Value) {
        let (mut client, mut server) = tokio::io::duplex(256 * 1024);
        let clock = runtime::FixedClock::new(TimestampMs::new(at));
        let serve = async {
            if admin {
                handle_recovery_admin_test_connection(
                    &mut server,
                    &self.controller,
                    &self.log,
                    &self.gate,
                    &clock,
                    None,
                    None,
                )
                .await
                .unwrap();
            } else {
                handle_http_connection(
                    &mut server,
                    &self.controller,
                    &self.log,
                    &self.gate,
                    &clock,
                    None,
                    None,
                )
                .await
                .unwrap();
            }
            drop(server);
        };
        let call = async {
            let encoded = body.to_string();
            client.write_all(format!("POST {path} HTTP/1.1\r\nHost: localhost\r\nContent-Length: {}\r\n\r\n{encoded}",encoded.len()).as_bytes()).await.unwrap();
            let mut response = Vec::new();
            client.read_to_end(&mut response).await.unwrap();
            let response = String::from_utf8(response).unwrap();
            let (head, body) = response.split_once("\r\n\r\n").unwrap();
            (
                head.split_whitespace().nth(1).unwrap().parse().unwrap(),
                serde_json::from_str(body).unwrap(),
            )
        };
        tokio::time::timeout(Duration::from_secs(10), async {
            tokio::join!(serve, call).1
        })
        .await
        .unwrap()
    }

    /// Installs a stable independently authenticated measurement producer through its public admin API.
    async fn sources(&self, continuous: bool, require_artifact: bool) {
        assert_eq!(
            self.request(
                "/v1/verifier-sources",
                &registry(continuous, require_artifact),
                true,
                2
            )
            .await
            .0,
            200
        );
    }

    /// Admits a single-Actor two-step compute plan whose intermediate Task needs independent verification.
    async fn submit(&self) {
        assert_eq!(self.request("/v1/missions", &plan(), false, 3).await.0, 202);
    }

    /// Applies actual protocol fact reduction but deliberately makes no device or physical-success claim.
    fn fact(
        &self,
        task: &str,
        sequence: u64,
        phase: integration::grpc::v0_4::ExecutionPhase,
        at: u64,
    ) {
        let mut controller = self.controller.lock().unwrap();
        let attempt = controller
            .bridge
            .attempt_history()
            .into_iter()
            .find(|attempt| attempt.command().task_id().as_str() == task)
            .unwrap();
        controller
            .bridge
            .consume(
                integration::GrpcNodeEvent::NodeMessage {
                    node_id: attempt.command().node_id().to_string(),
                    session_id: "software-contract-session".into(),
                    message: integration::grpc::v0_4::NodeMessage {
                        message: Some(
                            integration::grpc::v0_4::node_message::Message::ExecutionSnapshot(
                                integration::grpc::v0_4::ExecutionSnapshot {
                                    session_id: "software-contract-session".into(),
                                    execution_id: attempt.execution_id().into(),
                                    last_sequence: sequence,
                                    phase: phase as i32,
                                    reason: "software protocol input".into(),
                                },
                            ),
                        ),
                    },
                },
                TimestampMs::new(at),
                &domain::CorrelationId::new("software-fact").unwrap(),
            )
            .unwrap();
        apply_runtime_events(
            &mut controller,
            TimestampMs::new(at),
            &domain::CorrelationId::new("software-fact").unwrap(),
            &mut self.log.clone(),
        )
        .unwrap();
    }

    /// Advances only the actual application timer transaction at the explicit receive-relative time.
    fn tick(&self, at: u64) {
        drive_application_timer(
            &self.controller,
            &self.log,
            &self.gate,
            TimestampMs::new(at),
            None,
        )
        .unwrap();
    }

    /// Binds one producer payload to the actual accepted plan and complete current Task attempt set.
    fn evidence(
        &self,
        task: &str,
        id: &str,
        sequence: u64,
        verdict: &str,
        finality: &str,
    ) -> serde_json::Value {
        let controller = self.controller.lock().unwrap();
        let attempt = controller
            .bridge
            .attempt_history()
            .into_iter()
            .find(|attempt| attempt.command().task_id().as_str() == task)
            .unwrap();
        serde_json::json!({"evidence_id":id,"source_id":"independent-verifier","source_revision":1,"tenant_id":"local",
            "task_ref":attempt.command().task_ref(),"plan_digest":application::tenant::source_digest(&controller,attempt.command().mission_id()).unwrap(),
            "attempts":[{"role_id":attempt.command().role_id(),"attempt_id":attempt.execution_id()}],
            "predicate":"computed artifact matches","verdict":verdict,"finality":finality,"source_observed_at_ms":1,
            "valid_for_ms":10,"sequence":sequence,"artifact_digest":null,"artifact_size_bytes":null})
    }
}

/// A per-Task producer keeps legitimate intermediate verification and DAG dependencies.
#[tokio::test]
async fn plan_preflight_keeps_timely_independent_verifier_dependencies() {
    let fixture = Fixture::new();
    fixture.sources(false, false).await;
    let before = server_checkpoint_json(&fixture.controller.lock().unwrap()).unwrap();
    let sequence = fixture.log.latest_sequence().unwrap();
    let (status, result) = fixture
        .request("/v1/missions/validate-plan", &plan(), false, 3)
        .await;
    assert_eq!(status, 200);
    assert_eq!(result["decision"], "not_blocked");
    assert_eq!(result["verifier_source_digest"], serde_json::Value::Null);
    assert_eq!(
        server_checkpoint_json(&fixture.controller.lock().unwrap()).unwrap(),
        before
    );
    assert_eq!(fixture.log.latest_sequence().unwrap(), sequence);
}

/// Declares software producer ownership and nonrenewable continuous measurement bounds.
fn registry(continuous: bool, require_artifact: bool) -> serde_json::Value {
    serde_json::json!({"command_id":"enroll-verifier","expected_revision":0,"registry":{
        "schema_version":"roboguide.semantic-verifier-registry/v0.1","registry_id":"software-verifiers","revision":1,
        "sources":{"independent-verifier":{"source_id":"independent-verifier","revision":1,"tenant_id":"local",
            "producer_principals":["trusted-loopback:operator"],"verifier":{"namespace":"observation","name":"verify","version":"v1"},
            "predicates":["computed artifact matches"],"max_age_ms":50,"stop_timeout_ms":10000,"enabled":true,"continuous":continuous,"require_artifact":require_artifact}}}})
}

/// Defines a generic exact-contract dependency with no physical entity or device name in production behavior.
fn plan() -> serde_json::Value {
    let contract = serde_json::json!({"namespace":"compute","name":"work","version":"v1"});
    let task = |id: &str, dependencies: Vec<&str>| {
        serde_json::json!({"id":id,"description":"compute software result","context_id":"session","depends_on":dependencies,
        "roles":[{"id":"worker","context_role":"executor","requirements":{"capabilities":[{"contract":contract,"constraints":[]}],"resources":[{"kind":"compute","units":1}]},
            "execution_intent":{"operation":contract,"objective":"compute immutable software artifact","parameters":{}},"resource_scope":"task"}],
        "timing":{"earliest_start_offset_ms":0,"latest_start_offset_ms":null,"completion_deadline_offset_ms":null},
        "satisfaction":{"expected_effect":"computed artifact matches","basis":"verifier-evidence","verifier":{"contract":{"namespace":"observation","name":"verify","version":"v1"},"predicate":"computed artifact matches","max_evidence_age_ms":5000}}})
    };
    serde_json::json!({"schema_version":"roboguide.mission-plan/v0.7","mission":{"id":"verified-software","objective":"verify both software steps","actors":[{"id":"executor"}]},
        "contexts":[{"id":"session","roles":[{"id":"executor","actor":"executor"}],"relations":[],"coupling_mode":"independent"}],
        "tasks":[task("first",vec![]),task("second",vec!["first"])]})
}

/// Local completion retains ownership until final independent evidence, then releases and advances the DAG once.
#[test]
fn intermediate_verifier_releases_task_and_dispatches_dependency_once() {
    std::thread::Builder::new()
        .name("semantic-verifier-test".into())
        .stack_size(16 * 1024 * 1024)
        .spawn(|| {
            tokio::runtime::Builder::new_multi_thread()
                .worker_threads(2)
                .enable_all()
                .build()
                .expect("semantic verifier test runtime")
                .block_on(
                    intermediate_verifier_releases_task_and_dispatches_dependency_once_async(),
                );
        })
        .expect("semantic verifier test thread")
        .join()
        .expect("semantic verifier test thread did not panic");
}

/// Runs the semantic verifier scenario on an explicitly bounded large-stack test thread.
async fn intermediate_verifier_releases_task_and_dispatches_dependency_once_async() {
    let fixture = Fixture::new();
    fixture.sources(false, false).await;
    fixture.submit().await;
    fixture.fact(
        "first",
        1,
        integration::grpc::v0_4::ExecutionPhase::Started,
        4,
    );
    fixture.fact(
        "first",
        2,
        integration::grpc::v0_4::ExecutionPhase::Completed,
        5,
    );
    fixture.tick(5);
    assert_eq!(
        fixture
            .controller
            .lock()
            .unwrap()
            .bridge
            .attempt_history()
            .len(),
        1
    );
    let unknown = fixture.evidence("first", "unknown", 1, "unknown", "final");
    assert_eq!(
        fixture
            .request("/v1/verifier-evidence", &unknown, false, 6)
            .await
            .0,
        200
    );
    assert_eq!(
        fixture
            .controller
            .lock()
            .unwrap()
            .bridge
            .attempt_history()
            .len(),
        1
    );
    let input = fixture.evidence("first", "positive", 2, "satisfied", "final");
    let first = fixture
        .request("/v1/verifier-evidence", &input, false, 7)
        .await;
    assert_eq!(first.0, 200);
    assert_eq!(first.1["receipt"]["applied"], true);
    assert_eq!(
        fixture
            .controller
            .lock()
            .unwrap()
            .bridge
            .attempt_history()
            .len(),
        2
    );
    let repeated = fixture
        .request("/v1/verifier-evidence", &input, false, 10)
        .await;
    assert_eq!(repeated, first);
    assert_eq!(
        fixture
            .controller
            .lock()
            .unwrap()
            .bridge
            .attempt_history()
            .len(),
        2
    );
    let mut stale = input.clone();
    stale["evidence_id"] = "wrong-attempt".into();
    stale["attempts"][0]["attempt_id"] = "unowned-attempt".into();
    assert_eq!(
        fixture
            .request("/v1/verifier-evidence", &stale, false, 11)
            .await
            .0,
        409
    );
}

/// Checkpoint failure commits neither admitted evidence, semantic success, resources nor dependent Execute.
#[tokio::test]
async fn verifier_checkpoint_failure_is_atomic_and_retryable() {
    let fixture = Fixture::new();
    fixture.sources(false, false).await;
    fixture.submit().await;
    fixture.fact(
        "first",
        1,
        integration::grpc::v0_4::ExecutionPhase::Started,
        4,
    );
    fixture.fact(
        "first",
        2,
        integration::grpc::v0_4::ExecutionPhase::Completed,
        5,
    );
    fixture.tick(5);
    let before = server_checkpoint_json(&fixture.controller.lock().unwrap()).unwrap();
    let sql = rusqlite::Connection::open(fixture.directory.path().join("events.sqlite3")).unwrap();
    sql.execute_batch("CREATE TRIGGER reject_verdict BEFORE INSERT ON controller_checkpoint BEGIN SELECT RAISE(ABORT,'injected verdict checkpoint outage'); END").unwrap();
    let input = fixture.evidence("first", "independent-positive", 1, "satisfied", "final");
    assert_eq!(
        fixture
            .request("/v1/verifier-evidence", &input, false, 6)
            .await
            .0,
        409
    );
    assert_eq!(
        server_checkpoint_json(&fixture.controller.lock().unwrap()).unwrap(),
        before
    );
    sql.execute_batch("DROP TRIGGER reject_verdict").unwrap();
    assert_eq!(
        fixture
            .request("/v1/verifier-evidence", &input, false, 7)
            .await
            .0,
        200
    );
}

/// Silent or expired continuous evidence invokes the existing whole-set stop and retains all ownership.
#[tokio::test]
async fn continuous_expiry_holds_running_execution_and_restore_does_not_renew() {
    let fixture = Fixture::new();
    fixture.sources(true, false).await;
    fixture.submit().await;
    fixture.fact(
        "first",
        1,
        integration::grpc::v0_4::ExecutionPhase::Started,
        4,
    );
    let input = fixture.evidence(
        "first",
        "continuous-observation",
        1,
        "satisfied",
        "provisional",
    );
    assert_eq!(
        fixture
            .request("/v1/verifier-evidence", &input, false, 6)
            .await
            .0,
        200
    );
    let original = fixture
        .controller
        .lock()
        .unwrap()
        .bridge
        .control()
        .semantic_verifier_watches();
    fixture.tick(15);
    assert!(
        fixture
            .controller
            .lock()
            .unwrap()
            .orchestrator
            .execution(&domain::MissionId::new("verified-software").unwrap())
            .unwrap()
            .isolation()
            .is_none()
    );
    fixture.tick(16);
    let controller = fixture.controller.lock().unwrap();
    assert!(
        controller
            .orchestrator
            .execution(&domain::MissionId::new("verified-software").unwrap())
            .unwrap()
            .isolation()
            .is_some()
    );
    assert!(!controller.safety_stops.is_empty());
    assert_eq!(controller.bridge.attempt_history().len(), 1);
    assert!(
        !controller
            .bridge
            .control()
            .allocation_snapshot(TimestampMs::new(16))
            .unwrap()
            .allocations()
            .is_empty()
    );
    let restored =
        control::ControlPlane::restore(controller.bridge.control().checkpoint()).unwrap();
    assert_eq!(restored.semantic_verifier_watches(), original);
    assert_eq!(restored.semantic_verifier_receipts()[0].valid_until_ms, 16);
}

/// Missing or corrupted independently produced bytes cannot establish final affirmative evidence.
#[tokio::test]
async fn required_artifact_is_verified_from_actual_cas_bytes() {
    use sha2::{Digest, Sha256};
    let fixture = Fixture::new();
    fixture.sources(false, true).await;
    fixture.submit().await;
    fixture.fact(
        "first",
        1,
        integration::grpc::v0_4::ExecutionPhase::Started,
        4,
    );
    fixture.fact(
        "first",
        2,
        integration::grpc::v0_4::ExecutionPhase::Completed,
        5,
    );
    fixture.tick(5);
    let mut input = fixture.evidence("first", "artifact-proof", 1, "satisfied", "final");
    assert_eq!(
        fixture
            .request("/v1/verifier-evidence", &input, false, 6)
            .await
            .0,
        409
    );
    let bytes = b"independent software artifact\n";
    let digest = format!("sha256:{:x}", Sha256::digest(bytes));
    input["artifact_digest"] = digest.clone().into();
    input["artifact_size_bytes"] = serde_json::json!(bytes.len());
    assert_eq!(
        fixture
            .request("/v1/verifier-evidence", &input, false, 7)
            .await
            .0,
        409
    );
    let store = fixture
        .controller
        .lock()
        .unwrap()
        .verifier_artifact_store
        .clone()
        .unwrap();
    let mut upload = store.begin_upload("independent-artifact").unwrap();
    upload.write_chunk(bytes).unwrap();
    upload.finalize(&digest, bytes.len() as u64).unwrap();
    assert_eq!(
        fixture
            .request("/v1/verifier-evidence", &input, false, 8)
            .await
            .0,
        200
    );
}

/// A negative semantic verdict preserves successful local completion and reports semantic rejection separately.
#[tokio::test]
async fn negative_verifier_is_not_a_local_execution_failure() {
    let fixture = Fixture::new();
    fixture.sources(false, false).await;
    fixture.submit().await;
    fixture.fact(
        "first",
        1,
        integration::grpc::v0_4::ExecutionPhase::Started,
        4,
    );
    fixture.fact(
        "first",
        2,
        integration::grpc::v0_4::ExecutionPhase::Completed,
        5,
    );
    fixture.tick(5);
    let input = fixture.evidence("first", "negative", 1, "unsatisfied", "final");
    assert_eq!(
        fixture
            .request("/v1/verifier-evidence", &input, false, 6)
            .await
            .0,
        200
    );
    let controller = fixture.controller.lock().unwrap();
    let attempt = controller
        .bridge
        .attempt_history()
        .into_iter()
        .next()
        .unwrap();
    assert_eq!(
        controller.bridge.execution_status(attempt.execution_id()),
        Some(orchestration::RemoteExecutionStatus::Completed)
    );
    let mission = controller
        .orchestrator
        .execution(attempt.command().mission_id())
        .unwrap();
    assert_eq!(
        mission.lifecycle(),
        orchestration::MissionExecutionLifecycle::Failed
    );
    assert_eq!(
        controller
            .bridge
            .control()
            .group(mission.group_id())
            .unwrap()
            .task_execution(attempt.command().task_ref())
            .unwrap()
            .lifecycle(),
        domain::TaskExecutionLifecycle::SatisfactionRejected
    );
}

/// A conflicting report after an intermediate Task is satisfied remains an incident without rewriting the DAG.
#[tokio::test]
async fn late_task_report_is_an_incident_and_does_not_reopen_execution() {
    let fixture = Fixture::new();
    fixture.sources(false, false).await;
    fixture.submit().await;
    fixture.fact(
        "first",
        1,
        integration::grpc::v0_4::ExecutionPhase::Started,
        4,
    );
    fixture.fact(
        "first",
        2,
        integration::grpc::v0_4::ExecutionPhase::Completed,
        5,
    );
    fixture.tick(5);
    let positive = fixture.evidence("first", "positive", 1, "satisfied", "final");
    assert_eq!(
        fixture
            .request("/v1/verifier-evidence", &positive, false, 6)
            .await
            .0,
        200
    );
    let negative = fixture.evidence("first", "late-negative", 2, "unsatisfied", "final");
    let result = fixture
        .request("/v1/verifier-evidence", &negative, false, 7)
        .await;
    assert_eq!(result.0, 200);
    assert_eq!(result.1["receipt"]["incident"], "late_terminal_task_report");
    assert_eq!(result.1["receipt"]["applied"], false);
    assert_eq!(
        fixture
            .controller
            .lock()
            .unwrap()
            .bridge
            .attempt_history()
            .len(),
        2
    );
}

/// Revoking a frozen continuous producer holds existing work and cannot silently select a different producer.
#[tokio::test]
async fn verifier_source_revision_change_stops_its_current_watch() {
    let fixture = Fixture::new();
    fixture.sources(true, false).await;
    fixture.submit().await;
    fixture.fact(
        "first",
        1,
        integration::grpc::v0_4::ExecutionPhase::Started,
        4,
    );
    let mut update = registry(true, false);
    update["command_id"] = "disable-source".into();
    update["expected_revision"] = 1.into();
    update["registry"]["revision"] = 2.into();
    update["registry"]["sources"]["independent-verifier"]["revision"] = 2.into();
    update["registry"]["sources"]["independent-verifier"]["enabled"] = false.into();
    assert_eq!(
        fixture
            .request("/v1/verifier-sources", &update, false, 5)
            .await
            .0,
        403
    );
    assert_eq!(
        fixture
            .request("/v1/verifier-sources", &update, true, 5)
            .await
            .0,
        200
    );
    fixture.tick(6);
    let controller = fixture.controller.lock().unwrap();
    assert!(
        controller
            .orchestrator
            .execution(&domain::MissionId::new("verified-software").unwrap())
            .unwrap()
            .isolation()
            .is_some()
    );
    assert!(
        !controller
            .bridge
            .control()
            .allocation_snapshot(TimestampMs::new(6))
            .unwrap()
            .allocations()
            .is_empty()
    );
    let restored =
        control::ControlPlane::restore(controller.bridge.control().checkpoint()).unwrap();
    assert_eq!(
        restored
            .task_verifier_source(
                &controller.bridge.attempt_history()[0]
                    .command()
                    .task_ref()
                    .clone()
            )
            .unwrap()
            .revision,
        1
    );
}

/// Source administration returns its original receipt on retry, fences command conflicts and restores unchanged.
#[tokio::test]
async fn source_command_receipts_are_durable_and_subject_bound() {
    let fixture = Fixture::new();
    let body = registry(false, false);
    let first = fixture
        .request("/v1/verifier-sources", &body, true, 2)
        .await;
    assert_eq!(first.0, 200);
    assert_eq!(
        fixture
            .request("/v1/verifier-sources", &body, true, 200)
            .await,
        first
    );
    let mut stale_command = body.clone();
    stale_command["command_id"] = "new-command-with-old-revision".into();
    assert_eq!(
        fixture
            .request("/v1/verifier-sources", &stale_command, true, 201)
            .await
            .0,
        409
    );
    let mut conflicting = body.clone();
    conflicting["registry"]["sources"]["independent-verifier"]["max_age_ms"] = 100.into();
    assert_eq!(
        fixture
            .request("/v1/verifier-sources", &conflicting, true, 3)
            .await
            .0,
        409
    );
    let controller = fixture.controller.lock().unwrap();
    let restored =
        control::ControlPlane::restore(controller.bridge.control().checkpoint()).unwrap();
    assert_eq!(
        restored.semantic_verifier_registry(),
        controller.bridge.control().semantic_verifier_registry()
    );
    let receipt: domain::RegistryCommandReceipt =
        serde_json::from_value(first.1["receipt"].clone()).unwrap();
    assert_eq!(receipt.authority_subject, "test:recovery-admin");
    assert_eq!(receipt.accepted_at_ms, 2);
    assert!(
        restored
            .registry_command_receipt(
                &receipt.command_id,
                receipt.kind,
                &receipt.input_digest,
                "another-admin"
            )
            .is_err()
    );
}
