//! Independent lifecycle model and replayable ownership traces; no physical execution is involved.

use super::*;
use std::collections::BTreeSet;

/// Ownership stages predicted independently of Control's reservation implementation.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Stage {
    /// Normal Commit exists without a bound Task assignment.
    Committed,
    /// One exact Task/Role is bound to this endpoint.
    Bound(usize),
    /// A released Task/Role retains its previous endpoint and owns no resources.
    Unbound(usize),
    /// Replacement resources are committed but the role has not rebound.
    RecoveryPending(usize, usize),
    /// Task satisfaction or terminal Group release removed Task ownership.
    Released,
}

/// Replayable operation, with a stable logical slot rather than a physical Node key.
#[derive(Clone, Copy, Debug, serde::Serialize, serde::Deserialize)]
enum Operation {
    /// Release a stopped role; the test driver explicitly supplies stop authorization.
    Recover(usize),
    /// Commit the opposite endpoint as the replacement.
    Commit(usize),
    /// Abort only this slot's pending replacement.
    Abort(usize),
    /// Consume only this slot's exact pending commitment.
    Rebind(usize),
    /// Complete, satisfy and release a Task's own resources.
    Finish(usize),
    /// Restore durable ownership and explicitly re-register current-process Nodes.
    Restore,
    /// Reject an unknown logical Task without mutating any authority or event trace.
    RejectUnknownTask,
}

/// Two independent Tasks sharing a Role name and initial endpoint with distinct exclusive resources.
struct ModelFixture {
    /// Sole production reservation and binding authority.
    control: ControlPlane,
    /// Live registration evidence supplied through the existing State port.
    state: InMemorySharedNodeState,
    /// Inspectable evidence including rejected-operation atomicity.
    events: RecordingEvents,
    /// Immutable requirements with the same RoleId in different Tasks.
    requirements: [TaskRequirement; 2],
    /// Shared Mission-level Group.
    group: ExecutionGroupId,
    /// Exact Role name shared across Task-local slots.
    role: RoleId,
    /// Current endpoint inventory.
    nodes: [NodeId; 2],
    /// Resource selected for each endpoint and logical slot, empty for zero-resource tests.
    resources: [[Vec<ResourceId>; 2]; 2],
    /// Production committed handles; these are references, never model authority.
    pending: [Option<CommittedRecoveryAssignment>; 2],
    /// Independent predicted ownership stages.
    model: [Stage; 2],
    /// Monotonic receive/decision time in this offline trace.
    now: u64,
}

