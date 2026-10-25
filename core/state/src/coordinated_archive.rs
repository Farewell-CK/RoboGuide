//! Original online Archive maintenance; replicated metadata never substitutes for actual CAS bytes.

use super::*;
use serde::{Deserialize, Serialize};

/// Explicit original journal generation; older State writers refuse this marker.
pub const COORDINATED_ARCHIVE_STORAGE_SCHEMA: &str = "roboguide.archive-operations/v0.3";
/// Explicit recovery projection containing the complete coordinated journal, including empty history.
pub const COORDINATED_ARCHIVE_RECOVERY_SCHEMA: &str = "roboguide.application-recovery/v0.2";
/// Bound shared by admission, replay and immutable package verification.
const MAX_OPERATIONS: usize = 256;
/// Original metadata package bound, independent of any payload's declared size.
const MAX_PACKAGE_BYTES: usize = 128 * 1024 * 1024;

/// One exact original opaque CAS dependency; equal digests never merge independent owners.
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ArchiveContentKey {
    /// Actual original Artifact software owner.
    pub source_owner: String,
    /// Exact complete original bytes.
    pub content_digest: String,
    /// Exact complete original length.
    pub byte_size: u64,
}

/// Closed explicit command whose subject is supplied by the authenticated transport.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CoordinatedArchiveRequest {
    /// Original idempotent operation, never replaced on retry.
    pub command_id: String,
    /// Actual authenticated global recovery administrator.
    pub principal: String,
    /// Actual current application Artifact owner, independently checked by its adapter.
    pub source_owner: String,
    /// Exact positive checkpoint-covered prefix.
    pub end_sequence: u64,
    /// Original nonrenewable duration, at most five minutes.
    pub budget_ms: u64,
}

/// Immutable actual metadata package. Historical payload bytes are independent qualified inputs.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CoordinatedArchivePackage {
    /// Explicit portable package interpretation.
    pub schema: String,
    /// Original software State-store identity.
    pub authority_id: String,
    /// Exact original prefix and checkpoint identity.
    pub manifest: ArchiveManifest,
    /// Original complete checkpoint, preserving its raw nested JSON bytes.
    pub checkpoint: PersistedCheckpoint,
    /// Exact contiguous original prefix, retaining original timestamps and event identities.
    pub events: Vec<PersistedEvent>,
    /// Complete original protected Catalog evidence at first admission.
    pub catalog: Vec<PersistedEvent>,
    /// Identity of complete original auxiliary evidence before this operation was admitted.
    pub recovery_digest: String,
    /// Complete original current qualified requirements, never a placement or use grant.
    pub original_inputs: Vec<ArchiveContentKey>,
}

/// First admission survives missing replies, process exit and future original-epoch loss.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CoordinatedArchiveIntent {
    /// Explicit original operation meaning.
    pub schema: String,
    /// Complete actual command and its first duration.
    pub request: CoordinatedArchiveRequest,
    /// Original State identity, never regenerated from restored paths.
    pub authority_id: String,
    /// Actual current issuing writer/epoch, not a caller-declared term.
    pub issuing_epoch: String,
    /// Previous original history floor.
    pub previous_floor: u64,
    /// Exact original prefix and complete checkpoint identity.
    pub manifest: ArchiveManifest,
    /// Actual original source-owner metadata package identity, before any upload starts.
    pub package: ArchiveContentKey,
    /// Original complete protected Catalog identity.
    pub catalog_digest: String,
    /// Original auxiliary identity, without embedding recursively nested packages.
    pub recovery_digest: String,
    /// Actual original Linux boot identity; never device identity.
    pub boot_id: String,
    /// Actual original Linux time namespace.
    pub time_namespace: String,
    /// Original same-kernel admission sample.
    pub issued_ms: u64,
    /// Original nonrenewable deadline.
    pub deadline_ms: u64,
}

/// Independent rights-reducing abandonment; it never erases the original unknown publication.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CoordinatedArchiveAbort {
    /// Actual current administrator, independently authenticated at the new command boundary.
    pub principal: String,
    /// Original issuing epoch of this rights-reducing decision.
    pub issuing_epoch: String,
}

