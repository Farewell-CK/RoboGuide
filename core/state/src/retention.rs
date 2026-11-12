//! Versioned bounded history-retention policy and fail-closed candidate assessment.

use super::*;

/// Closed schema of the persisted retention policy.
pub const RETENTION_POLICY_SCHEMA: &str = "roboguide.retention-policy/v0.1";
/// Maximum original input bytes inspected by one maintenance batch.
pub const MAX_RETENTION_INPUT_BYTES: u64 = 4 * 1024 * 1024;
/// Existing Controller worker cadence; it does not create a separate service.
pub const RETENTION_INTERVAL_MS: u64 = 60_000;

/// Explicit operator choice; migration starts without authorizing removal of any data.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum RetentionMode {
    /// No automatic archival or expiry work is requested.
    Disabled,
    /// Evaluate bounded work and its blockers without changing historical evidence.
    Evaluate,
    /// Allow the existing archive protocol to process fully covered, unpinned batches.
    Enabled,
}

/// Administrator-owned defaults, independent from Node freshness and heartbeat periods.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RetentionPolicy {
    /// Closed schema marker; unknown policy interpretation is refused.
    pub schema: String,
    /// Explicit operating mode, initially evaluation only.
    pub mode: RetentionMode,
    /// Minimum duration ordinary telemetry remains in the hot event store.
    pub hot_window_ms: u64,
    /// Minimum duration after durable archive publication before ordinary data may expire.
    pub archive_window_ms: u64,
    /// Maximum original event rows assessed in one pass.
    pub max_events: u64,
    /// Maximum exact original input bytes assessed in one pass.
    pub max_input_bytes: u64,
}

impl Default for RetentionPolicy {
    /// Starts each explicit v3 installation with the authorized conservative defaults.
    fn default() -> Self {
        Self {
            schema: RETENTION_POLICY_SCHEMA.into(),
            mode: RetentionMode::Evaluate,
            hot_window_ms: 24 * 60 * 60 * 1_000,
            archive_window_ms: 30 * 24 * 60 * 60 * 1_000,
            max_events: MAX_ARCHIVE_EVENTS,
            max_input_bytes: MAX_RETENTION_INPUT_BYTES,
        }
    }
}

impl RetentionPolicy {
    /// Refuses unknown formats, invalid durations and requests above hard resource limits.
    pub fn validate(&self) -> Result<(), SqliteEventLogError> {
        if self.schema != RETENTION_POLICY_SCHEMA
            || self.hot_window_ms == 0
            || self.archive_window_ms == 0
            || self.hot_window_ms > i64::MAX as u64
            || self.archive_window_ms > i64::MAX as u64
            || self.max_events == 0
            || self.max_events > MAX_ARCHIVE_EVENTS
            || self.max_input_bytes == 0
            || self.max_input_bytes > MAX_RETENTION_INPUT_BYTES
        {
            return Err(invalid("unsupported policy or invalid hard bounds"));
        }
        Ok(())
    }
}

/// Exact administrator request retained so a lost reply can be retried unchanged.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RetentionPolicyCommand {
    /// Globally unique administrator command identity, unrelated to internal batch names.
    pub command_id: String,
    /// Revision observed by the caller; conflicting updates never replace current policy.
    pub expected_revision: u64,
    /// Complete requested policy rather than an order-dependent partial patch.
    pub policy: RetentionPolicy,
}

/// Original receipt returned unchanged for an exact duplicate command.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RetentionPolicyReceipt {
    /// Closed interpretation of the original acceptance receipt.
    pub schema: String,
    /// Original administrator command identity.
    pub command_id: String,
    /// Previous revision to which the command was applied.
    pub previous_revision: u64,
    /// Resulting revision; each accepted new command advances it exactly once.
    pub revision: u64,
    /// Controller-local acceptance time; retry never renews this clock.
    pub accepted_at_ms: u64,
    /// Digest of the exact persisted resulting policy bytes.
    pub policy_digest: String,
}

/// Current policy snapshot and its persisted monotonic maintenance clock floor.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RetentionPolicyState {
    /// Current administrator revision, initially zero.
    pub revision: u64,
    /// Validated current complete policy.
    pub policy: RetentionPolicy,
    /// Highest persisted Controller time seen by maintenance or policy acceptance.
    pub clock_high_water_ms: u64,
}

/// Exact event sequence dependency supplied by authoritative task/recovery readers.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RetentionPin {
    /// Stable original reference identity; never synthesized from command-name prefixes.
    pub reference_id: String,
    /// First original event sequence required by this reference.
    pub start_sequence: u64,
    /// Last original event sequence required by this reference, inclusive.
    pub end_sequence: u64,
}

