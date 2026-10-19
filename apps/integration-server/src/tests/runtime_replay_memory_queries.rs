//! Metadata adversaries for the real application reducer; process checks supply backend effects.

use super::*;
use application::memory_use_query as query;
use ports::{SharedNodeStateReader, SharedNodeStateWriter};

/// Declares query support in metadata only; it does not register a device or create a backend effect.
fn fixture() -> (ParallelMission, String, domain::MemoryUseReceipt) {
    let mut fixture = ParallelMission::new();
    retain_memory(&mut fixture, 0, 3);
    fixture.fact(0, 1, ExecutionPhase::Completed);
    let execution = fixture.attempts[0].execution_id().to_string();
    usage::revoke_attempt(&mut fixture.controller, &execution, TimestampMs::new(10)).unwrap();
    let proof = use_ack(&mut fixture, 0);
    let node = fixture.attempts[0].command().node_id().clone();
    let snapshot = fixture.controller.bridge.state().node(&node).unwrap();
    let mut encoded = serde_json::to_value(snapshot).unwrap();
    encoded["registration"]["contract_version"] = "roboguide.node.v0.11".into();
    fixture
        .controller
        .bridge
        .state_mut()
        .record_node(serde_json::from_value(encoded).unwrap())
        .unwrap();
    let mission = fixture.attempts[0].command().mission_id().clone();
    fixture
        .controller
        .bridge
        .control_mut()
        .register_mission_tenant(mission, "local")
        .unwrap();
    let mut unknown = proof.clone();
    unknown.status = MemoryUseStatus::Unknown;
    unknown.providers.truncate(1);
    admit_use_ack(&mut fixture, 0, &unknown).unwrap();
    context_ack(&mut fixture, 0);
    (fixture, execution, proof)
}

/// Creates a request for the exact original revoke and complete provider set.
fn request(proof: &domain::MemoryUseReceipt, generation: u64) -> query::UseQueryRequest {
    serde_json::from_value(serde_json::json!({"command_id":proof.command_id,"expected_generation":generation,"provider_ids":proof.providers.iter().map(|p|p.provider_id.clone()).collect::<Vec<_>>(),"deadline_ms":300_100,"query_timeout_ms":30_000})).unwrap()
}

/// Admits distinct recovery evidence through the authenticated production receipt reducer.
fn complete(
    fixture: &mut ParallelMission,
    proof: &domain::MemoryUseReceipt,
    received: u64,
) -> Result<(), String> {
    let record = &fixture.controller.memory_use.records[fixture.attempts[0].execution_id()];
    let value = serde_json::to_value(&record.controls[&proof.command_id]).unwrap();
    let retained: domain::MemoryUseQuery =
        serde_json::from_value(value["reconciliation"]["queries"]["1"]["query"].clone()).unwrap();
    let receipt = domain::MemoryUseQueryReceipt {
        schema: "roboguide.memory-use-query-receipt/v0.1".into(),
        query_id: retained.query_id.clone(),
        query_digest: retained.digest().unwrap(),
        observations: proof
            .providers
            .iter()
            .map(|p| domain::MemoryUseQueryObservation {
                provider_id: p.provider_id.clone(),
                status: domain::MemoryUseQueryStatus::Confirmed,
                proof: Some(p.clone()),
                reason: String::new(),
            })
            .collect(),
    };
    let node = fixture.attempts[0].command().node_id().as_str().to_string();
    query::receipt(
        &mut fixture.controller,
        &node,
        "memory-session",
        &integration::grpc::v0_4::MemoryUseQueryReceipt {
            session_id: "memory-session".into(),
            sequence: 2,
            receipt_json: serde_json::to_string(&receipt).unwrap(),
        },
        TimestampMs::new(received),
        TimestampMs::new(received),
    )
}

