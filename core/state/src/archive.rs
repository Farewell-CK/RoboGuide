//! Explicit append-only event archival and history compaction.

use super::*;
use sha2::Sha256;
use std::fs::{self, OpenOptions};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};

/// A maintenance request never materializes an unbounded history prefix.
pub const MAX_ARCHIVE_EVENTS: u64 = 8_192;
/// Maximum encoded segment retained in memory and published by one maintenance request.
pub const MAX_ARCHIVE_BYTES: usize = 16 * 1024 * 1024;
/// Process-local identity for staging files owned solely by this sink.
static NEXT_STAGING_ID: AtomicU64 = AtomicU64::new(0);

/// Checks the closed manifest shape before constructing paths or removing history.
fn validate_manifest(manifest: &ArchiveManifest) -> Result<(), String> {
    let digest_valid = |digest: &str| {
        digest.strip_prefix("sha256:").is_some_and(|value| {
            value.len() == 64
                && value
                    .bytes()
                    .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        })
    };
    if manifest.schema != "roboguide.event-archive/v1"
        || manifest.start_sequence == 0
        || manifest.end_sequence < manifest.start_sequence
        || manifest.end_sequence - manifest.start_sequence + 1 != manifest.event_count
        || manifest.event_count > MAX_ARCHIVE_EVENTS
        || manifest.checkpoint_sequence < manifest.end_sequence
        || !digest_valid(&manifest.segment_digest)
        || manifest.checkpoint_digest.as_deref().is_some_and(|digest| {
            digest.len() != 64
                || !digest
                    .bytes()
                    .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        })
    {
        return Err("invalid archive manifest identity, range or bounds".into());
    }
    Ok(())
}

/// Preserves evidence whose catalog or localization consumer currently requires full event replay.
fn requires_catalog_history(payload: &EventPayload) -> bool {
    matches!(
        payload,
        EventPayload::MapArtifactDeclared { .. }
            | EventPayload::ArtifactUploadTransition { .. }
            | EventPayload::MapArtifactPublished { .. }
            | EventPayload::MapArtifactStaged { .. }
            | EventPayload::MapArtifactImported { .. }
            | EventPayload::MapLocalizationVerified { .. }
            | EventPayload::MapLocalizationEvidenceRecorded { .. }
            | EventPayload::MapArtifactRejected { .. }
            | EventPayload::MemoryManifestPublished { .. }
            | EventPayload::MemoryArtifactStaged { .. }
            | EventPayload::MemoryArtifactImported { .. }
            | EventPayload::MapMemoryReplicaRecorded { .. }
            | EventPayload::MemoryArtifactRejected { .. }
    )
}

/// Reads malformed history floors as corruption instead of silently restarting at zero.
fn read_history_floor(connection: &Connection) -> Result<u64, SqliteEventLogError> {
    connection
        .query_row(
            "SELECT value FROM event_log_metadata WHERE key='history_floor'",
            [],
            |row| row.get::<_, String>(0),
        )
        .optional()?
        .map(|value| {
            value
                .parse::<u64>()
                .map_err(|_| SqliteEventLogError::Codec("invalid archive history floor".into()))
        })
        .transpose()
        .map(|value| value.unwrap_or(0))
}

/// Retains the restart clock floor after rows disappear; legacy GC without this proof requires trusted migration.
pub(super) fn read_archive_timestamp_floor(
    connection: &Connection,
) -> Result<u64, SqliteEventLogError> {
    let value = connection
        .query_row(
            "SELECT value FROM event_log_metadata WHERE key='archive_timestamp_floor'",
            [],
            |row| row.get::<_, String>(0),
        )
        .optional()?;
    match value {
        Some(value) => value
            .parse()
            .map_err(|_| SqliteEventLogError::Codec("invalid archive timestamp floor".into())),
        None if read_history_floor(connection)? > 0 => Err(SqliteEventLogError::Codec(
            "legacy GC has no durable restart clock floor; trusted migration required".into(),
        )),
        None => Ok(0),
    }
}

