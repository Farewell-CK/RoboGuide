//! SQLite schema, migration, recovery, and row decoding.

use super::*;

/// Configures durability and concurrency properties required by the journal.
pub(super) fn configure_connection(connection: &Connection) -> Result<(), JournalError> {
    connection.pragma_update(None, "journal_mode", "WAL")?;
    connection.pragma_update(None, "synchronous", "FULL")?;
    connection.pragma_update(None, "foreign_keys", "ON")?;
    connection.busy_timeout(std::time::Duration::from_secs(5))?;
    Ok(())
}

/// Creates the current journal schema without modifying existing execution records.
pub(super) fn create_schema(connection: &mut Connection) -> Result<(), JournalError> {
    let prior_version =
        connection.pragma_query_value(None, "user_version", |row| row.get::<_, i64>(0))?;
    if prior_version > 14 {
        return Err(JournalError::Corrupt(
            "journal was written by a newer storage authority".into(),
        ));
    }
    let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
    transaction.execute_batch("CREATE TABLE IF NOT EXISTS node_journal_storage_schema (
        singleton INTEGER PRIMARY KEY CHECK(singleton=1), version INTEGER NOT NULL CHECK(version>0))")?;
    let marker: Option<i64> = transaction
        .query_row(
            "SELECT version FROM node_journal_storage_schema WHERE singleton=1",
            [],
            |row| row.get(0),
        )
        .optional()?;
    if marker.is_some_and(|version| !matches!(version, 7..=14))
        || (prior_version >= 7 && marker != Some(prior_version))
    {
        return Err(JournalError::Corrupt(
            "journal storage marker is unsupported".into(),
        ));
    }
    migrate_authority_table(&transaction, "executions", "execution_authority_v7")?;
    migrate_authority_table(&transaction, "memory_imports", "memory_import_authority_v7")?;
    transaction.execute_batch(
        "CREATE TABLE IF NOT EXISTS execution_authority_v7 (
             execution_id TEXT PRIMARY KEY NOT NULL,
             invocation_content BLOB NOT NULL,
             invocation_digest TEXT NOT NULL,
             workflow_digest TEXT NOT NULL,
             resource_ids TEXT NOT NULL,
             local_handle TEXT,
             cancellation_requested INTEGER NOT NULL DEFAULT 0
                 CHECK(cancellation_requested IN (0, 1)),
             sequence INTEGER NOT NULL CHECK(sequence >= 0),
             status TEXT NOT NULL CHECK(status IN (
                 'dispatching', 'accepted', 'running', 'completed', 'failed', 'cancelled',
                 'reconciliation_required'
             )),
             reason TEXT NOT NULL
         );
         CREATE TABLE IF NOT EXISTS prepared_artifacts (
             binding_id TEXT PRIMARY KEY NOT NULL,
             map_id TEXT NOT NULL,
             revision_id TEXT NOT NULL,
             producer_execution_id TEXT NOT NULL,
             frozen_path TEXT NOT NULL,
             manifest_json TEXT NOT NULL,
             UNIQUE(map_id, revision_id),
             FOREIGN KEY(producer_execution_id) REFERENCES execution_authority_v7(execution_id)
         );
         CREATE TABLE IF NOT EXISTS artifact_finalizations (
             execution_id TEXT PRIMARY KEY NOT NULL,
             kind TEXT NOT NULL CHECK(kind IN ('publish', 'import', 'verify')),
             FOREIGN KEY(execution_id) REFERENCES execution_authority_v7(execution_id)
         );
         CREATE TABLE IF NOT EXISTS artifact_preparations (
             execution_id TEXT PRIMARY KEY NOT NULL,
             binding_id TEXT UNIQUE NOT NULL,
             FOREIGN KEY(execution_id) REFERENCES execution_authority_v7(execution_id)
         );
         CREATE TABLE IF NOT EXISTS localization_evidence (
             execution_id TEXT PRIMARY KEY NOT NULL,
             evidence_json TEXT NOT NULL,
             FOREIGN KEY(execution_id) REFERENCES execution_authority_v7(execution_id)
         );
         CREATE TABLE IF NOT EXISTS local_dispatch_authorizations (
             execution_id TEXT PRIMARY KEY NOT NULL,
             FOREIGN KEY(execution_id) REFERENCES execution_authority_v7(execution_id)
         );
         CREATE TABLE IF NOT EXISTS cancellation_intents (
             execution_id TEXT PRIMARY KEY NOT NULL
         );
         CREATE TABLE IF NOT EXISTS controller_epoch (
             singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
             term INTEGER NOT NULL CHECK(term >= 0),
             replica_id INTEGER NOT NULL CHECK(replica_id > 0)
         );
         CREATE TABLE IF NOT EXISTS memory_import_authority_v7 (
             import_id TEXT PRIMARY KEY NOT NULL,
             command_id TEXT NOT NULL,
             identity_digest TEXT NOT NULL,
             status TEXT NOT NULL CHECK(status IN ('pending', 'completed', 'rejected', 'unknown')),
             reason TEXT NOT NULL
         );
         CREATE TABLE IF NOT EXISTS memory_import_continuity_v7 (
             import_id TEXT PRIMARY KEY REFERENCES memory_import_authority_v7(import_id),
             evidence_json TEXT NOT NULL
         );
         CREATE TABLE IF NOT EXISTS memory_import_queries_v7 (
             query_id TEXT PRIMARY KEY,
             import_id TEXT NOT NULL REFERENCES memory_import_authority_v7(import_id),
             spec_json TEXT NOT NULL,
             received_at_ms INTEGER NOT NULL,
             result_json TEXT
         );
         CREATE TABLE IF NOT EXISTS memory_preparations (
             preparation_id TEXT PRIMARY KEY NOT NULL,
             execution_id TEXT UNIQUE NOT NULL,
             preparation_digest TEXT NOT NULL,
             preparation_json TEXT NOT NULL,
             budget_json TEXT NOT NULL,
             configuration_digest TEXT NOT NULL
         );
         CREATE TABLE IF NOT EXISTS memory_context_revocations (
             preparation_id TEXT PRIMARY KEY NOT NULL,
             preparation_digest TEXT NOT NULL
         );
         CREATE TABLE IF NOT EXISTS memory_control_receipts (
             command_id TEXT PRIMARY KEY NOT NULL,
             preparation_id TEXT NOT NULL,
             operation TEXT NOT NULL CHECK(operation IN ('prepare','probe','revoke')),
             generation INTEGER NOT NULL CHECK(generation BETWEEN 0 AND 16),
             command_json TEXT NOT NULL,
             receipt_json TEXT
         );
         CREATE INDEX IF NOT EXISTS memory_control_by_context
             ON memory_control_receipts(preparation_id,operation,generation);
         CREATE TABLE IF NOT EXISTS memory_use_heads (
             lease_id TEXT PRIMARY KEY, purpose_json TEXT NOT NULL,
             highest_generation INTEGER NOT NULL CHECK(highest_generation>=0),
             revoked INTEGER NOT NULL CHECK(revoked IN (0,1))
         );
         CREATE TABLE IF NOT EXISTS memory_use_commands (
             command_id TEXT PRIMARY KEY, lease_id TEXT NOT NULL REFERENCES memory_use_heads(lease_id),
             generation INTEGER NOT NULL CHECK(generation>=0), command_json TEXT NOT NULL,
             budget_json TEXT NOT NULL, receipt_json TEXT
         );
         CREATE INDEX IF NOT EXISTS memory_use_by_lease ON memory_use_commands(lease_id,generation);
         CREATE TABLE IF NOT EXISTS memory_use_effects (
             command_id TEXT NOT NULL REFERENCES memory_use_commands(command_id) ON DELETE CASCADE,
             provider_id TEXT NOT NULL, request_json TEXT NOT NULL,
             query_count INTEGER NOT NULL DEFAULT 0 CHECK(query_count BETWEEN 0 AND 3),
             receipt_json TEXT, PRIMARY KEY(command_id,provider_id)
         );",
    )?;
    transaction.execute_batch("CREATE TABLE IF NOT EXISTS memory_use_observation_sessions (
        command_id TEXT PRIMARY KEY REFERENCES memory_use_commands(command_id),
        opened_at_ms INTEGER NOT NULL, width_ms INTEGER NOT NULL, generation INTEGER NOT NULL, budget_json TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS memory_use_observation_queries (
        query_id TEXT PRIMARY KEY, command_id TEXT NOT NULL REFERENCES memory_use_commands(command_id),
        query_json TEXT NOT NULL, budget_json TEXT NOT NULL, receipt_json TEXT);
        CREATE TABLE IF NOT EXISTS memory_use_observation_providers (
        query_id TEXT NOT NULL REFERENCES memory_use_observation_queries(query_id), provider_id TEXT NOT NULL, PRIMARY KEY(query_id,provider_id));
        CREATE TABLE IF NOT EXISTS memory_use_auto_query_times (
        command_id TEXT NOT NULL, provider_id TEXT NOT NULL, last_lower_ms INTEGER NOT NULL, PRIMARY KEY(command_id,provider_id));")?;
    if prior_version < 2 {
        // Rows created by v1 could already have called Local EAIOS. Migrate them conservatively.
        transaction.execute(
            "INSERT OR IGNORE INTO local_dispatch_authorizations (execution_id)
             SELECT execution_id FROM execution_authority_v7
             WHERE status = 'dispatching' AND local_handle IS NULL",
            [],
        )?;
    }
    transaction.execute_batch(
        "CREATE VIEW IF NOT EXISTS executions AS SELECT * FROM execution_authority_v7;
        CREATE VIEW IF NOT EXISTS memory_imports AS SELECT * FROM memory_import_authority_v7;",
    )?;
    transaction.execute(
        "INSERT INTO node_journal_storage_schema VALUES (1,14)
         ON CONFLICT(singleton) DO UPDATE SET version=14",
        [],
    )?;
    transaction.pragma_update(None, "user_version", 14_i64)?;
    transaction.commit()?;
    Ok(())
}

