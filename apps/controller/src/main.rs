#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! Executable evidence for the first DEAIOS Node Contract vertical slice.

use control::{ControlPlane, GroupLifecycle, RoleRequirementView};
use domain::{
    Capability, CapabilityKind, CorrelationId, EventRecord, ExecutionCommand, ExecutionGroupId,
    LocalRuntime, MISSION_PLAN_SCHEMA_V0, MissionGoal, MissionId, MissionPlan, NodeHealth, NodeId,
    NodeRegistration, NodeStatus, PlannedTask, Resource, ResourceId, ResourceKind, RoleAssignment,
    RoleId, RoleRequirement, TaskGraph, TaskId, TaskRequirement, TimestampMs,
};
use runtime::Runtime;
use serde::Deserialize;
use state::InMemorySharedNodeState;
use std::collections::BTreeSet;
use testkit::{FailureMode, FakeNode, SharedEventLog, VirtualClock};

/// JSON adapter document for one versioned Mission Plan artifact.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct MissionPlanDocument {
    /// Contract version that controls adapter conversion.
    schema_version: String,
    /// User-visible mission identity and objective.
    mission: MissionDocument,
    /// Task Graph nodes in planner declaration order.
    tasks: Vec<TaskDocument>,
}

/// JSON adapter document for the mission goal.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct MissionDocument {
    /// Stable mission identity.
    id: String,
    /// User-visible objective that planning must preserve.
    objective: String,
}

/// JSON adapter document for one Task Graph node.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct TaskDocument {
    /// Stable task identity.
    id: String,
    /// Human-readable outcome of the task.
    description: String,
    /// Prerequisite task identities.
    depends_on: Vec<String>,
    /// Role-level execution requirements.
    roles: Vec<RoleDocument>,
}

/// JSON adapter document for one role requirement.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct RoleDocument {
    /// Stable role identity.
    id: String,
    /// Capability category required by the role.
    capability: CapabilityDocument,
    /// Optional shared resource category required by the role.
    resource_kind: Option<ResourceDocument>,
}

/// Capability categories accepted by Mission Plan v0.
#[derive(Debug, Clone, Copy, Deserialize)]
#[serde(rename_all = "lowercase")]
enum CapabilityDocument {
    /// Mobile navigation capability.
    Mobility,
    /// Payload transport capability.
    Transport,
    /// Compute execution capability.
    Compute,
    /// World or node observation capability.
    Observation,
}

/// Resource categories accepted by Mission Plan v0.
#[derive(Debug, Clone, Copy, Deserialize)]
#[serde(rename_all = "lowercase")]
enum ResourceDocument {
    /// Shared physical space.
    Space,
    /// Shared compute allocation.
    Compute,
    /// Shared time window.
    Time,
}

impl From<CapabilityDocument> for CapabilityKind {
    /// Converts the adapter enum into the transport-neutral domain enum.
    fn from(value: CapabilityDocument) -> Self {
        match value {
            CapabilityDocument::Mobility => Self::Mobility,
            CapabilityDocument::Transport => Self::Transport,
            CapabilityDocument::Compute => Self::Compute,
            CapabilityDocument::Observation => Self::Observation,
        }
    }
}

impl From<ResourceDocument> for ResourceKind {
    /// Converts the adapter enum into the transport-neutral domain enum.
    fn from(value: ResourceDocument) -> Self {
        match value {
            ResourceDocument::Space => Self::Space,
            ResourceDocument::Compute => Self::Compute,
            ResourceDocument::Time => Self::Time,
        }
    }
}

/// Runs the first deterministic normal-and-recovery vertical slice.
fn main() {
    match run_mvp_slice() {
        Ok(events) => println!("DEAIOS MVP slice completed with {} events", events.len()),
        Err(error) => {
            eprintln!("DEAIOS MVP slice failed: {error}");
            std::process::exit(1);
        }
    }
}

