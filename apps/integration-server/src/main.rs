#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! RoboGuide Integration Server process.

use integration::grpc::v0_2::robo_guide_node_protocol_server::RoboGuideNodeProtocolServer;
use integration::{
    CONTROLLER_CHECKPOINT_SCHEMA as INTEGRATION_CHECKPOINT_SCHEMA, GrpcIntegrationService,
    IntegrationRuntimeBridge, ObservedTaskResult,
};
use orchestration::{MissionOrchestrator, OrchestrationError, decode_mission_plan};
use ports::Clock;
use serde::{Deserialize, Serialize};
use std::sync::{Arc, Mutex};

/// Schema marker for the Phase 1 server checkpoint including Mission orchestration.
const SERVER_CHECKPOINT_SCHEMA: &str = "roboguide.controller-checkpoint/v4";

/// Live process state sharing one Control authority with Mission orchestration.
struct ControllerState {
    /// Integration, Runtime, Control, and horizontal State projections.
    bridge: IntegrationRuntimeBridge<state::SqliteEventLog>,
    /// Complete MissionPlan and explicit Mission lifecycle authority.
    orchestrator: MissionOrchestrator,
}

/// Durable Phase 1 process checkpoint saved in the same event-log transaction.
#[derive(Serialize, Deserialize)]
struct ServerCheckpoint {
    /// Exact wrapper schema marker.
    schema: String,
    /// Existing Integration/Control/State/Runtime checkpoint JSON.
    integration_json: String,
    /// Complete Mission orchestration checkpoint JSON.
    orchestration_json: String,
}

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
    let controller = match checkpoint {
        Some(checkpoint) => {
            if checkpoint.schema != SERVER_CHECKPOINT_SCHEMA {
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
            let saved: ServerCheckpoint = serde_json::from_str(&checkpoint.checkpoint_json)?;
            if saved.schema != SERVER_CHECKPOINT_SCHEMA {
                return Err(format!(
                    "controller checkpoint body uses unsupported schema {}",
                    saved.schema
                )
                .into());
            }
            ControllerState {
                bridge: IntegrationRuntimeBridge::restore_from_checkpoint(
                    &saved.integration_json,
                    event_log.clone(),
                    router,
                    domain::TimestampMs::new(0),
                )?,
                orchestrator: MissionOrchestrator::restore_json(&saved.orchestration_json)?,
            }
        }
        None if latest_sequence > 0 => {
            return Err(format!(
                "controller database {event_path} contains events but no controller checkpoint; refusing to start with empty authority"
            )
            .into());
        }
        None => ControllerState {
            bridge: IntegrationRuntimeBridge::new(
                control::ControlPlane::new(),
                state::InMemorySharedNodeState::new(),
                event_log.clone(),
                router,
            ),
            orchestrator: MissionOrchestrator::new(),
        },
    };
    let controller = Arc::new(Mutex::new(controller));
    let http_event_log = event_log.clone();
    let http_controller = controller.clone();
    let receiver_event_log = event_log.clone();
    let (fatal_sender, fatal_receiver) = tokio::sync::oneshot::channel::<String>();
    tokio::spawn(async move {
        if let Err(error) = serve_http(http_address, http_controller, http_event_log).await {
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
            match controller.lock() {
                Ok(mut controller) => {
                    let now = clock.now();
                    if let Err(error) = controller.bridge.consume(event, now, &correlation) {
                        eprintln!("integration fact rejected by Runtime/Control: {error}");
                    } else if let Err(error) = apply_runtime_outcomes(
                        &mut controller,
                        now,
                        &correlation,
                        &mut receiver_event_log.clone(),
                    ) {
                        drop(controller);
                        let _ = receiver_event_log.rollback_batch();
                        let _ = fatal_sender.send(format!(
                            "Mission orchestration failed after fact acceptance: {error}"
                        ));
                        return;
                    } else if let Err(error) = drive_ready_tasks(
                        &mut controller,
                        now,
                        &correlation,
                        &mut receiver_event_log.clone(),
                    ) {
                        drop(controller);
                        let _ = receiver_event_log.rollback_batch();
                        let _ = fatal_sender.send(format!(
                            "Mission Task dispatch failed after fact acceptance: {error}"
                        ));
                        return;
                    } else {
                        match server_checkpoint_json(&controller) {
                            Ok(checkpoint) => {
                                checkpoint_json = Some(checkpoint);
                                accepted = true;
                            }
                            Err(error) => {
                                drop(controller);
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
                && let Err(error) =
                    receiver_event_log.save_checkpoint(SERVER_CHECKPOINT_SCHEMA, &checkpoint_json)
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

/// Serializes Integration and Mission orchestration into one versioned durable wrapper.
fn server_checkpoint_json(
    controller: &ControllerState,
) -> Result<String, Box<dyn std::error::Error + Send + Sync>> {
    let integration_json = controller
        .bridge
        .checkpoint_json()
        .map_err(|error| format!("integration checkpoint failure: {error}"))?;
    let integration_value: serde_json::Value = serde_json::from_str(&integration_json)
        .map_err(|error| format!("integration checkpoint JSON failure: {error}"))?;
    if integration_value
        .get("schema")
        .and_then(serde_json::Value::as_str)
        != Some(INTEGRATION_CHECKPOINT_SCHEMA)
    {
        return Err("Integration checkpoint schema changed unexpectedly".into());
    }
    let orchestration_json = controller
        .orchestrator
        .checkpoint_json()
        .map_err(|error| format!("orchestration checkpoint failure: {error}"))?;
    serde_json::to_string(&ServerCheckpoint {
        schema: SERVER_CHECKPOINT_SCHEMA.to_string(),
        integration_json,
        orchestration_json,
    })
    .map_err(|error| format!("controller checkpoint wrapper failure: {error}").into())
}

/// Hands terminal Runtime facts to Mission orchestration without Runtime-owned completion.
fn apply_runtime_outcomes(
    controller: &mut ControllerState,
    timestamp: domain::TimestampMs,
    correlation_id: &domain::CorrelationId,
    events: &mut state::SqliteEventLog,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let outcomes = controller.bridge.terminal_task_outcomes();
    for outcome in outcomes {
        let mission_id = outcome.task_ref().mission_id().clone();
        if controller
            .orchestrator
            .execution(&mission_id)
            .is_some_and(|execution| {
                matches!(
                    execution.lifecycle(),
                    orchestration::MissionExecutionLifecycle::Completed
                        | orchestration::MissionExecutionLifecycle::Failed
                        | orchestration::MissionExecutionLifecycle::Cancelled
                )
            })
        {
            continue;
        }
        let ControllerState {
            bridge,
            orchestrator,
        } = controller;
        match outcome.result() {
            ObservedTaskResult::Succeeded => orchestrator.task_succeeded(
                &mission_id,
                outcome.task_ref(),
                bridge.control_mut(),
                timestamp,
                correlation_id,
                events,
            )?,
            ObservedTaskResult::Failed => orchestrator.task_failed(
                &mission_id,
                outcome.task_ref(),
                "Runtime observed a terminal role failure",
                bridge.control_mut(),
                timestamp,
                correlation_id,
                events,
            )?,
        }
    }
    Ok(())
}

/// Drives dependency-ready Tasks through Control binding and Runtime dispatch.
fn drive_ready_tasks(
    controller: &mut ControllerState,
    timestamp: domain::TimestampMs,
    correlation_id: &domain::CorrelationId,
    events: &mut state::SqliteEventLog,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    for mission_id in controller.orchestrator.mission_ids() {
        while let Some(task_ref) = controller
            .orchestrator
            .ready_tasks(&mission_id, controller.bridge.control())
            .into_iter()
            .next()
        {
            let state = controller.bridge.state().clone();
            let prepared = {
                let ControllerState {
                    bridge,
                    orchestrator,
                } = controller;
                match orchestrator.prepare_task(
                    &mission_id,
                    &task_ref,
                    &state,
                    bridge.control_mut(),
                    timestamp,
                    correlation_id,
                    events,
                ) {
                    Ok(prepared) => prepared,
                    Err(error) if deferred_dispatch(&error) => break,
                    Err(error) => return Err(error.into()),
                }
            };
            let execution = controller
                .orchestrator
                .execution(&mission_id)
                .ok_or_else(|| "Mission disappeared during Task dispatch".to_string())?;
            let group_id = execution.group_id().clone();
            let planned = execution
                .plan()
                .task_graph()
                .tasks()
                .iter()
                .find(|task| task.requirement().task_ref() == &task_ref)
                .ok_or_else(|| "Task disappeared from MissionPlan during dispatch".to_string())?;
            let intents = prepared
                .assignments()
                .iter()
                .map(|assignment| {
                    planned
                        .execution_intent(assignment.role_id())
                        .cloned()
                        .map(|intent| (assignment.role_id().clone(), intent))
                        .ok_or_else(|| {
                            format!("Task role {} has no ExecutionIntent", assignment.role_id())
                        })
                })
                .collect::<Result<Vec<_>, _>>()?;
            for (role_id, intent) in intents {
                let execution_id =
                    format!("execution-{mission_id}-{}-{role_id}", task_ref.task_id());
                controller.bridge.execute_task_bound(
                    execution_id,
                    &group_id,
                    &task_ref,
                    &role_id,
                    intent,
                    correlation_id.clone(),
                )?;
            }
        }
    }
    Ok(())
}

/// Identifies temporary scheduling failures that should leave a Task Ready for retry.
fn deferred_dispatch(error: &OrchestrationError) -> bool {
    matches!(
        error,
        OrchestrationError::Control(control::ControlError::NoCandidate(_))
    ) || matches!(
        error,
        OrchestrationError::Mission(reason)
            if reason.contains("no feasible deterministic selection")
    )
}

/// Serves the local Phase 1 Mission and operator diagnostics API.
async fn serve_http(
    address: std::net::SocketAddr,
    controller: Arc<Mutex<ControllerState>>,
    event_log: state::SqliteEventLog,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let listener = tokio::net::TcpListener::bind(address).await?;
    loop {
        let (mut stream, _) = listener.accept().await?;
        let shared_controller = controller.clone();
        let log = event_log.clone();
        tokio::spawn(async move {
            if let Err(error) = handle_http_connection(&mut stream, &shared_controller, &log).await
            {
                let _ = tokio::io::AsyncWriteExt::shutdown(&mut stream).await;
                eprintln!("control HTTP request failed: {error}");
            }
        });
    }
}

/// Handles one bounded HTTP/1.1 request for Mission commands and diagnostics.
async fn handle_http_connection(
    stream: &mut tokio::net::TcpStream,
    controller: &Arc<Mutex<ControllerState>>,
    event_log: &state::SqliteEventLog,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    use tokio::io::AsyncReadExt;
    let mut buffer = vec![0_u8; 16 * 1024];
    let length = stream.read(&mut buffer).await?;
    let request = std::str::from_utf8(&buffer[..length])?;
    let request_line = request.lines().next().unwrap_or_default();
    let request_body = request
        .split_once("\r\n\r\n")
        .map(|(_, body)| body)
        .unwrap_or_default();
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
        ("POST", "/v1/missions") => {
            let plan = match decode_mission_plan(request_body) {
                Ok(plan) => plan,
                Err(error) => {
                    return write_http_response(
                        stream,
                        "400 Bad Request",
                        serde_json::json!({"error": error.to_string()}),
                    )
                    .await;
                }
            };
            let mission_id = plan.goal().mission_id().clone();
            let group_id = domain::ExecutionGroupId::new(format!("group-{mission_id}"))?;
            event_log.begin_batch()?;
            let result: Result<String, String> = {
                let mut controller = controller
                    .lock()
                    .map_err(|_| "controller lock is poisoned")?;
                let mut events = event_log.clone();
                let operation = {
                    let ControllerState {
                        bridge,
                        orchestrator,
                    } = &mut *controller;
                    orchestrator
                        .submit(
                            plan,
                            group_id.clone(),
                            bridge.control_mut(),
                            domain::TimestampMs::new(0),
                            &domain::CorrelationId::new(format!("submit-{mission_id}"))?,
                            &mut events,
                        )
                        .map(|_| ())
                };
                operation
                    .map_err(|error| error.to_string())
                    .and_then(|_| {
                        let dispatch_correlation =
                            domain::CorrelationId::new(format!("dispatch-{mission_id}"))
                                .map_err(|error| error.to_string())?;
                        drive_ready_tasks(
                            &mut controller,
                            domain::TimestampMs::new(0),
                            &dispatch_correlation,
                            &mut events,
                        )
                        .map_err(|error| error.to_string())
                    })
                    .and_then(|_| {
                        server_checkpoint_json(&controller).map_err(|error| error.to_string())
                    })
            };
            match result {
                Ok(checkpoint_json) => {
                    event_log.save_checkpoint(SERVER_CHECKPOINT_SCHEMA, &checkpoint_json)?;
                    event_log.commit_batch()?;
                    (
                        "202 Accepted",
                        serde_json::json!({
                            "mission_id": mission_id.as_str(),
                            "group_id": group_id.as_str(),
                            "status": "Running"
                        }),
                    )
                }
                Err(error) => {
                    event_log.rollback_batch()?;
                    ("409 Conflict", serde_json::json!({"error": error}))
                }
            }
        }
        ("GET", path) if path.starts_with("/v1/missions/") => {
            let mission_text = path
                .trim_start_matches("/v1/missions/")
                .trim_end_matches('/');
            let mission_id = domain::MissionId::new(mission_text)?;
            let controller = controller
                .lock()
                .map_err(|_| "controller lock is poisoned")?;
            match controller.orchestrator.execution(&mission_id) {
                Some(execution) => {
                    let tasks = controller
                        .bridge
                        .control()
                        .group(execution.group_id())
                        .map(|group| {
                            group
                                .task_executions()
                                .map(|task| {
                                    serde_json::json!({
                                        "task_id": task.task_ref().task_id().as_str(),
                                        "context_id": task.context_id().as_str(),
                                        "status": format!("{:?}", task.lifecycle())
                                    })
                                })
                                .collect::<Vec<_>>()
                        })
                        .unwrap_or_default();
                    (
                        "200 OK",
                        serde_json::json!({
                            "mission_id": mission_id.as_str(),
                            "group_id": execution.group_id().as_str(),
                            "status": format!("{:?}", execution.lifecycle()),
                            "tasks": tasks
                        }),
                    )
                }
                None => (
                    "404 Not Found",
                    serde_json::json!({"error": "unknown Mission"}),
                ),
            }
        }
        ("POST", path) if path.starts_with("/v1/missions/") && path.ends_with("/cancel") => {
            let mission_text = path
                .trim_start_matches("/v1/missions/")
                .trim_end_matches("/cancel")
                .trim_end_matches('/');
            let mission_id = domain::MissionId::new(mission_text)?;
            event_log.begin_batch()?;
            let result: Result<String, String> = {
                let mut controller = controller
                    .lock()
                    .map_err(|_| "controller lock is poisoned")?;
                let mut events = event_log.clone();
                let operation = {
                    let ControllerState {
                        bridge,
                        orchestrator,
                    } = &mut *controller;
                    orchestrator.cancel(
                        &mission_id,
                        bridge.control_mut(),
                        domain::TimestampMs::new(0),
                        &domain::CorrelationId::new(format!("cancel-{mission_id}"))?,
                        &mut events,
                    )
                };
                operation.map_err(|error| error.to_string()).and_then(|()| {
                    server_checkpoint_json(&controller).map_err(|error| error.to_string())
                })
            };
            match result {
                Ok(checkpoint_json) => {
                    event_log.save_checkpoint(SERVER_CHECKPOINT_SCHEMA, &checkpoint_json)?;
                    event_log.commit_batch()?;
                    ("202 Accepted", serde_json::json!({"status": "Cancelled"}))
                }
                Err(error) => {
                    event_log.rollback_batch()?;
                    ("409 Conflict", serde_json::json!({"error": error}))
                }
            }
        }
        ("GET", path) if path.starts_with("/v1/executions/") => {
            let execution_id = path.trim_start_matches("/v1/executions/");
            let execution_id = execution_id.trim_end_matches("/");
            let controller = controller
                .lock()
                .map_err(|_| "controller lock is poisoned")?;
            match controller.bridge.execution_status(execution_id) {
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
            let controller = controller
                .lock()
                .map_err(|_| "controller lock is poisoned")?;
            match controller.bridge.cancel(execution_id) {
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
    write_http_response(stream, status, body).await
}

/// Writes one bounded JSON response and closes the HTTP/1.1 connection.
async fn write_http_response(
    stream: &mut tokio::net::TcpStream,
    status: &str,
    body: serde_json::Value,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    use tokio::io::AsyncWriteExt;
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
