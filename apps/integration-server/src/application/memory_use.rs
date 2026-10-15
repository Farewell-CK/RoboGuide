//! Application-owned current Memory permission; transport receipts never authorize physical release.

use crate::*;
use domain::{
    MemoryPreparation, MemoryUseControl, MemoryUseOperation, MemoryUseReceipt, MemoryUseStatus,
};

/// Four retained generations bound checkpoint growth; persistent heads reject older replay.
const RETAINED_GRANTS: usize = 4;
/// Renew before the original grant expires, without changing preparation/import admission time.
const RENEW_AFTER_MS: u64 = 10_000;

/// Durable exact-attempt use authority; original immutable contexts remain in preparation state.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct MemoryUseState {
    /// Stable attempt identities and irreversible fences, never current registration defaults.
    pub(crate) records: BTreeMap<String, MemoryUseRecord>,
}

/// Bounded command history plus a persistent monotonic grant watermark.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct MemoryUseRecord {
    /// Original physical attempt, independent of a mutable logical role binding.
    pub(crate) execution_id: String,
    /// Highest once-reserved grant generation, including interrupted deliveries.
    pub(crate) generation: u64,
    /// Irreversible local fence; no late positive receipt can clear it.
    pub(crate) revoked: bool,
    /// Current and last three grants plus the original revoke command.
    pub(crate) controls: BTreeMap<String, MemoryUseDelivery>,
    /// Bounded operator evidence, never parsed to decide authority.
    pub(crate) reason: String,
}

/// Original command intent is committed before delivery and never gains time from replay.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct MemoryUseDelivery {
    /// Exact current-policy command at the original issue time.
    pub(crate) command: MemoryUseControl,
    /// Original Controller-local admission time.
    pub(crate) issued_at_ms: u64,
    /// Once-admitted same-identity deliveries, at most four.
    pub(crate) deliveries: u32,
    /// Last durable delivery preparation time.
    pub(crate) last_delivery_ms: Option<u64>,
    /// Original authenticated aggregate provider outcome.
    pub(crate) receipt: Option<MemoryUseReceipt>,
    /// First original receive time, unchanged by duplicates.
    pub(crate) received_at_ms: Option<u64>,
}

/// Retains a byte-bounded UTF-8 diagnostic without turning it into a policy signal.
fn reason(text: &str) -> String {
    let mut end = text.len().min(1024);
    while !text.is_char_boundary(end) {
        end -= 1;
    }
    text[..end].into()
}

/// Checks exact current Mission, Runtime, source session and Read policy without import renewal.
fn authorize(
    controller: &ControllerState,
    preparation: &MemoryPreparation,
    now: domain::TimestampMs,
) -> Result<(), String> {
    let mission = controller
        .orchestrator
        .execution(preparation.task_ref.mission_id())
        .ok_or("Memory use Mission is absent")?;
    if mission.isolation().is_some()
        || mission.is_crash_loop_quarantined()
        || !matches!(
            mission.lifecycle(),
            orchestration::MissionExecutionLifecycle::Accepted
                | orchestration::MissionExecutionLifecycle::Running
        )
    {
        return Err("Memory use Mission is held, cancelling or terminal".into());
    }
    controller
        .bridge
        .validate_memory_use_context(preparation, now)
        .map_err(|e| e.to_string())
}

/// Pending grant delivery and admission must still name the exact issue-time policy/membership.
fn current_stamp(controller: &ControllerState, command: &MemoryUseControl) -> bool {
    let control = controller.bridge.control();
    control
        .memory_authorization_registry()
        .is_some_and(|registry| {
            registry.registry_id == command.registry_id
                && registry.revision == command.registry_revision
        })
        && control
            .group(&command.group_id)
            .and_then(|group| group.membership_version())
            == Some(&command.membership)
}

/// Finds original context/configuration; missing history never becomes current permission.
fn source<'a>(
    controller: &'a ControllerState,
    execution: &str,
) -> Result<
    (
        &'a super::memory_preparation::MemoryPreparationRecord,
        &'a MemoryPreparation,
        &'a str,
    ),
    String,
> {
    let record = controller
        .memory_preparation
        .records
        .get(execution)
        .ok_or("Memory use has no original preparation")?;
    let preparation = record
        .preparation
        .as_ref()
        .ok_or("Memory use source is unavailable")?;
    let configuration = record
        .configuration_digest
        .as_deref()
        .ok_or("Memory use has no original durable configuration receipt")?;
    if preparation
        .inputs
        .iter()
        .any(|input| !input.consumer.supports_use_lease())
    {
        return Err("original Memory provider did not negotiate current-use enforcement".into());
    }
    Ok((record, preparation, configuration))
}

