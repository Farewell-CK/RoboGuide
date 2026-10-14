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
    (fixture, request)
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
