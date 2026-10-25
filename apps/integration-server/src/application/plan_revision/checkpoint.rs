//! Complete original revision-state validation; historical absence grants no permission.

use super::*;
use serde::de::{Error as _, MapAccess, Visitor};

/// Deserializes a bounded map while rejecting duplicate Mission authorities rather than overwriting them.
pub(crate) fn deserialize_states<'de, D: serde::Deserializer<'de>>(
    deserializer: D,
) -> Result<BTreeMap<String, RevisionState>, D::Error> {
    /// Typed original map reader; duplicates are an authority ambiguity.
    struct States;
    impl<'de> Visitor<'de> for States {
        type Value = BTreeMap<String, RevisionState>;
        /// Describes the accepted durable shape without accepting a second authority for one Mission.
        fn expecting(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
            formatter.write_str("a bounded duplicate-free Mission revision authority map")
        }
        /// Keeps original full typed records and refuses duplicate keys or excess retained authorities.
        fn visit_map<A: MapAccess<'de>>(self, mut map: A) -> Result<Self::Value, A::Error> {
            let mut states = BTreeMap::new();
            while let Some((key, value)) = map.next_entry::<String, RevisionState>()? {
                if states.len() >= 128 || states.insert(key, value).is_some() {
                    return Err(A::Error::custom(
                        "duplicate or excessive Mission revision authorities",
                    ));
                }
            }
            Ok(states)
        }
    }
    deserializer.deserialize_map(States)
}

/// Finds the actual accepted source semantic version, including retired immutable originals.
fn source_plan(
    controller: &ControllerState,
    mission: &domain::MissionId,
    revision: u64,
) -> Result<domain::MissionPlan, String> {
    let execution = controller
        .orchestrator
        .execution(mission)
        .ok_or("revision has no original Mission")?;
    if revision == execution.plan_revision() {
        return Ok(execution.plan().clone());
    }
    let source = execution
        .plan_revisions()
        .get(usize::try_from(revision).map_err(|_| "revision index overflow")?)
        .ok_or("original revision source is absent")?;
    orchestration::decode_mission_plan(&source.source_plan.to_string()).map_err(|e| e.to_string())
}

