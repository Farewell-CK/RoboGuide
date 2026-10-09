//! Progress follows registered State channels through the actual Node batch reducer.

use super::*;

/// An operation-owned export reaches Runtime; wrong LocalSystem and malformed batches do not.
#[test]
fn progress_state_batch_requires_registered_operation_owner() {
    use integration::grpc::v0_4 as wire;
    let mut bridge = IntegrationRuntimeBridge::new(
        ControlPlane::new(),
        InMemorySharedNodeState::new(),
        InMemoryEventLog::new(),
        GrpcNodeRouter::default(),
    );
    let correlation = CorrelationId::new("progress-test").expect("correlation");
    let registration = wire::NodeRegistration {
        node_id: "node-a".into(),
        node_contract_version: "roboguide.node.v0.6".into(),
        local_systems: vec![wire::LocalSystemDescriptor {
            id: "motion".into(),
            runtime: Some(wire::LocalRuntime {
                name: "local".into(),
                version: "1".into(),
            }),
            ..Default::default()
        }],
        operation_support: vec![wire::OperationSupport {
            operation: Some(wire::OperationRef {
                namespace: "mobility".into(),
                name: "move".into(),
                version: "v1".into(),
            }),
            local_system_id: "motion".into(),
        }],
        state_exports: vec![wire::StateExportDescriptor {
            export_id: "progress".into(),
            local_system_id: "motion".into(),
            object_class: wire::StateObjectClass::Node as i32,
            object_type: "execution-progress".into(),
            object_id: "node-a".into(),
            semantic: wire::StateSemantic::Reported as i32,
            payload_schema: runtime::EXECUTION_PROGRESS_SCHEMA.into(),
            valid_for_ms: 100,
        }],
        ..Default::default()
    };
    bridge
        .consume(
            GrpcNodeEvent::Registered {
                session_id: "s1".into(),
                lease_id: "l1".into(),
                registration,
            },
            TimestampMs::new(1),
            &correlation,
        )
        .expect("register");
    let command = ExecutionCommand::new(
        domain::MissionId::new("m").expect("mission"),
        domain::TaskId::new("t").expect("task"),
        domain::ExecutionGroupId::new("g").expect("group"),
        domain::RoleId::new("r").expect("role"),
        NodeId::new("node-a").expect("node"),
        domain::ExecutionIntent::new(
            CapabilityContractRef::new("mobility", "move", "v1").expect("contract"),
            BTreeMap::new(),
        )
        .expect("intent"),
        correlation.clone(),
    );
    bridge
        .runtime
        .record_dispatched("attempt-1".into(), command.clone(), Vec::new())
        .expect("dispatch");
    bridge
        .runtime
        .observe_execution(
            "attempt-1",
            command.node_id().clone(),
            1,
            ExecutionStatus::Running,
            "",
        )
        .expect("running");
    let batch = runtime::OperationProgressBatch {
        schema_version: runtime::EXECUTION_PROGRESS_SCHEMA.into(),
        executions: vec![runtime::OperationProgressSample {
            execution_id: "attempt-1".into(),
            operation: command.intent().operation().clone(),
            stage_epoch: 0,
            completed_units: Some(0),
            activity: runtime::OperationActivity::Waiting,
        }],
    };
    bridge
        .consume_state_observations(
            "node-a",
            "s1",
            2,
            vec![wire::StateObservation {
                export_id: "progress".into(),
                json_value: serde_json::to_vec(&batch).expect("serializes"),
                ..Default::default()
            }],
            TimestampMs::new(10),
            &correlation,
        )
        .expect("observe");
    assert_eq!(
        bridge.execution_progress("attempt-1", TimestampMs::new(20), Some(1)),
        runtime::ProgressDisposition::Waiting
    );
    let mut wrong = batch.clone();
    wrong.executions[0].operation =
        domain::OperationRef::new("compute", "infer", "v1").expect("operation");
    bridge
        .consume_state_observations(
            "node-a",
            "s1",
            3,
            vec![wire::StateObservation {
                export_id: "progress".into(),
                json_value: serde_json::to_vec(&wrong).expect("serializes"),
                ..Default::default()
            }],
            TimestampMs::new(80),
            &correlation,
        )
        .expect("raw evidence stays observable");
    assert_eq!(
        bridge.execution_progress("attempt-1", TimestampMs::new(110), Some(1)),
        runtime::ProgressDisposition::Unknown
    );
    assert_eq!(
        bridge.execution_status("attempt-1"),
        Some(RemoteExecutionStatus::Running)
    );
}
