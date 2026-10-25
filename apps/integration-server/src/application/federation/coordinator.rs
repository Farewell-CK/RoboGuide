//! Root Orchestration journal: original outgoing intent precedes real pinned peer I/O.

use super::execution::{Command, Decision, Manifest, SCHEMA, digest};
use super::transport::{Peer, Reply};
use super::*;

/// Explicit versioned composition profile; no domain, address, resource or Node is built in.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct Profile {
    /// Exact actual peer profile generation.
    schema_version: String,
    /// Stable original root profile identity.
    profile_id: String,
    /// Consecutive explicit deployment revision.
    revision: u64,
    /// Actual domain retaining global decisions.
    local_domain: String,
    /// Explicit conservative relative clock-rate bound, not an observed synchronization claim.
    max_drift_ppm: u64,
    /// Per-request original owned network budget.
    request_budget_ms: u64,
    /// Exact independently declared participant endpoints and actual TLS inputs.
    peers: BTreeMap<String, Peer>,
}

/// Reads a closed explicit profile without leaking key bytes or inventing default peers.
pub(crate) fn configured() -> Result<Option<Profile>, String> {
    let Some(path) = std::env::var_os("ROBOGUIDE_FEDERATION_PEERS") else {
        return Ok(None);
    };
    use std::io::Read;
    let mut bytes = Vec::new();
    std::fs::File::open(path)
        .map_err(|error| error.to_string())?
        .take(1024 * 1024 + 1)
        .read_to_end(&mut bytes)
        .map_err(|error| error.to_string())?;
    if bytes.len() > 1024 * 1024 {
        return Err("Federation peer profile exceeds bound".into());
    }
    let profile: Profile = serde_json::from_slice(&bytes).map_err(|error| error.to_string())?;
    if profile.schema_version != "roboguide.federation-peers/v0.1"
        || profile.profile_id.is_empty()
        || profile.revision == 0
        || !(2..=16).contains(&profile.peers.len())
        || !profile.peers.contains_key(&profile.local_domain)
        || profile.max_drift_ppm > 1000
        || !(100..=10_000).contains(&profile.request_budget_ms)
        || profile.local_domain
            != configured_policy()?
                .ok_or("Federation authorization is absent")?
                .local_domain
    {
        return Err("invalid explicit Federation peer profile".into());
    }
    for peer in profile.peers.values() {
        peer.validate()?;
    }
    Ok(Some(profile))
}

/// One original real clock translation, with explicit bounded uncertainty rather than remote absolute time authority.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct ClockProof {
    /// Original local request intent clock.
    sent_at_ms: u64,
    /// Actual local receive clock after the original peer reply.
    received_at_ms: u64,
    /// Original peer local receive sample.
    remote_received_at_ms: u64,
    /// Original peer response-construction sample.
    remote_response_at_ms: u64,
    /// Conservative remote-minus-local offset interval, including explicit drift and resolution.
    minimum_offset_ms: i64,
    /// Conservative original interval upper endpoint.
    maximum_offset_ms: i64,
    /// Original conservative local remaining common budget at receipt.
    remaining_at_receive_ms: u64,
}

/// Builds an original interval; delayed/changed-clock observations cannot create a fresh prepare budget.
fn clock_proof(
    manifest: &Manifest,
    profile: &Profile,
    intent: &Intent,
    reply: &serde_json::Value,
    received: u64,
    remaining: u64,
) -> Result<ClockProof, String> {
    let first = reply["received_at_ms"]
        .as_u64()
        .ok_or("actual peer receive sample missing")?;
    let last = reply["response_at_ms"]
        .as_u64()
        .ok_or("actual peer response sample missing")?;
    let elapsed = received
        .checked_sub(intent.sent_at_ms)
        .ok_or("local clock regressed across original peer I/O")?;
    let drift = manifest
        .budget_ms
        .saturating_mul(profile.max_drift_ppm)
        .div_ceil(1_000_000)
        .saturating_add(20);
    let lower = i128::from(last) - i128::from(received) - i128::from(drift);
    let upper = i128::from(first) - i128::from(intent.sent_at_ms) + i128::from(drift);
    if first > last
        || upper < lower
        || upper - lower > i128::from(manifest.max_clock_uncertainty_ms)
        || reply["duration_ms"].as_u64() != Some(manifest.budget_ms)
    {
        return Err(
            "actual peer clock interval exceeds original deployment uncertainty contract".into(),
        );
    }
    Ok(ClockProof {
        sent_at_ms: intent.sent_at_ms,
        received_at_ms: received,
        remote_received_at_ms: first,
        remote_response_at_ms: last,
        minimum_offset_ms: lower.try_into().map_err(|_| "clock offset overflow")?,
        maximum_offset_ms: upper.try_into().map_err(|_| "clock offset overflow")?,
        remaining_at_receive_ms: remaining.min(
            manifest
                .budget_ms
                .saturating_sub(elapsed)
                .saturating_sub(drift),
        ),
    })
}

