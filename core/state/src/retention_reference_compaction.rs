//! Bounded retirement of expired, unreferenced original source identities after durable archival.

use super::*;
use std::collections::BTreeMap;
use std::fs::{File, OpenOptions};
use std::os::unix::fs::{MetadataExt, OpenOptionsExt};
use std::path::PathBuf;

/// Original-inode readonly WAL connection; its audit runs solely during outside-writer publication.
pub(in super::super) struct SourceRetirementReader {
    /// Original canonical Controller path, never an alternative snapshot supplied by an operator.
    path: PathBuf,
    /// Actual no-follow original inode retained through publication and finalization.
    file: File,
    /// Original device/inode identity bound before opening the readonly connection.
    identity: (u64, u64),
    /// Independent autocommit connection; no source writer mutex is needed for the full audit.
    connection: Connection,
}

/// Checksummed protected-reference identity frozen by a complete readonly snapshot audit.
#[derive(PartialEq, Eq)]
struct ProtectedHead {
    /// Original protected edge count, independent of ordinary source append volume.
    edges: u64,
    /// Original unresolved dependency count.
    gaps: u64,
    /// Ordered original protected-edge checksum.
    edges_digest: String,
    /// Ordered original unresolved-dependency checksum.
    gaps_digest: String,
    /// Any earlier archive prefix that never supplied complete original reference proof.
    archived_prefix_unresolved: bool,
}

/// Bounded candidate proof, without duplicated original State values.
struct SourceCandidate {
    /// Digest of the complete original source record at snapshot verification.
    record_digest: String,
    /// Verified original protection; later missing SQL edges cannot erase this fact.
    was_referenced: bool,
}

/// Opaque proof of original candidate dependencies; callers cannot invent unreferenced sources.
pub(in super::super) struct SourceRetirementProof {
    /// Original immutable Archive command identity.
    command_id: String,
    /// Exact original published prefix identity.
    segment_digest: String,
    /// Complete protected head whose later changes withhold source-index retirement.
    protected: ProtectedHead,
    /// Only this original bounded prefix's State source identities and protection facts.
    candidates: BTreeMap<u64, SourceCandidate>,
}

/// Copies only protected head fields; ordinary append coverage may advance during publication.
fn protected_head(head: &Head) -> ProtectedHead {
    ProtectedHead {
        edges: head.edges,
        gaps: head.gaps,
        edges_digest: head.edges_digest.clone(),
        gaps_digest: head.gaps_digest.clone(),
        archived_prefix_unresolved: head.archived_prefix_unresolved,
    }
}

impl SourceRetirementReader {
    /// Opens the original readonly WAL source under a short stable writer-held freeze.
    pub(in super::super) fn open(source: &Connection) -> Result<Self, SqliteEventLogError> {
        let path = source
            .path()
            .filter(|path| !path.is_empty())
            .map(PathBuf::from)
            .ok_or_else(|| {
                invalid("source retirement proof requires the original database file")
            })?;
        let metadata =
            std::fs::symlink_metadata(&path).map_err(|error| invalid(&error.to_string()))?;
        if !metadata.file_type().is_file() {
            return Err(invalid(
                "source retirement proof requires an original regular database",
            ));
        }
        let file = OpenOptions::new()
            .read(true)
            .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
            .open(&path)
            .map_err(|error| invalid(&error.to_string()))?;
        let held = file
            .metadata()
            .map_err(|error| invalid(&error.to_string()))?;
        if (held.dev(), held.ino()) != (metadata.dev(), metadata.ino()) {
            return Err(invalid(
                "source retirement original inode changed before opening",
            ));
        }
        let connection = Connection::open_with_flags(
            &path,
            rusqlite::OpenFlags::SQLITE_OPEN_READ_ONLY
                | rusqlite::OpenFlags::SQLITE_OPEN_NO_MUTEX
                | rusqlite::OpenFlags::SQLITE_OPEN_NOFOLLOW,
        )?;
        connection.busy_timeout(std::time::Duration::from_millis(250))?;
        connection.execute_batch("PRAGMA query_only=ON")?;
        super::super::telemetry_storage::install_functions(&connection)?;
        let reader = Self {
            path,
            file,
            identity: (held.dev(), held.ino()),
            connection,
        };
        reader.verify_identity()?;
        Ok(reader)
    }

    /// Refuses descriptor/path replacement before or after the outside-writer readonly audit.
    pub(in super::super) fn verify_identity(&self) -> Result<(), SqliteEventLogError> {
        let current =
            std::fs::symlink_metadata(&self.path).map_err(|error| invalid(&error.to_string()))?;
        let held = self
            .file
            .metadata()
            .map_err(|error| invalid(&error.to_string()))?;
        if !current.file_type().is_file()
            || !held.file_type().is_file()
            || (current.dev(), current.ino()) != self.identity
            || (held.dev(), held.ino()) != self.identity
        {
            return Err(invalid(
                "source retirement original database inode/path changed",
            ));
        }
        Ok(())
    }

