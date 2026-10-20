//! Durable prerequisite outbox; Prepared and Imported never imply current backend availability.

use crate::*;
use domain::{
    MemoryControlCommand, MemoryControlKind, MemoryControlReceipt, MemoryControlStatus,
    MemoryPreparation,
};
use sha2::{Digest as _, Sha256};

/// Original admission window shared by preparation/import/probe, without restart renewal.
const PREPARATION_WINDOW_MS: u64 = domain::MEMORY_PREPARATION_MAX_BUDGET_MS;
/// Maximum observed-content age before a pending Execute needs another once-only query.
const AVAILABILITY_WINDOW_MS: u64 = 10_000;

/// Memory-owned durable state; the catalog field is a read-only federation port, never checkpoint data.
#[derive(Clone, Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct MemoryPreparationState {
    /// Existing event-backed metadata projection, reacquired from deployment at startup.
    #[serde(skip)]
    pub(crate) catalog: Option<artifact_http::ArtifactCatalog>,
    /// Exact attempt to original context and all once-prepared deliveries/receipts.
    pub(crate) records: BTreeMap<String, MemoryPreparationRecord>,
}

/// Explicit phases separate source deferral, durable admission, effects, observations and revocation.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum MemoryPreparationPhase {
    /// Immutable source/policy is not yet admissible; Execute remains fenced.
    WaitingSource,
    /// Exact original context is awaiting a durable Node receipt.
    Preparing,
    /// Only the admitted original input imports may run.
    Importing,
    /// A once-admitted original-backend observation is outstanding.
    Probing,
    /// Complete current observations allow only the original pending Execute.
    Ready,
    /// Current permission ended; retain ownership while fencing the Node.
    Revoking,
    /// Node durably acknowledged the context fence, independently of physical stop.
    Revoked,
    /// Original ambiguity/budget requires explicit recovery; no automatic replacement identity.
    Unknown,
}

/// Original logical purpose plus immutable physical context, bounded commands and historical facts.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct MemoryPreparationRecord {
    /// Current physical attempt at original admission, never substituted after recovery.
    pub(crate) execution_id: String,
    /// Original receive time before any preparation or provider effects.
    pub(crate) admitted_at_ms: u64,
    /// Original exclusive Controller deadline; replay cannot renew it.
    pub(crate) deadline_ms: u64,
    /// Actual readiness phase, independently of Runtime execution lifecycle.
    pub(crate) phase: MemoryPreparationPhase,
    /// Complete immutable metadata only after Control admits every source/receiver.
    pub(crate) preparation: Option<MemoryPreparation>,
    /// Original Node-observed operation/provider configuration from the first durable admission.
    pub(crate) configuration_digest: Option<String>,
    /// Original command IDs and immutable first-delivery/receipt evidence.
    pub(crate) controls: BTreeMap<String, MemoryControlDelivery>,
    /// Highest reserved once-only query generation, including interrupted queries.
    pub(crate) probe_generation: u64,
    /// Exclusive Controller freshness bound anchored before sending the original probe.
    pub(crate) available_until_ms: Option<u64>,
    /// Bounded operator diagnostic, never parsed to grant permission.
    pub(crate) reason: String,
}

/// Durable command intent before send, retaining its exact first issue time and receive evidence.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct MemoryControlDelivery {
    /// Exact original immutable command; retries never replace its context or generation.
    pub(crate) command: MemoryControlCommand,
    /// First Controller authorization time, retained through retry and restore.
    pub(crate) issued_at_ms: u64,
    /// Last prepared send time; changed only before checkpoint Commit.
    pub(crate) last_delivery_ms: Option<u64>,
    /// Every management identity has a bounded delivery budget; Node journal deduplication keeps
    /// retries from repeating an admitted backend operation.
    pub(crate) deliveries: u32,
    /// Original Node outcome; a duplicate cannot change its timestamp or contents.
    pub(crate) receipt: Option<MemoryControlReceipt>,
    /// Original Controller receive time of that outcome.
    pub(crate) received_at_ms: Option<u64>,
}

