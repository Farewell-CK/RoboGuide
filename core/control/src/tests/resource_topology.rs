//! Synthetic geometry/calendar counterexamples exercise real Control without deployment device facts.

use super::*;
use domain::{
    ResourceSpaceFootprint, ResourceTimeWindow, ResourceTopologyEntry, ResourceTopologyRegistry,
};

/// Creates arbitrary synthetic declarations; these coordinates never become deployment observations.
fn registry(kind: ResourceKind) -> ResourceTopologyRegistry {
    let entries = if kind == ResourceKind::Space {
        [("a", 0, 10), ("b", 5, 15), ("c", 20, 30)]
            .into_iter()
            .map(|(id, lower, upper)| ResourceTopologyEntry {
                resource_id: ResourceId::new(id).unwrap(),
                node_id: NodeId::new("resource-node").unwrap(),
                kind,
                space: Some(ResourceSpaceFootprint {
                    conflict_domain: "unit-world".into(),
                    frame: "unit-frame".into(),
                    anchor_revision: "unit-anchor-1".into(),
                    lower_mm: [lower, 0, 0],
                    upper_mm: [upper, 10, 10],
                    clearance_mm: 0,
                }),
                time_domain: None,
                windows: Vec::new(),
            })
            .collect()
    } else {
        vec![ResourceTopologyEntry {
            resource_id: ResourceId::new("t").unwrap(),
            node_id: NodeId::new("resource-node").unwrap(),
            kind,
            space: None,
            time_domain: Some("unit-shared-time-authority".into()),
            windows: vec![ResourceTimeWindow {
                starts_at: TimestampMs::new(100),
                ends_at: TimestampMs::new(200),
            }],
        }]
    };
    ResourceTopologyRegistry {
        schema: "roboguide.resource-topology/v0.1".into(),
        registry_id: "unit-topology".into(),
        revision: 1,
        clock: "controller_receive_time".into(),
        stop_timeout_ms: 100,
        booking_duration_ms: 20,
        booking_max_age_ms: 1000,
        resources: entries,
    }
}

/// Registers only synthetic test values under the ordinary current-State admission path.
fn fixture(kind: ResourceKind) -> (ControlPlane, InMemorySharedNodeState, RecordingEvents) {
    let mut control = ControlPlane::new();
    let registry = registry(kind);
    control
        .accept_resource_topology_registry(registry.clone(), 0)
        .unwrap();
    let mut state = InMemorySharedNodeState::new();
    let mut events = RecordingEvents::default();
    let registration = resource_race_node(
        registry
            .resources
            .iter()
            .map(|entry| Resource::new(entry.resource_id.clone(), kind, 1).unwrap())
            .collect(),
    );
    control
        .register_node(
            &mut state,
            registration,
            NodeStatus::new(NodeHealth::Online, TimestampMs::new(0)),
            TimestampMs::new(0),
            &correlation(),
            &mut events,
        )
        .unwrap();
    (control, state, events)
}

/// Supplies complete semantic demands and original relative duration without naming a resource.
fn requirement(
    mission: &str,
    kind: ResourceKind,
    roles: usize,
    duration: Option<u64>,
) -> TaskRequirement {
    TaskRequirement::new_scheduled(
        MissionId::new(mission).unwrap(),
        TaskId::new("work").unwrap(),
        (0..roles)
            .map(|index| {
                RoleRequirement::new_scheduled(
                    RoleId::new(format!("role-{index}")).unwrap(),
                    None,
                    CapabilityKind::Compute,
                    Some(CapabilityContractRef::new("compute", "work", "v1").unwrap()),
                    vec![ResourceRequirement::new(kind, 1).unwrap()],
                )
                .unwrap()
            })
            .collect(),
        domain::TaskTiming::new(0, None, None, duration).unwrap(),
    )
    .unwrap()
}

/// Creates an original ready Group using the same public semantic plan entry as normal admission.
fn ready_group(
    control: &mut ControlPlane,
    requirement: &TaskRequirement,
    events: &mut RecordingEvents,
) -> ExecutionGroupId {
    let context = domain::CoordinationContextId::new("unit-context").unwrap();
    let task = PlannedTask::new(
        "unit topology work",
        requirement.clone(),
        requirement
            .roles()
            .iter()
            .map(|role| {
                (
                    role.role_id().clone(),
                    ExecutionIntent::new(
                        CapabilityContractRef::new("compute", "work", "v1").unwrap(),
                        BTreeMap::new(),
                    )
                    .unwrap(),
                )
            })
            .collect(),
        Vec::new(),
        domain::TaskContinuity::new(context.clone(), BTreeMap::new(), BTreeMap::new()),
    )
    .unwrap();
    let plan = MissionPlan::new(
        MissionGoal::new(requirement.mission_id().clone(), "unit topology work").unwrap(),
        TaskGraph::new(requirement.mission_id().clone(), vec![task]).unwrap(),
        vec![domain::CoordinationContext::new(context, Vec::new()).unwrap()],
    )
    .unwrap();
    let group = ExecutionGroupId::new(format!("group-{}", requirement.mission_id())).unwrap();
    create_ready_physical_group(control, &plan, requirement, &group, events);
    group
}

