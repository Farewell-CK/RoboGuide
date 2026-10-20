//! Bounded explicit recovery of original revoke results; no content permission or effect is issued.

use super::memory_use::{self, MemoryUseDelivery};
use crate::*;
use domain::{MemoryUseQuery, MemoryUseQueryReceipt, MemoryUseQueryStatus};
use ports::EventSink;
use sha2::Digest as _;

/// A request-local continuation of the actual ingress clock while its SQL worker waits.
pub(crate) struct AdmissionClock {
    /// Original actual Controller observation.
    timestamp: domain::TimestampMs,
    /// Process-local elapsed time prevents queued work from receiving a renewed budget.
    started: std::time::Instant,
}
impl AdmissionClock {
    /// Anchors a worker to the already observed Controller clock.
    pub(crate) fn new(timestamp: domain::TimestampMs) -> Self {
        Self {
            timestamp,
            started: std::time::Instant::now(),
        }
    }
}
impl Clock for AdmissionClock {
    /// Advances only by actual local elapsed time, independent of wall-clock adjustments.
    fn now(&self) -> domain::TimestampMs {
        domain::TimestampMs::new(
            self.timestamp.as_millis().saturating_add(
                u64::try_from(self.started.elapsed().as_millis()).unwrap_or(u64::MAX),
            ),
        )
    }
}

/// Immutable HTTP request, stable-principal retries and original-generation admission.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct UseQueryRequest {
    /// Original retained revoke command identity.
    command_id: String,
    /// Last admitted query ordinal; zero opens the single observation session.
    expected_generation: u64,
    /// Selected original providers in canonical order.
    provider_ids: Vec<String>,
    /// Fixed independent Controller-local session deadline.
    deadline_ms: u64,
    /// Bounded single-query timeout, never a permission budget.
    query_timeout_ms: u64,
}

/// One independent session per original revoke; expiry never permits a replacement session.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct UseReconciliation {
    /// Original Controller opening time.
    opened_at_ms: u64,
    /// Frozen deadline at most five minutes after opening.
    deadline_ms: u64,
    /// Immutable bounded query history, including interrupted deliveries.
    queries: BTreeMap<u64, UseQueryIntent>,
}

/// Original authority and observation; initial Unknown remains in the parent delivery.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct UseQueryIntent {
    /// Exact original HTTP request.
    request: UseQueryRequest,
    /// Certificate-derived stable operator identity.
    principal: String,
    /// Exact immutable negotiated query.
    query: MemoryUseQuery,
    /// Original admission time, used for send and receipt freshness.
    issued_at_ms: u64,
    /// Once-only post-Commit delivery preparation; absence is not proof of transport delivery.
    sent_at_ms: Option<u64>,
    /// Original authenticated first observation.
    receipt: Option<MemoryUseQueryReceipt>,
    /// Original Controller receive time.
    received_at_ms: Option<u64>,
    /// Whether the original owner/attempt remained current at first admission of evidence.
    current_authority_at_receipt: bool,
}

/// Resolves a retained original revoke without accepting an invented command identity.
fn locate(controller: &ControllerState, command: &str) -> Result<String, String> {
    controller
        .memory_use
        .records
        .iter()
        .find(|(_, record)| {
            record
                .controls
                .get(command)
                .is_some_and(|d| d.command.operation == domain::MemoryUseOperation::Revoke)
        })
        .map(|(id, _)| id.clone())
        .ok_or_else(|| "original Memory revoke is absent".into())
}

/// Current physical ownership is separate from revoked/expired content Read policy and Node sessions.
fn current_owner(
    controller: &ControllerState,
    delivery: &MemoryUseDelivery,
    preparation: &domain::MemoryPreparation,
) -> bool {
    controller.bridge.current_task_attempt_id(
        &preparation.group_id,
        &preparation.task_ref,
        &preparation.role_id,
    ) == Some(preparation.execution_id.as_str())
        && controller
            .bridge
            .control()
            .group(&preparation.group_id)
            .and_then(|group| group.membership_version())
            == Some(&delivery.command.membership)
        && controller
            .bridge
            .control()
            .mission_tenant(preparation.task_ref.mission_id())
            == Some(preparation.tenant_id.as_str())
}

