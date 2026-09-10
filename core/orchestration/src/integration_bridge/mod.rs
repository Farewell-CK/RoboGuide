//! Composition bridge from formal Node Protocol facts into Runtime/Control/State semantics.

use control::ControlPlane;
use domain::{
    Capability, CapabilityContractRef, CapabilityKind, CorrelationId, EventPayload,
    ExecutionCommand, ExecutionCouplingMode, ExecutionValue, LeaseId, LocalRuntime,
    LocalSystemDescriptor, LocalSystemId, MemoryKind, MemoryProviderDescriptor, MemoryScopeLimit,
    MemoryVisibility, NodeContractVersion, NodeEvent, NodeHealth, NodeHeartbeat, NodeId, NodeLease,
    NodeStatus, Resource, ResourceId, ResourceKind, SensorDescriptor, SensorId,
    StateExportDescriptor, StateObjectClass, StateObjectRef, StateRecord, StateSemantic,
    StateSource, TimestampMs,
};
use integration::grpc::v0_4::node_message::Message as NodePayload;
use integration::grpc::v0_4::{CanonicalInvocation, ExecutionPhase, NodeRegistration, ScalarValue};
use integration::{GrpcNodeEvent, GrpcNodeRouter};
use ports::{
    EventSink, SharedNodeStateReader, SharedNodeStateWriter, StateRecordReader, StateRecordWriter,
};
use runtime::{
    ExecutionEvent, ExecutionStatus, PeerChannelReadinessEvidence, RuntimeExecutionCheckpoint,
    RuntimeExecutionManager, RuntimeRelationSnapshot, SharedSpatialEvidence,
};
use state::{InMemorySharedNodeState, StateRecordProjection};
use std::collections::{BTreeMap, BTreeSet, VecDeque};
use std::fmt::{Display, Formatter};

mod conversion;
mod dispatch;
mod execution_facts;
mod ingestion;
mod liveness;
mod relation_view;

use conversion::*;

/// Schema marker for the complete Integration/Control/State controller checkpoint.
///
/// Version 13 adds durable Control scheduling reservations and their calendar generation.
pub const CONTROLLER_CHECKPOINT_SCHEMA: &str = "roboguide.controller-checkpoint/v13";

/// Immediately previous checkpoint accepted for one-step migration.
const PREVIOUS_CONTROLLER_CHECKPOINT_SCHEMA: &str = "roboguide.controller-checkpoint/v12";

/// Remote execution lifecycle observed by Runtime before Control terminal handling.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum RemoteExecutionStatus {
    /// Node accepted the command.
    Accepted,
    /// Node reports active execution.
    Running,
    /// Node completed the command.
    Completed,
    /// Node failed the command.
    Failed,
    /// Node cancelled the command.
    Cancelled,
    /// Node could not identify the command.
    Unknown,
}

/// Terminal Task result derived from role execution facts without mutating Control.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ObservedTaskResult {
    /// Every currently bound role completed successfully.
    Succeeded,
    /// At least one currently bound role failed, cancelled, or became unknown.
    Failed,
}

/// One terminal Task result for Mission orchestration to consume explicitly.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ObservedTaskOutcome {
    /// Mission-level Group containing the TaskExecution.
    group_id: domain::ExecutionGroupId,
    /// Mission-scoped Task represented by the result.
    task_ref: domain::TaskRef,
    /// Runtime-derived terminal role result.
    result: ObservedTaskResult,
}

/// Read-only Group-scoped view assembled from existing State evidence.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GroupSharedViewSnapshot {
    /// Mission-level Group that owns the view.
    group_id: domain::ExecutionGroupId,
    /// Coordination Context selecting the exposed member fields.
    context_id: domain::CoordinationContextId,
    /// Optional common map/frame interpretation.
    spatial_reference: Option<domain::SharedSpatialReference>,
    /// One result per logical Task/Role and declared field/schema binding.
    entries: Vec<GroupSharedViewEntry>,
}

/// Freshness classification for one Group view binding.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum GroupViewFreshness {
    /// Current State evidence remains inside its receive-relative validity window.
    Fresh,
    /// State evidence exists but its validity window has elapsed.
    Stale,
    /// No matching State evidence exists for the currently bound member.
    Unknown,
}

