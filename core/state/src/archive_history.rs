//! Bounded original event history reads across committed archives and the hot SQL tail.

use super::*;
use std::io::Read;
use std::path::{Path, PathBuf};

/// Maximum complete original envelopes returned by one history query.
pub const MAX_EVENT_HISTORY_PAGE_EVENTS: usize = 1_000;
/// Maximum original payload/envelope input retained for one returned page.
pub const MAX_EVENT_HISTORY_PAGE_BYTES: usize = 4 * 1024 * 1024;

/// Deployment-owned immutable Archive reader; publication checks happen without the SQL writer.
pub trait EventHistoryArchiveReader {
    /// Reads one exact published segment after validating its original optional content receipt.
    fn read_published_segment(
        &self,
        manifest: &ArchiveManifest,
        original_intent: Option<&ArchiveIntent>,
        original_receipt: Option<&str>,
    ) -> Result<Vec<u8>, String>;
}

/// Local historical reader for legacy v1 file archives only; no content proof is inferred.
pub struct FileHistoryReader {
    /// Fixed deployment root, never a caller-selected query path.
    root: PathBuf,
}

impl FileHistoryReader {
    /// Uses the deployment's original legacy FileArchiveSink directory without creating files.
    pub fn new(root: impl AsRef<Path>) -> Self {
        Self {
            root: root.as_ref().to_path_buf(),
        }
    }
}

impl EventHistoryArchiveReader for FileHistoryReader {
    /// Requires the separately published exact manifest and bounded regular segment bytes.
    fn read_published_segment(
        &self,
        manifest: &ArchiveManifest,
        original_intent: Option<&ArchiveIntent>,
        original_receipt: Option<&str>,
    ) -> Result<Vec<u8>, String> {
        archive::validate_manifest(manifest)?;
        if manifest.schema != "roboguide.event-archive/v1"
            || original_intent.is_some()
            || original_receipt.is_some()
        {
            return Err("legacy history reader requires its original v1 publication".into());
        }
        if !self.root.is_absolute()
            || self
                .root
                .components()
                .any(|part| matches!(part, std::path::Component::ParentDir))
        {
            return Err("historical Archive root must be an unambiguous absolute path".into());
        }
        for ancestor in self.root.ancestors() {
            if !std::fs::symlink_metadata(ancestor)
                .map_err(|e| e.to_string())?
                .is_dir()
            {
                return Err("historical Archive root cannot traverse symlinks".into());
            }
        }
        let path = FileArchiveSink::new(&self.root).segment_path(manifest);
        let manifest_path = path.with_extension("manifest.json");
        let original: ArchiveManifest =
            serde_json::from_slice(&read_file(&manifest_path, 4096)?).map_err(|e| e.to_string())?;
        if original != *manifest {
            return Err("historical Archive published manifest differs".into());
        }
        read_file(&path, MAX_ARCHIVE_BYTES)
    }
}

/// Readonly bounded no-follow regular file access; neither absence nor corruption means expiry.
fn read_file(path: &Path, maximum: usize) -> Result<Vec<u8>, String> {
    use std::os::unix::fs::OpenOptionsExt;
    if !path.is_absolute()
        || path
            .components()
            .any(|part| matches!(part, std::path::Component::ParentDir))
    {
        return Err(
            "historical Archive requires an absolute deployment path without traversal".into(),
        );
    }
    for parent in path.ancestors().skip(1) {
        if !std::fs::symlink_metadata(parent)
            .map_err(|error| error.to_string())?
            .file_type()
            .is_dir()
        {
            return Err("historical Archive ancestors cannot contain symlinks".into());
        }
    }
    if !std::fs::symlink_metadata(path)
        .map_err(|e| e.to_string())?
        .is_file()
    {
        return Err("historical Archive requires an original regular file".into());
    }
    let file = std::fs::OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .open(path)
        .map_err(|e| e.to_string())?;
    if !file.metadata().map_err(|e| e.to_string())?.is_file()
        || file.metadata().map_err(|e| e.to_string())?.len() > maximum as u64
    {
        return Err("historical Archive file exceeds its original byte bound".into());
    }
    let mut bytes = Vec::new();
    file.take(maximum as u64 + 1)
        .read_to_end(&mut bytes)
        .map_err(|e| e.to_string())?;
    if bytes.len() > maximum {
        return Err("historical Archive grew beyond its byte bound".into());
    }
    Ok(bytes)
}

