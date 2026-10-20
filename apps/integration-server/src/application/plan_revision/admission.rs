//! Actual writer-authorized original policy, candidate tickets and immutable Review.

use super::*;

/// Rechecks the original actual peer leaf after every current-role refresh, without accepting another leaf's grant.
pub(super) fn authorized_caller(
    controller: &ControllerState,
    caller: &Caller<'_>,
    now: domain::TimestampMs,
) -> bool {
    now.as_millis() < caller.certificate_expires_at_ms
        && controller
            .bridge
            .control()
            .operator_authorization()
            .and_then(|registry| registry.grants.get(caller.certificate_digest))
            .is_some_and(|grant| {
                grant.active(now.as_millis())
                    && grant.principal_id == caller.principal
                    && grant.role == caller.role
                    && matches!(grant.role.as_str(), "operator" | "recovery-admin")
                    && (grant.tenant_ids.contains("*") || grant.tenant_ids.contains(caller.tenant))
            })
}

/// Rechecks the actual original deployment file inside the existing writer, without a nested transaction.
pub(super) fn refresh_authority(
    controller: &mut ControllerState,
    state: &RevisionState,
) -> Result<(), String> {
    use std::io::Read;
    let file = std::fs::File::open(&state.authorization_path).map_err(|e| e.to_string())?;
    let mut bytes = Vec::new();
    file.take(2 * 1024 * 1024 + 1)
        .read_to_end(&mut bytes)
        .map_err(|e| e.to_string())?;
    if bytes.len() > 2 * 1024 * 1024 {
        return Err("original operator authorization exceeds its bound".into());
    }
    let profile: domain::OperatorAuthorization =
        serde_json::from_slice(&bytes).map_err(|e| e.to_string())?;
    profile.validate()?;
    if profile.registry_id != state.authorization_registry_id {
        return Err("original revision authorization registry changed".into());
    }
    controller
        .bridge
        .control_mut()
        .accept_operator_authorization(Some(profile))
        .map_err(|e| e.to_string())
}

/// Consults committed stable-principal/tenant grants; an old certificate or arbitrary role text is insufficient.
pub(super) fn authorized_subject(
    controller: &ControllerState,
    subject: &str,
    tenant: &str,
    admin: bool,
    now: domain::TimestampMs,
) -> bool {
    controller
        .bridge
        .control()
        .operator_authorization()
        .is_some_and(|registry| {
            registry.grants.values().any(|grant| {
                grant.principal_id == subject
                    && grant.active(now.as_millis())
                    && (grant.tenant_ids.contains("*") || grant.tenant_ids.contains(tenant))
                    && if admin {
                        grant.role == "recovery-admin"
                    } else {
                        matches!(grant.role.as_str(), "operator" | "recovery-admin")
                    }
            })
        })
}

/// Original policy permission remains bounded by its actual accepted clock and current delegation.
pub(super) fn active(
    controller: &ControllerState,
    state: &RevisionState,
    now: domain::TimestampMs,
    kernel: &KernelTime,
) -> bool {
    !state.revoked
        && state.budget.remaining(kernel) > 0
        && now.as_millis() < state.certificate_expires_at_ms
        && authorized_subject(controller, &state.authority, &state.tenant, true, now)
}

/// Rejects unbounded policy inputs before the original permission or any ticket is accepted.
pub(super) fn validate_policy(policy: &Policy) -> Result<(), String> {
    if !identity(&policy.policy_id)
        || !identity(&policy.proposer)
        || !(100..=3_600_000).contains(&policy.lifetime_ms)
        || !(1..=MAX_CANDIDATES).contains(&policy.candidate_limit)
        || !(100..=300_000).contains(&policy.candidate_timeout_ms)
        || !(1024..=256 * 1024).contains(&policy.candidate_bytes)
        || !(1..=orchestration::MAX_MISSION_PLAN_REVISIONS).contains(&policy.switch_limit)
        || !(100..=300_000).contains(&policy.switch_timeout_ms)
        || !policy.goal_context.is_object()
        || policy.goal_context.to_string().len() > 64 * 1024
        || policy.goal_verifier.predicate().trim().is_empty()
        || policy.goal_verifier.predicate().len() > 4096
        || !(100..=60_000).contains(&policy.goal_verifier.max_evidence_age_ms())
    {
        return Err("invalid bounded runtime revision policy".into());
    }
    Ok(())
}

