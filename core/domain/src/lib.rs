#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! Domain values shared by DEAIOS control, runtime, and node adapters.
//!
//! This crate intentionally contains no transport, serialization, SDK, or
//! simulator dependency. It defines the first internal Node Contract shape.

use std::collections::{BTreeMap, BTreeSet};
use std::fmt::{Display, Formatter};

mod actor;
mod allocation;
mod context;
mod execution;
mod execution_relation;
mod localization_evidence;
mod memory;
mod mission_plan;
mod node_registration;
mod spatial_memory;
mod state_model;
mod task_execution;

pub use actor::{ActorBinding, MissionActor};
pub use allocation::{
    AllocationOwner, AllocationPhase, AllocationViewSnapshot, ResourceAllocation,
    ResourceBindingScope,
};
pub use context::{ContextRole, CoordinationContext, TaskContinuity};
pub use execution::{CapabilityContractRef, ExecutionIntent, ExecutionValue};
pub use execution_relation::{
    CoordinationMechanism, ExecutionCouplingMode, ExecutionRelationKind, ExecutionRelationSpec,
    ExecutionRelationState, ExecutionRelationType, FreshnessPolicyRef, GroupSharedViewSpec,
    GroupViewBinding, GroupViewField, PeerChannelSpec, PlannedExecutionRef,
    RelationStateRequirement, SharedSpatialReference,
};
pub use localization_evidence::{
    LOCALIZATION_EVIDENCE_SCHEMA_V0_1, LocalizationFrames, LocalizationVerificationEvidence,
    PoseQualityComparison, PoseQualityEvidence,
};
pub use memory::{
    LEGACY_MEMORY_CONSUMER_PROVIDER_ID, MEMORY_MANIFEST_SCHEMA_V0_1, MemoryArtifactManifest,
    MemoryArtifactRef, MemoryId, MemoryKind, MemoryOwner, MemoryProviderDescriptor,
    MemoryReplicaSnapshot, MemoryReplicaStatus, MemoryRevisionId, MemoryScope, MemoryScopeLimit,
    MemorySelector, MemoryVisibility,
};

/// Supplies the conservative identity used only when decoding pre-v7 replica evidence.
fn legacy_memory_consumer_provider_id() -> String {
    LEGACY_MEMORY_CONSUMER_PROVIDER_ID.to_string()
}
pub use node_registration::{LocalSystemDescriptor, SensorDescriptor};
pub use spatial_memory::{
    ContentDigest, MAP_MANIFEST_SCHEMA_V0_1, MapArtifactManifest, MapArtifactRef, MapId,
    MapReplicaSnapshot, MapReplicaStatus, MapRevisionId, MapRevisionSelector, MapRevisionSnapshot,
    MapRevisionStatus, SPATIAL_MEMORY_SCHEMA_V0_1, SpatialAnchorId,
};
pub use state_model::{
    MAX_STATE_PAYLOAD_BYTES, STATE_RECORD_SCHEMA_V0_1, StateExportDescriptor, StateObjectClass,
    StateObjectRef, StateRecord, StateRecordKey, StateSemantic, StateSource,
};
pub use task_execution::{TaskExecution, TaskExecutionLifecycle};

/// Version identifier for the first cross-language Mission Plan contract.
pub const MISSION_PLAN_SCHEMA_V0: &str = "roboguide.mission-plan/v0";

/// Version identifier for Mission Plans carrying explicit role execution intents.
pub const MISSION_PLAN_SCHEMA_V0_1: &str = "roboguide.mission-plan/v0.1";

/// Version identifier for Mission Plans declaring Context and ContextRole continuity.
pub const MISSION_PLAN_SCHEMA_V0_2: &str = "roboguide.mission-plan/v0.2";

/// Version identifier for Mission Plans declaring execution-time coordination relations.
pub const MISSION_PLAN_SCHEMA_V0_3: &str = "roboguide.mission-plan/v0.3";

/// Version identifier for Mission Plans carrying execution coupling modes and typed relations.
pub const MISSION_PLAN_SCHEMA_V0_4: &str = "roboguide.mission-plan/v0.4";

/// Version identifier implemented by the first heterogeneous Node Contract.
pub const NODE_CONTRACT_VERSION_V0_1: &str = "roboguide.node.v0.1";

/// Version identifier implemented by the aggregate Local Integration Node Contract.
pub const NODE_CONTRACT_VERSION_V0_2: &str = "roboguide.node.v0.2";

/// Version identifier carrying selective State and Memory provider declarations.
pub const NODE_CONTRACT_VERSION_V0_3: &str = "roboguide.node.v0.3";

/// Errors raised when a domain value violates an invariant.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum DomainError {
    /// An identifier or runtime label was empty.
    EmptyValue {
        /// The kind of value that was empty.
        kind: &'static str,
    },
    /// A lease duration or timestamp range could not be represented safely.
    InvalidDuration {
        /// The duration or range that violated a domain invariant.
        kind: &'static str,
    },
    /// An operation attempted to use a lease after its expiry instant.
    LeaseExpired {
        /// The kind of lease operation that was rejected.
        kind: &'static str,
    },
    /// A Mission Plan or Task Graph violated a structural invariant.
    InvalidMissionPlan {
        /// Stable diagnostic reason suitable for adapter and test evidence.
        reason: String,
    },
    /// A Spatial Memory value or catalog transition violated an invariant.
    InvalidSpatialMemory {
        /// Stable diagnostic reason suitable for State and adapter evidence.
        reason: String,
    },
    /// A State record or export declaration violated its semantic contract.
    InvalidState {
        /// Stable diagnostic reason suitable for adapter and API evidence.
        reason: String,
    },
    /// A Memory manifest, provider, or replica violated its semantic contract.
    InvalidMemory {
        /// Stable diagnostic reason suitable for catalog and adapter evidence.
        reason: String,
    },
}

impl Display for DomainError {
    /// Formats a domain invariant violation for logs and test failures.
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::EmptyValue { kind } => write!(formatter, "{kind} must not be empty"),
            Self::InvalidDuration { kind } => write!(formatter, "invalid {kind} duration"),
            Self::LeaseExpired { kind } => write!(formatter, "{kind} lease has expired"),
            Self::InvalidMissionPlan { reason } => {
                write!(formatter, "invalid mission plan: {reason}")
            }
            Self::InvalidSpatialMemory { reason } => {
                write!(formatter, "invalid spatial memory value: {reason}")
            }
            Self::InvalidState { reason } => write!(formatter, "invalid state value: {reason}"),
            Self::InvalidMemory { reason } => write!(formatter, "invalid memory value: {reason}"),
        }
    }
}

impl std::error::Error for DomainError {}

/// Defines a validated, strongly typed identifier with a stable text form.
macro_rules! define_identifier {
    ($name:ident, $doc:literal, $kind:literal) => {
        #[doc = $doc]
        #[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, serde::Serialize, serde::Deserialize)]
        pub struct $name(String);

        impl $name {
            #[doc = concat!("Creates a validated ", $kind, " identifier.")]
            pub fn new(value: impl Into<String>) -> Result<Self, DomainError> {
                let value = value.into();
                if value.trim().is_empty() {
                    return Err(DomainError::EmptyValue { kind: $kind });
                }
                Ok(Self(value))
            }

            #[doc = concat!("Returns the ", $kind, " identifier as text.")]
            pub fn as_str(&self) -> &str {
                &self.0
            }
        }

        impl Display for $name {
            /// Writes the stable text form of this identifier.
            fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
                formatter.write_str(&self.0)
            }
        }
    };
}

define_identifier!(
    MissionId,
    "Identifies a mission supplied to DEAIOS.",
    "mission"
);
define_identifier!(TaskId, "Identifies a task within a mission.", "task");
define_identifier!(
    ActorId,
    "Identifies a logical execution actor within a mission.",
    "actor"
);
define_identifier!(NodeId, "Identifies a logical execution node.", "node");
define_identifier!(
    LocalSystemId,
    "Identifies one local embodied system within a node.",
    "local system"
);
define_identifier!(SensorId, "Identifies one sensor within a node.", "sensor");
define_identifier!(
    RoleId,
    "Identifies a responsibility inside an execution group.",
    "role"
);
define_identifier!(ResourceId, "Identifies a reservable resource.", "resource");
define_identifier!(
    ExecutionGroupId,
    "Identifies a dynamic execution group.",
    "execution group"
);
define_identifier!(
    CoordinationContextId,
    "Identifies one Mission Intelligence coordination context.",
    "coordination context"
);
define_identifier!(
    ContextRoleId,
    "Identifies one role that remains continuous across Tasks in a Context.",
    "context role"
);
define_identifier!(
    ExecutionRelationId,
    "Identifies one execution coordination relation within a mission.",
    "execution relation"
);
define_identifier!(EventId, "Identifies one immutable event record.", "event");
define_identifier!(
    CorrelationId,
    "Identifies one end-to-end operation trace.",
    "correlation"
);
define_identifier!(LeaseId, "Identifies a renewable node lease.", "lease");
define_identifier!(
    NodeContractVersion,
    "Identifies a versioned heterogeneous node integration contract.",
    "node contract version"
);

impl NodeContractVersion {
    /// Returns the first supported heterogeneous Node Contract version.
    pub fn v0_1() -> Self {
        Self(NODE_CONTRACT_VERSION_V0_1.to_string())
    }

    /// Returns the aggregate Local Integration Node Contract version.
    pub fn v0_2() -> Self {
        Self(NODE_CONTRACT_VERSION_V0_2.to_string())
    }

    /// Returns the contract version carrying State and Memory extension declarations.
    pub fn v0_3() -> Self {
        Self(NODE_CONTRACT_VERSION_V0_3.to_string())
    }
}

/// Uniquely identifies a mission-scoped task across concurrent missions.
#[derive(
    Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, serde::Serialize, serde::Deserialize,
)]
pub struct TaskRef {
    /// Mission that owns the task namespace.
    mission_id: MissionId,
    /// Task identity scoped by the owning mission.
    task_id: TaskId,
}

impl TaskRef {
    /// Creates an unambiguous task reference from its mission and local task identity.
    pub const fn new(mission_id: MissionId, task_id: TaskId) -> Self {
        Self {
            mission_id,
            task_id,
        }
    }

    /// Returns the mission that owns this task.
    pub const fn mission_id(&self) -> &MissionId {
        &self.mission_id
    }

    /// Returns the task identity within its mission namespace.
    pub const fn task_id(&self) -> &TaskId {
        &self.task_id
    }
}

impl Display for TaskRef {
    /// Formats a task reference without collapsing its mission namespace.
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        write!(formatter, "{}/{}", self.mission_id, self.task_id)
    }
}

/// A millisecond clock reading whose comparison domain is defined by its containing field.
///
/// Readings from independent source clocks and RoboGuide clocks are not
/// directly comparable merely because they share this representation.
#[derive(
    Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, serde::Serialize, serde::Deserialize,
)]
pub struct TimestampMs(u64);

impl TimestampMs {
    /// Creates a timestamp from elapsed milliseconds.
    pub const fn new(milliseconds: u64) -> Self {
        Self(milliseconds)
    }

    /// Returns elapsed milliseconds represented by this timestamp.
    pub const fn as_millis(self) -> u64 {
        self.0
    }
}

/// A renewable time-bound authority for a node to remain schedulable.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct NodeLease {
    /// Stable identity of the lease instance.
    lease_id: LeaseId,
    /// Node that owns the lease.
    node_id: NodeId,
    /// RoboGuide-local time at which this lease interval began.
    issued_at: TimestampMs,
    /// RoboGuide-local time after which the lease cannot authorize scheduling.
    expires_at: TimestampMs,
}

