//! Change-driven persistence for explicitly configured, non-authoritative periodic Node State.

use super::*;
use serde_json::Value;
use sha2::{Digest, Sha256};

#[cfg(test)]
#[path = "telemetry_persistence_tests.rs"]
mod tests;

/// Deployment configuration for ordinary periodic telemetry. The default persists every sample.
#[derive(Clone, Debug, Default, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TelemetryPersistencePolicy {
    /// Exact payload schemas approved for filtering and their volatile JSON field names.
    schemas: BTreeMap<String, BTreeSet<String>>,
    /// Maximum age of the durable sample before persisting the next accepted observation.
    #[serde(default = "default_interval")]
    max_persist_interval_ms: u64,
}

/// Default maximum distance between persisted samples for a continuously reported channel.
const fn default_interval() -> u64 {
    30_000
}

impl TelemetryPersistencePolicy {
    /// Parses a closed startup policy and rejects unknown or authority-bearing field exclusions.
    pub fn from_json(bytes: &[u8]) -> Result<Self, String> {
        if bytes.len() > 64 * 1024 {
            return Err("telemetry persistence policy exceeds 64 KiB".into());
        }
        let policy: Self = serde_json::from_slice(bytes).map_err(|error| error.to_string())?;
        const CLOCK_FIELDS: &[&str] = &[
            "observed_at_ns",
            "received_at_ns",
            "source_stamp_ns",
            "snapshot_read_at_ns",
            "sample_started_at_ns",
            "sample_completed_at_ns",
            "age_ms",
            "sample_age_ms",
            "uptime_seconds",
            "remaining_at_publisher_ms",
        ];
        if !(1..=300_000).contains(&policy.max_persist_interval_ms)
            || policy.schemas.len() > 64
            || policy.schemas.iter().any(|(schema, fields)| {
                schema.trim().is_empty()
                    || schema.len() > 128
                    || protected_schema(schema)
                    || fields
                        .iter()
                        .any(|field| !CLOCK_FIELDS.contains(&field.as_str()))
            })
        {
            return Err("invalid telemetry schema, volatile field or persistence interval".into());
        }
        Ok(policy)
    }
}

/// Exempts protocol-defined evidence even when the deployment has no decision policy registry.
fn protected_schema(schema: &str) -> bool {
    schema.starts_with("roboguide.safe-state-observation/")
        || schema.starts_with("roboguide.world.")
        || schema == runtime::EXECUTION_PROGRESS_SCHEMA
}

/// Matches a policy's immutable source identity independently of confidence and receive time.
fn policy_matches(policy: &domain::DecisionEvidencePolicy, record: &StateRecord) -> bool {
    let key = record.key();
    policy.object == *key.object()
        && policy.channel_id == key.channel_id()
        && policy.allowed_sources.contains(key.source())
        && policy.allowed_semantics.contains(&key.semantic())
        && policy
            .payload_schema
            .as_deref()
            .is_none_or(|schema| schema == record.payload_schema())
}

/// Computes a semantic digest without altering either live evidence or its stored content digest.
fn fingerprint(record: &StateRecord, fields: &BTreeSet<String>) -> Option<[u8; 32]> {
    let mut value = record.value().clone();
    omit_clock_fields(&mut value, fields);
    serde_json::to_vec(&value)
        .ok()
        .map(|bytes| Sha256::digest(bytes).into())
}

/// Removes configured clock fields at any depth while retaining array order and all other values.
fn omit_clock_fields(value: &mut Value, fields: &BTreeSet<String>) {
    match value {
        Value::Object(entries) => {
            entries.retain(|key, _| !fields.contains(key));
            entries
                .values_mut()
                .for_each(|value| omit_clock_fields(value, fields));
        }
        Value::Array(entries) => entries
            .iter_mut()
            .for_each(|value| omit_clock_fields(value, fields)),
        _ => {}
    }
}

impl<E: EventSink + Clone> IntegrationRuntimeBridge<E> {
    /// Installs deployment-owned sampling policy before accepting a Node session.
    pub fn set_telemetry_persistence(&mut self, policy: TelemetryPersistencePolicy) {
        self.telemetry_persistence = std::sync::Arc::new(policy);
    }

    /// Preserves the latest durable sample and its original timestamps in the checkpoint.
    pub(super) fn checkpoint_state_records(&self) -> Vec<StateRecord> {
        self.state_records
            .snapshots()
            .into_iter()
            .map(|live| self.deferred_state.get(live.key()).cloned().unwrap_or(live))
            .collect()
    }

    /// Supplies exact recorded facts for authority decisions, including after policy changes.
    pub(super) fn decision_evidence_records(&self) -> Vec<StateRecord> {
        self.state_records
            .records()
            .into_iter()
            .filter(|record| !self.deferred_state.contains_key(record.key()))
            .collect()
    }

    /// Checks both configured decision consumers and Belief source policies.
    fn protects_state_observation(&self, record: &StateRecord) -> bool {
        self.control
            .decision_policy_registry()
            .is_some_and(|registry| {
                registry
                    .gates
                    .values()
                    .any(|gate| policy_matches(&gate.policy, record))
                    || registry
                        .scheduling_costs
                        .values()
                        .any(|cost| policy_matches(&cost.policy, record))
            })
            || self
                .control
                .belief_policy_registry()
                .is_some_and(|registry| {
                    registry
                        .policies
                        .values()
                        .any(|belief| policy_matches(&belief.admission, record))
                })
    }

    /// Accepts a checkpoint/event update, or remembers the prior durable version of a live-only key.
    pub(super) fn retain_state_observation(
        &mut self,
        previous: &StateRecordProjection,
        record: &StateRecord,
    ) -> bool {
        let key = record.key();
        let Some(fields) = self
            .telemetry_persistence
            .schemas
            .get(record.payload_schema())
        else {
            self.deferred_state.remove(key);
            return true;
        };
        if key.object().class() != StateObjectClass::Node
            || !matches!(
                key.semantic(),
                StateSemantic::Observed | StateSemantic::Reported
            )
            || !matches!(key.source(), StateSource::Node { .. })
            || key.channel_id() == "pilot-task-safety"
            || protected_schema(record.payload_schema())
            || self.protects_state_observation(record)
        {
            self.deferred_state.remove(key);
            return true;
        }
        let durable = self
            .deferred_state
            .get(key)
            .cloned()
            .or_else(|| previous.record(key));
        if let Some(durable) = durable
            && durable.payload_schema() == record.payload_schema()
            && durable.source_epoch() == record.source_epoch()
            && durable.valid_for_ms() == record.valid_for_ms()
            && durable.confidence_millionths() == record.confidence_millionths()
            && record
                .received_at()
                .as_millis()
                .saturating_sub(durable.received_at().as_millis())
                < self.telemetry_persistence.max_persist_interval_ms
            && fingerprint(record, fields)
                .is_some_and(|digest| fingerprint(&durable, fields) == Some(digest))
        {
            self.deferred_state.entry(key.clone()).or_insert(durable);
            return false;
        }
        self.deferred_state.remove(key);
        true
    }
}
