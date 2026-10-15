//! Immutable original-provider evidence and bounded, once-admitted readonly queries.

use super::*;
use serde::{Deserialize, Serialize};

/// Original backend and invocation evidence retained before an import can have side effects.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ImportContinuity {
    /// Controller import identity.
    pub import_id: String,
    /// Original immutable import command.
    pub command_id: String,
    /// Exact consumer provider identity.
    pub provider_id: String,
    /// Digest of original invocation and configured provider contract.
    pub identity_digest: String,
    /// Original Controller authorization content without its replaceable leader epoch.
    pub original_content_digest: String,
    /// Backend-observed stable instance identity, never derived from a path.
    pub provider_instance_id: String,
    /// Immutable configured provider and connection contract digest.
    pub provider_contract_digest: String,
    /// Complete original authorization tuple, including immutable manifest metadata.
    pub original_invocation: serde_json::Value,
    /// Original Node-local receive time, never refreshed by replay.
    pub admitted_at_ms: u64,
}

impl ImportContinuity {
    /// Validates exact identity and bounded retained evidence before durable admission.
    pub(super) fn validate(
        &self,
        import: &str,
        command: &str,
        digest: &str,
    ) -> Result<(), JournalError> {
        if self.import_id != import
            || self.command_id != command
            || self.identity_digest != digest
            || self.provider_id.trim().is_empty()
            || self.provider_instance_id.trim().is_empty()
            || self.provider_instance_id.len() > 256
            || !self.original_invocation.is_object()
            || [
                self.identity_digest.as_str(),
                self.original_content_digest.as_str(),
                self.provider_contract_digest.as_str(),
            ]
            .iter()
            .any(|v| {
                let hex = v.strip_prefix("sha256:").unwrap_or(v);
                hex.len() != 64
                    || !hex
                        .bytes()
                        .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
            })
            || serde_json::to_vec(self)?.len() > 600 * 1024
        {
            return Err(JournalError::InvalidSpec(
                "invalid original Memory provider continuity evidence".into(),
            ));
        }
        let value = &self.original_invocation;
        let text = |name: &str| {
            value[name]
                .as_str()
                .filter(|s| !s.trim().is_empty())
                .map(str::to_string)
                .ok_or_else(|| {
                    JournalError::InvalidSpec(format!("original Memory invocation lacks {name}"))
                })
        };
        let original = integration::grpc::v0_4::MemoryImport {
            import_id: text("import_id")?,
            command_id: text("command_id")?,
            consumer_provider_id: text("consumer_provider_id")?,
            manifest_json: text("manifest_json")?,
            mission_id: text("mission_id")?,
            group_id: text("group_id")?,
            task_id: text("task_id")?,
            role_id: text("role_id")?,
            execution_id: text("execution_id")?,
            memory_preparation_id: value
                .get("memory_preparation_id")
                .and_then(serde_json::Value::as_str)
                .unwrap_or_default()
                .into(),
            typed_map_manifest_json: value
                .get("typed_map_manifest_json")
                .and_then(serde_json::Value::as_str)
                .unwrap_or_default()
                .into(),
            ..Default::default()
        };
        if original.import_id != self.import_id
            || original.command_id != self.command_id
            || original.consumer_provider_id != self.provider_id
            || integration::memory_import_content_digest(&original) != self.original_content_digest
        {
            return Err(JournalError::InvalidSpec(
                "retained original Memory invocation content digest differs".into(),
            ));
        }
        let manifest: domain::MemoryArtifactManifest =
            serde_json::from_str(&original.manifest_json)?;
        manifest
            .validate()
            .map_err(|e| JournalError::InvalidSpec(e.to_string()))?;
        Ok(())
    }
}

/// Immutable bounds of one readonly query; replay never renews its time or generation.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ImportQuerySpec {
    /// Controller-wide unique query identity.
    pub query_id: String,
    /// Original import identity.
    pub import_id: String,
    /// Original import command identity.
    pub command_id: String,
    /// Original provider identity.
    pub provider_id: String,
    /// Complete original authorization content digest.
    pub original_content_digest: String,
    /// Monotonic bounded Controller reconciliation ordinal for this original import.
    pub generation: u64,
    /// Original receive-relative query budget, at most thirty seconds.
    pub valid_for_ms: u64,
}

/// Typed result independently checked against the complete original backend contract.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ImportQueryResult {
    /// Query result contract identity.
    pub schema: String,
    /// Original immutable import identity.
    pub import_id: String,
    /// Original immutable command identity.
    pub command_id: String,
    /// Original consumer provider identity.
    pub provider_id: String,
    /// Original full identity digest.
    pub identity_digest: String,
    /// Original content digest independent of Controller epoch.
    pub original_content_digest: String,
    /// Original actual backend instance identity.
    pub provider_instance_id: String,
    /// Original configured workflow/connection contract digest.
    pub provider_contract_digest: String,
    /// completed, rejected_before_effect, or unknown; absence never means no side effects.
    pub outcome: String,
    /// Backend asserts the conclusion comes from its durable original-operation record.
    pub durable: bool,
    /// Diagnostic only, never interpreted to select an outcome.
    pub reason: String,
}

