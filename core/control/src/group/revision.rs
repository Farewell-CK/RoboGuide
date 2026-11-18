//! Control-owned replacement of an application-confirmed stopped Mission configuration.

use super::*;

/// Keeps completed Task Actor/ContextRole bindings immutable across a Group replacement.
fn validate_completed_bindings(
    source: &MissionPlan,
    target: &MissionPlan,
    completed: &BTreeSet<TaskId>,
) -> Result<(), ControlError> {
    if source.goal() != target.goal()
        || source.schema_version() != target.schema_version()
        || source.contexts().len() != target.contexts().len()
        || source
            .contexts()
            .iter()
            .zip(target.contexts())
            .any(|(source, target)| !source.same_revision_contract(target))
    {
        return Err(ControlError::InvalidProposal(
            "revision source, target, original goal, schema or Context safety contract is invalid"
                .into(),
        ));
    }
    for task_id in completed {
        let Some(task) = source.task_graph().task(task_id) else {
            return Err(ControlError::InvalidProposal(
                "revision completed Task is absent from the source plan".into(),
            ));
        };
        if target.task_graph().task(task_id) != Some(task) {
            return Err(ControlError::InvalidProposal(
                "revision changed or removed an actually satisfied Task".into(),
            ));
        }
        let context_id = task.continuity().context_id();
        let source_context = source
            .contexts()
            .iter()
            .find(|context| context.context_id() == context_id)
            .expect("MissionPlan validation guarantees source Context");
        let target_context = target
            .contexts()
            .iter()
            .find(|context| context.context_id() == context_id)
            .ok_or_else(|| {
                ControlError::InvalidProposal(
                    "revision removed the Context used by a completed Task".into(),
                )
            })?;
        for role_id in task.continuity().context_roles().values() {
            let source_role = source_context
                .role(role_id)
                .expect("validated source ContextRole");
            if target_context.role(role_id) != Some(source_role) {
                return Err(ControlError::InvalidProposal(
                    "revision changed a ContextRole used by a completed Task".into(),
                ));
            }
            let source_actor = source
                .actors()
                .iter()
                .find(|actor| actor.id() == source_role.actor_id())
                .expect("MissionPlan validation guarantees source Actor");
            if target
                .actors()
                .iter()
                .find(|actor| actor.id() == source_actor.id())
                != Some(source_actor)
            {
                return Err(ControlError::InvalidProposal(
                    "revision changed the Actor binding used by a completed Task".into(),
                ));
            }
        }
        for actor_id in task
            .requirement()
            .roles()
            .iter()
            .filter_map(|role| role.actor_id())
        {
            let source_actor = source
                .actors()
                .iter()
                .find(|actor| actor.id() == actor_id)
                .expect("MissionPlan validation guarantees source Actor");
            if target
                .actors()
                .iter()
                .find(|actor| actor.id() == source_actor.id())
                != Some(source_actor)
            {
                return Err(ControlError::InvalidProposal(
                    "revision changed the Actor binding used by a completed Task".into(),
                ));
            }
        }
    }
    Ok(())
}

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
        let completed: BTreeSet<_> = source
            .task_executions()
            .filter(|task| task.lifecycle() == TaskExecutionLifecycle::Completed)
            .map(|task| task.task_ref().task_id().clone())
            .collect();
        if source.lifecycle != GroupLifecycle::Blocked
            || source.superseded_by.is_some()
            || source_id == &target_id
            || self.groups.contains_key(&target_id)
        {
            return Err(ControlError::InvalidProposal(
                "revision source, target, original goal or Actor binding contract is invalid"
                    .into(),
            ));
        }
        validate_completed_bindings(source_plan, target_plan, &completed)?;
        let mut candidate = self.clone();
        let mission_id = source_plan.goal().mission_id().clone();
        let stale_actors: BTreeSet<_> = source_plan
            .actors()
            .iter()
            .filter(|actor| {
                target_plan
                    .actors()
                    .iter()
                    .find(|candidate| candidate.id() == actor.id())
                    .is_none_or(|replacement| {
                        replacement.physical_entity() != actor.physical_entity()
                    })
            })
            .map(|actor| actor.id().clone())
            .collect();
        candidate
            .actor_bindings
            .retain(|(bound_mission, actor_id), _| {
                bound_mission != &mission_id || !stale_actors.contains(actor_id)
            });
        candidate
            .actor_candidate_restrictions
            .retain(|(restricted_mission, actor_id), _| {
                restricted_mission != &mission_id || !stale_actors.contains(actor_id)
            });
        candidate
            .actor_node_constraints
            .retain(|(constrained_mission, actor_id), _| {
                constrained_mission != &mission_id || !stale_actors.contains(actor_id)
            });
        candidate.replace_mission_binding_semantics_for_revision(
            mission_id,
            target_plan.binding_semantics(),
        );
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
