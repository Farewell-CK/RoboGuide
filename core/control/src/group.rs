//! Execution Group models, lifecycle transitions, and committed Rebind.

use crate::{
    CommittedPlan, CommittedRecoveryAssignment, ControlError, ControlPlane, RecoveryOutcome,
};
use domain::{
    ActorId, ContextRoleId, CoordinationContextId, CorrelationId, EventPayload, ExecutionGroupId,
    NodeId, ResourceBindingScope, ResourceId, RoleAssignment, RoleId, TaskExecution,
    TaskExecutionLifecycle, TaskId, TaskRef, TaskRequirement, TimestampMs,
};
use ports::EventSink;
use std::collections::BTreeMap;

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
    /// Mission-scoped task owned by the group.
    pub(crate) task_ref: TaskRef,
    /// Current role, member, and resource bindings.
    pub(crate) assignments: Vec<RoleAssignment>,
    /// Roles awaiting replacement while the Group identity and context remain.
    pub(crate) unbound_roles: BTreeMap<RoleId, UnboundRole>,
    /// Task-local roles awaiting replacement while sibling Tasks remain intact.
    pub(crate) task_unbound_roles: BTreeMap<(TaskRef, RoleId), UnboundRole>,
    /// Lifecycle state used by adaptation and recovery.
    pub(crate) lifecycle: GroupLifecycle,
    /// Task execution units retained while the Group remains alive.
    pub(crate) task_executions: BTreeMap<TaskRef, TaskExecution>,
}

impl ExecutionGroup {
    /// Creates a group from a committed plan.
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
}

impl ControlPlane {
    /// Creates the default Mission-level Group before its first Task begins execution.
    pub fn create_mission_group<E: EventSink>(
        &mut self,
        group_id: ExecutionGroupId,
        mission_id: domain::MissionId,
        initial_task_ref: TaskRef,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<ExecutionGroup, ControlError> {
        if mission_id != *initial_task_ref.mission_id() {
            return Err(ControlError::InvalidProposal(
                "initial Task belongs to another Mission".to_string(),
            ));
        }
        if self.groups.contains_key(&group_id) {
            return Err(ControlError::InvalidProposal(
                "execution group identity already exists".to_string(),
            ));
        }
        let group =
            ExecutionGroup::new_mission(group_id.clone(), mission_id.clone(), initial_task_ref);
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::ExecutionGroupCreated {
                group_id,
                mission_id,
            },
        );
        self.groups.insert(group.group_id().clone(), group.clone());
        Ok(group)
    }

