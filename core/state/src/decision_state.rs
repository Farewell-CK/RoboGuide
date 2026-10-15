//! Owned immutable federation snapshot for Control decisions, with no second State authority.

use crate::InMemorySharedNodeState;
use domain::{NodeId, NodeStateSnapshot, StateRecord};
use ports::SharedNodeStateReader;

/// Current Node registration and independently retained State evidence, captured for one transaction.
#[derive(Debug, Clone)]
pub struct DecisionStateSnapshot {
    /// Read-only copy of the current accepted Node projection.
    nodes: InMemorySharedNodeState,
    /// Original evidence records; receive times, epochs and TTL are never rewritten.
    records: Vec<StateRecord>,
}

impl DecisionStateSnapshot {
    /// Captures a supplied current view without admitting new State or creating any facts.
    pub const fn new(nodes: InMemorySharedNodeState, records: Vec<StateRecord>) -> Self {
        Self { nodes, records }
    }
}

impl SharedNodeStateReader for DecisionStateSnapshot {
    /// Reads one supplied registration without ownership or readiness authority.
    fn node(&self, node_id: &NodeId) -> Option<&NodeStateSnapshot> {
        self.nodes.node(node_id)
    }

    /// Returns supplied Node snapshots in deterministic source ordering.
    fn nodes(&self) -> Vec<&NodeStateSnapshot> {
        self.nodes.nodes()
    }

    /// Returns original attributed evidence for a configured Control gate.
    fn decision_records(&self) -> Vec<StateRecord> {
        self.records.clone()
    }
}
