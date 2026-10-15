//! Explicit exact-binding Memory import admission, durable outbox and receipt reduction.

use crate::*;
use integration::grpc::v0_4::{MemoryImportReceipt, MemoryImportReceiptStatus};
use ports::EventSink;

/// Bounded administrator request; no field selects or creates a physical binding.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct MemoryImportRequest {
    /// Stable immutable caller identity, unique across retained Controller history.
    pub(crate) import_id: String,
    /// Existing Control Group containing the consumer.
    pub(crate) group_id: domain::ExecutionGroupId,
    /// Exact Mission-scoped consumer Task.
    pub(crate) task_ref: domain::TaskRef,
    /// Exact Task Role already bound by Control.
    pub(crate) role_id: domain::RoleId,
    /// Current physical attempt; old attempts never authorize a new consumer.
    pub(crate) execution_id: String,
    /// Current consumer Node from the committed binding.
    pub(crate) node_id: domain::NodeId,
    /// Exact registered receiving provider.
    pub(crate) consumer_provider_id: String,
    /// Complete immutable metadata; payload bytes remain on the Artifact data plane.
    pub(crate) manifest: domain::MemoryArtifactManifest,
    /// Exclusive Controller-local deadline that retries cannot renew.
    pub(crate) deadline_ms: u64,
    /// Optional administrator-frozen source/freshness/schema/value prerequisite for this import.
    #[serde(default)]
    pub(crate) evidence_gate: Option<MemoryEvidenceGate>,
}

/// Exact State policy consumed by the real import readiness decision, never an implicit Belief merge.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct MemoryEvidenceGate {
    /// Immutable versioned source and confidence policy.
    pub(crate) policy: state::DecisionEvidencePolicy,
    /// Explicit required structured value after source/schema/freshness admission.
    pub(crate) expected_value: serde_json::Value,
}

/// Durable command phase; receipts never manufacture Memory replica or Task success.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum ImportPhase {
    /// Authorized command awaiting delivery or a replayed receipt.
    Queued,
    /// Node journal accepted the immutable identity, without proving provider completion.
    Persisted,
    /// The Node durably reported local provider completion.
    Completed,
    /// The Node conclusively rejected the import before completion.
    Rejected,
    /// A bounded retry/deadline or ambiguous local side effect requires reconciliation.
    Unknown,
}

impl ImportPhase {
    /// Terminal evidence cannot be replaced by a later receipt or a caller retry.
    fn terminal(self) -> bool {
        matches!(self, Self::Completed | Self::Rejected | Self::Unknown)
    }
}

/// Application-owned command identity and delivery budget in the complete Controller checkpoint.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct MemoryImportIntent {
    /// Original authenticated stable submitter; historical absence is never fabricated on restore.
    #[serde(default)]
    pub(crate) authorized_by: Option<String>,
    /// Exact source-bound content permission; legacy absence never becomes a current Group grant.
    #[serde(default)]
    pub(crate) authority: Option<MemoryAuthorityStamp>,
    /// Original immutable administrator request.
    pub(crate) request: MemoryImportRequest,
    /// Durable receipt phase.
    pub(crate) phase: ImportPhase,
    /// First Controller-local authorization time.
    pub(crate) authorized_at_ms: u64,
    /// Last prepared delivery time; zero only before first preparation.
    pub(crate) last_delivery_ms: Option<u64>,
    /// At most sixteen deliveries of the same identity; never new provider authorization.
    pub(crate) deliveries: u32,
    /// Original receive time of latest accepted receipt, preserved across restore.
    pub(crate) receipt_received_at_ms: Option<u64>,
    /// Bounded outcome diagnostic, never interpreted as authority.
    pub(crate) reason: String,
    /// Late or recovery-time receipt retained without renewing an expired authorization.
    #[serde(default)]
    pub(crate) recovery_receipt: Option<ImportPhase>,
    /// Explicit bounded readonly reconciliation; no replacement import authorization.
    #[serde(default)]
    pub(crate) reconciliation: Option<super::memory_query::MemoryReconciliation>,
}

/// Original Control policy and member generation frozen before an import can have side effects.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct MemoryAuthorityStamp {
    /// Original immutable tenant namespace consumed by this authorization decision.
    pub(crate) tenant_id: String,
    /// Complete receiving Local EAIOS/provider contract, separate from the producer's owner.
    pub(crate) consumer: domain::MemoryProviderDescriptor,
    /// Immutable content authority identity.
    pub(crate) registry_id: String,
    /// Original full policy interpretation, never refreshed by replay.
    pub(crate) revision: u64,
    /// Current exact physical bindings at admission.
    pub(crate) membership: domain::GroupMembershipVersion,
}

