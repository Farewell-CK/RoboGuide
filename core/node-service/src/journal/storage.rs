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
    let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
    transaction.execute_batch(
        "CREATE TABLE IF NOT EXISTS executions (
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
             FOREIGN KEY(producer_execution_id) REFERENCES executions(execution_id)
         );
         CREATE TABLE IF NOT EXISTS artifact_finalizations (
             execution_id TEXT PRIMARY KEY NOT NULL,
             kind TEXT NOT NULL CHECK(kind IN ('publish', 'import', 'verify')),
             FOREIGN KEY(execution_id) REFERENCES executions(execution_id)
         );
         CREATE TABLE IF NOT EXISTS artifact_preparations (
             execution_id TEXT PRIMARY KEY NOT NULL,
             binding_id TEXT UNIQUE NOT NULL,
             FOREIGN KEY(execution_id) REFERENCES executions(execution_id)
         );
         CREATE TABLE IF NOT EXISTS localization_evidence (
             execution_id TEXT PRIMARY KEY NOT NULL,
             evidence_json TEXT NOT NULL,
             FOREIGN KEY(execution_id) REFERENCES executions(execution_id)
         );
         CREATE TABLE IF NOT EXISTS local_dispatch_authorizations (
             execution_id TEXT PRIMARY KEY NOT NULL,
             FOREIGN KEY(execution_id) REFERENCES executions(execution_id)
         );
         CREATE TABLE IF NOT EXISTS cancellation_intents (
             execution_id TEXT PRIMARY KEY NOT NULL
         );",
    )?;
    if prior_version < 2 {
        // Rows created by v1 could already have called Local EAIOS. Migrate them conservatively.
        transaction.execute(
            "INSERT OR IGNORE INTO local_dispatch_authorizations (execution_id)
             SELECT execution_id FROM executions
             WHERE status = 'dispatching' AND local_handle IS NULL",
            [],
        )?;
    }
    transaction.pragma_update(None, "user_version", 5_i64)?;
    transaction.commit()?;
    Ok(())
}

/// Fences dispatches whose local side effect or acceptance fact cannot be proven after restart.
pub(super) fn recover_ambiguous_dispatches(connection: &Connection) -> Result<(), JournalError> {
    connection.execute(
        "UPDATE executions
         SET status = 'failed', reason = ?1
         WHERE status = 'dispatching' AND local_handle IS NULL
           AND NOT EXISTS (
               SELECT 1 FROM local_dispatch_authorizations authorization
               WHERE authorization.execution_id = executions.execution_id
           )",
        [INTERRUPTED_PRE_DISPATCH_REASON],
    )?;
    connection.execute(
        "UPDATE executions
         SET status = 'reconciliation_required', reason = ?1
         WHERE status = 'dispatching' AND local_handle IS NOT NULL",
        [HANDLE_BEARING_DISPATCH_REASON],
    )?;
    connection.execute(
        "UPDATE executions
         SET status = 'reconciliation_required', reason = ?1
         WHERE status = 'dispatching' AND local_handle IS NULL
           AND EXISTS (
               SELECT 1 FROM local_dispatch_authorizations authorization
               WHERE authorization.execution_id = executions.execution_id
           )",
        [AMBIGUOUS_DISPATCH_REASON],
    )?;
    connection.execute(
        "UPDATE executions
         SET status = 'reconciliation_required', reason = ?1
         WHERE status NOT IN ('completed', 'failed', 'cancelled')
           AND EXISTS (
               SELECT 1 FROM artifact_preparations preparation
               WHERE preparation.execution_id = executions.execution_id
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
             FROM executions WHERE execution_id = ?1",
            [execution_id],
            record_from_row,
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
