//! Explicit planning-only cost sources, unavailable measurements and immutable decision provenance.

use crate::{
    DecisionEvidencePolicy, DecisionKind, EvidenceDisposition, NodeId, StateRecord, TimestampMs,
};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

/// Closed payload interpreted only by an explicitly authorized scheduling source policy.
pub const SCHEDULING_METRICS_SCHEMA: &str = "roboguide.scheduling-metrics/v0.1";

/// Independent observed metrics; absent measurements never become zero or hardware capabilities.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SchedulingMetrics {
    /// Exact interpretation selected by the source policy.
    pub schema_version: String,
    /// Observed queue delay, if the provider can measure its own queue.
    pub queue_delay_ms: Option<u64>,
    /// Source-attributed route estimate, if available.
    pub travel_distance_um: Option<u64>,
    /// Source-attributed traffic estimate, if available.
    pub traffic_delay_ms: Option<u64>,
    /// Observed load in millionths of one declared CPU capacity unit.
    pub load_millionths: Option<u64>,
    /// Actually observed available GPU memory; absence never declares a GPU.
    pub gpu_memory_bytes: Option<u64>,
}

/// Typed metric keys avoid arbitrary JSON expressions in scheduling policy.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SchedulingMetric {
    /// Queue delay in milliseconds.
    QueueDelayMs,
    /// Route length in micrometers.
    TravelDistanceUm,
    /// Traffic delay in milliseconds.
    TrafficDelayMs,
    /// Source load in millionths.
    LoadMillionths,
    /// Available GPU memory in bytes.
    GpuMemoryBytes,
}

/// Explicit units and bounds for one weighted integer cost term.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SchedulingCostTerm {
    /// Actual source field to consume.
    pub metric: SchedulingMetric,
    /// Positive integer weight after unit normalization.
    pub weight: u32,
    /// Positive source units per one cost unit.
    pub units_per_cost: u64,
    /// Explicit maximum admitted source value, never inferred as capacity.
    pub maximum: u64,
    /// Higher measurements reduce cost when true, for example available GPU memory.
    pub prefer_higher: bool,
}

/// Deployment-owned admission for one candidate's planning costs, separate from eligibility gates.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SchedulingCostPolicy {
    /// Owning immutable Mission tenant.
    pub tenant_id: String,
    /// Retained disabled policies revoke ordering permission immediately.
    pub enabled: bool,
    /// Exact configured candidate; the Core never invents an inventory.
    pub node_id: NodeId,
    /// Original source, schema, TTL, confidence and conflict admission.
    pub policy: DecisionEvidencePolicy,
    /// Explicit bounded metric normalization and preference.
    pub terms: Vec<SchedulingCostTerm>,
}

impl SchedulingCostPolicy {
    /// Rejects implicit sources, ambiguous units, repeated metrics and overflowing aggregate scores.
    pub fn validate(&self) -> Result<(), String> {
        self.policy.validate().map_err(|error| error.to_string())?;
        if self.tenant_id.trim().is_empty()
            || self.tenant_id.len() > 128
            || self.policy.policy_id.len() > 128
            || self.policy.payload_schema.as_deref() != Some(SCHEDULING_METRICS_SCHEMA)
            || self.policy.allowed_decisions != BTreeSet::from([DecisionKind::Schedule])
            || self.terms.is_empty()
            || self.terms.len() > 5
            || self
                .policy
                .allowed_sources
                .iter()
                .any(|source| source.node_id() != Some(&self.node_id))
        {
            return Err(
                "scheduling cost source, tenant or explicit interpretation is invalid".into(),
            );
        }
        let mut metrics = BTreeSet::new();
        let mut maximum_score = 0_u64;
        for term in &self.terms {
            if !metrics.insert(term.metric)
                || term.weight == 0
                || term.weight > 1_000_000
                || term.units_per_cost == 0
                || term.maximum == 0
                || term.maximum > 1 << 50
            {
                return Err("scheduling cost metric, weight, unit or bound is invalid".into());
            }
            maximum_score = (term.maximum / term.units_per_cost)
                .checked_mul(u64::from(term.weight))
                .and_then(|cost| maximum_score.checked_add(cost))
                .ok_or("scheduling cost aggregate exceeds its integer bound")?;
        }
        Ok(())
    }

