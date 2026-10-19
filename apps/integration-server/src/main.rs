#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! RoboGuide Integration Server process.

mod application;
mod artifact_http;
mod controller_http;
mod diagnostics;

use diagnostics::StageObservation;
use integration::grpc::v0_2::robo_guide_node_protocol_server::RoboGuideNodeProtocolServer as LegacyRoboGuideNodeProtocolServer;
use integration::grpc::v0_4::robo_guide_node_protocol_server::RoboGuideNodeProtocolServer;
use integration::{
    GrpcIntegrationService, GrpcLegacyV02Service, GrpcNodeEvent, NodeIdentityPolicy,
};
use orchestration::{
    CONTROLLER_CHECKPOINT_SCHEMA as INTEGRATION_CHECKPOINT_SCHEMA, IntegrationRuntimeBridge,
    IntegrationRuntimeError, ObservedTaskExecutionResult,
};
use orchestration::{MissionOrchestrator, OrchestrationError, decode_mission_plan};
use ports::{Clock, NodeLeasePolicy, NodeProtocolPolicy, SharedNodeStateReader, StateRecordReader};
use ports::{DiagnosticKind, DiagnosticStage};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use tonic::transport::{Certificate, Identity, ServerTlsConfig};

/// Schema marker for the Controller wrapper including deployment candidate restrictions.
///
/// The wrapper advances when a previous binary would silently ignore a new
/// authority field, fencing downgrade even though the inner JSON is compatible.
const SERVER_CHECKPOINT_SCHEMA: &str = state::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA;
/// Previous wrapper without continuous Group Memory-use authority.
const PRE_MEMORY_USE_SERVER_CHECKPOINT_SCHEMA: &str = "roboguide.controller-checkpoint/v36";
/// Previous wrapper without provider-qualified byte evidence for the sole Spatial source.
const PRE_TYPED_MEMORY_REPLICA_SERVER_CHECKPOINT_SCHEMA: &str =
    "roboguide.controller-checkpoint/v35";
/// Last wrapper without durable pre-execution Memory contexts and current backend observations.
const PRE_MEMORY_PREPARATION_SERVER_CHECKPOINT_SCHEMA: &str = "roboguide.controller-checkpoint/v34";
/// Last wrapper without complete typed-map content policy digests.
const PRE_TYPED_MAP_POLICY_SERVER_CHECKPOINT_SCHEMA: &str = "roboguide.controller-checkpoint/v33";
/// Last wrapper without registered typed-map producer and current publishing-attempt authority.
const PRE_SPATIAL_PROVIDER_SERVER_CHECKPOINT_SCHEMA: &str = "roboguide.controller-checkpoint/v32";
/// Last wrapper without Control-owned Memory membership and content authorization authority.
const PRE_MEMORY_AUTHORITY_SERVER_CHECKPOINT_SCHEMA: &str = "roboguide.controller-checkpoint/v31";
/// Last wrapper without explicit original-provider Memory reconciliation authority.
const PRE_MEMORY_SERVER_CHECKPOINT_SCHEMA: &str = "roboguide.controller-checkpoint/v30";
/// Previous wrapper had identity authority but no persistent State policy and generic verifier registry.
const PRE_POLICY_SERVER_CHECKPOINT_SCHEMA: &str = "roboguide.controller-checkpoint/v29";
/// Previous wrapper had typed stop authority but no durable Node identity lifecycle.
const PRE_IDENTITY_SERVER_CHECKPOINT_SCHEMA: &str = "roboguide.controller-checkpoint/v28";
/// Previous wrapper carried unverified relation stop strings without complete evidence authority.
const PRE_STOP_AUTHORITY_SERVER_CHECKPOINT_SCHEMA: &str = "roboguide.controller-checkpoint/v27";
/// Previous wrapper did not require quorum-replicated authority command evidence.
const PRE_JOURNAL_SERVER_CHECKPOINT_SCHEMA: &str = "roboguide.controller-checkpoint/v26";
/// Previous wrapper has legacy breaker counters but cannot carry the new rolling-window authority.
const PRE_BREAKER_SERVER_CHECKPOINT_SCHEMA: &str = "roboguide.controller-checkpoint/v25";
/// Prior wrapper with durable scheduling policy but no required replication profile.
const PRE_REPLICATION_SERVER_CHECKPOINT_SCHEMA: &str = "roboguide.controller-checkpoint/v24";
/// Prior wrapper with immutable terminal times but without persistent dispatch policy.
const PRE_DISPATCH_SERVER_CHECKPOINT_SCHEMA: &str = "roboguide.controller-checkpoint/v23";
/// Previous wrapper supports isolation but has no original terminal receive-time authority.
const PRE_TIMING_SERVER_CHECKPOINT_SCHEMA: &str = "roboguide.controller-checkpoint/v22";
/// Prior wrapper without persisted Orchestration isolation authority.
const PRE_ISOLATION_SERVER_CHECKPOINT_SCHEMA: &str = "roboguide.controller-checkpoint/v21";
/// Previous wrapper has progress/stop intents but no immutable stop/continuation declarations.
const PRE_RECOVERY_SERVER_CHECKPOINT_SCHEMA: &str = "roboguide.controller-checkpoint/v20";
/// Wrapper with exact HTTP admission records but no operation progress monitor.
const ADMISSION_SERVER_CHECKPOINT_SCHEMA: &str = "roboguide.controller-checkpoint/v19";

/// Immediately previous wrapper accepted with no deployment candidate restrictions.
const PREVIOUS_SERVER_CHECKPOINT_SCHEMA: &str = "roboguide.controller-checkpoint/v18";
/// Historical wrapper with verifier source identity but no HTTP admission digests.
const VERIFIER_SERVER_CHECKPOINT_SCHEMA: &str = "roboguide.controller-checkpoint/v17";
/// Historical wrapper before deployment candidate restrictions.
const LEGACY_SERVER_CHECKPOINT_SCHEMA: &str = "roboguide.controller-checkpoint/v16";

