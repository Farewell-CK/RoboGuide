//! Participant-local Federation authority with original receipts and immutable receive budgets.

use crate::*;
use control::{FederationSession, FederationSessionState};
use ports::Clock;
use sha2::{Digest as _, Sha256};

mod policy;
#[cfg(test)]
mod tests;
use super::plan_revision::time::{KernelTime, OriginalBudget};
pub(crate) use policy::{Policy, configured_policy};

/// Closed service contract; v0.1 has no durable participant permission receipts.
const SCHEMA_VERSION: &str = "roboguide.federation-service/v0.2";
/// Maximum retained participant operations; expiry never frees uncertain history.
const MAX_RECORDS: usize = 4096;

/// Exact current transport witness checked again under the same application writer.
#[derive(Debug, Clone)]
pub(crate) struct Caller {
    /// Actual peer leaf digest from the completed mTLS handshake.
    pub(crate) leaf: String,
    /// Independently enrolled stable operator principal.
    pub(crate) principal: String,
    /// Exact tenant selected within the current certificate grant.
    pub(crate) tenant: String,
}

/// Versioned command used for the first bounded participant admission.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct PrepareRequest {
    /// Exact participant contract codec.
    schema_version: String,
    /// Complete immutable input, with a deadline in this participant's local clock profile.
    session: FederationSession,
}

/// A commit must repeat every original semantic identity; the route supplies the transaction id.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct CommitRequest {
    /// Exact participant contract codec.
    schema_version: String,
    /// Original source domain epoch, independently matched to deployment expectations.
    source_epoch: u64,
    /// Exact originally prepared plan identity.
    plan_digest: String,
    /// Exact originally prepared ownership identity.
    ownership_digest: String,
}

/// Participant stages; none alone is a distributed commit decision.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum Operation {
    /// Retain the original complete prepared input.
    Prepare,
    /// Record a participant-local commitment.
    Commit,
    /// Retire a still-prepared participant without inferring any remote outcome.
    Abort,
}

/// Actual original receipt retained across phase changes and unrelated Controller mutations.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Record {
    /// Original immutable Prepared input, never overwritten with a terminal phase.
    request: PrepareRequest,
    /// Original stable principal; a second administrator cannot inherit this command identity.
    principal: String,
    /// Actual first leaf, retained as evidence beside later stable-principal rotations.
    leaf: String,
    /// Complete original authorization policy content identity.
    policy_digest: String,
    /// Original Controller-local receive time.
    accepted_at_ms: u64,
    /// Same-kernel original deadline, not reconstructed from wall time after restart.
    budget: OriginalBudget,
    /// First participant-local Commit receive time; an original retry never replaces it.
    committed_at_ms: Option<u64>,
    /// First participant-local Abort receive time, distinct from Commit.
    aborted_at_ms: Option<u64>,
}

/// Original participant policy history and complete software authority receipts.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct FederationState {
    /// Consecutive accepted policy revisions, including retired actual leaves.
    policies: BTreeMap<u64, Policy>,
    /// Bounded original transaction records; timeouts never erase their ownership evidence.
    records: BTreeMap<String, Record>,
}

impl FederationState {
    /// Returns the latest durable policy while retaining every original interpretation.
    fn policy(&self) -> Option<&Policy> {
        self.policies.last_key_value().map(|(_, policy)| policy)
    }

    /// Installs only an exact unchanged policy or a consecutive monotonic successor.
    fn accept_policy(&mut self, next: Option<Policy>) -> Result<bool, String> {
        match (self.policy(), next) {
            (None, None) => Ok(false),
            (Some(_), None) => Err("enrolled Federation policy cannot disappear".into()),
            (Some(previous), Some(next)) if previous == &next => Ok(false),
            (previous, Some(next)) => {
                next.validate()?;
                if self.policies.len() >= 1024 {
                    return Err("Federation policy history is full".into());
                }
                if let Some(previous) = previous {
                    previous.successor(&next)?;
                } else if next.revision != 1 {
                    return Err("first Federation policy must start at revision one".into());
                }
                self.policies.insert(next.revision, next);
                Ok(true)
            }
        }
    }
}

