//! Independent original-goal ingress, nonrenewable receive budgets and current completion gates.

use super::*;
use ports::EventSink as _;

/// Original complete-request receive clocks, captured before authorization, writer queues or CAS reads.
pub(crate) struct GoalIngress {
    /// Actual Controller receive time, independent of the producer's clock.
    received_at: domain::TimestampMs,
    /// Actual original same-kernel receive clock; no duplicate input can replace it.
    kernel: KernelTime,
}

/// Captures the original complete-request boundary without granting any source or completion authority.
pub(crate) fn observe_goal_ingress(
    received_at: domain::TimestampMs,
) -> Result<GoalIngress, String> {
    Ok(GoalIngress {
        received_at,
        kernel: KernelTime::observe()?,
    })
}

/// Reads the complete actual current attempt set; inherited completed Tasks acquire no invented attempts.
fn attempts(
    controller: &ControllerState,
    group: &domain::ExecutionGroupId,
) -> BTreeSet<domain::MissionGoalAttempt> {
    controller
        .bridge
        .attempt_history()
        .into_iter()
        .filter(|attempt| {
            let command = attempt.command();
            command.group_id() == group
                && controller.bridge.current_task_attempt_id(
                    group,
                    command.task_ref(),
                    command.role_id(),
                ) == Some(attempt.execution_id())
        })
        .map(|attempt| domain::MissionGoalAttempt {
            task_ref: attempt.command().task_ref().clone(),
            role_id: attempt.command().role_id().clone(),
            attempt_id: attempt.execution_id().into(),
        })
        .collect()
}

/// Confirms current plan and complete actual attempt coverage without interpreting local completion as success.
fn current(controller: &ControllerState, input: &domain::MissionGoalEvidence) -> bool {
    controller
        .orchestrator
        .execution(&input.mission_id)
        .is_some_and(|execution| {
            input.plan_revision == execution.plan_revision()
                && &input.group_id == execution.group_id()
                && input.plan_digest == orchestration::mission_plan_digest(execution.plan())
                && input.attempts.iter().cloned().collect::<BTreeSet<_>>()
                    == attempts(controller, execution.group_id())
        })
}

/// Checks immutable current scope and independently hashes complete optional/required proof bytes.
fn validate_input(
    controller: &ControllerState,
    input: &domain::MissionGoalEvidence,
) -> Result<(), String> {
    let execution = controller
        .orchestrator
        .execution(&input.mission_id)
        .ok_or("goal Mission absent")?;
    let known = if input.plan_revision == execution.plan_revision() {
        current(controller, input)
    } else {
        usize::try_from(input.plan_revision)
            .ok()
            .and_then(|index| execution.plan_revisions().get(index))
            .is_some_and(|version| {
                version.source_group_id == input.group_id
                    && orchestration::decode_mission_plan(&version.source_plan.to_string())
                        .is_ok_and(|plan| {
                            orchestration::mission_plan_digest(&plan) == input.plan_digest
                        })
                    && input.attempts.iter().cloned().collect::<BTreeSet<_>>()
                        == attempts(controller, &input.group_id)
            })
    };
    if !known {
        return Err(
            "goal input differs from actual current or retained original plan/attempt coverage"
                .into(),
        );
    }
    if let Some(digest) = &input.artifact_digest {
        controller
            .verifier_artifact_store
            .as_ref()
            .ok_or("goal verifier CAS is unavailable")?
            .verify_artifact(
                digest,
                input
                    .artifact_size_bytes
                    .ok_or("goal proof length is absent")?,
            )
            .map_err(|e| format!("independent goal proof bytes are unavailable/corrupt: {e}"))?;
    }
    Ok(())
}

