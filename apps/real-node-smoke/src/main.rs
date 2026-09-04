#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! Explicit smoke probe for the formal RoboGuide Node Protocol v0.3.
//!
//! The program acts as a small protocol participant: it registers a synthetic node,
//! sends one heartbeat, and optionally simulates one server-issued Execute. It never
//! calls a Local EAIOS or performs a physical action.

use integration::grpc::v0_3::node_message::Message as NodePayload;
use integration::grpc::v0_3::robo_guide_node_protocol_client::RoboGuideNodeProtocolClient;
use integration::grpc::v0_3::server_message::Message as ServerPayload;
use integration::grpc::v0_3::{
    Capability, ExecutionEvent, ExecutionPhase, Heartbeat, Hello, LocalRuntime,
    LocalSystemDescriptor, NODE_CONTRACT_VERSION, NodeMessage, NodeRegistration, NodeStatus,
    PROTOCOL_VERSION, Register, Resource, ServerMessage,
};
use std::env;
use std::time::Duration;
use tokio::sync::mpsc;
use tokio_stream::wrappers::UnboundedReceiverStream;

/// Parsed settings for one formal Node Protocol smoke session.
struct SmokeOptions {
    /// Controller-side gRPC endpoint hosting the Node Protocol service.
    endpoint: String,
    /// Synthetic node identity used by the registration handshake.
    node_id: String,
    /// Controller HTTP endpoint used to submit the synthetic Mission in simulation mode.
    control_endpoint: Option<String>,
    /// Whether to wait for and simulate one server-issued Execute command.
    simulate_execute: bool,
}

/// Per-process identities that isolate one synthetic Mission from every other capable Node.
struct SmokeRunIdentity {
    /// Exact capability contract advertised only by this smoke session.
    capability_contract: String,
    /// Version component used in the Mission contract document.
    capability_version: String,
    /// Unique Mission identity submitted through the Controller API.
    mission_id: String,
}

/// Parses the intentionally small smoke CLI and rejects implicit execution simulation.
fn parse_options() -> Result<SmokeOptions, String> {
    let mut endpoint = None;
    let mut node_id = "real-node-smoke".to_string();
    let mut control_endpoint = None;
    let mut simulate_execute = false;
    let mut arguments = env::args().skip(1);
    while let Some(argument) = arguments.next() {
        match argument.as_str() {
            "--endpoint" => {
                endpoint = Some(
                    arguments
                        .next()
                        .ok_or_else(|| "--endpoint requires a value".to_string())?,
                );
            }
            "--node-id" => {
                node_id = arguments
                    .next()
                    .ok_or_else(|| "--node-id requires a value".to_string())?;
                if node_id.trim().is_empty() {
                    return Err("--node-id must not be blank".to_string());
                }
            }
            "--control-endpoint" => {
                control_endpoint = Some(
                    arguments
                        .next()
                        .ok_or_else(|| "--control-endpoint requires a value".to_string())?,
                );
            }
            "--simulate-execute" => simulate_execute = true,
            _ => return Err(format!("unknown argument: {argument}")),
        }
    }
    if simulate_execute && control_endpoint.is_none() {
        return Err("--simulate-execute requires --control-endpoint".to_string());
    }
    Ok(SmokeOptions {
        endpoint: endpoint.ok_or_else(|| "--endpoint is required".to_string())?,
        node_id,
        control_endpoint,
        simulate_execute,
    })
}

