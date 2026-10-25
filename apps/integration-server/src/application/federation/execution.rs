//! Resource-bearing local participants behind a durable explicitly delegated global decision.

use super::*;
use domain::{ExecutionGroupId, MissionId, ResourceAllocationGrant, RoleAssignment, TaskRef};

/// Closed resource-bearing protocol, independent of legacy participant-local metadata receipts.
pub(crate) const SCHEMA: &str = "roboguide.federation-execution/v0.1";
/// Encodes all JSON kinds distinctly, retaining exact float bits across languages without reserved-object aliases.
fn identity_value(value: &serde_json::Value) -> serde_json::Value {
    match value {
        serde_json::Value::Null => serde_json::json!(["null"]),
        serde_json::Value::Bool(value) => serde_json::json!(["boolean", value]),
        serde_json::Value::String(value) => serde_json::json!(["string", value]),
        serde_json::Value::Number(value) if value.is_f64() => serde_json::json!([
            "float64",
            format!(
                "{:016x}",
                value.as_f64().expect("finite parsed float").to_bits()
            )
        ]),
        serde_json::Value::Number(value) => serde_json::json!(["integer", value.to_string()]),
        serde_json::Value::Array(values) => serde_json::json!([
            "array",
            values.iter().map(identity_value).collect::<Vec<_>>()
        ]),
        serde_json::Value::Object(values) => serde_json::json!([
            "object",
            values
                .iter()
                .map(|(key, value)| serde_json::json!([key, identity_value(value)]))
                .collect::<Vec<_>>()
        ]),
    }
}

/// Hashes only the new resource-bearing protocol with a portable typed identity; legacy v0.2 keeps its original codec.
pub(crate) fn digest(value: &impl Serialize) -> Result<String, String> {
    let value = serde_json::to_value(value).map_err(|error| error.to_string())?;
    let mut hash = Sha256::new();
    hash.update(b"roboguide.federation-identity/v0.1\0");
    hash.update(serde_json::to_vec(&identity_value(&value)).map_err(|error| error.to_string())?);
    Ok(format!("sha256:{:x}", hash.finalize()))
}

/// Complete original transaction identity; deployments supply domains and semantic plans, never devices.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Manifest {
    /// Exact closed manifest generation.
    pub(crate) schema_version: String,
    /// Nonreusable original root command identity.
    pub(crate) transaction_id: String,
    /// Domain whose actual Controller retains the global decision.
    pub(crate) coordinator_domain: String,
    /// Immutable complete transaction tenant.
    pub(crate) tenant_id: String,
    /// Original independently verified global objective.
    pub(crate) objective: String,
    /// Nonrenewable root and participant preparation bound.
    pub(crate) budget_ms: u64,
    /// Explicit conservative round-trip/clock uncertainty acceptance bound.
    pub(crate) max_clock_uncertainty_ms: u64,
    /// Every actual participating domain and its original local semantic plan.
    pub(crate) participants: BTreeMap<String, LocalPlan>,
}

/// Accepted local contribution with an independent original goal source.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct LocalPlan {
    /// Canonical provider-neutral MissionPlan; actual Control selects Nodes and resources.
    pub(crate) plan: serde_json::Value,
    /// Registered independent goal source; the root's objective is global.
    pub(crate) goal_verifier: domain::VerifierSatisfactionSpec,
}

impl Manifest {
    /// Rejects ambiguous domains, oversize history, missing root objective and unbounded clocks.
    pub(crate) fn validate(&self) -> Result<(), String> {
        if self.schema_version != SCHEMA
            || validate_transaction_id(&self.transaction_id).is_err()
            || self.tenant_id.is_empty()
            || self.tenant_id.len() > 128
            || self.objective.trim().is_empty()
            || self.objective.len() > 8192
            || !(1000..=300_000).contains(&self.budget_ms)
            || !(1..=10_000).contains(&self.max_clock_uncertainty_ms)
            || self.max_clock_uncertainty_ms >= self.budget_ms
            || !(2..=16).contains(&self.participants.len())
            || !self.participants.contains_key(&self.coordinator_domain)
            || serde_json::to_vec(self)
                .map_err(|error| error.to_string())?
                .len()
                > 2 * 1024 * 1024
        {
            return Err("invalid complete Federation execution manifest or original budget".into());
        }
        let mut missions = BTreeSet::new();
        for (domain, local) in &self.participants {
            if domain.is_empty()
                || domain.len() > 128
                || domain
                    .chars()
                    .any(|c| c.is_control() || matches!(c, '/' | '?' | '#' | '*'))
            {
                return Err("invalid explicit participant domain".into());
            }
            let plan =
                decode_mission_plan(&local.plan.to_string()).map_err(|error| error.to_string())?;
            if !missions.insert(plan.goal().mission_id().clone())
                || domain == &self.coordinator_domain && plan.goal().objective() != self.objective
            {
                return Err("participant Mission identities must be unique and root goal must retain global objective".into());
            }
        }
        Ok(())
    }
}

