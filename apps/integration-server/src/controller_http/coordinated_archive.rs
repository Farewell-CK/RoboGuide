//! Closed global recovery-admin Archive commands; repeated originals remain readonly evidence.

use super::*;

/// Stable actual HTTP outcome, independent of a publication or physical-lifecycle claim.
type Outcome = (&'static str, serde_json::Value);

/// Complete explicit first command; actual principal/source/epoch are never body fields.
#[derive(serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct Command {
    /// Explicit online coordinated protocol, independent of the old standalone Archive endpoint.
    schema: String,
    /// Original bounded operation identity.
    command_id: String,
    /// Exact checkpoint-covered positive prefix.
    end_sequence: u64,
    /// Original nonrenewable actual admission duration.
    budget_ms: u64,
}

/// Explicit rights-reducing command, never a request to repeat publication.
#[derive(serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct Abort {
    /// Explicit original-abandonment protocol.
    schema: String,
    /// Original operation whose unresolved publication remains evidence.
    original_command_id: String,
}

/// Reports concrete uncertainty instead of claiming that a failed reply produced no effects.
fn outcome(result: Result<serde_json::Value, String>) -> Outcome {
    match result {
        Ok(value) => ("200 OK", value),
        Err(error) => (
            "409 Conflict",
            serde_json::json!({"error":error,"maintenance_outcome":"not_confirmed"}),
        ),
    }
}

/// Reads a bounded complete original journal under the existing committed application gate.
pub(super) fn observe(log: &state::SqliteEventLog, gate: &Arc<AuthorizedRequestGate>) -> Outcome {
    outcome((|| {
        let _writer = gate.lock()?;
        Ok(
            serde_json::json!({"schema":"roboguide.coordinated-archive-status/v0.1","operations":log.coordinated_archive_operations().map_err(|e|e.to_string())?,"history_floor":log.history_floor().map_err(|e|e.to_string())?}),
        )
    })())
}

/// Completes the whole original module operation under its actual writer and request witness.
pub(super) fn command(
    body: &[u8],
    subject: String,
    controller: &Arc<Mutex<ControllerState>>,
    log: &state::SqliteEventLog,
    gate: &Arc<AuthorizedRequestGate>,
    abort: bool,
) -> Outcome {
    outcome((|| {
        if body.len() > 4096 {
            return Err("coordinated Archive command exceeds bound".into());
        }
        let _writer = gate.lock()?;
        if abort {
            let command: Abort = serde_json::from_slice(body).map_err(|e| e.to_string())?;
            if command.schema != "roboguide.coordinated-archive-abort/v0.1" {
                return Err("unsupported coordinated Archive abandonment".into());
            }
            let live = controller
                .lock()
                .map_err(|_| "Archive Controller mutex is poisoned")?;
            gate.validate(&live)?;
            drop(live);
            return serde_json::to_value(
                log.abort_coordinated_archive(&command.original_command_id, &subject, &|| {
                    let live = controller
                        .lock()
                        .map_err(|_| "Archive Controller mutex is poisoned")?;
                    gate.validate(&live)
                })
                .map_err(|e| e.to_string())?,
            )
            .map_err(|e| e.to_string());
        }
        let command: Command = serde_json::from_slice(body).map_err(|e| e.to_string())?;
        if command.schema != "roboguide.coordinated-archive-command/v0.1" {
            return Err("unsupported coordinated Archive command".into());
        }
        let source = {
            let live = controller
                .lock()
                .map_err(|_| "Archive Controller mutex is poisoned")?;
            gate.validate(&live)?;
            live.memory_preparation
                .catalog
                .as_ref()
                .ok_or("Archive original Catalog source is unavailable")?
                .archive_source()?
        };
        let request = state::CoordinatedArchiveRequest {
            command_id: command.command_id,
            principal: subject,
            source_owner: source
                .writer_identity()
                .map_err(|e| e.to_string())?
                .owner_id,
            end_sequence: command.end_sequence,
            budget_ms: command.budget_ms,
        };
        let current = controller.clone();
        let witness = gate.clone();
        let permission = Arc::new(move || {
            let live = current
                .lock()
                .map_err(|_| "Archive Controller mutex is poisoned")?;
            witness.validate(&live)
        });
        let source = application::coordinated_archive::Source::new(source, permission);
        serde_json::to_value(
            log.archive_coordinated(request, &source)
                .map_err(|e| e.to_string())?,
        )
        .map_err(|e| e.to_string())
    })())
}
