//! Shared Local Integration catalog test fixtures.

use super::*;
use crate::{RequestBindingConfig, RequestMappingConfig, ValueExpressionConfig};

/// Returns a valid multi-system catalog fixture with all supported drivers.
fn valid_config(_descriptor_set: PathBuf) -> NodeServiceConfig {
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
    let step = |id: &str, connection: &str, operation: LocalOperationConfig| WorkflowStepConfig {
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
                descriptor_set: None,
                reflection: true,
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
            readiness: None,
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
        capability_profiles: Vec::new(),
        operations: Vec::new(),
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
        state_exports: Vec::new(),
        peer_channel_observers: Vec::new(),
        memory_providers: Vec::new(),
    }
}

/// Adds the exact readiness declaration required by node-config/v0.4 and later.
fn add_readiness(config: &mut NodeServiceConfig) {
    config.capabilities[0].readiness = Some(CapabilityReadinessConfig {
        step: WorkflowStepConfig {
            id: "read-readiness".to_string(),
            connection: "motion-http".to_string(),
            operation: LocalOperationConfig::Http {
                method: "GET".to_string(),
                path: "/capabilities/reach-region".to_string(),
            },
            request: RequestMappingConfig::default(),
        },
        state_pointer: "/state".to_string(),
        detail_pointer: Some("/detail".to_string()),
        ready: vec!["READY".to_string()],
        unavailable: vec!["UNAVAILABLE".to_string()],
        case_sensitive: false,
    });
}

mod catalog;
mod memory;
mod validation;
