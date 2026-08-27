//! Startup-compiled catalog for one generic, declarative Local Integration Engine.

pub mod driver;
pub mod grpc_driver;
pub mod http_driver;
mod http_transport;
pub mod mapping;
pub mod mcp_driver;

use crate::{
    ArtifactInputBindingConfig, ArtifactOperationConfig, ArtifactOutputBindingConfig,
    ArtifactServiceConfig, CapabilityBindingConfig, ConnectionConfig, ExecutionStateMappingConfig,
    HealthCheckConfig, LocalOperationConfig, LocalSystemConfig, NodeServiceConfig, ResourceConfig,
    SensorConfig, WorkflowConfig, WorkflowStepConfig,
};
use driver::{CompiledDriverRequest, DriverKind};
use mapping::{
    CompiledRequestMapping, MappingError, WorkflowContext, evaluate, validate_expression,
    validate_pointer,
};
use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

/// Only accepted schema identity for the declarative Node Service catalog.
pub const CONFIG_SCHEMA_V0_2: &str = "roboguide.node-config/v0.2";
/// Schema identity for the node catalog with Spatial Memory artifact bindings.
pub const CONFIG_SCHEMA_V0_3: &str = "roboguide.node-config/v0.3";

/// Compiled local systems paired with their deferred health configuration.
type CompiledLocalSystems = (
    BTreeMap<String, CompiledLocalSystem>,
    BTreeMap<String, HealthCheckConfig>,
);

/// Immutable startup-validated local integration catalog.
#[derive(Debug, Clone, PartialEq)]
pub struct CompiledLocalCatalog {
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
    /// Canonical capability ownership by contract identity.
    capabilities: BTreeMap<String, CompiledCapability>,
    /// Control-visible resources by stable identity.
    resources: BTreeMap<String, CompiledResource>,
    /// Sensors by stable identity.
    sensors: BTreeMap<String, CompiledSensor>,
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

/// Immutable capability owner, resource requirements, locks, and workflow.
#[derive(Debug, Clone, PartialEq)]
pub struct CompiledCapability {
    /// Canonical contract identity.
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
    /// Compiled execute/status/cancel behavior.
    workflow: CompiledWorkflow,
}

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

impl CompiledLocalCatalog {
    /// Compiles and validates the entire catalog atomically before any connection is opened.
    pub fn compile(
        config: NodeServiceConfig,
        config_directory: &Path,
    ) -> Result<Self, CatalogError> {
        let supports_artifacts = config.schema == CONFIG_SCHEMA_V0_3;
        require(
            matches!(
                config.schema.as_str(),
                CONFIG_SCHEMA_V0_2 | CONFIG_SCHEMA_V0_3
            ),
            "schema",
            format!("expected `{CONFIG_SCHEMA_V0_2}` or `{CONFIG_SCHEMA_V0_3}`"),
        )?;
        validate_identity(&config.node_id, "node_id")?;
        validate_server_endpoint(&config.server_endpoint)?;
        require(
            config.reconnect_delay_ms > 0,
            "reconnect_delay_ms",
            "must be non-zero",
        )?;
        require(
            !config.state_directory.as_os_str().is_empty(),
            "state_directory",
            "must not be empty",
        )?;
        let state_directory = resolve_path(config_directory, &config.state_directory);

        let (local_systems, health_configs) = compile_local_systems(config.local_systems)?;
        require(
            !local_systems.is_empty(),
            "local_systems",
            "must contain at least one local system",
        )?;
        let connections =
            compile_connections(config.connections, &local_systems, config_directory)?;
        require(
            !connections.is_empty(),
            "connections",
            "must contain at least one local connection",
        )?;
        let health_checks = compile_health_checks(health_configs, &local_systems, &connections)?;
        let resources = compile_resources(config.resources, &local_systems)?;
        let sensors = compile_sensors(config.sensors, &local_systems)?;
        let capabilities = compile_capabilities(
            config.capabilities,
            &local_systems,
            &connections,
            &resources,
            supports_artifacts,
        )?;
        require(
            !capabilities.is_empty(),
            "capabilities",
            "must contain at least one canonical capability",
        )?;
        require(
            supports_artifacts || config.artifacts.is_none(),
            "artifacts",
            format!("requires schema `{CONFIG_SCHEMA_V0_3}`"),
        )?;
        let artifacts = compile_artifacts(config.artifacts, config_directory)?;

        Ok(Self {
            node_id: config.node_id,
            server_endpoint: config.server_endpoint,
            state_directory,
            reconnect_delay_ms: config.reconnect_delay_ms,
            local_systems,
            connections,
            health_checks,
            capabilities,
            resources,
            sensors,
            artifacts,
        })
    }

    /// Returns the stable node identity.
    pub fn node_id(&self) -> &str {
        &self.node_id
    }

    /// Returns the formal remote RoboGuide Server endpoint.
    pub fn server_endpoint(&self) -> &str {
        &self.server_endpoint
    }

    /// Returns the resolved durable execution-journal directory.
    pub fn state_directory(&self) -> &Path {
        &self.state_directory
    }

    /// Returns the configured reconnect delay.
    pub const fn reconnect_delay_ms(&self) -> u64 {
        self.reconnect_delay_ms
    }

    /// Returns local systems in stable lexical identity order.
    pub const fn local_systems(&self) -> &BTreeMap<String, CompiledLocalSystem> {
        &self.local_systems
    }

    /// Returns fixed local connections in stable lexical identity order.
    pub const fn connections(&self) -> &BTreeMap<String, CompiledConnection> {
        &self.connections
    }

    /// Returns local-system health checks in stable owner order.
    pub const fn health_checks(&self) -> &BTreeMap<String, CompiledHealthCheck> {
        &self.health_checks
    }

    /// Returns canonical capabilities in stable lexical contract order.
    pub const fn capabilities(&self) -> &BTreeMap<String, CompiledCapability> {
        &self.capabilities
    }

    /// Returns Control-visible resources in stable lexical identity order.
    pub const fn resources(&self) -> &BTreeMap<String, CompiledResource> {
        &self.resources
    }

    /// Returns sensors in stable lexical identity order.
    pub const fn sensors(&self) -> &BTreeMap<String, CompiledSensor> {
        &self.sensors
    }

    /// Returns optional startup-validated Spatial Memory artifact configuration.
    pub const fn artifact_service(&self) -> Option<&CompiledArtifactService> {
        self.artifacts.as_ref()
    }
}

impl CompiledArtifactService {
    /// Returns the central artifact data-plane endpoint.
    pub fn endpoint(&self) -> &str {
        &self.endpoint
    }

    /// Returns the deployment-owned cache directory.
    pub fn cache_directory(&self) -> &Path {
        &self.cache_directory
    }

    /// Returns the maximum artifact size accepted by this node.
    pub const fn max_artifact_bytes(&self) -> u64 {
        self.max_artifact_bytes
    }

    /// Returns the bounded artifact transfer chunk size.
    pub const fn chunk_size_bytes(&self) -> usize {
        self.chunk_size_bytes
    }

    /// Returns the artifact data-plane connection timeout.
    pub const fn connect_timeout_ms(&self) -> u64 {
        self.connect_timeout_ms
    }

    /// Returns the artifact data-plane read-idle timeout.
    pub const fn read_timeout_ms(&self) -> u64 {
        self.read_timeout_ms
    }

    /// Returns validated static input bindings.
    pub const fn input_bindings(&self) -> &BTreeMap<String, ArtifactInputBindingConfig> {
        &self.input_bindings
    }

    /// Returns validated static output bindings.
    pub const fn output_bindings(&self) -> &BTreeMap<String, ArtifactOutputBindingConfig> {
        &self.output_bindings
    }
}

impl CompiledLocalSystem {
    /// Returns the stable local-system identity.
    pub fn id(&self) -> &str {
        &self.id
    }

    /// Returns the runtime name.
    pub fn runtime_name(&self) -> &str {
        &self.runtime_name
    }

    /// Returns the runtime version.
    pub fn runtime_version(&self) -> &str {
        &self.runtime_version
    }

    /// Returns non-secret registration metadata.
    pub const fn metadata(&self) -> &BTreeMap<String, String> {
        &self.metadata
    }
}

impl CompiledHealthCheck {
    /// Returns the local system observed by this check.
    pub fn owner(&self) -> &str {
        &self.owner
    }

    /// Returns the fixed health operation step.
    pub const fn step(&self) -> &CompiledWorkflowStep {
        &self.step
    }

    /// Maps one completed health-step response into a canonical fact.
    pub fn map(&self, context: &WorkflowContext) -> Result<LocalHealthFact, MappingError> {
        let response_pointer = format!("/steps/{}", escape_pointer_segment(self.step.id()));
        let response = context
            .as_json()
            .pointer(&response_pointer)
            .ok_or_else(|| MappingError::MissingSource(response_pointer.clone()))?;
        let state = response
            .pointer(&self.state_pointer)
            .and_then(serde_json::Value::as_str)
            .ok_or_else(|| MappingError::MissingSource(self.state_pointer.clone()))?;
        let key = if self.case_sensitive {
            state.to_string()
        } else {
            state.to_ascii_lowercase()
        };
        let state =
            self.states.get(&key).copied().ok_or_else(|| {
                MappingError::MissingSource(format!("unmapped health state {key}"))
            })?;
        let detail = self
            .detail_pointer
            .as_ref()
            .and_then(|pointer| response.pointer(pointer))
            .map(value_to_reason)
            .unwrap_or_default();
        Ok(LocalHealthFact { state, detail })
    }
}

impl CompiledConnection {
    /// Returns the stable connection identity.
    pub fn id(&self) -> &str {
        match self {
            Self::Http { id, .. } | Self::Grpc { id, .. } | Self::Mcp { id, .. } => id,
        }
    }

