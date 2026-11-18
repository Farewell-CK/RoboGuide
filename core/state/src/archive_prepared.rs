//! Opaque content-archive preparation, publication and final original-writer compaction.
//!
//! A successful v3 commit retires only this frozen prefix's value bodies when no hot event still
//! references them. The durable archive carries complete original event JSON, independent of those
//! hot bodies. Source-identity/reference index rows remain retained: their growth still requires
//! separate checkpoint, decision, backup and replica coverage before metadata retirement is safe.

use super::*;

/// Frozen original content operation; prepare under the application writer, then release it.
pub struct PreparedContentArchive {
    /// Original durable operation and complete checkpoint, never rebased during publication.
    intent: super::super::archive_operation::ArchiveIntent,
    /// Original source prefix, bounded independently from all later appended history.
    events: Vec<PersistedEvent>,
    /// Exact encoded immutable segment published outside the application writer.
    bytes: Vec<u8>,
    /// Complete bounded original protected Catalog/source envelope closure.
    catalog: Vec<PersistedEvent>,
    /// Original committed archive coverage before this operation was prepared.
    floor: u64,
    /// Original archive timestamp high-water; later events must never lower it.
    timestamp_floor: u64,
    /// Only the first admission may copy; retry observes its original publication.
    first_publication: bool,
    /// Exact original completed receipt, if this operation already finished.
    completed_receipt: Option<String>,
    /// Independent original-inode reader; full source-reference audit runs during publication only.
    source_retirement_reader: Option<super::super::retention_references::SourceRetirementReader>,
}

impl PreparedContentArchive {
    /// Returns the frozen original operation, useful for observability without changing its budget.
    pub fn intent(&self) -> &super::super::archive_operation::ArchiveIntent {
        &self.intent
    }

    /// Returns the original bounded sequence range and segment digest.
    pub fn manifest(&self) -> &ArchiveManifest {
        &self.intent.manifest
    }
}

/// Publication proof bound to its owned preparation; finalization reacquires the original writer.
pub struct PublishedContentArchive {
    /// Opaque original operation whose source identities and bounds cannot be replaced by callers.
    prepared: PreparedContentArchive,
    /// Actual original sink publication proof; no caller-supplied string can fabricate this token.
    receipt: String,
    /// Fully audited bounded original source proof captured while the application writer is free.
    source_retirement_proof: Option<super::super::retention_references::SourceRetirementProof>,
}

impl PublishedContentArchive {
    /// Returns the original published range for inspection without granting compaction authority.
    pub fn manifest(&self) -> &ArchiveManifest {
        &self.prepared.intent.manifest
    }
}

/// Maps failures to the existing archive policy contract without deleting or replacing evidence.
fn invalid(message: &str) -> SqliteEventLogError {
    SqliteEventLogError::ArchivePolicy(format!("prepared content archive: {message}"))
}

