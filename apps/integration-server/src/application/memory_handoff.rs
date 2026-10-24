//! Durable stopped-Group exchange; Control alone owns membership and physical commitments.

use crate::*;
use domain::{
    ExecutionGroupId, GroupMembershipVersion, MapArtifactManifest, MemoryArtifactManifest,
    MemorySelector,
};
use ports::EventSink;
use sha2::Digest as _;

/// Explicit original request; stable retries never acquire new members, policies or time.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct HandoffRequest {
    /// Closed command contract, independent of Node protocol negotiation.
    schema_version: String,
    /// Stable caller-owned command identity.
    handoff_id: String,
    /// Published original source; discovery alone grants no exchange permission.
    parent_selector: MemorySelector,
    /// Complete immutable new child; original owner and production provenance remain unchanged.
    child: MemoryArtifactManifest,
    /// Complete original Spatial manifest when this handoff starts from a typed map.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    typed_map: Option<MapArtifactManifest>,
    /// Group relinquishing its original content-use context.
    source_group_id: ExecutionGroupId,
    /// Exact Control topology at admission.
    source_membership: GroupMembershipVersion,
    /// Complete sorted original current source set, not an operator-selected subset.
    source_execution_ids: Vec<String>,
    /// Exact already committed receiving topology.
    target_membership: GroupMembershipVersion,
    /// Original retained policy identity.
    registry_id: String,
    /// Expected exact full-registry policy revision.
    registry_revision: u64,
    /// Absolute Controller-local exclusive deadline, at most five minutes after admission.
    deadline_ms: u64,
}

/// Closed stages retain failure ownership instead of interpreting a timeout as success.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub(crate) enum HandoffPhase {
    /// Source stop and original receiver/provider revoke evidence is incomplete.
    Stopping,
    /// Scoped child is committed; only original receiver preparation may run.
    Importing,
    /// Every frozen receiver has current bytes and a current-use grant.
    Ready,
    /// Explicit current-State revalidation committed before Execute delivery.
    Committed,
    /// Irreversible admission failure/expiry; ownership and original evidence remain retained.
    Manual,
}

/// Full immutable command and lifecycle receipts, never a second resource registry.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct HandoffRecord {
    /// Original exact HTTP command.
    request: HandoffRequest,
    /// Published original source bytes/metadata and lineage.
    parent: MemoryArtifactManifest,
    /// Original authenticated stable principal, independent of rotating TLS leaves.
    principal: String,
    /// Immutable shared tenant of both Groups.
    tenant_id: String,
    /// First actual Controller receive time, never refreshed on restore.
    admitted_at_ms: u64,
    /// Complete current receiving attempts frozen before any child import.
    target_execution_ids: Vec<String>,
    /// Exact original Runtime commands and generations for both ends.
    attempts_digest: String,
    /// Current workflow stage; original receipts survive readiness changes and delivery fences.
    phase: HandoffPhase,
    /// Original publication time in the same authority transaction as its catalog event.
    published_at_ms: Option<u64>,
    /// Original complete receiver proof digest, populated only by explicit commit.
    receiver_proof_digest: Option<String>,
    /// Complete original receiver receipts retained independently of later lease history compaction.
    receiver_proofs: BTreeMap<String, HandoffReceiverProof>,
    /// Exact first committed receive time.
    committed_at_ms: Option<u64>,
    /// Bounded diagnostic, never parsed to decide retry or release.
    reason: String,
}

/// Exact original receiving preparation and use acknowledgements at the committed decision.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct HandoffReceiverProof {
    /// Immutable original imports, configuration, availability observations and budgets.
    preparation: super::memory_preparation::MemoryPreparationRecord,
    /// Original current grant head and its complete provider proofs, before later renewal/pruning.
    use_record: super::memory_use::MemoryUseRecord,
}

/// Retains both original endpoints until the existing handoff has its actual committed receiver proof.
pub(crate) fn holds_catalog(
    controller: &ControllerState,
    selector: &domain::MemorySelector,
) -> bool {
    controller.memory_handoffs.values().any(|record| {
        record.phase != HandoffPhase::Committed
            && (record.parent.selector() == selector || record.request.child.selector() == selector)
    })
}

/// Returns the target from its immutable child scope without guessing a Mission topology.
fn target(record: &HandoffRecord) -> &ExecutionGroupId {
    let domain::MemoryScope::ExecutionGroup(target) = record.request.child.scope() else {
        unreachable!("validated scoped child")
    };
    target
}

/// Hashes canonical JSON values rather than transport formatting or certificate leaf identity.
fn digest(value: &impl Serialize) -> Result<String, String> {
    let canonical = serde_json::to_value(value).map_err(|error| error.to_string())?;
    Ok(format!(
        "sha256:{:x}",
        sha2::Sha256::digest(serde_json::to_vec(&canonical).map_err(|error| error.to_string())?)
    ))
}

/// Lists every currently bound original attempt and rejects a partial physical set.
fn members(controller: &ControllerState, group: &ExecutionGroupId) -> Result<Vec<String>, String> {
    let bound = controller
        .bridge
        .control()
        .group(group)
        .ok_or("handoff Group is absent")?;
    let expected = bound
        .task_executions()
        .map(|task| task.assignments().len())
        .sum::<usize>();
    let mut ids = controller
        .bridge
        .attempt_history()
        .into_iter()
        .filter(|attempt| {
            let command = attempt.command();
            command.group_id() == group
                && controller.bridge.current_task_attempt_id(
                    group,
                    command.task_ref(),
                    command.role_id(),
                ) == Some(attempt.execution_id())
                && bound
                    .task_execution(command.task_ref())
                    .is_some_and(|task| {
                        task.assignments().iter().any(|assignment| {
                            assignment.role_id() == command.role_id()
                                && assignment.node_id() == command.node_id()
                        })
                    })
        })
        .map(|attempt| attempt.execution_id().to_string())
        .collect::<Vec<_>>();
    ids.sort();
    if ids.is_empty() || ids.len() > 32 || ids.len() != expected {
        return Err("handoff requires the complete bounded current bound attempt set".into());
    }
    Ok(ids)
}

