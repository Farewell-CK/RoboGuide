//! Durable artifact and Memory catalog projection with Controller admission.

use super::*;
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
    pub(super) node_id: NodeId,
    /// Current session identity issued after Controller registration acceptance.
    pub(super) session_id: String,
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
    pub(super) projection: Arc<Mutex<MapCatalogProjection>>,
    /// Generic non-map Memory metadata sharing the same evidence log and CAS.
    pub(super) memory_projection: Arc<Mutex<MemoryCatalogProjection>>,
    /// Durable evidence sink used for map lifecycle events.
    pub(super) event_log: SqliteEventLog,
    /// Process-local serializer shared with the controller's event batches.
    pub(super) write_gate: Arc<Mutex<()>>,
    /// Last event timestamp allocated under the shared write serializer.
    pub(super) timestamp_high_water: Arc<Mutex<TimestampMs>>,
    /// Process-local recovery fence set when a durable catalog commit outcome is uncertain.
    pub(super) recovery_fence: Arc<Mutex<Option<String>>>,
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
    pub(super) fn ensure_available(&self) -> Result<(), HttpError> {
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
    pub(super) fn fence(&self, reason: impl Into<String>) -> HttpError {
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
    pub(super) fn rollback_failed_batch(&self, log: &SqliteEventLog, reason: String) -> HttpError {
        match log.rollback_batch() {
            Ok(()) => HttpError::internal(reason),
            Err(rollback) => self.fence(format!(
                "{reason}; rollback outcome is uncertain: {rollback}"
            )),
        }
    }

    /// Reads one revision snapshot without exposing mutable State to the transport layer.
    pub(super) fn revision(
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
    pub(super) fn revisions(&self) -> Result<Vec<domain::MapRevisionSnapshot>, HttpError> {
        self.ensure_available()?;
        let projection = self
            .projection
            .lock()
            .map_err(|_| HttpError::internal("spatial catalog lock is poisoned"))?;
        Ok(projection.revisions())
    }

    /// Reads typed map replica evidence in deterministic node order.
    pub(super) fn map_replicas(
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
    pub(super) fn memories(&self) -> Result<Vec<MemoryArtifactManifest>, HttpError> {
        self.ensure_available()?;
        self.memory_projection
            .lock()
            .map_err(|_| HttpError::internal("generic Memory catalog lock is poisoned"))
            .map(|projection| projection.memories())
    }

    /// Reads one generic Memory manifest without exposing catalog mutation.
    pub(super) fn memory(
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
    pub(super) fn memory_replicas(
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
    pub(super) fn append(&self, payload: EventPayload) -> Result<TimestampMs, HttpError> {
        self.append_with_admission(payload, None)
    }

    /// Appends one generic Memory event after admission under the shared Controller write gate.
    pub(super) fn append_memory(
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
    pub(super) fn append_node_evidence(
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
    pub(super) fn next_timestamp(&self) -> Result<TimestampMs, HttpError> {
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
