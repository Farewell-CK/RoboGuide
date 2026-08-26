#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! Control Plane facade and composition root.

mod allocation;
mod coordination;
mod group;
mod matching;
mod node;
mod proposal;
mod reconciliation;
mod scheduler;

pub use allocation::AllocationProjectionError;
pub use coordination::CommittedPlan;
pub use group::{ContextBinding, ExecutionGroup, GroupLifecycle, RoleRequirementView};
pub use matching::{CandidateSet, RoleCandidates};
pub use proposal::AssignmentProposal;
pub use reconciliation::{
    CommittedRecoveryAssignment, ReconciliationAssessment, RecoveryAssignmentProposal,
    RecoveryCandidateSet, RecoveryOutcome, RoleRecoveryNeed,
};
pub use scheduler::{
    DeterministicBootstrapScheduler, RecoverySchedulingDecision, RecoverySchedulingOutcome,
    RoleSchedulingSelection, SchedulerError, TaskSchedulingDecision,
};

use coordination::Reservation;
use domain::{
    ActorBinding, ActorId, ExecutionGroupId, LeaseId, MissionId, NodeId, NodeLease, ResourceId,
    RoleId, TaskRef, TimestampMs,
};
use ports::SharedStateError;
use std::collections::BTreeMap;
use std::fmt::{Display, Formatter};

/// Default maximum age for a node status used by Control eligibility policy.
pub const DEFAULT_NODE_STATUS_TTL_MS: u64 = 5_000;

/// Default lease duration assigned by convenience registration.
pub const DEFAULT_NODE_LEASE_TTL_MS: u64 = 15_000;

/// Versioned durable representation of Control-owned commitments and Group state.
///
/// Process-local leases are deliberately absent because their monotonic timestamps cannot remain
/// authoritative after a process restart.
#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
pub struct ControlCheckpoint {
    /// Unique resource commitments keyed by resource identity.
    reservations: BTreeMap<ResourceId, Reservation>,
    /// Mission-scoped actor bindings represented as values to avoid composite JSON map keys.
    actor_bindings: Vec<ActorBinding>,
    /// Execution Groups represented as values to avoid relying on map-key codecs.
    groups: Vec<ExecutionGroup>,
    /// Committed replacement assignments awaiting Rebind or Abort.
    pending_recovery_commitments: Vec<CommittedRecoveryAssignment>,
    /// Maximum receive-time age accepted by Control eligibility policy.
    max_status_age_ms: u64,
}

/// Evaluates freshness using RoboGuide-local receive and decision times.
pub(crate) fn is_fresh_at(received_at: TimestampMs, now: TimestampMs, max_age_ms: u64) -> bool {
    now.as_millis().saturating_sub(received_at.as_millis()) <= max_age_ms
}

/// Errors raised by Control matching, scheduling, coordination, and Group logic.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ControlError {
    /// A referenced node was not registered.
    UnknownNode(NodeId),
    /// A referenced Group was not found.
    UnknownGroup(ExecutionGroupId),
    /// A role had no eligible candidate.
    NoCandidate(RoleId),
    /// A previously bound mission actor's node is no longer usable for a later task.
    ActorBindingRequiresReconciliation {
        /// Mission containing the actor binding.
        mission_id: MissionId,
        /// Actor whose continuity cannot currently be satisfied.
        actor_id: ActorId,
        /// Previously bound node requiring reconciliation.
        node_id: NodeId,
    },
    /// A proposal or internal invariant was invalid.
    InvalidProposal(String),
    /// Control allocation authority could not be projected due to an invariant violation.
    AllocationInvariant(String),
    /// A resource was already committed by another task or role.
    ResourceConflict {
        /// Conflicting resource.
        resource_id: ResourceId,
        /// Task currently holding the resource.
        owner_task_ref: TaskRef,
        /// Role currently holding the resource.
        owner_role_id: RoleId,
    },
    /// A Group role already owns a pending recovery commitment.
    PendingRecoveryCommitmentExists {
        /// Group with existing pending commitment.
        group_id: ExecutionGroupId,
        /// Role with existing pending commitment.
        role_id: RoleId,
    },
    /// No pending commitment exists for the supplied Group role.
    PendingRecoveryCommitmentNotFound {
        /// Expected Group.
        group_id: ExecutionGroupId,
        /// Expected Role.
        role_id: RoleId,
    },
    /// A commitment handle differs from current authority.
    PendingRecoveryCommitmentMismatch {
        /// Group whose handle is stale or forged.
        group_id: ExecutionGroupId,
        /// Role whose handle is stale or forged.
        role_id: RoleId,
    },
    /// A Group lifecycle transition was invalid.
    InvalidLifecycle(GroupLifecycle),
    /// A lease or heartbeat violated the Node Contract.
    InvalidLease(String),
    /// Shared Node State rejected an observation.
    SharedState(SharedStateError),
    /// A heartbeat used a lease not owned by its node.
    UnknownLease {
        /// Node that sent the unknown lease.
        node_id: NodeId,
        /// Rejected lease identity.
        lease_id: LeaseId,
    },
    /// A node attempted to use an expired lease.
    LeaseExpired {
        /// Node whose lease expired.
        node_id: NodeId,
        /// Expired lease identity.
        lease_id: LeaseId,
    },
}

