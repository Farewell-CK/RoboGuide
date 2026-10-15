//! Generic current-attempt verification ingress, continuous observation and late incident handling.

use crate::*;
use domain::{
    SemanticVerdict, SemanticVerifierEvidence, SemanticVerifierReceipt, VerifierAttemptRef,
    VerifierFinality,
};
use ports::EventSink;

/// Validates generic source coverage, falling back only to the explicitly configured legacy final feed.
pub(crate) fn validate_plan(
    controller: &ControllerState,
    plan: &domain::MissionPlan,
    tenant: &str,
    feed: Option<&TaskVerifierFeed>,
) -> Result<bool, String> {
    let generic = plan
        .task_graph()
        .tasks()
        .iter()
        .all(|task| match task.satisfaction_basis() {
            domain::TaskSatisfactionBasis::ExecutionReport => true,
            domain::TaskSatisfactionBasis::VerifierEvidence(spec) => controller
                .bridge
                .control()
                .registered_verifier_source(spec, tenant)
                .is_ok(),
        });
    if generic {
        return Ok(true);
    }
    feed.ok_or("Mission requires one authorized independent verifier source per Task")?
        .validate_plan(plan)?;
    Ok(false)
}

/// Derives a complete logical Role/current-attempt set from accepted execution authority.
fn attempts(
    controller: &ControllerState,
    task: &domain::TaskRef,
) -> Result<Vec<VerifierAttemptRef>, String> {
    let execution = controller
        .orchestrator
        .execution(task.mission_id())
        .ok_or("unknown verifier Mission")?;
    let planned = execution
        .plan()
        .task_graph()
        .tasks()
        .iter()
        .find(|planned| planned.requirement().task_ref() == task)
        .ok_or("unknown verifier Task")?;
    planned
        .requirement()
        .roles()
        .iter()
        .map(|role| {
            let attempt_id = controller
                .bridge
                .current_task_attempt_id(execution.group_id(), task, role.role_id())
                .ok_or("verifier Task has no complete current attempt set")?;
            Ok(VerifierAttemptRef {
                role_id: role.role_id().clone(),
                attempt_id: attempt_id.into(),
            })
        })
        .collect()
}

/// Checks immutable plan, declared measurement, exact attempts and independently hashed artifact bytes.
fn validate_input(
    controller: &ControllerState,
    input: &SemanticVerifierEvidence,
) -> Result<(), String> {
    input.validate()?;
    if super::tenant::source_digest(controller, input.task_ref.mission_id())? != input.plan_digest {
        return Err("verifier evidence plan identity is stale or contradictory".into());
    }
    let source = controller
        .bridge
        .control()
        .task_verifier_source(&input.task_ref)
        .ok_or("accepted Task has no frozen generic verifier source")?;
    let spec = match controller
        .orchestrator
        .task_satisfaction_basis(input.task_ref.mission_id(), &input.task_ref)
    {
        Some(domain::TaskSatisfactionBasis::VerifierEvidence(spec)) => spec,
        _ => return Err("Task did not declare independent verifier evidence".into()),
    };
    if source.source_id != input.source_id
        || source.revision != input.source_revision
        || source.tenant_id != input.tenant_id
        || source.verifier != *spec.verifier()
        || spec.predicate() != input.predicate
        || input.valid_for_ms > spec.max_evidence_age_ms()
        || attempts(controller, &input.task_ref)?
            .into_iter()
            .collect::<BTreeSet<_>>()
            != input.attempts.iter().cloned().collect::<BTreeSet<_>>()
    {
        return Err(
            "verifier evidence differs from the accepted source/predicate/current attempts".into(),
        );
    }
    if source.require_artifact
        && input.verdict == SemanticVerdict::Satisfied
        && input.finality == VerifierFinality::Final
        && input.artifact_digest.is_none()
    {
        return Err(
            "affirmative final evidence requires an independently produced artifact".into(),
        );
    }
    if let Some(digest) = &input.artifact_digest {
        controller
            .verifier_artifact_store
            .as_ref()
            .ok_or("verifier CAS is unavailable")?
            .verify_artifact(
                digest,
                input
                    .artifact_size_bytes
                    .ok_or("verifier artifact length is absent")?,
            )
            .map_err(|error| format!("verifier artifact is unavailable/corrupt: {error}"))?;
    }
    Ok(())
}