/// Versioned manifest binding one archived event segment to a complete checkpoint.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ArchiveManifest {
    /// Manifest schema marker.
    pub schema: String,
    /// First sequence included in this segment.
    pub start_sequence: u64,
    /// Last sequence included in this segment.
    pub end_sequence: u64,
    /// Number of event records encoded in the segment.
    pub event_count: u64,
    /// Digest of the exact newline-delimited segment bytes.
    pub segment_digest: String,
    /// Checkpoint sequence that covers the archived prefix.
    pub checkpoint_sequence: u64,
    /// Digest of the exact persisted checkpoint identity, when one exists.
    pub checkpoint_digest: Option<String>,
}

/// Pluggable durable sink for one immutable archive segment.
pub trait ArchiveSink {
    /// Stores or verifies an exact segment before SQLite rows may be deleted.
    fn append_segment(
        &self,
        manifest: &ArchiveManifest,
        bytes: &[u8],
    ) -> Result<(), Box<dyn std::error::Error + Send + Sync>>;
}

/// Local append-only segment sink. Existing identical segments are idempotent; mismatches fail.
#[derive(Debug, Clone)]
pub struct FileArchiveSink {
    /// Deployment-owned directory containing immutable segment files.
    root: PathBuf,
}

impl FileArchiveSink {
    /// Creates a sink rooted at a deployment-owned archive directory.
    pub fn new(root: impl AsRef<Path>) -> Self {
        Self {
            root: root.as_ref().to_path_buf(),
        }
    }

    /// Returns the deterministic path for one manifest.
    pub fn segment_path(&self, manifest: &ArchiveManifest) -> PathBuf {
        self.root.join(format!(
            "events-{}-{}-{}.jsonl",
            manifest.start_sequence,
            manifest.end_sequence,
            manifest.segment_digest.trim_start_matches("sha256:")
        ))
    }

    /// Publishes complete bytes without replacing an existing immutable file; retries re-sync it.
    fn publish(
        &self,
        path: &Path,
        bytes: &[u8],
    ) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
        let verify = || -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
            if !fs::symlink_metadata(path)?.file_type().is_file() {
                return Err("archive publication must be a regular file".into());
            }
            let mut file = fs::File::open(path)?;
            if file.metadata()?.len() != bytes.len() as u64 {
                return Err("archive publication length mismatch".into());
            }
            let mut existing = Vec::new();
            (&mut file)
                .take(bytes.len() as u64 + 1)
                .read_to_end(&mut existing)?;
            if existing != bytes {
                return Err("existing archive publication content mismatch".into());
            }
            file.sync_all()?;
            Ok(())
        };
        if path.try_exists()? {
            return verify();
        }
        let staging = self.root.join(format!(
            ".archive-{}-{}.pending",
            std::process::id(),
            NEXT_STAGING_ID.fetch_add(1, Ordering::Relaxed)
        ));
        let mut file = OpenOptions::new()
            .create_new(true)
            .write(true)
            .open(&staging)?;
        let result = (|| {
            file.write_all(bytes)?;
            file.sync_all()?;
            match fs::hard_link(&staging, path) {
                Ok(()) => Ok(()),
                Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => verify(),
                Err(error) => Err(error.into()),
            }
        })();
        drop(file);
        let cleanup = fs::remove_file(&staging);
        result?;
        cleanup?;
        Ok(())
    }
}

