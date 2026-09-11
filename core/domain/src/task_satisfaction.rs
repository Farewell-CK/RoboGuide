//! Mission-declared semantic satisfaction policy and verifier evidence.

use crate::{CapabilityContractRef, DomainError, StateSource, TaskRef, TimestampMs};

/// External verifier contract required to establish one Task's semantic effect.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct VerifierSatisfactionSpec {
    /// Canonical evidence/verifier contract selected by Mission Intelligence.
    verifier: CapabilityContractRef,
    /// Human-readable, provider-independent predicate the evidence must establish.
    predicate: String,
    /// Maximum accepted RoboGuide receive age for verifier evidence.
    max_evidence_age_ms: u64,
}

impl VerifierSatisfactionSpec {
    /// Creates a verifier policy while rejecting blank predicates and zero freshness windows.
    pub fn new(
        verifier: CapabilityContractRef,
        predicate: impl Into<String>,
        max_evidence_age_ms: u64,
    ) -> Result<Self, DomainError> {
        let predicate = predicate.into();
        if predicate.trim().is_empty() {
            return Err(DomainError::EmptyValue {
                kind: "task satisfaction predicate",
            });
        }
        if max_evidence_age_ms == 0 {
            return Err(DomainError::InvalidDuration {
                kind: "task satisfaction evidence age",
            });
        }
        Ok(Self {
            verifier,
            predicate,
            max_evidence_age_ms,
        })
    }

    /// Returns the exact verifier/evidence contract.
    pub const fn verifier(&self) -> &CapabilityContractRef {
        &self.verifier
    }

    /// Returns the semantic predicate evidence must establish.
    pub fn predicate(&self) -> &str {
        &self.predicate
    }

    /// Returns the maximum accepted RoboGuide-local receive age.
    pub const fn max_evidence_age_ms(&self) -> u64 {
        self.max_evidence_age_ms
    }
}

/// Evidence basis that Orchestration may use to declare a Task satisfied.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "kebab-case", tag = "basis", content = "verifier")]
pub enum TaskSatisfactionBasis {
    /// Accept successful terminal reports from every current Role execution.
    ExecutionReport,
    /// Require a separate source-aware verifier verdict after local execution completes.
    VerifierEvidence(VerifierSatisfactionSpec),
}

impl Default for TaskSatisfactionBasis {
    /// Preserves the v0.6 compatibility policy for historical plans.
    fn default() -> Self {
        Self::ExecutionReport
    }
}

/// One immutable external verdict offered to Orchestration after execution completion.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TaskSatisfactionEvidence {
    /// Exact Task whose semantic effect was assessed.
    task_ref: TaskRef,
    /// Verifier contract that produced the verdict.
    verifier: CapabilityContractRef,
    /// Predicate assessed by the verifier.
    predicate: String,
    /// Node or RoboGuide verifier component that produced the evidence.
    source: StateSource,
    /// Source-local evidence time retained for provenance.
    source_observed_at: TimestampMs,
    /// RoboGuide-local receive time used for freshness.
    received_at: TimestampMs,
    /// Positive or negative semantic verdict.
    satisfied: bool,
}

impl TaskSatisfactionEvidence {
    /// Creates source-aware verifier evidence while rejecting a blank predicate.
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        task_ref: TaskRef,
        verifier: CapabilityContractRef,
        predicate: impl Into<String>,
        source: StateSource,
        source_observed_at: TimestampMs,
        received_at: TimestampMs,
        satisfied: bool,
    ) -> Result<Self, DomainError> {
        let predicate = predicate.into();
        if predicate.trim().is_empty() {
            return Err(DomainError::EmptyValue {
                kind: "task satisfaction evidence predicate",
            });
        }
        Ok(Self {
            task_ref,
            verifier,
            predicate,
            source,
            source_observed_at,
            received_at,
            satisfied,
        })
    }

    /// Returns the Task assessed by this evidence.
    pub const fn task_ref(&self) -> &TaskRef {
        &self.task_ref
    }

    /// Returns the verifier contract that produced this verdict.
    pub const fn verifier(&self) -> &CapabilityContractRef {
        &self.verifier
    }

    /// Returns the exact semantic predicate assessed by the verifier.
    pub fn predicate(&self) -> &str {
        &self.predicate
    }

    /// Returns the attributed verifier producer without treating it as global truth.
    pub const fn source(&self) -> &StateSource {
        &self.source
    }

    /// Returns source-local observation time retained only as provenance.
    pub const fn source_observed_at(&self) -> TimestampMs {
        self.source_observed_at
    }

    /// Returns RoboGuide-local receive time used for freshness policy.
    pub const fn received_at(&self) -> TimestampMs {
        self.received_at
    }

    /// Returns the verifier's semantic verdict.
    pub const fn is_satisfied(&self) -> bool {
        self.satisfied
    }
}
