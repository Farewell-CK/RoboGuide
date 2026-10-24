//! Exact original Archive maintenance intent and content requirements, separate from application authority.

use super::*;
use std::collections::BTreeMap;

/// Mandatory current-wrapper operation storage marker, including original zero-operation history.
const STORAGE_SCHEMA: &str = "roboguide.archive-operations/v0.1";
/// Maximum one immutable original intent, including its complete checkpoint.
const MAX_INTENT_BYTES: usize = 128 * 1024 * 1024;

/// Introduces the mandatory empty journal only while upgrading from an older complete wrapper.
pub(super) fn initialize(
    connection: &Connection,
    accepted: bool,
) -> Result<(), SqliteEventLogError> {
    let marker: Option<String> = connection
        .query_row(
            "SELECT value FROM event_log_metadata WHERE key='archive_operations_schema'",
            [],
            |row| row.get(0),
        )
        .optional()?;
    if accepted || marker.as_deref() == Some(super::archive_abort::STORAGE_SCHEMA) {
        require_storage(connection)?;
        return super::archive_abort::validate_storage(connection);
    }
    match marker.as_deref() {
        Some(STORAGE_SCHEMA) => {
            require_storage(connection)?;
            let mut statement = connection
                .prepare("SELECT command_id FROM event_archive_operations ORDER BY command_id")?;
            let ids = statement
                .query_map([], |row| row.get::<_, String>(0))?
                .collect::<Result<Vec<_>, _>>()?;
            drop(statement);
            for id in ids {
                existing(connection, &id)?;
            }
        }
        None => {
            let evidence:bool=connection.query_row("SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE name IN ('event_archive_operations','event_archive_aborts')) OR EXISTS(SELECT 1 FROM event_log_metadata WHERE key='archive_authority_id')",[],|row|row.get(0))?;
            if evidence {
                return Err(SqliteEventLogError::Codec(
                    "original Archive storage marker is missing; replacement authority refused"
                        .into(),
                ));
            }
            connection.execute_batch("CREATE TABLE event_archive_operations(command_id TEXT PRIMARY KEY NOT NULL,intent_json TEXT NOT NULL,state TEXT NOT NULL,receipt_json TEXT)")?;
            use std::io::Read as _;
            let mut nonce = [0u8; 32];
            std::fs::File::open("/dev/urandom")
                .and_then(|mut file| file.read_exact(&mut nonce))
                .map_err(|error| SqliteEventLogError::Codec(error.to_string()))?;
            let hex = nonce
                .iter()
                .map(|value| format!("{value:02x}"))
                .collect::<String>();
            connection.execute(
                "INSERT INTO event_log_metadata(key,value) VALUES ('archive_authority_id',?1)",
                [format!("controller-store:{hex}")],
            )?;
        }
        _ => {
            return Err(SqliteEventLogError::Codec(
                "unsupported original Archive operations authority".into(),
            ));
        }
    }
    connection.execute_batch("CREATE TABLE event_archive_aborts(command_id TEXT PRIMARY KEY NOT NULL,original_command_id TEXT NOT NULL,intent_json TEXT NOT NULL,receipt_json TEXT)")?;
    connection.execute("INSERT INTO event_log_metadata(key,value) VALUES ('archive_operations_schema',?1) ON CONFLICT(key) DO UPDATE SET value=excluded.value",[super::archive_abort::STORAGE_SCHEMA])?;
    require_storage(connection)?;
    super::archive_abort::validate_storage(connection)
}

/// Preserves original v0.1 reads while requiring the complete explicit v0.2 journal when present.
pub(super) fn require_storage(connection: &Connection) -> Result<(), SqliteEventLogError> {
    let marker: Option<String> = connection
        .query_row(
            "SELECT value FROM event_log_metadata WHERE key='archive_operations_schema'",
            [],
            |row| row.get(0),
        )
        .optional()?;
    if !matches!(
        marker.as_deref(),
        Some(STORAGE_SCHEMA) | Some(super::archive_abort::STORAGE_SCHEMA)
    ) {
        return Err(SqliteEventLogError::Codec(
            "mandatory original Archive journal marker is missing or changed".into(),
        ));
    }
    authority_id(connection)?;
    let (count,bytes):(u64,u64)=connection.query_row("SELECT COUNT(*),COALESCE(SUM(length(CAST(intent_json AS BLOB))),0) FROM event_archive_operations",[],|row|Ok((row.get(0)?,row.get(1)?)))?;
    if count > 4096 || bytes > 256 * 1024 * 1024 {
        return Err(SqliteEventLogError::Codec(
            "original Archive journal exceeds its retained bounds".into(),
        ));
    }
    if marker.as_deref() == Some(super::archive_abort::STORAGE_SCHEMA) {
        super::archive_abort::require_storage(connection)?;
    }
    Ok(())
}

