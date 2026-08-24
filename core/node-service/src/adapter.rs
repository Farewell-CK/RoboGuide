//! EAIOS-agnostic local adapter boundary owned by Node Service.

use integration::grpc::v0_1::{
    CanonicalInvocation, Capability, ExecutionEvent, ExecutionPhase, ExecutionSnapshot,
    LocalRuntime, NodeRegistration, NodeStatus,
};
use std::fmt::{Display, Formatter};
use std::path::PathBuf;
use std::process::Command;
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

/// Robonix-facing operations isolated behind a testable client boundary.
pub trait RobonixClient: Send + Sync + 'static {
    /// Discovers active Robonix capability contracts from Atlas.
    fn discover_contracts(&self) -> Result<Vec<String>, AdapterError>;
    /// Returns current Robonix runtime health.
    fn status(&self) -> Result<NodeStatus, AdapterError>;
    /// Resolves a semantic region and starts Robonix navigation, returning its run id.
    fn reach_region(&self, region_id: &str) -> Result<String, AdapterError>;
    /// Returns the current Robonix navigation state and detail.
    fn navigation_status(&self, run_id: &str) -> Result<(String, String), AdapterError>;
    /// Requests cancellation through Robonix navigation's cancel sub-contract.
    fn cancel_navigation(&self, run_id: &str) -> Result<(), AdapterError>;
}

/// Local process binding to the installed Robonix Python SDK bridge.
pub struct RobonixCommandClient {
    /// Configured Python executable, never supplied by a network invocation.
    python: PathBuf,
    /// Fixed local helper script using the public Robonix SDK.
    bridge_script: PathBuf,
    /// Local Atlas endpoint inherited only by the helper process.
    atlas_endpoint: String,
}

impl RobonixCommandClient {
    /// Creates a local Robonix SDK bridge from adapter-owned configuration.
    pub fn new(python: PathBuf, bridge_script: PathBuf, atlas_endpoint: String) -> Self {
        Self {
            python,
            bridge_script,
            atlas_endpoint,
        }
    }

    /// Calls the fixed helper with structured JSON input and output.
    fn call(
        &self,
        operation: &str,
        payload: serde_json::Value,
    ) -> Result<serde_json::Value, AdapterError> {
        let output = Command::new(&self.python)
            .arg(&self.bridge_script)
            .arg(operation)
            .arg(payload.to_string())
            .env("ROBONIX_ATLAS", &self.atlas_endpoint)
            .output()
            .map_err(|error| AdapterError(format!("failed to start Robonix bridge: {error}")))?;
        if !output.status.success() {
            return Err(AdapterError(
                String::from_utf8_lossy(&output.stderr).trim().to_string(),
            ));
        }
        serde_json::from_slice(&output.stdout)
            .map_err(|error| AdapterError(format!("invalid Robonix bridge response: {error}")))
    }
}

impl RobonixClient for RobonixCommandClient {
    /// Queries Atlas through the installed Robonix SDK.
    fn discover_contracts(&self) -> Result<Vec<String>, AdapterError> {
        serde_json::from_value(self.call("discover", serde_json::json!({}))?)
            .map_err(|error| AdapterError(error.to_string()))
    }
    /// Reports Atlas reachability as Robonix runtime health.
    fn status(&self) -> Result<NodeStatus, AdapterError> {
        let value = self.call("health", serde_json::json!({}))?;
        Ok(NodeStatus {
            health: value
                .get("health")
                .and_then(serde_json::Value::as_str)
                .unwrap_or("offline")
                .to_string(),
            detail: value
                .get("detail")
                .and_then(serde_json::Value::as_str)
                .unwrap_or_default()
                .to_string(),
        })
    }
    /// Resolves a Scene room/region and invokes Navigation through Atlas channels.
    fn reach_region(&self, region_id: &str) -> Result<String, AdapterError> {
        self.call(
            "reach_region",
            serde_json::json!({ "region_id": region_id }),
        )?
        .get("run_id")
        .and_then(serde_json::Value::as_str)
        .map(ToString::to_string)
        .ok_or_else(|| AdapterError("Robonix navigation returned no run_id".to_string()))
    }
    /// Queries the navigation status sub-contract.
    fn navigation_status(&self, run_id: &str) -> Result<(String, String), AdapterError> {
        let value = self.call("navigation_status", serde_json::json!({ "run_id": run_id }))?;
        Ok((
            value
                .get("state")
                .and_then(serde_json::Value::as_str)
                .unwrap_or("UNKNOWN")
                .to_string(),
            value
                .get("detail")
                .and_then(serde_json::Value::as_str)
                .unwrap_or_default()
                .to_string(),
        ))
    }
    /// Calls the navigation cancel sub-contract.
    fn cancel_navigation(&self, run_id: &str) -> Result<(), AdapterError> {
        self.call("cancel_navigation", serde_json::json!({ "run_id": run_id }))
            .map(|_| ())
    }
}

