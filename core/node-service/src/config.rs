//! User-owned Node Service configuration.

use serde::Deserialize;
use std::collections::BTreeMap;
use std::path::Path;

use crate::{CatalogError, CompiledLocalCatalog};

/// Returns the default reconnect backoff.
const fn default_reconnect_delay_ms() -> u64 {
    1_000
}

/// Versioned configuration for one generic, configuration-driven Node Service.
#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NodeServiceConfig {
    /// Configuration schema identity; v0.2 through v0.6 are accepted by the compiler.
    pub schema: String,
    /// Stable node identity advertised to RoboGuide.
    pub node_id: String,
    /// Formal RoboGuide Server gRPC endpoint.
    pub server_endpoint: String,
    /// Durable directory used by the Node Service execution journal.
    pub state_directory: std::path::PathBuf,
    /// Delay before reconnect after transport loss.
    #[serde(default = "default_reconnect_delay_ms")]
    pub reconnect_delay_ms: u64,
    /// Local EAIOS/runtime identities aggregated behind this node.
    pub local_systems: Vec<LocalSystemConfig>,
    /// Fixed local connections available to declared workflows.
    pub connections: Vec<ConnectionConfig>,
    /// Canonical capability ownership and local workflows.
    pub capabilities: Vec<CapabilityBindingConfig>,
    /// Locally observable resources registered with RoboGuide.
    #[serde(default)]
    pub resources: Vec<ResourceConfig>,
    /// Locally observable sensors registered with RoboGuide.
    #[serde(default)]
    pub sensors: Vec<SensorConfig>,
    /// Optional Spatial Memory artifact data-plane configuration.
    ///
    /// The field is optional so an existing v0.2 deployment remains valid.  A v0.3
    /// deployment may enable it to stage immutable map revisions without changing the
    /// Node Protocol contract.
    #[serde(default)]
    pub artifacts: Option<ArtifactServiceConfig>,
    /// Selective periodically sampled State channels introduced by node-config/v0.5.
    #[serde(default)]
    pub state_exports: Vec<StateExportConfig>,
    /// Selective Memory discovery and exchange providers introduced by node-config/v0.5.
    #[serde(default)]
    pub memory_providers: Vec<MemoryProviderConfig>,
}

/// One fixed periodically sampled State channel owned by a local system.
#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StateExportConfig {
    /// Node-wide export identity.
    pub id: String,
    /// Local system that owns the source operation.
    pub owner: String,
    /// Semantic object class: `node` or `world`.
    pub object_class: String,
    /// Domain-specific object category.
    pub object_type: String,
    /// Stable object identity.
    pub object_id: String,
    /// Source meaning: `reported` or `observed`.
    pub semantic: String,
    /// Versioned JSON payload schema.
    pub payload_schema: String,
    /// Receive-relative validity period.
    pub valid_for_ms: u64,
    /// Period between local samples.
    pub interval_ms: u64,
    /// Fixed local observation operation used for sampling.
    pub step: WorkflowStepConfig,
    /// JSON Pointer locating the exported value in the step response.
    pub value_pointer: String,
    /// Optional JSON Pointer locating a source-local timestamp in milliseconds.
    #[serde(default)]
    pub source_observed_at_pointer: Option<String>,
    /// Optional JSON Pointer locating confidence in the inclusive range zero through one.
    #[serde(default)]
    pub confidence_pointer: Option<String>,
}

