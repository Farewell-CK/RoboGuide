//! Execution Relation, peer-channel, and selective Group view facade.

use super::*;

impl<E: EventSink + Clone> IntegrationRuntimeBridge<E> {
    /// Installs Mission-owned relation specifications into the sole Runtime live registry.
    pub fn register_execution_relations(
        &mut self,
        plan: &domain::MissionPlan,
        group_id: &domain::ExecutionGroupId,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
    ) -> Result<(), IntegrationRuntimeError> {
        crate::SupportedMechanismProfile::current()
            .validate(plan)
            .map_err(|error| IntegrationRuntimeError::Protocol(error.to_string()))?;
        let group = self.control.group(group_id).ok_or_else(|| {
            IntegrationRuntimeError::Protocol(
                "execution relations require an existing Mission-level Group".to_string(),
            )
        })?;
        if group.mission_id() != plan.goal().mission_id() {
            return Err(IntegrationRuntimeError::Protocol(
                "execution relation plan differs from Group Mission".to_string(),
            ));
        }
        let specifications = plan
            .contexts()
            .iter()
            .flat_map(domain::CoordinationContext::relations)
            .cloned()
            .collect::<Vec<_>>();
        let mut candidate_runtime = self.runtime.clone();
        for context in plan.contexts() {
            candidate_runtime
                .register_coordination_context(group_id, context)
                .map_err(|error| IntegrationRuntimeError::Protocol(error.to_string()))?;
        }
        let task_modes = effective_task_coupling_modes(plan);
        let runtime_events = candidate_runtime
            .register_relations_with_modes(
                group_id,
                plan.goal().mission_id(),
                &specifications,
                &task_modes,
            )
            .map_err(|error| IntegrationRuntimeError::Protocol(error.to_string()))?;
        self.runtime = candidate_runtime;
        for event in runtime_events {
            append_runtime_evidence(&mut self.events, &event, timestamp, correlation_id);
            self.runtime_events.push_back(event);
        }
        Ok(())
    }

    /// Confirms restored Runtime relation authority exactly matches an accepted MissionPlan.
    pub fn validate_execution_relations(
        &self,
        plan: &domain::MissionPlan,
        group_id: &domain::ExecutionGroupId,
    ) -> Result<(), IntegrationRuntimeError> {
        let group = self.control.group(group_id).ok_or_else(|| {
            IntegrationRuntimeError::Checkpoint(
                "execution relation Group is absent from Control authority".to_string(),
            )
        })?;
        if group.mission_id() != plan.goal().mission_id() {
            return Err(IntegrationRuntimeError::Checkpoint(
                "execution relation plan differs from restored Group Mission".to_string(),
            ));
        }
        let specifications = plan
            .contexts()
            .iter()
            .flat_map(domain::CoordinationContext::relations)
            .cloned()
            .collect::<Vec<_>>();
        let task_modes = effective_task_coupling_modes(plan);
        self.runtime
            .validate_coordination_contexts(group_id, plan.contexts())
            .map_err(|error| IntegrationRuntimeError::Checkpoint(error.to_string()))?;
        self.runtime
            .validate_relations_with_modes(
                group_id,
                plan.goal().mission_id(),
                &specifications,
                &task_modes,
            )
            .map_err(|error| IntegrationRuntimeError::Checkpoint(error.to_string()))
    }

    /// Returns observable live relation snapshots for one Mission-level Group.
    pub fn relation_snapshots(
        &self,
        group_id: &domain::ExecutionGroupId,
    ) -> Vec<RuntimeRelationSnapshot> {
        self.runtime.relation_snapshots(group_id)
    }

    /// Returns whether typed localization evidence names the exact current attempt and Node owner.
    pub fn localization_evidence_is_current(
        &self,
        evidence: &domain::LocalizationVerificationEvidence,
    ) -> bool {
        self.runtime
            .shared_spatial_evidence_targets_current_attempt(
                &SharedSpatialEvidence::from_localization(evidence, TimestampMs::new(0)),
            )
    }

    /// Applies durable strong localization evidence to the current Runtime execution attempt.
    ///
    /// Evidence for a known current logical slot must match its exact attempt and Node owner.
    /// Evidence unrelated to any live slot remains valid Spatial Memory catalog evidence and is
    /// deliberately ignored by Runtime.
    pub fn observe_localization_evidence(
        &mut self,
        evidence: &domain::LocalizationVerificationEvidence,
        received_at: TimestampMs,
        correlation_id: &CorrelationId,
    ) -> Result<(), IntegrationRuntimeError> {
        let spatial_evidence = SharedSpatialEvidence::from_localization(evidence, received_at);
        if !self
            .runtime
            .shared_spatial_evidence_targets_current_attempt(&spatial_evidence)
        {
            return Ok(());
        }
        let runtime_events = self
            .runtime
            .observe_shared_spatial_evidence(spatial_evidence)
            .map_err(|error| IntegrationRuntimeError::Protocol(error.to_string()))?;
        for event in runtime_events {
            append_runtime_evidence(&mut self.events, &event, received_at, correlation_id);
            self.runtime_events.push_back(event);
        }
        Ok(())
    }