impl ArchiveSink for FileArchiveSink {
    /// Writes one segment with create-new semantics and verifies any pre-existing copy.
    fn append_segment(
        &self,
        manifest: &ArchiveManifest,
        bytes: &[u8],
    ) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
        validate_manifest(manifest)?;
        if bytes.len() > MAX_ARCHIVE_BYTES {
            return Err("archive segment exceeds byte bound".into());
        }
        let digest = format!("sha256:{:x}", Sha256::digest(bytes));
        if digest != manifest.segment_digest {
            return Err("archive segment digest differs from its manifest".into());
        }
        let missing = self
            .root
            .ancestors()
            .take_while(|path| !path.exists())
            .map(Path::to_path_buf)
            .collect::<Vec<_>>();
        fs::create_dir_all(&self.root)?;
        if !fs::symlink_metadata(&self.root)?.file_type().is_dir() {
            return Err("archive root must be a real directory".into());
        }
        // Persist the root directory's publication when this request created it.
        for directory in missing {
            if let Some(parent) = directory
                .parent()
                .filter(|parent| !parent.as_os_str().is_empty())
            {
                fs::File::open(parent)?.sync_all()?;
            }
        }
        let path = self.segment_path(manifest);
        self.publish(&path, bytes)?;
        let manifest_bytes = serde_json::to_vec(manifest)?;
        self.publish(&path.with_extension("manifest.json"), &manifest_bytes)?;
        fs::File::open(&self.root)?.sync_all()?;
        Ok(())
    }
}

impl SqliteEventLog {
    /// Returns the highest sequence already covered by a committed archive prefix.
    pub fn history_floor(&self) -> Result<u64, SqliteEventLogError> {
        self.ensure_no_open_batch()?;
        let connection = self
            .connection
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        read_history_floor(&connection)
    }

