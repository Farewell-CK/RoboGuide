//! Whole-set stop, immutable budgets and checkpoint integrity with real Runtime reduction.

use super::*;
use domain::TimestampMs;

/// Creates any bounded number of independent current attempts sharing one frozen session.
fn running_group(count: usize) -> RuntimeExecutionManager {
    let mut session = domain::ExecutionSessionDescriptor {
        schema_version: "roboguide.execution-session/v0.1".into(),
        mission_id: domain::MissionId::new("mission-a").unwrap(),
        group_id: ExecutionGroupId::new("group-a").unwrap(),
        slots: (0..count)
            .map(|index| domain::ExecutionSessionSlot {
                task_id: domain::TaskId::new(format!("task-{index:02}")).unwrap(),
                role_id: RoleId::new("worker").unwrap(),
                actor_id: domain::ActorId::new(format!("actor-{index:02}")).unwrap(),
                dependencies: Vec::new(),
                independent: true,
            })
            .collect(),
        digest: String::new(),
    };
    session.digest = session.canonical_digest().unwrap();
    let mut runtime = RuntimeExecutionManager::new();
    for index in 0..count {
        let mut support = command().recovery_support().unwrap().clone();
        support.support.stop_scope = domain::ExecutionStopScope::ExecutionGroup;
        let command = command_for(
            &format!("task-{index:02}"),
            "worker",
            &format!("node-{index:02}"),
        )
        .with_session(session.clone())
        .with_recovery_support(support);
        let id = format!("original-{index:02}");
        runtime
            .record_dispatched(id.clone(), command.clone(), Vec::new())
            .unwrap();
        runtime
            .observe_execution(
                &id,
                command.node_id().clone(),
                1,
                ExecutionStatus::Running,
                "started",
            )
            .unwrap();
    }
    runtime
}

/// Uses the exact current physical set rather than guessing logical Actors or Node selectors.
fn members(runtime: &RuntimeExecutionManager) -> Vec<GroupRecoveryMember> {
    runtime
        .attempt_history()
        .iter()
        .filter(|attempt| {
            runtime.current_attempt_id(
                attempt.command().group_id(),
                attempt.command().task_ref(),
                attempt.command().role_id(),
            ) == Some(attempt.execution_id())
        })
        .map(|attempt| GroupRecoveryMember {
            execution_id: attempt.execution_id().into(),
            expected_node_id: attempt.command().node_id().clone(),
            repeat_authorized: true,
        })
        .collect()
}

/// Admits one fixed 100-ms purpose with no command delivery or resource side effects.
fn authorize(runtime: &mut RuntimeExecutionManager, id: &str) {
    runtime
        .request_group_recovery_stop(
            &ExecutionGroupId::new("group-a").unwrap(),
            id,
            members(runtime),
            TimestampMs::new(10),
            100,
            2,
        )
        .unwrap();
}

/// Reduces an actual ordered terminal owner fact before recording original receive time.
fn terminal(
    runtime: &mut RuntimeExecutionManager,
    id: &str,
    status: ExecutionStatus,
    received: u64,
) {
    let command = runtime
        .attempt_history()
        .into_iter()
        .find(|attempt| attempt.execution_id() == id)
        .unwrap()
        .command()
        .clone();
    runtime
        .observe_execution(id, command.node_id().clone(), 2, status, "actual terminal")
        .unwrap();
    runtime.confirm_group_recovery_stop(id, TimestampMs::new(received));
}

/// Prepares complete replacements on a candidate registry, preserving originals on any refusal.
fn prepare(runtime: &mut RuntimeExecutionManager, now: u64) -> BTreeMap<String, String> {
    let group = ExecutionGroupId::new("group-a").unwrap();
    let sources = runtime
        .group_continuation_sources(&group, TimestampMs::new(now))
        .unwrap();
    let mut candidate = runtime.clone();
    let mut replacements = BTreeMap::new();
    for (id, command) in sources {
        let fresh = candidate
            .allocate_attempt_id(&group, command.task_ref(), command.role_id())
            .unwrap();
        candidate
            .prepare_dispatch(fresh.clone(), command, Vec::new())
            .unwrap();
        replacements.insert(id, fresh);
    }
    candidate
        .record_group_continuation(&group, TimestampMs::new(now), replacements.clone())
        .unwrap();
    *runtime = candidate;
    replacements
}