/// Revalidates Control, Runtime, provider and scope without changing any authority.
pub(crate) fn authorize(
    controller: &ControllerState,
    request: &MemoryImportRequest,
    now: domain::TimestampMs,
) -> Result<Option<MemoryAuthorityStamp>, String> {
    if let Some(gate) = &request.evidence_gate {
        gate.policy.validate().map_err(|error| error.to_string())?;
        if gate.policy.payload_schema.is_none() {
            return Err("Memory decision evidence requires an exact payload schema".into());
        }
        if controller
            .memory_imports
            .values()
            .filter_map(|intent| intent.request.evidence_gate.as_ref())
            .any(|admitted| {
                admitted.policy.policy_id == gate.policy.policy_id
                    && (admitted.policy.revision > gate.policy.revision
                        || (admitted.policy.revision == gate.policy.revision
                            && admitted.policy != gate.policy))
            })
        {
            return Err(
                "Memory decision policy conflicts with its durable identity/revision watermark"
                    .into(),
            );
        }
        let evidence = controller.bridge.evaluate_decision_evidence(
            &gate.policy,
            state::DecisionKind::Readiness,
            now,
        );
        if evidence.disposition != state::EvidenceDisposition::Accepted
            || evidence
                .record
                .as_ref()
                .is_none_or(|record| record.value() != &gate.expected_value)
        {
            return Err(format!(
                "Memory readiness evidence is unavailable or differs: {:?}",
                evidence.disposition
            ));
        }
    }
    let mission = controller
        .orchestrator
        .execution(request.task_ref.mission_id())
        .ok_or("Memory consumer Mission is absent")?;
    if mission.group_id() != &request.group_id
        || mission.isolation().is_some()
        || !matches!(
            mission.lifecycle(),
            orchestration::MissionExecutionLifecycle::Accepted
                | orchestration::MissionExecutionLifecycle::Running
        )
    {
        return Err("Memory consumer Mission is held or no longer executable".into());
    }
    let group = controller
        .bridge
        .control()
        .group(&request.group_id)
        .ok_or("Memory consumer Group is absent")?;
    let task = group
        .task_execution(&request.task_ref)
        .ok_or("Memory consumer Task is absent")?;
    if group.lifecycle() == control::GroupLifecycle::Released
        || !task.assignments().iter().any(|assignment| {
            assignment.role_id() == &request.role_id && assignment.node_id() == &request.node_id
        })
        || controller.bridge.current_task_attempt_id(
            &request.group_id,
            &request.task_ref,
            &request.role_id,
        ) != Some(request.execution_id.as_str())
        || !controller.bridge.attempt_history().iter().any(|attempt| {
            attempt.execution_id() == request.execution_id
                && matches!(
                    attempt.status(),
                    runtime::ExecutionStatus::Dispatched
                        | runtime::ExecutionStatus::Accepted
                        | runtime::ExecutionStatus::Running
                )
        })
    {
        return Err(
            "Memory import requires the exact current Control binding and live Runtime attempt"
                .into(),
        );
    }
    let node = controller
        .bridge
        .state()
        .node(&request.node_id)
        .ok_or("Memory consumer Node is not registered")?;
    let provider = node
        .registration()
        .memory_providers()
        .iter()
        .find(|provider| provider.provider_id() == request.consumer_provider_id)
        .ok_or("Memory consumer provider is not registered")?;
    provider
        .admit_import(&request.manifest, &request.node_id)
        .map_err(|error| error.to_string())?;
    if request.manifest.artifact().is_none() {
        return Err("metadata-only Memory has no content to import".into());
    }
    if let domain::MemoryScope::ExecutionGroup(scope) = request.manifest.scope() {
        if scope != &request.group_id {
            return Err("Memory scope belongs to a different Group".into());
        }
        if let domain::MemoryOwner::Node { node_id, .. } = request.manifest.owner()
            && !group.task_executions().any(|task| {
                task.assignments()
                    .iter()
                    .any(|assignment| assignment.node_id() == node_id)
            })
        {
            return Err("Group-scoped producer is not a current Group participant".into());
        }
    }
    let current = if let Some(registry) =
        controller.bridge.control().memory_authorization_registry()
    {
        if !controller
            .bridge
            .control()
            .memory_node_identity_is_current(&request.node_id, now)
        {
            return Err("Memory consumer session, identity or attestation is not current".into());
        }
        let tenant = controller
            .bridge
            .control()
            .mission_tenant(request.task_ref.mission_id())
            .unwrap_or("local");
        let membership = controller
            .bridge
            .control()
            .authorize_memory_consumer(
                &request.manifest,
                tenant,
                &request.group_id,
                &request.task_ref,
                &request.role_id,
                &request.node_id,
                provider,
                domain::MemoryAccessOperation::Import,
                None,
            )
            .map_err(|error| error.to_string())?;
        Some(MemoryAuthorityStamp {
            tenant_id: tenant.into(),
            consumer: provider.clone(),
            registry_id: registry.registry_id.clone(),
            revision: registry.revision,
            membership,
        })
    } else {
        if matches!(
            request.manifest.scope(),
            domain::MemoryScope::ExecutionGroup(_)
        ) {
            return Err("Group Memory requires explicit distributed content authorization".into());
        }
        None
    };
    if controller
        .memory_imports
        .get(&request.import_id)
        .is_some_and(|original| original.authority != current)
    {
        return Err("original Memory policy or membership generation is no longer current".into());
    }
    Ok(current)
}

