//! Generic authenticated semantic verifier contracts, separate from local execution completion.

use crate::{CapabilityContractRef, RoleId, TaskRef};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};

/// Source-owned semantic verdict; unknown cannot become local or semantic success.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SemanticVerdict {
    /// Affirmative source observation under the declared verifier contract.
    Satisfied,
    /// Negative source observation, distinct from local execution failure.
    Unsatisfied,
    /// The source could not establish either result.
    Unknown,
}

/// Evidence lifecycle semantics, never inferred from observation names or local completion.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum VerifierFinality {
    /// Current observation which cannot terminate semantic satisfaction.
    Provisional,
    /// Final source decision for the exact current plan and attempt set.
    Final,
}

/// Trusted producer and immutable semantic measurement contract for one tenant.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SemanticVerifierSource {
    /// Source identity within this deployment registry.
    pub source_id: String,
    /// Monotonic independently versioned measurement contract.
    pub revision: u64,
    /// Authenticated consumer namespace.
    pub tenant_id: String,
    /// Stable certificate-derived principals permitted to publish this source.
    pub producer_principals: BTreeSet<String>,
    /// Exact canonical verifier capability.
    pub verifier: CapabilityContractRef,
    /// Exact predicates supported by this source.
    pub predicates: BTreeSet<String>,
    /// Maximum Controller receive-relative evidence lifetime.
    pub max_age_ms: u64,
    /// Irreversible current source disable fence until an explicitly newer source contract.
    pub enabled: bool,
    /// Running execution requires continuously fresh affirmative evidence when true.
    pub continuous: bool,
    /// Deployment-owned bounded timeout for the complete safety-stop barrier on verification loss.
    pub stop_timeout_ms: u64,
    /// Independent producer artifact required for affirmative final evidence when true.
    pub require_artifact: bool,
}

/// Versioned administrator-owned verifier source registry.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SemanticVerifierRegistry {
    /// Closed registry codec.
    pub schema_version: String,
    /// Immutable deployment authority identity.
    pub registry_id: String,
    /// Expected-revision update generation.
    pub revision: u64,
    /// Retained source contracts, including disabled sources.
    pub sources: BTreeMap<String, SemanticVerifierSource>,
}

/// Exact current logical Role and Runtime attempt covered by one source observation.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct VerifierAttemptRef {
    /// Logical Role, never a physical Node selector.
    pub role_id: RoleId,
    /// Exact current Runtime physical attempt identity.
    pub attempt_id: String,
}

/// Source-authored evidence input; receive time and trusted principal are added by the application.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SemanticVerifierEvidence {
    /// Immutable idempotent evidence identity.
    pub evidence_id: String,
    /// Exact registered producer contract.
    pub source_id: String,
    /// Frozen measurement interpretation generation.
    pub source_revision: u64,
    /// Explicit owning tenant.
    pub tenant_id: String,
    /// Exact accepted logical Task.
    pub task_ref: TaskRef,
    /// Exact accepted normalized plan content identity.
    pub plan_digest: String,
    /// Complete current Role/attempt set; no inferred or stale coverage.
    pub attempts: Vec<VerifierAttemptRef>,
    /// Exact Mission-declared predicate.
    pub predicate: String,
    /// Typed outcome of the producer's observation.
    pub verdict: SemanticVerdict,
    /// Whether the producer considers this observation final.
    pub finality: VerifierFinality,
    /// Original source-local time, retained without comparison to Controller clocks.
    pub source_observed_at_ms: u64,
    /// Nonrenewable Controller receive-relative validity, bounded by the source contract.
    pub valid_for_ms: u64,
    /// Monotonic source sequence within this exact Task and contract.
    pub sequence: u64,
    /// CAS-backed independent evidence artifact identity when supplied.
    pub artifact_digest: Option<String>,
    /// Exact independently produced CAS length; digest and length must be present together.
    pub artifact_size_bytes: Option<u64>,
}

/// Application-added immutable attribution and original admission time for one exact producer input.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SemanticVerifierReceipt {
    /// Original validated source input, never a synthetic execution outcome.
    pub evidence: SemanticVerifierEvidence,
    /// Original registry generation used for source/principal admission.
    pub registry_revision: u64,
    /// Digest of the complete immutable producer input.
    pub input_digest: String,
    /// Actual certificate-mapped stable producer principal.
    pub authority_subject: String,
    /// Original Controller receive time.
    pub received_at_ms: u64,
    /// Original exclusive validity fence, never renewed on replay/restore.
    pub valid_until_ms: u64,
    /// Whether owning Orchestration consumed this exact evidence.
    pub applied: bool,
    /// Retained dispute/late/stale evidence classification, independent of terminal Mission truth.
    pub incident: Option<String>,
}

