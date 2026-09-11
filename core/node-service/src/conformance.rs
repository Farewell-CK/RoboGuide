//! Offline Extension Conformance compilation and reporting.
//!
//! The conformance boundary deliberately reuses the production catalog compiler. It adds a
//! stable, machine-readable summary for deployment tooling and records the lifecycle invariants
//! shared by HTTP, dynamic gRPC, and MCP workflows without opening a local or remote connection.

use crate::{
    CatalogError, CompiledCapability, CompiledCapabilityProfile, CompiledConnection,
    CompiledLocalCatalog, CompiledOperation, CompiledWorkflowStep, LocalOperationConfig,
    NodeServiceConfig,
};
use serde::Serialize;
use std::path::Path;

/// Version marker for the offline device-extension conformance report.
pub const EXTENSION_CONFORMANCE_SCHEMA_V0_1: &str = "roboguide.extension-conformance/v0.1";
/// Current report schema separating capability evidence from operation workflow mappings.
pub const EXTENSION_CONFORMANCE_SCHEMA_V0_2: &str = "roboguide.extension-conformance/v0.2";

/// One diagnostic emitted when a configuration cannot be compiled.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ConformanceDiagnostic {
    /// Stable location such as `connections.motion.endpoint` or `workflow.step.status`.
    pub location: String,
    /// Machine-readable diagnostic category.
    pub code: String,
    /// Actionable explanation that does not include secret values.
    pub message: String,
}

impl std::fmt::Display for ConformanceDiagnostic {
    /// Formats a diagnostic for command-line consumers.
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(
            formatter,
            "{} at {}: {}",
            self.code, self.location, self.message
        )
    }
}

/// Failure returned by offline extension compilation or report serialization.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ConformanceError {
    /// The authored catalog violated a deployment or mapping invariant.
    Diagnostic(ConformanceDiagnostic),
    /// The successful report could not be encoded as JSON.
    Serialization(String),
}

impl std::fmt::Display for ConformanceError {
    /// Formats a stable conformance failure.
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Diagnostic(diagnostic) => diagnostic.fmt(formatter),
            Self::Serialization(reason) => write!(formatter, "conformance report: {reason}"),
        }
    }
}

impl std::error::Error for ConformanceError {}

/// One fixed connection included in a conformance report.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ConnectionConformance {
    /// Stable deployment connection identity.
    pub id: String,
    /// Generic driver family selected by the connection.
    pub driver: String,
    /// Local-system owner of the connection.
    pub owner: String,
    /// Fixed endpoint; credentials are never included in the report.
    pub endpoint: String,
}

/// One fixed workflow step included in a conformance report.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct StepConformance {
    /// Stable step identity used by mapping expressions.
    pub id: String,
    /// Fixed connection identity selected by the step.
    pub connection: String,
    /// Driver family selected by that connection.
    pub driver: String,
    /// Human-readable fixed operation summary.
    pub operation: String,
}

/// Shared execute/status/cancel workflow proof for one capability.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct WorkflowConformance {
    /// Ordered physical dispatch steps.
    pub execute: Vec<StepConformance>,
    /// Ordered reconciliation/status steps.
    pub status: Vec<StepConformance>,
    /// Ordered cancellation-request steps.
    pub cancel: Vec<StepConformance>,
    /// The local handle expression was proven to derive from execute output.
    pub local_handle_mapped: bool,
    /// The state mapping was proven to cover accepted/running/terminal phases.
    pub execution_state_mapped: bool,
}

/// One canonical capability owner and its deployment obligations.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct CapabilityConformance {
    /// Exact canonical capability contract.
    pub contract: String,
    /// Sole local-system owner.
    pub owner: String,
    /// Fixed readiness observation, when supplied by node-config/v0.5.
    pub readiness: Option<StepConformance>,
    /// Control-committed resource identities required by the workflow.
    pub required_resources: Vec<String>,
    /// Node-local lock identities; these do not grant Control authority.
    pub local_locks: Vec<String>,
    /// Whether schema v0.5 supplied an exact readiness observation.
    pub exact_readiness: bool,
    /// Compiled lifecycle and mapping summary.
    pub workflow: WorkflowConformance,
}

