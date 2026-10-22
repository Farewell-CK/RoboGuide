//! Original Catalog recovery evidence retained atomically with archive history-floor advancement.

use super::*;

/// Closed persistent original projection contract, independent of the application checkpoint body.
const SCHEMA: &str = "roboguide.catalog-archive-checkpoint/v0.1";
/// Explicitly bounded complete projection history; capacity exhaustion refuses maintenance.
const MAX_BYTES: usize = 64 * 1024 * 1024;
/// Maximum original records retained by this bootstrap projection.
const MAX_EVENTS: usize = 16_384;

/// Exact persisted recovery head; rows cannot disappear under a still-valid aggregate digest.
#[derive(serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct Head {
    /// Closed recovery interpretation.
    schema: String,
    /// Original archive coverage, independent of the last catalog event's own sequence.
    through_sequence: u64,
    /// Actual retained original protected rows.
    event_count: usize,
    /// Canonical exact ordered full envelope/payload identity.
    digest: String,
}

/// Hashes complete original ordered records without changing payload strings or numeric encodings.
fn identity(events: &[PersistedEvent]) -> Result<String, SqliteEventLogError> {
    let bytes = serde_json::to_vec(
        &serde_json::to_value(events)
            .map_err(|error| SqliteEventLogError::Codec(error.to_string()))?,
    )
    .map_err(|error| SqliteEventLogError::Codec(error.to_string()))?;
    if bytes.len() > MAX_BYTES || events.len() > MAX_EVENTS {
        return Err(SqliteEventLogError::ArchivePolicy(
            "Catalog archive recovery exceeds original bounds".into(),
        ));
    }
    Ok(format!("sha256:{:x}", Sha256::digest(bytes)))
}

/// Validates every original protected row and complete aggregate before any replay or extension.
pub(super) fn read(connection: &Connection) -> Result<Vec<PersistedEvent>, SqliteEventLogError> {
    let head: Option<String> = connection
        .query_row(
            "SELECT value FROM event_log_metadata WHERE key='catalog_archive_checkpoint'",
            [],
            |row| row.get(0),
        )
        .optional()?;
    let Some(head) = head else {
        let exists: bool = connection.query_row(
            "SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE name='catalog_archive_events')",
            [],
            |row| row.get(0),
        )?;
        let archive: Option<String> = connection
            .query_row(
                "SELECT value FROM event_log_metadata WHERE key='archive_manifest'",
                [],
                |row| row.get(0),
            )
            .optional()?;
        let operations: bool = connection.query_row(
            "SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE name='event_archive_operations')",
            [],
            |row| row.get(0),
        )?;
        let compacted = operations
            && connection.query_row(
                "SELECT EXISTS(SELECT 1 FROM event_archive_operations WHERE state='compacted')",
                [],
                |row| row.get::<_, bool>(0),
            )?;
        if exists
            || compacted
            || archive.is_some_and(|value| {
                serde_json::from_str::<super::archive::ArchiveManifest>(&value)
                    .is_ok_and(|manifest| manifest.schema == "roboguide.event-archive/v2")
            })
        {
            return Err(SqliteEventLogError::Codec(
                "mandatory original Catalog archive checkpoint is missing".into(),
            ));
        }
        return Ok(Vec::new());
    };
    let head: Head = serde_json::from_str(&head)
        .map_err(|error| SqliteEventLogError::Codec(error.to_string()))?;
    if head.schema != SCHEMA || head.event_count > MAX_EVENTS || head.through_sequence == 0 {
        return Err(SqliteEventLogError::Codec(
            "original Catalog archive recovery head is invalid".into(),
        ));
    }
    let floor: u64 = connection
        .query_row(
            "SELECT value FROM event_log_metadata WHERE key='history_floor'",
            [],
            |row| row.get::<_, String>(0),
        )?
        .parse()
        .map_err(|error: std::num::ParseIntError| SqliteEventLogError::Codec(error.to_string()))?;
    if head.through_sequence != floor {
        return Err(SqliteEventLogError::Codec(
            "Catalog archive recovery differs from actual history floor".into(),
        ));
    }
    let mut statement =
        connection.prepare("SELECT sequence,data FROM catalog_archive_events ORDER BY sequence")?;
    let mut events = Vec::new();
    let mut bytes = 0usize;
    for row in statement.query_map([], |row| {
        Ok((row.get::<_, u64>(0)?, row.get::<_, String>(1)?))
    })? {
        let (sequence, data) = row?;
        bytes = bytes.saturating_add(data.len());
        if events.len() >= MAX_EVENTS || bytes > MAX_BYTES {
            return Err(SqliteEventLogError::Codec(
                "original Catalog archive history exceeds bounds".into(),
            ));
        }
        let event: PersistedEvent = serde_json::from_str(&data)
            .map_err(|error| SqliteEventLogError::Codec(error.to_string()))?;
        validate_persisted_event(&event)?;
        let payload: EventPayload = serde_json::from_str(&event.payload_json)
            .map_err(|error| SqliteEventLogError::Codec(error.to_string()))?;
        if event.sequence != sequence
            || sequence > head.through_sequence
            || !super::archive::requires_catalog_history(&payload)
        {
            return Err(SqliteEventLogError::Codec(
                "original Catalog archive row differs from its recovery coverage".into(),
            ));
        }
        events.push(event);
    }
    if events.len() != head.event_count || identity(&events)? != head.digest {
        return Err(SqliteEventLogError::Codec(
            "original Catalog archive recovery is incomplete or changed".into(),
        ));
    }
    Ok(events)
}

