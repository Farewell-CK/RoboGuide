//! Runtime Execution Relation tests.

use super::*;
use crate::{ExecutionRuntimeError, ObservedTaskExecutionResult};
use domain::{
    CapabilityContractRef, CorrelationId, ExecutionCommand, ExecutionIntent, ExecutionRelationSpec,
    ExecutionRelationType, MapId, MapRevisionId, NodeId, PlannedExecutionRef,
    SharedSpatialReference, TaskId,
};

/// Builds one committed command for a logical relation endpoint.
fn command(task_id: &str, role_id: &str, node_id: &str) -> ExecutionCommand {
    ExecutionCommand::new(
        MissionId::new("mission-relation").expect("mission valid"),
        TaskId::new(task_id).expect("task valid"),
        ExecutionGroupId::new("group-relation").expect("group valid"),
        RoleId::new(role_id).expect("role valid"),
        NodeId::new(node_id).expect("node valid"),
        ExecutionIntent::new(
            CapabilityContractRef::new("test", "execute", "v1").expect("contract valid"),
            BTreeMap::new(),
        )
        .expect("intent valid"),
        CorrelationId::new("relation-test").expect("correlation valid"),
    )
}

/// Builds the v0.1 relation used by Runtime state-reduction tests.
fn relation() -> ExecutionRelationSpec {
    ExecutionRelationSpec::new(
        ExecutionRelationId::new("safety-guards-navigation").expect("relation valid"),
        PlannedExecutionRef::new(
            TaskId::new("observe").expect("task valid"),
            RoleId::new("safety").expect("role valid"),
        ),
        PlannedExecutionRef::new(
            TaskId::new("navigate").expect("task valid"),
            RoleId::new("navigator").expect("role valid"),
        ),
        ExecutionRelationKind::RequiresActive,
    )
    .expect("relation valid")
}

/// Builds a typed shared-map/frame relation over the same logical endpoint pair.
fn spatial_relation() -> ExecutionRelationSpec {
    ExecutionRelationSpec::new_typed(
        ExecutionRelationId::new("shared-localization").expect("relation valid"),
        PlannedExecutionRef::new(
            TaskId::new("observe").expect("task valid"),
            RoleId::new("safety").expect("role valid"),
        ),
        PlannedExecutionRef::new(
            TaskId::new("navigate").expect("task valid"),
            RoleId::new("navigator").expect("role valid"),
        ),
        ExecutionRelationType::SharedSpatialReference {
            reference: SharedSpatialReference::new(selector("r1"), "map")
                .expect("spatial reference valid"),
        },
    )
    .expect("typed relation valid")
}

/// Builds one immutable map selector for relation evidence tests.
fn selector(revision: &str) -> MapRevisionSelector {
    MapRevisionSelector::new(
        MapId::new("building-a").expect("map id valid"),
        MapRevisionId::new(revision).expect("revision id valid"),
    )
}

/// Builds strong spatial evidence for one dispatched command and attempt.
fn spatial_evidence(
    command: &ExecutionCommand,
    execution_id: &str,
    revision: &str,
    frame_id: &str,
    received_at: u64,
) -> SharedSpatialEvidence {
    SharedSpatialEvidence {
        group_id: command.group_id().clone(),
        task_ref: command.task_ref().clone(),
        role_id: command.role_id().clone(),
        execution_id: execution_id.to_string(),
        node_id: command.node_id().clone(),
        selector: selector(revision),
        frame_id: frame_id.to_string(),
        received_at: TimestampMs::new(received_at),
    }
}

