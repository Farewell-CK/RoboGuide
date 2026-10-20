//! Bounded, observation-only diagnostics; no record grants authority or changes fact acceptance.

use domain::TimestampMs;
use std::collections::BTreeMap;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

/// Maximum retained queue timestamps; omitted age is explicit when observation is incomplete.
const MAX_TRACKED_QUEUE_TIMESTAMPS: usize = 1024;

/// Fixed diagnostic message categories, independent of provider payload contents.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum DiagnosticKind {
    /// New session registration.
    Registration,
    /// Lease/health heartbeat.
    Heartbeat,
    /// Complete registration update.
    RegistrationUpdate,
    /// State observation batch.
    State,
    /// Peer readiness fact.
    PeerReadiness,
    /// Durable Node command receipt.
    Receipt,
    /// Execution lifecycle event or snapshot.
    Execution,
    /// Route loss observation.
    Unavailable,
    /// Other admitted protocol fact.
    Other,
    /// Application timer transaction.
    Timer,
    /// Application HTTP transaction.
    Http,
}

impl DiagnosticKind {
    /// Returns a fixed spelling for local diagnostics, never a retry/authority disposition.
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Registration => "registration",
            Self::Heartbeat => "heartbeat",
            Self::RegistrationUpdate => "registration_update",
            Self::State => "state",
            Self::PeerReadiness => "peer_readiness",
            Self::Receipt => "receipt",
            Self::Execution => "execution",
            Self::Unavailable => "unavailable",
            Self::Other => "other",
            Self::Timer => "timer",
            Self::Http => "http",
        }
    }
}

/// Fixed costs and evidence ages observed separately from the authoritative event stream.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub enum DiagnosticStage {
    /// Complete internal HTTP request handling, including response delivery.
    Request,
    /// Validated reader-to-session queue.
    IngressQueue,
    /// Session-to-application queue.
    ApplicationQueue,
    /// Controller write gate wait.
    WriteGate,
    /// Controller mutex wait.
    ControllerLock,
    /// Private transaction candidate clone.
    Clone,
    /// Fact reduction and derived application transitions.
    Reduce,
    /// Full checkpoint serialization.
    Serialize,
    /// Encoded checkpoint size.
    CheckpointBytes,
    /// Number of original inputs sharing one application checkpoint transaction.
    BatchFacts,
    /// Encoded baseline/delta bytes in one attempted checkpoint storage save, excluding page overhead.
    CheckpointWriteBytes,
    /// Whether one attempted checkpoint save used a delta, represented as zero or one.
    CheckpointDelta,
    /// SQLite checkpoint update.
    Save,
    /// SQLite transaction commit.
    Commit,
    /// Awaited application completion.
    Completion,
    /// Ingress-to-ACK emission latency.
    Ack,
    /// Timer scheduling lag.
    TimerLag,
    /// Post-commit command outbox flush.
    Outbox,
    /// Transport-only heartbeat age.
    TransportAge,
    /// Remaining Control lease interval.
    ControlLeaseRemaining,
    /// Age of last application-accepted health evidence.
    HealthAge,
}

impl DiagnosticStage {
    /// Returns a stable local metric spelling.
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Request => "http_request",
            Self::IngressQueue => "ingress_queue",
            Self::ApplicationQueue => "application_queue",
            Self::WriteGate => "write_gate",
            Self::ControllerLock => "controller_lock",
            Self::Clone => "clone",
            Self::Reduce => "reduce",
            Self::Serialize => "serialize",
            Self::CheckpointBytes => "checkpoint_bytes",
            Self::BatchFacts => "batch_facts",
            Self::CheckpointWriteBytes => "checkpoint_write_bytes",
            Self::CheckpointDelta => "checkpoint_delta",
            Self::Save => "sqlite_save",
            Self::Commit => "sqlite_commit",
            Self::Completion => "completion",
            Self::Ack => "ack",
            Self::TimerLag => "timer_lag",
            Self::Outbox => "outbox",
            Self::TransportAge => "transport_heartbeat_age",
            Self::ControlLeaseRemaining => "control_lease_remaining",
            Self::HealthAge => "health_age",
        }
    }
}