/// Rechecks the target's immutable tenant inside the actual authority transaction.
fn tenant_allowed(
    controller: &ControllerState,
    id: &str,
    tenant: Option<&str>,
) -> Result<(), String> {
    let (_, preparation, _) = memory_use::source(controller, id)?;
    if tenant.is_some_and(|tenant| tenant != preparation.tenant_id) {
        return Err("Memory revoke belongs to another tenant".into());
    }
    Ok(())
}

/// Adds one immutable generation; exact retries return the original outcome even after expiry.
pub(crate) fn admit(
    controller: &mut ControllerState,
    request: UseQueryRequest,
    principal: &str,
    tenant: Option<&str>,
    now: domain::TimestampMs,
) -> Result<(String, u64, bool), String> {
    let id = locate(controller, &request.command_id)?;
    tenant_allowed(controller, &id, tenant)?;
    let generation = request
        .expected_generation
        .checked_add(1)
        .ok_or("readonly query generation overflow")?;
    let delivery = &controller.memory_use.records[&id].controls[&request.command_id];
    if let Some(old) = delivery
        .reconciliation
        .as_ref()
        .and_then(|session| session.queries.get(&generation))
    {
        return if old.request == request && old.principal == principal {
            Ok((id, generation, false))
        } else {
            Err("readonly query identity conflicts with original authorization".into())
        };
    }
    let (_, preparation, _) = memory_use::source(controller, &id)?;
    if principal.trim().is_empty()
        || principal.len() > 256
        || request.deadline_ms <= now.as_millis()
        || request.deadline_ms - now.as_millis() > 300_000
        || !(1..=30_000).contains(&request.query_timeout_ms)
        || !(1..=16).contains(&generation)
        || !current_owner(controller, delivery, preparation)
        || !controller.memory_use.records[&id].revoked
    {
        return Err(
            "readonly use query requires original current ownership and bounded authority/time"
                .into(),
        );
    }
    let node = controller
        .bridge
        .state()
        .node(&preparation.node_id)
        .ok_or("original Node is unavailable")?;
    if !integration::grpc::v0_4::supports_memory_use_query(
        node.registration().contract_version().as_str(),
    ) {
        return Err(
            "original Node did not negotiate independent readonly Memory use queries".into(),
        );
    }
    let (opened, deadline) = if let Some(session) = &delivery.reconciliation {
        if session.deadline_ms != request.deadline_ms
            || session.queries.len() as u64 != request.expected_generation
            || now.as_millis() < session.opened_at_ms
        {
            return Err("original readonly use session cannot be replaced or extended".into());
        }
        if session.queries.values().any(|intent| {
            intent.receipt.is_none()
                && now.as_millis()
                    < intent
                        .issued_at_ms
                        .saturating_add(intent.query.valid_for_ms)
        }) {
            return Err("original readonly query remains in flight".into());
        }
        (session.opened_at_ms, session.deadline_ms)
    } else {
        if request.expected_generation != 0 {
            return Err("first readonly use query requires generation zero".into());
        }
        (now.as_millis(), request.deadline_ms)
    };
    let total: usize = controller
        .memory_use
        .records
        .values()
        .flat_map(|r| r.controls.values())
        .filter_map(|d| d.reconciliation.as_ref())
        .map(|s| s.queries.len())
        .sum();
    if total >= 1024 {
        return Err("readonly use query journal is full".into());
    }
    let mut query = MemoryUseQuery {
        schema: "roboguide.memory-use-query/v0.1".into(),
        query_id: String::new(),
        command_id: request.command_id.clone(),
        command_digest: delivery.command.digest()?,
        generation,
        provider_ids: request.provider_ids.clone(),
        opened_at_ms: opened,
        session_budget_ms: deadline - opened,
        valid_for_ms: request.query_timeout_ms.min(deadline - now.as_millis()),
    };
    query.query_id = query.expected_id();
    query.validate_original(&delivery.command, preparation)?;
    let delivery = controller
        .memory_use
        .records
        .get_mut(&id)
        .expect("retained record")
        .controls
        .get_mut(&request.command_id)
        .expect("retained original revoke");
    let session = delivery
        .reconciliation
        .get_or_insert_with(|| UseReconciliation {
            opened_at_ms: opened,
            deadline_ms: deadline,
            queries: BTreeMap::new(),
        });
    session.queries.insert(
        generation,
        UseQueryIntent {
            request,
            principal: principal.into(),
            query,
            issued_at_ms: now.as_millis(),
            sent_at_ms: None,
            receipt: None,
            received_at_ms: None,
            current_authority_at_receipt: false,
        },
    );
    Ok((id, generation, true))
}

