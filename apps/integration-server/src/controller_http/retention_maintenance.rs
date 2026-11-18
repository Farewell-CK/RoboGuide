//! Administrator-only policy authority; all mutations retain the original request witness.

use super::*;

/// Reads one finite assessment under the same writer used for source/reference changes.
pub(super) fn observe(
    log: &state::SqliteEventLog,
    gate: &Arc<AuthorizedRequestGate>,
    now: domain::TimestampMs,
) -> (&'static str, serde_json::Value) {
    let result = (|| {
        let _guard = gate.lock()?;
        let retention = application::retention::assess(log, now)?;
        let archive_only = log
            .retention_archive_status(now)
            .map_err(|error| error.to_string())?;
        let storage = log
            .telemetry_storage_metrics()
            .map_err(|error| error.to_string())?;
        Ok::<_, String>(
            serde_json::json!({"retention":retention,"archive_only":archive_only,"storage":storage,"permanent_expiry_enabled":false}),
        )
    })();
    render(result)
}

/// Accepts a complete versioned policy using explicit command identity and optimistic revision.
pub(super) fn update(
    body: &[u8],
    controller: &Arc<Mutex<ControllerState>>,
    log: &state::SqliteEventLog,
    gate: &Arc<AuthorizedRequestGate>,
    now: domain::TimestampMs,
) -> (&'static str, serde_json::Value) {
    if body.len() > 8192 {
        return (
            "400 Bad Request",
            serde_json::json!({"error":"retention policy request exceeds 8192 bytes"}),
        );
    }
    let command = match serde_json::from_slice::<state::RetentionPolicyCommand>(body) {
        Ok(command) => command,
        Err(error) => {
            return (
                "400 Bad Request",
                serde_json::json!({"error":error.to_string()}),
            );
        }
    };
    render(application::retention::update(
        controller, log, gate, &command, now,
    ))
}

/// Keeps unavailable storage/reference evidence visible instead of returning empty history.
fn render<T: serde::Serialize>(result: Result<T, String>) -> (&'static str, serde_json::Value) {
    match result {
        Ok(value) => (
            "200 OK",
            serde_json::json!({"schema":"roboguide.retention-management/v0.1","result":value}),
        ),
        Err(error) => ("409 Conflict", serde_json::json!({"error":error})),
    }
}