/// Builds a new immutable generation from current Control, retaining the exact original purpose.
fn command(
    controller: &ControllerState,
    execution: &str,
    generation: u64,
    operation: MemoryUseOperation,
) -> Result<MemoryUseControl, String> {
    let (_, preparation, configuration) = source(controller, execution)?;
    let control = controller.bridge.control();
    let registry = control
        .memory_authorization_registry()
        .ok_or("Memory policy is unavailable")?;
    let membership = control
        .group(&preparation.group_id)
        .and_then(|group| group.membership_version())
        .ok_or("Memory membership is unavailable")?;
    let mut command = MemoryUseControl {
        schema: "roboguide.memory-use-control/v0.1".into(),
        command_id: String::new(),
        lease_id: domain::memory_use_lease_id(&preparation.preparation_id),
        generation,
        operation,
        preparation_id: preparation.preparation_id.clone(),
        preparation_digest: preparation.digest()?,
        execution_id: execution.into(),
        node_id: preparation.node_id.clone(),
        source_session_id: preparation.source_session_id.clone(),
        tenant_id: preparation.tenant_id.clone(),
        group_id: preparation.group_id.clone(),
        registry_id: preparation.registry_id.clone(),
        registry_revision: registry.revision,
        membership: membership.clone(),
        configuration_digest: configuration.into(),
        valid_for_ms: if operation == MemoryUseOperation::Grant {
            domain::MEMORY_USE_MAX_BUDGET_MS
        } else {
            0
        },
    };
    command.command_id = command.expected_command_id();
    command.validate_preparation(preparation)?;
    Ok(command)
}

/// Reserves exactly one command before any send and prunes only completed older generations.
fn queue(
    record: &mut MemoryUseRecord,
    command: MemoryUseControl,
    now: domain::TimestampMs,
) -> Result<(), String> {
    command.validate()?;
    if command.execution_id != record.execution_id {
        return Err("Memory use command substitutes its original attempt".into());
    }
    if let Some(original) = record.controls.get(&command.command_id) {
        return if original.command == command {
            Ok(())
        } else {
            Err("Memory use command identity conflicts".into())
        };
    }
    if command.operation == MemoryUseOperation::Grant {
        if record.revoked
            || command.generation
                != record
                    .generation
                    .checked_add(1)
                    .ok_or("Memory use generation overflow")?
        {
            return Err("Memory use grant is revoked or noncontiguous".into());
        }
        record.generation = command.generation;
    } else {
        record.revoked = true;
    }
    record.controls.insert(
        command.command_id.clone(),
        MemoryUseDelivery {
            command,
            issued_at_ms: now.as_millis(),
            deliveries: 0,
            last_delivery_ms: None,
            receipt: None,
            received_at_ms: None,
        },
    );
    let minimum = record.generation.saturating_sub(RETAINED_GRANTS as u64 - 1);
    record.controls.retain(|_, value| {
        value.command.operation == MemoryUseOperation::Revoke || value.command.generation >= minimum
    });
    Ok(())
}

/// Uses issue-bound timing; receipt queuing, retransmission and restart cannot extend a grant.
fn expires(delivery: &MemoryUseDelivery) -> Option<u64> {
    let receipt = delivery.receipt.as_ref()?;
    (receipt.status == MemoryUseStatus::Granted).then(|| {
        delivery.issued_at_ms.saturating_add(
            delivery
                .command
                .valid_for_ms
                .min(receipt.remaining_budget_ms),
        )
    })
}

/// Retains the last accepted interval while its successor is pending, under the same current policy.
fn granted(record: &MemoryUseRecord, now: domain::TimestampMs) -> Option<&MemoryUseDelivery> {
    if record.revoked {
        return None;
    }
    record
        .controls
        .values()
        .filter(|delivery| expires(delivery).is_some_and(|until| now.as_millis() < until))
        .max_by_key(|delivery| delivery.command.generation)
}

/// Requires the acknowledged head for new Execute while retaining older grants for live work.
fn dispatch_grant(record: &MemoryUseRecord, now: domain::TimestampMs) -> bool {
    granted(record, now).is_some_and(|delivery| delivery.command.generation == record.generation)
}

