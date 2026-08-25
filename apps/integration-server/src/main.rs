#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! RoboGuide Integration Server process.

use integration::grpc::v0_2::robo_guide_node_protocol_server::RoboGuideNodeProtocolServer;
use integration::{CONTROLLER_CHECKPOINT_SCHEMA, GrpcIntegrationService, IntegrationRuntimeBridge};
use ports::Clock;
use std::sync::{Arc, Mutex};

/// Binds the configured integration listener and keeps accepting connector sessions.
#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut arguments = std::env::args().skip(1);
    let address: std::net::SocketAddr = arguments
        .next()
        .unwrap_or_else(|| "127.0.0.1:50051".to_string())
        .parse()?;
    let event_path = arguments
        .next()
        .unwrap_or_else(|| "roboguide-controller.sqlite3".to_string());
    let http_address: std::net::SocketAddr = arguments
        .next()
        .unwrap_or_else(|| "127.0.0.1:8080".to_string())
        .parse()?;
    let event_log = state::SqliteEventLog::open(&event_path)?;
    let latest_sequence = event_log.latest_sequence()?;
    let checkpoint = event_log.load_checkpoint()?;
    let (events, mut receiver) = tokio::sync::mpsc::unbounded_channel();
    let (service, router) = GrpcIntegrationService::new(events);
    let runtime_bridge = match checkpoint {
        Some(checkpoint) => {
            if checkpoint.schema != CONTROLLER_CHECKPOINT_SCHEMA {
                return Err(format!(
                    "controller database {event_path} uses unsupported checkpoint schema {}",
                    checkpoint.schema
                )
                .into());
            }
            if checkpoint.event_sequence != latest_sequence {
                return Err(format!(
                    "controller database {event_path} checkpoint is at event {} but log ends at {latest_sequence}; refusing inconsistent recovery",
                    checkpoint.event_sequence
                )
                .into());
            }
            IntegrationRuntimeBridge::restore_from_checkpoint(
                &checkpoint.checkpoint_json,
                event_log.clone(),
                router,
                domain::TimestampMs::new(0),
            )?
        }
        None if latest_sequence > 0 => {
            return Err(format!(
                "controller database {event_path} contains events but no controller checkpoint; refusing to start with empty authority"
            )
            .into());
        }
        None => IntegrationRuntimeBridge::new(
            control::ControlPlane::new(),
            state::InMemorySharedNodeState::new(),
            event_log.clone(),
            router,
        ),
    };
    let bridge = Arc::new(Mutex::new(runtime_bridge));
    let http_event_log = event_log.clone();
    let http_bridge = bridge.clone();
    let receiver_event_log = event_log.clone();
    let (fatal_sender, fatal_receiver) = tokio::sync::oneshot::channel::<String>();
    tokio::spawn(async move {
        if let Err(error) = serve_http(http_address, http_bridge, http_event_log).await {
            eprintln!("control HTTP server stopped: {error}");
        }
    });
    tokio::spawn(async move {
        let correlation = domain::CorrelationId::new("integration-server")
            .expect("static correlation id is valid");
        let clock = runtime::SystemMonotonicClock::new();
        while let Some(event) = receiver.recv().await {
            if let Err(error) = receiver_event_log.begin_batch() {
                let _ = fatal_sender.send(format!("cannot begin durable event batch: {error}"));
                return;
            }
            let mut accepted = false;
            let mut checkpoint_json = None;
            match bridge.lock() {
                Ok(mut bridge) => {
                    if let Err(error) = bridge.consume(event, clock.now(), &correlation) {
                        eprintln!("integration fact rejected by Runtime/Control: {error}");
                    } else if let Err(error) =
                        bridge.advance_group_lifecycle(clock.now(), &correlation)
                    {
                        drop(bridge);
                        let _ = receiver_event_log.rollback_batch();
                        let _ = fatal_sender.send(format!(
                            "group lifecycle advancement failed after fact acceptance: {error}"
                        ));
                        return;
                    } else {
                        match bridge.checkpoint_json() {
                            Ok(checkpoint) => {
                                checkpoint_json = Some(checkpoint);
                                accepted = true;
                            }
                            Err(error) => {
                                drop(bridge);
                                let _ = receiver_event_log.rollback_batch();
                                let _ = fatal_sender.send(format!(
                                    "controller checkpoint serialization failed: {error}"
                                ));
                                return;
                            }
                        }
                    }
                }
                Err(_) => {
                    let _ = receiver_event_log.rollback_batch();
                    let _ = fatal_sender.send("integration bridge lock is poisoned".to_string());
                    return;
                }
            }
            match receiver_event_log.take_error() {
                Ok(Some(error)) => {
                    let _ = receiver_event_log.rollback_batch();
                    let _ = fatal_sender.send(format!("durable event sink failed: {error}"));
                    return;
                }
                Ok(None) => {}
                Err(error) => {
                    let _ = receiver_event_log.rollback_batch();
                    let _ = fatal_sender
                        .send(format!("durable event sink health is unavailable: {error}"));
                    return;
                }
            }
            if let Some(checkpoint_json) = checkpoint_json
                && let Err(error) = receiver_event_log
                    .save_checkpoint(CONTROLLER_CHECKPOINT_SCHEMA, &checkpoint_json)
            {
                let _ = receiver_event_log.rollback_batch();
                let _ = fatal_sender.send(format!(
                    "cannot persist controller checkpoint with accepted fact: {error}"
                ));
                return;
            }
            let batch_result = if accepted {
                receiver_event_log.commit_batch()
            } else {
                receiver_event_log.rollback_batch()
            };
            if let Err(error) = batch_result {
                let _ = fatal_sender.send(format!("cannot finalize durable event batch: {error}"));
                return;
            }
        }
    });
    let server = tonic::transport::Server::builder()
        .add_service(RoboGuideNodeProtocolServer::new(service))
        .serve(address);
    tokio::select! {
        result = server => result.map_err(Into::into),
        fatal = fatal_receiver => Err(fatal.unwrap_or_else(|_| "fact consumer stopped unexpectedly".to_string()).into()),
    }
}

