//! Durable bounded original-provider reconciliation, independent of Task and replica authority.

use super::memory_import::{ImportPhase, MemoryImportRequest};
use crate::*;
use integration::grpc::v0_4::{
    MemoryImport, MemoryImportQuery, MemoryImportQueryReceipt, MemoryImportReceiptStatus,
};
use ports::EventSink;
use sha2::Digest as _;

/// One explicit query request; expected generation makes caller retries idempotent.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct MemoryQueryRequest {
    /// Original retained import, never a replacement import identity.
    import_id: String,
    /// Last known reconciliation generation; zero starts the original bounded session.
    expected_generation: u64,
    /// Frozen reconciliation deadline; subsequent queries cannot extend it.
    deadline_ms: u64,
    /// Maximum receive-relative time for this readonly query, bounded by the frozen deadline.
    query_timeout_ms: u64,
}

/// Query budget and immutable history belonging to one original Unknown import.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct MemoryReconciliation {
    /// Original opening time, unchanged by restart or HTTP retry.
    opened_at_ms: u64,
    /// Original nonrenewable deadline, at most five minutes after opening.
    deadline_ms: u64,
    /// Complete consecutive generation history, at most sixteen readonly queries.
    queries: BTreeMap<u64, MemoryQueryIntent>,
    /// Exact query proving a currently authorized original import's local outcome.
    resolved_by: Option<u64>,
}

/// One immutable query, its bounded outbox delivery and its first accepted original receipt.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct MemoryQueryIntent {
    /// Original HTTP body retained for exact idempotent retries.
    request: MemoryQueryRequest,
    /// Certificate-derived original stable principal, never a body/header authority.
    principal: String,
    /// Original Controller-local authorization time.
    authorized_at_ms: u64,
    /// Original validity and Node receive-relative timeout used for all deliveries.
    valid_for_ms: u64,
    /// Last prepared post-Commit delivery; replay does not reset the limit.
    last_delivery_ms: Option<u64>,
    /// At most four identical deliveries; each Node query identity runs at most once.
    deliveries: u32,
    /// First accepted receipt, retained verbatim for audit and restore checking.
    receipt: Option<serde_json::Value>,
    /// Original receive time of that receipt, never refreshed on replay.
    received_at_ms: Option<u64>,
    /// Current original physical-binding authorization at first receipt; false is history only.
    current_authority_at_receipt: bool,
}

/// Builds the complete original import content without a replaceable transport epoch.
fn original_command(request: &MemoryImportRequest) -> MemoryImport {
    MemoryImport {
        import_id: request.import_id.clone(),
        command_id: format!("memory-import-{}", request.import_id),
        consumer_provider_id: request.consumer_provider_id.clone(),
        manifest_json: serde_json::to_string(&request.manifest).expect("validated manifest"),
        mission_id: request.task_ref.mission_id().as_str().into(),
        group_id: request.group_id.as_str().into(),
        task_id: request.task_ref.task_id().as_str().into(),
        role_id: request.role_id.as_str().into(),
        execution_id: request.execution_id.clone(),
        ..Default::default()
    }
}