/// Validates exact participant routing without authorizing any resource or remote effect.
pub(crate) fn validate_session_route(
    session: &FederationSession,
    local_domain: &str,
    tenant: Option<&str>,
) -> Result<(), String> {
    if session.source_domain == session.target_domain
        || (session.source_domain != local_domain && session.target_domain != local_domain)
        || tenant != Some(session.tenant_id.as_str())
    {
        return Err("Federation session crosses the local domain or exact tenant".into());
    }
    Ok(())
}

/// Hashes complete typed JSON so whitespace or key order cannot create another command identity.
fn digest(value: &impl Serialize) -> Result<String, String> {
    let value = serde_json::to_value(value).map_err(|error| error.to_string())?;
    Ok(format!(
        "sha256:{:x}",
        Sha256::digest(serde_json::to_vec(&value).map_err(|error| error.to_string())?)
    ))
}

/// Saves a private candidate and its actual caller journal before publishing any authority.
#[allow(clippy::too_many_arguments)]
fn persist(
    controller: &Arc<Mutex<ControllerState>>,
    events: &state::SqliteEventLog,
    candidate: ControllerState,
    command_id: String,
    principal: &str,
    input: &[u8],
    gate: &dyn ApplicationWriteGate,
    current_record: Option<&str>,
) -> Result<(), String> {
    events.begin_batch().map_err(|error| error.to_string())?;
    let result = (|| {
        let checkpoint = server_checkpoint_json(&candidate).map_err(|error| error.to_string())?;
        let sequence = events
            .current_batch_event_sequence()
            .map_err(|error| error.to_string())?;
        let command = control_command_journal_entry(
            command_id,
            Some(principal.to_string()),
            input,
            &checkpoint,
            sequence,
        );
        events
            .save_checkpoint_with_command(SERVER_CHECKPOINT_SCHEMA, &checkpoint, command)
            .map_err(|error| error.to_string())?;
        gate.validate_locked()?;
        if configured_policy()?.as_ref() != candidate.federation.policy() {
            return Err("Federation policy changed before the actual SQL Commit".into());
        }
        if let Some(id) = current_record
            && candidate.federation.records[id]
                .budget
                .remaining(&KernelTime::observe()?)
                == 0
        {
            return Err("original Federation budget expired before the actual SQL Commit".into());
        }
        events.commit_batch().map_err(|error| error.to_string())?;
        *controller
            .lock()
            .map_err(|_| "Federation Controller authority is unavailable")? = candidate;
        Ok(())
    })();
    if result.is_err() {
        let _ = events.rollback_batch();
    }
    result
}

/// Durably accepts current deployment policy before grants become visible, including revocation.
pub(crate) fn synchronize_policy(
    controller: &Arc<Mutex<ControllerState>>,
    events: &state::SqliteEventLog,
    gate: &dyn ApplicationWriteGate,
) -> Result<(), String> {
    let _guard = gate.lock()?;
    let mut candidate = controller
        .lock()
        .map_err(|_| "Federation authority unavailable")?
        .clone();
    let configured = configured_policy()?;
    if !candidate.federation.accept_policy(configured)? {
        return Ok(());
    }
    let policy = candidate.federation.policy().expect("accepted policy");
    let input = serde_json::to_vec(policy).map_err(|error| error.to_string())?;
    let command = format!("federation-policy:{}", digest(policy)?);
    persist(
        controller,
        events,
        candidate,
        command,
        "trusted-deployment:federation-policy",
        &input,
        gate,
        None,
    )
}

/// Refuses startup with missing, regressed or unrelated domain policy without changing the database.
pub(crate) fn validate_startup_policy(controller: &ControllerState) -> Result<(), String> {
    let mut state = controller.federation.clone();
    state.accept_policy(configured_policy()?)?;
    Ok(())
}

