#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! Explicit probe and execution smoke for any HTTP Node Contract v0.1 bridge.

use adapters::http::{HttpNodeGateway, decode_intent_fixture};
use domain::{
    CorrelationId, EventId, EventPayload, ExecutionCommand, ExecutionGroupId, MissionId, RoleId,
    TaskId, TimestampMs,
};
use ports::{EventSink, NodeGateway};
use runtime::{Runtime, SystemMonotonicClock};
use std::env;
use std::fs;
use std::time::Duration;

/// Parsed command-line settings that keep probing separate from physical execution.
struct SmokeOptions {
    /// Remote generic EAIOS bridge endpoint.
    endpoint: String,
    /// Whether a real invocation is explicitly authorized.
    execute: bool,
    /// Versioned intent fixture required only for execution.
    intent_path: Option<String>,
}

/// Prints runtime observations immediately for operator inspection.
#[derive(Default)]
struct ConsoleEventSink;

impl EventSink for ConsoleEventSink {
    /// Prints one immutable observation without retaining adapter credentials or payload secrets.
    fn append(
        &mut self,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        _causation_id: Option<&EventId>,
        payload: EventPayload,
    ) {
        println!(
            "event at {}ms correlation={} payload={payload:?}",
            timestamp.as_millis(),
            correlation_id
        );
    }
}

/// Parses the intentionally small smoke CLI and rejects implicit execution.
fn parse_options() -> Result<SmokeOptions, String> {
    let mut endpoint = None;
    let mut execute = false;
    let mut intent_path = None;
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
            "--execute" => execute = true,
            "--intent" => {
                intent_path = Some(
                    arguments
                        .next()
                        .ok_or_else(|| "--intent requires a file path".to_string())?,
                );
            }
            _ => return Err(format!("unknown argument: {argument}")),
        }
    }
    let endpoint = endpoint.ok_or_else(|| "--endpoint is required".to_string())?;
    if execute && intent_path.is_none() {
        return Err("--execute requires --intent <file>".to_string());
    }
    if !execute && intent_path.is_some() {
        return Err("--intent is accepted only with explicit --execute".to_string());
    }
    Ok(SmokeOptions {
        endpoint,
        execute,
        intent_path,
    })
}

/// Probes one generic bridge and optionally invokes an explicitly supplied canonical intent.
fn run(options: SmokeOptions) -> Result<(), String> {
    let gateway = HttpNodeGateway::connect(&options.endpoint, Duration::from_secs(10))
        .map_err(|error| error.to_string())?;
    let node_id = gateway.registration().node_id().clone();
    let registration = gateway.registration();
    println!(
        "registration node={} runtime={}@{} contract={} capabilities={} resources={}",
        node_id,
        registration.local_runtime().name(),
        registration.local_runtime().version(),
        registration.contract_version(),
        registration.capabilities().len(),
        registration.resources().len()
    );
    let status = gateway.status().map_err(|error| error.to_string())?;
    println!(
        "status node={} health={:?} source_observed_at={}ms",
        node_id,
        status.health(),
        status.observed_at().as_millis()
    );
    if !options.execute {
        println!("probe completed; no execution was requested");
        return Ok(());
    }

    let intent_path = options
        .intent_path
        .ok_or_else(|| "execution intent path is missing".to_string())?;
    let source = fs::read_to_string(&intent_path)
        .map_err(|error| format!("failed to read intent fixture {intent_path}: {error}"))?;
    let intent = decode_intent_fixture(&source).map_err(|error| error.to_string())?;
    let command = ExecutionCommand::new(
        MissionId::new("real-node-smoke").map_err(|error| error.to_string())?,
        TaskId::new("intent-probe").map_err(|error| error.to_string())?,
        ExecutionGroupId::new("real-node-smoke-group").map_err(|error| error.to_string())?,
        RoleId::new("smoke-operation").map_err(|error| error.to_string())?,
        node_id,
        intent,
        CorrelationId::new("real-node-smoke-trace").map_err(|error| error.to_string())?,
    );
    let clock = SystemMonotonicClock::new();
    let mut runtime = Runtime::new(clock, ConsoleEventSink);
    runtime
        .register_node(Box::new(gateway))
        .map_err(|error| error.to_string())?;
    let event = runtime
        .execute(&command)
        .map_err(|error| error.to_string())?;
    println!("execution returned: {event:?}");
    Ok(())
}

/// Runs probe-only by default and exits nonzero on contract or invocation failure.
fn main() {
    let result = parse_options().and_then(run);
    if let Err(error) = result {
        eprintln!("real node smoke failed: {error}");
        std::process::exit(1);
    }
}