/// Original resource-bearing permission, including an actual pre-effect writer observation.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Probe {
    /// Complete exact original global input.
    pub(crate) manifest: Manifest,
    /// Root-generated original nonce, never replaced on restart or response loss.
    pub(crate) nonce: String,
    /// Actual stable delegated source principal.
    pub(crate) principal: String,
    /// Original actual incoming leaf, retained beside later explicit rotation.
    pub(crate) leaf: String,
    /// Original complete accepted authorization policy.
    pub(crate) policy_digest: String,
    /// Actual local writer incarnation or current Raft epoch, never policy.domain_epochs.
    pub(crate) epoch: String,
    /// Original local receive timestamp, not a remote absolute deadline.
    pub(crate) received_at_ms: u64,
    /// Original kernel budget; replay and recovery never renew it.
    pub(crate) budget: OriginalBudget,
    /// Immutable actual probe receipt.
    pub(crate) receipt: serde_json::Value,
    /// Irreversible original probe abandonment, even when normal resource preparation never ran.
    pub(crate) aborted: Option<String>,
}

/// Exact original Control binding and typed capacity receipt for one local Task Role.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Binding {
    /// Original accepted logical Task.
    pub(crate) task_ref: TaskRef,
    /// Normal Control-owned actual Node/resources; not a proposed inventory.
    pub(crate) assignment: RoleAssignment,
    /// Original normal divisible grants; exclusive resources remain explicit assignment IDs.
    pub(crate) grants: Vec<ResourceAllocationGrant>,
}

/// Original local resource authority plus immutable global phase evidence.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Prepared {
    /// Original transaction's actual probe identity.
    pub(crate) probe_id: String,
    /// Original admitted Mission identity.
    pub(crate) mission: MissionId,
    /// Original actual Control Group, unchanged through Commit and release.
    pub(crate) group: ExecutionGroupId,
    /// Complete initial Ready cohort held under normal Control.
    pub(crate) bindings: Vec<Binding>,
    /// Exact immutable original prepared receipt.
    pub(crate) receipt: serde_json::Value,
    /// Original independent goal receive budgets; this authority does not create a replan policy.
    pub(crate) goal_evidence: BTreeMap<String, super::super::plan_revision::GoalProgress>,
    /// Actual local operator registry file fixed at admission for current independent verifier grants.
    pub(crate) authorization_path: PathBuf,
    /// Original enrolled registry identity; replacement files cannot adopt this goal.
    pub(crate) authorization_registry_id: String,
    /// Complete globally decided prepare proofs; absence continues to fence dispatch.
    pub(crate) decision: Option<Decision>,
    /// Immutable local Commit receipt, independent of execution release.
    pub(crate) committed: Option<serde_json::Value>,
    /// Complete all-member original Commit receipts that authorized the one release.
    pub(crate) release: Option<BTreeMap<String, serde_json::Value>>,
    /// Irreversible explicit abort/stop or automatic loss/expiry observation.
    pub(crate) stopped: Option<String>,
}

/// Immutable root Commit decision; exact all-domain original resource receipts precede notification.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Decision {
    /// Original complete manifest identity.
    pub(crate) manifest_digest: String,
    /// Every original participant's real normal resource receipt.
    pub(crate) prepared: BTreeMap<String, serde_json::Value>,
    /// Digest over manifest and complete original proof set, excluding this field.
    pub(crate) digest: String,
}
impl Decision {
    /// Computes the one stable decision identity without deriving authority from a Boolean.
    pub(crate) fn seal(
        manifest: &Manifest,
        prepared: BTreeMap<String, serde_json::Value>,
    ) -> Result<Self, String> {
        let manifest_digest = digest(manifest)?;
        let value =
            digest(&serde_json::json!({"manifest_digest": manifest_digest, "prepared":prepared}))?;
        Ok(Self {
            manifest_digest,
            prepared,
            digest: value,
        })
    }
    /// Validates exact all-domain original identities; authenticated delegation supplies decision authority.
    fn validate(&self, manifest: &Manifest) -> Result<(), String> {
        if Self::seal(manifest, self.prepared.clone())? != *self
            || self.prepared.keys().collect::<Vec<_>>()
                != manifest.participants.keys().collect::<Vec<_>>()
            || self.prepared.iter().any(|(domain, value)| {
                value["manifest_digest"] != self.manifest_digest
                    || value["domain"] != *domain
                    || value["phase"] != "prepared"
                    || !value["bindings"].is_array()
                    || value["bindings"].as_array().is_none_or(Vec::is_empty)
            })
        {
            return Err("global decision lacks complete original resource proofs".into());
        }
        Ok(())
    }
}

/// Participant history is bounded without recycling expired or uncertain identities.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct State {
    /// Every original durable probe, including requests whose replies were lost.
    pub(crate) probes: BTreeMap<String, Probe>,
    /// Normal Control admissions and independent distributed phase histories.
    pub(crate) prepared: BTreeMap<String, Prepared>,
}

/// New commands always repeat their complete original semantic identity and original nonce.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Command {
    /// Exact closed resource-bearing protocol.
    pub(crate) schema_version: String,
    /// Complete original manifest, never substituted by its caller's digest string.
    pub(crate) manifest: Manifest,
    /// Original root nonce retained before first network I/O.
    pub(crate) nonce: String,
    /// Actual probe epoch, mandatory for every resource transition.
    pub(crate) epoch: Option<String>,
    /// Complete immutable global decision, absent before Prepare completes everywhere.
    pub(crate) decision: Option<Decision>,
    /// Actual all-domain Commit receipts, absent before release.
    pub(crate) commits: Option<BTreeMap<String, serde_json::Value>>,
}

