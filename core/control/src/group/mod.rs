//! Execution Group models, lifecycle transitions, and committed Rebind.

use crate::{
    CommittedPlan, CommittedRecoveryAssignment, ControlError, ControlPlane, RecoveryOutcome,
    SchedulingReservationPhase,
};
use domain::{
    ActorId, CoordinationContextId, CorrelationId, EventPayload, ExecutionGroupId, MissionPlan,
    NodeId, ResourceBindingScope, ResourceId, RoleAssignment, RoleId, RoleRequirement,
    TaskExecution, TaskExecutionLifecycle, TaskId, TaskRef, TaskRequirement, TaskSatisfactionBasis,
    TimestampMs,
};
use ports::EventSink;
use std::collections::{BTreeMap, BTreeSet};

mod checkpoint;
mod group_lifecycle;
mod mission_binding;
mod task_lifecycle;

/// Runtime binding retained by a Mission Context independently of any one TaskExecution.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct ContextBinding {
    /// Semantic Context owning the binding lifetime.
    context_id: CoordinationContextId,
    /// Continuous ContextRole represented by the binding.
    context_role_id: domain::ContextRoleId,
    /// Task that first established this committed binding.
    origin_task_ref: TaskRef,
    /// Current real node and resource binding.
    assignment: RoleAssignment,
}

/// Builds the stable JSON-safe key used for one ContextRole binding.
fn context_binding_key(
    context_id: &CoordinationContextId,
    context_role_id: &domain::ContextRoleId,
) -> String {
    format!("{context_id}::{context_role_id}")
}

impl ContextBinding {
    /// Returns the semantic Context owning this binding.
    pub const fn context_id(&self) -> &CoordinationContextId {
        &self.context_id
    }

    /// Returns the continuous ContextRole represented by this binding.
    pub const fn context_role_id(&self) -> &domain::ContextRoleId {
        &self.context_role_id
    }

    /// Returns the Task that first committed this Context binding.
    pub const fn origin_task_ref(&self) -> &TaskRef {
        &self.origin_task_ref
    }

    /// Returns the current real node and resources.
    pub const fn assignment(&self) -> &RoleAssignment {
        &self.assignment
    }
}

/// Lifecycle states for the Mission-level Execution Group.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum GroupLifecycle {
    /// The group exists with committed member/resource bindings.
    Bound,
    /// The bound group is authorized to begin role execution.
    Active,
    /// The group adapted after a recoverable deviation.
    Adapted,
    /// Recovery was explicitly exhausted and the group cannot complete its task.
    Failed,
    /// All assigned roles completed.
    Completed,
    /// The terminal group released all current bindings and reservations.
    Released,
    /// The current execution configuration cannot progress without reconciliation.
    Blocked,
}

/// Context retained when one role binding is released for recovery.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub(crate) struct UnboundRole {
    /// Node that held the failed binding before partial release.
    pub(crate) previous_node_id: NodeId,
    /// Original assignment position restored after successful rebind.
    pub(crate) assignment_index: usize,
}

/// A dynamic group of members, roles, and resource bindings.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct ExecutionGroup {
    /// Dynamic execution-group identity.
    pub(crate) group_id: ExecutionGroupId,
    /// Mission owning the long-lived coordination context.
    pub(crate) mission_id: domain::MissionId,
    /// First Task identity retained only for legacy lifecycle/event compatibility.
    ///
    /// Phase 1 orchestration uses `task_executions` and never treats this as the Group's sole
    /// Task. It remains until pre-Phase-1 compatibility callers are migrated.
    pub(crate) task_ref: TaskRef,
    /// Current role, member, and resource bindings for legacy single-Task flows.
    ///
    /// New Mission execution stores bindings under TaskExecution or ContextBinding.
    pub(crate) assignments: Vec<RoleAssignment>,
    /// Roles awaiting replacement while the Group identity and context remain.
    pub(crate) unbound_roles: BTreeMap<RoleId, UnboundRole>,
    /// Task-local roles awaiting replacement while sibling Tasks remain intact.
    #[serde(with = "task_unbound_serde")]
    pub(crate) task_unbound_roles: BTreeMap<(TaskRef, RoleId), UnboundRole>,
    /// Lifecycle state used by adaptation and recovery.
    pub(crate) lifecycle: GroupLifecycle,
    /// Task execution units retained while the Group remains alive.
    #[serde(with = "task_execution_serde")]
    pub(crate) task_executions: BTreeMap<TaskRef, TaskExecution>,
    /// Immutable MissionPlan role metadata used by Control recovery authority checks.
    #[serde(with = "task_role_requirement_serde")]
    pub(crate) role_requirements: BTreeMap<(TaskRef, RoleId), RoleRequirement>,
    /// Context-scoped bindings retained independently from TaskExecution bindings.
    pub(crate) context_bindings: BTreeMap<String, ContextBinding>,
}

