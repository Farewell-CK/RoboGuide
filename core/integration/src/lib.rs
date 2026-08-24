#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! Generic RoboGuide Integration Server and Node Connector protocol.
//!
//! The first wire transport is a newline-delimited, length-bounded JSON stream over
//! TCP. Its message envelope is deliberately equivalent to a gRPC bidirectional
//! stream: one ordered client stream carries node facts and one ordered server
//! stream carries control messages. The framing is isolated here so a tonic
//! service can replace it without changing session or execution invariants.

mod connector;
mod execution;
mod protocol;
mod server;
mod session;

pub use connector::{ConnectorError, LocalExecutionBackend, NodeConnector};
pub use execution::{ExecutionRegistry, ExecutionRegistryDecision, ExecutionStatus};
pub use protocol::{
    ClientFrame, ExecuteCommand, ExecutionFact, Hello, ProtocolError, Registration, ServerFrame,
    WireCapability, WireResource,
};
pub use server::{IntegrationServer, ServerError, ServerEvent};
pub use session::{SessionError, SessionState};

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::Map;
    use std::sync::Arc;
    use std::sync::atomic::{AtomicUsize, Ordering};

    struct FakeBackend;
    impl LocalExecutionBackend for FakeBackend {
        /// Emits accepted, started, and completed facts deterministically.
        fn execute(
            &self,
            _execution_id: &str,
            _command: &ExecuteCommand,
            events: tokio::sync::mpsc::UnboundedSender<ExecutionFact>,
        ) {
            let _ = events.send(ExecutionFact::Accepted);
            let _ = events.send(ExecutionFact::Started);
            let _ = events.send(ExecutionFact::Completed);
        }
        /// Emits a cancellation fact without claiming physical rollback.
        fn cancel(
            &self,
            _execution_id: &str,
            events: tokio::sync::mpsc::UnboundedSender<ExecutionFact>,
        ) {
            let _ = events.send(ExecutionFact::Cancelled);
        }
    }

    fn registration() -> Registration {
        Registration {
            node_id: "node-test".to_string(),
            runtime: "fake".to_string(),
            runtime_version: "0.1".to_string(),
            capabilities: vec![],
            resources: vec![],
            node_contract_version: "roboguide.node.v0.1".to_string(),
        }
    }

    /// Proves active connector negotiation and Execute-to-facts routing on one stream.
    #[tokio::test]
    async fn connector_round_trip_preserves_execution_identity() {
        let mut server = IntegrationServer::bind("127.0.0.1:0")
            .await
            .expect("server binds");
        let address = server.local_addr().expect("listener address");
        let connector = NodeConnector::new(address.to_string(), registration(), FakeBackend);
        let task = tokio::spawn(async move { connector.run().await.expect("connector runs") });
        let mut session = server.accept().await.expect("connector registers");
        let command = ExecuteCommand {
            mission_id: "m".to_string(),
            task_id: "t".to_string(),
            group_id: "g".to_string(),
            role_id: "r".to_string(),
            contract: "demo.run@v1".to_string(),
            parameters: Map::new(),
        };
        session
            .send_execute("execution-1", command)
            .await
            .expect("execute writes");
        let mut observed = false;
        for _ in 0..3 {
            let event = session
                .next_event()
                .await
                .expect("event reads")
                .expect("event exists");
            if matches!(event, ServerEvent::ExecutionFact { execution_id, .. } if execution_id == "execution-1")
            {
                observed = true;
                break;
            }
        }
        assert!(observed);
        drop(session);
        task.await.expect("connector task joins");
    }

    /// Repeated execution identities are idempotent while conflicting commands are rejected.
    #[test]
    fn execution_identity_is_idempotent() {
        let mut state = ExecutionRegistry::default();
        let command = ExecuteCommand {
            mission_id: "m".to_string(),
            task_id: "t".to_string(),
            group_id: "g".to_string(),
            role_id: "r".to_string(),
            contract: "demo.run@v1".to_string(),
            parameters: Map::new(),
        };
        assert_eq!(state.begin("e", &command), ExecutionRegistryDecision::Start);
        assert_eq!(
            state.begin("e", &command),
            ExecutionRegistryDecision::Existing(ExecutionStatus::Accepted)
        );
    }

    /// A long backend execution does not prevent Cancel from reaching the backend.
    #[tokio::test(flavor = "current_thread")]
    async fn long_execution_does_not_block_cancel() {
        struct BlockingBackend {
            cancellations: Arc<AtomicUsize>,
        }
        impl LocalExecutionBackend for BlockingBackend {
            /// Blocks after Started to model a long physical task.
            fn execute(
                &self,
                _id: &str,
                _command: &ExecuteCommand,
                events: tokio::sync::mpsc::UnboundedSender<ExecutionFact>,
            ) {
                let _ = events.send(ExecutionFact::Started);
                std::thread::sleep(std::time::Duration::from_millis(200));
            }
            /// Records prompt cancellation independently of execute.
            fn cancel(&self, _id: &str, events: tokio::sync::mpsc::UnboundedSender<ExecutionFact>) {
                self.cancellations.fetch_add(1, Ordering::SeqCst);
                let _ = events.send(ExecutionFact::Cancelled);
            }
        }
        let count = Arc::new(AtomicUsize::new(0));
        let mut server = IntegrationServer::bind("127.0.0.1:0")
            .await
            .expect("server binds");
        let connector = NodeConnector::new(
            server.local_addr().expect("address").to_string(),
            registration(),
            BlockingBackend {
                cancellations: Arc::clone(&count),
            },
        );
        let connector_task = tokio::spawn(async move { connector.run().await });
        let mut session = server.accept().await.expect("session accepted");
        let command = ExecuteCommand {
            mission_id: "m".to_string(),
            task_id: "t".to_string(),
            group_id: "g".to_string(),
            role_id: "r".to_string(),
            contract: "demo.run@v1".to_string(),
            parameters: Map::new(),
        };
        session
            .send_execute("long", command)
            .await
            .expect("execute sent");
        session.send_cancel("long").await.expect("cancel sent");
        tokio::time::timeout(std::time::Duration::from_millis(100), async {
            while count.load(Ordering::SeqCst) == 0 {
                tokio::task::yield_now().await;
            }
        })
        .await
        .expect("cancel must not wait for execute");
        drop(session);
        connector_task.abort();
    }

    /// Daemon-wide registry reports a running execution instead of starting it twice.
    #[test]
    fn reconnect_registry_preserves_running_execution() {
        let mut registry = ExecutionRegistry::default();
        let command = ExecuteCommand {
            mission_id: "m".to_string(),
            task_id: "t".to_string(),
            group_id: "g".to_string(),
            role_id: "r".to_string(),
            contract: "demo.run@v1".to_string(),
            parameters: Map::new(),
        };
        assert_eq!(
            registry.begin("physical-1", &command),
            ExecutionRegistryDecision::Start
        );
        assert_eq!(
            registry.record_fact("physical-1", &ExecutionFact::Started),
            Some(1)
        );
        assert_eq!(
            registry.begin("physical-1", &command),
            ExecutionRegistryDecision::Existing(ExecutionStatus::Running)
        );
    }

    /// Server accept loop negotiates multiple nodes without waiting for prior disconnects.
    #[tokio::test]
    async fn server_accepts_multiple_nodes_concurrently() {
        let server = IntegrationServer::bind("127.0.0.1:0")
            .await
            .expect("server binds");
        let address = server.local_addr().expect("address");
        let (events, mut receiver) = tokio::sync::mpsc::unbounded_channel();
        let server_task = tokio::spawn(server.serve(events));
        let connector_a = NodeConnector::new(
            address.to_string(),
            Registration {
                node_id: "dog-a".to_string(),
                ..registration()
            },
            FakeBackend,
        );
        let connector_b = NodeConnector::new(
            address.to_string(),
            Registration {
                node_id: "arm-c".to_string(),
                ..registration()
            },
            FakeBackend,
        );
        let task_a = tokio::spawn(async move { connector_a.run().await });
        let task_b = tokio::spawn(async move { connector_b.run().await });
        let mut nodes = std::collections::BTreeSet::new();
        tokio::time::timeout(std::time::Duration::from_secs(1), async {
            while nodes.len() < 2 {
                if let Some(ServerEvent::Registered { registration, .. }) = receiver.recv().await {
                    nodes.insert(registration.node_id);
                }
            }
        })
        .await
        .expect("both nodes register concurrently");
        assert_eq!(
            nodes,
            std::collections::BTreeSet::from(["arm-c".to_string(), "dog-a".to_string()])
        );
        task_a.abort();
        task_b.abort();
        server_task.abort();
    }
}