/// Complete retained operation; absent completion remains Unknown beside any explicit abandonment.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct CoordinatedArchiveRecord {
    /// Exact immutable first admission.
    pub intent: CoordinatedArchiveIntent,
    /// Original publication/compaction decision sample; never inferred from CAS presence.
    pub completed_ms: Option<u64>,
    /// Explicit independent abandonment; it cannot coexist with completed compaction.
    pub abort: Option<CoordinatedArchiveAbort>,
}

/// Actual source adapter; only first admission may upload, while retries only inspect SQL evidence.
pub trait CoordinatedArchiveSink {
    /// Rechecks original request permission immediately before each effect and commit.
    fn authorize(&self) -> Result<(), String>;
    /// Finalizes the actual original package in the owned CAS without creating semantic publication.
    fn publish(&self, intent: &CoordinatedArchiveIntent, bytes: &[u8]) -> Result<(), String>;
    /// Independently reads exact actual bytes before the complete replication transaction.
    fn verify(&self, key: &ArchiveContentKey) -> Result<(), String>;
}

/// Canonical metadata bytes retain original nested payload spelling and distinguish qualified owners.
pub fn coordinated_archive_bytes<T: Serialize + ?Sized>(value: &T) -> Result<Vec<u8>, String> {
    let bytes = serde_json::to_vec(&serde_json::to_value(value).map_err(|e| e.to_string())?)
        .map_err(|e| e.to_string())?;
    if bytes.len() > MAX_PACKAGE_BYTES {
        return Err("coordinated Archive metadata exceeds original bounded package".into());
    }
    Ok(bytes)
}

/// Computes the exact complete metadata digest without making an availability claim.
fn digest<T: Serialize + ?Sized>(value: &T) -> Result<String, String> {
    Ok(format!(
        "sha256:{:x}",
        Sha256::digest(coordinated_archive_bytes(value)?)
    ))
}

impl ArchiveContentKey {
    /// Rejects ambiguous identities and oversized declared payloads before actual file I/O.
    pub fn validate(&self) -> Result<(), String> {
        let hex = |value: &str| {
            value.len() == 64
                && value
                    .bytes()
                    .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        };
        if !self
            .source_owner
            .strip_prefix("artifact-store:")
            .is_some_and(hex)
            || !self.content_digest.strip_prefix("sha256:").is_some_and(hex)
            || self.byte_size > 4 * 1024 * 1024 * 1024
        {
            return Err("coordinated Archive original qualified content is invalid".into());
        }
        Ok(())
    }
}

impl CoordinatedArchiveRecord {
    /// Checks retained evidence only; this never refreshes a budget or tests the observer's epoch.
    pub fn validate(&self) -> Result<(), String> {
        let i = &self.intent;
        let valid = |value: &str, bound: usize| {
            !value.is_empty() && value.len() <= bound && !value.chars().any(char::is_control)
        };
        i.package.validate()?;
        super::archive::validate_manifest(&i.manifest)?;
        if i.schema != "roboguide.coordinated-archive-intent/v0.1"
            || !valid(&i.request.command_id, 128)
            || !valid(&i.request.principal, 256)
            || !valid(&i.issuing_epoch, 512)
            || !i
                .authority_id
                .strip_prefix("controller-store:")
                .is_some_and(|s| {
                    s.len() == 64
                        && s.bytes()
                            .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
                })
            || i.package.source_owner != i.request.source_owner
            || i.request.end_sequence != i.manifest.end_sequence
            || i.previous_floor.checked_add(1) != Some(i.manifest.start_sequence)
            || i.request.budget_ms < 100
            || i.request.budget_ms > 300_000
            || i.deadline_ms.checked_sub(i.issued_ms) != Some(i.request.budget_ms)
            || i.boot_id.len() != 36
            || !valid(&i.time_namespace, 128)
            || i.manifest.schema != "roboguide.event-archive/v2"
            || i.manifest.checkpoint_digest.is_none()
            || [i.catalog_digest.as_str(), i.recovery_digest.as_str()]
                .iter()
                .any(|d| {
                    !d.strip_prefix("sha256:").is_some_and(|s| {
                        s.len() == 64
                            && s.bytes()
                                .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
                    })
                })
            || i.package.byte_size > MAX_PACKAGE_BYTES as u64
            || self
                .completed_ms
                .is_some_and(|t| t < i.issued_ms || t >= i.deadline_ms)
            || (self.completed_ms.is_some() && self.abort.is_some())
            || self
                .abort
                .as_ref()
                .is_some_and(|a| !valid(&a.principal, 256) || !valid(&a.issuing_epoch, 512))
        {
            return Err(
                "coordinated Archive original identity, budget or disposition differs".into(),
            );
        }
        Ok(())
    }