/// Admits a stable command into the private transaction candidate before any network send.
pub(crate) fn admit(
    controller: &mut ControllerState,
    request: MemoryImportRequest,
    now: domain::TimestampMs,
) -> Result<(), String> {
    if let Some(existing) = controller.memory_imports.get(&request.import_id) {
        return if existing.request == request {
            Ok(())
        } else {
            Err("Memory import_id conflicts with durable authorization".into())
        };
    }
    if request.import_id.trim().is_empty()
        || request.import_id.len() > 128
        || request.execution_id.trim().is_empty()
        || request.deadline_ms <= now.as_millis()
        || request.deadline_ms.saturating_sub(now.as_millis()) > 3_600_000
        || controller.memory_imports.len() >= 4096
    {
        return Err("Memory import identity, deadline or retained-command bound is invalid".into());
    }
    if controller.memory_imports.values().any(|prior| {
        ((prior.request.node_id == request.node_id
            && prior.request.consumer_provider_id == request.consumer_provider_id)
            || (prior.request.group_id == request.group_id
                && prior.request.task_ref == request.task_ref
                && prior.request.role_id == request.role_id))
            && prior.request.manifest.selector() == request.manifest.selector()
            && !matches!(prior.phase, ImportPhase::Completed | ImportPhase::Rejected)
    }) {
        return Err(
            "an unresolved original import fences replacement IDs/providers for this consumer/selector"
                .into(),
        );
    }
    let authority = authorize(controller, &request, now)?;
    controller.memory_imports.insert(
        request.import_id.clone(),
        MemoryImportIntent {
            authorized_by: None,
            authority,
            request,
            phase: ImportPhase::Queued,
            authorized_at_ms: now.as_millis(),
            last_delivery_ms: None,
            deliveries: 0,
            receipt_received_at_ms: None,
            reason: String::new(),
            recovery_receipt: None,
            reconciliation: None,
        },
    );
    Ok(())
}

/// Prepares bounded replay delivery inside the same transaction as the resulting checkpoint.
pub(crate) fn prepare(controller: &mut ControllerState, now: domain::TimestampMs) {
    super::memory_query::prepare(controller, now);
    let ids = controller
        .memory_imports
        .keys()
        .cloned()
        .collect::<Vec<_>>();
    for id in ids {
        let intent = &controller.memory_imports[&id];
        if intent.phase.terminal() {
            continue;
        }
        let expired = now.as_millis() >= intent.request.deadline_ms;
        let still_authorized = authorize(controller, &intent.request, now).is_ok();
        let due = intent
            .last_delivery_ms
            .is_none_or(|last| now.as_millis().saturating_sub(last) >= 1000);
        let exhausted = intent.deliveries >= 16;
        let intent = controller
            .memory_imports
            .get_mut(&id)
            .expect("stable retained command");
        if expired || !still_authorized || (exhausted && due) {
            intent.phase = ImportPhase::Unknown;
            intent.reason =
                "import deadline, delivery budget or current binding requires reconciliation"
                    .into();
        } else if due {
            intent.last_delivery_ms = Some(now.as_millis());
            intent.deliveries += 1;
        }
    }
}