/// Reads the original persistent software-store identity without synthesizing a replacement.
fn authority_id(connection: &Connection) -> Result<String, SqliteEventLogError> {
    let value: String = connection.query_row(
        "SELECT value FROM event_log_metadata WHERE key='archive_authority_id'",
        [],
        |row| row.get(0),
    )?;
    let hex = value.strip_prefix("controller-store:").unwrap_or("");
    if hex.len() != 64
        || !hex
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err(SqliteEventLogError::Codec(
            "original Controller Archive identity is unavailable".into(),
        ));
    }
    Ok(value)
}

/// Actual transport/composition attribution supplied under the original application writer.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ArchiveRequest {
    /// Original idempotent maintenance command; retained forever within bounded history.
    pub command_id: String,
    /// Actual authenticated principal, never a request-body subject.
    pub principal: String,
    /// Actual original tenant scope; global maintenance is explicit.
    pub tenant: String,
    /// Actual original CAS writer-owned software backend identity.
    pub source_owner: String,
    /// Actual original Controller SQL-store identity, retained across restart and cold restore.
    pub authority_id: String,
    /// Actual declared archive root, fixed before effects and not a caller-selected URI.
    pub destination: String,
    /// Original nonrenewable actual kernel budget, at most five minutes.
    pub budget_ms: u64,
}

/// One exact opaque CAS dependency; ownership stays on its enclosing original intent.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ArchiveContent {
    /// Complete original opaque content identity.
    pub content_digest: String,
    /// Exact original complete byte length.
    pub byte_size: u64,
}

/// Original intent committed before archive I/O; uncertain work cannot acquire another budget.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ArchiveIntent {
    /// Closed operation contract, independent of a lifecycle terminal string.
    pub schema: String,
    /// Exact first authenticated command and destination/source identity.
    pub request: ArchiveRequest,
    /// Original immutable event prefix and checkpoint identity.
    pub manifest: super::archive::ArchiveManifest,
    /// Complete original application authority; the archive is not a replacement live writer.
    pub checkpoint: PersistedCheckpoint,
    /// Canonical complete original Catalog recovery evidence identity.
    pub catalog_digest: String,
    /// Exact original source-owner bytes retained before the first read/copy.
    pub inputs: Vec<ArchiveContent>,
    /// Actual Linux kernel boot identity, never device identity.
    pub boot_id: String,
    /// Actual original process time namespace, not wall time or a remote clock.
    pub time_namespace: String,
    /// First kernel uptime sample; intentionally conservative across suspend/restart.
    pub issued_ms: u64,
    /// Original kernel deadline, never renewed by query/restart.
    pub deadline_ms: u64,
}

/// Reads actual original kernel identity and uptime without introducing wall-clock fallback.
pub(super) fn kernel() -> Result<(String, String, u64), SqliteEventLogError> {
    let boot = std::fs::read_to_string("/proc/sys/kernel/random/boot_id")
        .map_err(|error| SqliteEventLogError::ArchivePolicy(error.to_string()))?
        .trim()
        .to_string();
    let namespace = std::fs::read_link("/proc/self/ns/time")
        .map_err(|error| SqliteEventLogError::ArchivePolicy(error.to_string()))?
        .to_string_lossy()
        .to_string();
    let uptime = std::fs::read_to_string("/proc/uptime")
        .map_err(|error| SqliteEventLogError::ArchivePolicy(error.to_string()))?;
    let (seconds, fraction) = uptime
        .split_whitespace()
        .next()
        .and_then(|value| value.split_once('.'))
        .ok_or_else(|| {
            SqliteEventLogError::ArchivePolicy("original kernel uptime unavailable".into())
        })?;
    let seconds = seconds
        .parse::<u64>()
        .map_err(|error| SqliteEventLogError::ArchivePolicy(error.to_string()))?;
    if fraction.len() < 2
        || !fraction.bytes().all(|byte| byte.is_ascii_digit())
        || boot.len() != 36
        || namespace.len() > 128
    {
        return Err(SqliteEventLogError::ArchivePolicy(
            "original kernel identity invalid".into(),
        ));
    }
    let fraction = format!("{fraction:0<3}")[..3]
        .parse::<u64>()
        .map_err(|error| SqliteEventLogError::ArchivePolicy(error.to_string()))?;
    let now = seconds
        .checked_mul(1000)
        .and_then(|value| value.checked_add(fraction))
        .ok_or_else(|| {
            SqliteEventLogError::ArchivePolicy("original kernel uptime overflow".into())
        })?;
    Ok((boot, namespace, now))
}