/// Strong localization status of one member against the Context's shared map/frame.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum GroupSpatialVerification {
    /// Current-attempt evidence proves the declared map revision and frame.
    Verified,
    /// No current-attempt strong localization evidence is available.
    Unknown,
    /// Current-attempt evidence names a different map revision or frame.
    Mismatched,
}

/// Read-only evidence for one logical Group member field.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GroupSharedViewEntry {
    /// Mission-scoped Task containing the logical member.
    task_ref: domain::TaskRef,
    /// Task-local Role occupying the logical member slot.
    role_id: domain::RoleId,
    /// Current bound Node supplying evidence.
    node_id: NodeId,
    /// Typed semantic field.
    field: domain::GroupViewField,
    /// Exact State payload schema selected by the Context, absent for Runtime execution status.
    payload_schema: Option<String>,
    /// Exact registered State export selected by the Context, absent for Runtime execution status.
    state_export_id: Option<String>,
    /// Latest independently attributed State evidence, when present.
    record: Option<StateRecord>,
    /// Current Runtime-owned execution status for an Execution field.
    execution_status: Option<RemoteExecutionStatus>,
    /// Receive-time freshness result, or Unknown when no record exists.
    freshness: Option<GroupViewFreshness>,
    /// Current-attempt strong localization evidence for the member, when available.
    spatial_evidence: Option<SharedSpatialEvidence>,
    /// Comparison with the Context's declared map/frame, when one is declared.
    spatial_verification: Option<GroupSpatialVerification>,
}

impl GroupSharedViewEntry {
    /// Returns the logical Task member.
    pub const fn task_ref(&self) -> &domain::TaskRef {
        &self.task_ref
    }

    /// Returns the logical Role member.
    pub const fn role_id(&self) -> &domain::RoleId {
        &self.role_id
    }

    /// Returns the current physical placement as evidence, not relation identity.
    pub const fn node_id(&self) -> &NodeId {
        &self.node_id
    }

    /// Returns the typed semantic field.
    pub const fn field(&self) -> domain::GroupViewField {
        self.field
    }

    /// Returns the selected State payload schema.
    pub fn payload_schema(&self) -> Option<&str> {
        self.payload_schema.as_deref()
    }

    /// Returns the exact node-wide State export identity.
    pub fn state_export_id(&self) -> Option<&str> {
        self.state_export_id.as_deref()
    }

    /// Returns the latest attributed State record when one exists.
    pub const fn record(&self) -> Option<&StateRecord> {
        self.record.as_ref()
    }

    /// Returns current Runtime status for an Execution field, when an attempt exists.
    pub const fn execution_status(&self) -> Option<RemoteExecutionStatus> {
        self.execution_status
    }

    /// Returns receive-time freshness when the Context requested it, including Unknown.
    pub const fn freshness(&self) -> Option<GroupViewFreshness> {
        self.freshness
    }

    /// Returns strong localization evidence tied to the current physical attempt.
    pub const fn spatial_evidence(&self) -> Option<&SharedSpatialEvidence> {
        self.spatial_evidence.as_ref()
    }

    /// Returns whether current evidence proves the declared shared spatial reference.
    pub const fn spatial_verification(&self) -> Option<GroupSpatialVerification> {
        self.spatial_verification
    }
}

impl GroupSharedViewSnapshot {
    /// Returns the owning Group.
    pub const fn group_id(&self) -> &domain::ExecutionGroupId {
        &self.group_id
    }

    /// Returns the declaring Context.
    pub const fn context_id(&self) -> &domain::CoordinationContextId {
        &self.context_id
    }

    /// Returns the optional shared spatial interpretation.
    pub const fn spatial_reference(&self) -> Option<&domain::SharedSpatialReference> {
        self.spatial_reference.as_ref()
    }

    /// Returns one deterministic result per member and declared binding.
    pub fn entries(&self) -> &[GroupSharedViewEntry] {
        &self.entries
    }
}

impl ObservedTaskOutcome {
    /// Returns the Mission-level Group containing this Task.
    pub const fn group_id(&self) -> &domain::ExecutionGroupId {
        &self.group_id
    }

    /// Returns the Mission-scoped Task represented by this result.
    pub const fn task_ref(&self) -> &domain::TaskRef {
        &self.task_ref
    }

    /// Returns the terminal role result observed by Runtime.
    pub const fn result(&self) -> ObservedTaskResult {
        self.result
    }
}

