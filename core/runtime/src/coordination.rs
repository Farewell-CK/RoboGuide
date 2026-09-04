//! Runtime lifecycle for transport-neutral execution coordination mechanisms.

use crate::{ExecutionRuntimeError, RuntimeExecutionManager};
use domain::{
    ContextRoleId, CoordinationContext, CoordinationContextId, CoordinationMechanism,
    ExecutionCouplingMode, ExecutionGroupId, GroupSharedViewSpec, PeerChannelSpec,
};
use std::collections::{BTreeMap, BTreeSet};

/// Stable Runtime key for one Context inside one Mission-level Group.
pub(crate) type CoordinationKey = (ExecutionGroupId, CoordinationContextId);

/// Observable lifecycle of a deployment-resolved peer coordination channel.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum PeerChannelLifecycle {
    /// Descriptor is registered but Local EAIOS peers have not confirmed readiness.
    Planned,
    /// Local EAIOS peers confirmed the direct channel is ready.
    Ready,
    /// Runtime fenced channel use after ambiguity or peer loss.
    Fenced,
    /// Coordination context ended and the descriptor is no longer usable.
    Closed,
}

/// Runtime-owned descriptor and lifecycle for a direct Local EAIOS peer channel.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct RuntimePeerChannel {
    /// Mission-level Group containing the peers.
    group_id: ExecutionGroupId,
    /// Coordination Context declaring the channel.
    context_id: CoordinationContextId,
    /// Logical ContextRole peers independent of Node placement.
    peers: Vec<ContextRoleId>,
    /// Deployment-resolved transport-neutral profile.
    descriptor: PeerChannelSpec,
    /// Current channel lifecycle.
    pub(crate) lifecycle: PeerChannelLifecycle,
}

impl RuntimePeerChannel {
    /// Returns the owning Mission-level Group.
    pub const fn group_id(&self) -> &ExecutionGroupId {
        &self.group_id
    }

    /// Returns the declaring coordination Context.
    pub const fn context_id(&self) -> &CoordinationContextId {
        &self.context_id
    }

    /// Returns logical peers without resolving them to physical Nodes.
    pub fn peers(&self) -> &[ContextRoleId] {
        &self.peers
    }

    /// Returns the deployment-resolved channel descriptor.
    pub const fn descriptor(&self) -> &PeerChannelSpec {
        &self.descriptor
    }

    /// Returns the current Runtime lifecycle.
    pub const fn lifecycle(&self) -> PeerChannelLifecycle {
        self.lifecycle
    }
}

/// Runtime copy of one Mission-owned coordination declaration.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub(crate) struct RuntimeCoordinationContext {
    /// Owning Mission-level Group.
    pub(crate) group_id: ExecutionGroupId,
    /// Mission-owned Context identity.
    pub(crate) context_id: CoordinationContextId,
    /// Context default coupling mode.
    pub(crate) coupling_mode: ExecutionCouplingMode,
    /// Selective shared view declaration, when present.
    pub(crate) shared_view: Option<GroupSharedViewSpec>,
    /// Number of Mission-owned relation specifications in this Context.
    #[serde(default)]
    pub(crate) relation_count: usize,
}

/// Readiness of the mechanisms declared by a coupling mode.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CoordinationReadiness {
    /// All mode-required mechanisms are declared and live.
    Ready,
    /// The mode requires a selective Group shared view which is absent.
    WaitingForSharedView,
    /// The mode requires a direct peer channel which is absent or not ready.
    WaitingForPeerChannel,
    /// The mode requires at least one Runtime relation evidence stream.
    WaitingForRelationEvidence,
}

