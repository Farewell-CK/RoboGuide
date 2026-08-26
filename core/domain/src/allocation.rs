//! Transport-neutral observable allocation projection types.

use crate::{
    ContextRoleId, CoordinationContextId, ExecutionGroupId, MissionId, ResourceId, RoleId, TaskRef,
    TimestampMs,
};

/// Authoritative lifetime owner represented by a Control reservation projection.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum AllocationOwner {
    /// Ownership ends with one TaskExecution.
    Task(TaskRef),
    /// Ownership remains continuous until one Mission Intelligence Context ends.
    Context {
        /// Mission owning the Context.
        mission_id: MissionId,
        /// Semantic Context owning the resource.
        context_id: CoordinationContextId,
        /// Persistent ContextRole owning the resource.
        context_role_id: ContextRoleId,
    },
}

/// Lifetime of a resource binding inside a Mission-level Execution Group.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum ResourceBindingScope {
    /// Resource is temporary and is released when its TaskExecution ends.
    Task,
    /// Resource remains committed until its Mission Intelligence Context ends.
    Context,
}

impl Default for ResourceBindingScope {
    /// Defaults legacy reservations to the Task lifetime.
    fn default() -> Self {
        Self::Task
    }
}

/// Observable stage of one resource commitment in the Control allocation pipeline.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AllocationPhase {
    /// Normal resources are committed but no Execution Group is bound yet.
    Committed,
    /// Resources belong to a current Execution Group assignment.
    Bound,
    /// Recovery resources are committed but the unbound role has not consumed them.
    RecoveryPending,
}

/// One non-authoritative view record projected from Control reservation authority.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ResourceAllocation {
    /// Resource represented by this view record.
    resource_id: ResourceId,
    /// Mission-scoped task that owns the Control commitment.
    task_ref: TaskRef,
    /// Role that owns the Control commitment.
    role_id: RoleId,
    /// Existing Group owning the commitment after normal Bind or recovery Commit.
    group_id: Option<ExecutionGroupId>,
    /// Observable commitment stage at projection time.
    phase: AllocationPhase,
    /// Task- or Context-scoped commitment owner.
    owner: AllocationOwner,
}

impl ResourceAllocation {
    /// Creates one normalized allocation projection record.
    pub const fn new(
        resource_id: ResourceId,
        task_ref: TaskRef,
        role_id: RoleId,
        group_id: Option<ExecutionGroupId>,
        phase: AllocationPhase,
        owner: AllocationOwner,
    ) -> Self {
        Self {
            resource_id,
            task_ref,
            role_id,
            group_id,
            phase,
            owner,
        }
    }

    /// Returns the projected resource identity.
    pub const fn resource_id(&self) -> &ResourceId {
        &self.resource_id
    }

    /// Returns the mission-scoped task owning the commitment.
    pub const fn task_ref(&self) -> &TaskRef {
        &self.task_ref
    }

    /// Returns the role owning the commitment.
    pub const fn role_id(&self) -> &RoleId {
        &self.role_id
    }

    /// Returns the existing Group when the commitment has Group ownership.
    pub const fn group_id(&self) -> Option<&ExecutionGroupId> {
        self.group_id.as_ref()
    }

    /// Returns the projected commitment phase.
    pub const fn phase(&self) -> AllocationPhase {
        self.phase
    }

    /// Returns the Task- or Context-scoped reservation owner.
    pub const fn owner(&self) -> &AllocationOwner {
        &self.owner
    }
}

/// Complete allocation projection generated at one RoboGuide-local instant.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AllocationViewSnapshot {
    /// RoboGuide-local time at which Control generated this projection.
    projected_at: TimestampMs,
    /// Resource records in deterministic ResourceId order.
    allocations: Vec<ResourceAllocation>,
}

impl AllocationViewSnapshot {
    /// Creates a complete normalized allocation projection.
    pub const fn new(projected_at: TimestampMs, allocations: Vec<ResourceAllocation>) -> Self {
        Self {
            projected_at,
            allocations,
        }
    }

    /// Returns the RoboGuide-local projection time.
    pub const fn projected_at(&self) -> TimestampMs {
        self.projected_at
    }

    /// Returns allocation records in deterministic ResourceId order.
    pub fn allocations(&self) -> &[ResourceAllocation] {
        &self.allocations
    }
}
