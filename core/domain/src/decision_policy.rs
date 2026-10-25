//! Explicit admission policy for State evidence that may influence a decision.
//!
//! The source-aware State projection deliberately retains independent records. This module adds
//! the missing, transport-neutral policy boundary: a caller must name the decision, allowed
//! sources, freshness, confidence, and deterministic conflict reducer before one record can be
//! consumed by Control or satisfaction logic. It never fuses records into a new Belief value.

use crate::{StateObjectRef, StateRecord, StateSemantic, StateSource, TimestampMs};
use std::collections::BTreeSet;
use std::fmt::{Display, Formatter};

/// Version identifier for the first decision-evidence policy contract.
pub const DECISION_EVIDENCE_POLICY_SCHEMA_V0_1: &str = "roboguide.decision-evidence-policy/v0.1";

/// Decision classes that may explicitly consume State evidence.
#[derive(
    Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, serde::Serialize, serde::Deserialize,
)]
#[serde(rename_all = "snake_case")]
pub enum DecisionKind {
    /// Explicit raw evidence consumption for a separately attributed conservative Belief derivation.
    BeliefFusion,
    /// Non-authoritative deterministic scheduling cost ordering.
    Schedule,
    /// Capability and eligibility matching.
    Match,
    /// Resource and ownership commitment.
    Commit,
    /// Initial logical binding after resource commitment.
    Bind,
    /// Binding a logical role to a current physical attempt.
    Rebind,
    /// Node or peer readiness admission.
    Readiness,
    /// Mission-declared semantic satisfaction.
    Satisfaction,
}

/// Deterministic policy for resolving multiple admissible source records.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ConflictReducer {
    /// Accept multiple sources only when their payloads are byte-equivalent.
    RequireEquivalent,
    /// Select one highest-confidence source; equal-confidence disagreement remains a conflict.
    HighestConfidence,
}

/// Why evidence was not admitted to a decision.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EvidenceDisposition {
    /// One deterministic source record passed every policy requirement.
    Accepted,
    /// No record matched the object, channel, semantic, or source constraints.
    Unknown,
    /// Matching records existed, but every matching record was outside its receive-time TTL.
    Stale,
    /// Matching fresh records existed, but none met the confidence floor.
    LowConfidence,
    /// The requested decision is not enabled by this policy.
    Unauthorized,
    /// Admissible records disagree under the configured reducer.
    Conflict,
}

/// Result of evaluating one policy against a bounded current State snapshot.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct EvidenceEvaluation {
    /// Final policy disposition.
    pub disposition: EvidenceDisposition,
    /// Selected source record when the disposition is [`EvidenceDisposition::Accepted`].
    pub record: Option<StateRecord>,
}

impl EvidenceEvaluation {
    /// Creates an evaluation with no admitted record.
    const fn rejected(disposition: EvidenceDisposition) -> Self {
        Self {
            disposition,
            record: None,
        }
    }
}

/// Validation failures for a deployment-owned evidence policy.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DecisionEvidencePolicyError {
    /// A required policy identity or field was blank.
    InvalidField(&'static str),
    /// A policy bound an invalid confidence or freshness value.
    InvalidBound(&'static str),
    /// No source or semantic was admitted, which would make every evaluation unknown.
    EmptyAdmissionSet(&'static str),
}

impl Display for DecisionEvidencePolicyError {
    /// Formats a stable policy validation diagnostic.
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::InvalidField(field) => write!(formatter, "decision policy {field} is blank"),
            Self::InvalidBound(field) => write!(formatter, "decision policy {field} is invalid"),
            Self::EmptyAdmissionSet(field) => {
                write!(formatter, "decision policy {field} must not be empty")
            }
        }
    }
}

impl std::error::Error for DecisionEvidencePolicyError {}

