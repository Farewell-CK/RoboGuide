//! Explicit source-attributed Belief derivation contracts, independent of any physical truth claim.

use crate::{DecisionEvidencePolicy, DecisionKind, StateSemantic};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

/// Closed scalar observation type; unknown and coercions never become affirmative evidence.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum BeliefScalarKind {
    /// Exact JSON Boolean.
    Boolean,
    /// Bounded explicit enum label.
    Enum,
}

/// Deployment-selected derivation algorithm over independently retained observations.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum BeliefAlgorithm {
    /// Derives one typed scalar under the admission policy's explicit conflict reducer.
    Scalar {
        /// Closed observed scalar type.
        value_kind: BeliefScalarKind,
        /// Exact bounded JSON pointer within the admitted schema.
        pointer: String,
    },
    /// Conservative covariance intersection for vectors with unknown cross-correlation.
    GaussianCi {
        /// Exact source-owned unit; no implicit conversion or confidence-to-variance mapping.
        unit: String,
        /// Exact shared coordinate frame; transforms must be supplied by a separate spatial authority.
        frame: String,
        /// Bounded state vector dimension.
        dimensions: u8,
        /// Deterministic convex-weight search subdivisions, including both endpoints.
        weight_steps: u8,
        /// Explicit squared Euclidean disagreement bound in squared units, scaled by one million.
        max_separation_squared_millionths: u64,
    },
}

/// One versioned raw-source consumer and its independently chosen derivation algorithm.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BeliefPolicy {
    /// Unique interpretation identity within the deployment registry.
    pub policy_id: String,
    /// Immutable meaning generation, incremented when any source/algorithm contract changes.
    pub revision: u64,
    /// Owning consumer namespace, never read from an untrusted observation payload.
    pub tenant_id: String,
    /// Disabled policies publish no affirmative Belief and retain their original history.
    pub enabled: bool,
    /// Minimum independently named raw sources; missing required support remains unavailable.
    pub minimum_sources: u8,
    /// Exact raw sources, object, channel, schema, confidence and original freshness admission.
    pub admission: DecisionEvidencePolicy,
    /// Explicit scalar or covariance-bearing derivation algorithm.
    pub algorithm: BeliefAlgorithm,
}

/// Administrator-owned bounded Belief producer registry; this registry alone grants no Control ownership.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BeliefPolicyRegistry {
    /// Closed contract identity.
    pub schema_version: String,
    /// Immutable deployment authority identity.
    pub registry_id: String,
    /// Expected-revision generation.
    pub revision: u64,
    /// Retained exact interpretation contracts, including disabled policies.
    pub policies: BTreeMap<String, BeliefPolicy>,
}

impl BeliefPolicyRegistry {
    /// Rejects unbounded, recursive, schema-free or implicitly interpreted source contracts.
    pub fn validate(&self) -> Result<(), String> {
        if self.schema_version != "roboguide.belief-policy-registry/v0.1"
            || self.registry_id.trim().is_empty()
            || self.registry_id.len() > 128
            || self.revision == 0
            || self.policies.len() > 16
        {
            return Err("Belief registry identity/bounds are invalid".into());
        }
        for (id, policy) in &self.policies {
            policy
                .admission
                .validate()
                .map_err(|error| error.to_string())?;
            if id != &policy.policy_id
                || id != &policy.admission.policy_id
                || id.len() > 128
                || policy.revision != policy.admission.revision
                || policy.tenant_id.trim().is_empty()
                || policy.tenant_id.len() > 128
                || policy.admission.payload_schema.is_none()
                || policy.admission.allowed_decisions
                    != [DecisionKind::BeliefFusion].into_iter().collect()
                || policy.admission.allowed_sources.len() > 8
                || policy.minimum_sources == 0
                || usize::from(policy.minimum_sources) > policy.admission.allowed_sources.len()
                || policy.admission.allowed_semantics.len() != 1
                || policy.admission.max_age_ms > 3_600_000
                || policy.admission.allowed_semantics.iter().any(|semantic| {
                    !matches!(semantic, StateSemantic::Observed | StateSemantic::Derived)
                })
                || policy
                    .admission
                    .allowed_sources
                    .iter()
                    .any(|source| source.node_id().is_none())
            {
                return Err("Belief raw source/tenant/interpretation contract is invalid".into());
            }
            match &policy.algorithm {
                BeliefAlgorithm::Scalar { pointer, .. } => crate::StateCondition::Boolean {
                    pointer: pointer.clone(),
                    value: true,
                }
                .validate()?,
                BeliefAlgorithm::GaussianCi {
                    unit,
                    frame,
                    dimensions,
                    weight_steps,
                    max_separation_squared_millionths,
                } => {
                    if unit.trim().is_empty()
                        || unit.len() > 128
                        || frame.trim().is_empty()
                        || frame.len() > 128
                        || !(1..=6).contains(dimensions)
                        || !(1..=32).contains(weight_steps)
                        || *max_separation_squared_millionths == 0
                    {
                        return Err(
                            "covariance intersection unit/frame/dimension/budget is invalid".into(),
                        );
                    }
                }
            }
        }
        Ok(())
    }

    /// Preserves every earlier source meaning and tenant boundary across explicit updates.
    pub fn validates_successor(&self, next: &Self) -> Result<(), String> {
        next.validate()?;
        if next.registry_id != self.registry_id
            || next.revision
                != self
                    .revision
                    .checked_add(1)
                    .ok_or("Belief revision exhausted")?
        {
            return Err("Belief registry expected revision conflicts".into());
        }
        for (id, old) in &self.policies {
            let new = next
                .policies
                .get(id)
                .ok_or("Belief policies must be disabled and retained")?;
            if new.tenant_id != old.tenant_id
                || new.revision < old.revision
                || new != old && new.revision == old.revision
            {
                return Err("Belief policy meaning/revision conflicts".into());
            }
        }
        Ok(())
    }
}
