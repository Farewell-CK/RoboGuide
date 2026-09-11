use super::*;
use control::ControlError;
use domain::{EventPayload, NodeEvent, RoleAssignment, TaskRef};

/// Builds a canonical no-parameter intent for command-routing assertions.
fn test_intent(namespace: &str, name: &str) -> ExecutionIntent {
    ExecutionIntent::new(
        CapabilityContractRef::new(namespace, name, "v1").expect("test operation must be valid"),
        BTreeMap::new(),
    )
    .expect("test intent must be valid")
}

/// Builds a two-role task used to exercise concurrent mission isolation.
fn multi_mission_requirement(mission: &str, task: &str) -> TaskRequirement {
    TaskRequirement::new(
        MissionId::new(mission).expect("mission identifier should be valid"),
        TaskId::new(task).expect("task identifier should be valid"),
        vec![
            RoleRequirement::new(
                RoleId::new("transport").expect("role identifier should be valid"),
                CapabilityKind::Transport,
                Some(ResourceKind::Space),
            ),
            RoleRequirement::new(
                RoleId::new("compute").expect("role identifier should be valid"),
                CapabilityKind::Compute,
                Some(ResourceKind::Compute),
            ),
        ],
    )
    .expect("test requirement should be valid")
}

/// Wraps a concurrent-test requirement in a complete one-Task MissionPlan.
fn single_task_mission_plan(requirement: TaskRequirement) -> MissionPlan {
    let mission_id = requirement.mission_id().clone();
    let intents = requirement
        .roles()
        .iter()
        .map(|role| {
            let intent = match role.capability() {
                Some(CapabilityKind::Compute) => test_intent("compute", "infer"),
                _ => test_intent("mobility", "move"),
            };
            (role.role_id().clone(), intent)
        })
        .collect();
    let context_id = CoordinationContextId::new(format!("context-{mission_id}"))
        .expect("context identifier should be valid");
    let task = PlannedTask::new(
        "exercise concurrent Mission isolation",
        requirement,
        intents,
        Vec::new(),
        TaskContinuity::new(context_id.clone(), BTreeMap::new(), BTreeMap::new()),
    )
    .expect("test Task should be valid");
    MissionPlan::new(
        MissionGoal::new(mission_id.clone(), "exercise concurrent Mission isolation")
            .expect("test Mission goal should be valid"),
        TaskGraph::new(mission_id, vec![task]).expect("test Task Graph should be valid"),
        vec![
            CoordinationContext::new(context_id, Vec::new()).expect("test Context should be valid"),
        ],
    )
    .expect("test MissionPlan should be valid")
}

/// Extracts the mission-scoped task identity carried by a task-level event.
fn event_task_ref(payload: &EventPayload) -> Option<&TaskRef> {
    match payload {
        EventPayload::CandidatesMatched { task_ref }
        | EventPayload::TaskSchedulingSelected { task_ref, .. }
        | EventPayload::TaskSchedulingDeferred { task_ref, .. }
        | EventPayload::SchedulingReservationCreated { task_ref, .. }
        | EventPayload::SchedulingReservationActivated { task_ref, .. }
        | EventPayload::SchedulingReservationInvalidated { task_ref, .. }
        | EventPayload::SchedulingReservationReleased { task_ref, .. }
        | EventPayload::ProposalCreated { task_ref }
        | EventPayload::PlanCommitted { task_ref }
        | EventPayload::ExecutionGroupBound { task_ref, .. }
        | EventPayload::TaskExecutionRegistered { task_ref, .. }
        | EventPayload::TaskExecutionActivated { task_ref, .. }
        | EventPayload::TaskExecutionReady { task_ref, .. }
        | EventPayload::TaskExecutionCompleted { task_ref, .. }
        | EventPayload::TaskSatisfied { task_ref, .. }
        | EventPayload::TaskExecutionFailed { task_ref, .. }
        | EventPayload::TaskExecutionBindingsReleased { task_ref, .. }
        | EventPayload::MissionActorBound { task_ref, .. }
        | EventPayload::ExecutionGroupActivated { task_ref, .. }
        | EventPayload::ReconciliationRoleRecoveryRequired { task_ref, .. }
        | EventPayload::RecoveryCandidatesMatched { task_ref, .. }
        | EventPayload::RecoverySchedulingSelected { task_ref, .. }
        | EventPayload::RecoverySchedulingNoSelection { task_ref, .. }
        | EventPayload::RecoveryAssignmentProposed { task_ref, .. }
        | EventPayload::RecoveryAssignmentCommitted { task_ref, .. }
        | EventPayload::RecoveryAssignmentAborted { task_ref, .. }
        | EventPayload::RecoveryRebound { task_ref, .. }
        | EventPayload::ExecutionGroupCompleted { task_ref, .. }
        | EventPayload::ExecutionGroupBlocked { task_ref, .. }
        | EventPayload::ExecutionGroupRoleBindingReleased { task_ref, .. }
        | EventPayload::ExecutionGroupFailed { task_ref, .. }
        | EventPayload::ExecutionGroupReleased { task_ref, .. }
        | EventPayload::NodeObservation(NodeEvent::TaskCompleted { task_ref, .. })
        | EventPayload::NodeObservation(NodeEvent::TaskFailed { task_ref, .. }) => Some(task_ref),
        EventPayload::RuntimeExecutionRecoveryRequired { task_ref, .. } => task_ref.as_ref(),
        EventPayload::ExecutionRelationReconciliationRequired {
            target_task_ref, ..
        } => Some(target_task_ref),
        EventPayload::MapLocalizationEvidenceRecorded { evidence } => Some(evidence.task_ref()),
        EventPayload::MapArtifactDeclared { .. }
        | EventPayload::StateRecordObserved { .. }
        | EventPayload::MemoryManifestPublished { .. }
        | EventPayload::MemoryArtifactStaged { .. }
        | EventPayload::MemoryArtifactImported { .. }
        | EventPayload::MemoryArtifactRejected { .. }
        | EventPayload::MapArtifactPublished { .. }
        | EventPayload::MapArtifactStaged { .. }
        | EventPayload::MapArtifactImported { .. }
        | EventPayload::MapLocalizationVerified { .. }
        | EventPayload::MapArtifactRejected { .. }
        | EventPayload::NodeRegistered { .. }
        | EventPayload::NodeHeartbeatAccepted { .. }
        | EventPayload::NodeLeaseExpired { .. }
        | EventPayload::ExecutionGroupCreated { .. }
        | EventPayload::ExecutionRelationRegistered { .. }
        | EventPayload::ExecutionRelationStateChanged { .. }
        | EventPayload::PeerChannelReadinessObserved { .. }
        | EventPayload::ContextBindingsReleased { .. }
        | EventPayload::NodeObservation(NodeEvent::SafeStopped { .. }) => None,
    }
}

