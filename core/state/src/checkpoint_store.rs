//! Opaque, bounded byte deltas reconstruct exact checkpoints without interpreting authority.

use super::*;
use base64::{Engine as _, engine::general_purpose::STANDARD};
use serde::{Deserialize, Serialize};
use sha2::{Digest as _, Sha256};
use std::collections::BTreeMap;

/// Downgrade fence in the historical baseline table, including zero-event revisions.
const CHAIN_SCHEMA: &str = "roboguide.checkpoint-chain/v1";
/// Exact byte-copy/literal codec supported by this storage implementation.
const DELTA_SCHEMA: &str = "roboguide.checkpoint-byte-delta/v1";
/// Maximum encoded authority snapshot accepted by the incremental store.
const MAX_BYTES: usize = 64 * 1024 * 1024;
/// Maximum retained deltas between full atomic compactions.
const MAX_DELTAS: usize = 32;
/// Aligned baseline block size; new-byte windows may start at any offset.
const BLOCK: usize = 1024;
/// Rolling fingerprint multiplier; collisions never authorize a Copy.
const PRIME: u64 = 257;

/// Selects storage representation without changing the complete checkpoint or its authority.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub enum CheckpointStoragePolicy {
    /// Compatibility representation with a full checkpoint per transaction.
    #[default]
    Full,
    /// Bounded exact delta chain with atomic compaction and a downgrade fence.
    Incremental,
}

/// Actual representation bytes written by the most recent checkpoint save.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct CheckpointWriteStats {
    /// UTF-8 baseline or delta bytes, excluding SQLite row/page overhead.
    pub encoded_bytes: u64,
    /// Whether the save appended a verified delta rather than a complete baseline.
    pub delta: bool,
}

/// Cache of the last complete committed or transaction-pending authority representation.
#[derive(Clone)]
pub(super) struct StoredCheckpoint {
    /// Complete result reconstructed from the exact storage chain.
    pub(super) snapshot: Arc<PersistedCheckpoint>,
    /// Current durable chain revision; plain compatibility storage uses zero.
    revision: u64,
    /// Revision of the retained complete baseline.
    base_revision: u64,
    /// Digest binding the retained baseline to schema, event tail and revision.
    base_digest: String,
    /// Exact digest of the cached current result; it is computed once and never inferred from a fingerprint.
    digest: String,
    /// Actual baseline bytes used to bound cumulative delta work.
    base_bytes: usize,
    /// Number of retained delta rows.
    delta_rows: usize,
    /// Total retained encoded delta bytes.
    delta_bytes: usize,
    /// Whether the historical row already carries the chain downgrade fence.
    chain: bool,
    /// Last successful write observation, never an acceptance decision.
    pub(super) write_stats: CheckpointWriteStats,
}

/// Compact storage head binding one full baseline and its exact current result.
struct Head {
    /// Retained baseline revision.
    base_revision: u64,
    /// Current chain revision.
    revision: u64,
    /// Current covered event tail.
    event_sequence: u64,
    /// Actual application checkpoint schema.
    schema: String,
    /// Digest of the retained full baseline.
    base_digest: String,
    /// Digest of the current complete result.
    digest: String,
}

/// Exact reconstruction program; fingerprints are deliberately absent from durable authority.
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct ByteDelta {
    /// Storage codec identity.
    codec: String,
    /// Exact expected output byte length.
    length: usize,
    /// Bounded ordered copies and literal bytes.
    pieces: Vec<Piece>,
}

/// One bounded part of an opaque byte reconstruction.
#[derive(Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "kebab-case", deny_unknown_fields)]
enum Piece {
    /// Copy exact bytes from the previous complete snapshot.
    Copy {
        /// Beginning in the previous snapshot.
        offset: usize,
        /// Number of exact bytes.
        length: usize,
    },
    /// Literal bytes encoded independently of UTF-8 character boundaries.
    Literal {
        /// Canonical base64 byte content.
        bytes: String,
    },
}

