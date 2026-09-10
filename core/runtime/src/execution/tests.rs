//! Runtime execution authority tests.

use super::*;
use domain::{
    CapabilityContractRef, CorrelationId, ExecutionIntent, ExecutionRelationId,
    ExecutionRelationKind, ExecutionRelationSpec, MissionId, PlannedExecutionRef, TaskId,
};

/// Builds one deterministic command for Runtime registry tests.
fn command() -> ExecutionCommand {
    command_for("task-a", "carrier", "node-a")
}

/// Builds one deterministic command for an exact logical execution slot.
fn command_for(task_id: &str, role_id: &str, node_id: &str) -> ExecutionCommand {
    ExecutionCommand::new(
        MissionId::new("mission-a").expect("mission valid"),
        TaskId::new(task_id).expect("task valid"),
        ExecutionGroupId::new("group-a").expect("group valid"),
        RoleId::new(role_id).expect("role valid"),
        NodeId::new(node_id).expect("node valid"),
        ExecutionIntent::new(
            CapabilityContractRef::new("mobility", "move", "v1").expect("contract valid"),
            BTreeMap::new(),
        )
        .expect("intent valid"),
        CorrelationId::new("runtime-test").expect("correlation valid"),
    )
}

/// Persists dispatch intent and fences automatic replay after Controller restart.
#[test]
fn dispatch_outbox_survives_restore_and_receipt_is_idempotent() {
    let mut runtime = RuntimeExecutionManager::new();
    let command = command();
    runtime
        .prepare_dispatch("attempt-1".to_string(), command.clone(), Vec::new())
        .expect("intent prepares");
    assert_eq!(runtime.pending_dispatch_intents().len(), 1);

    let mut restored = RuntimeExecutionManager::restore(runtime.checkpoint())
        .expect("unacknowledged dispatch restores");
    assert!(
        restored.pending_dispatch_intents().is_empty(),
        "restored physical ambiguity requires recovery rather than implicit replay"
    );
    let events = restored
        .observe_dispatch_receipt(
            "attempt-1",
            "dispatch-attempt-1",
            command.node_id(),
            true,
            "",
        )
        .expect("receipt is accepted");
    assert!(events.is_empty());
    assert!(restored.pending_dispatch_intents().is_empty());
    assert!(
        restored
            .observe_dispatch_receipt(
                "attempt-1",
                "dispatch-attempt-1",
                command.node_id(),
                true,
                "",
            )
            .is_ok()
    );
    assert!(matches!(
        restored.observe_dispatch_receipt(
            "attempt-1",
            "dispatch-attempt-1",
            &NodeId::new("wrong-node").expect("node valid"),
            true,
            "",
        ),
        Err(ExecutionRuntimeError::NodeOwnership(_))
    ));
}

/// Durable cancellation suppresses Execute and survives until terminal Node evidence.
#[test]
fn cancellation_intent_survives_restart_and_terminal_fact_clears_delivery() {
    let mut runtime = RuntimeExecutionManager::new();
    let command = command();
    runtime
        .prepare_dispatch("attempt-1".to_string(), command.clone(), Vec::new())
        .expect("intent prepares");
    runtime
        .request_cancellation("attempt-1")
        .expect("cancellation records");
    assert!(runtime.pending_dispatch_intents().is_empty());

    let mut restored =
        RuntimeExecutionManager::restore(runtime.checkpoint()).expect("cancellation restores");
    assert_eq!(restored.pending_cancellations().len(), 1);
    restored
        .observe_cancellation_receipt("attempt-1", "cancel-attempt-1", command.node_id(), true, "")
        .expect("Cancel receipt matches durable intent");
    assert_eq!(
        restored.execution_status("attempt-1"),
        Some(ExecutionStatus::Unknown),
        "Cancel receipt must not synthesize a terminal lifecycle fact"
    );
    restored
        .observe_execution(
            "attempt-1",
            command.node_id().clone(),
            1,
            ExecutionStatus::Cancelled,
            "cancelled before local dispatch",
        )
        .expect("terminal cancellation records");
    assert!(restored.pending_cancellations().is_empty());
}

/// Allocated physical attempt identities advance while the logical slot remains stable.
#[test]
fn attempt_identity_advances_per_logical_slot() {
    let mut runtime = RuntimeExecutionManager::new();
    let group = ExecutionGroupId::new("group-a").expect("group valid");
    let task = TaskRef::new(
        MissionId::new("mission-a").expect("mission valid"),
        TaskId::new("task-a").expect("task valid"),
    );
    let role = RoleId::new("carrier").expect("role valid");
    assert_eq!(
        runtime
            .allocate_attempt_id(&group, &task, &role)
            .expect("first attempt allocates"),
        "attempt-7:group-a-6:task-a-7:carrier-1"
    );
    assert_eq!(
        runtime
            .allocate_attempt_id(&group, &task, &role)
            .expect("second attempt allocates"),
        "attempt-7:group-a-6:task-a-7:carrier-2"
    );
}

