#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! Deterministic fake nodes, virtual time, and inspectable event evidence.

use domain::{
    CorrelationId, EventId, EventPayload, EventRecord, ExecutionCommand, NodeEvent, NodeHealth,
    NodeRegistration, NodeStatus, TimestampMs,
};
use ports::{Clock, EventSink, NodeGateway, NodeGatewayError, NodeGatewayErrorKind};
use std::cell::RefCell;
use std::rc::Rc;

/// A deterministic clock for offline core tests.
#[derive(Debug, Clone, Copy)]
pub struct VirtualClock {
    /// Current deterministic timestamp.
    now: TimestampMs,
}

impl VirtualClock {
    /// Creates a virtual clock at a known timestamp.
    pub const fn new(now: TimestampMs) -> Self {
        Self { now }
    }

    /// Advances virtual time without sleeping the test process.
    pub fn advance_by(&mut self, milliseconds: u64) {
        self.now = TimestampMs::new(self.now.as_millis() + milliseconds);
    }
}

impl Clock for VirtualClock {
    /// Returns the current virtual timestamp.
    fn now(&self) -> TimestampMs {
        self.now
    }
}

/// An in-memory immutable event log used by deterministic tests and demos.
#[derive(Debug, Default)]
pub struct InMemoryEventLog {
    /// Immutable event records in append order.
    records: Vec<EventRecord>,
    /// Counter used to generate deterministic event identities.
    next_event_number: u64,
}

impl InMemoryEventLog {
    /// Creates an empty event log.
    pub fn new() -> Self {
        Self::default()
    }

    /// Returns all recorded events in append order.
    pub fn records(&self) -> &[EventRecord] {
        &self.records
    }

    /// Returns whether at least one payload satisfies the supplied predicate.
    pub fn contains_payload(&self, predicate: impl Fn(&EventPayload) -> bool) -> bool {
        self.records
            .iter()
            .any(|record| predicate(record.payload()))
    }
}

impl EventSink for InMemoryEventLog {
    /// Assigns a deterministic event identity and appends the evidence record.
    fn append(
        &mut self,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        causation_id: Option<&EventId>,
        payload: EventPayload,
    ) {
        self.next_event_number += 1;
        let event_id = match EventId::new(format!("event-{}", self.next_event_number)) {
            Ok(event_id) => event_id,
            Err(error) => panic!("event id invariant violated: {error}"),
        };
        self.records.push(EventRecord::new(
            event_id,
            timestamp,
            correlation_id.clone(),
            causation_id.cloned(),
            payload,
        ));
    }
}

/// A cloneable event-log handle for Control and Runtime in one process.
#[derive(Clone, Debug)]
pub struct SharedEventLog {
    /// Single-process shared storage for Control and Runtime evidence.
    inner: Rc<RefCell<InMemoryEventLog>>,
}

impl SharedEventLog {
    /// Creates a shared, single-process event log handle.
    pub fn new() -> Self {
        Self {
            inner: Rc::new(RefCell::new(InMemoryEventLog::new())),
        }
    }

    /// Copies the current event trace for assertions or display.
    pub fn snapshot(&self) -> Vec<EventRecord> {
        self.inner.borrow().records().to_vec()
    }
}

impl Default for SharedEventLog {
    /// Creates a default shared event log.
    fn default() -> Self {
        Self::new()
    }
}

impl EventSink for SharedEventLog {
    /// Appends evidence through the shared in-memory log.
    fn append(
        &mut self,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        causation_id: Option<&EventId>,
        payload: EventPayload,
    ) {
        self.inner
            .borrow_mut()
            .append(timestamp, correlation_id, causation_id, payload);
    }
}

/// Failure behavior injected into a fake local runtime.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum FailureMode {
    /// Every command completes successfully.
    Never,
    /// The next command returns a task failure, then normal execution resumes.
    FailNext {
        /// Reason returned to DEAIOS.
        reason: String,
    },
    /// The next command fails and the fake adapter subsequently reports a new health fact.
    FailNextAndReportStatus {
        /// Reason returned to DEAIOS for the failed role execution.
        reason: String,
        /// Local health reported by the fake EAIOS after the failure.
        status: NodeStatus,
    },
    /// The next command causes a local safety stop.
    SafeStopNext {
        /// Reason returned by local safety.
        reason: String,
    },
}

