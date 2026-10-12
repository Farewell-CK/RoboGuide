//! Explicit append-only event archival and history compaction.

use super::*;
use sha2::{Digest as _, Sha256};
use std::fs::{self, OpenOptions};
use std::io::{Read, Write};
use std::path::{Path, PathBuf};

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
}

impl ArchiveSink for FileArchiveSink {
    /// Writes one segment with create-new semantics and verifies any pre-existing copy.
    fn append_segment(
        &self,
        manifest: &ArchiveManifest,
        bytes: &[u8],
    ) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
        let digest = format!("sha256:{:x}", Sha256::digest(bytes));
        if digest != manifest.segment_digest {
            return Err("archive segment digest differs from its manifest".into());
        }
        fs::create_dir_all(&self.root)?;
        let path = self.segment_path(manifest);
        match OpenOptions::new().create_new(true).write(true).open(&path) {
            Ok(mut file) => {
                file.write_all(bytes)?;
                file.sync_all()?;
            }
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => {
                let mut existing = Vec::new();
                fs::File::open(&path)?.read_to_end(&mut existing)?;
                if existing != bytes {
                    return Err(format!(
                        "archive segment {} differs from its manifest",
                        path.display()
                    )
                    .into());
                }
            }
            Err(error) => return Err(error.into()),
        }
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
        Ok(connection
            .query_row(
                "SELECT value FROM event_log_metadata WHERE key='history_floor'",
                [],
                |row| row.get::<_, String>(0),
            )
            .optional()?
            .and_then(|value| value.parse::<u64>().ok())
            .unwrap_or(0))
    }

    /// Archives and removes only a checkpoint-covered prefix after the sink verifies its bytes.
    ///
    /// The caller supplies an explicit safe cutoff. No timer, Mission lifecycle or retention
    /// policy invokes this method implicitly; active/unknown/held work therefore remains present
    /// until an operator or deployment policy proves that the cutoff is safe.
    pub fn archive_and_gc<S: ArchiveSink>(
        &self,
        end_sequence: u64,
        sink: &S,
    ) -> Result<ArchiveManifest, SqliteEventLogError> {
        self.ensure_no_open_batch()?;
        if end_sequence == 0 {
            return Err(SqliteEventLogError::Codec(
                "archive cutoff must be positive".into(),
            ));
        }
        if self.replicator.is_some() {
            return Err(SqliteEventLogError::Replication(
                "archive/GC requires a coordinated replica maintenance protocol".into(),
            ));
        }
        let connection = self
            .connection
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        let checkpoint = checkpoint_store::read(&connection)?.ok_or_else(|| {
            SqliteEventLogError::Codec("archive requires a complete controller checkpoint".into())
        })?;
        if checkpoint.snapshot.event_sequence < end_sequence {
            return Err(SqliteEventLogError::Codec(format!(
                "archive cutoff {end_sequence} exceeds checkpoint sequence {}",
                checkpoint.snapshot.event_sequence
            )));
        }
        let floor = connection
            .query_row(
                "SELECT value FROM event_log_metadata WHERE key='history_floor'",
                [],
                |row| row.get::<_, String>(0),
            )
            .optional()?
            .and_then(|value| value.parse::<u64>().ok())
            .unwrap_or(0);
        if end_sequence <= floor {
            return Err(SqliteEventLogError::Codec(format!(
                "archive cutoff {end_sequence} is not beyond history floor {floor}"
            )));
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
            serde_json::to_writer(&mut bytes, event)
                .map_err(|error| SqliteEventLogError::Codec(error.to_string()))?;
            bytes.push(b'\n');
        }
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
            .map_err(|error| SqliteEventLogError::Codec(format!("archive sink failed: {error}")))?;
        let digest = manifest.segment_digest.clone();
        connection.execute_batch("BEGIN IMMEDIATE")?;
        let result = (|| {
            connection.execute(
                "DELETE FROM events WHERE sequence > ?1 AND sequence <= ?2",
                params![floor, end_sequence],
            )?;
            connection.execute(
                "INSERT INTO event_log_metadata(key,value) VALUES ('history_floor',?1)
                 ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                params![end_sequence.to_string()],
            )?;
            connection.execute(
                "INSERT INTO event_log_metadata(key,value) VALUES ('archive_manifest',?1)
                 ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                params![serde_json::to_string(&manifest).map_err(|error| {
                    rusqlite::Error::ToSqlConversionFailure(Box::new(error))
                })?],
            )?;
            Ok::<(), rusqlite::Error>(())
        })();
        match result {
            Ok(()) => connection.execute_batch("COMMIT")?,
            Err(error) => {
                let _ = connection.execute_batch("ROLLBACK");
                return Err(error.into());
            }
        }
        let _ = digest;
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
                serde_json::from_str(&value)
                    .map_err(|error| SqliteEventLogError::Codec(error.to_string()))
            })
            .transpose()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

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
        assert!(sink.append_segment(&manifest, b"tampered\n").is_err());
    }
}
