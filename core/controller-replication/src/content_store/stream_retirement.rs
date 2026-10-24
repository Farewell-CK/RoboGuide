//! Root7 exact original stream retirement; scope/process closure never changes its I/O outcome.

use super::*;

/// Complete exact local close/end evidence, separate from the immutable original receive result.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Retirement {
    /// Closed member-local retirement interpretation, unavailable to old Root writers.
    schema: String,
    /// Exact original receiving store, never a replacement source owner.
    receiver: ReplicaContentIdentity,
    /// Complete first stream record; its receipt/Unknown and budget remain unchanged.
    original: StreamRecord,
    /// Separate first retirement clock including queue/observation/SQL time.
    budget: ContentBudget,
    /// Independent original process-end observation when actual worker close is absent.
    process_end: Option<ContentProcessEndObservation>,
    /// Actual decision sample within this original retirement budget.
    retired_ms: u64,
}

/// Creates the complete retirement journal/head only inside explicit fresh/drained Root7 admission.
pub(crate) fn initialize(connection: &Connection) -> Result<(), String> {
    connection.execute_batch("CREATE TABLE content_stream_retirements(stream_id TEXT PRIMARY KEY,record TEXT NOT NULL)").map_err(|error| error.to_string())?;
    layout::write_head(connection, "stream_retirement_ids", &BTreeSet::new())
}

/// Reads bounded first retirement without treating missing or oversized authority as a new close.
fn record(connection: &Connection, id: &str) -> Result<Option<Retirement>, String> {
    let size: Option<u64> = connection.query_row(
        "SELECT length(CAST(record AS BLOB)) FROM content_stream_retirements WHERE stream_id=?1",
        [id], |row| row.get(0),
    ).optional().map_err(|error| error.to_string())?;
    if size.is_some_and(|size| size > 16 * 1024 * 1024) {
        return Err("original stream retirement exceeds its fixed bound".into());
    }
    let raw: Option<String> = connection
        .query_row(
            "SELECT record FROM content_stream_retirements WHERE stream_id=?1",
            [id],
            |row| row.get(0),
        )
        .optional()
        .map_err(|error| error.to_string())?;
    raw.map(|raw| serde_json::from_str(&raw).map_err(|error| error.to_string()))
        .transpose()
}

/// Validates only original retained close/end evidence, without a new clock or permission.
fn validate(value: &Retirement, original: &StreamRecord) -> Result<(), String> {
    if value.schema != "roboguide.replica-content-stream-retirement/v0.1"
        || value.receiver != original.intent.receiver
        || value.original != *original
        || !value.budget.validates_completion(value.retired_ms)
    {
        return Err("stream retirement changed its exact original receive/clock".into());
    }
    if let Some(closed) = original.worker_closed_ms {
        if value.process_end.is_some() || !original.intent.budget.validates_close(closed) {
            return Err("stream retirement lacks its original actual worker close".into());
        }
    } else {
        let end = value
            .process_end
            .as_ref()
            .ok_or("stream retirement lacks original process-end evidence")?;
        if !original
            .intent
            .budget
            .validates_end_decision(end, &value.budget, value.retired_ms)
        {
            return Err(
                "stream retirement process observation differs from original decision".into(),
            );
        }
    }
    Ok(())
}

/// Resolves only an explicit Root7 release; old Root generations require all stream pins active.
pub(crate) fn release_digest(
    connection: &Connection,
    original: &StreamRecord,
) -> Result<Option<String>, String> {
    let version: i64 = connection
        .query_row("PRAGMA user_version", [], |row| row.get(0))
        .map_err(|error| error.to_string())?;
    if version != 7 {
        return Ok(None);
    }
    let Some(retired) = record(connection, &operation_id(&original.intent.command_id)?)? else {
        return Ok(None);
    };
    validate(&retired, original)?;
    Ok(Some(digest(&retired)?))
}

/// Requires every retained retirement to belong to one complete actual original stream journal.
pub(crate) fn validate_records(connection: &Connection) -> Result<(), String> {
    for id in layout::head(connection, "stream_retirement_ids")? {
        let original =
            super::record(connection, &id)?.ok_or("retired stream lost its original journal")?;
        let retired =
            record(connection, &id)?.ok_or("stream retirement head lost its original row")?;
        validate(&retired, &original)?;
    }
    Ok(())
}

impl ReplicaContentStore {
    /// Atomically retires only an actually closed original stream; live/unknown processes refuse.
    /// Repeats return the first close/end observation without renewing any original receive budget.
    pub fn retire_stream(
        &self,
        command: &str,
        duration_ms: u64,
    ) -> Result<serde_json::Value, String> {
        if !self.inner.reclamation {
            return Err("stream retirement requires explicit Root7".into());
        }
        let budget = ContentBudget::issue(duration_ms)?;
        let mut guard = self
            .inner
            .connection
            .lock()
            .map_err(|error| error.to_string())?;
        let connection = guard.as_mut().ok_or("original stream SQL is closed")?;
        self.validate_connection(connection)?;
        let id = operation_id(command)?;
        if let Some(previous) = record(connection, &id)? {
            return serde_json::to_value(previous).map_err(|error| error.to_string());
        }
        let original = super::record(connection, &id)?.ok_or("original stream is unavailable")?;
        let process_end = if original.worker_closed_ms.is_none() {
            Some(original.intent.budget.observe_process_end()?)
        } else {
            None
        };
        let retired = Retirement {
            schema: "roboguide.replica-content-stream-retirement/v0.1".into(),
            receiver: self.identity().clone(),
            original,
            budget,
            process_end,
            retired_ms: 0,
        };
        let mut retired = retired;
        retired.retired_ms = retired.budget.check()?;
        validate(&retired, &retired.original)?;
        let raw = serde_json::to_string(&retired).map_err(|error| error.to_string())?;
        if raw.len() > 16 * 1024 * 1024 {
            return Err("original stream retirement exceeds its fixed bound".into());
        }
        let transaction = connection
            .transaction()
            .map_err(|error| error.to_string())?;
        let expected = reference(&retired.original.intent)?;
        let mut pin = reference_record(&transaction, &expected.reference_id)?
            .ok_or("original stream pin is unavailable")?;
        if pin.reference != expected || !pin.active || pin.release_digest.is_some() {
            return Err("stream retirement changed its original pin".into());
        }
        pin.active = false;
        pin.release_digest = Some(digest(&retired)?);
        transaction
            .execute(
                "INSERT INTO content_stream_retirements VALUES (?1,?2)",
                params![id, raw],
            )
            .map_err(|error| error.to_string())?;
        transaction
            .execute(
                "UPDATE content_references SET record=?1 WHERE reference_id=?2",
                params![
                    serde_json::to_string(&pin).map_err(|error| error.to_string())?,
                    expected.reference_id
                ],
            )
            .map_err(|error| error.to_string())?;
        let mut heads = layout::head(&transaction, "stream_retirement_ids")?;
        heads.insert(id);
        layout::write_head(&transaction, "stream_retirement_ids", &heads)?;
        layout::within_budget(&transaction)?;
        retired.budget.check()?;
        transaction.commit().map_err(|error| error.to_string())?;
        serde_json::to_value(retired).map_err(|error| error.to_string())
    }
}