/// Freezes complete current Catalog and accepted proof history for an independent archive checkpoint.
pub(super) fn current(connection: &Connection) -> Result<Vec<PersistedEvent>, SqliteEventLogError> {
    let mut records = read(connection)?;
    let (count, bytes): (u64, u64) = connection.query_row(
        "SELECT COUNT(*),COALESCE(SUM(length(CAST(payload_json AS BLOB))),0) FROM events",
        [],
        |row| Ok((row.get(0)?, row.get(1)?)),
    )?;
    if count > MAX_EVENTS as u64 || bytes > MAX_BYTES as u64 {
        return Err(SqliteEventLogError::ArchivePolicy(
            "complete current Archive Catalog exceeds bounded evidence capacity".into(),
        ));
    }
    let mut statement = connection.prepare("SELECT sequence,event_id,timestamp_ms,correlation_id,causation_id,payload_schema,payload_json FROM events ORDER BY sequence")?;
    for row in statement.query_map([], event_from_row)? {
        let row = row?;
        validate_persisted_event(&row)?;
        let payload: EventPayload = serde_json::from_str(&row.payload_json)
            .map_err(|error| SqliteEventLogError::Codec(error.to_string()))?;
        if super::archive::requires_catalog_history(&payload) {
            records.push(row);
        }
    }
    identity(&records)?;
    Ok(records)
}

/// Adds exact original protected prefix rows in the caller's existing compact transaction.
pub(super) fn extend(
    connection: &Connection,
    inputs: &[PersistedEvent],
    through: u64,
) -> Result<(), SqliteEventLogError> {
    let mut records = read(connection)?;
    for input in inputs {
        let payload: EventPayload = serde_json::from_str(&input.payload_json)
            .map_err(|error| SqliteEventLogError::Codec(error.to_string()))?;
        if super::archive::requires_catalog_history(&payload) {
            records.push(input.clone());
        }
    }
    if records
        .windows(2)
        .any(|pair| pair[0].sequence >= pair[1].sequence)
    {
        return Err(SqliteEventLogError::Codec(
            "original Catalog archive prefix overlaps prior recovery".into(),
        ));
    }
    let digest = identity(&records)?;
    connection.execute_batch("CREATE TABLE IF NOT EXISTS catalog_archive_events(sequence INTEGER PRIMARY KEY NOT NULL,data TEXT NOT NULL)")?;
    for input in inputs {
        if !records
            .iter()
            .any(|record| record.sequence == input.sequence)
        {
            continue;
        }
        connection.execute(
            "INSERT INTO catalog_archive_events(sequence,data) VALUES (?1,?2)",
            params![
                input.sequence,
                serde_json::to_string(input)
                    .map_err(|error| SqliteEventLogError::Codec(error.to_string()))?
            ],
        )?;
    }
    let head = Head {
        schema: SCHEMA.into(),
        through_sequence: through,
        event_count: records.len(),
        digest,
    };
    connection.execute("INSERT INTO event_log_metadata(key,value) VALUES ('catalog_archive_checkpoint',?1) ON CONFLICT(key) DO UPDATE SET value=excluded.value",[serde_json::to_string(&head).map_err(|error|SqliteEventLogError::Codec(error.to_string()))?])?;
    Ok(())
}

