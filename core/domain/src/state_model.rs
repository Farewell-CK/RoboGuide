//! Source-aware State values shared by adapters, State projections, and query facades.
//!
//! A record is one attributed observation or declared view. It never claims to be a global
//! truth, and records from independent sources remain independently addressable.

use crate::{DomainError, LocalSystemId, NodeId, TimestampMs};
use serde::{Deserialize, Deserializer, Serialize};
use std::fmt::{Display, Formatter};

/// Version identifier for the first source-aware State record contract.
pub const STATE_RECORD_SCHEMA_V0_1: &str = "roboguide.state-record/v0.1";

/// Maximum encoded JSON payload accepted for one State record.
pub const MAX_STATE_PAYLOAD_BYTES: usize = 64 * 1024;

/// Classifies the semantic object described by a State record.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum StateObjectClass {
    /// A registered node or one of its locally owned subsystems.
    Node,
    /// An environment, place, person, object, or other world entity.
    World,
    /// A RoboGuide-owned Mission, Task, Group, execution, or projection.
    RoboGuide,
}

/// Identifies what epistemic or commitment meaning a State record carries.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum StateSemantic {
    /// Intended state declared by Mission or another owning authority.
    Desired,
    /// State committed by the Control Plane through an authoritative lifecycle.
    Committed,
    /// State explicitly reported by a node or local system.
    Reported,
    /// State directly observed by a node or RoboGuide component.
    Observed,
    /// Deterministic state projected from accepted evidence.
    Derived,
    /// An explicit uncertain interpretation produced by a named belief component.
    Belief,
}

/// A stable semantic object reference independent of transport or physical adapter handles.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
pub struct StateObjectRef {
    /// Broad ownership class of the referenced object.
    class: StateObjectClass,
    /// Domain-specific object category, such as `node`, `hazard`, or `execution`.
    object_type: String,
    /// Stable identity within the category's documented namespace.
    object_id: String,
}

impl StateObjectRef {
    /// Creates a semantic object reference with non-empty type and identity components.
    pub fn new(
        class: StateObjectClass,
        object_type: impl Into<String>,
        object_id: impl Into<String>,
    ) -> Result<Self, DomainError> {
        let object_type = object_type.into();
        let object_id = object_id.into();
        reject_empty(&object_type, "state object type")?;
        reject_empty(&object_id, "state object id")?;
        Ok(Self {
            class,
            object_type,
            object_id,
        })
    }

    /// Returns the broad ownership class.
    pub const fn class(&self) -> StateObjectClass {
        self.class
    }

    /// Returns the domain-specific object category.
    pub fn object_type(&self) -> &str {
        &self.object_type
    }

    /// Returns the stable object identity.
    pub fn object_id(&self) -> &str {
        &self.object_id
    }
}

/// Attributes one State record to its actual producer.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
#[serde(tag = "owner", rename_all = "snake_case")]
pub enum StateSource {
    /// A configured local system published the record through one node.
    Node {
        /// Node that owns the publishing connection.
        node_id: NodeId,
        /// Local system whose adapter produced the value.
        local_system_id: LocalSystemId,
    },
    /// A named RoboGuide component produced the record.
    RoboGuide {
        /// Stable component or projector identity.
        component: String,
    },
}

impl StateSource {
    /// Creates an explicitly named RoboGuide source.
    pub fn roboguide(component: impl Into<String>) -> Result<Self, DomainError> {
        let component = component.into();
        reject_empty(&component, "state source component")?;
        Ok(Self::RoboGuide { component })
    }

    /// Returns the node source identity when the producer is node-owned.
    pub const fn node_id(&self) -> Option<&NodeId> {
        match self {
            Self::Node { node_id, .. } => Some(node_id),
            Self::RoboGuide { .. } => None,
        }
    }

    /// Returns the local-system source identity when the producer is node-owned.
    pub const fn local_system_id(&self) -> Option<&LocalSystemId> {
        match self {
            Self::Node {
                local_system_id, ..
            } => Some(local_system_id),
            Self::RoboGuide { .. } => None,
        }
    }
}

impl Display for StateSource {
    /// Writes a stable attributed source identity for logs and projection keys.
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Node {
                node_id,
                local_system_id,
            } => write!(formatter, "node:{node_id}/{local_system_id}"),
            Self::RoboGuide { component } => write!(formatter, "roboguide:{component}"),
        }
    }
}