/// Builds a node registration while keeping identifier validation visible.
fn build_registration(
    node_name: &str,
    runtime_name: &str,
    capabilities: Vec<Capability>,
    resources: Vec<Resource>,
) -> Result<NodeRegistration, String> {
    let node_id = NodeId::new(node_name).map_err(|error| error.to_string())?;
    let runtime = LocalRuntime::new(runtime_name, "0.1.0").map_err(|error| error.to_string())?;
    Ok(NodeRegistration::new(
        node_id,
        runtime,
        capabilities,
        resources,
    ))
}

/// Loads the approved Mission Plan fixture and converts it into validated domain values.
fn load_mission_plan() -> Result<MissionPlan, String> {
    let source = include_str!("../../../scenarios/mvp-slice-v0.1/mission-plan.json");
    let document: MissionPlanDocument =
        serde_json::from_str(source).map_err(|error| error.to_string())?;
    if document.schema_version != MISSION_PLAN_SCHEMA_V0 {
        return Err(format!(
            "unsupported Mission Plan schema: {}",
            document.schema_version
        ));
    }
    let mission_id = MissionId::new(document.mission.id).map_err(|error| error.to_string())?;
    let goal = MissionGoal::new(mission_id.clone(), document.mission.objective)
        .map_err(|error| error.to_string())?;
    let tasks = document
        .tasks
        .into_iter()
        .map(|task| {
            let task_id = TaskId::new(task.id).map_err(|error| error.to_string())?;
            let roles = task
                .roles
                .into_iter()
                .map(|role| {
                    Ok(RoleRequirement::new(
                        RoleId::new(role.id).map_err(|error| error.to_string())?,
                        role.capability.into(),
                        role.resource_kind.map(Into::into),
                    ))
                })
                .collect::<Result<Vec<_>, String>>()?;
            let requirement = TaskRequirement::new(mission_id.clone(), task_id, roles)
                .map_err(|error| error.to_string())?;
            let dependencies = task
                .depends_on
                .into_iter()
                .map(|dependency| TaskId::new(dependency).map_err(|error| error.to_string()))
                .collect::<Result<Vec<_>, String>>()?;
            PlannedTask::new(task.description, requirement, dependencies)
                .map_err(|error| error.to_string())
        })
        .collect::<Result<Vec<_>, String>>()?;
    let task_graph = TaskGraph::new(mission_id, tasks).map_err(|error| error.to_string())?;
    MissionPlan::new(goal, task_graph).map_err(|error| error.to_string())
}

/// Finds the unique role requiring one capability in the selected task.
fn role_for_capability(
    requirement: &TaskRequirement,
    capability: CapabilityKind,
) -> Result<RoleId, String> {
    let matching_roles = requirement
        .roles()
        .iter()
        .filter(|role| role.capability() == capability)
        .collect::<Vec<_>>();
    match matching_roles.as_slice() {
        [role] => Ok(role.role_id().clone()),
        [] => Err(format!("task has no {capability:?} role")),
        _ => Err(format!("task has multiple {capability:?} roles")),
    }
}

