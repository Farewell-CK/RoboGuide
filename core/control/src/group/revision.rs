//! Control-owned replacement of an application-confirmed stopped Mission configuration.

use super::*;

impl ControlPlane {
    /// Replaces a blocked Group after the application has durably checked original stop/revoke gates.
    ///
    /// This creates no resource commitment or executor selection. The caller retains the original
    /// application writer/transaction and supplies its accepted plans, never raw model authority.
    #[allow(clippy::too_many_arguments)]
    pub fn supersede_mission_group<E: EventSink>(
        &mut self,
        source_id: &ExecutionGroupId,
        target_id: ExecutionGroupId,
        source_plan: &MissionPlan,
        target_plan: &MissionPlan,
        timestamp: TimestampMs,
        correlation: &CorrelationId,
        events: &mut E,
    ) -> Result<BTreeSet<TaskId>, ControlError> {
        self.validate_mission_group_plan(source_id, source_plan)?;
        let source = self.group(source_id).expect("validated original Group");
        if source.lifecycle != GroupLifecycle::Blocked
            || source.superseded_by.is_some()
            || source_id == &target_id
            || self.groups.contains_key(&target_id)
            || source_plan.goal() != target_plan.goal()
            || source_plan.actors() != target_plan.actors()
            || source_plan.binding_semantics() != target_plan.binding_semantics()
        {
            return Err(ControlError::InvalidProposal(
                "revision source, target, original goal or Actor binding contract is invalid"
                    .into(),
            ));
        }
        let completed: BTreeSet<_> = source
            .task_executions()
            .filter(|task| task.lifecycle() == TaskExecutionLifecycle::Completed)
            .map(|task| task.task_ref().task_id().clone())
            .collect();
        for task_id in &completed {
            let original = source_plan.task_graph().task(task_id);
            if original.is_none() || original != target_plan.task_graph().task(task_id) {
                return Err(ControlError::InvalidProposal(
                    "revision changed or removed an actually satisfied Task".into(),
                ));
            }
        }
        let mut candidate = self.clone();
        let mut pending = ports::PendingEvents::default();
        candidate.create_mission_group(
            target_id.clone(),
            target_plan,
            timestamp,
            correlation,
            &mut pending,
        )?;
        {
            let target = candidate
                .groups
                .get_mut(&target_id)
                .expect("new unbound Group");
            for task in target.task_executions.values_mut() {
                if completed.contains(task.task_ref().task_id()) {
                    *task = task.with_lifecycle(TaskExecutionLifecycle::Completed);
                }
            }
        }
        candidate.validate_mission_group_plan(&target_id, target_plan)?;
        {
            let source = candidate.groups.get_mut(source_id).expect("original Group");
            source.superseded_by = Some(target_id);
            source.lifecycle = GroupLifecycle::Superseded;
        }
        candidate.release_group(source_id, timestamp, correlation, &mut pending)?;
        *self = candidate;
        pending.publish(events);
        Ok(completed)
    }
}
