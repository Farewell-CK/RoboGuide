/// Builds one v0.6 registration with independent capability and operation evidence.
fn operation_support_node(
    node_id: &str,
    operation_name: &str,
) -> domain::NodeRegistration {
    let local_system_id = domain::LocalSystemId::new("local-eaios").expect("owner is valid");
    let capability = CapabilityContractRef::new("object", "relocate", "v1")
        .expect("capability is valid");
    NodeRegistration::new_with_local_systems_and_readiness(
        NodeId::new(node_id).expect("node is valid"),
        vec![domain::LocalSystemDescriptor::new(
            local_system_id.clone(),
            domain::LocalRuntime::new("test-eaios", "1").expect("runtime is valid"),
            BTreeMap::new(),
        )],
        domain::NodeContractVersion::v0_6(),
        vec![Capability::new(CapabilityKind::Transport, true)],
        BTreeMap::from([(capability.clone(), local_system_id.clone())]),
        BTreeMap::from([(capability.clone(), CapabilityKind::Transport)]),
        BTreeMap::from([(capability, true)]),
        Vec::new(),
        Vec::new(),
        BTreeMap::new(),
    )
    .expect("registration is valid")
    .with_operation_support(vec![domain::OperationSupport::new(
        domain::OperationRef::new("object", operation_name, "v1").expect("operation is valid"),
        local_system_id,
    )])
    .expect("operation support is valid")
}

/// Builds one normalized semantic Task whose capability and operation happen to share an identity.
fn operation_support_plan() -> (MissionPlan, TaskRequirement, RoleId) {
    let mission_id = domain::MissionId::new("mission-operation-support").expect("mission is valid");
    let role_id = RoleId::new("delivery").expect("role is valid");
    let capability = CapabilityContractRef::new("object", "relocate", "v1")
        .expect("capability is valid");
    let requirement = TaskRequirement::new(
        mission_id.clone(),
        TaskId::new("relocate").expect("task is valid"),
        vec![RoleRequirement::new_normalized(
            role_id.clone(),
            None,
            vec![domain::CapabilityRequirement::exact(capability)],
            Vec::new(),
        )
        .expect("role is valid")],
    )
    .expect("requirement is valid");
    let context_id = domain::CoordinationContextId::new("delivery-context")
        .expect("context is valid");
    let task = PlannedTask::new(
        "relocate the object",
        requirement.clone(),
        BTreeMap::from([(
            role_id.clone(),
            ExecutionIntent::new_semantic(
                domain::OperationRef::new("object", "relocate", "v1")
                    .expect("operation is valid"),
                "relocate the object to reception",
                BTreeMap::new(),
            )
            .expect("intent is valid"),
        )]),
        Vec::new(),
        domain::TaskContinuity::new(context_id.clone(), BTreeMap::new(), BTreeMap::new()),
    )
    .expect("planned task is valid");
    let plan = MissionPlan::new(
        MissionGoal::new(mission_id.clone(), "relocate the object").expect("goal is valid"),
        TaskGraph::new(mission_id, vec![task]).expect("graph is valid"),
        vec![domain::CoordinationContext::new(context_id, Vec::new())
            .expect("context is valid")],
    )
    .expect("plan is valid");
    (plan, requirement, role_id)
}

/// Replaces one registration while preserving the independently observed health and liveness.
fn replace_operation_registration(
    state: &mut InMemorySharedNodeState,
    registration: NodeRegistration,
) {
    let current = state
        .node(registration.node_id())
        .expect("node exists")
        .clone();
    state
        .record_node(domain::NodeStateSnapshot::new(
            registration,
            current.reported_status(),
            current.reported_status_received_at(),
            current.liveness(),
        ))
        .expect("registration update is accepted");
}