/// Derives exact actual CAS requirements from complete original catalog and accepted proof evidence.
pub(super) fn inputs(
    source_owner: &str,
    catalog: &[PersistedEvent],
    events: &[PersistedEvent],
) -> Result<Vec<ArchiveContent>, SqliteEventLogError> {
    let mut retained = crate::ArtifactRetentionProjection::default();
    let mut publications = BTreeMap::new();
    let mut uploads = BTreeMap::new();
    let mut retired_uploads = BTreeMap::new();
    let mut recoveries = BTreeMap::new();
    let mut blobs = BTreeMap::new();
    let mut add = |digest: String, size: u64| -> Result<(), SqliteEventLogError> {
        if let Some(old) = blobs.insert(digest, size)
            && old != size
        {
            return Err(SqliteEventLogError::Codec(
                "Archive original same-hash byte sizes conflict".into(),
            ));
        }
        Ok(())
    };
    for row in catalog {
        let payload: EventPayload = serde_json::from_str(&row.payload_json)
            .map_err(|error| SqliteEventLogError::Codec(error.to_string()))?;
        retained
            .observe_publication(&EventRecord::new(
                EventId::new(row.event_id.clone())
                    .map_err(|error| SqliteEventLogError::Codec(error.to_string()))?,
                TimestampMs::new(row.timestamp_ms),
                CorrelationId::new(row.correlation_id.clone())
                    .map_err(|error| SqliteEventLogError::Codec(error.to_string()))?,
                row.causation_id
                    .clone()
                    .map(EventId::new)
                    .transpose()
                    .map_err(|error| SqliteEventLogError::Codec(error.to_string()))?,
                payload.clone(),
            ))
            .map_err(SqliteEventLogError::Codec)?;
        match payload {
            EventPayload::ArtifactUploadTransition { record } => {
                uploads.insert(record.upload_id.clone(), record);
            }
            EventPayload::ArtifactUploadRecoveryTransition { record } => {
                crate::validate_upload_recovery(&uploads, &recoveries, &record)
                    .map_err(SqliteEventLogError::Codec)?;
                recoveries.insert(record.command_id.clone(), *record);
            }
            EventPayload::ArtifactUploadRetired { receipt } => {
                crate::validate_upload_retirement(&uploads, &retired_uploads, &receipt)
                    .map_err(SqliteEventLogError::Codec)?;
                if receipt.store_owner_id != source_owner {
                    return Err(SqliteEventLogError::ArchivePolicy(
                        "upload retirement names another actual source owner".into(),
                    ));
                }
                retired_uploads.insert(receipt.upload.upload_id.clone(), receipt);
            }
            EventPayload::ArtifactCatalogReferenceTransition { record } => retained
                .apply(&record)
                .map_err(SqliteEventLogError::Codec)?,
            EventPayload::ArtifactCatalogLegacyAdopted { receipt } => retained
                .apply_adoption(&receipt)
                .map_err(SqliteEventLogError::Codec)?,
            EventPayload::MemoryManifestPublished { manifest } => {
                publications.insert(manifest.selector().clone(), manifest);
            }
            EventPayload::MapArtifactPublished { manifest } => {
                if let Ok(generic) = domain::map_memory_manifest(&manifest) {
                    publications.insert(generic.selector().clone(), generic);
                } else {
                    add(
                        manifest.artifact().content_digest().as_str().into(),
                        manifest.artifact().byte_size(),
                    )?;
                }
            }
            EventPayload::SemanticVerifierEvidenceAdmitted { receipt } => {
                if let (Some(digest), Some(size)) = (
                    receipt.evidence.artifact_digest,
                    receipt.evidence.artifact_size_bytes,
                ) {
                    add(digest, size)?;
                }
            }
            EventPayload::MissionGoalEvidenceAdmitted { receipt } => {
                if let (Some(digest), Some(size)) = (
                    receipt.evidence.artifact_digest,
                    receipt.evidence.artifact_size_bytes,
                ) {
                    add(digest, size)?;
                }
            }
            _ => {}
        }
    }
    for record in uploads.values() {
        if matches!(
            record.phase,
            domain::ArtifactUploadPhase::Completed | domain::ArtifactUploadPhase::Prepared
        ) && !retired_uploads.contains_key(&record.upload_id)
            && let (Some(digest), Some(size)) = (&record.content_digest, record.byte_size)
        {
            add(digest.clone(), size)?;
        }
    }
    for manifest in publications.values() {
        if !retained.is_retired(manifest.selector())
            && let Some(artifact) = manifest.artifact()
        {
            add(
                artifact.content_digest().as_str().into(),
                artifact.byte_size(),
            )?;
        }
    }
    for record in retained
        .records()
        .filter(|record| record.retirement.is_none())
    {
        for dependency in &record.dependencies {
            if dependency.store_owner_id != source_owner {
                return Err(SqliteEventLogError::ArchivePolicy("Archive original content requires its exact source owner; foreign-owner closure is unavailable".into()));
            }
            add(dependency.content_digest.clone(), dependency.byte_size)?;
        }
    }
    for row in events {
        let payload: EventPayload = serde_json::from_str(&row.payload_json)
            .map_err(|error| SqliteEventLogError::Codec(error.to_string()))?;
        match payload {
            EventPayload::SemanticVerifierEvidenceAdmitted { receipt } => {
                if let (Some(digest), Some(size)) = (
                    receipt.evidence.artifact_digest,
                    receipt.evidence.artifact_size_bytes,
                ) {
                    add(digest, size)?;
                }
            }
            EventPayload::MissionGoalEvidenceAdmitted { receipt } => {
                if let (Some(digest), Some(size)) = (
                    receipt.evidence.artifact_digest,
                    receipt.evidence.artifact_size_bytes,
                ) {
                    add(digest, size)?;
                }
            }
            _ => {}
        }
    }
    if blobs.len() > 4096 {
        return Err(SqliteEventLogError::ArchivePolicy(
            "Archive original content closure exceeds bound".into(),
        ));
    }
    Ok(blobs
        .into_iter()
        .map(|(content_digest, byte_size)| ArchiveContent {
            content_digest,
            byte_size,
        })
        .collect())
}