/// One Memory discovery/exchange provider owned by a heterogeneous local system.
#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MemoryProviderConfig {
    /// Node-wide provider identity.
    pub id: String,
    /// Local system that retains semantic and storage ownership.
    pub owner: String,
    /// Kind: execution, spatial, semantic, experience, or artifact.
    pub kind: String,
    /// Maximum scope: local or global; an Execution Group scope is injected per operation.
    pub scope: String,
    /// Visibility: discoverable or exchangeable.
    pub visibility: String,
    /// Versioned schema of provider metadata or content.
    pub payload_schema: String,
    /// Content media type when the provider offers Artifact-backed bytes.
    pub media_type: String,
    /// Optional Node ledger/reference-backend root, resolved below the node state directory.
    #[serde(default)]
    pub storage_directory: Option<std::path::PathBuf>,
    /// Optional provider-local discovery workflow.
    #[serde(default)]
    pub discover: Option<MemoryWorkflowConfig>,
    /// Optional provider-local export workflow.
    #[serde(default)]
    pub export: Option<MemoryWorkflowConfig>,
    /// Optional provider-local import workflow.
    #[serde(default)]
    pub import: Option<MemoryWorkflowConfig>,
}

/// Declarative workflow hook for one heterogeneous Memory provider operation.
#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MemoryWorkflowConfig {
    /// Ordered local driver steps; repeated immutable selectors must be idempotent.
    #[serde(default)]
    pub steps: Vec<WorkflowStepConfig>,
    /// JSON Pointer to the provider-authorized publish-eligible manifest set (discover only).
    ///
    /// The response must contain only immutable Memory that this provider has explicitly
    /// authorized RoboGuide to publish. It is not an enumeration of all Local EAIOS Memory.
    #[serde(default)]
    pub manifests_pointer: Option<String>,
    /// JSON Pointer to a provider-storage-relative artifact path (export only).
    #[serde(default)]
    pub artifact_path_pointer: Option<String>,
}

/// Node-local configuration for the independent Spatial Memory artifact data plane.
#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ArtifactServiceConfig {
    /// Absolute HTTP(S) endpoint of the central artifact service.
    pub endpoint: String,
    /// Deployment-owned root for content-addressed blobs and staged inputs.
    pub cache_directory: std::path::PathBuf,
    /// Maximum accepted artifact size in bytes.
    #[serde(default = "default_artifact_max_bytes")]
    pub max_artifact_bytes: u64,
    /// Preferred chunk size for streaming reads and writes.
    #[serde(default = "default_artifact_chunk_size_bytes")]
    pub chunk_size_bytes: usize,
    /// Maximum time allowed to establish one artifact data-plane connection.
    #[serde(default = "default_artifact_connect_timeout_ms")]
    pub connect_timeout_ms: u64,
    /// Maximum idle time between successful reads from the artifact data plane.
    #[serde(default = "default_artifact_read_timeout_ms")]
    pub read_timeout_ms: u64,
    /// Fixed map revisions that may be downloaded for local workflows.
    #[serde(default)]
    pub input_bindings: Vec<ArtifactInputBindingConfig>,
    /// Fixed local map outputs that may be published after execution succeeds.
    #[serde(default)]
    pub output_bindings: Vec<ArtifactOutputBindingConfig>,
}

/// Declarative input binding for one preallocated immutable map revision.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ArtifactInputBindingConfig {
    /// Deployment-local binding identity.
    pub id: String,
    /// Logical map identity selected by the Mission plan.
    pub map_id: String,
    /// Immutable revision selected by the Mission plan.
    pub revision_id: String,
    /// Optional expected SHA-256 digest; the manifest digest is always verified.
    #[serde(default)]
    pub content_digest: Option<String>,
    /// Relative path under `cache_directory` exposed to the local workflow.
    pub target_path: std::path::PathBuf,
}

/// Declarative output binding for one fixed map revision and local source path.
#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ArtifactOutputBindingConfig {
    /// Deployment-local binding identity.
    pub id: String,
    /// Logical map identity reserved for this output.
    pub map_id: String,
    /// Immutable revision reserved for this output.
    pub revision_id: String,
    /// Local source path under the deployment-owned artifact root.
    pub source_path: std::path::PathBuf,
    /// Opaque bundle media type sent in the manifest.
    pub media_type: String,
    /// Producer-declared map format family name.
    pub format_name: String,
    /// Producer-declared map format version.
    pub format_version: String,
    /// Fixed root frame in the produced bundle.
    pub root_frame: String,
    /// Coordinate convention used by the produced bundle.
    pub coordinate_convention: String,
    /// Fixed physical or semantic anchor identity.
    pub spatial_anchor_id: String,
    /// Optional metric resolution declared by the producer.
    #[serde(default)]
    pub resolution_meters: Option<f64>,
}