impl ModelFixture {
    /// Creates legal compatibility Tasks and runs the actual Match/Propose/Commit/Bind chain.
    fn new(resource_bearing: bool) -> Self {
        let mission = MissionId::new("ownership-model").unwrap();
        let context = domain::CoordinationContextId::new("shared-context").unwrap();
        let role = RoleId::new("worker").unwrap();
        let requirements = std::array::from_fn(|slot| {
            TaskRequirement::new(
                mission.clone(),
                TaskId::new(format!("task-{slot}")).unwrap(),
                vec![RoleRequirement::new(
                    role.clone(),
                    CapabilityKind::Compute,
                    resource_bearing.then_some(ResourceKind::Compute),
                )],
            )
            .unwrap()
        });
        let tasks = requirements
            .iter()
            .map(|requirement| {
                PlannedTask::new(
                    "perform independent work",
                    requirement.clone(),
                    BTreeMap::from([(
                        role.clone(),
                        ExecutionIntent::new(
                            CapabilityContractRef::new("compute", "work", "v1").unwrap(),
                            BTreeMap::new(),
                        )
                        .unwrap(),
                    )]),
                    Vec::new(),
                    domain::TaskContinuity::new(context.clone(), BTreeMap::new(), BTreeMap::new()),
                )
                .unwrap()
            })
            .collect();
        let plan = MissionPlan::new(
            MissionGoal::new(mission.clone(), "complete both Tasks").unwrap(),
            TaskGraph::new(mission, tasks).unwrap(),
            vec![domain::CoordinationContext::new(context, Vec::new()).unwrap()],
        )
        .unwrap();
        let resources = std::array::from_fn(|node| {
            std::array::from_fn(|slot| {
                if resource_bearing {
                    vec![ResourceId::new(format!("resource-{node}-{slot}")).unwrap()]
                } else {
                    Vec::new()
                }
            })
        });
        let mut fixture = Self {
            control: ControlPlane::new(),
            state: InMemorySharedNodeState::new(),
            events: RecordingEvents::default(),
            requirements,
            group: ExecutionGroupId::new("model-group").unwrap(),
            role,
            nodes: [
                NodeId::new("worker-a").unwrap(),
                NodeId::new("worker-b").unwrap(),
            ],
            resources,
            pending: [None, None],
            model: [Stage::Committed; 2],
            now: 0,
        };
        fixture.register_nodes();
        fixture
            .control
            .create_mission_group(
                fixture.group.clone(),
                &plan,
                fixture.time(),
                &trace(),
                &mut fixture.events,
            )
            .unwrap();
        let mut handles = Vec::new();
        for slot in 0..2 {
            let requirement = &fixture.requirements[slot];
            fixture
                .control
                .ready_task_execution(
                    &fixture.group,
                    requirement.task_ref(),
                    fixture.time(),
                    &trace(),
                    &mut fixture.events,
                )
                .unwrap();
            let candidates = fixture
                .control
                .match_capabilities(
                    &fixture.state,
                    requirement,
                    fixture.time(),
                    &trace(),
                    &mut fixture.events,
                )
                .unwrap();
            let proposal = fixture
                .control
                .propose(
                    &fixture.state,
                    requirement,
                    &candidates,
                    vec![RoleAssignment::new(
                        fixture.role.clone(),
                        fixture.nodes[0].clone(),
                        fixture.resources[0][slot].clone(),
                    )],
                    fixture.time(),
                    &trace(),
                    &mut fixture.events,
                )
                .unwrap();
            handles.push(
                fixture
                    .control
                    .commit_for_group_with_state(
                        &fixture.state,
                        &fixture.group,
                        &proposal,
                        fixture.time(),
                        &trace(),
                        &mut fixture.events,
                    )
                    .unwrap(),
            );
        }
        fixture
            .check()
            .expect("normal pending Commit is legal ownership");
        assert!(ControlPlane::restore(fixture.control.checkpoint()).is_ok());
        for (slot, handle) in handles.iter().enumerate() {
            fixture
                .control
                .bind_task_execution(
                    &fixture.group,
                    handle,
                    fixture.time(),
                    &trace(),
                    &mut fixture.events,
                )
                .unwrap();
            fixture
                .control
                .activate_task_execution(
                    &fixture.group,
                    fixture.requirements[slot].task_ref(),
                    fixture.time(),
                    &trace(),
                    &mut fixture.events,
                )
                .unwrap();
            fixture.model[slot] = Stage::Bound(0);
            fixture.check().unwrap();
        }
        fixture
    }

    /// Returns the deterministic local time without advancing evidence freshness implicitly.
    fn time(&self) -> TimestampMs {
        TimestampMs::new(self.now)
    }

    /// Re-establishes process-local leases after restore while retaining exact resource identities.
    fn register_nodes(&mut self) {
        let timestamp = self.time();
        for node in 0..2 {
            let registration = NodeRegistration::new(
                self.nodes[node].clone(),
                domain::LocalRuntime::new("fake-model", "1").unwrap(),
                domain::NodeContractVersion::v0_1(),
                vec![Capability::new(CapabilityKind::Compute, true)],
                self.resources[node]
                    .iter()
                    .flatten()
                    .map(|id| Resource::new(id.clone(), ResourceKind::Compute, 1).unwrap())
                    .collect(),
            );
            self.control
                .register_node(
                    &mut self.state,
                    registration,
                    NodeStatus::new(NodeHealth::Online, timestamp),
                    timestamp,
                    &trace(),
                    &mut self.events,
                )
                .unwrap();
        }
    }

