//! Explicit offline adoption of current original parent retention; historical evidence is never backfilled.

use super::*;
use crate::content_store::{ContentBudget, ProducerAdmission, ProducerClock, ProducerPermit};
use std::os::unix::fs::MetadataExt;

#[cfg(test)]
#[path = "store_adoption_tests.rs"]
mod tests;

/// Mandatory complete local adoption history, including original interrupted operations.
const HISTORY_KEY: &str = "content_parent_adoptions";
/// Complete retained first-intent inventory, mandatory after the initial admission transaction.
const HISTORY_HEAD: &str = "content_parent_adoption_ids";
/// Pending original v4 source is fenced before producer effects; ordinary/old opens refuse this marker.
pub(super) const PENDING_SCHEMA: &str = "roboguide.replica-storage/v0.4-adoption-pending/v0.1";
/// Mandatory exact command committed with the v6 generation and complete current producer heads.
const COMMIT_KEY: &str = "content_parent_adoption_commit";
/// Exhaustion refuses new adoption rather than forgetting an original Unknown command.
const MAX_ADOPTIONS: usize = 32;
/// Complete bounded original SQL payload; metadata remains separate from actual content bytes.
const MAX_SOURCE_BYTES: u64 = 512 * 1024 * 1024;

/// New current retention responsibility, independently of the old unavailable effect budget.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct AdoptedSource {
    /// Exact actual current original parent record and its retention kind.
    source: ReplicaContentProducerSource,
    /// Complete sorted qualified current inputs; no hash-only aliasing is permitted.
    inputs: Vec<ArtifactContentKey>,
}

/// Exact original admission before any managed producer pin, byte verification or publication.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct AdoptionIntent {
    /// Closed local management interpretation.
    schema: String,
    /// Immutable explicit new management command, never an old append/build/install ID.
    operation_id: String,
    /// Actual original software cluster and domain checkpoint interpretation.
    profile: String,
    /// Exact original application checkpoint generation.
    application_schema: String,
    /// Independently sampled held original parent SQL descriptor.
    sql_identity: (u64, u64),
    /// Actual original member-local content backend, unchanged by adoption.
    content_root: ReplicaContentIdentity,
    /// Complete original business SQL rows; excludes only local adoption/head bookkeeping.
    source_digest: String,
    /// All retained legacy rows are preserved; this does not assign them all to this parent.
    legacy_inventory_digest: String,
    /// Complete current content-bearing Log/View/Snapshot responsibilities.
    sources: Vec<AdoptedSource>,
    /// Actual first process/kernel admission, including scan, writer wait and byte verification.
    budget: ContentBudget,
}

/// Actual new publication receipt, separate from all unavailable historical process/budget facts.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct AdoptionPublication {
    /// Exact complete new local producer IDs, never imported from a Snapshot or caller receipt.
    producer_ids: Vec<String>,
    /// Actual complete original SQL observation after installing the new local heads.
    source_digest: String,
    /// Actual same-kernel completion within this adoption's immutable original budget.
    completed_ms: u64,
}

/// Retained management command; missing completion stays Unknown across retries and new repairs.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct AdoptionRecord {
    /// Exact first admission, never reconstructed from a later process/configuration.
    intent: AdoptionIntent,
    /// New current-retention publication only, not an original historical append/build receipt.
    publication: Option<AdoptionPublication>,
}

/// Exact new member-local source of the accepted generation, independent of a completion response.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct AdoptionCommit {
    /// Exact original first intent committed with this generation.
    operation_id: String,
    /// Complete immutable admission binding, including actual SQL/root/process/budget evidence.
    intent_digest: String,
    /// Complete independent actual current-retention producer admissions.
    producer_ids: Vec<String>,
}

/// Bounded read-only management result; no quorum, content-use, Task or device authority follows.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ReplicaContentParentAdoptionStatus {
    /// Closed local observation meaning.
    pub schema: String,
    /// Exact original new adoption command.
    pub operation_id: String,
    /// Complete immutable first admission identity.
    pub intent_digest: String,
    /// Original complete first intent and optional actual new publication receipt.
    pub original: serde_json::Value,
    /// Unknown or published; old missing historical budgets remain unavailable in either case.
    pub disposition: String,
}