/// Reads one exact bounded contiguous original prefix before allocation of its payloads.
fn prefix(
    connection: &Connection,
    floor: u64,
    end: u64,
) -> Result<(Vec<PersistedEvent>, Vec<u8>), SqliteEventLogError> {
    if end <= floor || end - floor > MAX_ARCHIVE_EVENTS {
        return Err(invalid("source prefix is empty or exceeds the event bound"));
    }
    let input_bytes: u64 = connection.query_row(
        "SELECT COALESCE(SUM(length(CAST(payload_json AS BLOB))+length(CAST(event_id AS BLOB))+length(CAST(correlation_id AS BLOB))+COALESCE(length(CAST(causation_id AS BLOB)),0)),0) FROM events WHERE sequence>?1 AND sequence<=?2",
        params![floor,end],|row|row.get(0),
    )?;
    if input_bytes > (MAX_ARCHIVE_BYTES / 4) as u64 {
        return Err(invalid("source prefix exceeds the input byte bound"));
    }
    let mut statement = connection.prepare(
        "SELECT sequence,event_id,timestamp_ms,correlation_id,causation_id,payload_schema,payload_json FROM events WHERE sequence>?1 AND sequence<=?2 ORDER BY sequence",
    )?;
    let mut events = Vec::new();
    let mut bytes = Vec::new();
    for row in statement.query_map(params![floor, end], event_from_row)? {
        let event = row?;
        if event.sequence != floor + events.len() as u64 + 1 {
            return Err(invalid("original source prefix has missing events"));
        }
        validate_persisted_event(&event)?;
        if event.payload_json.len() > MAX_ARCHIVE_BYTES.saturating_sub(bytes.len()) / 2 {
            return Err(invalid("source prefix exceeds the encoded segment bound"));
        }
        serde_json::to_writer(&mut bytes, &event)
            .map_err(|error| SqliteEventLogError::Codec(error.to_string()))?;
        bytes.push(b'\n');
        if bytes.len() > MAX_ARCHIVE_BYTES {
            return Err(invalid("encoded segment exceeds its byte bound"));
        }
        events.push(event);
    }
    if events.len() as u64 != end - floor {
        return Err(invalid("original source prefix is incomplete"));
    }
    Ok((events, bytes))
}

/// Rechecks exact bounded original input rows without making a second full prefix allocation.
fn require_unchanged_prefix(
    connection: &Connection,
    prepared: &PreparedContentArchive,
) -> Result<(), SqliteEventLogError> {
    let mut statement = connection.prepare(
        "SELECT sequence,event_id,timestamp_ms,correlation_id,causation_id,payload_schema,payload_json FROM events WHERE sequence>?1 AND sequence<=?2 ORDER BY sequence",
    )?;
    let mut index = 0usize;
    for row in statement.query_map(
        params![prepared.floor, prepared.intent.manifest.end_sequence],
        event_from_row,
    )? {
        let event = row?;
        if prepared.events.get(index) != Some(&event) {
            return Err(invalid("original source prefix changed during publication"));
        }
        index += 1;
    }
    if index != prepared.events.len() {
        return Err(invalid(
            "original source prefix disappeared during publication",
        ));
    }
    Ok(())
}

impl SqliteEventLog {
    /// Freezes and admits bounded original content under the caller's existing application writer.
    pub fn prepare_content_archive(
        &self,
        end_sequence: u64,
        request: super::super::archive_operation::ArchiveRequest,
    ) -> Result<PreparedContentArchive, SqliteEventLogError> {
        self.require_writes_enabled()?;
        self.observe_write_result(self.prepare_content_archive_inner(end_sequence, request))
    }

