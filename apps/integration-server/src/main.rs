#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! RoboGuide Integration Server process.

mod artifact_http;

use integration::grpc::v0_2::robo_guide_node_protocol_server::RoboGuideNodeProtocolServer as LegacyRoboGuideNodeProtocolServer;
use integration::grpc::v0_3::robo_guide_node_protocol_server::RoboGuideNodeProtocolServer;
use integration::{GrpcIntegrationService, GrpcLegacyV02Service, GrpcNodeEvent};
use orchestration::{
    CONTROLLER_CHECKPOINT_SCHEMA as INTEGRATION_CHECKPOINT_SCHEMA, IntegrationRuntimeBridge,
    ObservedTaskResult,
};
use orchestration::{MissionOrchestrator, OrchestrationError, decode_mission_plan};
use ports::{Clock, SharedNodeStateReader, StateRecordReader};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::Duration;

/// Schema marker for the Phase 1 server checkpoint including Mission orchestration.
///
/// The outer version advances with the inner Integration checkpoint so old
/// checkpoints are rejected instead of being decoded with a different shape.
const SERVER_CHECKPOINT_SCHEMA: &str = "roboguide.controller-checkpoint/v10";

/// Immediately previous wrapper accepted for one-step State checkpoint migration.
const PREVIOUS_SERVER_CHECKPOINT_SCHEMA: &str = "roboguide.controller-checkpoint/v9";

/// Version marker for the optional deployment-owned actor placement file.
const ACTOR_PLACEMENT_SCHEMA: &str = "roboguide.actor-placement/v0.1";

/// Maximum HTTP header block accepted by the Mission/operator API.
const MAX_CONTROL_HTTP_HEADER_BYTES: usize = 64 * 1024;

/// Maximum JSON body accepted by the Mission/operator API.
const MAX_CONTROL_HTTP_BODY_BYTES: usize = 1024 * 1024;

/// Maximum wall time allowed for one complete control HTTP request.
const CONTROL_HTTP_REQUEST_TIMEOUT: Duration = Duration::from_secs(30);

/// One fully framed request accepted by the bounded control HTTP listener.
#[derive(Debug)]
struct ControlHttpRequest {
    /// Uppercase HTTP method.
    method: String,
    /// Raw origin-form request target, including an optional query.
    target: String,
    /// Exactly the number of body bytes declared by Content-Length.
    body: Vec<u8>,
}

/// Live process state sharing one Control authority with Mission orchestration.
#[derive(Clone)]
struct ControllerState {
    /// Integration, Runtime, Control, and horizontal State projections.
    bridge: IntegrationRuntimeBridge<state::SqliteEventLog>,
    /// Complete MissionPlan and explicit Mission lifecycle authority.
    orchestrator: MissionOrchestrator,
}

/// Read-only admission adapter from Artifact HTTP into current Controller registration facts.
struct ControllerMemoryAdmission {
    /// Shared Controller composition whose State projection owns registration snapshots.
    controller: Arc<Mutex<ControllerState>>,
    /// Current gRPC routes used only to bind Memory writes to an active Node session.
    router: integration::GrpcNodeRouter,
}

impl artifact_http::MemoryProviderAdmission for ControllerMemoryAdmission {
    /// Requires a Node-owned manifest to match one exact provider in its registration snapshot.
    fn admit_manifest(&self, manifest: &domain::MemoryArtifactManifest) -> Result<(), String> {
        let domain::MemoryOwner::Node { node_id, .. } = manifest.owner() else {
            return Err(
                "RoboGuide-owned Memory requires a composition-owned publisher, not the node data-plane endpoint"
                    .to_string(),
            );
        };
        let controller = self
            .controller
            .lock()
            .map_err(|_| "Controller registration State is unavailable".to_string())?;
        let registration = controller
            .bridge
            .state()
            .node(node_id)
            .ok_or_else(|| format!("Memory owner node {node_id} is not registered"))?
            .registration();
        let provider = registration
            .memory_providers()
            .iter()
            .find(|provider| provider.provider_id() == manifest.provider_id())
            .ok_or_else(|| {
                format!(
                    "Memory provider {} is not declared by node {node_id}",
                    manifest.provider_id()
                )
            })?;
        provider
            .admit_manifest(manifest)
            .map_err(|error| error.to_string())
    }

    /// Requires replica evidence to name one compatible provider on the receiving node.
    fn admit_replica(
        &self,
        node_id: &domain::NodeId,
        consumer_provider_id: &str,
        manifest: &domain::MemoryArtifactManifest,
    ) -> Result<(), String> {
        let controller = self
            .controller
            .lock()
            .map_err(|_| "Controller registration State is unavailable".to_string())?;
        let registration = controller
            .bridge
            .state()
            .node(node_id)
            .ok_or_else(|| format!("Memory replica node {node_id} is not registered"))?
            .registration();
        let provider = registration
            .memory_providers()
            .iter()
            .find(|provider| provider.provider_id() == consumer_provider_id)
            .ok_or_else(|| {
                format!(
                    "Memory consumer provider {consumer_provider_id} is not declared by node {node_id}"
                )
            })?;
        provider
            .admit_import(manifest, node_id)
            .map_err(|error| error.to_string())
    }

    /// Requires the declared publisher to own the active, unexpired route for the expected Node.
    fn admit_publisher(
        &self,
        publisher: Option<&artifact_http::MemoryPublicationIdentity>,
        expected_node_id: &domain::NodeId,
    ) -> Result<(), String> {
        let publisher = publisher.ok_or_else(|| {
            "generic Memory mutation requires current Node/session identity".to_string()
        })?;
        if publisher.node_id() != expected_node_id {
            return Err(format!(
                "Memory publisher node {} does not match semantic owner {expected_node_id}",
                publisher.node_id()
            ));
        }
        self.router
            .session_is_current(expected_node_id.as_str(), publisher.session_id())
            .map_err(|error| error.to_string())?
            .then_some(())
            .ok_or_else(|| {
                format!("Memory publisher session is not current for node {expected_node_id}")
            })
    }
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

/// Explicit deployment policy for constraining logical actors to physical nodes.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ActorPlacementFile {
    /// Schema marker preventing accidental interpretation of another configuration format.
    schema: String,
    /// Mission-scoped placement entries applied to Control before requests are accepted.
    constraints: Vec<ActorPlacementEntry>,
}

