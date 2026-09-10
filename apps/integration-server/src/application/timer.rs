//! Time-driven application transaction orchestration.

use crate::*;
/// Persists all time-driven application transitions before exposing their new live projection.
pub(crate) fn drive_application_timer(
    controller: &Arc<Mutex<ControllerState>>,
    event_log: &state::SqliteEventLog,
    event_write_gate: &Arc<Mutex<()>>,
    now: domain::TimestampMs,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let _write_guard = event_write_gate
        .lock()
        .map_err(|_| "event-log write gate is poisoned")?;
    event_log.begin_batch()?;
    let transition = (|| {
        let live = controller
            .lock()
            .map_err(|_| "controller lock is poisoned")?;
        let mut candidate = live.clone();
        drop(live);
        let correlation = domain::CorrelationId::new("application-timer")?;
        candidate.bridge.tick(now, &correlation)?;
        let mut events = event_log.clone();
        apply_runtime_events(&mut candidate, now, &correlation, &mut events)?;
        begin_current_ambiguity_recoveries(&mut candidate, now, &correlation, &mut events)?;
        resume_pending_recoveries(&mut candidate, now, &correlation, &mut events)?;
        apply_pending_cancellations(&mut candidate, now, &correlation, &mut events)?;
        apply_runtime_outcomes(&mut candidate, now, &correlation, &mut events)?;
        drive_ready_tasks(&mut candidate, now, &correlation, &mut events)?;
        drive_rebound_attempts(&mut candidate, now, &correlation)?;
        if let Some(error) = event_log.take_error()? {
            return Err(format!("application timer event sink failed: {error}").into());
        }
        let checkpoint = server_checkpoint_json(&candidate)?;
        event_log.save_checkpoint(SERVER_CHECKPOINT_SCHEMA, &checkpoint)?;
        event_log.commit_batch()?;
        let mut live = controller
            .lock()
            .map_err(|_| "controller lock is poisoned")?;
        *live = candidate;
        if let Err(error) = live.bridge.flush_command_outboxes() {
            eprintln!("durable command outbox delivery deferred: {error}");
        }
        Ok::<(), Box<dyn std::error::Error + Send + Sync>>(())
    })();
    if let Err(error) = transition {
        let _ = event_log.rollback_batch();
        return Err(error);
    }
    Ok(())
}
