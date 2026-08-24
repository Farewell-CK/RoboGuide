//! Blocking HTTP client implementation of the transport-neutral NodeGateway.

use super::HttpAdapterError;
use super::wire::{WireExecutionRequest, WireExecutionResponse, WireRegistration, WireStatus};
use domain::{ExecutionCommand, NodeEvent, NodeRegistration, NodeStatus};
use ports::{NodeGateway, NodeGatewayError, NodeGatewayErrorKind};
use reqwest::blocking::Client;
use std::time::Duration;

/// Minimal HTTP operation surface used by the gateway and deterministic test transport.
pub(super) trait HttpTransport {
    /// Retrieves one response body from a fully qualified URL.
    fn get(&self, url: &str) -> Result<String, HttpAdapterError>;

    /// Posts one JSON body and returns the response body.
    fn post_json(&self, url: &str, body: &str) -> Result<String, HttpAdapterError>;
}

/// Production blocking reqwest transport owned only by the adapter crate.
struct ReqwestTransport {
    /// Reusable client carrying the configured total timeout.
    client: Client,
}

impl ReqwestTransport {
    /// Builds a blocking HTTP client with an explicit total request timeout.
    fn new(timeout: Duration) -> Result<Self, HttpAdapterError> {
        let client = Client::builder()
            .timeout(timeout)
            .build()
            .map_err(map_reqwest_error)?;
        Ok(Self { client })
    }
}

impl HttpTransport for ReqwestTransport {
    /// Performs one versioned GET request and rejects non-success status codes.
    fn get(&self, url: &str) -> Result<String, HttpAdapterError> {
        self.client
            .get(url)
            .send()
            .and_then(reqwest::blocking::Response::error_for_status)
            .and_then(reqwest::blocking::Response::text)
            .map_err(map_reqwest_error)
    }

    /// Performs one JSON POST and rejects non-success status codes.
    fn post_json(&self, url: &str, body: &str) -> Result<String, HttpAdapterError> {
        self.client
            .post(url)
            .header("content-type", "application/json")
            .body(body.to_string())
            .send()
            .and_then(reqwest::blocking::Response::error_for_status)
            .and_then(reqwest::blocking::Response::text)
            .map_err(map_reqwest_error)
    }
}

/// Reference HTTP adapter implementing the generic NodeGateway contract.
pub struct HttpNodeGateway {
    /// Endpoint root without a trailing slash.
    endpoint: String,
    /// Immutable registration obtained during connection.
    registration: NodeRegistration,
    /// Adapter-local HTTP mechanism hidden behind the NodeGateway boundary.
    transport: Box<dyn HttpTransport>,
}

impl HttpNodeGateway {
    /// Connects to an EAIOS bridge and validates its v0.1 registration contract.
    pub fn connect(
        endpoint: impl Into<String>,
        timeout: Duration,
    ) -> Result<Self, HttpAdapterError> {
        let endpoint = normalize_endpoint(endpoint.into())?;
        let transport = Box::new(ReqwestTransport::new(timeout)?);
        Self::connect_with_transport(endpoint, transport)
    }

    /// Fetches and validates registration through an injected HTTP transport.
    pub(super) fn connect_with_transport(
        endpoint: String,
        transport: Box<dyn HttpTransport>,
    ) -> Result<Self, HttpAdapterError> {
        let body = transport.get(&format!("{endpoint}/v1/registration"))?;
        let wire: WireRegistration = serde_json::from_str(&body)
            .map_err(|error| HttpAdapterError::protocol(error.to_string()))?;
        let registration = wire.try_into()?;
        Ok(Self {
            endpoint,
            registration,
            transport,
        })
    }

    /// Maps an adapter error to the transport-neutral error visible through NodeGateway.
    fn gateway_error(&self, error: HttpAdapterError) -> NodeGatewayError {
        let (kind, reason) = match error {
            HttpAdapterError::Transport { kind, reason } => (kind, reason),
            HttpAdapterError::Protocol { reason } => (NodeGatewayErrorKind::Protocol, reason),
        };
        NodeGatewayError::new(self.registration.node_id().clone(), kind, reason)
    }
}

impl NodeGateway for HttpNodeGateway {
    /// Returns registration validated when this HTTP adapter was connected.
    fn registration(&self) -> &NodeRegistration {
        &self.registration
    }

    /// Fetches source-reported health without converting transport failure into Offline.
    fn status(&self) -> Result<NodeStatus, NodeGatewayError> {
        let body = self
            .transport
            .get(&format!("{}/v1/status", self.endpoint))
            .map_err(|error| self.gateway_error(error))?;
        let wire: WireStatus = serde_json::from_str(&body)
            .map_err(|error| self.gateway_error(HttpAdapterError::protocol(error.to_string())))?;
        wire.into_domain(self.registration.node_id())
            .map_err(|error| self.gateway_error(error))
    }

    /// Sends canonical intent and validates that the synchronous result matches command identity.
    fn execute(&mut self, command: &ExecutionCommand) -> Result<NodeEvent, NodeGatewayError> {
        if command.node_id() != self.registration.node_id() {
            return Err(NodeGatewayError::new(
                self.registration.node_id().clone(),
                NodeGatewayErrorKind::Protocol,
                format!(
                    "command node {} does not match adapter node {}",
                    command.node_id(),
                    self.registration.node_id()
                ),
            ));
        }
        let request = WireExecutionRequest::from_command(command);
        let body = serde_json::to_string(&request)
            .map_err(|error| self.gateway_error(HttpAdapterError::protocol(error.to_string())))?;
        let response = self
            .transport
            .post_json(&format!("{}/v1/execute", self.endpoint), &body)
            .map_err(|error| self.gateway_error(error))?;
        let wire: WireExecutionResponse = serde_json::from_str(&response)
            .map_err(|error| self.gateway_error(HttpAdapterError::protocol(error.to_string())))?;
        wire.into_domain(command)
            .map_err(|error| self.gateway_error(error))
    }
}

/// Removes trailing separators and rejects an empty endpoint before any request.
fn normalize_endpoint(endpoint: String) -> Result<String, HttpAdapterError> {
    let endpoint = endpoint.trim_end_matches('/').to_string();
    if endpoint.trim().is_empty() {
        return Err(HttpAdapterError::protocol(
            "HTTP endpoint must not be empty",
        ));
    }
    Ok(endpoint)
}

/// Converts reqwest failures into the narrow transport-neutral gateway categories.
fn map_reqwest_error(error: reqwest::Error) -> HttpAdapterError {
    let kind = if error.is_timeout() {
        NodeGatewayErrorKind::Timeout
    } else {
        NodeGatewayErrorKind::Unavailable
    };
    HttpAdapterError::Transport {
        kind,
        reason: error.to_string(),
    }
}
