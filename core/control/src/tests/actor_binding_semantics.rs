use domain::{
    DistinctPhysicalEntityConstraint, PhysicalEntityId, PhysicalEntityRegistration,
    PhysicalEntityRegistryId, PhysicalEntityRegistrySnapshot, PhysicalEntityRoutingProfile,
};

/// Builds one current deployment-owned physical-entity routing snapshot.
fn physical_registry(
    revision: u64,
    registrations: &[(&str, &str)],
) -> PhysicalEntityRegistrySnapshot {
    PhysicalEntityRegistrySnapshot::new(
        PhysicalEntityRegistryId::new("test-registry").expect("registry id valid"),
        revision,
        PhysicalEntityRoutingProfile::OneRoutableEntityPerNode,
        registrations
            .iter()
            .map(|(entity, node)| {
                PhysicalEntityRegistration::new(
                    PhysicalEntityId::new(*entity).expect("entity id valid"),
                    NodeId::new(*node).expect("node id valid"),
                )
            })
            .collect(),
    )
    .expect("test registry valid")
}

/// Builds one operation-capable Node with no exclusive resource requirement.
fn physical_node(node_id: &str) -> NodeRegistration {
    let contract =
        CapabilityContractRef::new("mobility", "move", "v1").expect("contract valid");
    NodeRegistration::new_with_contracts(
        NodeId::new(node_id).expect("node id valid"),
        domain::LocalRuntime::new("test-eaios", "1").expect("runtime valid"),
        domain::NodeContractVersion::v0_1(),
        vec![Capability::new(CapabilityKind::Mobility, true)],
        vec![contract],
        Vec::new(),
    )
}

/// Registers healthy deterministic Nodes in both Control and Shared Node State.
fn register_physical_nodes(
    control: &mut ControlPlane,
    state: &mut InMemorySharedNodeState,
    node_ids: &[&str],
) {
    let mut events = TestEvents;
    let correlation = CorrelationId::new("physical-node-registration").expect("correlation valid");
    for node_id in node_ids {
        control
            .register_node(
                state,
                physical_node(node_id),
                NodeStatus::new(NodeHealth::Online, TimestampMs::new(0)),
                TimestampMs::new(0),
                &correlation,
                &mut events,
            )
            .expect("node registration succeeds");
    }
}

/// Builds one two-Role MissionPlan with optional physical grounding and Context cardinality.
fn physical_binding_plan(
    mission_name: &str,
    alpha_entity: Option<&str>,
    beta_entity: Option<&str>,
    distinct: bool,
) -> (MissionPlan, TaskRequirement) {
    let mission_id = MissionId::new(mission_name).expect("mission id valid");
    let task_id = TaskId::new("joint-task").expect("task id valid");
    let context_id = domain::CoordinationContextId::new("joint-context")
        .expect("context id valid");
    let alpha = domain::ActorId::new("alpha").expect("actor id valid");
    let beta = domain::ActorId::new("beta").expect("actor id valid");
    let alpha_role = RoleId::new("role-alpha").expect("role id valid");
    let beta_role = RoleId::new("role-beta").expect("role id valid");
    let alpha_context_role =
        domain::ContextRoleId::new("actor-alpha").expect("context role valid");
    let beta_context_role =
        domain::ContextRoleId::new("actor-beta").expect("context role valid");
    let contract =
        CapabilityContractRef::new("mobility", "move", "v1").expect("contract valid");
    let requirement = TaskRequirement::new(
        mission_id.clone(),
        task_id.clone(),
        vec![
            RoleRequirement::new_with_actor_and_contract(
                alpha_role.clone(),
                alpha.clone(),
                CapabilityKind::Mobility,
                contract.clone(),
                None,
            ),
            RoleRequirement::new_with_actor_and_contract(
                beta_role.clone(),
                beta.clone(),
                CapabilityKind::Mobility,
                contract.clone(),
                None,
            ),
        ],
    )
    .expect("task requirement valid");
    let task = PlannedTask::new(
        "establish the joint end state",
        requirement.clone(),
        BTreeMap::from([
            (
                alpha_role.clone(),
                ExecutionIntent::new(contract.clone(), BTreeMap::new()).expect("intent valid"),
            ),
            (
                beta_role.clone(),
                ExecutionIntent::new(contract, BTreeMap::new()).expect("intent valid"),
            ),
        ]),
        Vec::new(),
        domain::TaskContinuity::new(
            context_id.clone(),
            BTreeMap::from([
                (alpha_role, alpha_context_role.clone()),
                (beta_role, beta_context_role.clone()),
            ]),
            BTreeMap::new(),
        ),
    )
    .expect("planned task valid");
    let graph = TaskGraph::new(mission_id.clone(), vec![task]).expect("task graph valid");
    let context = domain::CoordinationContext::new(
        context_id,
        vec![
            domain::ContextRole::new(alpha_context_role.clone(), alpha.clone()),
            domain::ContextRole::new(beta_context_role.clone(), beta.clone()),
        ],
    )
    .expect("context valid");
    let context = if distinct {
        context
            .with_executor_constraints(vec![
                DistinctPhysicalEntityConstraint::new(std::collections::BTreeSet::from([
                    alpha_context_role,
                    beta_context_role,
                ]))
                .expect("constraint valid"),
            ])
            .expect("context constraint valid")
    } else {
        context
    };
    let actor = |id: domain::ActorId, entity: Option<&str>| match entity {
        Some(entity) => domain::MissionActor::new_grounded(
            id,
            PhysicalEntityId::new(entity).expect("entity id valid"),
        ),
        None => domain::MissionActor::new(id),
    };
    let plan = MissionPlan::new_with_actors(
        MissionGoal::new(mission_id, "establish both requested occupancies")
            .expect("goal valid"),
        vec![actor(alpha, alpha_entity), actor(beta, beta_entity)],
        graph,
        vec![context],
    )
    .expect("mission plan valid");
    (plan, requirement)
}

