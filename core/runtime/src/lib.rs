#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! Runtime execution semantics between DEAIOS Control and local node adapters.

use domain::{EventPayload, ExecutionCommand, NodeEvent, NodeId, NodeStatus, TimestampMs};
use ports::{Clock, EventSink, NodeGateway, NodeGatewayError};
use std::collections::BTreeMap;
use std::fmt::{Display, Formatter};

/// Runtime failures that remain below global recovery policy.
#[derive(Debug)]
pub enum RuntimeError {
    /// No adapter was registered for the requested node.
    UnknownNode(NodeId),
    /// The local adapter rejected or failed the command.
    Gateway(NodeGatewayError),
}

impl Display for RuntimeError {
    /// Formats a runtime failure for control-plane reconciliation.
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::UnknownNode(node_id) => {
                write!(formatter, "runtime has no adapter for node {node_id}")
            }
            Self::Gateway(error) => Display::fmt(error, formatter),
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
