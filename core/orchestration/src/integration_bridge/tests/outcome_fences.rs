//! Recovery fences retain physical evidence without satisfying replacement bindings.

use super::*;

/// Late completion is deferred while Blocked and cannot satisfy a newly rebound Node.
#[test]
fn rebound_task_requires_replacement_attempt_completion() {
    let now = TimestampMs::new(1);
    let correlation = CorrelationId::new("outcome-fence").expect("correlation");
    let mut events = InMemoryEventLog::new();
    let mut control = ControlPlane::new();
    let mut state = InMemorySharedNodeState::new();
    let node_a = NodeId::new("node-a").expect("Node");
    let node_b = NodeId::new("node-b").expect("Node");
    let contract = CapabilityContractRef::new("compute", "work", "v1").expect("contract");
    for node_id in [&node_a, &node_b] {
        let registration = domain::NodeRegistration::new_with_contracts(
            node_id.clone(),
            LocalRuntime::new("test", "1").expect("runtime"),
            NodeContractVersion::v0_4(),
            vec![Capability::new(CapabilityKind::Compute, true)],
            vec![contract.clone()],
            Vec::new(),
        );
        control
            .register_node(
                &mut state,
                registration,
                NodeStatus::new(NodeHealth::Online, now),
                now,
                &correlation,
                &mut events,
            )
            .expect("Node registers");
    }
    // Actor-free Control fixture exercises the existing Role recovery authority, independently
    // of the separate explicit Actor-rebind policy required by normalized Mission plans.
    let role = domain::RoleId::new("worker").expect("Role");
    let requirement = domain::TaskRequirement::new(
        domain::MissionId::new("mission").expect("Mission"),
        domain::TaskId::new("task").expect("Task"),
        vec![domain::RoleRequirement::new(
            role.clone(),
            CapabilityKind::Compute,
            None,
        )],
    )
    .expect("requirement");
    let intent = domain::ExecutionIntent::new(contract, BTreeMap::new()).expect("intent");
    let plan = single_task_plan(requirement.clone(), intent.clone());
    let group_id = domain::ExecutionGroupId::new("group").expect("Group");
    control
        .create_mission_group(group_id.clone(), &plan, now, &correlation, &mut events)
        .expect("Group created");
    control
        .ready_task_execution(
            &group_id,
            requirement.task_ref(),
            now,
            &correlation,
            &mut events,
        )
        .expect("Task ready");
    let candidates = control
        .match_capabilities(&state, &requirement, now, &correlation, &mut events)
        .expect("Match");
    let proposal = control
        .propose(
            &state,
            &requirement,
            &candidates,
            vec![domain::RoleAssignment::new(
                role.clone(),
                node_a.clone(),
                Vec::new(),
            )],
            now,
            &correlation,
            &mut events,
        )
        .expect("Propose");
    let committed = control
        .commit(&proposal, now, &correlation, &mut events)
        .expect("Commit");
    control
        .bind_task_execution_with_requirement(
            &group_id,
            &committed,
            &requirement,
            now,
            &correlation,
            &mut events,
        )
        .expect("Bind");
    control
        .activate_task_execution(
            &group_id,
            requirement.task_ref(),
            now,
            &correlation,
            &mut events,
        )
        .expect("activate");
    let mut bridge =
        IntegrationRuntimeBridge::new(control, state, events.clone(), GrpcNodeRouter::default());
    bridge
        .execute_task_bound(
            "old".into(),
            &group_id,
            requirement.task_ref(),
            &role,
            intent.clone(),
            now,
            correlation.clone(),
        )
        .expect("dispatch old");
    bridge
        .runtime
        .observe_execution(
            "old",
            node_a.clone(),
            1,
            ExecutionStatus::Unknown,
            "local ambiguity",
        )
        .expect("Unknown evidence");
    bridge
        .control
        .begin_execution_recovery(
            &group_id,
            requirement.task_ref(),
            &role,
            &node_a,
            now,
            &correlation,
            &mut events,
        )
        .expect("begin recovery");
    bridge
        .runtime
        .observe_execution(
            "old",
            node_a,
            2,
            ExecutionStatus::Completed,
            "late completion",
        )
        .expect("late evidence retained");
    assert!(bridge.terminal_task_execution_outcomes().is_empty());
    let need = bridge.control.pending_role_recoveries()[0].clone();
    let candidates = bridge
        .control
        .match_recovery_candidates(
            &bridge.state,
            &need,
            &requirement,
            now,
            &correlation,
            &mut events,
        )
        .expect("recovery Match");
    let proposal = bridge
        .control
        .propose_role_recovery(
            &bridge.state,
            &candidates,
            &requirement,
            node_b.clone(),
            Vec::new(),
            now,
            &correlation,
            &mut events,
        )
        .expect("recovery Propose");
    let committed = bridge
        .control
        .commit_role_recovery(
            &bridge.state,
            &requirement,
            &proposal,
            now,
            &correlation,
            &mut events,
        )
        .expect("recovery Commit");
    bridge
        .control
        .rebind_role(&committed, now, &correlation, &mut events)
        .expect("Rebind");
    assert!(
        bridge.terminal_task_execution_outcomes().is_empty(),
        "old completion is not replacement evidence"
    );
    bridge
        .execute_task_bound(
            "new".into(),
            &group_id,
            requirement.task_ref(),
            &role,
            intent,
            now,
            correlation,
        )
        .expect("dispatch replacement");
    assert!(bridge.terminal_task_execution_outcomes().is_empty());
    bridge
        .runtime
        .observe_execution(
            "new",
            node_b,
            1,
            ExecutionStatus::Completed,
            "replacement complete",
        )
        .expect("replacement evidence");
    let outcomes = bridge.terminal_task_execution_outcomes();
    assert_eq!(outcomes.len(), 1);
    assert_eq!(
        outcomes[0].result(),
        ObservedTaskExecutionResult::ExecutionCompleted
    );
    assert_eq!(
        bridge.execution_status("old"),
        Some(RemoteExecutionStatus::Completed)
    );
    assert_eq!(bridge.attempt_history().len(), 2);
}