/// Recovery releases only the original Task while retaining initial ambiguity and a live peer.
#[test]
fn reconciled_provider_proofs_release_original_task_without_rewriting_unknown() {
    let (mut fixture, execution, proof) = fixture();
    let original = fixture.controller.memory_use.records[&execution].controls[&proof.command_id]
        .receipt
        .clone();
    query::admit(
        &mut fixture.controller,
        request(&proof, 0),
        "stable-admin",
        Some("local"),
        TimestampMs::new(100),
    )
    .unwrap();
    query::prepare(&mut fixture.controller, TimestampMs::new(100));
    assert!(usage::blocks_task_release(
        &fixture.controller,
        fixture.attempts[0].command().task_ref()
    ));
    complete(&mut fixture, &proof, 101).unwrap();
    complete(&mut fixture, &proof, 102).unwrap();
    assert_eq!(
        fixture.controller.memory_use.records[&execution].controls[&proof.command_id].receipt,
        original
    );
    fixture.apply_outcomes();
    assert_eq!(fixture.task_lifecycle(0), TaskExecutionLifecycle::Completed);
    assert_eq!(fixture.task_lifecycle(1), TaskExecutionLifecycle::Ready);
}

/// Idempotency binds stable principal, original request and one nonrenewable session.
#[test]
fn readonly_use_admission_rejects_other_tenants_principals_extension_and_inflight_queries() {
    let (mut fixture, execution, proof) = fixture();
    assert!(
        query::admit(
            &mut fixture.controller,
            request(&proof, 0),
            "admin",
            Some("foreign"),
            TimestampMs::new(100)
        )
        .is_err()
    );
    query::admit(
        &mut fixture.controller,
        request(&proof, 0),
        "admin",
        Some("local"),
        TimestampMs::new(100),
    )
    .unwrap();
    let before = serde_json::to_value(&fixture.controller.memory_use).unwrap();
    assert!(
        !query::admit(
            &mut fixture.controller,
            request(&proof, 0),
            "admin",
            Some("local"),
            TimestampMs::new(200)
        )
        .unwrap()
        .2
    );
    assert!(
        query::admit(
            &mut fixture.controller,
            request(&proof, 0),
            "another-admin",
            Some("local"),
            TimestampMs::new(200)
        )
        .is_err()
    );
    assert!(
        query::admit(
            &mut fixture.controller,
            request(&proof, 1),
            "admin",
            Some("local"),
            TimestampMs::new(200)
        )
        .is_err()
    );
    assert_eq!(
        before,
        serde_json::to_value(&fixture.controller.memory_use).unwrap()
    );
    assert!(
        query::admit(
            &mut fixture.controller,
            request(&proof, 1),
            "admin",
            Some("local"),
            TimestampMs::new(300_101)
        )
        .is_err()
    );
    let delivery = &fixture.controller.memory_use.records[&execution].controls[&proof.command_id];
    query::validate_restore(&fixture.controller, &execution, delivery).unwrap();
    let mut saved: ServerCheckpoint =
        serde_json::from_str(&server_checkpoint_json(&fixture.controller).unwrap()).unwrap();
    saved.schema = PRE_MEMORY_QUERY_SERVER_CHECKPOINT_SCHEMA.into();
    assert!(validate_isolation_checkpoint_schema(&saved).is_err());
}

/// Late or changed-instance observations cannot authorize release even if their outer IDs match.
#[test]
fn readonly_use_late_or_replacement_backend_proof_retains_ownership() {
    let (mut fixture, _execution, proof) = fixture();
    query::admit(
        &mut fixture.controller,
        request(&proof, 0),
        "admin",
        Some("local"),
        TimestampMs::new(100),
    )
    .unwrap();
    query::prepare(&mut fixture.controller, TimestampMs::new(100));
    let mut changed = proof.clone();
    changed.providers[1].provider_instance_id = "replacement".into();
    assert!(complete(&mut fixture, &changed, 101).is_err());
    complete(&mut fixture, &proof, 30_101).unwrap();
    assert!(usage::blocks_task_release(
        &fixture.controller,
        fixture.attempts[0].command().task_ref()
    ));
    fixture.apply_outcomes();
    assert_eq!(
        fixture.task_lifecycle(0),
        TaskExecutionLifecycle::AwaitingSatisfaction
    );
}
