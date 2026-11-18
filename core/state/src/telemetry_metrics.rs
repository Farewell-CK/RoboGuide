//! Transactional, checksummed telemetry storage counters without historical table scans.

use super::*;

/// Explicit interpretation of the original cumulative counters and current hot-store gauges.
pub const TELEMETRY_STORAGE_METRICS_SCHEMA: &str = "roboguide.telemetry-storage-metrics/v0.1";

/// Original append counters and current storage gauges maintained beside their SQL effects.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TelemetryStorageCounters {
    /// First measured sequence, fixed to the verified original history floor plus one at migration.
    /// Older archived pre-v3 rows have no invented historical byte/counter measurements.
    pub accounting_start_event_sequence: u64,
    /// Committed event appends in the measured span, including non-State events and migrated rows.
    pub appended_events_total: u64,
    /// Committed observations in the measured span; cached repetition is an independent event.
    pub observation_events_total: u64,
    /// Sum of the exact original value UTF-8 lengths for every committed observation.
    pub logical_value_bytes_total: u64,
    /// Observations which actually created a previously absent immutable scoped body.
    pub body_creation_misses_total: u64,
    /// Observations whose exact scoped body already existed; never a freshness conclusion.
    pub body_reuse_hits_total: u64,
    /// Original value UTF-8 bytes actually inserted into new bodies, excluding SQLite overhead.
    pub created_body_value_bytes_total: u64,
    /// Event rows currently retained in the hot SQL event table.
    pub hot_events: u64,
    /// State observations currently retained in the hot SQL event table.
    pub hot_observations: u64,
    /// Immutable bodies currently retained in the hot SQL body table, including required references.
    pub hot_bodies: u64,
    /// Exact current body value UTF-8 bytes, excluding references, envelopes, indices and pages.
    pub hot_body_value_bytes: u64,
    /// Original append high-water, initially the pre-v3 archive floor even with no measured events.
    pub last_appended_event_sequence: u64,
}

impl Default for TelemetryStorageCounters {
    /// Uses an empty unarchived origin; migration replaces only its verified original floor.
    fn default() -> Self {
        Self {
            accounting_start_event_sequence: 1,
            appended_events_total: 0,
            observation_events_total: 0,
            logical_value_bytes_total: 0,
            body_creation_misses_total: 0,
            body_reuse_hits_total: 0,
            created_body_value_bytes_total: 0,
            hot_events: 0,
            hot_observations: 0,
            hot_bodies: 0,
            hot_body_value_bytes: 0,
            last_appended_event_sequence: 0,
        }
    }
}

/// Consistent bounded storage observation; ordinary API reads cannot alter authority or freshness.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct TelemetryStorageMetrics {
    /// Closed interpretation of the counter meanings and SQL-page units.
    pub schema: String,
    /// Transactional cumulative values and current hot-table gauges.
    pub counters: TelemetryStorageCounters,
    /// Current SQLite main-database page count, independent of historical event count.
    pub sqlite_page_count: u64,
    /// Current internally reusable SQLite pages; this is not returned filesystem capacity.
    pub sqlite_freelist_pages: u64,
    /// Exact SQLite page size in bytes.
    pub sqlite_page_size_bytes: u64,
    /// SQLite main-database allocated bytes; WAL and external archives are separate measurements.
    pub sqlite_allocated_bytes: u64,
    /// Currently reusable bytes inside SQLite; no vacuum or deletion is implied by this number.
    pub sqlite_reusable_bytes: u64,
}

/// Maps metrics corruption to the original storage refusal boundary instead of inventing zeroes.
fn invalid(message: &str) -> SqliteEventLogError {
    SqliteEventLogError::Codec(format!("telemetry storage metrics: {message}"))
}

/// Hashes the exact persisted singleton bytes under an explicit independent digest domain.
fn digest(data: &str) -> String {
    let mut hash = Sha256::new();
    hash.update(TELEMETRY_STORAGE_METRICS_SCHEMA.as_bytes());
    hash.update(b"\n");
    hash.update(data.as_bytes());
    format!("sha256:{:x}", hash.finalize())
}

/// Requires the same actual transaction as the event/body mutation, never a separate autocommit.
fn require_transaction(connection: &Connection) -> Result<(), SqliteEventLogError> {
    if connection.is_autocommit() {
        return Err(invalid("mutation requires the original writer transaction"));
    }
    Ok(())
}