/// Sends only a just-committed delivery after rechecking the existing exact-binding authority.
pub(crate) fn flush(
    controller: &ControllerState,
    prepared_at: domain::TimestampMs,
    now: domain::TimestampMs,
) {
    super::memory_query::flush(controller, prepared_at, now);
    for intent in controller.memory_imports.values() {
        if intent.phase.terminal()
            || intent.last_delivery_ms != Some(prepared_at.as_millis())
            || now.as_millis() >= intent.request.deadline_ms
            || authorize(controller, &intent.request, now).is_err()
        {
            continue;
        }
        let request = &intent.request;
        if let Err(error) = controller.bridge.deliver_memory_import(
            request.node_id.as_str(),
            integration::grpc::v0_4::MemoryImport {
                import_id: request.import_id.clone(),
                command_id: format!("memory-import-{}", request.import_id),
                consumer_provider_id: request.consumer_provider_id.clone(),
                manifest_json: serde_json::to_string(&request.manifest)
                    .expect("validated manifest"),
                mission_id: request.task_ref.mission_id().as_str().into(),
                group_id: request.group_id.as_str().into(),
                task_id: request.task_ref.task_id().as_str().into(),
                role_id: request.role_id.as_str().into(),
                execution_id: request.execution_id.clone(),
                ..Default::default()
            },
        ) {
            eprintln!(
                "Memory import {} delivery deferred: {error}",
                request.import_id
            );
        }
    }
}

/// Applies one exact current-session receipt before the application checkpoint and sequence ACK.
pub(crate) fn receipt(
    controller: &mut ControllerState,
    node_id: &str,
    receipt: &MemoryImportReceipt,
    received_at: domain::TimestampMs,
) -> Result<(), String> {
    let intent = controller
        .memory_imports
        .get_mut(&receipt.import_id)
        .ok_or("Memory receipt has no durable Controller intent")?;
    if intent.request.node_id.as_str() != node_id
        || receipt.command_id != format!("memory-import-{}", intent.request.import_id)
        || intent.deliveries == 0
        || received_at.as_millis() < intent.authorized_at_ms
        || intent
            .receipt_received_at_ms
            .is_some_and(|previous| received_at.as_millis() < previous)
    {
        return Err("Memory receipt does not match the authorized consumer command".into());
    }
    let phase = match MemoryImportReceiptStatus::try_from(receipt.status)
        .map_err(|error| error.to_string())?
    {
        MemoryImportReceiptStatus::MemoryImportPersisted => ImportPhase::Persisted,
        MemoryImportReceiptStatus::MemoryImportCompleted => ImportPhase::Completed,
        MemoryImportReceiptStatus::MemoryImportRejected => ImportPhase::Rejected,
        MemoryImportReceiptStatus::MemoryImportUnknown => ImportPhase::Unknown,
        MemoryImportReceiptStatus::Unspecified => {
            return Err("Memory receipt status is unspecified".into());
        }
    };
    if intent.phase.terminal() {
        if intent.phase == ImportPhase::Unknown {
            if intent.recovery_receipt.is_some_and(|prior| {
                matches!(prior, ImportPhase::Completed | ImportPhase::Rejected)
                    && prior != phase
                    && phase != ImportPhase::Persisted
            }) {
                return Err(
                    "Memory recovery receipt conflicts with retained terminal evidence".into(),
                );
            }
            if phase != ImportPhase::Persisted {
                intent.recovery_receipt = Some(phase);
                intent.receipt_received_at_ms = Some(received_at.as_millis());
            }
            return Ok(());
        }
        return if intent.phase == phase || phase == ImportPhase::Persisted {
            Ok(())
        } else {
            Err("Memory terminal receipt conflicts with durable outcome".into())
        };
    }
    if received_at.as_millis() >= intent.request.deadline_ms {
        intent.phase = ImportPhase::Unknown;
        intent.recovery_receipt = Some(phase);
        intent.reason = "Memory receipt arrived after the original authorization deadline".into();
    } else {
        intent.phase = phase;
        intent.reason = receipt.reason.chars().take(1024).collect();
    }
    intent.receipt_received_at_ms = Some(received_at.as_millis());
    Ok(())
}

