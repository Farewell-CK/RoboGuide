//! Immutable neutral verifier-source/verdict codec; no Controller or lifecycle mutation authority.

use serde::Deserialize;
use sha2::{Digest, Sha256};
use std::collections::BTreeSet;
use std::path::{Path, PathBuf};

/// Maximum size of either verifier document read from a configured local path.
pub(super) const MAX_VERIFIER_DOCUMENT_BYTES: u64 = 256 * 1024;
/// Source descriptor schema shared with deployment-owned verifier producers.
pub(super) const SOURCE_SCHEMA: &str = "roboguide.task-verifier-source/v0.1";
/// Verdict schema shared with deployment-owned verifier producers.
pub(super) const VERDICT_SCHEMA: &str = "roboguide.task-verifier-verdict/v0.1";

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

impl VerifierVerdict {
    /// Returns the exact validated semantic predicate without exposing mutable source fields.
    pub(super) fn predicate(&self) -> &str {
        &self.predicate
    }
}

impl TaskVerifierFeed {
    /// Rechecks the immutable verifier contract/predicate before the application uses a verdict.
    pub(super) fn matches_verdict(&self, verdict: &VerifierVerdict) -> bool {
        verdict.verifier == self.source.verifier
            && self.source.supported_predicates == [verdict.predicate.as_str()]
    }

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
}