/// First real Local EAIOS Adapter mapping canonical mobility to Robonix capabilities.
pub struct RobonixAdapter<C> {
    /// Robonix Atlas/navigation client implementation local to the node.
    client: std::sync::Arc<C>,
    /// RoboGuide execution identities mapped to Robonix navigation run ids.
    runs: std::sync::Arc<std::sync::Mutex<std::collections::BTreeMap<String, String>>>,
}

impl<C: RobonixClient> RobonixAdapter<C> {
    /// Creates the adapter around an installed Robonix client.
    pub fn new(client: C) -> Self {
        Self {
            client: std::sync::Arc::new(client),
            runs: std::sync::Arc::new(std::sync::Mutex::new(std::collections::BTreeMap::new())),
        }
    }
}

impl<C: RobonixClient> LocalEaiosAdapter for RobonixAdapter<C> {
    /// Converts Atlas capability discovery into generic RoboGuide registration.
    fn discover(
        &self,
        node_id: &str,
        node_contract_version: &str,
    ) -> Result<NodeRegistration, AdapterError> {
        let contracts = self.client.discover_contracts()?;
        let mobility_available = contracts
            .iter()
            .any(|contract| contract == "robonix/system/scene/goal_room")
            && contracts
                .iter()
                .any(|contract| contract == "robonix/service/navigation/navigate");
        Ok(NodeRegistration {
            node_id: node_id.to_string(),
            runtime: Some(LocalRuntime {
                name: "robonix".to_string(),
                version: "dev".to_string(),
            }),
            capabilities: vec![Capability {
                kind: "mobility".to_string(),
                available: mobility_available,
                contracts: if mobility_available {
                    vec!["mobility.reach_region@v1".to_string()]
                } else {
                    Vec::new()
                },
            }],
            sensors: Vec::new(),
            resources: Vec::new(),
            metadata: std::collections::HashMap::new(),
            node_contract_version: node_contract_version.to_string(),
        })
    }

    /// Delegates health to the local Robonix client.
    fn status(&self) -> Result<NodeStatus, AdapterError> {
        self.client.status()
    }

    /// Expands reach_region into Robonix Scene goal resolution and Navigation execution.
    fn execute(
        &self,
        execution_id: &str,
        invocation: CanonicalInvocation,
    ) -> Result<mpsc::UnboundedReceiver<ExecutionEvent>, AdapterError> {
        if invocation.capability_contract != "mobility.reach_region@v1" {
            return Err(AdapterError(
                "unsupported canonical capability contract".to_string(),
            ));
        }
        let region_id = string_parameter(&invocation, "region_id")?;
        let run_id = self.client.reach_region(region_id)?;
        self.runs
            .lock()
            .map_err(|_| AdapterError("Robonix run registry unavailable".to_string()))?
            .insert(execution_id.to_string(), run_id.clone());
        let client = std::sync::Arc::clone(&self.client);
        let execution_id = execution_id.to_string();
        let (sender, receiver) = mpsc::unbounded_channel();
        std::thread::spawn(move || {
            let _ = sender.send(ExecutionEvent {
                session_id: String::new(),
                execution_id: execution_id.clone(),
                sequence: 1,
                phase: ExecutionPhase::Accepted as i32,
                reason: String::new(),
            });
            let _ = sender.send(ExecutionEvent {
                session_id: String::new(),
                execution_id: execution_id.clone(),
                sequence: 2,
                phase: ExecutionPhase::Started as i32,
                reason: String::new(),
            });
            let (phase, reason) = loop {
                match client.navigation_status(&run_id) {
                    Ok((state, detail)) if state == "SUCCEEDED" => {
                        break (ExecutionPhase::Completed, detail);
                    }
                    Ok((state, detail)) if state == "CANCELED" => {
                        break (ExecutionPhase::Cancelled, detail);
                    }
                    Ok((state, detail)) if matches!(state.as_str(), "FAILED" | "TIMEOUT") => {
                        break (ExecutionPhase::Failed, detail);
                    }
                    Ok(_) => std::thread::sleep(std::time::Duration::from_millis(200)),
                    Err(error) => break (ExecutionPhase::Failed, error.to_string()),
                }
            };
            let _ = sender.send(ExecutionEvent {
                session_id: String::new(),
                execution_id,
                sequence: 3,
                phase: phase as i32,
                reason,
            });
        });
        Ok(receiver)
    }

