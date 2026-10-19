//! Durable operation-owned stop evidence, Mission holds and lifecycle release fences.

use crate::*;
use domain::{SafetyStopIdentity, SafetyStopPhase, SafetyStopRecord, TaskRef, TimestampMs};
use sha2::{Digest, Sha256};

/// Bounded observation emitted by the operation's registered LocalSystem, never by Control.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct SafeStateObservation {
    /// Exact physical attempt being observed.
    execution_id: String,
    /// Frozen attempt generation.
    execution_generation: u64,
    /// Exact stop command observed by the provider.
    command_id: String,
    /// Provider-observed safe state, separate from command acceptance.
    safe_state: bool,
    /// Software-process observations cannot establish device stopping.
    assurance: domain::SafetyAssurance,
}

/// One fixed State export may observe multiple independent local handles without changing its object key.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SafeStateBatch {
    /// Bounded per-attempt observations from the same current provider session.
    observations: Vec<SafeStateObservation>,
}

/// Prior v27 purpose retained only as unverified historical evidence during migration.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct LegacySafetyStopPurpose {
    /// Original Group.
    group_id: domain::ExecutionGroupId,
    /// Original Mission-scoped Task.
    task_ref: TaskRef,
    /// Original logical Role.
    role_id: domain::RoleId,
    /// Original physical attempt.
    execution_id: String,
    /// Original generation.
    generation: u64,
    /// Original issuing epoch.
    controller_epoch: String,
    /// Unverified old policy label.
    policy_digest: String,
    /// Original receive time.
    requested_at: TimestampMs,
    /// Original nonrenewable deadline.
    deadline: TimestampMs,
    /// Prior phase, which does not grant new authority.
    phase: SafetyStopPhase,
    /// Original Node.
    node_id: domain::NodeId,
    /// Original source session.
    session_id: String,
    /// Historical terminal time.
    stopped_at: Option<TimestampMs>,
    /// Historical unverified safe time.
    safe_at: Option<TimestampMs>,
}

/// Exact retained evidence used for a confirmation, independent of later State projection replacement.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct SafetyObservationProof {
    /// Complete immutable source evidence allows restored proofs to be checked again.
    record: domain::StateRecord,
    /// Exact source-aware State channel.
    key: domain::StateRecordKey,
    /// Current provider session, never inferred from a later incoming message.
    source_epoch: String,
    /// Original Controller receive time.
    received_at: TimestampMs,
    /// Original receive-relative validity.
    valid_for_ms: u64,
    /// Original source confidence, without a numeric default.
    confidence_millionths: Option<u32>,
    /// State sequence that carried this observation.
    sequence: u64,
    /// Digest of the complete source record.
    record_digest: String,
    /// Exact attempt-specific observation extracted from that record.
    observation: SafeStateObservation,
}

/// Durable typed stop purpose; Runtime retains command and attempt authority.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct SafetyStopPurpose {
    /// Generic stop state machine and immutable execution/policy identity.
    record: SafetyStopRecord,
    /// Exact current physical owner.
    node_id: domain::NodeId,
    /// Frozen session; missing legacy/session evidence never grants confirmation.
    session_id: Option<String>,
    /// Exact operation-owned State declaration admitted before the stop.
    export: Option<domain::StateExportDescriptor>,
    /// Deployment-owned operation support, including assurance and explicit confidence policy.
    support: Option<domain::OperationSafetySupport>,
    /// Frozen source/schema/freshness/conflict gate, not supplied by observations.
    policy: Option<state::DecisionEvidencePolicy>,
    /// Exact Cancel identity in the existing Runtime outbox.
    command_id: String,
    /// First durable command receipt receive time.
    accepted_at: Option<TimestampMs>,
    /// First Runtime terminal stop receive time.
    stopped_at: Option<TimestampMs>,
    /// First admitted safe-state receive time.
    safe_at: Option<TimestampMs>,
    /// Digest of the accepted Node receipt.
    receipt_digest: Option<String>,
    /// Exact evidence supporting confirmation.
    safe_proof: Option<SafetyObservationProof>,
    /// Legacy unverified evidence retained for manual reconciliation, never automatic release.
    legacy_evidence: Option<serde_json::Value>,
    /// Bounded operator guidance; never parsed to make a decision.
    reason: String,
}