    /// Rehydrates current-attempt localization evidence without re-emitting durable events.
    ///
    /// Historical evidence for an older physical attempt remains in Spatial Memory but is not
    /// admitted to the current Runtime slot.
    pub fn restore_localization_evidence(
        &mut self,
        evidence: &domain::LocalizationVerificationEvidence,
        received_at: TimestampMs,
    ) -> Result<bool, IntegrationRuntimeError> {
        let evidence = SharedSpatialEvidence::from_localization(evidence, received_at);
        if !self
            .runtime
            .shared_spatial_evidence_targets_current_attempt(&evidence)
        {
            return Ok(false);
        }
        let changed = self.runtime.shared_spatial_evidence(
            evidence.group_id(),
            evidence.task_ref(),
            evidence.role_id(),
        ) != Some(&evidence);
        let _ = self
            .runtime
            .observe_shared_spatial_evidence(evidence)
            .map_err(|error| IntegrationRuntimeError::Checkpoint(error.to_string()))?;
        Ok(changed)
    }

    /// Returns transport-neutral direct peer channel lifecycle snapshots for one Group.
    pub fn peer_channel_snapshots(
        &self,
        group_id: &domain::ExecutionGroupId,
    ) -> Vec<runtime::RuntimePeerChannel> {
        self.runtime.peer_channels(group_id)
    }

    /// Returns peer channel snapshots with expired evidence conservatively shown as fenced.
    pub fn peer_channel_snapshots_at(
        &self,
        group_id: &domain::ExecutionGroupId,
        now: TimestampMs,
    ) -> Vec<runtime::RuntimePeerChannel> {
        self.runtime.peer_channels_at(group_id, now)
    }

    /// Returns whether one Context's declared coordination mechanisms are ready.
    pub fn coordination_readiness(
        &self,
        group_id: &domain::ExecutionGroupId,
        context_id: &domain::CoordinationContextId,
    ) -> Option<runtime::CoordinationReadiness> {
        self.runtime.coordination_readiness(group_id, context_id)
    }

    /// Builds a selective read-only Group view from State records and current bindings.
    pub fn group_shared_view(
        &self,
        plan: &domain::MissionPlan,
        group_id: &domain::ExecutionGroupId,
        context_id: &domain::CoordinationContextId,
        now: TimestampMs,
    ) -> Result<GroupSharedViewSnapshot, IntegrationRuntimeError> {
        let context = plan
            .contexts()
            .iter()
            .find(|context| context.context_id() == context_id)
            .ok_or_else(|| {
                IntegrationRuntimeError::Protocol("coordination Context is unknown".to_string())
            })?;
        let view = context.shared_view().ok_or_else(|| {
            IntegrationRuntimeError::Protocol("coordination Context has no shared view".to_string())
        })?;
        let group = self.control.group(group_id).ok_or_else(|| {
            IntegrationRuntimeError::Protocol("execution group is unknown".to_string())
        })?;
        if group.mission_id() != plan.goal().mission_id() {
            return Err(IntegrationRuntimeError::Protocol(
                "shared view plan differs from Group Mission".to_string(),
            ));
        }
        let context_tasks = plan
            .task_graph()
            .tasks()
            .iter()
            .filter(|task| task.continuity().context_id() == context_id)
            .map(|task| task.task_id())
            .collect::<BTreeSet<_>>();
        let state_records = self.state_records.records();
        let mut entries = Vec::new();
        for execution in group
            .task_executions()
            .filter(|execution| context_tasks.contains(execution.task_ref().task_id()))
        {
            for assignment in execution.assignments() {
                for binding in view.bindings() {
                    if execution.context_role(assignment.role_id())
                        != Some(binding.context_role_id())
                    {
                        continue;
                    }
                    let (record, execution_status, freshness) = match binding.field() {
                        domain::GroupViewField::Execution => (
                            None,
                            self.runtime
                                .current_execution_status(
                                    group_id,
                                    execution.task_ref(),
                                    assignment.role_id(),
                                )
                                .map(remote_status),
                            None,
                        ),
                        domain::GroupViewField::Pose | domain::GroupViewField::Velocity => {
                            let record = latest_group_record(
                                &state_records,
                                assignment.node_id(),
                                binding
                                    .state_export_id()
                                    .expect("validated spatial binding has State export"),
                                binding
                                    .payload_schema()
                                    .expect("validated spatial binding has payload schema"),
                            );
                            let freshness = view.include_freshness().then(|| match &record {
                                Some(record) if record.is_stale_at(now) => {
                                    GroupViewFreshness::Stale
                                }
                                Some(_) => GroupViewFreshness::Fresh,
                                None => GroupViewFreshness::Unknown,
                            });
                            (record, None, freshness)
                        }
                    };
                    let spatial_evidence = view.spatial_reference().and_then(|_| {
                        self.runtime
                            .shared_spatial_evidence(
                                group_id,
                                execution.task_ref(),
                                assignment.role_id(),
                            )
                            .cloned()
                    });
                    let spatial_verification = view.spatial_reference().map(|reference| {
                        spatial_evidence.as_ref().map_or(
                            GroupSpatialVerification::Unknown,
                            |evidence| {
                                if evidence.selector() == reference.selector()
                                    && evidence.frame_id() == reference.frame_id()
                                {
                                    GroupSpatialVerification::Verified
                                } else {
                                    GroupSpatialVerification::Mismatched
                                }
                            },
                        )
                    });
                    entries.push(GroupSharedViewEntry {
                        task_ref: execution.task_ref().clone(),
                        role_id: assignment.role_id().clone(),
                        node_id: assignment.node_id().clone(),
                        field: binding.field(),
                        payload_schema: binding.payload_schema().map(str::to_string),
                        state_export_id: binding.state_export_id().map(str::to_string),
                        record,
                        execution_status,
                        freshness,
                        spatial_evidence,
                        spatial_verification,
                    });
                }
            }
        }
        Ok(GroupSharedViewSnapshot {
            group_id: group_id.clone(),
            context_id: context_id.clone(),
            spatial_reference: view.spatial_reference().cloned(),
            entries,
        })
    }

