#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! Domain values shared by DEAIOS control, runtime, and node adapters.
//!
//! This crate intentionally contains no transport, serialization, SDK, or
//! simulator dependency. It defines the first internal Node Contract shape.

mod actor;
mod allocation;
mod artifact_authorization;
mod artifact_upload;
pub use artifact_upload::{ArtifactUploadPhase, ArtifactUploadRecord};
mod attestation;
mod belief;
mod capability;
mod capability_requirement;
mod context;
mod decision_policy;
mod decision_registry;
mod duration_estimate;
mod error;
mod event;
mod execution;
mod execution_relation;
mod identity;
mod lease;
mod localization_evidence;
mod memory;
mod memory_authority;
mod memory_preparation;
pub use artifact_authorization::{
    ArtifactAccessRole, ArtifactAuthorizationRegistry, ArtifactCertificateGrant,
};
pub use memory_authority::{
    GroupMembershipVersion, MEMORY_AUTHORIZATION_SCHEMA, MEMORY_AUTHORIZATION_SCHEMA_V0_1,
    MemoryAccessOperation, MemoryAccessPolicy, MemoryAuthorizationRegistry, MemoryConsumerPolicy,
    MemoryGroupPolicy, map_manifest_digest, map_memory_manifest, memory_manifest_digest,
};
pub use memory_preparation::{
    MemoryAvailabilityEvidence, MemoryControlCommand, MemoryControlKind, MemoryControlReceipt,
    MemoryControlStatus, MemoryPreparation, MemoryRequirement, PreparedMemoryInput,
};
mod mission;
mod mission_plan;
mod node_health;
mod node_identity;
#[cfg(test)]
mod node_identity_tests;
mod node_liveness_evidence;
mod node_registration;
mod node_state;
mod operator_authorization;
mod physical_entity;
mod physical_safety;
mod registry_command;
mod resource;
mod role_assignment;
mod spatial_memory;
mod spatial_replica;
mod state_model;
mod task_execution;
mod task_requirement;
mod task_runtime_contract;
mod task_satisfaction;
mod time;
mod verifier_registry;

