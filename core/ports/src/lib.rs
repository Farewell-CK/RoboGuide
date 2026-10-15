#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! Transport-neutral ports owned by the DEAIOS core.

mod allocation;
mod command_delivery;
pub use command_delivery::CommandDeliveryFence;
mod diagnostics;
mod memory;
mod node_gateway;
mod node_liveness;
mod spatial_memory;
mod state_record;
mod task_satisfaction;

pub use allocation::{AllocationStateError, AllocationStateReader, AllocationStateWriter};
pub use diagnostics::{
    DiagnosticHandle, DiagnosticIdentity, DiagnosticKind, DiagnosticObserver, DiagnosticQueue,
    DiagnosticQueueSnapshot, DiagnosticQueueTicket, DiagnosticRecord, DiagnosticStage,
    NodeFactMetadata,
};
pub use memory::{MemoryCatalogError, MemoryCatalogReader, MemoryCatalogWriter};
pub use node_gateway::{NodeGateway, NodeGatewayError, NodeGatewayErrorKind};
pub use node_liveness::{
    DEFAULT_NODE_APPLICATION_ACCEPTANCE_TIMEOUT_MS, DEFAULT_NODE_HEARTBEAT_INTERVAL_MS,
    DEFAULT_NODE_LEASE_DURATION_MS, NodeLeasePolicy, NodeLeasePolicyError, NodeProtocolPolicy,
    NodeProtocolPolicyError,
};
pub use spatial_memory::{
    ArtifactBlobReader, ArtifactBlobStore, ArtifactBlobWriter, ArtifactStoreError, MapCatalogError,
    MapCatalogReader, MapCatalogWriter,
};
pub use state_record::{StateRecordError, StateRecordReader, StateRecordWriter};
pub use task_satisfaction::{
    TaskSatisfactionEvidenceReader, TaskSatisfactionEvidenceWriter, TaskSatisfactionStateError,
};

use domain::{
    CorrelationId, EventId, EventPayload, NodeHealthObservation, NodeId, NodeLivenessObservation,
    NodeStateSnapshot, TimestampMs,
};
use std::fmt::{Display, Formatter};

/// A monotonic time source injectable into core tests and runtime adapters.
pub trait Clock {
    /// Returns the current monotonic timestamp.
    fn now(&self) -> TimestampMs;
}

/// Read-only current-process ingress continuity, supplied to existing Control authority.
pub trait NodeLivenessReader {
    /// Returns fixed-size exact-session evidence snapshots; failure cannot authorize renewal.
    fn node_liveness_evidence(&self) -> Result<Vec<domain::NodeSessionLivenessEvidence>, String>;
}

/// The event sink used to make cross-module behavior inspectable.
pub trait EventSink {
    /// Appends one immutable event to the configured evidence sink.
    fn append(
        &mut self,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        causation_id: Option<&EventId>,
        payload: EventPayload,
    );
}

/// Failures exposed by the transport-neutral Shared Node State contract.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SharedStateError {
    /// A health observation referenced a node absent from Shared State.
    UnknownNode(NodeId),
    /// An observation was older than the latest accepted node observation.
    StaleObservation {
        /// Node whose observation was rejected.
        node_id: NodeId,
        /// RoboGuide-local ordering time already represented by Shared State.
        current_ordering_time: TimestampMs,
        /// Older RoboGuide-local ordering time that was rejected.
        incoming_ordering_time: TimestampMs,
    },
}

impl Display for SharedStateError {
    /// Formats a stable Shared State failure for control and adapter diagnostics.
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::UnknownNode(node_id) => write!(formatter, "shared state has no node {node_id}"),
            Self::StaleObservation {
                node_id,
                current_ordering_time,
                incoming_ordering_time,
            } => write!(
                formatter,
                "stale observation for node {node_id}: current={}ms, incoming={}ms",
                current_ordering_time.as_millis(),
                incoming_ordering_time.as_millis()
            ),
        }
    }
}

impl std::error::Error for SharedStateError {}

/// Read access to current cross-mission Shared Node State facts.
pub trait SharedNodeStateReader {
    /// Supplies an immutable source-aware projection for explicitly enabled decision gates.
    /// Legacy node-only readers provide no evidence and cannot satisfy a configured gate.
    fn decision_records(&self) -> Vec<domain::StateRecord> {
        Vec::new()
    }
    /// Returns the latest snapshot for one node, if it is currently known.
    fn node(&self, node_id: &NodeId) -> Option<&NodeStateSnapshot>;

    /// Returns all current node snapshots in deterministic node-identity order.
    fn nodes(&self) -> Vec<&NodeStateSnapshot>;
}

/// Write access for accepted registration and health observations.
pub trait SharedNodeStateWriter {
    /// Records a node snapshot unless it would replace newer health evidence.
    fn record_node(&mut self, snapshot: NodeStateSnapshot) -> Result<(), SharedStateError>;

    /// Atomically records local health and its successful-receipt reachability evidence.
    fn record_node_health(
        &mut self,
        observation: NodeHealthObservation,
    ) -> Result<(), SharedStateError>;

    /// Records a non-older RoboGuide-derived liveness observation.
    fn record_node_liveness(
        &mut self,
        node_id: &NodeId,
        observation: NodeLivenessObservation,
    ) -> Result<(), SharedStateError>;
}
