//! Durable tenant/resource quota accounting kept separate from physical reservations.

use domain::{ResourceId, ResourceKind, ResourceQuotaLimit};

/// One resource demand is bounded by the domain's u32 capacity declaration.
const MAX_RESOURCE_DEMAND_UNITS: u64 = u32::MAX as u64;

/// Compatibility name for the domain's canonical tenant quota limit.
pub type QuotaLimit = ResourceQuotaLimit;

/// Read-only usage for one tenant/category, including commitments with no configured limit.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct QuotaUsage {
    /// Tenant charged for the usage.
    pub tenant_id: String,
    /// Resource category charged.
    pub kind: ResourceKind,
    /// Current committed logical units.
    pub used_units: u64,
    /// Explicit maximum, absent until a policy configures one.
    pub max_units: Option<u64>,
    /// Remaining configured units, absent when no policy exists.
    pub remaining_units: Option<u64>,
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
    /// Exact Control reservation, absent on legacy/operator-only logical claims.
    #[serde(default)]
    pub resource_id: Option<ResourceId>,
    /// Exact quantitative parent allocation; absent legacy claims retain resource-wide identity.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub allocation_id: Option<String>,
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
        let used = self.used(&tenant_id, kind);
        if used > max_units {
            return Err(QuotaError::Exceeded {
                limit: max_units,
                used,
                requested: 0,
            });
        }
        let limit = ResourceQuotaLimit {
            tenant_id,
            kind,
            max_units,
        };
        if let Some(existing) = self
            .limits
            .iter_mut()
            .find(|existing| existing.tenant_id == limit.tenant_id && existing.kind == limit.kind)
        {
            *existing = limit;
        } else {
            self.limits.push(limit);
            self.limits.sort_by(|left, right| {
                (&left.tenant_id, left.kind).cmp(&(&right.tenant_id, right.kind))
            });
        }
        Ok(())
    }

    /// Replaces all policy limits only when retained claims remain within every new ceiling.
    pub(crate) fn replace_limits(
        &mut self,
        limits: &[ResourceQuotaLimit],
    ) -> Result<(), QuotaError> {
        let mut candidate = Self {
            limits: Vec::new(),
            claims: self.claims.clone(),
        };
        for limit in limits {
            candidate.set_limit(limit.tenant_id.clone(), limit.kind, limit.max_units)?;
        }
        self.limits = candidate.limits;
        Ok(())
    }

    /// Adds one idempotent claim only when the configured logical quota remains available.
    pub fn reserve(&mut self, claim: QuotaClaim) -> Result<(), QuotaError> {
        validate_claim(&claim)?;
        if self
            .claims
            .iter()
            .any(|item| item.claim_id == claim.claim_id)
        {
            return self.track(claim);
        }
        if self
            .limits
            .iter()
            .find(|item| item.tenant_id == claim.tenant_id && item.kind == claim.kind)
            .is_none()
        {
            return Err(QuotaError::LimitMissing);
        }
        self.track(claim)
    }

    /// Tracks one actual resource reservation and enforces a configured tenant limit if present.
    pub(crate) fn track(&mut self, claim: QuotaClaim) -> Result<(), QuotaError> {
        validate_claim(&claim)?;
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
        if let Some(limit) = self
            .limits
            .iter()
            .find(|item| item.tenant_id == claim.tenant_id && item.kind == claim.kind)
        {
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
        }
        self.claims.push(claim);
        self.claims
            .sort_by(|left, right| left.claim_id.cmp(&right.claim_id));
        Ok(())
    }

    /// Tracks one resource-backed reservation, retaining the largest demand while it stays held.
    pub(crate) fn track_resource(&mut self, claim: QuotaClaim) -> Result<(), QuotaError> {
        if claim.resource_id.is_none() {
            return Err(QuotaError::Invalid(
                "resource claim lacks its exact reservation identity".into(),
            ));
        }
        validate_claim(&claim)?;
        if claim.allocation_id.is_some() {
            return self.track(claim);
        }
        if let Some(index) = self
            .claims
            .iter()
            .position(|item| item.claim_id == claim.claim_id)
        {
            let existing = &self.claims[index];
            if existing.tenant_id != claim.tenant_id
                || existing.kind != claim.kind
                || existing.resource_id != claim.resource_id
            {
                return Err(QuotaError::ClaimConflict);
            }
            if claim.units <= existing.units {
                return Ok(());
            }
            let previous = existing.units;
            let limit = self
                .limits
                .iter()
                .find(|item| item.tenant_id == claim.tenant_id && item.kind == claim.kind);
            if let Some(limit) = limit {
                let used_without_claim = self
                    .used(&claim.tenant_id, claim.kind)
                    .saturating_sub(previous);
                if used_without_claim
                    .checked_add(claim.units)
                    .is_none_or(|total| total > limit.max_units)
                {
                    return Err(QuotaError::Exceeded {
                        limit: limit.max_units,
                        used: used_without_claim,
                        requested: claim.units,
                    });
                }
            }
            self.claims[index].units = claim.units;
            return Ok(());
        }
        self.track(claim)
    }

    /// Removes one exact resource-backed claim when Control releases its reservation.
    pub(crate) fn release_resource(&mut self, resource_id: &str) {
        self.claims.retain(|claim| {
            claim.allocation_id.is_some()
                || claim.resource_id.as_ref().map(ResourceId::as_str) != Some(resource_id)
        });
    }

    /// Releases one exact quantitative allocation, never another owner of the same resource.
    pub(crate) fn release_allocation(&mut self, allocation_id: &str) {
        self.claims
            .retain(|claim| claim.allocation_id.as_deref() != Some(allocation_id));
    }

    /// Returns whether one resource identity has retained logical quota evidence.
    pub(crate) fn has_resource_claim(&self, resource_id: &str) -> bool {
        self.claims
            .iter()
            .any(|claim| claim.resource_id.as_ref().map(ResourceId::as_str) == Some(resource_id))
    }

    /// Returns all claims for exact checkpoint-to-reservation validation.
    pub(crate) fn claims(&self) -> &[QuotaClaim] {
        &self.claims
    }

    /// Returns whether a tenant/category limit is explicitly configured.
    pub(crate) fn has_limit(&self, tenant_id: &str, kind: ResourceKind) -> bool {
        self.limits
            .iter()
            .any(|limit| limit.tenant_id == tenant_id && limit.kind == kind)
    }

    /// Returns all configured limits in deterministic order.
    pub fn limits(&self) -> &[ResourceQuotaLimit] {
        &self.limits
    }

    /// Lists every configured or currently used tenant/category pair deterministically.
    pub fn usage(&self) -> Vec<QuotaUsage> {
        let mut keys = std::collections::BTreeSet::new();
        keys.extend(
            self.limits
                .iter()
                .map(|limit| (limit.tenant_id.clone(), limit.kind)),
        );
        keys.extend(
            self.claims
                .iter()
                .map(|claim| (claim.tenant_id.clone(), claim.kind)),
        );
        keys.into_iter()
            .map(|(tenant_id, kind)| {
                let used_units = self.used(&tenant_id, kind);
                let max_units = self
                    .limits
                    .iter()
                    .find(|limit| limit.tenant_id == tenant_id && limit.kind == kind)
                    .map(|limit| limit.max_units);
                QuotaUsage {
                    tenant_id,
                    kind,
                    used_units,
                    max_units,
                    remaining_units: max_units.map(|limit| limit.saturating_sub(used_units)),
                }
            })
            .collect()
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
            .fold(0, |total, claim| total.saturating_add(claim.units))
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
        if self
            .limits
            .windows(2)
            .any(|pair| (&pair[0].tenant_id, pair[0].kind) >= (&pair[1].tenant_id, pair[1].kind))
            || self
                .claims
                .windows(2)
                .any(|pair| pair[0].claim_id >= pair[1].claim_id)
        {
            return Err(QuotaError::Invalid(
                "checkpointed quota entries are duplicated or not canonical".into(),
            ));
        }
        let mut checked = Self::new();
        let mut resource_ids = std::collections::BTreeMap::new();
        for limit in &self.limits {
            checked.set_limit(limit.tenant_id.clone(), limit.kind, limit.max_units)?;
        }
        for claim in &self.claims {
            if let Some(resource_id) = &claim.resource_id
                && resource_ids
                    .insert(resource_id.as_str(), claim.allocation_id.is_some())
                    .is_some_and(|shared| !shared || claim.allocation_id.is_none())
            {
                return Err(QuotaError::Invalid(
                    "duplicate resource-backed quota claim".into(),
                ));
            }
            checked.track(claim.clone())?;
        }
        Ok(())
    }
}

