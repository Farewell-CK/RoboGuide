#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! Global DEAIOS decision and coordination logic for the first vertical slice.
//!
//! This crate validates Proposal versus Commit, resource reservation, Execution
//! Group binding, and role rebinding. It never sends raw actuator commands.

use domain::{
    CorrelationId, EventPayload, ExecutionGroupId, LeaseId, NodeHeartbeat, NodeId, NodeLease,
    NodeRegistration, NodeStatus, ResourceId, RoleAssignment, RoleId, TaskId, TaskRef,
    TaskRequirement, TimestampMs,
};
use ports::EventSink;
use std::collections::BTreeMap;
use std::fmt::{Display, Formatter};

/// Default maximum age for a node status used by capability matching.
pub const DEFAULT_NODE_STATUS_TTL_MS: u64 = 5_000;

/// Default lease duration assigned by the convenience registration method.
pub const DEFAULT_NODE_LEASE_TTL_MS: u64 = 15_000;

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
    /// Mission-scoped task for which matching was performed.
    task_ref: TaskRef,
    /// Candidate nodes grouped by required role.
    roles: Vec<RoleCandidates>,
}

impl CandidateSet {
    /// Creates a candidate set for one task.
    pub fn new(task_ref: TaskRef, roles: Vec<RoleCandidates>) -> Self {
        Self { task_ref, roles }
    }

    /// Returns the complete mission-scoped task identity.
    pub const fn task_ref(&self) -> &TaskRef {
        &self.task_ref
    }

    /// Returns the matched task identity.
    pub const fn task_id(&self) -> &TaskId {
        self.task_ref.task_id()
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
    /// Mission-scoped task represented by this proposal.
    task_ref: TaskRef,
    /// Proposed node and resource assignments by role.
    assignments: Vec<RoleAssignment>,
}

impl AssignmentProposal {
    /// Creates a proposal after Control validates its role assignments.
    fn new(task_ref: TaskRef, assignments: Vec<RoleAssignment>) -> Self {
        Self {
            task_ref,
            assignments,
        }
    }

    /// Returns the complete mission-scoped task identity.
    pub const fn task_ref(&self) -> &TaskRef {
        &self.task_ref
    }

    /// Returns the proposed task identity.
    pub const fn task_id(&self) -> &TaskId {
        self.task_ref.task_id()
    }

    /// Returns all proposed role assignments.
    pub fn assignments(&self) -> &[RoleAssignment] {
        &self.assignments
    }
}

/// A proposal whose resources are now system-recognized commitments.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CommittedPlan {
    /// Mission-scoped task represented by this committed plan.
    task_ref: TaskRef,
    /// Resource-checked assignments accepted by coordination.
    assignments: Vec<RoleAssignment>,
}

impl CommittedPlan {
    /// Creates a committed plan after reservation checks succeed.
    fn new(task_ref: TaskRef, assignments: Vec<RoleAssignment>) -> Self {
        Self {
            task_ref,
            assignments,
        }
    }

    /// Returns the complete mission-scoped task identity.
    pub const fn task_ref(&self) -> &TaskRef {
        &self.task_ref
    }

    /// Returns the committed task identity.
    pub const fn task_id(&self) -> &TaskId {
        self.task_ref.task_id()
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
    /// The bound group is authorized to begin role execution.
    Active,
    /// The group adapted after a recoverable deviation.
    Adapted,
    /// All assigned roles completed.
    Completed,
    /// The terminal group released all current bindings and reservations.
    Released,
    /// The group cannot safely continue.
    Blocked,
}

/// A dynamic group of members, roles, and resource bindings.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ExecutionGroup {
    /// Dynamic execution-group identity.
    group_id: ExecutionGroupId,
    /// Mission-scoped task owned by the group.
    task_ref: TaskRef,
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
            task_ref: plan.task_ref().clone(),
            assignments: plan.assignments().to_vec(),
            lifecycle: GroupLifecycle::Bound,
        }
    }

    /// Returns the group identity.
    pub fn group_id(&self) -> &ExecutionGroupId {
        &self.group_id
    }

    /// Returns the complete mission-scoped task identity.
    pub const fn task_ref(&self) -> &TaskRef {
        &self.task_ref
    }

    /// Returns the task owned by this group.
    pub const fn task_id(&self) -> &TaskId {
        self.task_ref.task_id()
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
        /// Mission-scoped task currently holding the resource.
        owner_task_ref: TaskRef,
        /// Role currently holding the resource.
        owner_role_id: RoleId,
    },
    /// A group lifecycle transition was invalid.
    InvalidLifecycle(GroupLifecycle),
    /// A node lease or heartbeat did not satisfy the Node Contract.
    InvalidLease(String),
    /// A node attempted to use an unknown lease identity.
    UnknownLease {
        /// Node that sent the invalid lease identity.
        node_id: NodeId,
        /// Lease identity that was not registered for the node.
        lease_id: LeaseId,
    },
    /// A node attempted to use a lease after expiry.
    LeaseExpired {
        /// Node whose lease expired.
        node_id: NodeId,
        /// Expired lease identity.
        lease_id: LeaseId,
    },
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
                owner_task_ref,
                owner_role_id,
            } => write!(
                formatter,
                "resource conflict: {resource_id} held by task {owner_task_ref}, role {owner_role_id}"
            ),
            Self::InvalidLifecycle(lifecycle) => {
                write!(formatter, "invalid lifecycle: {lifecycle:?}")
            }
            Self::InvalidLease(reason) => write!(formatter, "invalid node lease: {reason}"),
            Self::UnknownLease { node_id, lease_id } => {
                write!(formatter, "node {node_id} does not own lease {lease_id}")
            }
            Self::LeaseExpired { node_id, lease_id } => {
                write!(formatter, "lease {lease_id} for node {node_id} has expired")
            }
        }
    }
}

