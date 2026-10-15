//! Exact-binding import admission and durable receipt recovery through production authorities.

use super::*;
use application::memory_import::{self, ImportPhase, MemoryImportRequest};
use domain::*;
use integration::grpc::v0_4::{MemoryImportReceipt, MemoryImportReceiptStatus};
use ports::SharedNodeStateWriter;

/// Adds one declared Memory consumer to an already scheduled real Control/Runtime fixture.
fn fixture() -> (stop_recovery::Fixture, MemoryImportRequest) {
    let mut fixture = stop_recovery::Fixture::new();
    let command = fixture.attempt.command();
    let node_id = command.node_id().clone();
    let provider = MemoryProviderDescriptor::new(
        "consumer",
        LocalSystemId::new("fixture-runtime").unwrap(),
        MemoryKind::Semantic,
        MemoryScopeLimit::Global,
        MemoryVisibility::Exchangeable,
        "example.semantic/v1",
        "application/json",
    )
    .unwrap();
    let snapshot = fixture.controller.bridge.state().node(&node_id).unwrap();
    let registration = snapshot
        .registration()
        .clone()
        .with_state_memory_exports(vec![], vec![provider])
        .unwrap();
    let snapshot = NodeStateSnapshot::new(
        registration,
        snapshot.reported_status(),
        snapshot.reported_status_received_at(),
        snapshot.liveness(),
    );
    fixture
        .controller
        .bridge
        .state_mut()
        .record_node(snapshot)
        .unwrap();
    let request = MemoryImportRequest {
        import_id: "import-a".into(),
        group_id: command.group_id().clone(),
        task_ref: command.task_ref().clone(),
        role_id: command.role_id().clone(),
        execution_id: fixture.attempt.execution_id().into(),
        node_id,
        consumer_provider_id: "consumer".into(),
        deadline_ms: 1000,
        evidence_gate: None,
        manifest: MemoryArtifactManifest::new(
            MemorySelector::new(
                MemoryId::new("shared").unwrap(),
                MemoryRevisionId::new("r1").unwrap(),
            ),
            MemoryKind::Semantic,
            "producer",
            MemoryOwner::RoboGuide {
                component: "fixture-source".into(),
            },
            MemoryScope::ExecutionGroup(command.group_id().clone()),
            MemoryVisibility::Exchangeable,
            "example.semantic/v1",
            "application/json",
            Some(MemoryArtifactRef::new(
                ContentDigest::new(format!("sha256:{}", "a".repeat(64))).unwrap(),
                2,
            )),
            None,
            None,
            None,
            TimestampMs::new(1),
        )
        .unwrap(),
    };
    fixture
        .controller
        .bridge
        .control_mut()
        .register_node_session(&request.node_id, "fixture-admitted-session", 5_000)
        .unwrap();
    let policy = MemoryAccessPolicy {
        policy_id: "fixture-content-policy".into(),
        selector: request.manifest.selector().clone(),
        manifest_digest: memory_manifest_digest(&request.manifest).unwrap(),
        tenant_id: "local".into(),
        principals: BTreeMap::new(),
        groups: vec![MemoryGroupPolicy {
            group_id: request.group_id.clone(),
            operations: BTreeSet::from([
                MemoryAccessOperation::Discover,
                MemoryAccessOperation::Read,
                MemoryAccessOperation::Import,
            ]),
            providers: vec![MemoryConsumerPolicy {
                provider_id: Some("consumer".into()),
                local_system_id: Some(LocalSystemId::new("fixture-runtime").unwrap()),
                kind: MemoryKind::Semantic,
                payload_schema: "example.semantic/v1".into(),
                media_type: "application/json".into(),
            }],
        }],
        revoked: false,
    };
    fixture
        .controller
        .bridge
        .control_mut()
        .accept_memory_authorization_registry(
            MemoryAuthorizationRegistry {
                schema_version: MEMORY_AUTHORIZATION_SCHEMA.into(),
                registry_id: "fixture-memory-authority".into(),
                revision: 1,
                policies: BTreeMap::from([(policy.key(), policy)]),
            },
            0,
        )
        .unwrap();
    (fixture, request)
}

