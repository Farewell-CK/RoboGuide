//! Revisioned source admission and typed State conditions for owning authority consumers.

use crate::{
    CapabilityKind, DecisionEvidencePolicy, DecisionKind, EvidenceDisposition, NodeId,
    OperationRef, RoleId, StateRecord, TimestampMs,
};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

/// Closed scalar conditions; no expression interpreter or source text can grant authority.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum StateCondition {
    /// Exact Boolean state, such as an admitted hazard clear observation.
    Boolean {
        /// Bounded JSON pointer into the admitted payload.
        pointer: String,
        /// Explicit required value.
        value: bool,
    },
    /// Exact enumerated state under the configured payload schema.
    Enum {
        /// Bounded JSON pointer into the admitted payload.
        pointer: String,
        /// Explicit required enum label.
        value: String,
    },
    /// Explicit finite inclusive numeric interval; no covariance or confidence is inferred here.
    NumberInterval {
        /// Exact bounded JSON pointer into the admitted schema.
        pointer: String,
        /// Inclusive typed lower bound.
        minimum: serde_json::Number,
        /// Inclusive typed upper bound.
        maximum: serde_json::Number,
    },
}

impl StateCondition {
    /// Validates bounded pointer syntax and closed value kinds before accepting a policy.
    pub fn validate(&self) -> Result<(), String> {
        let pointer = match self {
            Self::Boolean { pointer, .. }
            | Self::Enum { pointer, .. }
            | Self::NumberInterval { pointer, .. } => pointer,
        };
        if pointer.len() > 256
            || !pointer.is_empty() && !pointer.starts_with('/')
            || pointer.contains('~')
                && pointer
                    .split('~')
                    .skip(1)
                    .any(|part| !part.starts_with('0') && !part.starts_with('1'))
        {
            return Err("State condition JSON pointer is invalid".into());
        }
        if matches!(self,Self::Enum {value,..} if value.trim().is_empty() || value.len()>128) {
            return Err("State condition enum is invalid".into());
        }
        if let Self::NumberInterval {
            minimum, maximum, ..
        } = self
        {
            let lower = minimum
                .as_f64()
                .ok_or("numeric condition lower bound is invalid")?;
            let upper = maximum
                .as_f64()
                .ok_or("numeric condition upper bound is invalid")?;
            if !lower.is_finite() || !upper.is_finite() || lower > upper {
                return Err("numeric condition interval is invalid".into());
            }
        }
        Ok(())
    }

    /// Interprets only the explicitly admitted schema's scalar, retaining unavailable values as false.
    pub fn matches(&self, record: &StateRecord) -> bool {
        match self {
            Self::Boolean { pointer, value } => {
                record
                    .value()
                    .pointer(pointer)
                    .and_then(serde_json::Value::as_bool)
                    == Some(*value)
            }
            Self::Enum { pointer, value } => {
                record
                    .value()
                    .pointer(pointer)
                    .and_then(serde_json::Value::as_str)
                    == Some(value.as_str())
            }
            Self::NumberInterval {
                pointer,
                minimum,
                maximum,
            } => record
                .value()
                .pointer(pointer)
                .and_then(serde_json::Value::as_f64)
                .is_some_and(|value| {
                    value.is_finite()
                        && minimum.as_f64().is_some_and(|lower| lower <= value)
                        && maximum.as_f64().is_some_and(|upper| value <= upper)
                }),
        }
    }
}

/// Declarative Role/operation target; IDs and hardware inventory are never embedded in Core behavior.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(deny_unknown_fields)]
pub struct DecisionGateTarget {
    /// Optional exact canonical operation.
    #[serde(default)]
    pub operation: Option<OperationRef>,
    /// Optional coarse capability kind for legacy Roles only.
    #[serde(default)]
    pub capability: Option<CapabilityKind>,
    /// Optional exact canonical capability required by a normalized Role.
    #[serde(default)]
    pub capability_contract: Option<crate::CapabilityContractRef>,
    /// Optional logical Role identity; absence applies to every eligible Role.
    #[serde(default)]
    pub role: Option<RoleId>,
}

/// One independently owned evidence gate, retaining the complete source/freshness/conflict policy.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionGate {
    /// Explicit v0.3 continuous Readiness monitoring; absence preserves the original admission-only gate.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub active_stop_timeout_ms: Option<u64>,
    /// Owning authenticated consumer tenant.
    pub tenant_id: String,
    /// Current enabled state; disabling a rule retains its history.
    pub enabled: bool,
    /// Exact immutable source and consumer admission policy.
    pub policy: DecisionEvidencePolicy,
    /// Declarative operation/Role scope.
    pub target: DecisionGateTarget,
    /// A Node-authored record must come from the candidate itself when true.
    pub candidate_source_only: bool,
    /// Exact current Node session epoch is required when true.
    pub require_current_session: bool,
    /// Explicit payload interpretation, never generic truthiness.
    pub condition: StateCondition,
}

/// Registry of versioned source contracts and authority consumers owned by deployment/Control.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionPolicyRegistry {
    /// Exact wire/checkpoint schema.
    pub schema_version: String,
    /// Immutable deployment registry identity.
    pub registry_id: String,
    /// Monotonic expected-revision update version.
    pub revision: u64,
    /// Policy IDs with full independent source and decision contracts.
    pub gates: BTreeMap<String, DecisionGate>,
    /// Explicit v0.2 planning-only cost sources; omitted to preserve original v0.1 digests.
    #[serde(default, skip_serializing_if = "BTreeMap::is_empty")]
    pub scheduling_costs: BTreeMap<String, crate::SchedulingCostPolicy>,
}