/// Exact session attribution, bounded independently of arbitrary input identifiers.
#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct DiagnosticIdentity {
    /// Complete Node identifier; absence is preferable to a fabricated/truncated identity.
    pub node_id: String,
    /// Complete current session identity.
    pub session_id: String,
}

impl DiagnosticIdentity {
    /// Retains exact bounded identifiers; oversized identifiers omit attribution without rejection.
    pub fn bounded(node_id: &str, session_id: &str) -> Option<Self> {
        (node_id.len() <= 256 && session_id.len() <= 256).then(|| Self {
            node_id: node_id.into(),
            session_id: session_id.into(),
        })
    }
}

/// A queue snapshot with explicit uncertainty when timestamp tracking reaches its bound.
#[derive(Clone, Copy, Debug, Default)]
pub struct DiagnosticQueueSnapshot {
    /// Current queued items, including those without a retained timestamp.
    pub depth: u64,
    /// Encoded wire bytes queued, never a copy of the payload.
    pub bytes: u64,
    /// Oldest enqueue age in local microseconds, unavailable when tracking is incomplete.
    pub oldest_age_us: Option<u64>,
}

/// Fixed metadata sent through a best-effort sink, separate from authority and payload bytes.
#[derive(Clone, Debug)]
pub struct DiagnosticRecord {
    /// Protocol/application category.
    pub kind: DiagnosticKind,
    /// Observed cost or evidence age.
    pub stage: DiagnosticStage,
    /// Optional duration sample; enqueue-only snapshots do not synthesize zero-latency samples.
    pub duration_us: Option<u64>,
    /// Optional scalar sample such as checkpoint byte count.
    pub value: Option<u64>,
    /// Exact bounded session attribution when available.
    pub identity: Option<DiagnosticIdentity>,
    /// Optional queue state after enqueue or dequeue.
    pub queue: Option<DiagnosticQueueSnapshot>,
}

/// Fixed microsecond boundaries shared by observation producers and composition renderers.
pub const DIAGNOSTIC_DURATION_BUCKETS_US: [u64; 12] = [
    100, 500, 1_000, 5_000, 10_000, 50_000, 100_000, 500_000, 1_000_000, 5_000_000, 10_000_000,
    30_000_000,
];

/// One bounded fixed-label aggregate; optional data is never synthesized from another category.
#[derive(Clone, Debug)]
pub struct DiagnosticAggregate {
    /// Original fixed message category.
    pub kind: DiagnosticKind,
    /// Original fixed stage.
    pub stage: DiagnosticStage,
    /// Actual duration samples observed, excluding scalar/queue-only records.
    pub duration_count: u64,
    /// Saturating total of actual local elapsed microseconds.
    pub duration_sum_us: u64,
    /// Cumulative duration counts at the fixed boundaries above, excluding infinity.
    pub duration_buckets: [u64; 12],
    /// Last actual scalar sample; missing remains unavailable.
    pub value: Option<u64>,
}

/// One queue-stage total whose completeness is explicit, independent of current application authority.
#[derive(Clone, Debug)]
pub struct DiagnosticQueueAggregate {
    /// Fixed queue stage, never an arbitrary user label.
    pub stage: DiagnosticStage,
    /// Sum of the actual tracked live queues; incomplete totals are only a lower bound.
    pub depth: u64,
    /// Sum of tracked queued wire bytes.
    pub bytes: u64,
    /// Oldest tracked queue age, unavailable when any contributing timestamp is missing.
    pub oldest_age_us: Option<u64>,
    /// False when observer loss, missing identity or a tracking bound prevents complete coverage.
    pub complete: bool,
}

