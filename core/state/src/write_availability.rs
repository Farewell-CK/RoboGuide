//! Process-local write isolation and an exact unchanged-checkpoint recovery probe.

use super::*;
use std::time::{SystemTime, UNIX_EPOCH};

#[cfg(test)]
#[path = "write_availability_tests.rs"]
mod tests;

/// First failure retained unchanged until an explicitly verified recovery commits.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize)]
pub struct WriteFailure {
    /// Bounded diagnostic, never parsed to decide retry or lifecycle policy.
    pub reason: String,
    /// Wall clock observation for operators only; never a State freshness timestamp.
    pub observed_unix_ms: u64,
    /// Unknown replica/cache authority requires startup replay, not a local write probe.
    pub restart_required: bool,
}

/// Availability of this process and its last locally committed authority snapshot.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize)]
pub struct AuthorityWriteStatus {
    /// Closed query codec independent of the Controller's authority checkpoint schema.
    pub schema_version: String,
    /// Startup-generated actual process instance, absent in nonparticipating library profiles.
    pub process_id: Option<String>,
    /// Increases only on a new failure after a successful recovery in this process.
    pub generation: u64,
    /// False blocks application mutations, durable ACKs and derived dispatch.
    pub write_enabled: bool,
    /// Original failure; repeated rejected writes do not replace it.
    pub failure: Option<WriteFailure>,
    /// Complete checkpoint identity, distinct from the hash of its JSON body alone.
    pub checkpoint_identity: Option<String>,
    /// Original event tail; observation never advances it.
    pub checkpoint_event_sequence: Option<u64>,
    /// Original command high-water; zero-event authority revisions are independently retained.
    pub authority_revision: u64,
}

/// Exact original disabled-process command, supplied by an authenticated recovery administrator.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct WriteRecoveryCommand {
    /// Required codec, never inferred from other fields.
    pub schema_version: String,
    /// Stable bounded operation identity; same identity with different input is rejected.
    pub command_id: String,
    /// Disabled process instance obtained from the status query.
    pub process_id: String,
    /// Exact disabled generation, preventing stale repair from clearing another failure.
    pub generation: u64,
    /// Exact complete last committed checkpoint, obtained before requesting recovery.
    pub checkpoint_identity: String,
    /// Exact committed journal head, including zero-event changes.
    pub authority_revision: u64,
}

/// Immutable evidence for one successful zero-event recovery probe.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize)]
pub struct WriteRecoveryReceipt {
    /// Recovery receipt codec.
    pub schema_version: String,
    /// Original command, including its disabled process and checkpoint identity.
    pub command: WriteRecoveryCommand,
    /// Actual authenticated principal, independently rechecked under the writer gate.
    pub authority_subject: String,
    /// Durable command revision produced by the probe.
    pub authority_revision: u64,
}

/// Only the composition that owns the writer can opt into failure isolation.
#[derive(Default)]
pub(super) struct WriteAvailability {
    /// Startup identity enables the policy; library compatibility callers remain unchanged.
    process_id: Option<String>,
    /// Exact process-local failure generation, never renewed by rejected requests.
    generation: u64,
    /// Latched first failure; an ordinary write cannot clear it.
    failure: Option<WriteFailure>,
}

impl SqliteEventLog {
    /// Enables the application policy before sharing clones; never creates a new authority store.
    pub fn with_write_failure_fence(self, process_id: &str) -> Result<Self, SqliteEventLogError> {
        if process_id.is_empty()
            || process_id.len() > 128
            || process_id.chars().any(char::is_control)
        {
            return Err(SqliteEventLogError::Codec(
                "invalid availability process identity".into(),
            ));
        }
        {
            let mut availability = self
                .write_availability
                .lock()
                .map_err(|_| SqliteEventLogError::LockPoisoned)?;
            if availability.process_id.is_some() {
                return Err(SqliteEventLogError::Codec(
                    "availability policy already installed".into(),
                ));
            }
            availability.process_id = Some(process_id.into());
        }
        Ok(self)
    }

