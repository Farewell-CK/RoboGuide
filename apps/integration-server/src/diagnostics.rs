//! Optional local JSONL diagnostics with bounded memory/files; observation never decides authority.

use ports::{
    DiagnosticHandle, DiagnosticIdentity, DiagnosticKind, DiagnosticObserver,
    DiagnosticQueueSnapshot, DiagnosticRecord, DiagnosticStage,
};
use std::collections::BTreeMap;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, mpsc};
use std::time::{Duration, Instant};

/// Maximum records waiting for the diagnostic writer, independent of protocol queues.
const RECORD_CAPACITY: usize = 1024;
/// Maximum extra session/category/stage dimensions retained in one summary window.
const SESSION_DIMENSIONS: usize = 64;
/// Maximum bytes in each retained JSONL file; only current and previous are retained.
const FILE_LIMIT_BYTES: u64 = 8 * 1024 * 1024;
/// Maximum time between diagnostic summary flushes.
const FLUSH_INTERVAL: Duration = Duration::from_secs(5);

/// RAII duration observation which records both successful and rejected/failed work.
pub(crate) struct StageObservation {
    /// Optional sink selected before the observed operation.
    diagnostics: DiagnosticHandle,
    /// Fixed message kind.
    kind: DiagnosticKind,
    /// Observed stage.
    stage: DiagnosticStage,
    /// Optional exact session attribution.
    identity: Option<DiagnosticIdentity>,
    /// Disabled observers take no monotonic timing sample.
    started: Option<Instant>,
}

impl StageObservation {
    /// Starts a payload-free observation; it never reads the authority Clock.
    pub(crate) fn start(
        diagnostics: &DiagnosticHandle,
        kind: DiagnosticKind,
        stage: DiagnosticStage,
        identity: Option<&DiagnosticIdentity>,
    ) -> Self {
        let enabled = diagnostics.is_enabled();
        Self {
            diagnostics: diagnostics.clone(),
            kind,
            stage,
            identity: enabled.then(|| identity.cloned()).flatten(),
            started: enabled.then(Instant::now),
        }
    }
}

impl Drop for StageObservation {
    /// Sends one best-effort sample on every exit, outside any diagnostic file I/O.
    fn drop(&mut self) {
        if let Some(started) = self.started {
            self.diagnostics.timing(
                self.kind,
                self.stage,
                started.elapsed(),
                self.identity.as_ref(),
            );
        }
    }
}

/// Non-blocking producer; a full, failed or stopped writer only loses diagnostic records.
struct JsonlObserver {
    /// Bounded diagnostic-only channel.
    sender: mpsc::SyncSender<DiagnosticRecord>,
    /// Cumulative dropped record count reported by successful later summaries.
    dropped: Arc<AtomicU64>,
}

impl DiagnosticObserver for JsonlObserver {
    /// Uses try-send so an observation cannot wait for log I/O or reduce application availability.
    fn try_record(&self, record: DiagnosticRecord) {
        if self.sender.try_send(record).is_err() {
            self.dropped.fetch_add(1, Ordering::Relaxed);
        }
    }
}

/// Process-lifetime guard for optional observation; no background thread exists when disabled.
pub(crate) struct DiagnosticWriter {
    /// Handle injected into transport and application composition.
    handle: DiagnosticHandle,
    /// Cooperative stop flag, unrelated to Node or Mission cancellation.
    stop: Arc<AtomicBool>,
    /// Optional dedicated blocking-I/O thread.
    worker: Option<std::thread::JoinHandle<()>>,
}

impl DiagnosticWriter {
    /// Starts optional diagnostics from a deployment-owned path; writer failure is observation-only.
    pub(crate) fn configured() -> Self {
        match std::env::var_os("ROBOGUIDE_DIAGNOSTICS_PATH") {
            Some(path) if !path.is_empty() => Self::start(PathBuf::from(path)),
            _ => Self::disabled(),
        }
    }

