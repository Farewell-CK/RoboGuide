//! Dispatch recovery, cancellation, and Mission outcome tests.

use super::*;

/// Restore clears process-local authority and never implicitly re-dispatches a command.
#[test]
fn checkpoint_restore_is_conservative_across_process_boundary() {
    let mut bridge = IntegrationRuntimeBridge::new(
        ControlPlane::new(),
        InMemorySharedNodeState::new(),
        InMemoryEventLog::new(),
        GrpcNodeRouter::default(),
    );
    let correlation = CorrelationId::new("checkpoint-test").expect("correlation valid");
    bridge
        .consume(
            GrpcNodeEvent::Registered {
                session_id: "session-old".to_string(),
                lease_id: "lease-old".to_string(),
                registration: NodeRegistration {
                    node_id: "dog-a".to_string(),
                    local_systems: vec![LocalSystemDescriptor {
                        id: "motion".to_string(),
                        runtime: Some(WireRuntime {
                            name: "local-motion".to_string(),
                            version: "1".to_string(),
                        }),
                        metadata: Default::default(),
                    }],
                    capabilities: Vec::new(),
                    sensors: Vec::new(),
                    resources: Vec::new(),
                    metadata: Default::default(),
                    node_contract_version: "roboguide.node.v0.3".to_string(),
                    state_exports: Vec::new(),
                    memory_providers: Vec::new(),
                    capability_profiles: Vec::new(),
                    operation_support: Vec::new(),
                },
            },
            TimestampMs::new(100),
            &correlation,
        )
        .expect("registration consumed");
    let node_id = NodeId::new("dog-a").expect("node valid");
    let command = ExecutionCommand::new(
        domain::MissionId::new("mission").expect("mission valid"),
        domain::TaskId::new("task").expect("task valid"),
        domain::ExecutionGroupId::new("group").expect("group valid"),
        domain::RoleId::new("role").expect("role valid"),
        node_id.clone(),
        domain::ExecutionIntent::new(
            CapabilityContractRef::new("compute", "noop", "v1").expect("contract valid"),
            BTreeMap::new(),
        )
        .expect("intent valid"),
        correlation,
    );
    bridge
        .runtime
        .record_dispatched("execution-1".to_string(), command.clone(), Vec::new())
        .expect("execution dispatch records");
    bridge
        .runtime
        .observe_execution(
            "execution-1",
            node_id.clone(),
            4,
            ExecutionStatus::Running,
            "",
        )
        .expect("running fact records");

    let checkpoint = bridge.checkpoint_json().expect("checkpoint serializes");
    let mut restored = IntegrationRuntimeBridge::restore_from_checkpoint(
        &checkpoint,
        InMemoryEventLog::new(),
        GrpcNodeRouter::default(),
        TimestampMs::new(7),
    )
    .expect("checkpoint restores");

    assert!(restored.control().node_lease(&node_id).is_none());
    let snapshot = restored.state().node(&node_id).expect("node fact restored");
    assert_eq!(
        snapshot.reported_status_received_at(),
        TimestampMs::new(100),
        "restart must not make old reported health appear newly received"
    );
    assert_eq!(
        snapshot.liveness().liveness(),
        domain::NodeLiveness::Unreachable
    );
    assert_eq!(snapshot.liveness().observed_at(), TimestampMs::new(7));
    assert_eq!(
        restored.execution_status("execution-1"),
        Some(RemoteExecutionStatus::Unknown)
    );
    assert!(matches!(
        restored.execute("execution-1".to_string(), command, Vec::new()),
        Err(IntegrationRuntimeError::Protocol(reason)) if reason.contains("controller restart")
    ));
}