/// Retains original commands and generation evidence independently of mutable execution outcomes.
fn attempt_digest(controller: &ControllerState, ids: &[String]) -> Result<String, String> {
    let history = controller.bridge.attempt_history();
    let mut frozen = Vec::new();
    for id in ids {
        let attempt = history
            .iter()
            .find(|attempt| attempt.execution_id() == id)
            .ok_or("handoff attempt is absent")?;
        let command = attempt.command();
        let generation = controller
            .bridge
            .current_attempt_generation(command.group_id(), command.task_ref(), command.role_id())
            .ok_or("handoff generation is absent")?;
        frozen.push((id, command, generation));
    }
    digest(&frozen)
}

/// Rechecks immutable Control authority and complete frozen attempts, without renewing budgets.
fn current(
    controller: &ControllerState,
    record: &HandoffRecord,
    now: domain::TimestampMs,
) -> Result<(), String> {
    if now.as_millis() < record.admitted_at_ms || now.as_millis() >= record.request.deadline_ms {
        return Err("original handoff deadline expired or clock regressed".into());
    }
    authority_current(controller, record)
}

/// Rechecks exact retained authority independently of the caller's separately enforced time budget.
fn authority_current(controller: &ControllerState, record: &HandoffRecord) -> Result<(), String> {
    let registry = controller
        .bridge
        .control()
        .memory_authorization_registry()
        .ok_or("handoff policy unavailable")?;
    if registry.registry_id != record.request.registry_id
        || registry.revision != record.request.registry_revision
    {
        return Err("original handoff policy revision changed".into());
    }
    if let Some(map) = &record.request.typed_map {
        controller
            .bridge
            .control()
            .authorize_stopped_typed_map_handoff(
                map,
                &record.request.child,
                &record.request.source_group_id,
                &record.request.source_membership,
                &record.request.target_membership,
                &record.tenant_id,
                &record.principal,
            )
            .map_err(|error| error.to_string())?;
    } else {
        controller
            .bridge
            .control()
            .authorize_stopped_memory_handoff(
                &record.parent,
                &record.request.child,
                &record.request.source_group_id,
                &record.request.source_membership,
                &record.request.target_membership,
                &record.tenant_id,
                &record.principal,
            )
            .map_err(|error| error.to_string())?;
    }
    if members(controller, &record.request.source_group_id)? != record.request.source_execution_ids
        || members(controller, target(record))? != record.target_execution_ids
    {
        return Err("handoff complete source or target attempt set changed".into());
    }
    let mut all = record.request.source_execution_ids.clone();
    all.extend(record.target_execution_ids.clone());
    if attempt_digest(controller, &all)? != record.attempts_digest {
        return Err("handoff original command or attempt generation changed".into());
    }
    let mission = controller
        .bridge
        .control()
        .group(target(record))
        .ok_or("target absent")?
        .mission_id();
    if controller
        .orchestrator
        .execution(mission)
        .is_none_or(|execution| {
            execution.isolation().is_some()
                || execution.is_crash_loop_quarantined()
                || !matches!(
                    execution.lifecycle(),
                    orchestration::MissionExecutionLifecycle::Accepted
                        | orchestration::MissionExecutionLifecycle::Running
                )
        })
    {
        return Err("handoff target Mission is held, cancelling or terminal".into());
    }
    Ok(())
}

/// Demands every frozen source's independent stop and every original content fence.
fn source_stopped(controller: &ControllerState, record: &HandoffRecord) -> bool {
    let mission = controller
        .bridge
        .control()
        .group(&record.request.source_group_id)
        .map(|group| group.mission_id());
    mission.is_some_and(|mission| {
        !super::safety_stop::blocks_release(controller, mission)
            && !super::memory_use::blocks_release(controller, mission)
    }) && record
        .request
        .source_execution_ids
        .iter()
        .all(|id| super::safety_stop::attempt_confirmed(controller, id))
}

/// Captures only complete currently admissible original receiver observations and grant heads.
fn receiver_proof(
    controller: &ControllerState,
    record: &HandoffRecord,
    now: domain::TimestampMs,
) -> Result<BTreeMap<String, HandoffReceiverProof>, String> {
    let ready = super::memory_preparation::ready(controller, now);
    let state = controller.bridge.decision_state();
    let pending = controller.bridge.pending_dispatch_intents();
    let mut proofs = BTreeMap::new();
    for id in &record.target_execution_ids {
        let command = &pending
            .iter()
            .find(|intent| &intent.execution_id == id)
            .ok_or("handoff receiver already left the pending Execute set")?
            .command;
        if [
            domain::DecisionKind::Commit,
            domain::DecisionKind::Readiness,
        ]
        .iter()
        .any(|decision| {
            !controller
                .bridge
                .control()
                .bound_command_decision_allowed(&state, command, *decision, now)
        }) {
            return Err("handoff receiver current Control State is no longer eligible".into());
        }
        let permit = ready
            .get(id)
            .ok_or("handoff receiver still lacks current import/probe/use evidence")?;
        if &permit.preparation.group_id != target(record) {
            return Err("handoff receiver belongs to another Group".into());
        }
        proofs.insert(
            id.clone(),
            HandoffReceiverProof {
                preparation: controller
                    .memory_preparation
                    .records
                    .get(id)
                    .ok_or("receiver preparation absent")?
                    .clone(),
                use_record: controller
                    .memory_use
                    .records
                    .get(id)
                    .ok_or("receiver use authority absent")?
                    .clone(),
            },
        );
    }
    if serde_json::to_vec(&proofs)
        .map_err(|e| e.to_string())?
        .len()
        > 512 * 1024
    {
        return Err("handoff receiver proof exceeds the bounded record contract".into());
    }
    Ok(proofs)
}

