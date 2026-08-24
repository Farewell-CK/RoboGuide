//! Stateless deterministic bootstrap policy for Control-owned node selection.
//!
//! The policy consumes Candidate Sets produced by Capability Matching and
//! returns selection evidence. It does not re-evaluate eligibility, validate
//! proposals, inspect reservations, commit resources, or mutate Groups/State.

use super::{CandidateSet, RecoveryCandidateSet};
use domain::{
    CorrelationId, EventPayload, ExecutionGroupId, NodeId, ResourceId, RoleAssignment, RoleId,
    RoleRequirement, TaskRef, TaskRequirement, TimestampMs,
};
use ports::{EventSink, SharedNodeStateReader};
use std::collections::BTreeSet;
use std::fmt::{Display, Formatter};

/// Failures produced while forming a deterministic scheduling decision.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SchedulerError {
    /// Candidate context did not match the supplied task or role requirements.
    InvalidCandidateSet(String),
    /// A Candidate Set referenced a node absent from Shared Node State.
    UnknownCandidate(NodeId),
    /// No candidate could provide an unused declared resource for one role.
    NoFeasibleSelection(RoleId),
}

impl Display for SchedulerError {
    /// Formats a scheduler boundary failure without claiming recovery exhaustion.
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::InvalidCandidateSet(reason) => {
                write!(formatter, "invalid scheduler candidate set: {reason}")
            }
            Self::UnknownCandidate(node_id) => {
                write!(
                    formatter,
                    "scheduler candidate {node_id} is absent from Shared State"
                )
            }
            Self::NoFeasibleSelection(role_id) => {
                write!(
                    formatter,
                    "no feasible deterministic selection for role {role_id}"
                )
            }
        }
    }
}

impl std::error::Error for SchedulerError {}

/// One Scheduler-selected role, node, and non-authoritative resource suggestion.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RoleSchedulingSelection {
    /// Role receiving the selection.
    role_id: RoleId,
    /// Candidate node selected by the bootstrap policy.
    node_id: NodeId,
    /// Declared resources suggested for later proposal validation.
    resource_ids: Vec<ResourceId>,
}

impl RoleSchedulingSelection {
    /// Creates one internal role selection from the shared policy primitive.
    fn new(role_id: RoleId, node_id: NodeId, resource_ids: Vec<ResourceId>) -> Self {
        Self {
            role_id,
            node_id,
            resource_ids,
        }
    }

    /// Returns the selected role.
    pub const fn role_id(&self) -> &RoleId {
        &self.role_id
    }

    /// Returns the selected candidate node.
    pub const fn node_id(&self) -> &NodeId {
        &self.node_id
    }

    /// Returns non-authoritative proposed resource IDs.
    pub fn resource_ids(&self) -> &[ResourceId] {
        &self.resource_ids
    }

    /// Converts selection evidence into the existing proposal-validation input type.
    fn to_role_assignment(&self) -> RoleAssignment {
        RoleAssignment::new(
            self.role_id.clone(),
            self.node_id.clone(),
            self.resource_ids.clone(),
        )
    }
}

/// Complete normal-task selection evidence produced before proposal validation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TaskSchedulingDecision {
    /// Mission-scoped task represented by the decision.
    task_ref: TaskRef,
    /// Role selections in TaskRequirement declaration order.
    selections: Vec<RoleSchedulingSelection>,
}

impl TaskSchedulingDecision {
    /// Creates a complete normal-task decision from deterministic role selections.
    fn new(task_ref: TaskRef, selections: Vec<RoleSchedulingSelection>) -> Self {
        Self {
            task_ref,
            selections,
        }
    }

    /// Returns the mission-scoped task represented by this decision.
    pub const fn task_ref(&self) -> &TaskRef {
        &self.task_ref
    }

    /// Returns role selections in requirement declaration order.
    pub fn selections(&self) -> &[RoleSchedulingSelection] {
        &self.selections
    }

