//! Constant-cardinality transactional storage counters exposed through the existing metrics route.

use std::fmt::Write as _;

/// Emits unavailable explicitly; legacy deployments do not gain a fabricated zero dedup ratio.
pub(super) fn render(log: &state::SqliteEventLog) -> String {
    let mut output = String::from("# TYPE roboguide_telemetry_storage_available gauge\n");
    let Ok(snapshot) = log.telemetry_storage_metrics() else {
        output.push_str("roboguide_telemetry_storage_available 0\n");
        return output;
    };
    output.push_str("roboguide_telemetry_storage_available 1\n");
    let counters = snapshot.counters;
    for (name, value, kind) in [
        (
            "roboguide_storage_events_total",
            counters.appended_events_total,
            "counter",
        ),
        (
            "roboguide_storage_observations_total",
            counters.observation_events_total,
            "counter",
        ),
        (
            "roboguide_storage_logical_value_bytes_total",
            counters.logical_value_bytes_total,
            "counter",
        ),
        (
            "roboguide_storage_created_value_bytes_total",
            counters.created_body_value_bytes_total,
            "counter",
        ),
        (
            "roboguide_storage_body_hits_total",
            counters.body_reuse_hits_total,
            "counter",
        ),
        (
            "roboguide_storage_body_misses_total",
            counters.body_creation_misses_total,
            "counter",
        ),
        ("roboguide_storage_hot_events", counters.hot_events, "gauge"),
        (
            "roboguide_storage_hot_observations",
            counters.hot_observations,
            "gauge",
        ),
        ("roboguide_storage_hot_bodies", counters.hot_bodies, "gauge"),
        (
            "roboguide_storage_hot_value_bytes",
            counters.hot_body_value_bytes,
            "gauge",
        ),
        (
            "roboguide_storage_accounting_start_sequence",
            counters.accounting_start_event_sequence,
            "gauge",
        ),
        (
            "roboguide_storage_sqlite_allocated_bytes",
            snapshot.sqlite_allocated_bytes,
            "gauge",
        ),
        (
            "roboguide_storage_sqlite_reusable_bytes",
            snapshot.sqlite_reusable_bytes,
            "gauge",
        ),
    ] {
        let _ = writeln!(output, "# TYPE {name} {kind}\n{name} {value}");
    }
    if counters.logical_value_bytes_total > 0 {
        let reduction = 1.0
            - counters.created_body_value_bytes_total as f64
                / counters.logical_value_bytes_total as f64;
        let _ = writeln!(
            output,
            "# TYPE roboguide_storage_value_write_reduction_ratio gauge\nroboguide_storage_value_write_reduction_ratio {reduction}"
        );
    }
    output
}