/// One Local EAIOS/runtime whose facts are aggregated into the Node identity.
#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LocalSystemConfig {
    /// Stable configuration-local identity referenced by connections and capabilities.
    pub id: String,
    /// Human-readable runtime or Local EAIOS name.
    pub runtime_name: String,
    /// Runtime version reported during registration.
    pub runtime_version: String,
    /// Non-secret, registration-visible runtime metadata.
    #[serde(default)]
    pub metadata: BTreeMap<String, String>,
    /// Fixed local health check and state mapping for heartbeat facts.
    pub health: HealthCheckConfig,
}

/// One configured local-system health observation.
#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HealthCheckConfig {
    /// Fixed driver operation used to read local health.
    pub step: WorkflowStepConfig,
    /// JSON Pointer locating the local health state in the step response.
    pub state_pointer: String,
    /// Optional JSON Pointer locating descriptive health detail.
    pub detail_pointer: Option<String>,
    /// Local values mapped to Online.
    pub online: Vec<String>,
    /// Local values mapped to Degraded.
    pub degraded: Vec<String>,
    /// Local values mapped to Offline.
    pub offline: Vec<String>,
    /// Whether health values are compared case-sensitively.
    #[serde(default)]
    pub case_sensitive: bool,
}

/// One fixed exact-capability readiness observation.
#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CapabilityReadinessConfig {
    /// Fixed driver operation used to observe whether the capability can execute now.
    pub step: WorkflowStepConfig,
    /// JSON Pointer locating the local readiness state in the step response.
    pub state_pointer: String,
    /// Optional JSON Pointer locating descriptive readiness detail.
    pub detail_pointer: Option<String>,
    /// Local values mapped to ready.
    pub ready: Vec<String>,
    /// Local values mapped to unavailable.
    pub unavailable: Vec<String>,
    /// Whether readiness values are compared case-sensitively.
    #[serde(default)]
    pub case_sensitive: bool,
}

/// A secret-bearing value that may only be sourced from the process environment.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CredentialSourceConfig {
    /// Environment variable read by a concrete driver at call time.
    pub env: String,
}

/// One fixed local transport connection.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(tag = "driver", rename_all = "snake_case", deny_unknown_fields)]
pub enum ConnectionConfig {
    /// Fixed local HTTP JSON endpoint.
    Http {
        /// Stable connection identity referenced by workflow steps.
        id: String,
        /// Local system owning this connection.
        local_system: String,
        /// Loopback or Unix-socket endpoint fixed at startup.
        endpoint: String,
        /// Request timeout for every call on this connection.
        #[serde(default = "default_request_timeout_ms")]
        timeout_ms: u64,
        /// Secret HTTP headers sourced from environment variables.
        #[serde(default)]
        headers: BTreeMap<String, CredentialSourceConfig>,
    },
    /// Fixed local dynamic gRPC endpoint.
    Grpc {
        /// Stable connection identity referenced by workflow steps.
        id: String,
        /// Local system owning this connection.
        local_system: String,
        /// Loopback or Unix-socket endpoint fixed at startup.
        endpoint: String,
        /// Protobuf descriptor set used for dynamic message encoding.
        descriptor_set: Option<std::path::PathBuf>,
        /// Explicit opt-in to local gRPC reflection instead of a descriptor set.
        #[serde(default)]
        reflection: bool,
        /// Request timeout for every call on this connection.
        #[serde(default = "default_request_timeout_ms")]
        timeout_ms: u64,
        /// Secret gRPC metadata sourced from environment variables.
        #[serde(default)]
        metadata: BTreeMap<String, CredentialSourceConfig>,
    },
    /// Fixed local MCP Streamable HTTP endpoint.
    Mcp {
        /// Stable connection identity referenced by workflow steps.
        id: String,
        /// Local system owning this connection.
        local_system: String,
        /// Loopback endpoint fixed at startup.
        endpoint: String,
        /// Request timeout for every tool call.
        #[serde(default = "default_request_timeout_ms")]
        timeout_ms: u64,
        /// Secret HTTP headers sourced from environment variables.
        #[serde(default)]
        headers: BTreeMap<String, CredentialSourceConfig>,
    },
}