impl NodeLease {
    /// Creates a lease with a strictly positive duration.
    pub fn new(
        lease_id: LeaseId,
        node_id: NodeId,
        issued_at: TimestampMs,
        duration_ms: u64,
    ) -> Result<Self, DomainError> {
        if duration_ms == 0 {
            return Err(DomainError::InvalidDuration { kind: "node lease" });
        }
        let expires_at = issued_at
            .as_millis()
            .checked_add(duration_ms)
            .ok_or(DomainError::InvalidDuration { kind: "node lease" })?;
        Ok(Self {
            lease_id,
            node_id,
            issued_at,
            expires_at: TimestampMs::new(expires_at),
        })
    }

    /// Returns the lease identity.
    pub fn lease_id(&self) -> &LeaseId {
        &self.lease_id
    }

    /// Returns the node authorized by this lease.
    pub fn node_id(&self) -> &NodeId {
        &self.node_id
    }

    /// Returns when the lease was issued or last renewed.
    pub const fn issued_at(&self) -> TimestampMs {
        self.issued_at
    }

    /// Returns the first timestamp at which the lease is no longer active.
    pub const fn expires_at(&self) -> TimestampMs {
        self.expires_at
    }

    /// Returns whether the lease is active at the supplied RoboGuide-local time.
    pub const fn is_active_at(&self, now: TimestampMs) -> bool {
        now.as_millis() < self.expires_at.as_millis()
    }

    /// Renews an active lease without changing its identity or owning node.
    pub fn renew(&self, now: TimestampMs, duration_ms: u64) -> Result<Self, DomainError> {
        if !self.is_active_at(now) {
            return Err(DomainError::LeaseExpired { kind: "node" });
        }
        Self::new(
            self.lease_id.clone(),
            self.node_id.clone(),
            now,
            duration_ms,
        )
    }
}

/// Identifies the local runtime implementation behind a node adapter.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct LocalRuntime {
    /// Human-readable name of the local EAIOS implementation.
    name: String,
    /// Version reported by the local runtime implementation.
    version: String,
}

impl LocalRuntime {
    /// Creates a validated local runtime descriptor for an EAIOS or equivalent.
    pub fn new(name: impl Into<String>, version: impl Into<String>) -> Result<Self, DomainError> {
        let name = name.into();
        let version = version.into();
        if name.trim().is_empty() {
            return Err(DomainError::EmptyValue {
                kind: "runtime name",
            });
        }
        if version.trim().is_empty() {
            return Err(DomainError::EmptyValue {
                kind: "runtime version",
            });
        }
        Ok(Self { name, version })
    }

    /// Returns the local runtime implementation name.
    pub fn name(&self) -> &str {
        &self.name
    }

    /// Returns the local runtime implementation version.
    pub fn version(&self) -> &str {
        &self.version
    }
}

/// Capability categories understood by the first DEAIOS slice.
#[derive(
    Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, serde::Serialize, serde::Deserialize,
)]
pub enum CapabilityKind {
    /// Ability to move through the shared physical space.
    Mobility,
    /// Ability to carry or transport a task payload.
    Transport,
    /// Ability to execute compute work.
    Compute,
    /// Ability to produce observations about the world or node state.
    Observation,
}

/// One capability advertised by a node.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct Capability {
    /// Capability category exposed by the node.
    kind: CapabilityKind,
    /// Whether control may currently schedule this capability.
    available: bool,
}

impl Capability {
    /// Creates a capability with an explicit initial availability state.
    pub const fn new(kind: CapabilityKind, available: bool) -> Self {
        Self { kind, available }
    }

    /// Returns the category of this capability.
    pub const fn kind(&self) -> CapabilityKind {
        self.kind
    }

    /// Returns whether the capability may currently be scheduled.
    pub const fn is_available(&self) -> bool {
        self.available
    }
}

/// Resource categories that may participate in a proposal or commitment.
#[derive(
    Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, serde::Serialize, serde::Deserialize,
)]
pub enum ResourceKind {
    /// A shared physical region, lane, or corridor.
    Space,
    /// A bounded compute allocation.
    Compute,
    /// A time window or temporal execution slot.
    Time,
}

/// A resource advertised by a node.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct Resource {
    /// Stable identity of the reservable resource.
    id: ResourceId,
    /// Resource category used during coordination.
    kind: ResourceKind,
    /// Capacity available for the resource's category.
    capacity: u32,
}

impl Resource {
    /// Creates a resource with a positive capacity.
    pub fn new(id: ResourceId, kind: ResourceKind, capacity: u32) -> Result<Self, DomainError> {
        if capacity == 0 {
            return Err(DomainError::EmptyValue {
                kind: "resource capacity",
            });
        }
        Ok(Self { id, kind, capacity })
    }

    /// Returns the resource identity.
    pub fn id(&self) -> &ResourceId {
        &self.id
    }

    /// Returns the resource category.
    pub const fn kind(&self) -> ResourceKind {
        self.kind
    }

    /// Returns the advertised capacity.
    pub const fn capacity(&self) -> u32 {
        self.capacity
    }
}

/// A role and the capability/resource facts required to perform it.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct RoleRequirement {
    /// Responsibility identity required by the task.
    role_id: RoleId,
    /// Capability category needed to perform the role.
    capability: CapabilityKind,
    /// Optional mission actor whose node binding must remain continuous across tasks.
    actor_id: Option<ActorId>,
    /// Exact canonical capability contract required by the role.
    contract: Option<CapabilityContractRef>,
    /// Optional resource category that must be bound to the role.
    resource_kind: Option<ResourceKind>,
}

impl RoleRequirement {
    /// Creates a role requirement for task matching.
    pub const fn new(
        role_id: RoleId,
        capability: CapabilityKind,
        resource_kind: Option<ResourceKind>,
    ) -> Self {
        Self {
            role_id,
            capability,
            actor_id: None,
            contract: None,
            resource_kind,
        }
    }

    /// Creates a role requirement with mission actor continuity and an exact contract.
    pub fn new_with_actor_and_contract(
        role_id: RoleId,
        actor_id: ActorId,
        capability: CapabilityKind,
        contract: CapabilityContractRef,
        resource_kind: Option<ResourceKind>,
    ) -> Self {
        Self {
            role_id,
            capability,
            actor_id: Some(actor_id),
            contract: Some(contract),
            resource_kind,
        }
    }

    /// Returns the role identity.
    pub fn role_id(&self) -> &RoleId {
        &self.role_id
    }

    /// Returns the required capability.
    pub const fn capability(&self) -> CapabilityKind {
        self.capability
    }

    /// Returns the mission actor, when this role participates in continuity.
    pub fn actor_id(&self) -> Option<&ActorId> {
        self.actor_id.as_ref()
    }

    /// Returns the exact canonical contract, when declared.
    pub fn required_contract(&self) -> Option<&CapabilityContractRef> {
        self.contract.as_ref()
    }

    /// Returns the optional resource category required by this role.
    pub const fn resource_kind(&self) -> Option<ResourceKind> {
        self.resource_kind
    }
}

/// A mission task's role-level execution requirements.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct TaskRequirement {
    /// Mission-scoped task whose execution requirements are being described.
    task_ref: TaskRef,
    /// Role requirements in the task's declared order.
    roles: Vec<RoleRequirement>,
}

impl TaskRequirement {
    /// Creates a task requirement with at least one uniquely identified role.
    pub fn new(
        mission_id: MissionId,
        task_id: TaskId,
        roles: Vec<RoleRequirement>,
    ) -> Result<Self, DomainError> {
        if roles.is_empty() {
            return Err(DomainError::EmptyValue { kind: "task roles" });
        }
        let mut role_ids = BTreeSet::new();
        if let Some(duplicate) = roles
            .iter()
            .map(RoleRequirement::role_id)
            .find(|role_id| !role_ids.insert((*role_id).clone()))
        {
            return Err(DomainError::InvalidMissionPlan {
                reason: format!("duplicate role id {duplicate}"),
            });
        }
        Ok(Self {
            task_ref: TaskRef::new(mission_id, task_id),
            roles,
        })
    }

    /// Returns the complete mission-scoped task identity.
    pub const fn task_ref(&self) -> &TaskRef {
        &self.task_ref
    }

    /// Returns the mission identity.
    pub const fn mission_id(&self) -> &MissionId {
        self.task_ref.mission_id()
    }

    /// Returns the task identity.
    pub const fn task_id(&self) -> &TaskId {
        self.task_ref.task_id()
    }

    /// Returns all role requirements in declaration order.
    pub fn roles(&self) -> &[RoleRequirement] {
        &self.roles
    }
}

/// A user-visible mission objective before global scheduling decisions.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct MissionGoal {
    /// Stable mission identity shared by every task in the graph.
    mission_id: MissionId,
    /// Outcome Mission Intelligence must decompose without selecting nodes.
    objective: String,
}

impl MissionGoal {
    /// Creates a mission goal with a nonblank objective.
    pub fn new(mission_id: MissionId, objective: impl Into<String>) -> Result<Self, DomainError> {
        let objective = objective.into();
        if objective.trim().is_empty() {
            return Err(DomainError::EmptyValue {
                kind: "mission objective",
            });
        }
        Ok(Self {
            mission_id,
            objective,
        })
    }

    /// Returns the stable mission identity.
    pub fn mission_id(&self) -> &MissionId {
        &self.mission_id
    }

    /// Returns the requested user-visible outcome.
    pub fn objective(&self) -> &str {
        &self.objective
    }
}

/// One Task Graph node with dependencies and role-level execution requirements.
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct PlannedTask {
    /// Human-readable task outcome used for review and diagnostics.
    description: String,
    /// Task requirements consumed by Control capability matching.
    requirement: TaskRequirement,
    /// Canonical operation intent for each declared role.
    execution_intents: BTreeMap<RoleId, ExecutionIntent>,
    /// Tasks that must complete before this task becomes ready.
    dependencies: Vec<TaskId>,
    /// Context and resource-lifetime declarations supplied by Mission Intelligence.
    continuity: TaskContinuity,
}

impl PlannedTask {
    /// Creates a task while rejecting blank descriptions and duplicate dependencies.
    pub fn new(
        description: impl Into<String>,
        requirement: TaskRequirement,
        execution_intents: BTreeMap<RoleId, ExecutionIntent>,
        dependencies: Vec<TaskId>,
        continuity: TaskContinuity,
    ) -> Result<Self, DomainError> {
        let description = description.into();
        if description.trim().is_empty() {
            return Err(DomainError::EmptyValue {
                kind: "task description",
            });
        }
        let unique_dependencies: BTreeSet<&TaskId> = dependencies.iter().collect();
        if unique_dependencies.len() != dependencies.len() {
            return Err(DomainError::InvalidMissionPlan {
                reason: format!("task {} has duplicate dependencies", requirement.task_id()),
            });
        }
        if dependencies
            .iter()
            .any(|dependency| dependency == requirement.task_id())
        {
            return Err(DomainError::InvalidMissionPlan {
                reason: format!("task {} depends on itself", requirement.task_id()),
            });
        }
        let required_roles = requirement
            .roles()
            .iter()
            .map(RoleRequirement::role_id)
            .collect::<BTreeSet<_>>();
        let intent_roles = execution_intents.keys().collect::<BTreeSet<_>>();
        if required_roles != intent_roles {
            return Err(DomainError::InvalidMissionPlan {
                reason: format!(
                    "task {} execution intents must exactly cover its roles",
                    requirement.task_id()
                ),
            });
        }
        if continuity
            .context_roles()
            .keys()
            .chain(continuity.resource_scopes().keys())
            .any(|role_id| !required_roles.contains(role_id))
        {
            return Err(DomainError::InvalidMissionPlan {
                reason: format!(
                    "task {} continuity references an unknown role",
                    requirement.task_id()
                ),
            });
        }
        for role in requirement.roles() {
            if let Some(contract) = role.required_contract() {
                let intent = execution_intents
                    .get(role.role_id())
                    .expect("role set validated");
                if intent.capability_contract() != contract {
                    return Err(DomainError::InvalidMissionPlan {
                        reason: format!(
                            "task {} role {} contract differs from execution intent",
                            requirement.task_id(),
                            role.role_id()
                        ),
                    });
                }
            }
        }
        Ok(Self {
            description,
            requirement,
            execution_intents,
            dependencies,
            continuity,
        })
    }

