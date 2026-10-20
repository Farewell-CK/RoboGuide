//! Original whole-Group stop/revoke barriers; candidate permission never selects executors.

use super::*;

/// Freezes only actual current Runtime attempts; absent attempts never become fabricated stop facts.
fn attempts(
    controller: &ControllerState,
    group: &domain::ExecutionGroupId,
) -> Result<Vec<FrozenAttempt>, String> {
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
        .map(|attempt| {
            let command = attempt.command();
            Ok(FrozenAttempt {
                execution_id: attempt.execution_id().into(),
                generation: controller
                    .bridge
                    .current_attempt_generation(group, command.task_ref(), command.role_id())
                    .ok_or("original attempt generation unavailable")?,
                task_ref: command.task_ref().clone(),
                role_id: command.role_id().clone(),
                node_id: command.node_id().clone(),
                session_id: controller
                    .bridge
                    .current_node_session(command.node_id())
                    .map(str::to_string),
            })
        })
        .collect()
}

/// Confirms the exact current candidate, completion precondition and actual current review delegation.
fn reviewed_plan(
    controller: &ControllerState,
    mission: &domain::MissionId,
    state: &RevisionState,
    ticket: &str,
    now: domain::TimestampMs,
    deployment: Option<&DeploymentFeasibility>,
    feed: Option<&TaskVerifierFeed>,
) -> Result<domain::MissionPlan, String> {
    let deployment = deployment.or(state.deployment.as_deref());
    if deployment.map(|profile| profile.digest()) != state.deployment_digest.as_deref() {
        return Err("original runtime revision deployment evidence changed".into());
    }
    let candidate = state
        .candidates
        .get(ticket)
        .ok_or("unknown switch candidate")?;
    let execution = controller
        .orchestrator
        .execution(mission)
        .ok_or("unknown switch Mission")?;
    let review = candidate
        .review
        .as_ref()
        .ok_or("candidate has no original administrative Review")?;
    if candidate.rejected.is_some()
        || review.decision != ReviewDecision::Approve
        || review.plan_digest != candidate.plan_digest.clone().unwrap_or_default()
        || candidate.base_revision != execution.plan_revision()
        || candidate.base_digest != orchestration::mission_plan_digest(execution.plan())
        || review.completion_precondition != admission::completed(controller, mission)?
        || review.memory_digest != memory::authorization_digest(candidate)?
        || now.as_millis() >= review.certificate_expires_at_ms
        || !admission::authorized_subject(controller, &review.principal, &state.tenant, true, now)
    {
        return Err(
            "candidate, original completion precondition or current Review authority changed"
                .into(),
        );
    }
    let plan = orchestration::decode_mission_plan(
        &candidate
            .plan
            .as_ref()
            .ok_or("original candidate unavailable")?
            .to_string(),
    )
    .map_err(|e| e.to_string())?;
    if orchestration::mission_plan_digest(&plan) != review.plan_digest {
        return Err("reviewed candidate digest differs from original bytes".into());
    }
    controller
        .orchestrator
        .validate_plan_revision(mission, &plan, controller.bridge.control())
        .map_err(|e| e.to_string())?;
    super::super::semantic_verifier::validate_revision_plan(
        controller,
        &plan,
        &state.tenant,
        feed,
        &review.completion_precondition,
    )?;
    if let Some(deployment) = deployment {
        let group = domain::ExecutionGroupId::new(format!("revision-check-{mission}"))
            .map_err(|e| e.to_string())?;
        deployment.restrictions_for_plan(&plan, &group)?;
    }
    Ok(plan)
}

