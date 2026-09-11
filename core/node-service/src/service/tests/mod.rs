//! Shared Node Service test fixtures.

use super::*;
use crate::local_engine::driver::{
    BoxDriverFuture, CompiledDriverRequest, DriverError, DriverEvent, DriverKind, DriverResponse,
    LocalDriver,
};
use crate::{
    ArtifactInputBindingConfig, ArtifactOperationConfig, ArtifactServiceConfig,
    CapabilityBindingConfig, CapabilityProfileConfig, CapabilityReadinessConfig, ConnectionConfig,
    ExecutionStateMappingConfig, HealthCheckConfig, LocalOperationConfig, LocalSystemConfig,
    NodeServiceConfig, OperationBindingConfig, RequestMappingConfig, ResourceConfig,
    ValueExpressionConfig, WorkflowConfig, WorkflowStepConfig,
};
use std::collections::BTreeMap;
use std::sync::Arc;
use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};

/// Builds one complete MissionPlan for the Node Service end-to-end authority path.
fn single_task_plan(
    requirement: domain::TaskRequirement,
    intent: domain::ExecutionIntent,
) -> domain::MissionPlan {
    let mission_id = requirement.mission_id().clone();
    let role_id = requirement.roles()[0].role_id().clone();
    let context_id = domain::CoordinationContextId::new("node-service-test-context")
        .expect("context identity is valid");
    let task = domain::PlannedTask::new(
        "exercise node integration",
        requirement,
        BTreeMap::from([(role_id, intent)]),
        Vec::new(),
        domain::TaskContinuity::new(context_id.clone(), BTreeMap::new(), BTreeMap::new()),
    )
    .expect("test Task is valid");
    domain::MissionPlan::new(
        domain::MissionGoal::new(mission_id.clone(), "exercise node integration")
            .expect("test Mission goal is valid"),
        domain::TaskGraph::new(mission_id, vec![task]).expect("test Task Graph is valid"),
        vec![
            domain::CoordinationContext::new(context_id, Vec::new())
                .expect("test Context is valid"),
        ],
    )
    .expect("test MissionPlan is valid")
}

/// Registration preserves multiple local-system owners from the compiled catalog.
#[test]
fn registration_aggregates_configured_local_systems() {
    let source = include_str!("../../../../../config/node.toml");
    let config: crate::NodeServiceConfig = toml::from_str(source).expect("example config parses");
    let directory = std::path::Path::new("../../config");
    let catalog =
        crate::CompiledLocalCatalog::compile(config, directory).expect("example catalog compiles");
    let registration = registration_from_catalog(&catalog);
    assert_eq!(
        registration.node_contract_version,
        integration::grpc::v0_4::NODE_CONTRACT_VERSION
    );
    assert!(!registration.local_systems.is_empty());
    assert!(
        registration
            .capability_profiles
            .iter()
            .all(|profile| !profile.local_system_id.is_empty())
    );
    assert_eq!(
        registration.operation_support.len(),
        catalog.operations().len()
    );
    assert!(
        registration
            .operation_support
            .iter()
            .all(|support| { support.operation.is_some() && !support.local_system_id.is_empty() })
    );
}

/// Resolves one checked-in Distributed Spatial Memory scenario file from the crate root.
fn spatial_scenario_path(file_name: &str) -> std::path::PathBuf {
    std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../scenarios/distributed-spatial-memory-v0.1")
        .join(file_name)
}

