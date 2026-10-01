//! Deployment-owned verifier source and strict, bounded verdict decoding.

use crate::*;
use ports::EventSink;
use serde::Deserialize;
use sha2::{Digest, Sha256};

/// Maximum size of either verifier document read from a configured local path.
const MAX_VERIFIER_DOCUMENT_BYTES: u64 = 256 * 1024;
/// Source descriptor schema shared with deployment-owned verifier producers.
const SOURCE_SCHEMA: &str = "roboguide.task-verifier-source/v0.1";
/// Verdict schema shared with deployment-owned verifier producers.
const VERDICT_SCHEMA: &str = "roboguide.task-verifier-verdict/v0.1";

/// One configured verifier source frozen before any Mission is submitted.
#[derive(Clone)]
pub(crate) struct TaskVerifierFeed {
    /// Exact source descriptor accepted at Controller startup.
    source: VerifierSource,
    /// Local deployment-owned atomic verdict artifact for this run.
    verdict_path: PathBuf,
}

/// Benchmark-neutral identity of the world used by a verifier source.
#[derive(Clone, Deserialize)]
#[serde(deny_unknown_fields)]
struct VerifierWorldIdentity {
    /// Run-local source identity.
    run_id: String,
    /// Environment episode identity.
    episode_id: String,
    /// Environment scene identity.
    scene_id: String,
    /// Dataset revision supplied by the deployment.
    dataset_revision: String,
    /// Exact dataset byte digest supplied by the deployment.
    dataset_sha256: String,
}

/// Exact canonical verifier contract, independent of a concrete provider.
#[derive(Clone, Deserialize, PartialEq, Eq)]
#[serde(deny_unknown_fields)]
struct VerifierContract {
    /// Capability namespace.
    namespace: String,
    /// Capability name.
    name: String,
    /// Capability version.
    version: String,
}

/// Startup-frozen source declaration and its content digest.
#[derive(Clone, Deserialize)]
#[serde(deny_unknown_fields)]
struct VerifierSource {
    /// Versioned source shape.
    schema_version: String,
    /// Deployment-owned producer identity.
    source_id: String,
    /// Source snapshot revision, such as a semantic-goal digest.
    source_revision: String,
    /// Frozen run, episode, scene, and dataset identity.
    identity: VerifierWorldIdentity,
    /// Exact advertised verifier contract.
    verifier: VerifierContract,
    /// Exact predicates this source can judge from its authoritative input.
    supported_predicates: Vec<String>,
    /// Final verdict can close a failed Task after local execution ends.
    verdict_finality: String,
    /// Canonical SHA-256 of all other fields.
    digest: String,
}

/// One current physical Role attempt covered by a verifier verdict.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct VerifierAttempt {
    /// Task-local Role identity.
    pub(crate) role_id: String,
    /// Runtime-owned physical attempt identity forwarded by Node workflow mapping.
    pub(crate) attempt_id: String,
}

/// One Task covered by a final verifier observation.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct VerifierTask {
    /// Mission identity captured in the canonical Local EAIOS invocation.
    pub(crate) mission_id: String,
    /// Task identity captured in the canonical Local EAIOS invocation.
    pub(crate) task_id: String,
    /// Complete current Role attempt set for the Task.
    pub(crate) attempts: Vec<VerifierAttempt>,
}

/// Exact final verdict loaded from the configured producer artifact.
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct VerifierVerdict {
    /// Versioned verdict shape.
    schema_version: String,
    /// Exact startup-frozen source descriptor digest.
    source_digest: String,
    /// Exact configured source identity.
    source_id: String,
    /// Verifier capability that produced the verdict.
    verifier: VerifierContract,
    /// Exact semantic predicate assessed.
    predicate: String,
    /// Source-local observation time, retained as provenance only.
    pub(crate) source_observed_at_ms: u64,
    /// Official final positive or negative result.
    pub(crate) satisfied: bool,
    /// Task/attempt identities whose accepted execution led to this observation.
    pub(crate) tasks: Vec<VerifierTask>,
    /// Canonical SHA-256 of all other fields.
    pub(crate) digest: String,
}