/// Explicit coverage proof from the original writer's task, Unknown and recovery references.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct RetentionProtection {
    /// Highest event sequence through which every relevant reference source was examined.
    pub complete_through_sequence: u64,
    /// Exact ranges still required by active tasks, Unknown, decisions, backup or replication.
    pub pins: Vec<RetentionPin>,
}

/// A single bounded hot-store range; publication must still use the original archive protocol.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RetentionBatch {
    /// Original first included sequence, immediately after the persisted archive floor.
    pub start_sequence: u64,
    /// Original final sequence included within both hard resource limits.
    pub end_sequence: u64,
    /// Actual number of original events in this contiguous range.
    pub event_count: u64,
    /// Exact original payload and envelope field bytes inspected in this batch.
    pub input_bytes: u64,
    /// Ordinary telemetry rows, subject to hot/archive duration only when unreferenced.
    pub ordinary_events: u64,
    /// Authority, task, cancellation, catalog or other non-ordinary original evidence.
    pub protected_events: u64,
    /// Oldest Controller-recorded timestamp found in this bounded range.
    pub oldest_timestamp_ms: u64,
    /// Explicit live references overlapping this range, retained even in enabled mode.
    pub protecting_references: Vec<String>,
}

/// Bounded maintenance assessment; these measurements never imply a full-history scan.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RetentionStatus {
    /// Current original policy revision and operating mode.
    pub policy: RetentionPolicyState,
    /// Controller time used for this assessment, not a Node-supplied timestamp.
    pub evaluated_at_ms: u64,
    /// Persisted contiguous archive coverage before this assessment.
    pub history_floor: u64,
    /// Maximum Controller checkpoint coverage used when judging removal eligibility.
    pub checkpoint_sequence: u64,
    /// Current bounded archival candidate, if any.
    pub batch: Option<RetentionBatch>,
    /// More hot rows remain after the inspected range; their amount is intentionally uncounted.
    pub more_rows: bool,
    /// Concrete reasons automatic archival is withheld; unknown references fail closed.
    pub blockers: Vec<String>,
}

/// Wraps policy corruption and unauthorized clock replacement under the ordinary storage contract.
fn invalid(message: &str) -> SqliteEventLogError {
    SqliteEventLogError::ArchivePolicy(format!("retention: {message}"))
}

/// Hashes exact UTF-8 bytes using the storage's stable digest representation.
fn digest(data: &str) -> String {
    format!("sha256:{:x}", Sha256::digest(data.as_bytes()))
}

/// Requires the original FIFO writer transaction; policy and clock updates never autocommit.
fn require_transaction(connection: &Connection) -> Result<(), SqliteEventLogError> {
    if connection.is_autocommit() {
        return Err(invalid("mutation requires the original writer transaction"));
    }
    Ok(())
}

/// Installs retention only inside the explicit v3 migration transaction, never during a read.
pub(super) fn initialize(connection: &Connection) -> Result<(), SqliteEventLogError> {
    require_transaction(connection)?;
    let existing: bool = connection.query_row(
        "SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE name IN ('retention_policy','retention_policy_commands'))",
        [],
        |row| row.get(0),
    )?;
    if existing {
        return Err(invalid("refusing to replace existing retention authority"));
    }
    connection.execute_batch(
        "CREATE TABLE retention_policy(singleton INTEGER PRIMARY KEY CHECK(singleton=1),revision INTEGER NOT NULL,policy_json TEXT NOT NULL,policy_digest TEXT NOT NULL,clock_high_water_ms INTEGER NOT NULL);
         CREATE TABLE retention_policy_commands(command_id TEXT PRIMARY KEY NOT NULL,revision INTEGER UNIQUE NOT NULL,command_json TEXT NOT NULL,command_digest TEXT NOT NULL,receipt_json TEXT NOT NULL,receipt_digest TEXT NOT NULL);",
    )?;
    let data = serde_json::to_string(&RetentionPolicy::default())
        .map_err(|error| invalid(&error.to_string()))?;
    connection.execute(
        "INSERT INTO retention_policy VALUES(1,0,?1,?2,0)",
        params![data, digest(&data)],
    )?;
    Ok(())
}