impl std::error::Error for ControlError {}

/// Global control state for registration, matching, commitment, and recovery.
#[derive(Debug)]
pub struct ControlPlane {
    /// Registered nodes and their shared health snapshots.
    nodes: BTreeMap<NodeId, RegisteredNode>,
    /// Resources currently held by committed task roles.
    reservations: BTreeMap<ResourceId, Reservation>,
    /// Dynamic execution groups known to the control plane.
    groups: BTreeMap<ExecutionGroupId, ExecutionGroup>,
    /// Maximum age accepted for a node's shared health snapshot.
    max_status_age_ms: u64,
}

impl Default for ControlPlane {
    /// Creates an empty control plane with the default status freshness window.
    fn default() -> Self {
        Self {
            nodes: BTreeMap::new(),
            reservations: BTreeMap::new(),
            groups: BTreeMap::new(),
            max_status_age_ms: DEFAULT_NODE_STATUS_TTL_MS,
        }
    }
}

/// A node registration plus its latest shared health view.
#[derive(Debug, Clone)]
struct RegisteredNode {
    /// Capability, resource, and local-runtime declaration.
    registration: NodeRegistration,
    /// Latest health state used by global matching.
    status: NodeStatus,
    /// Renewable authority required for the node to remain schedulable.
    lease: NodeLease,
}

/// The task and role that currently hold a resource commitment.
#[derive(Debug, Clone)]
struct Reservation {
    /// Mission-scoped task currently holding the resource.
    task_ref: TaskRef,
    /// Role currently holding the resource.
    role_id: RoleId,
    /// Group currently owning the binding after creation, if any.
    group_id: Option<ExecutionGroupId>,
}

impl ControlPlane {
    /// Creates an empty control plane.
    pub fn new() -> Self {
        Self::default()
    }

    /// Creates an empty control plane with an explicit status freshness window.
    pub const fn with_status_ttl(max_status_age_ms: u64) -> Self {
        Self {
            nodes: BTreeMap::new(),
            reservations: BTreeMap::new(),
            groups: BTreeMap::new(),
            max_status_age_ms,
        }
    }

    /// Registers one node with a generated lease and records its visibility.
    pub fn register_node<E: EventSink>(
        &mut self,
        registration: NodeRegistration,
        status: NodeStatus,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<(), ControlError> {
        let lease_id = LeaseId::new(format!("lease-{}", registration.node_id()))
            .map_err(|error| ControlError::InvalidLease(error.to_string()))?;
        let lease = NodeLease::new(
            lease_id,
            registration.node_id().clone(),
            timestamp,
            DEFAULT_NODE_LEASE_TTL_MS,
        )
        .map_err(|error| ControlError::InvalidLease(error.to_string()))?;
        self.register_node_with_lease(
            registration,
            status,
            lease,
            timestamp,
            correlation_id,
            events,
        )
    }

    /// Registers one node with an explicit lease from the Node Contract.
    pub fn register_node_with_lease<E: EventSink>(
        &mut self,
        registration: NodeRegistration,
        status: NodeStatus,
        lease: NodeLease,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<(), ControlError> {
        if lease.node_id() != registration.node_id() {
            return Err(ControlError::InvalidLease(
                "lease node does not match registration node".to_string(),
            ));
        }
        if !lease.is_active_at(timestamp) {
            return Err(ControlError::LeaseExpired {
                node_id: registration.node_id().clone(),
                lease_id: lease.lease_id().clone(),
            });
        }
        let node_id = registration.node_id().clone();
        let lease_id = lease.lease_id().clone();
        self.nodes.insert(
            node_id.clone(),
            RegisteredNode {
                registration,
                status,
                lease,
            },
        );
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::NodeRegistered { node_id, lease_id },
        );
        Ok(())
    }

    /// Accepts a heartbeat, refreshes its health snapshot, and renews its lease.
    pub fn accept_heartbeat<E: EventSink>(
        &mut self,
        heartbeat: NodeHeartbeat,
        received_at: TimestampMs,
        lease_duration_ms: u64,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<(), ControlError> {
        let node = self
            .nodes
            .get_mut(heartbeat.node_id())
            .ok_or_else(|| ControlError::UnknownNode(heartbeat.node_id().clone()))?;
        if node.lease.lease_id() != heartbeat.lease_id() {
            return Err(ControlError::UnknownLease {
                node_id: heartbeat.node_id().clone(),
                lease_id: heartbeat.lease_id().clone(),
            });
        }
        let renewed_lease = node
            .lease
            .renew(received_at, lease_duration_ms)
            .map_err(|error| match error {
                domain::DomainError::LeaseExpired { .. } => ControlError::LeaseExpired {
                    node_id: heartbeat.node_id().clone(),
                    lease_id: heartbeat.lease_id().clone(),
                },
                other => ControlError::InvalidLease(other.to_string()),
            })?;
        node.status = heartbeat.status();
        node.lease = renewed_lease;
        events.append(
            received_at,
            correlation_id,
            None,
            EventPayload::NodeHeartbeatAccepted {
                node_id: heartbeat.node_id().clone(),
                lease_id: heartbeat.lease_id().clone(),
            },
        );
        Ok(())
    }

    /// Expires leases and marks affected nodes Offline in the shared view.
    pub fn expire_leases<E: EventSink>(
        &mut self,
        now: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Vec<NodeId> {
        let mut expired_nodes = Vec::new();
        for (node_id, node) in &mut self.nodes {
            if node.status.health().is_schedulable() && !node.lease.is_active_at(now) {
                node.status = NodeStatus::new(domain::NodeHealth::Offline, now);
                expired_nodes.push(node_id.clone());
                events.append(
                    now,
                    correlation_id,
                    None,
                    EventPayload::NodeLeaseExpired {
                        node_id: node_id.clone(),
                        lease_id: node.lease.lease_id().clone(),
                    },
                );
            }
        }
        expired_nodes
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
                    node.status.health().is_schedulable()
                        && node.status.is_fresh_at(timestamp, self.max_status_age_ms)
                        && node.lease.is_active_at(timestamp)
                        && node.registration.supports_role(role)
                })
                .map(|(node_id, _)| node_id.clone())
                .collect::<Vec<_>>();
            if node_ids.is_empty() {
                return Err(ControlError::NoCandidate(role.role_id().clone()));
            }
            roles.push(RoleCandidates::new(role.role_id().clone(), node_ids));
        }

        let candidates = CandidateSet::new(requirement.task_ref().clone(), roles);
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::CandidatesMatched {
                task_ref: requirement.task_ref().clone(),
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
        if candidates.task_ref() != requirement.task_ref() {
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

        let proposal = AssignmentProposal::new(requirement.task_ref().clone(), assignments);
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::ProposalCreated {
                task_ref: requirement.task_ref().clone(),
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
                        owner_task_ref: reservation.task_ref.clone(),
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
                        task_ref: proposal.task_ref().clone(),
                        role_id: assignment.role_id().clone(),
                        group_id: None,
                    },
                );
            }
        }

