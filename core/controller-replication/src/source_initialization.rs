//! Parent9 member-local original initialization intent/capture/result, never imported by Raft snapshots.

use crate::content_store::ContentBudget;
use crate::*;
use rusqlite::{Connection, OptionalExtension};
use serde::{Deserialize, Serialize};
use std::path::PathBuf;

/// Mandatory local operation inventory; an empty accepted inventory differs from missing authority.
pub(crate) const HEAD_KEY: &str = "content_initialization_ids";
/// Original full candidate metadata shares the existing explicit large-message bound.
const MAX_RECORD_BYTES: u64 = 256 * 1024 * 1024;

/// Explicit local source paths; callers never send these through member RPC as filesystem authority.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ContentInitializationInput {
    /// Original existing absolute Controller SQL file.
    pub source_sql: PathBuf,
    /// Original existing absolute managed Artifact root.
    pub source_cas: PathBuf,
}

/// Readonly original source metadata observed under its actual SQL/CAS leases, never current quorum.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ContentInitializationSourceObservation {
    /// Closed readonly metadata interpretation.
    pub schema: String,
    /// Exact independently recomputable original complete SQL state identity.
    pub source_state_digest: String,
    /// Actual existing original managed CAS owner.
    pub source_owner: String,
    /// Original actual archived prefix, without synthesized event rows.
    pub history_floor: u64,
    /// Exact original checkpoint sequence, including its real hot tail.
    pub event_sequence: u64,
    /// Exact original last command revision, independent of future Replica revisions.
    pub authority_revision: u64,
}

impl ContentInitializationInput {
    /// Checks declaration shape without opening, normalizing or creating original source files.
    pub fn validate(&self) -> Result<(), String> {
        for path in [&self.source_sql, &self.source_cas] {
            if !path.is_absolute()
                || path.as_os_str().len() > 4096
                || path
                    .components()
                    .any(|value| matches!(value, std::path::Component::ParentDir))
            {
                return Err(
                    "initialization requires explicit bounded original source paths".into(),
                );
            }
        }
        Ok(())
    }
}

/// Exact first local declaration, durable before source snapshot/payload opening.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct InitializationIntent {
    /// Closed original local interpretation.
    pub(crate) schema: String,
    /// Exact original configured cluster/application identity.
    pub(crate) profile_identity: String,
    /// Original logical Root; restored backing remains independent.
    pub(crate) root: ReplicaContentIdentity,
    /// Original configured complete source, owner, member, operation and duration.
    pub(crate) policy: ContentInitializationPolicy,
    /// Exact original local paths, never replaced on same-ID retry.
    pub(crate) input: ContentInitializationInput,
    /// Original issuing infrastructure epoch.
    pub(crate) epoch: ControllerEpoch,
    /// Original actual process/kernel budget covering queue, source, bytes and result.
    pub(crate) budget: ContentBudget,
}

/// Original complete source captured once before its data-transfer/commit effects.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct CapturedInitialization {
    /// Original exact proposal, including original source writer and immutable namespace bindings.
    pub(crate) candidate: Box<ContentApplicationBatch>,
    /// Original successful capture publication sample; never reconstructed after restart.
    pub(crate) captured_ms: u64,
}

/// Actual first complete original quorum return, independent of later readonly observations.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct InitializationCompletion {
    /// Actual complete original commit return, not a caller readiness boolean.
    pub(crate) receipt: CommitReceipt,
    /// Actual original same-member complete-content witnesses returned by the maintained path.
    pub(crate) complete_members: BTreeSet<u64>,
    /// Original actual completion sample, within the original first budget.
    pub(crate) completed_ms: u64,
}

/// One original local operation; missing captured/result values remain uncertain after process loss.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct InitializationRecord {
    /// Complete original pre-I/O declaration.
    pub(crate) intent: InitializationIntent,
    /// Canonical exact original declaration identity.
    pub(crate) intent_digest: String,
    /// Original once-only complete capture.
    pub(crate) captured: Option<CapturedInitialization>,
    /// Original actual completed return; absence never becomes success from a current view.
    pub(crate) completion: Option<InitializationCompletion>,
}