/// Nonrenewable start fence for one current attempt set's continuous verification contract.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SemanticVerifierWatch {
    /// Exact accepted Task whose running execution requires this measurement.
    pub task_ref: TaskRef,
    /// Frozen source contract including original tenant, predicate and freshness requirements.
    pub source: SemanticVerifierSource,
    /// Exact accepted normalized plan identity.
    pub plan_digest: String,
    /// Complete current attempt set, never renewed by duplicate monitoring or restore.
    pub attempts: Vec<VerifierAttemptRef>,
    /// Original Controller time at monitor installation before Execute delivery.
    pub started_at_ms: u64,
    /// Initial exclusive deadline for a producer to supply fresh affirmative evidence.
    pub initial_deadline_ms: u64,
}

impl SemanticVerifierRegistry {
    /// Validates source attribution, measurement capability and nonempty explicit predicate contracts.
    pub fn validate(&self) -> Result<(), String> {
        if self.schema_version != "roboguide.semantic-verifier-registry/v0.1"
            || self.registry_id.trim().is_empty()
            || self.registry_id.len() > 128
            || self.revision == 0
            || self.sources.len() > 128
        {
            return Err("semantic verifier registry is invalid".into());
        }
        for (id, source) in &self.sources {
            if id != &source.source_id
                || !valid_text(id)
                || source.revision == 0
                || !valid_text(&source.tenant_id)
                || source.producer_principals.is_empty()
                || source.producer_principals.len() > 64
                || source
                    .producer_principals
                    .iter()
                    .any(|subject| !valid_text(subject))
                || source.predicates.is_empty()
                || source.predicates.len() > 64
                || source
                    .predicates
                    .iter()
                    .any(|predicate| !valid_text(predicate))
                || source.max_age_ms == 0
                || source.max_age_ms > 3_600_000
                || source.stop_timeout_ms == 0
                || source.stop_timeout_ms > 3_600_000
            {
                return Err("semantic verifier source contract is invalid".into());
            }
        }
        Ok(())
    }

    /// Preserves old source meaning, source ownership and rollback fences across updates.
    pub fn validates_successor(&self, next: &Self) -> Result<(), String> {
        next.validate()?;
        if self.registry_id != next.registry_id
            || next.revision < self.revision
            || next.revision == self.revision && next != self
            || next.revision > self.revision
                && next.revision
                    != self
                        .revision
                        .checked_add(1)
                        .ok_or("verifier registry revision exhausted")?
        {
            return Err("semantic verifier registry revision conflicts".into());
        }
        for (id, old) in &self.sources {
            let new = next
                .sources
                .get(id)
                .ok_or("verifier sources must be retained and disabled")?;
            if old.tenant_id != new.tenant_id
                || new.revision < old.revision
                || new != old && new.revision == old.revision
            {
                return Err("semantic verifier source revision conflicts".into());
            }
        }
        Ok(())
    }
}

impl SemanticVerifierEvidence {
    /// Rejects incomplete attempt sets, malformed digests and unbounded producer payloads.
    pub fn validate(&self) -> Result<(), String> {
        if !valid_text(&self.evidence_id)
            || !valid_text(&self.source_id)
            || !valid_text(&self.tenant_id)
            || self.source_revision == 0
            || !valid_digest(&self.plan_digest)
            || self.attempts.is_empty()
            || self.attempts.len() > 32
            || self
                .attempts
                .iter()
                .any(|attempt| !valid_text(&attempt.attempt_id))
            || self
                .attempts
                .iter()
                .map(|attempt| &attempt.role_id)
                .collect::<BTreeSet<_>>()
                .len()
                != self.attempts.len()
            || !valid_text(&self.predicate)
            || self.valid_for_ms == 0
            || self.valid_for_ms > 3_600_000
            || self.sequence == 0
            || self
                .artifact_digest
                .as_ref()
                .is_some_and(|digest| !valid_digest(digest))
            || self.artifact_digest.is_some() != self.artifact_size_bytes.is_some()
            || self
                .artifact_size_bytes
                .is_some_and(|size| size == 0 || size > 64 * 1024 * 1024)
        {
            return Err("semantic verifier evidence identity/bounds are invalid".into());
        }
        Ok(())
    }
}

/// Checks bounded opaque identities without interpreting workload or hardware names.
fn valid_text(text: &str) -> bool {
    !text.trim().is_empty() && text.len() <= 256
}

/// Validates a canonical content identity, never claiming the referenced bytes exist.
fn valid_digest(digest: &str) -> bool {
    digest.strip_prefix("sha256:").is_some_and(|hex| {
        hex.len() == 64
            && hex
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    })
}