/// Creates storage tables without migrating or accepting any checkpoint.
pub(super) fn initialize(connection: &Connection) -> Result<(), SqliteEventLogError> {
    connection.execute_batch(
        "CREATE TABLE IF NOT EXISTS controller_checkpoint_head (
            singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
            base_revision INTEGER NOT NULL,
            revision INTEGER NOT NULL,
            event_sequence INTEGER NOT NULL,
            schema TEXT NOT NULL,
            base_digest TEXT NOT NULL,
            digest TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS controller_checkpoint_deltas (
            revision INTEGER PRIMARY KEY,
            previous_revision INTEGER NOT NULL,
            event_sequence INTEGER NOT NULL,
            schema TEXT NOT NULL,
            base_digest TEXT NOT NULL,
            digest TEXT NOT NULL,
            delta_json TEXT NOT NULL
        );",
    )?;
    Ok(())
}

/// Computes a length-delimited digest of all checkpoint chain identity fields and exact bytes.
fn digest(schema: &str, sequence: u64, revision: u64, bytes: &[u8]) -> String {
    let mut hash = Sha256::new();
    hash.update(CHAIN_SCHEMA.as_bytes());
    hash.update((schema.len() as u64).to_le_bytes());
    hash.update(schema.as_bytes());
    hash.update(sequence.to_le_bytes());
    hash.update(revision.to_le_bytes());
    hash.update((bytes.len() as u64).to_le_bytes());
    hash.update(bytes);
    format!("{:x}", hash.finalize())
}

/// Rejects unbounded snapshots or an application schema trying to impersonate the storage codec.
fn validate_snapshot(schema: &str, bytes: &[u8]) -> Result<(), SqliteEventLogError> {
    if schema.trim().is_empty()
        || schema.len() > 512
        || schema == CHAIN_SCHEMA
        || bytes.len() > MAX_BYTES
    {
        return Err(SqliteEventLogError::Codec(
            "invalid or oversized checkpoint representation".into(),
        ));
    }
    Ok(())
}

/// Returns whether a readonly legacy database already knows the additive storage tables.
fn has_head_table(connection: &Connection) -> Result<bool, SqliteEventLogError> {
    Ok(connection.query_row(
        "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'controller_checkpoint_head'",
        [], |row| row.get::<_, u64>(0),
    )? != 0)
}