/// Loads one real scenario Node config, then redirects writable state into a test directory.
fn spatial_scenario_engine(
    file_name: &str,
    expected_node_id: &str,
    test_directory: &std::path::Path,
) -> crate::LocalIntegrationEngine {
    let path = spatial_scenario_path(file_name);
    let authored = crate::NodeServiceConfig::load_compiled(&path)
        .expect("checked-in scenario Node config compiles without filesystem side effects");
    assert_eq!(authored.node_id(), expected_node_id);
    assert_eq!(authored.capabilities().len(), 4);
    for (contract, operation) in [
        (
            "spatial.map.build@v0",
            ArtifactOperationConfig::PrepareOutput,
        ),
        ("spatial.map.publish@v0", ArtifactOperationConfig::Publish),
        ("spatial.map.import@v0", ArtifactOperationConfig::Import),
        (
            "spatial.localization.verify@v0",
            ArtifactOperationConfig::Verify,
        ),
    ] {
        assert_eq!(
            authored
                .capabilities()
                .get(contract)
                .expect("scenario capability is declared")
                .artifact_operation(),
            Some(operation),
            "scenario capability must fix the canonical artifact operation"
        );
    }

    let mut config = crate::NodeServiceConfig::load(&path).expect("scenario Node config loads");
    config.state_directory = test_directory.join(expected_node_id).join("node-state");
    config
        .artifacts
        .as_mut()
        .expect("scenario config enables artifacts")
        .cache_directory = test_directory.join(expected_node_id).join("artifact-cache");
    let catalog = crate::CompiledLocalCatalog::compile(
        config,
        path.parent().expect("scenario config has a parent"),
    )
    .expect("scenario Node catalog compiles with isolated test paths");
    crate::LocalIntegrationEngine::new(
        catalog,
        vec![Arc::new(GatedDriver::new(Arc::new(AtomicBool::new(false)))) as Arc<dyn LocalDriver>],
    )
    .expect("scenario Local Integration Engine compiles offline")
}

/// Returns the Node registration spelling for one domain capability category.
const fn capability_kind_name(kind: domain::CapabilityKind) -> &'static str {
    match kind {
        domain::CapabilityKind::Mobility => "mobility",
        domain::CapabilityKind::Transport => "transport",
        domain::CapabilityKind::Compute => "compute",
        domain::CapabilityKind::Observation => "observation",
    }
}

/// Returns the Node registration spelling for one domain resource category.
const fn resource_kind_name(kind: domain::ResourceKind) -> &'static str {
    match kind {
        domain::ResourceKind::Space => "space",
        domain::ResourceKind::Compute => "compute",
        domain::ResourceKind::Time => "time",
    }
}

/// Checks that one registration advertises exactly one eligible capability for a role.
fn registration_supports_role(
    registration: &NodeRegistration,
    role: &domain::RoleRequirement,
) -> bool {
    let Some(contract) = role.required_contract() else {
        return false;
    };
    let contract = contract.to_string();
    let matching_capabilities = registration
        .capabilities
        .iter()
        .filter(|capability| {
            capability.available
                && capability.kind
                    == capability_kind_name(
                        role.capability()
                            .expect("legacy fixture has a coarse capability"),
                    )
                && capability.contracts.iter().any(|item| item == &contract)
        })
        .count();
    let has_resource = role.resource_requirements().iter().all(|required| {
        registration.resources.iter().any(|resource| {
            resource.kind == resource_kind_name(required.kind())
                && resource.capacity >= required.units()
        })
    });
    matching_capabilities == 1 && has_resource
}

/// Loads the scenario placement fixture while rejecting missing or duplicate Actor entries.
fn spatial_actor_placements() -> BTreeMap<(String, String), String> {
    let source = std::fs::read_to_string(spatial_scenario_path("actor-placement.json"))
        .expect("actor placement fixture reads");
    let document: serde_json::Value =
        serde_json::from_str(&source).expect("actor placement fixture parses");
    assert_eq!(
        document.get("schema").and_then(serde_json::Value::as_str),
        Some("roboguide.actor-placement/v0.1")
    );
    let constraints = document
        .get("constraints")
        .and_then(serde_json::Value::as_array)
        .expect("actor placement constraints are present");
    let mut placements = BTreeMap::new();
    for constraint in constraints {
        let mission_id = constraint
            .get("mission_id")
            .and_then(serde_json::Value::as_str)
            .expect("placement mission identity is present");
        let actor_id = constraint
            .get("actor_id")
            .and_then(serde_json::Value::as_str)
            .expect("placement Actor identity is present");
        let node_id = constraint
            .get("node_id")
            .and_then(serde_json::Value::as_str)
            .expect("placement Node identity is present");
        assert!(
            placements
                .insert(
                    (mission_id.to_string(), actor_id.to_string()),
                    node_id.to_string(),
                )
                .is_none(),
            "placement fixture must not duplicate a Mission/Actor"
        );
    }
    placements
}

