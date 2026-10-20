//! Bounded observation aggregation and Prometheus rendering, separate from application authority.

use ports::{
    DIAGNOSTIC_DURATION_BUCKETS_US, DiagnosticAggregate, DiagnosticAggregateSnapshot,
    DiagnosticHandle, DiagnosticIdentity, DiagnosticKind, DiagnosticObserver,
    DiagnosticQueueAggregate, DiagnosticQueueSnapshot, DiagnosticRecord, DiagnosticStage,
};
use std::collections::BTreeMap;
use std::fmt::Write as _;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Instant;

/// Maximum active ingress sessions retained; overflow remains explicitly incomplete.
const MAX_QUEUE_SOURCES: usize = 256;

/// Latest original queue observation with a local age anchor, never a State receive time.
struct QueueReading {
    /// Actual original queue totals.
    snapshot: DiagnosticQueueSnapshot,
    /// Local instant permits age to grow without manufacturing another queue event.
    received: Instant,
}

/// Fixed-dimension statistics; no input payload or exposed arbitrary labels are retained.
#[derive(Default)]
struct AggregateBook {
    /// At most the fixed kind/stage enum product.
    aggregates: BTreeMap<(DiagnosticKind, DiagnosticStage), DiagnosticAggregate>,
    /// Global application queue or exact bounded ingress session totals.
    queues: BTreeMap<(DiagnosticStage, Option<DiagnosticIdentity>), QueueReading>,
    /// Missing/oversized queue source identities prevent a complete queue conclusion.
    queue_incomplete: bool,
}

/// Actual process-local observation sink; every authority operation treats loss as diagnostic only.
pub(crate) struct MetricsObserver {
    /// Original optional JSONL sink, retaining its independent bounded behavior.
    diagnostics: DiagnosticHandle,
    /// Observation mutex is only try-locked, never part of the application writer order.
    book: Mutex<AggregateBook>,
    /// Loss count is independent of the best-effort aggregate mutex.
    dropped: AtomicU64,
}

impl MetricsObserver {
    /// Installs one observation sink while leaving original diagnostic file policy unchanged.
    pub(crate) fn handle(diagnostics: DiagnosticHandle) -> DiagnosticHandle {
        DiagnosticHandle::new(Arc::new(Self {
            diagnostics,
            book: Mutex::new(AggregateBook::default()),
            dropped: AtomicU64::new(0),
        }))
    }
}

impl DiagnosticObserver for MetricsObserver {
    /// Attempts a fixed-size statistical update and forwards the original independent observation.
    fn try_record(&self, record: DiagnosticRecord) {
        self.diagnostics.record(record.clone());
        let Ok(mut book) = self.book.try_lock() else {
            self.dropped.fetch_add(1, Ordering::Relaxed);
            return;
        };
        let aggregate = book
            .aggregates
            .entry((record.kind, record.stage))
            .or_insert(DiagnosticAggregate {
                kind: record.kind,
                stage: record.stage,
                duration_count: 0,
                duration_sum_us: 0,
                duration_buckets: [0; 12],
                value: None,
            });
        if let Some(duration) = record.duration_us {
            aggregate.duration_count = aggregate.duration_count.saturating_add(1);
            aggregate.duration_sum_us = aggregate.duration_sum_us.saturating_add(duration);
            for (count, boundary) in aggregate
                .duration_buckets
                .iter_mut()
                .zip(DIAGNOSTIC_DURATION_BUCKETS_US)
            {
                *count = count.saturating_add(u64::from(duration <= boundary));
            }
        }
        if record.value.is_some() {
            aggregate.value = record.value;
        }
        if let Some(snapshot) = record.queue {
            let identity = match record.stage {
                DiagnosticStage::ApplicationQueue => None,
                DiagnosticStage::IngressQueue if record.identity.is_some() => record.identity,
                _ => {
                    book.queue_incomplete = true;
                    return;
                }
            };
            let key = (record.stage, identity);
            if snapshot.depth == 0 {
                book.queues.remove(&key);
            } else if book.queues.contains_key(&key) || book.queues.len() < MAX_QUEUE_SOURCES {
                book.queues.insert(
                    key,
                    QueueReading {
                        snapshot,
                        received: Instant::now(),
                    },
                );
            } else {
                book.queue_incomplete = true;
                self.dropped.fetch_add(1, Ordering::Relaxed);
            }
        }
    }

    /// Observes actual bounded totals; missing source ages and lost records remain unknown.
    fn snapshot(&self) -> Option<DiagnosticAggregateSnapshot> {
        let book = self.book.try_lock().ok()?;
        let dropped = self.dropped.load(Ordering::Relaxed);
        let queues = [
            DiagnosticStage::IngressQueue,
            DiagnosticStage::ApplicationQueue,
        ]
        .into_iter()
        .map(|stage| {
            let mut result = DiagnosticQueueAggregate {
                stage,
                depth: 0,
                bytes: 0,
                oldest_age_us: Some(0),
                complete: !book.queue_incomplete && dropped == 0,
            };
            for ((source_stage, _), reading) in &book.queues {
                if *source_stage != stage {
                    continue;
                }
                result.depth = result.depth.saturating_add(reading.snapshot.depth);
                result.bytes = result.bytes.saturating_add(reading.snapshot.bytes);
                result.oldest_age_us = result
                    .oldest_age_us
                    .zip(reading.snapshot.oldest_age_us)
                    .map(|(oldest, age)| {
                        oldest.max(
                            age.saturating_add(
                                u64::try_from(reading.received.elapsed().as_micros())
                                    .unwrap_or(u64::MAX),
                            ),
                        )
                    });
            }
            result.complete &= result.oldest_age_us.is_some();
            if !result.complete {
                result.oldest_age_us = None;
            }
            result
        })
        .collect();
        Some(DiagnosticAggregateSnapshot {
            aggregates: book.aggregates.values().cloned().collect(),
            queues,
            dropped,
        })
    }
}

