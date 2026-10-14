//! Deployment-owned revisioned HTTP certificate grants, separate from Mission and device identities.

use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};

/// Versioned operator certificate authority. Historical grants stay retained and may be revoked.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OperatorAuthorization {
    /// Exact wire/checkpoint schema.
    pub schema_version: String,
    /// Immutable deployment registry identity.
    pub registry_id: String,
    /// Monotonic policy revision.
    pub revision: u64,
    /// Exact verified leaf digest, qualified with sha256:.
    pub grants: BTreeMap<String, OperatorCertificateGrant>,
}

/// One stable human/service principal's certificate delegation.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OperatorCertificateGrant {
    /// Stable audit and idempotency principal, independent of rotation leaf.
    pub principal_id: String,
    /// Controller permission tier.
    pub role: String,
    /// Tenant delegation; global * is restricted to recovery administrator.
    pub tenant_ids: BTreeSet<String>,
    /// Inclusive deployment validity start.
    #[serde(default)]
    pub not_before_ms: u64,
    /// Exclusive deployment expiry, zero means managed validity bounded by actual X.509 expiry.
    #[serde(default)]
    pub expires_at_ms: u64,
    /// Irreversible certificate revocation.
    #[serde(default)]
    pub revoked: bool,
}

impl OperatorAuthorization {
    /// Checks closed schema, bounds and explicit global delegation before admission.
    pub fn validate(&self) -> Result<(), String> {
        if self.schema_version != "roboguide.operator-authorization/v0.1"
            || self.registry_id.trim().is_empty()
            || self.registry_id.len() > 128
            || self.revision == 0
            || self.grants.is_empty()
            || self.grants.len() > 4096
        {
            return Err("operator authorization envelope is invalid".into());
        }
        for (digest, grant) in &self.grants {
            if !digest.strip_prefix("sha256:").is_some_and(|hex| {
                hex.len() == 64
                    && hex
                        .bytes()
                        .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
            }) || grant.principal_id.trim().is_empty()
                || grant.principal_id.len() > 128
                || !matches!(
                    grant.role.as_str(),
                    "viewer" | "operator" | "recovery-admin"
                )
                || grant.tenant_ids.is_empty()
                || grant.tenant_ids.len() > 64
                || grant
                    .tenant_ids
                    .iter()
                    .any(|tenant| tenant.trim().is_empty() || tenant.len() > 128)
                || grant.tenant_ids.contains("*") && grant.role != "recovery-admin"
                || grant.expires_at_ms != 0 && grant.expires_at_ms <= grant.not_before_ms
            {
                return Err("operator certificate grant is invalid".into());
            }
        }
        Ok(())
    }

    /// Admits a monotonic deployment update without allowing retired leaves or principal reattribution.
    pub fn validates_successor(&self, next: &Self) -> Result<(), String> {
        next.validate()?;
        if self.registry_id != next.registry_id
            || self.revision > next.revision
            || self.revision == next.revision && self != next
        {
            return Err("operator authorization rollback or revision conflict".into());
        }
        for (digest, previous) in &self.grants {
            let current = next
                .grants
                .get(digest)
                .ok_or("historical grants must be revoked, not removed")?;
            if previous.principal_id != current.principal_id || previous.revoked && !current.revoked
            {
                return Err("operator certificate attribution/revocation is immutable".into());
            }
        }
        Ok(())
    }
}

impl OperatorCertificateGrant {
    /// Checks fresh local time; transport separately checks the actual peer certificate expiry.
    pub fn active(&self, now_ms: u64) -> bool {
        !self.revoked
            && now_ms >= self.not_before_ms
            && (self.expires_at_ms == 0 || now_ms < self.expires_at_ms)
    }
}