/// Hashes canonical local typed evidence without changing original persisted source encodings.
fn digest(value: &impl Serialize) -> Result<String, String> {
    Ok(format!(
        "{:x}",
        Sha256::digest(serde_json::to_vec(value).map_err(|error| error.to_string())?)
    ))
}

/// Converts only the retained actual record; observation never supplies a completion time.
fn status(record: &AdoptionRecord) -> Result<ReplicaContentParentAdoptionStatus, String> {
    Ok(ReplicaContentParentAdoptionStatus {
        schema: "roboguide.content-parent-adoption-status/v0.1".into(),
        operation_id: record.intent.operation_id.clone(),
        intent_digest: digest(&record.intent)?,
        original: serde_json::to_value(record).map_err(|error| error.to_string())?,
        disposition: if record.publication.is_some() {
            "published"
        } else {
            "unknown"
        }
        .into(),
    })
}

/// Requires a bounded complete history; only pre-adoption v4 may legitimately lack the new row.
fn history(connection: &Connection, mandatory: bool) -> Result<Vec<AdoptionRecord>, String> {
    let pending: bool = connection
        .query_row(
            "SELECT schema=?1 FROM replica_storage_format",
            [PENDING_SCHEMA],
            |row| row.get(0),
        )
        .map_err(|error| error.to_string())?;
    let head: Option<Vec<String>> =
        read_meta(connection, HISTORY_HEAD).map_err(|error| error.to_string())?;
    let length: Option<u64> = connection
        .query_row(
            "SELECT length(CAST(value AS BLOB)) FROM replica_v1_meta WHERE key=?1",
            [HISTORY_KEY],
            |row| row.get(0),
        )
        .optional()
        .map_err(|error| error.to_string())?;
    if length.is_some_and(|value| value > 32 * 1024 * 1024) {
        return Err("original parent adoption history exceeds its bound".into());
    }
    let raw: Option<String> = connection
        .query_row(
            "SELECT value FROM replica_v1_meta WHERE key=?1",
            [HISTORY_KEY],
            |row| row.get(0),
        )
        .optional()
        .map_err(|error| error.to_string())?;
    let Some(raw) = raw else {
        return if mandatory || pending || head.is_some() {
            Err("mandatory original parent adoption history is missing".into())
        } else {
            Ok(Vec::new())
        };
    };
    if raw.len() > 32 * 1024 * 1024 {
        return Err("original parent adoption history exceeds its bound".into());
    }
    let records: Vec<AdoptionRecord> =
        serde_json::from_str(&raw).map_err(|error| error.to_string())?;
    if records.is_empty() || records.len() > MAX_ADOPTIONS {
        return Err("original parent adoption history is incomplete or full".into());
    }
    let expected = records
        .iter()
        .map(|record| digest(&record.intent))
        .collect::<Result<Vec<_>, _>>()?;
    if head.as_ref() != Some(&expected) {
        return Err(
            "mandatory original parent adoption intent inventory is missing or changed".into(),
        );
    }
    let mut ids = BTreeSet::new();
    for record in &records {
        let intent = &record.intent;
        intent.budget.validate()?;
        if intent.schema != "roboguide.content-parent-adoption-intent/v0.1"
            || intent.operation_id.is_empty()
            || intent.operation_id.len() > 128
            || intent.operation_id.chars().any(char::is_control)
            || !ids.insert(&intent.operation_id)
            || intent.sql_identity.1 == 0
            || !crate::content::hex_digest(&intent.profile)
            || !crate::content::hex_digest(&intent.source_digest)
            || !crate::content::hex_digest(&intent.legacy_inventory_digest)
            || intent.sources.len() > 4096
        {
            return Err("original parent adoption identity/source differs".into());
        }
        let mut sources = BTreeSet::new();
        for adopted in &intent.sources {
            let source = &adopted.source;
            if source.epoch.term == 0
                || source.epoch.replica_id == 0
                || source.position.is_empty()
                || source.position.len() > 256
                || source.position.chars().any(char::is_control)
                || !crate::content::hex_digest(&source.authority_digest)
                || !crate::content::hex_digest(&source.parent_record_digest)
                || source
                    .checkpoint_digest
                    .as_deref()
                    .is_some_and(|value| !crate::content::hex_digest(value))
                || adopted.inputs.len() > 16_384
                || adopted.inputs.windows(2).any(|keys| keys[0] >= keys[1])
                || !sources.insert(digest(source)?)
            {
                return Err(
                    "original parent adoption lost complete bounded source semantics".into(),
                );
            }
            for key in &adopted.inputs {
                key.validate()?;
            }
        }
        if let Some(receipt) = &record.publication
            && (receipt.source_digest != intent.source_digest
                || receipt.producer_ids.len() != intent.sources.len()
                || receipt.producer_ids != producer_ids(intent)?
                || !intent.budget.validates_completion(receipt.completed_ms))
        {
            return Err("original parent adoption receipt lost its exact source/budget".into());
        }
    }
    Ok(records)
}

