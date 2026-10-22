//! Explicit release of one original completed upload, independent of Catalog or content deletion.

use crate::{ArtifactUploadPhase, ArtifactUploadRecord};
use serde::{Deserialize, Serialize};

/// Current authenticated retirement evidence; the original completed upload remains unchanged.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ArtifactUploadRetirement {
    /// Versioned release semantics, granting no unlink or content-read permission.
    pub schema: String,
    /// Original immutable administrative command identity.
    pub command_id: String,
    /// Actual original software CAS owner whose upload rights are being released.
    pub store_owner_id: String,
    /// Complete original completed upload and its immutable byte/source/time proof.
    pub upload: ArtifactUploadRecord,
    /// Actual current stable uploader or same-tenant administrator.
    pub principal_id: String,
    /// Actual current immutable tenant, equal to the original upload tenant.
    pub tenant_id: String,
    /// Actual current TLS leaf; explicit raw-loopback identity retains None.
    pub certificate_digest: Option<String>,
    /// Complete canonical current grant/registry interpretation, without credentials.
    pub canonical_grant_json: String,
    /// First original retirement admission; retry never refreshes it.
    pub admitted_at_ms: u64,
    /// Explicit bounded retention decision, never an inferred timeout or process-exit result.
    pub reason: String,
}

impl ArtifactUploadRetirement {
    /// Rejects incomplete original byte authority or inconsistent current tenant/grant evidence.
    pub fn validate(&self) -> Result<(), String> {
        self.upload.validate()?;
        let bounded = |value: &str, maximum: usize| {
            !value.trim().is_empty()
                && value.len() <= maximum
                && !value.chars().any(char::is_control)
                && !value.contains('*')
        };
        let hex = self
            .store_owner_id
            .strip_prefix("artifact-store:")
            .unwrap_or("");
        if self.schema != "roboguide.artifact-upload-retirement/v0.1"
            || !bounded(&self.command_id, 128)
            || !bounded(&self.principal_id, 256)
            || !bounded(&self.tenant_id, 128)
            || !bounded(&self.reason, 1024)
            || hex.len() != 64
            || !hex
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
            || self.upload.phase != ArtifactUploadPhase::Completed
            || self.upload.tenant_id != self.tenant_id
            || self.admitted_at_ms < self.upload.completed_at_ms.unwrap_or(u64::MAX)
            || self.canonical_grant_json.len() > 64 * 1024
        {
            return Err(
                "upload retirement changes original authority or lacks its byte/source proof"
                    .into(),
            );
        }
        let grant: serde_json::Value =
            serde_json::from_str(&self.canonical_grant_json).map_err(|error| error.to_string())?;
        if !grant.is_object()
            || serde_json::to_string(&grant).map_err(|error| error.to_string())?
                != self.canonical_grant_json
        {
            return Err("upload retirement lacks complete canonical current grant evidence".into());
        }
        if let Some(leaf) = &self.certificate_digest {
            let hex = leaf.strip_prefix("sha256:").unwrap_or("");
            if hex.len() != 64
                || !hex
                    .bytes()
                    .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
            {
                return Err("upload retirement lacks its actual TLS leaf identity".into());
            }
        }
        Ok(())
    }
}
