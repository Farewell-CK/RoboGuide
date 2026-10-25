//! Original-owner scoped children for a new revision Group; no provisional physical binding.

use super::*;

/// Keeps original revision declarations pinned until explicit policy revocation and complete closure.
/// Historical completed plans retain their source/goal evidence without requiring deleted payloads.
pub(crate) fn catalog_retirement_evidence(
    controller: &ControllerState,
    selector: &MemorySelector,
) -> Result<Vec<serde_json::Value>, String> {
    let mut result = Vec::new();
    for (mission, state) in &controller.runtime_revisions {
        if !state
            .candidates
            .values()
            .flat_map(|candidate| candidate.memory.values())
            .any(|record| {
                record.parent.selector() == selector || record.request.child.selector() == selector
            })
        {
            continue;
        }
        let mission = domain::MissionId::new(mission).map_err(|error| error.to_string())?;
        let execution = controller
            .orchestrator
            .execution(&mission)
            .ok_or("original revision Mission is unavailable")?;
        if !state.revoked
            || !matches!(
                execution.lifecycle(),
                orchestration::MissionExecutionLifecycle::Completed
                    | orchestration::MissionExecutionLifecycle::Failed
                    | orchestration::MissionExecutionLifecycle::Cancelled
            )
            || state
                .switches
                .iter()
                .any(|switch| !matches!(switch.phase, Phase::Applied | Phase::Cancelled))
        {
            return Err("original runtime revision still retains its Catalog declarations".into());
        }
        super::goal::validate_restored(controller, &mission, state)?;
        if execution.lifecycle() == orchestration::MissionExecutionLifecycle::Completed
            && (execution.goal_gate() != Some(state.goal_contract_digest.as_str())
                || execution.goal_receipt_digest().is_none_or(|receipt| {
                    state
                        .goal_evidence
                        .get(receipt)
                        .is_none_or(|proof| proof.completed_time.is_none())
                }))
        {
            return Err(
                "original revision lacks its independent original-goal completion proof".into(),
            );
        }
        result.push(serde_json::json!({"mission":mission,"revision":state,"execution":execution}));
    }
    Ok(result)
}
use domain::{MemoryArtifactManifest, MemoryScope, MemorySelector};
use ports::EventSink;

/// Complete immutable pre-output permission; a future Group identifier is not a reservation.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct AuthorizationRequest {
    /// Exact original published source.
    parent_selector: MemorySelector,
    /// Exact planned receiving scope, independently checked against the eventual switch.
    target_group_id: domain::ExecutionGroupId,
    /// Complete new immutable metadata retaining the original bytes and production identity.
    child: MemoryArtifactManifest,
    /// Complete immutable original Spatial source, mandatory only under explicit v0.4.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    typed_map: Option<domain::MapArtifactManifest>,
    /// Exact original authorization registry, not an unversioned deployment hint.
    registry_id: String,
    /// Expected complete original policy revision.
    registry_revision: u64,
}

/// Original authenticated administrative declaration retained independently of publication progress.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Authorization {
    /// Exact original command identity and input digest.
    command_id: String,
    /// Complete original typed command identity.
    command_digest: String,
    /// Original complete requested child and policy.
    request: AuthorizationRequest,
    /// Exact published source retained even after its original Group is superseded.
    parent: MemoryArtifactManifest,
    /// Original actual source Group, never guessed from the target or model output.
    source_group: domain::ExecutionGroupId,
    /// Exact original Control membership at admission.
    membership: domain::GroupMembershipVersion,
    /// Actual original administrative stable principal.
    principal: String,
    /// Actual transport leaf admitted under the original application writer.
    leaf_digest: String,
    /// Original leaf expiry bounds the pre-publication permission.
    certificate_expires_at_ms: u64,
    /// Original actual Controller receive time.
    admitted_at: domain::TimestampMs,
    /// Actual original same-kernel sample, not a caller-supplied timestamp.
    admitted_kernel: KernelTime,
    /// Bounded by the original candidate, policy and actual certificate; never renewed.
    budget: OriginalBudget,
    /// Canonical immutable complete evidence digest, computed with this field empty.
    digest: String,
}

impl Authorization {
    /// Exposes only the presence of complete typed source authority to the wrapper fence.
    pub(super) fn uses_typed_map(&self) -> bool {
        self.request.typed_map.is_some()
    }