/// Validates the required policy authority and its original current acceptance without rebuilding.
pub(super) fn validate(connection: &Connection) -> Result<(), SqliteEventLogError> {
    let current = policy(connection)?;
    let receipt: Option<(String, String, String, String)> = connection
        .query_row(
            "SELECT command_json,command_digest,receipt_json,receipt_digest FROM retention_policy_commands WHERE revision=?1",
            [current.revision],
            |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?)),
        )
        .optional()?;
    match (current.revision, receipt) {
        (0, None) if current.policy == RetentionPolicy::default() => {}
        (0, _) => return Err(invalid("initial policy or authority was replaced")),
        (_, Some((command, command_digest, receipt, receipt_digest))) => {
            if digest(&command) != command_digest || digest(&receipt) != receipt_digest {
                return Err(invalid(
                    "current policy original acceptance checksum differs",
                ));
            }
            let command: RetentionPolicyCommand =
                serde_json::from_str(&command).map_err(|error| invalid(&error.to_string()))?;
            let receipt: RetentionPolicyReceipt =
                serde_json::from_str(&receipt).map_err(|error| invalid(&error.to_string()))?;
            let policy_data = serde_json::to_string(&current.policy)
                .map_err(|error| invalid(&error.to_string()))?;
            if command.policy != current.policy
                || receipt.schema != "roboguide.retention-policy-receipt/v0.1"
                || receipt.command_id != command.command_id
                || receipt.revision != current.revision
                || receipt.previous_revision.checked_add(1) != Some(receipt.revision)
                || command.expected_revision != receipt.previous_revision
                || receipt.policy_digest != digest(&policy_data)
                || receipt.accepted_at_ms > current.clock_high_water_ms
            {
                return Err(invalid(
                    "current policy is detached from its original receipt",
                ));
            }
        }
        (_, None) => return Err(invalid("current policy acceptance receipt is missing")),
    }
    Ok(())
}

/// Reads the exact single policy row and validates format, checksum and integer bounds.
pub(super) fn policy(connection: &Connection) -> Result<RetentionPolicyState, SqliteEventLogError> {
    let count: u64 = connection.query_row("SELECT COUNT(*) FROM retention_policy", [], |row| {
        row.get(0)
    })?;
    if count != 1 {
        return Err(invalid("policy authority is missing or not unique"));
    }
    let (revision, data, expected, clock): (u64, String, String, u64) = connection.query_row(
        "SELECT revision,policy_json,policy_digest,clock_high_water_ms FROM retention_policy WHERE singleton=1",
        [],
        |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?)),
    )?;
    if digest(&data) != expected {
        return Err(invalid("policy checksum differs"));
    }
    let policy: RetentionPolicy =
        serde_json::from_str(&data).map_err(|error| invalid(&error.to_string()))?;
    policy.validate()?;
    if revision > i64::MAX as u64 || clock > i64::MAX as u64 {
        return Err(invalid("policy counters exceed SQLite representation"));
    }
    Ok(RetentionPolicyState {
        revision,
        policy,
        clock_high_water_ms: clock,
    })
}