/// Validates retained command identities before timer recovery can prepare another delivery.
pub(crate) fn validate_restored(controller: &ControllerState) -> Result<(), String> {
    super::memory_query::validate_restored(controller)?;
    if controller.memory_imports.len() > 4096 {
        return Err("Memory import checkpoint exceeds retained bound".into());
    }
    for (id, intent) in &controller.memory_imports {
        if let Some(author) = &intent.authorized_by
            && (author.trim().is_empty() || author.len() > 256)
        {
            return Err("Memory import original principal is invalid".into());
        }
        if let Some(stamp) = &intent.authority {
            stamp.membership.validate()?;
            let registry = controller
                .bridge
                .control()
                .memory_authorization_revision(stamp.revision)
                .filter(|r| r.registry_id == stamp.registry_id)
                .ok_or("Memory command lacks its original policy history")?;
            let policy = registry.policy(&intent.request.manifest, &stamp.tenant_id)?;
            if stamp.consumer.provider_id() != intent.request.consumer_provider_id
                || !policy.groups.iter().any(|g| {
                    g.group_id == intent.request.group_id
                        && g.operations
                            .contains(&domain::MemoryAccessOperation::Import)
                        && g.providers.iter().any(|p| p.matches(&stamp.consumer))
                })
            {
                return Err("Memory command original consumer policy evidence differs".into());
            }
            stamp
                .consumer
                .admit_import(&intent.request.manifest, &intent.request.node_id)
                .map_err(|e| e.to_string())?;
        }
        if id != &intent.request.import_id
            || id.trim().is_empty()
            || id.len() > 128
            || intent.deliveries > 16
            || intent.authorized_at_ms >= intent.request.deadline_ms
            || intent.request.deadline_ms - intent.authorized_at_ms > 3_600_000
            || intent
                .last_delivery_ms
                .is_some_and(|last| last < intent.authorized_at_ms)
            || intent
                .receipt_received_at_ms
                .is_some_and(|received| received < intent.authorized_at_ms)
            || intent.reason.len() > 4096
            || (intent.deliveries == 0) != intent.last_delivery_ms.is_none()
        {
            return Err(
                "Memory import checkpoint identity, timing or delivery fence is invalid".into(),
            );
        }
        intent
            .request
            .manifest
            .validate()
            .map_err(|error| error.to_string())?;
        if let Some(gate) = &intent.request.evidence_gate {
            gate.policy.validate().map_err(|error| error.to_string())?;
        }
    }
    Ok(())
}

/// Executes administrator admission with the same transaction, publication and recovery rules as Missions.
pub(crate) fn submit(
    controller: &Arc<Mutex<ControllerState>>,
    events: &state::SqliteEventLog,
    gate: &dyn crate::application::ApplicationWriteGate,
    clock: &impl Clock,
    body: &[u8],
    subject: &str,
) -> Result<serde_json::Value, String> {
    let request: MemoryImportRequest =
        serde_json::from_slice(body).map_err(|error| error.to_string())?;
    if subject.trim().is_empty() || subject.len() > 256 {
        return Err("invalid original Memory import principal".into());
    }
    let id = request.import_id.clone();
    let _guard = gate.lock()?;
    events.begin_batch().map_err(|error| error.to_string())?;
    let result = (|| {
        let mut live = controller
            .lock()
            .map_err(|_| "Controller authority is unavailable")?;
        let mut candidate = live.clone();
        let now = clock.now();
        if candidate
            .memory_imports
            .get(&id)
            .and_then(|i| i.authorized_by.as_deref())
            .is_some_and(|original| original != subject)
        {
            return Err("Memory import identity belongs to another authenticated principal".into());
        }
        let new = !candidate.memory_imports.contains_key(&id);
        admit(&mut candidate, request, now)?;
        if new {
            candidate
                .memory_imports
                .get_mut(&id)
                .expect("admitted original import")
                .authorized_by = Some(subject.into());
            let correlation = domain::CorrelationId::new("memory-import-authorization")
                .expect("static correlation");
            let value =
                serde_json::to_value(&candidate.memory_imports[&id]).map_err(|e| e.to_string())?;
            use sha2::{Digest as _, Sha256};
            let digest = format!(
                "sha256:{:x}",
                Sha256::digest(serde_json::to_vec(&value).map_err(|e| e.to_string())?)
            );
            let original = &candidate.memory_imports[&id].request;
            events.clone().append(
                now,
                &correlation,
                None,
                domain::EventPayload::MemoryCommandEvidence {
                    group_id: original.group_id.clone(),
                    task_ref: original.task_ref.clone(),
                    import_id: id.clone(),
                    command_id: format!("memory-import-{id}"),
                    generation: 0,
                    stage: "import_authorized".into(),
                    content_digest: digest,
                    principal: subject.into(),
                    received_at: now,
                },
            );
        }
        if let Some(error) = events.take_error().map_err(|e| e.to_string())? {
            return Err(error);
        }
        prepare(&mut candidate, now);
        let checkpoint = server_checkpoint_json(&candidate).map_err(|error| error.to_string())?;
        save_controller_checkpoint(events, &checkpoint).map_err(|error| error.to_string())?;
        events.commit_batch().map_err(|error| error.to_string())?;
        *live = candidate;
        // A fresh delivery-time clock may refuse a command whose authorization just expired.
        flush(&live, now, clock.now());
        serde_json::to_value(&live.memory_imports[&id]).map_err(|error| error.to_string())
    })();
    if result.is_err() {
        let _ = events.rollback_batch();
    }
    result
}
