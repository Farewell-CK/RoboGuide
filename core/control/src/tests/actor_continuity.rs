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
    let context_id = domain::CoordinationContextId::new("delivery")
        .expect("context id must be valid");
    let carrier_context_role = domain::ContextRoleId::new("carrier")
        .expect("context role id must be valid");
    let manipulator_context_role = domain::ContextRoleId::new("manipulator")
        .expect("context role id must be valid");
    let task = |description: &str, requirement: TaskRequirement, dependencies: Vec<TaskId>| {
        let role_id = requirement.roles()[0].role_id().clone();
        let context_role = if requirement.roles()[0]
            .actor_id()
            .is_some_and(|actor| actor.as_str() == "carrier")
        {
            carrier_context_role.clone()
        } else {
            manipulator_context_role.clone()
        };
        PlannedTask::new(
            description,
            requirement,
            BTreeMap::from([(role_id.clone(), intent(description))]),
            dependencies,
            domain::TaskContinuity::new(
                context_id.clone(),
                BTreeMap::from([(role_id, context_role)]),
                BTreeMap::new(),
            ),
        )
            .expect("planned task valid")
    };
    let graph = TaskGraph::new(mission_id.clone(), vec![
        task("go-to-shelf", t1.clone(), vec![]),
        task("pick-book", t2, vec![t1_id.clone()]),
        task("return-user", t3.clone(), vec![TaskId::new("t2").expect("task id valid")]),
    ]).expect("graph valid");
    let goal = MissionGoal::new(mission_id, "deliver book").expect("goal valid");
    let context = domain::CoordinationContext::new(
        context_id,
        vec![
            domain::ContextRole::new(
                carrier_context_role,
                domain::ActorId::new("carrier").expect("actor id valid"),
            ),
            domain::ContextRole::new(
                manipulator_context_role,
                domain::ActorId::new("manipulator").expect("actor id valid"),
            ),
        ],
    )
    .expect("context valid");
    (MissionPlan::new(goal, graph, vec![context]).expect("plan valid"), t1, t3)
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

/// A deployment placement constraint keeps a first-use actor on the declared physical dog.
///
/// Both nodes advertise the complete MissionPlan, so a deterministic scheduler alone would be
/// free to choose either one. The constraint narrows only the Candidate Set; it does not create an
/// Actor binding until the normal Proposal -> Commit -> Group Bind path succeeds.
#[test]
fn actor_placement_constraint_proves_two_node_experiment_assignment() {
    let (mission, t1, _) = continuity_plan();
    let mission_id = mission.goal().mission_id().clone();
    let actor_id = domain::ActorId::new("carrier").expect("actor id valid");
    let dog_a = NodeId::new("dog-a").expect("node id valid");
    let dog_b = NodeId::new("dog-b").expect("node id valid");
    let mut control = ControlPlane::new();
    let mut state = InMemorySharedNodeState::new();
    let mut events = TestEvents;
    let now = TimestampMs::new(0);
    let correlation_id = correlation();
    for (node, resource) in [(dog_a.clone(), "space-a"), (dog_b, "space-b")] {
        let registration = actor_node_with_contracts(
            node.as_str(),
            CapabilityKind::Mobility,
            &["go-to-shelf", "return-user"],
            resource,
            ResourceKind::Space,
        );
        control
            .register_node(
                &mut state,
                registration,
                NodeStatus::new(NodeHealth::Online, now),
                now,
                &correlation_id,
                &mut events,
            )
            .expect("node registration valid");
    }
    control
        .set_actor_node_constraint(mission_id.clone(), actor_id.clone(), dog_a.clone())
        .expect("placement constraint is accepted before actor binding");
    assert!(control.actor_binding(&mission_id, &actor_id).is_none());
    let candidates = control
        .match_capabilities_for_mission(
            &state,
            &mission,
            &t1,
            now,
            &correlation_id,
            &mut events,
        )
        .expect("constrained matching succeeds");
    assert_eq!(
        candidates.roles()[0].node_ids(),
        std::slice::from_ref(&dog_a),
        "the experiment must not silently schedule both logical actors on one sorted node"
    );
    assert_eq!(
        control
            .actor_node_constraint(&mission_id, &actor_id)
            .expect("constraint remains visible")
            .node_id(),
        &dog_a
    );
}