/// Gives a Control resource one stable ledger identity independent of its Task or Role owner.
pub(crate) fn resource_claim_id(resource_id: &str) -> String {
    format!("control-resource:{resource_id}")
}

/// Validates typed provenance while keeping legacy claims independent of reservation authority.
fn validate_claim(claim: &QuotaClaim) -> Result<(), QuotaError> {
    validate_text(&claim.claim_id, "claim_id")?;
    validate_text(&claim.tenant_id, "tenant_id")?;
    if claim.units == 0 || claim.units > MAX_RESOURCE_DEMAND_UNITS {
        return Err(QuotaError::Invalid(
            "claim units are outside their bound".into(),
        ));
    }
    if claim.allocation_id.as_ref().is_some_and(|id| {
        claim.resource_id.is_none()
            || claim.kind != ResourceKind::Compute
            || &claim.claim_id != id
            || !id.strip_prefix("allocation:sha256:").is_some_and(|hex| {
                hex.len() == 64
                    && hex
                        .bytes()
                        .all(|value| value.is_ascii_digit() || (b'a'..=b'f').contains(&value))
            })
    }) || claim.allocation_id.is_none()
        && claim
            .resource_id
            .as_ref()
            .is_some_and(|resource_id| claim.claim_id != resource_claim_id(resource_id.as_str()))
    {
        return Err(QuotaError::Invalid(
            "resource quota claim identity does not match its reservation".into(),
        ));
    }
    Ok(())
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
            allocation_id: None,
            claim_id: "claim-1".into(),
            tenant_id: "tenant".into(),
            kind: ResourceKind::Compute,
            units: 2,
            resource_id: None,
        };
        ledger.reserve(claim.clone()).unwrap();
        ledger.reserve(claim.clone()).unwrap();
        assert_eq!(ledger.remaining("tenant", ResourceKind::Compute), Some(1));
        assert!(
            ledger
                .reserve(QuotaClaim {
                    allocation_id: None,
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

    #[test]
    fn tracked_reservations_are_counted_before_a_limit_is_installed() {
        let mut ledger = QuotaLedger::new();
        let claim = QuotaClaim {
            allocation_id: None,
            claim_id: resource_claim_id("resource-1"),
            tenant_id: "tenant".into(),
            kind: ResourceKind::Compute,
            units: 2,
            resource_id: Some(ResourceId::new("resource-1").unwrap()),
        };
        ledger.track(claim.clone()).unwrap();
        ledger
            .set_limit("tenant", ResourceKind::Compute, 3)
            .unwrap();
        assert!(matches!(
            ledger.track(QuotaClaim {
                allocation_id: None,
                claim_id: resource_claim_id("resource-2"),
                units: 2,
                resource_id: Some(ResourceId::new("resource-2").unwrap()),
                ..claim
            }),
            Err(QuotaError::Exceeded {
                limit: 3,
                used: 2,
                requested: 2
            })
        ));
        ledger.release_resource("resource-1");
        assert_eq!(ledger.remaining("tenant", ResourceKind::Compute), Some(3));
    }

    #[test]
    fn usage_view_includes_existing_commitments_before_a_limit_is_configured() {
        let mut ledger = QuotaLedger::new();
        ledger
            .track(QuotaClaim {
                allocation_id: None,
                claim_id: resource_claim_id("resource-without-policy"),
                tenant_id: "tenant-a".into(),
                kind: ResourceKind::Compute,
                units: 3,
                resource_id: Some(ResourceId::new("resource-without-policy").unwrap()),
            })
            .unwrap();
        assert_eq!(
            ledger.usage(),
            vec![QuotaUsage {
                tenant_id: "tenant-a".into(),
                kind: ResourceKind::Compute,
                used_units: 3,
                max_units: None,
                remaining_units: None,
            }]
        );
    }

    #[test]
    fn restored_ledger_rejects_duplicate_claim_and_limit_identities() {
        let claim = QuotaClaim {
            allocation_id: None,
            claim_id: "claim-1".into(),
            tenant_id: "tenant".into(),
            kind: ResourceKind::Compute,
            units: 1,
            resource_id: None,
        };
        let mut duplicate_claims = QuotaLedger::new();
        duplicate_claims.claims = vec![claim.clone(), claim];
        assert!(duplicate_claims.validate().is_err());

        let limit = ResourceQuotaLimit {
            tenant_id: "tenant".into(),
            kind: ResourceKind::Compute,
            max_units: 2,
        };
        let mut duplicate_limits = QuotaLedger::new();
        duplicate_limits.limits = vec![limit.clone(), limit];
        assert!(duplicate_limits.validate().is_err());
    }
}
