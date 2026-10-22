//! Metadata adversaries for release scope; positive file effects are verified by process checks.

use super::*;
use application::{memory_preparation as preparation, memory_use as usage};
use domain::{MemoryControlKind, MemoryControlStatus, MemoryUseOperation, MemoryUseStatus};

#[path = "runtime_replay_memory_queries.rs"]
mod queries;

/// Adds explicitly synthetic journal evidence to existing independently scheduled software slots.
fn retain_memory(fixture: &mut ParallelMission, index: usize, providers: usize) {
    let attempt = &fixture.attempts[index];
    let command = attempt.command();
    let configuration = format!("sha256:{}", "c".repeat(64));
    let inputs = (0..providers)
        .map(|ordinal| {
            let selector = domain::MemorySelector::new(
                domain::MemoryId::new(format!("metadata-{ordinal}")).unwrap(),
                domain::MemoryRevisionId::new("r1").unwrap(),
            );
            let manifest = domain::MemoryArtifactManifest::new(
                selector.clone(),
                domain::MemoryKind::Artifact,
                "metadata-source",
                domain::MemoryOwner::RoboGuide {
                    component: "unit-only".into(),
                },
                domain::MemoryScope::Global,
                domain::MemoryVisibility::Exchangeable,
                "unit.bytes/v1",
                "application/octet-stream",
                Some(domain::MemoryArtifactRef::new(
                    domain::ContentDigest::new(format!("sha256:{}", "b".repeat(64))).unwrap(),
                    1,
                )),
                None,
                None,
                None,
                TimestampMs::new(1),
            )
            .unwrap();
            domain::PreparedMemoryInput {
                requirement: domain::MemoryRequirement {
                    selector,
                    kind: domain::MemoryKind::Artifact,
                    payload_schema: "unit.bytes/v1".into(),
                    media_type: "application/octet-stream".into(),
                },
                manifest,
                typed_map: None,
                consumer: domain::MemoryProviderDescriptor::new(
                    format!("metadata-provider-{ordinal}"),
                    domain::LocalSystemId::new("fixture-runtime").unwrap(),
                    domain::MemoryKind::Artifact,
                    domain::MemoryScopeLimit::Global,
                    domain::MemoryVisibility::Exchangeable,
                    "unit.bytes/v1",
                    "application/octet-stream",
                )
                .unwrap()
                .with_preparation_support()
                .with_use_lease_support(),
                import_id: format!("metadata-import-{index}-{ordinal}"),
            }
        })
        .collect::<Vec<_>>();
    let context = domain::MemoryPreparation {
        schema: "roboguide.memory-preparation/v0.2".into(),
        preparation_id: format!("metadata-preparation-{index}"),
        tenant_id: "local".into(),
        group_id: command.group_id().clone(),
        task_ref: command.task_ref().clone(),
        role_id: command.role_id().clone(),
        execution_id: attempt.execution_id().into(),
        node_id: command.node_id().clone(),
        source_session_id: "memory-session".into(),
        membership: fixture
            .controller
            .bridge
            .control()
            .group(&fixture.group_id)
            .unwrap()
            .membership_version()
            .unwrap()
            .clone(),
        registry_id: "metadata-registry".into(),
        registry_revision: 1,
        invocation: IntegrationRuntimeBridge::<state::SqliteEventLog>::memory_invocation_json(
            command,
            &[],
        )
        .unwrap(),
        resource_ids: vec![],
        valid_for_ms: 120_000,
        inputs,
    };
    context.validate().unwrap();
    fixture
        .controller
        .bridge
        .control_mut()
        .register_node_session(&context.node_id, "memory-session", 5000)
        .unwrap();
    let probe = domain::MemoryControlCommand {
        schema: "roboguide.memory-control/v0.1".into(),
        preparation_id: context.preparation_id.clone(),
        preparation_digest: context.digest().unwrap(),
        operation: MemoryControlKind::Probe,
        generation: 1,
        preparation: None,
    };
    let available = domain::MemoryControlReceipt {
        configuration_digest: Some(configuration.clone()),
        command_id: probe.command_id(),
        preparation_id: context.preparation_id.clone(),
        preparation_digest: context.digest().unwrap(),
        status: MemoryControlStatus::Available,
        remaining_budget_ms: 120_000,
        observations: context
            .inputs
            .iter()
            .enumerate()
            .map(|(ordinal, input)| domain::MemoryAvailabilityEvidence {
                schema: "roboguide.memory-provider-availability/v0.1".into(),
                import_id: input.import_id.clone(),
                identity_digest: format!("sha256:{}", "d".repeat(64)),
                provider_instance_id: format!("metadata-backend-{ordinal}"),
                provider_contract_digest: format!("sha256:{}", "e".repeat(64)),
                manifest_digest: domain::memory_manifest_digest(&input.manifest).unwrap(),
                content_digest: input
                    .manifest
                    .artifact()
                    .unwrap()
                    .content_digest()
                    .to_string(),
                byte_size: 1,
                available: true,
                reason: "unit evidence only".into(),
            })
            .collect(),
        reason: String::new(),
    };
    available.validate(&probe, &context).unwrap();
    fixture.controller.memory_preparation.records.insert(
        attempt.execution_id().into(),
        preparation::MemoryPreparationRecord {
            execution_id: attempt.execution_id().into(),
            admitted_at_ms: 3,
            deadline_ms: 120_003,
            phase: preparation::MemoryPreparationPhase::Ready,
            preparation: Some(context.clone()),
            configuration_digest: Some(configuration.clone()),
            controls: BTreeMap::from([(
                probe.command_id(),
                preparation::MemoryControlDelivery {
                    command: probe,
                    issued_at_ms: 3,
                    last_delivery_ms: Some(3),
                    deliveries: 1,
                    receipt: Some(available),
                    received_at_ms: Some(4),
                },
            )]),
            probe_generation: 1,
            available_until_ms: Some(10_003),
            reason: String::new(),
        },
    );
    let mut grant = domain::MemoryUseControl {
        schema: "roboguide.memory-use-control/v0.1".into(),
        command_id: String::new(),
        lease_id: domain::memory_use_lease_id(&context.preparation_id),
        generation: 1,
        operation: MemoryUseOperation::Grant,
        preparation_id: context.preparation_id.clone(),
        preparation_digest: context.digest().unwrap(),
        execution_id: context.execution_id.clone(),
        node_id: context.node_id.clone(),
        source_session_id: context.source_session_id.clone(),
        tenant_id: context.tenant_id.clone(),
        group_id: context.group_id.clone(),
        registry_id: context.registry_id.clone(),
        registry_revision: 1,
        membership: context.membership.clone(),
        configuration_digest: configuration,
        valid_for_ms: 30_000,
    };
    grant.command_id = grant.expected_command_id();
    fixture.controller.memory_use.records.insert(
        attempt.execution_id().into(),
        usage::MemoryUseRecord {
            execution_id: attempt.execution_id().into(),
            generation: 1,
            revoked: false,
            controls: BTreeMap::from([(
                grant.command_id.clone(),
                usage::MemoryUseDelivery {
                    command: grant,
                    issued_at_ms: 3,
                    deliveries: 1,
                    last_delivery_ms: Some(3),
                    receipt: None,
                    received_at_ms: None,
                    reconciliation: None,
                },
            )]),
            reason: String::new(),
        },
    );
}