    /// Keeps historical v0.3 command bytes unchanged rather than relabelling their schema.
    fn schema(&self) -> &'static str {
        if self.uses_typed_map() {
            TYPED_MEMORY_SCHEMA
        } else {
            MEMORY_SCHEMA
        }
    }
}

/// Returns the exact immutable child permissions included in Review, preserving legacy absence.
pub(super) fn authorization_digest(candidate: &Candidate) -> Result<Option<String>, String> {
    if candidate.memory.is_empty() {
        Ok(None)
    } else {
        digest(&candidate.memory).map(Some)
    }
}

/// Requires Review to cover every original child in the unchanged unfinished semantic plan.
pub(super) fn validate_review(
    candidate: &Candidate,
    completed: &BTreeSet<domain::TaskId>,
) -> Result<(), String> {
    if candidate.memory.is_empty() {
        return Ok(());
    }
    let plan = orchestration::decode_mission_plan(
        &candidate
            .plan
            .as_ref()
            .ok_or("candidate plan absent")?
            .to_string(),
    )
    .map_err(|e| e.to_string())?;
    let mut used = BTreeSet::new();
    for task in plan
        .task_graph()
        .tasks()
        .iter()
        .filter(|task| !completed.contains(task.task_id()))
    {
        for input in task
            .requirement()
            .roles()
            .iter()
            .flat_map(|role| role.memories())
        {
            let key = input.selector.to_string();
            if let Some(record) = candidate.memory.get(&key) {
                if !input.matches_manifest(&record.request.child) {
                    return Err(
                        "Review child contract differs from its original declaration".into(),
                    );
                }
                used.insert(key);
            }
        }
    }
    if used.len() != candidate.memory.len() {
        return Err("Review must directly consume every original child in unfinished Tasks".into());
    }
    Ok(())
}

/// Requires current original source, policy and administrator before any publication decision.
#[allow(clippy::too_many_arguments)]
fn current(
    controller: &ControllerState,
    mission: &domain::MissionId,
    state: &RevisionState,
    candidate: &Candidate,
    authorization: &Authorization,
    now: domain::TimestampMs,
    kernel: &KernelTime,
) -> Result<(), String> {
    let execution = controller
        .orchestrator
        .execution(mission)
        .ok_or("source Mission absent")?;
    let registry = controller
        .bridge
        .control()
        .memory_authorization_registry()
        .ok_or("Memory policy absent")?;
    if execution.group_id() != &authorization.source_group
        || execution.plan_revision() != candidate.base_revision
        || orchestration::mission_plan_digest(execution.plan()) != candidate.base_digest
        || registry.registry_id != authorization.request.registry_id
        || registry.revision != authorization.request.registry_revision
        || authorization.budget.remaining(kernel) == 0
        || candidate.budget.remaining(kernel) == 0
        || state.budget.remaining(kernel) == 0
        || now.as_millis() >= authorization.certificate_expires_at_ms
        || !controller
            .bridge
            .control()
            .operator_authorization()
            .and_then(|registry| registry.grants.get(&authorization.leaf_digest))
            .is_some_and(|grant| {
                grant.active(now.as_millis())
                    && grant.principal_id == authorization.principal
                    && grant.role == "recovery-admin"
                    && (grant.tenant_ids.contains("*") || grant.tenant_ids.contains(&state.tenant))
            })
        || !admission::authorized_subject(
            controller,
            &authorization.principal,
            &state.tenant,
            true,
            now,
        )
    {
        return Err("original revision Memory authority, policy, source or budget changed".into());
    }
    let catalog = controller
        .memory_preparation
        .catalog
        .as_ref()
        .ok_or("Memory catalog absent")?;
    let (parent, map) = catalog
        .memory_source(&authorization.request.parent_selector)?
        .ok_or("original Memory source absent")?;
    if parent != authorization.parent || map != authorization.request.typed_map {
        return Err("original complete Memory source changed".into());
    }
    if let Some(map) = &authorization.request.typed_map {
        validate_typed_source_reference(controller, authorization)?;
        return controller
            .bridge
            .control()
            .authorize_revision_typed_map_child(
                map,
                &authorization.request.child,
                &authorization.source_group,
                &authorization.membership,
                mission,
                &state.tenant,
                &authorization.principal,
            )
            .map_err(|e| e.to_string());
    }
    controller
        .bridge
        .control()
        .authorize_revision_memory_child(
            &authorization.parent,
            &authorization.request.child,
            &authorization.source_group,
            &authorization.membership,
            mission,
            &state.tenant,
            &authorization.principal,
        )
        .map_err(|e| e.to_string())
}