/// One bounded read intent retained before actual GET; it never repeats an uncertain command.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Observation {
    /// Original nonrenewable query budget, bounded by the transaction's first receive clock.
    budget: OriginalBudget,
    /// First actual local query-intent time; backoff never refreshes the transaction budget.
    sent_at_ms: u64,
    /// Actual query result; absence after restart remains an uncertain read.
    reply: Option<Reply>,
    /// Original actual transport failure retained beside later observations.
    error: Option<String>,
}

/// Original effect command and all subsequent explicitly read-only observations.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Intent {
    /// Exact original reconstructed wire input identity, frozen before I/O.
    request_digest: String,
    /// First actual local request-intent timestamp.
    sent_at_ms: u64,
    /// Actual original immutable outgoing request budget.
    budget: OriginalBudget,
    /// Original pinned configured peer profile revision.
    profile_revision: u64,
    /// Original reply; never overwritten by reconciliation.
    reply: Option<Reply>,
    /// Actual original local response sample, retained before waiting for result SQL.
    received_at_ms: Option<u64>,
    /// Actual original local receive kernel sample, never replaced by recovery time.
    received_kernel: Option<KernelTime>,
    /// First original network failure; cancellation or lost reply cannot prove no effects.
    error: Option<String>,
    /// At most sixteen actual query generations, all within the first transaction budget.
    observations: Vec<Observation>,
}

/// Original global transaction retained with its real command owner and complete local contributions.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Transaction {
    /// Exact original approved global input.
    manifest: Manifest,
    /// Actual original root writer epoch; replacement can stop but cannot silently inherit dispatch authority.
    root_epoch: String,
    /// Actual initiating manager, separate from each outgoing service identity.
    principal: String,
    /// Actual original manager leaf; revocation fences further commands without renewing history.
    leaf: String,
    /// Actual original certificate expiry supplied by the verified TLS witness.
    certificate_expires_at_ms: u64,
    /// Original local root receive budget.
    budget: OriginalBudget,
    /// One original root-generated nonce per domain, retained before any peer request.
    nonces: BTreeMap<String, String>,
    /// Exact actual peer probe receipts.
    probes: BTreeMap<String, serde_json::Value>,
    /// Complete original clock translations beside actual probes.
    clocks: BTreeMap<String, ClockProof>,
    /// Exact actual normal Control resource receipts.
    prepared: BTreeMap<String, serde_json::Value>,
    /// Irreversible complete global Commit decision, persisted before any Commit notification.
    decision: Option<Decision>,
    /// Exact all-participant original Commit receipts, independent of release.
    commits: BTreeMap<String, serde_json::Value>,
    /// Actual acknowledged execution releases; an outgoing POST intent is insufficient.
    released: BTreeSet<String>,
    /// All original network effect intents keyed by domain then phase.
    intents: BTreeMap<String, BTreeMap<String, Intent>>,
    /// Independent terminal local observations including original-goal receipts.
    completed: BTreeMap<String, serde_json::Value>,
    /// Irreversible stop/abort request; it never changes a durable Commit decision.
    stopping: Option<String>,
    /// One original bounded stop-only cleanup budget; it never grants new execution.
    cleanup_budget: Option<OriginalBudget>,
    /// Actual acknowledged stopped or unprepared peers.
    stopped: BTreeSet<String>,
}

/// Bounded root history; original profile authority survives restart and cold recovery.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct State {
    /// Consecutive complete original peer configurations, no private key material.
    profiles: BTreeMap<u64, Profile>,
    /// Every original transaction, including partial/Unknown outcomes.
    transactions: BTreeMap<String, Transaction>,
}

impl State {
    /// Exposes original retained root authority to explicit wrapper migration fences.
    pub(crate) fn has_authority(&self) -> bool {
        !self.profiles.is_empty() || !self.transactions.is_empty()
    }
}

/// A prepared owned work item; constructing it grants no network effect until its SQL intent commits.
struct Work {
    /// Original exact transaction.
    id: String,
    /// Original participant domain.
    domain: String,
    /// Original phase whose effect or observation is being performed.
    phase: String,
    /// Exact fixed original wire body, absent for read-only observation.
    body: Option<serde_json::Value>,
    /// Observation index, separate from the original POST.
    observation: Option<usize>,
    /// Exact original pinned peer used for this one operation.
    peer: Peer,
    /// Actual stable tenant selector.
    tenant: String,
    /// Exact SQL-admitted request clock, including time spent publishing the original intent.
    budget: OriginalBudget,
}