/// Persists original independently authenticated measurements and application timing before completion.
#[allow(clippy::too_many_arguments)]
pub(crate) fn submit_goal(
    controller: &Arc<Mutex<ControllerState>>,
    log: &state::SqliteEventLog,
    gate: &dyn crate::application::ApplicationWriteGate,
    clock: &impl Clock,
    body: &[u8],
    caller: &Caller<'_>,
    ingress: GoalIngress,
) -> Result<serde_json::Value, String> {
    let input: domain::MissionGoalEvidence =
        serde_json::from_slice(body).map_err(|e| e.to_string())?;
    let subject = caller.principal;
    if caller.tenant != "*" && caller.tenant != input.tenant_id {
        return Err("original goal evidence belongs to another tenant".into());
    }
    let _writer = gate.lock()?;
    let mut candidate = controller
        .lock()
        .map_err(|_| "Controller authority unavailable")?
        .clone();
    let state = candidate
        .runtime_revisions
        .get(input.mission_id.as_str())
        .ok_or("Mission has no original independent goal authority")?
        .clone();
    admission::refresh_authority(&mut candidate, &state)?;
    let now = clock.now();
    if ingress.received_at < state.accepted_at {
        return Err("goal request predates the original frozen interpretation".into());
    }
    if !admission::authorized_caller(&candidate, caller, now) {
        return Err("goal producer no longer has current original tenant delegation".into());
    }
    let input_digest = digest(&input)?;
    if let Some(original) = candidate
        .bridge
        .control()
        .mission_goal_receipt(&input_digest)
    {
        if original.authority_subject != subject {
            return Err("original goal receipt belongs to another principal".into());
        }
        return Ok(
            serde_json::json!({"schema_version":"roboguide.mission-goal-receipt/v0.1","receipt":original}),
        );
    }
    // This budget starts at complete request receive, never after the writer queue or hashing.
    let budget = OriginalBudget::new(&ingress.kernel, input.valid_for_ms)?;
    let receipt = candidate
        .bridge
        .control_mut()
        .admit_mission_goal_evidence(
            input,
            subject,
            caller.certificate_digest,
            ingress.received_at,
        )
        .map_err(|e| e.to_string())?;
    // Source/principal/shape checks precede expensive proof reads on this private candidate.
    validate_input(&candidate, &receipt.evidence)?;
    let late = !current(&candidate, &receipt.evidence);
    let key = receipt.evidence.mission_id.as_str().to_string();
    candidate
        .runtime_revisions
        .get_mut(&key)
        .expect("original goal authority")
        .goal_evidence
        .insert(
            receipt.input_digest.clone(),
            GoalProgress {
                budget,
                completed_time: None,
                incident: late.then(|| {
                    "retained old-version goal evidence; no current completion authority".into()
                }),
            },
        );
    log.begin_batch().map_err(|e| e.to_string())?;
    let transaction: Result<(), String> = (|| {
        let trace = domain::CorrelationId::new("mission-goal-ingress").expect("fixed trace");
        let mut events = log.clone();
        events.append(
            now,
            &trace,
            None,
            domain::EventPayload::MissionGoalEvidenceAdmitted {
                receipt: receipt.clone(),
            },
        );
        apply_pending(&mut candidate, clock)?;
        if let Some(error) = log.take_error().map_err(|e| e.to_string())? {
            return Err(error);
        }
        let checkpoint = server_checkpoint_json(&candidate).map_err(|e| e.to_string())?;
        let journal = control_command_journal_entry_with_digest(
            format!(
                "mission-goal:{}:{}",
                receipt.evidence.source_id, receipt.input_digest
            ),
            Some(subject.into()),
            receipt.input_digest.clone(),
            &checkpoint,
            log.current_batch_event_sequence()
                .map_err(|e| e.to_string())?,
        );
        log.save_checkpoint_with_command(SERVER_CHECKPOINT_SCHEMA, &checkpoint, journal)
            .and_then(|()| log.commit_batch())
            .map_err(|e| e.to_string())
    })();
    if let Err(error) = transaction {
        let rollback = log.rollback_batch();
        return Err(format!(
            "goal ingress transaction unavailable/rejected: {error}; rollback={rollback:?}"
        ));
    }
    let mut live = controller
        .lock()
        .map_err(|_| "Controller unavailable after goal Commit")?;
    *live = candidate;
    if let Err(error) = flush_unisolated_commands(&mut live, clock) {
        eprintln!("goal evidence committed; original outbox deferred: {error}");
    }
    Ok(
        serde_json::json!({"schema_version":"roboguide.mission-goal-receipt/v0.1","receipt":receipt}),
    )
}

