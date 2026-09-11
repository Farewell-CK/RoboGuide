//! Startup-compiled catalog for one generic, declarative Local Integration Engine.

#[path = "compile/artifact.rs"]
mod artifact_compile;
#[path = "compile/capability.rs"]
mod capability_compile;
mod catalog;
pub mod driver;
mod execution_catalog;
pub mod grpc_driver;
pub mod http_driver;
mod http_transport;
pub mod mapping;
pub mod mcp_driver;
mod observation_catalog;
#[path = "compile/observation.rs"]
mod observation_compile;
#[path = "compile/topology.rs"]
mod topology_compile;
#[path = "compile/workflow.rs"]
mod workflow_compile;

use crate::{
    ArtifactInputBindingConfig, ArtifactOperationConfig, ArtifactOutputBindingConfig,
    ArtifactServiceConfig, CapabilityBindingConfig, CapabilityProfileConfig,
    CapabilityReadinessConfig, ConnectionConfig, ExecutionStateMappingConfig, HealthCheckConfig,
    LocalOperationConfig, LocalSystemConfig, MemoryProviderConfig, MemoryWorkflowConfig,
    NodeServiceConfig, OperationBindingConfig, PeerChannelObserverConfig, ResourceConfig,
    SensorConfig, StateExportConfig, WorkflowConfig, WorkflowStepConfig,
};
use driver::{CompiledDriverRequest, DriverKind};
use mapping::{
    CompiledRequestMapping, MappingError, WorkflowContext, evaluate, validate_expression,
    validate_pointer,
};
use prost_reflect::DescriptorPool;
use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

use artifact_compile::*;
use capability_compile::*;
use observation_compile::*;
use topology_compile::*;
use workflow_compile::*;

/// Only accepted schema identity for the declarative Node Service catalog.
pub const CONFIG_SCHEMA_V0_2: &str = "roboguide.node-config/v0.2";
/// Schema identity for the node catalog with Spatial Memory artifact bindings.
pub const CONFIG_SCHEMA_V0_3: &str = "roboguide.node-config/v0.3";
/// Schema identity requiring an exact readiness observation for every capability.
pub const CONFIG_SCHEMA_V0_4: &str = "roboguide.node-config/v0.4";
/// Schema identity adding selective State exports and Memory providers over Protocol v0.3.
pub const CONFIG_SCHEMA_V0_5: &str = "roboguide.node-config/v0.5";
/// Schema identity adding executable heterogeneous Memory provider workflows.
pub const CONFIG_SCHEMA_V0_6: &str = "roboguide.node-config/v0.6";
/// Schema identity separating capability profiles from executable operation workflows.
pub const CONFIG_SCHEMA_V0_7: &str = "roboguide.node-config/v0.7";
/// Maximum peer endpoints accepted from one bounded local observation.
const MAX_PEER_CHANNELS_PER_OBSERVATION: usize = 64;
/// Maximum JSON bytes accepted from one local peer readiness response.
const MAX_PEER_CHANNEL_OBSERVATION_BYTES: usize = 64 * 1024;

/// Compiled local systems paired with their deferred health configuration.
type CompiledLocalSystems = (
    BTreeMap<String, CompiledLocalSystem>,
    BTreeMap<String, HealthCheckConfig>,
);

/// Immutable startup-validated local integration catalog.
#[derive(Debug, Clone, PartialEq)]
pub struct CompiledLocalCatalog {
    /// Accepted node-config schema identity retained for conformance policy.
    schema: String,
    /// Stable Node identity.
    node_id: String,
    /// Remote RoboGuide Server endpoint.
    server_endpoint: String,
    /// Durable journal directory resolved relative to the configuration file.
    state_directory: PathBuf,
    /// Reconnect backoff.
    reconnect_delay_ms: u64,
    /// Local systems by stable identity.
    local_systems: BTreeMap<String, CompiledLocalSystem>,
    /// Fixed local connections by stable identity.
    connections: BTreeMap<String, CompiledConnection>,
    /// Local-system health observations by stable owner identity.
    health_checks: BTreeMap<String, CompiledHealthCheck>,
    /// Canonical operation workflows by operation identity.
    operations: BTreeMap<String, CompiledOperation>,
    /// Exact capability readiness and feasibility evidence by contract identity.
    capability_profiles: BTreeMap<String, CompiledCapabilityProfile>,
    /// Control-visible resources by stable identity.
    resources: BTreeMap<String, CompiledResource>,
    /// Sensors by stable identity.
    sensors: BTreeMap<String, CompiledSensor>,
    /// Selective source-aware State channels by node-wide export identity.
    state_exports: BTreeMap<String, CompiledStateExport>,
    /// Fixed peer-channel readiness observations by node-wide observer identity.
    peer_channel_observers: BTreeMap<String, CompiledPeerChannelObserver>,
    /// Selective Memory discovery/exchange providers by node-wide identity.
    memory_providers: BTreeMap<String, CompiledMemoryProvider>,
    /// Optional independent Spatial Memory artifact data-plane configuration.
    artifacts: Option<CompiledArtifactService>,
}

