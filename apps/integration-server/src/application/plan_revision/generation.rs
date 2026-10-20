//! Explicit generation delegation; model work never grants Review or execution authority.

use super::*;

/// Checks the current original fault hold without accepting a caller-provided stopped flag.
#[allow(clippy::too_many_arguments)]
fn work_active(
    controller: &ControllerState,
    mission: &domain::MissionId,
    state: &RevisionState,
    command: &Command,
    caller: &Caller<'_>,
    ticket: &str,
    now: domain::TimestampMs,
    kernel: &KernelTime,
) -> Result<(), String> {
    let candidate = state
        .candidates
        .get(ticket)
        .ok_or("original generation ticket absent")?;
    let execution = controller
        .orchestrator
        .execution(mission)
        .ok_or("original Mission absent")?;
    if command.schema_version != GENERATION_SCHEMA
        || caller.principal != state.policy.proposer
        || !active(controller, state, now, kernel)
        || candidate.principal != caller.principal
        || candidate.budget.remaining(kernel) == 0
        || candidate.base_revision != execution.plan_revision()
        || candidate.base_digest != orchestration::mission_plan_digest(execution.plan())
        || !state.switches.iter().any(|switch| {
            switch.generation
                && switch.ticket_id == ticket
                && switch.phase == Phase::Generating
                && &switch.source_group == execution.group_id()
                && switch.budget.remaining(kernel) > 0
        })
    {
        return Err(
            "original generation source, delegation, stop hold or budget unavailable".into(),
        );
    }
    Ok(())
}

/// Claims once before model work; another database or implementation cannot adopt this ticket.
#[allow(clippy::too_many_arguments)]
pub(super) fn claim(
    controller: &ControllerState,
    mission: &domain::MissionId,
    state: &mut RevisionState,
    command: &Command,
    caller: &Caller<'_>,
    ticket: &str,
    input: &GenerationClaimInput,
    now: domain::TimestampMs,
    kernel: &KernelTime,
) -> Result<serde_json::Value, String> {
    work_active(
        controller, mission, state, command, caller, ticket, now, kernel,
    )?;
    if !identity(&input.store_instance_id)
        || !identity(&input.request_id)
        || !valid_digest(&input.work_id)
        || !valid_digest(&input.identity_digest)
    {
        return Err("invalid original MI worker identity".into());
    }
    let candidate = state.candidates.get_mut(ticket).expect("validated ticket");
    if let Some(original) = &candidate.claim {
        if original.input != *input || original.principal != caller.principal {
            return Err("original ticket already belongs to another MI SQL worker".into());
        }
        return Ok(
            serde_json::json!({"status":"runtime_work_claimed","claim_digest":original.digest}),
        );
    }
    if candidate.completed_time.is_some() || !candidate.provider_calls.is_empty() {
        return Err("completed candidate cannot acquire a new MI worker".into());
    }
    let mut receipt = GenerationClaim {
        input: input.clone(),
        principal: caller.principal.into(),
        certificate_digest: caller.certificate_digest.into(),
        certificate_expires_at_ms: caller.certificate_expires_at_ms,
        admitted_at: now,
        admitted_kernel: kernel.clone(),
        digest: String::new(),
    };
    receipt.digest = digest(&receipt)?;
    let response =
        serde_json::json!({"status":"runtime_work_claimed","claim_digest":receipt.digest});
    candidate.claim = Some(receipt);
    Ok(response)
}

