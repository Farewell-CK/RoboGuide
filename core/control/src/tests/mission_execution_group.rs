use std::collections::BTreeMap;

use domain::{
    ActorId, ContextRole, ContextRoleId, CoordinationContext, CoordinationContextId, MissionId,
    ResourceBindingScope, TaskContinuity,
};

/// Builds a two-Task plan whose complete DAG must be registered with its Group.
fn mission_plan(scope: ResourceBindingScope) -> MissionPlan {
    mission_plan_with_scope(scope, true)
}

/// Builds the same plan while optionally omitting explicit role scopes.
fn mission_plan_with_scope(scope: ResourceBindingScope, explicit_scope: bool) -> MissionPlan {
    let mission_id = MissionId::new("mission-long-lived").expect("mission id valid");
    let context_id = CoordinationContextId::new("context-main").expect("context id valid");
    let context_role_id = ContextRoleId::new("worker").expect("context role id valid");
    let actor_id = ActorId::new("worker").expect("actor id valid");
    let contract = CapabilityContractRef::new("compute", "work", "v1")
        .expect("contract valid");
    let task = |task_id: &str, dependencies: Vec<TaskId>| {
        let role_id = RoleId::new(format!("role-{task_id}")).expect("role id valid");
        let requirement = TaskRequirement::new(
            mission_id.clone(),
            TaskId::new(task_id).expect("task id valid"),
            vec![RoleRequirement::new_with_actor_and_contract(
                role_id.clone(),
                actor_id.clone(),
                CapabilityKind::Compute,
                contract.clone(),
                Some(ResourceKind::Compute),
            )],
        )
        .expect("requirement valid");
        let resource_scopes = if explicit_scope {
            BTreeMap::from([(role_id.clone(), scope)])
        } else {
            BTreeMap::new()
        };
        PlannedTask::new(
            format!("execute {task_id}"),
            requirement,
            BTreeMap::from([(
                role_id.clone(),
                ExecutionIntent::new(contract.clone(), BTreeMap::new()).expect("intent valid"),
            )]),
            dependencies,
            TaskContinuity::new(
                context_id.clone(),
                BTreeMap::from([(role_id.clone(), context_role_id.clone())]),
                resource_scopes,
            ),
        )
        .expect("planned Task valid")
    };
    let first = task("task-a", Vec::new());
    let second = task("task-b", vec![first.task_id().clone()]);
    let graph = TaskGraph::new(mission_id.clone(), vec![first, second]).expect("DAG valid");
    let goal = MissionGoal::new(mission_id, "execute both Tasks").expect("goal valid");
    let context = CoordinationContext::new(
        context_id,
        vec![ContextRole::new(context_role_id, actor_id)],
    )
    .expect("context valid");
    MissionPlan::new(goal, graph, vec![context]).expect("MissionPlan valid")
}

/// Returns a stable correlation identity for Group lifecycle evidence.
fn trace() -> CorrelationId {
    CorrelationId::new("mission-group-test").expect("correlation valid")
}

/// A Mission-level Group registers the complete DAG and survives one Task completion.
#[test]
fn mission_group_hosts_complete_dag_without_task_completion_releasing_group() {
    let mut control = ControlPlane::new();
    let mut events = TestEvents;
    let plan = mission_plan(ResourceBindingScope::Task);
    let group_id = domain::ExecutionGroupId::new("group-long-lived").expect("group id valid");
    control
        .create_mission_group(
            group_id.clone(),
            &plan,
            TimestampMs::new(1),
            &trace(),
            &mut events,
        )
        .expect("mission Group should be created");
    let first = plan.task_graph().tasks()[0].requirement().task_ref().clone();
    let second = plan.task_graph().tasks()[1].requirement().task_ref().clone();
    let first_role = plan.task_graph().tasks()[0].requirement().roles()[0]
        .role_id()
        .clone();
    control
        .ready_task_execution(&group_id, &first, TimestampMs::new(2), &trace(), &mut events)
        .expect("first Task ready");
    control
        .bind_task_execution(
            &group_id,
            &CommittedPlan::new(
                first.clone(),
                vec![RoleAssignment::new(
                    first_role,
                    NodeId::new("node-a").expect("node valid"),
                    Vec::new(),
                )],
            ),
            TimestampMs::new(3),
            &trace(),
            &mut events,
        )
        .expect("first Task binds");
    control
        .activate_task_execution(&group_id, &first, TimestampMs::new(4), &trace(), &mut events)
        .expect("first Task activates");
    control
        .complete_task_execution(&group_id, &first, TimestampMs::new(5), &trace(), &mut events)
        .expect("first Task completes");

    let group = control.group(&group_id).expect("Group remains alive");
    assert_eq!(group.lifecycle(), GroupLifecycle::Active);
    assert_eq!(group.task_executions().count(), 2);
    assert_eq!(
        group.task_execution(&first).expect("first retained").lifecycle(),
        domain::TaskExecutionLifecycle::Completed
    );
    assert_eq!(
        group.task_execution(&second).expect("second retained").lifecycle(),
        domain::TaskExecutionLifecycle::Pending
    );
}

