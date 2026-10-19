//! Versioned, transport-neutral physical stop evidence contract.
//!
//! Local EAIOS remains the immediate safety authority. RoboGuide only consumes this exact
//! attempt-bound evidence and may release or continue ownership after a timely `StopConfirmed`.
//! A command receipt, lease expiry, heartbeat loss, or checkpoint restore cannot manufacture a
//! confirmation.

use crate::{ExecutionGroupId, OperationRef, RoleId, TaskRef, TimestampMs};
use std::fmt::{Display, Formatter};

/// Version identifier for the generic physical stop contract.
pub const PHYSICAL_STOP_CONTRACT_SCHEMA_V0_1: &str = "roboguide.physical-stop-contract/v0.1";

/// LocalSystem metadata declaring actual operation-owned safety observation support.
pub const EXECUTION_SAFETY_METADATA_KEY: &str = "roboguide.execution-safety";
/// Payload schema separating software-process and device observations.
pub const SAFE_STATE_OBSERVATION_SCHEMA: &str = "roboguide.safe-state-observation/v0.2";

/// Version explicitly carrying original process-exit coverage.
pub const PROCESS_EXIT_OBSERVATION_SCHEMA: &str = "roboguide.safe-state-observation/v0.3";

/// Evidence assurance declared by a LocalSystem; a software process never proves device stopping.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum SafetyAssurance {
    /// The provider observes its real operating-system process lifecycle.
    SoftwareProcess,
    /// The provider observes device state; physical accuracy requires separate hardware validation.
    DeviceObservation,
}

/// Exact safety observation and timing policy for one registered operation.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OperationSafetySupport {
    /// Canonical operation owned by the declaring LocalSystem.
    pub operation: OperationRef,
    /// Exact registered State export that provides attempt-bound safe state.
    pub safe_state_export_id: String,
    /// Maximum receive-relative evidence age, independent of the producer's clock.
    pub max_evidence_age_ms: u64,
    /// Maximum stop wait; it cannot be renewed by retry or restart.
    pub stop_deadline_ms: u64,
    /// Optional configured confidence floor; absence supplies no invented confidence.
    #[serde(default)]
    pub min_confidence_millionths: Option<u32>,
    /// Evidence assurance required from this exact operation's safe-state payload.
    pub assurance: SafetyAssurance,
}

/// Bounded configuration-owned operation safety profile; it never grants stopping or resource authority.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ExecutionSafetyProfile {
    /// Versioned safety support declaration.
    pub schema: String,
    /// Exact operations for this LocalSystem, with no implicit wildcard support.
    pub operations: Vec<OperationSafetySupport>,
}

impl ExecutionSafetyProfile {
    /// Parses bounded support and rejects ambiguous exports, operations and timing before admission.
    pub fn from_metadata(raw: &str) -> Result<Self, SafetyStopError> {
        if raw.len() > 8192 {
            return Err(SafetyStopError::InvalidField("safety profile bounds"));
        }
        let profile: Self = serde_json::from_str(raw)
            .map_err(|_| SafetyStopError::InvalidField("safety profile codec"))?;
        if profile.schema != "roboguide.execution-safety/v0.1"
            || profile.operations.is_empty()
            || profile.operations.len() > 32
        {
            return Err(SafetyStopError::InvalidField(
                "safety profile schema/operations",
            ));
        }
        let mut operations = std::collections::BTreeSet::new();
        for support in &profile.operations {
            if !operations.insert(support.operation.clone())
                || support.safe_state_export_id.trim().is_empty()
                || support.safe_state_export_id.len() > 128
                || support.max_evidence_age_ms == 0
                || support.max_evidence_age_ms > 3_600_000
                || support.stop_deadline_ms == 0
                || support.stop_deadline_ms > 3_600_000
                || support
                    .min_confidence_millionths
                    .is_some_and(|value| value > 1_000_000)
            {
                return Err(SafetyStopError::InvalidField("operation safety support"));
            }
        }
        Ok(profile)
    }
}

