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
mod protocol;
mod server;
mod session;

pub use connector::{ConnectorError, LocalExecutionBackend, NodeConnector};
pub use protocol::{
    ClientFrame, ExecuteCommand, ExecutionFact, Hello, ProtocolError, Registration, ServerFrame,
    WireCapability, WireResource,
};
pub use server::{IntegrationServer, ServerError, ServerEvent};
pub use session::{ExecutionDisposition, SessionError, SessionState};

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::Map;

    struct FakeBackend;
    impl LocalExecutionBackend for FakeBackend {
        /// Emits accepted, started, and completed facts deterministically.
        fn execute(&self, _execution_id: &str, _command: &ExecuteCommand) -> Vec<ExecutionFact> {
            vec![
                ExecutionFact::Accepted,
                ExecutionFact::Started,
                ExecutionFact::Completed,
            ]
        }
        /// Emits a cancellation fact without claiming physical rollback.
        fn cancel(&self, _execution_id: &str) -> Vec<ExecutionFact> {
            vec![ExecutionFact::Cancelled]
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
        let mut state = SessionState::new("s", "l");
        assert_eq!(state.accept_execution("e", "a"), ExecutionDisposition::New);
        assert_eq!(
            state.accept_execution("e", "a"),
            ExecutionDisposition::Duplicate
        );
        assert_eq!(
            state.accept_execution("e", "b"),
            ExecutionDisposition::Conflict
        );
    }
}