/// The two checked-in Node configs compile and uniquely cover every placed scenario role.
#[test]
fn spatial_scenario_node_configs_cover_all_mission_roles() {
    let directory = tempfile::tempdir().expect("isolated Node state directory exists");
    let dog_a = spatial_scenario_engine("dog-a-node-v0.1.toml", "dog-a", directory.path());
    let dog_b = spatial_scenario_engine("dog-b-node-v0.1.toml", "dog-b", directory.path());
    let dog_a_artifacts = dog_a
        .catalog()
        .artifact_service()
        .expect("dog-a artifact bindings compile");
    assert!(dog_a_artifacts.output_bindings().contains_key("map-a-r1"));
    assert!(dog_a_artifacts.input_bindings().contains_key("map-b-r1"));
    let dog_b_artifacts = dog_b
        .catalog()
        .artifact_service()
        .expect("dog-b artifact bindings compile");
    assert!(dog_b_artifacts.output_bindings().contains_key("map-b-r1"));
    assert!(dog_b_artifacts.input_bindings().contains_key("map-a-r1"));

    let registrations = [
        registration_from_catalog(dog_a.catalog()),
        registration_from_catalog(dog_b.catalog()),
    ];
    let mut bridge = orchestration::IntegrationRuntimeBridge::new(
        control::ControlPlane::new(),
        state::InMemorySharedNodeState::new(),
        testkit::InMemoryEventLog::new(),
        integration::GrpcNodeRouter::default(),
    );
    let correlation = domain::CorrelationId::new("spatial-config-registration-test")
        .expect("correlation identity is valid");
    for (index, registration) in registrations.iter().cloned().enumerate() {
        bridge
            .consume(
                integration::GrpcNodeEvent::Registered {
                    session_id: format!("spatial-session-{index}"),
                    lease_id: format!("spatial-lease-{index}"),
                    registration,
                },
                domain::TimestampMs::new(index as u64),
                &correlation,
            )
            .expect("both real Node registrations coexist in Control authority");
    }
    let placements = spatial_actor_placements();
    let missions = [
        ("mission-a-build-publish.json", "dog-a"),
        ("mission-a-import-verify.json", "dog-a"),
        ("mission-b-build-publish.json", "dog-b"),
        ("mission-b-import-verify.json", "dog-b"),
    ];
    for (file_name, expected_node_id) in missions {
        let source = std::fs::read_to_string(spatial_scenario_path(file_name))
            .expect("Mission fixture reads");
        let plan = orchestration::decode_mission_plan(&source)
            .expect("Mission fixture decodes through the production adapter");
        for task in plan.task_graph().tasks() {
            for role in task.requirement().roles() {
                let actor_id = role.actor_id().expect("scenario role has an Actor");
                let placement = placements
                    .get(&(
                        plan.goal().mission_id().as_str().to_string(),
                        actor_id.as_str().to_string(),
                    ))
                    .expect("scenario Mission/Actor has explicit placement");
                assert_eq!(placement, expected_node_id);
                let candidates = registrations
                    .iter()
                    .filter(|registration| {
                        registration.node_id == *placement
                            && registration_supports_role(registration, role)
                    })
                    .collect::<Vec<_>>();
                assert_eq!(
                    candidates.len(),
                    1,
                    "{file_name} task {} role {} must have one placed registration candidate",
                    task.task_id(),
                    role.role_id()
                );
            }
        }
    }
}

/// Driver that proves heartbeat status follows Local EAIOS facts.
struct OfflineHealthDriver;

impl LocalDriver for OfflineHealthDriver {
    /// Uses the configured HTTP driver family.
    fn kind(&self) -> DriverKind {
        DriverKind::Http
    }