/// Renames legacy writable authority atomically while keeping readonly compatibility names for inspection.
fn migrate_authority_table(
    transaction: &rusqlite::Transaction<'_>,
    legacy: &str,
    current: &str,
) -> Result<(), JournalError> {
    let legacy_kind: Option<String> = transaction
        .query_row(
            "SELECT type FROM sqlite_master WHERE name=?1",
            [legacy],
            |row| row.get(0),
        )
        .optional()?;
    if legacy_kind.as_deref() == Some("table") {
        let current_exists: bool = transaction.query_row(
            "SELECT EXISTS(SELECT 1 FROM sqlite_master WHERE name=?1)",
            [current],
            |row| row.get(0),
        )?;
        if current_exists {
            return Err(JournalError::Corrupt(
                "journal contains conflicting legacy/current authorities".into(),
            ));
        }
        // Both identifiers are fixed call-site constants, never input from a manifest or command.
        transaction.execute_batch(&format!("ALTER TABLE {legacy} RENAME TO {current}"))?;
    } else if legacy_kind.as_deref().is_some_and(|kind| kind != "view") {
        return Err(JournalError::Corrupt(
            "legacy journal authority is not a table/view".into(),
        ));
    }
    Ok(())
}

/// Fences dispatches whose local side effect or acceptance fact cannot be proven after restart.
pub(super) fn recover_ambiguous_dispatches(connection: &Connection) -> Result<(), JournalError> {
    // A pending Memory import may have reached the Local EAIOS before a Node restart. The exact
    // identity is retained, but its outcome is unknown and may only be retried with the same
    // authorization digest.
    connection.execute(
        "UPDATE memory_import_authority_v7 SET status = 'unknown', reason = ?1 WHERE status = 'pending'",
        ["Node Service restarted before Memory import outcome was recorded"],
    )?;
    connection.execute(
        "UPDATE execution_authority_v7
         SET status = 'failed', reason = ?1
         WHERE status = 'dispatching' AND local_handle IS NULL
           AND NOT EXISTS (
               SELECT 1 FROM local_dispatch_authorizations authorization
               WHERE authorization.execution_id = execution_authority_v7.execution_id
           )",
        [INTERRUPTED_PRE_DISPATCH_REASON],
    )?;
    connection.execute(
        "UPDATE execution_authority_v7
         SET status = 'reconciliation_required', reason = ?1
         WHERE status = 'dispatching' AND local_handle IS NOT NULL",
        [HANDLE_BEARING_DISPATCH_REASON],
    )?;
    connection.execute(
        "UPDATE execution_authority_v7
         SET status = 'reconciliation_required', reason = ?1
         WHERE status = 'dispatching' AND local_handle IS NULL
           AND EXISTS (
               SELECT 1 FROM local_dispatch_authorizations authorization
               WHERE authorization.execution_id = execution_authority_v7.execution_id
           )",
        [AMBIGUOUS_DISPATCH_REASON],
    )?;
    connection.execute(
        "UPDATE execution_authority_v7
         SET status = 'reconciliation_required', reason = ?1
         WHERE status NOT IN ('completed', 'failed', 'cancelled')
           AND EXISTS (
               SELECT 1 FROM artifact_preparations preparation
               WHERE preparation.execution_id = execution_authority_v7.execution_id
           )",
        [INTERRUPTED_ARTIFACT_PREPARATION_REASON],
    )?;
    Ok(())
}

