#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! Global DEAIOS decision and coordination logic for the first vertical slice.
//!
//! This crate validates Proposal versus Commit, resource reservation, Execution
//! Group binding, and role rebinding. It never sends raw actuator commands.

mod reconciliation;

pub use reconciliation::{
    CommittedRecoveryAssignment, ReconciliationAssessment, RecoveryAssignmentProposal,
    RecoveryCandidateSet, RecoveryOutcome, RoleRecoveryNeed,
};

use domain::{
    CorrelationId, EventPayload, ExecutionGroupId, LeaseId, NodeHealthObservation, NodeHeartbeat,
    NodeId, NodeLease, NodeLiveness, NodeLivenessObservation, NodeRegistration, NodeStateSnapshot,
    NodeStatus, ResourceId, RoleAssignment, RoleId, RoleRequirement, TaskId, TaskRef,
    TaskRequirement, TimestampMs,
};
use ports::{EventSink, SharedNodeStateReader, SharedNodeStateWriter, SharedStateError};
use std::collections::BTreeMap;
use std::fmt::{Display, Formatter};

/// Default maximum age for a node status used by capability matching.
pub const DEFAULT_NODE_STATUS_TTL_MS: u64 = 5_000;

/// Default lease duration assigned by the convenience registration method.
pub const DEFAULT_NODE_LEASE_TTL_MS: u64 = 15_000;

/// Evaluates freshness using only RoboGuide-local receive and decision times.
fn is_fresh_at(received_at: TimestampMs, now: TimestampMs, max_age_ms: u64) -> bool {
    now.as_millis().saturating_sub(received_at.as_millis()) <= max_age_ms
}

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
    /// Recovery was explicitly exhausted and the group cannot complete its task.
    Failed,
    /// All assigned roles completed.
    Completed,
    /// The terminal group released all current bindings and reservations.
    Released,
    /// The current execution configuration cannot progress without reconciliation.
    Blocked,
}

/// Context retained when one role binding is released for recovery.
#[derive(Debug, Clone, PartialEq, Eq)]
struct UnboundRole {
    /// Node that held the failed binding before partial release.
    previous_node_id: NodeId,
    /// Original assignment position restored after successful rebind.
    assignment_index: usize,
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
    /// Roles awaiting replacement while the Group identity and context remain.
    unbound_roles: BTreeMap<RoleId, UnboundRole>,
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
            unbound_roles: BTreeMap::new(),
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