/// Version marker for the optional deployment-owned actor placement file.
const ACTOR_PLACEMENT_SCHEMA: &str = "roboguide.actor-placement/v0.1";
/// Schema identity for the mission-independent deployment entity registry file.
const PHYSICAL_ENTITY_REGISTRY_SCHEMA: &str = "roboguide.physical-entity-registry/v0.1";

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
    /// Lowercase bounded headers used by the operator authorization layer.
    headers: BTreeMap<String, String>,
    /// Exactly the number of body bytes declared by Content-Length.
    body: Vec<u8>,
}

/// Live process state sharing one Control authority with Mission orchestration.
#[derive(Clone)]
struct ControllerState {
    /// Immutable HTTP receipts; Orchestration still owns complete plan and Mission lifecycle.
    mission_admissions: BTreeMap<String, controller_http::MissionAdmission>,
    /// Memory prerequisite authority plus a startup-reacquired read-only catalog federation.
    memory_preparation: application::memory_preparation::MemoryPreparationState,
    /// Renewable current content permission, separate from immutable preparation/import budgets.
    memory_use: application::memory_use::MemoryUseState,
    /// Exact authorized selective-import intents and durable Node receipts.
    memory_imports: BTreeMap<String, application::memory_import::MemoryImportIntent>,
    /// Application safety policy over existing exact Runtime cancellation intents and raw evidence.
    safety_stops: BTreeMap<String, application::safety_stop::SafetyStopPurpose>,
    /// Integration, Runtime, Control, and horizontal State projections.
    bridge: IntegrationRuntimeBridge<state::SqliteEventLog>,
    /// Complete MissionPlan and explicit Mission lifecycle authority.
    orchestrator: MissionOrchestrator,
    /// Durable exact verdict/Task receipts preventing replay after restart.
    verifier_seen: BTreeSet<(String, String, String)>,
    /// CAS handle used to verify independent evidence bytes; never serialized into authority.
    verifier_artifact_store: Option<artifact_store::FileSystemArtifactStore>,
    /// Startup-frozen verifier source identity, fenced across checkpoint restore.
    verifier_source_digest: Option<String>,
}