/// Startup-validated node-side configuration for the independent artifact data plane.
#[derive(Debug, Clone, PartialEq)]
pub struct CompiledArtifactService {
    /// Absolute central artifact service endpoint.
    endpoint: String,
    /// Deployment-owned cache root resolved relative to the config file.
    cache_directory: PathBuf,
    /// Maximum accepted artifact size.
    max_artifact_bytes: u64,
    /// Bounded transfer chunk size.
    chunk_size_bytes: usize,
    /// Bounded connection establishment timeout.
    connect_timeout_ms: u64,
    /// Bounded read-idle timeout for response progress.
    read_timeout_ms: u64,
    /// Validated static input bindings.
    input_bindings: BTreeMap<String, ArtifactInputBindingConfig>,
    /// Validated static output bindings.
    output_bindings: BTreeMap<String, ArtifactOutputBindingConfig>,
}

/// Immutable local runtime identity and registration metadata.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CompiledLocalSystem {
    /// Stable configuration-local identity.
    id: String,
    /// Runtime name.
    runtime_name: String,
    /// Runtime version.
    runtime_version: String,
    /// Non-secret metadata.
    metadata: BTreeMap<String, String>,
}

/// Immutable local-system health check and state projection.
#[derive(Debug, Clone, PartialEq)]
pub struct CompiledHealthCheck {
    /// Local system whose health is observed.
    owner: String,
    /// Fixed local driver operation.
    step: CompiledWorkflowStep,
    /// Response-relative state pointer.
    state_pointer: String,
    /// Optional response-relative detail pointer.
    detail_pointer: Option<String>,
    /// Normalized local state lookup.
    states: BTreeMap<String, LocalHealthState>,
    /// Whether lookup is case-sensitive.
    case_sensitive: bool,
}

/// Canonical local-system health projected from configuration.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LocalHealthState {
    /// Local system is healthy and usable.
    Online,
    /// Local system is reachable with degraded operation.
    Degraded,
    /// Local system is unavailable.
    Offline,
}

/// One local-system health fact and descriptive detail.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LocalHealthFact {
    /// Canonical health state.
    pub state: LocalHealthState,
    /// Local descriptive detail.
    pub detail: String,
}

/// Immutable fixed local State sampling operation and semantic declaration.
#[derive(Debug, Clone, PartialEq)]
pub struct CompiledStateExport {
    /// Node-wide export identity.
    id: String,
    /// Local-system owner.
    owner: String,
    /// Node or world object class.
    object_class: String,
    /// Domain-specific object category.
    object_type: String,
    /// Stable object identity.
    object_id: String,
    /// Reported or observed source meaning.
    semantic: String,
    /// Versioned JSON value schema.
    payload_schema: String,
    /// Receive-relative validity period.
    valid_for_ms: u64,
    /// Period between local samples.
    interval_ms: u64,
    /// Fixed local observation operation; the deployment facade must keep it side-effect free.
    step: CompiledWorkflowStep,
    /// Response-relative JSON value pointer.
    value_pointer: String,
    /// Optional response-relative source timestamp pointer.
    source_observed_at_pointer: Option<String>,
    /// Optional response-relative confidence pointer.
    confidence_pointer: Option<String>,
}

/// One successful local State sample ready for a protocol observation batch.
#[derive(Debug, Clone, PartialEq)]
pub struct StateExportFact {
    /// Node-wide export identity.
    pub export_id: String,
    /// Bounded structured value.
    pub value: serde_json::Value,
    /// Optional source-local timestamp in milliseconds.
    pub source_observed_at_ms: Option<u64>,
    /// Optional confidence in millionths.
    pub confidence_millionths: Option<u32>,
}

/// Immutable fixed observation of Local EAIOS-established peer-channel endpoints.
#[derive(Debug, Clone, PartialEq)]
pub struct CompiledPeerChannelObserver {
    /// Node-wide observer identity.
    id: String,
    /// Configuration-owned Local EAIOS identity injected into every fact.
    owner: String,
    /// Period between observations.
    interval_ms: u64,
    /// Receive-relative lifetime applied to every returned endpoint.
    valid_for_ms: u64,
    /// Fixed read-only local operation.
    step: CompiledWorkflowStep,
    /// Response-relative array pointer.
    channels_pointer: String,
}

