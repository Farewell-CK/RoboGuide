//! Original reviewed cohort permissions; all effects still use the existing stop/revoke switch.

use super::*;

/// Original beneficiary policy and exact exclusive blockers, without a prospective allocation.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Beneficiary {
    /// Original higher-priority Mission.
    mission: domain::MissionId,
    /// Original complete beneficiary Group, distinct from a later revised configuration.
    group: domain::ExecutionGroupId,
    /// Exact accepted source version and bytes.
    revision: u64,
    /// Canonical accepted plan digest.
    plan_digest: String,
    /// Original immutable beneficiary scheduling policy.
    priority: u8,
    /// Original victim priority; changing either policy requires another bounded ticket.
    source_priority: u8,
    /// Exact currently held exclusive resources that a current matched Ready Role can use.
    conflicts: BTreeSet<domain::ResourceId>,
}

/// Exact normal Control result, admitted under the original switch budget before any source redispatch.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct BeneficiaryAdmission {
    /// Actual new Runtime intent created only after the beneficiary's normal Commit/Bind.
    execution_id: String,
    /// Immutable original resources retained in that Runtime execution context.
    resource_ids: Vec<domain::ResourceId>,
    /// Original actual admission clock inside the existing nonrenewable switch budget.
    admitted_kernel: KernelTime,
}

/// Complete original permission retained before candidate output and independent Review.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Authorization {
    /// Exact original administrative semantic input.
    request: domain::ExecutionCoordinationRequest,
    /// Original command admitted through the real revision journal.
    command_id: String,
    /// Actual original administrator and leaf.
    principal: String,
    /// Exact authenticated original TLS certificate.
    certificate_digest: String,
    /// Original actual receive time inside the existing candidate budget.
    admitted_at: domain::TimestampMs,
    /// Original same-kernel budget witness; it cannot renew the ticket.
    admitted_kernel: KernelTime,
    /// Complete original source Group and membership.
    source_group: domain::ExecutionGroupId,
    /// Exact source membership generation.
    membership: domain::GroupMembershipVersion,
    /// Original source plan revision.
    source_revision: u64,
    /// Complete current attempts, including terminal unsatisfied attempts.
    attempts: Vec<FrozenAttempt>,
    /// Actual operation-owned safety support frozen before the transition.
    safety: BTreeMap<String, domain::OperationSafetySupport>,
    /// Actual accepted beneficiary and original resource evidence, only for preemption.
    beneficiary: Option<Beneficiary>,
    /// Immutable original permission digest, included in independent Review.
    digest: String,
}

/// Hashes the complete original declaration, never deriving it from a later current registration.
pub(super) fn authorization_digest(candidate: &Candidate) -> Result<Option<String>, String> {
    candidate
        .coordination
        .as_ref()
        .map(|record| {
            let mut original = record.clone();
            original.digest.clear();
            let calculated = digest(&original)?;
            if calculated != record.digest {
                return Err("original coordination permission digest changed".into());
            }
            Ok(calculated)
        })
        .transpose()
}

