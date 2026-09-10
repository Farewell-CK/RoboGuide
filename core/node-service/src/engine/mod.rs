//! Generic declarative workflow execution with durable identity and local locking.

use crate::local_engine::driver::{DriverKind, LocalDriver};
use crate::{
    ArtifactError, ArtifactFinalizationKind, ArtifactOperationConfig, ArtifactProvenance,
    ArtifactStager, CapabilityReadinessFact, CompiledCapability, CompiledLocalCatalog,
    ExecutionJournal, ExecutionSpec, FilesystemMemoryLedger, JournalError, JournalExecution,
    JournalStatus, LocalHealthState, LocalMemoryLedger, MappedExecutionFact, MappedExecutionPhase,
    MemoryQuery, PeerChannelReadinessFact, PrepareArtifactFreeze, PrepareDispatch,
    PreparedArtifact, PreparedArtifactRecord, ReplicaEvidenceStatus, StateExportFact,
    WorkflowContext,
};
use domain::{
    LocalSystemId, MapArtifactManifest, MemoryArtifactManifest, MissionId, NodeId, TaskId, TaskRef,
    TimestampMs,
};
use integration::grpc::v0_4::{CanonicalInvocation, ExecutionPhase, ExecutionSnapshot};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::fmt::{Display, Formatter};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::{SystemTime, UNIX_EPOCH};
use tokio::sync::broadcast;

mod admission;
mod execution;
mod memory_operations;
mod observation;
mod validation;

pub(crate) use validation::workflow_digest;
use validation::*;

/// One progressive local execution fact independent of a remote transport session.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LocalExecutionEvent {
    /// Stable execution identity.
    pub execution_id: String,
    /// Monotonic execution-local sequence.
    pub sequence: u64,
    /// Canonical lifecycle phase.
    pub phase: ExecutionPhase,
    /// Local diagnostic detail.
    pub reason: String,
}

/// Local EAIOS acknowledgement that one direct peer channel endpoint is ready or unavailable.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LocalPeerChannelReadiness {
    /// Mission-level Group containing the coordination Context.
    pub group_id: String,
    /// Coordination Context declaring the channel.
    pub context_id: String,
    /// Logical ContextRole represented by this Local EAIOS.
    pub context_role_id: String,
    /// Registered node-local EAIOS that established this endpoint.
    pub local_system_id: String,
    /// Shared channel instance identity established by the Local EAIOS peers.
    pub channel_instance_id: String,
    /// Descriptor profile confirmed by the local channel implementation.
    pub profile_id: String,
    /// Descriptor message schema confirmed by the local channel implementation.
    pub message_schema: String,
    /// Whether the local endpoint currently confirms readiness.
    pub ready: bool,
    /// Receive-relative readiness lifetime requested by the Local EAIOS.
    pub valid_for_ms: u64,
}

impl From<PeerChannelReadinessFact> for LocalPeerChannelReadiness {
    /// Preserves the fixed observer owner and endpoint identity for protocol publication.
    fn from(fact: PeerChannelReadinessFact) -> Self {
        Self {
            group_id: fact.group_id,
            context_id: fact.context_id,
            context_role_id: fact.context_role_id,
            local_system_id: fact.local_system_id,
            channel_instance_id: fact.channel_instance_id,
            profile_id: fact.profile_id,
            message_schema: fact.message_schema,
            ready: fact.ready,
            valid_for_ms: fact.valid_for_ms,
        }
    }
}

/// One complete node observation used for registration readiness and heartbeat health.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NodeObservation {
    /// Process/local-system health, kept independent from exact capability readiness.
    status: integration::grpc::v0_4::NodeStatus,
    /// Exact canonical contract readiness in deterministic contract order.
    capabilities: BTreeMap<String, CapabilityReadinessFact>,
}

impl NodeObservation {
    /// Returns the current process/local-system health observation.
    pub const fn status(&self) -> &integration::grpc::v0_4::NodeStatus {
        &self.status
    }

    /// Returns readiness facts keyed by exact canonical contract.
    pub const fn capabilities(&self) -> &BTreeMap<String, CapabilityReadinessFact> {
        &self.capabilities
    }
}

/// Result of accepting a remote Execute command.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ExecuteDisposition {
    /// Local dispatch was durably authorized exactly once.
    Started,
    /// The same identity already exists and its current snapshot must be replayed.
    Existing(ExecutionSnapshot),
}

/// Cloneable generic Local Integration Engine.
#[derive(Clone)]
pub struct LocalIntegrationEngine {
    /// Shared immutable configuration and process-owned runtime state.
    inner: Arc<EngineInner>,
}

/// Process-owned engine state shared by workflow tasks.
struct EngineInner {
    /// Immutable startup-compiled local catalog.
    catalog: Arc<CompiledLocalCatalog>,
    /// Durable execution identity and lifecycle authority.
    journal: Arc<ExecutionJournal>,
    /// Generic transport drivers keyed by family.
    drivers: BTreeMap<DriverKind, Arc<dyn LocalDriver>>,
    /// Execution-scoped resource and local-lock ownership.
    locks: Mutex<BTreeMap<String, String>>,
    /// Cancellation workflows currently in flight, preventing duplicate local requests.
    cancellations_in_flight: Mutex<BTreeSet<String>>,
    /// Explicit artifact-finalization resumes currently in flight.
    artifact_finalizations_in_flight: Mutex<BTreeSet<String>>,
    /// Process-level fact bus surviving Node Protocol sessions.
    events: broadcast::Sender<LocalExecutionEvent>,
    /// Process-level readiness facts emitted by Local EAIOS channel adapters.
    peer_readiness: broadcast::Sender<LocalPeerChannelReadiness>,
    /// Optional Spatial Memory artifact stager configured independently of Node Protocol.
    artifact_stager: Option<ArtifactStager>,
    /// Node-side ledgers keyed by configured Local Memory Provider identity.
    memory_ledgers: BTreeMap<String, Arc<dyn LocalMemoryLedger>>,
}