/// Applies only latest current, fresh, independently authorized goal evidence after actual Task satisfaction.
pub(crate) fn apply_pending(
    controller: &mut ControllerState,
    clock: &impl Clock,
) -> Result<(), String> {
    if controller.runtime_revisions.is_empty() {
        return Ok(());
    }
    let missions: Vec<_> = controller.runtime_revisions.keys().cloned().collect();
    for key in missions {
        let mission = domain::MissionId::new(&key).map_err(|e| e.to_string())?;
        if controller.runtime_revisions[&key].goal_evidence.is_empty()
            || controller
                .orchestrator
                .execution(&mission)
                .is_none_or(|execution| {
                    execution.lifecycle() != orchestration::MissionExecutionLifecycle::Running
                        || execution.isolation().is_some()
                })
        {
            continue;
        }
        let state = controller.runtime_revisions[&key].clone();
        // Goal constraints survive replan-policy expiry/revocation. Their own source and receive budgets govern.
        if admission::refresh_authority(controller, &state).is_err() {
            continue;
        }
        let Ok(kernel) = KernelTime::observe() else {
            continue;
        };
        let now = clock.now();
        let Some(execution) = controller.orchestrator.execution(&mission) else {
            continue;
        };
        if execution.lifecycle() != orchestration::MissionExecutionLifecycle::Running
            || execution.isolation().is_some()
            || !controller
                .bridge
                .control()
                .mission_goal_source_current(&mission)
        {
            continue;
        }
        let Some(receipt) = controller
            .bridge
            .control()
            .mission_goal_receipts(&mission)
            .into_iter()
            .filter(|receipt| current(controller, &receipt.evidence))
            .max_by_key(|receipt| receipt.evidence.sequence)
        else {
            continue;
        };
        let Some(progress) = state.goal_evidence.get(&receipt.input_digest) else {
            return Err("goal source lacks original receive budget".into());
        };
        if progress.budget.remaining(&kernel) == 0
            || !admission::authorized_subject(
                controller,
                &receipt.authority_subject,
                &state.tenant,
                false,
                now,
            )
        {
            continue;
        }
        if receipt.evidence.finality == domain::VerifierFinality::Final
            && receipt.evidence.verdict == domain::SemanticVerdict::Unsatisfied
            && admission::completed(controller, &mission)?.len()
                == execution.plan().task_graph().tasks().len()
            && progress.incident.is_none()
        {
            let trace = domain::CorrelationId::new("mission-goal-rejected").expect("fixed trace");
            if !switching::observe_trigger(
                controller,
                &mission,
                Trigger::SatisfactionRejected,
                now,
            )? {
                controller
                    .orchestrator
                    .hold_for_safety(
                        &mission,
                        now,
                        &trace,
                        &mut controller.bridge.event_sink().clone(),
                    )
                    .map_err(|e| e.to_string())?;
                let group = controller
                    .orchestrator
                    .execution(&mission)
                    .expect("original Mission")
                    .group_id()
                    .clone();
                if controller
                    .bridge
                    .control()
                    .group(&group)
                    .is_some_and(|group| group.lifecycle() != control::GroupLifecycle::Blocked)
                {
                    let mut events = controller.bridge.event_sink().clone();
                    controller
                        .bridge
                        .control_mut()
                        .block_group(
                            &group,
                            "independent original Mission goal rejected",
                            now,
                            &trace,
                            &mut events,
                        )
                        .map_err(|e| e.to_string())?;
                }
            }
            controller.runtime_revisions.get_mut(&key).expect("original goal authority")
                .goal_evidence.get_mut(&receipt.input_digest).expect("original receive budget").incident=
                Some("final independent original goal rejection; ownership held or bounded reviewed revision requested".into());
            continue;
        }
        let trace = domain::CorrelationId::new("mission-goal-completion").expect("fixed trace");
        let mut events = controller.bridge.event_sink().clone();
        let completed = controller
            .orchestrator
            .complete_mission_with_goal(
                &mission,
                &receipt.input_digest,
                controller.bridge.control_mut(),
                now,
                &trace,
                &mut events,
            )
            .map_err(|e| e.to_string())?;
        if completed {
            super::super::close_terminal_mission_coordination(controller, &mission);
            controller
                .runtime_revisions
                .get_mut(&key)
                .expect("original goal authority")
                .goal_evidence
                .get_mut(&receipt.input_digest)
                .expect("original receive budget")
                .completed_time = Some(kernel.clone());
        }
    }
    Ok(())
}

/// Rechecks complete source receipts and original decision clocks without renewing any receive budget.
pub(super) fn validate_restored(
    controller: &ControllerState,
    mission: &domain::MissionId,
    state: &RevisionState,
) -> Result<(), String> {
    let contract = controller
        .bridge
        .control()
        .mission_goal_contract(mission)
        .ok_or("revision lacks original goal contract")?;
    if state.goal_contract_digest != contract.digest
        || contract.context_digest != digest(&state.policy.goal_context)?
        || contract.spec != state.policy.goal_verifier
        || contract
            .source
            .producer_principals
            .contains(&state.authority)
        || contract
            .source
            .producer_principals
            .contains(&state.policy.proposer)
    {
        return Err("revision changed its independently frozen original goal/source".into());
    }
    let receipts = controller.bridge.control().mission_goal_receipts(mission);
    if receipts.len() != state.goal_evidence.len() {
        return Err("goal receive-budget coverage is incomplete".into());
    }
    let execution = controller
        .orchestrator
        .execution(mission)
        .ok_or("goal Mission absent")?;
    if execution.goal_receipt_digest().is_some_and(|digest| {
        state
            .goal_evidence
            .get(digest)
            .is_none_or(|progress| progress.completed_time.is_none())
    }) {
        return Err("completed goal lost its original kernel decision evidence".into());
    }
    for receipt in receipts {
        let progress = state
            .goal_evidence
            .get(&receipt.input_digest)
            .ok_or("goal evidence original budget is missing")?;
        progress.budget.validate()?;
        if progress.budget.duration_ms() != receipt.evidence.valid_for_ms {
            return Err("goal original receive budget changed".into());
        }
        if let Some(completed) = &progress.completed_time {
            let execution = controller
                .orchestrator
                .execution(mission)
                .ok_or("goal Mission absent")?;
            if progress.budget.remaining(completed) == 0
                || execution.lifecycle() != orchestration::MissionExecutionLifecycle::Completed
                || execution.goal_receipt_digest() != Some(receipt.input_digest.as_str())
            {
                return Err(
                    "goal completion lacks original current receive-budget authority".into(),
                );
            }
        }
    }
    Ok(())
}