/// Encodes TaskRef-keyed execution units as JSON arrays for cross-process checkpoints.
mod task_execution_serde {
    use super::{TaskExecution, TaskRef};
    use serde::{Deserialize, Deserializer, Serialize, Serializer};
    use std::collections::BTreeMap;

    /// Serializes TaskExecution entries as typed TaskRef/value tuples.
    pub fn serialize<S: Serializer>(
        values: &BTreeMap<TaskRef, TaskExecution>,
        serializer: S,
    ) -> Result<S::Ok, S::Error> {
        values.iter().collect::<Vec<_>>().serialize(serializer)
    }

    /// Restores TaskExecution entries and rejects duplicate TaskRefs.
    pub fn deserialize<'de, D: Deserializer<'de>>(
        deserializer: D,
    ) -> Result<BTreeMap<TaskRef, TaskExecution>, D::Error> {
        let entries: Vec<(TaskRef, TaskExecution)> = Vec::deserialize(deserializer)?;
        let mut values = BTreeMap::new();
        for (task_ref, execution) in entries {
            if values.insert(task_ref, execution).is_some() {
                return Err(serde::de::Error::custom("duplicate TaskExecution key"));
            }
        }
        Ok(values)
    }
}

/// Encodes authoritative Task/Role requirements as duplicate-rejecting JSON records.
mod task_role_requirement_serde {
    use super::{RoleId, RoleRequirement, TaskRef};
    use serde::{Deserialize, Deserializer, Serialize, Serializer};
    use std::collections::BTreeMap;

    /// Serializes composite requirement keys as stable TaskRef/RoleId/value tuples.
    pub fn serialize<S: Serializer>(
        values: &BTreeMap<(TaskRef, RoleId), RoleRequirement>,
        serializer: S,
    ) -> Result<S::Ok, S::Error> {
        values
            .iter()
            .map(|((task_ref, role_id), requirement)| (task_ref, role_id, requirement))
            .collect::<Vec<_>>()
            .serialize(serializer)
    }

    /// Restores authoritative requirements and rejects duplicate Task/Role identities.
    pub fn deserialize<'de, D: Deserializer<'de>>(
        deserializer: D,
    ) -> Result<BTreeMap<(TaskRef, RoleId), RoleRequirement>, D::Error> {
        let entries: Vec<(TaskRef, RoleId, RoleRequirement)> = Vec::deserialize(deserializer)?;
        let mut values = BTreeMap::new();
        for (task_ref, role_id, requirement) in entries {
            if requirement.role_id() != &role_id {
                return Err(serde::de::Error::custom(
                    "Task role requirement key does not match its value",
                ));
            }
            if values.insert((task_ref, role_id), requirement).is_some() {
                return Err(serde::de::Error::custom(
                    "duplicate Task role requirement key",
                ));
            }
        }
        Ok(values)
    }
}

/// Encodes composite Task/Role recovery keys as JSON arrays instead of invalid object keys.
mod task_unbound_serde {
    use super::{RoleId, TaskRef, UnboundRole};
    use serde::{Deserialize, Deserializer, Serialize, Serializer};
    use std::collections::BTreeMap;

    /// Serializes composite recovery keys as a stable list of typed records.
    pub fn serialize<S: Serializer>(
        values: &BTreeMap<(TaskRef, RoleId), UnboundRole>,
        serializer: S,
    ) -> Result<S::Ok, S::Error> {
        values
            .iter()
            .map(|((task_ref, role_id), value)| (task_ref, role_id, value))
            .collect::<Vec<_>>()
            .serialize(serializer)
    }

