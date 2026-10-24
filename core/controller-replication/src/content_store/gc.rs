//! Root7 original GC intents and irreversible qualified-key tombstones; history never grants live bytes.

use super::copy::digest;
use super::*;
use rusqlite::OptionalExtension;
use std::collections::{BTreeMap, BTreeSet};
use std::fs::{self, File};
use std::os::unix::fs::MetadataExt;

/// Fixed complete item count; exhaustion never drops retained Unknown commands.
const MAX_ITEMS: usize = 16_384;
/// Complete first command and original receipts remain bounded independently of SQL size.
const MAX_RECORD: usize = 32 * 1024 * 1024;

/// Actual original file observation; absence is distinct from inaccessible/nonregular files.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Inode {
    /// Actual filesystem device, never an embodied device.
    device: u64,
    /// Actual filesystem inode, never a supplied path label.
    inode: u64,
}

/// Immutable selection evidence before the first unlink effect.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Item {
    /// Original qualified key and byte receipt remain historical after deletion.
    receipt: ReplicaContentReceipt,
    /// Complete exact inactive reference inventory digest for this key.
    reference_digest: String,
    /// Actual first file identity; None means actual NotFound only.
    file: Option<Inode>,
    /// First irreversible tombstone owner, if a prior command already fenced this key.
    prior_tombstone: Option<String>,
}

/// Original command fixed under actual leases independently of eventual outcomes.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Intent {
    /// Closed original effect interpretation.
    schema: String,
    /// Original operation ID, never reused for a second effect.
    command_id: String,
    /// Actual original Root identity.
    receiver: ReplicaContentIdentity,
    /// Original queue/effect clock, never renewed on replay or restart.
    budget: ContentBudget,
    /// Complete sorted original selection.
    items: Vec<Item>,
}

/// One actual outcome; absence stays Unknown even if the file later disappears.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Outcome {
    /// Actual unlink/fsync receipt or a separate actual NotFound observation.
    state: String,
    /// Actual sample within the original effect budget.
    observed_ms: u64,
}

/// Complete first issue plus independently durable per-item outcomes.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Record {
    /// Exact immutable issue before effects.
    intent: Intent,
    /// Same-position original outcomes; partial failure never shortens the inventory.
    outcomes: Vec<Option<Outcome>>,
}

/// First irreversible qualified-key fence, committed atomically with its original GC intent.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Tombstone {
    /// Exact original qualified key; another owner's equal hash is independent.
    content: ArtifactContentKey,
    /// First owning command, never a retry/repair substitution.
    command_id: String,
    /// Complete immutable first issue including original clock/reference evidence.
    intent_digest: String,
}

/// Readonly original GC outcome; current absence never fills an older Unknown receipt.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReplicaContentGcStatus {
    /// Closed observation interpretation.
    pub schema: String,
    /// Exact retained first command.
    pub command_id: String,
    /// Complete first provenance and partial outcomes.
    pub original: serde_json::Value,
    /// Actual original unlink/fsync receipts only.
    pub deleted: usize,
    /// Actual original absence observations, separate from unlink receipts.
    pub missing: usize,
    /// Items without their original durable outcome.
    pub unknown: usize,
    /// Complete first selection count.
    pub candidates: usize,
}

/// Creates mandatory journals only in explicit fresh/drained Root7 transactions.
pub(super) fn initialize(sql: &Connection) -> Result<(), String> {
    sql.execute_batch("CREATE TABLE content_gc_commands(operation_id TEXT PRIMARY KEY,record TEXT NOT NULL); CREATE TABLE content_tombstones(content_key TEXT PRIMARY KEY,record TEXT NOT NULL)").map_err(|e|e.to_string())?;
    for head in ["gc_ids", "tombstone_keys"] {
        layout::write_head(sql, head, &BTreeSet::new())?;
    }
    super::streams::initialize_retirements(sql)?;
    Ok(())
}

/// Reads a bounded original command; oversized records refuse instead of looking absent.
fn record(sql: &Connection, id: &str) -> Result<Option<Record>, String> {
    let length: Option<usize> = sql
        .query_row(
            "SELECT length(CAST(record AS BLOB)) FROM content_gc_commands WHERE operation_id=?1",
            [id],
            |row| row.get(0),
        )
        .optional()
        .map_err(|e| e.to_string())?;
    if length.is_some_and(|size| size > MAX_RECORD) {
        return Err("original GC record exceeds its fixed bound".into());
    }
    let raw: Option<String> = sql
        .query_row(
            "SELECT record FROM content_gc_commands WHERE operation_id=?1",
            [id],
            |row| row.get(0),
        )
        .optional()
        .map_err(|e| e.to_string())?;
    raw.map(|raw| serde_json::from_str(&raw).map_err(|e| e.to_string()))
        .transpose()
}