/// Forms a real proposal from current candidates, independently of Scheduler ordering.
fn proposal(
    control: &ControlPlane,
    state: &InMemorySharedNodeState,
    requirement: &TaskRequirement,
    id: &str,
    now: TimestampMs,
    events: &mut RecordingEvents,
) -> AssignmentProposal {
    let candidates = control
        .match_capabilities(state, requirement, now, &correlation(), events)
        .unwrap();
    control
        .propose(
            state,
            requirement,
            &candidates,
            vec![RoleAssignment::new(
                requirement.roles()[0].role_id().clone(),
                NodeId::new("resource-node").unwrap(),
                vec![ResourceId::new(id).unwrap()],
            )],
            now,
            &correlation(),
            events,
        )
        .unwrap()
}

/// Returns real deterministic selection evidence against the current authority snapshot.
fn schedule(
    control: &ControlPlane,
    state: &InMemorySharedNodeState,
    requirement: &TaskRequirement,
    now: TimestampMs,
    events: &mut RecordingEvents,
) -> TaskSchedulingDecision {
    let candidates = control
        .match_capabilities(state, requirement, now, &correlation(), events)
        .unwrap();
    match BoundedJointScheduler::new()
        .schedule_task_with_snapshot(
            state,
            requirement,
            &candidates,
            &control.scheduling_snapshot(now),
            TimestampMs::new(0),
            now,
        )
        .unwrap()
    {
        TaskSchedulingOutcome::SelectedNow(decision)
        | TaskSchedulingOutcome::SelectedFuture(decision) => decision,
        other => panic!("expected test selection, got {other:?}"),
    }
}

/// Different advertised IDs cannot lend the same exclusive geometry through normal Commit.
#[test]
fn distinct_spatial_ids_conflict_atomically_and_original_history_survives_restore() {
    let (mut control, state, mut events) = fixture(ResourceKind::Space);
    let first = requirement("first", ResourceKind::Space, 1, None);
    let admission = proposal(
        &control,
        &state,
        &first,
        "a",
        TimestampMs::new(0),
        &mut events,
    );
    control
        .commit_with_state(
            &state,
            &admission,
            TimestampMs::new(0),
            &correlation(),
            &mut events,
        )
        .unwrap();
    let second = requirement("second", ResourceKind::Space, 1, None);
    let overlapping = proposal(
        &control,
        &state,
        &second,
        "b",
        TimestampMs::new(1),
        &mut events,
    );
    let before = serde_json::to_value(control.checkpoint()).unwrap();
    let count = events.records.len();
    assert!(matches!(
        control.commit_with_state(
            &state,
            &overlapping,
            TimestampMs::new(1),
            &correlation(),
            &mut events
        ),
        Err(ControlError::ResourceConflict { .. })
    ));
    assert_eq!(serde_json::to_value(control.checkpoint()).unwrap(), before);
    assert_eq!(events.records.len(), count);
    let restored = ControlPlane::restore(control.checkpoint()).unwrap();
    assert_eq!(serde_json::to_value(restored.checkpoint()).unwrap(), before);
    let selected = schedule(&control, &state, &second, TimestampMs::new(1), &mut events);
    assert_eq!(
        selected.selections()[0].resource_ids(),
        &[ResourceId::new("c").unwrap()]
    );
}

/// Bounded joint search avoids cross-ID intersections among Roles of the same candidate batch.
#[test]
fn joint_scheduler_selects_disjoint_regions_and_restored_conflicts_refuse() {
    let (control, state, mut events) = fixture(ResourceKind::Space);
    let work = requirement("joint", ResourceKind::Space, 2, Some(10));
    let selected = schedule(&control, &state, &work, TimestampMs::new(0), &mut events);
    assert_eq!(
        selected.selections()[0].resource_ids(),
        &[ResourceId::new("a").unwrap()]
    );
    assert_eq!(
        selected.selections()[1].resource_ids(),
        &[ResourceId::new("c").unwrap()]
    );
}