/// A deterministic fake node that implements the local Node Contract.
pub struct FakeNode {
    /// Node contract exposed to the runtime.
    registration: NodeRegistration,
    /// Latest deterministic health state.
    status: NodeStatus,
    /// Optional deterministic failure returned by status observation.
    status_failure: Option<NodeGatewayError>,
    /// Failure behavior to apply to the next command.
    failure_mode: FailureMode,
    /// Commands received by the fake node in order.
    executed_commands: Vec<ExecutionCommand>,
}

impl FakeNode {
    /// Creates a healthy fake node with no injected failures.
    pub fn new(registration: NodeRegistration) -> Self {
        Self {
            registration,
            status: NodeStatus::new(NodeHealth::Online, TimestampMs::new(0)),
            status_failure: None,
            failure_mode: FailureMode::Never,
            executed_commands: Vec::new(),
        }
    }

    /// Configures one failure for the next command.
    pub fn with_failure_mode(mut self, failure_mode: FailureMode) -> Self {
        self.failure_mode = failure_mode;
        self
    }

    /// Configures the health fact returned through the fake adapter boundary.
    pub const fn with_status(mut self, status: NodeStatus) -> Self {
        self.status = status;
        self
    }

    /// Configures a deterministic gateway failure for every status request.
    pub fn with_status_failure(mut self, error: NodeGatewayError) -> Self {
        self.status_failure = Some(error);
        self
    }

    /// Returns all commands received by this fake node.
    pub fn executed_commands(&self) -> &[ExecutionCommand] {
        &self.executed_commands
    }
}

impl NodeGateway for FakeNode {
    /// Returns the fake node's registration.
    fn registration(&self) -> &NodeRegistration {
        &self.registration
    }

    /// Returns the fake node's latest health snapshot.
    fn status(&self) -> Result<NodeStatus, NodeGatewayError> {
        self.status_failure.clone().map_or(Ok(self.status), Err)
    }

    /// Executes deterministically and applies the configured failure injection.
    fn execute(&mut self, command: &ExecutionCommand) -> Result<NodeEvent, NodeGatewayError> {
        if !self.status.health().is_schedulable() {
            return Err(NodeGatewayError::new(
                self.registration.node_id().clone(),
                NodeGatewayErrorKind::Rejected,
                "local node is not schedulable",
            ));
        }
        self.executed_commands.push(command.clone());
        let failure_mode = std::mem::replace(&mut self.failure_mode, FailureMode::Never);
        match failure_mode {
            FailureMode::Never => Ok(NodeEvent::TaskCompleted {
                node_id: self.registration.node_id().clone(),
                task_ref: command.task_ref().clone(),
                group_id: command.group_id().clone(),
                role_id: command.role_id().clone(),
            }),
            FailureMode::FailNext { reason } => Ok(NodeEvent::TaskFailed {
                node_id: self.registration.node_id().clone(),
                task_ref: command.task_ref().clone(),
                group_id: command.group_id().clone(),
                role_id: command.role_id().clone(),
                reason,
            }),
            FailureMode::FailNextAndReportStatus { reason, status } => {
                self.status = status;
                Ok(NodeEvent::TaskFailed {
                    node_id: self.registration.node_id().clone(),
                    task_ref: command.task_ref().clone(),
                    group_id: command.group_id().clone(),
                    role_id: command.role_id().clone(),
                    reason,
                })
            }
            FailureMode::SafeStopNext { reason } => {
                self.status = NodeStatus::new(NodeHealth::SafeStopped, TimestampMs::new(0));
                Ok(NodeEvent::SafeStopped {
                    node_id: self.registration.node_id().clone(),
                    reason,
                })
            }
        }
    }
}

#[cfg(test)]
mod tests;
