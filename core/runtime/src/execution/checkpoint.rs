//! Runtime execution checkpoint creation and restoration.

use super::*;

impl RuntimeExecutionManager {
    /// Creates an empty live execution authority.
    pub const fn new() -> Self {
        Self {
            executions: BTreeMap::new(),
            execution_status: BTreeMap::new(),
            execution_sequences: BTreeMap::new(),
            execution_nodes: BTreeMap::new(),
            active_executions: BTreeMap::new(),
            restored_executions: BTreeSet::new(),
            activated_tasks: BTreeSet::new(),
            reactivation_attempts: BTreeSet::new(),
            relations: BTreeMap::new(),
            relation_states: BTreeMap::new(),
            relation_fences: BTreeSet::new(),
            relation_proofs: BTreeMap::new(),
            coordination_contexts: BTreeMap::new(),
            peer_channels: BTreeMap::new(),
            spatial_evidence: BTreeMap::new(),
            attempt_generations: BTreeMap::new(),
            dispatch_outbox: BTreeMap::new(),
            cancellation_intents: BTreeSet::new(),
        }
    }

    /// Returns a durable transport-neutral Runtime projection.
    pub fn checkpoint(&self) -> RuntimeExecutionCheckpoint {
        RuntimeExecutionCheckpoint {
            cancellation_intents: self.cancellation_intents.clone(),
            executions: self.executions.clone(),
            execution_status: self.execution_status.clone(),
            execution_sequences: self.execution_sequences.clone(),
            execution_nodes: self.execution_nodes.clone(),
            active_executions: self
                .active_executions
                .iter()
                .map(
                    |((group_id, task_ref, role_id), execution_id)| ActiveExecutionCheckpoint {
                        group_id: group_id.clone(),
                        task_ref: task_ref.clone(),
                        role_id: role_id.clone(),
                        execution_id: execution_id.clone(),
                    },
                )
                .collect(),
            relations: self.relations.values().cloned().collect(),
            relation_states: self
                .relation_states
                .iter()
                .map(|((group_id, relation_id), state)| RelationStateCheckpoint {
                    group_id: group_id.clone(),
                    relation_id: relation_id.clone(),
                    state: *state,
                })
                .collect(),
            relation_fences: self
                .relation_fences
                .iter()
                .map(|(group_id, relation_id)| RelationFenceCheckpoint {
                    group_id: group_id.clone(),
                    relation_id: relation_id.clone(),
                })
                .collect(),
            relation_proofs: self
                .relation_proofs
                .iter()
                .map(
                    |((group_id, relation_id), target_execution_id)| RelationProofCheckpoint {
                        group_id: group_id.clone(),
                        relation_id: relation_id.clone(),
                        target_execution_id: target_execution_id.clone(),
                    },
                )
                .collect(),
            coordination_contexts: self.coordination_contexts.values().cloned().collect(),
            peer_channels: self.peer_channels.values().cloned().collect(),
            spatial_evidence: self.spatial_evidence.values().cloned().collect(),
            attempt_generations: self
                .attempt_generations
                .iter()
                .map(
                    |((group_id, task_ref, role_id), generation)| AttemptGenerationCheckpoint {
                        group_id: group_id.clone(),
                        task_ref: task_ref.clone(),
                        role_id: role_id.clone(),
                        generation: *generation,
                    },
                )
                .collect(),
            dispatch_outbox: self
                .dispatch_outbox
                .values()
                .map(|intent| DispatchIntentCheckpoint {
                    execution_id: intent.execution_id.clone(),
                    command: intent.command.clone(),
                    resource_ids: intent.resource_ids.clone(),
                    delivery_attempts: intent.delivery_attempts,
                    delivered: intent.delivered,
                })
                .collect(),
        }
    }