    /// Scores only complete actual required observations; unavailable or out-of-range stays unknown.
    pub fn score(&self, metrics: &SchedulingMetrics) -> Result<u64, SchedulingCostFailure> {
        if metrics.schema_version != SCHEDULING_METRICS_SCHEMA {
            return Err(SchedulingCostFailure::InvalidPayload);
        }
        let mut total = 0_u64;
        for term in &self.terms {
            let value = match term.metric {
                SchedulingMetric::QueueDelayMs => metrics.queue_delay_ms,
                SchedulingMetric::TravelDistanceUm => metrics.travel_distance_um,
                SchedulingMetric::TrafficDelayMs => metrics.traffic_delay_ms,
                SchedulingMetric::LoadMillionths => metrics.load_millionths,
                SchedulingMetric::GpuMemoryBytes => metrics.gpu_memory_bytes,
            }
            .ok_or(SchedulingCostFailure::MetricUnavailable)?;
            if value > term.maximum {
                return Err(SchedulingCostFailure::MetricOutOfRange);
            }
            let normalized = if term.prefer_higher {
                term.maximum - value
            } else {
                value
            } / term.units_per_cost;
            total = normalized
                .checked_mul(u64::from(term.weight))
                .and_then(|cost| total.checked_add(cost))
                .ok_or(SchedulingCostFailure::MetricOutOfRange)?;
        }
        Ok(total)
    }
}

/// Why a selected source cannot supply an actual numeric ordering cost.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum SchedulingCostFailure {
    /// Original source payload cannot be decoded under the exact declared schema.
    InvalidPayload,
    /// At least one configured weighted metric has no actual measurement.
    MetricUnavailable,
    /// A measured value exceeds the explicit policy bound.
    MetricOutOfRange,
}

/// Complete planning observation retained with both normal and recovery selection evidence.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SchedulingCostAssessment {
    /// Exact policy registry interpretation at the decision time.
    pub registry_digest: String,
    /// Complete original admission and normalization policy.
    pub policy: SchedulingCostPolicy,
    /// Source admission result; no unavailable evidence becomes affirmative authority.
    pub disposition: EvidenceDisposition,
    /// Payload or metric interpretation failure, separate from source admission.
    pub failure: Option<SchedulingCostFailure>,
    /// Complete original attributed State evidence, if admissible.
    pub record: Option<StateRecord>,
    /// Numeric ordering cost only when every configured metric was observed.
    pub score: Option<u64>,
    /// Original Controller receive-relative decision clock.
    pub decided_at: TimestampMs,
}

impl SchedulingCostAssessment {
    /// Recomputes original accepted interpretation without refreshing receive clocks or missing values.
    pub fn validate(&self) -> Result<(), String> {
        self.policy.validate()?;
        if !self.policy.enabled
            || self.registry_digest.len() != 71
            || !self
                .registry_digest
                .strip_prefix("sha256:")
                .is_some_and(|hex| {
                    hex.bytes()
                        .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
                })
        {
            return Err("scheduling cost original policy identity is invalid".into());
        }
        if let Some(record) = &self.record {
            let admitted = self.policy.policy.evaluate(
                std::slice::from_ref(record),
                DecisionKind::Schedule,
                self.decided_at,
            );
            let scored = serde_json::from_value::<SchedulingMetrics>(record.value().clone())
                .map_err(|_| SchedulingCostFailure::InvalidPayload)
                .and_then(|metrics| self.policy.score(&metrics));
            if self.disposition != EvidenceDisposition::Accepted
                || admitted.disposition != EvidenceDisposition::Accepted
                || record.key().source().node_id() != Some(&self.policy.node_id)
                || record.source_epoch().is_none()
                || self.score != scored.as_ref().ok().copied()
                || self.failure != scored.err()
            {
                return Err("scheduling cost original source or score changed".into());
            }
        } else if self.disposition == EvidenceDisposition::Accepted
            || self.score.is_some()
            || self.failure.is_some()
        {
            return Err("scheduling cost lacks original admitted source".into());
        }
        Ok(())
    }
}