/// One serialized mission actor placement entry.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ActorPlacementEntry {
    /// Mission namespace for the logical actor.
    mission_id: String,
    /// Logical actor declared by the MissionPlan.
    actor_id: String,
    /// Physical node permitted for first-use matching.
    node_id: String,
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
    let artifact_address: std::net::SocketAddr = arguments
        .next()
        .unwrap_or_else(|| "127.0.0.1:8090".to_string())
        .parse()?;
    let artifact_root = arguments
        .next()
        .unwrap_or_else(|| "roboguide-artifacts".to_string());
    let actor_placement_path = arguments.next().filter(|path| !path.trim().is_empty());
    if arguments.next().is_some() {
        return Err(
            "unexpected integration-server argument; expected optional actor placement JSON path"
                .into(),
        );
    }
    let actor_placement_constraints = actor_placement_path
        .as_deref()
        .map(|path| load_actor_placement_file(Path::new(path)))
        .transpose()?;
    let _event_log_writer_lock = acquire_event_log_writer_lock(Path::new(&event_path))?;
    let event_log = state::SqliteEventLog::open(&event_path)?;
    let event_write_gate = Arc::new(Mutex::new(()));
    let process_clock = Arc::new(runtime::SystemMonotonicClock::new());
    let artifact_store = artifact_store::FileSystemArtifactStore::new(&artifact_root)?;
    let artifact_catalog =
        artifact_http::ArtifactCatalog::replay_with_gate(&event_log, event_write_gate.clone())
            .map_err(|error| format!("spatial catalog startup replay failed: {error}"))?;
    let artifact_listener = tokio::net::TcpListener::bind(artifact_address).await?;
    let latest_sequence = event_log.latest_sequence()?;
    let checkpoint = event_log.load_checkpoint()?;
    let initialize_checkpoint = checkpoint.is_none() && latest_sequence == 0;
    let (events, mut receiver) = tokio::sync::mpsc::unbounded_channel();
    let (service, router) = GrpcIntegrationService::new(events);
    let receiver_router = router.clone();
    let memory_session_router = router.clone();
    let mut controller = match checkpoint {
        Some(checkpoint) => {
            if !matches!(
                checkpoint.schema.as_str(),
                SERVER_CHECKPOINT_SCHEMA | PREVIOUS_SERVER_CHECKPOINT_SCHEMA
            ) {
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
            if !matches!(
                saved.schema.as_str(),
                SERVER_CHECKPOINT_SCHEMA | PREVIOUS_SERVER_CHECKPOINT_SCHEMA
            ) {
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
                    process_clock.now(),
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
    if let Some(constraints) = actor_placement_constraints {
        for constraint in constraints {
            controller.bridge.control_mut().set_actor_node_constraint(
                constraint.mission_id().clone(),
                constraint.actor_id().clone(),
                constraint.node_id().clone(),
            )?;
        }
    }
    controller
        .orchestrator
        .validate_control_authority(controller.bridge.control())
        .map_err(|error| format!("restored Mission authority is inconsistent: {error}"))?;
    for mission_id in controller.orchestrator.mission_ids() {
        let execution = controller
            .orchestrator
            .execution(&mission_id)
            .expect("Mission identity came from orchestration authority");
        controller
            .bridge
            .validate_execution_relations(execution.plan(), execution.group_id())
            .map_err(|error| {
                format!("restored Mission relation authority is inconsistent: {error}")
            })?;
    }
    validate_restored_actor_placement_coverage(
        controller.bridge.control(),
        &controller.orchestrator,
    )?;
    if initialize_checkpoint || actor_placement_path.is_some() {
        let checkpoint_json =
            server_checkpoint_json(&controller).map_err(|error| error.to_string())?;
        event_log.begin_batch()?;
        if let Err(error) = event_log.save_checkpoint(SERVER_CHECKPOINT_SCHEMA, &checkpoint_json) {
            let _ = event_log.rollback_batch();
            return Err(error.into());
        }
        if let Err(error) = event_log.commit_batch() {
            let _ = event_log.rollback_batch();
            return Err(error.into());
        }
    }
    let controller = Arc::new(Mutex::new(controller));
    let http_event_log = event_log.clone();
    let http_controller = controller.clone();
    let http_event_write_gate = event_write_gate.clone();
    let http_clock = process_clock.clone();
    let receiver_event_log = event_log.clone();
    let receiver_event_write_gate = event_write_gate.clone();
    let artifact_catalog_for_server = artifact_catalog.clone();
    let artifact_store_for_server = artifact_store.clone();
    let memory_admission: Arc<dyn artifact_http::MemoryProviderAdmission> =
        Arc::new(ControllerMemoryAdmission {
            controller: Arc::clone(&controller),
            router: memory_session_router,
        });
    let (fatal_sender, fatal_receiver) = tokio::sync::oneshot::channel::<String>();
    tokio::spawn(async move {
        if let Err(error) = serve_http(
            http_address,
            http_controller,
            http_event_log,
            http_event_write_gate,
            http_clock,
        )
        .await
        {
            eprintln!("control HTTP server stopped: {error}");
        }
    });
    tokio::spawn(async move {
        if let Err(error) = artifact_http::serve_artifact_http(
            artifact_listener,
            artifact_store_for_server,
            artifact_catalog_for_server,
            memory_admission,
        )
        .await
        {
            eprintln!("artifact HTTP server stopped: {error}");
        }
    });
    tokio::spawn(async move {
        let correlation = domain::CorrelationId::new("integration-server")
            .expect("static correlation id is valid");
        while let Some(delivery) = receiver.recv().await {
            let (event, completion) = delivery.into_parts();
            if let GrpcNodeEvent::NodeMessage {
                node_id,
                session_id,
                ..
            } = &event
            {
                match receiver_router.session_is_current(node_id, session_id) {
                    Ok(true) => {}
                    Ok(false) => {
                        completion.reject("Node session is no longer current");
                        continue;
                    }
                    Err(error) => {
                        let reason = format!("cannot validate Node session: {error}");
                        completion.unavailable(reason.clone());
                        let _ = fatal_sender.send(reason);
                        return;
                    }
                }
            }
            let registration_fact = matches!(&event, GrpcNodeEvent::Registered { .. });
            let _write_guard = match receiver_event_write_gate.lock() {
                Ok(guard) => guard,
                Err(_) => {
                    let reason = "event-log write gate is poisoned".to_string();
                    completion.unavailable(reason.clone());
                    let _ = fatal_sender.send(reason);
                    return;
                }
            };
            if let Err(error) = receiver_event_log.begin_batch() {
                let reason = format!("cannot begin durable event batch: {error}");
                completion.unavailable(reason.clone());
                let _ = fatal_sender.send(reason);
                return;
            }
            let mut accepted = false;
            let mut checkpoint_json = None;
            let mut rejection = None;
            let mut pending_controller = None;
            match controller.lock() {
                Ok(controller) => {
                    // Evaluate the complete application transition on a private candidate. The
                    // live authority is replaced only after the durable batch commits below.
                    let mut candidate = controller.clone();
                    let now = process_clock.now();
                    if let Err(error) = candidate.bridge.consume(event, now, &correlation) {
                        eprintln!("integration fact rejected by Runtime/Control: {error}");
                        rejection = Some(error.to_string());
                    } else if let Err(error) = apply_runtime_events(
                        &mut candidate,
                        now,
                        &correlation,
                        &mut receiver_event_log.clone(),
                    ) {
                        drop(controller);
                        let _ = receiver_event_log.rollback_batch();
                        let reason = format!(
                            "Runtime lifecycle transition failed after fact acceptance: {error}"
                        );
                        completion.unavailable(reason.clone());
                        let _ = fatal_sender.send(reason);
                        return;
                    } else if let Err(error) = apply_runtime_outcomes(
                        &mut candidate,
                        now,
                        &correlation,
                        &mut receiver_event_log.clone(),
                    ) {
                        drop(controller);
                        let _ = receiver_event_log.rollback_batch();
                        let reason =
                            format!("Mission orchestration failed after fact acceptance: {error}");
                        completion.unavailable(reason.clone());
                        let _ = fatal_sender.send(reason);
                        return;
                    } else if !registration_fact
                        && let Err(error) = drive_ready_tasks(
                            &mut candidate,
                            now,
                            &correlation,
                            &mut receiver_event_log.clone(),
                        )
                    {
                        let _ = receiver_event_log.rollback_batch();
                        let reason =
                            format!("Mission Task dispatch failed after fact acceptance: {error}");
                        completion.unavailable(reason.clone());
                        let _ = fatal_sender.send(reason);
                        return;
                    } else {
                        match server_checkpoint_json(&candidate) {
                            Ok(checkpoint) => {
                                checkpoint_json = Some(checkpoint);
                                accepted = true;
                                pending_controller = Some(candidate);
                            }
                            Err(error) => {
                                let _ = receiver_event_log.rollback_batch();
                                let reason =
                                    format!("controller checkpoint serialization failed: {error}");
                                completion.unavailable(reason.clone());
                                let _ = fatal_sender.send(reason);
                                return;
                            }
                        }
                    }
                }
                Err(_) => {
                    let _ = receiver_event_log.rollback_batch();
                    let reason = "integration bridge lock is poisoned".to_string();
                    completion.unavailable(reason.clone());
                    let _ = fatal_sender.send(reason);
                    return;
                }
            }
            match receiver_event_log.take_error() {
                Ok(Some(error)) => {
                    let _ = receiver_event_log.rollback_batch();
                    let reason = format!("durable event sink failed: {error}");
                    completion.unavailable(reason.clone());
                    let _ = fatal_sender.send(reason);
                    return;
                }
                Ok(None) => {}
                Err(error) => {
                    let _ = receiver_event_log.rollback_batch();
                    let reason = format!("durable event sink health is unavailable: {error}");
                    completion.unavailable(reason.clone());
                    let _ = fatal_sender.send(reason);
                    return;
                }
            }
            if let Some(checkpoint_json) = checkpoint_json
                && let Err(error) =
                    receiver_event_log.save_checkpoint(SERVER_CHECKPOINT_SCHEMA, &checkpoint_json)
            {
                let _ = receiver_event_log.rollback_batch();
                let reason =
                    format!("cannot persist controller checkpoint with accepted fact: {error}");
                completion.unavailable(reason.clone());
                let _ = fatal_sender.send(reason);
                return;
            }
            let batch_result = if accepted {
                receiver_event_log.commit_batch()
            } else {
                receiver_event_log.rollback_batch()
            };
            if let Err(error) = batch_result {
                let reason = format!("cannot finalize durable event batch: {error}");
                completion.unavailable(reason.clone());
                let _ = fatal_sender.send(reason);
                return;
            }
            if accepted {
                if let Some(candidate) = pending_controller {
                    match controller.lock() {
                        Ok(mut live) => *live = candidate,
                        Err(_) => {
                            let reason = "integration bridge lock is poisoned after durable commit";
                            completion.unavailable(reason);
                            let _ = fatal_sender.send(reason.to_string());
                            return;
                        }
                    }
                }
                completion.accept();
            } else {
                completion.reject(
                    rejection.unwrap_or_else(|| {
                        "Controller rejected the Node Protocol fact".to_string()
                    }),
                );
            }
        }
    });
    let server = tonic::transport::Server::builder()
        .add_service(LegacyRoboGuideNodeProtocolServer::new(GrpcLegacyV02Service))
        .add_service(RoboGuideNodeProtocolServer::new(service))
        .serve(address);
    tokio::select! {
        result = server => result.map_err(Into::into),
        fatal = fatal_receiver => Err(fatal.unwrap_or_else(|_| "fact consumer stopped unexpectedly".to_string()).into()),
    }
}

/// Loads and validates deployment-owned actor placement constraints from JSON.
fn load_actor_placement_file(
    path: &Path,
) -> Result<Vec<control::ActorNodeConstraint>, Box<dyn std::error::Error>> {
    let content = std::fs::read_to_string(path)?;
    let file: ActorPlacementFile = serde_json::from_str(&content)?;
    if file.schema != ACTOR_PLACEMENT_SCHEMA {
        return Err(format!(
            "actor placement file {} uses unsupported schema {}",
            path.display(),
            file.schema
        )
        .into());
    }
    file.constraints
        .into_iter()
        .map(|entry| {
            Ok(control::ActorNodeConstraint::new(
                domain::MissionId::new(entry.mission_id)?,
                domain::ActorId::new(entry.actor_id)?,
                domain::NodeId::new(entry.node_id)?,
            ))
        })
        .collect()
}

/// Requires a configured deployment policy to cover exactly the submitted Mission actors.
///
/// An empty Control placement set preserves generic matching. Once a placement file has installed
/// any constraints, strict coverage prevents a misspelled Mission or Actor from silently falling
/// back to deterministic unconstrained matching.
fn validate_actor_placement_coverage(
    control: &control::ControlPlane,
    plan: &domain::MissionPlan,
) -> Result<(), String> {
    let configured = control.actor_node_constraints().collect::<Vec<_>>();
    if configured.is_empty() {
        return Ok(());
    }
    let mission_id = plan.goal().mission_id();
    let expected = plan
        .task_graph()
        .tasks()
        .iter()
        .flat_map(|task| task.requirement().roles())
        .filter_map(domain::RoleRequirement::actor_id)
        .cloned()
        .collect::<BTreeSet<_>>();
    let declared = configured
        .into_iter()
        .filter(|constraint| constraint.mission_id() == mission_id)
        .map(|constraint| constraint.actor_id().clone())
        .collect::<BTreeSet<_>>();
    if declared == expected {
        return Ok(());
    }
    let missing = expected
        .difference(&declared)
        .map(ToString::to_string)
        .collect::<Vec<_>>();
    let unknown = declared
        .difference(&expected)
        .map(ToString::to_string)
        .collect::<Vec<_>>();
    Err(format!(
        "strict actor placement coverage failed for Mission {mission_id}; missing actors [{}], unknown actors [{}]",
        missing.join(", "),
        unknown.join(", ")
    ))
}

/// Revalidates every durable Mission after checkpoint recovery and placement replacement.
///
/// This runs before the server accepts traffic or persists a replacement placement policy, so a
/// typo or incomplete policy cannot silently change the Actor authority of an existing Mission.
fn validate_restored_actor_placement_coverage(
    control: &control::ControlPlane,
    orchestrator: &MissionOrchestrator,
) -> Result<(), String> {
    for mission_id in orchestrator.mission_ids() {
        let execution = orchestrator.execution(&mission_id).ok_or_else(|| {
            format!("restored Mission {mission_id} disappeared during placement validation")
        })?;
        validate_actor_placement_coverage(control, execution.plan())
            .map_err(|error| format!("restored Mission placement is invalid: {error}"))?;
    }
    Ok(())
}

/// Acquires the process-wide single-writer lease for one controller event database.
///
/// The returned file must remain alive for the server lifetime. A second server using the same
/// database fails before it can replay a stale projection or append a conflicting event sequence.
fn acquire_event_log_writer_lock(event_path: &Path) -> Result<std::fs::File, std::io::Error> {
    let lock_path = event_log_lock_path(event_path)?;
    let file = std::fs::OpenOptions::new()
        .create(true)
        .truncate(false)
        .read(true)
        .write(true)
        .open(&lock_path)?;
    file.try_lock().map_err(|error| {
        std::io::Error::other(format!(
            "controller database {} is already owned by another Integration Server: {error}",
            event_path.display()
        ))
    })?;
    Ok(file)
}

/// Returns a canonical sibling lock path so relative and symlink aliases share one lease.
fn event_log_lock_path(event_path: &Path) -> Result<PathBuf, std::io::Error> {
    let canonical_event_path = if event_path.exists() {
        event_path.canonicalize()?
    } else {
        let file_name = event_path.file_name().ok_or_else(|| {
            std::io::Error::new(
                std::io::ErrorKind::InvalidInput,
                "controller database path must name a file",
            )
        })?;
        let parent = event_path
            .parent()
            .filter(|parent| !parent.as_os_str().is_empty())
            .unwrap_or_else(|| Path::new("."));
        parent.canonicalize()?.join(file_name)
    };
    let mut lock_path = canonical_event_path.as_os_str().to_os_string();
    lock_path.push(".writer.lock");
    Ok(PathBuf::from(lock_path))
}

/// Applies Runtime-owned lifecycle transitions without giving Integration Control authority.
fn apply_runtime_events(
    controller: &mut ControllerState,
    timestamp: domain::TimestampMs,
    correlation_id: &domain::CorrelationId,
    events: &mut state::SqliteEventLog,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    for event in controller.bridge.take_runtime_events() {
        match event {
            runtime::ExecutionEvent::TaskActivated { group_id, task_ref } => {
                let should_activate = controller
                    .bridge
                    .control()
                    .group(&group_id)
                    .and_then(|group| group.task_execution(&task_ref))
                    .is_some_and(|task| task.lifecycle() == domain::TaskExecutionLifecycle::Ready);
                if should_activate {
                    controller.bridge.control_mut().activate_task_execution(
                        &group_id,
                        &task_ref,
                        timestamp,
                        correlation_id,
                        events,
                    )?;
                }
            }
            runtime::ExecutionEvent::RoleCompleted { .. }
            | runtime::ExecutionEvent::RoleFailed { .. }
            | runtime::ExecutionEvent::RecoveryRequired { .. }
            | runtime::ExecutionEvent::RelationRegistered { .. }
            | runtime::ExecutionEvent::RelationStateChanged { .. }
            | runtime::ExecutionEvent::RelationReconciliationRequired { .. } => {}
        }
    }
    Ok(())
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
    ) || matches!(
        error,
        OrchestrationError::Control(
            control::ControlError::ActorPlacementConstraintUnsatisfied { .. }
        )
    ) || matches!(
        error,
        OrchestrationError::Control(
            control::ControlError::ActorBindingRequiresReconciliation { .. }
        )
    )
}

/// Serves the local Phase 1 Mission and operator diagnostics API.
async fn serve_http(
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
async fn handle_http_connection(
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
                        *controller
                            .lock()
                            .map_err(|_| "controller lock is poisoned")? = candidate;
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
                                "kind": "requires-active",
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
                    (
                        "200 OK",
                        serde_json::json!({
                            "mission_id": mission_id.as_str(),
                            "group_id": execution.group_id().as_str(),
                            "status": format!("{:?}", execution.lifecycle()),
                            "tasks": tasks,
                            "relations": relations
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
                    orchestrator.cancel(
                        &mission_id,
                        bridge.control_mut(),
                        now,
                        &domain::CorrelationId::new(format!("cancel-{mission_id}"))?,
                        &mut events,
                    )
                };
                operation
                    .map_err(|error| error.to_string())
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
                        *controller
                            .lock()
                            .map_err(|_| "controller lock is poisoned")? = candidate;
                    }
                    ("202 Accepted", serde_json::json!({"status": "Cancelled"}))
                }
                Err(error) => {
                    event_log.rollback_batch()?;
                    drop(_write_guard);
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

/// Projects current Shared Node State for Mission Intelligence without adding decision authority.
fn inventory_json(
    state: &state::InMemorySharedNodeState,
    observed_at: domain::TimestampMs,
) -> serde_json::Value {
    let nodes = state
        .snapshots()
        .into_iter()
        .map(|snapshot| {
            let registration = snapshot.registration();
            serde_json::json!({
                "node_id": snapshot.node_id().as_str(),
                "reported_health": format!("{:?}", snapshot.reported_status().health()),
                "source_observed_at_ms": snapshot.reported_status().observed_at().as_millis(),
                "received_at_ms": snapshot.reported_status_received_at().as_millis(),
                "liveness": format!("{:?}", snapshot.liveness().liveness()),
                "liveness_observed_at_ms": snapshot.liveness().observed_at().as_millis(),
                "capabilities": registration.capabilities().iter().map(|capability| {
                    serde_json::json!({
                        "kind": format!("{:?}", capability.kind()).to_ascii_lowercase(),
                        "available": capability.is_available(),
                    })
                }).collect::<Vec<_>>(),
                "contracts": registration.supported_contracts().iter()
                    .filter(|contract| registration.contract_is_available(contract))
                    .map(ToString::to_string)
                    .collect::<Vec<_>>(),
                "resources": registration.resources().iter().map(|resource| {
                    serde_json::json!({
                        "resource_id": resource.id().as_str(),
                        "kind": format!("{:?}", resource.kind()).to_ascii_lowercase(),
                        "capacity": resource.capacity(),
                    })
                }).collect::<Vec<_>>(),
            })
        })
        .collect::<Vec<_>>();
    serde_json::json!({
        "schema_version": "roboguide.inventory/v0.1",
        "observed_at_ms": observed_at.as_millis(),
        "nodes": nodes,
    })
}

/// Describes built-in read adapters and selectively registered node providers.
fn state_providers_json(controller: &ControllerState) -> serde_json::Value {
    let built_in = [
        ("mission-orchestrator", "desired"),
        ("control-plane", "committed"),
        ("shared-node-state", "reported,observed"),
        ("runtime-orchestration", "derived"),
    ]
    .into_iter()
    .map(|(provider_id, semantics)| {
        serde_json::json!({
            "provider_id": provider_id,
            "owner": "roboguide",
            "semantics": semantics.split(',').collect::<Vec<_>>(),
            "writable_via_state_api": false,
        })
    })
    .collect::<Vec<_>>();
    let nodes = controller
        .bridge
        .state()
        .snapshots()
        .into_iter()
        .map(|snapshot| {
            let registration = snapshot.registration();
            serde_json::json!({
                "node_id": snapshot.node_id().as_str(),
                "state_exports": registration.state_exports(),
            })
        })
        .collect::<Vec<_>>();
    serde_json::json!({
        "schema": "roboguide.state-provider-catalog/v0.1",
        "built_in": built_in,
        "nodes": nodes,
        "belief_providers": [],
    })
}

/// Describes the generic catalog and selectively registered node Memory providers.
fn memory_providers_json(controller: &ControllerState) -> serde_json::Value {
    let nodes = controller
        .bridge
        .state()
        .snapshots()
        .into_iter()
        .map(|snapshot| {
            serde_json::json!({
                "node_id": snapshot.node_id().as_str(),
                "providers": snapshot.registration().memory_providers(),
            })
        })
        .collect::<Vec<_>>();
    serde_json::json!({
        "schema": "roboguide.memory-provider-catalog/v0.1",
        "built_in": [{
            "provider_id": "artifact-memory-catalog",
            "owner": "roboguide",
            "kinds": ["execution", "spatial", "semantic", "experience", "artifact"],
            "content_plane": "artifact-cas",
        }],
        "nodes": nodes,
    })
}

/// Builds a read-only federated State view over existing owners plus external records.
fn state_records_json(
    controller: &ControllerState,
    now: domain::TimestampMs,
    query: &std::collections::BTreeMap<&str, &str>,
) -> serde_json::Value {
    let mut records = Vec::new();
    for mission_id in controller.orchestrator.mission_ids() {
        let execution = controller
            .orchestrator
            .execution(&mission_id)
            .expect("Mission identity came from the same orchestrator");
        records.push(state_view_record(
            "roboguide",
            "mission",
            mission_id.as_str(),
            "desired",
            "roboguide:mission-orchestrator",
            "accepted-plan",
            serde_json::json!({
                "objective": execution.plan().goal().objective(),
                "task_ids": execution.plan().task_graph().tasks().iter()
                    .map(|task| task.task_id().as_str())
                    .collect::<Vec<_>>(),
                "schema": execution.plan().schema_version(),
            }),
            None,
        ));
        records.push(state_view_record(
            "roboguide",
            "mission",
            mission_id.as_str(),
            "derived",
            "roboguide:runtime-orchestration",
            "mission-lifecycle",
            serde_json::json!({"lifecycle": format!("{:?}", execution.lifecycle())}),
            None,
        ));
    }
    for group_id in controller.bridge.control().group_ids() {
        let Some(group) = controller.bridge.control().group(&group_id) else {
            continue;
        };
        records.push(state_view_record(
            "roboguide",
            "execution_group",
            group_id.as_str(),
            "committed",
            "roboguide:control-plane",
            "group-commitment",
            serde_json::json!({
                "mission_id": group.mission_id().as_str(),
                "lifecycle": format!("{:?}", group.lifecycle()),
                "assignments": group.assignments().iter().map(|assignment| serde_json::json!({
                    "role_id": assignment.role_id().as_str(),
                    "node_id": assignment.node_id().as_str(),
                    "resource_ids": assignment.resource_ids().iter()
                        .map(|resource| resource.as_str()).collect::<Vec<_>>(),
                })).chain(group.task_executions().flat_map(|task| task.assignments().iter().map(|assignment| serde_json::json!({
                    "task_id": task.task_ref().task_id().as_str(),
                    "role_id": assignment.role_id().as_str(),
                    "node_id": assignment.node_id().as_str(),
                    "resource_ids": assignment.resource_ids().iter()
                        .map(|resource| resource.as_str()).collect::<Vec<_>>(),
                })))).collect::<Vec<_>>(),
            }),
            None,
        ));
    }
    for snapshot in controller.bridge.state().snapshots() {
        records.push(state_view_record(
            "node",
            "node",
            snapshot.node_id().as_str(),
            "reported",
            &format!("node:{}/registration", snapshot.node_id()),
            "health",
            serde_json::json!({
                "health": format!("{:?}", snapshot.reported_status().health()).to_ascii_lowercase(),
                "source_observed_at_ms": snapshot.reported_status().observed_at().as_millis(),
                "received_at_ms": snapshot.reported_status_received_at().as_millis(),
            }),
            None,
        ));
        records.push(state_view_record(
            "node",
            "node",
            snapshot.node_id().as_str(),
            "observed",
            "roboguide:shared-node-state",
            "liveness",
            serde_json::json!({
                "liveness": format!("{:?}", snapshot.liveness().liveness()).to_ascii_lowercase(),
                "observed_at_ms": snapshot.liveness().observed_at().as_millis(),
            }),
            None,
        ));
    }
    for record in controller.bridge.state_records().records() {
        let key = record.key();
        records.push(state_view_record(
            &format!("{:?}", key.object().class()).to_ascii_lowercase(),
            key.object().object_type(),
            key.object().object_id(),
            &format!("{:?}", key.semantic()).to_ascii_lowercase(),
            &key.source().to_string(),
            key.channel_id(),
            serde_json::json!({
                "payload_schema": record.payload_schema(),
                "value": record.value(),
                "source_observed_at_ms": record.source_observed_at().map(domain::TimestampMs::as_millis),
                "received_at_ms": record.received_at().as_millis(),
                "valid_for_ms": record.valid_for_ms(),
                "confidence_millionths": record.confidence_millionths(),
                "source_epoch": record.source_epoch(),
                "sequence": record.sequence(),
            }),
            Some(record.is_stale_at(now)),
        ));
    }
    records.retain(|record| state_record_matches(record, query));
    serde_json::json!({
        "schema": "roboguide.state-query/v0.1",
        "observed_at_ms": now.as_millis(),
        "records": records,
    })
}

/// Creates one common read-facade envelope without moving authority into the API layer.
#[allow(clippy::too_many_arguments)]
fn state_view_record(
    object_class: &str,
    object_type: &str,
    object_id: &str,
    semantic: &str,
    source: &str,
    channel_id: &str,
    value: serde_json::Value,
    stale: Option<bool>,
) -> serde_json::Value {
    serde_json::json!({
        "object": {
            "class": object_class,
            "object_type": object_type,
            "object_id": object_id,
        },
        "semantic": semantic,
        "source": source,
        "channel_id": channel_id,
        "value": value,
        "stale": stale,
    })
}

/// Applies exact simple query filters while retaining stale records unless explicitly excluded.
fn state_record_matches(
    record: &serde_json::Value,
    query: &std::collections::BTreeMap<&str, &str>,
) -> bool {
    let object = &record["object"];
    let exact = [
        ("object_class", object["class"].as_str()),
        ("object_type", object["object_type"].as_str()),
        ("object_id", object["object_id"].as_str()),
        ("semantic", record["semantic"].as_str()),
        ("source", record["source"].as_str()),
        ("channel_id", record["channel_id"].as_str()),
    ];
    if exact.iter().any(|(name, actual)| {
        query
            .get(name)
            .is_some_and(|expected| Some(*expected) != *actual)
    }) {
        return false;
    }
    query
        .get("include_stale")
        .is_none_or(|include| *include != "false" || record["stale"].as_bool() != Some(true))
}

/// Reads one HTTP/1.1 request using explicit header and Content-Length boundaries.
async fn read_control_http_request(
    stream: &mut tokio::net::TcpStream,
) -> Result<ControlHttpRequest, String> {
    use tokio::io::AsyncReadExt;
    let mut bytes = Vec::new();
    let mut chunk = [0_u8; 4096];
    let header_end = loop {
        let count = stream
            .read(&mut chunk)
            .await
            .map_err(|error| format!("read control HTTP request: {error}"))?;
        if count == 0 {
            return Err("control HTTP request ended before headers".to_string());
        }
        bytes.extend_from_slice(&chunk[..count]);
        if let Some(index) = bytes.windows(4).position(|window| window == b"\r\n\r\n") {
            let end = index + 4;
            if end > MAX_CONTROL_HTTP_HEADER_BYTES {
                return Err("control HTTP headers exceed limit".to_string());
            }
            break end;
        }
        if bytes.len() > MAX_CONTROL_HTTP_HEADER_BYTES {
            return Err("control HTTP headers exceed limit".to_string());
        }
    };
    let header_text = std::str::from_utf8(&bytes[..header_end])
        .map_err(|_| "control HTTP headers are not UTF-8".to_string())?;
    let mut lines = header_text.split("\r\n");
    let request_line = lines
        .next()
        .ok_or_else(|| "control HTTP request line is missing".to_string())?;
    let mut fields = request_line.split_whitespace();
    let method = fields
        .next()
        .ok_or_else(|| "control HTTP method is missing".to_string())?
        .to_ascii_uppercase();
    let target = fields
        .next()
        .ok_or_else(|| "control HTTP target is missing".to_string())?
        .to_string();
    let version = fields
        .next()
        .ok_or_else(|| "control HTTP version is missing".to_string())?;
    if fields.next().is_some() || !matches!(version, "HTTP/1.0" | "HTTP/1.1") {
        return Err("control HTTP request line is invalid".to_string());
    }
    let mut content_length = None;
    for line in lines.filter(|line| !line.is_empty()) {
        let (name, value) = line
            .split_once(':')
            .ok_or_else(|| "control HTTP header is malformed".to_string())?;
        if name.eq_ignore_ascii_case("transfer-encoding") {
            return Err("Transfer-Encoding is unsupported".to_string());
        }
        if name.eq_ignore_ascii_case("content-length") {
            if content_length.is_some() {
                return Err("duplicate Content-Length header".to_string());
            }
            content_length = Some(
                value
                    .trim()
                    .parse::<usize>()
                    .map_err(|_| "Content-Length must be an integer".to_string())?,
            );
        }
    }
    let content_length = match content_length {
        Some(length) => length,
        None if matches!(method.as_str(), "GET" | "HEAD" | "DELETE") => 0,
        None => return Err("Content-Length is required".to_string()),
    };
    if content_length > MAX_CONTROL_HTTP_BODY_BYTES {
        return Err("control HTTP body exceeds limit".to_string());
    }
    let mut body = bytes[header_end..].to_vec();
    if body.len() > content_length {
        return Err("control HTTP request contains bytes beyond Content-Length".to_string());
    }
    while body.len() < content_length {
        let remaining = content_length - body.len();
        let take = remaining.min(chunk.len());
        let count = stream
            .read(&mut chunk[..take])
            .await
            .map_err(|error| format!("read control HTTP body: {error}"))?;
        if count == 0 {
            return Err("control HTTP body ended before Content-Length".to_string());
        }
        body.extend_from_slice(&chunk[..count]);
    }
    Ok(ControlHttpRequest {
        method,
        target,
        body,
    })
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

#[cfg(test)]
mod tests {
    use super::*;

    /// State query filters are exact and can exclude only records explicitly marked stale.
    #[test]
    fn state_query_filters_semantics_sources_and_staleness() {
        let fresh = state_view_record(
            "world",
            "hazard",
            "crossing-a",
            "observed",
            "node:cane-a/safety",
            "hazards",
            serde_json::json!({"present": false}),
            Some(false),
        );
        let stale = state_view_record(
            "world",
            "hazard",
            "crossing-a",
            "reported",
            "node:dog-a/navigation",
            "hazards",
            serde_json::json!({"present": true}),
            Some(true),
        );
        let query = parse_query(
            "object_class=world&object_type=hazard&semantic=observed&include_stale=false",
        );

        assert!(state_record_matches(&fresh, &query));
        assert!(!state_record_matches(&stale, &query));
        assert!(!state_record_matches(
            &stale,
            &parse_query("include_stale=false")
        ));
        assert!(state_record_matches(
            &stale,
            &parse_query("include_stale=true")
        ));
    }

    /// Empty inventory still carries a versioned advisory snapshot rather than an error.
    #[test]
    fn inventory_snapshot_is_versioned_and_empty_before_registration() {
        let value = inventory_json(
            &state::InMemorySharedNodeState::new(),
            domain::TimestampMs::new(42),
        );
        assert_eq!(value["schema_version"], "roboguide.inventory/v0.1");
        assert_eq!(value["observed_at_ms"], 42);
        assert_eq!(value["nodes"], serde_json::json!([]));
    }

    /// Nonempty inventory preserves observation times and normalizes capability/resource kinds.
    #[test]
    fn inventory_snapshot_projects_registered_planning_facts() {
        let registration = domain::NodeRegistration::new_with_contracts(
            domain::NodeId::new("dog-a").expect("node id is valid"),
            domain::LocalRuntime::new("local-runtime", "1").expect("runtime is valid"),
            domain::NodeContractVersion::v0_2(),
            vec![domain::Capability::new(
                domain::CapabilityKind::Transport,
                true,
            )],
            vec![
                domain::CapabilityContractRef::new("mobility", "move", "v1")
                    .expect("contract is valid"),
            ],
            vec![
                domain::Resource::new(
                    domain::ResourceId::new("space-a").expect("resource id is valid"),
                    domain::ResourceKind::Space,
                    2,
                )
                .expect("resource is valid"),
            ],
        );
        let snapshot = domain::NodeStateSnapshot::new(
            registration,
            domain::NodeStatus::new(domain::NodeHealth::Degraded, domain::TimestampMs::new(7)),
            domain::TimestampMs::new(8),
            domain::NodeLivenessObservation::new(
                domain::NodeLiveness::Reachable,
                domain::TimestampMs::new(9),
            ),
        );
        let mut shared_state = state::InMemorySharedNodeState::new();
        ports::SharedNodeStateWriter::record_node(&mut shared_state, snapshot)
            .expect("snapshot is accepted");

        let value = inventory_json(&shared_state, domain::TimestampMs::new(10));

        assert_eq!(value["nodes"][0]["reported_health"], "Degraded");
        assert_eq!(value["nodes"][0]["source_observed_at_ms"], 7);
        assert_eq!(value["nodes"][0]["received_at_ms"], 8);
        assert_eq!(value["nodes"][0]["liveness_observed_at_ms"], 9);
        assert_eq!(value["nodes"][0]["capabilities"][0]["kind"], "transport");
        assert_eq!(value["nodes"][0]["contracts"][0], "mobility.move@v1");
        assert_eq!(value["nodes"][0]["resources"][0]["kind"], "space");
        assert_eq!(value["nodes"][0]["resources"][0]["capacity"], 2);
    }

    /// Advisory inventory excludes an exact contract whose latest readiness fact is false.
    #[test]
    fn inventory_snapshot_excludes_unavailable_exact_contracts() {
        let system_id = domain::LocalSystemId::new("mapping").expect("system id is valid");
        let contract = domain::CapabilityContractRef::new("spatial.map", "localize", "v0")
            .expect("contract is valid");
        let registration = domain::NodeRegistration::new_with_local_systems_and_readiness(
            domain::NodeId::new("dog-b").expect("node id is valid"),
            vec![domain::LocalSystemDescriptor::new(
                system_id.clone(),
                domain::LocalRuntime::new("mapping", "1").expect("runtime is valid"),
                std::collections::BTreeMap::new(),
            )],
            domain::NodeContractVersion::v0_2(),
            vec![domain::Capability::new(
                domain::CapabilityKind::Compute,
                false,
            )],
            std::collections::BTreeMap::from([(contract.clone(), system_id)]),
            std::collections::BTreeMap::from([(contract.clone(), domain::CapabilityKind::Compute)]),
            std::collections::BTreeMap::from([(contract, false)]),
            Vec::new(),
            Vec::new(),
            std::collections::BTreeMap::new(),
        )
        .expect("registration is valid");
        let snapshot = domain::NodeStateSnapshot::new(
            registration,
            domain::NodeStatus::new(domain::NodeHealth::Online, domain::TimestampMs::new(1)),
            domain::TimestampMs::new(2),
            domain::NodeLivenessObservation::new(
                domain::NodeLiveness::Reachable,
                domain::TimestampMs::new(2),
            ),
        );
        let mut shared_state = state::InMemorySharedNodeState::new();
        ports::SharedNodeStateWriter::record_node(&mut shared_state, snapshot)
            .expect("snapshot is accepted");

        let value = inventory_json(&shared_state, domain::TimestampMs::new(3));

        assert_eq!(value["nodes"][0]["reported_health"], "Online");
        assert_eq!(value["nodes"][0]["contracts"], serde_json::json!([]));
    }

    /// The control HTTP reader reconstructs a Mission request split across arbitrary TCP writes.
    #[tokio::test]
    async fn control_http_reader_accepts_fragmented_body() {
        use tokio::io::AsyncWriteExt;
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
            .await
            .expect("test listener binds");
        let address = listener.local_addr().expect("test listener has address");
        let reader = tokio::spawn(async move {
            let (mut stream, _) = listener.accept().await.expect("test request connects");
            read_control_http_request(&mut stream).await
        });
        let mut client = tokio::net::TcpStream::connect(address)
            .await
            .expect("test client connects");
        let body = br#"{"schema":"roboguide.mission-plan/v0.2"}"#;
        let header = format!(
            "POST /v1/missions HTTP/1.1\r\nHost: localhost\r\nContent-Length: {}\r\n\r\n",
            body.len()
        );
        client
            .write_all(&header.as_bytes()[..19])
            .await
            .expect("first header fragment writes");
        tokio::task::yield_now().await;
        client
            .write_all(&header.as_bytes()[19..])
            .await
            .expect("second header fragment writes");
        client
            .write_all(&body[..7])
            .await
            .expect("first body fragment writes");
        tokio::task::yield_now().await;
        client
            .write_all(&body[7..])
            .await
            .expect("second body fragment writes");

        let request = reader
            .await
            .expect("reader task joins")
            .expect("fragmented request is valid");
        assert_eq!(request.method, "POST");
        assert_eq!(request.target, "/v1/missions");
        assert_eq!(request.body, body);
    }

    /// The control HTTP reader rejects an EOF before the declared body is complete.
    #[tokio::test]
    async fn control_http_reader_rejects_truncated_body() {
        use tokio::io::AsyncWriteExt;
        let listener = tokio::net::TcpListener::bind("127.0.0.1:0")
            .await
            .expect("test listener binds");
        let address = listener.local_addr().expect("test listener has address");
        let reader = tokio::spawn(async move {
            let (mut stream, _) = listener.accept().await.expect("test request connects");
            read_control_http_request(&mut stream).await
        });
        let mut client = tokio::net::TcpStream::connect(address)
            .await
            .expect("test client connects");
        client
            .write_all(
                b"POST /v1/missions HTTP/1.1\r\nHost: localhost\r\nContent-Length: 10\r\n\r\n{}",
            )
            .await
            .expect("truncated request writes");
        client.shutdown().await.expect("client write side closes");

        let error = reader
            .await
            .expect("reader task joins")
            .expect_err("truncated body is rejected");
        assert!(error.contains("ended before Content-Length"));
    }

    /// The controller database writer lease excludes peers and becomes available after release.
    #[test]
    fn event_log_writer_lock_is_process_exclusive() {
        let directory = tempfile::tempdir().expect("temporary directory exists");
        let event_path = directory.path().join("controller.sqlite3");
        let first = acquire_event_log_writer_lock(&event_path).expect("first writer acquires");

        assert!(acquire_event_log_writer_lock(&event_path).is_err());
        drop(first);
        acquire_event_log_writer_lock(&event_path).expect("released writer lock is reacquired");
    }

    /// The versioned placement fixture decodes into typed Control constraints.
    #[test]
    fn actor_placement_file_loads_typed_constraints() {
        let path = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../scenarios/distributed-spatial-memory-v0.1/actor-placement.json");
        let constraints = load_actor_placement_file(&path).expect("placement fixture loads");
        assert_eq!(constraints.len(), 4);
        assert!(constraints.iter().all(|constraint| {
            matches!(
                (
                    constraint.actor_id().as_str(),
                    constraint.node_id().as_str()
                ),
                ("robot-dog-a", "dog-a") | ("robot-dog-b", "dog-b")
            )
        }));
    }

    /// Placement files with an unknown schema fail before the server starts accepting traffic.
    #[test]
    fn actor_placement_file_rejects_unknown_schema() {
        let directory = tempfile::tempdir().expect("temporary directory exists");
        let path = directory.path().join("placement.json");
        std::fs::write(
            &path,
            r#"{"schema":"roboguide.actor-placement/v9","constraints":[]}"#,
        )
        .expect("placement fixture writes");
        let error = load_actor_placement_file(&path).expect_err("unknown schema is rejected");
        assert!(error.to_string().contains("unsupported schema"));
    }

    /// The experiment placement file exactly covers every Actor in all four submitted Missions.
    #[test]
    fn actor_placement_fixture_has_strict_four_mission_coverage() {
        let root = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../scenarios/distributed-spatial-memory-v0.1");
        let constraints = load_actor_placement_file(&root.join("actor-placement.json"))
            .expect("placement fixture loads");
        let mut control = control::ControlPlane::new();
        for constraint in constraints {
            control
                .set_actor_node_constraint(
                    constraint.mission_id().clone(),
                    constraint.actor_id().clone(),
                    constraint.node_id().clone(),
                )
                .expect("fixture constraints are mutually consistent");
        }
        for file_name in [
            "mission-a-build-publish.json",
            "mission-a-import-verify.json",
            "mission-b-build-publish.json",
            "mission-b-import-verify.json",
        ] {
            let source = std::fs::read_to_string(root.join(file_name))
                .expect("Mission fixture remains readable");
            let plan = decode_mission_plan(&source).expect("Mission fixture remains valid");
            validate_actor_placement_coverage(&control, &plan)
                .expect("placement exactly covers Mission actors");
        }
    }

    /// Strict placement rejects a misspelled Actor instead of falling back to generic matching.
    #[test]
    fn actor_placement_strict_coverage_rejects_typo() {
        let root = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../scenarios/distributed-spatial-memory-v0.1");
        let source = std::fs::read_to_string(root.join("mission-a-build-publish.json"))
            .expect("Mission fixture remains readable");
        let plan = decode_mission_plan(&source).expect("Mission fixture remains valid");
        let mut control = control::ControlPlane::new();
        control
            .set_actor_node_constraint(
                plan.goal().mission_id().clone(),
                domain::ActorId::new("robot-dog-typo").expect("typo remains syntactically valid"),
                domain::NodeId::new("dog-a").expect("node id is valid"),
            )
            .expect("syntactic configuration loads before plan validation");

        let error = validate_actor_placement_coverage(&control, &plan)
            .expect_err("unknown and missing Actors must fail closed");
        assert!(error.contains("missing actors [robot-dog-a]"));
        assert!(error.contains("unknown actors [robot-dog-typo]"));
    }

    /// A temporarily unavailable bound Actor leaves dispatch pending for a later heartbeat.
    #[test]
    fn unavailable_bound_actor_is_deferred_without_failing_server() {
        let error = orchestration::OrchestrationError::Control(
            control::ControlError::ActorBindingRequiresReconciliation {
                mission_id: domain::MissionId::new("mission").expect("mission id is valid"),
                actor_id: domain::ActorId::new("actor").expect("actor id is valid"),
                node_id: domain::NodeId::new("node").expect("node id is valid"),
            },
        );
        assert!(deferred_dispatch(&error));
    }

    /// Startup rejects a replacement placement policy that does not cover a restored Mission.
    #[test]
    fn restored_mission_is_revalidated_before_server_start() {
        let root = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../scenarios/distributed-spatial-memory-v0.1");
        let source = std::fs::read_to_string(root.join("mission-a-build-publish.json"))
            .expect("Mission fixture remains readable");
        let plan_value: serde_json::Value =
            serde_json::from_str(&source).expect("Mission fixture remains JSON");
        let orchestrator = MissionOrchestrator::restore_json(
            &serde_json::json!([{
                "plan": plan_value,
                "group_id": "group-restored-map-a",
                "lifecycle": "Accepted"
            }])
            .to_string(),
        )
        .expect("orchestration checkpoint restores");
        let mut control = control::ControlPlane::new();
        let mission_id = orchestrator.mission_ids()[0].clone();
        control
            .set_actor_node_constraint(
                mission_id,
                domain::ActorId::new("robot-dog-typo").expect("typo remains syntactically valid"),
                domain::NodeId::new("dog-a").expect("node identity is valid"),
            )
            .expect("syntactic replacement policy loads");

        let error = validate_restored_actor_placement_coverage(&control, &orchestrator)
            .expect_err("restored Mission coverage must fail closed");
        assert!(error.contains("restored Mission placement is invalid"));
        assert!(error.contains("missing actors [robot-dog-a]"));
    }
}
