//! Transport-neutral quorum acceptance and exact materialized event/checkpoint import.

use super::*;
use sha2::Sha256;
#[cfg(test)]
#[path = "application_replication/tests.rs"]
mod tests;

/// Versioned exact application batch, independent of Node Protocol and physical lifecycle.
pub const APPLICATION_COMMIT_SCHEMA: &str = "roboguide.application-commit/v2";

/// Exact transaction candidate requiring one durable quorum decision before local Commit.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ApplicationCommitBatch {
    /// Immutable DTO codec identity.
    pub schema: String,
    /// Previously accepted event tail.
    pub previous_event_sequence: u64,
    /// Digest of the preceding complete checkpoint, absent only for empty bootstrap.
    pub previous_checkpoint_digest: Option<String>,
    /// Complete authority target, covering every supplied event.
    pub checkpoint: PersistedCheckpoint,
    /// Exact consecutive immutable event rows accepted by existing application authorities.
    pub events: Vec<PersistedEvent>,
    /// Exact new authority command rows, including changes producing no domain event.
    #[serde(default)]
    pub commands: Vec<ControlCommandJournalEntry>,
}

/// Composition-owned durability port; it grants no Task, matching or resource policy authority.
pub trait ApplicationBatchReplicator: Send + Sync {
    /// Supplies the immutable issuing epoch for audit and restore, independent of Node delivery.
    fn controller_epoch(&self) -> Option<String> {
        None
    }
    /// Identifies the startup-frozen cluster/profile whose acceptance is required.
    fn profile_identity(&self) -> &str;
    /// Requires current epoch and quorum before the caller starts a private write transaction.
    fn assert_write_authority(&self) -> Result<(), String>;
    /// Confirms durable quorum acceptance of the exact validated candidate.
    fn confirm_batch(&self, batch: ApplicationCommitBatch) -> Result<(), String>;
}

/// Hashes the complete application identity without coupling it to local storage representation.
pub fn checkpoint_identity(checkpoint: &PersistedCheckpoint) -> String {
    let mut hash = Sha256::new();
    // This hash domain is immutable across DTO versions so old accepted identities remain valid.
    hash.update(b"roboguide.application-commit/v1");
    hash.update((checkpoint.schema.len() as u64).to_le_bytes());
    hash.update(checkpoint.schema.as_bytes());
    hash.update(checkpoint.event_sequence.to_le_bytes());
    hash.update((checkpoint.checkpoint_json.len() as u64).to_le_bytes());
    hash.update(checkpoint.checkpoint_json.as_bytes());
    format!("{:x}", hash.finalize())
}

impl ApplicationCommitBatch {
    /// Rejects wrong codecs, unbounded input, discontinuous evidence or an incomplete target.
    pub fn validate(&self) -> Result<(), SqliteEventLogError> {
        if !matches!(
            self.schema.as_str(),
            APPLICATION_COMMIT_SCHEMA | "roboguide.application-commit/v1"
        ) || self.events.len() > 8192
            || self.checkpoint.checkpoint_json.len() > 64 * 1024 * 1024
            || self.checkpoint.schema.trim().is_empty()
            || self
                .previous_event_sequence
                .checked_add(self.events.len() as u64)
                != Some(self.checkpoint.event_sequence)
        {
            return Err(SqliteEventLogError::Codec(
                "invalid application replication batch bounds/tail".into(),
            ));
        }
        for (offset, event) in self.events.iter().enumerate() {
            if event.sequence != self.previous_event_sequence + offset as u64 + 1 {
                return Err(SqliteEventLogError::Codec(
                    "replica batch evidence is not consecutive".into(),
                ));
            }
            validate_persisted_event(event)?;
        }
        if self.schema == "roboguide.application-commit/v1" {
            if !self.commands.is_empty()
                || crate::checkpoint_requires_authority_journal(&self.checkpoint.schema)
            {
                return Err(SqliteEventLogError::Codec(
                    "legacy replica codec cannot hide command authority".into(),
                ));
            }
            return Ok(());
        }
        if self.commands.is_empty()
            && self.previous_checkpoint_digest.as_deref()
                == Some(checkpoint_identity(&self.checkpoint).as_str())
            && self.events.is_empty()
        {
            return Ok(());
        }
        if self.commands.is_empty() || self.commands.len() > 8192 {
            return Err(SqliteEventLogError::Codec(
                "replica batch lacks bounded command authority".into(),
            ));
        }
        for commands in self.commands.windows(2) {
            if commands[0].authority_revision.checked_add(1) != Some(commands[1].authority_revision)
            {
                return Err(SqliteEventLogError::Codec(
                    "replica command revisions are discontinuous".into(),
                ));
            }
        }
        self.commands
            .last()
            .expect("checked nonempty")
            .validate_checkpoint(&self.checkpoint)?;
        Ok(())
    }
}