/// Creates and readies the Mission-level Group that will consume one Task commitment.
fn create_ready_physical_group(
    control: &mut ControlPlane,
    plan: &MissionPlan,
    requirement: &TaskRequirement,
    group_id: &ExecutionGroupId,
    events: &mut impl EventSink,
) {
    let correlation = CorrelationId::new("physical-group").expect("correlation valid");
    control
        .create_mission_group(
            group_id.clone(),
            plan,
            TimestampMs::new(0),
            &correlation,
            events,
        )
        .expect("Mission Group creation succeeds");
    control
        .ready_task_execution(
            group_id,
            requirement.task_ref(),
            TimestampMs::new(0),
            &correlation,
            events,
        )
        .expect("Task becomes ready");
}

/// Runs Match, Scheduler, Proposal, and Group Commit without performing Bind.
fn commit_physical_task(
    control: &mut ControlPlane,
    state: &InMemorySharedNodeState,
    plan: &MissionPlan,
    requirement: &TaskRequirement,
    group_id: &ExecutionGroupId,
    events: &mut impl EventSink,
) -> CommittedPlan {
    let correlation = CorrelationId::new("physical-commit").expect("correlation valid");
    let candidates = control
        .match_capabilities_for_mission(
            state,
            plan,
            requirement,
            TimestampMs::new(0),
            &correlation,
            events,
        )
        .expect("matching succeeds");
    let decision = BoundedJointScheduler::new()
        .schedule_task(
            state,
            requirement,
            &candidates,
            TimestampMs::new(0),
            &correlation,
            events,
        )
        .expect("scheduling succeeds");
    let proposal = control
        .propose(
            state,
            requirement,
            &candidates,
            decision.proposed_assignments(),
            TimestampMs::new(0),
            &correlation,
            events,
        )
        .expect("proposal succeeds");
    control
        .commit_for_group_with_state(
            state,
            group_id,
            &proposal,
            TimestampMs::new(0),
            &correlation,
            events,
        )
        .expect("commit succeeds")
}

/// Binds one already committed Task through the authoritative Mission Group API.
fn bind_physical_task(
    control: &mut ControlPlane,
    group_id: &ExecutionGroupId,
    requirement: &TaskRequirement,
    committed: &CommittedPlan,
    events: &mut impl EventSink,
) -> Result<domain::TaskExecution, ControlError> {
    control.bind_task_execution_with_requirement(
        group_id,
        committed,
        requirement,
        TimestampMs::new(0),
        &CorrelationId::new("physical-bind").expect("correlation valid"),
        events,
    )
}

