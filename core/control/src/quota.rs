//! Durable tenant/resource quota accounting kept separate from physical reservations.

use domain::ResourceKind;

/// One bounded tenant quota for a resource category.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct QuotaLimit {
    /// Immutable tenant namespace selected by the authenticated Mission authority.
    pub tenant_id: String,
    /// Resource category counted by this limit.
    pub kind: ResourceKind,
    /// Maximum concurrently claimed logical units.
    pub max_units: u64,
}

/// One idempotent logical quota claim; it grants no physical resource ownership.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct QuotaClaim {
    /// Stable Task/Group or operator claim identity.
    pub claim_id: String,
    /// Tenant charged for the claim.
    pub tenant_id: String,
    /// Resource category charged.
    pub kind: ResourceKind,
    /// Logical units consumed by this claim.
    pub units: u64,
}

/// Errors from the durable logical quota ledger.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum QuotaError {
    /// Tenant/category has no configured limit.
    LimitMissing,
    /// Claim identity was reused with changed tenant/category/units.
    ClaimConflict,
    /// Claim would exceed the configured logical limit.
    Exceeded {
        /// Configured maximum units.
        limit: u64,
        /// Units already claimed.
        used: u64,
        /// Units requested by the rejected claim.
        requested: u64,
    },
    /// Identity or unit bounds are invalid.
    Invalid(String),
}

impl std::fmt::Display for QuotaError {
    /// Formats a stable quota rejection without implying physical enforcement.
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::LimitMissing => formatter.write_str("quota limit is not configured"),
            Self::ClaimConflict => formatter.write_str("quota claim identity conflicts"),
            Self::Exceeded {
                limit,
                used,
                requested,
            } => write!(
                formatter,
                "quota exceeded: limit={limit}, used={used}, requested={requested}"
            ),
            Self::Invalid(reason) => write!(formatter, "invalid quota: {reason}"),
        }
    }
}

impl std::error::Error for QuotaError {}

/// Checkpointed quota limits and idempotent logical claims.
#[derive(Debug, Clone, Default, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct QuotaLedger {
    /// Explicit limits in deterministic declaration order.
    limits: Vec<QuotaLimit>,
    /// Current claims retained until their owning lifecycle releases them.
    claims: Vec<QuotaClaim>,
}

impl QuotaLedger {
    /// Creates an empty quota ledger with no implicit limits.
    pub const fn new() -> Self {
        Self {
            limits: Vec::new(),
            claims: Vec::new(),
        }
    }

    /// Installs or replaces one explicit tenant/category limit.
    pub fn set_limit(
        &mut self,
        tenant_id: impl Into<String>,
        kind: ResourceKind,
        max_units: u64,
    ) -> Result<(), QuotaError> {
        let tenant_id = tenant_id.into();
        validate_text(&tenant_id, "tenant_id")?;
        if max_units == 0 || max_units > 1_000_000_000 {
            return Err(QuotaError::Invalid("limit is outside its bound".into()));
        }
        let limit = QuotaLimit {
            tenant_id,
            kind,
            max_units,
        };
        let used = self.used(&limit.tenant_id, limit.kind);
        if let Some(existing) = self
            .limits
            .iter_mut()
            .find(|existing| existing.tenant_id == limit.tenant_id && existing.kind == limit.kind)
        {
            if used > max_units {
                return Err(QuotaError::Exceeded {
                    limit: max_units,
                    used,
                    requested: 0,
                });
            }
            *existing = limit;
        } else {
            self.limits.push(limit);
            self.limits.sort_by(|left, right| {
                (&left.tenant_id, left.kind).cmp(&(&right.tenant_id, right.kind))
            });
        }
        Ok(())
    }

