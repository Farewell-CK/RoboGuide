//! Closed deployment policy whose original revisions survive participant process restart.

use super::*;

/// Complete deployment-owned participant policy, separate from ordinary operator privileges.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Policy {
    /// Exact policy codec; static maps cannot acquire durable authority.
    pub(crate) schema_version: String,
    /// Stable original policy identity.
    pub(crate) registry_id: String,
    /// Consecutive policy generation.
    pub(crate) revision: u64,
    /// Immutable domain whose actual local Controller owns this database.
    pub(crate) local_domain: String,
    /// Explicit expected epochs, never inferred remote consensus observations.
    pub(crate) domain_epochs: BTreeMap<String, u64>,
    /// Actual TLS leaf grants, retained after irreversible revocation.
    pub(crate) grants: BTreeMap<String, Grant>,
}

/// Domain-pair delegation for one original actual leaf and stable operator principal.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Grant {
    /// Explicit v0.2 delegation for resource-bearing coordination, absent in old metadata grants.
    #[serde(default, skip_serializing_if = "is_false")]
    pub(crate) execution_coordinator: bool,
    /// Stable principal must also match the current operator TLS grant.
    pub(crate) principal_id: String,
    /// Exact tenants, without wildcard inheritance.
    pub(crate) tenant_ids: BTreeSet<String>,
    /// Explicit complete allowed domains, including the local participant.
    pub(crate) domain_ids: BTreeSet<String>,
    /// Irreversible retirement of this actual leaf.
    pub(crate) revoked: bool,
}

impl Policy {
    /// Rejects undeclared domains, unbounded grants and wildcard authority.
    pub(crate) fn validate(&self) -> Result<(), String> {
        if !matches!(
            self.schema_version.as_str(),
            "roboguide.federation-authorization/v0.1" | "roboguide.federation-authorization/v0.2"
        ) || self.schema_version.ends_with("/v0.1")
            && self
                .grants
                .values()
                .any(|grant| grant.execution_coordinator)
            || !valid_name(&self.registry_id)
            || !valid_name(&self.local_domain)
            || self.revision == 0
            || !(2..=64).contains(&self.domain_epochs.len())
            || !self.domain_epochs.contains_key(&self.local_domain)
            || self
                .domain_epochs
                .iter()
                .any(|(name, epoch)| !valid_name(name) || *epoch == 0)
            || self.grants.is_empty()
            || self.grants.len() > 4096
        {
            return Err("invalid Federation authorization policy".into());
        }
        for (leaf, grant) in &self.grants {
            if !valid_digest(leaf)
                || !valid_name(&grant.principal_id)
                || grant.tenant_ids.is_empty()
                || grant.tenant_ids.len() > 64
                || grant.tenant_ids.iter().any(|id| !valid_name(id))
                || !(2..=64).contains(&grant.domain_ids.len())
                || !grant.domain_ids.contains(&self.local_domain)
                || grant
                    .domain_ids
                    .iter()
                    .any(|id| !self.domain_epochs.contains_key(id))
            {
                return Err("invalid Federation certificate delegation".into());
            }
        }
        Ok(())
    }

    /// Retains identity, epoch watermarks and every historical leaf across consecutive updates.
    pub(crate) fn successor(&self, next: &Self) -> Result<(), String> {
        next.validate()?;
        if self == next {
            return Ok(());
        }
        if self.schema_version.ends_with("/v0.2") && !next.schema_version.ends_with("/v0.2")
            || next.registry_id != self.registry_id
            || next.local_domain != self.local_domain
            || self.revision.checked_add(1) != Some(next.revision)
            || self
                .domain_epochs
                .iter()
                .any(|(id, old)| next.domain_epochs.get(id).is_none_or(|new| new < old))
            || self.grants.iter().any(|(leaf, old)| {
                next.grants.get(leaf).is_none_or(|new| {
                    new.principal_id != old.principal_id || old.revoked && !new.revoked
                })
            })
        {
            return Err("Federation policy rollback, replacement or retired leaf reuse".into());
        }
        Ok(())
    }

    /// Checks current exact TLS/principal/tenant/pair without treating a configured epoch as a live proof.
    pub(crate) fn authorize(
        &self,
        caller: &Caller,
        session: &FederationSession,
    ) -> Result<(), String> {
        let grant = self
            .grants
            .get(&caller.leaf)
            .ok_or("Federation leaf is not enrolled")?;
        if grant.revoked
            || grant.principal_id != caller.principal
            || !grant.tenant_ids.contains(&caller.tenant)
            || !grant.domain_ids.contains(&session.source_domain)
            || !grant.domain_ids.contains(&session.target_domain)
            || self.domain_epochs.get(&session.source_domain) != Some(&session.source_epoch)
        {
            return Err(
                "Federation current principal, tenant, pair or expected epoch refused".into(),
            );
        }
        validate_session_route(session, &self.local_domain, Some(&caller.tenant))
    }
}

/// Preserves original v0.1 grant bytes when the new delegation is absent.
fn is_false(value: &bool) -> bool {
    !value
}

/// Reads only an explicitly configured closed policy; no default local domain is invented.
pub(crate) fn configured_policy() -> Result<Option<Policy>, String> {
    let domain = std::env::var("ROBOGUIDE_CONTROL_DOMAIN").ok();
    let path = std::env::var_os("ROBOGUIDE_FEDERATION_DOMAIN_GRANTS");
    match (domain, path) {
        (None, None) => Ok(None),
        (Some(domain), Some(path)) => {
            let file = std::fs::File::open(path).map_err(|error| error.to_string())?;
            if file.metadata().map_err(|error| error.to_string())?.len() > 1024 * 1024 {
                return Err("Federation policy file exceeds its bound".into());
            }
            use std::io::Read;
            let mut bytes = Vec::new();
            file.take(1024 * 1024 + 1)
                .read_to_end(&mut bytes)
                .map_err(|error| error.to_string())?;
            if bytes.len() > 1024 * 1024 {
                return Err("Federation policy file grew beyond its bound".into());
            }
            let policy: Policy =
                serde_json::from_slice(&bytes).map_err(|error| error.to_string())?;
            policy.validate()?;
            if policy.local_domain != domain {
                return Err("configured Control domain differs from its Federation policy".into());
            }
            Ok(Some(policy))
        }
        _ => Err("Federation domain and versioned policy must be configured together".into()),
    }
}

/// Bounds declared names and excludes wildcard, control characters and path delimiters.
fn valid_name(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 128
        && value.trim() == value
        && !value
            .chars()
            .any(|c| c.is_control() || matches!(c, '/' | '?' | '#' | '*'))
}

/// Checks the actual leaf/digest spelling independently of certificate subject text.
pub(super) fn valid_digest(value: &str) -> bool {
    value.strip_prefix("sha256:").is_some_and(|hex| {
        hex.len() == 64
            && hex
                .bytes()
                .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
    })
}