/// Upgrades only fixture-declared software registration to the explicit query contract.
fn continuity_fixture() -> (stop_recovery::Fixture, MemoryImportRequest) {
    let (mut fixture, request) = fixture();
    let node = fixture
        .controller
        .bridge
        .state()
        .node(&request.node_id)
        .unwrap()
        .clone();
    let old = node.registration();
    let registration = NodeRegistration::new_with_local_systems(
        old.node_id().clone(),
        old.local_systems().to_vec(),
        NodeContractVersion::v0_7(),
        old.capabilities().to_vec(),
        old.supported_contracts()
            .iter()
            .map(|c| (c.clone(), old.capability_owner(c).unwrap().clone()))
            .collect(),
        old.sensors().to_vec(),
        old.resources().to_vec(),
        old.resources()
            .iter()
            .map(|r| (r.id().clone(), old.resource_owner(r.id()).unwrap().clone()))
            .collect(),
    )
    .unwrap()
    .with_operation_support(
        old.supported_contracts()
            .iter()
            .map(|c| {
                OperationSupport::new(
                    OperationRef::from(c.clone()),
                    old.capability_owner(c).unwrap().clone(),
                )
            })
            .collect(),
    )
    .unwrap()
    .with_state_memory_exports(vec![], old.memory_providers().to_vec())
    .unwrap();
    fixture
        .controller
        .bridge
        .state_mut()
        .record_node(NodeStateSnapshot::new(
            registration,
            node.reported_status(),
            node.reported_status_received_at(),
            node.liveness(),
        ))
        .unwrap();
    memory_import::admit(
        &mut fixture.controller,
        request.clone(),
        TimestampMs::new(5),
    )
    .unwrap();
    memory_import::prepare(&mut fixture.controller, TimestampMs::new(5));
    memory_import::receipt(
        &mut fixture.controller,
        request.node_id.as_str(),
        &MemoryImportReceipt {
            import_id: request.import_id.clone(),
            command_id: format!("memory-import-{}", request.import_id),
            status: MemoryImportReceiptStatus::MemoryImportUnknown as i32,
            session_id: "session".into(),
            sequence: 1,
            reason: "interrupted import".into(),
        },
        TimestampMs::new(6),
    )
    .unwrap();
    (fixture, request)
}

/// Generates exact schema-bound protocol evidence solely for application authority unit tests.
fn query_receipt(
    request: &MemoryImportRequest,
    generation: u64,
) -> integration::grpc::v0_4::MemoryImportQueryReceipt {
    let command_id = format!("memory-import-{}", request.import_id);
    let digest = integration::memory_import_authorization_digest(
        &request.import_id,
        &command_id,
        &request.consumer_provider_id,
        &serde_json::to_string(&request.manifest).unwrap(),
        request.task_ref.mission_id().as_str(),
        request.group_id.as_str(),
        request.task_ref.task_id().as_str(),
        request.role_id.as_str(),
        &request.execution_id,
        "",
    );
    let result = serde_json::json!({"schema":"roboguide.memory-import-query-result/v0.1","import_id":request.import_id,"command_id":command_id,
        "provider_id":request.consumer_provider_id,"identity_digest":"a".repeat(64),"original_content_digest":digest,
        "provider_instance_id":"original-software-backend","provider_contract_digest":format!("sha256:{}","b".repeat(64)),"outcome":"completed","durable":true,"reason":""});
    integration::grpc::v0_4::MemoryImportQueryReceipt {
        session_id: "session".into(),
        sequence: generation + 1,
        query_id: format!("memory-query-{}-{generation}", request.import_id),
        import_id: request.import_id.clone(),
        command_id,
        generation,
        status: MemoryImportReceiptStatus::MemoryImportCompleted as i32,
        reason: String::new(),
        identity_digest: "a".repeat(64),
        provider_instance_id: "original-software-backend".into(),
        provider_contract_digest: format!("sha256:{}", "b".repeat(64)),
        result_json: result.to_string(),
    }
}

