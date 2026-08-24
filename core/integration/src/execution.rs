//! Connector-owned execution identity and lifecycle state across network sessions.

use crate::{ExecuteCommand, ExecutionFact};
use std::collections::BTreeMap;

/// Durable-in-daemon execution lifecycle independent of transport sessions and leases.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ExecutionStatus {
    /// Identity has not been observed by this connector daemon.
    Unknown,
    /// Local EAIOS accepted the execution.
    Accepted,
    /// A physical or logical action is currently running.
    Running,
    /// Execution completed successfully.
    Completed,
    /// Execution reached terminal failure.
    Failed(String),
    /// Local EAIOS confirmed cancellation.
    Cancelled,
}

impl ExecutionStatus {
    /// Returns whether no future fact may legally change this status.
    pub const fn is_terminal(&self) -> bool {
        matches!(self, Self::Completed | Self::Failed(_) | Self::Cancelled)
    }

    /// Converts current state into a reconnect status fact.
    pub fn as_fact(&self) -> ExecutionFact {
        match self {
            Self::Unknown => ExecutionFact::Unknown,
            Self::Accepted => ExecutionFact::Accepted,
            Self::Running => ExecutionFact::Started,
            Self::Completed => ExecutionFact::Completed,
            Self::Failed(reason) => ExecutionFact::Failed {
                reason: reason.clone(),
            },
            Self::Cancelled => ExecutionFact::Cancelled,
        }
    }
}

/// Decision made before forwarding an Execute to Local EAIOS.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ExecutionRegistryDecision {
    /// First observation of this identity; backend invocation is allowed once.
    Start,
    /// Same identity and command already exist; report state without invoking again.
    Existing(ExecutionStatus),
    /// Same identity was reused for a different command.
    Conflict,
}

/// Execution registry whose lifetime is the Connector daemon, not one session.
#[derive(Debug, Default)]
pub struct ExecutionRegistry {
    /// Stable identities mapped to command fingerprints and current lifecycle.
    entries: BTreeMap<String, ExecutionRecord>,
}

/// One execution identity and its immutable command fingerprint.
#[derive(Debug)]
struct ExecutionRecord {
    /// Serialized command identity used to detect conflicting reuse.
    fingerprint: String,
    /// Latest accepted lifecycle fact.
    status: ExecutionStatus,
    /// Last emitted event sequence across all reconnects.
    sequence: u64,
}

impl ExecutionRegistry {
    /// Classifies Execute without ever restarting an existing identity.
    pub fn begin(
        &mut self,
        execution_id: &str,
        command: &ExecuteCommand,
    ) -> ExecutionRegistryDecision {
        let fingerprint = serde_json::to_string(command).expect("wire command is serializable");
        match self.entries.get(execution_id) {
            Some(record) if record.fingerprint == fingerprint => {
                ExecutionRegistryDecision::Existing(record.status.clone())
            }
            Some(_) => ExecutionRegistryDecision::Conflict,
            None => {
                self.entries.insert(
                    execution_id.to_string(),
                    ExecutionRecord {
                        fingerprint,
                        status: ExecutionStatus::Accepted,
                        sequence: 0,
                    },
                );
                ExecutionRegistryDecision::Start
            }
        }
    }

    /// Applies one backend fact and returns its connector-wide sequence.
    pub fn record_fact(&mut self, execution_id: &str, fact: &ExecutionFact) -> Option<u64> {
        let record = self.entries.get_mut(execution_id)?;
        if record.status.is_terminal() {
            return None;
        }
        record.status = match fact {
            ExecutionFact::Accepted => ExecutionStatus::Accepted,
            ExecutionFact::Started => ExecutionStatus::Running,
            ExecutionFact::Completed => ExecutionStatus::Completed,
            ExecutionFact::Failed { reason } => ExecutionStatus::Failed(reason.clone()),
            ExecutionFact::Cancelled => ExecutionStatus::Cancelled,
            ExecutionFact::Unknown => ExecutionStatus::Unknown,
        };
        record.sequence += 1;
        Some(record.sequence)
    }

    /// Returns the known execution state without changing it.
    pub fn status(&self, execution_id: &str) -> ExecutionStatus {
        self.entries
            .get(execution_id)
            .map_or(ExecutionStatus::Unknown, |record| record.status.clone())
    }

    /// Returns reconnect snapshots in stable execution-id order.
    pub fn snapshots(&self) -> Vec<(String, ExecutionStatus)> {
        self.entries
            .iter()
            .map(|(id, record)| (id.clone(), record.status.clone()))
            .collect()
    }
}