impl Work {
    /// Sends once only within the original retained clock; SQL queueing cannot renew network time.
    fn send(&self) -> Result<Reply, String> {
        let remaining = self.budget.remaining(&KernelTime::observe()?);
        if remaining == 0 {
            return Err("original Federation request budget expired before peer I/O".into());
        }
        let path = if self.observation.is_some() || self.phase == "observe" {
            format!("/v1/federation/executions/{}", self.id)
        } else {
            format!("/v1/federation/executions/{}", self.phase)
        };
        super::transport::request(
            &self.peer,
            &self.tenant,
            &path,
            self.body.as_ref(),
            remaining,
        )
    }
}

/// Installs only an exact unchanged or consecutive same-root peer profile, retaining old source facts.
fn install_profile(state: &mut State, profile: Profile) -> Result<(), String> {
    if let Some((revision, previous)) = state.profiles.last_key_value() {
        if previous == &profile {
            return Ok(());
        }
        if revision.checked_add(1) != Some(profile.revision)
            || previous.profile_id != profile.profile_id
            || previous.local_domain != profile.local_domain
            || !previous
                .peers
                .keys()
                .all(|domain| profile.peers.contains_key(domain))
        {
            return Err("Federation peer profile regression or original domain removal".into());
        }
        for (domain, peer) in &previous.peers {
            if profile.peers[domain].principal_id != peer.principal_id {
                return Err("Federation peer stable principal changed".into());
            }
        }
    } else if profile.revision != 1 {
        return Err("first Federation peer profile must start at one".into());
    }
    if state.profiles.len() >= 1024 {
        return Err("original peer profile history capacity exhausted".into());
    }
    state.profiles.insert(profile.revision, profile);
    Ok(())
}

/// Admits a global original manifest once; exact same-caller replay returns its original progress only.
pub(crate) fn submit(
    controller: &Arc<Mutex<ControllerState>>,
    events: &state::SqliteEventLog,
    gate: &dyn ApplicationWriteGate,
    body: &[u8],
    caller: &Caller,
    expires_at: u64,
) -> Result<serde_json::Value, String> {
    let manifest: Manifest = serde_json::from_slice(body).map_err(|error| error.to_string())?;
    manifest.validate()?;
    let profile = configured()?.ok_or("explicit Federation peer composition is absent")?;
    if manifest.coordinator_domain != profile.local_domain
        || manifest
            .participants
            .keys()
            .any(|domain| !profile.peers.contains_key(domain))
    {
        return Err("manifest differs from actual root and configured peers".into());
    }
    let command = format!("federation-root:{}", digest(&manifest)?);
    let kernel = KernelTime::observe()?;
    execution::transaction(
        controller,
        events,
        gate,
        &command,
        &caller.principal,
        body,
        |candidate, _| {
            execution::authorize(candidate, caller, &manifest)?;
            let state = &mut candidate.federation.coordinations;
            install_profile(state, profile.clone())?;
            if let Some(original) = state.transactions.get(&manifest.transaction_id) {
                if original.manifest != manifest || original.principal != caller.principal {
                    return Err("original root transaction identity conflicts".into());
                }
                return Ok(view_record(original));
            }
            if state.transactions.len() >= 128 {
                return Err("original global transaction capacity exhausted".into());
            }
            let nonces = manifest
                .participants
                .keys()
                .map(|domain| {
                    let mut bytes = [0_u8; 32];
                    ring::rand::SecureRandom::fill(&ring::rand::SystemRandom::new(), &mut bytes)
                        .map_err(|_| "actual original nonce generation failed")?;
                    Ok((
                        domain.clone(),
                        bytes.iter().map(|byte| format!("{byte:02x}")).collect(),
                    ))
                })
                .collect::<Result<BTreeMap<String, String>, String>>()?;
            let original = Transaction {
                manifest: manifest.clone(),
                root_epoch: events
                    .current_writer_epoch()
                    .map_err(|error| error.to_string())?,
                principal: caller.principal.clone(),
                leaf: caller.leaf.clone(),
                certificate_expires_at_ms: expires_at,
                budget: OriginalBudget::new(&kernel, manifest.budget_ms)?,
                nonces,
                probes: BTreeMap::new(),
                clocks: BTreeMap::new(),
                prepared: BTreeMap::new(),
                decision: None,
                commits: BTreeMap::new(),
                released: BTreeSet::new(),
                intents: BTreeMap::new(),
                completed: BTreeMap::new(),
                stopping: None,
                cleanup_budget: None,
                stopped: BTreeSet::new(),
            };
            let receipt = view_record(&original);
            state
                .transactions
                .insert(manifest.transaction_id.clone(), original);
            Ok(receipt)
        },
        |candidate| {
            execution::authorize(candidate, caller, &manifest)?;
            if configured()?.as_ref() != Some(&profile) {
                return Err("original peer profile changed before root Commit".into());
            }
            bounded(candidate)
        },
    )
}