/// Persists an authenticated producer input and its semantic disposition before exposing the result.
#[allow(clippy::too_many_arguments)]
pub(crate) fn submit(
    controller: &Arc<Mutex<ControllerState>>,
    log: &state::SqliteEventLog,
    gate: &Arc<Mutex<()>>,
    clock: &impl Clock,
    body: &[u8],
    subject: &str,
    tenant: Option<&str>,
) -> Result<serde_json::Value, String> {
    let input: SemanticVerifierEvidence =
        serde_json::from_slice(body).map_err(|error| error.to_string())?;
    if tenant.is_some_and(|tenant| tenant != input.tenant_id) {
        return Err("verifier evidence belongs to another tenant".into());
    }
    let _gate = gate
        .lock()
        .map_err(|_| "application write gate is poisoned")?;
    let mut live = controller
        .lock()
        .map_err(|_| "controller lock is poisoned")?;
    validate_input(&live, &input)?;
    let mut candidate = live.clone();
    let now = clock.now();
    let old_count = candidate
        .bridge
        .control()
        .semantic_verifier_receipts()
        .len();
    let receipt = candidate
        .bridge
        .control_mut()
        .admit_semantic_verifier_evidence(input, subject, now)
        .map_err(|error| error.to_string())?;
    if candidate
        .bridge
        .control()
        .semantic_verifier_receipts()
        .len()
        == old_count
    {
        return Ok(
            serde_json::json!({"schema_version":"roboguide.semantic-verifier-receipt/v0.1","receipt":receipt}),
        );
    }
    log.begin_batch().map_err(|error| error.to_string())?;
    let transition = (|| {
        let correlation =
            domain::CorrelationId::new("semantic-verifier-ingress").expect("fixed correlation");
        let mut events = log.clone();
        events.append(
            now,
            &correlation,
            None,
            domain::EventPayload::SemanticVerifierEvidenceAdmitted {
                receipt: receipt.clone(),
            },
        );
        candidate
            .bridge
            .project_semantic_verifier(&receipt, &correlation)
            .map_err(|error| error.to_string())?;
        apply_pending(&mut candidate, now, &correlation, &mut events)?;
        drive_ready_tasks(&mut candidate, now, &correlation, &mut events)
            .map_err(|error| error.to_string())?;
        monitor_active(&mut candidate, now)?;
        if let Some(error) = log.take_error().map_err(|error| error.to_string())? {
            return Err(error);
        }
        let checkpoint = server_checkpoint_json(&candidate).map_err(|error| error.to_string())?;
        let command = control_command_journal_entry_with_digest(
            format!(
                "verifier:{}:{}",
                receipt.evidence.source_id, receipt.input_digest
            ),
            Some(subject.into()),
            receipt.input_digest.clone(),
            &checkpoint,
            log.current_batch_event_sequence()
                .map_err(|error| error.to_string())?,
        );
        log.save_checkpoint_with_command(SERVER_CHECKPOINT_SCHEMA, &checkpoint, command)
            .and_then(|()| log.commit_batch())
            .map_err(|error| error.to_string())
    })();
    if let Err(error) = transition {
        let rollback = log.rollback_batch();
        return Err(format!(
            "semantic verifier commit failed: {error}; rollback={rollback:?}"
        ));
    }
    *live = candidate;
    if let Err(error) = flush_unisolated_commands(&mut live, clock) {
        eprintln!("verifier committed; durable command delivery deferred: {error}");
    }
    let original = live
        .bridge
        .control()
        .semantic_verifier_receipts()
        .into_iter()
        .find(|item| item.input_digest == receipt.input_digest)
        .ok_or("committed verifier receipt disappeared")?;
    Ok(
        serde_json::json!({"schema_version":"roboguide.semantic-verifier-receipt/v0.1","receipt":original}),
    )
}

/// Records a first nonterminal or late dispute disposition without overwriting Mission truth.
fn incident(
    controller: &mut ControllerState,
    receipt: &SemanticVerifierReceipt,
    reason: &str,
    now: domain::TimestampMs,
    correlation: &domain::CorrelationId,
    events: &mut state::SqliteEventLog,
) -> Result<(), String> {
    controller
        .bridge
        .control_mut()
        .mark_semantic_verifier_receipt(
            &receipt.evidence.source_id,
            &receipt.evidence.evidence_id,
            receipt.applied,
            Some(reason.into()),
        )
        .map_err(|error| error.to_string())?;
    events.append(
        now,
        correlation,
        None,
        domain::EventPayload::SemanticVerifierIncident {
            task_ref: receipt.evidence.task_ref.clone(),
            source_id: receipt.evidence.source_id.clone(),
            evidence_id: receipt.evidence.evidence_id.clone(),
            reason: reason.into(),
            received_at: domain::TimestampMs::new(receipt.received_at_ms),
        },
    );
    Ok(())
}