/// Requires an explicit current v0.2 execution grant for the actual peer/principal/tenant/domains.
pub(crate) fn authorize(
    controller: &ControllerState,
    caller: &Caller,
    manifest: &Manifest,
) -> Result<Policy, String> {
    let policy = configured_policy()?.ok_or("Federation execution policy is not configured")?;
    let grant = policy
        .grants
        .get(&caller.leaf)
        .ok_or("actual execution coordinator leaf is not enrolled")?;
    if controller.federation.policy() != Some(&policy)
        || !policy.schema_version.ends_with("/v0.2")
        || !grant.execution_coordinator
        || grant.revoked
        || grant.principal_id != caller.principal
        || caller.tenant != manifest.tenant_id
        || !grant.tenant_ids.contains(&caller.tenant)
        || !manifest.participants.contains_key(&policy.local_domain)
        || !manifest
            .participants
            .keys()
            .all(|domain| grant.domain_ids.contains(domain))
    {
        return Err("actual coordinator execution delegation, tenant or domains refused".into());
    }
    Ok(policy)
}

/// Commits a private candidate plus original command before publishing resources or decisions.
#[allow(clippy::too_many_arguments)]
pub(crate) fn transaction(
    controller: &Arc<Mutex<ControllerState>>,
    events: &state::SqliteEventLog,
    gate: &dyn ApplicationWriteGate,
    id: &str,
    principal: &str,
    input: &[u8],
    apply: impl FnOnce(
        &mut ControllerState,
        &mut state::SqliteEventLog,
    ) -> Result<serde_json::Value, String>,
    validate: impl FnOnce(&ControllerState) -> Result<(), String>,
) -> Result<serde_json::Value, String> {
    let _guard = gate.lock()?;
    let mut candidate = controller
        .lock()
        .map_err(|_| "Federation original writer unavailable")?
        .clone();
    let original_checkpoint =
        server_checkpoint_json(&candidate).map_err(|error| error.to_string())?;
    events.begin_batch().map_err(|error| error.to_string())?;
    let original_sequence = events
        .current_batch_event_sequence()
        .map_err(|error| error.to_string())?;
    let result = (|| {
        let output = apply(&mut candidate, &mut events.clone())?;
        let checkpoint = server_checkpoint_json(&candidate).map_err(|error| error.to_string())?;
        let sequence = events
            .current_batch_event_sequence()
            .map_err(|error| error.to_string())?;
        if checkpoint == original_checkpoint && sequence == original_sequence {
            gate.validate_locked()?;
            validate(&candidate)?;
            events.rollback_batch().map_err(|error| error.to_string())?;
            return Ok(output);
        }
        let command =
            control_command_journal_entry(id, Some(principal.into()), input, &checkpoint, sequence);
        events
            .save_checkpoint_with_command(SERVER_CHECKPOINT_SCHEMA, &checkpoint, command)
            .map_err(|error| error.to_string())?;
        gate.validate_locked()?;
        validate(&candidate)?;
        events.commit_batch().map_err(|error| error.to_string())?;
        *controller
            .lock()
            .map_err(|_| "Federation writer publication unavailable")? = candidate;
        Ok(output)
    })();
    if result.is_err() {
        let _ = events.rollback_batch();
    }
    result
}

/// Reports an immutable original stage and fresh local remaining budget without renewing any receipt.
pub(crate) fn view(
    controller: &ControllerState,
    events: &state::SqliteEventLog,
    id: &str,
    caller: &Caller,
    now: domain::TimestampMs,
) -> Result<serde_json::Value, String> {
    let probe = controller
        .federation
        .executions
        .probes
        .get(id)
        .ok_or("original execution probe unavailable")?;
    authorize(controller, caller, &probe.manifest)?;
    if probe.principal != caller.principal {
        return Err("original coordinator principal differs".into());
    }
    let prepared = controller.federation.executions.prepared.get(id);
    let execution = prepared.and_then(|record| controller.orchestrator.execution(&record.mission));
    let local_goal = prepared.and_then(|record| {
        controller
            .bridge
            .control()
            .mission_goal_contract(&record.mission)
    });
    let goal_receipts = prepared
        .map(|record| {
            controller
                .bridge
                .control()
                .mission_goal_receipts(&record.mission)
        })
        .unwrap_or_default();
    Ok(
        serde_json::json!({"schema_version":SCHEMA,"transaction_id":id,"domain":configured_policy()?.ok_or("missing domain policy")?.local_domain,
        "probe":probe.receipt, "prepared":prepared.map(|value| &value.receipt), "committed":prepared.and_then(|value| value.committed.as_ref()),
        "released":prepared.is_some_and(|value| value.release.is_some()), "stopped":prepared.and_then(|value| value.stopped.as_ref()).or(probe.aborted.as_ref()),
        "current_epoch":events.current_writer_epoch().ok(), "remaining_ms":probe.budget.remaining(&KernelTime::observe()?), "response_at_ms":now.as_millis(),
        "resources_released":prepared.is_none_or(|record|controller.bridge.control().group(&record.group).is_some_and(|group|group.lifecycle()==control::GroupLifecycle::Released)),
        "mission_lifecycle":execution.map(|value| format!("{:?}",value.lifecycle())), "goal_contract":local_goal, "goal_receipts":goal_receipts,
        "global_dispatch_ready":false}),
    )
}