/// A crash after intent checkpoint but before receipt fences replay and emits recovery once.
#[test]
fn durable_dispatch_crash_window_restores_as_one_recovery_event() {
    let mut bridge = IntegrationRuntimeBridge::new(
        ControlPlane::new(),
        InMemorySharedNodeState::new(),
        InMemoryEventLog::new(),
        GrpcNodeRouter::default(),
    );
    let correlation = CorrelationId::new("dispatch-crash-test").expect("correlation valid");
    let command = ExecutionCommand::new(
        domain::MissionId::new("mission-crash").expect("mission valid"),
        domain::TaskId::new("task-crash").expect("task valid"),
        domain::ExecutionGroupId::new("group-crash").expect("group valid"),
        domain::RoleId::new("carrier").expect("role valid"),
        NodeId::new("dog-a").expect("node valid"),
        domain::ExecutionIntent::new(
            CapabilityContractRef::new("mobility", "move", "v1").expect("contract valid"),
            BTreeMap::new(),
        )
        .expect("intent valid"),
        correlation.clone(),
    );
    bridge
        .execute("attempt-crash".to_string(), command, Vec::new())
        .expect("intent prepares without routing");
    let checkpoint = bridge.checkpoint_json().expect("intent checkpoints");

    let mut restored = IntegrationRuntimeBridge::restore_from_checkpoint(
        &checkpoint,
        InMemoryEventLog::new(),
        GrpcNodeRouter::default(),
        TimestampMs::new(10),
    )
    .expect("intent restores conservatively");
    assert_eq!(
        restored.execution_status("attempt-crash"),
        Some(RemoteExecutionStatus::Unknown)
    );
    assert_eq!(
        restored
            .flush_dispatch_outbox()
            .expect("Unknown intent is not eligible to route"),
        0
    );
    restored
        .tick(TimestampMs::new(11), &correlation)
        .expect("first post-restore tick succeeds");
    assert!(matches!(
        restored.take_runtime_events().as_slice(),
        [ExecutionEvent::RecoveryRequired { execution_id, .. }]
            if execution_id == "attempt-crash"
    ));
    restored
        .tick(TimestampMs::new(12), &correlation)
        .expect("later tick succeeds");
    assert!(restored.take_runtime_events().is_empty());
}

/// Group cancellation includes superseded ambiguous attempts and waits for every terminal fact.
#[test]
fn group_cancellation_covers_rebind_attempt_history() {
    let mut bridge = IntegrationRuntimeBridge::new(
        ControlPlane::new(),
        InMemorySharedNodeState::new(),
        InMemoryEventLog::new(),
        GrpcNodeRouter::default(),
    );
    let correlation = CorrelationId::new("cancel-history-test").expect("correlation valid");
    let group_id = domain::ExecutionGroupId::new("group-cancel").expect("group valid");
    let original = ExecutionCommand::new(
        domain::MissionId::new("mission-cancel").expect("mission valid"),
        domain::TaskId::new("task-cancel").expect("task valid"),
        group_id.clone(),
        domain::RoleId::new("carrier").expect("role valid"),
        NodeId::new("dog-a").expect("node valid"),
        domain::ExecutionIntent::new(
            CapabilityContractRef::new("mobility", "move", "v1").expect("contract valid"),
            BTreeMap::new(),
        )
        .expect("intent valid"),
        correlation.clone(),
    );
    bridge
        .runtime
        .prepare_dispatch("attempt-old".to_string(), original.clone(), Vec::new())
        .expect("old attempt prepares");
    assert_eq!(
        bridge
            .runtime
            .observe_node_unavailable(original.node_id(), "route lost")
            .len(),
        1
    );
    let replacement = ExecutionCommand::new(
        original.mission_id().clone(),
        original.task_ref().task_id().clone(),
        group_id.clone(),
        original.role_id().clone(),
        NodeId::new("dog-b").expect("replacement node valid"),
        original.intent().clone(),
        correlation,
    );
    bridge
        .runtime
        .prepare_dispatch(
            "attempt-replacement".to_string(),
            replacement.clone(),
            Vec::new(),
        )
        .expect("replacement attempt prepares");

    assert_eq!(
        bridge
            .request_group_cancellation(&group_id)
            .expect("cancellation records"),
        2
    );
    assert_eq!(bridge.runtime.pending_cancellations().len(), 2);
    assert!(!bridge.group_attempts_terminal(&group_id));
    bridge
        .runtime
        .observe_cancellation_receipt(
            "attempt-replacement",
            "cancel-attempt-replacement",
            replacement.node_id(),
            true,
            "persisted",
        )
        .expect("nonterminal receipt is accepted");
    assert_eq!(
        bridge
            .flush_dispatch_outbox()
            .expect("fact-driven flush must not retry Cancel"),
        0,
    );
    assert_eq!(
        bridge.runtime.pending_cancellations().len(),
        2,
        "timer retains cancellation retry authority"
    );
    bridge
        .runtime
        .observe_execution(
            "attempt-replacement",
            replacement.node_id().clone(),
            1,
            ExecutionStatus::Cancelled,
            "replacement cancelled",
        )
        .expect("replacement terminal fact records");
    assert!(!bridge.group_attempts_terminal(&group_id));
    bridge
        .runtime
        .observe_execution(
            "attempt-old",
            original.node_id().clone(),
            1,
            ExecutionStatus::Cancelled,
            "old ambiguous attempt cancelled",
        )
        .expect("old terminal fact records");
    assert!(bridge.group_attempts_terminal(&group_id));
    assert!(bridge.runtime.pending_cancellations().is_empty());
}

