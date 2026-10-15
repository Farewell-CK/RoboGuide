//! Durable upload admission and byte-ownership receipts; hashes alone never grant content rights.

use serde::{Deserialize, Serialize};

/// Closed upload workflow stages, distinct from semantic Local EAIOS import outcomes.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ArtifactUploadPhase {
    /// Identity was durably admitted before creating an incomplete staging file.
    Started,
    /// The actual complete staged bytes were hashed and fsynced before CAS finalization.
    Prepared,
    /// CAS presence/size was verified after the original prepared byte proof.
    Completed,
    /// This upload was abandoned; possible immutable orphan bytes remain subject to normal GC.
    Abandoned,
}

/// Complete original upload source and current bounded transition evidence.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ArtifactUploadRecord {
    /// Original caller command identity; it is never reissued after a process restart.
    pub upload_id: String,
    /// Stable authenticated principal, independent of leaf rotation.
    pub principal_id: String,
    /// Original immutable tenant.
    pub tenant_id: String,
    /// Exact leaf present at admission, or explicit loopback identity.
    pub original_certificate: String,
    /// Original receive time, never refreshed by retry or reconciliation.
    pub admitted_at_ms: u64,
    /// Exclusive original workflow budget; retry cannot extend it.
    pub deadline_ms: u64,
    /// Current evidence phase; Prepared must precede any CAS completion receipt.
    pub phase: ArtifactUploadPhase,
    /// Actual complete staged content identity, present only after a durable byte proof.
    pub content_digest: Option<String>,
    /// Actual complete byte count from the same staged proof.
    pub byte_size: Option<u64>,
    /// First durable preparation time, retained after completion or abandonment.
    pub prepared_at_ms: Option<u64>,
    /// First conclusive completion time; late reconciliation never renews the original budget.
    pub completed_at_ms: Option<u64>,
}

impl ArtifactUploadRecord {
    /// Rejects incomplete source attribution, malformed content proof or contradictory workflow time.
    pub fn validate(&self) -> Result<(), String> {
        if self.upload_id.is_empty()
            || self.upload_id.len() > 128
            || !self
                .upload_id
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'-' | b'_'))
            || self.principal_id.trim().is_empty()
            || self.principal_id.len() > 256
            || self.tenant_id.trim().is_empty()
            || self.tenant_id.len() > 128
            || self.original_certificate.trim().is_empty()
            || self.original_certificate.len() > 256
            || self.deadline_ms <= self.admitted_at_ms
            || self.deadline_ms - self.admitted_at_ms > 1_800_000
        {
            return Err("Artifact upload original source/budget is invalid".into());
        }
        let proof = self.content_digest.as_deref().is_some_and(|d| {
            d.strip_prefix("sha256:").is_some_and(|h| {
                h.len() == 64
                    && h.bytes()
                        .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
            })
        }) && self.byte_size.is_some_and(|s| s <= 4 * 1024 * 1024 * 1024)
            && self
                .prepared_at_ms
                .is_some_and(|t| t >= self.admitted_at_ms && t < self.deadline_ms);
        match self.phase {
            ArtifactUploadPhase::Started
                if self.content_digest.is_none()
                    && self.byte_size.is_none()
                    && self.prepared_at_ms.is_none()
                    && self.completed_at_ms.is_none() =>
            {
                Ok(())
            }
            ArtifactUploadPhase::Prepared if proof && self.completed_at_ms.is_none() => Ok(()),
            ArtifactUploadPhase::Completed
                if proof
                    && self
                        .completed_at_ms
                        .is_some_and(|t| t >= self.prepared_at_ms.unwrap_or(u64::MAX)) =>
            {
                Ok(())
            }
            ArtifactUploadPhase::Abandoned
                if self.completed_at_ms.is_none()
                    && (proof
                        || self.content_digest.is_none()
                            && self.byte_size.is_none()
                            && self.prepared_at_ms.is_none()) =>
            {
                Ok(())
            }
            _ => Err("Artifact upload phase lacks its actual original byte/time proof".into()),
        }
    }

    /// Checks one immutable-source transition; replay never adds another physical upload or fresh proof.
    pub fn validates_successor(&self, next: &Self) -> Result<(), String> {
        next.validate()?;
        if self == next {
            return Ok(());
        }
        if self.upload_id != next.upload_id
            || self.principal_id != next.principal_id
            || self.tenant_id != next.tenant_id
            || self.original_certificate != next.original_certificate
            || self.admitted_at_ms != next.admitted_at_ms
            || self.deadline_ms != next.deadline_ms
            || (self.content_digest.is_some()
                && (self.content_digest != next.content_digest
                    || self.byte_size != next.byte_size
                    || self.prepared_at_ms != next.prepared_at_ms))
            || !matches!(
                (self.phase, next.phase),
                (
                    ArtifactUploadPhase::Started,
                    ArtifactUploadPhase::Prepared | ArtifactUploadPhase::Abandoned
                ) | (
                    ArtifactUploadPhase::Prepared,
                    ArtifactUploadPhase::Completed | ArtifactUploadPhase::Abandoned
                )
            )
        {
            return Err(
                "Artifact upload original identity, byte proof or monotonic phase conflicts".into(),
            );
        }
        Ok(())
    }
}
