#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! Generic, configuration-driven RoboGuide Node Service.
//!
//! The crate contains no Local EAIOS or vendor-specific implementation. A single
//! service compiles local capability, resource, transport, and workflow declarations
//! at startup and executes them through generic HTTP, dynamic gRPC, and MCP drivers.

mod artifact;
mod config;
mod conformance;
mod engine;
mod journal;
mod local_engine;
mod service;

pub use artifact::{
    ArtifactClient, ArtifactError, ArtifactManifestEnvelope, ArtifactOutput, ArtifactProvenance,
    ArtifactStager, PreparedArtifact, ReplicaEvidenceStatus, StagedArtifact,
};
pub use config::{
    ArtifactInputBindingConfig, ArtifactOperationConfig, ArtifactOutputBindingConfig,
    ArtifactServiceConfig, CapabilityBindingConfig, CapabilityReadinessConfig, ConnectionConfig,
    CredentialSourceConfig, ExecutionStateMappingConfig, HealthCheckConfig, LocalOperationConfig,
    LocalSystemConfig, NodeServiceConfig, RequestBindingConfig, RequestMappingConfig,
    ResourceConfig, SensorConfig, ValueExpressionConfig, ValueFunction, WorkflowConfig,
    WorkflowStepConfig,
};
pub use conformance::{
    CapabilityConformance, ConformanceChecks, ConformanceDiagnostic, ConformanceError,
    ConnectionConformance, EXTENSION_CONFORMANCE_SCHEMA_V0_1, ExtensionConformanceReport,
    LifecycleConformanceInvariant, SHARED_LIFECYCLE_CONFORMANCE, StepConformance,
    WorkflowConformance, compile_extension_config, compile_extension_config_json,
};
pub use engine::{
    EngineError, ExecuteDisposition, LocalExecutionEvent, LocalIntegrationEngine, NodeObservation,
    journal_path,
};
pub use journal::{
    ArtifactFinalizationKind, ExecutionJournal, ExecutionSpec, JournalError, JournalExecution,
    JournalStatus, PrepareArtifactFreeze, PrepareDispatch, PreparedArtifactRecord,
};
pub use local_engine::driver::{
    BoxDriverFuture, CompiledDriverRequest, DriverError, DriverEvent, DriverEventStream,
    DriverKind, DriverResponse, DriverResponseStream, LocalDriver,
};
pub use local_engine::grpc_driver::GrpcDriver;
pub use local_engine::http_driver::HttpDriver;
pub use local_engine::mapping::{CompiledRequestMapping, MappingError, WorkflowContext};
pub use local_engine::mcp_driver::McpDriver;
pub use local_engine::{
    CONFIG_SCHEMA_V0_2, CONFIG_SCHEMA_V0_3, CONFIG_SCHEMA_V0_4, CapabilityReadinessFact,
    CatalogError, CompiledArtifactService, CompiledCapability, CompiledCapabilityReadiness,
    CompiledConnection, CompiledHealthCheck, CompiledLocalCatalog, CompiledLocalSystem,
    CompiledResource, CompiledSensor, CompiledWorkflow, CompiledWorkflowStep, LocalHealthFact,
    LocalHealthState, MappedExecutionFact, MappedExecutionPhase,
};
pub use service::{NodeService, NodeServiceError};

#[cfg(test)]
mod boundary_tests {
    /// Production Node Service sources remain free of Local EAIOS product branches.
    #[test]
    fn production_node_service_is_vendor_neutral() {
        let sources = [
            include_str!("config.rs"),
            include_str!("engine.rs"),
            include_str!("journal.rs"),
            include_str!("service.rs"),
            include_str!("local_engine/mod.rs"),
            include_str!("local_engine/driver.rs"),
            include_str!("local_engine/grpc_driver.rs"),
            include_str!("local_engine/http_driver.rs"),
            include_str!("local_engine/mcp_driver.rs"),
            include_str!("../../../apps/roboguide-node/src/main.rs"),
        ];
        let forbidden = [
            concat!("ro", "bonix"),
            concat!("at", "las"),
            concat!("pi", "lot"),
            concat!("ros", " topic"),
        ];
        for source in sources {
            let source = source.to_ascii_lowercase();
            assert!(
                forbidden.iter().all(|term| !source.contains(term)),
                "Node Service production source contains a Local EAIOS product term"
            );
        }
    }
}