    /// Keeps first admission and original completed/abandoned evidence immutable on every successor.
    pub fn follows(&self, old: &Self) -> Result<(), String> {
        self.validate()?;
        if self.intent != old.intent
            || old.completed_ms.is_some() && self != old
            || old.abort.is_some() && self != old
        {
            return Err("coordinated Archive successor rewrote original evidence".into());
        }
        Ok(())
    }
}

impl CoordinatedArchiveIntent {
    /// Rechecks the original same-kernel clock; restart/another host cannot renew it.
    pub fn require_budget(&self) -> Result<u64, String> {
        let (boot, namespace, now) =
            super::archive_operation::kernel().map_err(|e| e.to_string())?;
        if boot != self.boot_id
            || namespace != self.time_namespace
            || now < self.issued_ms
            || now.saturating_add(10) >= self.deadline_ms
        {
            return Err(
                "coordinated Archive original kernel budget is unavailable or expired".into(),
            );
        }
        Ok(now)
    }
}

/// Reads mandatory complete rows; a missing table/marker never becomes an invented empty journal.
pub(super) fn read(
    connection: &Connection,
) -> Result<Option<Vec<CoordinatedArchiveRecord>>, SqliteEventLogError> {
    let marker: Option<String> = connection
        .query_row(
            "SELECT value FROM event_log_metadata WHERE key='archive_operations_schema'",
            [],
            |r| r.get(0),
        )
        .optional()?;
    let exists: bool = connection.query_row(
        "SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE name='coordinated_archive_operations')",
        [],
        |r| r.get(0),
    )?;
    if marker.as_deref() != Some(COORDINATED_ARCHIVE_STORAGE_SCHEMA) {
        if exists {
            return Err(SqliteEventLogError::Codec(
                "coordinated Archive table lacks its original mandatory generation".into(),
            ));
        }
        return Ok(None);
    }
    if !exists {
        return Err(SqliteEventLogError::Codec(
            "mandatory coordinated Archive journal is missing".into(),
        ));
    }
    let (count, bytes): (u64, u64) = connection.query_row("SELECT COUNT(*),COALESCE(SUM(length(CAST(record_json AS BLOB))),0) FROM coordinated_archive_operations", [], |r| Ok((r.get(0)?,r.get(1)?)))?;
    if count > MAX_OPERATIONS as u64 || bytes > MAX_PACKAGE_BYTES as u64 {
        return Err(SqliteEventLogError::Codec(
            "coordinated Archive retained journal exceeds bounds".into(),
        ));
    }
    let mut statement = connection.prepare(
        "SELECT command_id,record_json FROM coordinated_archive_operations ORDER BY command_id",
    )?;
    let mut rows = Vec::new();
    for row in statement.query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, String>(1)?)))? {
        let (id, raw) = row?;
        let record: CoordinatedArchiveRecord =
            serde_json::from_str(&raw).map_err(|e| SqliteEventLogError::Codec(e.to_string()))?;
        record.validate().map_err(SqliteEventLogError::Codec)?;
        if id != record.intent.request.command_id {
            return Err(SqliteEventLogError::Codec(
                "coordinated Archive original row key differs".into(),
            ));
        }
        rows.push(record);
    }
    Ok(Some(rows))
}

