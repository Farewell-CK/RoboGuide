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

/// Composition-owned admission for generic Memory claims entering the catalog data plane.
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
}

/// Node/session identity attached to one generic Memory mutation on the internal data plane.
#[derive(Debug, Clone)]
pub struct MemoryPublicationIdentity {
    /// Stable Node identity expected to own the current gRPC route.
    node_id: NodeId,
    /// Current session identity issued after Controller registration acceptance.
    session_id: String,
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

    /// Reads generic Memory replica evidence in deterministic node order.
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
    fn append(&self, payload: EventPayload) -> Result<(), HttpError> {
        self.append_with_admission(payload, None)
    }

    /// Appends one generic Memory event after admission under the shared Controller write gate.
    fn append_memory(
        &self,
        payload: EventPayload,
        admission: &dyn MemoryProviderAdmission,
        publisher: Option<&MemoryPublicationIdentity>,
    ) -> Result<(), HttpError> {
        self.append_with_admission(payload, Some((admission, publisher, None)))
    }

    /// Appends replica evidence with an exact consumer provider under the shared write gate.
    fn append_memory_replica(
        &self,
        payload: EventPayload,
        admission: &dyn MemoryProviderAdmission,
        publisher: Option<&MemoryPublicationIdentity>,
        consumer_provider_id: &str,
    ) -> Result<(), HttpError> {
        self.append_with_admission(
            payload,
            Some((admission, publisher, Some(consumer_provider_id))),
        )
    }

