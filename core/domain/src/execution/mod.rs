//! Transport-neutral execution intent values shared across integration boundaries.

mod command;
mod intent;
mod node_event;
mod recovery;
mod session;
mod value;

pub use command::ExecutionCommand;
pub use intent::{CapabilityContractRef, ExecutionIntent, OperationRef};
pub use node_event::NodeEvent;
pub use recovery::{
    EXECUTION_RECOVERY_METADATA_KEY, EXECUTION_RECOVERY_PROFILE_SCHEMA, ExecutionContinuation,
    ExecutionRecoveryProfile, ExecutionRecoverySupport, ExecutionStopScope,
    OperationRecoverySupport,
};
pub use session::{ExecutionSessionDescriptor, ExecutionSessionSlot};
pub use value::ExecutionValue;

#[cfg(test)]
mod tests;