        let plan = CommittedPlan::new(proposal.task_ref().clone(), proposal.assignments().to_vec());
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::PlanCommitted {
                task_ref: proposal.task_ref().clone(),
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
        for assignment in plan.assignments() {
            for resource_id in assignment.resource_ids() {
                let reservation = self.reservations.get(resource_id).ok_or_else(|| {
                    ControlError::InvalidProposal(format!(
                        "committed resource {resource_id} has no reservation"
                    ))
                })?;
                if reservation.task_ref != *plan.task_ref()
                    || reservation.role_id != *assignment.role_id()
                    || reservation.group_id.is_some()
                {
                    return Err(ControlError::InvalidProposal(format!(
                        "committed resource {resource_id} cannot bind to group {group_id}"
                    )));
                }
            }
        }
        for assignment in plan.assignments() {
            for resource_id in assignment.resource_ids() {
                if let Some(reservation) = self.reservations.get_mut(resource_id) {
                    reservation.group_id = Some(group_id.clone());
                }
            }
        }
        let group = ExecutionGroup::new(group_id.clone(), plan);
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::ExecutionGroupBound {
                group_id: group_id.clone(),
                task_ref: plan.task_ref().clone(),
            },
        );
        self.groups.insert(group_id, group.clone());
        Ok(group)
    }

    /// Activates a bound group before any role invocation begins.
    pub fn activate_group<E: EventSink>(
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
        if group.lifecycle != GroupLifecycle::Bound {
            return Err(ControlError::InvalidLifecycle(group.lifecycle));
        }
        group.lifecycle = GroupLifecycle::Active;
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::ExecutionGroupActivated {
                group_id: group_id.clone(),
                task_ref: group.task_ref.clone(),
            },
        );
        Ok(())
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
        if !matches!(
            group.lifecycle,
            GroupLifecycle::Active | GroupLifecycle::Adapted
        ) {
            return Err(ControlError::InvalidLifecycle(group.lifecycle));
        }
        let assignment = group
            .assignments
            .iter_mut()
            .find(|assignment| assignment.role_id() == role.role_id())
            .ok_or_else(|| {
                ControlError::InvalidProposal(format!("group has no role {}", role.role_id()))
            })?;
        let previous_node = assignment.node_id().clone();
        for resource_id in assignment.resource_ids() {
            let reservation = self.reservations.get(resource_id).ok_or_else(|| {
                ControlError::InvalidProposal(format!(
                    "group {group_id} binding {resource_id} has no reservation"
                ))
            })?;
            if reservation.task_ref != group.task_ref
                || reservation.role_id != *role.role_id()
                || reservation.group_id.as_ref() != Some(group_id)
            {
                return Err(ControlError::InvalidProposal(format!(
                    "group {group_id} does not own role reservation {resource_id}"
                )));
            }
        }
        for resource_id in assignment.resource_ids() {
            self.reservations.remove(resource_id);
        }
        for resource_id in &replacement_resources {
            self.reservations.insert(
                resource_id.clone(),
                Reservation {
                    task_ref: group.task_ref.clone(),
                    role_id: role.role_id().clone(),
                    group_id: Some(group_id.clone()),
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
                task_ref: group.task_ref.clone(),
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
        if !matches!(
            group.lifecycle,
            GroupLifecycle::Active | GroupLifecycle::Adapted
        ) {
            return Err(ControlError::InvalidLifecycle(group.lifecycle));
        }
        group.lifecycle = GroupLifecycle::Completed;
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::ExecutionGroupCompleted {
                group_id: group_id.clone(),
                task_ref: group.task_ref.clone(),
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
        if !matches!(
            group.lifecycle,
            GroupLifecycle::Bound | GroupLifecycle::Active | GroupLifecycle::Adapted
        ) {
            return Err(ControlError::InvalidLifecycle(group.lifecycle));
        }
        group.lifecycle = GroupLifecycle::Blocked;
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::ExecutionGroupBlocked {
                group_id: group_id.clone(),
                task_ref: group.task_ref.clone(),
                reason: reason.into(),
            },
        );
        Ok(())
    }

    /// Releases all reservations and current bindings owned by a terminal group.
    pub fn release_group<E: EventSink>(
        &mut self,
        group_id: &ExecutionGroupId,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<(), ControlError> {
        let group = self
            .groups
            .get(group_id)
            .ok_or_else(|| ControlError::UnknownGroup(group_id.clone()))?;
        if !matches!(
            group.lifecycle,
            GroupLifecycle::Completed | GroupLifecycle::Blocked
        ) {
            return Err(ControlError::InvalidLifecycle(group.lifecycle));
        }
        let task_ref = group.task_ref.clone();
        let resource_ids = group
            .assignments
            .iter()
            .flat_map(|assignment| assignment.resource_ids().iter().cloned())
            .collect::<Vec<_>>();
        for resource_id in &resource_ids {
            let reservation = self.reservations.get(resource_id).ok_or_else(|| {
                ControlError::InvalidProposal(format!(
                    "group {group_id} binding {resource_id} has no reservation"
                ))
            })?;
            if reservation.task_ref != task_ref || reservation.group_id.as_ref() != Some(group_id) {
                return Err(ControlError::InvalidProposal(format!(
                    "group {group_id} does not own reservation {resource_id}"
                )));
            }
        }
        for resource_id in &resource_ids {
            self.reservations.remove(resource_id);
        }
        let group = self
            .groups
            .get_mut(group_id)
            .ok_or_else(|| ControlError::UnknownGroup(group_id.clone()))?;
        group.assignments.clear();
        group.lifecycle = GroupLifecycle::Released;
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::ExecutionGroupReleased {
                group_id: group_id.clone(),
                task_ref,
                resource_ids,
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

#[cfg(test)]
mod tests {
    use super::*;
    use domain::{
        Capability, CapabilityKind, CorrelationId, LeaseId, NodeHealth, NodeHeartbeat, NodeLease,
        Resource, ResourceKind, RoleRequirement,
    };

    /// Discards event evidence while exercising Control behavior in isolation.
    #[derive(Default)]
    struct TestEvents;

    impl EventSink for TestEvents {
        /// Ignores the event because these tests assert returned control state.
        fn append(
            &mut self,
            _timestamp: TimestampMs,
            _correlation_id: &CorrelationId,
            _causation_id: Option<&domain::EventId>,
            _payload: EventPayload,
        ) {
        }
    }

    /// Builds a single-capability registration for deterministic control tests.
    fn registration(
        node_id: &str,
        capability: CapabilityKind,
        resource_id: &str,
    ) -> NodeRegistration {
        NodeRegistration::new(
            NodeId::new(node_id).expect("test node id must be valid"),
            domain::LocalRuntime::new("fake-eaios", "0.1.0").expect("test runtime must be valid"),
            vec![Capability::new(capability, true)],
            vec![
                Resource::new(
                    ResourceId::new(resource_id).expect("test resource id must be valid"),
                    ResourceKind::Space,
                    1,
                )
                .expect("test resource must be valid"),
            ],
        )
    }

    /// Builds a one-role task requirement for a control test.
    fn requirement(task_id: &str, role_id: &str, capability: CapabilityKind) -> TaskRequirement {
        requirement_for_mission("mission-control-test", task_id, role_id, capability)
    }

    /// Builds a one-role task requirement in an explicit mission namespace.
    fn requirement_for_mission(
        mission_id: &str,
        task_id: &str,
        role_id: &str,
        capability: CapabilityKind,
    ) -> TaskRequirement {
        TaskRequirement::new(
            domain::MissionId::new(mission_id).expect("test mission id must be valid"),
            TaskId::new(task_id).expect("test task id must be valid"),
            vec![RoleRequirement::new(
                RoleId::new(role_id).expect("test role id must be valid"),
                capability,
                Some(ResourceKind::Space),
            )],
        )
        .expect("test requirement must be valid")
    }

    /// Creates the correlation identity shared by one deterministic test.
    fn correlation() -> CorrelationId {
        CorrelationId::new("control-test-trace").expect("test correlation id must be valid")
    }

    /// A schedulable node can produce a proposal and a committed plan.
    #[test]
    fn normal_path_matches_proposes_and_commits() {
        let node = registration("node-a", CapabilityKind::Transport, "space-a");
        let node_id = node.node_id().clone();
        let resource_id = ResourceId::new("space-a").expect("test resource id must be valid");
        let role_id = RoleId::new("transport").expect("test role id must be valid");
        let task = requirement("task-normal", "transport", CapabilityKind::Transport);
        let timestamp = TimestampMs::new(0);
        let correlation_id = correlation();
        let mut control = ControlPlane::new();
        let mut events = TestEvents;

        control
            .register_node(
                node,
                NodeStatus::new(NodeHealth::Online, timestamp),
                timestamp,
                &correlation_id,
                &mut events,
            )
            .expect("test node registration should succeed");
        let candidates = control
            .match_capabilities(&task, timestamp, &correlation_id, &mut events)
            .expect("online node should match");
        assert_eq!(
            candidates
                .for_role(&role_id)
                .expect("role candidates should exist")
                .node_ids(),
            std::slice::from_ref(&node_id)
        );

        let proposal = control
            .propose(
                &task,
                &candidates,
                vec![RoleAssignment::new(role_id, node_id, vec![resource_id])],
                timestamp,
                &correlation_id,
                &mut events,
            )
            .expect("candidate assignment should produce a proposal");
        let plan = control
            .commit(&proposal, timestamp, &correlation_id, &mut events)
            .expect("unreserved resource should commit");
        assert_eq!(plan.assignments().len(), 1);
    }

    /// Mission-scoped TaskRefs prevent identical local TaskIds from colliding.
    #[test]
    fn mission_scoped_task_identity_survives_control_chain() {
        let node_a = registration("node-a", CapabilityKind::Transport, "space-a");
        let node_b = registration("node-b", CapabilityKind::Transport, "space-b");
        let node_a_id = node_a.node_id().clone();
        let node_b_id = node_b.node_id().clone();
        let resource_a = ResourceId::new("space-a").expect("test resource id must be valid");
        let resource_b = ResourceId::new("space-b").expect("test resource id must be valid");
        let role_id = RoleId::new("transport").expect("test role id must be valid");
        let task_a = requirement_for_mission(
            "mission-a",
            "task-01",
            "transport",
            CapabilityKind::Transport,
        );
        let task_b = requirement_for_mission(
            "mission-b",
            "task-01",
            "transport",
            CapabilityKind::Transport,
        );
        let group_a = ExecutionGroupId::new("group-a").expect("test group id must be valid");
        let group_b = ExecutionGroupId::new("group-b").expect("test group id must be valid");
        let timestamp = TimestampMs::new(0);
        let correlation_id = correlation();
        let mut control = ControlPlane::new();
        let mut events = TestEvents;
        for node in [node_a, node_b] {
            control
                .register_node(
                    node,
                    NodeStatus::new(NodeHealth::Online, timestamp),
                    timestamp,
                    &correlation_id,
                    &mut events,
                )
                .expect("test node registration should succeed");
        }

        let candidates_a = control
            .match_capabilities(&task_a, timestamp, &correlation_id, &mut events)
            .expect("Mission A task should match");
        let candidates_b = control
            .match_capabilities(&task_b, timestamp, &correlation_id, &mut events)
            .expect("Mission B task should match");
        let proposal_a = control
            .propose(
                &task_a,
                &candidates_a,
                vec![RoleAssignment::new(
                    role_id.clone(),
                    node_a_id,
                    vec![resource_a.clone()],
                )],
                timestamp,
                &correlation_id,
                &mut events,
            )
            .expect("Mission A proposal should succeed");
        let proposal_b = control
            .propose(
                &task_b,
                &candidates_b,
                vec![RoleAssignment::new(
                    role_id,
                    node_b_id,
                    vec![resource_b.clone()],
                )],
                timestamp,
                &correlation_id,
                &mut events,
            )
            .expect("Mission B proposal should succeed");
        let plan_a = control
            .commit(&proposal_a, timestamp, &correlation_id, &mut events)
            .expect("Mission A plan should commit");
        let plan_b = control
            .commit(&proposal_b, timestamp, &correlation_id, &mut events)
            .expect("Mission B plan should commit");
        control
            .create_group(
                group_a.clone(),
                &plan_a,
                timestamp,
                &correlation_id,
                &mut events,
            )
            .expect("Mission A group should bind");
        control
            .create_group(
                group_b.clone(),
                &plan_b,
                timestamp,
                &correlation_id,
                &mut events,
            )
            .expect("Mission B group should bind");

        assert_eq!(task_a.task_id(), task_b.task_id());
        assert_ne!(task_a.task_ref(), task_b.task_ref());
        assert_eq!(candidates_a.task_ref(), task_a.task_ref());
        assert_eq!(candidates_b.task_ref(), task_b.task_ref());
        assert_eq!(proposal_a.task_ref(), task_a.task_ref());
        assert_eq!(proposal_b.task_ref(), task_b.task_ref());
        assert_eq!(plan_a.task_ref(), task_a.task_ref());
        assert_eq!(plan_b.task_ref(), task_b.task_ref());
        assert_eq!(
            control
                .group(&group_a)
                .expect("Mission A group must exist")
                .task_ref(),
            task_a.task_ref()
        );
        assert_eq!(
            control
                .group(&group_b)
                .expect("Mission B group must exist")
                .task_ref(),
            task_b.task_ref()
        );
        let reservation_a = control
            .reservations
            .get(&resource_a)
            .expect("Mission A reservation must exist");
        let reservation_b = control
            .reservations
            .get(&resource_b)
            .expect("Mission B reservation must exist");
        assert_eq!(&reservation_a.task_ref, task_a.task_ref());
        assert_eq!(&reservation_b.task_ref, task_b.task_ref());
        assert_eq!(reservation_a.group_id.as_ref(), Some(&group_a));
        assert_eq!(reservation_b.group_id.as_ref(), Some(&group_b));
    }

    /// A node without the required capability is rejected during matching.
    #[test]
    fn matching_rejects_missing_capability() {
        let node = registration("node-a", CapabilityKind::Mobility, "space-a");
        let task = requirement("task-rejected", "transport", CapabilityKind::Transport);
        let timestamp = TimestampMs::new(0);
        let correlation_id = correlation();
        let mut control = ControlPlane::new();
        let mut events = TestEvents;
        control
            .register_node(
                node,
                NodeStatus::new(NodeHealth::Online, timestamp),
                timestamp,
                &correlation_id,
                &mut events,
            )
            .expect("test node registration should succeed");

        assert!(matches!(
            control.match_capabilities(&task, timestamp, &correlation_id, &mut events),
            Err(ControlError::NoCandidate(role)) if role.as_str() == "transport"
        ));
    }

    /// A second commit cannot take a resource already held by another task.
    #[test]
    fn commit_rejects_resource_conflict() {
        let node = registration("node-a", CapabilityKind::Transport, "space-a");
        let node_id = node.node_id().clone();
        let resource_id = ResourceId::new("space-a").expect("test resource id must be valid");
        let timestamp = TimestampMs::new(0);
        let correlation_id = correlation();
        let mut control = ControlPlane::new();
        let mut events = TestEvents;
        control
            .register_node(
                node,
                NodeStatus::new(NodeHealth::Online, timestamp),
                timestamp,
                &correlation_id,
                &mut events,
            )
            .expect("test node registration should succeed");

        let first_task = requirement("task-first", "transport-first", CapabilityKind::Transport);
        let first_candidates = control
            .match_capabilities(&first_task, timestamp, &correlation_id, &mut events)
            .expect("first task should match");
        let first_proposal = control
            .propose(
                &first_task,
                &first_candidates,
                vec![RoleAssignment::new(
                    RoleId::new("transport-first").expect("test role id must be valid"),
                    node_id.clone(),
                    vec![resource_id.clone()],
                )],
                timestamp,
                &correlation_id,
                &mut events,
            )
            .expect("first proposal should be valid");
        control
            .commit(&first_proposal, timestamp, &correlation_id, &mut events)
            .expect("first proposal should commit");

        let second_task = requirement("task-second", "transport-second", CapabilityKind::Transport);
        let second_candidates = control
            .match_capabilities(&second_task, timestamp, &correlation_id, &mut events)
            .expect("second task can match before commit");
        let second_proposal = control
            .propose(
                &second_task,
                &second_candidates,
                vec![RoleAssignment::new(
                    RoleId::new("transport-second").expect("test role id must be valid"),
                    node_id,
                    vec![resource_id.clone()],
                )],
                timestamp,
                &correlation_id,
                &mut events,
            )
            .expect("second proposal should be valid before reservation");

        assert!(matches!(
            control.commit(&second_proposal, timestamp, &correlation_id, &mut events),
            Err(ControlError::ResourceConflict { resource_id: conflict, .. })
                if conflict == resource_id
        ));
    }

    /// Terminal lifecycle states reject reactivation and release enables resource reuse.
    #[test]
    fn lifecycle_guards_terminal_states_and_release_frees_resources() {
        let node = registration(
            "node-lifecycle",
            CapabilityKind::Transport,
            "space-lifecycle",
        );
        let node_id = node.node_id().clone();
        let resource_id =
            ResourceId::new("space-lifecycle").expect("test resource id must be valid");
        let role_id = RoleId::new("transport").expect("test role id must be valid");
        let first_task = requirement_for_mission(
            "mission-lifecycle-a",
            "task-01",
            "transport",
            CapabilityKind::Transport,
        );
        let first_group =
            ExecutionGroupId::new("group-lifecycle-a").expect("test group id must be valid");
        let timestamp = TimestampMs::new(0);
        let correlation_id = correlation();
        let mut control = ControlPlane::new();
        let mut events = TestEvents;
        control
            .register_node(
                node,
                NodeStatus::new(NodeHealth::Online, timestamp),
                timestamp,
                &correlation_id,
                &mut events,
            )
            .expect("test node registration should succeed");
        let first_candidates = control
            .match_capabilities(&first_task, timestamp, &correlation_id, &mut events)
            .expect("first task should match");
        let first_proposal = control
            .propose(
                &first_task,
                &first_candidates,
                vec![RoleAssignment::new(
                    role_id.clone(),
                    node_id.clone(),
                    vec![resource_id.clone()],
                )],
                timestamp,
                &correlation_id,
                &mut events,
            )
            .expect("first proposal should be valid");
        let first_plan = control
            .commit(&first_proposal, timestamp, &correlation_id, &mut events)
            .expect("first proposal should commit");
        control
            .create_group(
                first_group.clone(),
                &first_plan,
                timestamp,
                &correlation_id,
                &mut events,
            )
            .expect("first group should bind");
        assert!(matches!(
            control.complete_group(
                &first_group,
                TimestampMs::new(1),
                &correlation_id,
                &mut events,
            ),
            Err(ControlError::InvalidLifecycle(GroupLifecycle::Bound))
        ));
        control
            .activate_group(
                &first_group,
                TimestampMs::new(2),
                &correlation_id,
                &mut events,
            )
            .expect("bound group should activate");
        control
            .complete_group(
                &first_group,
                TimestampMs::new(3),
                &correlation_id,
                &mut events,
            )
            .expect("active group should complete");
        assert!(matches!(
            control.activate_group(
                &first_group,
                TimestampMs::new(4),
                &correlation_id,
                &mut events,
            ),
            Err(ControlError::InvalidLifecycle(GroupLifecycle::Completed))
        ));
        control
            .release_group(
                &first_group,
                TimestampMs::new(5),
                &correlation_id,
                &mut events,
            )
            .expect("completed group should release");
        assert!(!control.reservations.contains_key(&resource_id));
        assert!(
            control
                .group(&first_group)
                .expect("released group should remain observable")
                .assignments()
                .is_empty()
        );
        assert!(matches!(
            control.activate_group(
                &first_group,
                TimestampMs::new(6),
                &correlation_id,
                &mut events,
            ),
            Err(ControlError::InvalidLifecycle(GroupLifecycle::Released))
        ));

        let second_task = requirement_for_mission(
            "mission-lifecycle-b",
            "task-01",
            "transport",
            CapabilityKind::Transport,
        );
        let second_group =
            ExecutionGroupId::new("group-lifecycle-b").expect("test group id must be valid");
        let second_candidates = control
            .match_capabilities(
                &second_task,
                TimestampMs::new(6),
                &correlation_id,
                &mut events,
            )
            .expect("second task should match");
        let second_proposal = control
            .propose(
                &second_task,
                &second_candidates,
                vec![RoleAssignment::new(
                    role_id,
                    node_id,
                    vec![resource_id.clone()],
                )],
                TimestampMs::new(6),
                &correlation_id,
                &mut events,
            )
            .expect("released resource should be proposed again");
        let second_plan = control
            .commit(
                &second_proposal,
                TimestampMs::new(6),
                &correlation_id,
                &mut events,
            )
            .expect("released resource should commit again");
        control
            .create_group(
                second_group.clone(),
                &second_plan,
                TimestampMs::new(6),
                &correlation_id,
                &mut events,
            )
            .expect("second group should bind");
        control
            .activate_group(
                &second_group,
                TimestampMs::new(7),
                &correlation_id,
                &mut events,
            )
            .expect("second group should activate");
        control
            .block_group(
                &second_group,
                "no safe continuation",
                TimestampMs::new(8),
                &correlation_id,
                &mut events,
            )
            .expect("active group may become blocked");
        assert!(matches!(
            control.complete_group(
                &second_group,
                TimestampMs::new(9),
                &correlation_id,
                &mut events,
            ),
            Err(ControlError::InvalidLifecycle(GroupLifecycle::Blocked))
        ));
        control
            .release_group(
                &second_group,
                TimestampMs::new(10),
                &correlation_id,
                &mut events,
            )
            .expect("blocked group should release");
        assert_eq!(
            control
                .group(&second_group)
                .expect("released blocked group should remain observable")
                .lifecycle(),
            GroupLifecycle::Released
        );
        assert!(!control.reservations.contains_key(&resource_id));
    }

    /// A stale health snapshot is rejected after the configured status TTL.
    #[test]
    fn matching_rejects_stale_node_status() {
        let node = registration("node-a", CapabilityKind::Transport, "space-a");
        let task = requirement("task-timeout", "transport", CapabilityKind::Transport);
        let observed_at = TimestampMs::new(0);
        let now = TimestampMs::new(101);
        let correlation_id = correlation();
        let mut control = ControlPlane::with_status_ttl(100);
        let mut events = TestEvents;
        control
            .register_node(
                node,
                NodeStatus::new(NodeHealth::Online, observed_at),
                observed_at,
                &correlation_id,
                &mut events,
            )
            .expect("test node registration should succeed");

        assert!(matches!(
            control.match_capabilities(&task, now, &correlation_id, &mut events),
            Err(ControlError::NoCandidate(_))
        ));
    }

    /// A valid heartbeat renews the lease and refreshes the node health snapshot.
    #[test]
    fn heartbeat_renews_lease_and_updates_health() {
        let node = registration(
            "node-heartbeat",
            CapabilityKind::Transport,
            "space-heartbeat",
        );
        let node_id = node.node_id().clone();
        let lease_id = LeaseId::new("lease-heartbeat").expect("test lease id must be valid");
        let lease = NodeLease::new(lease_id.clone(), node_id.clone(), TimestampMs::new(0), 100)
            .expect("test lease should be valid");
        let correlation_id = correlation();
        let mut control = ControlPlane::with_status_ttl(200);
        let mut events = TestEvents;
        control
            .register_node_with_lease(
                node,
                NodeStatus::new(NodeHealth::Online, TimestampMs::new(0)),
                lease,
                TimestampMs::new(0),
                &correlation_id,
                &mut events,
            )
            .expect("explicit lease registration should succeed");

        control
            .accept_heartbeat(
                NodeHeartbeat::new(
                    node_id.clone(),
                    lease_id,
                    NodeStatus::new(NodeHealth::Degraded, TimestampMs::new(50)),
                ),
                TimestampMs::new(50),
                100,
                &correlation_id,
                &mut events,
            )
            .expect("heartbeat should renew active lease");

        let task = requirement("task-heartbeat", "transport", CapabilityKind::Transport);
        assert!(
            control
                .match_capabilities(&task, TimestampMs::new(149), &correlation_id, &mut events)
                .is_ok()
        );
        assert!(matches!(
            control.match_capabilities(&task, TimestampMs::new(150), &correlation_id, &mut events),
            Err(ControlError::NoCandidate(_))
        ));
    }

    /// Lease expiry marks a previously schedulable node Offline.
    #[test]
    fn expired_lease_marks_node_offline() {
        let node = registration("node-expiring", CapabilityKind::Transport, "space-expiring");
        let node_id = node.node_id().clone();
        let task = requirement("task-expiring", "transport", CapabilityKind::Transport);
        let correlation_id =
            CorrelationId::new("lease-expiry-trace").expect("test correlation id must be valid");
        let mut control = ControlPlane::with_status_ttl(200);
        let mut events = TestEvents;
        control
            .register_node(
                node,
                NodeStatus::new(NodeHealth::Online, TimestampMs::new(0)),
                TimestampMs::new(0),
                &correlation_id,
                &mut events,
            )
            .expect("test node registration should succeed");

        let expired = control.expire_leases(
            TimestampMs::new(DEFAULT_NODE_LEASE_TTL_MS),
            &correlation_id,
            &mut events,
        );
        assert_eq!(expired, vec![node_id]);
        assert!(matches!(
            control.match_capabilities(
                &task,
                TimestampMs::new(DEFAULT_NODE_LEASE_TTL_MS),
                &correlation_id,
                &mut events,
            ),
            Err(ControlError::NoCandidate(_))
        ));
    }

    /// A heartbeat carrying another node's lease is rejected.
    #[test]
    fn heartbeat_rejects_unknown_lease() {
        let node = registration("node-lease-owner", CapabilityKind::Transport, "space-owner");
        let node_id = node.node_id().clone();
        let correlation_id = correlation();
        let mut control = ControlPlane::new();
        let mut events = TestEvents;
        control
            .register_node(
                node,
                NodeStatus::new(NodeHealth::Online, TimestampMs::new(0)),
                TimestampMs::new(0),
                &correlation_id,
                &mut events,
            )
            .expect("test node registration should succeed");

        let error = control
            .accept_heartbeat(
                NodeHeartbeat::new(
                    node_id,
                    LeaseId::new("lease-not-owned").expect("test lease id must be valid"),
                    NodeStatus::new(NodeHealth::Online, TimestampMs::new(1)),
                ),
                TimestampMs::new(1),
                DEFAULT_NODE_LEASE_TTL_MS,
                &correlation_id,
                &mut events,
            )
            .expect_err("unknown lease must be rejected");
        assert!(matches!(error, ControlError::UnknownLease { .. }));
    }

    /// A group without a safe replacement is recorded as blocked, never complete.
    #[test]
    fn group_can_be_marked_blocked_after_recovery_exhaustion() {
        let node = registration("node-a", CapabilityKind::Transport, "space-a");
        let node_id = node.node_id().clone();
        let resource_id = ResourceId::new("space-a").expect("test resource id must be valid");
        let role_id = RoleId::new("transport").expect("test role id must be valid");
        let group_id = ExecutionGroupId::new("group-blocked").expect("test group id must be valid");
        let task = requirement("task-blocked", "transport", CapabilityKind::Transport);
        let timestamp = TimestampMs::new(0);
        let correlation_id = correlation();
        let mut control = ControlPlane::new();
        let mut events = TestEvents;
        control
            .register_node(
                node,
                NodeStatus::new(NodeHealth::Online, timestamp),
                timestamp,
                &correlation_id,
                &mut events,
            )
            .expect("test node registration should succeed");
        let candidates = control
            .match_capabilities(&task, timestamp, &correlation_id, &mut events)
            .expect("task should initially match");
        let proposal = control
            .propose(
                &task,
                &candidates,
                vec![RoleAssignment::new(role_id, node_id, vec![resource_id])],
                timestamp,
                &correlation_id,
                &mut events,
            )
            .expect("proposal should be valid");
        let plan = control
            .commit(&proposal, timestamp, &correlation_id, &mut events)
            .expect("proposal should commit");
        control
            .create_group(
                group_id.clone(),
                &plan,
                timestamp,
                &correlation_id,
                &mut events,
            )
            .expect("group should bind before recovery exhaustion");

        control
            .block_group(
                &group_id,
                "no safe replacement is available",
                TimestampMs::new(1),
                &correlation_id,
                &mut events,
            )
            .expect("blocked transition should be recorded");
        assert_eq!(
            control
                .group(&group_id)
                .expect("blocked group should remain observable")
                .lifecycle(),
            GroupLifecycle::Blocked
        );
    }
}