/// Admits explicit readonly queries on a private transaction candidate before any network delivery.
fn admit(
    controller: &mut ControllerState,
    request: MemoryQueryRequest,
    principal: &str,
    now: domain::TimestampMs,
) -> Result<u64, String> {
    let total: usize = controller
        .memory_imports
        .values()
        .filter_map(|i| i.reconciliation.as_ref())
        .map(|r| r.queries.len())
        .sum();
    let import = controller
        .memory_imports
        .get(&request.import_id)
        .ok_or("original import is absent")?;
    let generation = request
        .expected_generation
        .checked_add(1)
        .ok_or("query generation overflow")?;
    if let Some(existing) = import
        .reconciliation
        .as_ref()
        .and_then(|r| r.queries.get(&generation))
    {
        return if existing.request == request && existing.principal == principal {
            Ok(generation)
        } else {
            Err("query identity conflicts with original authorization".into())
        };
    }
    if import.phase != ImportPhase::Unknown
        || total >= 1024
        || principal.trim().is_empty()
        || principal.len() > 256
        || !(1..=16).contains(&generation)
        || !(1..=30_000).contains(&request.query_timeout_ms)
        || now.as_millis() >= request.deadline_ms
        || request.deadline_ms - now.as_millis() > 300_000
    {
        return Err(
            "query requires original Unknown, bounded authority, time and retained history".into(),
        );
    }
    let node = controller
        .bridge
        .state()
        .node(&import.request.node_id)
        .ok_or("original Node is not registered")?;
    if node.registration().contract_version().as_str()
        != integration::grpc::v0_4::CONTINUITY_NODE_CONTRACT_VERSION
        || !node
            .registration()
            .memory_providers()
            .iter()
            .any(|p| p.provider_id() == import.request.consumer_provider_id)
    {
        return Err("original provider has no negotiated Memory continuity route".into());
    }
    if let Some(session) = &import.reconciliation {
        if session.deadline_ms != request.deadline_ms
            || session.queries.len() as u64 != request.expected_generation
            || now.as_millis() < session.opened_at_ms
        {
            return Err(
                "original query session generation, deadline or receive time differs".into(),
            );
        }
    } else if request.expected_generation != 0 {
        return Err("first query requires generation zero".into());
    }
    let valid_for_ms = request
        .query_timeout_ms
        .min(request.deadline_ms - now.as_millis());
    let import = controller
        .memory_imports
        .get_mut(&request.import_id)
        .expect("retained import");
    let session = import
        .reconciliation
        .get_or_insert_with(|| MemoryReconciliation {
            opened_at_ms: now.as_millis(),
            deadline_ms: request.deadline_ms,
            queries: BTreeMap::new(),
            resolved_by: None,
        });
    session.queries.insert(
        generation,
        MemoryQueryIntent {
            request,
            principal: principal.into(),
            authorized_at_ms: now.as_millis(),
            valid_for_ms,
            last_delivery_ms: None,
            deliveries: 0,
            receipt: None,
            received_at_ms: None,
            current_authority_at_receipt: false,
        },
    );
    Ok(generation)
}

/// Prepares identical bounded query delivery in the same transaction as the complete checkpoint.
pub(crate) fn prepare(controller: &mut ControllerState, now: domain::TimestampMs) {
    for import in controller.memory_imports.values_mut() {
        let Some(session) = &mut import.reconciliation else {
            continue;
        };
        for query in session.queries.values_mut() {
            if query.receipt.is_none()
                && query.deliveries < 4
                && now.as_millis() >= query.authorized_at_ms
                && now.as_millis()
                    < session
                        .deadline_ms
                        .min(query.authorized_at_ms.saturating_add(query.valid_for_ms))
                && query
                    .last_delivery_ms
                    .is_none_or(|last| now.as_millis().saturating_sub(last) >= 1000)
            {
                query.last_delivery_ms = Some(now.as_millis());
                query.deliveries += 1;
            }
        }
    }
}

/// Delivers only prepared durable readonly commands through current epoch, quorum and Node IAM gates.
pub(crate) fn flush(
    controller: &ControllerState,
    prepared_at: domain::TimestampMs,
    now: domain::TimestampMs,
) {
    for import in controller.memory_imports.values() {
        let Some(session) = &import.reconciliation else {
            continue;
        };
        for (&generation, query) in &session.queries {
            if query.receipt.is_some()
                || query.last_delivery_ms != Some(prepared_at.as_millis())
                || now.as_millis() < query.authorized_at_ms
                || now.as_millis()
                    >= session
                        .deadline_ms
                        .min(query.authorized_at_ms.saturating_add(query.valid_for_ms))
            {
                continue;
            }
            let wire = MemoryImportQuery {
                query_id: format!("memory-query-{}-{generation}", import.request.import_id),
                import_id: import.request.import_id.clone(),
                command_id: format!("memory-import-{}", import.request.import_id),
                consumer_provider_id: import.request.consumer_provider_id.clone(),
                original_content_digest: integration::memory_import_content_digest(
                    &original_command(&import.request),
                ),
                generation,
                valid_for_ms: query.valid_for_ms,
                ..Default::default()
            };
            if let Err(error) = controller
                .bridge
                .deliver_memory_import_query(import.request.node_id.as_str(), wire)
            {
                eprintln!("Memory readonly query delivery deferred: {error}");
            }
        }
    }
}

