//! Independent streaming HTTP data plane for immutable Spatial Memory artifacts.
//!
//! This module owns HTTP parsing and transport only.  The Artifact Store owns bytes, while the
//! `MapCatalogProjection` owns rebuildable manifest/replica metadata.  No endpoint starts a
//! mission, mutates a TaskExecution, or selects an active map.

use artifact_store::{ArtifactStoreError as CasError, ArtifactUpload, FileSystemArtifactStore};
use domain::{
    EventPayload, MapArtifactManifest, MapRevisionSelector, MemoryArtifactManifest, MemoryId,
    MemoryOwner, MemoryRevisionId, MemorySelector, MissionId, NodeId, SpatialAnchorId, TimestampMs,
};
use ports::{
    EventSink, MapCatalogReader, MapCatalogWriter, MemoryCatalogReader, MemoryCatalogWriter,
};
use serde::Deserialize;
use state::{MapCatalogProjection, MemoryCatalogProjection, PersistedCheckpoint, SqliteEventLog};
use std::collections::BTreeMap;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};
use tokio::time::{MissedTickBehavior, interval, timeout};

/// Maximum HTTP header block accepted by the artifact listener.
const MAX_HEADER_BYTES: usize = 64 * 1024;
/// Maximum JSON request body accepted by catalog and upload-control endpoints.
const MAX_JSON_BODY_BYTES: u64 = 1024 * 1024;
/// Maximum artifact body accepted by this v0 listener.
const MAX_ARTIFACT_BYTES: u64 = 4 * 1024 * 1024 * 1024;
/// Maximum number of upload identities, including requests currently streaming or finalizing.
const MAX_ACTIVE_UPLOADS: usize = 32;
/// Maximum aggregate staged and reserved bytes across all active upload identities.
const MAX_ACTIVE_UPLOAD_BYTES: u64 = 8 * 1024 * 1024 * 1024;
/// Maximum idle time between successful mutations of one staged upload.
const UPLOAD_IDLE_TTL: Duration = Duration::from_secs(15 * 60);
/// Maximum wall time allowed for one declared artifact request body to arrive.
const UPLOAD_BODY_TIMEOUT: Duration = Duration::from_secs(5 * 60);
/// Frequency at which idle sessions are removed even when no HTTP request arrives.
const UPLOAD_SWEEP_INTERVAL: Duration = Duration::from_secs(60);

/// Composition-owned admission for Node-authored typed evidence and generic Memory claims.
pub trait MemoryProviderAdmission: Send + Sync {
    /// Validates that one manifest is covered by a current provider declaration.
    fn admit_manifest(&self, manifest: &MemoryArtifactManifest) -> Result<(), String>;

    /// Validates that one replica reporter has the named compatible consumer provider.
    fn admit_replica(
        &self,
        node_id: &NodeId,
        consumer_provider_id: &str,
        manifest: &MemoryArtifactManifest,
    ) -> Result<(), String>;

    /// Validates that one write was issued by the active session of its semantic Node owner.
    fn admit_publisher(
        &self,
        publisher: Option<&MemoryPublicationIdentity>,
        expected_node_id: &NodeId,
    ) -> Result<(), String>;

    /// Validates that typed localization evidence names the current physical attempt and owner.
    fn admit_localization_evidence(
        &self,
        evidence: &domain::LocalizationVerificationEvidence,
    ) -> Result<(), String>;
}

/// Composition callback that projects durable strong localization evidence into Runtime.
pub trait LocalizationEvidenceObserver: Send + Sync {
    /// Applies one already-persisted evidence record using its RoboGuide-local receive time.
    fn observe(
        &self,
        evidence: &domain::LocalizationVerificationEvidence,
        received_at: TimestampMs,
    ) -> Result<(), String>;
}

/// Node/session identity attached to one Node-authored mutation on the internal data plane.
#[derive(Debug, Clone)]
pub struct MemoryPublicationIdentity {
    /// Stable Node identity expected to own the current gRPC route.
    node_id: NodeId,
    /// Current session identity issued after Controller registration acceptance.
    session_id: String,
}

