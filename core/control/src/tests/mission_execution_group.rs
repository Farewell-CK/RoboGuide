/// A Mission-level Group retains completed Task execution units and remains usable.
#[test]
fn mission_group_hosts_many_tasks_without_task_completion_releasing_group() {
    let mut control = ControlPlane::new();
    let mut events = TestEvents;
    let mission_id = domain::MissionId::new("mission-long-lived").expect("mission id valid");
    let group_id = domain::ExecutionGroupId::new("group-long-lived").expect("group id valid");
    let first_task = domain::TaskRef::new(
        mission_id.clone(),
        domain::TaskId::new("task-a").expect("task id valid"),
    );
    let context_id = domain::CoordinationContextId::new("context-main")
        .expect("context id valid");
    control
        .create_mission_group(
            group_id.clone(),
            mission_id.clone(),
            first_task.clone(),
            TimestampMs::new(1),
            &CorrelationId::new("mission-group-test").expect("correlation valid"),
            &mut events,
        )
        .expect("mission group should be created");
    let role = RoleId::new("carrier").expect("role id valid");
    let node = NodeId::new("node-a").expect("node id valid");
    let plan_a = CommittedPlan::new(
        first_task.clone(),
        vec![RoleAssignment::new(role.clone(), node.clone(), Vec::new())],
    );
    let task_a = control
        .register_task_execution(
            &group_id,
            &plan_a,
            context_id.clone(),
            TimestampMs::new(2),
            &CorrelationId::new("mission-group-test").expect("correlation valid"),
            &mut events,
        )
        .expect("first task should register");
    assert_eq!(task_a.lifecycle(), domain::TaskExecutionLifecycle::Pending);
    control
        .ready_task_execution(
            &group_id,
            &first_task,
            TimestampMs::new(3),
            &CorrelationId::new("mission-group-test").expect("correlation valid"),
            &mut events,
        )
        .expect("first task should become ready");
    control
        .activate_task_execution(
            &group_id,
            &first_task,
            TimestampMs::new(4),
            &CorrelationId::new("mission-group-test").expect("correlation valid"),
            &mut events,
        )
        .expect("first task should activate");
    control
        .complete_task_execution(
            &group_id,
            &first_task,
            TimestampMs::new(5),
            &CorrelationId::new("mission-group-test").expect("correlation valid"),
            &mut events,
        )
        .expect("first task should complete");

    let second_task = domain::TaskRef::new(
        mission_id,
        domain::TaskId::new("task-b").expect("task id valid"),
    );
    let plan_b = CommittedPlan::new(
        second_task.clone(),
        vec![RoleAssignment::new(role, node, Vec::new())],
    );
    control
        .register_task_execution(
            &group_id,
            &plan_b,
            context_id,
            TimestampMs::new(6),
            &CorrelationId::new("mission-group-test").expect("correlation valid"),
            &mut events,
        )
        .expect("second task should register in the same group");

    let group = control.group(&group_id).expect("group remains alive");
    assert_eq!(group.lifecycle(), GroupLifecycle::Active);
    assert_eq!(
        group
            .task_execution(&first_task)
            .expect("first task retained")
            .lifecycle(),
        domain::TaskExecutionLifecycle::Completed
    );
    assert_eq!(
        group
            .task_execution(&second_task)
            .expect("second task retained")
            .lifecycle(),
        domain::TaskExecutionLifecycle::Pending
    );
}

