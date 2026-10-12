//! Durable application transitions split by orchestration responsibility.

mod actor_placement;
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
mod outcomes;
mod persistence;
mod physical_entity_registry;
mod recovery;
mod task_verifier;
mod timer;
mod verifier_source;
pub(crate) use isolation::resume_mission_candidate;
pub(crate) use isolation::{
    flush_unisolated_commands, flush_unisolated_dispatch, run_mission_transition,
    validate_isolation_checkpoint_schema,
};

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
pub(crate) use persistence::{acquire_event_log_writer_lock, server_checkpoint_json};
pub(crate) use physical_entity_registry::load_physical_entity_registry_file;
#[cfg(test)]
pub(crate) use recovery::{apply_recovery_required, resume_role_recovery};
pub(crate) use recovery::{
    apply_runtime_events, begin_current_ambiguity_recoveries, resume_pending_recoveries,
};
pub(crate) use task_verifier::{
    TaskVerifierFeed, apply_task_verifier, validate_restored_verifier_source,
    validate_task_verifier,
};
#[cfg(test)]
pub(crate) use timer::drive_application_timer;
pub(crate) use timer::drive_application_timer_with_clock;
