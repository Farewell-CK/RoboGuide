#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! RoboGuide Integration Server process.

mod application;
mod artifact_http;
mod controller_http;

use integration::grpc::v0_2::robo_guide_node_protocol_server::RoboGuideNodeProtocolServer as LegacyRoboGuideNodeProtocolServer;
use integration::grpc::v0_4::robo_guide_node_protocol_server::RoboGuideNodeProtocolServer;
use integration::{GrpcIntegrationService, GrpcLegacyV02Service, GrpcNodeEvent};
use orchestration::{
    CONTROLLER_CHECKPOINT_SCHEMA as INTEGRATION_CHECKPOINT_SCHEMA, IntegrationRuntimeBridge,
    IntegrationRuntimeError, ObservedTaskExecutionResult,
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
const SERVER_CHECKPOINT_SCHEMA: &str = "roboguide.controller-checkpoint/v15";

/// Immediately previous wrapper accepted for one-step coordination checkpoint migration.
const PREVIOUS_SERVER_CHECKPOINT_SCHEMA: &str = "roboguide.controller-checkpoint/v14";

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
    /// Current gRPC routes used to bind Node-authored evidence to an active Node session.
    router: integration::GrpcNodeRouter,
}

/// Composition adapter that admits durable localization evidence to current Runtime attempts.
struct ControllerLocalizationObserver {
    /// Shared Controller state installed only after its checkpoint commits.
    controller: Arc<Mutex<ControllerState>>,
    /// Durable event/checkpoint store shared with the Integration path.
    event_log: state::SqliteEventLog,
    /// Process-local writer ordering shared by every Controller mutation path.
    write_gate: Arc<Mutex<()>>,
}