/// Checks exact original-provider result bytes without treating missing records as no side effects.
fn validate_result(
    request: &MemoryImportRequest,
    receipt: &MemoryImportQueryReceipt,
) -> Result<ImportPhase, String> {
    let hex = |text: &str, prefixed: bool| {
        let value = if prefixed {
            text.strip_prefix("sha256:").unwrap_or("")
        } else {
            text
        };
        value.len() == 64
            && value
                .bytes()
                .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
    };
    if receipt.provider_instance_id.len() > 256
        || (!receipt.identity_digest.is_empty() && !hex(&receipt.identity_digest, false))
        || (!receipt.provider_contract_digest.is_empty()
            && !hex(&receipt.provider_contract_digest, true))
    {
        return Err(
            "query receipt identity metadata exceeds its exact digest/instance bounds".into(),
        );
    }
    let phase =
        match MemoryImportReceiptStatus::try_from(receipt.status).map_err(|e| e.to_string())? {
            MemoryImportReceiptStatus::MemoryImportCompleted => ImportPhase::Completed,
            MemoryImportReceiptStatus::MemoryImportRejected => ImportPhase::Rejected,
            MemoryImportReceiptStatus::MemoryImportUnknown => ImportPhase::Unknown,
            _ => return Err("readonly query cannot report an import admission receipt".into()),
        };
    if receipt.result_json.is_empty() {
        return if phase == ImportPhase::Unknown {
            Ok(phase)
        } else {
            Err("conclusive query has no original durable result".into())
        };
    }
    let result: serde_json::Value =
        serde_json::from_str(&receipt.result_json).map_err(|e| e.to_string())?;
    let expected_content = integration::memory_import_content_digest(&original_command(request));
    let required = [
        ("schema", "roboguide.memory-import-query-result/v0.1"),
        ("import_id", request.import_id.as_str()),
        ("command_id", receipt.command_id.as_str()),
        ("provider_id", request.consumer_provider_id.as_str()),
        ("original_content_digest", expected_content.as_str()),
        ("identity_digest", receipt.identity_digest.as_str()),
        (
            "provider_instance_id",
            receipt.provider_instance_id.as_str(),
        ),
        (
            "provider_contract_digest",
            receipt.provider_contract_digest.as_str(),
        ),
    ];
    if result.as_object().is_none_or(|o| o.len() != 11)
        || required
            .iter()
            .any(|(key, expected)| result[*key].as_str() != Some(*expected))
        || receipt.provider_instance_id.trim().is_empty()
        || receipt.provider_instance_id.len() > 256
        || receipt.identity_digest.len() != 64
        || receipt.provider_contract_digest.len() != 71
        || result["reason"].as_str().is_none_or(|s| s.len() > 4096)
    {
        return Err("query result differs from complete original import/provider identity".into());
    }
    let outcome = result["outcome"]
        .as_str()
        .ok_or("query outcome is unavailable")?;
    match (phase, outcome, result["durable"].as_bool()) {
        (ImportPhase::Completed, "completed", Some(true))
        | (ImportPhase::Rejected, "rejected_before_effect", Some(true))
        | (ImportPhase::Unknown, "unknown", Some(_)) => Ok(phase),
        _ => Err("query outcome lacks exact durable original-import proof".into()),
    }
}

/// Retains first query evidence; changed ownership or late reports remain history without activation.
pub(crate) fn receipt(
    controller: &mut ControllerState,
    node: &str,
    receipt: &MemoryImportQueryReceipt,
    received: domain::TimestampMs,
    events: &mut state::SqliteEventLog,
) -> Result<(), String> {
    let import = controller
        .memory_imports
        .get(&receipt.import_id)
        .ok_or("query receipt has no original import")?;
    let session = import
        .reconciliation
        .as_ref()
        .ok_or("query receipt was not authorized")?;
    let query = session
        .queries
        .get(&receipt.generation)
        .ok_or("query generation was not authorized")?;
    if node != import.request.node_id.as_str()
        || receipt.command_id != format!("memory-import-{}", receipt.import_id)
        || receipt.query_id != format!("memory-query-{}-{}", receipt.import_id, receipt.generation)
        || query.deliveries == 0
        || received.as_millis() < query.authorized_at_ms
        || receipt.reason.len() > 4096
        || receipt.result_json.len() > 16 * 1024
    {
        return Err("query receipt differs from original authorized command".into());
    }
    let phase = validate_result(&import.request, receipt)?;
    if let Some(prior) = &query.receipt {
        return if prior["result_json"] == receipt.result_json && prior["status"] == receipt.status {
            Ok(())
        } else {
            Err("query result is immutable".into())
        };
    }
    for prior in session
        .queries
        .values()
        .filter_map(|q| q.receipt.as_ref())
        .filter(|r| !r["result_json"].as_str().unwrap_or_default().is_empty())
    {
        if prior["identity_digest"] != receipt.identity_digest
            || prior["provider_instance_id"] != receipt.provider_instance_id
            || prior["provider_contract_digest"] != receipt.provider_contract_digest
        {
            return Err("query changed the original retained backend identity".into());
        }
        let prior_status = prior["status"].as_i64().unwrap_or_default();
        if matches!(prior_status, 2 | 3)
            && phase != ImportPhase::Unknown
            && prior_status != i64::from(receipt.status)
        {
            return Err("query contradicts a durable original provider conclusion".into());
        }
    }
    let current = received.as_millis()
        < session
            .deadline_ms
            .min(query.authorized_at_ms.saturating_add(query.valid_for_ms))
        && super::memory_import::authorize(controller, &import.request, received).is_ok();
    let import = controller
        .memory_imports
        .get_mut(&receipt.import_id)
        .expect("original import retained");
    let session = import.reconciliation.as_mut().expect("authorized query");
    let query = session
        .queries
        .get_mut(&receipt.generation)
        .expect("authorized generation");
    query.receipt = Some(serde_json::to_value(receipt).expect("wire receipt serializes"));
    query.received_at_ms = Some(received.as_millis());
    query.current_authority_at_receipt = current;
    if current
        && matches!(phase, ImportPhase::Completed | ImportPhase::Rejected)
        && import.phase == ImportPhase::Unknown
    {
        session.resolved_by = Some(receipt.generation);
        import.phase = phase;
        import.reason = "original provider outcome reconciled without repeating import".into();
    }
    audit(
        controller,
        &receipt.import_id,
        receipt.generation,
        "query_receipt",
        &format!("node:{node}:session:{}", receipt.session_id),
        received,
        events,
    );
    Ok(())
}