/// Updates one exact policy under optimistic revision and immutable command-id semantics.
pub(super) fn update_policy(
    connection: &Connection,
    command: &RetentionPolicyCommand,
    now_ms: u64,
) -> Result<RetentionPolicyReceipt, SqliteEventLogError> {
    require_transaction(connection)?;
    command.policy.validate()?;
    if command.command_id.trim().is_empty() || command.command_id.len() > 256 {
        return Err(invalid("invalid original command identity"));
    }
    let data = serde_json::to_string(command).map_err(|error| invalid(&error.to_string()))?;
    let prior: Option<(String, String, String, String)> = connection
        .query_row(
            "SELECT command_json,command_digest,receipt_json,receipt_digest FROM retention_policy_commands WHERE command_id=?1",
            [&command.command_id],
            |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?)),
        )
        .optional()?;
    if let Some((prior, prior_digest, receipt, receipt_digest)) = prior {
        if digest(&prior) != prior_digest || digest(&receipt) != receipt_digest || prior != data {
            return Err(invalid("original command conflicts or receipt is corrupt"));
        }
        let receipt: RetentionPolicyReceipt =
            serde_json::from_str(&receipt).map_err(|error| invalid(&error.to_string()))?;
        if receipt.schema != "roboguide.retention-policy-receipt/v0.1"
            || receipt.command_id != command.command_id
            || receipt.previous_revision != command.expected_revision
            || receipt.revision != command.expected_revision.checked_add(1).unwrap_or(0)
            || receipt.policy_digest
                != digest(
                    &serde_json::to_string(&command.policy)
                        .map_err(|error| invalid(&error.to_string()))?,
                )
        {
            return Err(invalid("original receipt is detached from its command"));
        }
        return Ok(receipt);
    }
    validate(connection)?;
    let current = policy(connection)?;
    if current.revision != command.expected_revision {
        return Err(invalid("policy revision conflict"));
    }
    if now_ms < current.clock_high_water_ms || now_ms > i64::MAX as u64 {
        return Err(invalid(
            "Controller clock regressed or exceeds supported range",
        ));
    }
    let revision = current
        .revision
        .checked_add(1)
        .filter(|value| *value <= i64::MAX as u64)
        .ok_or_else(|| invalid("policy revision exhausted"))?;
    let policy_json =
        serde_json::to_string(&command.policy).map_err(|error| invalid(&error.to_string()))?;
    let receipt = RetentionPolicyReceipt {
        schema: "roboguide.retention-policy-receipt/v0.1".into(),
        command_id: command.command_id.clone(),
        previous_revision: current.revision,
        revision,
        accepted_at_ms: now_ms,
        policy_digest: digest(&policy_json),
    };
    let receipt_json =
        serde_json::to_string(&receipt).map_err(|error| invalid(&error.to_string()))?;
    connection.execute(
        "UPDATE retention_policy SET revision=?1,policy_json=?2,policy_digest=?3,clock_high_water_ms=?4 WHERE singleton=1",
        params![revision, policy_json, receipt.policy_digest, now_ms],
    )?;
    connection.execute(
        "INSERT INTO retention_policy_commands VALUES(?1,?2,?3,?4,?5,?6)",
        params![
            command.command_id,
            revision,
            data,
            digest(&data),
            receipt_json,
            digest(&receipt_json)
        ],
    )?;
    Ok(receipt)
}

/// Records the original Controller clock before assessing work; backwards time never resets it.
pub(super) fn observe_clock(
    connection: &Connection,
    now_ms: u64,
) -> Result<(), SqliteEventLogError> {
    require_transaction(connection)?;
    validate(connection)?;
    let current = policy(connection)?;
    if now_ms < current.clock_high_water_ms || now_ms > i64::MAX as u64 {
        return Err(invalid(
            "Controller clock regressed or exceeds supported range",
        ));
    }
    connection.execute(
        "UPDATE retention_policy SET clock_high_water_ms=?1 WHERE singleton=1",
        [now_ms],
    )?;
    Ok(())
}

/// Classifies only the three typed routine telemetry variants; all other evidence is protected.
pub(super) fn ordinary(payload: &EventPayload) -> bool {
    match payload {
        EventPayload::StateRecordObserved { record } => {
            matches!(
                record.key().object().class(),
                domain::StateObjectClass::Node | domain::StateObjectClass::World
            ) && matches!(
                record.key().semantic(),
                domain::StateSemantic::Reported | domain::StateSemantic::Observed
            ) && matches!(record.key().source(), domain::StateSource::Node { .. })
        }
        EventPayload::NodeHeartbeatAccepted { .. }
        | EventPayload::NodeSessionLivenessAccepted { .. } => true,
        _ => false,
    }
}

/// Counts exact original UTF-8 fields and fixed integer fields, without JSON normalization.
fn original_bytes(event: &PersistedEvent) -> Result<u64, SqliteEventLogError> {
    [
        event.event_id.len(),
        event.correlation_id.len(),
        event.causation_id.as_ref().map_or(0, String::len),
        event.payload_schema.len(),
        event.payload_json.len(),
        16,
    ]
    .into_iter()
    .try_fold(0u64, |sum, size| {
        sum.checked_add(size as u64)
            .ok_or_else(|| invalid("original event byte count overflow"))
    })
}