/// Checks every durable original permission/receipt/source against accepted plans and actual stop history.
pub(crate) fn validate_restored(controller: &ControllerState) -> Result<(), String> {
    if controller.runtime_revisions.len() > 128 {
        return Err("revision authority bound exceeded".into());
    }
    let mut total_bytes = 0_usize;
    let mut memory_bytes = 0_usize;
    for (key, state) in &controller.runtime_revisions {
        let mission = domain::MissionId::new(key).map_err(|e| e.to_string())?;
        admission::validate_policy(&state.policy)?;
        let execution = controller
            .orchestrator
            .execution(&mission)
            .ok_or("revision has no accepted Mission")?;
        if controller.bridge.control().mission_tenant(&mission) != Some(state.tenant.as_str())
            || state.accepted_at < execution.accepted_at()
            || state.certificate_expires_at_ms <= state.accepted_at.as_millis()
            || state.policy_digest
                != digest(&(
                    &state.policy,
                    state.authority.as_str(),
                    state.tenant.as_str(),
                    &state.budget,
                    state.accepted_at,
                    &state.deployment_digest,
                    state.certificate_expires_at_ms,
                    &state.authorization_path,
                    &state.authorization_registry_id,
                    &state.goal_contract_digest,
                    &state.authority_certificate_digest,
                    &state.authority_grant,
                ))?
            || state.candidates.len() > state.policy.candidate_limit
            || state.switches.len() > state.policy.switch_limit
            || state.commands.len() > MAX_COMMANDS
            || state.revision < (state.commands.len() as u64)
            || !valid_digest(&state.authority_certificate_digest)
            || state.authority_grant.principal_id != state.authority
            || state.authority_grant.role != "recovery-admin"
            || !state.authority_grant.active(state.accepted_at.as_millis())
            || !(state.authority_grant.tenant_ids.contains("*")
                || state.authority_grant.tenant_ids.contains(&state.tenant))
        {
            return Err("inconsistent original revision policy or immutable Mission tenant".into());
        }
        state.budget.validate()?;
        generation::validate(state)?;
        goal::validate_restored(controller, &mission, state)?;
        for (id, candidate) in &state.candidates {
            memory_bytes = memory_bytes.saturating_add(memory::validate_restored(
                controller, &mission, state, candidate,
            )?);
            if memory_bytes > 512 * 1024 {
                return Err("restored revision Memory metadata exceeds its bound".into());
            }
            if id != &candidate.ticket_id
                || !identity(id)
                || !identity(&candidate.principal)
                || candidate.base_revision > execution.plan_revision()
                || candidate.base_digest
                    != orchestration::mission_plan_digest(&source_plan(
                        controller,
                        &mission,
                        candidate.base_revision,
                    )?)
                || candidate.completed_time.is_none()
                    && (candidate.plan.is_some()
                        || candidate.provenance.is_some()
                        || candidate.review.is_some()
                        || candidate.rejected.is_some())
            {
                return Err("inconsistent original candidate ticket/output identity".into());
            }
            candidate.budget.validate()?;
            if let Some(plan) = &candidate.plan {
                total_bytes = total_bytes.saturating_add(plan.to_string().len());
                if plan.to_string().len() > state.policy.candidate_bytes {
                    return Err("retained candidate exceeds its original byte budget".into());
                }
            }
            if let Some(provenance) = &candidate.provenance {
                admission::validate_provenance(provenance, &candidate.principal)?;
            }
            if let Some(at) = &candidate.completed_time
                && candidate.rejected.is_none()
            {
                let decoded = orchestration::decode_mission_plan(
                    &candidate
                        .plan
                        .as_ref()
                        .ok_or("accepted original candidate bytes absent")?
                        .to_string(),
                )
                .map_err(|e| e.to_string())?;
                if candidate.budget.remaining(at) == 0
                    || candidate.plan_digest.as_ref()
                        != Some(&orchestration::mission_plan_digest(&decoded))
                {
                    return Err(
                        "candidate output exceeded its original deadline or changed bytes".into(),
                    );
                }
            }
            if let Some(review) = &candidate.review {
                let mut original = review.clone();
                original.digest.clear();
                if review.digest != digest(&original)?
                    || candidate.budget.remaining(&review.reviewed_kernel) == 0
                    || Some(&review.plan_digest) != candidate.plan_digest.as_ref()
                    || review.reviewed_at < state.accepted_at
                    || review.reviewed_at.as_millis() >= review.certificate_expires_at_ms
                    || review.reason.trim().is_empty()
                    || review.reason.len() > 512
                {
                    return Err("original candidate Review is inconsistent".into());
                }
            }
        }
        let mut sources = BTreeSet::new();
        let mut commands = BTreeSet::new();
        for switch in &state.switches {
            switch.budget.validate()?;
            let candidate = state
                .candidates
                .get(&switch.ticket_id)
                .ok_or("switch lacks original candidate")?;
            if !identity(&switch.command_id)
                || !sources.insert(switch.source_group.clone())
                || !commands.insert(switch.command_id.clone())
                || switch.source_group == switch.target_group
                || switch.requested_at < state.accepted_at
                || candidate.base_revision != switch.source_revision
                || switch.generation
                    && (!switch.automatic
                        || state
                            .generation
                            .as_ref()
                            .is_none_or(|permission| permission.admitted_at > switch.requested_at)
                        || candidate.principal != state.policy.proposer)
                || switch.phase == Phase::Generating && !switch.generation
                || switch.automatic
                    && switch
                        .trigger
                        .is_none_or(|trigger| !state.policy.automatic_triggers.contains(&trigger))
            {
                return Err(
                    "original switch identity, trigger or source budget is inconsistent".into(),
                );
            }
            let mut attempt_ids = BTreeSet::new();
            for attempt in &switch.attempts {
                if !attempt_ids.insert(attempt.execution_id.clone())
                    || attempt.generation == 0
                    || attempt.task_ref.mission_id() != &mission
                {
                    return Err("original switch attempt coverage is invalid".into());
                }
                let actual = controller
                    .bridge
                    .attempt_history()
                    .into_iter()
                    .find(|actual| actual.execution_id() == attempt.execution_id)
                    .ok_or("original switch attempt history disappeared")?;
                if actual.command().group_id() != &switch.source_group
                    || actual.command().task_ref() != &attempt.task_ref
                    || actual.command().role_id() != &attempt.role_id
                    || actual.command().node_id() != &attempt.node_id
                    || controller.bridge.current_task_attempt_id(
                        &switch.source_group,
                        &attempt.task_ref,
                        &attempt.role_id,
                    ) != Some(attempt.execution_id.as_str())
                    || controller.bridge.current_attempt_generation(
                        &switch.source_group,
                        &attempt.task_ref,
                        &attempt.role_id,
                    ) != Some(attempt.generation)
                {
                    return Err("original revision attempt generation/owner/slot changed".into());
                }
            }
            let history = controller.bridge.attempt_history();
            let actual: BTreeSet<_> = history
                .iter()
                .filter(|attempt| attempt.command().group_id() == &switch.source_group)
                .map(|attempt| attempt.execution_id())
                .collect();
            let expected: BTreeSet<_> = attempt_ids
                .iter()
                .map(String::as_str)
                .chain(switch.prior_attempts.iter().map(String::as_str))
                .collect();
            if expected != actual
                || expected.len() != attempt_ids.len() + switch.prior_attempts.len()
                || switch.prior_attempts.iter().any(|id| {
                    !super::super::safety_stop::attempt_confirmed(controller, id)
                        || super::super::memory_use::blocks_attempt_release(controller, id)
                        || history
                            .iter()
                            .find(|attempt| attempt.execution_id() == id)
                            .is_none_or(|attempt| !attempt.status().is_terminal())
                })
            {
                return Err(
                    "original full source attempt/stop/revoke coverage is incomplete".into(),
                );
            }
            if switch.phase == Phase::Applied {
                let version = execution
                    .plan_revisions()
                    .iter()
                    .find(|version| version.command_id == switch.command_id)
                    .ok_or("applied switch lacks actual accepted version")?;
                if version.source_group_id != switch.source_group
                    || version.target_group_id != switch.target_group
                    || candidate.plan_digest.as_ref() != Some(&version.target_plan_digest)
                    || candidate
                        .review
                        .as_ref()
                        .map(|review| review.digest.as_str())
                        != Some(version.review_digest.as_str())
                {
                    return Err(
                        "applied switch differs from original candidate/Review/version".into(),
                    );
                }
                for attempt in &switch.attempts {
                    if !super::super::safety_stop::attempt_confirmed(
                        controller,
                        &attempt.execution_id,
                    ) || super::super::memory_use::blocks_attempt_release(
                        controller,
                        &attempt.execution_id,
                    ) {
                        return Err("applied revision lacks original stop/revoke authority".into());
                    }
                }
            } else if switch.phase == Phase::Cancelled {
                if !matches!(
                    execution.lifecycle(),
                    orchestration::MissionExecutionLifecycle::Cancelled
                        | orchestration::MissionExecutionLifecycle::Failed
                ) || execution.group_id() != &switch.source_group
                    || controller
                        .bridge
                        .control()
                        .group(&switch.source_group)
                        .is_none_or(|group| group.lifecycle() != control::GroupLifecycle::Released)
                {
                    return Err(
                        "cancelled revision lacks actual original terminal Mission release".into(),
                    );
                }
            } else if execution.group_id() != &switch.source_group
                || execution.plan_revision() != switch.source_revision
                || execution.isolation().is_none()
            {
                return Err("pending switch lost its original Mission ownership/hold".into());
            }
        }
        for (id, receipt) in &state.commands {
            if !identity(id)
                || !identity(&receipt.principal)
                || !valid_digest(&receipt.leaf_digest)
                || !matches!(receipt.role.as_str(), "operator" | "recovery-admin")
                || controller
                    .bridge
                    .control()
                    .operator_authorization()
                    .and_then(|registry| registry.grants.get(&receipt.leaf_digest))
                    .is_none_or(|grant| grant.principal_id != receipt.principal)
                || receipt.received_at < state.accepted_at
                || receipt.result["command_id"].as_str() != Some(id.as_str())
                || !receipt.result["schema_version"]
                    .as_str()
                    .is_some_and(|schema| {
                        matches!(
                            schema,
                            SCHEMA
                                | GENERATION_SCHEMA
                                | MEMORY_SCHEMA
                                | TYPED_MEMORY_SCHEMA
                                | SCOPED_MEMORY_SCHEMA
                        )
                    })
                || receipt.result["revision"]
                    .as_u64()
                    .is_none_or(|revision| revision == 0 || revision > state.revision)
            {
                return Err("inconsistent original revision command receipt".into());
            }
        }
    }
    if total_bytes > MAX_RETAINED_PLAN_BYTES {
        return Err("retained revision proposal capacity exceeded".into());
    }
    for mission in controller.orchestrator.mission_ids() {
        if let Some(execution) = controller.orchestrator.execution(&mission) {
            if execution.goal_gate().is_some()
                && !controller.runtime_revisions.contains_key(mission.as_str())
            {
                return Err(
                    "original Mission goal gate lost its application receive-budget authority"
                        .into(),
                );
            }
            for version in execution.plan_revisions() {
                if controller
                    .runtime_revisions
                    .get(mission.as_str())
                    .is_none_or(|state| {
                        !state.switches.iter().any(|switch| {
                            switch.command_id == version.command_id
                                && switch.phase == Phase::Applied
                        })
                    })
                {
                    return Err(
                        "accepted version lacks its original application permission/stop intent"
                            .into(),
                    );
                }
            }
        }
    }
    Ok(())
}

/// Reattaches only the actual original startup deployment profile; copying a checkpoint grants no profile.
pub(crate) fn attach_deployment(
    controller: &mut ControllerState,
    deployment: Option<Arc<DeploymentFeasibility>>,
) -> Result<(), String> {
    for state in controller.runtime_revisions.values_mut() {
        if state.deployment_digest.as_deref() != deployment.as_ref().map(|profile| profile.digest())
        {
            return Err("runtime revision requires its original startup deployment profile".into());
        }
        state.deployment = deployment.clone();
    }
    Ok(())
}