/// Constructs an authenticated-envelope unit ACK with exact retained original backend identities.
fn use_ack(fixture: &mut ParallelMission, index: usize) -> domain::MemoryUseReceipt {
    let record = fixture
        .controller
        .memory_use
        .records
        .get_mut(fixture.attempts[index].execution_id())
        .unwrap();
    let delivery = record
        .controls
        .values_mut()
        .find(|delivery| delivery.command.operation == MemoryUseOperation::Revoke)
        .unwrap();
    delivery.deliveries = 1;
    delivery.last_delivery_ms = Some(10);
    let command = &delivery.command;
    let context = fixture.controller.memory_preparation.records[&record.execution_id]
        .preparation
        .as_ref()
        .unwrap();
    domain::MemoryUseReceipt { schema: "roboguide.memory-use-receipt/v0.1".into(), command_id: command.command_id.clone(), lease_id: command.lease_id.clone(), generation: 0, preparation_id: command.preparation_id.clone(), preparation_digest: command.preparation_digest.clone(), configuration_digest: command.configuration_digest.clone(), status: MemoryUseStatus::Revoked, remaining_budget_ms: 0,
        providers: context.inputs.iter().enumerate().map(|(ordinal, input)| {
            let original_receipt = serde_json::json!({"schema":"unit.receipt/v1", "command_id":command.command_id,"status":"revoked","physical_evidence":"unavailable"});
            domain::MemoryUseProviderAcknowledgement { provider_id: input.consumer.provider_id().into(), provider_instance_id: format!("metadata-backend-{ordinal}"), provider_contract_digest: format!("sha256:{}", "e".repeat(64)), import_ids: vec![], receipt_digest: format!("sha256:{:x}", Sha256::digest(serde_json::to_vec(&original_receipt).unwrap())), original_receipt }
        }).collect(), reason: String::new() }
}

/// Sends unit evidence through the production application reducer, without a fabricated device.
fn admit_use_ack(
    fixture: &mut ParallelMission,
    index: usize,
    ack: &domain::MemoryUseReceipt,
) -> Result<(), String> {
    usage::receipt(
        &mut fixture.controller,
        fixture.attempts[index].command().node_id().as_str(),
        "memory-session",
        &integration::grpc::v0_4::MemoryUseReceipt {
            session_id: "memory-session".into(),
            sequence: 1,
            receipt_json: serde_json::to_string(ack).unwrap(),
        },
        TimestampMs::new(11),
        TimestampMs::new(11),
    )
}