/// Computes a current same-tenant higher-priority Ready beneficiary through normal operation matching.
fn beneficiary(
    controller: &ControllerState,
    mission: &domain::MissionId,
    target: &domain::MissionId,
    now: domain::TimestampMs,
) -> Result<Beneficiary, String> {
    let source = controller
        .orchestrator
        .execution(mission)
        .ok_or("source Mission absent")?;
    let target_execution = controller
        .orchestrator
        .execution(target)
        .ok_or("beneficiary absent")?;
    if target == mission
        || !matches!(
            target_execution.lifecycle(),
            orchestration::MissionExecutionLifecycle::Accepted
                | orchestration::MissionExecutionLifecycle::Running
        )
        || target_execution.isolation().is_some()
        || controller.bridge.control().mission_tenant(target)
            != controller.bridge.control().mission_tenant(mission)
        || target_execution.dispatch_policy().priority() <= source.dispatch_policy().priority()
        || blocks_mission(controller, target)
    {
        return Err(
            "running preemption requires an active same-tenant higher-priority beneficiary".into(),
        );
    }
    let control = controller.bridge.control();
    let ready = controller.orchestrator.ready_tasks(target, control);
    let mut conflicts = BTreeSet::new();
    let trace = domain::CorrelationId::new("running-preemption-evidence").expect("fixed trace");
    for task in target_execution
        .plan()
        .task_graph()
        .tasks()
        .iter()
        .filter(|task| {
            ready.contains(task.requirement().task_ref())
                && target_execution
                    .accepted_at()
                    .as_millis()
                    .checked_add(task.requirement().timing().earliest_start_offset_ms())
                    .is_some_and(|start| start <= now.as_millis())
                && task
                    .requirement()
                    .timing()
                    .latest_start_offset_ms()
                    .is_none_or(|offset| {
                        target_execution
                            .accepted_at()
                            .as_millis()
                            .checked_add(offset)
                            .is_some_and(|end| now.as_millis() <= end)
                    })
        })
    {
        if let Ok(candidates) = control.match_capabilities_for_mission(
            &controller.bridge.decision_state(),
            target_execution.plan(),
            task.requirement(),
            now,
            &trace,
            &mut controller.bridge.event_sink().clone(),
        ) {
            conflicts.extend(control.preemption_conflicts(
                &controller.bridge.decision_state(),
                source.group_id(),
                task.requirement(),
                &candidates,
            ));
        }
    }
    if conflicts.is_empty() {
        return Err("beneficiary has no current matched Ready Role blocked by the original exclusive resources".into());
    }
    Ok(Beneficiary {
        mission: target.clone(),
        group: target_execution.group_id().clone(),
        revision: target_execution.plan_revision(),
        plan_digest: orchestration::mission_plan_digest(target_execution.plan()),
        priority: target_execution.dispatch_policy().priority(),
        source_priority: source.dispatch_policy().priority(),
        conflicts,
    })
}

/// Freezes current original attempts and their declared technical support without admitting any stop.
#[allow(clippy::too_many_arguments)]
pub(super) fn authorize(
    controller: &ControllerState,
    mission: &domain::MissionId,
    state: &mut RevisionState,
    command: &Command,
    caller: &Caller<'_>,
    ticket_id: &str,
    request: &domain::ExecutionCoordinationRequest,
    now: domain::TimestampMs,
    kernel: &KernelTime,
) -> Result<serde_json::Value, String> {
    request.validate()?;
    if command.schema_version != COORDINATION_SCHEMA || caller.role != "recovery-admin" {
        return Err(
            "coordination requires explicit v0.6 and actual recovery-admin authority".into(),
        );
    }
    let candidate = state
        .candidates
        .get(ticket_id)
        .ok_or("original coordination ticket absent")?;
    if candidate.coordination.is_some()
        || candidate.completed_time.is_some()
        || candidate.claim.is_some()
        || candidate.review.is_some()
        || candidate.budget.remaining(kernel) == 0
    {
        return Err("coordination must precede original output, provider claim and Review".into());
    }
    let execution = controller
        .orchestrator
        .execution(mission)
        .ok_or("source Mission absent")?;
    if candidate.base_revision != execution.plan_revision()
        || candidate.base_digest != orchestration::mission_plan_digest(execution.plan())
        || execution.lifecycle() != orchestration::MissionExecutionLifecycle::Running
        || blocks_mission(controller, mission)
        || super::super::memory_handoff::holds_binding(controller, execution.group_id())
    {
        return Err("coordination source revision, Group or current ownership changed".into());
    }
    let attempts = switching::attempts(controller, execution.group_id())?;
    if attempts.is_empty() || attempts.len() > 256 {
        return Err("coordination requires a bounded actual original cohort".into());
    }
    if request.kind == domain::ExecutionCoordinationKind::CoupledRecovery
        && (attempts.len() < 2
            || !(execution.plan().contexts().iter().any(|context| {
                context.coupling_mode() != domain::ExecutionCouplingMode::Independent
            }) || execution.plan().task_graph().tasks().iter().any(|task| {
                task.continuity()
                    .coupling_mode_override()
                    .is_some_and(|mode| mode != domain::ExecutionCouplingMode::Independent)
            })))
    {
        return Err("coupled recovery requires multiple actual original Role attempts".into());
    }
    let history = controller.bridge.attempt_history();
    let mut safety = BTreeMap::new();
    for original in &attempts {
        let actual = history
            .iter()
            .find(|attempt| attempt.execution_id() == original.execution_id)
            .ok_or("original attempt absent")?;
        if actual.command().recovery_support().is_none_or(|support| {
            support.support.continuation != domain::ExecutionContinuation::RepeatAfterStop
                || support.support.stop_scope == domain::ExecutionStopScope::Unsupported
        }) {
            return Err(
                "original operation cannot preserve interrupted context after stopping".into(),
            );
        }
        let (_, support, _) =
            super::super::safety_stop::safety_declaration(controller, actual.command())
                .ok_or("original operation-owned safe-state support unavailable")?;
        if support.assurance != request.stop_assurance {
            return Err(
                "operation-owned stopping assurance does not match the required coordination class"
                    .into(),
            );
        }
        safety.insert(original.execution_id.clone(), support);
    }
    let beneficiary = request
        .beneficiary_mission_id
        .as_ref()
        .map(|target| beneficiary(controller, mission, target, now))
        .transpose()?;
    if beneficiary.is_some() && execution.isolation().is_some() {
        return Err("running preemption cannot replace an already held source purpose".into());
    }
    let mut record = Authorization {
        request: request.clone(),
        command_id: command.command_id.clone(),
        principal: caller.principal.into(),
        certificate_digest: caller.certificate_digest.into(),
        admitted_at: now,
        admitted_kernel: kernel.clone(),
        source_group: execution.group_id().clone(),
        membership: controller
            .bridge
            .control()
            .group(execution.group_id())
            .and_then(|group| group.membership_version())
            .ok_or("source membership absent")?
            .clone(),
        source_revision: execution.plan_revision(),
        attempts,
        safety,
        beneficiary,
        digest: String::new(),
    };
    record.digest = digest(&record)?;
    let retained = controller
        .runtime_revisions
        .values()
        .chain(std::iter::once(&*state))
        .try_fold(0_usize, |total, state| {
            retained_bytes(state).map(|bytes| total.saturating_add(bytes))
        })?;
    if retained.saturating_add(
        serde_json::to_vec(&record)
            .map_err(|error| error.to_string())?
            .len(),
    ) > MAX_RETAINED_COORDINATION_BYTES
    {
        return Err("original complete coordination metadata budget exhausted".into());
    }

    let response = serde_json::json!({"status":"coordination_authorized","ticket_id":ticket_id,"coordination_digest":record.digest,"source_group_id":record.source_group,"kind":record.request.kind,"permission_is_stop_proof":false});
    state
        .candidates
        .get_mut(ticket_id)
        .expect("original ticket")
        .coordination = Some(record);
    Ok(response)
}