impl ConnectionConfig {
    /// Returns the stable connection identity independently of driver kind.
    pub fn id(&self) -> &str {
        match self {
            Self::Http { id, .. } | Self::Grpc { id, .. } | Self::Mcp { id, .. } => id,
        }
    }

    /// Returns the owning local system independently of driver kind.
    pub fn local_system(&self) -> &str {
        match self {
            Self::Http { local_system, .. }
            | Self::Grpc { local_system, .. }
            | Self::Mcp { local_system, .. } => local_system,
        }
    }

    /// Returns the configured endpoint independently of driver kind.
    pub fn endpoint(&self) -> &str {
        match self {
            Self::Http { endpoint, .. }
            | Self::Grpc { endpoint, .. }
            | Self::Mcp { endpoint, .. } => endpoint,
        }
    }
}

/// One canonical capability mapped to exactly one local-system workflow.
#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CapabilityBindingConfig {
    /// Canonical capability contract such as `mobility.reach_region@v1`.
    pub contract: String,
    /// Coarse capability kind consumed by Control Matching.
    pub kind: String,
    /// Unique local system responsible for executing this contract.
    pub owner: String,
    /// Control-committed resource identities required before local dispatch.
    #[serde(default)]
    pub required_resources: Vec<String>,
    /// Node-local concurrency locks, which never grant Control authority.
    #[serde(default)]
    pub local_locks: Vec<String>,
    /// Optional node-owned artifact action fixed for every execution of this capability.
    #[serde(default)]
    pub artifact_operation: Option<ArtifactOperationConfig>,
    /// Fixed observation proving whether this exact contract can execute now.
    #[serde(default)]
    pub readiness: Option<CapabilityReadinessConfig>,
    /// Declarative execute/status/cancel workflow.
    pub workflow: WorkflowConfig,
}

/// Typed artifact action owned by one configured capability.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum ArtifactOperationConfig {
    /// Expose the configured output path without publishing its bytes.
    PrepareOutput,
    /// Upload, finalize, and publish the configured immutable output.
    Publish,
    /// Stage a verified input and record import evidence after local completion.
    Import,
    /// Stage a verified input and record localization evidence after local completion.
    Verify,
}

impl ArtifactOperationConfig {
    /// Returns the stable configuration spelling used by TOML and JSON Schema.
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::PrepareOutput => "prepare-output",
            Self::Publish => "publish",
            Self::Import => "import",
            Self::Verify => "verify",
        }
    }
}

/// Local execution workflow for one canonical capability.
#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkflowConfig {
    /// Ordered steps that dispatch one physical or computational execution.
    pub execute: Vec<WorkflowStepConfig>,
    /// Ordered steps that reconcile current local execution state.
    pub status: Vec<WorkflowStepConfig>,
    /// Ordered steps that submit cancellation without implying terminal cancellation.
    pub cancel: Vec<WorkflowStepConfig>,
    /// Expression extracting the durable local execution handle after dispatch.
    pub local_handle: ValueExpressionConfig,
    /// Delay between status observations while an execution remains non-terminal.
    #[serde(default = "default_status_poll_interval_ms")]
    pub poll_interval_ms: u64,
    /// Mapping from local state values to RoboGuide execution facts.
    pub execution_state: ExecutionStateMappingConfig,
}