    /// Serializes optional Memory admission with Controller registration updates and persistence.
    fn append_with_admission(
        &self,
        payload: EventPayload,
        admission: Option<(
            &dyn MemoryProviderAdmission,
            Option<&MemoryPublicationIdentity>,
            Option<&str>,
        )>,
    ) -> Result<(), HttpError> {
        let _write_guard = self
            .write_gate
            .lock()
            .map_err(|_| HttpError::internal("event-log write gate is poisoned"))?;
        self.ensure_available()?;
        if let Some((admission, publisher, consumer_provider_id)) = admission {
            admit_memory_payload(&payload, admission, publisher, consumer_provider_id)?;
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
        Ok(())
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
    consumer_provider_id: Option<&str>,
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
        EventPayload::MemoryArtifactStaged { manifest, node_id }
        | EventPayload::MemoryArtifactImported { manifest, node_id }
        | EventPayload::MemoryArtifactRejected {
            manifest, node_id, ..
        } => {
            admission
                .admit_replica(
                    node_id,
                    consumer_provider_id.ok_or_else(|| {
                        HttpError::internal("replica admission lacks a consumer provider identity")
                    })?,
                    manifest,
                )
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

/// Accepts independent artifact HTTP connections from an already-bound listener forever.
pub async fn serve_artifact_http(
    listener: TcpListener,
    store: FileSystemArtifactStore,
    catalog: ArtifactCatalog,
    memory_admission: Arc<dyn MemoryProviderAdmission>,
) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let uploads: Uploads = Arc::new(Mutex::new(UploadRegistry::production()));
    let mut sweep = interval(UPLOAD_SWEEP_INTERVAL);
    sweep.set_missed_tick_behavior(MissedTickBehavior::Skip);
    loop {
        tokio::select! {
            accepted = listener.accept() => {
                let (mut stream, _) = accepted?;
                let shared_store = store.clone();
                let shared_catalog = catalog.clone();
                let shared_uploads = uploads.clone();
                let shared_memory_admission = Arc::clone(&memory_admission);
                tokio::spawn(async move {
                    if let Err(error) =
                        handle_connection(
                            &mut stream,
                            &shared_store,
                            &shared_catalog,
                            &shared_uploads,
                            shared_memory_admission.as_ref(),
                        ).await
                    {
                        let _ = write_json(&mut stream, error.status(), &error.body()).await;
                    }
                });
            }
            _ = sweep.tick() => {
                if let Err(error) = expire_uploads(&uploads, Instant::now()) {
                    eprintln!("artifact upload expiration failed: {}", error.message);
                }
            }
        }
    }
}

/// Handles one request and closes the short-lived HTTP connection.
async fn handle_connection(
    stream: &mut TcpStream,
    store: &FileSystemArtifactStore,
    catalog: &ArtifactCatalog,
    uploads: &Uploads,
    memory_admission: &dyn MemoryProviderAdmission,
) -> Result<(), HttpError> {
    let mut head = read_request_head(stream).await?;
    let method = head.method.clone();
    let path = head.path.clone();
    let response = match (method.as_str(), path.as_str()) {
        ("GET", "/healthz") => {
            catalog.ensure_available()?;
            Response::Json("200 OK", serde_json::json!({"status": "ok"}))
        }
        ("POST", "/v1/artifact-uploads") => {
            let request = head.read_json(stream).await?;
            start_upload(&request, store, uploads)?
        }
        ("GET", "/v1/maps") => {
            let revisions = catalog.revisions()?;
            Response::Json("200 OK", serde_json::json!({"revisions": revisions}))
        }
        ("GET", "/v1/memories") => list_memories(catalog)?,
        ("GET", path) if path.starts_with("/v1/artifacts/") => {
            stream_artifact(stream, store, path.trim_start_matches("/v1/artifacts/")).await?;
            return Ok(());
        }
        ("GET", path) if path.starts_with("/v1/maps/") => get_revision(catalog, path)?,
        ("GET", path) if path.starts_with("/v1/memories/") => get_memory(catalog, path)?,
        (method, path)
            if matches!(method, "POST" | "PUT")
                && path.starts_with("/v1/artifact-uploads/")
                && path.ends_with("/content") =>
        {
            append_upload(stream, &head, uploads).await?
        }
        ("POST", path)
            if path.starts_with("/v1/artifact-uploads/") && path.ends_with("/finalize") =>
        {
            let request = head.read_json(stream).await?;
            finalize_upload(&request, store, uploads)?
        }
        ("DELETE", path) if path.starts_with("/v1/artifact-uploads/") => {
            abort_upload(path, uploads)?
        }
        ("POST", path)
            if path.starts_with("/v1/maps/") && path.ends_with("/localization-evidence") =>
        {
            let request = head.read_json(stream).await?;
            record_localization_evidence(catalog, &request, path)?
        }
        ("POST", path) if path.starts_with("/v1/maps/") && path.ends_with("/replicas") => {
            let request = head.read_json(stream).await?;
            record_replica(catalog, &request, path)?
        }
        ("POST", path) if path.starts_with("/v1/memories/") && path.ends_with("/replicas") => {
            let request = head.read_json(stream).await?;
            record_memory_replica(catalog, store, memory_admission, &request, path)?
        }
        ("POST", path) if path.starts_with("/v1/memories/") && path.contains("/revisions/") => {
            let request = head.read_json(stream).await?;
            publish_memory(catalog, store, memory_admission, &request, path)?
        }
        ("POST", path) if path.starts_with("/v1/maps/") && path.contains("/revisions/") => {
            let request = head.read_json(stream).await?;
            publish_revision(catalog, store, &request, path)?
        }
        _ => return Err(HttpError::not_found("artifact endpoint not found")),
    };
    response.write(stream).await
}

/// Lists generic Memory plus a read-only adapter over typed Spatial map revisions.
fn list_memories(catalog: &ArtifactCatalog) -> Result<Response, HttpError> {
    let mut memories = catalog
        .memories()?
        .into_iter()
        .map(|manifest| {
            let key = (
                manifest.selector().memory_id().as_str().to_string(),
                manifest.selector().revision_id().as_str().to_string(),
            );
            serde_json::to_value(manifest)
                .map(|value| (key, value))
                .map_err(|error| HttpError::internal(format!("encode Memory manifest: {error}")))
        })
        .collect::<Result<Vec<_>, _>>()?;
    memories.extend(
        catalog
            .revisions()?
            .into_iter()
            .map(|snapshot| {
                let selector = snapshot.manifest().selector();
                let key = (
                    selector.map_id().as_str().to_string(),
                    selector.revision_id().as_str().to_string(),
                );
                (key, map_memory_view(snapshot))
            })
            .collect::<Vec<_>>(),
    );
    memories.sort_by(|left, right| left.0.cmp(&right.0));
    let memories = memories
        .into_iter()
        .map(|(_, value)| value)
        .collect::<Vec<_>>();
    Ok(Response::Json(
        "200 OK",
        serde_json::json!({
            "schema": "roboguide.memory-catalog/v0.1",
            "memories": memories,
        }),
    ))
}

/// Returns one generic Memory manifest and its node-local replica evidence.
fn get_memory(catalog: &ArtifactCatalog, path: &str) -> Result<Response, HttpError> {
    let selector = memory_selector_from_path(path, false)?;
    if let Some(manifest) = catalog.memory(&selector)? {
        let replicas = catalog.memory_replicas(&selector)?;
        return Ok(Response::Json(
            "200 OK",
            serde_json::json!({"manifest": manifest, "replicas": replicas}),
        ));
    }
    let map_selector = map_selector_from_memory(&selector)?;
    let snapshot = catalog
        .revision(&map_selector)?
        .ok_or_else(|| HttpError::not_found("unknown Memory revision"))?;
    let replicas = catalog.map_replicas(&map_selector)?;
    Ok(Response::Json(
        "200 OK",
        serde_json::json!({"manifest": map_memory_view(snapshot), "replicas": replicas}),
    ))
}

/// Publishes immutable Memory metadata after verifying any referenced CAS content.
fn publish_memory(
    catalog: &ArtifactCatalog,
    store: &FileSystemArtifactStore,
    memory_admission: &dyn MemoryProviderAdmission,
    request: &Request,
    path: &str,
) -> Result<Response, HttpError> {
    let selector = memory_selector_from_path(path, false)?;
    let manifest: MemoryArtifactManifest = parse_json(&request.body)?;
    manifest
        .validate()
        .map_err(|error| HttpError::bad_request(error.to_string()))?;
    if manifest.selector() != &selector {
        return Err(HttpError::bad_request(
            "Memory manifest selector does not match request path",
        ));
    }
    if manifest.kind() == domain::MemoryKind::Spatial
        && manifest.payload_schema() == domain::SPATIAL_MEMORY_SCHEMA_V0_1
    {
        return Err(HttpError::bad_request(
            "typed map manifests must use /v1/maps so Spatial validation remains authoritative",
        ));
    }
    if let Some(artifact) = manifest.artifact() {
        store
            .verify_artifact(artifact.content_digest().as_str(), artifact.byte_size())
            .map_err(map_cas_error)?;
    }
    catalog.append_memory(
        EventPayload::MemoryManifestPublished { manifest },
        memory_admission,
        request.memory_publisher.as_ref(),
    )?;
    Ok(Response::Json(
        "201 Created",
        serde_json::json!({"status": "published"}),
    ))
}

/// Records one generic staged/imported/rejected exchange transition.
fn record_memory_replica(
    catalog: &ArtifactCatalog,
    store: &FileSystemArtifactStore,
    memory_admission: &dyn MemoryProviderAdmission,
    request: &Request,
    path: &str,
) -> Result<Response, HttpError> {
    let selector = memory_selector_from_path(path, true)?;
    let input: MemoryReplicaInput = parse_json(&request.body)?;
    if input.manifest.selector() != &selector {
        return Err(HttpError::bad_request(
            "Memory replica manifest selector does not match request path",
        ));
    }
    let artifact = input.manifest.artifact().ok_or_else(|| {
        HttpError::bad_request("metadata-only Memory cannot produce replica evidence")
    })?;
    store
        .verify_artifact(artifact.content_digest().as_str(), artifact.byte_size())
        .map_err(map_cas_error)?;
    let node_id =
        NodeId::new(input.node_id).map_err(|error| HttpError::bad_request(error.to_string()))?;
    let consumer_provider_id = input.consumer_provider_id;
    let payload = match input.status.as_str() {
        "staged" => EventPayload::MemoryArtifactStaged {
            manifest: input.manifest,
            node_id,
        },
        "imported" => EventPayload::MemoryArtifactImported {
            manifest: input.manifest,
            node_id,
        },
        "rejected" => EventPayload::MemoryArtifactRejected {
            manifest: input.manifest,
            node_id,
            reason: input
                .reason
                .unwrap_or_else(|| "rejected by node".to_string()),
        },
        _ => {
            return Err(HttpError::bad_request(
                "generic Memory replica status must be staged/imported/rejected",
            ));
        }
    };
    catalog.append_memory_replica(
        payload,
        memory_admission,
        request.memory_publisher.as_ref(),
        &consumer_provider_id,
    )?;
    Ok(Response::Json(
        "202 Accepted",
        serde_json::json!({"status": input.status}),
    ))
}

/// Parses the fixed `/v1/memories/{id}/revisions/{revision}` resource grammar.
fn memory_selector_from_path(path: &str, replica: bool) -> Result<MemorySelector, HttpError> {
    let parts = path
        .trim_start_matches("/v1/memories/")
        .split('/')
        .collect::<Vec<_>>();
    let expected_len = if replica { 4 } else { 3 };
    if parts.len() != expected_len || parts[1] != "revisions" || replica && parts[3] != "replicas" {
        return Err(HttpError::not_found("Memory revision path is invalid"));
    }
    Ok(MemorySelector::new(
        MemoryId::new(parts[0]).map_err(|error| HttpError::bad_request(error.to_string()))?,
        MemoryRevisionId::new(parts[2])
            .map_err(|error| HttpError::bad_request(error.to_string()))?,
    ))
}

/// Converts the shared path-safe Memory selector into its typed map counterpart.
fn map_selector_from_memory(selector: &MemorySelector) -> Result<MapRevisionSelector, HttpError> {
    Ok(MapRevisionSelector::new(
        domain::MapId::new(selector.memory_id().as_str())
            .map_err(|error| HttpError::bad_request(error.to_string()))?,
        domain::MapRevisionId::new(selector.revision_id().as_str())
            .map_err(|error| HttpError::bad_request(error.to_string()))?,
    ))
}

/// Adapts one typed map snapshot into generic Memory discovery JSON without duplicating facts.
fn map_memory_view(snapshot: domain::MapRevisionSnapshot) -> serde_json::Value {
    let manifest = snapshot.manifest();
    serde_json::json!({
        "schema": "roboguide.memory-manifest-view/v0.1",
        "selector": {
            "memory_id": manifest.selector().map_id().as_str(),
            "revision_id": manifest.selector().revision_id().as_str(),
        },
        "kind": "spatial",
        "provider_id": "typed-map-catalog",
        "owner": {
            "owner": "node",
            "node_id": manifest.producer_node_id().as_str(),
            "local_system_id": manifest.producer_local_system_id().map(domain::LocalSystemId::as_str),
        },
        "scope": {"kind": "global"},
        "visibility": "exchangeable",
        "payload_schema": domain::SPATIAL_MEMORY_SCHEMA_V0_1,
        "media_type": manifest.media_type(),
        "artifact": {
            "content_digest": manifest.artifact().content_digest(),
            "byte_size": manifest.artifact().byte_size(),
        },
        "source_mission_id": manifest.source_mission_id(),
        "source_execution_id": manifest.source_execution_id(),
        "source_task_ref": manifest.source_task_ref(),
        "created_at": manifest.created_at(),
        "typed_extension": "map",
        "status": snapshot.status(),
    })
}

/// Records one complete strong localization evidence event after path identity validation.
fn record_localization_evidence(
    catalog: &ArtifactCatalog,
    request: &Request,
    path: &str,
) -> Result<Response, HttpError> {
    let parts = path
        .trim_start_matches("/v1/maps/")
        .split('/')
        .collect::<Vec<_>>();
    if parts.len() != 4 || parts[1] != "revisions" || parts[3] != "localization-evidence" {
        return Err(HttpError::not_found(
            "localization evidence path is invalid",
        ));
    }
    let evidence: domain::LocalizationVerificationEvidence = parse_json(&request.body)?;
    if evidence.artifact().selector().map_id().as_str() != parts[0]
        || evidence.artifact().selector().revision_id().as_str() != parts[2]
    {
        return Err(HttpError::bad_request(
            "localization evidence selector does not match path",
        ));
    }
    catalog.append(EventPayload::MapLocalizationEvidenceRecorded { evidence })?;
    Ok(Response::Json(
        "201 Created",
        serde_json::json!({"status": "strongly-verified"}),
    ))
}

/// Starts one path-safe temporary upload and returns its opaque upload identity.
fn start_upload(
    request: &Request,
    store: &FileSystemArtifactStore,
    uploads: &Uploads,
) -> Result<Response, HttpError> {
    let input: UploadStart = parse_json(&request.body)?;
    let now = Instant::now();
    let mut registry = uploads
        .lock()
        .map_err(|_| HttpError::internal("artifact upload lock is poisoned"))?;
    registry.expire_idle(now)?;
    if registry.active_uploads() >= registry.max_uploads {
        return Err(HttpError::resource_exhausted(
            "active artifact upload count exceeds limit",
        ));
    }
    if registry.sessions.contains_key(&input.upload_id) {
        return Err(HttpError::conflict("artifact upload id is already active"));
    }
    let upload = store
        .begin_upload(input.upload_id.clone())
        .map_err(map_cas_error)?;
    registry.insert_new(input.upload_id.clone(), upload, now)?;
    Ok(Response::Json(
        "201 Created",
        serde_json::json!({"upload_id": input.upload_id}),
    ))
}

/// Appends the streamed request body to one active upload without buffering the artifact.
async fn append_upload(
    stream: &mut TcpStream,
    head: &RequestHead,
    uploads: &Uploads,
) -> Result<Response, HttpError> {
    let upload_id = head
        .path
        .trim_start_matches("/v1/artifact-uploads/")
        .trim_end_matches("/content");
    if head.content_length > MAX_ARTIFACT_BYTES {
        return Err(HttpError::too_large("artifact exceeds v0 size limit"));
    }
    let mut in_flight = take_for_append(uploads, upload_id, head.content_length)?;
    let stream_result = timeout(
        UPLOAD_BODY_TIMEOUT,
        head.stream_body(stream, &mut in_flight.session.upload),
    )
    .await;
    match stream_result {
        Ok(Ok(())) => {}
        Ok(Err(error)) => {
            abort_in_flight(uploads, in_flight)?;
            return Err(error);
        }
        Err(_) => {
            abort_in_flight(uploads, in_flight)?;
            return Err(HttpError::request_timeout(
                "artifact request body timed out",
            ));
        }
    }
    let size = in_flight.session.upload.size();
    restore_after_append(uploads, upload_id, in_flight)?;
    Ok(Response::Json(
        "202 Accepted",
        serde_json::json!({"upload_id": upload_id, "received_bytes": size}),
    ))
}

/// Finalizes one upload after validating the expected digest and byte count.
fn finalize_upload(
    request: &Request,
    store: &FileSystemArtifactStore,
    uploads: &Uploads,
) -> Result<Response, HttpError> {
    let upload_id = request
        .path
        .trim_start_matches("/v1/artifact-uploads/")
        .trim_end_matches("/finalize");
    let input: UploadFinalize = parse_json(&request.body)?;
    let mut in_flight = take_for_terminal(uploads, upload_id)?;
    let artifact = match in_flight
        .session
        .upload
        .finalize(input.content_digest.as_str(), input.byte_size)
    {
        Ok(artifact) => artifact,
        Err(error) => {
            let response_error = map_cas_error(error);
            abort_in_flight(uploads, in_flight)?;
            return Err(response_error);
        }
    };
    release_in_flight(uploads, in_flight.accounted_bytes)?;
    let present = store.contains(artifact.digest()).map_err(map_cas_error)?;
    if !present {
        return Err(HttpError::internal(
            "finalized artifact disappeared from CAS",
        ));
    }
    Ok(Response::Json(
        "201 Created",
        serde_json::json!({"digest": artifact.digest(), "byte_size": artifact.size()}),
    ))
}

/// Explicitly aborts one staged upload and releases its count and byte quota.
fn abort_upload(path: &str, uploads: &Uploads) -> Result<Response, HttpError> {
    let upload_id = path.trim_start_matches("/v1/artifact-uploads/");
    if upload_id.is_empty() || upload_id.contains('/') {
        return Err(HttpError::not_found("unknown artifact upload"));
    }
    let in_flight = take_for_terminal(uploads, upload_id)?;
    abort_in_flight(uploads, in_flight)?;
    Ok(Response::Json(
        "200 OK",
        serde_json::json!({"upload_id": upload_id, "status": "aborted"}),
    ))
}

/// Streams one immutable CAS blob to the caller without loading it into memory.
async fn stream_artifact(
    stream: &mut TcpStream,
    store: &FileSystemArtifactStore,
    digest: &str,
) -> Result<(), HttpError> {
    let file = store.open_artifact(digest).map_err(map_cas_error)?;
    let metadata = file
        .metadata()
        .map_err(|error| HttpError::internal(format!("stat artifact: {error}")))?;
    let mut file = tokio::fs::File::from_std(file);
    let header = format!(
        "HTTP/1.1 200 OK\r\nContent-Type: application/octet-stream\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        metadata.len()
    );
    stream
        .write_all(header.as_bytes())
        .await
        .map_err(|error| HttpError::internal(error.to_string()))?;
    let mut buffer = vec![0_u8; 1024 * 1024];
    loop {
        let count = file
            .read(&mut buffer)
            .await
            .map_err(|error| HttpError::internal(error.to_string()))?;
        if count == 0 {
            break;
        }
        stream
            .write_all(&buffer[..count])
            .await
            .map_err(|error| HttpError::internal(error.to_string()))?;
    }
    stream
        .shutdown()
        .await
        .map_err(|error| HttpError::internal(error.to_string()))?;
    Ok(())
}

/// Returns one manifest by its logical map/revision path.
fn get_revision(catalog: &ArtifactCatalog, path: &str) -> Result<Response, HttpError> {
    let parts = path
        .trim_start_matches("/v1/maps/")
        .split('/')
        .collect::<Vec<_>>();
    if parts.len() != 3 || parts[1] != "revisions" {
        return Err(HttpError::not_found("map revision path is invalid"));
    }
    let selector = MapRevisionSelector::new(
        domain::MapId::new(parts[0]).map_err(|error| HttpError::bad_request(error.to_string()))?,
        domain::MapRevisionId::new(parts[2])
            .map_err(|error| HttpError::bad_request(error.to_string()))?,
    );
    match catalog.revision(&selector)? {
        Some(snapshot) => Ok(Response::Json("200 OK", serde_json::json!(snapshot))),
        None => Err(HttpError::not_found("unknown map revision")),
    }
}

/// Publishes a validated immutable manifest after confirming the CAS blob and byte count.
fn publish_revision(
    catalog: &ArtifactCatalog,
    store: &FileSystemArtifactStore,
    request: &Request,
    path: &str,
) -> Result<Response, HttpError> {
    let parts = path
        .trim_start_matches("/v1/maps/")
        .split('/')
        .collect::<Vec<_>>();
    if parts.len() != 3 || parts[1] != "revisions" {
        return Err(HttpError::not_found("map revision path is invalid"));
    }
    let manifest: MapArtifactManifest = parse_json(&request.body)?;
    if manifest.selector().map_id().as_str() != parts[0]
        || manifest.selector().revision_id().as_str() != parts[2]
    {
        return Err(HttpError::bad_request(
            "manifest selector does not match request path",
        ));
    }
    store
        .verify_artifact(
            manifest.artifact().content_digest().as_str(),
            manifest.artifact().byte_size(),
        )
        .map_err(map_cas_error)?;
    catalog.append(EventPayload::MapArtifactPublished { manifest })?;
    Ok(Response::Json(
        "201 Created",
        serde_json::json!({"status": "published"}),
    ))
}

/// Records one node-local staged/imported/verified/rejected replica evidence event.
fn record_replica(
    catalog: &ArtifactCatalog,
    request: &Request,
    path: &str,
) -> Result<Response, HttpError> {
    let parts = path
        .trim_start_matches("/v1/maps/")
        .split('/')
        .collect::<Vec<_>>();
    if parts.len() != 4 || parts[1] != "revisions" || parts[3] != "replicas" {
        return Err(HttpError::not_found("replica path is invalid"));
    }
    let path_revision = parts[2];
    let input: ReplicaInput = parse_json(&request.body)?;
    let manifest = input.manifest;
    if manifest.selector().map_id().as_str() != parts[0]
        || manifest.selector().revision_id().as_str() != path_revision
    {
        return Err(HttpError::bad_request(
            "replica manifest selector does not match path",
        ));
    }
    let node_id =
        NodeId::new(input.node_id).map_err(|error| HttpError::bad_request(error.to_string()))?;
    let mission_id = MissionId::new(input.mission_id)
        .map_err(|error| HttpError::bad_request(error.to_string()))?;
    let payload = match input.status.as_str() {
        "staged" => EventPayload::MapArtifactStaged {
            manifest,
            node_id,
            mission_id,
        },
        "imported" => EventPayload::MapArtifactImported {
            manifest,
            node_id,
            mission_id,
        },
        "verified" => EventPayload::MapLocalizationVerified {
            artifact: manifest.artifact().clone(),
            node_id,
            mission_id,
            anchor_id: SpatialAnchorId::new(input.anchor_id.unwrap_or_default())
                .map_err(|error| HttpError::bad_request(error.to_string()))?,
        },
        "rejected" => EventPayload::MapArtifactRejected {
            artifact: manifest.artifact().clone(),
            node_id,
            mission_id,
            reason: input
                .reason
                .unwrap_or_else(|| "rejected by node".to_string()),
        },
        _ => {
            return Err(HttpError::bad_request(
                "replica status must be staged/imported/verified/rejected",
            ));
        }
    };
    catalog.append(payload)?;
    Ok(Response::Json(
        "202 Accepted",
        serde_json::json!({"status": input.status}),
    ))
}

/// Expires idle sessions under one registry lock at a caller-supplied monotonic time.
fn expire_uploads(uploads: &Uploads, now: Instant) -> Result<usize, HttpError> {
    uploads
        .lock()
        .map_err(|_| HttpError::internal("artifact upload lock is poisoned"))?
        .expire_idle(now)
}

/// Extracts one upload while atomically reserving all bytes declared by its request body.
fn take_for_append(
    uploads: &Uploads,
    upload_id: &str,
    incoming_bytes: u64,
) -> Result<InFlightUpload, HttpError> {
    let now = Instant::now();
    let mut registry = uploads
        .lock()
        .map_err(|_| HttpError::internal("artifact upload lock is poisoned"))?;
    registry.expire_idle(now)?;
    registry.take_for_append(upload_id, incoming_bytes)
}

/// Extracts one upload for a terminal operation after expiring stale idle sessions.
fn take_for_terminal(uploads: &Uploads, upload_id: &str) -> Result<InFlightUpload, HttpError> {
    let now = Instant::now();
    let mut registry = uploads
        .lock()
        .map_err(|_| HttpError::internal("artifact upload lock is poisoned"))?;
    registry.expire_idle(now)?;
    registry.take_for_terminal(upload_id)
}

/// Returns a successfully streamed upload to the registry and refreshes its idle deadline.
fn restore_after_append(
    uploads: &Uploads,
    upload_id: &str,
    in_flight: InFlightUpload,
) -> Result<(), HttpError> {
    uploads
        .lock()
        .map_err(|_| HttpError::internal("artifact upload lock is poisoned"))?
        .restore_after_append(upload_id.to_string(), in_flight, Instant::now())
}

/// Releases quota for one terminal upload after finalization has removed its staging name.
fn release_in_flight(uploads: &Uploads, accounted_bytes: u64) -> Result<(), HttpError> {
    uploads
        .lock()
        .map_err(|_| HttpError::internal("artifact upload lock is poisoned"))?
        .release_in_flight(accounted_bytes)
}

/// Explicitly aborts an extracted upload, then releases its reserved count and bytes.
fn abort_in_flight(uploads: &Uploads, mut in_flight: InFlightUpload) -> Result<(), HttpError> {
    let abort_result = in_flight.session.upload.abort().map_err(map_cas_error);
    release_in_flight(uploads, in_flight.accounted_bytes)?;
    abort_result
}

/// Initial upload-control request body.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct UploadStart {
    /// Path-safe temporary upload identity.
    upload_id: String,
}

/// Upload finalization request body.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct UploadFinalize {
    /// Expected canonical SHA-256 digest.
    content_digest: domain::ContentDigest,
    /// Expected exact byte count.
    byte_size: u64,
}

/// Node replica evidence request body.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ReplicaInput {
    /// Exact manifest represented by the replica.
    manifest: MapArtifactManifest,
    /// Node reporting the local evidence.
    node_id: String,
    /// Mission requesting the replica operation.
    mission_id: String,
    /// Replica lifecycle transition name.
    status: String,
    /// Anchor used by a verification report.
    anchor_id: Option<String>,
    /// Rejection diagnostic.
    reason: Option<String>,
}

