//! Durable application transitions split by orchestration responsibility.

use sha2::{Digest as _, Sha256};

mod actor_placement;
pub(crate) mod archive_content;
pub(crate) mod attestation;
pub(crate) mod catalog_retention;
mod deployment_document;
mod deployment_feasibility;
mod deployment_operation_admission;
mod deployment_sources;
mod dispatch;
mod fact_batch;
#[cfg(test)]
pub(crate) use fact_batch::fact_is_barrier;
pub(crate) use fact_batch::{NodeFactDriver, collect_fact_batch};
mod initial_operation_assessment;
mod initial_operation_preferences;
pub(crate) use initial_operation_assessment::unavailable as unavailable_initial_assessment;
mod isolation;
#[cfg(test)]
pub(crate) use isolation::revalidate_mission_resume;
pub(crate) mod map_publication;
pub(crate) mod memory_handoff;
pub(crate) mod memory_import;
pub(crate) mod memory_preparation;
pub(crate) mod memory_query;
pub(crate) mod memory_use;
pub(crate) mod memory_use_query;
pub(crate) mod node_identity;
mod outcomes;
mod persistence;
mod physical_entity_registry;
pub(crate) mod plan_revision;
pub(crate) mod plan_validation;
mod recovery;
pub(crate) mod safety_stop;
pub(crate) mod semantic_verifier;
pub(crate) mod state_policy;
mod task_verifier;
pub(crate) mod tenant;
mod timer;
mod verifier_source;
mod write_gate;
#[cfg(test)]
pub(crate) use isolation::resume_mission_candidate;
pub(crate) use isolation::resume_mission_candidate_with_policy;
pub(crate) use isolation::{
    flush_unisolated_commands, flush_unisolated_dispatch, run_mission_transition,
    validate_isolation_checkpoint_schema,
};
pub(crate) use write_gate::{ApplicationWriteGate, run_synchronous_transaction};

pub(crate) use actor_placement::{
    load_actor_placement_file, validate_actor_placement_coverage,
    validate_restored_actor_placement_coverage,
};
pub(crate) use deployment_feasibility::DeploymentFeasibility;
#[cfg(test)]
pub(crate) use dispatch::{deferred_dispatch, node_accepts_execution_session};
pub(crate) use dispatch::{drive_ready_tasks, drive_rebound_attempts};
pub(crate) use outcomes::{
    apply_pending_cancellations, apply_runtime_outcomes, close_terminal_mission_coordination,
};
pub(crate) use persistence::{
    acquire_event_log_writer_lock, server_checkpoint_json, validate_replication_checkpoint,
};
pub(crate) use physical_entity_registry::load_physical_entity_registry_file;
#[cfg(test)]
pub(crate) use recovery::resume_role_recovery;
pub(crate) use recovery::{
    apply_runtime_events, begin_current_ambiguity_recoveries, recovery_view,
    resume_pending_recoveries,
};
pub(crate) use task_verifier::{
    TaskVerifierFeed, apply_task_verifier, validate_restored_verifier_source,
    validate_task_verifier,
};
#[cfg(test)]
pub(crate) use timer::drive_application_timer;
pub(crate) use timer::drive_application_timer_with_clock;

/// Builds a checkpoint-bound Control journal row from the actual application mutation input.
pub(crate) fn control_command_journal_entry(
    command_id: impl Into<String>,
    causation_id: Option<String>,
    input: impl AsRef<[u8]>,
    checkpoint_json: &str,
    event_sequence: u64,
) -> state::ControlCommandJournalEntry {
    control_command_journal_entry_with_digest(
        command_id,
        causation_id,
        format!("sha256:{:x}", Sha256::digest(input.as_ref())),
        checkpoint_json,
        event_sequence,
    )
}

/// Builds the same journal row when a caller already computed the canonical input digest.
pub(crate) fn control_command_journal_entry_with_digest(
    command_id: impl Into<String>,
    causation_id: Option<String>,
    input_digest: impl Into<String>,
    checkpoint_json: &str,
    event_sequence: u64,
) -> state::ControlCommandJournalEntry {
    let input_digest = input_digest.into();
    let resulting_state_digest = format!("sha256:{:x}", Sha256::digest(checkpoint_json.as_bytes()));
    let wrapper: serde_json::Value =
        serde_json::from_str(checkpoint_json).expect("server checkpoint is generated JSON");
    let integration: serde_json::Value = serde_json::from_str(
        wrapper["integration_json"]
            .as_str()
            .expect("server integration snapshot"),
    )
    .expect("generated integration JSON");
    let orchestration: serde_json::Value = serde_json::from_str(
        wrapper["orchestration_json"]
            .as_str()
            .expect("server plan snapshot"),
    )
    .expect("generated orchestration JSON");
    let plans = orchestration
        .as_array()
        .expect("ordered accepted Missions")
        .iter()
        .map(|mission| mission["plan"].clone())
        .collect::<Vec<_>>();
    let plan_digest = format!(
        "sha256:{:x}",
        Sha256::digest(serde_json::to_vec(&plans).expect("JSON plans"))
    );
    let ownership_digest = format!(
        "sha256:{:x}",
        Sha256::digest(serde_json::to_vec(&integration["control"]).expect("JSON Control snapshot"))
    );
    state::ControlCommandJournalEntry {
        command_id: command_id.into(),
        causation_id,
        authority_revision: event_sequence,
        controller_epoch: None,
        plan_digest: Some(plan_digest),
        input_digest,
        ownership_digest: Some(ownership_digest),
        resulting_state_digest,
        checkpoint_event_sequence: event_sequence,
        checkpoint_schema: crate::SERVER_CHECKPOINT_SCHEMA.to_string(),
    }
}

/// Journals every Controller HTTP/startup/localization mutation through the same authority transaction.
pub(crate) fn save_controller_checkpoint(
    log: &state::SqliteEventLog,
    checkpoint: &str,
) -> Result<(), state::SqliteEventLogError> {
    let sequence = log.current_batch_event_sequence()?;
    let digest = format!("sha256:{:x}", Sha256::digest(checkpoint.as_bytes()));
    let command = control_command_journal_entry_with_digest(
        format!("controller:{sequence}:{digest}"),
        None,
        digest,
        checkpoint,
        sequence,
    );
    log.save_checkpoint_with_command(crate::SERVER_CHECKPOINT_SCHEMA, checkpoint, command)
}
