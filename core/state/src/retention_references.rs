//! Exact original State-input references consumed by protected authority decisions.

use super::*;

#[path = "retention_reference_compaction.rs"]
mod compaction;
pub(super) use compaction::{
    SourceRetirementProof, SourceRetirementReader, compact_published_sources,
};

/// Bounded decision-reference proof for one proposed original event range.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RetentionDecisionReferences {
    /// All original events through this global sequence were indexed transactionally.
    pub indexed_through_sequence: u64,
    /// Exact observed input events still required by immutable original decision evidence.
    pub pins: Vec<RetentionPin>,
    /// Original decisions whose selected State record has no identifiable source event.
    pub unresolved_decision_sequences: Vec<u64>,
    /// Further unresolved decisions exist beyond this bounded report.
    pub more_unresolved: bool,
    /// Original archive-prefix references have not been reindexed from verified archive content.
    pub archived_prefix_unresolved: bool,
}

/// Checksummed original coverage and protected-reference aggregates, without repeated business data.
struct Head {
    /// Last original global event sequence covered by this index.
    through: u64,
    /// Exact retained source identity count, independent of value-body size.
    records: u64,
    /// Exact protected decision dependency count.
    edges: u64,
    /// Exact unresolved original dependency count.
    gaps: u64,
    /// Ordered checksum of protected dependency metadata only.
    edges_digest: String,
    /// Ordered checksum of unresolved original dependency metadata only.
    gaps_digest: String,
    /// A preexisting archive prefix did not supply its original dependency proof.
    archived_prefix_unresolved: bool,
}

/// Stable independent hash domains distinguish an empty dependency set from missing proof.
fn empty_digest(kind: &str) -> String {
    format!("sha256:{:x}", Sha256::digest(kind.as_bytes()))
}

/// Extends one ordered dependency checksum using exact metadata bytes and length.
fn extend(previous: &str, data: &str) -> String {
    let mut hash = Sha256::new();
    hash.update(previous.as_bytes());
    hash.update((data.len() as u64).to_le_bytes());
    hash.update(data.as_bytes());
    format!("sha256:{:x}", hash.finalize())
}

/// Binds coverage, counters, dependency hashes and archive completeness under one checksum.
fn head_digest(head: &Head) -> Result<String, SqliteEventLogError> {
    let data = serde_json::to_vec(&(
        head.through,
        head.records,
        head.edges,
        head.gaps,
        &head.edges_digest,
        &head.gaps_digest,
        head.archived_prefix_unresolved,
    ))
    .map_err(|error| invalid(&error.to_string()))?;
    Ok(format!("sha256:{:x}", Sha256::digest(data)))
}

/// Saves the exact original dependency authority in its source event transaction.
fn save_head(connection: &Connection, head: &Head) -> Result<(), SqliteEventLogError> {
    connection.execute(
        "INSERT INTO retention_reference_head VALUES(1,?1,?2,?3,?4,?5,?6,?7,?8) ON CONFLICT(singleton) DO UPDATE SET through_sequence=excluded.through_sequence,records_count=excluded.records_count,edges_count=excluded.edges_count,gaps_count=excluded.gaps_count,edges_digest=excluded.edges_digest,gaps_digest=excluded.gaps_digest,archived_prefix_unresolved=excluded.archived_prefix_unresolved,head_digest=excluded.head_digest",
        params![head.through,head.records,head.edges,head.gaps,head.edges_digest,head.gaps_digest,head.archived_prefix_unresolved,head_digest(head)?],
    )?;
    Ok(())
}

/// Reads the required original head; missing coverage never recreates an empty authority.
fn read_head(connection: &Connection) -> Result<Head, SqliteEventLogError> {
    let count: u64 =
        connection.query_row("SELECT COUNT(*) FROM retention_reference_head", [], |row| {
            row.get(0)
        })?;
    if count != 1 {
        return Err(invalid(
            "original reference coverage is missing or duplicated",
        ));
    }
    let (head, expected): (Head,String) = connection.query_row(
        "SELECT through_sequence,records_count,edges_count,gaps_count,edges_digest,gaps_digest,archived_prefix_unresolved,head_digest FROM retention_reference_head WHERE singleton=1",
        [],
        |row| Ok((Head { through:row.get(0)?,records:row.get(1)?,edges:row.get(2)?,gaps:row.get(3)?,edges_digest:row.get(4)?,gaps_digest:row.get(5)?,archived_prefix_unresolved:row.get(6)? },row.get(7)?)),
    )?;
    if head_digest(&head)? != expected {
        return Err(invalid("original reference head checksum differs"));
    }
    Ok(head)
}

