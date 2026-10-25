//! Metadata-only authority tests; these fixtures register no Node and prove no physical fact.

use super::*;
use crate::{MapArtifactManifest, map_memory_manifest};

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

/// Builds synthetic scoped lineage; it creates no publication, Node, provider or device fact.
fn scoped_preparation() -> MemoryPreparation {
    let map: MapArtifactManifest = serde_json::from_value(serde_json::json!({
        "schema":"roboguide.map-manifest/v0.2","map_id":"unit-map","revision_id":"original",
        "producer_node_id":"unit-producer","producer_local_system_id":"files","producer_provider_id":"unit-maps",
        "source_mission_id":"unit-production","source_execution_id":"unit-production-attempt",
        "source_task_ref":{"mission_id":"unit-production","task_id":"unit-build"},
        "media_type":"application/vnd.roboguide.spatial-map","payload_schema":"unit.map/v1",
        "content_digest":format!("sha256:{}","b".repeat(64)),"byte_size":10,"created_at_ms":1,
        "root_frame":"unit-original-frame","coordinate_convention":"unit-declared-convention",
        "spatial_anchor":{"kind":"fixed","id":"unit-original-anchor"},
        "format":{"name":"unit-format","version":"v1"},"resolution_meters":0.1
    })).unwrap();
    let mut manifest = serde_json::to_value(map_memory_manifest(&map).unwrap()).unwrap();
    manifest["selector"]["revision_id"] = "scoped-child".into();
    manifest["scope"] =
        serde_json::json!({"kind":"execution_group","execution_group_id":"unit-group"});
    manifest["created_at"] = 2.into();
    let mut context = serde_json::to_value(preparation()).unwrap();
    context["schema"] = "roboguide.memory-preparation/v0.3".into();
    let input = &mut context["inputs"][0];
    input["manifest"] = manifest.clone();
    input["typed_map"] = serde_json::to_value(map).unwrap();
    input["requirement"] = serde_json::json!({"selector":manifest["selector"],"kind":"spatial",
        "payload_schema":"unit.map/v1","media_type":"application/vnd.roboguide.spatial-map","source_contract":"scoped_typed_map"});
    input["consumer"]["preparation_contract"] =
        "roboguide.memory-provider-availability/v0.2".into();
    input["consumer"]["kind"] = "spatial".into();
    input["consumer"]["payload_schema"] = "unit.map/v1".into();
    input["consumer"]["media_type"] = "application/vnd.roboguide.spatial-map".into();
    serde_json::from_value(context).unwrap()
}

/// Explicit new negotiation is mandatory; old contexts cannot silently borrow scoped typed meaning.
#[test]
fn scoped_map_preparation_requires_original_root_and_negotiated_contract() {
    let context = scoped_preparation();
    context.validate().unwrap();
    command(&context).validate_preparation(&context).unwrap();
    assert!(context.inputs[0].is_scoped_typed_map());
    for schema in [
        "roboguide.memory-preparation/v0.1",
        "roboguide.memory-preparation/v0.2",
    ] {
        let mut old = context.clone();
        old.schema = schema.into();
        assert!(old.validate().is_err());
    }
    let mut downgraded = serde_json::to_value(&context).unwrap();
    downgraded["inputs"][0]["consumer"]["preparation_contract"] =
        "roboguide.memory-provider-availability/v0.1".into();
    let old: MemoryPreparation = serde_json::from_value(downgraded).unwrap();
    assert!(old.validate().is_err());
    assert!(
        !context.inputs[0]
            .requirement
            .matches_provider(&old.inputs[0].consumer)
    );
    assert!(
        context.inputs[0]
            .requirement
            .matches_provider(&context.inputs[0].consumer)
    );
}

/// Independent scoped metadata cannot alter the producing owner, bytes, attempt or receiving Group.
#[test]
fn scoped_map_preparation_rejects_source_substitution_and_scope_broadening() {
    let context = scoped_preparation();
    let original = serde_json::to_value(&context).unwrap();
    for pointer in [
        "/inputs/0/manifest/provider_id",
        "/inputs/0/manifest/owner/node_id",
        "/inputs/0/manifest/source_execution_id",
        "/inputs/0/manifest/source_mission_id",
        "/inputs/0/manifest/scope/execution_group_id",
        "/inputs/0/manifest/artifact/content_digest",
    ] {
        let mut changed = original.clone();
        *changed.pointer_mut(pointer).unwrap() = if pointer.ends_with("content_digest") {
            format!("sha256:{}", "c".repeat(64)).into()
        } else {
            "another-original".into()
        };
        let changed: MemoryPreparation = serde_json::from_value(changed).unwrap();
        assert!(changed.validate().is_err(), "{pointer}");
    }
    let mut changed = context.clone();
    changed.inputs[0].typed_map = None;
    assert!(changed.validate().is_err());
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
