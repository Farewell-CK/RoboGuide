//! Original application-use proof required before a Catalog owner may retire its content claim.

use crate::*;
use sha2::Digest as _;

/// Freezes real remaining use, context/provider revoke and import history under the original writer.
/// Local Task terminal flags or elapsed deadlines alone never discharge an uncertain reader.
pub(crate) fn retirement_proof(
    controller: &ControllerState,
    record: &domain::ArtifactCatalogReference,
) -> Result<String, String> {
    let mut missions = Vec::new();
    for id in controller.orchestrator.mission_ids() {
        let mission = controller
            .orchestrator
            .execution(&id)
            .ok_or("Mission history disappeared")?;
        let consumes = mission.plan().task_graph().tasks().iter().any(|task| {
            task.requirement().roles().iter().any(|role| {
                role.memories()
                    .iter()
                    .any(|input| input.selector == record.selector)
            })
        });
        if consumes {
            if !matches!(
                mission.lifecycle(),
                orchestration::MissionExecutionLifecycle::Completed
                    | orchestration::MissionExecutionLifecycle::Failed
                    | orchestration::MissionExecutionLifecycle::Cancelled
            ) {
                return Err("original Catalog is still required by a nonterminal Mission".into());
            }
            missions.push((id, mission));
        }
    }
    let mut preparations = Vec::new();
    let mut uses = Vec::new();
    for preparation in controller.memory_preparation.records.values() {
        if preparation.preparation.as_ref().is_some_and(|context| {
            context
                .inputs
                .iter()
                .any(|input| input.manifest.selector() == &record.selector)
        }) {
            if super::memory_use::blocks_attempt_release(controller, &preparation.execution_id) {
                return Err("original Catalog context/provider revoke proof is incomplete".into());
            }
            preparations.push(preparation);
            uses.push(controller.memory_use.records.get(&preparation.execution_id));
        }
    }
    let mut imports = Vec::new();
    for import in controller
        .memory_imports
        .values()
        .filter(|import| import.request.manifest.selector() == &record.selector)
    {
        if import.phase != super::memory_import::ImportPhase::Rejected {
            let original = controller
                .memory_preparation
                .records
                .get(&import.request.execution_id)
                .and_then(|preparation| preparation.preparation.as_ref())
                .ok_or("Catalog import has no original revocable preparation")?;
            if import.request.preparation_id.as_ref() != Some(&original.preparation_id)
                || !original.inputs.iter().any(|input| {
                    input.import_id == import.request.import_id
                        && input.manifest == import.request.manifest
                })
                || super::memory_use::blocks_attempt_release(
                    controller,
                    &import.request.execution_id,
                )
            {
                return Err("Catalog import retains an unresolved original reader".into());
            }
        }
        imports.push(import);
    }
    if super::memory_handoff::holds_catalog(controller, &record.selector) {
        return Err("original pending handoff retains its Catalog source/target".into());
    }
    let canonical = serde_json::to_value(serde_json::json!({
        "selector": record.selector,
        "generation": record.generation,
        "authority": record.authority_digest,
        "missions": missions,
        "preparations": preparations,
        "uses": uses,
        "imports": imports,
    }))
    .map_err(|error| error.to_string())?;
    Ok(format!(
        "sha256:{:x}",
        sha2::Sha256::digest(serde_json::to_vec(&canonical).map_err(|error| error.to_string())?)
    ))
}
