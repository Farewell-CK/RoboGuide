//! Authenticated deployment publication on the original writer, checkpoint and command journal.

use crate::*;
use domain::RegistryCommandKind;
use serde::{Deserialize, Serialize};
use sha2::{Digest as _, Sha256};

/// Closed retained feasibility history; physical routing remains external current deployment evidence.
#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct PublishedDeployments {
    /// Required opt-in generation; old Control receipt enum readers refuse its commands.
    schema: String,
    /// Stable publication authority, independent of each source World's original identity.
    registry_id: String,
    /// Original startup source which must still be explicitly supplied at cold restore.
    original_digest: Option<String>,
    /// Complete original documents for every accepted version, never renewed on read or restore.
    history: BTreeMap<u64, serde_json::Value>,
    /// Original physical provenance checked by each publication's original application transaction.
    physical_revisions: BTreeMap<u64, u64>,
    /// Validated immutable interpretations, rebuilt from original evidence before cold service.
    #[serde(skip)]
    compiled: BTreeMap<u64, Arc<DeploymentFeasibility>>,
}

/// Historical application wrappers cannot conceal opted-in online publication authority.
pub(crate) fn validate_wrapper(saved: &ServerCheckpoint) -> Result<(), String> {
    let integration: serde_json::Value =
        serde_json::from_str(&saved.integration_json).map_err(|e| e.to_string())?;
    let online = integration
        .get("control")
        .and_then(|c| c.get("deployment_publication_history"))
        .and_then(serde_json::Value::as_object)
        .is_some_and(|h| !h.is_empty());
    if saved.schema != SERVER_CHECKPOINT_SCHEMA && (saved.published_deployments.is_some() || online)
    {
        return Err(
            "historical wrapper cannot contain online deployment publication authority".into(),
        );
    }
    Ok(())
}

/// Complete expected-revision physical deployment command.
#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct PhysicalCommand {
    /// Immutable operator command identity.
    command_id: String,
    /// Exact current externally installed registry revision; zero explicitly bootstraps.
    expected_revision: u64,
    /// Complete existing physical-entities file format, not a partial patch.
    registry: serde_json::Value,
}

/// Complete expected-revision feasibility publication command.
#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct FeasibilityCommand {
    /// Immutable operator command identity.
    command_id: String,
    /// Stable publication authority selected on first adoption.
    registry_id: String,
    /// Exact previous published version; zero adopts the supplied startup interpretation.
    expected_revision: u64,
    /// Monotonic version for this document, strictly previous plus one.
    revision: u64,
    /// Current physical registry checked inside the same original writer.
    expected_physical_revision: u64,
    /// Complete content-addressed deployment source document, including its original digest.
    document: serde_json::Value,
}

/// Computes a typed canonical public command/document identity without touching clocks.
fn digest(value: &impl Serialize) -> Result<String, String> {
    let value = serde_json::to_value(value).map_err(|e| e.to_string())?;
    Ok(format!(
        "sha256:{:x}",
        Sha256::digest(serde_json::to_vec(&value).map_err(|e| e.to_string())?)
    ))
}

/// Resolves the current committed feasibility snapshot; startup stays the explicit legacy baseline.
pub(crate) fn current(
    controller: &ControllerState,
    startup: Option<&DeploymentFeasibility>,
) -> Result<Option<Arc<DeploymentFeasibility>>, String> {
    let Some(published) = &controller.published_deployments else {
        return Ok(startup.map(|p| Arc::new(p.clone())));
    };
    let (revision, _) = published
        .history
        .last_key_value()
        .ok_or("empty deployment publication history")?;
    published
        .compiled
        .get(revision)
        .cloned()
        .map(Some)
        .ok_or_else(|| "deployment publication has not been validated for service".into())
}