/// Generic Memory replica evidence request body.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct MemoryReplicaInput {
    /// Exact immutable Memory metadata represented by the replica.
    manifest: MemoryArtifactManifest,
    /// Node reporting local staging/import evidence.
    node_id: String,
    /// Exact receiving provider admitted by the replica Node registration.
    consumer_provider_id: String,
    /// Monotonic generic replica transition.
    status: String,
    /// Optional rejection diagnostic.
    reason: Option<String>,
}

/// Parsed HTTP request headers and bytes read beyond the header delimiter.
#[derive(Debug)]
struct RequestHead {
    /// Uppercase HTTP method.
    method: String,
    /// Raw path without query parameters.
    path: String,
    /// Declared request body length.
    content_length: u64,
    /// Optional Node/session identity used only by generic Memory mutations.
    memory_publisher: Option<MemoryPublicationIdentity>,
    /// Body prefix already read while locating the header delimiter.
    prefetched_body: Vec<u8>,
}

/// A request head plus a fully collected bounded JSON body.
struct Request {
    /// Raw path without query parameters.
    path: String,
    /// JSON request body bytes.
    body: Vec<u8>,
    /// Optional Node/session identity used only by generic Memory mutations.
    memory_publisher: Option<MemoryPublicationIdentity>,
}

impl RequestHead {
    /// Reads a bounded JSON body after the header parser has retained its prefix.
    async fn read_json(&mut self, stream: &mut TcpStream) -> Result<Request, HttpError> {
        if self.content_length > MAX_JSON_BODY_BYTES {
            return Err(HttpError::too_large("JSON request body exceeds limit"));
        }
        let expected = usize::try_from(self.content_length)
            .map_err(|_| HttpError::too_large("request body is too large"))?;
        Self::read_remaining(stream, &mut self.prefetched_body, expected).await?;
        Ok(Request {
            path: self.path.clone(),
            body: self.prefetched_body.clone(),
            memory_publisher: self.memory_publisher.clone(),
        })
    }

    /// Streams the declared body into a CAS upload in bounded chunks.
    async fn stream_body(
        &self,
        stream: &mut TcpStream,
        upload: &mut ArtifactUpload,
    ) -> Result<(), HttpError> {
        let expected = usize::try_from(self.content_length)
            .map_err(|_| HttpError::too_large("request body is too large"))?;
        if self.prefetched_body.len() > expected {
            return Err(HttpError::bad_request(
                "request contains bytes beyond declared Content-Length",
            ));
        }
        let prefetched = self.prefetched_body.len();
        if prefetched > 0 {
            upload
                .write_chunk(&self.prefetched_body[..prefetched])
                .map_err(map_cas_error)?;
        }
        let mut received = prefetched;
        let mut buffer = [0_u8; 64 * 1024];
        while received < expected {
            let remaining = expected - received;
            let read_limit = remaining.min(buffer.len());
            let count = stream
                .read(&mut buffer[..read_limit])
                .await
                .map_err(|error| HttpError::internal(error.to_string()))?;
            if count == 0 {
                return Err(HttpError::bad_request("request body ended early"));
            }
            upload
                .write_chunk(&buffer[..count])
                .map_err(map_cas_error)?;
            received += count;
        }
        Ok(())
    }

    /// Reads exactly the remaining body bytes into a bounded control-request buffer.
    async fn read_remaining(
        stream: &mut TcpStream,
        body: &mut Vec<u8>,
        expected: usize,
    ) -> Result<(), HttpError> {
        if body.len() > expected {
            return Err(HttpError::bad_request(
                "request contains bytes beyond declared Content-Length",
            ));
        }
        let mut chunk = [0_u8; 8192];
        while body.len() < expected {
            let remaining = expected - body.len();
            let read_limit = remaining.min(chunk.len());
            let count = stream
                .read(&mut chunk[..read_limit])
                .await
                .map_err(|error| HttpError::internal(error.to_string()))?;
            if count == 0 {
                return Err(HttpError::bad_request("request body ended early"));
            }
            body.extend_from_slice(&chunk[..count]);
        }
        Ok(())
    }
}

/// Reads and validates one HTTP request head while retaining only a small body prefix.
async fn read_request_head(stream: &mut TcpStream) -> Result<RequestHead, HttpError> {
    let mut bytes = Vec::new();
    let mut chunk = [0_u8; 8192];
    loop {
        let count = stream
            .read(&mut chunk)
            .await
            .map_err(|error| HttpError::internal(error.to_string()))?;
        if count == 0 {
            return Err(HttpError::bad_request("request ended before headers"));
        }
        bytes.extend_from_slice(&chunk[..count]);
        if let Some(index) = bytes.windows(4).position(|window| window == b"\r\n\r\n") {
            return parse_request_head(bytes, index + 4);
        }
        if bytes.len() >= MAX_HEADER_BYTES {
            return Err(HttpError::headers_too_large("HTTP headers exceed limit"));
        }
    }
}

/// Parses one complete bounded HTTP/1.1 head and validates its fixed-length body framing.
fn parse_request_head(bytes: Vec<u8>, header_end: usize) -> Result<RequestHead, HttpError> {
    if header_end > MAX_HEADER_BYTES {
        return Err(HttpError::headers_too_large("HTTP headers exceed limit"));
    }
    let header_text = std::str::from_utf8(&bytes[..header_end])
        .map_err(|_| HttpError::bad_request("HTTP headers are not UTF-8"))?;
    let mut lines = header_text.split("\r\n");
    let request_line = lines
        .next()
        .ok_or_else(|| HttpError::bad_request("missing request line"))?;
    let mut fields = request_line.split_whitespace();
    let method = fields
        .next()
        .ok_or_else(|| HttpError::bad_request("missing HTTP method"))?
        .to_ascii_uppercase();
    let target = fields
        .next()
        .ok_or_else(|| HttpError::bad_request("missing HTTP target"))?;
    let version = fields
        .next()
        .ok_or_else(|| HttpError::bad_request("missing HTTP version"))?;
    if fields.next().is_some() || version != "HTTP/1.1" {
        return Err(HttpError::bad_request("request line must use HTTP/1.1"));
    }
    if !target.starts_with('/') {
        return Err(HttpError::bad_request("HTTP target must be origin-form"));
    }
    let path = target.split('?').next().unwrap_or(target).to_string();
    let mut content_length = None;
    let mut has_transfer_encoding = false;
    let mut memory_node_id = None;
    let mut memory_session_id = None;
    for line in lines.filter(|line| !line.is_empty()) {
        let (name, value) = line
            .split_once(':')
            .ok_or_else(|| HttpError::bad_request("malformed HTTP header"))?;
        if name.is_empty() || name.trim() != name {
            return Err(HttpError::bad_request("malformed HTTP header name"));
        }
        if name.eq_ignore_ascii_case("content-length") {
            if content_length.is_some() {
                return Err(HttpError::bad_request("duplicate Content-Length header"));
            }
            let value = value.trim();
            if value.is_empty() || !value.bytes().all(|byte| byte.is_ascii_digit()) {
                return Err(HttpError::bad_request(
                    "Content-Length must contain decimal digits only",
                ));
            }
            let parsed = value
                .parse::<u64>()
                .map_err(|_| HttpError::bad_request("Content-Length must be an integer"))?;
            content_length = Some(parsed);
        } else if name.eq_ignore_ascii_case("transfer-encoding") {
            has_transfer_encoding = true;
        } else if name.eq_ignore_ascii_case("x-roboguide-node-id") {
            if memory_node_id.replace(value.trim().to_string()).is_some() {
                return Err(HttpError::bad_request(
                    "duplicate X-RoboGuide-Node-Id header",
                ));
            }
        } else if name.eq_ignore_ascii_case("x-roboguide-session-id")
            && memory_session_id
                .replace(value.trim().to_string())
                .is_some()
        {
            return Err(HttpError::bad_request(
                "duplicate X-RoboGuide-Session-Id header",
            ));
        }
    }
    if has_transfer_encoding {
        return Err(HttpError::bad_request(
            "Transfer-Encoding is unsupported; use Content-Length",
        ));
    }
    let content_length = match content_length {
        Some(length) => length,
        // Bodyless reads are valid without an explicit header.  Mutating endpoints still
        // require Content-Length so the streaming parser never has to guess where bytes end.
        None if matches!(method.as_str(), "GET" | "HEAD" | "DELETE") => 0,
        None => return Err(HttpError::bad_request("Content-Length is required")),
    };
    if content_length > MAX_ARTIFACT_BYTES {
        return Err(HttpError::too_large("request body exceeds artifact limit"));
    }
    let memory_publisher = match (memory_node_id, memory_session_id) {
        (Some(node_id), Some(session_id)) if !session_id.is_empty() => {
            Some(MemoryPublicationIdentity {
                node_id: NodeId::new(node_id)
                    .map_err(|error| HttpError::bad_request(error.to_string()))?,
                session_id,
            })
        }
        (None, None) => None,
        _ => {
            return Err(HttpError::bad_request(
                "generic Memory publisher requires both Node and session headers",
            ));
        }
    };
    let prefetched_body = bytes[header_end..].to_vec();
    let expected = usize::try_from(content_length)
        .map_err(|_| HttpError::too_large("request body is too large"))?;
    if prefetched_body.len() > expected {
        return Err(HttpError::bad_request(
            "request contains bytes beyond declared Content-Length",
        ));
    }
    Ok(RequestHead {
        method,
        path,
        content_length,
        memory_publisher,
        prefetched_body,
    })
}

/// Decodes a bounded JSON request body into a typed control structure.
fn parse_json<T: serde::de::DeserializeOwned>(body: &[u8]) -> Result<T, HttpError> {
    serde_json::from_slice(body)
        .map_err(|error| HttpError::bad_request(format!("invalid JSON body: {error}")))
}

/// Returns the local receive timestamp used for artifact evidence ordering.
fn receive_timestamp() -> TimestampMs {
    let millis = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_millis().min(u128::from(u64::MAX)) as u64)
        .unwrap_or_default();
    TimestampMs::new(millis)
}

