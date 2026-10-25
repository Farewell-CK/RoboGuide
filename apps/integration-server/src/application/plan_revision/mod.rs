//! Durable candidate/Review budgets and original stopped-configuration switching.

use crate::*;
use serde::{Deserialize, Serialize};
use sha2::{Digest as _, Sha256};

mod admission;
mod checkpoint;
mod coordination;
mod discovery;
mod generation;
mod goal;
mod memory;
pub(crate) use memory::typed_publication_edges;
mod switching;
#[cfg(test)]
mod tests;
pub(crate) mod time;
pub(crate) use discovery::{work_page, work_query};
use time::{KernelTime, OriginalBudget};

/// Explicit current application protocol; old wrappers cannot acquire its permissions.
const SCHEMA: &str = "roboguide.runtime-plan-revision/v0.1";
/// Explicit generation delegation; v0.1 never acquires this additional permission.
const GENERATION_SCHEMA: &str = "roboguide.runtime-plan-revision/v0.2";
/// Explicit scoped Memory permission, never inferred from an older candidate or manifest alias.
const MEMORY_SCHEMA: &str = "roboguide.runtime-plan-revision/v0.3";
/// Complete typed-map source permission; generic v0.3 never acquires it through a projection.
const TYPED_MEMORY_SCHEMA: &str = "roboguide.runtime-plan-revision/v0.4";
/// Preserves a published scoped parent's original typed root under an independent new Group.
const SCOPED_MEMORY_SCHEMA: &str = "roboguide.runtime-plan-revision/v0.5";
/// Explicit reviewed complete-cohort coordination; ordinary switches acquire no such permission.
const COORDINATION_SCHEMA: &str = "roboguide.runtime-plan-revision/v0.6";
/// Candidate and command storage bounds are independent of prospective Mission admission.
const MAX_CANDIDATES: usize = 16;
/// Immutable command receipts cannot grow without a deployment bound.
const MAX_COMMANDS: usize = 256;
/// Complete retained proposal bytes across all Missions; exhaustion never changes running work.
const MAX_RETAINED_PLAN_BYTES: usize = 16 * 1024 * 1024;
/// Complete additional original cohort metadata is bounded independently of candidate plan bytes.
const MAX_RETAINED_COORDINATION_BYTES: usize = 512 * 1024;

/// Exact application principal supplied only after the actual transport/writer authorization check.
pub(crate) struct Caller<'a> {
    /// Actual trusted role-profile path captured from the TLS listener, never supplied in JSON.
    pub(crate) authorization_path: Option<&'a Path>,
    /// Stable current authenticated principal, never a request field or role header.
    pub(crate) principal: &'a str,
    /// Current certificate-mapped role, rechecked by the original writer gate.
    pub(crate) role: &'a str,
    /// Actual TLS leaf expiry; unverified loopback cannot install this profile.
    pub(crate) certificate_expires_at_ms: u64,
    /// Exact actual authenticated TLS leaf, independent of caller JSON or another rotation leaf.
    pub(crate) certificate_digest: &'a str,
    /// Explicit selected Mission tenant, or the actual immutable Mission tenant for a global caller.
    pub(crate) tenant: &'a str,
}

/// Frozen bounded policy installed by an actually authorized Mission administrator.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Policy {
    /// Frozen independent verifier of the original Mission goal, independent of a new DAG.
    goal_verifier: domain::VerifierSatisfactionSpec,
    /// Immutable original goal-grounding/measurement context; this declares constraints, not facts.
    goal_context: serde_json::Value,
    /// Operator-owned immutable policy identity.
    policy_id: String,
    /// Stable principal permitted to consume candidate/model-work tickets.
    proposer: String,
    /// Original total policy duration, anchored once at actual acceptance.
    lifetime_ms: u64,
    /// Maximum original candidate tickets, including interrupted or rejected generation.
    candidate_limit: usize,
    /// Per-ticket generation/Review budget, bounded by the original total policy.
    candidate_timeout_ms: u64,
    /// Maximum complete semantic candidate bytes per ticket.
    candidate_bytes: usize,
    /// Maximum accepted switch requests under this original policy.
    switch_limit: usize,
    /// Original bounded stop/revoke/switch budget per accepted request.
    switch_timeout_ms: u64,
    /// Explicit automatic fault sources; absence authorizes no automatic transition.
    automatic_triggers: BTreeSet<Trigger>,
}