/// Retains original manually supplied or MI-reported provenance without inventing a provider call.
pub(super) fn validate_provenance(
    value: &serde_json::Value,
    principal: &str,
) -> Result<(), String> {
    let object = value
        .as_object()
        .ok_or("candidate provenance must be a closed object")?;
    let kind = object
        .get("kind")
        .and_then(serde_json::Value::as_str)
        .ok_or("candidate provenance kind absent")?;
    let fields: &[&str] = match kind {
        "operator" => &["kind", "principal"],
        "mission-intelligence" => &[
            "kind",
            "principal",
            "request_id",
            "provider_call_id",
            "code_digest",
            "config_digest",
            "catalog_digest",
            "prompt_digest",
            "dependency_digest",
            "original_output_digest",
        ],
        _ => return Err("unknown original candidate provenance".into()),
    };
    if object.len() != fields.len()
        || fields.iter().any(|name| {
            object
                .get(*name)
                .and_then(serde_json::Value::as_str)
                .is_none_or(|value| value.is_empty() || value.len() > 256)
        })
        || object["principal"] != principal
        || kind == "mission-intelligence"
            && fields
                .iter()
                .filter(|name| name.ends_with("_digest"))
                .any(|name| {
                    !object
                        .get(*name)
                        .and_then(serde_json::Value::as_str)
                        .is_some_and(valid_digest)
                })
    {
        return Err(
            "candidate provenance is absent, changed or not tied to its original principal".into(),
        );
    }
    Ok(())
}

/// Returns actual current semantic satisfaction, never a provider's local Completed flag.
pub(super) fn completed(
    controller: &ControllerState,
    mission: &domain::MissionId,
) -> Result<BTreeSet<domain::TaskId>, String> {
    let execution = controller
        .orchestrator
        .execution(mission)
        .ok_or("unknown revision Mission")?;
    Ok(controller
        .bridge
        .control()
        .group(execution.group_id())
        .ok_or("original Group is absent")?
        .task_executions()
        .filter(|task| task.lifecycle() == domain::TaskExecutionLifecycle::Completed)
        .map(|task| task.task_ref().task_id().clone())
        .collect())
}

/// Validates complete published and current deployment/verifier contracts without creating a Group.
fn validate_candidate(
    controller: &ControllerState,
    mission: &domain::MissionId,
    plan: &domain::MissionPlan,
    expected: &BTreeSet<domain::TaskId>,
    deployment: Option<&DeploymentFeasibility>,
    feed: Option<&TaskVerifierFeed>,
    tenant: &str,
) -> Result<(), String> {
    controller
        .orchestrator
        .validate_plan_alternative(mission, plan, expected)
        .map_err(|error| error.to_string())?;
    super::super::semantic_verifier::validate_revision_plan(
        controller, plan, tenant, feed, expected,
    )?;
    if let Some(deployment) = deployment {
        let group = domain::ExecutionGroupId::new(format!("revision-preflight-{mission}"))
            .map_err(|e| e.to_string())?;
        deployment.restrictions_for_plan(plan, &group)?;
    }
    Ok(())
}

/// Creates a current exact-digest administrative approval whose completion precondition remains explicit.
fn review(
    caller: &Caller<'_>,
    candidate: &Candidate,
    expected: BTreeSet<domain::TaskId>,
    decision: ReviewDecision,
    reason: &str,
    now: domain::TimestampMs,
    kernel: &KernelTime,
) -> Result<Review, String> {
    if reason.trim().is_empty() || reason.len() > 512 || candidate.budget.remaining(kernel) == 0 {
        return Err("Review requires a bounded explicit reason".into());
    }
    let mut review = Review {
        decision,
        reason: reason.into(),
        principal: caller.principal.into(),
        plan_digest: candidate
            .plan_digest
            .clone()
            .ok_or("candidate has no accepted digest")?,
        completion_precondition: expected,
        certificate_expires_at_ms: caller.certificate_expires_at_ms,
        reviewed_at: now,
        reviewed_kernel: kernel.clone(),
        digest: String::new(),
    };
    review.digest = digest(&review)?;
    Ok(review)
}