impl TaskVerifierFeed {
    /// Loads one configured source before serving Mission requests.
    pub(crate) fn load(source_path: &Path, verdict_path: PathBuf) -> Result<Self, String> {
        let body = read_bounded(source_path)?;
        let source: VerifierSource = serde_json::from_slice(&body)
            .map_err(|error| format!("invalid task verifier source: {error}"))?;
        if source.schema_version != SOURCE_SCHEMA
            || source.verdict_finality != "terminal"
            || !valid_digest(&source.source_revision)
            || !valid_text(&source.source_id)
            || !valid_text(&source.identity.run_id)
            || !valid_text(&source.identity.episode_id)
            || !valid_text(&source.identity.scene_id)
            || !valid_text(&source.identity.dataset_revision)
            || !valid_hex_sha256(&source.identity.dataset_sha256)
            || source.supported_predicates.len() > 1
            || source
                .supported_predicates
                .iter()
                .any(|item| !valid_text(item))
            || !valid_digest(&source.digest)
            || document_digest(&body)? != source.digest
        {
            return Err("task verifier source identity, schema, or digest is invalid".into());
        }
        domain::CapabilityContractRef::new(
            &source.verifier.namespace,
            &source.verifier.name,
            &source.verifier.version,
        )
        .map_err(|error| format!("task verifier contract is invalid: {error}"))?;
        Ok(Self {
            source,
            verdict_path,
        })
    }

    /// Requires every verifier-backed Task to have a real supported final source.
    pub(crate) fn validate_plan(&self, plan: &domain::MissionPlan) -> Result<(), String> {
        for task in plan.task_graph().tasks() {
            let domain::TaskSatisfactionBasis::VerifierEvidence(spec) = task.satisfaction_basis()
            else {
                continue;
            };
            if !self.supports(spec) {
                return Err(format!(
                    "Task {} requires verifier evidence absent from this deployment source",
                    task.task_id()
                ));
            }
            if plan.task_graph().tasks().iter().any(|other| {
                other
                    .dependencies()
                    .iter()
                    .any(|dependency| dependency == task.task_id())
            }) {
                return Err(format!(
                    "Task {} requires a final verifier verdict before dependent Tasks can run",
                    task.task_id()
                ));
            }
            if plan.actors().len() == 1 && plan.task_graph().tasks().len() > 1 {
                let mut prerequisites = BTreeSet::new();
                let mut pending = task.dependencies().to_vec();
                while let Some(dependency) = pending.pop() {
                    if !prerequisites.insert(dependency.clone()) {
                        continue;
                    }
                    let predecessor = plan
                        .task_graph()
                        .tasks()
                        .iter()
                        .find(|candidate| candidate.task_id() == &dependency)
                        .ok_or_else(|| "verifier Task has an unknown prerequisite".to_string())?;
                    pending.extend_from_slice(predecessor.dependencies());
                }
                if plan.task_graph().tasks().iter().any(|other| {
                    other.task_id() != task.task_id() && !prerequisites.contains(other.task_id())
                }) {
                    return Err(format!(
                        "Task {} needs a final verifier verdict but single-Actor execution has an unordered Task that could await the same terminal world",
                        task.task_id()
                    ));
                }
            }
        }
        Ok(())
    }

    /// Returns whether one accepted spec matches this exact advertised source.
    pub(crate) fn supports(&self, spec: &domain::VerifierSatisfactionSpec) -> bool {
        spec.verifier().namespace() == self.source.verifier.namespace
            && spec.verifier().name() == self.source.verifier.name
            && spec.verifier().version() == self.source.verifier.version
            && self.source.supported_predicates == [spec.predicate()]
    }

