#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! Executable evidence for the first DEAIOS Node Contract vertical slice.

use control::{
    ControlPlane, DeterministicBootstrapScheduler, GroupLifecycle, ReconciliationAssessment,
    RecoverySchedulingOutcome,
};
use domain::{
    ActorId, AllocationPhase, Capability, CapabilityContractRef, CapabilityKind,
    CoordinationContext, CoordinationContextId, CorrelationId, EventRecord, ExecutionCommand,
    ExecutionGroupId, ExecutionIntent, ExecutionValue, LocalRuntime, MISSION_PLAN_SCHEMA_V0_1,
    MissionGoal, MissionId, MissionPlan, NodeContractVersion, NodeHealth, NodeId, NodeRegistration,
    NodeStatus, PlannedTask, Resource, ResourceBindingScope, ResourceId, ResourceKind, RoleId,
    RoleRequirement, TaskContinuity, TaskGraph, TaskId, TaskRequirement, TimestampMs,
};
use ports::{AllocationStateReader, AllocationStateWriter};
use runtime::Runtime;
use serde::Deserialize;
use state::{InMemoryAllocationState, InMemorySharedNodeState};
use std::collections::{BTreeMap, BTreeSet};
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
    /// Mission-scoped logical actor identity.
    actor: String,
    /// Exact canonical capability contract required by this role.
    contract: OperationDocument,
    /// Capability category required by the role.
    capability: CapabilityDocument,
    /// Optional shared resource category required by the role.
    resource_kind: Option<ResourceDocument>,
    /// Canonical operation requested from whichever node is selected.
    execution: ExecutionDocument,
}

/// JSON adapter document for one canonical role execution intent.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ExecutionDocument {
    /// Canonical canonical capability contract identity independent of local skills.
    capability_contract: OperationDocument,
    /// Scalar parameters interpreted by the target adapter or local EAIOS.
    parameters: BTreeMap<String, serde_json::Value>,
}

/// JSON adapter document for one canonical capability contract reference.
#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct OperationDocument {
    /// Extensible operation family.
    namespace: String,
    /// Operation name within its family.
    name: String,
    /// Independently versioned operation semantics.
    version: String,
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
        Ok(events) => println!("DEAIOS control slice produced {} events", events.len()),
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
    let supported_contracts = capabilities
        .iter()
        .filter_map(|capability| {
            let (namespace, name) = match capability.kind() {
                CapabilityKind::Mobility | CapabilityKind::Transport => ("mobility", "move"),
                CapabilityKind::Compute => ("compute", "infer"),
                CapabilityKind::Observation => ("observation", "capture"),
            };
            CapabilityContractRef::new(namespace, name, "v1").ok()
        })
        .collect();
    Ok(NodeRegistration::new_with_contracts(
        node_id,
        runtime,
        NodeContractVersion::v0_1(),
        capabilities,
        supported_contracts,
        resources,
    ))
}

/// Converts one wire execution document into transport-neutral domain values.
fn execution_intent(document: ExecutionDocument) -> Result<ExecutionIntent, String> {
    let operation = CapabilityContractRef::new(
        document.capability_contract.namespace,
        document.capability_contract.name,
        document.capability_contract.version,
    )
    .map_err(|error| error.to_string())?;
    let parameters = document
        .parameters
        .into_iter()
        .map(|(key, value)| {
            let value = match value {
                serde_json::Value::Bool(value) => ExecutionValue::Bool(value),
                serde_json::Value::Number(value) if value.is_i64() => ExecutionValue::Integer(
                    value
                        .as_i64()
                        .ok_or_else(|| format!("execution parameter {key} exceeds i64"))?,
                ),
                serde_json::Value::Number(value) => ExecutionValue::Float(
                    value
                        .as_f64()
                        .ok_or_else(|| format!("execution parameter {key} is not finite"))?,
                ),
                serde_json::Value::String(value) => ExecutionValue::String(value),
                _ => return Err(format!("execution parameter {key} must be a scalar")),
            };
            Ok((key, value))
        })
        .collect::<Result<BTreeMap<_, _>, String>>()?;
    ExecutionIntent::new(operation, parameters).map_err(|error| error.to_string())
}

