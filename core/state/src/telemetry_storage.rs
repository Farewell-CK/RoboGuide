//! Lossless v3 telemetry bodies and original event reconstruction under the same SQL writer.

use super::*;
use rusqlite::functions::FunctionFlags;
use serde::Deserialize;
use serde_json::value::RawValue;

/// Exact public view; original payloads are verified before returning reconstructed bytes.
pub(super) const VIEW: &str = "CREATE VIEW events AS SELECT e.sequence,e.event_id,e.timestamp_ms,e.correlation_id,e.causation_id,e.payload_schema,CASE WHEN e.body_id IS NULL THEN rg_verified_event(e.payload_json,e.payload_digest) ELSE rg_verified_event(e.payload_prefix || rg_verified_body(e.body_id,b.scope_hash,b.value_digest,b.byte_size,b.value_json) || e.payload_suffix,e.payload_digest) END AS payload_json FROM controller_v3_events e LEFT JOIN telemetry_bodies b ON e.body_id=b.body_id";

/// Borrow only the observation record while preserving exact input slices.
#[derive(Deserialize)]
struct Envelope<'a> {
    /// The sole supported deduplicated event variant.
    #[serde(rename = "StateRecordObserved", borrow)]
    observation: Observation<'a>,
}

/// Borrow the original State record wrapper.
#[derive(Deserialize)]
struct Observation<'a> {
    /// Original validated record, including all temporal fields in its surrounding bytes.
    #[serde(borrow)]
    record: Record<'a>,
}

/// Original source/value slices; other fields stay in the immutable event prefix/suffix.
#[derive(Deserialize)]
struct Record<'a> {
    /// Exact object, semantic, owner and channel scope; different sources never share storage identity.
    #[serde(borrow)]
    key: &'a RawValue,
    /// Source-declared payload interpretation.
    payload_schema: &'a str,
    /// Borrowed unmodified value bytes, including strings and float representations.
    #[serde(borrow)]
    value: &'a RawValue,
}

/// Reports a persistent integrity violation without substituting empty State or newer evidence.
fn invalid(message: &str) -> SqliteEventLogError {
    SqliteEventLogError::Codec(format!("telemetry storage v3: {message}"))
}

/// Digests one exact input using the stable lowercase SHA-256 representation.
fn digest(bytes: &[u8]) -> String {
    format!("sha256:{:x}", Sha256::digest(bytes))
}

/// Registers deterministic verified reconstruction on this actual SQLite connection.
pub(super) fn install_functions(connection: &Connection) -> Result<(), SqliteEventLogError> {
    let flags = FunctionFlags::SQLITE_UTF8 | FunctionFlags::SQLITE_DETERMINISTIC;
    connection.create_scalar_function("rg_verified_body", 5, flags, |context| {
        let id: Option<String> = context.get(0)?;
        let scope: Option<String> = context.get(1)?;
        let expected: Option<String> = context.get(2)?;
        let size: Option<u64> = context.get(3)?;
        let value: Option<String> = context.get(4)?;
        let (Some(id), Some(scope), Some(expected), Some(size), Some(value)) =
            (id, scope, expected, size, value)
        else {
            return Err(rusqlite::Error::UserFunctionError(
                "required telemetry body is missing".into(),
            ));
        };
        if value.len() as u64 != size
            || digest(value.as_bytes()) != expected
            || digest(format!("{scope}\n{expected}").as_bytes()) != id
        {
            return Err(rusqlite::Error::UserFunctionError(
                "telemetry body checksum differs".into(),
            ));
        }
        Ok(value)
    })?;
    connection.create_scalar_function("rg_verified_event", 2, flags, |context| {
        let value: String = context.get(0)?;
        let expected: String = context.get(1)?;
        if digest(value.as_bytes()) != expected {
            return Err(rusqlite::Error::UserFunctionError(
                "original event reconstruction differs".into(),
            ));
        }
        Ok(value)
    })?;
    Ok(())
}

