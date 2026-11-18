//! Explicit lossless hot relocation under committed policy; no permanent-expiry authority.
//!
//! The worker may move a bounded aged original prefix, including protected Task/Unknown evidence,
//! into the existing verified content archive. It does not claim complete expiry-reference proof.
//! Its independent WAL reader observes committed policy through concurrent Node transactions and
//! never reacquires the application writer from a publication callback. Finalization requires the
//! same original policy revision and actual Controller clock under the reacquired writer.

use super::*;
use std::fs::{File, OpenOptions};
use std::os::unix::fs::{MetadataExt, OpenOptionsExt};
use std::path::PathBuf;

/// Explicit classification: hot relocation preserves original evidence and cannot expire it.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize)]
#[serde(rename_all = "kebab-case")]
pub enum ArchiveExpiryCoverage {
    /// Permanent deletion reference coverage has not been established by this operation.
    Unknown,
}

/// Bounded candidate assessment for lossless relocation, separate from permanent-retention status.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize)]
pub struct ArchiveRelocationStatus {
    /// Closed schema describing relocation rather than physical or expiry safety.
    pub schema: String,
    /// Original committed policy revision checked by freeze and finalization.
    pub policy_revision: u64,
    /// Current explicit operator choice; evaluation never creates an Archive intent.
    pub mode: RetentionMode,
    /// Original committed hot-store archive boundary.
    pub history_floor: u64,
    /// Current complete authority checkpoint through which archival is recoverable.
    pub checkpoint_sequence: u64,
    /// First original sequence in the current bounded aged candidate, when any.
    pub start_sequence: Option<u64>,
    /// Last original sequence in that bounded candidate, unchanged by archive-row numbering.
    pub end_sequence: Option<u64>,
    /// Original rows examined and included within both policy resource limits.
    pub event_count: u64,
    /// Original envelope field bytes included within the input budget.
    pub input_bytes: u64,
    /// Independent permanent-expiry reference coverage; never promoted by a complete checkpoint.
    pub expiry_reference_coverage: ArchiveExpiryCoverage,
    /// Concrete reason no candidate is currently prepared; not an all-Missions-terminal test.
    pub blockers: Vec<String>,
}

/// Independent original-inode policy reader, safe to retain in an Arc during outside-writer copying.
pub struct CommittedRetentionPolicyWitness {
    /// Original canonical Controller database path, never a request-supplied alternative database.
    path: PathBuf,
    /// Original no-follow database descriptor retained for inode identity checks.
    file: File,
    /// Original device/inode fixed before opening the independent readonly WAL connection.
    identity: (u64, u64),
    /// Policy-only connection; no statement remains open between permission observations.
    connection: Mutex<Connection>,
    /// Original accepted policy revision; later changes cancel this operation's write authority.
    expected_revision: u64,
}

impl CommittedRetentionPolicyWitness {
    /// Returns the original policy revision without reinterpreting a later policy as the same grant.
    pub const fn expected_revision(&self) -> u64 {
        self.expected_revision
    }

    /// Checks committed policy only; concurrent uncommitted Node batches neither cancel nor leak.
    pub fn require_current(&self) -> Result<(), SqliteEventLogError> {
        self.verify_inode()?;
        let connection = self
            .connection
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        if !connection.is_autocommit() {
            return Err(invalid("policy witness retained a read transaction"));
        }
        connection.execute_batch("BEGIN DEFERRED")?;
        let result = (|| {
            let version: u64 =
                connection.pragma_query_value(None, "user_version", |row| row.get(0))?;
            let marker: Vec<(u64, String)> = connection
                .prepare("SELECT version,schema FROM controller_storage_format")?
                .query_map([], |row| Ok((row.get(0)?, row.get(1)?)))?
                .collect::<Result<_, _>>()?;
            if version != 3 || marker != vec![(3, "roboguide.controller-storage/v3".into())] {
                return Err(invalid("committed storage generation differs"));
            }
            retention::validate(&connection)?;
            let policy = retention::policy(&connection)?;
            if policy.revision != self.expected_revision
                || policy.policy.mode != RetentionMode::Enabled
            {
                return Err(invalid(
                    "original committed retention policy was replaced or disabled",
                ));
            }
            self.verify_inode()
        })();
        let rollback = connection.execute_batch("ROLLBACK");
        if let Err(error) = rollback {
            return Err(error.into());
        }
        result
    }