    /// Fences a direct peer channel while preserving its logical Context identity.
    pub fn fence_peer_channel(
        &mut self,
        group_id: &domain::ExecutionGroupId,
        context_id: &domain::CoordinationContextId,
    ) -> Result<(), IntegrationRuntimeError> {
        self.runtime
            .fence_peer_channel(group_id, context_id)
            .map_err(|error| IntegrationRuntimeError::Protocol(error.to_string()))
    }

    /// Closes Runtime peer descriptors after application orchestration ends their Group scope.
    pub fn close_group_peer_channels(&mut self, group_id: &domain::ExecutionGroupId) {
        self.runtime.close_peer_channels_for_group(group_id);
    }

    /// Applies an explicit Control recovery acknowledgement to one satisfied relation.
    pub fn acknowledge_relation_reconciliation(
        &mut self,
        group_id: &domain::ExecutionGroupId,
        relation_id: &domain::ExecutionRelationId,
    ) -> Result<(), IntegrationRuntimeError> {
        self.runtime
            .acknowledge_relation_reconciliation(group_id, relation_id)
            .map_err(|error| IntegrationRuntimeError::Protocol(error.to_string()))
    }

    /// Drains canonical Runtime transitions for application-level lifecycle handling.
    pub fn take_runtime_events(&mut self) -> Vec<ExecutionEvent> {
        self.runtime_events.drain(..).collect()
    }

    /// Reports terminal outcomes for active TaskExecutions without changing Mission or Group state.
    pub fn terminal_task_outcomes(&self) -> Vec<ObservedTaskOutcome> {
        let mut outcomes = Vec::new();
        for group_id in self.control.group_ids() {
            let Some(group) = self.control.group(&group_id) else {
                continue;
            };
            for task in group.task_executions().filter(|task| {
                task.lifecycle() == domain::TaskExecutionLifecycle::Active
                    && task_assignments_are_complete(task)
            }) {
                let role_ids = task
                    .assignments()
                    .iter()
                    .map(|assignment| assignment.role_id());
                if let Some(result) = self
                    .runtime
                    .task_result(&group_id, task.task_ref(), role_ids)
                {
                    outcomes.push(ObservedTaskOutcome {
                        group_id: group_id.clone(),
                        task_ref: task.task_ref().clone(),
                        result: match result {
                            runtime::ObservedTaskResult::Succeeded => ObservedTaskResult::Succeeded,
                            runtime::ObservedTaskResult::Failed => ObservedTaskResult::Failed,
                        },
                    });
                    continue;
                }
            }
        }
        outcomes
    }
}
