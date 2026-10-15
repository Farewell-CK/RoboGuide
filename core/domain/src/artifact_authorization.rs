//! Revisioned data-plane certificate attribution; content policy remains independently authoritative.

use crate::NodeId;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};

/// Closed data-plane role, independent of operator and Node transport privileges.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum ArtifactAccessRole {
    /// Read exact resources; immutable content policy and scope are checked separately.
    Viewer,
    /// Upload/publish only through the attributed Node and providers.
    Writer,
    /// Manage data-plane resources within the original explicit tenant.
    Admin,
}

/// One actual TLS leaf delegated to an immutable stable principal and tenant.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ArtifactCertificateGrant {
    /// Stable service/human identity, retained through certificate rotation.
    pub principal_id: String,
    /// Exact original tenant, never inferred from a request header or Node display name.
    pub tenant_id: String,
    /// Closed data-plane operation tier.
    pub role: ArtifactAccessRole,
    /// Exact publishing Node when delegated.
    pub node_id: Option<NodeId>,
    /// Exact receiving or producing provider identities.
    pub provider_ids: BTreeSet<String>,
    /// Resource-level bound for generic metadata access.
    pub memory_ids: BTreeSet<String>,
    /// Resource-level bound for typed Spatial metadata access.
    pub map_ids: BTreeSet<String>,
    /// Explicit immutable blob identities; content scope/policy still applies.
    pub artifact_digests: BTreeSet<String>,
    /// Inclusive Controller-local deployment validity start.
    pub not_before_ms: u64,
    /// Exclusive deployment expiry; zero remains bounded by the actual TLS leaf validity.
    pub expires_at_ms: u64,
    /// Irreversible retirement of this exact leaf.
    pub revoked: bool,
}

impl ArtifactCertificateGrant {
    /// Rechecks current Controller time independently of the actual certificate notAfter fence.
    pub fn active(&self, now_ms: u64) -> bool {
        !self.revoked
            && now_ms >= self.not_before_ms
            && (self.expires_at_ms == 0 || now_ms < self.expires_at_ms)
    }
}

/// Current explicit Artifact identity authority; revocation never deletes original attribution.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ArtifactAuthorizationRegistry {
    /// Required versioned data-plane schema.
    pub schema_version: String,
    /// Immutable registry identity across restarts and migrations.
    pub registry_id: String,
    /// Monotonic complete-registry revision.
    pub revision: u64,
    /// Exact SHA-256 leaf identity to stable principal and bounded privileges.
    pub grants: BTreeMap<String, ArtifactCertificateGrant>,
}

impl ArtifactAuthorizationRegistry {
    /// Validates exact, bounded resource grants and consistent stable-principal attribution.
    pub fn validate(&self) -> Result<(), String> {
        if self.schema_version != "roboguide.artifact-authorization/v0.1"
            || !valid_text(&self.registry_id, 128)
            || self.revision == 0
            || self.grants.is_empty()
            || self.grants.len() > 4096
        {
            return Err("Artifact authorization envelope is invalid".into());
        }
        let mut principals = BTreeMap::new();
        for (leaf, grant) in &self.grants {
            if !valid_leaf(leaf)
                || !valid_text(&grant.principal_id, 128)
                || !valid_text(&grant.tenant_id, 128)
                || grant.role == ArtifactAccessRole::Writer
                    && (grant.node_id.is_none() || grant.provider_ids.is_empty())
                || grant.expires_at_ms != 0 && grant.expires_at_ms <= grant.not_before_ms
            {
                return Err("Artifact certificate attribution/validity is invalid".into());
            }
            for items in [
                &grant.provider_ids,
                &grant.memory_ids,
                &grant.map_ids,
                &grant.artifact_digests,
            ] {
                if items.len() > 128 || items.iter().any(|v| !valid_text(v, 256)) {
                    return Err("Artifact resource grants require bounded exact identities".into());
                }
            }
            if grant.artifact_digests.iter().any(|v| !valid_leaf(v)) {
                return Err("Artifact blob grants must name exact content digests".into());
            }
            if principals
                .insert(&grant.principal_id, (&grant.tenant_id, &grant.node_id))
                .is_some_and(|previous| previous != (&grant.tenant_id, &grant.node_id))
            {
                return Err(
                    "Artifact principal cannot name different original tenants or Nodes".into(),
                );
            }
        }
        Ok(())
    }

    /// Retains original leaf/principal attribution and rejects rollback, removal and unrevocation.
    pub fn validates_successor(&self, next: &Self) -> Result<(), String> {
        next.validate()?;
        if self.registry_id != next.registry_id
            || next.revision < self.revision
            || next.revision == self.revision && next != self
        {
            return Err("Artifact authorization identity/revision conflicts".into());
        }
        for (leaf, old) in &self.grants {
            let current = next
                .grants
                .get(leaf)
                .ok_or("Artifact historical leaves must be revoked, not removed")?;
            if old.principal_id != current.principal_id
                || old.tenant_id != current.tenant_id
                || old.node_id != current.node_id
                || old.revoked && !current.revoked
            {
                return Err(
                    "Artifact original attribution and leaf revocation are immutable".into(),
                );
            }
        }
        Ok(())
    }
}

/// Excludes wildcards, control characters and ambiguous surrounding whitespace from trusted identifiers.
fn valid_text(value: &str, max: usize) -> bool {
    !value.is_empty()
        && value.trim() == value
        && value.len() <= max
        && !value.contains('*')
        && !value.chars().any(char::is_control)
}

/// Checks exact lowercase SHA-256 for both verified leaves and immutable CAS identifiers.
fn valid_leaf(value: &str) -> bool {
    value.strip_prefix("sha256:").is_some_and(|h| {
        h.len() == 64
            && h.bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    })
}
