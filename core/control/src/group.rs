//! Execution Group models, lifecycle transitions, and committed Rebind.

use crate::{
    CommittedPlan, CommittedRecoveryAssignment, ControlError, ControlPlane, RecoveryOutcome,
};
use domain::{
    ActorId, CoordinationContextId, CorrelationId, EventPayload, ExecutionGroupId, MissionPlan,
    NodeId, ResourceBindingScope, ResourceId, RoleAssignment, RoleId, RoleRequirement,
    TaskExecution, TaskExecutionLifecycle, TaskId, TaskRef, TaskRequirement, TimestampMs,
};
use ports::EventSink;
use std::collections::BTreeMap;

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
    pub(crate) fn role_requirement(
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

impl ControlPlane {
    /// Validates checkpointed Group role metadata against bindings and recovery authority.
    pub(crate) fn validate_group_checkpoint_authority(&self) -> Result<(), ControlError> {
        for group in self.groups.values() {
            if group.task_ref.mission_id() != &group.mission_id {
                return Err(ControlError::InvalidProposal(
                    "checkpoint Group task belongs to another Mission".to_string(),
                ));
            }
            if !group.task_executions.is_empty() {
                self.validate_mission_group_checkpoint(group)?;
            } else {
                self.validate_legacy_group_checkpoint(group)?;
            }
        }
        for commitment in self.pending_recovery_commitments.values() {
            let group = self.groups.get(commitment.group_id()).ok_or_else(|| {
                ControlError::InvalidProposal(
                    "checkpoint recovery commitment references an unknown Group".to_string(),
                )
            })?;
            let role = group.role_requirement(commitment.task_ref(), commitment.role_id());
            if !group.task_executions.is_empty() && role.is_none() {
                return Err(ControlError::InvalidProposal(
                    "checkpoint recovery commitment lacks authoritative role metadata".to_string(),
                ));
            }
            if let Some(actor_id) = role.and_then(RoleRequirement::actor_id)
                && self
                    .actor_authority_node(commitment.task_ref().mission_id(), actor_id)
                    .is_none_or(|node_id| node_id != commitment.replacement_node_id())
            {
                return Err(ControlError::InvalidProposal(
                    "checkpoint recovery commitment violates Actor authority".to_string(),
                ));
            }
        }
        Ok(())
    }

    /// Validates complete Task/Role coverage and Actor assignments for one Mission-level Group.
    fn validate_mission_group_checkpoint(
        &self,
        group: &ExecutionGroup,
    ) -> Result<(), ControlError> {
        if !group.assignments.is_empty() || !group.unbound_roles.is_empty() {
            return Err(ControlError::InvalidProposal(
                "Mission-level Group checkpoint contains legacy role state".to_string(),
            ));
        }
        for (task_ref, execution) in &group.task_executions {
            if task_ref != execution.task_ref() || task_ref.mission_id() != &group.mission_id {
                return Err(ControlError::InvalidProposal(
                    "checkpoint TaskExecution identity differs from its Group key".to_string(),
                ));
            }
            let expected_roles = execution
                .role_scopes()
                .keys()
                .collect::<std::collections::BTreeSet<_>>();
            if execution
                .context_roles()
                .keys()
                .any(|role_id| !expected_roles.contains(role_id))
            {
                return Err(ControlError::InvalidProposal(
                    "checkpoint TaskExecution continuity references an unknown role".to_string(),
                ));
            }
            let authoritative_roles = group
                .role_requirements
                .iter()
                .filter(|((requirement_task, _), _)| requirement_task == task_ref)
                .map(|((_, role_id), _)| role_id)
                .collect::<std::collections::BTreeSet<_>>();
            if expected_roles != authoritative_roles {
                return Err(ControlError::InvalidProposal(
                    "checkpoint TaskExecution lacks exact authoritative role metadata".to_string(),
                ));
            }
            let mut represented_roles = std::collections::BTreeSet::new();
            for assignment in execution.assignments() {
                if !represented_roles.insert(assignment.role_id())
                    || !authoritative_roles.contains(assignment.role_id())
                {
                    return Err(ControlError::InvalidProposal(
                        "checkpoint TaskExecution contains an unknown or duplicate assignment"
                            .to_string(),
                    ));
                }
                self.validate_checkpoint_actor_assignment(
                    group,
                    task_ref,
                    assignment.role_id(),
                    assignment.node_id(),
                )?;
            }
            for ((unbound_task, role_id), unbound) in &group.task_unbound_roles {
                if unbound_task == task_ref {
                    if !represented_roles.insert(role_id) || !authoritative_roles.contains(role_id)
                    {
                        return Err(ControlError::InvalidProposal(
                            "checkpoint Task recovery contains an unknown or duplicate role"
                                .to_string(),
                        ));
                    }
                    self.validate_checkpoint_actor_assignment(
                        group,
                        task_ref,
                        role_id,
                        &unbound.previous_node_id,
                    )?;
                }
            }
            self.validate_task_checkpoint_coverage(
                group,
                execution,
                &authoritative_roles,
                &represented_roles,
            )?;
        }
        for ((task_ref, role_id), requirement) in &group.role_requirements {
            if requirement.role_id() != role_id
                || group.task_execution(task_ref).is_none()
                || task_ref.mission_id() != &group.mission_id
            {
                return Err(ControlError::InvalidProposal(
                    "checkpoint Group contains orphan authoritative role metadata".to_string(),
                ));
            }
        }
        for (task_ref, role_id) in group.task_unbound_roles.keys() {
            if group.role_requirement(task_ref, role_id).is_none() {
                return Err(ControlError::InvalidProposal(
                    "checkpoint Group contains orphan Task recovery state".to_string(),
                ));
            }
        }
        Ok(())
    }

    /// Enforces lifecycle-specific assignment coverage for one restored TaskExecution.
    fn validate_task_checkpoint_coverage(
        &self,
        group: &ExecutionGroup,
        execution: &TaskExecution,
        authoritative_roles: &std::collections::BTreeSet<&RoleId>,
        represented_roles: &std::collections::BTreeSet<&RoleId>,
    ) -> Result<(), ControlError> {
        if group.lifecycle == GroupLifecycle::Released {
            if !represented_roles.is_empty() {
                return Err(ControlError::InvalidProposal(
                    "Released Mission Group retains TaskExecution bindings".to_string(),
                ));
            }
            return Ok(());
        }
        let exact = represented_roles == authoritative_roles;
        let empty = represented_roles.is_empty();
        let has_task_recovery = group
            .task_unbound_roles
            .keys()
            .any(|(task_ref, _)| task_ref == execution.task_ref());
        let valid = match execution.lifecycle() {
            TaskExecutionLifecycle::Pending => empty,
            // A Ready Task is either waiting for its first Commit or has a complete committed
            // binding that has not been activated yet.
            TaskExecutionLifecycle::Ready => !has_task_recovery && (empty || exact),
            // Active/Blocked execution must account for every role, whether bound or awaiting
            // the externally decided recovery rebind.
            TaskExecutionLifecycle::Active | TaskExecutionLifecycle::Blocked => exact,
            // Terminal Task history may retain Context-scoped role assignments until Context or
            // Group release, so role coverage is intentionally not required here.
            TaskExecutionLifecycle::Completed => !has_task_recovery,
            TaskExecutionLifecycle::Failed | TaskExecutionLifecycle::Cancelled => exact,
        };
        if !valid {
            return Err(ControlError::InvalidProposal(format!(
                "checkpoint TaskExecution {} has invalid assignment coverage for {:?}",
                execution.task_ref(),
                execution.lifecycle()
            )));
        }
        Ok(())
    }

    /// Validates the optional metadata retained by a legacy single-Task Group.
    fn validate_legacy_group_checkpoint(&self, group: &ExecutionGroup) -> Result<(), ControlError> {
        for ((task_ref, role_id), requirement) in &group.role_requirements {
            if task_ref != &group.task_ref || role_id != requirement.role_id() {
                return Err(ControlError::InvalidProposal(
                    "legacy Group checkpoint contains mismatched role metadata".to_string(),
                ));
            }
        }
        for assignment in &group.assignments {
            if group.role_requirements.is_empty() {
                continue;
            }
            self.validate_checkpoint_actor_assignment(
                group,
                &group.task_ref,
                assignment.role_id(),
                assignment.node_id(),
            )?;
        }
        for (role_id, unbound) in &group.unbound_roles {
            if !group.role_requirements.is_empty() {
                self.validate_checkpoint_actor_assignment(
                    group,
                    &group.task_ref,
                    role_id,
                    &unbound.previous_node_id,
                )?;
            }
        }
        Ok(())
    }

    /// Confirms one current or released assignment remains on its authoritative Actor node.
    fn validate_checkpoint_actor_assignment(
        &self,
        group: &ExecutionGroup,
        task_ref: &TaskRef,
        role_id: &RoleId,
        node_id: &NodeId,
    ) -> Result<(), ControlError> {
        let role = group.role_requirement(task_ref, role_id).ok_or_else(|| {
            ControlError::InvalidProposal(
                "checkpoint assignment lacks authoritative role metadata".to_string(),
            )
        })?;
        let Some(actor_id) = role.actor_id() else {
            return Ok(());
        };
        if self
            .actor_authority_node(task_ref.mission_id(), actor_id)
            .is_none_or(|authority_node| authority_node != node_id)
        {
            return Err(ControlError::InvalidProposal(
                "checkpoint assignment violates Actor binding or placement authority".to_string(),
            ));
        }
        Ok(())
    }

    /// Creates the default Mission-level Group and all pending TaskExecutions from the full DAG.
    pub fn create_mission_group<E: EventSink>(
        &mut self,
        group_id: ExecutionGroupId,
        plan: &MissionPlan,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<ExecutionGroup, ControlError> {
        if self.groups.contains_key(&group_id) {
            return Err(ControlError::InvalidProposal(
                "execution group identity already exists".to_string(),
            ));
        }
        let mission_id = plan.goal().mission_id().clone();
        let initial_task_ref = plan
            .task_graph()
            .tasks()
            .first()
            .expect("validated Task Graph is nonempty")
            .requirement()
            .task_ref()
            .clone();
        let mut group =
            ExecutionGroup::new_mission(group_id.clone(), mission_id.clone(), initial_task_ref);
        for task in plan.task_graph().tasks() {
            let continuity = task.continuity();
            for role in task.requirement().roles() {
                group.role_requirements.insert(
                    (
                        task.requirement().task_ref().clone(),
                        role.role_id().clone(),
                    ),
                    role.clone(),
                );
            }
            let role_scopes = task
                .requirement()
                .roles()
                .iter()
                .map(|role| {
                    (
                        role.role_id().clone(),
                        continuity.resource_scope(role.role_id()),
                    )
                })
                .collect();
            let context = plan
                .contexts()
                .iter()
                .find(|context| context.context_id() == continuity.context_id())
                .expect("MissionPlan validation guarantees Task Context");
            let coupling_mode = continuity
                .coupling_mode_override()
                .unwrap_or_else(|| context.coupling_mode());
            let execution = TaskExecution::new_with_coupling_mode(
                task.requirement().task_ref().clone(),
                continuity.context_id().clone(),
                continuity.context_roles().clone(),
                role_scopes,
                coupling_mode,
            );
            group
                .task_executions
                .insert(task.requirement().task_ref().clone(), execution);
        }
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::ExecutionGroupCreated {
                group_id: group_id.clone(),
                mission_id,
            },
        );
        self.groups.insert(group.group_id().clone(), group.clone());
        for execution in group.task_executions() {
            events.append(
                timestamp,
                correlation_id,
                None,
                EventPayload::TaskExecutionRegistered {
                    group_id: group_id.clone(),
                    task_ref: execution.task_ref().clone(),
                    context_id: execution.context_id().clone(),
                },
            );
        }
        Ok(group)
    }

    /// Confirms a restored Mission Group is the exact Control projection of one accepted plan.
    pub fn validate_mission_group_plan(
        &self,
        group_id: &ExecutionGroupId,
        plan: &MissionPlan,
    ) -> Result<(), ControlError> {
        let group = self
            .groups
            .get(group_id)
            .ok_or_else(|| ControlError::UnknownGroup(group_id.clone()))?;
        if group.mission_id() != plan.goal().mission_id() || group.task_executions.is_empty() {
            return Err(ControlError::InvalidProposal(
                "MissionPlan does not identify this Mission-level Group".to_string(),
            ));
        }
        let planned_tasks = plan
            .task_graph()
            .tasks()
            .iter()
            .map(|task| task.requirement().task_ref())
            .collect::<std::collections::BTreeSet<_>>();
        let registered_tasks = group
            .task_executions
            .keys()
            .collect::<std::collections::BTreeSet<_>>();
        if planned_tasks != registered_tasks {
            return Err(ControlError::InvalidProposal(
                "MissionPlan Task DAG differs from the restored Execution Group".to_string(),
            ));
        }
        for task in plan.task_graph().tasks() {
            validate_group_task_requirement(group, task.requirement())?;
            let execution = group
                .task_execution(task.requirement().task_ref())
                .expect("TaskRef sets were validated above");
            let role_scopes = task
                .requirement()
                .roles()
                .iter()
                .map(|role| {
                    (
                        role.role_id().clone(),
                        task.continuity().resource_scope(role.role_id()),
                    )
                })
                .collect::<BTreeMap<_, _>>();
            let context = plan
                .contexts()
                .iter()
                .find(|context| context.context_id() == task.continuity().context_id())
                .expect("MissionPlan validation guarantees Task Context");
            let coupling_mode = task
                .continuity()
                .coupling_mode_override()
                .unwrap_or_else(|| context.coupling_mode());
            if execution.context_id() != task.continuity().context_id()
                || execution.role_scopes() != &role_scopes
                || execution.context_roles() != task.continuity().context_roles()
                || execution.coupling_mode() != coupling_mode
            {
                return Err(ControlError::InvalidProposal(
                    "MissionPlan continuity differs from the restored TaskExecution".to_string(),
                ));
            }
        }
        for (key, binding) in &group.context_bindings {
            let context = plan
                .contexts()
                .iter()
                .find(|context| context.context_id() == binding.context_id())
                .ok_or_else(|| {
                    ControlError::InvalidProposal(
                        "restored Context binding is absent from the MissionPlan".to_string(),
                    )
                })?;
            let context_role = context.role(binding.context_role_id()).ok_or_else(|| {
                ControlError::InvalidProposal(
                    "restored ContextRole binding is absent from the MissionPlan".to_string(),
                )
            })?;
            let origin = group
                .task_execution(binding.origin_task_ref())
                .ok_or_else(|| {
                    ControlError::InvalidProposal(
                        "restored Context binding origin Task is absent".to_string(),
                    )
                })?;
            let task_role = binding.assignment().role_id();
            let role = group
                .role_requirement(binding.origin_task_ref(), task_role)
                .ok_or_else(|| {
                    ControlError::InvalidProposal(
                        "restored Context binding origin role is absent".to_string(),
                    )
                })?;
            if key != &context_binding_key(binding.context_id(), binding.context_role_id())
                || origin.context_id() != binding.context_id()
                || origin.context_role(task_role) != Some(binding.context_role_id())
                || origin.role_scope(task_role) != ResourceBindingScope::Context
                || role.actor_id() != Some(context_role.actor_id())
            {
                return Err(ControlError::InvalidProposal(
                    "restored Context binding differs from MissionPlan continuity".to_string(),
                ));
            }
        }
        Ok(())
    }

    /// Writes a committed plan into the existing ready TaskExecution without creating a Group.
    pub fn bind_task_execution<E: EventSink>(
        &mut self,
        group_id: &ExecutionGroupId,
        plan: &CommittedPlan,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<TaskExecution, ControlError> {
        let group = self
            .groups
            .get(group_id)
            .ok_or_else(|| ControlError::UnknownGroup(group_id.clone()))?;
        let task_ref = plan.task_ref();
        if group.mission_id() != task_ref.mission_id() {
            return Err(ControlError::InvalidProposal(
                "Task belongs to another Mission than its Execution Group".to_string(),
            ));
        }
        let execution = group
            .task_executions
            .get(task_ref)
            .ok_or_else(|| {
                ControlError::InvalidProposal("Task is absent from the Mission DAG".to_string())
            })?
            .clone();
        if execution.lifecycle() != TaskExecutionLifecycle::Ready {
            return Err(ControlError::InvalidProposal(
                "only a ready Task execution can accept committed bindings".to_string(),
            ));
        }
        validate_task_assignments(&execution, plan.assignments())?;
        let actor_nodes = validate_authoritative_actor_assignments(self, group, plan)?;
        if !execution.assignments().is_empty() {
            return Err(ControlError::InvalidProposal(
                "Task execution already has committed bindings".to_string(),
            ));
        }
        for assignment in plan.assignments() {
            let scope = execution.role_scope(assignment.role_id());
            let context_role_id = execution.context_role(assignment.role_id()).cloned();
            for resource_id in assignment.resource_ids() {
                let reservation = self.reservations.get(resource_id).ok_or_else(|| {
                    ControlError::InvalidProposal(format!(
                        "committed resource {resource_id} has no reservation"
                    ))
                })?;
                let valid = match scope {
                    ResourceBindingScope::Task => {
                        reservation.task_ref == *task_ref
                            && reservation.role_id == *assignment.role_id()
                            && reservation.group_id.is_none()
                            && reservation.owner == domain::AllocationOwner::Task(task_ref.clone())
                    }
                    ResourceBindingScope::Context => {
                        context_role_id.as_ref().is_some_and(|context_role_id| {
                            reservation.scope == ResourceBindingScope::Context
                                && reservation.owner
                                    == domain::AllocationOwner::Context {
                                        mission_id: task_ref.mission_id().clone(),
                                        context_id: execution.context_id().clone(),
                                        context_role_id: context_role_id.clone(),
                                    }
                                && (reservation.group_id.is_none()
                                    || reservation.group_id.as_ref() == Some(group_id))
                        })
                    }
                };
                if !valid {
                    return Err(ControlError::InvalidProposal(format!(
                        "resource {resource_id} is not a valid reservation for this Task"
                    )));
                }
            }
        }
        for assignment in plan.assignments() {
            if execution.role_scope(assignment.role_id()) != ResourceBindingScope::Context {
                continue;
            }
            let context_role_id = execution
                .context_role(assignment.role_id())
                .expect("Context-scoped role has a validated ContextRole");
            let key = context_binding_key(execution.context_id(), context_role_id);
            if let Some(existing) = group.context_bindings.get(&key)
                && (existing.assignment.node_id() != assignment.node_id()
                    || existing.assignment.resource_ids() != assignment.resource_ids())
            {
                return Err(ControlError::InvalidProposal(
                    "ContextRole binding changed across Tasks".to_string(),
                ));
            }
        }
        for assignment in plan.assignments() {
            let scope = execution.role_scope(assignment.role_id());
            let context_role_id = execution.context_role(assignment.role_id()).cloned();
            for resource_id in assignment.resource_ids() {
                let reservation = self
                    .reservations
                    .get_mut(resource_id)
                    .expect("reservation validated above");
                reservation.group_id = Some(group_id.clone());
                if scope == ResourceBindingScope::Context {
                    let context_role_id = context_role_id.clone().expect("validated ContextRole");
                    let key = context_binding_key(execution.context_id(), &context_role_id);
                    let group = self
                        .groups
                        .get_mut(group_id)
                        .expect("Group validated above");
                    group
                        .context_bindings
                        .entry(key)
                        .or_insert_with(|| ContextBinding {
                            context_id: execution.context_id().clone(),
                            context_role_id,
                            origin_task_ref: task_ref.clone(),
                            assignment: assignment.clone(),
                        });
                }
            }
        }
        let execution = execution.with_assignments(plan.assignments().to_vec());
        let group = self
            .groups
            .get_mut(group_id)
            .expect("Group validated above");
        group
            .task_executions
            .insert(task_ref.clone(), execution.clone());
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::ExecutionGroupBound {
                group_id: group_id.clone(),
                task_ref: task_ref.clone(),
            },
        );
        for (actor_id, node_id) in actor_nodes {
            self.record_actor_binding(
                plan.task_ref().mission_id().clone(),
                actor_id.clone(),
                node_id.clone(),
            )?;
            events.append(
                timestamp,
                correlation_id,
                None,
                EventPayload::MissionActorBound {
                    mission_id: plan.task_ref().mission_id().clone(),
                    actor_id,
                    node_id,
                    task_ref: plan.task_ref().clone(),
                    group_id: group_id.clone(),
                },
            );
        }
        Ok(execution)
    }

    /// Binds a committed Task and records Mission actor continuity for its role assignments.
    pub fn bind_task_execution_with_requirement<E: EventSink>(
        &mut self,
        group_id: &ExecutionGroupId,
        plan: &CommittedPlan,
        requirement: &TaskRequirement,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<TaskExecution, ControlError> {
        if plan.task_ref() != requirement.task_ref() {
            return Err(ControlError::InvalidProposal(
                "Task requirement does not match committed plan".to_string(),
            ));
        }
        let group = self
            .groups
            .get(group_id)
            .ok_or_else(|| ControlError::UnknownGroup(group_id.clone()))?;
        validate_group_task_requirement(group, requirement)?;
        self.bind_task_execution(group_id, plan, timestamp, correlation_id, events)
    }

    /// Marks a registered Task ready after its DAG dependencies have been satisfied.
    pub fn ready_task_execution<E: EventSink>(
        &mut self,
        group_id: &ExecutionGroupId,
        task_ref: &TaskRef,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<(), ControlError> {
        let group = self
            .groups
            .get_mut(group_id)
            .ok_or_else(|| ControlError::UnknownGroup(group_id.clone()))?;
        let execution = group
            .task_executions
            .get(task_ref)
            .ok_or_else(|| ControlError::InvalidProposal("unknown Task execution".to_string()))?;
        if execution.lifecycle() != TaskExecutionLifecycle::Pending {
            return Err(ControlError::InvalidProposal(
                "only a pending Task can become ready".to_string(),
            ));
        }
        group.task_executions.insert(
            task_ref.clone(),
            execution.with_lifecycle(TaskExecutionLifecycle::Ready),
        );
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::TaskExecutionReady {
                group_id: group_id.clone(),
                task_ref: task_ref.clone(),
            },
        );
        Ok(())
    }

    /// Releases all Context-scoped resources when their Intelligence Context ends.
    pub fn release_context_bindings<E: EventSink>(
        &mut self,
        group_id: &ExecutionGroupId,
        context_id: &CoordinationContextId,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<Vec<ResourceId>, ControlError> {
        let group = self
            .groups
            .get(group_id)
            .ok_or_else(|| ControlError::UnknownGroup(group_id.clone()))?
            .clone();
        let mut resources = group
            .context_bindings()
            .filter(|binding| binding.context_id() == context_id)
            .flat_map(|binding| binding.assignment().resource_ids().iter().cloned())
            .collect::<Vec<_>>();
        resources.sort();
        resources.dedup();
        for resource_id in &resources {
            let reservation = self.reservations.get(resource_id).ok_or_else(|| {
                ControlError::InvalidProposal(format!(
                    "Context resource {resource_id} has no reservation"
                ))
            })?;
            if reservation.group_id.as_ref() != Some(group_id)
                || reservation.scope != ResourceBindingScope::Context
            {
                return Err(ControlError::InvalidProposal(format!(
                    "Context does not own resource {resource_id}"
                )));
            }
        }
        for resource_id in &resources {
            self.reservations.remove(resource_id);
        }
        let group_mut = self
            .groups
            .get_mut(group_id)
            .expect("group validated above");
        group_mut
            .context_bindings
            .retain(|_, binding| binding.context_id() != context_id);
        for execution in group_mut.task_executions.values_mut() {
            if execution.context_id() != context_id {
                continue;
            }
            let remaining = execution
                .assignments()
                .iter()
                .map(|assignment| {
                    RoleAssignment::new(
                        assignment.role_id().clone(),
                        assignment.node_id().clone(),
                        assignment
                            .resource_ids()
                            .iter()
                            .filter(|resource_id| !resources.contains(resource_id))
                            .cloned()
                            .collect(),
                    )
                })
                .collect();
            *execution = execution.with_assignments(remaining);
        }
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::ContextBindingsReleased {
                group_id: group_id.clone(),
                context_id: context_id.clone(),
                resource_ids: resources.clone(),
            },
        );
        Ok(resources)
    }

    /// Activates a registered Task while retaining the Mission-level Group.
    pub fn activate_task_execution<E: EventSink>(
        &mut self,
        group_id: &ExecutionGroupId,
        task_ref: &TaskRef,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<(), ControlError> {
        let group = self
            .groups
            .get_mut(group_id)
            .ok_or_else(|| ControlError::UnknownGroup(group_id.clone()))?;
        let execution = group
            .task_executions
            .get(task_ref)
            .ok_or_else(|| ControlError::InvalidProposal("unknown Task execution".to_string()))?;
        if !matches!(execution.lifecycle(), TaskExecutionLifecycle::Ready) {
            return Err(ControlError::InvalidProposal(
                "Task execution is not ready to activate".to_string(),
            ));
        }
        group.task_executions.insert(
            task_ref.clone(),
            execution.with_lifecycle(TaskExecutionLifecycle::Active),
        );
        if matches!(
            group.lifecycle,
            GroupLifecycle::Bound | GroupLifecycle::Adapted
        ) {
            group.lifecycle = GroupLifecycle::Active;
        }
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::TaskExecutionActivated {
                group_id: group_id.clone(),
                task_ref: task_ref.clone(),
            },
        );
        Ok(())
    }

    /// Completes one Task and leaves its parent Group alive for later Tasks.
    pub fn complete_task_execution<E: EventSink>(
        &mut self,
        group_id: &ExecutionGroupId,
        task_ref: &TaskRef,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<(), ControlError> {
        let group = self
            .groups
            .get_mut(group_id)
            .ok_or_else(|| ControlError::UnknownGroup(group_id.clone()))?;
        let execution = group
            .task_executions
            .get(task_ref)
            .ok_or_else(|| ControlError::InvalidProposal("unknown Task execution".to_string()))?;
        if !matches!(execution.lifecycle(), TaskExecutionLifecycle::Active) {
            return Err(ControlError::InvalidProposal(
                "Task execution is not active".to_string(),
            ));
        }
        group.task_executions.insert(
            task_ref.clone(),
            execution.with_lifecycle(TaskExecutionLifecycle::Completed),
        );
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::TaskExecutionCompleted {
                group_id: group_id.clone(),
                task_ref: task_ref.clone(),
            },
        );
        Ok(())
    }

    /// Marks one active Task failed while retaining the parent Group for recovery policy.
    pub fn fail_task_execution<E: EventSink>(
        &mut self,
        group_id: &ExecutionGroupId,
        task_ref: &TaskRef,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<(), ControlError> {
        let group = self
            .groups
            .get_mut(group_id)
            .ok_or_else(|| ControlError::UnknownGroup(group_id.clone()))?;
        let execution = group
            .task_executions
            .get(task_ref)
            .ok_or_else(|| ControlError::InvalidProposal("unknown Task execution".to_string()))?;
        if !matches!(
            execution.lifecycle(),
            TaskExecutionLifecycle::Active | TaskExecutionLifecycle::Blocked
        ) {
            return Err(ControlError::InvalidProposal(
                "only an active or blocked Task can fail".to_string(),
            ));
        }
        group.task_executions.insert(
            task_ref.clone(),
            execution.with_lifecycle(TaskExecutionLifecycle::Failed),
        );
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::TaskExecutionFailed {
                group_id: group_id.clone(),
                task_ref: task_ref.clone(),
            },
        );
        Ok(())
    }

    /// Releases only the supplied temporary Task resources and retains the parent Group.
    pub fn release_task_bindings<E: EventSink>(
        &mut self,
        group_id: &ExecutionGroupId,
        task_ref: &TaskRef,
        resource_ids: &[ResourceId],
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<(), ControlError> {
        let group = self
            .groups
            .get(group_id)
            .ok_or_else(|| ControlError::UnknownGroup(group_id.clone()))?;
        let execution = group
            .task_execution(task_ref)
            .ok_or_else(|| ControlError::InvalidProposal("unknown Task execution".to_string()))?;
        if !matches!(
            execution.lifecycle(),
            TaskExecutionLifecycle::Completed
                | TaskExecutionLifecycle::Failed
                | TaskExecutionLifecycle::Cancelled
        ) {
            return Err(ControlError::InvalidProposal(
                "Task bindings can only be released after terminal completion".to_string(),
            ));
        }
        let expected = execution
            .assignments()
            .iter()
            .flat_map(|assignment| assignment.resource_ids())
            .filter(|resource_id| {
                execution.binding_scope(resource_id) == ResourceBindingScope::Task
            })
            .cloned()
            .collect::<Vec<_>>();
        let mut expected_sorted = expected;
        expected_sorted.sort();
        let mut supplied = resource_ids.to_vec();
        supplied.sort();
        supplied.dedup();
        if supplied != expected_sorted {
            return Err(ControlError::InvalidProposal(
                "released resources must exactly match Task-scoped bindings".to_string(),
            ));
        }
        for resource_id in &expected_sorted {
            let reservation = self.reservations.get(resource_id).ok_or_else(|| {
                ControlError::InvalidProposal(format!(
                    "Task resource {resource_id} has no reservation"
                ))
            })?;
            if reservation.group_id.as_ref() != Some(group_id)
                || reservation.task_ref != *task_ref
                || reservation.scope != ResourceBindingScope::Task
            {
                return Err(ControlError::InvalidProposal(format!(
                    "Task does not own resource {resource_id}"
                )));
            }
        }
        for resource_id in &expected_sorted {
            self.reservations.remove(resource_id);
        }
        // Keep Context-scoped assignments visible on the TaskExecution. Their reservation
        // belongs to the Context and must survive the Task terminal transition.
        let remaining = execution
            .assignments()
            .iter()
            .filter(|assignment| {
                execution.role_scope(assignment.role_id()) == ResourceBindingScope::Context
            })
            .cloned()
            .collect::<Vec<_>>();
        let group = self
            .groups
            .get_mut(group_id)
            .expect("group validated above");
        let execution = group
            .task_executions
            .get(task_ref)
            .expect("Task validated above");
        group
            .task_executions
            .insert(task_ref.clone(), execution.with_assignments(remaining));
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::TaskExecutionBindingsReleased {
                group_id: group_id.clone(),
                task_ref: task_ref.clone(),
                resource_ids: expected_sorted,
            },
        );
        Ok(())
    }

    /// Creates a legacy single-Task Group and establishes first-use Actor bindings.
    ///
    /// New Mission execution must use [`Self::create_mission_group`] and bind TaskExecutions.
    #[cfg(test)]
    pub(crate) fn create_group_with_actor_bindings<E: EventSink>(
        &mut self,
        group_id: ExecutionGroupId,
        plan: &CommittedPlan,
        requirement: &TaskRequirement,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<ExecutionGroup, ControlError> {
        if plan.task_ref() != requirement.task_ref() {
            return Err(ControlError::InvalidProposal(
                "task requirement does not match committed plan".to_string(),
            ));
        }
        let mut actor_nodes = std::collections::BTreeMap::<ActorId, NodeId>::new();
        for role in requirement.roles() {
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
            if let Some(existing) = self.actor_binding(requirement.mission_id(), actor_id) {
                if existing.node_id() != assignment.node_id() {
                    return Err(ControlError::InvalidProposal(
                        "mission actor is already bound to another node".to_string(),
                    ));
                }
            } else if let Some(constraint) =
                self.actor_node_constraint(requirement.mission_id(), actor_id)
                && constraint.node_id() != assignment.node_id()
            {
                return Err(ControlError::InvalidProposal(
                    "actor assignment violates deployment placement constraint".to_string(),
                ));
            } else {
                let previous = actor_nodes.insert(actor_id.clone(), assignment.node_id().clone());
                if previous.is_some_and(|node| node != *assignment.node_id()) {
                    return Err(ControlError::InvalidProposal(
                        "one mission actor cannot bind multiple nodes in one Group".to_string(),
                    ));
                }
            }
        }
        self.create_group(group_id.clone(), plan, timestamp, correlation_id, events)?;
        let group = self
            .groups
            .get_mut(&group_id)
            .expect("Group was inserted by create_group");
        for role in requirement.roles() {
            group.role_requirements.insert(
                (requirement.task_ref().clone(), role.role_id().clone()),
                role.clone(),
            );
        }
        for (actor_id, node_id) in actor_nodes {
            self.record_actor_binding(
                requirement.mission_id().clone(),
                actor_id.clone(),
                node_id.clone(),
            )?;
            events.append(
                timestamp,
                correlation_id,
                None,
                EventPayload::MissionActorBound {
                    mission_id: requirement.mission_id().clone(),
                    actor_id,
                    node_id,
                    task_ref: requirement.task_ref().clone(),
                    group_id: group_id.clone(),
                },
            );
        }
        self.groups
            .get(&group_id)
            .cloned()
            .ok_or(ControlError::UnknownGroup(group_id))
    }

    /// Creates and binds a legacy single-Task Execution Group from a committed plan.
    ///
    /// New Mission execution must use [`Self::create_mission_group`] and bind TaskExecutions.
    #[cfg(test)]
    pub(crate) fn create_group<E: EventSink>(
        &mut self,
        group_id: ExecutionGroupId,
        plan: &CommittedPlan,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<ExecutionGroup, ControlError> {
        if self.groups.contains_key(&group_id) {
            return Err(ControlError::InvalidProposal(
                "execution group identity already exists".to_string(),
            ));
        }
        for assignment in plan.assignments() {
            for resource_id in assignment.resource_ids() {
                let reservation = self.reservations.get(resource_id).ok_or_else(|| {
                    ControlError::InvalidProposal(format!(
                        "committed resource {resource_id} has no reservation"
                    ))
                })?;
                if reservation.task_ref != *plan.task_ref()
                    || reservation.role_id != *assignment.role_id()
                    || reservation.group_id.is_some()
                {
                    return Err(ControlError::InvalidProposal(format!(
                        "committed resource {resource_id} cannot bind to group {group_id}"
                    )));
                }
            }
        }
        for assignment in plan.assignments() {
            for resource_id in assignment.resource_ids() {
                if let Some(reservation) = self.reservations.get_mut(resource_id) {
                    reservation.group_id = Some(group_id.clone());
                }
            }
        }
        let group = ExecutionGroup::new(group_id.clone(), plan);
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::ExecutionGroupBound {
                group_id: group_id.clone(),
                task_ref: plan.task_ref().clone(),
            },
        );
        self.groups.insert(group_id, group.clone());
        Ok(group)
    }

    /// Activates a newly bound or fully rebound group before role invocation.
    pub fn activate_group<E: EventSink>(
        &mut self,
        group_id: &ExecutionGroupId,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<(), ControlError> {
        let group = self
            .groups
            .get_mut(group_id)
            .ok_or_else(|| ControlError::UnknownGroup(group_id.clone()))?;
        if !matches!(
            group.lifecycle,
            GroupLifecycle::Bound | GroupLifecycle::Adapted
        ) {
            return Err(ControlError::InvalidLifecycle(group.lifecycle));
        }
        if !group.unbound_roles.is_empty() || !group.task_unbound_roles.is_empty() {
            return Err(ControlError::InvalidProposal(
                "execution group still has unbound roles".to_string(),
            ));
        }
        if !group.task_executions.is_empty()
            && !group.task_executions.values().any(|execution| {
                matches!(
                    execution.lifecycle(),
                    TaskExecutionLifecycle::Active | TaskExecutionLifecycle::Completed
                )
            })
        {
            return Err(ControlError::InvalidProposal(
                "Mission Group requires an explicitly activated TaskExecution".to_string(),
            ));
        }
        group.lifecycle = GroupLifecycle::Active;
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::ExecutionGroupActivated {
                group_id: group_id.clone(),
                task_ref: group.task_ref.clone(),
            },
        );
        Ok(())
    }

    /// Releases only one role's current member and resource binding for recovery.
    pub fn release_role_binding<E: EventSink>(
        &mut self,
        group_id: &ExecutionGroupId,
        role_id: &RoleId,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<(), ControlError> {
        let group = self
            .groups
            .get(group_id)
            .ok_or_else(|| ControlError::UnknownGroup(group_id.clone()))?;
        if group.lifecycle != GroupLifecycle::Blocked {
            return Err(ControlError::InvalidLifecycle(group.lifecycle));
        }
        let assignment_index = group
            .assignments
            .iter()
            .position(|assignment| assignment.role_id() == role_id)
            .ok_or_else(|| {
                ControlError::InvalidProposal(format!(
                    "group has no active binding for role {role_id}"
                ))
            })?;
        let assignment = &group.assignments[assignment_index];
        let task_ref = group.task_ref.clone();
        let node_id = assignment.node_id().clone();
        let resource_ids = assignment.resource_ids().to_vec();
        for resource_id in &resource_ids {
            let reservation = self.reservations.get(resource_id).ok_or_else(|| {
                ControlError::InvalidProposal(format!(
                    "group {group_id} binding {resource_id} has no reservation"
                ))
            })?;
            if reservation.task_ref != task_ref
                || reservation.role_id != *role_id
                || reservation.group_id.as_ref() != Some(group_id)
            {
                return Err(ControlError::InvalidProposal(format!(
                    "group {group_id} does not own role reservation {resource_id}"
                )));
            }
        }
        for resource_id in &resource_ids {
            self.reservations.remove(resource_id);
        }
        let group = self
            .groups
            .get_mut(group_id)
            .ok_or_else(|| ControlError::UnknownGroup(group_id.clone()))?;
        group.assignments.remove(assignment_index);
        group.unbound_roles.insert(
            role_id.clone(),
            UnboundRole {
                previous_node_id: node_id.clone(),
                assignment_index,
            },
        );
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::ExecutionGroupRoleBindingReleased {
                group_id: group_id.clone(),
                task_ref,
                role_id: role_id.clone(),
                node_id,
                resource_ids,
            },
        );
        Ok(())
    }

    /// Releases one Task-local role binding without affecting sibling Task executions.
    pub fn release_task_role_binding<E: EventSink>(
        &mut self,
        group_id: &ExecutionGroupId,
        task_ref: &TaskRef,
        role_id: &RoleId,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<(), ControlError> {
        let group = self
            .groups
            .get(group_id)
            .ok_or_else(|| ControlError::UnknownGroup(group_id.clone()))?;
        if group.lifecycle != GroupLifecycle::Blocked {
            return Err(ControlError::InvalidLifecycle(group.lifecycle));
        }
        let execution = group.task_execution(task_ref).ok_or_else(|| {
            ControlError::InvalidProposal("Task execution is not registered".to_string())
        })?;
        let assignment_index = execution
            .assignments()
            .iter()
            .position(|assignment| assignment.role_id() == role_id)
            .ok_or_else(|| ControlError::InvalidProposal(format!("Task has no role {role_id}")))?;
        let assignment = &execution.assignments()[assignment_index];
        let node_id = assignment.node_id().clone();
        let resource_ids = assignment.resource_ids().to_vec();
        for resource_id in &resource_ids {
            let reservation = self.reservations.get(resource_id).ok_or_else(|| {
                ControlError::InvalidProposal(format!(
                    "Task binding {resource_id} has no reservation"
                ))
            })?;
            if reservation.task_ref != *task_ref
                || reservation.role_id != *role_id
                || reservation.group_id.as_ref() != Some(group_id)
            {
                return Err(ControlError::InvalidProposal(format!(
                    "Task {task_ref} does not own role reservation {resource_id}"
                )));
            }
            if reservation.scope == ResourceBindingScope::Context {
                return Err(ControlError::InvalidProposal(
                    "Context-scoped role bindings must end with their Context before recovery release"
                        .to_string(),
                ));
            }
        }
        for resource_id in &resource_ids {
            if self
                .reservations
                .get(resource_id)
                .is_some_and(|reservation| reservation.scope == ResourceBindingScope::Task)
            {
                self.reservations.remove(resource_id);
            }
        }
        let group = self
            .groups
            .get_mut(group_id)
            .expect("group validated above");
        let execution = group
            .task_executions
            .get_mut(task_ref)
            .expect("Task validated above");
        let mut assignments = execution.assignments().to_vec();
        assignments.remove(assignment_index);
        *execution = execution.with_assignments(assignments);
        group.task_unbound_roles.insert(
            (task_ref.clone(), role_id.clone()),
            UnboundRole {
                previous_node_id: node_id.clone(),
                assignment_index,
            },
        );
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::ExecutionGroupRoleBindingReleased {
                group_id: group_id.clone(),
                task_ref: task_ref.clone(),
                role_id: role_id.clone(),
                node_id,
                resource_ids,
            },
        );
        Ok(())
    }

    /// Rebinds one blocked role using resources already committed by coordination.
    pub fn rebind_role<E: EventSink>(
        &mut self,
        committed: &CommittedRecoveryAssignment,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<RecoveryOutcome, ControlError> {
        let group = self
            .groups
            .get(committed.group_id())
            .ok_or_else(|| ControlError::UnknownGroup(committed.group_id().clone()))?;
        if group.lifecycle != GroupLifecycle::Blocked {
            return Err(ControlError::InvalidLifecycle(group.lifecycle));
        }
        if group.task_ref != *committed.task_ref()
            && group.task_execution(committed.task_ref()).is_none()
        {
            return Err(ControlError::InvalidProposal(
                "committed recovery belongs to another task".to_string(),
            ));
        }
        self.validate_pending_recovery_commitment(committed)?;
        let unbound_role = group
            .unbound_roles
            .get(committed.role_id())
            .cloned()
            .or_else(|| {
                group
                    .task_unbound_roles
                    .get(&(committed.task_ref().clone(), committed.role_id().clone()))
                    .cloned()
            })
            .ok_or_else(|| {
                ControlError::InvalidProposal(format!(
                    "role {} is not unbound for committed rebind",
                    committed.role_id()
                ))
            })?;
        let previous_node = unbound_role.previous_node_id.clone();
        let assignment_index = unbound_role.assignment_index;
        if previous_node != *committed.previous_node_id()
            || committed.replacement_node_id() == committed.previous_node_id()
        {
            return Err(ControlError::InvalidProposal(
                "committed recovery does not match the released role binding".to_string(),
            ));
        }
        self.validate_recovery_commitment_reservations(committed)?;
        let replacement_assignment = RoleAssignment::new(
            committed.role_id().clone(),
            committed.replacement_node_id().clone(),
            committed.committed_resource_ids().to_vec(),
        );
        let group = self
            .groups
            .get_mut(committed.group_id())
            .ok_or_else(|| ControlError::UnknownGroup(committed.group_id().clone()))?;
        if group.task_execution(committed.task_ref()).is_some() {
            let execution = group
                .task_executions
                .get_mut(committed.task_ref())
                .expect("Task validated above");
            let insertion_index = assignment_index.min(execution.assignments().len());
            let mut assignments = execution.assignments().to_vec();
            assignments.insert(insertion_index, replacement_assignment);
            *execution = execution.with_assignments(assignments);
            group
                .task_unbound_roles
                .remove(&(committed.task_ref().clone(), committed.role_id().clone()));
        } else {
            let insertion_index = assignment_index.min(group.assignments.len());
            group
                .assignments
                .insert(insertion_index, replacement_assignment);
            group.unbound_roles.remove(committed.role_id());
        }
        group.lifecycle = GroupLifecycle::Adapted;
        self.pending_recovery_commitments.remove(&(
            committed.group_id().clone(),
            committed.task_ref().clone(),
            committed.role_id().clone(),
        ));
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::RecoveryRebound {
                group_id: committed.group_id().clone(),
                task_ref: committed.task_ref().clone(),
                role_id: committed.role_id().clone(),
                from_node: previous_node.clone(),
                to_node: committed.replacement_node_id().clone(),
            },
        );
        Ok(RecoveryOutcome::Recovered {
            group_id: committed.group_id().clone(),
            task_ref: committed.task_ref().clone(),
            role_id: committed.role_id().clone(),
            from_node: previous_node,
            to_node: committed.replacement_node_id().clone(),
        })
    }

    /// Marks a group complete after every registered TaskExecution succeeds.
    pub fn complete_group<E: EventSink>(
        &mut self,
        group_id: &ExecutionGroupId,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<(), ControlError> {
        let group = self
            .groups
            .get_mut(group_id)
            .ok_or_else(|| ControlError::UnknownGroup(group_id.clone()))?;
        if !matches!(
            group.lifecycle,
            GroupLifecycle::Active | GroupLifecycle::Adapted
        ) {
            return Err(ControlError::InvalidLifecycle(group.lifecycle));
        }
        if !group.unbound_roles.is_empty() || !group.task_unbound_roles.is_empty() {
            return Err(ControlError::InvalidProposal(
                "execution group still has unbound roles".to_string(),
            ));
        }
        if let Some(incomplete) = group
            .task_executions
            .values()
            .find(|execution| execution.lifecycle() != TaskExecutionLifecycle::Completed)
        {
            return Err(ControlError::InvalidProposal(format!(
                "TaskExecution {} is not completed",
                incomplete.task_ref()
            )));
        }
        group.lifecycle = GroupLifecycle::Completed;
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::ExecutionGroupCompleted {
                group_id: group_id.clone(),
                task_ref: group.task_ref.clone(),
            },
        );
        Ok(())
    }

    /// Marks a group blocked until reconciliation restores progress or declares failure.
    pub fn block_group<E: EventSink>(
        &mut self,
        group_id: &ExecutionGroupId,
        reason: impl Into<String>,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<(), ControlError> {
        let group = self
            .groups
            .get_mut(group_id)
            .ok_or_else(|| ControlError::UnknownGroup(group_id.clone()))?;
        if !matches!(
            group.lifecycle,
            GroupLifecycle::Bound | GroupLifecycle::Active | GroupLifecycle::Adapted
        ) {
            return Err(ControlError::InvalidLifecycle(group.lifecycle));
        }
        group.lifecycle = GroupLifecycle::Blocked;
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::ExecutionGroupBlocked {
                group_id: group_id.clone(),
                task_ref: group.task_ref.clone(),
                reason: reason.into(),
            },
        );
        Ok(())
    }

    /// Marks a blocked group terminally failed after recovery is explicitly exhausted.
    pub fn fail_group<E: EventSink>(
        &mut self,
        group_id: &ExecutionGroupId,
        reason: impl Into<String>,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<(), ControlError> {
        let group = self
            .groups
            .get_mut(group_id)
            .ok_or_else(|| ControlError::UnknownGroup(group_id.clone()))?;
        if group.lifecycle != GroupLifecycle::Blocked {
            return Err(ControlError::InvalidLifecycle(group.lifecycle));
        }
        group.lifecycle = GroupLifecycle::Failed;
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::ExecutionGroupFailed {
                group_id: group_id.clone(),
                task_ref: group.task_ref.clone(),
                reason: reason.into(),
            },
        );
        Ok(())
    }

    /// Releases every reservation and pending commitment owned by a terminal Group.
    pub fn release_group<E: EventSink>(
        &mut self,
        group_id: &ExecutionGroupId,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<(), ControlError> {
        let group = self
            .groups
            .get(group_id)
            .ok_or_else(|| ControlError::UnknownGroup(group_id.clone()))?;
        if !matches!(
            group.lifecycle,
            GroupLifecycle::Completed | GroupLifecycle::Failed
        ) {
            return Err(ControlError::InvalidLifecycle(group.lifecycle));
        }
        let task_ref = group.task_ref.clone();
        let mut expected_resources = BTreeMap::<ResourceId, RoleId>::new();
        for assignment in &group.assignments {
            for resource_id in assignment.resource_ids() {
                if expected_resources
                    .insert(resource_id.clone(), assignment.role_id().clone())
                    .is_some()
                {
                    return Err(ControlError::InvalidProposal(format!(
                        "group {group_id} has duplicate active resource {resource_id}"
                    )));
                }
            }
        }
        for execution in group.task_executions.values() {
            for assignment in execution.assignments() {
                for resource_id in assignment.resource_ids() {
                    if execution.binding_scope(resource_id) == ResourceBindingScope::Context {
                        continue;
                    }
                    if expected_resources
                        .insert(resource_id.clone(), assignment.role_id().clone())
                        .is_some()
                    {
                        return Err(ControlError::InvalidProposal(format!(
                            "group {group_id} has duplicate active resource {resource_id}"
                        )));
                    }
                }
            }
        }
        for binding in group.context_bindings.values() {
            for resource_id in binding.assignment().resource_ids() {
                if expected_resources
                    .insert(resource_id.clone(), binding.assignment().role_id().clone())
                    .is_some()
                {
                    return Err(ControlError::InvalidProposal(format!(
                        "group {group_id} has duplicate context resource {resource_id}"
                    )));
                }
            }
        }
        let pending_keys = self
            .pending_recovery_commitments
            .iter()
            .filter(|((pending_group_id, _, _), _)| pending_group_id == group_id)
            .map(|(key, committed)| {
                if committed.group_id() != group_id
                    || (committed.task_ref() != &task_ref
                        && group.task_execution(committed.task_ref()).is_none())
                    || committed.task_ref() != &key.1
                    || committed.role_id() != &key.2
                {
                    return Err(ControlError::InvalidProposal(format!(
                        "group {group_id} has inconsistent pending recovery ownership"
                    )));
                }
                for resource_id in committed.committed_resource_ids() {
                    if expected_resources
                        .insert(resource_id.clone(), committed.role_id().clone())
                        .is_some()
                    {
                        return Err(ControlError::InvalidProposal(format!(
                            "group {group_id} has duplicate committed resource {resource_id}"
                        )));
                    }
                }
                Ok(key.clone())
            })
            .collect::<Result<Vec<_>, ControlError>>()?;

        for (resource_id, role_id) in &expected_resources {
            let reservation = self.reservations.get(resource_id).ok_or_else(|| {
                ControlError::InvalidProposal(format!(
                    "group {group_id} ownership {resource_id} has no reservation"
                ))
            })?;
            let task_owned = reservation.task_ref == task_ref
                || group.task_execution(&reservation.task_ref).is_some()
                || matches!(reservation.owner, domain::AllocationOwner::Context { .. });
            if !task_owned
                || reservation.role_id != *role_id
                || reservation.group_id.as_ref() != Some(group_id)
            {
                return Err(ControlError::InvalidProposal(format!(
                    "group {group_id} has mismatched reservation {resource_id}"
                )));
            }
        }
        let resource_ids = self
            .reservations
            .iter()
            .filter(|(_, reservation)| reservation.group_id.as_ref() == Some(group_id))
            .map(|(resource_id, reservation)| {
                let task_owned = reservation.task_ref == task_ref
                    || group.task_execution(&reservation.task_ref).is_some()
                    || matches!(reservation.owner, domain::AllocationOwner::Context { .. });
                if !task_owned || expected_resources.get(resource_id) != Some(&reservation.role_id)
                {
                    return Err(ControlError::InvalidProposal(format!(
                        "group {group_id} has orphan reservation {resource_id}"
                    )));
                }
                Ok(resource_id.clone())
            })
            .collect::<Result<Vec<_>, ControlError>>()?;

        for resource_id in &resource_ids {
            self.reservations.remove(resource_id);
        }
        for key in pending_keys {
            self.pending_recovery_commitments.remove(&key);
        }
        let group = self
            .groups
            .get_mut(group_id)
            .ok_or_else(|| ControlError::UnknownGroup(group_id.clone()))?;
        group.assignments.clear();
        group.context_bindings.clear();
        group.unbound_roles.clear();
        group.task_unbound_roles.clear();
        for execution in group.task_executions.values_mut() {
            // Terminal Group release removes live Task bindings from the durable projection;
            // historical binding events remain available in the event log.
            *execution = execution.with_assignments(Vec::new());
        }
        group.lifecycle = GroupLifecycle::Released;
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::ExecutionGroupReleased {
                group_id: group_id.clone(),
                task_ref,
                resource_ids,
            },
        );
        Ok(())
    }

    /// Returns the current group snapshot for assertions and adapters.
    pub fn group(&self, group_id: &ExecutionGroupId) -> Option<&ExecutionGroup> {
        self.groups.get(group_id)
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
