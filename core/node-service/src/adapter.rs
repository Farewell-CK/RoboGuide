//! EAIOS-agnostic local adapter boundary owned by Node Service.

use integration::grpc::v0_1::{
    CanonicalInvocation, Capability, ExecutionEvent, ExecutionPhase, ExecutionSnapshot,
    LocalRuntime, NodeRegistration, NodeStatus,
};
use std::fmt::{Display, Formatter};
use tokio::sync::mpsc;

/// Adapter boundary for discovery, health, execution, cancellation, and reconciliation facts.
pub trait LocalEaiosAdapter: Send + Sync + 'static {
    /// Discovers current runtime, capability, sensor, resource, and metadata facts.
    fn discover(
        &self,
        node_id: &str,
        node_contract_version: &str,
    ) -> Result<NodeRegistration, AdapterError>;
    /// Reads current local health without granting business authority.
    fn status(&self) -> Result<NodeStatus, AdapterError>;
    /// Starts one canonical invocation and returns a progressive local fact stream.
    fn execute(
        &self,
        execution_id: &str,
        invocation: CanonicalInvocation,
    ) -> Result<mpsc::UnboundedReceiver<ExecutionEvent>, AdapterError>;
    /// Requests cancellation under local safety authority.
    fn cancel(
        &self,
        execution_id: &str,
    ) -> Result<mpsc::UnboundedReceiver<ExecutionEvent>, AdapterError>;
    /// Returns known execution snapshots for reconnect reconciliation.
    fn execution_snapshots(&self) -> Result<Vec<ExecutionSnapshot>, AdapterError>;
}

/// Deterministic reference adapter containing no vendor-specific semantics.
#[derive(Debug, Clone)]
pub struct FakeAdapter {
    /// Runtime name exposed by reference discovery.
    runtime_name: String,
    /// Runtime version exposed by reference discovery.
    runtime_version: String,
    /// Local metadata exposed without vendor semantics.
    metadata: std::collections::HashMap<String, String>,
}

impl FakeAdapter {
    /// Creates the generic reference adapter.
    pub fn new(
        runtime_name: String,
        runtime_version: String,
        metadata: std::collections::BTreeMap<String, String>,
    ) -> Self {
        Self {
            runtime_name,
            runtime_version,
            metadata: metadata.into_iter().collect(),
        }
    }
}

impl LocalEaiosAdapter for FakeAdapter {
    /// Returns deterministic discovery facts.
    fn discover(
        &self,
        node_id: &str,
        node_contract_version: &str,
    ) -> Result<NodeRegistration, AdapterError> {
        Ok(NodeRegistration {
            node_id: node_id.to_string(),
            runtime: Some(LocalRuntime {
                name: self.runtime_name.clone(),
                version: self.runtime_version.clone(),
            }),
            capabilities: vec![Capability {
                kind: "compute".to_string(),
                available: true,
                contracts: vec!["reference.noop@v1".to_string()],
            }],
            sensors: Vec::new(),
            resources: Vec::new(),
            metadata: self.metadata.clone(),
            node_contract_version: node_contract_version.to_string(),
        })
    }
    /// Reports deterministic online health.
    fn status(&self) -> Result<NodeStatus, AdapterError> {
        Ok(NodeStatus {
            health: "online".to_string(),
            detail: String::new(),
        })
    }
    /// Emits lifecycle facts for the reference invocation.
    fn execute(
        &self,
        execution_id: &str,
        _invocation: CanonicalInvocation,
    ) -> Result<mpsc::UnboundedReceiver<ExecutionEvent>, AdapterError> {
        let (sender, receiver) = mpsc::unbounded_channel();
        for (sequence, phase) in [
            ExecutionPhase::Accepted,
            ExecutionPhase::Started,
            ExecutionPhase::Completed,
        ]
        .into_iter()
        .enumerate()
        {
            let _ = sender.send(ExecutionEvent {
                session_id: String::new(),
                execution_id: execution_id.to_string(),
                sequence: sequence as u64 + 1,
                phase: phase as i32,
                reason: String::new(),
            });
        }
        Ok(receiver)
    }
    /// Accepts cancellation in the reference adapter.
    fn cancel(
        &self,
        execution_id: &str,
    ) -> Result<mpsc::UnboundedReceiver<ExecutionEvent>, AdapterError> {
        let (sender, receiver) = mpsc::unbounded_channel();
        let _ = sender.send(ExecutionEvent {
            session_id: String::new(),
            execution_id: execution_id.to_string(),
            sequence: 1,
            phase: ExecutionPhase::Cancelled as i32,
            reason: String::new(),
        });
        Ok(receiver)
    }
    /// Returns no durable work for the stateless reference adapter.
    fn execution_snapshots(&self) -> Result<Vec<ExecutionSnapshot>, AdapterError> {
        Ok(Vec::new())
    }
}

/// Local adapter failure that never exposes vendor transport types to Node Protocol.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AdapterError(pub String);
impl Display for AdapterError {
    /// Formats the local adapter diagnostic.
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(&self.0)
    }
}
impl std::error::Error for AdapterError {}