pub use actor::{ActorBinding, MissionActor, MissionBindingSemantics};
pub use allocation::{
    AllocationOwner, AllocationPhase, AllocationViewSnapshot, ResourceAllocation,
    ResourceBindingScope,
};
pub use attestation::{
    AttestationChallenge, AttestationEvidence, AttestationPolicy, AttestationReport,
};
pub use capability::{Capability, CapabilityKind, LocalRuntime};
pub use capability_requirement::{
    CapabilityConstraint, CapabilityConstraintOperator, CapabilityRequirement,
};
pub use context::{
    ContextRole, CoordinationContext, DistinctPhysicalEntityConstraint, TaskContinuity,
};
pub use decision_policy::{
    ConflictReducer, DECISION_EVIDENCE_POLICY_SCHEMA_V0_1, DecisionEvidencePolicy,
    DecisionEvidencePolicyError, DecisionKind, EvidenceDisposition, EvidenceEvaluation,
};
pub use decision_registry::{
    DecisionEvidenceReceipt, DecisionGate, DecisionGateTarget, DecisionPolicyRegistry,
    StateCondition,
};
pub use duration_estimate::TaskDurationEstimate;
pub use error::DomainError;
pub use event::{EventPayload, EventRecord};
pub use execution::{
    CapabilityContractRef, EXECUTION_RECOVERY_METADATA_KEY, EXECUTION_RECOVERY_PROFILE_SCHEMA,
    ExecutionCommand, ExecutionContinuation, ExecutionIntent, ExecutionRecoveryProfile,
    ExecutionRecoverySupport, ExecutionRegistrationEvidence, ExecutionSessionDescriptor,
    ExecutionSessionSlot, ExecutionStopScope, ExecutionValue, NodeEvent, OperationRecoverySupport,
    OperationRef,
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
    PhysicalEntityId, PhysicalEntityRegistryId, ResourceId, RoleId, SensorId, TaskId, TaskRef,
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
pub use node_identity::{
    IdentityAction, IdentityCommand, IdentityCommandReceipt, IdentityError, IdentityPhase,
    NODE_IDENTITY_REGISTRY_SCHEMA, NodeIdentityRecord, NodeIdentityRegistry,
};
pub use node_liveness_evidence::NodeSessionLivenessEvidence;
pub use operator_authorization::{OperatorAuthorization, OperatorCertificateGrant};
pub use task_runtime_contract::TaskRuntimeContractViolation;
pub use verifier_registry::SemanticVerifierReceipt;
pub use verifier_registry::SemanticVerifierWatch;
pub use verifier_registry::{
    SemanticVerdict, SemanticVerifierEvidence, SemanticVerifierRegistry, SemanticVerifierSource,
    VerifierAttemptRef, VerifierFinality,
};

/// Supplies the conservative identity used only when decoding pre-v7 replica evidence.
fn legacy_memory_consumer_provider_id() -> String {
    LEGACY_MEMORY_CONSUMER_PROVIDER_ID.to_string()
}
pub use node_health::{
    NodeHealth, NodeHealthObservation, NodeHeartbeat, NodeLiveness, NodeLivenessObservation,
    NodeStatus,
};
pub use node_registration::{LocalSystemDescriptor, OperationSupport, SensorDescriptor};
pub use node_state::{NodeRegistration, NodeStateSnapshot};
pub use physical_entity::{
    PhysicalEntityRegistration, PhysicalEntityRegistrySnapshot, PhysicalEntityRoutingProfile,
};
pub use physical_safety::{
    EXECUTION_SAFETY_METADATA_KEY, ExecutionSafetyProfile, OperationSafetySupport,
    PHYSICAL_STOP_CONTRACT_SCHEMA_V0_1, SAFE_STATE_OBSERVATION_SCHEMA, SafetyAssurance,
    SafetyStopError, SafetyStopIdentity, SafetyStopPhase, SafetyStopRecord,
};
pub use resource::{Resource, ResourceKind, ResourceRequirement};
pub use role_assignment::RoleAssignment;
pub use spatial_memory::{
    ContentDigest, MAP_MANIFEST_SCHEMA_V0_1, MapArtifactManifest, MapArtifactRef, MapId,
    MapReplicaStatus, MapRevisionId, MapRevisionSelector, MapRevisionSnapshot, MapRevisionStatus,
    SPATIAL_MEMORY_SCHEMA_V0_1, SPATIAL_MEMORY_SCHEMA_V0_2, SpatialAnchorId,
};
pub use spatial_replica::MapReplicaSnapshot;
pub use state_model::{
    MAX_STATE_PAYLOAD_BYTES, STATE_RECORD_SCHEMA_V0_1, StateExportDescriptor, StateObjectClass,
    StateObjectRef, StateRecord, StateRecordKey, StateSemantic, StateSource,
};
pub use task_execution::{TaskExecution, TaskExecutionLifecycle};
pub use task_requirement::{RoleRequirement, TaskRequirement};
pub use task_satisfaction::{
    TaskSatisfactionBasis, TaskSatisfactionEvidence, VerifierSatisfactionSpec,
};
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

/// Version identifier for Mission Plans declaring Task satisfaction evidence policy.
pub const MISSION_PLAN_SCHEMA_V0_6: &str = "roboguide.mission-plan/v0.6";

/// Version identifier for normalized capability, operation, actor, timing, and satisfaction semantics.
pub const MISSION_PLAN_SCHEMA_V0_7: &str = "roboguide.mission-plan/v0.7";
/// Mission-plan schema that can declare actor binding semantics.
pub const MISSION_PLAN_SCHEMA_V0_8: &str = "roboguide.mission-plan/v0.8";

/// Version with explicit semantic Memory prerequisites and Control-selected receivers.
pub const MISSION_PLAN_SCHEMA_V0_9: &str = "roboguide.mission-plan/v0.9";

/// Version identifier implemented by the first heterogeneous Node Contract.
pub const NODE_CONTRACT_VERSION_V0_1: &str = "roboguide.node.v0.1";

/// Version identifier implemented by the aggregate Local Integration Node Contract.
pub const NODE_CONTRACT_VERSION_V0_2: &str = "roboguide.node.v0.2";

/// Version identifier carrying selective State and Memory provider declarations.
pub const NODE_CONTRACT_VERSION_V0_3: &str = "roboguide.node.v0.3";

/// Version identifier carrying durable command identity and admission receipts.
pub const NODE_CONTRACT_VERSION_V0_4: &str = "roboguide.node.v0.4";

/// Version identifier carrying exact capability profiles and semantic execution intents.
pub const NODE_CONTRACT_VERSION_V0_5: &str = "roboguide.node.v0.5";

/// Version identifier carrying explicit canonical operation-support declarations.
pub const NODE_CONTRACT_VERSION_V0_6: &str = "roboguide.node.v0.6";
/// Node Contract with durable provider-qualified Memory continuity.
pub const NODE_CONTRACT_VERSION_V0_7: &str = "roboguide.node.v0.7";
/// Node Contract with exact typed-map producer and current publication provenance.
pub const NODE_CONTRACT_VERSION_V0_8: &str = "roboguide.node.v0.8";
/// Node Contract with durable pre-execution Memory context and current backend observation.
pub const NODE_CONTRACT_VERSION_V0_9: &str = "roboguide.node.v0.9";

#[cfg(test)]
#[path = "domain_tests.rs"]
mod tests;

pub use belief::{BeliefAlgorithm, BeliefPolicy, BeliefPolicyRegistry, BeliefScalarKind};
pub use registry_command::{RegistryCommandKind, RegistryCommandReceipt};