/// Validates every original record against this exact root/profile before normal opens or migrations.
pub(super) fn validate_history(
    connection: &Connection,
    version: i64,
    profile: &str,
    application_schema: &str,
    content: Option<&ReplicaContentIdentity>,
) -> Result<(), String> {
    let records = history(connection, version == ADOPTED_STORAGE_VERSION)?;
    if !records.is_empty() && !matches!(version, CONTENT_STORAGE_VERSION | ADOPTED_STORAGE_VERSION)
    {
        return Err("original adoption authority is hidden in another storage generation".into());
    }
    for record in &records {
        if record.intent.profile != profile
            || record.intent.application_schema != application_schema
            || Some(&record.intent.content_root) != content
        {
            return Err("original parent adoption profile/root identity differs".into());
        }
    }
    if version == ADOPTED_STORAGE_VERSION {
        let commit = read_commit(connection)?;
        let original = records.last().ok_or("original adopted history is empty")?;
        if commit.operation_id != original.intent.operation_id
            || commit.intent_digest != digest(&original.intent)?
            || commit.producer_ids != producer_ids(&original.intent)?
        {
            return Err("original adoption generation lost its exact committed intent".into());
        }
    } else if read_meta::<serde_json::Value>(connection, COMMIT_KEY)
        .map_err(|error| error.to_string())?
        .is_some()
    {
        return Err("original adoption Commit is hidden in an old storage generation".into());
    }
    Ok(())
}

/// Reads the exact mandatory original generation Commit; no missing row is reconstructed.
fn read_commit(connection: &Connection) -> Result<AdoptionCommit, String> {
    read_meta(connection, COMMIT_KEY)
        .map_err(|error| error.to_string())?
        .ok_or_else(|| "mandatory original parent adoption Commit is missing".into())
}

/// Validates original local journal ownership even after current heads are replaced normally.
pub(super) fn validate_root_records(
    store: &SqliteRaftStore,
    connection: &Connection,
) -> Result<(), String> {
    let version: i64 = connection
        .query_row("PRAGMA user_version", [], |row| row.get(0))
        .map_err(|error| error.to_string())?;
    if version != ADOPTED_STORAGE_VERSION {
        return Ok(());
    }
    let records = history(connection, true)?;
    let original = records.last().ok_or("original adopted history is empty")?;
    if store._writer.identity()? != original.intent.sql_identity {
        return Err("original adoption source SQL inode differs".into());
    }
    let content = store
        .content
        .as_ref()
        .ok_or("original adoption content backend is absent")?;
    for (source, id) in original
        .intent
        .sources
        .iter()
        .zip(&read_commit(connection)?.producer_ids)
    {
        let producer = content
            .producer_status(id)?
            .ok_or("original adopted producer journal disappeared")?;
        let actual = &producer.original["intent"];
        if actual["source"]
            != serde_json::to_value(&source.source).map_err(|error| error.to_string())?
            || actual["inputs"]
                != serde_json::to_value(&source.inputs).map_err(|error| error.to_string())?
            || actual["budget"]
                != serde_json::to_value(&original.intent.budget)
                    .map_err(|error| error.to_string())?
            || actual["receiver"]
                != serde_json::to_value(&original.intent.content_root)
                    .map_err(|error| error.to_string())?
            || actual["sql_device"].as_u64() != Some(original.intent.sql_identity.0)
            || actual["sql_inode"].as_u64() != Some(original.intent.sql_identity.1)
        {
            return Err(
                "original adopted producer differs from its original management intent".into(),
            );
        }
    }
    Ok(())
}