/// Requires an actual original source-Group preparation, never merely a discoverable map label.
fn validate_typed_source_reference(
    controller: &ControllerState,
    record: &Authorization,
) -> Result<(), String> {
    let Some(map) = &record.request.typed_map else {
        return Ok(());
    };
    if controller
        .memory_preparation
        .records
        .values()
        .any(|preparation| {
            preparation.preparation.as_ref().is_some_and(|preparation| {
                preparation.group_id == record.source_group
                    && preparation.inputs.iter().any(|input| {
                        input.manifest == record.parent && input.typed_map.as_ref() == Some(map)
                    })
            })
        })
    {
        Ok(())
    } else {
        Err("typed-map revision lacks its exact original source-Group preparation".into())
    }
}

/// Freezes only an actual authorized original source before the candidate can claim provider work.
#[allow(clippy::too_many_arguments)]
pub(super) fn authorize(
    controller: &ControllerState,
    mission: &domain::MissionId,
    state: &mut RevisionState,
    command: &Command,
    caller: &Caller<'_>,
    ticket: &str,
    request: &AuthorizationRequest,
    now: domain::TimestampMs,
    kernel: &KernelTime,
) -> Result<serde_json::Value, String> {
    if caller.role != "recovery-admin"
        || if request.typed_map.is_some() {
            command.schema_version != TYPED_MEMORY_SCHEMA
        } else {
            command.schema_version != MEMORY_SCHEMA
        }
    {
        return Err(
            "revision Memory requires its explicit source contract and actual recovery-admin"
                .into(),
        );
    }
    let candidate = state
        .candidates
        .get(ticket)
        .ok_or("original candidate absent")?;
    if candidate.claim.is_some()
        || candidate.completed_time.is_some()
        || candidate.review.is_some()
        || !candidate.provider_calls.is_empty()
        || candidate.memory.len() >= 16
        || !matches!(request.child.scope(), MemoryScope::ExecutionGroup(group) if group == &request.target_group_id)
    {
        return Err(
            "Memory must be declared before original output, claim and Review within its bound"
                .into(),
        );
    }
    let catalog = controller
        .memory_preparation
        .catalog
        .as_ref()
        .ok_or("Memory catalog absent")?;
    let (parent, map) = catalog
        .memory_source(&request.parent_selector)?
        .ok_or("original Memory source absent")?;
    let key = request.child.selector().to_string();
    if map != request.typed_map
        || catalog.memory_source(request.child.selector())?.is_some()
        || state
            .candidates
            .values()
            .chain(
                controller
                    .runtime_revisions
                    .values()
                    .flat_map(|state| state.candidates.values()),
            )
            .any(|candidate| candidate.memory.contains_key(&key))
    {
        return Err(
            "revision child requires an exact complete source and a new unique selector".into(),
        );
    }
    let execution = controller
        .orchestrator
        .execution(mission)
        .ok_or("source Mission absent")?;
    let source_group = execution.group_id().clone();
    let membership = controller
        .bridge
        .control()
        .group(&source_group)
        .and_then(|group| group.membership_version())
        .cloned()
        .ok_or("source membership absent")?;
    let duration = candidate
        .budget
        .remaining(kernel)
        .min(state.budget.remaining(kernel))
        .min(
            caller
                .certificate_expires_at_ms
                .saturating_sub(now.as_millis()),
        );
    let mut record = Authorization {
        command_id: command.command_id.clone(),
        command_digest: digest(command)?,
        request: request.clone(),
        parent,
        source_group,
        membership,
        principal: caller.principal.into(),
        leaf_digest: caller.certificate_digest.into(),
        certificate_expires_at_ms: caller.certificate_expires_at_ms,
        admitted_at: now,
        admitted_kernel: kernel.clone(),
        budget: OriginalBudget::new(kernel, duration)?,
        digest: String::new(),
    };
    current(controller, mission, state, candidate, &record, now, kernel)?;
    record.digest = digest(&record)?;
    let retained = state
        .candidates
        .values()
        .flat_map(|candidate| candidate.memory.values())
        .chain(
            controller
                .runtime_revisions
                .values()
                .flat_map(|state| state.candidates.values())
                .flat_map(|candidate| candidate.memory.values()),
        )
        .try_fold(
            serde_json::to_vec(&record)
                .map_err(|e| e.to_string())?
                .len(),
            |size, row| {
                Ok::<_, String>(
                    size.saturating_add(serde_json::to_vec(row).map_err(|e| e.to_string())?.len()),
                )
            },
        )?;
    if retained > 512 * 1024 {
        return Err("retained revision Memory metadata exceeds the deployment bound".into());
    }
    let result = serde_json::json!({"status":"revision_memory_authorized","ticket_id":ticket,
        "memory_authorization_digest":record.digest,"child":record.request.child,"target_group_id":record.request.target_group_id});
    state
        .candidates
        .get_mut(ticket)
        .expect("original candidate")
        .memory
        .insert(key, record);
    Ok(result)
}