/// Installs already validated complete original rows inside the owner's actual transaction.
pub(super) fn install(
    connection: &Connection,
    rows: Option<&[CoordinatedArchiveRecord]>,
) -> Result<(), SqliteEventLogError> {
    connection.execute_batch("DROP TABLE IF EXISTS coordinated_archive_operations")?;
    if let Some(rows) = rows {
        connection.execute_batch("CREATE TABLE coordinated_archive_operations(command_id TEXT PRIMARY KEY NOT NULL,record_json TEXT NOT NULL)")?;
        for row in rows {
            row.validate().map_err(SqliteEventLogError::Codec)?;
            connection.execute(
                "INSERT INTO coordinated_archive_operations VALUES (?1,?2)",
                params![
                    row.intent.request.command_id,
                    serde_json::to_string(row)
                        .map_err(|e| SqliteEventLogError::Codec(e.to_string()))?
                ],
            )?;
        }
    }
    Ok(())
}

/// Reconstructs exact immutable package metadata from the complete original predecessor.
pub fn coordinated_archive_package(
    original: &CompleteReplicaState,
    end: u64,
    inputs: Vec<ArchiveContentKey>,
) -> Result<CoordinatedArchivePackage, String> {
    original.validate().map_err(|e| e.to_string())?;
    let floor = original
        .recovery
        .history_floor()
        .map_err(|e| e.to_string())?;
    if end <= floor
        || end > original.checkpoint.event_sequence
        || end - floor > MAX_ARCHIVE_EVENTS
        || inputs.len() > 16_384
        || inputs.windows(2).any(|r| r[0] >= r[1])
    {
        return Err("coordinated Archive cutoff or original input bounds differ".into());
    }
    for key in &inputs {
        key.validate()?;
    }
    let events = original
        .events
        .iter()
        .filter(|r| r.sequence <= end)
        .cloned()
        .collect::<Vec<_>>();
    let mut segment = Vec::new();
    for row in &events {
        serde_json::to_writer(&mut segment, row).map_err(|e| e.to_string())?;
        segment.push(b'\n');
    }
    if segment.len() > super::archive::MAX_ARCHIVE_BYTES {
        return Err("coordinated Archive segment exceeds original bound".into());
    }
    let mut catalog = original
        .recovery
        .catalog_archive_events
        .clone()
        .unwrap_or_default();
    for event in &original.events {
        let payload: EventPayload =
            serde_json::from_str(&event.payload_json).map_err(|e| e.to_string())?;
        if super::archive::requires_catalog_history(&payload) {
            catalog.push(event.clone());
        }
    }
    let package = CoordinatedArchivePackage {
        schema: "roboguide.coordinated-archive-package/v0.1".into(),
        authority_id: original
            .recovery
            .metadata
            .get("archive_authority_id")
            .ok_or("original Archive authority is unavailable")?
            .clone(),
        manifest: ArchiveManifest {
            schema: "roboguide.event-archive/v2".into(),
            start_sequence: floor + 1,
            end_sequence: end,
            event_count: events.len() as u64,
            segment_digest: format!("sha256:{:x}", Sha256::digest(&segment)),
            checkpoint_sequence: original.checkpoint.event_sequence,
            checkpoint_digest: Some(checkpoint_identity(&original.checkpoint)),
        },
        checkpoint: original.checkpoint.clone(),
        events,
        catalog,
        recovery_digest: original.recovery.identity().map_err(|e| e.to_string())?,
        original_inputs: inputs,
    };
    super::archive::validate_manifest(&package.manifest)?;
    coordinated_archive_bytes(&package)?;
    Ok(package)
}