/// Handles one actual authorized participant command; original replay never repeats normal Control effects.
#[allow(clippy::too_many_arguments)]
pub(crate) fn submit(
    controller: &Arc<Mutex<ControllerState>>,
    events: &state::SqliteEventLog,
    gate: &dyn ApplicationWriteGate,
    clock: &impl Clock,
    body: &[u8],
    action: &str,
    caller: &Caller,
    deployment: Option<&DeploymentFeasibility>,
    feed: Option<&TaskVerifierFeed>,
) -> Result<serde_json::Value, String> {
    let request: Command = serde_json::from_slice(body).map_err(|error| error.to_string())?;
    request.manifest.validate()?;
    if request.schema_version != SCHEMA
        || request.nonce.len() != 64
        || !request.nonce.bytes().all(|value| value.is_ascii_hexdigit())
    {
        return Err("execution requires original closed command and nonce".into());
    }
    let received = clock.now();
    let kernel = KernelTime::observe()?;
    let command_id = format!("federation-execution:{action}:{}", digest(&request)?);
    transaction(
        controller,
        events,
        gate,
        &command_id,
        &caller.principal,
        body,
        |candidate, audit| {
            let policy = authorize(candidate, caller, &request.manifest)?;
            let id = &request.manifest.transaction_id;
            if action == "probe" {
                if request.epoch.is_some()
                    || request.decision.is_some()
                    || request.commits.is_some()
                {
                    return Err("probe carries later authority".into());
                }
                if let Some(original) = candidate.federation.executions.probes.get(id) {
                    if original.manifest != request.manifest
                        || original.nonce != request.nonce
                        || original.principal != caller.principal
                    {
                        return Err("original probe identity conflicts".into());
                    }
                    return Ok(original.receipt.clone());
                }
                if candidate.federation.executions.probes.len() >= 128 {
                    return Err("original execution probe capacity exhausted".into());
                }
                let epoch = events
                    .current_writer_epoch()
                    .map_err(|error| error.to_string())?;
                let receipt = serde_json::json!({"schema_version":SCHEMA,"phase":"probe","transaction_id":id,"domain":policy.local_domain,"nonce":request.nonce,
                "manifest_digest":digest(&request.manifest)?,"epoch":epoch,"received_at_ms":received.as_millis(),"response_at_ms":clock.now().as_millis(),"duration_ms":request.manifest.budget_ms});
                candidate.federation.executions.probes.insert(
                    id.clone(),
                    Probe {
                        manifest: request.manifest.clone(),
                        nonce: request.nonce.clone(),
                        principal: caller.principal.clone(),
                        leaf: caller.leaf.clone(),
                        policy_digest: digest(&policy)?,
                        epoch,
                        received_at_ms: received.as_millis(),
                        budget: OriginalBudget::new(&kernel, request.manifest.budget_ms)?,
                        receipt: receipt.clone(),
                        aborted: None,
                    },
                );
                return Ok(receipt);
            }
            let probe = candidate
                .federation
                .executions
                .probes
                .get(id)
                .ok_or("original probe missing")?
                .clone();
            if probe.manifest != request.manifest
                || probe.nonce != request.nonce
                || probe.principal != caller.principal
                || request.epoch.as_deref() != Some(probe.epoch.as_str())
            {
                return Err(
                    "execution differs from original manifest, nonce, principal or epoch".into(),
                );
            }
            if action == "abort" || action == "stop" {
                return stop(candidate, id, action, audit, received);
            }
            if probe.aborted.is_some()
                || probe.budget.remaining(&KernelTime::observe()?) == 0
                || events
                    .current_writer_epoch()
                    .map_err(|error| error.to_string())?
                    != probe.epoch
            {
                return Err(
                    "original execution kernel budget or actual writer epoch is no longer current"
                        .into(),
                );
            }
            match action {
                "prepare" => prepare(
                    candidate,
                    &probe,
                    &policy.local_domain,
                    audit,
                    received,
                    deployment,
                    feed,
                ),
                "commit" => {
                    if candidate
                        .federation
                        .executions
                        .prepared
                        .get(id)
                        .is_some_and(|record| record.committed.is_none())
                    {
                        validate_current_bindings(candidate, id, received)?;
                    }
                    let decision = request
                        .decision
                        .as_ref()
                        .ok_or("global durable decision absent")?;
                    decision.validate(&request.manifest)?;
                    let original = candidate
                        .federation
                        .executions
                        .prepared
                        .get_mut(id)
                        .ok_or("normal Control preparation absent")?;
                    if original.stopped.is_some()
                        || decision.prepared.get(&policy.local_domain) != Some(&original.receipt)
                    {
                        return Err(
                            "global decision differs from original local resource receipt".into(),
                        );
                    }
                    if let Some(value) = &original.decision
                        && value != decision
                    {
                        return Err("immutable global Commit decision changed".into());
                    }
                    if let Some(receipt) = &original.committed {
                        return Ok(receipt.clone());
                    }
                    let receipt = serde_json::json!({"schema_version":SCHEMA,"phase":"committed","transaction_id":id,"domain":policy.local_domain,
                    "manifest_digest":digest(&request.manifest)?,"decision_digest":decision.digest,"prepared_digest":digest(&original.receipt)?,"epoch":probe.epoch});
                    original.decision = Some(decision.clone());
                    original.committed = Some(receipt.clone());
                    Ok(receipt)
                }
                "release" => {
                    if candidate
                        .federation
                        .executions
                        .prepared
                        .get(id)
                        .is_some_and(|record| record.release.is_none())
                    {
                        validate_current_bindings(candidate, id, received)?;
                    }
                    let commits = request
                        .commits
                        .as_ref()
                        .ok_or("complete original Commit receipts absent")?;
                    let original = candidate
                        .federation
                        .executions
                        .prepared
                        .get_mut(id)
                        .ok_or("normal Control preparation absent")?;
                    let decision = original
                        .decision
                        .as_ref()
                        .ok_or("original immutable global Commit absent")?;
                    if request.decision.as_ref() != Some(decision)
                        || original.stopped.is_some()
                        || commits.keys().collect::<Vec<_>>()
                            != request.manifest.participants.keys().collect::<Vec<_>>()
                        || commits.get(&policy.local_domain) != original.committed.as_ref()
                        || commits.iter().any(|(domain, value)| {
                            value["phase"] != "committed"
                                || value["domain"] != *domain
                                || value["decision_digest"] != decision.digest
                                || value["prepared_digest"]
                                    != digest(&decision.prepared[domain]).unwrap_or_default()
                        })
                    {
                        return Err(
                            "release requires exact complete original Commit receipts".into()
                        );
                    }
                    if original
                        .release
                        .as_ref()
                        .is_some_and(|value| value != commits)
                    {
                        return Err("original execution release changed".into());
                    }
                    original.release = Some(commits.clone());
                    Ok(
                        serde_json::json!({"schema_version":SCHEMA,"phase":"released","transaction_id":id,"decision_digest":decision.digest}),
                    )
                }
                _ => Err("unsupported original Federation execution action".into()),
            }
        },
        |candidate| {
            authorize(candidate, caller, &request.manifest)?;
            if !matches!(action, "abort" | "stop")
                && candidate.federation.executions.probes[&request.manifest.transaction_id]
                    .budget
                    .remaining(&KernelTime::observe()?)
                    == 0
            {
                return Err("original execution budget expired before Commit".into());
            }
            Ok(())
        },
    )
}

