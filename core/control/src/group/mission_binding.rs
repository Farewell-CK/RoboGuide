//! Mission-level Execution Group creation and Task binding.

use super::*;

impl ControlPlane {
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
}