/// Examines only one bounded original prefix, requiring explicit reference and checkpoint coverage.
pub(super) fn evaluate(
    connection: &Connection,
    now_ms: u64,
    checkpoint_sequence: u64,
    protection: &RetentionProtection,
) -> Result<RetentionStatus, SqliteEventLogError> {
    let policy = policy(connection)?;
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
                .map_err(|_| invalid("invalid archive floor"))
        })
        .transpose()?
        .unwrap_or(0);
    let mut status = RetentionStatus {
        policy,
        evaluated_at_ms: now_ms,
        history_floor: floor,
        checkpoint_sequence,
        batch: None,
        more_rows: false,
        blockers: Vec::new(),
    };
    if now_ms < status.policy.clock_high_water_ms || now_ms > i64::MAX as u64 {
        status.blockers.push("Controller clock regressed".into());
        return Ok(status);
    }
    for pin in &protection.pins {
        if pin.reference_id.trim().is_empty()
            || pin.start_sequence == 0
            || pin.end_sequence < pin.start_sequence
        {
            return Err(invalid("invalid original protection reference"));
        }
    }
    if status.policy.policy.mode == RetentionMode::Disabled {
        status.blockers.push("retention is disabled".into());
        return Ok(status);
    }
    let cutoff = now_ms.saturating_sub(status.policy.policy.hot_window_ms);
    let mut statement = connection.prepare(
        "SELECT sequence,timestamp_ms,length(CAST(event_id AS BLOB))+length(CAST(correlation_id AS BLOB))+COALESCE(length(CAST(causation_id AS BLOB)),0)+length(CAST(payload_schema AS BLOB))+length(CAST(payload_json AS BLOB))+16 FROM events WHERE sequence>?1 ORDER BY sequence LIMIT ?2",
    )?;
    let mut rows = statement.query(params![floor, status.policy.policy.max_events])?;
    let mut expected = floor
        .checked_add(1)
        .ok_or_else(|| invalid("archive floor exhausted"))?;
    while let Some(row) = rows.next()? {
        let sequence: u64 = row.get(0)?;
        let timestamp_ms: u64 = row.get(1)?;
        let size: u64 = row.get(2)?;
        status.more_rows = true;
        if sequence != expected {
            return Err(invalid("hot prefix has missing original events"));
        }
        if sequence > checkpoint_sequence {
            status
                .blockers
                .push("checkpoint does not cover the next original event".into());
            break;
        }
        if sequence > protection.complete_through_sequence {
            status
                .blockers
                .push("protection reference coverage is incomplete".into());
            break;
        }
        if timestamp_ms >= cutoff {
            status
                .blockers
                .push("next original event is inside the hot retention window".into());
            break;
        }
        if status
            .batch
            .as_ref()
            .map_or(0, |batch| batch.input_bytes)
            .saturating_add(size)
            > status.policy.policy.max_input_bytes
        {
            if status.batch.is_none() {
                status
                    .blockers
                    .push("next original event exceeds the input byte limit".into());
            }
            break;
        }
        let event = connection.query_row(
            "SELECT sequence,event_id,timestamp_ms,correlation_id,causation_id,payload_schema,payload_json FROM events WHERE sequence=?1",
            [sequence],
            event_from_row,
        )?;
        if original_bytes(&event)? != size {
            return Err(invalid("original event changed during assessment"));
        }
        validate_persisted_event(&event)?;
        let payload: EventPayload = serde_json::from_str(&event.payload_json)
            .map_err(|error| invalid(&error.to_string()))?;
        if let Some(batch) = &status.batch
            && (batch.ordinary_events > 0) != ordinary(&payload)
        {
            break;
        }
        let batch = status.batch.get_or_insert_with(|| RetentionBatch {
            start_sequence: event.sequence,
            end_sequence: event.sequence,
            event_count: 0,
            input_bytes: 0,
            ordinary_events: 0,
            protected_events: 0,
            oldest_timestamp_ms: event.timestamp_ms,
            protecting_references: Vec::new(),
        });
        batch.end_sequence = event.sequence;
        batch.event_count += 1;
        batch.input_bytes += size;
        batch.oldest_timestamp_ms = batch.oldest_timestamp_ms.min(event.timestamp_ms);
        if ordinary(&payload) {
            batch.ordinary_events += 1;
        } else {
            batch.protected_events += 1;
        }
        for pin in &protection.pins {
            if pin.start_sequence <= event.sequence && pin.end_sequence >= event.sequence {
                batch.protecting_references.push(pin.reference_id.clone());
            }
        }
        expected += 1;
        status.more_rows = false;
    }
    if let Some(batch) = &mut status.batch {
        batch.protecting_references.sort();
        batch.protecting_references.dedup();
        if !batch.protecting_references.is_empty() {
            status
                .blockers
                .push("original evidence has active protection references".into());
        }
        status.more_rows = connection.query_row(
            "SELECT EXISTS(SELECT 1 FROM events WHERE sequence>?1)",
            [batch.end_sequence],
            |row| row.get(0),
        )?;
    }
    if status.policy.policy.mode == RetentionMode::Evaluate {
        status
            .blockers
            .push("evaluation mode does not authorize archival or expiry".into());
    }
    Ok(status)
}

#[cfg(test)]
#[path = "retention_tests.rs"]
mod tests;