/// Bounds original diagnostics by bytes while preserving UTF-8 and restorable history.
fn bounded_reason(reason: &str) -> String {
    let mut end = reason.len().min(1024);
    while !reason.is_char_boundary(end) {
        end -= 1;
    }
    reason[..end].into()
}

/// Derives a bounded immutable identity from the existing physical attempt, never a device name.
fn preparation_id(execution: &str) -> String {
    format!("prep-{:x}", Sha256::digest(execution.as_bytes()))
}

/// Creates immutable intent only from current Control/State/Runtime and already admitted catalog data.
fn build(
    controller: &ControllerState,
    intent: &runtime::DispatchIntent,
    now: domain::TimestampMs,
) -> Result<MemoryPreparation, String> {
    let command = &intent.command;
    let control = controller.bridge.control();
    let group = control
        .group(command.group_id())
        .ok_or("Memory prerequisite Group is absent")?;
    let registry = control
        .memory_authorization_registry()
        .ok_or("Memory prerequisite policy is unavailable")?;
    let catalog = controller
        .memory_preparation
        .catalog
        .as_ref()
        .ok_or("Memory source federation is unavailable")?;
    let id = preparation_id(&intent.execution_id);
    let mut inputs = Vec::new();
    for requirement in command.memory_requirements() {
        super::memory_handoff::authorize_input(
            controller,
            &intent.execution_id,
            &requirement.selector,
            now,
        )?;
        let (manifest, typed_map) = catalog
            .memory_source(&requirement.selector)?
            .ok_or("Memory source is not yet admitted")?;
        if !requirement.matches_manifest(&manifest) {
            return Err("Memory source differs from accepted semantic contracts".into());
        }
        let consumer = controller
            .bridge
            .select_memory_provider(
                command.node_id(),
                requirement,
                command.intent().operation(),
                now,
            )
            .map_err(|e| e.to_string())?;
        let content = serde_json::json!([id, requirement.selector, consumer.provider_id()]);
        let import_id = format!(
            "import-{:x}",
            Sha256::digest(serde_json::to_vec(&content).expect("metadata JSON"))
        );
        inputs.push(domain::PreparedMemoryInput {
            requirement: requirement.clone(),
            manifest,
            typed_map,
            consumer,
            import_id,
        });
    }
    let preparation=MemoryPreparation {
        schema:"roboguide.memory-preparation/v0.2".into(),preparation_id:id,
        tenant_id:control.mission_tenant(command.mission_id()).unwrap_or("local").into(),
        group_id:command.group_id().clone(),task_ref:command.task_ref().clone(),role_id:command.role_id().clone(),
        execution_id:intent.execution_id.clone(),node_id:command.node_id().clone(),
        source_session_id:control.current_node_session(command.node_id()).ok_or("Memory receiver has no current session")?.into(),
        membership:group.membership_version().ok_or("Memory Group membership is unavailable")?.clone(),
        registry_id:registry.registry_id.clone(),registry_revision:registry.revision,
        invocation:orchestration::IntegrationRuntimeBridge::<state::SqliteEventLog>::memory_invocation_json(command,&intent.resource_ids).map_err(|e|e.to_string())?,
        resource_ids:intent.resource_ids.iter().map(|id|id.as_str().to_string()).collect(),
        valid_for_ms:PREPARATION_WINDOW_MS,inputs,
    };
    controller
        .bridge
        .validate_memory_preparation(&preparation, now)
        .map_err(|e| e.to_string())?;
    Ok(preparation)
}

/// Checks original budget, current Mission/attempt and exact retained Control permission.
pub(crate) fn authorize(
    controller: &ControllerState,
    record: &MemoryPreparationRecord,
    now: domain::TimestampMs,
) -> Result<(), String> {
    let preparation = record
        .preparation
        .as_ref()
        .ok_or("Memory source context is unavailable")?;
    for input in &preparation.inputs {
        super::memory_handoff::authorize_input(
            controller,
            &preparation.execution_id,
            &input.requirement.selector,
            now,
        )?;
    }
    let mission = controller
        .orchestrator
        .execution(preparation.task_ref.mission_id())
        .ok_or("Memory Mission is absent")?;
    if now.as_millis() >= record.deadline_ms
        || mission.isolation().is_some()
        || mission.is_crash_loop_quarantined()
        || !matches!(
            mission.lifecycle(),
            orchestration::MissionExecutionLifecycle::Accepted
                | orchestration::MissionExecutionLifecycle::Running
        )
    {
        return Err("original Memory purpose expired or its Mission is held/terminal".into());
    }
    controller
        .bridge
        .validate_memory_preparation(preparation, now)
        .map_err(|e| e.to_string())
}