/// Typed original fault triggers; observations never grant permission without the frozen policy.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub(crate) enum Trigger {
    /// Current original local execution conclusively failed.
    LocalExecutionFailed,
    /// Current final verifier conclusively rejected semantic satisfaction.
    SatisfactionRejected,
    /// Current physical attempt is recovery-pending Unknown.
    RuntimeUnknown,
}

/// One original ticket is consumed before a model call or manual proposal can be attempted.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Candidate {
    /// Original complete-cohort permission, frozen before output or independent Review.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    coordination: Option<coordination::Authorization>,
    /// Immutable original administrative child declarations, frozen before output and provider claims.
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    memory: BTreeMap<String, memory::Authorization>,
    /// Immutable original MI worker/store claim; replacing a database never acquires the ticket again.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    claim: Option<GenerationClaim>,
    /// At most one original generation and one semantic-review provider permission.
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    provider_calls: BTreeMap<String, ProviderCallReceipt>,
    /// Stable ticket identity, separate from the transport command.
    ticket_id: String,
    /// Frozen original proposing principal and Mission version.
    principal: String,
    /// Current source revision at original ticket admission.
    base_revision: u64,
    /// Exact original accepted plan digest.
    base_digest: String,
    /// Original actual receive budget, never refreshed by duplicate commands.
    budget: OriginalBudget,
    /// Actual original output receive clock; retained separately from its original work deadline.
    completed_time: Option<KernelTime>,
    /// Complete original output, including a rejected semantic draft when retained.
    plan: Option<serde_json::Value>,
    /// Digest of exact normalized published candidate bytes, when decoding succeeded.
    plan_digest: Option<String>,
    /// Immutable original provenance; absence never becomes a fabricated model call.
    provenance: Option<serde_json::Value>,
    /// Bounded original validation rejection; presence prevents switching.
    rejected: Option<String>,
    /// Actual reviewer decision or explicit pre-review of a complete alternative.
    review: Option<Review>,
}

/// Exact SQL worker identity declared before a provider call; the authenticated proposer owns this claim.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct GenerationClaimInput {
    /// Actual persistent SQL instance UUID, independent of filename or host path.
    store_instance_id: String,
    /// Complete original service/tenant/Mission/ticket identity digest.
    work_id: String,
    /// Original code/configuration/Catalog/Prompt/dependency and peer identity digest.
    identity_digest: String,
    /// Original actual MI Request identity, subsequently retained in candidate provenance.
    request_id: String,
}

/// Original authenticated claim receipt, without mutable call progress or renewed deadlines.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct GenerationClaim {
    /// Exact immutable original worker input.
    input: GenerationClaimInput,
    /// Actual original authenticated proposer.
    principal: String,
    /// Actual original peer leaf.
    certificate_digest: String,
    /// Actual original leaf expiry.
    certificate_expires_at_ms: u64,
    /// First actual Controller admission time.
    admitted_at: domain::TimestampMs,
    /// Original nonrenewable candidate clock at claim admission.
    admitted_kernel: KernelTime,
    /// Immutable original receipt digest.
    digest: String,
}

/// One actual provider-call permission; an older SQL copy cannot obtain a different nonce for this phase.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct ProviderCallReceipt {
    /// Actual original proposer leaf at this separate provider permission.
    certificate_digest: String,
    /// Exact original SQL claim.
    claim_digest: String,
    /// Fresh original SQL effect nonce, committed before requesting this permission.
    nonce: String,
    /// Complete original provider request/endpoint digest, excluding credentials.
    request_digest: String,
    /// Actual current proposer receiving this original permission.
    principal: String,
    /// Actual original peer leaf expiry.
    certificate_expires_at_ms: u64,
    /// First actual Controller admission time.
    admitted_at: domain::TimestampMs,
    /// Actual original clock inside the already admitted candidate budget.
    admitted_kernel: KernelTime,
    /// Immutable receipt identity.
    digest: String,
}