/// Route loss and rejected command receipts immediately fence a previously satisfied relation.
#[test]
fn physical_ambiguity_refreshes_relation_fences_without_another_fact() {
    let mut baseline = RuntimeExecutionManager::new();
    let source = command("observe", "safety", "cane-a");
    let target = command("navigate", "navigator", "dog-a");
    baseline
        .register_relations(source.group_id(), source.mission_id(), &[relation()])
        .expect("relation registers");
    for (id, command) in [("source", &source), ("target", &target)] {
        baseline
            .prepare_dispatch(id.to_string(), command.clone(), Vec::new())
            .expect("attempt prepares");
        baseline
            .observe_execution(
                id,
                command.node_id().clone(),
                1,
                ExecutionStatus::Running,
                "",
            )
            .expect("running evidence records");
    }
    assert_eq!(
        baseline.relation_snapshots(source.group_id())[0].state(),
        ExecutionRelationState::Satisfied
    );
    for fault in 0..3 {
        let mut runtime = baseline.clone();
        let events = match fault {
            0 => runtime.observe_node_unavailable(source.node_id(), "route lost"),
            1 => runtime
                .observe_dispatch_receipt(
                    "source",
                    "dispatch-source",
                    source.node_id(),
                    false,
                    "rejected",
                )
                .expect("Execute rejection reduces"),
            _ => {
                runtime
                    .request_cancellation("source")
                    .expect("cancel intent records");
                runtime
                    .observe_cancellation_receipt(
                        "source",
                        "cancel-source",
                        source.node_id(),
                        false,
                        "rejected",
                    )
                    .expect("Cancel rejection reduces")
            }
        };
        let snapshot = &runtime.relation_snapshots(source.group_id())[0];
        assert_eq!(snapshot.state(), ExecutionRelationState::Unknown);
        assert!(snapshot.reconciliation_required());
        assert!(
            events.iter().any(|event| matches!(
                event,
                ExecutionEvent::RelationReconciliationRequired { .. }
            ))
        );
    }
}

/// Strong map/frame evidence drives Pending, Satisfied, and Violated relation states.
#[test]
fn shared_spatial_relation_reduces_current_attempt_evidence() {
    let mut runtime = RuntimeExecutionManager::new();
    let group_id = ExecutionGroupId::new("group-relation").expect("group valid");
    runtime
        .register_relations(
            &group_id,
            &MissionId::new("mission-relation").expect("mission valid"),
            &[spatial_relation()],
        )
        .expect("relation registers");
    let source = command("observe", "safety", "cane-a");
    let target = command("navigate", "navigator", "dog-a");
    runtime
        .record_dispatched("attempt-source".to_string(), source.clone(), Vec::new())
        .expect("source dispatch records");
    runtime
        .record_dispatched("attempt-target".to_string(), target.clone(), Vec::new())
        .expect("target dispatch records");
    runtime
        .observe_execution(
            "attempt-target",
            target.node_id().clone(),
            1,
            ExecutionStatus::Running,
            "",
        )
        .expect("target running records");
    assert_eq!(
        runtime.relation_snapshots(&group_id)[0].state(),
        ExecutionRelationState::Pending
    );
    assert!(!runtime.relation_snapshots(&group_id)[0].reconciliation_required());
    runtime
        .observe_execution(
            "attempt-source",
            source.node_id().clone(),
            1,
            ExecutionStatus::Running,
            "",
        )
        .expect("source running records");

    runtime
        .observe_shared_spatial_evidence(spatial_evidence(
            &source,
            "attempt-source",
            "r1",
            "map",
            10,
        ))
        .expect("source localization records");
    let satisfied = runtime
        .observe_shared_spatial_evidence(spatial_evidence(
            &target,
            "attempt-target",
            "r1",
            "map",
            11,
        ))
        .expect("target localization records");
    assert!(satisfied.iter().any(|event| matches!(
        event,
        ExecutionEvent::RelationStateChanged {
            current: ExecutionRelationState::Satisfied,
            ..
        }
    )));
    assert!(!runtime.relation_snapshots(&group_id)[0].reconciliation_required());

    let violated = runtime
        .observe_shared_spatial_evidence(spatial_evidence(
            &source,
            "attempt-source",
            "r2",
            "map",
            12,
        ))
        .expect("newer conflicting localization records");
    assert!(violated.iter().any(|event| matches!(
        event,
        ExecutionEvent::RelationReconciliationRequired {
            state: ExecutionRelationState::Violated,
            ..
        }
    )));
}

/// Rebind removes prior-attempt spatial evidence and rejects its later delivery.
#[test]
fn shared_spatial_evidence_follows_rebind_attempt_identity() {
    let mut runtime = RuntimeExecutionManager::new();
    let source = command("observe", "safety", "cane-a");
    runtime
        .record_dispatched("attempt-source-1".to_string(), source.clone(), Vec::new())
        .expect("first source dispatch records");
    runtime
        .observe_shared_spatial_evidence(spatial_evidence(
            &source,
            "attempt-source-1",
            "r1",
            "map",
            10,
        ))
        .expect("first attempt evidence records");
    let replacement = command("observe", "safety", "cane-b");
    runtime
        .record_dispatched(
            "attempt-source-2".to_string(),
            replacement.clone(),
            Vec::new(),
        )
        .expect("replacement dispatch records");

    assert!(
        runtime
            .shared_spatial_evidence(
                replacement.group_id(),
                replacement.task_ref(),
                replacement.role_id()
            )
            .is_none()
    );
    assert!(matches!(
        runtime.observe_shared_spatial_evidence(spatial_evidence(
            &source,
            "attempt-source-1",
            "r1",
            "map",
            11,
        )),
        Err(ExecutionRuntimeError::ReconciliationRequired(_))
    ));
}