    /// Refuses path/descriptor substitution rather than adopting another readable database.
    fn verify_inode(&self) -> Result<(), SqliteEventLogError> {
        let actual =
            std::fs::symlink_metadata(&self.path).map_err(|error| invalid(&error.to_string()))?;
        let held = self
            .file
            .metadata()
            .map_err(|error| invalid(&error.to_string()))?;
        if !actual.file_type().is_file()
            || !held.file_type().is_file()
            || (actual.dev(), actual.ino()) != self.identity
            || (held.dev(), held.ino()) != self.identity
        {
            return Err(invalid("original Controller database inode/path changed"));
        }
        Ok(())
    }
}

/// Binds a prepared original prefix to the exact committed policy grant checked during publication.
pub struct PreparedRetentionContentArchive {
    /// Original bounded immutable content operation.
    prepared: PreparedContentArchive,
    /// Independent policy-only reader for current permission callbacks.
    witness: Arc<CommittedRetentionPolicyWitness>,
    /// Original relocation-only status; it grants no permanent-expiry authority.
    status: ArchiveRelocationStatus,
}

impl PreparedRetentionContentArchive {
    /// Clones only the policy observer; callers cannot substitute another policy revision.
    pub fn policy_witness(&self) -> Arc<CommittedRetentionPolicyWitness> {
        self.witness.clone()
    }
    /// Returns bounded original relocation metadata without cloning source payloads.
    pub fn status(&self) -> &ArchiveRelocationStatus {
        &self.status
    }
    /// Exposes the immutable content operation identity for a source-owned archive sink.
    pub fn intent(&self) -> &ArchiveIntent {
        self.prepared.intent()
    }
}

/// Published immutable original prefix still requiring policy/current authority revalidation.
pub struct PublishedRetentionContentArchive {
    /// Actual published token; callers cannot fabricate its source or durable proof.
    published: PublishedContentArchive,
    /// Exact original committed policy grant checked again under final writer ownership.
    witness: Arc<CommittedRetentionPolicyWitness>,
}

/// Reports policy/relocation refusal without pretending missing deletion proof authorizes expiry.
fn invalid(message: &str) -> SqliteEventLogError {
    SqliteEventLogError::ArchivePolicy(format!("retention hot relocation: {message}"))
}

