use super::*;
use domain::{LocalSystemId, MissionId, NodeId, StateSource, TaskId, TaskRef, TimestampMs};

/// Builds one exact-source verifier verdict for deterministic projection tests.
fn evidence(
    source: &str,
    source_time: u64,
    received_at: u64,
    satisfied: bool,
) -> TaskSatisfactionEvidence {
    TaskSatisfactionEvidence::new(
        TaskRef::new(
            MissionId::new("mission-evidence").expect("mission valid"),
            TaskId::new("task-evidence").expect("task valid"),
        ),
        CapabilityContractRef::new("observation", "verify", "v1").expect("contract valid"),
        "payload is at destination",
        StateSource::Node {
            node_id: NodeId::new(source).expect("node valid"),
            local_system_id: LocalSystemId::new("verifier").expect("system valid"),
        },
        TimestampMs::new(source_time),
        TimestampMs::new(received_at),
        satisfied,
    )
    .expect("evidence valid")
}

/// Independent verifier sources remain visible rather than collapsing into one truth value.
#[test]
fn satisfaction_sources_remain_independent() {
    let mut state = InMemoryTaskSatisfactionState::new();
    let first = evidence("camera-a", 100, 10, true);
    let second = evidence("camera-b", 1, 11, false);
    state
        .record_task_satisfaction_evidence(first.clone())
        .expect("first source records");
    state
        .record_task_satisfaction_evidence(second.clone())
        .expect("second source records");

    assert_eq!(
        state.task_satisfaction_evidence(first.task_ref()),
        vec![&first, &second]
    );
}

/// Receive time, not independent source time, orders one verifier channel.
#[test]
fn later_receive_time_accepts_backward_source_time() {
    let mut state = InMemoryTaskSatisfactionState::new();
    let current = evidence("camera-a", 1_000, 10, false);
    let incoming = evidence("camera-a", 900, 20, true);
    state
        .record_task_satisfaction_evidence(current)
        .expect("current evidence records");
    state
        .record_task_satisfaction_evidence(incoming.clone())
        .expect("later receive time replaces source-local rollback");

    assert_eq!(
        state.task_satisfaction_evidence(incoming.task_ref()),
        vec![&incoming]
    );
}

/// Older receive-time evidence is rejected without changing the latest verdict.
#[test]
fn older_receive_time_is_rejected() {
    let mut state = InMemoryTaskSatisfactionState::new();
    let current = evidence("camera-a", 10, 20, true);
    let stale = evidence("camera-a", 99, 10, false);
    state
        .record_task_satisfaction_evidence(current.clone())
        .expect("current evidence records");

    assert!(matches!(
        state.record_task_satisfaction_evidence(stale),
        Err(TaskSatisfactionStateError::StaleEvidence { .. })
    ));
    assert_eq!(
        state.task_satisfaction_evidence(current.task_ref()),
        vec![&current]
    );
}