    /// Archives and removes only a checkpoint-covered prefix after the sink verifies its bytes.
    ///
    /// The caller supplies an explicit safe cutoff. No timer, Mission lifecycle or retention
    /// policy invokes this method implicitly. Catalog replay evidence is never removed, even when
    /// a checkpoint covers it, because that checkpoint does not contain the catalog projection.
    pub fn archive_and_gc<S: ArchiveSink>(
        &self,
        end_sequence: u64,
        sink: &S,
    ) -> Result<ArchiveManifest, SqliteEventLogError> {
        self.ensure_no_open_batch()?;
        if end_sequence == 0 {
            return Err(SqliteEventLogError::ArchivePolicy(
                "archive cutoff must be positive".into(),
            ));
        }
        if self.replicator.is_some() {
            return Err(SqliteEventLogError::ArchivePolicy(
                "archive/GC requires a coordinated replica maintenance protocol".into(),
            ));
        }
        let mut connection = self
            .connection
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        let checkpoint = checkpoint_store::read(&connection)?.ok_or_else(|| {
            SqliteEventLogError::ArchivePolicy(
                "archive requires a complete controller checkpoint".into(),
            )
        })?;
        if checkpoint.snapshot.event_sequence < end_sequence {
            return Err(SqliteEventLogError::ArchivePolicy(format!(
                "archive cutoff {end_sequence} exceeds checkpoint sequence {}",
                checkpoint.snapshot.event_sequence
            )));
        }
        let floor = read_history_floor(&connection)?;
        let previous_timestamp_floor = read_archive_timestamp_floor(&connection)?;
        if end_sequence <= floor {
            return Err(SqliteEventLogError::ArchivePolicy(format!(
                "archive cutoff {end_sequence} is not beyond history floor {floor}"
            )));
        }
        if end_sequence - floor > MAX_ARCHIVE_EVENTS {
            return Err(SqliteEventLogError::ArchivePolicy(
                "archive cutoff exceeds per-segment event bound".into(),
            ));
        }
        let source_bytes: u64 = connection.query_row(
            "SELECT COALESCE(SUM(length(CAST(payload_json AS BLOB)) + length(CAST(event_id AS BLOB)) + length(CAST(correlation_id AS BLOB)) + COALESCE(length(CAST(causation_id AS BLOB)),0)),0) FROM events WHERE sequence > ?1 AND sequence <= ?2",
            params![floor,end_sequence], |row| row.get(0))?;
        if source_bytes > (MAX_ARCHIVE_BYTES / 4) as u64 {
            return Err(SqliteEventLogError::ArchivePolicy(
                "archive prefix exceeds input byte bound".into(),
            ));
        }
        let mut statement = connection.prepare(
            "SELECT sequence, event_id, timestamp_ms, correlation_id, causation_id,
                    payload_schema, payload_json FROM events
             WHERE sequence > ?1 AND sequence <= ?2 ORDER BY sequence",
        )?;
        let events = statement
            .query_map(params![floor, end_sequence], event_from_row)?
            .collect::<Result<Vec<_>, _>>()?;
        let contiguous = events
            .windows(2)
            .all(|pair| pair[1].sequence == pair[0].sequence.saturating_add(1));
        if events.is_empty()
            || events[0].sequence != floor.saturating_add(1)
            || events
                .last()
                .is_none_or(|event| event.sequence != end_sequence)
            || !contiguous
        {
            return Err(SqliteEventLogError::Codec(
                "archive prefix is incomplete or already compacted".into(),
            ));
        }
        let mut bytes = Vec::new();
        for event in &events {
            validate_persisted_event(event)?;
            let payload: EventPayload = serde_json::from_str(&event.payload_json)
                .map_err(|error| SqliteEventLogError::Codec(error.to_string()))?;
            if requires_catalog_history(&payload) {
                return Err(SqliteEventLogError::ArchivePolicy(
                    "cutoff contains catalog/localization evidence that still requires full replay"
                        .into(),
                ));
            }
            if event.payload_json.len() > MAX_ARCHIVE_BYTES.saturating_sub(bytes.len()) / 2 {
                return Err(SqliteEventLogError::ArchivePolicy(
                    "archive prefix exceeds segment byte bound".into(),
                ));
            }
            serde_json::to_writer(&mut bytes, event)
                .map_err(|error| SqliteEventLogError::Codec(error.to_string()))?;
            bytes.push(b'\n');
            if bytes.len() > MAX_ARCHIVE_BYTES {
                return Err(SqliteEventLogError::ArchivePolicy(
                    "archive prefix exceeds segment byte bound".into(),
                ));
            }
        }
        drop(statement);
        let manifest = ArchiveManifest {
            schema: "roboguide.event-archive/v1".into(),
            start_sequence: events[0].sequence,
            end_sequence,
            event_count: events.len() as u64,
            segment_digest: format!("sha256:{:x}", Sha256::digest(&bytes)),
            checkpoint_sequence: checkpoint.snapshot.event_sequence,
            checkpoint_digest: Some(checkpoint_identity(&checkpoint.snapshot)),
        };
        sink.append_segment(&manifest, &bytes)
            .map_err(|error| SqliteEventLogError::ArchiveSink(error.to_string()))?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        transaction.execute_batch("CREATE TABLE IF NOT EXISTS event_archive_manifests (start_sequence INTEGER PRIMARY KEY, end_sequence INTEGER UNIQUE NOT NULL, manifest_json TEXT NOT NULL)")?;
        transaction.execute(
            "DELETE FROM events WHERE sequence > ?1 AND sequence <= ?2",
            params![floor, end_sequence],
        )?;
        transaction.execute(
            "INSERT INTO event_log_metadata(key,value) VALUES ('history_floor',?1)
                 ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            params![end_sequence.to_string()],
        )?;
        let timestamp_floor = events
            .iter()
            .map(|event| event.timestamp_ms)
            .max()
            .unwrap_or(0)
            .max(previous_timestamp_floor);
        transaction.execute("INSERT INTO event_log_metadata(key,value) VALUES ('archive_timestamp_floor',?1) ON CONFLICT(key) DO UPDATE SET value=excluded.value",[timestamp_floor.to_string()])?;
        transaction.execute(
            "INSERT INTO event_log_metadata(key,value) VALUES ('archive_manifest',?1)
                 ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            params![
                serde_json::to_string(&manifest).map_err(|error| {
                    rusqlite::Error::ToSqlConversionFailure(Box::new(error))
                })?
            ],
        )?;
        transaction.execute("INSERT INTO event_archive_manifests(start_sequence,end_sequence,manifest_json) VALUES (?1,?2,?3)", params![manifest.start_sequence,manifest.end_sequence,serde_json::to_string(&manifest).map_err(|error| SqliteEventLogError::Codec(error.to_string()))?])?;
        transaction.commit()?;
        Ok(manifest)
    }