/// Retains the complete authority/query digest in the same transaction as its immutable history.
fn audit(
    controller: &ControllerState,
    id: &str,
    generation: u64,
    stage: &str,
    principal: &str,
    now: domain::TimestampMs,
    events: &mut state::SqliteEventLog,
) {
    let import = &controller.memory_imports[id];
    let query = &import
        .reconciliation
        .as_ref()
        .expect("authorized reconciliation")
        .queries[&generation];
    let digest = format!(
        "sha256:{:x}",
        sha2::Sha256::digest(serde_json::to_vec(query).expect("typed query serializes"))
    );
    events.append(
        now,
        &domain::CorrelationId::new("memory-original-provider-reconciliation")
            .expect("fixed correlation"),
        None,
        domain::EventPayload::MemoryCommandEvidence {
            group_id: import.request.group_id.clone(),
            task_ref: import.request.task_ref.clone(),
            import_id: id.into(),
            command_id: format!("memory-query-{id}-{generation}"),
            generation,
            stage: stage.into(),
            content_digest: digest,
            principal: principal.into(),
            received_at: now,
        },
    );
}

/// Checks all original query clocks, generation bounds and result identities before restore publication.
pub(crate) fn validate_restored(controller: &ControllerState) -> Result<(), String> {
    let mut total = 0;
    for import in controller.memory_imports.values() {
        let Some(session) = &import.reconciliation else {
            continue;
        };
        if session.deadline_ms <= session.opened_at_ms
            || session.deadline_ms - session.opened_at_ms > 300_000
            || session.queries.is_empty()
            || session.queries.len() > 16
        {
            return Err("invalid original reconciliation budget".into());
        }
        for (ordinal, (&generation, q)) in session.queries.iter().enumerate() {
            total += 1;
            if generation != ordinal as u64 + 1
                || q.request.expected_generation + 1 != generation
                || q.request.import_id != import.request.import_id
                || q.request.deadline_ms != session.deadline_ms
                || q.principal.trim().is_empty()
                || q.principal.len() > 256
                || q.authorized_at_ms < session.opened_at_ms
                || q.authorized_at_ms >= session.deadline_ms
                || q.valid_for_ms == 0
                || q.valid_for_ms
                    != q.request
                        .query_timeout_ms
                        .min(session.deadline_ms - q.authorized_at_ms)
                || q.valid_for_ms > 30_000
                || q.deliveries > 4
                || (q.deliveries == 0) != q.last_delivery_ms.is_none()
                || q.last_delivery_ms.is_some_and(|t| {
                    t < q.authorized_at_ms || t >= q.authorized_at_ms.saturating_add(q.valid_for_ms)
                })
                || q.received_at_ms.is_some_and(|t| t < q.authorized_at_ms)
                || q.receipt.is_some() != q.received_at_ms.is_some()
            {
                return Err(
                    "invalid Memory query generation, clock or immutable delivery budget".into(),
                );
            }
            if let Some(r) = &q.receipt {
                let receipt = receipt_from_value(r)?;
                validate_result(&import.request, &receipt)?;
                if receipt.generation != generation
                    || receipt.query_id
                        != format!("memory-query-{}-{generation}", import.request.import_id)
                {
                    return Err("query receipt generation differs".into());
                }
                if receipt.import_id != import.request.import_id
                    || receipt.command_id != format!("memory-import-{}", import.request.import_id)
                    || q.current_authority_at_receipt
                        && q.received_at_ms.is_none_or(|t| {
                            t >= session
                                .deadline_ms
                                .min(q.authorized_at_ms.saturating_add(q.valid_for_ms))
                        })
                {
                    return Err(
                        "retained query receipt original identity or authority time differs".into(),
                    );
                }
            }
        }
        if session.resolved_by.is_some_and(|g| {
            session.queries.get(&g).is_none_or(|q| {
                !q.current_authority_at_receipt
                    || q.receipt.as_ref().is_none_or(|r| {
                        receipt_from_value(r)
                            .and_then(|receipt| validate_result(&import.request, &receipt))
                            .map_or(true, |phase| {
                                !matches!(phase, ImportPhase::Completed | ImportPhase::Rejected)
                                    || phase != import.phase
                            })
                    })
            })
        }) {
            return Err("reconciliation resolution lacks original current-authority proof".into());
        }
    }
    if total > 1024 {
        return Err("retained readonly query history exceeds its bound".into());
    }
    Ok(())
}

