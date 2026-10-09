//! Progress observation cannot change physical execution, health or task outcomes.

use super::*;
use domain::{
    LocalSystemId, OperationRef, StateObjectClass, StateObjectRef, StateRecord, StateSemantic,
    StateSource, TimestampMs,
};

/// Builds a source-attributed sample whose source clock deliberately differs from receive time.
fn record(node: &str, sequence: u64, received: u64, epoch: &str) -> StateRecord {
    StateRecord::new_with_source_epoch(
        StateObjectRef::new(StateObjectClass::Node, "execution-progress", node).expect("object"),
        StateSemantic::Reported,
        StateSource::Node {
            node_id: NodeId::new(node).expect("node"),
            local_system_id: LocalSystemId::new("local").expect("owner"),
        },
        "progress",
        EXECUTION_PROGRESS_SCHEMA,
        serde_json::json!({}),
        Some(TimestampMs::new(999_999_999)),
        TimestampMs::new(received),
        100,
        None,
        Some(epoch.to_string()),
        sequence,
    )
    .expect("record")
}

/// Starts one exact operation without creating any Control reservation or adapter call.
fn runtime() -> RuntimeExecutionManager {
    let mut runtime = RuntimeExecutionManager::new();
    runtime
        .record_dispatched("attempt-1".into(), command(), Vec::new())
        .expect("dispatch");
    runtime
        .observe_execution(
            "attempt-1",
            command().node_id().clone(),
            1,
            ExecutionStatus::Running,
            "",
        )
        .expect("running");
    runtime
}

/// Builds a generic local counter, without robot position or simulator-specific thresholds.
fn sample(activity: OperationActivity, units: Option<u64>) -> OperationProgressSample {
    OperationProgressSample {
        execution_id: "attempt-1".into(),
        operation: OperationRef::new("mobility", "move", "v1").expect("operation"),
        stage_epoch: 0,
        completed_units: units,
        activity,
    }
}

/// Wait is intentional, stalled work needs a policy, and real advance resets only its baseline.
#[test]
fn working_waiting_stale_and_unmeasured_progress_are_distinct() {
    let mut runtime = runtime();
    assert!(runtime.observe_progress(
        &record("node-a", 1, 10, "s1"),
        sample(OperationActivity::Working, Some(0))
    ));
    assert!(runtime.observe_progress(
        &record("node-a", 2, 30, "s1"),
        sample(OperationActivity::Working, Some(0))
    ));
    assert_eq!(
        runtime.progress_disposition("attempt-1", TimestampMs::new(40), Some(20)),
        ProgressDisposition::Stalled
    );
    assert_eq!(
        runtime.progress_disposition("attempt-1", TimestampMs::new(40), None),
        ProgressDisposition::Working
    );
    assert!(runtime.observe_progress(
        &record("node-a", 3, 41, "s1"),
        sample(OperationActivity::Working, Some(1))
    ));
    assert_eq!(
        runtime.progress_disposition("attempt-1", TimestampMs::new(42), Some(20)),
        ProgressDisposition::Working
    );
    assert!(runtime.observe_progress(
        &record("node-a", 4, 43, "s1"),
        sample(OperationActivity::Waiting, Some(1))
    ));
    assert_eq!(
        runtime.progress_disposition("attempt-1", TimestampMs::new(100), Some(20)),
        ProgressDisposition::Waiting
    );
    assert_eq!(
        runtime.progress_disposition("attempt-1", TimestampMs::new(143), Some(20)),
        ProgressDisposition::Unknown
    );
    assert!(runtime.observe_progress(
        &record("node-a", 5, 150, "s1"),
        sample(OperationActivity::Working, None)
    ));
    assert_eq!(
        runtime.progress_disposition("attempt-1", TimestampMs::new(190), Some(20)),
        ProgressDisposition::Working
    );
    assert_eq!(
        runtime.execution_status("attempt-1"),
        Some(ExecutionStatus::Running)
    );
    assert!(runtime.pending_cancellations().is_empty());
}

