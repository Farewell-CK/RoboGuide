#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! Generic Node Connector process. A concrete Local EAIOS backend is injected later.

use integration::{ExecutionFact, LocalExecutionBackend, NodeConnector, Registration};

/// Deterministic backend used until a real Local EAIOS adapter is selected.
struct LocalBackend;
impl LocalExecutionBackend for LocalBackend {
    /// Reports lifecycle facts without performing physical work.
    fn execute(
        &self,
        _execution_id: &str,
        _command: &integration::ExecuteCommand,
    ) -> Vec<ExecutionFact> {
        vec![
            ExecutionFact::Accepted,
            ExecutionFact::Started,
            ExecutionFact::Completed,
        ]
    }
    /// Reports cancellation as a local fact.
    fn cancel(&self, _execution_id: &str) -> Vec<ExecutionFact> {
        vec![ExecutionFact::Cancelled]
    }
}

/// Connects to the configured server using a generic deterministic backend.
#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let address = std::env::args()
        .nth(1)
        .unwrap_or_else(|| "127.0.0.1:50051".to_string());
    let registration = Registration {
        node_id: "generic-node".to_string(),
        runtime: "local-eaios".to_string(),
        runtime_version: "0.1.0".to_string(),
        capabilities: Vec::new(),
        resources: Vec::new(),
        node_contract_version: "roboguide.node.v0.1".to_string(),
    };
    NodeConnector::new(address, registration, LocalBackend)
        .run()
        .await?;
    Ok(())
}