/// Reads one optional execution using its primary key.
pub(super) fn load_execution(
    connection: &Connection,
    execution_id: &str,
) -> Result<Option<JournalExecution>, JournalError> {
    connection
        .query_row(
            "SELECT execution_id, invocation_content, invocation_digest, workflow_digest,
                    resource_ids, local_handle, cancellation_requested, sequence, status, reason
             FROM execution_authority_v7 WHERE execution_id = ?1",
            [execution_id],
            record_from_row,
        )
        .optional()
        .map_err(JournalError::from)
}

/// Reads one optional durable selective-import identity.
pub(super) fn load_memory_import(
    connection: &Connection,
    import_id: &str,
) -> Result<Option<MemoryImportRecord>, JournalError> {
    connection
        .query_row(
            "SELECT import_id, command_id, identity_digest, status, reason
             FROM memory_import_authority_v7 WHERE import_id = ?1",
            [import_id],
            |row| {
                let status: String = row.get(3)?;
                Ok(MemoryImportRecord {
                    import_id: row.get(0)?,
                    command_id: row.get(1)?,
                    identity_digest: row.get(2)?,
                    status: MemoryImportStatus::parse(&status).map_err(|error| {
                        rusqlite::Error::ToSqlConversionFailure(Box::new(error))
                    })?,
                    reason: row.get(4)?,
                })
            },
        )
        .optional()
        .map_err(JournalError::from)
}

