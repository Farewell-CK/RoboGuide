//! Bounded standalone storage recovery measurement; no Node, network listener or action is created.

use state::{CheckpointStoragePolicy, ControllerStorageFormat, SqliteEventLog};
use std::time::Instant;

/// Opens an explicitly selected owned database and reports validated Catalog recovery cost.
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let arguments: Vec<String> = std::env::args().skip(1).collect();
    let [mode, path] = arguments.as_slice() else {
        return Err(
            "usage: catalog_recovery_probe legacy|2|3|audit|audit3 /owned/database.sqlite3".into(),
        );
    };
    let format = match mode.as_str() {
        "legacy" => ControllerStorageFormat::Legacy,
        "2" | "audit" => ControllerStorageFormat::V2,
        "3" | "audit3" => ControllerStorageFormat::V3,
        _ => return Err("explicit legacy, 2, 3, audit or audit3 mode required".into()),
    };
    let start = Instant::now();
    let log = SqliteEventLog::open_controller_with_checkpoint_policy(
        path,
        CheckpointStoragePolicy::Full,
        format,
    )?;
    let opened = start.elapsed().as_secs_f64();
    let recovery = log.catalog_recovery_events()?;
    let recovered = start.elapsed().as_secs_f64();
    let audit = if matches!(mode.as_str(), "audit" | "audit3") {
        let audit = log.audit_catalog_history()?;
        if mode == "audit3" {
            log.audit_retention_reference_index()?;
        }
        Some(audit)
    } else {
        None
    };
    let checkpoint = log.load_checkpoint()?;
    println!(
        "{}",
        serde_json::json!({
            "mode":mode, "open_seconds":opened, "catalog_ready_seconds":recovered,
            "retained_catalog_events":recovery.events.len(),
            "recovery_rows_read":recovery.scanned_events,
            "recovery_payload_bytes":recovery.scanned_payload_bytes,
            "audit_rows_read":audit.as_ref().map(|audit|audit.scanned_events),
            "audit_payload_bytes":audit.as_ref().map(|audit|audit.scanned_payload_bytes),
            "audit_retained_catalog_events":audit.as_ref().map(|audit|audit.events.len()),
            "audit_timestamp_high_water":audit.as_ref().map(|audit|audit.timestamp_high_water.as_millis()),
            "timestamp_high_water":recovery.timestamp_high_water.as_millis(),
            "original_latest_sequence":log.latest_sequence()?,
            "checkpoint_schema":checkpoint.as_ref().map(|value|&value.schema),
            "checkpoint_sequence":checkpoint.as_ref().map(|value|value.event_sequence),
            "checkpoint_json":checkpoint.as_ref().map(|value|&value.checkpoint_json),
            "physical_robot_used":false,
        })
    );
    Ok(())
}
