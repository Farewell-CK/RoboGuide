//! Mission and operator HTTP request routing.

use super::protocol::{parse_query, read_control_http_request, write_http_response};
use super::view::{
    inventory_json, memory_providers_json, relation_kind_name, state_providers_json,
    state_records_json,
};
use crate::*;
/// Serves the local Phase 1 Mission and operator diagnostics API.
pub(crate) async fn serve_http(
    address: std::net::SocketAddr,
    controller: Arc<Mutex<ControllerState>>,
    event_log: state::SqliteEventLog,
    event_write_gate: Arc<Mutex<()>>,
    clock: Arc<runtime::SystemMonotonicClock>,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let listener = tokio::net::TcpListener::bind(address).await?;
    loop {
        let (mut stream, _) = listener.accept().await?;
        let shared_controller = controller.clone();
        let log = event_log.clone();
        let write_gate = event_write_gate.clone();
        let shared_clock = clock.clone();
        tokio::spawn(async move {
            if let Err(error) = handle_http_connection(
                &mut stream,
                &shared_controller,
                &log,
                &write_gate,
                &shared_clock,
            )
            .await
            {
                let _ = tokio::io::AsyncWriteExt::shutdown(&mut stream).await;
                eprintln!("control HTTP request failed: {error}");
            }
        });
    }
}