/// Admits one exact provider phase/nonce before I/O; an older SQL copy cannot spend it again.
#[allow(clippy::too_many_arguments)]
pub(super) fn admit_call(
    controller: &ControllerState,
    mission: &domain::MissionId,
    state: &mut RevisionState,
    command: &Command,
    caller: &Caller<'_>,
    ticket: &str,
    phase: &str,
    nonce: &str,
    request_digest: &str,
    now: domain::TimestampMs,
    kernel: &KernelTime,
) -> Result<serde_json::Value, String> {
    work_active(
        controller, mission, state, command, caller, ticket, now, kernel,
    )?;
    if !matches!(phase, "provider-generate" | "provider-review")
        || !identity(nonce)
        || !valid_digest(request_digest)
    {
        return Err("invalid bounded original provider call".into());
    }
    let candidate = state.candidates.get_mut(ticket).expect("validated ticket");
    let claim = candidate
        .claim
        .as_ref()
        .ok_or("original MI SQL claim absent")?;
    if let Some(original) = candidate.provider_calls.get(phase) {
        if original.nonce != nonce
            || original.request_digest != request_digest
            || original.principal != caller.principal
        {
            return Err(
                "original provider phase already consumed by another SQL effect nonce".into(),
            );
        }
        return Ok(
            serde_json::json!({"status":"provider_call_admitted","receipt_digest":original.digest}),
        );
    }
    if phase == "provider-generate" && candidate.completed_time.is_some()
        || phase == "provider-review"
            && (candidate.plan.is_none()
                || candidate.rejected.is_some()
                || !candidate.provider_calls.contains_key("provider-generate"))
    {
        return Err("provider phase does not match original candidate progress".into());
    }
    let mut receipt = ProviderCallReceipt {
        certificate_digest: caller.certificate_digest.into(),
        claim_digest: claim.digest.clone(),
        nonce: nonce.into(),
        request_digest: request_digest.into(),
        principal: caller.principal.into(),
        certificate_expires_at_ms: caller.certificate_expires_at_ms,
        admitted_at: now,
        admitted_kernel: kernel.clone(),
        digest: String::new(),
    };
    receipt.digest = digest(&receipt)?;
    let response =
        serde_json::json!({"status":"provider_call_admitted","receipt_digest":receipt.digest});
    candidate.provider_calls.insert(phase.into(), receipt);
    Ok(response)
}

/// Connects claimed MI provenance to its already admitted actual work identity and generation phase.
pub(super) fn validate_provenance(
    state: &RevisionState,
    ticket: &str,
    candidate: &Candidate,
    value: &serde_json::Value,
) -> Result<(), String> {
    let generation = state
        .switches
        .iter()
        .any(|switch| switch.generation && switch.ticket_id == ticket);
    let Some(claim) = &candidate.claim else {
        return if generation && value["kind"] == "mission-intelligence" {
            Err("runtime MI output has no original SQL worker claim".into())
        } else {
            Ok(())
        };
    };
    if value["kind"] != "mission-intelligence"
        || value["request_id"] != claim.input.request_id
        || value["provider_call_id"] != format!("{}:provider-generate", claim.input.work_id)
        || !candidate.provider_calls.contains_key("provider-generate")
    {
        return Err("runtime MI output differs from its original admitted provider work".into());
    }
    Ok(())
}

/// Retains one actual administrative permission without replacing the original policy or its budget.
pub(super) fn authorize(
    controller: &ControllerState,
    state: &mut RevisionState,
    caller: &Caller<'_>,
    now: domain::TimestampMs,
    kernel: &KernelTime,
) -> Result<serde_json::Value, String> {
    if caller.role != "recovery-admin" || state.generation.is_some() {
        return Err("generation requires one original current administrative delegation".into());
    }
    if state.policy.automatic_triggers.is_empty() {
        return Err("original policy authorizes no automatic fault source".into());
    }
    let grant = controller
        .bridge
        .control()
        .operator_authorization()
        .and_then(|registry| registry.grants.get(caller.certificate_digest))
        .ok_or("actual original generation grant absent")?
        .clone();
    let mut permission = GenerationGrant {
        principal: caller.principal.into(),
        certificate_digest: caller.certificate_digest.into(),
        grant,
        certificate_expires_at_ms: caller.certificate_expires_at_ms,
        policy_digest: state.policy_digest.clone(),
        admitted_at: now,
        admitted_kernel: kernel.clone(),
        digest: String::new(),
    };
    permission.digest = digest(&permission)?;
    let result = serde_json::json!({"status":"runtime_generation_authorized","permission_digest":permission.digest,"remaining_policy_ms":state.budget.remaining(kernel),"review_authorized":false});
    state.generation = Some(permission);
    Ok(result)
}