    /// Returns the task identity carried by its execution requirements.
    pub fn task_id(&self) -> &TaskId {
        self.requirement.task_id()
    }

    /// Returns the human-readable task outcome.
    pub fn description(&self) -> &str {
        &self.description
    }

    /// Returns the role-level requirements consumed by Control.
    pub const fn requirement(&self) -> &TaskRequirement {
        &self.requirement
    }

    /// Returns the canonical execution intent associated with one role.
    pub fn execution_intent(&self, role_id: &RoleId) -> Option<&ExecutionIntent> {
        self.execution_intents.get(role_id)
    }

    /// Returns all role intents in stable role-identity order.
    pub const fn execution_intents(&self) -> &BTreeMap<RoleId, ExecutionIntent> {
        &self.execution_intents
    }

    /// Returns prerequisite task identities in declaration order.
    pub fn dependencies(&self) -> &[TaskId] {
        &self.dependencies
    }

    /// Returns this Task's semantic continuity and resource-lifetime declaration.
    pub const fn continuity(&self) -> &TaskContinuity {
        &self.continuity
    }
}

/// A validated acyclic Task Graph owned by one mission.
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct TaskGraph {
    /// Mission that owns every task in this graph.
    mission_id: MissionId,
    /// Tasks retained in planner declaration order.
    tasks: Vec<PlannedTask>,
}

impl TaskGraph {
    /// Creates a graph and rejects identity mismatch, unknown dependencies, or cycles.
    pub fn new(mission_id: MissionId, tasks: Vec<PlannedTask>) -> Result<Self, DomainError> {
        if tasks.is_empty() {
            return Err(DomainError::InvalidMissionPlan {
                reason: "task graph must not be empty".to_string(),
            });
        }
        let mut dependencies = BTreeMap::<TaskId, BTreeSet<TaskId>>::new();
        for task in &tasks {
            if task.requirement().mission_id() != &mission_id {
                return Err(DomainError::InvalidMissionPlan {
                    reason: format!("task {} belongs to another mission", task.task_id()),
                });
            }
            if dependencies
                .insert(
                    task.task_id().clone(),
                    task.dependencies().iter().cloned().collect(),
                )
                .is_some()
            {
                return Err(DomainError::InvalidMissionPlan {
                    reason: format!("duplicate task id {}", task.task_id()),
                });
            }
        }
        let known_tasks: BTreeSet<TaskId> = dependencies.keys().cloned().collect();
        for (task_id, prerequisites) in &dependencies {
            if let Some(unknown) = prerequisites
                .iter()
                .find(|dependency| !known_tasks.contains(*dependency))
            {
                return Err(DomainError::InvalidMissionPlan {
                    reason: format!("task {task_id} depends on unknown task {unknown}"),
                });
            }
        }
        let mut remaining = dependencies;
        while !remaining.is_empty() {
            let ready: BTreeSet<TaskId> = remaining
                .iter()
                .filter(|(_, prerequisites)| prerequisites.is_empty())
                .map(|(task_id, _)| task_id.clone())
                .collect();
            if ready.is_empty() {
                return Err(DomainError::InvalidMissionPlan {
                    reason: "task graph contains a cycle".to_string(),
                });
            }
            remaining.retain(|task_id, _| !ready.contains(task_id));
            for prerequisites in remaining.values_mut() {
                prerequisites.retain(|dependency| !ready.contains(dependency));
            }
        }
        Ok(Self { mission_id, tasks })
    }

    /// Returns the mission that owns this graph.
    pub fn mission_id(&self) -> &MissionId {
        &self.mission_id
    }

    /// Returns tasks in planner declaration order.
    pub fn tasks(&self) -> &[PlannedTask] {
        &self.tasks
    }

    /// Returns tasks whose dependencies are all present in the completed set.
    pub fn ready_tasks(&self, completed: &BTreeSet<TaskId>) -> Vec<&PlannedTask> {
        self.tasks
            .iter()
            .filter(|task| {
                !completed.contains(task.task_id())
                    && task
                        .dependencies()
                        .iter()
                        .all(|dependency| completed.contains(dependency))
            })
            .collect()
    }
}

/// A versioned Mission Intelligence result accepted by the DEAIOS core.
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct MissionPlan {
    /// User-visible goal preserved across planning and recovery.
    goal: MissionGoal,
    /// Validated task decomposition and execution requirements.
    task_graph: TaskGraph,
    /// Mission Intelligence contexts available to every planned Task.
    contexts: Vec<CoordinationContext>,
}

impl MissionPlan {
    /// Creates a plan only when the goal and Task Graph share one mission identity.
    pub fn new(
        goal: MissionGoal,
        task_graph: TaskGraph,
        contexts: Vec<CoordinationContext>,
    ) -> Result<Self, DomainError> {
        if goal.mission_id() != task_graph.mission_id() {
            return Err(DomainError::InvalidMissionPlan {
                reason: "goal and task graph mission ids differ".to_string(),
            });
        }
        let context_ids = contexts
            .iter()
            .map(CoordinationContext::context_id)
            .collect::<BTreeSet<_>>();
        if context_ids.len() != contexts.len() {
            return Err(DomainError::InvalidMissionPlan {
                reason: "Mission Plan has duplicate context ids".to_string(),
            });
        }
        let relation_ids = contexts
            .iter()
            .flat_map(CoordinationContext::relations)
            .map(ExecutionRelationSpec::relation_id)
            .collect::<BTreeSet<_>>();
        let relation_count = contexts
            .iter()
            .map(|context| context.relations().len())
            .sum::<usize>();
        if relation_ids.len() != relation_count {
            return Err(DomainError::InvalidMissionPlan {
                reason: "Mission Plan has duplicate execution relation ids".to_string(),
            });
        }
        for task in task_graph.tasks() {
            let context = contexts
                .iter()
                .find(|context| context.context_id() == task.continuity().context_id())
                .ok_or_else(|| DomainError::InvalidMissionPlan {
                    reason: format!("task {} references an unknown context", task.task_id()),
                })?;
            for (role_id, context_role_id) in task.continuity().context_roles() {
                let context_role = context.role(context_role_id).ok_or_else(|| {
                    DomainError::InvalidMissionPlan {
                        reason: format!(
                            "task {} role {role_id} references an unknown context role",
                            task.task_id()
                        ),
                    }
                })?;
                let actor_id = task
                    .requirement()
                    .roles()
                    .iter()
                    .find(|role| role.role_id() == role_id)
                    .and_then(RoleRequirement::actor_id);
                if actor_id != Some(context_role.actor_id()) {
                    return Err(DomainError::InvalidMissionPlan {
                        reason: format!(
                            "task {} role {role_id} actor differs from its context role",
                            task.task_id()
                        ),
                    });
                }
            }
            for (role_id, scope) in task.continuity().resource_scopes() {
                if *scope == ResourceBindingScope::Context
                    && task.continuity().context_role(role_id).is_none()
                {
                    return Err(DomainError::InvalidMissionPlan {
                        reason: format!(
                            "task {} context-scoped role {role_id} has no ContextRole",
                            task.task_id()
                        ),
                    });
                }
            }
            let coupling_mode = task
                .continuity()
                .coupling_mode_override()
                .unwrap_or_else(|| context.coupling_mode());
            context.validate_mechanisms_for(coupling_mode)?;
        }
        for context in &contexts {
            for relation in context.relations() {
                if relation.kind() != relation.relation_type().kind() {
                    return Err(DomainError::InvalidMissionPlan {
                        reason: format!(
                            "execution relation {} has inconsistent kind and typed relation",
                            relation.relation_id()
                        ),
                    });
                }
                validate_relation_endpoint(&task_graph, context, relation.source())?;
                validate_relation_endpoint(&task_graph, context, relation.target())?;
                if relation.source().task_id() != relation.target().task_id()
                    && (task_depends_on(
                        &task_graph,
                        relation.source().task_id(),
                        relation.target().task_id(),
                    ) || task_depends_on(
                        &task_graph,
                        relation.target().task_id(),
                        relation.source().task_id(),
                    ))
                {
                    return Err(DomainError::InvalidMissionPlan {
                        reason: format!(
                            "execution relation {} connects Tasks ordered by the DAG",
                            relation.relation_id()
                        ),
                    });
                }
            }
        }
        Ok(Self {
            goal,
            task_graph,
            contexts,
        })
    }

    /// Returns the versioned adapter contract represented by this domain shape.
    pub const fn schema_version(&self) -> &'static str {
        MISSION_PLAN_SCHEMA_V0_4
    }

    /// Returns the original mission goal.
    pub const fn goal(&self) -> &MissionGoal {
        &self.goal
    }

    /// Returns the validated Task Graph.
    pub const fn task_graph(&self) -> &TaskGraph {
        &self.task_graph
    }

    /// Returns Mission Intelligence contexts in declaration order.
    pub fn contexts(&self) -> &[CoordinationContext] {
        &self.contexts
    }
}

/// Confirms one relation endpoint is an exact Task/Role in the containing Context.
fn validate_relation_endpoint(
    graph: &TaskGraph,
    context: &CoordinationContext,
    endpoint: &PlannedExecutionRef,
) -> Result<(), DomainError> {
    let task = graph
        .tasks()
        .iter()
        .find(|task| task.task_id() == endpoint.task_id())
        .ok_or_else(|| DomainError::InvalidMissionPlan {
            reason: format!(
                "execution relation references unknown Task {}",
                endpoint.task_id()
            ),
        })?;
    if task.continuity().context_id() != context.context_id() {
        return Err(DomainError::InvalidMissionPlan {
            reason: format!(
                "execution relation endpoint {}:{} belongs to another Context",
                endpoint.task_id(),
                endpoint.role_id()
            ),
        });
    }
    if !task
        .requirement()
        .roles()
        .iter()
        .any(|role| role.role_id() == endpoint.role_id())
    {
        return Err(DomainError::InvalidMissionPlan {
            reason: format!(
                "execution relation references unknown Role {} in Task {}",
                endpoint.role_id(),
                endpoint.task_id()
            ),
        });
    }
    Ok(())
}

/// Returns whether `task_id` transitively depends on `candidate_dependency`.
fn task_depends_on(graph: &TaskGraph, task_id: &TaskId, candidate_dependency: &TaskId) -> bool {
    let Some(task) = graph.tasks().iter().find(|task| task.task_id() == task_id) else {
        return false;
    };
    task.dependencies().iter().any(|dependency| {
        dependency == candidate_dependency
            || task_depends_on(graph, dependency, candidate_dependency)
    })
}