/// One exact capability evidence profile independently of executable workflow mappings.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct CapabilityProfileConformance {
    /// Exact canonical capability contract.
    pub contract: String,
    /// Sole Local EAIOS owner of the evidence.
    pub owner: String,
    /// Typed feasibility attributes retained without interpreting provider availability.
    pub attributes: std::collections::BTreeMap<String, serde_json::Value>,
    /// Fixed readiness observation route.
    pub readiness: Option<StepConformance>,
}

/// One canonical operation mapped to a fixed Local EAIOS workflow.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct OperationConformance {
    /// Canonical operation identity accepted from `ExecutionIntent`.
    pub operation: String,
    /// Sole Local EAIOS owner of the workflow.
    pub owner: String,
    /// Control-committed resource identities required before dispatch.
    pub required_resources: Vec<String>,
    /// Node-local concurrency locks, which never grant Control authority.
    pub local_locks: Vec<String>,
    /// Compiled lifecycle and mapping summary.
    pub workflow: WorkflowConformance,
}

/// One selective State channel proven to use a fixed local sampling route.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct StateExportConformance {
    /// Node-wide export identity.
    pub id: String,
    /// Local-system owner.
    pub owner: String,
    /// Reported or observed semantic.
    pub semantic: String,
    /// Fixed sampling workflow step.
    pub step: StepConformance,
}

/// One fixed Local EAIOS peer-channel readiness observation source.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct PeerChannelObserverConformance {
    /// Node-wide observer identity.
    pub id: String,
    /// Configuration-owned Local EAIOS identity injected into every fact.
    pub owner: String,
    /// Fixed, mechanically read-only observation step.
    pub step: StepConformance,
}

/// One selective Memory provider declaration retained in the extension report.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct MemoryProviderConformance {
    /// Node-wide provider identity.
    pub id: String,
    /// Local-system owner.
    pub owner: String,
    /// Execution, Spatial, Semantic, Experience, or Artifact kind.
    pub kind: String,
    /// Discoverable or exchangeable policy.
    pub visibility: String,
    /// Whether node-config/v0.6 enables the Node ledger/reference fallback.
    ///
    /// Serialized as the v0.1-compatible `local_backend`; it does not claim EAIOS authority.
    #[serde(rename = "local_backend")]
    pub node_ledger: bool,
    /// Whether a shared Artifact/catalog endpoint is available for publication and exchange.
    pub shared_data_plane: bool,
    /// Whether a provider-local discovery workflow is configured.
    pub discovery_workflow: bool,
    /// Whether a provider-local export workflow is configured.
    pub export_workflow: bool,
    /// Whether a provider-local import workflow is configured.
    pub import_workflow: bool,
}

/// One Node Service implementation guarantee shared by every supported local driver family.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub struct LifecycleConformanceInvariant {
    /// Stable guarantee identifier for CI and deployment reports.
    pub id: &'static str,
    /// Safety/lifecycle property covered by production engine and journal tests.
    pub description: &'static str,
}

/// Implementation-level guarantees applied equally to HTTP, dynamic gRPC, and MCP workflows.
///
/// The offline compiler reports these separately from per-configuration static checks. Their
/// presence is not evidence that a deployment facade or physical device passed a runtime probe.
pub const NODE_SERVICE_IMPLEMENTATION_GUARANTEES: &[LifecycleConformanceInvariant] = &[
    LifecycleConformanceInvariant {
        id: "execute-status-cancel",
        description: "execute dispatches once; status is the only source of terminal physical outcome; cancel is a request",
    },
    LifecycleConformanceInvariant {
        id: "unknown-fences",
        description: "unknown or transport-ambiguous outcomes become reconciliation-required",
    },
    LifecycleConformanceInvariant {
        id: "timeout-fences",
        description: "timeouts never imply a safe retry of a possibly started physical action",
    },
    LifecycleConformanceInvariant {
        id: "identity-idempotency",
        description: "an execution identity is bound to one invocation/workflow/resource tuple",
    },
    LifecycleConformanceInvariant {
        id: "restart-no-replay",
        description: "restart recovery status-polls known handles and never automatically replays execute",
    },
    LifecycleConformanceInvariant {
        id: "fixed-local-how",
        description: "network intent cannot select endpoint, executable, service, method, or MCP tool",
    },
    LifecycleConformanceInvariant {
        id: "control-commitment-boundary",
        description: "local locks protect the node only; Control remains reservation and recovery authority",
    },
];