impl SqliteEventLog {
    /// Reads only original Catalog recovery rows, preserving full original envelopes and timestamps.
    pub fn archived_catalog_events(&self) -> Result<Vec<PersistedEvent>, SqliteEventLogError> {
        self.ensure_no_open_batch()?;
        let connection = self
            .connection
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        read(&connection)
    }

    /// Replays the independent original Catalog prefix before the current unarchived event tail.
    pub fn catalog_replay_events(&self) -> Result<Vec<EventRecord>, SqliteEventLogError> {
        self.ensure_no_open_batch()?;
        let connection = self
            .connection
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        let mut records = read(&connection)?;
        let mut statement=connection.prepare("SELECT sequence,event_id,timestamp_ms,correlation_id,causation_id,payload_schema,payload_json FROM events ORDER BY sequence")?;
        records.extend(
            statement
                .query_map([], event_from_row)?
                .collect::<Result<Vec<_>, _>>()?,
        );
        let mut result = Vec::with_capacity(records.len());
        for event in records {
            validate_persisted_event(&event)?;
            let payload = serde_json::from_str(&event.payload_json)
                .map_err(|error| SqliteEventLogError::Codec(error.to_string()))?;
            result.push(EventRecord::new(
                EventId::new(event.event_id)
                    .map_err(|error| SqliteEventLogError::Codec(error.to_string()))?,
                TimestampMs::new(event.timestamp_ms),
                CorrelationId::new(event.correlation_id)
                    .map_err(|error| SqliteEventLogError::Codec(error.to_string()))?,
                event
                    .causation_id
                    .map(EventId::new)
                    .transpose()
                    .map_err(|error| SqliteEventLogError::Codec(error.to_string()))?,
                payload,
            ));
        }
        Ok(result)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use domain::*;
    use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};

    /// Independent explicit sink contract; no deployment, content placement or physical facts result.
    struct SnapshotSink {
        /// Actual original store used to reject authority callbacks under its storage mutex.
        log: SqliteEventLog,
        /// Complete original publication received before the SQL prefix can be deleted.
        originals: Mutex<Vec<PersistedEvent>>,
        /// Controlled publication outage leaves SQL and Catalog recovery unchanged.
        fail: AtomicBool,
        /// Actual fixture effect-boundary entries, independent of query or SQL reconciliation.
        effects: AtomicUsize,
    }

    impl super::super::archive::ArchiveSink for SnapshotSink {
        /// Legacy sink entry is never valid for the explicit original Catalog publication.
        fn append_segment(
            &self,
            _manifest: &super::super::archive::ArchiveManifest,
            _bytes: &[u8],
        ) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
            Err("explicit Catalog publication required".into())
        }