    /// Applies only the already-defined aggregate Blocked barrier required by recovery APIs.
    fn block_for_recovery(&mut self) {
        if self.control.group(&self.group).unwrap().lifecycle() != GroupLifecycle::Blocked {
            self.control
                .block_group(
                    &self.group,
                    "explicit test stop authorization",
                    self.time(),
                    &trace(),
                    &mut self.events,
                )
                .unwrap();
        }
    }

    /// Compares projected resources and logical-slot bindings to the independent stage model.
    fn check(&self) -> Result<(), String> {
        let snapshot = self
            .control
            .allocation_snapshot(self.time())
            .map_err(|error| error.to_string())?;
        let mut expected = BTreeMap::new();
        for (slot, stage) in self.model.iter().enumerate() {
            let ownership = match stage {
                Stage::Committed => Some((0, domain::AllocationPhase::Committed)),
                Stage::Bound(node) => Some((*node, domain::AllocationPhase::Bound)),
                Stage::RecoveryPending(_, node) => {
                    Some((*node, domain::AllocationPhase::RecoveryPending))
                }
                Stage::Unbound(_) | Stage::Released => None,
            };
            if let Some((node, phase)) = ownership {
                for resource in &self.resources[node][slot] {
                    expected.insert(
                        resource.clone(),
                        (
                            self.requirements[slot].task_ref().clone(),
                            self.role.clone(),
                            phase,
                        ),
                    );
                }
            }
            let execution = self
                .control
                .group(&self.group)
                .unwrap()
                .task_execution(self.requirements[slot].task_ref())
                .unwrap();
            let bound = match stage {
                Stage::Bound(node) => Some(*node),
                _ => None,
            };
            if execution.assignments().len() != usize::from(bound.is_some()) {
                return Err(format!(
                    "slot {slot}: assignment count does not match {stage:?}"
                ));
            }
            if let Some(node) = bound {
                let assignment = &execution.assignments()[0];
                if assignment.role_id() != &self.role
                    || assignment.node_id() != &self.nodes[node]
                    || assignment.resource_ids() != self.resources[node][slot]
                {
                    return Err(format!("slot {slot}: binding differs from model"));
                }
            }
            if self
                .control
                .pending_recovery_commitment_for_task(
                    &self.group,
                    self.requirements[slot].task_ref(),
                    &self.role,
                )
                .is_some()
                != matches!(stage, Stage::RecoveryPending(..))
            {
                return Err(format!(
                    "slot {slot}: pending handle presence differs from model"
                ));
            }
        }
        let actual = snapshot
            .allocations()
            .iter()
            .map(|allocation| {
                (
                    allocation.resource_id().clone(),
                    (
                        allocation.task_ref().clone(),
                        allocation.role_id().clone(),
                        allocation.phase(),
                    ),
                )
            })
            .collect::<BTreeMap<_, _>>();
        if actual != expected {
            return Err(format!(
                "allocation mismatch: actual={actual:?}, expected={expected:?}"
            ));
        }
        Ok(())
    }

