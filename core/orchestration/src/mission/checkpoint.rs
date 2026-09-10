//! Mission checkpoint, submission, and Control-authority cross-checking.

use super::super::*;
use super::serialization::mission_plan_json;
use super::timing::{
    checked_plan_timestamp, plan_latest_activation_at, validate_plan_timing_anchor,
};

impl MissionOrchestrator {
    /// Creates an empty Mission execution authority.
    pub const fn new() -> Self {
        Self {
            executions: BTreeMap::new(),
        }
    }

    /// Serializes accepted Mission plans and orchestration lifecycle for process recovery.
    pub fn checkpoint_json(&self) -> Result<String, OrchestrationError> {
        let executions = self
            .executions
            .values()
            .map(|execution| {
                serde_json::json!({
                    "plan": mission_plan_json(execution.plan()),
                    "group_id": execution.group_id().as_str(),
                    "lifecycle": execution.lifecycle(),
                    "accepted_at_ms": execution.accepted_at().as_millis(),
                    "scheduling_deferrals": execution.scheduling_deferrals.iter().map(
                        |(task_id, reason)| serde_json::json!({
                            "task_id": task_id.as_str(),
                            "reason": reason,
                        })
                    ).collect::<Vec<_>>(),
                })
            })
            .collect::<Vec<_>>();
        serde_json::to_string(&executions).map_err(|error| {
            OrchestrationError::Mission(format!(
                "cannot serialize orchestration checkpoint: {error}"
            ))
        })
    }

    /// Restores accepted Mission plans and rejects malformed orchestration evidence.
    pub fn restore_json(json: &str) -> Result<Self, OrchestrationError> {
        let executions: Vec<serde_json::Value> = serde_json::from_str(json).map_err(|error| {
            OrchestrationError::Mission(format!("cannot restore orchestration checkpoint: {error}"))
        })?;
        let mut restored = Self::new();
        for value in executions {
            let plan_value = value.get("plan").ok_or_else(|| {
                OrchestrationError::Mission("checkpoint misses MissionPlan".to_string())
            })?;
            let plan =
                decode_mission_plan(&serde_json::to_string(plan_value).map_err(|error| {
                    OrchestrationError::Mission(format!("invalid MissionPlan checkpoint: {error}"))
                })?)?;
            SupportedMechanismProfile::current().validate(&plan)?;
            let group_id = ExecutionGroupId::new(
                value
                    .get("group_id")
                    .and_then(serde_json::Value::as_str)
                    .ok_or_else(|| {
                        OrchestrationError::Mission("checkpoint misses Group identity".to_string())
                    })?,
            )
            .map_err(|error| OrchestrationError::Mission(error.to_string()))?;
            let lifecycle: MissionExecutionLifecycle =
                serde_json::from_value(value.get("lifecycle").cloned().ok_or_else(|| {
                    OrchestrationError::Mission("checkpoint misses Mission lifecycle".to_string())
                })?)
                .map_err(|error| {
                    OrchestrationError::Mission(format!("invalid Mission lifecycle: {error}"))
                })?;
            let accepted_at = TimestampMs::new(
                value
                    .get("accepted_at_ms")
                    .and_then(serde_json::Value::as_u64)
                    .unwrap_or(0),
            );
            validate_plan_timing_anchor(&plan, accepted_at)?;
            let mut scheduling_deferrals = BTreeMap::new();
            let deferral_values = value
                .get("scheduling_deferrals")
                .map(|value| {
                    value.as_array().ok_or_else(|| {
                        OrchestrationError::Mission(
                            "checkpoint scheduling deferrals must be an array".to_string(),
                        )
                    })
                })
                .transpose()?
                .cloned()
                .unwrap_or_default();
            for deferral in deferral_values {
                let task_id = TaskId::new(
                    deferral
                        .get("task_id")
                        .and_then(serde_json::Value::as_str)
                        .ok_or_else(|| {
                            OrchestrationError::Mission(
                                "checkpoint scheduling deferral misses Task identity".to_string(),
                            )
                        })?,
                )
                .map_err(|error| OrchestrationError::Mission(error.to_string()))?;
                let reason = deferral
                    .get("reason")
                    .and_then(serde_json::Value::as_str)
                    .filter(|reason| !reason.is_empty())
                    .ok_or_else(|| {
                        OrchestrationError::Mission(
                            "checkpoint scheduling deferral misses reason".to_string(),
                        )
                    })?
                    .to_string();
                if !plan
                    .task_graph()
                    .tasks()
                    .iter()
                    .any(|task| task.requirement().task_ref().task_id() == &task_id)
                    || scheduling_deferrals.insert(task_id, reason).is_some()
                {
                    return Err(OrchestrationError::Mission(
                        "checkpoint scheduling deferral references an unknown or duplicate Task"
                            .to_string(),
                    ));
                }
            }
            let execution = MissionExecution {
                plan,
                group_id,
                lifecycle,
                accepted_at,
                scheduling_deferrals,
            };
            let mission_id = execution.plan.goal().mission_id().clone();
            if restored.executions.insert(mission_id, execution).is_some() {
                return Err(OrchestrationError::Mission(
                    "orchestration checkpoint contains duplicate Mission".to_string(),
                ));
            }
        }
        Ok(restored)
    }

