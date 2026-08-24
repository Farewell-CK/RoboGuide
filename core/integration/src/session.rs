//! Session, lease, and execution idempotency state.

/// Session state retained across one connector connection.
#[derive(Debug, Default)]
pub struct SessionState {
    /// Active server-issued session identity.
    pub session_id: String,
    /// Active server-issued lease identity.
    pub lease_id: String,
    /// Last accepted client sequence.
    last_sequence: u64,
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
}

/// Session protocol failure.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SessionError(pub String);
