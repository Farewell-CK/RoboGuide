#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! Deterministic in-memory implementation of Shared Node State Slice v0.1.
//!
//! This crate stores current node registration, reported health, and liveness facts. It does not
//! decide scheduling eligibility, own leases or reservations, project
//! Execution Groups, implement Shared Belief, or provide Memory persistence.

use domain::{NodeHealthObservation, NodeId, NodeLivenessObservation, NodeStateSnapshot};
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
    /// Records a snapshot ordered by its RoboGuide-local health receive time.
    fn record_node(&mut self, snapshot: NodeStateSnapshot) -> Result<(), SharedStateError> {
        if let Some(current) = self.nodes.get(snapshot.node_id()) {
            reject_older_timestamp(
                snapshot.node_id(),
                current.reported_status_received_at(),
                snapshot.reported_status_received_at(),
            )?;
        }
        self.nodes.insert(snapshot.node_id().clone(), snapshot);
        Ok(())
    }

    /// Atomically records health ordered by receive time and successful reachability.
    fn record_node_health(
        &mut self,
        observation: NodeHealthObservation,
    ) -> Result<(), SharedStateError> {
        let node_id = observation.node_id();
        let current = self
            .nodes
            .get(node_id)
            .ok_or_else(|| SharedStateError::UnknownNode(node_id.clone()))?;
        let status = observation.status();
        reject_older_timestamp(
            node_id,
            current.reported_status_received_at(),
            observation.received_at(),
        )?;
        let registration = current.registration().clone();
        let liveness = if observation.received_at() >= current.liveness().observed_at() {
            domain::NodeLivenessObservation::new(
                domain::NodeLiveness::Reachable,
                observation.received_at(),
            )
        } else {
            current.liveness()
        };
        self.nodes.insert(
            node_id.clone(),
            NodeStateSnapshot::new(registration, status, observation.received_at(), liveness),
        );
        Ok(())
    }

    /// Records system-observed liveness without altering local reported health.
    fn record_node_liveness(
        &mut self,
        node_id: &NodeId,
        observation: NodeLivenessObservation,
    ) -> Result<(), SharedStateError> {
        let current = self
            .nodes
            .get(node_id)
            .ok_or_else(|| SharedStateError::UnknownNode(node_id.clone()))?;
        reject_older_timestamp(
            node_id,
            current.liveness().observed_at(),
            observation.observed_at(),
        )?;
        let registration = current.registration().clone();
        let reported_status = current.reported_status();
        self.nodes.insert(
            node_id.clone(),
            NodeStateSnapshot::new(
                registration,
                reported_status,
                current.reported_status_received_at(),
                observation,
            ),
        );
        Ok(())
    }
}

