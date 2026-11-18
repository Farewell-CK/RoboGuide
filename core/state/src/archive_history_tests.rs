//! Original cursor, immutable publication and split-phase historical read counterexamples.

use super::*;
use std::sync::atomic::{AtomicUsize, Ordering};

/// Appends one valid original event without inferring any physical execution outcome.
fn append(log: &SqliteEventLog, number: u64) {
    log.clone().append(
        TimestampMs::new(number),
        &CorrelationId::new("history-test").unwrap(),
        None,
        EventPayload::ExecutionGroupBlocked {
            group_id: domain::ExecutionGroupId::new(format!("group-{number}")).unwrap(),
            task_ref: domain::TaskRef::new(
                domain::MissionId::new("mission").unwrap(),
                domain::TaskId::new(format!("task-{number}")).unwrap(),
            ),
            reason: "original test evidence".into(),
        },
    );
    assert!(log.take_error().unwrap().is_none());
}

/// Saves current authority before explicit archive; fixtures grant no device or field permission.
fn checkpoint(log: &SqliteEventLog) {
    log.begin_batch().unwrap();
    log.save_checkpoint("test/checkpoint/v1", "{}").unwrap();
    log.commit_batch().unwrap();
}

/// Creates two actual published segments and an independently writable hot tail.
fn fixture() -> (
    tempfile::TempDir,
    SqliteEventLog,
    FileHistoryReader,
    Vec<ArchiveManifest>,
) {
    let root = tempfile::tempdir().unwrap();
    let log = SqliteEventLog::open(root.path().join("history.sqlite3")).unwrap();
    let sink = FileArchiveSink::new(root.path().join("archive"));
    for n in 1..=6 {
        append(&log, n);
    }
    checkpoint(&log);
    let first = log.archive_and_gc(2, &sink).unwrap();
    let second = log.archive_and_gc(4, &sink).unwrap();
    let reader = FileHistoryReader::new(root.path().join("archive"));
    (root, log, reader, vec![first, second])
}

/// Original append cursors cross multiple archive segments and then the hot tail without omission.
#[test]
fn pages_cross_archive_and_hot_with_original_cursors() {
    let (_root, log, reader, _) = fixture();
    let mut after = None;
    let mut seen = Vec::new();
    loop {
        let prepared = log.prepare_event_history_page(after, 3).unwrap();
        let page = SqliteEventLog::read_prepared_event_history_page(prepared, &reader).unwrap();
        assert!(page.events.len() <= 3);
        assert_eq!(page.history_floor, 4);
        seen.extend(page.events.iter().map(|row| row.sequence));
        after = Some(page.next_after_sequence);
        if !page.has_more {
            break;
        }
    }
    assert_eq!(seen, vec![1, 2, 3, 4, 5, 6]);
    let page = SqliteEventLog::read_prepared_event_history_page(
        log.prepare_event_history_page(Some(1), 1).unwrap(),
        &reader,
    )
    .unwrap();
    assert_eq!(
        page.events
            .iter()
            .map(|row| row.sequence)
            .collect::<Vec<_>>(),
        vec![2]
    );
    assert_eq!(page.next_after_sequence, 2);
    assert!(page.has_more);
}

/// Owned hot pages remain stable across new appends and compaction after preparation released SQL.
#[test]
fn prepared_hot_page_survives_original_writer_changes() {
    let (root, log, reader, _) = fixture();
    let prepared = log.prepare_event_history_page(Some(4), 1000).unwrap();
    append(&log, 7);
    checkpoint(&log);
    log.archive_and_gc(6, &FileArchiveSink::new(root.path().join("archive")))
        .unwrap();
    let page = SqliteEventLog::read_prepared_event_history_page(prepared, &reader).unwrap();
    assert_eq!(
        page.events
            .iter()
            .map(|row| row.sequence)
            .collect::<Vec<_>>(),
        vec![5, 6]
    );
    assert_eq!(page.through_sequence, 6);
    assert!(!page.has_more);
}