/// Checks the independently committed lease at Runtime's whole-Group final Execute barrier.
pub(crate) fn permits(
    controller: &ControllerState,
    execution: &str,
    now: domain::TimestampMs,
) -> bool {
    controller
        .memory_use
        .records
        .get(execution)
        .is_some_and(|record| dispatch_grant(record, now))
        && source(controller, execution)
            .is_ok_and(|(_, preparation, _)| authorize(controller, preparation, now).is_ok())
}

/// Records an irreversible use fence and invokes existing whole-Group stop/hold for live work.
fn revoke(
    controller: &mut ControllerState,
    record: &mut MemoryUseRecord,
    why: &str,
    now: domain::TimestampMs,
) -> Result<(), String> {
    if !record.revoked {
        let mut fence = record
            .controls
            .values()
            .max_by_key(|delivery| delivery.command.generation)
            .ok_or("Memory use fence has no original purpose")?
            .command
            .clone();
        fence.operation = MemoryUseOperation::Revoke;
        fence.generation = 0;
        fence.valid_for_ms = 0;
        fence.command_id = fence.expected_command_id();
        queue(record, fence, now)?;
        record.reason = reason(why);
    }
    let (_, preparation, _) = source(controller, &record.execution_id)?;
    let group = preparation.group_id.clone();
    let live = controller.bridge.attempt_history().iter().any(|attempt| {
        attempt.execution_id() == record.execution_id && !attempt.status().is_terminal()
    });
    if live {
        super::safety_stop::admit_group_stop(controller, &group, now, 10_000)?;
    }
    Ok(())
}

/// Advances only the private application candidate; all delivery budgets commit with its checkpoint.
pub(crate) fn prepare(
    controller: &mut ControllerState,
    now: domain::TimestampMs,
) -> Result<(), String> {
    let initial = controller
        .memory_preparation
        .records
        .iter()
        .filter(|(id, record)| {
            !controller.memory_use.records.contains_key(*id)
                && record.phase == super::memory_preparation::MemoryPreparationPhase::Ready
                && record
                    .available_until_ms
                    .is_some_and(|until| now.as_millis() < until)
                && super::memory_preparation::authorize(controller, record, now).is_ok()
        })
        .map(|(id, _)| id.clone())
        .collect::<Vec<_>>();
    for id in initial {
        if controller.memory_use.records.len() >= 4096 {
            return Err("Memory use journal is full".into());
        }
        let mut record = MemoryUseRecord {
            execution_id: id.clone(),
            generation: 0,
            revoked: false,
            controls: BTreeMap::new(),
            reason: String::new(),
        };
        let grant = command(controller, &id, 1, MemoryUseOperation::Grant)?;
        queue(&mut record, grant, now)?;
        controller.memory_use.records.insert(id, record);
    }
    let ids = controller
        .memory_use
        .records
        .keys()
        .cloned()
        .collect::<Vec<_>>();
    for id in ids {
        let mut record = controller.memory_use.records[&id].clone();
        let (_, preparation, _) = source(controller, &id)?;
        let ended = controller
            .bridge
            .attempt_history()
            .iter()
            .any(|attempt| attempt.execution_id() == id && attempt.status().is_terminal());
        let authorization = authorize(controller, preparation, now);
        let latest = record
            .controls
            .values()
            .filter(|value| value.command.operation == MemoryUseOperation::Grant)
            .max_by_key(|value| value.command.generation)
            .ok_or("Memory use head has no original grant")?
            .clone();
        let failed = latest
            .receipt
            .as_ref()
            .is_some_and(|receipt| receipt.status != MemoryUseStatus::Granted);
        let expired = now.as_millis()
            >= latest
                .issued_at_ms
                .saturating_add(latest.command.valid_for_ms)
            || (record
                .controls
                .values()
                .any(|delivery| expires(delivery).is_some())
                && granted(&record, now).is_none());
        let stale_pending = latest.receipt.is_none() && !current_stamp(controller, &latest.command);
        if !record.revoked
            && (ended || authorization.is_err() || failed || expired || stale_pending)
        {
            let why = authorization.err().unwrap_or_else(|| {
                if ended {
                    "original execution ended".into()
                } else {
                    "original Memory use is unknown, rejected or expired".into()
                }
            });
            revoke(controller, &mut record, &why, now)?;
        } else if !record.revoked
            && latest.receipt.is_some()
            && now.as_millis().saturating_sub(latest.issued_at_ms) >= RENEW_AFTER_MS
        {
            let next = command(
                controller,
                &id,
                record
                    .generation
                    .checked_add(1)
                    .ok_or("Memory use generation overflow")?,
                MemoryUseOperation::Grant,
            )?;
            queue(&mut record, next, now)?;
        }
        let mut exhausted = false;
        for delivery in record.controls.values_mut() {
            let active = if record.revoked {
                delivery.command.operation == MemoryUseOperation::Revoke
            } else {
                delivery.command.operation == MemoryUseOperation::Grant
                    && delivery.command.generation == record.generation
            };
            if active
                && delivery.receipt.is_none()
                && delivery
                    .last_delivery_ms
                    .is_none_or(|last| now.as_millis().saturating_sub(last) >= 1000)
            {
                if delivery.deliveries >= 4 {
                    exhausted = !record.revoked;
                    break;
                }
                delivery.deliveries += 1;
                delivery.last_delivery_ms = Some(now.as_millis());
            }
        }
        if exhausted {
            revoke(
                controller,
                &mut record,
                "original Memory use delivery budget exhausted",
                now,
            )?;
        }
        controller.memory_use.records.insert(id, record);
    }
    Ok(())
}

