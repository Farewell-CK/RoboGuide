//! Root8 reclamation of retired private operation names, independent of finalized CAS authority.

use super::*;
use rusqlite::OptionalExtension;
use sha2::{Digest as _, Sha256};
use std::collections::BTreeSet;
use std::io::Read;
use std::os::unix::fs::MetadataExt;

/// Exact original operation selector; callers never supply a path, inode or asserted stop proof.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "kebab-case", deny_unknown_fields)]
pub enum ReplicaStagingTarget {
    /// Original synchronous source copy and its independently released receive pin.
    Copy {
        /// Original immutable copy command ID.
        command_id: String,
    },
    /// Original stream with complete retained close/end retirement.
    Stream {
        /// Original immutable stream command ID.
        command_id: String,
    },
}

/// Provider-derived original source and release evidence; a pathname alone supplies no ownership.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Ownership {
    /// Complete original qualified input, not the digest of a partial staging file.
    pub(super) content: ArtifactContentKey,
    /// Canonical immutable original intent/result or actual end evidence.
    pub(super) source_digest: String,
    /// Exact released reference or complete original stream-retirement digest.
    pub(super) release_digest: String,
    /// Private original operation leaf, derived only by its original provider module.
    pub(super) leaf: String,
}

/// Actual first observed file; absence is represented separately and never proves an earlier unlink.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct FileIdentity {
    /// Actual filesystem device, independent of any robot/device identity.
    device: u64,
    /// Actual original selected inode.
    inode: u64,
    /// Actual partial/full staging length, bounded by its original input.
    byte_size: u64,
}

/// One sorted immutable selection under the actual original Root writer.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Item {
    /// Exact original selector, never a new import/copy command.
    target: ReplicaStagingTarget,
    /// Actual complete source and retirement lineage.
    ownership: Ownership,
    /// Actual file at admission or actual NotFound.
    file: Option<FileIdentity>,
}

/// Complete first command, committed with all initially Unknown outcomes before payload effects.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Intent {
    /// Closed local interpretation, fenced by explicit Root8.
    schema: String,
    /// Original caller ID; replays cannot produce another effect.
    command_id: String,
    /// Original logical Root identity, preserved separately from restored physical backing.
    receiver: ReplicaContentIdentity,
    /// Original admission/scan/effect/SQL clock, never renewed on observation.
    budget: ContentBudget,
    /// Complete bounded sorted source/file selection.
    items: Vec<Item>,
}

/// One actual original effect or independent original absence, never reconstructed after failure.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "state", rename_all = "kebab-case", deny_unknown_fields)]
enum Outcome {
    /// Actual complete hash/unlink/directory fsync under the first command budget.
    Deleted {
        /// Actual first completion sample.
        observed_ms: u64,
        /// Actual staging bytes, which need not equal the full original CAS digest.
        observed_digest: String,
    },
    /// Actual original absence; it does not fill an older command's Unknown.
    Missing {
        /// Actual first absence sample.
        observed_ms: u64,
    },
}

/// Small independently updated outcome row, avoiding whole-intent rewrites for each effect.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct ItemRecord {
    /// Exact owning command SQL key.
    operation_id: String,
    /// Original zero-based position inside its immutable sorted intent.
    ordinal: usize,
    /// Complete canonical original intent, including clock and source/release evidence.
    intent_digest: String,
    /// Actual original durable outcome; None remains Unknown across restart and restore.
    outcome: Option<Outcome>,
}

/// Readonly original command view; it never rechecks a file or creates availability/use permission.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReplicaStagingGcStatus {
    /// Closed original observation interpretation.
    pub schema: String,
    /// Exact retained first command ID.
    pub command_id: String,
    /// Complete original intent and original per-item records.
    pub original: serde_json::Value,
    /// Actual original unlink/fsync receipts only.
    pub deleted: usize,
    /// Actual independent original absence observations.
    pub missing: usize,
    /// Items lacking their original durable outcome.
    pub unknown: usize,
    /// Complete first selection size.
    pub candidates: usize,
}

/// Creates both mandatory journals/inventories only during explicit fresh/drained Root8 admission.
pub(super) fn initialize(sql: &Connection) -> Result<(), String> {
    sql.execute_batch("CREATE TABLE content_staging_gc_commands(operation_id TEXT PRIMARY KEY,record TEXT NOT NULL); CREATE TABLE content_staging_gc_items(item_id TEXT PRIMARY KEY,record TEXT NOT NULL)").map_err(|error| error.to_string())?;
    for name in ["staging_gc_ids", "staging_gc_item_ids"] {
        layout::write_head(sql, name, &BTreeSet::new())?;
    }
    Ok(())
}