/// Adds one exact command once, retaining its original first issue time on every retry.
fn queue(
    record: &mut MemoryPreparationRecord,
    operation: MemoryControlKind,
    generation: u64,
    now: domain::TimestampMs,
) -> Result<(), String> {
    let preparation = record
        .preparation
        .as_ref()
        .ok_or("Memory context is unavailable")?;
    let command = MemoryControlCommand {
        schema: "roboguide.memory-control/v0.1".into(),
        preparation_id: preparation.preparation_id.clone(),
        preparation_digest: preparation.digest()?,
        operation,
        generation,
        preparation: (operation == MemoryControlKind::Prepare).then(|| preparation.clone()),
    };
    command.validate()?;
    let id = command.command_id();
    match record.controls.get(&id) {
        Some(original) if original.command != command => {
            return Err("original Memory control identity conflicts".into());
        }
        Some(_) => {}
        None => {
            record.controls.insert(
                id,
                MemoryControlDelivery {
                    command,
                    issued_at_ms: now.as_millis(),
                    last_delivery_ms: None,
                    deliveries: 0,
                    receipt: None,
                    received_at_ms: None,
                },
            );
        }
    }
    Ok(())
}

/// Drives original imports only after the Node durably prepared the complete context.
fn admit_inputs(
    controller: &mut ControllerState,
    record: &MemoryPreparationRecord,
    now: domain::TimestampMs,
) -> Result<bool, String> {
    let preparation = record
        .preparation
        .as_ref()
        .ok_or("Memory context is unavailable")?;
    let mut completed = true;
    for input in &preparation.inputs {
        let request = super::memory_import::MemoryImportRequest {
            import_id: input.import_id.clone(),
            group_id: preparation.group_id.clone(),
            task_ref: preparation.task_ref.clone(),
            role_id: preparation.role_id.clone(),
            execution_id: preparation.execution_id.clone(),
            node_id: preparation.node_id.clone(),
            consumer_provider_id: input.consumer.provider_id().into(),
            manifest: input.manifest.clone(),
            deadline_ms: record.deadline_ms,
            evidence_gate: None,
            preparation_id: Some(preparation.preparation_id.clone()),
            typed_map: input.typed_map.clone(),
        };
        if let Some(existing) = controller.memory_imports.get(&input.import_id) {
            if existing.request != request {
                return Err(
                    "original Memory import belongs to another preparation owner or deadline"
                        .into(),
                );
            }
            match existing.phase {
                super::memory_import::ImportPhase::Completed => continue,
                super::memory_import::ImportPhase::Unknown => {
                    super::memory_query::drive_prerequisite(
                        controller,
                        &input.import_id,
                        record.deadline_ms,
                        now,
                    )?;
                    completed = false;
                    continue;
                }
                super::memory_import::ImportPhase::Rejected => {
                    return Err("original Memory import requires readonly reconciliation".into());
                }
                _ => {
                    completed = false;
                    continue;
                }
            }
        }
        super::memory_import::admit(controller, request, now)?;
        controller
            .memory_imports
            .get_mut(&input.import_id)
            .expect("admitted import")
            .authorized_by = Some("control:accepted-memory-prerequisite".into());
        completed = false;
    }
    Ok(completed)
}

/// Reserves the original irreversible context fence without renewing issue time or delivery counts.
fn revoke(
    record: &mut MemoryPreparationRecord,
    why: &str,
    now: domain::TimestampMs,
) -> Result<(), String> {
    if record.preparation.is_some() && record.phase != MemoryPreparationPhase::Revoked {
        record.phase = MemoryPreparationPhase::Revoking;
        record.available_until_ms = None;
        record.reason = bounded_reason(why);
        queue(record, MemoryControlKind::Revoke, 0, now)?;
    }
    Ok(())
}