/// Ordered safety-stop phases shared by Runtime, Node Service, and Local EAIOS adapters.
#[derive(
    Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, serde::Serialize, serde::Deserialize,
)]
#[serde(rename_all = "snake_case")]
pub enum SafetyStopPhase {
    /// RoboGuide durably requested a stop for the exact current attempt.
    StopRequested,
    /// Node Service durably accepted the stop command for local dispatch.
    StopCommandAccepted,
    /// Local EAIOS observed its configured safe state.
    SafeStateObserved,
    /// A current natural terminal fact has independent original-worker exit evidence.
    TerminalProcessObserved,
    /// RoboGuide received a timely, attempt-bound terminal stop confirmation.
    StopConfirmed,
    /// Control prepared a continuation after retaining the stop evidence.
    ContinuationPrepared,
    /// The affected execution group is durably stopped and will not continue automatically.
    GroupStopped,
    /// The stop deadline or safety policy requires human handling.
    ManualEscalation,
}

impl SafetyStopPhase {
    /// Returns whether a transition is valid for the generic contract.
    const fn can_follow(self, next: Self) -> bool {
        matches!(
            (self, next),
            (Self::StopRequested, Self::StopCommandAccepted)
                | (Self::StopRequested, Self::TerminalProcessObserved)
                | (Self::TerminalProcessObserved, Self::StopConfirmed)
                | (Self::TerminalProcessObserved, Self::ManualEscalation)
                | (Self::StopRequested, Self::ManualEscalation)
                | (Self::StopCommandAccepted, Self::SafeStateObserved)
                | (Self::StopCommandAccepted, Self::ManualEscalation)
                | (Self::SafeStateObserved, Self::StopConfirmed)
                | (Self::SafeStateObserved, Self::ManualEscalation)
                | (Self::StopConfirmed, Self::ContinuationPrepared)
                | (Self::StopConfirmed, Self::GroupStopped)
        )
    }

    /// Returns whether this phase contains a physical stop confirmation gate.
    pub const fn confirms_stop(self) -> bool {
        matches!(
            self,
            Self::StopConfirmed | Self::ContinuationPrepared | Self::GroupStopped
        )
    }
}

/// Exact logical and physical attempt identity carried by every safety-stop record.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct SafetyStopIdentity {
    /// Execution Group containing the affected attempt.
    pub group_id: ExecutionGroupId,
    /// Mission-scoped Task containing the affected attempt.
    pub task_ref: TaskRef,
    /// Logical Role slot whose current attempt is being stopped.
    pub role_id: RoleId,
    /// Stable Runtime physical-attempt identity.
    pub execution_id: String,
    /// Generation fence preventing a late stop from affecting a replacement attempt.
    pub execution_generation: u64,
    /// Controller epoch that issued the stop purpose.
    pub controller_epoch: String,
    /// Digest of the deployment safety policy used to admit the stop.
    pub safety_policy_digest: String,
}

/// A durable exact-attempt safety-stop state machine.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct SafetyStopRecord {
    /// Contract schema marker.
    pub schema: String,
    /// Exact attempt identity and controller fence.
    pub identity: SafetyStopIdentity,
    /// Controller-local request receive time.
    pub requested_at: TimestampMs,
    /// Exclusive deadline for a timely physical confirmation.
    pub deadline: TimestampMs,
    /// Current monotonic phase.
    pub phase: SafetyStopPhase,
    /// Source-provided receipt for the current phase.
    pub last_evidence_source: String,
    /// Digest of the immutable source evidence.
    pub last_evidence_digest: String,
    /// Receive time of the first timely `StopConfirmed` evidence.
    pub confirmed_at: Option<TimestampMs>,
}

/// Errors returned when a safety-stop evidence transition violates the contract.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SafetyStopError {
    /// A text identity or digest was blank.
    InvalidField(&'static str),
    /// The deadline was not after the request time.
    InvalidDeadline,
    /// A transition skipped or repeated a phase.
    InvalidTransition {
        /// Current phase before the attempted transition.
        current: SafetyStopPhase,
        /// Requested next phase.
        next: SafetyStopPhase,
    },
    /// The evidence belongs to another attempt or policy generation.
    IdentityConflict,
    /// The evidence arrived after the physical stop deadline.
    DeadlineExceeded,
    /// A phase was already confirmed and cannot be overwritten by a later receipt.
    EvidenceConflict,
}

impl Display for SafetyStopError {
    /// Formats a stable safety contract diagnostic.
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::InvalidField(field) => write!(formatter, "physical stop {field} is blank"),
            Self::InvalidDeadline => formatter.write_str("physical stop deadline is invalid"),
            Self::InvalidTransition { current, next } => {
                write!(
                    formatter,
                    "physical stop transition {current:?} -> {next:?} is invalid"
                )
            }
            Self::IdentityConflict => {
                formatter.write_str("physical stop evidence identity conflicts")
            }
            Self::DeadlineExceeded => {
                formatter.write_str("physical stop evidence missed its deadline")
            }
            Self::EvidenceConflict => {
                formatter.write_str("physical stop confirmation evidence conflicts")
            }
        }
    }
}