    /// Restores composite recovery keys and rejects duplicate entries.
    pub fn deserialize<'de, D: Deserializer<'de>>(
        deserializer: D,
    ) -> Result<BTreeMap<(TaskRef, RoleId), UnboundRole>, D::Error> {
        let entries: Vec<(TaskRef, RoleId, UnboundRole)> = Vec::deserialize(deserializer)?;
        let mut values = BTreeMap::new();
        for (task_ref, role_id, value) in entries {
            if values.insert((task_ref, role_id), value).is_some() {
                return Err(serde::de::Error::custom("duplicate Task recovery key"));
            }
        }
        Ok(values)
    }
}

impl ExecutionGroup {
    /// Creates a group from a committed plan.
    #[cfg(test)]
    pub(crate) fn new(group_id: ExecutionGroupId, plan: &CommittedPlan) -> Self {
        Self {
            group_id,
            mission_id: plan.task_ref().mission_id().clone(),
            task_ref: plan.task_ref().clone(),
            assignments: plan.assignments().to_vec(),
            unbound_roles: BTreeMap::new(),
            task_unbound_roles: BTreeMap::new(),
            lifecycle: GroupLifecycle::Bound,
            task_executions: BTreeMap::new(),
            role_requirements: BTreeMap::new(),
            context_bindings: BTreeMap::new(),
        }
    }

    /// Creates a Mission-level Group with its first Task execution unit.
    pub(crate) fn new_mission(
        group_id: ExecutionGroupId,
        mission_id: domain::MissionId,
        initial_task_ref: TaskRef,
    ) -> Self {
        Self {
            group_id,
            mission_id,
            task_ref: initial_task_ref,
            assignments: Vec::new(),
            unbound_roles: BTreeMap::new(),
            task_unbound_roles: BTreeMap::new(),
            lifecycle: GroupLifecycle::Bound,
            task_executions: BTreeMap::new(),
            role_requirements: BTreeMap::new(),
            context_bindings: BTreeMap::new(),
        }
    }

    /// Returns the group identity.
    pub fn group_id(&self) -> &ExecutionGroupId {
        &self.group_id
    }

    /// Returns the Mission owning this long-lived Execution Group.
    pub const fn mission_id(&self) -> &domain::MissionId {
        &self.mission_id
    }

    /// Returns the complete mission-scoped task identity.
    pub const fn task_ref(&self) -> &TaskRef {
        &self.task_ref
    }

    /// Returns the task owned by this group.
    pub const fn task_id(&self) -> &TaskId {
        self.task_ref.task_id()
    }

    /// Returns member-role-resource bindings.
    pub fn assignments(&self) -> &[RoleAssignment] {
        &self.assignments
    }

    /// Returns whether a role is retained by the Group but awaits a new binding.
    pub fn is_role_unbound(&self, role_id: &RoleId) -> bool {
        self.unbound_roles.contains_key(role_id)
    }

    /// Returns whether one Task-local role is awaiting replacement.
    pub fn is_task_role_unbound(&self, task_ref: &TaskRef, role_id: &RoleId) -> bool {
        self.task_unbound_roles
            .contains_key(&(task_ref.clone(), role_id.clone()))
    }

    /// Returns the current group lifecycle.
    pub const fn lifecycle(&self) -> GroupLifecycle {
        self.lifecycle
    }

    /// Returns all Task execution units retained by this Group.
    pub fn task_executions(&self) -> impl Iterator<Item = &TaskExecution> {
        self.task_executions.values()
    }

    /// Returns one Task execution unit retained by this Group.
    pub fn task_execution(&self, task_ref: &TaskRef) -> Option<&TaskExecution> {
        self.task_executions.get(task_ref)
    }

    /// Returns Control's immutable role metadata captured from the accepted MissionPlan.
    pub fn role_requirement(
        &self,
        task_ref: &TaskRef,
        role_id: &RoleId,
    ) -> Option<&RoleRequirement> {
        self.role_requirements
            .get(&(task_ref.clone(), role_id.clone()))
    }

    /// Returns all current Context-scoped bindings in stable Context/Role order.
    pub fn context_bindings(&self) -> impl Iterator<Item = &ContextBinding> {
        self.context_bindings.values()
    }

    /// Returns one ContextRole binding when it is currently retained by the Group.
    pub fn context_binding(
        &self,
        context_id: &CoordinationContextId,
        context_role_id: &domain::ContextRoleId,
    ) -> Option<&ContextBinding> {
        self.context_bindings
            .get(&context_binding_key(context_id, context_role_id))
    }
}

