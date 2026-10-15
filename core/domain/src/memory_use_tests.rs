//! Metadata-only authority tests; these fixtures register no Node and prove no physical fact.

use super::*;

/// Supplies complete semantic metadata only for deterministic command/receipt validation.
fn preparation() -> MemoryPreparation {
    serde_json::from_value(serde_json::json!({
        "schema":"roboguide.memory-preparation/v0.2","preparation_id":"unit-preparation",
        "tenant_id":"unit-tenant","group_id":"unit-group",
        "task_ref":{"mission_id":"unit-mission","task_id":"unit-task"},
        "role_id":"unit-role","execution_id":"unit-attempt","node_id":"unit-receiver",
        "source_session_id":"unit-session","membership":{"generation":1,"bindings_digest":format!("sha256:{}","a".repeat(64))},
        "registry_id":"unit-content-policy","registry_revision":1,
        "invocation":{"mission_id":"unit-mission","group_id":"unit-group","task_id":"unit-task",
            "role_id":"unit-role","operation":"compute.hash-files@v1","objective":"unit metadata",
            "parameters":{},"resource_ids":[]},
        "resource_ids":[],"valid_for_ms":120000,"inputs":[{
            "requirement":{"selector":{"memory_id":"unit-memory","revision_id":"r1"},"kind":"artifact",
                "payload_schema":"unit.file/v1","media_type":"application/octet-stream"},
            "manifest":{"schema":"roboguide.memory-manifest/v0.1",
                "selector":{"memory_id":"unit-memory","revision_id":"r1"},"kind":"artifact",
                "provider_id":"unit-source","owner":{"owner":"node","node_id":"unit-publisher","local_system_id":"files"},
                "scope":{"kind":"global"},"visibility":"exchangeable","payload_schema":"unit.file/v1",
                "media_type":"application/octet-stream","artifact":{"content_digest":format!("sha256:{}","b".repeat(64)),"byte_size":10},
                "created_at":1},
            "typed_map":null,"consumer":{"preparation_contract":"roboguide.memory-provider-availability/v0.1",
                "use_contract":"roboguide.memory-use/shared-kernel/v0.1",
                "provider_id":"unit-files","local_system_id":"files","kind":"artifact",
                "scope":{"kind":"global"},"visibility":"exchangeable","payload_schema":"unit.file/v1",
                "media_type":"application/octet-stream"},"import_id":"unit-import"}]
    })).unwrap()
}

/// Constructs metadata authority without invoking Control, providers or devices.
fn command(preparation: &MemoryPreparation) -> MemoryUseControl {
    let mut value = MemoryUseControl {
        schema: "roboguide.memory-use-control/v0.1".into(),
        command_id: String::new(),
        lease_id: memory_use_lease_id(&preparation.preparation_id),
        generation: 1,
        operation: MemoryUseOperation::Grant,
        preparation_id: preparation.preparation_id.clone(),
        preparation_digest: preparation.digest().unwrap(),
        execution_id: preparation.execution_id.clone(),
        node_id: preparation.node_id.clone(),
        source_session_id: preparation.source_session_id.clone(),
        tenant_id: preparation.tenant_id.clone(),
        group_id: preparation.group_id.clone(),
        registry_id: preparation.registry_id.clone(),
        registry_revision: preparation.registry_revision,
        membership: preparation.membership.clone(),
        configuration_digest: format!("sha256:{}", "c".repeat(64)),
        valid_for_ms: MEMORY_USE_MAX_BUDGET_MS,
    };
    value.command_id = value.expected_command_id();
    value
}

/// Supplies opaque unit evidence; Node adapter tests own actual local-profile validation.
fn receipt(command: &MemoryUseControl) -> MemoryUseReceipt {
    let original_receipt =
        serde_json::json!({"schema":"unit.local-receipt/v1","physical_evidence":"unavailable"});
    MemoryUseReceipt {
        schema: "roboguide.memory-use-receipt/v0.1".into(),
        command_id: command.command_id.clone(),
        lease_id: command.lease_id.clone(),
        generation: command.generation,
        preparation_id: command.preparation_id.clone(),
        preparation_digest: command.preparation_digest.clone(),
        configuration_digest: command.configuration_digest.clone(),
        status: MemoryUseStatus::Granted,
        remaining_budget_ms: 20_000,
        providers: vec![MemoryUseProviderAcknowledgement {
            provider_id: "unit-files".into(),
            provider_instance_id: "unit-backend".into(),
            provider_contract_digest: format!("sha256:{}", "d".repeat(64)),
            import_ids: vec!["unit-import".into()],
            receipt_digest: crate::memory_authority::metadata_digest(&original_receipt).unwrap(),
            original_receipt,
        }],
        reason: String::new(),
    }
}

/// Commands cannot substitute source, owner, interval or immutable identity.
#[test]
fn use_commands_bind_original_context_and_bounded_authority() {
    let preparation = preparation();
    let original = command(&preparation);
    original.validate_preparation(&preparation).unwrap();
    assert_eq!(
        original.digest().unwrap(),
        crate::memory_authority::metadata_digest(&original).unwrap()
    );
    let mut changed = original.clone();
    changed.node_id = NodeId::new("another-unit-owner").unwrap();
    assert!(changed.validate_preparation(&preparation).is_err());
    let mut changed = original.clone();
    changed.membership.bindings_digest = format!("sha256:{}", "e".repeat(64));
    assert!(changed.validate_preparation(&preparation).is_err());
    changed.membership.generation += 1;
    changed.validate_preparation(&preparation).unwrap();
    let mut changed = original.clone();
    changed.valid_for_ms += 1;
    assert!(changed.validate().is_err());
    let mut changed = original.clone();
    changed.generation = 0;
    changed.command_id = changed.expected_command_id();
    assert!(changed.validate().is_err());
    let mut changed = original.clone();
    changed.command_id.push_str("-replacement");
    assert!(changed.validate().is_err());
    let mut changed = original.clone();
    changed.operation = MemoryUseOperation::Revoke;
    changed.generation = 0;
    changed.valid_for_ms = 0;
    changed.command_id = changed.expected_command_id();
    changed.validate_preparation(&preparation).unwrap();
}

/// Positive receipts need every provider/input and cannot alter retained opaque proof.
#[test]
fn use_receipt_requires_complete_provider_coverage_and_original_budget() {
    let preparation = preparation();
    let command = command(&preparation);
    let original = receipt(&command);
    original.validate(&command, &preparation).unwrap();
    let mut changed = original.clone();
    changed.providers.clear();
    assert!(changed.validate(&command, &preparation).is_err());
    let mut changed = original.clone();
    changed.providers[0]
        .import_ids
        .push("another-unit-input".into());
    assert!(changed.validate(&command, &preparation).is_err());
    let mut changed = original.clone();
    changed.providers.push(changed.providers[0].clone());
    assert!(changed.validate(&command, &preparation).is_err());
    let mut changed = original.clone();
    changed.providers[0].original_receipt["physical_evidence"] = serde_json::json!("changed");
    assert!(changed.validate(&command, &preparation).is_err());
    let mut changed = original.clone();
    changed.remaining_budget_ms = 0;
    assert!(changed.validate(&command, &preparation).is_err());
    changed.status = MemoryUseStatus::Unknown;
    changed.validate(&command, &preparation).unwrap();
    changed.providers[0].import_ids = vec!["invented-unit-input".into()];
    assert!(changed.validate(&command, &preparation).is_err());
}
