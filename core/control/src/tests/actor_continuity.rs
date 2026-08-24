/// Builds a contract-bearing role for the continuity scenario.
fn actor_role(
    role: &str,
    actor: &str,
    capability: CapabilityKind,
    contract: &str,
    resource_kind: ResourceKind,
) -> RoleRequirement {
    RoleRequirement::new_with_actor_and_contract(
        RoleId::new(role).expect("role id must be valid"),
        domain::ActorId::new(actor).expect("actor id must be valid"),
        capability,
        CapabilityContractRef::new("mission", contract, "v1").expect("contract must be valid"),
        Some(resource_kind),
    )
}

/// Builds a MissionPlan whose carrier appears in both first and later tasks.
fn continuity_plan() -> (domain::MissionPlan, TaskRequirement, TaskRequirement) {
    let mission_id = domain::MissionId::new("mission-continuity").expect("mission id must be valid");
    let t1_id = TaskId::new("t1").expect("task id must be valid");
    let t3_id = TaskId::new("t3").expect("task id must be valid");
    let t1_role = actor_role("carrier-t1", "carrier", CapabilityKind::Mobility, "go-to-shelf", ResourceKind::Space);
    let t3_role = actor_role("carrier-t3", "carrier", CapabilityKind::Mobility, "return-user", ResourceKind::Space);
    let t2_role = actor_role("manipulator", "manipulator", CapabilityKind::Compute, "pick-book", ResourceKind::Compute);
    let t1 = TaskRequirement::new(mission_id.clone(), t1_id.clone(), vec![t1_role]).expect("t1 requirement valid");
    let t3 = TaskRequirement::new(mission_id.clone(), t3_id.clone(), vec![t3_role]).expect("t3 requirement valid");
    let t2 = TaskRequirement::new(mission_id.clone(), TaskId::new("t2").expect("task id valid"), vec![t2_role]).expect("t2 requirement valid");
    let intent = |name: &str| {
        ExecutionIntent::new(
            CapabilityContractRef::new("mission", name, "v1").expect("contract valid"),
            BTreeMap::new(),
        )
        .expect("intent valid")
    };
    let task = |description: &str, requirement: TaskRequirement, dependencies: Vec<TaskId>| {
        let role_id = requirement.roles()[0].role_id().clone();
        PlannedTask::new(description, requirement, BTreeMap::from([(role_id, intent(description))]), dependencies)
            .expect("planned task valid")
    };
    let graph = TaskGraph::new(mission_id.clone(), vec![
        task("go-to-shelf", t1.clone(), vec![]),
        task("pick-book", t2, vec![t1_id.clone()]),
        task("return-user", t3.clone(), vec![TaskId::new("t2").expect("task id valid")]),
    ]).expect("graph valid");
    let goal = MissionGoal::new(mission_id, "deliver book").expect("goal valid");
    (MissionPlan::new(goal, graph).expect("plan valid"), t1, t3)
}

/// Registers a node with one capability and one exact mission contract.
fn actor_node(name: &str, capability: CapabilityKind, contract: &str, resource: &str, kind: ResourceKind) -> NodeRegistration {
    actor_node_with_contracts(name, capability, &[contract], resource, kind)
}

/// Registers a node with multiple exact mission contracts.
fn actor_node_with_contracts(name: &str, capability: CapabilityKind, contracts: &[&str], resource: &str, kind: ResourceKind) -> NodeRegistration {
    NodeRegistration::new_with_contracts(
        NodeId::new(name).expect("node id valid"),
        domain::LocalRuntime::new("test-runtime", "0.1").expect("runtime valid"),
        domain::NodeContractVersion::v0_1(),
        vec![Capability::new(capability, true)],
        contracts.iter().map(|contract| CapabilityContractRef::new("mission", *contract, "v1").expect("contract valid")).collect(),
        vec![Resource::new(ResourceId::new(resource).expect("resource id valid"), kind, 1).expect("resource valid")],
    )
}

/// Whole-plan actor matching excludes a node that only satisfies the first task.
#[test]
fn first_actor_selection_uses_all_mission_requirements() {
    let (mission, t1, _) = continuity_plan();
    let mut control = ControlPlane::new();
    let mut state = InMemorySharedNodeState::new();
    let mut events = TestEvents;
    let now = TimestampMs::new(0);
    let correlation_id = correlation();
    for node in [
        actor_node("dog-a", CapabilityKind::Mobility, "go-to-shelf", "space-a", ResourceKind::Space),
        actor_node_with_contracts("dog-b", CapabilityKind::Mobility, &["go-to-shelf", "return-user"], "space-b", ResourceKind::Space),
    ] {
        control.register_node(&mut state, node, NodeStatus::new(NodeHealth::Online, now), now, &correlation_id, &mut events).expect("node registration valid");
    }
    let candidates = control.match_capabilities_for_mission(&state, &mission, &t1, now, &correlation_id, &mut events).expect("matching succeeds");
    assert_eq!(candidates.roles()[0].node_ids(), &[NodeId::new("dog-b").expect("node id valid")]);
}

