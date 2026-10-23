//! Inactive local Controller projection; no Node, process-drain or quorum authority is created.

use controller_replication::ReplicaView;
use std::path::Path;

/// Installs only the inactive original SQL under its actual inode/sibling leases and transaction.
pub(super) fn materialize(
    directory: &Path,
    view: &ReplicaView,
    format: state::ControllerStorageFormat,
    application_schema: &str,
    profile: &str,
) -> Result<(), String> {
    view.validate_for_materialization(application_schema, profile)?;
    if view.checkpoint.as_ref().is_some_and(|checkpoint| {
        state::checkpoint_requires_complete_recovery(&checkpoint.schema) && view.content.is_none()
    }) {
        return Err(
            "modern replica projection requires its original complete recovery authority".into(),
        );
    }
    if let Some(content) = &view.content {
        let original = state::CompleteReplicaState {
            schema: "roboguide.complete-replica-state/v0.1".into(),
            checkpoint: view
                .checkpoint
                .clone()
                .ok_or("complete replica checkpoint is absent")?,
            events: view.events.clone(),
            commands: view.commands.clone(),
            recovery: content.recovery.clone(),
        };
        return install_original(directory, &original, format);
    }
    let log = state::SqliteEventLog::open_controller_with_checkpoint_policy(
        directory.join("controller.sqlite3"),
        state::CheckpointStoragePolicy::Full,
        format,
    )
    .map_err(|error| error.to_string())?;
    if let Some(checkpoint) = &view.checkpoint {
        log.install_replica_authority(
            &view.events,
            checkpoint,
            view.history_anchor
                .as_ref()
                .map(|anchor| anchor.base_sequence),
            &view.commands,
        )
        .map_err(|error| error.to_string())?;
    } else if !log.is_empty().map_err(|error| error.to_string())?
        || log
            .load_checkpoint()
            .map_err(|error| error.to_string())?
            .is_some()
        || log
            .control_command_count()
            .map_err(|error| error.to_string())?
            != 0
    {
        return Err("empty replica view cannot replace original Controller authority".into());
    }
    // The managed SQL connection closes before its actual leases; no pathname replacement is used.
    drop(log);
    Ok(())
}

/// Installs complete original data under the same actual leases and SQL transaction used by the supervisor.
/// An offline capture can test this path but never establishes live consensus or execution authority.
pub(super) fn install_original(
    directory: &Path,
    original: &state::CompleteReplicaState,
    format: state::ControllerStorageFormat,
) -> Result<(), String> {
    original.validate().map_err(|error| error.to_string())?;
    let log = state::SqliteEventLog::open_controller_with_checkpoint_policy(
        directory.join("controller.sqlite3"),
        state::CheckpointStoragePolicy::Full,
        format,
    )
    .map_err(|error| error.to_string())?;
    log.install_complete_replica_state(original)
        .map_err(|error| error.to_string())
}
