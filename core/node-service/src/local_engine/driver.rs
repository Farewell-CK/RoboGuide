//! Language-neutral local driver boundary for HTTP, dynamic gRPC, and MCP calls.

use serde_json::Value;
use std::collections::BTreeMap;
use std::future::Future;
use std::path::PathBuf;
use std::pin::Pin;
use tokio::sync::mpsc;

/// Supported local transport driver families.
#[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
pub enum DriverKind {
    /// HTTP request with a JSON request and response body.
    Http,
    /// Dynamic protobuf gRPC request.
    Grpc,
    /// MCP Streamable HTTP tool invocation.
    Mcp,
}

/// Fully rendered request whose routing fields came only from startup configuration.
#[derive(Debug, Clone, PartialEq)]
pub enum CompiledDriverRequest {
    /// Fixed HTTP JSON request.
    Http {
        /// Local endpoint selected by the compiled connection.
        endpoint: String,
        /// Whitelisted, fixed HTTP method.
        method: String,
        /// Fixed request path.
        path: String,
        /// Header names mapped to environment variable names.
        credential_headers: BTreeMap<String, String>,
        /// Rendered request JSON.
        body: Value,
        /// Request timeout.
        timeout_ms: u64,
    },
    /// Fixed dynamic gRPC request.
    Grpc {
        /// Local endpoint selected by the compiled connection.
        endpoint: String,
        /// Validated descriptor set path, absent only with explicit reflection.
        descriptor_set: Option<PathBuf>,
        /// Whether local reflection was explicitly enabled.
        reflection: bool,
        /// Fully qualified protobuf service name.
        service: String,
        /// Fixed protobuf method name.
        method: String,
        /// Whether the configured call returns a server stream.
        server_streaming: bool,
        /// Metadata names mapped to environment variable names.
        credential_metadata: BTreeMap<String, String>,
        /// Rendered protobuf message represented as JSON.
        message: Value,
        /// Request timeout.
        timeout_ms: u64,
    },
    /// Fixed MCP Streamable HTTP tool invocation.
    Mcp {
        /// Local endpoint selected by the compiled connection.
        endpoint: String,
        /// Fixed MCP tool name.
        tool: String,
        /// Header names mapped to environment variable names.
        credential_headers: BTreeMap<String, String>,
        /// Rendered tool arguments.
        arguments: Value,
        /// Request timeout.
        timeout_ms: u64,
    },
}

impl CompiledDriverRequest {
    /// Returns the driver family required to execute this request.
    pub const fn driver_kind(&self) -> DriverKind {
        match self {
            Self::Http { .. } => DriverKind::Http,
            Self::Grpc { .. } => DriverKind::Grpc,
            Self::Mcp { .. } => DriverKind::Mcp,
        }
    }

    /// Returns the immutable local endpoint selected during catalog compilation.
    pub fn endpoint(&self) -> &str {
        match self {
            Self::Http { endpoint, .. }
            | Self::Grpc { endpoint, .. }
            | Self::Mcp { endpoint, .. } => endpoint,
        }
    }
}

/// One ordered response fact produced by a local driver.
#[derive(Debug, Clone, PartialEq)]
pub struct DriverEvent {
    /// Driver-local event order within one request.
    pub sequence: u64,
    /// Structured response body or streamed message.
    pub payload: Value,
    /// True when no further response messages will follow.
    pub terminal: bool,
}

/// Receiver used for unary and streaming driver responses.
pub type DriverResponseStream = mpsc::Receiver<Result<DriverEvent, DriverError>>;

/// Successful dispatch result; even unary calls use a response stream for one uniform engine.
pub struct DriverResponse {
    /// Ordered response messages from the local transport.
    pub events: DriverResponseStream,
}

/// Future returned by a language-neutral local driver invocation.
pub type BoxDriverFuture<'a> =
    Pin<Box<dyn Future<Output = Result<DriverResponse, DriverError>> + Send + 'a>>;

/// Alias used by workflow executors that consume response messages directly.
pub type DriverEventStream = DriverResponseStream;

/// Transport implementation boundary owned by the generic Local Integration Engine.
pub trait LocalDriver: Send + Sync + 'static {
    /// Identifies the request family accepted by this driver implementation.
    fn kind(&self) -> DriverKind;

    /// Dispatches exactly once; callers must never infer retry safety from transport failure.
    fn invoke<'a>(&'a self, request: &'a CompiledDriverRequest) -> BoxDriverFuture<'a>;
}

/// Local driver setup, routing, transport, or response failure.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum DriverError {
    /// A request was routed to an implementation of the wrong driver family.
    #[error("driver kind mismatch")]
    KindMismatch,
    /// A referenced credential environment variable was unavailable.
    #[error("credential environment variable `{0}` is unavailable")]
    MissingCredential(String),
    /// The local transport failed; physical dispatch outcome may be unknown.
    #[error("local transport failed: {0}")]
    Transport(String),
    /// The Local EAIOS returned data that violated the configured contract.
    #[error("invalid local response: {0}")]
    InvalidResponse(String),
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Rendered requests retain fixed routing fields separately from dynamic bodies.
    #[test]
    fn request_exposes_fixed_route_and_driver_kind() {
        let request = CompiledDriverRequest::Mcp {
            endpoint: "http://127.0.0.1:7777/mcp".to_string(),
            tool: "navigate".to_string(),
            credential_headers: BTreeMap::new(),
            arguments: serde_json::json!({ "region": "library" }),
            timeout_ms: 1_000,
        };
        assert_eq!(request.driver_kind(), DriverKind::Mcp);
        assert_eq!(request.endpoint(), "http://127.0.0.1:7777/mcp");
    }
}