/// Rechecks exact actual peer, current persisted/configured policy and expected epoch under writer ordering.
fn authorize_current(
    controller: &ControllerState,
    caller: &Caller,
    session: &FederationSession,
) -> Result<Policy, String> {
    let configured = configured_policy()?.ok_or("Federation service is not configured")?;
    if controller.federation.policy() != Some(&configured) {
        return Err("Federation policy changed while awaiting the application writer".into());
    }
    configured.authorize(caller, session)?;
    Ok(configured)
}

/// Returns an immutable original stage receipt, separately from current and remote readiness.
fn receipt(record: &Record, operation: Operation) -> serde_json::Value {
    let (phase, accepted_at) = match operation {
        Operation::Prepare => ("prepared", record.accepted_at_ms),
        Operation::Commit => (
            "committed",
            record.committed_at_ms.expect("committed receipt"),
        ),
        Operation::Abort => ("aborted", record.aborted_at_ms.expect("aborted receipt")),
    };
    serde_json::json!({"schema_version": SCHEMA_VERSION, "transaction_id": record.request.session.transaction_id,
        "phase": phase, "accepted_at_ms": accepted_at, "original_session": record.request.session,
        "principal": record.principal, "original_leaf": record.leaf, "policy_digest": record.policy_digest,
        "global_dispatch_ready": false})
}

