//! Blocking HTTP/JSON reference transport for the transport-neutral NodeGateway.

mod client;
mod error;
mod wire;

pub use client::HttpNodeGateway;
pub use error::HttpAdapterError;
pub use wire::decode_intent_fixture;

#[cfg(test)]
mod tests;