/// Runs one synthetic Node Protocol session without contacting a Local EAIOS.
async fn run(options: SmokeOptions) -> Result<(), String> {
    let identity = smoke_run_identity()?;
    let mut client = RoboGuideNodeProtocolClient::connect(options.endpoint.clone())
        .await
        .map_err(|error| format!("connect to Node Protocol endpoint: {error}"))?;
    let (outbound, receiver) = mpsc::unbounded_channel();
    outbound
        .send(NodeMessage {
            message: Some(NodePayload::Hello(Hello {
                node_id: options.node_id.clone(),
                protocol_versions: vec![PROTOCOL_VERSION.to_string()],
                node_contract_versions: vec![NODE_CONTRACT_VERSION.to_string()],
            })),
        })
        .map_err(|_| "Node Protocol outbound stream closed before Hello".to_string())?;
    let mut inbound = client
        .node_session(UnboundedReceiverStream::new(receiver))
        .await
        .map_err(|error| format!("open Node Protocol session: {error}"))?
        .into_inner();

    let welcome = next_server_payload(&mut inbound).await?;
    let ServerPayload::Welcome(welcome) = welcome else {
        return Err("expected Welcome during Node Protocol handshake".to_string());
    };
    if welcome.selected_protocol_version != PROTOCOL_VERSION
        || welcome.selected_node_contract_version != NODE_CONTRACT_VERSION
    {
        return Err("server selected an unsupported Node Protocol version".to_string());
    }
    outbound
        .send(NodeMessage {
            message: Some(NodePayload::Register(Register {
                registration: Some(smoke_registration(
                    &options.node_id,
                    &identity.capability_contract,
                )),
            })),
        })
        .map_err(|_| "Node Protocol outbound stream closed before Register".to_string())?;

    let registered = next_server_payload(&mut inbound).await?;
    let ServerPayload::Registered(registered) = registered else {
        return Err("expected Registered during Node Protocol handshake".to_string());
    };
    println!(
        "registered node={} session={} lease={} protocol={} contract={}",
        options.node_id,
        registered.session_id,
        registered.lease_id,
        welcome.selected_protocol_version,
        welcome.selected_node_contract_version,
    );

    outbound
        .send(NodeMessage {
            message: Some(NodePayload::Heartbeat(Heartbeat {
                session_id: registered.session_id.clone(),
                lease_id: registered.lease_id.clone(),
                sequence: 1,
                status: Some(NodeStatus {
                    health: "online".to_string(),
                    detail: "formal protocol smoke probe".to_string(),
                }),
            })),
        })
        .map_err(|_| "Node Protocol outbound stream closed before Heartbeat".to_string())?;
    await_ack(&mut inbound, 1).await?;
    println!("heartbeat acknowledged; no physical action was requested");

    if options.simulate_execute {
        submit_smoke_mission(
            options
                .control_endpoint
                .as_deref()
                .expect("simulation options require a Control endpoint"),
            &options.node_id,
            &identity,
        )
        .await?;
        simulate_one_execute(
            &mut inbound,
            &outbound,
            &registered.session_id,
            &options.node_id,
            &identity.capability_contract,
        )
        .await?;
    }
    Ok(())
}

/// Builds a valid synthetic registration accepted by the v0.3 server validator.
fn smoke_registration(node_id: &str, capability_contract: &str) -> NodeRegistration {
    NodeRegistration {
        node_id: node_id.to_string(),
        local_systems: vec![LocalSystemDescriptor {
            id: "smoke-system".to_string(),
            runtime: Some(LocalRuntime {
                name: "roboguide-protocol-smoke".to_string(),
                version: "0.1".to_string(),
            }),
            metadata: Default::default(),
        }],
        capabilities: vec![Capability {
            kind: "compute".to_string(),
            available: true,
            contracts: vec![capability_contract.to_string()],
            local_system_id: "smoke-system".to_string(),
        }],
        sensors: vec![],
        resources: vec![Resource {
            id: smoke_resource_id(node_id),
            kind: "compute".to_string(),
            capacity: 1,
            metadata: Default::default(),
            local_system_id: "smoke-system".to_string(),
        }],
        metadata: Default::default(),
        node_contract_version: NODE_CONTRACT_VERSION.to_string(),
        state_exports: Vec::new(),
        memory_providers: Vec::new(),
    }
}