/// Produces original progress without equating releases or local completion with the global goal.
fn view_record(record: &Transaction) -> serde_json::Value {
    serde_json::json!({"schema_version":SCHEMA,"transaction_id":record.manifest.transaction_id,"manifest_digest":digest(&record.manifest).ok(),
        "original_manifest":record.manifest,"probes":record.probes,"clock_translations":record.clocks,"prepared":record.prepared,"decision":record.decision,
        "commits":record.commits,"released":record.released,"completed":record.completed,"stopping":record.stopping,"stopped":record.stopped,
        "global_completed":record.stopping.is_none() && record.completed.len()==record.manifest.participants.len(),
        "original_intents":record.intents,"all_original_commits_received":record.commits.len()==record.manifest.participants.len(),
        "global_dispatch_ready":false})
}

/// Reads one exact tenant/principal-owned original global transaction through current explicit delegation.
pub(crate) fn view(
    controller: &ControllerState,
    id: &str,
    caller: &Caller,
) -> Result<serde_json::Value, String> {
    let record = controller
        .federation
        .coordinations
        .transactions
        .get(id)
        .ok_or("unknown original global transaction")?;
    execution::authorize(controller, caller, &record.manifest)?;
    if caller.principal != record.principal {
        return Err("global transaction belongs to another original manager".into());
    }
    Ok(view_record(record))
}

/// Builds the exact original effect body solely from retained original authority, never from a fresh plan.
fn body(record: &Transaction, domain: &str, phase: &str) -> Result<serde_json::Value, String> {
    let request = Command {
        schema_version: SCHEMA.into(),
        manifest: record.manifest.clone(),
        nonce: record.nonces[domain].clone(),
        epoch: if phase == "probe" {
            None
        } else {
            Some(
                record.probes[domain]["epoch"]
                    .as_str()
                    .ok_or("original actual peer epoch absent")?
                    .into(),
            )
        },
        decision: if matches!(phase, "commit" | "release") {
            record.decision.clone()
        } else {
            None
        },
        commits: if phase == "release" {
            Some(record.commits.clone())
        } else {
            None
        },
    };
    serde_json::to_value(request).map_err(|error| error.to_string())
}

/// Finds a real original effect receipt or an independently observed exact peer phase without rewriting Unknown.
fn effect(intent: &Intent, phase: &str) -> Option<serde_json::Value> {
    if let Some(reply) = &intent.reply
        && (200..300).contains(&reply.status)
        && (!matches!(phase, "stop" | "abort") || reply.body["resources_released"] == true)
        && (phase != "observe"
            || reply.body["mission_lifecycle"] == "Completed"
                && reply.body["goal_contract"].is_object()
                && reply.body["goal_receipts"]
                    .as_array()
                    .is_some_and(|values| !values.is_empty()))
    {
        return Some(reply.body.clone());
    }
    for observation in intent.observations.iter().rev() {
        let Some(reply) = &observation.reply else {
            continue;
        };
        if reply.status != 200 {
            continue;
        }
        if matches!(phase, "release" | "stop" | "abort" | "observe") {
            let passed = match phase {
                "release" => reply.body["released"] == true,
                "observe" => {
                    reply.body["mission_lifecycle"] == "Completed"
                        && reply.body["goal_contract"].is_object()
                        && reply.body["goal_receipts"]
                            .as_array()
                            .is_some_and(|values| !values.is_empty())
                }
                _ => !reply.body["stopped"].is_null() && reply.body["resources_released"] == true,
            };
            if passed {
                return Some(reply.body.clone());
            }
        } else if let Some(value) = reply.body.get(match phase {
            "probe" => "probe",
            "prepare" => "prepared",
            "commit" => "committed",
            _ => continue,
        }) && !value.is_null()
        {
            return Some(value.clone());
        }
    }
    None
}