/// One successful response variant used by the small HTTP transport.
enum Response {
    /// JSON response body.
    Json(&'static str, serde_json::Value),
}

impl Response {
    /// Writes a JSON response and closes the connection.
    async fn write(self, stream: &mut TcpStream) -> Result<(), HttpError> {
        match self {
            Self::Json(status, body) => write_json(stream, status, &body).await,
        }
    }
}

/// Writes one JSON response with a fixed content length.
async fn write_json(
    stream: &mut TcpStream,
    status: &str,
    body: &serde_json::Value,
) -> Result<(), HttpError> {
    let body = serde_json::to_vec(body).map_err(|error| HttpError::internal(error.to_string()))?;
    let response = format!(
        "HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        body.len()
    );
    stream
        .write_all(response.as_bytes())
        .await
        .map_err(|error| HttpError::internal(error.to_string()))?;
    stream
        .write_all(&body)
        .await
        .map_err(|error| HttpError::internal(error.to_string()))?;
    stream
        .shutdown()
        .await
        .map_err(|error| HttpError::internal(error.to_string()))?;
    Ok(())
}

/// HTTP error carrying a status and JSON-safe diagnostic.
#[derive(Debug)]
struct HttpError {
    /// HTTP status line.
    status: &'static str,
    /// Stable diagnostic message.
    message: String,
}

impl HttpError {
    /// Builds a 400 error.
    fn bad_request(message: impl Into<String>) -> Self {
        Self {
            status: "400 Bad Request",
            message: message.into(),
        }
    }

    /// Builds a 404 error.
    fn not_found(message: impl Into<String>) -> Self {
        Self {
            status: "404 Not Found",
            message: message.into(),
        }
    }

    /// Builds a 408 error for a body stream that exceeded its bounded receive window.
    fn request_timeout(message: impl Into<String>) -> Self {
        Self {
            status: "408 Request Timeout",
            message: message.into(),
        }
    }

    /// Builds a 409 error.
    fn conflict(message: impl Into<String>) -> Self {
        Self {
            status: "409 Conflict",
            message: message.into(),
        }
    }

    /// Builds a 403 error for claims outside current provider ownership.
    fn forbidden(message: impl Into<String>) -> Self {
        Self {
            status: "403 Forbidden",
            message: message.into(),
        }
    }

    /// Builds a 413 error.
    fn too_large(message: impl Into<String>) -> Self {
        Self {
            status: "413 Payload Too Large",
            message: message.into(),
        }
    }

    /// Builds a 431 error for a request head that exceeds the fixed parser bound.
    fn headers_too_large(message: impl Into<String>) -> Self {
        Self {
            status: "431 Request Header Fields Too Large",
            message: message.into(),
        }
    }

    /// Builds a 429 error when the process-local active upload count is exhausted.
    fn resource_exhausted(message: impl Into<String>) -> Self {
        Self {
            status: "429 Too Many Requests",
            message: message.into(),
        }
    }

    /// Builds a 500 error.
    fn internal(message: impl Into<String>) -> Self {
        Self {
            status: "500 Internal Server Error",
            message: message.into(),
        }
    }

    /// Builds a 503 error when durable state requires process recovery.
    fn service_unavailable(message: impl Into<String>) -> Self {
        Self {
            status: "503 Service Unavailable",
            message: message.into(),
        }
    }

    /// Returns the status line.
    fn status(&self) -> &'static str {
        self.status
    }

    /// Converts this error into a JSON body.
    fn body(&self) -> serde_json::Value {
        serde_json::json!({"error": self.message})
    }
}

