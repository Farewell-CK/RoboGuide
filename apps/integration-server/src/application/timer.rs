//! Time-driven application transaction orchestration.

use crate::diagnostics::StageObservation;
use crate::*;
use ports::{DiagnosticKind, DiagnosticStage};
/// Persists all time-driven application transitions before exposing their new live projection.
#[cfg(test)]
pub(crate) fn drive_application_timer(
    controller: &Arc<Mutex<ControllerState>>,
    event_log: &state::SqliteEventLog,
    event_write_gate: &Arc<Mutex<()>>,
    now: domain::TimestampMs,
    verifier_feed: Option<&TaskVerifierFeed>,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    drive_application_timer_with_clock(
        controller,
        event_log,
        event_write_gate,
        &runtime::FixedClock::new(now),
        verifier_feed,
    )
}

/// Uses one receive clock for transition time and a fresh read after durable Commit for delivery.
pub(crate) fn drive_application_timer_with_clock(
    controller: &Arc<Mutex<ControllerState>>,
    event_log: &state::SqliteEventLog,
    event_write_gate: &Arc<Mutex<()>>,
    clock: &impl Clock,
    verifier_feed: Option<&TaskVerifierFeed>,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let now = clock.now();
    let gate_started = std::time::Instant::now();
    let _write_guard = event_write_gate
        .lock()
        .map_err(|_| "event-log write gate is poisoned")?;
    let gate_elapsed = gate_started.elapsed();
    event_log.begin_batch()?;
    let transition = (|| {
        let lock_started = std::time::Instant::now();
        let live = controller
            .lock()
            .map_err(|_| "controller lock is poisoned")?;
        let diagnostics = live.bridge.diagnostics().clone();
        diagnostics.timing(
            DiagnosticKind::Timer,
            DiagnosticStage::WriteGate,
            gate_elapsed,
            None,
        );
        diagnostics.timing(
            DiagnosticKind::Timer,
            DiagnosticStage::ControllerLock,
            lock_started.elapsed(),
            None,
        );
        let clone_observation = StageObservation::start(
            &diagnostics,
            DiagnosticKind::Timer,
            DiagnosticStage::Clone,
            None,
        );
        let mut candidate = live.clone();
        drop(clone_observation);
        drop(live);
        let reduction = StageObservation::start(
            &diagnostics,
            DiagnosticKind::Timer,
            DiagnosticStage::Reduce,
            None,
        );
        let correlation = domain::CorrelationId::new("application-timer")?;
        candidate.bridge.tick(now, &correlation)?;
        let mut events = event_log.clone();
        apply_runtime_events(&mut candidate, now, &correlation, &mut events)?;
        begin_current_ambiguity_recoveries(&mut candidate, now, &correlation, &mut events)?;
        resume_pending_recoveries(&mut candidate, now, &correlation, &mut events)?;
        apply_pending_cancellations(&mut candidate, now, &correlation, &mut events)?;
        apply_runtime_outcomes(&mut candidate, now, &correlation, &mut events)?;
        if let Some(feed) = verifier_feed {
            match feed.read_verdict() {
                Ok(Some(verdict)) => match validate_task_verifier(&candidate, feed, &verdict) {
                    Ok(()) => apply_task_verifier(
                        &mut candidate,
                        feed,
                        &verdict,
                        now,
                        &correlation,
                        &mut events,
                    )?,
                    Err(error) => eprintln!("task verifier verdict rejected: {error}"),
                },
                Ok(None) => {}
                Err(error) => eprintln!("task verifier evidence unavailable: {error}"),
            }
        }
        drive_ready_tasks(&mut candidate, now, &correlation, &mut events)?;
        drive_rebound_attempts(&mut candidate, now, &correlation)?;
        if let Some(error) = event_log.take_error()? {
            return Err(format!("application timer event sink failed: {error}").into());
        }
        drop(reduction);
        crate::diagnostics::observe_node_ages(&candidate.bridge, now);
        let serialization = StageObservation::start(
            &diagnostics,
            DiagnosticKind::Timer,
            DiagnosticStage::Serialize,
            None,
        );
        let checkpoint = server_checkpoint_json(&candidate)?;
        drop(serialization);
        diagnostics.value(
            DiagnosticKind::Timer,
            DiagnosticStage::CheckpointBytes,
            checkpoint.len() as u64,
            None,
        );
        let save = StageObservation::start(
            &diagnostics,
            DiagnosticKind::Timer,
            DiagnosticStage::Save,
            None,
        );
        event_log.save_checkpoint(SERVER_CHECKPOINT_SCHEMA, &checkpoint)?;
        drop(save);
        let commit = StageObservation::start(
            &diagnostics,
            DiagnosticKind::Timer,
            DiagnosticStage::Commit,
            None,
        );
        event_log.commit_batch()?;
        drop(commit);
        let mut live = controller
            .lock()
            .map_err(|_| "controller lock is poisoned")?;
        *live = candidate;
        let _outbox = StageObservation::start(
            &diagnostics,
            DiagnosticKind::Timer,
            DiagnosticStage::Outbox,
            None,
        );
        if let Err(error) = live.bridge.flush_command_outboxes_with_clock(clock) {
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
