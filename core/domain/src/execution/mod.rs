//! Transport-neutral execution intent values shared across integration boundaries.

mod intent;
mod value;

pub use intent::{ExecutionIntent, OperationRef};
pub use value::ExecutionValue;

#[cfg(test)]
mod tests;
