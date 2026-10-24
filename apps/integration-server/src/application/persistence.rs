//! Controller writer fencing and checkpoint serialization.

use crate::*;

/// Selects an explicit storage generation and moves the actual inode/path leases into the SQL lifetime.
pub(crate) fn open_controller_event_log(
    event_path: &Path,
    policy: state::CheckpointStoragePolicy,
) -> Result<state::SqliteEventLog, Box<dyn std::error::Error>> {
    let format = match std::env::var("ROBOGUIDE_CONTROLLER_STORAGE_SCHEMA").as_deref() {
        Ok("legacy") | Err(std::env::VarError::NotPresent) => {
            state::ControllerStorageFormat::Legacy
        }
        Ok("1") => state::ControllerStorageFormat::V1,
        _ => return Err("ROBOGUIDE_CONTROLLER_STORAGE_SCHEMA must be legacy or 1".into()),
    };
    Ok(state::SqliteEventLog::open_controller_with_checkpoint_policy(event_path, policy, format)?)
}

/// Serializes Integration and Mission orchestration into one versioned durable wrapper.
pub(crate) fn server_checkpoint_json(
    controller: &ControllerState,
) -> Result<String, Box<dyn std::error::Error + Send + Sync>> {
    let integration_json = controller
        .bridge
        .checkpoint_json()
        .map_err(|error| format!("integration checkpoint failure: {error}"))?;
    let integration_value: serde_json::Value = serde_json::from_str(&integration_json)
        .map_err(|error| format!("integration checkpoint JSON failure: {error}"))?;
    if integration_value
        .get("schema")
        .and_then(serde_json::Value::as_str)
        != Some(INTEGRATION_CHECKPOINT_SCHEMA)
    {
        return Err("Integration checkpoint schema changed unexpectedly".into());
    }
    let orchestration_json = controller
        .orchestrator
        .checkpoint_json()
        .map_err(|error| format!("orchestration checkpoint failure: {error}"))?;
    serde_json::to_string(&ServerCheckpoint {
        federation: controller.federation.clone(),
        replication_profile: controller
            .bridge
            .event_sink()
            .replication_profile()
            .map(str::to_string),
        mission_admissions: controller.mission_admissions.clone(),
        memory_imports: controller.memory_imports.clone(),
        memory_preparation: controller.memory_preparation.clone(),
        memory_use: controller.memory_use.clone(),
        memory_handoffs: controller.memory_handoffs.clone(),
        runtime_revisions: controller.runtime_revisions.clone(),
        safety_stops: controller.safety_stops.clone(),
        schema: SERVER_CHECKPOINT_SCHEMA.to_string(),
        integration_json,
        orchestration_json,
        verifier_seen: controller.verifier_seen.clone(),
        verifier_source_digest: controller.verifier_source_digest.clone(),
    })
    .map_err(|error| format!("controller checkpoint wrapper failure: {error}").into())
}

/// Rejects cluster history under standalone/different profiles and legacy wrappers hiding quorum authority.
pub(crate) fn validate_replication_checkpoint(
    saved: &ServerCheckpoint,
    profile: Option<&str>,
) -> Result<(), String> {
    if saved.replication_profile.as_deref() != profile
        || (saved.schema != SERVER_CHECKPOINT_SCHEMA && saved.replication_profile.is_some())
    {
        return Err(
            "controller checkpoint replication profile differs from startup authority".into(),
        );
    }
    Ok(())
}