/// Admission check serialized with one durable catalog/evidence append.
enum EvidenceWriteAdmission<'a> {
    /// Validate the provider, semantic owner, and publishing session for generic Memory.
    Memory {
        /// Composition-owned provider and session authority.
        authority: &'a dyn MemoryProviderAdmission,
        /// Node/session identity parsed from the request.
        publisher: Option<&'a MemoryPublicationIdentity>,
    },
    /// Validate only the current Node/session identity for typed Node-authored evidence.
    NodePublisher {
        /// Composition-owned session authority.
        authority: &'a dyn MemoryProviderAdmission,
        /// Node/session identity parsed from the request.
        publisher: Option<&'a MemoryPublicationIdentity>,
        /// Semantic Node owner carried by the typed evidence.
        expected_node_id: &'a NodeId,
        /// Typed evidence whose execution provenance must still be current.
        evidence: &'a domain::LocalizationVerificationEvidence,
    },
}

impl MemoryPublicationIdentity {
    /// Returns the publishing Node identity.
    pub const fn node_id(&self) -> &NodeId {
        &self.node_id
    }

    /// Returns the publishing Node's current session identity.
    pub fn session_id(&self) -> &str {
        &self.session_id
    }
}

/// Shared catalog and evidence-log authority used by the artifact listener.
#[derive(Clone)]
pub struct ArtifactCatalog {
    /// Rebuildable metadata projection guarded for concurrent HTTP requests.
    projection: Arc<Mutex<MapCatalogProjection>>,
    /// Generic non-map Memory metadata sharing the same evidence log and CAS.
    memory_projection: Arc<Mutex<MemoryCatalogProjection>>,
    /// Durable evidence sink used for map lifecycle events.
    event_log: SqliteEventLog,
    /// Process-local serializer shared with the controller's event batches.
    write_gate: Arc<Mutex<()>>,
    /// Last event timestamp allocated under the shared write serializer.
    timestamp_high_water: Arc<Mutex<TimestampMs>>,
    /// Process-local recovery fence set when a durable catalog commit outcome is uncertain.
    recovery_fence: Arc<Mutex<Option<String>>>,
}

impl ArtifactCatalog {
    /// Replays the catalog while sharing a process-local event-log write serializer.
    pub fn replay_with_gate(
        event_log: &SqliteEventLog,
        write_gate: Arc<Mutex<()>>,
    ) -> Result<Self, Box<dyn std::error::Error + Send + Sync>> {
        let mut projection = MapCatalogProjection::new();
        let mut memory_projection = MemoryCatalogProjection::new();
        let mut timestamp_high_water = TimestampMs::new(0);
        for event in event_log.decoded_events()? {
            timestamp_high_water = timestamp_high_water.max(event.timestamp());
            if let Err(error) = projection.apply_event(&event) {
                return Err(format!("spatial catalog replay failed: {error}").into());
            }
            if matches!(
                event.payload(),
                EventPayload::MemoryManifestPublished { .. }
                    | EventPayload::MemoryArtifactStaged { .. }
                    | EventPayload::MemoryArtifactImported { .. }
                    | EventPayload::MemoryArtifactRejected { .. }
            ) && let Err(error) = memory_projection.apply_memory_event(&event)
            {
                return Err(format!("generic Memory catalog replay failed: {error}").into());
            }
        }
        validate_disjoint_memory_namespace(&projection, &memory_projection)
            .map_err(|error| format!("Memory selector replay failed: {error}"))?;
        Ok(Self {
            projection: Arc::new(Mutex::new(projection)),
            memory_projection: Arc::new(Mutex::new(memory_projection)),
            event_log: event_log.clone(),
            write_gate,
            timestamp_high_water: Arc::new(Mutex::new(timestamp_high_water)),
            recovery_fence: Arc::new(Mutex::new(None)),
        })
    }

    /// Rejects catalog access after an uncertain durable commit until the process is restarted.
    fn ensure_available(&self) -> Result<(), HttpError> {
        let fence = self.recovery_fence.lock().map_err(|_| {
            HttpError::service_unavailable("spatial catalog recovery fence is unavailable")
        })?;
        if let Some(reason) = fence.as_deref() {
            return Err(HttpError::service_unavailable(format!(
                "spatial catalog requires restart for recovery: {reason}"
            )));
        }
        Ok(())
    }