/// Original sequence page; short archive pages retain a resumable unchanged append cursor.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize)]
pub struct EventHistoryPage {
    /// Byte-exact original hot or archived envelopes, in append order.
    pub events: Vec<PersistedEvent>,
    /// Last returned original sequence, or the caller's original cursor for an empty page.
    pub next_after_sequence: u64,
    /// More original events existed at preparation time; later new events use normal polling.
    pub has_more: bool,
    /// Original committed hot-history boundary captured for this page.
    pub history_floor: u64,
    /// Original committed high-water captured without reading Archive content under the writer.
    pub through_sequence: u64,
}

/// Immutable owned query prepared under SQL; contains no writer guard or mutable authority.
pub struct PreparedEventHistoryPage {
    /// Original caller cursor, never translated into a local archive row number.
    after: u64,
    /// Bounded response count.
    limit: usize,
    /// Original current archive coverage.
    floor: u64,
    /// Captured original global sequence high-water.
    through: u64,
    /// One immutable archive per query bounds external I/O independently of history size.
    archived: Option<HistoryArchive>,
    /// Bounded original hot rows copied while the SQL snapshot is stable.
    hot: Vec<PersistedEvent>,
}

/// One original manifest and optional complete content-publication receipt.
struct HistoryArchive {
    /// Exact manifest retained by the original committed SQL operation.
    manifest: ArchiveManifest,
    /// Original v2 authority; absence is valid only for legacy v1 file segments.
    intent: Option<ArchiveIntent>,
    /// Original completed durable publication proof; a pending operation is never history.
    receipt: Option<String>,
}

/// Reports unavailable history without inventing a retention tombstone or empty successful page.
fn invalid(message: &str) -> SqliteEventLogError {
    SqliteEventLogError::ArchivePolicy(format!("event history: {message}"))
}

/// Bounds original payload plus immutable envelope strings before copying the next response row.
fn input_size(row: &PersistedEvent) -> usize {
    row.payload_json
        .len()
        .saturating_add(row.event_id.len())
        .saturating_add(row.correlation_id.len())
        .saturating_add(row.payload_schema.len())
        .saturating_add(row.causation_id.as_ref().map_or(0, String::len))
}

