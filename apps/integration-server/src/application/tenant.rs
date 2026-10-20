//! Explicit source-bound historical Mission ownership migration without execution effects.

use crate::*;
use sha2::{Digest as _, Sha256};

/// Returns the immutable normalized accepted-plan digest for trusted legacy migration.
pub(crate) fn source_digest(
    controller: &ControllerState,
    mission: &domain::MissionId,
) -> Result<String, String> {
    let execution = controller
        .orchestrator
        .execution(mission)
        .ok_or("unknown Mission")?;
    let bytes = serde_json::to_vec(execution.plan()).map_err(|error| error.to_string())?;
    Ok(format!("sha256:{:x}", Sha256::digest(bytes)))
}

/// Applies an authorized migration in the ordinary application checkpoint transaction.
pub(crate) fn migrate(
    controller: &Arc<Mutex<ControllerState>>,
    log: &state::SqliteEventLog,
    gate: &dyn crate::application::ApplicationWriteGate,
    clock: &impl Clock,
    body: &[u8],
    subject: &str,
) -> Result<serde_json::Value, String> {
    let command: control::MissionTenantMigration =
        serde_json::from_slice(body).map_err(|error| error.to_string())?;
    let _gate = gate.lock()?;
    let live = controller
        .lock()
        .map_err(|_| "controller lock is poisoned")?;
    let digest = source_digest(&live, &command.mission_id)?;
    let mut candidate = live.clone();
    drop(live);
    let receipt = candidate
        .bridge
        .control_mut()
        .migrate_mission_tenant(command, &digest, subject, clock.now())
        .map_err(|error| error.to_string())?;
    candidate
        .bridge
        .control()
        .validate_mission_tenant_authority()
        .map_err(|error| error.to_string())?;
    let checkpoint = server_checkpoint_json(&candidate).map_err(|error| error.to_string())?;
    log.begin_batch().map_err(|error| error.to_string())?;
    if let Err(error) =
        save_controller_checkpoint(log, &checkpoint).and_then(|()| log.commit_batch())
    {
        let rollback = log.rollback_batch();
        return Err(format!(
            "tenant migration commit failed: {error}; rollback={rollback:?}"
        ));
    }
    let mut live = controller
        .lock()
        .map_err(|_| "controller lock is poisoned")?;
    *live = candidate;
    Ok(
        serde_json::json!({"schema_version":"roboguide.mission-tenant-migration/v0.1","receipt":receipt,"execution_repeated":false}),
    )
}