/// Persists a hold and complete original stop intent before any outbox can reach a Node.
#[allow(clippy::too_many_arguments)]
pub(super) fn begin(
    controller: &mut ControllerState,
    mission: &domain::MissionId,
    state: &mut RevisionState,
    ticket: &str,
    target_group: domain::ExecutionGroupId,
    command_id: &str,
    trigger: Option<Trigger>,
    automatic: bool,
    now: domain::TimestampMs,
    kernel: &KernelTime,
    deployment: Option<&DeploymentFeasibility>,
    feed: Option<&TaskVerifierFeed>,
) -> Result<serde_json::Value, String> {
    if !admission::active(controller, state, now, kernel)
        || state.switches.len() >= state.policy.switch_limit
    {
        return Err("original switch policy permission/budget exhausted".into());
    }
    let generation = automatic
        && trigger.is_some()
        && state
            .candidates
            .get(ticket)
            .is_some_and(|candidate| candidate.review.is_none());
    let plan = if generation {
        if !generation::active(controller, state, now, kernel) {
            return Err("original generation delegation is unavailable".into());
        }
        None
    } else {
        Some(reviewed_plan(
            controller, mission, state, ticket, now, deployment, feed,
        )?)
    };
    let execution = controller
        .orchestrator
        .execution(mission)
        .ok_or("unknown switch Mission")?;
    let source_group = execution.group_id().clone();
    let source_revision = execution.plan_revision();
    if controller.bridge.control().group(&target_group).is_some()
        || target_group == source_group
        || state
            .switches
            .iter()
            .any(|switch| switch.source_group == source_group)
        || super::super::memory_handoff::holds_binding(controller, &source_group)
    {
        return Err(
            "original source already owns a switch/handoff or target identity was used".into(),
        );
    }
    if let Some(plan) = &plan {
        memory::validate_inputs(
            controller,
            mission,
            state,
            &state.candidates[ticket],
            plan,
            &target_group,
            &admission::completed(controller, mission)?,
            now,
            kernel,
        )?;
    }
    let group = controller
        .bridge
        .control()
        .group(&source_group)
        .ok_or("source Group absent")?;
    let membership = group
        .membership_version()
        .cloned()
        .ok_or("source Group has no original membership authority")?;
    for task in group.task_executions() {
        for role in task.context_roles().keys() {
            if controller
                .bridge
                .control()
                .pending_recovery_commitment_for_task(&source_group, task.task_ref(), role)
                .is_some()
            {
                return Err("source retains a committed recovery assignment".into());
            }
        }
    }
    let candidate = &state.candidates[ticket];
    let review_remaining = if generation {
        candidate.budget.remaining(kernel).min(
            state
                .generation
                .as_ref()
                .expect("explicit generation delegation")
                .certificate_expires_at_ms
                .saturating_sub(now.as_millis()),
        )
    } else {
        candidate
            .review
            .as_ref()
            .expect("reviewed candidate")
            .certificate_expires_at_ms
            .saturating_sub(now.as_millis())
    };
    let duration = state
        .budget
        .remaining(kernel)
        .min(state.policy.switch_timeout_ms)
        .min(review_remaining);
    let current_attempts = attempts(controller, &source_group)?;
    let current_ids: BTreeSet<_> = current_attempts
        .iter()
        .map(|attempt| attempt.execution_id.as_str())
        .collect();
    let prior_attempts: BTreeSet<_> = controller
        .bridge
        .attempt_history()
        .into_iter()
        .filter(|attempt| {
            attempt.command().group_id() == &source_group
                && !current_ids.contains(attempt.execution_id())
        })
        .map(|attempt| attempt.execution_id().to_string())
        .collect();
    for id in &prior_attempts {
        if !super::super::safety_stop::attempt_confirmed(controller, id)
            || super::super::memory_use::blocks_attempt_release(controller, id)
            || controller
                .bridge
                .attempt_history()
                .iter()
                .find(|attempt| attempt.execution_id() == id)
                .is_none_or(|attempt| !attempt.status().is_terminal())
        {
            return Err(
                "older source attempt still lacks original terminal/stop/revoke proof".into(),
            );
        }
    }
    let mut switch = Switch {
        generation,
        command_id: command_id.into(),
        ticket_id: ticket.into(),
        source_revision,
        source_group: source_group.clone(),
        target_group,
        membership,
        attempts: current_attempts,
        prior_attempts,
        budget: OriginalBudget::new(kernel, duration)?,
        requested_at: now,
        trigger,
        automatic,
        phase: Phase::Stopping,
        reason: None,
    };
    let trace = domain::CorrelationId::new("runtime-plan-revision-stop").expect("fixed trace");
    controller
        .orchestrator
        .hold_for_safety(
            mission,
            now,
            &trace,
            &mut controller.bridge.event_sink().clone(),
        )
        .map_err(|e| e.to_string())?;
    if controller
        .bridge
        .control()
        .group(&source_group)
        .is_some_and(|group| group.lifecycle() != control::GroupLifecycle::Blocked)
    {
        let mut events = controller.bridge.event_sink().clone();
        controller
            .bridge
            .control_mut()
            .block_group(
                &source_group,
                "runtime plan revision original hold",
                now,
                &trace,
                &mut events,
            )
            .map_err(|e| e.to_string())?;
    }
    // A partial stop-admission error cannot publish part of the intended Runtime/SQL evidence.
    let mut stopped = controller.clone();
    let events = controller.bridge.event_sink().clone();
    let admitted = events
        .with_transition_savepoint(|| {
            for attempt in &switch.attempts {
                super::super::safety_stop::admit(
                    &mut stopped,
                    &source_group,
                    &attempt.task_ref,
                    &attempt.role_id,
                    now,
                    duration,
                )?;
            }
            super::super::memory_use::revoke_group(
                &mut stopped,
                &source_group,
                "runtime plan revision",
                now,
            )?;
            Ok::<(), String>(())
        })
        .map_err(|e| e.to_string())?;
    match admitted {
        Ok(()) => *controller = stopped,
        Err(error) => {
            switch.phase = Phase::Manual;
            switch.reason = Some(error.chars().take(1024).collect());
        }
    }
    let response = serde_json::json!({"status":"revision_switch_requested","switch_command_id":command_id,"source_group_id":switch.source_group.as_str(),"target_group_id":switch.target_group.as_str(),"phase":switch.phase,"command_receipt_is_stop_proof":false});
    state.switches.push(switch);
    Ok(response)
}

