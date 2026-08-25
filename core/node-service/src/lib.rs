#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! Node-side RoboGuide service and Local EAIOS Adapter boundary.

mod adapter;
mod config;
mod service;

pub use adapter::{
    AdapterError, FakeAdapter, LocalEaiosAdapter, RobonixAdapter, RobonixClient,
    RobonixCommandClient,
};
pub use config::{AdapterConfig, NodeServiceConfig};
pub use service::{NodeService, NodeServiceError};

#[cfg(test)]
mod tests {
    use super::*;
    use integration::grpc::v0_1::node_message::Message as NodePayload;
    use integration::grpc::v0_1::robo_guide_node_protocol_server::RoboGuideNodeProtocolServer;
    use integration::grpc::v0_1::{CanonicalInvocation, ExecutionPhase};
    use integration::{GrpcIntegrationService, GrpcNodeEvent};
    use std::collections::BTreeMap;
    use std::sync::Arc;
    use std::sync::atomic::{AtomicBool, Ordering};

    /// Configuration keeps adapter selection and adapter-owned settings separate.
    #[test]
    fn config_loads_generic_adapter_settings() {
        let directory = tempfile::tempdir().expect("temporary directory exists");
        let path = directory.path().join("node.toml");
        std::fs::write(&path, "node_id = \"dog-a\"\nserver_endpoint = \"http://127.0.0.1:50051\"\n[adapter]\ntype = \"ros2\"\nnamespace = \"/dog\"\n").expect("fixture writes");
        let config = NodeServiceConfig::load(&path).expect("configuration parses");
        assert_eq!(config.node_id, "dog-a");
        assert_eq!(config.adapter.adapter_type, "ros2");
        assert_eq!(config.adapter.settings["namespace"].as_str(), Some("/dog"));
    }

    /// Formal gRPC lifecycle negotiates, registers, executes, and pushes events.
    #[tokio::test]
    async fn grpc_node_service_completes_formal_lifecycle() {
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
        let config = NodeServiceConfig {
            node_id: "dog-a".to_string(),
            server_endpoint: format!("http://{address}"),
            reconnect_delay_ms: 1,
            adapter: AdapterConfig {
                adapter_type: "fake".to_string(),
                settings: BTreeMap::new(),
            },
        };
        let node = NodeService::new(
            config,
            FakeAdapter::new("fake-eaios".to_string(), "0.1".to_string(), BTreeMap::new()),
        );
        let node_task = tokio::spawn(async move { node.run_session().await });
        let registered =
            tokio::time::timeout(std::time::Duration::from_secs(2), event_receiver.recv())
                .await
                .expect("registration arrives")
                .expect("event exists");
        assert!(
            matches!(registered, GrpcNodeEvent::Registered { registration, .. } if registration.node_id == "dog-a")
        );
        router
            .execute(
                "dog-a",
                "execution-1".to_string(),
                CanonicalInvocation {
                    mission_id: "m".to_string(),
                    task_id: "t".to_string(),
                    group_id: "g".to_string(),
                    role_id: "r".to_string(),
                    capability_contract: "reference.noop@v1".to_string(),
                    parameters: std::collections::HashMap::new(),
                },
            )
            .expect("execute routes");
        let mut completed = false;
        tokio::time::timeout(std::time::Duration::from_secs(2), async {
            while let Some(GrpcNodeEvent::NodeMessage { message, .. }) = event_receiver.recv().await {
                if matches!(message.message, Some(NodePayload::ExecutionEvent(event)) if event.phase == ExecutionPhase::Completed as i32) { completed = true; break; }
            }
        }).await.expect("completion arrives");
        assert!(completed);
        node_task.abort();
        server.abort();
    }

    struct TerminalGateRobonix {
        /// Test-controlled physical completion gate.
        terminal: Arc<AtomicBool>,
    }