/// Confirms only the Node context fence; it is deliberately insufficient for provider release.
fn context_ack(fixture: &mut ParallelMission, index: usize) {
    let record = fixture
        .controller
        .memory_preparation
        .records
        .get_mut(fixture.attempts[index].execution_id())
        .unwrap();
    let delivery = record
        .controls
        .values_mut()
        .find(|delivery| delivery.command.operation == MemoryControlKind::Revoke)
        .unwrap();
    delivery.deliveries = 1;
    delivery.last_delivery_ms = Some(10);
    let ack = domain::MemoryControlReceipt {
        configuration_digest: record.configuration_digest.clone(),
        command_id: delivery.command.command_id(),
        preparation_id: delivery.command.preparation_id.clone(),
        preparation_digest: delivery.command.preparation_digest.clone(),
        status: MemoryControlStatus::Revoked,
        remaining_budget_ms: 0,
        observations: vec![],
        reason: String::new(),
    };
    preparation::receipt(
        &mut fixture.controller,
        fixture.attempts[index].command().node_id().as_str(),
        "memory-session",
        &integration::grpc::v0_4::MemoryControlReceipt {
            session_id: "memory-session".into(),
            sequence: 1,
            receipt_json: serde_json::to_string(&ack).unwrap(),
        },
        TimestampMs::new(11),
        TimestampMs::new(11),
    )
    .unwrap();
}

/// Completion records locally, then releases only its own ACK-complete Task while a peer runs.
#[test]
fn completed_memory_task_advances_without_revoking_live_peer() {
    let mut fixture = ParallelMission::new();
    fixture.fact(0, 1, ExecutionPhase::Started);
    fixture.fact(1, 1, ExecutionPhase::Started);
    retain_memory(&mut fixture, 0, 2);
    retain_memory(&mut fixture, 1, 1);
    fixture.fact(0, 2, ExecutionPhase::Completed);
    assert_eq!(
        fixture.task_lifecycle(0),
        TaskExecutionLifecycle::AwaitingSatisfaction
    );
    let execution = fixture.attempts[0].execution_id().to_string();
    usage::revoke_attempt(&mut fixture.controller, &execution, TimestampMs::new(10)).unwrap();
    let ack = use_ack(&mut fixture, 0);
    admit_use_ack(&mut fixture, 0, &ack).unwrap();
    fixture.apply_outcomes();
    assert_eq!(
        fixture.task_lifecycle(0),
        TaskExecutionLifecycle::AwaitingSatisfaction
    );
    context_ack(&mut fixture, 0);
    fixture.apply_outcomes();
    assert_eq!(fixture.task_lifecycle(0), TaskExecutionLifecycle::Completed);
    assert_eq!(fixture.task_lifecycle(1), TaskExecutionLifecycle::Active);
    assert!(!fixture.controller.memory_use.records[fixture.attempts[1].execution_id()].revoked);
    assert!(fixture.controller.safety_stops.is_empty());
    assert!(usage::blocks_release(
        &fixture.controller,
        fixture.attempts[0].command().mission_id()
    ));
}

/// A provider name or aggregate status cannot substitute another original backend or omit one.
#[test]
fn revoke_receipt_rejects_replacement_backend_missing_and_duplicate_provider() {
    let mut fixture = ParallelMission::new();
    retain_memory(&mut fixture, 0, 2);
    fixture.fact(0, 1, ExecutionPhase::Completed);
    let execution = fixture.attempts[0].execution_id().to_string();
    usage::revoke_attempt(&mut fixture.controller, &execution, TimestampMs::new(10)).unwrap();
    let ack = use_ack(&mut fixture, 0);
    for changed in ["instance", "contract", "missing", "duplicate"] {
        let mut invalid = ack.clone();
        match changed {
            "instance" => invalid.providers[0].provider_instance_id = "replacement".into(),
            "contract" => {
                invalid.providers[0].provider_contract_digest = format!("sha256:{}", "f".repeat(64))
            }
            "missing" => {
                invalid.providers.pop();
            }
            _ => invalid.providers.push(invalid.providers[0].clone()),
        }
        let before = serde_json::to_value(&fixture.controller.memory_use).unwrap();
        assert!(
            admit_use_ack(&mut fixture, 0, &invalid).is_err(),
            "{changed}"
        );
        assert_eq!(
            before,
            serde_json::to_value(&fixture.controller.memory_use).unwrap()
        );
    }
    admit_use_ack(&mut fixture, 0, &ack).unwrap();
    context_ack(&mut fixture, 0);
    fixture.apply_outcomes();
    assert_eq!(fixture.task_lifecycle(0), TaskExecutionLifecycle::Completed);
}