    /// Runs full original index verification outside the writer and freezes only bounded source pins.
    pub(in super::super) fn capture(
        &self,
        intent: &super::super::archive_operation::ArchiveIntent,
        events: &[PersistedEvent],
    ) -> Result<SourceRetirementProof, SqliteEventLogError> {
        self.verify_identity()?;
        if !self.connection.is_autocommit() {
            return Err(invalid(
                "source retirement reader retained a previous transaction",
            ));
        }
        self.connection.execute_batch("BEGIN DEFERRED")?;
        let result = (|| {
            let version: u64 = self
                .connection
                .pragma_query_value(None, "user_version", |row| row.get(0))?;
            if version != 3 {
                return Err(invalid("source retirement storage generation changed"));
            }
            let latest:u64=self.connection.query_row("SELECT MAX(COALESCE((SELECT MAX(sequence) FROM controller_v3_events),0),COALESCE((SELECT CAST(value AS INTEGER) FROM event_log_metadata WHERE key='history_floor'),0))",[],|row|row.get(0))?;
            validate(&self.connection, latest)?;
            let head = read_head(&self.connection)?;
            require_original_prefix(events, intent)?;
            let mut candidates = BTreeMap::new();
            for event in events {
                let payload: EventPayload = serde_json::from_str(&event.payload_json)
                    .map_err(|error| invalid(&error.to_string()))?;
                let EventPayload::StateRecordObserved { record } = payload else {
                    continue;
                };
                let identity = record_identity(&record)?;
                let retained:String=self.connection.query_row("SELECT record_digest FROM retention_reference_records WHERE source_sequence=?1",[event.sequence],|row|row.get(0))?;
                if retained != identity {
                    return Err(invalid(
                        "source retirement original candidate index identity differs",
                    ));
                }
                let was_referenced=self.connection.query_row("SELECT EXISTS(SELECT 1 FROM retention_reference_edges WHERE source_sequence=?1)",[event.sequence],|row|row.get(0))?;
                candidates.insert(
                    event.sequence,
                    SourceCandidate {
                        record_digest: identity,
                        was_referenced,
                    },
                );
            }
            self.verify_identity()?;
            Ok(SourceRetirementProof {
                command_id: intent.request.command_id.clone(),
                segment_digest: intent.manifest.segment_digest.clone(),
                protected: protected_head(&head),
                candidates,
            })
        })();
        self.connection.execute_batch("ROLLBACK")?;
        result
    }
}

/// Outcome for one frozen original archive prefix; this never claims all references were covered.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize)]
pub struct RetentionSourceCompaction {
    /// Original source-index rows retired in the same SQL transaction as archive completion.
    pub retired_records: u64,
    /// Original sources still inside their native TTL, independently of hot retention duration.
    pub retained_fresh_records: u64,
    /// Original sources consumed by a protected decision whose edge remains retained.
    pub retained_referenced_records: u64,
    /// Concrete incomplete-proof reason; no source index is retired when present.
    pub blocked_reason: Option<String>,
    /// Ordered retired original source identities, binding audit evidence without copied values.
    pub retired_digest: String,
}

/// Captures no full business payload copies; digesting uses at most one original event at a time.
fn require_original_prefix(
    events: &[PersistedEvent],
    intent: &super::super::archive_operation::ArchiveIntent,
) -> Result<(), SqliteEventLogError> {
    let manifest = &intent.manifest;
    if events.len() as u64 != manifest.event_count || events.len() as u64 > MAX_ARCHIVE_EVENTS {
        return Err(invalid(
            "source compaction input is not its bounded original archive prefix",
        ));
    }
    let mut digest = Sha256::new();
    let mut input_bytes = 0u64;
    for (offset, event) in events.iter().enumerate() {
        if manifest.start_sequence.checked_add(offset as u64) != Some(event.sequence) {
            return Err(invalid(
                "source compaction original sequence coverage differs",
            ));
        }
        input_bytes = input_bytes
            .checked_add(event.payload_json.len() as u64)
            .and_then(|size| size.checked_add(event.event_id.len() as u64))
            .and_then(|size| size.checked_add(event.correlation_id.len() as u64))
            .and_then(|size| {
                size.checked_add(event.causation_id.as_ref().map_or(0, String::len) as u64)
            })
            .ok_or_else(|| invalid("source compaction original byte count overflow"))?;
        if input_bytes > (MAX_ARCHIVE_BYTES / 4) as u64 {
            return Err(invalid(
                "source compaction input exceeds the bounded original archive budget",
            ));
        }
        validate_persisted_event(event)?;
        let data = serde_json::to_vec(event).map_err(|error| invalid(&error.to_string()))?;
        digest.update(&data);
        digest.update(b"\n");
    }
    if format!("sha256:{:x}", digest.finalize()) != manifest.segment_digest {
        return Err(invalid(
            "source compaction prefix differs from its durable publication",
        ));
    }
    Ok(())
}

