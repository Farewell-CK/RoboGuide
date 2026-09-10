//! Durable execution identity and lifecycle journal for the generic Node Service.

use domain::{LocalizationVerificationEvidence, MapArtifactManifest, NodeId};
use rusqlite::{Connection, OptionalExtension, Row, TransactionBehavior, params};
use sha2::{Digest, Sha256};
use std::path::{Path, PathBuf};
use std::sync::{Mutex, MutexGuard};
use thiserror::Error;

mod authority;
mod storage;

use storage::*;

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
/// Canonical contract that may produce localization verification evidence v0.1.
const LOCALIZATION_VERIFY_CONTRACT: &str = "spatial.localization.verify@v0";

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
    /// One execution was bound to different strong localization evidence or attempt identity.
    #[error("execution {0} has conflicting localization verification evidence")]
    LocalizationEvidenceConflict(String),
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
#[path = "../journal_tests.rs"]
mod tests;