/// A relation follows the current logical-slot attempt and ignores old-attempt late facts.
#[test]
fn relation_tracks_rebind_without_node_identity() {
    let mut runtime = RuntimeExecutionManager::new();
    let group_id = ExecutionGroupId::new("group-relation").expect("group valid");
    let mission_id = MissionId::new("mission-relation").expect("mission valid");
    runtime
        .register_relations(&group_id, &mission_id, &[relation()])
        .expect("relation registers");
    let source_old = command("observe", "safety", "cane-a");
    let target = command("navigate", "navigator", "dog-a");
    runtime
        .record_dispatched(
            "attempt-source-1".to_string(),
            source_old.clone(),
            Vec::new(),
        )
        .expect("source dispatch records");
    runtime
        .record_dispatched("attempt-target-1".to_string(), target.clone(), Vec::new())
        .expect("target dispatch records");

    let pending = runtime
        .observe_execution(
            "attempt-target-1",
            target.node_id().clone(),
            1,
            ExecutionStatus::Accepted,
            "",
        )
        .expect("target acceptance records");
    assert!(pending.iter().any(|event| matches!(
        event,
        ExecutionEvent::RelationStateChanged {
            current: ExecutionRelationState::Pending,
            ..
        }
    )));
    runtime
        .observe_execution(
            "attempt-source-1",
            source_old.node_id().clone(),
            1,
            ExecutionStatus::Running,
            "",
        )
        .expect("source running records");
    assert_eq!(
        runtime.relation_snapshots(&group_id)[0].state(),
        ExecutionRelationState::Satisfied
    );

    let unknown = runtime
        .observe_execution(
            "attempt-source-1",
            source_old.node_id().clone(),
            2,
            ExecutionStatus::Unknown,
            "source connection lost",
        )
        .expect("source ambiguity records");
    assert!(unknown.iter().any(|event| matches!(
        event,
        ExecutionEvent::RelationReconciliationRequired {
            state: ExecutionRelationState::Unknown,
            ..
        }
    )));

    let source_new = command("observe", "safety", "cane-b");
    runtime
        .record_dispatched(
            "attempt-source-2".to_string(),
            source_new.clone(),
            Vec::new(),
        )
        .expect("replacement attempt dispatches");
    runtime
        .observe_execution(
            "attempt-source-2",
            source_new.node_id().clone(),
            1,
            ExecutionStatus::Accepted,
            "",
        )
        .expect("replacement acceptance records");
    let snapshot = &runtime.relation_snapshots(&group_id)[0];
    assert_eq!(snapshot.state(), ExecutionRelationState::Satisfied);
    assert!(snapshot.reconciliation_required());
    assert_eq!(snapshot.source_execution_id(), Some("attempt-source-2"));

    runtime
        .acknowledge_relation_reconciliation(
            &group_id,
            &ExecutionRelationId::new("safety-guards-navigation").expect("relation valid"),
        )
        .expect("Control recovery explicitly acknowledges the repaired relation");
    assert!(!runtime.relation_snapshots(&group_id)[0].reconciliation_required());

    let late_old_fact = runtime
        .observe_execution(
            "attempt-source-1",
            source_old.node_id().clone(),
            3,
            ExecutionStatus::Running,
            "late old-attempt fact",
        )
        .expect("late old attempt remains valid history");
    assert!(
        !late_old_fact
            .iter()
            .any(|event| matches!(event, ExecutionEvent::RelationStateChanged { .. }))
    );
    assert_eq!(
        runtime.relation_snapshots(&group_id)[0].source_execution_id(),
        Some("attempt-source-2")
    );

    runtime
        .observe_execution(
            "attempt-target-1",
            target.node_id().clone(),
            2,
            ExecutionStatus::Completed,
            "",
        )
        .expect("target completion records");
    assert_eq!(
        runtime.task_execution_result(
            &group_id,
            target.task_ref(),
            std::iter::once(target.role_id())
        ),
        Some(ObservedTaskExecutionResult::ExecutionCompleted)
    );
}