/// Match rejects a capability-compatible Node that cannot receive the Role operation.
#[test]
fn mission_matching_requires_independent_operation_support() {
    let (plan, requirement, role_id) = operation_support_plan();
    let timestamp = TimestampMs::new(0);
    let correlation = CorrelationId::new("operation-match").expect("correlation is valid");
    let mut control = ControlPlane::new();
    let mut state = InMemorySharedNodeState::new();
    let mut events = TestEvents;
    for registration in [
        operation_support_node("node-a", "relocate"),
        operation_support_node("node-b", "inspect"),
    ] {
        control
            .register_node(
                &mut state,
                registration,
                NodeStatus::new(NodeHealth::Online, timestamp),
                timestamp,
                &correlation,
                &mut events,
            )
            .expect("node registers");
    }

    let candidates = control
        .match_capabilities_for_mission(
            &state,
            &plan,
            &requirement,
            timestamp,
            &correlation,
            &mut events,
        )
        .expect("operation-aware matching succeeds");

    assert_eq!(
        candidates
            .for_role(&role_id)
            .expect("role candidates exist")
            .node_ids(),
        &[NodeId::new("node-a").expect("node is valid")]
    );
    assert_eq!(
        candidates
            .operation_for_role(&role_id)
            .expect("operation constraint is retained")
            .to_string(),
        "object.relocate@v1"
    );
}

/// Diagnostics and actual matching share one predicate across health, time and contract faults.
#[test]
fn first_use_diagnostics_match_actual_eligibility_without_mutation() {
    use crate::CandidateExclusionReason as Reason;
    for (fault, expected) in [
        ("healthy", None), ("operation", Some(Reason::OperationUnsupported)),
        ("health", Some(Reason::HealthUnschedulable)), ("stale", Some(Reason::StatusStale)),
        ("lease", Some(Reason::LeaseInactive)), ("liveness", Some(Reason::LivenessUnreachable)),
        ("capability", Some(Reason::RoleContractUnavailable)),
    ] {
        let (plan, requirement, role_id) = operation_support_plan();
        let mut control = ControlPlane::new();
        let mut state = InMemorySharedNodeState::new();
        let mut events = RecordingEvents { records: Vec::new() };
        let node = NodeId::new("node-a").unwrap();
        control.register_node(&mut state, operation_support_node("node-a", if fault == "operation" { "inspect" } else { "relocate" }),
            NodeStatus::new(NodeHealth::Online, TimestampMs::new(0)), TimestampMs::new(0), &correlation(), &mut events).unwrap();
        let now = if fault == "lease" { TimestampMs::new(16000) } else if fault == "stale" { TimestampMs::new(6000) } else { TimestampMs::new(0) };
        if fault == "health" || fault == "lease" {
            state.record_node_health(domain::NodeHealthObservation::new(node.clone(),
                NodeStatus::new(if fault == "health" { NodeHealth::Offline } else { NodeHealth::Online }, now), now)).unwrap();
        }
        if fault == "liveness" {
            state.record_node_liveness(&node, domain::NodeLivenessObservation::new(domain::NodeLiveness::Unreachable, now)).unwrap();
        }
        if fault == "capability" {
            replace_operation_registration(&mut state, registration("node-a", CapabilityKind::Compute, "space-a"));
        }
        let before = serde_json::to_string(&control.checkpoint()).unwrap();
        let event_count = events.records.len();
        let report = control.first_use_candidate_diagnostics(&state, &plan, &requirement, now).unwrap().remove(0);
        assert_eq!(report.considered_count, 1);
        assert_eq!(report.eligible_count, usize::from(expected.is_none()), "{fault}");
        assert_eq!(report.exclusions, expected.map(|reason| BTreeMap::from([(reason, 1)])).unwrap_or_default(), "{fault}");
        assert_eq!(serde_json::to_string(&control.checkpoint()).unwrap(), before);
        assert_eq!(events.records.len(), event_count);
        let result = control.match_capabilities_for_mission(&state, &plan, &requirement, now, &correlation(), &mut events);
        if expected.is_none() {
            assert_eq!(result.unwrap().for_role(&role_id).unwrap().node_ids().len(), report.eligible_count);
        } else {
            assert!(matches!(result, Err(ControlError::NoCandidate(_))), "{fault}");
        }
    }
}