/// Rechecks the frozen complete source, original support, successor operations and beneficiary before effects.
pub(super) fn validate_candidate(
    controller: &ControllerState,
    mission: &domain::MissionId,
    candidate: &Candidate,
    plan: &domain::MissionPlan,
    now: domain::TimestampMs,
) -> Result<(), String> {
    let Some(original) = &candidate.coordination else {
        return Ok(());
    };
    authorization_digest(candidate)?;
    original.request.validate()?;
    let execution = controller
        .orchestrator
        .execution(mission)
        .ok_or("source Mission absent")?;
    if execution.group_id() != &original.source_group
        || execution.plan_revision() != original.source_revision
        || controller
            .bridge
            .control()
            .group(&original.source_group)
            .and_then(|group| group.membership_version())
            != Some(&original.membership)
        || switching::attempts(controller, &original.source_group)? != original.attempts
    {
        return Err("original complete coordination cohort or membership changed".into());
    }
    let completed = admission::completed(controller, mission)?;
    if plan
        .task_graph()
        .tasks()
        .iter()
        .filter(|task| !completed.contains(task.task_id()))
        .any(|task| {
            task.execution_intents().values().any(|intent| {
                !original
                    .request
                    .continuation_operations
                    .contains(intent.operation())
            })
        })
    {
        return Err(
            "successor operations exceed the exact reviewed coordination permission".into(),
        );
    }
    if (original.request.stop_assurance == domain::SafetyAssurance::DeviceObservation
        || matches!(
            original.request.kind,
            domain::ExecutionCoordinationKind::Handoff | domain::ExecutionCoordinationKind::Regrasp
        ))
        && candidate.memory.is_empty()
    {
        return Err("device coordination requires original context through the reviewed Memory transfer contract".into());
    }
    let history = controller.bridge.attempt_history();
    for attempt in &original.attempts {
        let actual = history
            .iter()
            .find(|actual| actual.execution_id() == attempt.execution_id)
            .ok_or("original Runtime attempt absent")?;
        let (_, support, _) =
            super::super::safety_stop::safety_declaration(controller, actual.command())
                .ok_or("current original safety declaration unavailable")?;
        if original.safety.get(&attempt.execution_id) != Some(&support) {
            return Err("original operation-owned coordination safety support changed".into());
        }
    }
    if let Some(expected) = &original.beneficiary
        && beneficiary(controller, mission, &expected.mission, now)? != *expected
    {
        return Err("original beneficiary priority, plan or exclusive blocker changed".into());
    }
    Ok(())
}

