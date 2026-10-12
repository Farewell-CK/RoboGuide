//! Orchestration-owned execution holds; no physical-stop, reservation or Runtime authority.

use super::super::*;

/// Default review window for a Mission-local Hold; expiry escalates only and never releases work.
pub const MISSION_HOLD_REVIEW_WINDOW_MS: u64 = 5 * 60 * 1_000;

/// Exact derived-work category whose rejected transition remains replayable from durable authority.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum MissionIsolationSource {
    /// Accepted-plan Ready/committed Task dispatch.
    ReadyDispatch,
    /// Durable Runtime terminal-attempt outcome application.
    TaskOutcome,
}

/// A typed, generation-bound Mission hold retaining work and ownership for explicit revalidation.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MissionIsolation {
    /// Monotonic generation required by operator resume, never reset by restart.
    pub generation: u64,
    /// Derived-work boundary that conclusively rejected this Mission's contract.
    pub source: MissionIsolationSource,
    /// Current local decision time at which the hold was committed.
    pub isolated_at: TimestampMs,
    /// Durable local review deadline; absence means legacy evidence has no SLA claim.
    #[serde(default)]
    pub review_by: Option<TimestampMs>,
    /// Monotonic operator-visible escalation level, never a retry or release decision.
    #[serde(default)]
    pub escalation_level: u8,
}

impl MissionIsolation {
    /// Returns the hold age in the same RoboGuide-local clock domain.
    pub const fn age_ms(&self, now: TimestampMs) -> u64 {
        now.as_millis().saturating_sub(self.isolated_at.as_millis())
    }

    /// Returns whether the durable review deadline has elapsed.
    pub const fn is_review_due(&self, now: TimestampMs) -> bool {
        match self.review_by {
            Some(review_by) => now.as_millis() >= review_by.as_millis(),
            None => false,
        }
    }

    /// Returns the next operator escalation level without changing execution authority.
    pub const fn escalation_level_at(&self, now: TimestampMs) -> u8 {
        if self.is_review_due(now) {
            1
        } else {
            self.escalation_level
        }
    }
}

impl MissionOrchestrator {
    /// Records only a caller-proven contract-local failure; caller must rollback the failed candidate.
    pub fn isolate_execution<E: EventSink>(
        &mut self,
        mission_id: &MissionId,
        source: MissionIsolationSource,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<(), OrchestrationError> {
        let execution = self
            .executions
            .get_mut(mission_id)
            .ok_or_else(|| OrchestrationError::Mission("unknown Mission isolation scope".into()))?;
        if !matches!(
            execution.lifecycle,
            MissionExecutionLifecycle::Accepted | MissionExecutionLifecycle::Running
        ) {
            return Err(OrchestrationError::Mission(
                "isolation requires a nonterminal noncancelling Mission".into(),
            ));
        }
        if execution.isolation.is_some() {
            return Ok(());
        }
        let generation = execution
            .isolation_generation
            .checked_add(1)
            .ok_or_else(|| OrchestrationError::Mission("isolation generation exhausted".into()))?;
        execution.isolation_generation = generation;
        execution.isolation = Some(MissionIsolation {
            generation,
            source,
            isolated_at: timestamp,
            review_by: timestamp
                .as_millis()
                .checked_add(MISSION_HOLD_REVIEW_WINDOW_MS)
                .map(TimestampMs::new),
            escalation_level: 0,
        });
        events.append(
            timestamp,
            correlation_id,
            None,
            domain::EventPayload::MissionExecutionIsolated {
                mission_id: mission_id.clone(),
                generation,
                source: match source {
                    MissionIsolationSource::ReadyDispatch => "ready-dispatch",
                    MissionIsolationSource::TaskOutcome => "task-outcome",
                }
                .into(),
            },
        );
        Ok(())
    }

    /// Removes exactly the current hold after application-owned live revalidation, never authorizing replay.
    pub fn resume_isolated_execution<E: EventSink>(
        &mut self,
        mission_id: &MissionId,
        generation: u64,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<(), OrchestrationError> {
        let execution = self
            .executions
            .get_mut(mission_id)
            .ok_or_else(|| OrchestrationError::Mission("unknown Mission resume scope".into()))?;
        if execution
            .isolation
            .as_ref()
            .is_none_or(|hold| hold.generation != generation)
            || !matches!(
                execution.lifecycle,
                MissionExecutionLifecycle::Accepted | MissionExecutionLifecycle::Running
            )
        {
            return Err(OrchestrationError::Mission(
                "resume generation does not match the current execution hold".into(),
            ));
        }
        execution.isolation = None;
        events.append(
            timestamp,
            correlation_id,
            None,
            domain::EventPayload::MissionExecutionResumed {
                mission_id: mission_id.clone(),
                generation,
            },
        );
        Ok(())
    }
}