/// Fences original Group preparations, including imports which never reached a use grant.
pub(crate) fn revoke_group(
    controller: &mut ControllerState,
    group: &domain::ExecutionGroupId,
    why: &str,
    now: domain::TimestampMs,
) -> Result<(), String> {
    for record in controller.memory_preparation.records.values_mut() {
        if record
            .preparation
            .as_ref()
            .is_some_and(|context| &context.group_id == group)
        {
            revoke(record, why, now)?;
        }
    }
    Ok(())
}

/// Fences only the original receiver context of an independently stopped recovery attempt.
pub(crate) fn revoke_attempt(
    controller: &mut ControllerState,
    execution: &str,
    now: domain::TimestampMs,
) -> Result<(), String> {
    if let Some(record) = controller.memory_preparation.records.get_mut(execution) {
        revoke(record, "original attempt recovery requested", now)?;
    }
    Ok(())
}

/// Checks one context without requiring unrelated live peers to relinquish their ownership.
pub(crate) fn blocks_attempt_release(controller: &ControllerState, execution: &str) -> bool {
    controller
        .memory_preparation
        .records
        .get(execution)
        .is_some_and(|record| {
            record.preparation.is_some()
                && !record.controls.values().any(|delivery| {
                    delivery.command.operation == MemoryControlKind::Revoke
                        && delivery.received_at_ms.is_some()
                        && delivery
                            .receipt
                            .as_ref()
                            .is_some_and(|receipt| receipt.status == MemoryControlStatus::Revoked)
                })
        })
}

/// Requires durable context revocation in the selected release scope, separately from use ACKs.
pub(crate) fn blocks_release(
    controller: &ControllerState,
    mission: &domain::MissionId,
    task: Option<&domain::TaskRef>,
) -> bool {
    controller
        .memory_preparation
        .records
        .values()
        .any(|record| {
            let Some(context) = &record.preparation else {
                return false;
            };
            context.task_ref.mission_id() == mission
                && task.is_none_or(|task| task == &context.task_ref)
                && blocks_attempt_release(controller, &record.execution_id)
        })
}

