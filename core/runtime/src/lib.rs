#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! Runtime execution semantics between DEAIOS Control and local node adapters.

mod clock;
mod execution;

pub use clock::{FixedClock, SystemMonotonicClock};
pub use execution::{
    DispatchDecision, ExecutionContext, ExecutionEvent, ExecutionRuntimeError, ExecutionStatus,
    ObservedTaskResult, RuntimeExecutionCheckpoint, RuntimeExecutionManager,
};

use domain::{
    EventPayload, ExecutionCommand, NODE_CONTRACT_VERSION_V0_1, NodeContractVersion, NodeEvent,
    NodeHealthObservation, NodeId, NodeLiveness, NodeLivenessObservation, NodeStatus,
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
    /// The adapter implements a Node Contract version this Runtime does not support.
    UnsupportedNodeContract {
        /// Node advertising the unsupported contract.
        node_id: NodeId,
        /// Version advertised by the adapter.
        version: NodeContractVersion,
    },
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
            Self::UnsupportedNodeContract { node_id, version } => write!(
                formatter,
                "node {node_id} advertises unsupported contract {version}"
            ),
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
        if node.registration().contract_version().as_str() != NODE_CONTRACT_VERSION_V0_1 {
            return Err(RuntimeError::UnsupportedNodeContract {
                node_id,
                version: node.registration().contract_version().clone(),
            });
        }
        self.nodes.insert(node_id, node);
        Ok(())
    }

    /// Returns the latest local health snapshot for one node.
    pub fn status(&self, node_id: &NodeId) -> Result<NodeStatus, RuntimeError> {
        self.nodes
            .get(node_id)
            .ok_or_else(|| RuntimeError::UnknownNode(node_id.clone()))?
            .status()
            .map_err(RuntimeError::Gateway)
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
        let status = match node.status() {
            Ok(status) => status,
            Err(error) => {
                state
                    .record_node_liveness(
                        node_id,
                        NodeLivenessObservation::new(NodeLiveness::Unreachable, received_at),
                    )
                    .map_err(RuntimeError::SharedState)?;
                return Err(RuntimeError::Gateway(error));
            }
        };
        let observation = NodeHealthObservation::new(node_id.clone(), status, received_at);
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

#[cfg(test)]
mod tests {
    use super::*;
    use domain::{
        Capability, CapabilityKind, LocalRuntime, NodeHealth, NodeLiveness,
        NodeLivenessObservation, NodeRegistration, NodeStateSnapshot, Resource, ResourceId,
        ResourceKind, TimestampMs,
    };
    use ports::{NodeGatewayError, NodeGatewayErrorKind};
    use ports::{SharedNodeStateReader, SharedNodeStateWriter};
    use state::InMemorySharedNodeState;
    use testkit::{FakeNode, InMemoryEventLog};

    /// Builds one transport registration used by Runtime observation tests.
    fn registration() -> NodeRegistration {
        NodeRegistration::new(
            NodeId::new("node-a").expect("test node id should be valid"),
            LocalRuntime::new("fake-eaios", "0.1.0").expect("test runtime should be valid"),
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

    /// Gateway status failure changes liveness without forging a local Offline report.
    #[test]
    fn runtime_status_failure_preserves_reported_health() {
        let registration = registration();
        let node_id = registration.node_id().clone();
        let reported_status = NodeStatus::new(NodeHealth::Online, TimestampMs::new(1_000));
        let mut state = InMemorySharedNodeState::new();
        state
            .record_node(NodeStateSnapshot::new(
                registration.clone(),
                reported_status,
                TimestampMs::new(10),
                NodeLivenessObservation::new(NodeLiveness::Reachable, TimestampMs::new(10)),
            ))
            .expect("initial node facts should enter Shared State");
        let mut runtime = Runtime::new(
            FixedClock::new(TimestampMs::new(20)),
            InMemoryEventLog::new(),
        );
        runtime
            .register_node(Box::new(FakeNode::new(registration).with_status_failure(
                NodeGatewayError::new(
                    node_id.clone(),
                    NodeGatewayErrorKind::Timeout,
                    "status request timed out",
                ),
            )))
            .expect("fake adapter registration should succeed");

        let error = runtime
            .observe_node_status(&node_id, &mut state)
            .expect_err("status transport failure must remain visible");

        assert!(matches!(error, RuntimeError::Gateway(_)));
        let stored = state.node(&node_id).expect("node facts should remain");
        assert_eq!(stored.reported_status(), reported_status);
        assert_eq!(stored.reported_status_received_at(), TimestampMs::new(10));
        assert_eq!(stored.liveness().liveness(), NodeLiveness::Unreachable);
        assert_eq!(stored.liveness().observed_at(), TimestampMs::new(20));
    }

    /// Runtime rejects unknown semantic Node Contract versions before registration.
    #[test]
    fn runtime_rejects_unknown_node_contract_version() {
        let registration = NodeRegistration::new(
            NodeId::new("node-legacy").expect("node id must be valid"),
            LocalRuntime::new("legacy-eaios", "0.1.0").expect("runtime must be valid"),
            domain::NodeContractVersion::new("roboguide.node.v9")
                .expect("contract version must be valid"),
            vec![],
            vec![],
        );
        let mut runtime = Runtime::new(
            FixedClock::new(TimestampMs::new(0)),
            InMemoryEventLog::new(),
        );

        let result = runtime.register_node(Box::new(FakeNode::new(registration)));

        assert!(matches!(
            result,
            Err(RuntimeError::UnsupportedNodeContract { .. })
        ));
    }
}