/// The legacy normal Commit API cannot bypass Context cardinality by skipping Group authority.
#[test]
fn physical_proposal_cannot_commit_outside_mission_group() {
    let (plan, requirement) = physical_binding_plan("mission-group-only", None, None, true);
    let mut control = ControlPlane::new();
    let mut state = InMemorySharedNodeState::new();
    let mut events = TestEvents;
    register_physical_nodes(&mut control, &mut state, &["node-a", "node-b"]);
    control
        .install_physical_entity_registry(physical_registry(
            1,
            &[("entity-a", "node-a"), ("entity-b", "node-b")],
        ))
        .expect("registry installs");
    control
        .register_mission_binding_semantics(
            plan.goal().mission_id().clone(),
            plan.binding_semantics(),
        )
        .expect("mission binding semantics register");
    let correlation = CorrelationId::new("group-only").expect("correlation valid");
    let candidates = control
        .match_capabilities_for_mission(
            &state,
            &plan,
            &requirement,
            TimestampMs::new(0),
            &correlation,
            &mut events,
        )
        .expect("candidate matching succeeds");
    let decision = BoundedJointScheduler::new()
        .schedule_task(
            &state,
            &requirement,
            &candidates,
            TimestampMs::new(0),
            &correlation,
            &mut events,
        )
        .expect("scheduler selects two entities");
    let proposal = control
        .propose(
            &state,
            &requirement,
            &candidates,
            decision.proposed_assignments(),
            TimestampMs::new(0),
            &correlation,
            &mut events,
        )
        .expect("proposal is not commitment");
    assert!(matches!(
        control.commit_with_state(
            &state,
            &proposal,
            TimestampMs::new(0),
            &correlation,
            &mut events,
        ),
        Err(ControlError::InvalidProposal(reason)) if reason.contains("Mission Group Commit")
    ));
    assert!(control.reservations.is_empty());
}

/// The current routing profile exposes its one-entity-per-Node limitation explicitly.
#[test]
fn routing_profile_rejects_multiple_entities_hidden_behind_one_node() {
    let result = PhysicalEntityRegistrySnapshot::new(
        PhysicalEntityRegistryId::new("registry").expect("registry id valid"),
        1,
        PhysicalEntityRoutingProfile::OneRoutableEntityPerNode,
        vec![
            PhysicalEntityRegistration::new(
                PhysicalEntityId::new("robot-a").expect("entity valid"),
                NodeId::new("aggregate-node").expect("node valid"),
            ),
            PhysicalEntityRegistration::new(
                PhysicalEntityId::new("robot-b").expect("entity valid"),
                NodeId::new("aggregate-node").expect("node valid"),
            ),
        ],
    );
    assert!(result.is_err());
}

/// A Context hard constraint produces two distinct physical Actor bindings end to end.
#[test]
fn distinct_context_roles_bind_two_physical_entities() {
    let (plan, requirement) = physical_binding_plan("mission-distinct", None, None, true);
    let group_id = ExecutionGroupId::new("group-distinct").expect("group id valid");
    let mut control = ControlPlane::new();
    let mut state = InMemorySharedNodeState::new();
    let mut events = RecordingEvents::default();
    register_physical_nodes(&mut control, &mut state, &["node-a", "node-b"]);
    control
        .install_physical_entity_registry(physical_registry(
            1,
            &[("entity-a", "node-a"), ("entity-b", "node-b")],
        ))
        .expect("registry installs");
    create_ready_physical_group(&mut control, &plan, &requirement, &group_id, &mut events);
    let committed = commit_physical_task(
        &mut control,
        &state,
        &plan,
        &requirement,
        &group_id,
        &mut events,
    );
    assert!(control
        .actor_binding(plan.goal().mission_id(), &domain::ActorId::new("alpha").unwrap())
        .is_none());
    bind_physical_task(
        &mut control,
        &group_id,
        &requirement,
        &committed,
        &mut events,
    )
    .expect("Bind succeeds");
    let alpha = control
        .actor_binding(plan.goal().mission_id(), &domain::ActorId::new("alpha").unwrap())
        .expect("alpha binding exists");
    let beta = control
        .actor_binding(plan.goal().mission_id(), &domain::ActorId::new("beta").unwrap())
        .expect("beta binding exists");
    assert_ne!(alpha.physical_entity_id(), beta.physical_entity_id());
    assert_ne!(alpha.node_id(), beta.node_id());
}