/// Original exact-candidate approval, tied to a current stable administrative principal.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Review {
    /// Exact original coordination input approved alongside this candidate and its Memory context.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    coordination_digest: Option<String>,
    /// Exact pre-output child permissions reviewed with this plan; historical absence grants none.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    memory_digest: Option<String>,
    /// Explicit administrative decision; this is permission, never a physical success fact.
    decision: ReviewDecision,
    /// Original bounded administrative explanation.
    reason: String,
    /// Actual authenticated reviewer, independent of the candidate/model's opinion.
    principal: String,
    /// Complete candidate identity reviewed.
    plan_digest: String,
    /// Exact Control completion precondition, never a claim that those Tasks have completed.
    completion_precondition: BTreeSet<domain::TaskId>,
    /// Original actual TLS expiry constraining this approval.
    certificate_expires_at_ms: u64,
    /// Actual original decision time.
    reviewed_at: domain::TimestampMs,
    /// Actual original decision clock inside the admitted candidate work budget.
    reviewed_kernel: KernelTime,
    /// Immutable permission/evidence digest.
    digest: String,
}

/// Explicit current administrative Review disposition.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
enum ReviewDecision {
    /// Authorizes this exact complete semantic candidate under its original policy.
    Approve,
    /// Retains the candidate and decision without permitting a switch.
    Reject,
}

/// Original physical-attempt identity frozen before any cancellation or content revocation.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct FrozenAttempt {
    /// Exact Runtime attempt, never a logical Task alias.
    execution_id: String,
    /// Exact original attempt generation.
    generation: u64,
    /// Source Task and Role in the original Group.
    task_ref: domain::TaskRef,
    /// Original logical Role.
    role_id: domain::RoleId,
    /// Current source Node owner.
    node_id: domain::NodeId,
    /// Actual original source session, not physical identity evidence.
    session_id: Option<String>,
}

/// Switch progress is separate from Mission satisfaction and physical stop evidence.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
enum Phase {
    /// Durable hold/stop/revoke requested; ownership remains original.
    Stopping,
    /// All original barriers are confirmed; explicit or authorized automatic Commit remains.
    Ready,
    /// Original stop barriers are confirmed while a bounded new candidate or Review remains absent.
    Generating,
    /// Complete original transaction accepted the new Group/plan version.
    Applied,
    /// Original Mission explicitly terminated through its existing complete release gates.
    Cancelled,
    /// Original evidence, current authority or budget is unavailable; ownership is retained.
    Manual,
}

/// One immutable source/target decision plus separately monotonic progress.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Switch {
    /// Actual normal beneficiary Commit/Bind admitted atomically with a running-preemption switch.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    beneficiary_admission: Option<coordination::BeneficiaryAdmission>,
    /// Explicit v0.2 permission admitted generation before an exact reviewed candidate existed.
    #[serde(default, skip_serializing_if = "std::ops::Not::not")]
    generation: bool,
    /// Actual immutable application switch command.
    command_id: String,
    /// Original candidate ticket and exact reviewed candidate identity.
    ticket_id: String,
    /// Source accepted revision.
    source_revision: u64,
    /// Original source and target Group identities.
    source_group: domain::ExecutionGroupId,
    /// New configuration identity, containing no assignment or commitment.
    target_group: domain::ExecutionGroupId,
    /// Exact source Control membership generation.
    membership: domain::GroupMembershipVersion,
    /// Complete original Runtime attempt set.
    attempts: Vec<FrozenAttempt>,
    /// Complete previously superseded attempts in this original Group; each already requires stop/revoke proof.
    prior_attempts: BTreeSet<String>,
    /// Original stop/revoke/switch budget.
    budget: OriginalBudget,
    /// Original Controller decision time.
    requested_at: domain::TimestampMs,
    /// Actual triggering policy source; None is an explicit operator request.
    trigger: Option<Trigger>,
    /// Whether the original policy permitted automatic Commit of this exact reviewed alternative.
    automatic: bool,
    /// Current progress, independent of immutable original evidence.
    phase: Phase,
    /// Bounded explanation for current manual hold.
    reason: Option<String>,
}

