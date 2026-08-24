//! Transport-neutral execution intent values shared across integration boundaries.

mod intent;
mod value;

pub use intent::{CapabilityContractRef, ExecutionIntent};
pub use value::ExecutionValue;

#[cfg(test)]
mod tests;
