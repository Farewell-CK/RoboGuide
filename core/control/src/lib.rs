#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! Global DEAIOS decision and coordination logic for the first vertical slice.
//!
//! This crate validates Proposal versus Commit, resource reservation, Execution
//! Group binding, and role rebinding. It never sends raw actuator commands.

use domain::{
    CorrelationId, EventPayload, ExecutionGroupId, NodeId, NodeRegistration, NodeStatus,
    ResourceId, RoleAssignment, RoleId, TaskId, TaskRequirement, TimestampMs,
};
use ports::EventSink;
use std::collections::BTreeMap;
use std::fmt::{Display, Formatter};

/// Candidate node identifiers for one task role.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RoleCandidates {
    /// Role for which the candidate nodes were produced.
    role_id: RoleId,
    /// Nodes that can satisfy the role in deterministic order.
    node_ids: Vec<NodeId>,
}

impl RoleCandidates {
    /// Creates a deterministic candidate list for one role.
    pub fn new(role_id: RoleId, node_ids: Vec<NodeId>) -> Self {
        Self { role_id, node_ids }
    }

    /// Returns the role being matched.
    pub fn role_id(&self) -> &RoleId {
        &self.role_id
    }

    /// Returns candidate nodes in stable registration order.
    pub fn node_ids(&self) -> &[NodeId] {
        &self.node_ids
    }
}

/// The complete Candidate Set produced by Capability Matching.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CandidateSet {
    /// Task for which matching was performed.
    task_id: TaskId,
    /// Candidate nodes grouped by required role.
    roles: Vec<RoleCandidates>,
}

impl CandidateSet {
    /// Creates a candidate set for one task.
    pub fn new(task_id: TaskId, roles: Vec<RoleCandidates>) -> Self {
        Self { task_id, roles }
    }

    /// Returns the matched task identity.
    pub fn task_id(&self) -> &TaskId {
        &self.task_id
    }

    /// Returns role-level candidates.
    pub fn roles(&self) -> &[RoleCandidates] {
        &self.roles
    }

    /// Returns candidates for one role, if that role was included.
    pub fn for_role(&self, role_id: &RoleId) -> Option<&RoleCandidates> {
        self.roles.iter().find(|role| role.role_id() == role_id)
    }
}

/// A scheduler proposal before shared resources are committed.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AssignmentProposal {
    /// Task represented by this proposal.
    task_id: TaskId,
    /// Proposed node and resource assignments by role.
    assignments: Vec<RoleAssignment>,
}

impl AssignmentProposal {
    /// Creates a proposal after Control validates its role assignments.
    fn new(task_id: TaskId, assignments: Vec<RoleAssignment>) -> Self {
        Self {
            task_id,
            assignments,
        }
    }

    /// Returns the proposed task identity.
    pub fn task_id(&self) -> &TaskId {
        &self.task_id
    }

    /// Returns all proposed role assignments.
    pub fn assignments(&self) -> &[RoleAssignment] {
        &self.assignments
    }
}

/// A proposal whose resources are now system-recognized commitments.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CommittedPlan {
    /// Task represented by this committed plan.
    task_id: TaskId,
    /// Resource-checked assignments accepted by coordination.
    assignments: Vec<RoleAssignment>,
}

impl CommittedPlan {
    /// Creates a committed plan after reservation checks succeed.
    fn new(task_id: TaskId, assignments: Vec<RoleAssignment>) -> Self {
        Self {
            task_id,
            assignments,
        }
    }

    /// Returns the committed task identity.
    pub fn task_id(&self) -> &TaskId {
        &self.task_id
    }

    /// Returns committed role assignments.
    pub fn assignments(&self) -> &[RoleAssignment] {
        &self.assignments
    }
}

/// Lifecycle states for the task-level Execution Group.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum GroupLifecycle {
    /// The group exists with committed member/resource bindings.
    Bound,
    /// At least one role has begun execution.
    Active,
    /// The group adapted after a recoverable deviation.
    Adapted,
    /// All assigned roles completed.
    Completed,
    /// The group cannot safely continue.
    Blocked,
}

/// A dynamic group of members, roles, and resource bindings.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ExecutionGroup {
    /// Dynamic execution-group identity.
    group_id: ExecutionGroupId,
    /// Task owned by the group.
    task_id: TaskId,
    /// Current role, member, and resource bindings.
    assignments: Vec<RoleAssignment>,
    /// Lifecycle state used by adaptation and recovery.
    lifecycle: GroupLifecycle,
}

