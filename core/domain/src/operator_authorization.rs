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
    /// Tenant delegation; global * requires recovery administration or the metrics-only role.
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
        if !matches!(
            self.schema_version.as_str(),
            "roboguide.operator-authorization/v0.1" | "roboguide.operator-authorization/v0.2"
        ) || self.registry_id.trim().is_empty()
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
                    "viewer" | "operator" | "recovery-admin" | "metrics-reader"
                )
                || grant.tenant_ids.is_empty()
                || grant.tenant_ids.len() > 64
                || grant
                    .tenant_ids
                    .iter()
                    .any(|tenant| tenant.trim().is_empty() || tenant.len() > 128)
                || grant.tenant_ids.contains("*")
                    && !matches!(grant.role.as_str(), "recovery-admin" | "metrics-reader")
                || grant.role == "metrics-reader"
                    && grant.tenant_ids != BTreeSet::from(["*".into()])
                || grant.role == "metrics-reader"
                    && self.schema_version != "roboguide.operator-authorization/v0.2"
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
            || self.schema_version == "roboguide.operator-authorization/v0.2"
                && next.schema_version != self.schema_version
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

#[cfg(test)]
mod tests {
    use super::*;

    /// Global metrics delegation is purpose-limited; ordinary viewers/operators retain tenant fences.
    #[test]
    fn global_metrics_role_cannot_be_used_as_a_tenant_or_generic_viewer_grant() {
        for (role, tenants, valid) in [
            ("metrics-reader", vec!["*"], true),
            ("metrics-reader", vec!["tenant"], false),
            ("metrics-reader", vec!["*", "tenant"], false),
            ("viewer", vec!["*"], false),
            ("operator", vec!["*"], false),
            ("recovery-admin", vec!["*"], true),
        ] {
            let profile = OperatorAuthorization {
                schema_version: "roboguide.operator-authorization/v0.2".into(),
                registry_id: "unit-authorization".into(),
                revision: 1,
                grants: BTreeMap::from([(
                    format!("sha256:{}", "a".repeat(64)),
                    OperatorCertificateGrant {
                        principal_id: "unit-principal".into(),
                        role: role.into(),
                        tenant_ids: tenants.into_iter().map(str::to_string).collect(),
                        not_before_ms: 0,
                        expires_at_ms: 0,
                        revoked: false,
                    },
                )]),
            };
            assert_eq!(profile.validate().is_ok(), valid, "{role}");
        }
    }

    /// The explicit metrics-role codec can upgrade original grants but cannot be downgraded by revision.
    #[test]
    fn metrics_authorization_upgrade_retains_a_codec_downgrade_fence() {
        let original = OperatorAuthorization {
            schema_version: "roboguide.operator-authorization/v0.1".into(),
            registry_id: "original".into(),
            revision: 1,
            grants: BTreeMap::from([(
                format!("sha256:{}", "a".repeat(64)),
                OperatorCertificateGrant {
                    principal_id: "administrator".into(),
                    role: "recovery-admin".into(),
                    tenant_ids: BTreeSet::from(["*".into()]),
                    not_before_ms: 0,
                    expires_at_ms: 0,
                    revoked: false,
                },
            )]),
        };
        let mut upgraded = original.clone();
        upgraded.schema_version = "roboguide.operator-authorization/v0.2".into();
        upgraded.revision = 2;
        assert!(original.validates_successor(&upgraded).is_ok());
        let mut downgraded = original;
        downgraded.revision = 3;
        assert!(upgraded.validates_successor(&downgraded).is_err());
        downgraded.grants.values_mut().next().unwrap().role = "metrics-reader".into();
        assert!(downgraded.validate().is_err());
    }
}