/// Reads one required execution or reports a caller identity error.
pub(super) fn required_execution(
    connection: &Connection,
    execution_id: &str,
) -> Result<JournalExecution, JournalError> {
    load_execution(connection, execution_id)?
        .ok_or_else(|| JournalError::UnknownExecution(execution_id.to_string()))
}

/// Binds adapter evidence to the exact durable invocation, local node, and staged manifest.
pub(super) fn validate_localization_evidence_binding(
    execution: &JournalExecution,
    local_node_id: &NodeId,
    manifest: &MapArtifactManifest,
    evidence: &LocalizationVerificationEvidence,
) -> Result<(), JournalError> {
    let invocation: serde_json::Value =
        serde_json::from_slice(execution.spec().invocation_content())?;
    let invocation_text = |pointer: &str| {
        invocation
            .pointer(pointer)
            .and_then(serde_json::Value::as_str)
    };
    let operation_matches = invocation_text("/operation")
        .or_else(|| invocation_text("/capability_contract"))
        == Some(LOCALIZATION_VERIFY_CONTRACT);
    let matches_invocation = operation_matches
        && [
            ("/mission_id", evidence.mission_id().as_str()),
            ("/task_id", evidence.task_ref().task_id().as_str()),
            ("/group_id", evidence.group_id().as_str()),
            ("/role_id", evidence.role_id().as_str()),
            (
                "/parameters/map_id",
                evidence.artifact().selector().map_id().as_str(),
            ),
            (
                "/parameters/revision_id",
                evidence.artifact().selector().revision_id().as_str(),
            ),
            (
                "/parameters/spatial_anchor_id",
                evidence.anchor_id().as_str(),
            ),
            ("/parameters/artifact_operation", "verify"),
        ]
        .into_iter()
        .all(|(pointer, expected)| invocation_text(pointer) == Some(expected));
    if evidence.execution_id() != execution.execution_id()
        || execution.local_handle() != Some(evidence.local_attempt_id())
        || evidence.node_id() != local_node_id
        || manifest.artifact() != evidence.artifact()
        || manifest.anchor_id() != evidence.anchor_id()
        || !matches_invocation
    {
        return Err(JournalError::LocalizationEvidenceConflict(
            execution.execution_id().to_string(),
        ));
    }
    Ok(())
}