/// One missing confirmation prevents all three fresh attempts, independent of simulator topology.
#[test]
fn complete_stop_barrier_preserves_old_facts_and_prepares_only_cancelled_members() {
    let mut runtime = running_group(3);
    terminal(&mut runtime, "original-00", ExecutionStatus::Completed, 5);
    authorize(&mut runtime, "first");
    let group = ExecutionGroupId::new("group-a").unwrap();
    terminal(&mut runtime, "original-01", ExecutionStatus::Cancelled, 20);
    assert_eq!(
        runtime.group_recovery_disposition(&group, TimestampMs::new(21)),
        GroupRecoveryDisposition::AwaitingStop
    );
    assert!(
        runtime
            .group_continuation_sources(&group, TimestampMs::new(21))
            .is_none()
    );
    let task = runtime.executions["original-01"].command.task_ref().clone();
    let role = RoleId::new("worker").unwrap();
    assert_eq!(runtime.task_execution_result(&group, &task, [&role]), None);
    terminal(&mut runtime, "original-02", ExecutionStatus::Cancelled, 22);
    let fresh = prepare(&mut runtime, 23);
    assert_eq!(fresh.len(), 2);
    assert_eq!(
        runtime.execution_status("original-00"),
        Some(ExecutionStatus::Completed)
    );
    assert_eq!(
        runtime.execution_status("original-01"),
        Some(ExecutionStatus::Cancelled)
    );
    assert_eq!(runtime.pending_dispatch_intents().len(), 2);
    assert_eq!(
        runtime.group_recovery_disposition(&group, TimestampMs::new(24)),
        GroupRecoveryDisposition::ContinuationPrepared
    );
    RuntimeExecutionManager::restore(runtime.checkpoint()).unwrap();
}

/// Incomplete sets, wrong owners and absent repeat permission create no partial authorization.
#[test]
fn admission_refuses_wrong_or_partial_sets_without_consuming_any_budget() {
    let mut original = running_group(3);
    let frozen = serde_json::to_value(original.checkpoint()).unwrap();
    let group = ExecutionGroupId::new("group-a").unwrap();
    for variant in 0..4 {
        let mut value = members(&original);
        match variant {
            0 => {
                value.pop();
            }
            1 => {
                value[0].expected_node_id = NodeId::new("wrong").unwrap();
            }
            2 => {
                value[0].repeat_authorized = false;
            }
            _ => {
                value[1] = value[0].clone();
            }
        }
        assert!(
            original
                .request_group_recovery_stop(&group, "r", value, TimestampMs::new(10), 100, 2)
                .is_err()
        );
        assert_eq!(serde_json::to_value(original.checkpoint()).unwrap(), frozen);
    }
    authorize(&mut original, "valid");
}

/// Idempotent commands cannot renew time, alter consent, enlarge the ceiling or omit peers.
#[test]
fn command_identity_and_budget_are_immutable() {
    let mut runtime = running_group(2);
    authorize(&mut runtime, "first");
    let group = ExecutionGroupId::new("group-a").unwrap();
    let targets = members(&runtime);
    runtime
        .request_group_recovery_stop(
            &group,
            "first",
            targets.clone(),
            TimestampMs::new(500),
            100,
            2,
        )
        .unwrap();
    assert_eq!(runtime.group_recovery(&group).unwrap().deadline_ms, 110);
    assert!(
        runtime
            .request_group_recovery_stop(
                &group,
                "first",
                targets.clone(),
                TimestampMs::new(20),
                101,
                2
            )
            .is_err()
    );
    assert!(
        runtime
            .request_group_recovery_stop(&group, "second", targets, TimestampMs::new(20), 100, 2)
            .is_err()
    );
    terminal(&mut runtime, "original-00", ExecutionStatus::Cancelled, 20);
    terminal(&mut runtime, "original-01", ExecutionStatus::Cancelled, 110);
    assert!(
        runtime
            .group_continuation_sources(&group, TimestampMs::new(111))
            .is_none()
    );
    assert_eq!(
        runtime.group_recovery_disposition(&group, TimestampMs::new(111)),
        GroupRecoveryDisposition::BudgetExpired
    );
}