/// Executes registration, proposal, commit, failure, rebind, and completion.
fn run_mvp_slice() -> Result<Vec<EventRecord>, String> {
    let mission_plan = load_mission_plan()?;
    let ready_tasks = mission_plan.task_graph().ready_tasks(&BTreeSet::new());
    let [planned_task] = ready_tasks.as_slice() else {
        return Err(format!(
            "MVP fixture must contain exactly one initially ready task, found {}",
            ready_tasks.len()
        ));
    };
    let requirement = planned_task.requirement().clone();
    let mission_id = mission_plan.goal().mission_id().clone();
    let task_id = requirement.task_id().clone();
    let correlation_id = CorrelationId::new("trace-mvp-001").map_err(|error| error.to_string())?;
    let transport_role = role_for_capability(&requirement, CapabilityKind::Transport)?;
    let compute_role = role_for_capability(&requirement, CapabilityKind::Compute)?;
    let group_id = ExecutionGroupId::new("group-mvp-001").map_err(|error| error.to_string())?;

    let a_space = ResourceId::new("corridor-a").map_err(|error| error.to_string())?;
    let a_compute = ResourceId::new("compute-a").map_err(|error| error.to_string())?;
    let b_space = ResourceId::new("corridor-b").map_err(|error| error.to_string())?;
    let edge_compute = ResourceId::new("edge-gpu-0").map_err(|error| error.to_string())?;

    let node_a = build_registration(
        "node-a",
        "eaios-fake-a",
        vec![
            Capability::new(CapabilityKind::Transport, true),
            Capability::new(CapabilityKind::Compute, true),
        ],
        vec![
            Resource::new(a_space.clone(), ResourceKind::Space, 1)
                .map_err(|error| error.to_string())?,
            Resource::new(a_compute, ResourceKind::Compute, 1)
                .map_err(|error| error.to_string())?,
        ],
    )?;
    let node_b = build_registration(
        "node-b",
        "eaios-fake-b",
        vec![Capability::new(CapabilityKind::Transport, true)],
        vec![
            Resource::new(b_space.clone(), ResourceKind::Space, 1)
                .map_err(|error| error.to_string())?,
        ],
    )?;
    let edge = build_registration(
        "edge-gpu",
        "edge-agent",
        vec![Capability::new(CapabilityKind::Compute, true)],
        vec![
            Resource::new(edge_compute.clone(), ResourceKind::Compute, 1)
                .map_err(|error| error.to_string())?,
        ],
    )?;

    let mut control = ControlPlane::new();
    let mut state = InMemorySharedNodeState::new();
    let mut log = SharedEventLog::new();
    let timestamp = TimestampMs::new(0);
    control
        .register_node(
            &mut state,
            node_a.clone(),
            NodeStatus::new(NodeHealth::Online, timestamp),
            timestamp,
            &correlation_id,
            &mut log,
        )
        .map_err(|error| error.to_string())?;
    control
        .register_node(
            &mut state,
            node_b.clone(),
            NodeStatus::new(NodeHealth::Online, timestamp),
            timestamp,
            &correlation_id,
            &mut log,
        )
        .map_err(|error| error.to_string())?;
    control
        .register_node(
            &mut state,
            edge.clone(),
            NodeStatus::new(NodeHealth::Online, timestamp),
            timestamp,
            &correlation_id,
            &mut log,
        )
        .map_err(|error| error.to_string())?;

    let candidates = control
        .match_capabilities(&state, &requirement, timestamp, &correlation_id, &mut log)
        .map_err(|error| error.to_string())?;
    let proposal = control
        .propose(
            &state,
            &requirement,
            &candidates,
            vec![
                RoleAssignment::new(
                    transport_role.clone(),
                    node_a.node_id().clone(),
                    vec![a_space],
                ),
                RoleAssignment::new(
                    compute_role.clone(),
                    edge.node_id().clone(),
                    vec![edge_compute],
                ),
            ],
            timestamp,
            &correlation_id,
            &mut log,
        )
        .map_err(|error| error.to_string())?;
    let plan = control
        .commit(&proposal, timestamp, &correlation_id, &mut log)
        .map_err(|error| error.to_string())?;
    control
        .create_group(
            group_id.clone(),
            &plan,
            timestamp,
            &correlation_id,
            &mut log,
        )
        .map_err(|error| error.to_string())?;
    control
        .activate_group(&group_id, timestamp, &correlation_id, &mut log)
        .map_err(|error| error.to_string())?;

    let mut runtime = Runtime::new(VirtualClock::new(timestamp), log.clone());
    runtime
        .register_node(Box::new(FakeNode::new(node_a).with_failure_mode(
            FailureMode::FailNext {
                reason: "onboard execution capability degraded".to_string(),
            },
        )))
        .map_err(|error| error.to_string())?;
    runtime
        .register_node(Box::new(FakeNode::new(node_b.clone())))
        .map_err(|error| error.to_string())?;
    runtime
        .register_node(Box::new(FakeNode::new(edge)))
        .map_err(|error| error.to_string())?;
    runtime
        .observe_node_status(
            &NodeId::new("node-a").map_err(|error| error.to_string())?,
            &mut state,
        )
        .map_err(|error| error.to_string())?;

    let compute_command = ExecutionCommand::new(
        mission_id.clone(),
        task_id.clone(),
        group_id.clone(),
        compute_role,
        NodeId::new("edge-gpu").map_err(|error| error.to_string())?,
        correlation_id.clone(),
    );
    runtime
        .execute(&compute_command)
        .map_err(|error| error.to_string())?;

    let transport_command = ExecutionCommand::new(
        mission_id.clone(),
        task_id.clone(),
        group_id.clone(),
        transport_role.clone(),
        NodeId::new("node-a").map_err(|error| error.to_string())?,
        correlation_id.clone(),
    );
    let failure = runtime
        .execute(&transport_command)
        .map_err(|error| error.to_string())?;
    if !matches!(failure, domain::NodeEvent::TaskFailed { .. }) {
        return Err("failure injection did not produce a task failure".to_string());
    }

    control
        .block_group(
            &group_id,
            "transport role cannot progress on node-a",
            TimestampMs::new(1),
            &correlation_id,
            &mut log,
        )
        .map_err(|error| error.to_string())?;
    control
        .release_role_binding(
            &group_id,
            &transport_role,
            TimestampMs::new(1),
            &correlation_id,
            &mut log,
        )
        .map_err(|error| error.to_string())?;
    control
        .rebind_role(
            &state,
            &group_id,
            &RoleRequirementView::new(RoleRequirement::new(
                transport_role.clone(),
                CapabilityKind::Transport,
                Some(ResourceKind::Space),
            )),
            node_b.node_id().clone(),
            vec![b_space],
            TimestampMs::new(1),
            &correlation_id,
            &mut log,
        )
        .map_err(|error| error.to_string())?;
    control
        .activate_group(&group_id, TimestampMs::new(1), &correlation_id, &mut log)
        .map_err(|error| error.to_string())?;

    let replacement_command = ExecutionCommand::new(
        mission_id,
        task_id,
        group_id.clone(),
        transport_role,
        NodeId::new("node-b").map_err(|error| error.to_string())?,
        correlation_id.clone(),
    );
    runtime
        .execute(&replacement_command)
        .map_err(|error| error.to_string())?;
    control
        .complete_group(&group_id, TimestampMs::new(2), &correlation_id, &mut log)
        .map_err(|error| error.to_string())?;

    if control.group(&group_id).map(|group| group.lifecycle()) != Some(GroupLifecycle::Completed) {
        return Err("execution group did not reach Completed".to_string());
    }
    control
        .release_group(&group_id, TimestampMs::new(3), &correlation_id, &mut log)
        .map_err(|error| error.to_string())?;
    if control.group(&group_id).map(|group| group.lifecycle()) != Some(GroupLifecycle::Released) {
        return Err("execution group did not reach Released".to_string());
    }
    Ok(log.snapshot())
}

