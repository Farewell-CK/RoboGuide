//! Durable execution identity and lifecycle journal for the generic Node Service.

use domain::MapArtifactManifest;
use rusqlite::{Connection, OptionalExtension, Row, TransactionBehavior, params};
use sha2::{Digest, Sha256};
use std::path::{Path, PathBuf};
use std::sync::{Mutex, MutexGuard};
use thiserror::Error;

/// Explanation persisted when a restart makes a physical dispatch outcome ambiguous.
const AMBIGUOUS_DISPATCH_REASON: &str =
    "local dispatch outcome is unknown after node service restart";
/// Explanation persisted when a local handle exists but the acceptance fact was interrupted.
const HANDLE_BEARING_DISPATCH_REASON: &str =
    "local dispatch handle recovered before acceptance fact; status reconciliation required";
/// Explanation persisted when a restart interrupted artifact preparation before local dispatch.
const INTERRUPTED_PRE_DISPATCH_REASON: &str =
    "node service restarted before local dispatch was authorized";
/// Explanation persisted when a restart interrupted the output freeze commit window.
const INTERRUPTED_ARTIFACT_PREPARATION_REASON: &str =
    "artifact output freeze was interrupted before its immutable record committed";

/// Immutable identity inputs bound to one execution ID for its full lifetime.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ExecutionSpec {
    /// Canonically encoded invocation used to detect even theoretical digest collisions.
    invocation_content: Vec<u8>,
    /// SHA-256 identity of the canonical invocation content.
    invocation_digest: String,
    /// Digest of the immutable configured local workflow.
    workflow_digest: String,
    /// Stable, sorted committed resource identities used by the execution.
    resource_ids: Vec<String>,
}

impl ExecutionSpec {
    /// Builds an execution identity from canonical invocation bytes and workflow inputs.
    ///
    /// Resource order is not semantically significant, so IDs are sorted and deduplicated.
    /// Empty invocation content, workflow digests, or resource IDs are rejected.
    pub fn new(
        invocation_content: impl Into<Vec<u8>>,
        workflow_digest: impl Into<String>,
        resource_ids: impl IntoIterator<Item = String>,
    ) -> Result<Self, JournalError> {
        let invocation_content = invocation_content.into();
        if invocation_content.is_empty() {
            return Err(JournalError::InvalidSpec(
                "canonical invocation content must not be empty".to_string(),
            ));
        }
        let workflow_digest = workflow_digest.into();
        if workflow_digest.is_empty() {
            return Err(JournalError::InvalidSpec(
                "workflow digest must not be empty".to_string(),
            ));
        }
        let mut resource_ids = resource_ids.into_iter().collect::<Vec<_>>();
        if resource_ids.iter().any(String::is_empty) {
            return Err(JournalError::InvalidSpec(
                "resource IDs must not be empty".to_string(),
            ));
        }
        resource_ids.sort();
        resource_ids.dedup();
        let invocation_digest = digest_bytes(&invocation_content);
        Ok(Self {
            invocation_content,
            invocation_digest,
            workflow_digest,
            resource_ids,
        })
    }

    /// Returns the canonical invocation bytes persisted for audit and collision checking.
    pub fn invocation_content(&self) -> &[u8] {
        &self.invocation_content
    }

    /// Returns the SHA-256 digest calculated from the canonical invocation bytes.
    pub fn invocation_digest(&self) -> &str {
        &self.invocation_digest
    }

    /// Returns the immutable local workflow digest.
    pub fn workflow_digest(&self) -> &str {
        &self.workflow_digest
    }

    /// Returns the sorted committed resource identities.
    pub fn resource_ids(&self) -> &[String] {
        &self.resource_ids
    }
}

/// Durable lifecycle state understood by the Node Service, independent of an EAIOS.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum JournalStatus {
    /// The durable intent exists, but the local dispatch result is not yet recorded.
    Dispatching,
    /// The local system accepted the execution.
    Accepted,
    /// The local system reports that execution is active.
    Running,
    /// The local system reports successful completion.
    Completed,
    /// The local system reports terminal failure.
    Failed,
    /// The local system reports terminal cancellation.
    Cancelled,
    /// A restart left the physical dispatch outcome ambiguous and requires reconciliation.
    ReconciliationRequired,
}

impl JournalStatus {
    /// Returns whether this state is a replayable business-terminal fact.
    pub fn is_terminal(self) -> bool {
        matches!(self, Self::Completed | Self::Failed | Self::Cancelled)
    }