/// Configuration-owned artifact operation reused by validated execution directives.
type ArtifactOperation = ArtifactOperationConfig;

/// Validated artifact directive carried opaquely through Node Protocol parameters.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct ArtifactDirective<'a> {
    /// Deployment-owned static binding identity.
    slot: &'a str,
    /// Exact node-side action to perform for the selected binding.
    operation: ArtifactOperation,
    /// Mission-selected logical map identity.
    map_id: &'a str,
    /// Mission-selected immutable revision identity.
    revision_id: &'a str,
    /// Mission-selected fixed spatial anchor.
    spatial_anchor_id: &'a str,
}

/// Generic local execution failure.
#[derive(Debug)]
pub enum EngineError {
    /// Startup configuration or driver installation is incomplete.
    Configuration(String),
    /// A canonical capability has no configured owner/workflow.
    UnsupportedCapability(String),
    /// Control did not commit every configured required resource.
    MissingCommittedResource,
    /// The same execution identity was reused for another semantic tuple.
    ExecutionConflict(String),
    /// A local resource or lock is owned by another active execution.
    LocalLockConflict {
        /// Conflicting lock identity.
        key: String,
        /// Execution currently owning it.
        owner: String,
    },
    /// Execution requires explicit reconciliation before any further action.
    ReconciliationRequired(String),
    /// Execution identity is unknown locally.
    UnknownExecution(String),
    /// Local lock state was poisoned.
    LockState,
    /// Local state directory could not be created.
    Io(std::io::Error),
    /// Durable journal rejected an operation.
    Journal(JournalError),
    /// Compiled catalog failed to render a request.
    Catalog(crate::CatalogError),
    /// Mapping evaluation failed.
    Mapping(crate::MappingError),
    /// Local driver failed without implied retry safety.
    Driver(crate::DriverError),
    /// Spatial Memory artifact staging configuration or transfer failed.
    Artifact(ArtifactError),
    /// Local EAIOS Memory workflow or Node ledger failed; callers may retry or fence independently.
    Memory(String),
    /// Canonical JSON encoding or decoding failed.
    Json(serde_json::Error),
    /// Protocol fact was structurally invalid.
    Protocol(String),
}

#[cfg(test)]
#[path = "../engine_tests.rs"]
mod artifact_directive_tests;

impl Display for EngineError {
    /// Formats a stable engine diagnostic.
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Configuration(reason) | Self::Protocol(reason) => formatter.write_str(reason),
            Self::UnsupportedCapability(contract) => {
                write!(formatter, "unsupported canonical capability {contract}")
            }
            Self::MissingCommittedResource => {
                formatter.write_str("required resource is not committed")
            }
            Self::ExecutionConflict(id) => {
                write!(formatter, "execution {id} has conflicting identity")
            }
            Self::LocalLockConflict { key, owner } => {
                write!(formatter, "local lock {key} is owned by execution {owner}")
            }
            Self::ReconciliationRequired(id) => {
                write!(formatter, "execution {id} requires reconciliation")
            }
            Self::UnknownExecution(id) => write!(formatter, "unknown execution {id}"),
            Self::LockState => formatter.write_str("local lock state unavailable"),
            Self::Io(error) => error.fmt(formatter),
            Self::Journal(error) => error.fmt(formatter),
            Self::Catalog(error) => error.fmt(formatter),
            Self::Mapping(error) => error.fmt(formatter),
            Self::Driver(error) => error.fmt(formatter),
            Self::Artifact(error) => error.fmt(formatter),
            Self::Memory(reason) => formatter.write_str(reason),
            Self::Json(error) => error.fmt(formatter),
        }
    }
}

impl std::error::Error for EngineError {}
impl From<JournalError> for EngineError {
    fn from(value: JournalError) -> Self {
        Self::Journal(value)
    }
}
impl From<crate::CatalogError> for EngineError {
    fn from(value: crate::CatalogError) -> Self {
        Self::Catalog(value)
    }
}
impl From<crate::MappingError> for EngineError {
    fn from(value: crate::MappingError) -> Self {
        Self::Mapping(value)
    }
}
impl From<crate::DriverError> for EngineError {
    fn from(value: crate::DriverError) -> Self {
        Self::Driver(value)
    }
}

impl From<ArtifactError> for EngineError {
    /// Converts a node-local artifact failure into the engine error boundary.
    fn from(value: ArtifactError) -> Self {
        Self::Artifact(value)
    }
}

/// Returns the fixed journal path for diagnostics and tests.
pub fn journal_path(state_directory: &std::path::Path) -> PathBuf {
    state_directory.join("execution-journal.sqlite3")
}