/// Checks immutable event envelope and typed codec without interpreting its authority semantics.
pub fn validate_persisted_event(event: &PersistedEvent) -> Result<(), SqliteEventLogError> {
    EventId::new(event.event_id.clone())
        .map_err(|error| SqliteEventLogError::Codec(error.to_string()))?;
    CorrelationId::new(event.correlation_id.clone())
        .map_err(|error| SqliteEventLogError::Codec(error.to_string()))?;
    if let Some(cause) = &event.causation_id {
        EventId::new(cause.clone())
            .map_err(|error| SqliteEventLogError::Codec(error.to_string()))?;
    }
    let payload: EventPayload = serde_json::from_str(&event.payload_json)
        .map_err(|error| SqliteEventLogError::Codec(error.to_string()))?;
    validate_payload_schema(&event.payload_schema, &payload)
}

impl SqliteEventLog {
    /// Returns the current immutable issuing epoch for transaction-bound evidence.
    pub fn issuing_controller_epoch(&self) -> String {
        self.replicator
            .as_ref()
            .and_then(|replicator| replicator.controller_epoch())
            .unwrap_or_else(|| "standalone".into())
    }
    /// Returns the required cluster identity without exposing its network or consensus state.
    pub fn replication_profile(&self) -> Option<&str> {
        self.replicator
            .as_ref()
            .map(|replicator| replicator.profile_identity())
    }
    /// Installs an optional quorum port before the application shares this store or creates authority.
    pub fn with_replicator(mut self, replicator: Arc<dyn ApplicationBatchReplicator>) -> Self {
        self.replicator = Some(replicator);
        self
    }

    /// Atomically installs already-quorum-accepted history into an inactive materialized Controller store.
    ///
    /// The supervisor holds the existing process writer lease and fences its child before this call.
    /// This never runs Control/Runtime reducers or delivers a command; ordinary startup must validate
    /// the imported application schema and restore all physical ambiguity conservatively.
    pub fn install_replica_view(
        &self,
        events: &[PersistedEvent],
        checkpoint: &PersistedCheckpoint,
    ) -> Result<(), SqliteEventLogError> {
        self.install_replica_view_with_history_floor(events, checkpoint, None)
    }

    /// Installs a checkpoint plus a retained event tail after a verified replication-history anchor.
    ///
    /// The complete checkpoint remains the recovery authority.  A nonzero history floor means
    /// older immutable event rows were compacted and therefore cannot be replayed from this local
    /// copy; subsequent event sequences continue from the checkpoint without being renumbered.
    pub fn install_replica_view_with_history_floor(
        &self,
        events: &[PersistedEvent],
        checkpoint: &PersistedCheckpoint,
        history_floor: Option<u64>,
    ) -> Result<(), SqliteEventLogError> {
        self.install_replica_authority(events, checkpoint, history_floor, &[])
    }