    /// Returns the owning local-system identity.
    pub fn owner(&self) -> &str {
        match self {
            Self::Http { owner, .. } | Self::Grpc { owner, .. } | Self::Mcp { owner, .. } => owner,
        }
    }

    /// Returns the driver implementation family.
    pub const fn driver_kind(&self) -> DriverKind {
        match self {
            Self::Http { .. } => DriverKind::Http,
            Self::Grpc { .. } => DriverKind::Grpc,
            Self::Mcp { .. } => DriverKind::Mcp,
        }
    }

    /// Returns the fixed local endpoint.
    pub fn endpoint(&self) -> &str {
        match self {
            Self::Http { endpoint, .. }
            | Self::Grpc { endpoint, .. }
            | Self::Mcp { endpoint, .. } => endpoint,
        }
    }

    /// Renders one fixed operation with a dynamic request body only.
    fn render_request(
        &self,
        operation: &LocalOperationConfig,
        payload: serde_json::Value,
    ) -> Result<CompiledDriverRequest, CatalogError> {
        match (self, operation) {
            (
                Self::Http {
                    endpoint,
                    timeout_ms,
                    credential_headers,
                    ..
                },
                LocalOperationConfig::Http { method, path },
            ) => Ok(CompiledDriverRequest::Http {
                endpoint: endpoint.clone(),
                method: method.clone(),
                path: path.clone(),
                credential_headers: credential_headers.clone(),
                body: payload,
                timeout_ms: *timeout_ms,
            }),
            (
                Self::Grpc {
                    endpoint,
                    descriptor_set,
                    reflection,
                    timeout_ms,
                    credential_metadata,
                    ..
                },
                LocalOperationConfig::GrpcUnary { service, method }
                | LocalOperationConfig::GrpcServerStream { service, method },
            ) => Ok(CompiledDriverRequest::Grpc {
                endpoint: endpoint.clone(),
                descriptor_set: descriptor_set.clone(),
                reflection: *reflection,
                service: service.clone(),
                method: method.clone(),
                server_streaming: matches!(
                    operation,
                    LocalOperationConfig::GrpcServerStream { .. }
                ),
                credential_metadata: credential_metadata.clone(),
                message: payload,
                timeout_ms: *timeout_ms,
            }),
            (
                Self::Mcp {
                    endpoint,
                    timeout_ms,
                    credential_headers,
                    ..
                },
                LocalOperationConfig::McpTool { tool },
            ) => Ok(CompiledDriverRequest::Mcp {
                endpoint: endpoint.clone(),
                tool: tool.clone(),
                credential_headers: credential_headers.clone(),
                arguments: payload,
                timeout_ms: *timeout_ms,
            }),
            _ => Err(validation(
                "workflow.operation",
                "operation driver does not match its compiled connection",
            )),
        }
    }
}

impl CompiledCapability {
    /// Returns the canonical capability contract.
    pub fn contract(&self) -> &str {
        &self.contract
    }

    /// Returns the coarse capability kind consumed by Control Matching.
    pub fn kind(&self) -> &str {
        &self.kind
    }

    /// Returns the sole local-system owner.
    pub fn owner(&self) -> &str {
        &self.owner
    }

    /// Returns resource identities that must be present in Control's commitment.
    pub const fn required_resources(&self) -> &BTreeSet<String> {
        &self.required_resources
    }

    /// Returns node-local concurrency lock identities.
    pub const fn local_locks(&self) -> &BTreeSet<String> {
        &self.local_locks
    }

    /// Returns the artifact action fixed for this capability, when configured.
    pub const fn artifact_operation(&self) -> Option<ArtifactOperationConfig> {
        self.artifact_operation
    }

    /// Returns immutable local execution behavior.
    pub const fn workflow(&self) -> &CompiledWorkflow {
        &self.workflow
    }
}

impl CompiledWorkflow {
    /// Returns physical/computational dispatch steps.
    pub fn execute(&self) -> &[CompiledWorkflowStep] {
        &self.execute
    }

    /// Returns status/reconciliation steps.
    pub fn status(&self) -> &[CompiledWorkflowStep] {
        &self.status
    }

    /// Returns cancellation-request steps.
    pub fn cancel(&self) -> &[CompiledWorkflowStep] {
        &self.cancel
    }

    /// Returns the configured status polling interval.
    pub const fn poll_interval_ms(&self) -> u64 {
        self.poll_interval_ms
    }

    /// Extracts the local execution handle from completed dispatch responses.
    pub fn local_handle(&self, context: &WorkflowContext) -> Result<String, MappingError> {
        evaluate(&self.local_handle, context.as_json())?
            .as_str()
            .filter(|handle| !handle.trim().is_empty())
            .map(ToString::to_string)
            .ok_or_else(|| MappingError::InvalidFunctionArguments("local_handle".to_string()))
    }

    /// Projects current local state without treating cancel acknowledgement as terminal.
    pub fn map_execution_state(
        &self,
        context: &WorkflowContext,
    ) -> Result<MappedExecutionFact, MappingError> {
        self.execution_state.map(context)
    }
}

impl CompiledWorkflowStep {
    /// Returns the stable workflow-local step identity.
    pub fn id(&self) -> &str {
        &self.id
    }

    /// Returns the fixed connection identity.
    pub fn connection(&self) -> &str {
        &self.connection
    }

    /// Renders one driver request while preserving compiled route and operation authority.
    pub fn render(
        &self,
        catalog: &CompiledLocalCatalog,
        context: &WorkflowContext,
    ) -> Result<CompiledDriverRequest, CatalogError> {
        let connection = catalog.connections.get(&self.connection).ok_or_else(|| {
            validation(
                "workflow.connection",
                format!("compiled connection `{}` is unavailable", self.connection),
            )
        })?;
        let payload = self
            .request
            .render(context)
            .map_err(|source| CatalogError::Mapping {
                step: self.id.clone(),
                source,
            })?;
        connection.render_request(&self.operation, payload)
    }
}

impl CompiledExecutionStateMapping {
    /// Maps configured local state and optional reason into one canonical fact.
    fn map(&self, context: &WorkflowContext) -> Result<MappedExecutionFact, MappingError> {
        let state = context
            .as_json()
            .pointer(&self.state_pointer)
            .and_then(serde_json::Value::as_str)
            .ok_or_else(|| MappingError::MissingSource(self.state_pointer.clone()))?;
        let normalized = if self.case_sensitive {
            state.to_string()
        } else {
            state.to_ascii_lowercase()
        };
        let phase = self
            .states
            .get(&normalized)
            .copied()
            .ok_or_else(|| MappingError::MissingSource(format!("unmapped state `{state}`")))?;
        let reason = self
            .reason_pointer
            .as_ref()
            .and_then(|pointer| context.as_json().pointer(pointer).map(value_to_reason));
        Ok(MappedExecutionFact { phase, reason })
    }
}

impl CompiledResource {
    /// Returns the stable resource identity.
    pub fn id(&self) -> &str {
        &self.id
    }

    /// Returns the transport-neutral resource kind.
    pub fn kind(&self) -> &str {
        &self.kind
    }

    /// Returns non-zero resource capacity.
    pub const fn capacity(&self) -> u32 {
        self.capacity
    }

    /// Returns the owning local-system identity.
    pub fn owner(&self) -> &str {
        &self.owner
    }

    /// Returns non-secret registration metadata.
    pub const fn metadata(&self) -> &BTreeMap<String, String> {
        &self.metadata
    }
}

impl CompiledSensor {
    /// Returns the stable sensor identity.
    pub fn id(&self) -> &str {
        &self.id
    }

    /// Returns the transport-neutral sensor kind.
    pub fn kind(&self) -> &str {
        &self.kind
    }

    /// Returns the owning local-system identity.
    pub fn owner(&self) -> &str {
        &self.owner
    }