/// Rejects a fact older in the relevant RoboGuide-local ordering dimension.
fn reject_older_timestamp(
    node_id: &NodeId,
    current_ordering_time: domain::TimestampMs,
    incoming_ordering_time: domain::TimestampMs,
) -> Result<(), SharedStateError> {
    if incoming_ordering_time < current_ordering_time {
        return Err(SharedStateError::StaleObservation {
            node_id: node_id.clone(),
            current_ordering_time,
            incoming_ordering_time,
        });
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use domain::{
        Capability, CapabilityKind, LocalRuntime, NodeHealth, NodeLiveness, NodeRegistration,
        NodeStatus, Resource, ResourceId, ResourceKind, TimestampMs,
    };

    /// Builds one transport node snapshot for deterministic State tests.
    fn snapshot(source_observed_at: u64, received_at: u64) -> NodeStateSnapshot {
        let registration = NodeRegistration::new(
            NodeId::new("node-a").expect("test node id should be valid"),
            LocalRuntime::new("vendor-runtime", "1.0.0").expect("test runtime should be valid"),
            domain::NodeContractVersion::v0_1(),
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
            NodeStatus::new(NodeHealth::Online, TimestampMs::new(source_observed_at)),
            TimestampMs::new(received_at),
            NodeLivenessObservation::new(NodeLiveness::Reachable, TimestampMs::new(received_at)),
        )
    }

    /// Registration, capability, resource, runtime, and health facts remain readable.
    #[test]
    fn registration_enters_shared_node_state() {
        let mut state = InMemorySharedNodeState::new();
        state
            .record_node(snapshot(1_000, 10))
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
        assert_eq!(stored.reported_status().health(), NodeHealth::Online);
        assert_eq!(
            stored.reported_status().observed_at(),
            TimestampMs::new(1_000)
        );
        assert_eq!(stored.reported_status_received_at(), TimestampMs::new(10));
        assert_eq!(stored.liveness().liveness(), NodeLiveness::Reachable);
    }

    /// Later receive time accepts health even when the source-local clock moves backwards.
    #[test]
    fn later_receive_time_accepts_backward_source_time() {
        let mut state = InMemorySharedNodeState::new();
        state
            .record_node(snapshot(1_000, 10))
            .expect("initial observation should be accepted");
        let node_id = NodeId::new("node-a").expect("test node id should be valid");
        state
            .record_node_health(NodeHealthObservation::new(
                node_id.clone(),
                NodeStatus::new(NodeHealth::Degraded, TimestampMs::new(900)),
                TimestampMs::new(20),
            ))
            .expect("later receive time should win despite source clock regression");

        let stored = state.node(&node_id).expect("registered node should remain");
        assert_eq!(stored.reported_status().health(), NodeHealth::Degraded);
        assert_eq!(
            stored.reported_status().observed_at(),
            TimestampMs::new(900)
        );
        assert_eq!(stored.reported_status_received_at(), TimestampMs::new(20));
    }

    /// Older receive time cannot overwrite current health even with newer source time.
    #[test]
    fn older_receive_time_cannot_overwrite_health() {
        let mut state = InMemorySharedNodeState::new();
        state
            .record_node(snapshot(1_000, 20))
            .expect("initial observation should be accepted");
        let node_id = NodeId::new("node-a").expect("test node id should be valid");

        let error = state
            .record_node_health(NodeHealthObservation::new(
                node_id.clone(),
                NodeStatus::new(NodeHealth::Offline, TimestampMs::new(2_000)),
                TimestampMs::new(10),
            ))
            .expect_err("older receive time must not replace current health");
        assert!(matches!(error, SharedStateError::StaleObservation { .. }));
        let stored = state.node(&node_id).expect("registered node should remain");
        assert_eq!(stored.reported_status().health(), NodeHealth::Online);
        assert_eq!(
            stored.reported_status().observed_at(),
            TimestampMs::new(1_000)
        );
        assert_eq!(stored.reported_status_received_at(), TimestampMs::new(20));
    }

    /// Liveness changes independently without rewriting local reported health.
    #[test]
    fn liveness_update_preserves_reported_health() {
        let mut state = InMemorySharedNodeState::new();
        state
            .record_node(snapshot(8_000, 10))
            .expect("initial observation should be accepted");
        let node_id = NodeId::new("node-a").expect("test node id should be valid");
        state
            .record_node_liveness(
                &node_id,
                NodeLivenessObservation::new(NodeLiveness::Unreachable, TimestampMs::new(20)),
            )
            .expect("newer liveness evidence should be accepted");

        let stored = state.node(&node_id).expect("registered node should remain");
        assert_eq!(stored.reported_status().health(), NodeHealth::Online);
        assert_eq!(
            stored.reported_status().observed_at(),
            TimestampMs::new(8_000)
        );
        assert_eq!(stored.reported_status_received_at(), TimestampMs::new(10));
        assert_eq!(stored.liveness().liveness(), NodeLiveness::Unreachable);
        assert_eq!(stored.liveness().observed_at(), TimestampMs::new(20));
    }
}