/// Atomically admits the real local plan and complete initial Ready cohort without any Execute intent.
#[allow(clippy::too_many_arguments)]
fn prepare(
    controller: &mut ControllerState,
    probe: &Probe,
    domain: &str,
    events: &mut state::SqliteEventLog,
    now: domain::TimestampMs,
    deployment: Option<&DeploymentFeasibility>,
    feed: Option<&TaskVerifierFeed>,
) -> Result<serde_json::Value, String> {
    let id = &probe.manifest.transaction_id;
    if let Some(original) = controller.federation.executions.prepared.get(id) {
        return Ok(original.receipt.clone());
    }
    let local = &probe.manifest.participants[domain];
    let plan = decode_mission_plan(&local.plan.to_string()).map_err(|error| error.to_string())?;
    if let Some(issue) = super::super::plan_validation::issues(
        controller,
        &plan,
        &probe.manifest.tenant_id,
        deployment,
        feed,
    )
    .first()
    {
        return Err(issue.message.clone());
    }
    let mission = plan.goal().mission_id().clone();
    if controller.orchestrator.execution(&mission).is_some() {
        return Err("Federation requires an original unadmitted local Mission".into());
    }
    let group =
        ExecutionGroupId::new(format!("federated-{mission}")).map_err(|error| error.to_string())?;
    let trace = domain::CorrelationId::new(format!("federation-{id}"))
        .map_err(|error| error.to_string())?;
    controller
        .bridge
        .control_mut()
        .register_mission_tenant(mission.clone(), &probe.manifest.tenant_id)
        .map_err(|error| error.to_string())?;
    let generic = super::super::semantic_verifier::validate_plan(
        controller,
        &plan,
        &probe.manifest.tenant_id,
        feed,
    )?;
    if generic {
        controller
            .bridge
            .control_mut()
            .freeze_mission_verifier_sources(&plan, &probe.manifest.tenant_id)
            .map_err(|error| error.to_string())?;
    }
    if let Some(deployment) = deployment {
        for (actor, nodes) in deployment.restrictions_for_plan(&plan, &group)? {
            controller
                .bridge
                .control_mut()
                .set_actor_candidate_restriction(
                    mission.clone(),
                    actor,
                    nodes,
                    deployment.digest().into(),
                )
                .map_err(|error| error.to_string())?;
        }
    }
    super::super::validate_actor_placement_coverage(controller.bridge.control(), &plan)?;
    let goal = controller
        .bridge
        .control_mut()
        .freeze_mission_goal(
            plan.goal(),
            local.goal_verifier.clone(),
            digest(&probe.manifest)?,
            &probe.manifest.tenant_id,
        )
        .map_err(|error| error.to_string())?;
    if goal.source.producer_principals.contains(&probe.principal) {
        return Err("original Federation goal requires an independent verifier principal".into());
    }
    controller
        .orchestrator
        .submit(
            plan.clone(),
            group.clone(),
            controller.bridge.control_mut(),
            now,
            &trace,
            events,
        )
        .map_err(|error| error.to_string())?;
    controller
        .orchestrator
        .install_mission_goal_gate(&mission, controller.bridge.control())
        .map_err(|error| error.to_string())?;
    controller
        .bridge
        .register_execution_relations(&plan, &group, now, &trace)
        .map_err(|error| error.to_string())?;
    let mut bindings = Vec::new();
    for task in controller
        .orchestrator
        .dispatchable_tasks(&mission, controller.bridge.control())
    {
        let state = controller.bridge.decision_state();
        let booking = super::super::resource_topology::booking(controller, &task)?;
        let prepared = if let Some(booking) = &booking {
            controller.orchestrator.prepare_task_with_duration_estimate(
                &mission,
                &task,
                &state,
                controller.bridge.control_mut(),
                now,
                &trace,
                events,
                booking,
            )
        } else {
            controller.orchestrator.prepare_task(
                &mission,
                &task,
                &state,
                controller.bridge.control_mut(),
                now,
                &trace,
                events,
            )
        }
        .map_err(|error| error.to_string())?;
        if prepared.assignments().len()
            != plan
                .task_graph()
                .task(task.task_id())
                .ok_or("original ready Task lost")?
                .requirement()
                .roles()
                .len()
        {
            return Err("complete original ready cohort did not obtain every Role".into());
        }
        for assignment in prepared.assignments() {
            let grants = controller
                .bridge
                .control()
                .resource_allocation_grants(&group, &task, assignment.role_id())
                .map_err(|error| error.to_string())?;
            bindings.push(Binding {
                task_ref: task.clone(),
                assignment: assignment.clone(),
                grants,
            });
        }
    }
    if bindings.is_empty()
        || bindings.len() > 256
        || bindings
            .iter()
            .all(|binding| binding.assignment.resource_ids().is_empty())
    {
        return Err(
            "complete initial Federation cohort has no actual normal resource bindings".into(),
        );
    }
    let receipt = serde_json::json!({"schema_version":SCHEMA,"phase":"prepared","transaction_id":id,"domain":domain,"manifest_digest":digest(&probe.manifest)?,
        "epoch":probe.epoch,"nonce":probe.nonce,"mission_id":mission,"group_id":group,"plan_digest":orchestration::mission_plan_digest(&plan),"bindings":bindings,"goal_contract_digest":goal.digest});
    controller.mission_admissions.insert(
        mission.as_str().into(),
        controller_http::MissionAdmission::new(
            mission.clone(),
            group.clone(),
            local.plan.to_string().as_bytes(),
            now,
        ),
    );
    let authorization_path = std::path::absolute(
        std::env::var_os("ROBOGUIDE_OPERATOR_MTLS_ROLE_MAP")
            .ok_or("Federation original goal requires actual operator authorization file")?,
    )
    .map_err(|error| error.to_string())?;
    let authorization_registry_id = controller
        .bridge
        .control()
        .operator_authorization()
        .ok_or("original goal operator registry absent")?
        .registry_id
        .clone();
    controller.federation.executions.prepared.insert(
        id.clone(),
        Prepared {
            probe_id: id.clone(),
            mission,
            group,
            bindings,
            receipt: receipt.clone(),
            goal_evidence: BTreeMap::new(),
            authorization_path,
            authorization_registry_id,
            decision: None,
            committed: None,
            release: None,
            stopped: None,
        },
    );
    Ok(receipt)
}

