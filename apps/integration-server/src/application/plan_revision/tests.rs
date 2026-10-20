//! Durable revision application unit fixtures with real SQL/files and zero registered Nodes or devices.

use super::*;

/// Owns original file authorization, SQLite and a zero-attempt Controller without fabricating transport identity.
struct Fixture {
    /// Retains actual stores for this unit fixture's lifetime.
    root: tempfile::TempDir,
    /// Original authority transaction store.
    log: state::SqliteEventLog,
    /// Actual composition under test, containing zero enrolled Nodes.
    controller: Arc<Mutex<ControllerState>>,
    /// Original application writer mutex; transport authorization is covered separately by process checks.
    writer: Mutex<()>,
    /// Explicit local unit clock; no distributed or device time assertion is made.
    clock: runtime::FixedClock,
    /// Original accepted semantic plan.
    plan: domain::MissionPlan,
}

impl Fixture {
    /// Accepts a published unit plan and actual file-backed principal/source grants without selecting an executor.
    fn new() -> Self {
        let root = tempfile::tempdir().unwrap();
        let log = state::SqliteEventLog::open(root.path().join("events.sqlite3")).unwrap();
        let plan = orchestration::decode_mission_plan(include_str!(
            "../../../../../scenarios/phase1-mission-v0.2/mission-plan.json"
        ))
        .unwrap();
        let mut control = control::ControlPlane::new();
        let grants: BTreeMap<_, _> = [
            ("administrator", "recovery-admin"),
            ("proposer", "operator"),
            ("goal-producer", "operator"),
        ]
        .into_iter()
        .enumerate()
        .map(|(index, (principal, role))| {
            (
                format!("sha256:{:064x}", index + 1),
                domain::OperatorCertificateGrant {
                    principal_id: principal.into(),
                    role: role.into(),
                    tenant_ids: BTreeSet::from(["unit-tenant".into()]),
                    not_before_ms: 0,
                    expires_at_ms: 0,
                    revoked: false,
                },
            )
        })
        .collect();
        let authorization = domain::OperatorAuthorization {
            schema_version: "roboguide.operator-authorization/v0.2".into(),
            registry_id: "unit-authority".into(),
            revision: 1,
            grants,
        };
        std::fs::write(
            root.path().join("operator.json"),
            serde_json::to_vec(&authorization).unwrap(),
        )
        .unwrap();
        control
            .accept_operator_authorization(Some(authorization))
            .unwrap();
        control
            .register_mission_tenant(plan.goal().mission_id().clone(), "unit-tenant")
            .unwrap();
        let source:domain::SemanticVerifierRegistry=serde_json::from_value(serde_json::json!({
            "schema_version":"roboguide.semantic-verifier-registry/v0.1","registry_id":"unit-goals","revision":1,
            "sources":{"original-goal":{"source_id":"original-goal","revision":1,"tenant_id":"unit-tenant",
                "producer_principals":["goal-producer"],"verifier":{"namespace":"observation","name":"goal","version":"v1"},
                "predicates":["original goal independently measured"],"max_age_ms":1000,"enabled":true,"continuous":false,
                "stop_timeout_ms":1000,"require_artifact":false}}
        })).unwrap();
        control
            .accept_semantic_verifier_registry(source, 0)
            .unwrap();
        let mut orchestrator = MissionOrchestrator::new();
        orchestrator
            .submit(
                plan.clone(),
                domain::ExecutionGroupId::new("original-unit-group").unwrap(),
                &mut control,
                domain::TimestampMs::new(1),
                &domain::CorrelationId::new("unit-admission").unwrap(),
                &mut log.clone(),
            )
            .unwrap();
        let controller = Arc::new(Mutex::new(ControllerState {
            bridge: IntegrationRuntimeBridge::new(
                control,
                state::InMemorySharedNodeState::new(),
                log.clone(),
                integration::GrpcNodeRouter::default(),
            ),
            orchestrator,
            mission_admissions: BTreeMap::new(),
            memory_imports: BTreeMap::new(),
            memory_preparation: Default::default(),
            memory_use: Default::default(),
            memory_handoffs: BTreeMap::new(),
            runtime_revisions: BTreeMap::new(),
            safety_stops: BTreeMap::new(),
            verifier_seen: BTreeSet::new(),
            verifier_source_digest: None,
            verifier_artifact_store: None,
        }));
        Self {
            root,
            log,
            controller,
            writer: Mutex::new(()),
            clock: runtime::FixedClock::new(domain::TimestampMs::new(10)),
            plan,
        }
    }