/// Sends only the just-committed intents; queue time consumes the original issue-bound budget.
pub(crate) fn flush(
    controller: &ControllerState,
    prepared_at: domain::TimestampMs,
    now: domain::TimestampMs,
) {
    for record in controller.memory_use.records.values() {
        let Ok((_, preparation, _)) = source(controller, &record.execution_id) else {
            continue;
        };
        for delivery in record.controls.values().filter(|value| {
            value.receipt.is_none() && value.last_delivery_ms == Some(prepared_at.as_millis())
        }) {
            let grant = delivery.command.operation == MemoryUseOperation::Grant;
            if grant
                && (record.revoked
                    || delivery.command.generation != record.generation
                    || !current_stamp(controller, &delivery.command)
                    || authorize(controller, preparation, now).is_err())
            {
                continue;
            }
            let remaining = if grant {
                delivery
                    .issued_at_ms
                    .saturating_add(delivery.command.valid_for_ms)
                    .saturating_sub(now.as_millis())
            } else {
                0
            };
            if grant && remaining == 0 {
                continue;
            }
            if let Err(error) = controller.bridge.deliver_memory_use_control(
                &preparation.node_id,
                &delivery.command,
                remaining,
            ) {
                eprintln!(
                    "original Memory use {} delivery deferred: {error}",
                    delivery.command.command_id
                );
            }
        }
    }
}

/// Pins provider proofs to the actual original availability evidence, independently of current names.
fn validate_providers(
    controller: &ControllerState,
    execution: &str,
    outcome: &MemoryUseReceipt,
) -> Result<(), String> {
    let (original, _, _) = source(controller, execution)?;
    for provider in &outcome.providers {
        let expected = original
            .controls
            .values()
            .filter_map(|delivery| delivery.receipt.as_ref())
            .filter(|receipt| receipt.status == domain::MemoryControlStatus::Available)
            .flat_map(|receipt| &receipt.observations)
            .filter(|observation| provider.import_ids.contains(&observation.import_id))
            .map(|observation| (observation.import_id.as_str(), observation))
            .collect::<BTreeMap<_, _>>();
        if outcome.status == MemoryUseStatus::Granted
            && (expected.len() != provider.import_ids.len()
                || expected.values().any(|observation| {
                    observation.provider_instance_id != provider.provider_instance_id
                        || observation.provider_contract_digest != provider.provider_contract_digest
                }))
        {
            return Err("Memory use proof changed its original backend or configuration".into());
        }
    }
    Ok(())
}