/// Serves the local, read-only operator diagnostics API.
async fn serve_http(
    address: std::net::SocketAddr,
    bridge: Arc<Mutex<IntegrationRuntimeBridge<state::SqliteEventLog>>>,
    event_log: state::SqliteEventLog,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let listener = tokio::net::TcpListener::bind(address).await?;
    loop {
        let (mut stream, _) = listener.accept().await?;
        let runtime_bridge = bridge.clone();
        let log = event_log.clone();
        tokio::spawn(async move {
            if let Err(error) = handle_http_connection(&mut stream, &runtime_bridge, &log).await {
                let _ = tokio::io::AsyncWriteExt::shutdown(&mut stream).await;
                eprintln!("control HTTP request failed: {error}");
            }
        });
    }
}

/// Handles one bounded HTTP/1.1 request without mutating Control or State.
async fn handle_http_connection(
    stream: &mut tokio::net::TcpStream,
    bridge: &Arc<Mutex<IntegrationRuntimeBridge<state::SqliteEventLog>>>,
    event_log: &state::SqliteEventLog,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    use tokio::io::{AsyncReadExt, AsyncWriteExt};
    let mut buffer = vec![0_u8; 16 * 1024];
    let length = stream.read(&mut buffer).await?;
    let request = std::str::from_utf8(&buffer[..length])?;
    let request_line = request.lines().next().unwrap_or_default();
    let method = request_line.split_whitespace().next().unwrap_or("GET");
    let target = request_line.split_whitespace().nth(1).unwrap_or("/");
    let (path, query) = target.split_once('?').unwrap_or((target, ""));
    let (status, body) = match (method, path) {
        ("GET", "/healthz") => ("200 OK", serde_json::json!({"status": "ok"})),
        ("GET", "/v1/events") => {
            let query = parse_query(query);
            let after_sequence = query
                .get("after")
                .and_then(|value| value.parse::<u64>().ok());
            let limit = query
                .get("limit")
                .and_then(|value| value.parse::<usize>().ok())
                .unwrap_or(100);
            let events = event_log.events_page(after_sequence, limit)?;
            let records = events
                .iter()
                .map(|event| {
                    let payload = serde_json::from_str(&event.payload_json)
                        .unwrap_or_else(|_| serde_json::json!(event.payload_json));
                    serde_json::json!({
                        "sequence": event.sequence,
                        "event_id": event.event_id,
                        "timestamp_ms": event.timestamp_ms,
                        "correlation_id": event.correlation_id,
                        "causation_id": event.causation_id,
                        "payload_schema": event.payload_schema,
                        "payload": payload,
                    })
                })
                .collect::<Vec<_>>();
            ("200 OK", serde_json::json!({"events": records}))
        }
        ("GET", path) if path.starts_with("/v1/executions/") => {
            let execution_id = path.trim_start_matches("/v1/executions/");
            let execution_id = execution_id.trim_end_matches("/");
            let bridge = bridge
                .lock()
                .map_err(|_| "integration bridge lock is poisoned")?;
            match bridge.execution_status(execution_id) {
                Some(status) => (
                    "200 OK",
                    serde_json::json!({"execution_id": execution_id, "status": format!("{status:?}")}),
                ),
                None => (
                    "404 Not Found",
                    serde_json::json!({"error": "unknown execution"}),
                ),
            }
        }
        ("POST", path) if path.starts_with("/v1/executions/") && path.ends_with("/cancel") => {
            let execution_id = path
                .trim_start_matches("/v1/executions/")
                .trim_end_matches("/cancel")
                .trim_end_matches('/');
            let bridge = bridge
                .lock()
                .map_err(|_| "integration bridge lock is poisoned")?;
            match bridge.cancel(execution_id) {
                Ok(()) => (
                    "202 Accepted",
                    serde_json::json!({"status": "cancel_requested"}),
                ),
                Err(error) => (
                    "409 Conflict",
                    serde_json::json!({"error": error.to_string()}),
                ),
            }
        }
        _ => ("404 Not Found", serde_json::json!({"error": "not found"})),
    };
    let body = serde_json::to_string(&body)?;
    let response = format!(
        "HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
        body.len()
    );
    stream.write_all(response.as_bytes()).await?;
    stream.shutdown().await?;
    Ok(())
}

/// Parses simple URL query pairs used by the bounded event page API.
fn parse_query(query: &str) -> std::collections::BTreeMap<&str, &str> {
    query
        .split('&')
        .filter_map(|pair| pair.split_once('='))
        .collect()
}