    /// Sends a closed application action using explicit unit principals; no HTTP/TLS claim is made.
    fn command(
        &self,
        id: &str,
        expected: u64,
        action: serde_json::Value,
        principal: &str,
    ) -> Result<serde_json::Value, String> {
        let path = self.root.path().join("operator.json");
        let caller = Caller {
            certificate_digest: &format!(
                "sha256:{:064x}",
                if principal == "administrator" {
                    1
                } else if principal == "proposer" {
                    2
                } else {
                    3
                }
            ),
            authorization_path: Some(&path),
            principal,
            role: if principal == "administrator" {
                "recovery-admin"
            } else {
                "operator"
            },
            certificate_expires_at_ms: 100_000,
            tenant: "unit-tenant",
        };
        let body=serde_json::to_vec(&serde_json::json!({"schema_version":SCHEMA,"command_id":id,"expected_revision":expected,"action":action})).unwrap();
        admission::submit(
            &self.controller,
            &self.log,
            &self.writer,
            &self.clock,
            self.plan.goal().mission_id(),
            &body,
            &caller,
            None,
            None,
        )
    }

    /// Installs one original independently measured goal and finite candidate/switch budgets.
    fn install(&self) -> serde_json::Value {
        self.command("install-original",0,serde_json::json!({"kind":"install-policy","policy":{
            "goal_verifier":{"verifier":{"namespace":"observation","name":"goal","version":"v1"},"predicate":"original goal independently measured","max_evidence_age_ms":1000},
            "goal_context":{"measurement":"original frozen objective"},"policy_id":"unit-revision-policy","proposer":"proposer","lifetime_ms":60000,
            "candidate_limit":4,"candidate_timeout_ms":10000,"candidate_bytes":65536,"switch_limit":2,"switch_timeout_ms":10000,"automatic_triggers":[]
        }}),"administrator").unwrap()
    }

    /// Returns a fresh semantic decomposition without changing any original goal/Actor/Context contract.
    fn alternative(&self) -> serde_json::Value {
        let mut plan = orchestration::mission_plan_document(&self.plan);
        for task in plan["tasks"].as_array_mut().unwrap() {
            task["id"] = format!("{}-new", task["id"].as_str().unwrap()).into();
            for dependency in task["depends_on"].as_array_mut().unwrap() {
                *dependency = format!("{}-new", dependency.as_str().unwrap()).into();
            }
        }
        plan
    }
}