/// Starts one original candidate work budget before returning any model input or calling a provider.
pub(super) fn ticket(
    controller: &ControllerState,
    mission: &domain::MissionId,
    state: &RevisionState,
    id: &str,
    principal: &str,
    kernel: &KernelTime,
    alternative: bool,
) -> Result<Candidate, String> {
    if !identity(id)
        || state.candidates.contains_key(id)
        || state.candidates.len() >= state.policy.candidate_limit
    {
        return Err("original candidate ticket identity or budget exhausted".into());
    }
    let execution = controller
        .orchestrator
        .execution(mission)
        .ok_or("unknown candidate Mission")?;
    let remaining = state.budget.remaining(kernel);
    Ok(Candidate {
        claim: None,
        provider_calls: BTreeMap::new(),
        ticket_id: id.into(),
        principal: principal.into(),
        base_revision: execution.plan_revision(),
        base_digest: orchestration::mission_plan_digest(execution.plan()),
        budget: OriginalBudget::new(
            kernel,
            if alternative {
                remaining
            } else {
                remaining.min(state.policy.candidate_timeout_ms)
            },
        )?,
        completed_time: None,
        plan: None,
        plan_digest: None,
        provenance: None,
        rejected: None,
        review: None,
    })
}

/// Counts original retained raw proposal bytes; dropping a row never grants more original policy attempts.
fn retained_bytes(controller: &ControllerState, state: &RevisionState) -> usize {
    controller
        .runtime_revisions
        .values()
        .chain(std::iter::once(state))
        .flat_map(|state| state.candidates.values())
        .filter_map(|candidate| candidate.plan.as_ref())
        .map(|plan| plan.to_string().len())
        .sum()
}