/// Handles one bounded HTTP/1.1 request for Mission commands and diagnostics.
pub(crate) async fn handle_http_connection(
    stream: &mut tokio::net::TcpStream,
    controller: &Arc<Mutex<ControllerState>>,
    event_log: &state::SqliteEventLog,
    event_write_gate: &Arc<Mutex<()>>,
    clock: &runtime::SystemMonotonicClock,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let request = match tokio::time::timeout(
        CONTROL_HTTP_REQUEST_TIMEOUT,
        read_control_http_request(stream),
    )
    .await
    {
        Ok(Ok(request)) => request,
        Ok(Err(error)) => {
            return write_http_response(
                stream,
                "400 Bad Request",
                serde_json::json!({"error": error}),
            )
            .await;
        }
        Err(_) => {
            return write_http_response(
                stream,
                "408 Request Timeout",
                serde_json::json!({"error": "control HTTP request timed out"}),
            )
            .await;
        }
    };
    let request_body = match std::str::from_utf8(&request.body) {
        Ok(body) => body,
        Err(_) => {
            return write_http_response(
                stream,
                "400 Bad Request",
                serde_json::json!({"error": "control HTTP body is not UTF-8"}),
            )
            .await;
        }
    };
    let method = request.method.as_str();
    let target = request.target.as_str();
    let (path, query) = target.split_once('?').unwrap_or((target, ""));
    let (status, body) = match (method, path) {
        ("GET", "/healthz") => ("200 OK", serde_json::json!({"status": "ok"})),
        ("GET", "/v1/inventory") => {
            let controller = controller
                .lock()
                .map_err(|_| "controller lock is poisoned")?;
            (
                "200 OK",
                inventory_json(controller.bridge.state(), clock.now()),
            )
        }
        ("GET", "/v1/state/providers") => {
            let controller = controller
                .lock()
                .map_err(|_| "controller lock is poisoned")?;
            ("200 OK", state_providers_json(&controller))
        }
        ("GET", "/v1/memory/providers") => {
            let controller = controller
                .lock()
                .map_err(|_| "controller lock is poisoned")?;
            ("200 OK", memory_providers_json(&controller))
        }
        ("GET", "/v1/state/records") => {
            let controller = controller
                .lock()
                .map_err(|_| "controller lock is poisoned")?;
            (
                "200 OK",
                state_records_json(&controller, clock.now(), &parse_query(query)),
            )
        }
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
            let _write_guard = event_write_gate
                .lock()
                .map_err(|_| "event-log write gate is poisoned")?;
            event_log.begin_batch()?;
            let now = clock.now();
            let mut pending_controller = None;
            let result: Result<String, String> = {
                let controller = controller
                    .lock()
                    .map_err(|_| "controller lock is poisoned")?;
                let mut candidate = controller.clone();
                let mut events = event_log.clone();
                let operation = {
                    let ControllerState {
                        bridge,
                        orchestrator,
                    } = &mut candidate;
                    validate_actor_placement_coverage(bridge.control(), &plan).and_then(|_| {
                        let submit_correlation =
                            domain::CorrelationId::new(format!("submit-{mission_id}"))
                                .map_err(|error| error.to_string())?;
                        orchestrator
                            .submit(
                                plan.clone(),
                                group_id.clone(),
                                bridge.control_mut(),
                                now,
                                &submit_correlation,
                                &mut events,
                            )
                            .map_err(|error| error.to_string())?;
                        bridge
                            .register_execution_relations(
                                &plan,
                                &group_id,
                                now,
                                &submit_correlation,
                            )
                            .map_err(|error| error.to_string())
                    })
                };
                operation
                    .and_then(|_| {
                        let dispatch_correlation =
                            domain::CorrelationId::new(format!("dispatch-{mission_id}"))
                                .map_err(|error| error.to_string())?;
                        drive_ready_tasks(&mut candidate, now, &dispatch_correlation, &mut events)
                            .map_err(|error| error.to_string())
                    })
                    .and_then(|_| {
                        server_checkpoint_json(&candidate).map_err(|error| error.to_string())
                    })
                    .inspect(|_| pending_controller = Some(candidate))
            };
            match result {
                Ok(checkpoint_json) => {
                    if let Err(error) =
                        event_log.save_checkpoint(SERVER_CHECKPOINT_SCHEMA, &checkpoint_json)
                    {
                        let rollback = event_log.rollback_batch();
                        drop(_write_guard);
                        return write_http_response(
                            stream,
                            "503 Service Unavailable",
                            serde_json::json!({"error": format!("checkpoint persistence failed: {error}; rollback: {}", rollback.as_ref().err().map(ToString::to_string).unwrap_or_else(|| "ok".to_string()))}),
                        ).await;
                    }
                    if let Err(error) = event_log.commit_batch() {
                        let rollback = event_log.rollback_batch();
                        drop(_write_guard);
                        return write_http_response(
                            stream,
                            "503 Service Unavailable",
                            serde_json::json!({"error": format!("event batch commit failed: {error}; rollback: {}", rollback.as_ref().err().map(ToString::to_string).unwrap_or_else(|| "ok".to_string()))}),
                        ).await;
                    }
                    if let Some(candidate) = pending_controller {
                        let mut live = controller
                            .lock()
                            .map_err(|_| "controller lock is poisoned")?;
                        *live = candidate;
                        if let Err(error) = live.bridge.flush_command_outboxes() {
                            eprintln!("durable command outbox delivery deferred: {error}");
                        }
                    }
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
                    drop(_write_guard);
                    ("409 Conflict", serde_json::json!({"error": error}))
                }
            }
        }
        ("GET", path) if path.starts_with("/v1/missions/") => {
            let now = clock.now();
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
                    let relations = controller
                        .bridge
                        .relation_snapshots(execution.group_id())
                        .into_iter()
                        .map(|snapshot| {
                            let relation = snapshot.relation();
                            serde_json::json!({
                                "id": relation.relation_id().as_str(),
                                "kind": relation_kind_name(relation.kind()),
                                "source": {
                                    "task_id": relation.source_task_ref().task_id().as_str(),
                                    "role_id": relation.source_role_id().as_str(),
                                    "execution_id": snapshot.source_execution_id(),
                                },
                                "target": {
                                    "task_id": relation.target_task_ref().task_id().as_str(),
                                    "role_id": relation.target_role_id().as_str(),
                                    "execution_id": snapshot.target_execution_id(),
                                },
                                "state": format!("{:?}", snapshot.state()),
                                "reconciliation_required": snapshot.reconciliation_required(),
                            })
                        })
                        .collect::<Vec<_>>();
                    let peer_channels = controller
                        .bridge
                        .peer_channel_snapshots_at(execution.group_id(), now)
                        .into_iter()
                        .map(|channel| {
                            serde_json::json!({
                                "context_id": channel.context_id().as_str(),
                                "lifecycle": format!("{:?}", channel.lifecycle()),
                                "profile_id": channel.descriptor().profile_id,
                                "message_schema": channel.descriptor().message_schema,
                                "peers": channel.peers().iter().map(domain::ContextRoleId::as_str).collect::<Vec<_>>(),
                                "readiness": channel.readiness().iter().map(|evidence| serde_json::json!({
                                    "context_role_id": evidence.context_role_id.as_str(),
                                    "node_id": evidence.node_id.as_str(),
                                    "local_system_id": evidence.local_system_id.as_str(),
                                    "session_id": evidence.session_id,
                                    "channel_instance_id": evidence.channel_instance_id,
                                    "sequence": evidence.sequence,
                                    "received_at_ms": evidence.received_at.as_millis(),
                                    "expires_at_ms": evidence.expires_at.as_millis(),
                                    "ready": evidence.ready,
                                })).collect::<Vec<_>>(),
                            })
                        })
                        .collect::<Vec<_>>();
                    (
                        "200 OK",
                        serde_json::json!({
                            "mission_id": mission_id.as_str(),
                            "group_id": execution.group_id().as_str(),
                            "status": format!("{:?}", execution.lifecycle()),
                            "tasks": tasks,
                            "relations": relations,
                            "peer_channels": peer_channels
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
            let _write_guard = event_write_gate
                .lock()
                .map_err(|_| "event-log write gate is poisoned")?;
            event_log.begin_batch()?;
            let now = clock.now();
            let mut pending_controller = None;
            let mut cancellation_status = "Cancelling".to_string();
            let result: Result<String, String> = {
                let controller = controller
                    .lock()
                    .map_err(|_| "controller lock is poisoned")?;
                let mut candidate = controller.clone();
                let mut events = event_log.clone();
                let operation: Result<(), String> = (|| {
                    let ControllerState {
                        bridge,
                        orchestrator,
                    } = &mut candidate;
                    orchestrator
                        .request_cancel(
                            &mission_id,
                            bridge.control_mut(),
                            now,
                            &domain::CorrelationId::new(format!("cancel-{mission_id}"))
                                .map_err(|error| error.to_string())?,
                            &mut events,
                        )
                        .map_err(|error| error.to_string())?;
                    let group_id = orchestrator
                        .execution(&mission_id)
                        .ok_or_else(|| "Mission disappeared during cancellation".to_string())?
                        .group_id()
                        .clone();
                    bridge
                        .request_group_cancellation(&group_id)
                        .map_err(|error| error.to_string())?;
                    if bridge.group_attempts_terminal(&group_id) {
                        orchestrator
                            .finalize_cancel(
                                &mission_id,
                                bridge.control_mut(),
                                now,
                                &domain::CorrelationId::new(format!(
                                    "cancel-finalize-{mission_id}"
                                ))
                                .map_err(|error| error.to_string())?,
                                &mut events,
                            )
                            .map_err(|error| error.to_string())?;
                    }
                    cancellation_status = format!(
                        "{:?}",
                        orchestrator
                            .execution(&mission_id)
                            .expect("cancellation retained Mission authority")
                            .lifecycle()
                    );
                    Ok(())
                })();
                if operation.is_ok() {
                    close_terminal_mission_coordination(&mut candidate, &mission_id);
                }
                operation
                    .and_then(|()| {
                        server_checkpoint_json(&candidate).map_err(|error| error.to_string())
                    })
                    .inspect(|_| pending_controller = Some(candidate))
            };
            match result {
                Ok(checkpoint_json) => {
                    if let Err(error) =
                        event_log.save_checkpoint(SERVER_CHECKPOINT_SCHEMA, &checkpoint_json)
                    {
                        let rollback = event_log.rollback_batch();
                        drop(_write_guard);
                        return write_http_response(
                            stream,
                            "503 Service Unavailable",
                            serde_json::json!({"error": format!("checkpoint persistence failed: {error}; rollback: {}", rollback.as_ref().err().map(ToString::to_string).unwrap_or_else(|| "ok".to_string()))}),
                        ).await;
                    }
                    if let Err(error) = event_log.commit_batch() {
                        let rollback = event_log.rollback_batch();
                        drop(_write_guard);
                        return write_http_response(
                            stream,
                            "503 Service Unavailable",
                            serde_json::json!({"error": format!("event batch commit failed: {error}; rollback: {}", rollback.as_ref().err().map(ToString::to_string).unwrap_or_else(|| "ok".to_string()))}),
                        ).await;
                    }
                    if let Some(candidate) = pending_controller {
                        let mut live = controller
                            .lock()
                            .map_err(|_| "controller lock is poisoned")?;
                        *live = candidate;
                        if let Err(error) = live.bridge.flush_command_outboxes() {
                            eprintln!("durable command outbox delivery deferred: {error}");
                        }
                    }
                    (
                        "202 Accepted",
                        serde_json::json!({"status": cancellation_status}),
                    )
                }
                Err(error) => {
                    event_log.rollback_batch()?;
                    drop(_write_guard);
                    ("409 Conflict", serde_json::json!({"error": error}))
                }
            }
        }
        ("GET", "/v1/scheduling-reservations") => {
            let controller = controller
                .lock()
                .map_err(|_| "controller lock is poisoned")?;
            let reservations = controller
                .bridge
                .control()
                .scheduled_tasks()
                .map(|reservation| {
                    let decision = reservation.decision();
                    serde_json::json!({
                        "mission_id": decision.task_ref().mission_id(),
                        "task_id": decision.task_ref().task_id(),
                        "group_id": reservation.group_id(),
                        "phase": format!("{:?}", reservation.phase()),
                        "starts_at_ms": decision.starts_at().as_millis(),
                        "ends_at_ms": decision.ends_at().map(domain::TimestampMs::as_millis),
                        "latest_activation_at_ms": decision.latest_activation_at()
                            .map(domain::TimestampMs::as_millis),
                        "snapshot_version": decision.snapshot_version(),
                        "expansions": decision.expansions(),
                        "reason": reservation.reason(),
                        "selections": decision.selections().iter().map(|selection| {
                            serde_json::json!({
                                "role_id": selection.role_id(),
                                "node_id": selection.node_id(),
                                "resources": selection.resource_ids().iter()
                                    .zip(selection.resource_units())
                                    .map(|(resource_id, units)| serde_json::json!({
                                        "resource_id": resource_id,
                                        "units": units,
                                    }))
                                    .collect::<Vec<_>>(),
                            })
                        }).collect::<Vec<_>>(),
                    })
                })
                .collect::<Vec<_>>();
            (
                "200 OK",
                serde_json::json!({
                    "schema": "roboguide.scheduling-reservations/v0.1",
                    "reservations": reservations,
                }),
            )
        }
        ("GET", "/v1/execution-attempts") => {
            let controller = controller
                .lock()
                .map_err(|_| "controller lock is poisoned")?;
            let attempts = controller
                .bridge
                .attempt_history()
                .into_iter()
                .map(|attempt| {
                    let command = attempt.command();
                    serde_json::json!({
                        "execution_id": attempt.execution_id(),
                        "mission_id": command.mission_id(),
                        "task_id": command.task_ref().task_id(),
                        "group_id": command.group_id(),
                        "role_id": command.role_id(),
                        "node_id": command.node_id(),
                        "status": format!("{:?}", attempt.status()),
                    })
                })
                .collect::<Vec<_>>();
            (
                "200 OK",
                serde_json::json!({
                    "schema": "roboguide.execution-attempt-history/v0.1",
                    "attempts": attempts,
                }),
            )
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
                .trim_end_matches('/')
                .to_string();
            let _write_guard = event_write_gate
                .lock()
                .map_err(|_| "event-log write gate is poisoned")?;
            event_log.begin_batch()?;
            let result: Result<(ControllerState, String), String> = (|| {
                let live = controller
                    .lock()
                    .map_err(|_| "controller lock is poisoned")?;
                let mut candidate = live.clone();
                candidate
                    .bridge
                    .cancel(&execution_id)
                    .map_err(|error| error.to_string())?;
                let checkpoint =
                    server_checkpoint_json(&candidate).map_err(|error| error.to_string())?;
                Ok((candidate, checkpoint))
            })();
            match result {
                Ok((candidate, checkpoint)) => {
                    if let Err(error) =
                        event_log.save_checkpoint(SERVER_CHECKPOINT_SCHEMA, &checkpoint)
                    {
                        event_log.rollback_batch()?;
                        return Err(error.into());
                    }
                    if let Err(error) = event_log.commit_batch() {
                        let _ = event_log.rollback_batch();
                        return Err(error.into());
                    }
                    let mut live = controller
                        .lock()
                        .map_err(|_| "controller lock is poisoned")?;
                    *live = candidate;
                    if let Err(error) = live.bridge.flush_command_outboxes() {
                        eprintln!("durable command outbox delivery deferred: {error}");
                    }
                    (
                        "202 Accepted",
                        serde_json::json!({"status": "cancel_requested"}),
                    )
                }
                Err(error) => {
                    event_log.rollback_batch()?;
                    ("409 Conflict", serde_json::json!({"error": error}))
                }
            }
        }
        _ => ("404 Not Found", serde_json::json!({"error": "not found"})),
    };
    write_http_response(stream, status, body).await
}
