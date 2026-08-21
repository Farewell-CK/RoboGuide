#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! Deterministic in-memory implementation of Shared Node State Slice v0.1.
//!
//! This crate stores current node registration and health facts. It does not
//! decide scheduling eligibility, own leases or reservations, project
//! Execution Groups, implement Shared Belief, or provide Memory persistence.

use domain::{NodeId, NodeStateSnapshot, NodeStatus};
use ports::{SharedNodeStateReader, SharedNodeStateWriter, SharedStateError};
use std::collections::BTreeMap;

/// Deterministic current-state store for shared node facts.
#[derive(Debug, Default)]
pub struct InMemorySharedNodeState {
    /// Latest accepted node snapshots indexed in stable identity order.
    nodes: BTreeMap<NodeId, NodeStateSnapshot>,
}

impl InMemorySharedNodeState {
    /// Creates an empty Shared Node State implementation.
    pub const fn new() -> Self {
        Self {
            nodes: BTreeMap::new(),
        }
    }
}

impl SharedNodeStateReader for InMemorySharedNodeState {
    /// Returns the latest accepted snapshot for one node.
    fn node(&self, node_id: &NodeId) -> Option<&NodeStateSnapshot> {
        self.nodes.get(node_id)
    }

    /// Returns current snapshots in deterministic node-identity order.
    fn nodes(&self) -> Vec<&NodeStateSnapshot> {
        self.nodes.values().collect()
    }
}

impl SharedNodeStateWriter for InMemorySharedNodeState {
    /// Records registration and health facts without overwriting newer evidence.
    fn record_node(&mut self, snapshot: NodeStateSnapshot) -> Result<(), SharedStateError> {
        if let Some(current) = self.nodes.get(snapshot.node_id()) {
            reject_older_status(snapshot.node_id(), current.status(), snapshot.status())?;
        }
        self.nodes.insert(snapshot.node_id().clone(), snapshot);
        Ok(())
    }

    /// Updates health while preserving the latest accepted observation invariant.
    fn update_node_status(
        &mut self,
        node_id: &NodeId,
        status: NodeStatus,
    ) -> Result<(), SharedStateError> {
        let current = self
            .nodes
            .get(node_id)
            .ok_or_else(|| SharedStateError::UnknownNode(node_id.clone()))?;
        reject_older_status(node_id, current.status(), status)?;
        let registration = current.registration().clone();
        self.nodes.insert(
            node_id.clone(),
            NodeStateSnapshot::new(registration, status),
        );
        Ok(())
    }
}

/// Rejects an incoming status older than the latest accepted observation.
fn reject_older_status(
    node_id: &NodeId,
    current: NodeStatus,
    incoming: NodeStatus,
) -> Result<(), SharedStateError> {
    if incoming.observed_at() < current.observed_at() {
        return Err(SharedStateError::StaleObservation {
            node_id: node_id.clone(),
            current_observed_at: current.observed_at(),
            incoming_observed_at: incoming.observed_at(),
        });
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use domain::{
        Capability, CapabilityKind, LocalRuntime, NodeHealth, NodeRegistration, Resource,
        ResourceId, ResourceKind, TimestampMs,
    };

    /// Builds one transport node snapshot for deterministic State tests.
    fn snapshot(observed_at: u64) -> NodeStateSnapshot {
        let registration = NodeRegistration::new(
            NodeId::new("node-a").expect("test node id should be valid"),
            LocalRuntime::new("vendor-runtime", "1.0.0").expect("test runtime should be valid"),
            vec![Capability::new(CapabilityKind::Transport, true)],
            vec![
                Resource::new(
                    ResourceId::new("space-a").expect("test resource id should be valid"),
                    ResourceKind::Space,
                    1,
                )
                .expect("test resource should be valid"),
            ],
        );
        NodeStateSnapshot::new(
            registration,
            NodeStatus::new(NodeHealth::Online, TimestampMs::new(observed_at)),
        )
    }

    /// Registration, capability, resource, runtime, and health facts remain readable.
    #[test]
    fn registration_enters_shared_node_state() {
        let mut state = InMemorySharedNodeState::new();
        state
            .record_node(snapshot(10))
            .expect("initial observation should be accepted");

        let node_id = NodeId::new("node-a").expect("test node id should be valid");
        let stored = state.node(&node_id).expect("registered node should exist");
        assert_eq!(stored.node_id(), &node_id);
        assert_eq!(
            stored.registration().local_runtime().name(),
            "vendor-runtime"
        );
        assert_eq!(stored.registration().capabilities().len(), 1);
        assert_eq!(stored.registration().resources().len(), 1);
        assert_eq!(stored.status().health(), NodeHealth::Online);
        assert_eq!(stored.status().observed_at(), TimestampMs::new(10));
    }

    /// New health evidence replaces current facts while older evidence is rejected.
    #[test]
    fn health_updates_preserve_latest_observation() {
        let mut state = InMemorySharedNodeState::new();
        state
            .record_node(snapshot(10))
            .expect("initial observation should be accepted");
        let node_id = NodeId::new("node-a").expect("test node id should be valid");
        state
            .update_node_status(
                &node_id,
                NodeStatus::new(NodeHealth::Offline, TimestampMs::new(20)),
            )
            .expect("newer health evidence should be accepted");

        let error = state
            .update_node_status(
                &node_id,
                NodeStatus::new(NodeHealth::Online, TimestampMs::new(15)),
            )
            .expect_err("older health evidence must not replace current state");
        assert!(matches!(error, SharedStateError::StaleObservation { .. }));
        let stored = state.node(&node_id).expect("registered node should remain");
        assert_eq!(stored.status().health(), NodeHealth::Offline);
        assert_eq!(stored.status().observed_at(), TimestampMs::new(20));
    }
}
