//! Session, lease, and execution idempotency state.

use std::collections::BTreeMap;

/// Result of accepting an execution identity and sequence.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ExecutionDisposition {
    /// This execution/sequence is new and should be delivered to the backend.
    New,
    /// The same or an older sequence was already observed; do not repeat the action.
    Duplicate,
    /// The execution identity was previously used with a different command.
    Conflict,
}

/// Session state retained across one connector connection.
#[derive(Debug, Default)]
pub struct SessionState {
    /// Active server-issued session identity.
    pub session_id: String,
    /// Active server-issued lease identity.
    pub lease_id: String,
    /// Last accepted client sequence.
    last_sequence: u64,
    /// Execution identity to command fingerprint.
    executions: BTreeMap<String, String>,
}

impl SessionState {
    /// Creates a session with explicit server identities.
    pub fn new(session_id: impl Into<String>, lease_id: impl Into<String>) -> Self {
        Self {
            session_id: session_id.into(),
            lease_id: lease_id.into(),
            ..Self::default()
        }
    }

    /// Accepts a client sequence monotonically without treating it as task completion.
    pub fn accept_sequence(&mut self, sequence: u64) -> bool {
        if sequence <= self.last_sequence {
            return false;
        }
        self.last_sequence = sequence;
        true
    }

    /// Applies execution idempotency before forwarding a command to Local EAIOS.
    pub fn accept_execution(
        &mut self,
        execution_id: &str,
        fingerprint: &str,
    ) -> ExecutionDisposition {
        match self.executions.get(execution_id) {
            Some(previous) if previous == fingerprint => ExecutionDisposition::Duplicate,
            Some(_) => ExecutionDisposition::Conflict,
            None => {
                self.executions
                    .insert(execution_id.to_string(), fingerprint.to_string());
                ExecutionDisposition::New
            }
        }
    }
}

/// Session protocol failure.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SessionError(pub String);
