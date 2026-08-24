#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! State & Memory Plane facade for implemented observable projections.

mod allocation;
mod node;

pub use allocation::InMemoryAllocationState;
pub use node::InMemorySharedNodeState;
