//! Bounded owned-database workload for repeat-value write reduction; never contacts a Node or robot.

use domain::*;
use ports::EventSink;
use rusqlite::Connection;
use state::{CheckpointStoragePolicy, ControllerStorageFormat, SqliteEventLog};
use std::path::PathBuf;
use std::time::Instant;

/// Generates an exact repeat-value workload while retaining source sequence, epoch and receive time.
fn main() -> Result<(), Box<dyn std::error::Error>> {
    let arguments: Vec<String> = std::env::args().skip(1).collect();
    let [path, count] = arguments.as_slice() else {
        return Err("usage: telemetry_storage_probe /new/owned.sqlite3 1..1000000".into());
    };
    let count: u64 = count.parse()?;
    let path = PathBuf::from(path);
    if count == 0 || count > 1_000_000 || path.exists() {
        return Err("requires new owned path and bounded observation count".into());
    }
    let start = Instant::now();
    let mut log = SqliteEventLog::open_controller_with_checkpoint_policy(
        &path,
        CheckpointStoragePolicy::Full,
        ControllerStorageFormat::V3,
    )?;
    let value =
        serde_json::json!({"artifact":"stable-original-capture","payload":"x".repeat(4096)});
    log.begin_batch()?;
    for sequence in 1..=count {
        let record = StateRecord::new_with_source_epoch(
            StateObjectRef::new(StateObjectClass::Node, "fixture", "software-node")?,
            StateSemantic::Reported,
            StateSource::Node {
                node_id: NodeId::new("software-node")?,
                local_system_id: LocalSystemId::new("fixture")?,
            },
            "capture",
            "fixture.result/v1",
            value.clone(),
            Some(TimestampMs::new(sequence)),
            TimestampMs::new(sequence + 1),
            1000,
            None,
            Some(
                if sequence <= count / 2 {
                    "epoch-one"
                } else {
                    "epoch-two"
                }
                .into(),
            ),
            sequence,
        )?;
        log.append(
            TimestampMs::new(sequence),
            &CorrelationId::new("repeat-value-fixture")?,
            None,
            EventPayload::StateRecordObserved { record },
        );
        if let Some(error) = log.take_error()? {
            return Err(error.into());
        }
    }
    log.save_checkpoint("fixture/v1", "{}")?;
    log.commit_batch()?;
    let append_seconds = start.elapsed().as_secs_f64();
    let connection = Connection::open(&path)?;
    let (bodies, stored): (u64, u64) = connection.query_row(
        "SELECT COUNT(*),SUM(byte_size) FROM telemetry_bodies",
        [],
        |row| Ok((row.get(0)?, row.get(1)?)),
    )?;
    let observations: u64 =
        connection.query_row("SELECT COUNT(*) FROM controller_v3_events", [], |row| {
            row.get(0)
        })?;
    let logical = stored
        .checked_mul(count)
        .ok_or("workload byte count overflow")?;
    let mut cursor = 0;
    let mut verified = 0;
    loop {
        let page = log.events_page(Some(cursor), 1000)?;
        if page.is_empty() {
            break;
        }
        for event in page {
            let EventPayload::StateRecordObserved { record } =
                serde_json::from_str(&event.payload_json)?
            else {
                return Err("unexpected original payload".into());
            };
            let sequence = event.sequence;
            if sequence != cursor + 1 || record.sequence() != sequence || record.value() != &value {
                return Err("original value/source sequence differs".into());
            }
            cursor = sequence;
            verified += 1;
        }
    }
    if bodies != 1 || observations != count || verified != count {
        return Err("repeat body or original event coverage differs".into());
    }
    println!(
        "{}",
        serde_json::json!({"observations":count,"bodies":bodies,"logical_value_bytes":logical,"stored_value_bytes":stored,
        "body_write_reduction":1.0-stored as f64/logical as f64,"append_seconds":append_seconds,"total_seconds":start.elapsed().as_secs_f64(),
        "verified_original_events":verified,"physical_robot_used":false})
    );
    Ok(())
}
