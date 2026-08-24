//! Versioned wire messages for the long-lived node integration stream.

use domain::{CapabilityKind, NodeId, NodeRegistration, ResourceKind};
use serde::{Deserialize, Serialize};
use std::fmt::{Display, Formatter};

/// Protocol version negotiated by Hello.
pub const PROTOCOL_VERSION_V0_1: &str = "roboguide.integration/v0.1";
/// Server implementation version advertised during Hello.
pub const SERVER_VERSION_V0_1: &str = "roboguide.server/v0.1";
/// Maximum accepted JSON frame size.
pub const MAX_FRAME_BYTES: usize = 1_048_576;

/// One client-to-server stream message.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum ClientFrame {
    /// Initial connection negotiation.
    Hello(Hello),
    /// Registration sent after compatible HelloAck.
    Register(Registration),
    /// Lease-bound node health fact.
    Heartbeat {
        /// Current session identity.
        session_id: String,
        /// Current server-issued lease identity.
        lease_id: String,
        /// Monotonic node sequence for duplicate detection.
        sequence: u64,
        /// Optional health label from Local EAIOS.
        status: Option<String>,
    },
    /// A registration or capability fact changed.
    RegistrationUpdate {
        /// Current session identity.
        session_id: String,
        /// Updated node registration.
        registration: Registration,
        /// Monotonic node sequence for duplicate detection.
        sequence: u64,
    },
    /// Execution lifecycle fact pushed by the node.
    ExecutionEvent {
        /// Current or previously negotiated session identity.
        session_id: String,
        /// Globally stable execution identity.
        execution_id: String,
        /// Node-local ordered event sequence for this execution.
        sequence: u64,
        /// Fact reported by Local EAIOS.
        fact: ExecutionFact,
    },
}

/// One server-to-client stream message.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum ServerFrame {
    /// Result of protocol negotiation.
    HelloAck {
        /// Server implementation version.
        server_version: String,
        /// Negotiated stream protocol version.
        protocol_version: String,
        /// Node Contract version accepted for registration.
        node_contract_version: String,
    },
    /// Registration was accepted and a lease/session was created.
    RegistrationAccepted {
        /// Server session identity.
        session_id: String,
        /// Lease authorizing heartbeats and control messages.
        lease_id: String,
        /// Suggested heartbeat period.
        heartbeat_interval_ms: u64,
    },
    /// A server-side execution request.
    Execute {
        /// Current session identity.
        session_id: String,
        /// Globally stable execution identity.
        execution_id: String,
        /// Canonical command forwarded to the backend.
        command: ExecuteCommand,
    },
    /// Cancellation request; the backend decides local safety behavior.
    Cancel {
        /// Current session identity.
        session_id: String,
        /// Existing execution identity.
        execution_id: String,
    },
    /// Acknowledgement of a node sequence.
    Ack {
        /// Sequence acknowledged.
        sequence: u64,
    },
    /// Protocol or lifecycle rejection.
    Error {
        /// Stable diagnostic reason.
        reason: String,
    },
}

/// Hello negotiation payload.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Hello {
    /// Protocol versions understood by the connector.
    pub protocol_versions: Vec<String>,
    /// Node Contract versions understood by the connector.
    pub node_contract_versions: Vec<String>,
    /// Stable node identity used for reconnect correlation.
    pub node_id: String,
}

/// Node registration payload independent of Domain serialization.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Registration {
    /// Stable node identity.
    pub node_id: String,
    /// Local EAIOS/runtime name.
    pub runtime: String,
    /// Local runtime version.
    pub runtime_version: String,
    /// Advertised capabilities and exact contracts.
    pub capabilities: Vec<WireCapability>,
    /// Advertised physical/logical resources.
    pub resources: Vec<WireResource>,
    /// Semantic Node Contract version.
    pub node_contract_version: String,
}

/// Capability and canonical contract advertised by one node.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WireCapability {
    /// Coarse capability category.
    pub kind: String,
    /// Whether the capability is currently available.
    pub available: bool,
    /// Exact canonical contracts executable by this capability.
    pub contracts: Vec<String>,
}