/// Reduces real complete receipt identities only; all-domain preparation durably fixes one Commit decision.
fn reduce(record: &mut Transaction, profile: &Profile) -> Result<(), String> {
    for domain in record
        .manifest
        .participants
        .keys()
        .cloned()
        .collect::<Vec<_>>()
    {
        let Some(intents) = record.intents.get(&domain) else {
            continue;
        };
        for (phase, intent) in intents {
            if std::iter::once(intent.reply.as_ref())
                .chain(intent.observations.iter().map(|query| query.reply.as_ref()))
                .flatten()
                .any(|reply| {
                    reply.status == 200
                        && (!reply.body["stopped"].is_null() && reply.body.get("stopped").is_some()
                            || matches!(
                                reply.body["mission_lifecycle"].as_str(),
                                Some("Failed" | "Cancelled")
                            ))
                })
            {
                record
                    .stopping
                    .get_or_insert_with(|| "actual-participant-stop-or-failure".into());
            }
            let Some(value) = effect(intent, phase) else {
                continue;
            };
            if matches!(phase.as_str(), "probe" | "prepare" | "commit")
                && (value["manifest_digest"] != digest(&record.manifest)?
                    || value["domain"] != domain
                    || value["transaction_id"] != record.manifest.transaction_id)
            {
                return Err("real peer receipt differs from original global identity".into());
            }
            match phase.as_str() {
                "probe" if !record.probes.contains_key(&domain) => {
                    if value["nonce"] != record.nonces[&domain] {
                        return Err("real peer probe nonce differs".into());
                    }
                    // Only the original direct reply establishes this original request/reply clock interval.
                    if intent
                        .reply
                        .as_ref()
                        .is_some_and(|reply| (200..300).contains(&reply.status))
                    {
                        let proof = clock_proof(
                            &record.manifest,
                            profile,
                            intent,
                            &value,
                            intent
                                .received_at_ms
                                .ok_or("original actual response sample absent")?,
                            record.budget.remaining(
                                intent
                                    .received_kernel
                                    .as_ref()
                                    .ok_or("original actual receive kernel missing")?,
                            ),
                        )?;
                        record.clocks.entry(domain.clone()).or_insert(proof);
                    } else {
                        // Lost replies preserve the original probe but cannot invent its missing round-trip clock proof.
                        return Err(
                            "original probe reply missing; clock translation remains unavailable"
                                .into(),
                        );
                    }
                    record.probes.insert(domain.clone(), value);
                }
                "prepare" => {
                    if value["epoch"] != record.probes[&domain]["epoch"]
                        || value["nonce"] != record.nonces[&domain]
                        || value["bindings"].as_array().is_none_or(Vec::is_empty)
                    {
                        return Err(
                            "real preparation lacks original actual resource or epoch proof".into(),
                        );
                    }
                    record.prepared.entry(domain.clone()).or_insert(value);
                }
                "commit" => {
                    let decision = record
                        .decision
                        .as_ref()
                        .ok_or("peer Commit precedes durable root decision")?;
                    if value["decision_digest"] != decision.digest
                        || value["prepared_digest"] != digest(&record.prepared[&domain])?
                    {
                        return Err("real peer Commit differs from original global decision".into());
                    }
                    record.commits.entry(domain.clone()).or_insert(value);
                }
                "release" => {
                    record.released.insert(domain.clone());
                }
                "stop" | "abort" => {
                    record.stopped.insert(domain.clone());
                }
                "observe" => {
                    record.completed.entry(domain.clone()).or_insert(value);
                }
                _ => {}
            }
        }
    }
    if record.decision.is_none()
        && record.stopping.is_none()
        && record.prepared.len() == record.manifest.participants.len()
    {
        record.decision = Some(Decision::seal(&record.manifest, record.prepared.clone())?);
    }
    Ok(())
}

/// Selects the next original bounded phase; later stages cannot overtake the complete distributed decision.
fn phase(record: &Transaction, domain: &str) -> Option<&'static str> {
    if record.stopping.is_some() {
        return if record.stopped.contains(domain) {
            None
        } else if !record.probes.contains_key(domain) {
            Some("probe")
        } else if record.decision.is_none() {
            Some("abort")
        } else {
            Some("stop")
        };
    }
    if !record.probes.contains_key(domain) {
        return Some("probe");
    }
    if record.probes.len() != record.manifest.participants.len() {
        return None;
    }
    if !record.prepared.contains_key(domain) {
        return Some("prepare");
    }
    record.decision.as_ref()?;
    if !record.commits.contains_key(domain) {
        return Some("commit");
    }
    if record.commits.len() != record.manifest.participants.len() {
        return None;
    }
    if !record.released.contains(domain) {
        return Some("release");
    }
    if !record.completed.contains_key(domain) {
        return Some("observe");
    }
    None
}

/// Keeps all new retained metadata below the existing complete-checkpoint byte limit.
fn bounded(controller: &ControllerState) -> Result<(), String> {
    if serde_json::to_vec(&controller.federation.coordinations)
        .map_err(|error| error.to_string())?
        .len()
        > 16 * 1024 * 1024
        || serde_json::to_vec(&controller.federation.executions)
            .map_err(|error| error.to_string())?
            .len()
            > 16 * 1024 * 1024
    {
        return Err("original Federation retained authority capacity exhausted".into());
    }
    Ok(())
}