/// Bounded readonly original status; current local metadata is separate from the original outcome.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ContentInitializationStatus {
    /// Closed original status interpretation.
    pub schema: String,
    /// Exact immutable original operation.
    pub command_id: String,
    /// Exact original declaration hash.
    pub intent_digest: String,
    /// Original first budget, retained without sampling a new clock.
    pub original_budget: serde_json::Value,
    /// Whether the original full proposal is durably present.
    pub source_captured: bool,
    /// Original actual first result only; None remains Unknown.
    pub original_receipt: Option<CommitReceipt>,
    /// Independent current local applied lineage; it proves no fresh quorum or byte availability.
    pub current_local_anchor: Option<ContentInitializationAnchor>,
}

/// Only a newly durable intent can authorize source reads; retries expose original evidence only.
pub(crate) enum InitializationAdmission {
    /// Original actual first admission, owned by one initialization worker.
    New(Box<InitializationRecord>),
    /// Exact existing operation, including original Unknown and expired budgets.
    Existing(Box<ContentInitializationStatus>),
}

/// Canonically hashes original declarations without treating a retained digest as current authority.
pub(crate) fn identity(value: &impl Serialize) -> Result<String, String> {
    let raw = serde_json::to_vec(&serde_json::to_value(value).map_err(|error| error.to_string())?)
        .map_err(|error| error.to_string())?;
    Ok(artifact_store::digest_bytes(&raw))
}

/// Verifies only retained original shape and source relationships, without renewing old clocks.
pub(crate) fn validate_record(record: &InitializationRecord) -> Result<(), String> {
    let intent = &record.intent;
    intent.policy.validate()?;
    intent.input.validate()?;
    intent.budget.validate()?;
    if intent.schema != "roboguide.content-initialization-intent/v0.1"
        || record.intent_digest != identity(intent)?
        || intent.profile_identity != intent.root.profile_identity
        || intent.root.member != intent.policy.source_member
        || intent.epoch.replica_id != intent.root.member
        || intent.epoch.term == 0
        || intent.budget.duration_ms != intent.policy.budget_ms
    {
        return Err("original source initialization declaration/identity differs".into());
    }
    if let Some(captured) = &record.captured {
        intent.policy.require_candidate(&captured.candidate)?;
        captured
            .candidate
            .validate_against(&ReplicaView::default(), &intent.profile_identity)?;
        if captured.candidate.epoch != intent.epoch
            || !intent.budget.validates_completion(captured.captured_ms)
        {
            return Err("original source capture changed its first epoch/budget".into());
        }
    }
    if let Some(completed) = &record.completion {
        let captured = record
            .captured
            .as_ref()
            .ok_or("initialization completed without original capture")?;
        if !completed.receipt.accepted
            || completed.receipt.revision == 0
            || completed.receipt.checkpoint_digest.as_deref()
                != Some(state::checkpoint_identity(&captured.candidate.batch.checkpoint).as_str())
            || completed.complete_members.is_empty()
            || !intent.budget.validates_completion(completed.completed_ms)
            || completed.completed_ms < captured.captured_ms
        {
            return Err(
                "original initialization result changed original source/clock evidence".into(),
            );
        }
    }
    Ok(())
}

/// Creates the mandatory member-local table/head only during explicit fresh or drained parent9 setup.
pub(crate) fn initialize_storage(sql: &Connection) -> Result<(), String> {
    sql.execute_batch(
        "CREATE TABLE content_initializations(command_id TEXT PRIMARY KEY,record TEXT NOT NULL)",
    )
    .map_err(|error| error.to_string())?;
    sql.execute(
        "INSERT INTO replica_v1_meta(key,value) VALUES (?1,'[]')",
        [HEAD_KEY],
    )
    .map_err(|error| error.to_string())?;
    Ok(())
}