    /// Returns the last committed archive manifest, if one was recorded.
    pub fn archive_manifest(&self) -> Result<Option<ArchiveManifest>, SqliteEventLogError> {
        self.ensure_no_open_batch()?;
        let connection = self
            .connection
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        let value = connection
            .query_row(
                "SELECT value FROM event_log_metadata WHERE key='archive_manifest'",
                [],
                |row| row.get::<_, String>(0),
            )
            .optional()?;
        value
            .map(|value| {
                let manifest: ArchiveManifest = serde_json::from_str(&value)
                    .map_err(|error| SqliteEventLogError::Codec(error.to_string()))?;
                validate_manifest(&manifest).map_err(SqliteEventLogError::Codec)?;
                Ok(manifest)
            })
            .transpose()
    }

    /// Returns a bounded page of committed manifests; older segments are never overwritten.
    pub fn archive_manifests(
        &self,
        after_sequence: u64,
        limit: usize,
    ) -> Result<Vec<ArchiveManifest>, SqliteEventLogError> {
        self.ensure_no_open_batch()?;
        let connection = self
            .connection
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        let exists = connection.query_row("SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE type='table' AND name='event_archive_manifests')", [], |row| row.get::<_, bool>(0))?;
        if !exists {
            return Ok(Vec::new());
        }
        let mut statement = connection.prepare("SELECT manifest_json FROM event_archive_manifests WHERE end_sequence > ?1 ORDER BY start_sequence LIMIT ?2")?;
        let rows = statement
            .query_map(params![after_sequence, limit.clamp(1, 128) as u64], |row| {
                row.get::<_, String>(0)
            })?;
        rows.map(|row| {
            let manifest: ArchiveManifest = serde_json::from_str(&row?)
                .map_err(|error| SqliteEventLogError::Codec(error.to_string()))?;
            validate_manifest(&manifest).map_err(SqliteEventLogError::Codec)?;
            Ok(manifest)
        })
        .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    /// Appends a noncatalog event whose application authority is covered by the test checkpoint.
    fn append(log: &mut SqliteEventLog, sequence: u64) {
        let correlation =
            CorrelationId::new(format!("archive-{sequence}")).expect("test correlation is valid");
        log.append(
            TimestampMs::new(sequence),
            &correlation,
            None,
            domain::EventPayload::ExecutionGroupBlocked {
                group_id: domain::ExecutionGroupId::new(format!("group-{sequence}"))
                    .expect("group is valid"),
                task_ref: domain::TaskRef::new(
                    domain::MissionId::new("mission").expect("mission is valid"),
                    domain::TaskId::new(format!("task-{sequence}")).expect("task is valid"),
                ),
                reason: "archive-test".into(),
            },
        );
        assert!(log.take_error().expect("error probe").is_none());
    }

    /// The exact committed prefix and immutable sink can be inspected independently of the tail.
    #[test]
    fn archive_requires_checkpoint_and_is_idempotent_at_sink() {
        let directory = tempdir().expect("tempdir");
        let path = directory.path().join("events.sqlite3");
        let log = SqliteEventLog::open(&path).expect("open");
        let mut log_writer = log.clone();
        append(&mut log_writer, 1);
        assert!(
            log.archive_and_gc(1, &FileArchiveSink::new(directory.path()))
                .is_err()
        );
        log.begin_batch().expect("batch");
        log.save_checkpoint("test/checkpoint/v1", "{}")
            .expect("checkpoint");
        log.commit_batch().expect("commit");
        let sink = FileArchiveSink::new(directory.path().join("archive"));
        let manifest = log.archive_and_gc(1, &sink).expect("archive");
        assert_eq!(manifest.event_count, 1);
        assert_eq!(log.history_floor().expect("floor"), 1);
        assert_eq!(log.len().expect("tail length"), 0);
        assert_eq!(
            log.archive_manifest().expect("manifest").expect("present"),
            manifest
        );
        assert!(log.archive_and_gc(1, &sink).is_err());
    }

    /// Retrying original bytes detects an on-disk modification rather than merely rejecting a wrong input digest.
    #[test]
    fn archive_sink_rejects_tampered_existing_segment() {
        let directory = tempdir().expect("tempdir");
        let sink = FileArchiveSink::new(directory.path());
        let manifest = ArchiveManifest {
            schema: "roboguide.event-archive/v1".into(),
            start_sequence: 1,
            end_sequence: 1,
            event_count: 1,
            segment_digest: format!("sha256:{:x}", Sha256::digest(b"good\n")),
            checkpoint_sequence: 1,
            checkpoint_digest: None,
        };
        sink.append_segment(&manifest, b"good\n")
            .expect("first write");
        sink.append_segment(&manifest, b"good\n")
            .expect("identical retry");
        fs::write(sink.segment_path(&manifest), b"evil\n").expect("tamper actual file");
        assert!(sink.append_segment(&manifest, b"good\n").is_err());
        fs::write(sink.segment_path(&manifest), b"good\n").unwrap();
        fs::write(
            sink.segment_path(&manifest).with_extension("manifest.json"),
            b"{}",
        )
        .unwrap();
        assert!(sink.append_segment(&manifest, b"good\n").is_err());
    }

    /// Covers the complete latest authority without relying on event rows after GC.
    fn checkpoint(log: &SqliteEventLog) {
        log.begin_batch().unwrap();
        log.save_checkpoint("test/checkpoint/v1", "{}").unwrap();
        log.commit_batch().unwrap();
    }

    /// A deferred-FK COMMIT failure rolls back deletion, metadata and the index, leaving the next batch usable.
    #[test]
    fn failed_archive_commit_rolls_back_and_allows_next_write() {
        let directory = tempdir().unwrap();
        let log = SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
        append(&mut log.clone(), 1);
        checkpoint(&log);
        {
            let connection = log.connection.lock().unwrap();
            connection.execute_batch("PRAGMA foreign_keys=ON; CREATE TABLE archive_commit_fault(event_sequence INTEGER REFERENCES events(sequence) DEFERRABLE INITIALLY DEFERRED); INSERT INTO archive_commit_fault VALUES (1);").unwrap();
        }
        assert!(matches!(
            log.archive_and_gc(1, &FileArchiveSink::new(directory.path().join("archive"))),
            Err(SqliteEventLogError::Sqlite(_))
        ));
        assert!(log.connection.lock().unwrap().is_autocommit());
        assert_eq!(log.len().unwrap(), 1);
        assert_eq!(log.history_floor().unwrap(), 0);
        assert_eq!(log.latest_timestamp().unwrap(), TimestampMs::new(1));
        assert_eq!(log.archive_manifest().unwrap(), None);
        assert!(log.archive_manifests(0, 128).unwrap().is_empty());
        log.begin_batch().unwrap();
        append(&mut log.clone(), 2);
        log.save_checkpoint("test/checkpoint/v1", "{\"new\":true}")
            .unwrap();
        log.commit_batch().unwrap();
        assert_eq!(log.len().unwrap(), 2);
    }

    /// Archive publication rejection never enters the deletion transaction.
    struct FailingSink;
    impl ArchiveSink for FailingSink {
        /// Injects a durable sink failure before any SQL compaction is permitted.
        fn append_segment(
            &self,
            _manifest: &ArchiveManifest,
            _bytes: &[u8],
        ) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
            Err("injected sink failure".into())
        }
    }