    /// Returns non-secret registration metadata.
    pub const fn metadata(&self) -> &BTreeMap<String, String> {
        &self.metadata
    }
}

/// Compiles optional artifact settings and rejects unsafe or ambiguous static bindings.
fn compile_artifacts(
    config: Option<ArtifactServiceConfig>,
    config_directory: &Path,
) -> Result<Option<CompiledArtifactService>, CatalogError> {
    let Some(config) = config else {
        return Ok(None);
    };
    validate_server_endpoint(&config.endpoint).map_err(|error| match error {
        CatalogError::Validation { reason, .. } => validation("artifacts.endpoint", reason),
        other => other,
    })?;
    require(
        config.max_artifact_bytes > 0,
        "artifacts.max_artifact_bytes",
        "must be non-zero",
    )?;
    require(
        config.chunk_size_bytes > 0,
        "artifacts.chunk_size_bytes",
        "must be non-zero",
    )?;
    require(
        config.connect_timeout_ms > 0,
        "artifacts.connect_timeout_ms",
        "must be non-zero",
    )?;
    require(
        config.read_timeout_ms > 0,
        "artifacts.read_timeout_ms",
        "must be non-zero",
    )?;
    require(
        !config.cache_directory.as_os_str().is_empty(),
        "artifacts.cache_directory",
        "must not be empty",
    )?;
    let cache_directory = resolve_path(config_directory, &config.cache_directory);
    let mut binding_paths = BTreeMap::new();
    let mut input_bindings = BTreeMap::new();
    for binding in config.input_bindings {
        validate_artifact_binding_identity(&binding.id, "artifacts.input_bindings.id")?;
        validate_artifact_map_identity(&binding.map_id, "artifacts.input_bindings.map_id")?;
        validate_artifact_revision_identity(
            &binding.revision_id,
            "artifacts.input_bindings.revision_id",
        )?;
        validate_relative_artifact_path(
            &binding.target_path,
            "artifacts.input_bindings.target_path",
        )?;
        register_artifact_binding_path(
            &mut binding_paths,
            &binding.target_path,
            "artifacts.input_bindings.target_path",
        )?;
        if let Some(digest) = &binding.content_digest {
            validate_artifact_digest(digest, "artifacts.input_bindings.content_digest")?;
        }
        require(
            input_bindings.insert(binding.id.clone(), binding).is_none(),
            "artifacts.input_bindings.id",
            "duplicate binding identity",
        )?;
    }
    let mut output_bindings = BTreeMap::new();
    for binding in config.output_bindings {
        validate_artifact_binding_identity(&binding.id, "artifacts.output_bindings.id")?;
        require(
            !input_bindings.contains_key(&binding.id),
            "artifacts.output_bindings.id",
            "binding identity is already used by an input binding",
        )?;
        validate_artifact_map_identity(&binding.map_id, "artifacts.output_bindings.map_id")?;
        validate_artifact_revision_identity(
            &binding.revision_id,
            "artifacts.output_bindings.revision_id",
        )?;
        validate_relative_artifact_path(
            &binding.source_path,
            "artifacts.output_bindings.source_path",
        )?;
        register_artifact_binding_path(
            &mut binding_paths,
            &binding.source_path,
            "artifacts.output_bindings.source_path",
        )?;
        for (value, field) in [
            (&binding.media_type, "artifacts.output_bindings.media_type"),
            (
                &binding.format_name,
                "artifacts.output_bindings.format_name",
            ),
            (
                &binding.format_version,
                "artifacts.output_bindings.format_version",
            ),
            (&binding.root_frame, "artifacts.output_bindings.root_frame"),
            (
                &binding.coordinate_convention,
                "artifacts.output_bindings.coordinate_convention",
            ),
            (
                &binding.spatial_anchor_id,
                "artifacts.output_bindings.spatial_anchor_id",
            ),
        ] {
            validate_artifact_text(value, field)?;
        }
        if binding
            .resolution_meters
            .is_some_and(|value| !value.is_finite() || value <= 0.0)
        {
            return Err(validation(
                "artifacts.output_bindings.resolution_meters",
                "must be finite and positive",
            ));
        }
        require(
            output_bindings
                .insert(binding.id.clone(), binding)
                .is_none(),
            "artifacts.output_bindings.id",
            "duplicate binding identity",
        )?;
    }
    Ok(Some(CompiledArtifactService {
        endpoint: config.endpoint,
        cache_directory,
        max_artifact_bytes: config.max_artifact_bytes,
        chunk_size_bytes: config.chunk_size_bytes,
        connect_timeout_ms: config.connect_timeout_ms,
        read_timeout_ms: config.read_timeout_ms,
        input_bindings,
        output_bindings,
    }))
}

/// Reserves one relative artifact path and rejects aliases or file/directory overlap.
fn register_artifact_binding_path(
    paths: &mut BTreeMap<PathBuf, String>,
    path: &Path,
    field: &str,
) -> Result<(), CatalogError> {
    if let Some((existing, owner)) = paths
        .iter()
        .find(|(existing, _)| existing.starts_with(path) || path.starts_with(existing))
    {
        return Err(validation(
            field,
            format!(
                "path {} overlaps {} owned by {owner}",
                path.display(),
                existing.display()
            ),
        ));
    }
    paths.insert(path.to_path_buf(), field.to_string());
    Ok(())
}

/// Validates an artifact identity used in a URL or fixed binding.
fn validate_artifact_binding_identity(value: &str, field: &str) -> Result<(), CatalogError> {
    require(
        !value.trim().is_empty() && value.trim() == value && !value.contains(['/', '\\']),
        field,
        "must be a nonblank path-safe identity",
    )
}

/// Validates a logical map identity using the Domain path-safe selector invariant.
fn validate_artifact_map_identity(value: &str, field: &str) -> Result<(), CatalogError> {
    domain::MapId::new(value.to_string())
        .map(|_| ())
        .map_err(|error| validation(field, error.to_string()))
}

/// Validates an immutable map revision identity using the Domain path-safe selector invariant.
fn validate_artifact_revision_identity(value: &str, field: &str) -> Result<(), CatalogError> {
    domain::MapRevisionId::new(value.to_string())
        .map(|_| ())
        .map_err(|error| validation(field, error.to_string()))
}

/// Validates a nonblank opaque artifact metadata value without imposing an identity grammar.
fn validate_artifact_text(value: &str, field: &str) -> Result<(), CatalogError> {
    require(
        !value.trim().is_empty() && value.trim() == value,
        field,
        "must be nonblank and have no surrounding whitespace",
    )
}

/// Validates a deployment-owned relative artifact path.
fn validate_relative_artifact_path(path: &Path, field: &str) -> Result<(), CatalogError> {
    require(
        !path.as_os_str().is_empty() && !path.is_absolute(),
        field,
        "must be a nonempty relative file path",
    )?;
    require(
        !path.components().any(|component| {
            matches!(
                component,
                std::path::Component::CurDir
                    | std::path::Component::ParentDir
                    | std::path::Component::RootDir
                    | std::path::Component::Prefix(_)
            )
        }),
        field,
        "must not contain current-directory or parent traversal segments",
    )
}

/// Validates a plain or `sha256:`-prefixed lowercase digest.
fn validate_artifact_digest(value: &str, field: &str) -> Result<(), CatalogError> {
    let digest = value.strip_prefix("sha256:").unwrap_or(value);
    require(
        digest.len() == 64
            && digest
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte)),
        field,
        "must be a lowercase SHA-256 digest",
    )
}

/// Compiles unique local systems.
fn compile_local_systems(
    configs: Vec<LocalSystemConfig>,
) -> Result<CompiledLocalSystems, CatalogError> {
    let mut systems = BTreeMap::new();
    let mut health_checks = BTreeMap::new();
    for config in configs {
        validate_identity(&config.id, "local_systems.id")?;
        validate_identity(&config.runtime_name, "local_systems.runtime_name")?;
        validate_identity(&config.runtime_version, "local_systems.runtime_version")?;
        let id = config.id.clone();
        health_checks.insert(id.clone(), config.health);
        let system = CompiledLocalSystem {
            id: config.id,
            runtime_name: config.runtime_name,
            runtime_version: config.runtime_version,
            metadata: config.metadata,
        };
        insert_unique(&mut systems, id, system, "local_systems.id")?;
    }
    Ok((systems, health_checks))
}

/// Compiles one required health check per configured local system.
fn compile_health_checks(
    configs: BTreeMap<String, HealthCheckConfig>,
    systems: &BTreeMap<String, CompiledLocalSystem>,
    connections: &BTreeMap<String, CompiledConnection>,
) -> Result<BTreeMap<String, CompiledHealthCheck>, CatalogError> {
    let mut checks = BTreeMap::new();
    for (owner, config) in configs {
        validate_step_sources(
            std::slice::from_ref(&config.step),
            false,
            &format!("local_systems.{owner}.health"),
        )?;
        let mut step_ids = BTreeSet::new();
        for binding in &config.step.request.bindings {
            if contains_pointer_expression(&binding.value) {
                return Err(validation(
                    format!("local_systems.{owner}.health.step"),
                    "health request mappings must use deployment constants only",
                ));
            }
        }
        let mut steps = compile_steps(vec![config.step], &owner, connections, &mut step_ids)?;
        let step = steps.pop().expect("one health step compiles into one step");
        validate_pointer(&config.state_pointer).map_err(|source| CatalogError::Mapping {
            step: format!("local_systems.{owner}.health"),
            source,
        })?;
        if let Some(pointer) = &config.detail_pointer {
            validate_pointer(pointer).map_err(|source| CatalogError::Mapping {
                step: format!("local_systems.{owner}.health"),
                source,
            })?;
        }
        let mut states = BTreeMap::new();
        insert_health_states(
            &mut states,
            config.online,
            LocalHealthState::Online,
            config.case_sensitive,
        )?;
        insert_health_states(
            &mut states,
            config.degraded,
            LocalHealthState::Degraded,
            config.case_sensitive,
        )?;
        insert_health_states(
            &mut states,
            config.offline,
            LocalHealthState::Offline,
            config.case_sensitive,
        )?;
        require(
            states
                .values()
                .any(|state| *state == LocalHealthState::Online)
                && states
                    .values()
                    .any(|state| *state == LocalHealthState::Degraded)
                && states
                    .values()
                    .any(|state| *state == LocalHealthState::Offline),
            format!("local_systems.{owner}.health"),
            "online, degraded, and offline mappings must all be nonempty",
        )?;
        checks.insert(
            owner.clone(),
            CompiledHealthCheck {
                owner,
                step,
                state_pointer: config.state_pointer,
                detail_pointer: config.detail_pointer,
                states,
                case_sensitive: config.case_sensitive,
            },
        );
    }
    require(
        checks.len() == systems.len(),
        "local_systems.health",
        "every local system must have one health check",
    )?;
    Ok(checks)
}