/// Appends a typed complete-lineage transition under the caller's managed authority transaction.
fn audit(
    controller: &ControllerState,
    record: &HandoffRecord,
    now: domain::TimestampMs,
) -> Result<(), String> {
    let mut events = controller.bridge.event_sink().clone();
    events.append(
        now,
        &domain::CorrelationId::new("memory-handoff").expect("fixed correlation"),
        None,
        domain::EventPayload::MemoryHandoffTransition {
            handoff_id: record.request.handoff_id.clone(),
            source_group_id: record.request.source_group_id.clone(),
            target_group_id: target(record).clone(),
            parent: record.parent.clone(),
            child: Box::new(record.request.child.clone()),
            phase: serde_json::to_value(record.phase)
                .map_err(|e| e.to_string())?
                .as_str()
                .expect("enum string")
                .into(),
            content_digest: digest(record)?,
            principal: record.principal.clone(),
        },
    );
    if let Some(error) = events.take_error().map_err(|e| e.to_string())? {
        return Err(error.to_string());
    }
    Ok(())
}

/// Opens exactly one source/target exchange, retaining original source and receiving commitments.
pub(crate) fn admit(
    controller: &mut ControllerState,
    request: HandoffRequest,
    principal: &str,
    tenant: Option<&str>,
    now: domain::TimestampMs,
) -> Result<String, String> {
    if let Some(old) = controller.memory_handoffs.get(&request.handoff_id) {
        if old.request == request
            && old.principal == principal
            && tenant.is_none_or(|tenant| old.tenant_id == tenant)
        {
            return Ok(request.handoff_id);
        }
        return Err("handoff identity conflicts with its original request/principal/tenant".into());
    }
    let domain::MemoryScope::ExecutionGroup(target_id) = request.child.scope() else {
        return Err("handoff requires a scoped child".into());
    };
    if !matches!(
        request.schema_version.as_str(),
        "roboguide.memory-handoff/v0.1" | "roboguide.memory-handoff/v0.2"
    ) || request.typed_map.is_some() && request.schema_version != "roboguide.memory-handoff/v0.2"
        || request.handoff_id.is_empty()
        || request.handoff_id.len() > 128
        || !request
            .handoff_id
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, b'-' | b'_'))
        || principal.trim().is_empty()
        || principal.len() > 256
        || controller.memory_handoffs.len() >= 128
        || request.deadline_ms <= now.as_millis()
        || request.deadline_ms - now.as_millis() > 300_000
    {
        return Err("handoff requires bounded exact identity and original deadline".into());
    }
    if controller.memory_handoffs.values().any(|record| {
        &record.request.source_group_id == target_id
            || target(record) == target_id
            || record.request.source_group_id == request.source_group_id
            || target(record) == &request.source_group_id
            || record.request.child.selector() == request.child.selector()
    }) {
        return Err("Group or child already belongs to a retained handoff".into());
    }
    let catalog = controller
        .memory_preparation
        .catalog
        .as_ref()
        .ok_or("Memory federation unavailable")?;
    let (parent, map) = catalog
        .memory_source(&request.parent_selector)?
        .ok_or("published original handoff source unavailable")?;
    match (map, request.typed_map.as_ref()) {
        (Some(original), Some(provided)) if original == *provided => {}
        (None, None) if parent.kind() != domain::MemoryKind::Spatial => {}
        (Some(_), None) | (None, Some(_)) => {
            return Err("typed-map handoff requires its complete original manifest".into());
        }
        _ => return Err("typed-map handoff source manifest differs from catalog authority".into()),
    }
    if catalog.memory_source(request.child.selector())?.is_some() {
        return Err("handoff child selector is already published".into());
    }
    let tenant_id = controller
        .bridge
        .control()
        .group(&request.source_group_id)
        .and_then(|group| {
            controller
                .bridge
                .control()
                .mission_tenant(group.mission_id())
        })
        .unwrap_or("local")
        .to_string();
    if tenant.is_some_and(|tenant| tenant != tenant_id) {
        return Err("handoff belongs to another tenant".into());
    }
    if let Some(map) = &request.typed_map {
        controller
            .bridge
            .control()
            .authorize_stopped_typed_map_handoff(
                map,
                &request.child,
                &request.source_group_id,
                &request.source_membership,
                &request.target_membership,
                &tenant_id,
                principal,
            )
            .map_err(|error| error.to_string())?;
    } else {
        controller
            .bridge
            .control()
            .authorize_memory_handoff(&parent, &request.child, &tenant_id, principal)
            .map_err(|error| error.to_string())?;
    }
    let source_ids = members(controller, &request.source_group_id)?;
    if source_ids != request.source_execution_ids {
        return Err("handoff source set is partial or not canonical".into());
    }
    let target_execution_ids = members(controller, target_id)?;
    let pending = controller.bridge.pending_dispatch_intents();
    let mut uses_child = false;
    for id in &target_execution_ids {
        let intent = pending
            .iter()
            .find(|intent| &intent.execution_id == id)
            .ok_or("handoff target has already received Execute")?;
        if intent.command.memory_requirements().is_empty() {
            return Err("handoff target attempts must have explicit Memory prerequisites".into());
        }
        uses_child |= intent.command.memory_requirements().iter().any(|input| {
            input.selector == *request.child.selector() && input.matches_manifest(&request.child)
        });
    }
    if !uses_child {
        return Err("target accepted plan does not require the exact scoped child".into());
    }
    let mut all = source_ids;
    all.extend(target_execution_ids.clone());
    let record = HandoffRecord {
        attempts_digest: attempt_digest(controller, &all)?,
        request,
        parent,
        principal: principal.into(),
        tenant_id,
        admitted_at_ms: now.as_millis(),
        target_execution_ids,
        phase: HandoffPhase::Stopping,
        published_at_ms: None,
        receiver_proof_digest: None,
        receiver_proofs: BTreeMap::new(),
        committed_at_ms: None,
        reason: String::new(),
    };
    if serde_json::to_vec(&record)
        .map_err(|e| e.to_string())?
        .len()
        > 256 * 1024
    {
        return Err("original handoff metadata exceeds its bounded record contract".into());
    }
    current(controller, &record, now)?;
    let source = controller
        .bridge
        .control()
        .group(&record.request.source_group_id)
        .expect("checked source")
        .mission_id()
        .clone();
    if controller
        .orchestrator
        .execution(&source)
        .is_none_or(|execution| {
            execution.isolation().is_some()
                || execution.lifecycle() != orchestration::MissionExecutionLifecycle::Running
        })
    {
        return Err("handoff requires an unheld running source Mission".into());
    }
    super::safety_stop::admit_group_stop(
        controller,
        &record.request.source_group_id,
        now,
        record.request.deadline_ms - now.as_millis(),
    )?;
    if record
        .request
        .source_execution_ids
        .iter()
        .any(|id| !controller.safety_stops.contains_key(id))
    {
        return Err(
            "handoff requires an independent stop purpose for every original bound source attempt"
                .into(),
        );
    }
    super::memory_use::revoke_group(
        controller,
        &record.request.source_group_id,
        "explicit Group Memory handoff",
        now,
    )?;
    audit(controller, &record, now)?;
    let id = record.request.handoff_id.clone();
    controller.memory_handoffs.insert(id.clone(), record);
    Ok(id)
}