/// Checks one Task's complete committed role set against an explicit State decision consumer.
pub(crate) fn task_policy_allows(
    controller: &ControllerState,
    task: &domain::TaskRef,
    decision: domain::DecisionKind,
    now: domain::TimestampMs,
) -> bool {
    let Some(execution) = controller.orchestrator.execution(task.mission_id()) else {
        return false;
    };
    let Some(planned) = execution
        .plan()
        .task_graph()
        .tasks()
        .iter()
        .find(|planned| planned.requirement().task_ref() == task)
    else {
        return false;
    };
    let Some(bound) = controller
        .bridge
        .control()
        .group(execution.group_id())
        .and_then(|group| group.task_execution(task))
    else {
        return false;
    };
    let state = controller.bridge.decision_state();
    planned.requirement().roles().iter().all(|role| {
        let Some(assignment) = bound
            .assignments()
            .iter()
            .find(|assignment| assignment.role_id() == role.role_id())
        else {
            return false;
        };
        let operation = planned
            .execution_intent(role.role_id())
            .map(domain::ExecutionIntent::operation);
        controller
            .bridge
            .control()
            .evaluate_decision_gates(&state, assignment.node_id(), role, operation, decision, now)
            .iter()
            .all(|receipt| receipt.condition_passed)
    })
}

/// Retains the exact source interpretation used by a positive semantic authority transition.
pub(crate) fn audit_satisfaction(
    controller: &ControllerState,
    task: &domain::TaskRef,
    now: domain::TimestampMs,
    correlation: &domain::CorrelationId,
    events: &mut state::SqliteEventLog,
) {
    let Some(execution) = controller.orchestrator.execution(task.mission_id()) else {
        return;
    };
    let Some(planned) = execution
        .plan()
        .task_graph()
        .tasks()
        .iter()
        .find(|planned| planned.requirement().task_ref() == task)
    else {
        return;
    };
    let Some(bound) = controller
        .bridge
        .control()
        .group(execution.group_id())
        .and_then(|group| group.task_execution(task))
    else {
        return;
    };
    let state = controller.bridge.decision_state();
    for role in planned.requirement().roles() {
        if let Some(assignment) = bound
            .assignments()
            .iter()
            .find(|assignment| assignment.role_id() == role.role_id())
        {
            controller.bridge.control().audit_role_decision(
                &state,
                task,
                assignment.node_id(),
                role,
                planned
                    .execution_intent(role.role_id())
                    .map(domain::ExecutionIntent::operation),
                domain::DecisionKind::Satisfaction,
                now,
                correlation,
                events,
            );
        }
    }
}

