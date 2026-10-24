//! Authenticated cross-domain Federation session service over the existing Controller authority.

use crate::*;
use control::FederationSession;
use ports::Clock;
use sha2::{Digest as _, Sha256};

/// Versioned wire envelope for a durable cross-domain prepare.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct PrepareRequest {
    /// Closed service contract marker.
    schema_version: String,
    /// Complete immutable session identity shared by both Control domains.
    session: FederationSession,
}

/// Versioned wire envelope for a durable cross-domain commit.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct CommitRequest {
    /// Closed service contract marker.
    schema_version: String,
    /// Original source epoch, never refreshed by a retry.
    source_epoch: u64,
    /// Original accepted plan digest.
    plan_digest: String,
    /// Original Control ownership digest.
    ownership_digest: String,
}

/// Closed service contract used by the two independent Control domains.
const SCHEMA_VERSION: &str = "roboguide.federation-service/v0.1";

/// Mutation selected by the route after transport, tenant and domain authorization.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum Operation {
    /// Store one complete prepared session.
    Prepare,
    /// Commit one exact prepared session.
    Commit,
    /// Abort one exact still-prepared session.
    Abort,
}

/// Validates the deployment domain and immutable tenant boundary before Control mutation.
pub(crate) fn validate_session_route(
    session: &FederationSession,
    local_domain: &str,
    tenant: Option<&str>,
) -> Result<(), String> {
    if local_domain.trim().is_empty() || local_domain.len() > 128 {
        return Err("configured Control domain is invalid".into());
    }
    if session.source_domain != local_domain && session.target_domain != local_domain {
        return Err("Federation session does not address this Control domain".into());
    }
    if tenant != Some(session.tenant_id.as_str()) {
        return Err("Federation session tenant differs from the authenticated delegation".into());
    }
    Ok(())
}

/// Applies one authenticated Federation mutation to a private Controller candidate.
#[allow(clippy::too_many_arguments)]
pub(crate) fn submit(
    controller: &std::sync::Arc<std::sync::Mutex<ControllerState>>,
    events: &state::SqliteEventLog,
    gate: &dyn ApplicationWriteGate,
    clock: &impl Clock,
    body: &[u8],
    operation: Operation,
    transaction_id: Option<&str>,
    local_domain: &str,
    tenant: Option<&str>,
    principal: &str,
) -> Result<serde_json::Value, String> {
    if principal.trim().is_empty() || principal.len() > 256 {
        return Err("Federation principal identity is invalid".into());
    }
    let transaction_id = transaction_id.map(validate_transaction_id).transpose()?;
    let _guard = gate.lock()?;
    events.begin_batch().map_err(|error| error.to_string())?;
    let result = (|| {
        let live = controller
            .lock()
            .map_err(|_| "Federation Controller authority is unavailable")?;
        let mut candidate = live.clone();
        drop(live);
        let now = clock.now();
        let id = match operation {
            Operation::Prepare => {
                let request = serde_json::from_slice::<PrepareRequest>(body)
                    .map_err(|error| format!("invalid Federation prepare: {error}"))?;
                if request.schema_version != SCHEMA_VERSION {
                    return Err("unsupported Federation service schema".into());
                }
                validate_session_route(&request.session, local_domain, tenant)?;
                let id = request.session.transaction_id.clone();
                candidate
                    .bridge
                    .control_mut()
                    .prepare_federation_session(request.session, now)
                    .map_err(|error| error.to_string())?;
                id
            }
            Operation::Commit => {
                let id = transaction_id
                    .as_deref()
                    .ok_or("Federation commit requires a transaction id")?;
                let request = serde_json::from_slice::<CommitRequest>(body)
                    .map_err(|error| format!("invalid Federation commit: {error}"))?;
                if request.schema_version != SCHEMA_VERSION {
                    return Err("unsupported Federation service schema".into());
                }
                let session = candidate
                    .bridge
                    .control()
                    .federation_session(id)
                    .ok_or("unknown Federation transaction")?;
                validate_session_route(session, local_domain, tenant)?;
                candidate
                    .bridge
                    .control_mut()
                    .commit_federation_session(
                        id,
                        request.source_epoch,
                        &request.plan_digest,
                        &request.ownership_digest,
                        now,
                    )
                    .map_err(|error| error.to_string())?;
                id.to_string()
            }
            Operation::Abort => {
                let id = transaction_id
                    .as_deref()
                    .ok_or("Federation abort requires a transaction id")?;
                if serde_json::from_slice::<serde_json::Value>(body)
                    .map_err(|error| error.to_string())?
                    != serde_json::json!({"schema_version": SCHEMA_VERSION})
                {
                    return Err("Federation abort body must contain only schema_version".into());
                }
                let session = candidate
                    .bridge
                    .control()
                    .federation_session(id)
                    .ok_or("unknown Federation transaction")?;
                validate_session_route(session, local_domain, tenant)?;
                candidate
                    .bridge
                    .control_mut()
                    .abort_federation_session(id, now)
                    .map_err(|error| error.to_string())?;
                id.to_string()
            }
        };
        let checkpoint = server_checkpoint_json(&candidate).map_err(|error| error.to_string())?;
        let sequence = events
            .current_batch_event_sequence()
            .map_err(|error| error.to_string())?;
        let operation_name = match operation {
            Operation::Prepare => "prepare",
            Operation::Commit => "commit",
            Operation::Abort => "abort",
        };
        let command_identity = format!(
            "federation:{operation_name}:{id}:sha256:{:x}",
            Sha256::digest(body)
        );
        let command_id = format!("federation:sha256:{:x}", Sha256::digest(command_identity));
        let command = control_command_journal_entry(
            command_id,
            Some(principal.to_string()),
            body,
            &checkpoint,
            sequence,
        );
        events
            .save_checkpoint_with_command(crate::SERVER_CHECKPOINT_SCHEMA, &checkpoint, command)
            .map_err(|error| error.to_string())?;
        events.commit_batch().map_err(|error| error.to_string())?;
        let mut live = controller
            .lock()
            .map_err(|_| "Federation Controller authority is unavailable")?;
        *live = candidate;
        let session = live
            .bridge
            .control()
            .federation_session(&id)
            .ok_or("Federation session disappeared after commit")?;
        Ok(serde_json::json!({
            "schema_version": SCHEMA_VERSION,
            "transaction_id": id,
            "session": session,
            "federation_dispatch_ready": live
                .bridge
                .control()
                .federation_dispatch_ready(&id, now),
        }))
    })();
    if result.is_err() {
        let _ = events.rollback_batch();
    }
    result
}