/// Holds and fences both sides after an irreversible failure; no new identity retries the exchange.
fn manual(
    controller: &mut ControllerState,
    record: &mut HandoffRecord,
    why: &str,
    now: domain::TimestampMs,
) -> Result<(), String> {
    record.phase = HandoffPhase::Manual;
    record.reason = why.chars().take(512).collect();
    let correlation =
        domain::CorrelationId::new("memory-handoff-manual").expect("fixed correlation");
    for group in [&record.request.source_group_id, target(record)] {
        let mission = controller
            .bridge
            .control()
            .group(group)
            .ok_or("handoff Group lost during manual hold")?
            .mission_id()
            .clone();
        if controller
            .orchestrator
            .execution(&mission)
            .is_some_and(|mission| {
                matches!(
                    mission.lifecycle(),
                    orchestration::MissionExecutionLifecycle::Accepted
                        | orchestration::MissionExecutionLifecycle::Running
                        | orchestration::MissionExecutionLifecycle::Cancelling
                )
            })
        {
            controller
                .orchestrator
                .hold_for_safety(
                    &mission,
                    now,
                    &correlation,
                    &mut controller.bridge.event_sink().clone(),
                )
                .map_err(|e| e.to_string())?;
        }
        super::safety_stop::admit_group_stop(controller, group, now, 10_000)?;
        super::memory_use::revoke_group(
            controller,
            group,
            "handoff requires manual reconciliation",
            now,
        )?;
    }
    audit(controller, record, now)
}

/// Advances only original evidence; source publication commits before any receiver import.
pub(crate) fn tick(
    controller: &mut ControllerState,
    now: domain::TimestampMs,
) -> Result<(), String> {
    let ids = controller
        .memory_handoffs
        .keys()
        .cloned()
        .collect::<Vec<_>>();
    for id in ids {
        let mut record = controller.memory_handoffs[&id].clone();
        if record.phase == HandoffPhase::Committed {
            if controller
                .bridge
                .pending_dispatch_intents()
                .iter()
                .any(|intent| record.target_execution_ids.contains(&intent.execution_id))
                && let Err(error) = current(controller, &record, now)
            {
                manual(controller, &mut record, &error, now)?;
                controller.memory_handoffs.insert(id, record);
            }
            continue;
        }
        if record.phase == HandoffPhase::Manual {
            continue;
        }
        if record.target_execution_ids.iter().any(|id| {
            controller
                .memory_preparation
                .records
                .get(id)
                .is_some_and(|preparation| {
                    matches!(
                        preparation.phase,
                        super::memory_preparation::MemoryPreparationPhase::Unknown
                            | super::memory_preparation::MemoryPreparationPhase::Revoking
                            | super::memory_preparation::MemoryPreparationPhase::Revoked
                    )
                })
                || controller
                    .memory_use
                    .records
                    .get(id)
                    .is_some_and(|use_record| use_record.revoked)
        }) {
            manual(
                controller,
                &mut record,
                "original handoff receiver preparation was fenced",
                now,
            )?;
            controller.memory_handoffs.insert(id, record);
            continue;
        }
        if let Err(error) = current(controller, &record, now) {
            manual(controller, &mut record, &error, now)?;
        } else if record.phase == HandoffPhase::Stopping && source_stopped(controller, &record) {
            let catalog = controller
                .memory_preparation
                .catalog
                .as_ref()
                .ok_or("Memory catalog unavailable")?;
            catalog.validate_application_memory_publication(&record.request.child)?;
            let artifact = record
                .parent
                .artifact()
                .ok_or("handoff source has no bytes")?;
            let verified = controller
                .verifier_artifact_store
                .as_ref()
                .ok_or("handoff CAS unavailable")?
                .verify_artifact(artifact.content_digest().as_str(), artifact.byte_size());
            if let Err(error) = verified {
                manual(
                    controller,
                    &mut record,
                    &format!("original handoff bytes are unavailable: {error}"),
                    now,
                )?;
                controller.memory_handoffs.insert(id, record);
                continue;
            }
            let reference_events = catalog.publication_reference_events(
                &record.request.child,
                std::slice::from_ref(&record.parent),
                None,
                &artifact_http::ArtifactPublicationSource {
                    tenant_id: record.tenant_id.clone(),
                    principal_id: record.principal.clone(),
                    authority_digest: digest(&serde_json::json!({
                        "request": record.request,
                        "attempts": record.attempts_digest,
                        "principal": record.principal,
                        "tenant": record.tenant_id,
                        "admitted_at_ms": record.admitted_at_ms,
                    }))?,
                },
                now,
            )?;
            for event in reference_events {
                controller.bridge.event_sink().clone().append(
                    now,
                    &domain::CorrelationId::new("memory-handoff-publication")
                        .expect("fixed correlation"),
                    None,
                    event,
                );
            }
            controller.bridge.event_sink().clone().append(
                now,
                &domain::CorrelationId::new("memory-handoff-publication")
                    .expect("fixed correlation"),
                None,
                domain::EventPayload::MemoryManifestPublished {
                    manifest: record.request.child.clone(),
                },
            );
            record.published_at_ms = Some(now.as_millis());
            record.phase = HandoffPhase::Importing;
            audit(controller, &record, now)?;
        } else if matches!(record.phase, HandoffPhase::Importing | HandoffPhase::Ready) {
            let phase = if receiver_proof(controller, &record, now).is_ok() {
                HandoffPhase::Ready
            } else {
                HandoffPhase::Importing
            };
            if phase != record.phase {
                record.phase = phase;
                audit(controller, &record, now)?;
            }
        }
        controller.memory_handoffs.insert(id, record);
    }
    Ok(())
}