/// Immutable policy describing one State key and the decisions allowed to consume it.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionEvidencePolicy {
    /// Policy schema marker.
    pub schema: String,
    /// Stable deployment policy identity.
    pub policy_id: String,
    /// Monotonic policy revision bound into acceptance evidence.
    pub revision: u64,
    /// Exact semantic object addressed by the policy.
    pub object: StateObjectRef,
    /// Exact State channel addressed by the policy.
    pub channel_id: String,
    /// Allowed epistemic meanings for this decision key.
    pub allowed_semantics: BTreeSet<StateSemantic>,
    /// Allowed source identities for this decision key.
    pub allowed_sources: BTreeSet<StateSource>,
    /// Maximum receive-relative age accepted by the policy.
    pub max_age_ms: u64,
    /// Optional minimum fixed-point confidence.
    pub min_confidence_millionths: Option<u32>,
    /// Decisions that may consume this key.
    pub allowed_decisions: BTreeSet<DecisionKind>,
    /// Explicit multi-source conflict behavior.
    pub reducer: ConflictReducer,
    /// Optional exact payload codec; a matching channel alone cannot establish its interpretation.
    #[serde(default)]
    pub payload_schema: Option<String>,
}

impl DecisionEvidencePolicy {
    /// Creates a validated policy with no implicit source or decision authority.
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        policy_id: impl Into<String>,
        revision: u64,
        object: StateObjectRef,
        channel_id: impl Into<String>,
        allowed_semantics: BTreeSet<StateSemantic>,
        allowed_sources: BTreeSet<StateSource>,
        max_age_ms: u64,
        min_confidence_millionths: Option<u32>,
        allowed_decisions: BTreeSet<DecisionKind>,
        reducer: ConflictReducer,
    ) -> Result<Self, DecisionEvidencePolicyError> {
        let policy_id = policy_id.into();
        let channel_id = channel_id.into();
        if revision == 0 {
            return Err(DecisionEvidencePolicyError::InvalidBound("revision"));
        }
        if policy_id.trim().is_empty() {
            return Err(DecisionEvidencePolicyError::InvalidField("policy_id"));
        }
        if channel_id.trim().is_empty() {
            return Err(DecisionEvidencePolicyError::InvalidField("channel_id"));
        }
        if allowed_semantics.is_empty() {
            return Err(DecisionEvidencePolicyError::EmptyAdmissionSet(
                "allowed_semantics",
            ));
        }
        if allowed_sources.is_empty() {
            return Err(DecisionEvidencePolicyError::EmptyAdmissionSet(
                "allowed_sources",
            ));
        }
        if allowed_decisions.is_empty() {
            return Err(DecisionEvidencePolicyError::EmptyAdmissionSet(
                "allowed_decisions",
            ));
        }
        if max_age_ms == 0 {
            return Err(DecisionEvidencePolicyError::InvalidBound("max_age_ms"));
        }
        if min_confidence_millionths.is_some_and(|value| value > 1_000_000) {
            return Err(DecisionEvidencePolicyError::InvalidBound(
                "min_confidence_millionths",
            ));
        }
        Ok(Self {
            schema: DECISION_EVIDENCE_POLICY_SCHEMA_V0_1.to_string(),
            policy_id,
            revision,
            object,
            channel_id,
            allowed_semantics,
            allowed_sources,
            max_age_ms,
            min_confidence_millionths,
            allowed_decisions,
            reducer,
            payload_schema: None,
        })
    }

    /// Validates deserialized policies as strictly as programmatically created ones.
    pub fn validate(&self) -> Result<(), DecisionEvidencePolicyError> {
        if self.schema != DECISION_EVIDENCE_POLICY_SCHEMA_V0_1 {
            return Err(DecisionEvidencePolicyError::InvalidField("schema"));
        }
        if self
            .payload_schema
            .as_ref()
            .is_some_and(|schema| schema.trim().is_empty())
        {
            return Err(DecisionEvidencePolicyError::InvalidField("payload_schema"));
        }
        if self.allowed_sources.len() > 64 {
            return Err(DecisionEvidencePolicyError::InvalidBound("allowed_sources"));
        }
        Self::new(
            &self.policy_id,
            self.revision,
            self.object.clone(),
            &self.channel_id,
            self.allowed_semantics.clone(),
            self.allowed_sources.clone(),
            self.max_age_ms,
            self.min_confidence_millionths,
            self.allowed_decisions.clone(),
            self.reducer,
        )
        .map(|_| ())
    }

    /// Evaluates one immutable current State view without creating a fused record.
    pub fn evaluate(
        &self,
        records: &[StateRecord],
        decision: DecisionKind,
        now: TimestampMs,
    ) -> EvidenceEvaluation {
        if self.validate().is_err() || !self.allowed_decisions.contains(&decision) {
            return EvidenceEvaluation::rejected(EvidenceDisposition::Unauthorized);
        }
        let matching = records
            .iter()
            .filter(|record| {
                record.key().object() == &self.object
                    && record.key().channel_id() == self.channel_id
                    && self.allowed_semantics.contains(&record.key().semantic())
                    && self.allowed_sources.contains(record.key().source())
                    && self
                        .payload_schema
                        .as_ref()
                        .is_none_or(|schema| schema == record.payload_schema())
            })
            .collect::<Vec<_>>();
        if matching.is_empty() {
            return EvidenceEvaluation::rejected(EvidenceDisposition::Unknown);
        }
        let fresh = matching
            .iter()
            .copied()
            .filter(|record| {
                record.received_at() <= now
                    && !record.is_stale_at(now)
                    && now
                        .as_millis()
                        .saturating_sub(record.received_at().as_millis())
                        <= self.max_age_ms
            })
            .collect::<Vec<_>>();
        if fresh.is_empty() {
            return EvidenceEvaluation::rejected(EvidenceDisposition::Stale);
        }
        let confident = fresh
            .into_iter()
            .filter(|record| {
                self.min_confidence_millionths.is_none_or(|minimum| {
                    record
                        .confidence_millionths()
                        .is_some_and(|value| value >= minimum)
                })
            })
            .collect::<Vec<_>>();
        if confident.is_empty() {
            return EvidenceEvaluation::rejected(EvidenceDisposition::LowConfidence);
        }
        let selected = match self.reducer {
            ConflictReducer::RequireEquivalent => {
                let first = confident[0];
                if confident
                    .iter()
                    .any(|record| record.value() != first.value())
                {
                    return EvidenceEvaluation::rejected(EvidenceDisposition::Conflict);
                }
                select_latest(confident)
            }
            ConflictReducer::HighestConfidence => {
                let highest = confident
                    .iter()
                    .map(|record| record.confidence_millionths().unwrap_or_default())
                    .max()
                    .unwrap_or_default();
                let highest_records = confident
                    .into_iter()
                    .filter(|record| record.confidence_millionths().unwrap_or_default() == highest)
                    .collect::<Vec<_>>();
                let first = highest_records[0];
                if highest_records
                    .iter()
                    .any(|record| record.value() != first.value())
                {
                    return EvidenceEvaluation::rejected(EvidenceDisposition::Conflict);
                }
                select_latest(highest_records)
            }
        };
        EvidenceEvaluation {
            disposition: EvidenceDisposition::Accepted,
            record: Some(selected.clone()),
        }
    }
}