/// Applies only fresh final evidence after local completion; running failures request complete safe stop.
pub(crate) fn apply_pending(
    controller: &mut ControllerState,
    now: domain::TimestampMs,
    correlation: &domain::CorrelationId,
    events: &mut state::SqliteEventLog,
) -> Result<(), String> {
    let mut receipts = controller.bridge.control().semantic_verifier_receipts();
    receipts.sort_by_key(|receipt| (receipt.received_at_ms, receipt.evidence.sequence));
    for receipt in receipts {
        if receipt.applied || receipt.incident.is_some() {
            continue;
        }
        let input = &receipt.evidence;
        let Some(execution) = controller
            .orchestrator
            .execution(input.task_ref.mission_id())
        else {
            continue;
        };
        if matches!(
            execution.lifecycle(),
            orchestration::MissionExecutionLifecycle::Completed
                | orchestration::MissionExecutionLifecycle::Failed
                | orchestration::MissionExecutionLifecycle::Cancelled
        ) {
            incident(
                controller,
                &receipt,
                "late_terminal_report",
                now,
                correlation,
                events,
            )?;
            continue;
        }
        if controller
            .bridge
            .control()
            .group(execution.group_id())
            .and_then(|group| group.task_execution(&input.task_ref))
            .is_some_and(|task| {
                matches!(
                    task.lifecycle(),
                    domain::TaskExecutionLifecycle::Completed
                        | domain::TaskExecutionLifecycle::SatisfactionRejected
                        | domain::TaskExecutionLifecycle::Failed
                )
            })
        {
            incident(
                controller,
                &receipt,
                "late_terminal_task_report",
                now,
                correlation,
                events,
            )?;
            continue;
        }
        if now.as_millis() >= receipt.valid_until_ms {
            incident(
                controller,
                &receipt,
                "expired_before_consumption",
                now,
                correlation,
                events,
            )?;
            continue;
        }
        if validate_input(controller, input).is_err() {
            incident(
                controller,
                &receipt,
                "current_plan_attempt_or_artifact_conflict",
                now,
                correlation,
                events,
            )?;
            continue;
        }
        let source_current = controller
            .bridge
            .control()
            .semantic_verifier_registry()
            .and_then(|registry| registry.sources.get(&input.source_id));
        if source_current
            != controller
                .bridge
                .control()
                .task_verifier_source(&input.task_ref)
        {
            incident(
                controller,
                &receipt,
                "source_revoked_or_revised",
                now,
                correlation,
                events,
            )?;
            continue;
        }
        if input.verdict == SemanticVerdict::Unknown {
            incident(
                controller,
                &receipt,
                "unknown_verdict",
                now,
                correlation,
                events,
            )?;
            continue;
        }
        if input.finality == VerifierFinality::Provisional {
            continue;
        }
        let group = execution.group_id().clone();
        let awaiting = controller
            .bridge
            .control()
            .group(&group)
            .and_then(|group| group.task_execution(&input.task_ref))
            .is_some_and(|task| {
                task.lifecycle() == domain::TaskExecutionLifecycle::AwaitingSatisfaction
            });
        if !awaiting
            || execution.isolation().is_some()
            || safety_stop::blocks_release(controller, input.task_ref.mission_id())
        {
            continue;
        }
        if input.verdict == SemanticVerdict::Unsatisfied
            && safety_stop::has_live_group_attempts(controller, &group)
        {
            let timeout = controller
                .bridge
                .control()
                .task_verifier_source(&input.task_ref)
                .expect("frozen source validated")
                .stop_timeout_ms;
            safety_stop::admit_group_stop(controller, &group, now, timeout)?;
            continue;
        }
        if input.verdict == SemanticVerdict::Satisfied {
            if !task_policy_allows(
                controller,
                &input.task_ref,
                domain::DecisionKind::Satisfaction,
                now,
            ) {
                continue;
            }
            audit_satisfaction(controller, &input.task_ref, now, correlation, events);
        }
        let source = controller
            .bridge
            .control()
            .task_verifier_source(&input.task_ref)
            .expect("frozen source validated");
        let evidence = domain::TaskSatisfactionEvidence::new(
            input.task_ref.clone(),
            source.verifier.clone(),
            input.predicate.clone(),
            domain::StateSource::RoboGuide {
                component: format!("verifier/{}/{}", input.tenant_id, input.source_id),
            },
            domain::TimestampMs::new(input.source_observed_at_ms),
            domain::TimestampMs::new(receipt.received_at_ms),
            input.verdict == SemanticVerdict::Satisfied,
        )
        .map_err(|error| error.to_string())?;
        controller
            .bridge
            .record_task_satisfaction_evidence(evidence.clone())
            .map_err(|error| error.to_string())?;
        if input.verdict == SemanticVerdict::Satisfied {
            controller
                .orchestrator
                .satisfy_task_from_verifier(
                    input.task_ref.mission_id(),
                    &evidence,
                    controller.bridge.control_mut(),
                    now,
                    correlation,
                    events,
                )
                .map_err(|error| error.to_string())?;
        } else {
            controller
                .orchestrator
                .reject_task_satisfaction_from_verifier(
                    input.task_ref.mission_id(),
                    &evidence,
                    controller.bridge.control_mut(),
                    now,
                    correlation,
                    events,
                )
                .map_err(|error| error.to_string())?;
        }
        controller
            .bridge
            .control_mut()
            .mark_semantic_verifier_receipt(&input.source_id, &input.evidence_id, true, None)
            .map_err(|error| error.to_string())?;
        close_terminal_mission_coordination(controller, input.task_ref.mission_id());
    }
    check_continuous(controller, now)
}

