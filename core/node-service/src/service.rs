//! Formal Node Protocol v0.2 lifecycle around the generic Local Integration Engine.

use crate::{EngineError, ExecuteDisposition, LocalIntegrationEngine};
use integration::grpc::v0_2::node_message::Message as NodePayload;
use integration::grpc::v0_2::robo_guide_node_protocol_client::RoboGuideNodeProtocolClient;
use integration::grpc::v0_2::server_message::Message as ServerPayload;
use integration::grpc::v0_2::{
    Cancel, Capability, ExecutionEvent, Heartbeat, Hello, LocalRuntime, LocalSystemDescriptor,
    NODE_CONTRACT_VERSION, NodeMessage, NodeRegistration, PROTOCOL_VERSION, ProtocolError,
    Register, Resource, Sensor, ServerMessage,
};
use std::fmt::{Display, Formatter};
use tokio::sync::{broadcast, mpsc};
use tokio_stream::wrappers::UnboundedReceiverStream;

/// Long-running, vendor-neutral node-side RoboGuide service.
pub struct NodeService {
    /// Generic local catalog, drivers, locks, journal, and workflow tasks.
    engine: LocalIntegrationEngine,
}

impl NodeService {
    /// Creates the single node-side service around an immutable local engine.
    pub const fn new(engine: LocalIntegrationEngine) -> Self {
        Self { engine }
    }

    /// Recovers durable executions and reconnects forever after session loss.
    pub async fn run(&self) -> Result<(), NodeServiceError> {
        self.engine.recover()?;
        loop {
            if let Err(error) = self.run_session().await {
                eprintln!("roboguide-node session ended: {error}");
            }
            tokio::time::sleep(std::time::Duration::from_millis(
                self.engine.catalog().reconnect_delay_ms(),
            ))
            .await;
        }
    }

    /// Runs one Hello -> Welcome -> Register -> Registered v0.2 session.
    pub async fn run_session(&self) -> Result<(), NodeServiceError> {
        let catalog = self.engine.catalog();
        let mut client =
            RoboGuideNodeProtocolClient::connect(catalog.server_endpoint().to_string())
                .await
                .map_err(NodeServiceError::Transport)?;
        let (outbound, receiver) = mpsc::unbounded_channel();
        outbound
            .send(NodeMessage {
                message: Some(NodePayload::Hello(Hello {
                    node_id: catalog.node_id().to_string(),
                    protocol_versions: vec![PROTOCOL_VERSION.to_string()],
                    node_contract_versions: vec![NODE_CONTRACT_VERSION.to_string()],
                })),
            })
            .map_err(|_| NodeServiceError::Closed)?;
        let mut inbound = client
            .node_session(UnboundedReceiverStream::new(receiver))
            .await
            .map_err(NodeServiceError::Status)?
            .into_inner();
        let welcome = next_server_payload(&mut inbound).await?;
        let ServerPayload::Welcome(welcome) = welcome else {
            return Err(NodeServiceError::Protocol("expected Welcome".to_string()));
        };
        if welcome.selected_protocol_version != PROTOCOL_VERSION
            || welcome.selected_node_contract_version != NODE_CONTRACT_VERSION
        {
            return Err(NodeServiceError::Protocol(
                "server selected an unsupported protocol or contract".to_string(),
            ));
        }
        outbound
            .send(NodeMessage {
                message: Some(NodePayload::Register(Register {
                    registration: Some(registration_from_catalog(catalog)),
                })),
            })
            .map_err(|_| NodeServiceError::Closed)?;
        let registered = next_server_payload(&mut inbound).await?;
        let ServerPayload::Registered(registered) = registered else {
            return Err(NodeServiceError::Protocol(
                "expected Registered".to_string(),
            ));
        };
        let mut local_events = self.engine.subscribe();
        self.replay_snapshots(&registered.session_id, &outbound)?;
        let heartbeat_task = self.spawn_heartbeat(
            registered.session_id.clone(),
            registered.lease_id.clone(),
            welcome.heartbeat_interval_ms.max(1),
            outbound.clone(),
        );
        let session_result: Result<(), NodeServiceError> = async {
            loop {
                tokio::select! {
                    message = inbound.message() => {
                        let Some(message) = message.map_err(NodeServiceError::Status)? else {
                            return Ok(());
                        };
                        self.handle_server_message(message, &registered.session_id, &outbound)?;
                    }
                    event = local_events.recv() => match event {
                        Ok(event) => outbound.send(NodeMessage { message: Some(NodePayload::ExecutionEvent(ExecutionEvent {
                            session_id: registered.session_id.clone(),
                            execution_id: event.execution_id,
                            sequence: event.sequence,
                            phase: event.phase as i32,
                            reason: event.reason,
                        })) }).map_err(|_| NodeServiceError::Closed)?,
                        Err(broadcast::error::RecvError::Lagged(_)) => self.replay_snapshots(&registered.session_id, &outbound)?,
                        Err(broadcast::error::RecvError::Closed) => return Err(NodeServiceError::Closed),
                    },
                }
            }
        }.await;
        heartbeat_task.abort();
        session_result
    }

    /// Runs health observation independently so a slow local system cannot block Cancel/Execute.
    fn spawn_heartbeat(
        &self,
        session_id: String,
        lease_id: String,
        interval_ms: u64,
        outbound: mpsc::UnboundedSender<NodeMessage>,
    ) -> tokio::task::JoinHandle<()> {
        let engine = self.engine.clone();
        tokio::spawn(async move {
            let mut heartbeat =
                tokio::time::interval(std::time::Duration::from_millis(interval_ms));
            let mut sequence = 0_u64;
            loop {
                heartbeat.tick().await;
                sequence = sequence.saturating_add(1);
                let status = engine.status().await;
                if outbound
                    .send(NodeMessage {
                        message: Some(NodePayload::Heartbeat(Heartbeat {
                            session_id: session_id.clone(),
                            lease_id: lease_id.clone(),
                            sequence,
                            status: Some(status),
                        })),
                    })
                    .is_err()
                {
                    break;
                }
            }
        })
    }

