//! Compatibility exports for pure Domain evidence-policy values; State owns records, not decisions.

pub use domain::{
    ConflictReducer, DECISION_EVIDENCE_POLICY_SCHEMA_V0_1, DecisionEvidencePolicy,
    DecisionEvidencePolicyError, DecisionKind, EvidenceDisposition, EvidenceEvaluation,
};