/// Commits a participant transition once; exact retries return original receipts without another SQL mutation.
#[allow(clippy::too_many_arguments)]
pub(crate) fn submit(
    controller: &Arc<Mutex<ControllerState>>,
    events: &state::SqliteEventLog,
    gate: &dyn ApplicationWriteGate,
    clock: &impl Clock,
    body: &[u8],
    operation: Operation,
    transaction_id: Option<&str>,
    caller: &Caller,
) -> Result<serde_json::Value, String> {
    let ingress = KernelTime::observe()?;
    let received_at = clock.now();
    let _guard = gate.lock()?;
    let mut candidate = controller
        .lock()
        .map_err(|_| "Federation Controller authority unavailable")?
        .clone();
    let id = if operation == Operation::Prepare {
        let input: PrepareRequest =
            serde_json::from_slice(body).map_err(|error| error.to_string())?;
        if input.schema_version != SCHEMA_VERSION
            || input.session.state != FederationSessionState::Prepared
        {
            return Err("Federation prepare requires v0.2 and original Prepared phase".into());
        }
        validate_transaction_id(&input.session.transaction_id)?;
        let policy = authorize_current(&candidate, caller, &input.session)?;
        let id = input.session.transaction_id.clone();
        if let Some(record) = candidate.federation.records.get(&id) {
            if record.request == input && record.principal == caller.principal {
                return Ok(receipt(record, operation));
            }
            return Err("Federation prepare identity conflicts with its original receipt".into());
        }
        let duration = input
            .session
            .deadline
            .as_millis()
            .checked_sub(received_at.as_millis())
            .ok_or("Federation prepare deadline expired")?;
        if duration == 0 || duration > 300_000 || candidate.federation.records.len() >= MAX_RECORDS
        {
            return Err("Federation original deadline or retained-operation bound exceeded".into());
        }
        let budget = OriginalBudget::new(&ingress, duration)?;
        if budget.remaining(&KernelTime::observe()?) == 0 {
            return Err("Federation prepare budget expired while waiting for writer".into());
        }
        candidate
            .bridge
            .control_mut()
            .prepare_federation_session(input.session.clone(), clock.now())
            .map_err(|error| error.to_string())?;
        candidate.federation.records.insert(
            id.clone(),
            Record {
                request: input,
                principal: caller.principal.clone(),
                leaf: caller.leaf.clone(),
                policy_digest: digest(&policy)?,
                accepted_at_ms: received_at.as_millis(),
                budget,
                committed_at_ms: None,
                aborted_at_ms: None,
            },
        );
        id
    } else {
        let id =
            validate_transaction_id(transaction_id.ok_or("Federation transaction id absent")?)?;
        let record = candidate
            .federation
            .records
            .get(&id)
            .ok_or("original Federation service authority unavailable")?
            .clone();
        let policy = authorize_current(&candidate, caller, &record.request.session)?;
        if caller.principal != record.principal {
            return Err("Federation transaction belongs to a different original principal".into());
        }
        if operation == Operation::Commit {
            let input: CommitRequest =
                serde_json::from_slice(body).map_err(|error| error.to_string())?;
            let session = &record.request.session;
            if input.schema_version != SCHEMA_VERSION
                || input.source_epoch != session.source_epoch
                || input.plan_digest != session.plan_digest
                || input.ownership_digest != session.ownership_digest
            {
                return Err("Federation Commit differs from the original complete input".into());
            }
            if record.committed_at_ms.is_some() {
                return Ok(receipt(&record, operation));
            }
        } else {
            if serde_json::from_slice::<serde_json::Value>(body)
                .map_err(|error| error.to_string())?
                != serde_json::json!({"schema_version": SCHEMA_VERSION})
            {
                return Err("Federation Abort requires only the exact v0.2 schema".into());
            }
            if record.aborted_at_ms.is_some() {
                return Ok(receipt(&record, operation));
            }
        }
        let now = clock.now();
        if digest(&policy)? != record.policy_digest
            || record.budget.remaining(&KernelTime::observe()?) == 0
            || now.as_millis() < record.accepted_at_ms
            || now >= record.request.session.deadline
        {
            return Err("original Federation policy or receive budget is no longer current".into());
        }
        match operation {
            Operation::Commit => {
                let session = &record.request.session;
                candidate
                    .bridge
                    .control_mut()
                    .commit_federation_session(
                        &id,
                        session.source_epoch,
                        &session.plan_digest,
                        &session.ownership_digest,
                        now,
                    )
                    .map_err(|error| error.to_string())?;
                candidate
                    .federation
                    .records
                    .get_mut(&id)
                    .expect("original record")
                    .committed_at_ms = Some(now.as_millis());
            }
            Operation::Abort => {
                candidate
                    .bridge
                    .control_mut()
                    .abort_federation_session(&id, now)
                    .map_err(|error| error.to_string())?;
                candidate
                    .federation
                    .records
                    .get_mut(&id)
                    .expect("original record")
                    .aborted_at_ms = Some(now.as_millis());
            }
            Operation::Prepare => unreachable!("separate prepare branch"),
        }
        id
    };
    let record = candidate
        .federation
        .records
        .get(&id)
        .expect("candidate record");
    let body = receipt(record, operation);
    let command_id = format!("federation:{}", digest(&body)?);
    let input = serde_json::to_vec(&body).map_err(|error| error.to_string())?;
    persist(
        controller,
        events,
        candidate,
        command_id,
        &caller.principal,
        &input,
        gate,
        Some(&id),
    )?;
    Ok(body)
}

/// Reports participant-local evidence and explicitly unavailable global dispatch authority.
pub(crate) fn view(
    controller: &Arc<Mutex<ControllerState>>,
    id: &str,
    caller: &Caller,
    now: domain::TimestampMs,
) -> Result<serde_json::Value, String> {
    let id = validate_transaction_id(id)?;
    let live = controller
        .lock()
        .map_err(|_| "Federation Controller authority unavailable")?;
    let session = live
        .bridge
        .control()
        .federation_session(&id)
        .ok_or("unknown Federation transaction")?;
    let policy = authorize_current(&live, caller, session)?;
    let record = live.federation.records.get(&id);
    let local_commit_current = record.is_some_and(|record| {
        record.committed_at_ms.is_some()
            && digest(&policy).ok().as_deref() == Some(&record.policy_digest)
            && KernelTime::observe().is_ok_and(|time| record.budget.remaining(&time) > 0)
            && now.as_millis() >= record.accepted_at_ms
            && live.bridge.control().federation_dispatch_ready(&id, now)
    });
    Ok(
        serde_json::json!({"schema_version": SCHEMA_VERSION, "transaction_id": id, "session": session,
        "original_receipt_available": record.is_some(), "local_commit_current": local_commit_current,
        "global_dispatch_ready": false, "remote_decision": "unavailable"}),
    )
}