    /// Handles commands without allowing Server input to select Local How.
    fn handle_server_message(
        &self,
        message: ServerMessage,
        session_id: &str,
        outbound: &mpsc::UnboundedSender<NodeMessage>,
    ) -> Result<(), NodeServiceError> {
        match message.message {
            Some(ServerPayload::Execute(execute)) if execute.session_id == session_id => {
                let invocation = execute.invocation.ok_or_else(|| {
                    NodeServiceError::Protocol("Execute lacks canonical invocation".to_string())
                })?;
                let execution_id = execute.execution_id;
                match self
                    .engine
                    .execute(execution_id.clone(), invocation, execute.resource_ids)
                {
                    Ok(ExecuteDisposition::Started) => {}
                    Ok(ExecuteDisposition::Existing(mut snapshot)) => {
                        snapshot.session_id = session_id.to_string();
                        outbound
                            .send(NodeMessage {
                                message: Some(NodePayload::ExecutionSnapshot(snapshot)),
                            })
                            .map_err(|_| NodeServiceError::Closed)?;
                    }
                    Err(error) => send_local_rejection(
                        outbound,
                        session_id,
                        &execution_id,
                        "execute_rejected",
                        &error,
                    )?,
                }
                Ok(())
            }
            Some(ServerPayload::Cancel(Cancel {
                session_id: command_session,
                execution_id,
            })) if command_session == session_id => {
                if let Err(error) = self.engine.cancel(&execution_id) {
                    send_local_rejection(
                        outbound,
                        session_id,
                        &execution_id,
                        "cancel_rejected",
                        &error,
                    )?;
                }
                Ok(())
            }
            Some(ServerPayload::Ack(_)) | Some(ServerPayload::Error(_)) => Ok(()),
            _ => Err(NodeServiceError::Protocol(
                "unexpected server message or session".to_string(),
            )),
        }
    }

    /// Replays durable execution state into the current transport session.
    fn replay_snapshots(
        &self,
        session_id: &str,
        outbound: &mpsc::UnboundedSender<NodeMessage>,
    ) -> Result<(), NodeServiceError> {
        for mut snapshot in self.engine.snapshots()? {
            snapshot.session_id = session_id.to_string();
            outbound
                .send(NodeMessage {
                    message: Some(NodePayload::ExecutionSnapshot(snapshot)),
                })
                .map_err(|_| NodeServiceError::Closed)?;
        }
        Ok(())
    }
}

/// Sends an explicit local rejection without changing execution terminal state.
fn send_local_rejection(
    outbound: &mpsc::UnboundedSender<NodeMessage>,
    session_id: &str,
    execution_id: &str,
    code: &str,
    error: &EngineError,
) -> Result<(), NodeServiceError> {
    outbound
        .send(NodeMessage {
            message: Some(NodePayload::Error(ProtocolError {
                code: code.to_string(),
                reason: error.to_string(),
                session_id: session_id.to_string(),
                execution_id: execution_id.to_string(),
            })),
        })
        .map_err(|_| NodeServiceError::Closed)
}

/// Builds a complete v0.2 registration from the immutable compiled catalog.
fn registration_from_catalog(catalog: &crate::CompiledLocalCatalog) -> NodeRegistration {
    let local_systems = catalog
        .local_systems()
        .values()
        .map(|system| LocalSystemDescriptor {
            id: system.id().to_string(),
            runtime: Some(LocalRuntime {
                name: system.runtime_name().to_string(),
                version: system.runtime_version().to_string(),
            }),
            metadata: system.metadata().clone().into_iter().collect(),
        })
        .collect();
    let capabilities = catalog
        .capabilities()
        .values()
        .map(|capability| Capability {
            kind: capability.kind().to_string(),
            available: true,
            contracts: vec![capability.contract().to_string()],
            local_system_id: capability.owner().to_string(),
        })
        .collect();
    let resources = catalog
        .resources()
        .values()
        .map(|resource| Resource {
            id: resource.id().to_string(),
            kind: resource.kind().to_string(),
            capacity: resource.capacity(),
            metadata: resource.metadata().clone().into_iter().collect(),
            local_system_id: resource.owner().to_string(),
        })
        .collect();
    let sensors = catalog
        .sensors()
        .values()
        .map(|sensor| Sensor {
            id: sensor.id().to_string(),
            kind: sensor.kind().to_string(),
            metadata: sensor.metadata().clone().into_iter().collect(),
            local_system_id: sensor.owner().to_string(),
        })
        .collect();
    NodeRegistration {
        node_id: catalog.node_id().to_string(),
        local_systems,
        capabilities,
        sensors,
        resources,
        metadata: Default::default(),
        node_contract_version: NODE_CONTRACT_VERSION.to_string(),
    }
}

/// Reads one required Server stream message.
async fn next_server_payload(
    inbound: &mut tonic::Streaming<ServerMessage>,
) -> Result<ServerPayload, NodeServiceError> {
    inbound
        .message()
        .await
        .map_err(NodeServiceError::Status)?
        .and_then(|message| message.message)
        .ok_or(NodeServiceError::Closed)
}

/// Node Service lifecycle failure.
#[derive(Debug)]
pub enum NodeServiceError {
    /// gRPC channel connection failed.
    Transport(tonic::transport::Error),
    /// gRPC stream status failed.
    Status(tonic::Status),
    /// Generic local engine rejected an operation.
    Engine(EngineError),
    /// Protocol lifecycle was invalid.
    Protocol(String),
    /// Stream closed.
    Closed,
}

impl Display for NodeServiceError {
    /// Formats a stable Node Service diagnostic.
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Transport(error) => error.fmt(formatter),
            Self::Status(error) => error.fmt(formatter),
            Self::Engine(error) => error.fmt(formatter),
            Self::Protocol(reason) => formatter.write_str(reason),
            Self::Closed => formatter.write_str("Node Protocol stream closed"),
        }
    }
}