/// Mission Task registration must consume an existing Control reservation and preserve allocation
/// projection ownership for every Task in the long-lived Group.
#[test]
fn mission_task_registration_uses_commit_authority_and_releases_exact_bindings() {
    let mut control = ControlPlane::new();
    let mut events = TestEvents;
    let mission = domain::MissionId::new("mission-authority").expect("mission valid");
    let task = domain::TaskRef::new(
        mission.clone(),
        domain::TaskId::new("task-authority").expect("task valid"),
    );
    let group = domain::ExecutionGroupId::new("group-authority").expect("group valid");
    let role = RoleId::new("worker").expect("role valid");
    let node = NodeId::new("node-worker").expect("node valid");
    let resource = ResourceId::new("compute-worker").expect("resource valid");
    control
        .create_mission_group(
            group.clone(),
            mission,
            task.clone(),
            TimestampMs::new(1),
            &CorrelationId::new("authority-test").expect("correlation valid"),
            &mut events,
        )
        .expect("group creates");
    control.reservations.insert(
        resource.clone(),
        crate::coordination::Reservation {
            task_ref: task.clone(),
            role_id: role.clone(),
            group_id: None,
            scope: domain::ResourceBindingScope::Task,
        },
    );
    let plan = CommittedPlan::new(
        task.clone(),
        vec![RoleAssignment::new(role, node, vec![resource.clone()])],
    );
    control
        .register_task_execution(
            &group,
            &plan,
            domain::CoordinationContextId::new("context-authority").expect("context valid"),
            TimestampMs::new(2),
            &CorrelationId::new("authority-test").expect("correlation valid"),
            &mut events,
        )
        .expect("committed plan binds");
    assert_eq!(
        control
            .allocation_snapshot(TimestampMs::new(2))
            .expect("allocation projects")
            .allocations()
            .len(),
        1
    );
    assert!(control
        .activate_task_execution(
            &group,
            &task,
            TimestampMs::new(3),
            &CorrelationId::new("authority-test").expect("correlation valid"),
            &mut events,
        )
        .is_err());
    control
        .ready_task_execution(
            &group,
            &task,
            TimestampMs::new(3),
            &CorrelationId::new("authority-test").expect("correlation valid"),
            &mut events,
        )
        .expect("Task becomes ready");
    control
        .activate_task_execution(
            &group,
            &task,
            TimestampMs::new(4),
            &CorrelationId::new("authority-test").expect("correlation valid"),
            &mut events,
        )
        .expect("Task activates");
    control
        .complete_task_execution(
            &group,
            &task,
            TimestampMs::new(5),
            &CorrelationId::new("authority-test").expect("correlation valid"),
            &mut events,
        )
        .expect("Task completes");
    control
        .release_task_bindings(
            &group,
            &task,
            &[resource],
            TimestampMs::new(6),
            &CorrelationId::new("authority-test").expect("correlation valid"),
            &mut events,
        )
        .expect("Task resources release");
    assert!(control
        .allocation_snapshot(TimestampMs::new(6))
        .expect("allocation projects after release")
        .allocations()
        .is_empty());
}

/// Context-scoped resources survive Task completion and are released only at Context end.
#[test]
fn context_scope_outlives_task_and_releases_explicitly() {
    let mut control = ControlPlane::new();
    let mut events = TestEvents;
    let mission = domain::MissionId::new("mission-context").expect("mission valid");
    let task = domain::TaskRef::new(
        mission.clone(),
        domain::TaskId::new("task-context").expect("task valid"),
    );
    let group = domain::ExecutionGroupId::new("group-context").expect("group valid");
    let role = RoleId::new("worker").expect("role valid");
    let resource = ResourceId::new("context-compute").expect("resource valid");
    control
        .create_mission_group(
            group.clone(),
            mission,
            task.clone(),
            TimestampMs::new(1),
            &CorrelationId::new("context-test").expect("correlation valid"),
            &mut events,
        )
        .expect("group creates");
    control.reservations.insert(
        resource.clone(),
        crate::coordination::Reservation {
            task_ref: task.clone(),
            role_id: role.clone(),
            group_id: None,
            scope: domain::ResourceBindingScope::Task,
        },
    );
    let plan = CommittedPlan::new(
        task.clone(),
        vec![RoleAssignment::new(
            role.clone(),
            NodeId::new("node-context").expect("node valid"),
            vec![resource.clone()],
        )],
    );
    let context = domain::CoordinationContextId::new("context-long").expect("context valid");
    control
        .register_task_execution(
            &group,
            &plan,
            context.clone(),
            TimestampMs::new(2),
            &CorrelationId::new("context-test").expect("correlation valid"),
            &mut events,
        )
        .expect("Task registers");
    control
        .set_binding_scope(
            &group,
            &task,
            &resource,
            domain::ResourceBindingScope::Context,
        )
        .expect("Context scope is declared");
    control
        .release_context_bindings(
            &group,
            &context,
            TimestampMs::new(3),
            &CorrelationId::new("context-test").expect("correlation valid"),
            &mut events,
        )
        .expect("Context release succeeds");
    assert!(!control.reservations.contains_key(&resource));
}