/// Compatibility alias retained for Extension Conformance v0.1 report consumers.
///
/// These entries are implementation guarantees, not per-device runtime conformance evidence.
pub const SHARED_LIFECYCLE_CONFORMANCE: &[LifecycleConformanceInvariant] =
    NODE_SERVICE_IMPLEMENTATION_GUARANTEES;

/// Complete machine-readable offline conformance result.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ExtensionConformanceReport {
    /// Report schema marker.
    pub schema: &'static str,
    /// Authored configuration path used for compilation.
    pub config_path: String,
    /// Stable node identity from the configuration.
    pub node_id: String,
    /// Always true because report generation performs no network calls.
    pub offline_compile: bool,
    /// Always false because Controller connectivity is outside this command.
    pub controller_contacted: bool,
    /// Always false because offline compilation does not invoke a Local EAIOS workflow.
    pub runtime_probes_executed: bool,
    /// Always false because offline compilation never actuates physical hardware.
    pub hardware_probes_executed: bool,
    /// Local systems included in the compiled catalog.
    pub local_systems: Vec<String>,
    /// Fixed connections included in the compiled catalog.
    pub connections: Vec<ConnectionConformance>,
    /// Legacy combined declarations retained only for v0.6 report consumers.
    pub capabilities: Vec<CapabilityConformance>,
    /// Exact capability/readiness/attribute evidence declarations.
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub capability_profiles: Vec<CapabilityProfileConformance>,
    /// Canonical operation to fixed Local EAIOS workflow mappings.
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub operations: Vec<OperationConformance>,
    /// Selective fixed-route State exports.
    pub state_exports: Vec<StateExportConformance>,
    /// Fixed Local EAIOS peer-channel readiness observation sources.
    pub peer_channel_observers: Vec<PeerChannelObserverConformance>,
    /// Selective heterogeneous Memory providers.
    pub memory_providers: Vec<MemoryProviderConformance>,
    /// Static checks guaranteed by successful production compilation.
    pub checks: ConformanceChecks,
    /// Extension Conformance v0.1 compatibility alias for implementation guarantees.
    ///
    /// This field does not mean that a runtime or hardware probe was executed. New consumers
    /// should use `implementation_guarantees` together with the explicit probe flags.
    pub lifecycle: Vec<LifecycleConformanceInvariant>,
    /// Driver-independent guarantees of this Node Service implementation.
    ///
    /// These are not per-device runtime-probe results.
    pub implementation_guarantees: Vec<LifecycleConformanceInvariant>,
}

/// Boolean summary of the invariants checked before a node can start.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ConformanceChecks {
    /// Every canonical contract has one owner.
    pub unique_capability_owner: bool,
    /// Current configurations have one exact readiness workflow per capability profile.
    pub exact_readiness: bool,
    /// All endpoint, method, service, and tool selections are fixed.
    pub fixed_routes: bool,
    /// Every request mapping is closed and compiled.
    pub request_mappings: bool,
    /// Every execution state mapping is disjoint and complete.
    pub execution_state_mapping: bool,
    /// Every required resource exists and belongs to the capability owner.
    pub required_resources: bool,
    /// State and Memory exposure is explicit, owner-scoped, and schema v0.6+ validated.
    pub selective_state_memory: bool,
}