/// Applies one already authenticated action to its private candidate and retains typed original outcomes.
#[allow(clippy::too_many_arguments)]
fn apply(
    controller: &mut ControllerState,
    mission: &domain::MissionId,
    state: &mut RevisionState,
    command: &Command,
    caller: &Caller<'_>,
    now: domain::TimestampMs,
    kernel: &KernelTime,
    deployment: Option<&DeploymentFeasibility>,
    feed: Option<&TaskVerifierFeed>,
) -> Result<serde_json::Value, String> {
    if matches!(command.action, Action::RevokePolicy) {
        if caller.role != "recovery-admin" {
            return Err("policy revocation requires actual recovery-admin authority".into());
        }
        state.revoked = true;
        return Ok(serde_json::json!({"status":"policy_revoked"}));
    }
    if !active(controller, state, now, kernel) {
        return Err(
            "original revision policy is revoked, expired or lacks current delegation".into(),
        );
    }
    if controller
        .orchestrator
        .execution(mission)
        .is_none_or(|execution| {
            !matches!(
                execution.lifecycle(),
                orchestration::MissionExecutionLifecycle::Accepted
                    | orchestration::MissionExecutionLifecycle::Running
            )
        })
    {
        return Err(
            "terminal or cancelling Mission cannot acquire new candidate, Review or switch work"
                .into(),
        );
    }
    if matches!(
        command.action,
        Action::RequestSwitch { .. } | Action::CommitSwitch { .. }
    ) && caller.role != "recovery-admin"
        && caller.principal != state.policy.proposer
    {
        return Err("switch request belongs to another original delegated principal".into());
    }
    match &command.action {
        Action::ClaimRuntimeWork { ticket_id, claim } => generation::claim(
            controller, mission, state, command, caller, ticket_id, claim, now, kernel,
        ),
        Action::AdmitProviderCall {
            ticket_id,
            phase,
            nonce,
            request_digest,
        } => generation::admit_call(
            controller,
            mission,
            state,
            command,
            caller,
            ticket_id,
            phase,
            nonce,
            request_digest,
            now,
            kernel,
        ),
        Action::AuthorizeGeneration => {
            if command.schema_version != GENERATION_SCHEMA {
                return Err("generation delegation requires explicit runtime revision v0.2".into());
            }
            super::generation::authorize(controller, state, caller, now, kernel)
        }
        Action::InstallPolicy { .. } => {
            Err("an original revision policy cannot be replaced or its budget renewed".into())
        }
        Action::RevokePolicy => unreachable!("handled original revocation"),
        Action::RequestCandidate { ticket_id } => {
            if caller.principal != state.policy.proposer {
                return Err("candidate work belongs to another stable principal".into());
            }
            let candidate = ticket(
                controller,
                mission,
                state,
                ticket_id,
                caller.principal,
                kernel,
                false,
            )?;
            let result = serde_json::json!({"status":"candidate_work_admitted","ticket_id":ticket_id,"base_revision":candidate.base_revision,"base_plan_digest":candidate.base_digest,"remaining_ms":candidate.budget.remaining(kernel)});
            state.candidates.insert(ticket_id.clone(), candidate);
            Ok(result)
        }
        Action::SubmitCandidate {
            ticket_id,
            plan,
            provenance,
        } => {
            let candidate = state
                .candidates
                .get(ticket_id)
                .ok_or("unknown original candidate ticket")?;
            if candidate.principal != caller.principal
                || candidate.plan.is_some()
                || candidate.completed_time.is_some()
            {
                return Err(
                    "candidate output was already recorded or belongs to another principal".into(),
                );
            }
            validate_provenance(provenance, caller.principal)?;
            generation::validate_provenance(state, ticket_id, candidate, provenance)?;
            let execution = controller
                .orchestrator
                .execution(mission)
                .ok_or("unknown candidate Mission")?;
            let mut rejection = if candidate.budget.remaining(kernel) == 0 {
                Some("original candidate work budget expired or changed time domain".into())
            } else if execution.plan_revision() != candidate.base_revision
                || orchestration::mission_plan_digest(execution.plan()) != candidate.base_digest
            {
                Some("original candidate source version changed".into())
            } else {
                None
            };
            let byte_size = plan.to_string().len();
            let retain = byte_size <= state.policy.candidate_bytes
                && retained_bytes(controller, state).saturating_add(byte_size)
                    <= MAX_RETAINED_PLAN_BYTES;
            let mut plan_digest = None;
            if !retain {
                rejection =
                    Some("original candidate output exceeds its retained byte budget".into());
            }
            if rejection.is_none() {
                match orchestration::decode_mission_plan(&plan.to_string()) {
                    Ok(decoded) => {
                        plan_digest = Some(orchestration::mission_plan_digest(&decoded));
                        let expected = completed(controller, mission)?;
                        if let Err(error) = validate_candidate(
                            controller,
                            mission,
                            &decoded,
                            &expected,
                            deployment,
                            feed,
                            &state.tenant,
                        ) {
                            rejection = Some(error);
                        }
                    }
                    Err(error) => rejection = Some(error.to_string()),
                }
            }
            let candidate = state
                .candidates
                .get_mut(ticket_id)
                .expect("original candidate");
            candidate.completed_time = Some(kernel.clone());
            candidate.plan = retain.then(|| plan.clone());
            candidate.plan_digest = plan_digest;
            candidate.provenance = Some(provenance.clone());
            candidate.rejected = rejection.map(|reason| reason.chars().take(1024).collect());
            Ok(
                serde_json::json!({"status":if candidate.rejected.is_some(){"candidate_rejected"}else{"awaiting_review"},"ticket_id":ticket_id,"plan_digest":candidate.plan_digest,"rejection":candidate.rejected}),
            )
        }
        Action::ReviewCandidate {
            ticket_id,
            plan_digest,
            decision,
            reason,
        } => {
            if caller.role != "recovery-admin" {
                return Err("candidate Review requires actual recovery-admin authority".into());
            }
            let candidate = state
                .candidates
                .get(ticket_id)
                .ok_or("unknown reviewed ticket")?;
            if candidate.rejected.is_some()
                || candidate.review.is_some()
                || candidate.plan_digest.as_ref() != Some(plan_digest)
            {
                return Err("candidate is rejected, already reviewed or changed identity".into());
            }
            let expected = completed(controller, mission)?;
            let plan = orchestration::decode_mission_plan(
                &candidate
                    .plan
                    .as_ref()
                    .ok_or("original output unavailable")?
                    .to_string(),
            )
            .map_err(|e| e.to_string())?;
            validate_candidate(
                controller,
                mission,
                &plan,
                &expected,
                deployment,
                feed,
                &state.tenant,
            )?;
            let approved = review(
                caller,
                candidate,
                expected,
                *decision,
                reason,
                now,
                &KernelTime::observe()?,
            )?;
            let result = serde_json::json!({"status":"review_recorded","ticket_id":ticket_id,"decision":decision,"review_digest":approved.digest});
            state
                .candidates
                .get_mut(ticket_id)
                .expect("original candidate")
                .review = Some(approved);
            Ok(result)
        }
        Action::ApproveAlternative {
            ticket_id,
            plan,
            completion_precondition,
        } => {
            if caller.role != "recovery-admin" {
                return Err("alternative Review requires actual recovery-admin authority".into());
            }
            let byte_size = plan.to_string().len();
            if byte_size > state.policy.candidate_bytes
                || retained_bytes(controller, state).saturating_add(byte_size)
                    > MAX_RETAINED_PLAN_BYTES
            {
                return Err("alternative exceeds original retained byte budget".into());
            }
            let decoded =
                orchestration::decode_mission_plan(&plan.to_string()).map_err(|e| e.to_string())?;
            validate_candidate(
                controller,
                mission,
                &decoded,
                completion_precondition,
                deployment,
                feed,
                &state.tenant,
            )?;
            let mut candidate = ticket(
                controller,
                mission,
                state,
                ticket_id,
                caller.principal,
                kernel,
                true,
            )?;
            candidate.plan = Some(plan.clone());
            candidate.plan_digest = Some(orchestration::mission_plan_digest(&decoded));
            candidate.completed_time = Some(kernel.clone());
            candidate.provenance =
                Some(serde_json::json!({"kind":"operator","principal":caller.principal}));
            let approved = review(
                caller,
                &candidate,
                completion_precondition.clone(),
                ReviewDecision::Approve,
                "explicit complete alternative with completion precondition",
                now,
                &KernelTime::observe()?,
            )?;
            let result = serde_json::json!({"status":"alternative_reviewed","ticket_id":ticket_id,"plan_digest":candidate.plan_digest,"review_digest":approved.digest});
            candidate.review = Some(approved);
            state.candidates.insert(ticket_id.clone(), candidate);
            Ok(result)
        }
        Action::RequestSwitch {
            ticket_id,
            target_group_id,
        } => super::switching::begin(
            controller,
            mission,
            state,
            ticket_id,
            target_group_id.clone(),
            &command.command_id,
            None,
            false,
            now,
            kernel,
            deployment,
            feed,
        ),
        Action::CommitSwitch { switch_command_id } => super::switching::commit(
            controller,
            mission,
            state,
            switch_command_id,
            now,
            kernel,
            deployment,
            feed,
        ),
    }
}