    /// Sink failures and impossible input bounds preserve both event history and checkpoint identity.
    #[test]
    fn sink_failure_and_oversized_cutoff_preserve_history() {
        let directory = tempdir().unwrap();
        let log = SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
        append(&mut log.clone(), 1);
        checkpoint(&log);
        let before = log.load_checkpoint().unwrap();
        assert!(matches!(
            log.archive_and_gc(1, &FailingSink),
            Err(SqliteEventLogError::ArchiveSink(_))
        ));
        assert_eq!(log.len().unwrap(), 1);
        assert_eq!(log.history_floor().unwrap(), 0);
        assert_eq!(log.load_checkpoint().unwrap(), before);
        // A checkpoint can advance without retaining every historical event, but never bypasses segment bounds.
        *log.next_event_number.lock().unwrap() = MAX_ARCHIVE_EVENTS + 1;
        checkpoint(&log);
        assert!(matches!(
            log.archive_and_gc(MAX_ARCHIVE_EVENTS + 1, &FailingSink),
            Err(SqliteEventLogError::ArchivePolicy(_))
        ));
        assert_eq!(log.len().unwrap(), 1);
    }

    /// Every new committed segment remains paged after restart, and future append identities remain monotonic.
    #[test]
    fn archive_pages_survive_restart_and_sequence_continues() {
        let directory = tempdir().unwrap();
        let path = directory.path().join("events.sqlite3");
        let log = SqliteEventLog::open(&path).unwrap();
        let sink = FileArchiveSink::new(directory.path().join("archive"));
        append(&mut log.clone(), 1);
        checkpoint(&log);
        let first = log.archive_and_gc(1, &sink).unwrap();
        append(&mut log.clone(), 2);
        checkpoint(&log);
        let second = log.archive_and_gc(2, &sink).unwrap();
        drop(log);
        let log = SqliteEventLog::open(&path).unwrap();
        assert_eq!(log.archive_manifests(0, 1).unwrap(), vec![first]);
        assert_eq!(log.archive_manifests(1, 1).unwrap(), vec![second]);
        assert!(log.archive_manifests(2, 128).unwrap().is_empty());
        assert_eq!(log.latest_timestamp().unwrap(), TimestampMs::new(2));
        append(&mut log.clone(), 3);
        checkpoint(&log);
        assert_eq!(log.events().unwrap()[0].sequence, 3);
        assert_eq!(log.latest_sequence().unwrap(), 3);
    }