/// Group aggregation and dispatch validation follow the current TaskExecution.
#[test]
fn mission_dispatch_and_outcomes_use_current_task_execution() {
    let now = TimestampMs::new(0);
    let correlation = CorrelationId::new("current-execution-test").expect("correlation valid");
    let contract = CapabilityContractRef::new("mobility", "move", "v1").expect("contract valid");
    let registration = domain::NodeRegistration::new_with_contracts(
        NodeId::new("node-a").expect("node valid"),
        LocalRuntime::new("runtime", "1").expect("runtime valid"),
        NodeContractVersion::v0_1(),
        vec![Capability::new(CapabilityKind::Mobility, true)],
        vec![contract.clone()],
        Vec::new(),
    );
    let mut control = ControlPlane::new();
    let mut state = InMemorySharedNodeState::new();
    let mut events = InMemoryEventLog::new();
    control
        .register_node(
            &mut state,
            registration,
            NodeStatus::new(NodeHealth::Online, now),
            now,
            &correlation,
            &mut events,
        )
        .expect("node registers");
    let role_id = domain::RoleId::new("carrier").expect("role valid");
    let requirement = domain::TaskRequirement::new(
        domain::MissionId::new("mission-a").expect("mission valid"),
        domain::TaskId::new("task-a").expect("task valid"),
        vec![domain::RoleRequirement::new_with_actor_and_contract(
            role_id.clone(),
            domain::ActorId::new("carrier").expect("actor valid"),
            CapabilityKind::Mobility,
            contract.clone(),
            None,
        )],
    )
    .expect("requirement valid");
    let intent = domain::ExecutionIntent::new(contract, BTreeMap::new()).expect("intent valid");
    let mission_plan = single_task_plan(requirement.clone(), intent.clone());
    let group_id = domain::ExecutionGroupId::new("group-a").expect("group valid");
    control
        .create_mission_group(
            group_id.clone(),
            &mission_plan,
            now,
            &correlation,
            &mut events,
        )
        .expect("Mission Group registers");
    control
        .ready_task_execution(
            &group_id,
            requirement.task_ref(),
            now,
            &correlation,
            &mut events,
        )
        .expect("Task becomes ready");
    let candidates = control
        .match_capabilities(&state, &requirement, now, &correlation, &mut events)
        .expect("matching succeeds");
    let proposal = control
        .propose(
            &state,
            &requirement,
            &candidates,
            vec![domain::RoleAssignment::new(
                role_id.clone(),
                NodeId::new("node-a").expect("node valid"),
                Vec::new(),
            )],
            now,
            &correlation,
            &mut events,
        )
        .expect("proposal succeeds");
    let committed = control
        .commit(&proposal, now, &correlation, &mut events)
        .expect("commit succeeds");
    control
        .bind_task_execution_with_requirement(
            &group_id,
            &committed,
            &requirement,
            now,
            &correlation,
            &mut events,
        )
        .expect("Task binds");
    control
        .activate_task_execution(
            &group_id,
            requirement.task_ref(),
            now,
            &correlation,
            &mut events,
        )
        .expect("Task activates");
    let command = ExecutionCommand::new(
        domain::MissionId::new("mission-a").expect("mission valid"),
        domain::TaskId::new("task-a").expect("task valid"),
        group_id.clone(),
        role_id.clone(),
        NodeId::new("node-a").expect("node valid"),
        intent.clone(),
        correlation.clone(),
    );
    let mut bridge =
        IntegrationRuntimeBridge::new(control, state, events, GrpcNodeRouter::default());
    assert!(matches!(
        bridge.execute_bound(
            "execution-ambiguous".to_string(),
            &group_id,
            &role_id,
            intent.clone(),
            correlation.clone(),
        ),
        Err(IntegrationRuntimeError::Protocol(reason)) if reason.contains("explicit TaskRef")
    ));
    bridge
        .runtime
        .record_dispatched("execution-old".to_string(), command.clone(), Vec::new())
        .expect("old execution records");
    bridge
        .runtime
        .observe_execution(
            "execution-old",
            command.node_id().clone(),
            1,
            ExecutionStatus::Failed,
            "old failure",
        )
        .expect("old failure records");
    bridge
        .runtime
        .record_dispatched("execution-current".to_string(), command.clone(), Vec::new())
        .expect("current execution records");
    bridge
        .runtime
        .observe_execution(
            "execution-current",
            command.node_id().clone(),
            1,
            ExecutionStatus::Running,
            "",
        )
        .expect("current running fact records");

    assert!(bridge.terminal_task_execution_outcomes().is_empty());
    assert_eq!(
        bridge
            .control()
            .group(&group_id)
            .expect("group exists")
            .lifecycle(),
        control::GroupLifecycle::Active
    );
    bridge
        .runtime
        .observe_execution(
            "execution-current",
            command.node_id().clone(),
            2,
            ExecutionStatus::Completed,
            "",
        )
        .expect("current completion records");
    let outcomes = bridge.terminal_task_execution_outcomes();
    assert_eq!(outcomes.len(), 1);
    assert_eq!(outcomes[0].group_id(), &group_id);
    assert_eq!(outcomes[0].task_ref(), requirement.task_ref());
    assert_eq!(
        outcomes[0].result(),
        ObservedTaskExecutionResult::ExecutionCompleted
    );
    assert_eq!(
        bridge
            .control()
            .group(&group_id)
            .expect("group exists")
            .lifecycle(),
        control::GroupLifecycle::Active
    );
    {
        let (control, events) = (&mut bridge.control, &mut bridge.events);
        control
            .record_task_execution_completed(
                &group_id,
                requirement.task_ref(),
                now,
                &correlation,
                events,
            )
            .expect("Task execution completion records");
        control
            .satisfy_task_execution(
                &group_id,
                requirement.task_ref(),
                domain::TaskSatisfactionBasis::ExecutionReport,
                now,
                &correlation,
                events,
            )
            .expect("Task satisfaction records");
    }
    assert!(matches!(
        bridge.execute_task_bound(
            "execution-after-completion".to_string(),
            &group_id,
            requirement.task_ref(),
            &role_id,
            intent,
            now,
            correlation,
        ),
        Err(IntegrationRuntimeError::Protocol(reason)) if reason.contains("not dispatchable")
    ));
}