    /// Accepts a complete plan, creates its one default Group, and exposes initial Ready Tasks.
    pub fn submit<E: EventSink>(
        &mut self,
        plan: MissionPlan,
        group_id: ExecutionGroupId,
        control: &mut ControlPlane,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<&MissionExecution, OrchestrationError> {
        SupportedMechanismProfile::current().validate(&plan)?;
        let mission_id = plan.goal().mission_id().clone();
        if self.executions.contains_key(&mission_id) {
            let existing = self
                .executions
                .get(&mission_id)
                .expect("contains_key proves existing Mission authority");
            if existing.plan() == &plan && existing.group_id() == &group_id {
                return Ok(existing);
            }
            return Err(OrchestrationError::Mission(format!(
                "Mission {mission_id} already exists with a different plan or Group"
            )));
        }
        validate_plan_timing_anchor(&plan, timestamp)?;
        control.create_mission_group(group_id.clone(), &plan, timestamp, correlation_id, events)?;
        self.executions.insert(
            mission_id.clone(),
            MissionExecution {
                plan,
                group_id,
                lifecycle: MissionExecutionLifecycle::Accepted,
                accepted_at: timestamp,
                scheduling_deferrals: BTreeMap::new(),
            },
        );
        self.refresh_ready(&mission_id, control, timestamp, correlation_id, events)?;
        self.executions
            .get(&mission_id)
            .ok_or_else(|| OrchestrationError::Mission("accepted Mission disappeared".to_string()))
    }

    /// Returns one accepted Mission execution.
    pub fn execution(&self, mission_id: &MissionId) -> Option<&MissionExecution> {
        self.executions.get(mission_id)
    }

    /// Returns all accepted Mission identities in deterministic order.
    pub fn mission_ids(&self) -> Vec<MissionId> {
        self.executions.keys().cloned().collect()
    }

    /// Validates restored Mission authority against Control before execution traffic is accepted.
    pub fn validate_control_authority(
        &self,
        control: &ControlPlane,
    ) -> Result<(), OrchestrationError> {
        let orchestration_groups = self
            .executions
            .values()
            .map(MissionExecution::group_id)
            .collect::<BTreeSet<_>>();
        for (mission_id, execution) in &self.executions {
            control.validate_mission_group_plan(execution.group_id(), execution.plan())?;
            let group = control
                .group(execution.group_id())
                .expect("Control plan validation requires the Group");
            let lifecycle_is_aligned = match execution.lifecycle() {
                MissionExecutionLifecycle::Accepted | MissionExecutionLifecycle::Running => {
                    matches!(
                        group.lifecycle(),
                        GroupLifecycle::Bound
                            | GroupLifecycle::Active
                            | GroupLifecycle::Adapted
                            | GroupLifecycle::Blocked
                    )
                }
                MissionExecutionLifecycle::Cancelling => {
                    matches!(
                        group.lifecycle(),
                        GroupLifecycle::Bound
                            | GroupLifecycle::Active
                            | GroupLifecycle::Adapted
                            | GroupLifecycle::Blocked
                    )
                }
                MissionExecutionLifecycle::Completed => {
                    group.lifecycle() == GroupLifecycle::Released
                        && group
                            .task_executions()
                            .all(|task| task.lifecycle() == TaskExecutionLifecycle::Completed)
                }
                MissionExecutionLifecycle::Failed | MissionExecutionLifecycle::Cancelled => {
                    group.lifecycle() == GroupLifecycle::Released
                }
            };
            if !lifecycle_is_aligned {
                return Err(OrchestrationError::Mission(format!(
                    "restored Mission {mission_id} lifecycle disagrees with its Execution Group"
                )));
            }
            for reservation in control
                .scheduled_tasks()
                .filter(|reservation| reservation.group_id() == execution.group_id())
            {
                let requirement = execution
                    .plan()
                    .task_graph()
                    .tasks()
                    .iter()
                    .find(|task| task.requirement().task_ref() == reservation.task_ref())
                    .map(|task| task.requirement())
                    .ok_or_else(|| {
                        OrchestrationError::Mission(
                            "restored scheduling reservation is absent from MissionPlan"
                                .to_string(),
                        )
                    })?;
                let decision = reservation.decision();
                let earliest_at = checked_plan_timestamp(
                    execution.accepted_at(),
                    requirement.timing().earliest_start_offset_ms(),
                    decision.task_ref(),
                )?;
                let expected_end = requirement
                    .timing()
                    .estimated_duration_ms()
                    .map(|duration| {
                        checked_plan_timestamp(decision.starts_at(), duration, decision.task_ref())
                    })
                    .transpose()?;
                let expected_latest = plan_latest_activation_at(
                    requirement.timing(),
                    execution.accepted_at(),
                    decision.task_ref(),
                )?;
                if decision.starts_at() < earliest_at
                    || decision.ends_at() != expected_end
                    || decision.latest_activation_at() != expected_latest
                    || expected_latest.is_some_and(|latest| decision.starts_at() > latest)
                {
                    return Err(OrchestrationError::Mission(format!(
                        "restored scheduling decision for {} disagrees with accepted Task timing",
                        decision.task_ref()
                    )));
                }
            }
        }
        for group_id in control.group_ids() {
            let group = control
                .group(&group_id)
                .expect("Control returned its own Group identity");
            if group.task_executions().next().is_some() && !orchestration_groups.contains(&group_id)
            {
                return Err(OrchestrationError::Mission(format!(
                    "restored Mission-level Group {group_id} has no orchestration authority"
                )));
            }
        }
        Ok(())
    }
}