/// Bounded observation-only export; no source identity/payload or authority timestamp is exposed.
#[derive(Clone, Debug)]
pub struct DiagnosticAggregateSnapshot {
    /// Fixed category/stage statistical observations, bounded by the enum product.
    pub aggregates: Vec<DiagnosticAggregate>,
    /// Aggregate transport queue observations, independent of Task ownership.
    pub queues: Vec<DiagnosticQueueAggregate>,
    /// Actual best-effort records omitted due to contention or capacity.
    pub dropped: u64,
}

/// Non-blocking diagnostic observation port; failure must never reach application authority.
pub trait DiagnosticObserver: Send + Sync {
    /// Attempts to retain a bounded record; implementations may discard it without affecting work.
    fn try_record(&self, record: DiagnosticRecord);

    /// Optionally exports a bounded observation snapshot; absence never means zero failures.
    fn snapshot(&self) -> Option<DiagnosticAggregateSnapshot> {
        None
    }
}

/// Optional observation handle; disabled construction retains no sink or queue bookkeeping.
#[derive(Clone, Default)]
pub struct DiagnosticHandle {
    /// Best-effort sink, absent on the compatibility/default path.
    observer: Option<Arc<dyn DiagnosticObserver>>,
}

impl std::fmt::Debug for DiagnosticHandle {
    /// Shows only whether observation is enabled, without printing sink configuration.
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("DiagnosticHandle")
            .field("enabled", &self.is_enabled())
            .finish()
    }
}

impl DiagnosticHandle {
    /// Reads a bounded optional aggregate without interpreting it as permission or fresh State.
    pub fn snapshot(&self) -> Option<DiagnosticAggregateSnapshot> {
        self.observer
            .as_ref()
            .and_then(|observer| observer.snapshot())
    }
    /// Enables a deployment-owned sink whose implementation follows the non-blocking contract.
    pub fn new(observer: Arc<dyn DiagnosticObserver>) -> Self {
        Self {
            observer: Some(observer),
        }
    }

    /// Returns whether record construction and queue bookkeeping are needed.
    pub fn is_enabled(&self) -> bool {
        self.observer.is_some()
    }

    /// Sends a prebuilt diagnostic through the optional observer; no authority result is returned.
    pub fn record(&self, record: DiagnosticRecord) {
        if let Some(observer) = &self.observer {
            observer.try_record(record);
        }
    }

    /// Records elapsed monotonic time without interpreting the operation outcome.
    pub fn timing(
        &self,
        kind: DiagnosticKind,
        stage: DiagnosticStage,
        elapsed: Duration,
        identity: Option<&DiagnosticIdentity>,
    ) {
        if self.is_enabled() {
            self.record(DiagnosticRecord {
                kind,
                stage,
                duration_us: Some(microseconds(elapsed)),
                value: None,
                identity: identity.cloned(),
                queue: None,
            });
        }
    }

    /// Records an independent scalar; it cannot refresh any evidence or change a policy.
    pub fn value(
        &self,
        kind: DiagnosticKind,
        stage: DiagnosticStage,
        value: u64,
        identity: Option<&DiagnosticIdentity>,
    ) {
        if self.is_enabled() {
            self.record(DiagnosticRecord {
                kind,
                stage,
                duration_us: None,
                value: Some(value),
                identity: identity.cloned(),
                queue: None,
            });
        }
    }
}

/// Original receive metadata; O3 observation alone never changes application authority timestamps.
#[derive(Clone, Debug)]
pub struct NodeFactMetadata {
    /// RoboGuide-local transport receive time from the injected process Clock.
    pub received_at: TimestampMs,
    /// Monotonic instant used only for local duration measurement.
    pub received_instant: Instant,
    /// Fixed message category.
    pub kind: DiagnosticKind,
    /// Wire-size measurement without retained bytes.
    pub wire_bytes: usize,
    /// Exact bounded session identity when available.
    pub identity: Option<DiagnosticIdentity>,
}