#[cfg(test)]
mod tests {
    use super::*;
    use control::ControlError;
    use domain::{EventPayload, NodeEvent, TaskRef};

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

    /// Extracts the mission-scoped task identity carried by a task-level event.
    fn event_task_ref(payload: &EventPayload) -> Option<&TaskRef> {
        match payload {
            EventPayload::CandidatesMatched { task_ref }
            | EventPayload::ProposalCreated { task_ref }
            | EventPayload::PlanCommitted { task_ref }
            | EventPayload::ExecutionGroupBound { task_ref, .. }
            | EventPayload::ExecutionGroupActivated { task_ref, .. }
            | EventPayload::RecoveryRebound { task_ref, .. }
            | EventPayload::ExecutionGroupCompleted { task_ref, .. }
            | EventPayload::ExecutionGroupBlocked { task_ref, .. }
            | EventPayload::ExecutionGroupRoleBindingReleased { task_ref, .. }
            | EventPayload::ExecutionGroupFailed { task_ref, .. }
            | EventPayload::ExecutionGroupReleased { task_ref, .. }
            | EventPayload::NodeObservation(NodeEvent::TaskCompleted { task_ref, .. })
            | EventPayload::NodeObservation(NodeEvent::TaskFailed { task_ref, .. }) => {
                Some(task_ref)
            }
            EventPayload::NodeRegistered { .. }
            | EventPayload::NodeHeartbeatAccepted { .. }
            | EventPayload::NodeLeaseExpired { .. }
            | EventPayload::NodeObservation(NodeEvent::SafeStopped { .. }) => None,
        }
    }