/// Partial recovery cannot dispatch or complete a multi-role Task from its surviving role.
#[test]
fn incomplete_multi_role_task_does_not_dispatch_or_report_terminal_outcome() {
    let now = TimestampMs::new(0);
    let correlation = CorrelationId::new("partial-multi-role-test").expect("correlation valid");
    let contract = CapabilityContractRef::new("mobility", "move", "v1").expect("contract valid");
    let node_id = NodeId::new("node-a").expect("node valid");
    let registration = domain::NodeRegistration::new_with_contracts(
        node_id.clone(),
        LocalRuntime::new("runtime", "1").expect("runtime valid"),
        NodeContractVersion::v0_1(),
        vec![Capability::new(CapabilityKind::Mobility, true)],
        vec![contract.clone()],
        Vec::new(),
    );
    let mut control = ControlPlane::new();
    let mut state = InMemorySharedNodeState::new();
    let mut events = InMemoryEventLog::new();
    control
        .register_node(
            &mut state,
            registration,
            NodeStatus::new(NodeHealth::Online, now),
            now,
            &correlation,
            &mut events,
        )
        .expect("node registers");
    let retained_role = domain::RoleId::new("carrier").expect("role valid");
    let released_role = domain::RoleId::new("observer").expect("role valid");
    let requirement = domain::TaskRequirement::new(
        domain::MissionId::new("mission-multi").expect("mission valid"),
        domain::TaskId::new("task-multi").expect("task valid"),
        vec![
            domain::RoleRequirement::new_with_actor_and_contract(
                retained_role.clone(),
                domain::ActorId::new("carrier").expect("actor valid"),
                CapabilityKind::Mobility,
                contract.clone(),
                None,
            ),
            domain::RoleRequirement::new_with_actor_and_contract(
                released_role.clone(),
                domain::ActorId::new("observer").expect("actor valid"),
                CapabilityKind::Mobility,
                contract.clone(),
                None,
            ),
        ],
    )
    .expect("requirement valid");
    let intent = domain::ExecutionIntent::new(contract, BTreeMap::new()).expect("intent valid");
    let mission_plan = single_task_plan(requirement.clone(), intent.clone());
    let group_id = domain::ExecutionGroupId::new("group-multi").expect("group valid");
    control
        .create_mission_group(
            group_id.clone(),
            &mission_plan,
            now,
            &correlation,
            &mut events,
        )
        .expect("Mission Group registers");
    control
        .ready_task_execution(
            &group_id,
            requirement.task_ref(),
            now,
            &correlation,
            &mut events,
        )
        .expect("Task becomes ready");
    let candidates = control
        .match_capabilities(&state, &requirement, now, &correlation, &mut events)
        .expect("matching succeeds");
    let proposal = control
        .propose(
            &state,
            &requirement,
            &candidates,
            vec![
                domain::RoleAssignment::new(retained_role.clone(), node_id.clone(), Vec::new()),
                domain::RoleAssignment::new(released_role.clone(), node_id.clone(), Vec::new()),
            ],
            now,
            &correlation,
            &mut events,
        )
        .expect("proposal succeeds");
    let committed = control
        .commit(&proposal, now, &correlation, &mut events)
        .expect("commit succeeds");
    control
        .bind_task_execution_with_requirement(
            &group_id,
            &committed,
            &requirement,
            now,
            &correlation,
            &mut events,
        )
        .expect("Task binds");
    control
        .activate_task_execution(
            &group_id,
            requirement.task_ref(),
            now,
            &correlation,
            &mut events,
        )
        .expect("Task activates");
    control
        .block_group(
            &group_id,
            "observer unavailable",
            now,
            &correlation,
            &mut events,
        )
        .expect("Group blocks for recovery");
    control
        .release_task_role_binding(
            &group_id,
            requirement.task_ref(),
            &released_role,
            now,
            &correlation,
            &mut events,
        )
        .expect("failed role releases");
    let execution = control
        .group(&group_id)
        .and_then(|group| group.task_execution(requirement.task_ref()))
        .expect("Task remains registered");
    assert_eq!(
        execution.lifecycle(),
        domain::TaskExecutionLifecycle::Active
    );
    assert!(!task_assignments_are_complete(execution));

    let command = ExecutionCommand::new(
        requirement.mission_id().clone(),
        requirement.task_id().clone(),
        group_id.clone(),
        retained_role.clone(),
        node_id.clone(),
        intent.clone(),
        correlation.clone(),
    );
    let mut bridge =
        IntegrationRuntimeBridge::new(control, state, events, GrpcNodeRouter::default());
    bridge
        .runtime
        .record_dispatched("execution-retained".to_string(), command, Vec::new())
        .expect("retained role execution records");
    bridge
        .runtime
        .observe_execution(
            "execution-retained",
            node_id,
            1,
            ExecutionStatus::Completed,
            "",
        )
        .expect("retained role completion records");

    assert!(bridge.terminal_task_execution_outcomes().is_empty());
    assert!(matches!(
        bridge.execute_task_bound(
            "execution-during-recovery".to_string(),
            &group_id,
            requirement.task_ref(),
            &retained_role,
            intent,
            now,
            correlation,
        ),
        Err(IntegrationRuntimeError::Protocol(reason)) if reason.contains("not bound")
    ));
    assert_eq!(bridge.execution_status("execution-during-recovery"), None);
}