/// A node's proposed assignment for one execution-group role.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct RoleAssignment {
    /// Role receiving the assignment.
    role_id: RoleId,
    /// Node selected to execute the role.
    node_id: NodeId,
    /// Resources proposed for the role's execution.
    resource_ids: Vec<ResourceId>,
}

impl RoleAssignment {
    /// Creates a role assignment before proposal validation.
    pub const fn new(role_id: RoleId, node_id: NodeId, resource_ids: Vec<ResourceId>) -> Self {
        Self {
            role_id,
            node_id,
            resource_ids,
        }
    }

    /// Returns the assigned role.
    pub fn role_id(&self) -> &RoleId {
        &self.role_id
    }

    /// Returns the assigned node.
    pub fn node_id(&self) -> &NodeId {
        &self.node_id
    }

    /// Returns resources reserved for this role.
    pub fn resource_ids(&self) -> &[ResourceId] {
        &self.resource_ids
    }
}

/// The health state a node reports to the distributed system.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum NodeHealth {
    /// The node is available for normal scheduling and execution.
    Online,
    /// The node may execute work but has degraded evidence or capacity.
    Degraded,
    /// The node cannot receive new work.
    Offline,
    /// The node has entered a local safety stop.
    SafeStopped,
}

impl NodeHealth {
    /// Returns whether this health state may be considered by matching.
    pub const fn is_schedulable(self) -> bool {
        matches!(self, Self::Online | Self::Degraded)
    }
}

/// A timestamped health snapshot for a node.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct NodeStatus {
    /// Most recent health classification reported by the node.
    health: NodeHealth,
    /// Source-local time at which the Local EAIOS observed this health.
    observed_at: TimestampMs,
}

/// A health-bearing heartbeat sent by a local EAIOS to DEAIOS.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NodeHeartbeat {
    /// Node sending the heartbeat.
    node_id: NodeId,
    /// Lease the node claims to renew.
    lease_id: LeaseId,
    /// Latest health snapshot observed by the node.
    status: NodeStatus,
}

impl NodeHeartbeat {
    /// Creates a heartbeat for one node and lease.
    pub const fn new(node_id: NodeId, lease_id: LeaseId, status: NodeStatus) -> Self {
        Self {
            node_id,
            lease_id,
            status,
        }
    }

    /// Returns the node sending this heartbeat.
    pub fn node_id(&self) -> &NodeId {
        &self.node_id
    }

    /// Returns the lease being renewed.
    pub fn lease_id(&self) -> &LeaseId {
        &self.lease_id
    }

    /// Returns the health snapshot carried by this heartbeat.
    pub const fn status(&self) -> NodeStatus {
        self.status
    }
}

impl NodeStatus {
    /// Creates a health snapshot with its observation time.
    pub const fn new(health: NodeHealth, observed_at: TimestampMs) -> Self {
        Self {
            health,
            observed_at,
        }
    }

    /// Returns the reported health state.
    pub const fn health(self) -> NodeHealth {
        self.health
    }

    /// Returns when the source observed this health in its own local time domain.
    pub const fn observed_at(self) -> TimestampMs {
        self.observed_at
    }
}

/// A normalized health observation reported by one local EAIOS or adapter.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NodeHealthObservation {
    /// Node whose local health was observed.
    node_id: NodeId,
    /// Latest timestamped health explicitly reported by the local system.
    status: NodeStatus,
    /// RoboGuide-local time at which this observation was received and normalized.
    received_at: TimestampMs,
}

impl NodeHealthObservation {
    /// Creates a transport-neutral node health observation.
    pub const fn new(node_id: NodeId, status: NodeStatus, received_at: TimestampMs) -> Self {
        Self {
            node_id,
            status,
            received_at,
        }
    }

    /// Returns the node that produced the health observation.
    pub const fn node_id(&self) -> &NodeId {
        &self.node_id
    }

    /// Returns the timestamped health reported by the local system.
    pub const fn status(&self) -> NodeStatus {
        self.status
    }

    /// Returns when RoboGuide received this observation in its local time domain.
    pub const fn received_at(&self) -> TimestampMs {
        self.received_at
    }
}

/// Minimal system-observed reachability of one node.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum NodeLiveness {
    /// RoboGuide successfully observed or reached the node.
    Reachable,
    /// RoboGuide can no longer establish current reachability.
    Unreachable,
}

/// A timestamped liveness fact derived by RoboGuide rather than the local EAIOS.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct NodeLivenessObservation {
    /// Current minimal reachability classification.
    liveness: NodeLiveness,
    /// RoboGuide-local time at which it observed this liveness.
    observed_at: TimestampMs,
}

impl NodeLivenessObservation {
    /// Creates a timestamped system-observed liveness fact.
    pub const fn new(liveness: NodeLiveness, observed_at: TimestampMs) -> Self {
        Self {
            liveness,
            observed_at,
        }
    }

    /// Returns the observed reachability classification.
    pub const fn liveness(self) -> NodeLiveness {
        self.liveness
    }

    /// Returns when RoboGuide observed this liveness.
    pub const fn observed_at(self) -> TimestampMs {
        self.observed_at
    }
}

/// The local runtime and resources a node exposes to DEAIOS.
///
/// Owner maps are encoded as arrays of typed entries instead of JSON objects.  A
/// `CapabilityContractRef` is a structured value rather than a scalar string, so
/// serde_json cannot use it directly as an object key during controller checkpoint
/// serialization.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct NodeRegistration {
    /// Logical node identity exposed to DEAIOS.
    node_id: NodeId,
    /// Local EAIOS/runtime descriptors aggregated behind this Node identity.
    local_systems: Vec<LocalSystemDescriptor>,
    /// Semantic integration contract implemented by the adapter or bridge.
    contract_version: NodeContractVersion,
    /// Capabilities currently advertised by the node.
    capabilities: Vec<Capability>,
    /// Canonical capability contracts executable through this node's adapter boundary.
    supported_contracts: Vec<CapabilityContractRef>,
    /// Unique local-system owner of each canonical contract.
    #[serde(with = "capability_owner_map_serde")]
    capability_owners: BTreeMap<CapabilityContractRef, LocalSystemId>,
    /// Exact coarse capability category associated with each canonical contract.
    #[serde(default, with = "capability_kind_map_serde")]
    capability_kinds: BTreeMap<CapabilityContractRef, CapabilityKind>,
    /// Latest observed readiness of each canonical contract.
    #[serde(default, with = "capability_readiness_map_serde")]
    capability_readiness: BTreeMap<CapabilityContractRef, bool>,
    /// Sensors exposed by configured local systems.
    sensors: Vec<SensorDescriptor>,
    /// Resources currently advertised by the node.
    resources: Vec<Resource>,
    /// Unique local-system owner of each node-wide resource.
    #[serde(with = "resource_owner_map_serde")]
    resource_owners: BTreeMap<ResourceId, LocalSystemId>,
    /// Selective source-aware State channels exposed by configured local systems.
    #[serde(default)]
    state_exports: Vec<StateExportDescriptor>,
    /// Selective Memory discovery and exchange providers exposed by local systems.
    #[serde(default)]
    memory_providers: Vec<MemoryProviderDescriptor>,
}

/// Encodes structured capability-contract owner keys as checkpoint-safe records.
mod capability_owner_map_serde {
    use super::{CapabilityContractRef, LocalSystemId};
    use serde::{Deserialize, Deserializer, Serialize, Serializer};
    use std::collections::BTreeMap;

    /// Serializes each capability owner mapping as a typed two-element record.
    pub fn serialize<S: Serializer>(
        values: &BTreeMap<CapabilityContractRef, LocalSystemId>,
        serializer: S,
    ) -> Result<S::Ok, S::Error> {
        values.iter().collect::<Vec<_>>().serialize(serializer)
    }

    /// Restores capability owner mappings and rejects duplicate contract identities.
    pub fn deserialize<'de, D: Deserializer<'de>>(
        deserializer: D,
    ) -> Result<BTreeMap<CapabilityContractRef, LocalSystemId>, D::Error> {
        let entries: Vec<(CapabilityContractRef, LocalSystemId)> = Vec::deserialize(deserializer)?;
        let mut values = BTreeMap::new();
        for (contract, owner) in entries {
            if values.insert(contract, owner).is_some() {
                return Err(serde::de::Error::custom(
                    "duplicate capability owner contract",
                ));
            }
        }
        Ok(values)
    }
}

/// Encodes structured capability-contract readiness keys as checkpoint-safe records.
mod capability_readiness_map_serde {
    use super::CapabilityContractRef;
    use serde::{Deserialize, Deserializer, Serialize, Serializer};
    use std::collections::BTreeMap;

    /// Serializes each contract readiness fact as a typed two-element record.
    pub fn serialize<S: Serializer>(
        values: &BTreeMap<CapabilityContractRef, bool>,
        serializer: S,
    ) -> Result<S::Ok, S::Error> {
        values.iter().collect::<Vec<_>>().serialize(serializer)
    }

    /// Restores readiness facts and rejects duplicate contract identities.
    pub fn deserialize<'de, D: Deserializer<'de>>(
        deserializer: D,
    ) -> Result<BTreeMap<CapabilityContractRef, bool>, D::Error> {
        let entries: Vec<(CapabilityContractRef, bool)> = Vec::deserialize(deserializer)?;
        let mut values = BTreeMap::new();
        for (contract, available) in entries {
            if values.insert(contract, available).is_some() {
                return Err(serde::de::Error::custom(
                    "duplicate capability readiness contract",
                ));
            }
        }
        Ok(values)
    }
}

/// Encodes structured capability-contract category keys as checkpoint-safe records.
mod capability_kind_map_serde {
    use super::{CapabilityContractRef, CapabilityKind};
    use serde::{Deserialize, Deserializer, Serialize, Serializer};
    use std::collections::BTreeMap;

    /// Serializes each exact contract/category pair as a typed record.
    pub fn serialize<S: Serializer>(
        values: &BTreeMap<CapabilityContractRef, CapabilityKind>,
        serializer: S,
    ) -> Result<S::Ok, S::Error> {
        values.iter().collect::<Vec<_>>().serialize(serializer)
    }

    /// Restores category facts and rejects duplicate contract identities.
    pub fn deserialize<'de, D: Deserializer<'de>>(
        deserializer: D,
    ) -> Result<BTreeMap<CapabilityContractRef, CapabilityKind>, D::Error> {
        let entries: Vec<(CapabilityContractRef, CapabilityKind)> = Vec::deserialize(deserializer)?;
        let mut values = BTreeMap::new();
        for (contract, kind) in entries {
            if values.insert(contract, kind).is_some() {
                return Err(serde::de::Error::custom(
                    "duplicate capability kind contract",
                ));
            }
        }
        Ok(values)
    }
}

/// Encodes resource owner mappings as checkpoint-safe records.
mod resource_owner_map_serde {
    use super::{LocalSystemId, ResourceId};
    use serde::{Deserialize, Deserializer, Serialize, Serializer};
    use std::collections::BTreeMap;

    /// Serializes each resource owner mapping as a typed two-element record.
    pub fn serialize<S: Serializer>(
        values: &BTreeMap<ResourceId, LocalSystemId>,
        serializer: S,
    ) -> Result<S::Ok, S::Error> {
        values.iter().collect::<Vec<_>>().serialize(serializer)
    }