    /// Reads one atomic verdict, reporting malformed or altered evidence explicitly.
    pub(crate) fn read_verdict(&self) -> Result<Option<VerifierVerdict>, String> {
        match std::fs::metadata(&self.verdict_path) {
            Ok(_) => {}
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
            Err(error) => {
                return Err(format!(
                    "task verifier verdict cannot be inspected: {error}"
                ));
            }
        }
        let body = read_bounded(&self.verdict_path)?;
        let verdict: VerifierVerdict = serde_json::from_slice(&body)
            .map_err(|error| format!("invalid task verifier verdict: {error}"))?;
        if verdict.schema_version != VERDICT_SCHEMA
            || verdict.source_digest != self.source.digest
            || verdict.source_id != self.source.source_id
            || verdict.verifier != self.source.verifier
            || self.source.supported_predicates != [verdict.predicate.as_str()]
            || !valid_digest(&verdict.digest)
            || document_digest(&body)? != verdict.digest
            || verdict.source_observed_at_ms == 0
            || verdict.tasks.is_empty()
        {
            return Err("task verifier verdict source, predicate, or digest is invalid".into());
        }
        let mut seen_tasks = BTreeSet::new();
        for task in &verdict.tasks {
            if !valid_text(&task.mission_id)
                || !valid_text(&task.task_id)
                || !seen_tasks.insert((&task.mission_id, &task.task_id))
                || task.attempts.is_empty()
            {
                return Err("task verifier verdict has invalid Task coverage".into());
            }
            let mut seen_roles = BTreeSet::new();
            for attempt in &task.attempts {
                if !valid_text(&attempt.role_id)
                    || !valid_text(&attempt.attempt_id)
                    || !seen_roles.insert(&attempt.role_id)
                {
                    return Err("task verifier verdict has invalid Role attempt coverage".into());
                }
            }
        }
        Ok(Some(verdict))
    }

    /// Attributes accepted evidence to its configured source, not to a model or a Node.
    pub(crate) fn source(&self) -> domain::StateSource {
        domain::StateSource::roboguide(self.source.source_id.clone())
            .expect("startup source identity was validated")
    }

    /// Returns the immutable source revision retained in durable event evidence.
    pub(crate) fn source_revision(&self) -> &str {
        &self.source.source_revision
    }

    /// Returns the configured producer name for durable attribution.
    pub(crate) fn source_id(&self) -> &str {
        &self.source.source_id
    }

    /// Returns the exact source document identity used to fence Controller restore.
    pub(crate) fn source_digest(&self) -> &str {
        &self.source.digest
    }
}

/// Applies one exact final verdict inside the Controller's existing atomic timer transaction.
pub(crate) fn validate_task_verifier(
    controller: &ControllerState,
    feed: &TaskVerifierFeed,
    verdict: &VerifierVerdict,
) -> Result<(), String> {
    if controller.verifier_source_digest.as_deref() != Some(feed.source_digest())
        || verdict.verifier != feed.source.verifier
        || feed.source.supported_predicates != [verdict.predicate.as_str()]
    {
        return Err("verifier verdict does not match the frozen source".into());
    }
    for row in &verdict.tasks {
        if controller.verifier_seen.contains(&(
            verdict.digest.clone(),
            row.mission_id.clone(),
            row.task_id.clone(),
        )) {
            continue;
        }
        let mission_id = domain::MissionId::new(&row.mission_id)
            .map_err(|error| format!("verifier Mission identity is invalid: {error}"))?;
        let task_id = domain::TaskId::new(&row.task_id)
            .map_err(|error| format!("verifier Task identity is invalid: {error}"))?;
        let task_ref = domain::TaskRef::new(mission_id.clone(), task_id);
        let execution = controller
            .orchestrator
            .execution(&mission_id)
            .ok_or_else(|| "verifier verdict refers to an unknown Mission".to_string())?;
        let planned = execution
            .plan()
            .task_graph()
            .tasks()
            .iter()
            .find(|task| task.requirement().task_ref() == &task_ref)
            .ok_or_else(|| "verifier verdict refers to an unknown Task".to_string())?;
        if let domain::TaskSatisfactionBasis::VerifierEvidence(spec) = planned.satisfaction_basis()
            && !feed.supports(spec)
        {
            return Err("verifier verdict differs from the accepted Task specification".into());
        }
        let mut expected = BTreeSet::new();
        for role in planned.requirement().roles() {
            let attempt_id = controller
                .bridge
                .current_task_attempt_id(execution.group_id(), &task_ref, role.role_id())
                .ok_or_else(|| {
                    "verifier verdict arrived before a current physical attempt".to_string()
                })?;
            expected.insert((role.role_id().as_str().to_string(), attempt_id.to_string()));
        }
        let observed = row
            .attempts
            .iter()
            .map(|attempt| (attempt.role_id.clone(), attempt.attempt_id.clone()))
            .collect::<BTreeSet<_>>();
        if observed != expected || observed.len() != row.attempts.len() {
            return Err("verifier verdict does not cover the exact current Role attempts".into());
        }
    }
    Ok(())
}