    /// Creates a handle with no observer, thread, file or queue bookkeeping.
    fn disabled() -> Self {
        Self {
            handle: DiagnosticHandle::default(),
            stop: Arc::new(AtomicBool::new(false)),
            worker: None,
        }
    }

    /// Spawns a dedicated bounded writer; filesystem failure does not escape into Controller work.
    fn start(path: PathBuf) -> Self {
        let (sender, receiver) = mpsc::sync_channel(RECORD_CAPACITY);
        let stop = Arc::new(AtomicBool::new(false));
        let dropped = Arc::new(AtomicU64::new(0));
        let observer = Arc::new(JsonlObserver {
            sender,
            dropped: dropped.clone(),
        });
        let worker_stop = stop.clone();
        match std::thread::Builder::new()
            .name("roboguide-diagnostics".into())
            .spawn(move || write_summaries(&path, receiver, worker_stop, dropped))
        {
            Ok(worker) => Self {
                handle: DiagnosticHandle::new(observer),
                stop,
                worker: Some(worker),
            },
            Err(error) => {
                eprintln!("local diagnostics writer unavailable: {error}");
                Self::disabled()
            }
        }
    }

    /// Returns a cloneable best-effort observer; it is never part of a durable checkpoint.
    pub(crate) fn handle(&self) -> DiagnosticHandle {
        self.handle.clone()
    }

    /// Flushes retained observations at a controlled shutdown/test boundary, outside async workers.
    pub(crate) fn finish(mut self) {
        self.stop.store(true, Ordering::Release);
        if let Some(worker) = self.worker.take() {
            let _ = worker.join();
        }
    }
}

impl Drop for DiagnosticWriter {
    /// Signals shutdown without blocking application cleanup on a filesystem operation.
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Release);
    }
}

/// Fixed logarithmic duration histogram and independent scalar/queue samples.
struct Summary {
    /// Total observation records, including enqueue-only snapshots.
    records: u64,
    /// Number of actual elapsed-duration samples.
    duration_count: u64,
    /// Sum of elapsed microseconds.
    duration_sum: u64,
    /// Minimum elapsed sample.
    duration_min: u64,
    /// Maximum elapsed sample.
    duration_max: u64,
    /// Exact fixed bucket counts; bucket upper bounds are documented approximations.
    histogram: [u64; 65],
    /// Number of independent scalar samples.
    value_count: u64,
    /// Sum of independent scalar samples.
    value_sum: u64,
    /// Most recent scalar sample.
    latest_value: Option<u64>,
    /// Most recent queue totals/age uncertainty.
    queue: Option<DiagnosticQueueSnapshot>,
}

impl Default for Summary {
    /// Creates fixed-size empty aggregation without retained raw sample arrays.
    fn default() -> Self {
        Self {
            records: 0,
            duration_count: 0,
            duration_sum: 0,
            duration_min: u64::MAX,
            duration_max: 0,
            histogram: [0; 65],
            value_count: 0,
            value_sum: 0,
            latest_value: None,
            queue: None,
        }
    }
}

impl Summary {
    /// Merges one sample without retaining identity-independent payload or dynamic timing arrays.
    fn observe(&mut self, record: &DiagnosticRecord) {
        self.records = self.records.saturating_add(1);
        if let Some(value) = record.duration_us {
            self.duration_count = self.duration_count.saturating_add(1);
            self.duration_sum = self.duration_sum.saturating_add(value);
            self.duration_min = self.duration_min.min(value);
            self.duration_max = self.duration_max.max(value);
            let bucket = if value == 0 {
                0
            } else {
                (u64::BITS - value.leading_zeros()) as usize
            };
            self.histogram[bucket] = self.histogram[bucket].saturating_add(1);
        }
        if let Some(value) = record.value {
            self.value_count = self.value_count.saturating_add(1);
            self.value_sum = self.value_sum.saturating_add(value);
            self.latest_value = Some(value);
        }
        if let Some(queue) = record.queue {
            self.queue = Some(queue);
        }
    }