/// A command replay returns its original result without changing budgets, principals or effects.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct CommandReceipt {
    /// Actual original authenticated peer; stable-principal rotation never rewrites this evidence.
    leaf_digest: String,
    /// Original role admitted by the actual transport/writer authority, retained after later downgrades.
    role: String,
    /// Exact normalized command identity.
    digest: String,
    /// Original authenticated stable caller.
    principal: String,
    /// Original acceptance time, distinct from later observations.
    received_at: domain::TimestampMs,
    /// Original immutable response, containing no duplicate candidate bytes.
    result: serde_json::Value,
}

/// Application authority retains original policy, tickets, Review, switches and immutable receipts.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct RevisionState {
    /// Explicit additive v0.2 permission; historical absence grants no provider work.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    generation: Option<GenerationGrant>,
    /// Original actual issuer leaf, retained across stable-principal certificate rotation.
    authority_certificate_digest: String,
    /// Complete actual original grant at acceptance, never reconstructed from today's role.
    authority_grant: domain::OperatorCertificateGrant,
    /// Actual Control-frozen original goal/source identity.
    goal_contract_digest: String,
    /// Original goal receive budgets and application progress; raw measurements remain Control-owned.
    goal_evidence: BTreeMap<String, GoalProgress>,
    /// Original trusted authorization input, re-read under the application writer for autonomous work.
    authorization_path: PathBuf,
    /// Original committed authorization registry identity; another registry cannot adopt the policy.
    authorization_registry_id: String,
    /// Exact original read-only deployment identity; startup cannot silently replace it.
    deployment_digest: Option<String>,
    /// Startup-reacquired read-only profile, never another Control authority.
    #[serde(skip)]
    deployment: Option<Arc<DeploymentFeasibility>>,
    /// Complete original policy input.
    policy: Policy,
    /// Actual original installing principal and tenant.
    authority: String,
    /// Immutable Control Mission tenant.
    tenant: String,
    /// Original TLS expiry prevents policy outliving its original certificate witness.
    certificate_expires_at_ms: u64,
    /// Original total budget; restart/rollback cannot refresh it.
    budget: OriginalBudget,
    /// Original accepted local time.
    accepted_at: domain::TimestampMs,
    /// Original complete policy/issuer/budget identity.
    policy_digest: String,
    /// Irreversible revocation reduces authority without discarding evidence or holds.
    revoked: bool,
    /// Monotonic observable command/progress revision.
    revision: u64,
    /// Original candidate/model-work tickets.
    candidates: BTreeMap<String, Candidate>,
    /// Original switch requests; only one may own the current source configuration.
    switches: Vec<Switch>,
    /// Stable caller-bound idempotent command results.
    commands: BTreeMap<String, CommandReceipt>,
}

/// Actual administrative delegation bound once to the original immutable policy and budgets.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct GenerationGrant {
    /// Original actual granting principal, never a model-supplied identity.
    principal: String,
    /// Exact authenticated original leaf; another rotation leaf cannot replace this evidence.
    certificate_digest: String,
    /// Complete original certificate-mapped grant at acceptance.
    grant: domain::OperatorCertificateGrant,
    /// Original TLS leaf expiry.
    certificate_expires_at_ms: u64,
    /// Immutable original policy identity; generation cannot replace or extend it.
    policy_digest: String,
    /// First actual delegation receive time.
    admitted_at: domain::TimestampMs,
    /// Actual original kernel clock inside the policy budget.
    admitted_kernel: KernelTime,
    /// Complete original permission digest.
    digest: String,
}

