//! One bounded maintenance assessment under the existing Controller writer.

use super::ApplicationWriteGate;
use crate::*;
use sha2::{Digest as _, Sha256};

/// Reads only one bounded prefix; missing reference coverage remains an explicit blocker.
pub(crate) fn assess(
    log: &state::SqliteEventLog,
    now: domain::TimestampMs,
) -> Result<state::RetentionStatus, String> {
    let checkpoint = log.load_checkpoint().map_err(|error| error.to_string())?;
    log.retention_status(
        now.as_millis(),
        checkpoint.map_or(0, |checkpoint| checkpoint.event_sequence),
        &state::RetentionProtection::default(),
    )
    .map_err(|error| error.to_string())
}

/// Commits policy authority and its original external command receipt in the same SQL batch.
pub(crate) fn update(
    controller: &Arc<Mutex<ControllerState>>,
    log: &state::SqliteEventLog,
    gate: &impl ApplicationWriteGate,
    command: &state::RetentionPolicyCommand,
    now: domain::TimestampMs,
) -> Result<state::RetentionPolicyReceipt, String> {
    let _guard = gate.lock()?;
    log.begin_batch().map_err(|error| error.to_string())?;
    let result = (|| {
        let (receipt, changed) = log
            .update_retention_policy(command, now.as_millis())
            .map_err(|error| error.to_string())?;
        if !changed {
            log.rollback_batch().map_err(|error| error.to_string())?;
            return Ok(receipt);
        }
        let live = controller
            .lock()
            .map_err(|_| "Controller authority mutex is poisoned".to_string())?;
        let checkpoint = super::server_checkpoint_json(&live).map_err(|error| error.to_string())?;
        drop(live);
        let checkpoint_digest = format!("sha256:{:x}", Sha256::digest(checkpoint.as_bytes()));
        let input = serde_json::to_vec(command).map_err(|error| error.to_string())?;
        log.save_checkpoint_with_class(
            SERVER_CHECKPOINT_SCHEMA,
            &checkpoint,
            state::ControlCommandJournalEntry {
                command_id: format!("retention-policy:{}", command.command_id),
                causation_id: Some(command.command_id.clone()),
                authority_revision: 0,
                controller_epoch: None,
                plan_digest: None,
                input_digest: format!("sha256:{:x}", Sha256::digest(input)),
                ownership_digest: None,
                resulting_state_digest: checkpoint_digest,
                checkpoint_event_sequence: log
                    .current_batch_event_sequence()
                    .map_err(|error| error.to_string())?,
                checkpoint_schema: SERVER_CHECKPOINT_SCHEMA.into(),
            },
            state::CommandJournalClass::External,
        )
        .map_err(|error| error.to_string())?;
        gate.validate_locked()?;
        log.commit_batch().map_err(|error| error.to_string())?;
        Ok(receipt)
    })();
    if result.is_err() {
        log.rollback_batch()
            .map_err(|error| format!("policy rejection could not roll back: {error}"))?;
    }
    result
}