    /// Encodes the status into the stable SQLite representation.
    fn as_str(self) -> &'static str {
        match self {
            Self::Dispatching => "dispatching",
            Self::Accepted => "accepted",
            Self::Running => "running",
            Self::Completed => "completed",
            Self::Failed => "failed",
            Self::Cancelled => "cancelled",
            Self::ReconciliationRequired => "reconciliation_required",
        }
    }

    /// Decodes a stable SQLite status or reports journal corruption.
    fn parse(value: &str) -> Result<Self, JournalError> {
        match value {
            "dispatching" => Ok(Self::Dispatching),
            "accepted" => Ok(Self::Accepted),
            "running" => Ok(Self::Running),
            "completed" => Ok(Self::Completed),
            "failed" => Ok(Self::Failed),
            "cancelled" => Ok(Self::Cancelled),
            "reconciliation_required" => Ok(Self::ReconciliationRequired),
            _ => Err(JournalError::Corrupt(format!(
                "unknown execution status {value}"
            ))),
        }
    }
}

/// One persisted execution identity and its latest locally known fact.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct JournalExecution {
    /// Stable business execution identity, preserved across transport sessions.
    execution_id: String,
    /// Immutable invocation, workflow, and resource tuple.
    spec: ExecutionSpec,
    /// Local EAIOS handle, when dispatch returned one durably.
    local_handle: Option<String>,
    /// Whether the local system accepted a cancellation request for this execution.
    cancellation_requested: bool,
    /// Latest accepted local event sequence.
    sequence: u64,
    /// Latest durable lifecycle status.
    status: JournalStatus,
    /// Adapter-provided detail for the latest status.
    reason: String,
}

impl JournalExecution {
    /// Returns the stable cross-session execution identity.
    pub fn execution_id(&self) -> &str {
        &self.execution_id
    }

    /// Returns the immutable invocation, workflow, and resource identity tuple.
    pub fn spec(&self) -> &ExecutionSpec {
        &self.spec
    }

    /// Returns the local execution handle when its dispatch result was persisted.
    pub fn local_handle(&self) -> Option<&str> {
        self.local_handle.as_deref()
    }

    /// Returns whether cancellation was requested without implying terminal cancellation.
    pub fn cancellation_requested(&self) -> bool {
        self.cancellation_requested
    }

    /// Returns the latest accepted local sequence.
    pub fn sequence(&self) -> u64 {
        self.sequence
    }

    /// Returns the latest durable lifecycle state.
    pub fn status(&self) -> JournalStatus {
        self.status
    }

    /// Returns the latest durable lifecycle detail.
    pub fn reason(&self) -> &str {
        &self.reason
    }
}

/// Result of atomically preparing an execution before any local physical dispatch.
#[derive(Clone, Debug, Eq, PartialEq)]
pub enum PrepareDispatch {
    /// The durable `Dispatching` record was committed; local execution may start exactly once.
    Start(JournalExecution),
    /// The same immutable identity already exists and must be replayed or reconciled.
    Existing(JournalExecution),
    /// The execution ID is already bound to a different invocation, workflow, or resource set.
    Conflict(JournalExecution),
}

/// Result of requesting the one permitted read of a mutable artifact output.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum PrepareArtifactFreeze {
    /// The durable preparation marker was newly committed, so the source may be read once.
    Start,
    /// The same marker already exists and the mutable source must not be read again.
    Pending,
}

/// One immutable artifact frozen after its producing local execution completed.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct PreparedArtifactRecord {
    /// Static deployment binding that selected the output metadata and source path.
    binding_id: String,
    /// Execution that built the bytes, distinct from any later publication execution.
    producer_execution_id: String,
    /// Immutable content-addressed local copy retained across process restarts.
    frozen_path: PathBuf,
    /// Typed manifest carrying the build execution and Task provenance.
    manifest: MapArtifactManifest,
}