/// Query reconciliation persists original budgets but cannot release Control ownership or satisfy Tasks.
#[test]
fn original_provider_query_resolves_only_current_authorized_import_and_fences_old_wrapper() {
    let (fixture, request) = continuity_fixture();
    let events = fixture.events.clone();
    let controller = Arc::new(Mutex::new(fixture.controller));
    let gate = Arc::new(Mutex::new(()));
    let body = serde_json::to_vec(&serde_json::json!({"import_id":request.import_id,"expected_generation":0,"deadline_ms":100,"query_timeout_ms":50})).unwrap();
    let first = application::memory_query::submit(
        &controller,
        &events,
        &gate,
        &runtime::FixedClock::new(TimestampMs::new(7)),
        &body,
        "principal",
    )
    .unwrap();
    let replay = application::memory_query::submit(
        &controller,
        &events,
        &gate,
        &runtime::FixedClock::new(TimestampMs::new(8)),
        &body,
        "principal",
    )
    .unwrap();
    assert_eq!(first, replay);
    let mut live = controller.lock().unwrap();
    let bindings = live
        .bridge
        .control()
        .group(&request.group_id)
        .unwrap()
        .task_execution(&request.task_ref)
        .unwrap()
        .assignments()
        .to_vec();
    application::memory_query::receipt(
        &mut live,
        request.node_id.as_str(),
        &query_receipt(&request, 1),
        TimestampMs::new(9),
        &mut events.clone(),
    )
    .unwrap();
    assert_eq!(
        live.memory_imports[&request.import_id].phase,
        ImportPhase::Completed
    );
    assert_eq!(
        live.bridge
            .control()
            .group(&request.group_id)
            .unwrap()
            .task_execution(&request.task_ref)
            .unwrap()
            .assignments(),
        bindings
    );
    memory_import::validate_restored(&live).unwrap();
    let mut saved: ServerCheckpoint =
        serde_json::from_str(&server_checkpoint_json(&live).unwrap()).unwrap();
    saved.schema = PRE_MEMORY_SERVER_CHECKPOINT_SCHEMA.into();
    assert!(application::validate_isolation_checkpoint_schema(&saved).is_err());
}

/// A late query conclusion is history, and a new import ID cannot bypass the original Unknown.
#[test]
fn late_original_provider_query_does_not_renew_authorization_or_allow_replacement_import() {
    let (fixture, request) = continuity_fixture();
    let events = fixture.events.clone();
    let controller = Arc::new(Mutex::new(fixture.controller));
    let gate = Arc::new(Mutex::new(()));
    let body = serde_json::to_vec(&serde_json::json!({"import_id":request.import_id,"expected_generation":0,"deadline_ms":100,"query_timeout_ms":20})).unwrap();
    application::memory_query::submit(
        &controller,
        &events,
        &gate,
        &runtime::FixedClock::new(TimestampMs::new(7)),
        &body,
        "principal",
    )
    .unwrap();
    let mut live = controller.lock().unwrap();
    application::memory_query::receipt(
        &mut live,
        request.node_id.as_str(),
        &query_receipt(&request, 1),
        TimestampMs::new(28),
        &mut events.clone(),
    )
    .unwrap();
    assert_eq!(
        live.memory_imports[&request.import_id].phase,
        ImportPhase::Unknown
    );
    let mut replacement = request.clone();
    replacement.import_id = "replacement".into();
    assert!(memory_import::admit(&mut live, replacement, TimestampMs::new(29)).is_err());
    let mut changed_provider = request.clone();
    changed_provider.import_id = "replacement-provider".into();
    changed_provider.consumer_provider_id = "another-provider".into();
    assert!(
        memory_import::admit(&mut live, changed_provider, TimestampMs::new(29))
            .unwrap_err()
            .contains("unresolved original")
    );
    let retained = serde_json::to_value(&live.memory_imports[&request.import_id]).unwrap();
    assert_eq!(retained["reconciliation"]["opened_at_ms"], 7);
    assert_eq!(
        retained["reconciliation"]["queries"]["1"]["received_at_ms"],
        28
    );
    assert_eq!(
        retained["reconciliation"]["queries"]["1"]["current_authority_at_receipt"],
        false
    );
    memory_import::validate_restored(&live).unwrap();
}