    /// The first vertical slice must preserve completed work and recover by rebinding.
    #[test]
    fn mvp_slice_recovers_after_node_failure() {
        let events = super::run_mvp_slice().expect("deterministic MVP slice should pass");
        assert_eq!(events.len(), 17);
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
            EventPayload::CandidatesMatched { .. }
        ));
        assert!(matches!(
            events[4].payload(),
            EventPayload::ProposalCreated { .. }
        ));
        assert!(matches!(
            events[5].payload(),
            EventPayload::PlanCommitted { .. }
        ));
        assert!(matches!(
            events[6].payload(),
            EventPayload::ExecutionGroupBound { .. }
        ));
        assert!(matches!(
            events[7].payload(),
            EventPayload::ExecutionGroupActivated { .. }
        ));
        assert!(matches!(
            events[8].payload(),
            EventPayload::NodeObservation(domain::NodeEvent::TaskCompleted { .. })
        ));
        assert!(matches!(
            events[9].payload(),
            EventPayload::NodeObservation(domain::NodeEvent::TaskFailed { node_id, .. })
                if node_id.as_str() == "node-a"
        ));
        assert!(matches!(
            events[10].payload(),
            EventPayload::ExecutionGroupBlocked { .. }
        ));
        assert!(matches!(
            events[11].payload(),
            EventPayload::ExecutionGroupRoleBindingReleased { role_id, .. }
                if role_id.as_str() == "primary-transport"
        ));
        assert!(matches!(
            events[12].payload(),
            EventPayload::RecoveryRebound { from_node, to_node, .. }
                if from_node.as_str() == "node-a" && to_node.as_str() == "node-b"
        ));
        assert!(matches!(
            events[13].payload(),
            EventPayload::ExecutionGroupActivated { .. }
        ));
        assert!(matches!(
            events[14].payload(),
            EventPayload::NodeObservation(domain::NodeEvent::TaskCompleted { node_id, .. })
                if node_id.as_str() == "node-b"
        ));
        assert!(matches!(
            events[15].payload(),
            EventPayload::ExecutionGroupCompleted { .. }
        ));
        assert!(matches!(
            events[16].payload(),
            EventPayload::ExecutionGroupReleased { .. }
        ));
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

    /// Concurrent missions must isolate recovery, lifecycle, resources, and traces.
    #[test]
    fn concurrent_missions_rebind_and_release_independently() {
        let started_at = TimestampMs::new(0);
        let setup_trace =
            CorrelationId::new("trace-setup").expect("correlation identifier should be valid");
        let trace_a =
            CorrelationId::new("trace-mission-a").expect("correlation identifier should be valid");
        let trace_b =
            CorrelationId::new("trace-mission-b").expect("correlation identifier should be valid");
        let trace_c =
            CorrelationId::new("trace-mission-c").expect("correlation identifier should be valid");
        let group_a = ExecutionGroupId::new("group-a").expect("group identifier should be valid");
        let group_b = ExecutionGroupId::new("group-b").expect("group identifier should be valid");
        let requirement_a = multi_mission_requirement("mission-a", "task-01");
        let requirement_b = multi_mission_requirement("mission-b", "task-01");
        let task_ref_a = requirement_a.task_ref().clone();
        let task_ref_b = requirement_b.task_ref().clone();
        let transport_role = RoleId::new("transport").expect("role identifier should be valid");
        let compute_role = RoleId::new("compute").expect("role identifier should be valid");

        let space_a = ResourceId::new("space-a").expect("resource identifier should be valid");
        let space_b = ResourceId::new("space-b").expect("resource identifier should be valid");
        let space_d = ResourceId::new("space-d").expect("resource identifier should be valid");
        let compute_c = ResourceId::new("compute-c").expect("resource identifier should be valid");
        let compute_e = ResourceId::new("compute-e").expect("resource identifier should be valid");

        let node_a = build_registration(
            "node-a",
            "vendor-runtime-a",
            vec![Capability::new(CapabilityKind::Transport, true)],
            vec![
                Resource::new(space_a.clone(), ResourceKind::Space, 1)
                    .expect("resource should be valid"),
            ],
        )
        .expect("node registration should be valid");
        let node_b = build_registration(
            "node-b",
            "vendor-runtime-b",
            vec![Capability::new(CapabilityKind::Transport, true)],
            vec![
                Resource::new(space_b.clone(), ResourceKind::Space, 1)
                    .expect("resource should be valid"),
            ],
        )
        .expect("node registration should be valid");
        let node_d = build_registration(
            "node-d",
            "vendor-runtime-d",
            vec![Capability::new(CapabilityKind::Transport, true)],
            vec![
                Resource::new(space_d.clone(), ResourceKind::Space, 1)
                    .expect("resource should be valid"),
            ],
        )
        .expect("node registration should be valid");
        let edge_c = build_registration(
            "edge-c",
            "vendor-runtime-c",
            vec![Capability::new(CapabilityKind::Compute, true)],
            vec![
                Resource::new(compute_c.clone(), ResourceKind::Compute, 1)
                    .expect("resource should be valid"),
            ],
        )
        .expect("node registration should be valid");
        let edge_e = build_registration(
            "edge-e",
            "vendor-runtime-e",
            vec![Capability::new(CapabilityKind::Compute, true)],
            vec![
                Resource::new(compute_e.clone(), ResourceKind::Compute, 1)
                    .expect("resource should be valid"),
            ],
        )
        .expect("node registration should be valid");

        let mut control = ControlPlane::new();
        let mut state = InMemorySharedNodeState::new();
        let mut log = SharedEventLog::new();
        for registration in [&node_a, &node_b, &node_d, &edge_c, &edge_e] {
            control
                .register_node(
                    &mut state,
                    registration.clone(),
                    NodeStatus::new(NodeHealth::Online, started_at),
                    started_at,
                    &setup_trace,
                    &mut log,
                )
                .expect("node registration should succeed");
        }

        let candidates_a = control
            .match_capabilities(&state, &requirement_a, started_at, &trace_a, &mut log)
            .expect("Mission A matching should succeed");
        let proposal_a = control
            .propose(
                &state,
                &requirement_a,
                &candidates_a,
                vec![
                    RoleAssignment::new(
                        transport_role.clone(),
                        node_a.node_id().clone(),
                        vec![space_a],
                    ),
                    RoleAssignment::new(
                        compute_role.clone(),
                        edge_c.node_id().clone(),
                        vec![compute_c.clone()],
                    ),
                ],
                started_at,
                &trace_a,
                &mut log,
            )
            .expect("Mission A proposal should succeed");
        let plan_a = control
            .commit(&proposal_a, started_at, &trace_a, &mut log)
            .expect("Mission A commit should succeed");
        control
            .create_group(group_a.clone(), &plan_a, started_at, &trace_a, &mut log)
            .expect("Mission A group creation should succeed");
        control
            .activate_group(&group_a, started_at, &trace_a, &mut log)
            .expect("Mission A activation should succeed");

        let candidates_b = control
            .match_capabilities(&state, &requirement_b, started_at, &trace_b, &mut log)
            .expect("Mission B matching should succeed");
        let proposal_b = control
            .propose(
                &state,
                &requirement_b,
                &candidates_b,
                vec![
                    RoleAssignment::new(
                        transport_role.clone(),
                        node_d.node_id().clone(),
                        vec![space_d],
                    ),
                    RoleAssignment::new(
                        compute_role.clone(),
                        edge_e.node_id().clone(),
                        vec![compute_e],
                    ),
                ],
                started_at,
                &trace_b,
                &mut log,
            )
            .expect("Mission B proposal should succeed");
        let plan_b = control
            .commit(&proposal_b, started_at, &trace_b, &mut log)
            .expect("Mission B commit should succeed");
        control
            .create_group(group_b.clone(), &plan_b, started_at, &trace_b, &mut log)
            .expect("Mission B group creation should succeed");
        control
            .activate_group(&group_b, started_at, &trace_b, &mut log)
            .expect("Mission B activation should succeed");
        let group_b_bindings = control
            .group(&group_b)
            .expect("Mission B group should exist")
            .assignments()
            .to_vec();

        let mut runtime = Runtime::new(VirtualClock::new(started_at), log.clone());
        runtime
            .register_node(Box::new(FakeNode::new(node_a.clone()).with_failure_mode(
                FailureMode::FailNext {
                    reason: "transport unavailable".to_string(),
                },
            )))
            .expect("Node A runtime registration should succeed");
        for registration in [
            node_b.clone(),
            node_d.clone(),
            edge_c.clone(),
            edge_e.clone(),
        ] {
            runtime
                .register_node(Box::new(FakeNode::new(registration)))
                .expect("runtime registration should succeed");
        }

        runtime
            .execute(&ExecutionCommand::new(
                requirement_a.mission_id().clone(),
                requirement_a.task_id().clone(),
                group_a.clone(),
                compute_role.clone(),
                edge_c.node_id().clone(),
                trace_a.clone(),
            ))
            .expect("Mission A compute should complete");
        runtime
            .execute(&ExecutionCommand::new(
                requirement_b.mission_id().clone(),
                requirement_b.task_id().clone(),
                group_b.clone(),
                compute_role.clone(),
                edge_e.node_id().clone(),
                trace_b.clone(),
            ))
            .expect("Mission B compute should complete");
        let failure = runtime
            .execute(&ExecutionCommand::new(
                requirement_a.mission_id().clone(),
                requirement_a.task_id().clone(),
                group_a.clone(),
                transport_role.clone(),
                node_a.node_id().clone(),
                trace_a.clone(),
            ))
            .expect("failure injection should return an observation");
        assert!(
            matches!(failure, NodeEvent::TaskFailed { ref task_ref, .. } if task_ref == &task_ref_a)
        );

        control
            .block_group(
                &group_a,
                "transport role cannot progress on node-a",
                TimestampMs::new(1),
                &trace_a,
                &mut log,
            )
            .expect("Mission A should enter reconciliation");
        control
            .release_role_binding(
                &group_a,
                &transport_role,
                TimestampMs::new(1),
                &trace_a,
                &mut log,
            )
            .expect("Mission A should release only the failed role binding");
        control
            .rebind_role(
                &state,
                &group_a,
                &RoleRequirementView::new(RoleRequirement::new(
                    transport_role.clone(),
                    CapabilityKind::Transport,
                    Some(ResourceKind::Space),
                )),
                node_b.node_id().clone(),
                vec![space_b.clone()],
                TimestampMs::new(1),
                &trace_a,
                &mut log,
            )
            .expect("Mission A failed role should rebind");
        assert_eq!(
            control
                .group(&group_a)
                .expect("Mission A group should exist")
                .lifecycle(),
            GroupLifecycle::Adapted
        );
        control
            .activate_group(&group_a, TimestampMs::new(1), &trace_a, &mut log)
            .expect("Mission A recovered group should reactivate");
        assert_eq!(
            control
                .group(&group_a)
                .expect("Mission A group should exist")
                .lifecycle(),
            GroupLifecycle::Active
        );
        assert_eq!(
            control
                .group(&group_b)
                .expect("Mission B group should exist")
                .lifecycle(),
            GroupLifecycle::Active
        );
        assert_eq!(
            control
                .group(&group_b)
                .expect("Mission B group should exist")
                .assignments(),
            group_b_bindings.as_slice()
        );

        runtime
            .execute(&ExecutionCommand::new(
                requirement_a.mission_id().clone(),
                requirement_a.task_id().clone(),
                group_a.clone(),
                transport_role.clone(),
                node_b.node_id().clone(),
                trace_a.clone(),
            ))
            .expect("Mission A replacement transport should complete");
        runtime
            .execute(&ExecutionCommand::new(
                requirement_b.mission_id().clone(),
                requirement_b.task_id().clone(),
                group_b.clone(),
                transport_role.clone(),
                node_d.node_id().clone(),
                trace_b.clone(),
            ))
            .expect("Mission B transport should complete");
        control
            .complete_group(&group_a, TimestampMs::new(2), &trace_a, &mut log)
            .expect("Mission A should complete");
        control
            .complete_group(&group_b, TimestampMs::new(2), &trace_b, &mut log)
            .expect("Mission B should complete");
        control
            .release_group(&group_a, TimestampMs::new(3), &trace_a, &mut log)
            .expect("Mission A should release resources");
        control
            .release_group(&group_b, TimestampMs::new(3), &trace_b, &mut log)
            .expect("Mission B should release resources");
        assert_eq!(
            control
                .group(&group_a)
                .expect("Mission A group should exist")
                .lifecycle(),
            GroupLifecycle::Released
        );
        assert_eq!(
            control
                .group(&group_b)
                .expect("Mission B group should exist")
                .lifecycle(),
            GroupLifecycle::Released
        );

        let requirement_c = multi_mission_requirement("mission-c", "task-02");
        let candidates_c = control
            .match_capabilities(
                &state,
                &requirement_c,
                TimestampMs::new(4),
                &trace_c,
                &mut log,
            )
            .expect("Mission C matching should succeed");
        let proposal_c = control
            .propose(
                &state,
                &requirement_c,
                &candidates_c,
                vec![
                    RoleAssignment::new(
                        transport_role,
                        node_b.node_id().clone(),
                        vec![space_b.clone()],
                    ),
                    RoleAssignment::new(
                        compute_role,
                        edge_c.node_id().clone(),
                        vec![compute_c.clone()],
                    ),
                ],
                TimestampMs::new(4),
                &trace_c,
                &mut log,
            )
            .expect("Mission C proposal should reuse released resources");
        control
            .commit(&proposal_c, TimestampMs::new(4), &trace_c, &mut log)
            .expect("Mission C commit should reserve released resources");

        let events = log.snapshot();
        for event in &events {
            match event_task_ref(event.payload()) {
                Some(task_ref) if task_ref == &task_ref_a => {
                    assert_eq!(event.correlation_id(), &trace_a);
                }
                Some(task_ref) if task_ref == &task_ref_b => {
                    assert_eq!(event.correlation_id(), &trace_b);
                }
                _ => {}
            }
        }
        for task_ref in [&task_ref_a, &task_ref_b] {
            assert!(events.iter().any(|event| matches!(
                event.payload(),
                EventPayload::CandidatesMatched { task_ref: event_task_ref }
                    if event_task_ref == task_ref
            )));
            assert!(events.iter().any(|event| matches!(
                event.payload(),
                EventPayload::ProposalCreated { task_ref: event_task_ref }
                    if event_task_ref == task_ref
            )));
            assert!(events.iter().any(|event| matches!(
                event.payload(),
                EventPayload::PlanCommitted { task_ref: event_task_ref }
                    if event_task_ref == task_ref
            )));
            assert!(events.iter().any(|event| matches!(
                event.payload(),
                EventPayload::ExecutionGroupBound { task_ref: event_task_ref, .. }
                    if event_task_ref == task_ref
            )));
        }
        let recovery_events = events
            .iter()
            .filter(|event| matches!(event.payload(), EventPayload::RecoveryRebound { .. }))
            .collect::<Vec<_>>();
        assert_eq!(recovery_events.len(), 1);
        assert!(matches!(
            recovery_events[0].payload(),
            EventPayload::RecoveryRebound { group_id, task_ref, .. }
                if group_id == &group_a && task_ref == &task_ref_a
        ));
        assert!(events.iter().any(|event| matches!(
            event.payload(),
            EventPayload::ExecutionGroupCompleted { group_id, task_ref }
                if group_id == &group_a && task_ref == &task_ref_a
        )));
        assert!(events.iter().any(|event| matches!(
            event.payload(),
            EventPayload::ExecutionGroupCompleted { group_id, task_ref }
                if group_id == &group_b && task_ref == &task_ref_b
        )));
        assert!(events.iter().any(|event| matches!(
            event.payload(),
            EventPayload::ExecutionGroupReleased { group_id, task_ref, resource_ids }
                if group_id == &group_a && task_ref == &task_ref_a
                    && resource_ids.contains(&space_b) && resource_ids.contains(&compute_c)
        )));
        assert!(events.iter().any(|event| matches!(
            event.payload(),
            EventPayload::ExecutionGroupReleased { group_id, task_ref, .. }
                if group_id == &group_b && task_ref == &task_ref_b
        )));
    }
}