/// Checks every unfinished exact input, preserving scope and refusing unused side-effect declarations.
#[allow(clippy::too_many_arguments)]
pub(super) fn validate_inputs(
    controller: &ControllerState,
    mission: &domain::MissionId,
    state: &RevisionState,
    candidate: &Candidate,
    plan: &domain::MissionPlan,
    target: &domain::ExecutionGroupId,
    completed: &BTreeSet<domain::TaskId>,
    now: domain::TimestampMs,
    kernel: &KernelTime,
) -> Result<(), String> {
    let mut used = BTreeSet::new();
    for task in plan
        .task_graph()
        .tasks()
        .iter()
        .filter(|task| !completed.contains(task.task_id()))
    {
        for input in task
            .requirement()
            .roles()
            .iter()
            .flat_map(|role| role.memories())
        {
            let key = input.selector.to_string();
            let manifest = if let Some(record) = candidate.memory.get(&key) {
                current(controller, mission, state, candidate, record, now, kernel)?;
                if &record.request.target_group_id != target
                    || !input.matches_manifest(&record.request.child)
                {
                    return Err(
                        "candidate changed its original declared child or target Group".into(),
                    );
                }
                used.insert(key);
                record.request.child.clone()
            } else {
                controller
                    .memory_preparation
                    .catalog
                    .as_ref()
                    .ok_or("Memory catalog absent")?
                    .memory_source(&input.selector)?
                    .ok_or("candidate Memory source absent")?
                    .0
            };
            if matches!(manifest.scope(), MemoryScope::ExecutionGroup(owner) if owner != target) {
                return Err("Group-scoped input requires an explicit original-owner handoff before revision switching".into());
            }
        }
    }
    if used.len() != candidate.memory.len() {
        return Err("reviewed revision does not consume every original declared child".into());
    }
    Ok(())
}

/// Verifies actual source bytes and appends only publication events inside the original SQL transaction.
pub(super) fn publish(
    controller: &ControllerState,
    mission: &domain::MissionId,
    state: &RevisionState,
    candidate: &Candidate,
    switch: &Switch,
    now: domain::TimestampMs,
) -> Result<(), String> {
    for authorization in candidate.memory.values() {
        if authorization.membership != switch.membership
            || authorization.source_group != switch.source_group
            || authorization.request.target_group_id != switch.target_group
        {
            return Err("revision Memory original source or switch membership differs".into());
        }
        let kernel = KernelTime::observe()?;
        current(
            controller,
            mission,
            state,
            candidate,
            authorization,
            now,
            &kernel,
        )?;
        let catalog = controller
            .memory_preparation
            .catalog
            .as_ref()
            .ok_or("Memory catalog absent")?;
        catalog.validate_application_memory_publication(&authorization.request.child)?;
        let artifact = authorization
            .parent
            .artifact()
            .ok_or("revision parent has no bytes")?;
        controller
            .verifier_artifact_store
            .as_ref()
            .ok_or("revision CAS unavailable")?
            .verify_artifact(artifact.content_digest().as_str(), artifact.byte_size())
            .map_err(|error| error.to_string())?;
        let reference_events = catalog.publication_reference_events(
            &authorization.request.child,
            std::slice::from_ref(&authorization.parent),
            None,
            &artifact_http::ArtifactPublicationSource {
                tenant_id: state.tenant.clone(),
                principal_id: authorization.principal.clone(),
                authority_digest: authorization.digest.clone(),
            },
            now,
        )?;
        let after = KernelTime::observe()?;
        current(
            controller,
            mission,
            state,
            candidate,
            authorization,
            now,
            &after,
        )?;
        if switch.budget.remaining(&after) == 0 {
            return Err("original revision stop/publication budget expired".into());
        }
        for event in reference_events {
            controller.bridge.event_sink().clone().append(
                now,
                &domain::CorrelationId::new("revision-memory-publication")
                    .expect("fixed correlation"),
                None,
                event,
            );
        }
        controller.bridge.event_sink().clone().append(
            now,
            &domain::CorrelationId::new("revision-memory-publication").expect("fixed correlation"),
            None,
            domain::EventPayload::MemoryManifestPublished {
                manifest: authorization.request.child.clone(),
            },
        );
    }
    Ok(())
}

