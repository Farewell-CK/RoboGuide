//! Explicit future planning preemption shares the existing durable candidate transaction.

use crate::*;

/// Closed HTTP request cannot authorize a stop, select an executor or change a Mission plan.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct PreemptionCommand {
    /// Accepted lower-priority Mission namespace.
    victim_mission_id: String,
    /// Its exact currently future Task.
    victim_task_id: String,
    /// Calendar generation reviewed by the caller.
    expected_calendar_version: u64,
}

/// Validates current logical policy and atomically releases only an uncommitted future interval.
pub(super) fn preempt(
    controller: &Arc<Mutex<ControllerState>>,
    events: &state::SqliteEventLog,
    gate: &dyn crate::application::ApplicationWriteGate,
    clock: &impl Clock,
    path: &str,
    body: &str,
    diagnostics: &ports::DiagnosticHandle,
) -> Result<(&'static str, serde_json::Value), Box<dyn std::error::Error + Send + Sync>> {
    let request = (|| {
        let command: PreemptionCommand =
            serde_json::from_str(body).map_err(|error| error.to_string())?;
        Ok::<_, String>(orchestration::FutureSchedulingPreemption {
            beneficiary: domain::MissionId::new(
                path.trim_start_matches("/v1/missions/")
                    .trim_end_matches("/scheduling-preemption"),
            )
            .map_err(|error| error.to_string())?,
            victim: domain::TaskRef::new(
                domain::MissionId::new(command.victim_mission_id)
                    .map_err(|error| error.to_string())?,
                domain::TaskId::new(command.victim_task_id).map_err(|error| error.to_string())?,
            ),
            expected_calendar_version: command.expected_calendar_version,
        })
    })();
    let request = match request {
        Ok(request) => request,
        Err(error) => return Ok(("400 Bad Request", serde_json::json!({"error":error}))),
    };
    let _gate = gate.lock()?;
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
        let correlation = domain::CorrelationId::new(format!("preemption-{}", request.beneficiary))
            .map_err(|error| ("400 Bad Request", error.to_string()))?;
        candidate
            .orchestrator
            .preempt_future_schedule(
                &request,
                candidate.bridge.control_mut(),
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
            serde_json::json!({"status":"future-reservation-preempted","victim":request.victim,
            "beneficiary_mission_id":request.beneficiary.as_str(),"expected_calendar_version":request.expected_calendar_version}),
        )),
        Err((status, error)) => match events.rollback_batch() {
            Ok(()) => Ok((status, serde_json::json!({"error":error}))),
            Err(rollback) => Ok((
                "503 Service Unavailable",
                serde_json::json!({"error":format!("{error}; rollback failed: {rollback}")}),
            )),
        },
    }
}