/// Marks pending bounded deliveries inside the enclosing authority transaction.
pub(crate) fn prepare(controller: &mut ControllerState, now: domain::TimestampMs) {
    for record in controller.memory_use.records.values_mut() {
        for delivery in record.controls.values_mut() {
            if let Some(session) = &mut delivery.reconciliation {
                for intent in session.queries.values_mut() {
                    if intent.sent_at_ms.is_none()
                        && now.as_millis() >= intent.issued_at_ms
                        && now.as_millis() < session.deadline_ms
                        && now.as_millis()
                            < intent
                                .issued_at_ms
                                .saturating_add(intent.query.valid_for_ms)
                    {
                        intent.sent_at_ms = Some(now.as_millis());
                    }
                }
            }
        }
    }
}

/// Sends only once-committed observations; a lost delivery consumes that query generation.
pub(crate) fn flush(
    controller: &ControllerState,
    prepared: domain::TimestampMs,
    now: domain::TimestampMs,
) {
    for (id, record) in &controller.memory_use.records {
        let Ok((_, preparation, _)) = memory_use::source(controller, id) else {
            continue;
        };
        for delivery in record.controls.values() {
            if let Some(session) = &delivery.reconciliation {
                if !current_owner(controller, delivery, preparation) {
                    continue;
                }
                for intent in session.queries.values().filter(|intent| {
                    intent.sent_at_ms == Some(prepared.as_millis()) && intent.receipt.is_none()
                }) {
                    let remaining = intent
                        .issued_at_ms
                        .saturating_add(intent.query.valid_for_ms)
                        .saturating_sub(now.as_millis());
                    let session_remaining = session.deadline_ms.saturating_sub(now.as_millis());
                    if remaining > 0
                        && session_remaining >= remaining
                        && let Err(error) = controller.bridge.deliver_memory_use_query(
                            &preparation.node_id,
                            &intent.query,
                            remaining,
                            session_remaining,
                        )
                    {
                        eprintln!("original readonly use query deferred: {error}");
                    }
                }
            }
        }
    }
}

/// Records source-authenticated observation and history-only ownership changes in one transaction.
pub(crate) fn receipt(
    controller: &mut ControllerState,
    node: &str,
    session: &str,
    frame: &integration::grpc::v0_4::MemoryUseQueryReceipt,
    received: domain::TimestampMs,
    now: domain::TimestampMs,
) -> Result<(), String> {
    let outcome: MemoryUseQueryReceipt =
        serde_json::from_str(&frame.receipt_json).map_err(|e| e.to_string())?;
    let (id, command, generation) = controller
        .memory_use
        .records
        .iter()
        .find_map(|(id, record)| {
            record.controls.iter().find_map(|(command, delivery)| {
                delivery
                    .reconciliation
                    .as_ref()?
                    .queries
                    .iter()
                    .find(|(_, intent)| intent.query.query_id == outcome.query_id)
                    .map(|(g, _)| (id.clone(), command.clone(), *g))
            })
        })
        .ok_or("readonly use receipt has no retained query")?;
    let (_, preparation, _) = memory_use::source(controller, &id)?;
    if preparation.node_id.as_str() != node
        || frame.session_id != session
        || controller.bridge.current_node_session(&preparation.node_id) != Some(session)
    {
        return Err("readonly use receipt changed current authenticated Node session".into());
    }
    let delivery = &controller.memory_use.records[&id].controls[&command];
    let reconciliation = delivery.reconciliation.as_ref().expect("located session");
    let intent = &reconciliation.queries[&generation];
    outcome.validate(&intent.query, &delivery.command, preparation)?;
    memory_use::validate_provider_proofs(
        controller,
        &id,
        &outcome
            .observations
            .iter()
            .filter_map(|o| o.proof.clone())
            .collect::<Vec<_>>(),
    )?;
    if let Some(old) = &intent.receipt {
        return if old == &outcome {
            Ok(())
        } else {
            Err("original readonly use receipt conflicts".into())
        };
    }
    if intent.sent_at_ms.is_none() || received.as_millis() < intent.issued_at_ms || received > now {
        return Err("readonly use receipt predates its committed intent".into());
    }
    let current = current_owner(controller, delivery, preparation)
        && received.as_millis() < reconciliation.deadline_ms
        && received.as_millis()
            < intent
                .issued_at_ms
                .saturating_add(intent.query.valid_for_ms);
    // Confirmed proofs are monotonic, including across different query generations.
    for observation in &outcome.observations {
        if let Some(proof) = &observation.proof {
            for old in reconciled_proofs(delivery) {
                if old.provider_id == proof.provider_id && old != *proof {
                    return Err("original provider revoke proof changed across observations".into());
                }
            }
            if let Some(old) = delivery.receipt.as_ref().and_then(|receipt| {
                receipt
                    .providers
                    .iter()
                    .find(|p| p.provider_id == proof.provider_id)
            }) && old != proof
            {
                return Err("readonly proof conflicts with the original aggregate evidence".into());
            }
        }
    }
    let intent = controller
        .memory_use
        .records
        .get_mut(&id)
        .expect("retained record")
        .controls
        .get_mut(&command)
        .expect("original control")
        .reconciliation
        .as_mut()
        .expect("original session")
        .queries
        .get_mut(&generation)
        .expect("original query");
    intent.receipt = Some(outcome);
    intent.received_at_ms = Some(received.as_millis());
    intent.current_authority_at_receipt = current;
    audit(
        controller, &id, &command, generation, "observed", session, received,
    )?;
    Ok(())
}

