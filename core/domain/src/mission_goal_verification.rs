//! Independent evidence for one frozen original Mission goal; it never proves device stopping.

use crate::*;

/// Original goal interpretation frozen by Control before any runtime replan permission is granted.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MissionGoalContract {
    /// Immutable original Mission identity.
    pub mission_id: MissionId,
    /// Immutable original owning tenant.
    pub tenant_id: String,
    /// Canonical original MissionGoal bytes, including objective and identity.
    pub original_goal_digest: String,
    /// Canonical original goal-grounding/measurement context, independent of the candidate plan.
    pub context_digest: String,
    /// Exact independently measured predicate and original receive-relative freshness bound.
    pub spec: VerifierSatisfactionSpec,
    /// Actual original Control-authorized source; its complete identity is retained.
    pub source: SemanticVerifierSource,
    /// Complete original contract/source digest.
    pub digest: String,
}

/// Exact current physical software/robot attempt covered by a goal observation, without a Node selector.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MissionGoalAttempt {
    /// Logical Task in the current accepted version.
    pub task_ref: TaskRef,
    /// Logical Role in that Task.
    pub role_id: RoleId,
    /// Exact Runtime physical attempt, never a fabricated completion flag.
    pub attempt_id: String,
}

/// Independent producer input bound to the original goal and one exact current plan/Group/attempt set.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MissionGoalEvidence {
    /// Closed evidence schema.
    pub schema_version: String,
    /// Immutable source evidence identity.
    pub evidence_id: String,
    /// Original Mission and tenant.
    pub mission_id: MissionId,
    /// Original owning tenant, never inferred from a new candidate.
    pub tenant_id: String,
    /// Frozen original goal interpretation.
    pub contract_digest: String,
    /// Actual independently authorized source.
    pub source_id: String,
    /// Exact frozen source interpretation revision.
    pub source_revision: u64,
    /// Current Orchestration plan revision.
    pub plan_revision: u64,
    /// Current Control configuration.
    pub group_id: ExecutionGroupId,
    /// Complete current accepted semantic plan identity.
    pub plan_digest: String,
    /// Complete actual current non-inherited attempt coverage.
    pub attempts: Vec<MissionGoalAttempt>,
    /// Exact original independent predicate.
    pub predicate: String,
    /// Actual source verdict; Unknown grants no completion authority.
    pub verdict: SemanticVerdict,
    /// Whether this original source considers the observation final.
    pub finality: VerifierFinality,
    /// Original source-local measurement time, never compared to Controller time.
    pub source_observed_at_ms: u64,
    /// Original monotonic source sequence for this exact version/Group.
    pub sequence: u64,
    /// Original receive-relative validity, bounded by the frozen source and goal contract.
    pub valid_for_ms: u64,
    /// Actual independent CAS proof, required when the frozen source demands it.
    pub artifact_digest: Option<String>,
    /// Actual complete independent proof length, present exactly with its digest.
    pub artifact_size_bytes: Option<u64>,
}

/// Immutable Control source admission; application adds its independent nonrenewable local budget.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MissionGoalReceipt {
    /// Complete original measurement; accepting it never invents execution or stopping facts.
    pub evidence: MissionGoalEvidence,
    /// Actual authenticated original publishing principal.
    pub authority_subject: String,
    /// Exact original verified publisher leaf; rotation replays retain this original transport evidence.
    pub authority_leaf_digest: String,
    /// Exact original normalized input digest.
    pub input_digest: String,
    /// Original Controller receive time, never refreshed by replay or restore.
    pub received_at: TimestampMs,
}
