//! Durable revision application unit fixtures with real SQL/files and zero registered Nodes or devices.

mod memory_tests;

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
    writer: Arc<Mutex<()>>,
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
            writer: Arc::new(Mutex::new(())),
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
        self.command_schema(SCHEMA, id, expected, action, principal)
    }

    /// Exercises explicit protocol negotiation using only labelled unit principals.
    fn command_schema(
        &self,
        schema: &str,
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
        let body=serde_json::to_vec(&serde_json::json!({"schema_version":schema,"command_id":id,"expected_revision":expected,"action":action})).unwrap();
        admission::submit(
            &self.controller,
            &self.log,
            self.writer.as_ref(),
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
        self.install_for_trigger(false)
    }

    /// Runs unit timer/fault inputs through their real SQL batch and checkpoint boundary.
    fn transition<R>(&self, action: impl FnOnce(&mut ControllerState) -> R) -> R {
        let _writer = self.writer.lock().unwrap();
        self.log.begin_batch().unwrap();
        let mut controller = self.controller.lock().unwrap();
        let result = action(&mut controller);
        self.log
            .save_checkpoint(
                SERVER_CHECKPOINT_SCHEMA,
                &server_checkpoint_json(&controller).unwrap(),
            )
            .unwrap();
        self.log.commit_batch().unwrap();
        result
    }

    /// Keeps optional typed triggers explicit without fabricating any Runtime failure observation.
    fn install_for_trigger(&self, automatic: bool) -> serde_json::Value {
        self.command("install-original",0,serde_json::json!({"kind":"install-policy","policy":{
            "goal_verifier":{"verifier":{"namespace":"observation","name":"goal","version":"v1"},"predicate":"original goal independently measured","max_evidence_age_ms":1000},
            "goal_context":{"measurement":"original frozen objective"},"policy_id":"unit-revision-policy","proposer":"proposer","lifetime_ms":60000,
            "candidate_limit":4,"candidate_timeout_ms":10000,"candidate_bytes":65536,"switch_limit":2,"switch_timeout_ms":10000,"automatic_triggers":if automatic {vec!["local-execution-failed"]}else{Vec::<&str>::new()}
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

/// Discovery is current evidence filtered by actual subject/tenant and cannot renew original authority.
#[test]
fn generation_work_discovery_is_readonly_current_and_tenant_principal_scoped() {
    let fixture = Fixture::new();
    fixture.install_for_trigger(true);
    fixture
        .command_schema(
            GENERATION_SCHEMA,
            "authorize-discovery",
            1,
            serde_json::json!({"kind":"authorize-generation"}),
            "administrator",
        )
        .unwrap();
    let mission = fixture.plan.goal().mission_id();
    fixture.transition(|controller| {
        switching::observe_trigger(
            controller,
            mission,
            Trigger::LocalExecutionFailed,
            fixture.clock.now(),
        )
        .unwrap();
        switching::tick(controller, &fixture.clock, None, None).unwrap();
    });
    let mut controller = fixture.controller.lock().unwrap();
    let original = serde_json::to_value(&controller.runtime_revisions).unwrap();
    let page = work_page(
        &controller,
        "proposer",
        "unit-tenant",
        None,
        1,
        fixture.clock.now(),
    )
    .unwrap();
    assert_eq!(page["items"].as_array().unwrap().len(), 1);
    assert_eq!(page["items"][0]["mission_id"], mission.as_str());
    assert!(page["items"][0].get("plan").is_none());
    assert!(page["items"][0].get("nodes").is_none());
    assert_eq!(
        serde_json::to_value(&controller.runtime_revisions).unwrap(),
        original
    );
    for (principal, tenant, after) in [
        ("administrator", "unit-tenant", None),
        ("proposer", "other-tenant", None),
        ("proposer", "unit-tenant", Some(mission.as_str())),
    ] {
        assert!(
            work_page(
                &controller,
                principal,
                tenant,
                after,
                32,
                fixture.clock.now()
            )
            .unwrap()["items"]
                .as_array()
                .unwrap()
                .is_empty()
        );
    }
    assert!(work_page(&controller, "proposer", "*", None, 32, fixture.clock.now()).is_err());
    let state = controller
        .runtime_revisions
        .get_mut(mission.as_str())
        .unwrap();
    state.candidates.values_mut().next().unwrap().base_revision += 1;
    assert!(
        work_page(
            &controller,
            "proposer",
            "unit-tenant",
            None,
            32,
            fixture.clock.now()
        )
        .unwrap()["items"]
            .as_array()
            .unwrap()
            .is_empty()
    );
    let state = controller
        .runtime_revisions
        .get_mut(mission.as_str())
        .unwrap();
    state.candidates.values_mut().next().unwrap().base_revision -= 1;
    state.revoked = true;
    assert!(
        work_page(
            &controller,
            "proposer",
            "unit-tenant",
            None,
            32,
            fixture.clock.now()
        )
        .unwrap()["items"]
            .as_array()
            .unwrap()
            .is_empty()
    );
    assert_eq!(
        work_query("after=Mission%2B%E4%B8%80&limit=1").unwrap(),
        (Some("Mission+一".into()), 1)
    );
    for query in [
        "limit=0",
        "limit=33",
        "limit=1&limit=2",
        "principal=proposer",
        "after=a&after=b",
        "after=%ff",
        "after=%0a",
        "after=%",
    ] {
        assert!(work_query(query).is_err(), "{query}");
    }
}

/// Pagination covers multiple actual Orchestration admissions without inventing any Node or attempt.
#[test]
fn generation_discovery_pages_do_not_drop_or_repeat_active_missions() {
    let mut fixture = Fixture::new();
    for index in 0..3 {
        if index > 0 {
            let mut document = orchestration::mission_plan_document(&fixture.plan);
            document["mission"]["id"] = serde_json::json!(format!("paged-mission-{index}"));
            let plan = orchestration::decode_mission_plan(&document.to_string()).unwrap();
            {
                let mut controller = fixture.controller.lock().unwrap();
                controller
                    .bridge
                    .control_mut()
                    .register_mission_tenant(plan.goal().mission_id().clone(), "unit-tenant")
                    .unwrap();
                let ControllerState {
                    orchestrator,
                    bridge,
                    ..
                } = &mut *controller;
                orchestrator
                    .submit(
                        plan.clone(),
                        domain::ExecutionGroupId::new(format!("paged-group-{index}")).unwrap(),
                        bridge.control_mut(),
                        fixture.clock.now(),
                        &domain::CorrelationId::new("page-admission").unwrap(),
                        &mut fixture.log.clone(),
                    )
                    .unwrap();
            }
            fixture.plan = plan;
        }
        fixture.install_for_trigger(true);
        fixture
            .command_schema(
                GENERATION_SCHEMA,
                "authorize-paged-work",
                1,
                serde_json::json!({"kind":"authorize-generation"}),
                "administrator",
            )
            .unwrap();
        fixture.transition(|controller| {
            switching::observe_trigger(
                controller,
                fixture.plan.goal().mission_id(),
                Trigger::LocalExecutionFailed,
                fixture.clock.now(),
            )
            .unwrap();
            switching::tick(controller, &fixture.clock, None, None).unwrap();
        });
    }
    let controller = fixture.controller.lock().unwrap();
    let expected = controller
        .runtime_revisions
        .keys()
        .cloned()
        .collect::<Vec<_>>();
    let original = serde_json::to_value(&controller.runtime_revisions).unwrap();
    let mut cursor = None;
    let mut actual = Vec::new();
    for _ in 0..3 {
        let page = work_page(
            &controller,
            "proposer",
            "unit-tenant",
            cursor.as_deref(),
            1,
            fixture.clock.now(),
        )
        .unwrap();
        assert_eq!(page["items"].as_array().unwrap().len(), 1);
        actual.push(page["items"][0]["mission_id"].as_str().unwrap().to_string());
        cursor = page["next_after"].as_str().map(str::to_string);
    }
    assert!(cursor.is_none());
    assert_eq!(actual, expected);
    assert_eq!(
        serde_json::to_value(&controller.runtime_revisions).unwrap(),
        original
    );
}

/// Explicit generation freezes a zero-attempt unit source and never bypasses exact Review or old-writer fences.
#[test]
fn generation_waits_for_original_candidate_and_review_without_renewing_ownership() {
    let fixture = Fixture::new();
    fixture.install_for_trigger(true);
    let action = serde_json::json!({"kind":"authorize-generation"});
    assert!(
        fixture
            .command("old-generation", 1, action.clone(), "administrator")
            .is_err()
    );
    assert!(
        fixture
            .command_schema(
                GENERATION_SCHEMA,
                "proposer-generation",
                1,
                action.clone(),
                "proposer"
            )
            .is_err()
    );
    let authorized = fixture
        .command_schema(
            GENERATION_SCHEMA,
            "authorize-generation",
            1,
            action,
            "administrator",
        )
        .unwrap();
    assert_eq!(authorized["review_authorized"], false);
    let mission = fixture.plan.goal().mission_id();
    let (ticket, budget, policy_digest) = fixture.transition(|controller| {
        assert!(
            switching::observe_trigger(
                controller,
                mission,
                Trigger::LocalExecutionFailed,
                fixture.clock.now()
            )
            .unwrap()
        );
        switching::tick(controller, &fixture.clock, None, None).unwrap();
        validate_restored(controller).unwrap();
        let state = &controller.runtime_revisions[mission.as_str()];
        assert_eq!(state.switches.len(), 1);
        assert_eq!(state.switches[0].phase, Phase::Generating);
        assert!(state.switches[0].generation);
        let ticket = state.switches[0].ticket_id.clone();
        assert!(state.candidates[&ticket].plan.is_none());
        let mut saved: ServerCheckpoint =
            serde_json::from_str(&server_checkpoint_json(controller).unwrap()).unwrap();
        saved.schema = PRE_GENERATION_SERVER_CHECKPOINT_SCHEMA.into();
        assert!(super::super::isolation::validate_isolation_checkpoint_schema(&saved).is_err());
        (
            ticket,
            state.switches[0].budget.clone(),
            state.policy_digest.clone(),
        )
    });
    let revision = |fixture: &Fixture| {
        fixture.controller.lock().unwrap().runtime_revisions[mission.as_str()].revision
    };
    let submitted=fixture.command("generated-output",revision(&fixture),serde_json::json!({"kind":"submit-candidate","ticket_id":ticket,"plan":fixture.alternative(),"provenance":{"kind":"operator","principal":"proposer"}}),"proposer").unwrap();
    assert_eq!(submitted["status"], "awaiting_review");
    fixture.transition(|controller| {
        switching::tick(controller, &fixture.clock, None, None).unwrap();
        let state = &controller.runtime_revisions[mission.as_str()];
        assert_eq!(state.switches[0].phase, Phase::Generating);
        assert_eq!(state.switches[0].budget, budget);
        assert!(
            controller
                .orchestrator
                .execution(mission)
                .unwrap()
                .plan_revisions()
                .is_empty()
        );
    });
    fixture.command("review-generated",revision(&fixture),serde_json::json!({"kind":"review-candidate","ticket_id":ticket,"plan_digest":submitted["plan_digest"],"decision":"approve","reason":"explicit unit administrative review"}),"administrator").unwrap();
    fixture.transition(|controller| {
        switching::tick(controller, &fixture.clock, None, None).unwrap();
        let state = &controller.runtime_revisions[mission.as_str()];
        assert_eq!(state.switches[0].phase, Phase::Applied);
        assert_eq!(state.switches[0].budget, budget);
        assert_eq!(state.policy_digest, policy_digest);
        assert_eq!(
            controller
                .orchestrator
                .execution(mission)
                .unwrap()
                .plan_revision(),
            1
        );
        assert!(controller.bridge.attempt_history().is_empty());
        validate_restored(controller).unwrap();
    });
}

/// Original idempotency, stopped zero-attempt switching, version history and old-writer fences survive SQL.
#[test]
fn generation_claim_and_provider_nonce_fence_replaced_or_regressed_sql_workers() {
    let fixture = Fixture::new();
    fixture.install_for_trigger(true);
    fixture
        .command_schema(
            GENERATION_SCHEMA,
            "authorize-generation",
            1,
            serde_json::json!({"kind":"authorize-generation"}),
            "administrator",
        )
        .unwrap();
    let mission = fixture.plan.goal().mission_id();
    let ticket = fixture.transition(|controller| {
        switching::observe_trigger(
            controller,
            mission,
            Trigger::LocalExecutionFailed,
            fixture.clock.now(),
        )
        .unwrap();
        switching::tick(controller, &fixture.clock, None, None).unwrap();
        controller.runtime_revisions[mission.as_str()].switches[0]
            .ticket_id
            .clone()
    });
    let revision =
        || fixture.controller.lock().unwrap().runtime_revisions[mission.as_str()].revision;
    let hash = format!("sha256:{}", "a".repeat(64));
    let claim = serde_json::json!({"store_instance_id":"original-unit-sql","work_id":hash,
                               "identity_digest":hash,"request_id":"original-unit-request"});
    fixture
        .command_schema(
            GENERATION_SCHEMA,
            "claim",
            revision(),
            serde_json::json!({"kind":"claim-runtime-work","ticket_id":ticket,"claim":claim}),
            "proposer",
        )
        .unwrap();
    let mut other = claim.clone();
    other["store_instance_id"] = "replacement-unit-sql".into();
    assert!(
        fixture
            .command_schema(
                GENERATION_SCHEMA,
                "other-store",
                revision(),
                serde_json::json!({"kind":"claim-runtime-work","ticket_id":ticket,"claim":other}),
                "proposer"
            )
            .is_err()
    );
    let first = fixture
        .command_schema(
            GENERATION_SCHEMA,
            "first-call",
            revision(),
            serde_json::json!({"kind":"admit-provider-call","ticket_id":ticket,
            "phase":"provider-generate","nonce":"original-unit-nonce","request_digest":hash}),
            "proposer",
        )
        .unwrap();
    assert!(
        fixture
            .command_schema(
                GENERATION_SCHEMA,
                "regressed-copy",
                revision(),
                serde_json::json!({"kind":"admit-provider-call","ticket_id":ticket,
            "phase":"provider-generate","nonce":"new-nonce-from-old-copy","request_digest":hash}),
                "proposer"
            )
            .is_err()
    );
    let replay = fixture
        .command_schema(
            GENERATION_SCHEMA,
            "same-original-call",
            revision(),
            serde_json::json!({"kind":"admit-provider-call","ticket_id":ticket,
            "phase":"provider-generate","nonce":"original-unit-nonce","request_digest":hash}),
            "proposer",
        )
        .unwrap();
    assert_eq!(replay["receipt_digest"], first["receipt_digest"]);
    let provenance = serde_json::json!({"kind":"mission-intelligence","principal":"proposer",
        "request_id":"original-unit-request","provider_call_id":format!("{hash}:provider-generate"),
        "code_digest":hash,"config_digest":hash,"catalog_digest":hash,"prompt_digest":hash,
        "dependency_digest":hash,"original_output_digest":hash});
    fixture
        .command(
            "actual-unit-output",
            revision(),
            serde_json::json!({"kind":"submit-candidate",
        "ticket_id":ticket,"plan":fixture.alternative(),"provenance":provenance}),
            "proposer",
        )
        .unwrap();
    let controller = fixture.controller.lock().unwrap();
    validate_restored(&controller).unwrap();
    let mut forged = controller.clone();
    forged
        .runtime_revisions
        .get_mut(mission.as_str())
        .unwrap()
        .candidates
        .get_mut(&ticket)
        .unwrap()
        .provider_calls
        .get_mut("provider-generate")
        .unwrap()
        .nonce = "changed".into();
    assert!(validate_restored(&forged).is_err());
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