/// Creates one unique contract and Mission identity without relying on the authored Node ID.
fn smoke_run_identity() -> Result<SmokeRunIdentity, String> {
    let nonce = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_err(|error| format!("system clock cannot create smoke identity: {error}"))?
        .as_nanos();
    let suffix = format!("{nonce}-{}", std::process::id());
    let capability_version = format!("v1-{suffix}");
    Ok(SmokeRunIdentity {
        capability_contract: format!("roboguide.smoke_probe@{capability_version}"),
        capability_version,
        mission_id: format!("mission-node-smoke-{suffix}"),
    })
}

/// Returns a globally unique synthetic resource identity for one logical smoke Node.
fn smoke_resource_id(node_id: &str) -> String {
    format!("smoke-slot:{node_id}")
}

/// Submits one single-role Mission so the real Controller path issues the simulated Execute.
async fn submit_smoke_mission(
    control_endpoint: &str,
    node_id: &str,
    identity: &SmokeRunIdentity,
) -> Result<(), String> {
    let plan = smoke_mission_plan(node_id, &identity.mission_id, &identity.capability_version);
    let endpoint = format!("{}/v1/missions", control_endpoint.trim_end_matches('/'));
    let client = reqwest::Client::builder()
        .timeout(Duration::from_secs(10))
        .build()
        .map_err(|error| format!("build smoke Mission HTTP client: {error}"))?;
    let response = client
        .post(&endpoint)
        .json(&plan)
        .send()
        .await
        .map_err(|error| format!("submit smoke Mission to {endpoint}: {error}"))?;
    let status = response.status();
    let body = response
        .text()
        .await
        .map_err(|error| format!("read smoke Mission response: {error}"))?;
    if !status.is_success() {
        return Err(format!(
            "Controller rejected smoke Mission with HTTP {status}: {body}"
        ));
    }
    println!("synthetic Mission accepted: {body}");
    Ok(())
}

/// Builds the complete single-role Mission used to trigger one formal Execute command.
fn smoke_mission_plan(
    node_id: &str,
    mission_id: &str,
    capability_version: &str,
) -> serde_json::Value {
    serde_json::json!({
        "schema_version": "roboguide.mission-plan/v0.3",
        "mission": {
            "id": mission_id,
            "objective": "Validate one synthetic formal Node Protocol execution"
        },
        "contexts": [{
            "id": "smoke-context",
            "roles": [{"id": "smoke-context-role", "actor": "smoke-actor"}],
            "relations": []
        }],
        "tasks": [{
            "id": "smoke-task",
            "description": "Complete one synthetic no-op execution",
            "context_id": "smoke-context",
            "depends_on": [],
            "roles": [{
                "id": "smoke-role",
                "actor": "smoke-actor",
                "capability": "compute",
                "contract": {
                    "namespace": "roboguide",
                    "name": "smoke_probe",
                    "version": capability_version
                },
                "resource_kind": "compute",
                "context_role": "smoke-context-role",
                "resource_scope": "task",
                "execution": {
                    "capability_contract": {
                        "namespace": "roboguide",
                        "name": "smoke_probe",
                        "version": capability_version
                    },
                    "parameters": {"probe": node_id}
                }
            }]
        }]
    })
}

/// Waits for the acknowledgement of one management sequence and rejects protocol errors.
async fn await_ack(
    inbound: &mut tonic::Streaming<ServerMessage>,
    sequence: u64,
) -> Result<(), String> {
    let message = tokio::time::timeout(Duration::from_secs(5), next_server_payload(inbound))
        .await
        .map_err(|_| format!("timed out waiting for Node Protocol Ack sequence {sequence}"))??;
    match message {
        ServerPayload::Ack(ack) if ack.sequence == sequence => Ok(()),
        ServerPayload::Ack(ack) => Err(format!(
            "Node Protocol Ack sequence mismatch: expected {sequence}, got {}",
            ack.sequence
        )),
        ServerPayload::Error(error) => Err(format!(
            "Node Protocol server error {}: {}",
            error.code, error.reason
        )),
        other => Err(format!(
            "expected Node Protocol Ack sequence {sequence}, got {other:?}"
        )),
    }
}

