//! Orchestration-owned execution holds; no physical-stop, reservation or Runtime authority.

use super::super::*;

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
