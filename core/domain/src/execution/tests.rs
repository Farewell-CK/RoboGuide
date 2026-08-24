//! Deterministic tests for canonical execution intent values.

use super::*;
use crate::{CorrelationId, ExecutionCommand, ExecutionGroupId, MissionId, NodeId, RoleId, TaskId};
use std::collections::BTreeMap;

/// Operation identity rejects every blank component independently.
#[test]
fn operation_ref_rejects_blank_components() {
    assert!(OperationRef::new("", "move", "v1").is_err());
    assert!(OperationRef::new("mobility", " ", "v1").is_err());
    assert!(OperationRef::new("mobility", "move", "").is_err());
}

/// Intent parameters retain scalar values in deterministic key order.
#[test]
fn execution_intent_orders_transport_neutral_parameters() {
    let intent = ExecutionIntent::new(
        OperationRef::new("navigation", "goto", "v1").expect("operation must be valid"),
        BTreeMap::from([
            ("target_y".to_string(), ExecutionValue::Float(2.0)),
            ("target_x".to_string(), ExecutionValue::Integer(1)),
            ("avoid_stairs".to_string(), ExecutionValue::Bool(true)),
            (
                "frame".to_string(),
                ExecutionValue::String("map".to_string()),
            ),
        ]),
    )
    .expect("intent must be valid");

    assert_eq!(intent.operation().to_string(), "navigation.goto@v1");
    assert_eq!(
        intent
            .parameters()
            .keys()
            .map(String::as_str)
            .collect::<Vec<_>>(),
        vec!["avoid_stairs", "frame", "target_x", "target_y"]
    );
}

/// Blank parameter keys fail before an adapter can serialize the intent.
#[test]
fn execution_intent_rejects_blank_parameter_key() {
    let result = ExecutionIntent::new(
        OperationRef::new("system", "ping", "v1").expect("operation must be valid"),
        BTreeMap::from([(" ".to_string(), ExecutionValue::Bool(true))]),
    );

    assert!(result.is_err());
}

/// Execution commands retain canonical intent alongside existing routing identity.
#[test]
fn execution_command_retains_intent() {
    let intent = ExecutionIntent::new(
        OperationRef::new("observation", "capture", "v1").expect("operation must be valid"),
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
        NodeId::new("camera-a").expect("node id must be valid"),
        intent.clone(),
        CorrelationId::new("trace-a").expect("correlation id must be valid"),
    );

    assert_eq!(command.intent(), &intent);
    assert_eq!(command.task_ref().to_string(), "mission-a/task-01");
}