/// Reconstructs and checks every durable identity, output bound and digest before returning authority.
pub(super) fn read(
    connection: &Connection,
) -> Result<Option<StoredCheckpoint>, SqliteEventLogError> {
    let baseline = connection.query_row(
        "SELECT event_sequence, schema, checkpoint_json FROM controller_checkpoint WHERE singleton = 1",
        [], |row| Ok(PersistedCheckpoint { event_sequence: row.get(0)?, schema: row.get(1)?, checkpoint_json: row.get(2)? }),
    ).optional()?;
    let has_tables = has_head_table(connection)?;
    let head = if has_tables {
        connection
            .query_row(
                "SELECT base_revision, revision, event_sequence, schema, base_digest, digest
             FROM controller_checkpoint_head WHERE singleton = 1",
                [],
                |row| {
                    Ok(Head {
                        base_revision: row.get(0)?,
                        revision: row.get(1)?,
                        event_sequence: row.get(2)?,
                        schema: row.get(3)?,
                        base_digest: row.get(4)?,
                        digest: row.get(5)?,
                    })
                },
            )
            .optional()?
    } else {
        None
    };
    let (count, retained_bytes, max_delta_bytes): (usize, usize, usize) = if has_tables {
        connection.query_row(
            "SELECT COUNT(*), COALESCE(SUM(length(CAST(delta_json AS BLOB))), 0),
            COALESCE(MAX(length(CAST(delta_json AS BLOB))), 0) FROM controller_checkpoint_deltas",
            [],
            |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?)),
        )?
    } else {
        (0, 0, 0)
    };
    let Some(mut baseline) = baseline else {
        if head.is_some() || count != 0 {
            return Err(SqliteEventLogError::Codec(
                "checkpoint chain lacks a full baseline".into(),
            ));
        }
        return Ok(None);
    };
    if baseline.schema != CHAIN_SCHEMA {
        if head.is_some() || count != 0 {
            return Err(SqliteEventLogError::Codec(
                "plain checkpoint has hidden chain authority".into(),
            ));
        }
        validate_snapshot(&baseline.schema, baseline.checkpoint_json.as_bytes())?;
        let baseline_digest = digest(
            &baseline.schema,
            baseline.event_sequence,
            0,
            baseline.checkpoint_json.as_bytes(),
        );
        return Ok(Some(StoredCheckpoint {
            base_digest: baseline_digest.clone(),
            digest: baseline_digest,
            base_bytes: baseline.checkpoint_json.len(),
            snapshot: Arc::new(baseline),
            revision: 0,
            base_revision: 0,
            delta_rows: 0,
            delta_bytes: 0,
            chain: false,
            write_stats: CheckpointWriteStats::default(),
        }));
    }
    let head =
        head.ok_or_else(|| SqliteEventLogError::Codec("checkpoint chain lacks its head".into()))?;
    validate_snapshot(&head.schema, baseline.checkpoint_json.as_bytes())?;
    if count > MAX_DELTAS
        || retained_bytes > MAX_BYTES * 2
        || max_delta_bytes > MAX_BYTES * 2
        || head.revision.checked_sub(head.base_revision) != Some(count as u64)
    {
        return Err(SqliteEventLogError::Codec(
            "checkpoint chain exceeds bounds or has missing revisions".into(),
        ));
    }
    baseline.schema = head.schema.clone();
    let base_bytes = baseline.checkpoint_json.len();
    let base_digest = digest(
        &baseline.schema,
        baseline.event_sequence,
        head.base_revision,
        baseline.checkpoint_json.as_bytes(),
    );
    if base_digest != head.base_digest {
        return Err(SqliteEventLogError::Codec(
            "checkpoint baseline digest mismatch".into(),
        ));
    }
    let mut current_digest = base_digest.clone();
    let mut revision = head.base_revision;
    let mut statement = connection.prepare(
        "SELECT revision, previous_revision, event_sequence, schema, base_digest, digest, delta_json
         FROM controller_checkpoint_deltas ORDER BY revision",
    )?;
    let mut rows = statement.query([])?;
    while let Some(row) = rows.next()? {
        let next_revision: u64 = row.get(0)?;
        let previous_revision: u64 = row.get(1)?;
        let sequence: u64 = row.get(2)?;
        let schema: String = row.get(3)?;
        let previous_digest: String = row.get(4)?;
        let target_digest: String = row.get(5)?;
        if revision.checked_add(1) != Some(next_revision)
            || previous_revision != revision
            || sequence < baseline.event_sequence
            || schema != head.schema
            || previous_digest != current_digest
        {
            return Err(SqliteEventLogError::Codec(
                "checkpoint delta identity/order mismatch".into(),
            ));
        }
        let encoded: String = row.get(6)?;
        let delta: ByteDelta = serde_json::from_str(&encoded)
            .map_err(|error| SqliteEventLogError::Codec(error.to_string()))?;
        let bytes = reconstruct(&delta, baseline.checkpoint_json.as_bytes())?;
        current_digest = digest(&schema, sequence, next_revision, &bytes);
        if current_digest != target_digest {
            return Err(SqliteEventLogError::Codec(
                "checkpoint delta result digest mismatch".into(),
            ));
        }
        baseline.checkpoint_json = String::from_utf8(bytes)
            .map_err(|error| SqliteEventLogError::Codec(error.to_string()))?;
        baseline.event_sequence = sequence;
        revision = next_revision;
    }
    if revision != head.revision
        || current_digest != head.digest
        || baseline.event_sequence != head.event_sequence
    {
        return Err(SqliteEventLogError::Codec(
            "checkpoint head differs from reconstructed authority".into(),
        ));
    }
    Ok(Some(StoredCheckpoint {
        snapshot: Arc::new(baseline),
        revision,
        base_revision: head.base_revision,
        base_digest,
        digest: current_digest,
        base_bytes,
        delta_rows: count,
        delta_bytes: retained_bytes,
        chain: true,
        write_stats: CheckpointWriteStats::default(),
    }))
}

