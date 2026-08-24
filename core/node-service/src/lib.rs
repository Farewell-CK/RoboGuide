#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! Node-side RoboGuide service and Local EAIOS Adapter boundary.

mod adapter;
mod config;
mod service;

pub use adapter::{AdapterError, FakeAdapter, LocalEaiosAdapter};
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
            matches!(registered, GrpcNodeEvent::Registered(registration) if registration.node_id == "dog-a")
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
            while let Some(GrpcNodeEvent::NodeMessage(message)) = event_receiver.recv().await {
                if matches!(message.message, Some(NodePayload::ExecutionEvent(event)) if event.phase == ExecutionPhase::Completed as i32) { completed = true; break; }
            }
        }).await.expect("completion arrives");
        assert!(completed);
        node_task.abort();
        server.abort();
    }
}