    /// Fences this catalog and returns a retryable service-unavailable response.
    fn fence(&self, reason: impl Into<String>) -> HttpError {
        let reason = reason.into();
        match self.recovery_fence.lock() {
            Ok(mut fence) => {
                if fence.is_none() {
                    *fence = Some(reason.clone());
                }
                HttpError::service_unavailable(format!(
                    "spatial catalog requires restart for recovery: {reason}"
                ))
            }
            Err(_) => HttpError::service_unavailable(
                "spatial catalog recovery fence is unavailable; restart required",
            ),
        }
    }

    /// Rolls back a failed pre-commit batch or fences when rollback itself is inconclusive.
    fn rollback_failed_batch(&self, log: &SqliteEventLog, reason: String) -> HttpError {
        match log.rollback_batch() {
            Ok(()) => HttpError::internal(reason),
            Err(rollback) => self.fence(format!(
                "{reason}; rollback outcome is uncertain: {rollback}"
            )),
        }
    }

    /// Reads one revision snapshot without exposing mutable State to the transport layer.
    fn revision(
        &self,
        selector: &MapRevisionSelector,
    ) -> Result<Option<domain::MapRevisionSnapshot>, HttpError> {
        self.ensure_available()?;
        let projection = self
            .projection
            .lock()
            .map_err(|_| HttpError::internal("spatial catalog lock is poisoned"))?;
        Ok(projection.revision(selector))
    }

    /// Reads all revisions in deterministic order for the catalog endpoint.
    fn revisions(&self) -> Result<Vec<domain::MapRevisionSnapshot>, HttpError> {
        self.ensure_available()?;
        let projection = self
            .projection
            .lock()
            .map_err(|_| HttpError::internal("spatial catalog lock is poisoned"))?;
        Ok(projection.revisions())
    }

    /// Reads typed map replica evidence in deterministic node order.
    fn map_replicas(
        &self,
        selector: &MapRevisionSelector,
    ) -> Result<Vec<domain::MapReplicaSnapshot>, HttpError> {
        self.ensure_available()?;
        let projection = self
            .projection
            .lock()
            .map_err(|_| HttpError::internal("spatial catalog lock is poisoned"))?;
        Ok(projection.replicas(selector))
    }

    /// Reads every generic Memory manifest in deterministic selector order.
    fn memories(&self) -> Result<Vec<MemoryArtifactManifest>, HttpError> {
        self.ensure_available()?;
        self.memory_projection
            .lock()
            .map_err(|_| HttpError::internal("generic Memory catalog lock is poisoned"))
            .map(|projection| projection.memories())
    }

    /// Reads one generic Memory manifest without exposing catalog mutation.
    fn memory(
        &self,
        selector: &MemorySelector,
    ) -> Result<Option<MemoryArtifactManifest>, HttpError> {
        self.ensure_available()?;
        self.memory_projection
            .lock()
            .map_err(|_| HttpError::internal("generic Memory catalog lock is poisoned"))
            .map(|projection| projection.memory(selector))
    }

    /// Reads generic Memory replica evidence in deterministic node/provider order.
    fn memory_replicas(
        &self,
        selector: &MemorySelector,
    ) -> Result<Vec<domain::MemoryReplicaSnapshot>, HttpError> {
        self.ensure_available()?;
        self.memory_projection
            .lock()
            .map_err(|_| HttpError::internal("generic Memory catalog lock is poisoned"))
            .map(|projection| projection.memory_replicas(selector))
    }

    /// Appends and projects one map event atomically from the HTTP caller's perspective.
    fn append(&self, payload: EventPayload) -> Result<TimestampMs, HttpError> {
        self.append_with_admission(payload, None)
    }

    /// Appends one generic Memory event after admission under the shared Controller write gate.
    fn append_memory(
        &self,
        payload: EventPayload,
        admission: &dyn MemoryProviderAdmission,
        publisher: Option<&MemoryPublicationIdentity>,
    ) -> Result<(), HttpError> {
        self.append_with_admission(
            payload,
            Some(EvidenceWriteAdmission::Memory {
                authority: admission,
                publisher,
            }),
        )
        .map(|_| ())
    }