impl SafetyStopPurpose {
    /// Identifies current typed authority so old wrapper versions cannot hide it.
    pub(crate) fn is_current_authority(&self) -> bool {
        self.legacy_evidence.is_none()
    }
}

/// Restores typed purposes or retains legacy strings as manual, unverified evidence.
pub(crate) fn deserialize_purposes<'de, D: serde::Deserializer<'de>>(
    deserializer: D,
) -> Result<BTreeMap<String, SafetyStopPurpose>, D::Error> {
    use serde::de::Error;
    let values = BTreeMap::<String, serde_json::Value>::deserialize(deserializer)?;
    if values.len() > 8192 {
        return Err(D::Error::custom("stop purpose bound exceeded"));
    }
    values
        .into_iter()
        .map(|(id, value)| {
            if value.get("record").is_some() {
                return serde_json::from_value(value)
                    .map(|purpose| (id, purpose))
                    .map_err(D::Error::custom);
            }
            let legacy: LegacySafetyStopPurpose =
                serde_json::from_value(value.clone()).map_err(D::Error::custom)?;
            if legacy.session_id.is_empty() || legacy.execution_id != id {
                return Err(D::Error::custom("legacy stop identity/session is invalid"));
            }
            let identity = SafetyStopIdentity {
                group_id: legacy.group_id,
                task_ref: legacy.task_ref,
                role_id: legacy.role_id,
                execution_id: id.clone(),
                execution_generation: legacy.generation,
                controller_epoch: legacy.controller_epoch,
                safety_policy_digest: legacy.policy_digest,
            };
            let mut record = SafetyStopRecord::new(
                identity,
                legacy.requested_at,
                legacy.deadline,
                "legacy-unverified",
                digest(&value).map_err(D::Error::custom)?,
            )
            .map_err(D::Error::custom)?;
            record.phase = SafetyStopPhase::ManualEscalation;
            let previous_phase = legacy.phase;
            Ok((
                id.clone(),
                SafetyStopPurpose {
                    record,
                    node_id: legacy.node_id,
                    session_id: Some(legacy.session_id),
                    export: None,
                    support: None,
                    policy: None,
                    command_id: format!("cancel-{id}"),
                    accepted_at: None,
                    stopped_at: legacy.stopped_at,
                    safe_at: legacy.safe_at,
                    receipt_digest: None,
                    safe_proof: None,
                    legacy_evidence: Some(value),
                    reason: format!("legacy {previous_phase:?} lacks verified safety authority"),
                },
            ))
        })
        .collect()
}

/// Hashes immutable typed evidence using its deterministic serde representation.
fn digest(value: &impl Serialize) -> Result<String, String> {
    let bytes = serde_json::to_vec(value).map_err(|error| error.to_string())?;
    Ok(format!("sha256:{:x}", Sha256::digest(bytes)))
}

/// Freezes only a declared canonical operation's exact registered safety provider.
fn safety_declaration(
    controller: &ControllerState,
    command: &domain::ExecutionCommand,
) -> Option<(
    domain::StateExportDescriptor,
    domain::OperationSafetySupport,
    state::DecisionEvidencePolicy,
)> {
    let registration = controller
        .bridge
        .state()
        .node(command.node_id())?
        .registration();
    let owner = registration.operation_owner(command.intent().operation())?;
    let system = registration
        .local_systems()
        .iter()
        .find(|system| system.id() == owner)?;
    let profile = domain::ExecutionSafetyProfile::from_metadata(
        system
            .metadata()
            .get(domain::EXECUTION_SAFETY_METADATA_KEY)?,
    )
    .ok()?;
    let support = profile
        .operations
        .into_iter()
        .find(|support| &support.operation == command.intent().operation())?;
    let export = registration
        .state_exports()
        .iter()
        .find(|export| {
            export.export_id() == support.safe_state_export_id
                && export.local_system_id() == owner
                && export.semantic() == domain::StateSemantic::Observed
                && export.payload_schema() == domain::SAFE_STATE_OBSERVATION_SCHEMA
        })?
        .clone();
    let mut policy = state::DecisionEvidencePolicy::new(
        format!("safety:{}:{}", owner, support.operation),
        1,
        export.object().clone(),
        export.export_id(),
        BTreeSet::from([domain::StateSemantic::Observed]),
        BTreeSet::from([domain::StateSource::Node {
            node_id: command.node_id().clone(),
            local_system_id: owner.clone(),
        }]),
        support.max_evidence_age_ms.min(export.valid_for_ms()),
        support.min_confidence_millionths,
        BTreeSet::from([state::DecisionKind::Readiness]),
        state::ConflictReducer::RequireEquivalent,
    )
    .ok()?;
    policy.payload_schema = Some(domain::SAFE_STATE_OBSERVATION_SCHEMA.into());
    Some((export, support, policy))
}