impl RevisionState {
    /// Fences additional coordination permission, including immutable zero-event command receipts.
    pub(crate) fn uses_coordination_authority(&self) -> bool {
        if self
            .switches
            .iter()
            .any(|switch| switch.beneficiary_admission.is_some())
        {
            return true;
        }
        self.commands
            .values()
            .any(|receipt| receipt.result["schema_version"].as_str() == Some(COORDINATION_SCHEMA))
            || self.candidates.values().any(|candidate| {
                candidate.coordination.is_some()
                    || candidate
                        .review
                        .as_ref()
                        .is_some_and(|review| review.coordination_digest.is_some())
            })
    }

    /// Lets migration reject new Memory permissions hidden in an older wrapper.
    pub(crate) fn uses_revision_memory_authority(&self) -> bool {
        self.commands.values().any(|receipt| {
            matches!(
                receipt.result["schema_version"].as_str(),
                Some(MEMORY_SCHEMA | TYPED_MEMORY_SCHEMA | SCOPED_MEMORY_SCHEMA)
            )
        }) || self.candidates.values().any(|candidate| {
            !candidate.memory.is_empty()
                || candidate
                    .review
                    .as_ref()
                    .is_some_and(|review| review.memory_digest.is_some())
        })
    }

    /// Fences complete typed source declarations, including zero-event administrative receipts.
    pub(crate) fn uses_typed_revision_memory_authority(&self) -> bool {
        self.commands.values().any(|receipt| {
            matches!(
                receipt.result["schema_version"].as_str(),
                Some(TYPED_MEMORY_SCHEMA | SCOPED_MEMORY_SCHEMA)
            )
        }) || self.candidates.values().any(|candidate| {
            candidate
                .memory
                .values()
                .any(|record| record.uses_typed_map())
        })
    }

    /// Refuses scoped typed declarations hidden inside an older Controller writer generation.
    pub(crate) fn uses_scoped_revision_memory_authority(&self) -> bool {
        self.commands
            .values()
            .any(|receipt| receipt.result["schema_version"].as_str() == Some(SCOPED_MEMORY_SCHEMA))
            || self.candidates.values().any(|candidate| {
                candidate
                    .memory
                    .values()
                    .any(|record| record.schema() == SCOPED_MEMORY_SCHEMA)
            })
    }

    /// Lets outer checkpoint migration reject hidden v0.2 permissions in any older wrapper.
    pub(crate) fn uses_generation_authority(&self) -> bool {
        self.generation.is_some()
            || self.switches.iter().any(|switch| switch.generation)
            || self
                .candidates
                .values()
                .any(|candidate| candidate.claim.is_some() || !candidate.provider_calls.is_empty())
    }
}

/// Application proof of original receive-relative timing, separate from Control source admission.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct GoalProgress {
    /// Nonrenewable same-kernel receive budget.
    budget: OriginalBudget,
    /// Actual kernel time when the original goal fact completed the Mission, if it did.
    completed_time: Option<KernelTime>,
    /// Original bounded late/negative/unavailable disposition; it never rewrites source evidence.
    incident: Option<String>,
}

/// Canonical closed mutation; transport authorization remains outside the supplied JSON.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Command {
    /// Exact negotiated application schema.
    schema_version: String,
    /// Immutable caller idempotency identity.
    command_id: String,
    /// Expected authority revision, with zero denoting no original policy.
    expected_revision: u64,
    /// Explicit one-step action.
    action: Action,
}