/// Missing or altered immutable files never become an empty page or an unproven expiry response.
#[test]
fn missing_corrupt_or_unpublished_files_fail_closed() {
    let (root, log, reader, manifests) = fixture();
    let path = FileArchiveSink::new(root.path().join("archive")).segment_path(&manifests[0]);
    let original = std::fs::read(&path).unwrap();
    std::fs::write(&path, b"changed\n").unwrap();
    assert!(
        SqliteEventLog::read_prepared_event_history_page(
            log.prepare_event_history_page(None, 100).unwrap(),
            &reader
        )
        .is_err()
    );
    std::fs::write(&path, &original).unwrap();
    std::fs::remove_file(path.with_extension("manifest.json")).unwrap();
    assert!(
        SqliteEventLog::read_prepared_event_history_page(
            log.prepare_event_history_page(None, 100).unwrap(),
            &reader
        )
        .is_err()
    );
    std::fs::remove_file(&path).unwrap();
    assert!(
        SqliteEventLog::read_prepared_event_history_page(
            log.prepare_event_history_page(None, 100).unwrap(),
            &reader
        )
        .is_err()
    );
}

/// A symlink leaf or ancestor never redirects the original archive directory to another store.
#[test]
fn symlink_leaf_and_ancestor_are_rejected() {
    use std::os::unix::fs::symlink;
    let (root, log, reader, manifests) = fixture();
    let path = FileArchiveSink::new(root.path().join("archive")).segment_path(&manifests[0]);
    let moved = root.path().join("actual.jsonl");
    std::fs::rename(&path, &moved).unwrap();
    symlink(&moved, &path).unwrap();
    assert!(
        SqliteEventLog::read_prepared_event_history_page(
            log.prepare_event_history_page(None, 2).unwrap(),
            &reader
        )
        .is_err()
    );
    std::fs::remove_file(&path).unwrap();
    std::fs::rename(&moved, &path).unwrap();
    symlink(root.path().join("archive"), root.path().join("alias")).unwrap();
    let alias = FileHistoryReader::new(root.path().join("alias"));
    assert!(
        SqliteEventLog::read_prepared_event_history_page(
            log.prepare_event_history_page(None, 2).unwrap(),
            &alias
        )
        .is_err()
    );
}

/// Missing SQL Archive coverage cannot be hidden by a surviving hot tail.
#[test]
fn missing_original_coverage_is_refused() {
    let (_root, log, _reader, _) = fixture();
    log.connection
        .lock()
        .unwrap()
        .execute(
            "DELETE FROM event_archive_manifests WHERE start_sequence=1",
            [],
        )
        .unwrap();
    assert!(log.prepare_event_history_page(None, 2).is_err());
}

/// Existing hot-query cursor semantics preserve monotonically ordered rows despite sequence gaps.
#[test]
fn hot_cursor_preserves_legacy_sequence_gap_semantics() {
    let (_root, log, reader, _) = fixture();
    log.connection
        .lock()
        .unwrap()
        .execute("DELETE FROM events WHERE sequence=5", [])
        .unwrap();
    let page = SqliteEventLog::read_prepared_event_history_page(
        log.prepare_event_history_page(Some(4), 2).unwrap(),
        &reader,
    )
    .unwrap();
    assert_eq!(
        page.events
            .iter()
            .map(|row| row.sequence)
            .collect::<Vec<_>>(),
        vec![6]
    );
    assert_eq!(page.next_after_sequence, 6);
    assert!(!page.has_more);
}

/// A fake external reader cannot change bytes while retaining the original committed segment digest.
struct CorruptReader;

impl EventHistoryArchiveReader for CorruptReader {
    /// Returns intentionally inconsistent bytes as a publication-adapter counterexample.
    fn read_published_segment(
        &self,
        _manifest: &ArchiveManifest,
        _intent: Option<&ArchiveIntent>,
        _receipt: Option<&str>,
    ) -> Result<Vec<u8>, String> {
        Ok(b"different\n".to_vec())
    }
}

/// Core verifies all returned immutable bytes even when an application supplies the external reader.
#[test]
fn reader_cannot_replace_original_segment() {
    let (_root, log, _reader, _) = fixture();
    assert!(
        SqliteEventLog::read_prepared_event_history_page(
            log.prepare_event_history_page(None, 1).unwrap(),
            &CorruptReader
        )
        .is_err()
    );
}

/// A read adapter records calls while proving SQL is unlocked and its original token cannot mutate.
struct WriterDuringRead {
    /// Shared original SQL clone used solely for the split-phase lock counterexample.
    log: SqliteEventLog,
    /// Original verified immutable legacy reader.
    reader: FileHistoryReader,
    /// Number of external reads observed in this prepared page.
    calls: AtomicUsize,
}