/// Detects current nonterminal physical work before any whole-Group terminal release decision.
pub(crate) fn has_live_group_attempts(
    controller: &ControllerState,
    group_id: &domain::ExecutionGroupId,
) -> bool {
    controller
        .bridge
        .attempt_history()
        .into_iter()
        .any(|attempt| {
            let command = attempt.command();
            command.group_id() == group_id
                && !attempt.status().is_terminal()
                && controller.bridge.current_task_attempt_id(
                    group_id,
                    command.task_ref(),
                    command.role_id(),
                ) == Some(attempt.execution_id())
        })
}

/// Admits the same stop authority for operator cancellation of every live current Group attempt.
pub(crate) fn admit_group_stop(
    controller: &mut ControllerState,
    group_id: &domain::ExecutionGroupId,
    now: TimestampMs,
    timeout_ms: u64,
) -> Result<(), String> {
    let command = controller
        .bridge
        .attempt_history()
        .into_iter()
        .find(|attempt| {
            let command = attempt.command();
            command.group_id() == group_id
                && !attempt.status().is_terminal()
                && controller.bridge.current_task_attempt_id(
                    group_id,
                    command.task_ref(),
                    command.role_id(),
                ) == Some(attempt.execution_id())
        })
        .map(|attempt| attempt.command().clone());
    if let Some(command) = command {
        admit(
            controller,
            group_id,
            command.task_ref(),
            command.role_id(),
            now,
            timeout_ms,
        )?;
    }
    Ok(())
}

/// Holds and cancels the complete current Group attempt set when a live relation is fenced.
pub(crate) fn admit(
    controller: &mut ControllerState,
    group_id: &domain::ExecutionGroupId,
    task_ref: &TaskRef,
    role_id: &domain::RoleId,
    now: TimestampMs,
    timeout_ms: u64,
) -> Result<(), String> {
    if timeout_ms == 0
        || timeout_ms > 3_600_000
        || now.as_millis().checked_add(timeout_ms).is_none()
    {
        return Err("invalid application stop policy deadline".into());
    }
    let Some(target) = controller
        .bridge
        .current_task_attempt_id(group_id, task_ref, role_id)
        .map(str::to_owned)
    else {
        // Pre-dispatch relation readiness has no physical attempt to stop.
        return Ok(());
    };
    let mission = controller
        .orchestrator
        .execution(task_ref.mission_id())
        .ok_or("relation references an absent Mission")?;
    if mission.group_id() != group_id
        || matches!(
            mission.lifecycle(),
            orchestration::MissionExecutionLifecycle::Completed
                | orchestration::MissionExecutionLifecycle::Failed
                | orchestration::MissionExecutionLifecycle::Cancelled
        )
    {
        return Ok(());
    }
    let attempts: Vec<_> = controller
        .bridge
        .attempt_history()
        .into_iter()
        .filter(|attempt| {
            let command = attempt.command();
            command.group_id() == group_id
                && controller.bridge.current_task_attempt_id(
                    group_id,
                    command.task_ref(),
                    command.role_id(),
                ) == Some(attempt.execution_id())
                && (!attempt.status().is_terminal() || attempt.execution_id() == target)
        })
        .collect();
    if attempts.is_empty() {
        return Ok(());
    }
    let correlation =
        domain::CorrelationId::new("relation-safety-stop").map_err(|error| error.to_string())?;
    controller
        .orchestrator
        .hold_for_safety(
            task_ref.mission_id(),
            now,
            &correlation,
            &mut controller.bridge.event_sink().clone(),
        )
        .map_err(|error| error.to_string())?;
    for attempt in attempts {
        if controller.safety_stops.contains_key(attempt.execution_id()) {
            continue;
        }
        let command = attempt.command();
        let generation = controller
            .bridge
            .current_attempt_generation(group_id, command.task_ref(), command.role_id())
            .ok_or("current stop attempt has no durable generation")?;
        let declaration = safety_declaration(controller, command);
        let (export, support, policy) = match declaration {
            Some((export, support, policy)) => (Some(export), Some(support), Some(policy)),
            None => (None, None, None),
        };
        let session_id = controller
            .bridge
            .current_node_session(command.node_id())
            .map(str::to_owned);
        let wait = support.as_ref().map_or(timeout_ms, |support| {
            support.stop_deadline_ms.min(timeout_ms)
        });
        let identity = SafetyStopIdentity {
            group_id: group_id.clone(),
            task_ref: command.task_ref().clone(),
            role_id: command.role_id().clone(),
            execution_id: attempt.execution_id().into(),
            execution_generation: generation,
            controller_epoch: controller.bridge.event_sink().issuing_controller_epoch(),
            safety_policy_digest: digest(&(&export, &support, &policy))?,
        };
        let mut record = SafetyStopRecord::new(
            identity,
            now,
            TimestampMs::new(now.as_millis() + wait),
            "controller-relation-policy",
            digest(command)?,
        )
        .map_err(|error| error.to_string())?;
        let reason = if policy.is_none() || session_id.is_none() {
            record.phase = SafetyStopPhase::ManualEscalation;
            "operation safety declaration or current session is unavailable".into()
        } else {
            String::new()
        };
        controller.safety_stops.insert(
            attempt.execution_id().into(),
            SafetyStopPurpose {
                record,
                node_id: command.node_id().clone(),
                session_id,
                export,
                support,
                policy,
                command_id: format!("cancel-{}", attempt.execution_id()),
                accepted_at: None,
                stopped_at: None,
                safe_at: None,
                receipt_digest: None,
                safe_proof: None,
                legacy_evidence: None,
                reason,
            },
        );
    }
    controller
        .bridge
        .request_group_cancellation(group_id)
        .map_err(|error| error.to_string())?;
    Ok(())
}