impl std::error::Error for SafetyStopError {}

impl SafetyStopRecord {
    /// Validates restored identity, phase and original confirmation time without refreshing evidence.
    pub fn validate(&self) -> Result<(), SafetyStopError> {
        validate_identity(&self.identity)?;
        validate_evidence(&self.last_evidence_source, &self.last_evidence_digest)?;
        if self.schema != PHYSICAL_STOP_CONTRACT_SCHEMA_V0_1 || self.requested_at >= self.deadline {
            return Err(SafetyStopError::InvalidDeadline);
        }
        if self.phase.confirms_stop() != self.confirmed_at.is_some()
            || self
                .confirmed_at
                .is_some_and(|time| time < self.requested_at || time >= self.deadline)
        {
            return Err(SafetyStopError::EvidenceConflict);
        }
        Ok(())
    }

    /// Expires only an unconfirmed stop; subsequent messages cannot erase a proven confirmation.
    pub fn expire(&mut self, now: TimestampMs) -> bool {
        if now < self.deadline
            || self.phase.confirms_stop()
            || self.phase == SafetyStopPhase::ManualEscalation
        {
            return false;
        }
        self.phase = SafetyStopPhase::ManualEscalation;
        true
    }
    /// Creates a new stop purpose; ownership remains held until confirmation.
    pub fn new(
        identity: SafetyStopIdentity,
        requested_at: TimestampMs,
        deadline: TimestampMs,
        source: impl Into<String>,
        evidence_digest: impl Into<String>,
    ) -> Result<Self, SafetyStopError> {
        validate_identity(&identity)?;
        if deadline <= requested_at {
            return Err(SafetyStopError::InvalidDeadline);
        }
        let source = source.into();
        let evidence_digest = evidence_digest.into();
        validate_evidence(&source, &evidence_digest)?;
        Ok(Self {
            schema: PHYSICAL_STOP_CONTRACT_SCHEMA_V0_1.to_string(),
            identity,
            requested_at,
            deadline,
            phase: SafetyStopPhase::StopRequested,
            last_evidence_source: source,
            last_evidence_digest: evidence_digest,
            confirmed_at: None,
        })
    }

    /// Advances one exact-attempt phase while preserving the first confirmation evidence.
    pub fn advance(
        &mut self,
        identity: &SafetyStopIdentity,
        next: SafetyStopPhase,
        received_at: TimestampMs,
        source: impl Into<String>,
        evidence_digest: impl Into<String>,
    ) -> Result<(), SafetyStopError> {
        if &self.identity != identity {
            return Err(SafetyStopError::IdentityConflict);
        }
        if !self.phase.can_follow(next) {
            return Err(SafetyStopError::InvalidTransition {
                current: self.phase,
                next,
            });
        }
        let source = source.into();
        let evidence_digest = evidence_digest.into();
        validate_evidence(&source, &evidence_digest)?;
        if received_at < self.requested_at {
            return Err(SafetyStopError::IdentityConflict);
        }
        if matches!(
            next,
            SafetyStopPhase::StopCommandAccepted
                | SafetyStopPhase::SafeStateObserved
                | SafetyStopPhase::TerminalProcessObserved
                | SafetyStopPhase::StopConfirmed
        ) && received_at >= self.deadline
        {
            return Err(SafetyStopError::DeadlineExceeded);
        }
        if next == SafetyStopPhase::StopConfirmed {
            if received_at >= self.deadline {
                return Err(SafetyStopError::DeadlineExceeded);
            }
            if self.confirmed_at.is_some() {
                return Err(SafetyStopError::EvidenceConflict);
            }
            self.confirmed_at = Some(received_at);
        }
        self.phase = next;
        self.last_evidence_source = source;
        self.last_evidence_digest = evidence_digest;
        Ok(())
    }

    /// Returns whether Control may release or continue the physical ownership.
    pub const fn may_release_ownership(&self) -> bool {
        self.phase.confirms_stop() && self.confirmed_at.is_some()
    }

    /// Returns whether confirmation arrived before the exclusive deadline.
    pub const fn confirmation_was_timely(&self) -> bool {
        self.confirmed_at.is_some()
    }
}