    /// Reports the local endpoint as unavailable.
    fn invoke<'a>(&'a self, _request: &'a CompiledDriverRequest) -> BoxDriverFuture<'a> {
        Box::pin(async {
            Err(DriverError::Transport(
                "local runtime is unavailable".to_string(),
            ))
        })
    }
}

/// Deterministic driver whose status becomes terminal only after the test gate opens.
struct GatedDriver {
    /// Local physical completion gate.
    completed: Arc<AtomicBool>,
    /// Optional sink proving the exact request delivered to the Local EAIOS boundary.
    dispatch_requests: Option<Arc<std::sync::Mutex<Vec<serde_json::Value>>>>,
}

impl GatedDriver {
    /// Creates a deterministic lifecycle driver without retaining request bodies.
    fn new(completed: Arc<AtomicBool>) -> Self {
        Self {
            completed,
            dispatch_requests: None,
        }
    }

    /// Creates a driver that records physical dispatch request bodies for boundary assertions.
    fn recording(
        completed: Arc<AtomicBool>,
        dispatch_requests: Arc<std::sync::Mutex<Vec<serde_json::Value>>>,
    ) -> Self {
        Self {
            completed,
            dispatch_requests: Some(dispatch_requests),
        }
    }
}

impl LocalDriver for GatedDriver {
    /// This mock implements the same generic HTTP driver family selected by config.
    fn kind(&self) -> DriverKind {
        DriverKind::Http
    }

    /// Produces one structured response without embedding any Local EAIOS semantics.
    fn invoke<'a>(&'a self, request: &'a CompiledDriverRequest) -> BoxDriverFuture<'a> {
        Box::pin(async move {
            let CompiledDriverRequest::Http { path, body, .. } = request else {
                return Err(DriverError::KindMismatch);
            };
            if path == "/dispatch"
                && let Some(requests) = &self.dispatch_requests
            {
                requests
                    .lock()
                    .map_err(|_| DriverError::InvalidResponse("request sink poisoned".into()))?
                    .push(body.clone());
            }
            let payload = match path.as_str() {
                "/health" => serde_json::json!({ "state": "ONLINE", "detail": "ready" }),
                "/dispatch" => serde_json::json!({ "execution_id": "local-1" }),
                "/status" if self.completed.load(Ordering::SeqCst) => {
                    serde_json::json!({ "state": "COMPLETED", "detail": "done" })
                }
                "/status" => {
                    serde_json::json!({ "state": "RUNNING", "detail": "moving" })
                }
                "/cancel" => serde_json::json!({ "accepted": true }),
                "/readiness" if self.completed.load(Ordering::SeqCst) => {
                    serde_json::json!({ "state": "READY", "detail": "service available" })
                }
                "/readiness" => serde_json::json!({
                    "state": "UNAVAILABLE",
                    "detail": "service unavailable"
                }),
                _ => return Err(DriverError::InvalidResponse("unknown mock path".into())),
            };
            let (sender, receiver) = tokio::sync::mpsc::channel(1);
            let _ = sender
                .send(Ok(DriverEvent {
                    sequence: 1,
                    payload,
                    terminal: true,
                }))
                .await;
            Ok(DriverResponse { events: receiver })
        })
    }
}

/// Driver that models a request timeout without revealing whether the local call started.
struct TimeoutDriver {
    /// Number of local dispatch attempts observed by the test facade.
    calls: Arc<AtomicUsize>,
}

impl LocalDriver for TimeoutDriver {
    /// This mock uses the configured HTTP workflow family.
    fn kind(&self) -> DriverKind {
        DriverKind::Http
    }

    /// Returns an ambiguous transport timeout after counting one dispatch attempt.
    fn invoke<'a>(&'a self, _request: &'a CompiledDriverRequest) -> BoxDriverFuture<'a> {
        let calls = Arc::clone(&self.calls);
        Box::pin(async move {
            calls.fetch_add(1, Ordering::SeqCst);
            Err(DriverError::Transport(
                "local request timed out".to_string(),
            ))
        })
    }
}