/// Proposal and Commit reject operation support removed after their preceding stage.
#[test]
fn proposal_and_commit_revalidate_current_operation_support() {
    let (plan, requirement, role_id) = operation_support_plan();
    let timestamp = TimestampMs::new(0);
    let correlation = CorrelationId::new("operation-revalidate").expect("correlation is valid");
    let assignment = domain::RoleAssignment::new(
        role_id.clone(),
        NodeId::new("node-a").expect("node is valid"),
        Vec::new(),
    );
    let mut control = ControlPlane::new();
    let mut state = InMemorySharedNodeState::new();
    let mut events = TestEvents;
    control
        .register_node(
            &mut state,
            operation_support_node("node-a", "relocate"),
            NodeStatus::new(NodeHealth::Online, timestamp),
            timestamp,
            &correlation,
            &mut events,
        )
        .expect("node registers");
    let candidates = control
        .match_capabilities_for_mission(
            &state,
            &plan,
            &requirement,
            timestamp,
            &correlation,
            &mut events,
        )
        .expect("matching succeeds");
    replace_operation_registration(&mut state, operation_support_node("node-a", "inspect"));
    assert!(matches!(
        control.propose(
            &state,
            &requirement,
            &candidates,
            vec![assignment.clone()],
            timestamp,
            &correlation,
            &mut events,
        ),
        Err(ControlError::AssignmentUnavailable(reason)) if reason.contains("no longer supports operation")
    ));

    replace_operation_registration(&mut state, operation_support_node("node-a", "relocate"));
    let candidates = control
        .match_capabilities_for_mission(
            &state,
            &plan,
            &requirement,
            timestamp,
            &correlation,
            &mut events,
        )
        .expect("matching succeeds again");
    let proposal = control
        .propose(
            &state,
            &requirement,
            &candidates,
            vec![assignment],
            timestamp,
            &correlation,
            &mut events,
        )
        .expect("proposal succeeds");
    replace_operation_registration(&mut state, operation_support_node("node-a", "inspect"));
    assert!(matches!(
        control.commit_with_state(
            &state,
            &proposal,
            timestamp,
            &correlation,
            &mut events,
        ),
        Err(ControlError::AssignmentUnavailable(reason)) if reason.contains("no longer satisfies capability and operation")
    ));
    assert!(control.allocation_snapshot(timestamp).expect("projection is valid").allocations().is_empty());
}

/// Recovery Match and Commit preserve the same exact operation-support feasibility check.
#[test]
fn recovery_pipeline_requires_and_revalidates_operation_support() {
    let (plan, requirement, role_id) = operation_support_plan();
    let timestamp = TimestampMs::new(0);
    let correlation = CorrelationId::new("operation-recovery").expect("correlation is valid");
    let group_id = ExecutionGroupId::new("operation-group").expect("group is valid");
    let node_a = NodeId::new("node-a").expect("node is valid");
    let mut control = ControlPlane::new();
    let mut state = InMemorySharedNodeState::new();
    let mut events = TestEvents;
    for registration in [
        operation_support_node("node-a", "relocate"),
        operation_support_node("node-b", "inspect"),
        operation_support_node("node-c", "relocate"),
    ] {
        control
            .register_node(
                &mut state,
                registration,
                NodeStatus::new(NodeHealth::Online, timestamp),
                timestamp,
                &correlation,
                &mut events,
            )
            .expect("node registers");
    }
    let candidates = control
        .match_capabilities_for_mission(
            &state,
            &plan,
            &requirement,
            timestamp,
            &correlation,
            &mut events,
        )
        .expect("initial match succeeds");
    let proposal = control
        .propose(
            &state,
            &requirement,
            &candidates,
            vec![domain::RoleAssignment::new(
                role_id.clone(),
                node_a.clone(),
                Vec::new(),
            )],
            timestamp,
            &correlation,
            &mut events,
        )
        .expect("initial proposal succeeds");
    let committed = control
        .commit_with_state(&state, &proposal, timestamp, &correlation, &mut events)
        .expect("initial commit succeeds");
    control
        .create_group(
            group_id.clone(),
            &committed,
            timestamp,
            &correlation,
            &mut events,
        )
        .expect("group binds");
    control
        .activate_group(&group_id, timestamp, &correlation, &mut events)
        .expect("group activates");
    let need = control
        .begin_execution_recovery(
            &group_id,
            requirement.task_ref(),
            &role_id,
            &node_a,
            timestamp,
            &correlation,
            &mut events,
        )
        .expect("recovery begins");
    let operation = plan.task_graph().tasks()[0]
        .execution_intent(&role_id)
        .expect("intent exists")
        .operation();
    let recovery_candidates = control
        .match_recovery_candidates_for_operation(
            &state,
            &need,
            &requirement,
            operation,
            timestamp,
            &correlation,
            &mut events,
        )
        .expect("recovery matching succeeds");
    assert_eq!(
        recovery_candidates.candidate_node_ids(),
        &[NodeId::new("node-c").expect("node is valid")]
    );
    let node_c = NodeId::new("node-c").expect("node is valid");
    let recovery_proposal = control
        .propose_role_recovery(
            &state,
            &recovery_candidates,
            &requirement,
            node_c,
            Vec::new(),
            timestamp,
            &correlation,
            &mut events,
        )
        .expect("recovery proposal succeeds");
    replace_operation_registration(&mut state, operation_support_node("node-c", "inspect"));
    assert!(matches!(
        control.commit_role_recovery(
            &state,
            &requirement,
            &recovery_proposal,
            timestamp,
            &correlation,
            &mut events,
        ),
        Err(ControlError::InvalidProposal(reason)) if reason.contains("no longer supports operation")
    ));
    assert_eq!(
        control.group(&group_id).expect("group exists").lifecycle(),
        GroupLifecycle::Blocked
    );
}

