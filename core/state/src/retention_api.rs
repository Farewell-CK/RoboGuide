//! Storage facade preserving the existing Controller transaction and single-writer ordering.

use super::*;

impl SqliteEventLog {
    /// Requires explicit storage negotiation; reading old deployments never migrates their policy.
    fn require_retention_storage(&self) -> Result<(), SqliteEventLogError> {
        if self.events_table != "controller_v3_events" {
            return Err(SqliteEventLogError::ArchivePolicy(
                "retention requires explicit Controller storage v3".into(),
            ));
        }
        Ok(())
    }

    /// Reads the current policy without extending expiry or taking maintenance authority.
    pub fn retention_policy(&self) -> Result<RetentionPolicyState, SqliteEventLogError> {
        self.require_retention_storage()?;
        self.ensure_no_open_batch()?;
        let connection = self
            .connection
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        retention::policy(&connection)
    }

    /// Records an administrator policy update within the caller's original SQL event batch.
    pub fn update_retention_policy(
        &self,
        command: &RetentionPolicyCommand,
        now_ms: u64,
    ) -> Result<(RetentionPolicyReceipt, bool), SqliteEventLogError> {
        self.require_retention_storage()?;
        self.require_writes_enabled()?;
        let connection = self
            .connection
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        let before = connection.total_changes();
        let receipt = retention::update_policy(&connection, command, now_ms)?;
        Ok((receipt, connection.total_changes() != before))
    }

    /// Advances only the Controller's persisted maintenance clock under the original writer.
    pub fn observe_retention_clock(&self, now_ms: u64) -> Result<(), SqliteEventLogError> {
        self.require_retention_storage()?;
        self.require_writes_enabled()?;
        let connection = self
            .connection
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        retention::observe_clock(&connection, now_ms)
    }

    /// Assesses one bounded original prefix; unknown references cannot authorize compaction.
    pub fn retention_status(
        &self,
        now_ms: u64,
        checkpoint_sequence: u64,
        protection: &RetentionProtection,
    ) -> Result<RetentionStatus, SqliteEventLogError> {
        self.require_retention_storage()?;
        self.ensure_no_open_batch()?;
        let connection = self
            .connection
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        retention::evaluate(&connection, now_ms, checkpoint_sequence, protection)
    }

    /// Returns exact original decision-input pins; other reference kinds require independent coverage.
    pub fn decision_retention_references(
        &self,
        start_sequence: u64,
        end_sequence: u64,
    ) -> Result<RetentionDecisionReferences, SqliteEventLogError> {
        self.require_retention_storage()?;
        self.ensure_no_open_batch()?;
        let connection = self
            .connection
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        retention_references::decision_references(&connection, start_sequence, end_sequence)
    }

    /// Returns original pending archive identity before a worker can select a new cutoff or budget.
    pub fn pending_archive_operation(&self) -> Result<Option<ArchiveIntent>, SqliteEventLogError> {
        self.ensure_no_open_batch()?;
        let connection = self
            .connection
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        let mut statement=connection.prepare("SELECT command_id FROM event_archive_operations WHERE state='pending' ORDER BY command_id LIMIT 2")?;
        let ids = statement
            .query_map([], |row| row.get::<_, String>(0))?
            .collect::<Result<Vec<_>, _>>()?;
        if ids.len() > 1 {
            return Err(SqliteEventLogError::ArchivePolicy(
                "multiple original pending archives require explicit reconciliation".into(),
            ));
        }
        ids.first()
            .map(|id| {
                archive_operation::existing(&connection, id)?
                    .map(|(intent, _)| intent)
                    .ok_or_else(|| {
                        SqliteEventLogError::Codec("original pending archive disappeared".into())
                    })
            })
            .transpose()
    }

    /// Fully audits derived source/reference rows without granting any action or deletion permission.
    pub fn audit_retention_reference_index(&self) -> Result<(), SqliteEventLogError> {
        self.require_retention_storage()?;
        self.ensure_no_open_batch()?;
        let connection = self
            .connection
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        let latest=connection.query_row("SELECT MAX(COALESCE((SELECT MAX(sequence) FROM controller_v3_events),0),COALESCE((SELECT CAST(value AS INTEGER) FROM event_log_metadata WHERE key='history_floor'),0))",[],|row|row.get::<_,u64>(0))?;
        retention_references::validate(&connection, latest)
    }
}