/// Reports original input/reference corruption without treating absence as permission to delete.
fn invalid(message: &str) -> SqliteEventLogError {
    SqliteEventLogError::ArchivePolicy(format!("retention references: {message}"))
}

/// Uses complete typed State identity, including source, epoch, sequence and original receive time.
fn record_identity(record: &domain::StateRecord) -> Result<String, SqliteEventLogError> {
    let data = serde_json::to_vec(record).map_err(|error| invalid(&error.to_string()))?;
    Ok(format!("sha256:{:x}", Sha256::digest(data)))
}

/// Creates only empty original authority within the explicit exclusive storage migration.
pub(super) fn initialize(connection: &Connection) -> Result<(), SqliteEventLogError> {
    if connection.is_autocommit() {
        return Err(invalid(
            "initialization requires the original SQL transaction",
        ));
    }
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
                .map_err(|_| invalid("invalid original archive floor"))
        })
        .transpose()?
        .unwrap_or(0);
    connection.execute_batch(
        "CREATE TABLE retention_reference_head(singleton INTEGER PRIMARY KEY CHECK(singleton=1),through_sequence INTEGER NOT NULL,records_count INTEGER NOT NULL,edges_count INTEGER NOT NULL,gaps_count INTEGER NOT NULL,edges_digest TEXT NOT NULL,gaps_digest TEXT NOT NULL,archived_prefix_unresolved INTEGER NOT NULL,head_digest TEXT NOT NULL);
         CREATE TABLE retention_reference_records(source_sequence INTEGER PRIMARY KEY NOT NULL,record_digest TEXT NOT NULL);
         CREATE INDEX retention_reference_record_identity ON retention_reference_records(record_digest,source_sequence);
         CREATE TABLE retention_reference_edges(decision_sequence INTEGER NOT NULL,source_sequence INTEGER NOT NULL,record_digest TEXT NOT NULL,PRIMARY KEY(decision_sequence,source_sequence));
         CREATE INDEX retention_reference_source ON retention_reference_edges(source_sequence,decision_sequence);
         CREATE TABLE retention_reference_gaps(decision_sequence INTEGER NOT NULL,record_digest TEXT NOT NULL,PRIMARY KEY(decision_sequence,record_digest));",
    )?;
    save_head(
        connection,
        &Head {
            through: floor,
            records: 0,
            edges: 0,
            gaps: 0,
            edges_digest: empty_digest("roboguide.retention-decision-edges/v0.1"),
            gaps_digest: empty_digest("roboguide.retention-decision-gaps/v0.1"),
            archived_prefix_unresolved: floor > 0,
        },
    )?;
    Ok(())
}

/// Reads the required original index coverage without silently recreating missing authority.
fn through_sequence(connection: &Connection) -> Result<u64, SqliteEventLogError> {
    Ok(read_head(connection)?.through)
}

