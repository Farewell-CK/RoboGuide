//! Atomic certificate lifecycle administration and committed transport publication.

use crate::*;

/// Applies a deduplicated administrator command and commits before changing any transport policy.
pub(crate) fn submit(
    controller: &Arc<Mutex<ControllerState>>,
    event_log: &state::SqliteEventLog,
    event_write_gate: &dyn crate::application::ApplicationWriteGate,
    clock: &impl Clock,
    body: &[u8],
    authority_subject: &str,
) -> Result<serde_json::Value, String> {
    let command: domain::IdentityCommand =
        serde_json::from_slice(body).map_err(|error| error.to_string())?;
    let _gate = event_write_gate.lock()?;
    let live = controller
        .lock()
        .map_err(|_| "controller lock is poisoned")?;
    let mut candidate = live.clone();
    drop(live);
    let receipt = candidate
        .bridge
        .control_mut()
        .apply_node_identity_command(&command, clock.now(), authority_subject)
        .map_err(|error| error.to_string())?;
    let checkpoint = server_checkpoint_json(&candidate).map_err(|error| error.to_string())?;
    event_log.begin_batch().map_err(|error| error.to_string())?;
    if let Err(error) =
        save_controller_checkpoint(event_log, &checkpoint).and_then(|()| event_log.commit_batch())
    {
        let rollback = event_log.rollback_batch();
        return Err(format!(
            "Node identity commit failed: {error}; rollback={rollback:?}"
        ));
    }
    let mut live = controller
        .lock()
        .map_err(|_| "controller lock is poisoned")?;
    *live = candidate;
    live.bridge.publish_node_identity_registry()?;
    Ok(
        serde_json::json!({"schema_version": "roboguide.node-identity-command-receipt/v0.1", "receipt": receipt, "physical_identity": "HardwareEvidencePending"}),
    )
}