/// Reinstalls only actually committed publication evidence after Commit or restore, never staged declarations.
pub(crate) fn install_catalog(controller: &ControllerState) -> Result<(), String> {
    for (mission, state) in &controller.runtime_revisions {
        for switch in state
            .switches
            .iter()
            .filter(|switch| switch.phase == Phase::Applied)
        {
            let candidate = &state.candidates[&switch.ticket_id];
            if candidate.memory.is_empty() {
                continue;
            }
            let mission = domain::MissionId::new(mission).map_err(|e| e.to_string())?;
            let version = controller
                .orchestrator
                .execution(&mission)
                .and_then(|execution| {
                    execution
                        .plan_revisions()
                        .iter()
                        .find(|version| version.command_id == switch.command_id)
                })
                .ok_or("revision child has no committed plan version")?;
            let catalog = controller
                .memory_preparation
                .catalog
                .as_ref()
                .ok_or("revision Memory catalog absent")?;
            for record in candidate.memory.values() {
                catalog.install_application_memory_publication(
                    &record.request.child,
                    version.committed_at,
                )?;
            }
        }
    }
    Ok(())
}

/// Grants an actual current child import its exact original-parent CAS reference exception.
pub(crate) fn permits_parent_reference(
    controller: &ControllerState,
    protected: &MemoryArtifactManifest,
    import_id: &str,
) -> Result<bool, String> {
    let Some(import) = controller.memory_imports.get(import_id) else {
        return Ok(false);
    };
    for state in controller.runtime_revisions.values() {
        for switch in state.switches.iter().filter(|switch| {
            switch.phase == Phase::Applied && switch.target_group == import.request.group_id
        }) {
            for record in state.candidates[&switch.ticket_id].memory.values() {
                if &record.parent != protected || record.request.child != import.request.manifest {
                    continue;
                }
                if switch
                    .attempts
                    .iter()
                    .map(|attempt| &attempt.execution_id)
                    .chain(switch.prior_attempts.iter())
                    .any(|id| {
                        !super::super::safety_stop::attempt_confirmed(controller, id)
                            || super::super::memory_use::blocks_attempt_release(controller, id)
                    })
                {
                    return Err(
                        "revision parent retains incomplete original stop/revoke evidence".into(),
                    );
                }
                return Ok(true);
            }
        }
    }
    Ok(false)
}