/// Validates bounded original identifiers without interpreting them as filesystem components.
fn command(value: &str) -> Result<(), String> {
    if value.trim().is_empty() || value.len() > 128 {
        return Err("staging reclamation requires a bounded original command ID".into());
    }
    Ok(())
}

/// Resolves exact actual retirement from the original provider, never from caller flags.
fn ownership(sql: &Connection, target: &ReplicaStagingTarget) -> Result<Ownership, String> {
    match target {
        ReplicaStagingTarget::Copy { command_id } => {
            command(command_id)?;
            super::copy::staging_ownership(sql, command_id)
        }
        ReplicaStagingTarget::Stream { command_id } => {
            command(command_id)?;
            super::streams::staging_ownership(sql, command_id)
        }
    }
}

/// Reads only the first bounded original command without treating lost SQL as empty authority.
fn intent(sql: &Connection, id: &str) -> Result<Option<Intent>, String> {
    let raw: Option<String> = sql.query_row(
        "SELECT CASE WHEN length(CAST(record AS BLOB))<=33554432 THEN record ELSE NULL END FROM content_staging_gc_commands WHERE operation_id=?1",
        [id], |row| row.get::<_, Option<String>>(0)?.ok_or(rusqlite::Error::InvalidQuery),
    ).optional().map_err(|error| error.to_string())?;
    raw.map(|raw| serde_json::from_str(&raw).map_err(|error| error.to_string()))
        .transpose()
}

/// Derives a small path-independent item key from its original command and position.
fn item_id(id: &str, ordinal: usize) -> Result<String, String> {
    copy::digest(&(id, ordinal))
}

/// Reads one mandatory original item without permitting absence or oversized replacement evidence.
fn item_record(sql: &Connection, id: &str) -> Result<ItemRecord, String> {
    let raw: String = sql.query_row(
        "SELECT CASE WHEN length(CAST(record AS BLOB))<=4096 THEN record ELSE NULL END FROM content_staging_gc_items WHERE item_id=?1",
        [id], |row| row.get::<_, Option<String>>(0)?.ok_or(rusqlite::Error::InvalidQuery),
    ).map_err(|error| error.to_string())?;
    serde_json::from_str(&raw).map_err(|error| error.to_string())
}

/// Checks exact immutable intent/lineage and original outcomes without sampling new clocks or files.
fn records(
    sql: &Connection,
    id: &str,
    value: &Intent,
    receiver: &ReplicaContentIdentity,
) -> Result<Vec<ItemRecord>, String> {
    command(&value.command_id)?;
    value.budget.validate()?;
    if value.schema != "roboguide.replica-staging-gc-intent/v0.1"
        || value.receiver != *receiver
        || copy::digest(&value.command_id)? != id
        || value.items.is_empty()
        || value.items.len() > 64
        || value
            .items
            .windows(2)
            .any(|pair| pair[0].target >= pair[1].target)
    {
        return Err("original staging GC identity/selection differs".into());
    }
    let expected_digest = copy::digest(value)?;
    let mut result = Vec::new();
    for (ordinal, selected) in value.items.iter().enumerate() {
        if selected.ownership != ownership(sql, &selected.target)?
            || selected.file.as_ref().is_some_and(|file| {
                file.inode == 0 || file.byte_size > selected.ownership.content.byte_size
            })
        {
            return Err("original staging GC lost exact retired source/file lineage".into());
        }
        let actual = item_record(sql, &item_id(id, ordinal)?)?;
        if actual.operation_id != id
            || actual.ordinal != ordinal
            || actual.intent_digest != expected_digest
        {
            return Err("original staging GC item changed command/position/intent".into());
        }
        if let Some(outcome) = &actual.outcome {
            let sample = match (outcome, &selected.file) {
                (
                    Outcome::Deleted {
                        observed_ms,
                        observed_digest,
                    },
                    Some(_),
                ) if observed_digest
                    .strip_prefix("sha256:")
                    .is_some_and(crate::content::hex_digest) =>
                {
                    *observed_ms
                }
                (Outcome::Missing { observed_ms }, None) => *observed_ms,
                _ => {
                    return Err("original staging GC outcome changed its selected file kind".into());
                }
            };
            if !value.budget.validates_completion(sample) {
                return Err("original staging GC outcome renewed its first budget".into());
            }
        }
        result.push(actual);
    }
    Ok(result)
}