/// Declares one periodically sampled State channel exposed by a local system.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct StateExportDescriptor {
    /// Node-wide export identity used to correlate observations with registration.
    export_id: String,
    /// Local system that owns the adapter and source value.
    local_system_id: LocalSystemId,
    /// Semantic object described by every value on this channel.
    object: StateObjectRef,
    /// Source meaning; node exports may only be Reported or Observed.
    semantic: StateSemantic,
    /// Versioned schema that interprets the JSON payload.
    payload_schema: String,
    /// Maximum validity period after RoboGuide receive time.
    valid_for_ms: u64,
}

impl StateExportDescriptor {
    /// Creates a node-owned State export and enforces the node publication authority boundary.
    pub fn new(
        export_id: impl Into<String>,
        local_system_id: LocalSystemId,
        object: StateObjectRef,
        semantic: StateSemantic,
        payload_schema: impl Into<String>,
        valid_for_ms: u64,
    ) -> Result<Self, DomainError> {
        let export_id = export_id.into();
        let payload_schema = payload_schema.into();
        reject_empty(&export_id, "state export id")?;
        reject_empty(&payload_schema, "state payload schema")?;
        if !matches!(
            object.class(),
            StateObjectClass::Node | StateObjectClass::World
        ) {
            return Err(invalid_state(
                "node state exports may reference only Node or World objects",
            ));
        }
        if !matches!(semantic, StateSemantic::Reported | StateSemantic::Observed) {
            return Err(invalid_state(
                "node state exports may publish only Reported or Observed semantics",
            ));
        }
        if valid_for_ms == 0 {
            return Err(invalid_state("state export valid_for_ms must be positive"));
        }
        Ok(Self {
            export_id,
            local_system_id,
            object,
            semantic,
            payload_schema,
            valid_for_ms,
        })
    }

    /// Returns the node-wide export identity.
    pub fn export_id(&self) -> &str {
        &self.export_id
    }

    /// Returns the local system that owns the export.
    pub const fn local_system_id(&self) -> &LocalSystemId {
        &self.local_system_id
    }

    /// Returns the semantic object described by the export.
    pub const fn object(&self) -> &StateObjectRef {
        &self.object
    }

    /// Returns the publication semantic.
    pub const fn semantic(&self) -> StateSemantic {
        self.semantic
    }

    /// Returns the JSON payload schema identifier.
    pub fn payload_schema(&self) -> &str {
        &self.payload_schema
    }

    /// Returns the receive-relative validity period.
    pub const fn valid_for_ms(&self) -> u64 {
        self.valid_for_ms
    }
}

/// Deterministic identity of one independently attributed State channel.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
pub struct StateRecordKey {
    /// Semantic object described by the record.
    object: StateObjectRef,
    /// Epistemic or commitment meaning of the record.
    semantic: StateSemantic,
    /// Attributed producer of the record.
    source: StateSource,
    /// Source-local channel identity.
    channel_id: String,
}

impl StateRecordKey {
    /// Returns the referenced object.
    pub const fn object(&self) -> &StateObjectRef {
        &self.object
    }

    /// Returns the record semantic.
    pub const fn semantic(&self) -> StateSemantic {
        self.semantic
    }

    /// Returns the attributed producer.
    pub const fn source(&self) -> &StateSource {
        &self.source
    }

    /// Returns the source-local channel identity.
    pub fn channel_id(&self) -> &str {
        &self.channel_id
    }
}

/// One bounded source-aware State value ordered by RoboGuide receive time.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct StateRecord {
    /// Contract used to interpret this record envelope.
    schema: String,
    /// Independently attributed channel identity.
    key: StateRecordKey,
    /// Versioned schema used to interpret the JSON value.
    payload_schema: String,
    /// Bounded structured value; high-rate streams and artifact bytes do not belong here.
    value: serde_json::Value,
    /// Optional timestamp in the source's local clock domain.
    source_observed_at: Option<TimestampMs>,
    /// RoboGuide-local receive time used for projection ordering and freshness.
    received_at: TimestampMs,
    /// Receive-relative validity period; stale records remain inspectable.
    valid_for_ms: u64,
    /// Optional confidence in millionths, constrained to zero through one million.
    confidence_millionths: Option<u32>,
    /// Optional source-session epoch used only to disambiguate equal receive times.
    #[serde(skip_serializing_if = "Option::is_none")]
    source_epoch: Option<String>,
    /// Monotonic sequence within the node management session or named source.
    sequence: u64,
}