/// Returns only exact originally current confirmations; initial Unknown remains unmodified.
pub(crate) fn reconciled_proofs(
    delivery: &MemoryUseDelivery,
) -> Vec<domain::MemoryUseProviderAcknowledgement> {
    delivery
        .reconciliation
        .as_ref()
        .into_iter()
        .flat_map(|s| s.queries.values())
        .filter(|intent| intent.current_authority_at_receipt)
        .filter_map(|intent| intent.receipt.as_ref())
        .flat_map(|receipt| &receipt.observations)
        .filter(|o| o.status == MemoryUseQueryStatus::Confirmed)
        .filter_map(|o| o.proof.clone())
        .collect()
}

/// Appends a typed content-addressed audit event through the enclosing managed transaction.
fn audit(
    controller: &ControllerState,
    id: &str,
    command: &str,
    generation: u64,
    stage: &str,
    principal: &str,
    now: domain::TimestampMs,
) -> Result<(), String> {
    let (_, preparation, _) = memory_use::source(controller, id)?;
    let intent = &controller.memory_use.records[id].controls[command]
        .reconciliation
        .as_ref()
        .expect("admitted session")
        .queries[&generation];
    let digest = format!(
        "sha256:{:x}",
        sha2::Sha256::digest(serde_json::to_vec(intent).map_err(|e| e.to_string())?)
    );
    let mut sink = controller.bridge.event_sink().clone();
    sink.append(
        now,
        &domain::CorrelationId::new("memory-use-reconciliation").expect("fixed correlation"),
        None,
        domain::EventPayload::MemoryUseReconciliationEvidence {
            group_id: preparation.group_id.clone(),
            task_ref: preparation.task_ref.clone(),
            command_id: command.into(),
            query_id: intent.query.query_id.clone(),
            stage: stage.into(),
            content_digest: digest,
            principal: principal.into(),
            received_at: now,
        },
    );
    if let Some(error) = sink.take_error().map_err(|e| e.to_string())? {
        return Err(error.to_string());
    }
    Ok(())
}