/// Returns whether an expression reads dynamic invocation or prior workflow context.
fn contains_pointer_expression(expression: &crate::ValueExpressionConfig) -> bool {
    match expression {
        crate::ValueExpressionConfig::Pointer { .. } => true,
        crate::ValueExpressionConfig::Constant { .. } => false,
        crate::ValueExpressionConfig::Function { arguments, .. } => {
            arguments.iter().any(contains_pointer_expression)
        }
    }
}

/// Inserts one health phase while rejecting ambiguous local values.
fn insert_health_states(
    states: &mut BTreeMap<String, LocalHealthState>,
    values: Vec<String>,
    state: LocalHealthState,
    case_sensitive: bool,
) -> Result<(), CatalogError> {
    for value in values {
        validate_identity(&value, "local_systems.health.state")?;
        let key = if case_sensitive {
            value
        } else {
            value.to_ascii_lowercase()
        };
        require(
            states.insert(key.clone(), state).is_none(),
            "local_systems.health",
            format!("local health state `{key}` has multiple mappings"),
        )?;
    }
    Ok(())
}

/// Compiles fixed connections and resolves descriptor paths.
fn compile_connections(
    configs: Vec<ConnectionConfig>,
    systems: &BTreeMap<String, CompiledLocalSystem>,
    config_directory: &Path,
) -> Result<BTreeMap<String, CompiledConnection>, CatalogError> {
    let mut connections = BTreeMap::new();
    for config in configs {
        validate_identity(config.id(), "connections.id")?;
        require(
            systems.contains_key(config.local_system()),
            format!("connections.{}.local_system", config.id()),
            format!("unknown local system `{}`", config.local_system()),
        )?;
        validate_local_endpoint(config.endpoint(), "connections.endpoint")?;
        let (id, connection) = match config {
            ConnectionConfig::Http {
                id,
                local_system,
                endpoint,
                timeout_ms,
                headers,
            } => {
                require(timeout_ms > 0, "connections.timeout_ms", "must be non-zero")?;
                let headers = compile_credentials(headers, "connections.headers")?;
                let key = id.clone();
                (
                    key,
                    CompiledConnection::Http {
                        id,
                        owner: local_system,
                        endpoint,
                        timeout_ms,
                        credential_headers: headers,
                    },
                )
            }
            ConnectionConfig::Grpc {
                id,
                local_system,
                endpoint,
                descriptor_set,
                reflection,
                timeout_ms,
                metadata,
            } => {
                require(timeout_ms > 0, "connections.timeout_ms", "must be non-zero")?;
                require(
                    descriptor_set.is_some() ^ reflection,
                    format!("connections.{id}.descriptor_set"),
                    "configure exactly one descriptor_set or reflection=true",
                )?;
                let descriptor_set = descriptor_set
                    .map(|path| resolve_path(config_directory, &path))
                    .map(|path| {
                        require(
                            path.is_file(),
                            format!("connections.{id}.descriptor_set"),
                            format!("file `{}` does not exist", path.display()),
                        )?;
                        Ok::<_, CatalogError>(path)
                    })
                    .transpose()?;
                let metadata = compile_credentials(metadata, "connections.metadata")?;
                let key = id.clone();
                (
                    key,
                    CompiledConnection::Grpc {
                        id,
                        owner: local_system,
                        endpoint,
                        descriptor_set,
                        reflection,
                        timeout_ms,
                        credential_metadata: metadata,
                    },
                )
            }
            ConnectionConfig::Mcp {
                id,
                local_system,
                endpoint,
                timeout_ms,
                headers,
            } => {
                require(timeout_ms > 0, "connections.timeout_ms", "must be non-zero")?;
                let headers = compile_credentials(headers, "connections.headers")?;
                let key = id.clone();
                (
                    key,
                    CompiledConnection::Mcp {
                        id,
                        owner: local_system,
                        endpoint,
                        timeout_ms,
                        credential_headers: headers,
                    },
                )
            }
        };
        insert_unique(&mut connections, id, connection, "connections.id")?;
    }
    Ok(connections)
}

/// Compiles unique resources and validates ownership and capacity.
fn compile_resources(
    configs: Vec<ResourceConfig>,
    systems: &BTreeMap<String, CompiledLocalSystem>,
) -> Result<BTreeMap<String, CompiledResource>, CatalogError> {
    let mut resources = BTreeMap::new();
    for config in configs {
        validate_identity(&config.id, "resources.id")?;
        validate_identity(&config.kind, "resources.kind")?;
        require(
            matches!(config.kind.as_str(), "space" | "compute" | "time"),
            format!("resources.{}.kind", config.id),
            "must be space, compute, or time",
        )?;
        require(
            config.capacity > 0,
            format!("resources.{}.capacity", config.id),
            "must be non-zero",
        )?;
        require(
            systems.contains_key(&config.owner),
            format!("resources.{}.owner", config.id),
            format!("unknown local system `{}`", config.owner),
        )?;
        let id = config.id.clone();
        let resource = CompiledResource {
            id: config.id,
            kind: config.kind,
            capacity: config.capacity,
            owner: config.owner,
            metadata: config.metadata,
        };
        insert_unique(&mut resources, id, resource, "resources.id")?;
    }
    Ok(resources)
}

/// Compiles unique sensors and validates ownership.
fn compile_sensors(
    configs: Vec<SensorConfig>,
    systems: &BTreeMap<String, CompiledLocalSystem>,
) -> Result<BTreeMap<String, CompiledSensor>, CatalogError> {
    let mut sensors = BTreeMap::new();
    for config in configs {
        validate_identity(&config.id, "sensors.id")?;
        validate_identity(&config.kind, "sensors.kind")?;
        require(
            systems.contains_key(&config.owner),
            format!("sensors.{}.owner", config.id),
            format!("unknown local system `{}`", config.owner),
        )?;
        let id = config.id.clone();
        let sensor = CompiledSensor {
            id: config.id,
            kind: config.kind,
            owner: config.owner,
            metadata: config.metadata,
        };
        insert_unique(&mut sensors, id, sensor, "sensors.id")?;
    }
    Ok(sensors)
}

/// Compiles canonical capability owners and their local workflows.
fn compile_capabilities(
    configs: Vec<CapabilityBindingConfig>,
    systems: &BTreeMap<String, CompiledLocalSystem>,
    connections: &BTreeMap<String, CompiledConnection>,
    resources: &BTreeMap<String, CompiledResource>,
    supports_artifacts: bool,
) -> Result<BTreeMap<String, CompiledCapability>, CatalogError> {
    let mut capabilities = BTreeMap::new();
    for config in configs {
        validate_contract(&config.contract)?;
        require(
            matches!(
                config.kind.as_str(),
                "mobility" | "transport" | "compute" | "observation"
            ),
            format!("capabilities.{}.kind", config.contract),
            "must be mobility, transport, compute, or observation",
        )?;
        require(
            !capabilities.contains_key(&config.contract),
            "capabilities.contract",
            format!("duplicate identity `{}`", config.contract),
        )?;
        require(
            systems.contains_key(&config.owner),
            format!("capabilities.{}.owner", config.contract),
            format!("unknown local system `{}`", config.owner),
        )?;
        let required_resources = unique_nonblank(
            config.required_resources,
            &format!("capabilities.{}.required_resources", config.contract),
        )?;
        for resource_id in &required_resources {
            let resource = resources.get(resource_id).ok_or_else(|| {
                validation(
                    format!("capabilities.{}.required_resources", config.contract),
                    format!("unknown resource `{resource_id}`"),
                )
            })?;
            require(
                resource.owner == config.owner,
                format!("capabilities.{}.required_resources", config.contract),
                format!("resource `{resource_id}` belongs to `{}`", resource.owner),
            )?;
        }
        let local_locks = unique_nonblank(
            config.local_locks,
            &format!("capabilities.{}.local_locks", config.contract),
        )?;
        require(
            supports_artifacts || config.artifact_operation.is_none(),
            format!("capabilities.{}.artifact_operation", config.contract),
            format!("requires schema `{CONFIG_SCHEMA_V0_3}`"),
        )?;
        let workflow = compile_workflow(config.workflow, &config.owner, connections)?;
        let contract = config.contract.clone();
        let capability = CompiledCapability {
            contract: config.contract,
            kind: config.kind,
            owner: config.owner,
            required_resources,
            local_locks,
            artifact_operation: config.artifact_operation,
            workflow,
        };
        insert_unique(
            &mut capabilities,
            contract,
            capability,
            "capabilities.contract",
        )?;
    }
    Ok(capabilities)
}