/// Loads the approved Mission Plan fixture and converts it into validated domain values.
fn load_mission_plan() -> Result<MissionPlan, String> {
    let source = include_str!("../../../scenarios/mvp-slice-v0.1/mission-plan.json");
    let document: MissionPlanDocument =
        serde_json::from_str(source).map_err(|error| error.to_string())?;
    if document.schema_version != MISSION_PLAN_SCHEMA_V0_1 {
        return Err(format!(
            "unsupported Mission Plan schema: {}",
            document.schema_version
        ));
    }
    let mission_id = MissionId::new(document.mission.id).map_err(|error| error.to_string())?;
    let goal = MissionGoal::new(mission_id.clone(), document.mission.objective)
        .map_err(|error| error.to_string())?;
    let context_id = CoordinationContextId::new("legacy-controller-context")
        .map_err(|error| error.to_string())?;
    let tasks = document
        .tasks
        .into_iter()
        .map(|task| {
            let task_id = TaskId::new(task.id).map_err(|error| error.to_string())?;
            let mut roles = Vec::with_capacity(task.roles.len());
            let mut execution_intents = BTreeMap::new();
            for role in task.roles {
                let role_id = RoleId::new(role.id).map_err(|error| error.to_string())?;
                let intent = execution_intent(role.execution)?;
                let contract = CapabilityContractRef::new(
                    role.contract.namespace,
                    role.contract.name,
                    role.contract.version,
                )
                .map_err(|error| error.to_string())?;
                roles.push(RoleRequirement::new_with_actor_and_contract(
                    role_id.clone(),
                    ActorId::new(role.actor).map_err(|error| error.to_string())?,
                    role.capability.into(),
                    contract,
                    role.resource_kind.map(Into::into),
                ));
                execution_intents.insert(role_id, intent);
            }
            let requirement = TaskRequirement::new(mission_id.clone(), task_id, roles)
                .map_err(|error| error.to_string())?;
            let dependencies = task
                .depends_on
                .into_iter()
                .map(|dependency| TaskId::new(dependency).map_err(|error| error.to_string()))
                .collect::<Result<Vec<_>, String>>()?;
            PlannedTask::new(
                task.description,
                requirement,
                execution_intents,
                dependencies,
                TaskContinuity::new(context_id.clone(), BTreeMap::new(), BTreeMap::new()),
            )
            .map_err(|error| error.to_string())
        })
        .collect::<Result<Vec<_>, String>>()?;
    let task_graph = TaskGraph::new(mission_id, tasks).map_err(|error| error.to_string())?;
    let context =
        CoordinationContext::new(context_id, Vec::new()).map_err(|error| error.to_string())?;
    MissionPlan::new(goal, task_graph, vec![context]).map_err(|error| error.to_string())
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

/// Refreshes the non-authoritative Allocation View from current Control authority.
fn refresh_allocation_view(
    control: &ControlPlane,
    allocation_state: &mut InMemoryAllocationState,
    projected_at: TimestampMs,
) -> Result<(), String> {
    allocation_state
        .replace_allocation_view(
            control
                .allocation_snapshot(projected_at)
                .map_err(|error| error.to_string())?,
        )
        .map_err(|error| error.to_string())
}

/// Verifies one projected resource phase and optional Group ownership.
fn require_allocation_phase(
    allocation_state: &InMemoryAllocationState,
    resource_id: &ResourceId,
    phase: AllocationPhase,
    group_id: Option<&ExecutionGroupId>,
) -> Result<(), String> {
    let allocation = allocation_state
        .allocation(resource_id)
        .ok_or_else(|| format!("allocation projection lacks resource {resource_id}"))?;
    if allocation.phase() != phase || allocation.group_id() != group_id {
        return Err(format!(
            "allocation projection mismatch for resource {resource_id}"
        ));
    }
    Ok(())
}

/// Executes registration and committed work, then fences unauthorized Actor migration on failure.
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
    let scheduler = DeterministicBootstrapScheduler::new();
    let mut state = InMemorySharedNodeState::new();
    let mut allocation_state = InMemoryAllocationState::new();
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

    control
        .create_mission_group(
            group_id.clone(),
            &mission_plan,
            timestamp,
            &correlation_id,
            &mut log,
        )
        .map_err(|error| error.to_string())?;
    control
        .ready_task_execution(
            &group_id,
            requirement.task_ref(),
            timestamp,
            &correlation_id,
            &mut log,
        )
        .map_err(|error| error.to_string())?;

    let candidates = control
        .match_capabilities_for_mission(
            &state,
            &mission_plan,
            &requirement,
            timestamp,
            &correlation_id,
            &mut log,
        )
        .map_err(|error| error.to_string())?;
    let scheduling_decision = scheduler
        .schedule_task(
            &state,
            &requirement,
            &candidates,
            timestamp,
            &correlation_id,
            &mut log,
        )
        .map_err(|error| error.to_string())?;
    let proposal = control
        .propose(
            &state,
            &requirement,
            &candidates,
            scheduling_decision.proposed_assignments(),
            timestamp,
            &correlation_id,
            &mut log,
        )
        .map_err(|error| error.to_string())?;
    let plan = control
        .commit(&proposal, timestamp, &correlation_id, &mut log)
        .map_err(|error| error.to_string())?;
    refresh_allocation_view(&control, &mut allocation_state, TimestampMs::new(0))?;
    require_allocation_phase(
        &allocation_state,
        &a_space,
        AllocationPhase::Committed,
        None,
    )?;
    control
        .bind_task_execution_with_requirement(
            &group_id,
            &plan,
            &requirement,
            timestamp,
            &correlation_id,
            &mut log,
        )
        .map_err(|error| error.to_string())?;
    refresh_allocation_view(&control, &mut allocation_state, TimestampMs::new(1))?;
    require_allocation_phase(
        &allocation_state,
        &a_space,
        AllocationPhase::Bound,
        Some(&group_id),
    )?;
    control
        .activate_task_execution(
            &group_id,
            requirement.task_ref(),
            timestamp,
            &correlation_id,
            &mut log,
        )
        .map_err(|error| error.to_string())?;

    let mut runtime = Runtime::new(VirtualClock::new(timestamp), log.clone());
    runtime
        .register_node(Box::new(FakeNode::new(node_a).with_failure_mode(
            FailureMode::FailNextAndReportStatus {
                reason: "onboard execution capability degraded".to_string(),
                status: NodeStatus::new(NodeHealth::Offline, TimestampMs::new(1)),
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
        compute_role.clone(),
        NodeId::new("edge-gpu").map_err(|error| error.to_string())?,
        planned_task
            .execution_intent(&compute_role)
            .ok_or_else(|| "compute role lacks execution intent".to_string())?
            .clone(),
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
        planned_task
            .execution_intent(&transport_role)
            .ok_or_else(|| "transport role lacks execution intent".to_string())?
            .clone(),
        correlation_id.clone(),
    );
    let failure = runtime
        .execute(&transport_command)
        .map_err(|error| error.to_string())?;
    if !matches!(failure, domain::NodeEvent::TaskFailed { .. }) {
        return Err("failure injection did not produce a task failure".to_string());
    }

    runtime
        .observe_node_status(
            &NodeId::new("node-a").map_err(|error| error.to_string())?,
            &mut state,
        )
        .map_err(|error| error.to_string())?;
    let assessment = control
        .assess_group(
            &state,
            &group_id,
            &requirement,
            TimestampMs::new(1),
            &correlation_id,
            &mut log,
        )
        .map_err(|error| error.to_string())?;
    let ReconciliationAssessment::RoleRecoveryRequired(need) = assessment else {
        return Err("failed assigned node did not produce a recovery need".to_string());
    };
    control
        .begin_role_recovery(&need, TimestampMs::new(1), &correlation_id, &mut log)
        .map_err(|error| error.to_string())?;
    refresh_allocation_view(&control, &mut allocation_state, TimestampMs::new(2))?;
    if allocation_state.allocation(&a_space).is_some() {
        return Err("partial release remained visible in allocation projection".to_string());
    }
    require_allocation_phase(
        &allocation_state,
        &edge_compute,
        AllocationPhase::Bound,
        Some(&group_id),
    )?;
    let recovery_candidates = control
        .match_recovery_candidates(
            &state,
            &need,
            &requirement,
            TimestampMs::new(1),
            &correlation_id,
            &mut log,
        )
        .map_err(|error| error.to_string())?;
    let recovery_scheduling = scheduler
        .schedule_recovery(
            &state,
            &requirement,
            &recovery_candidates,
            TimestampMs::new(1),
            &correlation_id,
            &mut log,
        )
        .map_err(|error| error.to_string())?;
    let RecoverySchedulingOutcome::Selected(recovery_decision) = recovery_scheduling else {
        return Ok(log.snapshot());
    };
    let replacement_node_id = recovery_decision.replacement_node_id().clone();
    let recovery_proposal = control
        .propose_role_recovery(
            &state,
            &recovery_candidates,
            &requirement,
            replacement_node_id.clone(),
            recovery_decision.resource_ids().to_vec(),
            TimestampMs::new(1),
            &correlation_id,
            &mut log,
        )
        .map_err(|error| error.to_string())?;
    let committed_recovery = control
        .commit_role_recovery(
            &state,
            &requirement,
            &recovery_proposal,
            TimestampMs::new(1),
            &correlation_id,
            &mut log,
        )
        .map_err(|error| error.to_string())?;
    refresh_allocation_view(&control, &mut allocation_state, TimestampMs::new(3))?;
    require_allocation_phase(
        &allocation_state,
        &b_space,
        AllocationPhase::RecoveryPending,
        Some(&group_id),
    )?;
    control
        .rebind_role(
            &committed_recovery,
            TimestampMs::new(1),
            &correlation_id,
            &mut log,
        )
        .map_err(|error| error.to_string())?;
    refresh_allocation_view(&control, &mut allocation_state, TimestampMs::new(4))?;
    require_allocation_phase(
        &allocation_state,
        &b_space,
        AllocationPhase::Bound,
        Some(&group_id),
    )?;
    control
        .activate_group(&group_id, TimestampMs::new(1), &correlation_id, &mut log)
        .map_err(|error| error.to_string())?;

    let replacement_command = ExecutionCommand::new(
        mission_id,
        task_id,
        group_id.clone(),
        transport_role.clone(),
        replacement_node_id,
        planned_task
            .execution_intent(&transport_role)
            .ok_or_else(|| "transport role lacks execution intent".to_string())?
            .clone(),
        correlation_id.clone(),
    );
    runtime
        .execute(&replacement_command)
        .map_err(|error| error.to_string())?;
    let task_resources = control
        .group(&group_id)
        .and_then(|group| group.task_execution(requirement.task_ref()))
        .ok_or_else(|| "Mission Task execution disappeared before completion".to_string())?
        .assignments()
        .iter()
        .flat_map(|assignment| assignment.resource_ids())
        .filter(|resource_id| {
            control
                .group(&group_id)
                .and_then(|group| group.task_execution(requirement.task_ref()))
                .is_some_and(|execution| {
                    execution.binding_scope(resource_id) == ResourceBindingScope::Task
                })
        })
        .cloned()
        .collect::<Vec<_>>();
    control
        .complete_task_execution(
            &group_id,
            requirement.task_ref(),
            TimestampMs::new(2),
            &correlation_id,
            &mut log,
        )
        .map_err(|error| error.to_string())?;
    control
        .release_task_bindings(
            &group_id,
            requirement.task_ref(),
            &task_resources,
            TimestampMs::new(2),
            &correlation_id,
            &mut log,
        )
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
    refresh_allocation_view(&control, &mut allocation_state, TimestampMs::new(5))?;
    if allocation_state
        .allocations()
        .iter()
        .any(|allocation| allocation.group_id() == Some(&group_id))
    {
        return Err("released Group remains in allocation projection".to_string());
    }
    if control.group(&group_id).map(|group| group.lifecycle()) != Some(GroupLifecycle::Released) {
        return Err("execution group did not reach Released".to_string());
    }
    Ok(log.snapshot())
}

#[cfg(test)]
mod tests;
