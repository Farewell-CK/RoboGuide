#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! Runtime execution semantics between DEAIOS Control and local node adapters.

use domain::{
    EventPayload, ExecutionCommand, NodeEvent, NodeHealthObservation, NodeId, NodeStatus,
    TimestampMs,
};
use ports::{
    Clock, EventSink, NodeGateway, NodeGatewayError, SharedNodeStateWriter, SharedStateError,
};
use std::collections::BTreeMap;
use std::fmt::{Display, Formatter};

/// Runtime failures that remain below global recovery policy.
#[derive(Debug)]
pub enum RuntimeError {
    /// No adapter was registered for the requested node.
    UnknownNode(NodeId),
    /// The local adapter rejected or failed the command.
    Gateway(NodeGatewayError),
    /// Shared Node State rejected a normalized runtime observation.
    SharedState(SharedStateError),
}

impl Display for RuntimeError {
    /// Formats a runtime failure for control-plane reconciliation.
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::UnknownNode(node_id) => {
                write!(formatter, "runtime has no adapter for node {node_id}")
            }
            Self::Gateway(error) => Display::fmt(error, formatter),
            Self::SharedState(error) => write!(formatter, "shared state error: {error}"),
        }
    }
}

impl std::error::Error for RuntimeError {}

/// DEAIOS Runtime state for discovery, invocation, and evidence propagation.
pub struct Runtime<C, E> {
    /// Injectable monotonic clock for invocation evidence.
    clock: C,
    /// Sink receiving immutable runtime observations.
    events: E,
    /// Local node gateways indexed by their logical identity.
    nodes: BTreeMap<NodeId, Box<dyn NodeGateway>>,
}

impl<C: Clock, E: EventSink> Runtime<C, E> {
    /// Creates a runtime with an injectable clock and evidence sink.
    pub fn new(clock: C, events: E) -> Self {
        Self {
            clock,
            events,
            nodes: BTreeMap::new(),
        }
    }

    /// Registers one local EAIOS or vendor adapter.
    pub fn register_node(&mut self, node: Box<dyn NodeGateway>) -> Result<(), RuntimeError> {
        let node_id = node.registration().node_id().clone();
        self.nodes.insert(node_id, node);
        Ok(())
    }

    /// Returns the latest local health snapshot for one node.
    pub fn status(&self, node_id: &NodeId) -> Result<NodeStatus, RuntimeError> {
        self.nodes
            .get(node_id)
            .map(|node| node.status())
            .ok_or_else(|| RuntimeError::UnknownNode(node_id.clone()))
    }

    /// Adds RoboGuide receive time to one gateway health report and writes it to State.
    ///
    /// A successfully read gateway status is also evidence that the node is
    /// currently reachable. Source-local observation time remains evidence;
    /// Runtime clock time controls receive ordering and later freshness policy.
    pub fn observe_node_status<S: SharedNodeStateWriter>(
        &self,
        node_id: &NodeId,
        state: &mut S,
    ) -> Result<NodeHealthObservation, RuntimeError> {
        let node = self
            .nodes
            .get(node_id)
            .ok_or_else(|| RuntimeError::UnknownNode(node_id.clone()))?;
        let received_at = self.clock.now();
        let observation = NodeHealthObservation::new(node_id.clone(), node.status(), received_at);
        state
            .record_node_health(observation.clone())
            .map_err(RuntimeError::SharedState)?;
        Ok(observation)
    }

    /// Invokes one role through the local node contract and records its result.
    pub fn execute(&mut self, command: &ExecutionCommand) -> Result<NodeEvent, RuntimeError> {
        let node = self
            .nodes
            .get_mut(command.node_id())
            .ok_or_else(|| RuntimeError::UnknownNode(command.node_id().clone()))?;
        let event = node.execute(command).map_err(RuntimeError::Gateway)?;
        self.events.append(
            self.clock.now(),
            command.correlation_id(),
            None,
            EventPayload::NodeObservation(event.clone()),
        );
        Ok(event)
    }