    /// Restores resource owner mappings and rejects duplicate resource identities.
    pub fn deserialize<'de, D: Deserializer<'de>>(
        deserializer: D,
    ) -> Result<BTreeMap<ResourceId, LocalSystemId>, D::Error> {
        let entries: Vec<(ResourceId, LocalSystemId)> = Vec::deserialize(deserializer)?;
        let mut values = BTreeMap::new();
        for (resource, owner) in entries {
            if values.insert(resource, owner).is_some() {
                return Err(serde::de::Error::custom(
                    "duplicate resource owner resource",
                ));
            }
        }
        Ok(values)
    }
}

impl NodeRegistration {
    /// Creates a node registration used by matching and adapter negotiation.
    pub fn new(
        node_id: NodeId,
        local_runtime: LocalRuntime,
        contract_version: NodeContractVersion,
        capabilities: Vec<Capability>,
        resources: Vec<Resource>,
    ) -> Self {
        Self::new_with_contracts(
            node_id,
            local_runtime,
            contract_version,
            capabilities,
            Vec::new(),
            resources,
        )
    }

    /// Creates a registration with coarse capabilities and executable canonical contracts.
    pub fn new_with_contracts(
        node_id: NodeId,
        local_runtime: LocalRuntime,
        contract_version: NodeContractVersion,
        capabilities: Vec<Capability>,
        supported_contracts: Vec<CapabilityContractRef>,
        resources: Vec<Resource>,
    ) -> Self {
        let exact_kind = (capabilities.len() == 1).then(|| capabilities[0].kind());
        Self {
            node_id,
            local_systems: vec![LocalSystemDescriptor::new(
                LocalSystemId("default".to_string()),
                local_runtime,
                BTreeMap::new(),
            )],
            contract_version,
            capabilities,
            capability_owners: supported_contracts
                .iter()
                .cloned()
                .map(|contract| (contract, LocalSystemId("default".to_string())))
                .collect(),
            capability_readiness: supported_contracts
                .iter()
                .cloned()
                .map(|contract| (contract, true))
                .collect(),
            capability_kinds: exact_kind
                .map(|kind| {
                    supported_contracts
                        .iter()
                        .cloned()
                        .map(|contract| (contract, kind))
                        .collect()
                })
                .unwrap_or_default(),
            supported_contracts,
            sensors: Vec::new(),
            resource_owners: resources
                .iter()
                .map(|resource| (resource.id().clone(), LocalSystemId("default".to_string())))
                .collect(),
            resources,
            state_exports: Vec::new(),
            memory_providers: Vec::new(),
        }
    }

    /// Creates a v0.2 aggregate registration with explicit local ownership.
    #[allow(clippy::too_many_arguments)]
    pub fn new_with_local_systems(
        node_id: NodeId,
        local_systems: Vec<LocalSystemDescriptor>,
        contract_version: NodeContractVersion,
        capabilities: Vec<Capability>,
        capability_owners: BTreeMap<CapabilityContractRef, LocalSystemId>,
        sensors: Vec<SensorDescriptor>,
        resources: Vec<Resource>,
        resource_owners: BTreeMap<ResourceId, LocalSystemId>,
    ) -> Result<Self, DomainError> {
        let capability_readiness = capability_owners
            .keys()
            .cloned()
            .map(|contract| (contract, true))
            .collect();
        let capability_kinds = (capabilities.len() == 1)
            .then(|| capabilities[0].kind())
            .map(|kind| {
                capability_owners
                    .keys()
                    .cloned()
                    .map(|contract| (contract, kind))
                    .collect()
            })
            .unwrap_or_default();
        Self::new_with_local_systems_and_readiness(
            node_id,
            local_systems,
            contract_version,
            capabilities,
            capability_owners,
            capability_kinds,
            capability_readiness,
            sensors,
            resources,
            resource_owners,
        )
    }

    /// Creates an aggregate registration with explicit ownership and exact readiness facts.
    #[allow(clippy::too_many_arguments)]
    pub fn new_with_local_systems_and_readiness(
        node_id: NodeId,
        local_systems: Vec<LocalSystemDescriptor>,
        contract_version: NodeContractVersion,
        capabilities: Vec<Capability>,
        capability_owners: BTreeMap<CapabilityContractRef, LocalSystemId>,
        capability_kinds: BTreeMap<CapabilityContractRef, CapabilityKind>,
        capability_readiness: BTreeMap<CapabilityContractRef, bool>,
        sensors: Vec<SensorDescriptor>,
        resources: Vec<Resource>,
        resource_owners: BTreeMap<ResourceId, LocalSystemId>,
    ) -> Result<Self, DomainError> {
        let owners = local_systems
            .iter()
            .map(LocalSystemDescriptor::id)
            .collect::<BTreeSet<_>>();
        let sensor_ids = sensors
            .iter()
            .map(SensorDescriptor::id)
            .collect::<BTreeSet<_>>();
        let resource_ids = resources.iter().map(Resource::id).collect::<BTreeSet<_>>();
        let advertised_capability_kinds = capabilities
            .iter()
            .map(Capability::kind)
            .collect::<BTreeSet<_>>();
        if local_systems.is_empty() || owners.len() != local_systems.len() {
            return Err(DomainError::InvalidMissionPlan {
                reason: "node local systems must be nonempty and unique".to_string(),
            });
        }
        if capability_readiness.keys().collect::<BTreeSet<_>>()
            != capability_owners.keys().collect::<BTreeSet<_>>()
            || (!capability_kinds.is_empty()
                && capability_kinds.keys().collect::<BTreeSet<_>>()
                    != capability_owners.keys().collect::<BTreeSet<_>>())
        {
            return Err(DomainError::InvalidMissionPlan {
                reason: "capability readiness and any supplied capability-kind map must cover every configured contract exactly"
                    .to_string(),
            });
        }
        if capability_owners
            .values()
            .any(|owner| !owners.contains(owner))
            || capability_kinds
                .values()
                .any(|kind| !advertised_capability_kinds.contains(kind))
            || sensors
                .iter()
                .any(|sensor| !owners.contains(sensor.local_system_id()))
            || sensor_ids.len() != sensors.len()
            || resource_owners
                .values()
                .any(|owner| !owners.contains(owner))
            || resource_owners.len() != resources.len()
            || resources
                .iter()
                .any(|resource| !resource_owners.contains_key(resource.id()))
            || resource_owners
                .keys()
                .any(|resource_id| !resource_ids.contains(resource_id))
        {
            return Err(DomainError::InvalidMissionPlan {
                reason: "node declaration references an unknown local system owner".to_string(),
            });
        }
        let supported_contracts = capability_owners.keys().cloned().collect();
        Ok(Self {
            node_id,
            local_systems,
            contract_version,
            capabilities,
            supported_contracts,
            capability_owners,
            capability_kinds,
            capability_readiness,
            sensors,
            resources,
            resource_owners,
            state_exports: Vec::new(),
            memory_providers: Vec::new(),
        })
    }

    /// Adds validated selective State and Memory declarations to an aggregate registration.
    ///
    /// Existing constructors deliberately produce empty declarations so legacy checkpoints and
    /// in-process callers retain their prior behavior until a v0.3 adapter opts in.
    pub fn with_state_memory_exports(
        mut self,
        state_exports: Vec<StateExportDescriptor>,
        memory_providers: Vec<MemoryProviderDescriptor>,
    ) -> Result<Self, DomainError> {
        let owners = self
            .local_systems
            .iter()
            .map(LocalSystemDescriptor::id)
            .collect::<BTreeSet<_>>();
        let export_ids = state_exports
            .iter()
            .map(StateExportDescriptor::export_id)
            .collect::<BTreeSet<_>>();
        let provider_ids = memory_providers
            .iter()
            .map(MemoryProviderDescriptor::provider_id)
            .collect::<BTreeSet<_>>();
        if export_ids.len() != state_exports.len() {
            return Err(DomainError::InvalidState {
                reason: "node state export identities must be unique".to_string(),
            });
        }
        if provider_ids.len() != memory_providers.len() {
            return Err(DomainError::InvalidMemory {
                reason: "node memory provider identities must be unique".to_string(),
            });
        }
        if state_exports
            .iter()
            .any(|descriptor| !owners.contains(descriptor.local_system_id()))
        {
            return Err(DomainError::InvalidState {
                reason: "node state export references an unknown local system owner".to_string(),
            });
        }
        if memory_providers
            .iter()
            .any(|descriptor| !owners.contains(descriptor.local_system_id()))
        {
            return Err(DomainError::InvalidMemory {
                reason: "node memory provider references an unknown local system owner".to_string(),
            });
        }
        self.state_exports = state_exports;
        self.memory_providers = memory_providers;
        Ok(self)
    }

    /// Returns the logical node identity.
    pub fn node_id(&self) -> &NodeId {
        &self.node_id
    }

    /// Returns the first runtime for legacy single-runtime readers.
    ///
    /// Aggregate-aware consumers use [`Self::local_systems`] instead.
    pub fn local_runtime(&self) -> &LocalRuntime {
        self.local_systems[0].runtime()
    }

    /// Returns all configured local systems in stable declaration order.
    pub fn local_systems(&self) -> &[LocalSystemDescriptor] {
        &self.local_systems
    }

    /// Returns the semantic Node Contract version exposed by the integration boundary.
    pub const fn contract_version(&self) -> &NodeContractVersion {
        &self.contract_version
    }

    /// Returns the node's advertised capabilities.
    pub fn capabilities(&self) -> &[Capability] {
        &self.capabilities
    }

    /// Returns canonical capability contracts exposed by this node.
    pub fn supported_contracts(&self) -> &[CapabilityContractRef] {
        &self.supported_contracts
    }

    /// Returns the configured owner of one canonical capability contract.
    pub fn capability_owner(&self, contract: &CapabilityContractRef) -> Option<&LocalSystemId> {
        self.capability_owners.get(contract)
    }

    /// Returns whether one configured canonical contract is currently ready to execute.
    ///
    /// A missing fact can only come from a legacy checkpoint, whose former static-ready
    /// semantics remain in force until a complete registration observation replaces it.
    pub fn contract_is_available(&self, contract: &CapabilityContractRef) -> bool {
        self.capability_owners.contains_key(contract)
            && self
                .capability_readiness
                .get(contract)
                .copied()
                .unwrap_or(true)
    }

    /// Returns whether an exact canonical contract is ready under the requested capability kind.
    pub fn contract_is_available_for_kind(
        &self,
        contract: &CapabilityContractRef,
        kind: CapabilityKind,
    ) -> bool {
        self.contract_is_available(contract)
            && self
                .capability_kinds
                .get(contract)
                .copied()
                .or_else(|| self.inferred_legacy_capability_kind())
                == Some(kind)
    }

    /// Infers a missing legacy contract category only when every coarse declaration agrees.
    fn inferred_legacy_capability_kind(&self) -> Option<CapabilityKind> {
        let mut kinds = self.capabilities.iter().map(Capability::kind);
        let first = kinds.next()?;
        kinds.all(|kind| kind == first).then_some(first)
    }

    /// Returns exact readiness facts in deterministic contract order.
    pub const fn capability_readiness(&self) -> &BTreeMap<CapabilityContractRef, bool> {
        &self.capability_readiness
    }

    /// Returns the selective State channels declared by this node.
    pub fn state_exports(&self) -> &[StateExportDescriptor] {
        &self.state_exports
    }

    /// Returns the selective Memory providers declared by this node.
    pub fn memory_providers(&self) -> &[MemoryProviderDescriptor] {
        &self.memory_providers
    }

    /// Returns all node sensors in stable declaration order.
    pub fn sensors(&self) -> &[SensorDescriptor] {
        &self.sensors
    }

    /// Returns the node's advertised resources.
    pub fn resources(&self) -> &[Resource] {
        &self.resources
    }