/// One fixed operation plus a request mapping in an ordered workflow.
#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WorkflowStepConfig {
    /// Stable step identity used by later JSON Pointer expressions.
    pub id: String,
    /// Fixed configured connection used for this step.
    pub connection: String,
    /// Fixed local operation selected at startup.
    pub operation: LocalOperationConfig,
    /// Mapping from invocation and prior responses into the local request body.
    #[serde(default)]
    pub request: RequestMappingConfig,
}

/// Fixed operation selected by a workflow step; runtime input cannot alter it.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum LocalOperationConfig {
    /// HTTP JSON request with a fixed method and relative path.
    Http {
        /// Uppercase HTTP method from the supported method whitelist.
        method: String,
        /// Absolute path relative to the configured endpoint.
        path: String,
    },
    /// Dynamic unary gRPC request with fixed service and method names.
    GrpcUnary {
        /// Fully qualified protobuf service name.
        service: String,
        /// Protobuf method name.
        method: String,
    },
    /// Dynamic server-streaming gRPC request with fixed service and method names.
    GrpcServerStream {
        /// Fully qualified protobuf service name.
        service: String,
        /// Protobuf method name.
        method: String,
    },
    /// MCP Streamable HTTP request with a fixed tool name.
    McpTool {
        /// Tool name configured by the node deployer.
        tool: String,
    },
}

/// JSON request template populated only through explicit bindings.
#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RequestMappingConfig {
    /// Immutable base JSON value cloned for every request.
    #[serde(default = "default_request_base")]
    pub base: serde_json::Value,
    /// Ordered, unique JSON Pointer target bindings.
    #[serde(default)]
    pub bindings: Vec<RequestBindingConfig>,
}

impl Default for RequestMappingConfig {
    /// Creates an empty JSON object request with no dynamic bindings.
    fn default() -> Self {
        Self {
            base: default_request_base(),
            bindings: Vec::new(),
        }
    }
}

/// One request target populated from a whitelisted value expression.
#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RequestBindingConfig {
    /// JSON Pointer inside the request body to create or replace.
    pub target: String,
    /// Value expression evaluated against immutable workflow context.
    pub value: ValueExpressionConfig,
}

/// Safe, non-scriptable request value expression.
#[derive(Debug, Clone, PartialEq, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum ValueExpressionConfig {
    /// Reads an exact JSON Pointer from invocation or prior step context.
    Pointer {
        /// Source JSON Pointer.
        pointer: String,
    },
    /// Injects a deployment-owned constant JSON value.
    Constant {
        /// Constant value that network input cannot alter.
        value: serde_json::Value,
    },
    /// Evaluates one whitelisted deterministic conversion function.
    Function {
        /// Function chosen from a closed enum.
        function: ValueFunction,
        /// Recursively evaluated function arguments.
        #[serde(default)]
        arguments: Vec<ValueExpressionConfig>,
    },
}

/// Whitelisted deterministic mapping functions.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ValueFunction {
    /// Converts a scalar value into a string.
    ToString,
    /// Converts a number or numeric string into an integer.
    ToInteger,
    /// Converts a number or numeric string into a finite float.
    ToFloat,
    /// Converts a boolean or `true`/`false` string into a boolean.
    ToBoolean,
    /// Converts one finite yaw angle in radians into a Z-axis quaternion object.
    QuaternionFromYaw,
}

/// Maps local status response values into canonical execution phases.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ExecutionStateMappingConfig {
    /// JSON Pointer locating the local state value in workflow context.
    pub state_pointer: String,
    /// Optional JSON Pointer locating a local failure/cancellation detail.
    pub reason_pointer: Option<String>,
    /// Local values mapped to Accepted.
    #[serde(default)]
    pub accepted: Vec<String>,
    /// Local values mapped to Started/Running.
    pub running: Vec<String>,
    /// Local values mapped to Completed.
    pub completed: Vec<String>,
    /// Local values mapped to Failed.
    pub failed: Vec<String>,
    /// Local values mapped to Cancelled.
    pub cancelled: Vec<String>,
    /// Whether mappings compare local values with case sensitivity.
    #[serde(default)]
    pub case_sensitive: bool,
}

