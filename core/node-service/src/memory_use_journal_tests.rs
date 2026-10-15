//! SQLite authority tests with metadata-only contexts, not registered devices or backend facts.

use crate::*;
use domain::{
    MemoryPreparation, MemoryUseControl, MemoryUseOperation, MemoryUseReceipt, MemoryUseStatus,
};
use sha2::{Digest as _, Sha256};

/// Supplies support metadata for the journal only; no physical inventory is registered.
fn context() -> MemoryPreparation {
    let mut context = super::preparation();
    context.schema = "roboguide.memory-preparation/v0.2".into();
    for input in &mut context.inputs {
        input.consumer = input.consumer.clone().with_use_lease_support();
    }
    context
}

/// Constructs metadata authority without a provider call or physical claim.
fn use_command(context: &MemoryPreparation, generation: u64) -> MemoryUseControl {
    let mut command = MemoryUseControl {
        schema: "roboguide.memory-use-control/v0.1".into(),
        command_id: String::new(),
        lease_id: domain::memory_use_lease_id(&context.preparation_id),
        generation,
        operation: MemoryUseOperation::Grant,
        preparation_id: context.preparation_id.clone(),
        preparation_digest: context.digest().unwrap(),
        execution_id: context.execution_id.clone(),
        node_id: context.node_id.clone(),
        source_session_id: context.source_session_id.clone(),
        tenant_id: context.tenant_id.clone(),
        group_id: context.group_id.clone(),
        registry_id: context.registry_id.clone(),
        registry_revision: context.registry_revision,
        membership: context.membership.clone(),
        configuration_digest: format!("sha256:{}", "c".repeat(64)),
        valid_for_ms: 30_000,
    };
    command.command_id = command.expected_command_id();
    command
}

/// Retains unit proof solely for persistence tests; actual adapter validation is separate.
fn record_unit_proofs(
    journal: &ExecutionJournal,
    command: &MemoryUseControl,
    context: &MemoryPreparation,
    clock: &ReceiverClockObservation,
) -> MemoryUseReceipt {
    let mut providers = Vec::new();
    for input in &context.inputs {
        let provider = input.consumer.provider_id();
        let request = serde_json::json!({"unit-metadata-intent":command.command_id});
        assert!(
            journal
                .prepare_memory_use_effect(&command.command_id, provider, &request, clock)
                .unwrap()
                .0
        );
        assert!(
            !journal
                .prepare_memory_use_effect(&command.command_id, provider, &request, clock)
                .unwrap()
                .0
        );
        let original =
            serde_json::json!({"schema":"unit.local-receipt/v1","physical_evidence":"unavailable"});
        journal
            .record_memory_use_effect_receipt(&command.command_id, provider, &original)
            .unwrap();
        providers.push(domain::MemoryUseProviderAcknowledgement {
            provider_id: provider.into(),
            provider_instance_id: "unit-backend".into(),
            provider_contract_digest: format!("sha256:{}", "d".repeat(64)),
            import_ids: vec![input.import_id.clone()],
            receipt_digest: format!(
                "sha256:{:x}",
                Sha256::digest(serde_json::to_vec(&original).unwrap())
            ),
            original_receipt: original,
        });
    }
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
        providers,
        reason: String::new(),
    }
}