/// The first vertical slice preserves completed work and fences implicit Actor migration.
#[test]
fn mvp_slice_blocks_when_actor_authority_node_fails() {
    let events = super::run_mvp_slice().expect("deterministic MVP slice should pass");
    assert_eq!(events.len(), 21);
    assert!(matches!(
        events[0].payload(),
        EventPayload::NodeRegistered { .. }
    ));
    assert!(matches!(
        events[1].payload(),
        EventPayload::NodeRegistered { .. }
    ));
    assert!(matches!(
        events[2].payload(),
        EventPayload::NodeRegistered { .. }
    ));
    assert!(matches!(
        events[3].payload(),
        EventPayload::ExecutionGroupCreated { .. }
    ));
    assert!(matches!(
        events[4].payload(),
        EventPayload::TaskExecutionRegistered { .. }
    ));
    assert!(matches!(
        events[5].payload(),
        EventPayload::TaskExecutionReady { .. }
    ));
    assert!(matches!(
        events[6].payload(),
        EventPayload::CandidatesMatched { .. }
    ));
    assert!(matches!(
        events[7].payload(),
        EventPayload::TaskSchedulingSelected { .. }
    ));
    assert!(matches!(
        events[8].payload(),
        EventPayload::ProposalCreated { .. }
    ));
    assert!(matches!(
        events[9].payload(),
        EventPayload::PlanCommitted { .. }
    ));
    assert!(matches!(
        events[10].payload(),
        EventPayload::ExecutionGroupBound { .. }
    ));
    assert!(matches!(
        events[11].payload(),
        EventPayload::MissionActorBound { .. }
    ));
    assert!(matches!(
        events[12].payload(),
        EventPayload::MissionActorBound { .. }
    ));
    assert!(matches!(
        events[13].payload(),
        EventPayload::TaskExecutionActivated { .. }
    ));
    assert!(matches!(
        events[14].payload(),
        EventPayload::NodeObservation(domain::NodeEvent::TaskCompleted { .. })
    ));
    assert!(matches!(
        events[15].payload(),
        EventPayload::NodeObservation(domain::NodeEvent::TaskFailed { node_id, .. })
            if node_id.as_str() == "node-a"
    ));
    assert!(matches!(
        events[16].payload(),
        EventPayload::ReconciliationRoleRecoveryRequired { role_id, node_id, .. }
            if role_id.as_str() == "primary-transport" && node_id.as_str() == "node-a"
    ));
    assert!(matches!(
        events[17].payload(),
        EventPayload::ExecutionGroupBlocked { .. }
    ));
    assert!(matches!(
        events[18].payload(),
        EventPayload::ExecutionGroupRoleBindingReleased { role_id, .. }
            if role_id.as_str() == "primary-transport"
    ));
    assert!(matches!(
        events[19].payload(),
        EventPayload::RecoveryCandidatesMatched { candidate_node_ids, .. }
            if candidate_node_ids.is_empty()
    ));
    assert!(matches!(
        events[20].payload(),
        EventPayload::RecoverySchedulingNoSelection { .. }
    ));
    assert!(!events.iter().any(|event| matches!(
        event.payload(),
        EventPayload::RecoveryRebound { .. }
            | EventPayload::ExecutionGroupCompleted { .. }
            | EventPayload::ExecutionGroupReleased { .. }
    )));
}