/// Restore preserves actual stop times and fences nonterminal peers as Unknown until real evidence.
#[test]
fn restore_preserves_deadline_and_does_not_invent_the_missing_stop() {
    let mut runtime = running_group(2);
    authorize(&mut runtime, "first");
    terminal(&mut runtime, "original-00", ExecutionStatus::Cancelled, 20);
    let mut restored = RuntimeExecutionManager::restore(runtime.checkpoint()).unwrap();
    let group = ExecutionGroupId::new("group-a").unwrap();
    assert_eq!(
        restored.execution_status("original-01"),
        Some(ExecutionStatus::Unknown)
    );
    assert_eq!(
        restored.group_recovery(&group).unwrap().confirmed_at_ms["original-00"],
        20
    );
    assert!(
        restored
            .group_continuation_sources(&group, TimestampMs::new(21))
            .is_none()
    );
    terminal(&mut restored, "original-01", ExecutionStatus::Cancelled, 22);
    assert_eq!(prepare(&mut restored, 23).len(), 2);
}

/// Cancellation receipts, failures, unsupported continuation and non-independent sessions grant nothing.
#[test]
fn original_failure_and_unsupported_semantics_fail_closed() {
    let group = ExecutionGroupId::new("group-a").unwrap();
    let mut runtime = running_group(2);
    authorize(&mut runtime, "first");
    terminal(&mut runtime, "original-00", ExecutionStatus::Failed, 20);
    terminal(&mut runtime, "original-01", ExecutionStatus::Cancelled, 21);
    assert_eq!(
        runtime.group_recovery_disposition(&group, TimestampMs::new(22)),
        GroupRecoveryDisposition::OriginalFailed
    );
    assert!(
        runtime
            .group_continuation_sources(&group, TimestampMs::new(22))
            .is_none()
    );
    for variant in 0..3 {
        let mut value = running_group(2);
        let context = value.executions.get_mut("original-00").unwrap();
        let mut support = context.command.recovery_support().unwrap().clone();
        match variant {
            0 => {
                support.support.continuation = domain::ExecutionContinuation::Unsupported;
            }
            1 => {
                support.support.stop_scope = domain::ExecutionStopScope::Execution;
            }
            _ => {
                let mut session = context.command.session().unwrap().clone();
                session.slots[0].independent = false;
                session.digest = session.canonical_digest().unwrap();
                context.command = context.command.clone().with_session(session);
            }
        }
        context.command = context.command.clone().with_recovery_support(support);
        assert!(
            value
                .request_group_recovery_stop(
                    &group,
                    "r",
                    members(&value),
                    TimestampMs::new(10),
                    100,
                    2
                )
                .is_err()
        );
        assert!(value.pending_cancellations().is_empty());
    }
}

/// Explicit ordinary Cancel disables the whole round while retaining real stopped history.
#[test]
fn ordinary_cancel_aborts_group_continuation() {
    let mut runtime = running_group(2);
    authorize(&mut runtime, "first");
    terminal(&mut runtime, "original-00", ExecutionStatus::Cancelled, 20);
    runtime.request_cancellation("original-00").unwrap();
    terminal(&mut runtime, "original-01", ExecutionStatus::Cancelled, 21);
    let group = ExecutionGroupId::new("group-a").unwrap();
    assert_eq!(
        runtime.group_recovery_disposition(&group, TimestampMs::new(22)),
        GroupRecoveryDisposition::Aborted
    );
    assert!(
        runtime
            .group_continuation_sources(&group, TimestampMs::new(22))
            .is_none()
    );
    assert_eq!(
        runtime.execution_status("original-00"),
        Some(ExecutionStatus::Cancelled)
    );
}

/// Budget history spans attempts; two accepted rounds cannot be enlarged to admit a third.
#[test]
fn bounded_round_history_survives_new_attempts() {
    let mut runtime = running_group(2);
    let group = ExecutionGroupId::new("group-a").unwrap();
    for round in 0..2 {
        runtime
            .request_group_recovery_stop(
                &group,
                &format!("round-{round}"),
                members(&runtime),
                TimestampMs::new(10 + round * 30),
                100,
                2,
            )
            .unwrap();
        let targets = members(&runtime);
        for target in targets {
            terminal(
                &mut runtime,
                &target.execution_id,
                ExecutionStatus::Cancelled,
                20 + round * 30,
            );
        }
        for fresh in prepare(&mut runtime, 21 + round * 30).values() {
            let owner = runtime.executions[fresh].command.node_id().clone();
            runtime
                .observe_execution(fresh, owner, 1, ExecutionStatus::Running, "admitted")
                .unwrap();
            runtime.confirm_group_continuation_admission(fresh, TimestampMs::new(22 + round * 30));
        }
    }
    assert!(
        runtime
            .request_group_recovery_stop(
                &group,
                "third",
                members(&runtime),
                TimestampMs::new(90),
                100,
                2
            )
            .is_err()
    );
    assert!(
        runtime
            .request_group_recovery_stop(
                &group,
                "third",
                members(&runtime),
                TimestampMs::new(90),
                100,
                3
            )
            .is_err()
    );
    RuntimeExecutionManager::restore(runtime.checkpoint()).unwrap();
}