/// Finds a runtime policy's original retained interpretation instead of replacing it with current.
pub(crate) fn original(
    controller: &ControllerState,
    wanted: Option<&str>,
    startup: Option<&DeploymentFeasibility>,
) -> Result<Option<Arc<DeploymentFeasibility>>, String> {
    let Some(wanted) = wanted else {
        return Ok(None);
    };
    if let Some(startup) = startup.filter(|p| p.digest() == wanted) {
        return Ok(Some(Arc::new(startup.clone())));
    }
    if let Some(published) = &controller.published_deployments {
        for profile in published.compiled.values() {
            if profile.digest() == wanted {
                return Ok(Some(profile.clone()));
            }
        }
    }
    Err("runtime revision requires its original retained deployment profile".into())
}

/// Validates all online authority and exact startup reattachment before serving any restored work.
pub(crate) fn validate_restored(
    controller: &mut ControllerState,
    startup: Option<&DeploymentFeasibility>,
) -> Result<(), String> {
    if let Some(published) = &mut controller.published_deployments {
        published.compiled.clear();
    }
    let mut compiled = BTreeMap::new();
    let control = controller.bridge.control();
    let history = control.deployment_publications(RegistryCommandKind::DeploymentFeasibility);
    match (&controller.published_deployments, history) {
        (None, None) => {}
        (Some(published), Some(history)) => {
            if published.schema != "roboguide.published-deployments/v0.1"
                || published.registry_id.trim().is_empty()
                || published.registry_id.len() > 128
                || published.original_digest.as_deref() != startup.map(|p| p.digest())
                || published.history.is_empty()
                || published.history.len() != history.len()
                || published.history.len() > 128
                || published.physical_revisions.len() != published.history.len()
                || serde_json::to_vec(published)
                    .map_err(|e| e.to_string())?
                    .len()
                    > 8 * 1024 * 1024
            {
                return Err(
                    "deployment publication history/startup authority changed or missing".into(),
                );
            }
            for (index, (revision, document)) in published.history.iter().enumerate() {
                if published
                    .physical_revisions
                    .get(revision)
                    .is_none_or(|r| *r > control.physical_entity_registry_revision())
                {
                    return Err(
                        "publication physical provenance exceeds original Control authority".into(),
                    );
                }
                let profile = DeploymentFeasibility::from_document(document.clone())?;
                let entry = publication_document(published, *revision, &profile.source_document);
                if *revision != index as u64 + 1 || history.get(revision) != Some(&digest(&entry)?)
                {
                    return Err("deployment publication original document/receipt mismatch".into());
                }
                compiled.insert(*revision, Arc::new(profile));
            }
        }
        _ => {
            return Err(
                "deployment history or required Control publication evidence is missing".into(),
            );
        }
    }
    if let Some(published) = &mut controller.published_deployments {
        published.compiled = compiled;
    }
    Ok(())
}

/// Requires current external physical evidence to match the latest online publication on restart.
pub(crate) fn validate_physical_restore(controller: &ControllerState) -> Result<(), String> {
    let control = controller.bridge.control();
    if let Some(history) = control.deployment_publications(RegistryCommandKind::PhysicalEntities) {
        let snapshot = control
            .physical_entity_registry()
            .ok_or("online physical registry requires current deployment file on restore")?;
        if history.last_key_value()
            != Some((
                &snapshot.revision(),
                &digest(&super::physical_entity_registry::document(snapshot))?,
            ))
        {
            return Err(
                "current physical deployment file does not match latest online publication".into(),
            );
        }
    }
    Ok(())
}

/// Names the complete original feasibility publication, including its original startup lineage.
fn publication_document(
    published: &PublishedDeployments,
    revision: u64,
    document: &serde_json::Value,
) -> serde_json::Value {
    serde_json::json!({"schema":published.schema,"registry_id":published.registry_id,"original_digest":published.original_digest,"revision":revision,"physical_revision":published.physical_revisions.get(&revision),"document":document})
}

/// Projects the current physical registry without treating its declarations as actual device facts.
pub(crate) fn physical_view(controller: &ControllerState) -> serde_json::Value {
    serde_json::json!({"schema_version":"roboguide.physical-entities-view/v0.1","registry":controller.bridge.control().physical_entity_registry().map(super::physical_entity_registry::document)})
}