/// Requires the exact actual leased inode to remain at the opened SQLite filename during all effects.
fn require_path(path: &Path, identity: (u64, u64)) -> Result<(), String> {
    let actual = std::fs::symlink_metadata(path).map_err(|error| error.to_string())?;
    if !actual.is_file() || (actual.dev(), actual.ino()) != identity {
        return Err("original adoption SQL path changed its held inode".into());
    }
    Ok(())
}

/// Defines the complete fixed source projection and its exact byte/count preflight queries.
fn source_queries() -> [(&'static str, &'static str); 5] {
    [
        (
            "SELECT key,value FROM replica_v1_meta WHERE key NOT IN ('content_parent_adoptions','content_producer_heads','content_parent_adoption_commit','content_parent_adoption_ids') ORDER BY key",
            "SELECT COUNT(*),COALESCE(SUM(length(CAST(key AS BLOB))+length(CAST(value AS BLOB))),0) FROM replica_v1_meta WHERE key NOT IN ('content_parent_adoptions','content_producer_heads','content_parent_adoption_commit','content_parent_adoption_ids')",
        ),
        (
            "SELECT revision,command_id,data FROM replica_v1_commands ORDER BY revision",
            "SELECT COUNT(*),COALESCE(SUM(length(CAST(data AS BLOB))+length(CAST(command_id AS BLOB))+8),0) FROM replica_v1_commands",
        ),
        (
            "SELECT log_index,data FROM replica_v1_log ORDER BY log_index",
            "SELECT COUNT(*),COALESCE(SUM(length(CAST(data AS BLOB))+8),0) FROM replica_v1_log",
        ),
        (
            "SELECT sequence,event_id,data,digest FROM replica_v1_events ORDER BY sequence",
            "SELECT COUNT(*),COALESCE(SUM(length(CAST(data AS BLOB))+length(CAST(event_id AS BLOB))+length(CAST(digest AS BLOB))+8),0) FROM replica_v1_events",
        ),
        (
            "SELECT singleton,meta,data FROM replica_v1_snapshot ORDER BY singleton",
            "SELECT COUNT(*),COALESCE(SUM(length(CAST(meta AS BLOB))+length(data)+8),0) FROM replica_v1_snapshot",
        ),
    ]
}

/// Bounds original stored values before full source deserialization or large SQL value allocation.
pub(super) fn validate_scan_bounds(connection: &Connection) -> Result<(), String> {
    let mut bytes = 0u64;
    for (_, bound) in source_queries() {
        let (rows, length): (u64, u64) = connection
            .query_row(bound, [], |row| Ok((row.get(0)?, row.get(1)?)))
            .map_err(|error| error.to_string())?;
        bytes = bytes
            .checked_add(length)
            .ok_or("original parent source size overflow")?;
        if rows > 65_536 || bytes > MAX_SOURCE_BYTES {
            return Err("original parent source exceeds retained scan bounds".into());
        }
    }
    Ok(())
}

/// Hashes every original business row under fixed whole-source bounds before reading large values.
fn source_digest(connection: &Connection) -> Result<String, String> {
    validate_scan_bounds(connection)?;
    let mut result = Sha256::new();
    for (index, (query, _)) in source_queries().iter().enumerate() {
        result.update((index as u64).to_be_bytes());
        let mut statement = connection
            .prepare(query)
            .map_err(|error| error.to_string())?;
        let columns = statement.column_count();
        let mut rows = statement.query([]).map_err(|error| error.to_string())?;
        while let Some(row) = rows.next().map_err(|error| error.to_string())? {
            result.update([0xff]);
            for column in 0..columns {
                use rusqlite::types::ValueRef;
                match row.get_ref(column).map_err(|error| error.to_string())? {
                    ValueRef::Integer(value) => {
                        result.update([0]);
                        result.update(value.to_be_bytes());
                    }
                    ValueRef::Text(value) | ValueRef::Blob(value) => {
                        result.update([
                            if matches!(
                                row.get_ref(column).map_err(|error| error.to_string())?,
                                ValueRef::Text(_)
                            ) {
                                1
                            } else {
                                2
                            },
                        ]);
                        result.update((value.len() as u64).to_be_bytes());
                        result.update(value);
                    }
                    _ => return Err("original parent source contains unexpected SQL types".into()),
                }
            }
        }
    }
    Ok(format!("{:x}", result.finalize()))
}