/// Encodes within the original record bound before any durable mutation.
fn encode(value: &Record) -> Result<String, String> {
    let raw = serde_json::to_string(value).map_err(|e| e.to_string())?;
    if raw.len() > MAX_RECORD {
        return Err("original GC record exceeds its fixed bound".into());
    }
    Ok(raw)
}

/// Reads the first qualified-key fence independently of current availability.
fn tombstone(sql: &Connection, key: &ArtifactContentKey) -> Result<Option<Tombstone>, String> {
    let raw: Option<String> = sql
        .query_row(
            "SELECT record FROM content_tombstones WHERE content_key=?1",
            [digest(key)?],
            |row| row.get(0),
        )
        .optional()
        .map_err(|e| e.to_string())?;
    raw.map(|raw| {
        if raw.len() > 16384 {
            return Err("original GC tombstone exceeds its bound".into());
        }
        serde_json::from_str(&raw).map_err(|e| e.to_string())
    })
    .transpose()
}

/// Fences only explicit Root7 keys; original Root4/5/6 retain their interpretation.
pub(super) fn require_live(sql: &Connection, key: &ArtifactContentKey) -> Result<(), String> {
    let version: i64 = sql
        .query_row("PRAGMA user_version", [], |row| row.get(0))
        .map_err(|e| e.to_string())?;
    if version == 7 && tombstone(sql, key)?.is_some() {
        return Err("original qualified content is irreversibly tombstoned".into());
    }
    Ok(())
}

