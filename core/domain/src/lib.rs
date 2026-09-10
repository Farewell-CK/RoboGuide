#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! Domain values shared by DEAIOS control, runtime, and node adapters.
//!
//! This crate intentionally contains no transport, serialization, SDK, or
//! simulator dependency. It defines the first internal Node Contract shape.

mod actor;
mod allocation;
mod capability;
mod context;
mod error;
mod event;
mod execution;
mod execution_relation;
mod identity;
mod lease;
mod localization_evidence;
mod memory;
mod mission;
mod mission_plan;
mod node_health;
mod node_registration;
mod node_state;
mod resource;
mod role_assignment;
mod spatial_memory;
mod spatial_replica;
mod state_model;
mod task_execution;
mod task_requirement;
mod time;

pub use actor::{ActorBinding, MissionActor};
pub use allocation::{
    AllocationOwner, AllocationPhase, AllocationViewSnapshot, ResourceAllocation,
    ResourceBindingScope,
};
pub use capability::{Capability, CapabilityKind, LocalRuntime};
pub use context::{ContextRole, CoordinationContext, TaskContinuity};
pub use error::DomainError;
pub use event::{EventPayload, EventRecord};
pub use execution::{
    CapabilityContractRef, ExecutionCommand, ExecutionIntent, ExecutionValue, NodeEvent,
};
pub use execution_relation::{
    CoordinationMechanism, ExecutionCouplingMode, ExecutionRelationKind, ExecutionRelationSpec,
    ExecutionRelationState, ExecutionRelationType, FreshnessPolicyRef, GroupSharedViewSpec,
    GroupViewBinding, GroupViewField, PeerChannelSpec, PlannedExecutionRef,
    RelationStateRequirement, SharedSpatialReference,
};
pub use identity::{
    ActorId, ContextRoleId, CoordinationContextId, CorrelationId, EventId, ExecutionGroupId,
    ExecutionRelationId, LeaseId, LocalSystemId, MissionId, NodeContractVersion, NodeId,
    ResourceId, RoleId, SensorId, TaskId, TaskRef,
};
pub use lease::NodeLease;
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
pub use mission::{MissionGoal, MissionPlan, PlannedTask, TaskGraph};

/// Supplies the conservative identity used only when decoding pre-v7 replica evidence.
fn legacy_memory_consumer_provider_id() -> String {
    LEGACY_MEMORY_CONSUMER_PROVIDER_ID.to_string()
}
pub use node_health::{
    NodeHealth, NodeHealthObservation, NodeHeartbeat, NodeLiveness, NodeLivenessObservation,
    NodeStatus,
};
pub use node_registration::{LocalSystemDescriptor, SensorDescriptor};
pub use node_state::{NodeRegistration, NodeStateSnapshot};
pub use resource::{Resource, ResourceKind, ResourceRequirement};
pub use role_assignment::RoleAssignment;
pub use spatial_memory::{
    ContentDigest, MAP_MANIFEST_SCHEMA_V0_1, MapArtifactManifest, MapArtifactRef, MapId,
    MapReplicaStatus, MapRevisionId, MapRevisionSelector, MapRevisionSnapshot, MapRevisionStatus,
    SPATIAL_MEMORY_SCHEMA_V0_1, SpatialAnchorId,
};
pub use spatial_replica::MapReplicaSnapshot;
pub use state_model::{
    MAX_STATE_PAYLOAD_BYTES, STATE_RECORD_SCHEMA_V0_1, StateExportDescriptor, StateObjectClass,
    StateObjectRef, StateRecord, StateRecordKey, StateSemantic, StateSource,
};
pub use task_execution::{TaskExecution, TaskExecutionLifecycle};
pub use task_requirement::{RoleRequirement, TaskRequirement};
pub use time::{TaskTiming, TimestampMs};

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

/// Version identifier for Mission Plans carrying joint resource and temporal requirements.
pub const MISSION_PLAN_SCHEMA_V0_5: &str = "roboguide.mission-plan/v0.5";

/// Version identifier implemented by the first heterogeneous Node Contract.
pub const NODE_CONTRACT_VERSION_V0_1: &str = "roboguide.node.v0.1";

/// Version identifier implemented by the aggregate Local Integration Node Contract.
pub const NODE_CONTRACT_VERSION_V0_2: &str = "roboguide.node.v0.2";

/// Version identifier carrying selective State and Memory provider declarations.
pub const NODE_CONTRACT_VERSION_V0_3: &str = "roboguide.node.v0.3";

/// Version identifier carrying durable command identity and admission receipts.
pub const NODE_CONTRACT_VERSION_V0_4: &str = "roboguide.node.v0.4";

#[cfg(test)]
#[path = "domain_tests.rs"]
mod tests;
