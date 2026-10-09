/// Creates a short-lived attributed ordering for one exact Task Role.
fn initial_preference(requirement: &TaskRequirement, node: &str) -> InitialCandidatePreferences {
    InitialCandidatePreferences::new(
        requirement.task_ref().clone(),
        BTreeMap::from([(requirement.roles()[0].role_id().clone(), BTreeMap::from([
            (NodeId::new(node).unwrap(), 0),
        ]))]),
        format!("sha256:{}", "a".repeat(64)), TimestampMs::new(0), TimestampMs::new(1_000),
    ).expect("bounded preference")
}

/// Registers interchangeable endpoints without placing or binding a logical Actor.
fn initial_preference_state(control: &mut ControlPlane) -> InMemorySharedNodeState {
    let mut state = InMemorySharedNodeState::new();
    for (node, resource) in [("dog-a", "space-a"), ("dog-b", "space-b")] {
        control.register_node(&mut state, actor_node_with_contracts(node, CapabilityKind::Mobility,
            &["go-to-shelf", "return-user"], resource, ResourceKind::Space),
            NodeStatus::new(NodeHealth::Online, TimestampMs::new(0)), TimestampMs::new(0), &correlation(), &mut TestEvents).unwrap();
    }
    state
}

/// Positive evidence reorders search but cannot exclude fallback Nodes or grant resources.
#[test]
fn initial_preferences_respect_candidates_resources_and_expiry() {
    let (plan, task, _) = continuity_plan();
    let mut control = ControlPlane::new();
    let state = initial_preference_state(&mut control);
    let before = serde_json::to_string(&control.checkpoint()).unwrap();
    control.set_initial_candidate_preferences(&plan, initial_preference(&task, "dog-b")).unwrap();
    assert_eq!(serde_json::to_string(&control.checkpoint()).unwrap(), before);
    let candidates = control.match_capabilities_for_mission(&state, &plan, &task, TimestampMs::new(0), &correlation(), &mut TestEvents).unwrap();
    assert_eq!(candidates.roles()[0].node_ids().len(), 2);
    let scheduler = BoundedJointScheduler::new();
    for (time, occupied, expected) in [
        (0, vec![], Some("dog-b")), (0, vec!["space-b"], Some("dog-a")),
        (0, vec!["space-a", "space-b"], None), (1_000, vec![], Some("dog-a")),
    ] {
        let snapshot = SchedulingSnapshot::new(0, occupied.into_iter().map(|resource| SchedulingOccupancy::new(ResourceId::new(resource).unwrap(), TimestampMs::new(0), None)).collect());
        let outcome = scheduler.schedule_task_with_snapshot(&state, &task, &candidates, &snapshot, TimestampMs::new(0), TimestampMs::new(time)).unwrap();
        if let Some(expected) = expected {
            let TaskSchedulingOutcome::SelectedNow(decision) = outcome else { panic!("selection expected"); };
            assert_eq!(decision.selections()[0].node_id().as_str(), expected);
        } else { assert_eq!(outcome, TaskSchedulingOutcome::Deferred); }
    }
    control.set_actor_node_constraint(plan.goal().mission_id().clone(), domain::ActorId::new("carrier").unwrap(), NodeId::new("dog-a").unwrap()).unwrap();
    let constrained = control.match_capabilities_for_mission(&state, &plan, &task, TimestampMs::new(0), &correlation(), &mut TestEvents).unwrap();
    let decision = scheduler.schedule_task(&state, &task, &constrained, TimestampMs::new(0), &correlation(), &mut TestEvents).unwrap();
    assert_eq!(decision.selections()[0].node_id().as_str(), "dog-a");
    assert!(control.actor_binding(plan.goal().mission_id(), &domain::ActorId::new("carrier").unwrap()).is_none());
}