/// Refuses arithmetic wrapping and SQLite's signed integer exhaustion before a writer can commit.
fn add(left: u64, right: u64) -> Result<u64, SqliteEventLogError> {
    left.checked_add(right)
        .filter(|value| *value <= i64::MAX as u64)
        .ok_or_else(|| invalid("counter overflow"))
}

/// Decreases only a current hot-store gauge; missing coverage cannot silently clamp to zero.
fn subtract(left: u64, right: u64) -> Result<u64, SqliteEventLogError> {
    left.checked_sub(right)
        .ok_or_else(|| invalid("retirement exceeds the original hot-store gauge"))
}

/// Checks the small closed head's internal relations without reading any historical body or event.
fn validate(counters: &TelemetryStorageCounters) -> Result<(), SqliteEventLogError> {
    let cumulative = [
        counters.accounting_start_event_sequence,
        counters.appended_events_total,
        counters.observation_events_total,
        counters.logical_value_bytes_total,
        counters.body_creation_misses_total,
        counters.body_reuse_hits_total,
        counters.created_body_value_bytes_total,
        counters.hot_events,
        counters.hot_observations,
        counters.hot_bodies,
        counters.hot_body_value_bytes,
        counters.last_appended_event_sequence,
    ];
    let covered_tail = counters
        .accounting_start_event_sequence
        .checked_sub(1)
        .and_then(|floor| floor.checked_add(counters.appended_events_total));
    if cumulative.iter().any(|value| *value > i64::MAX as u64)
        || counters.accounting_start_event_sequence == 0
        || covered_tail != Some(counters.last_appended_event_sequence)
        || counters.observation_events_total > counters.appended_events_total
        || add(
            counters.body_creation_misses_total,
            counters.body_reuse_hits_total,
        )? != counters.observation_events_total
        || counters.hot_events > counters.appended_events_total
        || counters.hot_observations > counters.hot_events
        || counters.hot_observations > counters.observation_events_total
        || counters.hot_bodies > counters.hot_observations
        || counters.hot_bodies > counters.body_creation_misses_total
        || counters.hot_body_value_bytes > counters.created_body_value_bytes_total
        || counters.created_body_value_bytes_total > counters.logical_value_bytes_total
        || counters.logical_value_bytes_total < counters.observation_events_total
        || (counters.observation_events_total == 0) != (counters.logical_value_bytes_total == 0)
        || (counters.body_creation_misses_total == 0)
            != (counters.created_body_value_bytes_total == 0)
        || (counters.hot_bodies == 0) != (counters.hot_body_value_bytes == 0)
        || (counters.hot_observations > 0 && counters.hot_bodies == 0)
        || counters.last_appended_event_sequence < counters.appended_events_total
    {
        return Err(invalid("counter identities or hot-store coverage differ"));
    }
    Ok(())
}

/// Reads only the original durable archive floor, refusing malformed or exhausted spelling.
fn original_history_floor(connection: &Connection) -> Result<u64, SqliteEventLogError> {
    let raw_floor: Option<String> = connection
        .query_row(
            "SELECT value FROM event_log_metadata WHERE key='history_floor'",
            [],
            |row| row.get(0),
        )
        .optional()?;
    let floor = match raw_floor {
        None => 0,
        Some(raw) => {
            let parsed = raw
                .parse::<u64>()
                .map_err(|_| invalid("original history floor is malformed"))?;
            if parsed.to_string() != raw {
                return Err(invalid("original history floor spelling differs"));
            }
            parsed
        }
    };
    if floor >= i64::MAX as u64 {
        return Err(invalid(
            "original history floor exhausts the event sequence",
        ));
    }
    Ok(floor)
}