/// Checks actual current original Control ownership and source/Node readiness before any first phase advancement.
fn validate_current_bindings(
    controller: &ControllerState,
    id: &str,
    now: domain::TimestampMs,
) -> Result<(), String> {
    let original = controller
        .federation
        .executions
        .prepared
        .get(id)
        .ok_or("original resource preparation absent")?;
    let mission = controller
        .orchestrator
        .execution(&original.mission)
        .ok_or("original local Mission absent")?;
    if !matches!(
        mission.lifecycle(),
        orchestration::MissionExecutionLifecycle::Accepted
            | orchestration::MissionExecutionLifecycle::Running
    ) || mission.isolation().is_some()
    {
        return Err("original local Mission is held or no longer live".into());
    }
    let control = controller.bridge.control();
    control
        .validate_bound_mission_nodes(
            &controller.bridge.decision_state(),
            mission.plan(),
            &original.group,
            now,
        )
        .map_err(|error| error.to_string())?;
    let group = control
        .group(&original.group)
        .ok_or("original Group absent")?;
    for binding in &original.bindings {
        if !group
            .task_execution(&binding.task_ref)
            .is_some_and(|task| task.assignments().contains(&binding.assignment))
            || control
                .resource_allocation_grants(
                    &original.group,
                    &binding.task_ref,
                    binding.assignment.role_id(),
                )
                .map_err(|error| error.to_string())?
                != binding.grants
        {
            return Err(
                "original prepared resource ownership changed before distributed Commit/release"
                    .into(),
            );
        }
    }
    Ok(())
}