/// Checks actual original attempts, current ownership and independent stop/revoke facts, without renewal.
fn barriers(
    controller: &ControllerState,
    mission: &domain::MissionId,
    state: &RevisionState,
    switch: &Switch,
    now: domain::TimestampMs,
    kernel: &KernelTime,
) -> Result<bool, String> {
    if !admission::active(controller, state, now, kernel) || switch.budget.remaining(kernel) == 0 {
        return Err("original switch policy, time domain or stop/revoke budget expired".into());
    }
    if switch.generation
        && (!generation::active(controller, state, now, kernel)
            || state.candidates[&switch.ticket_id].budget.remaining(kernel) == 0)
    {
        return Err("original generation permission or work budget expired".into());
    }
    let execution = controller
        .orchestrator
        .execution(mission)
        .ok_or("source Mission absent")?;
    let group = controller
        .bridge
        .control()
        .group(&switch.source_group)
        .ok_or("source Group absent")?;
    if execution.plan_revision() != switch.source_revision
        || execution.group_id() != &switch.source_group
        || execution.isolation().is_none()
        || group.lifecycle() != control::GroupLifecycle::Blocked
        || group.membership_version() != Some(&switch.membership)
        || super::super::memory_handoff::holds_binding(controller, &switch.source_group)
    {
        return Err("original switch ownership, membership or hold changed".into());
    }
    let history = controller.bridge.attempt_history();
    let frozen: BTreeSet<_> = switch
        .attempts
        .iter()
        .map(|attempt| attempt.execution_id.as_str())
        .collect();
    let current: BTreeSet<_> = history
        .iter()
        .filter(|attempt| {
            attempt.command().group_id() == &switch.source_group
                && controller.bridge.current_task_attempt_id(
                    &switch.source_group,
                    attempt.command().task_ref(),
                    attempt.command().role_id(),
                ) == Some(attempt.execution_id())
        })
        .map(|attempt| attempt.execution_id())
        .collect();
    if frozen != current {
        return Err("original complete attempt coverage changed".into());
    }
    for original in &switch.attempts {
        let actual = history
            .iter()
            .find(|attempt| attempt.execution_id() == original.execution_id)
            .ok_or("original attempt history absent")?;
        if actual.command().task_ref() != &original.task_ref
            || actual.command().role_id() != &original.role_id
            || actual.command().node_id() != &original.node_id
            || controller.bridge.current_attempt_generation(
                &switch.source_group,
                &original.task_ref,
                &original.role_id,
            ) != Some(original.generation)
        {
            return Err("original attempt generation or owner changed".into());
        }
        if !super::super::safety_stop::attempt_confirmed(controller, &original.execution_id) {
            if original.session_id.as_deref()
                != controller.bridge.current_node_session(&original.node_id)
            {
                return Err("unconfirmed original source session changed".into());
            }
            return Ok(false);
        }
        if !actual.status().is_terminal() {
            return Ok(false);
        }
    }
    Ok(
        !super::super::safety_stop::blocks_release(controller, mission)
            && !super::super::memory_use::blocks_release(controller, mission),
    )
}