/// Creates the mandatory empty head before the explicit v3 migration streams actual original rows.
pub(super) fn initialize(connection: &Connection) -> Result<(), SqliteEventLogError> {
    require_transaction(connection)?;
    connection.execute_batch(
        "CREATE TABLE telemetry_storage_metrics(singleton INTEGER PRIMARY KEY CHECK(singleton=1),schema TEXT NOT NULL,data_json TEXT NOT NULL,digest TEXT NOT NULL);",
    )?;
    let floor = original_history_floor(connection)?;
    let baseline = TelemetryStorageCounters {
        accounting_start_event_sequence: floor
            .checked_add(1)
            .filter(|value| *value <= i64::MAX as u64)
            .ok_or_else(|| invalid("original history floor exhausts the event sequence"))?,
        last_appended_event_sequence: floor,
        ..TelemetryStorageCounters::default()
    };
    validate(&baseline)?;
    let data = serde_json::to_string(&baseline).map_err(|error| invalid(&error.to_string()))?;
    connection.execute(
        "INSERT INTO telemetry_storage_metrics VALUES(1,?1,?2,?3)",
        params![TELEMETRY_STORAGE_METRICS_SCHEMA, data, digest(&data)],
    )?;
    Ok(())
}

/// Reads and verifies the exact original small head; missing/corrupt metrics never rebuild silently.
pub(super) fn read(
    connection: &Connection,
) -> Result<TelemetryStorageCounters, SqliteEventLogError> {
    let columns = super::table_columns(connection, "telemetry_storage_metrics")?;
    let required = ["singleton", "schema", "data_json", "digest"];
    if columns.len() != required.len() || required.iter().any(|column| !columns.contains(*column)) {
        return Err(invalid("mandatory metrics layout is missing or changed"));
    }
    let (schema, data, original_digest): (String, String, String) = connection.query_row(
        "SELECT schema,data_json,digest FROM telemetry_storage_metrics WHERE singleton=1",
        [],
        |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
    )?;
    let extra_head: bool = connection.query_row(
        "SELECT EXISTS(SELECT 1 FROM telemetry_storage_metrics WHERE singleton<1 OR singleton>1 LIMIT 1)",
        [],
        |row| row.get(0),
    )?;
    if schema != TELEMETRY_STORAGE_METRICS_SCHEMA
        || extra_head
        || data.len() > 4096
        || digest(&data) != original_digest
    {
        return Err(invalid(
            "metrics generation, size or original checksum differs",
        ));
    }
    let counters = serde_json::from_str(&data).map_err(|error| invalid(&error.to_string()))?;
    validate(&counters)?;
    if counters.accounting_start_event_sequence > original_history_floor(connection)? + 1 {
        return Err(invalid(
            "original accounting start skips unarchived source history",
        ));
    }
    Ok(counters)
}

/// Persists the validated original counter head inside its caller's still-open SQL transaction.
fn save(
    connection: &Connection,
    counters: &TelemetryStorageCounters,
) -> Result<(), SqliteEventLogError> {
    require_transaction(connection)?;
    validate(counters)?;
    let data = serde_json::to_string(counters).map_err(|error| invalid(&error.to_string()))?;
    let updated = connection.execute(
        "UPDATE telemetry_storage_metrics SET data_json=?1,digest=?2 WHERE singleton=1 AND schema=?3",
        params![data, digest(&data), TELEMETRY_STORAGE_METRICS_SCHEMA],
    )?;
    if updated != 1 {
        return Err(invalid("original metrics head disappeared"));
    }
    Ok(())
}

/// Accounts one actual SQL append after its body/event insert and before the original Commit.
///
/// `logical_value_bytes` is the exact original value slice length, or None for a non-State event.
/// `created_body` must come from the actual immutable-body INSERT row count, not a prior SELECT.
/// The counters follow all caller savepoints and rollbacks and never grant observation freshness.
pub(super) fn record_append(
    connection: &Connection,
    event_sequence: u64,
    logical_value_bytes: Option<u64>,
    created_body: bool,
) -> Result<(), SqliteEventLogError> {
    require_transaction(connection)?;
    let mut counters = read(connection)?;
    if event_sequence == 0
        || counters.last_appended_event_sequence.checked_add(1) != Some(event_sequence)
        || (created_body && logical_value_bytes.is_none())
    {
        return Err(invalid("append identity or body accounting differs"));
    }
    counters.appended_events_total = add(counters.appended_events_total, 1)?;
    counters.hot_events = add(counters.hot_events, 1)?;
    counters.last_appended_event_sequence = event_sequence;
    if let Some(bytes) = logical_value_bytes {
        if bytes == 0 {
            return Err(invalid("an original JSON value cannot be empty"));
        }
        counters.observation_events_total = add(counters.observation_events_total, 1)?;
        counters.logical_value_bytes_total = add(counters.logical_value_bytes_total, bytes)?;
        counters.hot_observations = add(counters.hot_observations, 1)?;
        if created_body {
            counters.body_creation_misses_total = add(counters.body_creation_misses_total, 1)?;
            counters.created_body_value_bytes_total =
                add(counters.created_body_value_bytes_total, bytes)?;
            counters.hot_bodies = add(counters.hot_bodies, 1)?;
            counters.hot_body_value_bytes = add(counters.hot_body_value_bytes, bytes)?;
        } else {
            if counters.hot_bodies == 0 || counters.hot_body_value_bytes < bytes {
                return Err(invalid(
                    "reused body is absent from the original hot-store gauge",
                ));
            }
            counters.body_reuse_hits_total = add(counters.body_reuse_hits_total, 1)?;
        }
    }
    save(connection, &counters)
}

