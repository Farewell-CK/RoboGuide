//! Shared Integration Runtime Bridge test fixtures.

use super::*;
use integration::grpc::v0_4::{
    Capability as WireCapability, LocalRuntime as WireRuntime, LocalSystemDescriptor,
};
use ports::{SharedNodeStateReader, StateRecordReader};
use testkit::InMemoryEventLog;

mod checkpoint_ingestion;
mod dispatch_recovery;
mod execution_facts;
mod relation_view;
mod state_view;

/// Builds one complete MissionPlan so integration tests use the production Group authority.
fn single_task_plan(
    requirement: domain::TaskRequirement,
    intent: domain::ExecutionIntent,
) -> domain::MissionPlan {
    let mission_id = requirement.mission_id().clone();
    let intents = requirement
        .roles()
        .iter()
        .map(|role| (role.role_id().clone(), intent.clone()))
        .collect();
    let context_id = domain::CoordinationContextId::new("integration-test-context")
        .expect("context identity is valid");
    let task = domain::PlannedTask::new(
        "exercise integration runtime",
        requirement,
        intents,
        Vec::new(),
        domain::TaskContinuity::new(context_id.clone(), BTreeMap::new(), BTreeMap::new()),
    )
    .expect("test Task is valid");
    domain::MissionPlan::new(
        domain::MissionGoal::new(mission_id.clone(), "exercise integration runtime")
            .expect("test Mission goal is valid"),
        domain::TaskGraph::new(mission_id, vec![task]).expect("test Task Graph is valid"),
        vec![
            domain::CoordinationContext::new(context_id, Vec::new())
                .expect("test Context is valid"),
        ],
    )
    .expect("test MissionPlan is valid")
}

/// Builds one same-Task two-Role plan with a Node-independent execution relation.
fn related_single_task_plan() -> domain::MissionPlan {
    let mission_id = domain::MissionId::new("mission-relation").expect("mission id is valid");
    let task_id = domain::TaskId::new("guidance").expect("task id is valid");
    let source_role = domain::RoleId::new("safety-observer").expect("role id is valid");
    let target_role = domain::RoleId::new("navigator").expect("role id is valid");
    let requirement = domain::TaskRequirement::new(
        mission_id.clone(),
        task_id.clone(),
        vec![
            domain::RoleRequirement::new(source_role.clone(), CapabilityKind::Observation, None),
            domain::RoleRequirement::new(target_role.clone(), CapabilityKind::Mobility, None),
        ],
    )
    .expect("requirement is valid");
    let intent = domain::ExecutionIntent::new(
        CapabilityContractRef::new("test", "execute", "v1").expect("contract is valid"),
        BTreeMap::new(),
    )
    .expect("intent is valid");
    let context_id =
        domain::CoordinationContextId::new("guidance-context").expect("context id is valid");
    let task = domain::PlannedTask::new(
        "exercise relation integration",
        requirement,
        BTreeMap::from([
            (source_role.clone(), intent.clone()),
            (target_role.clone(), intent),
        ]),
        Vec::new(),
        domain::TaskContinuity::new(context_id.clone(), BTreeMap::new(), BTreeMap::new()),
    )
    .expect("task is valid");
    let relation = domain::ExecutionRelationSpec::new(
        domain::ExecutionRelationId::new("safety-guards-navigation").expect("relation id is valid"),
        domain::PlannedExecutionRef::new(task_id.clone(), source_role),
        domain::PlannedExecutionRef::new(task_id, target_role),
        domain::ExecutionRelationKind::RequiresActive,
    )
    .expect("relation is valid");
    domain::MissionPlan::new(
        domain::MissionGoal::new(mission_id.clone(), "exercise relation integration")
            .expect("goal is valid"),
        domain::TaskGraph::new(mission_id, vec![task]).expect("graph is valid"),
        vec![
            domain::CoordinationContext::new_with_relations(context_id, Vec::new(), vec![relation])
                .expect("context is valid"),
        ],
    )
    .expect("relation plan is valid")
}