/// Applies only the original accepted candidate after the exact complete source barriers are proven.
#[allow(clippy::too_many_arguments)]
pub(super) fn commit(
    controller: &mut ControllerState,
    mission: &domain::MissionId,
    state: &mut RevisionState,
    command_id: &str,
    now: domain::TimestampMs,
    _admitted_kernel: &KernelTime,
    deployment: Option<&DeploymentFeasibility>,
    feed: Option<&TaskVerifierFeed>,
) -> Result<serde_json::Value, String> {
    let decision_kernel = KernelTime::observe()?;
    let kernel = &decision_kernel;
    let source_deployment = state.deployment.clone();
    let deployment = deployment.or(source_deployment.as_deref());
    let index = state
        .switches
        .iter()
        .position(|switch| switch.command_id == command_id)
        .ok_or("unknown original switch")?;
    let switch = state.switches[index].clone();
    if switch.phase == Phase::Applied {
        return Ok(
            serde_json::json!({"status":"revision_applied","switch_command_id":command_id,"target_group_id":switch.target_group.as_str()}),
        );
    }
    if switch.phase == Phase::Manual || !barriers(controller, mission, state, &switch, now, kernel)?
    {
        return Err("original stop/revoke barriers are pending or require manual evidence".into());
    }
    let plan = reviewed_plan(
        controller,
        mission,
        state,
        &switch.ticket_id,
        now,
        deployment,
        feed,
    )?;
    memory::validate_inputs(
        controller,
        mission,
        state,
        &state.candidates[&switch.ticket_id],
        &plan,
        &switch.target_group,
        &admission::completed(controller, mission)?,
        now,
        kernel,
    )?;
    memory::publish(
        controller,
        mission,
        state,
        &state.candidates[&switch.ticket_id],
        &switch,
        now,
    )?;
    let trace = domain::CorrelationId::new("runtime-plan-revision-commit").expect("fixed trace");
    let review = &state.candidates[&switch.ticket_id]
        .review
        .as_ref()
        .expect("reviewed candidate")
        .digest;
    let version = {
        let ControllerState {
            orchestrator,
            bridge,
            ..
        } = controller;
        let mut events = bridge.event_sink().clone();
        orchestrator
            .commit_plan_revision(
                mission,
                plan.clone(),
                switch.target_group.clone(),
                command_id,
                review,
                bridge.control_mut(),
                now,
                &trace,
                &mut events,
            )
            .map_err(|e| e.to_string())?
    };
    if super::super::semantic_verifier::validate_revision_plan(
        controller,
        &plan,
        &state.tenant,
        feed,
        &version.completed_tasks,
    )? {
        controller
            .bridge
            .control_mut()
            .freeze_revision_verifier_sources(&switch.source_group, &plan, &state.tenant)
            .map_err(|e| e.to_string())?;
    }
    if let Some(deployment) = deployment {
        for (actor, nodes) in deployment.restrictions_for_plan(&plan, &switch.target_group)? {
            controller
                .bridge
                .control_mut()
                .set_actor_candidate_restriction(
                    mission.clone(),
                    actor,
                    nodes,
                    deployment.digest().into(),
                )
                .map_err(|e| e.to_string())?;
        }
    }
    controller
        .bridge
        .register_execution_relations(&plan, &switch.target_group, now, &trace)
        .map_err(|e| e.to_string())?;
    state.switches[index].phase = Phase::Applied;
    super::super::drive_ready_tasks(
        controller,
        now,
        &trace,
        &mut controller.bridge.event_sink().clone(),
    )
    .map_err(|e| e.to_string())?;
    Ok(
        serde_json::json!({"status":"revision_applied","switch_command_id":command_id,"plan_revision":version.revision,"source_group_id":switch.source_group.as_str(),"target_group_id":switch.target_group.as_str(),"target_plan_digest":version.target_plan_digest,"completed_tasks":version.completed_tasks}),
    )
}

/// Holds current derived work while the original switch owns stop/revoke or manual reconciliation.
pub(crate) fn blocks_mission(controller: &ControllerState, mission: &domain::MissionId) -> bool {
    controller
        .runtime_revisions
        .get(mission.as_str())
        .is_some_and(|state| {
            state.switches.iter().any(|switch| {
                !matches!(switch.phase, Phase::Applied | Phase::Cancelled)
                    && controller
                        .orchestrator
                        .execution(mission)
                        .is_some_and(|execution| execution.group_id() == &switch.source_group)
            })
        })
}