/// One stable, Control-visible resource owned by a local system.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ResourceConfig {
    /// Stable resource identity.
    pub id: String,
    /// Transport-neutral resource kind.
    pub kind: String,
    /// Non-zero capacity exposed for matching and commitment.
    pub capacity: u32,
    /// Local system responsible for observing and using the resource.
    pub owner: String,
    /// Non-secret registration metadata.
    #[serde(default)]
    pub metadata: BTreeMap<String, String>,
}

/// One stable sensor owned by a local system.
#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SensorConfig {
    /// Stable sensor identity.
    pub id: String,
    /// Transport-neutral sensor kind.
    pub kind: String,
    /// Local system responsible for observing the sensor.
    pub owner: String,
    /// Non-secret registration metadata.
    #[serde(default)]
    pub metadata: BTreeMap<String, String>,
}

impl NodeServiceConfig {
    /// Loads one strict versioned TOML document without performing filesystem validation.
    pub fn load(path: &Path) -> Result<Self, std::io::Error> {
        let source = std::fs::read_to_string(path)?;
        toml::from_str(&source)
            .map_err(|error| std::io::Error::new(std::io::ErrorKind::InvalidData, error))
    }

    /// Loads and compiles one immutable catalog relative to the configuration file.
    pub fn load_compiled(path: &Path) -> Result<CompiledLocalCatalog, CatalogError> {
        let config = Self::load(path).map_err(CatalogError::Load)?;
        let directory = path.parent().unwrap_or_else(|| Path::new("."));
        CompiledLocalCatalog::compile(config, directory)
    }
}

/// Returns the default local request timeout.
const fn default_request_timeout_ms() -> u64 {
    5_000
}

/// Returns the default local execution status polling interval.
const fn default_status_poll_interval_ms() -> u64 {
    250
}

/// Returns the default maximum artifact size accepted by the node cache.
const fn default_artifact_max_bytes() -> u64 {
    4 * 1024 * 1024 * 1024
}

/// Returns the default bounded chunk size used by artifact transfers.
const fn default_artifact_chunk_size_bytes() -> usize {
    1024 * 1024
}

/// Returns the default artifact data-plane connection timeout.
const fn default_artifact_connect_timeout_ms() -> u64 {
    5_000
}

/// Returns the default artifact data-plane read-idle timeout.
const fn default_artifact_read_timeout_ms() -> u64 {
    30_000
}

/// Returns the default empty JSON request object.
fn default_request_base() -> serde_json::Value {
    serde_json::Value::Object(serde_json::Map::new())
}

#[cfg(test)]
mod tests {
    use super::ArtifactOperationConfig;

    /// Typed artifact operations accept only the four v0.3 schema spellings.
    #[test]
    fn parses_typed_artifact_operation_spellings() {
        for (wire, expected) in [
            ("prepare-output", ArtifactOperationConfig::PrepareOutput),
            ("publish", ArtifactOperationConfig::Publish),
            ("import", ArtifactOperationConfig::Import),
            ("verify", ArtifactOperationConfig::Verify),
        ] {
            let decoded = serde_json::from_value::<ArtifactOperationConfig>(
                serde_json::Value::String(wire.to_string()),
            )
            .expect("known artifact operation decodes");
            assert_eq!(decoded, expected);
            assert_eq!(decoded.as_str(), wire);
        }
        assert!(
            serde_json::from_value::<ArtifactOperationConfig>(serde_json::Value::String(
                "automatic".to_string()
            ))
            .is_err()
        );
    }
}
