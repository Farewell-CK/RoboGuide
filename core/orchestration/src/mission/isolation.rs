//! Orchestration-owned execution holds; no physical-stop, reservation or Runtime authority.

use super::super::*;

/// Default review window for a Mission-local Hold; expiry escalates only and never releases work.
pub const MISSION_HOLD_REVIEW_WINDOW_MS: u64 = 5 * 60 * 1_000;

/// Number of identical local failures within the breaker window that quarantines a Mission.
pub const MISSION_CRASH_LOOP_THRESHOLD: u32 = 3;

/// Time window in which repeated identical failures are considered one crash loop.
pub const MISSION_CRASH_LOOP_WINDOW_MS: u64 = 10 * 60 * 1_000;

/// Exact derived-work category whose rejected transition remains replayable from durable authority.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum MissionIsolationSource {
    /// Accepted-plan Ready/committed Task dispatch.
    ReadyDispatch,
    /// Durable Runtime terminal-attempt outcome application.
    TaskOutcome,
}

/// Typed local failure category retained for operator review and crash-loop accounting.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum MissionFailureClass {
    /// The accepted Mission contract was rejected before physical work was changed.
    InvalidContract,
    /// One accepted Task's typed Runtime completion contract was rejected.
    RuntimeContractViolation,
    /// Legacy or manually created hold without a typed failure classification.
    Unknown,
}

impl Default for MissionFailureClass {
    /// Old checkpoints and manually created holds remain explicitly unclassified.
    fn default() -> Self {
        Self::Unknown
    }
}

/// Durable repeated-failure evidence independent of free-form diagnostic text.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MissionFailureState {
    /// At most three actual failure receive times; None preserves unavailable legacy window evidence.
    #[serde(default)]
    pub recent_failed_at: Option<Vec<TimestampMs>>,
    /// Typed source category used by the breaker.
    #[serde(default)]
    pub class: MissionFailureClass,
    /// Stable digest of the typed failure identity.
    #[serde(default)]
    pub fingerprint: String,
    /// Consecutive occurrences for this fingerprint in the current window.
    #[serde(default)]
    pub count: u32,
    /// First occurrence in the current repeated-failure window.
    #[serde(default = "zero_timestamp")]
    pub first_failed_at: TimestampMs,
    /// Most recent occurrence in the current repeated-failure window.
    #[serde(default = "zero_timestamp")]
    pub last_failed_at: TimestampMs,
    /// Human-approved quarantine is required before new Execute work is allowed.
    #[serde(default)]
    pub quarantined: bool,
}

/// Supplies the legacy zero receive-time when old checkpoints omit failure evidence.
fn zero_timestamp() -> TimestampMs {
    TimestampMs::new(0)
}

impl Default for MissionFailureState {
    /// Legacy holds carry no typed failure evidence until a new failure is classified.
    fn default() -> Self {
        Self {
            recent_failed_at: Some(Vec::new()),
            class: MissionFailureClass::Unknown,
            fingerprint: String::new(),
            count: 0,
            first_failed_at: TimestampMs::new(0),
            last_failed_at: TimestampMs::new(0),
            quarantined: false,
        }
    }
}

impl MissionFailureState {
    /// Builds the next durable state without treating a different typed failure as a loop.
    pub fn next(
        previous: Option<&Self>,
        class: MissionFailureClass,
        fingerprint: String,
        timestamp: TimestampMs,
    ) -> Self {
        // An unreviewed quarantine cannot expire or change identity as a side effect of a new failure.
        if let Some(previous) = previous.filter(|previous| previous.quarantined) {
            return previous.clone();
        }
        let repeated = previous
            .filter(|previous| previous.class == class && previous.fingerprint == fingerprint);
        let mut recent = repeated
            .map(|previous| {
                previous
                    .recent_failed_at
                    .clone()
                    .unwrap_or_else(|| match previous.count {
                        1 => vec![previous.last_failed_at],
                        2 => vec![previous.first_failed_at, previous.last_failed_at],
                        _ => Vec::new(),
                    })
            })
            .unwrap_or_default();
        recent.retain(|failed_at| {
            *failed_at <= timestamp
                && timestamp.as_millis() - failed_at.as_millis() <= MISSION_CRASH_LOOP_WINDOW_MS
        });
        recent.push(timestamp);
        if recent.len() > MISSION_CRASH_LOOP_THRESHOLD as usize {
            recent.remove(0);
        }
        let count = recent.len() as u32;
        Self {
            first_failed_at: recent[0],
            recent_failed_at: Some(recent),
            class,
            fingerprint,
            count,
            last_failed_at: timestamp,
            quarantined: count >= MISSION_CRASH_LOOP_THRESHOLD,
        }
    }