/// Captures and independently validates only actual complete current parent responsibilities.
fn sources(
    connection: &Connection,
    profile: &str,
    application_schema: &str,
) -> Result<Vec<AdoptedSource>, String> {
    let mut result = Vec::new();
    let mut statement = connection
        .prepare("SELECT log_index,data FROM replica_v1_log ORDER BY log_index")
        .map_err(|error| error.to_string())?;
    let mut rows = statement.query([]).map_err(|error| error.to_string())?;
    while let Some(row) = rows.next().map_err(|error| error.to_string())? {
        let index: u64 = row.get(0).map_err(|error| error.to_string())?;
        let raw: String = row.get(1).map_err(|error| error.to_string())?;
        let entry: Entry<ControllerRaftConfig> =
            serde_json::from_str(&raw).map_err(|error| error.to_string())?;
        if entry.log_id.index != index {
            return Err("original parent log position differs from its raw row".into());
        }
        if let EntryPayload::Normal(ReplicaCommand::ContentApplication {
            content_application: candidate,
        }) = &entry.payload
        {
            if candidate.profile_identity != profile
                || candidate.batch.checkpoint.schema != application_schema
            {
                return Err("original parent log has another profile/application source".into());
            }
            candidate
                .batch
                .validate()
                .map_err(|error| error.to_string())?;
            result.push(AdoptedSource {
                source: ReplicaContentProducerSource {
                    kind: ReplicaContentProducerKind::Log,
                    epoch: ControllerEpoch {
                        term: entry.log_id.leader_id.term,
                        replica_id: entry.log_id.leader_id.node_id,
                    },
                    position: serde_json::to_string(&entry.log_id)
                        .map_err(|error| error.to_string())?,
                    authority_digest: candidate.identity()?,
                    parent_record_digest: format!("{:x}", Sha256::digest(raw.as_bytes())),
                    checkpoint_digest: Some(state::checkpoint_identity(
                        &candidate.batch.checkpoint,
                    )),
                },
                inputs: candidate.inputs.clone(),
            });
        }
        if result.len() > 4096 {
            return Err("original parent producer source count exceeds retained bounds".into());
        }
    }
    let view = read_view(connection).map_err(|error| error.to_string())?;
    view.validate(application_schema, profile)
        .map_err(|error| error.to_string())?;
    if let Some(current) = &view.content {
        result.push(AdoptedSource {
            source: ReplicaContentProducerSource {
                kind: ReplicaContentProducerKind::View,
                epoch: current.epoch,
                position: format!("adoption-view:{}:{}", view.revision, current.batch_digest),
                authority_digest: current.batch_digest.clone(),
                parent_record_digest: producers::view_record_digest(&view.content)
                    .map_err(|error| error.to_string())?,
                checkpoint_digest: view.checkpoint.as_ref().map(state::checkpoint_identity),
            },
            inputs: current.inputs.clone(),
        });
    }
    let snapshot: Option<(String, Vec<u8>)> = connection
        .query_row(
            "SELECT meta,data FROM replica_v1_snapshot WHERE singleton=1",
            [],
            |row| Ok((row.get(0)?, row.get(1)?)),
        )
        .optional()
        .map_err(|error| error.to_string())?;
    if let Some((raw, data)) = snapshot {
        if data.len() > MAX_SNAPSHOT_BYTES {
            return Err("original adopted Snapshot exceeds retained bounds".into());
        }
        let meta: SnapshotMeta<u64, BasicNode> =
            serde_json::from_str(&raw).map_err(|error| error.to_string())?;
        let body: StoreSnapshot =
            serde_json::from_slice(&data).map_err(|error| error.to_string())?;
        if body.profile != profile
            || body.last_applied != meta.last_log_id
            || body.membership != meta.last_membership
            || meta.snapshot_id != snapshot_identity(body.last_applied, body.view.revision, &data)
        {
            return Err("original adopted Snapshot identity/pointers differ".into());
        }
        body.view
            .validate(application_schema, profile)
            .map_err(|error| error.to_string())?;
        if let Some(trust) = &body.certificate_trust {
            trust.validate()?;
        }
        let epoch = body
            .last_applied
            .map(|id| ControllerEpoch {
                term: id.leader_id.term,
                replica_id: id.leader_id.node_id,
            })
            .or_else(|| body.view.content.as_ref().map(|state| state.epoch))
            .ok_or("original adopted Snapshot epoch is unavailable; no epoch is invented")?;
        result.push(AdoptedSource {
            source: ReplicaContentProducerSource {
                kind: ReplicaContentProducerKind::SnapshotBuild,
                epoch,
                position: meta.snapshot_id.clone(),
                authority_digest: format!("{:x}", Sha256::digest(&data)),
                parent_record_digest: producers::snapshot_record_digest(&meta, &data)
                    .map_err(|error| error.to_string())?,
                checkpoint_digest: body
                    .view
                    .checkpoint
                    .as_ref()
                    .map(state::checkpoint_identity),
            },
            inputs: body
                .view
                .content
                .as_ref()
                .map(|state| state.inputs.clone())
                .unwrap_or_default(),
        });
    }
    if result.len() > 4096 {
        return Err("original parent producer source count exceeds retained bounds".into());
    }
    Ok(result)
}

