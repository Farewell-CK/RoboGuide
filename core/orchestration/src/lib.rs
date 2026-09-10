#![deny(clippy::missing_docs_in_private_items)]
#![deny(missing_docs)]
#![forbid(unsafe_code)]

//! Mission execution authority over the complete MissionPlan and its long-lived Group.

use control::{
    BoundedJointScheduler, ControlError, ControlPlane, GroupLifecycle, SchedulingReservationPhase,
    TaskSchedulingOutcome,
};
use domain::{
    CorrelationId, ExecutionGroupId, MissionId, MissionPlan, ResourceBindingScope, ResourceId,
    TaskExecutionLifecycle, TaskId, TaskRef, TimestampMs,
};
use ports::{EventSink, SharedNodeStateReader};
use std::collections::{BTreeMap, BTreeSet};
use std::fmt::{Display, Formatter};

mod integration_bridge;
mod mechanism_profile;
mod mission {
    //! Mission acceptance, scheduling, and lifecycle responsibilities.

    pub(super) mod checkpoint;
    pub(super) mod lifecycle;
    pub(super) mod scheduling;
    pub(super) mod serialization;
    pub(super) mod timing;
}
mod mission_contract;

pub use integration_bridge::{
    CONTROLLER_CHECKPOINT_SCHEMA, GroupSharedViewEntry, GroupSharedViewSnapshot,
    GroupSpatialVerification, GroupViewFreshness, IntegrationRuntimeBridge,
    IntegrationRuntimeError, ObservedTaskOutcome, ObservedTaskResult, RemoteExecutionStatus,
};
pub use mechanism_profile::SupportedMechanismProfile;
pub use mission_contract::decode_mission_plan;

/// Mission execution lifecycle owned by orchestration rather than Runtime.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub enum MissionExecutionLifecycle {
    /// The complete plan is accepted and its Group has been created.
    Accepted,
    /// At least one Task is Ready, Active, Blocked, or completed while later Tasks remain.
    Running,
    /// An explicit cancellation was durably requested; active attempts are draining.
    Cancelling,
    /// Every Task in the accepted plan completed and the Group was released.
    Completed,
    /// Mission policy declared a final failure and released the Group.
    Failed,
    /// An explicit cancellation terminated the Mission and released the Group.
    Cancelled,
}

/// One accepted MissionPlan and its single default Phase 1 Execution Group.
#[derive(Debug, Clone, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct MissionExecution {
    /// Complete immutable plan used for DAG and completion decisions.
    plan: MissionPlan,
    /// Mission-level runtime coordination context.
    group_id: ExecutionGroupId,
    /// Current orchestration-owned lifecycle.
    lifecycle: MissionExecutionLifecycle,
    /// RoboGuide-local acceptance time anchoring relative scheduling constraints.
    accepted_at: TimestampMs,
    /// Last durable scheduling deferral reason per Task, used to suppress duplicate evidence.
    #[serde(default)]
    scheduling_deferrals: BTreeMap<TaskId, String>,
}

impl MissionExecution {
    /// Returns the complete accepted MissionPlan.
    pub const fn plan(&self) -> &MissionPlan {
        &self.plan
    }

    /// Returns the Mission-level Execution Group identity.
    pub const fn group_id(&self) -> &ExecutionGroupId {
        &self.group_id
    }

    /// Returns the orchestration-owned Mission lifecycle.
    pub const fn lifecycle(&self) -> MissionExecutionLifecycle {
        self.lifecycle
    }

    /// Returns the durable Mission acceptance time used by task timing offsets.
    pub const fn accepted_at(&self) -> TimestampMs {
        self.accepted_at
    }
}

/// Errors raised when Mission orchestration invariants are violated.
#[derive(Debug)]
pub enum OrchestrationError {
    /// Control rejected a lifecycle or ownership transition.
    Control(ControlError),
    /// A Mission identity was absent or reused.
    Mission(String),
}

impl Display for OrchestrationError {
    /// Formats a stable orchestration diagnostic.
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Control(error) => write!(formatter, "control rejected orchestration: {error}"),
            Self::Mission(reason) => formatter.write_str(reason),
        }
    }
}

impl std::error::Error for OrchestrationError {}

impl From<ControlError> for OrchestrationError {
    /// Preserves Control diagnostics across the orchestration boundary.
    fn from(value: ControlError) -> Self {
        Self::Control(value)
    }
}

/// Deterministic Phase 1 Mission execution authority.
#[derive(Debug, Default, Clone)]
pub struct MissionOrchestrator {
    /// Accepted Missions keyed independently from Task-local identity.
    executions: BTreeMap<MissionId, MissionExecution>,
}

#[cfg(test)]
#[path = "mission/tests/mod.rs"]
mod tests;