/// Corrupt membership, timing and cross-set replacement association cannot restore permission.
#[test]
fn corrupted_group_checkpoint_is_rejected() {
    let mut runtime = running_group(2);
    authorize(&mut runtime, "first");
    terminal(&mut runtime, "original-00", ExecutionStatus::Cancelled, 20);
    terminal(&mut runtime, "original-01", ExecutionStatus::Cancelled, 21);
    prepare(&mut runtime, 22);
    let valid = serde_json::to_value(runtime.checkpoint()).unwrap();
    for variant in 0..5 {
        let mut document = valid.clone();
        match variant {
            0 => {
                document["group_recoveries"][0]["deadline_ms"] = serde_json::json!(1000);
            }
            1 => {
                document["group_recoveries"][0]["members"][0]["expected_node_id"] =
                    serde_json::json!("wrong");
            }
            2 => {
                document["group_recoveries"][0]["confirmed_at_ms"]["original-00"] =
                    serde_json::json!(1);
            }
            3 => {
                document["group_recoveries"][0]["replacements"]
                    .as_object_mut()
                    .unwrap()
                    .remove("original-01");
            }
            _ => {
                let replacement = document["group_recoveries"][0]["replacements"]["original-00"]
                    .as_str()
                    .unwrap()
                    .to_owned();
                // Prepared outbox entries are still Dispatched and have no actual Node admission.
                document["group_recoveries"][0]["admitted_at_ms"][replacement] =
                    serde_json::json!(25);
            }
        }
        assert!(
            RuntimeExecutionManager::restore(serde_json::from_value(document).unwrap()).is_err()
        );
    }
}

/// Late actual node admission is evidence, but cannot turn an expired recovery budget into success.
#[test]
fn late_replacement_admission_never_renews_group_budget() {
    for late in [false, true] {
        let mut runtime = running_group(2);
        let group = ExecutionGroupId::new("group-a").unwrap();
        authorize(&mut runtime, "first");
        terminal(&mut runtime, "original-00", ExecutionStatus::Cancelled, 20);
        terminal(&mut runtime, "original-01", ExecutionStatus::Cancelled, 21);
        let replacements = prepare(&mut runtime, 22);
        for (index, fresh) in replacements.values().enumerate() {
            let owner = runtime.execution_command(fresh).unwrap().node_id().clone();
            runtime
                .observe_execution(
                    fresh,
                    owner,
                    1,
                    ExecutionStatus::Accepted,
                    "actual admitted",
                )
                .unwrap();
            runtime.confirm_group_continuation_admission(
                fresh,
                TimestampMs::new(if late && index == 1 { 110 } else { 25 }),
            );
        }
        assert_eq!(
            runtime.group_recovery_disposition(&group, TimestampMs::new(120)),
            if late {
                GroupRecoveryDisposition::BudgetExpired
            } else {
                GroupRecoveryDisposition::Continued
            }
        );
        let restored = RuntimeExecutionManager::restore(runtime.checkpoint()).unwrap();
        assert_eq!(restored.group_recovery(&group).unwrap().deadline_ms, 110);
        assert_eq!(
            restored.group_recovery_disposition(&group, TimestampMs::new(121)),
            if late {
                GroupRecoveryDisposition::BudgetExpired
            } else {
                GroupRecoveryDisposition::Continued
            }
        );
        if late {
            assert!(restored.group_recovery_holds_dispatch(&group, TimestampMs::new(121)));
            assert!(
                runtime
                    .request_group_recovery_stop(
                        &group,
                        "second",
                        members(&runtime),
                        TimestampMs::new(122),
                        100,
                        2
                    )
                    .is_err()
            );
        }
    }
}