/// Maps Artifact Store failures into stable HTTP statuses without leaking local paths.
fn map_cas_error(error: CasError) -> HttpError {
    match error {
        CasError::InvalidDigest { .. }
        | CasError::InvalidUploadId { .. }
        | CasError::UploadClosed { .. } => HttpError::bad_request(error.to_string()),
        CasError::ArtifactNotFound { .. } | CasError::UploadNotFound { .. } => {
            HttpError::not_found(error.to_string())
        }
        CasError::DigestMismatch { .. }
        | CasError::SizeMismatch { .. }
        | CasError::ArtifactConflict { .. }
        | CasError::UploadAlreadyExists { .. } => HttpError::conflict(error.to_string()),
        CasError::Io { .. } => HttpError::internal(error.to_string()),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use artifact_store::digest_bytes;
    use domain::{
        ContentDigest, LocalSystemId, MapArtifactRef, MapId, MapReplicaStatus, MapRevisionId,
        MemoryArtifactRef, MemoryKind, MemoryOwner, MemoryScope, MemoryVisibility, MissionId,
        SpatialAnchorId,
    };
    use ports::MapCatalogReader;

    /// Opaque checkpoint schema used to prove Spatial Memory never rewrites controller content.
    const TEST_CHECKPOINT_SCHEMA: &str = "roboguide.test-controller/v1";
    /// Opaque checkpoint body carried forward by artifact-only event batches.
    const TEST_CHECKPOINT_JSON: &str = r#"{"controller":"unchanged"}"#;

    /// Test-only admission that isolates catalog behavior from Controller registration fixtures.
    struct AllowTestMemoryAdmission;

    impl MemoryProviderAdmission for AllowTestMemoryAdmission {
        /// Accepts fixture manifests whose provider semantics are tested separately.
        fn admit_manifest(&self, _manifest: &MemoryArtifactManifest) -> Result<(), String> {
            Ok(())
        }

        /// Accepts fixture replica nodes whose transition semantics are tested by the projection.
        fn admit_replica(
            &self,
            _node_id: &NodeId,
            _consumer_provider_id: &str,
            _manifest: &MemoryArtifactManifest,
        ) -> Result<(), String> {
            Ok(())
        }

        /// Accepts absent fixture sessions because transport behavior is tested independently.
        fn admit_publisher(
            &self,
            _publisher: Option<&MemoryPublicationIdentity>,
            _expected_node_id: &NodeId,
        ) -> Result<(), String> {
            Ok(())
        }
    }

    /// Test-only admission that proves HTTP publication fails before catalog mutation.
    struct DenyTestMemoryAdmission;

    impl MemoryProviderAdmission for DenyTestMemoryAdmission {
        /// Rejects every fixture manifest as undeclared.
        fn admit_manifest(&self, _manifest: &MemoryArtifactManifest) -> Result<(), String> {
            Err("fixture provider is not registered".to_string())
        }

        /// Rejects every fixture replica reporter as unregistered.
        fn admit_replica(
            &self,
            _node_id: &NodeId,
            _consumer_provider_id: &str,
            _manifest: &MemoryArtifactManifest,
        ) -> Result<(), String> {
            Err("fixture replica node is not registered".to_string())
        }

        /// Rejects every fixture session as non-current.
        fn admit_publisher(
            &self,
            _publisher: Option<&MemoryPublicationIdentity>,
            _expected_node_id: &NodeId,
        ) -> Result<(), String> {
            Err("fixture publisher session is not current".to_string())
        }
    }

    /// Test-only admission that requires one exact current owner session.
    struct FixtureSessionMemoryAdmission;

    impl MemoryProviderAdmission for FixtureSessionMemoryAdmission {
        /// Accepts provider semantics so this fixture isolates publisher fencing.
        fn admit_manifest(&self, _manifest: &MemoryArtifactManifest) -> Result<(), String> {
            Ok(())
        }

        /// Requires the exact consumer provider independently of producer ownership.
        fn admit_replica(
            &self,
            _node_id: &NodeId,
            consumer_provider_id: &str,
            _manifest: &MemoryArtifactManifest,
        ) -> Result<(), String> {
            (consumer_provider_id == "fixture-consumer")
                .then_some(())
                .ok_or_else(|| "fixture consumer provider is incompatible".to_string())
        }

        /// Requires the semantic owner and fixture current session to match exactly.
        fn admit_publisher(
            &self,
            publisher: Option<&MemoryPublicationIdentity>,
            expected_node_id: &NodeId,
        ) -> Result<(), String> {
            let publisher = publisher.ok_or_else(|| "fixture publisher is missing".to_string())?;
            if publisher.node_id() != expected_node_id {
                return Err("fixture publisher does not own the Memory".to_string());
            }
            if publisher.session_id() != "session-current" {
                return Err("fixture publisher session is stale".to_string());
            }
            Ok(())
        }
    }

    /// Creates one production-shaped empty upload registry for HTTP request tests.
    fn test_uploads() -> Uploads {
        Arc::new(Mutex::new(UploadRegistry::production()))
    }

    /// Persists a sequence-zero controller checkpoint for an otherwise empty test log.
    fn seed_controller_checkpoint(event_log: &SqliteEventLog) {
        event_log.begin_batch().expect("checkpoint batch starts");
        event_log
            .save_checkpoint(TEST_CHECKPOINT_SCHEMA, TEST_CHECKPOINT_JSON)
            .expect("checkpoint saves");
        event_log.commit_batch().expect("checkpoint batch commits");
    }

    /// Builds one valid immutable manifest for the HTTP round-trip fixture.
    fn fixture_manifest(digest: &str, byte_size: u64) -> MapArtifactManifest {
        let selector = MapRevisionSelector::new(
            MapId::new("map-a").expect("map id is valid"),
            MapRevisionId::new("r1").expect("revision id is valid"),
        );
        let artifact = MapArtifactRef::new(
            selector,
            ContentDigest::new(digest).expect("digest is valid"),
            byte_size,
        );
        MapArtifactManifest::new(
            artifact,
            "application/octet-stream",
            "grid-v1",
            NodeId::new("dog-a").expect("node id is valid"),
            None,
            MissionId::new("mission-a").expect("mission id is valid"),
            Some("execution-a".to_string()),
            None,
            "map",
            "enu",
            SpatialAnchorId::new("anchor-lab").expect("anchor is valid"),
            Some(0.05),
            TimestampMs::new(1),
            None,
        )
        .expect("manifest is valid")
    }

    /// Builds one generic exchangeable Memory manifest backed by a finalized CAS artifact.
    fn fixture_memory_manifest(
        id: &str,
        kind: MemoryKind,
        digest: &str,
        byte_size: u64,
    ) -> MemoryArtifactManifest {
        MemoryArtifactManifest::new(
            MemorySelector::new(
                MemoryId::new(id).expect("Memory id is valid"),
                MemoryRevisionId::new("r1").expect("Memory revision is valid"),
            ),
            kind,
            "fixture-provider",
            MemoryOwner::Node {
                node_id: NodeId::new("dog-a").expect("node id is valid"),
                local_system_id: LocalSystemId::new("memory").expect("system id is valid"),
            },
            MemoryScope::Global,
            MemoryVisibility::Exchangeable,
            "example.memory/v1",
            "application/octet-stream",
            Some(MemoryArtifactRef::new(
                ContentDigest::new(digest).expect("digest is valid"),
                byte_size,
            )),
            None,
            None,
            None,
            TimestampMs::new(1),
        )
        .expect("Memory manifest is valid")
    }

    /// Builds one raw HTTP request with an optional body and explicit content length.
    fn raw_request(method: &str, path: &str, body: &[u8], include_length: bool) -> Vec<u8> {
        let length = if include_length {
            format!("Content-Length: {}\r\n", body.len())
        } else {
            String::new()
        };
        let header = format!(
            "{method} {path} HTTP/1.1\r\nHost: localhost\r\n{length}Connection: close\r\n\r\n"
        );
        let mut request = header.into_bytes();
        request.extend_from_slice(body);
        request
    }

    /// Builds one generic Memory mutation carrying its framework-level Node session identity.
    fn raw_memory_request(path: &str, body: &[u8], node_id: &str, session_id: &str) -> Vec<u8> {
        let header = format!(
            "POST {path} HTTP/1.1\r\nHost: localhost\r\nContent-Length: {}\r\nX-RoboGuide-Node-Id: {node_id}\r\nX-RoboGuide-Session-Id: {session_id}\r\nConnection: close\r\n\r\n",
            body.len()
        );
        let mut request = header.into_bytes();
        request.extend_from_slice(body);
        request
    }

    /// Builds a request whose declared length may exceed its bytes to exercise disconnect cleanup.
    fn raw_request_with_declared_length(
        method: &str,
        path: &str,
        body: &[u8],
        declared_length: u64,
    ) -> Vec<u8> {
        let header = format!(
            "{method} {path} HTTP/1.1\r\nHost: localhost\r\nContent-Length: {declared_length}\r\nConnection: close\r\n\r\n"
        );
        let mut request = header.into_bytes();
        request.extend_from_slice(body);
        request
    }

    /// Sends one request through a one-shot local listener and returns the raw response.
    async fn request_once(
        store: &FileSystemArtifactStore,
        catalog: &ArtifactCatalog,
        uploads: &Uploads,
        request: Vec<u8>,
    ) -> Vec<u8> {
        request_once_with_admission(
            store,
            catalog,
            uploads,
            request,
            Arc::new(AllowTestMemoryAdmission),
        )
        .await
    }

    /// Sends one request with caller-selected Memory admission behavior.
    async fn request_once_with_admission(
        store: &FileSystemArtifactStore,
        catalog: &ArtifactCatalog,
        uploads: &Uploads,
        request: Vec<u8>,
        memory_admission: Arc<dyn MemoryProviderAdmission>,
    ) -> Vec<u8> {
        let listener = TcpListener::bind("127.0.0.1:0")
            .await
            .expect("test listener binds");
        let address = listener.local_addr().expect("test listener has address");
        let server_store = store.clone();
        let server_catalog = catalog.clone();
        let server_uploads = uploads.clone();
        let server = tokio::spawn(async move {
            let (mut stream, _) = listener.accept().await.expect("test request accepts");
            if let Err(error) = handle_connection(
                &mut stream,
                &server_store,
                &server_catalog,
                &server_uploads,
                memory_admission.as_ref(),
            )
            .await
            {
                let _ = write_json(&mut stream, error.status(), &error.body()).await;
            }
        });
        let mut client = TcpStream::connect(address)
            .await
            .expect("test client connects");
        client
            .write_all(&request)
            .await
            .expect("test request writes");
        client
            .shutdown()
            .await
            .expect("test client shuts down write");
        let mut response = Vec::new();
        client
            .read_to_end(&mut response)
            .await
            .expect("test response reads");
        server.await.expect("test server joins");
        response
    }

    /// Sends caller-selected TCP fragments through a one-shot listener and returns the response.
    async fn fragmented_request_once(
        store: &FileSystemArtifactStore,
        catalog: &ArtifactCatalog,
        uploads: &Uploads,
        fragments: &[&[u8]],
    ) -> Vec<u8> {
        let listener = TcpListener::bind("127.0.0.1:0")
            .await
            .expect("test listener binds");
        let address = listener.local_addr().expect("test listener has address");
        let server_store = store.clone();
        let server_catalog = catalog.clone();
        let server_uploads = uploads.clone();
        let memory_admission = Arc::new(AllowTestMemoryAdmission);
        let server = tokio::spawn(async move {
            let (mut stream, _) = listener.accept().await.expect("test request accepts");
            if let Err(error) = handle_connection(
                &mut stream,
                &server_store,
                &server_catalog,
                &server_uploads,
                memory_admission.as_ref(),
            )
            .await
            {
                let _ = write_json(&mut stream, error.status(), &error.body()).await;
            }
        });
        let mut client = TcpStream::connect(address)
            .await
            .expect("test client connects");
        for fragment in fragments {
            client
                .write_all(fragment)
                .await
                .expect("test fragment writes");
            tokio::task::yield_now().await;
        }
        client
            .shutdown()
            .await
            .expect("test client shuts down write");
        let mut response = Vec::new();
        client
            .read_to_end(&mut response)
            .await
            .expect("test response reads");
        server.await.expect("test server joins");
        response
    }

    /// Checks a response status line and returns its body bytes.
    fn response_body(response: &[u8], status: &str) -> Vec<u8> {
        let separator = response
            .windows(4)
            .position(|window| window == b"\r\n\r\n")
            .expect("response has headers");
        let header = std::str::from_utf8(&response[..separator]).expect("response header is UTF-8");
        assert!(
            header.starts_with(&format!("HTTP/1.1 {status}")),
            "{header}"
        );
        response[separator + 4..].to_vec()
    }

    /// All five Memory kinds publish through one catalog while their bytes remain in CAS.
    #[tokio::test]
    async fn generic_memory_catalog_publishes_all_kinds_and_tracks_selective_exchange() {
        let directory = tempfile::tempdir().expect("temporary directory exists");
        let store = FileSystemArtifactStore::new(directory.path().join("artifacts"))
            .expect("CAS initializes");
        let bytes = b"shared-memory-artifact";
        let digest = digest_bytes(bytes);
        let mut upload = store.begin_upload("memory-fixture").expect("upload begins");
        upload.write_chunk(bytes).expect("artifact bytes write");
        upload
            .finalize(&digest, bytes.len() as u64)
            .expect("artifact finalizes");
        let event_log =
            SqliteEventLog::open(directory.path().join("events.sqlite3")).expect("event log opens");
        seed_controller_checkpoint(&event_log);
        let catalog = ArtifactCatalog::replay_with_gate(&event_log, Arc::new(Mutex::new(())))
            .expect("catalog replays");
        let uploads = test_uploads();
        let kinds = [
            ("execution-a", MemoryKind::Execution),
            ("spatial-a", MemoryKind::Spatial),
            ("semantic-a", MemoryKind::Semantic),
            ("experience-a", MemoryKind::Experience),
            ("artifact-a", MemoryKind::Artifact),
        ];

        for (id, kind) in kinds {
            let manifest = fixture_memory_manifest(id, kind, &digest, bytes.len() as u64);
            let body = serde_json::to_vec(&manifest).expect("manifest serializes");
            let path = format!("/v1/memories/{id}/revisions/r1");
            response_body(
                &request_once(
                    &store,
                    &catalog,
                    &uploads,
                    raw_request("POST", &path, &body, true),
                )
                .await,
                "201 Created",
            );
        }

        let execution = fixture_memory_manifest(
            "execution-a",
            MemoryKind::Execution,
            &digest,
            bytes.len() as u64,
        );
        let replica = serde_json::to_vec(&serde_json::json!({
            "manifest": execution,
            "node_id": "dog-b",
            "consumer_provider_id": "execution-consumer",
            "status": "staged",
        }))
        .expect("replica request serializes");
        response_body(
            &request_once(
                &store,
                &catalog,
                &uploads,
                raw_request(
                    "POST",
                    "/v1/memories/execution-a/revisions/r1/replicas",
                    &replica,
                    true,
                ),
            )
            .await,
            "202 Accepted",
        );

        let list = request_once(
            &store,
            &catalog,
            &uploads,
            raw_request("GET", "/v1/memories", &[], false),
        )
        .await;
        let list: serde_json::Value = serde_json::from_slice(&response_body(&list, "200 OK"))
            .expect("Memory catalog response is JSON");
        assert_eq!(list["memories"].as_array().map(Vec::len), Some(5));

        let detail = request_once(
            &store,
            &catalog,
            &uploads,
            raw_request("GET", "/v1/memories/execution-a/revisions/r1", &[], false),
        )
        .await;
        let detail: serde_json::Value = serde_json::from_slice(&response_body(&detail, "200 OK"))
            .expect("Memory detail response is JSON");
        assert_eq!(detail["replicas"][0]["status"], "staged");
    }

    /// Node A publishes CAS-backed Spatial Memory and Node B selectively imports it locally.
    #[tokio::test]
    async fn local_memory_provider_cross_node_data_plane_exchange_is_integrated() {
        use node_service::{
            BoxDriverFuture, CompiledDriverRequest, DriverKind, DriverResponse, LocalDriver,
            LocalIntegrationEngine, MemoryQuery, NodeServiceConfig,
        };

        /// Fake heterogeneous Local EAIOS driver used by Node B's configured import workflow.
        struct NoopHttpDriver {
            /// Counts actual provider import calls.
            imports: Arc<std::sync::atomic::AtomicUsize>,
        }

        impl LocalDriver for NoopHttpDriver {
            /// Identifies the configured local HTTP route family.
            fn kind(&self) -> DriverKind {
                DriverKind::Http
            }

            /// Returns one terminal response if a workflow is unexpectedly exercised.
            fn invoke<'a>(&'a self, request: &'a CompiledDriverRequest) -> BoxDriverFuture<'a> {
                if let CompiledDriverRequest::Http { path, body, .. } = request
                    && path == "/memory/import"
                {
                    assert_eq!(body["manifest"]["selector"]["memory_id"], "provider-map-a");
                    assert!(body["staged_path"].as_str().is_some());
                    self.imports
                        .fetch_add(1, std::sync::atomic::Ordering::SeqCst);
                }
                Box::pin(async {
                    let (sender, receiver) = tokio::sync::mpsc::channel(1);
                    sender
                        .send(Ok(node_service::DriverEvent {
                            sequence: 1,
                            payload: serde_json::json!({"state": "READY", "run_id": "noop"}),
                            terminal: true,
                        }))
                        .await
                        .expect("test receiver remains available");
                    Ok(DriverResponse { events: receiver })
                })
            }
        }

        let directory = tempfile::tempdir().expect("temporary directory exists");
        let store =
            FileSystemArtifactStore::new(directory.path().join("cas")).expect("CAS initializes");
        let event_log =
            SqliteEventLog::open(directory.path().join("events.sqlite3")).expect("event log opens");
        seed_controller_checkpoint(&event_log);
        let catalog = ArtifactCatalog::replay_with_gate(&event_log, Arc::new(Mutex::new(())))
            .expect("catalog replays");
        let listener = TcpListener::bind("127.0.0.1:0")
            .await
            .expect("artifact listener binds");
        let address = listener.local_addr().expect("listener has address");
        let server = tokio::spawn(serve_artifact_http(
            listener,
            store.clone(),
            catalog.clone(),
            Arc::new(AllowTestMemoryAdmission),
        ));
        let endpoint = format!("http://{address}");
        let client = node_service::ArtifactClient::new(&endpoint, 4, 1024, 1_000, 1_000)
            .expect("Artifact client config is valid");
        let bytes = b"cross-node-spatial-memory";
        let source = directory.path().join("node-a-map.bin");
        std::fs::write(&source, bytes).expect("Node A source writes");
        let digest = digest_bytes(bytes);
        let manifest = fixture_memory_manifest(
            "provider-map-a",
            MemoryKind::Spatial,
            &digest,
            bytes.len() as u64,
        );
        let wrong_reference = MemoryArtifactRef::new(
            ContentDigest::new("f".repeat(64)).expect("wrong digest is structurally valid"),
            bytes.len() as u64,
        );
        assert!(matches!(
            client.upload_memory_file(&source, &wrong_reference).await,
            Err(node_service::ArtifactError::DigestMismatch { .. })
        ));
        assert!(
            !store
                .contains(&digest)
                .expect("failed preflight must leave no CAS blob")
        );
        client
            .upload_memory_file(
                &source,
                manifest
                    .artifact()
                    .expect("exchangeable manifest has bytes"),
            )
            .await
            .expect("Node A uploads CAS bytes");
        client
            .publish_memory_manifest(
                &manifest,
                &NodeId::new("dog-a").expect("Node A id is valid"),
                "session-a",
            )
            .await
            .expect("Node A publishes Memory metadata");

        let config: NodeServiceConfig = serde_json::from_value(serde_json::json!({
            "schema": "roboguide.node-config/v0.6",
            "node_id": "dog-b",
            "server_endpoint": "http://127.0.0.1:1",
            "state_directory": directory.path().join("node-b-state"),
            "local_systems": [{
                "id": "memory",
                "runtime_name": "fixture-memory",
                "runtime_version": "1",
                "health": {
                    "step": {"id": "health", "connection": "local", "operation": {"kind": "http", "method": "GET", "path": "/health"}},
                    "state_pointer": "/state", "online": ["READY"], "degraded": ["DEGRADED"], "offline": ["OFFLINE"]
                }
            }],
            "connections": [{"driver": "http", "id": "local", "local_system": "memory", "endpoint": "http://127.0.0.1:2", "timeout_ms": 1000}],
            "capabilities": [{
                "contract": "memory.import@v1", "kind": "compute", "owner": "memory",
                "readiness": {"step": {"id": "ready", "connection": "local", "operation": {"kind": "http", "method": "GET", "path": "/ready"}}, "state_pointer": "/state", "ready": ["READY"], "unavailable": ["OFFLINE"]},
                "workflow": {
                    "execute": [{"id": "execute", "connection": "local", "operation": {"kind": "http", "method": "POST", "path": "/execute"}}],
                    "status": [{"id": "status", "connection": "local", "operation": {"kind": "http", "method": "GET", "path": "/status"}}],
                    "cancel": [{"id": "cancel", "connection": "local", "operation": {"kind": "http", "method": "POST", "path": "/cancel"}}],
                    "local_handle": {"kind": "pointer", "pointer": "/steps/execute/run_id"},
                    "execution_state": {"state_pointer": "/steps/status/state", "accepted": ["ACCEPTED"], "running": ["RUNNING"], "completed": ["COMPLETED"], "failed": ["FAILED"], "cancelled": ["CANCELLED"]}
                }
            }],
            "artifacts": {"endpoint": endpoint, "cache_directory": "artifact-cache", "max_artifact_bytes": 1024, "chunk_size_bytes": 4, "connect_timeout_ms": 1000, "read_timeout_ms": 1000},
            "memory_providers": [{
                "id": "local-spatial", "owner": "memory", "kind": "spatial", "scope": "global", "visibility": "exchangeable", "payload_schema": "example.memory/v1", "media_type": "application/octet-stream", "storage_directory": "memory-index",
                "import": {"steps": [{
                    "id": "import-memory", "connection": "local", "operation": {"kind": "http", "method": "POST", "path": "/memory/import"},
                    "request": {"base": {}, "bindings": [
                        {"target": "/manifest", "value": {"kind": "pointer", "pointer": "/invocation/memory_manifest"}},
                        {"target": "/staged_path", "value": {"kind": "pointer", "pointer": "/invocation/staged_artifact"}}
                    ]}
                }]}
            }]
        }))
        .expect("Node B config decodes");
        let compiled = node_service::CompiledLocalCatalog::compile(config, directory.path())
            .expect("Node B catalog compiles");
        let imports = Arc::new(std::sync::atomic::AtomicUsize::new(0));
        let engine = LocalIntegrationEngine::new(
            compiled,
            vec![Arc::new(NoopHttpDriver {
                imports: Arc::clone(&imports),
            }) as Arc<dyn LocalDriver>],
        )
        .expect("Node B engine starts");
        let incompatible = fixture_memory_manifest(
            "semantic-memory-a",
            MemoryKind::Semantic,
            &digest,
            bytes.len() as u64,
        );
        assert!(matches!(
            engine
                .exchange_memory(
                    "local-spatial",
                    &incompatible,
                    "session-b",
                    serde_json::json!({}),
                )
                .await,
            Err(node_service::EngineError::Configuration(_))
        ));
        assert_eq!(imports.load(std::sync::atomic::Ordering::SeqCst), 0);
        engine
            .exchange_memory(
                "local-spatial",
                &manifest,
                "session-b",
                serde_json::json!({}),
            )
            .await
            .expect("Node B selectively imports verified bytes");
        let staged = engine
            .artifact_stager()
            .expect("Artifact data plane is configured")
            .memory_cache_path(manifest.selector());
        assert_eq!(std::fs::read(&staged).expect("staged bytes read"), bytes);
        assert_eq!(imports.load(std::sync::atomic::Ordering::SeqCst), 1);
        std::fs::remove_file(&staged).expect("test removes transfer cache after local import");
        engine
            .exchange_memory(
                "local-spatial",
                &manifest,
                "session-b",
                serde_json::json!({}),
            )
            .await
            .expect("same selective import is retry-idempotent");
        assert_eq!(imports.load(std::sync::atomic::Ordering::SeqCst), 1);
        assert!(
            !staged.exists(),
            "provider-owned import retry must not reconstruct the transfer cache"
        );
        assert_eq!(
            engine
                .discover_memories(
                    "local-spatial",
                    &MemoryQuery::default(),
                    serde_json::json!({}),
                )
                .await
                .expect("Node B local index discovers import"),
            vec![manifest.clone()]
        );
        assert_eq!(
            catalog
                .memory_replicas(manifest.selector())
                .expect("replica evidence reads")[0]
                .status(),
            domain::MemoryReplicaStatus::Imported
        );
        server.abort();
    }

    /// Exchangeable Memory publication fails closed when referenced CAS bytes are absent.
    #[tokio::test]
    async fn generic_memory_publication_requires_existing_verified_cas_bytes() {
        let directory = tempfile::tempdir().expect("temporary directory exists");
        let store = FileSystemArtifactStore::new(directory.path().join("artifacts"))
            .expect("CAS initializes");
        let event_log =
            SqliteEventLog::open(directory.path().join("events.sqlite3")).expect("event log opens");
        seed_controller_checkpoint(&event_log);
        let catalog = ArtifactCatalog::replay_with_gate(&event_log, Arc::new(Mutex::new(())))
            .expect("catalog replays");
        let manifest = fixture_memory_manifest(
            "missing-a",
            MemoryKind::Artifact,
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            12,
        );
        let body = serde_json::to_vec(&manifest).expect("manifest serializes");

        response_body(
            &request_once(
                &store,
                &catalog,
                &test_uploads(),
                raw_request("POST", "/v1/memories/missing-a/revisions/r1", &body, true),
            )
            .await,
            "404 Not Found",
        );
        assert!(
            catalog
                .memories()
                .expect("catalog remains readable")
                .is_empty()
        );
    }

    /// HTTP publication cannot bypass the composition-owned provider admission port.
    #[tokio::test]
    async fn generic_memory_publication_requires_admitted_provider() {
        let directory = tempfile::tempdir().expect("temporary directory exists");
        let store = FileSystemArtifactStore::new(directory.path().join("artifacts"))
            .expect("CAS initializes");
        let bytes = b"memory-provider-admission";
        let digest = digest_bytes(bytes);
        let mut upload = store
            .begin_upload("admission-fixture")
            .expect("upload begins");
        upload.write_chunk(bytes).expect("artifact bytes write");
        upload
            .finalize(&digest, bytes.len() as u64)
            .expect("artifact finalizes");
        let event_log =
            SqliteEventLog::open(directory.path().join("events.sqlite3")).expect("event log opens");
        seed_controller_checkpoint(&event_log);
        let catalog = ArtifactCatalog::replay_with_gate(&event_log, Arc::new(Mutex::new(())))
            .expect("catalog replays");
        let manifest = fixture_memory_manifest(
            "experience-a",
            MemoryKind::Experience,
            &digest,
            bytes.len() as u64,
        );
        let body = serde_json::to_vec(&manifest).expect("manifest serializes");

        response_body(
            &request_once_with_admission(
                &store,
                &catalog,
                &test_uploads(),
                raw_request(
                    "POST",
                    "/v1/memories/experience-a/revisions/r1",
                    &body,
                    true,
                ),
                Arc::new(DenyTestMemoryAdmission),
            )
            .await,
            "403 Forbidden",
        );
        assert!(
            catalog
                .memories()
                .expect("catalog remains readable")
                .is_empty()
        );
    }

    /// Generic Memory publication is fenced to the current session of its semantic owner Node.
    #[tokio::test]
    async fn generic_memory_publication_requires_current_owner_session() {
        let directory = tempfile::tempdir().expect("temporary directory exists");
        let store = FileSystemArtifactStore::new(directory.path().join("artifacts"))
            .expect("CAS initializes");
        let bytes = b"memory-publisher-session";
        let digest = digest_bytes(bytes);
        let mut upload = store
            .begin_upload("session-fixture")
            .expect("upload begins");
        upload.write_chunk(bytes).expect("artifact bytes write");
        upload
            .finalize(&digest, bytes.len() as u64)
            .expect("artifact finalizes");
        let event_log =
            SqliteEventLog::open(directory.path().join("events.sqlite3")).expect("event log opens");
        seed_controller_checkpoint(&event_log);
        let catalog = ArtifactCatalog::replay_with_gate(&event_log, Arc::new(Mutex::new(())))
            .expect("catalog replays");
        let manifest = fixture_memory_manifest(
            "session-memory",
            MemoryKind::Experience,
            &digest,
            bytes.len() as u64,
        );
        let body = serde_json::to_vec(&manifest).expect("manifest serializes");
        let path = "/v1/memories/session-memory/revisions/r1";
        let admission: Arc<dyn MemoryProviderAdmission> = Arc::new(FixtureSessionMemoryAdmission);

        for request in [
            raw_request("POST", path, &body, true),
            raw_memory_request(path, &body, "dog-b", "session-current"),
            raw_memory_request(path, &body, "dog-a", "session-old"),
        ] {
            response_body(
                &request_once_with_admission(
                    &store,
                    &catalog,
                    &test_uploads(),
                    request,
                    Arc::clone(&admission),
                )
                .await,
                "403 Forbidden",
            );
            assert!(
                catalog
                    .memories()
                    .expect("catalog remains readable")
                    .is_empty()
            );
        }

        response_body(
            &request_once_with_admission(
                &store,
                &catalog,
                &test_uploads(),
                raw_memory_request(path, &body, "dog-a", "session-current"),
                admission,
            )
            .await,
            "201 Created",
        );
        assert_eq!(catalog.memories().expect("catalog is readable").len(), 1);

        let replica = serde_json::to_vec(&serde_json::json!({
            "manifest": manifest,
            "node_id": "dog-b",
            "consumer_provider_id": "fixture-consumer",
            "status": "staged",
        }))
        .expect("replica request serializes");
        let replica_path = "/v1/memories/session-memory/revisions/r1/replicas";
        response_body(
            &request_once_with_admission(
                &store,
                &catalog,
                &test_uploads(),
                raw_memory_request(replica_path, &replica, "dog-a", "session-current"),
                Arc::new(FixtureSessionMemoryAdmission),
            )
            .await,
            "403 Forbidden",
        );
        assert!(
            catalog
                .memory_replicas(manifest.selector())
                .expect("replicas remain readable")
                .is_empty()
        );
        let wrong_provider_replica = serde_json::to_vec(&serde_json::json!({
            "manifest": manifest,
            "node_id": "dog-b",
            "consumer_provider_id": "wrong-provider",
            "status": "staged",
        }))
        .expect("replica request serializes");
        response_body(
            &request_once_with_admission(
                &store,
                &catalog,
                &test_uploads(),
                raw_memory_request(
                    replica_path,
                    &wrong_provider_replica,
                    "dog-b",
                    "session-current",
                ),
                Arc::new(FixtureSessionMemoryAdmission),
            )
            .await,
            "403 Forbidden",
        );
        response_body(
            &request_once_with_admission(
                &store,
                &catalog,
                &test_uploads(),
                raw_memory_request(replica_path, &replica, "dog-b", "session-current"),
                Arc::new(FixtureSessionMemoryAdmission),
            )
            .await,
            "202 Accepted",
        );
        assert_eq!(
            catalog
                .memory_replicas(manifest.selector())
                .expect("replicas are readable")
                .len(),
            1
        );
    }

    /// Typed maps and generic Memory cannot claim the same unified selector in either order.
    #[test]
    fn typed_and_generic_memory_selectors_are_mutually_exclusive() {
        let first_directory = tempfile::tempdir().expect("temporary directory exists");
        let first_log = SqliteEventLog::open(first_directory.path().join("events.sqlite3"))
            .expect("event log opens");
        seed_controller_checkpoint(&first_log);
        let first_catalog = ArtifactCatalog::replay_with_gate(&first_log, Arc::new(Mutex::new(())))
            .expect("catalog replays");
        let digest = "a".repeat(64);
        let map = fixture_manifest(&digest, 12);
        let generic = fixture_memory_manifest("map-a", MemoryKind::Artifact, &digest, 12);
        first_catalog
            .append(EventPayload::MapArtifactPublished {
                manifest: map.clone(),
            })
            .expect("typed map claims selector first");
        assert_eq!(
            first_catalog
                .append(EventPayload::MemoryManifestPublished {
                    manifest: generic.clone(),
                })
                .expect_err("generic Memory must not reuse typed selector")
                .status(),
            "409 Conflict"
        );

        let second_directory = tempfile::tempdir().expect("temporary directory exists");
        let second_log = SqliteEventLog::open(second_directory.path().join("events.sqlite3"))
            .expect("event log opens");
        seed_controller_checkpoint(&second_log);
        let second_catalog =
            ArtifactCatalog::replay_with_gate(&second_log, Arc::new(Mutex::new(())))
                .expect("catalog replays");
        second_catalog
            .append(EventPayload::MemoryManifestPublished { manifest: generic })
            .expect("generic Memory claims selector first");
        assert_eq!(
            second_catalog
                .append(EventPayload::MapArtifactPublished { manifest: map })
                .expect_err("typed map must not reuse generic selector")
                .status(),
            "409 Conflict"
        );
    }

    /// Fixed-length framing rejects ambiguous requests and separates head and body size limits.
    #[test]
    fn request_head_framing_is_bounded_and_unambiguous() {
        let cases = [
            (
                b"POST /v1/artifact-uploads HTTP/1.1\r\nHost: localhost\r\n\r\n".as_slice(),
                "400 Bad Request",
            ),
            (
                b"POST /v1/artifact-uploads HTTP/1.1\r\nContent-Length: 0\r\nContent-Length: 0\r\n\r\n"
                    .as_slice(),
                "400 Bad Request",
            ),
            (
                b"POST /v1/artifact-uploads HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n"
                    .as_slice(),
                "400 Bad Request",
            ),
            (
                b"POST /v1/artifact-uploads HTTP/1.0\r\nContent-Length: 0\r\n\r\n".as_slice(),
                "400 Bad Request",
            ),
            (
                b"POST /v1/artifact-uploads HTTP/1.1\r\nMalformed\r\nContent-Length: 0\r\n\r\n"
                    .as_slice(),
                "400 Bad Request",
            ),
            (
                b"POST /v1/artifact-uploads HTTP/1.1\r\nContent-Length: 2\r\n\r\nabc"
                    .as_slice(),
                "400 Bad Request",
            ),
            (
                b"POST /v1/artifact-uploads HTTP/1.1\r\nContent-Length: +1\r\n\r\n"
                    .as_slice(),
                "400 Bad Request",
            ),
            (
                b"POST /v1/memories/a/revisions/r1 HTTP/1.1\r\nContent-Length: 0\r\nX-RoboGuide-Node-Id: dog-a\r\n\r\n"
                    .as_slice(),
                "400 Bad Request",
            ),
            (
                b"POST /v1/memories/a/revisions/r1 HTTP/1.1\r\nContent-Length: 0\r\nX-RoboGuide-Session-Id: session-a\r\n\r\n"
                    .as_slice(),
                "400 Bad Request",
            ),
        ];
        for (bytes, expected_status) in cases {
            let header_end = bytes
                .windows(4)
                .position(|window| window == b"\r\n\r\n")
                .map(|index| index + 4)
                .expect("fixture has complete headers");
            let error = parse_request_head(bytes.to_vec(), header_end)
                .expect_err("ambiguous framing is rejected");
            assert_eq!(error.status(), expected_status);
        }

        let oversized_length = format!(
            "POST /v1/artifact-uploads HTTP/1.1\r\nContent-Length: {}\r\n\r\n",
            MAX_ARTIFACT_BYTES + 1
        )
        .into_bytes();
        let oversized_length_end = oversized_length
            .windows(4)
            .position(|window| window == b"\r\n\r\n")
            .map(|index| index + 4)
            .expect("fixture has complete headers");
        assert_eq!(
            parse_request_head(oversized_length, oversized_length_end)
                .expect_err("oversized body is rejected")
                .status(),
            "413 Payload Too Large"
        );

        let mut oversized_header = b"GET /healthz HTTP/1.1\r\nX-Pad: ".to_vec();
        oversized_header.resize(MAX_HEADER_BYTES, b'a');
        oversized_header.extend_from_slice(b"\r\n\r\n");
        let oversized_header_end = oversized_header.len();
        assert_eq!(
            parse_request_head(oversized_header, oversized_header_end)
                .expect_err("oversized headers are rejected")
                .status(),
            "431 Request Header Fields Too Large"
        );

        let body = vec![b'x'; MAX_HEADER_BYTES + 1];
        let mut large_prefetch = format!(
            "PUT /v1/artifact-uploads/upload/content HTTP/1.1\r\nContent-Length: {}\r\n\r\n",
            body.len()
        )
        .into_bytes();
        let large_prefetch_header_end = large_prefetch.len();
        large_prefetch.extend_from_slice(&body);
        let parsed = parse_request_head(large_prefetch, large_prefetch_header_end)
            .expect("a bounded header may be followed by a large body prefix");
        assert_eq!(parsed.content_length, body.len() as u64);
        assert_eq!(parsed.prefetched_body, body);
    }

    /// Header delimiters and fixed-length JSON/artifact bodies may arrive in separate TCP reads.
    #[tokio::test]
    async fn fragmented_headers_and_bodies_are_read_to_declared_length() {
        let directory = tempfile::tempdir().expect("temporary directory exists");
        let store = FileSystemArtifactStore::new(directory.path()).expect("CAS initializes");
        let event_log =
            SqliteEventLog::open(directory.path().join("events.sqlite3")).expect("event log opens");
        seed_controller_checkpoint(&event_log);
        let catalog = ArtifactCatalog::replay_with_gate(&event_log, Arc::new(Mutex::new(())))
            .expect("catalog replays");
        let uploads = test_uploads();

        let start_body = br#"{"upload_id":"fragmented"}"#;
        let start_head = format!(
            "POST /v1/artifact-uploads HTTP/1.1\r\nHost: localhost\r\nContent-Length: {}\r\nConnection: close\r\n\r",
            start_body.len()
        );
        let start_fragments = [
            start_head.as_bytes(),
            b"\n{".as_slice(),
            &start_body[1..12],
            &start_body[12..],
        ];
        response_body(
            &fragmented_request_once(&store, &catalog, &uploads, &start_fragments).await,
            "201 Created",
        );

        let artifact = b"map-body-arrives-in-several-packets";
        let content_head = format!(
            "PUT /v1/artifact-uploads/fragmented/content HTTP/1.1\r\nHost: localhost\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
            artifact.len()
        );
        let content_fragments = [
            content_head.as_bytes(),
            &artifact[..3],
            &artifact[3..17],
            &artifact[17..],
        ];
        let response =
            fragmented_request_once(&store, &catalog, &uploads, &content_fragments).await;
        let response: serde_json::Value =
            serde_json::from_slice(&response_body(&response, "202 Accepted"))
                .expect("append response is JSON");
        assert_eq!(response["received_bytes"], artifact.len() as u64);

        let finalize_body = serde_json::to_vec(&serde_json::json!({
            "content_digest": digest_bytes(artifact),
            "byte_size": artifact.len(),
        }))
        .expect("finalize body serializes");
        let finalize = raw_request(
            "POST",
            "/v1/artifact-uploads/fragmented/finalize",
            &finalize_body,
            true,
        );
        response_body(
            &request_once(&store, &catalog, &uploads, finalize).await,
            "201 Created",
        );
    }

    /// JSON endpoints reject their smaller body limit before attempting to wait for payload bytes.
    #[tokio::test]
    async fn json_body_limit_returns_payload_too_large() {
        let directory = tempfile::tempdir().expect("temporary directory exists");
        let store = FileSystemArtifactStore::new(directory.path()).expect("CAS initializes");
        let event_log =
            SqliteEventLog::open(directory.path().join("events.sqlite3")).expect("event log opens");
        seed_controller_checkpoint(&event_log);
        let catalog = ArtifactCatalog::replay_with_gate(&event_log, Arc::new(Mutex::new(())))
            .expect("catalog replays");
        let uploads = test_uploads();
        let request = format!(
            "POST /v1/artifact-uploads HTTP/1.1\r\nHost: localhost\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
            MAX_JSON_BODY_BYTES + 1
        )
        .into_bytes();
        response_body(
            &request_once(&store, &catalog, &uploads, request).await,
            "413 Payload Too Large",
        );
    }

    /// Registry quotas reserve declared bytes atomically and expiration removes staged files.
    #[test]
    fn upload_registry_enforces_count_bytes_and_idle_expiration() {
        let directory = tempfile::tempdir().expect("temporary directory exists");
        let store = FileSystemArtifactStore::new(directory.path()).expect("CAS initializes");
        let now = Instant::now();
        let mut registry = UploadRegistry::with_limits(2, 5, Duration::from_secs(10));
        let first = store.begin_upload("first").expect("first upload begins");
        registry
            .insert_new("first".to_string(), first, now)
            .expect("first upload enters registry");
        let second = store.begin_upload("second").expect("second upload begins");
        registry
            .insert_new("second".to_string(), second, now)
            .expect("second upload enters registry");
        let excess = store
            .begin_upload("excess")
            .expect("temporary upload begins");
        let error = registry
            .insert_new("excess".to_string(), excess, now)
            .expect_err("count quota rejects a third identity");
        assert_eq!(error.status(), "429 Too Many Requests");
        assert!(!store.root().join("staging").join("excess.partial").exists());

        let mut first = registry
            .take_for_append("first", 4)
            .expect("four bytes fit aggregate quota");
        first
            .session
            .upload
            .write_chunk(b"map1")
            .expect("reserved bytes write");
        registry
            .restore_after_append("first".to_string(), first, now)
            .expect("completed append restores session");
        let error = match registry.take_for_append("second", 2) {
            Ok(_) => panic!("aggregate byte quota must reject declared body"),
            Err(error) => error,
        };
        assert_eq!(error.status(), "413 Payload Too Large");
        assert_eq!(registry.active_bytes, 4);

        let expired_at = now
            .checked_add(Duration::from_secs(10))
            .expect("test instant can advance");
        assert_eq!(
            registry.expire_idle(expired_at).expect("expiry succeeds"),
            2
        );
        assert_eq!(registry.active_uploads(), 0);
        assert_eq!(registry.active_bytes, 0);
        assert!(!store.root().join("staging").join("first.partial").exists());
        assert!(!store.root().join("staging").join("second.partial").exists());
    }

    /// An interrupted body and a mismatched finalize both abort staging and release all quota.
    #[tokio::test]
    async fn upload_errors_abort_session_and_release_registry_quota() {
        let directory = tempfile::tempdir().expect("temporary directory exists");
        let store = FileSystemArtifactStore::new(directory.path()).expect("CAS initializes");
        let event_log =
            SqliteEventLog::open(directory.path().join("events.sqlite3")).expect("event log opens");
        seed_controller_checkpoint(&event_log);
        let catalog = ArtifactCatalog::replay_with_gate(&event_log, Arc::new(Mutex::new(())))
            .expect("catalog replays");
        let uploads = test_uploads();

        let interrupted_start = Request {
            path: "/v1/artifact-uploads".to_string(),
            body: br#"{"upload_id":"interrupted"}"#.to_vec(),
            memory_publisher: None,
        };
        start_upload(&interrupted_start, &store, &uploads).expect("upload starts");
        let interrupted = raw_request_with_declared_length(
            "PUT",
            "/v1/artifact-uploads/interrupted/content",
            b"ab",
            4,
        );
        response_body(
            &request_once(&store, &catalog, &uploads, interrupted).await,
            "400 Bad Request",
        );
        {
            let registry = uploads.lock().expect("registry remains readable");
            assert_eq!(registry.active_uploads(), 0);
            assert_eq!(registry.active_bytes, 0);
        }
        assert!(
            !store
                .root()
                .join("staging")
                .join("interrupted.partial")
                .exists()
        );

        let mismatch_start = raw_request(
            "POST",
            "/v1/artifact-uploads",
            br#"{"upload_id":"mismatch"}"#,
            true,
        );
        response_body(
            &request_once(&store, &catalog, &uploads, mismatch_start).await,
            "201 Created",
        );
        let content = raw_request("PUT", "/v1/artifact-uploads/mismatch/content", b"map", true);
        response_body(
            &request_once(&store, &catalog, &uploads, content).await,
            "202 Accepted",
        );
        let finalize_body = serde_json::to_vec(&serde_json::json!({
            "content_digest": digest_bytes(b"different"),
            "byte_size": 3,
        }))
        .expect("finalize body serializes");
        let finalize = raw_request(
            "POST",
            "/v1/artifact-uploads/mismatch/finalize",
            &finalize_body,
            true,
        );
        response_body(
            &request_once(&store, &catalog, &uploads, finalize).await,
            "409 Conflict",
        );
        {
            let registry = uploads.lock().expect("registry remains readable");
            assert_eq!(registry.active_uploads(), 0);
            assert_eq!(registry.active_bytes, 0);
        }
        assert!(
            !store
                .root()
                .join("staging")
                .join("mismatch.partial")
                .exists()
        );
        let explicit_start = raw_request(
            "POST",
            "/v1/artifact-uploads",
            br#"{"upload_id":"explicit-abort"}"#,
            true,
        );
        response_body(
            &request_once(&store, &catalog, &uploads, explicit_start).await,
            "201 Created",
        );
        let explicit_abort =
            raw_request("DELETE", "/v1/artifact-uploads/explicit-abort", &[], false);
        response_body(
            &request_once(&store, &catalog, &uploads, explicit_abort).await,
            "200 OK",
        );
        let registry = uploads.lock().expect("registry remains readable");
        assert_eq!(registry.active_uploads(), 0);
        assert_eq!(registry.active_bytes, 0);
    }

    /// Exercises Node A map export through Node B import and strong localization verification.
    #[tokio::test]
    async fn spatial_map_cross_node_exchange_reaches_strong_verification() {
        let directory = tempfile::tempdir().expect("temporary directory exists");
        let store = FileSystemArtifactStore::new(directory.path()).expect("CAS initializes");
        let event_path = directory.path().join("events.sqlite3");
        let event_log = SqliteEventLog::open(&event_path).expect("event log opens");
        seed_controller_checkpoint(&event_log);
        let catalog = ArtifactCatalog::replay_with_gate(&event_log, Arc::new(Mutex::new(())))
            .expect("catalog replays");
        let uploads = test_uploads();
        let bytes = b"map-bytes";
        let digest = digest_bytes(bytes);

        let start = raw_request(
            "POST",
            "/v1/artifact-uploads",
            br#"{"upload_id":"upload-test"}"#,
            true,
        );
        response_body(
            &request_once(&store, &catalog, &uploads, start).await,
            "201 Created",
        );
        let content = raw_request(
            "PUT",
            "/v1/artifact-uploads/upload-test/content",
            bytes,
            true,
        );
        response_body(
            &request_once(&store, &catalog, &uploads, content).await,
            "202 Accepted",
        );
        let finalize_body = serde_json::to_vec(&serde_json::json!({
            "content_digest": digest,
            "byte_size": bytes.len(),
        }))
        .expect("finalize body serializes");
        let finalize = raw_request(
            "POST",
            "/v1/artifact-uploads/upload-test/finalize",
            &finalize_body,
            true,
        );
        response_body(
            &request_once(&store, &catalog, &uploads, finalize).await,
            "201 Created",
        );

        let manifest = fixture_manifest(&digest, bytes.len() as u64);
        let publish_body = serde_json::to_vec(&manifest).expect("manifest serializes");
        let publish = raw_request("POST", "/v1/maps/map-a/revisions/r1", &publish_body, true);
        response_body(
            &request_once(&store, &catalog, &uploads, publish).await,
            "201 Created",
        );

        let get_manifest = raw_request("GET", "/v1/maps/map-a/revisions/r1", &[], false);
        let manifest_body = response_body(
            &request_once(&store, &catalog, &uploads, get_manifest).await,
            "200 OK",
        );
        let manifest_json: serde_json::Value =
            serde_json::from_slice(&manifest_body).expect("manifest response is JSON");
        assert_eq!(manifest_json["status"], "Published");

        let list_memories = raw_request("GET", "/v1/memories", &[], false);
        let memories_body = response_body(
            &request_once(&store, &catalog, &uploads, list_memories).await,
            "200 OK",
        );
        let memories_json: serde_json::Value =
            serde_json::from_slice(&memories_body).expect("memory catalog response is JSON");
        assert_eq!(memories_json["memories"][0]["kind"], "spatial");
        assert_eq!(memories_json["memories"][0]["typed_extension"], "map");
        assert_eq!(
            memories_json["memories"][0]["artifact"]["content_digest"],
            digest
        );

        let memory_detail = raw_request("GET", "/v1/memories/map-a/revisions/r1", &[], false);
        let memory_detail_body = response_body(
            &request_once(&store, &catalog, &uploads, memory_detail).await,
            "200 OK",
        );
        let memory_detail_json: serde_json::Value = serde_json::from_slice(&memory_detail_body)
            .expect("typed map Memory detail response is JSON");
        assert_eq!(memory_detail_json["manifest"]["typed_extension"], "map");
        assert_eq!(
            memory_detail_json["manifest"]["selector"]["memory_id"],
            "map-a"
        );
        assert_eq!(
            memory_detail_json["replicas"].as_array().map(Vec::len),
            Some(0)
        );

        let get_blob = raw_request("GET", &format!("/v1/artifacts/{digest}"), &[], false);
        let blob_body = response_body(
            &request_once(&store, &catalog, &uploads, get_blob).await,
            "200 OK",
        );
        assert_eq!(blob_body, bytes);

        let staged_body = serde_json::to_vec(&serde_json::json!({
            "manifest": manifest,
            "node_id": "dog-b",
            "mission_id": "mission-b",
            "status": "staged"
        }))
        .expect("replica body serializes");
        let staged = raw_request(
            "POST",
            "/v1/maps/map-a/revisions/r1/replicas",
            &staged_body,
            true,
        );
        response_body(
            &request_once(&store, &catalog, &uploads, staged).await,
            "202 Accepted",
        );
        let imported_body = serde_json::to_vec(&serde_json::json!({
            "manifest": manifest,
            "node_id": "dog-b",
            "mission_id": "mission-b",
            "status": "imported"
        }))
        .expect("replica body serializes");
        let imported = raw_request(
            "POST",
            "/v1/maps/map-a/revisions/r1/replicas",
            &imported_body,
            true,
        );
        response_body(
            &request_once(&store, &catalog, &uploads, imported).await,
            "202 Accepted",
        );
        let evidence_body = serde_json::to_vec(&serde_json::json!({
            "schema": "roboguide.localization-verification-evidence/v0.1",
            "map_id": "map-a",
            "revision_id": "r1",
            "content_digest": digest,
            "byte_size": bytes.len(),
            "mission_id": "mission-b",
            "task_id": "verify-map",
            "group_id": "group-b",
            "role_id": "localizer",
            "node_id": "dog-b",
            "execution_id": "execution-verify",
            "local_attempt_id": "local-verify-1",
            "active_local_map_id": "map-a-local",
            "mode": "localization",
            "pose_quality": {
                "metric": "translation_stddev",
                "value": "0.08",
                "threshold": "0.10",
                "unit": "m",
                "comparison": "at_most"
            },
            "frames": {"map": "map", "odom": "odom", "base": "base_link"},
            "anchor_id": "anchor-lab",
            "source_observed_at_ms": 50
        }))
        .expect("localization evidence serializes");
        let evidence = raw_request(
            "POST",
            "/v1/maps/map-a/revisions/r1/localization-evidence",
            &evidence_body,
            true,
        );
        response_body(
            &request_once(&store, &catalog, &uploads, evidence).await,
            "201 Created",
        );
        let selector = MapRevisionSelector::new(
            MapId::new("map-a").expect("map id"),
            MapRevisionId::new("r1").expect("revision id"),
        );
        let projection =
            MapCatalogProjection::from_events(event_log.decoded_events().expect("events decode"))
                .expect("catalog projection rebuilds");
        assert_eq!(
            projection.replicas(&selector)[0].status(),
            MapReplicaStatus::Verified
        );
        assert!(projection.replicas(&selector)[0].is_strongly_verified());
        let reopened = SqliteEventLog::open(&event_path).expect("event log reopens");
        let checkpoint = reopened
            .load_checkpoint()
            .expect("checkpoint reads")
            .expect("checkpoint remains present");
        assert_eq!(checkpoint.event_sequence, 4);
        assert_eq!(
            checkpoint.event_sequence,
            reopened.latest_sequence().expect("log head reads")
        );
        assert_eq!(checkpoint.schema, TEST_CHECKPOINT_SCHEMA);
        assert_eq!(checkpoint.checkpoint_json, TEST_CHECKPOINT_JSON);
    }

    /// Publishing a manifest without a finalized CAS blob is rejected before catalog mutation.
    #[tokio::test]
    async fn publish_requires_existing_blob() {
        let directory = tempfile::tempdir().expect("temporary directory exists");
        let store = FileSystemArtifactStore::new(directory.path()).expect("CAS initializes");
        let event_log =
            SqliteEventLog::open(directory.path().join("events.sqlite3")).expect("event log opens");
        seed_controller_checkpoint(&event_log);
        let catalog = ArtifactCatalog::replay_with_gate(&event_log, Arc::new(Mutex::new(())))
            .expect("catalog replays");
        let uploads = test_uploads();
        let digest = digest_bytes(b"missing");
        let manifest = fixture_manifest(&digest, 7);
        let body = serde_json::to_vec(&manifest).expect("manifest serializes");
        let request = raw_request("POST", "/v1/maps/map-a/revisions/r1", &body, true);
        response_body(
            &request_once(&store, &catalog, &uploads, request).await,
            "404 Not Found",
        );
        assert!(event_log.is_empty().expect("event log reads"));
        assert!(
            catalog
                .revisions()
                .expect("catalog remains readable")
                .is_empty()
        );
        let checkpoint = event_log
            .load_checkpoint()
            .expect("checkpoint reads")
            .expect("checkpoint remains present");
        assert_eq!(checkpoint.event_sequence, 0);
        assert_eq!(checkpoint.checkpoint_json, TEST_CHECKPOINT_JSON);
    }

    /// Publication rejects a same-size CAS replacement whose bytes no longer match its digest.
    #[tokio::test]
    async fn publish_rehashes_the_finalized_blob_before_catalog_mutation() {
        let directory = tempfile::tempdir().expect("temporary directory exists");
        let store = FileSystemArtifactStore::new(directory.path()).expect("CAS initializes");
        let event_log =
            SqliteEventLog::open(directory.path().join("events.sqlite3")).expect("event log opens");
        seed_controller_checkpoint(&event_log);
        let catalog = ArtifactCatalog::replay_with_gate(&event_log, Arc::new(Mutex::new(())))
            .expect("catalog replays");
        let uploads = test_uploads();
        let bytes = b"map-bytes";
        let digest = digest_bytes(bytes);
        let mut upload = store.begin_upload("upload-corrupt").expect("upload begins");
        upload.write_chunk(bytes).expect("map bytes write");
        let artifact = upload
            .finalize(&digest, bytes.len() as u64)
            .expect("upload finalizes");
        std::fs::remove_file(artifact.path()).expect("test removes sealed blob");
        std::fs::write(artifact.path(), b"bad-bytes")
            .expect("test replaces blob with same-size corruption");

        let manifest = fixture_manifest(&digest, bytes.len() as u64);
        let body = serde_json::to_vec(&manifest).expect("manifest serializes");
        let request = raw_request("POST", "/v1/maps/map-a/revisions/r1", &body, true);
        response_body(
            &request_once(&store, &catalog, &uploads, request).await,
            "409 Conflict",
        );
        assert!(event_log.is_empty().expect("event log reads"));
        assert!(
            catalog
                .revisions()
                .expect("catalog remains readable")
                .is_empty()
        );
    }

    /// Spatial evidence refuses to create controller authority when no checkpoint exists.
    #[test]
    fn append_requires_an_existing_controller_checkpoint() {
        let directory = tempfile::tempdir().expect("temporary directory exists");
        let event_log =
            SqliteEventLog::open(directory.path().join("events.sqlite3")).expect("event log opens");
        let catalog = ArtifactCatalog::replay_with_gate(&event_log, Arc::new(Mutex::new(())))
            .expect("catalog replays");
        let manifest = fixture_manifest(&digest_bytes(b"map"), 3);

        let error = catalog
            .append(EventPayload::MapArtifactPublished { manifest })
            .expect_err("missing controller checkpoint must fail closed");

        assert_eq!(error.status(), "500 Internal Server Error");
        assert!(event_log.is_empty().expect("event log reads"));
        assert!(
            catalog
                .revisions()
                .expect("catalog remains readable")
                .is_empty()
        );
    }

    /// Spatial evidence refuses to conceal a checkpoint that already trails the event log.
    #[test]
    fn append_rejects_a_divergent_controller_checkpoint() {
        let directory = tempfile::tempdir().expect("temporary directory exists");
        let event_log =
            SqliteEventLog::open(directory.path().join("events.sqlite3")).expect("event log opens");
        seed_controller_checkpoint(&event_log);
        let manifest = fixture_manifest(&digest_bytes(b"map"), 3);
        let correlation =
            domain::CorrelationId::new("divergent-test").expect("correlation identity is valid");
        let mut divergent_log = event_log.clone();
        divergent_log.append(
            TimestampMs::new(1),
            &correlation,
            None,
            EventPayload::MapArtifactPublished {
                manifest: manifest.clone(),
            },
        );
        assert!(
            divergent_log
                .take_error()
                .expect("append health reads")
                .is_none()
        );
        let catalog = ArtifactCatalog::replay_with_gate(&event_log, Arc::new(Mutex::new(())))
            .expect("catalog replays the divergent log");

        let error = catalog
            .append(EventPayload::MapArtifactPublished { manifest })
            .expect_err("divergent controller checkpoint must fail closed");

        assert_eq!(error.status(), "500 Internal Server Error");
        assert_eq!(event_log.latest_sequence().expect("log head reads"), 1);
        let checkpoint = event_log
            .load_checkpoint()
            .expect("checkpoint reads")
            .expect("checkpoint remains present");
        assert_eq!(checkpoint.event_sequence, 0);
        assert_eq!(
            catalog.revisions().expect("catalog remains readable").len(),
            1
        );
    }

    /// Spatial evidence ordering remains monotonic when the local wall clock moves backward.
    #[test]
    fn append_allocates_timestamp_after_persisted_high_water() {
        let directory = tempfile::tempdir().expect("temporary directory exists");
        let event_log =
            SqliteEventLog::open(directory.path().join("events.sqlite3")).expect("event log opens");
        seed_controller_checkpoint(&event_log);
        let catalog = ArtifactCatalog::replay_with_gate(&event_log, Arc::new(Mutex::new(())))
            .expect("catalog replays");
        let forced_future = TimestampMs::new(9_000_000_000_000);
        *catalog
            .timestamp_high_water
            .lock()
            .expect("timestamp high-water lock remains healthy") = forced_future;
        let manifest = fixture_manifest(&digest_bytes(b"map"), 3);
        catalog
            .append(EventPayload::MapArtifactPublished {
                manifest: manifest.clone(),
            })
            .expect("publication appends after forced high-water");
        catalog
            .append(EventPayload::MapArtifactStaged {
                manifest: manifest.clone(),
                node_id: NodeId::new("dog-b").expect("node id is valid"),
                mission_id: MissionId::new("mission-import").expect("mission id is valid"),
            })
            .expect("later replica evidence remains ordered");

        let events = event_log.decoded_events().expect("durable events decode");
        assert_eq!(events.len(), 2);
        assert!(events[0].timestamp() > forced_future);
        assert!(events[1].timestamp() > events[0].timestamp());
        assert_eq!(
            catalog
                .projection
                .lock()
                .expect("catalog lock remains healthy")
                .replicas(manifest.selector())[0]
                .status(),
            MapReplicaStatus::Staged
        );
    }

    /// An uncertain catalog commit fences both reads and writes until replay on restart.
    #[test]
    fn recovery_fence_rejects_stale_projection_access() {
        let directory = tempfile::tempdir().expect("temporary directory exists");
        let event_log =
            SqliteEventLog::open(directory.path().join("events.sqlite3")).expect("event log opens");
        seed_controller_checkpoint(&event_log);
        let catalog = ArtifactCatalog::replay_with_gate(&event_log, Arc::new(Mutex::new(())))
            .expect("catalog replays");
        let fenced = catalog.fence("simulated commit uncertainty");
        assert_eq!(fenced.status(), "503 Service Unavailable");
        assert!(matches!(
            catalog.revisions(),
            Err(error) if error.status() == "503 Service Unavailable"
        ));
        assert!(matches!(
            catalog.append(EventPayload::MapArtifactPublished {
                manifest: fixture_manifest(&digest_bytes(b"map"), 3),
            }),
            Err(error) if error.status() == "503 Service Unavailable"
        ));
    }
}