impl EventHistoryArchiveReader for WriterDuringRead {
    /// Actual append succeeds during the external read, proving no source SQL guard is retained.
    fn read_published_segment(
        &self,
        manifest: &ArchiveManifest,
        intent: Option<&ArchiveIntent>,
        receipt: Option<&str>,
    ) -> Result<Vec<u8>, String> {
        self.calls.fetch_add(1, Ordering::SeqCst);
        assert!(self.log.connection.try_lock().is_ok());
        append(&self.log, 7);
        self.reader
            .read_published_segment(manifest, intent, receipt)
    }
}

/// One request performs at most one bounded segment read independently of total archived history.
#[test]
fn external_io_releases_sql_and_only_reads_one_segment() {
    let (_root, log, reader, _) = fixture();
    let prepared = log.prepare_event_history_page(None, 1000).unwrap();
    let observed = WriterDuringRead {
        log: log.clone(),
        reader,
        calls: AtomicUsize::new(0),
    };
    let page = SqliteEventLog::read_prepared_event_history_page(prepared, &observed).unwrap();
    assert_eq!(observed.calls.load(Ordering::SeqCst), 1);
    assert_eq!(page.next_after_sequence, 2);
    assert_eq!(page.through_sequence, 6);
    assert_eq!(log.events().unwrap().last().unwrap().sequence, 7);
}

/// A future cursor is an ordinary empty poll and does not force an unrelated old segment read.
#[test]
fn future_cursor_is_empty_without_archive_io() {
    let (_root, log, _reader, _) = fixture();
    let page = SqliteEventLog::read_prepared_event_history_page(
        log.prepare_event_history_page(Some(99), 0).unwrap(),
        &CorruptReader,
    )
    .unwrap();
    assert!(page.events.is_empty());
    assert_eq!(page.next_after_sequence, 99);
    assert!(!page.has_more);
}

/// Isolated original content publisher/reader fixture; original receipt remains independent from bytes.
#[derive(Default)]
struct ContentFixture {
    /// Complete original operation and exact immutable segment; no later query may replace either.
    publication: Mutex<Option<(ArchiveIntent, Vec<u8>)>>,
}

impl ArchiveSink for ContentFixture {
    /// Content fixture refuses legacy byte-only publication.
    fn append_segment(
        &self,
        _manifest: &ArchiveManifest,
        _bytes: &[u8],
    ) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
        Err("content publication requires original intent".into())
    }

    /// Freezes actual original intent and bytes before source compaction may record its receipt.
    fn append_catalog_segment(
        &self,
        manifest: &ArchiveManifest,
        bytes: &[u8],
        _catalog: &[PersistedEvent],
        checkpoint: &PersistedCheckpoint,
        intent: &ArchiveIntent,
    ) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
        assert_eq!(manifest, &intent.manifest);
        assert_eq!(checkpoint, &intent.checkpoint);
        *self.publication.lock().unwrap() = Some((intent.clone(), bytes.to_vec()));
        Ok(())
    }

    /// Models a complete immutable publication identity independent from its event segment digest.
    fn inspect_catalog_segment(
        &self,
        intent: &ArchiveIntent,
    ) -> Result<String, Box<dyn std::error::Error + Send + Sync>> {
        let publication = self.publication.lock().unwrap();
        if publication.as_ref().map(|value| &value.0) != Some(intent) {
            return Err("original unpublished".into());
        }
        Ok(format!(
            "sha256:{:x}",
            Sha256::digest(b"complete original content publication")
        ))
    }
}

impl EventHistoryArchiveReader for ContentFixture {
    /// Requires original SQL receipt equality before returning the immutable published event segment.
    fn read_published_segment(
        &self,
        manifest: &ArchiveManifest,
        intent: Option<&ArchiveIntent>,
        receipt: Option<&str>,
    ) -> Result<Vec<u8>, String> {
        let intent = intent.ok_or("missing original content intent")?;
        let proof = self
            .inspect_catalog_segment(intent)
            .map_err(|error| error.to_string())?;
        if receipt != Some(proof.as_str()) || manifest != &intent.manifest {
            return Err("original content receipt differs".into());
        }
        Ok(self.publication.lock().unwrap().as_ref().unwrap().1.clone())
    }
}