    /// Executes a trace operation; invalid generator preconditions are filtered rather than retried.
    fn apply(&mut self, operation: Operation) -> Result<(), String> {
        self.now += 1;
        match operation {
            Operation::Recover(slot) => {
                if let Stage::Bound(node) = self.model[slot] {
                    self.block_for_recovery();
                    self.control
                        .release_task_role_binding(
                            &self.group,
                            self.requirements[slot].task_ref(),
                            &self.role,
                            self.time(),
                            &trace(),
                            &mut self.events,
                        )
                        .map_err(|error| error.to_string())?;
                    self.model[slot] = Stage::Unbound(node);
                }
            }
            Operation::Commit(slot) => {
                if let Stage::Unbound(previous) = self.model[slot] {
                    self.block_for_recovery();
                    let requirement = &self.requirements[slot];
                    let need = RoleRecoveryNeed::new(
                        self.group.clone(),
                        requirement.task_ref().clone(),
                        self.role.clone(),
                        self.nodes[previous].clone(),
                    );
                    let candidates = self
                        .control
                        .match_recovery_candidates(
                            &self.state,
                            &need,
                            requirement,
                            self.time(),
                            &trace(),
                            &mut self.events,
                        )
                        .map_err(|error| error.to_string())?;
                    let replacement = 1 - previous;
                    let proposal = self
                        .control
                        .propose_role_recovery(
                            &self.state,
                            &candidates,
                            requirement,
                            self.nodes[replacement].clone(),
                            self.resources[replacement][slot].clone(),
                            self.time(),
                            &trace(),
                            &mut self.events,
                        )
                        .map_err(|error| error.to_string())?;
                    self.pending[slot] = Some(
                        self.control
                            .commit_role_recovery(
                                &self.state,
                                requirement,
                                &proposal,
                                self.time(),
                                &trace(),
                                &mut self.events,
                            )
                            .map_err(|error| error.to_string())?,
                    );
                    self.model[slot] = Stage::RecoveryPending(previous, replacement);
                }
            }
            Operation::Abort(slot) | Operation::Rebind(slot) => {
                if let Stage::RecoveryPending(previous, replacement) = self.model[slot] {
                    self.block_for_recovery();
                    let committed = self.pending[slot].take().unwrap();
                    let before_sibling = serde_json::to_value(
                        self.control
                            .group(&self.group)
                            .unwrap()
                            .task_execution(self.requirements[1 - slot].task_ref()),
                    )
                    .unwrap();
                    if matches!(operation, Operation::Abort(_)) {
                        self.control
                            .abort_role_recovery_commitment(
                                &committed,
                                self.time(),
                                &trace(),
                                &mut self.events,
                            )
                            .map_err(|error| error.to_string())?;
                        self.model[slot] = Stage::Unbound(previous);
                    } else {
                        self.control
                            .rebind_role(&committed, self.time(), &trace(), &mut self.events)
                            .map_err(|error| error.to_string())?;
                        self.model[slot] = Stage::Bound(replacement);
                    }
                    let after_sibling = serde_json::to_value(
                        self.control
                            .group(&self.group)
                            .unwrap()
                            .task_execution(self.requirements[1 - slot].task_ref()),
                    )
                    .unwrap();
                    if before_sibling != after_sibling {
                        return Err("replacement mutated the sibling Task".into());
                    }
                    let before = serde_json::to_value(self.control.checkpoint()).unwrap();
                    let count = self.events.records.len();
                    if self
                        .control
                        .rebind_role(&committed, self.time(), &trace(), &mut self.events)
                        .is_ok()
                        || serde_json::to_value(self.control.checkpoint()).unwrap() != before
                        || self.events.records.len() != count
                    {
                        return Err(
                            "consumed commitment was replayable or rejection mutated authority"
                                .into(),
                        );
                    }
                }
            }
            Operation::Finish(slot) => {
                if matches!(self.model[slot], Stage::Bound(_)) {
                    let task = self.requirements[slot].task_ref();
                    let resources = self
                        .control
                        .group(&self.group)
                        .unwrap()
                        .task_execution(task)
                        .unwrap()
                        .assignments()[0]
                        .resource_ids()
                        .to_vec();
                    self.control
                        .record_task_execution_completed(
                            &self.group,
                            task,
                            self.time(),
                            &trace(),
                            &mut self.events,
                        )
                        .map_err(|error| error.to_string())?;
                    self.control
                        .satisfy_task_execution(
                            &self.group,
                            task,
                            domain::TaskSatisfactionBasis::ExecutionReport,
                            self.time(),
                            &trace(),
                            &mut self.events,
                        )
                        .map_err(|error| error.to_string())?;
                    self.control
                        .release_task_bindings(
                            &self.group,
                            task,
                            &resources,
                            self.time(),
                            &trace(),
                            &mut self.events,
                        )
                        .map_err(|error| error.to_string())?;
                    self.model[slot] = Stage::Released;
                }
            }
            Operation::Restore => {
                self.control = ControlPlane::restore(self.control.checkpoint())
                    .map_err(|error| error.to_string())?;
                self.register_nodes();
            }
            Operation::RejectUnknownTask => {
                let requirement = TaskRequirement::new(
                    self.requirements[0].mission_id().clone(),
                    TaskId::new("unknown").unwrap(),
                    self.requirements[0].roles().to_vec(),
                )
                .unwrap();
                let need = RoleRecoveryNeed::new(
                    self.group.clone(),
                    requirement.task_ref().clone(),
                    self.role.clone(),
                    self.nodes[0].clone(),
                );
                let before = serde_json::to_value(self.control.checkpoint()).unwrap();
                let count = self.events.records.len();
                if self
                    .control
                    .match_recovery_candidates(
                        &self.state,
                        &need,
                        &requirement,
                        self.time(),
                        &trace(),
                        &mut self.events,
                    )
                    .is_ok()
                    || serde_json::to_value(self.control.checkpoint()).unwrap() != before
                    || self.events.records.len() != count
                {
                    return Err("unknown Task accepted or rejection mutated authority".into());
                }
            }
        }
        self.check()
    }
}