/// Independently joins retained original permissions to command receipts, source plans and Runtime history.
pub(super) fn validate_restored(
    controller: &ControllerState,
    mission: &domain::MissionId,
    state: &RevisionState,
    candidate: &Candidate,
) -> Result<(), String> {
    let Some(record) = &candidate.coordination else {
        if candidate
            .review
            .as_ref()
            .is_some_and(|review| review.coordination_digest.is_some())
        {
            return Err("Review lost its original coordination permission".into());
        }
        return Ok(());
    };
    record.request.validate()?;
    record.membership.validate()?;
    let identity = authorization_digest(candidate)?;
    let receipt = state
        .commands
        .get(&record.command_id)
        .ok_or("original coordination command absent")?;
    let original_command = Command {
        schema_version: COORDINATION_SCHEMA.into(),
        command_id: record.command_id.clone(),
        expected_revision: receipt.result["revision"]
            .as_u64()
            .and_then(|revision| revision.checked_sub(1))
            .ok_or("original coordination revision absent")?,
        action: Action::AuthorizeCoordination {
            ticket_id: candidate.ticket_id.clone(),
            authorization: record.request.clone(),
        },
    };
    if receipt.digest != digest(&original_command)? {
        return Err(
            "original coordination request differs from its retained command digest".into(),
        );
    }
    let execution = controller
        .orchestrator
        .execution(mission)
        .ok_or("original coordination Mission absent")?;
    let source_group = if record.source_revision == execution.plan_revision() {
        execution.group_id()
    } else {
        &execution
            .plan_revisions()
            .get(
                usize::try_from(record.source_revision)
                    .map_err(|_| "original revision overflow")?,
            )
            .ok_or("original coordination version absent")?
            .source_group_id
    };
    if source_group != &record.source_group {
        return Err("coordination source Group differs from original accepted version".into());
    }
    for switch in state
        .switches
        .iter()
        .filter(|switch| switch.ticket_id == candidate.ticket_id)
    {
        if switch.attempts != record.attempts
            || switch.membership != record.membership
            || switch.source_group != record.source_group
        {
            return Err("coordination switch changed complete original cohort coverage".into());
        }
    }
    if receipt.role != "recovery-admin"
        || receipt.principal != record.principal
        || receipt.leaf_digest != record.certificate_digest
        || receipt.received_at != record.admitted_at
        || receipt.result["schema_version"] != COORDINATION_SCHEMA
        || receipt.result["coordination_digest"].as_str() != identity.as_deref()
        || receipt.result["ticket_id"] != candidate.ticket_id
        || record.source_revision != candidate.base_revision
        || record.attempts.is_empty()
        || record.attempts.len() > 256
        || record.safety.len() != record.attempts.len()
        || candidate.budget.remaining(&record.admitted_kernel) == 0
        || record.admitted_at < state.accepted_at
        || candidate
            .review
            .as_ref()
            .is_some_and(|review| review.coordination_digest != identity)
        || (record.request.kind == domain::ExecutionCoordinationKind::RunningPreemption)
            != record.beneficiary.is_some()
    {
        return Err(
            "retained coordination command, original clock or independent Review differs".into(),
        );
    }
    let history = controller.bridge.attempt_history();
    let mut ids = BTreeSet::new();
    for attempt in &record.attempts {
        let actual = history
            .iter()
            .find(|actual| actual.execution_id() == attempt.execution_id)
            .ok_or("retained coordination lost original Runtime history")?;
        if super::super::safety_stop::original_support(controller, &attempt.execution_id)
            .is_some_and(|support| record.safety.get(&attempt.execution_id) != Some(support))
        {
            return Err("coordination original stop assurance differs from its original operation-owned declaration".into());
        }
        if !ids.insert(&attempt.execution_id)
            || actual.command().group_id() != &record.source_group
            || actual.command().task_ref() != &attempt.task_ref
            || attempt.task_ref.mission_id() != mission
            || actual.command().role_id() != &attempt.role_id
            || actual.command().node_id() != &attempt.node_id
            || actual.command().execution_generation() != Some(attempt.generation)
            || actual.command().recovery_support().is_none_or(|support| {
                support.support.continuation != domain::ExecutionContinuation::RepeatAfterStop
                    || support.support.stop_scope == domain::ExecutionStopScope::Unsupported
            })
            || record
                .safety
                .get(&attempt.execution_id)
                .is_none_or(|support| {
                    support.assurance != record.request.stop_assurance
                        || &support.operation != actual.command().intent().operation()
                })
        {
            return Err(
                "retained coordination changed its original complete attempt or assurance".into(),
            );
        }
    }
    Ok(())
}