    /// Returns the configured local-system owner of one resource.
    pub fn resource_owner(&self, resource_id: &ResourceId) -> Option<&LocalSystemId> {
        self.resource_owners.get(resource_id)
    }

    /// Checks whether this registration can satisfy one role requirement.
    pub fn supports_role(&self, requirement: &RoleRequirement) -> bool {
        let has_capability = self.capabilities.iter().any(|capability| {
            capability.kind() == requirement.capability() && capability.is_available()
        });
        let has_contract = requirement.required_contract().is_none_or(|contract| {
            self.contract_is_available_for_kind(contract, requirement.capability())
        });
        let has_resource = requirement.resource_kind().is_none_or(|kind| {
            self.resources
                .iter()
                .any(|resource| resource.kind() == kind && resource.capacity() > 0)
        });
        has_capability && has_contract && has_resource
    }

    /// Returns all resource identifiers of the requested category.
    pub fn resource_ids_of_kind(&self, kind: ResourceKind) -> Vec<ResourceId> {
        self.resources
            .iter()
            .filter(|resource| resource.kind() == kind)
            .map(|resource| resource.id().clone())
            .collect()
    }

    /// Checks whether a resource belongs to this node and has the requested kind.
    pub fn owns_resource(&self, resource_id: &ResourceId, kind: Option<ResourceKind>) -> bool {
        self.resources.iter().any(|resource| {
            resource.id() == resource_id && kind.is_none_or(|expected| resource.kind() == expected)
        })
    }
}

/// The latest shared registration, reported health, and liveness facts for one node.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct NodeStateSnapshot {
    /// Local runtime, capability, and resource facts advertised by the node.
    registration: NodeRegistration,
    /// Latest accepted health explicitly reported by the local EAIOS.
    reported_status: NodeStatus,
    /// RoboGuide-local receive time of the latest reported health.
    reported_status_received_at: TimestampMs,
    /// Latest reachability fact observed by RoboGuide.
    liveness: NodeLivenessObservation,
}

impl NodeStateSnapshot {
    /// Creates a shared node snapshot from transport-neutral domain facts.
    pub const fn new(
        registration: NodeRegistration,
        reported_status: NodeStatus,
        reported_status_received_at: TimestampMs,
        liveness: NodeLivenessObservation,
    ) -> Self {
        Self {
            registration,
            reported_status,
            reported_status_received_at,
            liveness,
        }
    }

    /// Returns the node identity represented by this snapshot.
    pub fn node_id(&self) -> &NodeId {
        self.registration.node_id()
    }

    /// Returns the node's latest advertised runtime, capabilities, and resources.
    pub const fn registration(&self) -> &NodeRegistration {
        &self.registration
    }

    /// Returns the latest health explicitly reported by the local EAIOS.
    pub const fn reported_status(&self) -> NodeStatus {
        self.reported_status
    }

    /// Returns when RoboGuide received the latest reported health.
    pub const fn reported_status_received_at(&self) -> TimestampMs {
        self.reported_status_received_at
    }

    /// Returns the latest system-observed liveness fact.
    pub const fn liveness(&self) -> NodeLivenessObservation {
        self.liveness
    }
}

/// The result reported by a local node after receiving an execution command.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum NodeEvent {
    /// The local node completed the assigned role.
    TaskCompleted {
        /// Node that executed the role.
        node_id: NodeId,
        /// Mission-scoped task that was executed.
        task_ref: TaskRef,
        /// Execution group containing the role.
        group_id: ExecutionGroupId,
        /// Role that completed.
        role_id: RoleId,
    },
    /// The local node rejected or failed the assigned role.
    TaskFailed {
        /// Node that attempted the role.
        node_id: NodeId,
        /// Mission-scoped task that failed.
        task_ref: TaskRef,
        /// Execution group containing the role.
        group_id: ExecutionGroupId,
        /// Role that failed.
        role_id: RoleId,
        /// Stable human-readable failure reason.
        reason: String,
    },
    /// The local node entered a safety stop.
    SafeStopped {
        /// Node that stopped.
        node_id: NodeId,
        /// Reason reported by local safety.
        reason: String,
    },
}

/// A command sent through the runtime to a local node.
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct ExecutionCommand {
    /// Mission-scoped task whose role is being invoked.
    task_ref: TaskRef,
    /// Execution group that owns the role lifecycle.
    group_id: ExecutionGroupId,
    /// Role being invoked on the node.
    role_id: RoleId,
    /// Node that receives the command.
    node_id: NodeId,
    /// Canonical operation and parameters requested from the local EAIOS.
    intent: ExecutionIntent,
    /// Correlation identity for the command and its observations.
    correlation_id: CorrelationId,
}

impl ExecutionCommand {
    /// Creates a role-scoped command without exposing transport details.
    pub const fn new(
        mission_id: MissionId,
        task_id: TaskId,
        group_id: ExecutionGroupId,
        role_id: RoleId,
        node_id: NodeId,
        intent: ExecutionIntent,
        correlation_id: CorrelationId,
    ) -> Self {
        Self {
            task_ref: TaskRef::new(mission_id, task_id),
            group_id,
            role_id,
            node_id,
            intent,
            correlation_id,
        }
    }

    /// Returns the complete mission-scoped task identity.
    pub const fn task_ref(&self) -> &TaskRef {
        &self.task_ref
    }

    /// Returns the mission targeted by this command.
    pub const fn mission_id(&self) -> &MissionId {
        self.task_ref.mission_id()
    }

    /// Returns the task targeted by this command.
    pub const fn task_id(&self) -> &TaskId {
        self.task_ref.task_id()
    }

    /// Returns the execution group targeted by this command.
    pub fn group_id(&self) -> &ExecutionGroupId {
        &self.group_id
    }

    /// Returns the role targeted by this command.
    pub fn role_id(&self) -> &RoleId {
        &self.role_id
    }

    /// Returns the node receiving this command.
    pub fn node_id(&self) -> &NodeId {
        &self.node_id
    }

    /// Returns the canonical capability contract request for the local EAIOS adapter.
    pub const fn intent(&self) -> &ExecutionIntent {
        &self.intent
    }

    /// Returns the operation correlation identity.
    pub fn correlation_id(&self) -> &CorrelationId {
        &self.correlation_id
    }
}