    /// Builds fresh proposal-validation inputs without granting proposal authority.
    pub fn proposed_assignments(&self) -> Vec<RoleAssignment> {
        self.selections
            .iter()
            .map(RoleSchedulingSelection::to_role_assignment)
            .collect()
    }
}

/// Role-scoped recovery selection evidence produced before recovery proposal validation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RecoverySchedulingDecision {
    /// Existing Group awaiting recovery.
    group_id: ExecutionGroupId,
    /// Mission-scoped task retained by the Group.
    task_ref: TaskRef,
    /// Single unbound role selected in this decision.
    role_id: RoleId,
    /// Failed node excluded by Recovery Matching.
    previous_node_id: NodeId,
    /// Candidate replacement selected by the bootstrap policy.
    replacement_node_id: NodeId,
    /// Non-authoritative resources suggested for proposal validation.
    resource_ids: Vec<ResourceId>,
}

impl RecoverySchedulingDecision {
    /// Creates one recovery decision from the shared role-selection primitive.
    fn new(
        group_id: ExecutionGroupId,
        task_ref: TaskRef,
        role_id: RoleId,
        previous_node_id: NodeId,
        replacement_node_id: NodeId,
        resource_ids: Vec<ResourceId>,
    ) -> Self {
        Self {
            group_id,
            task_ref,
            role_id,
            previous_node_id,
            replacement_node_id,
            resource_ids,
        }
    }

    /// Returns the existing Group awaiting the replacement.
    pub const fn group_id(&self) -> &ExecutionGroupId {
        &self.group_id
    }

    /// Returns the mission-scoped task retained by the Group.
    pub const fn task_ref(&self) -> &TaskRef {
        &self.task_ref
    }

    /// Returns the single recovery role represented by this decision.
    pub const fn role_id(&self) -> &RoleId {
        &self.role_id
    }

    /// Returns the failed node excluded from selection.
    pub const fn previous_node_id(&self) -> &NodeId {
        &self.previous_node_id
    }

    /// Returns the Scheduler-selected replacement node.
    pub const fn replacement_node_id(&self) -> &NodeId {
        &self.replacement_node_id
    }

    /// Returns non-authoritative proposed replacement resources.
    pub fn resource_ids(&self) -> &[ResourceId] {
        &self.resource_ids
    }
}

/// Recovery scheduling result that preserves empty candidates as non-terminal pending work.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RecoverySchedulingOutcome {
    /// No supplied candidate can currently form a deterministic selection.
    NoSelection,
    /// The bootstrap policy selected one recovery candidate and resources.
    Selected(RecoverySchedulingDecision),
}

/// Stateless stable-first Scheduler used to establish the Control selection boundary.
#[derive(Debug, Clone, Copy, Default)]
pub struct DeterministicBootstrapScheduler;

impl DeterministicBootstrapScheduler {
    /// Creates the stateless deterministic bootstrap policy.
    pub const fn new() -> Self {
        Self
    }

    /// Selects every normal task role without validating or committing a proposal.
    pub fn schedule_task<S: SharedNodeStateReader, E: EventSink>(
        &self,
        state: &S,
        requirement: &TaskRequirement,
        candidates: &CandidateSet,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<TaskSchedulingDecision, SchedulerError> {
        if candidates.task_ref() != requirement.task_ref() {
            return Err(SchedulerError::InvalidCandidateSet(
                "normal candidates belong to another task".to_string(),
            ));
        }
        let mut selected_resources = BTreeSet::new();
        let mut selections = Vec::with_capacity(requirement.roles().len());
        for role in requirement.roles() {
            let role_candidates = candidates.for_role(role.role_id()).ok_or_else(|| {
                SchedulerError::InvalidCandidateSet(format!(
                    "normal candidates omit role {}",
                    role.role_id()
                ))
            })?;
            let selection =
                select_role(state, role, role_candidates.node_ids(), &selected_resources)?
                    .ok_or_else(|| SchedulerError::NoFeasibleSelection(role.role_id().clone()))?;
            selected_resources.extend(selection.resource_ids().iter().cloned());
            selections.push(selection);
        }
        let decision = TaskSchedulingDecision::new(requirement.task_ref().clone(), selections);
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::TaskSchedulingSelected {
                task_ref: requirement.task_ref().clone(),
                assignments: decision.proposed_assignments(),
            },
        );
        Ok(decision)
    }