/// Records exact observation identities and protected decision dependencies in the event transaction.
pub(super) fn observe(
    connection: &Connection,
    event: &PersistedEvent,
) -> Result<(), SqliteEventLogError> {
    if connection.is_autocommit() {
        return Err(invalid(
            "reference append requires the original SQL transaction",
        ));
    }
    let mut head = read_head(connection)?;
    if head.through.checked_add(1) != Some(event.sequence) {
        return Err(invalid("original reference coverage is not contiguous"));
    }
    let payload: EventPayload =
        serde_json::from_str(&event.payload_json).map_err(|error| invalid(&error.to_string()))?;
    match payload {
        EventPayload::StateRecordObserved { record } => {
            connection.execute(
                "INSERT INTO retention_reference_records VALUES(?1,?2)",
                params![event.sequence, record_identity(&record)?],
            )?;
            head.records += 1;
        }
        EventPayload::StatePolicyDecision { receipts, .. } => {
            for receipt in receipts {
                let Some(record) = receipt.record else {
                    continue;
                };
                let identity = record_identity(&record)?;
                let mut statement = connection.prepare(
                    "SELECT source_sequence FROM retention_reference_records WHERE record_digest=?1 AND source_sequence<?2 ORDER BY source_sequence",
                )?;
                let mut changed = 0;
                for source in statement.query_map(params![identity, event.sequence], |row| {
                    row.get::<_, u64>(0)
                })? {
                    let source = source?;
                    let original=connection.query_row(
                        "SELECT sequence,event_id,timestamp_ms,correlation_id,causation_id,payload_schema,payload_json FROM events WHERE sequence=?1",
                        [source],event_from_row,
                    ).optional()?;
                    let Some(original) = original else {
                        // SQL-only admission cannot attest an already archived selected source.
                        connection.execute(
                            "INSERT OR IGNORE INTO retention_reference_gaps VALUES(?1,?2)",
                            params![event.sequence, identity],
                        )?;
                        continue;
                    };
                    validate_persisted_event(&original)?;
                    let original: EventPayload = serde_json::from_str(&original.payload_json)
                        .map_err(|error| invalid(&error.to_string()))?;
                    let EventPayload::StateRecordObserved {
                        record: source_record,
                    } = original
                    else {
                        return Err(invalid(
                            "source identity index names a non-observation event",
                        ));
                    };
                    if source_record != record || record_identity(&source_record)? != identity {
                        return Err(invalid(
                            "source identity index differs from original observation",
                        ));
                    }
                    changed += connection.execute(
                        "INSERT OR IGNORE INTO retention_reference_edges VALUES(?1,?2,?3)",
                        params![event.sequence, source, identity],
                    )?;
                }
                if changed == 0 {
                    // Duplicate receipt entries may already name the same exact original input.
                    let known: bool = connection.query_row(
                        "SELECT EXISTS(SELECT 1 FROM retention_reference_edges WHERE decision_sequence=?1 AND record_digest=?2)",
                        params![event.sequence, identity],
                        |row| row.get(0),
                    )?;
                    if !known {
                        connection.execute(
                            "INSERT OR IGNORE INTO retention_reference_gaps VALUES(?1,?2)",
                            params![event.sequence, identity],
                        )?;
                    }
                }
            }
        }
        _ => {}
    }
    let mut statement = connection.prepare("SELECT source_sequence,record_digest FROM retention_reference_edges WHERE decision_sequence=?1 ORDER BY source_sequence")?;
    for row in statement.query_map([event.sequence], |row| {
        Ok((row.get::<_, u64>(0)?, row.get::<_, String>(1)?))
    })? {
        let (source, digest) = row?;
        let data = serde_json::to_string(&(event.sequence, source, digest))
            .map_err(|error| invalid(&error.to_string()))?;
        head.edges_digest = extend(&head.edges_digest, &data);
        head.edges += 1;
    }
    let mut statement = connection.prepare("SELECT record_digest FROM retention_reference_gaps WHERE decision_sequence=?1 ORDER BY record_digest")?;
    for row in statement.query_map([event.sequence], |row| row.get::<_, String>(0))? {
        let data = serde_json::to_string(&(event.sequence, row?))
            .map_err(|error| invalid(&error.to_string()))?;
        head.gaps_digest = extend(&head.gaps_digest, &data);
        head.gaps += 1;
    }
    head.through = event.sequence;
    save_head(connection, &head)?;
    Ok(())
}

/// Checks only derived-index schema and committed coverage; never grants GC or claims a full scrub.
pub(super) fn validate_head(
    connection: &Connection,
    latest_sequence: u64,
) -> Result<(), SqliteEventLogError> {
    if through_sequence(connection)? != latest_sequence {
        return Err(invalid("reference coverage differs from original events"));
    }
    for name in [
        "retention_reference_records",
        "retention_reference_edges",
        "retention_reference_gaps",
    ] {
        let exists: bool = connection.query_row(
            "SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE name=?1 AND type='table')",
            [name],
            |row| row.get(0),
        )?;
        if !exists {
            return Err(invalid("required original reference table is missing"));
        }
    }
    for (table, required) in [
        (
            "retention_reference_records",
            &["source_sequence", "record_digest"][..],
        ),
        (
            "retention_reference_edges",
            &["decision_sequence", "source_sequence", "record_digest"][..],
        ),
        (
            "retention_reference_gaps",
            &["decision_sequence", "record_digest"][..],
        ),
    ] {
        let columns = super::table_columns(connection, table)?;
        if columns.len() != required.len() || required.iter().any(|name| !columns.contains(*name)) {
            return Err(invalid("derived reference index columns differ"));
        }
    }
    Ok(())
}

