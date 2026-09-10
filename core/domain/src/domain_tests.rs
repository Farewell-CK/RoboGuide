//! Domain facade and foundational value tests.

use super::*;
use std::collections::{BTreeMap, BTreeSet};

/// Rejects duration arithmetic overflow even when no completion deadline is declared.
#[test]
fn task_timing_rejects_duration_overflow_without_deadline() {
    assert!(matches!(
        TaskTiming::new(u64::MAX, None, None, Some(1)),
        Err(DomainError::InvalidDuration {
            kind: "task estimated duration overflow"
        })
    ));
}

/// Rejects ambiguous Task role declarations before Control persists role authority.
#[test]
fn task_requirement_rejects_duplicate_role_identity() {
    let mission_id =
        MissionId::new("mission-duplicate-role").expect("test mission identity must be valid");
    let task_id = TaskId::new("task-duplicate-role").expect("test task identity must be valid");
    let role_id = RoleId::new("mapper").expect("test role identity must be valid");
    let error = TaskRequirement::new(
        mission_id,
        task_id,
        vec![
            RoleRequirement::new(role_id.clone(), CapabilityKind::Observation, None),
            RoleRequirement::new(role_id, CapabilityKind::Compute, None),
        ],
    )
    .expect_err("duplicate role identities must be rejected");

    assert!(matches!(
        error,
        DomainError::InvalidMissionPlan { reason }
            if reason == "duplicate role id mapper"
    ));
}

/// Builds one valid task with no dependencies for graph invariant tests.
fn task(mission_id: &MissionId, task_id: &str, dependencies: Vec<TaskId>) -> PlannedTask {
    let task_id = TaskId::new(task_id).expect("test task id must be valid");
    let role = RoleRequirement::new(
        RoleId::new(format!("role-{task_id}")).expect("test role id must be valid"),
        CapabilityKind::Transport,
        Some(ResourceKind::Space),
    );
    let requirement = TaskRequirement::new(mission_id.clone(), task_id, vec![role])
        .expect("test requirement must be valid");
    let role_id = requirement.roles()[0].role_id().clone();
    let intent = ExecutionIntent::new(
        CapabilityContractRef::new("mobility", "move", "v1").expect("test operation must be valid"),
        BTreeMap::new(),
    )
    .expect("test intent must be valid");
    PlannedTask::new(
        "transport payload",
        requirement,
        BTreeMap::from([(role_id, intent)]),
        dependencies,
        TaskContinuity::new(
            CoordinationContextId::new("context-test").expect("test context id must be valid"),
            BTreeMap::new(),
            BTreeMap::new(),
        ),
    )
    .expect("test task must be valid")
}

/// Acyclic dependencies expose only tasks whose prerequisites have completed.
#[test]
fn task_graph_returns_ready_tasks() {
    let mission_id = MissionId::new("mission-ready").expect("test mission id must be valid");
    let first = task(&mission_id, "task-first", vec![]);
    let first_id = first.task_id().clone();
    let second = task(&mission_id, "task-second", vec![first_id.clone()]);
    let graph =
        TaskGraph::new(mission_id, vec![first, second]).expect("acyclic test graph must be valid");

    let initially_ready = graph.ready_tasks(&BTreeSet::new());
    assert_eq!(initially_ready.len(), 1);
    assert_eq!(initially_ready[0].task_id(), &first_id);

    let completed = BTreeSet::from([first_id]);
    let ready_after_first = graph.ready_tasks(&completed);
    assert_eq!(ready_after_first.len(), 1);
    assert_eq!(ready_after_first[0].task_id().as_str(), "task-second");
}

/// A cyclic Task Graph is rejected before Control can consume any requirement.
#[test]
fn task_graph_rejects_cycle() {
    let mission_id = MissionId::new("mission-cycle").expect("test mission id must be valid");
    let first = task(
        &mission_id,
        "task-first",
        vec![TaskId::new("task-second").expect("test dependency id must be valid")],
    );
    let second = task(
        &mission_id,
        "task-second",
        vec![TaskId::new("task-first").expect("test dependency id must be valid")],
    );

    assert!(matches!(
        TaskGraph::new(mission_id, vec![first, second]),
        Err(DomainError::InvalidMissionPlan { reason }) if reason.contains("cycle")
    ));
}