    /// Cancels the exact Robonix run associated with the RoboGuide execution id.
    fn cancel(
        &self,
        execution_id: &str,
    ) -> Result<mpsc::UnboundedReceiver<ExecutionEvent>, AdapterError> {
        let run_id = self
            .runs
            .lock()
            .map_err(|_| AdapterError("Robonix run registry unavailable".to_string()))?
            .get(execution_id)
            .cloned()
            .ok_or_else(|| AdapterError("unknown Robonix execution".to_string()))?;
        self.client.cancel_navigation(&run_id)?;
        let (sender, receiver) = mpsc::unbounded_channel();
        let _ = sender.send(ExecutionEvent {
            session_id: String::new(),
            execution_id: execution_id.to_string(),
            sequence: 4,
            phase: ExecutionPhase::Cancelled as i32,
            reason: String::new(),
        });
        Ok(receiver)
    }

    /// Returns Running snapshots for known Robonix run ids after reconnect.
    fn execution_snapshots(&self) -> Result<Vec<ExecutionSnapshot>, AdapterError> {
        let runs = self
            .runs
            .lock()
            .map_err(|_| AdapterError("Robonix run registry unavailable".to_string()))?;
        Ok(runs
            .keys()
            .map(|execution_id| ExecutionSnapshot {
                session_id: String::new(),
                execution_id: execution_id.clone(),
                last_sequence: 2,
                phase: ExecutionPhase::Started as i32,
                reason: String::new(),
            })
            .collect())
    }
}

/// Reads one required canonical string parameter without local Robonix naming.
fn string_parameter<'a>(
    invocation: &'a CanonicalInvocation,
    key: &str,
) -> Result<&'a str, AdapterError> {
    invocation
        .parameters
        .get(key)
        .and_then(|value| value.value.as_ref())
        .and_then(|value| match value {
            integration::grpc::v0_1::scalar_value::Value::StringValue(value) => {
                Some(value.as_str())
            }
            _ => None,
        })
        .ok_or_else(|| AdapterError(format!("canonical parameter {key} must be text")))
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

#[cfg(test)]
mod tests {
    use super::*;
    use integration::grpc::v0_1::{ScalarValue, scalar_value};
    use std::sync::Mutex;

    struct MockRobonix {
        cancelled: Mutex<Vec<String>>,
    }
    impl RobonixClient for MockRobonix {
        fn discover_contracts(&self) -> Result<Vec<String>, AdapterError> {
            Ok(vec![
                "robonix/system/scene/goal_room".to_string(),
                "robonix/service/navigation/navigate".to_string(),
            ])
        }
        fn status(&self) -> Result<NodeStatus, AdapterError> {
            Ok(NodeStatus {
                health: "online".to_string(),
                detail: String::new(),
            })
        }
        fn reach_region(&self, region_id: &str) -> Result<String, AdapterError> {
            assert_eq!(region_id, "library");
            Ok("run-1".to_string())
        }
        fn navigation_status(&self, run_id: &str) -> Result<(String, String), AdapterError> {
            assert_eq!(run_id, "run-1");
            Ok(("SUCCEEDED".to_string(), String::new()))
        }
        fn cancel_navigation(&self, run_id: &str) -> Result<(), AdapterError> {
            self.cancelled
                .lock()
                .expect("lock available")
                .push(run_id.to_string());
            Ok(())
        }
    }

    /// Robonix-specific discovery and navigation map to generic protocol facts.
    #[tokio::test]
    async fn robonix_adapter_maps_reach_region_and_cancel() {
        let adapter = RobonixAdapter::new(MockRobonix {
            cancelled: Mutex::new(Vec::new()),
        });
        let registration = adapter
            .discover("dog-a", "roboguide.node.v0.1")
            .expect("discovery succeeds");
        assert_eq!(
            registration.capabilities[0].contracts,
            vec!["mobility.reach_region@v1"]
        );
        let invocation = CanonicalInvocation {
            mission_id: "m".to_string(),
            task_id: "t".to_string(),
            group_id: "g".to_string(),
            role_id: "carrier".to_string(),
            capability_contract: "mobility.reach_region@v1".to_string(),
            parameters: std::collections::HashMap::from([(
                "region_id".to_string(),
                ScalarValue {
                    value: Some(scalar_value::Value::StringValue("library".to_string())),
                },
            )]),
        };
        let mut events = adapter
            .execute("execution-1", invocation)
            .expect("execution starts");
        let mut phases = Vec::new();
        while let Some(event) = events.recv().await {
            phases.push(event.phase);
        }
        assert_eq!(
            phases,
            vec![
                ExecutionPhase::Accepted as i32,
                ExecutionPhase::Started as i32,
                ExecutionPhase::Completed as i32
            ]
        );
        assert!(adapter.cancel("execution-1").is_ok());
    }
}
