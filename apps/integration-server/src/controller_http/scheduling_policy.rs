//! Operator logical priority changes share the existing event/checkpoint acceptance boundary.

use crate::*;

/// Closed logical policy input cannot carry an executor, plan change or stop authorization.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct PriorityCommand {
    /// Requested priority in the complete supported scalar range.
    priority: u8,
}

/// Changes one accepted Mission's logical priority on a private candidate, with full SQL rollback.
pub(super) fn update_priority(
    controller: &Arc<Mutex<ControllerState>>,
    events: &state::SqliteEventLog,
    gate: &dyn crate::application::ApplicationWriteGate,
    clock: &impl Clock,
    path: &str,
    body: &str,
    diagnostics: &ports::DiagnosticHandle,
) -> Result<(&'static str, serde_json::Value), Box<dyn std::error::Error + Send + Sync>> {
    let command: PriorityCommand = match serde_json::from_str(body) {
        Ok(command) => command,
        Err(_) => {
            return Ok((
                "400 Bad Request",
                serde_json::json!({"error":"priority must be an integer in 0..255, with no other fields"}),
            ));
        }
    };
    let mission = domain::MissionId::new(
        path.trim_start_matches("/v1/missions/")
            .trim_end_matches("/scheduling-policy"),
    )?;
    let _guard = gate.lock()?;
    events.begin_batch()?;
    let result = (|| {
        let mut live = controller.lock().map_err(|_| {
            (
                "503 Service Unavailable",
                "controller lock is poisoned".to_string(),
            )
        })?;
        let mut candidate = live.clone();
        let now = clock.now();
        candidate
            .bridge
            .control()
            .allocation_snapshot(now)
            .map_err(|error| ("503 Service Unavailable", error.to_string()))?;
        candidate
            .orchestrator
            .validate_control_authority(candidate.bridge.control())
            .map_err(|error| ("503 Service Unavailable", error.to_string()))?;
        let correlation = domain::CorrelationId::new(format!("priority-{mission}"))
            .map_err(|error| ("400 Bad Request", error.to_string()))?;
        candidate
            .orchestrator
            .set_mission_priority(
                &mission,
                command.priority,
                now,
                &correlation,
                &mut events.clone(),
            )
            .map_err(|error| ("409 Conflict", error.to_string()))?;
        if let Some(error) = events
            .take_error()
            .map_err(|error| ("503 Service Unavailable", error.to_string()))?
        {
            return Err(("503 Service Unavailable", error));
        }
        let checkpoint = crate::diagnostics::http_checkpoint(&candidate)
            .map_err(|error| ("503 Service Unavailable", error.to_string()))?;
        crate::diagnostics::http_save(events, &checkpoint, diagnostics)
            .map_err(|error| ("503 Service Unavailable", error.to_string()))?;
        crate::diagnostics::http_commit(events, diagnostics)
            .map_err(|error| ("503 Service Unavailable", error.to_string()))?;
        *live = candidate;
        Ok::<_, (&str, String)>(())
    })();
    match result {
        Ok(()) => Ok((
            "200 OK",
            serde_json::json!({"mission_id":mission.as_str(),"priority":command.priority}),
        )),
        Err((status, error)) => {
            if let Err(rollback) = events.rollback_batch() {
                return Ok((
                    "503 Service Unavailable",
                    serde_json::json!({"error":format!("{error}; rollback failed: {rollback}")}),
                ));
            }
            Ok((status, serde_json::json!({"error":error})))
        }
    }
}