/// Resource advertised by one node.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct WireResource {
    /// Stable resource identity.
    pub id: String,
    /// Resource category.
    pub kind: String,
    /// Capacity exposed to scheduling.
    pub capacity: u32,
}

/// Canonical command sent to a connector backend.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct ExecuteCommand {
    /// Mission namespace.
    pub mission_id: String,
    /// Task namespace within the mission.
    pub task_id: String,
    /// Execution Group authority.
    pub group_id: String,
    /// Role being invoked.
    pub role_id: String,
    /// Canonical operation identity.
    pub contract: String,
    /// Scalar parameters interpreted by the local backend.
    pub parameters: serde_json::Map<String, serde_json::Value>,
}

/// Execution fact emitted by a connector backend.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "phase", rename_all = "snake_case")]
pub enum ExecutionFact {
    /// Local EAIOS accepted the request.
    Accepted,
    /// Local EAIOS began physical execution.
    Started,
    /// Local EAIOS completed the request.
    Completed,
    /// Local EAIOS failed or rejected the request.
    Failed {
        /// Stable local reason.
        reason: String,
    },
    /// Local EAIOS cancelled the request.
    Cancelled,
}

/// Protocol-level conversion or validation failure.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProtocolError(pub String);

impl Display for ProtocolError {
    /// Formats a stable protocol rejection.
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for ProtocolError {}

impl Registration {
    /// Converts a validated Domain registration into the wire shape.
    pub fn from_domain(registration: &NodeRegistration) -> Self {
        let capabilities = registration
            .capabilities()
            .iter()
            .map(|capability| WireCapability {
                kind: capability_kind_name(capability.kind()).to_string(),
                available: capability.is_available(),
                contracts: registration
                    .supported_contracts()
                    .iter()
                    .map(ToString::to_string)
                    .collect(),
            })
            .collect();
        let resources = registration
            .resources()
            .iter()
            .map(|resource| WireResource {
                id: resource.id().as_str().to_string(),
                kind: resource_kind_name(resource.kind()).to_string(),
                capacity: resource.capacity(),
            })
            .collect();
        Self {
            node_id: registration.node_id().as_str().to_string(),
            runtime: registration.local_runtime().name().to_string(),
            runtime_version: registration.local_runtime().version().to_string(),
            capabilities,
            resources,
            node_contract_version: registration.contract_version().as_str().to_string(),
        }
    }

    /// Validates the identity and negotiated Node Contract version.
    pub fn validate(&self, expected_node_contract: &str) -> Result<NodeId, ProtocolError> {
        if self.node_contract_version != expected_node_contract {
            return Err(ProtocolError("node contract version mismatch".to_string()));
        }
        NodeId::new(self.node_id.clone()).map_err(|error| ProtocolError(error.to_string()))
    }
}

/// Returns stable protocol text for a coarse capability.
fn capability_kind_name(kind: CapabilityKind) -> &'static str {
    match kind {
        CapabilityKind::Mobility => "mobility",
        CapabilityKind::Transport => "transport",
        CapabilityKind::Compute => "compute",
        CapabilityKind::Observation => "observation",
    }
}

/// Returns stable protocol text for a resource category.
fn resource_kind_name(kind: ResourceKind) -> &'static str {
    match kind {
        ResourceKind::Space => "space",
        ResourceKind::Compute => "compute",
        ResourceKind::Time => "time",
    }
}

/// Parses one newline-delimited frame and rejects oversized input.
pub(crate) fn decode_frame<T: for<'de> Deserialize<'de>>(line: &[u8]) -> Result<T, ProtocolError> {
    if line.len() > MAX_FRAME_BYTES {
        return Err(ProtocolError("frame exceeds maximum size".to_string()));
    }
    serde_json::from_slice(line)
        .map_err(|error| ProtocolError(format!("invalid protocol frame: {error}")))
}

/// Encodes one frame with a newline delimiter.
pub(crate) fn encode_frame<T: Serialize>(frame: &T) -> Result<Vec<u8>, ProtocolError> {
    let mut bytes = serde_json::to_vec(frame).map_err(|error| ProtocolError(error.to_string()))?;
    bytes.push(b'\n');
    Ok(bytes)
}