    /// Adds one idempotent claim only when the configured logical quota remains available.
    pub fn reserve(&mut self, claim: QuotaClaim) -> Result<(), QuotaError> {
        validate_text(&claim.claim_id, "claim_id")?;
        validate_text(&claim.tenant_id, "tenant_id")?;
        if claim.units == 0 || claim.units > 1_000_000_000 {
            return Err(QuotaError::Invalid(
                "claim units are outside their bound".into(),
            ));
        }
        if let Some(existing) = self
            .claims
            .iter()
            .find(|item| item.claim_id == claim.claim_id)
        {
            return if existing == &claim {
                Ok(())
            } else {
                Err(QuotaError::ClaimConflict)
            };
        }
        let Some(limit) = self
            .limits
            .iter()
            .find(|item| item.tenant_id == claim.tenant_id && item.kind == claim.kind)
        else {
            return Err(QuotaError::LimitMissing);
        };
        let used = self.used(&claim.tenant_id, claim.kind);
        if used
            .checked_add(claim.units)
            .is_none_or(|total| total > limit.max_units)
        {
            return Err(QuotaError::Exceeded {
                limit: limit.max_units,
                used,
                requested: claim.units,
            });
        }
        self.claims.push(claim);
        self.claims
            .sort_by(|left, right| left.claim_id.cmp(&right.claim_id));
        Ok(())
    }

    /// Releases one exact claim; unknown claims are idempotently treated as already released.
    pub fn release(&mut self, claim_id: &str) -> Result<bool, QuotaError> {
        validate_text(claim_id, "claim_id")?;
        let before = self.claims.len();
        self.claims.retain(|claim| claim.claim_id != claim_id);
        Ok(before != self.claims.len())
    }

    /// Returns currently used logical units for a tenant/category.
    pub fn used(&self, tenant_id: &str, kind: ResourceKind) -> u64 {
        self.claims
            .iter()
            .filter(|claim| claim.tenant_id == tenant_id && claim.kind == kind)
            .map(|claim| claim.units)
            .sum()
    }

    /// Returns the configured remaining logical units, or `None` when no limit exists.
    pub fn remaining(&self, tenant_id: &str, kind: ResourceKind) -> Option<u64> {
        self.limits
            .iter()
            .find(|limit| limit.tenant_id == tenant_id && limit.kind == kind)
            .map(|limit| limit.max_units.saturating_sub(self.used(tenant_id, kind)))
    }

    /// Validates restored limits, claims, duplicate identities and all usage bounds.
    pub fn validate(&self) -> Result<(), QuotaError> {
        let mut checked = Self::new();
        for limit in &self.limits {
            checked.set_limit(limit.tenant_id.clone(), limit.kind, limit.max_units)?;
        }
        for claim in &self.claims {
            checked.reserve(claim.clone())?;
        }
        Ok(())
    }
}

/// Checks one bounded logical quota identity.
fn validate_text(value: &str, name: &str) -> Result<(), QuotaError> {
    if value.trim().is_empty() || value.len() > 256 || value.chars().any(char::is_control) {
        return Err(QuotaError::Invalid(format!("{name} is empty or unbounded")));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn quota_is_idempotent_bounded_and_releases() {
        let mut ledger = QuotaLedger::new();
        ledger
            .set_limit("tenant", ResourceKind::Compute, 3)
            .unwrap();
        let claim = QuotaClaim {
            claim_id: "claim-1".into(),
            tenant_id: "tenant".into(),
            kind: ResourceKind::Compute,
            units: 2,
        };
        ledger.reserve(claim.clone()).unwrap();
        ledger.reserve(claim.clone()).unwrap();
        assert_eq!(ledger.remaining("tenant", ResourceKind::Compute), Some(1));
        assert!(
            ledger
                .reserve(QuotaClaim {
                    claim_id: "claim-2".into(),
                    units: 2,
                    ..claim
                })
                .is_err()
        );
        assert!(ledger.release("claim-1").unwrap());
        assert_eq!(ledger.used("tenant", ResourceKind::Compute), 0);
        ledger.validate().unwrap();
    }
}