/// Compiles one complete workflow and enforces unique step identities.
fn compile_workflow(
    config: WorkflowConfig,
    owner: &str,
    connections: &BTreeMap<String, CompiledConnection>,
) -> Result<CompiledWorkflow, CatalogError> {
    validate_expression(&config.local_handle).map_err(|source| CatalogError::Mapping {
        step: "local_handle".to_string(),
        source,
    })?;
    require(
        !config.execute.is_empty(),
        "workflow.execute",
        "must not be empty",
    )?;
    require(
        !config.status.is_empty(),
        "workflow.status",
        "must not be empty",
    )?;
    require(
        !config.cancel.is_empty(),
        "workflow.cancel",
        "must not be empty",
    )?;
    require(
        config.poll_interval_ms > 0,
        "workflow.poll_interval_ms",
        "must be non-zero",
    )?;
    validate_step_sources(&config.execute, false, "workflow.execute")?;
    validate_step_sources(&config.status, true, "workflow.status")?;
    validate_step_sources(&config.cancel, true, "workflow.cancel")?;
    let execute_ids = config
        .execute
        .iter()
        .map(|step| step.id.as_str())
        .collect::<BTreeSet<_>>();
    validate_handle_expression(&config.local_handle, &execute_ids, "workflow.local_handle")?;
    let status_ids = config
        .status
        .iter()
        .map(|step| step.id.as_str())
        .collect::<BTreeSet<_>>();
    validate_status_pointer(
        &config.execution_state.state_pointer,
        &status_ids,
        "execution_state.state_pointer",
    )?;
    if let Some(pointer) = &config.execution_state.reason_pointer {
        validate_status_pointer(pointer, &status_ids, "execution_state.reason_pointer")?;
    }
    let mut step_ids = BTreeSet::new();
    let execute = compile_steps(config.execute, owner, connections, &mut step_ids)?;
    let status = compile_steps(config.status, owner, connections, &mut step_ids)?;
    let cancel = compile_steps(config.cancel, owner, connections, &mut step_ids)?;
    let execution_state = compile_execution_state(config.execution_state)?;
    Ok(CompiledWorkflow {
        execute,
        status,
        cancel,
        local_handle: config.local_handle,
        poll_interval_ms: config.poll_interval_ms,
        execution_state,
    })
}

/// Requires the durable local handle to derive from a completed execute response.
fn validate_handle_expression(
    expression: &crate::ValueExpressionConfig,
    execute_steps: &BTreeSet<&str>,
    field: &str,
) -> Result<(), CatalogError> {
    match expression {
        crate::ValueExpressionConfig::Pointer { pointer }
            if execute_steps
                .iter()
                .any(|step| pointer_targets_step(pointer, step)) =>
        {
            Ok(())
        }
        crate::ValueExpressionConfig::Function { arguments, .. } => {
            for argument in arguments {
                validate_handle_expression(argument, execute_steps, field)?;
            }
            Ok(())
        }
        _ => Err(validation(
            field,
            "must derive from a configured execute-step response",
        )),
    }
}

/// Validates request expressions against only facts available before each ordered step.
fn validate_step_sources(
    steps: &[WorkflowStepConfig],
    local_handle_available: bool,
    field: &str,
) -> Result<(), CatalogError> {
    let mut completed = BTreeSet::new();
    for step in steps {
        for binding in &step.request.bindings {
            validate_expression_sources(
                &binding.value,
                local_handle_available,
                &completed,
                &format!("{field}.{}.request", step.id),
            )?;
        }
        completed.insert(step.id.as_str());
    }
    Ok(())
}

/// Validates one expression tree without allowing future or cross-phase step references.
fn validate_expression_sources(
    expression: &crate::ValueExpressionConfig,
    local_handle_available: bool,
    completed_steps: &BTreeSet<&str>,
    field: &str,
) -> Result<(), CatalogError> {
    match expression {
        crate::ValueExpressionConfig::Constant { .. } => Ok(()),
        crate::ValueExpressionConfig::Pointer { pointer } => {
            if pointer == "/invocation" || pointer.starts_with("/invocation/") {
                return Ok(());
            }
            if pointer == "/artifacts" || pointer.starts_with("/artifacts/") {
                return Ok(());
            }
            if local_handle_available
                && (pointer == "/local_handle" || pointer.starts_with("/local_handle/"))
            {
                return Ok(());
            }
            if completed_steps
                .iter()
                .any(|step| pointer_targets_step(pointer, step))
            {
                return Ok(());
            }
            Err(validation(
                field,
                format!("mapping source `{pointer}` is unavailable at this step"),
            ))
        }
        crate::ValueExpressionConfig::Function { arguments, .. } => {
            for argument in arguments {
                validate_expression_sources(
                    argument,
                    local_handle_available,
                    completed_steps,
                    field,
                )?;
            }
            Ok(())
        }
    }
}

/// Requires execution state mappings to read a status-step response.
fn validate_status_pointer(
    pointer: &str,
    status_steps: &BTreeSet<&str>,
    field: &str,
) -> Result<(), CatalogError> {
    require(
        status_steps
            .iter()
            .any(|step| pointer_targets_step(pointer, step)),
        field,
        "must reference a configured status step",
    )
}

/// Returns whether one JSON Pointer targets a specific configured step.
fn pointer_targets_step(pointer: &str, step: &str) -> bool {
    let escaped = escape_pointer_segment(step);
    let prefix = format!("/steps/{escaped}");
    pointer == prefix || pointer.starts_with(&format!("{prefix}/"))
}

/// Escapes one JSON Pointer path segment.
fn escape_pointer_segment(value: &str) -> String {
    value.replace('~', "~0").replace('/', "~1")
}

/// Compiles an ordered step list against connection ownership and driver kind.
fn compile_steps(
    configs: Vec<WorkflowStepConfig>,
    owner: &str,
    connections: &BTreeMap<String, CompiledConnection>,
    step_ids: &mut BTreeSet<String>,
) -> Result<Vec<CompiledWorkflowStep>, CatalogError> {
    let mut steps = Vec::with_capacity(configs.len());
    for config in configs {
        validate_identity(&config.id, "workflow.step.id")?;
        require(
            step_ids.insert(config.id.clone()),
            "workflow.step.id",
            format!("duplicate step `{}`", config.id),
        )?;
        let connection = connections.get(&config.connection).ok_or_else(|| {
            validation(
                format!("workflow.{}.connection", config.id),
                format!("unknown connection `{}`", config.connection),
            )
        })?;
        require(
            connection.owner() == owner,
            format!("workflow.{}.connection", config.id),
            format!(
                "connection `{}` belongs to `{}`, not capability owner `{owner}`",
                config.connection,
                connection.owner()
            ),
        )?;
        validate_operation(&config.operation, connection, &config.id)?;
        let request = CompiledRequestMapping::compile(config.request).map_err(|source| {
            CatalogError::Mapping {
                step: config.id.clone(),
                source,
            }
        })?;
        steps.push(CompiledWorkflowStep {
            id: config.id,
            connection: config.connection,
            operation: config.operation,
            request,
        });
    }
    Ok(steps)
}

/// Validates fixed operation syntax and its connection driver family.
fn validate_operation(
    operation: &LocalOperationConfig,
    connection: &CompiledConnection,
    step_id: &str,
) -> Result<(), CatalogError> {
    let field = format!("workflow.{step_id}.operation");
    match (connection.driver_kind(), operation) {
        (DriverKind::Http, LocalOperationConfig::Http { method, path }) => {
            require(
                matches!(method.as_str(), "GET" | "POST" | "PUT" | "PATCH" | "DELETE"),
                &field,
                "HTTP method must be GET, POST, PUT, PATCH, or DELETE",
            )?;
            require(
                path.starts_with('/')
                    && !path.contains(['{', '}', '$', '?', '#'])
                    && !path.contains(".."),
                &field,
                "HTTP path must be a fixed absolute path without templates, query, or traversal",
            )
        }
        (
            DriverKind::Grpc,
            LocalOperationConfig::GrpcUnary { service, method }
            | LocalOperationConfig::GrpcServerStream { service, method },
        ) => {
            validate_fixed_symbol(service, &field, true)?;
            validate_fixed_symbol(method, &field, false)
        }
        (DriverKind::Mcp, LocalOperationConfig::McpTool { tool }) => {
            validate_fixed_symbol(tool, &field, true)
        }
        _ => Err(validation(
            field,
            "operation kind does not match connection driver",
        )),
    }
}