/// Mutates only the private application candidate; every send waits for its checkpoint Commit.
pub(crate) fn prepare(
    controller: &mut ControllerState,
    now: domain::TimestampMs,
) -> Result<(), String> {
    let pending = controller.bridge.pending_dispatch_intents();
    for intent in pending
        .iter()
        .filter(|intent| !intent.command.memory_requirements().is_empty())
    {
        if controller
            .memory_preparation
            .records
            .contains_key(&intent.execution_id)
        {
            continue;
        }
        if controller.memory_preparation.records.len() >= 4096 {
            return Err("Memory prerequisite journal is full".into());
        }
        controller.memory_preparation.records.insert(
            intent.execution_id.clone(),
            MemoryPreparationRecord {
                execution_id: intent.execution_id.clone(),
                admitted_at_ms: now.as_millis(),
                deadline_ms: now
                    .as_millis()
                    .checked_add(PREPARATION_WINDOW_MS)
                    .ok_or("Memory preparation deadline overflow")?,
                phase: MemoryPreparationPhase::WaitingSource,
                preparation: None,
                configuration_digest: None,
                controls: BTreeMap::new(),
                probe_generation: 0,
                available_until_ms: None,
                reason: String::new(),
            },
        );
    }
    let ids = controller
        .memory_preparation
        .records
        .keys()
        .cloned()
        .collect::<Vec<_>>();
    for id in ids {
        let mut record = controller.memory_preparation.records[&id].clone();
        if controller
            .bridge
            .attempt_history()
            .iter()
            .any(|attempt| attempt.execution_id() == id && attempt.status().is_terminal())
        {
            revoke(&mut record, "original execution ended", now)?;
        }
        if record.phase == MemoryPreparationPhase::WaitingSource
            && now.as_millis() < record.deadline_ms
            && let Some(intent) = pending.iter().find(|intent| intent.execution_id == id)
        {
            match build(controller, intent, now) {
                Ok(mut preparation) => {
                    preparation.valid_for_ms =
                        record.deadline_ms.saturating_sub(record.admitted_at_ms);
                    record.preparation = Some(preparation);
                    record.phase = MemoryPreparationPhase::Preparing;
                    queue(&mut record, MemoryControlKind::Prepare, 0, now)?;
                }
                Err(error) => record.reason = bounded_reason(&error),
            }
        }
        if record.phase == MemoryPreparationPhase::Unknown
            && record.preparation.is_some()
            && !record
                .controls
                .values()
                .any(|delivery| delivery.command.operation == MemoryControlKind::Revoke)
        {
            record.phase = MemoryPreparationPhase::Revoking;
            queue(&mut record, MemoryControlKind::Revoke, 0, now)?;
        }
        if !matches!(
            record.phase,
            MemoryPreparationPhase::WaitingSource
                | MemoryPreparationPhase::Revoking
                | MemoryPreparationPhase::Revoked
                | MemoryPreparationPhase::Unknown
        ) && let Err(error) = if record.phase == MemoryPreparationPhase::Ready
            && !pending.iter().any(|intent| intent.execution_id == id)
        {
            record
                .preparation
                .as_ref()
                .ok_or_else(|| "Memory source context is unavailable".to_string())
                .and_then(|preparation| {
                    controller
                        .bridge
                        .validate_memory_use_context(preparation, now)
                        .map_err(|error| error.to_string())
                })
        } else {
            authorize(controller, &record, now)
        } {
            record.reason = bounded_reason(&error);
            record.available_until_ms = None;
            record.phase = MemoryPreparationPhase::Revoking;
            queue(&mut record, MemoryControlKind::Revoke, 0, now)?;
        }
        if record.phase == MemoryPreparationPhase::Revoking
            && let Some(context) = &record.preparation
            && controller.bridge.attempt_history().iter().any(|attempt| {
                attempt.execution_id() == record.execution_id && !attempt.status().is_terminal()
            })
        {
            super::safety_stop::admit_group_stop(controller, &context.group_id, now, 10_000)?;
        }
        if record.phase == MemoryPreparationPhase::Importing {
            match admit_inputs(controller, &record, now) {
                Ok(true) => {
                    record.probe_generation += 1;
                    let generation = record.probe_generation;
                    queue(&mut record, MemoryControlKind::Probe, generation, now)?;
                    record.phase = MemoryPreparationPhase::Probing;
                }
                Ok(false) => {}
                Err(error) => {
                    record.reason = bounded_reason(&error);
                    record.phase = MemoryPreparationPhase::Unknown;
                }
            }
        }
        if record.phase == MemoryPreparationPhase::Ready
            && record
                .available_until_ms
                .is_none_or(|until| now.as_millis() >= until)
            && pending.iter().any(|intent| intent.execution_id == id)
        {
            if record.probe_generation >= 16 {
                record.phase = MemoryPreparationPhase::Unknown;
                record.reason = "original Memory observation budget exhausted".into();
            } else {
                record.probe_generation += 1;
                let generation = record.probe_generation;
                queue(&mut record, MemoryControlKind::Probe, generation, now)?;
                record.phase = MemoryPreparationPhase::Probing;
                record.available_until_ms = None;
            }
        }
        if now.as_millis() >= record.deadline_ms
            && record.phase == MemoryPreparationPhase::WaitingSource
        {
            record.phase = MemoryPreparationPhase::Unknown;
            record.reason = "original Memory source admission window expired".into();
        }
        for delivery in record.controls.values_mut() {
            if delivery.receipt.is_some() {
                continue;
            }
            let active = match delivery.command.operation {
                MemoryControlKind::Prepare => record.phase == MemoryPreparationPhase::Preparing,
                MemoryControlKind::Probe => {
                    record.phase == MemoryPreparationPhase::Probing
                        && delivery.command.generation == record.probe_generation
                }
                MemoryControlKind::Revoke => record.phase == MemoryPreparationPhase::Revoking,
            };
            if active
                && delivery
                    .last_delivery_ms
                    .is_none_or(|last| now.as_millis().saturating_sub(last) >= 1000)
            {
                if delivery.deliveries >= 4 {
                    record.phase = MemoryPreparationPhase::Unknown;
                    record.reason = if delivery.command.operation == MemoryControlKind::Revoke {
                        "original Memory revoke delivery budget exhausted"
                    } else {
                        "original Memory control delivery budget exhausted"
                    }
                    .into();
                    break;
                }
                delivery.last_delivery_ms = Some(now.as_millis());
                delivery.deliveries += 1;
            }
        }
        controller.memory_preparation.records.insert(id, record);
    }
    Ok(())
}

