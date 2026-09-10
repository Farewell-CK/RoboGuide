//! Runtime coordination lifecycle tests.

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
        .peer_channels
        .get_mut(&(group_id.clone(), context_id.clone()))
        .expect("registered channel remains present")
        .lifecycle = PeerChannelLifecycle::Ready;
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
    let mut restored = restored;
    restored.close_peer_channels_for_group(&group_id);
    assert_eq!(
        restored.peer_channels(&group_id)[0].lifecycle(),
        PeerChannelLifecycle::Closed
    );
    assert!(restored.peer_channels(&group_id)[0].readiness().is_empty());
}

/// A channel becomes Ready only after both logical Local EAIOS endpoints identify one instance.
#[test]
fn peer_channel_requires_two_ended_identified_readiness() {
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
                domain::ExecutionRelationId::new("relation").expect("relation id valid"),
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
                vec![domain::GroupViewBinding::new_execution(
                    ContextRoleId::new("dog").expect("role id valid"),
                )],
                true,
            )
            .expect("view valid"),
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
    let readiness = |role: &str, node: &str, instance: &str, sequence: u64, ready: bool| {
        PeerChannelReadinessEvidence {
            group_id: group_id.clone(),
            context_id: context_id.clone(),
            context_role_id: ContextRoleId::new(role).expect("role id valid"),
            node_id: NodeId::new(node).expect("node id valid"),
            local_system_id: LocalSystemId::new(format!("{role}-system"))
                .expect("local system id valid"),
            session_id: format!("session-{node}"),
            channel_instance_id: instance.to_string(),
            profile_id: "guidance-peer".to_string(),
            message_schema: "guidance/v1".to_string(),
            sequence,
            received_at: TimestampMs::new(sequence),
            expires_at: TimestampMs::new(sequence + 100),
            ready,
        }
    };
    runtime
        .observe_peer_channel_readiness(readiness("dog", "dog-a", "channel-1", 1, true))
        .expect("first endpoint confirms");
    assert_eq!(
        runtime.coordination_readiness(&group_id, &context_id),
        Some(CoordinationReadiness::WaitingForPeerChannel)
    );
    runtime
        .observe_peer_channel_readiness(readiness("cane", "cane-a", "channel-1", 1, true))
        .expect("second endpoint confirms");
    assert_eq!(
        runtime.peer_channels(&group_id)[0].lifecycle(),
        PeerChannelLifecycle::Ready
    );
    let mut wrong_profile = readiness("cane", "cane-a", "channel-1", 2, true);
    wrong_profile.profile_id = "different-profile".to_string();
    runtime
        .observe_peer_channel_readiness(wrong_profile)
        .expect("descriptor conflict becomes a channel fence");
    runtime
        .observe_peer_channel_readiness(readiness("dog", "dog-a", "channel-1", 2, true))
        .expect("unaffected endpoint renews");
    assert_eq!(
        runtime.peer_channels(&group_id)[0].lifecycle(),
        PeerChannelLifecycle::Fenced
    );
    runtime
        .observe_peer_channel_readiness(readiness("cane", "cane-a", "channel-1", 3, true))
        .expect("conflicting endpoint reproves the declared descriptor");
    assert_eq!(
        runtime.peer_channels(&group_id)[0].lifecycle(),
        PeerChannelLifecycle::Ready
    );
    runtime.fence_peer_channels_for_node(&NodeId::new("dog-a").expect("node id valid"));
    assert_eq!(
        runtime.peer_channels(&group_id)[0].lifecycle(),
        PeerChannelLifecycle::Fenced
    );
    runtime
        .observe_peer_channel_readiness(readiness("cane", "cane-a", "channel-1", 4, true))
        .expect("connected endpoint renews");
    assert_eq!(
        runtime.peer_channels(&group_id)[0].lifecycle(),
        PeerChannelLifecycle::Fenced
    );
    runtime
        .observe_peer_channel_readiness(readiness("dog", "dog-a", "channel-1", 4, true))
        .expect("lost endpoint confirms through a new route");
    assert_eq!(
        runtime.peer_channels(&group_id)[0].lifecycle(),
        PeerChannelLifecycle::Ready
    );
    runtime.refresh_peer_channel_deadlines(TimestampMs::new(104));
    assert_eq!(
        runtime.peer_channels(&group_id)[0].lifecycle(),
        PeerChannelLifecycle::Fenced
    );
    runtime
        .observe_peer_channel_readiness(readiness("dog", "dog-a", "channel-1", 104, true))
        .expect("first endpoint renews");
    runtime
        .observe_peer_channel_readiness(readiness("cane", "cane-a", "channel-1", 104, true))
        .expect("second endpoint renews");
    assert_eq!(
        runtime.peer_channels(&group_id)[0].lifecycle(),
        PeerChannelLifecycle::Ready
    );
    runtime
        .observe_peer_channel_readiness(readiness("cane", "cane-a", "channel-2", 105, true))
        .expect("new instance is retained as a conflict fence");
    assert_eq!(
        runtime.peer_channels(&group_id)[0].lifecycle(),
        PeerChannelLifecycle::Fenced
    );
}