impl PreparedArtifactRecord {
    /// Validates one prepared artifact before it may become durable publication input.
    ///
    /// The producer execution must match the manifest and a source Task is mandatory. The frozen
    /// path must be an explicit non-empty path; content verification remains the stager's job.
    pub fn new(
        binding_id: impl Into<String>,
        producer_execution_id: impl Into<String>,
        frozen_path: impl Into<PathBuf>,
        manifest: MapArtifactManifest,
    ) -> Result<Self, JournalError> {
        let binding_id = binding_id.into();
        let producer_execution_id = producer_execution_id.into();
        let frozen_path = frozen_path.into();
        if binding_id.trim().is_empty() || producer_execution_id.trim().is_empty() {
            return Err(JournalError::InvalidPreparedArtifact(
                "binding and producer execution identities must be nonblank".to_string(),
            ));
        }
        if frozen_path.as_os_str().is_empty() {
            return Err(JournalError::InvalidPreparedArtifact(
                "frozen artifact path must not be empty".to_string(),
            ));
        }
        if manifest.source_execution_id() != Some(producer_execution_id.as_str()) {
            return Err(JournalError::InvalidPreparedArtifact(
                "manifest source execution differs from producer execution".to_string(),
            ));
        }
        if manifest.source_task_ref().is_none() {
            return Err(JournalError::InvalidPreparedArtifact(
                "prepared artifact manifest requires source Task provenance".to_string(),
            ));
        }
        Ok(Self {
            binding_id,
            producer_execution_id,
            frozen_path,
            manifest,
        })
    }

    /// Returns the static output binding identity.
    pub fn binding_id(&self) -> &str {
        &self.binding_id
    }

    /// Returns the execution that built the immutable bytes.
    pub fn producer_execution_id(&self) -> &str {
        &self.producer_execution_id
    }

    /// Returns the immutable local copy used by later publication attempts.
    pub fn frozen_path(&self) -> &Path {
        &self.frozen_path
    }

    /// Returns the typed manifest containing build provenance and immutable metadata.
    pub const fn manifest(&self) -> &MapArtifactManifest {
        &self.manifest
    }
}

/// Artifact-only work that may be retried after local execution is already complete.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ArtifactFinalizationKind {
    /// Upload frozen output bytes and publish their already-built manifest.
    Publish,
    /// Record completion of a local import workflow.
    Import,
    /// Record completion of local anchor verification.
    Verify,
}

impl ArtifactFinalizationKind {
    /// Encodes the durable SQLite spelling.
    fn as_str(self) -> &'static str {
        match self {
            Self::Publish => "publish",
            Self::Import => "import",
            Self::Verify => "verify",
        }
    }

    /// Decodes a durable SQLite value or reports journal corruption.
    fn parse(value: &str) -> Result<Self, JournalError> {
        match value {
            "publish" => Ok(Self::Publish),
            "import" => Ok(Self::Import),
            "verify" => Ok(Self::Verify),
            _ => Err(JournalError::Corrupt(format!(
                "unknown artifact finalization kind {value}"
            ))),
        }
    }
}

/// SQLite WAL journal retaining execution identity beyond process and network sessions.
pub struct ExecutionJournal {
    /// Single process-local connection serialized for atomic read-modify-write operations.
    connection: Mutex<Connection>,
}

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
        let record = load_execution(&transaction, execution_id)?.ok_or_else(|| {
            JournalError::Corrupt("new dispatch record could not be read".to_string())
        })?;
        transaction.commit()?;
        Ok(PrepareDispatch::Start(record))
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
    fn query_records(&self, sql: &str) -> Result<Vec<JournalExecution>, JournalError> {
        let connection = self.lock_connection()?;
        let mut statement = connection.prepare(sql)?;
        let rows = statement.query_map([], record_from_row)?;
        rows.collect::<Result<Vec<_>, _>>()
            .map_err(JournalError::from)
    }

    /// Locks the process-local connection or reports poisoned shared state.
    fn lock_connection(&self) -> Result<MutexGuard<'_, Connection>, JournalError> {
        self.connection.lock().map_err(|_| JournalError::Poisoned)
    }
}

/// Configures durability and concurrency properties required by the journal.
fn configure_connection(connection: &Connection) -> Result<(), JournalError> {
    connection.pragma_update(None, "journal_mode", "WAL")?;
    connection.pragma_update(None, "synchronous", "FULL")?;
    connection.pragma_update(None, "foreign_keys", "ON")?;
    connection.busy_timeout(std::time::Duration::from_secs(5))?;
    Ok(())
}