/// Compiles one authored Node configuration and returns an offline conformance report.
pub fn compile_extension_config(
    path: &Path,
) -> Result<ExtensionConformanceReport, ConformanceError> {
    let config = NodeServiceConfig::load_compiled(path).map_err(catalog_error)?;
    if !matches!(
        config.schema(),
        crate::CONFIG_SCHEMA_V0_6 | crate::CONFIG_SCHEMA_V0_7
    ) {
        return Err(ConformanceError::Diagnostic(ConformanceDiagnostic {
            location: "schema".to_string(),
            code: "state-memory-contract-required".to_string(),
            message: "Extension Conformance requires node-config/v0.6 or v0.7 semantics"
                .to_string(),
        }));
    }
    if let Some(capability) = config
        .capability_profiles()
        .values()
        .find(|capability| capability.readiness().is_none())
    {
        return Err(ConformanceError::Diagnostic(ConformanceDiagnostic {
            location: format!("capability_profiles.{}.readiness", capability.contract()),
            code: "readiness-required".to_string(),
            message: "Extension Conformance requires exact capability-profile readiness"
                .to_string(),
        }));
    }
    if let Some(capability) = config
        .operations()
        .values()
        .find(|capability| !capability.workflow().execution_state_mapped())
    {
        return Err(ConformanceError::Diagnostic(ConformanceDiagnostic {
            location: format!(
                "operations.{}.workflow.execution_state",
                capability.operation()
            ),
            code: "execution-state-incomplete".to_string(),
            message:
                "map accepted, running, completed, failed, and cancelled to distinct local states"
                    .to_string(),
        }));
    }
    Ok(report_for_catalog(path, &config))
}

/// Compiles one authored Node configuration and returns pretty JSON for CI or deployment tooling.
pub fn compile_extension_config_json(path: &Path) -> Result<String, ConformanceError> {
    let report = compile_extension_config(path)?;
    serde_json::to_string_pretty(&report)
        .map_err(|error| ConformanceError::Serialization(error.to_string()))
}

/// Converts one catalog failure into a location-aware conformance diagnostic.
fn catalog_error(error: CatalogError) -> ConformanceError {
    let diagnostic = match error {
        CatalogError::Load(error) => ConformanceDiagnostic {
            location: "config".to_string(),
            code: "config-load".to_string(),
            message: redacted_load_error(&error),
        },
        CatalogError::Validation { field, reason } => ConformanceDiagnostic {
            location: field,
            code: "config-validation".to_string(),
            message: reason,
        },
        CatalogError::Mapping { step, source } => ConformanceDiagnostic {
            location: format!("workflow.step.{step}"),
            code: "mapping-validation".to_string(),
            message: source.to_string(),
        },
    };
    ConformanceError::Diagnostic(diagnostic)
}

/// Formats configuration loading failures without echoing secret-bearing TOML source lines.
fn redacted_load_error(error: &std::io::Error) -> String {
    if error.kind() == std::io::ErrorKind::InvalidData {
        "configuration syntax or shape is invalid; source text is redacted".to_string()
    } else {
        format!("configuration file could not be read: {}", error.kind())
    }
}