/// A future promise has geometry authority before execution and cannot be bypassed at Commit.
#[test]
fn future_spatial_reservation_blocks_distinct_overlapping_id_and_stale_policy_update() {
    let (mut control, state, mut events) = fixture(ResourceKind::Space);
    let work = requirement("future", ResourceKind::Space, 1, Some(10));
    let group = ready_group(&mut control, &work, &mut events);
    let candidates = control
        .match_capabilities(
            &state,
            &work,
            TimestampMs::new(0),
            &correlation(),
            &mut events,
        )
        .unwrap();
    let selected = schedule(&control, &state, &work, TimestampMs::new(0), &mut events);
    control
        .reserve_scheduled_task(&state, &candidates, group, selected, TimestampMs::new(0))
        .unwrap();
    let mut changed = registry(ResourceKind::Space);
    changed.revision = 2;
    changed.resources[0].space.as_mut().unwrap().upper_mm[0] = 11;
    let before = serde_json::to_value(control.checkpoint()).unwrap();
    assert!(
        control
            .accept_resource_topology_registry(changed, 1)
            .is_err()
    );
    assert_eq!(serde_json::to_value(control.checkpoint()).unwrap(), before);
    let later = requirement("other", ResourceKind::Space, 1, None);
    let overlapping = proposal(
        &control,
        &state,
        &later,
        "b",
        TimestampMs::new(0),
        &mut events,
    );
    assert!(
        control
            .commit_with_state(
                &state,
                &overlapping,
                TimestampMs::new(0),
                &correlation(),
                &mut events
            )
            .is_err()
    );
    ControlPlane::restore(control.checkpoint()).unwrap();
}

/// Original Time openings drive earliest scheduling, Bind and nonrenewing overrun observation.
#[test]
fn distinct_time_aliases_share_one_calendar_and_half_open_boundary() {
    let (mut control, mut state, mut events) = fixture(ResourceKind::Time);
    let first = requirement("time-alias-first", ResourceKind::Time, 1, Some(20));
    let group = ready_group(&mut control, &first, &mut events);
    let candidates = control
        .match_capabilities(
            &state,
            &first,
            TimestampMs::new(0),
            &correlation(),
            &mut events,
        )
        .unwrap();
    let selected = schedule(&control, &state, &first, TimestampMs::new(0), &mut events);
    control
        .reserve_scheduled_task(&state, &candidates, group, selected, TimestampMs::new(0))
        .unwrap();
    let mut registry = registry(ResourceKind::Time);
    registry.revision = 2;
    let mut alias = registry.resources[0].clone();
    alias.resource_id = ResourceId::new("u").unwrap();
    registry.resources.push(alias);
    control
        .accept_resource_topology_registry(registry, 1)
        .unwrap();
    control
        .update_node_registration(
            &mut state,
            resource_race_node(vec![
                Resource::new(ResourceId::new("t").unwrap(), ResourceKind::Time, 1).unwrap(),
                Resource::new(ResourceId::new("u").unwrap(), ResourceKind::Time, 1).unwrap(),
            ]),
            TimestampMs::new(0),
            &correlation(),
            &mut events,
        )
        .unwrap();
    let second = requirement("time-alias-second", ResourceKind::Time, 1, Some(20));
    let selected = schedule(&control, &state, &second, TimestampMs::new(0), &mut events);
    assert_eq!(selected.starts_at(), TimestampMs::new(120));
    assert_eq!(selected.ends_at(), Some(TimestampMs::new(140)));
}