/// Persists exactly one owned outgoing effect/read intent before returning work outside every application lock.
fn admit_work(
    controller: &Arc<Mutex<ControllerState>>,
    events: &state::SqliteEventLog,
    gate: &dyn ApplicationWriteGate,
    clock: &impl Clock,
    id: &str,
    domain: &str,
    profile: &Profile,
) -> Result<Option<Work>, String> {
    let mut work = None;
    let command = format!(
        "federation-work:{id}:{domain}:{}",
        events
            .write_status()
            .map_err(|error| error.to_string())?
            .authority_revision
    );
    execution::transaction(
        controller,
        events,
        gate,
        &command,
        "trusted-composition:federation-peer-worker",
        id.as_bytes(),
        |candidate, _| {
            install_profile(&mut candidate.federation.coordinations, profile.clone())?;
            let snapshot = candidate.federation.coordinations.transactions[id].clone();
            let caller = Caller {
                leaf: snapshot.leaf.clone(),
                principal: snapshot.principal.clone(),
                tenant: snapshot.manifest.tenant_id.clone(),
            };
            let authorized = execution::authorize(candidate, &caller, &snapshot.manifest).is_ok();
            let record = candidate
                .federation
                .coordinations
                .transactions
                .get_mut(id)
                .expect("original transaction");
            let kernel = KernelTime::observe()?;
            let remaining = record.budget.remaining(&kernel);
            let wall = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map_err(|error| error.to_string())?
                .as_millis();
            if events
                .current_writer_epoch()
                .map_err(|error| error.to_string())?
                != record.root_epoch
                || !authorized
                || remaining == 0
                || wall >= u128::from(record.certificate_expires_at_ms)
            {
                record
                    .stopping
                    .get_or_insert_with(|| "original-root-permission-or-budget-lost".into());
            }
            if let Err(error) = reduce(record, profile) {
                record.stopping.get_or_insert(error);
            }
            if record.stopping.is_some() && record.cleanup_budget.is_none() {
                record.cleanup_budget = Some(OriginalBudget::new(&kernel, 30_000)?);
            }
            if record.stopping.is_some()
                && !record
                    .intents
                    .get(domain)
                    .is_some_and(|intents| intents.contains_key("probe"))
                && !record.probes.contains_key(domain)
            {
                record.stopped.insert(domain.into());
                return Ok(serde_json::json!({"original_intent_never_admitted":true}));
            }
            let Some(phase) = phase(record, domain) else {
                return Ok(serde_json::json!({"idle":true}));
            };
            let timeout = record
                .cleanup_budget
                .as_ref()
                .filter(|_| record.stopping.is_some())
                .map_or(remaining, |budget| budget.remaining(&kernel))
                .min(profile.request_budget_ms);
            if timeout == 0 {
                return Ok(serde_json::json!({"budget_expired":true}));
            }
            let encoded = if phase == "observe" {
                None
            } else {
                Some(body(record, domain, phase)?)
            };
            let intents = record.intents.entry(domain.into()).or_default();
            let request_digest = digest(&encoded)?;
            let (observation, wire) = if let Some(intent) = intents.get_mut(phase) {
                if intent.request_digest != request_digest {
                    return Err("original peer wire identity changed".into());
                }
                let last = intent
                    .observations
                    .last()
                    .map_or(intent.sent_at_ms, |query| query.sent_at_ms);
                let backoff = (500_u64 << intent.observations.len().min(6)).min(30_000);
                if clock.now().as_millis().saturating_sub(last) < backoff {
                    return Ok(serde_json::json!({"original_query_backoff":true}));
                }
                if intent.observations.len() >= 16 {
                    return Ok(serde_json::json!({"observation_bound":true}));
                }
                let index = intent.observations.len();
                intent.observations.push(Observation {
                    budget: OriginalBudget::new(&kernel, timeout)?,
                    sent_at_ms: clock.now().as_millis(),
                    reply: None,
                    error: None,
                });
                (Some(index), None)
            } else {
                intents.insert(
                    phase.into(),
                    Intent {
                        request_digest,
                        sent_at_ms: clock.now().as_millis(),
                        budget: OriginalBudget::new(&kernel, timeout)?,
                        profile_revision: profile.revision,
                        reply: None,
                        received_at_ms: None,
                        received_kernel: None,
                        error: None,
                        observations: Vec::new(),
                    },
                );
                (None, encoded)
            };
            let original = &intents[phase];
            let budget = observation.map_or_else(
                || original.budget.clone(),
                |index| original.observations[index].budget.clone(),
            );
            work = Some(Work {
                id: id.into(),
                domain: domain.into(),
                phase: phase.into(),
                body: wire,
                observation,
                peer: profile.peers[domain].clone(),
                tenant: record.manifest.tenant_id.clone(),
                budget,
            });
            bounded(candidate)?;
            Ok(serde_json::json!({"intent_retained":true}))
        },
        |_| {
            if configured()?.as_ref() != Some(profile) {
                return Err(
                    "actual Federation peer configuration changed before intent Commit".into(),
                );
            }
            Ok(())
        },
    )?;
    Ok(work)
}

