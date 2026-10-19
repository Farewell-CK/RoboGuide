//! Metadata-only clock/replay tests; no registration, provider outcome or device evidence is created.

use super::*;

/// Supplies a valid immutable command solely to test application journal admission and timing.
fn control(generation: u64) -> MemoryUseControl {
    let preparation_id = "unit-original-preparation";
    let mut command = MemoryUseControl {
        schema: "roboguide.memory-use-control/v0.1".into(),
        command_id: String::new(),
        lease_id: domain::memory_use_lease_id(preparation_id),
        generation,
        operation: MemoryUseOperation::Grant,
        preparation_id: preparation_id.into(),
        preparation_digest: format!("sha256:{}", "a".repeat(64)),
        execution_id: "unit-original-attempt".into(),
        node_id: domain::NodeId::new("unit-metadata-node").unwrap(),
        source_session_id: "unit-original-session".into(),
        tenant_id: "unit-tenant".into(),
        group_id: domain::ExecutionGroupId::new("unit-group").unwrap(),
        registry_id: "unit-policy".into(),
        registry_revision: 1,
        membership: domain::GroupMembershipVersion {
            generation: 1,
            bindings_digest: format!("sha256:{}", "b".repeat(64)),
        },
        configuration_digest: format!("sha256:{}", "c".repeat(64)),
        valid_for_ms: 30_000,
    };
    command.command_id = command.expected_command_id();
    command
}

/// Supplies an empty local metadata journal rather than a Control or Runtime authority.
fn record() -> MemoryUseRecord {
    MemoryUseRecord {
        execution_id: "unit-original-attempt".into(),
        generation: 0,
        revoked: false,
        controls: BTreeMap::new(),
        reason: String::new(),
    }
}

/// Supplies only the timing fields used below; Domain/provider validation is independently required.
fn timing_receipt(command: &MemoryUseControl) -> MemoryUseReceipt {
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
        providers: Vec::new(),
        reason: "timing-only metadata; not provider proof".into(),
    }
}

/// A release gate cannot treat an aggregate revoke status as complete without every provider.
#[test]
fn revoke_release_gate_requires_distinct_provider_acknowledgements() {
    let command = control(1);
    let mut revoke = command.clone();
    revoke.operation = MemoryUseOperation::Revoke;
    revoke.generation = 0;
    revoke.valid_for_ms = 0;
    revoke.command_id = revoke.expected_command_id();
    let mut record = record();
    queue(&mut record, revoke.clone(), domain::TimestampMs::new(100)).unwrap();
    let preparation: domain::MemoryPreparation = serde_json::from_value(serde_json::json!({
        "schema":"roboguide.memory-preparation/v0.2","preparation_id":"unit-original-preparation",
        "tenant_id":"unit-tenant","group_id":"unit-group","task_ref":{"mission_id":"unit-mission","task_id":"unit-task"},
        "role_id":"unit-role","execution_id":"unit-original-attempt","node_id":"unit-metadata-node","source_session_id":"unit-original-session",
        "membership":{"generation":1,"bindings_digest":format!("sha256:{}","b".repeat(64))},"registry_id":"unit-policy","registry_revision":1,
        "invocation":{"mission_id":"unit-mission","group_id":"unit-group","task_id":"unit-task","role_id":"unit-role","operation":"unit@v1","objective":"unit","parameters":{},"resource_ids":[]},
        "resource_ids":[],"valid_for_ms":120000,"inputs":[{"requirement":{"selector":{"memory_id":"unit-memory","revision_id":"r1"},"kind":"artifact","payload_schema":"unit.file/v1","media_type":"application/octet-stream"},
        "manifest":{"schema":"roboguide.memory-manifest/v0.1","selector":{"memory_id":"unit-memory","revision_id":"r1"},"kind":"artifact","provider_id":"unit-source","owner":{"owner":"node","node_id":"unit-publisher","local_system_id":"files"},"scope":{"kind":"global"},"visibility":"exchangeable","payload_schema":"unit.file/v1","media_type":"application/octet-stream","artifact":{"content_digest":format!("sha256:{}","c".repeat(64)),"byte_size":1},"created_at":1},
        "typed_map":null,"consumer":{"preparation_contract":"unit","use_contract":"roboguide.memory-use/shared-kernel/v0.1","provider_id":"provider-a","local_system_id":"files","kind":"artifact","scope":{"kind":"global"},"visibility":"exchangeable","payload_schema":"unit.file/v1","media_type":"application/octet-stream"},"import_id":"unit-import"}]
    })).unwrap();
    let delivery = record.controls.get_mut(&revoke.command_id).unwrap();
    delivery.receipt = Some(MemoryUseReceipt {
        schema: "roboguide.memory-use-receipt/v0.1".into(),
        command_id: revoke.command_id.clone(),
        lease_id: revoke.lease_id.clone(),
        generation: 0,
        preparation_id: revoke.preparation_id.clone(),
        preparation_digest: revoke.preparation_digest.clone(),
        configuration_digest: revoke.configuration_digest.clone(),
        status: MemoryUseStatus::Revoked,
        remaining_budget_ms: 0,
        providers: vec![],
        reason: String::new(),
    });
    delivery.received_at_ms = Some(101);
    assert!(!revoke_acknowledged(&record, &preparation));
    record
        .controls
        .get_mut(&revoke.command_id)
        .unwrap()
        .receipt
        .as_mut()
        .unwrap()
        .providers
        .push(domain::MemoryUseProviderAcknowledgement {
            provider_id: "provider-a".into(),
            provider_instance_id: "unit-backend".into(),
            provider_contract_digest: format!("sha256:{}", "d".repeat(64)),
            import_ids: vec![],
            receipt_digest: format!("sha256:{}", "e".repeat(64)),
            original_receipt: serde_json::json!({"status":"revoked"}),
        });
    assert!(revoke_acknowledged(&record, &preparation));
    record
        .controls
        .get_mut(&revoke.command_id)
        .unwrap()
        .receipt
        .as_mut()
        .unwrap()
        .providers
        .push(domain::MemoryUseProviderAcknowledgement {
            provider_id: "provider-a".into(),
            provider_instance_id: "unit-backend".into(),
            provider_contract_digest: format!("sha256:{}", "d".repeat(64)),
            import_ids: vec![],
            receipt_digest: format!("sha256:{}", "e".repeat(64)),
            original_receipt: serde_json::json!({"status":"revoked"}),
        });
    assert!(!revoke_acknowledged(&record, &preparation));
}