/// Effects use exact original identities; no caller-provided success or stopped booleans are accepted.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "kebab-case", deny_unknown_fields)]
enum Action {
    /// Freezes complete current ownership and explicit transition permission before candidate output.
    AuthorizeCoordination {
        /// Original bounded candidate ticket.
        ticket_id: String,
        /// Semantic purpose and required original stopping assurance.
        authorization: domain::ExecutionCoordinationRequest,
    },
    /// Declares an exact original-owner child before output, provider claims or Review.
    AuthorizeMemory {
        /// Original finite candidate budget; the declaration never creates another ticket.
        ticket_id: String,
        /// Complete immutable planned child and original policy identity, without resource selection.
        authorization: Box<memory::AuthorizationRequest>,
    },
    /// Freezes the original MI SQL worker and provenance identity before any model work.
    ClaimRuntimeWork {
        /// Original fault-admitted ticket.
        ticket_id: String,
        /// Exact original persistent worker identity.
        claim: GenerationClaimInput,
    },
    /// Consumes a phase-specific provider permission before its actual network request.
    AdmitProviderCall {
        /// Original claimed fault ticket.
        ticket_id: String,
        /// Exactly provider-generate or provider-review; each may be admitted once.
        phase: String,
        /// Fresh original SQL effect nonce.
        nonce: String,
        /// Exact original request and endpoint digest.
        request_digest: String,
    },
    /// Explicit v0.2 administrative delegation of bounded fault-triggered candidate generation.
    AuthorizeGeneration,
    /// Freezes original bounded permission; subsequent install is an immutable replay only.
    InstallPolicy {
        /// Complete original permission and independent goal contract input.
        policy: Policy,
    },
    /// Reduces authority without releasing resources or erasing original work.
    RevokePolicy,
    /// Consumes one durable original candidate/model-call budget before work starts.
    RequestCandidate {
        /// New immutable work identity, consumed before a provider may run.
        ticket_id: String,
    },
    /// Records original output; semantic rejection still consumes its original ticket.
    SubmitCandidate {
        /// Previously admitted original work identity.
        ticket_id: String,
        /// Complete published semantic output, never a Node assignment.
        plan: serde_json::Value,
        /// Actual proposing principal's retained original production provenance.
        provenance: serde_json::Value,
    },
    /// Explicit current administrative Review of the exact submitted candidate.
    ReviewCandidate {
        /// Previously completed original work identity.
        ticket_id: String,
        /// Exact normalized candidate digest seen by the reviewer.
        plan_digest: String,
        /// Explicit administrative permission or rejection.
        decision: ReviewDecision,
        /// Original bounded explanation, separate from independent success evidence.
        reason: String,
    },
    /// Freezes an explicit future completion precondition and complete reviewed alternative.
    ApproveAlternative {
        /// Original future alternative identity, consuming one policy ticket.
        ticket_id: String,
        /// Exact complete published semantic alternative.
        plan: serde_json::Value,
        /// Declared future condition, never a claim that these Tasks are already satisfied.
        completion_precondition: BTreeSet<domain::TaskId>,
    },
    /// Persists original source/target plus hold before physical stop/content revoke delivery.
    RequestSwitch {
        /// Exact previously approved candidate.
        ticket_id: String,
        /// New configuration identity, granting no executor or resource ownership.
        target_group_id: domain::ExecutionGroupId,
    },
    /// Applies a previously accepted exact switch only after all original independent barriers.
    CommitSwitch {
        /// Original durable stop/revoke intent; this action cannot replace its source or deadline.
        switch_command_id: String,
    },
}

/// Hashes typed original evidence with a stable canonical JSON representation.
fn digest(value: &impl Serialize) -> Result<String, String> {
    Ok(format!(
        "sha256:{:x}",
        Sha256::digest(
            serde_json::to_vec(&serde_json::to_value(value).map_err(|e| e.to_string())?)
                .map_err(|e| e.to_string())?
        )
    ))
}

/// Validates bounded caller identities without interpreting text as an authority decision.
fn identity(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 128
        && value.trim() == value
        && !value.chars().any(char::is_control)
}

/// Checks a canonical original evidence digest rather than accepting an arbitrary provenance label.
fn valid_digest(value: &str) -> bool {
    value.strip_prefix("sha256:").is_some_and(|hex| {
        hex.len() == 64
            && hex
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    })
}

pub(crate) use admission::{candidate_view, submit, view};
pub(crate) use checkpoint::{attach_deployment, deserialize_states, validate_restored};
pub(crate) use goal::apply_pending as apply_goal_pending;
pub(crate) use goal::{observe_goal_ingress, submit_goal};
pub(crate) use memory::{
    catalog_retirement_evidence, install_catalog, permits_parent_reference,
    requires_scoped_preparation, scoped_typed_source,
};
pub(crate) use switching::{blocks_mission, close_terminal, observe_trigger, tick};