/// One available physical executor makes an explicit pairwise constraint unschedulable.
#[test]
fn one_physical_entity_cannot_satisfy_distinct_context_roles() {
    let (plan, requirement) = physical_binding_plan("mission-one-entity", None, None, true);
    let mut control = ControlPlane::new();
    let mut state = InMemorySharedNodeState::new();
    let mut events = TestEvents;
    register_physical_nodes(&mut control, &mut state, &["node-a"]);
    control
        .install_physical_entity_registry(physical_registry(1, &[("entity-a", "node-a")]))
        .expect("registry installs");
    control
        .register_mission_binding_semantics(
            plan.goal().mission_id().clone(),
            plan.binding_semantics(),
        )
        .expect("semantics register");
    let candidates = control
        .match_capabilities_for_mission(
            &state,
            &plan,
            &requirement,
            TimestampMs::new(0),
            &CorrelationId::new("one-entity").unwrap(),
            &mut events,
        )
        .expect("matching reports who can execute");
    assert!(matches!(
        BoundedJointScheduler::new().schedule_task(
            &state,
            &requirement,
            &candidates,
            TimestampMs::new(0),
            &CorrelationId::new("one-entity").unwrap(),
            &mut events,
        ),
        Err(SchedulerError::NoFeasibleSelection(_))
    ));
}

/// Different logical Actors may share one entity when no cardinality constraint says otherwise.
#[test]
fn unconstrained_actors_may_share_one_physical_entity() {
    let (plan, requirement) = physical_binding_plan(
        "mission-colocated",
        Some("entity-a"),
        Some("entity-a"),
        false,
    );
    let group_id = ExecutionGroupId::new("group-colocated").expect("group id valid");
    let mut control = ControlPlane::new();
    let mut state = InMemorySharedNodeState::new();
    let mut events = TestEvents;
    register_physical_nodes(&mut control, &mut state, &["node-a"]);
    control
        .install_physical_entity_registry(physical_registry(1, &[("entity-a", "node-a")]))
        .expect("registry installs");
    create_ready_physical_group(&mut control, &plan, &requirement, &group_id, &mut events);
    let committed = commit_physical_task(
        &mut control,
        &state,
        &plan,
        &requirement,
        &group_id,
        &mut events,
    );
    bind_physical_task(
        &mut control,
        &group_id,
        &requirement,
        &committed,
        &mut events,
    )
    .expect("unconstrained Bind succeeds");
    let alpha = control
        .actor_binding(plan.goal().mission_id(), &domain::ActorId::new("alpha").unwrap())
        .expect("alpha binding exists");
    let beta = control
        .actor_binding(plan.goal().mission_id(), &domain::ActorId::new("beta").unwrap())
        .expect("beta binding exists");
    assert_eq!(alpha.physical_entity_id(), beta.physical_entity_id());
}

