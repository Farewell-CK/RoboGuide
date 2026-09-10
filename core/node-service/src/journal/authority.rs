//! Durable execution-journal authority operations.

use super::*;

impl ExecutionJournal {
    /// Opens or creates a journal and fences incomplete dispatches from replay.
    ///
    /// Opening the same database represents a new Node Service process lifetime. Any
    /// `Dispatching` row is durably fenced before replay. Rows without a local handle are either
    /// known pre-dispatch failures or ambiguous calls; rows with a handle are also fenced until a
    /// status fact confirms what happened after the acceptance fact's crash window.
    pub fn open(path: impl AsRef<Path>) -> Result<Self, JournalError> {
        let mut connection = Connection::open(path)?;
        configure_connection(&connection)?;
        create_schema(&mut connection)?;
        recover_ambiguous_dispatches(&connection)?;
        Ok(Self {
            connection: Mutex::new(connection),
        })
    }

    /// Commits a new `Dispatching` identity before granting permission to call local execute.
    ///
    /// Repeating the same immutable tuple returns `Existing`; changing invocation bytes,
    /// workflow digest, or committed resources returns `Conflict`. Neither case grants a
    /// second dispatch permission.
    pub fn prepare_dispatch(
        &self,
        execution_id: &str,
        spec: &ExecutionSpec,
    ) -> Result<PrepareDispatch, JournalError> {
        if execution_id.is_empty() {
            return Err(JournalError::InvalidExecutionId);
        }
        let mut connection = self.lock_connection()?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        if let Some(existing) = load_execution(&transaction, execution_id)? {
            let outcome = if existing.spec == *spec {
                PrepareDispatch::Existing(existing)
            } else {
                PrepareDispatch::Conflict(existing)
            };
            transaction.commit()?;
            return Ok(outcome);
        }
        let resource_ids = serde_json::to_string(&spec.resource_ids)?;
        transaction.execute(
            "INSERT INTO executions (
                execution_id, invocation_content, invocation_digest, workflow_digest,
                resource_ids, local_handle, cancellation_requested, sequence, status, reason
             ) VALUES (?1, ?2, ?3, ?4, ?5, NULL, 0, 0, 'dispatching', '')",
            params![
                execution_id,
                &spec.invocation_content,
                &spec.invocation_digest,
                &spec.workflow_digest,
                resource_ids,
            ],
        )?;
        let cancelled: bool = transaction.query_row(
            "SELECT EXISTS(SELECT 1 FROM cancellation_intents WHERE execution_id = ?1)",
            [execution_id],
            |row| row.get(0),
        )?;
        if cancelled {
            transaction.execute("UPDATE executions SET status = 'cancelled', sequence = 1, reason = 'cancelled before local dispatch' WHERE execution_id = ?1", [execution_id])?;
        }
        let record = load_execution(&transaction, execution_id)?.ok_or_else(|| {
            JournalError::Corrupt("new dispatch record could not be read".to_string())
        })?;
        transaction.commit()?;
        Ok(if cancelled {
            PrepareDispatch::Existing(record)
        } else {
            PrepareDispatch::Start(record)
        })
    }

    /// Persists a cancellation tombstone even when Execute has not reached this Node yet.
    pub fn request_cancellation(&self, execution_id: &str) -> Result<(), JournalError> {
        if execution_id.trim().is_empty() {
            return Err(JournalError::InvalidExecutionId);
        }
        let mut connection = self.lock_connection()?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        transaction.execute(
            "INSERT OR IGNORE INTO cancellation_intents(execution_id) VALUES (?1)",
            [execution_id],
        )?;
        transaction.execute("UPDATE executions SET status = 'cancelled', sequence = sequence + 1, reason = 'cancelled before local dispatch' WHERE execution_id = ?1 AND status = 'dispatching' AND NOT EXISTS(SELECT 1 FROM local_dispatch_authorizations WHERE execution_id = ?1)", [execution_id])?;
        transaction.commit()?;
        Ok(())
    }

    /// Reports durable cancellation demand without implying Local EAIOS accepted or completed it.
    pub fn cancellation_pending(&self, execution_id: &str) -> Result<bool, JournalError> {
        Ok(self.lock_connection()?.query_row(
            "SELECT EXISTS(SELECT 1 FROM cancellation_intents WHERE execution_id = ?1)",
            [execution_id],
            |row| row.get(0),
        )?)
    }

    /// Persists the local handle returned by the single permitted physical dispatch.
    ///
    /// Repeating the same handle is idempotent. A different handle or a handle arriving
    /// after the execution became ambiguous is rejected rather than changing identity.
    pub fn record_local_handle(
        &self,
        execution_id: &str,
        local_handle: &str,
    ) -> Result<JournalExecution, JournalError> {
        if local_handle.is_empty() {
            return Err(JournalError::InvalidLocalHandle);
        }
        let mut connection = self.lock_connection()?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let existing = required_execution(&transaction, execution_id)?;
        match existing.local_handle.as_deref() {
            Some(handle) if handle == local_handle => {
                transaction.commit()?;
                return Ok(existing);
            }
            Some(_) => return Err(JournalError::LocalHandleConflict(execution_id.to_string())),
            None if existing.status != JournalStatus::Dispatching => {
                return Err(JournalError::AmbiguousDispatch(execution_id.to_string()));
            }
            None => {}
        }
        transaction.execute(
            "UPDATE executions SET local_handle = ?2 WHERE execution_id = ?1",
            params![execution_id, local_handle],
        )?;
        let updated = required_execution(&transaction, execution_id)?;
        transaction.commit()?;
        Ok(updated)
    }

    /// Durably marks the exact point after preparation when local dispatch may begin.
    ///
    /// The marker is idempotent and must be written immediately before invoking Local EAIOS. A
    /// restart can then distinguish a known pre-dispatch interruption from an ambiguous call.
    pub fn authorize_local_dispatch(&self, execution_id: &str) -> Result<(), JournalError> {
        let mut connection = self.lock_connection()?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let execution = required_execution(&transaction, execution_id)?;
        if execution.status != JournalStatus::Dispatching || execution.local_handle.is_some() {
            return Err(JournalError::InvalidTransition(
                execution_id.to_string(),
                "local dispatch may only be authorized before the first handle is recorded"
                    .to_string(),
            ));
        }
        transaction.execute(
            "INSERT OR IGNORE INTO local_dispatch_authorizations (execution_id) VALUES (?1)",
            [execution_id],
        )?;
        transaction.commit()?;
        Ok(())
    }

    /// Durably records acceptance of a local cancellation request without changing status.
    ///
    /// This operation is idempotent. It deliberately does not synthesize `Cancelled`;
    /// only a later execution fact may establish that terminal business state.
    pub fn record_cancellation_requested(
        &self,
        execution_id: &str,
    ) -> Result<JournalExecution, JournalError> {
        let mut connection = self.lock_connection()?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let existing = required_execution(&transaction, execution_id)?;
        if existing.cancellation_requested || existing.status.is_terminal() {
            transaction.commit()?;
            return Ok(existing);
        }
        transaction.execute(
            "UPDATE executions SET cancellation_requested = 1 WHERE execution_id = ?1",
            [execution_id],
        )?;
        let updated = required_execution(&transaction, execution_id)?;
        transaction.commit()?;
        Ok(updated)
    }

    /// Persists one progressive execution fact with monotonic sequence enforcement.
    ///
    /// An exact duplicate is idempotent. Stale sequences, sequence reuse with different
    /// content, and transitions away from a terminal fact are rejected.
    pub fn record_status(
        &self,
        execution_id: &str,
        sequence: u64,
        status: JournalStatus,
        reason: impl Into<String>,
    ) -> Result<JournalExecution, JournalError> {
        if status == JournalStatus::Dispatching {
            return Err(JournalError::InvalidTransition(
                execution_id.to_string(),
                "Dispatching may only be created by prepare_dispatch".to_string(),
            ));
        }
        let reason = reason.into();
        let sequence_i64 = i64::try_from(sequence).map_err(|_| JournalError::SequenceOverflow)?;
        let mut connection = self.lock_connection()?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let existing = required_execution(&transaction, execution_id)?;
        if sequence == existing.sequence && status == existing.status && reason == existing.reason {
            transaction.commit()?;
            return Ok(existing);
        }
        if sequence <= existing.sequence {
            return Err(JournalError::StaleSequence {
                execution_id: execution_id.to_string(),
                current: existing.sequence,
                received: sequence,
            });
        }
        if existing.status.is_terminal() {
            return Err(JournalError::InvalidTransition(
                execution_id.to_string(),
                "terminal execution facts are immutable".to_string(),
            ));
        }
        transaction.execute(
            "UPDATE executions SET sequence = ?2, status = ?3, reason = ?4
             WHERE execution_id = ?1",
            params![execution_id, sequence_i64, status.as_str(), reason],
        )?;
        let updated = required_execution(&transaction, execution_id)?;
        transaction.commit()?;
        Ok(updated)
    }

    /// Persists one immutable prepared artifact or confirms an exact prior record.
    ///
    /// Reusing a binding or map revision with different bytes, metadata, provenance, or path is
    /// rejected. A new record requires the exact durable preparation fence created before the
    /// mutable source was read; an exact already-stored record remains idempotent after that fence
    /// has been atomically cleared.
    pub fn record_prepared_artifact(
        &self,
        artifact: &PreparedArtifactRecord,
    ) -> Result<PreparedArtifactRecord, JournalError> {
        let frozen_path = artifact.frozen_path.to_str().ok_or_else(|| {
            JournalError::InvalidPreparedArtifact(
                "frozen artifact path must be valid UTF-8".to_string(),
            )
        })?;
        let manifest_json = serde_json::to_string(&artifact.manifest)?;
        let map_id = artifact.manifest.selector().map_id().as_str();
        let revision_id = artifact.manifest.selector().revision_id().as_str();
        let mut connection = self.lock_connection()?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        required_execution(&transaction, &artifact.producer_execution_id)?;
        if let Some(existing) =
            load_prepared_artifact_by_binding(&transaction, &artifact.binding_id)?
        {
            if existing == *artifact {
                clear_matching_artifact_preparation(&transaction, artifact)?;
                transaction.commit()?;
                return Ok(existing);
            }
            return Err(JournalError::PreparedArtifactConflict(
                artifact.binding_id.clone(),
            ));
        }
        if let Some(existing) =
            load_prepared_artifact_by_selector(&transaction, map_id, revision_id)?
        {
            if existing == *artifact {
                transaction.commit()?;
                return Ok(existing);
            }
            return Err(JournalError::PreparedArtifactConflict(format!(
                "{map_id}/{revision_id}"
            )));
        }
        validate_artifact_preparation_owner(&transaction, artifact)?;
        transaction.execute(
            "INSERT INTO prepared_artifacts (
                binding_id, map_id, revision_id, producer_execution_id, frozen_path, manifest_json
             ) VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
            params![
                &artifact.binding_id,
                map_id,
                revision_id,
                &artifact.producer_execution_id,
                frozen_path,
                manifest_json,
            ],
        )?;
        let stored = load_prepared_artifact_by_binding(&transaction, &artifact.binding_id)?
            .ok_or_else(|| {
                JournalError::Corrupt(
                    "prepared artifact could not be read after insert".to_string(),
                )
            })?;
        clear_matching_artifact_preparation(&transaction, artifact)?;
        transaction.commit()?;
        Ok(stored)
    }

    /// Grants exactly one durable attempt to freeze a mutable output for an execution.
    ///
    /// The first exact execution/binding tuple returns [`PrepareArtifactFreeze::Start`]. Any
    /// repeated call returns `Pending` and therefore cannot authorize a second source read. A
    /// different execution or binding cannot take over an unresolved preparation.
    pub fn prepare_artifact_freeze(
        &self,
        execution_id: &str,
        binding_id: &str,
    ) -> Result<PrepareArtifactFreeze, JournalError> {
        if execution_id.trim().is_empty() || binding_id.trim().is_empty() {
            return Err(JournalError::InvalidPreparedArtifact(
                "artifact preparation identities must be nonblank".to_string(),
            ));
        }
        let mut connection = self.lock_connection()?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let execution = required_execution(&transaction, execution_id)?;
        if execution.status.is_terminal() {
            return Err(JournalError::InvalidTransition(
                execution_id.to_string(),
                "terminal execution cannot start artifact preparation".to_string(),
            ));
        }
        if let Some(existing_binding) = load_artifact_preparation(&transaction, execution_id)? {
            if existing_binding == binding_id {
                transaction.commit()?;
                return Ok(PrepareArtifactFreeze::Pending);
            }
            return Err(JournalError::ArtifactPreparationConflict(
                execution_id.to_string(),
            ));
        }
        if let Some(existing_execution) = load_artifact_preparation_owner(&transaction, binding_id)?
        {
            return Err(JournalError::ArtifactPreparationConflict(format!(
                "{binding_id} is owned by {existing_execution}"
            )));
        }
        transaction.execute(
            "INSERT INTO artifact_preparations (execution_id, binding_id) VALUES (?1, ?2)",
            params![execution_id, binding_id],
        )?;
        transaction.commit()?;
        Ok(PrepareArtifactFreeze::Start)
    }

    /// Returns the unresolved output binding whose mutable bytes this execution already read.
    pub fn artifact_preparation(&self, execution_id: &str) -> Result<Option<String>, JournalError> {
        let connection = self.lock_connection()?;
        load_artifact_preparation(&connection, execution_id)
    }

    /// Loads the immutable artifact prepared for one static output binding.
    pub fn prepared_artifact(
        &self,
        binding_id: &str,
    ) -> Result<Option<PreparedArtifactRecord>, JournalError> {
        let connection = self.lock_connection()?;
        load_prepared_artifact_by_binding(&connection, binding_id)
    }

    /// Durably marks artifact-only finalization before the first possible remote write.
    ///
    /// An exact repeated marker is idempotent. A different action for the same execution is an
    /// identity conflict and is never silently replaced.
    pub fn prepare_artifact_finalization(
        &self,
        execution_id: &str,
        kind: ArtifactFinalizationKind,
    ) -> Result<(), JournalError> {
        let mut connection = self.lock_connection()?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let execution = required_execution(&transaction, execution_id)?;
        if execution.status.is_terminal() {
            return Err(JournalError::InvalidTransition(
                execution_id.to_string(),
                "terminal execution cannot start artifact finalization".to_string(),
            ));
        }
        let existing = transaction
            .query_row(
                "SELECT kind FROM artifact_finalizations WHERE execution_id = ?1",
                [execution_id],
                |row| row.get::<_, String>(0),
            )
            .optional()?;
        if let Some(existing) = existing {
            if ArtifactFinalizationKind::parse(&existing)? == kind {
                transaction.commit()?;
                return Ok(());
            }
            return Err(JournalError::ArtifactFinalizationConflict(
                execution_id.to_string(),
            ));
        }
        transaction.execute(
            "INSERT INTO artifact_finalizations (execution_id, kind) VALUES (?1, ?2)",
            params![execution_id, kind.as_str()],
        )?;
        transaction.commit()?;
        Ok(())
    }

    /// Returns pending artifact-only finalization for an execution, when present.
    pub fn artifact_finalization(
        &self,
        execution_id: &str,
    ) -> Result<Option<ArtifactFinalizationKind>, JournalError> {
        let connection = self.lock_connection()?;
        connection
            .query_row(
                "SELECT kind FROM artifact_finalizations WHERE execution_id = ?1",
                [execution_id],
                |row| row.get::<_, String>(0),
            )
            .optional()?
            .map(|value| ArtifactFinalizationKind::parse(&value))
            .transpose()
    }

    /// Persists the exact strong localization evidence before any remote delivery attempt.
    ///
    /// Evidence must match the durable canonical invocation, local node and attempt, and staged
    /// artifact manifest. The execution must already be fenced for Verify finalization. Exact
    /// repetition is idempotent; different evidence for the same execution is an identity conflict.
    pub fn prepare_localization_evidence(
        &self,
        execution_id: &str,
        local_node_id: &NodeId,
        manifest: &MapArtifactManifest,
        evidence: &LocalizationVerificationEvidence,
    ) -> Result<(), JournalError> {
        let mut connection = self.lock_connection()?;
        let transaction = connection.transaction_with_behavior(TransactionBehavior::Immediate)?;
        let execution = required_execution(&transaction, execution_id)?;
        validate_localization_evidence_binding(&execution, local_node_id, manifest, evidence)?;
        let finalization = transaction
            .query_row(
                "SELECT kind FROM artifact_finalizations WHERE execution_id = ?1",
                [execution_id],
                |row| row.get::<_, String>(0),
            )
            .optional()?;
        if finalization.as_deref() != Some(ArtifactFinalizationKind::Verify.as_str()) {
            return Err(JournalError::InvalidTransition(
                execution_id.to_string(),
                "strong localization evidence requires pending Verify finalization".to_string(),
            ));
        }
        let encoded = serde_json::to_string(evidence)?;
        let existing = transaction
            .query_row(
                "SELECT evidence_json FROM localization_evidence WHERE execution_id = ?1",
                [execution_id],
                |row| row.get::<_, String>(0),
            )
            .optional()?;
        if let Some(existing) = existing {
            if existing == encoded {
                transaction.commit()?;
                return Ok(());
            }
            return Err(JournalError::LocalizationEvidenceConflict(
                execution_id.to_string(),
            ));
        }
        transaction.execute(
            "INSERT INTO localization_evidence (execution_id, evidence_json) VALUES (?1, ?2)",
            params![execution_id, encoded],
        )?;
        transaction.commit()?;
        Ok(())
    }

    /// Loads durable strong localization evidence for exact finalization retry.
    pub fn localization_evidence(
        &self,
        execution_id: &str,
    ) -> Result<Option<LocalizationVerificationEvidence>, JournalError> {
        let connection = self.lock_connection()?;
        connection
            .query_row(
                "SELECT evidence_json FROM localization_evidence WHERE execution_id = ?1",
                [execution_id],
                |row| row.get::<_, String>(0),
            )
            .optional()?
            .map(|encoded| serde_json::from_str(&encoded).map_err(JournalError::from))
            .transpose()
    }

    /// Clears a completed or deterministically failed artifact finalization marker.
    pub fn clear_artifact_finalization(&self, execution_id: &str) -> Result<(), JournalError> {
        let connection = self.lock_connection()?;
        connection.execute(
            "DELETE FROM artifact_finalizations WHERE execution_id = ?1",
            [execution_id],
        )?;
        Ok(())
    }

    /// Reads one execution identity and latest state.
    pub fn get(&self, execution_id: &str) -> Result<Option<JournalExecution>, JournalError> {
        let connection = self.lock_connection()?;
        load_execution(&connection, execution_id)
    }

    /// Returns every known execution in stable execution-ID order for reconnect snapshots.
    pub fn replay_records(&self) -> Result<Vec<JournalExecution>, JournalError> {
        self.query_records(
            "SELECT execution_id, invocation_content, invocation_digest, workflow_digest,
                    resource_ids, local_handle, cancellation_requested, sequence, status, reason
             FROM executions ORDER BY execution_id",
        )
    }

    /// Returns terminal business facts in stable execution-ID order for reconnect replay.
    pub fn terminal_records(&self) -> Result<Vec<JournalExecution>, JournalError> {
        self.query_records(
            "SELECT execution_id, invocation_content, invocation_digest, workflow_digest,
                    resource_ids, local_handle, cancellation_requested, sequence, status, reason
             FROM executions
             WHERE status IN ('completed', 'failed', 'cancelled')
             ORDER BY execution_id",
        )
    }

    /// Executes a stable record query and decodes all rows before releasing the connection.
    pub(super) fn query_records(&self, sql: &str) -> Result<Vec<JournalExecution>, JournalError> {
        let connection = self.lock_connection()?;
        let mut statement = connection.prepare(sql)?;
        let rows = statement.query_map([], record_from_row)?;
        rows.collect::<Result<Vec<_>, _>>()
            .map_err(JournalError::from)
    }

    /// Locks the process-local connection or reports poisoned shared state.
    pub(super) fn lock_connection(&self) -> Result<MutexGuard<'_, Connection>, JournalError> {
        self.connection.lock().map_err(|_| JournalError::Poisoned)
    }
}