/// Writes an exact full snapshot or beneficial delta inside the caller's existing SQLite transaction.
pub(super) fn write(
    connection: &Connection,
    previous: Option<&StoredCheckpoint>,
    policy: CheckpointStoragePolicy,
    schema: &str,
    json: &str,
    sequence: u64,
) -> Result<StoredCheckpoint, SqliteEventLogError> {
    validate_snapshot(schema, json.as_bytes())?;
    if previous.is_some_and(|previous| sequence < previous.snapshot.event_sequence) {
        return Err(SqliteEventLogError::Codec(
            "checkpoint event tail regressed".into(),
        ));
    }
    let chain = policy == CheckpointStoragePolicy::Incremental
        || previous.is_some_and(|previous| previous.chain);
    let revision = if chain {
        previous
            .map_or(Some(1), |previous| previous.revision.checked_add(1))
            .ok_or_else(|| SqliteEventLogError::Codec("checkpoint revision exhausted".into()))?
    } else {
        0
    };
    let current_digest = if chain {
        digest(schema, sequence, revision, json.as_bytes())
    } else {
        String::new()
    };
    let snapshot = Arc::new(PersistedCheckpoint {
        schema: schema.into(),
        checkpoint_json: json.into(),
        event_sequence: sequence,
    });
    let delta = if chain && policy == CheckpointStoragePolicy::Incremental {
        previous
            .filter(|previous| {
                previous.snapshot.schema == schema && previous.delta_rows < MAX_DELTAS
            })
            .map(|previous| {
                encode_delta(
                    previous.snapshot.checkpoint_json.as_bytes(),
                    json.as_bytes(),
                )
            })
            .transpose()?
            .filter(|encoded| {
                encoded.len() < json.len().saturating_mul(3) / 4
                    && previous.is_some_and(|previous| {
                        previous.delta_bytes.saturating_add(encoded.len()) < previous.base_bytes
                    })
            })
    } else {
        None
    };
    if let (Some(previous), Some(encoded)) = (previous, delta) {
        if !previous.chain {
            connection.execute(
                "UPDATE controller_checkpoint SET schema = ?1 WHERE singleton = 1",
                [CHAIN_SCHEMA],
            )?;
        }
        let previous_digest = previous.digest.clone();
        connection.execute("INSERT INTO controller_checkpoint_deltas(revision, previous_revision, event_sequence, schema, base_digest, digest, delta_json)
            VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7)",
            params![revision, previous.revision, sequence, schema, previous_digest, current_digest, encoded])?;
        save_head(
            connection,
            &Head {
                base_revision: previous.base_revision,
                revision,
                event_sequence: sequence,
                schema: schema.into(),
                base_digest: previous.base_digest.clone(),
                digest: current_digest.clone(),
            },
        )?;
        return Ok(StoredCheckpoint {
            snapshot,
            revision,
            base_revision: previous.base_revision,
            base_digest: previous.base_digest.clone(),
            digest: current_digest,
            base_bytes: previous.base_bytes,
            delta_rows: previous.delta_rows + 1,
            delta_bytes: previous.delta_bytes + encoded.len(),
            chain,
            write_stats: CheckpointWriteStats {
                encoded_bytes: encoded.len() as u64,
                delta: true,
            },
        });
    }
    connection.execute("INSERT INTO controller_checkpoint(singleton, event_sequence, schema, checkpoint_json)
        VALUES (1, ?1, ?2, ?3) ON CONFLICT(singleton) DO UPDATE SET
        event_sequence = excluded.event_sequence, schema = excluded.schema, checkpoint_json = excluded.checkpoint_json",
        params![sequence, if chain { CHAIN_SCHEMA } else { schema }, json])?;
    connection.execute("DELETE FROM controller_checkpoint_deltas", [])?;
    if chain {
        save_head(
            connection,
            &Head {
                base_revision: revision,
                revision,
                event_sequence: sequence,
                schema: schema.into(),
                base_digest: current_digest.clone(),
                digest: current_digest.clone(),
            },
        )?;
    } else {
        connection.execute("DELETE FROM controller_checkpoint_head", [])?;
    }
    Ok(StoredCheckpoint {
        snapshot,
        revision,
        base_revision: revision,
        base_digest: current_digest.clone(),
        digest: current_digest,
        base_bytes: json.len(),
        delta_rows: 0,
        delta_bytes: 0,
        chain,
        write_stats: CheckpointWriteStats {
            encoded_bytes: json.len() as u64,
            delta: false,
        },
    })
}

