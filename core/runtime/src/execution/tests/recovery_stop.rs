//! Stop proof, purpose and per-logical-Role budgets remain distinct from command receipts.

use super::*;
use domain::TimestampMs;

/// Starts a real Runtime attempt with an ordered Running fact and no adapter side effects.
fn running() -> RuntimeExecutionManager {
    let mut runtime = RuntimeExecutionManager::new();
    runtime
        .record_dispatched("old".into(), command(), Vec::new())
        .expect("dispatch");
    runtime
        .observe_execution(
            "old",
            command().node_id().clone(),
            1,
            ExecutionStatus::Running,
            "",
        )
        .expect("running");
    runtime
}

/// Authorizes one bounded repeat and preserves its original deadline.
fn authorize(runtime: &mut RuntimeExecutionManager) {
    runtime
        .request_recovery_stop("old", command().node_id(), TimestampMs::new(10), 100, 2)
        .expect("explicit authorization");
}

/// Reduces actual owner cancellation before capturing its Controller-local receive time.
fn stopped(runtime: &mut RuntimeExecutionManager, received: u64) {
    runtime
        .observe_execution(
            "old",
            command().node_id().clone(),
            2,
            ExecutionStatus::Cancelled,
            "stopped",
        )
        .expect("terminal owner fact");
    runtime.confirm_recovery_stop("old", TimestampMs::new(received));
}

/// Neither a durable receipt nor Unknown can authorize release; only timely actual stop can.
#[test]
fn recovery_receipt_and_unknown_do_not_prove_stop() {
    let mut runtime = running();
    authorize(&mut runtime);
    runtime
        .observe_cancellation_receipt("old", "cancel-old", command().node_id(), true, "")
        .expect("receipt");
    runtime.observe_node_unavailable(command().node_id(), "lost heartbeat");
    assert!(!runtime.recovery_stop_ready(&command(), TimestampMs::new(20)));
    assert_eq!(
        runtime.recovery_stop_disposition("old", TimestampMs::new(20)),
        RecoveryStopDisposition::AwaitingStop
    );
    assert_eq!(
        runtime.execution_status("old"),
        Some(ExecutionStatus::Unknown)
    );
    assert!(
        runtime
            .observe_execution(
                "old",
                NodeId::new("wrong").expect("node"),
                99,
                ExecutionStatus::Cancelled,
                ""
            )
            .is_err()
    );
    stopped(&mut runtime, 21);
    assert!(runtime.recovery_stop_ready(&command(), TimestampMs::new(22)));
    assert!(!runtime.recovery_stop_ready(&command(), TimestampMs::new(20)));
    assert_eq!(
        runtime
            .recovery_stop("old")
            .expect("intent")
            .confirmed_at_ms,
        Some(21)
    );
    runtime.confirm_recovery_stop("old", TimestampMs::new(30));
    assert_eq!(
        runtime
            .recovery_stop("old")
            .expect("intent")
            .confirmed_at_ms,
        Some(21)
    );
    assert_eq!(
        runtime.task_execution_result(
            command().group_id(),
            command().task_ref(),
            [command().role_id()]
        ),
        None
    );
}