/// Closes pending revision bookkeeping only after actual original Mission termination and Group release.
pub(crate) fn close_terminal(controller: &mut ControllerState, mission: &domain::MissionId) {
    let Some(execution) = controller.orchestrator.execution(mission) else {
        return;
    };
    if !matches!(
        execution.lifecycle(),
        orchestration::MissionExecutionLifecycle::Failed
            | orchestration::MissionExecutionLifecycle::Cancelled
    ) || controller
        .bridge
        .control()
        .group(execution.group_id())
        .is_none_or(|group| group.lifecycle() != control::GroupLifecycle::Released)
    {
        return;
    }
    if let Some(state) = controller.runtime_revisions.get_mut(mission.as_str()) {
        for switch in &mut state.switches {
            if !matches!(switch.phase, Phase::Applied | Phase::Cancelled)
                && &switch.source_group == execution.group_id()
            {
                switch.phase = Phase::Cancelled;
                switch.reason = Some(
                    "original Mission explicitly terminated through complete release gates".into(),
                );
                state.revision = state.revision.saturating_add(1);
            }
        }
    }
}

/// Chooses only an actually pre-reviewed exact alternative under its original configured trigger.
pub(crate) fn observe_trigger(
    controller: &mut ControllerState,
    mission: &domain::MissionId,
    trigger: Trigger,
    now: domain::TimestampMs,
) -> Result<bool, String> {
    let Some(mut state) = controller.runtime_revisions.remove(mission.as_str()) else {
        return Ok(false);
    };
    let result = (|| {
        if admission::refresh_authority(controller, &state).is_err() {
            return Ok(false);
        }
        if state.switches.iter().any(|switch| {
            !matches!(switch.phase, Phase::Applied | Phase::Cancelled)
                && controller
                    .orchestrator
                    .execution(mission)
                    .is_some_and(|execution| execution.group_id() == &switch.source_group)
        }) {
            return Ok(true);
        }
        let kernel = KernelTime::observe()?;
        if !state.policy.automatic_triggers.contains(&trigger)
            || !admission::active(controller, &state, now, &kernel)
        {
            return Ok(false);
        }
        let ticket = state
            .candidates
            .values()
            .find(|candidate| {
                candidate
                    .review
                    .as_ref()
                    .is_some_and(|review| review.decision == ReviewDecision::Approve)
                    && reviewed_plan(
                        controller,
                        mission,
                        &state,
                        &candidate.ticket_id,
                        now,
                        None,
                        None,
                    )
                    .is_ok()
            })
            .map(|candidate| candidate.ticket_id.clone());
        let id = format!(
            "revision-auto-{}",
            &digest(&(
                &state.policy_digest,
                mission,
                controller
                    .orchestrator
                    .execution(mission)
                    .map(|execution| execution.plan_revision()),
                trigger
            ))?[7..47]
        );
        let target =
            domain::ExecutionGroupId::new(format!("group-{id}")).map_err(|e| e.to_string())?;
        let ticket = if let Some(ticket) = ticket {
            ticket
        } else if generation::active(controller, &state, now, &kernel) {
            let ticket = format!("work-{id}");
            let candidate = admission::ticket(
                controller,
                mission,
                &state,
                &ticket,
                &state.policy.proposer,
                &kernel,
                false,
            )?;
            state.candidates.insert(ticket.clone(), candidate);
            ticket
        } else {
            return Ok(false);
        };
        begin(
            controller,
            mission,
            &mut state,
            &ticket,
            target,
            &id,
            Some(trigger),
            true,
            now,
            &kernel,
            None,
            None,
        )?;
        state.revision = state.revision.checked_add(1).ok_or("revision overflow")?;
        Ok(true)
    })();
    controller
        .runtime_revisions
        .insert(mission.as_str().into(), state);
    result
}