/// Compiles a disjoint local-state lookup table.
fn compile_execution_state(
    config: ExecutionStateMappingConfig,
) -> Result<CompiledExecutionStateMapping, CatalogError> {
    validate_pointer(&config.state_pointer).map_err(|source| CatalogError::Mapping {
        step: "execution_state".to_string(),
        source,
    })?;
    if let Some(reason_pointer) = &config.reason_pointer {
        validate_pointer(reason_pointer).map_err(|source| CatalogError::Mapping {
            step: "execution_state".to_string(),
            source,
        })?;
    }
    require(
        !config.running.is_empty(),
        "execution_state.running",
        "must not be empty",
    )?;
    require(
        !config.completed.is_empty(),
        "execution_state.completed",
        "must not be empty",
    )?;
    require(
        !config.failed.is_empty(),
        "execution_state.failed",
        "must not be empty",
    )?;
    require(
        !config.cancelled.is_empty(),
        "execution_state.cancelled",
        "must not be empty",
    )?;
    let mut states = BTreeMap::new();
    insert_states(
        &mut states,
        config.accepted,
        MappedExecutionPhase::Accepted,
        config.case_sensitive,
    )?;
    insert_states(
        &mut states,
        config.running,
        MappedExecutionPhase::Running,
        config.case_sensitive,
    )?;
    insert_states(
        &mut states,
        config.completed,
        MappedExecutionPhase::Completed,
        config.case_sensitive,
    )?;
    insert_states(
        &mut states,
        config.failed,
        MappedExecutionPhase::Failed,
        config.case_sensitive,
    )?;
    insert_states(
        &mut states,
        config.cancelled,
        MappedExecutionPhase::Cancelled,
        config.case_sensitive,
    )?;
    Ok(CompiledExecutionStateMapping {
        state_pointer: config.state_pointer,
        reason_pointer: config.reason_pointer,
        states,
        case_sensitive: config.case_sensitive,
    })
}

/// Inserts one phase's local states while rejecting ambiguous mappings.
fn insert_states(
    states: &mut BTreeMap<String, MappedExecutionPhase>,
    values: Vec<String>,
    phase: MappedExecutionPhase,
    case_sensitive: bool,
) -> Result<(), CatalogError> {
    for value in values {
        validate_identity(&value, "execution_state.value")?;
        let key = if case_sensitive {
            value
        } else {
            value.to_ascii_lowercase()
        };
        require(
            states.insert(key.clone(), phase).is_none(),
            "execution_state",
            format!("local state `{key}` maps to more than one phase"),
        )?;
    }
    Ok(())
}

/// Compiles environment-only credentials without reading or retaining their values.
fn compile_credentials(
    configs: BTreeMap<String, crate::CredentialSourceConfig>,
    field: &str,
) -> Result<BTreeMap<String, String>, CatalogError> {
    configs
        .into_iter()
        .map(|(name, source)| {
            validate_identity(&name, field)?;
            validate_identity(&source.env, field)?;
            Ok((name, source.env))
        })
        .collect()
}

/// Validates a remote RoboGuide Server endpoint without requiring loopback.
fn validate_server_endpoint(endpoint: &str) -> Result<(), CatalogError> {
    let url = url::Url::parse(endpoint)
        .map_err(|error| validation("server_endpoint", error.to_string()))?;
    require(
        matches!(url.scheme(), "http" | "https") && url.host().is_some(),
        "server_endpoint",
        "must be an absolute http(s) gRPC endpoint",
    )
}

/// Validates a local endpoint and forbids configuration-driven remote calls.
fn validate_local_endpoint(endpoint: &str, field: &str) -> Result<(), CatalogError> {
    require(
        !endpoint.contains(['{', '}', '$']),
        field,
        "must be fixed and cannot contain template syntax",
    )?;
    let url = url::Url::parse(endpoint).map_err(|error| validation(field, error.to_string()))?;
    if url.scheme() == "unix" {
        return require(
            url.path().starts_with('/') && !url.path().is_empty(),
            field,
            "Unix endpoint must use an absolute socket path",
        );
    }
    require(
        matches!(url.scheme(), "http" | "https"),
        field,
        "must use http(s) or unix scheme",
    )?;
    require(
        url.username().is_empty()
            && url.password().is_none()
            && url.query().is_none()
            && url.fragment().is_none(),
        field,
        "must not contain inline credentials, query, or fragment",
    )?;
    let loopback = match url.host() {
        Some(url::Host::Domain(host)) => host.eq_ignore_ascii_case("localhost"),
        Some(url::Host::Ipv4(address)) => address.is_loopback(),
        Some(url::Host::Ipv6(address)) => address.is_loopback(),
        None => false,
    };
    require(
        loopback,
        field,
        "must target localhost or a loopback address",
    )
}

/// Validates canonical `namespace.name@version` identity.
fn validate_contract(contract: &str) -> Result<(), CatalogError> {
    let Some((qualified_name, version)) = contract.split_once('@') else {
        return Err(validation(
            "capabilities.contract",
            "must use namespace.name@version",
        ));
    };
    let Some((namespace, name)) = qualified_name.rsplit_once('.') else {
        return Err(validation(
            "capabilities.contract",
            "must use namespace.name@version",
        ));
    };
    require(
        !namespace.trim().is_empty()
            && !name.trim().is_empty()
            && !version.trim().is_empty()
            && !version.contains('@'),
        "capabilities.contract",
        "must contain non-empty namespace, name, and version",
    )
}

/// Validates a fixed service, method, or tool symbol without templates.
fn validate_fixed_symbol(value: &str, field: &str, allow_dots: bool) -> Result<(), CatalogError> {
    let valid = !value.is_empty()
        && value.chars().all(|character| {
            character.is_ascii_alphanumeric()
                || character == '_'
                || (allow_dots && matches!(character, '.' | '-' | '/'))
        });
    require(
        valid,
        field,
        "contains invalid or dynamic symbol characters",
    )
}

/// Validates a non-empty identity without surrounding whitespace.
fn validate_identity(value: &str, field: &str) -> Result<(), CatalogError> {
    require(
        !value.trim().is_empty() && value.trim() == value,
        field,
        "must be non-empty and have no surrounding whitespace",
    )
}

/// Returns a unique set of validated configured identities.
fn unique_nonblank(values: Vec<String>, field: &str) -> Result<BTreeSet<String>, CatalogError> {
    let mut unique = BTreeSet::new();
    for value in values {
        validate_identity(&value, field)?;
        require(
            unique.insert(value.clone()),
            field,
            format!("duplicate value `{value}`"),
        )?;
    }
    Ok(unique)
}

/// Inserts one unique key into a compiled catalog map.
fn insert_unique<T>(
    values: &mut BTreeMap<String, T>,
    key: String,
    value: T,
    field: &str,
) -> Result<(), CatalogError> {
    require(
        values.insert(key.clone(), value).is_none(),
        field,
        format!("duplicate identity `{key}`"),
    )
}

/// Resolves one deployment path without requiring it to already exist.
fn resolve_path(directory: &Path, path: &Path) -> PathBuf {
    if path.is_absolute() {
        path.to_path_buf()
    } else {
        directory.join(path)
    }
}

/// Converts a structured reason value into deterministic human-readable text.
fn value_to_reason(value: &serde_json::Value) -> String {
    value
        .as_str()
        .map(ToString::to_string)
        .unwrap_or_else(|| value.to_string())
}

/// Creates one validation error.
fn validation(field: impl Into<String>, reason: impl Into<String>) -> CatalogError {
    CatalogError::Validation {
        field: field.into(),
        reason: reason.into(),
    }
}