/// Mission-level Task recovery uses only its Task-local released binding and preserves
/// the surrounding Group state through a successful replacement.
#[test]
fn mission_task_recovery_rebinds_exact_task_scope() {
    let (plan, requirement, role_id) = operation_support_plan();
    let timestamp = TimestampMs::new(0);
    let correlation = CorrelationId::new("task-recovery-scope").expect("correlation is valid");
    let mut control = ControlPlane::new();
    let mut state = InMemorySharedNodeState::new();
    let mut events = RecordingEvents::default();
    for node_id in ["node-a", "node-b"] {
        control
            .register_node(
                &mut state,
                operation_support_node(node_id, "relocate"),
                NodeStatus::new(NodeHealth::Online, timestamp),
                timestamp,
                &correlation,
                &mut events,
            )
            .expect("recovery node registers");
    }
    let group_id = ExecutionGroupId::new("group-task-recovery-scope").expect("group is valid");
    let task_ref = requirement.task_ref().clone();
    control
        .create_mission_group(
            group_id.clone(),
            &plan,
            timestamp,
            &correlation,
            &mut events,
        )
        .expect("Mission Group creates");
    control
        .ready_task_execution(
            &group_id,
            &task_ref,
            TimestampMs::new(1),
            &correlation,
            &mut events,
        )
        .expect("Task becomes ready");
    control
        .bind_task_execution(
            &group_id,
            &CommittedPlan::new(
                task_ref.clone(),
                vec![domain::RoleAssignment::new(
                    role_id.clone(),
                    NodeId::new("node-a").expect("node is valid"),
                    Vec::new(),
                )],
            ),
            TimestampMs::new(2),
            &correlation,
            &mut events,
        )
        .expect("Task binding succeeds");
    control
        .activate_task_execution(
            &group_id,
            &task_ref,
            TimestampMs::new(3),
            &correlation,
            &mut events,
        )
        .expect("Task activates");
    let need = control
        .begin_execution_recovery(
            &group_id,
            &task_ref,
            &role_id,
            &NodeId::new("node-a").expect("node is valid"),
            TimestampMs::new(4),
            &correlation,
            &mut events,
        )
        .expect("Task-local recovery begins");
    let wrong_requirement = TaskRequirement::new(
        requirement.mission_id().clone(),
        TaskId::new("wrong-task").expect("Task is valid"),
        requirement.roles().to_vec(),
    )
    .expect("wrong Task requirement is structurally valid");
    let wrong_need = RoleRecoveryNeed::new(
        group_id.clone(),
        wrong_requirement.task_ref().clone(),
        role_id.clone(),
        NodeId::new("node-a").expect("node is valid"),
    );
    let before = serde_json::to_value(control.checkpoint()).expect("checkpoint serializes");
    let event_count = events.records.len();
    assert!(matches!(
        control.match_recovery_candidates(
            &state,
            &wrong_need,
            &wrong_requirement,
            TimestampMs::new(5),
            &correlation,
            &mut events,
        ),
        Err(ControlError::InvalidProposal(reason)) if reason.contains("not unbound")
    ));
    assert_eq!(
        serde_json::to_value(control.checkpoint()).expect("checkpoint serializes"),
        before,
        "wrong Task scope must not mutate recovery state"
    );
    assert_eq!(events.records.len(), event_count);
    let operation = plan.task_graph().tasks()[0]
        .execution_intent(&role_id)
        .expect("operation intent exists")
        .operation();
    let candidates = control
        .match_recovery_candidates_for_operation(
            &state,
            &need,
            &requirement,
            operation,
            TimestampMs::new(5),
            &correlation,
            &mut events,
        )
        .expect("Task-local recovery matching succeeds");
    assert_eq!(
        candidates.candidate_node_ids(),
        &[NodeId::new("node-b").expect("node is valid")]
    );
    let proposal = control
        .propose_role_recovery(
            &state,
            &candidates,
            &requirement,
            NodeId::new("node-b").expect("node is valid"),
            Vec::new(),
            TimestampMs::new(6),
            &correlation,
            &mut events,
        )
        .expect("Task-local recovery proposal succeeds");
    let committed = control
        .commit_role_recovery(
            &state,
            &requirement,
            &proposal,
            TimestampMs::new(7),
            &correlation,
            &mut events,
        )
        .expect("Task-local recovery commitment succeeds");
    control
        .rebind_role(
            &committed,
            TimestampMs::new(8),
            &correlation,
            &mut events,
        )
        .expect("Task-local recovery rebind succeeds");

    let group = control.group(&group_id).expect("Group remains");
    let execution = group.task_execution(&task_ref).expect("Task remains");
    assert_eq!(group.assignments(), &[]);
    assert_eq!(execution.assignments().len(), 1);
    assert_eq!(execution.assignments()[0].role_id(), &role_id);
    assert_eq!(
        execution.assignments()[0].node_id(),
        &NodeId::new("node-b").expect("node is valid")
    );
    assert!(control.pending_role_recoveries().is_empty());
}