    /// Returns a shared reference to the runtime evidence sink.
    pub fn events(&self) -> &E {
        &self.events
    }

    /// Consumes the runtime and returns its evidence sink.
    pub fn into_events(self) -> E {
        self.events
    }
}

/// Provides the current runtime timestamp through a fixed value.
#[derive(Debug, Clone, Copy)]
pub struct FixedClock {
    /// Timestamp returned by every clock read.
    timestamp: TimestampMs,
}

impl FixedClock {
    /// Creates a clock that always returns the supplied timestamp.
    pub const fn new(timestamp: TimestampMs) -> Self {
        Self { timestamp }
    }
}

impl Clock for FixedClock {
    /// Returns the configured fixed timestamp.
    fn now(&self) -> TimestampMs {
        self.timestamp
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use domain::{
        Capability, CapabilityKind, LocalRuntime, NodeHealth, NodeLiveness,
        NodeLivenessObservation, NodeRegistration, NodeStateSnapshot, Resource, ResourceId,
        ResourceKind,
    };
    use ports::{SharedNodeStateReader, SharedNodeStateWriter};
    use state::InMemorySharedNodeState;
    use testkit::{FakeNode, InMemoryEventLog};

    /// Builds one transport registration used by Runtime observation tests.
    fn registration() -> NodeRegistration {
        NodeRegistration::new(
            NodeId::new("node-a").expect("test node id should be valid"),
            LocalRuntime::new("fake-eaios", "0.1.0").expect("test runtime should be valid"),
            vec![Capability::new(CapabilityKind::Transport, true)],
            vec![
                Resource::new(
                    ResourceId::new("space-a").expect("test resource id should be valid"),
                    ResourceKind::Space,
                    1,
                )
                .expect("test resource should be valid"),
            ],
        )
    }

    /// Runtime normalizes gateway health and writes it to Shared Node State.
    #[test]
    fn runtime_health_observation_updates_state() {
        let registration = registration();
        let node_id = registration.node_id().clone();
        let mut state = InMemorySharedNodeState::new();
        state
            .record_node(NodeStateSnapshot::new(
                registration.clone(),
                NodeStatus::new(NodeHealth::Online, TimestampMs::new(0)),
                TimestampMs::new(0),
                NodeLivenessObservation::new(NodeLiveness::Reachable, TimestampMs::new(0)),
            ))
            .expect("initial node facts should enter Shared State");
        state
            .record_node_liveness(
                &node_id,
                NodeLivenessObservation::new(NodeLiveness::Unreachable, TimestampMs::new(5)),
            )
            .expect("test should start from unreachable liveness");
        let mut runtime = Runtime::new(
            FixedClock::new(TimestampMs::new(20)),
            InMemoryEventLog::new(),
        );
        runtime
            .register_node(Box::new(FakeNode::new(registration).with_status(
                NodeStatus::new(NodeHealth::Degraded, TimestampMs::new(1_000)),
            )))
            .expect("fake adapter registration should succeed");

        let observation = runtime
            .observe_node_status(&node_id, &mut state)
            .expect("Runtime should ingest the gateway observation");
        assert_eq!(observation.node_id(), &node_id);
        assert_eq!(observation.status().health(), NodeHealth::Degraded);
        assert_eq!(observation.status().observed_at(), TimestampMs::new(1_000));
        assert_eq!(observation.received_at(), TimestampMs::new(20));
        let stored = state.node(&node_id).expect("node facts should remain");
        assert_eq!(stored.reported_status(), observation.status());
        assert_eq!(stored.reported_status_received_at(), TimestampMs::new(20));
        assert_eq!(stored.liveness().liveness(), NodeLiveness::Reachable);
        assert_eq!(stored.liveness().observed_at(), TimestampMs::new(20));
    }
}