    /// Rejects malformed durable authority; legacy counters retain quarantine without inventing samples.
    pub fn validate_checkpoint(&self, accepted_at: TimestampMs) -> Result<(), OrchestrationError> {
        let invalid_samples = self.recent_failed_at.as_ref().is_some_and(|samples| {
            samples.len() > MISSION_CRASH_LOOP_THRESHOLD as usize
                || samples.len() != self.count as usize
                || samples.windows(2).any(|pair| pair[0] > pair[1])
                || samples
                    .first()
                    .is_some_and(|first| *first != self.first_failed_at)
                || samples
                    .last()
                    .is_some_and(|last| *last != self.last_failed_at)
                || self
                    .last_failed_at
                    .as_millis()
                    .saturating_sub(self.first_failed_at.as_millis())
                    > MISSION_CRASH_LOOP_WINDOW_MS
                    && !samples.is_empty()
        });
        if self.fingerprint.len() > 128
            || (self.count > 0
                && (self.fingerprint.is_empty()
                    || self.first_failed_at < accepted_at
                    || self.last_failed_at < self.first_failed_at))
            || self.quarantined != (self.count >= MISSION_CRASH_LOOP_THRESHOLD)
            || invalid_samples
        {
            return Err(OrchestrationError::Mission(
                "inconsistent Mission failure checkpoint authority".into(),
            ));
        }
        Ok(())
    }
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
    /// Typed repeated-failure state that caused this hold.
    #[serde(default)]
    pub failure: MissionFailureState,
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
        self.isolate_execution_with_failure(
            mission_id,
            source,
            MissionFailureClass::Unknown,
            format!("legacy:{source:?}"),
            timestamp,
            correlation_id,
            events,
        )
    }

    /// Records a typed local failure and opens or updates a Mission-local hold.
    #[allow(clippy::too_many_arguments)]
    pub fn isolate_execution_with_failure<E: EventSink>(
        &mut self,
        mission_id: &MissionId,
        source: MissionIsolationSource,
        failure_class: MissionFailureClass,
        failure_fingerprint: String,
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
        if timestamp < execution.accepted_at
            || execution
                .failure_state
                .as_ref()
                .is_some_and(|failure| timestamp < failure.last_failed_at)
            || failure_fingerprint.is_empty()
            || failure_fingerprint.len() > 128
        {
            return Err(OrchestrationError::Mission(
                "invalid Mission failure identity or receive time".into(),
            ));
        }
        let generation = execution
            .isolation_generation
            .checked_add(1)
            .ok_or_else(|| OrchestrationError::Mission("isolation generation exhausted".into()))?;
        let failure = MissionFailureState::next(
            execution.failure_state.as_ref(),
            failure_class,
            failure_fingerprint,
            timestamp,
        );
        execution.isolation_generation = generation;
        execution.failure_state = Some(failure.clone());
        execution.isolation = Some(MissionIsolation {
            generation,
            source,
            isolated_at: timestamp,
            review_by: timestamp
                .as_millis()
                .checked_add(MISSION_HOLD_REVIEW_WINDOW_MS)
                .map(TimestampMs::new),
            escalation_level: 0,
            failure: failure.clone(),
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
                failure_class: Some(format!("{:?}", failure.class)),
                failure_fingerprint: Some(failure.fingerprint.clone()),
                failure_count: failure.count,
                crash_loop: failure.quarantined,
                failure_window: failure.recent_failed_at.clone(),
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
        if execution
            .failure_state
            .as_ref()
            .is_some_and(|failure| failure.quarantined)
        {
            return Err(OrchestrationError::Mission(
                "Mission crash-loop quarantine requires explicit operator acknowledgement".into(),
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

    /// Clears a crash-loop quarantine only after the application has revalidated live authority.
    #[allow(clippy::too_many_arguments)]
    pub fn clear_crash_loop_quarantine<E: EventSink>(
        &mut self,
        mission_id: &MissionId,
        generation: u64,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
        operator_role: String,
        operator_reason: String,
    ) -> Result<(), OrchestrationError> {
        let execution = self.executions.get_mut(mission_id).ok_or_else(|| {
            OrchestrationError::Mission("unknown Mission quarantine scope".into())
        })?;
        if execution
            .isolation
            .as_ref()
            .is_none_or(|hold| hold.generation != generation)
        {
            return Err(OrchestrationError::Mission(
                "quarantine generation does not match the current execution hold".into(),
            ));
        }
        let Some(failure) = execution.failure_state.as_mut() else {
            return Err(OrchestrationError::Mission(
                "Mission has no crash-loop evidence".into(),
            ));
        };
        if operator_role.trim().is_empty() || operator_reason.trim().is_empty() {
            return Err(OrchestrationError::Mission(
                "crash-loop acknowledgement requires operator role and reason".into(),
            ));
        }
        if operator_role.len() > 64 || operator_reason.len() > 512 {
            return Err(OrchestrationError::Mission(
                "crash-loop acknowledgement role/reason exceeds bounds".into(),
            ));
        }
        if !failure.quarantined {
            return Ok(());
        }
        let failure_count = failure.count;
        let failure_window = failure.recent_failed_at.clone();
        failure.count = 0;
        failure.recent_failed_at = Some(Vec::new());
        failure.first_failed_at = timestamp;
        failure.last_failed_at = timestamp;
        failure.quarantined = false;
        execution
            .isolation
            .as_mut()
            .expect("hold validated above")
            .failure = failure.clone();
        events.append(
            timestamp,
            correlation_id,
            None,
            domain::EventPayload::MissionExecutionQuarantineCleared {
                mission_id: mission_id.clone(),
                generation,
                failure_count,
                operator_role,
                operator_reason,
                failure_window,
            },
        );
        Ok(())
    }
}