/// Advances original evidence only; authority/clock loss produces a retained manual hold.
pub(crate) fn tick(
    controller: &mut ControllerState,
    clock: &impl Clock,
    deployment: Option<&DeploymentFeasibility>,
    feed: Option<&TaskVerifierFeed>,
) -> Result<(), String> {
    if !controller.runtime_revisions.values().any(|state| {
        state.switches.iter().any(|switch| {
            matches!(
                switch.phase,
                Phase::Stopping | Phase::Ready | Phase::Generating
            )
        })
    }) {
        return Ok(());
    }
    match KernelTime::observe() {
        Ok(_) => {}
        Err(reason) => {
            for state in controller.runtime_revisions.values_mut() {
                for switch in &mut state.switches {
                    if matches!(
                        switch.phase,
                        Phase::Stopping | Phase::Ready | Phase::Generating
                    ) {
                        switch.phase = Phase::Manual;
                        switch.reason = Some(reason.clone());
                        state.revision =
                            state.revision.checked_add(1).ok_or("revision overflow")?;
                    }
                }
            }
            return Ok(());
        }
    };
    let missions = controller
        .runtime_revisions
        .keys()
        .cloned()
        .collect::<Vec<_>>();
    for key in missions {
        let mission = domain::MissionId::new(&key).map_err(|e| e.to_string())?;
        let mut state = controller
            .runtime_revisions
            .remove(&key)
            .expect("original revision state");
        let result: Result<(), String> = (|| {
            if let Err(reason) = admission::refresh_authority(controller, &state) {
                for switch in &mut state.switches {
                    if matches!(
                        switch.phase,
                        Phase::Stopping | Phase::Ready | Phase::Generating
                    ) {
                        switch.phase = Phase::Manual;
                        switch.reason = Some(reason.clone());
                        state.revision =
                            state.revision.checked_add(1).ok_or("revision overflow")?;
                    }
                }
                return Ok(());
            }
            let now = clock.now();
            let kernel = KernelTime::observe()?;
            for index in 0..state.switches.len() {
                let switch = state.switches[index].clone();
                if !matches!(switch.phase, Phase::Applied | Phase::Cancelled)
                    && controller
                        .orchestrator
                        .execution(&mission)
                        .is_some_and(|execution| {
                            matches!(
                                execution.lifecycle(),
                                orchestration::MissionExecutionLifecycle::Cancelled
                                    | orchestration::MissionExecutionLifecycle::Failed
                            ) && execution.group_id() == &switch.source_group
                        })
                {
                    state.switches[index].phase = Phase::Cancelled;
                    state.switches[index].reason =
                        Some("original Mission terminated through existing release gates".into());
                    state.revision = state.revision.checked_add(1).ok_or("revision overflow")?;
                    continue;
                }
                if !matches!(
                    switch.phase,
                    Phase::Stopping | Phase::Ready | Phase::Generating
                ) {
                    continue;
                }
                match barriers(controller, &mission, &state, &switch, now, &kernel) {
                    Err(reason) => {
                        state.switches[index].phase = Phase::Manual;
                        state.switches[index].reason = Some(reason);
                        state.revision =
                            state.revision.checked_add(1).ok_or("revision overflow")?;
                    }
                    Ok(false) => {}
                    Ok(true) => {
                        if switch.generation {
                            let candidate = &state.candidates[&switch.ticket_id];
                            if candidate.rejected.is_some()
                                || candidate
                                    .review
                                    .as_ref()
                                    .is_some_and(|review| review.decision == ReviewDecision::Reject)
                            {
                                state.switches[index].phase = Phase::Manual;
                                state.switches[index].reason = Some(
                                    "original generated candidate or Review was rejected".into(),
                                );
                                state.revision =
                                    state.revision.checked_add(1).ok_or("revision overflow")?;
                                continue;
                            }
                            if candidate.review.is_none() {
                                if switch.phase != Phase::Generating {
                                    state.switches[index].phase = Phase::Generating;
                                    state.revision =
                                        state.revision.checked_add(1).ok_or("revision overflow")?;
                                }
                                continue;
                            }
                        }
                        if switch.phase != Phase::Ready {
                            state.switches[index].phase = Phase::Ready;
                            state.revision =
                                state.revision.checked_add(1).ok_or("revision overflow")?;
                        }
                        if switch.automatic {
                            let mut committed = controller.clone();
                            let mut original = state.clone();
                            let events = controller.bridge.event_sink().clone();
                            let outcome = events
                                .with_transition_savepoint(|| {
                                    commit(
                                        &mut committed,
                                        &mission,
                                        &mut original,
                                        &switch.command_id,
                                        now,
                                        &kernel,
                                        deployment,
                                        feed,
                                    )
                                })
                                .map_err(|e| e.to_string())?;
                            if let Some(error) = events.take_error().map_err(|e| e.to_string())? {
                                return Err(error);
                            }
                            match outcome {
                                Ok(_) => {
                                    *controller = committed;
                                    state = original;
                                }
                                Err(reason) => {
                                    state.switches[index].phase = Phase::Manual;
                                    state.switches[index].reason =
                                        Some(reason.chars().take(1024).collect());
                                }
                            }
                            state.revision =
                                state.revision.checked_add(1).ok_or("revision overflow")?;
                        }
                    }
                }
            }
            Ok(())
        })();
        controller.runtime_revisions.insert(key, state);
        result?;
    }
    Ok(())
}