/// An identical command cannot gain time or delivery opportunities through a retry or serialization.
#[test]
fn duplicate_grant_preserves_original_issue_and_expiry() {
    let mut record = record();
    let original = control(1);
    queue(&mut record, original.clone(), domain::TimestampMs::new(100)).unwrap();
    let delivery = record.controls.get_mut(&original.command_id).unwrap();
    delivery.receipt = Some(timing_receipt(&original));
    delivery.received_at_ms = Some(9000);
    let retained = record.clone();
    queue(&mut record, original, domain::TimestampMs::new(10_000)).unwrap();
    assert_eq!(record, retained);
    let restored: MemoryUseRecord =
        serde_json::from_str(&serde_json::to_string(&record).unwrap()).unwrap();
    assert_eq!(
        expires(restored.controls.values().next().unwrap()),
        Some(20_100)
    );
    assert!(granted(&restored, domain::TimestampMs::new(20_099)).is_some());
    assert!(granted(&restored, domain::TimestampMs::new(20_100)).is_none());
}

/// Renewal preserves live-work time but cannot dispatch Execute before the new head is accepted.
#[test]
fn pending_successor_retains_continuity_without_authorizing_new_execute() {
    let mut record = record();
    let first = control(1);
    queue(&mut record, first.clone(), domain::TimestampMs::new(100)).unwrap();
    record.controls.get_mut(&first.command_id).unwrap().receipt = Some(timing_receipt(&first));
    assert!(dispatch_grant(&record, domain::TimestampMs::new(200)));

    let second = control(2);
    queue(
        &mut record,
        second.clone(),
        domain::TimestampMs::new(10_100),
    )
    .unwrap();
    let now = domain::TimestampMs::new(10_200);
    assert_eq!(granted(&record, now).unwrap().command.generation, 1);
    assert!(!dispatch_grant(&record, now));

    record.controls.get_mut(&second.command_id).unwrap().receipt = Some(timing_receipt(&second));
    assert!(dispatch_grant(&record, now));
    assert!(!dispatch_grant(&record, domain::TimestampMs::new(30_100)));
}

/// Bounded history preserves its head and rejects a pruned generation instead of recreating it.
#[test]
fn pruned_commands_cannot_restart_generation_or_budget() {
    let mut record = record();
    for generation in 1..=10 {
        queue(
            &mut record,
            control(generation),
            domain::TimestampMs::new(generation * 10),
        )
        .unwrap();
    }
    assert_eq!(record.controls.len(), RETAINED_GRANTS);
    assert_eq!(record.generation, 10);
    assert!(queue(&mut record, control(1), domain::TimestampMs::new(999)).is_err());
    assert!(queue(&mut record, control(12), domain::TimestampMs::new(999)).is_err());
    let mut substituted = control(11);
    substituted.execution_id = "another-unit-attempt".into();
    assert!(queue(&mut record, substituted, domain::TimestampMs::new(999)).is_err());
    assert_eq!(record.generation, 10);
}

/// A retained positive or late receipt cannot reverse the application fence or admit a successor.
#[test]
fn revoke_is_irreversible_even_with_retained_positive_receipt() {
    let mut record = record();
    let original = control(1);
    queue(&mut record, original.clone(), domain::TimestampMs::new(100)).unwrap();
    let mut revoke = original.clone();
    revoke.operation = MemoryUseOperation::Revoke;
    revoke.generation = 0;
    revoke.valid_for_ms = 0;
    revoke.command_id = revoke.expected_command_id();
    queue(&mut record, revoke.clone(), domain::TimestampMs::new(200)).unwrap();
    record
        .controls
        .get_mut(&original.command_id)
        .unwrap()
        .receipt = Some(timing_receipt(&original));
    assert!(granted(&record, domain::TimestampMs::new(201)).is_none());
    assert!(queue(&mut record, control(2), domain::TimestampMs::new(300)).is_err());
    let retained = record.clone();
    queue(&mut record, revoke, domain::TimestampMs::new(400)).unwrap();
    assert_eq!(record, retained);
}