impl artifact_http::LocalizationEvidenceObserver for ControllerLocalizationObserver {
    /// Persists Runtime relation transitions and the resulting checkpoint before publishing state.
    fn observe(
        &self,
        evidence: &domain::LocalizationVerificationEvidence,
        received_at: domain::TimestampMs,
    ) -> Result<(), String> {
        let _write_guard = self
            .write_gate
            .lock()
            .map_err(|_| "event-log write gate is poisoned".to_string())?;
        let log = self.event_log.clone();
        log.begin_batch()
            .map_err(|error| format!("localization Runtime projection is busy: {error}"))?;
        let mut live = self.controller.lock().map_err(|_| {
            let _ = log.rollback_batch();
            "integration bridge lock is poisoned".to_string()
        })?;
        let mut candidate = live.clone();
        let correlation = domain::CorrelationId::new("localization-runtime-projection")
            .expect("static localization correlation identity is valid");
        if let Err(error) =
            candidate
                .bridge
                .observe_localization_evidence(evidence, received_at, &correlation)
        {
            let _ = log.rollback_batch();
            return Err(format!("Runtime rejected localization evidence: {error}"));
        }
        match log.take_error() {
            Ok(Some(error)) => {
                let _ = log.rollback_batch();
                return Err(format!(
                    "localization Runtime evidence persistence failed: {error}"
                ));
            }
            Ok(None) => {}
            Err(error) => {
                let _ = log.rollback_batch();
                return Err(format!(
                    "localization Runtime event sink is unavailable: {error}"
                ));
            }
        }
        let checkpoint_json = match server_checkpoint_json(&candidate) {
            Ok(checkpoint) => checkpoint,
            Err(error) => {
                let _ = log.rollback_batch();
                return Err(format!(
                    "localization checkpoint serialization failed: {error}"
                ));
            }
        };
        if let Err(error) = log.save_checkpoint(SERVER_CHECKPOINT_SCHEMA, &checkpoint_json) {
            let _ = log.rollback_batch();
            return Err(format!(
                "localization checkpoint persistence failed: {error}"
            ));
        }
        if let Err(error) = log.commit_batch() {
            let _ = log.rollback_batch();
            return Err(format!(
                "localization Runtime projection commit failed: {error}"
            ));
        }
        *live = candidate;
        Ok(())
    }
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
            "Node-authored mutation requires current Node/session identity".to_string()
        })?;
        if publisher.node_id() != expected_node_id {
            return Err(format!(
                "publisher node {} does not match semantic owner {expected_node_id}",
                publisher.node_id()
            ));
        }
        self.router
            .session_is_current(expected_node_id.as_str(), publisher.session_id())
            .map_err(|error| error.to_string())?
            .then_some(())
            .ok_or_else(|| format!("publisher session is not current for node {expected_node_id}"))
    }

    /// Requires typed evidence to name Runtime's exact current logical-slot attempt and owner.
    fn admit_localization_evidence(
        &self,
        evidence: &domain::LocalizationVerificationEvidence,
    ) -> Result<(), String> {
        let controller = self
            .controller
            .lock()
            .map_err(|_| "Controller Runtime authority is unavailable".to_string())?;
        controller
            .bridge
            .localization_evidence_is_current(evidence)
            .then_some(())
            .ok_or_else(|| {
                "localization evidence does not name the current physical attempt and Node owner"
                    .to_string()
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
    let mut restored_localization_evidence = false;
    for (evidence, received_at) in artifact_catalog
        .localization_evidence()
        .map_err(|error| format!("localization evidence replay failed: {error}"))?
    {
        restored_localization_evidence |= controller
            .bridge
            .restore_localization_evidence(&evidence, received_at)
            .map_err(|error| format!("localization Runtime replay failed: {error}"))?;
    }
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
    if initialize_checkpoint || actor_placement_path.is_some() || restored_localization_evidence {
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
    let receiver_clock = process_clock.clone();
    let timer_controller = Arc::clone(&controller);
    let timer_event_log = event_log.clone();
    let timer_event_write_gate = event_write_gate.clone();
    let timer_clock = process_clock.clone();
    let artifact_catalog_for_server = artifact_catalog.clone();
    let artifact_store_for_server = artifact_store.clone();
    let memory_admission: Arc<dyn artifact_http::MemoryProviderAdmission> =
        Arc::new(ControllerMemoryAdmission {
            controller: Arc::clone(&controller),
            router: memory_session_router,
        });
    let localization_observer: Arc<dyn artifact_http::LocalizationEvidenceObserver> =
        Arc::new(ControllerLocalizationObserver {
            controller: Arc::clone(&controller),
            event_log: event_log.clone(),
            write_gate: event_write_gate.clone(),
        });
    let (fatal_sender, mut fatal_receiver) = tokio::sync::mpsc::unbounded_channel::<String>();
    let timer_fatal_sender = fatal_sender.clone();
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
            localization_observer,
        )
        .await
        {
            eprintln!("artifact HTTP server stopped: {error}");
        }
    });
    tokio::spawn(async move {
        let mut interval = tokio::time::interval(Duration::from_secs(1));
        interval.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
        loop {
            interval.tick().await;
            if let Err(error) = drive_application_timer(
                &timer_controller,
                &timer_event_log,
                &timer_event_write_gate,
                timer_clock.now(),
            ) {
                let reason = format!("application timer stopped: {error}");
                let _ = timer_fatal_sender.send(reason);
                return;
            }
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
                    let now = receiver_clock.now();
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
                    } else if !registration_fact
                        && let Err(error) = begin_current_ambiguity_recoveries(
                            &mut candidate,
                            now,
                            &correlation,
                            &mut receiver_event_log.clone(),
                        )
                    {
                        let _ = receiver_event_log.rollback_batch();
                        let reason = format!("physical ambiguity recovery failed: {error}");
                        completion.unavailable(reason.clone());
                        let _ = fatal_sender.send(reason);
                        return;
                    } else if !registration_fact
                        && let Err(error) = resume_pending_recoveries(
                            &mut candidate,
                            now,
                            &correlation,
                            &mut receiver_event_log.clone(),
                        )
                    {
                        let _ = receiver_event_log.rollback_batch();
                        let reason = format!("pending recovery progression failed: {error}");
                        completion.unavailable(reason.clone());
                        let _ = fatal_sender.send(reason);
                        return;
                    } else if let Err(error) = apply_pending_cancellations(
                        &mut candidate,
                        now,
                        &correlation,
                        &mut receiver_event_log.clone(),
                    ) {
                        let _ = receiver_event_log.rollback_batch();
                        let reason = format!("Mission cancellation progression failed: {error}");
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
                    } else if !registration_fact
                        && let Err(error) =
                            drive_rebound_attempts(&mut candidate, now, &correlation)
                    {
                        let _ = receiver_event_log.rollback_batch();
                        let reason = format!("rebound Task dispatch failed: {error}");
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
                        Ok(mut live) => {
                            *live = candidate;
                            // Cancel receipts are nonterminal: retrying Cancel in response to its
                            // own receipt/snapshot would form an unbounded feedback loop. The
                            // application timer owns those retries until terminal evidence.
                            if let Err(error) = live.bridge.flush_dispatch_outbox() {
                                eprintln!("durable command outbox delivery deferred: {error}");
                            }
                        }
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
        fatal = fatal_receiver.recv() => Err(fatal.unwrap_or_else(|| "application driver stopped unexpectedly".to_string()).into()),
    }
}

use application::*;
use controller_http::*;

#[cfg(test)]
mod tests;