/// Resolves the immutable committed manifest covering exactly the next requested original event.
fn original_archive(
    connection: &Connection,
    next: u64,
    floor: u64,
) -> Result<HistoryArchive, SqliteEventLogError> {
    let mut manifests=connection.prepare("SELECT start_sequence,end_sequence,manifest_json FROM event_archive_manifests WHERE start_sequence<=?1 AND end_sequence>=?1 ORDER BY start_sequence LIMIT 2")?;
    let rows = manifests
        .query_map([next], |row| {
            Ok((
                row.get::<_, u64>(0)?,
                row.get::<_, u64>(1)?,
                row.get::<_, String>(2)?,
            ))
        })?
        .collect::<Result<Vec<_>, _>>()?;
    if rows.len() != 1 {
        return Err(invalid(
            "original archive manifest is missing or overlapping; expiry is unproven",
        ));
    }
    let (start, end, raw) = &rows[0];
    if raw.len() > 4096 {
        return Err(invalid("original archive manifest is unbounded"));
    }
    let manifest: ArchiveManifest =
        serde_json::from_str(raw).map_err(|e| invalid(&e.to_string()))?;
    archive::validate_manifest(&manifest).map_err(|e| invalid(&e))?;
    if manifest.start_sequence != *start
        || manifest.end_sequence != *end
        || manifest.end_sequence > floor
    {
        return Err(invalid(
            "manifest exceeds committed original archive coverage",
        ));
    }
    if manifest.schema == "roboguide.event-archive/v1" {
        return Ok(HistoryArchive {
            manifest,
            intent: None,
            receipt: None,
        });
    }
    let mut statement=connection.prepare("SELECT command_id FROM event_archive_operations WHERE state='compacted' AND json_extract(intent_json,'$.manifest.start_sequence')=?1 AND json_extract(intent_json,'$.manifest.end_sequence')=?2 LIMIT 2")?;
    let ids = statement
        .query_map(
            params![manifest.start_sequence, manifest.end_sequence],
            |r| r.get::<_, String>(0),
        )?
        .collect::<Result<Vec<_>, _>>()?;
    if ids.len() != 1 {
        return Err(invalid(
            "original completed content operation is missing or ambiguous",
        ));
    }
    let intent_bytes:u64=connection.query_row("SELECT length(CAST(intent_json AS BLOB)) FROM event_archive_operations WHERE command_id=?1",[&ids[0]],|r|r.get(0))?;
    if intent_bytes > MAX_EVENT_HISTORY_PAGE_BYTES as u64 {
        return Err(invalid(
            "unsupported history query: original content intent exceeds the 4 MiB metadata bound; original data remains retained",
        ));
    }
    let (intent, receipt) = archive_operation::existing(connection, &ids[0])?
        .ok_or_else(|| invalid("original content operation disappeared"))?;
    let authority: Option<String> = connection
        .query_row(
            "SELECT value FROM event_log_metadata WHERE key='archive_authority_id'",
            [],
            |row| row.get(0),
        )
        .optional()?;
    if intent.manifest != manifest
        || receipt.is_none()
        || authority.as_deref() != Some(intent.request.authority_id.as_str())
        || archive_abort::completed_for(connection, &intent.request.command_id)?
    {
        return Err(invalid(
            "original content operation or publication receipt differs",
        ));
    }
    Ok(HistoryArchive {
        manifest,
        intent: Some(intent),
        receipt,
    })
}

impl SqliteEventLog {
    /// Captures one bounded original cursor page without performing external Archive reads.
    pub fn prepare_event_history_page(
        &self,
        after_sequence: Option<u64>,
        limit: usize,
    ) -> Result<PreparedEventHistoryPage, SqliteEventLogError> {
        self.ensure_no_open_batch()?;
        let after = after_sequence.unwrap_or(0);
        let limit = limit.clamp(1, MAX_EVENT_HISTORY_PAGE_EVENTS);
        let connection = self
            .connection
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        let floor: Option<String> = connection
            .query_row(
                "SELECT value FROM event_log_metadata WHERE key='history_floor'",
                [],
                |r| r.get(0),
            )
            .optional()?;
        let floor = floor
            .map(|value| {
                value
                    .parse::<u64>()
                    .map_err(|_| invalid("malformed original history floor"))
            })
            .transpose()?
            .unwrap_or(0);
        let maximum: u64 =
            connection.query_row("SELECT COALESCE(MAX(sequence),0) FROM events", [], |r| {
                r.get(0)
            })?;
        let through = maximum.max(floor);
        let mut prepared = PreparedEventHistoryPage {
            after,
            limit,
            floor,
            through,
            archived: None,
            hot: Vec::new(),
        };
        if after < floor {
            prepared.archived = Some(original_archive(&connection, after + 1, floor)?);
            return Ok(prepared);
        }
        if after >= through {
            return Ok(prepared);
        }
        let mut statement=connection.prepare("SELECT sequence,event_id,timestamp_ms,correlation_id,causation_id,payload_schema,payload_json,length(CAST(payload_json AS BLOB))+length(CAST(event_id AS BLOB))+length(CAST(correlation_id AS BLOB))+length(CAST(payload_schema AS BLOB))+COALESCE(length(CAST(causation_id AS BLOB)),0) FROM events WHERE sequence>?1 ORDER BY sequence LIMIT ?2")?;
        let mut bytes = 0usize;
        let mut rows = statement.query(params![after, limit as u64])?;
        while let Some(row) = rows.next()? {
            let size = row.get::<_, u64>(7)?;
            if size > MAX_EVENT_HISTORY_PAGE_BYTES.saturating_sub(bytes) as u64 {
                if prepared.hot.is_empty() {
                    return Err(invalid("original event exceeds history page byte bound"));
                }
                break;
            }
            let row = event_from_row(row)?;
            if row.sequence <= prepared.hot.last().map_or(after, |prior| prior.sequence) {
                return Err(invalid("original hot sequence is reordered"));
            }
            validate_persisted_event(&row)?;
            bytes += size as usize;
            prepared.hot.push(row);
        }
        if prepared.hot.is_empty() {
            return Err(invalid("original hot history is unavailable"));
        }
        Ok(prepared)
    }