/// Renders only fixed labels and actual observations; no missing duration is converted to zero.
pub(crate) fn render(diagnostics: &DiagnosticHandle) -> String {
    let mut output = String::new();
    let snapshot = diagnostics.snapshot();
    writeln!(output, "# HELP roboguide_observability_available Whether the bounded observation snapshot was readable.\n# TYPE roboguide_observability_available gauge\nroboguide_observability_available {}", u8::from(snapshot.is_some())).expect("String formatting is infallible");
    let Some(snapshot) = snapshot else {
        return output;
    };
    writeln!(output, "# HELP roboguide_observability_dropped_total Observations omitted due to contention or capacity.\n# TYPE roboguide_observability_dropped_total counter\nroboguide_observability_dropped_total {}", snapshot.dropped).expect("String formatting is infallible");
    output.push_str("# HELP roboguide_controller_stage_duration_seconds Actual local stage elapsed durations.\n# TYPE roboguide_controller_stage_duration_seconds histogram\n# HELP roboguide_controller_stage_value Last actual fixed-stage scalar sample.\n# TYPE roboguide_controller_stage_value gauge\n");
    for aggregate in snapshot.aggregates {
        let kind = aggregate.kind.as_str();
        let stage = aggregate.stage.as_str();
        if aggregate.duration_count > 0 {
            for (boundary, count) in DIAGNOSTIC_DURATION_BUCKETS_US
                .into_iter()
                .zip(aggregate.duration_buckets)
            {
                writeln!(output, "roboguide_controller_stage_duration_seconds_bucket{{kind=\"{kind}\",stage=\"{stage}\",le=\"{}\"}} {count}", boundary as f64 / 1_000_000.0).expect("String formatting is infallible");
            }
            writeln!(output, "roboguide_controller_stage_duration_seconds_bucket{{kind=\"{kind}\",stage=\"{stage}\",le=\"+Inf\"}} {}\nroboguide_controller_stage_duration_seconds_count{{kind=\"{kind}\",stage=\"{stage}\"}} {}\nroboguide_controller_stage_duration_seconds_sum{{kind=\"{kind}\",stage=\"{stage}\"}} {}", aggregate.duration_count, aggregate.duration_count, aggregate.duration_sum_us as f64 / 1_000_000.0).expect("String formatting is infallible");
        }
        if let Some(value) = aggregate.value {
            let connection_metric = match aggregate.stage {
                DiagnosticStage::OperatorConnectionsActive => Some((
                    "roboguide_operator_connections_active",
                    "gauge",
                    "Last one-second sample of actual live Operator transports.",
                )),
                DiagnosticStage::OperatorConnectionLimit => Some((
                    "roboguide_operator_connections_limit",
                    "gauge",
                    "Configured maximum simultaneous Operator transports.",
                )),
                DiagnosticStage::OperatorConnectionRejections => Some((
                    "roboguide_operator_connections_rejected_total",
                    "counter",
                    "Actual Operator transports closed before TLS because all permits were held.",
                )),
                _ => None,
            };
            if aggregate.kind == DiagnosticKind::Http
                && let Some((name, metric_type, help)) = connection_metric
            {
                writeln!(
                    output,
                    "# HELP {name} {help}\n# TYPE {name} {metric_type}\n{name} {value}"
                )
                .expect("String formatting is infallible");
            }
            writeln!(
                output,
                "roboguide_controller_stage_value{{kind=\"{kind}\",stage=\"{stage}\"}} {value}"
            )
            .expect("String formatting is infallible");
        }
    }
    output.push_str("# HELP roboguide_protocol_queue_depth Actual tracked protocol queue depth, lower bound when incomplete.\n# TYPE roboguide_protocol_queue_depth gauge\n# HELP roboguide_protocol_queue_bytes Actual tracked queued wire bytes.\n# TYPE roboguide_protocol_queue_bytes gauge\n# HELP roboguide_protocol_queue_complete Whether queue coverage is complete.\n# TYPE roboguide_protocol_queue_complete gauge\n# HELP roboguide_protocol_queue_oldest_age_seconds Oldest tracked queue age or NaN when unavailable.\n# TYPE roboguide_protocol_queue_oldest_age_seconds gauge\n");
    for queue in snapshot.queues {
        let stage = queue.stage.as_str();
        let age = queue.oldest_age_us.map_or_else(
            || "NaN".into(),
            |value| (value as f64 / 1_000_000.0).to_string(),
        );
        writeln!(output, "roboguide_protocol_queue_depth{{stage=\"{stage}\"}} {}\nroboguide_protocol_queue_bytes{{stage=\"{stage}\"}} {}\nroboguide_protocol_queue_complete{{stage=\"{stage}\"}} {}\nroboguide_protocol_queue_oldest_age_seconds{{stage=\"{stage}\"}} {age}", queue.depth, queue.bytes, u8::from(queue.complete)).expect("String formatting is infallible");
    }
    output
}

#[cfg(test)]
#[path = "observability_tests.rs"]
mod tests;