impl ArchiveIntent {
    /// Observes complete immutable original identity without granting another source read or copy.
    pub fn digest(&self) -> Result<String, String> {
        super::archive_abort::identity(self).map_err(|error| error.to_string())
    }
    /// Validates retained immutable authority before a stored row can support reconciliation or GC.
    pub fn validate(&self) -> Result<(), String> {
        let sha = |value: &str| {
            value.strip_prefix("sha256:").is_some_and(|hex| {
                hex.len() == 64
                    && hex
                        .bytes()
                        .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
            })
        };
        let owner = self
            .request
            .source_owner
            .strip_prefix("artifact-store:")
            .unwrap_or("");
        let authority = self
            .request
            .authority_id
            .strip_prefix("controller-store:")
            .unwrap_or("");
        if self.schema != "roboguide.archive-intent/v0.1"
            || owner.len() != 64
            || authority.len() != 64
            || !authority
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
            || !owner
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
            || self.request.command_id.is_empty()
            || self.request.command_id.len() > 128
            || self.request.principal.is_empty()
            || self.request.principal.len() > 256
            || self.request.tenant.is_empty()
            || self.request.tenant.len() > 128
            || !std::path::Path::new(&self.request.destination).is_absolute()
            || self.request.budget_ms == 0
            || self.request.budget_ms > 300_000
            || self.deadline_ms.checked_sub(self.issued_ms) != Some(self.request.budget_ms)
            || self.boot_id.len() != 36
            || self.time_namespace.is_empty()
            || self.time_namespace.len() > 128
            || !sha(&self.catalog_digest)
            || !matches!(
                self.checkpoint.schema.as_str(),
                "roboguide.controller-checkpoint/v49"
                    | "roboguide.controller-checkpoint/v50"
                    | "roboguide.controller-checkpoint/v51"
                    | "roboguide.controller-checkpoint/v52"
                    | "roboguide.controller-checkpoint/v53"
                    | crate::CONTROLLER_AUTHORITY_CHECKPOINT_SCHEMA
            )
            || self.checkpoint.event_sequence < self.manifest.end_sequence
            || self.manifest.checkpoint_digest.as_deref()
                != Some(checkpoint_identity(&self.checkpoint).as_str())
            || self.inputs.len() > 4096
            || self
                .inputs
                .windows(2)
                .any(|pair| pair[0].content_digest >= pair[1].content_digest)
            || self.inputs.iter().any(|input| {
                !sha(&input.content_digest) || input.byte_size > 4 * 1024 * 1024 * 1024
            })
        {
            return Err(
                "original Archive intent identity, checkpoint, kernel budget or inputs are invalid"
                    .into(),
            );
        }
        super::archive::validate_manifest(&self.manifest)?;
        if self.manifest.schema != "roboguide.event-archive/v2" {
            return Err("original Archive intent requires the explicit content contract".into());
        }
        Ok(())
    }
    /// Freezes original request, prefix, Catalog, actual content and one same-kernel budget.
    pub(super) fn new(
        request: ArchiveRequest,
        manifest: super::archive::ArchiveManifest,
        checkpoint: PersistedCheckpoint,
        catalog: &[PersistedEvent],
        events: &[PersistedEvent],
    ) -> Result<Self, SqliteEventLogError> {
        let valid = |value: &str, max: usize| {
            !value.is_empty() && value.len() <= max && !value.chars().any(char::is_control)
        };
        if !valid(&request.command_id, 128)
            || !valid(&request.principal, 256)
            || !valid(&request.tenant, 128)
            || !request.source_owner.starts_with("artifact-store:")
            || !std::path::Path::new(&request.destination).is_absolute()
            || request.budget_ms == 0
            || request.budget_ms > 300_000
        {
            return Err(SqliteEventLogError::ArchivePolicy(
                "Archive original request is invalid or unbounded".into(),
            ));
        }
        let (boot_id, time_namespace, issued_ms) = kernel()?;
        let deadline_ms = issued_ms.checked_add(request.budget_ms).ok_or_else(|| {
            SqliteEventLogError::ArchivePolicy("Archive original deadline overflow".into())
        })?;
        let catalog_digest = format!(
            "sha256:{:x}",
            Sha256::digest(
                serde_json::to_vec(
                    &serde_json::to_value(catalog)
                        .map_err(|error| SqliteEventLogError::Codec(error.to_string()))?
                )
                .map_err(|error| SqliteEventLogError::Codec(error.to_string()))?
            )
        );
        let inputs = inputs(&request.source_owner, catalog, events)?;
        let intent = Self {
            schema: "roboguide.archive-intent/v0.1".into(),
            request,
            manifest,
            checkpoint,
            catalog_digest,
            inputs,
            boot_id,
            time_namespace,
            issued_ms,
            deadline_ms,
        };
        intent
            .validate()
            .map_err(SqliteEventLogError::ArchivePolicy)?;
        Ok(intent)
    }