/// Refuses lost inventories, orphan items and incorrect original retirement before any Root authority.
pub(super) fn validate_records(
    sql: &Connection,
    receiver: &ReplicaContentIdentity,
) -> Result<(), String> {
    let heads = layout::head(sql, "staging_gc_ids")?;
    let items = layout::head(sql, "staging_gc_item_ids")?;
    if heads.len() > 4096 || items.len() > 16384 {
        return Err("original staging GC history exceeds its fixed bound".into());
    }
    let mut expected = BTreeSet::new();
    for id in heads {
        let value = intent(sql, &id)?.ok_or("original staging GC command disappeared")?;
        records(sql, &id, &value, receiver)?;
        for ordinal in 0..value.items.len() {
            expected.insert(item_id(&id, ordinal)?);
        }
    }
    if items != expected {
        return Err("original staging GC item inventory is missing or has unrelated rows".into());
    }
    Ok(())
}

/// Projects only the exact retained first records, never current filesystem observations.
fn status(
    sql: &Connection,
    id: &str,
    value: Intent,
    receiver: &ReplicaContentIdentity,
) -> Result<ReplicaStagingGcStatus, String> {
    let outcomes = records(sql, id, &value, receiver)?;
    let deleted = outcomes
        .iter()
        .filter(|row| matches!(row.outcome, Some(Outcome::Deleted { .. })))
        .count();
    let missing = outcomes
        .iter()
        .filter(|row| matches!(row.outcome, Some(Outcome::Missing { .. })))
        .count();
    Ok(ReplicaStagingGcStatus {
        schema: "roboguide.replica-staging-gc-status/v0.1".into(),
        command_id: value.command_id.clone(),
        candidates: value.items.len(),
        deleted,
        missing,
        unknown: value.items.len() - deleted - missing,
        original: serde_json::json!({"intent":value,"items":outcomes}),
    })
}

/// Samples only an actual regular private file or actual NotFound, refusing symlinks and larger data.
fn file_identity(path: &Path, maximum: u64) -> Result<Option<FileIdentity>, String> {
    match std::fs::symlink_metadata(path) {
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(error.to_string()),
        Ok(meta) if meta.is_file() && meta.ino() > 0 && meta.len() <= maximum => {
            Ok(Some(FileIdentity {
                device: meta.dev(),
                inode: meta.ino(),
                byte_size: meta.len(),
            }))
        }
        Ok(_) => Err("selected staging entry is not its bounded actual regular file".into()),
    }
}

/// Hashes/unlinks only the frozen observed retired name; current absence cannot fill an old effect.
fn remove(root: &Path, selected: &Item, budget: &ContentBudget) -> Result<Outcome, String> {
    budget.check()?;
    let path = root.join("staging-v1").join(&selected.ownership.leaf);
    let actual = file_identity(&path, selected.ownership.content.byte_size)?;
    if actual != selected.file {
        return Err("original staging file changed after durable admission".into());
    }
    let Some(expected) = &selected.file else {
        return Ok(Outcome::Missing {
            observed_ms: budget.check()?,
        });
    };
    let mut file = layout::regular(&path, false)?;
    let opened = file.metadata().map_err(|error| error.to_string())?;
    if (opened.dev(), opened.ino(), opened.len())
        != (expected.device, expected.inode, expected.byte_size)
    {
        return Err("actual staging FD differs from the original selected file".into());
    }
    let mut digest = Sha256::new();
    let mut total = 0u64;
    let mut bytes = [0u8; 65536];
    loop {
        budget.check()?;
        let count = file.read(&mut bytes).map_err(|error| error.to_string())?;
        if count == 0 {
            break;
        }
        total = total
            .checked_add(count as u64)
            .ok_or("staging byte count overflow")?;
        if total > expected.byte_size {
            return Err("actual staging data grew after admission".into());
        }
        digest.update(&bytes[..count]);
    }
    if total != expected.byte_size
        || file_identity(&path, expected.byte_size)?.as_ref() != Some(expected)
    {
        return Err("original staging file changed during its bounded read".into());
    }
    budget.check()?;
    std::fs::remove_file(&path).map_err(|error| error.to_string())?;
    File::open(root.join("staging-v1"))
        .and_then(|directory| directory.sync_all())
        .map_err(|error| error.to_string())?;
    Ok(Outcome::Deleted {
        observed_ms: budget.check()?,
        observed_digest: format!("sha256:{:x}", digest.finalize()),
    })
}

impl ReplicaContentStore {
    /// Observes only an original Root8 cleanup; it never opens a payload or renews a command budget.
    pub fn staging_gc_status(
        &self,
        command_id: &str,
    ) -> Result<Option<ReplicaStagingGcStatus>, String> {
        command(command_id)?;
        if !self.inner.staging_gc {
            return Err("staging reclamation requires explicit Root8".into());
        }
        let guard = self
            .inner
            .connection
            .lock()
            .map_err(|error| error.to_string())?;
        let sql = guard.as_ref().ok_or("original staging GC SQL is closed")?;
        self.validate_connection(sql)?;
        let id = copy::digest(&command_id)?;
        intent(sql, &id)?
            .map(|value| status(sql, &id, value, self.identity()))
            .transpose()
    }