/// Projects current version metadata without refreshing or claiming any source measurement.
pub(crate) fn feasibility_view(
    controller: &ControllerState,
    startup: Option<&DeploymentFeasibility>,
) -> Result<serde_json::Value, String> {
    let profile = current(controller, startup)?;
    Ok(
        serde_json::json!({"schema_version":"roboguide.deployment-feasibility-view/v0.1","registry_id":controller.published_deployments.as_ref().map(|p| &p.registry_id),"revision":controller.published_deployments.as_ref().and_then(|p| p.history.last_key_value().map(|(r,_)| *r)).unwrap_or(0),"digest":profile.as_ref().map(|p| p.digest()),"document":profile.as_ref().map(|p| &p.source_document)}),
    )
}

/// Commits one immutable administrator result before publishing any candidate or routing change.
#[allow(clippy::too_many_arguments)]
fn commit(
    controller: &Arc<Mutex<ControllerState>>,
    log: &state::SqliteEventLog,
    gate: &dyn super::ApplicationWriteGate,
    mut candidate: ControllerState,
    command_id: String,
    kind: RegistryCommandKind,
    input_digest: String,
    registry_digest: String,
    revision: u64,
    subject: &str,
    now: domain::TimestampMs,
) -> Result<serde_json::Value, String> {
    candidate
        .bridge
        .control_mut()
        .retain_deployment_publication(kind, revision, registry_digest.clone())
        .map_err(|e| e.to_string())?;
    let receipt = domain::RegistryCommandReceipt {
        command_id,
        kind,
        input_digest,
        registry_digest,
        revision,
        authority_subject: subject.into(),
        accepted_at_ms: now.as_millis(),
    };
    candidate
        .bridge
        .control_mut()
        .retain_registry_command_receipt(receipt.clone())
        .map_err(|e| e.to_string())?;
    let checkpoint = server_checkpoint_json(&candidate).map_err(|e| e.to_string())?;
    log.begin_batch().map_err(|e| e.to_string())?;
    let transaction: Result<(), String> = (|| {
        let command = control_command_journal_entry_with_digest(
            format!("registry:{}", receipt.command_id),
            Some(subject.into()),
            receipt.input_digest.clone(),
            &checkpoint,
            log.current_batch_event_sequence()
                .map_err(|e| e.to_string())?,
        );
        log.save_checkpoint_with_command(SERVER_CHECKPOINT_SCHEMA, &checkpoint, command)
            .map_err(|e| e.to_string())?;
        gate.validate_locked()?;
        log.commit_batch().map_err(|e| e.to_string())
    })();
    if let Err(error) = transaction {
        let rollback = log.rollback_batch();
        return Err(format!(
            "deployment publication commit failed: {error}; rollback={rollback:?}"
        ));
    }
    *controller
        .lock()
        .map_err(|_| "Controller lock is poisoned")? = candidate;
    Ok(
        serde_json::json!({"schema_version":"roboguide.registry-command-receipt/v0.1","receipt":receipt}),
    )
}

