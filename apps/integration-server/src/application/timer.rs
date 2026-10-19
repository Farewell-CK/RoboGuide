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
        let now = clock.now();
        let reduction = StageObservation::start(
            &diagnostics,
            DiagnosticKind::Timer,
            DiagnosticStage::Reduce,
            None,
        );
        let correlation = domain::CorrelationId::new("application-timer")?;
        candidate.bridge.tick(now, &correlation)?;
        candidate.bridge.refresh_beliefs(now, &correlation)?;
        let mut events = event_log.clone();
        apply_runtime_events(&mut candidate, now, &correlation, &mut events)?;
        safety_stop::tick(&mut candidate, now)?;
        super::memory_handoff::tick(&mut candidate, now)?;
        begin_current_ambiguity_recoveries(&mut candidate, now, &correlation, &mut events)?;
        resume_pending_recoveries(&mut candidate, now, &correlation, &mut events)?;
        apply_pending_cancellations(&mut candidate, now, &correlation, &mut events)?;
        apply_runtime_outcomes(&mut candidate, now, &correlation, &mut events)?;
        semantic_verifier::apply_pending(&mut candidate, now, &correlation, &mut events)?;
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
        semantic_verifier::monitor_active(&mut candidate, now)?;
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
        super::memory_preparation::prepare(&mut candidate, now)?;
        super::memory_use::prepare(&mut candidate, now)?;
        super::memory_import::prepare(&mut candidate, now);
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
        let event_sequence = event_log.current_batch_event_sequence()?;
        let input = format!(
            "timer:{}:verifier={}",
            now.as_millis(),
            verifier_feed.is_some()
        );
        let command = control_command_journal_entry(
            format!("application-timer:{event_sequence}:{}", now.as_millis()),
            Some("integration.application-timer".to_string()),
            input.as_bytes(),
            &checkpoint,
            event_sequence,
        );
        event_log.save_checkpoint_with_command(SERVER_CHECKPOINT_SCHEMA, &checkpoint, command)?;
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
        let has_memory_imports = !candidate.memory_imports.is_empty();
        *live = candidate;
        super::memory_handoff::install_catalog(&live)?;
        if !live.memory_preparation.records.is_empty() {
            super::memory_preparation::flush(&live, now, clock.now());
        }
        if !live.memory_use.records.is_empty() {
            super::memory_use::flush(&live, now, clock.now());
        }
        if has_memory_imports {
            super::memory_import::flush(&live, now, clock.now());
        }
        let _outbox = StageObservation::start(
            &diagnostics,
            DiagnosticKind::Timer,
            DiagnosticStage::Outbox,
            None,
        );
        if let Err(error) = flush_unisolated_commands(&mut live, clock) {
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