/// Emits only deliveries just prepared in the committed authority snapshot.
pub(crate) fn flush(
    controller: &ControllerState,
    prepared_at: domain::TimestampMs,
    now: domain::TimestampMs,
) {
    for record in controller.memory_preparation.records.values() {
        let Some(preparation) = &record.preparation else {
            continue;
        };
        for delivery in record.controls.values().filter(|delivery| {
            delivery.receipt.is_none() && delivery.last_delivery_ms == Some(prepared_at.as_millis())
        }) {
            let active = match delivery.command.operation {
                MemoryControlKind::Prepare => record.phase == MemoryPreparationPhase::Preparing,
                MemoryControlKind::Probe => {
                    record.phase == MemoryPreparationPhase::Probing
                        && delivery.command.generation == record.probe_generation
                }
                MemoryControlKind::Revoke => record.phase == MemoryPreparationPhase::Revoking,
            };
            if !active {
                continue;
            }
            if delivery.command.operation != MemoryControlKind::Revoke
                && authorize(controller, record, now).is_err()
            {
                continue;
            }
            let remaining = record.deadline_ms.saturating_sub(now.as_millis());
            if let Err(error) = controller.bridge.deliver_memory_control(
                &preparation.node_id,
                &delivery.command,
                remaining,
            ) {
                eprintln!(
                    "original Memory control {} delivery deferred: {error}",
                    delivery.command.command_id()
                );
            }
        }
    }
}

/// Reduces a current exact-session fact before application Commit; duplicate evidence never renews time.
pub(crate) fn receipt(
    controller: &mut ControllerState,
    node: &str,
    session: &str,
    value: &integration::grpc::v0_4::MemoryControlReceipt,
    received: domain::TimestampMs,
    now: domain::TimestampMs,
) -> Result<(), String> {
    let outcome: MemoryControlReceipt =
        serde_json::from_str(&value.receipt_json).map_err(|e| e.to_string())?;
    let id = controller
        .memory_preparation
        .records
        .iter()
        .find(|(_, record)| {
            record
                .preparation
                .as_ref()
                .is_some_and(|p| p.preparation_id == outcome.preparation_id)
        })
        .map(|(id, _)| id.clone())
        .ok_or("Memory receipt has no original context")?;
    let mut record = controller.memory_preparation.records[&id].clone();
    let preparation = record
        .preparation
        .as_ref()
        .ok_or("Memory receipt has no original source")?;
    if preparation.node_id.as_str() != node
        || value.session_id != session
        || controller.bridge.current_node_session(&preparation.node_id) != Some(session)
    {
        return Err("Memory receipt differs from current authenticated Node session".into());
    }
    let delivery = record
        .controls
        .get(&outcome.command_id)
        .ok_or("Memory receipt names a never-issued command")?;
    outcome.validate(&delivery.command, preparation)?;
    if delivery.deliveries == 0
        || delivery.last_delivery_ms.is_none()
        || received.as_millis() < delivery.issued_at_ms
        || received > now
    {
        return Err("Memory receipt predates original delivery authorization".into());
    }
    if let Some(original) = &delivery.receipt {
        return if original == &outcome {
            Ok(())
        } else {
            Err("original Memory receipt conflicts".into())
        };
    }
    let current = authorize(controller, &record, now).is_ok();
    let command = delivery.command.clone();
    let issued_at = delivery.issued_at_ms;
    if matches!(
        outcome.status,
        MemoryControlStatus::Prepared | MemoryControlStatus::Available
    ) {
        if record
            .configuration_digest
            .as_ref()
            .is_some_and(|digest| outcome.configuration_digest.as_ref() != Some(digest))
        {
            return Err("original Node preparation configuration changed".into());
        }
        if record.configuration_digest.is_none() {
            record.configuration_digest = outcome.configuration_digest.clone();
        }
    }
    let delivery = record
        .controls
        .get_mut(&outcome.command_id)
        .expect("retained command");
    delivery.receipt = Some(outcome.clone());
    delivery.received_at_ms = Some(received.as_millis());
    if command.operation == MemoryControlKind::Revoke
        && outcome.status == MemoryControlStatus::Revoked
    {
        record.phase = MemoryPreparationPhase::Revoked;
        record.available_until_ms = None;
    } else if current {
        match outcome.status {
            MemoryControlStatus::Prepared if record.phase == MemoryPreparationPhase::Preparing => {
                record.phase = MemoryPreparationPhase::Importing
            }
            MemoryControlStatus::Available
                if record.phase == MemoryPreparationPhase::Probing
                    && command.generation == record.probe_generation =>
            {
                let until = issued_at
                    .saturating_add(AVAILABILITY_WINDOW_MS.min(outcome.remaining_budget_ms))
                    .min(record.deadline_ms);
                if now.as_millis() < until {
                    record.available_until_ms = Some(until);
                    record.phase = MemoryPreparationPhase::Ready;
                } else {
                    record.phase = MemoryPreparationPhase::Unknown;
                    record.reason="original availability observation arrived after its issue-bound freshness window".into();
                }
            }
            MemoryControlStatus::Unavailable | MemoryControlStatus::Rejected => {
                record.phase = MemoryPreparationPhase::Unknown;
                record.reason = outcome.reason.clone();
            }
            _ => {}
        }
    }
    controller.memory_preparation.records.insert(id, record);
    Ok(())
}