/// Returns success when an invariant holds or a field-specific error otherwise.
fn require(
    condition: bool,
    field: impl Into<String>,
    reason: impl Into<String>,
) -> Result<(), CatalogError> {
    if condition {
        Ok(())
    } else {
        Err(validation(field, reason))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{RequestBindingConfig, RequestMappingConfig, ValueExpressionConfig};

    /// Returns a valid multi-system catalog fixture with all supported drivers.
    fn valid_config(descriptor_set: PathBuf) -> NodeServiceConfig {
        let state_mapping = ExecutionStateMappingConfig {
            state_pointer: "/steps/read-state/state".to_string(),
            reason_pointer: Some("/steps/read-state/detail".to_string()),
            accepted: vec!["ACCEPTED".to_string()],
            running: vec!["RUNNING".to_string()],
            completed: vec!["SUCCEEDED".to_string()],
            failed: vec!["FAILED".to_string()],
            cancelled: vec!["CANCELED".to_string()],
            case_sensitive: false,
        };
        let step =
            |id: &str, connection: &str, operation: LocalOperationConfig| WorkflowStepConfig {
                id: id.to_string(),
                connection: connection.to_string(),
                operation,
                request: RequestMappingConfig::default(),
            };
        NodeServiceConfig {
            schema: CONFIG_SCHEMA_V0_2.to_string(),
            node_id: "dog-a".to_string(),
            server_endpoint: "http://192.0.2.10:50051".to_string(),
            state_directory: PathBuf::from("state"),
            reconnect_delay_ms: 100,
            local_systems: vec![
                LocalSystemConfig {
                    id: "motion".to_string(),
                    runtime_name: "local-motion".to_string(),
                    runtime_version: "1.0".to_string(),
                    metadata: BTreeMap::new(),
                    health: HealthCheckConfig {
                        step: step(
                            "motion-health",
                            "motion-http",
                            LocalOperationConfig::Http {
                                method: "GET".to_string(),
                                path: "/health".to_string(),
                            },
                        ),
                        state_pointer: "/state".to_string(),
                        detail_pointer: Some("/detail".to_string()),
                        online: vec!["ONLINE".to_string()],
                        degraded: vec!["DEGRADED".to_string()],
                        offline: vec!["OFFLINE".to_string()],
                        case_sensitive: false,
                    },
                },
                LocalSystemConfig {
                    id: "perception".to_string(),
                    runtime_name: "local-perception".to_string(),
                    runtime_version: "2.0".to_string(),
                    metadata: BTreeMap::new(),
                    health: HealthCheckConfig {
                        step: step(
                            "perception-health",
                            "perception-mcp",
                            LocalOperationConfig::McpTool {
                                tool: "health".to_string(),
                            },
                        ),
                        state_pointer: "/state".to_string(),
                        detail_pointer: None,
                        online: vec!["ONLINE".to_string()],
                        degraded: vec!["DEGRADED".to_string()],
                        offline: vec!["OFFLINE".to_string()],
                        case_sensitive: false,
                    },
                },
            ],
            connections: vec![
                ConnectionConfig::Http {
                    id: "motion-http".to_string(),
                    local_system: "motion".to_string(),
                    endpoint: "http://127.0.0.1:8100".to_string(),
                    timeout_ms: 1_000,
                    headers: BTreeMap::new(),
                },
                ConnectionConfig::Grpc {
                    id: "motion-grpc".to_string(),
                    local_system: "motion".to_string(),
                    endpoint: "http://[::1]:8200".to_string(),
                    descriptor_set: Some(descriptor_set),
                    reflection: false,
                    timeout_ms: 1_000,
                    metadata: BTreeMap::new(),
                },
                ConnectionConfig::Mcp {
                    id: "perception-mcp".to_string(),
                    local_system: "perception".to_string(),
                    endpoint: "http://localhost:8300/mcp".to_string(),
                    timeout_ms: 1_000,
                    headers: BTreeMap::new(),
                },
            ],
            capabilities: vec![CapabilityBindingConfig {
                contract: "mobility.reach_region@v1".to_string(),
                kind: "mobility".to_string(),
                owner: "motion".to_string(),
                required_resources: vec!["base".to_string()],
                local_locks: vec!["locomotion".to_string()],
                artifact_operation: None,
                workflow: WorkflowConfig {
                    execute: vec![step(
                        "dispatch",
                        "motion-http",
                        LocalOperationConfig::Http {
                            method: "POST".to_string(),
                            path: "/navigation/reach".to_string(),
                        },
                    )],
                    status: vec![step(
                        "read-state",
                        "motion-grpc",
                        LocalOperationConfig::GrpcUnary {
                            service: "local.Navigation".to_string(),
                            method: "GetStatus".to_string(),
                        },
                    )],
                    cancel: vec![step(
                        "request-cancel",
                        "motion-http",
                        LocalOperationConfig::Http {
                            method: "POST".to_string(),
                            path: "/navigation/cancel".to_string(),
                        },
                    )],
                    local_handle: ValueExpressionConfig::Pointer {
                        pointer: "/steps/dispatch/run_id".to_string(),
                    },
                    poll_interval_ms: 50,
                    execution_state: state_mapping,
                },
            }],
            resources: vec![ResourceConfig {
                id: "base".to_string(),
                kind: "space".to_string(),
                capacity: 1,
                owner: "motion".to_string(),
                metadata: BTreeMap::new(),
            }],
            sensors: vec![SensorConfig {
                id: "front-camera".to_string(),
                kind: "camera".to_string(),
                owner: "perception".to_string(),
                metadata: BTreeMap::new(),
            }],
            artifacts: None,
        }
    }

    /// Catalog compiles multiple local systems and all generic driver configurations.
    #[test]
    fn compiles_multi_system_generic_catalog() {
        let directory = tempfile::tempdir().expect("temporary directory exists");
        let descriptor = directory.path().join("local.pb");
        std::fs::write(&descriptor, b"descriptor fixture").expect("descriptor writes");
        let catalog = CompiledLocalCatalog::compile(valid_config(descriptor), directory.path())
            .expect("catalog compiles");
        assert_eq!(catalog.local_systems().len(), 2);
        assert_eq!(catalog.connections().len(), 3);
        assert_eq!(catalog.resources()["base"].owner(), "motion");
        assert_eq!(catalog.sensors()["front-camera"].owner(), "perception");
        assert_eq!(
            catalog.capabilities()["mobility.reach_region@v1"].owner(),
            "motion"
        );
        assert_eq!(
            catalog.capabilities()["mobility.reach_region@v1"].artifact_operation(),
            None
        );
    }

    /// Artifact operations are typed in v0.3 and rejected under the v0.2 schema.
    #[test]
    fn gates_typed_capability_artifact_operations_by_schema() {
        let directory = tempfile::tempdir().expect("temporary directory exists");
        let descriptor = directory.path().join("local.pb");
        std::fs::write(&descriptor, b"descriptor fixture").expect("descriptor writes");
        for operation in [
            ArtifactOperationConfig::PrepareOutput,
            ArtifactOperationConfig::Publish,
            ArtifactOperationConfig::Import,
            ArtifactOperationConfig::Verify,
        ] {
            let mut config = valid_config(descriptor.clone());
            config.capabilities[0].artifact_operation = Some(operation);
            assert!(matches!(
                CompiledLocalCatalog::compile(config.clone(), directory.path()),
                Err(CatalogError::Validation { field, .. })
                    if field == "capabilities.mobility.reach_region@v1.artifact_operation"
            ));

            config.schema = CONFIG_SCHEMA_V0_3.to_string();
            let catalog = CompiledLocalCatalog::compile(config, directory.path())
                .expect("v0.3 artifact operation compiles");
            assert_eq!(
                catalog.capabilities()["mobility.reach_region@v1"].artifact_operation(),
                Some(operation)
            );
        }
    }

    /// v0.3 compiles static artifact bindings without changing local workflow ownership.
    #[test]
    fn compiles_spatial_artifact_bindings() {
        let directory = tempfile::tempdir().expect("temporary directory exists");
        let descriptor = directory.path().join("local.pb");
        std::fs::write(&descriptor, b"descriptor fixture").expect("descriptor writes");
        let mut config = valid_config(descriptor);
        config.artifacts = Some(ArtifactServiceConfig {
            endpoint: "http://127.0.0.1:8090".to_string(),
            cache_directory: PathBuf::from("artifact-cache"),
            max_artifact_bytes: 1024,
            chunk_size_bytes: 64,
            connect_timeout_ms: 1_234,
            read_timeout_ms: 5_678,
            input_bindings: vec![ArtifactInputBindingConfig {
                id: "lab-map-input".to_string(),
                map_id: "lab-map".to_string(),
                revision_id: "r1".to_string(),
                content_digest: Some(format!("sha256:{}", "a".repeat(64))),
                target_path: PathBuf::from("inputs/lab.map"),
            }],
            output_bindings: vec![ArtifactOutputBindingConfig {
                id: "lab-map-output".to_string(),
                map_id: "lab-map".to_string(),
                revision_id: "r2".to_string(),
                source_path: PathBuf::from("outputs/lab.map"),
                media_type: "application/octet-stream".to_string(),
                format_name: "nav2-map-bundle".to_string(),
                format_version: "bundle-v1".to_string(),
                root_frame: "map".to_string(),
                coordinate_convention: "enu".to_string(),
                spatial_anchor_id: "lab-origin".to_string(),
                resolution_meters: Some(0.05),
            }],
        });
        assert!(matches!(
            CompiledLocalCatalog::compile(config.clone(), directory.path()),
            Err(CatalogError::Validation { field, .. }) if field == "artifacts"
        ));
        config.schema = CONFIG_SCHEMA_V0_3.to_string();
        let mut overlapping = config.clone();
        overlapping
            .artifacts
            .as_mut()
            .expect("artifacts are present")
            .output_bindings[0]
            .source_path = PathBuf::from("inputs/lab.map");
        assert!(matches!(
            CompiledLocalCatalog::compile(overlapping, directory.path()),
            Err(CatalogError::Validation { field, .. })
                if field == "artifacts.output_bindings.source_path"
        ));
        let mut invalid_selector = config.clone();
        invalid_selector
            .artifacts
            .as_mut()
            .expect("artifacts are present")
            .input_bindings[0]
            .map_id = "../lab-map".to_string();
        assert!(matches!(
            CompiledLocalCatalog::compile(invalid_selector, directory.path()),
            Err(CatalogError::Validation { field, .. })
                if field == "artifacts.input_bindings.map_id"
        ));
        let mut duplicate_binding = config.clone();
        duplicate_binding
            .artifacts
            .as_mut()
            .expect("artifacts are present")
            .output_bindings[0]
            .id = "lab-map-input".to_string();
        assert!(matches!(
            CompiledLocalCatalog::compile(duplicate_binding, directory.path()),
            Err(CatalogError::Validation { field, .. })
                if field == "artifacts.output_bindings.id"
        ));
        for (field, connect_timeout_ms, read_timeout_ms) in [
            ("artifacts.connect_timeout_ms", 0, 5_678),
            ("artifacts.read_timeout_ms", 1_234, 0),
        ] {
            let mut invalid = config.clone();
            let artifacts = invalid.artifacts.as_mut().expect("artifacts are present");
            artifacts.connect_timeout_ms = connect_timeout_ms;
            artifacts.read_timeout_ms = read_timeout_ms;
            assert!(matches!(
                CompiledLocalCatalog::compile(invalid, directory.path()),
                Err(CatalogError::Validation { field: actual, .. }) if actual == field
            ));
        }
        let catalog =
            CompiledLocalCatalog::compile(config, directory.path()).expect("v0.3 catalog compiles");
        let artifacts = catalog.artifact_service().expect("artifacts are present");
        assert_eq!(artifacts.input_bindings().len(), 1);
        assert_eq!(artifacts.output_bindings().len(), 1);
        assert!(artifacts.cache_directory().ends_with("artifact-cache"));
        assert_eq!(artifacts.connect_timeout_ms(), 1_234);
        assert_eq!(artifacts.read_timeout_ms(), 5_678);
    }

    /// Artifact bindings reject traversal before any local workflow can use a path.
    #[test]
    fn rejects_spatial_artifact_path_traversal() {
        let directory = tempfile::tempdir().expect("temporary directory exists");
        let descriptor = directory.path().join("local.pb");
        std::fs::write(&descriptor, b"descriptor fixture").expect("descriptor writes");
        let mut config = valid_config(descriptor);
        config.schema = CONFIG_SCHEMA_V0_3.to_string();
        config.artifacts = Some(ArtifactServiceConfig {
            endpoint: "http://127.0.0.1:8090".to_string(),
            cache_directory: PathBuf::from("artifact-cache"),
            max_artifact_bytes: 1024,
            chunk_size_bytes: 64,
            connect_timeout_ms: 5_000,
            read_timeout_ms: 30_000,
            input_bindings: vec![ArtifactInputBindingConfig {
                id: "lab-map-input".to_string(),
                map_id: "lab-map".to_string(),
                revision_id: "r1".to_string(),
                content_digest: None,
                target_path: PathBuf::from("../escape.map"),
            }],
            output_bindings: Vec::new(),
        });
        assert!(matches!(
            CompiledLocalCatalog::compile(config, directory.path()),
            Err(CatalogError::Validation { field, .. })
                if field == "artifacts.input_bindings.target_path"
        ));
    }

    /// Artifact bindings reject paths that resolve to the cache root itself.
    #[test]
    fn rejects_empty_or_current_directory_artifact_paths() {
        let directory = tempfile::tempdir().expect("temporary directory exists");
        let descriptor = directory.path().join("local.pb");
        std::fs::write(&descriptor, b"descriptor fixture").expect("descriptor writes");
        for invalid_path in [PathBuf::new(), PathBuf::from(".")] {
            let mut config = valid_config(descriptor.clone());
            config.schema = CONFIG_SCHEMA_V0_3.to_string();
            config.artifacts = Some(ArtifactServiceConfig {
                endpoint: "http://127.0.0.1:8090".to_string(),
                cache_directory: PathBuf::from("artifact-cache"),
                max_artifact_bytes: 1024,
                chunk_size_bytes: 64,
                connect_timeout_ms: 5_000,
                read_timeout_ms: 30_000,
                input_bindings: vec![ArtifactInputBindingConfig {
                    id: "lab-map-input".to_string(),
                    map_id: "lab-map".to_string(),
                    revision_id: "r1".to_string(),
                    content_digest: None,
                    target_path: invalid_path,
                }],
                output_bindings: Vec::new(),
            });
            assert!(matches!(
                CompiledLocalCatalog::compile(config, directory.path()),
                Err(CatalogError::Validation { field, .. })
                    if field == "artifacts.input_bindings.target_path"
            ));
        }
    }

    /// Duplicate capability contracts fail rather than creating ambiguous owners.
    #[test]
    fn rejects_duplicate_capability_owner() {
        let directory = tempfile::tempdir().expect("temporary directory exists");
        let descriptor = directory.path().join("local.pb");
        std::fs::write(&descriptor, b"descriptor fixture").expect("descriptor writes");
        let mut config = valid_config(descriptor);
        let mut duplicate = config.capabilities[0].clone();
        duplicate.owner = "perception".to_string();
        config.capabilities.push(duplicate);
        assert!(matches!(
            CompiledLocalCatalog::compile(config, directory.path()),
            Err(CatalogError::Validation { field, .. }) if field == "capabilities.contract"
        ));
    }

    /// Remote local endpoints and absent descriptor sets fail before runtime.
    #[test]
    fn rejects_remote_endpoint_and_missing_descriptor() {
        let directory = tempfile::tempdir().expect("temporary directory exists");
        let missing = directory.path().join("missing.pb");
        let mut remote = valid_config(missing.clone());
        if let ConnectionConfig::Http { endpoint, .. } = &mut remote.connections[0] {
            *endpoint = "http://198.51.100.7:8100".to_string();
        }
        assert!(matches!(
            CompiledLocalCatalog::compile(remote, directory.path()),
            Err(CatalogError::Validation { field, .. }) if field == "connections.endpoint"
        ));
        assert!(matches!(
            CompiledLocalCatalog::compile(valid_config(missing), directory.path()),
            Err(CatalogError::Validation { field, .. }) if field.contains("descriptor_set")
        ));
    }

    /// Runtime invocation values populate only request data and cannot alter fixed routing.
    #[test]
    fn rendered_request_preserves_fixed_endpoint_and_method() {
        let directory = tempfile::tempdir().expect("temporary directory exists");
        let descriptor = directory.path().join("local.pb");
        std::fs::write(&descriptor, b"descriptor fixture").expect("descriptor writes");
        let mut config = valid_config(descriptor);
        config.capabilities[0].workflow.execute[0].request = RequestMappingConfig {
            base: serde_json::json!({}),
            bindings: vec![RequestBindingConfig {
                target: "/region".to_string(),
                value: ValueExpressionConfig::Pointer {
                    pointer: "/invocation/parameters/region".to_string(),
                },
            }],
        };
        let catalog =
            CompiledLocalCatalog::compile(config, directory.path()).expect("catalog compiles");
        let context = WorkflowContext::new(serde_json::json!({
            "parameters": {
                "region": "http://remote.example/replace-route"
            }
        }));
        let request = catalog.capabilities()["mobility.reach_region@v1"]
            .workflow()
            .execute()[0]
            .render(&catalog, &context)
            .expect("request renders");
        match request {
            CompiledDriverRequest::Http {
                endpoint,
                method,
                path,
                body,
                ..
            } => {
                assert_eq!(endpoint, "http://127.0.0.1:8100");
                assert_eq!(method, "POST");
                assert_eq!(path, "/navigation/reach");
                assert_eq!(body["region"], "http://remote.example/replace-route");
            }
            _ => panic!("HTTP workflow renders an HTTP request"),
        }
    }

    /// Cancel submission does not affect state projection until status reports cancellation.
    #[test]
    fn maps_cancelled_only_from_status_fact() {
        let directory = tempfile::tempdir().expect("temporary directory exists");
        let descriptor = directory.path().join("local.pb");
        std::fs::write(&descriptor, b"descriptor fixture").expect("descriptor writes");
        let catalog = CompiledLocalCatalog::compile(valid_config(descriptor), directory.path())
            .expect("catalog compiles");
        let workflow = catalog.capabilities()["mobility.reach_region@v1"].workflow();
        let mut context = WorkflowContext::new(serde_json::json!({}));
        context
            .record_step("request-cancel", serde_json::json!({ "accepted": true }))
            .expect("cancel response records");
        assert!(workflow.map_execution_state(&context).is_err());
        context
            .record_step(
                "read-state",
                serde_json::json!({ "state": "CANCELED", "detail": "stopped" }),
            )
            .expect("status records");
        assert_eq!(
            workflow.map_execution_state(&context).expect("state maps"),
            MappedExecutionFact {
                phase: MappedExecutionPhase::Cancelled,
                reason: Some("stopped".to_string()),
            }
        );
    }

    /// Startup validation rejects handles and requests that depend on unavailable facts.
    #[test]
    fn rejects_unavailable_workflow_sources() {
        let directory = tempfile::tempdir().expect("temporary directory exists");
        let descriptor = directory.path().join("local.pb");
        std::fs::write(&descriptor, b"descriptor fixture").expect("descriptor writes");
        let mut invalid_handle = valid_config(descriptor.clone());
        invalid_handle.capabilities[0].workflow.local_handle = ValueExpressionConfig::Constant {
            value: serde_json::json!("shared-handle"),
        };
        assert!(matches!(
            CompiledLocalCatalog::compile(invalid_handle, directory.path()),
            Err(CatalogError::Validation { field, .. }) if field == "workflow.local_handle"
        ));

        let mut future_step = valid_config(descriptor);
        future_step.capabilities[0].workflow.execute[0].request = RequestMappingConfig {
            base: serde_json::json!({}),
            bindings: vec![RequestBindingConfig {
                target: "/value".to_string(),
                value: ValueExpressionConfig::Pointer {
                    pointer: "/steps/read-state/value".to_string(),
                },
            }],
        };
        assert!(matches!(
            CompiledLocalCatalog::compile(future_step, directory.path()),
            Err(CatalogError::Validation { field, .. }) if field.contains("workflow.execute")
        ));
    }
}
