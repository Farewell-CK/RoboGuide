//! Synthetic original-owner allocation contracts; no deployment Nodes or device facts are created.

use super::*;

/// Builds one explicit software test pool with arbitrary fixture identities and typed units.
fn pool_node(capacity: u32) -> NodeRegistration {
    let contract = CapabilityContractRef::new("compute", "work", "v1").unwrap();
    NodeRegistration::new_with_contracts(
        NodeId::new("quantity-unit-node").unwrap(),
        domain::LocalRuntime::new("unit", "1").unwrap(),
        domain::NodeContractVersion::v0_13(),
        vec![Capability::new(CapabilityKind::Compute, true)],
        vec![contract.clone()],
        vec![
            Resource::new_divisible(ResourceId::new("quantity-unit-pool").unwrap(), capacity)
                .unwrap(),
        ],
    )
    .with_operation_support(vec![domain::OperationSupport::new(
        domain::OperationRef::new("compute", "work", "v1").unwrap(),
        domain::LocalSystemId::new("default").unwrap(),
    )])
    .unwrap()
}

/// Freezes a complete original semantic plan, including explicit Context capacity when requested.
fn quantity_plan(
    scope: ResourceBindingScope,
    envelope: Option<u32>,
    demands: &[u32],
) -> MissionPlan {
    let mission = domain::MissionId::new("quantity-unit-mission").unwrap();
    let context = domain::CoordinationContextId::new("quantity-context").unwrap();
    let context_role = domain::ContextRoleId::new("quantity-context-role").unwrap();
    let actor = domain::ActorId::new("quantity-actor").unwrap();
    let role = RoleId::new("worker").unwrap();
    let contract = CapabilityContractRef::new("compute", "work", "v1").unwrap();
    let tasks = demands
        .iter()
        .enumerate()
        .map(|(index, units)| {
            let requirement = TaskRequirement::new(
                mission.clone(),
                TaskId::new(format!("task-{index}")).unwrap(),
                vec![
                    RoleRequirement::new_normalized(
                        role.clone(),
                        Some(actor.clone()),
                        vec![domain::CapabilityRequirement::exact(contract.clone())],
                        vec![ResourceRequirement::new_divisible(*units).unwrap()],
                    )
                    .unwrap(),
                ],
            )
            .unwrap();
            PlannedTask::new(
                "original quantity work",
                requirement,
                BTreeMap::from([(
                    role.clone(),
                    ExecutionIntent::new_semantic(
                        domain::OperationRef::new("compute", "work", "v1").unwrap(),
                        "original quantity work",
                        BTreeMap::new(),
                    )
                    .unwrap(),
                )]),
                Vec::new(),
                domain::TaskContinuity::new(
                    context.clone(),
                    BTreeMap::from([(role.clone(), context_role.clone())]),
                    BTreeMap::from([(role.clone(), scope)]),
                ),
            )
            .unwrap()
        })
        .collect();
    let mut declaration = domain::ContextRole::new(context_role, actor);
    if let Some(units) = envelope {
        declaration = declaration
            .with_resource_envelope(vec![ResourceRequirement::new_divisible(units).unwrap()])
            .unwrap();
    }
    MissionPlan::new(
        MissionGoal::new(mission.clone(), "original quantity work").unwrap(),
        TaskGraph::new(mission, tasks).unwrap(),
        vec![domain::CoordinationContext::new(context, vec![declaration]).unwrap()],
    )
    .unwrap()
    .with_v0_10_contract()
}