    /// Registers one already-committed Task execution without creating or destroying its Group.
    #[allow(clippy::too_many_arguments)]
    pub fn register_task_execution<E: EventSink>(
        &mut self,
        group_id: &ExecutionGroupId,
        plan: &CommittedPlan,
        context_id: CoordinationContextId,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<TaskExecution, ControlError> {
        let group = self
            .groups
            .get_mut(group_id)
            .ok_or_else(|| ControlError::UnknownGroup(group_id.clone()))?;
        let task_ref = plan.task_ref();
        if group.mission_id() != task_ref.mission_id() {
            return Err(ControlError::InvalidProposal(
                "Task belongs to another Mission than its Execution Group".to_string(),
            ));
        }
        if group.task_executions.contains_key(task_ref) {
            return Err(ControlError::InvalidProposal(
                "Task execution already exists in the Execution Group".to_string(),
            ));
        }
        for assignment in plan.assignments() {
            for resource_id in assignment.resource_ids() {
                let reservation = self.reservations.get(resource_id).ok_or_else(|| {
                    ControlError::InvalidProposal(format!(
                        "committed resource {resource_id} has no reservation"
                    ))
                })?;
                if reservation.task_ref != *task_ref
                    || reservation.role_id != *assignment.role_id()
                    || reservation.group_id.is_some()
                {
                    return Err(ControlError::InvalidProposal(format!(
                        "resource {resource_id} is not an unbound reservation for this Task"
                    )));
                }
            }
        }
        for assignment in plan.assignments() {
            for resource_id in assignment.resource_ids() {
                self.reservations
                    .get_mut(resource_id)
                    .expect("reservation validated above")
                    .group_id = Some(group_id.clone());
            }
        }
        let execution = TaskExecution::new(
            task_ref.clone(),
            context_id.clone(),
            plan.assignments().to_vec(),
        );
        group
            .task_executions
            .insert(task_ref.clone(), execution.clone());
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::TaskExecutionRegistered {
                group_id: group_id.clone(),
                task_ref: task_ref.clone(),
                context_id,
            },
        );
        Ok(execution)
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

    /// Declares a committed Task resource as Context-scoped before that Task starts.
    pub fn set_binding_scope(
        &mut self,
        group_id: &ExecutionGroupId,
        task_ref: &TaskRef,
        resource_id: &ResourceId,
        scope: ResourceBindingScope,
    ) -> Result<(), ControlError> {
        let group = self
            .groups
            .get_mut(group_id)
            .ok_or_else(|| ControlError::UnknownGroup(group_id.clone()))?;
        let execution = group
            .task_executions
            .get(task_ref)
            .ok_or_else(|| ControlError::InvalidProposal("unknown Task execution".to_string()))?;
        if execution.lifecycle() != TaskExecutionLifecycle::Pending
            && execution.lifecycle() != TaskExecutionLifecycle::Ready
        {
            return Err(ControlError::InvalidProposal(
                "binding scope can only change before Task activation".to_string(),
            ));
        }
        let reservation = self.reservations.get_mut(resource_id).ok_or_else(|| {
            ControlError::InvalidProposal(format!("resource {resource_id} has no reservation"))
        })?;
        if reservation.group_id.as_ref() != Some(group_id)
            || reservation.task_ref != *task_ref
            || !execution
                .assignments()
                .iter()
                .any(|assignment| assignment.resource_ids().contains(resource_id))
        {
            return Err(ControlError::InvalidProposal(
                "resource is not owned by this Task execution".to_string(),
            ));
        }
        reservation.scope = scope;
        group.task_executions.insert(
            task_ref.clone(),
            execution.with_binding_scope(resource_id.clone(), scope),
        );
        Ok(())
    }

    /// Associates a Task-local role with a persistent Mission Intelligence ContextRole.
    pub fn set_context_role(
        &mut self,
        group_id: &ExecutionGroupId,
        task_ref: &TaskRef,
        role_id: &RoleId,
        context_role_id: ContextRoleId,
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
            TaskExecutionLifecycle::Pending | TaskExecutionLifecycle::Ready
        ) {
            return Err(ControlError::InvalidProposal(
                "ContextRole can only be declared before Task activation".to_string(),
            ));
        }
        if !execution
            .assignments()
            .iter()
            .any(|assignment| assignment.role_id() == role_id)
        {
            return Err(ControlError::InvalidProposal(format!(
                "Task has no role {role_id}"
            )));
        }
        group.task_executions.insert(
            task_ref.clone(),
            execution.with_context_role(role_id.clone(), context_role_id),
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
        let mut resources = Vec::new();
        for execution in group.task_executions() {
            if execution.context_id() != context_id {
                continue;
            }
            for assignment in execution.assignments() {
                for resource_id in assignment.resource_ids() {
                    if execution.binding_scope(resource_id) == ResourceBindingScope::Context {
                        resources.push(resource_id.clone());
                    }
                }
            }
        }
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
        let group = self
            .groups
            .get_mut(group_id)
            .expect("group validated above");
        let execution = group
            .task_executions
            .get(task_ref)
            .expect("Task validated above");
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
                        .filter(|resource_id| !expected_sorted.contains(resource_id))
                        .cloned()
                        .collect(),
                )
            })
            .collect();
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

    /// Creates a Group and establishes first-use Actor bindings after Group Bind succeeds.
    pub fn create_group_with_actor_bindings<E: EventSink>(
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
            } else {
                let previous = actor_nodes.insert(actor_id.clone(), assignment.node_id().clone());
                if previous.is_some_and(|node| node != *assignment.node_id()) {
                    return Err(ControlError::InvalidProposal(
                        "one mission actor cannot bind multiple nodes in one Group".to_string(),
                    ));
                }
            }
        }
        let group = self.create_group(group_id.clone(), plan, timestamp, correlation_id, events)?;
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
        Ok(group)
    }

    /// Creates and binds an Execution Group from a committed plan.
    pub fn create_group<E: EventSink>(
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
        if !group.unbound_roles.is_empty() {
            return Err(ControlError::InvalidProposal(
                "execution group still has unbound roles".to_string(),
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

    /// Marks a group complete after all required role executions succeed.
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
            GroupLifecycle::Active | GroupLifecycle::Adapted
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
                || group.task_execution(&reservation.task_ref).is_some();
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
                    || group.task_execution(&reservation.task_ref).is_some();
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
        group.unbound_roles.clear();
        group.task_unbound_roles.clear();
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