/// A small fixed-seed generator used only for deterministic operation selection, never security.
fn next_seed(seed: &mut u64) -> u64 {
    *seed = seed.wrapping_add(0x9e3779b97f4a7c15);
    let mut value = *seed;
    value = (value ^ (value >> 30)).wrapping_mul(0xbf58476d1ce4e5b9);
    value = (value ^ (value >> 27)).wrapping_mul(0x94d049bb133111eb);
    value ^ (value >> 31)
}

/// Replays a saved operation trace against a fresh fixture and returns its first counterexample.
fn replay(resource_bearing: bool, operations: &[Operation]) -> Result<(), String> {
    let mut fixture = ModelFixture::new(resource_bearing);
    for (index, operation) in operations.iter().enumerate() {
        fixture
            .apply(*operation)
            .map_err(|error| format!("step {index}: {error}"))?;
    }
    Ok(())
}

/// Removes chunks while preserving the counterexample, including generator precondition filtering.
fn shrink(resource_bearing: bool, mut operations: Vec<Operation>) -> Vec<Operation> {
    let mut width = operations.len().max(1) / 2;
    while width > 0 {
        let mut start = 0;
        while start + width <= operations.len() {
            let mut candidate = operations.clone();
            candidate.drain(start..start + width);
            if replay(resource_bearing, &candidate).is_err() {
                operations = candidate;
            } else {
                start += 1;
            }
        }
        width /= 2;
    }
    operations
}

/// Retains a machine-readable reduced counterexample in temporary storage before failing a check.
fn assert_replay(seed: u64, resource_bearing: bool, operations: Vec<Operation>) {
    if let Err(error) = replay(resource_bearing, &operations) {
        let reduced = shrink(resource_bearing, operations.clone());
        let case = serde_json::json!({
            "seed": seed, "resource_bearing": resource_bearing,
            "operations": operations, "reduced": reduced,
        });
        let path = std::env::temp_dir().join(format!(
            "roboguide-ownership-{}-{seed}-{resource_bearing}.json",
            std::process::id(),
        ));
        let encoded = serde_json::to_string_pretty(&case).unwrap();
        let saved = std::fs::write(&path, &encoded);
        panic!(
            "seed={seed}: {error}; case={} save={saved:?}; {encoded}",
            path.display()
        );
    }
}

