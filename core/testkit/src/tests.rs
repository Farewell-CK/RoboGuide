//! Deterministic FakeNode contract tests.

use super::*;
use domain::{
    Capability, CapabilityContractRef, CapabilityKind, CorrelationId, ExecutionGroupId,
    ExecutionIntent, ExecutionValue, LocalRuntime, MissionId, NodeId, NodeRegistration, RoleId,
    TaskId,
};
use ports::NodeGateway;
use std::collections::BTreeMap;

/// FakeNode receives the same canonical intent that Runtime would route to a real adapter.
#[test]
fn fake_node_retains_execution_intent() {
    let registration = NodeRegistration::new(
        NodeId::new("fake-node").expect("node id must be valid"),
        LocalRuntime::new("fake-eaios", "0.1.0").expect("runtime must be valid"),
        domain::NodeContractVersion::v0_1(),
        vec![Capability::new(CapabilityKind::Observation, true)],
        vec![],
    );
    let intent = ExecutionIntent::new(
        CapabilityContractRef::new("observation", "capture", "v1")
            .expect("operation must be valid"),
        BTreeMap::from([(
            "camera".to_string(),
            ExecutionValue::String("front".to_string()),
        )]),
    )
    .expect("intent must be valid");
    let command = ExecutionCommand::new(
        MissionId::new("mission-a").expect("mission id must be valid"),
        TaskId::new("task-01").expect("task id must be valid"),
        ExecutionGroupId::new("group-a").expect("group id must be valid"),
        RoleId::new("observer").expect("role id must be valid"),
        registration.node_id().clone(),
        intent.clone(),
        CorrelationId::new("trace-a").expect("correlation id must be valid"),
    );
    let mut node = FakeNode::new(registration);

    node.execute(&command)
        .expect("healthy fake node must execute the command");

    assert_eq!(node.executed_commands()[0].intent(), &intent);
}