    /// Appends typed Node-authored evidence under an atomic current-session admission check.
    fn append_node_evidence(
        &self,
        payload: EventPayload,
        admission: &dyn MemoryProviderAdmission,
        publisher: Option<&MemoryPublicationIdentity>,
        evidence: &domain::LocalizationVerificationEvidence,
    ) -> Result<TimestampMs, HttpError> {
        self.append_with_admission(
            payload,
            Some(EvidenceWriteAdmission::NodePublisher {
                authority: admission,
                publisher,
                expected_node_id: evidence.node_id(),
                evidence,
            }),
        )
    }

    /// Serializes optional Memory admission with Controller registration updates and persistence.
    fn append_with_admission(
        &self,
        payload: EventPayload,
        admission: Option<EvidenceWriteAdmission<'_>>,
    ) -> Result<TimestampMs, HttpError> {
        let _write_guard = self
            .write_gate
            .lock()
            .map_err(|_| HttpError::internal("event-log write gate is poisoned"))?;
        self.ensure_available()?;
        if let Some(admission) = admission {
            match admission {
                EvidenceWriteAdmission::Memory {
                    authority,
                    publisher,
                } => admit_memory_payload(&payload, authority, publisher)?,
                EvidenceWriteAdmission::NodePublisher {
                    authority,
                    publisher,
                    expected_node_id,
                    evidence,
                } => {
                    authority
                        .admit_publisher(publisher, expected_node_id)
                        .map_err(HttpError::forbidden)?;
                    authority
                        .admit_localization_evidence(evidence)
                        .map_err(HttpError::forbidden)?;
                }
            }
        }
        let timestamp = self.next_timestamp()?;
        let mut projection = self
            .projection
            .lock()
            .map_err(|_| HttpError::internal("spatial catalog lock is poisoned"))?;
        let mut candidate = projection.clone();
        candidate
            .apply_payload(timestamp, &payload)
            .map_err(|error| HttpError::conflict(error.to_string()))?;
        let mut memory_projection = self
            .memory_projection
            .lock()
            .map_err(|_| HttpError::internal("generic Memory catalog lock is poisoned"))?;
        let mut memory_candidate = memory_projection.clone();
        if matches!(
            &payload,
            EventPayload::MemoryManifestPublished { .. }
                | EventPayload::MemoryArtifactStaged { .. }
                | EventPayload::MemoryArtifactImported { .. }
                | EventPayload::MemoryArtifactRejected { .. }
        ) {
            memory_candidate
                .apply_memory_payload(timestamp, &payload)
                .map_err(|error| HttpError::conflict(error.to_string()))?;
        }
        validate_disjoint_memory_namespace(&candidate, &memory_candidate)
            .map_err(HttpError::conflict)?;
        let correlation = domain::CorrelationId::new("spatial-artifact-http")
            .map_err(|error| HttpError::internal(error.to_string()))?;
        let mut log = self.event_log.clone();
        let checkpoint = current_checkpoint(&log)?;
        log.begin_batch()
            .map_err(|error| HttpError::conflict(format!("spatial catalog is busy: {error}")))?;
        log.append(timestamp, &correlation, None, payload);
        match log.take_error() {
            Ok(Some(error)) => {
                return Err(self.rollback_failed_batch(
                    &log,
                    format!("durable spatial evidence failed: {error}"),
                ));
            }
            Ok(None) => {}
            Err(error) => {
                let rollback = log.rollback_batch();
                let rollback_detail = rollback
                    .err()
                    .map(|rollback| format!("; rollback outcome is uncertain: {rollback}"))
                    .unwrap_or_default();
                return Err(self.fence(format!(
                    "durable spatial evidence health is unavailable: {error}{rollback_detail}"
                )));
            }
        }
        if let Err(error) = log.save_checkpoint(&checkpoint.schema, &checkpoint.checkpoint_json) {
            return Err(self.rollback_failed_batch(
                &log,
                format!("controller checkpoint carry-forward failed: {error}"),
            ));
        }
        if let Err(error) = log.commit_batch() {
            let rollback = log.rollback_batch();
            let rollback_detail = rollback
                .err()
                .map(|rollback| format!("; rollback outcome is uncertain: {rollback}"))
                .unwrap_or_default();
            return Err(self.fence(format!(
                "durable spatial evidence commit outcome is uncertain: {error}{rollback_detail}"
            )));
        }
        *projection = candidate;
        *memory_projection = memory_candidate;
        Ok(timestamp)
    }