/// A serializable-in-spirit event payload before a transport is selected.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum EventPayload {
    /// A source-aware State record was durably accepted for projection.
    StateRecordObserved {
        /// Bounded record retaining source, semantic, receive time, and freshness.
        record: StateRecord,
    },
    /// A generic immutable Memory manifest became discoverable.
    MemoryManifestPublished {
        /// Immutable metadata; content bytes remain in the Artifact data plane.
        manifest: MemoryArtifactManifest,
    },
    /// A node staged one generic Memory artifact after digest verification.
    MemoryArtifactStaged {
        /// Immutable Memory revision staged by the node.
        manifest: MemoryArtifactManifest,
        /// Node that owns the local staging cache.
        node_id: NodeId,
        /// Exact node-local provider receiving the staged revision.
        #[serde(default = "legacy_memory_consumer_provider_id")]
        consumer_provider_id: String,
    },
    /// A node imported one generic Memory artifact into a local heterogeneous store.
    MemoryArtifactImported {
        /// Immutable Memory revision imported by the node.
        manifest: MemoryArtifactManifest,
        /// Node that owns the local imported representation.
        node_id: NodeId,
        /// Exact node-local provider owning the imported representation.
        #[serde(default = "legacy_memory_consumer_provider_id")]
        consumer_provider_id: String,
    },
    /// A node rejected generic Memory staging or import.
    MemoryArtifactRejected {
        /// Immutable Memory revision involved in the failed exchange.
        manifest: MemoryArtifactManifest,
        /// Node that rejected the operation.
        node_id: NodeId,
        /// Exact node-local provider that rejected the operation.
        #[serde(default = "legacy_memory_consumer_provider_id")]
        consumer_provider_id: String,
        /// Stable diagnostic retained as evidence.
        reason: String,
    },
    /// A map revision manifest was declared before its bytes were published.
    MapArtifactDeclared {
        /// Immutable map manifest retained by the catalog.
        manifest: MapArtifactManifest,
    },
    /// An immutable map artifact became available from the central artifact store.
    MapArtifactPublished {
        /// Immutable map manifest retained by the catalog.
        manifest: MapArtifactManifest,
    },
    /// A node began staging an immutable map artifact locally.
    MapArtifactStaged {
        /// Immutable map manifest being staged.
        manifest: MapArtifactManifest,
        /// Node that is staging the artifact.
        node_id: NodeId,
        /// Mission that requested the staging operation.
        mission_id: MissionId,
    },
    /// A node imported an immutable map artifact into its local cache.
    MapArtifactImported {
        /// Immutable map manifest imported by the node.
        manifest: MapArtifactManifest,
        /// Node that imported the artifact.
        node_id: NodeId,
        /// Mission that requested the import operation.
        mission_id: MissionId,
    },
    /// A node verified an imported artifact and its declared spatial metadata.
    MapLocalizationVerified {
        /// Resolved immutable artifact reference that was verified.
        artifact: MapArtifactRef,
        /// Node that performed the verification.
        node_id: NodeId,
        /// Mission that requested the verification operation.
        mission_id: MissionId,
        /// Anchor used by the localization check.
        anchor_id: SpatialAnchorId,
    },
    /// A node produced complete strong localization verification evidence.
    MapLocalizationEvidenceRecorded {
        /// Canonical evidence bound to artifact and execution identity.
        evidence: LocalizationVerificationEvidence,
    },
    /// A node rejected an artifact or could not verify its spatial metadata.
    MapArtifactRejected {
        /// Resolved immutable artifact reference that was rejected.
        artifact: MapArtifactRef,
        /// Node that rejected the artifact.
        node_id: NodeId,
        /// Mission that requested the import or verification operation.
        mission_id: MissionId,
        /// Stable diagnostic retained as evidence.
        reason: String,
    },
    /// A node registration became visible to control.
    NodeRegistered {
        /// Registered node identity.
        node_id: NodeId,
        /// Lease issued to the registered node.
        lease_id: LeaseId,
    },
    /// A node heartbeat was accepted and its lease renewed.
    NodeHeartbeatAccepted {
        /// Node whose heartbeat was accepted.
        node_id: NodeId,
        /// Lease renewed by the heartbeat.
        lease_id: LeaseId,
    },
    /// A node lease expired and the node became unschedulable.
    NodeLeaseExpired {
        /// Node whose lease expired.
        node_id: NodeId,
        /// Expired lease identity.
        lease_id: LeaseId,
    },
    /// Matching produced role candidates.
    CandidatesMatched {
        /// Mission-scoped task for which candidates were produced.
        task_ref: TaskRef,
    },
    /// The deterministic bootstrap Scheduler selected all normal task assignments.
    TaskSchedulingSelected {
        /// Mission-scoped task represented by the selection decision.
        task_ref: TaskRef,
        /// Selected role, node, and proposed resource mappings.
        assignments: Vec<RoleAssignment>,
    },
    /// A scheduler proposal was accepted for validation.
    ProposalCreated {
        /// Mission-scoped task represented by the proposal.
        task_ref: TaskRef,
    },
    /// Resource coordination committed a proposal.
    PlanCommitted {
        /// Mission-scoped task represented by the committed plan.
        task_ref: TaskRef,
    },
    /// An execution group was created and bound.
    ExecutionGroupBound {
        /// Group identity.
        group_id: ExecutionGroupId,
        /// Mission-scoped task assigned to the group.
        task_ref: TaskRef,
    },
    /// A Mission-level Execution Group was created for long-lived multi-Task execution.
    ExecutionGroupCreated {
        /// Group identity.
        group_id: ExecutionGroupId,
        /// Mission owning the Group.
        mission_id: MissionId,
    },
    /// A Task became an execution unit inside an existing Mission-level Group.
    TaskExecutionRegistered {
        /// Group hosting the Task execution.
        group_id: ExecutionGroupId,
        /// Mission-scoped Task identity.
        task_ref: TaskRef,
        /// Mission Intelligence context referenced by the Task.
        context_id: CoordinationContextId,
    },
    /// A registered Task became eligible after its DAG dependencies were satisfied.
    TaskExecutionReady {
        /// Group hosting the Task execution.
        group_id: ExecutionGroupId,
        /// Task that became ready.
        task_ref: TaskRef,
    },
    /// A Task execution became active inside its existing Group.
    TaskExecutionActivated {
        /// Group hosting the Task execution.
        group_id: ExecutionGroupId,
        /// Task that became active.
        task_ref: TaskRef,
    },
    /// A Task execution completed without closing its parent Group.
    TaskExecutionCompleted {
        /// Group retaining the Mission execution context.
        group_id: ExecutionGroupId,
        /// Task that completed.
        task_ref: TaskRef,
    },
    /// A Task execution reached an unrecoverable failure state.
    TaskExecutionFailed {
        /// Group hosting the failed Task.
        group_id: ExecutionGroupId,
        /// Task that failed.
        task_ref: TaskRef,
    },
    /// Temporary Task bindings were released while the parent Group remained alive.
    TaskExecutionBindingsReleased {
        /// Group retaining unaffected members and Context bindings.
        group_id: ExecutionGroupId,
        /// Task whose temporary bindings were released.
        task_ref: TaskRef,
        /// Resources released for this Task only.
        resource_ids: Vec<ResourceId>,
    },
    /// Context-scoped bindings were released when a Mission Intelligence Context ended.
    ContextBindingsReleased {
        /// Group retaining the Mission execution context.
        group_id: ExecutionGroupId,
        /// Context whose continuous resources ended.
        context_id: CoordinationContextId,
        /// Resources released for the Context.
        resource_ids: Vec<ResourceId>,
    },
    /// A mission actor became authoritative only after its task Group was bound.
    MissionActorBound {
        /// Mission namespace for the actor binding.
        mission_id: MissionId,
        /// Logical actor that gained continuity authority.
        actor_id: ActorId,
        /// Concrete node selected for the actor.
        node_id: NodeId,
        /// Task whose successful Group bind established the authority.
        task_ref: TaskRef,
        /// Group bind that established the authority.
        group_id: ExecutionGroupId,
    },
    /// An execution group began executing its bound roles.
    ExecutionGroupActivated {
        /// Activated group identity.
        group_id: ExecutionGroupId,
        /// Mission-scoped task executed by the group.
        task_ref: TaskRef,
    },
    /// Reconciliation detected one assigned role whose node is no longer eligible.
    ReconciliationRoleRecoveryRequired {
        /// Active group containing the unavailable assignment.
        group_id: ExecutionGroupId,
        /// Mission-scoped task that owns the group.
        task_ref: TaskRef,
        /// Role whose current assignment requires recovery.
        role_id: RoleId,
        /// Currently assigned node that became unavailable.
        node_id: NodeId,
    },
    /// Role-scoped matching produced currently eligible recovery candidates.
    RecoveryCandidatesMatched {
        /// Blocked Group waiting for a replacement.
        group_id: ExecutionGroupId,
        /// Mission-scoped task retained by the Group.
        task_ref: TaskRef,
        /// Unbound role being rematched.
        role_id: RoleId,
        /// Eligible candidates in deterministic node order.
        candidate_node_ids: Vec<NodeId>,
    },
    /// The deterministic bootstrap Scheduler selected one recovery replacement.
    RecoverySchedulingSelected {
        /// Blocked Group awaiting the selected replacement.
        group_id: ExecutionGroupId,
        /// Mission-scoped task retained by the Group.
        task_ref: TaskRef,
        /// Unbound role being scheduled.
        role_id: RoleId,
        /// Failed node excluded from selection.
        previous_node_id: NodeId,
        /// Scheduler-selected replacement node.
        replacement_node_id: NodeId,
        /// Deterministically proposed resource IDs.
        resource_ids: Vec<ResourceId>,
    },
    /// Recovery scheduling found no feasible candidate in the supplied Candidate Set.
    RecoverySchedulingNoSelection {
        /// Blocked Group that remains pending.
        group_id: ExecutionGroupId,
        /// Mission-scoped task retained by the Group.
        task_ref: TaskRef,
        /// Unbound role that remains without selection.
        role_id: RoleId,
    },
    /// An external scheduler choice passed recovery proposal validation.
    RecoveryAssignmentProposed {
        /// Group targeted by the non-committed proposal.
        group_id: ExecutionGroupId,
        /// Mission-scoped task retained by the Group.
        task_ref: TaskRef,
        /// Role proposed for reassignment.
        role_id: RoleId,
        /// Scheduler-selected replacement node.
        replacement_node_id: NodeId,
        /// Proposed resources, not yet reserved.
        resource_ids: Vec<ResourceId>,
    },
    /// Shared Resource Coordination committed one replacement assignment.
    RecoveryAssignmentCommitted {
        /// Existing Group that owns the replacement commitment.
        group_id: ExecutionGroupId,
        /// Mission-scoped task that owns the commitment.
        task_ref: TaskRef,
        /// Role receiving the committed replacement.
        role_id: RoleId,
        /// Replacement node covered by the commitment.
        replacement_node_id: NodeId,
        /// Resources atomically reserved for the existing Group.
        resource_ids: Vec<ResourceId>,
    },
    /// A committed-but-not-bound recovery assignment was explicitly aborted.
    RecoveryAssignmentAborted {
        /// Group that owned the pending commitment.
        group_id: ExecutionGroupId,
        /// Mission-scoped task that owned the pending commitment.
        task_ref: TaskRef,
        /// Unbound role whose replacement attempt was aborted.
        role_id: RoleId,
        /// Replacement node no longer intended for rebind.
        replacement_node_id: NodeId,
        /// Replacement resources released by the abort.
        resource_ids: Vec<ResourceId>,
    },
    /// A node emitted an execution observation.
    NodeObservation(NodeEvent),
    /// Runtime detected an execution whose physical outcome requires reconciliation.
    RuntimeExecutionRecoveryRequired {
        /// Stable cross-session execution identity.
        execution_id: String,
        /// Node that reported or owns the ambiguous execution.
        node_id: NodeId,
        /// Committed Group identity when Runtime knows the execution context.
        group_id: Option<ExecutionGroupId>,
        /// Committed Task identity when Runtime knows the execution context.
        task_ref: Option<TaskRef>,
        /// Committed role identity when Runtime knows the execution context.
        role_id: Option<RoleId>,
        /// Diagnostic explaining why execution cannot safely continue.
        reason: String,
    },
    /// Runtime registered one Mission-owned execution coordination relation.
    ExecutionRelationRegistered {
        /// Mission-level Group containing both logical endpoints.
        group_id: ExecutionGroupId,
        /// Stable relation identity from the accepted MissionPlan.
        relation_id: ExecutionRelationId,
        /// Logical condition-provider Task.
        source_task_ref: TaskRef,
        /// Logical condition-provider Role.
        source_role_id: RoleId,
        /// Logical constrained Task.
        target_task_ref: TaskRef,
        /// Logical constrained Role.
        target_role_id: RoleId,
        /// Closed relation behavior.
        kind: ExecutionRelationKind,
        /// Typed relation contract retained for replay and evidence inspection.
        #[serde(default)]
        relation_type: ExecutionRelationType,
        /// Effective coupling mode of the constrained Task execution scope.
        #[serde(default)]
        coupling_mode: ExecutionCouplingMode,
    },
    /// Runtime execution facts changed the observable state of a relation.
    ExecutionRelationStateChanged {
        /// Mission-level Group containing both logical endpoints.
        group_id: ExecutionGroupId,
        /// Stable relation identity.
        relation_id: ExecutionRelationId,
        /// Previous Runtime-derived state.
        previous: ExecutionRelationState,
        /// New Runtime-derived state.
        current: ExecutionRelationState,
        /// Current source attempt, when dispatched.
        source_execution_id: Option<String>,
        /// Current target attempt, when dispatched.
        target_execution_id: Option<String>,
        /// Typed relation contract retained for replay and evidence inspection.
        #[serde(default)]
        relation_type: ExecutionRelationType,
        /// Effective coupling mode of the constrained Task execution scope.
        #[serde(default)]
        coupling_mode: ExecutionCouplingMode,
    },
    /// A relation violation or ambiguity fenced target progression for reconciliation.
    ExecutionRelationReconciliationRequired {
        /// Mission-level Group containing both logical endpoints.
        group_id: ExecutionGroupId,
        /// Stable relation identity.
        relation_id: ExecutionRelationId,
        /// Violated or unknown Runtime-derived state.
        state: ExecutionRelationState,
        /// Logical condition-provider Task.
        source_task_ref: TaskRef,
        /// Logical condition-provider Role.
        source_role_id: RoleId,
        /// Logical constrained Task.
        target_task_ref: TaskRef,
        /// Logical constrained Role.
        target_role_id: RoleId,
        /// Current source attempt, when dispatched.
        source_execution_id: Option<String>,
        /// Current target attempt, when dispatched.
        target_execution_id: Option<String>,
        /// Stable Runtime diagnostic.
        reason: String,
        /// Typed relation contract retained for replay and evidence inspection.
        #[serde(default)]
        relation_type: ExecutionRelationType,
        /// Effective coupling mode of the constrained Task execution scope.
        #[serde(default)]
        coupling_mode: ExecutionCouplingMode,
    },
    /// One admitted Local EAIOS peer-channel readiness acknowledgement.
    PeerChannelReadinessObserved {
        /// Mission-level Group containing the coordination Context.
        group_id: ExecutionGroupId,
        /// Coordination Context declaring the peer channel.
        context_id: CoordinationContextId,
        /// Logical ContextRole represented by the endpoint.
        context_role_id: ContextRoleId,
        /// Current physical Node carrying the endpoint.
        node_id: NodeId,
        /// Registered Local EAIOS owning the endpoint capability.
        local_system_id: LocalSystemId,
        /// Current Node Protocol session that supplied the fact.
        session_id: String,
        /// Shared channel instance agreed by Local EAIOS peers.
        channel_instance_id: String,
        /// Transport-neutral channel profile confirmed by the endpoint.
        profile_id: String,
        /// Transport-neutral message schema confirmed by the endpoint.
        message_schema: String,
        /// Node-management sequence admitted for this acknowledgement.
        sequence: u64,
        /// RoboGuide-local receive-relative evidence deadline.
        expires_at: TimestampMs,
        /// Whether the Local EAIOS currently confirms readiness.
        ready: bool,
    },
    /// A role was rebound after a recoverable failure.
    RecoveryRebound {
        /// Group being adapted.
        group_id: ExecutionGroupId,
        /// Mission-scoped task whose role was rebound.
        task_ref: TaskRef,
        /// Role being replaced.
        role_id: RoleId,
        /// Previous node.
        from_node: NodeId,
        /// Replacement node.
        to_node: NodeId,
    },
    /// The group completed all assigned roles.
    ExecutionGroupCompleted {
        /// Completed group identity.
        group_id: ExecutionGroupId,
        /// Mission-scoped task completed by the group.
        task_ref: TaskRef,
    },
    /// The current Group configuration cannot progress without reconciliation.
    ExecutionGroupBlocked {
        /// Blocked group identity.
        group_id: ExecutionGroupId,
        /// Mission-scoped task that could not continue.
        task_ref: TaskRef,
        /// Reason for escalation.
        reason: String,
    },
    /// One failed role released only its current member and resource binding.
    ExecutionGroupRoleBindingReleased {
        /// Group retaining its identity and unaffected bindings.
        group_id: ExecutionGroupId,
        /// Mission-scoped task that owns the group.
        task_ref: TaskRef,
        /// Role whose failed binding was released.
        role_id: RoleId,
        /// Node formerly bound to the role.
        node_id: NodeId,
        /// Resource reservations released only for this role.
        resource_ids: Vec<ResourceId>,
    },
    /// Recovery was explicitly exhausted and the group became terminally failed.
    ExecutionGroupFailed {
        /// Failed group identity.
        group_id: ExecutionGroupId,
        /// Mission-scoped task the group could not complete.
        task_ref: TaskRef,
        /// Explicit reason recovery could not continue.
        reason: String,
    },
    /// A terminal group released all current role and resource bindings.
    ExecutionGroupReleased {
        /// Released group identity.
        group_id: ExecutionGroupId,
        /// Mission-scoped task formerly owned by the group.
        task_ref: TaskRef,
        /// Resource reservations released in deterministic assignment order.
        resource_ids: Vec<ResourceId>,
    },
}