/// Whole-Mission cancellation retains both Node owners until every context and provider responds.
#[test]
fn cancellation_waits_for_both_nodes_and_all_original_providers() {
    let mut fixture = ParallelMission::new_with_verifier(true);
    retain_memory(&mut fixture, 0, 2);
    retain_memory(&mut fixture, 1, 1);
    fixture.fact(0, 1, ExecutionPhase::Completed);
    fixture.fact(1, 1, ExecutionPhase::Completed);
    let mission = fixture.attempts[0].command().mission_id().clone();
    fixture
        .controller
        .orchestrator
        .request_cancel(
            &mission,
            fixture.controller.bridge.control_mut(),
            TimestampMs::new(10),
            &fixture.correlation,
            &mut fixture.events.clone(),
        )
        .unwrap();
    apply_pending_cancellations(
        &mut fixture.controller,
        TimestampMs::new(10),
        &fixture.correlation,
        &mut fixture.events.clone(),
    )
    .unwrap();
    context_ack(&mut fixture, 0);
    let ack = use_ack(&mut fixture, 0);
    admit_use_ack(&mut fixture, 0, &ack).unwrap();
    apply_pending_cancellations(
        &mut fixture.controller,
        TimestampMs::new(11),
        &fixture.correlation,
        &mut fixture.events.clone(),
    )
    .unwrap();
    assert_eq!(
        fixture
            .controller
            .orchestrator
            .execution(&mission)
            .unwrap()
            .lifecycle(),
        orchestration::MissionExecutionLifecycle::Cancelling
    );
    context_ack(&mut fixture, 1);
    let ack = use_ack(&mut fixture, 1);
    admit_use_ack(&mut fixture, 1, &ack).unwrap();
    apply_pending_cancellations(
        &mut fixture.controller,
        TimestampMs::new(11),
        &fixture.correlation,
        &mut fixture.events.clone(),
    )
    .unwrap();
    assert_eq!(
        fixture
            .controller
            .orchestrator
            .execution(&mission)
            .unwrap()
            .lifecycle(),
        orchestration::MissionExecutionLifecycle::Cancelled
    );
    assert_eq!(
        fixture
            .controller
            .bridge
            .control()
            .group(&fixture.group_id)
            .unwrap()
            .lifecycle(),
        control::GroupLifecycle::Released
    );
}

/// Prior use journals migrate conservatively; older wrappers cannot hide current permission.
#[test]
fn prior_wrapper_preserves_original_memory_history_and_restart_fences() {
    let mut fixture = ParallelMission::new();
    retain_memory(&mut fixture, 0, 2);
    let original = fixture.controller.memory_preparation.records.clone();
    let mut saved: ServerCheckpoint =
        serde_json::from_str(&server_checkpoint_json(&fixture.controller).unwrap()).unwrap();
    for schema in [
        PRE_ARCHIVE_ABORT_SERVER_CHECKPOINT_SCHEMA,
        PRE_CATALOG_ADOPTION_SERVER_CHECKPOINT_SCHEMA,
        PRE_CATALOG_ARCHIVE_SERVER_CHECKPOINT_SCHEMA,
        PRE_ARTIFACT_REFERENCE_SERVER_CHECKPOINT_SCHEMA,
        PRE_QUOTA_POLICY_SERVER_CHECKPOINT_SCHEMA,
        PRE_RESOURCE_COMMIT_SERVER_CHECKPOINT_SCHEMA,
    ] {
        saved.schema = schema.into();
        validate_isolation_checkpoint_schema(&saved).unwrap();
    }
    saved.schema = PRE_MEMORY_RELEASE_SERVER_CHECKPOINT_SCHEMA.into();
    validate_isolation_checkpoint_schema(&saved).unwrap();
    saved.schema = PRE_MEMORY_USE_SERVER_CHECKPOINT_SCHEMA.into();
    assert!(validate_isolation_checkpoint_schema(&saved).is_err());
    assert!(usage::restore(&mut fixture.controller, TimestampMs::new(12)).unwrap());
    assert_eq!(original, fixture.controller.memory_preparation.records);
    let execution = fixture.attempts[0].execution_id();
    let record = &fixture.controller.memory_use.records[execution];
    assert!(record.revoked);
    assert!(usage::blocks_attempt_release(
        &fixture.controller,
        execution
    ));
    assert_eq!(
        record
            .controls
            .values()
            .find(|item| item.command.operation == MemoryUseOperation::Grant)
            .unwrap()
            .issued_at_ms,
        3
    );
    assert!(!usage::restore(&mut fixture.controller, TimestampMs::new(13)).unwrap());
}
