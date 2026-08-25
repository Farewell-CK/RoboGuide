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
    let task_a = control
        .register_task_execution(
            &group_id,
            first_task.clone(),
            context_id.clone(),
            vec![RoleAssignment::new(role.clone(), node.clone(), Vec::new())],
            TimestampMs::new(2),
            &CorrelationId::new("mission-group-test").expect("correlation valid"),
            &mut events,
        )
        .expect("first task should register");
    assert_eq!(task_a.lifecycle(), domain::TaskExecutionLifecycle::Pending);
    control
        .activate_task_execution(
            &group_id,
            &first_task,
            TimestampMs::new(3),
            &CorrelationId::new("mission-group-test").expect("correlation valid"),
            &mut events,
        )
        .expect("first task should activate");
    control
        .complete_task_execution(
            &group_id,
            &first_task,
            TimestampMs::new(4),
            &CorrelationId::new("mission-group-test").expect("correlation valid"),
            &mut events,
        )
        .expect("first task should complete");

    let second_task = domain::TaskRef::new(
        mission_id,
        domain::TaskId::new("task-b").expect("task id valid"),
    );
    control
        .register_task_execution(
            &group_id,
            second_task.clone(),
            context_id,
            vec![RoleAssignment::new(role, node, Vec::new())],
            TimestampMs::new(5),
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