/// Rejects incomplete, duplicate, or unknown role assignments before Group mutation.
fn validate_task_assignments(
    execution: &TaskExecution,
    assignments: &[RoleAssignment],
) -> Result<(), ControlError> {
    let expected = execution
        .role_scopes()
        .keys()
        .collect::<std::collections::BTreeSet<_>>();
    let actual = assignments
        .iter()
        .map(RoleAssignment::role_id)
        .collect::<std::collections::BTreeSet<_>>();
    if expected != actual {
        return Err(ControlError::InvalidProposal(
            "committed assignments must exactly cover TaskExecution roles".to_string(),
        ));
    }
    if assignments.len() != actual.len() {
        return Err(ControlError::InvalidProposal(
            "committed assignments contain duplicate roles".to_string(),
        ));
    }
    Ok(())
}

/// Rejects caller-supplied role metadata that differs from the accepted MissionPlan projection.
fn validate_group_task_requirement(
    group: &ExecutionGroup,
    requirement: &TaskRequirement,
) -> Result<(), ControlError> {
    let expected = group
        .role_requirements
        .iter()
        .filter(|((task_ref, _), _)| task_ref == requirement.task_ref())
        .map(|((_, role_id), role)| (role_id, role))
        .collect::<BTreeMap<_, _>>();
    let supplied = requirement
        .roles()
        .iter()
        .map(|role| (role.role_id(), role))
        .collect::<BTreeMap<_, _>>();
    if expected.is_empty() || expected == supplied {
        return Ok(());
    }
    Err(ControlError::InvalidProposal(
        "Task requirement differs from authoritative Execution Group role metadata".to_string(),
    ))
}

/// Validates authoritative Mission actor continuity before any Task or Group mutation occurs.
fn validate_authoritative_actor_assignments(
    control: &ControlPlane,
    group: &ExecutionGroup,
    plan: &CommittedPlan,
) -> Result<BTreeMap<ActorId, NodeId>, ControlError> {
    let mut actor_nodes = BTreeMap::<ActorId, NodeId>::new();
    let roles = group
        .role_requirements
        .iter()
        .filter(|((task_ref, _), _)| task_ref == plan.task_ref())
        .map(|(_, role)| role);
    for role in roles {
        let Some(actor_id) = role.actor_id() else {
            continue;
        };
        let assignment = plan
            .assignments()
            .iter()
            .find(|assignment| assignment.role_id() == role.role_id())
            .ok_or_else(|| {
                ControlError::InvalidProposal(format!("missing role {}", role.role_id()))
            })?;
        if let Some(existing) = control.actor_binding(plan.task_ref().mission_id(), actor_id)
            && existing.node_id() != assignment.node_id()
        {
            return Err(ControlError::InvalidProposal(
                "mission actor is already bound to another node".to_string(),
            ));
        }
        if let Some(constraint) =
            control.actor_node_constraint(plan.task_ref().mission_id(), actor_id)
            && constraint.node_id() != assignment.node_id()
        {
            return Err(ControlError::InvalidProposal(
                "actor assignment violates deployment placement constraint".to_string(),
            ));
        }
        if let Some(previous) = actor_nodes.insert(actor_id.clone(), assignment.node_id().clone())
            && previous != *assignment.node_id()
        {
            return Err(ControlError::InvalidProposal(
                "one mission actor cannot bind multiple nodes in one Task".to_string(),
            ));
        }
    }
    Ok(actor_nodes)
}

/// A narrow role view used by recovery adapters without exposing the task object.
#[derive(Debug, Clone)]
pub struct RoleRequirementView {
    /// Role requirement exposed to recovery validation.
    requirement: domain::RoleRequirement,
}

impl RoleRequirementView {
    /// Creates a recovery view from a role requirement.
    pub fn new(requirement: domain::RoleRequirement) -> Self {
        Self { requirement }
    }

    /// Returns the role identity.
    pub fn role_id(&self) -> &RoleId {
        self.requirement.role_id()
    }

    /// Returns the wrapped requirement for capability validation.
    pub fn requirement(&self) -> &domain::RoleRequirement {
        &self.requirement
    }
}