/// Shared queue diagnostics with bounded age bookkeeping and cancellation-safe tickets.
#[derive(Clone)]
pub struct DiagnosticQueue {
    /// Optional shared bookkeeping, absent when diagnostics are disabled.
    inner: Option<Arc<Mutex<QueueBook>>>,
    /// Best-effort observer.
    diagnostics: DiagnosticHandle,
    /// Fixed queue kind.
    stage: DiagnosticStage,
}

/// Fixed-count tracking for queue age, plus exact totals for omitted timestamps.
struct QueueBook {
    /// Next diagnostic ticket identity, unrelated to protocol sequences.
    next_ticket: u64,
    /// Number of queued messages.
    depth: u64,
    /// Encoded wire bytes queued.
    bytes: u64,
    /// Retained enqueue instants, bounded by `capacity`.
    timestamps: BTreeMap<u64, Instant>,
    /// Count whose timestamp was omitted due to the tracking bound.
    untracked: u64,
    /// Maximum retained timestamps.
    capacity: usize,
}

impl QueueBook {
    /// Projects queue totals; no oldest age is claimed while omitted entries remain.
    fn snapshot(&self) -> DiagnosticQueueSnapshot {
        DiagnosticQueueSnapshot {
            depth: self.depth,
            bytes: self.bytes,
            oldest_age_us: if self.untracked > 0 {
                None
            } else {
                Some(
                    self.timestamps
                        .values()
                        .min()
                        .map_or(0, |instant| microseconds(instant.elapsed())),
                )
            },
        }
    }
}

impl DiagnosticQueue {
    /// Creates a queue probe without changing capacity, delivery order or admission of that queue.
    pub fn new(diagnostics: DiagnosticHandle, stage: DiagnosticStage) -> Self {
        Self::with_capacity(diagnostics, stage, MAX_TRACKED_QUEUE_TIMESTAMPS)
    }

    /// Creates bounded tracking; test-only smaller capacities verify explicit unknown ages.
    fn with_capacity(
        diagnostics: DiagnosticHandle,
        stage: DiagnosticStage,
        capacity: usize,
    ) -> Self {
        let inner = diagnostics.is_enabled().then(|| {
            Arc::new(Mutex::new(QueueBook {
                next_ticket: 0,
                depth: 0,
                bytes: 0,
                timestamps: BTreeMap::new(),
                untracked: 0,
                capacity,
            }))
        });
        Self {
            inner,
            diagnostics,
            stage,
        }
    }

    /// Tracks an enqueue with only fixed metadata; dropping the ticket accounts for cancellation.
    pub fn enqueue(&self, metadata: &NodeFactMetadata) -> Option<DiagnosticQueueTicket> {
        let inner = self.inner.as_ref()?;
        let mut book = inner.lock().ok()?;
        let ticket = book.next_ticket;
        book.next_ticket = book.next_ticket.saturating_add(1);
        let tracked = book.timestamps.len() < book.capacity && ticket != u64::MAX;
        let started = Instant::now();
        if tracked {
            book.timestamps.insert(ticket, started);
        } else {
            book.untracked = book.untracked.saturating_add(1);
        }
        book.depth = book.depth.saturating_add(1);
        book.bytes = book.bytes.saturating_add(metadata.wire_bytes as u64);
        let snapshot = book.snapshot();
        drop(book);
        self.diagnostics.record(DiagnosticRecord {
            kind: metadata.kind,
            stage: self.stage,
            duration_us: None,
            value: None,
            identity: metadata.identity.clone(),
            queue: Some(snapshot),
        });
        Some(DiagnosticQueueTicket {
            queue: self.clone(),
            ticket,
            tracked,
            started,
            wire_bytes: metadata.wire_bytes as u64,
            kind: metadata.kind,
            identity: metadata.identity.clone(),
        })
    }
}