    /// Observes only committed memory and the first failure, even when SQL is unavailable.
    pub fn write_status(&self) -> Result<AuthorityWriteStatus, SqliteEventLogError> {
        let availability = self
            .write_availability
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        let checkpoint = self
            .checkpoint_cache
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        Ok(AuthorityWriteStatus {
            schema_version: "roboguide.authority-write-status/v0.1".into(),
            process_id: availability.process_id.clone(),
            generation: availability.generation,
            write_enabled: availability.failure.is_none(),
            failure: availability.failure.clone(),
            checkpoint_identity: checkpoint
                .as_ref()
                .map(|value| checkpoint_identity(&value.snapshot)),
            checkpoint_event_sequence: checkpoint
                .as_ref()
                .map(|value| value.snapshot.event_sequence),
            authority_revision: *self
                .committed_authority_revision
                .lock()
                .map_err(|_| SqliteEventLogError::LockPoisoned)?,
        })
    }

    /// Latches a shared application failure while the composition still owns its actual writer.
    ///
    /// This does not roll back SQL, stop a device, release ownership or persist invented evidence.
    pub fn disable_writes(
        &self,
        reason: &str,
        restart_required: bool,
    ) -> Result<(), SqliteEventLogError> {
        let mut availability = self
            .write_availability
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        if availability.process_id.is_some() && availability.failure.is_none() {
            availability.generation = availability.generation.checked_add(1).ok_or_else(|| {
                SqliteEventLogError::Codec("availability generation exhausted".into())
            })?;
            availability.failure = Some(WriteFailure {
                reason: reason.chars().take(2048).collect(),
                observed_unix_ms: u64::try_from(
                    SystemTime::now()
                        .duration_since(UNIX_EPOCH)
                        .unwrap_or_default()
                        .as_millis(),
                )
                .unwrap_or(u64::MAX),
                restart_required: restart_required || self.replicator.is_some(),
            });
        }
        Ok(())
    }

    /// Rejects a new effect before it can enter SQLite or application candidate mutation.
    pub fn require_writes_enabled(&self) -> Result<(), SqliteEventLogError> {
        let availability = self
            .write_availability
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        match &availability.failure {
            Some(failure) => Err(SqliteEventLogError::WriteDisabled(failure.reason.clone())),
            None => Ok(()),
        }
    }

    /// Requires the original current replica epoch/quorum before exposing protected cached content.
    /// Standalone reads remain available through local SQL failure; no source timestamp is refreshed.
    pub fn require_read_authority(&self) -> Result<(), SqliteEventLogError> {
        if let Some(replicator) = &self.replicator {
            replicator
                .assert_write_authority()
                .map_err(SqliteEventLogError::Replication)?;
        }
        Ok(())
    }

    /// Converts actual persistence failures into the opt-in first-failure latch, never input errors.
    pub(super) fn observe_write_result<T>(
        &self,
        result: Result<T, SqliteEventLogError>,
    ) -> Result<T, SqliteEventLogError> {
        if let Err(
            error @ (SqliteEventLogError::Sqlite(_)
            | SqliteEventLogError::Replication(_)
            | SqliteEventLogError::LockPoisoned),
        ) = &result
        {
            self.disable_writes(
                &error.to_string(),
                matches!(error, SqliteEventLogError::LockPoisoned),
            )?;
        }
        result
    }

    /// Returns the exact cached snapshot without reading tentative SQL or refreshing source times.
    pub fn committed_checkpoint(&self) -> Result<Option<PersistedCheckpoint>, SqliteEventLogError> {
        Ok(self
            .checkpoint_cache
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?
            .as_ref()
            .map(|value| (*value.snapshot).clone()))
    }