/// Retires only expired exact original source metadata after this prefix is durably compacted.
///
/// The caller holds the original application writer and final archive SQL transaction. Its
/// Controller clock must already be durably observed in that transaction. Current fresh State is
/// retained even when superseded, and any earlier unresolved dependency withholds all retirement.
pub(in super::super) fn compact_published_sources(
    connection: &Connection,
    intent: &super::super::archive_operation::ArchiveIntent,
    events: &[PersistedEvent],
    controller_now_ms: u64,
    proof: Option<&SourceRetirementProof>,
) -> Result<RetentionSourceCompaction, SqliteEventLogError> {
    if connection.is_autocommit() {
        return Err(invalid(
            "source compaction requires the original final SQL transaction",
        ));
    }
    let mut result = RetentionSourceCompaction {
        retired_records: 0,
        retained_fresh_records: 0,
        retained_referenced_records: 0,
        blocked_reason: None,
        retired_digest: empty_digest("roboguide.retired-state-source-identities/v0.1"),
    };
    let mut head = read_head(connection)?;
    let Some(proof) = proof else {
        result.blocked_reason =
            Some("original outside-writer source-reference proof is unavailable".into());
        return Ok(result);
    };
    if proof.command_id != intent.request.command_id
        || proof.segment_digest != intent.manifest.segment_digest
    {
        return Err(invalid(
            "source retirement proof names a different original publication",
        ));
    }
    if proof.protected != protected_head(&head) {
        result.blocked_reason = Some(
            "original protected dependencies changed after outside-writer verification".into(),
        );
        return Ok(result);
    }
    if head.archived_prefix_unresolved || head.gaps != 0 {
        result.blocked_reason = Some(
            "original decision dependencies or archived-prefix coverage are unresolved".into(),
        );
        return Ok(result);
    }
    let policy = super::super::retention::policy(connection)?;
    if controller_now_ms != policy.clock_high_water_ms {
        result.blocked_reason = Some(
            "original Controller maintenance clock has not been observed at this finalization"
                .into(),
        );
        return Ok(result);
    }
    let original =
        super::super::archive_operation::existing(connection, &intent.request.command_id)?;
    if original
        .as_ref()
        .is_none_or(|(current, receipt)| current != intent || receipt.is_none())
    {
        return Err(invalid(
            "source compaction lacks its exact completed original Archive receipt",
        ));
    }
    let floor: String = connection.query_row(
        "SELECT value FROM event_log_metadata WHERE key='history_floor'",
        [],
        |row| row.get(0),
    )?;
    let floor = floor
        .parse::<u64>()
        .map_err(|_| invalid("source compaction archive floor is invalid"))?;
    if floor < intent.manifest.end_sequence {
        return Err(invalid(
            "source compaction prefix is not covered by durable archive metadata",
        ));
    }
    require_original_prefix(events, intent)?;
    for event in events {
        let payload: EventPayload = serde_json::from_str(&event.payload_json)
            .map_err(|error| invalid(&error.to_string()))?;
        let EventPayload::StateRecordObserved { record } = payload else {
            continue;
        };
        let candidate = proof.candidates.get(&event.sequence).ok_or_else(|| {
            invalid("original source candidate is absent from outside-writer proof")
        })?;
        let retained: Option<String> = connection
            .query_row(
                "SELECT record_digest FROM retention_reference_records WHERE source_sequence=?1",
                [event.sequence],
                |row| row.get(0),
            )
            .optional()?;
        let retained = retained
            .ok_or_else(|| invalid("verified source candidate disappeared before finalization"))?;
        if retained != record_identity(&record)? || retained != candidate.record_digest {
            return Err(invalid(
                "source compaction index differs from its original published record",
            ));
        }
        if event.timestamp_ms > controller_now_ms
            || record.received_at().as_millis() > controller_now_ms
        {
            return Err(invalid(
                "source compaction Controller clock precedes original evidence",
            ));
        }
        let referenced: bool = connection.query_row(
            "SELECT EXISTS(SELECT 1 FROM retention_reference_edges WHERE source_sequence=?1)",
            [event.sequence],
            |row| row.get(0),
        )?;
        if candidate.was_referenced || referenced {
            result.retained_referenced_records += 1;
            continue;
        }
        if !record.is_stale_at(TimestampMs::new(controller_now_ms)) {
            result.retained_fresh_records += 1;
            continue;
        }
        let changed = connection.execute(
            "DELETE FROM retention_reference_records WHERE source_sequence=?1 AND record_digest=?2 AND NOT EXISTS(SELECT 1 FROM retention_reference_edges WHERE source_sequence=?1)",
            params![event.sequence,retained],
        )?;
        if changed != 1 {
            return Err(invalid(
                "source compaction original reference ownership changed",
            ));
        }
        head.records = head
            .records
            .checked_sub(1)
            .ok_or_else(|| invalid("source compaction original record count underflow"))?;
        result.retired_records += 1;
        let original = serde_json::to_string(&(
            event.sequence,
            retained,
            record.received_at().as_millis(),
            record.valid_for_ms(),
        ))
        .map_err(|error| invalid(&error.to_string()))?;
        result.retired_digest = extend(&result.retired_digest, &original);
    }
    save_head(connection, &head)?;
    Ok(result)
}
