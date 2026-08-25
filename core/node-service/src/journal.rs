//! Durable execution identity and lifecycle journal for the generic Node Service.

use rusqlite::{Connection, OptionalExtension, Row, TransactionBehavior, params};
use sha2::{Digest, Sha256};
use std::path::Path;
use std::sync::{Mutex, MutexGuard};
use thiserror::Error;

/// Explanation persisted when a restart makes a physical dispatch outcome ambiguous.
const AMBIGUOUS_DISPATCH_REASON: &str =
    "local dispatch outcome is unknown after node service restart";

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

/// SQLite WAL journal retaining execution identity beyond process and network sessions.
pub struct ExecutionJournal {
    /// Single process-local connection serialized for atomic read-modify-write operations.
    connection: Mutex<Connection>,
}

impl ExecutionJournal {
    /// Opens or creates a journal and fences ambiguous pre-handle dispatches from replay.
    ///
    /// Opening the same database represents a new Node Service process lifetime. Any
    /// `Dispatching` row without a local handle is durably changed to
    /// `ReconciliationRequired`, because the previous physical call may have succeeded.
    pub fn open(path: impl AsRef<Path>) -> Result<Self, JournalError> {
        let connection = Connection::open(path)?;
        configure_connection(&connection)?;
        create_schema(&connection)?;
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

/// Creates the v1 journal schema without modifying existing records.
fn create_schema(connection: &Connection) -> Result<(), JournalError> {
    connection.execute_batch(
        "BEGIN IMMEDIATE;
         CREATE TABLE IF NOT EXISTS executions (
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
         PRAGMA user_version = 1;
         COMMIT;",
    )?;
    Ok(())
}

/// Fences dispatches whose local side effect cannot be proven after process restart.
fn recover_ambiguous_dispatches(connection: &Connection) -> Result<(), JournalError> {
    connection.execute(
        "UPDATE executions
         SET status = 'reconciliation_required', reason = ?1
         WHERE status = 'dispatching' AND local_handle IS NULL",
        [AMBIGUOUS_DISPATCH_REASON],
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