/// Deployment registry presence cannot change an ungrounded, unconstrained Mission's bindings.
#[test]
fn ungrounded_unconstrained_pipeline_is_independent_of_registry_presence() {
    let (plan, requirement) = physical_binding_plan("mission-logical-only", None, None, false);
    let plan = plan.with_v0_8_contract();
    let group_id = ExecutionGroupId::new("group-logical-only").expect("group valid");
    let correlation = CorrelationId::new("logical-only").expect("correlation valid");
    let now = TimestampMs::new(0);
    let mut results = Vec::new();
    for install_registry in [false, true] {
        let mut control = ControlPlane::new();
        let mut state = InMemorySharedNodeState::new();
        let mut events = TestEvents;
        register_physical_nodes(&mut control, &mut state, &["node-a"]);
        if install_registry {
            control
                .install_physical_entity_registry(physical_registry(1, &[("entity-a", "node-a")]))
                .expect("registry installs");
        }
        create_ready_physical_group(&mut control, &plan, &requirement, &group_id, &mut events);
        let candidates = control
            .match_capabilities_for_mission(
                &state, &plan, &requirement, now, &correlation, &mut events,
            )
            .expect("logical Actors match");
        assert!(candidates.distinct_actor_groups().is_empty());
        assert!(candidates.physical_entity_for_node(&NodeId::new("node-a").unwrap()).is_none());
        let decision = BoundedJointScheduler::new()
            .schedule_task(&state, &requirement, &candidates, now, &correlation, &mut events)
            .expect("Actors may share the only Node");
        let proposal = control
            .propose(
                &state, &requirement, &candidates, decision.proposed_assignments(),
                now, &correlation, &mut events,
            )
            .expect("proposal validates");
        assert!(proposal.role_physical_entities().is_empty());
        let committed = control
            .commit_for_group_with_state(&state, &group_id, &proposal, now, &correlation, &mut events)
            .expect("commit succeeds");
        let execution = bind_physical_task(
            &mut control, &group_id, &requirement, &committed, &mut events,
        ).expect("registry presence cannot reject a logical binding");
        let bindings = ["alpha", "beta"].map(|actor| {
            let binding = control.actor_binding(
                plan.goal().mission_id(), &domain::ActorId::new(actor).unwrap(),
            ).expect("Actor bound").clone();
            assert!(binding.physical_entity_id().is_none());
            assert!(binding.registry_id().is_none());
            binding
        });
        results.push((execution, bindings));
    }
    assert_eq!(results[0], results[1]);
}

/// Grounding narrows each logical Actor to its admitted physical entity rather than a label guess.
#[test]
fn grounded_actor_matches_only_the_registered_physical_entity() {
    let (plan, requirement) = physical_binding_plan(
        "mission-grounded",
        Some("entity-b"),
        Some("entity-a"),
        false,
    );
    let mut control = ControlPlane::new();
    let mut state = InMemorySharedNodeState::new();
    let mut events = TestEvents;
    register_physical_nodes(&mut control, &mut state, &["node-a", "node-b"]);
    control
        .install_physical_entity_registry(physical_registry(
            1,
            &[("entity-a", "node-a"), ("entity-b", "node-b")],
        ))
        .expect("registry installs");
    control
        .register_mission_binding_semantics(
            plan.goal().mission_id().clone(),
            plan.binding_semantics(),
        )
        .expect("semantics register");
    let candidates = control
        .match_capabilities_for_mission(
            &state,
            &plan,
            &requirement,
            TimestampMs::new(0),
            &CorrelationId::new("grounded-match").unwrap(),
            &mut events,
        )
        .expect("matching succeeds");
    assert_eq!(
        candidates
            .for_role(&RoleId::new("role-alpha").unwrap())
            .expect("alpha candidates")
            .node_ids(),
        &[NodeId::new("node-b").unwrap()]
    );
}

/// A grounded entity absent from current deployment evidence fails closed before scheduling.
#[test]
fn unknown_grounded_entity_is_rejected() {
    let (plan, requirement) = physical_binding_plan(
        "mission-unknown-grounding",
        Some("entity-missing"),
        Some("entity-a"),
        false,
    );
    let mut control = ControlPlane::new();
    let mut state = InMemorySharedNodeState::new();
    let mut events = TestEvents;
    register_physical_nodes(&mut control, &mut state, &["node-a"]);
    control
        .install_physical_entity_registry(physical_registry(1, &[("entity-a", "node-a")]))
        .expect("registry installs");
    control
        .register_mission_binding_semantics(
            plan.goal().mission_id().clone(),
            plan.binding_semantics(),
        )
        .expect("semantics register");
    assert!(matches!(
        control.match_capabilities_for_mission(
            &state,
            &plan,
            &requirement,
            TimestampMs::new(0),
            &CorrelationId::new("unknown-grounding").unwrap(),
            &mut events,
        ),
        Err(ControlError::ActorGroundingUnresolved { .. })
    ));
}