/// Admits, journals and checkpoints before any transport delivery under the actual writer gate.
pub(crate) fn submit(
    controller: &Arc<Mutex<ControllerState>>,
    events: &state::SqliteEventLog,
    gate: &dyn crate::application::ApplicationWriteGate,
    clock: &impl Clock,
    body: &[u8],
    principal: &str,
    tenant: Option<&str>,
) -> Result<serde_json::Value, String> {
    let request: UseQueryRequest = serde_json::from_slice(body).map_err(|e| e.to_string())?;
    let command = request.command_id.clone();
    let _guard = gate.lock()?;
    events.begin_batch().map_err(|e| e.to_string())?;
    let result = (|| {
        let live = controller
            .lock()
            .map_err(|_| "Controller authority unavailable")?;
        let mut candidate = live.clone();
        drop(live);
        let now = clock.now();
        let (id, generation, added) = admit(&mut candidate, request, principal, tenant, now)?;
        if added {
            audit(
                &candidate,
                &id,
                &command,
                generation,
                "authorized",
                principal,
                now,
            )?;
        }
        if added {
            prepare(&mut candidate, now);
        }
        let checkpoint = server_checkpoint_json(&candidate).map_err(|e| e.to_string())?;
        save_controller_checkpoint(events, &checkpoint).map_err(|e| e.to_string())?;
        events.commit_batch().map_err(|e| e.to_string())?;
        let mut live = controller
            .lock()
            .map_err(|_| "Controller authority unavailable")?;
        *live = candidate;
        if added {
            flush(&live, now, clock.now());
        }
        let session = live.memory_use.records[&id].controls[&command]
            .reconciliation
            .as_ref()
            .expect("admitted query session");
        Ok(
            serde_json::json!({"schema_version":"roboguide.memory-use-query-admission/v0.1","command_id":command,"generation":generation,"intent":session.queries[&generation]}),
        )
    })();
    if result.is_err() {
        let _ = events.rollback_batch();
    }
    result
}

/// Returns bounded source history filtered by authenticated immutable Mission tenant.
pub(crate) fn view(
    controller: &ControllerState,
    tenant: Option<&str>,
    after: Option<&str>,
    limit: usize,
) -> serde_json::Value {
    let mut ordered = BTreeMap::new();
    for (id, record) in &controller.memory_use.records {
        if tenant_allowed(controller, id, tenant).is_err() {
            continue;
        }
        for (command, delivery) in &record.controls {
            if let Some(session) = &delivery.reconciliation {
                for intent in session.queries.values() {
                    ordered.insert(intent.query.query_id.clone(),serde_json::json!({"execution_id":id,"command_id":command,"opened_at_ms":session.opened_at_ms,"deadline_ms":session.deadline_ms,"intent":intent}));
                }
            }
        }
    }
    let mut records = Vec::new();
    let mut bytes = 0;
    let mut next_after = None;
    for (key, value) in ordered {
        if after.is_some_and(|after| key.as_str() <= after) {
            continue;
        }
        let size = serde_json::to_vec(&value).map_or(usize::MAX, |value| value.len());
        if records.len() >= limit.clamp(1, 100) || bytes + size > 1024 * 1024 {
            break;
        }
        bytes += size;
        next_after = Some(key);
        records.push(value);
    }
    serde_json::json!({"schema_version":"roboguide.memory-use-query-view/v0.1","records":records,"next_after":next_after,"receipt_is_physical_stop_proof":false})
}

/// Projects retained revoke evidence without inferring transport delivery or device stopping.
pub(crate) fn progress(
    controller: &ControllerState,
    now: domain::TimestampMs,
) -> serde_json::Value {
    let mut rows = BTreeMap::new();
    for (id, record) in &controller.memory_use.records {
        let Ok((_, preparation, _)) = memory_use::source(controller, id) else {
            continue;
        };
        let providers = preparation
            .inputs
            .iter()
            .map(|input| input.consumer.provider_id().to_string())
            .collect::<BTreeSet<_>>();
        for delivery in record
            .controls
            .values()
            .filter(|delivery| delivery.command.operation == domain::MemoryUseOperation::Revoke)
        {
            let confirmed = delivery
                .receipt
                .as_ref()
                .into_iter()
                .flat_map(|receipt| receipt.providers.iter().cloned())
                .chain(reconciled_proofs(delivery))
                .map(|proof| proof.provider_id)
                .collect::<BTreeSet<_>>();
            let exhausted = delivery.reconciliation.as_ref().is_some_and(|session| {
                now.as_millis() >= session.deadline_ms
                    || session.queries.len() == 16
                        && session.queries.values().all(|q| {
                            q.receipt.is_some()
                                || now.as_millis()
                                    >= q.issued_at_ms.saturating_add(q.query.valid_for_ms)
                        })
            });
            let statuses = providers
                .iter()
                .map(|provider| {
                    let status = if confirmed.contains(provider) {
                        "confirmed"
                    } else if exhausted {
                        "observation_exhausted"
                    } else {
                        "unknown"
                    };
                    (provider.clone(), serde_json::json!({"status":status}))
                })
                .collect::<BTreeMap<_, _>>();
            rows.insert(id.clone(),serde_json::json!({"command_id":delivery.command.command_id,"providers":statuses,"all_providers_confirmed":confirmed==providers,"observation_exhausted":exhausted}));
        }
    }
    serde_json::json!(rows)
}