/// Fixed prefix guarantees interleaved same-Role recovery is exercised by every generated trace.
fn operation_trace(mut seed: u64) -> Vec<Operation> {
    let mut operations = vec![
        Operation::Recover(0),
        Operation::Recover(1),
        Operation::Commit(0),
        Operation::Commit(1),
        Operation::Restore,
        Operation::Rebind(1),
        Operation::Abort(0),
        Operation::Commit(0),
        Operation::Rebind(0),
    ];
    for _ in 0..48 {
        let value = next_seed(&mut seed);
        let slot = ((value >> 32) & 1) as usize;
        operations.push(match value % 8 {
            0 => Operation::Recover(slot),
            1 => Operation::Commit(slot),
            2 => Operation::Abort(slot),
            3 => Operation::Rebind(slot),
            4 => Operation::Restore,
            5 => Operation::RejectUnknownTask,
            6 => Operation::Finish(slot),
            _ => Operation::Recover(slot),
        });
    }
    operations
}

/// Tests legal pending/zero-resource states and interleavings, reporting a reduced replay on failure.
#[test]
fn ownership_model_fixed_seed_traces() {
    for seed in 0..128 {
        for resource_bearing in [false, true] {
            let operations = operation_trace(seed);
            assert_replay(seed, resource_bearing, operations);
        }
    }
}

/// Explicit extended check; intentionally excluded from ordinary workspace runs.
#[test]
#[ignore = "extended 10000-sequence ownership audit"]
fn ownership_model_extended_traces() {
    for seed in 0..10_000 {
        let resource_bearing = seed % 2 == 0;
        let operations = operation_trace(seed);
        assert_replay(seed, resource_bearing, operations);
    }
}

/// Replays a retained counterexample without running any other generated population.
#[test]
#[ignore = "requires ROBOGUIDE_OWNERSHIP_REPLAY_PATH"]
fn ownership_model_replay_saved_case() {
    let path = std::env::var_os("ROBOGUIDE_OWNERSHIP_REPLAY_PATH")
        .expect("supply a saved counterexample path");
    let raw = std::fs::read(path).unwrap();
    assert!(raw.len() <= 256 * 1024, "counterexample is bounded");
    let case: serde_json::Value = serde_json::from_slice(&raw).unwrap();
    let resource_bearing = case["resource_bearing"].as_bool().unwrap();
    let operations: Vec<Operation> = serde_json::from_value(case["reduced"].clone()).unwrap();
    assert!(operations.len() <= 1024);
    assert!(operations.iter().all(|operation| match operation {
        Operation::Recover(slot)
        | Operation::Commit(slot)
        | Operation::Abort(slot)
        | Operation::Rebind(slot)
        | Operation::Finish(slot) => *slot < 2,
        Operation::Restore | Operation::RejectUnknownTask => true,
    }));
    replay(resource_bearing, &operations).unwrap();
}

/// Completing one Task leaves the sibling's exact ownership and resource commitment intact.
#[test]
fn ownership_model_task_release_is_scoped() {
    let mut fixture = ModelFixture::new(true);
    fixture.apply(Operation::Finish(0)).unwrap();
    fixture.apply(Operation::Recover(1)).unwrap();
    fixture.apply(Operation::Commit(1)).unwrap();
    fixture.apply(Operation::Rebind(1)).unwrap();
    fixture.apply(Operation::Finish(1)).unwrap();
    fixture
        .control
        .complete_group(
            &fixture.group,
            fixture.time(),
            &trace(),
            &mut fixture.events,
        )
        .unwrap();
    fixture
        .control
        .release_group(
            &fixture.group,
            fixture.time(),
            &trace(),
            &mut fixture.events,
        )
        .unwrap();
    fixture.check().unwrap();
    assert!(ControlPlane::restore(fixture.control.checkpoint()).is_ok());
}