/// Reconstructs a retained wire receipt through required typed fields without accepting defaults.
fn receipt_from_value(value: &serde_json::Value) -> Result<MemoryImportQueryReceipt, String> {
    let text = |key: &str| {
        value[key]
            .as_str()
            .map(str::to_string)
            .ok_or_else(|| format!("query receipt lacks {key}"))
    };
    Ok(MemoryImportQueryReceipt {
        session_id: text("session_id")?,
        sequence: value["sequence"]
            .as_u64()
            .ok_or("receipt sequence absent")?,
        query_id: text("query_id")?,
        import_id: text("import_id")?,
        command_id: text("command_id")?,
        generation: value["generation"]
            .as_u64()
            .ok_or("receipt generation absent")?,
        status: value["status"]
            .as_i64()
            .and_then(|s| i32::try_from(s).ok())
            .ok_or("receipt status absent")?,
        reason: text("reason")?,
        identity_digest: text("identity_digest")?,
        provider_instance_id: text("provider_instance_id")?,
        provider_contract_digest: text("provider_contract_digest")?,
        result_json: text("result_json")?,
    })
}

/// Commits query admission and budget before sending an explicitly negotiated readonly command.
pub(crate) fn submit(
    controller: &Arc<Mutex<ControllerState>>,
    events: &state::SqliteEventLog,
    gate: &dyn crate::application::ApplicationWriteGate,
    clock: &impl Clock,
    body: &[u8],
    principal: &str,
) -> Result<serde_json::Value, String> {
    let request: MemoryQueryRequest = serde_json::from_slice(body).map_err(|e| e.to_string())?;
    let id = request.import_id.clone();
    let _guard = gate.lock()?;
    events.begin_batch().map_err(|e| e.to_string())?;
    let result = (|| {
        let mut live = controller
            .lock()
            .map_err(|_| "Controller authority unavailable")?;
        let mut candidate = live.clone();
        let now = clock.now();
        let count = candidate
            .memory_imports
            .get(&id)
            .and_then(|i| i.reconciliation.as_ref())
            .map_or(0, |s| s.queries.len());
        let generation = admit(&mut candidate, request, principal, now)?;
        if candidate.memory_imports[&id]
            .reconciliation
            .as_ref()
            .expect("admitted query")
            .queries
            .len()
            != count
        {
            audit(
                &candidate,
                &id,
                generation,
                "query_authorized",
                principal,
                now,
                &mut events.clone(),
            );
        }
        prepare(&mut candidate, now);
        let checkpoint = server_checkpoint_json(&candidate).map_err(|e| e.to_string())?;
        save_controller_checkpoint(events, &checkpoint).map_err(|e| e.to_string())?;
        events.commit_batch().map_err(|e| e.to_string())?;
        *live = candidate;
        flush(&live, now, clock.now());
        serde_json::to_value(&live.memory_imports[&id]).map_err(|e| e.to_string())
    })();
    if result.is_err() {
        let _ = events.rollback_batch();
    }
    result
}
