//! Backend-neutral translation from canonical operations to configured local invocations.

mod configured;

pub use configured::{
    BackendError, ConfiguredCommandBackend, LocalEaiosBackend, LocalExecutionContext,
    LocalInvocation,
};

#[cfg(test)]
mod tests;