/// Live composition state consuming Integration events and routing Runtime commands.
#[derive(Clone)]
pub struct IntegrationRuntimeBridge<E: Clone> {
    /// Existing Control authority for node leases and registration.
    control: ControlPlane,
    /// Shared Node State updated by remote facts.
    state: InMemorySharedNodeState,
    /// Source-aware State records kept separate from typed authority projections.
    state_records: StateRecordProjection,
    /// Existing domain event sink used by Runtime/Control.
    events: E,
    /// Current formal gRPC Node routes.
    router: GrpcNodeRouter,
    /// Runtime-owned live execution registry and sole execution checkpoint authority.
    runtime: RuntimeExecutionManager,
    /// Canonical Runtime transitions awaiting application/orchestration consumption.
    runtime_events: VecDeque<ExecutionEvent>,
    /// Whether restored Unknown attempts still need one application-visible recovery transition.
    restored_recovery_pending: bool,
}

/// Complete durable projection required to reconstruct the controller process.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
struct ControllerCheckpoint {
    /// Exact schema marker validated before any state is restored.
    schema: String,
    /// Control-owned commitments, bindings, and Group lifecycle.
    control: control::ControlCheckpoint,
    /// Shared reported node facts; local receive/liveness times are rebased on restore.
    nodes: Vec<domain::NodeStateSnapshot>,
    /// Independently attributed State channels, preserving their original receive times.
    #[serde(default)]
    state_records: Vec<StateRecord>,
    /// Runtime-owned live execution contexts and continuity state.
    runtime: RuntimeExecutionCheckpoint,
}

/// Validated execution fact context received from one current Node session.
struct ReceivedExecutionFact<'a> {
    /// Reporting node identity.
    node_id: &'a str,
    /// Stable cross-session execution identity.
    execution_id: &'a str,
    /// Execution-local monotonic sequence.
    sequence: u64,
    /// Wire execution phase.
    phase: i32,
    /// Local diagnostic detail.
    reason: &'a str,
}

#[cfg(test)]
#[path = "tests/mod.rs"]
mod tests;

/// Runtime bridge failure.
#[derive(Debug)]
pub enum IntegrationRuntimeError {
    /// Core Control rejected a fact.
    Control(control::ControlError),
    /// Shared State rejected a fact.
    State(ports::SharedStateError),
    /// Source-aware State rejected an ordering or conflict invariant.
    StateRecord(ports::StateRecordError),
    /// Domain conversion failed.
    Domain(domain::DomainError),
    /// gRPC router rejected a command.
    Route(tonic::Status),
    /// Protocol conversion failed.
    Protocol(String),
    /// Stable execution id was reused for another command.
    ExecutionConflict(String),
    /// Durable controller checkpoint was malformed or incompatible.
    Checkpoint(String),
}
impl Display for IntegrationRuntimeError {
    /// Formats bridge failures.
    fn fmt(&self, f: &mut Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Control(error) => error.fmt(f),
            Self::State(error) => error.fmt(f),
            Self::StateRecord(error) => error.fmt(f),
            Self::Domain(error) => error.fmt(f),
            Self::Route(error) => error.fmt(f),
            Self::Protocol(reason) => f.write_str(reason),
            Self::ExecutionConflict(id) => {
                write!(f, "execution {id} was reused with another command")
            }
            Self::Checkpoint(reason) => write!(f, "controller checkpoint failure: {reason}"),
        }
    }
}
impl std::error::Error for IntegrationRuntimeError {}
impl From<control::ControlError> for IntegrationRuntimeError {
    fn from(value: control::ControlError) -> Self {
        Self::Control(value)
    }
}
impl From<ports::SharedStateError> for IntegrationRuntimeError {
    fn from(value: ports::SharedStateError) -> Self {
        Self::State(value)
    }
}
impl From<ports::StateRecordError> for IntegrationRuntimeError {
    /// Preserves source-aware State projection failures at the composition boundary.
    fn from(value: ports::StateRecordError) -> Self {
        Self::StateRecord(value)
    }
}
impl From<domain::DomainError> for IntegrationRuntimeError {
    fn from(value: domain::DomainError) -> Self {
        Self::Domain(value)
    }
}
impl From<tonic::Status> for IntegrationRuntimeError {
    fn from(value: tonic::Status) -> Self {
        Self::Route(value)
    }
}