/// Atomically replaces the storage head inside the same outer transaction as evidence and bytes.
fn save_head(connection: &Connection, head: &Head) -> Result<(), SqliteEventLogError> {
    connection.execute("INSERT INTO controller_checkpoint_head(singleton, base_revision, revision, event_sequence, schema, base_digest, digest)
        VALUES (1, ?1, ?2, ?3, ?4, ?5, ?6) ON CONFLICT(singleton) DO UPDATE SET
        base_revision = excluded.base_revision, revision = excluded.revision, event_sequence = excluded.event_sequence,
        schema = excluded.schema, base_digest = excluded.base_digest, digest = excluded.digest",
        params![head.base_revision, head.revision, head.event_sequence, head.schema, head.base_digest, head.digest])?;
    Ok(())
}

/// Computes a bounded rolling fingerprint, always followed by exact byte equality on a hit.
fn fingerprint(bytes: &[u8]) -> u64 {
    bytes.iter().fold(0_u64, |hash, byte| {
        hash.wrapping_mul(PRIME).wrapping_add(u64::from(*byte) + 1)
    })
}

/// Emits pending exact literals without assuming Copy boundaries align to UTF-8 characters.
fn flush_literal(pieces: &mut Vec<Piece>, pending: &mut Vec<u8>) {
    if !pending.is_empty() {
        pieces.push(Piece::Literal {
            bytes: STANDARD.encode(&*pending),
        });
        pending.clear();
    }
}