/// Validates all identity fields that must be bound into a stop command and evidence.
fn validate_identity(identity: &SafetyStopIdentity) -> Result<(), SafetyStopError> {
    if identity.execution_generation == 0 {
        return Err(SafetyStopError::InvalidField("execution generation"));
    }
    for (value, field) in [
        (identity.execution_id.as_str(), "execution_id"),
        (identity.controller_epoch.as_str(), "controller_epoch"),
        (
            identity.safety_policy_digest.as_str(),
            "safety_policy_digest",
        ),
    ] {
        if value.trim().is_empty() {
            return Err(SafetyStopError::InvalidField(field));
        }
    }
    Ok(())
}

/// Validates one source and immutable evidence digest.
fn validate_evidence(source: &str, digest: &str) -> Result<(), SafetyStopError> {
    if source.trim().is_empty() {
        return Err(SafetyStopError::InvalidField("evidence_source"));
    }
    if digest.trim().is_empty() {
        return Err(SafetyStopError::InvalidField("evidence_digest"));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{ExecutionGroupId, MissionId, TaskId};

    /// Builds one exact stop identity for state-machine tests.
    fn identity() -> SafetyStopIdentity {
        SafetyStopIdentity {
            group_id: ExecutionGroupId::new("group-a").expect("group is valid"),
            task_ref: TaskRef::new(
                MissionId::new("mission-a").expect("mission is valid"),
                TaskId::new("task-a").expect("task is valid"),
            ),
            role_id: RoleId::new("role-a").expect("role is valid"),
            execution_id: "attempt-a".to_string(),
            execution_generation: 1,
            controller_epoch: "epoch-a".to_string(),
            safety_policy_digest: "sha256:policy".to_string(),
        }
    }

    /// A stop request retains ownership until a complete timely confirmation sequence arrives.
    #[test]
    fn confirmation_gate_is_explicit() {
        let identity = identity();
        let mut record = SafetyStopRecord::new(
            identity.clone(),
            TimestampMs::new(10),
            TimestampMs::new(100),
            "controller",
            "sha256:request",
        )
        .expect("record is valid");
        assert!(!record.may_release_ownership());
        record
            .advance(
                &identity,
                SafetyStopPhase::StopCommandAccepted,
                TimestampMs::new(20),
                "node-journal",
                "sha256:accepted",
            )
            .expect("command acceptance is valid");
        record
            .advance(
                &identity,
                SafetyStopPhase::SafeStateObserved,
                TimestampMs::new(30),
                "local-eaios",
                "sha256:safe-state",
            )
            .expect("safe state is valid");
        record
            .advance(
                &identity,
                SafetyStopPhase::StopConfirmed,
                TimestampMs::new(40),
                "local-eaios",
                "sha256:confirmed",
            )
            .expect("confirmation is valid");
        assert!(record.may_release_ownership());
    }

    /// A late confirmation and an identity mismatch cannot authorize release.
    #[test]
    fn late_or_stale_confirmation_is_rejected() {
        let identity = identity();
        let mut record = SafetyStopRecord::new(
            identity.clone(),
            TimestampMs::new(10),
            TimestampMs::new(100),
            "controller",
            "sha256:request",
        )
        .expect("record is valid");
        record
            .advance(
                &identity,
                SafetyStopPhase::StopCommandAccepted,
                TimestampMs::new(20),
                "node-journal",
                "sha256:accepted",
            )
            .expect("command acceptance is valid");
        record
            .advance(
                &identity,
                SafetyStopPhase::SafeStateObserved,
                TimestampMs::new(30),
                "local-eaios",
                "sha256:safe-state",
            )
            .expect("safe state is valid");
        assert_eq!(
            record.advance(
                &identity,
                SafetyStopPhase::StopConfirmed,
                TimestampMs::new(100),
                "local-eaios",
                "sha256:late",
            ),
            Err(SafetyStopError::DeadlineExceeded)
        );
        let mut stale = identity.clone();
        stale.execution_id = "attempt-b".to_string();
        assert_eq!(
            record.advance(
                &stale,
                SafetyStopPhase::StopConfirmed,
                TimestampMs::new(40),
                "local-eaios",
                "sha256:wrong",
            ),
            Err(SafetyStopError::IdentityConflict)
        );
        assert!(!record.may_release_ownership());
    }
}