/// Performs one real peer operation and stores its original response/error without repeating a POST.
fn perform(
    controller: &Arc<Mutex<ControllerState>>,
    events: &state::SqliteEventLog,
    gate: &dyn ApplicationWriteGate,
    clock: &impl Clock,
    work: Work,
) -> Result<(), String> {
    let reply = work.send();
    let received_at = clock.now().as_millis();
    let received_kernel = KernelTime::observe()?;
    let input=serde_json::to_vec(&serde_json::json!({"transaction":work.id,"domain":work.domain,"phase":work.phase,"observation":work.observation,"reply":reply.as_ref().ok(),"error":reply.as_ref().err()})).map_err(|error|error.to_string())?;
    let command = format!(
        "federation-peer-result:{}",
        digest(
            &serde_json::from_slice::<serde_json::Value>(&input)
                .map_err(|error| error.to_string())?
        )?
    );
    execution::transaction(
        controller,
        events,
        gate,
        &command,
        "trusted-composition:federation-peer-worker",
        &input,
        |candidate, _| {
            let record = candidate
                .federation
                .coordinations
                .transactions
                .get_mut(&work.id)
                .ok_or("original transaction disappeared")?;
            let intent = record
                .intents
                .get_mut(&work.domain)
                .and_then(|intents| intents.get_mut(&work.phase))
                .ok_or("original peer intent disappeared")?;
            let (slot, error) = if let Some(index) = work.observation {
                let observation = &mut intent.observations[index];
                (&mut observation.reply, &mut observation.error)
            } else {
                (&mut intent.reply, &mut intent.error)
            };
            if slot.is_some() || error.is_some() {
                return Err("original peer result already retained".into());
            }
            match reply {
                Ok(value) => *slot = Some(value),
                Err(value) => *error = Some(value),
            }
            if work.observation.is_none() {
                intent.received_at_ms = Some(received_at);
                intent.received_kernel = Some(received_kernel);
            }
            bounded(candidate)?;
            Ok(serde_json::json!({"original_result_retained":true}))
        },
        |_| Ok(()),
    )?;
    Ok(())
}

/// Drives bounded fair one-operation turns in an owned blocking worker, leaving network/timer threads free.
pub(crate) fn step(
    controller: &Arc<Mutex<ControllerState>>,
    events: &state::SqliteEventLog,
    gate: &dyn ApplicationWriteGate,
    clock: &impl Clock,
    cursor: &mut usize,
) -> Result<(), String> {
    let Some(profile) = configured()? else {
        return Ok(());
    };
    let turns: Vec<_> = {
        let live = controller
            .lock()
            .map_err(|_| "Federation root unavailable")?;
        live.federation
            .coordinations
            .transactions
            .iter()
            .filter(|(_, record)| {
                record.completed.len() != record.manifest.participants.len()
                    && record.stopped.len() != record.manifest.participants.len()
            })
            .flat_map(|(id, record)| {
                record
                    .manifest
                    .participants
                    .keys()
                    .map(move |domain| (id.clone(), domain.clone()))
            })
            .collect()
    };
    if turns.is_empty() {
        return Ok(());
    }
    let (id, domain) = &turns[*cursor % turns.len()];
    *cursor = cursor.wrapping_add(1);
    if let Some(work) = admit_work(controller, events, gate, clock, id, domain, &profile)? {
        perform(controller, events, gate, clock, work)?;
    }
    Ok(())
}