    /// Selects only the role represented by a Recovery Candidate Set.
    pub fn schedule_recovery<S: SharedNodeStateReader, E: EventSink>(
        &self,
        state: &S,
        requirement: &TaskRequirement,
        candidates: &RecoveryCandidateSet,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<RecoverySchedulingOutcome, SchedulerError> {
        if candidates.task_ref() != requirement.task_ref() {
            return Err(SchedulerError::InvalidCandidateSet(
                "recovery candidates belong to another task".to_string(),
            ));
        }
        let role = requirement
            .roles()
            .iter()
            .find(|role| role.role_id() == candidates.role_id())
            .ok_or_else(|| {
                SchedulerError::InvalidCandidateSet(format!(
                    "task requirement omits recovery role {}",
                    candidates.role_id()
                ))
            })?;
        let selection = select_role(
            state,
            role,
            candidates.candidate_node_ids(),
            &BTreeSet::new(),
        )?;
        let Some(selection) = selection else {
            events.append(
                timestamp,
                correlation_id,
                None,
                EventPayload::RecoverySchedulingNoSelection {
                    group_id: candidates.group_id().clone(),
                    task_ref: candidates.task_ref().clone(),
                    role_id: candidates.role_id().clone(),
                },
            );
            return Ok(RecoverySchedulingOutcome::NoSelection);
        };
        let decision = RecoverySchedulingDecision::new(
            candidates.group_id().clone(),
            candidates.task_ref().clone(),
            candidates.role_id().clone(),
            candidates.previous_node_id().clone(),
            selection.node_id().clone(),
            selection.resource_ids().to_vec(),
        );
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::RecoverySchedulingSelected {
                group_id: candidates.group_id().clone(),
                task_ref: candidates.task_ref().clone(),
                role_id: candidates.role_id().clone(),
                previous_node_id: candidates.previous_node_id().clone(),
                replacement_node_id: decision.replacement_node_id().clone(),
                resource_ids: decision.resource_ids().to_vec(),
            },
        );
        Ok(RecoverySchedulingOutcome::Selected(decision))
    }
}