/// Builds the report from an already compiled, immutable catalog.
fn report_for_catalog(path: &Path, catalog: &CompiledLocalCatalog) -> ExtensionConformanceReport {
    let connections = catalog
        .connections()
        .values()
        .map(connection_report)
        .collect::<Vec<_>>();
    let legacy = catalog.schema() == crate::CONFIG_SCHEMA_V0_6;
    let capabilities = if legacy {
        catalog
            .operations()
            .values()
            .map(capability_report)
            .collect::<Vec<_>>()
    } else {
        Vec::new()
    };
    let capability_profiles = if legacy {
        Vec::new()
    } else {
        catalog
            .capability_profiles()
            .values()
            .map(capability_profile_report)
            .collect::<Vec<_>>()
    };
    let operations = if legacy {
        Vec::new()
    } else {
        catalog
            .operations()
            .values()
            .map(operation_report)
            .collect::<Vec<_>>()
    };
    let exact_readiness = catalog
        .capability_profiles()
        .values()
        .all(|profile| profile.readiness().is_some());
    let execution_state_mapping = catalog
        .operations()
        .values()
        .all(|operation| operation.workflow().execution_state_mapped());
    ExtensionConformanceReport {
        schema: if legacy {
            EXTENSION_CONFORMANCE_SCHEMA_V0_1
        } else {
            EXTENSION_CONFORMANCE_SCHEMA_V0_2
        },
        config_path: path.display().to_string(),
        node_id: catalog.node_id().to_string(),
        offline_compile: true,
        controller_contacted: false,
        runtime_probes_executed: false,
        hardware_probes_executed: false,
        local_systems: catalog.local_systems().keys().cloned().collect(),
        connections,
        capabilities,
        capability_profiles,
        operations,
        state_exports: catalog
            .state_exports()
            .values()
            .map(|export| StateExportConformance {
                id: export.id().to_string(),
                owner: export.owner().to_string(),
                semantic: export.semantic().to_string(),
                step: step_report(export.step()),
            })
            .collect(),
        peer_channel_observers: catalog
            .peer_channel_observers()
            .values()
            .map(|observer| PeerChannelObserverConformance {
                id: observer.id().to_string(),
                owner: observer.owner().to_string(),
                step: step_report(observer.step()),
            })
            .collect(),
        memory_providers: catalog
            .memory_providers()
            .values()
            .map(|provider| MemoryProviderConformance {
                id: provider.id().to_string(),
                owner: provider.owner().to_string(),
                kind: provider.kind().to_string(),
                visibility: provider.visibility().to_string(),
                node_ledger: provider.operational(),
                shared_data_plane: catalog.artifact_service().is_some(),
                discovery_workflow: provider.discover().is_some(),
                export_workflow: provider.export().is_some(),
                import_workflow: provider.import().is_some(),
            })
            .collect(),
        checks: ConformanceChecks {
            unique_capability_owner: true,
            exact_readiness,
            fixed_routes: true,
            request_mappings: true,
            execution_state_mapping,
            required_resources: true,
            selective_state_memory: true,
        },
        lifecycle: NODE_SERVICE_IMPLEMENTATION_GUARANTEES.to_vec(),
        implementation_guarantees: NODE_SERVICE_IMPLEMENTATION_GUARANTEES.to_vec(),
    }
}

/// Converts one compiled connection to a report-safe summary.
fn connection_report(connection: &CompiledConnection) -> ConnectionConformance {
    ConnectionConformance {
        id: connection.id().to_string(),
        driver: driver_name(connection.driver_kind()),
        owner: connection.owner().to_string(),
        endpoint: connection.endpoint().to_string(),
    }
}

/// Converts one compiled capability to a report-safe summary.
fn capability_report(capability: &CompiledCapability) -> CapabilityConformance {
    CapabilityConformance {
        contract: capability.contract().to_string(),
        owner: capability.owner().to_string(),
        readiness: capability
            .readiness()
            .map(|readiness| step_report(readiness.step())),
        required_resources: capability.required_resources().iter().cloned().collect(),
        local_locks: capability.local_locks().iter().cloned().collect(),
        exact_readiness: capability.readiness().is_some(),
        workflow: WorkflowConformance {
            execute: capability
                .workflow()
                .execute()
                .iter()
                .map(step_report)
                .collect(),
            status: capability
                .workflow()
                .status()
                .iter()
                .map(step_report)
                .collect(),
            cancel: capability
                .workflow()
                .cancel()
                .iter()
                .map(step_report)
                .collect(),
            local_handle_mapped: true,
            execution_state_mapped: mapped_state_contract(capability),
        },
    }
}