/// Original idempotency, stopped zero-attempt switching, version history and old-writer fences survive SQL.
#[test]
fn revision_sql_preserves_original_permissions_and_source_history() {
    let fixture = Fixture::new();
    let installed = fixture.install();
    assert_eq!(fixture.install(), installed);
    assert!(
        fixture
            .command(
                "replace-policy",
                1,
                serde_json::json!({"kind":"install-policy","policy":{}}),
                "administrator"
            )
            .is_err()
    );
    let alternative = fixture.alternative();
    let approved=fixture.command("preapprove",1,serde_json::json!({"kind":"approve-alternative","ticket_id":"alternative","plan":alternative,"completion_precondition":[]}),"administrator").unwrap();
    assert_eq!(approved["status"], "alternative_reviewed");
    assert!(fixture.command("wrong-owner",2,serde_json::json!({"kind":"request-switch","ticket_id":"alternative","target_group_id":"unit-new-group"}),"goal-producer").is_err());
    let requested=fixture.command("switch-original",2,serde_json::json!({"kind":"request-switch","ticket_id":"alternative","target_group_id":"unit-new-group"}),"proposer").unwrap();
    assert_eq!(requested["phase"], "stopping");
    let committed = fixture
        .command(
            "commit-original",
            3,
            serde_json::json!({"kind":"commit-switch","switch_command_id":"switch-original"}),
            "proposer",
        )
        .unwrap();
    assert_eq!(committed["plan_revision"], 1);
    assert_eq!(
        fixture
            .controller
            .lock()
            .unwrap()
            .bridge
            .attempt_history()
            .len(),
        0
    );
    let live = fixture.controller.lock().unwrap();
    validate_restored(&live).unwrap();
    live.orchestrator
        .validate_control_authority(live.bridge.control())
        .unwrap();
    assert_eq!(
        live.orchestrator
            .execution(fixture.plan.goal().mission_id())
            .unwrap()
            .accepted_at(),
        domain::TimestampMs::new(1)
    );
    let bytes = server_checkpoint_json(&live).unwrap();
    let mut legacy_inner: serde_json::Value =
        serde_json::from_str(&live.bridge.checkpoint_json().unwrap()).unwrap();
    legacy_inner["schema"] = "roboguide.controller-checkpoint/v22".into();
    assert!(
        IntegrationRuntimeBridge::restore_from_checkpoint(
            &legacy_inner.to_string(),
            fixture.log.clone(),
            integration::GrpcNodeRouter::default(),
            domain::TimestampMs::new(20)
        )
        .is_err()
    );
    let mut saved: ServerCheckpoint = serde_json::from_str(&bytes).unwrap();
    super::super::isolation::validate_isolation_checkpoint_schema(&saved).unwrap();
    saved.schema = PRE_RUNTIME_REVISION_SERVER_CHECKPOINT_SCHEMA.into();
    assert!(super::super::isolation::validate_isolation_checkpoint_schema(&saved).is_err());
    let mut forged = live.clone();
    forged
        .runtime_revisions
        .values_mut()
        .next()
        .unwrap()
        .goal_contract_digest = "sha256:changed".into();
    assert!(validate_restored(&forged).is_err());
    drop(live);
    assert_eq!(
        fixture
            .command(
                "commit-original",
                3,
                serde_json::json!({"kind":"commit-switch","switch_command_id":"switch-original"}),
                "proposer"
            )
            .unwrap(),
        committed
    );
}

/// An actual original role-file revocation wins before queued candidate work, without another API refresh.
#[test]
fn revision_work_rechecks_original_file_and_does_not_renew_revoked_policy() {
    let fixture = Fixture::new();
    fixture.install();
    let file = fixture.root.path().join("operator.json");
    let mut registry: domain::OperatorAuthorization =
        serde_json::from_slice(&std::fs::read(&file).unwrap()).unwrap();
    registry.revision = 2;
    registry
        .grants
        .values_mut()
        .find(|grant| grant.principal_id == "proposer")
        .unwrap()
        .revoked = true;
    let mut replacement = registry
        .grants
        .values()
        .find(|grant| grant.principal_id == "proposer")
        .unwrap()
        .clone();
    replacement.revoked = false;
    registry
        .grants
        .insert(format!("sha256:{:064x}", 4), replacement);
    std::fs::write(&file, serde_json::to_vec(&registry).unwrap()).unwrap();
    assert!(
        fixture
            .command(
                "candidate-after-revoke",
                1,
                serde_json::json!({"kind":"request-candidate","ticket_id":"work"}),
                "proposer"
            )
            .is_err()
    );
    let live = fixture.controller.lock().unwrap();
    let state = live.runtime_revisions.values().next().unwrap();
    assert_eq!(state.revision, 1);
    assert!(state.candidates.is_empty());
    validate_restored(&live).unwrap();
}