/// Explicitly commits current receiver proofs; exact retries retain their original receipt.
fn commit(
    controller: &mut ControllerState,
    id: &str,
    principal: &str,
    tenant: Option<&str>,
    now: domain::TimestampMs,
) -> Result<String, String> {
    let mut record = controller
        .memory_handoffs
        .get(id)
        .ok_or("unknown handoff")?
        .clone();
    if record.principal != principal || tenant.is_some_and(|tenant| tenant != record.tenant_id) {
        return Err("handoff commit principal/tenant differs from original".into());
    }
    if record.committed_at_ms.is_some() {
        return Ok(id.into());
    }
    if record.phase != HandoffPhase::Ready {
        return Err("handoff is not ready for explicit commit".into());
    }
    current(controller, &record, now)?;
    if !source_stopped(controller, &record) {
        return Err("handoff original stop/revoke barrier is incomplete".into());
    }
    record.receiver_proofs = receiver_proof(controller, &record, now)?;
    record.receiver_proof_digest = Some(digest(&record.receiver_proofs)?);
    record.phase = HandoffPhase::Committed;
    record.committed_at_ms = Some(now.as_millis());
    audit(controller, &record, now)?;
    controller.memory_handoffs.insert(id.into(), record);
    Ok(id.into())
}

/// Fences all new target binding while an original handoff is pending; source never auto-resumes.
pub(crate) fn holds_binding(controller: &ControllerState, group: &ExecutionGroupId) -> bool {
    controller.memory_handoffs.values().any(|record| {
        &record.request.source_group_id == group
            || target(record) == group
                && (record.phase != HandoffPhase::Committed
                    || controller
                        .bridge
                        .pending_dispatch_intents()
                        .iter()
                        .any(|intent| record.target_execution_ids.contains(&intent.execution_id)))
    })
}

/// Stops Execute for both incomplete handoffs and any attempt outside the frozen original target set.
pub(crate) fn holds_dispatch(controller: &ControllerState, group: &ExecutionGroupId) -> bool {
    controller.memory_handoffs.values().any(|record| {
        &record.request.source_group_id == group
            || target(record) == group && record.phase != HandoffPhase::Committed
    }) || controller.memory_handoffs.values().any(|record| {
        target(record) == group
            && record.phase == HandoffPhase::Committed
            && controller
                .bridge
                .pending_dispatch_intents()
                .iter()
                .any(|intent| record.target_execution_ids.contains(&intent.execution_id))
            && authority_current(controller, record).is_err()
    }) || controller.memory_handoffs.values().any(|record| {
        target(record) == group
            && controller
                .bridge
                .pending_dispatch_intents()
                .iter()
                .any(|intent| {
                    intent.command.group_id() == group
                        && intent
                            .command
                            .memory_requirements()
                            .iter()
                            .any(|r| &r.selector == record.request.child.selector())
                        && !record.target_execution_ids.contains(&intent.execution_id)
                        && !original_receivers_retired(controller, record)
                })
    })
}

/// Requires every original first-use receiver to relinquish its exact context before later members.
/// Later Tasks still require fresh Control binding, current policy and new preparation/use authority.
fn original_receivers_retired(controller: &ControllerState, record: &HandoffRecord) -> bool {
    if record.phase != HandoffPhase::Committed {
        return false;
    }
    let Some(group) = controller.bridge.control().group(target(record)) else {
        return false;
    };
    let history = controller.bridge.attempt_history();
    record.target_execution_ids.iter().all(|id| {
        let Some(attempt) = history.iter().find(|attempt| attempt.execution_id() == id) else {
            return false;
        };
        let command = attempt.command();
        let bound = group
            .task_execution(command.task_ref())
            .is_some_and(|task| {
                task.assignments().iter().any(|assignment| {
                    assignment.role_id() == command.role_id()
                        && assignment.node_id() == command.node_id()
                })
            })
            && controller.bridge.current_task_attempt_id(
                target(record),
                command.task_ref(),
                command.role_id(),
            ) == Some(id.as_str());
        !bound
            && !super::memory_use::blocks_attempt_release(controller, id)
            && (attempt.status() == runtime::ExecutionStatus::Completed
                || super::safety_stop::attempt_confirmed(controller, id))
    })
}