impl ImportQueryResult {
    /// Admits only exact durable original-provider conclusions, preserving all other ambiguity.
    pub fn status(&self, original: &ImportContinuity) -> Result<MemoryImportStatus, JournalError> {
        if self.schema != "roboguide.memory-import-query-result/v0.1"
            || self.import_id != original.import_id
            || self.command_id != original.command_id
            || self.provider_id != original.provider_id
            || self.identity_digest != original.identity_digest
            || self.original_content_digest != original.original_content_digest
            || self.provider_instance_id != original.provider_instance_id
            || self.provider_contract_digest != original.provider_contract_digest
            || self.reason.len() > 4096
        {
            return Err(JournalError::InvalidSpec(
                "query result differs from original backend identity".into(),
            ));
        }
        match (self.outcome.as_str(), self.durable) {
            ("completed", true) => Ok(MemoryImportStatus::Completed),
            ("rejected_before_effect", true) => Ok(MemoryImportStatus::Rejected),
            ("unknown", _) => Ok(MemoryImportStatus::Unknown),
            _ => Err(JournalError::InvalidSpec(
                "query lacks a conclusive durable original-import outcome".into(),
            )),
        }
    }
}

/// Original receipt time and immutable result of one once-admitted readonly query.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ImportQueryRecord {
    /// Immutable original query and time budget.
    pub spec: ImportQuerySpec,
    /// Original Node-local admission time.
    pub received_at_ms: u64,
    /// None means interrupted or still running, never permission to rerun the same query.
    pub result: Option<ImportQueryResult>,
}

impl ExecutionJournal {
    /// Retains raw Unknown while recognizing a conclusive original query; this grants no current lease.
    pub fn memory_import_completion_known(&self, import_id: &str) -> Result<bool, JournalError> {
        let record = self.memory_import(import_id)?;
        let Some(original) = self.import_continuity(import_id)? else {
            return Ok(false);
        };
        let mut outcome = record.as_ref().and_then(|record| match record.status() {
            crate::MemoryImportStatus::Completed => Some(crate::MemoryImportStatus::Completed),
            crate::MemoryImportStatus::Rejected => Some(crate::MemoryImportStatus::Rejected),
            _ => None,
        });
        let connection = self.lock_connection()?;
        let mut query=connection.prepare("SELECT result_json FROM memory_import_queries_v7 WHERE import_id=?1 AND result_json IS NOT NULL ORDER BY query_id LIMIT 17")?;
        let results = query
            .query_map([import_id], |row| row.get::<_, String>(0))?
            .collect::<Result<Vec<_>, _>>()?;
        if results.len() > 16 {
            return Err(JournalError::Corrupt(
                "original import query history exceeds its bound".into(),
            ));
        }
        for encoded in results {
            let result: ImportQueryResult = serde_json::from_str(&encoded)?;
            let current = result.status(&original)?;
            if matches!(
                current,
                crate::MemoryImportStatus::Completed | crate::MemoryImportStatus::Rejected
            ) {
                if outcome.is_some_and(|prior| prior != current) {
                    return Err(JournalError::Corrupt(
                        "original import conclusions conflict".into(),
                    ));
                }
                outcome = Some(current);
            }
        }
        Ok(outcome == Some(crate::MemoryImportStatus::Completed))
    }
    /// Returns the original backend evidence, preserving absence for legacy imports.
    pub fn import_continuity(
        &self,
        import: &str,
    ) -> Result<Option<ImportContinuity>, JournalError> {
        let connection = self.lock_connection()?;
        let json: Option<String> = connection
            .query_row(
                "SELECT evidence_json FROM memory_import_continuity_v7 WHERE import_id=?1",
                [import],
                |r| r.get(0),
            )
            .optional()?;
        let original: Option<ImportContinuity> = json
            .map(|s| serde_json::from_str(&s).map_err(JournalError::from))
            .transpose()?;
        if let Some(original) = &original {
            original.validate(
                &original.import_id,
                &original.command_id,
                &original.identity_digest,
            )?;
        }
        Ok(original)
    }