impl RuntimeExecutionManager {
    /// Registers one Mission-owned Context without resolving peers to physical Nodes.
    pub fn register_coordination_context(
        &mut self,
        group_id: &ExecutionGroupId,
        context: &CoordinationContext,
    ) -> Result<(), ExecutionRuntimeError> {
        let key = (group_id.clone(), context.context_id().clone());
        let declaration = RuntimeCoordinationContext {
            group_id: group_id.clone(),
            context_id: context.context_id().clone(),
            coupling_mode: context.coupling_mode(),
            shared_view: context.shared_view().cloned(),
            relation_count: context.relations().len(),
        };
        let channel = context.peer_channel().map(|descriptor| {
            let mut peers = context
                .roles()
                .iter()
                .map(|role| role.context_role_id().clone())
                .collect::<Vec<_>>();
            peers.sort();
            RuntimePeerChannel {
                group_id: group_id.clone(),
                context_id: context.context_id().clone(),
                peers,
                descriptor: descriptor.clone(),
                lifecycle: PeerChannelLifecycle::Planned,
            }
        });
        if let Some(existing) = self.coordination_contexts.get(&key) {
            if existing != &declaration {
                return Err(ExecutionRuntimeError::ExecutionConflict(format!(
                    "coordination Context {} in Group {}",
                    context.context_id(),
                    group_id
                )));
            }
            let channel_matches = match (self.peer_channels.get(&key), channel.as_ref()) {
                (None, None) => true,
                (Some(existing), Some(expected)) => {
                    existing.descriptor == expected.descriptor && existing.peers == expected.peers
                }
                _ => false,
            };
            if !channel_matches {
                return Err(ExecutionRuntimeError::ExecutionConflict(format!(
                    "peer channel {} in Group {}",
                    context.context_id(),
                    group_id
                )));
            }
            return Ok(());
        }
        if self.peer_channels.contains_key(&key) {
            return Err(ExecutionRuntimeError::ExecutionConflict(format!(
                "orphan peer channel {} in Group {}",
                context.context_id(),
                group_id
            )));
        }
        self.coordination_contexts.insert(key.clone(), declaration);
        if let Some(channel) = channel {
            self.peer_channels.insert(key, channel);
        }
        Ok(())
    }

    /// Confirms restored coordination declarations match an accepted MissionPlan exactly.
    pub fn validate_coordination_contexts(
        &self,
        group_id: &ExecutionGroupId,
        contexts: &[CoordinationContext],
    ) -> Result<(), ExecutionRuntimeError> {
        let mut expected_contexts = BTreeMap::new();
        let mut expected_channels = BTreeMap::new();
        for context in contexts {
            let key = (group_id.clone(), context.context_id().clone());
            expected_contexts.insert(
                key.clone(),
                RuntimeCoordinationContext {
                    group_id: group_id.clone(),
                    context_id: context.context_id().clone(),
                    coupling_mode: context.coupling_mode(),
                    shared_view: context.shared_view().cloned(),
                    relation_count: context.relations().len(),
                },
            );
            if let Some(descriptor) = context.peer_channel() {
                let mut peers = context
                    .roles()
                    .iter()
                    .map(|role| role.context_role_id().clone())
                    .collect::<Vec<_>>();
                peers.sort();
                expected_channels.insert(key, (peers, descriptor.clone()));
            }
        }
        let actual_contexts = self
            .coordination_contexts
            .iter()
            .filter(|((candidate, _), _)| candidate == group_id)
            .map(|(key, value)| (key.clone(), value.clone()))
            .collect::<BTreeMap<_, _>>();
        let actual_channels = self
            .peer_channels
            .iter()
            .filter(|((candidate, _), _)| candidate == group_id)
            .map(|(key, value)| (key.clone(), (value.peers.clone(), value.descriptor.clone())))
            .collect::<BTreeMap<_, _>>();
        let legacy_default_contexts = actual_contexts.is_empty()
            && expected_contexts.values().all(|context| {
                context.coupling_mode == ExecutionCouplingMode::Independent
                    && context.shared_view.is_none()
            })
            && expected_channels.is_empty();
        if !legacy_default_contexts
            && (expected_contexts != actual_contexts || expected_channels != actual_channels)
        {
            return Err(ExecutionRuntimeError::InvalidCheckpoint(format!(
                "Runtime coordination declarations differ for Group {group_id}"
            )));
        }
        Ok(())
    }

    /// Returns mode mechanism readiness without applying Task progression policy.
    pub fn coordination_readiness(
        &self,
        group_id: &ExecutionGroupId,
        context_id: &CoordinationContextId,
    ) -> Option<CoordinationReadiness> {
        let key = (group_id.clone(), context_id.clone());
        let mode = self.coordination_contexts.get(&key)?.coupling_mode;
        self.coordination_readiness_for_mode(group_id, context_id, mode)
    }

    /// Returns mechanism readiness for a Task's effective mode override.
    pub fn coordination_readiness_for_mode(
        &self,
        group_id: &ExecutionGroupId,
        context_id: &CoordinationContextId,
        mode: ExecutionCouplingMode,
    ) -> Option<CoordinationReadiness> {
        let key = (group_id.clone(), context_id.clone());
        let context = self.coordination_contexts.get(&key)?;
        // Sequential handoff readiness is supplied by the existing Mission DAG and Task
        // lifecycle; Runtime owns no duplicate handoff registry in this first version.
        if mode.requires(CoordinationMechanism::GroupSharedState) && context.shared_view.is_none() {
            return Some(CoordinationReadiness::WaitingForSharedView);
        }
        if mode.requires(CoordinationMechanism::RelationEvidence) && context.relation_count == 0 {
            return Some(CoordinationReadiness::WaitingForRelationEvidence);
        }
        if mode.requires(CoordinationMechanism::DirectPeerChannel)
            && self
                .peer_channels
                .get(&key)
                .is_none_or(|channel| channel.lifecycle != PeerChannelLifecycle::Ready)
        {
            return Some(CoordinationReadiness::WaitingForPeerChannel);
        }
        Some(CoordinationReadiness::Ready)
    }