    /// Returns whether a role is retained by the Group but awaits a new binding.
    pub fn is_role_unbound(&self, role_id: &RoleId) -> bool {
        self.unbound_roles.contains_key(role_id)
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
    /// A Group role already owns a committed replacement that has not been consumed or aborted.
    PendingRecoveryCommitmentExists {
        /// Group that already owns a pending replacement commitment.
        group_id: ExecutionGroupId,
        /// Role that already owns a pending replacement commitment.
        role_id: RoleId,
    },
    /// No authoritative pending commitment exists for the supplied Group role.
    PendingRecoveryCommitmentNotFound {
        /// Group expected to own the pending commitment.
        group_id: ExecutionGroupId,
        /// Role expected to own the pending commitment.
        role_id: RoleId,
    },
    /// A commitment handle does not match the current authoritative pending commitment.
    PendingRecoveryCommitmentMismatch {
        /// Group whose commitment handle was stale or forged.
        group_id: ExecutionGroupId,
        /// Role whose commitment handle was stale or forged.
        role_id: RoleId,
    },
    /// A group lifecycle transition was invalid.
    InvalidLifecycle(GroupLifecycle),
    /// A node lease or heartbeat did not satisfy the Node Contract.
    InvalidLease(String),
    /// Shared Node State rejected an observation or lacked a required node fact.
    SharedState(SharedStateError),
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
            Self::PendingRecoveryCommitmentExists { group_id, role_id } => write!(
                formatter,
                "pending recovery commitment already exists for group {group_id}, role {role_id}"
            ),
            Self::PendingRecoveryCommitmentNotFound { group_id, role_id } => write!(
                formatter,
                "no pending recovery commitment for group {group_id}, role {role_id}"
            ),
            Self::PendingRecoveryCommitmentMismatch { group_id, role_id } => write!(
                formatter,
                "pending recovery commitment mismatch for group {group_id}, role {role_id}"
            ),
            Self::InvalidLifecycle(lifecycle) => {
                write!(formatter, "invalid lifecycle: {lifecycle:?}")
            }
            Self::InvalidLease(reason) => write!(formatter, "invalid node lease: {reason}"),
            Self::SharedState(error) => write!(formatter, "shared state error: {error}"),
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
    /// Renewable lease authority retained pending a later ownership decision.
    leases: BTreeMap<NodeId, NodeLease>,
    /// Resources currently held by committed task roles.
    reservations: BTreeMap<ResourceId, Reservation>,
    /// Dynamic execution groups known to the control plane.
    groups: BTreeMap<ExecutionGroupId, ExecutionGroup>,
    /// Committed replacements not yet consumed as active Group bindings.
    pending_recovery_commitments: BTreeMap<(ExecutionGroupId, RoleId), CommittedRecoveryAssignment>,
    /// Maximum age accepted for a node's shared health snapshot.
    max_status_age_ms: u64,
}

impl Default for ControlPlane {
    /// Creates an empty control plane with the default status freshness window.
    fn default() -> Self {
        Self {
            leases: BTreeMap::new(),
            reservations: BTreeMap::new(),
            groups: BTreeMap::new(),
            pending_recovery_commitments: BTreeMap::new(),
            max_status_age_ms: DEFAULT_NODE_STATUS_TTL_MS,
        }
    }
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
            leases: BTreeMap::new(),
            reservations: BTreeMap::new(),
            groups: BTreeMap::new(),
            pending_recovery_commitments: BTreeMap::new(),
            max_status_age_ms,
        }
    }

    /// Registers one node with a generated lease and records its visibility.
    pub fn register_node<S: SharedNodeStateWriter, E: EventSink>(
        &mut self,
        state: &mut S,
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
            state,
            registration,
            status,
            lease,
            timestamp,
            correlation_id,
            events,
        )
    }

    /// Registers one node with an explicit lease from the Node Contract.
    #[allow(clippy::too_many_arguments)]
    pub fn register_node_with_lease<S: SharedNodeStateWriter, E: EventSink>(
        &mut self,
        state: &mut S,
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
        state
            .record_node(NodeStateSnapshot::new(
                registration,
                status,
                timestamp,
                NodeLivenessObservation::new(NodeLiveness::Reachable, timestamp),
            ))
            .map_err(ControlError::SharedState)?;
        self.leases.insert(node_id.clone(), lease);
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::NodeRegistered { node_id, lease_id },
        );
        Ok(())
    }

    /// Accepts a heartbeat, refreshes its health snapshot, and renews its lease.
    pub fn accept_heartbeat<S: SharedNodeStateWriter, E: EventSink>(
        &mut self,
        state: &mut S,
        heartbeat: NodeHeartbeat,
        received_at: TimestampMs,
        lease_duration_ms: u64,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<(), ControlError> {
        let lease = self
            .leases
            .get_mut(heartbeat.node_id())
            .ok_or_else(|| ControlError::UnknownNode(heartbeat.node_id().clone()))?;
        if lease.lease_id() != heartbeat.lease_id() {
            return Err(ControlError::UnknownLease {
                node_id: heartbeat.node_id().clone(),
                lease_id: heartbeat.lease_id().clone(),
            });
        }
        let renewed_lease =
            lease
                .renew(received_at, lease_duration_ms)
                .map_err(|error| match error {
                    domain::DomainError::LeaseExpired { .. } => ControlError::LeaseExpired {
                        node_id: heartbeat.node_id().clone(),
                        lease_id: heartbeat.lease_id().clone(),
                    },
                    other => ControlError::InvalidLease(other.to_string()),
                })?;
        state
            .record_node_health(NodeHealthObservation::new(
                heartbeat.node_id().clone(),
                heartbeat.status(),
                received_at,
            ))
            .map_err(ControlError::SharedState)?;
        *lease = renewed_lease;
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

    /// Expires leases and records affected nodes as unreachable without changing reported health.
    pub fn expire_leases<S: SharedNodeStateReader + SharedNodeStateWriter, E: EventSink>(
        &mut self,
        state: &mut S,
        now: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<Vec<NodeId>, ControlError> {
        let expired = self
            .leases
            .iter()
            .filter(|(node_id, lease)| {
                !lease.is_active_at(now)
                    && state.node(node_id).is_some_and(|snapshot| {
                        snapshot.liveness().liveness() == NodeLiveness::Reachable
                    })
            })
            .map(|(node_id, lease)| (node_id.clone(), lease.lease_id().clone()))
            .collect::<Vec<_>>();
        for (node_id, lease_id) in &expired {
            state
                .record_node_liveness(
                    node_id,
                    NodeLivenessObservation::new(NodeLiveness::Unreachable, now),
                )
                .map_err(ControlError::SharedState)?;
            events.append(
                now,
                correlation_id,
                None,
                EventPayload::NodeLeaseExpired {
                    node_id: node_id.clone(),
                    lease_id: lease_id.clone(),
                },
            );
        }
        Ok(expired.into_iter().map(|(node_id, _)| node_id).collect())
    }

    /// Returns whether one node currently satisfies Control execution eligibility for a role.
    pub(crate) fn node_is_eligible_for_role<S: SharedNodeStateReader>(
        &self,
        state: &S,
        node_id: &NodeId,
        role: &RoleRequirement,
        timestamp: TimestampMs,
    ) -> bool {
        state.node(node_id).is_some_and(|snapshot| {
            snapshot.reported_status().health().is_schedulable()
                && is_fresh_at(
                    snapshot.reported_status_received_at(),
                    timestamp,
                    self.max_status_age_ms,
                )
                && snapshot.liveness().liveness() == NodeLiveness::Reachable
                && self
                    .leases
                    .get(node_id)
                    .is_some_and(|lease| lease.is_active_at(timestamp))
                && snapshot.registration().supports_role(role)
        })
    }

    /// Matches every task role against currently schedulable node capabilities.
    pub fn match_capabilities<S: SharedNodeStateReader, E: EventSink>(
        &self,
        state: &S,
        requirement: &TaskRequirement,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<CandidateSet, ControlError> {
        let mut roles = Vec::with_capacity(requirement.roles().len());
        for role in requirement.roles() {
            let node_ids = state
                .nodes()
                .into_iter()
                .filter(|snapshot| {
                    self.node_is_eligible_for_role(state, snapshot.node_id(), role, timestamp)
                })
                .map(|snapshot| snapshot.node_id().clone())
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
    #[allow(clippy::too_many_arguments)]
    pub fn propose<S: SharedNodeStateReader, E: EventSink>(
        &self,
        state: &S,
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
            let node = state
                .node(assignment.node_id())
                .ok_or_else(|| ControlError::UnknownNode(assignment.node_id().clone()))?;
            if !self.node_is_eligible_for_role(state, assignment.node_id(), role, timestamp) {
                return Err(ControlError::InvalidProposal(format!(
                    "node {} is no longer eligible for role {}",
                    assignment.node_id(),
                    role.role_id()
                )));
            }
            if assignment.resource_ids().iter().any(|resource_id| {
                !node
                    .registration()
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

    /// Activates a newly bound or fully rebound group before role invocation.
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
        if !matches!(
            group.lifecycle,
            GroupLifecycle::Bound | GroupLifecycle::Adapted
        ) {
            return Err(ControlError::InvalidLifecycle(group.lifecycle));
        }
        if !group.unbound_roles.is_empty() {
            return Err(ControlError::InvalidProposal(
                "execution group still has unbound roles".to_string(),
            ));
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

    /// Releases only one role's current member and resource binding for recovery.
    pub fn release_role_binding<E: EventSink>(
        &mut self,
        group_id: &ExecutionGroupId,
        role_id: &RoleId,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<(), ControlError> {
        let group = self
            .groups
            .get(group_id)
            .ok_or_else(|| ControlError::UnknownGroup(group_id.clone()))?;
        if group.lifecycle != GroupLifecycle::Blocked {
            return Err(ControlError::InvalidLifecycle(group.lifecycle));
        }
        let assignment_index = group
            .assignments
            .iter()
            .position(|assignment| assignment.role_id() == role_id)
            .ok_or_else(|| {
                ControlError::InvalidProposal(format!(
                    "group has no active binding for role {role_id}"
                ))
            })?;
        let assignment = &group.assignments[assignment_index];
        let task_ref = group.task_ref.clone();
        let node_id = assignment.node_id().clone();
        let resource_ids = assignment.resource_ids().to_vec();
        for resource_id in &resource_ids {
            let reservation = self.reservations.get(resource_id).ok_or_else(|| {
                ControlError::InvalidProposal(format!(
                    "group {group_id} binding {resource_id} has no reservation"
                ))
            })?;
            if reservation.task_ref != task_ref
                || reservation.role_id != *role_id
                || reservation.group_id.as_ref() != Some(group_id)
            {
                return Err(ControlError::InvalidProposal(format!(
                    "group {group_id} does not own role reservation {resource_id}"
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
        group.assignments.remove(assignment_index);
        group.unbound_roles.insert(
            role_id.clone(),
            UnboundRole {
                previous_node_id: node_id.clone(),
                assignment_index,
            },
        );
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::ExecutionGroupRoleBindingReleased {
                group_id: group_id.clone(),
                task_ref,
                role_id: role_id.clone(),
                node_id,
                resource_ids,
            },
        );
        Ok(())
    }

    /// Rebinds one blocked role using resources already committed by coordination.
    pub fn rebind_role<E: EventSink>(
        &mut self,
        committed: &CommittedRecoveryAssignment,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<RecoveryOutcome, ControlError> {
        let group = self
            .groups
            .get(committed.group_id())
            .ok_or_else(|| ControlError::UnknownGroup(committed.group_id().clone()))?;
        if group.lifecycle != GroupLifecycle::Blocked {
            return Err(ControlError::InvalidLifecycle(group.lifecycle));
        }
        if group.task_ref != *committed.task_ref() {
            return Err(ControlError::InvalidProposal(
                "committed recovery belongs to another task".to_string(),
            ));
        }
        self.validate_pending_recovery_commitment(committed)?;
        let unbound_role = group
            .unbound_roles
            .get(committed.role_id())
            .ok_or_else(|| {
                ControlError::InvalidProposal(format!(
                    "role {} is not unbound for committed rebind",
                    committed.role_id()
                ))
            })?;
        let previous_node = unbound_role.previous_node_id.clone();
        let assignment_index = unbound_role.assignment_index;
        if previous_node != *committed.previous_node_id()
            || committed.replacement_node_id() == committed.previous_node_id()
        {
            return Err(ControlError::InvalidProposal(
                "committed recovery does not match the released role binding".to_string(),
            ));
        }
        self.validate_recovery_commitment_reservations(committed)?;
        let replacement_assignment = RoleAssignment::new(
            committed.role_id().clone(),
            committed.replacement_node_id().clone(),
            committed.committed_resource_ids().to_vec(),
        );
        let group = self
            .groups
            .get_mut(committed.group_id())
            .ok_or_else(|| ControlError::UnknownGroup(committed.group_id().clone()))?;
        let insertion_index = assignment_index.min(group.assignments.len());
        group
            .assignments
            .insert(insertion_index, replacement_assignment);
        group.unbound_roles.remove(committed.role_id());
        group.lifecycle = GroupLifecycle::Adapted;
        self.pending_recovery_commitments
            .remove(&(committed.group_id().clone(), committed.role_id().clone()));
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::RecoveryRebound {
                group_id: committed.group_id().clone(),
                task_ref: committed.task_ref().clone(),
                role_id: committed.role_id().clone(),
                from_node: previous_node.clone(),
                to_node: committed.replacement_node_id().clone(),
            },
        );
        Ok(RecoveryOutcome::Recovered {
            group_id: committed.group_id().clone(),
            task_ref: committed.task_ref().clone(),
            role_id: committed.role_id().clone(),
            from_node: previous_node,
            to_node: committed.replacement_node_id().clone(),
        })
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
        if !group.unbound_roles.is_empty() {
            return Err(ControlError::InvalidProposal(
                "execution group still has unbound roles".to_string(),
            ));
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

    /// Marks a group blocked until reconciliation restores progress or declares failure.
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
            GroupLifecycle::Active | GroupLifecycle::Adapted
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

    /// Marks a blocked group terminally failed after recovery is explicitly exhausted.
    pub fn fail_group<E: EventSink>(
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
        if group.lifecycle != GroupLifecycle::Blocked {
            return Err(ControlError::InvalidLifecycle(group.lifecycle));
        }
        group.lifecycle = GroupLifecycle::Failed;
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::ExecutionGroupFailed {
                group_id: group_id.clone(),
                task_ref: group.task_ref.clone(),
                reason: reason.into(),
            },
        );
        Ok(())
    }

    /// Releases every reservation and pending commitment owned by a terminal Group.
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
            GroupLifecycle::Completed | GroupLifecycle::Failed
        ) {
            return Err(ControlError::InvalidLifecycle(group.lifecycle));
        }
        let task_ref = group.task_ref.clone();
        let mut expected_resources = BTreeMap::<ResourceId, RoleId>::new();
        for assignment in &group.assignments {
            for resource_id in assignment.resource_ids() {
                if expected_resources
                    .insert(resource_id.clone(), assignment.role_id().clone())
                    .is_some()
                {
                    return Err(ControlError::InvalidProposal(format!(
                        "group {group_id} has duplicate active resource {resource_id}"
                    )));
                }
            }
        }
        let pending_keys = self
            .pending_recovery_commitments
            .iter()
            .filter(|((pending_group_id, _), _)| pending_group_id == group_id)
            .map(|(key, committed)| {
                if committed.group_id() != group_id
                    || committed.task_ref() != &task_ref
                    || committed.role_id() != &key.1
                {
                    return Err(ControlError::InvalidProposal(format!(
                        "group {group_id} has inconsistent pending recovery ownership"
                    )));
                }
                for resource_id in committed.committed_resource_ids() {
                    if expected_resources
                        .insert(resource_id.clone(), committed.role_id().clone())
                        .is_some()
                    {
                        return Err(ControlError::InvalidProposal(format!(
                            "group {group_id} has duplicate committed resource {resource_id}"
                        )));
                    }
                }
                Ok(key.clone())
            })
            .collect::<Result<Vec<_>, ControlError>>()?;

        for (resource_id, role_id) in &expected_resources {
            let reservation = self.reservations.get(resource_id).ok_or_else(|| {
                ControlError::InvalidProposal(format!(
                    "group {group_id} ownership {resource_id} has no reservation"
                ))
            })?;
            if reservation.task_ref != task_ref
                || reservation.role_id != *role_id
                || reservation.group_id.as_ref() != Some(group_id)
            {
                return Err(ControlError::InvalidProposal(format!(
                    "group {group_id} has mismatched reservation {resource_id}"
                )));
            }
        }
        let resource_ids = self
            .reservations
            .iter()
            .filter(|(_, reservation)| reservation.group_id.as_ref() == Some(group_id))
            .map(|(resource_id, reservation)| {
                if reservation.task_ref != task_ref
                    || expected_resources.get(resource_id) != Some(&reservation.role_id)
                {
                    return Err(ControlError::InvalidProposal(format!(
                        "group {group_id} has orphan reservation {resource_id}"
                    )));
                }
                Ok(resource_id.clone())
            })
            .collect::<Result<Vec<_>, ControlError>>()?;

        for resource_id in &resource_ids {
            self.reservations.remove(resource_id);
        }
        for key in pending_keys {
            self.pending_recovery_commitments.remove(&key);
        }
        let group = self
            .groups
            .get_mut(group_id)
            .ok_or_else(|| ControlError::UnknownGroup(group_id.clone()))?;
        group.assignments.clear();
        group.unbound_roles.clear();
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
    use state::InMemorySharedNodeState;

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

    /// Captures lifecycle evidence and correlation identities for recovery tests.
    #[derive(Default)]
    struct RecordingEvents {
        /// Event payloads paired with the correlation identity supplied by Control.
        records: Vec<(CorrelationId, EventPayload)>,
    }

    impl EventSink for RecordingEvents {
        /// Records immutable payload evidence in deterministic append order.
        fn append(
            &mut self,
            _timestamp: TimestampMs,
            correlation_id: &CorrelationId,
            _causation_id: Option<&domain::EventId>,
            payload: EventPayload,
        ) {
            self.records.push((correlation_id.clone(), payload));
        }
    }

    /// Complete in-process setup for two-role Group recovery tests.
    struct RecoveryFixture {
        /// Control instance owning reservations and Group lifecycle.
        control: ControlPlane,
        /// Cross-mission node facts read by Control through the State Port.
        state: InMemorySharedNodeState,
        /// Structured lifecycle evidence emitted by Control.
        events: RecordingEvents,
        /// Task requirements used to prove replacement availability.
        requirement: TaskRequirement,
        /// Stable Group identity retained throughout recovery.
        group_id: ExecutionGroupId,
        /// Stable task identity retained throughout recovery.
        task_ref: TaskRef,
        /// Failed role released and rebound by recovery.
        transport_role: RoleId,
        /// Unaffected role retained throughout recovery.
        compute_role: RoleId,
        /// Original transport member.
        node_a_id: NodeId,
        /// Replacement transport member, whether or not it is registered.
        node_b_id: NodeId,
        /// Unaffected compute member retained throughout recovery.
        edge_c_id: NodeId,
        /// Original transport resource released by partial recovery.
        space_a: ResourceId,
        /// Replacement transport resource committed by rebind.
        space_b: ResourceId,
        /// Additional Node B resource used to prove atomic multi-resource commit.
        space_b_secondary: ResourceId,
        /// Unaffected compute resource retained throughout recovery.
        compute_c: ResourceId,
        /// Correlation identity expected on all recovery evidence.
        correlation_id: CorrelationId,
    }

    /// Builds a single-capability registration for deterministic control tests.
    fn registration(
        node_id: &str,
        capability: CapabilityKind,
        resource_id: &str,
    ) -> NodeRegistration {
        registration_with_resource_kind(node_id, capability, resource_id, ResourceKind::Space)
    }

    /// Builds a single-capability registration with an explicit resource kind.
    fn registration_with_resource_kind(
        node_id: &str,
        capability: CapabilityKind,
        resource_id: &str,
        resource_kind: ResourceKind,
    ) -> NodeRegistration {
        NodeRegistration::new(
            NodeId::new(node_id).expect("test node id must be valid"),
            domain::LocalRuntime::new("fake-eaios", "0.1.0").expect("test runtime must be valid"),
            vec![Capability::new(capability, true)],
            vec![
                Resource::new(
                    ResourceId::new(resource_id).expect("test resource id must be valid"),
                    resource_kind,
                    1,
                )
                .expect("test resource must be valid"),
            ],
        )
    }

    /// Creates one active two-role Group with an optional transport replacement.
    fn recovery_fixture(include_replacement: bool) -> RecoveryFixture {
        let node_a = registration_with_resource_kind(
            "node-a",
            CapabilityKind::Transport,
            "space-a",
            ResourceKind::Space,
        );
        let space_b = ResourceId::new("space-b").expect("test resource id must be valid");
        let space_b_secondary =
            ResourceId::new("space-b-secondary").expect("test resource id must be valid");
        let node_b = NodeRegistration::new(
            NodeId::new("node-b").expect("test node id must be valid"),
            domain::LocalRuntime::new("fake-eaios", "0.1.0").expect("test runtime must be valid"),
            vec![Capability::new(CapabilityKind::Transport, true)],
            vec![
                Resource::new(space_b.clone(), ResourceKind::Space, 1)
                    .expect("test resource must be valid"),
                Resource::new(space_b_secondary.clone(), ResourceKind::Space, 1)
                    .expect("test resource must be valid"),
            ],
        );
        let edge_c = registration_with_resource_kind(
            "edge-c",
            CapabilityKind::Compute,
            "compute-c",
            ResourceKind::Compute,
        );
        let node_a_id = node_a.node_id().clone();
        let node_b_id = node_b.node_id().clone();
        let edge_c_id = edge_c.node_id().clone();
        let space_a = ResourceId::new("space-a").expect("test resource id must be valid");
        let compute_c = ResourceId::new("compute-c").expect("test resource id must be valid");
        let transport_role = RoleId::new("transport").expect("test role id must be valid");
        let compute_role = RoleId::new("compute").expect("test role id must be valid");
        let requirement = TaskRequirement::new(
            domain::MissionId::new("mission-recovery").expect("test mission id must be valid"),
            TaskId::new("task-01").expect("test task id must be valid"),
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
        .expect("test requirement must be valid");
        let task_ref = requirement.task_ref().clone();
        let group_id =
            ExecutionGroupId::new("group-recovery").expect("test group id must be valid");
        let correlation_id =
            CorrelationId::new("recovery-trace").expect("test correlation id must be valid");
        let timestamp = TimestampMs::new(0);
        let mut control = ControlPlane::new();
        let mut state = InMemorySharedNodeState::new();
        let mut events = RecordingEvents::default();
        let mut registrations = vec![node_a, edge_c];
        if include_replacement {
            registrations.push(node_b);
        }
        for node in registrations {
            control
                .register_node(
                    &mut state,
                    node,
                    NodeStatus::new(NodeHealth::Online, timestamp),
                    timestamp,
                    &correlation_id,
                    &mut events,
                )
                .expect("test node registration should succeed");
        }
        let candidates = control
            .match_capabilities(
                &state,
                &requirement,
                timestamp,
                &correlation_id,
                &mut events,
            )
            .expect("initial role matching should succeed");
        let proposal = control
            .propose(
                &state,
                &requirement,
                &candidates,
                vec![
                    RoleAssignment::new(
                        transport_role.clone(),
                        node_a_id.clone(),
                        vec![space_a.clone()],
                    ),
                    RoleAssignment::new(
                        compute_role.clone(),
                        edge_c_id.clone(),
                        vec![compute_c.clone()],
                    ),
                ],
                timestamp,
                &correlation_id,
                &mut events,
            )
            .expect("initial proposal should succeed");
        let plan = control
            .commit(&proposal, timestamp, &correlation_id, &mut events)
            .expect("initial proposal should commit");
        control
            .create_group(
                group_id.clone(),
                &plan,
                timestamp,
                &correlation_id,
                &mut events,
            )
            .expect("test Group should bind");
        control
            .activate_group(&group_id, timestamp, &correlation_id, &mut events)
            .expect("test Group should activate");
        RecoveryFixture {
            control,
            state,
            events,
            requirement,
            group_id,
            task_ref,
            transport_role,
            compute_role,
            node_a_id,
            node_b_id,
            edge_c_id,
            space_a,
            space_b,
            space_b_secondary,
            compute_c,
            correlation_id,
        }
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

    /// Moves a recovery fixture from Active to Blocked without releasing bindings.
    fn block_fixture(fixture: &mut RecoveryFixture) {
        fixture
            .control
            .block_group(
                &fixture.group_id,
                "transport role cannot progress",
                TimestampMs::new(1),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("active fixture should become blocked");
    }

    /// Marks the fixture's assigned transport node unreachable in Shared State.
    fn mark_transport_unreachable(fixture: &mut RecoveryFixture, timestamp: TimestampMs) {
        fixture
            .state
            .record_node_liveness(
                &fixture.node_a_id,
                NodeLivenessObservation::new(NodeLiveness::Unreachable, timestamp),
            )
            .expect("transport liveness observation should be accepted");
    }

    /// Assesses the fixture and returns its single transport recovery need.
    fn assess_transport_recovery(
        fixture: &mut RecoveryFixture,
        timestamp: TimestampMs,
    ) -> RoleRecoveryNeed {
        match fixture
            .control
            .assess_group(
                &fixture.state,
                &fixture.group_id,
                &fixture.requirement,
                timestamp,
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("fixture assessment should succeed")
        {
            ReconciliationAssessment::RoleRecoveryRequired(need) => need,
            ReconciliationAssessment::NoAction => {
                panic!("unreachable transport assignment should require recovery")
            }
        }
    }

    /// Detects transport unavailability and leaves the fixture Blocked and unbound.
    fn begin_detected_transport_recovery(fixture: &mut RecoveryFixture) -> RoleRecoveryNeed {
        mark_transport_unreachable(fixture, TimestampMs::new(1));
        let need = assess_transport_recovery(fixture, TimestampMs::new(1));
        fixture
            .control
            .begin_role_recovery(
                &need,
                TimestampMs::new(2),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("detected transport recovery should begin");
        need
    }

    /// Matches the fixture's unbound transport role without selecting a candidate.
    fn match_fixture_recovery_candidates(
        fixture: &mut RecoveryFixture,
        need: &RoleRecoveryNeed,
        timestamp: TimestampMs,
    ) -> RecoveryCandidateSet {
        fixture
            .control
            .match_recovery_candidates(
                &fixture.state,
                need,
                &fixture.requirement,
                timestamp,
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("role-scoped recovery matching should succeed")
    }

    /// Produces a validated non-committed proposal for the fixture's known Node B.
    fn propose_fixture_node_b(
        fixture: &mut RecoveryFixture,
        candidates: &RecoveryCandidateSet,
        timestamp: TimestampMs,
    ) -> RecoveryAssignmentProposal {
        fixture
            .control
            .propose_role_recovery(
                &fixture.state,
                candidates,
                &fixture.requirement,
                fixture.node_b_id.clone(),
                vec![fixture.space_b.clone()],
                timestamp,
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("known Node B recovery proposal should validate")
    }

    /// Commits the fixture's proposed Node B resources without rebinding the Group.
    fn commit_fixture_node_b(
        fixture: &mut RecoveryFixture,
        proposal: &RecoveryAssignmentProposal,
        timestamp: TimestampMs,
    ) -> CommittedRecoveryAssignment {
        fixture
            .control
            .commit_role_recovery(
                &fixture.state,
                &fixture.requirement,
                proposal,
                timestamp,
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("known Node B recovery proposal should commit")
    }

    /// Registers one additional transport replacement for recovery retry tests.
    fn register_transport_replacement(
        fixture: &mut RecoveryFixture,
        node_name: &str,
        resource_name: &str,
        timestamp: TimestampMs,
    ) -> (NodeId, ResourceId) {
        let registration = registration(node_name, CapabilityKind::Transport, resource_name);
        let node_id = registration.node_id().clone();
        let resource_id = ResourceId::new(resource_name).expect("test resource id must be valid");
        fixture
            .control
            .register_node(
                &mut fixture.state,
                registration,
                NodeStatus::new(NodeHealth::Online, timestamp),
                timestamp,
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("additional transport replacement should register");
        (node_id, resource_id)
    }

    /// A healthy active Group produces NoAction without lifecycle mutation or events.
    #[test]
    fn reconciliation_healthy_active_group_requires_no_action() {
        let mut fixture = recovery_fixture(true);
        let event_count = fixture.events.records.len();
        let assessment = fixture
            .control
            .assess_group(
                &fixture.state,
                &fixture.group_id,
                &fixture.requirement,
                TimestampMs::new(1),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("healthy Group assessment should succeed");

        assert_eq!(assessment, ReconciliationAssessment::NoAction);
        assert_eq!(fixture.events.records.len(), event_count);
        assert_eq!(
            fixture
                .control
                .group(&fixture.group_id)
                .expect("healthy Group should remain")
                .lifecycle(),
            GroupLifecycle::Active
        );
    }

    /// Detection identifies one unavailable assignment without modifying the Group.
    #[test]
    fn reconciliation_detects_unreachable_assignment_without_mutation() {
        let mut fixture = recovery_fixture(true);
        let original_assignments = fixture
            .control
            .group(&fixture.group_id)
            .expect("active Group should exist")
            .assignments()
            .to_vec();
        mark_transport_unreachable(&mut fixture, TimestampMs::new(1));
        let need = assess_transport_recovery(&mut fixture, TimestampMs::new(1));

        assert_eq!(need.group_id(), &fixture.group_id);
        assert_eq!(need.task_ref(), &fixture.task_ref);
        assert_eq!(need.role_id(), &fixture.transport_role);
        assert_eq!(need.current_node_id(), &fixture.node_a_id);
        let group = fixture
            .control
            .group(&fixture.group_id)
            .expect("assessment must retain the Group");
        assert_eq!(group.lifecycle(), GroupLifecycle::Active);
        assert_eq!(group.assignments(), original_assignments.as_slice());
        assert!(fixture.events.records.iter().any(|(_, payload)| matches!(
            payload,
            EventPayload::ReconciliationRoleRecoveryRequired {
                group_id,
                task_ref,
                role_id,
                node_id,
            } if group_id == &fixture.group_id
                && task_ref == &fixture.task_ref
                && role_id == &fixture.transport_role
                && node_id == &fixture.node_a_id
        )));
    }

    /// Beginning recovery blocks the Group and releases only the affected role.
    #[test]
    fn reconciliation_begin_recovery_preserves_unaffected_binding() {
        let mut fixture = recovery_fixture(true);
        let original_compute = fixture
            .control
            .group(&fixture.group_id)
            .expect("active Group should exist")
            .assignments()
            .iter()
            .find(|assignment| assignment.role_id() == &fixture.compute_role)
            .expect("compute assignment should exist")
            .clone();
        mark_transport_unreachable(&mut fixture, TimestampMs::new(1));
        let need = assess_transport_recovery(&mut fixture, TimestampMs::new(1));
        let outcome = fixture
            .control
            .begin_role_recovery(
                &need,
                TimestampMs::new(2),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("recovery should block and partially release transport");

        assert!(matches!(
            outcome,
            RecoveryOutcome::Pending { ref group_id, ref task_ref, ref role_id }
                if group_id == &fixture.group_id
                    && task_ref == &fixture.task_ref
                    && role_id == &fixture.transport_role
        ));
        let group = fixture
            .control
            .group(&fixture.group_id)
            .expect("blocked Group should remain");
        assert_eq!(group.group_id(), &fixture.group_id);
        assert_eq!(group.task_ref(), &fixture.task_ref);
        assert_eq!(group.lifecycle(), GroupLifecycle::Blocked);
        assert!(group.is_role_unbound(&fixture.transport_role));
        assert_eq!(
            group
                .assignments()
                .iter()
                .find(|assignment| assignment.role_id() == &fixture.compute_role),
            Some(&original_compute)
        );
        assert!(!fixture.control.reservations.contains_key(&fixture.space_a));
        assert!(
            fixture
                .control
                .reservations
                .contains_key(&fixture.compute_c)
        );
    }

    /// The complete role-scoped pipeline commits before rebinding and preserves Group context.
    #[test]
    fn recovery_pipeline_commits_then_rebinds_external_choice() {
        let mut fixture = recovery_fixture(true);
        let original_compute = fixture
            .control
            .group(&fixture.group_id)
            .expect("active Group should exist")
            .assignments()
            .iter()
            .find(|assignment| assignment.role_id() == &fixture.compute_role)
            .expect("compute assignment should exist")
            .clone();
        let need = begin_detected_transport_recovery(&mut fixture);
        let candidates =
            match_fixture_recovery_candidates(&mut fixture, &need, TimestampMs::new(3));
        assert_eq!(candidates.role_id(), &fixture.transport_role);
        assert_eq!(
            candidates.candidate_node_ids(),
            std::slice::from_ref(&fixture.node_b_id)
        );
        assert!(!candidates.candidate_node_ids().contains(&fixture.node_a_id));
        let proposal = propose_fixture_node_b(&mut fixture, &candidates, TimestampMs::new(4));
        assert!(!fixture.control.reservations.contains_key(&fixture.space_b));
        assert_eq!(
            fixture
                .control
                .group(&fixture.group_id)
                .expect("proposal must not bind the Group")
                .lifecycle(),
            GroupLifecycle::Blocked
        );
        let committed = commit_fixture_node_b(&mut fixture, &proposal, TimestampMs::new(5));
        assert_eq!(committed.group_id(), &fixture.group_id);
        assert_eq!(committed.task_ref(), &fixture.task_ref);
        assert_eq!(committed.role_id(), &fixture.transport_role);
        assert_eq!(
            fixture
                .control
                .pending_recovery_commitment(&fixture.group_id, &fixture.transport_role),
            Some(&committed)
        );
        assert_eq!(
            fixture
                .control
                .reservations
                .get(&fixture.space_b)
                .and_then(|reservation| reservation.group_id.as_ref()),
            Some(&fixture.group_id)
        );
        let committed_but_unbound = fixture
            .control
            .group(&fixture.group_id)
            .expect("committed Group should remain observable");
        assert_eq!(committed_but_unbound.lifecycle(), GroupLifecycle::Blocked);
        assert!(committed_but_unbound.is_role_unbound(&fixture.transport_role));

        let outcome = fixture
            .control
            .rebind_role(
                &committed,
                TimestampMs::new(6),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("committed replacement should rebind transport");
        assert!(
            fixture
                .control
                .pending_recovery_commitment(&fixture.group_id, &fixture.transport_role)
                .is_none()
        );
        assert_eq!(
            fixture
                .control
                .reservations
                .get(&fixture.space_b)
                .and_then(|reservation| reservation.group_id.as_ref()),
            Some(&fixture.group_id)
        );

        assert!(matches!(
            outcome,
            RecoveryOutcome::Recovered { ref group_id, ref role_id, ref from_node, ref to_node, .. }
                if group_id == &fixture.group_id
                    && role_id == &fixture.transport_role
                    && from_node == &fixture.node_a_id
                    && to_node == &fixture.node_b_id
        ));
        let adapted = fixture
            .control
            .group(&fixture.group_id)
            .expect("adapted Group should remain");
        assert_eq!(adapted.lifecycle(), GroupLifecycle::Adapted);
        assert_eq!(adapted.task_ref(), &fixture.task_ref);
        assert_eq!(
            adapted
                .assignments()
                .iter()
                .find(|assignment| assignment.role_id() == &fixture.compute_role),
            Some(&original_compute)
        );
        assert!(adapted.assignments().iter().any(|assignment| {
            assignment.role_id() == &fixture.transport_role
                && assignment.node_id() == &fixture.node_b_id
                && assignment.resource_ids() == std::slice::from_ref(&fixture.space_b)
        }));
        fixture
            .control
            .activate_group(
                &fixture.group_id,
                TimestampMs::new(7),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("adapted Group should reactivate");
        assert_eq!(
            fixture
                .control
                .group(&fixture.group_id)
                .expect("reactivated Group should remain")
                .lifecycle(),
            GroupLifecycle::Active
        );
    }

    /// A replacement that becomes unavailable after proposal is rejected at commit.
    #[test]
    fn recovery_commit_rejects_replacement_that_became_unavailable() {
        let mut fixture = recovery_fixture(true);
        let need = begin_detected_transport_recovery(&mut fixture);
        let candidates =
            match_fixture_recovery_candidates(&mut fixture, &need, TimestampMs::new(3));
        let proposal = propose_fixture_node_b(&mut fixture, &candidates, TimestampMs::new(4));
        fixture
            .state
            .record_node_liveness(
                &fixture.node_b_id,
                NodeLivenessObservation::new(NodeLiveness::Unreachable, TimestampMs::new(5)),
            )
            .expect("replacement liveness observation should be accepted");

        assert!(matches!(
            fixture.control.commit_role_recovery(
                &fixture.state,
                &fixture.requirement,
                &proposal,
                TimestampMs::new(5),
                &fixture.correlation_id,
                &mut fixture.events,
            ),
            Err(ControlError::InvalidProposal(_))
        ));
        let group = fixture
            .control
            .group(&fixture.group_id)
            .expect("pending Group should remain");
        assert_eq!(group.lifecycle(), GroupLifecycle::Blocked);
        assert!(group.is_role_unbound(&fixture.transport_role));
        assert!(!fixture.control.reservations.contains_key(&fixture.space_b));
        assert!(
            fixture
                .control
                .reservations
                .contains_key(&fixture.compute_c)
        );
    }

    /// Scheduler choices outside the role-scoped Candidate Set cannot become proposals.
    #[test]
    fn recovery_proposal_requires_candidate_membership() {
        let mut fixture = recovery_fixture(true);
        let need = begin_detected_transport_recovery(&mut fixture);
        let candidates =
            match_fixture_recovery_candidates(&mut fixture, &need, TimestampMs::new(3));
        let node_c = NodeId::new("node-c").expect("test node id must be valid");

        assert!(matches!(
            fixture.control.propose_role_recovery(
                &fixture.state,
                &candidates,
                &fixture.requirement,
                node_c,
                vec![fixture.space_b.clone()],
                TimestampMs::new(4),
                &fixture.correlation_id,
                &mut fixture.events,
            ),
            Err(ControlError::InvalidProposal(_))
        ));
        assert!(!fixture.control.reservations.contains_key(&fixture.space_b));
        assert_eq!(
            fixture
                .control
                .group(&fixture.group_id)
                .expect("invalid proposal must not mutate Group")
                .lifecycle(),
            GroupLifecycle::Blocked
        );
    }

    /// Resource conflict after proposal leaves every recovery resource uncommitted atomically.
    #[test]
    fn recovery_commit_conflict_is_atomic_and_multi_mission_isolated() {
        let mut fixture = recovery_fixture(true);
        let need = begin_detected_transport_recovery(&mut fixture);
        let candidates =
            match_fixture_recovery_candidates(&mut fixture, &need, TimestampMs::new(3));
        let proposal = fixture
            .control
            .propose_role_recovery(
                &fixture.state,
                &candidates,
                &fixture.requirement,
                fixture.node_b_id.clone(),
                vec![fixture.space_b.clone(), fixture.space_b_secondary.clone()],
                TimestampMs::new(4),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("both Node B resources should be valid at proposal time");
        assert!(!fixture.control.reservations.contains_key(&fixture.space_b));
        assert!(
            !fixture
                .control
                .reservations
                .contains_key(&fixture.space_b_secondary)
        );

        let mission_b_task = requirement_for_mission(
            "mission-b",
            "task-resource-owner",
            "transport-b",
            CapabilityKind::Transport,
        );
        let mission_b_candidates = fixture
            .control
            .match_capabilities(
                &fixture.state,
                &mission_b_task,
                TimestampMs::new(5),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("Mission B should match Node B");
        let mission_b_proposal = fixture
            .control
            .propose(
                &fixture.state,
                &mission_b_task,
                &mission_b_candidates,
                vec![RoleAssignment::new(
                    RoleId::new("transport-b").expect("test role id must be valid"),
                    fixture.node_b_id.clone(),
                    vec![fixture.space_b_secondary.clone()],
                )],
                TimestampMs::new(5),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("Mission B proposal should remain independent");
        fixture
            .control
            .commit(
                &mission_b_proposal,
                TimestampMs::new(5),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("Mission B should reserve the secondary resource");

        assert!(matches!(
            fixture.control.commit_role_recovery(
                &fixture.state,
                &fixture.requirement,
                &proposal,
                TimestampMs::new(6),
                &fixture.correlation_id,
                &mut fixture.events,
            ),
            Err(ControlError::ResourceConflict { resource_id, .. })
                if resource_id == fixture.space_b_secondary
        ));
        assert!(!fixture.control.reservations.contains_key(&fixture.space_b));
        let mission_b_reservation = fixture
            .control
            .reservations
            .get(&fixture.space_b_secondary)
            .expect("Mission B reservation must remain");
        assert_eq!(&mission_b_reservation.task_ref, mission_b_task.task_ref());
        assert!(mission_b_reservation.group_id.is_none());
        let group_a = fixture
            .control
            .group(&fixture.group_id)
            .expect("Mission A Group should remain pending");
        assert_eq!(group_a.lifecycle(), GroupLifecycle::Blocked);
        assert!(group_a.is_role_unbound(&fixture.transport_role));
        assert!(
            fixture
                .control
                .reservations
                .contains_key(&fixture.compute_c)
        );
    }

    /// Rebind rejects a commitment-shaped value when reservation authority has no commitment.
    #[test]
    fn recovery_rebind_without_reservation_commit_is_rejected() {
        let mut fixture = recovery_fixture(true);
        begin_detected_transport_recovery(&mut fixture);
        let uncommitted = CommittedRecoveryAssignment::new(
            fixture.group_id.clone(),
            fixture.task_ref.clone(),
            fixture.transport_role.clone(),
            fixture.node_a_id.clone(),
            fixture.node_b_id.clone(),
            vec![fixture.space_b.clone()],
        );

        assert!(matches!(
            fixture.control.rebind_role(
                &uncommitted,
                TimestampMs::new(3),
                &fixture.correlation_id,
                &mut fixture.events,
            ),
            Err(ControlError::PendingRecoveryCommitmentNotFound { .. })
        ));
        let group = fixture
            .control
            .group(&fixture.group_id)
            .expect("uncommitted rebind must retain Group");
        assert_eq!(group.lifecycle(), GroupLifecycle::Blocked);
        assert!(group.is_role_unbound(&fixture.transport_role));
        assert!(!fixture.control.reservations.contains_key(&fixture.space_b));
    }

    /// Committed rebind is legal only for a Blocked Group with an unbound role.
    #[test]
    fn recovery_rebind_requires_blocked_lifecycle() {
        let mut fixture = recovery_fixture(true);
        let fake_commitment = CommittedRecoveryAssignment::new(
            fixture.group_id.clone(),
            fixture.task_ref.clone(),
            fixture.transport_role.clone(),
            fixture.node_a_id.clone(),
            fixture.node_b_id.clone(),
            vec![fixture.space_b.clone()],
        );
        assert!(matches!(
            fixture.control.rebind_role(
                &fake_commitment,
                TimestampMs::new(1),
                &fixture.correlation_id,
                &mut fixture.events,
            ),
            Err(ControlError::InvalidLifecycle(GroupLifecycle::Active))
        ));

        let need = begin_detected_transport_recovery(&mut fixture);
        let candidates =
            match_fixture_recovery_candidates(&mut fixture, &need, TimestampMs::new(3));
        let proposal = propose_fixture_node_b(&mut fixture, &candidates, TimestampMs::new(4));
        let committed = commit_fixture_node_b(&mut fixture, &proposal, TimestampMs::new(5));
        fixture
            .control
            .rebind_role(
                &committed,
                TimestampMs::new(6),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("Blocked committed recovery should rebind");
        assert!(matches!(
            fixture.control.rebind_role(
                &committed,
                TimestampMs::new(7),
                &fixture.correlation_id,
                &mut fixture.events,
            ),
            Err(ControlError::InvalidLifecycle(GroupLifecycle::Adapted))
        ));
    }

    /// A Group role cannot own two simultaneous pending recovery commitments.
    #[test]
    fn second_recovery_commit_for_same_group_role_is_rejected() {
        let mut fixture = recovery_fixture(true);
        let need = begin_detected_transport_recovery(&mut fixture);
        let candidates =
            match_fixture_recovery_candidates(&mut fixture, &need, TimestampMs::new(3));
        let proposal_b = propose_fixture_node_b(&mut fixture, &candidates, TimestampMs::new(4));
        let committed_b = commit_fixture_node_b(&mut fixture, &proposal_b, TimestampMs::new(5));
        let (node_c, space_c) =
            register_transport_replacement(&mut fixture, "node-c", "space-c", TimestampMs::new(6));
        let candidates_c =
            match_fixture_recovery_candidates(&mut fixture, &need, TimestampMs::new(6));
        let proposal_c = fixture
            .control
            .propose_role_recovery(
                &fixture.state,
                &candidates_c,
                &fixture.requirement,
                node_c,
                vec![space_c.clone()],
                TimestampMs::new(6),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("a second non-authoritative proposal may be created");

        assert!(matches!(
            fixture.control.commit_role_recovery(
                &fixture.state,
                &fixture.requirement,
                &proposal_c,
                TimestampMs::new(7),
                &fixture.correlation_id,
                &mut fixture.events,
            ),
            Err(ControlError::PendingRecoveryCommitmentExists { .. })
        ));
        assert_eq!(
            fixture
                .control
                .pending_recovery_commitment(&fixture.group_id, &fixture.transport_role),
            Some(&committed_b)
        );
        assert!(fixture.control.reservations.contains_key(&fixture.space_b));
        assert!(!fixture.control.reservations.contains_key(&space_c));
    }

    /// Abort releases only replacement resources and invalidates the old commitment handle.
    #[test]
    fn abort_recovery_commitment_preserves_group_and_rejects_stale_handle() {
        let mut fixture = recovery_fixture(true);
        let need = begin_detected_transport_recovery(&mut fixture);
        let candidates =
            match_fixture_recovery_candidates(&mut fixture, &need, TimestampMs::new(3));
        let proposal = propose_fixture_node_b(&mut fixture, &candidates, TimestampMs::new(4));
        let committed = commit_fixture_node_b(&mut fixture, &proposal, TimestampMs::new(5));
        fixture
            .control
            .abort_role_recovery_commitment(
                &committed,
                TimestampMs::new(6),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("pending Node B commitment should abort");

        assert!(
            fixture
                .control
                .pending_recovery_commitment(&fixture.group_id, &fixture.transport_role)
                .is_none()
        );
        assert!(!fixture.control.reservations.contains_key(&fixture.space_b));
        assert!(
            fixture
                .control
                .reservations
                .contains_key(&fixture.compute_c)
        );
        let group = fixture
            .control
            .group(&fixture.group_id)
            .expect("aborted recovery Group should remain");
        assert_eq!(group.lifecycle(), GroupLifecycle::Blocked);
        assert!(group.is_role_unbound(&fixture.transport_role));
        assert_eq!(group.assignments().len(), 1);
        assert_eq!(group.assignments()[0].role_id(), &fixture.compute_role);
        assert!(matches!(
            fixture.control.rebind_role(
                &committed,
                TimestampMs::new(7),
                &fixture.correlation_id,
                &mut fixture.events,
            ),
            Err(ControlError::PendingRecoveryCommitmentNotFound { .. })
        ));
        assert!(fixture.events.records.iter().any(|(_, payload)| matches!(
            payload,
            EventPayload::RecoveryAssignmentAborted { group_id, role_id, resource_ids, .. }
                if group_id == &fixture.group_id
                    && role_id == &fixture.transport_role
                    && resource_ids == std::slice::from_ref(&fixture.space_b)
        )));
    }

    /// Abort returns recovery to Pending and permits a new Node C commitment.
    #[test]
    fn abort_permits_new_recovery_commitment() {
        let mut fixture = recovery_fixture(true);
        let need = begin_detected_transport_recovery(&mut fixture);
        let candidates_b =
            match_fixture_recovery_candidates(&mut fixture, &need, TimestampMs::new(3));
        let proposal_b = propose_fixture_node_b(&mut fixture, &candidates_b, TimestampMs::new(4));
        let committed_b = commit_fixture_node_b(&mut fixture, &proposal_b, TimestampMs::new(5));
        fixture
            .control
            .abort_role_recovery_commitment(
                &committed_b,
                TimestampMs::new(6),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("Node B attempt should abort");
        let (node_c, space_c) =
            register_transport_replacement(&mut fixture, "node-c", "space-c", TimestampMs::new(7));
        let candidates_c =
            match_fixture_recovery_candidates(&mut fixture, &need, TimestampMs::new(7));
        let proposal_c = fixture
            .control
            .propose_role_recovery(
                &fixture.state,
                &candidates_c,
                &fixture.requirement,
                node_c.clone(),
                vec![space_c.clone()],
                TimestampMs::new(8),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("bootstrap scheduler should propose Node C");
        let committed_c = fixture
            .control
            .commit_role_recovery(
                &fixture.state,
                &fixture.requirement,
                &proposal_c,
                TimestampMs::new(9),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("Node C attempt should commit after Abort");

        assert_eq!(committed_c.replacement_node_id(), &node_c);
        assert_eq!(
            fixture
                .control
                .pending_recovery_commitment(&fixture.group_id, &fixture.transport_role),
            Some(&committed_c)
        );
        assert!(!fixture.control.reservations.contains_key(&fixture.space_b));
        assert!(fixture.control.reservations.contains_key(&space_c));
        assert!(matches!(
            fixture.control.rebind_role(
                &committed_b,
                TimestampMs::new(10),
                &fixture.correlation_id,
                &mut fixture.events,
            ),
            Err(ControlError::PendingRecoveryCommitmentMismatch { .. })
        ));
    }

    /// Failed terminal release cleans committed-but-not-bound resources and pending authority.
    #[test]
    fn failed_group_release_cleans_pending_recovery_commitment() {
        let mut fixture = recovery_fixture(true);
        let need = begin_detected_transport_recovery(&mut fixture);
        let candidates =
            match_fixture_recovery_candidates(&mut fixture, &need, TimestampMs::new(3));
        let proposal = propose_fixture_node_b(&mut fixture, &candidates, TimestampMs::new(4));
        let committed = commit_fixture_node_b(&mut fixture, &proposal, TimestampMs::new(5));
        fixture
            .control
            .fail_group(
                &fixture.group_id,
                "recovery explicitly exhausted",
                TimestampMs::new(6),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("Blocked Group should explicitly fail");
        fixture
            .control
            .release_group(
                &fixture.group_id,
                TimestampMs::new(7),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("Failed Group should release all ownership");

        assert_eq!(
            fixture
                .control
                .group(&fixture.group_id)
                .expect("Released Group should remain observable")
                .lifecycle(),
            GroupLifecycle::Released
        );
        assert!(
            fixture
                .control
                .pending_recovery_commitment(&fixture.group_id, &fixture.transport_role)
                .is_none()
        );
        assert!(!fixture.control.reservations.contains_key(&fixture.space_b));
        assert!(
            !fixture
                .control
                .reservations
                .contains_key(&fixture.compute_c)
        );
        assert!(
            !fixture
                .control
                .reservations
                .values()
                .any(|reservation| { reservation.group_id.as_ref() == Some(&fixture.group_id) })
        );
        assert!(matches!(
            fixture.control.rebind_role(
                &committed,
                TimestampMs::new(8),
                &fixture.correlation_id,
                &mut fixture.events,
            ),
            Err(ControlError::InvalidLifecycle(GroupLifecycle::Released))
        ));
    }

    /// Terminal cleanup for Mission A cannot remove an active Mission B reservation.
    #[test]
    fn pending_cleanup_is_multi_mission_isolated() {
        let mut fixture = recovery_fixture(true);
        let need = begin_detected_transport_recovery(&mut fixture);
        let candidates =
            match_fixture_recovery_candidates(&mut fixture, &need, TimestampMs::new(3));
        let proposal = propose_fixture_node_b(&mut fixture, &candidates, TimestampMs::new(4));
        let committed = commit_fixture_node_b(&mut fixture, &proposal, TimestampMs::new(5));
        let (node_c, space_c) =
            register_transport_replacement(&mut fixture, "node-c", "space-c", TimestampMs::new(6));
        let mission_b_task = requirement_for_mission(
            "mission-b",
            "task-b",
            "transport-b",
            CapabilityKind::Transport,
        );
        let mission_b_candidates = fixture
            .control
            .match_capabilities(
                &fixture.state,
                &mission_b_task,
                TimestampMs::new(6),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("Mission B should match Node C");
        let mission_b_proposal = fixture
            .control
            .propose(
                &fixture.state,
                &mission_b_task,
                &mission_b_candidates,
                vec![RoleAssignment::new(
                    RoleId::new("transport-b").expect("test role id must be valid"),
                    node_c,
                    vec![space_c.clone()],
                )],
                TimestampMs::new(6),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("Mission B proposal should succeed");
        let mission_b_plan = fixture
            .control
            .commit(
                &mission_b_proposal,
                TimestampMs::new(6),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("Mission B resource should commit");
        let group_b = ExecutionGroupId::new("group-b").expect("test group id must be valid");
        fixture
            .control
            .create_group(
                group_b.clone(),
                &mission_b_plan,
                TimestampMs::new(6),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("Mission B Group should bind");
        fixture
            .control
            .activate_group(
                &group_b,
                TimestampMs::new(6),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("Mission B Group should activate");

        fixture
            .control
            .abort_role_recovery_commitment(
                &committed,
                TimestampMs::new(7),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("Mission A pending commitment should abort");
        fixture
            .control
            .fail_group(
                &fixture.group_id,
                "Mission A recovery exhausted",
                TimestampMs::new(8),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("Mission A should fail explicitly");
        fixture
            .control
            .release_group(
                &fixture.group_id,
                TimestampMs::new(9),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("Mission A should release");

        assert_eq!(
            fixture
                .control
                .group(&group_b)
                .expect("Mission B Group should remain")
                .lifecycle(),
            GroupLifecycle::Active
        );
        assert_eq!(
            fixture
                .control
                .reservations
                .get(&space_c)
                .and_then(|reservation| reservation.group_id.as_ref()),
            Some(&group_b)
        );
    }

    /// Abort validates every resource before mutating any pending ownership.
    #[test]
    fn multi_resource_abort_is_atomic_on_ownership_mismatch() {
        let mut fixture = recovery_fixture(true);
        let need = begin_detected_transport_recovery(&mut fixture);
        let candidates =
            match_fixture_recovery_candidates(&mut fixture, &need, TimestampMs::new(3));
        let proposal = fixture
            .control
            .propose_role_recovery(
                &fixture.state,
                &candidates,
                &fixture.requirement,
                fixture.node_b_id.clone(),
                vec![fixture.space_b.clone(), fixture.space_b_secondary.clone()],
                TimestampMs::new(4),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("multi-resource proposal should validate");
        let committed = fixture
            .control
            .commit_role_recovery(
                &fixture.state,
                &fixture.requirement,
                &proposal,
                TimestampMs::new(5),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("multi-resource proposal should commit");
        fixture
            .control
            .reservations
            .get_mut(&fixture.space_b_secondary)
            .expect("secondary reservation should exist")
            .role_id = RoleId::new("other-role").expect("test role id must be valid");

        assert!(matches!(
            fixture.control.abort_role_recovery_commitment(
                &committed,
                TimestampMs::new(6),
                &fixture.correlation_id,
                &mut fixture.events,
            ),
            Err(ControlError::InvalidProposal(_))
        ));
        assert!(fixture.control.reservations.contains_key(&fixture.space_b));
        assert!(
            fixture
                .control
                .reservations
                .contains_key(&fixture.space_b_secondary)
        );
        assert_eq!(
            fixture
                .control
                .pending_recovery_commitment(&fixture.group_id, &fixture.transport_role),
            Some(&committed)
        );
    }

    /// Roles without resources still require authoritative pending commitment consumption.
    #[test]
    fn zero_resource_recovery_commitment_is_tracked_and_consumed() {
        let node_a = registration("node-zero-a", CapabilityKind::Observation, "space-zero-a");
        let node_b = registration("node-zero-b", CapabilityKind::Observation, "space-zero-b");
        let node_a_id = node_a.node_id().clone();
        let node_b_id = node_b.node_id().clone();
        let mission_id =
            domain::MissionId::new("mission-zero").expect("test mission id must be valid");
        let role_id = RoleId::new("observe").expect("test role id must be valid");
        let requirement = TaskRequirement::new(
            mission_id,
            TaskId::new("task-zero").expect("test task id must be valid"),
            vec![RoleRequirement::new(
                role_id.clone(),
                CapabilityKind::Observation,
                None,
            )],
        )
        .expect("zero-resource requirement should be valid");
        let group_id = ExecutionGroupId::new("group-zero").expect("test group id must be valid");
        let correlation_id = correlation();
        let timestamp = TimestampMs::new(0);
        let mut control = ControlPlane::new();
        let mut state = InMemorySharedNodeState::new();
        let mut events = RecordingEvents::default();
        for node in [node_a, node_b] {
            control
                .register_node(
                    &mut state,
                    node,
                    NodeStatus::new(NodeHealth::Online, timestamp),
                    timestamp,
                    &correlation_id,
                    &mut events,
                )
                .expect("zero-resource node should register");
        }
        let candidates = control
            .match_capabilities(
                &state,
                &requirement,
                timestamp,
                &correlation_id,
                &mut events,
            )
            .expect("zero-resource task should match");
        let proposal = control
            .propose(
                &state,
                &requirement,
                &candidates,
                vec![RoleAssignment::new(
                    role_id.clone(),
                    node_a_id.clone(),
                    vec![],
                )],
                timestamp,
                &correlation_id,
                &mut events,
            )
            .expect("zero-resource proposal should validate");
        let plan = control
            .commit(&proposal, timestamp, &correlation_id, &mut events)
            .expect("zero-resource proposal should commit");
        control
            .create_group(
                group_id.clone(),
                &plan,
                timestamp,
                &correlation_id,
                &mut events,
            )
            .expect("zero-resource Group should bind");
        control
            .activate_group(&group_id, timestamp, &correlation_id, &mut events)
            .expect("zero-resource Group should activate");
        state
            .record_node_liveness(
                &node_a_id,
                NodeLivenessObservation::new(NodeLiveness::Unreachable, TimestampMs::new(1)),
            )
            .expect("source node should become unreachable");
        let assessment = control
            .assess_group(
                &state,
                &group_id,
                &requirement,
                TimestampMs::new(1),
                &correlation_id,
                &mut events,
            )
            .expect("zero-resource Group assessment should succeed");
        let ReconciliationAssessment::RoleRecoveryRequired(need) = assessment else {
            panic!("zero-resource role should require recovery");
        };
        control
            .begin_role_recovery(&need, TimestampMs::new(2), &correlation_id, &mut events)
            .expect("zero-resource recovery should begin");
        let recovery_candidates = control
            .match_recovery_candidates(
                &state,
                &need,
                &requirement,
                TimestampMs::new(3),
                &correlation_id,
                &mut events,
            )
            .expect("zero-resource role should rematch");
        let recovery_proposal = control
            .propose_role_recovery(
                &state,
                &recovery_candidates,
                &requirement,
                node_b_id.clone(),
                vec![],
                TimestampMs::new(4),
                &correlation_id,
                &mut events,
            )
            .expect("zero-resource replacement should be proposed");
        let committed = control
            .commit_role_recovery(
                &state,
                &requirement,
                &recovery_proposal,
                TimestampMs::new(5),
                &correlation_id,
                &mut events,
            )
            .expect("zero-resource replacement should commit");
        assert!(committed.committed_resource_ids().is_empty());
        assert_eq!(
            control.pending_recovery_commitment(&group_id, &role_id),
            Some(&committed)
        );
        control
            .rebind_role(
                &committed,
                TimestampMs::new(6),
                &correlation_id,
                &mut events,
            )
            .expect("zero-resource commitment should be consumed by rebind");
        assert!(
            control
                .pending_recovery_commitment(&group_id, &role_id)
                .is_none()
        );
        let group = control
            .group(&group_id)
            .expect("zero-resource Group should remain");
        assert_eq!(group.lifecycle(), GroupLifecycle::Adapted);
        assert_eq!(group.assignments()[0].node_id(), &node_b_id);
        assert!(group.assignments()[0].resource_ids().is_empty());
    }

    /// Missing replacement input leaves the Group pending rather than Failed or Released.
    #[test]
    fn reconciliation_without_replacement_remains_pending() {
        let mut fixture = recovery_fixture(false);
        let need = begin_detected_transport_recovery(&mut fixture);
        let candidates =
            match_fixture_recovery_candidates(&mut fixture, &need, TimestampMs::new(3));

        assert!(candidates.is_empty());
        let group = fixture
            .control
            .group(&fixture.group_id)
            .expect("pending Group should remain");
        assert_eq!(group.lifecycle(), GroupLifecycle::Blocked);
        assert!(group.is_role_unbound(&fixture.transport_role));
        assert!(!fixture.events.records.iter().any(|(_, payload)| matches!(
            payload,
            EventPayload::ExecutionGroupFailed { .. } | EventPayload::ExecutionGroupReleased { .. }
        )));
    }

    /// Reconciliation uses receive-time freshness from the shared eligibility predicate.
    #[test]
    fn reconciliation_detects_stale_assignment_with_large_source_time() {
        let mut fixture = recovery_fixture(true);
        fixture
            .state
            .record_node_health(NodeHealthObservation::new(
                fixture.node_a_id.clone(),
                NodeStatus::new(NodeHealth::Online, TimestampMs::new(1_000_000)),
                TimestampMs::new(0),
            ))
            .expect("equal receive time should preserve source evidence");
        fixture
            .state
            .record_node_health(NodeHealthObservation::new(
                fixture.edge_c_id.clone(),
                NodeStatus::new(NodeHealth::Online, TimestampMs::new(1)),
                TimestampMs::new(5_001),
            ))
            .expect("compute health should remain fresh");

        let need = assess_transport_recovery(&mut fixture, TimestampMs::new(5_001));
        assert_eq!(need.role_id(), &fixture.transport_role);
        assert_eq!(need.current_node_id(), &fixture.node_a_id);
        assert_eq!(
            fixture
                .control
                .group(&fixture.group_id)
                .expect("assessment must not mutate Group")
                .lifecycle(),
            GroupLifecycle::Active
        );
    }

    /// Lease-derived Unreachable state triggers the same shared eligibility policy.
    #[test]
    fn reconciliation_detects_lease_expired_assignment() {
        let mut fixture = recovery_fixture(false);
        fixture
            .control
            .accept_heartbeat(
                &mut fixture.state,
                NodeHeartbeat::new(
                    fixture.edge_c_id.clone(),
                    LeaseId::new("lease-edge-c").expect("test lease id should be valid"),
                    NodeStatus::new(NodeHealth::Online, TimestampMs::new(10)),
                ),
                TimestampMs::new(DEFAULT_NODE_LEASE_TTL_MS - 1),
                DEFAULT_NODE_LEASE_TTL_MS,
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("compute lease should renew before expiry");
        fixture
            .control
            .expire_leases(
                &mut fixture.state,
                TimestampMs::new(DEFAULT_NODE_LEASE_TTL_MS),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("expired transport lease should update liveness");

        let need =
            assess_transport_recovery(&mut fixture, TimestampMs::new(DEFAULT_NODE_LEASE_TTL_MS));
        assert_eq!(need.role_id(), &fixture.transport_role);
        assert_eq!(need.current_node_id(), &fixture.node_a_id);
    }

    /// Blocked preserves the Group and every binding until recovery acts explicitly.
    #[test]
    fn blocked_does_not_release_whole_group() {
        let mut fixture = recovery_fixture(true);
        block_fixture(&mut fixture);

        let group = fixture
            .control
            .group(&fixture.group_id)
            .expect("blocked Group should remain");
        assert_eq!(group.group_id(), &fixture.group_id);
        assert_eq!(group.task_ref(), &fixture.task_ref);
        assert_eq!(group.lifecycle(), GroupLifecycle::Blocked);
        assert!(group.assignments().iter().any(|assignment| {
            assignment.role_id() == &fixture.compute_role
                && assignment.resource_ids() == std::slice::from_ref(&fixture.compute_c)
        }));
        assert_eq!(
            fixture
                .control
                .reservations
                .get(&fixture.compute_c)
                .and_then(|reservation| reservation.group_id.as_ref()),
            Some(&fixture.group_id)
        );
        assert!(
            !fixture
                .events
                .records
                .iter()
                .any(|(_, payload)| matches!(payload, EventPayload::ExecutionGroupReleased { .. }))
        );
    }

    /// Partial release removes only the failed role's assignment and reservations.
    #[test]
    fn partial_release_preserves_unaffected_bindings() {
        let mut fixture = recovery_fixture(true);
        block_fixture(&mut fixture);
        fixture
            .control
            .release_role_binding(
                &fixture.group_id,
                &fixture.transport_role,
                TimestampMs::new(2),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("failed transport binding should release");

        let group = fixture
            .control
            .group(&fixture.group_id)
            .expect("partially released Group should remain");
        assert_eq!(group.lifecycle(), GroupLifecycle::Blocked);
        assert!(group.is_role_unbound(&fixture.transport_role));
        assert!(!fixture.control.reservations.contains_key(&fixture.space_a));
        assert!(
            fixture
                .control
                .reservations
                .contains_key(&fixture.compute_c)
        );
        assert_eq!(group.assignments().len(), 1);
        assert_eq!(group.assignments()[0].role_id(), &fixture.compute_role);
        assert!(
            fixture
                .events
                .records
                .iter()
                .any(|(correlation_id, payload)| {
                    correlation_id == &fixture.correlation_id
                        && matches!(
                            payload,
                            EventPayload::ExecutionGroupRoleBindingReleased {
                                group_id,
                                task_ref,
                                role_id,
                                resource_ids,
                                ..
                            } if group_id == &fixture.group_id
                                && task_ref == &fixture.task_ref
                                && role_id == &fixture.transport_role
                                && resource_ids == std::slice::from_ref(&fixture.space_a)
                        )
                })
        );
    }

    /// Partial release is legal only after the Group explicitly becomes Blocked.
    #[test]
    fn partial_release_requires_blocked_lifecycle() {
        let mut fixture = recovery_fixture(true);
        assert!(matches!(
            fixture.control.release_role_binding(
                &fixture.group_id,
                &fixture.transport_role,
                TimestampMs::new(1),
                &fixture.correlation_id,
                &mut fixture.events,
            ),
            Err(ControlError::InvalidLifecycle(GroupLifecycle::Active))
        ));

        let need = begin_detected_transport_recovery(&mut fixture);
        let candidates =
            match_fixture_recovery_candidates(&mut fixture, &need, TimestampMs::new(3));
        let proposal = propose_fixture_node_b(&mut fixture, &candidates, TimestampMs::new(4));
        let committed = commit_fixture_node_b(&mut fixture, &proposal, TimestampMs::new(5));
        fixture
            .control
            .rebind_role(
                &committed,
                TimestampMs::new(6),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("blocked Group should recover to Adapted");
        assert!(matches!(
            fixture.control.release_role_binding(
                &fixture.group_id,
                &fixture.compute_role,
                TimestampMs::new(7),
                &fixture.correlation_id,
                &mut fixture.events,
            ),
            Err(ControlError::InvalidLifecycle(GroupLifecycle::Adapted))
        ));
    }

    /// A blocked Group rebinds only the failed role and reactivates in place.
    #[test]
    fn blocked_group_recovers_through_adapted_and_active() {
        let mut fixture = recovery_fixture(true);
        let original_compute = fixture
            .control
            .group(&fixture.group_id)
            .expect("active Group should exist")
            .assignments()
            .iter()
            .find(|assignment| assignment.role_id() == &fixture.compute_role)
            .expect("compute assignment should exist")
            .clone();
        let need = begin_detected_transport_recovery(&mut fixture);
        let candidates =
            match_fixture_recovery_candidates(&mut fixture, &need, TimestampMs::new(3));
        let proposal = propose_fixture_node_b(&mut fixture, &candidates, TimestampMs::new(4));
        let committed = commit_fixture_node_b(&mut fixture, &proposal, TimestampMs::new(5));
        fixture
            .control
            .rebind_role(
                &committed,
                TimestampMs::new(6),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("released role should rebind to Node B");

        let adapted = fixture
            .control
            .group(&fixture.group_id)
            .expect("adapted Group should retain identity");
        assert_eq!(adapted.group_id(), &fixture.group_id);
        assert_eq!(adapted.task_ref(), &fixture.task_ref);
        assert_eq!(adapted.lifecycle(), GroupLifecycle::Adapted);
        assert!(!adapted.is_role_unbound(&fixture.transport_role));
        assert_eq!(
            adapted
                .assignments()
                .iter()
                .find(|assignment| assignment.role_id() == &fixture.compute_role),
            Some(&original_compute)
        );
        assert!(adapted.assignments().iter().any(|assignment| {
            assignment.role_id() == &fixture.transport_role
                && assignment.node_id() == &fixture.node_b_id
                && assignment.resource_ids() == std::slice::from_ref(&fixture.space_b)
        }));
        assert!(
            fixture
                .control
                .reservations
                .contains_key(&fixture.compute_c)
        );
        assert!(fixture.control.reservations.contains_key(&fixture.space_b));
        fixture
            .control
            .activate_group(
                &fixture.group_id,
                TimestampMs::new(7),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("fully rebound Group should reactivate");
        assert_eq!(
            fixture
                .control
                .group(&fixture.group_id)
                .expect("reactivated Group should remain")
                .lifecycle(),
            GroupLifecycle::Active
        );
        assert!(
            fixture
                .events
                .records
                .iter()
                .any(|(correlation_id, payload)| {
                    correlation_id == &fixture.correlation_id
                        && matches!(
                            payload,
                            EventPayload::RecoveryRebound {
                                group_id,
                                task_ref,
                                role_id,
                                from_node,
                                to_node,
                            } if group_id == &fixture.group_id
                                && task_ref == &fixture.task_ref
                                && role_id == &fixture.transport_role
                                && from_node == &fixture.node_a_id
                                && to_node == &fixture.node_b_id
                        )
                })
        );
    }

    /// Exhausted recovery explicitly enters Failed before whole-group release.
    #[test]
    fn recovery_exhausted_transitions_through_failed() {
        let mut fixture = recovery_fixture(false);
        block_fixture(&mut fixture);
        fixture
            .control
            .release_role_binding(
                &fixture.group_id,
                &fixture.transport_role,
                TimestampMs::new(2),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("failed transport binding should release");
        fixture
            .state
            .record_node_health(NodeHealthObservation::new(
                fixture.node_a_id.clone(),
                NodeStatus::new(NodeHealth::Offline, TimestampMs::new(3)),
                TimestampMs::new(3),
            ))
            .expect("offline observation should update Shared State");
        assert!(matches!(
            fixture.control.match_capabilities(
                &fixture.state,
                &fixture.requirement,
                TimestampMs::new(3),
                &fixture.correlation_id,
                &mut fixture.events,
            ),
            Err(ControlError::NoCandidate(role_id)) if role_id == fixture.transport_role
        ));
        fixture
            .control
            .fail_group(
                &fixture.group_id,
                "no replacement candidate",
                TimestampMs::new(4),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("blocked Group should explicitly fail");
        assert_eq!(
            fixture
                .control
                .group(&fixture.group_id)
                .expect("failed Group should remain observable")
                .lifecycle(),
            GroupLifecycle::Failed
        );
        assert!(fixture.events.records.iter().any(|(_, payload)| matches!(
            payload,
            EventPayload::ExecutionGroupFailed { group_id, task_ref, .. }
                if group_id == &fixture.group_id && task_ref == &fixture.task_ref
        )));
        fixture
            .control
            .release_group(
                &fixture.group_id,
                TimestampMs::new(5),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("failed Group should release remaining bindings");
        assert_eq!(
            fixture
                .control
                .group(&fixture.group_id)
                .expect("released Group should remain observable")
                .lifecycle(),
            GroupLifecycle::Released
        );
        assert!(!fixture.control.reservations.contains_key(&fixture.space_a));
        assert!(
            !fixture
                .control
                .reservations
                .contains_key(&fixture.compute_c)
        );
    }

    /// Completed releases every remaining reservation and emits whole-group evidence.
    #[test]
    fn completed_group_releases_all_bindings() {
        let mut fixture = recovery_fixture(true);
        fixture
            .control
            .complete_group(
                &fixture.group_id,
                TimestampMs::new(1),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("active Group should complete");
        fixture
            .control
            .release_group(
                &fixture.group_id,
                TimestampMs::new(2),
                &fixture.correlation_id,
                &mut fixture.events,
            )
            .expect("completed Group should release");

        let group = fixture
            .control
            .group(&fixture.group_id)
            .expect("released Group should remain observable");
        assert_eq!(group.lifecycle(), GroupLifecycle::Released);
        assert!(group.assignments().is_empty());
        assert!(!fixture.control.reservations.contains_key(&fixture.space_a));
        assert!(
            !fixture
                .control
                .reservations
                .contains_key(&fixture.compute_c)
        );
        assert!(fixture.events.records.iter().any(|(_, payload)| matches!(
            payload,
            EventPayload::ExecutionGroupReleased { group_id, task_ref, resource_ids }
                if group_id == &fixture.group_id
                    && task_ref == &fixture.task_ref
                    && resource_ids.contains(&fixture.space_a)
                    && resource_ids.contains(&fixture.compute_c)
        )));
    }

    /// Blocked rejects direct whole-group release and retains all reservations.
    #[test]
    fn blocked_group_cannot_release_directly() {
        let mut fixture = recovery_fixture(true);
        block_fixture(&mut fixture);
        assert!(matches!(
            fixture.control.release_group(
                &fixture.group_id,
                TimestampMs::new(2),
                &fixture.correlation_id,
                &mut fixture.events,
            ),
            Err(ControlError::InvalidLifecycle(GroupLifecycle::Blocked))
        ));
        assert!(fixture.control.reservations.contains_key(&fixture.space_a));
        assert!(
            fixture
                .control
                .reservations
                .contains_key(&fixture.compute_c)
        );
        assert!(
            !fixture
                .events
                .records
                .iter()
                .any(|(_, payload)| matches!(payload, EventPayload::ExecutionGroupReleased { .. }))
        );
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
        let mut state = InMemorySharedNodeState::new();
        let mut events = TestEvents;

        control
            .register_node(
                &mut state,
                node,
                NodeStatus::new(NodeHealth::Online, timestamp),
                timestamp,
                &correlation_id,
                &mut events,
            )
            .expect("test node registration should succeed");
        let stored = state
            .node(&node_id)
            .expect("registration should be readable from Shared State");
        assert_eq!(stored.registration().capabilities().len(), 1);
        assert_eq!(stored.registration().resources().len(), 1);
        assert_eq!(stored.reported_status().health(), NodeHealth::Online);
        let candidates = control
            .match_capabilities(&state, &task, timestamp, &correlation_id, &mut events)
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
                &state,
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

    /// Matching reads heterogeneous node capability facts from Shared State.
    #[test]
    fn matching_reads_shared_state_capability_facts() {
        let node_a = registration_with_resource_kind(
            "node-a",
            CapabilityKind::Transport,
            "space-a",
            ResourceKind::Space,
        );
        let node_b = registration_with_resource_kind(
            "node-b",
            CapabilityKind::Compute,
            "compute-b",
            ResourceKind::Compute,
        );
        let requirement = TaskRequirement::new(
            domain::MissionId::new("mission-shared-state")
                .expect("test mission id should be valid"),
            TaskId::new("task-01").expect("test task id should be valid"),
            vec![
                RoleRequirement::new(
                    RoleId::new("transport").expect("test role id should be valid"),
                    CapabilityKind::Transport,
                    Some(ResourceKind::Space),
                ),
                RoleRequirement::new(
                    RoleId::new("compute").expect("test role id should be valid"),
                    CapabilityKind::Compute,
                    Some(ResourceKind::Compute),
                ),
            ],
        )
        .expect("test requirement should be valid");
        let timestamp = TimestampMs::new(0);
        let correlation_id = correlation();
        let mut control = ControlPlane::new();
        let mut state = InMemorySharedNodeState::new();
        let mut events = TestEvents;
        for node in [node_a, node_b] {
            control
                .register_node(
                    &mut state,
                    node,
                    NodeStatus::new(NodeHealth::Online, timestamp),
                    timestamp,
                    &correlation_id,
                    &mut events,
                )
                .expect("test node registration should succeed");
        }

        let candidates = control
            .match_capabilities(
                &state,
                &requirement,
                timestamp,
                &correlation_id,
                &mut events,
            )
            .expect("heterogeneous state facts should satisfy both roles");
        assert_eq!(
            candidates
                .for_role(&RoleId::new("transport").expect("test role id should be valid"))
                .expect("transport candidates should exist")
                .node_ids()[0]
                .as_str(),
            "node-a"
        );
        assert_eq!(
            candidates
                .for_role(&RoleId::new("compute").expect("test role id should be valid"))
                .expect("compute candidates should exist")
                .node_ids()[0]
                .as_str(),
            "node-b"
        );
    }

    /// Concurrent missions share node facts while retaining distinct TaskRefs.
    #[test]
    fn multi_mission_matching_shares_state_without_identity_collision() {
        let node = registration("node-shared", CapabilityKind::Transport, "space-shared");
        let node_id = node.node_id().clone();
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
        let timestamp = TimestampMs::new(0);
        let correlation_id = correlation();
        let mut control = ControlPlane::new();
        let mut state = InMemorySharedNodeState::new();
        let mut events = TestEvents;
        control
            .register_node(
                &mut state,
                node,
                NodeStatus::new(NodeHealth::Online, timestamp),
                timestamp,
                &correlation_id,
                &mut events,
            )
            .expect("shared node registration should succeed");

        let candidates_a = control
            .match_capabilities(&state, &task_a, timestamp, &correlation_id, &mut events)
            .expect("Mission A should read Shared State");
        let candidates_b = control
            .match_capabilities(&state, &task_b, timestamp, &correlation_id, &mut events)
            .expect("Mission B should read the same Shared State");
        assert_eq!(state.nodes().len(), 1);
        assert_ne!(candidates_a.task_ref(), candidates_b.task_ref());
        assert_eq!(
            candidates_a.roles()[0].node_ids(),
            std::slice::from_ref(&node_id)
        );
        assert_eq!(
            candidates_a.roles()[0].node_ids(),
            candidates_b.roles()[0].node_ids()
        );

        state
            .record_node_health(NodeHealthObservation::new(
                node_id.clone(),
                NodeStatus::new(NodeHealth::Offline, TimestampMs::new(1)),
                TimestampMs::new(1),
            ))
            .expect("shared health update should be accepted");
        for task in [&task_a, &task_b] {
            assert!(matches!(
                control.match_capabilities(
                    &state,
                    task,
                    TimestampMs::new(1),
                    &correlation_id,
                    &mut events,
                ),
                Err(ControlError::NoCandidate(_))
            ));
        }
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
        let mut state = InMemorySharedNodeState::new();
        let mut events = TestEvents;
        for node in [node_a, node_b] {
            control
                .register_node(
                    &mut state,
                    node,
                    NodeStatus::new(NodeHealth::Online, timestamp),
                    timestamp,
                    &correlation_id,
                    &mut events,
                )
                .expect("test node registration should succeed");
        }

        let candidates_a = control
            .match_capabilities(&state, &task_a, timestamp, &correlation_id, &mut events)
            .expect("Mission A task should match");
        let candidates_b = control
            .match_capabilities(&state, &task_b, timestamp, &correlation_id, &mut events)
            .expect("Mission B task should match");
        let proposal_a = control
            .propose(
                &state,
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
                &state,
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
        let mut state = InMemorySharedNodeState::new();
        let mut events = TestEvents;
        control
            .register_node(
                &mut state,
                node,
                NodeStatus::new(NodeHealth::Online, timestamp),
                timestamp,
                &correlation_id,
                &mut events,
            )
            .expect("test node registration should succeed");

        assert!(matches!(
            control.match_capabilities(
                &state,
                &task,
                timestamp,
                &correlation_id,
                &mut events,
            ),
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
        let mut state = InMemorySharedNodeState::new();
        let mut events = TestEvents;
        control
            .register_node(
                &mut state,
                node,
                NodeStatus::new(NodeHealth::Online, timestamp),
                timestamp,
                &correlation_id,
                &mut events,
            )
            .expect("test node registration should succeed");

        let first_task = requirement("task-first", "transport-first", CapabilityKind::Transport);
        let first_candidates = control
            .match_capabilities(&state, &first_task, timestamp, &correlation_id, &mut events)
            .expect("first task should match");
        let first_proposal = control
            .propose(
                &state,
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
            .match_capabilities(
                &state,
                &second_task,
                timestamp,
                &correlation_id,
                &mut events,
            )
            .expect("second task can match before commit");
        let second_proposal = control
            .propose(
                &state,
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
        let mut state = InMemorySharedNodeState::new();
        let mut events = TestEvents;
        control
            .register_node(
                &mut state,
                node,
                NodeStatus::new(NodeHealth::Online, timestamp),
                timestamp,
                &correlation_id,
                &mut events,
            )
            .expect("test node registration should succeed");
        let first_candidates = control
            .match_capabilities(&state, &first_task, timestamp, &correlation_id, &mut events)
            .expect("first task should match");
        let first_proposal = control
            .propose(
                &state,
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
                &state,
                &second_task,
                TimestampMs::new(6),
                &correlation_id,
                &mut events,
            )
            .expect("second task should match");
        let second_proposal = control
            .propose(
                &state,
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
        assert!(matches!(
            control.release_group(
                &second_group,
                TimestampMs::new(10),
                &correlation_id,
                &mut events,
            ),
            Err(ControlError::InvalidLifecycle(GroupLifecycle::Blocked))
        ));
        control
            .fail_group(
                &second_group,
                "recovery exhausted",
                TimestampMs::new(11),
                &correlation_id,
                &mut events,
            )
            .expect("blocked group should explicitly fail");
        control
            .release_group(
                &second_group,
                TimestampMs::new(12),
                &correlation_id,
                &mut events,
            )
            .expect("failed group should release");
        assert_eq!(
            control
                .group(&second_group)
                .expect("released failed group should remain observable")
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
        let mut state = InMemorySharedNodeState::new();
        let mut events = TestEvents;
        control
            .register_node(
                &mut state,
                node,
                NodeStatus::new(NodeHealth::Online, observed_at),
                observed_at,
                &correlation_id,
                &mut events,
            )
            .expect("test node registration should succeed");

        assert!(matches!(
            control.match_capabilities(&state, &task, now, &correlation_id, &mut events),
            Err(ControlError::NoCandidate(_))
        ));
        let stored = state
            .node(&NodeId::new("node-a").expect("test node id should be valid"))
            .expect("stale facts should remain represented by State");
        assert_eq!(stored.reported_status().health(), NodeHealth::Online);
        assert_eq!(stored.reported_status().observed_at(), observed_at);
        assert_eq!(stored.reported_status_received_at(), observed_at);
    }

    /// Health freshness uses RoboGuide receive time, never source-local time.
    #[test]
    fn matching_freshness_uses_roboguide_receive_time() {
        let node = registration(
            "node-clock-domain",
            CapabilityKind::Transport,
            "space-clock",
        );
        let task = requirement("task-clock-domain", "transport", CapabilityKind::Transport);
        let correlation_id = correlation();
        let received_at = TimestampMs::new(100);
        let mut control = ControlPlane::with_status_ttl(100);
        let mut state = InMemorySharedNodeState::new();
        let mut events = TestEvents;
        control
            .register_node(
                &mut state,
                node,
                NodeStatus::new(NodeHealth::Online, TimestampMs::new(1_000_000)),
                received_at,
                &correlation_id,
                &mut events,
            )
            .expect("registration should preserve independent source time");

        control
            .match_capabilities(
                &state,
                &task,
                TimestampMs::new(150),
                &correlation_id,
                &mut events,
            )
            .expect("receive time age 50 should be fresh despite source clock value");
        let stored = state
            .node(&NodeId::new("node-clock-domain").expect("test node id should be valid"))
            .expect("registered node should exist");
        assert_eq!(
            stored.reported_status().observed_at(),
            TimestampMs::new(1_000_000)
        );
        assert_eq!(stored.reported_status_received_at(), received_at);
    }

    /// Matching observes the latest health fact instead of a Control-owned cache.
    #[test]
    fn health_update_is_visible_to_next_matching_decision() {
        let node = registration("node-health", CapabilityKind::Transport, "space-health");
        let node_id = node.node_id().clone();
        let task = requirement("task-health", "transport", CapabilityKind::Transport);
        let timestamp = TimestampMs::new(0);
        let correlation_id = correlation();
        let mut control = ControlPlane::new();
        let mut state = InMemorySharedNodeState::new();
        let mut events = TestEvents;
        control
            .register_node(
                &mut state,
                node,
                NodeStatus::new(NodeHealth::Online, timestamp),
                timestamp,
                &correlation_id,
                &mut events,
            )
            .expect("test node registration should succeed");
        control
            .match_capabilities(&state, &task, timestamp, &correlation_id, &mut events)
            .expect("online node should initially match");

        state
            .record_node_health(NodeHealthObservation::new(
                node_id.clone(),
                NodeStatus::new(NodeHealth::Offline, TimestampMs::new(1)),
                TimestampMs::new(1),
            ))
            .expect("newer health observation should enter Shared State");
        assert!(matches!(
            control.match_capabilities(
                &state,
                &task,
                TimestampMs::new(1),
                &correlation_id,
                &mut events,
            ),
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
        let mut state = InMemorySharedNodeState::new();
        let mut events = TestEvents;
        control
            .register_node_with_lease(
                &mut state,
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
                &mut state,
                NodeHeartbeat::new(
                    node_id.clone(),
                    lease_id,
                    NodeStatus::new(NodeHealth::Degraded, TimestampMs::new(8_000)),
                ),
                TimestampMs::new(30),
                100,
                &correlation_id,
                &mut events,
            )
            .expect("heartbeat should renew active lease");

        let stored = state
            .node(&node_id)
            .expect("heartbeat node should remain in Shared State");
        assert_eq!(
            stored.reported_status().observed_at(),
            TimestampMs::new(8_000)
        );
        assert_eq!(stored.reported_status_received_at(), TimestampMs::new(30));
        assert_eq!(stored.liveness().observed_at(), TimestampMs::new(30));

        let task = requirement("task-heartbeat", "transport", CapabilityKind::Transport);
        assert!(
            control
                .match_capabilities(
                    &state,
                    &task,
                    TimestampMs::new(129),
                    &correlation_id,
                    &mut events,
                )
                .is_ok()
        );
        assert!(matches!(
            control.match_capabilities(
                &state,
                &task,
                TimestampMs::new(130),
                &correlation_id,
                &mut events,
            ),
            Err(ControlError::NoCandidate(_))
        ));
    }

    /// Lease expiry changes liveness without rewriting local reported health.
    #[test]
    fn expired_lease_marks_liveness_unreachable() {
        let node = registration("node-expiring", CapabilityKind::Transport, "space-expiring");
        let node_id = node.node_id().clone();
        let task = requirement("task-expiring", "transport", CapabilityKind::Transport);
        let lease_id = LeaseId::new("lease-expiring").expect("test lease id must be valid");
        let lease = NodeLease::new(lease_id, node_id.clone(), TimestampMs::new(0), 100)
            .expect("test lease should be valid");
        let correlation_id =
            CorrelationId::new("lease-expiry-trace").expect("test correlation id must be valid");
        let mut control = ControlPlane::with_status_ttl(100);
        let mut state = InMemorySharedNodeState::new();
        let mut events = TestEvents;
        control
            .register_node_with_lease(
                &mut state,
                node,
                NodeStatus::new(NodeHealth::Online, TimestampMs::new(10)),
                lease,
                TimestampMs::new(0),
                &correlation_id,
                &mut events,
            )
            .expect("test node registration should succeed");

        let expired = control.expire_leases(
            &mut state,
            TimestampMs::new(100),
            &correlation_id,
            &mut events,
        );
        assert_eq!(
            expired.expect("lease expiry should update Shared State"),
            vec![node_id]
        );
        let stored = state
            .node(&NodeId::new("node-expiring").expect("test node id should be valid"))
            .expect("expired node should remain represented in State");
        assert_eq!(stored.reported_status().health(), NodeHealth::Online);
        assert_eq!(stored.reported_status().observed_at(), TimestampMs::new(10));
        assert_eq!(stored.reported_status_received_at(), TimestampMs::new(0));
        assert_eq!(
            stored.liveness(),
            NodeLivenessObservation::new(NodeLiveness::Unreachable, TimestampMs::new(100))
        );
        assert!(matches!(
            control.match_capabilities(
                &state,
                &task,
                TimestampMs::new(100),
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
        let mut state = InMemorySharedNodeState::new();
        let mut events = TestEvents;
        control
            .register_node(
                &mut state,
                node,
                NodeStatus::new(NodeHealth::Online, TimestampMs::new(0)),
                TimestampMs::new(0),
                &correlation_id,
                &mut events,
            )
            .expect("test node registration should succeed");

        let error = control
            .accept_heartbeat(
                &mut state,
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
        let mut state = InMemorySharedNodeState::new();
        let mut events = TestEvents;
        control
            .register_node(
                &mut state,
                node,
                NodeStatus::new(NodeHealth::Online, timestamp),
                timestamp,
                &correlation_id,
                &mut events,
            )
            .expect("test node registration should succeed");
        let candidates = control
            .match_capabilities(&state, &task, timestamp, &correlation_id, &mut events)
            .expect("task should initially match");
        let proposal = control
            .propose(
                &state,
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
            .activate_group(&group_id, timestamp, &correlation_id, &mut events)
            .expect("group should activate before becoming blocked");

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