    /// Returns durable strong localization evidence in event order for restart rehydration.
    pub fn localization_evidence(
        &self,
    ) -> Result<Vec<(domain::LocalizationVerificationEvidence, TimestampMs)>, String> {
        self.ensure_available().map_err(|error| error.message)?;
        self.event_log
            .decoded_events()
            .map_err(|error| error.to_string())
            .map(|events| {
                events
                    .into_iter()
                    .filter_map(|event| match event.payload() {
                        EventPayload::MapLocalizationEvidenceRecorded { evidence } => {
                            Some((evidence.clone(), event.timestamp()))
                        }
                        _ => None,
                    })
                    .collect()
            })
    }

    /// Allocates one process-local receive timestamp monotonically under the durable write gate.
    fn next_timestamp(&self) -> Result<TimestampMs, HttpError> {
        let wall_clock = receive_timestamp().as_millis();
        let mut high_water = self
            .timestamp_high_water
            .lock()
            .map_err(|_| HttpError::internal("spatial timestamp lock is poisoned"))?;
        let next = wall_clock.max(high_water.as_millis().saturating_add(1));
        *high_water = TimestampMs::new(next);
        Ok(*high_water)
    }
}

/// Admits one generic Memory payload while the caller holds the shared event write gate.
fn admit_memory_payload(
    payload: &EventPayload,
    admission: &dyn MemoryProviderAdmission,
    publisher: Option<&MemoryPublicationIdentity>,
) -> Result<(), HttpError> {
    match payload {
        EventPayload::MemoryManifestPublished { manifest } => {
            admission
                .admit_manifest(manifest)
                .map_err(HttpError::forbidden)?;
            let MemoryOwner::Node { node_id, .. } = manifest.owner() else {
                return Err(HttpError::forbidden(
                    "public Memory publication requires a Node-owned manifest",
                ));
            };
            admission
                .admit_publisher(publisher, node_id)
                .map_err(HttpError::forbidden)
        }
        EventPayload::MemoryArtifactStaged {
            manifest,
            node_id,
            consumer_provider_id,
        }
        | EventPayload::MemoryArtifactImported {
            manifest,
            node_id,
            consumer_provider_id,
        }
        | EventPayload::MemoryArtifactRejected {
            manifest,
            node_id,
            consumer_provider_id,
            ..
        } => {
            admission
                .admit_replica(node_id, consumer_provider_id, manifest)
                .map_err(HttpError::forbidden)?;
            admission
                .admit_publisher(publisher, node_id)
                .map_err(HttpError::forbidden)
        }
        _ => Err(HttpError::internal(
            "Memory admission was requested for unrelated evidence",
        )),
    }
}

/// Rejects selectors that would resolve to both a typed map and a generic Memory revision.
fn validate_disjoint_memory_namespace(
    maps: &MapCatalogProjection,
    memories: &MemoryCatalogProjection,
) -> Result<(), String> {
    let generic = memories
        .memories()
        .into_iter()
        .map(|manifest| {
            (
                manifest.selector().memory_id().as_str().to_string(),
                manifest.selector().revision_id().as_str().to_string(),
            )
        })
        .collect::<std::collections::BTreeSet<_>>();
    for revision in maps.revisions() {
        let selector = revision.manifest().selector();
        let key = (
            selector.map_id().as_str().to_string(),
            selector.revision_id().as_str().to_string(),
        );
        if generic.contains(&key) {
            return Err(format!(
                "selector {}/{} is already owned by the other Memory catalog",
                key.0, key.1
            ));
        }
    }
    Ok(())
}