    /// Commits one bounded readonly-query admission; an existing identity never runs again.
    pub fn prepare_import_query(
        &self,
        spec: &ImportQuerySpec,
        received_at_ms: u64,
    ) -> Result<(ImportQueryRecord, bool), JournalError> {
        if spec.query_id != format!("memory-query-{}-{}", spec.import_id, spec.generation)
            || spec.generation == 0
            || spec.generation > 16
            || !(1..=30_000).contains(&spec.valid_for_ms)
        {
            return Err(JournalError::InvalidSpec(
                "invalid Memory query identity or original bounds".into(),
            ));
        }
        let mut connection = self.lock_connection()?;
        let tx = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        if let Some(existing) = load_query(&tx, &spec.query_id)? {
            if existing.spec != *spec {
                return Err(JournalError::InvalidSpec(
                    "Memory query identity conflict".into(),
                ));
            }
            tx.commit()?;
            return Ok((existing, false));
        }
        let original: String = tx.query_row(
            "SELECT evidence_json FROM memory_import_continuity_v7 WHERE import_id=?1",
            [&spec.import_id],
            |r| r.get(0),
        )?;
        let original: ImportContinuity = serde_json::from_str(&original)?;
        original.validate(
            &original.import_id,
            &original.command_id,
            &original.identity_digest,
        )?;
        if received_at_ms < original.admitted_at_ms {
            return Err(JournalError::InvalidSpec(
                "Node query clock precedes original import admission".into(),
            ));
        }
        if spec.command_id != original.command_id
            || spec.provider_id != original.provider_id
            || spec.original_content_digest != original.original_content_digest
        {
            return Err(JournalError::InvalidSpec(
                "Memory query does not bind the original import".into(),
            ));
        }
        let highest: u64 = tx.query_row("SELECT COALESCE(MAX(json_extract(spec_json,'$.generation')),0) FROM memory_import_queries_v7 WHERE import_id=?1", [&spec.import_id], |r| r.get(0))?;
        if spec.generation <= highest {
            return Err(JournalError::InvalidSpec(
                "Memory query generation is stale".into(),
            ));
        }
        tx.execute(
            "INSERT INTO memory_import_queries_v7 VALUES (?1,?2,?3,?4,NULL)",
            params![
                spec.query_id,
                spec.import_id,
                serde_json::to_string(spec)?,
                i64::try_from(received_at_ms)
                    .map_err(|_| JournalError::InvalidSpec("query time overflow".into()))?
            ],
        )?;
        tx.commit()?;
        Ok((
            ImportQueryRecord {
                spec: spec.clone(),
                received_at_ms,
                result: None,
            },
            true,
        ))
    }

    /// Commits a once-only query result without rewriting original import or replica history.
    pub fn record_import_query(
        &self,
        query_id: &str,
        result: &ImportQueryResult,
        now: u64,
    ) -> Result<ImportQueryRecord, JournalError> {
        let mut connection = self.lock_connection()?;
        let tx = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let existing = load_query(&tx, query_id)?
            .ok_or_else(|| JournalError::InvalidSpec("Memory query was not admitted".into()))?;
        if let Some(prior) = &existing.result {
            if prior != result {
                return Err(JournalError::InvalidSpec(
                    "Memory query conclusion is immutable".into(),
                ));
            }
        } else {
            if now
                >= existing
                    .received_at_ms
                    .saturating_add(existing.spec.valid_for_ms)
                || now < existing.received_at_ms
            {
                return Err(JournalError::InvalidSpec(
                    "Memory query original deadline expired".into(),
                ));
            }
            let original: String = tx.query_row(
                "SELECT evidence_json FROM memory_import_continuity_v7 WHERE import_id=?1",
                [&existing.spec.import_id],
                |r| r.get(0),
            )?;
            let status = result.status(&serde_json::from_str(&original)?)?;
            let import = load_memory_import(&tx, &existing.spec.import_id)?
                .ok_or_else(|| JournalError::Corrupt("original import disappeared".into()))?;
            if matches!(
                import.status(),
                MemoryImportStatus::Completed | MemoryImportStatus::Rejected
            ) && status != MemoryImportStatus::Unknown
                && status != import.status()
            {
                return Err(JournalError::InvalidSpec(
                    "readonly result contradicts a durable original import conclusion".into(),
                ));
            }
            tx.execute("UPDATE memory_import_queries_v7 SET result_json=?2 WHERE query_id=?1 AND result_json IS NULL", params![query_id, serde_json::to_string(result)?])?;
            if matches!(
                status,
                MemoryImportStatus::Completed | MemoryImportStatus::Rejected
            ) && matches!(
                import.status(),
                MemoryImportStatus::Unknown | MemoryImportStatus::Pending
            ) {
                tx.execute(
                    "UPDATE memory_import_authority_v7 SET status=?2,reason=?3 WHERE import_id=?1",
                    params![existing.spec.import_id, status.as_str(), result.reason],
                )?;
            }
        }
        let updated = load_query(&tx, query_id)?
            .ok_or_else(|| JournalError::Corrupt("query disappeared".into()))?;
        tx.commit()?;
        Ok(updated)
    }
}

/// Reads a query without renewing its once-only backend-read admission or original clock.
fn load_query(
    connection: &Connection,
    id: &str,
) -> Result<Option<ImportQueryRecord>, JournalError> {
    let row: Option<(String,u64,Option<String>)> = connection.query_row("SELECT spec_json,received_at_ms,result_json FROM memory_import_queries_v7 WHERE query_id=?1", [id], |r| Ok((r.get(0)?,r.get(1)?,r.get(2)?))).optional()?;
    row.map(|(spec, time, result)| {
        Ok(ImportQueryRecord {
            spec: serde_json::from_str(&spec)?,
            received_at_ms: time,
            result: result.map(|s| serde_json::from_str(&s)).transpose()?,
        })
    })
    .transpose()
}