impl StateRecord {
    /// Creates a validated bounded State record without fusing it with other sources.
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        object: StateObjectRef,
        semantic: StateSemantic,
        source: StateSource,
        channel_id: impl Into<String>,
        payload_schema: impl Into<String>,
        value: serde_json::Value,
        source_observed_at: Option<TimestampMs>,
        received_at: TimestampMs,
        valid_for_ms: u64,
        confidence_millionths: Option<u32>,
        sequence: u64,
    ) -> Result<Self, DomainError> {
        Self::new_with_source_epoch(
            object,
            semantic,
            source,
            channel_id,
            payload_schema,
            value,
            source_observed_at,
            received_at,
            valid_for_ms,
            confidence_millionths,
            None,
            sequence,
        )
    }

    /// Creates a record carrying one source-session epoch for equal-time ordering.
    #[allow(clippy::too_many_arguments)]
    pub fn new_with_source_epoch(
        object: StateObjectRef,
        semantic: StateSemantic,
        source: StateSource,
        channel_id: impl Into<String>,
        payload_schema: impl Into<String>,
        value: serde_json::Value,
        source_observed_at: Option<TimestampMs>,
        received_at: TimestampMs,
        valid_for_ms: u64,
        confidence_millionths: Option<u32>,
        source_epoch: Option<String>,
        sequence: u64,
    ) -> Result<Self, DomainError> {
        let channel_id = channel_id.into();
        let payload_schema = payload_schema.into();
        reject_empty(&channel_id, "state channel id")?;
        reject_empty(&payload_schema, "state payload schema")?;
        if valid_for_ms == 0 {
            return Err(invalid_state("state valid_for_ms must be positive"));
        }
        if confidence_millionths.is_some_and(|confidence| confidence > 1_000_000) {
            return Err(invalid_state(
                "state confidence_millionths must not exceed 1000000",
            ));
        }
        if source_epoch
            .as_deref()
            .is_some_and(|epoch| epoch.trim().is_empty())
        {
            return Err(invalid_state("state source epoch must not be blank"));
        }
        if source_epoch.is_some() && !matches!(source, StateSource::Node { .. }) {
            return Err(invalid_state(
                "only node-sourced state may carry a source-session epoch",
            ));
        }
        let payload_size = serde_json::to_vec(&value)
            .map_err(|error| invalid_state(format!("state payload is not encodable: {error}")))?
            .len();
        if payload_size > MAX_STATE_PAYLOAD_BYTES {
            return Err(invalid_state(format!(
                "state payload exceeds {MAX_STATE_PAYLOAD_BYTES} bytes"
            )));
        }
        if matches!(source, StateSource::Node { .. })
            && (!matches!(
                object.class(),
                StateObjectClass::Node | StateObjectClass::World
            ) || !matches!(semantic, StateSemantic::Reported | StateSemantic::Observed))
        {
            return Err(invalid_state(
                "node sources may publish only Reported/Observed Node or World state",
            ));
        }
        Ok(Self {
            schema: STATE_RECORD_SCHEMA_V0_1.to_string(),
            key: StateRecordKey {
                object,
                semantic,
                source,
                channel_id,
            },
            payload_schema,
            value,
            source_observed_at,
            received_at,
            valid_for_ms,
            confidence_millionths,
            source_epoch,
            sequence,
        })
    }

    /// Returns the independently attributed projection key.
    pub const fn key(&self) -> &StateRecordKey {
        &self.key
    }

    /// Returns the payload schema identifier.
    pub fn payload_schema(&self) -> &str {
        &self.payload_schema
    }

    /// Returns the bounded structured value.
    pub const fn value(&self) -> &serde_json::Value {
        &self.value
    }

    /// Returns the optional source-local timestamp without comparing it across sources.
    pub const fn source_observed_at(&self) -> Option<TimestampMs> {
        self.source_observed_at
    }

    /// Returns the RoboGuide-local receive timestamp.
    pub const fn received_at(&self) -> TimestampMs {
        self.received_at
    }

    /// Returns the receive-relative validity period.
    pub const fn valid_for_ms(&self) -> u64 {
        self.valid_for_ms
    }

    /// Returns whether the record is stale at one RoboGuide-local instant.
    pub fn is_stale_at(&self, now: TimestampMs) -> bool {
        now.as_millis()
            >= self
                .received_at
                .as_millis()
                .saturating_add(self.valid_for_ms)
    }

    /// Returns the optional fixed-point confidence.
    pub const fn confidence_millionths(&self) -> Option<u32> {
        self.confidence_millionths
    }

    /// Returns the optional source-session epoch used for equal-time ordering.
    pub fn source_epoch(&self) -> Option<&str> {
        self.source_epoch.as_deref()
    }

    /// Returns the source-local ordering sequence.
    pub const fn sequence(&self) -> u64 {
        self.sequence
    }
}

