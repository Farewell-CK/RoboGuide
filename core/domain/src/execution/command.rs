//! Canonical role-scoped command routed to one selected node.

use crate::{
    CorrelationId, ExecutionGroupId, MemoryProviderDescriptor, MissionId, NodeContractVersion,
    NodeId, NodeRegistration, RoleId, TaskId, TaskRef,
};

use super::ExecutionIntent;
use super::ExecutionRecoverySupport;
use super::ExecutionSessionDescriptor;

/// A command sent through the runtime to a local node.
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct ExecutionCommand {
    /// Original dispatch registration evidence; absent history never gains a later provider snapshot.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    registration_evidence: Option<ExecutionRegistrationEvidence>,
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
    /// Immutable accepted-plan topology evidence; absent on legacy commands.
    #[serde(default)]
    session: Option<ExecutionSessionDescriptor>,
    /// Immutable dispatch-time stop/continuation declaration; absent legacy attempts cannot retry.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    recovery_support: Option<ExecutionRecoverySupport>,
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
            registration_evidence: None,
            task_ref: TaskRef::new(mission_id, task_id),
            group_id,
            role_id,
            node_id,
            intent,
            session: None,
            recovery_support: None,
            correlation_id,
        }
    }

    /// Returns the complete mission-scoped task identity.
    pub const fn task_ref(&self) -> &TaskRef {
        &self.task_ref
    }

    /// Attaches composition-supplied original registration evidence outside semantic model parameters.
    pub fn with_registration_evidence(
        mut self,
        evidence: Option<ExecutionRegistrationEvidence>,
    ) -> Self {
        self.registration_evidence = evidence;
        self
    }

    /// Returns dispatch-time declarations without consulting newer registration or renewing evidence.
    pub const fn registration_evidence(&self) -> Option<&ExecutionRegistrationEvidence> {
        self.registration_evidence.as_ref()
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

    /// Attaches accepted-plan topology without changing the semantic execution intent.
    pub fn with_session(mut self, session: ExecutionSessionDescriptor) -> Self {
        self.session = Some(session);
        self
    }

    /// Returns immutable topology evidence retained across durable Runtime replay.
    pub const fn session(&self) -> Option<&ExecutionSessionDescriptor> {
        self.session.as_ref()
    }

    /// Freezes exact implementation support without granting cancellation or repetition authority.
    pub fn with_recovery_support(mut self, support: ExecutionRecoverySupport) -> Self {
        self.recovery_support = Some(support);
        self
    }

    /// Returns original stop/continuation facts; later registration cannot upgrade this attempt.
    pub const fn recovery_support(&self) -> Option<&ExecutionRecoverySupport> {
        self.recovery_support.as_ref()
    }

    /// Returns the operation correlation identity.
    pub fn correlation_id(&self) -> &CorrelationId {
        &self.correlation_id
    }
}

/// Immutable registration claims observed by the Controller before this attempt's dispatch.
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ExecutionRegistrationEvidence {
    /// Original Node identity; it must match the command's retained physical owner.
    node_id: NodeId,
    /// Explicitly negotiated source contract rather than current software version.
    contract_version: NodeContractVersion,
    /// Actual accepted source route at preparation; this history does not renew its lease.
    source_session_id: Option<String>,
    /// Bounded registered provider declarations; bytes and configured Local How remain outside Core.
    memory_providers: Vec<MemoryProviderDescriptor>,
}

impl ExecutionRegistrationEvidence {
    /// Copies actual existing registration evidence without asserting hardware or backend continuity.
    pub fn from_registration(
        registration: &NodeRegistration,
        source_session_id: Option<&str>,
    ) -> Self {
        Self {
            node_id: registration.node_id().clone(),
            contract_version: registration.contract_version().clone(),
            source_session_id: source_session_id.map(str::to_string),
            memory_providers: registration.memory_providers().to_vec(),
        }
    }

    /// Returns the original registered Node owning the provider claims.
    pub const fn node_id(&self) -> &NodeId {
        &self.node_id
    }

    /// Returns the original negotiated protocol contract.
    pub const fn contract_version(&self) -> &NodeContractVersion {
        &self.contract_version
    }

    /// Returns the original accepted session, independently of its current validity.
    pub fn source_session_id(&self) -> Option<&str> {
        self.source_session_id.as_deref()
    }

    /// Returns the original source declarations, never current placement or availability.
    pub fn memory_providers(&self) -> &[MemoryProviderDescriptor] {
        &self.memory_providers
    }
}
