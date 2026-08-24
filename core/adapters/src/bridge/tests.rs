//! Architecture-level tests for heterogeneous Local EAIOS operation mapping.

use super::*;
use domain::{
    ExecutionGroupId, ExecutionIntent, MissionId, NodeId, OperationRef, RoleId, TaskId, TaskRef,
};
use std::collections::BTreeMap;

/// Builds immutable local execution context for backend translation tests.
fn context(node_id: &str) -> LocalExecutionContext {
    LocalExecutionContext::new(
        TaskRef::new(
            MissionId::new("mission-a").expect("mission id must be valid"),
            TaskId::new("task-01").expect("task id must be valid"),
        ),
        ExecutionGroupId::new("group-a").expect("group id must be valid"),
        RoleId::new("transport").expect("role id must be valid"),
        NodeId::new(node_id).expect("node id must be valid"),
    )
}

/// The same canonical intent maps to distinct local representations without Core changes.
#[test]
fn canonical_intent_maps_to_heterogeneous_local_operations() {
    let operation =
        OperationRef::new("mobility", "move", "v1").expect("canonical operation must be valid");
    let intent = ExecutionIntent::new(operation.clone(), BTreeMap::new())
        .expect("canonical intent must be valid");
    let backend_a = ConfiguredCommandBackend::new(BTreeMap::from([(
        operation.clone(),
        vec!["vendor_a_walk".to_string(), "--safe".to_string()],
    )]))
    .expect("backend A configuration must be valid");
    let backend_b = ConfiguredCommandBackend::new(BTreeMap::from([(
        operation,
        vec!["vendor_b_motion".to_string()],
    )]))
    .expect("backend B configuration must be valid");

    let invocation_a = backend_a
        .translate(&context("node-a"), &intent)
        .expect("backend A must support the canonical operation");
    let invocation_b = backend_b
        .translate(&context("node-b"), &intent)
        .expect("backend B must support the canonical operation");

    assert_eq!(invocation_a.argv()[0], "vendor_a_walk");
    assert_eq!(invocation_b.argv()[0], "vendor_b_motion");
    assert_ne!(invocation_a, invocation_b);
}

/// Invalid local configuration is rejected before any network intent can use it.
#[test]
fn configured_backend_rejects_blank_argv() {
    let operation =
        OperationRef::new("system", "ping", "v1").expect("canonical operation must be valid");
    let result =
        ConfiguredCommandBackend::new(BTreeMap::from([(operation, vec![" ".to_string()])]));

    assert!(matches!(
        result,
        Err(BackendError::InvalidConfiguredCommand(_))
    ));
}
