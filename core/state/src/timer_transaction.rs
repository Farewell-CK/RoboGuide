//! Conservative rollback of an actually unchanged original internal timer transaction.

use super::*;

#[cfg(test)]
#[path = "timer_transaction_tests.rs"]
mod tests;

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
        let committed = *self
            .committed_sql_witness
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        let Some((committed_changes, data_version)) = committed else {
            return Ok(false);
        };
        if *sequence != start
            || connection.total_changes() != original_changes
            || original_changes != committed_changes
            || connection.query_row("PRAGMA data_version", [], |row| row.get::<_, u64>(0))?
                != data_version
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