/// Simulates one accepted, started, and completed Execute without invoking hardware.
async fn simulate_one_execute(
    inbound: &mut tonic::Streaming<ServerMessage>,
    outbound: &mpsc::UnboundedSender<NodeMessage>,
    session_id: &str,
    node_id: &str,
    capability_contract: &str,
) -> Result<(), String> {
    let message = tokio::time::timeout(Duration::from_secs(10), next_server_payload(inbound))
        .await
        .map_err(|_| "timed out waiting for a server-issued Execute".to_string())??;
    let ServerPayload::Execute(execute) = message else {
        return Err(format!(
            "expected Execute in simulation mode, got {message:?}"
        ));
    };
    if execute.session_id != session_id {
        return Err("server Execute carried a stale session identity".to_string());
    }
    let invocation = execute
        .invocation
        .ok_or_else(|| "server Execute omitted its canonical invocation".to_string())?;
    if invocation.capability_contract != capability_contract
        || execute.resource_ids != vec![smoke_resource_id(node_id)]
    {
        return Err("server Execute does not match the synthetic smoke registration".to_string());
    }
    println!(
        "simulating execution={} capability={} resources={:?}",
        execute.execution_id, invocation.capability_contract, execute.resource_ids
    );
    for (sequence, phase) in [
        (1_u64, ExecutionPhase::Accepted),
        (2_u64, ExecutionPhase::Started),
        (3_u64, ExecutionPhase::Completed),
    ] {
        outbound
            .send(NodeMessage {
                message: Some(NodePayload::ExecutionEvent(ExecutionEvent {
                    session_id: session_id.to_string(),
                    execution_id: execute.execution_id.clone(),
                    sequence,
                    phase: phase as i32,
                    reason: "protocol smoke simulation; no physical action".to_string(),
                })),
            })
            .map_err(|_| "Node Protocol outbound stream closed during simulation".to_string())?;
        await_ack(inbound, sequence).await?;
    }
    println!("simulated execution completed; no physical action was performed");
    Ok(())
}

/// Reads one non-empty message from the server stream and reports closure explicitly.
async fn next_server_payload(
    inbound: &mut tonic::Streaming<ServerMessage>,
) -> Result<ServerPayload, String> {
    inbound
        .message()
        .await
        .map_err(|error| format!("Node Protocol stream status: {error}"))?
        .and_then(|message| message.message)
        .ok_or_else(|| "Node Protocol stream closed without a message".to_string())
}

/// Runs probe-only by default and exits nonzero on protocol failure.
#[tokio::main]
async fn main() {
    let result = match parse_options() {
        Ok(options) => run(options).await,
        Err(error) => Err(error),
    };
    if let Err(error) = result {
        eprintln!("real node smoke failed: {error}");
        std::process::exit(1);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Synthetic resource identities remain stable per Node and disjoint across Nodes.
    #[test]
    fn smoke_resources_are_node_scoped() {
        assert_eq!(smoke_resource_id("node-a"), "smoke-slot:node-a");
        assert_ne!(smoke_resource_id("node-a"), smoke_resource_id("node-b"));
    }

    /// The self-triggered Mission requests exactly the contract advertised by the smoke Node.
    #[test]
    fn smoke_mission_matches_synthetic_registration() {
        let contract = "roboguide.smoke_probe@v1-test";
        let registration = smoke_registration("node-a", contract);
        let plan = smoke_mission_plan("node-a", "mission-a", "v1-test");
        assert_eq!(plan["mission"]["id"], "mission-a");
        assert_eq!(
            plan["tasks"][0]["roles"][0]["execution"]["capability_contract"],
            serde_json::json!({
                "namespace": "roboguide",
                "name": "smoke_probe",
                "version": "v1-test"
            })
        );
        assert_eq!(registration.capabilities[0].contracts, [contract]);
        assert_eq!(
            plan["tasks"][0]["roles"][0]["execution"]["parameters"]["probe"],
            "node-a"
        );
    }
}