/// Initial Bind rechecks exact operation support even when capability and a prior Commit remain valid.
#[test]
fn initial_binding_rejects_operation_loss_after_commit() {
    let (plan, requirement, role) = operation_support_plan();
    let mut control = ControlPlane::new();
    let mut state = InMemorySharedNodeState::new();
    let mut events = TestEvents;
    let correlation = CorrelationId::new("bind-current-operation").unwrap();
    let group = ExecutionGroupId::new("bind-current-operation").unwrap();
    control.register_node(&mut state, operation_support_node("configured-owner", "relocate"),
        NodeStatus::new(NodeHealth::Online, TimestampMs::new(0)), TimestampMs::new(0), &correlation, &mut events).unwrap();
    control.create_mission_group(group.clone(), &plan, TimestampMs::new(1), &correlation, &mut events).unwrap();
    control.ready_task_execution(&group, requirement.task_ref(), TimestampMs::new(1), &correlation, &mut events).unwrap();
    let candidates = control.match_capabilities_for_mission(&state, &plan, &requirement, TimestampMs::new(2), &correlation, &mut events).unwrap();
    let proposal = control.propose(&state, &requirement, &candidates,
        vec![RoleAssignment::new(role, NodeId::new("configured-owner").unwrap(), Vec::new())],
        TimestampMs::new(2), &correlation, &mut events).unwrap();
    let committed = control.commit_for_group_with_state(&state, &group, &proposal, TimestampMs::new(2), &correlation, &mut events).unwrap();
    replace_operation_registration(&mut state, operation_support_node("configured-owner", "inspect"));
    assert!(matches!(control.bind_task_execution_with_state(&state, &group, &committed, TimestampMs::new(3), &correlation, &mut events),
        Err(ControlError::AssignmentUnavailable(_))));
    assert!(control.group(&group).unwrap().task_execution(requirement.task_ref()).unwrap().assignments().is_empty());
    replace_operation_registration(&mut state, operation_support_node("configured-owner", "relocate"));
    control.bind_task_execution_with_state(&state, &group, &committed, TimestampMs::new(4), &correlation, &mut events).unwrap();
}