/// Updates physical routing atomically while preserving every original Actor binding and provenance.
pub(crate) fn submit_physical(
    controller: &Arc<Mutex<ControllerState>>,
    log: &state::SqliteEventLog,
    gate: &dyn super::ApplicationWriteGate,
    body: &[u8],
    subject: &str,
    clock: &impl Clock,
) -> Result<serde_json::Value, String> {
    let input: PhysicalCommand = serde_json::from_slice(body).map_err(|e| e.to_string())?;
    let input_digest = digest(&input)?;
    let _writer = gate.lock()?;
    let mut candidate = controller
        .lock()
        .map_err(|_| "Controller lock is poisoned")?
        .clone();
    let kind = RegistryCommandKind::PhysicalEntities;
    if let Some(receipt) = candidate
        .bridge
        .control()
        .registry_command_receipt(&input.command_id, kind, &input_digest, subject)
        .map_err(|e| e.to_string())?
    {
        return Ok(
            serde_json::json!({"schema_version":"roboguide.registry-command-receipt/v0.1","receipt":receipt}),
        );
    }
    let current = candidate
        .bridge
        .control()
        .physical_entity_registry()
        .map_or(0, |p| p.revision());
    let snapshot = super::physical_entity_registry::parse_document(input.registry)?;
    if current != input.expected_revision || current.checked_add(1) != Some(snapshot.revision()) {
        return Err("physical registry expected revision conflicts".into());
    }
    let revision = snapshot.revision();
    let registry_digest = digest(&super::physical_entity_registry::document(&snapshot))?;
    candidate
        .bridge
        .control_mut()
        .install_physical_entity_registry(snapshot)
        .map_err(|e| e.to_string())?;
    candidate
        .bridge
        .control()
        .validate_physical_entity_registry_on_restore()
        .map_err(|e| e.to_string())?;
    commit(
        controller,
        log,
        gate,
        candidate,
        input.command_id,
        kind,
        input_digest,
        registry_digest,
        revision,
        subject,
        clock.now(),
    )
}

/// Publishes new-request feasibility without replacing any original runtime policy or clocks.
#[allow(clippy::too_many_arguments)]
pub(crate) fn submit_feasibility(
    controller: &Arc<Mutex<ControllerState>>,
    log: &state::SqliteEventLog,
    gate: &dyn super::ApplicationWriteGate,
    body: &[u8],
    subject: &str,
    clock: &impl Clock,
    startup: Option<&DeploymentFeasibility>,
) -> Result<serde_json::Value, String> {
    let input: FeasibilityCommand = serde_json::from_slice(body).map_err(|e| e.to_string())?;
    let input_digest = digest(&input)?;
    let _writer = gate.lock()?;
    let mut candidate = controller
        .lock()
        .map_err(|_| "Controller lock is poisoned")?
        .clone();
    let kind = RegistryCommandKind::DeploymentFeasibility;
    if let Some(receipt) = candidate
        .bridge
        .control()
        .registry_command_receipt(&input.command_id, kind, &input_digest, subject)
        .map_err(|e| e.to_string())?
    {
        return Ok(
            serde_json::json!({"schema_version":"roboguide.registry-command-receipt/v0.1","receipt":receipt}),
        );
    }
    if input.registry_id.trim().is_empty()
        || input.registry_id.len() > 128
        || input.expected_revision.checked_add(1) != Some(input.revision)
        || candidate
            .bridge
            .control()
            .physical_entity_registry()
            .map_or(0, |p| p.revision())
            != input.expected_physical_revision
    {
        return Err("deployment identity, revision or physical registry conflicts".into());
    }
    let profile = DeploymentFeasibility::from_document(input.document.clone())?;
    let published = candidate
        .published_deployments
        .get_or_insert_with(|| PublishedDeployments {
            schema: "roboguide.published-deployments/v0.1".into(),
            registry_id: input.registry_id.clone(),
            original_digest: startup.map(|p| p.digest().into()),
            history: BTreeMap::new(),
            physical_revisions: BTreeMap::new(),
            compiled: BTreeMap::new(),
        });
    if published.registry_id != input.registry_id
        || published.history.last_key_value().map_or(0, |(r, _)| *r) != input.expected_revision
        || published.history.len() >= 128
    {
        return Err("deployment publication expected revision conflicts".into());
    }
    published
        .physical_revisions
        .insert(input.revision, input.expected_physical_revision);
    let entry = publication_document(published, input.revision, &profile.source_document);
    let registry_digest = digest(&entry)?;
    published.history.insert(input.revision, input.document);
    published.compiled.insert(input.revision, Arc::new(profile));
    if serde_json::to_vec(published)
        .map_err(|e| e.to_string())?
        .len()
        > 8 * 1024 * 1024
    {
        return Err("retained deployment evidence exceeds 8 MiB".into());
    }
    commit(
        controller,
        log,
        gate,
        candidate,
        input.command_id,
        kind,
        input_digest,
        registry_digest,
        input.revision,
        subject,
        clock.now(),
    )
}