/// Builds one immutable generic workflow catalog for the end-to-end test.
fn gated_catalog(
    endpoint: String,
    state_directory: std::path::PathBuf,
) -> crate::CompiledLocalCatalog {
    gated_catalog_with_artifacts(endpoint, state_directory, None, false)
}

/// Builds a current semantic-contract catalog for end-to-end intent transport tests.
fn semantic_gated_catalog(
    endpoint: String,
    state_directory: std::path::PathBuf,
) -> crate::CompiledLocalCatalog {
    gated_catalog_for_contract(endpoint, state_directory, None, false, true)
}

/// Builds the generic test catalog with one optional immutable map-input binding.
fn gated_catalog_with_artifacts(
    endpoint: String,
    state_directory: std::path::PathBuf,
    artifact_endpoint: Option<String>,
    readiness: bool,
) -> crate::CompiledLocalCatalog {
    gated_catalog_for_contract(
        endpoint,
        state_directory,
        artifact_endpoint,
        readiness,
        false,
    )
}

/// Builds either a legacy combined or current split Node catalog from one workflow fixture.
fn gated_catalog_for_contract(
    endpoint: String,
    state_directory: std::path::PathBuf,
    artifact_endpoint: Option<String>,
    readiness: bool,
    semantic_contract: bool,
) -> crate::CompiledLocalCatalog {
    let step = |id: &str, path: &str, request: RequestMappingConfig| WorkflowStepConfig {
        id: id.to_string(),
        connection: "local".to_string(),
        operation: LocalOperationConfig::Http {
            method: "POST".to_string(),
            path: path.to_string(),
        },
        request,
    };
    let handle_request = RequestMappingConfig {
        base: serde_json::json!({}),
        bindings: vec![crate::RequestBindingConfig {
            target: "/execution_id".to_string(),
            value: ValueExpressionConfig::Pointer {
                pointer: "/local_handle".to_string(),
            },
        }],
    };
    let artifacts = artifact_endpoint.map(|artifact_endpoint| ArtifactServiceConfig {
        endpoint: artifact_endpoint,
        cache_directory: state_directory.join("artifact-cache"),
        max_artifact_bytes: 1024,
        chunk_size_bytes: 4,
        connect_timeout_ms: 5_000,
        read_timeout_ms: 30_000,
        input_bindings: vec![ArtifactInputBindingConfig {
            id: "lab-r1-input".to_string(),
            map_id: "lab".to_string(),
            revision_id: "r1".to_string(),
            content_digest: None,
            target_path: std::path::PathBuf::from("inputs/lab-r1.bundle"),
        }],
        output_bindings: Vec::new(),
    });
    let artifact_operation = artifacts.as_ref().map(|_| ArtifactOperationConfig::Import);
    let capability_readiness = readiness.then(|| CapabilityReadinessConfig {
        step: step("readiness", "/readiness", RequestMappingConfig::default()),
        state_pointer: "/state".to_string(),
        detail_pointer: Some("/detail".to_string()),
        ready: vec!["READY".to_string()],
        unavailable: vec!["UNAVAILABLE".to_string()],
        case_sensitive: false,
    });
    let mut workflow = WorkflowConfig {
        execute: vec![step(
            "dispatch",
            "/dispatch",
            RequestMappingConfig::default(),
        )],
        status: vec![step("status", "/status", handle_request.clone())],
        cancel: vec![step("cancel", "/cancel", handle_request)],
        local_handle: ValueExpressionConfig::Pointer {
            pointer: "/steps/dispatch/execution_id".to_string(),
        },
        poll_interval_ms: 10,
        execution_state: ExecutionStateMappingConfig {
            state_pointer: "/steps/status/state".to_string(),
            reason_pointer: Some("/steps/status/detail".to_string()),
            accepted: Vec::new(),
            running: vec!["RUNNING".to_string()],
            completed: vec!["COMPLETED".to_string()],
            failed: vec!["FAILED".to_string()],
            cancelled: vec!["CANCELLED".to_string()],
            case_sensitive: false,
        },
    };
    if semantic_contract {
        workflow.execute[0].request = RequestMappingConfig {
            base: serde_json::json!({}),
            bindings: vec![crate::RequestBindingConfig {
                target: "/invocation".to_string(),
                value: ValueExpressionConfig::Pointer {
                    pointer: "/invocation".to_string(),
                },
            }],
        };
    }
    let (schema, capabilities, capability_profiles, operations) = if semantic_contract {
        let readiness = capability_readiness.unwrap_or_else(|| CapabilityReadinessConfig {
            step: step("readiness", "/readiness", RequestMappingConfig::default()),
            state_pointer: "/state".to_string(),
            detail_pointer: Some("/detail".to_string()),
            ready: vec!["READY".to_string()],
            unavailable: vec!["UNAVAILABLE".to_string()],
            case_sensitive: false,
        });
        (
            crate::CONFIG_SCHEMA_V0_7.to_string(),
            Vec::new(),
            vec![CapabilityProfileConfig {
                contract: "mobility.reach_region@v1".to_string(),
                kind: "mobility".to_string(),
                owner: "motion".to_string(),
                attributes: BTreeMap::new(),
                readiness,
            }],
            vec![OperationBindingConfig {
                operation: "mobility.reach_region@v1".to_string(),
                owner: "motion".to_string(),
                required_resources: vec!["base".to_string()],
                local_locks: vec!["locomotion".to_string()],
                artifact_operation,
                workflow,
            }],
        )
    } else {
        (
            if readiness {
                crate::CONFIG_SCHEMA_V0_4.to_string()
            } else if artifacts.is_some() {
                crate::CONFIG_SCHEMA_V0_3.to_string()
            } else {
                crate::CONFIG_SCHEMA_V0_2.to_string()
            },
            vec![CapabilityBindingConfig {
                contract: "mobility.reach_region@v1".to_string(),
                kind: "mobility".to_string(),
                owner: "motion".to_string(),
                required_resources: vec!["base".to_string()],
                local_locks: vec!["locomotion".to_string()],
                artifact_operation,
                readiness: capability_readiness,
                workflow,
            }],
            Vec::new(),
            Vec::new(),
        )
    };
    crate::CompiledLocalCatalog::compile(
        NodeServiceConfig {
            schema,
            node_id: "dog-a".to_string(),
            server_endpoint: endpoint,
            state_directory,
            reconnect_delay_ms: 10,
            local_systems: vec![LocalSystemConfig {
                id: "motion".to_string(),
                runtime_name: "configured-runtime".to_string(),
                runtime_version: "1".to_string(),
                metadata: BTreeMap::new(),
                health: HealthCheckConfig {
                    step: step("health", "/health", RequestMappingConfig::default()),
                    state_pointer: "/state".to_string(),
                    detail_pointer: Some("/detail".to_string()),
                    online: vec!["ONLINE".to_string()],
                    degraded: vec!["DEGRADED".to_string()],
                    offline: vec!["OFFLINE".to_string()],
                    case_sensitive: false,
                },
            }],
            connections: vec![ConnectionConfig::Http {
                id: "local".to_string(),
                local_system: "motion".to_string(),
                endpoint: "http://127.0.0.1:8080".to_string(),
                timeout_ms: 1_000,
                headers: BTreeMap::new(),
            }],
            capabilities,
            capability_profiles,
            operations,
            resources: vec![
                ResourceConfig {
                    id: "base".to_string(),
                    kind: "space".to_string(),
                    capacity: 1,
                    owner: "motion".to_string(),
                    metadata: BTreeMap::new(),
                },
                ResourceConfig {
                    id: "aux".to_string(),
                    kind: "compute".to_string(),
                    capacity: 1,
                    owner: "motion".to_string(),
                    metadata: BTreeMap::new(),
                },
            ],
            sensors: Vec::new(),
            artifacts,
            state_exports: Vec::new(),
            peer_channel_observers: Vec::new(),
            memory_providers: Vec::new(),
        },
        std::path::Path::new("."),
    )
    .expect("generic catalog compiles")
}

mod artifact;
mod execution;
mod protocol;
mod workflow;