/// Applies only the exact verified prefix to an otherwise unchanged complete recovery projection.
pub fn coordinated_archive_projection(
    original: &CompleteReplicaState,
    record: &CoordinatedArchiveRecord,
) -> Result<ReplicaRecoveryProjection, String> {
    record.validate()?;
    let mut recovery = original.recovery.clone();
    recovery.schema = COORDINATED_ARCHIVE_RECOVERY_SCHEMA.into();
    recovery.metadata.insert(
        "archive_operations_schema".into(),
        COORDINATED_ARCHIVE_STORAGE_SCHEMA.into(),
    );
    let rows = recovery.coordinated_archives.get_or_insert_with(Vec::new);
    if let Some(index) = rows
        .iter()
        .position(|r| r.intent.request.command_id == record.intent.request.command_id)
    {
        record.follows(&rows[index])?;
        rows[index] = record.clone();
    } else {
        if rows.len() >= MAX_OPERATIONS
            || rows
                .iter()
                .any(|r| r.completed_ms.is_none() && r.abort.is_none())
        {
            return Err(
                "original pending Archive or retained capacity fences another admission".into(),
            );
        }
        rows.push(record.clone());
        rows.sort_by(|a, b| {
            a.intent
                .request
                .command_id
                .cmp(&b.intent.request.command_id)
        });
    }
    if record.completed_ms.is_some() {
        let end = record.intent.manifest.end_sequence;
        if original
            .recovery
            .history_floor()
            .map_err(|e| e.to_string())?
            != record.intent.previous_floor
        {
            return Err("coordinated Archive original floor changed".into());
        }
        let protected = recovery.catalog_archive_events.get_or_insert_with(Vec::new);
        for row in original.events.iter().filter(|r| r.sequence <= end) {
            let payload: EventPayload =
                serde_json::from_str(&row.payload_json).map_err(|e| e.to_string())?;
            if super::archive::requires_catalog_history(&payload) {
                protected.push(row.clone());
            }
        }
        let manifest_json =
            serde_json::to_string(&record.intent.manifest).map_err(|e| e.to_string())?;
        recovery
            .archive_manifests
            .get_or_insert_with(Vec::new)
            .push(super::replica_recovery::ReplicaArchiveManifest {
                start_sequence: record.intent.manifest.start_sequence,
                end_sequence: end,
                manifest_json: manifest_json.clone(),
            });
        recovery
            .metadata
            .insert("history_floor".into(), end.to_string());
        recovery.metadata.insert("catalog_archive_checkpoint".into(),serde_json::to_string(&serde_json::json!({"schema":"roboguide.catalog-archive-checkpoint/v0.1","through_sequence":end,"event_count":protected.len(),"digest":digest(protected)?})).map_err(|e|e.to_string())?);
        let old_time = recovery
            .metadata
            .get("archive_timestamp_floor")
            .map(|s| s.parse::<u64>())
            .transpose()
            .map_err(|e| e.to_string())?
            .unwrap_or(0);
        let timestamp = original
            .events
            .iter()
            .filter(|r| r.sequence <= end)
            .map(|r| r.timestamp_ms)
            .max()
            .unwrap_or(0)
            .max(old_time);
        recovery
            .metadata
            .insert("archive_timestamp_floor".into(), timestamp.to_string());
        recovery
            .metadata
            .insert("archive_manifest".into(), manifest_json);
    }
    let floor = recovery.history_floor().map_err(|e| e.to_string())?;
    let events = original
        .events
        .iter()
        .filter(|r| r.sequence > floor)
        .cloned()
        .collect::<Vec<_>>();
    recovery
        .validate_for(&events, &original.checkpoint)
        .map_err(|e| e.to_string())?;
    Ok(recovery)
}

impl SqliteEventLog {
    /// Reports only explicit startup capability; old/default profiles avoid new metadata scans.
    pub fn coordinated_archive_enabled(&self) -> bool {
        self.replicator
            .as_ref()
            .is_some_and(|p| p.supports_coordinated_archive())
    }

    /// Reads the complete original coordinated journal without publication, retry or budget renewal.
    pub fn coordinated_archive_operations(
        &self,
    ) -> Result<Vec<CoordinatedArchiveRecord>, SqliteEventLogError> {
        self.ensure_no_open_batch()?;
        let connection = self
            .connection
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        Ok(read(&connection)?.unwrap_or_default())
    }

