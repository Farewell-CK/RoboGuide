#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! Explicit smoke probe for the formal RoboGuide Node Protocol v0.2.
//!
//! The program acts as a small protocol participant: it registers a synthetic node,
//! sends one heartbeat, and optionally simulates one server-issued Execute. It never
//! calls a Local EAIOS or performs a physical action.

use integration::grpc::v0_2::node_message::Message as NodePayload;
use integration::grpc::v0_2::robo_guide_node_protocol_client::RoboGuideNodeProtocolClient;
use integration::grpc::v0_2::server_message::Message as ServerPayload;
use integration::grpc::v0_2::{
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
    /// Whether to wait for and simulate one server-issued Execute command.
    simulate_execute: bool,
}

/// Parses the intentionally small smoke CLI and rejects implicit execution simulation.
fn parse_options() -> Result<SmokeOptions, String> {
    let mut endpoint = None;
    let mut node_id = "real-node-smoke".to_string();
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
            "--simulate-execute" => simulate_execute = true,
            _ => return Err(format!("unknown argument: {argument}")),
        }
    }
    Ok(SmokeOptions {
        endpoint: endpoint.ok_or_else(|| "--endpoint is required".to_string())?,
        node_id,
        simulate_execute,
    })
}

/// Runs one synthetic Node Protocol session without contacting a Local EAIOS.
async fn run(options: SmokeOptions) -> Result<(), String> {
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
                registration: Some(smoke_registration(&options.node_id)),
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
        simulate_one_execute(&mut inbound, &outbound, &registered.session_id).await?;
    }
    Ok(())
}

/// Builds a valid synthetic registration accepted by the v0.2 server validator.
fn smoke_registration(node_id: &str) -> NodeRegistration {
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
            contracts: vec!["compute.noop@v1".to_string()],
            local_system_id: "smoke-system".to_string(),
        }],
        sensors: vec![],
        resources: vec![Resource {
            id: "smoke-slot".to_string(),
            kind: "compute".to_string(),
            capacity: 1,
            metadata: Default::default(),
            local_system_id: "smoke-system".to_string(),
        }],
        metadata: Default::default(),
        node_contract_version: NODE_CONTRACT_VERSION.to_string(),
    }
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
    println!(
        "simulating execution={} capability={} resources={:?}",
        execute.execution_id, invocation.capability_contract, execute.resource_ids
    );
    for (sequence, phase) in [
        (2_u64, ExecutionPhase::Accepted),
        (3_u64, ExecutionPhase::Started),
        (4_u64, ExecutionPhase::Completed),
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