impl DecisionPolicyRegistry {
    /// Validates bounded policy contents without changing State, eligibility or ownership.
    pub fn validate(&self) -> Result<(), String> {
        if !matches!(
            self.schema_version.as_str(),
            "roboguide.decision-policy-registry/v0.1"
                | "roboguide.decision-policy-registry/v0.2"
                | "roboguide.decision-policy-registry/v0.3"
        ) || self.schema_version.ends_with("/v0.1") && !self.scheduling_costs.is_empty()
            || self.scheduling_costs.len() > 128
            || self.registry_id.trim().is_empty()
            || self.registry_id.len() > 128
            || self.revision == 0
            || self.gates.len() > 128
        {
            return Err("decision policy registry envelope is invalid".into());
        }
        let mut candidates = std::collections::BTreeSet::new();
        for (id, cost) in &self.scheduling_costs {
            cost.validate()?;
            if id != &cost.policy.policy_id
                || self.gates.contains_key(id)
                || cost.enabled && !candidates.insert((&cost.tenant_id, &cost.node_id))
            {
                return Err(
                    "scheduling cost identity or candidate has conflicting policies".into(),
                );
            }
        }
        for (id, gate) in &self.gates {
            if let Some(timeout) = gate.active_stop_timeout_ms
                && (self.schema_version != "roboguide.decision-policy-registry/v0.3"
                    || !(1..=3_600_000).contains(&timeout)
                    || !gate
                        .policy
                        .allowed_decisions
                        .contains(&DecisionKind::Readiness))
            {
                return Err("active hazard monitoring requires explicit v0.3, Readiness and a bounded stop timeout".into());
            }
            gate.policy.validate().map_err(|error| error.to_string())?;
            gate.condition.validate()?;
            if id != &gate.policy.policy_id
                || id.len() > 128
                || gate.tenant_id.trim().is_empty()
                || gate.tenant_id.len() > 128
                || gate.policy.payload_schema.is_none()
                || gate
                    .policy
                    .allowed_decisions
                    .iter()
                    .any(|kind| matches!(kind, DecisionKind::BeliefFusion | DecisionKind::Schedule))
            {
                return Err("decision gate identity/tenant/schema is invalid".into());
            }
        }
        Ok(())
    }

    /// Fences rollback, silent removal and same-revision interpretation changes.
    pub fn validates_successor(&self, next: &Self) -> Result<(), String> {
        next.validate()?;
        if self.schema_version.ends_with("/v0.3") && !next.schema_version.ends_with("/v0.3")
            || self.schema_version.ends_with("/v0.2") && next.schema_version.ends_with("/v0.1")
            || self.registry_id != next.registry_id
            || self.revision > next.revision
            || self.revision == next.revision && self != next
            || next.revision > self.revision
                && next.revision
                    != self
                        .revision
                        .checked_add(1)
                        .ok_or("policy revision exhausted")?
        {
            return Err("decision registry revision conflicts".into());
        }
        for (id, old) in &self.scheduling_costs {
            let new = next
                .scheduling_costs
                .get(id)
                .ok_or("cost policies must be retained and disabled, not removed")?;
            if old.tenant_id != new.tenant_id
                || old.node_id != new.node_id
                || old.policy.revision > new.policy.revision
                || old != new && old.policy.revision == new.policy.revision
            {
                return Err("cost policy attribution/revision conflicts".into());
            }
        }
        for (id, old) in &self.gates {
            let new = next
                .gates
                .get(id)
                .ok_or("decision gates must be retained and disabled, not removed")?;
            if old.tenant_id != new.tenant_id
                || old.policy.revision > new.policy.revision
                || old != new && old.policy.revision == new.policy.revision
            {
                return Err("decision policy attribution/revision conflicts".into());
            }
        }
        Ok(())
    }

    /// Computes a content identity over complete interpretation and source admission contracts.
    pub fn digest(&self) -> Result<String, String> {
        use sha2::{Digest, Sha256};
        Ok(format!(
            "sha256:{:x}",
            Sha256::digest(
                serde_json::to_vec(&serde_json::to_value(self).map_err(|error| error.to_string())?)
                    .map_err(|error| error.to_string())?
            )
        ))
    }
}

/// Exact selected/rejected evidence retained with an authority decision, not an independent State value.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DecisionEvidenceReceipt {
    /// Registry content identity whose interpretation admitted this result.
    pub registry_digest: String,
    /// Exact independently versioned policy identity.
    pub policy_id: String,
    /// Exact policy revision.
    pub policy_revision: u64,
    /// Owning authority consumer.
    pub decision: DecisionKind,
    /// Selected physical executor candidate, never chosen by a model.
    pub node_id: NodeId,
    /// Source admission disposition, including conflicts and absent evidence.
    pub disposition: EvidenceDisposition,
    /// Whether the explicitly typed condition passed.
    pub condition_passed: bool,
    /// Complete original selected State record when admission succeeded.
    pub record: Option<StateRecord>,
    /// Original local decision time, never refreshed by restore or lookup.
    pub decided_at: TimestampMs,
}