/// Refuses receiving identities before commit or before all original contexts are retired.
pub(crate) fn authorize_input(
    controller: &ControllerState,
    execution: &str,
    selector: &MemorySelector,
    now: domain::TimestampMs,
) -> Result<(), String> {
    for record in controller
        .memory_handoffs
        .values()
        .filter(|record| record.request.child.selector() == selector)
    {
        if !matches!(
            record.phase,
            HandoffPhase::Importing | HandoffPhase::Ready | HandoffPhase::Committed
        ) || !record.target_execution_ids.iter().any(|id| id == execution)
            && !original_receivers_retired(controller, record)
        {
            return Err("handoff child is fenced for this receiving attempt".into());
        }
        if record.phase != HandoffPhase::Committed {
            current(controller, record, now)?;
            if !source_stopped(controller, record) {
                return Err("handoff source stop/revoke evidence is incomplete".into());
            }
        }
    }
    Ok(())
}

/// Identifies only the retained protected parent of a current original child import.
pub(crate) fn permits_parent_reference(
    controller: &ControllerState,
    protected: &MemoryArtifactManifest,
    import_id: &str,
    now: domain::TimestampMs,
) -> Result<bool, String> {
    let Some(import) = controller.memory_imports.get(import_id) else {
        return Ok(false);
    };
    for record in controller.memory_handoffs.values() {
        if &record.parent != protected || record.request.child != import.request.manifest {
            continue;
        }
        let original_receiver = record
            .target_execution_ids
            .contains(&import.request.execution_id);
        let later_receiver = record.phase == HandoffPhase::Committed
            && original_receivers_retired(controller, record);
        if !matches!(
            record.phase,
            HandoffPhase::Importing | HandoffPhase::Ready | HandoffPhase::Committed
        ) || now.as_millis() < record.admitted_at_ms
            || original_receiver && now.as_millis() >= record.request.deadline_ms
            || !original_receiver && !later_receiver
            || &import.request.group_id != target(record)
            || !record
                .request
                .source_execution_ids
                .iter()
                .all(|id| super::safety_stop::attempt_confirmed(controller, id))
        {
            return Err("original scoped parent exchange is no longer admissible".into());
        }
        if record.phase != HandoffPhase::Committed {
            current(controller, record, now)?;
        }
        return Ok(true);
    }
    Ok(false)
}

/// Adds the original handoff deadline to each receiver's final per-send Memory permit.
pub(crate) fn constrain_permits(
    controller: &ControllerState,
    permits: &mut BTreeMap<String, orchestration::MemoryDispatchPermit>,
) {
    for record in controller.memory_handoffs.values() {
        for id in &record.target_execution_ids {
            if let Some(permit) = permits.get_mut(id) {
                permit.expires_at = permit
                    .expires_at
                    .min(domain::TimestampMs::new(record.request.deadline_ms));
            }
        }
    }
}

/// Installs only already committed manifest events while the outer application writer is held.
pub(crate) fn install_catalog(controller: &ControllerState) -> Result<(), String> {
    let Some(catalog) = controller.memory_preparation.catalog.as_ref() else {
        return Ok(());
    };
    for record in controller.memory_handoffs.values() {
        if let Some(time) = record.published_at_ms {
            catalog.install_application_memory_publication(
                &record.request.child,
                domain::TimestampMs::new(time),
            )?;
        }
    }
    Ok(())
}

