//! Durable administrator command identity for independently versioned source registries.

use serde::{Deserialize, Serialize};

/// Closed source registry administration kind; an arbitrary label cannot select an authority.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RegistryCommandKind {
    /// Explicit scalar/covariance source fusion contracts.
    BeliefPolicy,
    /// Source-aware State decision policy.
    StatePolicy,
    /// Independent semantic verification producers.
    SemanticVerifier,
    /// Explicit immutable Memory content, consumer and exchange policy.
    MemoryAuthorization,
    /// Revisioned stable-principal Artifact certificate grants.
    ArtifactAuthorization,
}

/// Original authenticated result of one idempotent registry change.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RegistryCommandReceipt {
    /// Caller-assigned immutable operation identity.
    pub command_id: String,
    /// Exact authority selected by the public route.
    pub kind: RegistryCommandKind,
    /// Hash of the complete typed input, including expected revision.
    pub input_digest: String,
    /// Accepted complete registry content identity.
    pub registry_digest: String,
    /// Original resulting authority revision.
    pub revision: u64,
    /// Certificate-derived stable principal that admitted the original command.
    pub authority_subject: String,
    /// Original Controller receive time, never renewed by retry.
    pub accepted_at_ms: u64,
}
