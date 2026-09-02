//! Transport-neutral ports for independently attributed State records.

use domain::{StateRecord, StateRecordKey};
use std::fmt::{Display, Formatter};

/// Failures exposed by the source-aware State projection boundary.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum StateRecordError {
    /// An older receive time or source sequence attempted to replace newer evidence.
    StaleRecord(String),
    /// Equal ordering coordinates carried a conflicting immutable value.
    ConflictingRecord(String),
    /// The supplied event was unrelated to source-aware State.
    UnsupportedEvent,
}

impl Display for StateRecordError {
    /// Formats a stable State projection diagnostic.
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::StaleRecord(reason) => write!(formatter, "stale State record: {reason}"),
            Self::ConflictingRecord(reason) => {
                write!(formatter, "conflicting State record: {reason}")
            }
            Self::UnsupportedEvent => {
                formatter.write_str("event is not source-aware State evidence")
            }
        }
    }
}

impl std::error::Error for StateRecordError {}

/// Read-only access to source-aware State records without cross-source fusion.
pub trait StateRecordReader {
    /// Returns the latest record for one exact object/semantic/source/channel key.
    fn record(&self, key: &StateRecordKey) -> Option<StateRecord>;

    /// Returns every latest independently attributed record in deterministic key order.
    fn records(&self) -> Vec<StateRecord>;
}

/// Event-sourced write boundary for source-aware State records.
pub trait StateRecordWriter {
    /// Applies one validated record according to RoboGuide receive ordering.
    fn record_state(&mut self, record: StateRecord) -> Result<(), StateRecordError>;

    /// Applies one immutable event carrying source-aware State evidence.
    fn apply_state_event(&mut self, event: &domain::EventRecord) -> Result<(), StateRecordError>;
}