    impl RobonixClient for TerminalGateRobonix {
        fn discover_contracts(&self) -> Result<Vec<String>, AdapterError> {
            Ok(vec![
                "robonix/system/scene/goal_room".to_string(),
                "robonix/service/navigation/navigate".to_string(),
            ])
        }
        fn status(&self) -> Result<integration::grpc::v0_1::NodeStatus, AdapterError> {
            Ok(integration::grpc::v0_1::NodeStatus {
                health: "online".to_string(),
                detail: String::new(),
            })
        }
        fn reach_region(&self, _region_id: &str) -> Result<String, AdapterError> {
            Ok("run-e2e".to_string())
        }
        fn navigation_status(&self, _run_id: &str) -> Result<(String, String), AdapterError> {
            if self.terminal.load(Ordering::SeqCst) {
                Ok(("SUCCEEDED".to_string(), String::new()))
            } else {
                Ok(("RUNNING".to_string(), String::new()))
            }
        }
        fn cancel_navigation(&self, _run_id: &str) -> Result<(), AdapterError> {
            Ok(())
        }
    }

    /// Bound Control assignment routes to Robonix and becomes terminal only after local completion.
    #[tokio::test]
    async fn bound_control_command_round_trips_through_robonix() {
        use control::{ControlPlane, DeterministicBootstrapScheduler};
        use domain::{
            ActorId, Capability, CapabilityContractRef, CapabilityKind, CorrelationId,
            ExecutionGroupId, ExecutionIntent, ExecutionValue, LocalRuntime, MissionId,
            NodeContractVersion, NodeHealth, NodeId, NodeRegistration, NodeStatus, RoleId,
            RoleRequirement, TaskId, TaskRequirement, TimestampMs,
        };
        use integration::{IntegrationRuntimeBridge, RemoteExecutionStatus};
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
        let config = NodeServiceConfig {
            node_id: "dog-a".to_string(),
            server_endpoint: format!("http://{address}"),
            reconnect_delay_ms: 1,
            adapter: AdapterConfig {
                adapter_type: "robonix".to_string(),
                settings: BTreeMap::new(),
            },
        };
        let node = NodeService::new(
            config,
            RobonixAdapter::new(TerminalGateRobonix {
                terminal: Arc::clone(&terminal),
            }),
        );
        let node_task = tokio::spawn(async move { node.run_session().await });

        let now = TimestampMs::new(0);
        let correlation = CorrelationId::new("real-loop-test").expect("correlation valid");
        let contract =
            CapabilityContractRef::new("mobility", "reach_region", "v1").expect("contract valid");
        let registration = NodeRegistration::new_with_contracts(
            NodeId::new("dog-a").expect("node valid"),
            LocalRuntime::new("robonix", "dev").expect("runtime valid"),
            NodeContractVersion::v0_1(),
            vec![Capability::new(CapabilityKind::Mobility, true)],
            vec![contract.clone()],
            Vec::new(),
        );
        let mut control = ControlPlane::new();
        let mut state = InMemorySharedNodeState::new();
        let mut log = InMemoryEventLog::new();
        control
            .register_node(
                &mut state,
                registration,
                NodeStatus::new(NodeHealth::Online, now),
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
                None,
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
                .expect("event exists");
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
                    .expect("event exists");
            bridge
                .consume(event, TimestampMs::new(2), &correlation)
                .expect("event consumed");
        }
        assert_ne!(
            bridge.execution_status("execution-e2e"),
            Some(RemoteExecutionStatus::Completed)
        );
        terminal.store(true, Ordering::SeqCst);
        while bridge.execution_status("execution-e2e") != Some(RemoteExecutionStatus::Completed) {
            let event =
                tokio::time::timeout(std::time::Duration::from_secs(2), event_receiver.recv())
                    .await
                    .expect("terminal event arrives")
                    .expect("event exists");
            bridge
                .consume(event, TimestampMs::new(3), &correlation)
                .expect("event consumed");
        }
        node_task.abort();
        server.abort();
    }
}