    /// Reads the original source/checkpoint atomically with respect to the caller's writer order.
    fn prepare_content_archive_inner(
        &self,
        end_sequence: u64,
        request: super::super::archive_operation::ArchiveRequest,
    ) -> Result<PreparedContentArchive, SqliteEventLogError> {
        self.ensure_no_open_batch()?;
        if end_sequence == 0 || self.replicator.is_some() {
            return Err(invalid(
                "requires a positive standalone original content cutoff",
            ));
        }
        let connection = self
            .connection
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        if super::super::archive_abort::completed_for(&connection, &request.command_id)? {
            return Err(invalid("original Archive was explicitly aborted"));
        }
        let existing = super::super::archive_operation::existing(&connection, &request.command_id)?;
        if let Some((intent, receipt)) = &existing {
            if intent.request != request || intent.manifest.end_sequence != end_sequence {
                return Err(invalid("original Archive command identity conflicts"));
            }
            if let Some(receipt) = receipt {
                return Ok(PreparedContentArchive {
                    intent: intent.clone(),
                    events: Vec::new(),
                    bytes: Vec::new(),
                    catalog: Vec::new(),
                    floor: intent.manifest.start_sequence - 1,
                    timestamp_floor: 0,
                    first_publication: false,
                    completed_receipt: Some(receipt.clone()),
                    source_retirement_reader: None,
                });
            }
        }
        let checkpoint = checkpoint_store::read(&connection)?
            .ok_or_else(|| invalid("complete current checkpoint is unavailable"))?;
        authority_journal::validate(&connection, Some(&checkpoint.snapshot))?;
        if checkpoint.snapshot.schema != crate::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA
            || checkpoint.snapshot.event_sequence < end_sequence
        {
            return Err(invalid(
                "current original authority checkpoint does not cover cutoff",
            ));
        }
        let floor = read_history_floor(&connection)?;
        let timestamp_floor = read_archive_timestamp_floor(&connection)?;
        let source_retirement_reader = if self.events_table == "controller_v3_events" {
            Some(super::super::retention_references::SourceRetirementReader::open(&connection)?)
        } else {
            None
        };
        let (events, bytes) = prefix(&connection, floor, end_sequence)?;
        let actual_digest = format!("sha256:{:x}", Sha256::digest(&bytes));
        let (intent, catalog, first_publication) = if let Some((intent, None)) = existing {
            if intent.manifest.start_sequence != floor + 1
                || intent.manifest.segment_digest != actual_digest
            {
                return Err(invalid("original pending Archive source changed"));
            }
            (intent, Vec::new(), false)
        } else {
            let catalog = super::super::catalog_archive::current(&connection)?;
            let manifest = ArchiveManifest {
                schema: "roboguide.event-archive/v2".into(),
                start_sequence: floor + 1,
                end_sequence,
                event_count: events.len() as u64,
                segment_digest: actual_digest,
                checkpoint_sequence: checkpoint.snapshot.event_sequence,
                checkpoint_digest: Some(checkpoint_identity(&checkpoint.snapshot)),
            };
            let intent = super::super::archive_operation::ArchiveIntent::new(
                request,
                manifest,
                checkpoint.snapshot.as_ref().clone(),
                &catalog,
                &events,
            )?;
            super::super::archive_operation::admit(&connection, &intent)?;
            (intent, catalog, true)
        };
        Ok(PreparedContentArchive {
            intent,
            events,
            bytes,
            catalog,
            floor,
            timestamp_floor,
            first_publication,
            completed_receipt: None,
            source_retirement_reader,
        })
    }

    /// Publishes and verifies frozen bytes without acquiring the SQL or application writer locks.
    pub fn publish_prepared_content_archive<S: ArchiveSink>(
        prepared: PreparedContentArchive,
        sink: &S,
    ) -> Result<PublishedContentArchive, SqliteEventLogError> {
        let intent = &prepared.intent;
        let receipt = if let Some(original) = &prepared.completed_receipt {
            let proof = sink
                .inspect_catalog_segment(intent)
                .map_err(|error| SqliteEventLogError::ArchiveSink(error.to_string()))?;
            if &proof != original {
                return Err(invalid("original completed publication proof changed"));
            }
            proof
        } else {
            if prepared.first_publication {
                sink.append_catalog_segment(
                    &intent.manifest,
                    &prepared.bytes,
                    &prepared.catalog,
                    &intent.checkpoint,
                    intent,
                )
                .map_err(|error| SqliteEventLogError::ArchiveSink(error.to_string()))?;
            }
            sink.confirm_catalog_segment(intent)
                .map_err(|error| SqliteEventLogError::ArchiveSink(error.to_string()))?
        };
        if receipt.strip_prefix("sha256:").is_none_or(|hex| {
            hex.len() != 64
                || !hex
                    .bytes()
                    .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        }) {
            return Err(invalid(
                "publication receipt is not an original content checksum",
            ));
        }
        let source_retirement_proof = prepared
            .source_retirement_reader
            .as_ref()
            .map(|reader| reader.capture(&prepared.intent, &prepared.events))
            .transpose()?;
        Ok(PublishedContentArchive {
            prepared,
            receipt,
            source_retirement_proof,
        })
    }