/// Creates the current journal schema without modifying existing execution records.
fn create_schema(connection: &mut Connection) -> Result<(), JournalError> {
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
         CREATE TABLE IF NOT EXISTS local_dispatch_authorizations (
             execution_id TEXT PRIMARY KEY NOT NULL,
             FOREIGN KEY(execution_id) REFERENCES executions(execution_id)
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
    transaction.pragma_update(None, "user_version", 3_i64)?;
    transaction.commit()?;
    Ok(())
}

/// Fences dispatches whose local side effect or acceptance fact cannot be proven after restart.
fn recover_ambiguous_dispatches(connection: &Connection) -> Result<(), JournalError> {
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
fn load_execution(
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
fn required_execution(
    connection: &Connection,
    execution_id: &str,
) -> Result<JournalExecution, JournalError> {
    load_execution(connection, execution_id)?
        .ok_or_else(|| JournalError::UnknownExecution(execution_id.to_string()))
}

/// Loads one prepared artifact by static binding identity.
fn load_prepared_artifact_by_binding(
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
fn load_prepared_artifact_by_selector(
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
fn load_artifact_preparation(
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
fn load_artifact_preparation_owner(
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
fn validate_artifact_preparation_owner(
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
fn clear_matching_artifact_preparation(
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
fn prepared_artifact_from_row(row: &Row<'_>) -> rusqlite::Result<PreparedArtifactRecord> {
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
fn record_from_row(row: &Row<'_>) -> rusqlite::Result<JournalExecution> {
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
fn digest_bytes(content: &[u8]) -> String {
    let digest = Sha256::digest(content);
    format!("sha256:{digest:x}")
}

/// Execution journal storage or invariant failure.
#[derive(Debug, Error)]
pub enum JournalError {
    /// SQLite could not complete a durable operation.
    #[error("execution journal SQLite failure: {0}")]
    Sqlite(#[from] rusqlite::Error),
    /// Structured resource identities could not be encoded.
    #[error("execution journal encoding failure: {0}")]
    Encoding(#[from] serde_json::Error),
    /// The in-process connection mutex was poisoned.
    #[error("execution journal connection is unavailable")]
    Poisoned,
    /// A supplied identity tuple was structurally invalid.
    #[error("invalid execution journal spec: {0}")]
    InvalidSpec(String),
    /// A prepared artifact lacks exact provenance, identity, or a usable frozen path.
    #[error("invalid prepared artifact: {0}")]
    InvalidPreparedArtifact(String),
    /// An immutable output binding or selector was reused for different prepared content.
    #[error("prepared artifact identity {0} is already bound to different content")]
    PreparedArtifactConflict(String),
    /// An unresolved mutable-output read was reused by another execution or binding.
    #[error("artifact preparation identity {0} is already pending")]
    ArtifactPreparationConflict(String),
    /// One execution was assigned two different artifact-only finalization actions.
    #[error("execution {0} has a conflicting artifact finalization action")]
    ArtifactFinalizationConflict(String),
    /// The execution ID was empty.
    #[error("execution ID must not be empty")]
    InvalidExecutionId,
    /// A local handle was empty.
    #[error("local execution handle must not be empty")]
    InvalidLocalHandle,
    /// The execution identity does not exist.
    #[error("execution {0} is not present in the journal")]
    UnknownExecution(String),
    /// A second, different local handle attempted to replace the first.
    #[error("execution {0} is already bound to a different local handle")]
    LocalHandleConflict(String),
    /// A handle arrived after the journal fenced an ambiguous dispatch.
    #[error("execution {0} has an ambiguous dispatch and requires reconciliation")]
    AmbiguousDispatch(String),
    /// A lifecycle transition violated terminal or dispatch invariants.
    #[error("invalid transition for execution {0}: {1}")]
    InvalidTransition(String, String),
    /// A fact reused or regressed a durable sequence number.
    #[error("stale sequence for execution {execution_id}: current {current}, received {received}")]
    StaleSequence {
        /// Execution whose sequence invariant was violated.
        execution_id: String,
        /// Latest durable sequence.
        current: u64,
        /// Reused or stale received sequence.
        received: u64,
    },
    /// A sequence cannot be represented by SQLite's signed integer storage.
    #[error("execution sequence exceeds SQLite integer range")]
    SequenceOverflow,
    /// Persisted data did not satisfy the journal schema's semantic invariants.
    #[error("execution journal is corrupt: {0}")]
    Corrupt(String),
}

#[cfg(test)]
mod tests {
    use super::*;
    use domain::{
        ContentDigest, MapArtifactRef, MapId, MapRevisionId, MapRevisionSelector, MissionId,
        NodeId, SpatialAnchorId, TaskId, TaskRef, TimestampMs,
    };
    use tempfile::TempDir;

    /// Creates a deterministic canonical identity fixture.
    fn spec(invocation: &str, workflow: &str, resources: &[&str]) -> ExecutionSpec {
        ExecutionSpec::new(
            invocation.as_bytes().to_vec(),
            workflow,
            resources.iter().map(|value| (*value).to_string()),
        )
        .expect("fixture spec is valid")
    }

    /// Creates a temporary on-disk journal and returns its owning directory.
    fn journal() -> (TempDir, std::path::PathBuf, ExecutionJournal) {
        let directory = tempfile::tempdir().expect("temporary directory exists");
        let path = directory.path().join("executions.sqlite3");
        let journal = ExecutionJournal::open(&path).expect("journal opens");
        (directory, path, journal)
    }

    /// Builds one typed prepared manifest with mandatory build execution and Task provenance.
    fn prepared_manifest(execution_id: &str) -> MapArtifactManifest {
        let mission = MissionId::new("mission-a").expect("mission id");
        let artifact = MapArtifactRef::new(
            MapRevisionSelector::new(
                MapId::new("lab").expect("map id"),
                MapRevisionId::new("r1").expect("revision id"),
            ),
            ContentDigest::new(format!("sha256:{}", "a".repeat(64))).expect("digest"),
            9,
        );
        MapArtifactManifest::new_with_format(
            artifact,
            "application/octet-stream",
            "grid",
            "v1",
            NodeId::new("dog-a").expect("node id"),
            None,
            mission.clone(),
            Some(execution_id.to_string()),
            Some(TaskRef::new(
                mission,
                TaskId::new("build-map").expect("task id"),
            )),
            "map",
            "enu",
            SpatialAnchorId::new("anchor-lab").expect("anchor id"),
            Some(0.05),
            TimestampMs::new(7),
            None,
        )
        .expect("manifest")
    }

    /// New identities are durably prepared before a caller may dispatch locally.
    #[test]
    fn prepare_dispatch_uses_wal_and_persists_dispatching() {
        let (_directory, _path, journal) = journal();
        let identity = spec("{\"task\":\"reach\"}", "workflow-a", &["motor"]);
        let PrepareDispatch::Start(record) = journal
            .prepare_dispatch("execution-a", &identity)
            .expect("dispatch prepares")
        else {
            panic!("new execution must receive dispatch permission");
        };
        assert_eq!(record.status(), JournalStatus::Dispatching);
        assert_eq!(record.sequence(), 0);
        assert_eq!(
            record.spec().invocation_digest(),
            identity.invocation_digest()
        );
        let connection = journal.lock_connection().expect("connection locks");
        let mode = connection
            .pragma_query_value(None, "journal_mode", |row| row.get::<_, String>(0))
            .expect("journal mode reads");
        assert_eq!(mode.to_ascii_lowercase(), "wal");
    }

    /// One execution ID is idempotent only for the complete immutable identity tuple.
    #[test]
    fn identity_tuple_is_idempotent_and_conflict_checked() {
        let (_directory, _path, journal) = journal();
        let original = spec(
            "{\"capability\":\"reach\"}",
            "workflow-a",
            &["camera", "motor"],
        );
        assert!(matches!(
            journal.prepare_dispatch("execution-a", &original),
            Ok(PrepareDispatch::Start(_))
        ));
        let reordered = spec(
            "{\"capability\":\"reach\"}",
            "workflow-a",
            &["motor", "camera", "motor"],
        );
        assert!(matches!(
            journal.prepare_dispatch("execution-a", &reordered),
            Ok(PrepareDispatch::Existing(_))
        ));
        for conflict in [
            spec(
                "{\"capability\":\"dock\"}",
                "workflow-a",
                &["camera", "motor"],
            ),
            spec(
                "{\"capability\":\"reach\"}",
                "workflow-b",
                &["camera", "motor"],
            ),
            spec("{\"capability\":\"reach\"}", "workflow-a", &["camera"]),
        ] {
            assert!(matches!(
                journal.prepare_dispatch("execution-a", &conflict),
                Ok(PrepareDispatch::Conflict(_))
            ));
        }
    }

    /// Terminal status, sequence, handle, and identity survive restart for replay.
    #[test]
    fn terminal_execution_replays_after_restart() {
        let (directory, path, journal) = journal();
        let identity = spec("{\"task\":\"reach\"}", "workflow-a", &["motor"]);
        journal
            .prepare_dispatch("execution-a", &identity)
            .expect("dispatch prepares");
        journal
            .record_local_handle("execution-a", "local-run-7")
            .expect("handle persists");
        journal
            .record_status("execution-a", 1, JournalStatus::Accepted, "")
            .expect("acceptance persists");
        journal
            .record_status("execution-a", 2, JournalStatus::Running, "moving")
            .expect("running persists");
        journal
            .record_status("execution-a", 3, JournalStatus::Completed, "arrived")
            .expect("completion persists");
        drop(journal);

        let reopened = ExecutionJournal::open(&path).expect("journal reopens");
        let records = reopened.terminal_records().expect("terminal facts replay");
        assert_eq!(records.len(), 1);
        assert_eq!(records[0].execution_id(), "execution-a");
        assert_eq!(records[0].local_handle(), Some("local-run-7"));
        assert!(!records[0].cancellation_requested());
        assert_eq!(records[0].sequence(), 3);
        assert_eq!(records[0].status(), JournalStatus::Completed);
        assert_eq!(records[0].reason(), "arrived");
        assert_eq!(records[0].spec(), &identity);
        drop(reopened);
        drop(directory);
    }

    /// A durable cancel request remains distinct from a later terminal Cancelled fact.
    #[test]
    fn cancellation_request_persists_without_synthesizing_cancelled() {
        let (_directory, path, journal) = journal();
        let identity = spec("{\"task\":\"reach\"}", "workflow-a", &["motor"]);
        journal
            .prepare_dispatch("execution-a", &identity)
            .expect("dispatch prepares");
        journal
            .record_local_handle("execution-a", "local-run-7")
            .expect("handle persists");
        journal
            .record_status("execution-a", 1, JournalStatus::Running, "moving")
            .expect("running persists");
        let requested = journal
            .record_cancellation_requested("execution-a")
            .expect("cancel request persists");
        assert!(requested.cancellation_requested());
        assert_eq!(requested.status(), JournalStatus::Running);
        drop(journal);

        let reopened = ExecutionJournal::open(&path).expect("journal reopens");
        let replay = reopened
            .get("execution-a")
            .expect("record reads")
            .expect("record exists");
        assert!(replay.cancellation_requested());
        assert_eq!(replay.status(), JournalStatus::Running);
        let cancelled = reopened
            .record_status(
                "execution-a",
                2,
                JournalStatus::Cancelled,
                "local terminal fact",
            )
            .expect("terminal cancellation persists");
        assert!(cancelled.cancellation_requested());
        assert_eq!(cancelled.status(), JournalStatus::Cancelled);
    }

    /// A crash before handle persistence becomes unknown and never grants redispatch.
    #[test]
    fn ambiguous_dispatch_requires_reconciliation_and_never_restarts() {
        let (_directory, path, journal) = journal();
        let identity = spec("{\"task\":\"reach\"}", "workflow-a", &[]);
        assert!(matches!(
            journal.prepare_dispatch("execution-a", &identity),
            Ok(PrepareDispatch::Start(_))
        ));
        journal
            .authorize_local_dispatch("execution-a")
            .expect("local dispatch is durably authorized");
        drop(journal);

        let reopened = ExecutionJournal::open(&path).expect("journal reopens");
        let record = reopened
            .get("execution-a")
            .expect("record reads")
            .expect("record exists");
        assert_eq!(record.status(), JournalStatus::ReconciliationRequired);
        assert_eq!(record.reason(), AMBIGUOUS_DISPATCH_REASON);
        assert!(matches!(
            reopened.prepare_dispatch("execution-a", &identity),
            Ok(PrepareDispatch::Existing(JournalExecution {
                status: JournalStatus::ReconciliationRequired,
                ..
            }))
        ));
        assert!(matches!(
            reopened.record_local_handle("execution-a", "late-handle"),
            Err(JournalError::AmbiguousDispatch(_))
        ));
    }

    /// A crash after local handle persistence fences the handle for status-only reconciliation.
    #[test]
    fn handle_bearing_dispatch_requires_status_reconciliation_after_restart() {
        let (_directory, path, journal) = journal();
        let identity = spec("{\"task\":\"reach\"}", "workflow-a", &[]);
        journal
            .prepare_dispatch("execution-a", &identity)
            .expect("dispatch prepares");
        journal
            .authorize_local_dispatch("execution-a")
            .expect("local dispatch is authorized");
        journal
            .record_local_handle("execution-a", "local-run-7")
            .expect("local handle persists");
        drop(journal);

        let reopened = ExecutionJournal::open(&path).expect("journal reopens");
        let record = reopened
            .get("execution-a")
            .expect("record reads")
            .expect("record exists");
        assert_eq!(record.status(), JournalStatus::ReconciliationRequired);
        assert_eq!(record.reason(), HANDLE_BEARING_DISPATCH_REASON);
        assert_eq!(record.local_handle(), Some("local-run-7"));
    }

    /// A restart before local dispatch authorization is a conclusive failed preparation.
    #[test]
    fn interrupted_pre_dispatch_is_failed_without_claiming_physical_ambiguity() {
        let (_directory, path, journal) = journal();
        let identity = spec("{\"task\":\"reach\"}", "workflow-a", &[]);
        journal
            .prepare_dispatch("execution-a", &identity)
            .expect("dispatch prepares");
        drop(journal);

        let reopened = ExecutionJournal::open(&path).expect("journal reopens");
        let record = reopened
            .get("execution-a")
            .expect("record reads")
            .expect("record exists");
        assert_eq!(record.status(), JournalStatus::Failed);
        assert_eq!(record.reason(), INTERRUPTED_PRE_DISPATCH_REASON);
    }

    /// Prepared bytes and pending remote finalization survive a complete process restart.
    #[test]
    fn prepared_artifact_and_finalization_are_durable_and_conflict_checked() {
        let (_directory, path, journal) = journal();
        let build_spec = spec("{\"task\":\"build\"}", "workflow-a", &[]);
        let publish_spec = spec("{\"task\":\"publish\"}", "workflow-b", &[]);
        journal
            .prepare_dispatch("build-execution", &build_spec)
            .expect("build execution prepares");
        journal
            .prepare_dispatch("publish-execution", &publish_spec)
            .expect("publish execution prepares");
        journal
            .record_local_handle("publish-execution", "local-publish")
            .expect("publish handle persists");
        journal
            .record_status(
                "publish-execution",
                1,
                JournalStatus::Running,
                "local publication workflow completed",
            )
            .expect("publish execution is active");
        let prepared = PreparedArtifactRecord::new(
            "lab-r1-output",
            "build-execution",
            "/var/lib/roboguide/prepared/aa/content",
            prepared_manifest("build-execution"),
        )
        .expect("prepared record validates");
        assert!(matches!(
            journal.record_prepared_artifact(&prepared),
            Err(JournalError::ArtifactPreparationConflict(_))
        ));
        assert_eq!(
            journal
                .prepare_artifact_freeze("build-execution", "lab-r1-output")
                .expect("one mutable-source read is granted"),
            PrepareArtifactFreeze::Start
        );
        assert_eq!(
            journal
                .record_prepared_artifact(&prepared)
                .expect("prepared artifact persists"),
            prepared
        );
        assert_eq!(
            journal
                .artifact_preparation("build-execution")
                .expect("preparation fence reads"),
            None,
            "prepared record and fence removal commit atomically"
        );
        journal
            .record_prepared_artifact(&prepared)
            .expect("exact prepared artifact is idempotent");
        journal
            .prepare_artifact_finalization("publish-execution", ArtifactFinalizationKind::Publish)
            .expect("finalization marker persists");
        drop(journal);

        let reopened = ExecutionJournal::open(&path).expect("journal reopens");
        assert_eq!(
            reopened
                .prepared_artifact("lab-r1-output")
                .expect("prepared artifact reads"),
            Some(prepared.clone())
        );
        assert_eq!(
            reopened
                .artifact_finalization("publish-execution")
                .expect("finalization reads"),
            Some(ArtifactFinalizationKind::Publish)
        );
        let conflicting = PreparedArtifactRecord::new(
            "lab-r1-output",
            "build-execution",
            "/different/path",
            prepared_manifest("build-execution"),
        )
        .expect("conflict fixture validates");
        assert!(matches!(
            reopened.record_prepared_artifact(&conflicting),
            Err(JournalError::PreparedArtifactConflict(_))
        ));
        assert!(matches!(
            reopened.prepare_artifact_finalization(
                "publish-execution",
                ArtifactFinalizationKind::Verify
            ),
            Err(JournalError::ArtifactFinalizationConflict(_))
        ));
    }

    /// A crash after freezing bytes never grants the same execution another mutable-source read.
    #[test]
    fn interrupted_artifact_freeze_is_durably_fenced_across_restart() {
        let (directory, path, journal) = journal();
        let identity = spec("{\"task\":\"build\"}", "workflow-a", &[]);
        journal
            .prepare_dispatch("build-execution", &identity)
            .expect("build execution prepares");
        journal
            .authorize_local_dispatch("build-execution")
            .expect("local dispatch is authorized");
        journal
            .record_local_handle("build-execution", "local-build")
            .expect("local handle persists");
        journal
            .record_status(
                "build-execution",
                1,
                JournalStatus::Running,
                "local map builder completed",
            )
            .expect("running state persists");
        assert_eq!(
            journal
                .prepare_artifact_freeze("build-execution", "lab-r1-output")
                .expect("first source read is durably granted"),
            PrepareArtifactFreeze::Start
        );
        let frozen = directory.path().join("prepared-snapshot");
        std::fs::write(&frozen, b"first-map").expect("simulated frozen snapshot writes");
        drop(journal);

        let reopened = ExecutionJournal::open(&path).expect("journal reopens after crash");
        let execution = reopened
            .get("build-execution")
            .expect("execution reads")
            .expect("execution exists");
        assert_eq!(execution.status(), JournalStatus::ReconciliationRequired);
        assert_eq!(execution.reason(), INTERRUPTED_ARTIFACT_PREPARATION_REASON);
        assert_eq!(
            reopened
                .artifact_preparation("build-execution")
                .expect("preparation marker reads"),
            Some("lab-r1-output".to_string())
        );
        assert_eq!(
            reopened
                .prepare_artifact_freeze("build-execution", "lab-r1-output")
                .expect("exact retry checks prior grant"),
            PrepareArtifactFreeze::Pending,
            "an exact retry must not authorize rereading a potentially changed source"
        );
        assert_eq!(
            std::fs::read(frozen).expect("first immutable snapshot remains available"),
            b"first-map"
        );
    }

    /// Migrating a v1 dispatch remains conservative because its call boundary was not recorded.
    #[test]
    fn v1_dispatch_migration_preserves_possible_local_side_effect() {
        let directory = tempfile::tempdir().expect("temporary directory exists");
        let path = directory.path().join("legacy.sqlite3");
        let connection = Connection::open(&path).expect("legacy database opens");
        connection
            .execute_batch(
                "CREATE TABLE executions (
                    execution_id TEXT PRIMARY KEY NOT NULL,
                    invocation_content BLOB NOT NULL,
                    invocation_digest TEXT NOT NULL,
                    workflow_digest TEXT NOT NULL,
                    resource_ids TEXT NOT NULL,
                    local_handle TEXT,
                    cancellation_requested INTEGER NOT NULL,
                    sequence INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    reason TEXT NOT NULL
                 );
                 PRAGMA user_version = 1;",
            )
            .expect("legacy schema creates");
        let invocation = b"{\"task\":\"legacy\"}";
        connection
            .execute(
                "INSERT INTO executions VALUES (?1, ?2, ?3, ?4, ?5, NULL, 0, 0, 'dispatching', '')",
                params![
                    "legacy-execution",
                    invocation.as_slice(),
                    digest_bytes(invocation),
                    "legacy-workflow",
                    "[]"
                ],
            )
            .expect("legacy dispatch inserts");
        drop(connection);

        let migrated = ExecutionJournal::open(&path).expect("legacy journal migrates");
        let record = migrated
            .get("legacy-execution")
            .expect("record reads")
            .expect("record exists");
        assert_eq!(record.status(), JournalStatus::ReconciliationRequired);
        assert_eq!(record.reason(), AMBIGUOUS_DISPATCH_REASON);
    }

    /// Sequence and terminal guards preserve a single ordered fact history.
    #[test]
    fn status_updates_reject_stale_and_post_terminal_facts() {
        let (_directory, _path, journal) = journal();
        let identity = spec("{\"task\":\"reach\"}", "workflow-a", &[]);
        journal
            .prepare_dispatch("execution-a", &identity)
            .expect("dispatch prepares");
        journal
            .record_status("execution-a", 1, JournalStatus::Running, "moving")
            .expect("running persists");
        journal
            .record_status("execution-a", 1, JournalStatus::Running, "moving")
            .expect("exact duplicate is idempotent");
        assert!(matches!(
            journal.record_status("execution-a", 1, JournalStatus::Failed, "late"),
            Err(JournalError::StaleSequence { .. })
        ));
        journal
            .record_status("execution-a", 2, JournalStatus::Completed, "done")
            .expect("completion persists");
        assert!(matches!(
            journal.record_status("execution-a", 3, JournalStatus::Running, "impossible"),
            Err(JournalError::InvalidTransition(_, _))
        ));
    }
}