/// Completed v2 history requires the exact first intent and actual original publication receipt.
#[test]
fn content_history_requires_original_completed_receipt() {
    let root = tempfile::tempdir().unwrap();
    let log = SqliteEventLog::open(root.path().join("history.sqlite3")).unwrap();
    append(&log, 1);
    log.begin_batch().unwrap();
    log.save_checkpoint(crate::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA, "{}")
        .unwrap();
    log.commit_batch().unwrap();
    let sink = ContentFixture::default();
    let request = ArchiveRequest {
        command_id: "content-original".into(),
        principal: "admin".into(),
        tenant: "*".into(),
        source_owner: format!("artifact-store:{}", "a".repeat(64)),
        authority_id: log.archive_authority_id().unwrap(),
        destination: root.path().join("archive").to_string_lossy().into(),
        budget_ms: 60_000,
    };
    log.archive_and_gc_with_catalog(1, &sink, request).unwrap();
    let page = SqliteEventLog::read_prepared_event_history_page(
        log.prepare_event_history_page(None, 10).unwrap(),
        &sink,
    )
    .unwrap();
    assert_eq!(page.events.len(), 1);
    assert_eq!(page.next_after_sequence, 1);
    assert!(!page.has_more);
    let original_authority = log.archive_authority_id().unwrap();
    log.connection
        .lock()
        .unwrap()
        .execute(
            "UPDATE event_log_metadata SET value=?1 WHERE key='archive_authority_id'",
            [format!("controller-store:{}", "e".repeat(64))],
        )
        .unwrap();
    assert!(log.prepare_event_history_page(None, 10).is_err());
    log.connection
        .lock()
        .unwrap()
        .execute(
            "UPDATE event_log_metadata SET value=?1 WHERE key='archive_authority_id'",
            [original_authority],
        )
        .unwrap();
    let receipt = format!("sha256:{}", "c".repeat(64));
    log.connection
        .lock()
        .unwrap()
        .execute(
            "UPDATE event_archive_operations SET receipt_json=?1",
            [receipt],
        )
        .unwrap();
    assert!(
        SqliteEventLog::read_prepared_event_history_page(
            log.prepare_event_history_page(None, 10).unwrap(),
            &sink
        )
        .is_err()
    );
    log.connection
        .lock()
        .unwrap()
        .execute(
            "UPDATE event_archive_operations SET state='pending',receipt_json=NULL",
            [],
        )
        .unwrap();
    assert!(log.prepare_event_history_page(None, 10).is_err());
}

/// Limits are applied to original envelope counts and input bytes before returning a page.
#[test]
fn hot_page_count_and_payload_bytes_are_bounded() {
    let root = tempfile::tempdir().unwrap();
    let log = SqliteEventLog::open(root.path().join("history.sqlite3")).unwrap();
    for number in 1..=1_002 {
        append(&log, number);
    }
    let page = SqliteEventLog::read_prepared_event_history_page(
        log.prepare_event_history_page(None, usize::MAX).unwrap(),
        &CorruptReader,
    )
    .unwrap();
    assert_eq!(page.events.len(), MAX_EVENT_HISTORY_PAGE_EVENTS);
    assert_eq!(page.next_after_sequence, 1_000);
    assert!(page.has_more);
    let huge = "a".repeat(MAX_EVENT_HISTORY_PAGE_BYTES + 1);
    let mut payload: EventPayload =
        serde_json::from_str(&log.events_page(Some(1_000), 1).unwrap()[0].payload_json).unwrap();
    if let EventPayload::ExecutionGroupBlocked { reason, .. } = &mut payload {
        *reason = huge;
    }
    log.connection
        .lock()
        .unwrap()
        .execute(
            "UPDATE events SET payload_json=?1 WHERE sequence=1001",
            [serde_json::to_string(&payload).unwrap()],
        )
        .unwrap();
    assert!(log.prepare_event_history_page(Some(1_000), 10).is_err());
}

/// Indexed original manifest ranges cannot be silently replaced by overlapping or detached metadata.
#[test]
fn conflicting_manifest_coverage_is_refused() {
    let (_root, log, _reader, manifests) = fixture();
    let mut overlap = manifests[0].clone();
    overlap.start_sequence = 2;
    overlap.event_count = 1;
    log.connection
        .lock()
        .unwrap()
        .execute(
            "INSERT INTO event_archive_manifests VALUES(2,99,?1)",
            [serde_json::to_string(&overlap).unwrap()],
        )
        .unwrap();
    assert!(log.prepare_event_history_page(Some(1), 1).is_err());
}