    /// Returns a fixed-bucket quantile upper bound, never an exact raw percentile claim.
    fn quantile_upper(&self, percent: u64) -> Option<u64> {
        if self.duration_count == 0 {
            return None;
        }
        let target = self.duration_count.saturating_mul(percent).div_ceil(100);
        let mut count: u64 = 0;
        for (bucket, samples) in self.histogram.iter().enumerate() {
            count = count.saturating_add(*samples);
            if count >= target {
                return Some(if bucket == 64 {
                    u64::MAX
                } else {
                    (1u64 << bucket).saturating_sub(1)
                });
            }
        }
        None
    }

    /// Serializes only bounded metric information and explicit queue-age uncertainty.
    fn to_json(&self) -> serde_json::Value {
        serde_json::json!({
            "records": self.records,
            "duration_us": { "samples": self.duration_count, "sum": self.duration_sum,
                "min": (self.duration_count > 0).then_some(self.duration_min), "max": (self.duration_count > 0).then_some(self.duration_max),
                "p50_upper": self.quantile_upper(50), "p95_upper": self.quantile_upper(95), "p99_upper": self.quantile_upper(99), "log2_buckets": self.histogram.to_vec() },
            "value": { "samples": self.value_count, "sum": self.value_sum, "latest": self.latest_value },
            "queue": self.queue.map(|queue| serde_json::json!({ "depth": queue.depth, "bytes": queue.bytes, "oldest_age_us": queue.oldest_age_us })),
        })
    }
}

/// One finite aggregation window with globally fixed labels and bounded session dimensions.
#[derive(Default)]
struct Window {
    /// Global summaries by fixed protocol category and stage.
    global: BTreeMap<(DiagnosticKind, DiagnosticStage), Summary>,
    /// Bounded exact session summaries, never a durable inventory.
    sessions: BTreeMap<(DiagnosticIdentity, DiagnosticKind, DiagnosticStage), Summary>,
    /// Count of samples whose extra session dimension was not retained.
    omitted_dimensions: u64,
}

impl Window {
    /// Retains all aggregate counts but caps extra identity dimensions and rejects oversized identity.
    fn observe(&mut self, record: DiagnosticRecord) {
        self.global
            .entry((record.kind, record.stage))
            .or_default()
            .observe(&record);
        if let Some(identity) = &record.identity {
            let bounded = DiagnosticIdentity::bounded(&identity.node_id, &identity.session_id);
            if let Some(identity) = bounded {
                let key = (identity, record.kind, record.stage);
                if self.sessions.contains_key(&key) || self.sessions.len() < SESSION_DIMENSIONS {
                    self.sessions.entry(key).or_default().observe(&record);
                    return;
                }
            }
            self.omitted_dimensions = self.omitted_dimensions.saturating_add(1);
        }
    }

    /// Builds a single bounded JSONL summary; all omitted/failed observation counts remain visible.
    fn to_json(&self, elapsed: Duration, dropped: u64, io_errors: u64) -> serde_json::Value {
        let mut metrics = Vec::with_capacity(self.global.len() + self.sessions.len());
        for ((kind, stage), summary) in &self.global {
            metrics.push(serde_json::json!({"kind": kind.as_str(), "stage": stage.as_str(), "identity": null, "summary": summary.to_json()}));
        }
        for ((identity, kind, stage), summary) in &self.sessions {
            metrics.push(serde_json::json!({"kind": kind.as_str(), "stage": stage.as_str(), "identity": {"node_id": identity.node_id, "session_id": identity.session_id}, "summary": summary.to_json()}));
        }
        serde_json::json!({"schema": "roboguide.local-diagnostics/v0.1", "pid": std::process::id(), "window_ms": elapsed.as_millis() as u64, "dropped_records_total": dropped, "io_errors_total": io_errors, "omitted_session_dimensions": self.omitted_dimensions, "metrics": metrics})
    }
}