/// Binds new current-retention IDs to this adoption and exact original sources, never to old effect IDs.
fn producer_ids(intent: &AdoptionIntent) -> Result<Vec<String>, String> {
    intent
        .sources
        .iter()
        .map(|source| {
            digest(&(
                "parent-adoption",
                &intent.operation_id,
                &intent.sql_identity,
                source,
            ))
            .map(|id| format!("producer:{id}"))
        })
        .collect()
}

impl SqliteRaftStore {
    /// Explicitly adopts current v4 responsibility under original actual writers; retries only observe.
    /// The caller must have upgraded the same root separately; legacy pins/history are never retired here.
    pub fn adopt_original_content_parent(
        path: &Path,
        profile: &str,
        application_schema: &str,
        content: ReplicaContentStore,
        operation_id: &str,
        duration_ms: u64,
    ) -> Result<ReplicaContentParentAdoptionStatus, String> {
        Self::adopt_content_parent_inner(
            path,
            profile,
            application_schema,
            content,
            operation_id,
            ContentBudget::issue(duration_ms)?,
        )
    }

    /// Issues the original maintenance budget before acquiring any original root or parent SQL writer.
    pub fn adopt_original_content_parent_from_root(
        path: &Path,
        profile: &str,
        application_schema: &str,
        content_root: &Path,
        member: u64,
        operation_id: &str,
        duration_ms: u64,
    ) -> Result<ReplicaContentParentAdoptionStatus, String> {
        let budget = ContentBudget::issue(duration_ms)?;
        let content = ReplicaContentStore::open_with_producers(content_root, profile, member)?;
        Self::adopt_content_parent_inner(
            path,
            profile,
            application_schema,
            content,
            operation_id,
            budget,
        )
    }