    /// Admits exact retired private names before effects; duplicate original IDs only observe.
    pub fn collect_retired_staging(
        &self,
        command_id: &str,
        mut sources: Vec<ReplicaStagingTarget>,
        duration_ms: u64,
    ) -> Result<ReplicaStagingGcStatus, String> {
        let budget = ContentBudget::issue(duration_ms)?;
        command(command_id)?;
        if !self.inner.staging_gc {
            return Err("staging reclamation requires explicit Root8".into());
        }
        sources.sort();
        if sources.is_empty()
            || sources.len() > 64
            || sources.windows(2).any(|pair| pair[0] == pair[1])
        {
            return Err(
                "staging reclamation requires one to sixty-four distinct original sources".into(),
            );
        }
        let mut guard = self
            .inner
            .connection
            .lock()
            .map_err(|error| error.to_string())?;
        let sql = guard.as_mut().ok_or("original staging GC SQL is closed")?;
        self.validate_connection(sql)?;
        let id = copy::digest(&command_id)?;
        if let Some(previous) = intent(sql, &id)? {
            if previous
                .items
                .iter()
                .map(|item| item.target.clone())
                .collect::<Vec<_>>()
                != sources
                || previous.budget.duration_ms != duration_ms
            {
                return Err("original staging GC replay changed its first request".into());
            }
            return status(sql, &id, previous, self.identity());
        }
        let mut selected = Vec::new();
        for target in sources {
            budget.check()?;
            let ownership = ownership(sql, &target)?;
            let file = file_identity(
                &self.root().join("staging-v1").join(&ownership.leaf),
                ownership.content.byte_size,
            )?;
            selected.push(Item {
                target,
                ownership,
                file,
            });
        }
        let value = Intent {
            schema: "roboguide.replica-staging-gc-intent/v0.1".into(),
            command_id: command_id.into(),
            receiver: self.identity().clone(),
            budget,
            items: selected,
        };
        let raw = serde_json::to_string(&value).map_err(|error| error.to_string())?;
        if raw.len() > 32 * 1024 * 1024 {
            return Err("original staging GC intent exceeds its bound".into());
        }
        let mut commands = layout::head(sql, "staging_gc_ids")?;
        let mut items = layout::head(sql, "staging_gc_item_ids")?;
        if commands.len() >= 4096 || items.len() + value.items.len() > 16384 {
            return Err("original staging GC history is full".into());
        }
        let transaction = sql.transaction().map_err(|error| error.to_string())?;
        transaction
            .execute(
                "INSERT INTO content_staging_gc_commands VALUES (?1,?2)",
                params![id, raw],
            )
            .map_err(|error| error.to_string())?;
        commands.insert(id.clone());
        let intent_digest = copy::digest(&value)?;
        for ordinal in 0..value.items.len() {
            let key = item_id(&id, ordinal)?;
            let record = ItemRecord {
                operation_id: id.clone(),
                ordinal,
                intent_digest: intent_digest.clone(),
                outcome: None,
            };
            transaction
                .execute(
                    "INSERT INTO content_staging_gc_items VALUES (?1,?2)",
                    params![
                        key,
                        serde_json::to_string(&record).map_err(|error| error.to_string())?
                    ],
                )
                .map_err(|error| error.to_string())?;
            items.insert(key);
        }
        layout::write_head(&transaction, "staging_gc_ids", &commands)?;
        layout::write_head(&transaction, "staging_gc_item_ids", &items)?;
        layout::within_budget(&transaction)?;
        value.budget.check()?;
        transaction.commit().map_err(|error| error.to_string())?;
        for (ordinal, selected) in value.items.iter().enumerate() {
            let outcome = remove(self.root(), selected, &value.budget)?;
            let key = item_id(&id, ordinal)?;
            let mut record = item_record(sql, &key)?;
            if record.outcome.is_some() {
                return Err("original staging GC outcome was already fixed".into());
            }
            record.outcome = Some(outcome);
            let transaction = sql.transaction().map_err(|error| error.to_string())?;
            transaction
                .execute(
                    "UPDATE content_staging_gc_items SET record=?1 WHERE item_id=?2",
                    params![
                        serde_json::to_string(&record).map_err(|error| error.to_string())?,
                        key
                    ],
                )
                .map_err(|error| error.to_string())?;
            layout::within_budget(&transaction)?;
            value.budget.check()?;
            transaction.commit().map_err(|error| error.to_string())?;
        }
        status(sql, &id, value, self.identity())
    }
}