    /// Marks a declared direct channel ready after deployment/local integration confirmation.
    pub fn mark_peer_channel_ready(
        &mut self,
        group_id: &ExecutionGroupId,
        context_id: &CoordinationContextId,
    ) -> Result<(), ExecutionRuntimeError> {
        self.set_peer_channel_lifecycle(group_id, context_id, PeerChannelLifecycle::Ready)
    }

    /// Fences a direct peer channel while preserving its declaration for reconciliation.
    pub fn fence_peer_channel(
        &mut self,
        group_id: &ExecutionGroupId,
        context_id: &CoordinationContextId,
    ) -> Result<(), ExecutionRuntimeError> {
        self.set_peer_channel_lifecycle(group_id, context_id, PeerChannelLifecycle::Fenced)
    }

    /// Closes a direct peer channel descriptor after its coordination scope ends.
    pub fn close_peer_channel(
        &mut self,
        group_id: &ExecutionGroupId,
        context_id: &CoordinationContextId,
    ) -> Result<(), ExecutionRuntimeError> {
        self.set_peer_channel_lifecycle(group_id, context_id, PeerChannelLifecycle::Closed)
    }

    /// Returns peer channel snapshots for one Group in stable Context order.
    pub fn peer_channels(&self, group_id: &ExecutionGroupId) -> Vec<RuntimePeerChannel> {
        self.peer_channels
            .values()
            .filter(|channel| channel.group_id() == group_id)
            .cloned()
            .collect()
    }

    /// Changes one known channel lifecycle without creating transport authority.
    fn set_peer_channel_lifecycle(
        &mut self,
        group_id: &ExecutionGroupId,
        context_id: &CoordinationContextId,
        lifecycle: PeerChannelLifecycle,
    ) -> Result<(), ExecutionRuntimeError> {
        let channel = self
            .peer_channels
            .get_mut(&(group_id.clone(), context_id.clone()))
            .ok_or_else(|| {
                ExecutionRuntimeError::ReconciliationRequired(format!(
                    "unknown peer channel {context_id} in Group {group_id}"
                ))
            })?;
        if channel.lifecycle == PeerChannelLifecycle::Closed
            && lifecycle != PeerChannelLifecycle::Closed
        {
            return Err(ExecutionRuntimeError::ReconciliationRequired(format!(
                "closed peer channel {context_id} in Group {group_id} cannot be reopened"
            )));
        }
        channel.lifecycle = lifecycle;
        Ok(())
    }
}

/// Restores coordination maps while rejecting duplicate and orphan channels.
#[allow(clippy::type_complexity)]
pub(crate) fn restore_coordination_maps(
    contexts: Vec<RuntimeCoordinationContext>,
    channels: Vec<RuntimePeerChannel>,
) -> Result<
    (
        BTreeMap<CoordinationKey, RuntimeCoordinationContext>,
        BTreeMap<CoordinationKey, RuntimePeerChannel>,
    ),
    ExecutionRuntimeError,
> {
    let mut context_map = BTreeMap::new();
    for context in contexts {
        if let Some(view) = &context.shared_view {
            view.validate().map_err(|error| {
                ExecutionRuntimeError::InvalidCheckpoint(format!(
                    "checkpoint Group shared view is invalid: {error}"
                ))
            })?;
        }
        let key = (context.group_id.clone(), context.context_id.clone());
        if context_map.insert(key, context).is_some() {
            return Err(ExecutionRuntimeError::InvalidCheckpoint(
                "checkpoint contains duplicate coordination Context".to_string(),
            ));
        }
    }
    let mut channel_map = BTreeMap::new();
    for channel in channels {
        let key = (channel.group_id.clone(), channel.context_id.clone());
        if !context_map.contains_key(&key)
            || channel.peers.iter().collect::<BTreeSet<_>>().len() != channel.peers.len()
            || channel.descriptor.profile_id.trim().is_empty()
            || channel.descriptor.message_schema.trim().is_empty()
            || channel_map.insert(key, channel).is_some()
        {
            return Err(ExecutionRuntimeError::InvalidCheckpoint(
                "checkpoint contains duplicate or orphan peer channel".to_string(),
            ));
        }
    }
    Ok((context_map, channel_map))
}