/// Checks structural history on restart without manufacturing current receiving or stop authority.
pub(crate) fn validate_restored(controller: &ControllerState) -> Result<(), String> {
    if controller.memory_handoffs.len() > 128 {
        return Err("handoff history bound exceeded".into());
    }
    for (id, record) in &controller.memory_handoffs {
        if serde_json::to_vec(record).map_err(|e| e.to_string())?.len() > 768 * 1024 {
            return Err("restored handoff record exceeds its byte bound".into());
        }
        record.parent.validate().map_err(|e| e.to_string())?;
        record.request.child.validate().map_err(|e| e.to_string())?;
        if !matches!(
            record.request.child.scope(),
            domain::MemoryScope::ExecutionGroup(_)
        ) {
            return Err("restored handoff lacks its original scoped child".into());
        }
        record.request.source_membership.validate()?;
        record.request.target_membership.validate()?;
        let original_parent = serde_json::to_value(&record.parent).map_err(|e| e.to_string())?;
        let mut normalized_child =
            serde_json::to_value(&record.request.child).map_err(|e| e.to_string())?;
        for key in ["selector", "scope", "created_at"] {
            normalized_child[key] = original_parent[key].clone();
        }
        if normalized_child != original_parent
            || record.parent.selector() != &record.request.parent_selector
            || record.parent.selector() == record.request.child.selector()
            || !matches!(record.parent.scope(),domain::MemoryScope::ExecutionGroup(group) if group==&record.request.source_group_id)
            || record.parent.created_at() > record.request.child.created_at()
            || (record.request.typed_map.is_none()
                && record.parent.kind() == domain::MemoryKind::Spatial)
            || record.parent.visibility() != domain::MemoryVisibility::Exchangeable
            || target(record) == &record.request.source_group_id
        {
            return Err("restored handoff immutable source lineage changed".into());
        }
        if id != &record.request.handoff_id
            || !matches!(
                record.request.schema_version.as_str(),
                "roboguide.memory-handoff/v0.1" | "roboguide.memory-handoff/v0.2"
            )
            || record.request.typed_map.is_some()
                && record.request.schema_version != "roboguide.memory-handoff/v0.2"
            || record.admitted_at_ms >= record.request.deadline_ms
            || record.request.deadline_ms - record.admitted_at_ms > 300_000
            || record.target_execution_ids.is_empty()
            || record.target_execution_ids.len() > 32
            || record
                .target_execution_ids
                .windows(2)
                .any(|ids| ids[0] >= ids[1])
            || record.request.source_execution_ids.is_empty()
            || record.request.source_execution_ids.len() > 32
            || record
                .request
                .source_execution_ids
                .windows(2)
                .any(|ids| ids[0] >= ids[1])
            || !matches!(
                record.request.child.scope(),
                domain::MemoryScope::ExecutionGroup(_)
            )
            || record.principal.is_empty()
            || record.reason.len() > 2048
        {
            return Err("invalid original handoff history".into());
        }
        if record.phase == HandoffPhase::Committed && record.committed_at_ms.is_none()
            || record.committed_at_ms.is_some() != record.receiver_proof_digest.is_some()
            || record.committed_at_ms.is_some()
                && !matches!(record.phase, HandoffPhase::Committed | HandoffPhase::Manual)
            || record.committed_at_ms.is_some_and(|time| {
                time < record.admitted_at_ms || time >= record.request.deadline_ms
            })
            || record.published_at_ms.is_some_and(|time| {
                time < record.admitted_at_ms || time >= record.request.deadline_ms
            })
            || matches!(
                record.phase,
                HandoffPhase::Importing | HandoffPhase::Ready | HandoffPhase::Committed
            ) && record.published_at_ms.is_none()
        {
            return Err("handoff phase and original receipts conflict".into());
        }
        if record.committed_at_ms.is_some() {
            validate_receiver_proofs(record)?;
        } else if !record.receiver_proofs.is_empty()
            || record.receiver_proof_digest.is_some()
            || record.committed_at_ms.is_some()
        {
            return Err("uncommitted handoff contains a fabricated receiver commit".into());
        }
        if record
            .request
            .source_execution_ids
            .iter()
            .any(|id| !controller.safety_stops.contains_key(id))
            || record.published_at_ms.is_some() && !source_stopped(controller, record)
        {
            return Err("restored handoff lost its original complete stop/revoke barrier".into());
        }
        let control = controller.bridge.control();
        for group in [&record.request.source_group_id, target(record)] {
            let bound = control
                .group(group)
                .ok_or("restored handoff Group absent")?;
            let expected_membership = if group == &record.request.source_group_id {
                &record.request.source_membership
            } else {
                &record.request.target_membership
            };
            if bound.membership_version() != Some(expected_membership) {
                return Err("restored handoff Group membership changed".into());
            }
            let mission = bound.mission_id();
            if control.mission_tenant(mission).unwrap_or("local") != record.tenant_id {
                return Err("restored handoff immutable tenant changed".into());
            }
        }
        let original = control
            .memory_authorization_revision(record.request.registry_revision)
            .ok_or("handoff original policy unavailable")?;
        if original.registry_id != record.request.registry_id {
            return Err("handoff original registry changed".into());
        }
        let source_handoff = if let Some(map) = &record.request.typed_map {
            if domain::map_memory_manifest(map).map_err(|e| e.to_string())? != record.parent {
                return Err("restored typed-map handoff projection changed".into());
            }
            if map.source_mission_id()
                != &control
                    .group(&record.request.source_group_id)
                    .ok_or("restored handoff source Group absent")?
                    .mission_id()
                    .clone()
                || record.parent.source_mission_id() != record.request.child.source_mission_id()
            {
                return Err("restored typed-map handoff source Mission changed".into());
            }
            original
                .typed_map_policy(map, &record.tenant_id)?
                .principals
                .get(&record.principal)
                .is_some_and(|ops| ops.contains(&domain::MemoryAccessOperation::Handoff))
        } else {
            true
        };
        let parent_policy = if record.request.typed_map.is_some() {
            None
        } else {
            Some(original.policy(&record.parent, &record.tenant_id)?)
        };
        let child_policy = original.policy(&record.request.child, &record.tenant_id)?;
        if !source_handoff
            || !parent_policy.as_ref().is_none_or(|policy| {
                policy
                    .principals
                    .get(&record.principal)
                    .is_some_and(|ops| ops.contains(&domain::MemoryAccessOperation::Handoff))
            })
            || !child_policy.groups.iter().any(|grant| {
                &grant.group_id == target(record)
                    && [
                        domain::MemoryAccessOperation::Read,
                        domain::MemoryAccessOperation::Import,
                    ]
                    .iter()
                    .all(|op| grant.operations.contains(op))
            })
        {
            return Err("restored handoff lacks original explicit source/target policy".into());
        }
    }
    Ok(())
}