/// Exposes only exact committed live observations to Runtime's final per-Group dispatch barrier.
pub(crate) fn ready(
    controller: &ControllerState,
    now: domain::TimestampMs,
) -> BTreeMap<String, orchestration::MemoryDispatchPermit> {
    controller
        .memory_preparation
        .records
        .iter()
        .filter(|(_, record)| {
            record.phase == MemoryPreparationPhase::Ready
                && record
                    .available_until_ms
                    .is_some_and(|until| now.as_millis() < until)
                && authorize(controller, record, now).is_ok()
                && super::memory_use::permits(controller, &record.execution_id, now)
        })
        .filter_map(|(id, record)| {
            let expires_at_ms = record
                .deadline_ms
                .min(record.available_until_ms?)
                .min(super::memory_use::dispatch_expires_at(controller, id, now)?);
            record.preparation.clone().map(|preparation| {
                (
                    id.clone(),
                    orchestration::MemoryDispatchPermit {
                        preparation,
                        expires_at: domain::TimestampMs::new(expires_at_ms),
                    },
                )
            })
        })
        .collect()
}

/// Binds data-plane imports to the original prepared source, receiver and durable admission.
pub(crate) fn authorize_import(
    controller: &ControllerState,
    request: &super::memory_import::MemoryImportRequest,
    now: domain::TimestampMs,
) -> Result<super::memory_import::MemoryAuthorityStamp, String> {
    if request.evidence_gate.is_some() {
        return Err("prepared imports retain their original accepted readiness policy".into());
    }
    let record = controller
        .memory_preparation
        .records
        .get(&request.execution_id)
        .ok_or("import has no original prepared purpose")?;
    authorize(controller, record, now)?;
    let preparation = record
        .preparation
        .as_ref()
        .ok_or("import source context is unavailable")?;
    if request.preparation_id.as_deref() != Some(preparation.preparation_id.as_str())
        || !matches!(
            record.phase,
            MemoryPreparationPhase::Importing
                | MemoryPreparationPhase::Probing
                | MemoryPreparationPhase::Ready
        )
        || record.configuration_digest.is_none()
    {
        return Err("import lacks durable original Node preparation acceptance".into());
    }
    let input = preparation
        .inputs
        .iter()
        .find(|input| input.import_id == request.import_id)
        .ok_or("import is not an original prepared input")?;
    if request.node_id != preparation.node_id
        || request.group_id != preparation.group_id
        || request.task_ref != preparation.task_ref
        || request.role_id != preparation.role_id
        || request.deadline_ms != record.deadline_ms
        || request.consumer_provider_id != input.consumer.provider_id()
        || request.manifest != input.manifest
        || request.typed_map != input.typed_map
    {
        return Err("import replaced original scope, input or consumer metadata".into());
    }
    Ok(super::memory_import::MemoryAuthorityStamp {
        tenant_id: preparation.tenant_id.clone(),
        consumer: input.consumer.clone(),
        registry_id: preparation.registry_id.clone(),
        revision: preparation.registry_revision,
        membership: preparation.membership.clone(),
    })
}