/// Rechecks the exact original live grant and leaf, separately from a possible later Review identity.
pub(super) fn active(
    controller: &ControllerState,
    state: &RevisionState,
    now: domain::TimestampMs,
    kernel: &KernelTime,
) -> bool {
    state.generation.as_ref().is_some_and(|permission| {
        admission::active(controller, state, now, kernel)
            && now.as_millis() < permission.certificate_expires_at_ms
            && controller
                .bridge
                .control()
                .operator_authorization()
                .and_then(|registry| registry.grants.get(&permission.certificate_digest))
                .is_some_and(|grant| {
                    grant.active(now.as_millis())
                        && grant.principal_id == permission.principal
                        && grant.role == "recovery-admin"
                        && (grant.tenant_ids.contains("*")
                            || grant.tenant_ids.contains(&state.tenant))
                })
            && admission::authorized_subject(
                controller,
                &state.policy.proposer,
                &state.tenant,
                false,
                now,
            )
    })
}

/// Refuses forged or renewed delegation metadata while retaining expired original history on restore.
pub(super) fn validate(state: &RevisionState) -> Result<(), String> {
    let Some(permission) = &state.generation else {
        if state.uses_generation_authority() {
            return Err("generation switch has no original explicit permission".into());
        }
        return Ok(());
    };
    let mut original = permission.clone();
    original.digest.clear();
    if permission.digest != digest(&original)?
        || permission.policy_digest != state.policy_digest
        || !valid_digest(&permission.certificate_digest)
        || permission.grant.principal_id != permission.principal
        || permission.grant.role != "recovery-admin"
        || !permission.grant.active(permission.admitted_at.as_millis())
        || !(permission.grant.tenant_ids.contains("*")
            || permission.grant.tenant_ids.contains(&state.tenant))
        || permission.admitted_at < state.accepted_at
        || permission.admitted_at.as_millis() >= permission.certificate_expires_at_ms
        || state.budget.remaining(&permission.admitted_kernel) == 0
        || state.policy.automatic_triggers.is_empty()
    {
        return Err("original generation permission identity or budget changed".into());
    }
    for (ticket, candidate) in &state.candidates {
        let Some(claim) = &candidate.claim else {
            if !candidate.provider_calls.is_empty() {
                return Err("provider work lacks original MI SQL claim".into());
            }
            continue;
        };
        let mut original = claim.clone();
        original.digest.clear();
        if claim.digest != digest(&original)?
            || claim.principal != state.policy.proposer
            || !identity(&claim.input.store_instance_id)
            || !identity(&claim.input.request_id)
            || !valid_digest(&claim.input.work_id)
            || !valid_digest(&claim.input.identity_digest)
            || !valid_digest(&claim.certificate_digest)
            || claim.admitted_at < state.accepted_at
            || claim.admitted_at.as_millis() >= claim.certificate_expires_at_ms
            || candidate.budget.remaining(&claim.admitted_kernel) == 0
            || !state
                .switches
                .iter()
                .any(|switch| switch.generation && &switch.ticket_id == ticket)
            || candidate.provider_calls.len() > 2
        {
            return Err("original MI claim receipt is inconsistent".into());
        }
        for (phase, call) in &candidate.provider_calls {
            let mut original = call.clone();
            original.digest.clear();
            if !matches!(phase.as_str(), "provider-generate" | "provider-review")
                || call.digest != digest(&original)?
                || call.claim_digest != claim.digest
                || call.principal != claim.principal
                || !identity(&call.nonce)
                || !valid_digest(&call.request_digest)
                || !valid_digest(&call.certificate_digest)
                || call.admitted_at < claim.admitted_at
                || call.admitted_at.as_millis() >= call.certificate_expires_at_ms
                || candidate.budget.remaining(&call.admitted_kernel) == 0
                || phase == "provider-review"
                    && (candidate.plan.is_none()
                        || !candidate.provider_calls.contains_key("provider-generate"))
            {
                return Err("original provider call permission is inconsistent".into());
            }
        }
        if let Some(provenance) = &candidate.provenance {
            validate_provenance(state, ticket, candidate, provenance)?;
        }
    }
    Ok(())
}