/// Wrong owners, stale sequences, regressing counters and superseded attempts cannot renew evidence.
#[test]
fn progress_order_identity_and_attempt_fences_hold() {
    let mut runtime = runtime();
    let original = sample(OperationActivity::Working, Some(3));
    assert!(runtime.observe_progress(&record("node-a", 2, 10, "s1"), original.clone()));
    assert!(!runtime.observe_progress(&record("node-a", 1, 90, "s1"), original.clone()));
    assert!(!runtime.observe_progress(&record("node-b", 3, 90, "s1"), original.clone()));
    assert!(!runtime.observe_progress(
        &record("node-a", 3, 90, "s1"),
        sample(OperationActivity::Working, Some(2))
    ));
    let mut wrong_operation = original.clone();
    wrong_operation.operation = OperationRef::new("compute", "infer", "v1").expect("operation");
    assert!(!runtime.observe_progress(&record("node-a", 3, 90, "s1"), wrong_operation));
    assert_eq!(
        runtime.progress_disposition("attempt-1", TimestampMs::new(110), Some(20)),
        ProgressDisposition::Unknown
    );
    runtime
        .prepare_dispatch("attempt-2".into(), command(), Vec::new())
        .expect("replacement");
    assert!(!runtime.observe_progress(&record("node-a", 4, 120, "s1"), original));
    assert_eq!(
        runtime.progress_disposition("attempt-1", TimestampMs::new(120), Some(20)),
        ProgressDisposition::Unknown
    );
}

/// Restart keeps receive times and requires fresh confirmation rather than renewing stale progress.
#[test]
fn progress_restore_and_registration_change_do_not_renew_evidence() {
    let mut runtime = runtime();
    assert!(runtime.observe_progress(
        &record("node-a", 1, 10, "s1"),
        sample(OperationActivity::Blocked, Some(0))
    ));
    let encoded = serde_json::to_string(&runtime.checkpoint()).expect("encodes");
    let mut restored =
        RuntimeExecutionManager::restore(serde_json::from_str(&encoded).expect("decodes"))
            .expect("restores");
    assert_eq!(
        restored.progress_disposition("attempt-1", TimestampMs::new(20), Some(10)),
        ProgressDisposition::Unknown
    );
    restored
        .observe_execution(
            "attempt-1",
            command().node_id().clone(),
            2,
            ExecutionStatus::Running,
            "",
        )
        .expect("fresh running");
    assert_eq!(
        restored.progress_disposition("attempt-1", TimestampMs::new(111), Some(10)),
        ProgressDisposition::Unknown
    );
    assert!(restored.observe_progress(
        &record("node-a", 1, 120, "s2"),
        sample(OperationActivity::Working, Some(0))
    ));
    assert_eq!(
        restored.progress_disposition("attempt-1", TimestampMs::new(125), Some(10)),
        ProgressDisposition::Working
    );
    restored.fence_progress_for_node(command().node_id());
    assert_eq!(
        restored.progress_disposition("attempt-1", TimestampMs::new(125), Some(10)),
        ProgressDisposition::Unknown
    );
}

/// A shared batch is retained once by State; Runtime never duplicates its arbitrary payload.
#[test]
fn progress_checkpoint_retains_bounded_attribution_instead_of_raw_batch() {
    let mut runtime = runtime();
    let oversized_raw = StateRecord::new_with_source_epoch(
        record("node-a", 1, 10, "s1").key().object().clone(),
        StateSemantic::Reported,
        record("node-a", 1, 10, "s1").key().source().clone(),
        "progress",
        EXECUTION_PROGRESS_SCHEMA,
        serde_json::json!({"raw_batch": "x".repeat(60_000)}),
        None,
        TimestampMs::new(10),
        100,
        None,
        Some("s1".into()),
        1,
    )
    .expect("State record");
    assert!(runtime.observe_progress(&oversized_raw, sample(OperationActivity::Working, Some(1))));
    let evidence = serde_json::to_value(runtime.progress_evidence("attempt-1").expect("sample"))
        .expect("evidence");
    assert_eq!(evidence["record"]["sequence"], 1);
    assert_eq!(evidence["record"]["received_at"], 10);
    assert!(
        !serde_json::to_string(&runtime.checkpoint())
            .expect("checkpoint")
            .contains("raw_batch")
    );
}
