//! Standalone v2 Catalog recovery coverage maintained in the original event transaction.

use super::*;
use std::collections::BTreeMap;

/// Closed projection format; a storage marker never supplies missing original evidence.
const SCHEMA: &str = "roboguide.catalog-recovery-checkpoint/v0.1";
/// Bound the retained protected input independently of the volume of unrelated telemetry.
const MAX_BYTES: u64 = 64 * 1024 * 1024;
/// The existing Catalog archive supports the same bounded protected history.
const MAX_RECORDS: u64 = 16_384;

/// Complete original coverage and ordered protected-envelope checksum.
#[derive(serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct Head {
    /// Exact interpretation of the required projection.
    schema: String,
    /// All original events through this sequence were validated or atomically appended.
    through_sequence: u64,
    /// Timestamp floor includes unrelated telemetry and archived observations.
    timestamp_high_water: u64,
    /// Expected number of original protected envelopes, including the archived prefix.
    records: u64,
    /// Expected encoded bytes of the protected envelopes.
    bytes: u64,
    /// Ordered checksum of those exact envelopes.
    digest: String,
}

/// Reports projection failures under the ordinary fail-closed storage error contract.
fn invalid(message: &str) -> SqliteEventLogError {
    SqliteEventLogError::Codec(format!("Catalog recovery checkpoint: {message}"))
}

/// Starts an empty ordered chain without confusing absence with an omitted projection.
fn empty() -> Head {
    Head {
        schema: SCHEMA.into(),
        through_sequence: 0,
        timestamp_high_water: 0,
        records: 0,
        bytes: 0,
        digest: format!("sha256:{:x}", Sha256::digest(SCHEMA.as_bytes())),
    }
}

/// Hashes one exact original envelope after the previous committed ordered prefix.
fn extend_digest(previous: &str, data: &str) -> String {
    let mut hash = Sha256::new();
    hash.update(previous.as_bytes());
    hash.update((data.len() as u64).to_le_bytes());
    hash.update(data.as_bytes());
    format!("sha256:{:x}", hash.finalize())
}

/// Retains all head fields behind a checksum so accidental timestamp/count edits are refused.
fn save(connection: &Connection, head: &Head) -> Result<(), SqliteEventLogError> {
    let data = serde_json::to_string(head).map_err(|error| invalid(&error.to_string()))?;
    let digest = format!("sha256:{:x}", Sha256::digest(data.as_bytes()));
    connection.execute(
        "INSERT INTO catalog_recovery_head(singleton,data,digest) VALUES(1,?1,?2)
         ON CONFLICT(singleton) DO UPDATE SET data=excluded.data,digest=excluded.digest",
        params![data, digest],
    )?;
    Ok(())
}

/// Reads required original coverage; absence, an extra row or an invalid head never rebuilds silently.
fn head(connection: &Connection) -> Result<Head, SqliteEventLogError> {
    let count: u64 =
        connection.query_row("SELECT COUNT(*) FROM catalog_recovery_head", [], |row| {
            row.get(0)
        })?;
    let (data, digest): (String, String) = connection.query_row(
        "SELECT data,digest FROM catalog_recovery_head WHERE singleton=1 AND length(CAST(data AS BLOB))<=4096",
        [], |row| Ok((row.get(0)?, row.get(1)?)),
    )?;
    if count != 1 || format!("sha256:{:x}", Sha256::digest(data.as_bytes())) != digest {
        return Err(invalid("head count or checksum differs"));
    }
    let result: Head = serde_json::from_str(&data).map_err(|error| invalid(&error.to_string()))?;
    if result.schema != SCHEMA || result.records > MAX_RECORDS || result.bytes > MAX_BYTES {
        return Err(invalid("unsupported or unbounded head"));
    }
    Ok(result)
}

/// Checks the original high-water without reading historical payloads or trusting caller clocks.
fn high_water(connection: &Connection) -> Result<u64, SqliteEventLogError> {
    let counter: Option<String> = connection
        .query_row(
            "SELECT value FROM event_log_metadata WHERE key='next_event_number'",
            [],
            |row| row.get(0),
        )
        .optional()?;
    let counter = counter
        .map(|value| {
            value
                .parse::<u64>()
                .map_err(|_| invalid("invalid original counter"))
        })
        .transpose()?;
    let sequence: u64 =
        connection.query_row("SELECT COALESCE(MAX(sequence),0) FROM events", [], |row| {
            row.get(0)
        })?;
    let floor: Option<String> = connection
        .query_row(
            "SELECT value FROM event_log_metadata WHERE key='history_floor'",
            [],
            |row| row.get(0),
        )
        .optional()?;
    let floor = floor
        .map(|value| {
            value
                .parse::<u64>()
                .map_err(|_| invalid("invalid original archive floor"))
        })
        .transpose()?
        .unwrap_or(0);
    let actual = sequence.max(floor);
    if counter.is_some_and(|counter| counter != actual) {
        return Err(invalid(
            "original counter differs from retained tail/archive floor",
        ));
    }
    Ok(actual)
}