/// One immutable event with trace identities.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct EventRecord {
    /// Stable identity of this immutable event.
    event_id: EventId,
    /// Monotonic time at which the event was recorded.
    timestamp: TimestampMs,
    /// Operation trace identity shared by related events.
    correlation_id: CorrelationId,
    /// Immediate preceding event that caused this record, when known.
    causation_id: Option<EventId>,
    /// Domain observation or lifecycle transition represented by the event.
    payload: EventPayload,
}

impl EventRecord {
    /// Creates an immutable event record.
    pub const fn new(
        event_id: EventId,
        timestamp: TimestampMs,
        correlation_id: CorrelationId,
        causation_id: Option<EventId>,
        payload: EventPayload,
    ) -> Self {
        Self {
            event_id,
            timestamp,
            correlation_id,
            causation_id,
            payload,
        }
    }

    /// Returns the event identity.
    pub fn event_id(&self) -> &EventId {
        &self.event_id
    }

    /// Returns the event timestamp.
    pub const fn timestamp(&self) -> TimestampMs {
        self.timestamp
    }

    /// Returns the operation correlation identity.
    pub fn correlation_id(&self) -> &CorrelationId {
        &self.correlation_id
    }

    /// Returns the optional causation event identity.
    pub fn causation_id(&self) -> Option<&EventId> {
        self.causation_id.as_ref()
    }

    /// Returns the immutable event payload.
    pub fn payload(&self) -> &EventPayload {
        &self.payload
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Rejects ambiguous Task role declarations before Control persists role authority.
    #[test]
    fn task_requirement_rejects_duplicate_role_identity() {
        let mission_id =
            MissionId::new("mission-duplicate-role").expect("test mission identity must be valid");
        let task_id = TaskId::new("task-duplicate-role").expect("test task identity must be valid");
        let role_id = RoleId::new("mapper").expect("test role identity must be valid");
        let error = TaskRequirement::new(
            mission_id,
            task_id,
            vec![
                RoleRequirement::new(role_id.clone(), CapabilityKind::Observation, None),
                RoleRequirement::new(role_id, CapabilityKind::Compute, None),
            ],
        )
        .expect_err("duplicate role identities must be rejected");

        assert!(matches!(
            error,
            DomainError::InvalidMissionPlan { reason }
                if reason == "duplicate role id mapper"
        ));
    }

    /// Builds one valid task with no dependencies for graph invariant tests.
    fn task(mission_id: &MissionId, task_id: &str, dependencies: Vec<TaskId>) -> PlannedTask {
        let task_id = TaskId::new(task_id).expect("test task id must be valid");
        let role = RoleRequirement::new(
            RoleId::new(format!("role-{task_id}")).expect("test role id must be valid"),
            CapabilityKind::Transport,
            Some(ResourceKind::Space),
        );
        let requirement = TaskRequirement::new(mission_id.clone(), task_id, vec![role])
            .expect("test requirement must be valid");
        let role_id = requirement.roles()[0].role_id().clone();
        let intent = ExecutionIntent::new(
            CapabilityContractRef::new("mobility", "move", "v1")
                .expect("test operation must be valid"),
            BTreeMap::new(),
        )
        .expect("test intent must be valid");
        PlannedTask::new(
            "transport payload",
            requirement,
            BTreeMap::from([(role_id, intent)]),
            dependencies,
            TaskContinuity::new(
                CoordinationContextId::new("context-test").expect("test context id must be valid"),
                BTreeMap::new(),
                BTreeMap::new(),
            ),
        )
        .expect("test task must be valid")
    }

    /// Acyclic dependencies expose only tasks whose prerequisites have completed.
    #[test]
    fn task_graph_returns_ready_tasks() {
        let mission_id = MissionId::new("mission-ready").expect("test mission id must be valid");
        let first = task(&mission_id, "task-first", vec![]);
        let first_id = first.task_id().clone();
        let second = task(&mission_id, "task-second", vec![first_id.clone()]);
        let graph = TaskGraph::new(mission_id, vec![first, second])
            .expect("acyclic test graph must be valid");

        let initially_ready = graph.ready_tasks(&BTreeSet::new());
        assert_eq!(initially_ready.len(), 1);
        assert_eq!(initially_ready[0].task_id(), &first_id);

        let completed = BTreeSet::from([first_id]);
        let ready_after_first = graph.ready_tasks(&completed);
        assert_eq!(ready_after_first.len(), 1);
        assert_eq!(ready_after_first[0].task_id().as_str(), "task-second");
    }

    /// A cyclic Task Graph is rejected before Control can consume any requirement.
    #[test]
    fn task_graph_rejects_cycle() {
        let mission_id = MissionId::new("mission-cycle").expect("test mission id must be valid");
        let first = task(
            &mission_id,
            "task-first",
            vec![TaskId::new("task-second").expect("test dependency id must be valid")],
        );
        let second = task(
            &mission_id,
            "task-second",
            vec![TaskId::new("task-first").expect("test dependency id must be valid")],
        );

        assert!(matches!(
            TaskGraph::new(mission_id, vec![first, second]),
            Err(DomainError::InvalidMissionPlan { reason }) if reason.contains("cycle")
        ));
    }

    /// A plan cannot combine a goal and Task Graph from different missions.
    #[test]
    fn mission_plan_rejects_identity_mismatch() {
        let goal_mission =
            MissionId::new("mission-goal").expect("test goal mission id must be valid");
        let graph_mission =
            MissionId::new("mission-graph").expect("test graph mission id must be valid");
        let goal = MissionGoal::new(goal_mission, "deliver payload")
            .expect("test mission goal must be valid");
        let graph = TaskGraph::new(
            graph_mission.clone(),
            vec![task(&graph_mission, "task-deliver", vec![])],
        )
        .expect("test task graph must be valid");

        assert!(matches!(
            MissionPlan::new(
                goal,
                graph,
                vec![
                    CoordinationContext::new(
                        CoordinationContextId::new("context-test")
                            .expect("test context id must be valid"),
                        Vec::new(),
                    )
                    .expect("test context must be valid")
                ],
            ),
            Err(DomainError::InvalidMissionPlan { .. })
        ));
    }

    /// Node owner maps remain serializable when a checkpoint contains structured contract keys.
    #[test]
    fn node_registration_round_trips_owner_maps() {
        let node_id = NodeId::new("node-checkpoint").expect("node id must be valid");
        let local_system_id = LocalSystemId::new("mapping").expect("local system id is valid");
        let contract = CapabilityContractRef::new("spatial.map", "build", "v0")
            .expect("contract must be valid");
        let resource_id = ResourceId::new("mapping-compute").expect("resource id is valid");
        let registration = NodeRegistration::new_with_local_systems(
            node_id,
            vec![LocalSystemDescriptor::new(
                local_system_id.clone(),
                LocalRuntime::new("robonix", "0.1").expect("runtime is valid"),
                BTreeMap::new(),
            )],
            NodeContractVersion::v0_2(),
            vec![Capability::new(CapabilityKind::Compute, true)],
            BTreeMap::from([(contract.clone(), local_system_id.clone())]),
            Vec::new(),
            vec![
                Resource::new(resource_id.clone(), ResourceKind::Compute, 1)
                    .expect("resource is valid"),
            ],
            BTreeMap::from([(resource_id.clone(), local_system_id.clone())]),
        )
        .expect("registration is valid");

        let encoded = serde_json::to_string(&registration).expect("registration serializes");
        let decoded: NodeRegistration =
            serde_json::from_str(&encoded).expect("registration deserializes");
        assert_eq!(decoded, registration);
        assert!(encoded.contains("\"capability_owners\":[["));
        assert!(encoded.contains("\"capability_kinds\":[["));
        assert!(encoded.contains("\"capability_readiness\":[["));
        assert!(encoded.contains("\"resource_owners\":[["));

        let mut legacy: serde_json::Value =
            serde_json::from_str(&encoded).expect("registration JSON parses");
        legacy
            .as_object_mut()
            .expect("registration is an object")
            .remove("capability_readiness");
        legacy
            .as_object_mut()
            .expect("registration is an object")
            .remove("capability_kinds");
        let restored: NodeRegistration =
            serde_json::from_value(legacy).expect("legacy registration restores");
        assert!(restored.contract_is_available(&contract));
    }

    /// Legacy aggregate registration fails closed when several kinds prevent exact inference.
    #[test]
    fn legacy_registration_rejects_ambiguous_contract_kinds() {
        let node_id = NodeId::new("node-legacy-mixed").expect("node id must be valid");
        let local_system_id = LocalSystemId::new("mixed").expect("local system id is valid");
        let compute = CapabilityContractRef::new("spatial.map", "build", "v0")
            .expect("compute contract is valid");
        let observation = CapabilityContractRef::new("spatial.map", "observe", "v0")
            .expect("observation contract is valid");
        let registration = NodeRegistration::new_with_local_systems(
            node_id,
            vec![LocalSystemDescriptor::new(
                local_system_id.clone(),
                LocalRuntime::new("mixed-runtime", "0.1").expect("runtime is valid"),
                BTreeMap::new(),
            )],
            NodeContractVersion::v0_2(),
            vec![
                Capability::new(CapabilityKind::Compute, true),
                Capability::new(CapabilityKind::Observation, true),
            ],
            BTreeMap::from([
                (compute.clone(), local_system_id.clone()),
                (observation.clone(), local_system_id),
            ]),
            Vec::new(),
            Vec::new(),
            BTreeMap::new(),
        )
        .expect("legacy mixed registration remains structurally valid");

        assert!(!registration.contract_is_available_for_kind(&compute, CapabilityKind::Compute));
        assert!(
            !registration.contract_is_available_for_kind(&compute, CapabilityKind::Observation)
        );
        assert!(
            !registration.contract_is_available_for_kind(&observation, CapabilityKind::Observation)
        );
        assert!(
            !registration.contract_is_available_for_kind(&observation, CapabilityKind::Compute)
        );
    }
}