/// A plan cannot combine a goal and Task Graph from different missions.
#[test]
fn mission_plan_rejects_identity_mismatch() {
    let goal_mission = MissionId::new("mission-goal").expect("test goal mission id must be valid");
    let graph_mission =
        MissionId::new("mission-graph").expect("test graph mission id must be valid");
    let goal =
        MissionGoal::new(goal_mission, "deliver payload").expect("test mission goal must be valid");
    let graph = TaskGraph::new(
        graph_mission.clone(),
        vec![task(&graph_mission, "task-deliver", vec![])],
    )
    .expect("test task graph must be valid");

    assert!(matches!(
        MissionPlan::new(
            goal,
            graph,
            vec![
                CoordinationContext::new(
                    CoordinationContextId::new("context-test")
                        .expect("test context id must be valid"),
                    Vec::new(),
                )
                .expect("test context must be valid")
            ],
        ),
        Err(DomainError::InvalidMissionPlan { .. })
    ));
}

/// Node owner maps remain serializable when a checkpoint contains structured contract keys.
#[test]
fn node_registration_round_trips_owner_maps() {
    let node_id = NodeId::new("node-checkpoint").expect("node id must be valid");
    let local_system_id = LocalSystemId::new("mapping").expect("local system id is valid");
    let contract =
        CapabilityContractRef::new("spatial.map", "build", "v0").expect("contract must be valid");
    let resource_id = ResourceId::new("mapping-compute").expect("resource id is valid");
    let registration = NodeRegistration::new_with_local_systems(
        node_id,
        vec![LocalSystemDescriptor::new(
            local_system_id.clone(),
            LocalRuntime::new("robonix", "0.1").expect("runtime is valid"),
            BTreeMap::new(),
        )],
        NodeContractVersion::v0_2(),
        vec![Capability::new(CapabilityKind::Compute, true)],
        BTreeMap::from([(contract.clone(), local_system_id.clone())]),
        Vec::new(),
        vec![
            Resource::new(resource_id.clone(), ResourceKind::Compute, 1)
                .expect("resource is valid"),
        ],
        BTreeMap::from([(resource_id.clone(), local_system_id.clone())]),
    )
    .expect("registration is valid");

    let encoded = serde_json::to_string(&registration).expect("registration serializes");
    let decoded: NodeRegistration =
        serde_json::from_str(&encoded).expect("registration deserializes");
    assert_eq!(decoded, registration);
    assert!(encoded.contains("\"capability_owners\":[["));
    assert!(encoded.contains("\"capability_kinds\":[["));
    assert!(encoded.contains("\"capability_readiness\":[["));
    assert!(encoded.contains("\"resource_owners\":[["));

    let mut legacy: serde_json::Value =
        serde_json::from_str(&encoded).expect("registration JSON parses");
    legacy
        .as_object_mut()
        .expect("registration is an object")
        .remove("capability_readiness");
    legacy
        .as_object_mut()
        .expect("registration is an object")
        .remove("capability_kinds");
    let restored: NodeRegistration =
        serde_json::from_value(legacy).expect("legacy registration restores");
    assert!(restored.contract_is_available(&contract));
}

/// Legacy aggregate registration fails closed when several kinds prevent exact inference.
#[test]
fn legacy_registration_rejects_ambiguous_contract_kinds() {
    let node_id = NodeId::new("node-legacy-mixed").expect("node id must be valid");
    let local_system_id = LocalSystemId::new("mixed").expect("local system id is valid");
    let compute = CapabilityContractRef::new("spatial.map", "build", "v0")
        .expect("compute contract is valid");
    let observation = CapabilityContractRef::new("spatial.map", "observe", "v0")
        .expect("observation contract is valid");
    let registration = NodeRegistration::new_with_local_systems(
        node_id,
        vec![LocalSystemDescriptor::new(
            local_system_id.clone(),
            LocalRuntime::new("mixed-runtime", "0.1").expect("runtime is valid"),
            BTreeMap::new(),
        )],
        NodeContractVersion::v0_2(),
        vec![
            Capability::new(CapabilityKind::Compute, true),
            Capability::new(CapabilityKind::Observation, true),
        ],
        BTreeMap::from([
            (compute.clone(), local_system_id.clone()),
            (observation.clone(), local_system_id),
        ]),
        Vec::new(),
        Vec::new(),
        BTreeMap::new(),
    )
    .expect("legacy mixed registration remains structurally valid");

    assert!(!registration.contract_is_available_for_kind(&compute, CapabilityKind::Compute));
    assert!(!registration.contract_is_available_for_kind(&compute, CapabilityKind::Observation));
    assert!(
        !registration.contract_is_available_for_kind(&observation, CapabilityKind::Observation)
    );
    assert!(!registration.contract_is_available_for_kind(&observation, CapabilityKind::Compute));
}