/// Validates full original authority before startup, preserving Unknown and all first-issued clocks.
pub(crate) fn validate(controller: &ControllerState) -> Result<(), String> {
    let state = &controller.federation.coordinations;
    if state.transactions.len() > 128 || state.profiles.len() > 1024 {
        return Err("Federation coordinator history bound exceeded".into());
    }
    let mut revisions = State::default();
    for profile in state.profiles.values() {
        install_profile(&mut revisions, profile.clone())?;
    }
    if !state.profiles.is_empty() {
        let actual = configured()?.ok_or("original Federation peer profile missing")?;
        install_profile(&mut revisions, actual)?;
    }
    for (id, record) in &state.transactions {
        record.manifest.validate()?;
        record.budget.validate()?;
        if record.root_epoch.is_empty()
            || id != &record.manifest.transaction_id
            || record.budget.duration_ms() != record.manifest.budget_ms
            || record.nonces.keys().collect::<Vec<_>>()
                != record.manifest.participants.keys().collect::<Vec<_>>()
            || record.decision.as_ref().is_some_and(|decision| {
                Decision::seal(&record.manifest, record.prepared.clone())
                    .ok()
                    .as_ref()
                    != Some(decision)
            })
            || !record
                .released
                .iter()
                .all(|domain| record.commits.contains_key(domain))
        {
            return Err("original Federation root manifest, clocks or decision mismatch".into());
        }
        for (domain, proof) in &record.clocks {
            let intent = record
                .intents
                .get(domain)
                .and_then(|values| values.get("probe"))
                .ok_or("original clock proof lost its send intent")?;
            let original = record
                .probes
                .get(domain)
                .ok_or("original clock proof lost its actual peer reply")?;
            let expected = clock_proof(
                &record.manifest,
                &state.profiles[&intent.profile_revision],
                intent,
                original,
                intent
                    .received_at_ms
                    .ok_or("original clock receive sample absent")?,
                record.budget.remaining(
                    intent
                        .received_kernel
                        .as_ref()
                        .ok_or("original receive kernel sample absent")?,
                ),
            )?;
            if serde_json::to_value(proof).map_err(|error| error.to_string())?
                != serde_json::to_value(expected).map_err(|error| error.to_string())?
            {
                return Err(
                    "original clock translation changed its actual request/reply evidence".into(),
                );
            }
        }
        for (domain, intents) in &record.intents {
            if !record.manifest.participants.contains_key(domain) || intents.len() > 7 {
                return Err("original peer intent domains or phases invalid".into());
            }
            for (phase, intent) in intents {
                intent.budget.validate()?;
                if intent.observations.len() > 16
                    || !state.profiles.contains_key(&intent.profile_revision)
                    || intent.request_digest
                        != digest(&if phase == "observe" {
                            None
                        } else {
                            Some(body(record, domain, phase)?)
                        })?
                {
                    return Err("original peer intent wire/profile identity changed".into());
                }
                for observation in &intent.observations {
                    observation.budget.validate()?;
                }
            }
        }
    }
    bounded(controller)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// An actual owned TCP listener receives no connection after the original SQL intent expires.
    #[test]
    fn original_sql_queue_consumes_peer_io_budget_before_any_connection() {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        listener.set_nonblocking(true).unwrap();
        let mut clock = serde_json::to_value(KernelTime::observe().unwrap()).unwrap();
        clock["elapsed_ms"] = 0.into();
        let clock: KernelTime = serde_json::from_value(clock).unwrap();
        let work = Work {
            id: "expired-original".into(),
            domain: "declared-peer".into(),
            phase: "prepare".into(),
            body: Some(serde_json::json!({"original":"effect"})),
            observation: None,
            peer: Peer {
                address: listener.local_addr().unwrap(),
                server_name: "localhost".into(),
                server_leaf: format!("sha256:{}", "a".repeat(64)),
                ca_file: "/unavailable-original-credential".into(),
                ca_digest: format!("sha256:{}", "b".repeat(64)),
                client_cert: "/unavailable-original-leaf".into(),
                client_leaf: format!("sha256:{}", "c".repeat(64)),
                client_key: "/unavailable-original-key".into(),
                principal_id: "declared-original".into(),
            },
            tenant: "original-tenant".into(),
            budget: OriginalBudget::new(&clock, 100).unwrap(),
        };
        let original = serde_json::to_value(&work.budget).unwrap();
        assert!(
            work.send()
                .unwrap_err()
                .contains("budget expired before peer I/O")
        );
        assert_eq!(
            listener.accept().unwrap_err().kind(),
            std::io::ErrorKind::WouldBlock
        );
        assert_eq!(serde_json::to_value(&work.budget).unwrap(), original);
    }

    /// Builds only synthetic readonly effect history, with an actual local original clock sample.
    fn intent(value: serde_json::Value) -> Intent {
        Intent {
            request_digest: "synthetic".into(),
            sent_at_ms: 1000,
            budget: OriginalBudget::new(&KernelTime::observe().unwrap(), 1000).unwrap(),
            profile_revision: 1,
            reply: Some(Reply {
                status: 200,
                body: value,
                leaf: "synthetic".into(),
            }),
            received_at_ms: Some(1010),
            received_kernel: Some(KernelTime::observe().unwrap()),
            error: None,
            observations: Vec::new(),
        }
    }

    /// An authenticated successful GET never promotes Running or a missing original goal to completion.
    #[test]
    fn actual_http_success_is_not_global_goal_satisfaction() {
        for value in [
            serde_json::json!({"mission_lifecycle":"Running","goal_contract":{},"goal_receipts":[{}]}),
            serde_json::json!({"mission_lifecycle":"Completed","goal_contract":null,"goal_receipts":[{}]}),
            serde_json::json!({"mission_lifecycle":"Completed","goal_contract":{},"goal_receipts":[]}),
        ] {
            assert!(effect(&intent(value), "observe").is_none());
        }
        assert!(effect(&intent(serde_json::json!({"mission_lifecycle":"Completed","goal_contract":{},"goal_receipts":[{}]})),"observe").is_some());
    }

    /// Losing the original reply preserves its Unknown while independent original peer evidence may reconcile it.
    #[test]
    fn read_observation_does_not_replace_original_uncertain_effect() {
        let mut original = intent(serde_json::json!({}));
        original.reply = None;
        original.error = Some("actual reply unavailable".into());
        original.observations.push(Observation {
            budget: OriginalBudget::new(&KernelTime::observe().unwrap(), 1000).unwrap(),
            sent_at_ms: 1100,
            reply: Some(Reply {
                status: 200,
                body: serde_json::json!({"prepared":{"phase":"prepared","original":"exact"}}),
                leaf: "synthetic".into(),
            }),
            error: None,
        });
        assert_eq!(effect(&original, "prepare").unwrap()["original"], "exact");
        assert!(original.reply.is_none());
        assert_eq!(original.error.as_deref(), Some("actual reply unavailable"));
    }
}