/// Placement constraints round-trip through the durable Control checkpoint and reject conflicts.
#[test]
fn actor_placement_constraint_checkpoint_is_durable_and_conflict_checked() {
    let mission_id = domain::MissionId::new("mission-placement").expect("mission id valid");
    let actor_id = domain::ActorId::new("robot-dog-a").expect("actor id valid");
    let dog_a = NodeId::new("dog-a").expect("node id valid");
    let dog_b = NodeId::new("dog-b").expect("node id valid");
    let mut control = ControlPlane::new();
    control
        .set_actor_node_constraint(mission_id.clone(), actor_id.clone(), dog_a.clone())
        .expect("placement constraint accepted");
    let json = serde_json::to_string(&control.checkpoint()).expect("checkpoint serializes");
    let restored = ControlPlane::restore(
        serde_json::from_str(&json).expect("checkpoint deserializes"),
    )
    .expect("checkpoint restores");
    assert_eq!(
        restored
            .actor_node_constraint(&mission_id, &actor_id)
            .expect("constraint restored")
            .node_id(),
        &dog_a
    );
    let mut restored = restored;
    assert!(matches!(
        restored.set_actor_node_constraint(mission_id, actor_id, dog_b),
        Err(ControlError::InvalidProposal(reason)) if reason.contains("different placement constraint")
    ));
}