/// Selects the newest evidence deterministically without comparing source-local clocks.
fn select_latest(records: Vec<&StateRecord>) -> StateRecord {
    records
        .into_iter()
        .max_by(|left, right| {
            left.received_at()
                .cmp(&right.received_at())
                .then_with(|| left.sequence().cmp(&right.sequence()))
                .then_with(|| left.key().source().cmp(right.key().source()))
        })
        .expect("policy selection always receives at least one record")
        .clone()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{LocalSystemId, NodeId, StateObjectClass};

    /// Builds a bounded source-aware record for policy tests.
    fn record(node: &str, received_at: u64, confidence: Option<u32>, value: bool) -> StateRecord {
        StateRecord::new(
            StateObjectRef::new(StateObjectClass::World, "hazard", "crossing-a")
                .expect("object is valid"),
            StateSemantic::Observed,
            StateSource::Node {
                node_id: NodeId::new(node).expect("node is valid"),
                local_system_id: LocalSystemId::new("safety").expect("system is valid"),
            },
            "hazard",
            "example.hazard/v1",
            serde_json::json!({"present": value}),
            None,
            TimestampMs::new(received_at),
            100,
            confidence,
            received_at,
        )
        .expect("record is valid")
    }

    /// Builds a policy that accepts two named sources for one readiness decision.
    fn policy(reducer: ConflictReducer) -> DecisionEvidencePolicy {
        DecisionEvidencePolicy::new(
            "hazard-policy",
            1,
            StateObjectRef::new(StateObjectClass::World, "hazard", "crossing-a")
                .expect("object is valid"),
            "hazard",
            BTreeSet::from([StateSemantic::Observed]),
            BTreeSet::from([
                StateSource::Node {
                    node_id: NodeId::new("cane-a").expect("node is valid"),
                    local_system_id: LocalSystemId::new("safety").expect("system is valid"),
                },
                StateSource::Node {
                    node_id: NodeId::new("dog-a").expect("node is valid"),
                    local_system_id: LocalSystemId::new("safety").expect("system is valid"),
                },
            ]),
            100,
            Some(500_000),
            BTreeSet::from([DecisionKind::Readiness]),
            reducer,
        )
        .expect("policy is valid")
    }

    /// An unlisted decision cannot consume otherwise valid State.
    #[test]
    fn decision_scope_is_explicit() {
        let result = policy(ConflictReducer::RequireEquivalent).evaluate(
            &[record("cane-a", 10, Some(900_000), true)],
            DecisionKind::Commit,
            TimestampMs::new(10),
        );
        assert_eq!(result.disposition, EvidenceDisposition::Unauthorized);
    }

    /// A stale source remains evidence but cannot drive a decision.
    #[test]
    fn stale_evidence_is_not_refreshed() {
        let result = policy(ConflictReducer::RequireEquivalent).evaluate(
            &[record("cane-a", 10, Some(900_000), true)],
            DecisionKind::Readiness,
            TimestampMs::new(110),
        );
        assert_eq!(result.disposition, EvidenceDisposition::Stale);
    }

    /// Equal-valued independent sources are safe under the explicit equivalent reducer.
    #[test]
    fn equivalent_sources_are_admitted_without_fusion() {
        let result = policy(ConflictReducer::RequireEquivalent).evaluate(
            &[
                record("cane-a", 10, Some(900_000), true),
                record("dog-a", 11, Some(800_000), true),
            ],
            DecisionKind::Readiness,
            TimestampMs::new(20),
        );
        assert_eq!(result.disposition, EvidenceDisposition::Accepted);
        assert_eq!(
            result
                .record
                .expect("record is selected")
                .key()
                .source()
                .node_id(),
            Some(&NodeId::new("dog-a").unwrap())
        );
    }

    /// Conflicting sources remain explicitly unknown to the consumer.
    #[test]
    fn conflicting_sources_are_rejected() {
        let result = policy(ConflictReducer::RequireEquivalent).evaluate(
            &[
                record("cane-a", 10, Some(900_000), true),
                record("dog-a", 11, Some(800_000), false),
            ],
            DecisionKind::Readiness,
            TimestampMs::new(20),
        );
        assert_eq!(result.disposition, EvidenceDisposition::Conflict);
    }

    /// Highest confidence is deterministic but equal-confidence disagreement remains fenced.
    #[test]
    fn highest_confidence_reducer_is_bounded() {
        let result = policy(ConflictReducer::HighestConfidence).evaluate(
            &[
                record("cane-a", 10, Some(900_000), true),
                record("dog-a", 11, Some(700_000), false),
            ],
            DecisionKind::Readiness,
            TimestampMs::new(20),
        );
        assert_eq!(result.disposition, EvidenceDisposition::Accepted);
        assert_eq!(
            result
                .record
                .expect("record is selected")
                .key()
                .source()
                .node_id(),
            Some(&NodeId::new("cane-a").unwrap())
        );
    }
}