/// Records an already protocol-admitted receipt without interpreting it as physical stopping.
pub(crate) fn receipt(
    controller: &mut ControllerState,
    node_id: &domain::NodeId,
    session_id: &str,
    receipt: &integration::grpc::v0_4::CommandReceipt,
    received_at: TimestampMs,
    now: TimestampMs,
) -> Result<(), String> {
    use integration::grpc::v0_4::{CommandKind, CommandReceiptStatus};
    if receipt.kind != CommandKind::CommandCancel as i32 {
        return Ok(());
    }
    if !controller
        .bridge
        .control()
        .source_session_is_current(node_id, session_id, now)
    {
        return Ok(());
    }
    let Some(purpose) = controller.safety_stops.get_mut(&receipt.execution_id) else {
        return Ok(());
    };
    if purpose.record.phase.confirms_stop()
        || purpose.record.phase == SafetyStopPhase::ManualEscalation
    {
        return Ok(());
    }
    if &purpose.node_id != node_id
        || purpose.session_id.as_deref() != Some(session_id)
        || receipt.session_id != session_id
        || purpose.command_id != receipt.command_id
        || received_at < purpose.record.requested_at
        || received_at >= purpose.record.deadline
    {
        return Err("stop receipt identity/time differs from the immutable purpose".into());
    }
    if receipt.status == CommandReceiptStatus::CommandPersisted as i32 {
        purpose.accepted_at.get_or_insert(received_at);
        purpose
            .receipt_digest
            .get_or_insert(digest(&serde_json::json!({
                "session_id": receipt.session_id, "command_id": receipt.command_id,
                "execution_id": receipt.execution_id, "kind": receipt.kind,
                "status": receipt.status, "reason": receipt.reason,
            }))?);
    } else {
        purpose.record.phase = SafetyStopPhase::ManualEscalation;
        purpose.reason = "Node rejected the durable stop command".into();
    }
    Ok(())
}