/// Group binding rejects an assignment that bypassed mission-aware placement matching.
#[test]
fn actor_placement_constraint_is_enforced_again_at_group_bind() {
    let (mission, t1, _) = continuity_plan();
    let mission_id = mission.goal().mission_id().clone();
    let actor_id = domain::ActorId::new("carrier").expect("actor id valid");
    let dog_a = NodeId::new("dog-a").expect("node id valid");
    let dog_b = NodeId::new("dog-b").expect("node id valid");
    let mut control = ControlPlane::new();
    let mut state = InMemorySharedNodeState::new();
    let mut events = TestEvents;
    let now = TimestampMs::new(0);
    let correlation_id = correlation();
    for (node, resource) in [(dog_a.clone(), "space-a"), (dog_b.clone(), "space-b")] {
        control
            .register_node(
                &mut state,
                actor_node_with_contracts(
                    node.as_str(),
                    CapabilityKind::Mobility,
                    &["go-to-shelf", "return-user"],
                    resource,
                    ResourceKind::Space,
                ),
                NodeStatus::new(NodeHealth::Online, now),
                now,
                &correlation_id,
                &mut events,
            )
            .expect("node registration valid");
    }
    control
        .set_actor_node_constraint(mission_id.clone(), actor_id.clone(), dog_a)
        .expect("placement constraint accepted");
    let unconstrained_candidates = control
        .match_capabilities(&state, &t1, now, &correlation_id, &mut events)
        .expect("generic matching exposes both eligible nodes");
    let proposal = control
        .propose(
            &state,
            &t1,
            &unconstrained_candidates,
            vec![RoleAssignment::new(
                t1.roles()[0].role_id().clone(),
                dog_b,
                vec![ResourceId::new("space-b").expect("resource id valid")],
            )],
            now,
            &correlation_id,
            &mut events,
        )
        .expect("out-of-policy proposal remains uncommitted configuration");
    let committed = control
        .commit(&proposal, now, &correlation_id, &mut events)
        .expect("commit is independent of Mission actor metadata");
    let group_id = ExecutionGroupId::new("group-placement-guard").expect("group id valid");
    assert!(matches!(
        control.create_group_with_actor_bindings(
            group_id.clone(),
            &committed,
            &t1,
            now,
            &correlation_id,
            &mut events,
        ),
        Err(ControlError::InvalidProposal(reason))
            if reason.contains("placement constraint")
    ));
    assert!(control.group(&group_id).is_none());
    assert!(control.actor_binding(&mission_id, &actor_id).is_none());
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

/// Recovery keeps a failed physical Actor fenced instead of migrating it to another eligible node.
#[test]
fn actor_recovery_cannot_bypass_binding_or_placement_authority() {
    let (mission, t1, _) = continuity_plan();
    let mission_id = mission.goal().mission_id().clone();
    let actor_id = domain::ActorId::new("carrier").expect("actor id valid");
    let dog_a = NodeId::new("dog-a").expect("node id valid");
    let dog_b = NodeId::new("dog-b").expect("node id valid");
    let group_id = ExecutionGroupId::new("group-actor-recovery").expect("group id valid");
    let correlation_id = correlation();
    let mut control = ControlPlane::new();
    let mut state = InMemorySharedNodeState::new();
    let mut events = TestEvents;
    let stripped_requirement = TaskRequirement::new(
        mission_id.clone(),
        t1.task_id().clone(),
        vec![RoleRequirement::new(
            t1.roles()[0].role_id().clone(),
            t1.roles()[0].capability(),
            t1.roles()[0].resource_kind(),
        )],
    )
    .expect("attacker-controlled requirement retains only public Task and Role identities");
    for (node_id, resource_id) in [(dog_a.clone(), "space-a"), (dog_b.clone(), "space-b")] {
        control
            .register_node(
                &mut state,
                actor_node_with_contracts(
                    node_id.as_str(),
                    CapabilityKind::Mobility,
                    &["go-to-shelf", "return-user"],
                    resource_id,
                    ResourceKind::Space,
                ),
                NodeStatus::new(NodeHealth::Online, TimestampMs::new(0)),
                TimestampMs::new(0),
                &correlation_id,
                &mut events,
            )
            .expect("node registration valid");
    }
    control
        .set_actor_node_constraint(mission_id.clone(), actor_id.clone(), dog_a.clone())
        .expect("placement constraint accepted");
    control
        .create_mission_group(
            group_id.clone(),
            &mission,
            TimestampMs::new(0),
            &correlation_id,
            &mut events,
        )
        .expect("Mission-level Group is created");
    control
        .ready_task_execution(
            &group_id,
            t1.task_ref(),
            TimestampMs::new(0),
            &correlation_id,
            &mut events,
        )
        .expect("first Task becomes ready");
    let candidates = control
        .match_capabilities_for_mission(
            &state,
            &mission,
            &t1,
            TimestampMs::new(0),
            &correlation_id,
            &mut events,
        )
        .expect("placement-constrained matching succeeds");
    let decision = DeterministicBootstrapScheduler::new()
        .schedule_task(
            &state,
            &t1,
            &candidates,
            TimestampMs::new(0),
            &correlation_id,
            &mut events,
        )
        .expect("scheduler selects the constrained node");
    let proposal = control
        .propose(
            &state,
            &t1,
            &candidates,
            decision.proposed_assignments(),
            TimestampMs::new(0),
            &correlation_id,
            &mut events,
        )
        .expect("assignment proposal succeeds");
    let committed = control
        .commit(
            &proposal,
            TimestampMs::new(0),
            &correlation_id,
            &mut events,
        )
        .expect("assignment commit succeeds");
    control
        .bind_task_execution_with_requirement(
            &group_id,
            &committed,
            &t1,
            TimestampMs::new(0),
            &correlation_id,
            &mut events,
        )
        .expect("committed Task binds inside the Mission Group");
    control
        .activate_task_execution(
            &group_id,
            t1.task_ref(),
            TimestampMs::new(0),
            &correlation_id,
            &mut events,
        )
        .expect("Task execution becomes active");
    state
        .record_node_health(NodeHealthObservation::new(
            dog_a.clone(),
            NodeStatus::new(NodeHealth::Offline, TimestampMs::new(1)),
            TimestampMs::new(1),
        ))
        .expect("failed Actor node observation is recorded");
    assert!(matches!(
        control.assess_group(
            &state,
            &group_id,
            &stripped_requirement,
            TimestampMs::new(1),
            &correlation_id,
            &mut events,
        ),
        Err(ControlError::InvalidProposal(reason))
            if reason.contains("authoritative Execution Group role metadata")
    ));
    let need = match control
        .assess_group(
            &state,
            &group_id,
            &t1,
            TimestampMs::new(1),
            &correlation_id,
            &mut events,
        )
        .expect("failed Actor assignment is assessed")
    {
        ReconciliationAssessment::RoleRecoveryRequired(need) => need,
        ReconciliationAssessment::NoAction => panic!("offline Actor must require recovery"),
    };
    control
        .begin_role_recovery(
            &need,
            TimestampMs::new(2),
            &correlation_id,
            &mut events,
        )
        .expect("role-scoped recovery releases only the failed Task binding");
    let recovery_candidates = control
        .match_recovery_candidates(
            &state,
            &need,
            &t1,
            TimestampMs::new(2),
            &correlation_id,
            &mut events,
        )
        .expect("recovery matching remains a valid empty decision input");
    assert!(recovery_candidates.candidate_node_ids().is_empty());
    assert!(matches!(
        control.match_recovery_candidates(
            &state,
            &need,
            &stripped_requirement,
            TimestampMs::new(2),
            &correlation_id,
            &mut events,
        ),
        Err(ControlError::InvalidProposal(reason))
            if reason.contains("authoritative Execution Group role metadata")
    ));
    let checkpoint_json =
        serde_json::to_string(&control.checkpoint()).expect("Control checkpoint serializes");
    let mut tampered_checkpoint =
        serde_json::to_value(control.checkpoint()).expect("Control checkpoint converts to JSON");
    tampered_checkpoint["groups"][0]["role_requirements"] = serde_json::json!([]);
    assert!(matches!(
        ControlPlane::restore(
            serde_json::from_value(tampered_checkpoint)
                .expect("exact-schema tampered checkpoint still decodes")
        ),
        Err(ControlError::InvalidProposal(reason))
            if reason.contains("exact authoritative role metadata")
    ));
    let restored = ControlPlane::restore(
        serde_json::from_str(&checkpoint_json).expect("Control checkpoint deserializes"),
    )
    .expect("Control checkpoint restores");
    assert!(matches!(
        restored.match_recovery_candidates(
            &state,
            &need,
            &stripped_requirement,
            TimestampMs::new(2),
            &correlation_id,
            &mut events,
        ),
        Err(ControlError::InvalidProposal(reason))
            if reason.contains("authoritative Execution Group role metadata")
    ));
    let forged_candidates = RecoveryCandidateSet::new(
        group_id.clone(),
        t1.task_ref().clone(),
        t1.roles()[0].role_id().clone(),
        dog_a.clone(),
        vec![dog_b.clone()],
    );
    let forged_proposal = control
        .propose_role_recovery(
            &state,
            &forged_candidates,
            &t1,
            dog_b,
            vec![ResourceId::new("space-b").expect("resource id valid")],
            TimestampMs::new(2),
            &correlation_id,
            &mut events,
        )
        .expect("a crate-internal forged Candidate Set can reach the commit guard");
    assert!(matches!(
        control.commit_role_recovery(
            &state,
            &stripped_requirement,
            &forged_proposal,
            TimestampMs::new(2),
            &correlation_id,
            &mut events,
        ),
        Err(ControlError::InvalidProposal(reason))
            if reason.contains("authoritative Execution Group role metadata")
    ));
    assert!(matches!(
        control.commit_role_recovery(
            &state,
            &t1,
            &forged_proposal,
            TimestampMs::new(2),
            &correlation_id,
            &mut events,
        ),
        Err(ControlError::InvalidProposal(reason))
            if reason.contains("explicit Actor rebind is required")
    ));
    assert!(
        control
            .pending_recovery_commitment(&group_id, t1.roles()[0].role_id())
            .is_none(),
        "a rejected cross-Actor commit must not reserve replacement resources"
    );
    assert_eq!(
        control
            .actor_binding(&mission_id, &actor_id)
            .expect("Actor authority survives partial release")
            .node_id(),
        &dog_a
    );
    let group = control.group(&group_id).expect("Mission Group remains present");
    assert_eq!(group.lifecycle(), GroupLifecycle::Blocked);
    assert!(
        group
            .task_execution(t1.task_ref())
            .expect("Task execution remains registered")
            .assignments()
            .is_empty()
    );
}