impl ExecutionGroup {
    /// Creates a group from a committed plan.
    fn new(group_id: ExecutionGroupId, plan: &CommittedPlan) -> Self {
        Self {
            group_id,
            task_id: plan.task_id().clone(),
            assignments: plan.assignments().to_vec(),
            lifecycle: GroupLifecycle::Bound,
        }
    }

    /// Returns the group identity.
    pub fn group_id(&self) -> &ExecutionGroupId {
        &self.group_id
    }

    /// Returns the task owned by this group.
    pub fn task_id(&self) -> &TaskId {
        &self.task_id
    }

    /// Returns member-role-resource bindings.
    pub fn assignments(&self) -> &[RoleAssignment] {
        &self.assignments
    }

    /// Returns the current group lifecycle.
    pub const fn lifecycle(&self) -> GroupLifecycle {
        self.lifecycle
    }
}

/// Errors raised by global matching, coordination, and group lifecycle logic.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ControlError {
    /// A referenced node was not registered.
    UnknownNode(NodeId),
    /// A referenced group was not found.
    UnknownGroup(ExecutionGroupId),
    /// A role had no node satisfying its requirements.
    NoCandidate(RoleId),
    /// A proposal did not match its Candidate Set or task requirements.
    InvalidProposal(String),
    /// A resource was already committed by another task or role.
    ResourceConflict {
        /// Resource that could not be committed.
        resource_id: ResourceId,
        /// Task currently holding the resource.
        owner_task_id: TaskId,
        /// Role currently holding the resource.
        owner_role_id: RoleId,
    },
    /// A group lifecycle transition was invalid.
    InvalidLifecycle(GroupLifecycle),
}

impl Display for ControlError {
    /// Formats a control rejection for event evidence and diagnostics.
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::UnknownNode(node_id) => write!(formatter, "unknown node {node_id}"),
            Self::UnknownGroup(group_id) => write!(formatter, "unknown execution group {group_id}"),
            Self::NoCandidate(role_id) => write!(formatter, "no candidate for role {role_id}"),
            Self::InvalidProposal(reason) => write!(formatter, "invalid proposal: {reason}"),
            Self::ResourceConflict {
                resource_id,
                owner_task_id,
                owner_role_id,
            } => write!(
                formatter,
                "resource conflict: {resource_id} held by task {owner_task_id}, role {owner_role_id}"
            ),
            Self::InvalidLifecycle(lifecycle) => {
                write!(formatter, "invalid lifecycle: {lifecycle:?}")
            }
        }
    }
}

impl std::error::Error for ControlError {}

/// Global control state for registration, matching, commitment, and recovery.
#[derive(Debug, Default)]
pub struct ControlPlane {
    /// Registered nodes and their shared health snapshots.
    nodes: BTreeMap<NodeId, RegisteredNode>,
    /// Resources currently held by committed task roles.
    reservations: BTreeMap<ResourceId, Reservation>,
    /// Dynamic execution groups known to the control plane.
    groups: BTreeMap<ExecutionGroupId, ExecutionGroup>,
}

/// A node registration plus its latest shared health view.
#[derive(Debug, Clone)]
struct RegisteredNode {
    /// Capability, resource, and local-runtime declaration.
    registration: NodeRegistration,
    /// Latest health state used by global matching.
    status: NodeStatus,
}

/// The task and role that currently hold a resource commitment.
#[derive(Debug, Clone)]
struct Reservation {
    /// Task currently holding the resource.
    task_id: TaskId,
    /// Role currently holding the resource.
    role_id: RoleId,
}

impl ControlPlane {
    /// Creates an empty control plane.
    pub fn new() -> Self {
        Self::default()
    }