/// Validates immutable historical permission and Review/Command linkage without renewing expired authority.
pub(super) fn validate_restored(
    controller: &ControllerState,
    mission: &domain::MissionId,
    state: &RevisionState,
    candidate: &Candidate,
) -> Result<usize, String> {
    if candidate.memory.len() > 16
        || candidate.review.as_ref().is_some_and(|review| {
            authorization_digest(candidate).ok().flatten() != review.memory_digest
        })
    {
        return Err("revision Memory bound or original Review permissions changed".into());
    }
    if let Some(review) = &candidate.review
        && review.decision == ReviewDecision::Approve
    {
        validate_review(candidate, &review.completion_precondition)?;
    }
    let mut bytes = 0_usize;
    for (key, record) in &candidate.memory {
        let mut original = record.clone();
        original.digest.clear();
        record.budget.validate()?;
        record.membership.validate()?;
        let receipt = state
            .commands
            .get(&record.command_id)
            .ok_or("Memory declaration lacks original command receipt")?;
        let registry = controller
            .bridge
            .control()
            .memory_authorization_revision(record.request.registry_revision)
            .ok_or("original revision Memory policy history absent")?;
        let original_command = Command {
            schema_version: record.schema().into(),
            command_id: record.command_id.clone(),
            expected_revision: receipt.result["revision"]
                .as_u64()
                .and_then(|revision| revision.checked_sub(1))
                .ok_or("original Memory command revision absent")?,
            action: Action::AuthorizeMemory {
                ticket_id: candidate.ticket_id.clone(),
                authorization: Box::new(record.request.clone()),
            },
        };
        let mut normalized =
            serde_json::to_value(&record.request.child).map_err(|e| e.to_string())?;
        let parent = serde_json::to_value(&record.parent).map_err(|e| e.to_string())?;
        for field in ["selector", "scope", "created_at"] {
            normalized[field] = parent[field].clone();
        }
        record.parent.validate().map_err(|e| e.to_string())?;
        record.request.child.validate().map_err(|e| e.to_string())?;
        if key != &record.request.child.selector().to_string()
            || record.digest != digest(&original)?
            || receipt.digest != record.command_digest
            || record.command_digest != digest(&original_command)?
            || receipt.principal != record.principal
            || receipt.leaf_digest != record.leaf_digest
            || receipt.role != "recovery-admin"
            || receipt.received_at != record.admitted_at
            || receipt.result["memory_authorization_digest"].as_str()
                != Some(record.digest.as_str())
            || receipt.result["schema_version"] != record.schema()
            || !valid_digest(&record.leaf_digest)
            || record.admitted_at < state.accepted_at
            || record.admitted_at.as_millis() >= record.certificate_expires_at_ms
            || record.budget.duration_ms() > candidate.budget.remaining(&record.admitted_kernel)
            || record.budget.duration_ms() > state.budget.remaining(&record.admitted_kernel)
            || record.budget.duration_ms()
                > record
                    .certificate_expires_at_ms
                    .saturating_sub(record.admitted_at.as_millis())
            || record.budget.remaining(&record.admitted_kernel) == 0
            || candidate.completed_time.as_ref().is_some_and(|at| {
                candidate.budget.remaining(at) > candidate.budget.remaining(&record.admitted_kernel)
            })
            || candidate.claim.as_ref().is_some_and(|claim| {
                candidate.budget.remaining(&claim.admitted_kernel)
                    > candidate.budget.remaining(&record.admitted_kernel)
            })
            || registry.registry_id != record.request.registry_id
            || normalized != parent
            || record.parent.selector() != &record.request.parent_selector
            || record.parent.selector() == record.request.child.selector()
            || if let Some(map) = &record.request.typed_map {
                domain::map_memory_manifest(map)? != record.parent
                    || record.parent.scope() != &MemoryScope::Global
            } else {
                record.parent.kind() == domain::MemoryKind::Spatial
                    || !matches!(record.parent.scope(), MemoryScope::ExecutionGroup(owner) if owner == &record.source_group)
            }
            || record.parent.visibility() != domain::MemoryVisibility::Exchangeable
            || record.parent.created_at() > record.request.child.created_at()
            || !matches!(record.request.child.scope(), MemoryScope::ExecutionGroup(owner) if owner == &record.request.target_group_id)
            || record.source_group == record.request.target_group_id
            || controller
                .bridge
                .control()
                .group(&record.source_group)
                .is_none_or(|group| group.mission_id() != mission)
        {
            return Err("original revision Memory declaration or lineage changed".into());
        }
        validate_typed_source_reference(controller, record)?;
        let source_policy = if let Some(map) = &record.request.typed_map {
            registry.typed_map_policy(map, &state.tenant)?
        } else {
            registry.policy(&record.parent, &state.tenant)?
        };
        if !source_policy
            .principals
            .get(&record.principal)
            .is_some_and(|operations| operations.contains(&domain::MemoryAccessOperation::Handoff))
        {
            return Err("original revision Memory principal lacked Handoff permission".into());
        }
        let child_policy = registry.policy(&record.request.child, &state.tenant)?;
        if child_policy.typed_map_manifest_digest.is_some()
            || !child_policy.groups.iter().any(|grant| {
                grant.group_id == record.request.target_group_id
                    && [
                        domain::MemoryAccessOperation::Read,
                        domain::MemoryAccessOperation::Import,
                    ]
                    .iter()
                    .all(|op| grant.operations.contains(op))
            })
        {
            return Err(
                "original revision child lacked explicit target Read/Import permission".into(),
            );
        }
        for switch in state
            .switches
            .iter()
            .filter(|switch| switch.ticket_id == candidate.ticket_id)
        {
            if switch.source_group != record.source_group
                || switch.membership != record.membership
                || switch.target_group != record.request.target_group_id
            {
                return Err("revision Memory original membership differs from the switch".into());
            }
        }
        bytes = bytes.saturating_add(serde_json::to_vec(record).map_err(|e| e.to_string())?.len());
    }
    Ok(bytes)
}