/// Journals the exact current caller and original command before exposing any candidate or stop outbox.
#[allow(clippy::too_many_arguments)]
pub(crate) fn submit(
    controller: &Arc<Mutex<ControllerState>>,
    log: &state::SqliteEventLog,
    gate: &dyn ApplicationWriteGate,
    clock: &impl Clock,
    mission: &domain::MissionId,
    body: &[u8],
    caller: &Caller<'_>,
    deployment: Option<&DeploymentFeasibility>,
    feed: Option<&TaskVerifierFeed>,
) -> Result<serde_json::Value, String> {
    let command: Command = serde_json::from_slice(body).map_err(|e| e.to_string())?;
    if !matches!(command.schema_version.as_str(), SCHEMA | GENERATION_SCHEMA)
        || !identity(&command.command_id)
    {
        return Err("invalid closed revision command".into());
    }
    let input_digest = digest(&command)?;
    let _writer = gate.lock()?;
    let now = clock.now();
    let kernel = KernelTime::observe()?;
    let mut candidate = controller
        .lock()
        .map_err(|_| "Controller authority unavailable")?
        .clone();
    if candidate.bridge.control().mission_tenant(mission) != Some(caller.tenant)
        || !matches!(caller.role, "operator" | "recovery-admin")
        || !authorized_caller(&candidate, caller, now)
    {
        return Err(
            "revision requires current authenticated tenant/principal/TLS authority".into(),
        );
    }
    let key = mission.as_str().to_string();
    if let Some(receipt) = candidate
        .runtime_revisions
        .get(&key)
        .and_then(|state| state.commands.get(&command.command_id))
    {
        if receipt.digest != input_digest || receipt.principal != caller.principal {
            return Err("revision command identity belongs to another input or principal".into());
        }
        return Ok(receipt.result.clone());
    }
    let mut state = if let Some(state) = candidate.runtime_revisions.remove(&key) {
        state
    } else {
        let Action::InstallPolicy { policy } = &command.action else {
            return Err("Mission has no original revision policy".into());
        };
        if caller.role != "recovery-admin" || command.expected_revision != 0 {
            return Err(
                "original policy installation requires current recovery-admin and revision zero"
                    .into(),
            );
        }
        validate_policy(policy)?;
        if !authorized_subject(&candidate, &policy.proposer, caller.tenant, false, now) {
            return Err("revision proposer lacks current tenant delegation".into());
        }
        let execution = candidate
            .orchestrator
            .execution(mission)
            .ok_or("unknown revision Mission")?;
        if !matches!(
            execution.lifecycle(),
            orchestration::MissionExecutionLifecycle::Accepted
                | orchestration::MissionExecutionLifecycle::Running
        ) {
            return Err("terminal Mission cannot acquire new policy".into());
        }
        let budget = OriginalBudget::new(
            &kernel,
            policy.lifetime_ms.min(
                caller
                    .certificate_expires_at_ms
                    .saturating_sub(now.as_millis()),
            ),
        )?;
        let deployment_digest = deployment.map(|profile| profile.digest().to_string());
        let authorization_path =
            std::path::absolute(caller.authorization_path.ok_or(
                "runtime revision requires the actual versioned TLS authorization profile",
            )?)
            .map_err(|e| e.to_string())?;
        let authorization_registry_id = candidate
            .bridge
            .control()
            .operator_authorization()
            .ok_or("versioned operator authority unavailable")?
            .registry_id
            .clone();
        let authority_grant = candidate
            .bridge
            .control()
            .operator_authorization()
            .and_then(|registry| registry.grants.get(caller.certificate_digest))
            .ok_or("original actual issuer grant unavailable")?
            .clone();
        let original_goal = candidate
            .orchestrator
            .execution(mission)
            .ok_or("unknown original Mission")?
            .plan()
            .goal()
            .clone();
        let goal_contract = candidate
            .bridge
            .control_mut()
            .freeze_mission_goal(
                &original_goal,
                policy.goal_verifier.clone(),
                digest(&policy.goal_context)?,
                caller.tenant,
            )
            .map_err(|e| e.to_string())?;
        if goal_contract
            .source
            .producer_principals
            .contains(caller.principal)
            || goal_contract
                .source
                .producer_principals
                .contains(&policy.proposer)
        {
            return Err(
                "original goal producer must be independent of the policy installer and proposer"
                    .into(),
            );
        }
        candidate
            .orchestrator
            .install_mission_goal_gate(mission, candidate.bridge.control())
            .map_err(|e| e.to_string())?;
        let policy_digest = digest(&(
            policy,
            caller.principal,
            caller.tenant,
            &budget,
            now,
            &deployment_digest,
            caller.certificate_expires_at_ms,
            &authorization_path,
            &authorization_registry_id,
            &goal_contract.digest,
            caller.certificate_digest,
            &authority_grant,
        ))?;
        RevisionState {
            generation: None,
            authority_certificate_digest: caller.certificate_digest.into(),
            authority_grant,
            goal_contract_digest: goal_contract.digest,
            goal_evidence: BTreeMap::new(),
            authorization_path,
            authorization_registry_id,
            deployment_digest,
            deployment: deployment.map(|profile| Arc::new(profile.clone())),
            policy: policy.clone(),
            authority: caller.principal.into(),
            tenant: caller.tenant.into(),
            certificate_expires_at_ms: caller.certificate_expires_at_ms,
            budget,
            accepted_at: now,
            policy_digest,
            revoked: false,
            revision: 0,
            candidates: BTreeMap::new(),
            switches: Vec::new(),
            commands: BTreeMap::new(),
        }
    };
    if command.expected_revision != state.revision
        || state.commands.len() >= MAX_COMMANDS
        || state.commands.len() >= MAX_COMMANDS - 8
            && !matches!(command.action, Action::RevokePolicy)
    {
        return Err("revision changed or original command capacity exhausted".into());
    }
    log.begin_batch().map_err(|e| e.to_string())?;
    let transaction: Result<serde_json::Value, String> = (|| {
        refresh_authority(&mut candidate, &state)?;
        if !authorized_caller(&candidate, caller, clock.now()) {
            return Err("current revision delegation changed under original writer".into());
        }
        let mut response =
            if state.revision == 0 && matches!(command.action, Action::InstallPolicy { .. }) {
                serde_json::json!({"status":"policy_installed","policy_digest":state.policy_digest})
            } else {
                apply(
                    &mut candidate,
                    mission,
                    &mut state,
                    &command,
                    caller,
                    now,
                    &kernel,
                    deployment,
                    feed,
                )?
            };
        if let Some(error) = log.take_error().map_err(|e| e.to_string())? {
            return Err(error);
        }
        state.revision = state.revision.checked_add(1).ok_or("revision overflow")?;
        response["schema_version"] = command.schema_version.clone().into();
        response["command_id"] = command.command_id.clone().into();
        response["revision"] = state.revision.into();
        state.commands.insert(
            command.command_id.clone(),
            CommandReceipt {
                leaf_digest: caller.certificate_digest.into(),
                role: caller.role.into(),
                digest: input_digest.clone(),
                principal: caller.principal.into(),
                received_at: now,
                result: response.clone(),
            },
        );
        candidate.runtime_revisions.insert(key, state);
        let checkpoint = server_checkpoint_json(&candidate).map_err(|e| e.to_string())?;
        let journal = control_command_journal_entry_with_digest(
            format!("plan-revision:{mission}:{}", command.command_id),
            Some(caller.principal.into()),
            input_digest,
            &checkpoint,
            log.current_batch_event_sequence()
                .map_err(|e| e.to_string())?,
        );
        log.save_checkpoint_with_command(SERVER_CHECKPOINT_SCHEMA, &checkpoint, journal)
            .map_err(|e| e.to_string())?;
        log.commit_batch().map_err(|e| e.to_string())?;
        Ok(response)
    })();
    let response = match transaction {
        Ok(response) => response,
        Err(error) => {
            let rollback = log.rollback_batch();
            return Err(format!(
                "revision transaction rejected/unavailable: {error}; rollback={rollback:?}"
            ));
        }
    };
    let mut live = controller
        .lock()
        .map_err(|_| "Controller authority unavailable after Commit")?;
    *live = candidate;
    if let Err(error) = flush_unisolated_commands(&mut live, clock) {
        eprintln!("revision committed; original outbox deferred: {error}");
    }
    super::super::memory_use::flush(&live, now, clock.now());
    super::super::memory_preparation::flush(&live, now, clock.now());
    Ok(response)
}