    /// Commits one unchanged-checkpoint maintenance projection through its original complete quorum.
    fn commit_coordinated_archive(
        &self,
        original: &CompleteReplicaState,
        recovery: &ReplicaRecoveryProjection,
        permission: &dyn Fn() -> Result<(), String>,
    ) -> Result<(), SqliteEventLogError> {
        self.require_writes_enabled()?;
        permission().map_err(SqliteEventLogError::ArchivePolicy)?;
        let replicator = self.replicator.as_ref().ok_or_else(|| {
            SqliteEventLogError::ArchivePolicy(
                "coordinated Archive requires its explicit original replica port".into(),
            )
        })?;
        let mut connection = self
            .connection
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        if super::replica_recovery::complete(&connection)? != *original {
            return Err(SqliteEventLogError::ArchivePolicy(
                "coordinated Archive original committed predecessor changed".into(),
            ));
        }
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        super::replica_recovery::install(&transaction, recovery)?;
        transaction.execute(
            &format!("DELETE FROM {} WHERE sequence<=?1", self.events_table),
            [recovery.history_floor()?],
        )?;
        let batch = ApplicationCommitBatch {
            schema: APPLICATION_COMMIT_SCHEMA.into(),
            previous_event_sequence: original.checkpoint.event_sequence,
            previous_checkpoint_digest: Some(checkpoint_identity(&original.checkpoint)),
            checkpoint: original.checkpoint.clone(),
            events: Vec::new(),
            commands: Vec::new(),
        };
        permission().map_err(SqliteEventLogError::ArchivePolicy)?;
        replicator
            .confirm_recovery_batch(batch, recovery.clone())
            .map_err(SqliteEventLogError::Replication)?;
        // A lost/late complete reply cannot let an expired original command or caller commit local
        // materialization. The already-submitted global outcome stays uncertain until startup replay.
        permission().map_err(SqliteEventLogError::Replication)?;
        let sql_witness = timer_transaction::SqlWitness::observe(&transaction)?;
        transaction.commit()?;
        *self
            .committed_sql_witness
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)? = Some(sql_witness);
        Ok(())
    }

    /// Publishes one original immutable package, then advances only its verified complete prefix.
    pub fn archive_coordinated<S: CoordinatedArchiveSink>(
        &self,
        request: CoordinatedArchiveRequest,
        sink: &S,
    ) -> Result<CoordinatedArchiveRecord, SqliteEventLogError> {
        self.observe_write_result(self.archive_coordinated_inner(request, sink))
    }

    /// Holds the caller's actual application gate across original admission, bounded I/O and Commit.
    fn archive_coordinated_inner<S: CoordinatedArchiveSink>(
        &self,
        request: CoordinatedArchiveRequest,
        sink: &S,
    ) -> Result<CoordinatedArchiveRecord, SqliteEventLogError> {
        self.ensure_no_open_batch()?;
        self.require_writes_enabled()?;
        let replicator = self
            .replicator
            .as_ref()
            .filter(|p| p.supports_coordinated_archive())
            .ok_or_else(|| {
                SqliteEventLogError::ArchivePolicy(
                    "coordinated Archive is not explicitly negotiated by this deployment".into(),
                )
            })?;
        let original = {
            let connection = self
                .connection
                .lock()
                .map_err(|_| SqliteEventLogError::LockPoisoned)?;
            super::replica_recovery::complete(&connection)?
        };
        if let Some(row) = original
            .recovery
            .coordinated_archives
            .as_ref()
            .and_then(|rows| {
                rows.iter()
                    .find(|r| r.intent.request.command_id == request.command_id)
            })
        {
            let mut original_request = request.clone();
            // The public retry never supplies an owner. A new actual leader may only observe the
            // original source; it must not relabel that source as its own current Artifact store.
            original_request.source_owner = row.intent.request.source_owner.clone();
            if row.intent.request != original_request {
                return Err(SqliteEventLogError::ArchivePolicy(
                    "coordinated Archive original command identity conflicts".into(),
                ));
            }
            return Ok(row.clone());
        }
        sink.authorize()
            .map_err(SqliteEventLogError::ArchivePolicy)?;
        let package = coordinated_archive_package(
            &original,
            request.end_sequence,
            replicator
                .archive_inputs()
                .map_err(SqliteEventLogError::Replication)?,
        )
        .map_err(SqliteEventLogError::ArchivePolicy)?;
        let bytes =
            coordinated_archive_bytes(&package).map_err(SqliteEventLogError::ArchivePolicy)?;
        let (boot_id, time_namespace, issued_ms) = super::archive_operation::kernel()?;
        let deadline_ms = issued_ms.checked_add(request.budget_ms).ok_or_else(|| {
            SqliteEventLogError::ArchivePolicy("coordinated Archive deadline overflow".into())
        })?;
        let intent = CoordinatedArchiveIntent {
            schema: "roboguide.coordinated-archive-intent/v0.1".into(),
            authority_id: package.authority_id.clone(),
            issuing_epoch: self.current_writer_epoch()?,
            previous_floor: original.recovery.history_floor()?,
            manifest: package.manifest.clone(),
            package: ArchiveContentKey {
                source_owner: request.source_owner.clone(),
                content_digest: format!("sha256:{:x}", Sha256::digest(&bytes)),
                byte_size: bytes.len() as u64,
            },
            catalog_digest: digest(&package.catalog).map_err(SqliteEventLogError::ArchivePolicy)?,
            recovery_digest: package.recovery_digest.clone(),
            request,
            boot_id,
            time_namespace,
            issued_ms,
            deadline_ms,
        };
        let mut record = CoordinatedArchiveRecord {
            intent,
            completed_ms: None,
            abort: None,
        };
        let pending = coordinated_archive_projection(&original, &record)
            .map_err(SqliteEventLogError::ArchivePolicy)?;
        self.commit_coordinated_archive(&original, &pending, &|| {
            record.intent.require_budget()?;
            sink.authorize()
        })?;
        record
            .intent
            .require_budget()
            .map_err(SqliteEventLogError::ArchivePolicy)?;
        sink.authorize()
            .map_err(SqliteEventLogError::ArchivePolicy)?;
        sink.publish(&record.intent, &bytes)
            .map_err(SqliteEventLogError::ArchiveSink)?;
        sink.verify(&record.intent.package)
            .map_err(SqliteEventLogError::ArchiveSink)?;
        sink.authorize()
            .map_err(SqliteEventLogError::ArchivePolicy)?;
        if self.current_writer_epoch()? != record.intent.issuing_epoch {
            return Err(SqliteEventLogError::ArchivePolicy(
                "original Archive issuing epoch no longer owns publication".into(),
            ));
        }
        record.completed_ms = Some(
            record
                .intent
                .require_budget()
                .map_err(SqliteEventLogError::ArchivePolicy)?,
        );
        let mut admitted = original;
        admitted.recovery = pending;
        let completed = coordinated_archive_projection(&admitted, &record)
            .map_err(SqliteEventLogError::ArchivePolicy)?;
        self.commit_coordinated_archive(&admitted, &completed, &|| {
            record.intent.require_budget()?;
            sink.authorize()
        })?;
        Ok(record)
    }

    /// Abandons only an unresolved original operation; original Unknown evidence and bytes remain.
    pub fn abort_coordinated_archive(
        &self,
        command: &str,
        principal: &str,
        permission: &dyn Fn() -> Result<(), String>,
    ) -> Result<CoordinatedArchiveRecord, SqliteEventLogError> {
        self.ensure_no_open_batch()?;
        self.require_writes_enabled()?;
        if self
            .replicator
            .as_ref()
            .is_none_or(|p| !p.supports_coordinated_archive())
        {
            return Err(SqliteEventLogError::ArchivePolicy(
                "coordinated Archive is not negotiated".into(),
            ));
        }
        let original = {
            let connection = self
                .connection
                .lock()
                .map_err(|_| SqliteEventLogError::LockPoisoned)?;
            super::replica_recovery::complete(&connection)?
        };
        let mut row = original
            .recovery
            .coordinated_archives
            .as_ref()
            .and_then(|rows| rows.iter().find(|r| r.intent.request.command_id == command))
            .cloned()
            .ok_or_else(|| {
                SqliteEventLogError::ArchivePolicy(
                    "original coordinated Archive is unavailable".into(),
                )
            })?;
        if row.completed_ms.is_some() {
            return Err(SqliteEventLogError::ArchivePolicy(
                "completed original Archive cannot be abandoned".into(),
            ));
        }
        if row.abort.is_some() {
            return Ok(row);
        }
        row.abort = Some(CoordinatedArchiveAbort {
            principal: principal.into(),
            issuing_epoch: self.current_writer_epoch()?,
        });
        let next = coordinated_archive_projection(&original, &row)
            .map_err(SqliteEventLogError::ArchivePolicy)?;
        self.observe_write_result(self.commit_coordinated_archive(&original, &next, permission))?;
        Ok(row)
    }
}