/// Fences a restored database to the same verifier producer snapshot.
pub(crate) fn validate_restored_verifier_source(
    checkpoint: &ServerCheckpoint,
    configured_digest: Option<&str>,
) -> Result<(), String> {
    if matches!(
        checkpoint.schema.as_str(),
        SERVER_CHECKPOINT_SCHEMA | PREVIOUS_SERVER_CHECKPOINT_SCHEMA
    ) && checkpoint.verifier_source_digest.as_deref() != configured_digest
    {
        return Err("controller verifier source changed across checkpoint restore".into());
    }
    if !checkpoint.verifier_seen.is_empty() && checkpoint.verifier_source_digest.is_none() {
        return Err("controller verifier receipts lack a source identity".into());
    }
    Ok(())
}

/// Advances one validated verifier verdict inside the Controller timer transaction.
pub(crate) fn apply_task_verifier(
    controller: &mut ControllerState,
    feed: &TaskVerifierFeed,
    verdict: &VerifierVerdict,
    now: domain::TimestampMs,
    correlation_id: &domain::CorrelationId,
    events: &mut state::SqliteEventLog,
) -> Result<(), String> {
    for row in &verdict.tasks {
        let mission_id = domain::MissionId::new(&row.mission_id)
            .expect("verdict identities were validated before mutation");
        let task_ref = domain::TaskRef::new(
            mission_id.clone(),
            domain::TaskId::new(&row.task_id)
                .expect("verdict identities were validated before mutation"),
        );
        let key = (
            verdict.digest.clone(),
            row.mission_id.clone(),
            row.task_id.clone(),
        );
        if controller.verifier_seen.contains(&key) {
            continue;
        }
        let Some(execution) = controller.orchestrator.execution(&mission_id) else {
            continue;
        };
        if matches!(
            execution.lifecycle(),
            orchestration::MissionExecutionLifecycle::Completed
                | orchestration::MissionExecutionLifecycle::Failed
                | orchestration::MissionExecutionLifecycle::Cancelling
                | orchestration::MissionExecutionLifecycle::Cancelled
        ) {
            // A terminal Runtime failure may release the Group before the
            // final-world producer publishes its verdict. That verdict cannot
            // reopen Mission satisfaction or fail the released Group again.
            continue;
        }
        let group_id = execution.group_id().clone();
        let awaiting = controller
            .bridge
            .control()
            .group(&group_id)
            .and_then(|group| group.task_execution(&task_ref))
            .is_some_and(|task| {
                task.lifecycle() == domain::TaskExecutionLifecycle::AwaitingSatisfaction
            });
        if !awaiting {
            continue;
        }
        let Some(domain::TaskSatisfactionBasis::VerifierEvidence(spec)) = controller
            .orchestrator
            .task_satisfaction_basis(&mission_id, &task_ref)
        else {
            continue;
        };
        let evidence = domain::TaskSatisfactionEvidence::new(
            task_ref.clone(),
            spec.verifier().clone(),
            verdict.predicate.clone(),
            feed.source(),
            domain::TimestampMs::new(verdict.source_observed_at_ms),
            now,
            verdict.satisfied,
        )
        .map_err(|error| error.to_string())?;
        events.append(
            now,
            correlation_id,
            None,
            domain::EventPayload::TaskVerifierVerdictObserved {
                task_ref: task_ref.clone(),
                source_id: feed.source_id().to_string(),
                source_revision: feed.source_revision().to_string(),
                verdict_digest: verdict.digest.clone(),
                satisfied: verdict.satisfied,
            },
        );
        let ControllerState {
            bridge,
            orchestrator,
            verifier_seen,
            ..
        } = controller;
        if verdict.satisfied {
            orchestrator
                .satisfy_task_from_verifier(
                    &mission_id,
                    &evidence,
                    bridge.control_mut(),
                    now,
                    correlation_id,
                    events,
                )
                .map_err(|error| error.to_string())?;
        } else {
            orchestrator
                .task_failed(
                    &mission_id,
                    &task_ref,
                    "final independent verifier verdict was negative",
                    bridge.control_mut(),
                    now,
                    correlation_id,
                    events,
                )
                .map_err(|error| error.to_string())?;
        }
        verifier_seen.insert(key);
        close_terminal_mission_coordination(controller, &mission_id);
    }
    Ok(())
}