/// Initializes only virtual State and a normal Mission Group, retaining complete original Role declarations.
fn quantity_fixture(
    plan: &MissionPlan,
    quota: u64,
) -> (
    ControlPlane,
    InMemorySharedNodeState,
    RecordingEvents,
    ExecutionGroupId,
) {
    let mut control = ControlPlane::new();
    let mut state = InMemorySharedNodeState::new();
    let mut events = RecordingEvents::default();
    control
        .register_mission_tenant(plan.goal().mission_id().clone(), "quantity-tenant")
        .unwrap();
    install_quota_limit(
        &mut control,
        "quantity-tenant",
        ResourceKind::Compute,
        quota,
    );
    control
        .register_node(
            &mut state,
            pool_node(4),
            NodeStatus::new(NodeHealth::Online, TimestampMs::new(0)),
            TimestampMs::new(0),
            &correlation(),
            &mut events,
        )
        .unwrap();
    let group = ExecutionGroupId::new("quantity-unit-group").unwrap();
    control
        .create_mission_group(
            group.clone(),
            plan,
            TimestampMs::new(0),
            &correlation(),
            &mut events,
        )
        .unwrap();
    for task in plan.task_graph().tasks() {
        control
            .ready_task_execution(
                &group,
                task.requirement().task_ref(),
                TimestampMs::new(0),
                &correlation(),
                &mut events,
            )
            .unwrap();
    }
    (control, state, events, group)
}

/// Forms a normal candidate/proposal and uses the complete current-State Group Commit path.
fn quantity_commit(
    control: &mut ControlPlane,
    state: &InMemorySharedNodeState,
    events: &mut RecordingEvents,
    plan: &MissionPlan,
    group: &ExecutionGroupId,
    index: usize,
) -> Result<CommittedPlan, ControlError> {
    let requirement = plan.task_graph().tasks()[index].requirement();
    let candidates = control.match_capabilities_for_mission(
        state,
        plan,
        requirement,
        TimestampMs::new(0),
        &correlation(),
        events,
    )?;
    let proposal = control.propose(
        state,
        requirement,
        &candidates,
        vec![RoleAssignment::new(
            RoleId::new("worker").unwrap(),
            NodeId::new("quantity-unit-node").unwrap(),
            vec![ResourceId::new("quantity-unit-pool").unwrap()],
        )],
        TimestampMs::new(0),
        &correlation(),
        events,
    )?;
    control.commit_for_group_with_state(
        state,
        group,
        &proposal,
        TimestampMs::new(0),
        &correlation(),
        events,
    )
}

/// Binds an exact quantity commitment through normal current-State authority.
fn quantity_bind(
    control: &mut ControlPlane,
    state: &InMemorySharedNodeState,
    events: &mut RecordingEvents,
    plan: &MissionPlan,
    group: &ExecutionGroupId,
    index: usize,
    committed: &CommittedPlan,
) {
    assert_eq!(
        committed.task_ref(),
        plan.task_graph().tasks()[index].requirement().task_ref()
    );
    control
        .bind_task_execution_with_state(
            state,
            group,
            committed,
            TimestampMs::new(0),
            &correlation(),
            events,
        )
        .unwrap();
}

/// Uses existing logical terminal/release APIs; this unit test establishes no physical stopping.
fn quantity_finish(
    control: &mut ControlPlane,
    events: &mut RecordingEvents,
    plan: &MissionPlan,
    group: &ExecutionGroupId,
    index: usize,
    task_scoped: bool,
) {
    let task = plan.task_graph().tasks()[index].requirement().task_ref();
    control
        .activate_task_execution(group, task, TimestampMs::new(0), &correlation(), events)
        .unwrap();
    control
        .record_task_execution_completed(group, task, TimestampMs::new(0), &correlation(), events)
        .unwrap();
    control
        .satisfy_task_execution(
            group,
            task,
            domain::TaskSatisfactionBasis::ExecutionReport,
            TimestampMs::new(0),
            &correlation(),
            events,
        )
        .unwrap();
    let resources = if task_scoped {
        vec![ResourceId::new("quantity-unit-pool").unwrap()]
    } else {
        Vec::new()
    };
    control
        .release_task_bindings(
            group,
            task,
            &resources,
            TimestampMs::new(0),
            &correlation(),
            events,
        )
        .unwrap();
}