    /// A pre-fix GC database cannot silently lose its historical receive-time lower bound.
    #[test]
    fn legacy_archive_without_timestamp_floor_requires_trusted_migration() {
        let directory = tempdir().unwrap();
        let log = SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
        log.connection
            .lock()
            .unwrap()
            .execute(
                "INSERT INTO event_log_metadata(key,value) VALUES ('history_floor','1')",
                [],
            )
            .unwrap();
        assert!(matches!(
            log.latest_timestamp(),
            Err(SqliteEventLogError::Codec(_))
        ));
    }

    /// Corrupted metadata and gaps cannot be silently interpreted as a valid complete prefix.
    #[test]
    fn malformed_history_floor_and_sequence_gap_reject() {
        let directory = tempdir().unwrap();
        let log = SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
        for sequence in 1..=3 {
            append(&mut log.clone(), sequence);
        }
        checkpoint(&log);
        log.connection
            .lock()
            .unwrap()
            .execute(
                "INSERT INTO event_log_metadata(key,value) VALUES ('history_floor','invalid')",
                [],
            )
            .unwrap();
        assert!(log.history_floor().is_err());
        assert!(matches!(
            log.archive_and_gc(3, &FailingSink),
            Err(SqliteEventLogError::Codec(_))
        ));
        log.connection.lock().unwrap().execute_batch("DELETE FROM event_log_metadata WHERE key='history_floor'; DELETE FROM events WHERE sequence=2;").unwrap();
        assert!(matches!(
            log.archive_and_gc(3, &FailingSink),
            Err(SqliteEventLogError::Codec(_))
        ));
        assert_eq!(log.len().unwrap(), 2);
    }
}