/// Registry movement after Proposal is fenced by Commit without creating Actor authority.
#[test]
fn registry_change_between_proposal_and_commit_is_rejected() {
    let (plan, requirement) = physical_binding_plan(
        "mission-commit-race",
        Some("entity-a"),
        Some("entity-b"),
        false,
    );
    let group_id = ExecutionGroupId::new("group-commit-race").expect("group id valid");
    let mut control = ControlPlane::new();
    let mut state = InMemorySharedNodeState::new();
    let mut events = TestEvents;
    register_physical_nodes(&mut control, &mut state, &["node-a", "node-b"]);
    control
        .install_physical_entity_registry(physical_registry(
            1,
            &[("entity-a", "node-a"), ("entity-b", "node-b")],
        ))
        .expect("registry installs");
    create_ready_physical_group(&mut control, &plan, &requirement, &group_id, &mut events);
    let candidates = control
        .match_capabilities_for_mission(
            &state,
            &plan,
            &requirement,
            TimestampMs::new(0),
            &CorrelationId::new("race").unwrap(),
            &mut events,
        )
        .expect("matching succeeds");
    let decision = BoundedJointScheduler::new()
        .schedule_task(
            &state,
            &requirement,
            &candidates,
            TimestampMs::new(0),
            &CorrelationId::new("race").unwrap(),
            &mut events,
        )
        .expect("scheduling succeeds");
    let proposal = control
        .propose(
            &state,
            &requirement,
            &candidates,
            decision.proposed_assignments(),
            TimestampMs::new(0),
            &CorrelationId::new("race").unwrap(),
            &mut events,
        )
        .expect("proposal succeeds");
    control
        .install_physical_entity_registry(physical_registry(
            2,
            &[("entity-a", "node-b"), ("entity-b", "node-a")],
        ))
        .expect("unbound topology may advance");
    assert!(control
        .commit_for_group_with_state(
            &state,
            &group_id,
            &proposal,
            TimestampMs::new(0),
            &CorrelationId::new("race").unwrap(),
            &mut events,
        )
        .is_err());
    assert!(control
        .actor_binding(plan.goal().mission_id(), &domain::ActorId::new("alpha").unwrap())
        .is_none());
}

/// Bind consumes the exact committed entity and rejects topology changes after Commit atomically.
#[test]
fn registry_change_between_commit_and_bind_is_rejected_without_group_mutation() {
    let (plan, requirement) = physical_binding_plan(
        "mission-bind-race",
        Some("entity-a"),
        Some("entity-b"),
        false,
    );
    let group_id = ExecutionGroupId::new("group-bind-race").expect("group id valid");
    let mut control = ControlPlane::new();
    let mut state = InMemorySharedNodeState::new();
    let mut events = TestEvents;
    register_physical_nodes(&mut control, &mut state, &["node-a", "node-b"]);
    control
        .install_physical_entity_registry(physical_registry(
            1,
            &[("entity-a", "node-a"), ("entity-b", "node-b")],
        ))
        .expect("registry installs");
    create_ready_physical_group(&mut control, &plan, &requirement, &group_id, &mut events);
    let committed = commit_physical_task(
        &mut control,
        &state,
        &plan,
        &requirement,
        &group_id,
        &mut events,
    );
    control
        .install_physical_entity_registry(physical_registry(
            2,
            &[("entity-a", "node-b"), ("entity-b", "node-a")],
        ))
        .expect("unbound topology may advance");
    assert!(bind_physical_task(
        &mut control,
        &group_id,
        &requirement,
        &committed,
        &mut events,
    )
    .is_err());
    let execution = control
        .group(&group_id)
        .and_then(|group| group.task_execution(requirement.task_ref()))
        .expect("Task remains registered");
    assert_eq!(execution.lifecycle(), domain::TaskExecutionLifecycle::Ready);
    assert!(execution.assignments().is_empty());
    assert!(control
        .actor_binding(plan.goal().mission_id(), &domain::ActorId::new("alpha").unwrap())
        .is_none());
}