    /// Atomically materializes quorum-accepted command history beside its complete checkpoint.
    pub fn install_replica_authority(
        &self,
        events: &[PersistedEvent],
        checkpoint: &PersistedCheckpoint,
        history_floor: Option<u64>,
        commands: &[ControlCommandJournalEntry],
    ) -> Result<(), SqliteEventLogError> {
        self.require_writes_enabled()?;
        self.ensure_no_open_batch()?;
        if self.replicator.is_some()
            || history_floor.is_some_and(|floor| floor > checkpoint.event_sequence)
        {
            return Err(SqliteEventLogError::Codec(
                "replica materialization requires an inactive complete history".into(),
            ));
        }
        let first_sequence = events.first().map_or(0, |event| event.sequence);
        let expected_first = history_floor.map_or(1, |floor| floor.saturating_add(1));
        if !(events.is_empty()
            && ((checkpoint.event_sequence == 0 && history_floor.is_none())
                || history_floor == Some(checkpoint.event_sequence)))
            && (first_sequence != expected_first
                || history_floor.is_none() && checkpoint.event_sequence != events.len() as u64)
        {
            return Err(SqliteEventLogError::Codec(
                "replica view has missing evidence without a valid history floor".into(),
            ));
        }
        for (index, event) in events.iter().enumerate() {
            if event.sequence != expected_first + index as u64
                || event.sequence > checkpoint.event_sequence
            {
                return Err(SqliteEventLogError::Codec(
                    "replica view has missing evidence".into(),
                ));
            }
            validate_persisted_event(event)?;
        }
        let mut connection = self
            .connection
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        transaction.execute("DELETE FROM control_command_journal", [])?;
        transaction.execute(
            "DELETE FROM event_log_metadata WHERE key='journal_committed_revision'",
            [],
        )?;
        for command in commands {
            authority_journal::insert(&transaction, command)?;
        }
        if let Some(command) = commands.last() {
            command.validate_checkpoint(checkpoint)?;
            transaction.execute("INSERT INTO event_log_metadata(key,value) VALUES ('journal_committed_revision',?1)", [command.authority_revision.to_string()])?;
        }
        authority_journal::validate(&transaction, Some(checkpoint))?;
        transaction.execute("DELETE FROM events", [])?;
        transaction.execute("DELETE FROM controller_checkpoint_deltas", [])?;
        transaction.execute("DELETE FROM controller_checkpoint_head", [])?;
        for event in events {
            transaction.execute("INSERT INTO events(sequence,event_id,timestamp_ms,correlation_id,causation_id,payload_schema,payload_json)
                VALUES (?1,?2,?3,?4,?5,?6,?7)", params![event.sequence,event.event_id,event.timestamp_ms,event.correlation_id,
                event.causation_id,event.payload_schema,event.payload_json])?;
        }
        transaction.execute("INSERT INTO controller_checkpoint(singleton,event_sequence,schema,checkpoint_json) VALUES (1,?1,?2,?3)
            ON CONFLICT(singleton) DO UPDATE SET event_sequence=excluded.event_sequence,schema=excluded.schema,checkpoint_json=excluded.checkpoint_json",
            params![checkpoint.event_sequence,checkpoint.schema,checkpoint.checkpoint_json])?;
        transaction.execute(
            "INSERT INTO event_log_metadata(key,value) VALUES ('next_event_number',?1)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            [checkpoint.event_sequence.to_string()],
        )?;
        if let Some(floor) = history_floor {
            transaction.execute(
                "INSERT INTO event_log_metadata(key,value) VALUES ('history_floor',?1)
                 ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                [floor.to_string()],
            )?;
        } else {
            transaction.execute(
                "DELETE FROM event_log_metadata WHERE key='history_floor'",
                [],
            )?;
        }
        transaction.commit()?;
        *self
            .committed_authority_revision
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)? =
            authority_journal::revision(&connection)?;
        *self
            .next_event_number
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)? = checkpoint.event_sequence;
        *self
            .checkpoint_cache
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)? = checkpoint_store::read(&connection)?;
        *self
            .pending_checkpoint_cache
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)? = None;
        *self
            .last_error
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)? = None;
        Ok(())
    }
}