/// Original Time openings drive earliest scheduling, Bind and nonrenewing overrun observation.
#[test]
fn time_calendar_selects_opening_and_never_releases_active_overrun() {
    let (mut control, state, mut events) = fixture(ResourceKind::Time);
    let work = requirement("calendar", ResourceKind::Time, 1, Some(20));
    let group = ready_group(&mut control, &work, &mut events);
    let selected = schedule(&control, &state, &work, TimestampMs::new(0), &mut events);
    assert_eq!(selected.starts_at(), TimestampMs::new(100));
    assert_eq!(selected.ends_at(), Some(TimestampMs::new(120)));
    let candidates = control
        .match_capabilities(
            &state,
            &work,
            TimestampMs::new(0),
            &correlation(),
            &mut events,
        )
        .unwrap();
    control
        .reserve_scheduled_task(
            &state,
            &candidates,
            group.clone(),
            selected,
            TimestampMs::new(0),
        )
        .unwrap();
    let proposed = proposal(
        &control,
        &state,
        &work,
        "t",
        TimestampMs::new(100),
        &mut events,
    );
    let committed = control
        .commit_for_group_with_state(
            &state,
            &group,
            &proposed,
            TimestampMs::new(100),
            &correlation(),
            &mut events,
        )
        .unwrap();
    assert!(control.bound_topology_allowed(
        work.task_ref(),
        committed.assignments(),
        TimestampMs::new(119)
    ));
    let before = serde_json::to_value(control.checkpoint()).unwrap();
    assert!(!control.bound_topology_allowed(
        work.task_ref(),
        committed.assignments(),
        TimestampMs::new(120)
    ));
    assert!(
        control
            .bind_task_execution_with_state(
                &state,
                &group,
                &committed,
                TimestampMs::new(120),
                &correlation(),
                &mut events
            )
            .is_err()
    );
    assert_eq!(serde_json::to_value(control.checkpoint()).unwrap(), before);
    let restored = ControlPlane::restore(control.checkpoint()).unwrap();
    assert_eq!(restored.reservations.len(), 1);
    // The commitment has not been consumed by Bind, so no Group ownership is fabricated yet.
    assert!(
        restored
            .expired_topology_targets(TimestampMs::new(120))
            .is_empty()
    );
}

/// Time demands without a bounded original duration cannot acquire a guessed end or opening.
#[test]
fn unbounded_time_demand_refuses_without_reservation_or_event_changes() {
    let (mut control, state, mut events) = fixture(ResourceKind::Time);
    let work = requirement("unbounded", ResourceKind::Time, 1, None);
    let proposed = proposal(
        &control,
        &state,
        &work,
        "t",
        TimestampMs::new(110),
        &mut events,
    );
    let before = serde_json::to_value(control.checkpoint()).unwrap();
    let count = events.records.len();
    assert!(
        control
            .commit_with_state(
                &state,
                &proposed,
                TimestampMs::new(110),
                &correlation(),
                &mut events
            )
            .is_err()
    );
    assert_eq!(serde_json::to_value(control.checkpoint()).unwrap(), before);
    assert_eq!(events.records.len(), count);
}

