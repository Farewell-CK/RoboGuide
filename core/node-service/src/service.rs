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
            let _ = self.run_session().await;
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
        CapabilityBindingConfig, ConnectionConfig, ExecutionStateMappingConfig, HealthCheckConfig,
        LocalOperationConfig, LocalSystemConfig, NodeServiceConfig, RequestMappingConfig,
        ResourceConfig, ValueExpressionConfig, WorkflowConfig, WorkflowStepConfig,
    };
    use std::collections::BTreeMap;
    use std::sync::Arc;
    use std::sync::atomic::{AtomicBool, Ordering};

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
        crate::CompiledLocalCatalog::compile(
            NodeServiceConfig {
                schema: crate::CONFIG_SCHEMA_V0_2.to_string(),
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
        let group_id = ExecutionGroupId::new("group-a").expect("group valid");
        control
            .create_group_with_actor_bindings(
                group_id.clone(),
                &committed,
                &requirement,
                now,
                &correlation,
                &mut log,
            )
            .expect("group bound");

        let mut bridge = IntegrationRuntimeBridge::new(control, state, log, router);
        let registered =
            tokio::time::timeout(std::time::Duration::from_secs(2), event_receiver.recv())
                .await
                .expect("registration arrives")
                .expect("registration exists");
        bridge
            .consume(registered, TimestampMs::new(1), &correlation)
            .expect("registration consumed");
        let intent = ExecutionIntent::new(
            contract,
            BTreeMap::from([(
                "region_id".to_string(),
                ExecutionValue::String("library".to_string()),
            )]),
        )
        .expect("intent valid");
        let command = bridge
            .execute_bound(
                "execution-e2e".to_string(),
                &group_id,
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
        let spec = crate::ExecutionSpec::new(
            serde_json::to_vec(&invocation).expect("invocation serializes"),
            "workflow-v1",
            vec!["base".to_string()],
        )
        .expect("execution spec is valid");
        assert!(matches!(
            journal
                .prepare_dispatch("ambiguous", &spec)
                .expect("dispatch is prepared"),
            crate::PrepareDispatch::Start(_)
        ));
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
        engine.recover().expect("config drift is fenced");
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
