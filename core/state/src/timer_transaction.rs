//! Conservative rollback of an actually unchanged original internal timer transaction.

use super::*;

#[cfg(test)]
#[path = "timer_transaction_tests.rs"]
mod tests;

/// Exact process-local SQL evidence; no serialized flag can recreate this completed-writer witness.
#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct SqlWitness {
    /// Original connection writes include statements later rolled back.
    pub(super) changes: u64,
    /// External commits remain independent of original local writes.
    data_version: u64,
    /// Own DDL cannot borrow the prior authority snapshot.
    schema_version: u64,
    /// Explicit storage generation remains independently sampled.
    user_version: u64,
    /// Journal locking cannot change under an unchanged witness.
    journal_mode: String,
}

impl SqlWitness {
    /// Samples original actual counters/headers; callers retain the actual connection and writer.
    pub(super) fn observe(connection: &Connection) -> Result<Self, SqliteEventLogError> {
        Ok(Self {
            changes: connection.total_changes(),
            data_version: connection.query_row("PRAGMA data_version", [], |r| r.get(0))?,
            schema_version: connection.query_row("PRAGMA schema_version", [], |r| r.get(0))?,
            user_version: connection.query_row("PRAGMA user_version", [], |r| r.get(0))?,
            journal_mode: connection.query_row("PRAGMA journal_mode", [], |r| r.get(0))?,
        })
    }
}

impl SqliteEventLog {
    /// Rolls back only an untouched real batch with the exact original committed checkpoint.
    /// Callers retain the original application writer; external commands must keep their journals.
    /// False leaves the original transaction open for normal checkpoint/command/Commit handling.
    pub fn rollback_unchanged_timer_batch(
        &self,
        schema: &str,
        checkpoint_json: &str,
    ) -> Result<bool, SqliteEventLogError> {
        self.require_writes_enabled()?;
        self.observe_write_result(
            self.rollback_unchanged_timer_batch_inner(schema, checkpoint_json),
        )
    }

    /// Checks actual SQL changes, events, pending writes and original raw checkpoint before rollback.
    fn rollback_unchanged_timer_batch_inner(
        &self,
        schema: &str,
        checkpoint_json: &str,
    ) -> Result<bool, SqliteEventLogError> {
        let mut batch = self
            .batch_start_number
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        let start = batch.ok_or_else(|| {
            SqliteEventLogError::Codec(
                "unchanged timer check requires an open original batch".into(),
            )
        })?;
        let mut changes = self
            .batch_start_changes
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        let Some(original_changes) = *changes else {
            return Ok(false);
        };
        let sequence = self
            .next_event_number
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        let connection = self
            .connection
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        let committed = self
            .committed_sql_witness
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?
            .clone();
        let Some(committed) = committed else {
            return Ok(false);
        };
        if *sequence != start
            || connection.total_changes() != original_changes
            || original_changes != committed.changes
            || SqlWitness::observe(&connection)? != committed
            || self
                .pending_checkpoint_cache
                .lock()
                .map_err(|_| SqliteEventLogError::LockPoisoned)?
                .is_some()
        {
            return Ok(false);
        }
        let previous = self
            .checkpoint_cache
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?
            .clone();
        let Some(previous) = previous else {
            return Ok(false);
        };
        if previous.snapshot.schema != schema
            || previous.snapshot.event_sequence != start
            || previous.snapshot.checkpoint_json != checkpoint_json
        {
            return Ok(false);
        }
        let actual = checkpoint_store::read(&connection)?;
        if actual.as_ref().map(|stored| &stored.snapshot) != Some(&previous.snapshot) {
            return Err(SqliteEventLogError::Codec(
                "unchanged timer actual checkpoint differs from original committed cache".into(),
            ));
        }
        authority_journal::validate(&connection, Some(&previous.snapshot))?;
        connection.execute_batch("ROLLBACK")?;
        *changes = None;
        *batch = None;
        Ok(true)
    }
}