/// Counts unresolved original providers and bounded pending/exhausted observation sessions.
pub(crate) fn metrics(
    controller: &ControllerState,
    now: domain::TimestampMs,
) -> (usize, usize, usize) {
    let progress = progress(controller, now);
    let unresolved = progress
        .as_object()
        .into_iter()
        .flat_map(|rows| rows.values())
        .flat_map(|row| {
            row["providers"]
                .as_object()
                .into_iter()
                .flat_map(|p| p.values())
        })
        .filter(|row| row["status"] != "confirmed")
        .count();
    let mut pending = 0;
    let mut exhausted = 0;
    for record in controller.memory_use.records.values() {
        for delivery in record.controls.values() {
            if let Some(session) = &delivery.reconciliation {
                if now.as_millis() >= session.deadline_ms {
                    exhausted += 1;
                }
                pending += session
                    .queries
                    .values()
                    .filter(|intent| {
                        intent.receipt.is_none()
                            && now.as_millis()
                                < intent
                                    .issued_at_ms
                                    .saturating_add(intent.query.valid_for_ms)
                            && now.as_millis() < session.deadline_ms
                    })
                    .count();
            }
        }
    }
    (unresolved, pending, exhausted)
}

/// Validates every retained query and receive-time gate before accepting restored authority.
pub(crate) fn validate_restore(
    controller: &ControllerState,
    id: &str,
    delivery: &MemoryUseDelivery,
) -> Result<(), String> {
    let Some(session) = &delivery.reconciliation else {
        return Ok(());
    };
    if delivery.command.operation != domain::MemoryUseOperation::Revoke
        || session.deadline_ms <= session.opened_at_ms
        || session.deadline_ms - session.opened_at_ms > 300_000
        || session.queries.is_empty()
        || session.queries.len() > 16
    {
        return Err("readonly use session checkpoint is malformed".into());
    }
    let (_, preparation, _) = memory_use::source(controller, id)?;
    for (index, (generation, intent)) in session.queries.iter().enumerate() {
        intent
            .query
            .validate_original(&delivery.command, preparation)?;
        if *generation != index as u64 + 1
            || intent.query.generation != *generation
            || intent.query.opened_at_ms != session.opened_at_ms
            || intent.query.session_budget_ms != session.deadline_ms - session.opened_at_ms
            || intent.issued_at_ms < session.opened_at_ms
            || intent.issued_at_ms >= session.deadline_ms
            || intent.principal.trim().is_empty()
            || intent.principal.len() > 256
            || !(1..=30_000).contains(&intent.request.query_timeout_ms)
            || intent.request.command_id != delivery.command.command_id
            || intent.request.expected_generation.checked_add(1) != Some(*generation)
            || intent.request.provider_ids != intent.query.provider_ids
            || intent.request.deadline_ms != session.deadline_ms
            || intent.query.valid_for_ms
                != intent
                    .request
                    .query_timeout_ms
                    .min(session.deadline_ms - intent.issued_at_ms)
            || intent
                .sent_at_ms
                .is_some_and(|sent| sent < intent.issued_at_ms)
            || intent.receipt.is_some() != intent.received_at_ms.is_some()
        {
            return Err(
                "readonly use checkpoint changed original authority, request or timing".into(),
            );
        }
        if let Some(receipt) = &intent.receipt {
            receipt.validate(&intent.query, &delivery.command, preparation)?;
            memory_use::validate_provider_proofs(
                controller,
                id,
                &receipt
                    .observations
                    .iter()
                    .filter_map(|o| o.proof.clone())
                    .collect::<Vec<_>>(),
            )?;
            if intent.sent_at_ms.is_none()
                || intent
                    .received_at_ms
                    .is_some_and(|received| received < intent.issued_at_ms)
                || (intent.current_authority_at_receipt
                    && intent.received_at_ms.is_none_or(|received| {
                        received >= session.deadline_ms
                            || received
                                >= intent
                                    .issued_at_ms
                                    .saturating_add(intent.query.valid_for_ms)
                    }))
            {
                return Err("readonly use checkpoint forged receive authority".into());
            }
        } else if intent.current_authority_at_receipt {
            return Err("readonly use checkpoint authorizes absent evidence".into());
        }
    }
    Ok(())
}