/// Returns only a current-session, operation-owned, fresh and unambiguous safety observation.
fn safe_proof(
    controller: &ControllerState,
    purpose: &SafetyStopPurpose,
    now: TimestampMs,
) -> Option<SafetyObservationProof> {
    let session = purpose.session_id.as_deref()?;
    let policy = purpose.policy.as_ref()?;
    let identity = &purpose.record.identity;
    if !controller
        .bridge
        .control()
        .source_session_is_current(&purpose.node_id, session, now)
        || controller.bridge.current_task_attempt_id(
            &identity.group_id,
            &identity.task_ref,
            &identity.role_id,
        ) != Some(identity.execution_id.as_str())
        || controller.bridge.current_attempt_generation(
            &identity.group_id,
            &identity.task_ref,
            &identity.role_id,
        ) != Some(identity.execution_generation)
    {
        return None;
    }
    let command = controller
        .bridge
        .attempt_history()
        .into_iter()
        .find(|attempt| attempt.execution_id() == identity.execution_id)?
        .command()
        .clone();
    let (export, support, current_policy) = safety_declaration(controller, &command)?;
    if purpose.export.as_ref() != Some(&export)
        || purpose.support.as_ref() != Some(&support)
        || policy != &current_policy
    {
        return None;
    }
    let records: Vec<_> = controller
        .bridge
        .state_records()
        .records()
        .into_iter()
        .filter(|record| {
            record.source_epoch() == Some(session)
                && record.received_at() >= purpose.record.requested_at
                && record.received_at() < purpose.record.deadline
        })
        .collect();
    let evaluation = policy.evaluate(&records, state::DecisionKind::Readiness, now);
    if evaluation.disposition != state::EvidenceDisposition::Accepted {
        return None;
    }
    let record = evaluation.record?;
    let batch: SafeStateBatch = serde_json::from_value(record.value().clone()).ok()?;
    if batch.observations.len() > 256 {
        return None;
    }
    let matching: Vec<_> = batch
        .observations
        .into_iter()
        .filter(|observation| observation.execution_id == identity.execution_id)
        .collect();
    if matching.len() != 1 {
        return None;
    }
    let observation = matching.into_iter().next()?;
    if !observation.safe_state
        || observation.execution_generation != identity.execution_generation
        || observation.command_id != purpose.command_id
        || observation.assurance != support.assurance
    {
        return None;
    }
    Some(SafetyObservationProof {
        record: record.clone(),
        key: record.key().clone(),
        source_epoch: session.into(),
        received_at: record.received_at(),
        valid_for_ms: record.valid_for_ms(),
        confidence_millionths: record.confidence_millionths(),
        sequence: record.sequence(),
        record_digest: digest(&record).ok()?,
        observation,
    })
}

/// Progresses typed phases and deadlines using a fresh decision clock, including ticks with no Node message.
pub(crate) fn tick(controller: &mut ControllerState, now: TimestampMs) -> Result<(), String> {
    let ids: Vec<_> = controller.safety_stops.keys().cloned().collect();
    for id in ids {
        let previous = &controller.safety_stops[&id];
        if previous.record.phase.confirms_stop()
            || previous.record.phase == SafetyStopPhase::ManualEscalation
        {
            continue;
        }
        let proof = safe_proof(controller, previous, now);
        let terminal = (controller.bridge.execution_status(&id)
            == Some(orchestration::RemoteExecutionStatus::Cancelled))
        .then(|| controller.bridge.terminal_received_at(&id))
        .flatten();
        let purpose = controller
            .safety_stops
            .get_mut(&id)
            .expect("selected existing stop");
        if let Some(terminal) = terminal
            .filter(|time| *time >= purpose.record.requested_at && *time < purpose.record.deadline)
        {
            purpose.stopped_at.get_or_insert(terminal);
        }
        let identity = purpose.record.identity.clone();
        if purpose.record.phase == SafetyStopPhase::StopRequested
            && let Some(accepted) = purpose.accepted_at
        {
            purpose
                .record
                .advance(
                    &identity,
                    SafetyStopPhase::StopCommandAccepted,
                    accepted,
                    "node-durable-cancel-receipt",
                    purpose
                        .receipt_digest
                        .as_deref()
                        .ok_or("stop receipt digest is absent")?,
                )
                .map_err(|error| error.to_string())?;
        }
        if let Some(proof) = proof {
            if purpose.record.phase == SafetyStopPhase::StopCommandAccepted {
                purpose
                    .record
                    .advance(
                        &identity,
                        SafetyStopPhase::SafeStateObserved,
                        proof.received_at,
                        "operation-owned-State-export",
                        &proof.record_digest,
                    )
                    .map_err(|error| error.to_string())?;
                purpose.safe_at.get_or_insert(proof.received_at);
            }
            if purpose.record.phase == SafetyStopPhase::SafeStateObserved
                && let Some(stopped) = purpose.stopped_at
            {
                let confirmation_time = stopped
                    .max(proof.received_at)
                    .max(purpose.accepted_at.ok_or("stop acceptance is absent")?);
                let confirmation_digest =
                    digest(&(&identity, &purpose.receipt_digest, stopped, &proof))?;
                purpose
                    .record
                    .advance(
                        &identity,
                        SafetyStopPhase::StopConfirmed,
                        confirmation_time,
                        "terminal-cancel-and-independent-safe-state",
                        confirmation_digest,
                    )
                    .map_err(|error| error.to_string())?;
                purpose.safe_proof = Some(proof);
            }
        }
        // Timely original receive times can confirm fresh evidence already admitted in this
        // transaction, even when queueing delayed this reduction. A prior durable escalation
        // remains fenced above; expired or late evidence cannot renew the deadline.
        if purpose.record.expire(now) {
            purpose.reason =
                "stop deadline requires explicit reconciliation; ownership retained".into();
        }
    }
    Ok(())
}

