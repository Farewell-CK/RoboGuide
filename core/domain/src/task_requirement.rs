//! Role-level capability, resource, actor, and timing requirements.

use crate::*;
use std::borrow::Cow;
use std::collections::BTreeSet;

/// A role and the capability/resource facts required to perform it.
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct RoleRequirement {
    /// Responsibility identity required by the task.
    role_id: RoleId,
    /// Capability category needed to perform the role.
    #[serde(default, rename = "capability")]
    legacy_capability: Option<CapabilityKind>,
    /// Optional mission actor whose node binding must remain continuous across tasks.
    actor_id: Option<ActorId>,
    /// Exact canonical capability contract required by the role.
    #[serde(default, rename = "contract")]
    legacy_contract: Option<CapabilityContractRef>,
    /// Exact extensible capability requirements evaluated together by Control.
    #[serde(default)]
    capabilities: Vec<CapabilityRequirement>,
    /// Optional resource category that must be bound to the role.
    resource_kind: Option<ResourceKind>,
    /// Quantitative resource requirements evaluated together by Scheduler v0.2.
    #[serde(default)]
    resources: Vec<ResourceRequirement>,
}

impl RoleRequirement {
    /// Creates a role requirement for task matching.
    pub fn new(
        role_id: RoleId,
        capability: CapabilityKind,
        resource_kind: Option<ResourceKind>,
    ) -> Self {
        Self {
            role_id,
            legacy_capability: Some(capability),
            actor_id: None,
            legacy_contract: None,
            capabilities: Vec::new(),
            resource_kind,
            resources: resource_kind
                .map(|kind| vec![ResourceRequirement { kind, units: 1 }])
                .unwrap_or_default(),
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
            legacy_capability: Some(capability),
            actor_id: Some(actor_id),
            legacy_contract: Some(contract.clone()),
            capabilities: vec![CapabilityRequirement::exact(contract)],
            resource_kind,
            resources: resource_kind
                .map(|kind| vec![ResourceRequirement { kind, units: 1 }])
                .unwrap_or_default(),
        }
    }

    /// Creates a Role with multiple quantitative requirements for joint scheduling.
    pub fn new_scheduled(
        role_id: RoleId,
        actor_id: Option<ActorId>,
        capability: CapabilityKind,
        contract: Option<CapabilityContractRef>,
        resources: Vec<ResourceRequirement>,
    ) -> Result<Self, DomainError> {
        let mut kinds = BTreeSet::new();
        if resources
            .iter()
            .any(|resource| !kinds.insert(resource.kind()))
        {
            return Err(DomainError::InvalidMissionPlan {
                reason: format!("role {role_id} has duplicate resource kinds"),
            });
        }
        Ok(Self {
            role_id,
            legacy_capability: Some(capability),
            actor_id,
            legacy_contract: contract.clone(),
            capabilities: contract
                .into_iter()
                .map(CapabilityRequirement::exact)
                .collect(),
            resource_kind: match resources.as_slice() {
                [resource] => Some(resource.kind()),
                _ => None,
            },
            resources,
        })
    }

    /// Creates a normalized Role with one or more extensible capability requirements.
    pub fn new_normalized(
        role_id: RoleId,
        actor_id: Option<ActorId>,
        capabilities: Vec<CapabilityRequirement>,
        resources: Vec<ResourceRequirement>,
    ) -> Result<Self, DomainError> {
        if capabilities.is_empty() {
            return Err(DomainError::EmptyValue {
                kind: "role capability requirements",
            });
        }
        let mut contracts = BTreeSet::new();
        if let Some(duplicate) = capabilities
            .iter()
            .map(CapabilityRequirement::contract)
            .find(|contract| !contracts.insert((*contract).clone()))
        {
            return Err(DomainError::InvalidMissionPlan {
                reason: format!("role {role_id} repeats capability {duplicate}"),
            });
        }
        let mut kinds = BTreeSet::new();
        if resources
            .iter()
            .any(|resource| !kinds.insert(resource.kind()))
        {
            return Err(DomainError::InvalidMissionPlan {
                reason: format!("role {role_id} has duplicate resource kinds"),
            });
        }
        Ok(Self {
            role_id,
            legacy_capability: None,
            actor_id,
            legacy_contract: None,
            capabilities,
            resource_kind: match resources.as_slice() {
                [resource] => Some(resource.kind()),
                _ => None,
            },
            resources,
        })
    }

    /// Returns the role identity.
    pub fn role_id(&self) -> &RoleId {
        &self.role_id
    }

    /// Returns the required capability.
    pub const fn capability(&self) -> Option<CapabilityKind> {
        self.legacy_capability
    }

    /// Returns the mission actor, when this role participates in continuity.
    pub fn actor_id(&self) -> Option<&ActorId> {
        self.actor_id.as_ref()
    }

    /// Returns the exact canonical contract, when declared.
    pub fn required_contract(&self) -> Option<&CapabilityContractRef> {
        self.capabilities
            .first()
            .map(CapabilityRequirement::contract)
            .or(self.legacy_contract.as_ref())
    }

    /// Returns all canonical capability requirements evaluated for this Role.
    pub fn capability_requirements(&self) -> Cow<'_, [CapabilityRequirement]> {
        if self.capabilities.is_empty() {
            match &self.legacy_contract {
                Some(contract) => Cow::Owned(vec![CapabilityRequirement::exact(contract.clone())]),
                None => Cow::Borrowed(&self.capabilities),
            }
        } else {
            Cow::Borrowed(&self.capabilities)
        }
    }

    /// Returns the optional resource category required by this role.
    pub const fn resource_kind(&self) -> Option<ResourceKind> {
        self.resource_kind
    }

    /// Returns all quantitative requirements used by joint scheduling.
    pub fn resource_requirements(&self) -> Cow<'_, [ResourceRequirement]> {
        match (self.resources.is_empty(), self.resource_kind) {
            (true, Some(kind)) => Cow::Owned(vec![ResourceRequirement { kind, units: 1 }]),
            _ => Cow::Borrowed(&self.resources),
        }
    }
}

/// A mission task's role-level execution requirements.
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct TaskRequirement {
    /// Mission-scoped task whose execution requirements are being described.
    task_ref: TaskRef,
    /// Role requirements in the task's declared order.
    roles: Vec<RoleRequirement>,
    /// Relative time constraints used by scheduling and future reservation.
    #[serde(default)]
    timing: TaskTiming,
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
            timing: TaskTiming::default(),
        })
    }

    /// Creates a task requirement with explicit relative time constraints.
    pub fn new_scheduled(
        mission_id: MissionId,
        task_id: TaskId,
        roles: Vec<RoleRequirement>,
        timing: TaskTiming,
    ) -> Result<Self, DomainError> {
        let mut requirement = Self::new(mission_id, task_id, roles)?;
        requirement.timing = timing;
        Ok(requirement)
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

    /// Returns relative time constraints anchored to Mission acceptance.
    pub const fn timing(&self) -> &TaskTiming {
        &self.timing
    }
}