    /// Registers or refreshes one logical node and records its visibility.
    pub fn register_node<E: EventSink>(
        &mut self,
        registration: NodeRegistration,
        status: NodeStatus,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) {
        let node_id = registration.node_id().clone();
        self.nodes.insert(
            node_id.clone(),
            RegisteredNode {
                registration,
                status,
            },
        );
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::NodeRegistered { node_id },
        );
    }

    /// Matches every task role against currently schedulable node capabilities.
    pub fn match_capabilities<E: EventSink>(
        &self,
        requirement: &TaskRequirement,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<CandidateSet, ControlError> {
        let mut roles = Vec::with_capacity(requirement.roles().len());
        for role in requirement.roles() {
            let node_ids = self
                .nodes
                .iter()
                .filter(|(_, node)| {
                    node.status.health().is_schedulable() && node.registration.supports_role(role)
                })
                .map(|(node_id, _)| node_id.clone())
                .collect::<Vec<_>>();
            if node_ids.is_empty() {
                return Err(ControlError::NoCandidate(role.role_id().clone()));
            }
            roles.push(RoleCandidates::new(role.role_id().clone(), node_ids));
        }

        let candidates = CandidateSet::new(requirement.task_id().clone(), roles);
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::CandidatesMatched {
                task_id: requirement.task_id().clone(),
            },
        );
        Ok(candidates)
    }

    /// Validates a scheduler's role assignments without committing resources.
    pub fn propose<E: EventSink>(
        &self,
        requirement: &TaskRequirement,
        candidates: &CandidateSet,
        assignments: Vec<RoleAssignment>,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<AssignmentProposal, ControlError> {
        if candidates.task_id() != requirement.task_id() {
            return Err(ControlError::InvalidProposal(
                "candidate set belongs to another task".to_string(),
            ));
        }
        if assignments.len() != requirement.roles().len() {
            return Err(ControlError::InvalidProposal(
                "proposal must assign every role exactly once".to_string(),
            ));
        }

        for role in requirement.roles() {
            let assignment = assignments
                .iter()
                .find(|assignment| assignment.role_id() == role.role_id())
                .ok_or_else(|| {
                    ControlError::InvalidProposal(format!("missing role {}", role.role_id()))
                })?;
            let role_candidates = candidates.for_role(role.role_id()).ok_or_else(|| {
                ControlError::InvalidProposal(format!(
                    "missing candidates for role {}",
                    role.role_id()
                ))
            })?;
            if !role_candidates.node_ids().contains(assignment.node_id()) {
                return Err(ControlError::InvalidProposal(format!(
                    "node {} is not a candidate for role {}",
                    assignment.node_id(),
                    role.role_id()
                )));
            }
            let node = self
                .nodes
                .get(assignment.node_id())
                .ok_or_else(|| ControlError::UnknownNode(assignment.node_id().clone()))?;
            if !node.registration.supports_role(role) {
                return Err(ControlError::InvalidProposal(format!(
                    "node {} no longer satisfies role {}",
                    assignment.node_id(),
                    role.role_id()
                )));
            }
            if assignment.resource_ids().iter().any(|resource_id| {
                !node
                    .registration
                    .owns_resource(resource_id, role.resource_kind())
            }) {
                return Err(ControlError::InvalidProposal(format!(
                    "role {} references a resource it does not own",
                    role.role_id()
                )));
            }
        }

        let proposal = AssignmentProposal::new(requirement.task_id().clone(), assignments);
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::ProposalCreated {
                task_id: requirement.task_id().clone(),
            },
        );
        Ok(proposal)
    }

    /// Commits all proposal resources atomically from the control-plane view.
    pub fn commit<E: EventSink>(
        &mut self,
        proposal: &AssignmentProposal,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<CommittedPlan, ControlError> {
        for assignment in proposal.assignments() {
            for resource_id in assignment.resource_ids() {
                if let Some(reservation) = self.reservations.get(resource_id) {
                    return Err(ControlError::ResourceConflict {
                        resource_id: resource_id.clone(),
                        owner_task_id: reservation.task_id.clone(),
                        owner_role_id: reservation.role_id.clone(),
                    });
                }
            }
        }

        for assignment in proposal.assignments() {
            for resource_id in assignment.resource_ids() {
                self.reservations.insert(
                    resource_id.clone(),
                    Reservation {
                        task_id: proposal.task_id().clone(),
                        role_id: assignment.role_id().clone(),
                    },
                );
            }
        }

        let plan = CommittedPlan::new(proposal.task_id().clone(), proposal.assignments().to_vec());
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::PlanCommitted {
                task_id: proposal.task_id().clone(),
            },
        );
        Ok(plan)
    }

    /// Creates and binds an Execution Group from a committed plan.
    pub fn create_group<E: EventSink>(
        &mut self,
        group_id: ExecutionGroupId,
        plan: &CommittedPlan,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<ExecutionGroup, ControlError> {
        if self.groups.contains_key(&group_id) {
            return Err(ControlError::InvalidProposal(
                "execution group identity already exists".to_string(),
            ));
        }
        let group = ExecutionGroup::new(group_id.clone(), plan);
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::ExecutionGroupBound {
                group_id: group_id.clone(),
                task_id: plan.task_id().clone(),
            },
        );
        self.groups.insert(group_id, group.clone());
        Ok(group)
    }

    /// Rebinds one group role to a replacement node after a recoverable failure.
    ///
    /// Recovery inputs remain separate so the event trace preserves the exact
    /// replacement node, resource set, time, correlation, and evidence sink.
    #[allow(clippy::too_many_arguments)]
    pub fn rebind_role<E: EventSink>(
        &mut self,
        group_id: &ExecutionGroupId,
        role: &RoleRequirementView,
        replacement_node_id: NodeId,
        replacement_resources: Vec<ResourceId>,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<(), ControlError> {
        let replacement = self
            .nodes
            .get(&replacement_node_id)
            .ok_or_else(|| ControlError::UnknownNode(replacement_node_id.clone()))?;
        if !replacement.registration.supports_role(role.requirement()) {
            return Err(ControlError::InvalidProposal(format!(
                "replacement node {} cannot satisfy role {}",
                replacement_node_id,
                role.role_id()
            )));
        }
        if replacement_resources.iter().any(|resource_id| {
            !replacement
                .registration
                .owns_resource(resource_id, role.requirement().resource_kind())
                || self.reservations.contains_key(resource_id)
        }) {
            return Err(ControlError::InvalidProposal(
                "replacement resources are invalid or already committed".to_string(),
            ));
        }

        let group = self
            .groups
            .get_mut(group_id)
            .ok_or_else(|| ControlError::UnknownGroup(group_id.clone()))?;
        let assignment = group
            .assignments
            .iter_mut()
            .find(|assignment| assignment.role_id() == role.role_id())
            .ok_or_else(|| {
                ControlError::InvalidProposal(format!("group has no role {}", role.role_id()))
            })?;
        let previous_node = assignment.node_id().clone();
        for resource_id in assignment.resource_ids() {
            self.reservations.remove(resource_id);
        }
        for resource_id in &replacement_resources {
            self.reservations.insert(
                resource_id.clone(),
                Reservation {
                    task_id: group.task_id.clone(),
                    role_id: role.role_id().clone(),
                },
            );
        }
        *assignment = RoleAssignment::new(
            role.role_id().clone(),
            replacement_node_id.clone(),
            replacement_resources,
        );
        group.lifecycle = GroupLifecycle::Adapted;
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::RecoveryRebound {
                group_id: group_id.clone(),
                role_id: role.role_id().clone(),
                from_node: previous_node,
                to_node: replacement_node_id,
            },
        );
        Ok(())
    }

    /// Marks a group complete after all required role executions succeed.
    pub fn complete_group<E: EventSink>(
        &mut self,
        group_id: &ExecutionGroupId,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<(), ControlError> {
        let group = self
            .groups
            .get_mut(group_id)
            .ok_or_else(|| ControlError::UnknownGroup(group_id.clone()))?;
        if group.lifecycle == GroupLifecycle::Blocked {
            return Err(ControlError::InvalidLifecycle(group.lifecycle));
        }
        group.lifecycle = GroupLifecycle::Completed;
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::ExecutionGroupCompleted {
                group_id: group_id.clone(),
            },
        );
        Ok(())
    }

    /// Marks a group blocked when local safety or recovery evidence is insufficient.
    pub fn block_group<E: EventSink>(
        &mut self,
        group_id: &ExecutionGroupId,
        reason: impl Into<String>,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<(), ControlError> {
        let group = self
            .groups
            .get_mut(group_id)
            .ok_or_else(|| ControlError::UnknownGroup(group_id.clone()))?;
        group.lifecycle = GroupLifecycle::Blocked;
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::ExecutionGroupBlocked {
                group_id: group_id.clone(),
                reason: reason.into(),
            },
        );
        Ok(())
    }

    /// Returns the current group snapshot for assertions and adapters.
    pub fn group(&self, group_id: &ExecutionGroupId) -> Option<&ExecutionGroup> {
        self.groups.get(group_id)
    }
}

/// A narrow role view used by recovery adapters without exposing the task object.
#[derive(Debug, Clone)]
pub struct RoleRequirementView {
    /// Role requirement exposed to recovery validation.
    requirement: domain::RoleRequirement,
}

impl RoleRequirementView {
    /// Creates a recovery view from a role requirement.
    pub fn new(requirement: domain::RoleRequirement) -> Self {
        Self { requirement }
    }

    /// Returns the role identity.
    pub fn role_id(&self) -> &RoleId {
        self.requirement.role_id()
    }

    /// Returns the wrapped requirement for capability validation.
    pub fn requirement(&self) -> &domain::RoleRequirement {
        &self.requirement
    }
}