/// Unknown Task recovery is rejected before any evidence or binding is changed.
#[test]
fn execution_recovery_unknown_task_is_atomic() {
    let mut fixture = ModelFixture::new(true);
    let before = serde_json::to_value(fixture.control.checkpoint()).unwrap();
    let count = fixture.events.records.len();
    let unknown = TaskRef::new(
        fixture.requirements[0].mission_id().clone(),
        TaskId::new("unknown").unwrap(),
    );
    assert!(
        fixture
            .control
            .begin_execution_recovery(
                &fixture.group,
                &unknown,
                &fixture.role,
                &fixture.nodes[0],
                fixture.time(),
                &trace(),
                &mut fixture.events
            )
            .is_err()
    );
    assert_eq!(
        serde_json::to_value(fixture.control.checkpoint()).unwrap(),
        before
    );
    assert_eq!(
        fixture.events.records.len(),
        count,
        "rejected recovery must not append success-path evidence"
    );
}

/// Zero-resource malformed legacy checkpoints still must enforce logical Role uniqueness.
#[test]
fn legacy_checkpoint_rejects_duplicate_and_bound_unbound_role_overlap() {
    let mut fixture = recovery_fixture(true);
    let assignment = fixture.control.groups[&fixture.group_id].assignments[0].clone();
    fixture
        .control
        .groups
        .get_mut(&fixture.group_id)
        .unwrap()
        .assignments
        .push(RoleAssignment::new(
            assignment.role_id().clone(),
            assignment.node_id().clone(),
            Vec::new(),
        ));
    assert!(
        ControlPlane::restore(fixture.control.checkpoint()).is_err(),
        "duplicate logical Role is invalid even with no resources"
    );
    fixture
        .control
        .groups
        .get_mut(&fixture.group_id)
        .unwrap()
        .assignments
        .pop();
    fixture
        .control
        .groups
        .get_mut(&fixture.group_id)
        .unwrap()
        .unbound_roles
        .insert(
            assignment.role_id().clone(),
            crate::group::UnboundRole {
                previous_node_id: assignment.node_id().clone(),
                assignment_index: 0,
            },
        );
    assert!(
        ControlPlane::restore(fixture.control.checkpoint()).is_err(),
        "one Role cannot be simultaneously bound and unbound"
    );
}

/// Context ownership is one owner even when consecutive Tasks expose the same resource.
#[test]
fn context_reuse_model_keeps_one_owner_until_context_release() {
    let plan = mission_plan(domain::ResourceBindingScope::Context);
    let mut control = ControlPlane::new();
    let mut events = RecordingEvents::default();
    let group = ExecutionGroupId::new("context-model").unwrap();
    let resource = ResourceId::new("context-resource").unwrap();
    let node = NodeId::new("context-node").unwrap();
    let first = plan.task_graph().tasks()[0].requirement();
    let second = plan.task_graph().tasks()[1].requirement();
    control
        .create_mission_group(
            group.clone(),
            &plan,
            TimestampMs::new(0),
            &trace(),
            &mut events,
        )
        .unwrap();
    control.reservations.insert(
        resource.clone(),
        Reservation {
            topology: None,
            task_ref: first.task_ref().clone(),
            role_id: first.roles()[0].role_id().clone(),
            group_id: None,
            scope: domain::ResourceBindingScope::Context,
            owner: domain::AllocationOwner::Context {
                mission_id: first.mission_id().clone(),
                context_id: plan.contexts()[0].context_id().clone(),
                context_role_id: domain::ContextRoleId::new("worker").unwrap(),
            },
        },
    );
    for requirement in [first, second] {
        control
            .ready_task_execution(
                &group,
                requirement.task_ref(),
                TimestampMs::new(0),
                &trace(),
                &mut events,
            )
            .unwrap();
        control
            .bind_task_execution(
                &group,
                &CommittedPlan::new(
                    requirement.task_ref().clone(),
                    vec![RoleAssignment::new(
                        requirement.roles()[0].role_id().clone(),
                        node.clone(),
                        vec![resource.clone()],
                    )],
                ),
                TimestampMs::new(0),
                &trace(),
                &mut events,
            )
            .unwrap();
        let snapshot = control.allocation_snapshot(TimestampMs::new(0)).unwrap();
        assert_eq!(snapshot.allocations().len(), 1);
        assert!(matches!(
            snapshot.allocations()[0].owner(),
            domain::AllocationOwner::Context { .. }
        ));
    }
    let bound_nodes = control
        .group(&group)
        .unwrap()
        .task_executions()
        .flat_map(|task| {
            task.assignments()
                .iter()
                .map(|assignment| assignment.node_id())
        })
        .collect::<BTreeSet<_>>();
    assert_eq!(bound_nodes, BTreeSet::from([&node]));
    let before = serde_json::to_value(control.checkpoint()).unwrap();
    let count = events.records.len();
    assert!(
        control
            .begin_execution_recovery(
                &group,
                first.task_ref(),
                first.roles()[0].role_id(),
                &node,
                TimestampMs::new(0),
                &trace(),
                &mut events
            )
            .is_err()
    );
    assert_eq!(
        serde_json::to_value(control.checkpoint()).unwrap(),
        before,
        "unsupported Context recovery cannot partially block a Group"
    );
    assert_eq!(events.records.len(), count);
    control
        .release_context_bindings(
            &group,
            plan.contexts()[0].context_id(),
            TimestampMs::new(0),
            &trace(),
            &mut events,
        )
        .unwrap();
    assert!(
        control
            .allocation_snapshot(TimestampMs::new(0))
            .unwrap()
            .allocations()
            .is_empty()
    );
}