/// Installs nonrenewable monitoring for newly prepared complete current attempts inside the transaction.
pub(crate) fn monitor_active(
    controller: &mut ControllerState,
    now: domain::TimestampMs,
) -> Result<(), String> {
    let mut installs = Vec::new();
    for mission in controller.orchestrator.mission_ids() {
        let execution = controller
            .orchestrator
            .execution(&mission)
            .ok_or("Mission disappeared")?;
        for planned in execution.plan().task_graph().tasks() {
            let task = planned.requirement().task_ref();
            if controller
                .bridge
                .control()
                .task_verifier_source(task)
                .is_some_and(|source| source.continuous)
                && let Ok(attempts) = attempts(controller, task)
            {
                installs.push((
                    task.clone(),
                    super::tenant::source_digest(controller, &mission)?,
                    attempts,
                ));
            }
        }
    }
    for (task, digest, attempts) in installs {
        controller
            .bridge
            .control_mut()
            .monitor_semantic_verifier(task, digest, attempts, now)
            .map_err(|error| error.to_string())?;
    }
    Ok(())
}

/// Stops current nonterminal attempts when their frozen continuous authority is silent, stale or negative.
fn check_continuous(
    controller: &mut ControllerState,
    now: domain::TimestampMs,
) -> Result<(), String> {
    let receipts = controller.bridge.control().semantic_verifier_receipts();
    let watches = controller.bridge.control().semantic_verifier_watches();
    for watch in watches {
        let Some(execution) = controller
            .orchestrator
            .execution(watch.task_ref.mission_id())
        else {
            continue;
        };
        if execution.isolation().is_some()
            || matches!(
                execution.lifecycle(),
                orchestration::MissionExecutionLifecycle::Completed
                    | orchestration::MissionExecutionLifecycle::Failed
                    | orchestration::MissionExecutionLifecycle::Cancelled
                    | orchestration::MissionExecutionLifecycle::Cancelling
            )
        {
            continue;
        }
        if attempts(controller, &watch.task_ref).map_or(true, |current| current != watch.attempts) {
            continue;
        }
        let group = execution.group_id().clone();
        if !watch.attempts.iter().any(|attempt| {
            controller
                .bridge
                .execution_status(&attempt.attempt_id)
                .is_some_and(|status| {
                    !matches!(
                        status,
                        orchestration::RemoteExecutionStatus::Completed
                            | orchestration::RemoteExecutionStatus::Failed
                            | orchestration::RemoteExecutionStatus::Cancelled
                    )
                })
        }) {
            continue;
        }
        let source_current = controller
            .bridge
            .control()
            .semantic_verifier_registry()
            .and_then(|registry| registry.sources.get(&watch.source.source_id));
        let latest = receipts
            .iter()
            .filter(|receipt| {
                receipt.evidence.task_ref == watch.task_ref
                    && receipt.evidence.source_id == watch.source.source_id
                    && receipt.evidence.source_revision == watch.source.revision
                    && receipt.evidence.plan_digest == watch.plan_digest
                    && receipt
                        .evidence
                        .attempts
                        .iter()
                        .cloned()
                        .collect::<BTreeSet<_>>()
                        == watch.attempts.iter().cloned().collect()
            })
            .max_by_key(|receipt| receipt.evidence.sequence);
        let failed = source_current != Some(&watch.source)
            || latest.map_or(now.as_millis() >= watch.initial_deadline_ms, |receipt| {
                receipt.evidence.verdict != SemanticVerdict::Satisfied
                    || now.as_millis() >= receipt.valid_until_ms
            });
        if failed {
            safety_stop::admit_group_stop(controller, &group, now, watch.source.stop_timeout_ms)?;
        }
    }
    Ok(())
}

/// Refuses unmonitored or revoked continuous execution before outbox delivery without renewing a fence.
pub(crate) fn delivery_permitted(
    controller: &ControllerState,
    group: &domain::ExecutionGroupId,
) -> bool {
    let watches = controller.bridge.control().semantic_verifier_watches();
    let Some(group) = controller.bridge.control().group(group) else {
        return false;
    };
    group.task_executions().all(|task| {
        let Some(source) = controller
            .bridge
            .control()
            .task_verifier_source(task.task_ref())
            .filter(|source| source.continuous)
        else {
            return true;
        };
        let Ok(attempts) = attempts(controller, task.task_ref()) else {
            return true;
        };
        controller
            .bridge
            .control()
            .semantic_verifier_registry()
            .and_then(|registry| registry.sources.get(&source.source_id))
            == Some(source)
            && watches.iter().any(|watch| {
                watch.task_ref == *task.task_ref()
                    && watch.source == *source
                    && watch.attempts == attempts
            })
    })
}