/// Loads the complete controller checkpoint represented by the current durable log head.
///
/// Spatial evidence may advance the shared event sequence, but it cannot synthesize or repair
/// Control/Runtime authority. Missing or already-divergent checkpoints therefore fail closed
/// before an event batch is opened.
fn current_checkpoint(log: &SqliteEventLog) -> Result<PersistedCheckpoint, HttpError> {
    let latest_sequence = log
        .latest_sequence()
        .map_err(|error| HttpError::internal(format!("read event-log head: {error}")))?;
    let checkpoint = log
        .load_checkpoint()
        .map_err(|error| HttpError::internal(format!("read controller checkpoint: {error}")))?
        .ok_or_else(|| {
            HttpError::internal(
                "controller checkpoint is unavailable; refusing spatial evidence append",
            )
        })?;
    if checkpoint.event_sequence != latest_sequence {
        return Err(HttpError::internal(format!(
            "controller checkpoint is at event {} but log ends at {latest_sequence}; refusing spatial evidence append",
            checkpoint.event_sequence
        )));
    }
    Ok(checkpoint)
}

/// One staged upload plus the last successful HTTP mutation time.
struct UploadSession {
    /// CAS handle owning the incomplete staging file.
    upload: ArtifactUpload,
    /// Monotonic process-local activity time used only for idle expiration.
    last_activity: Instant,
}

/// Upload temporarily removed from the registry while one request mutates it.
struct InFlightUpload {
    /// Session unavailable to concurrent requests until this operation completes.
    session: UploadSession,
    /// Bytes reserved in the registry for this upload, including the incoming body.
    accounted_bytes: u64,
}

/// Bounded process-local registry for incomplete HTTP uploads.
struct UploadRegistry {
    /// Idle sessions keyed by safe caller-generated IDs.
    sessions: BTreeMap<String, UploadSession>,
    /// Requests currently streaming, finalizing, or aborting an extracted session.
    in_flight_count: usize,
    /// Staged bytes plus declared bytes reserved by active streaming requests.
    active_bytes: u64,
    /// Hard count quota applied before a staging file is created.
    max_uploads: usize,
    /// Hard aggregate byte quota applied before a body is streamed.
    max_bytes: u64,
    /// Idle duration after which a staged session is explicitly aborted.
    idle_ttl: Duration,
}

/// Shared bounded upload registry.
type Uploads = Arc<Mutex<UploadRegistry>>;

impl UploadRegistry {
    /// Creates the production registry with fixed v0 resource limits.
    fn production() -> Self {
        Self::with_limits(MAX_ACTIVE_UPLOADS, MAX_ACTIVE_UPLOAD_BYTES, UPLOAD_IDLE_TTL)
    }

    /// Creates an empty registry with explicit limits for deterministic policy tests.
    fn with_limits(max_uploads: usize, max_bytes: u64, idle_ttl: Duration) -> Self {
        Self {
            sessions: BTreeMap::new(),
            in_flight_count: 0,
            active_bytes: 0,
            max_uploads,
            max_bytes,
            idle_ttl,
        }
    }

    /// Returns the number of idle and in-flight upload identities consuming quota.
    fn active_uploads(&self) -> usize {
        self.sessions.len().saturating_add(self.in_flight_count)
    }

    /// Aborts sessions whose idle deadline has elapsed and releases their byte accounting.
    fn expire_idle(&mut self, now: Instant) -> Result<usize, HttpError> {
        let expired = self
            .sessions
            .iter()
            .filter(|(_, session)| {
                now.saturating_duration_since(session.last_activity) >= self.idle_ttl
            })
            .map(|(upload_id, _)| upload_id.clone())
            .collect::<Vec<_>>();
        let mut first_error = None;
        for upload_id in &expired {
            let Some(mut session) = self.sessions.remove(upload_id) else {
                continue;
            };
            self.active_bytes = self.active_bytes.saturating_sub(session.upload.size());
            if let Err(error) = session.upload.abort()
                && first_error.is_none()
            {
                first_error = Some(map_cas_error(error));
            }
        }
        if let Some(error) = first_error {
            Err(error)
        } else {
            Ok(expired.len())
        }
    }