/// Shared Commit refuses excess demand without mutating ledger/quota/events; exact release retains peers.
#[test]
fn quantitative_task_commit_release_and_restart_keep_exact_owners() {
    let plan = quantity_plan(ResourceBindingScope::Task, None, &[2, 2, 1]);
    let (mut control, state, mut events, group) = quantity_fixture(&plan, 4);
    for index in 0..2 {
        let committed =
            quantity_commit(&mut control, &state, &mut events, &plan, &group, index).unwrap();
        quantity_bind(
            &mut control,
            &state,
            &mut events,
            &plan,
            &group,
            index,
            &committed,
        );
    }
    let before = serde_json::to_value(control.checkpoint()).unwrap();
    let event_count = events.records.len();
    assert!(quantity_commit(&mut control, &state, &mut events, &plan, &group, 2).is_err());
    assert_eq!(before, serde_json::to_value(control.checkpoint()).unwrap());
    // Match/proposal evidence may be appended; a rejected Commit itself appends no authority evidence.
    assert!(
        events.records[event_count..]
            .iter()
            .all(|(_, event)| !matches!(event, EventPayload::PlanCommitted { .. }))
    );
    assert_eq!(
        control.quota_usage("quantity-tenant", ResourceKind::Compute),
        (4, Some(0))
    );
    let mut restored = ControlPlane::restore(control.checkpoint()).unwrap();
    assert_eq!(
        restored
            .allocation_snapshot(TimestampMs::new(0))
            .unwrap()
            .allocations()
            .len(),
        2
    );
    quantity_finish(&mut restored, &mut events, &plan, &group, 0, true);
    assert_eq!(
        restored.quota_usage("quantity-tenant", ResourceKind::Compute),
        (2, Some(2))
    );
    assert_eq!(
        restored
            .resource_allocation_grants(
                &group,
                plan.task_graph().tasks()[1].requirement().task_ref(),
                &RoleId::new("worker").unwrap()
            )
            .unwrap()[0]
            .units,
        2
    );
    ControlPlane::restore(restored.checkpoint()).unwrap();
}

/// Context borrowing never resizes or double-charges its explicit parent and rejects concurrent excess.
#[test]
fn quantitative_context_envelope_survives_borrower_release_and_restart() {
    let plan = quantity_plan(ResourceBindingScope::Context, Some(3), &[2, 1, 2]);
    let (mut control, mut state, mut events, group) = quantity_fixture(&plan, 4);
    for index in 0..2 {
        let committed =
            quantity_commit(&mut control, &state, &mut events, &plan, &group, index).unwrap();
        quantity_bind(
            &mut control,
            &state,
            &mut events,
            &plan,
            &group,
            index,
            &committed,
        );
    }
    assert_eq!(
        control.quota_usage("quantity-tenant", ResourceKind::Compute),
        (3, Some(1))
    );
    let before = serde_json::to_value(control.checkpoint()).unwrap();
    assert!(quantity_commit(&mut control, &state, &mut events, &plan, &group, 2).is_err());
    assert_eq!(before, serde_json::to_value(control.checkpoint()).unwrap());
    quantity_finish(&mut control, &mut events, &plan, &group, 0, false);
    let mut control = ControlPlane::restore(control.checkpoint()).unwrap();
    assert!(quantity_commit(&mut control, &state, &mut events, &plan, &group, 2).is_err());
    control
        .register_node(
            &mut state,
            pool_node(4),
            NodeStatus::new(NodeHealth::Online, TimestampMs::new(0)),
            TimestampMs::new(0),
            &correlation(),
            &mut events,
        )
        .unwrap();

    let committed = quantity_commit(&mut control, &state, &mut events, &plan, &group, 2).unwrap();
    quantity_bind(
        &mut control,
        &state,
        &mut events,
        &plan,
        &group,
        2,
        &committed,
    );
    let view = control.allocation_snapshot(TimestampMs::new(0)).unwrap();
    assert_eq!(view.allocations().len(), 1);
    assert_eq!(
        view.allocations()[0]
            .quantitative_grant()
            .unwrap()
            .allocation_units,
        3
    );
    assert_eq!(
        view.allocations()[0]
            .borrowers()
            .iter()
            .map(|value| value.units)
            .sum::<u32>(),
        3
    );
    ControlPlane::restore(control.checkpoint()).unwrap();
}