/// Restart preserves durable Actor semantics but requires current topology to be reacquired.
#[test]
fn checkpoint_restore_reacquires_and_fences_physical_topology() {
    let (plan, requirement) = physical_binding_plan("mission-restart", None, None, true);
    let group_id = ExecutionGroupId::new("group-restart").expect("group id valid");
    let mut control = ControlPlane::new();
    let mut state = InMemorySharedNodeState::new();
    let mut events = TestEvents;
    register_physical_nodes(&mut control, &mut state, &["node-a", "node-b"]);
    control
        .install_physical_entity_registry(physical_registry(
            1,
            &[("entity-a", "node-a"), ("entity-b", "node-b")],
        ))
        .expect("registry installs");
    create_ready_physical_group(&mut control, &plan, &requirement, &group_id, &mut events);
    let committed = commit_physical_task(
        &mut control,
        &state,
        &plan,
        &requirement,
        &group_id,
        &mut events,
    );
    bind_physical_task(
        &mut control,
        &group_id,
        &requirement,
        &committed,
        &mut events,
    )
    .expect("Bind succeeds");
    let checkpoint = serde_json::to_string(&control.checkpoint()).expect("checkpoint serializes");
    let mut restored = ControlPlane::restore(
        serde_json::from_str(&checkpoint).expect("checkpoint deserializes"),
    )
    .expect("checkpoint restores");
    assert!(restored.physical_entity_registry().is_none());
    assert!(matches!(
        restored.validate_physical_entity_registry_on_restore(),
        Err(ControlError::InvalidProposal(reason)) if reason.contains("current deployment registry")
    ));
    restored
        .install_physical_entity_registry(physical_registry(
            2,
            &[("entity-a", "node-a"), ("entity-b", "node-b")],
        ))
        .expect("same routing at a newer revision is accepted");
    restored
        .validate_physical_entity_registry_on_restore()
        .expect("current topology covers restored bindings");

    let mut moved = ControlPlane::restore(
        serde_json::from_str(&checkpoint).expect("checkpoint deserializes again"),
    )
    .expect("checkpoint restores again");
    assert!(matches!(
        moved.install_physical_entity_registry(physical_registry(
            2,
            &[("entity-a", "node-b"), ("entity-b", "node-a")],
        )),
        Err(ControlError::ActorBindingRequiresReconciliation { .. })
    ));
    let mut rolled_back = ControlPlane::restore(
        serde_json::from_str(&checkpoint).expect("checkpoint deserializes"),
    )
    .expect("checkpoint restores for stale revision test");
    assert!(matches!(
        rolled_back.install_physical_entity_registry(physical_registry(
            0,
            &[("entity-a", "node-a"), ("entity-b", "node-b")],
        )),
        Err(ControlError::ActorBindingRequiresReconciliation { .. })
    ));
}

/// Same routing at a newer revision preserves the original binding provenance for later Tasks.
#[test]
fn registry_revision_update_without_topology_change_preserves_actor_binding() {
    let (plan, requirement) = physical_binding_plan("mission-revision", None, None, true);
    let group_id = ExecutionGroupId::new("group-revision").unwrap();
    let mut control = ControlPlane::new();
    let mut state = InMemorySharedNodeState::new();
    let mut events = TestEvents;
    register_physical_nodes(&mut control, &mut state, &["node-a", "node-b"]);
    let entities = &[("entity-a", "node-a"), ("entity-b", "node-b")];
    control
        .install_physical_entity_registry(physical_registry(1, entities))
        .expect("initial registry installs");
    create_ready_physical_group(&mut control, &plan, &requirement, &group_id, &mut events);
    let committed = commit_physical_task(
        &mut control,
        &state,
        &plan,
        &requirement,
        &group_id,
        &mut events,
    );
    bind_physical_task(
        &mut control,
        &group_id,
        &requirement,
        &committed,
        &mut events,
    )
    .expect("first binding succeeds");
    control
        .install_physical_entity_registry(physical_registry(2, entities))
        .expect("same topology at newer revision is valid");
    let alpha = domain::ActorId::new("alpha").unwrap();
    assert_eq!(
        control
            .actor_binding(plan.goal().mission_id(), &alpha)
            .expect("binding exists")
            .registry_revision(),
        Some(1)
    );
    assert!(matches!(
        control.install_physical_entity_registry(physical_registry(
            2,
            &[("entity-a", "node-b"), ("entity-b", "node-a")]
        )),
        Err(ControlError::InvalidProposal(reason)) if reason.contains("without a new revision")
    ));
}