impl Display for ControlError {
    /// Formats a stable Control rejection.
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::UnknownNode(id) => write!(formatter, "unknown node {id}"),
            Self::UnknownGroup(id) => write!(formatter, "unknown execution group {id}"),
            Self::NoCandidate(id) => write!(formatter, "no candidate for role {id}"),
            Self::ActorBindingRequiresReconciliation {
                mission_id,
                actor_id,
                node_id,
            } => write!(
                formatter,
                "mission actor {mission_id}/{actor_id} remains bound to unavailable node {node_id}; reconciliation required"
            ),
            Self::InvalidProposal(reason) => write!(formatter, "invalid proposal: {reason}"),
            Self::AllocationInvariant(reason) => {
                write!(formatter, "allocation invariant violation: {reason}")
            }
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
            Self::InvalidLifecycle(state) => write!(formatter, "invalid lifecycle: {state:?}"),
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

/// Authoritative in-process Control Plane state for the bootstrap.
#[derive(Debug)]
pub struct ControlPlane {
    /// Renewable lease authority pending later ownership review.
    pub(crate) leases: BTreeMap<NodeId, NodeLease>,
    /// Unique resource commitment authority.
    pub(crate) reservations: BTreeMap<ResourceId, Reservation>,
    /// Mission-scoped actor binding authority, populated only after successful binding.
    pub(crate) actor_bindings: BTreeMap<(MissionId, ActorId), ActorBinding>,
    /// Dynamic Execution Groups owned by Group Manager.
    pub(crate) groups: BTreeMap<ExecutionGroupId, ExecutionGroup>,
    /// Committed replacement assignments awaiting Consume or Abort.
    pub(crate) pending_recovery_commitments:
        BTreeMap<(ExecutionGroupId, TaskRef, RoleId), CommittedRecoveryAssignment>,
    /// Maximum receive-time age accepted by Control eligibility policy.
    pub(crate) max_status_age_ms: u64,
}

impl Default for ControlPlane {
    /// Creates an empty Control Plane with default freshness policy.
    fn default() -> Self {
        Self::with_status_ttl(DEFAULT_NODE_STATUS_TTL_MS)
    }
}

impl ControlPlane {
    /// Creates an empty Control Plane.
    pub fn new() -> Self {
        Self::default()
    }

    /// Creates an empty Control Plane with explicit receive-time freshness TTL.
    pub const fn with_status_ttl(max_status_age_ms: u64) -> Self {
        Self {
            leases: BTreeMap::new(),
            reservations: BTreeMap::new(),
            actor_bindings: BTreeMap::new(),
            groups: BTreeMap::new(),
            pending_recovery_commitments: BTreeMap::new(),
            max_status_age_ms,
        }
    }

    /// Captures durable Control authority without process-local lease timestamps.
    pub fn checkpoint(&self) -> ControlCheckpoint {
        ControlCheckpoint {
            reservations: self.reservations.clone(),
            actor_bindings: self.actor_bindings.values().cloned().collect(),
            groups: self.groups.values().cloned().collect(),
            pending_recovery_commitments: self
                .pending_recovery_commitments
                .values()
                .cloned()
                .collect(),
            max_status_age_ms: self.max_status_age_ms,
        }
    }