/// Fully audits derived rows before GC proofs, public reference answers and explicit cold audits.
pub(super) fn validate(
    connection: &Connection,
    latest_sequence: u64,
) -> Result<(), SqliteEventLogError> {
    validate_head(connection, latest_sequence)?;
    let head = read_head(connection)?;
    let counts: (u64,u64,u64) = connection.query_row(
        "SELECT (SELECT COUNT(*) FROM retention_reference_records),(SELECT COUNT(*) FROM retention_reference_edges),(SELECT COUNT(*) FROM retention_reference_gaps)",
        [], |row| Ok((row.get(0)?,row.get(1)?,row.get(2)?)),
    )?;
    if counts != (head.records, head.edges, head.gaps) {
        return Err(invalid("original reference rows are missing or replaced"));
    }
    let bad_edge: bool = connection.query_row(
        "SELECT EXISTS(SELECT 1 FROM retention_reference_edges e LEFT JOIN retention_reference_records r ON r.source_sequence=e.source_sequence WHERE r.source_sequence IS NULL OR r.record_digest!=e.record_digest OR e.source_sequence>=e.decision_sequence)",
        [], |row| row.get(0),
    )?;
    if bad_edge {
        return Err(invalid("original decision source identity differs"));
    }
    let mut edges_digest = empty_digest("roboguide.retention-decision-edges/v0.1");
    let mut statement = connection.prepare("SELECT decision_sequence,source_sequence,record_digest FROM retention_reference_edges ORDER BY decision_sequence,source_sequence")?;
    for row in statement.query_map([], |row| {
        Ok((
            row.get::<_, u64>(0)?,
            row.get::<_, u64>(1)?,
            row.get::<_, String>(2)?,
        ))
    })? {
        edges_digest = extend(
            &edges_digest,
            &serde_json::to_string(&row?).map_err(|error| invalid(&error.to_string()))?,
        );
    }
    let mut gaps_digest = empty_digest("roboguide.retention-decision-gaps/v0.1");
    let mut statement = connection.prepare("SELECT decision_sequence,record_digest FROM retention_reference_gaps ORDER BY decision_sequence,record_digest")?;
    for row in statement.query_map([], |row| {
        Ok((row.get::<_, u64>(0)?, row.get::<_, String>(1)?))
    })? {
        gaps_digest = extend(
            &gaps_digest,
            &serde_json::to_string(&row?).map_err(|error| invalid(&error.to_string()))?,
        );
    }
    if edges_digest != head.edges_digest || gaps_digest != head.gaps_digest {
        return Err(invalid("original protected dependency checksum differs"));
    }
    Ok(())
}

/// Reads only source pins for one bounded range; it does not claim coverage of other reference kinds.
pub(super) fn decision_references(
    connection: &Connection,
    start_sequence: u64,
    end_sequence: u64,
) -> Result<RetentionDecisionReferences, SqliteEventLogError> {
    if start_sequence == 0
        || end_sequence < start_sequence
        || end_sequence - start_sequence >= MAX_ARCHIVE_EVENTS
    {
        return Err(invalid(
            "requested reference range exceeds one bounded batch",
        ));
    }
    let through = through_sequence(connection)?;
    validate(connection, through)?;
    if through < end_sequence {
        return Err(invalid("requested range exceeds indexed original coverage"));
    }
    let mut statement = connection.prepare(
        "SELECT source_sequence,MIN(decision_sequence) FROM retention_reference_edges WHERE source_sequence>=?1 AND source_sequence<=?2 GROUP BY source_sequence ORDER BY source_sequence",
    )?;
    let pins = statement
        .query_map(params![start_sequence, end_sequence], |row| {
            let source: u64 = row.get(0)?;
            let owner: u64 = row.get(1)?;
            Ok(RetentionPin {
                reference_id: format!("original-decision-event:{owner}"),
                start_sequence: source,
                end_sequence: source,
            })
        })?
        .collect::<Result<Vec<_>, _>>()?;
    let mut statement = connection.prepare(
        "SELECT DISTINCT decision_sequence FROM retention_reference_gaps ORDER BY decision_sequence LIMIT 129",
    )?;
    let mut unresolved_decision_sequences = statement
        .query_map([], |row| row.get::<_, u64>(0))?
        .collect::<Result<Vec<_>, _>>()?;
    let more_unresolved = unresolved_decision_sequences.len() > 128;
    unresolved_decision_sequences.truncate(128);
    Ok(RetentionDecisionReferences {
        indexed_through_sequence: through,
        pins,
        unresolved_decision_sequences,
        more_unresolved,
        archived_prefix_unresolved: read_head(connection)?.archived_prefix_unresolved,
    })
}

#[cfg(test)]
#[path = "retention_references_tests.rs"]
mod tests;