/// Restart turns a previously satisfied nonterminal relation into Unknown and fences success.
#[test]
fn relation_restore_is_conservative() {
    let mut runtime = RuntimeExecutionManager::new();
    let group_id = ExecutionGroupId::new("group-relation").expect("group valid");
    runtime
        .register_relations(
            &group_id,
            &MissionId::new("mission-relation").expect("mission valid"),
            &[relation()],
        )
        .expect("relation registers");
    let source = command("observe", "safety", "cane-a");
    let target = command("navigate", "navigator", "dog-a");
    runtime
        .record_dispatched("attempt-source".to_string(), source.clone(), Vec::new())
        .expect("source dispatch records");
    runtime
        .record_dispatched("attempt-target".to_string(), target.clone(), Vec::new())
        .expect("target dispatch records");
    runtime
        .observe_execution(
            "attempt-source",
            source.node_id().clone(),
            1,
            ExecutionStatus::Running,
            "",
        )
        .expect("source running records");
    runtime
        .observe_execution(
            "attempt-target",
            target.node_id().clone(),
            1,
            ExecutionStatus::Running,
            "",
        )
        .expect("target running records");

    let restored = RuntimeExecutionManager::restore(runtime.checkpoint())
        .expect("relation checkpoint restores");
    let snapshot = &restored.relation_snapshots(&group_id)[0];
    assert_eq!(snapshot.state(), ExecutionRelationState::Unknown);
    assert!(snapshot.reconciliation_required());
    assert!(matches!(
        restored.validate_dispatch("attempt-target", &target, &[]),
        Err(ExecutionRuntimeError::ReconciliationRequired(_))
    ));
}

/// A target cannot complete successfully without evidence that its source became active.
#[test]
fn target_completion_requires_relation_satisfaction_proof() {
    let mut runtime = RuntimeExecutionManager::new();
    let group_id = ExecutionGroupId::new("group-relation").expect("group valid");
    runtime
        .register_relations(
            &group_id,
            &MissionId::new("mission-relation").expect("mission valid"),
            &[relation()],
        )
        .expect("relation registers");
    let target = command("navigate", "navigator", "dog-a");
    runtime
        .record_dispatched("attempt-target".to_string(), target.clone(), Vec::new())
        .expect("target dispatch records");
    runtime
        .observe_execution(
            "attempt-target",
            target.node_id().clone(),
            1,
            ExecutionStatus::Accepted,
            "",
        )
        .expect("target acceptance records");
    let completion = runtime
        .observe_execution(
            "attempt-target",
            target.node_id().clone(),
            2,
            ExecutionStatus::Completed,
            "",
        )
        .expect("target completion records");
    assert!(completion.iter().any(|event| matches!(
        event,
        ExecutionEvent::RelationReconciliationRequired {
            state: ExecutionRelationState::Unknown,
            ..
        }
    )));
    assert_eq!(
        runtime.task_execution_result(
            &group_id,
            target.task_ref(),
            std::iter::once(target.role_id())
        ),
        None
    );
}

/// A failed target ends the relation window without manufacturing a second ambiguity.
#[test]
fn target_failure_remains_a_task_failure_without_relation_unknown() {
    let mut runtime = RuntimeExecutionManager::new();
    let group_id = ExecutionGroupId::new("group-relation").expect("group valid");
    runtime
        .register_relations(
            &group_id,
            &MissionId::new("mission-relation").expect("mission valid"),
            &[relation()],
        )
        .expect("relation registers");
    let target = command("navigate", "navigator", "dog-a");
    runtime
        .record_dispatched("attempt-target".to_string(), target.clone(), Vec::new())
        .expect("target dispatch records");
    runtime
        .observe_execution(
            "attempt-target",
            target.node_id().clone(),
            1,
            ExecutionStatus::Accepted,
            "",
        )
        .expect("target acceptance records");
    let failure = runtime
        .observe_execution(
            "attempt-target",
            target.node_id().clone(),
            2,
            ExecutionStatus::Failed,
            "local execution failed",
        )
        .expect("target failure records");
    assert!(
        !failure
            .iter()
            .any(|event| matches!(event, ExecutionEvent::RelationReconciliationRequired { .. }))
    );
    assert_eq!(
        runtime.relation_snapshots(&group_id)[0].state(),
        ExecutionRelationState::Dormant
    );
    assert_eq!(
        runtime.task_execution_result(
            &group_id,
            target.task_ref(),
            std::iter::once(target.role_id())
        ),
        Some(ObservedTaskExecutionResult::Failed)
    );
}
