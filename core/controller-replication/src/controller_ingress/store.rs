//! Original parent8 command admission and exact first result, retained across native process death.

use super::*;

/// Reads a bounded exact original row, refusing index/JSON/hash drift before returning any evidence.
pub(super) fn record(
    connection: &Connection,
    command: &str,
) -> Result<Option<IngressRecord>, String> {
    let length:Option<u64>=connection.query_row(
        "SELECT length(CAST(record AS BLOB)) FROM controller_content_ingress WHERE command_id=?1",
        [command],|row|row.get(0)).optional().map_err(|error|error.to_string())?;
    if length.is_some_and(|length| length > MAX_RECORD as u64) {
        return Err("original Controller ingress record exceeds its bound".into());
    }
    let row: Option<(String, String)> = connection
        .query_row(
            "SELECT request_digest,record FROM controller_content_ingress WHERE command_id=?1",
            [command],
            |row| Ok((row.get(0)?, row.get(1)?)),
        )
        .optional()
        .map_err(|error| error.to_string())?;
    row.map(|(digest, raw)| {
        let original: IngressRecord =
            serde_json::from_str(&raw).map_err(|error| error.to_string())?;
        validate_record(&original)?;
        if original.intent.command_id != command || original.request_digest != digest {
            return Err("original Controller ingress SQL identity changed".into());
        }
        Ok(original)
    })
    .transpose()
}

/// Persists one complete original intent before Ready; duplicate IDs return their first evidence only.
pub(super) fn admit(
    store: &SqliteRaftStore,
    intent: IngressIntent,
    permission: StreamPermission,
) -> Result<IngressAdmission, String> {
    if !store.controller_content {
        return Err("original child admission requires parent8".into());
    }
    let digest = identity(&intent)?;
    let original = IngressRecord {
        intent,
        request_digest: digest,
        receipt: None,
    };
    validate_record(&original)?;
    let mut connection = store.connection.lock().map_err(|error| error.to_string())?;
    let transaction = connection
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .map_err(|error| error.to_string())?;
    let mut ids = require_marker(&transaction)?;
    if let Some(existing) = record(&transaction, &original.intent.command_id)? {
        if !ids.contains(&original.intent.command_id)
            || existing.intent.process != original.intent.process
            || existing.intent.peer_leaf != original.intent.peer_leaf
            || existing.intent.candidate != original.intent.candidate
            || existing.intent.target != original.intent.target
            || existing.intent.budget.duration_ms != original.intent.budget.duration_ms
        {
            return Err(
                "original Controller command changed its immutable input or first permission"
                    .into(),
            );
        }
        return Ok(IngressAdmission::Existing(Box::new(status(&existing)?)));
    }
    if ids.contains(&original.intent.command_id) || ids.len() >= MAX_OPERATIONS {
        return Err("original Controller ingress history is incomplete or full".into());
    }
    permission()?;
    original.intent.budget.check()?;
    let raw = serde_json::to_string(&original).map_err(|error| error.to_string())?;
    if raw.len() > MAX_RECORD {
        return Err("original Controller ingress record exceeds its bound".into());
    }
    transaction
        .execute(
            "INSERT INTO controller_content_ingress VALUES (?1,?2,?3)",
            params![original.intent.command_id, original.request_digest, raw],
        )
        .map_err(|error| error.to_string())?;
    ids.insert(original.intent.command_id.clone());
    write_meta(&transaction, HEAD_KEY, &ids).map_err(|error| error.to_string())?;
    permission()?;
    original.intent.budget.check()?;
    transaction.commit().map_err(|error| error.to_string())?;
    Ok(IngressAdmission::New(Box::new(original)))
}

/// Saves only an actual exact original complete quorum receipt under the unchanged original budget.
pub(super) fn complete(
    store: &SqliteRaftStore,
    mut original: IngressRecord,
    receipt: IngressReceipt,
    permission: StreamPermission,
) -> Result<ControllerIngressStatus, String> {
    let mut connection = store.connection.lock().map_err(|error| error.to_string())?;
    let transaction = connection
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .map_err(|error| error.to_string())?;
    let ids = require_marker(&transaction)?;
    let retained = record(&transaction, &original.intent.command_id)?
        .ok_or("original Controller ingress intent was lost")?;
    if !ids.contains(&original.intent.command_id)
        || retained.intent != original.intent
        || retained.request_digest != original.request_digest
        || retained.receipt.is_some()
    {
        return Err("original Controller ingress result changed before publication".into());
    }
    permission()?;
    original.intent.budget.check()?;
    original.receipt = Some(receipt);
    validate_record(&original)?;
    let raw = serde_json::to_string(&original).map_err(|error| error.to_string())?;
    if raw.len() > MAX_RECORD {
        return Err("original Controller ingress result exceeds its bound".into());
    }
    transaction
        .execute(
            "UPDATE controller_content_ingress SET record=?2 WHERE command_id=?1",
            params![original.intent.command_id, raw],
        )
        .map_err(|error| error.to_string())?;
    permission()?;
    original.intent.budget.check()?;
    transaction.commit().map_err(|error| error.to_string())?;
    status(&original)
}