/// Accounts only actual deleted hot rows/bodies after a verified original archive publication.
///
/// Cumulative measurements remain original history. The caller supplies exact bounded deletion
/// row counts and actual removed body bytes under the same short transaction; this grants no GC.
pub(super) fn record_retirement(
    connection: &Connection,
    events: u64,
    observations: u64,
    bodies: u64,
    body_value_bytes: u64,
) -> Result<(), SqliteEventLogError> {
    require_transaction(connection)?;
    if observations > events || (bodies == 0) != (body_value_bytes == 0) {
        return Err(invalid("retirement counts are inconsistent"));
    }
    let mut counters = read(connection)?;
    if events - observations > counters.hot_events - counters.hot_observations {
        return Err(invalid(
            "retirement removes more non-observation rows than remain hot",
        ));
    }
    counters.hot_events = subtract(counters.hot_events, events)?;
    counters.hot_observations = subtract(counters.hot_observations, observations)?;
    counters.hot_bodies = subtract(counters.hot_bodies, bodies)?;
    counters.hot_body_value_bytes = subtract(counters.hot_body_value_bytes, body_value_bytes)?;
    save(connection, &counters)
}

/// Produces a coherent small observation using only its persisted head and constant-time pragmas.
fn snapshot(connection: &Connection) -> Result<TelemetryStorageMetrics, SqliteEventLogError> {
    let counters = read(connection)?;
    let page_count: u64 = connection.pragma_query_value(None, "page_count", |row| row.get(0))?;
    let freelist: u64 = connection.pragma_query_value(None, "freelist_count", |row| row.get(0))?;
    let page_size: u64 = connection.pragma_query_value(None, "page_size", |row| row.get(0))?;
    if freelist > page_count || page_size == 0 {
        return Err(invalid("SQLite page accounting differs"));
    }
    Ok(TelemetryStorageMetrics {
        schema: TELEMETRY_STORAGE_METRICS_SCHEMA.into(),
        counters,
        sqlite_page_count: page_count,
        sqlite_freelist_pages: freelist,
        sqlite_page_size_bytes: page_size,
        sqlite_allocated_bytes: page_count
            .checked_mul(page_size)
            .ok_or_else(|| invalid("SQLite page size overflow"))?,
        sqlite_reusable_bytes: freelist
            .checked_mul(page_size)
            .ok_or_else(|| invalid("SQLite freelist size overflow"))?,
    })
}

impl SqliteEventLog {
    /// Observes committed v3 storage metrics without reading or modifying historical authority.
    ///
    /// This rejects an open event batch rather than reporting uncommitted candidate counters.
    /// File-system/WAL/archive byte measurements belong to their existing observers, separately.
    pub fn telemetry_storage_metrics(
        &self,
    ) -> Result<TelemetryStorageMetrics, SqliteEventLogError> {
        let batch = self
            .batch_start_number
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        if batch.is_some() {
            return Err(invalid("cannot expose an uncommitted event batch"));
        }
        if self.events_table != "controller_v3_events" {
            return Err(invalid("metrics require explicit storage v3"));
        }
        let mut connection = self
            .connection
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Deferred)?;
        let result = snapshot(&transaction)?;
        transaction.commit()?;
        Ok(result)
    }
}

#[cfg(test)]
#[path = "telemetry_metrics_tests.rs"]
mod tests;