/// Inserts one validated event and optionally reuses its exact original value within the source scope.
pub(super) fn insert(
    connection: &Connection,
    row: &PersistedEvent,
) -> Result<(), SqliteEventLogError> {
    if connection.is_autocommit() {
        return Err(invalid("event requires its actual transaction"));
    }
    let split = serde_json::from_str::<Envelope<'_>>(&row.payload_json).ok();
    let (body_id, prefix, suffix, value_bytes, created_body) = if let Some(envelope) = split {
        let record = envelope.observation.record;
        let value = record.value.get();
        let offset = (value.as_ptr() as usize)
            .checked_sub(row.payload_json.as_ptr() as usize)
            .ok_or_else(|| invalid("value is not an original source slice"))?;
        let end = offset
            .checked_add(value.len())
            .ok_or_else(|| invalid("slice overflow"))?;
        if row.payload_json.get(offset..end) != Some(value) {
            return Err(invalid("source slice differs"));
        }
        let scope = digest(
            serde_json::to_string(&(record.key, record.payload_schema))
                .map_err(|error| invalid(&error.to_string()))?
                .as_bytes(),
        );
        let value_digest = digest(value.as_bytes());
        let id = digest(format!("{scope}\n{value_digest}").as_bytes());
        let created=connection.execute("INSERT OR IGNORE INTO telemetry_bodies(body_id,scope_hash,value_digest,byte_size,value_json) VALUES(?1,?2,?3,?4,?5)",
            params![id,scope,value_digest,value.len() as u64,value])?;
        let retained: String = connection.query_row("SELECT rg_verified_body(body_id,scope_hash,value_digest,byte_size,value_json) FROM telemetry_bodies WHERE body_id=?1",[&id],|r|r.get(0))?;
        if retained != value {
            return Err(invalid("existing immutable body conflicts"));
        }
        (
            Some(id),
            Some(row.payload_json[..offset].to_string()),
            Some(row.payload_json[end..].to_string()),
            Some(value.len() as u64),
            created == 1,
        )
    } else {
        (None, None, None, None, false)
    };
    connection.execute("INSERT INTO controller_v3_events(sequence,event_id,timestamp_ms,correlation_id,causation_id,payload_schema,payload_json,body_id,payload_prefix,payload_suffix,payload_digest) VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11)",
        params![row.sequence,row.event_id,row.timestamp_ms,row.correlation_id,row.causation_id,row.payload_schema,
            body_id.is_none().then_some(&row.payload_json),body_id,prefix,suffix,digest(row.payload_json.as_bytes())])?;
    super::retention_references::observe(connection, row)?;
    super::telemetry_metrics::record_append(connection, row.sequence, value_bytes, created_body)?;
    Ok(())
}