/// Binding consumes Commit authority and Task release removes Task-scoped resources exactly.
#[test]
fn mission_task_bind_uses_commit_authority_and_releases_task_scope() {
    let mut control = ControlPlane::new();
    let mut events = TestEvents;
    let plan = mission_plan(ResourceBindingScope::Task);
    let task = plan.task_graph().tasks()[0].requirement().task_ref().clone();
    let role = plan.task_graph().tasks()[0].requirement().roles()[0]
        .role_id()
        .clone();
    let group = domain::ExecutionGroupId::new("group-authority").expect("group valid");
    let resource = ResourceId::new("compute-worker").expect("resource valid");
    control
        .create_mission_group(group.clone(), &plan, TimestampMs::new(1), &trace(), &mut events)
        .expect("Group creates");
    control.reservations.insert(
        resource.clone(),
        crate::coordination::Reservation {
            task_ref: task.clone(),
            role_id: role.clone(),
            group_id: None,
            scope: ResourceBindingScope::Task,
            owner: domain::AllocationOwner::Task(task.clone()),
        },
    );
    control
        .ready_task_execution(&group, &task, TimestampMs::new(2), &trace(), &mut events)
        .expect("Task ready");
    control
        .bind_task_execution(
            &group,
            &CommittedPlan::new(
                task.clone(),
                vec![RoleAssignment::new(
                    role,
                    NodeId::new("node-worker").expect("node valid"),
                    vec![resource.clone()],
                )],
            ),
            TimestampMs::new(3),
            &trace(),
            &mut events,
        )
        .expect("committed plan binds");
    control
        .activate_task_execution(&group, &task, TimestampMs::new(4), &trace(), &mut events)
        .expect("Task activates");
    control
        .complete_task_execution(&group, &task, TimestampMs::new(5), &trace(), &mut events)
        .expect("Task completes");
    control
        .release_task_bindings(
            &group,
            &task,
            std::slice::from_ref(&resource),
            TimestampMs::new(6),
            &trace(),
            &mut events,
        )
        .expect("Task resources release");
    assert!(!control.reservations.contains_key(&resource));
}

/// Context-scoped resources survive Task release and end only with their Context.
#[test]
fn context_scope_outlives_task_and_releases_explicitly() {
    let mut control = ControlPlane::new();
    let mut events = TestEvents;
    let plan = mission_plan(ResourceBindingScope::Context);
    let task = plan.task_graph().tasks()[0].requirement().task_ref().clone();
    let role = plan.task_graph().tasks()[0].requirement().roles()[0]
        .role_id()
        .clone();
    let context = plan.task_graph().tasks()[0].continuity().context_id().clone();
    let group = domain::ExecutionGroupId::new("group-context").expect("group valid");
    let resource = ResourceId::new("context-compute").expect("resource valid");
    control
        .create_mission_group(group.clone(), &plan, TimestampMs::new(1), &trace(), &mut events)
        .expect("Group creates");
    control.reservations.insert(
        resource.clone(),
        crate::coordination::Reservation {
            task_ref: task.clone(),
            role_id: role.clone(),
            group_id: None,
            scope: ResourceBindingScope::Context,
            owner: domain::AllocationOwner::Context {
                mission_id: task.mission_id().clone(),
                context_id: context.clone(),
                context_role_id: ContextRoleId::new("worker").expect("context role id valid"),
            },
        },
    );
    control
        .ready_task_execution(&group, &task, TimestampMs::new(2), &trace(), &mut events)
        .expect("Task ready");
    control
        .bind_task_execution(
            &group,
            &CommittedPlan::new(
                task,
                vec![RoleAssignment::new(
                    role,
                    NodeId::new("node-context").expect("node valid"),
                    vec![resource.clone()],
                )],
            ),
            TimestampMs::new(3),
            &trace(),
            &mut events,
        )
        .expect("Task binds");
    control
        .release_context_bindings(
            &group,
            &context,
            TimestampMs::new(4),
            &trace(),
            &mut events,
        )
        .expect("Context release succeeds");
    assert!(!control.reservations.contains_key(&resource));
}