    /// Probes unchanged standalone authority under the caller's original authorized writer guard.
    ///
    /// A receipt from another process can be observed but never clears this process's failure.
    pub fn recover_writes(
        &self,
        command: &WriteRecoveryCommand,
        subject: &str,
    ) -> Result<WriteRecoveryReceipt, SqliteEventLogError> {
        if command.schema_version != "roboguide.authority-write-recovery/v0.1"
            || command.command_id.is_empty()
            || command.command_id.trim() != command.command_id
            || command.command_id.len() > 256
            || command.command_id.chars().any(char::is_control)
            || command.process_id.is_empty()
            || command.process_id.len() > 128
            || command.process_id.chars().any(char::is_control)
            || subject.is_empty()
            || subject.len() > 256
            || subject.chars().any(char::is_control)
            || command.generation == 0
            || command.checkpoint_identity.len() != 64
            || !command
                .checkpoint_identity
                .bytes()
                .all(|value| value.is_ascii_hexdigit())
        {
            return Err(SqliteEventLogError::Codec(
                "invalid original write-recovery command".into(),
            ));
        }
        self.ensure_no_open_batch()?;
        let input = serde_json::to_vec(&(command, subject))
            .map_err(|error| SqliteEventLogError::Codec(error.to_string()))?;
        let input_digest = format!("sha256:{:x}", Sha256::digest(input));
        let journal_id = format!("write-recovery:{}", command.command_id);
        let mut availability = self
            .write_availability
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        let mut cache = self
            .checkpoint_cache
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        let mut connection = self
            .connection
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        if let Some(original) = authority_journal::existing(&connection, &journal_id)? {
            if original.input_digest != input_digest
                || original.causation_id.as_deref() != Some(subject)
            {
                return Err(SqliteEventLogError::Codec(
                    "original write-recovery command conflicts".into(),
                ));
            }
            return Ok(WriteRecoveryReceipt {
                schema_version: "roboguide.authority-write-recovery-receipt/v0.1".into(),
                command: command.clone(),
                authority_subject: subject.into(),
                authority_revision: original.authority_revision,
            });
        }
        let failure = availability.failure.as_ref().ok_or_else(|| {
            SqliteEventLogError::WriteDisabled(
                "process has no disabled generation to recover".into(),
            )
        })?;
        if availability.process_id.as_deref() != Some(command.process_id.as_str())
            || availability.generation != command.generation
            || failure.restart_required
            || self.replicator.is_some()
        {
            return Err(SqliteEventLogError::WriteDisabled(
                "original process/generation differs or startup replay is required".into(),
            ));
        }
        let original = cache.as_ref().ok_or_else(|| {
            SqliteEventLogError::WriteDisabled("committed authority cache is unavailable".into())
        })?;
        if checkpoint_identity(&original.snapshot) != command.checkpoint_identity
            || self
                .pending_checkpoint_cache
                .lock()
                .map_err(|_| SqliteEventLogError::LockPoisoned)?
                .is_some()
            || *self
                .next_event_number
                .lock()
                .map_err(|_| SqliteEventLogError::LockPoisoned)?
                != original.snapshot.event_sequence
        {
            return Err(SqliteEventLogError::WriteDisabled(
                "original checkpoint or pending authority differs".into(),
            ));
        }
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let actual = checkpoint_store::read(&transaction)?.map(|value| value.snapshot);
        authority_journal::validate(&transaction, actual.as_deref())?;
        let mut committed_revision = self
            .committed_authority_revision
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)?;
        if actual.as_deref() != Some(original.snapshot.as_ref())
            || *committed_revision != command.authority_revision
            || authority_journal::revision(&transaction)? != command.authority_revision
        {
            return Err(SqliteEventLogError::WriteDisabled(
                "SQL authority differs from last committed cache; replay required".into(),
            ));
        }
        let revision = authority_journal::revision(&transaction)?
            .checked_add(1)
            .ok_or_else(|| SqliteEventLogError::Codec("authority revision exhausted".into()))?;
        let entry = ControlCommandJournalEntry {
            command_id: journal_id,
            causation_id: Some(subject.into()),
            authority_revision: revision,
            controller_epoch: Some("standalone".into()),
            plan_digest: None,
            input_digest,
            ownership_digest: None,
            resulting_state_digest: format!(
                "sha256:{:x}",
                Sha256::digest(original.snapshot.checkpoint_json.as_bytes())
            ),
            checkpoint_event_sequence: original.snapshot.event_sequence,
            checkpoint_schema: original.snapshot.schema.clone(),
        };
        entry.validate_checkpoint(&original.snapshot)?;
        authority_journal::insert(&transaction, &entry)?;
        let candidate = checkpoint_store::write(
            &transaction,
            Some(original),
            self.checkpoint_policy,
            &original.snapshot.schema,
            &original.snapshot.checkpoint_json,
            original.snapshot.event_sequence,
        )?;
        transaction.execute("INSERT INTO event_log_metadata(key,value) VALUES ('journal_committed_revision',?1) ON CONFLICT(key) DO UPDATE SET value=excluded.value", [revision.to_string()])?;
        transaction.commit()?;
        *cache = Some(candidate);
        *committed_revision = revision;
        *self
            .last_error
            .lock()
            .map_err(|_| SqliteEventLogError::LockPoisoned)? = None;
        availability.failure = None;
        Ok(WriteRecoveryReceipt {
            schema_version: "roboguide.authority-write-recovery-receipt/v0.1".into(),
            command: command.clone(),
            authority_subject: subject.into(),
            authority_revision: revision,
        })
    }
}
