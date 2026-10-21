//! Revisioned tenant resource quota policy carried by authenticated administration commands.

use crate::ResourceKind;
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

/// One bounded tenant/category logical allocation ceiling.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ResourceQuotaLimit {
    /// Immutable tenant namespace selected by authenticated Mission authority.
    pub tenant_id: String,
    /// Category charged by committed logical resource demand.
    pub kind: ResourceKind,
    /// Maximum concurrently committed logical units.
    pub max_units: u64,
}

/// Complete revisioned quota policy committed through the privileged operator command path.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ResourceQuotaRegistry {
    /// Stable policy identity across revisions.
    pub registry_id: String,
    /// Monotonic complete-policy generation.
    pub revision: u64,
    /// Complete tenant/category limits in canonical order.
    pub limits: Vec<ResourceQuotaLimit>,
}

impl ResourceQuotaRegistry {
    /// Validates identity, bounds, uniqueness and canonical ordering before policy admission.
    pub fn validate(&self) -> Result<(), String> {
        if self.registry_id.trim().is_empty()
            || self.registry_id.len() > 128
            || self.revision == 0
            || self.limits.len() > 4096
        {
            return Err("resource quota registry identity or bounds are invalid".into());
        }
        let mut previous = None;
        let mut keys = BTreeSet::new();
        for limit in &self.limits {
            if limit.tenant_id.trim().is_empty()
                || limit.tenant_id.len() > 128
                || limit.max_units == 0
                || limit.max_units > 1_000_000_000
                || !keys.insert((limit.tenant_id.as_str(), limit.kind))
            {
                return Err("resource quota limit is invalid or duplicated".into());
            }
            let key = (&limit.tenant_id, limit.kind);
            if previous.is_some_and(|old| old >= key) {
                return Err("resource quota limits are not in canonical order".into());
            }
            previous = Some(key);
        }
        Ok(())
    }

    /// Rejects identity changes, rollback and skipped revisions.
    pub fn validates_successor(&self, next: &Self) -> Result<(), String> {
        next.validate()?;
        if self.registry_id != next.registry_id
            || next.revision
                != self
                    .revision
                    .checked_add(1)
                    .ok_or("quota revision exhausted")?
        {
            return Err("resource quota registry successor conflicts".into());
        }
        Ok(())
    }
}