/// Appends one bounded summary, retaining only current and previous files.
fn append_summary(path: &Path, bytes: &[u8], limit: u64) -> std::io::Result<()> {
    if bytes.len() as u64 > limit {
        return Err(std::io::Error::other(
            "diagnostic summary exceeds file bound",
        ));
    }
    let size = std::fs::metadata(path)
        .map(|metadata| metadata.len())
        .unwrap_or(0);
    if size.saturating_add(bytes.len() as u64 + 1) > limit {
        let mut previous = path.as_os_str().to_os_string();
        previous.push(".1");
        let previous = PathBuf::from(previous);
        match std::fs::remove_file(&previous) {
            Ok(()) => {}
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Err(error) => return Err(error),
        }
        if path.exists() {
            std::fs::rename(path, previous)?;
        }
    }
    let mut file = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(path)?;
    file.write_all(bytes)?;
    file.write_all(b"\n")?;
    file.flush()
}

/// Performs all diagnostic file work on a dedicated thread, flushing at finite intervals or stop.
fn write_summaries(
    path: &Path,
    receiver: mpsc::Receiver<DiagnosticRecord>,
    stop: Arc<AtomicBool>,
    dropped: Arc<AtomicU64>,
) {
    let mut window = Window::default();
    let mut started = Instant::now();
    let mut io_errors = 0u64;
    loop {
        let timeout = FLUSH_INTERVAL
            .saturating_sub(started.elapsed())
            .min(Duration::from_millis(100));
        let disconnected = match receiver.recv_timeout(timeout) {
            Ok(record) => {
                window.observe(record);
                false
            }
            Err(mpsc::RecvTimeoutError::Timeout) => false,
            Err(mpsc::RecvTimeoutError::Disconnected) => true,
        };
        let stopping = stop.load(Ordering::Acquire) || disconnected;
        if stopping {
            for _ in 0..RECORD_CAPACITY {
                match receiver.try_recv() {
                    Ok(record) => window.observe(record),
                    Err(_) => break,
                }
            }
        }
        if started.elapsed() >= FLUSH_INTERVAL || stopping {
            if !window.global.is_empty() {
                let json = window.to_json(
                    started.elapsed(),
                    dropped.load(Ordering::Relaxed),
                    io_errors,
                );
                let result = serde_json::to_vec(&json)
                    .map_err(std::io::Error::other)
                    .and_then(|bytes| append_summary(path, &bytes, FILE_LIMIT_BYTES));
                if result.is_err() {
                    io_errors = io_errors.saturating_add(1);
                }
            }
            window = Window::default();
            started = Instant::now();
        }
        if stopping {
            break;
        }
    }
}

/// Observes accepted health age and Control lease remaining without deriving authority from them.
pub(crate) fn observe_node_ages(
    bridge: &orchestration::IntegrationRuntimeBridge<state::SqliteEventLog>,
    now: domain::TimestampMs,
) {
    use ports::SharedNodeStateReader;
    let diagnostics = bridge.diagnostics();
    if !diagnostics.is_enabled() {
        return;
    }
    for node in bridge.state().nodes() {
        let identity = bridge
            .diagnostic_session(node.node_id().as_str())
            .ok()
            .flatten();
        let diagnostic_identity = identity
            .as_ref()
            .and_then(|(session, _)| DiagnosticIdentity::bounded(node.node_id().as_str(), session));
        diagnostics.timing(
            DiagnosticKind::Timer,
            DiagnosticStage::HealthAge,
            Duration::from_millis(
                now.as_millis()
                    .saturating_sub(node.reported_status_received_at().as_millis()),
            ),
            diagnostic_identity.as_ref(),
        );
        if let Some(lease) = bridge.control().node_lease(node.node_id()) {
            diagnostics.timing(
                DiagnosticKind::Timer,
                DiagnosticStage::ControlLeaseRemaining,
                Duration::from_millis(
                    lease
                        .expires_at()
                        .as_millis()
                        .saturating_sub(now.as_millis()),
                ),
                diagnostic_identity.as_ref(),
            );
        }
        if let Some((_, age)) = identity {
            diagnostics.timing(
                DiagnosticKind::Timer,
                DiagnosticStage::TransportAge,
                age,
                diagnostic_identity.as_ref(),
            );
        }
    }
}