/// Reads one exact session after a lost or delayed mutation response.
pub(crate) fn view(
    controller: &std::sync::Arc<std::sync::Mutex<ControllerState>>,
    transaction_id: &str,
    local_domain: &str,
    tenant: Option<&str>,
    now: domain::TimestampMs,
) -> Result<serde_json::Value, String> {
    let transaction_id = validate_transaction_id(transaction_id)?;
    let live = controller
        .lock()
        .map_err(|_| "Federation Controller authority is unavailable")?;
    let session = live
        .bridge
        .control()
        .federation_session(&transaction_id)
        .ok_or("unknown Federation transaction")?;
    validate_session_route(session, local_domain, tenant)?;
    Ok(serde_json::json!({
        "schema_version": SCHEMA_VERSION,
        "transaction_id": transaction_id,
        "session": session,
        "federation_dispatch_ready": live
            .bridge
            .control()
            .federation_dispatch_ready(&transaction_id, now),
    }))
}

/// Rejects path identities that could escape the transaction route or exceed journal bounds.
fn validate_transaction_id(value: &str) -> Result<String, String> {
    if value.trim().is_empty()
        || value.len() > 256
        || value.contains('/')
        || value.contains('?')
        || value.contains('#')
    {
        return Err("Federation transaction id is invalid".into());
    }
    Ok(value.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Builds a complete software-only session for route validation.
    fn session() -> FederationSession {
        FederationSession {
            transaction_id: "federation-test".into(),
            source_domain: "domain-a".into(),
            target_domain: "domain-b".into(),
            tenant_id: "tenant-a".into(),
            source_epoch: 1,
            plan_digest: format!("sha256:{}", "a".repeat(64)),
            ownership_digest: format!("sha256:{}", "b".repeat(64)),
            deadline: domain::TimestampMs::new(10),
            state: control::FederationSessionState::Prepared,
        }
    }

    /// A tenant or unrelated configured domain cannot consume the session route.
    #[test]
    fn route_requires_exact_tenant_and_local_domain() {
        let value = session();
        assert!(validate_session_route(&value, "domain-a", Some("tenant-a")).is_ok());
        assert!(validate_session_route(&value, "domain-c", Some("tenant-a")).is_err());
        assert!(validate_session_route(&value, "domain-a", Some("tenant-b")).is_err());
    }

    /// Transaction path identities remain bounded and cannot inject another HTTP route.
    #[test]
    fn transaction_id_rejects_route_escape() {
        assert!(validate_transaction_id("federation-test").is_ok());
        assert!(validate_transaction_id("../commit").is_err());
        assert!(validate_transaction_id("").is_err());
    }
}