/// Runtime health ingestion immediately changes the next Control decision.
#[test]
fn runtime_health_observation_changes_control_matching() {
    let timestamp = TimestampMs::new(0);
    let observed_offline_at = TimestampMs::new(10);
    let correlation_id =
        CorrelationId::new("runtime-state-trace").expect("correlation id should be valid");
    let node = build_registration(
        "node-observed",
        "vendor-runtime",
        vec![Capability::new(CapabilityKind::Transport, true)],
        vec![
            Resource::new(
                ResourceId::new("space-observed").expect("resource id should be valid"),
                ResourceKind::Space,
                1,
            )
            .expect("resource should be valid"),
        ],
    )
    .expect("node registration should be valid");
    let node_id = node.node_id().clone();
    let requirement = TaskRequirement::new(
        MissionId::new("mission-observation").expect("mission id should be valid"),
        TaskId::new("task-01").expect("task id should be valid"),
        vec![RoleRequirement::new(
            RoleId::new("transport").expect("role id should be valid"),
            CapabilityKind::Transport,
            Some(ResourceKind::Space),
        )],
    )
    .expect("task requirement should be valid");
    let mut control = ControlPlane::new();
    let mut state = InMemorySharedNodeState::new();
    let mut log = SharedEventLog::new();
    control
        .register_node(
            &mut state,
            node.clone(),
            NodeStatus::new(NodeHealth::Online, timestamp),
            timestamp,
            &correlation_id,
            &mut log,
        )
        .expect("node admission should succeed");
    control
        .match_capabilities(&state, &requirement, timestamp, &correlation_id, &mut log)
        .expect("initial online observation should be eligible");

    let mut runtime = Runtime::new(VirtualClock::new(observed_offline_at), log.clone());
    runtime
        .register_node(Box::new(FakeNode::new(node).with_status(NodeStatus::new(
            NodeHealth::Offline,
            observed_offline_at,
        ))))
        .expect("fake EAIOS adapter registration should succeed");
    runtime
        .observe_node_status(&node_id, &mut state)
        .expect("Runtime should ingest local health");

    assert!(matches!(
        control.match_capabilities(
            &state,
            &requirement,
            observed_offline_at,
            &correlation_id,
            &mut log,
        ),
        Err(ControlError::NoCandidate(role_id)) if role_id.as_str() == "transport"
    ));
}

/// Independent source clock values do not affect Control receive-time freshness.
#[test]
fn runtime_source_clock_does_not_affect_control_freshness() {
    let admitted_at = TimestampMs::new(0);
    let runtime_received_at = TimestampMs::new(10);
    let correlation_id =
        CorrelationId::new("clock-domain-trace").expect("correlation id should be valid");
    let node = build_registration(
        "node-clock-domain",
        "vendor-runtime",
        vec![Capability::new(CapabilityKind::Transport, true)],
        vec![
            Resource::new(
                ResourceId::new("space-clock-domain").expect("resource id should be valid"),
                ResourceKind::Space,
                1,
            )
            .expect("resource should be valid"),
        ],
    )
    .expect("node registration should be valid");
    let node_id = node.node_id().clone();
    let requirement = TaskRequirement::new(
        MissionId::new("mission-clock-domain").expect("mission id should be valid"),
        TaskId::new("task-01").expect("task id should be valid"),
        vec![RoleRequirement::new(
            RoleId::new("transport").expect("role id should be valid"),
            CapabilityKind::Transport,
            Some(ResourceKind::Space),
        )],
    )
    .expect("task requirement should be valid");
    let mut control = ControlPlane::with_status_ttl(20);
    let mut state = InMemorySharedNodeState::new();
    let mut log = SharedEventLog::new();
    control
        .register_node(
            &mut state,
            node.clone(),
            NodeStatus::new(NodeHealth::Online, TimestampMs::new(1)),
            admitted_at,
            &correlation_id,
            &mut log,
        )
        .expect("node admission should succeed");
    let mut runtime = Runtime::new(VirtualClock::new(runtime_received_at), log.clone());
    runtime
        .register_node(Box::new(FakeNode::new(node).with_status(NodeStatus::new(
            NodeHealth::Online,
            TimestampMs::new(500_000),
        ))))
        .expect("fake EAIOS adapter registration should succeed");
    runtime
        .observe_node_status(&node_id, &mut state)
        .expect("Runtime should record source and receive times separately");

    control
        .match_capabilities(
            &state,
            &requirement,
            TimestampMs::new(20),
            &correlation_id,
            &mut log,
        )
        .expect("receive time age 10 should remain eligible");
}

mod multi_mission;
