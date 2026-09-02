#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! State & Memory Plane facade for implemented observable projections.

mod allocation;
mod event_log;
mod memory;
mod node;
mod spatial_memory;
mod state_record;

pub use allocation::InMemoryAllocationState;
pub use event_log::{PersistedCheckpoint, PersistedEvent, SqliteEventLog, SqliteEventLogError};
pub use memory::MemoryCatalogProjection;
pub use node::InMemorySharedNodeState;
pub use spatial_memory::MapCatalogProjection;
pub use state_record::StateRecordProjection;