/// Initial evidence is immutable, Task-scoped and never renewed by checkpoint restoration.
#[test]
fn initial_preferences_reject_replacement_and_restore_without_hints() {
    let (plan, task, _) = continuity_plan();
    let mut control = ControlPlane::new();
    let _state = initial_preference_state(&mut control);
    control.set_initial_candidate_preferences(&plan, initial_preference(&task, "dog-b")).unwrap();
    assert!(control.set_initial_candidate_preferences(&plan, initial_preference(&task, "dog-a")).is_err());
    let mut other = task.clone();
    other = TaskRequirement::new(domain::MissionId::new("another-mission").unwrap(), other.task_id().clone(), other.roles().to_vec()).unwrap();
    assert!(control.set_initial_candidate_preferences(&plan, initial_preference(&other, "dog-b")).is_err());
    let mut restored = ControlPlane::restore(control.checkpoint()).unwrap();
    assert!(restored.initial_candidate_preferences.is_empty());
    let state = initial_preference_state(&mut restored);
    let candidates = restored.match_capabilities_for_mission(&state, &plan, &task, TimestampMs::new(0), &correlation(), &mut TestEvents).unwrap();
    assert_eq!(candidates.initial_priority(task.roles()[0].role_id(), &NodeId::new("dog-b").unwrap(), TimestampMs::new(0)), u32::MAX);
    for (source, end) in [("invalid".to_string(), 1), (format!("sha256:{}", "a".repeat(64)), MAX_INITIAL_PREFERENCE_AGE_MS + 1)] {
        assert!(InitialCandidatePreferences::new(task.task_ref().clone(), BTreeMap::from([(task.roles()[0].role_id().clone(), BTreeMap::from([(NodeId::new("dog-b").unwrap(), 0)]))]), source, TimestampMs::new(0), TimestampMs::new(end)).is_err());
    }
}

/// First successful Bind fences every initial hint while preserving Actor continuity.
#[test]
fn initial_preferences_are_consumed_by_bind_before_later_tasks() {
    let (plan, first, later) = continuity_plan();
    let mut control = ControlPlane::new();
    let state = initial_preference_state(&mut control);
    control.set_initial_candidate_preferences(&plan, initial_preference(&first, "dog-b")).unwrap();
    control.set_initial_candidate_preferences(&plan, initial_preference(&later, "dog-a")).unwrap();
    let now = TimestampMs::new(0);
    let group = ExecutionGroupId::new("initial-group").unwrap();
    control.create_mission_group(group.clone(), &plan, now, &correlation(), &mut TestEvents).unwrap();
    control.ready_task_execution(&group, first.task_ref(), now, &correlation(), &mut TestEvents).unwrap();
    let candidates = control.match_capabilities_for_mission(&state, &plan, &first, now, &correlation(), &mut TestEvents).unwrap();
    let decision = BoundedJointScheduler::new().schedule_task(&state, &first, &candidates, now, &correlation(), &mut TestEvents).unwrap();
    let proposal = control.propose(&state, &first, &candidates, decision.proposed_assignments(), now, &correlation(), &mut TestEvents).unwrap();
    assert_eq!(control.initial_candidate_preferences.len(), 2);
    let committed = control.commit_with_state(&state, &proposal, now, &correlation(), &mut TestEvents).unwrap();
    control.bind_task_execution_with_requirement(&group, &committed, &first, now, &correlation(), &mut TestEvents).unwrap();
    assert!(control.initial_candidate_preferences.is_empty());
    assert!(control.set_initial_candidate_preferences(&plan, initial_preference(&later, "dog-a")).is_err());
    let later_candidates = control.match_capabilities_for_mission(&state, &plan, &later, now, &correlation(), &mut TestEvents).unwrap();
    assert_eq!(later_candidates.roles()[0].node_ids(), &[NodeId::new("dog-b").unwrap()]);
    assert_eq!(later_candidates.initial_priority(later.roles()[0].role_id(), &NodeId::new("dog-a").unwrap(), now), u32::MAX);
}