/// Prevents every Mission release/continuation until all retained stop purposes are confirmed.
pub(crate) fn blocks_release(controller: &ControllerState, mission_id: &domain::MissionId) -> bool {
    controller.safety_stops.values().any(|purpose| {
        purpose.record.identity.task_ref.mission_id() == mission_id
            && !purpose.record.may_release_ownership()
    }) || super::memory_use::blocks_release(controller, mission_id)
}

/// Revalidates complete retained confirmation evidence without relying on a new live session.
fn validate_confirmation(purpose: &SafetyStopPurpose) -> Result<(), String> {
    let identity = &purpose.record.identity;
    let proof = purpose
        .safe_proof
        .as_ref()
        .ok_or("confirmed stop checkpoint lacks its observation proof")?;
    let accepted = purpose
        .accepted_at
        .ok_or("confirmed stop lacks original acceptance time")?;
    let stopped = purpose
        .stopped_at
        .ok_or("confirmed stop lacks original terminal time")?;
    let confirmed = purpose
        .record
        .confirmed_at
        .ok_or("confirmed stop lacks confirmation time")?;
    let policy = purpose
        .policy
        .as_ref()
        .ok_or("confirmed stop lacks evidence policy")?;
    let support = purpose
        .support
        .as_ref()
        .ok_or("confirmed stop lacks operation support")?;
    let batch: SafeStateBatch =
        serde_json::from_value(proof.record.value().clone()).map_err(|error| error.to_string())?;
    let matches: Vec<_> = batch
        .observations
        .iter()
        .filter(|observation| observation.execution_id == identity.execution_id)
        .collect();
    if batch.observations.len() > 256
        || matches.len() != 1
        || matches[0] != &proof.observation
        || purpose
            .receipt_digest
            .as_ref()
            .is_none_or(|value| !is_digest(value))
        || purpose.session_id.as_deref() != Some(proof.source_epoch.as_str())
        || proof.record.source_epoch() != Some(proof.source_epoch.as_str())
        || proof.key != *proof.record.key()
        || proof.received_at != proof.record.received_at()
        || proof.valid_for_ms != proof.record.valid_for_ms()
        || proof.sequence != proof.record.sequence()
        || proof.confidence_millionths != proof.record.confidence_millionths()
        || proof.record_digest != digest(&proof.record)?
        || proof.observation.assurance != support.assurance
        || !proof.observation.safe_state
        || proof.observation.execution_id != identity.execution_id
        || proof.observation.execution_generation != identity.execution_generation
        || proof.observation.command_id != purpose.command_id
        || [accepted, stopped, proof.received_at]
            .iter()
            .any(|time| *time < purpose.record.requested_at || *time >= purpose.record.deadline)
        || confirmed != accepted.max(stopped).max(proof.received_at)
        || policy
            .evaluate(
                std::slice::from_ref(&proof.record),
                state::DecisionKind::Readiness,
                confirmed,
            )
            .disposition
            != state::EvidenceDisposition::Accepted
        || purpose.record.last_evidence_digest
            != digest(&(identity, &purpose.receipt_digest, stopped, proof))?
    {
        return Err("confirmed stop checkpoint evidence is inconsistent".into());
    }
    Ok(())
}

/// Checks a canonical SHA-256 label before using it as retained evidence identity.
fn is_digest(value: &str) -> bool {
    value.strip_prefix("sha256:").is_some_and(|hex| {
        hex.len() == 64
            && hex
                .bytes()
                .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
    })
}