/// Reads one bounded actual original row; a missing/oversized row never grants another attempt.
pub(crate) fn record(
    sql: &Connection,
    command: &str,
) -> Result<Option<InitializationRecord>, String> {
    let raw: Option<String> = sql.query_row(
        "SELECT CASE WHEN length(CAST(record AS BLOB))<=?2 THEN record ELSE NULL END FROM content_initializations WHERE command_id=?1",
        rusqlite::params![command, MAX_RECORD_BYTES],
        |row| row.get::<_, Option<String>>(0)?.ok_or(rusqlite::Error::InvalidQuery),
    ).optional().map_err(|error| error.to_string())?;
    raw.map(|raw| {
        let original: InitializationRecord =
            serde_json::from_str(&raw).map_err(|error| error.to_string())?;
        validate_record(&original)?;
        if original.intent.policy.command_id != command {
            return Err("original initialization SQL key differs".into());
        }
        Ok(original)
    })
    .transpose()
}

/// Requires complete mandatory local table/head/rows, preserving original root, policy and Unknown.
pub(crate) fn validate_storage(
    sql: &Connection,
    root: Option<&ReplicaContentIdentity>,
    profile: &str,
    policy: Option<&ContentInitializationPolicy>,
    enabled: bool,
) -> Result<(), String> {
    let table: Option<String> = sql
        .query_row(
            "SELECT type FROM sqlite_master WHERE name='content_initializations'",
            [],
            |row| row.get(0),
        )
        .optional()
        .map_err(|error| error.to_string())?;
    let head: Option<String> = sql
        .query_row(
            "SELECT value FROM replica_v1_meta WHERE key=?1",
            [HEAD_KEY],
            |row| row.get(0),
        )
        .optional()
        .map_err(|error| error.to_string())?;
    if !enabled {
        if table.is_some() || head.is_some() {
            return Err("old parent contains hidden initialization operations".into());
        }
        return Ok(());
    }
    if table.as_deref() != Some("table") {
        return Err("parent9 initialization operation table is missing".into());
    }
    let ids: Vec<String> = serde_json::from_str(
        head.as_deref()
            .ok_or("parent9 initialization inventory is missing")?,
    )
    .map_err(|error| error.to_string())?;
    if ids.len() > 1 {
        return Err("original initialization inventory exceeds its declared singleton".into());
    }
    let columns = sql
        .prepare("PRAGMA table_info(content_initializations)")
        .map_err(|error| error.to_string())?
        .query_map([], |row| {
            Ok((
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, i64>(3)?,
                row.get::<_, i64>(5)?,
            ))
        })
        .map_err(|error| error.to_string())?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|error| error.to_string())?;
    if columns
        != vec![
            ("command_id".into(), "TEXT".into(), 0, 1),
            ("record".into(), "TEXT".into(), 1, 0),
        ]
    {
        return Err("original initialization table columns differ".into());
    }
    let rows = sql
        .prepare("SELECT command_id FROM content_initializations ORDER BY command_id LIMIT 2")
        .map_err(|error| error.to_string())?
        .query_map([], |row| row.get::<_, String>(0))
        .map_err(|error| error.to_string())?
        .collect::<Result<Vec<_>, _>>()
        .map_err(|error| error.to_string())?;
    if rows != ids {
        return Err("original initialization inventory lost its operation".into());
    }
    for command in ids {
        let original =
            record(sql, &command)?.ok_or("original initialization operation disappeared")?;
        if Some(&original.intent.root) != root
            || original.intent.profile_identity != profile
            || Some(&original.intent.policy) != policy
        {
            return Err("original initialization operation changed root/profile/policy".into());
        }
    }
    Ok(())
}

/// Projects bounded original evidence and separate local applied lineage, never a new effect or clock.
pub(crate) fn status(
    record: &InitializationRecord,
    view: &ReplicaView,
) -> Result<ContentInitializationStatus, String> {
    validate_record(record)?;
    Ok(ContentInitializationStatus {
        schema: "roboguide.content-initialization-status/v0.1".into(),
        command_id: record.intent.policy.command_id.clone(),
        intent_digest: record.intent_digest.clone(),
        original_budget: serde_json::to_value(&record.intent.budget)
            .map_err(|error| error.to_string())?,
        source_captured: record.captured.is_some(),
        original_receipt: record
            .completion
            .as_ref()
            .map(|completed| completed.receipt.clone()),
        current_local_anchor: view
            .content
            .as_ref()
            .and_then(|content| content.source_initialization.clone()),
    })
}