    /// Checks count/identity limits and inserts a newly created zero-byte CAS upload.
    fn insert_new(
        &mut self,
        upload_id: String,
        upload: ArtifactUpload,
        now: Instant,
    ) -> Result<(), HttpError> {
        if self.active_uploads() >= self.max_uploads {
            return Err(HttpError::resource_exhausted(
                "active artifact upload count exceeds limit",
            ));
        }
        if self.sessions.contains_key(&upload_id) {
            return Err(HttpError::conflict("artifact upload id is already active"));
        }
        self.sessions.insert(
            upload_id,
            UploadSession {
                upload,
                last_activity: now,
            },
        );
        Ok(())
    }

    /// Extracts one session and reserves its declared incoming bytes atomically.
    fn take_for_append(
        &mut self,
        upload_id: &str,
        incoming_bytes: u64,
    ) -> Result<InFlightUpload, HttpError> {
        let session = self
            .sessions
            .get(upload_id)
            .ok_or_else(|| HttpError::not_found("unknown artifact upload"))?;
        let upload_size = session.upload.size();
        let projected_upload = upload_size
            .checked_add(incoming_bytes)
            .ok_or_else(|| HttpError::too_large("artifact size overflows v0 limit"))?;
        if projected_upload > MAX_ARTIFACT_BYTES {
            return Err(HttpError::too_large("artifact exceeds v0 size limit"));
        }
        let projected_total = self
            .active_bytes
            .checked_add(incoming_bytes)
            .ok_or_else(|| HttpError::too_large("active artifact bytes overflow quota"))?;
        if projected_total > self.max_bytes {
            return Err(HttpError::too_large(
                "active artifact upload bytes exceed quota",
            ));
        }
        let session = self
            .sessions
            .remove(upload_id)
            .expect("checked upload session remains under registry lock");
        self.active_bytes = projected_total;
        self.in_flight_count = self.in_flight_count.saturating_add(1);
        Ok(InFlightUpload {
            session,
            accounted_bytes: projected_upload,
        })
    }

    /// Extracts a session for a terminal finalize or explicit abort operation.
    fn take_for_terminal(&mut self, upload_id: &str) -> Result<InFlightUpload, HttpError> {
        let session = self
            .sessions
            .remove(upload_id)
            .ok_or_else(|| HttpError::not_found("unknown artifact upload"))?;
        let accounted_bytes = session.upload.size();
        self.in_flight_count = self.in_flight_count.saturating_add(1);
        Ok(InFlightUpload {
            session,
            accounted_bytes,
        })
    }

    /// Restores a completely streamed session while preserving its reserved byte accounting.
    fn restore_after_append(
        &mut self,
        upload_id: String,
        mut in_flight: InFlightUpload,
        now: Instant,
    ) -> Result<(), HttpError> {
        if in_flight.session.upload.size() != in_flight.accounted_bytes {
            let release_result = self.release_in_flight(in_flight.accounted_bytes);
            let _ = in_flight.session.upload.abort();
            release_result?;
            return Err(HttpError::internal(
                "artifact upload byte accounting diverged",
            ));
        }
        if self.sessions.contains_key(&upload_id) {
            let release_result = self.release_in_flight(in_flight.accounted_bytes);
            let _ = in_flight.session.upload.abort();
            release_result?;
            return Err(HttpError::conflict(
                "artifact upload id became active concurrently",
            ));
        }
        in_flight.session.last_activity = now;
        self.in_flight_count = self.in_flight_count.saturating_sub(1);
        self.sessions.insert(upload_id, in_flight.session);
        Ok(())
    }

    /// Releases all count and byte quota reserved for one extracted terminal session.
    fn release_in_flight(&mut self, accounted_bytes: u64) -> Result<(), HttpError> {
        let in_flight_count = self.in_flight_count.checked_sub(1).ok_or_else(|| {
            HttpError::internal("artifact upload in-flight accounting underflowed")
        })?;
        let active_bytes = self
            .active_bytes
            .checked_sub(accounted_bytes)
            .ok_or_else(|| HttpError::internal("artifact upload byte accounting underflowed"))?;
        self.in_flight_count = in_flight_count;
        self.active_bytes = active_bytes;
        Ok(())
    }
}

mod protocol;
mod server;

use protocol::*;
pub use server::serve_artifact_http;

#[cfg(test)]
mod tests;