    /// Restores a checkpoint conservatively without granting replay authority.
    pub fn restore(checkpoint: RuntimeExecutionCheckpoint) -> Result<Self, ExecutionRuntimeError> {
        validate_checkpoint(&checkpoint)?;
        let (relations, relation_states, relation_fences, relation_proofs) = restore_relation_maps(
            checkpoint.relations.clone(),
            checkpoint.relation_states.clone(),
            checkpoint.relation_fences.clone(),
            checkpoint.relation_proofs.clone(),
        )?;
        let (coordination_contexts, mut peer_channels) = restore_coordination_maps(
            checkpoint.coordination_contexts.clone(),
            checkpoint.peer_channels.clone(),
        )?;
        let mut spatial_evidence = BTreeMap::new();
        for evidence in checkpoint.spatial_evidence {
            if evidence.execution_id.trim().is_empty() || evidence.frame_id.trim().is_empty() {
                return Err(ExecutionRuntimeError::InvalidCheckpoint(
                    "checkpoint contains invalid shared spatial evidence".to_string(),
                ));
            }
            if spatial_evidence.insert(evidence.slot(), evidence).is_some() {
                return Err(ExecutionRuntimeError::InvalidCheckpoint(
                    "checkpoint contains duplicate shared spatial evidence".to_string(),
                ));
            }
        }
        for channel in peer_channels.values_mut() {
            if channel.lifecycle() == crate::PeerChannelLifecycle::Ready {
                channel.lifecycle = crate::PeerChannelLifecycle::Fenced;
            }
            channel.readiness.clear();
        }
        let restored_executions = checkpoint.executions.keys().cloned().collect();
        let activated_tasks = checkpoint
            .active_executions
            .iter()
            .filter(|active| {
                checkpoint
                    .execution_status
                    .get(&active.execution_id)
                    .is_some_and(|status| status.proves_activation())
            })
            .map(|active| (active.group_id.clone(), active.task_ref.clone()))
            .collect();
        let mut active_executions = BTreeMap::new();
        for active in checkpoint.active_executions {
            let key = (active.group_id, active.task_ref, active.role_id);
            if active_executions.insert(key, active.execution_id).is_some() {
                return Err(ExecutionRuntimeError::InvalidCheckpoint(
                    "checkpoint contains duplicate active Group Task role".to_string(),
                ));
            }
        }
        let execution_status = checkpoint
            .execution_status
            .into_iter()
            .map(|(execution_id, status)| {
                let restored_status = if status.is_terminal() {
                    status
                } else {
                    ExecutionStatus::Unknown
                };
                (execution_id, restored_status)
            })
            .collect();
        for (slot, evidence) in &spatial_evidence {
            if active_executions.get(slot) != Some(&evidence.execution_id)
                || checkpoint.execution_nodes.get(&evidence.execution_id) != Some(&evidence.node_id)
            {
                return Err(ExecutionRuntimeError::InvalidCheckpoint(
                    "checkpoint shared spatial evidence is not owned by the current attempt"
                        .to_string(),
                ));
            }
        }
        let mut restored = Self {
            cancellation_intents: checkpoint.cancellation_intents,
            executions: checkpoint.executions,
            execution_status,
            execution_sequences: checkpoint.execution_sequences,
            execution_nodes: checkpoint.execution_nodes,
            active_executions,
            restored_executions,
            activated_tasks,
            reactivation_attempts: BTreeSet::new(),
            relations,
            relation_states,
            relation_fences,
            relation_proofs,
            coordination_contexts,
            peer_channels,
            spatial_evidence,
            attempt_generations: checkpoint
                .attempt_generations
                .into_iter()
                .map(|entry| {
                    (
                        (entry.group_id, entry.task_ref, entry.role_id),
                        entry.generation,
                    )
                })
                .collect(),
            dispatch_outbox: checkpoint
                .dispatch_outbox
                .into_iter()
                .map(|intent| {
                    (
                        intent.execution_id.clone(),
                        DispatchIntent {
                            execution_id: intent.execution_id,
                            command: intent.command,
                            resource_ids: intent.resource_ids,
                            delivery_attempts: intent.delivery_attempts,
                            delivered: intent.delivered,
                        },
                    )
                })
                .collect(),
        };
        for active in restored.active_executions.keys() {
            restored
                .attempt_generations
                .entry(active.clone())
                .or_insert(1);
        }
        restored.refresh_all_relations_after_restore();
        Ok(restored)
    }
}