/// A ContextRole conflict is rejected before the replacement reservation becomes Group-owned.
#[test]
fn context_bind_conflict_is_atomic() {
    let mut control = ControlPlane::new();
    let mut events = TestEvents;
    let plan = mission_plan(ResourceBindingScope::Context);
    let first = plan.task_graph().tasks()[0].requirement().task_ref().clone();
    let second = plan.task_graph().tasks()[1].requirement().task_ref().clone();
    let first_role = plan.task_graph().tasks()[0].requirement().roles()[0]
        .role_id()
        .clone();
    let second_role = plan.task_graph().tasks()[1].requirement().roles()[0]
        .role_id()
        .clone();
    let group = domain::ExecutionGroupId::new("group-context-atomic").expect("group valid");
    let context_id = plan.task_graph().tasks()[0].continuity().context_id().clone();
    let context_role_id = ContextRoleId::new("worker").expect("context role valid");
    let first_resource = ResourceId::new("context-first").expect("resource valid");
    let second_resource = ResourceId::new("context-second").expect("resource valid");
    control
        .create_mission_group(group.clone(), &plan, TimestampMs::new(1), &trace(), &mut events)
        .expect("Group creates");
    for (resource, task_ref, role_id) in [
        (first_resource.clone(), first.clone(), first_role.clone()),
        (second_resource.clone(), second.clone(), second_role.clone()),
    ] {
        control.reservations.insert(
            resource,
            crate::coordination::Reservation {
                task_ref,
                role_id,
                group_id: None,
                scope: ResourceBindingScope::Context,
                owner: domain::AllocationOwner::Context {
                    mission_id: first.mission_id().clone(),
                    context_id: context_id.clone(),
                    context_role_id: context_role_id.clone(),
                },
            },
        );
    }
    control
        .ready_task_execution(&group, &first, TimestampMs::new(2), &trace(), &mut events)
        .expect("first Task ready");
    control
        .bind_task_execution(
            &group,
            &CommittedPlan::new(
                first.clone(),
                vec![RoleAssignment::new(
                    first_role,
                    NodeId::new("node-first").expect("node valid"),
                    vec![first_resource.clone()],
                )],
            ),
            TimestampMs::new(3),
            &trace(),
            &mut events,
        )
        .expect("first Context binding succeeds");
    control
        .ready_task_execution(&group, &second, TimestampMs::new(4), &trace(), &mut events)
        .expect("second Task ready");
    assert!(control
        .bind_task_execution(
            &group,
            &CommittedPlan::new(
                second,
                vec![RoleAssignment::new(
                    second_role,
                    NodeId::new("node-second").expect("node valid"),
                    vec![second_resource.clone()],
                )],
            ),
            TimestampMs::new(5),
            &trace(),
            &mut events,
        )
        .is_err());
    assert_eq!(
        control
            .reservations
            .get(&second_resource)
            .expect("reservation retained")
            .group_id,
        None
    );
}

/// A role without an explicit scope still uses the documented Task-scoped default.
#[test]
fn omitted_role_scope_defaults_to_task() {
    let mut control = ControlPlane::new();
    let mut events = TestEvents;
    let plan = mission_plan_with_scope(ResourceBindingScope::Task, false);
    let task = plan.task_graph().tasks()[0].requirement().task_ref().clone();
    let role = plan.task_graph().tasks()[0].requirement().roles()[0]
        .role_id()
        .clone();
    let group = domain::ExecutionGroupId::new("group-default-scope").expect("group valid");
    let resource = ResourceId::new("default-task-resource").expect("resource valid");
    control
        .create_mission_group(group.clone(), &plan, TimestampMs::new(1), &trace(), &mut events)
        .expect("Group creates");
    control.reservations.insert(
        resource.clone(),
        crate::coordination::Reservation {
            task_ref: task.clone(),
            role_id: role.clone(),
            group_id: None,
            scope: ResourceBindingScope::Task,
            owner: domain::AllocationOwner::Task(task.clone()),
        },
    );
    control
        .ready_task_execution(&group, &task, TimestampMs::new(2), &trace(), &mut events)
        .expect("Task ready");
    control
        .bind_task_execution(
            &group,
            &CommittedPlan::new(
                task,
                vec![RoleAssignment::new(
                    role,
                    NodeId::new("node-default").expect("node valid"),
                    vec![resource],
                )],
            ),
            TimestampMs::new(3),
            &trace(),
            &mut events,
        )
        .expect("default Task scope should bind");
}