/// Checks restored receipts against the original policy interpretation and exact Control session.
pub(crate) fn validate_restored(controller: &ControllerState) -> Result<(), String> {
    let state = &controller.federation;
    if state.records.len() > MAX_RECORDS || state.policies.len() > 1024 {
        return Err("Federation restored bounds exceeded".into());
    }
    let mut previous: Option<&Policy> = None;
    for (revision, policy) in &state.policies {
        policy.validate()?;
        if revision != &policy.revision {
            return Err("Federation policy history key differs".into());
        }
        if let Some(previous) = previous {
            previous.successor(policy)?;
        } else if *revision != 1 {
            return Err("Federation original policy history absent".into());
        }
        previous = Some(policy);
    }
    for (id, record) in &state.records {
        validate_transaction_id(id)?;
        let original = &record.request.session;
        let session = controller
            .bridge
            .control()
            .federation_session(id)
            .ok_or("Federation receipt has no Control session")?;
        let mut expected = original.clone();
        expected.state = if record.committed_at_ms.is_some() {
            FederationSessionState::Committed
        } else if record.aborted_at_ms.is_some() {
            FederationSessionState::Aborted
        } else {
            FederationSessionState::Prepared
        };
        record.budget.validate()?;
        if session != &expected
            || &original.transaction_id != id
            || original.state != FederationSessionState::Prepared
            || record.request.schema_version != SCHEMA_VERSION
            || record.committed_at_ms.is_some() && record.aborted_at_ms.is_some()
            || original
                .deadline
                .as_millis()
                .checked_sub(record.accepted_at_ms)
                != Some(record.budget.duration_ms())
            || record.budget.duration_ms() > 300_000
            || !state.policies.values().any(|policy| {
                digest(policy).ok().as_deref() == Some(&record.policy_digest)
                    && policy
                        .authorize(
                            &Caller {
                                leaf: record.leaf.clone(),
                                principal: record.principal.clone(),
                                tenant: original.tenant_id.clone(),
                            },
                            original,
                        )
                        .is_ok()
            })
            || [record.committed_at_ms, record.aborted_at_ms]
                .into_iter()
                .flatten()
                .any(|time| time < record.accepted_at_ms || time >= original.deadline.as_millis())
        {
            return Err("Federation original receipt, policy, phase or deadline mismatch".into());
        }
    }
    Ok(())
}

/// Refuses a legacy wrapper that hides the newly versioned participant service authority.
pub(crate) fn validate_wrapper(saved: &ServerCheckpoint) -> Result<(), String> {
    if !matches!(
        saved.schema.as_str(),
        SERVER_CHECKPOINT_SCHEMA
            | PRE_SCOPED_MAP_SERVER_CHECKPOINT_SCHEMA
            | PRE_CAPACITY_SERVER_CHECKPOINT_SCHEMA
            | PRE_TYPED_REVISION_SERVER_CHECKPOINT_SCHEMA
    ) && (!saved.federation.records.is_empty() || !saved.federation.policies.is_empty())
    {
        return Err("legacy wrapper cannot carry Federation service authority".into());
    }
    Ok(())
}

/// Rejects unbounded and ambiguous transaction route identities.
fn validate_transaction_id(value: &str) -> Result<String, String> {
    if value.is_empty()
        || value.len() > 128
        || value.trim() != value
        || !value
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, b'-' | b'_' | b'.'))
    {
        return Err("Federation transaction id is invalid".into());
    }
    Ok(value.to_string())
}
