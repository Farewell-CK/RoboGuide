//! Transactional command evidence shared by checkpoint, replication and recovery validation.

use super::*;

/// Reads one complete command row using the stable SQL projection.
pub(super) fn from_row(row: &rusqlite::Row<'_>) -> rusqlite::Result<ControlCommandJournalEntry> {
    Ok(ControlCommandJournalEntry {
        command_id: row.get(0)?,
        causation_id: row.get(1)?,
        authority_revision: row.get(2)?,
        controller_epoch: row.get(3)?,
        plan_digest: row.get(4)?,
        input_digest: row.get(5)?,
        ownership_digest: row.get(6)?,
        resulting_state_digest: row.get(7)?,
        checkpoint_event_sequence: row.get(8)?,
        checkpoint_schema: row.get(9)?,
    })
}

/// Stable column order for journal reads, including restored replica materialization.
const COLUMNS: &str = "command_id,causation_id,authority_revision,controller_epoch,plan_digest,input_digest,ownership_digest,resulting_state_digest,checkpoint_event_sequence,checkpoint_schema";

/// Returns the current authority revision independently of event count.
pub(super) fn revision(connection: &Connection) -> Result<u64, SqliteEventLogError> {
    Ok(connection.query_row(
        "SELECT COALESCE(MAX(authority_revision),0) FROM control_command_journal",
        [],
        |row| row.get(0),
    )?)
}

/// Reads a bounded transaction delta, with no relationship to possibly unchanged event sequences.
pub(super) fn since(
    connection: &Connection,
    revision: u64,
) -> Result<Vec<ControlCommandJournalEntry>, SqliteEventLogError> {
    let mut statement = connection.prepare(&format!("SELECT {COLUMNS} FROM control_command_journal WHERE authority_revision>?1 ORDER BY authority_revision"))?;
    let rows = statement
        .query_map([revision], from_row)?
        .collect::<Result<Vec<_>, _>>()?;
    Ok(rows)
}

/// Returns an exact existing identity before assigning a new monotonic revision.
pub(super) fn existing(
    connection: &Connection,
    id: &str,
) -> Result<Option<ControlCommandJournalEntry>, SqliteEventLogError> {
    Ok(connection
        .query_row(
            &format!("SELECT {COLUMNS} FROM control_command_journal WHERE command_id=?1"),
            [id],
            from_row,
        )
        .optional()?)
}

/// Inserts only exact command evidence; a conflicting identity cannot overwrite prior acceptance.
pub(super) fn insert(
    connection: &Connection,
    command: &ControlCommandJournalEntry,
) -> Result<(), SqliteEventLogError> {
    if let Some(prior) = existing(connection, &command.command_id)? {
        if &prior != command {
            return Err(SqliteEventLogError::Codec(
                "Control command identity conflicts with durable acceptance".into(),
            ));
        }
        return Ok(());
    }
    connection.execute(
        "INSERT INTO control_command_journal VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10)",
        params![
            command.command_id,
            command.causation_id,
            command.authority_revision,
            command.controller_epoch,
            command.plan_digest,
            command.input_digest,
            command.ownership_digest,
            command.resulting_state_digest,
            command.checkpoint_event_sequence,
            command.checkpoint_schema
        ],
    )?;
    Ok(())
}

/// Validates the exact checkpoint high-water; a legacy store may explicitly have no journal yet.
pub(super) fn validate(
    connection: &Connection,
    checkpoint: Option<&PersistedCheckpoint>,
) -> Result<(), SqliteEventLogError> {
    let head = connection.query_row(&format!("SELECT {COLUMNS} FROM control_command_journal ORDER BY authority_revision DESC LIMIT 1"), [], from_row).optional()?;
    let required: Option<String> = connection
        .query_row(
            "SELECT value FROM event_log_metadata WHERE key='journal_committed_revision'",
            [],
            |row| row.get(0),
        )
        .optional()?;
    match (head, checkpoint) {
        (Some(head), Some(checkpoint)) => {
            head.validate_checkpoint(checkpoint)?;
            if (crate::checkpoint_requires_authority_journal(&checkpoint.schema)
                && required.is_none())
                || required.is_some_and(|revision| revision != head.authority_revision.to_string())
            {
                return Err(SqliteEventLogError::Codec(
                    "Control journal high-water differs from committed revision".into(),
                ));
            }
        }
        (None, Some(checkpoint))
            if required.is_none()
                && !crate::checkpoint_requires_authority_journal(&checkpoint.schema) => {}
        (None, None) if required.is_none() => {}
        _ => {
            return Err(SqliteEventLogError::Codec(
                "Control journal and checkpoint authority are detached".into(),
            ));
        }
    }
    Ok(())
}

impl ControlCommandJournalEntry {
    /// Confirms that the journal head describes these exact authority bytes and codec.
    pub fn validate_checkpoint(
        &self,
        checkpoint: &PersistedCheckpoint,
    ) -> Result<(), SqliteEventLogError> {
        if self.command_id.trim().is_empty()
            || self.input_digest.trim().is_empty()
            || self.checkpoint_schema != checkpoint.schema
            || self.checkpoint_event_sequence != checkpoint.event_sequence
            || self.resulting_state_digest
                != format!(
                    "sha256:{:x}",
                    Sha256::digest(checkpoint.checkpoint_json.as_bytes())
                )
        {
            return Err(SqliteEventLogError::Codec(
                "Control journal does not match the complete checkpoint".into(),
            ));
        }
        Ok(())
    }
}