/// Adds a protected row while retaining its exact original serialized identity and finite capacity.
fn retain(
    connection: &Connection,
    state: &mut Head,
    row: &PersistedEvent,
) -> Result<(), SqliteEventLogError> {
    let data = serde_json::to_string(row).map_err(|error| invalid(&error.to_string()))?;
    state.records += 1;
    state.bytes = state
        .bytes
        .checked_add(data.len() as u64)
        .ok_or_else(|| invalid("byte overflow"))?;
    if state.records > MAX_RECORDS || state.bytes > MAX_BYTES {
        return Err(invalid("protected history capacity exceeded"));
    }
    connection.execute(
        "INSERT INTO catalog_recovery_events(sequence,data) VALUES(?1,?2)",
        params![row.sequence, data],
    )?;
    state.digest = extend_digest(&state.digest, &data);
    Ok(())
}

/// Migrates once under the caller's exclusive transaction; every original row receives typed validation.
pub(super) fn initialize(connection: &Connection) -> Result<(), SqliteEventLogError> {
    if connection.is_autocommit() {
        return Err(invalid(
            "migration requires the actual exclusive writer transaction",
        ));
    }
    controller_storage::validate(connection)?;
    let checkpoint = checkpoint_store::read(connection)?;
    authority_journal::validate(
        connection,
        checkpoint.as_ref().map(|stored| stored.snapshot.as_ref()),
    )?;
    // Plain CREATE refuses orphaned auxiliary data instead of adopting an incomplete old attempt.
    connection.execute_batch(
        "CREATE TABLE catalog_recovery_head(singleton INTEGER PRIMARY KEY CHECK(singleton=1),data TEXT NOT NULL,digest TEXT NOT NULL);
         CREATE TABLE catalog_recovery_events(sequence INTEGER PRIMARY KEY,data TEXT NOT NULL);",
    )?;
    let mut state = empty();
    let mut validator = crate::artifact_retention::AdoptionSourceValidator::default();
    let prefix = catalog_archive::read(connection)?;
    let mut statement = connection.prepare(
        "SELECT sequence,event_id,timestamp_ms,correlation_id,causation_id,payload_schema,payload_json FROM events ORDER BY sequence",
    )?;
    let rows = statement.query_map([], event_from_row)?;
    let floor: Option<String> = connection
        .query_row(
            "SELECT value FROM event_log_metadata WHERE key='history_floor'",
            [],
            |row| row.get(0),
        )
        .optional()?;
    let mut tail_sequence = floor
        .map(|value| {
            value
                .parse::<u64>()
                .map_err(|_| invalid("invalid original archive floor"))
        })
        .transpose()?
        .unwrap_or(0);
    for (row, live_tail) in prefix
        .into_iter()
        .map(|row| (Ok(row), false))
        .chain(rows.map(|row| (row, true)))
    {
        let row = row?;
        if live_tail {
            if tail_sequence.checked_add(1) != Some(row.sequence) {
                return Err(invalid(
                    "original unarchived tail has a missing or reordered sequence",
                ));
            }
            tail_sequence = row.sequence;
        }
        if row.sequence <= state.through_sequence {
            return Err(invalid("source prefix and tail overlap or are unordered"));
        }
        let decoded = application_replication::decode_persisted_event(&row)?;
        validator
            .observe(&row, decoded.payload())
            .map_err(|error| invalid(&error))?;
        state.through_sequence = row.sequence;
        state.timestamp_high_water = state.timestamp_high_water.max(row.timestamp_ms);
        if archive::requires_catalog_history(decoded.payload()) {
            retain(connection, &mut state, &row)?;
        }
    }
    state.through_sequence = high_water(connection)?;
    if tail_sequence != state.through_sequence {
        return Err(invalid(
            "original tail does not cover its committed high-water",
        ));
    }
    state.timestamp_high_water = state
        .timestamp_high_water
        .max(archive::read_archive_timestamp_floor(connection)?);
    save(connection, &state)?;
    connection.execute_batch(
        "DROP TABLE controller_storage_format;
         CREATE TABLE controller_storage_format(version INTEGER PRIMARY KEY CHECK(version=2),schema TEXT NOT NULL);
         INSERT INTO controller_storage_format VALUES(2,'roboguide.controller-storage/v2');
         PRAGMA user_version=2;",
    )?;
    read(connection)?;
    Ok(())
}