/// Ordinary Role recovery cannot silently migrate an already bound logical Actor.
#[test]
fn recovery_preserves_bound_physical_entity_and_remains_pending() {
    let (plan, requirement) = physical_binding_plan("mission-recovery-entity", None, None, true);
    let group_id = ExecutionGroupId::new("group-recovery-entity").expect("group id valid");
    let mut control = ControlPlane::new();
    let mut state = InMemorySharedNodeState::new();
    let mut events = TestEvents;
    register_physical_nodes(&mut control, &mut state, &["node-a", "node-b"]);
    control
        .install_physical_entity_registry(physical_registry(
            1,
            &[("entity-a", "node-a"), ("entity-b", "node-b")],
        ))
        .expect("registry installs");
    create_ready_physical_group(&mut control, &plan, &requirement, &group_id, &mut events);
    let committed = commit_physical_task(
        &mut control,
        &state,
        &plan,
        &requirement,
        &group_id,
        &mut events,
    );
    bind_physical_task(
        &mut control,
        &group_id,
        &requirement,
        &committed,
        &mut events,
    )
    .expect("Bind succeeds");
    control
        .activate_task_execution(
            &group_id,
            requirement.task_ref(),
            TimestampMs::new(0),
            &CorrelationId::new("recover").unwrap(),
            &mut events,
        )
        .expect("Task activates");
    state
        .record_node_health(NodeHealthObservation::new(
            NodeId::new("node-a").unwrap(),
            NodeStatus::new(NodeHealth::Offline, TimestampMs::new(1)),
            TimestampMs::new(1),
        ))
        .expect("failure observation records");
    let need = match control
        .assess_group(
            &state,
            &group_id,
            &requirement,
            TimestampMs::new(1),
            &CorrelationId::new("recover").unwrap(),
            &mut events,
        )
        .expect("assessment succeeds")
    {
        ReconciliationAssessment::RoleRecoveryRequired(need) => need,
        ReconciliationAssessment::NoAction => panic!("offline binding must require recovery"),
    };
    control
        .begin_role_recovery(
            &need,
            TimestampMs::new(2),
            &CorrelationId::new("recover").unwrap(),
            &mut events,
        )
        .expect("recovery begins");
    let candidates = control
        .match_recovery_candidates(
            &state,
            &need,
            &requirement,
            TimestampMs::new(2),
            &CorrelationId::new("recover").unwrap(),
            &mut events,
        )
        .expect("recovery matching remains valid");
    assert!(candidates.candidate_node_ids().is_empty());
    assert_eq!(
        control
            .actor_binding(
                plan.goal().mission_id(),
                &domain::ActorId::new("alpha").unwrap(),
            )
            .and_then(domain::ActorBinding::physical_entity_id),
        Some(&PhysicalEntityId::new("entity-a").unwrap())
    );
}

/// Restore rejects a checkpoint whose durable Actor bindings violate Context cardinality.
#[test]
fn checkpoint_rejects_duplicate_physical_entity_occupancy() {
    let (plan, _) = physical_binding_plan("mission-corrupt", None, None, true);
    let mut control = ControlPlane::new();
    control
        .register_mission_binding_semantics(
            plan.goal().mission_id().clone(),
            plan.binding_semantics(),
        )
        .expect("semantics register");
    let registry_id = PhysicalEntityRegistryId::new("test-registry").unwrap();
    control.actor_bindings_for_test().insert(
        (
            plan.goal().mission_id().clone(),
            domain::ActorId::new("alpha").unwrap(),
        ),
        domain::ActorBinding::new_physical(
            plan.goal().mission_id().clone(),
            domain::ActorId::new("alpha").unwrap(),
            NodeId::new("node-a").unwrap(),
            PhysicalEntityId::new("entity-a").unwrap(),
            registry_id.clone(),
            1,
        ),
    );
    control.actor_bindings_for_test().insert(
        (
            plan.goal().mission_id().clone(),
            domain::ActorId::new("beta").unwrap(),
        ),
        domain::ActorBinding::new_physical(
            plan.goal().mission_id().clone(),
            domain::ActorId::new("beta").unwrap(),
            NodeId::new("node-b").unwrap(),
            PhysicalEntityId::new("entity-a").unwrap(),
            registry_id,
            1,
        ),
    );
    let encoded = serde_json::to_string(&control.checkpoint()).expect("checkpoint serializes");
    assert!(ControlPlane::restore(serde_json::from_str(&encoded).unwrap()).is_err());
}