/// Restore, repeated authorization and changed limits cannot renew a time/count budget.
#[test]
fn recovery_budgets_survive_restart_and_replacement() {
    let mut runtime = running();
    authorize(&mut runtime);
    stopped(&mut runtime, 20);
    runtime.mark_recovery_release(&command());
    let mut runtime = RuntimeExecutionManager::restore(runtime.checkpoint()).expect("restores");
    runtime
        .request_recovery_stop("old", command().node_id(), TimestampMs::new(70), 100, 2)
        .expect("idempotent original authorization");
    assert_eq!(
        runtime.recovery_stop("old").expect("intent").deadline_ms,
        110
    );
    assert!(
        runtime
            .request_recovery_stop("old", command().node_id(), TimestampMs::new(70), 200, 2)
            .is_err()
    );
    assert!(runtime.recovery_stop_ready(&command(), TimestampMs::new(109)));
    assert!(!runtime.recovery_stop_ready(&command(), TimestampMs::new(110)));
    assert_eq!(
        runtime.recovery_stop_disposition("old", TimestampMs::new(110)),
        RecoveryStopDisposition::BudgetExpired
    );
    runtime
        .prepare_dispatch(
            "new".into(),
            command_for("task-a", "carrier", "node-b"),
            Vec::new(),
        )
        .expect("replacement");
    assert!(!runtime.recovery_stop_ready(&command(), TimestampMs::new(80)));
    assert!(
        runtime
            .request_recovery_stop("old", command().node_id(), TimestampMs::new(80), 100, 2)
            .is_err()
    );
    let new = command_for("task-a", "carrier", "node-b");
    runtime
        .request_recovery_stop("new", new.node_id(), TimestampMs::new(80), 100, 2)
        .expect("second and final authorization");
    let mut runtime = RuntimeExecutionManager::restore(runtime.checkpoint()).expect("restores");
    runtime
        .prepare_dispatch("third".into(), command(), Vec::new())
        .expect("another exact slot");
    assert!(
        runtime
            .request_recovery_stop("third", command().node_id(), TimestampMs::new(90), 100, 2)
            .is_err()
    );
    assert!(
        runtime
            .request_recovery_stop("third", command().node_id(), TimestampMs::new(90), 100, 3)
            .is_err()
    );
}

/// Completion and ordinary cancellation retain their original distinct Task outcomes.
#[test]
fn recovery_preserves_completion_failure_and_ordinary_cancel() {
    for status in [
        ExecutionStatus::Completed,
        ExecutionStatus::Failed,
        ExecutionStatus::Cancelled,
    ] {
        let mut runtime = running();
        authorize(&mut runtime);
        if status == ExecutionStatus::Cancelled {
            runtime
                .request_cancellation("old")
                .expect("ordinary cancel overrides recovery");
        }
        runtime
            .observe_execution(
                "old",
                command().node_id().clone(),
                2,
                status,
                "original terminal",
            )
            .expect("real terminal");
        runtime.confirm_recovery_stop("old", TimestampMs::new(20));
        assert!(!runtime.recovery_stop_ready(&command(), TimestampMs::new(20)));
        assert_eq!(
            runtime.task_execution_result(
                command().group_id(),
                command().task_ref(),
                [command().role_id()]
            ),
            Some(if status == ExecutionStatus::Completed {
                ObservedTaskExecutionResult::ExecutionCompleted
            } else {
                ObservedTaskExecutionResult::Failed
            })
        );
    }
    let mut late = running();
    authorize(&mut late);
    stopped(&mut late, 110);
    assert!(!late.recovery_stop_ready(&command(), TimestampMs::new(111)));
    assert_eq!(
        late.recovery_stop_disposition("old", TimestampMs::new(111)),
        RecoveryStopDisposition::BudgetExpired
    );
}

/// Malformed durable budgets cannot erase counts or invent accepted stop confirmation.
#[test]
fn recovery_checkpoint_rejects_duplicate_slots_and_invalid_proof() {
    let mut runtime = running();
    authorize(&mut runtime);
    let valid = runtime.checkpoint();
    let mut duplicate = valid.clone();
    duplicate
        .recovery_budgets
        .push(duplicate.recovery_budgets[0].clone());
    assert!(RuntimeExecutionManager::restore(duplicate).is_err());
    let mut invented = valid.clone();
    invented
        .recovery_stops
        .get_mut("old")
        .expect("intent")
        .confirmed_at_ms = Some(20);
    assert!(RuntimeExecutionManager::restore(invented).is_err());
    let mut count = valid;
    count.recovery_budgets[0].used = 0;
    assert!(RuntimeExecutionManager::restore(count).is_err());
    stopped(&mut runtime, 110);
    let mut late_release = runtime.checkpoint();
    late_release
        .recovery_stops
        .get_mut("old")
        .expect("intent")
        .release_authorized = true;
    assert!(RuntimeExecutionManager::restore(late_release).is_err());
}