/// Opens one independent no-follow original-inode readonly WAL reader while the writer is stable.
fn witness(
    log: &SqliteEventLog,
    expected_revision: u64,
) -> Result<CommittedRetentionPolicyWitness, SqliteEventLogError> {
    let path = {
        let connection = log
            .connection
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        connection
            .path()
            .filter(|path| !path.is_empty())
            .map(PathBuf::from)
            .ok_or_else(|| {
                invalid("committed policy witness requires a real Controller database file")
            })?
    };
    let actual = std::fs::symlink_metadata(&path).map_err(|error| invalid(&error.to_string()))?;
    if !actual.file_type().is_file() {
        return Err(invalid(
            "committed policy witness requires its original regular database",
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
    if (held.dev(), held.ino()) != (actual.dev(), actual.ino()) {
        return Err(invalid(
            "Controller path changed before policy reader opened",
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
    let witness = CommittedRetentionPolicyWitness {
        path,
        file,
        identity: (held.dev(), held.ino()),
        connection: Mutex::new(connection),
        expected_revision,
    };
    witness.require_current()?;
    Ok(witness)
}

/// Assesses only aged, checkpoint-covered original event metadata within the active policy bounds.
fn relocation_status(
    log: &SqliteEventLog,
    now: TimestampMs,
) -> Result<ArchiveRelocationStatus, SqliteEventLogError> {
    log.ensure_no_open_batch()?;
    if log.events_table != "controller_v3_events" || log.replicator.is_some() {
        return Err(invalid(
            "automatic hot relocation requires negotiated standalone storage v3",
        ));
    }
    let connection = log
        .connection
        .lock()
        .map_err(|_| SqliteEventLogError::LockPoisoned)?;
    retention::validate(&connection)?;
    let policy = retention::policy(&connection)?;
    let checkpoint = checkpoint_store::read(&connection)?;
    if let Some(checkpoint) = &checkpoint {
        authority_journal::validate(&connection, Some(&checkpoint.snapshot))?;
        if checkpoint.snapshot.schema != crate::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA {
            return Err(invalid("current complete checkpoint schema is required"));
        }
    }
    let floor: Option<String> = connection
        .query_row(
            "SELECT value FROM event_log_metadata WHERE key='history_floor'",
            [],
            |row| row.get(0),
        )
        .optional()?;
    let floor = floor
        .map(|floor| {
            floor
                .parse::<u64>()
                .map_err(|_| invalid("original archive floor is invalid"))
        })
        .transpose()?
        .unwrap_or(0);
    let mut status = ArchiveRelocationStatus {
        schema: "roboguide.archive-relocation-status/v0.1".into(),
        policy_revision: policy.revision,
        mode: policy.policy.mode,
        history_floor: floor,
        checkpoint_sequence: checkpoint
            .as_ref()
            .map_or(0, |checkpoint| checkpoint.snapshot.event_sequence),
        start_sequence: None,
        end_sequence: None,
        event_count: 0,
        input_bytes: 0,
        expiry_reference_coverage: ArchiveExpiryCoverage::Unknown,
        blockers: Vec::new(),
    };
    if checkpoint.is_none() {
        status
            .blockers
            .push("complete current checkpoint is unavailable".into());
        return Ok(status);
    }
    if now.as_millis() < policy.clock_high_water_ms || now.as_millis() > i64::MAX as u64 {
        status
            .blockers
            .push("Controller maintenance clock regressed".into());
        return Ok(status);
    }
    if status.mode == RetentionMode::Disabled {
        status.blockers.push("retention is disabled".into());
        return Ok(status);
    }
    let cutoff = now.as_millis().saturating_sub(policy.policy.hot_window_ms);
    let mut statement=connection.prepare("SELECT sequence,timestamp_ms,length(CAST(event_id AS BLOB))+length(CAST(correlation_id AS BLOB))+COALESCE(length(CAST(causation_id AS BLOB)),0)+length(CAST(payload_schema AS BLOB))+length(CAST(payload_json AS BLOB))+16 FROM events WHERE sequence>?1 ORDER BY sequence LIMIT ?2")?;
    let mut rows = statement.query(params![floor, policy.policy.max_events])?;
    let mut original_class = None;
    let mut expected = floor
        .checked_add(1)
        .ok_or_else(|| invalid("original archive floor exhausted"))?;
    while let Some(row) = rows.next()? {
        let sequence: u64 = row.get(0)?;
        let timestamp: u64 = row.get(1)?;
        let bytes: u64 = row.get(2)?;
        if sequence != expected {
            return Err(invalid("original hot history prefix has a missing event"));
        }
        if sequence > status.checkpoint_sequence {
            status
                .blockers
                .push("current checkpoint does not cover next original event".into());
            break;
        }
        if timestamp >= cutoff {
            if status.event_count == 0 {
                status
                    .blockers
                    .push("next original event remains inside hot retention".into());
            }
            break;
        }
        if status.input_bytes.saturating_add(bytes) > policy.policy.max_input_bytes {
            if status.event_count == 0 {
                status
                    .blockers
                    .push("next original event exceeds bounded input bytes".into());
            }
            break;
        }
        let payload: String = connection.query_row(
            "SELECT payload_json FROM events WHERE sequence=?1",
            [sequence],
            |row| row.get(0),
        )?;
        let payload: EventPayload =
            serde_json::from_str(&payload).map_err(|error| invalid(&error.to_string()))?;
        let class = retention::ordinary(&payload);
        if original_class.is_some_and(|original| original != class) {
            break;
        }
        original_class = Some(class);
        status.start_sequence.get_or_insert(sequence);
        status.end_sequence = Some(sequence);
        status.event_count += 1;
        status.input_bytes += bytes;
        expected = expected
            .checked_add(1)
            .ok_or_else(|| invalid("original event sequence exhausted"))?;
    }
    if status.mode == RetentionMode::Evaluate {
        status
            .blockers
            .push("evaluation mode cannot admit content publication".into());
    }
    Ok(status)
}

impl SqliteEventLog {
    /// Assesses lossless relocation only; no fake Task/Unknown deletion-reference coverage is supplied.
    pub fn retention_archive_status(
        &self,
        now: TimestampMs,
    ) -> Result<ArchiveRelocationStatus, SqliteEventLogError> {
        relocation_status(self, now)
    }

    /// Freezes one aged original prefix under the caller's FIFO writer and exact Enabled policy.
    pub fn prepare_retention_content_archive(
        &self,
        now: TimestampMs,
        expected_policy_revision: u64,
        request: ArchiveRequest,
    ) -> Result<Option<PreparedRetentionContentArchive>, SqliteEventLogError> {
        self.require_writes_enabled()?;
        let mut status = relocation_status(self, now)?;
        if status.policy_revision != expected_policy_revision {
            return Err(invalid("original policy revision changed before freeze"));
        }
        if status.mode != RetentionMode::Enabled {
            return Ok(None);
        }
        let Some(mut end) = status.end_sequence else {
            return Ok(None);
        };
        if let Some(original) = self.pending_archive_operation()? {
            if original.request != request
                || original.manifest.end_sequence > end
                || original.manifest.start_sequence != status.start_sequence.unwrap_or(0)
            {
                return Err(invalid(
                    "original pending operation must be reconciled unchanged before selecting another cutoff",
                ));
            }
            end = original.manifest.end_sequence;
            status.end_sequence = Some(end);
            status.event_count = original.manifest.event_count;
            let connection = self
                .connection
                .lock()
                .map_err(|_| SqliteEventLogError::LockPoisoned)?;
            status.input_bytes=connection.query_row("SELECT COALESCE(SUM(length(CAST(event_id AS BLOB))+length(CAST(correlation_id AS BLOB))+COALESCE(length(CAST(causation_id AS BLOB)),0)+length(CAST(payload_schema AS BLOB))+length(CAST(payload_json AS BLOB))+16),0) FROM events WHERE sequence>=?1 AND sequence<=?2",params![original.manifest.start_sequence,end],|row|row.get(0))?;
        }
        let witness = Arc::new(witness(self, expected_policy_revision)?);
        let prepared = self.prepare_content_archive(end, request)?;
        Ok(Some(PreparedRetentionContentArchive {
            prepared,
            witness,
            status,
        }))
    }

    /// Publishes outside the writer; callers attach the returned policy observer to every sink effect.
    pub fn publish_retention_content_archive<S: ArchiveSink>(
        prepared: PreparedRetentionContentArchive,
        sink: &S,
    ) -> Result<PublishedRetentionContentArchive, SqliteEventLogError> {
        prepared.witness.require_current()?;
        let published = Self::publish_prepared_content_archive(prepared.prepared, sink)?;
        prepared.witness.require_current()?;
        Ok(PublishedRetentionContentArchive {
            published,
            witness: prepared.witness,
        })
    }

    /// Rechecks exact committed/current policy under final FIFO ownership, then preserves newer state.
    pub fn finish_retention_content_archive<S: ArchiveSink>(
        &self,
        published: PublishedRetentionContentArchive,
        sink: &S,
        now: TimestampMs,
    ) -> Result<ArchiveManifest, SqliteEventLogError> {
        self.ensure_no_open_batch()?;
        published.witness.require_current()?;
        let current = self.retention_policy()?;
        if current.revision != published.witness.expected_revision
            || current.policy.mode != RetentionMode::Enabled
        {
            return Err(invalid(
                "original policy changed before final archive commit",
            ));
        }
        self.finish_content_archive_at(published.published, sink, now)
    }
}
