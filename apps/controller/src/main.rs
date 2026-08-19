#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! Executable evidence for the first DEAIOS Node Contract vertical slice.

use control::{ControlPlane, GroupLifecycle, RoleRequirementView};
use domain::{
    Capability, CapabilityKind, CorrelationId, EventRecord, ExecutionCommand, ExecutionGroupId,
    LocalRuntime, MissionId, NodeHealth, NodeId, NodeRegistration, NodeStatus, Resource,
    ResourceId, ResourceKind, RoleAssignment, RoleId, RoleRequirement, TaskId, TaskRequirement,
    TimestampMs,
};
use runtime::Runtime;
use testkit::{FailureMode, FakeNode, SharedEventLog, VirtualClock};

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

/// Executes registration, proposal, commit, failure, rebind, and completion.
fn run_mvp_slice() -> Result<Vec<EventRecord>, String> {
    let mission_id = MissionId::new("mission-mvp-001").map_err(|error| error.to_string())?;
    let task_id = TaskId::new("task-transport-and-compute").map_err(|error| error.to_string())?;
    let correlation_id = CorrelationId::new("trace-mvp-001").map_err(|error| error.to_string())?;
    let transport_role = RoleId::new("primary-transport").map_err(|error| error.to_string())?;
    let compute_role = RoleId::new("execution-compute").map_err(|error| error.to_string())?;
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

    let requirement = TaskRequirement::new(
        mission_id.clone(),
        task_id.clone(),
        vec![
            RoleRequirement::new(
                transport_role.clone(),
                CapabilityKind::Transport,
                Some(ResourceKind::Space),
            ),
            RoleRequirement::new(
                compute_role.clone(),
                CapabilityKind::Compute,
                Some(ResourceKind::Compute),
            ),
        ],
    )
    .map_err(|error| error.to_string())?;

    let mut control = ControlPlane::new();
    let mut log = SharedEventLog::new();
    let timestamp = TimestampMs::new(0);
    control.register_node(
        node_a.clone(),
        NodeStatus::new(NodeHealth::Online, timestamp),
        timestamp,
        &correlation_id,
        &mut log,
    );
    control.register_node(
        node_b.clone(),
        NodeStatus::new(NodeHealth::Online, timestamp),
        timestamp,
        &correlation_id,
        &mut log,
    );
    control.register_node(
        edge.clone(),
        NodeStatus::new(NodeHealth::Online, timestamp),
        timestamp,
        &correlation_id,
        &mut log,
    );

    let candidates = control
        .match_capabilities(&requirement, timestamp, &correlation_id, &mut log)
        .map_err(|error| error.to_string())?;
    let proposal = control
        .propose(
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
        .rebind_role(
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
    Ok(log.snapshot())
}

#[cfg(test)]
mod tests {
    use domain::EventPayload;

    /// The first vertical slice must preserve completed work and recover by rebinding.
    #[test]
    fn mvp_slice_recovers_after_node_failure() {
        let events = super::run_mvp_slice().expect("deterministic MVP slice should pass");
        assert_eq!(events.len(), 12);
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
            EventPayload::NodeObservation(domain::NodeEvent::TaskCompleted { .. })
        ));
        assert!(matches!(
            events[8].payload(),
            EventPayload::NodeObservation(domain::NodeEvent::TaskFailed { node_id, .. })
                if node_id.as_str() == "node-a"
        ));
        assert!(matches!(
            events[9].payload(),
            EventPayload::RecoveryRebound { from_node, to_node, .. }
                if from_node.as_str() == "node-a" && to_node.as_str() == "node-b"
        ));
        assert!(matches!(
            events[10].payload(),
            EventPayload::NodeObservation(domain::NodeEvent::TaskCompleted { node_id, .. })
                if node_id.as_str() == "node-b"
        ));
        assert!(matches!(
            events[11].payload(),
            EventPayload::ExecutionGroupCompleted { .. }
        ));
    }
}
