//! Bounded observation loss and source aggregation, with no synthetic authority evidence.

use super::*;
use std::time::Duration;

/// Builds only diagnostic queue data; it cannot create a Node registration or State fact.
fn queue(stage: DiagnosticStage, node: &str, depth: u64, age: Option<u64>) -> DiagnosticRecord {
    DiagnosticRecord {
        kind: DiagnosticKind::Heartbeat,
        stage,
        duration_us: None,
        value: None,
        identity: DiagnosticIdentity::bounded(node, "unit-session"),
        queue: Some(DiagnosticQueueSnapshot {
            depth,
            bytes: depth * 10,
            oldest_age_us: age,
        }),
    }
}

/// Multiple ingress queues sum independently; application observations already describe one global book.
#[test]
fn queue_totals_do_not_double_count_global_application_snapshots() {
    let observer = MetricsObserver::handle(DiagnosticHandle::default());
    observer.record(queue(
        DiagnosticStage::IngressQueue,
        "private-a",
        2,
        Some(100),
    ));
    observer.record(queue(
        DiagnosticStage::IngressQueue,
        "private-b",
        3,
        Some(200),
    ));
    observer.record(queue(
        DiagnosticStage::ApplicationQueue,
        "private-a",
        4,
        Some(300),
    ));
    observer.record(queue(
        DiagnosticStage::ApplicationQueue,
        "private-b",
        3,
        Some(400),
    ));
    let snapshot = observer.snapshot().unwrap();
    assert_eq!(snapshot.queues[0].depth, 5);
    assert_eq!(snapshot.queues[0].bytes, 50);
    assert_eq!(snapshot.queues[1].depth, 3);
    assert!(snapshot.queues.iter().all(|queue| queue.complete));
    observer.record(queue(
        DiagnosticStage::IngressQueue,
        "private-a",
        0,
        Some(0),
    ));
    observer.record(queue(DiagnosticStage::IngressQueue, "private-b", 3, None));
    assert!(
        observer.snapshot().unwrap().queues[0]
            .oldest_age_us
            .is_none()
    );
    let rendered = render(&observer);
    assert!(!rendered.contains("private-a") && !rendered.contains("private-b"));
    assert!(rendered.contains("roboguide_protocol_queue_depth{stage=\"application_queue\"} 3"));
    assert!(
        rendered
            .contains("roboguide_protocol_queue_oldest_age_seconds{stage=\"ingress_queue\"} NaN")
    );
}

/// Contention and missing identity cannot block producers or advertise complete queue evidence.
#[test]
fn lost_or_unattributed_observations_remain_explicitly_incomplete() {
    let observer = Arc::new(MetricsObserver {
        diagnostics: DiagnosticHandle::default(),
        book: Mutex::new(Default::default()),
        dropped: AtomicU64::new(0),
    });
    let guard = observer.book.lock().unwrap();
    observer.try_record(queue(DiagnosticStage::IngressQueue, "source", 1, Some(0)));
    assert_eq!(observer.dropped.load(Ordering::Relaxed), 1);
    assert!(observer.snapshot().is_none());
    drop(guard);
    assert!(
        observer
            .snapshot()
            .unwrap()
            .queues
            .iter()
            .all(|queue| !queue.complete)
    );
    let independent = MetricsObserver::handle(DiagnosticHandle::default());
    let mut missing = queue(DiagnosticStage::IngressQueue, "source", 1, Some(0));
    missing.identity = None;
    independent.record(missing);
    assert!(
        independent
            .snapshot()
            .unwrap()
            .queues
            .iter()
            .all(|queue| !queue.complete)
    );
}

/// Actual elapsed samples alone enter cumulative histograms; scalar records create no zero sample.
#[test]
fn histogram_preserves_bucket_units_counts_and_missing_samples() {
    let handle = MetricsObserver::handle(DiagnosticHandle::default());
    handle.value(
        DiagnosticKind::Timer,
        DiagnosticStage::CheckpointBytes,
        123,
        None,
    );
    handle.timing(
        DiagnosticKind::Http,
        DiagnosticStage::Request,
        Duration::from_micros(500),
        None,
    );
    handle.timing(
        DiagnosticKind::Http,
        DiagnosticStage::Request,
        Duration::from_secs(2),
        None,
    );
    let snapshot = handle.snapshot().unwrap();
    let timing = snapshot
        .aggregates
        .iter()
        .find(|aggregate| aggregate.kind == DiagnosticKind::Http)
        .unwrap();
    assert_eq!(timing.duration_count, 2);
    assert_eq!(timing.duration_sum_us, 2_000_500);
    assert_eq!(timing.duration_buckets[0], 0);
    assert_eq!(timing.duration_buckets[1], 1);
    assert_eq!(timing.duration_buckets[9], 2);
    let rendered = render(&handle);
    assert!(rendered.contains("le=\"0.0005\"} 1"));
    assert!(rendered.contains("stage=\"http_request\"} 2.0005"));
    assert!(!rendered.contains("duration_seconds_count{kind=\"timer\""));
    assert!(render(&DiagnosticHandle::default()).contains("roboguide_observability_available 0"));
}

/// Repeated arbitrary sessions cannot grow the retained queue book past its declared bound.
#[test]
fn queue_source_capacity_is_bounded_without_changing_protocol_admission() {
    let handle = MetricsObserver::handle(DiagnosticHandle::default());
    for index in 0..MAX_QUEUE_SOURCES + 3 {
        handle.record(queue(
            DiagnosticStage::IngressQueue,
            &format!("private-{index}"),
            1,
            Some(0),
        ));
    }
    let snapshot = handle.snapshot().unwrap();
    assert_eq!(snapshot.dropped, 3);
    assert_eq!(snapshot.queues[0].depth, MAX_QUEUE_SOURCES as u64);
    assert!(!snapshot.queues[0].complete);
    assert!(snapshot.queues[0].oldest_age_us.is_none());
}