/// Original budget and once-only effect survive reopen without another write grant.
#[test]
fn memory_use_journal_reopen_preserves_budget_and_once_only_effects() {
    let (_directory, path, journal) = super::journal();
    let context = context();
    let command = use_command(&context, 1);
    let clock = ReceiverClockObservation::read().unwrap();
    journal
        .prepare_memory_context(&context, &clock, 120_000, &command.configuration_digest)
        .unwrap();
    let first = journal
        .admit_memory_use_control(&command, &clock, 30_000)
        .unwrap();
    assert!(
        journal
            .current_memory_use(&context.preparation_id, &clock)
            .is_err()
    );
    let provider = context.inputs[0].consumer.provider_id();
    let request = serde_json::json!({"unit-only":true});
    assert!(
        journal
            .prepare_memory_use_effect(&command.command_id, provider, &request, &clock)
            .unwrap()
            .0
    );
    for _ in 0..3 {
        journal
            .begin_memory_use_query(&command.command_id, provider)
            .unwrap();
    }
    assert!(
        journal
            .begin_memory_use_query(&command.command_id, provider)
            .is_err()
    );
    drop(journal);
    let journal = ExecutionJournal::open(&path).unwrap();
    let later = ReceiverClockObservation {
        lower_ms: clock.lower_ms + 1000,
        ..clock.clone()
    };
    assert_eq!(
        journal
            .admit_memory_use_control(&command, &later, 30_000)
            .unwrap(),
        first
    );
    let (write, effect) = journal
        .prepare_memory_use_effect(&command.command_id, provider, &request, &later)
        .unwrap();
    assert!(!write);
    assert_eq!(effect.query_count, 3);
    assert!(
        journal
            .begin_memory_use_query(&command.command_id, provider)
            .is_err()
    );
    assert!(
        journal
            .admit_memory_use_control(&use_command(&context, 2), &later, 30_000)
            .is_err()
    );
}

/// Durable proof is necessary, while revoke fences late renewal immediately.
#[test]
fn memory_use_journal_requires_proofs_and_fences_late_grants() {
    let (_directory, _path, journal) = super::journal();
    let context = context();
    let command = use_command(&context, 1);
    let clock = ReceiverClockObservation::read().unwrap();
    journal
        .prepare_memory_context(&context, &clock, 120_000, &command.configuration_digest)
        .unwrap();
    journal
        .admit_memory_use_control(&command, &clock, 30_000)
        .unwrap();
    let receipt = record_unit_proofs(&journal, &command, &context, &clock);
    assert!(
        journal
            .current_memory_use(&context.preparation_id, &clock)
            .is_err()
    );
    journal.record_memory_use_receipt(&receipt).unwrap();
    assert_eq!(
        journal
            .current_memory_use(&context.preparation_id, &clock)
            .unwrap()
            .receipt,
        Some(receipt.clone())
    );
    let mut revoke = command.clone();
    revoke.operation = MemoryUseOperation::Revoke;
    revoke.generation = 0;
    revoke.valid_for_ms = 0;
    revoke.command_id = revoke.expected_command_id();
    journal
        .admit_memory_use_control(&revoke, &clock, 0)
        .unwrap();
    assert!(
        journal
            .current_memory_use(&context.preparation_id, &clock)
            .is_err()
    );
    assert!(
        journal
            .admit_memory_use_control(&use_command(&context, 2), &clock, 30_000)
            .is_err()
    );
    assert_eq!(
        journal.record_memory_use_receipt(&receipt).unwrap(),
        receipt
    );
}

/// A SQL abort cannot leave permission without its exact durable aggregate receipt.
#[test]
fn memory_use_journal_receipt_failure_does_not_authorize_execution() {
    let (_directory, path, journal) = super::journal();
    let context = context();
    let command = use_command(&context, 1);
    let clock = ReceiverClockObservation::read().unwrap();
    journal
        .prepare_memory_context(&context, &clock, 120_000, &command.configuration_digest)
        .unwrap();
    journal
        .admit_memory_use_control(&command, &clock, 30_000)
        .unwrap();
    let receipt = record_unit_proofs(&journal, &command, &context, &clock);
    let connection = rusqlite::Connection::open(path).unwrap();
    connection.execute_batch("CREATE TRIGGER abort_use_receipt BEFORE UPDATE OF receipt_json ON memory_use_commands BEGIN SELECT RAISE(ABORT,'injected use receipt failure'); END").unwrap();
    assert!(journal.record_memory_use_receipt(&receipt).is_err());
    assert!(
        journal
            .current_memory_use(&context.preparation_id, &clock)
            .is_err()
    );
    assert!(
        journal
            .memory_use_command(&command.command_id)
            .unwrap()
            .unwrap()
            .receipt
            .is_none()
    );
    connection
        .execute_batch("DROP TRIGGER abort_use_receipt")
        .unwrap();
    journal.record_memory_use_receipt(&receipt).unwrap();
    assert!(
        journal
            .current_memory_use(&context.preparation_id, &clock)
            .is_ok()
    );
}