/// Applies the shared stable-first node/resource policy to one role and Candidate Set.
fn select_role<S: SharedNodeStateReader>(
    state: &S,
    role: &RoleRequirement,
    candidate_node_ids: &[NodeId],
    selected_resources: &BTreeSet<ResourceId>,
) -> Result<Option<RoleSchedulingSelection>, SchedulerError> {
    let mut stable_candidates = candidate_node_ids.to_vec();
    stable_candidates.sort();
    stable_candidates.dedup();
    for node_id in stable_candidates {
        let node = state
            .node(&node_id)
            .ok_or_else(|| SchedulerError::UnknownCandidate(node_id.clone()))?;
        let resource_ids = if let Some(resource_kind) = role.resource_kind() {
            let mut declared = node.registration().resource_ids_of_kind(resource_kind);
            declared.sort();
            let Some(resource_id) = declared
                .into_iter()
                .find(|resource_id| !selected_resources.contains(resource_id))
            else {
                continue;
            };
            vec![resource_id]
        } else {
            vec![]
        };
        return Ok(Some(RoleSchedulingSelection::new(
            role.role_id().clone(),
            node_id,
            resource_ids,
        )));
    }
    Ok(None)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{ControlError, ControlPlane, RoleCandidates};
    use domain::{
        Capability, CapabilityKind, EventId, LocalRuntime, MissionId, NodeHealth, NodeLiveness,
        NodeLivenessObservation, NodeRegistration, NodeStateSnapshot, NodeStatus, Resource,
        ResourceKind, RoleRequirement, TaskId,
    };
    use ports::{SharedNodeStateWriter, SharedStateError};
    use state::InMemorySharedNodeState;

    /// Captures scheduler evidence without introducing transport or persistence.
    #[derive(Default)]
    struct TestEvents {
        /// Payloads appended by scheduling and downstream validation.
        payloads: Vec<EventPayload>,
    }

    impl EventSink for TestEvents {
        /// Appends one deterministic payload while ignoring generated record metadata.
        fn append(
            &mut self,
            _timestamp: TimestampMs,
            _correlation_id: &CorrelationId,
            _causation_id: Option<&EventId>,
            payload: EventPayload,
        ) {
            self.payloads.push(payload);
        }
    }

    /// Builds one node registration with explicit capability and resource declarations.
    fn registration(
        node_id: &str,
        capability: CapabilityKind,
        resources: Vec<(&str, ResourceKind)>,
    ) -> NodeRegistration {
        NodeRegistration::new(
            NodeId::new(node_id).expect("test node id must be valid"),
            LocalRuntime::new("scheduler-test-runtime", "0.1.0")
                .expect("test runtime must be valid"),
            vec![Capability::new(capability, true)],
            resources
                .into_iter()
                .map(|(resource_id, kind)| {
                    Resource::new(
                        ResourceId::new(resource_id).expect("test resource id must be valid"),
                        kind,
                        1,
                    )
                    .expect("test resource must be valid")
                })
                .collect(),
        )
    }

    /// Records one healthy reachable node snapshot for scheduler-only tests.
    fn record_node(
        state: &mut InMemorySharedNodeState,
        registration: NodeRegistration,
    ) -> Result<(), SharedStateError> {
        state.record_node(NodeStateSnapshot::new(
            registration,
            NodeStatus::new(NodeHealth::Online, TimestampMs::new(0)),
            TimestampMs::new(0),
            NodeLivenessObservation::new(NodeLiveness::Reachable, TimestampMs::new(0)),
        ))
    }

    /// Builds one TaskRequirement with the supplied roles in declaration order.
    fn requirement(mission: &str, task: &str, roles: Vec<RoleRequirement>) -> TaskRequirement {
        TaskRequirement::new(
            MissionId::new(mission).expect("test mission id must be valid"),
            TaskId::new(task).expect("test task id must be valid"),
            roles,
        )
        .expect("test requirement must be valid")
    }

    /// Creates the common deterministic scheduler correlation identity.
    fn correlation() -> CorrelationId {
        CorrelationId::new("scheduler-test-trace").expect("test correlation id must be valid")
    }

    /// Stable node ordering produces the same decision across repeated calls.
    #[test]
    fn normal_scheduler_is_stable_and_repeatable() {
        let mut state = InMemorySharedNodeState::new();
        for node_id in ["node-c", "node-a", "node-b"] {
            record_node(
                &mut state,
                registration(
                    node_id,
                    CapabilityKind::Transport,
                    vec![(
                        match node_id {
                            "node-a" => "space-a",
                            "node-b" => "space-b",
                            _ => "space-c",
                        },
                        ResourceKind::Space,
                    )],
                ),
            )
            .expect("test node snapshot should be accepted");
        }
        let role_id = RoleId::new("transport").expect("test role id must be valid");
        let task = requirement(
            "mission-a",
            "task-a",
            vec![RoleRequirement::new(
                role_id.clone(),
                CapabilityKind::Transport,
                Some(ResourceKind::Space),
            )],
        );
        let candidates = CandidateSet::new(
            task.task_ref().clone(),
            vec![RoleCandidates::new(
                role_id,
                vec![
                    NodeId::new("node-c").expect("test node id must be valid"),
                    NodeId::new("node-a").expect("test node id must be valid"),
                    NodeId::new("node-b").expect("test node id must be valid"),
                ],
            )],
        );
        let scheduler = DeterministicBootstrapScheduler::new();
        let mut events = TestEvents::default();
        let first = scheduler
            .schedule_task(
                &state,
                &task,
                &candidates,
                TimestampMs::new(0),
                &correlation(),
                &mut events,
            )
            .expect("first deterministic decision should succeed");
        let second = scheduler
            .schedule_task(
                &state,
                &task,
                &candidates,
                TimestampMs::new(0),
                &correlation(),
                &mut events,
            )
            .expect("second deterministic decision should succeed");

        assert_eq!(first, second);
        assert_eq!(first.selections()[0].node_id().as_str(), "node-a");
        assert_eq!(first.selections()[0].resource_ids()[0].as_str(), "space-a");
    }

    /// Nodes absent from CandidateSet are never selected even when present in State.
    #[test]
    fn scheduler_never_bypasses_candidate_set() {
        let mut state = InMemorySharedNodeState::new();
        for (node_id, resource_id) in [("node-a", "space-a"), ("node-b", "space-b")] {
            record_node(
                &mut state,
                registration(
                    node_id,
                    CapabilityKind::Transport,
                    vec![(resource_id, ResourceKind::Space)],
                ),
            )
            .expect("test node snapshot should be accepted");
        }
        let role_id = RoleId::new("transport").expect("test role id must be valid");
        let task = requirement(
            "mission-a",
            "task-a",
            vec![RoleRequirement::new(
                role_id.clone(),
                CapabilityKind::Transport,
                Some(ResourceKind::Space),
            )],
        );
        let candidates = CandidateSet::new(
            task.task_ref().clone(),
            vec![RoleCandidates::new(
                role_id,
                vec![NodeId::new("node-b").expect("test node id must be valid")],
            )],
        );
        let mut events = TestEvents::default();
        let decision = DeterministicBootstrapScheduler::new()
            .schedule_task(
                &state,
                &task,
                &candidates,
                TimestampMs::new(0),
                &correlation(),
                &mut events,
            )
            .expect("candidate-only decision should succeed");

        assert_eq!(decision.selections()[0].node_id().as_str(), "node-b");
    }

    /// Resource choice is stable and selects only one declared resource of the required kind.
    #[test]
    fn scheduler_selects_stable_minimal_resource() {
        let mut state = InMemorySharedNodeState::new();
        record_node(
            &mut state,
            registration(
                "node-a",
                CapabilityKind::Transport,
                vec![
                    ("space-z", ResourceKind::Space),
                    ("space-a", ResourceKind::Space),
                    ("space-b", ResourceKind::Space),
                ],
            ),
        )
        .expect("test node snapshot should be accepted");
        let role_id = RoleId::new("transport").expect("test role id must be valid");
        let task = requirement(
            "mission-a",
            "task-a",
            vec![RoleRequirement::new(
                role_id.clone(),
                CapabilityKind::Transport,
                Some(ResourceKind::Space),
            )],
        );
        let candidates = CandidateSet::new(
            task.task_ref().clone(),
            vec![RoleCandidates::new(
                role_id,
                vec![NodeId::new("node-a").expect("test node id must be valid")],
            )],
        );
        let mut events = TestEvents::default();
        let decision = DeterministicBootstrapScheduler::new()
            .schedule_task(
                &state,
                &task,
                &candidates,
                TimestampMs::new(0),
                &correlation(),
                &mut events,
            )
            .expect("resource decision should succeed");

        assert_eq!(decision.selections()[0].resource_ids().len(), 1);
        assert_eq!(
            decision.selections()[0].resource_ids()[0].as_str(),
            "space-a"
        );
    }

    /// A role without ResourceKind produces an empty resource suggestion.
    #[test]
    fn scheduler_supports_resource_free_role() {
        let mut state = InMemorySharedNodeState::new();
        record_node(
            &mut state,
            registration("node-a", CapabilityKind::Observation, vec![]),
        )
        .expect("test node snapshot should be accepted");
        let role_id = RoleId::new("observe").expect("test role id must be valid");
        let task = requirement(
            "mission-a",
            "task-a",
            vec![RoleRequirement::new(
                role_id.clone(),
                CapabilityKind::Observation,
                None,
            )],
        );
        let candidates = CandidateSet::new(
            task.task_ref().clone(),
            vec![RoleCandidates::new(
                role_id,
                vec![NodeId::new("node-a").expect("test node id must be valid")],
            )],
        );
        let mut events = TestEvents::default();
        let decision = DeterministicBootstrapScheduler::new()
            .schedule_task(
                &state,
                &task,
                &candidates,
                TimestampMs::new(0),
                &correlation(),
                &mut events,
            )
            .expect("resource-free decision should succeed");

        assert!(decision.selections()[0].resource_ids().is_empty());
    }

    /// Multi-role scheduling avoids duplicate exclusive resources without backtracking.
    #[test]
    fn scheduler_avoids_duplicate_resource_within_decision() {
        let mut state = InMemorySharedNodeState::new();
        for (node_id, resource_id) in [("node-a", "space-a"), ("node-b", "space-b")] {
            record_node(
                &mut state,
                registration(
                    node_id,
                    CapabilityKind::Transport,
                    vec![(resource_id, ResourceKind::Space)],
                ),
            )
            .expect("test node snapshot should be accepted");
        }
        let first_role = RoleId::new("transport-a").expect("test role id must be valid");
        let second_role = RoleId::new("transport-b").expect("test role id must be valid");
        let task = requirement(
            "mission-a",
            "task-a",
            vec![
                RoleRequirement::new(
                    first_role.clone(),
                    CapabilityKind::Transport,
                    Some(ResourceKind::Space),
                ),
                RoleRequirement::new(
                    second_role.clone(),
                    CapabilityKind::Transport,
                    Some(ResourceKind::Space),
                ),
            ],
        );
        let candidate_nodes = vec![
            NodeId::new("node-a").expect("test node id must be valid"),
            NodeId::new("node-b").expect("test node id must be valid"),
        ];
        let candidates = CandidateSet::new(
            task.task_ref().clone(),
            vec![
                RoleCandidates::new(first_role, candidate_nodes.clone()),
                RoleCandidates::new(second_role, candidate_nodes),
            ],
        );
        let mut events = TestEvents::default();
        let decision = DeterministicBootstrapScheduler::new()
            .schedule_task(
                &state,
                &task,
                &candidates,
                TimestampMs::new(0),
                &correlation(),
                &mut events,
            )
            .expect("multi-role decision should succeed");

        assert_eq!(decision.selections()[0].node_id().as_str(), "node-a");
        assert_eq!(decision.selections()[1].node_id().as_str(), "node-b");
        assert_ne!(
            decision.selections()[0].resource_ids(),
            decision.selections()[1].resource_ids()
        );
    }

    /// Resource reuse constraints return a typed error when no simple selection is feasible.
    #[test]
    fn scheduler_reports_no_feasible_selection_without_backtracking() {
        let mut state = InMemorySharedNodeState::new();
        record_node(
            &mut state,
            registration(
                "node-a",
                CapabilityKind::Transport,
                vec![("space-a", ResourceKind::Space)],
            ),
        )
        .expect("test node snapshot should be accepted");
        let first_role = RoleId::new("transport-a").expect("test role id must be valid");
        let second_role = RoleId::new("transport-b").expect("test role id must be valid");
        let task = requirement(
            "mission-a",
            "task-a",
            vec![
                RoleRequirement::new(
                    first_role.clone(),
                    CapabilityKind::Transport,
                    Some(ResourceKind::Space),
                ),
                RoleRequirement::new(
                    second_role.clone(),
                    CapabilityKind::Transport,
                    Some(ResourceKind::Space),
                ),
            ],
        );
        let only_node = vec![NodeId::new("node-a").expect("test node id must be valid")];
        let candidates = CandidateSet::new(
            task.task_ref().clone(),
            vec![
                RoleCandidates::new(first_role, only_node.clone()),
                RoleCandidates::new(second_role.clone(), only_node),
            ],
        );
        let mut events = TestEvents::default();

        assert_eq!(
            DeterministicBootstrapScheduler::new().schedule_task(
                &state,
                &task,
                &candidates,
                TimestampMs::new(0),
                &correlation(),
                &mut events,
            ),
            Err(SchedulerError::NoFeasibleSelection(second_role))
        );
    }

    /// Normal and recovery scheduling share the same stable role-selection policy.
    #[test]
    fn normal_and_recovery_scheduling_are_policy_consistent() {
        let mut state = InMemorySharedNodeState::new();
        for (node_id, resource_id) in [("node-b", "space-b"), ("node-c", "space-c")] {
            record_node(
                &mut state,
                registration(
                    node_id,
                    CapabilityKind::Transport,
                    vec![(resource_id, ResourceKind::Space)],
                ),
            )
            .expect("test node snapshot should be accepted");
        }
        let role_id = RoleId::new("transport").expect("test role id must be valid");
        let task = requirement(
            "mission-a",
            "task-a",
            vec![RoleRequirement::new(
                role_id.clone(),
                CapabilityKind::Transport,
                Some(ResourceKind::Space),
            )],
        );
        let node_b = NodeId::new("node-b").expect("test node id must be valid");
        let node_c = NodeId::new("node-c").expect("test node id must be valid");
        let normal_candidates = CandidateSet::new(
            task.task_ref().clone(),
            vec![RoleCandidates::new(
                role_id.clone(),
                vec![node_c.clone(), node_b.clone()],
            )],
        );
        let recovery_candidates = RecoveryCandidateSet::new(
            ExecutionGroupId::new("group-a").expect("test group id must be valid"),
            task.task_ref().clone(),
            role_id,
            NodeId::new("node-a").expect("test node id must be valid"),
            vec![node_c, node_b],
        );
        let scheduler = DeterministicBootstrapScheduler::new();
        let mut events = TestEvents::default();
        let normal = scheduler
            .schedule_task(
                &state,
                &task,
                &normal_candidates,
                TimestampMs::new(0),
                &correlation(),
                &mut events,
            )
            .expect("normal decision should succeed");
        let recovery = scheduler
            .schedule_recovery(
                &state,
                &task,
                &recovery_candidates,
                TimestampMs::new(0),
                &correlation(),
                &mut events,
            )
            .expect("recovery decision should succeed");
        let RecoverySchedulingOutcome::Selected(recovery) = recovery else {
            panic!("recovery candidates should produce a selection");
        };

        assert_eq!(
            normal.selections()[0].node_id(),
            recovery.replacement_node_id()
        );
        assert_eq!(
            normal.selections()[0].resource_ids(),
            recovery.resource_ids()
        );
    }

    /// Empty recovery candidates return NoSelection without Group or authority mutation.
    #[test]
    fn recovery_scheduler_empty_candidates_return_no_selection() {
        let state = InMemorySharedNodeState::new();
        let role_id = RoleId::new("transport").expect("test role id must be valid");
        let task = requirement(
            "mission-a",
            "task-a",
            vec![RoleRequirement::new(
                role_id.clone(),
                CapabilityKind::Transport,
                Some(ResourceKind::Space),
            )],
        );
        let candidates = RecoveryCandidateSet::new(
            ExecutionGroupId::new("group-a").expect("test group id must be valid"),
            task.task_ref().clone(),
            role_id,
            NodeId::new("node-a").expect("test node id must be valid"),
            vec![],
        );
        let control = ControlPlane::new();
        let mut events = TestEvents::default();
        let outcome = DeterministicBootstrapScheduler::new()
            .schedule_recovery(
                &state,
                &task,
                &candidates,
                TimestampMs::new(0),
                &correlation(),
                &mut events,
            )
            .expect("empty recovery candidates are not an error");

        assert_eq!(outcome, RecoverySchedulingOutcome::NoSelection);
        assert!(control.reservations.is_empty());
        assert!(control.groups.is_empty());
        assert!(control.pending_recovery_commitments.is_empty());
        assert!(matches!(
            events.payloads.last(),
            Some(EventPayload::RecoverySchedulingNoSelection { .. })
        ));
    }

    /// Scheduler decision remains non-authoritative and Commit can reject it after Proposal.
    #[test]
    fn scheduler_decision_and_proposal_do_not_override_commit_conflict() {
        let registration = registration(
            "node-a",
            CapabilityKind::Transport,
            vec![("space-a", ResourceKind::Space)],
        );
        let node_id = registration.node_id().clone();
        let resource_id = ResourceId::new("space-a").expect("test resource id must be valid");
        let role_a = RoleId::new("transport-a").expect("test role id must be valid");
        let role_b = RoleId::new("transport-b").expect("test role id must be valid");
        let task_a = requirement(
            "mission-a",
            "task-a",
            vec![RoleRequirement::new(
                role_a.clone(),
                CapabilityKind::Transport,
                Some(ResourceKind::Space),
            )],
        );
        let task_b = requirement(
            "mission-b",
            "task-b",
            vec![RoleRequirement::new(
                role_b.clone(),
                CapabilityKind::Transport,
                Some(ResourceKind::Space),
            )],
        );
        let mut control = ControlPlane::new();
        let mut state = InMemorySharedNodeState::new();
        let mut events = TestEvents::default();
        control
            .register_node(
                &mut state,
                registration,
                NodeStatus::new(NodeHealth::Online, TimestampMs::new(0)),
                TimestampMs::new(0),
                &correlation(),
                &mut events,
            )
            .expect("test node should register");
        let candidates_a = control
            .match_capabilities(
                &state,
                &task_a,
                TimestampMs::new(0),
                &correlation(),
                &mut events,
            )
            .expect("Mission A should match");
        let candidates_b = control
            .match_capabilities(
                &state,
                &task_b,
                TimestampMs::new(0),
                &correlation(),
                &mut events,
            )
            .expect("Mission B should match");
        let state_before_scheduling = state
            .node(&node_id)
            .expect("scheduler node should remain in State")
            .clone();
        let scheduler = DeterministicBootstrapScheduler::new();
        let decision_a = scheduler
            .schedule_task(
                &state,
                &task_a,
                &candidates_a,
                TimestampMs::new(0),
                &correlation(),
                &mut events,
            )
            .expect("Mission A decision should succeed");
        let decision_b = scheduler
            .schedule_task(
                &state,
                &task_b,
                &candidates_b,
                TimestampMs::new(0),
                &correlation(),
                &mut events,
            )
            .expect("Mission B decision should succeed");
        assert!(control.reservations.is_empty());
        assert!(control.groups.is_empty());
        assert!(control.pending_recovery_commitments.is_empty());
        assert_eq!(decision_a.task_ref(), task_a.task_ref());
        assert_eq!(decision_b.task_ref(), task_b.task_ref());
        assert_eq!(
            decision_a.selections()[0].node_id(),
            decision_b.selections()[0].node_id()
        );
        assert_eq!(
            state.node(&node_id).expect("State must remain unchanged"),
            &state_before_scheduling
        );
        let proposal_a = control
            .propose(
                &state,
                &task_a,
                &candidates_a,
                decision_a.proposed_assignments(),
                TimestampMs::new(0),
                &correlation(),
                &mut events,
            )
            .expect("Mission A proposal should validate");
        let proposal_b = control
            .propose(
                &state,
                &task_b,
                &candidates_b,
                decision_b.proposed_assignments(),
                TimestampMs::new(0),
                &correlation(),
                &mut events,
            )
            .expect("Mission B proposal should validate");
        assert!(control.reservations.is_empty());
        control
            .commit(
                &proposal_b,
                TimestampMs::new(0),
                &correlation(),
                &mut events,
            )
            .expect("Mission B should commit first");

        assert!(matches!(
            control.commit(
                &proposal_a,
                TimestampMs::new(0),
                &correlation(),
                &mut events,
            ),
            Err(ControlError::ResourceConflict { resource_id: conflict, .. })
                if conflict == resource_id
        ));
        let reservation = control
            .reservations
            .get(&resource_id)
            .expect("Mission B reservation should remain");
        assert_eq!(reservation.role_id, role_b);
        assert_eq!(reservation.task_ref, *task_b.task_ref());
        assert_eq!(node_id.as_str(), "node-a");
        assert_eq!(role_a.as_str(), "transport-a");
    }
}