/// Commits the beneficiary first through normal Control, or lets the original outer transaction roll back.
pub(super) fn admit_beneficiary(
    controller: &mut ControllerState,
    candidate: &Candidate,
    switch: &Switch,
    now: domain::TimestampMs,
    trace: &domain::CorrelationId,
) -> Result<Option<BeneficiaryAdmission>, String> {
    let Some(beneficiary) = candidate
        .coordination
        .as_ref()
        .and_then(|record| record.beneficiary.as_ref())
    else {
        return Ok(None);
    };
    let prior: BTreeSet<_> = controller
        .bridge
        .attempt_history()
        .iter()
        .map(|attempt| attempt.execution_id().to_string())
        .collect();
    super::super::dispatch::drive_ready_mission(
        controller,
        &beneficiary.mission,
        now,
        trace,
        &mut controller.bridge.event_sink().clone(),
    )
    .map_err(|error| error.to_string())?;
    let admitted_kernel = KernelTime::observe()?;
    if switch.budget.remaining(&admitted_kernel) == 0 {
        return Err(
            "original running-preemption switch budget expired before normal beneficiary admission"
                .into(),
        );
    }
    for actual in controller.bridge.attempt_history() {
        if actual.command().group_id() == &beneficiary.group
            && !prior.contains(actual.execution_id())
            && let Some(resources) = controller
                .bridge
                .attempt_resource_ids(actual.execution_id())
            && resources
                .iter()
                .any(|identity| beneficiary.conflicts.contains(identity))
        {
            return Ok(Some(BeneficiaryAdmission {
                execution_id: actual.execution_id().into(),
                resource_ids: resources.to_vec(),
                admitted_kernel,
            }));
        }
    }
    Err("original beneficiary has not obtained the conflicting resources through normal Control; switch remains uncommitted".into())
}

/// Joins an applied preemption to its exact original beneficiary resource context without renewing authority.
pub(super) fn validate_beneficiary_admission(
    controller: &ControllerState,
    candidate: &Candidate,
    switch: &Switch,
) -> Result<(), String> {
    let beneficiary = candidate
        .coordination
        .as_ref()
        .and_then(|record| record.beneficiary.as_ref());
    match (&switch.beneficiary_admission, beneficiary, switch.phase) {
        (Some(proof), Some(beneficiary), Phase::Applied) => {
            let history = controller.bridge.attempt_history();
            let actual = history
                .iter()
                .find(|attempt| attempt.execution_id() == proof.execution_id)
                .ok_or("applied preemption lost original beneficiary Runtime intent")?;
            if actual.command().group_id() != &beneficiary.group
                || actual.command().mission_id() != &beneficiary.mission
                || controller.bridge.attempt_resource_ids(&proof.execution_id)
                    != Some(proof.resource_ids.as_slice())
                || !proof
                    .resource_ids
                    .iter()
                    .any(|identity| beneficiary.conflicts.contains(identity))
                || switch.budget.remaining(&proof.admitted_kernel) == 0
            {
                return Err(
                    "applied preemption changed original beneficiary Commit/Bind or clock evidence"
                        .into(),
                );
            }
        }
        (None, Some(_), Phase::Applied) => {
            return Err(
                "applied preemption lacks its actual original beneficiary admission".into(),
            );
        }
        (None, _, _) => {}
        _ => {
            return Err(
                "beneficiary admission is hidden outside its original applied preemption".into(),
            );
        }
    }
    Ok(())
}

/// Counts retained original cohort metadata without deleting history or renewing admission capacity.
pub(super) fn retained_bytes(state: &RevisionState) -> Result<usize, String> {
    state
        .candidates
        .values()
        .filter_map(|candidate| candidate.coordination.as_ref())
        .try_fold(0_usize, |total, record| {
            serde_json::to_vec(record)
                .map(|bytes| total.saturating_add(bytes.len()))
                .map_err(|error| error.to_string())
        })
}