/// Loads one prepared artifact by static binding identity.
pub(super) fn load_prepared_artifact_by_binding(
    connection: &Connection,
    binding_id: &str,
) -> Result<Option<PreparedArtifactRecord>, JournalError> {
    connection
        .query_row(
            "SELECT binding_id, map_id, revision_id, producer_execution_id, frozen_path,
                    manifest_json
             FROM prepared_artifacts WHERE binding_id = ?1",
            [binding_id],
            prepared_artifact_from_row,
        )
        .optional()
        .map_err(JournalError::from)
}

/// Loads one prepared artifact by immutable map/revision selector.
pub(super) fn load_prepared_artifact_by_selector(
    connection: &Connection,
    map_id: &str,
    revision_id: &str,
) -> Result<Option<PreparedArtifactRecord>, JournalError> {
    connection
        .query_row(
            "SELECT binding_id, map_id, revision_id, producer_execution_id, frozen_path,
                    manifest_json
             FROM prepared_artifacts WHERE map_id = ?1 AND revision_id = ?2",
            params![map_id, revision_id],
            prepared_artifact_from_row,
        )
        .optional()
        .map_err(JournalError::from)
}

/// Loads the binding fenced by one execution's unresolved artifact preparation.
pub(super) fn load_artifact_preparation(
    connection: &Connection,
    execution_id: &str,
) -> Result<Option<String>, JournalError> {
    connection
        .query_row(
            "SELECT binding_id FROM artifact_preparations WHERE execution_id = ?1",
            [execution_id],
            |row| row.get(0),
        )
        .optional()
        .map_err(JournalError::from)
}

/// Loads the execution that already owns one unresolved output-binding preparation.
pub(super) fn load_artifact_preparation_owner(
    connection: &Connection,
    binding_id: &str,
) -> Result<Option<String>, JournalError> {
    connection
        .query_row(
            "SELECT execution_id FROM artifact_preparations WHERE binding_id = ?1",
            [binding_id],
            |row| row.get(0),
        )
        .optional()
        .map_err(JournalError::from)
}