    /// Retains one actual original clock through every source read and effect, including existing-ID observation.
    fn adopt_content_parent_inner(
        path: &Path,
        profile: &str,
        application_schema: &str,
        content: ReplicaContentStore,
        operation_id: &str,
        budget: ContentBudget,
    ) -> Result<ReplicaContentParentAdoptionStatus, String> {
        if operation_id.is_empty()
            || operation_id.len() > 128
            || operation_id.chars().any(char::is_control)
        {
            return Err("original parent adoption command ID is unbounded".into());
        }
        if !content.producer_journal_enabled()
            || content.identity().profile_identity != profile
            || application_schema != state::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA
        {
            return Err(
                "original parent adoption requires its exact upgraded root/profile/application"
                    .into(),
            );
        }
        let actual = std::fs::symlink_metadata(path).map_err(|error| error.to_string())?;
        if !actual.is_file() {
            return Err("original parent adoption requires an existing regular SQL file".into());
        }
        let mut store = Self::open_inner(path, profile, application_schema, Some(content), true)?;
        require_path(path, store._writer.identity()?)?;
        let version: i64;
        let mut originals;
        let source_digest;
        let sources;
        {
            let connection = store.connection.lock().map_err(|error| error.to_string())?;
            version = connection
                .query_row("PRAGMA user_version", [], |row| row.get(0))
                .map_err(|error| error.to_string())?;
            originals = history(&connection, version == ADOPTED_STORAGE_VERSION)?;
            if let Some(original) = originals
                .iter()
                .find(|record| record.intent.operation_id == operation_id)
            {
                if original.intent.budget.duration_ms != budget.duration_ms
                    || original.intent.sql_identity != store._writer.identity()?
                {
                    return Err(
                        "original parent adoption command changed its actual source/duration"
                            .into(),
                    );
                }
                return status(original);
            }
            if version != CONTENT_STORAGE_VERSION {
                return Err(
                    "original parent is already adopted; new commands cannot repeat publication"
                        .into(),
                );
            }
            if originals.len() >= MAX_ADOPTIONS {
                return Err("original parent adoption history reached its bound".into());
            }
            Self::require_adoption_children_closed(&connection)?;
            source_digest = self::source_digest(&connection)?;
            sources = self::sources(&connection, profile, application_schema)?;
        }
        budget.check()?;
        let content = store
            .content
            .as_ref()
            .ok_or("original adoption content backend disappeared")?
            .clone();
        let intent = AdoptionIntent {
            schema: "roboguide.content-parent-adoption-intent/v0.1".into(),
            operation_id: operation_id.into(),
            profile: profile.into(),
            application_schema: application_schema.into(),
            sql_identity: store._writer.identity()?,
            content_root: content.identity().clone(),
            source_digest,
            legacy_inventory_digest: content.legacy_producer_inventory_digest()?,
            sources,
            budget,
        };
        let ids = producer_ids(&intent)?;
        originals.push(AdoptionRecord {
            intent: intent.clone(),
            publication: None,
        });
        {
            let mut connection = store.connection.lock().map_err(|error| error.to_string())?;
            let transaction = connection
                .transaction_with_behavior(TransactionBehavior::Exclusive)
                .map_err(|error| error.to_string())?;
            intent.budget.check()?;
            require_path(path, intent.sql_identity)?;
            if self::source_digest(&transaction)? != intent.source_digest {
                return Err("original adoption source changed before intent".into());
            }
            write_history(&transaction, &originals)?;
            transaction
                .execute(
                    "UPDATE replica_storage_format SET schema=?1 WHERE version=4",
                    [PENDING_SCHEMA],
                )
                .map_err(|error| error.to_string())?;
            intent.budget.check()?;
            transaction.commit().map_err(|error| error.to_string())?;
        }
        store.work_clock = Some(ProducerClock::from_original(&intent.budget)?);
        let mut permits: Vec<Box<ProducerPermit>> = Vec::new();
        for (source, id) in intent.sources.iter().zip(&ids) {
            match store.begin_content_producer_for(id, source.source.clone(), &source.inputs).map_err(|error| error.to_string())? {
                Some(ProducerAdmission::New(permit)) => permits.push(permit),
                _ => return Err("new original adoption unexpectedly reused an old producer; no repeated effects".into()),
            }
        }
        for source in &intent.sources {
            for key in &source.inputs {
                intent.budget.check()?;
                require_path(path, intent.sql_identity)?;
                content.verify_content(key)?;
            }
        }
        let mut heads = producers::ProducerHeads::default();
        for (source, id) in intent.sources.iter().zip(&ids) {
            let head = producers::ProducerHead {
                operation_id: id.clone(),
                record_digest: source.source.parent_record_digest.clone(),
            };
            match source.source.kind {
                ReplicaContentProducerKind::View => heads.view = Some(head),
                ReplicaContentProducerKind::SnapshotBuild => heads.snapshot = Some(head),
                ReplicaContentProducerKind::Log => {}
                ReplicaContentProducerKind::SnapshotInstall => {
                    return Err("unexpected original adoption source kind".into());
                }
            }
        }
        let mut connection = store.connection.lock().map_err(|error| error.to_string())?;
        {
            let transaction = connection
                .transaction_with_behavior(TransactionBehavior::Exclusive)
                .map_err(|error| error.to_string())?;
            intent.budget.check()?;
            require_path(path, intent.sql_identity)?;
            if self::source_digest(&transaction)? != intent.source_digest
                || content.legacy_producer_inventory_digest()? != intent.legacy_inventory_digest
            {
                return Err(
                    "original adoption source/legacy inventory changed before publication".into(),
                );
            }
            write_meta(&transaction, producers::HEADS_KEY, &heads)
                .map_err(|error| error.to_string())?;
            write_meta(
                &transaction,
                COMMIT_KEY,
                &AdoptionCommit {
                    operation_id: intent.operation_id.clone(),
                    intent_digest: digest(&intent)?,
                    producer_ids: ids.clone(),
                },
            )
            .map_err(|error| error.to_string())?;
            transaction.execute_batch("DROP TABLE replica_storage_format; CREATE TABLE replica_storage_format(version INTEGER PRIMARY KEY CHECK(version=6),schema TEXT NOT NULL); INSERT INTO replica_storage_format VALUES (6,'roboguide.replica-storage/v0.6'); PRAGMA user_version=6;").map_err(|error| error.to_string())?;
            store.validate_content_producer_heads(&transaction)?;
            intent.budget.check()?;
            transaction.commit().map_err(|error| error.to_string())?;
        }
        for permit in permits {
            let original = content
                .producer_status(permit.operation_id())?
                .ok_or("new adoption producer intent disappeared")?;
            let (present, proof) = store
                .producer_is_referenced(&connection, &original)
                .map_err(|error| error.to_string())?;
            if !present {
                return Err("actual adopted producer publication is absent".into());
            }
            permit.published(&proof)?;
        }
        let completed = intent.budget.check()?;
        require_path(path, intent.sql_identity)?;
        if self::source_digest(&connection)? != intent.source_digest {
            return Err("original adoption source changed after publication".into());
        }
        originals
            .last_mut()
            .ok_or("original adoption history disappeared")?
            .publication = Some(AdoptionPublication {
            producer_ids: ids,
            source_digest: intent.source_digest.clone(),
            completed_ms: completed,
        });
        let transaction = connection
            .transaction_with_behavior(TransactionBehavior::Exclusive)
            .map_err(|error| error.to_string())?;
        write_history(&transaction, &originals)?;
        intent.budget.check()?;
        transaction.commit().map_err(|error| error.to_string())?;
        status(
            originals
                .last()
                .ok_or("original adoption result disappeared")?,
        )
    }