/// Revalidates complete original commit evidence after grant pruning, restart or policy revocation.
fn validate_receiver_proofs(record: &HandoffRecord) -> Result<(), String> {
    if digest(&record.receiver_proofs)?.as_str()
        != record
            .receiver_proof_digest
            .as_deref()
            .ok_or("handoff proof digest unavailable")?
        || record.receiver_proofs.keys().cloned().collect::<Vec<_>>() != record.target_execution_ids
        || serde_json::to_vec(&record.receiver_proofs)
            .map_err(|e| e.to_string())?
            .len()
            > 512 * 1024
    {
        return Err("handoff original receiver proof digest or set changed".into());
    }
    let committed = record
        .committed_at_ms
        .ok_or("handoff original commit time absent")?;
    for (id, proof) in &record.receiver_proofs {
        let preparation = proof
            .preparation
            .preparation
            .as_ref()
            .ok_or("handoff original receiver context absent")?;
        preparation.validate()?;
        if id != &preparation.execution_id
            || proof.preparation.execution_id != *id
            || proof.use_record.execution_id != *id
            || proof.preparation.phase != super::memory_preparation::MemoryPreparationPhase::Ready
            || &preparation.group_id != target(record)
            || preparation.membership != record.request.target_membership
            || proof.preparation.deadline_ms <= committed
            || proof.preparation.admitted_at_ms > committed
            || proof
                .preparation
                .available_until_ms
                .is_none_or(|until| until <= committed)
            || proof.use_record.revoked
        {
            return Err("handoff original receiver context, ownership or freshness changed".into());
        }
        for delivery in proof.preparation.controls.values() {
            delivery.command.validate()?;
            if let Some(receipt) = &delivery.receipt {
                receipt.validate(&delivery.command, preparation)?;
            }
        }
        let grant = proof
            .use_record
            .controls
            .values()
            .find(|delivery| {
                delivery.command.generation == proof.use_record.generation
                    && delivery.command.operation == domain::MemoryUseOperation::Grant
            })
            .ok_or("handoff original current grant absent")?;
        grant.command.validate_preparation(preparation)?;
        let receipt = grant
            .receipt
            .as_ref()
            .ok_or("handoff original use acknowledgement absent")?;
        receipt.validate(&grant.command, preparation)?;
        if receipt.status != domain::MemoryUseStatus::Granted
            || grant.received_at_ms.is_none_or(|time| time > committed)
            || grant
                .issued_at_ms
                .saturating_add(grant.command.valid_for_ms)
                <= committed
            || proof.preparation.configuration_digest.as_deref()
                != Some(receipt.configuration_digest.as_str())
        {
            return Err("handoff original use grant was not current at commit".into());
        }
    }
    Ok(())
}

/// Returns bounded immutable tenant-filtered lineage and progress, with a real page cursor.
pub(crate) fn view(
    controller: &ControllerState,
    tenant: Option<&str>,
    after: Option<&str>,
    limit: usize,
) -> serde_json::Value {
    let mut records = Vec::new();
    let mut bytes = 0;
    let mut next = None;
    for (id, record) in &controller.memory_handoffs {
        if tenant.is_some_and(|tenant| tenant != record.tenant_id)
            || after.is_some_and(|after| id.as_str() <= after)
        {
            continue;
        }
        let value = serde_json::json!({"handoff_id":id,"record":record,"source_barrier_complete":source_stopped(controller,record)});
        let size = serde_json::to_vec(&value).map_or(usize::MAX, |bytes| bytes.len());
        if records.len() >= limit.clamp(1, 100) || bytes + size > 1024 * 1024 {
            break;
        }
        records.push(value);
        bytes += size;
        next = Some(id);
    }
    serde_json::json!({"schema_version":"roboguide.memory-handoff-view/v0.1","records":records,"next_after":next,"command_receipt_is_stop_proof":false})
}

/// Aggregates only fixed lifecycle counts, without high-cardinality tenant/Group/attempt labels.
pub(crate) fn metrics(controller: &ControllerState) -> (usize, usize) {
    (
        controller
            .memory_handoffs
            .values()
            .filter(|record| {
                matches!(
                    record.phase,
                    HandoffPhase::Stopping | HandoffPhase::Importing | HandoffPhase::Ready
                )
            })
            .count(),
        controller
            .memory_handoffs
            .values()
            .filter(|record| record.phase == HandoffPhase::Manual)
            .count(),
    )
}

/// Journals original admission or explicit commit under actual current transport authorization.
#[allow(clippy::too_many_arguments)]
pub(crate) fn submit(
    controller: &Arc<Mutex<ControllerState>>,
    events: &state::SqliteEventLog,
    gate: &dyn ApplicationWriteGate,
    clock: &impl Clock,
    body: &[u8],
    principal: &str,
    tenant: Option<&str>,
    commit_id: Option<&str>,
) -> Result<serde_json::Value, String> {
    let request = if commit_id.is_none() {
        Some(serde_json::from_slice::<HandoffRequest>(body).map_err(|e| e.to_string())?)
    } else {
        if serde_json::from_slice::<serde_json::Value>(body).map_err(|e| e.to_string())?
            != serde_json::json!({})
        {
            return Err("handoff commit body must be the closed empty object".into());
        }
        None
    };
    let _guard = gate.lock()?;
    events.begin_batch().map_err(|e| e.to_string())?;
    let result = (|| {
        let live = controller
            .lock()
            .map_err(|_| "handoff authority unavailable")?;
        let mut candidate = live.clone();
        drop(live);
        let now = clock.now();
        let id = if let Some(id) = commit_id {
            commit(&mut candidate, id, principal, tenant, now)?
        } else {
            admit(
                &mut candidate,
                request.expect("parsed request"),
                principal,
                tenant,
                now,
            )?
        };
        let checkpoint = server_checkpoint_json(&candidate).map_err(|e| e.to_string())?;
        save_controller_checkpoint(events, &checkpoint).map_err(|e| e.to_string())?;
        events.commit_batch().map_err(|e| e.to_string())?;
        let mut live = controller
            .lock()
            .map_err(|_| "handoff authority unavailable")?;
        *live = candidate;
        if let Err(error) = flush_unisolated_commands(&mut live, clock) {
            eprintln!("committed handoff command delivery deferred: {error}");
        }
        Ok(
            serde_json::json!({"schema_version":"roboguide.memory-handoff-admission/v0.1","handoff_id":id,"record":live.memory_handoffs[&id]}),
        )
    })();
    if result.is_err() {
        let _ = events.rollback_batch();
    }
    result
}

#[cfg(test)]
mod tests;