/// Zero-resource recovery cannot be restored onto a sibling bound Task or a different old owner.
#[test]
fn checkpoint_recovery_requires_exact_unbound_slot_and_previous_owner() {
    let mut fixture = ModelFixture::new(false);
    fixture.apply(Operation::Recover(0)).unwrap();
    fixture.apply(Operation::Commit(0)).unwrap();
    let checkpoint = serde_json::to_value(fixture.control.checkpoint()).unwrap();
    for (field, value) in [
        (
            "task_ref",
            serde_json::to_value(fixture.requirements[1].task_ref()).unwrap(),
        ),
        (
            "previous_node_id",
            serde_json::to_value(&fixture.nodes[1]).unwrap(),
        ),
    ] {
        let mut forged = checkpoint.clone();
        forged["pending_recovery_commitments"][0][field] = value;
        let parsed = serde_json::from_value(forged).unwrap();
        assert!(
            ControlPlane::restore(parsed).is_err(),
            "forged recovery {field} must fail closed even without resources"
        );
    }
}

/// Wrong Group, Role or previous owner is rejected before lifecycle or event mutation.
#[test]
fn execution_recovery_identity_rejections_are_atomic() {
    let mut fixture = ModelFixture::new(true);
    for (group, role, node) in [
        (
            ExecutionGroupId::new("missing").unwrap(),
            fixture.role.clone(),
            fixture.nodes[0].clone(),
        ),
        (
            fixture.group.clone(),
            RoleId::new("missing").unwrap(),
            fixture.nodes[0].clone(),
        ),
        (
            fixture.group.clone(),
            fixture.role.clone(),
            fixture.nodes[1].clone(),
        ),
    ] {
        let before = serde_json::to_value(fixture.control.checkpoint()).unwrap();
        let count = fixture.events.records.len();
        assert!(
            fixture
                .control
                .begin_execution_recovery(
                    &group,
                    fixture.requirements[0].task_ref(),
                    &role,
                    &node,
                    fixture.time(),
                    &trace(),
                    &mut fixture.events
                )
                .is_err()
        );
        assert_eq!(
            serde_json::to_value(fixture.control.checkpoint()).unwrap(),
            before
        );
        assert_eq!(fixture.events.records.len(), count);
    }
}