/// Validates original history and fences process-interrupted grants before any restored delivery.
pub(crate) fn restore(
    controller: &mut ControllerState,
    now: domain::TimestampMs,
) -> Result<bool, String> {
    if controller.memory_preparation.records.len() > 4096
        || serde_json::to_vec(&controller.memory_preparation)
            .map_err(|error| error.to_string())?
            .len()
            > 128 * 1024 * 1024
    {
        return Err("restored Memory prerequisite history exceeds its bound".into());
    }
    let mut changed = false;
    for (id, record) in &mut controller.memory_preparation.records {
        if id != &record.execution_id
            || id.is_empty()
            || id.len() > 1024
            || record.deadline_ms.checked_sub(record.admitted_at_ms) != Some(PREPARATION_WINDOW_MS)
            || record.probe_generation > 16
            || record.controls.len() > 18
            || record.reason.len() > 1024
            || record
                .available_until_ms
                .is_some_and(|until| until > record.deadline_ms)
        {
            return Err("restored Memory purpose identity, budget or history is invalid".into());
        }
        if let Some(preparation) = &record.preparation {
            preparation.validate()?;
            if preparation.execution_id != *id
                || preparation.preparation_id != preparation_id(id)
                || preparation.valid_for_ms != PREPARATION_WINDOW_MS
            {
                return Err("restored Memory context differs from its original attempt".into());
            }
            for (command_id, delivery) in &record.controls {
                delivery.command.validate()?;
                if command_id != &delivery.command.command_id()
                    || delivery.command.preparation_id != preparation.preparation_id
                    || delivery.command.preparation_digest != preparation.digest()?
                    || delivery
                        .command
                        .preparation
                        .as_ref()
                        .is_some_and(|value| value != preparation)
                    || delivery.command.generation > record.probe_generation
                    || delivery.deliveries > 4
                    || (delivery.deliveries == 0) != delivery.last_delivery_ms.is_none()
                    || delivery.issued_at_ms < record.admitted_at_ms
                    || delivery
                        .last_delivery_ms
                        .is_some_and(|last| last < delivery.issued_at_ms)
                    || delivery.receipt.is_some() != delivery.received_at_ms.is_some()
                    || delivery
                        .received_at_ms
                        .is_some_and(|time| time < delivery.issued_at_ms)
                {
                    return Err(
                        "restored Memory control history replaced original authority".into(),
                    );
                }
                if let Some(receipt) = &delivery.receipt {
                    receipt.validate(&delivery.command, preparation)?;
                    if matches!(
                        receipt.status,
                        MemoryControlStatus::Prepared | MemoryControlStatus::Available
                    ) && receipt.configuration_digest != record.configuration_digest
                    {
                        return Err("restored Memory Node configuration evidence conflicts".into());
                    }
                }
            }
        } else if !record.controls.is_empty()
            || record.configuration_digest.is_some()
            || record.available_until_ms.is_some()
            || !matches!(
                record.phase,
                MemoryPreparationPhase::WaitingSource | MemoryPreparationPhase::Unknown
            )
        {
            return Err("restored Memory authority has no original source context".into());
        }
        if !matches!(
            record.phase,
            MemoryPreparationPhase::Revoked
                | MemoryPreparationPhase::Revoking
                | MemoryPreparationPhase::Unknown
        ) {
            record.available_until_ms = None;
            record.reason = "Controller restart fenced original preparation; budgets and imports remain unchanged".into();
            if record.preparation.is_some() {
                record.phase = MemoryPreparationPhase::Revoking;
                queue(record, MemoryControlKind::Revoke, 0, now)?;
            } else {
                record.phase = MemoryPreparationPhase::Unknown;
            }
            changed = true;
        }
    }
    Ok(changed)
}