/// Validates retained stop authority and reports whether a transactional legacy hold is needed.
pub(crate) fn validate_restored(controller: &ControllerState) -> Result<bool, String> {
    for (execution_id, purpose) in &controller.safety_stops {
        purpose
            .record
            .validate()
            .map_err(|error| error.to_string())?;
        let identity = &purpose.record.identity;
        if execution_id != &identity.execution_id
            || purpose.command_id != format!("cancel-{execution_id}")
        {
            return Err("stop checkpoint command identity is invalid".into());
        }
        let attempt = controller
            .bridge
            .attempt_history()
            .into_iter()
            .find(|attempt| attempt.execution_id() == execution_id)
            .ok_or("stop checkpoint attempt is absent")?;
        let command = attempt.command();
        if command.group_id() != &identity.group_id
            || command.task_ref() != &identity.task_ref
            || command.role_id() != &identity.role_id
            || command.node_id() != &purpose.node_id
        {
            return Err("stop checkpoint ownership is inconsistent".into());
        }
        if let Some(policy) = &purpose.policy {
            policy.validate().map_err(|error| error.to_string())?;
            let export = purpose
                .export
                .as_ref()
                .ok_or("stop policy export is absent")?;
            let support = purpose
                .support
                .as_ref()
                .ok_or("stop policy operation support is absent")?;
            if digest(&(&purpose.export, &purpose.support, &purpose.policy))?
                != identity.safety_policy_digest
            {
                return Err("stop checkpoint policy digest is inconsistent".into());
            }
            if &support.operation != command.intent().operation()
                || support.safe_state_export_id != export.export_id()
                || export.semantic() != domain::StateSemantic::Observed
                || export.payload_schema() != domain::SAFE_STATE_OBSERVATION_SCHEMA
                || policy.object != *export.object()
                || policy.channel_id != export.export_id()
                || policy.allowed_sources
                    != BTreeSet::from([domain::StateSource::Node {
                        node_id: purpose.node_id.clone(),
                        local_system_id: export.local_system_id().clone(),
                    }])
            {
                return Err("stop checkpoint export authority is inconsistent".into());
            }
        } else if purpose.record.phase != SafetyStopPhase::ManualEscalation {
            return Err("unverified stop checkpoint lacks a manual fence".into());
        }
        if purpose.record.phase.confirms_stop() {
            if purpose.legacy_evidence.is_some() {
                return Err("legacy evidence cannot grant stop confirmation".into());
            }
            validate_confirmation(purpose)?;
        }
    }
    let mut needs_hold = false;
    let held: Vec<_> = controller
        .safety_stops
        .values()
        .filter(|purpose| !purpose.record.may_release_ownership())
        .map(|purpose| {
            (
                purpose.record.identity.task_ref.mission_id().clone(),
                purpose.record.requested_at,
            )
        })
        .collect();
    for (mission_id, _) in held {
        let mission = controller
            .orchestrator
            .execution(&mission_id)
            .ok_or("retained stop Mission is absent")?;
        if !matches!(
            mission.lifecycle(),
            orchestration::MissionExecutionLifecycle::Accepted
                | orchestration::MissionExecutionLifecycle::Running
                | orchestration::MissionExecutionLifecycle::Cancelling
        ) {
            return Err("legacy terminal Mission lacks verified stop authority; trusted reconciliation required".into());
        }
        if mission.lifecycle() != orchestration::MissionExecutionLifecycle::Cancelling
            && mission.isolation().is_none()
        {
            needs_hold = true;
        }
    }
    Ok(needs_hold)
}

/// Establishes missing legacy holds inside the startup application transaction before delivery.
pub(crate) fn restore_holds(controller: &mut ControllerState) -> Result<(), String> {
    validate_restored(controller)?;
    let missions: BTreeMap<_, _> = controller
        .safety_stops
        .values()
        .filter(|purpose| !purpose.record.may_release_ownership())
        .map(|purpose| {
            (
                purpose.record.identity.task_ref.mission_id().clone(),
                purpose.record.requested_at,
            )
        })
        .collect();
    let correlation =
        domain::CorrelationId::new("safety-stop-migration").map_err(|error| error.to_string())?;
    for (mission, requested_at) in missions {
        controller
            .orchestrator
            .hold_for_safety(
                &mission,
                requested_at,
                &correlation,
                &mut controller.bridge.event_sink().clone(),
            )
            .map_err(|error| error.to_string())?;
    }
    Ok(())
}