/// One Local EAIOS acknowledgement for a logical peer-channel endpoint.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PeerChannelReadinessFact {
    /// Mission-level Group containing the coordination Context.
    pub group_id: String,
    /// Coordination Context declaring the channel.
    pub context_id: String,
    /// Logical ContextRole represented by this Local EAIOS.
    pub context_role_id: String,
    /// Configured node-local EAIOS that owns this endpoint.
    pub local_system_id: String,
    /// Shared channel instance identity established by the Local EAIOS peers.
    pub channel_instance_id: String,
    /// Descriptor profile confirmed by the local channel implementation.
    pub profile_id: String,
    /// Descriptor message schema confirmed by the local channel implementation.
    pub message_schema: String,
    /// Whether the local endpoint currently confirms readiness.
    pub ready: bool,
    /// Receive-relative readiness lifetime fixed by Node configuration.
    pub valid_for_ms: u64,
}

/// Immutable Memory provider declaration without a local storage implementation requirement.
#[derive(Debug, Clone, PartialEq)]
pub struct CompiledMemoryProvider {
    /// Node-wide provider identity.
    id: String,
    /// Local-system owner.
    owner: String,
    /// Generic Memory kind.
    kind: String,
    /// Maximum local/global sharing scope.
    scope: String,
    /// Discoverable or exchangeable policy.
    visibility: String,
    /// Versioned provider payload schema.
    payload_schema: String,
    /// Artifact media type when bytes are offered.
    media_type: String,
    /// Whether node-config/v0.6 enables local backend operations.
    operational: bool,
    /// Node ledger, controlled export handoff, and reference-backend root.
    storage_directory: PathBuf,
    /// Optional discovery workflow.
    discover: Option<CompiledMemoryWorkflow>,
    /// Optional export workflow.
    export: Option<CompiledMemoryWorkflow>,
    /// Optional import workflow.
    import: Option<CompiledMemoryWorkflow>,
}

/// Startup-validated Memory provider workflow.
#[derive(Debug, Clone, PartialEq)]
pub struct CompiledMemoryWorkflow {
    /// Ordered local driver steps.
    steps: Vec<CompiledWorkflowStep>,
    /// Optional response pointer to the provider-authorized publish-eligible manifest set.
    manifests_pointer: Option<String>,
    /// Optional response pointer to an exported artifact path.
    artifact_path_pointer: Option<String>,
}

/// Immutable exact-capability readiness check and state projection.
#[derive(Debug, Clone, PartialEq)]
pub struct CompiledCapabilityReadiness {
    /// Fixed local driver operation.
    step: CompiledWorkflowStep,
    /// Response-relative state pointer.
    state_pointer: String,
    /// Optional response-relative detail pointer.
    detail_pointer: Option<String>,
    /// Normalized local readiness lookup.
    states: BTreeMap<String, bool>,
    /// Whether lookup is case-sensitive.
    case_sensitive: bool,
}

/// One exact-capability readiness fact and descriptive detail.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CapabilityReadinessFact {
    /// Whether the exact canonical contract can execute now.
    pub available: bool,
    /// Local descriptive detail or observation failure.
    pub detail: String,
}

/// Immutable exact capability evidence exposed through Node Contract v0.5.
#[derive(Debug, Clone, PartialEq)]
pub struct CompiledCapabilityProfile {
    /// Canonical capability contract identity.
    contract: String,
    /// Transitional coarse kind consumed by the current Control compatibility model.
    kind: String,
    /// Sole local-system owner of this evidence.
    owner: String,
    /// Typed feasibility attributes in stable lexical order.
    attributes: BTreeMap<String, domain::ExecutionValue>,
    /// Exact-contract readiness observation.
    readiness: Option<CompiledCapabilityReadiness>,
}

/// Immutable validated connection details for one local driver.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum CompiledConnection {
    /// Local HTTP JSON connection.
    Http {
        /// Stable identity.
        id: String,
        /// Owning local system.
        owner: String,
        /// Fixed loopback or Unix endpoint.
        endpoint: String,
        /// Request timeout.
        timeout_ms: u64,
        /// Header names mapped to environment variable names.
        credential_headers: BTreeMap<String, String>,
    },
    /// Local dynamic gRPC connection.
    Grpc {
        /// Stable identity.
        id: String,
        /// Owning local system.
        owner: String,
        /// Fixed loopback or Unix endpoint.
        endpoint: String,
        /// Resolved descriptor-set path, absent only with reflection enabled.
        descriptor_set: Option<PathBuf>,
        /// Explicit local-reflection opt-in.
        reflection: bool,
        /// Request timeout.
        timeout_ms: u64,
        /// Metadata names mapped to environment variable names.
        credential_metadata: BTreeMap<String, String>,
    },
    /// Local MCP Streamable HTTP connection.
    Mcp {
        /// Stable identity.
        id: String,
        /// Owning local system.
        owner: String,
        /// Fixed loopback endpoint.
        endpoint: String,
        /// Request timeout.
        timeout_ms: u64,
        /// Header names mapped to environment variable names.
        credential_headers: BTreeMap<String, String>,
    },
}