impl std::error::Error for NodeServiceError {}
impl From<EngineError> for NodeServiceError {
    fn from(value: EngineError) -> Self {
        Self::Engine(value)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::local_engine::driver::{
        BoxDriverFuture, CompiledDriverRequest, DriverError, DriverEvent, DriverKind,
        DriverResponse, LocalDriver,
    };
    use crate::{
        ArtifactInputBindingConfig, ArtifactOperationConfig, ArtifactServiceConfig,
        CapabilityBindingConfig, ConnectionConfig, ExecutionStateMappingConfig, HealthCheckConfig,
        LocalOperationConfig, LocalSystemConfig, NodeServiceConfig, RequestMappingConfig,
        ResourceConfig, ValueExpressionConfig, WorkflowConfig, WorkflowStepConfig,
    };
    use std::collections::BTreeMap;
    use std::sync::Arc;
    use std::sync::atomic::{AtomicBool, Ordering};

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
        let source = include_str!("../../../config/node.toml");
        let config: crate::NodeServiceConfig =
            toml::from_str(source).expect("example config parses");
        let directory = std::path::Path::new("../../config");
        let catalog = crate::CompiledLocalCatalog::compile(config, directory)
            .expect("example catalog compiles");
        let registration = registration_from_catalog(&catalog);
        assert_eq!(registration.node_contract_version, NODE_CONTRACT_VERSION);
        assert!(!registration.local_systems.is_empty());
        assert!(
            registration
                .capabilities
                .iter()
                .all(|capability| !capability.local_system_id.is_empty())
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
            vec![Arc::new(GatedDriver {
                completed: Arc::new(AtomicBool::new(false)),
            }) as Arc<dyn LocalDriver>],
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
                    && capability.kind == capability_kind_name(role.capability())
                    && capability.contracts.iter().any(|item| item == &contract)
            })
            .count();
        let has_resource = role.resource_kind().is_none_or(|kind| {
            registration
                .resources
                .iter()
                .any(|resource| resource.kind == resource_kind_name(kind) && resource.capacity > 0)
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
        let mut bridge = integration::IntegrationRuntimeBridge::new(
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
    }

    impl LocalDriver for GatedDriver {
        /// This mock implements the same generic HTTP driver family selected by config.
        fn kind(&self) -> DriverKind {
            DriverKind::Http
        }

        /// Produces one structured response without embedding any Local EAIOS semantics.
        fn invoke<'a>(&'a self, request: &'a CompiledDriverRequest) -> BoxDriverFuture<'a> {
            Box::pin(async move {
                let CompiledDriverRequest::Http { path, .. } = request else {
                    return Err(DriverError::KindMismatch);
                };
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

    /// Builds one immutable generic workflow catalog for the end-to-end test.
    fn gated_catalog(
        endpoint: String,
        state_directory: std::path::PathBuf,
    ) -> crate::CompiledLocalCatalog {
        gated_catalog_with_artifacts(endpoint, state_directory, None)
    }

    /// Builds the generic test catalog with one optional immutable map-input binding.
    fn gated_catalog_with_artifacts(
        endpoint: String,
        state_directory: std::path::PathBuf,
        artifact_endpoint: Option<String>,
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
        crate::CompiledLocalCatalog::compile(
            NodeServiceConfig {
                schema: if artifacts.is_some() {
                    crate::CONFIG_SCHEMA_V0_3.to_string()
                } else {
                    crate::CONFIG_SCHEMA_V0_2.to_string()
                },
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
                capabilities: vec![CapabilityBindingConfig {
                    contract: "mobility.reach_region@v1".to_string(),
                    kind: "mobility".to_string(),
                    owner: "motion".to_string(),
                    required_resources: vec!["base".to_string()],
                    local_locks: vec!["locomotion".to_string()],
                    artifact_operation,
                    workflow: WorkflowConfig {
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
                    },
                }],
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
            },
            std::path::Path::new("."),
        )
        .expect("generic catalog compiles")
    }

    /// Node health reports configured local-system unavailability instead of process liveness.
    #[tokio::test]
    async fn node_health_reports_local_runtime_unavailability() {
        let state_dir = tempfile::tempdir().expect("state directory exists");
        let engine = crate::LocalIntegrationEngine::new(
            gated_catalog(
                "http://127.0.0.1:50051".to_string(),
                state_dir.path().to_path_buf(),
            ),
            vec![Arc::new(OfflineHealthDriver) as Arc<dyn LocalDriver>],
        )
        .expect("engine initializes");
        let status = engine.status().await;
        assert_eq!(status.health, "offline");
        assert!(status.detail.contains("local runtime is unavailable"));
    }

    /// Resource IDs are an unordered semantic set for execution identity.
    #[tokio::test]
    async fn execution_identity_canonicalizes_resource_order() {
        let state_dir = tempfile::tempdir().expect("state directory exists");
        let engine = crate::LocalIntegrationEngine::new(
            gated_catalog(
                "http://127.0.0.1:50051".to_string(),
                state_dir.path().to_path_buf(),
            ),
            vec![Arc::new(GatedDriver {
                completed: Arc::new(AtomicBool::new(false)),
            }) as Arc<dyn LocalDriver>],
        )
        .expect("engine initializes");
        let invocation = integration::grpc::v0_2::CanonicalInvocation {
            mission_id: "mission-a".to_string(),
            task_id: "task-a".to_string(),
            group_id: "group-a".to_string(),
            role_id: "carrier".to_string(),
            capability_contract: "mobility.reach_region@v1".to_string(),
            ..Default::default()
        };
        assert_eq!(
            engine
                .execute(
                    "resource-order".to_string(),
                    invocation.clone(),
                    vec!["aux".to_string(), "base".to_string()]
                )
                .expect("first dispatch starts"),
            crate::ExecuteDisposition::Started
        );
        assert!(matches!(
            engine
                .execute(
                    "resource-order".to_string(),
                    invocation,
                    vec!["base".to_string(), "aux".to_string()]
                )
                .expect("same resource set is idempotent"),
            crate::ExecuteDisposition::Existing(_)
        ));
    }

    /// Control-bound command reaches the local engine and terminates only on a local terminal fact.
    #[tokio::test]
    async fn control_bound_command_round_trips_through_generic_engine() {
        use control::{ControlPlane, DeterministicBootstrapScheduler};
        use domain::{
            ActorId, Capability, CapabilityContractRef, CapabilityKind, CorrelationId,
            ExecutionGroupId, ExecutionIntent, ExecutionValue, LocalRuntime, MissionId,
            NodeContractVersion, NodeHealth, NodeId, NodeRegistration as DomainRegistration,
            NodeStatus as DomainStatus, Resource as DomainResource, ResourceId, ResourceKind,
            RoleId, RoleRequirement, TaskId, TaskRequirement, TimestampMs,
        };
        use integration::grpc::v0_2::CanonicalInvocation;
        use integration::grpc::v0_2::robo_guide_node_protocol_server::RoboGuideNodeProtocolServer;
        use integration::{
            GrpcIntegrationService, IntegrationRuntimeBridge, RemoteExecutionStatus,
        };
        use state::InMemorySharedNodeState;
        use testkit::InMemoryEventLog;

        let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
            .await
            .expect("listener binds");
        let address = listener.local_addr().expect("listener address");
        let incoming = tokio_stream::wrappers::TcpListenerStream::new(listener);
        let (events, mut event_receiver) = tokio::sync::mpsc::unbounded_channel();
        let (grpc_service, router) = GrpcIntegrationService::new(events);
        let server = tokio::spawn(async move {
            tonic::transport::Server::builder()
                .add_service(RoboGuideNodeProtocolServer::new(grpc_service))
                .serve_with_incoming(incoming)
                .await
        });
        let terminal = Arc::new(AtomicBool::new(false));
        let state_dir = tempfile::tempdir().expect("state directory exists");
        let engine = crate::LocalIntegrationEngine::new(
            gated_catalog(format!("http://{address}"), state_dir.path().to_path_buf()),
            vec![Arc::new(GatedDriver {
                completed: Arc::clone(&terminal),
            }) as Arc<dyn LocalDriver>],
        )
        .expect("engine initializes");
        let node = NodeService::new(engine.clone());
        let node_task = tokio::spawn(async move { node.run_session().await });

        let now = TimestampMs::new(0);
        let correlation = CorrelationId::new("generic-loop-test").expect("correlation valid");
        let contract =
            CapabilityContractRef::new("mobility", "reach_region", "v1").expect("contract valid");
        let registration = DomainRegistration::new_with_contracts(
            NodeId::new("dog-a").expect("node valid"),
            LocalRuntime::new("configured-runtime", "1").expect("runtime valid"),
            NodeContractVersion::new(integration::grpc::v0_2::NODE_CONTRACT_VERSION)
                .expect("contract version valid"),
            vec![Capability::new(CapabilityKind::Mobility, true)],
            vec![contract.clone()],
            vec![
                DomainResource::new(
                    ResourceId::new("base").expect("resource ID is valid"),
                    ResourceKind::Space,
                    1,
                )
                .expect("resource is valid"),
            ],
        );
        let mut control = ControlPlane::new();
        let mut state = InMemorySharedNodeState::new();
        let mut log = InMemoryEventLog::new();
        control
            .register_node(
                &mut state,
                registration,
                DomainStatus::new(NodeHealth::Online, now),
                now,
                &correlation,
                &mut log,
            )
            .expect("node registered");
        let role_id = RoleId::new("carrier").expect("role valid");
        let requirement = TaskRequirement::new(
            MissionId::new("mission-a").expect("mission valid"),
            TaskId::new("task-a").expect("task valid"),
            vec![RoleRequirement::new_with_actor_and_contract(
                role_id.clone(),
                ActorId::new("carrier").expect("actor valid"),
                CapabilityKind::Mobility,
                contract.clone(),
                Some(ResourceKind::Space),
            )],
        )
        .expect("requirement valid");
        let intent = ExecutionIntent::new(
            contract,
            BTreeMap::from([(
                "region_id".to_string(),
                ExecutionValue::String("library".to_string()),
            )]),
        )
        .expect("intent valid");
        let mission_plan = single_task_plan(requirement.clone(), intent.clone());
        let group_id = ExecutionGroupId::new("group-a").expect("group valid");
        control
            .create_mission_group(group_id.clone(), &mission_plan, now, &correlation, &mut log)
            .expect("Mission Group registers");
        control
            .ready_task_execution(
                &group_id,
                requirement.task_ref(),
                now,
                &correlation,
                &mut log,
            )
            .expect("Task becomes ready");
        let candidates = control
            .match_capabilities(&state, &requirement, now, &correlation, &mut log)
            .expect("matching succeeds");
        let decision = DeterministicBootstrapScheduler::new()
            .schedule_task(
                &state,
                &requirement,
                &candidates,
                now,
                &correlation,
                &mut log,
            )
            .expect("scheduling succeeds");
        let proposal = control
            .propose(
                &state,
                &requirement,
                &candidates,
                decision.proposed_assignments(),
                now,
                &correlation,
                &mut log,
            )
            .expect("proposal succeeds");
        let committed = control
            .commit(&proposal, now, &correlation, &mut log)
            .expect("commit succeeds");
        control
            .bind_task_execution_with_requirement(
                &group_id,
                &committed,
                &requirement,
                now,
                &correlation,
                &mut log,
            )
            .expect("Task bound");
        control
            .activate_task_execution(
                &group_id,
                requirement.task_ref(),
                now,
                &correlation,
                &mut log,
            )
            .expect("Task activates");

        let mut bridge = IntegrationRuntimeBridge::new(control, state, log, router);
        let registered =
            tokio::time::timeout(std::time::Duration::from_secs(2), event_receiver.recv())
                .await
                .expect("registration arrives")
                .expect("registration exists");
        bridge
            .consume(registered, TimestampMs::new(1), &correlation)
            .expect("registration consumed");
        let command = bridge
            .execute_task_bound(
                "execution-e2e".to_string(),
                &group_id,
                requirement.task_ref(),
                &role_id,
                intent,
                correlation.clone(),
            )
            .expect("bound command routes");
        assert_eq!(command.node_id().as_str(), "dog-a");
        while bridge.execution_status("execution-e2e") != Some(RemoteExecutionStatus::Running) {
            let event =
                tokio::time::timeout(std::time::Duration::from_secs(2), event_receiver.recv())
                    .await
                    .expect("running event arrives")
                    .expect("running event exists");
            bridge
                .consume(event, TimestampMs::new(2), &correlation)
                .expect("running event consumed");
        }
        assert_ne!(
            bridge.execution_status("execution-e2e"),
            Some(RemoteExecutionStatus::Completed)
        );
        let competing = CanonicalInvocation {
            mission_id: "mission-a".to_string(),
            task_id: "task-b".to_string(),
            group_id: "group-b".to_string(),
            role_id: "carrier".to_string(),
            capability_contract: "mobility.reach_region@v1".to_string(),
            parameters: Default::default(),
        };
        assert!(matches!(
            engine.execute(
                "execution-competing".to_string(),
                competing.clone(),
                vec!["base".to_string()]
            ),
            Err(crate::EngineError::LocalLockConflict { .. })
        ));
        terminal.store(true, Ordering::SeqCst);
        while bridge.execution_status("execution-e2e") != Some(RemoteExecutionStatus::Completed) {
            let event =
                tokio::time::timeout(std::time::Duration::from_secs(2), event_receiver.recv())
                    .await
                    .expect("terminal event arrives")
                    .expect("terminal event exists");
            bridge
                .consume(event, TimestampMs::new(3), &correlation)
                .expect("terminal event consumed");
        }
        assert_eq!(
            engine
                .execute(
                    "execution-competing".to_string(),
                    competing,
                    vec!["base".to_string()]
                )
                .expect("terminal execution releases local locks"),
            crate::ExecuteDisposition::Started
        );
        node_task.abort();
        server.abort();
    }

    /// Ambiguous pre-handle dispatches retain local locks after process restart.
    #[tokio::test]
    async fn reconciliation_required_execution_fences_local_resources_after_restart() {
        let state_dir = tempfile::tempdir().expect("state directory exists");
        let catalog = gated_catalog(
            "http://127.0.0.1:50051".to_string(),
            state_dir.path().to_path_buf(),
        );
        let journal_path = crate::journal_path(state_dir.path());
        let journal = crate::ExecutionJournal::open(&journal_path).expect("journal opens");
        let invocation = serde_json::json!({
            "mission_id": "mission-a",
            "task_id": "task-a",
            "group_id": "group-a",
            "role_id": "carrier",
            "capability_contract": "mobility.reach_region@v1",
            "parameters": {},
            "resource_ids": ["base"],
        });
        let workflow_digest = crate::engine::workflow_digest(
            &catalog,
            &catalog.capabilities()["mobility.reach_region@v1"],
            &invocation,
        )
        .expect("workflow identity computes");
        let spec = crate::ExecutionSpec::new(
            serde_json::to_vec(&invocation).expect("invocation serializes"),
            workflow_digest,
            vec!["base".to_string()],
        )
        .expect("execution spec is valid");
        assert!(matches!(
            journal
                .prepare_dispatch("ambiguous", &spec)
                .expect("dispatch is prepared"),
            crate::PrepareDispatch::Start(_)
        ));
        journal
            .authorize_local_dispatch("ambiguous")
            .expect("local dispatch is authorized");
        drop(journal);

        let engine = crate::LocalIntegrationEngine::new(
            catalog,
            vec![Arc::new(GatedDriver {
                completed: Arc::new(AtomicBool::new(false)),
            }) as Arc<dyn LocalDriver>],
        )
        .expect("engine reopens journal");
        engine.recover().expect("ambiguous execution is fenced");
        let competing = integration::grpc::v0_2::CanonicalInvocation {
            mission_id: "mission-a".to_string(),
            task_id: "task-a".to_string(),
            group_id: "group-a".to_string(),
            role_id: "carrier".to_string(),
            capability_contract: "mobility.reach_region@v1".to_string(),
            ..Default::default()
        };
        assert!(matches!(
            engine.execute(
                "new-execution".to_string(),
                competing,
                vec!["base".to_string()]
            ),
            Err(crate::EngineError::LocalLockConflict { .. })
        ));
    }

    /// A persisted local handle resumes status polling after restart without releasing its locks.
    #[tokio::test]
    async fn handle_bearing_dispatch_resumes_status_only_recovery_after_restart() {
        let state_dir = tempfile::tempdir().expect("state directory exists");
        let catalog = gated_catalog(
            "http://127.0.0.1:50051".to_string(),
            state_dir.path().to_path_buf(),
        );
        let journal_path = crate::journal_path(state_dir.path());
        let journal = crate::ExecutionJournal::open(&journal_path).expect("journal opens");
        let invocation = serde_json::json!({
            "mission_id": "mission-a",
            "task_id": "task-a",
            "group_id": "group-a",
            "role_id": "carrier",
            "capability_contract": "mobility.reach_region@v1",
            "parameters": {},
            "resource_ids": ["base"],
        });
        let workflow_digest = crate::engine::workflow_digest(
            &catalog,
            &catalog.capabilities()["mobility.reach_region@v1"],
            &invocation,
        )
        .expect("workflow identity computes");
        let spec = crate::ExecutionSpec::new(
            serde_json::to_vec(&invocation).expect("invocation serializes"),
            workflow_digest,
            vec!["base".to_string()],
        )
        .expect("execution spec is valid");
        assert!(matches!(
            journal
                .prepare_dispatch("handle-recovered", &spec)
                .expect("dispatch is prepared"),
            crate::PrepareDispatch::Start(_)
        ));
        journal
            .authorize_local_dispatch("handle-recovered")
            .expect("local dispatch is authorized");
        journal
            .record_local_handle("handle-recovered", "local-1")
            .expect("local handle persists");
        drop(journal);

        let engine = crate::LocalIntegrationEngine::new(
            catalog,
            vec![Arc::new(GatedDriver {
                completed: Arc::new(AtomicBool::new(false)),
            }) as Arc<dyn LocalDriver>],
        )
        .expect("engine reopens journal");
        let mut events = engine.subscribe();
        engine.recover().expect("known handle resumes polling");
        let event = tokio::time::timeout(std::time::Duration::from_secs(2), events.recv())
            .await
            .expect("running fact arrives")
            .expect("running fact exists");
        assert_eq!(event.execution_id, "handle-recovered");
        assert_eq!(
            event.phase,
            integration::grpc::v0_2::ExecutionPhase::Started
        );

        let competing = integration::grpc::v0_2::CanonicalInvocation {
            mission_id: "mission-a".to_string(),
            task_id: "task-b".to_string(),
            group_id: "group-a".to_string(),
            role_id: "carrier".to_string(),
            capability_contract: "mobility.reach_region@v1".to_string(),
            ..Default::default()
        };
        assert!(matches!(
            engine.execute(
                "new-execution".to_string(),
                competing,
                vec!["base".to_string()]
            ),
            Err(crate::EngineError::LocalLockConflict { .. })
        ));
    }

    /// A pending remote artifact finalization stays fenced until an exact Execute retry authorizes it.
    #[tokio::test]
    async fn artifact_finalization_marker_blocks_implicit_restart_resume() {
        let state_dir = tempfile::tempdir().expect("state directory exists");
        let catalog = gated_catalog(
            "http://127.0.0.1:50051".to_string(),
            state_dir.path().to_path_buf(),
        );
        let journal_path = crate::journal_path(state_dir.path());
        let journal = crate::ExecutionJournal::open(&journal_path).expect("journal opens");
        let invocation = serde_json::json!({
            "mission_id": "mission-a",
            "task_id": "task-a",
            "group_id": "group-a",
            "role_id": "carrier",
            "capability_contract": "mobility.reach_region@v1",
            "parameters": {},
            "resource_ids": ["base"],
        });
        let workflow_digest = crate::engine::workflow_digest(
            &catalog,
            &catalog.capabilities()["mobility.reach_region@v1"],
            &invocation,
        )
        .expect("workflow identity computes");
        let spec = crate::ExecutionSpec::new(
            serde_json::to_vec(&invocation).expect("invocation serializes"),
            workflow_digest,
            vec!["base".to_string()],
        )
        .expect("execution spec is valid");
        assert!(matches!(
            journal
                .prepare_dispatch("artifact-ambiguous", &spec)
                .expect("dispatch is prepared"),
            crate::PrepareDispatch::Start(_)
        ));
        journal
            .authorize_local_dispatch("artifact-ambiguous")
            .expect("local dispatch is authorized");
        journal
            .record_local_handle("artifact-ambiguous", "local-1")
            .expect("local handle persists");
        journal
            .prepare_artifact_finalization(
                "artifact-ambiguous",
                crate::ArtifactFinalizationKind::Publish,
            )
            .expect("artifact finalization marker persists");
        drop(journal);

        let engine = crate::LocalIntegrationEngine::new(
            catalog,
            vec![Arc::new(GatedDriver {
                completed: Arc::new(AtomicBool::new(false)),
            }) as Arc<dyn LocalDriver>],
        )
        .expect("engine reopens journal");
        let mut events = engine.subscribe();
        engine
            .recover()
            .expect("ambiguous artifact finalization remains fenced");
        assert_eq!(
            crate::ExecutionJournal::open(&journal_path)
                .expect("audit journal opens")
                .get("artifact-ambiguous")
                .expect("record reads")
                .expect("record exists")
                .status(),
            crate::JournalStatus::ReconciliationRequired
        );
        assert!(
            tokio::time::timeout(std::time::Duration::from_millis(100), events.recv())
                .await
                .is_err(),
            "restart must not status-poll or retry remote finalization before explicit Execute"
        );
    }

    /// An interrupted output freeze blocks status polling and exact Execute replay after restart.
    #[tokio::test]
    async fn artifact_preparation_marker_blocks_mutable_source_reread_after_restart() {
        let state_dir = tempfile::tempdir().expect("state directory exists");
        let catalog = gated_catalog(
            "http://127.0.0.1:50051".to_string(),
            state_dir.path().to_path_buf(),
        );
        let invocation_json = serde_json::json!({
            "mission_id": "mission-a",
            "task_id": "build-map",
            "group_id": "group-a",
            "role_id": "mapper",
            "capability_contract": "mobility.reach_region@v1",
            "parameters": {},
            "resource_ids": ["base"],
        });
        let workflow_digest = crate::engine::workflow_digest(
            &catalog,
            &catalog.capabilities()["mobility.reach_region@v1"],
            &invocation_json,
        )
        .expect("workflow identity computes");
        let journal_path = crate::journal_path(state_dir.path());
        let journal = crate::ExecutionJournal::open(&journal_path).expect("journal opens");
        let spec = crate::ExecutionSpec::new(
            serde_json::to_vec(&invocation_json).expect("invocation serializes"),
            workflow_digest,
            vec!["base".to_string()],
        )
        .expect("execution spec is valid");
        journal
            .prepare_dispatch("build-recovery", &spec)
            .expect("dispatch prepares");
        journal
            .authorize_local_dispatch("build-recovery")
            .expect("local dispatch is authorized");
        journal
            .record_local_handle("build-recovery", "local-build")
            .expect("local handle persists");
        journal
            .record_status(
                "build-recovery",
                1,
                crate::JournalStatus::Running,
                "local map builder completed",
            )
            .expect("running fact persists");
        assert_eq!(
            journal
                .prepare_artifact_freeze("build-recovery", "lab-r1-output")
                .expect("first mutable-source read is granted"),
            crate::PrepareArtifactFreeze::Start
        );
        std::fs::write(state_dir.path().join("simulated-frozen-map"), b"map-v1")
            .expect("simulated immutable snapshot writes");
        drop(journal);

        let engine = crate::LocalIntegrationEngine::new(
            catalog,
            vec![Arc::new(GatedDriver {
                completed: Arc::new(AtomicBool::new(true)),
            }) as Arc<dyn LocalDriver>],
        )
        .expect("engine reopens journal");
        let mut events = engine.subscribe();
        engine
            .recover()
            .expect("interrupted artifact preparation remains fenced");
        assert!(
            tokio::time::timeout(std::time::Duration::from_millis(100), events.recv())
                .await
                .is_err(),
            "restart must not status-poll a completed local execution and freeze the source again"
        );

        let invocation = integration::grpc::v0_2::CanonicalInvocation {
            mission_id: "mission-a".to_string(),
            task_id: "build-map".to_string(),
            group_id: "group-a".to_string(),
            role_id: "mapper".to_string(),
            capability_contract: "mobility.reach_region@v1".to_string(),
            ..Default::default()
        };
        assert!(matches!(
            engine
                .execute(
                    "build-recovery".to_string(),
                    invocation,
                    vec!["base".to_string()]
                )
                .expect("exact retry returns durable state"),
            crate::ExecuteDisposition::Existing(_)
        ));
        assert!(
            tokio::time::timeout(std::time::Duration::from_millis(100), events.recv())
                .await
                .is_err(),
            "an exact Execute retry must not grant another source read"
        );
        let audit = crate::ExecutionJournal::open(&journal_path).expect("audit journal opens");
        assert_eq!(
            audit
                .get("build-recovery")
                .expect("execution reads")
                .expect("execution exists")
                .status(),
            crate::JournalStatus::ReconciliationRequired
        );
        assert_eq!(
            audit
                .artifact_preparation("build-recovery")
                .expect("preparation marker reads"),
            Some("lab-r1-output".to_string())
        );
    }

    /// Exact input-finalization retry stays fenced when durable staged bytes are unavailable.
    #[tokio::test]
    async fn artifact_input_retry_reproves_local_bytes_before_replica_evidence() {
        use domain::{
            ContentDigest, MapArtifactManifest, MapArtifactRef, MapId, MapRevisionId,
            MapRevisionSelector, MissionId, NodeId, SpatialAnchorId, TimestampMs,
        };
        use integration::grpc::v0_2::scalar_value::Value as Scalar;
        use integration::grpc::v0_2::{CanonicalInvocation, ExecutionPhase, ScalarValue};
        use sha2::Digest;
        use std::collections::HashMap;
        use std::sync::atomic::AtomicUsize;
        use tokio::io::{AsyncReadExt, AsyncWriteExt};

        let digest = format!("sha256:{:x}", sha2::Sha256::digest(b"maps"));
        let manifest = MapArtifactManifest::new(
            MapArtifactRef::new(
                MapRevisionSelector::new(
                    MapId::new("lab").expect("map id"),
                    MapRevisionId::new("r1").expect("revision id"),
                ),
                ContentDigest::new(digest).expect("digest"),
                4,
            ),
            "application/octet-stream",
            "grid-v1",
            NodeId::new("dog-a").expect("node id"),
            None,
            MissionId::new("mission-build").expect("source mission id"),
            Some("build-execution".to_string()),
            None,
            "map",
            "enu",
            SpatialAnchorId::new("lab-origin").expect("anchor id"),
            Some(0.05),
            TimestampMs::new(7),
            None,
        )
        .expect("manifest is valid");
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
            .await
            .expect("artifact listener binds");
        let address = listener.local_addr().expect("artifact address reads");
        let replica_writes = Arc::new(AtomicUsize::new(0));
        let server_writes = Arc::clone(&replica_writes);
        let body = serde_json::to_vec(&serde_json::json!({
            "status": "published",
            "manifest": manifest,
        }))
        .expect("manifest response serializes");
        let artifact_server = tokio::spawn(async move {
            loop {
                let (mut socket, _) = listener.accept().await.expect("artifact request accepts");
                let mut request = Vec::new();
                loop {
                    let mut chunk = [0_u8; 1024];
                    let length = socket.read(&mut chunk).await.expect("request reads");
                    if length == 0 {
                        break;
                    }
                    request.extend_from_slice(&chunk[..length]);
                    if request.windows(4).any(|window| window == b"\r\n\r\n") {
                        break;
                    }
                }
                let request = String::from_utf8_lossy(&request);
                let response_body = if request.starts_with("GET /v1/maps/lab/revisions/r1 HTTP/") {
                    body.clone()
                } else {
                    server_writes.fetch_add(1, Ordering::SeqCst);
                    b"{}".to_vec()
                };
                let response = format!(
                    "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n",
                    response_body.len()
                );
                socket
                    .write_all(response.as_bytes())
                    .await
                    .expect("response headers write");
                socket
                    .write_all(&response_body)
                    .await
                    .expect("response body writes");
                socket.shutdown().await.expect("response shuts down");
            }
        });

        let state_dir = tempfile::tempdir().expect("state directory exists");
        let catalog = gated_catalog_with_artifacts(
            "http://127.0.0.1:50051".to_string(),
            state_dir.path().to_path_buf(),
            Some(format!("http://{address}")),
        );
        let parameters = HashMap::from([
            (
                "artifact_operation".to_string(),
                ScalarValue {
                    value: Some(Scalar::StringValue("import".to_string())),
                },
            ),
            (
                "artifact_slot".to_string(),
                ScalarValue {
                    value: Some(Scalar::StringValue("lab-r1-input".to_string())),
                },
            ),
            (
                "map_id".to_string(),
                ScalarValue {
                    value: Some(Scalar::StringValue("lab".to_string())),
                },
            ),
            (
                "revision_id".to_string(),
                ScalarValue {
                    value: Some(Scalar::StringValue("r1".to_string())),
                },
            ),
            (
                "spatial_anchor_id".to_string(),
                ScalarValue {
                    value: Some(Scalar::StringValue("lab-origin".to_string())),
                },
            ),
        ]);
        let invocation = CanonicalInvocation {
            mission_id: "mission-consume".to_string(),
            task_id: "import-map".to_string(),
            group_id: "group-consume".to_string(),
            role_id: "consumer".to_string(),
            capability_contract: "mobility.reach_region@v1".to_string(),
            parameters,
        };
        let invocation_json = serde_json::json!({
            "mission_id": invocation.mission_id,
            "task_id": invocation.task_id,
            "group_id": invocation.group_id,
            "role_id": invocation.role_id,
            "capability_contract": invocation.capability_contract,
            "parameters": {
                "artifact_operation": "import",
                "artifact_slot": "lab-r1-input",
                "map_id": "lab",
                "revision_id": "r1",
                "spatial_anchor_id": "lab-origin",
            },
            "resource_ids": ["base"],
        });
        let workflow_digest = crate::engine::workflow_digest(
            &catalog,
            &catalog.capabilities()["mobility.reach_region@v1"],
            &invocation_json,
        )
        .expect("workflow identity computes");
        let journal_path = crate::journal_path(state_dir.path());
        let journal = crate::ExecutionJournal::open(&journal_path).expect("journal opens");
        let spec = crate::ExecutionSpec::new(
            serde_json::to_vec(&invocation_json).expect("invocation serializes"),
            workflow_digest,
            vec!["base".to_string()],
        )
        .expect("execution spec is valid");
        journal
            .prepare_dispatch("import-recovery", &spec)
            .expect("dispatch prepares");
        journal
            .authorize_local_dispatch("import-recovery")
            .expect("local dispatch is authorized");
        journal
            .record_local_handle("import-recovery", "local-1")
            .expect("local handle persists");
        journal
            .record_status(
                "import-recovery",
                1,
                crate::JournalStatus::Running,
                "local import completed",
            )
            .expect("running fact persists");
        journal
            .prepare_artifact_finalization(
                "import-recovery",
                crate::ArtifactFinalizationKind::Import,
            )
            .expect("finalization marker persists");
        drop(journal);

        let engine = crate::LocalIntegrationEngine::new(
            catalog,
            vec![Arc::new(GatedDriver {
                completed: Arc::new(AtomicBool::new(false)),
            }) as Arc<dyn LocalDriver>],
        )
        .expect("engine reopens journal");
        engine.recover().expect("pending finalization is fenced");
        let mut events = engine.subscribe();
        assert!(matches!(
            engine
                .execute(
                    "import-recovery".to_string(),
                    invocation,
                    vec!["base".to_string()]
                )
                .expect("exact retry is accepted"),
            crate::ExecuteDisposition::Existing(_)
        ));
        let event = tokio::time::timeout(std::time::Duration::from_secs(2), events.recv())
            .await
            .expect("recovery fence event arrives")
            .expect("recovery fence event exists");
        assert_eq!(event.phase, ExecutionPhase::Unknown);
        let audit = crate::ExecutionJournal::open(&journal_path).expect("audit journal opens");
        assert_eq!(
            audit
                .get("import-recovery")
                .expect("execution reads")
                .expect("execution exists")
                .status(),
            crate::JournalStatus::ReconciliationRequired
        );
        assert_eq!(
            audit
                .artifact_finalization("import-recovery")
                .expect("marker reads"),
            Some(crate::ArtifactFinalizationKind::Import)
        );
        assert_eq!(replica_writes.load(Ordering::SeqCst), 0);
        artifact_server.abort();
    }

    /// Changed local workflow configuration fences active persisted execution state.
    #[test]
    fn active_execution_does_not_reconcile_through_changed_workflow() {
        let state_dir = tempfile::tempdir().expect("state directory exists");
        let catalog = gated_catalog(
            "http://127.0.0.1:50051".to_string(),
            state_dir.path().to_path_buf(),
        );
        let journal_path = crate::journal_path(state_dir.path());
        let journal = crate::ExecutionJournal::open(&journal_path).expect("journal opens");
        let invocation = serde_json::json!({
            "capability_contract": "mobility.reach_region@v1",
            "parameters": {},
            "resource_ids": ["base"],
        });
        let spec = crate::ExecutionSpec::new(
            serde_json::to_vec(&invocation).expect("invocation serializes"),
            "obsolete-workflow-digest",
            vec!["base".to_string()],
        )
        .expect("execution spec is valid");
        journal
            .prepare_dispatch("running-old-config", &spec)
            .expect("dispatch is prepared");
        journal
            .record_local_handle("running-old-config", "local-1")
            .expect("handle records");
        journal
            .record_status(
                "running-old-config",
                1,
                crate::JournalStatus::Running,
                "moving",
            )
            .expect("running status records");
        drop(journal);

        let engine = crate::LocalIntegrationEngine::new(
            catalog,
            vec![Arc::new(GatedDriver {
                completed: Arc::new(AtomicBool::new(false)),
            }) as Arc<dyn LocalDriver>],
        )
        .expect("engine opens journal");
        assert!(matches!(
            engine.recover(),
            Err(crate::EngineError::ReconciliationRequired(_))
        ));
        let audit = crate::ExecutionJournal::open(&journal_path).expect("audit journal opens");
        assert_eq!(
            audit
                .get("running-old-config")
                .expect("record reads")
                .expect("record exists")
                .status(),
            crate::JournalStatus::ReconciliationRequired
        );
    }
}
