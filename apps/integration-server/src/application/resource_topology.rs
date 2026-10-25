//! Original time-booking expiry delegates to retained whole-Group stop and Memory revocation.

use crate::*;

/// Supplies an explicit deployment booking budget anchored to original Mission acceptance.
pub(crate) fn booking(
    controller: &ControllerState,
    task: &domain::TaskRef,
) -> Result<Option<domain::TaskDurationEstimate>, String> {
    let Some(registry) = controller.bridge.control().resource_topology_registry() else {
        return Ok(None);
    };
    let execution = controller
        .orchestrator
        .execution(task.mission_id())
        .ok_or("booking Mission is absent")?;
    let planned = execution
        .plan()
        .task_graph()
        .tasks()
        .iter()
        .find(|planned| planned.requirement().task_ref() == task)
        .ok_or("booking Task is absent")?;
    if !planned.requirement().roles().iter().any(|role| {
        role.resource_requirements().iter().any(|demand| {
            matches!(
                demand.kind(),
                domain::ResourceKind::Space | domain::ResourceKind::Time
            )
        })
    }) {
        return Ok(None);
    }
    domain::TaskDurationEstimate::new(
        task.clone(),
        registry.booking_duration_ms,
        domain::StateSource::RoboGuide {
            component: format!(
                "resource-topology-booking/{}/{}",
                registry.registry_id, registry.revision
            ),
        },
        execution.accepted_at(),
        registry.booking_max_age_ms,
    )
    .map(Some)
    .map_err(|error| error.to_string())
}

/// Cross-binds retained deployment budget clocks to each original Mission on startup.
pub(crate) fn validate_restored(controller: &ControllerState) -> Result<(), String> {
    for record in controller.bridge.control().scheduled_tasks() {
        let Some(estimate) = record.decision().duration_estimate() else {
            continue;
        };
        if let domain::StateSource::RoboGuide { component } = estimate.source()
            && component.starts_with("resource-topology-booking/")
        {
            let execution = controller
                .orchestrator
                .execution(record.task_ref().mission_id())
                .ok_or("booking lost its original Mission")?;
            if estimate.received_at() != execution.accepted_at() {
                return Err("booking changed original Mission acceptance clock".into());
            }
        }
    }
    Ok(())
}

/// Stops original targets and all live peers without releasing resources or renewing stop evidence.
pub(crate) fn monitor(
    controller: &mut ControllerState,
    now: domain::TimestampMs,
) -> Result<(), String> {
    let Some(registry) = controller.bridge.control().resource_topology_registry() else {
        return Ok(());
    };
    let timeout = registry.stop_timeout_ms;
    let targets = controller.bridge.control().expired_topology_targets(now);
    for (group, task, role) in targets {
        super::memory_use::revoke_group(
            controller,
            &group,
            "original resource time booking expired",
            now,
        )?;
        super::safety_stop::admit(controller, &group, &task, &role, now, timeout)?;
    }
    Ok(())
}