/// Allocation skips a retained attempt identity when migrating pre-generation history.
#[test]
fn attempt_identity_does_not_collide_with_retained_history() {
    let mut runtime = RuntimeExecutionManager::new();
    let command = command();
    runtime
        .prepare_dispatch(
            "attempt-7:group-a-6:task-a-7:carrier-1".to_string(),
            command.clone(),
            Vec::new(),
        )
        .expect("historical attempt prepares");

    assert_eq!(
        runtime
            .allocate_attempt_id(command.group_id(), command.task_ref(), command.role_id())
            .expect("unused attempt allocates"),
        "attempt-7:group-a-6:task-a-7:carrier-2"
    );
}

/// Node loss fences one physical attempt once while a replacement retains immutable history.
#[test]
fn node_loss_emits_one_recovery_event_and_retains_attempt_history() {
    let mut runtime = RuntimeExecutionManager::new();
    let original = command();
    runtime
        .prepare_dispatch("attempt-1".to_string(), original.clone(), Vec::new())
        .expect("original attempt prepares");
    runtime
        .observe_execution(
            "attempt-1",
            original.node_id().clone(),
            1,
            ExecutionStatus::Running,
            "started",
        )
        .expect("running fact records");

    let recovery = runtime.observe_node_unavailable(original.node_id(), "route lost");
    assert!(matches!(
        recovery.as_slice(),
        [ExecutionEvent::RecoveryRequired { execution_id, .. }] if execution_id == "attempt-1"
    ));
    assert!(
        runtime
            .observe_node_unavailable(original.node_id(), "duplicate route loss")
            .is_empty()
    );

    let replacement = command_for("task-a", "carrier", "node-b");
    runtime
        .prepare_dispatch("attempt-2".to_string(), replacement, Vec::new())
        .expect("replacement attempt prepares");
    assert_eq!(
        runtime.current_attempt_id(original.group_id(), original.task_ref(), original.role_id()),
        Some("attempt-2")
    );
    let history = runtime.attempt_history();
    assert_eq!(history.len(), 2);
    assert_eq!(runtime.attempts_for_group(original.group_id()).len(), 2);
    assert!(history.iter().any(|attempt| {
        attempt.execution_id() == "attempt-1" && attempt.status() == ExecutionStatus::Unknown
    }));
    assert!(history.iter().any(|attempt| {
        attempt.execution_id() == "attempt-2" && attempt.status() == ExecutionStatus::Dispatched
    }));
    assert!(
        runtime
            .observe_node_unavailable(original.node_id(), "historical route loss")
            .is_empty()
    );
    assert!(
        runtime
            .observe_execution(
                "attempt-1",
                original.node_id().clone(),
                2,
                ExecutionStatus::Cancelled,
                "late historical terminal fact",
            )
            .expect("historical terminal fact is retained")
            .is_empty()
    );
    let reactivated = runtime
        .observe_execution(
            "attempt-2",
            NodeId::new("node-b").expect("node valid"),
            1,
            ExecutionStatus::Accepted,
            "replacement accepted",
        )
        .expect("replacement acceptance records");
    assert!(matches!(
        reactivated.as_slice(),
        [ExecutionEvent::TaskActivated { .. }]
    ));
}

/// Node acceptance activates a Task exactly once and terminal facts reduce its result.
#[test]
fn runtime_drives_activation_and_terminal_result() {
    let mut runtime = RuntimeExecutionManager::new();
    let command = command();
    runtime
        .record_dispatched("execution-a".to_string(), command.clone(), Vec::new())
        .expect("dispatch records");

    let activated = runtime
        .observe_execution(
            "execution-a",
            command.node_id().clone(),
            1,
            ExecutionStatus::Accepted,
            "",
        )
        .expect("acceptance records");
    assert!(matches!(
        activated.as_slice(),
        [ExecutionEvent::TaskActivated { .. }]
    ));
    let repeated = runtime
        .observe_execution(
            "execution-a",
            command.node_id().clone(),
            2,
            ExecutionStatus::Running,
            "",
        )
        .expect("running records");
    assert!(repeated.is_empty());
    runtime
        .observe_execution(
            "execution-a",
            command.node_id().clone(),
            3,
            ExecutionStatus::Completed,
            "",
        )
        .expect("completion records");
    assert_eq!(
        runtime.task_result(
            command.group_id(),
            command.task_ref(),
            std::iter::once(command.role_id())
        ),
        Some(ObservedTaskResult::Succeeded)
    );
}