/// RAII queue observation; it never consumes, acknowledges or rejects the corresponding fact.
pub struct DiagnosticQueueTicket {
    /// Bookkeeping and sink shared with this queue.
    queue: DiagnosticQueue,
    /// Diagnostic-only enqueue ordinal.
    ticket: u64,
    /// Whether its timestamp was retained.
    tracked: bool,
    /// Local enqueue instant.
    started: Instant,
    /// Encoded bytes accounted for at enqueue.
    wire_bytes: u64,
    /// Message category.
    kind: DiagnosticKind,
    /// Exact optional session attribution.
    identity: Option<DiagnosticIdentity>,
}

impl std::fmt::Debug for DiagnosticQueueTicket {
    /// Reports fixed ticket metadata without printing payloads or observer configuration.
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("DiagnosticQueueTicket")
            .field("ticket", &self.ticket)
            .finish()
    }
}

impl Drop for DiagnosticQueueTicket {
    /// Removes the observed queue entry on dequeue, dropped channel, or cancelled session.
    fn drop(&mut self) {
        let Some(inner) = &self.queue.inner else {
            return;
        };
        let Ok(mut book) = inner.lock() else {
            return;
        };
        book.depth = book.depth.saturating_sub(1);
        book.bytes = book.bytes.saturating_sub(self.wire_bytes);
        if self.tracked {
            book.timestamps.remove(&self.ticket);
        } else {
            book.untracked = book.untracked.saturating_sub(1);
        }
        let snapshot = book.snapshot();
        drop(book);
        self.queue.diagnostics.record(DiagnosticRecord {
            kind: self.kind,
            stage: self.queue.stage,
            duration_us: Some(microseconds(self.started.elapsed())),
            value: None,
            identity: self.identity.clone(),
            queue: Some(snapshot),
        });
    }
}

/// Converts a local elapsed duration into a bounded diagnostic integer.
fn microseconds(duration: Duration) -> u64 {
    u64::try_from(duration.as_micros()).unwrap_or(u64::MAX)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Bounded in-process test observer retaining only synthetic metric metadata.
    #[derive(Default)]
    struct Recorder {
        /// Inspectable observed records.
        records: Mutex<Vec<DiagnosticRecord>>,
    }

    impl DiagnosticObserver for Recorder {
        /// Captures synthetic observations without changing any application fact.
        fn try_record(&self, record: DiagnosticRecord) {
            self.records.lock().unwrap().push(record);
        }
    }

    /// A cancelled/untracked queue ticket cannot invent a new oldest age or leak totals.
    #[test]
    fn queue_tracking_is_bounded_and_drop_safe() {
        let recorder = Arc::new(Recorder::default());
        let queue = DiagnosticQueue::with_capacity(
            DiagnosticHandle::new(recorder.clone()),
            DiagnosticStage::IngressQueue,
            1,
        );
        let metadata = NodeFactMetadata {
            received_at: TimestampMs::new(1),
            received_instant: Instant::now(),
            kind: DiagnosticKind::Heartbeat,
            wire_bytes: 11,
            identity: DiagnosticIdentity::bounded("node", "session"),
        };
        let first = queue.enqueue(&metadata).unwrap();
        let second = queue.enqueue(&metadata).unwrap();
        let snapshot = recorder
            .records
            .lock()
            .unwrap()
            .last()
            .unwrap()
            .queue
            .unwrap();
        assert_eq!(
            (snapshot.depth, snapshot.bytes, snapshot.oldest_age_us),
            (2, 22, None)
        );
        drop(first);
        assert_eq!(
            recorder
                .records
                .lock()
                .unwrap()
                .last()
                .unwrap()
                .queue
                .unwrap()
                .oldest_age_us,
            None
        );
        drop(second);
        let snapshot = recorder
            .records
            .lock()
            .unwrap()
            .last()
            .unwrap()
            .queue
            .unwrap();
        assert_eq!(
            (snapshot.depth, snapshot.bytes, snapshot.oldest_age_us),
            (0, 0, Some(0))
        );
        assert!(
            DiagnosticQueue::new(DiagnosticHandle::default(), DiagnosticStage::IngressQueue)
                .enqueue(&metadata)
                .is_none()
        );
    }
}
