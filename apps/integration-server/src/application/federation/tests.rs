//! Participant policy and receipt invariants independent of any device or remote model.

use super::*;

/// Builds explicitly declared software-only policy with two different administrative principals.
fn policy() -> Policy {
    serde_json::from_value(serde_json::json!({
        "schema_version":"roboguide.federation-authorization/v0.1", "registry_id":"test-policy",
        "revision":1,"local_domain":"a","domain_epochs":{"a":7,"b":8},"grants":{
            format!("sha256:{}","a".repeat(64)):{"principal_id":"admin", "tenant_ids":["tenant"],
                "domain_ids":["a","b"],"revoked":false}
        }
    }))
    .unwrap()
}

/// Complete protocol input is a fixture, not a registered Node or resource claim.
fn session() -> FederationSession {
    FederationSession {
        transaction_id: "test-transaction".into(),
        source_domain: "a".into(),
        target_domain: "b".into(),
        tenant_id: "tenant".into(),
        source_epoch: 7,
        plan_digest: format!("sha256:{}", "a".repeat(64)),
        ownership_digest: format!("sha256:{}", "b".repeat(64)),
        deadline: domain::TimestampMs::new(10_000),
        state: FederationSessionState::Prepared,
    }
}

/// Actual leaf and stable principal, exact tenant, pair and expected epoch are all required.
#[test]
fn policy_never_borrows_another_principal_tenant_or_domain_epoch() {
    let policy = policy();
    let session = session();
    let caller = Caller {
        leaf: format!("sha256:{}", "a".repeat(64)),
        principal: "admin".into(),
        tenant: "tenant".into(),
    };
    assert!(policy.authorize(&caller, &session).is_ok());
    for different in [
        Caller {
            principal: "other".into(),
            ..caller.clone()
        },
        Caller {
            tenant: "other".into(),
            ..caller.clone()
        },
    ] {
        assert!(policy.authorize(&different, &session).is_err());
    }
    let mut changed = session.clone();
    changed.source_epoch += 1;
    assert!(policy.authorize(&caller, &changed).is_err());
    changed = session;
    changed.target_domain = "c".into();
    assert!(policy.authorize(&caller, &changed).is_err());
}

/// Original domain/registry identity and retired actual leaves cannot be rewritten by later files.
#[test]
fn policy_history_fences_rollback_revival_and_local_domain_replacement() {
    let original = policy();
    let mut state = FederationState::default();
    assert!(state.accept_policy(Some(original.clone())).unwrap());
    assert!(!state.accept_policy(Some(original.clone())).unwrap());
    assert!(state.accept_policy(None).is_err());
    let mut next = original.clone();
    next.revision = 2;
    next.grants.values_mut().next().unwrap().revoked = true;
    state.accept_policy(Some(next.clone())).unwrap();
    assert!(state.accept_policy(Some(original.clone())).is_err());
    let mut revived = original.clone();
    revived.revision = 3;
    assert!(state.accept_policy(Some(revived)).is_err());
    let mut replaced = next;
    replaced.revision = 3;
    replaced.local_domain = "b".into();
    assert!(state.accept_policy(Some(replaced)).is_err());
    let restored: FederationState =
        serde_json::from_str(&serde_json::to_string(&state).unwrap()).unwrap();
    assert_eq!(restored.policy(), state.policy());
}

/// The complete original input and budget remain separate from later terminal receipts.
#[test]
fn original_receipt_does_not_follow_current_phase_or_renew_time() {
    let input = PrepareRequest {
        schema_version: SCHEMA_VERSION.into(),
        session: session(),
    };
    let time = KernelTime::observe().unwrap();
    let mut record = Record {
        request: input,
        principal: "admin".into(),
        leaf: format!("sha256:{}", "a".repeat(64)),
        policy_digest: digest(&policy()).unwrap(),
        accepted_at_ms: 100,
        budget: OriginalBudget::new(&time, 9900).unwrap(),
        committed_at_ms: None,
        aborted_at_ms: None,
    };
    let prepared = receipt(&record, Operation::Prepare);
    let original_budget = serde_json::to_string(&record.budget).unwrap();
    record.committed_at_ms = Some(200);
    assert_eq!(receipt(&record, Operation::Prepare), prepared);
    assert_eq!(
        receipt(&record, Operation::Commit)["global_dispatch_ready"],
        false
    );
    assert_eq!(
        serde_json::to_string(&record.budget).unwrap(),
        original_budget
    );
    assert!(validate_transaction_id("a/b").is_err());
    assert!(validate_transaction_id("a?b").is_err());
    assert!(validate_transaction_id("a\nb").is_err());
}

/// Builds the actual standalone Controller projection, without registering any Node or capability.
fn controller(log: &state::SqliteEventLog) -> ControllerState {
    ControllerState {
        federation: Default::default(),
        runtime_revisions: Default::default(),
        mission_admissions: Default::default(),
        memory_preparation: Default::default(),
        memory_use: Default::default(),
        memory_handoffs: Default::default(),
        memory_imports: Default::default(),
        safety_stops: Default::default(),
        bridge: IntegrationRuntimeBridge::new(
            control::ControlPlane::new(),
            state::InMemorySharedNodeState::new(),
            log.clone(),
            integration::GrpcNodeRouter::default(),
        ),
        orchestrator: MissionOrchestrator::new(),
        verifier_seen: Default::default(),
        verifier_artifact_store: None,
        verifier_source_digest: None,
    }
}

/// A real retained writer whose final transport witness becomes unavailable after SQL writes.
struct RejectedFinalWitness(Mutex<()>);

impl ApplicationWriteGate for RejectedFinalWitness {
    /// Holds the original mutex through the caller's candidate and SQL transaction.
    fn lock(&self) -> Result<std::sync::MutexGuard<'_, ()>, String> {
        self.0.lock().map_err(|_| "writer poisoned".into())
    }

    /// Explicit contract fixture rejects only at the final effect boundary.
    fn validate_locked(&self) -> Result<(), String> {
        Err("original transport witness revoked before Commit".into())
    }
}

/// SQL/checkpoint/journal candidates must roll back when the actual final witness refuses publication.
#[test]
fn final_transport_refusal_preserves_original_checkpoint_and_command_journal() {
    let directory = tempfile::tempdir().unwrap();
    let log = state::SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
    let original = controller(&log);
    let checkpoint = server_checkpoint_json(&original).unwrap();
    log.begin_batch().unwrap();
    save_controller_checkpoint(&log, &checkpoint).unwrap();
    log.commit_batch().unwrap();
    let before = log.load_checkpoint().unwrap();
    let commands = log.control_command_count().unwrap();
    let live = Arc::new(Mutex::new(original));
    let mut candidate = live.lock().unwrap().clone();
    candidate.federation.accept_policy(Some(policy())).unwrap();
    let gate = RejectedFinalWitness(Mutex::new(()));
    let _guard = gate.lock().unwrap();
    let error = persist(
        &live,
        &log,
        candidate,
        "federation:final-witness-test".into(),
        "test:actual-principal",
        b"{}",
        &gate,
        None,
    )
    .unwrap_err();
    assert!(error.contains("transport witness revoked"));
    assert_eq!(log.load_checkpoint().unwrap(), before);
    assert_eq!(log.control_command_count().unwrap(), commands);
    assert_eq!(
        server_checkpoint_json(&live.lock().unwrap()).unwrap(),
        checkpoint
    );
}