/// Requires a new prepared record to consume its exact durable preparation identity.
pub(super) fn validate_artifact_preparation_owner(
    connection: &Connection,
    artifact: &PreparedArtifactRecord,
) -> Result<(), JournalError> {
    let binding_id = load_artifact_preparation(connection, &artifact.producer_execution_id)?;
    let execution_id = load_artifact_preparation_owner(connection, &artifact.binding_id)?;
    if binding_id.as_deref() != Some(artifact.binding_id.as_str())
        || execution_id.as_deref() != Some(artifact.producer_execution_id.as_str())
    {
        return Err(JournalError::ArtifactPreparationConflict(
            artifact.producer_execution_id.clone(),
        ));
    }
    Ok(())
}

/// Clears only the exact preparation consumed by an immutable prepared-artifact commit.
pub(super) fn clear_matching_artifact_preparation(
    connection: &Connection,
    artifact: &PreparedArtifactRecord,
) -> Result<(), JournalError> {
    connection.execute(
        "DELETE FROM artifact_preparations WHERE execution_id = ?1 AND binding_id = ?2",
        params![&artifact.producer_execution_id, &artifact.binding_id],
    )?;
    Ok(())
}

/// Decodes and cross-checks one prepared artifact row.
pub(super) fn prepared_artifact_from_row(
    row: &Row<'_>,
) -> rusqlite::Result<PreparedArtifactRecord> {
    let map_id = row.get::<_, String>(1)?;
    let revision_id = row.get::<_, String>(2)?;
    let manifest_json = row.get::<_, String>(5)?;
    let manifest =
        serde_json::from_str::<MapArtifactManifest>(&manifest_json).map_err(|error| {
            rusqlite::Error::FromSqlConversionFailure(
                5,
                rusqlite::types::Type::Text,
                Box::new(error),
            )
        })?;
    if manifest.selector().map_id().as_str() != map_id
        || manifest.selector().revision_id().as_str() != revision_id
    {
        return Err(rusqlite::Error::FromSqlConversionFailure(
            5,
            rusqlite::types::Type::Text,
            Box::new(JournalError::Corrupt(
                "prepared artifact selector differs from indexed selector".to_string(),
            )),
        ));
    }
    PreparedArtifactRecord::new(
        row.get::<_, String>(0)?,
        row.get::<_, String>(3)?,
        PathBuf::from(row.get::<_, String>(4)?),
        manifest,
    )
    .map_err(|error| {
        rusqlite::Error::FromSqlConversionFailure(5, rusqlite::types::Type::Text, Box::new(error))
    })
}

/// Decodes a SQLite row while surfacing malformed persisted data as conversion failure.
pub(super) fn record_from_row(row: &Row<'_>) -> rusqlite::Result<JournalExecution> {
    let resources_json = row.get::<_, String>(4)?;
    let resource_ids = serde_json::from_str::<Vec<String>>(&resources_json).map_err(|error| {
        rusqlite::Error::FromSqlConversionFailure(4, rusqlite::types::Type::Text, Box::new(error))
    })?;
    let sequence = row.get::<_, i64>(7)?;
    let sequence = u64::try_from(sequence).map_err(|error| {
        rusqlite::Error::FromSqlConversionFailure(
            7,
            rusqlite::types::Type::Integer,
            Box::new(error),
        )
    })?;
    let status_value = row.get::<_, String>(8)?;
    let status = JournalStatus::parse(&status_value).map_err(|error| {
        rusqlite::Error::FromSqlConversionFailure(8, rusqlite::types::Type::Text, Box::new(error))
    })?;
    Ok(JournalExecution {
        execution_id: row.get(0)?,
        spec: ExecutionSpec {
            invocation_content: row.get(1)?,
            invocation_digest: row.get(2)?,
            workflow_digest: row.get(3)?,
            resource_ids,
        },
        local_handle: row.get(5)?,
        cancellation_requested: row.get(6)?,
        sequence,
        status,
        reason: row.get(9)?,
    })
}

/// Calculates the stable digest stored alongside canonical invocation content.
pub(super) fn digest_bytes(content: &[u8]) -> String {
    let digest = Sha256::digest(content);
    format!("sha256:{digest:x}")
}