    /// Restores durable Control authority and rejects duplicate or inconsistent checkpoint data.
    ///
    /// Node leases start empty so every node must establish fresh authority in the new process.
    pub fn restore(checkpoint: ControlCheckpoint) -> Result<Self, ControlError> {
        let mut actor_bindings = BTreeMap::new();
        for binding in checkpoint.actor_bindings {
            let key = (binding.mission_id().clone(), binding.actor_id().clone());
            if actor_bindings.insert(key, binding).is_some() {
                return Err(ControlError::InvalidProposal(
                    "checkpoint contains duplicate actor binding".to_string(),
                ));
            }
        }
        let mut groups = BTreeMap::new();
        for group in checkpoint.groups {
            if groups.insert(group.group_id().clone(), group).is_some() {
                return Err(ControlError::InvalidProposal(
                    "checkpoint contains duplicate execution group".to_string(),
                ));
            }
        }
        let mut pending_recovery_commitments = BTreeMap::new();
        for commitment in checkpoint.pending_recovery_commitments {
            let key = (
                commitment.group_id().clone(),
                commitment.task_ref().clone(),
                commitment.role_id().clone(),
            );
            if pending_recovery_commitments
                .insert(key, commitment)
                .is_some()
            {
                return Err(ControlError::InvalidProposal(
                    "checkpoint contains duplicate pending recovery commitment".to_string(),
                ));
            }
        }
        let restored = Self {
            leases: BTreeMap::new(),
            reservations: checkpoint.reservations,
            actor_bindings,
            groups,
            pending_recovery_commitments,
            max_status_age_ms: checkpoint.max_status_age_ms,
        };
        restored.allocation_snapshot(TimestampMs::new(0))?;
        Ok(restored)
    }

    /// Records an actor binding after a successful committed task and Group binding.
    pub(crate) fn record_actor_binding(
        &mut self,
        mission_id: MissionId,
        actor_id: ActorId,
        node_id: NodeId,
    ) -> Result<(), ControlError> {
        let key = (mission_id.clone(), actor_id.clone());
        if let Some(existing) = self.actor_bindings.get(&key) {
            if existing.node_id() != &node_id {
                return Err(ControlError::InvalidProposal(
                    "mission actor is already bound to another node".to_string(),
                ));
            }
            return Ok(());
        }
        self.actor_bindings
            .insert(key, ActorBinding::new(mission_id, actor_id, node_id));
        Ok(())
    }

    /// Returns the authoritative binding for one mission actor, if any.
    pub fn actor_binding(
        &self,
        mission_id: &MissionId,
        actor_id: &ActorId,
    ) -> Option<&ActorBinding> {
        self.actor_bindings
            .get(&(mission_id.clone(), actor_id.clone()))
    }

    /// Returns the current lease authority for one node.
    pub fn node_lease(&self, node_id: &NodeId) -> Option<&NodeLease> {
        self.leases.get(node_id)
    }

    /// Returns all current Execution Group identities in deterministic order.
    pub fn group_ids(&self) -> Vec<ExecutionGroupId> {
        self.groups.keys().cloned().collect()
    }
}

#[cfg(test)]
mod tests;

#[cfg(test)]
mod checkpoint_tests {
    use super::*;

    /// The versioned Control checkpoint remains JSON-compatible with typed resource map keys.
    #[test]
    fn checkpoint_round_trips_typed_resource_keys() {
        let mut control = ControlPlane::new();
        control.reservations.insert(
            ResourceId::new("space-a").expect("resource id valid"),
            Reservation {
                task_ref: TaskRef::new(
                    MissionId::new("mission-a").expect("mission id valid"),
                    domain::TaskId::new("task-a").expect("task id valid"),
                ),
                role_id: RoleId::new("role-a").expect("role id valid"),
                group_id: None,
                scope: domain::ResourceBindingScope::Task,
                owner: domain::AllocationOwner::Task(TaskRef::new(
                    MissionId::new("mission-a").expect("mission id valid"),
                    domain::TaskId::new("task-a").expect("task id valid"),
                )),
            },
        );
        let json = serde_json::to_string(&control.checkpoint()).expect("checkpoint serializes");
        let decoded: ControlCheckpoint =
            serde_json::from_str(&json).expect("checkpoint deserializes");
        assert!(ControlPlane::restore(decoded).is_ok());
    }
}