/// Retains real dispatched work behind complete original stop/revoke; unexecuted preparation can release normally.
fn stop(
    controller: &mut ControllerState,
    id: &str,
    reason: &str,
    events: &mut state::SqliteEventLog,
    now: domain::TimestampMs,
) -> Result<serde_json::Value, String> {
    if !controller.federation.executions.prepared.contains_key(id) {
        controller
            .federation
            .executions
            .probes
            .get_mut(id)
            .ok_or("original probe absent")?
            .aborted
            .get_or_insert_with(|| reason.into());
        return Ok(serde_json::json!({"phase":"stopped","transaction_id":id,"reason":reason}));
    }
    let original = controller.federation.executions.prepared[id].clone();
    if reason == "abort" && original.decision.is_some() {
        return Err("durable global Commit cannot be aborted".into());
    }
    if original.stopped.is_some() {
        return Ok(
            serde_json::json!({"phase":"stopped","transaction_id":id,"reason":original.stopped}),
        );
    }
    let trace = domain::CorrelationId::new("federation-stop").expect("fixed trace");
    let live = controller
        .bridge
        .attempt_history()
        .iter()
        .any(|attempt| attempt.command().group_id() == &original.group);
    if live {
        super::super::memory_use::revoke_group(
            controller,
            &original.group,
            "Federation original authority lost",
            now,
        )?;
        let commands: Vec<_> = controller
            .bridge
            .attempt_history()
            .into_iter()
            .filter(|attempt| {
                let command = attempt.command();
                command.group_id() == &original.group
                    && controller.bridge.current_task_attempt_id(
                        command.group_id(),
                        command.task_ref(),
                        command.role_id(),
                    ) == Some(attempt.execution_id())
            })
            .map(|attempt| attempt.command().clone())
            .collect();
        for command in commands {
            super::super::safety_stop::admit(
                controller,
                &original.group,
                command.task_ref(),
                command.role_id(),
                now,
                30_000,
            )?;
        }
        if controller
            .orchestrator
            .execution(&original.mission)
            .is_some_and(|mission| {
                matches!(
                    mission.lifecycle(),
                    orchestration::MissionExecutionLifecycle::Accepted
                        | orchestration::MissionExecutionLifecycle::Running
                        | orchestration::MissionExecutionLifecycle::Cancelling
                )
            })
        {
            controller
                .orchestrator
                .request_cancel(
                    &original.mission,
                    controller.bridge.control_mut(),
                    now,
                    &trace,
                    events,
                )
                .map_err(|error| error.to_string())?;
        }
    } else {
        controller
            .orchestrator
            .cancel(
                &original.mission,
                controller.bridge.control_mut(),
                now,
                &trace,
                events,
            )
            .map_err(|error| error.to_string())?;
    }
    controller
        .federation
        .executions
        .prepared
        .get_mut(id)
        .expect("original preparation")
        .stopped = Some(reason.into());
    Ok(
        serde_json::json!({"phase":"stopped","transaction_id":id,"reason":reason,"physical_stop":"unavailable"}),
    )
}

/// Returns the one original resource-bearing participant for a local Mission, without a new replan authority.
pub(crate) fn goal_owner<'a>(
    controller: &'a ControllerState,
    mission: &domain::MissionId,
) -> Option<&'a Prepared> {
    controller
        .federation
        .executions
        .prepared
        .values()
        .find(|record| &record.mission == mission)
}

/// Updates only the original participant's independent goal history under the existing application writer.
pub(crate) fn goal_owner_mut<'a>(
    controller: &'a mut ControllerState,
    mission: &domain::MissionId,
) -> Option<&'a mut Prepared> {
    controller
        .federation
        .executions
        .prepared
        .values_mut()
        .find(|record| &record.mission == mission)
}

/// Keeps timer, resume, recovery and outbox paths behind original distributed authority.
pub(crate) fn blocks_mission(controller: &ControllerState, mission: &MissionId) -> bool {
    if controller.federation.executions.prepared.is_empty() {
        return false;
    }
    let kernel = KernelTime::observe();
    controller
        .federation
        .executions
        .prepared
        .values()
        .any(|record| {
            &record.mission == mission
                && (record.release.is_none()
                    || record.stopped.is_some()
                    || kernel.as_ref().map_or(true, |sample| {
                        controller.federation.executions.probes[&record.probe_id]
                            .budget
                            .remaining(sample)
                            == 0
                    }))
        })
}

/// Requires startup to persist original epoch loss before any restored outbox can become visible.
pub(crate) fn has_pending(controller: &ControllerState) -> bool {
    controller
        .federation
        .executions
        .prepared
        .values()
        .any(|record| {
            controller
                .orchestrator
                .execution(&record.mission)
                .is_some_and(|mission| {
                    matches!(
                        mission.lifecycle(),
                        orchestration::MissionExecutionLifecycle::Accepted
                            | orchestration::MissionExecutionLifecycle::Running
                            | orchestration::MissionExecutionLifecycle::Cancelling
                    )
                })
        })
}