/// Immutable canonical operation owner, resource requirements, locks, and workflow.
#[derive(Debug, Clone, PartialEq)]
pub struct CompiledOperation {
    /// Canonical operation identity; named `contract` for legacy journal compatibility.
    contract: String,
    /// Coarse capability kind consumed by Control Matching.
    kind: String,
    /// Sole local-system owner.
    owner: String,
    /// Control-committed resources required for dispatch.
    required_resources: BTreeSet<String>,
    /// Node-local concurrency locks.
    local_locks: BTreeSet<String>,
    /// Optional artifact action fixed by the versioned node configuration.
    artifact_operation: Option<ArtifactOperationConfig>,
    /// Optional exact-contract readiness observation; required by schema v0.4.
    readiness: Option<CompiledCapabilityReadiness>,
    /// Compiled execute/status/cancel behavior.
    workflow: CompiledWorkflow,
}

/// Legacy name for a compiled canonical operation workflow.
///
/// Configurations through node-config/v0.6 combined capability evidence and execution workflow
/// in one declaration. New code should use [`CompiledOperation`].
pub type CompiledCapability = CompiledOperation;

/// Immutable execute, status, cancel, and state-mapping workflow.
#[derive(Debug, Clone, PartialEq)]
pub struct CompiledWorkflow {
    /// Physical/computational dispatch steps.
    execute: Vec<CompiledWorkflowStep>,
    /// Reconciliation/status steps.
    status: Vec<CompiledWorkflowStep>,
    /// Cancellation-request steps.
    cancel: Vec<CompiledWorkflowStep>,
    /// Validated extraction of the durable local execution handle.
    local_handle: crate::ValueExpressionConfig,
    /// Status polling interval.
    poll_interval_ms: u64,
    /// Local state projection.
    execution_state: CompiledExecutionStateMapping,
}

/// One compiled workflow step with fixed routing and dynamic-body mapping.
#[derive(Debug, Clone, PartialEq)]
pub struct CompiledWorkflowStep {
    /// Stable workflow-local identity.
    id: String,
    /// Fixed connection identity.
    connection: String,
    /// Fixed operation.
    operation: LocalOperationConfig,
    /// Validated request mapping.
    request: CompiledRequestMapping,
}

/// Validated local-state mapping.
#[derive(Debug, Clone, PartialEq, Eq)]
struct CompiledExecutionStateMapping {
    /// State value pointer.
    state_pointer: String,
    /// Optional detail pointer.
    reason_pointer: Option<String>,
    /// Normalized state-to-phase lookup.
    states: BTreeMap<String, MappedExecutionPhase>,
    /// Whether lookup preserves local case.
    case_sensitive: bool,
}

/// Canonical lifecycle phase produced from configured local status values.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MappedExecutionPhase {
    /// Local system accepted the invocation but has not reported work started.
    Accepted,
    /// Local work is currently active.
    Running,
    /// Local work completed successfully.
    Completed,
    /// Local work terminated unsuccessfully.
    Failed,
    /// Local work confirmed terminal cancellation.
    Cancelled,
}

/// Canonical execution fact projected from current workflow context.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct MappedExecutionFact {
    /// Canonical phase.
    pub phase: MappedExecutionPhase,
    /// Optional local detail converted to text.
    pub reason: Option<String>,
}

/// Immutable Control-visible resource.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CompiledResource {
    /// Stable resource identity.
    id: String,
    /// Transport-neutral kind.
    kind: String,
    /// Non-zero capacity.
    capacity: u32,
    /// Owning local system.
    owner: String,
    /// Non-secret metadata.
    metadata: BTreeMap<String, String>,
}

/// Immutable locally observed sensor.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CompiledSensor {
    /// Stable sensor identity.
    id: String,
    /// Transport-neutral kind.
    kind: String,
    /// Owning local system.
    owner: String,
    /// Non-secret metadata.
    metadata: BTreeMap<String, String>,
}

/// Configuration loading or startup compilation failure.
#[derive(Debug, thiserror::Error)]
pub enum CatalogError {
    /// Configuration file could not be loaded or parsed.
    #[error("failed to load node configuration: {0}")]
    Load(#[source] std::io::Error),
    /// A cross-reference or deployment invariant is invalid.
    #[error("invalid `{field}`: {reason}")]
    Validation {
        /// Configuration field or collection element.
        field: String,
        /// Actionable invariant failure.
        reason: String,
    },
    /// A request mapping is invalid.
    #[error("invalid mapping for step `{step}`: {source}")]
    Mapping {
        /// Workflow step identity.
        step: String,
        /// Mapping validation failure.
        #[source]
        source: MappingError,
    },
}

#[cfg(test)]
#[path = "tests/mod.rs"]
mod tests;