/// Migrates validated v2 under the outer exclusive transaction; no original JSON bytes are lost.
pub(super) fn initialize(connection: &Connection) -> Result<(), SqliteEventLogError> {
    if connection.is_autocommit() {
        return Err(invalid("migration requires an exclusive transaction"));
    }
    catalog_checkpoint::read(connection)?;
    connection.execute_batch("CREATE TABLE telemetry_bodies(body_id TEXT PRIMARY KEY,scope_hash TEXT NOT NULL,value_digest TEXT NOT NULL,byte_size INTEGER NOT NULL,value_json TEXT NOT NULL);
        CREATE TABLE controller_v3_events(sequence INTEGER PRIMARY KEY AUTOINCREMENT,event_id TEXT UNIQUE NOT NULL,timestamp_ms INTEGER NOT NULL,correlation_id TEXT NOT NULL,causation_id TEXT,payload_schema TEXT NOT NULL,payload_json TEXT,body_id TEXT,payload_prefix TEXT,payload_suffix TEXT,payload_digest TEXT NOT NULL,
        CHECK((body_id IS NULL AND payload_json IS NOT NULL AND payload_prefix IS NULL AND payload_suffix IS NULL) OR (body_id IS NOT NULL AND payload_json IS NULL AND payload_prefix IS NOT NULL AND payload_suffix IS NOT NULL)));
        CREATE TABLE command_journal_classes(command_id TEXT PRIMARY KEY,kind TEXT NOT NULL CHECK(kind IN ('external','node-facts','timer')));")?;
    super::retention_references::initialize(connection)?;
    super::telemetry_metrics::initialize(connection)?;
    let mut statement = connection.prepare("SELECT sequence,event_id,timestamp_ms,correlation_id,causation_id,payload_schema,payload_json FROM controller_v1_events ORDER BY sequence")?;
    for row in statement.query_map([], event_from_row)? {
        let row = row?;
        application_replication::decode_persisted_event(&row)?;
        insert(connection, &row)?;
    }
    drop(statement);
    connection.execute_batch("DROP VIEW events; DROP TABLE controller_v1_events;
        CREATE INDEX controller_v3_events_timestamp ON controller_v3_events(timestamp_ms,event_id);
        CREATE INDEX controller_v3_events_correlation ON controller_v3_events(correlation_id,event_id);
        CREATE INDEX controller_v3_events_body ON controller_v3_events(body_id);
        DROP TABLE controller_storage_format;
        CREATE TABLE controller_storage_format(version INTEGER PRIMARY KEY CHECK(version=3),schema TEXT NOT NULL);
        INSERT INTO controller_storage_format VALUES(3,'roboguide.controller-storage/v3'); PRAGMA user_version=3;")?;
    connection.execute_batch(VIEW)?;
    retention::initialize(connection)?;
    validate(connection)?;
    catalog_checkpoint::read(connection)?;
    Ok(())
}

/// Validates the closed v3 layout before any durable write; missing bodies fail on reads/full audit.
pub(super) fn validate(connection: &Connection) -> Result<(), SqliteEventLogError> {
    let version: u64 = connection.pragma_query_value(None, "user_version", |r| r.get(0))?;
    let rows: Vec<(u64, String)> = connection
        .prepare("SELECT version,schema FROM controller_storage_format")?
        .query_map([], |r| Ok((r.get(0)?, r.get(1)?)))?
        .collect::<Result<_, _>>()?;
    let view: String = connection.query_row(
        "SELECT sql FROM sqlite_master WHERE name='events' AND type='view'",
        [],
        |r| r.get(0),
    )?;
    if version != 3 || rows != vec![(3, "roboguide.controller-storage/v3".into())] || view != VIEW {
        return Err(invalid(
            "storage marker or original reconstruction view differs",
        ));
    }
    for name in [
        "telemetry_bodies",
        "controller_v3_events",
        "command_journal_classes",
        "catalog_recovery_head",
        "catalog_recovery_events",
        "controller_checkpoint",
        "controller_checkpoint_head",
        "controller_checkpoint_deltas",
        "event_log_metadata",
        "control_command_journal",
        "service_access_audit",
    ] {
        let count: u64 = connection.query_row(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?1",
            [name],
            |r| r.get(0),
        )?;
        if count != 1 {
            return Err(invalid("required original table is missing"));
        }
    }
    let triggers: u64 = connection.query_row(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='trigger'",
        [],
        |r| r.get(0),
    )?;
    if triggers != 0 {
        return Err(invalid("unrecognized storage trigger"));
    }
    for (table, required) in [
        (
            "controller_v3_events",
            &[
                "sequence",
                "event_id",
                "timestamp_ms",
                "correlation_id",
                "causation_id",
                "payload_schema",
                "payload_json",
                "body_id",
                "payload_prefix",
                "payload_suffix",
                "payload_digest",
            ][..],
        ),
        (
            "telemetry_bodies",
            &[
                "body_id",
                "scope_hash",
                "value_digest",
                "byte_size",
                "value_json",
            ][..],
        ),
        ("command_journal_classes", &["command_id", "kind"][..]),
    ] {
        let columns = super::table_columns(connection, table)?;
        if columns.len() != required.len() || required.iter().any(|name| !columns.contains(*name)) {
            return Err(invalid("required storage columns differ"));
        }
    }
    let old_tables: u64 = connection.query_row(
        "SELECT COUNT(*) FROM sqlite_master WHERE name='controller_v1_events'",
        [],
        |row| row.get(0),
    )?;
    if old_tables != 0 {
        return Err(invalid("obsolete writable event table remains"));
    }
    retention::validate(connection)?;
    let latest=connection.query_row("SELECT MAX(COALESCE((SELECT MAX(sequence) FROM controller_v3_events),0),COALESCE((SELECT CAST(value AS INTEGER) FROM event_log_metadata WHERE key='history_floor'),0))",[],|row|row.get::<_,u64>(0))?;
    super::retention_references::validate_head(connection, latest)?;
    super::telemetry_metrics::read(connection)?;
    Ok(())
}

/// Retires only the verified bounded prefix and its unreferenced scoped bodies in the caller transaction.
pub(super) fn retire_prefix(
    connection: &Connection,
    floor: u64,
    end: u64,
) -> Result<u64, SqliteEventLogError> {
    if connection.is_autocommit() || end <= floor || end - floor > MAX_ARCHIVE_EVENTS {
        return Err(invalid(
            "body retirement requires the original bounded archive transaction",
        ));
    }
    let observations:u64=connection.query_row("SELECT COUNT(*) FROM controller_v3_events WHERE sequence>?1 AND sequence<=?2 AND body_id IS NOT NULL",params![floor,end],|row|row.get(0))?;
    let mut statement=connection.prepare("SELECT DISTINCT body_id FROM controller_v3_events WHERE sequence>?1 AND sequence<=?2 AND body_id IS NOT NULL ORDER BY body_id")?;
    let bodies = statement
        .query_map(params![floor, end], |row| row.get::<_, String>(0))?
        .collect::<Result<Vec<_>, _>>()?;
    drop(statement);
    if bodies.len() as u64 > end - floor {
        return Err(invalid("body retirement exceeds original event count"));
    }
    let removed = connection.execute(
        "DELETE FROM controller_v3_events WHERE sequence>?1 AND sequence<=?2",
        params![floor, end],
    )? as u64;
    let mut retired_bodies = 0u64;
    let mut retired_bytes = 0u64;
    for body_id in bodies {
        let size:Option<u64>=connection.query_row("DELETE FROM telemetry_bodies WHERE body_id=?1 AND NOT EXISTS(SELECT 1 FROM controller_v3_events WHERE body_id=?1) RETURNING byte_size",[body_id],|row|row.get(0)).optional()?;
        if let Some(size) = size {
            retired_bodies += 1;
            retired_bytes = retired_bytes
                .checked_add(size)
                .ok_or_else(|| invalid("body retirement byte count overflow"))?;
        }
    }
    super::telemetry_metrics::record_retirement(
        connection,
        removed,
        observations,
        retired_bodies,
        retired_bytes,
    )?;
    Ok(removed)
}

#[cfg(test)]
#[path = "telemetry_storage_tests.rs"]
mod tests;
