//! Explicit replacement uploads retain original Prepared evidence and independent ownership.

use crate::{ArtifactUploadPhase, ArtifactUploadRecord};
use serde::{Deserialize, Serialize};

/// Actual complete replacement evidence; absence remains an unresolved original recovery.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ArtifactUploadRecoveryCompletion {
    /// Normal new upload's actual completed CAS proof, never a mutation of the source upload.
    pub replacement: ArtifactUploadRecord,
    /// Exact canonical actual Root reader close receipt, independently validated by Replica.
    pub reader_receipt_json: String,
    /// Actual original reader admission binding, distinct from its close receipt digest.
    pub reader_authority_digest: String,
}

/// Original one-shot recovery intent and independently recorded successful replacement.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ArtifactUploadRecovery {
    /// Explicit independent recovery contract.
    pub schema: String,
    /// Original bounded command identity; retry never repeats its effects.
    pub command_id: String,
    /// Original explicitly requested complete recovery duration, never refreshed by retry.
    pub budget_ms: u64,
    /// Exact one-shot original reader, frozen before any Root admission or payload I/O.
    pub reader_id: String,
    /// Exact unchanged original Prepared record, including its original tenant and budget.
    pub upload: ArtifactUploadRecord,
    /// Actual original qualified Artifact owner; never the current Replica Root owner.
    pub source_owner_id: String,
    /// Independent normal replacement's frozen Started admission.
    pub replacement_admission: ArtifactUploadRecord,
    /// Actual new application CAS owner, independent of the original source.
    pub destination_owner_id: String,
    /// Actual original destination writer lease device.
    pub destination_device: u64,
    /// Actual original destination writer lease inode.
    pub destination_inode: u64,
    /// Canonical original managed Controller incarnation, validated against actual Unix peer evidence.
    pub controller_process_json: String,
    /// Canonical original same-kernel budget, independently decoded and checked by Replica.
    pub kernel_budget_json: String,
    /// Canonical complete actual authenticated grant/registry context, without secrets.
    pub canonical_grant_json: String,
    /// Actual original replacement completion; None preserves the original unresolved outcome.
    pub completion: Option<ArtifactUploadRecoveryCompletion>,
}

/// Validates canonical bounded evidence without pretending serialized values are current authority.
fn canonical(value: &str, bound: usize) -> Result<(), String> {
    if value.len() > bound {
        return Err("original upload recovery evidence exceeds its bound".into());
    }
    let parsed: serde_json::Value = serde_json::from_str(value).map_err(|e| e.to_string())?;
    if !parsed.is_object() || serde_json::to_string(&parsed).map_err(|e| e.to_string())? != value {
        return Err("upload recovery requires complete canonical object evidence".into());
    }
    Ok(())
}

/// Recognizes an exact software CAS namespace without interpreting it as a physical identity.
fn owner(value: &str) -> bool {
    value.strip_prefix("artifact-store:").is_some_and(|hex| {
        hex.len() == 64
            && hex
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    })
}

impl ArtifactUploadRecovery {
    /// Checks immutable source/replacement identity and complete terminal shape without new effects.
    pub fn validate(&self) -> Result<(), String> {
        self.upload.validate()?;
        self.replacement_admission.validate()?;
        let next = &self.replacement_admission;
        if self.schema != "roboguide.artifact-upload-recovery/v0.1"
            || self.reader_id.is_empty()
            || self.reader_id.len() > 128
            || self.reader_id.chars().any(char::is_control)
            || !(100..=300_000).contains(&self.budget_ms)
            || self.command_id.is_empty()
            || self.command_id.len() > 128
            || !self
                .command_id
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || matches!(b, b'-' | b'_'))
            || self.upload.phase != ArtifactUploadPhase::Prepared
            || next.phase != ArtifactUploadPhase::Started
            || next.upload_id == self.upload.upload_id
            || next.principal_id != self.upload.principal_id
            || next.tenant_id != self.upload.tenant_id
            || !owner(&self.source_owner_id)
            || !owner(&self.destination_owner_id)
            || self.source_owner_id == self.destination_owner_id
            || self.destination_inode == 0
        {
            return Err(
                "upload recovery changes its original source or independent replacement".into(),
            );
        }
        canonical(&self.controller_process_json, 4096)?;
        canonical(&self.kernel_budget_json, 4096)?;
        canonical(&self.canonical_grant_json, 64 * 1024)?;
        if let Some(done) = &self.completion {
            done.replacement.validate()?;
            let mut started = done.replacement.clone();
            started.phase = ArtifactUploadPhase::Started;
            started.content_digest = None;
            started.byte_size = None;
            started.prepared_at_ms = None;
            started.completed_at_ms = None;
            if started != *next
                || done.replacement.phase != ArtifactUploadPhase::Completed
                || done.replacement.content_digest != self.upload.content_digest
                || done.replacement.byte_size != self.upload.byte_size
                || done.reader_authority_digest.len() != 64
                || !done
                    .reader_authority_digest
                    .bytes()
                    .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
            {
                return Err(
                    "upload recovery completion changed its original replacement or bytes".into(),
                );
            }
            canonical(&done.reader_receipt_json, 16 * 1024)?;
        }
        Ok(())
    }

    /// Permits only the original pending intent to gain one immutable actual completion.
    pub fn validates_successor(&self, next: &Self) -> Result<(), String> {
        next.validate()?;
        let mut pending = next.clone();
        pending.completion = self.completion.clone();
        if self != &pending || (self.completion.is_some() && self != next) {
            return Err("upload recovery command changed or repeated its original outcome".into());
        }
        Ok(())
    }
}