/// Converts one compiled profile into report-safe capability evidence.
fn capability_profile_report(profile: &CompiledCapabilityProfile) -> CapabilityProfileConformance {
    CapabilityProfileConformance {
        contract: profile.contract().to_string(),
        owner: profile.owner().to_string(),
        attributes: profile
            .attributes()
            .iter()
            .map(|(name, value)| (name.clone(), execution_value_json(value)))
            .collect(),
        readiness: profile
            .readiness()
            .map(|readiness| step_report(readiness.step())),
    }
}

/// Converts one compiled operation into a report-safe workflow mapping.
fn operation_report(operation: &CompiledOperation) -> OperationConformance {
    OperationConformance {
        operation: operation.operation().to_string(),
        owner: operation.owner().to_string(),
        required_resources: operation.required_resources().iter().cloned().collect(),
        local_locks: operation.local_locks().iter().cloned().collect(),
        workflow: workflow_report(operation),
    }
}

/// Converts one transport-neutral scalar to JSON without changing its type.
fn execution_value_json(value: &domain::ExecutionValue) -> serde_json::Value {
    match value {
        domain::ExecutionValue::Bool(value) => serde_json::Value::Bool(*value),
        domain::ExecutionValue::Integer(value) => serde_json::Value::Number((*value).into()),
        domain::ExecutionValue::Float(value) => serde_json::Number::from_f64(*value)
            .map(serde_json::Value::Number)
            .expect("compiled capability attributes contain only finite floats"),
        domain::ExecutionValue::String(value) => serde_json::Value::String(value.clone()),
    }
}

/// Builds the common fixed workflow proof used by legacy and current reports.
fn workflow_report(operation: &CompiledOperation) -> WorkflowConformance {
    WorkflowConformance {
        execute: operation
            .workflow()
            .execute()
            .iter()
            .map(step_report)
            .collect(),
        status: operation
            .workflow()
            .status()
            .iter()
            .map(step_report)
            .collect(),
        cancel: operation
            .workflow()
            .cancel()
            .iter()
            .map(step_report)
            .collect(),
        local_handle_mapped: true,
        execution_state_mapped: mapped_state_contract(operation),
    }
}

/// Converts one compiled workflow step to a report-safe summary.
fn step_report(step: &CompiledWorkflowStep) -> StepConformance {
    StepConformance {
        id: step.id().to_string(),
        connection: step.connection().to_string(),
        driver: operation_driver_name(step.operation()),
        operation: operation_summary(step.operation()),
    }
}

/// Returns the driver family represented by one operation.
fn operation_driver_name(operation: &LocalOperationConfig) -> String {
    match operation {
        LocalOperationConfig::Http { .. } => "http".to_string(),
        LocalOperationConfig::GrpcUnary { .. } | LocalOperationConfig::GrpcServerStream { .. } => {
            "grpc".to_string()
        }
        LocalOperationConfig::McpTool { .. } => "mcp".to_string(),
    }
}

/// Returns a stable summary of one fixed operation without dynamic request data.
fn operation_summary(operation: &LocalOperationConfig) -> String {
    match operation {
        LocalOperationConfig::Http { method, path } => format!("http {method} {path}"),
        LocalOperationConfig::GrpcUnary { service, method } => {
            format!("grpc unary {service}.{method}")
        }
        LocalOperationConfig::GrpcServerStream { service, method } => {
            format!("grpc server-stream {service}.{method}")
        }
        LocalOperationConfig::McpTool { tool } => format!("mcp tool {tool}"),
    }
}

/// Returns the stable report spelling for a compiled driver family.
fn driver_name(kind: crate::DriverKind) -> String {
    match kind {
        crate::DriverKind::Http => "http".to_string(),
        crate::DriverKind::Grpc => "grpc".to_string(),
        crate::DriverKind::Mcp => "mcp".to_string(),
    }
}

/// Confirms that the production compiler accepted every canonical execution phase mapping.
fn mapped_state_contract(operation: &CompiledOperation) -> bool {
    operation.workflow().execution_state_mapped()
}

#[cfg(test)]
#[path = "conformance/tests.rs"]
mod tests;