    /// Rechecks the same original kernel deadline before each bounded publication effect.
    pub fn require_budget(&self) -> Result<(), String> {
        let (boot, namespace, now) = kernel().map_err(|error| error.to_string())?;
        if boot != self.boot_id
            || namespace != self.time_namespace
            || now < self.issued_ms
            || now.saturating_add(10) >= self.deadline_ms
        {
            return Err("Archive original kernel budget is unavailable or expired".into());
        }
        Ok(())
    }
}

/// Persists original intent before effects; exact retries are observations, never renewed work permits.
pub(super) fn admit(
    connection: &Connection,
    intent: &ArchiveIntent,
) -> Result<(), SqliteEventLogError> {
    require_storage(connection)?;
    if intent.request.authority_id != authority_id(connection)? {
        return Err(SqliteEventLogError::ArchivePolicy(
            "Archive intent names another original Controller authority".into(),
        ));
    }
    intent
        .validate()
        .map_err(SqliteEventLogError::ArchivePolicy)?;
    let raw = serde_json::to_string(intent)
        .map_err(|error| SqliteEventLogError::Codec(error.to_string()))?;
    if raw.len() > MAX_INTENT_BYTES {
        return Err(SqliteEventLogError::ArchivePolicy(
            "original Archive intent exceeds bounded storage".into(),
        ));
    }
    super::archive_abort::validate_storage(connection)?;
    let pending: bool = connection.query_row(
        "SELECT EXISTS(SELECT 1 FROM event_archive_operations AS original WHERE state='pending' AND NOT EXISTS(SELECT 1 FROM event_archive_aborts AS cleanup WHERE cleanup.original_command_id=original.command_id AND cleanup.receipt_json IS NOT NULL))",
        [],
        |row| row.get(0),
    )?;
    if pending {
        return Err(SqliteEventLogError::ArchivePolicy(
            "an original pending Archive must be reconciled before another command".into(),
        ));
    }
    let count: u64 =
        connection.query_row("SELECT COUNT(*) FROM event_archive_operations", [], |row| {
            row.get(0)
        })?;
    if count >= 4096 {
        return Err(SqliteEventLogError::ArchivePolicy(
            "original Archive history is full".into(),
        ));
    }
    connection.execute(
        "INSERT INTO event_archive_operations VALUES (?1,?2,'pending',NULL)",
        params![intent.request.command_id, raw],
    )?;
    Ok(())
}