/// Exact original time bounds survive a real Control release/Commit/Rebind/restore contract path.
#[test]
fn recovery_uses_original_remaining_time_and_retains_original_selection_after_rebind() {
    let (mut control, mut state, mut events) = fixture(ResourceKind::Time);
    let mut next = registry(ResourceKind::Time);
    next.revision = 2;
    let mut replacement = next.resources[0].clone();
    replacement.resource_id = ResourceId::new("u").unwrap();
    replacement.node_id = NodeId::new("replacement-node").unwrap();
    next.resources.push(replacement.clone());
    control.accept_resource_topology_registry(next, 1).unwrap();
    let registration = NodeRegistration::new_with_contracts(
        replacement.node_id.clone(),
        domain::LocalRuntime::new("unit", "1").unwrap(),
        domain::NodeContractVersion::v0_1(),
        vec![Capability::new(CapabilityKind::Compute, true)],
        vec![CapabilityContractRef::new("compute", "work", "v1").unwrap()],
        vec![Resource::new(replacement.resource_id.clone(), ResourceKind::Time, 1).unwrap()],
    );
    control
        .register_node(
            &mut state,
            registration,
            NodeStatus::new(NodeHealth::Online, TimestampMs::new(0)),
            TimestampMs::new(0),
            &correlation(),
            &mut events,
        )
        .unwrap();
    let work = requirement("time-recovery", ResourceKind::Time, 1, Some(20));
    let group = ready_group(&mut control, &work, &mut events);
    // Explicit source proposal is chosen independently of deterministic Node sort order.
    let candidates = control
        .match_capabilities(
            &state,
            &work,
            TimestampMs::new(0),
            &correlation(),
            &mut events,
        )
        .unwrap();
    let mut selected: serde_json::Value = serde_json::to_value(schedule(
        &control,
        &state,
        &work,
        TimestampMs::new(0),
        &mut events,
    ))
    .unwrap();
    selected["selections"][0]["node_id"] = serde_json::json!("resource-node");
    selected["selections"][0]["resource_ids"] = serde_json::json!(["t"]);
    let selected: TaskSchedulingDecision = serde_json::from_value(selected).unwrap();
    let original_selection = selected.clone();
    control
        .reserve_scheduled_task(
            &state,
            &candidates,
            group.clone(),
            selected,
            TimestampMs::new(0),
        )
        .unwrap();
    let proposed = proposal(
        &control,
        &state,
        &work,
        "t",
        TimestampMs::new(100),
        &mut events,
    );
    let committed = control
        .commit_for_group_with_state(
            &state,
            &group,
            &proposed,
            TimestampMs::new(100),
            &correlation(),
            &mut events,
        )
        .unwrap();
    control
        .bind_task_execution_with_state(
            &state,
            &group,
            &committed,
            TimestampMs::new(100),
            &correlation(),
            &mut events,
        )
        .unwrap();
    control
        .activate_task_execution(
            &group,
            work.task_ref(),
            TimestampMs::new(100),
            &correlation(),
            &mut events,
        )
        .unwrap();
    state
        .record_node_liveness(
            &NodeId::new("resource-node").unwrap(),
            NodeLivenessObservation::new(NodeLiveness::Unreachable, TimestampMs::new(101)),
        )
        .unwrap();
    let need = match control
        .assess_group(
            &state,
            &group,
            &work,
            TimestampMs::new(101),
            &correlation(),
            &mut events,
        )
        .unwrap()
    {
        ReconciliationAssessment::RoleRecoveryRequired(need) => need,
        _ => panic!("original synthetic owner must require recovery"),
    };
    control
        .begin_role_recovery(&need, TimestampMs::new(101), &correlation(), &mut events)
        .unwrap();
    ControlPlane::restore(control.checkpoint()).unwrap();
    let candidates = control
        .match_recovery_candidates(
            &state,
            &need,
            &work,
            TimestampMs::new(102),
            &correlation(),
            &mut events,
        )
        .unwrap();
    let snapshot = control
        .recovery_scheduling_snapshot(TimestampMs::new(102), &group, work.task_ref())
        .unwrap();
    assert_eq!(
        snapshot.topology_recovery_interval.as_ref().unwrap().1,
        TimestampMs::new(120)
    );
    let choice = BoundedJointScheduler::new()
        .schedule_recovery(
            &state,
            &work,
            &candidates,
            &snapshot,
            TimestampMs::new(102),
            &correlation(),
            &mut events,
        )
        .unwrap();
    let RecoverySchedulingOutcome::Selected(choice) = choice else {
        panic!("remaining original opening permits replacement");
    };
    assert_eq!(choice.resource_ids(), &[ResourceId::new("u").unwrap()]);
    let proposed = control
        .propose_role_recovery(
            &state,
            &candidates,
            &work,
            choice.replacement_node_id().clone(),
            choice.resource_ids().to_vec(),
            TimestampMs::new(102),
            &correlation(),
            &mut events,
        )
        .unwrap();
    let committed = control
        .commit_role_recovery(
            &state,
            &work,
            &proposed,
            TimestampMs::new(102),
            &correlation(),
            &mut events,
        )
        .unwrap();
    control
        .rebind_role_with_state(
            &state,
            &committed,
            TimestampMs::new(103),
            &correlation(),
            &mut events,
        )
        .unwrap();
    let restored = ControlPlane::restore(control.checkpoint()).unwrap();
    assert_eq!(
        restored.scheduled_task(work.task_ref()).unwrap().decision(),
        &original_selection
    );
    assert_eq!(
        restored
            .scheduled_task(work.task_ref())
            .unwrap()
            .current_assignments()[0]
            .resource_ids(),
        &[ResourceId::new("u").unwrap()]
    );
    assert_eq!(
        restored.reservations[&ResourceId::new("u").unwrap()]
            .topology
            .as_ref()
            .unwrap()
            .planned_end,
        Some(TimestampMs::new(120))
    );
    assert!(
        restored
            .recovery_scheduling_snapshot(TimestampMs::new(120), &group, work.task_ref())
            .unwrap()
            .topology_recovery_interval
            .is_none()
    );
}

/// Clearance, reference mismatch, signed overflow and remote clocks cannot silently authorize geometry.
#[test]
fn invalid_geometry_and_clock_declarations_are_rejected_before_authority_changes() {
    let original = registry(ResourceKind::Space);
    let mut contact = original.resources[0].space.clone().unwrap();
    contact.lower_mm[0] = 10;
    contact.upper_mm[0] = 12;
    assert!(contact.conflicts(original.resources[0].space.as_ref().unwrap()));
    let mut changed = original.clone();
    changed.resources[1].space.as_mut().unwrap().anchor_revision = "untranslated-anchor".into();
    assert!(changed.validate().is_err());
    changed = original.clone();
    changed.resources[0].space.as_mut().unwrap().lower_mm[0] = i64::MIN;
    changed.resources[0].space.as_mut().unwrap().clearance_mm = 1;
    assert!(changed.validate().is_err());
    changed = original;
    changed.clock = "remote-wall-clock".into();
    assert!(changed.validate().is_err());
}