/// Reduces exact authenticated original evidence; late positives remain history behind a revoke fence.
pub(crate) fn receipt(
    controller: &mut ControllerState,
    node: &str,
    session: &str,
    value: &integration::grpc::v0_4::MemoryUseReceipt,
    received: domain::TimestampMs,
    now: domain::TimestampMs,
) -> Result<(), String> {
    let outcome: MemoryUseReceipt =
        serde_json::from_str(&value.receipt_json).map_err(|e| e.to_string())?;
    let id = controller
        .memory_use
        .records
        .iter()
        .find(|(_, record)| record.controls.contains_key(&outcome.command_id))
        .map(|(id, _)| id.clone())
        .ok_or("Memory use receipt has no retained original command")?;
    let (_, preparation, _) = source(controller, &id)?;
    if preparation.node_id.as_str() != node
        || value.session_id != session
        || controller.bridge.current_node_session(&preparation.node_id) != Some(session)
    {
        return Err(
            "Memory use receipt differs from the current authenticated Node session".into(),
        );
    }
    let record = &controller.memory_use.records[&id];
    let delivery = &record.controls[&outcome.command_id];
    outcome.validate(&delivery.command, preparation)?;
    validate_providers(controller, &id, &outcome)?;
    if delivery.deliveries == 0 || received.as_millis() < delivery.issued_at_ms || received > now {
        return Err("Memory use receipt predates its committed delivery intent".into());
    }
    if let Some(original) = &delivery.receipt {
        return if original == &outcome {
            Ok(())
        } else {
            Err("original Memory use receipt conflicts".into())
        };
    }
    let fence = !record.revoked
        && outcome.generation == record.generation
        && outcome.status == MemoryUseStatus::Granted
        && (!current_stamp(controller, &delivery.command)
            || authorize(controller, preparation, now).is_err());
    let mut record = controller.memory_use.records[&id].clone();
    let delivery = record
        .controls
        .get_mut(&outcome.command_id)
        .expect("retained original command");
    delivery.receipt = Some(outcome);
    delivery.received_at_ms = Some(received.as_millis());
    if fence {
        revoke(
            controller,
            &mut record,
            "original Memory-use receipt arrived after policy, membership or purpose changed",
            now,
        )?;
    }
    controller.memory_use.records.insert(id, record);
    Ok(())
}

/// Validates complete original history then fences interrupted permission before any startup outbox.
pub(crate) fn restore(
    controller: &mut ControllerState,
    now: domain::TimestampMs,
) -> Result<bool, String> {
    if controller.memory_use.records.len() > 4096
        || serde_json::to_vec(&controller.memory_use)
            .map_err(|e| e.to_string())?
            .len()
            > 128 * 1024 * 1024
    {
        return Err("Memory use checkpoint exceeds its bound".into());
    }
    let mut changed = false;
    let ids = controller
        .memory_use
        .records
        .keys()
        .cloned()
        .collect::<Vec<_>>();
    for id in ids {
        let mut record = controller.memory_use.records[&id].clone();
        let (_, preparation, configuration) = source(controller, &id)?;
        if id != record.execution_id
            || record.generation == 0
            || record.generation > i64::MAX as u64
            || record.controls.len() > RETAINED_GRANTS + 1
            || record.reason.len() > 1024
        {
            return Err("Memory use checkpoint identity or history is invalid".into());
        }
        let mut generations = BTreeSet::new();
        let mut has_revoke = false;
        for (key, delivery) in &record.controls {
            delivery.command.validate_preparation(preparation)?;
            if key != &delivery.command.command_id
                || delivery.command.configuration_digest != configuration
                || delivery.deliveries > 4
                || (delivery.deliveries == 0) != delivery.last_delivery_ms.is_none()
                || delivery
                    .last_delivery_ms
                    .is_some_and(|time| time < delivery.issued_at_ms)
                || delivery.receipt.is_some() != delivery.received_at_ms.is_some()
                || delivery
                    .received_at_ms
                    .is_some_and(|time| time < delivery.issued_at_ms)
                || (delivery.receipt.is_some() && delivery.deliveries == 0)
            {
                return Err("Memory use checkpoint replaced its original command or timing".into());
            }
            if delivery.command.operation == MemoryUseOperation::Grant {
                if delivery.command.valid_for_ms != domain::MEMORY_USE_MAX_BUDGET_MS
                    || !generations.insert(delivery.command.generation)
                {
                    return Err(
                        "Memory use checkpoint changed its grant budget or generation".into(),
                    );
                }
            } else {
                has_revoke = true;
            }
            if let Some(outcome) = &delivery.receipt {
                outcome.validate(&delivery.command, preparation)?;
                validate_providers(controller, &id, outcome)?;
            }
        }
        let minimum = record
            .generation
            .saturating_sub(RETAINED_GRANTS as u64 - 1)
            .max(1);
        if generations != (minimum..=record.generation).collect() || record.revoked != has_revoke {
            return Err(
                "Memory use checkpoint lost its contiguous watermark or revoke fence".into(),
            );
        }
        if !record.revoked {
            revoke(
                controller,
                &mut record,
                "Controller restart fenced original Memory use; imports and budgets are unchanged",
                now,
            )?;
            changed = true;
        }
        controller.memory_use.records.insert(id, record);
    }
    Ok(changed)
}

#[cfg(test)]
#[path = "memory_use_tests.rs"]
mod tests;