    /// Reads and verifies an owned page outside SQL/application locks; never retries an effect.
    pub fn read_prepared_event_history_page<R: EventHistoryArchiveReader>(
        prepared: PreparedEventHistoryPage,
        reader: &R,
    ) -> Result<EventHistoryPage, SqliteEventLogError> {
        let mut events = prepared.hot;
        if let Some(archived) = prepared.archived {
            let bytes = reader
                .read_published_segment(
                    &archived.manifest,
                    archived.intent.as_ref(),
                    archived.receipt.as_deref(),
                )
                .map_err(SqliteEventLogError::ArchiveSink)?;
            if bytes.len() > MAX_ARCHIVE_BYTES
                || format!("sha256:{:x}", Sha256::digest(&bytes))
                    != archived.manifest.segment_digest
            {
                return Err(invalid(
                    "published original archive segment checksum or bound differs",
                ));
            }
            if !bytes.ends_with(b"\n") {
                return Err(invalid("original archive segment is incomplete"));
            }
            let text = std::str::from_utf8(&bytes).map_err(|e| invalid(&e.to_string()))?;
            let mut count = 0u64;
            let mut selected_bytes = 0usize;
            let mut page_full = false;
            for line in text.split_terminator('\n') {
                let row: PersistedEvent =
                    serde_json::from_str(line).map_err(|e| invalid(&e.to_string()))?;
                if count >= archived.manifest.event_count {
                    return Err(invalid("original archive contains extra events"));
                }
                if row.sequence != archived.manifest.start_sequence + count {
                    return Err(invalid(
                        "original archived sequence is missing or reordered",
                    ));
                }
                application_replication::decode_persisted_event(&row)?;
                count += 1;
                if row.sequence <= prepared.after || page_full {
                    continue;
                }
                let size = input_size(&row);
                if events.len() == prepared.limit
                    || size > MAX_EVENT_HISTORY_PAGE_BYTES.saturating_sub(selected_bytes)
                {
                    if events.is_empty() {
                        return Err(invalid("original archived event exceeds page byte bound"));
                    }
                    page_full = true;
                    continue;
                }
                selected_bytes += size;
                events.push(row);
            }
            if count != archived.manifest.event_count {
                return Err(invalid("original archive event count differs"));
            }
            if events.is_empty() {
                return Err(invalid("original archived cursor has no covered event"));
            }
        }
        let next = events.last().map_or(prepared.after, |row| row.sequence);
        Ok(EventHistoryPage {
            events,
            next_after_sequence: next,
            has_more: next < prepared.through,
            history_floor: prepared.floor,
            through_sequence: prepared.through,
        })
    }
}

#[cfg(test)]
#[path = "archive_history_tests.rs"]
mod tests;