    /// Revalidates original input/current authority under the reacquired writer, preserving all new state.
    pub fn finish_content_archive<S: ArchiveSink>(
        &self,
        published: PublishedContentArchive,
        sink: &S,
    ) -> Result<ArchiveManifest, SqliteEventLogError> {
        self.require_writes_enabled()?;
        self.observe_write_result(self.finish_content_archive_inner(published, sink, None))
    }

    /// Finalizes with the actual Controller clock and bounded retirement of expired source indexes.
    ///
    /// The caller reacquires its original application writer before sampling `controller_now`.
    /// This opt-in clock authority is absent from the compatibility finalizer, which keeps indexes.
    pub fn finish_content_archive_at<S: ArchiveSink>(
        &self,
        published: PublishedContentArchive,
        sink: &S,
        controller_now: TimestampMs,
    ) -> Result<ArchiveManifest, SqliteEventLogError> {
        self.require_writes_enabled()?;
        self.observe_write_result(self.finish_content_archive_inner(
            published,
            sink,
            Some(controller_now),
        ))
    }

    /// Commits only the original covered prefix after current permissions and ownership still match.
    fn finish_content_archive_inner<S: ArchiveSink>(
        &self,
        published: PublishedContentArchive,
        sink: &S,
        controller_now: Option<TimestampMs>,
    ) -> Result<ArchiveManifest, SqliteEventLogError> {
        self.ensure_no_open_batch()?;
        if self.replicator.is_some() {
            return Err(invalid(
                "replicated compaction requires its coordinated protocol",
            ));
        }
        let source_retirement_proof = published.source_retirement_proof;
        let prepared = published.prepared;
        let intent = &prepared.intent;
        sink.require_commit_permission()
            .map_err(|error| SqliteEventLogError::ArchiveSink(error.to_string()))?;
        let mut connection = self
            .connection
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        let authority: String = connection.query_row(
            "SELECT value FROM event_log_metadata WHERE key='archive_authority_id'",
            [],
            |row| row.get(0),
        )?;
        if authority != intent.request.authority_id
            || super::super::archive_abort::completed_for(&connection, &intent.request.command_id)?
        {
            return Err(invalid("original Archive authority changed or was aborted"));
        }
        let existing =
            super::super::archive_operation::existing(&connection, &intent.request.command_id)?;
        if let Some(original) = &prepared.completed_receipt {
            if existing.as_ref() != Some(&(intent.clone(), Some(original.clone())))
                || &published.receipt != original
            {
                return Err(invalid("original completed operation changed"));
            }
            return Ok(intent.manifest.clone());
        }
        if existing.as_ref() != Some(&(intent.clone(), None))
            || read_history_floor(&connection)? != prepared.floor
        {
            return Err(invalid(
                "original pending ownership or archive floor changed",
            ));
        }
        let current = checkpoint_store::read(&connection)?
            .ok_or_else(|| invalid("current complete checkpoint is missing"))?;
        authority_journal::validate(&connection, Some(&current.snapshot))?;
        if current.snapshot.schema != crate::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA
            || current.snapshot.event_sequence < intent.checkpoint.event_sequence
            || current.snapshot.event_sequence < intent.manifest.end_sequence
        {
            return Err(invalid(
                "current checkpoint no longer covers original authority",
            ));
        }
        require_unchanged_prefix(&connection, &prepared)?;
        let current_timestamp_floor = read_archive_timestamp_floor(&connection)?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        super::super::catalog_archive::extend(
            &transaction,
            &prepared.events,
            intent.manifest.end_sequence,
        )?;
        transaction.execute_batch("CREATE TABLE IF NOT EXISTS event_archive_manifests(start_sequence INTEGER PRIMARY KEY,end_sequence INTEGER UNIQUE NOT NULL,manifest_json TEXT NOT NULL)")?;
        let removed = if self.events_table == "controller_v3_events" {
            super::super::telemetry_storage::retire_prefix(
                &transaction,
                prepared.floor,
                intent.manifest.end_sequence,
            )?
        } else {
            transaction.execute(
                &format!(
                    "DELETE FROM {} WHERE sequence>?1 AND sequence<=?2",
                    self.events_table
                ),
                params![prepared.floor, intent.manifest.end_sequence],
            )? as u64
        };
        if removed != intent.manifest.event_count {
            return Err(invalid(
                "original source prefix count changed before commit",
            ));
        }
        let timestamp_floor = prepared
            .events
            .iter()
            .map(|event| event.timestamp_ms)
            .max()
            .unwrap_or(0)
            .max(prepared.timestamp_floor)
            .max(current_timestamp_floor);
        for (key, value) in [
            ("history_floor", intent.manifest.end_sequence.to_string()),
            ("archive_timestamp_floor", timestamp_floor.to_string()),
            (
                "archive_manifest",
                serde_json::to_string(&intent.manifest)
                    .map_err(|error| SqliteEventLogError::Codec(error.to_string()))?,
            ),
        ] {
            transaction.execute("INSERT INTO event_log_metadata(key,value) VALUES(?1,?2) ON CONFLICT(key) DO UPDATE SET value=excluded.value",params![key,value])?;
        }
        transaction.execute(
            "INSERT INTO event_archive_manifests VALUES(?1,?2,?3)",
            params![
                intent.manifest.start_sequence,
                intent.manifest.end_sequence,
                serde_json::to_string(&intent.manifest)
                    .map_err(|error| SqliteEventLogError::Codec(error.to_string()))?
            ],
        )?;
        let changed=transaction.execute("UPDATE event_archive_operations SET state='compacted',receipt_json=?1 WHERE command_id=?2 AND state='pending'",params![published.receipt,intent.request.command_id])?;
        if changed != 1 {
            return Err(invalid("original pending operation changed before commit"));
        }
        if self.events_table == "controller_v3_events"
            && let Some(now) = controller_now
        {
            super::super::retention::observe_clock(&transaction, now.as_millis())?;
            if let Some(reader) = &prepared.source_retirement_reader {
                reader.verify_identity()?;
            }
            let outcome = super::super::retention_references::compact_published_sources(
                &transaction,
                intent,
                &prepared.events,
                now.as_millis(),
                source_retirement_proof.as_ref(),
            )?;
            let mut ordinary_events = 0u64;
            for event in &prepared.events {
                let payload: EventPayload = serde_json::from_str(&event.payload_json)
                    .map_err(|error| SqliteEventLogError::Codec(error.to_string()))?;
                ordinary_events += u64::from(super::super::retention::ordinary(&payload));
            }
            let protected_events = intent.manifest.event_count - ordinary_events;
            let audit = serde_json::to_string(&serde_json::json!({
                "schema":"roboguide.retired-state-source-index/v0.1",
                "command_id":intent.request.command_id,
                "start_sequence":intent.manifest.start_sequence,
                "end_sequence":intent.manifest.end_sequence,
                "segment_digest":intent.manifest.segment_digest,
                "controller_observed_at_ms":now.as_millis(),
                "ordinary_events":ordinary_events,"protected_events":protected_events,
                "event_segment_class":if protected_events==0 {"ordinary-telemetry"} else if ordinary_events==0 {"protected-evidence"} else {"mixed-protected"},
                "permanent_expiry_reference_coverage":"unknown",
                "outcome":outcome,
            }))
            .map_err(|error| SqliteEventLogError::Codec(error.to_string()))?;
            let key = format!(
                "retention_source_compaction:{:x}",
                Sha256::digest(intent.request.command_id.as_bytes())
            );
            transaction.execute(
                "INSERT INTO event_log_metadata(key,value) VALUES(?1,?2)",
                params![key, audit],
            )?;
        }
        sink.require_commit_permission()
            .map_err(|error| SqliteEventLogError::ArchiveSink(error.to_string()))?;
        transaction.commit()?;
        Ok(intent.manifest.clone())
    }
}

#[cfg(test)]
#[path = "archive_prepared_tests.rs"]
mod tests;
