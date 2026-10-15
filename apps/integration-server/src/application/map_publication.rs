//! Current typed-map publication checks over existing Control, Runtime and registration authorities.

use crate::ControllerState;
use domain::{ExecutionCommand, ExecutionValue, MapArtifactManifest};
use ports::SharedNodeStateReader;

/// Reads an exact semantic parameter without promoting any parameter into authentication.
fn parameter<'a>(command: &'a ExecutionCommand, key: &str) -> Option<&'a str> {
    match command.intent().parameters().get(key) {
        Some(ExecutionValue::String(value)) => Some(value.as_str()),
        _ => None,
    }
}

/// Checks canonical artifact semantics against immutable metadata, independent of Local How names.
fn intent_matches(command: &ExecutionCommand, map: &MapArtifactManifest, operation: &str) -> bool {
    parameter(command, "map_id") == Some(map.selector().map_id().as_str())
        && parameter(command, "revision_id") == Some(map.selector().revision_id().as_str())
        && parameter(command, "spatial_anchor_id") == Some(map.anchor_id().as_str())
        && parameter(command, "artifact_operation") == Some(operation)
        && parameter(command, "artifact_slot").is_some_and(|slot| !slot.is_empty())
}

/// Requires the original completed producer and a distinct exact live Control-bound publish attempt.
pub(crate) fn validate(
    controller: &ControllerState,
    manifest: &MapArtifactManifest,
    publication_id: &str,
) -> Result<(), String> {
    let history = controller.bridge.attempt_history();
    let source_id = manifest
        .source_execution_id()
        .ok_or("original build attempt is unavailable")?;
    let source = history
        .iter()
        .find(|attempt| attempt.execution_id() == source_id)
        .ok_or("original Runtime build attempt is unavailable")?;
    let publication = history
        .iter()
        .find(|attempt| attempt.execution_id() == publication_id)
        .ok_or("current Runtime publication attempt is unavailable")?;
    let command = publication.command();
    if source_id == publication_id
        || source.status() != runtime::ExecutionStatus::Completed
        || !matches!(
            publication.status(),
            runtime::ExecutionStatus::Dispatched
                | runtime::ExecutionStatus::Accepted
                | runtime::ExecutionStatus::Running
        )
        || source.command().node_id() != manifest.producer_node_id()
        || command.node_id() != manifest.producer_node_id()
        || Some(source.command().task_ref()) != manifest.source_task_ref()
        || source.command().mission_id() != manifest.source_mission_id()
        || command.mission_id() != manifest.source_mission_id()
        || source.command().group_id() != command.group_id()
        || !intent_matches(source.command(), manifest, "prepare-output")
        || !intent_matches(command, manifest, "publish")
        || parameter(source.command(), "artifact_slot") != parameter(command, "artifact_slot")
        || controller.bridge.current_task_attempt_id(
            command.group_id(),
            command.task_ref(),
            command.role_id(),
        ) != Some(publication_id)
        || controller
            .bridge
            .attempt_cancellation_pending(publication_id)
    {
        return Err(
            "map original producer or current publishing attempt differs from retained authority"
                .into(),
        );
    }
    let mission = controller
        .orchestrator
        .execution(command.mission_id())
        .ok_or("publication Mission is absent")?;
    let group = controller
        .bridge
        .control()
        .group(command.group_id())
        .ok_or("publication Group is absent")?;
    if mission.group_id() != command.group_id()
        || mission.isolation().is_some()
        || !matches!(
            mission.lifecycle(),
            orchestration::MissionExecutionLifecycle::Accepted
                | orchestration::MissionExecutionLifecycle::Running
        )
        || group.lifecycle() == control::GroupLifecycle::Released
        || !group
            .task_execution(command.task_ref())
            .is_some_and(|task| {
                task.assignments()
                    .iter()
                    .any(|a| a.role_id() == command.role_id() && a.node_id() == command.node_id())
            })
    {
        return Err("map publication is not currently executable under Control ownership".into());
    }
    let node = controller
        .bridge
        .state()
        .node(command.node_id())
        .ok_or("publication Node is absent")?;
    let registration = node.registration();
    let original_provider = registration
        .memory_providers()
        .iter()
        .find(|provider| Some(provider.provider_id()) == manifest.producer_provider_id())
        .ok_or("current original map provider is unavailable")?;
    for attempt in [source, publication] {
        let evidence = attempt.command().registration_evidence().ok_or(
            "original dispatch provider declarations are unavailable; no automatic upgrade",
        )?;
        if evidence.node_id() != attempt.command().node_id()
            || evidence.contract_version().as_str() != domain::NODE_CONTRACT_VERSION_V0_8
            || evidence.source_session_id().is_none_or(str::is_empty)
            || evidence
                .memory_providers()
                .iter()
                .find(|provider| Some(provider.provider_id()) == manifest.producer_provider_id())
                != Some(original_provider)
        {
            return Err("map provider differs from original dispatch declarations".into());
        }
    }
    if registration.contract_version().as_str() != domain::NODE_CONTRACT_VERSION_V0_8
        || registration.operation_owner(source.command().intent().operation())
            != manifest.producer_local_system_id()
        || registration.operation_owner(command.intent().operation())
            != manifest.producer_local_system_id()
    {
        return Err(
            "map provider and current operations do not share the original LocalSystem".into(),
        );
    }
    Ok(())
}
