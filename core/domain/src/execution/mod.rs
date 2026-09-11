//! Transport-neutral execution intent values shared across integration boundaries.

mod command;
mod intent;
mod node_event;
mod value;

pub use command::ExecutionCommand;
pub use intent::{CapabilityContractRef, ExecutionIntent, OperationRef};
pub use node_event::NodeEvent;
pub use value::ExecutionValue;

#[cfg(test)]
mod tests;
