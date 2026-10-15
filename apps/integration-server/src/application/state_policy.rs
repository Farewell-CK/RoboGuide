//! Privileged durable source registry administration with original authenticated command receipts.

use crate::*;
use sha2::{Digest as _, Sha256};

/// Expected-revision replacement shared by closed, explicitly selected source registry routes.
#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct RegistryUpdate<R> {
    /// Immutable caller-assigned idempotent administration identity.
    command_id: String,
    /// Exact previous registry revision, or zero for first installation.
    expected_revision: u64,
    /// Complete typed source, interpretation and scope contracts.
    registry: R,
}

/// Parses and commits State policy authority without treating payloads as policy declarations.
pub(crate) fn submit(
    controller: &Arc<Mutex<ControllerState>>,
    log: &state::SqliteEventLog,
    gate: &Arc<Mutex<()>>,
    body: &[u8],
    subject: &str,
    now: domain::TimestampMs,
) -> Result<serde_json::Value, String> {
    let input: RegistryUpdate<domain::DecisionPolicyRegistry> =
        serde_json::from_slice(body).map_err(|error| error.to_string())?;
    commit_registry(
        controller,
        log,
        gate,
        &input,
        subject,
        now,
        domain::RegistryCommandKind::StatePolicy,
        |control| {
            control.accept_decision_policy_registry(input.registry.clone(), input.expected_revision)
        },
    )
}

/// Parses and commits independent verifier producers; no verdict or Task lifecycle is synthesized.
pub(crate) fn submit_verifiers(
    controller: &Arc<Mutex<ControllerState>>,
    log: &state::SqliteEventLog,
    gate: &Arc<Mutex<()>>,
    body: &[u8],
    subject: &str,
    now: domain::TimestampMs,
) -> Result<serde_json::Value, String> {
    let input: RegistryUpdate<domain::SemanticVerifierRegistry> =
        serde_json::from_slice(body).map_err(|error| error.to_string())?;
    commit_registry(
        controller,
        log,
        gate,
        &input,
        subject,
        now,
        domain::RegistryCommandKind::SemanticVerifier,
        |control| {
            control
                .accept_semantic_verifier_registry(input.registry.clone(), input.expected_revision)
        },
    )
}

/// Commits explicit raw-source scalar and covariance derivation policies through the same authority journal.
pub(crate) fn submit_beliefs(
    controller: &Arc<Mutex<ControllerState>>,
    log: &state::SqliteEventLog,
    gate: &Arc<Mutex<()>>,
    body: &[u8],
    subject: &str,
    now: domain::TimestampMs,
) -> Result<serde_json::Value, String> {
    let input: RegistryUpdate<domain::BeliefPolicyRegistry> =
        serde_json::from_slice(body).map_err(|error| error.to_string())?;
    commit_registry(
        controller,
        log,
        gate,
        &input,
        subject,
        now,
        domain::RegistryCommandKind::BeliefPolicy,
        |control| {
            control.accept_belief_policy_registry(input.registry.clone(), input.expected_revision)
        },
    )
}

/// Hashes canonical parsed JSON so insignificant HTTP whitespace cannot change command meaning.
fn digest(value: &impl Serialize) -> Result<String, String> {
    let value = serde_json::to_value(value).map_err(|error| error.to_string())?;
    Ok(format!(
        "sha256:{:x}",
        Sha256::digest(serde_json::to_vec(&value).map_err(|error| error.to_string())?)
    ))
}

/// Commits the original administration attribution and candidate registry before publishing authority.
#[allow(clippy::too_many_arguments)]
fn commit_registry<R: Serialize>(
    controller: &Arc<Mutex<ControllerState>>,
    log: &state::SqliteEventLog,
    gate: &Arc<Mutex<()>>,
    input: &RegistryUpdate<R>,
    subject: &str,
    now: domain::TimestampMs,
    kind: domain::RegistryCommandKind,
    apply: impl FnOnce(&mut control::ControlPlane) -> Result<(), control::ControlError>,
) -> Result<serde_json::Value, String> {
    let input_digest = digest(input)?;
    let _gate = gate
        .lock()
        .map_err(|_| "application write gate is poisoned")?;
    let mut live = controller
        .lock()
        .map_err(|_| "controller lock is poisoned")?;
    if let Some(receipt) = live
        .bridge
        .control()
        .registry_command_receipt(&input.command_id, kind, &input_digest, subject)
        .map_err(|error| error.to_string())?
    {
        return Ok(
            serde_json::json!({"schema_version":"roboguide.registry-command-receipt/v0.1","receipt":receipt}),
        );
    }
    let mut candidate = live.clone();
    apply(candidate.bridge.control_mut()).map_err(|error| error.to_string())?;
    let revision = serde_json::to_value(&input.registry)
        .map_err(|error| error.to_string())?
        .get("revision")
        .and_then(serde_json::Value::as_u64)
        .ok_or("registry lacks revision")?;
    let receipt = domain::RegistryCommandReceipt {
        command_id: input.command_id.clone(),
        kind,
        input_digest,
        registry_digest: digest(&input.registry)?,
        revision,
        authority_subject: subject.into(),
        accepted_at_ms: now.as_millis(),
    };
    candidate
        .bridge
        .control_mut()
        .retain_registry_command_receipt(receipt.clone())
        .map_err(|error| error.to_string())?;
    let checkpoint = server_checkpoint_json(&candidate).map_err(|error| error.to_string())?;
    log.begin_batch().map_err(|error| error.to_string())?;
    let transaction = (|| {
        let command = control_command_journal_entry_with_digest(
            format!("registry:{}", input.command_id),
            Some(subject.into()),
            receipt.input_digest.clone(),
            &checkpoint,
            log.current_batch_event_sequence()?,
        );
        log.save_checkpoint_with_command(SERVER_CHECKPOINT_SCHEMA, &checkpoint, command)?;
        log.commit_batch()
    })();
    if let Err(error) = transaction {
        let rollback = log.rollback_batch();
        return Err(format!(
            "source registry commit failed: {error}; rollback={rollback:?}"
        ));
    }
    *live = candidate;
    Ok(
        serde_json::json!({"schema_version":"roboguide.registry-command-receipt/v0.1","receipt":receipt}),
    )
}