/// Read-only admission adapter from Artifact HTTP into current Controller registration facts.
struct ControllerMemoryAdmission {
    /// Same Controller receive clock used by original Memory intent deadlines.
    clock: Arc<runtime::SystemMonotonicClock>,
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
        if let Err(error) = application::save_controller_checkpoint(&log, &checkpoint_json) {
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
    /// Revalidates the actual current route and provider before exact original/current attempt checks.
    fn admit_map_publication(
        &self,
        manifest: &domain::MapArtifactManifest,
        publisher: Option<&artifact_http::MemoryPublicationIdentity>,
        execution_id: Option<&str>,
    ) -> Result<(), String> {
        if manifest.producer_provider_id().is_none() {
            return Ok(());
        }
        self.admit_publisher(publisher, manifest.producer_node_id())?;
        self.admit_manifest(&artifact_http::map_authorization_manifest(manifest)?)?;
        let controller = self
            .controller
            .lock()
            .map_err(|_| "map publication authority unavailable")?;
        application::map_publication::validate(
            &controller,
            manifest,
            execution_id.ok_or("current publication attempt is unavailable")?,
        )
    }
    /// Typed-map reads retain original Node tenant; missing old LocalSystem never becomes new policy evidence.
    fn authorize_map_access(
        &self,
        principal: &artifact_http::ArtifactPrincipal,
        manifest: &domain::MapArtifactManifest,
        operation: domain::MemoryAccessOperation,
        import_id: Option<&str>,
    ) -> Result<(), String> {
        self.revalidate_artifact_principal(principal)?;
        if let Some(id) = import_id {
            {
                let live = self
                    .controller
                    .lock()
                    .map_err(|_| "typed Memory purpose authority is unavailable")?;
                if live
                    .memory_imports
                    .get(id)
                    .and_then(|import| import.request.typed_map.as_ref())
                    != Some(manifest)
                {
                    return Err(
                        "typed Memory purpose lacks this complete original map manifest".into(),
                    );
                }
            }
            return self.authorize_memory_access(
                principal,
                &domain::map_memory_manifest(manifest)?,
                operation,
                Some(id),
            );
        }
        let live = self
            .controller
            .lock()
            .map_err(|_| "typed map authority unavailable")?;
        let control = live.bridge.control();
        if control.node_identity_registry().is_some_and(|r| {
            r.records
                .get(manifest.producer_node_id().as_str())
                .is_none_or(|id| id.tenant_id != principal.tenant_id)
        }) {
            return Err("typed-map producer belongs to another tenant".into());
        }
        if control.memory_authorization_registry().is_some() {
            control
                .authorize_typed_map_principal(
                    manifest,
                    &principal.tenant_id,
                    principal.subject(),
                    operation,
                )
                .map_err(|e| e.to_string())?;
        }
        Ok(())
    }
    /// Resolves an opaque original import through current Control instead of treating headers as grants.
    fn authorize_memory_access(
        &self,
        principal: &artifact_http::ArtifactPrincipal,
        manifest: &domain::MemoryArtifactManifest,
        operation: domain::MemoryAccessOperation,
        import_id: Option<&str>,
    ) -> Result<(), String> {
        self.revalidate_artifact_principal(principal)?;
        let live = self
            .controller
            .lock()
            .map_err(|_| "Memory content authority unavailable")?;
        let control = live.bridge.control();
        if operation != domain::MemoryAccessOperation::Discover
            && matches!(manifest.scope(), domain::MemoryScope::Local)
            && !matches!(manifest.owner(),domain::MemoryOwner::Node{node_id,..} if principal.node_id()==Some(node_id))
        {
            return Err("Local Memory consumption is confined to its original Node".into());
        }
        if control.memory_authorization_registry().is_none() {
            if operation != domain::MemoryAccessOperation::Discover
                && matches!(manifest.scope(), domain::MemoryScope::ExecutionGroup(_))
            {
                return Err("Group content requires explicit current Control authorization".into());
            }
            if let domain::MemoryOwner::Node { node_id, .. } = manifest.owner()
                && control.node_identity_registry().is_some_and(|r| {
                    r.records
                        .get(node_id.as_str())
                        .is_none_or(|id| id.tenant_id != principal.tenant_id)
                })
            {
                return Err("Memory content belongs to another tenant".into());
            }
            return Ok(());
        }
        if let Some(import_id) = import_id {
            let original = live
                .memory_imports
                .get(import_id)
                .ok_or("content request has no original durable Memory purpose")?;
            if &original.request.manifest != manifest
                || principal.node_id() != Some(&original.request.node_id)
                || !principal.provider_allowed(&original.request.consumer_provider_id)
            {
                return Err("Memory purpose differs from actual certificate Node/provider or immutable manifest".into());
            }
            let now = ports::Clock::now(self.clock.as_ref());
            if now.as_millis() >= original.request.deadline_ms {
                return Err("original Memory content access budget expired".into());
            }
            application::memory_import::authorize(&live, &original.request, now)?;
            let provider = live
                .bridge
                .state()
                .node(&original.request.node_id)
                .and_then(|node| {
                    node.registration()
                        .memory_providers()
                        .iter()
                        .find(|p| p.provider_id() == original.request.consumer_provider_id)
                })
                .ok_or("Memory consumer provider unavailable")?;
            let expected = if original.request.preparation_id.is_some() {
                None
            } else {
                original.authority.as_ref().map(|a| &a.membership)
            };
            if let Some(map) = original.request.typed_map.as_ref() {
                control
                    .authorize_typed_map_consumer(
                        map,
                        &principal.tenant_id,
                        &original.request.group_id,
                        &original.request.task_ref,
                        &original.request.role_id,
                        &original.request.node_id,
                        provider,
                        operation,
                        expected,
                    )
                    .map_err(|e| e.to_string())?;
            } else {
                control
                    .authorize_memory_consumer(
                        manifest,
                        &principal.tenant_id,
                        &original.request.group_id,
                        &original.request.task_ref,
                        &original.request.role_id,
                        &original.request.node_id,
                        provider,
                        operation,
                        expected,
                    )
                    .map_err(|e| e.to_string())?;
            }
            return Ok(());
        }
        control
            .authorize_memory_principal(
                manifest,
                &principal.tenant_id,
                principal.subject(),
                operation,
            )
            .map_err(|e| e.to_string())
    }
    /// Authorizes a source-bound immutable handoff through the current tenant/policy registry.
    fn authorize_memory_handoff(
        &self,
        principal: &artifact_http::ArtifactPrincipal,
        parent: &domain::MemoryArtifactManifest,
        child: &domain::MemoryArtifactManifest,
    ) -> Result<(), String> {
        self.revalidate_artifact_principal(principal)?;
        let live = self
            .controller
            .lock()
            .map_err(|_| "Memory handoff authority unavailable")?;
        live.bridge
            .control()
            .authorize_memory_handoff(parent, child, &principal.tenant_id, principal.subject())
            .map_err(|error| error.to_string())
    }
    /// Resolves current stable principal and exact actual certificate validity without role headers.
    fn refresh_artifact_principal(
        &self,
        principal: &artifact_http::ArtifactPrincipal,
    ) -> Result<artifact_http::ArtifactPrincipal, String> {
        let live = self
            .controller
            .lock()
            .map_err(|_| "Artifact identity authority is unavailable")?;
        let control = live.bridge.control();
        if let Some(registry) = control.artifact_authorization_registry() {
            let leaf = principal
                .certificate_digest
                .as_deref()
                .ok_or("enrolled Artifact authority requires actual mTLS")?;
            let now = domain::TimestampMs::new(
                u64::try_from(
                    SystemTime::now()
                        .duration_since(UNIX_EPOCH)
                        .map_err(|e| e.to_string())?
                        .as_millis(),
                )
                .map_err(|e| e.to_string())?,
            );
            let grant = control
                .authorize_artifact_certificate(leaf, principal.certificate_expires_at_ms, now)
                .map_err(|e| e.to_string())?;
            return Ok(principal.with_committed_grant(registry, grant));
        }
        if principal.registry_id.is_some() {
            return Err("Artifact authority cannot downgrade to static grants".into());
        }
        principal.validate_transport()?;
        Ok(principal.clone())
    }
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
        if let domain::MemoryScope::ExecutionGroup(group_id) = manifest.scope() {
            let group = controller
                .bridge
                .control()
                .group(group_id)
                .ok_or("Group-scoped Memory owner Group is absent")?;
            if group.lifecycle() == control::GroupLifecycle::Released
                || group.membership_version().is_none()
                || !group
                    .task_executions()
                    .any(|task| task.assignments().iter().any(|a| a.node_id() == node_id))
                || manifest
                    .source_mission_id()
                    .is_some_and(|mission| mission != group.mission_id())
            {
                return Err(
                    "Group-scoped Memory producer is not a current exact participant".into(),
                );
            }
        }
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
#[derive(Clone, Serialize, Deserialize)]
struct ServerCheckpoint {
    /// Required cluster/profile identity; standalone restore cannot bypass quorum authority.
    #[serde(default)]
    replication_profile: Option<String>,
    /// Actual accepted body digests; old checkpoints retain unavailable receipt evidence.
    #[serde(default)]
    mission_admissions: BTreeMap<String, controller_http::MissionAdmission>,
    /// Original prerequisite contexts, budgets, pending commands and immutable Node receipts.
    #[serde(default)]
    memory_preparation: application::memory_preparation::MemoryPreparationState,
    /// Original use commands and fences; historical absence remains unavailable.
    #[serde(default)]
    memory_use: application::memory_use::MemoryUseState,
    /// Exact authorized selective-import intents and durable Node receipts.
    #[serde(default)]
    memory_imports: BTreeMap<String, application::memory_import::MemoryImportIntent>,
    /// Application safety policy over existing exact Runtime cancellation intents and raw evidence.
    #[serde(
        default,
        deserialize_with = "application::safety_stop::deserialize_purposes"
    )]
    safety_stops: BTreeMap<String, application::safety_stop::SafetyStopPurpose>,
    /// Exact wrapper schema marker.
    schema: String,
    /// Existing Integration/Control/State/Runtime checkpoint JSON.
    integration_json: String,
    /// Complete Mission orchestration checkpoint JSON.
    orchestration_json: String,
    /// Verdict receipts already processed, including negative final verdicts.
    #[serde(default)]
    verifier_seen: BTreeSet<(String, String, String)>,
    /// Source digest used by this database; a restart cannot silently change it.
    #[serde(default)]
    verifier_source_digest: Option<String>,
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

/// Rejects a remotely bound Artifact listener unless its mTLS profile is complete.
fn require_secure_artifact_binding(
    address: std::net::SocketAddr,
    mtls_configured: bool,
) -> Result<(), Box<dyn std::error::Error>> {
    if !address.ip().is_loopback() && !mtls_configured {
        return Err(
            "Artifact HTTP must remain loopback unless the opt-in mTLS identity profile is enabled"
                .into(),
        );
    }
    Ok(())
}

/// Loads the shared Node transport lease policy from startup-owned configuration.
///
/// Defaults preserve the existing five-second heartbeat and fifteen-second lease. Core crates do
/// not read process environment variables; this composition root resolves them once and passes the
/// immutable policy to both transport and application authorities.
fn configured_node_protocol_policy() -> Result<NodeProtocolPolicy, Box<dyn std::error::Error>> {
    let heartbeat = configured_duration(
        "ROBOGUIDE_NODE_HEARTBEAT_INTERVAL_MS",
        ports::DEFAULT_NODE_HEARTBEAT_INTERVAL_MS,
    )?;
    let lease = configured_duration(
        "ROBOGUIDE_NODE_LEASE_DURATION_MS",
        ports::DEFAULT_NODE_LEASE_DURATION_MS,
    )?;
    let acceptance = configured_duration(
        "ROBOGUIDE_NODE_APPLICATION_ACCEPTANCE_TIMEOUT_MS",
        ports::DEFAULT_NODE_APPLICATION_ACCEPTANCE_TIMEOUT_MS,
    )?;
    let lease_policy = NodeLeasePolicy::try_new(heartbeat, lease)?;
    Ok(NodeProtocolPolicy::try_new(lease_policy, acceptance)?)
}

/// Loads the opt-in mutual-TLS identity profile; unset variables retain trusted loopback mode.
fn configured_node_identity()
-> Result<(NodeIdentityPolicy, Option<ServerTlsConfig>), Box<dyn std::error::Error>> {
    let names = [
        "ROBOGUIDE_NODE_MTLS_SERVER_CERT",
        "ROBOGUIDE_NODE_MTLS_SERVER_KEY",
        "ROBOGUIDE_NODE_MTLS_CLIENT_CA",
        "ROBOGUIDE_NODE_MTLS_NODE_CERT_DIGESTS",
    ];
    let values = names
        .iter()
        .map(|name| std::env::var(name).ok())
        .collect::<Vec<_>>();
    if values.iter().all(Option::is_none) {
        return Ok((NodeIdentityPolicy::TrustedLoopback, None));
    }
    if values.iter().any(Option::is_none) {
        return Err(
            "mTLS requires server cert, server key, client CA and Node certificate digest map"
                .into(),
        );
    }
    let [server_cert, server_key, client_ca, digest_json] = values
        .try_into()
        .expect("four mTLS variables are collected");
    let server_cert = std::fs::read(server_cert.expect("validated mTLS cert path"))?;
    let server_key = std::fs::read(server_key.expect("validated mTLS key path"))?;
    let client_ca = std::fs::read(client_ca.expect("validated mTLS CA path"))?;
    let policy =
        NodeIdentityPolicy::from_json(&digest_json.expect("validated mTLS identity profile"))?;
    let tls = ServerTlsConfig::new()
        .identity(Identity::from_pem(server_cert, server_key))
        .client_ca_root(Certificate::from_pem(client_ca));
    Ok((policy, Some(tls)))
}

/// Parses an optional unsigned duration without silently treating malformed configuration as absent.
fn parsed_duration(name: &str, value: Option<&str>, default: u64) -> Result<u64, String> {
    value
        .map(|value| {
            value
                .parse::<u64>()
                .map_err(|error| format!("{name} must be an unsigned integer: {error}"))
        })
        .transpose()
        .map(|value| value.unwrap_or(default))
}

/// Reads one startup-owned duration; non-Unicode values fail closed without exposing their bytes.
fn configured_duration(name: &str, default: u64) -> Result<u64, Box<dyn std::error::Error>> {
    let value = match std::env::var(name) {
        Ok(value) => Some(value),
        Err(std::env::VarError::NotPresent) => None,
        Err(std::env::VarError::NotUnicode(_)) => {
            return Err(format!("{name} must be UTF-8").into());
        }
    };
    parsed_duration(name, value.as_deref(), default).map_err(Into::into)
}

/// Binds the configured integration listener and keeps accepting connector sessions.
#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error>> {
    // The workspace may enable multiple rustls crypto backends; choose ring before any TLS builder.
    let _ = rustls::crypto::ring::default_provider().install_default();
    let mut arguments = std::env::args().skip(1).peekable();
    if arguments
        .peek()
        .is_some_and(|argument| argument == "--application-schema")
    {
        arguments.next();
        if arguments.next().is_some() {
            return Err("schema inspection accepts no configuration or storage path".into());
        }
        println!("{SERVER_CHECKPOINT_SCHEMA}");
        return Ok(());
    }
    if arguments
        .peek()
        .is_some_and(|argument| argument == "--inspect-checkpoint")
    {
        arguments.next();
        let path = arguments
            .next()
            .ok_or("checkpoint inspection requires one existing database path")?;
        if arguments.next().is_some() {
            return Err("checkpoint inspection accepts exactly one database path".into());
        }
        let saved =
            state::SqliteEventLog::read_checkpoint(path)?.ok_or("database has no checkpoint")?;
        println!(
            "{}",
            serde_json::json!({
                "event_sequence": saved.event_sequence, "schema": saved.schema, "checkpoint_json": saved.checkpoint_json,
            })
        );
        return Ok(());
    }
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
    let physical_entity_registry_path = arguments.next().filter(|path| !path.trim().is_empty());
    if arguments.next().is_some() {
        return Err(
            "unexpected integration-server argument; expected optional actor placement and physical entity registry JSON paths"
                .into(),
        );
    }
    let node_protocol_policy = configured_node_protocol_policy()?;
    let (node_identity_policy, node_tls) = configured_node_identity()?;
    let artifact_tls = artifact_http::configured_artifact_tls()?;
    if node_tls.is_none() && !address.ip().is_loopback() {
        return Err(
            "Node Protocol must remain loopback unless the opt-in mTLS identity profile is enabled"
                .into(),
        );
    }
    let operator_tls_configured = [
        "ROBOGUIDE_OPERATOR_MTLS_SERVER_CERT",
        "ROBOGUIDE_OPERATOR_MTLS_SERVER_KEY",
        "ROBOGUIDE_OPERATOR_MTLS_CLIENT_CA",
        "ROBOGUIDE_OPERATOR_MTLS_ROLE_MAP",
    ]
    .iter()
    .all(|name| std::env::var_os(name).is_some());
    if !http_address.ip().is_loopback() && !operator_tls_configured {
        return Err(
            "operator HTTP must remain loopback unless the opt-in mTLS role profile is enabled"
                .into(),
        );
    }
    require_secure_artifact_binding(artifact_address, artifact_tls.is_some())?;
    let node_lease_policy = node_protocol_policy.lease_policy();
    let fact_batch_limit = configured_duration("ROBOGUIDE_FACT_BATCH_LIMIT", 8)?;
    if !(1..=32).contains(&fact_batch_limit) {
        return Err("ROBOGUIDE_FACT_BATCH_LIMIT must be in 1..32".into());
    }
    let default_batch_wait = if fact_batch_limit == 1 {
        0
    } else {
        2.min(
            node_protocol_policy
                .application_acceptance_timeout_ms()
                .saturating_sub(1),
        )
    };
    let fact_batch_wait = configured_duration("ROBOGUIDE_FACT_BATCH_WAIT_MS", default_batch_wait)?;
    if fact_batch_wait > 5
        || fact_batch_wait >= node_protocol_policy.application_acceptance_timeout_ms()
    {
        return Err(
            "ROBOGUIDE_FACT_BATCH_WAIT_MS must be 0..5 and below the acceptance timeout".into(),
        );
    }
    let fact_batch_wait = Duration::from_millis(fact_batch_wait);
    let actor_placement_constraints = actor_placement_path
        .as_deref()
        .map(|path| load_actor_placement_file(Path::new(path)))
        .transpose()?;
    let physical_entity_registry = physical_entity_registry_path
        .as_deref()
        .map(|path| load_physical_entity_registry_file(Path::new(path)))
        .transpose()?;
    let deployment_feasibility_path =
        std::env::var_os("ROBOGUIDE_DEPLOYMENT_FEASIBILITY_PATH").map(PathBuf::from);
    let mut deployment_feasibility = deployment_feasibility_path
        .as_deref()
        .map(DeploymentFeasibility::load)
        .transpose()
        .map_err(|error| format!("deployment feasibility startup failed: {error}"))?;
    let verifier_feed = match (
        std::env::var_os("ROBOGUIDE_TASK_VERIFIER_SOURCE_PATH"),
        std::env::var_os("ROBOGUIDE_TASK_VERIFIER_VERDICT_PATH"),
    ) {
        (Some(source), Some(verdict)) => Some(Arc::new(TaskVerifierFeed::load(
            Path::new(&source),
            PathBuf::from(verdict),
        )?)),
        (None, None) => None,
        _ => {
            return Err(
                "task verifier source and verdict paths must be configured together".into(),
            );
        }
    };
    let _event_log_writer_lock = acquire_event_log_writer_lock(Path::new(&event_path))?;
    let checkpoint_policy = match std::env::var("ROBOGUIDE_CHECKPOINT_STORAGE").as_deref() {
        Ok("full") | Err(std::env::VarError::NotPresent) => state::CheckpointStoragePolicy::Full,
        Ok("incremental") => state::CheckpointStoragePolicy::Incremental,
        _ => return Err("ROBOGUIDE_CHECKPOINT_STORAGE must be full or incremental".into()),
    };
    let mut event_log =
        state::SqliteEventLog::open_with_checkpoint_policy(&event_path, checkpoint_policy)?;
    let replica_fence =
        controller_replication::ReplicaFenceClient::from_environment()?.map(Arc::new);
    if let Some(fence) = &replica_fence {
        event_log = event_log.with_replicator(fence.clone());
    }
    let event_write_gate = Arc::new(Mutex::new(()));
    let process_clock = Arc::new(runtime::SystemMonotonicClock::from_floor(
        event_log.latest_timestamp()?,
    ));
    let diagnostic_writer = diagnostics::DiagnosticWriter::configured();
    let diagnostic_handle = diagnostic_writer.handle();
    let event_log = event_log.with_diagnostics(diagnostic_handle.clone());
    let artifact_store = artifact_store::FileSystemArtifactStore::new(&artifact_root)?;
    let artifact_catalog =
        artifact_http::ArtifactCatalog::replay_with_gate(&event_log, event_write_gate.clone())
            .map_err(|error| format!("spatial catalog startup replay failed: {error}"))?;
    let artifact_listener = tokio::net::TcpListener::bind(artifact_address).await?;
    let latest_sequence = event_log.latest_sequence()?;
    let checkpoint = event_log.load_checkpoint()?;
    let migrate_checkpoint = checkpoint
        .as_ref()
        .is_some_and(|saved| saved.schema != SERVER_CHECKPOINT_SCHEMA);
    let initialize_checkpoint = checkpoint.is_none() && latest_sequence == 0;
    let initial_preference_path = std::env::var_os("ROBOGUIDE_INITIAL_OPERATION_PREFERENCES_PATH")
        .filter(|path| !path.is_empty());
    let initial_preference_source =
        std::env::var_os("ROBOGUIDE_INITIAL_OPERATION_PREFERENCES_SOURCE_PATH")
            .filter(|path| !path.is_empty());
    if initial_preference_path.is_some() != initial_preference_source.is_some() {
        return Err(
            "initial operation preferences require both projection and original source paths"
                .into(),
        );
    }
    if let (Some(path), Some(source)) = (initial_preference_path, initial_preference_source) {
        deployment_feasibility
            .as_mut()
            .ok_or("initial operation preferences require deployment feasibility")?
            .configure_initial_preferences(
                Path::new(&path),
                Path::new(&source),
                process_clock.now(),
                initialize_checkpoint,
            )
            .map_err(|error| format!("initial operation preference startup failed: {error}"))?;
        if !initialize_checkpoint {
            eprintln!("initial operation preferences disabled after Controller restore");
        }
    }
    match std::env::var("ROBOGUIDE_INITIAL_OPERATION_ASSESSMENT").as_deref() {
        Ok("1") => deployment_feasibility
            .as_mut()
            .ok_or("initial support assessment requires deployment feasibility")?
            .configure_initial_assessment()?,
        Ok("0") | Err(std::env::VarError::NotPresent) => {}
        _ => return Err("ROBOGUIDE_INITIAL_OPERATION_ASSESSMENT must be 0 or 1".into()),
    }
    let deployment_feasibility = deployment_feasibility.map(Arc::new);
    let bootstrap_identity_registry = match &node_identity_policy {
        NodeIdentityPolicy::CertificateLifecycle(registry) => Some(registry.clone()),
        _ => None,
    };
    let (events, mut receiver) = tokio::sync::mpsc::unbounded_channel();
    let (service, router) = GrpcIntegrationService::new_with_observer_and_identity(
        events,
        node_protocol_policy,
        process_clock.clone(),
        diagnostic_handle.clone(),
        node_identity_policy,
    );
    if let Some(fence) = &replica_fence {
        router.set_command_delivery_fence(fence.clone())?;
    }
    let receiver_router = router.clone();
    let memory_session_router = router.clone();
    let mut controller = match checkpoint {
        Some(checkpoint) => {
            if !matches!(
                checkpoint.schema.as_str(),
                SERVER_CHECKPOINT_SCHEMA
                    | PRE_MEMORY_USE_SERVER_CHECKPOINT_SCHEMA
                    | PRE_TYPED_MEMORY_REPLICA_SERVER_CHECKPOINT_SCHEMA
                    | PRE_MEMORY_PREPARATION_SERVER_CHECKPOINT_SCHEMA
                    | PRE_TYPED_MAP_POLICY_SERVER_CHECKPOINT_SCHEMA
                    | PRE_SPATIAL_PROVIDER_SERVER_CHECKPOINT_SCHEMA
                    | PRE_MEMORY_AUTHORITY_SERVER_CHECKPOINT_SCHEMA
                    | PRE_MEMORY_SERVER_CHECKPOINT_SCHEMA
                    | PRE_POLICY_SERVER_CHECKPOINT_SCHEMA
                    | PRE_IDENTITY_SERVER_CHECKPOINT_SCHEMA
                    | PRE_STOP_AUTHORITY_SERVER_CHECKPOINT_SCHEMA
                    | PRE_JOURNAL_SERVER_CHECKPOINT_SCHEMA
                    | PRE_BREAKER_SERVER_CHECKPOINT_SCHEMA
                    | PRE_REPLICATION_SERVER_CHECKPOINT_SCHEMA
                    | PRE_TIMING_SERVER_CHECKPOINT_SCHEMA
                    | PRE_DISPATCH_SERVER_CHECKPOINT_SCHEMA
                    | PRE_ISOLATION_SERVER_CHECKPOINT_SCHEMA
                    | PRE_RECOVERY_SERVER_CHECKPOINT_SCHEMA
                    | ADMISSION_SERVER_CHECKPOINT_SCHEMA
                    | PREVIOUS_SERVER_CHECKPOINT_SCHEMA
                    | VERIFIER_SERVER_CHECKPOINT_SCHEMA
                    | LEGACY_SERVER_CHECKPOINT_SCHEMA
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
                SERVER_CHECKPOINT_SCHEMA
                    | PRE_MEMORY_USE_SERVER_CHECKPOINT_SCHEMA
                    | PRE_TYPED_MEMORY_REPLICA_SERVER_CHECKPOINT_SCHEMA
                    | PRE_MEMORY_PREPARATION_SERVER_CHECKPOINT_SCHEMA
                    | PRE_TYPED_MAP_POLICY_SERVER_CHECKPOINT_SCHEMA
                    | PRE_SPATIAL_PROVIDER_SERVER_CHECKPOINT_SCHEMA
                    | PRE_MEMORY_AUTHORITY_SERVER_CHECKPOINT_SCHEMA
                    | PRE_MEMORY_SERVER_CHECKPOINT_SCHEMA
                    | PRE_POLICY_SERVER_CHECKPOINT_SCHEMA
                    | PRE_IDENTITY_SERVER_CHECKPOINT_SCHEMA
                    | PRE_STOP_AUTHORITY_SERVER_CHECKPOINT_SCHEMA
                    | PRE_JOURNAL_SERVER_CHECKPOINT_SCHEMA
                    | PRE_BREAKER_SERVER_CHECKPOINT_SCHEMA
                    | PRE_REPLICATION_SERVER_CHECKPOINT_SCHEMA
                    | PRE_TIMING_SERVER_CHECKPOINT_SCHEMA
                    | PRE_DISPATCH_SERVER_CHECKPOINT_SCHEMA
                    | PRE_ISOLATION_SERVER_CHECKPOINT_SCHEMA
                    | PRE_RECOVERY_SERVER_CHECKPOINT_SCHEMA
                    | ADMISSION_SERVER_CHECKPOINT_SCHEMA
                    | PREVIOUS_SERVER_CHECKPOINT_SCHEMA
                    | VERIFIER_SERVER_CHECKPOINT_SCHEMA
                    | LEGACY_SERVER_CHECKPOINT_SCHEMA
            ) {
                return Err(format!(
                    "controller checkpoint body uses unsupported schema {}",
                    saved.schema
                )
                .into());
            }
            let configured_verifier_digest = verifier_feed
                .as_ref()
                .map(|feed| feed.source_digest().to_string());
            validate_restored_verifier_source(&saved, configured_verifier_digest.as_deref())?;
            validate_replication_checkpoint(&saved, event_log.replication_profile())?;
            application::validate_isolation_checkpoint_schema(&saved)?;
            ControllerState {
                mission_admissions: saved.mission_admissions,
                memory_imports: saved.memory_imports,
                memory_preparation: saved.memory_preparation,
                memory_use: saved.memory_use,
                safety_stops: saved.safety_stops,
                bridge: IntegrationRuntimeBridge::restore_from_checkpoint_with_lease_policy(
                    &saved.integration_json,
                    event_log.clone(),
                    router,
                    process_clock.now(),
                    node_lease_policy,
                )?,
                orchestrator: MissionOrchestrator::restore_json(&saved.orchestration_json)?,
                verifier_seen: saved.verifier_seen,
                verifier_artifact_store: Some(artifact_store.clone()),
                verifier_source_digest: configured_verifier_digest,
            }
        }
        None if latest_sequence > 0 => {
            return Err(format!(
                "controller database {event_path} contains events but no controller checkpoint; refusing to start with empty authority"
            )
            .into());
        }
        None => ControllerState {
            mission_admissions: BTreeMap::new(),
            memory_imports: BTreeMap::new(),
            memory_preparation: Default::default(),
            memory_use: Default::default(),
            safety_stops: BTreeMap::new(),
            bridge: IntegrationRuntimeBridge::new_with_lease_policy(
                control::ControlPlane::new(),
                state::InMemorySharedNodeState::new(),
                event_log.clone(),
                router,
                node_lease_policy,
            ),
            orchestrator: MissionOrchestrator::new(),
            verifier_seen: BTreeSet::new(),
            verifier_artifact_store: Some(artifact_store.clone()),
            verifier_source_digest: verifier_feed
                .as_ref()
                .map(|feed| feed.source_digest().to_string()),
        },
    };
    controller.bridge.set_diagnostics(diagnostic_handle.clone());
    let installed_identity_registry = match (
        bootstrap_identity_registry,
        controller.bridge.control().node_identity_registry(),
    ) {
        (Some(configured), Some(saved)) if configured.registry_id == saved.registry_id => false,
        (Some(configured), None) => {
            controller
                .bridge
                .control_mut()
                .bootstrap_node_identity_registry(configured)?;
            true
        }
        (None, None) => false,
        _ => {
            return Err(
                "restored Node identity registry requires its original lifecycle mTLS profile"
                    .into(),
            );
        }
    };
    let restored_stop_holds = application::safety_stop::validate_restored(&controller)?;
    application::attestation::validate_restored(&controller)?;
    controller
        .bridge
        .control()
        .validate_mission_tenant_authority()?;
    application::memory_import::validate_restored(&controller)?;
    let restored_memory_fences =
        application::memory_preparation::restore(&mut controller, process_clock.now())?;
    let restored_use_fences =
        application::memory_use::restore(&mut controller, process_clock.now())?;
    controller
        .orchestrator
        .set_admission_limits(orchestration::MissionAdmissionLimits::try_new(
            usize::try_from(configured_duration("ROBOGUIDE_MAX_ADMITTED_MISSIONS", 128)?)?,
            usize::try_from(configured_duration(
                "ROBOGUIDE_MAX_ADMITTED_ROLE_SLOTS",
                4096,
            )?)?,
        )?);
    for (mission_id, admission) in &controller.mission_admissions {
        admission.validate(&controller, mission_id)?;
    }
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
    if let Some(snapshot) = physical_entity_registry {
        controller
            .bridge
            .control_mut()
            .install_physical_entity_registry(snapshot)?;
    }
    controller
        .bridge
        .control()
        .validate_physical_entity_registry_on_restore()?;
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
    for restriction in controller.bridge.control().actor_candidate_restrictions() {
        if deployment_feasibility
            .as_ref()
            .is_none_or(|snapshot| snapshot.digest() != restriction.evidence_digest())
        {
            return Err(
                "restored actor candidate restriction lacks its original deployment evidence"
                    .into(),
            );
        }
    }
    if initialize_checkpoint
        || migrate_checkpoint
        || restored_stop_holds
        || restored_memory_fences
        || restored_use_fences
        || installed_identity_registry
        || actor_placement_path.is_some()
        || physical_entity_registry_path.is_some()
        || restored_localization_evidence
    {
        event_log.begin_batch()?;
        if let Err(error) = application::safety_stop::restore_holds(&mut controller) {
            let _ = event_log.rollback_batch();
            return Err(error.into());
        }
        let checkpoint_json = match server_checkpoint_json(&controller) {
            Ok(checkpoint) => checkpoint,
            Err(error) => {
                let _ = event_log.rollback_batch();
                return Err(error.to_string().into());
            }
        };
        if let Err(error) = application::save_controller_checkpoint(&event_log, &checkpoint_json) {
            let _ = event_log.rollback_batch();
            return Err(error.into());
        }
        if let Err(error) = event_log.commit_batch() {
            let _ = event_log.rollback_batch();
            return Err(error.into());
        }
    }
    controller.memory_preparation.catalog = Some(artifact_catalog.clone());
    controller.bridge.publish_node_identity_registry()?;
    let controller = Arc::new(Mutex::new(controller));
    application::state_policy::bootstrap_artifact(
        &controller,
        &event_log,
        &event_write_gate,
        artifact_tls
            .as_ref()
            .and_then(|profile| profile.authorization_registry()),
        artifact_tls.is_some(),
        process_clock.now(),
    )?;
    let http_event_log = event_log.clone();
    let http_controller = controller.clone();
    let http_deployment_feasibility = deployment_feasibility.clone();
    let http_verifier_feed = verifier_feed.clone();
    let http_event_write_gate = event_write_gate.clone();
    let http_clock = process_clock.clone();
    let receiver_event_log = event_log.clone();
    let receiver_event_write_gate = event_write_gate.clone();
    let receiver_clock = process_clock.clone();
    let timer_controller = Arc::clone(&controller);
    let timer_verifier_feed = verifier_feed.clone();
    let timer_event_log = event_log.clone();
    let timer_event_write_gate = event_write_gate.clone();
    let timer_clock = process_clock.clone();
    let artifact_catalog_for_server = artifact_catalog.clone();
    let artifact_store_for_server = artifact_store.clone();
    let memory_admission: Arc<dyn artifact_http::MemoryProviderAdmission> =
        Arc::new(ControllerMemoryAdmission {
            clock: Arc::clone(&process_clock),
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
    if let Some(fence) = replica_fence {
        let watchdog_fatal = fatal_sender.clone();
        tokio::spawn(async move {
            let mut timer = tokio::time::interval(Duration::from_millis(200));
            timer.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
            loop {
                timer.tick().await;
                let probe = fence.clone();
                let result = tokio::task::spawn_blocking(move || probe.check()).await;
                let failure = match result {
                    Ok(Ok(())) => None,
                    Ok(Err(error)) => Some(error),
                    Err(error) => Some(error.to_string()),
                };
                if let Some(error) = failure {
                    let _ = watchdog_fatal.send(format!(
                        "Controller original replica epoch/quorum lost: {error}"
                    ));
                    return;
                }
            }
        });
    }
    let timer_fatal_sender = fatal_sender.clone();
    let http_fatal_sender = fatal_sender.clone();
    let artifact_fatal_sender = fatal_sender.clone();
    let artifact_tls_for_server = artifact_tls.clone();
    let timer_diagnostics = diagnostic_handle.clone();
    let receiver_diagnostics = diagnostic_handle.clone();
    tokio::spawn(async move {
        if let Err(error) = serve_http(
            http_address,
            http_controller,
            http_event_log,
            http_event_write_gate,
            http_clock,
            http_deployment_feasibility,
            http_verifier_feed,
        )
        .await
        {
            let _ = http_fatal_sender.send(format!("control HTTP server stopped: {error}"));
        }
    });
    tokio::spawn(async move {
        if let Err(error) = artifact_http::serve_artifact_http(
            artifact_listener,
            artifact_store_for_server,
            artifact_catalog_for_server,
            memory_admission,
            localization_observer,
            artifact_tls_for_server,
        )
        .await
        {
            let _ = artifact_fatal_sender.send(format!("artifact HTTP server stopped: {error}"));
        }
    });
    tokio::spawn(async move {
        let mut interval = tokio::time::interval(Duration::from_secs(1));
        interval.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
        loop {
            let scheduled = interval.tick().await;
            timer_diagnostics.timing(
                DiagnosticKind::Timer,
                DiagnosticStage::TimerLag,
                scheduled.elapsed(),
                None,
            );
            if let Err(error) = drive_application_timer_with_clock(
                &timer_controller,
                &timer_event_log,
                &timer_event_write_gate,
                timer_clock.as_ref(),
                timer_verifier_feed.as_deref(),
            ) {
                let reason = format!("application timer stopped: {error}");
                let _ = timer_fatal_sender.send(reason);
                return;
            }
        }
    });
    tokio::spawn(async move {
        let mut barrier = None;
        loop {
            let first = match barrier.take() {
                Some(delivery) => delivery,
                None => match receiver.recv().await {
                    Some(delivery) => delivery,
                    None => return,
                },
            };
            let (deliveries, next_barrier) = collect_fact_batch(
                first,
                &mut receiver,
                fact_batch_limit as usize,
                fact_batch_wait,
            )
            .await;
            barrier = next_barrier;
            let driver = NodeFactDriver {
                controller: &controller,
                events: &receiver_event_log,
                write_gate: &receiver_event_write_gate,
                router: &receiver_router,
                clock: receiver_clock.as_ref(),
                diagnostics: &receiver_diagnostics,
            };
            if let Err(error) = driver.run(deliveries) {
                let _ = fatal_sender.send(format!("durable Node fact driver failed: {error}"));
                return;
            }
        }
    });
    let server = if let Some(tls) = node_tls {
        tonic::transport::Server::builder()
            .tls_config(tls)?
            .add_service(LegacyRoboGuideNodeProtocolServer::new(GrpcLegacyV02Service))
            .add_service(RoboGuideNodeProtocolServer::new(service))
            .serve(address)
    } else {
        tonic::transport::Server::builder()
            .add_service(LegacyRoboGuideNodeProtocolServer::new(GrpcLegacyV02Service))
            .add_service(RoboGuideNodeProtocolServer::new(service))
            .serve(address)
    };
    let outcome = tokio::select! {
        result = server => result.map_err(Into::into),
        fatal = fatal_receiver.recv() => Err(fatal.unwrap_or_else(|| "application driver stopped unexpectedly".to_string()).into()),
    };
    let finishing = tokio::task::spawn_blocking(move || diagnostic_writer.finish());
    let _ = tokio::time::timeout(Duration::from_secs(1), finishing).await;
    outcome
}

use application::*;
use controller_http::*;

#[cfg(test)]
mod tests;