/// Reads one configured file with an explicit byte budget.
fn read_bounded(path: &Path) -> Result<Vec<u8>, String> {
    let metadata = std::fs::metadata(path).map_err(|error| {
        format!(
            "task verifier file {} is unavailable: {error}",
            path.display()
        )
    })?;
    if metadata.len() > MAX_VERIFIER_DOCUMENT_BYTES {
        return Err("task verifier document exceeds the byte limit".into());
    }
    let bytes = std::fs::read(path).map_err(|error| {
        format!(
            "task verifier file {} cannot be read: {error}",
            path.display()
        )
    })?;
    if bytes.len() as u64 > MAX_VERIFIER_DOCUMENT_BYTES {
        return Err("task verifier document exceeds the byte limit".into());
    }
    Ok(bytes)
}

/// Computes a canonical SHA-256 without trusting a claimed document digest.
fn document_digest(body: &[u8]) -> Result<String, String> {
    let mut value: serde_json::Value = serde_json::from_slice(body)
        .map_err(|error| format!("task verifier JSON is invalid: {error}"))?;
    let object = value
        .as_object_mut()
        .ok_or_else(|| "task verifier document must be an object".to_string())?;
    object.remove("digest");
    let canonical = serde_json::to_vec(&value)
        .map_err(|error| format!("task verifier canonicalization failed: {error}"))?;
    Ok(format!("sha256:{:x}", Sha256::digest(canonical)))
}

/// Rejects blank or unbounded source labels and predicates.
fn valid_text(value: &str) -> bool {
    !value.trim().is_empty() && value.len() <= 4096
}

/// Validates one prefixed SHA-256 identity without accepting decoration.
fn valid_digest(value: &str) -> bool {
    value.strip_prefix("sha256:").is_some_and(valid_hex_sha256)
}