    /// Observes every retained original adoption without modifying source data, pins or budgets.
    pub fn content_parent_adoption_history(
        &self,
    ) -> Result<Vec<ReplicaContentParentAdoptionStatus>, String> {
        let connection = self.connection.lock().map_err(|error| error.to_string())?;
        let version: i64 = connection
            .query_row("PRAGMA user_version", [], |row| row.get(0))
            .map_err(|error| error.to_string())?;
        history(&connection, version == ADOPTED_STORAGE_VERSION)?
            .iter()
            .map(status)
            .collect()
    }
}

/// Writes the complete bounded journal atomically; no dropped record becomes an empty command history.
fn write_history(connection: &Connection, records: &[AdoptionRecord]) -> Result<(), String> {
    let raw = serde_json::to_string(records).map_err(|error| error.to_string())?;
    if raw.len() > 32 * 1024 * 1024 {
        return Err("original adoption journal reached its fixed byte bound".into());
    }
    connection.execute("INSERT INTO replica_v1_meta(key,value) VALUES (?1,?2) ON CONFLICT(key) DO UPDATE SET value=excluded.value", params![HISTORY_KEY,raw]).map_err(|error| error.to_string())?;
    write_meta(
        connection,
        HISTORY_HEAD,
        &records
            .iter()
            .map(|record| digest(&record.intent))
            .collect::<Result<Vec<_>, _>>()?,
    )
    .map_err(|error| error.to_string())?;
    Ok(())
}