/// Reads bounded metadata and original semantic input; observations never consume or renew a ticket.
pub(crate) fn view(
    controller: &ControllerState,
    mission: &domain::MissionId,
    now: domain::TimestampMs,
) -> Result<serde_json::Value, String> {
    let execution = controller
        .orchestrator
        .execution(mission)
        .ok_or("unknown revision Mission")?;
    let state = controller.runtime_revisions.get(mission.as_str());
    let kernel = KernelTime::observe()?;
    let mut used_task_ids: BTreeSet<_> = execution
        .plan()
        .task_graph()
        .tasks()
        .iter()
        .map(|task| task.task_id().clone())
        .collect();
    for version in execution.plan_revisions() {
        let original = orchestration::decode_mission_plan(&version.source_plan.to_string())
            .map_err(|error| error.to_string())?;
        used_task_ids.extend(
            original
                .task_graph()
                .tasks()
                .iter()
                .map(|task| task.task_id().clone()),
        );
    }
    Ok(
        serde_json::json!({"schema_version":SCHEMA,"generation_schema":GENERATION_SCHEMA,"mission_id":mission.as_str(),"used_task_ids":used_task_ids,"current_plan_revision":execution.plan_revision(),"current_group_id":execution.group_id().as_str(),"original_group_id":execution.original_group_id().as_str(),"current_plan_digest":orchestration::mission_plan_digest(execution.plan()),"current_plan":orchestration::mission_plan_document(execution.plan()),"history":execution.plan_revisions().iter().map(|version|serde_json::json!({"revision":version.revision,"command_id":version.command_id,"source_group_id":version.source_group_id,"target_group_id":version.target_group_id,"plan_digest":version.target_plan_digest,"review_digest":version.review_digest,"completed_tasks":version.completed_tasks,"committed_at_ms":version.committed_at.as_millis()})).collect::<Vec<_>>(),"completed_tasks":completed(controller,mission)?,"goal_contract":controller.bridge.control().mission_goal_contract(mission),"goal_source_current":controller.bridge.control().mission_goal_source_current(mission),"goal_evidence":state.map(|state|state.goal_evidence.iter().map(|(digest,progress)|serde_json::json!({"input_digest":digest,"remaining_ms":progress.budget.remaining(&kernel),"completed":progress.completed_time.is_some(),"incident":progress.incident})).collect::<Vec<_>>()),"goal_context":state.map(|state|&state.policy.goal_context),"authority":state.map(|state|serde_json::json!({"revision":state.revision,"policy":state.policy,"policy_id":state.policy.policy_id,"policy_digest":state.policy_digest,"revoked":state.revoked,"active":active(controller,state,now,&kernel),"remaining_ms":state.budget.remaining(&kernel),"candidates":state.candidates.values().map(|candidate|serde_json::json!({"ticket_id":candidate.ticket_id,"principal":candidate.principal,"base_revision":candidate.base_revision,"base_digest":candidate.base_digest,"remaining_work_ms":candidate.budget.remaining(&kernel),"remaining_switch_ms":state.switches.iter().filter(|switch|switch.ticket_id==candidate.ticket_id).map(|switch|switch.budget.remaining(&kernel)).min(),"output_received":candidate.completed_time.is_some(),"plan_digest":candidate.plan_digest,"claim":candidate.claim,"provider_calls":candidate.provider_calls,"rejection":candidate.rejected,"review":candidate.review})).collect::<Vec<_>>(),"generation":state.generation,"generation_active":generation::active(controller,state,now,&kernel),"switches":state.switches,"commands_retained":state.commands.len()}))}),
    )
}

/// Returns exact original candidate bytes and provenance so an authorized reviewer can inspect the digest.
pub(crate) fn candidate_view(
    controller: &ControllerState,
    mission: &domain::MissionId,
    ticket: &str,
) -> Result<serde_json::Value, String> {
    if !identity(ticket) {
        return Err("invalid bounded candidate identity".into());
    }
    let state = controller
        .runtime_revisions
        .get(mission.as_str())
        .ok_or("Mission has no original revision authority")?;
    let candidate = state
        .candidates
        .get(ticket)
        .ok_or("original candidate absent")?;
    let kernel = KernelTime::observe()?;
    Ok(
        serde_json::json!({"schema_version":SCHEMA,"mission_id":mission.as_str(),"ticket_id":candidate.ticket_id,
        "principal":candidate.principal,"base_revision":candidate.base_revision,"base_plan_digest":candidate.base_digest,
        "claim":candidate.claim,"provider_calls":candidate.provider_calls,"plan":candidate.plan,"plan_digest":candidate.plan_digest,"provenance":candidate.provenance,"review":candidate.review,
        "rejection":candidate.rejected,"remaining_work_ms":candidate.budget.remaining(&kernel)}),
    )
}
