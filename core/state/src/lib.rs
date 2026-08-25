#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! State & Memory Plane facade for implemented observable projections.

mod allocation;
mod event_log;
mod node;

pub use allocation::InMemoryAllocationState;
pub use event_log::{PersistedCheckpoint, PersistedEvent, SqliteEventLog, SqliteEventLogError};
pub use node::InMemorySharedNodeState;