/// Measures HTTP checkpoint serialization without changing the wrapper, schema or error result.
pub(crate) fn http_checkpoint(
    controller: &crate::ControllerState,
) -> Result<String, Box<dyn std::error::Error + Send + Sync>> {
    let diagnostics = controller.bridge.diagnostics();
    let _serialization = StageObservation::start(
        diagnostics,
        DiagnosticKind::Http,
        DiagnosticStage::Serialize,
        None,
    );
    let json = crate::server_checkpoint_json(controller)?;
    diagnostics.value(
        DiagnosticKind::Http,
        DiagnosticStage::CheckpointBytes,
        json.len() as u64,
        None,
    );
    Ok(json)
}

/// Times an HTTP-owned checkpoint update, preserving its existing authority transaction.
pub(crate) fn http_save(
    log: &state::SqliteEventLog,
    checkpoint: &str,
    diagnostics: &DiagnosticHandle,
) -> Result<(), state::SqliteEventLogError> {
    let _save = StageObservation::start(
        diagnostics,
        DiagnosticKind::Http,
        DiagnosticStage::Save,
        None,
    );
    log.save_checkpoint(crate::SERVER_CHECKPOINT_SCHEMA, checkpoint)
}

/// Times an HTTP-owned SQLite commit, without converting or swallowing persistence failures.
pub(crate) fn http_commit(
    log: &state::SqliteEventLog,
    diagnostics: &DiagnosticHandle,
) -> Result<(), state::SqliteEventLogError> {
    let _commit = StageObservation::start(
        diagnostics,
        DiagnosticKind::Http,
        DiagnosticStage::Commit,
        None,
    );
    log.commit_batch()
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Creates payload-free samples with exactly bound synthetic session identity.
    fn record(index: usize) -> DiagnosticRecord {
        DiagnosticRecord {
            kind: DiagnosticKind::Heartbeat,
            stage: DiagnosticStage::Clone,
            duration_us: Some(index as u64),
            value: None,
            identity: DiagnosticIdentity::bounded("test-node", &format!("session-{index}")),
            queue: None,
        }
    }

    /// Aggregation bounds identities and distinguishes actual samples from queue snapshots.
    #[test]
    fn bounded_session_histograms_keep_global_sample_count() {
        let mut window = Window::default();
        for index in 0..1024 {
            window.observe(record(index));
        }
        assert_eq!(window.sessions.len(), SESSION_DIMENSIONS);
        assert_eq!(window.omitted_dimensions, 1024 - SESSION_DIMENSIONS as u64);
        let summary = &window.global[&(DiagnosticKind::Heartbeat, DiagnosticStage::Clone)];
        assert_eq!(summary.duration_count, 1024);
        assert_eq!(summary.duration_min, 0);
        assert_eq!(summary.duration_max, 1023);
        assert_eq!(summary.quantile_upper(95), Some(1023));
    }

    /// File rotation retains only two files, and a failed destination never becomes an authority error.
    #[test]
    fn rotation_and_failed_sink_are_observation_only() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("metrics.jsonl");
        append_summary(&path, b"first", 12).unwrap();
        append_summary(&path, b"second", 12).unwrap();
        append_summary(&path, b"third", 12).unwrap();
        assert_eq!(std::fs::read(&path).unwrap(), b"third\n");
        assert_eq!(
            std::fs::read(path.with_extension("jsonl.1")).unwrap(),
            b"second\n"
        );
        let writer = DiagnosticWriter::start(directory.path().join("missing/metrics.jsonl"));
        writer.handle().record(record(1));
        writer.finish();
    }

    /// A full writer queue discards diagnostics rather than blocking producers.
    #[test]
    fn full_diagnostic_queue_never_blocks() {
        let (sender, _receiver) = mpsc::sync_channel(1);
        let dropped = Arc::new(AtomicU64::new(0));
        let observer = JsonlObserver {
            sender,
            dropped: dropped.clone(),
        };
        observer.try_record(record(1));
        observer.try_record(record(2));
        assert_eq!(dropped.load(Ordering::Relaxed), 1);
    }

    /// Runs a real lease-expiry/SQLite/checkpoint/outbox timer transaction with synthetic Node facts.
    fn timer_trace(diagnostics: DiagnosticHandle) -> (String, Vec<state::PersistedEvent>) {
        let directory = tempfile::tempdir().unwrap();
        let log = state::SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
        let mut control = control::ControlPlane::new();
        let mut shared = state::InMemorySharedNodeState::new();
        control
            .register_node(
                &mut shared,
                domain::NodeRegistration::new(
                    domain::NodeId::new("diagnostic-node").unwrap(),
                    domain::LocalRuntime::new("synthetic", "1").unwrap(),
                    domain::NodeContractVersion::v0_1(),
                    vec![domain::Capability::new(
                        domain::CapabilityKind::Compute,
                        true,
                    )],
                    Vec::new(),
                ),
                domain::NodeStatus::new(domain::NodeHealth::Online, domain::TimestampMs::new(1)),
                domain::TimestampMs::new(1),
                &domain::CorrelationId::new("diagnostic-trace").unwrap(),
                &mut log.clone(),
            )
            .unwrap();
        let mut bridge = orchestration::IntegrationRuntimeBridge::new(
            control,
            shared,
            log.clone(),
            integration::GrpcNodeRouter::default(),
        );
        bridge.set_diagnostics(diagnostics);
        let controller = Arc::new(std::sync::Mutex::new(crate::ControllerState {
            mission_admissions: BTreeMap::new(),
            bridge,
            orchestrator: orchestration::MissionOrchestrator::new(),
            verifier_seen: Default::default(),
            verifier_source_digest: None,
        }));
        crate::drive_application_timer(
            &controller,
            &log,
            &Arc::new(std::sync::Mutex::new(())),
            domain::TimestampMs::new(20_000),
            None,
        )
        .unwrap();
        (
            log.load_checkpoint().unwrap().unwrap().checkpoint_json,
            log.events().unwrap(),
        )
    }

    /// Disabled, functioning, full and failing sinks produce byte-identical authoritative evidence.
    #[test]
    fn diagnostic_sinks_do_not_change_durable_timer_trace() {
        let expected = timer_trace(DiagnosticHandle::default());
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("diagnostics.jsonl");
        let working = DiagnosticWriter::start(path.clone());
        assert_eq!(timer_trace(working.handle()), expected);
        working.finish();
        let summaries = std::fs::read_to_string(path).unwrap();
        let json: serde_json::Value = serde_json::from_str(summaries.trim()).unwrap();
        assert_eq!(json["schema"], "roboguide.local-diagnostics/v0.1");
        let failing = DiagnosticWriter::start(directory.path().join("missing/diagnostics.jsonl"));
        assert_eq!(timer_trace(failing.handle()), expected);
        failing.finish();
        let (sender, _receiver) = mpsc::sync_channel(1);
        let observer = Arc::new(JsonlObserver {
            sender,
            dropped: Arc::new(AtomicU64::new(0)),
        });
        assert_eq!(
            timer_trace(DiagnosticHandle::new(observer.clone())),
            expected
        );
        assert!(observer.dropped.load(Ordering::Relaxed) > 0);
    }
}