#[cfg(test)]
mod tests {
    use super::*;
    use domain::{ActorId, ContextRole, ExecutionRelationSpec};

    /// Sequential handoff delegates readiness to the Mission DAG instead of a Runtime registry.
    #[test]
    fn sequential_handoff_uses_existing_task_progression() {
        let context_id = CoordinationContextId::new("handoff").expect("context id valid");
        let context = CoordinationContext::new_with_coordination(
            context_id.clone(),
            vec![ContextRole::new(
                ContextRoleId::new("carrier").expect("role id valid"),
                ActorId::new("carrier").expect("actor id valid"),
            )],
            Vec::new(),
            ExecutionCouplingMode::SequentialHandoff,
            None,
            None,
        )
        .expect("context valid");
        let group_id = ExecutionGroupId::new("group-handoff").expect("group id valid");
        let mut runtime = RuntimeExecutionManager::new();
        runtime
            .register_coordination_context(&group_id, &context)
            .expect("context registers");

        assert_eq!(
            runtime.coordination_readiness(&group_id, &context_id),
            Some(CoordinationReadiness::Ready)
        );
    }

    /// A tightly coupled Context keeps a planned channel until local readiness is confirmed.
    #[test]
    fn peer_channel_lifecycle_is_explicit_and_checkpointed() {
        let context_id = CoordinationContextId::new("guidance").expect("context id valid");
        let context = CoordinationContext::new_with_coordination(
            context_id.clone(),
            vec![
                ContextRole::new(
                    ContextRoleId::new("dog").expect("role id valid"),
                    ActorId::new("dog").expect("actor id valid"),
                ),
                ContextRole::new(
                    ContextRoleId::new("cane").expect("role id valid"),
                    ActorId::new("cane").expect("actor id valid"),
                ),
            ],
            vec![
                ExecutionRelationSpec::new(
                    domain::ExecutionRelationId::new("dog-guards-cane").expect("relation id valid"),
                    domain::PlannedExecutionRef::new(
                        domain::TaskId::new("dog-task").expect("task id valid"),
                        domain::RoleId::new("dog-role").expect("role id valid"),
                    ),
                    domain::PlannedExecutionRef::new(
                        domain::TaskId::new("cane-task").expect("task id valid"),
                        domain::RoleId::new("cane-role").expect("role id valid"),
                    ),
                    domain::ExecutionRelationKind::RequiresActive,
                )
                .expect("relation valid"),
            ],
            ExecutionCouplingMode::TightlyCoupledCooperation,
            Some(
                GroupSharedViewSpec::new(
                    None,
                    vec![
                        domain::GroupViewBinding::new(
                            ContextRoleId::new("dog").expect("context role id valid"),
                            domain::GroupViewField::Pose,
                            "dog-pose",
                            "roboguide.pose/v1",
                        )
                        .expect("view binding valid"),
                    ],
                    true,
                )
                .expect("shared view valid"),
            ),
            Some(PeerChannelSpec {
                profile_id: "guidance-peer".to_string(),
                message_schema: "guidance/v1".to_string(),
            }),
        )
        .expect("context valid");
        let group_id = ExecutionGroupId::new("group-guidance").expect("group id valid");
        let mut runtime = RuntimeExecutionManager::new();
        runtime
            .register_coordination_context(&group_id, &context)
            .expect("context registers");
        let conflicting_context = CoordinationContext::new_with_coordination(
            context_id.clone(),
            context.roles().to_vec(),
            context.relations().to_vec(),
            ExecutionCouplingMode::TightlyCoupledCooperation,
            context.shared_view().cloned(),
            Some(PeerChannelSpec {
                profile_id: "different-peer".to_string(),
                message_schema: "guidance/v1".to_string(),
            }),
        )
        .expect("conflicting context remains structurally valid");
        assert!(
            runtime
                .register_coordination_context(&group_id, &conflicting_context)
                .is_err()
        );
        assert_eq!(
            runtime.peer_channels(&group_id)[0].descriptor().profile_id,
            "guidance-peer"
        );
        assert_eq!(
            runtime.coordination_readiness(&group_id, &context_id),
            Some(CoordinationReadiness::WaitingForPeerChannel)
        );
        runtime
            .mark_peer_channel_ready(&group_id, &context_id)
            .expect("channel becomes ready");
        assert_eq!(
            runtime.coordination_readiness(&group_id, &context_id),
            Some(CoordinationReadiness::Ready)
        );
        let restored = RuntimeExecutionManager::restore(runtime.checkpoint())
            .expect("checkpoint restores conservatively");
        assert_eq!(
            restored.peer_channels(&group_id)[0].lifecycle(),
            PeerChannelLifecycle::Fenced
        );
    }
}