        /// Captures exact original envelopes/checkpoint before allowing the maintenance Commit.
        fn append_catalog_segment(
            &self,
            manifest: &super::super::archive::ArchiveManifest,
            bytes: &[u8],
            catalog: &[PersistedEvent],
            checkpoint: &PersistedCheckpoint,
            _intent: &super::super::archive_operation::ArchiveIntent,
        ) -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
            assert!(
                self.log.connection.try_lock().is_ok(),
                "source SQL mutex leaked into actual publication port"
            );
            assert_eq!(manifest.schema, "roboguide.event-archive/v2");
            assert_eq!(
                manifest.segment_digest,
                format!("sha256:{:x}", Sha256::digest(bytes))
            );
            assert!(checkpoint.event_sequence >= manifest.end_sequence);
            self.effects.fetch_add(1, Ordering::SeqCst);
            if self.fail.load(Ordering::SeqCst) {
                return Err("actual publication boundary unavailable".into());
            }
            *self.originals.lock().unwrap() = catalog.to_vec();
            Ok(())
        }

        /// Reports only an original publication actually retained by this contract fixture.
        fn inspect_catalog_segment(
            &self,
            intent: &super::super::archive_operation::ArchiveIntent,
        ) -> Result<String, Box<dyn std::error::Error + Send + Sync>> {
            assert!(
                self.log.connection.try_lock().is_ok(),
                "source SQL mutex leaked into original-result observation"
            );
            if self.fail.load(Ordering::SeqCst) || self.originals.lock().unwrap().is_empty() {
                return Err("original publication unavailable".into());
            }
            Ok(intent.catalog_digest.clone())
        }
    }

    /// Persists a real original Memory event and full valid authority journal checkpoint in SQLite.
    fn original(log: &SqliteEventLog) -> MemoryArtifactManifest {
        let manifest = MemoryArtifactManifest::new(
            MemorySelector::new(
                MemoryId::new("parser-original").unwrap(),
                MemoryRevisionId::new("one").unwrap(),
            ),
            MemoryKind::Artifact,
            "fixture-provider",
            MemoryOwner::RoboGuide {
                component: "fixture".into(),
            },
            MemoryScope::Global,
            MemoryVisibility::Discoverable,
            "opaque/v1",
            "application/octet-stream",
            None,
            None,
            None,
            None,
            TimestampMs::new(7),
        )
        .unwrap();
        log.begin_batch().unwrap();
        let mut writer = log.clone();
        writer.append(
            TimestampMs::new(7),
            &CorrelationId::new("original").unwrap(),
            None,
            EventPayload::MemoryManifestPublished {
                manifest: manifest.clone(),
            },
        );
        assert!(log.take_error().unwrap().is_none());
        let checkpoint = "{}";
        let command = ControlCommandJournalEntry {
            command_id: "original-command".into(),
            causation_id: None,
            authority_revision: 0,
            controller_epoch: None,
            plan_digest: None,
            input_digest: format!("sha256:{:x}", Sha256::digest(b"input")),
            ownership_digest: None,
            resulting_state_digest: format!("sha256:{:x}", Sha256::digest(checkpoint.as_bytes())),
            checkpoint_event_sequence: 1,
            checkpoint_schema: crate::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA.into(),
        };
        log.save_checkpoint_with_command(
            crate::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA,
            checkpoint,
            command,
        )
        .unwrap();
        log.commit_batch().unwrap();
        manifest
    }

    /// Publication/SQL failure leaves original evidence; successful compact replays exact prior catalog.
    #[test]
    fn original_catalog_survives_compaction_and_missing_projection_refuses_replay() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("events.sqlite3");
        let log = SqliteEventLog::open(&path).unwrap();
        let manifest = original(&log);
        let originals = log.events().unwrap();
        let sink = SnapshotSink {
            log: log.clone(),
            originals: Mutex::new(Vec::new()),
            fail: AtomicBool::new(false),
            effects: AtomicUsize::new(0),
        };
        let request = super::super::archive_operation::ArchiveRequest {
            command_id: "original-catalog-archive".into(),
            principal: "unit-admin".into(),
            tenant: "local".into(),
            source_owner: format!("artifact-store:{}", "a".repeat(64)),
            authority_id: log.archive_authority_id().unwrap(),
            destination: directory.path().join("archive").to_string_lossy().into(),
            budget_ms: 60_000,
        };
        let connection = Connection::open(&path).unwrap();
        connection.execute_batch("CREATE TRIGGER refuse_floor BEFORE INSERT ON event_log_metadata WHEN NEW.key='history_floor' BEGIN SELECT RAISE(ABORT,'actual compact SQL fault'); END").unwrap();
        assert!(
            log.archive_and_gc_with_catalog(1, &sink, request.clone())
                .is_err()
        );
        assert_eq!(log.events().unwrap(), originals);
        assert!(log.archived_catalog_events().unwrap().is_empty());
        connection
            .execute_batch("DROP TRIGGER refuse_floor")
            .unwrap();
        log.archive_and_gc_with_catalog(1, &sink, request.clone())
            .unwrap();
        log.archive_and_gc_with_catalog(1, &sink, request).unwrap();
        assert_eq!(sink.effects.load(Ordering::SeqCst), 1);
        assert_eq!(log.history_floor().unwrap(), 1);
        assert_eq!(log.len().unwrap(), 0);
        assert_eq!(log.archived_catalog_events().unwrap(), originals);
        let replay = log.catalog_replay_events().unwrap();
        assert_eq!(replay[0].timestamp(), TimestampMs::new(7));
        assert_eq!(
            replay[0].payload(),
            &EventPayload::MemoryManifestPublished { manifest }
        );
        connection
            .execute("DELETE FROM catalog_archive_events", [])
            .unwrap();
        assert!(log.catalog_replay_events().is_err());
        connection
            .execute(
                "DELETE FROM event_log_metadata WHERE key='catalog_archive_checkpoint'",
                [],
            )
            .unwrap();
        assert!(log.catalog_replay_events().is_err());
    }

    /// An uncertain original effect never gets called again, and its original pending pin blocks replacement.
    #[test]
    fn interrupted_original_effect_never_repeats_or_renews_its_budget() {
        let directory = tempfile::tempdir().unwrap();
        let log = SqliteEventLog::open(directory.path().join("events.sqlite3")).unwrap();
        original(&log);
        let sink = SnapshotSink {
            log: log.clone(),
            originals: Mutex::new(Vec::new()),
            fail: AtomicBool::new(true),
            effects: AtomicUsize::new(0),
        };
        let request = super::super::archive_operation::ArchiveRequest {
            command_id: "original-effect".into(),
            principal: "unit-admin".into(),
            tenant: "local".into(),
            source_owner: format!("artifact-store:{}", "a".repeat(64)),
            authority_id: log.archive_authority_id().unwrap(),
            destination: directory.path().join("archive").to_string_lossy().into(),
            budget_ms: 60_000,
        };
        assert!(
            log.archive_and_gc_with_catalog(1, &sink, request.clone())
                .is_err()
        );
        let first = log.archive_operation(&request.command_id).unwrap().unwrap();
        assert!(first.1.is_none());
        sink.fail.store(false, Ordering::SeqCst);
        assert!(
            log.archive_and_gc_with_catalog(1, &sink, request.clone())
                .is_err()
        );
        assert_eq!(sink.effects.load(Ordering::SeqCst), 1);
        assert_eq!(
            log.archive_operation(&request.command_id).unwrap().unwrap(),
            first
        );
        let mut replacement = request.clone();
        replacement.command_id = "replacement".into();
        assert!(
            log.archive_and_gc_with_catalog(1, &sink, replacement)
                .is_err()
        );
        assert_eq!(sink.effects.load(Ordering::SeqCst), 1);
        let mut renewed = request.clone();
        renewed.budget_ms += 1;
        assert!(log.archive_and_gc_with_catalog(1, &sink, renewed).is_err());
        assert_eq!(log.history_floor().unwrap(), 0);
        assert_eq!(log.len().unwrap(), 1);
    }

    /// Accepted empty operation authority cannot be replaced after losing its identity, marker or table.
    #[test]
    fn accepted_archive_authority_never_reinitializes_missing_original_records() {
        for statement in [
            "DROP TABLE event_archive_operations",
            "DELETE FROM event_log_metadata WHERE key='archive_operations_schema'",
            "DELETE FROM event_log_metadata WHERE key='archive_authority_id'",
        ] {
            let directory = tempfile::tempdir().unwrap();
            let path = directory.path().join("events.sqlite3");
            let log = SqliteEventLog::open(&path).unwrap();
            original(&log);
            let original = log.archive_authority_id().unwrap();
            let connection = Connection::open(&path).unwrap();
            connection.execute_batch(statement).unwrap();
            assert!(log.archive_authority_id().is_err());
            assert!(log.load_checkpoint().is_err());
            assert!(super::super::archive_operation::initialize(&connection, true).is_err());
            assert!(log.archive_authority_id().is_err());
            assert!(!original.is_empty());
        }
    }
}
