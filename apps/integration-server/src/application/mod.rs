//! Durable application transitions split by orchestration responsibility.

mod actor_placement;
mod dispatch;
mod outcomes;
mod persistence;
mod physical_entity_registry;
mod recovery;
mod timer;

pub(crate) use actor_placement::{
    load_actor_placement_file, validate_actor_placement_coverage,
    validate_restored_actor_placement_coverage,
};
#[cfg(test)]
pub(crate) use dispatch::{deferred_dispatch, node_accepts_execution_session};
pub(crate) use dispatch::{drive_ready_tasks, drive_rebound_attempts};
pub(crate) use outcomes::{
    apply_pending_cancellations, apply_runtime_outcomes, close_terminal_mission_coordination,
};
pub(crate) use persistence::{acquire_event_log_writer_lock, server_checkpoint_json};
pub(crate) use physical_entity_registry::load_physical_entity_registry_file;
#[cfg(test)]
pub(crate) use recovery::{apply_recovery_required, resume_role_recovery};
pub(crate) use recovery::{
    apply_runtime_events, begin_current_ambiguity_recoveries, resume_pending_recoveries,
};
pub(crate) use timer::drive_application_timer;