/// Maintains the original projection in the same transaction as this already-typed event append.
pub(super) fn append(
    connection: &Connection,
    row: &PersistedEvent,
    payload: &EventPayload,
) -> Result<(), SqliteEventLogError> {
    if connection.is_autocommit() {
        return Err(invalid("append has no original event transaction"));
    }
    let mut state = head(connection)?;
    if state.through_sequence.checked_add(1) != Some(row.sequence) {
        return Err(invalid("append does not extend original coverage"));
    }
    if archive::requires_catalog_history(payload) {
        retain(connection, &mut state, row)?;
    }
    state.through_sequence = row.sequence;
    state.timestamp_high_water = state.timestamp_high_water.max(row.timestamp_ms);
    save(connection, &state)
}

/// Validates original protected sources and coverage while avoiding every unrelated telemetry payload.
pub(super) fn read(connection: &Connection) -> Result<CatalogRecoveryEvents, SqliteEventLogError> {
    let version: u64 = connection.pragma_query_value(None, "user_version", |row| row.get(0))?;
    if version == 3 {
        super::telemetry_storage::validate(connection)?;
    } else {
        controller_storage::validate_version(connection, 2)?;
    }
    let state = head(connection)?;
    if state.through_sequence != high_water(connection)? {
        return Err(invalid(
            "projection is ahead of or behind original event history",
        ));
    }
    let (count, bytes): (u64, u64) = connection.query_row(
        "SELECT COUNT(*),COALESCE(SUM(length(CAST(data AS BLOB))),0) FROM catalog_recovery_events",
        [],
        |row| Ok((row.get(0)?, row.get(1)?)),
    )?;
    if count != state.records || bytes != state.bytes || bytes > MAX_BYTES || count > MAX_RECORDS {
        return Err(invalid(
            "protected rows are missing, extra or exceed original bounds",
        ));
    }
    let archived: BTreeMap<u64, PersistedEvent> = catalog_archive::read(connection)?
        .into_iter()
        .map(|row| (row.sequence, row))
        .collect();
    let mut validator = crate::artifact_retention::AdoptionSourceValidator::default();
    let mut statement = connection
        .prepare("SELECT sequence,data FROM catalog_recovery_events ORDER BY sequence")?;
    let mut original = connection.prepare("SELECT sequence,event_id,timestamp_ms,correlation_id,causation_id,payload_schema,payload_json FROM events WHERE sequence=?1")?;
    let mut digest = empty().digest;
    let mut result = CatalogRecoveryEvents {
        events: Vec::new(),
        timestamp_high_water: TimestampMs::new(state.timestamp_high_water),
        scanned_events: 0,
        scanned_payload_bytes: 0,
    };
    for item in statement.query_map([], |row| {
        Ok((row.get::<_, u64>(0)?, row.get::<_, String>(1)?))
    })? {
        let (sequence, data) = item?;
        let row: PersistedEvent =
            serde_json::from_str(&data).map_err(|error| invalid(&error.to_string()))?;
        let source = original.query_row([sequence], event_from_row).optional()?;
        let source = source.as_ref().or_else(|| archived.get(&sequence));
        if row.sequence != sequence
            || sequence > state.through_sequence
            || row.timestamp_ms > state.timestamp_high_water
            || source != Some(&row)
        {
            return Err(invalid("protected original envelope or coverage differs"));
        }
        let event = application_replication::decode_persisted_event(&row)?;
        if !archive::requires_catalog_history(event.payload()) {
            return Err(invalid("unrelated row in protected projection"));
        }
        validator
            .observe(&row, event.payload())
            .map_err(|error| invalid(&error))?;
        digest = extend_digest(&digest, &data);
        result.scanned_events += 1;
        result.scanned_payload_bytes += row.payload_json.len() as u64;
        result.events.push(event);
    }
    if digest != state.digest {
        return Err(invalid("ordered protected envelope checksum differs"));
    }
    Ok(result)
}

#[cfg(test)]
#[path = "catalog_checkpoint_tests.rs"]
mod tests;