/// Rejects a blank semantic string through the State-specific error category.
fn reject_empty(value: &str, kind: &str) -> Result<(), DomainError> {
    if value.trim().is_empty() {
        return Err(invalid_state(format!("{kind} must not be empty")));
    }
    Ok(())
}

/// Builds one stable State invariant error.
fn invalid_state(reason: impl Into<String>) -> DomainError {
    DomainError::InvalidState {
        reason: reason.into(),
    }
}

/// Deserializes a State record and rechecks constructor invariants.
impl<'de> Deserialize<'de> for StateRecord {
    /// Restores only v0.1 records satisfying current bounds and authority rules.
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        #[derive(Deserialize)]
        struct Wire {
            /// Envelope schema.
            schema: String,
            /// Stable projection key.
            key: StateRecordKey,
            /// Value schema.
            payload_schema: String,
            /// Structured bounded value.
            value: serde_json::Value,
            /// Source-local time.
            source_observed_at: Option<TimestampMs>,
            /// RoboGuide receive time.
            received_at: TimestampMs,
            /// Validity period.
            valid_for_ms: u64,
            /// Fixed-point confidence.
            confidence_millionths: Option<u32>,
            /// Optional source-session epoch added compatibly to v0.1.
            #[serde(default)]
            source_epoch: Option<String>,
            /// Source sequence.
            sequence: u64,
        }

        let wire = Wire::deserialize(deserializer)?;
        if wire.schema != STATE_RECORD_SCHEMA_V0_1 {
            return Err(serde::de::Error::custom("unsupported State record schema"));
        }
        Self::new_with_source_epoch(
            wire.key.object,
            wire.key.semantic,
            wire.key.source,
            wire.key.channel_id,
            wire.payload_schema,
            wire.value,
            wire.source_observed_at,
            wire.received_at,
            wire.valid_for_ms,
            wire.confidence_millionths,
            wire.source_epoch,
            wire.sequence,
        )
        .map_err(serde::de::Error::custom)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Node publications cannot impersonate Control or Belief authority.
    #[test]
    fn node_source_rejects_committed_semantic() {
        let result = StateRecord::new(
            StateObjectRef::new(StateObjectClass::Node, "node", "dog-a")
                .expect("object should be valid"),
            StateSemantic::Committed,
            StateSource::Node {
                node_id: NodeId::new("dog-a").expect("node should be valid"),
                local_system_id: LocalSystemId::new("motion").expect("system should be valid"),
            },
            "motion-state",
            "example.motion/v1",
            serde_json::json!({"moving": true}),
            None,
            TimestampMs::new(10),
            1_000,
            None,
            1,
        );
        assert!(matches!(result, Err(DomainError::InvalidState { .. })));
    }

    /// Freshness uses receive time while retaining independent source time.
    #[test]
    fn state_record_freshness_uses_receive_time() {
        let record = StateRecord::new(
            StateObjectRef::new(StateObjectClass::World, "hazard", "crossing-a")
                .expect("object should be valid"),
            StateSemantic::Observed,
            StateSource::Node {
                node_id: NodeId::new("cane-a").expect("node should be valid"),
                local_system_id: LocalSystemId::new("safety").expect("system should be valid"),
            },
            "hazards",
            "example.hazard/v1",
            serde_json::json!({"present": true}),
            Some(TimestampMs::new(50_000)),
            TimestampMs::new(100),
            20,
            Some(900_000),
            7,
        )
        .expect("record should be valid");
        assert!(!record.is_stale_at(TimestampMs::new(119)));
        assert!(record.is_stale_at(TimestampMs::new(120)));
        assert_eq!(record.source_observed_at(), Some(TimestampMs::new(50_000)));
    }
}