/// Restore fences command replay and converts nonterminal state to Unknown.
#[test]
fn restore_requires_reconciliation_before_replay() {
    let mut runtime = RuntimeExecutionManager::new();
    let command = command();
    runtime
        .record_dispatched("execution-a".to_string(), command.clone(), Vec::new())
        .expect("dispatch records");
    runtime
        .observe_execution(
            "execution-a",
            command.node_id().clone(),
            1,
            ExecutionStatus::Running,
            "",
        )
        .expect("running records");

    let restored =
        RuntimeExecutionManager::restore(runtime.checkpoint()).expect("checkpoint restores");
    assert_eq!(
        restored.execution_status("execution-a"),
        Some(ExecutionStatus::Unknown)
    );
    assert!(matches!(
        restored.validate_dispatch("execution-a", &command, &[]),
        Err(ExecutionRuntimeError::ReconciliationRequired(_))
    ));
    assert_eq!(
        restored.task_result(
            command.group_id(),
            command.task_ref(),
            std::iter::once(command.role_id())
        ),
        None,
        "unknown physical state must remain recovery-pending"
    );
}

/// Restore rejects a satisfaction proof attached to a non-target execution attempt.
#[test]
fn restore_rejects_relation_proof_for_wrong_logical_slot() {
    let mut runtime = RuntimeExecutionManager::new();
    let group_id = ExecutionGroupId::new("group-a").expect("group valid");
    runtime
        .register_relations(
            &group_id,
            &MissionId::new("mission-a").expect("mission valid"),
            &[ExecutionRelationSpec::new(
                ExecutionRelationId::new("source-guards-target").expect("relation valid"),
                PlannedExecutionRef::new(
                    TaskId::new("source-task").expect("task valid"),
                    RoleId::new("source-role").expect("role valid"),
                ),
                PlannedExecutionRef::new(
                    TaskId::new("task-a").expect("task valid"),
                    RoleId::new("carrier").expect("role valid"),
                ),
                ExecutionRelationKind::RequiresActive,
            )
            .expect("relation valid")],
        )
        .expect("relation registers");
    let source = command_for("source-task", "source-role", "node-source");
    let target = command();
    runtime
        .record_dispatched("source-execution".to_string(), source.clone(), Vec::new())
        .expect("source dispatch records");
    runtime
        .record_dispatched("target-execution".to_string(), target.clone(), Vec::new())
        .expect("target dispatch records");
    runtime
        .observe_execution(
            "source-execution",
            source.node_id().clone(),
            1,
            ExecutionStatus::Running,
            "",
        )
        .expect("source running records");
    runtime
        .observe_execution(
            "target-execution",
            target.node_id().clone(),
            1,
            ExecutionStatus::Running,
            "",
        )
        .expect("target running records");

    let mut checkpoint = runtime.checkpoint();
    checkpoint.relation_proofs[0].target_execution_id = "source-execution".to_string();
    assert!(matches!(
        RuntimeExecutionManager::restore(checkpoint),
        Err(ExecutionRuntimeError::InvalidCheckpoint(reason))
            if reason.contains("target logical slot")
    ));
}

/// Unknown execution produces recovery evidence without becoming a terminal Task failure.
#[test]
fn unknown_execution_requires_reconciliation_without_task_failure() {
    let mut runtime = RuntimeExecutionManager::new();
    let command = command();
    runtime
        .record_dispatched("execution-a".to_string(), command.clone(), Vec::new())
        .expect("dispatch records");

    let events = runtime
        .observe_execution(
            "execution-a",
            command.node_id().clone(),
            1,
            ExecutionStatus::Unknown,
            "physical outcome is ambiguous",
        )
        .expect("unknown fact records");

    assert!(matches!(
        events.as_slice(),
        [ExecutionEvent::RecoveryRequired {
            context: Some(_),
            ..
        }]
    ));
    assert_eq!(
        runtime.task_result(
            command.group_id(),
            command.task_ref(),
            std::iter::once(command.role_id())
        ),
        None
    );

    let mut restored =
        RuntimeExecutionManager::restore(runtime.checkpoint()).expect("checkpoint restores");
    let accepted = restored
        .observe_execution(
            "execution-a",
            command.node_id().clone(),
            2,
            ExecutionStatus::Accepted,
            "",
        )
        .expect("accepted fact records after recovery");
    assert!(matches!(
        accepted.as_slice(),
        [ExecutionEvent::TaskActivated { .. }]
    ));
}