/// Assignment completeness requires exact role coverage without duplicate role entries.
#[test]
fn task_assignment_completeness_rejects_missing_and_duplicate_roles() {
    let first_role = domain::RoleId::new("first").expect("role valid");
    let second_role = domain::RoleId::new("second").expect("role valid");
    let node_id = NodeId::new("node-a").expect("node valid");
    let execution = domain::TaskExecution::new(
        domain::TaskRef::new(
            domain::MissionId::new("mission-coverage").expect("mission valid"),
            domain::TaskId::new("task-coverage").expect("task valid"),
        ),
        domain::CoordinationContextId::new("context-coverage").expect("context valid"),
        BTreeMap::new(),
        BTreeMap::from([
            (first_role.clone(), domain::ResourceBindingScope::Task),
            (second_role.clone(), domain::ResourceBindingScope::Task),
        ]),
    );
    let first_assignment =
        domain::RoleAssignment::new(first_role.clone(), node_id.clone(), Vec::new());
    let second_assignment = domain::RoleAssignment::new(second_role, node_id.clone(), Vec::new());

    assert!(task_assignments_are_complete(&execution.with_assignments(
        vec![first_assignment.clone(), second_assignment]
    )));
    assert!(!task_assignments_are_complete(
        &execution.with_assignments(vec![first_assignment.clone()])
    ));
    assert!(!task_assignments_are_complete(&execution.with_assignments(
        vec![
            first_assignment.clone(),
            domain::RoleAssignment::new(first_role, node_id, Vec::new()),
        ]
    )));
}

/// Builds one reconnect snapshot event with a validated execution phase.
pub(super) fn execution_snapshot(
    node_id: &str,
    execution_id: &str,
    sequence: u64,
    phase: ExecutionPhase,
) -> GrpcNodeEvent {
    execution_snapshot_with_phase(node_id, execution_id, sequence, phase as i32)
}

/// Builds one reconnect snapshot event with an arbitrary raw phase value.
pub(super) fn execution_snapshot_with_phase(
    node_id: &str,
    execution_id: &str,
    sequence: u64,
    phase: i32,
) -> GrpcNodeEvent {
    GrpcNodeEvent::NodeMessage {
        node_id: node_id.to_string(),
        session_id: "session-test".to_string(),
        message: integration::grpc::v0_4::NodeMessage {
            message: Some(NodePayload::ExecutionSnapshot(
                integration::grpc::v0_4::ExecutionSnapshot {
                    session_id: "session-test".to_string(),
                    execution_id: execution_id.to_string(),
                    last_sequence: sequence,
                    phase,
                    reason: String::new(),
                },
            )),
        },
    }
}