/// Historical raw capacity and quota records cannot be reattributed through a forged parent or borrower.
#[test]
fn quantitative_checkpoint_rejects_parent_tenant_and_borrower_tampering() {
    let plan = quantity_plan(ResourceBindingScope::Task, None, &[2]);
    let (mut control, state, mut events, group) = quantity_fixture(&plan, 4);
    let committed = quantity_commit(&mut control, &state, &mut events, &plan, &group, 0).unwrap();
    quantity_bind(
        &mut control,
        &state,
        &mut events,
        &plan,
        &group,
        0,
        &committed,
    );
    for (field, value) in [
        ("tenant_id", serde_json::json!("foreign")),
        ("allocation_units", serde_json::json!(3)),
        ("generation", serde_json::json!(999)),
    ] {
        let mut checkpoint = serde_json::to_value(control.checkpoint()).unwrap();
        let records = checkpoint["capacity_ledger"]["allocations"]
            .as_object_mut()
            .unwrap();
        records.values_mut().next().unwrap()["original"][field] = value;
        assert!(ControlPlane::restore(serde_json::from_value(checkpoint).unwrap()).is_err());
    }
    let mut checkpoint = serde_json::to_value(control.checkpoint()).unwrap();
    checkpoint["capacity_ledger"]["allocations"]
        .as_object_mut()
        .unwrap()
        .values_mut()
        .next()
        .unwrap()["borrowers"][0]["units"] = serde_json::json!(1);
    assert!(ControlPlane::restore(serde_json::from_value(checkpoint).unwrap()).is_err());
}

/// Two positive owners of one pool remain separately observable; ambiguous singular lookup grants nothing.
#[test]
fn quantitative_allocation_projection_retains_multiple_original_claims() {
    let plan = quantity_plan(ResourceBindingScope::Task, None, &[1, 1]);
    let (mut control, state, mut events, group) = quantity_fixture(&plan, 4);
    for index in 0..2 {
        let committed =
            quantity_commit(&mut control, &state, &mut events, &plan, &group, index).unwrap();
        quantity_bind(
            &mut control,
            &state,
            &mut events,
            &plan,
            &group,
            index,
            &committed,
        );
    }
    let mut projection = InMemoryAllocationState::new();
    projection
        .replace_allocation_view(control.allocation_snapshot(TimestampMs::new(0)).unwrap())
        .unwrap();
    assert_eq!(projection.allocations().len(), 2);
    assert!(
        projection
            .allocation(&ResourceId::new("quantity-unit-pool").unwrap())
            .is_none()
    );
}

/// Tenant quota denial remains atomic when the physical logical pool still has enough units.
#[test]
fn quantitative_quota_rejects_before_mutating_an_available_pool() {
    let plan = quantity_plan(ResourceBindingScope::Task, None, &[2, 2]);
    let (mut control, state, mut events, group) = quantity_fixture(&plan, 3);
    let first = quantity_commit(&mut control, &state, &mut events, &plan, &group, 0).unwrap();
    quantity_bind(&mut control, &state, &mut events, &plan, &group, 0, &first);
    let before = serde_json::to_value(control.checkpoint()).unwrap();
    assert!(quantity_commit(&mut control, &state, &mut events, &plan, &group, 1).is_err());
    assert_eq!(before, serde_json::to_value(control.checkpoint()).unwrap());
    assert_eq!(
        control.quota_usage("quantity-tenant", ResourceKind::Compute),
        (2, Some(1))
    );
}