/// Builds a deterministic linear scan with at most four exact comparisons per fingerprint bucket.
fn encode_delta(previous: &[u8], target: &[u8]) -> Result<String, SqliteEventLogError> {
    let prefix = previous
        .iter()
        .zip(target)
        .take_while(|(left, right)| left == right)
        .count();
    let suffix = previous[prefix..]
        .iter()
        .rev()
        .zip(target[prefix..].iter().rev())
        .take_while(|(left, right)| left == right)
        .count();
    let middle = &target[prefix..target.len() - suffix];
    let mut index: BTreeMap<u64, Vec<usize>> = BTreeMap::new();
    for (number, bytes) in previous[prefix..previous.len() - suffix]
        .chunks_exact(BLOCK)
        .enumerate()
    {
        let bucket = index.entry(fingerprint(bytes)).or_default();
        if bucket.len() < 4 {
            bucket.push(prefix + number * BLOCK);
        }
    }
    let power = (0..BLOCK - 1).fold(1_u64, |power, _| power.wrapping_mul(PRIME));
    let mut pieces = Vec::new();
    if prefix != 0 {
        pieces.push(Piece::Copy {
            offset: 0,
            length: prefix,
        });
    }
    let mut pending = Vec::new();
    let mut offset = 0;
    let mut hash = middle.get(..BLOCK).map(fingerprint);
    while offset < middle.len() {
        let found = hash
            .and_then(|hash| index.get(&hash))
            .and_then(|candidates| {
                candidates.iter().copied().find(|candidate| {
                    previous[*candidate..*candidate + BLOCK] == middle[offset..offset + BLOCK]
                })
            });
        if let Some(source) = found {
            flush_literal(&mut pieces, &mut pending);
            match pieces.last_mut() {
                Some(Piece::Copy { offset, length }) if *offset + *length == source => {
                    *length += BLOCK
                }
                _ => pieces.push(Piece::Copy {
                    offset: source,
                    length: BLOCK,
                }),
            }
            offset += BLOCK;
            hash = middle
                .get(offset..)
                .and_then(|bytes| bytes.get(..BLOCK))
                .map(fingerprint);
        } else {
            pending.push(middle[offset]);
            hash = hash.zip(middle.get(offset + BLOCK)).map(|(hash, byte)| {
                hash.wrapping_sub((u64::from(middle[offset]) + 1).wrapping_mul(power))
                    .wrapping_mul(PRIME)
                    .wrapping_add(u64::from(*byte) + 1)
            });
            offset += 1;
        }
    }
    flush_literal(&mut pieces, &mut pending);
    if suffix != 0 {
        pieces.push(Piece::Copy {
            offset: previous.len() - suffix,
            length: suffix,
        });
    }
    serde_json::to_string(&ByteDelta {
        codec: DELTA_SCHEMA.into(),
        length: target.len(),
        pieces,
    })
    .map_err(|error| SqliteEventLogError::Codec(error.to_string()))
}

/// Reconstructs bounded bytes and rejects invalid codecs, Copy ranges, encodings or output lengths.
fn reconstruct(delta: &ByteDelta, previous: &[u8]) -> Result<Vec<u8>, SqliteEventLogError> {
    if delta.codec != DELTA_SCHEMA
        || delta.length > MAX_BYTES
        || delta.pieces.len() > 2 * MAX_BYTES / BLOCK + 1
    {
        return Err(SqliteEventLogError::Codec(
            "invalid or unbounded checkpoint delta".into(),
        ));
    }
    let mut output = Vec::with_capacity(delta.length);
    for piece in &delta.pieces {
        match piece {
            Piece::Copy { offset, length } => {
                let end = offset
                    .checked_add(*length)
                    .ok_or_else(|| SqliteEventLogError::Codec("checkpoint Copy overflow".into()))?;
                let bytes = previous.get(*offset..end).ok_or_else(|| {
                    SqliteEventLogError::Codec("checkpoint Copy outside baseline".into())
                })?;
                if bytes.len() > delta.length.saturating_sub(output.len()) {
                    return Err(SqliteEventLogError::Codec(
                        "checkpoint Copy exceeds result length".into(),
                    ));
                }
                output.extend_from_slice(bytes);
            }
            Piece::Literal { bytes } => {
                if bytes.len() > delta.length.saturating_sub(output.len()).div_ceil(3) * 4 {
                    return Err(SqliteEventLogError::Codec(
                        "checkpoint literal exceeds result bound".into(),
                    ));
                }
                let bytes = STANDARD
                    .decode(bytes)
                    .map_err(|error| SqliteEventLogError::Codec(error.to_string()))?;
                if bytes.len() > delta.length.saturating_sub(output.len()) {
                    return Err(SqliteEventLogError::Codec(
                        "checkpoint literal exceeds result length".into(),
                    ));
                }
                output.extend_from_slice(&bytes);
            }
        }
    }
    if output.len() != delta.length {
        return Err(SqliteEventLogError::Codec(
            "checkpoint reconstructed length mismatch".into(),
        ));
    }
    Ok(output)
}

#[cfg(test)]
#[path = "checkpoint_store_tests.rs"]
mod tests;