/// Observes a real file, refusing symlinks and inaccessible paths rather than calling them missing.
fn inode(path: &Path) -> Result<Option<Inode>, String> {
    match fs::symlink_metadata(path) {
        Ok(meta) if meta.is_file() => Ok(Some(Inode {
            device: meta.dev(),
            inode: meta.ino(),
        })),
        Ok(_) => Err("GC input is not an actual regular file".into()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(error) => Err(error.to_string()),
    }
}

/// Complete same-transaction reference inventory grouped by qualified original content.
type References = BTreeMap<ArtifactContentKey, BTreeMap<String, ReferenceRecord>>;

/// Reads every original reference once; neither selection nor validation performs quadratic scans.
fn reference_inventory(sql: &Connection) -> Result<References, String> {
    let mut refs = References::new();
    for id in layout::head(sql, "reference_ids")? {
        let original =
            reference_record(sql, &id)?.ok_or("GC reference head lost its original row")?;
        refs.entry(original.reference.content.clone())
            .or_default()
            .insert(id, original);
    }
    Ok(refs)
}

/// Resolves all exact same-key references; an active independent scope blocks selection.
fn references(refs: &References, key: &ArtifactContentKey) -> Result<(bool, String), String> {
    let empty = BTreeMap::new();
    let values = refs.get(key).unwrap_or(&empty);
    Ok((values.values().any(|record| record.active), digest(values)?))
}

/// Checks every command/outcome and first tombstone against complete mandatory original inventories.
pub(super) fn validate_records(
    sql: &Connection,
    receiver: &ReplicaContentIdentity,
) -> Result<(), String> {
    let ids = layout::head(sql, "gc_ids")?;
    if ids.len() > 4096 {
        return Err("GC history exceeds its fixed bound".into());
    }
    let refs = reference_inventory(sql)?;
    let mut owned = BTreeSet::new();
    for id in ids {
        let value = record(sql, &id)?.ok_or("GC mandatory head lost its original command")?;
        let intent = &value.intent;
        intent.budget.validate()?;
        if intent.schema != "roboguide.replica-content-gc/v0.1"
            || intent.receiver != *receiver
            || intent.command_id.is_empty()
            || intent.command_id.len() > 128
            || intent.command_id.chars().any(char::is_control)
            || digest(&intent.command_id)? != id
            || intent.items.len() > MAX_ITEMS
            || value.outcomes.len() != intent.items.len()
            || intent
                .items
                .windows(2)
                .any(|items| items[0].receipt.content >= items[1].receipt.content)
        {
            return Err("original GC identity/items/budget changed".into());
        }
        let intent_digest = digest(intent)?;
        for (item, outcome) in intent.items.iter().zip(&value.outcomes) {
            let key = &item.receipt.content;
            key.validate()?;
            if item.receipt.store_owner != receiver.store_owner
                || item.receipt.member != receiver.member
                || item.receipt.profile_identity != receiver.profile_identity
                || item.file.as_ref().is_some_and(|file| file.inode == 0)
                || !crate::content::hex_digest(&item.reference_digest)
            {
                return Err("original GC item provenance differs".into());
            }
            if item.receipt.schema == super::streams::RECEIPT_SCHEMA {
                super::streams::validate_object_receipt(sql, &item.receipt)?;
            } else {
                copy::validate_original_receipt(sql, &item.receipt)?;
            }
            if let Some(outcome) = outcome
                && (!matches!(outcome.state.as_str(), "deleted" | "missing")
                    || !intent.budget.validates_completion(outcome.observed_ms)
                    || (outcome.state == "deleted" && item.file.is_none()))
            {
                return Err("GC outcome lacks its original effect evidence".into());
            }
            let fence =
                tombstone(sql, key)?.ok_or("GC original intent lost its mandatory tombstone")?;
            let (active, reference_digest) = references(&refs, key)?;
            if fence.content != *key || active || reference_digest != item.reference_digest {
                return Err("GC tombstone/input references changed".into());
            }
            if let Some(first) = &item.prior_tombstone {
                if first == &intent.command_id || first != &fence.command_id {
                    return Err("GC follow-up replaced its original tombstone owner".into());
                }
            } else {
                if fence.command_id != intent.command_id || fence.intent_digest != intent_digest {
                    return Err("GC first tombstone identity differs".into());
                }
                owned.insert(digest(key)?);
            }
        }
    }
    if owned != layout::head(sql, "tombstone_keys")? {
        return Err("GC tombstone inventory lost its original first command".into());
    }
    Ok(())
}

/// Returns retained outcomes only; it never samples replacement completion or current availability.
fn status(value: &Record) -> Result<ReplicaContentGcStatus, String> {
    Ok(ReplicaContentGcStatus {
        schema: "roboguide.replica-content-gc-status/v0.1".into(),
        command_id: value.intent.command_id.clone(),
        original: serde_json::to_value(value).map_err(|e| e.to_string())?,
        deleted: value
            .outcomes
            .iter()
            .flatten()
            .filter(|x| x.state == "deleted")
            .count(),
        missing: value
            .outcomes
            .iter()
            .flatten()
            .filter(|x| x.state == "missing")
            .count(),
        unknown: value.outcomes.iter().filter(|x| x.is_none()).count(),
        candidates: value.intent.items.len(),
    })
}

impl ReplicaContentStore {
    /// Observes an existing original Root7 command without creating storage, effects or a new budget.
    pub fn gc_status(&self, command: &str) -> Result<Option<ReplicaContentGcStatus>, String> {
        if !self.inner.reclamation {
            return Err("GC observation requires explicit Root7".into());
        }
        let guard = self.inner.connection.lock().map_err(|e| e.to_string())?;
        let sql = guard.as_ref().ok_or("original Root SQL is closed")?;
        self.validate_connection(sql)?;
        record(sql, &digest(&command)?)?
            .as_ref()
            .map(status)
            .transpose()
    }

    /// Freezes inactive exact selections and irreversible tombstones before bounded actual unlink.
    /// Same-ID calls only observe; partial failure preserves first provenance and unresolved outcomes.
    pub fn collect_unreferenced_content(
        &self,
        command: &str,
        duration_ms: u64,
    ) -> Result<ReplicaContentGcStatus, String> {
        if !self.inner.reclamation {
            return Err("GC requires explicit Root7".into());
        }
        if command.is_empty() || command.len() > 128 || command.chars().any(char::is_control) {
            return Err("GC command identity is invalid".into());
        }
        let clock = ContentBudget::issue(duration_ms)?;
        let mut guard = self.inner.connection.lock().map_err(|e| e.to_string())?;
        let sql = guard.as_mut().ok_or("original Root SQL is closed")?;
        self.validate_connection(sql)?;
        let id = digest(&command)?;
        if let Some(original) = record(sql, &id)? {
            return status(&original);
        }
        let refs = reference_inventory(sql)?;
        let mut items = Vec::new();
        for key in layout::head(sql, "content_keys")? {
            clock.check()?;
            let raw: String = sql
                .query_row(
                    "SELECT record FROM content_objects WHERE content_key=?1",
                    [key],
                    |row| row.get(0),
                )
                .map_err(|e| e.to_string())?;
            let receipt: ReplicaContentReceipt =
                serde_json::from_str(&raw).map_err(|e| e.to_string())?;
            let (active, reference_digest) = references(&refs, &receipt.content)?;
            if active {
                continue;
            }
            let file = inode(&copy::object_path(self.root(), &receipt.content))?;
            let prior_tombstone = tombstone(sql, &receipt.content)?.map(|fence| fence.command_id);
            items.push(Item {
                receipt,
                reference_digest,
                file,
                prior_tombstone,
            });
            if items.len() > MAX_ITEMS {
                return Err("GC selection exceeds its fixed bound".into());
            }
        }
        items.sort_by(|a, b| a.receipt.content.cmp(&b.receipt.content));
        let mut value = Record {
            outcomes: vec![None; items.len()],
            intent: Intent {
                schema: "roboguide.replica-content-gc/v0.1".into(),
                command_id: command.into(),
                receiver: self.identity().clone(),
                budget: clock,
                items,
            },
        };
        let transaction = sql.transaction().map_err(|e| e.to_string())?;
        let mut ids = layout::head(&transaction, "gc_ids")?;
        if ids.len() >= 4096 {
            return Err("GC history reached its fixed bound".into());
        }
        let mut keys = layout::head(&transaction, "tombstone_keys")?;
        let intent_digest = digest(&value.intent)?;
        transaction
            .execute(
                "INSERT INTO content_gc_commands VALUES (?1,?2)",
                params![id, encode(&value)?],
            )
            .map_err(|e| e.to_string())?;
        for item in &value.intent.items {
            if item.prior_tombstone.is_some() {
                continue;
            }
            let key = digest(&item.receipt.content)?;
            let fence = Tombstone {
                content: item.receipt.content.clone(),
                command_id: command.into(),
                intent_digest: intent_digest.clone(),
            };
            transaction
                .execute(
                    "INSERT INTO content_tombstones VALUES (?1,?2)",
                    params![
                        key,
                        serde_json::to_string(&fence).map_err(|e| e.to_string())?
                    ],
                )
                .map_err(|e| e.to_string())?;
            keys.insert(key);
        }
        ids.insert(id.clone());
        layout::write_head(&transaction, "gc_ids", &ids)?;
        layout::write_head(&transaction, "tombstone_keys", &keys)?;
        layout::within_budget(&transaction)?;
        value.intent.budget.check()?;
        transaction.commit().map_err(|e| e.to_string())?;
        for (index, item) in value.intent.items.iter().enumerate() {
            value.intent.budget.check()?;
            let path = copy::object_path(self.root(), &item.receipt.content);
            let state = match inode(&path)? {
                None => "missing",
                Some(actual) => {
                    if Some(&actual) != item.file.as_ref() {
                        return Err("GC original file changed before unlink".into());
                    }
                    let file = copy::verify_file_with_check(&path, &item.receipt.content, || {
                        value.intent.budget.check().map(|_| ())
                    })?;
                    let meta = file.metadata().map_err(|e| e.to_string())?;
                    if actual
                        != (Inode {
                            device: meta.dev(),
                            inode: meta.ino(),
                        })
                        || inode(&path)?.as_ref() != Some(&actual)
                    {
                        return Err("GC named file changed during verification".into());
                    }
                    value.intent.budget.check()?;
                    fs::remove_file(&path).map_err(|e| e.to_string())?;
                    File::open(path.parent().ok_or("GC CAS parent absent")?)
                        .and_then(|file| file.sync_all())
                        .map_err(|e| e.to_string())?;
                    "deleted"
                }
            };
            value.outcomes[index] = Some(Outcome {
                state: state.into(),
                observed_ms: value.intent.budget.check()?,
            });
            let transaction = sql.transaction().map_err(|e| e.to_string())?;
            transaction
                .execute(
                    "UPDATE content_gc_commands SET record=?1 WHERE operation_id=?2",
                    params![encode(&value)?, id],
                )
                .map_err(|e| e.to_string())?;
            layout::within_budget(&transaction)?;
            value.intent.budget.check()?;
            transaction.commit().map_err(|e| e.to_string())?;
        }
        status(&value)
    }
}