/// Actor binding is established only by successful Group Bind and is reused by later matching.
#[test]
fn actor_binding_survives_t1_to_t3_and_is_audited() {
    let (mission, t1, t3) = continuity_plan();
    let mut control = ControlPlane::new();
    let mut state = InMemorySharedNodeState::new();
    let mut events = RecordingEvents::default();
    let now = TimestampMs::new(0);
    let correlation_id = correlation();
    let node = actor_node_with_contracts("dog-b", CapabilityKind::Mobility, &["go-to-shelf", "return-user"], "space-b", ResourceKind::Space);
    control.register_node(&mut state, node, NodeStatus::new(NodeHealth::Online, now), now, &correlation_id, &mut events).expect("node registration valid");
    let candidates = control.match_capabilities_for_mission(&state, &mission, &t1, now, &correlation_id, &mut events).expect("matching succeeds");
    let scheduler = DeterministicBootstrapScheduler::new();
    let decision = scheduler.schedule_task(&state, &t1, &candidates, now, &correlation_id, &mut events).expect("schedule succeeds");
    let proposal = control.propose(&state, &t1, &candidates, decision.proposed_assignments(), now, &correlation_id, &mut events).expect("proposal succeeds");
    let committed = control.commit(&proposal, now, &correlation_id, &mut events).expect("commit succeeds");
    let group_id = ExecutionGroupId::new("group-t1").expect("group id valid");
    control.create_group_with_actor_bindings(group_id.clone(), &committed, &t1, now, &correlation_id, &mut events).expect("group bind succeeds");
    let actor_id = domain::ActorId::new("carrier").expect("actor id valid");
    assert_eq!(control.actor_binding(mission.goal().mission_id(), &actor_id).expect("binding exists").node_id(), &NodeId::new("dog-b").expect("node id valid"));
    assert!(events.records.iter().any(|(_, payload)| matches!(payload, EventPayload::MissionActorBound { .. })));
    let later = control.match_capabilities_for_mission(&state, &mission, &t3, now, &correlation_id, &mut events).expect("later match reuses binding");
    assert_eq!(later.roles()[0].node_ids(), &[NodeId::new("dog-b").expect("node id valid")]);
}

/// Failed proposal or Group Bind paths cannot create an Actor binding authority.
#[test]
fn actor_binding_is_absent_until_group_bind_succeeds() {
    let (mission, t1, _) = continuity_plan();
    let mut control = ControlPlane::new();
    let mut state = InMemorySharedNodeState::new();
    let mut events = TestEvents;
    let now = TimestampMs::new(0);
    let correlation_id = correlation();
    let node = actor_node_with_contracts("dog-b", CapabilityKind::Mobility, &["go-to-shelf", "return-user"], "space-b", ResourceKind::Space);
    control.register_node(&mut state, node, NodeStatus::new(NodeHealth::Online, now), now, &correlation_id, &mut events).expect("node registration valid");
    let candidates = control.match_capabilities_for_mission(&state, &mission, &t1, now, &correlation_id, &mut events).expect("matching succeeds");
    let actor_id = domain::ActorId::new("carrier").expect("actor id valid");
    assert!(control.propose(&state, &t1, &candidates, Vec::new(), now, &correlation_id, &mut events).is_err());
    assert!(control.actor_binding(mission.goal().mission_id(), &actor_id).is_none());
}

/// A bound Actor whose node becomes unavailable returns reconciliation explicitly.
#[test]
fn unavailable_bound_actor_requires_reconciliation() {
    let (mission, t1, t3) = continuity_plan();
    let mut control = ControlPlane::new();
    let mut state = InMemorySharedNodeState::new();
    let mut events = TestEvents;
    let now = TimestampMs::new(0);
    let correlation_id = correlation();
    let node = actor_node_with_contracts("dog-b", CapabilityKind::Mobility, &["go-to-shelf", "return-user"], "space-b", ResourceKind::Space);
    control.register_node(&mut state, node, NodeStatus::new(NodeHealth::Online, now), now, &correlation_id, &mut events).expect("node registration valid");
    let candidates = control.match_capabilities_for_mission(&state, &mission, &t1, now, &correlation_id, &mut events).expect("matching succeeds");
    let decision = DeterministicBootstrapScheduler::new().schedule_task(&state, &t1, &candidates, now, &correlation_id, &mut events).expect("schedule succeeds");
    let proposal = control.propose(&state, &t1, &candidates, decision.proposed_assignments(), now, &correlation_id, &mut events).expect("proposal succeeds");
    let committed = control.commit(&proposal, now, &correlation_id, &mut events).expect("commit succeeds");
    control.create_group_with_actor_bindings(ExecutionGroupId::new("group-t1").expect("group id valid"), &committed, &t1, now, &correlation_id, &mut events).expect("group bind succeeds");
    state.record_node_health(NodeHealthObservation::new(NodeId::new("dog-b").expect("node id valid"), NodeStatus::new(NodeHealth::Offline, TimestampMs::new(1)), TimestampMs::new(1))).expect("health update succeeds");
    assert!(matches!(control.match_capabilities_for_mission(&state, &mission, &t3, TimestampMs::new(1), &correlation_id, &mut events), Err(ControlError::ActorBindingRequiresReconciliation { .. })));
}