/// Reads exact original authority before any retry can propose another prefix or budget.
pub(super) fn existing(
    connection: &Connection,
    id: &str,
) -> Result<Option<(ArchiveIntent, Option<String>)>, SqliteEventLogError> {
    let exists: bool = connection.query_row(
        "SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE name='event_archive_operations')",
        [],
        |row| row.get(0),
    )?;
    if !exists {
        return Ok(None);
    }
    require_storage(connection)?;
    let row:Option<(String,String,Option<String>)>=connection.query_row("SELECT intent_json,state,receipt_json FROM event_archive_operations WHERE command_id=?1",[id],|row|Ok((row.get(0)?,row.get(1)?,row.get(2)?))).optional()?;
    row.map(|(raw, state, receipt)| {
        if !matches!(state.as_str(), "pending" | "compacted")
            || (state == "compacted") != receipt.is_some()
        {
            return Err(SqliteEventLogError::Codec(
                "original Archive disposition is invalid".into(),
            ));
        }
        if raw.len() > MAX_INTENT_BYTES
            || receipt
                .as_ref()
                .is_some_and(|receipt| receipt.len() != 71 || !receipt.starts_with("sha256:"))
        {
            return Err(SqliteEventLogError::Codec(
                "original Archive record exceeds its exact bounds".into(),
            ));
        }
        let intent: ArchiveIntent = serde_json::from_str(&raw)
            .map_err(|error| SqliteEventLogError::Codec(error.to_string()))?;
        intent.validate().map_err(SqliteEventLogError::Codec)?;
        if intent.request.command_id != id {
            return Err(SqliteEventLogError::Codec(
                "original Archive row key changed".into(),
            ));
        }
        Ok((intent, receipt))
    })
    .transpose()
}

impl SqliteEventLog {
    /// Returns only the accepted original software-store identity; absence remains unavailable.
    pub fn archive_authority_id(&self) -> Result<String, SqliteEventLogError> {
        self.ensure_no_open_batch()?;
        let connection = self
            .connection
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        require_storage(&connection)?;
        authority_id(&connection)
    }

    /// Observes only the original immutable Archive command; no copying or retirement occurs here.
    pub fn archive_operation(
        &self,
        id: &str,
    ) -> Result<Option<(ArchiveIntent, Option<String>)>, SqliteEventLogError> {
        self.ensure_no_open_batch()?;
        let connection = self
            .connection
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        existing(&connection, id)
    }
}