/// Wrong Group/attempt/provider and identity reuse cannot create a new import or reservation.
#[test]
fn imports_require_exact_binding_and_preserve_authorization_across_restore() {
    let (mut fixture, request) = fixture();
    let now = TimestampMs::new(5);
    let mut invalid = request.clone();
    invalid.group_id = ExecutionGroupId::new("other-group").unwrap();
    assert!(memory_import::admit(&mut fixture.controller, invalid, now).is_err());
    let mut stale = request.clone();
    stale.execution_id = "old-attempt".into();
    assert!(memory_import::admit(&mut fixture.controller, stale, now).is_err());
    let mut wrong_provider = request.clone();
    wrong_provider.consumer_provider_id = "other-provider".into();
    assert!(memory_import::admit(&mut fixture.controller, wrong_provider, now).is_err());
    memory_import::admit(&mut fixture.controller, request.clone(), now).unwrap();
    memory_import::prepare(&mut fixture.controller, now);
    fixture.events.begin_batch().unwrap();
    let checkpoint = server_checkpoint_json(&fixture.controller).unwrap();
    application::save_controller_checkpoint(&fixture.events, &checkpoint).unwrap();
    fixture.events.commit_batch().unwrap();
    let saved: ServerCheckpoint = serde_json::from_str(
        &fixture
            .events
            .load_checkpoint()
            .unwrap()
            .unwrap()
            .checkpoint_json,
    )
    .unwrap();
    assert_eq!(saved.memory_imports["import-a"].deliveries, 1);
    let mut conflicting = request.clone();
    conflicting.deadline_ms += 1;
    assert!(
        memory_import::admit(&mut fixture.controller, conflicting, TimestampMs::new(6)).is_err()
    );
    let receipt = MemoryImportReceipt {
        session_id: "current-session".into(),
        sequence: 1,
        import_id: request.import_id.clone(),
        command_id: "memory-import-import-a".into(),
        status: MemoryImportReceiptStatus::MemoryImportCompleted as i32,
        reason: String::new(),
    };
    assert!(
        memory_import::receipt(
            &mut fixture.controller,
            "wrong-node",
            &receipt,
            TimestampMs::new(7)
        )
        .is_err()
    );
    memory_import::receipt(
        &mut fixture.controller,
        request.node_id.as_str(),
        &receipt,
        TimestampMs::new(7),
    )
    .unwrap();
    assert_eq!(
        fixture.controller.memory_imports["import-a"].phase,
        ImportPhase::Completed
    );
    assert!(
        !fixture
            .controller
            .bridge
            .control()
            .group(&request.group_id)
            .unwrap()
            .task_execution(&request.task_ref)
            .unwrap()
            .assignments()
            .is_empty()
    );
    memory_import::validate_restored(&fixture.controller).unwrap();
}

/// A late completion is retained as recovery evidence without renewing the deadline or repeating work.
#[test]
fn expired_import_retains_late_receipt_and_never_renews_delivery() {
    let (mut fixture, request) = fixture();
    memory_import::admit(
        &mut fixture.controller,
        request.clone(),
        TimestampMs::new(5),
    )
    .unwrap();
    memory_import::prepare(&mut fixture.controller, TimestampMs::new(5));
    memory_import::prepare(&mut fixture.controller, TimestampMs::new(1000));
    let receipt = MemoryImportReceipt {
        session_id: "current-session".into(),
        sequence: 1,
        import_id: request.import_id.clone(),
        command_id: "memory-import-import-a".into(),
        status: MemoryImportReceiptStatus::MemoryImportCompleted as i32,
        reason: String::new(),
    };
    memory_import::receipt(
        &mut fixture.controller,
        request.node_id.as_str(),
        &receipt,
        TimestampMs::new(1001),
    )
    .unwrap();
    memory_import::prepare(&mut fixture.controller, TimestampMs::new(1002));
    let intent = &fixture.controller.memory_imports["import-a"];
    assert_eq!(intent.phase, ImportPhase::Unknown);
    assert_eq!(intent.recovery_receipt, Some(ImportPhase::Completed));
    assert_eq!(intent.deliveries, 1);
    assert_eq!(intent.request.deadline_ms, 1000);
}