/// Validates exactly 32 SHA-256 bytes in lowercase hexadecimal.
fn valid_hex_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Adds the canonical digest used by both deployment producer and Controller decoder.
    fn signed(mut value: serde_json::Value) -> serde_json::Value {
        let encoded = serde_json::to_vec(&value).expect("finite fixture JSON");
        value["digest"] =
            serde_json::Value::String(format!("sha256:{:x}", Sha256::digest(encoded)));
        value
    }

    /// Freezes one neutral source supporting only one exact predicate.
    fn source() -> serde_json::Value {
        signed(serde_json::json!({
            "schema_version": SOURCE_SCHEMA,
            "source_id": "test-official-goal",
            "source_revision": format!("sha256:{}", "a".repeat(64)),
            "identity": {
                "run_id": "run-1", "episode_id": "episode-1", "scene_id": "scene-1",
                "dataset_revision": "dataset-1", "dataset_sha256": "b".repeat(64)
            },
            "verifier": {"namespace": "observation", "name": "verify", "version": "v1"},
            "supported_predicates": ["goal-predicate"],
            "verdict_finality": "terminal"
        }))
    }

    /// Installs one isolated source with an absent verdict until the producer publishes.
    fn feed(directory: &tempfile::TempDir) -> TaskVerifierFeed {
        let source_path = directory.path().join("source.json");
        std::fs::write(&source_path, source().to_string()).expect("source written");
        TaskVerifierFeed::load(&source_path, directory.path().join("verdict.json"))
            .expect("source admitted")
    }

    /// A missing source cannot be inferred from a MissionPlan verifier contract alone.
    #[test]
    fn source_requires_exact_schema_and_digest() {
        let directory = tempfile::tempdir().expect("directory");
        let source_path = directory.path().join("source.json");
        let mut altered = source();
        altered["identity"]["scene_id"] = "other-scene".into();
        std::fs::write(&source_path, altered.to_string()).expect("tampered source written");
        assert!(
            TaskVerifierFeed::load(&source_path, directory.path().join("verdict.json")).is_err()
        );
        let mut unsupported = source();
        unsupported["schema_version"] = "future".into();
        let unsupported = signed(serde_json::json!({
            "schema_version": unsupported["schema_version"],
            "source_id": unsupported["source_id"],
            "source_revision": unsupported["source_revision"],
            "identity": unsupported["identity"],
            "verifier": unsupported["verifier"],
            "supported_predicates": unsupported["supported_predicates"],
            "verdict_finality": unsupported["verdict_finality"]
        }));
        std::fs::write(&source_path, unsupported.to_string()).expect("future source written");
        assert!(
            TaskVerifierFeed::load(&source_path, directory.path().join("verdict.json")).is_err()
        );
    }

    /// The Controller decodes the exact source bytes produced by the Python adapter.
    #[test]
    fn python_producer_source_fixture_is_accepted_cross_language() {
        let source_path = Path::new(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../integrations/habitat-local-eaios/tests/fixtures/task-verifier-source.json"
        ));
        let verdict_path = PathBuf::from(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../../integrations/habitat-local-eaios/tests/fixtures/task-verifier-verdict.json"
        ));
        let feed = TaskVerifierFeed::load(source_path, verdict_path)
            .expect("Python producer source is accepted");
        assert_eq!(feed.source.identity.episode_id, "51");
        assert_eq!(
            feed.source.supported_predicates,
            ["any_at(any_targets|0) AND any_at(TARGET_any_targets|0)"]
        );
        let verdict = feed
            .read_verdict()
            .expect("Python verdict decodes")
            .expect("verdict");
        assert!(verdict.satisfied);
        assert_eq!(verdict.tasks[0].attempts[0].attempt_id, "attempt-a");
    }

    /// Rejected, oversized, and stale-source verdict files never become positive evidence.
    #[test]
    fn verdict_rejects_tamper_duplicate_and_oversize() {
        let directory = tempfile::tempdir().expect("directory");
        let feed = feed(&directory);
        assert!(feed.read_verdict().expect("absence is explicit").is_none());
        let path = directory.path().join("verdict.json");
        let body = signed(serde_json::json!({
            "schema_version": VERDICT_SCHEMA,
            "source_digest": source()["digest"],
            "source_id": "test-official-goal",
            "verifier": {"namespace": "observation", "name": "verify", "version": "v1"},
            "predicate": "goal-predicate", "source_observed_at_ms": 10,
            "satisfied": true,
            "tasks": [{"mission_id": "mission", "task_id": "task",
                "attempts": [{"role_id": "role", "attempt_id": "attempt"}]}]
        }));
        std::fs::write(&path, body.to_string()).expect("verdict written");
        assert!(feed.read_verdict().expect("valid verdict").is_some());
        let mut altered = body.clone();
        altered["satisfied"] = false.into();
        std::fs::write(&path, altered.to_string()).expect("tamper written");
        assert!(feed.read_verdict().is_err());
        let mut duplicate = body.clone();
        duplicate["tasks"][0]["attempts"]
            .as_array_mut()
            .expect("array")
            .push(serde_json::json!({"role_id": "role", "attempt_id": "other"}));
        let duplicate = signed(serde_json::json!({
            "schema_version": duplicate["schema_version"],
            "source_digest": duplicate["source_digest"],
            "source_id": duplicate["source_id"],
            "verifier": duplicate["verifier"],
            "predicate": duplicate["predicate"],
            "source_observed_at_ms": duplicate["source_observed_at_ms"],
            "satisfied": duplicate["satisfied"],
            "tasks": duplicate["tasks"]
        }));
        std::fs::write(&path, duplicate.to_string()).expect("duplicate written");
        assert!(feed.read_verdict().is_err());
        std::fs::write(&path, vec![b' '; MAX_VERIFIER_DOCUMENT_BYTES as usize + 1])
            .expect("oversized document written");
        assert!(feed.read_verdict().is_err());
    }

    /// An exact source is required for verifier plans; ordinary execution reports still work.
    #[test]
    fn plan_admission_requires_actual_supported_predicate() {
        let directory = tempfile::tempdir().expect("directory");
        let feed = feed(&directory);
        let mut plan: serde_json::Value = serde_json::from_str(include_str!(
            "../../../../scenarios/e1-shared-world-episode-51/mission-plan.json"
        ))
        .expect("plan fixture");
        let ordinary = orchestration::decode_mission_plan(&plan.to_string()).expect("plan decodes");
        assert!(feed.validate_plan(&ordinary).is_ok());
        plan["tasks"][0]["satisfaction"] = serde_json::json!({
            "expected_effect": "goal-predicate", "basis": "verifier-evidence",
            "verifier": {
                "contract": {"namespace": "observation", "name": "verify", "version": "v1"},
                "predicate": "goal-predicate", "max_evidence_age_ms": 5000
            }
        });
        let supported =
            orchestration::decode_mission_plan(&plan.to_string()).expect("plan decodes");
        assert!(feed.validate_plan(&supported).is_ok());
        plan["tasks"][0]["satisfaction"]["verifier"]["predicate"] = "other".into();
        let unsupported =
            orchestration::decode_mission_plan(&plan.to_string()).expect("plan decodes");
        assert!(feed.validate_plan(&unsupported).is_err());
    }

    /// A final-world verdict cannot be required before a serial Actor's next segment runs.
    #[test]
    fn serial_verifier_task_must_be_last_in_accepted_dag() {
        let directory = tempfile::tempdir().expect("directory");
        let feed = feed(&directory);
        let mut plan: serde_json::Value = serde_json::from_str(include_str!(
            "../../../../scenarios/e1-shared-world-episode-51/mission-plan.json"
        ))
        .expect("plan fixture");
        plan["mission"]["actors"] = serde_json::json!([{"id": "shared-robot-a"}]);
        plan["contexts"][1]["roles"][0]["actor"] = "shared-robot-a".into();
        plan["tasks"][1]["satisfaction"] = serde_json::json!({
            "expected_effect": "goal-predicate", "basis": "verifier-evidence",
            "verifier": {
                "contract": {"namespace": "observation", "name": "verify", "version": "v1"},
                "predicate": "goal-predicate", "max_evidence_age_ms": 5000
            }
        });
        let unordered =
            orchestration::decode_mission_plan(&plan.to_string()).expect("same Actor plan decodes");
        assert!(feed.validate_plan(&unordered).is_err());
        plan["tasks"][1]["depends_on"] = serde_json::json!(["task-navigate-goal-a"]);
        let ordered =
            orchestration::decode_mission_plan(&plan.to_string()).expect("ordered plan decodes");
        assert!(feed.validate_plan(&ordered).is_ok());
        plan["tasks"][0]["satisfaction"] = plan["tasks"][1]["satisfaction"].clone();
        let premature = orchestration::decode_mission_plan(&plan.to_string())
            .expect("premature verifier plan decodes");
        assert!(feed.validate_plan(&premature).is_err());
    }

    /// A new wrapper cannot silently replay old verdict receipts under another source.
    #[test]
    fn checkpoint_restore_requires_the_same_verifier_source() {
        let checkpoint = ServerCheckpoint {
            schema: SERVER_CHECKPOINT_SCHEMA.to_string(),
            integration_json: String::new(),
            orchestration_json: String::new(),
            mission_admissions: BTreeMap::new(),
            verifier_seen: BTreeSet::new(),
            verifier_source_digest: Some("sha256:source-a".to_string()),
        };
        assert!(validate_restored_verifier_source(&checkpoint, Some("sha256:source-a")).is_ok());
        assert!(validate_restored_verifier_source(&checkpoint, Some("sha256:source-b")).is_err());
        assert!(validate_restored_verifier_source(&checkpoint, None).is_err());
        let mut missing = checkpoint;
        missing.verifier_source_digest = None;
        missing
            .verifier_seen
            .insert(("verdict".into(), "mission".into(), "task".into()));
        assert!(validate_restored_verifier_source(&missing, None).is_err());
    }
}