/// Observes original local budget and actual epoch without renewing clocks or interpreting a process wait as device proof.
pub(crate) fn monitor(
    controller: &mut ControllerState,
    events: &state::SqliteEventLog,
    now: domain::TimestampMs,
) -> Result<(), String> {
    if controller.federation.executions.prepared.is_empty() {
        return Ok(());
    }
    let actual = events
        .current_writer_epoch()
        .map_err(|error| error.to_string())?;
    let kernel = KernelTime::observe()?;
    let ids: Vec<_> = controller
        .federation
        .executions
        .prepared
        .iter()
        .filter(|(_, record)| {
            record.stopped.is_none()
                && controller
                    .orchestrator
                    .execution(&record.mission)
                    .is_some_and(|mission| {
                        !matches!(
                            mission.lifecycle(),
                            orchestration::MissionExecutionLifecycle::Completed
                                | orchestration::MissionExecutionLifecycle::Cancelled
                                | orchestration::MissionExecutionLifecycle::Failed
                        )
                    })
        })
        .filter(|(_, record)| {
            let probe = &controller.federation.executions.probes[&record.probe_id];
            probe.epoch != actual || probe.budget.remaining(&kernel) == 0
        })
        .map(|(id, _)| id.clone())
        .collect();
    for id in ids {
        stop(
            controller,
            &id,
            "original-budget-or-epoch-lost",
            &mut events.clone(),
            now,
        )?;
    }
    Ok(())
}

/// Validates complete retained original joins before startup or immutable recovery publication.
pub(crate) fn validate(controller: &ControllerState) -> Result<(), String> {
    let state = &controller.federation.executions;
    if state.probes.len() > 128 || state.prepared.len() > 128 {
        return Err("Federation execution history bound exceeded".into());
    }
    for (id, probe) in &state.probes {
        probe.manifest.validate()?;
        probe.budget.validate()?;
        if id != &probe.manifest.transaction_id
            || probe.budget.duration_ms() != probe.manifest.budget_ms
            || probe.epoch.is_empty()
            || probe.receipt["manifest_digest"] != digest(&probe.manifest)?
            || probe.receipt["nonce"] != probe.nonce
            || probe.receipt["epoch"] != probe.epoch
            || !controller.federation.policies.values().any(|policy| {
                digest(policy).ok().as_deref() == Some(&probe.policy_digest)
                    && policy.grants.get(&probe.leaf).is_some_and(|grant| {
                        grant.execution_coordinator
                            && grant.principal_id == probe.principal
                            && !grant.revoked
                    })
            })
        {
            return Err(
                "original Federation probe input, epoch, budget or delegation differs".into(),
            );
        }
    }
    for (id, record) in &state.prepared {
        let probe = state
            .probes
            .get(id)
            .ok_or("Federation preparation lost original probe")?;
        let mission = controller
            .orchestrator
            .execution(&record.mission)
            .ok_or("Federation local Mission absent")?;
        let domain = controller
            .federation
            .policies
            .values()
            .find(|policy| digest(policy).ok().as_deref() == Some(&probe.policy_digest))
            .ok_or("original execution policy absent")?
            .local_domain
            .clone();
        let plan = decode_mission_plan(&probe.manifest.participants[&domain].plan.to_string())
            .map_err(|error| error.to_string())?;
        if record.probe_id != *id
            || mission.group_id() != &record.group
            || mission.plan() != &plan
            || record.bindings.is_empty()
            || record.receipt["bindings"]
                != serde_json::to_value(&record.bindings).map_err(|error| error.to_string())?
            || record.receipt["manifest_digest"] != digest(&probe.manifest)?
            || record.release.is_some() && record.committed.is_none()
            || controller
                .bridge
                .control()
                .mission_goal_contract(&record.mission)
                .is_none_or(|goal| {
                    goal.context_digest != digest(&probe.manifest).unwrap_or_default()
                })
        {
            return Err("Federation original plan, resources, Group or goal join differs".into());
        }
        if controller
            .runtime_revisions
            .contains_key(record.mission.as_str())
            || record.authorization_registry_id.is_empty()
        {
            return Err("Federation goal has ambiguous or missing original owner".into());
        }
        super::super::plan_revision::validate_goal_progress(
            controller,
            &record.mission,
            &record.goal_evidence,
        )?;
        if let Some(decision) = &record.decision {
            decision.validate(&probe.manifest)?;
            if decision.prepared.get(&domain) != Some(&record.receipt) {
                return Err("original global decision lost exact local preparation".into());
            }
        }
        if record.committed.is_some() != record.decision.is_some() {
            return Err("Federation local Commit lost original global decision".into());
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Exact shared Python/Rust fixtures cover small exponents, negative zero, large integers and literal tags.
    #[test]
    fn portable_identity_retains_types_and_float_bits() {
        let fixtures: Vec<serde_json::Value> = serde_json::from_str(include_str!(
            "../../../../../contracts/control/federation-execution-v0.1/identity-fixtures.json"
        ))
        .unwrap();
        for fixture in fixtures {
            assert_eq!(
                digest(&fixture["value"]).unwrap(),
                fixture["digest"].as_str().unwrap()
            );
        }
        assert_ne!(
            digest(&serde_json::json!(1.0)).unwrap(),
            digest(&serde_json::json!({"$f64_bits":"3ff0000000000000"})).unwrap()
        );
        assert_ne!(
            digest(&serde_json::json!(1.0)).unwrap(),
            digest(&serde_json::json!(1)).unwrap()
        );
    }
}
