//! Strongly typed identities used across RoboGuide authority boundaries.

use crate::{
    DomainError, NODE_CONTRACT_VERSION_V0_1, NODE_CONTRACT_VERSION_V0_2,
    NODE_CONTRACT_VERSION_V0_3, NODE_CONTRACT_VERSION_V0_4, NODE_CONTRACT_VERSION_V0_5,
    NODE_CONTRACT_VERSION_V0_6,
};
use std::fmt::{Display, Formatter};

/// Defines a validated, strongly typed identifier with a stable text form.
macro_rules! define_identifier {
    ($name:ident, $doc:literal, $kind:literal) => {
        #[doc = $doc]
        #[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, serde::Serialize, serde::Deserialize)]
        pub struct $name(pub(crate) String);

        impl $name {
            #[doc = concat!("Creates a validated ", $kind, " identifier.")]
            pub fn new(value: impl Into<String>) -> Result<Self, DomainError> {
                let value = value.into();
                if value.trim().is_empty() {
                    return Err(DomainError::EmptyValue { kind: $kind });
                }
                Ok(Self(value))
            }

            #[doc = concat!("Returns the ", $kind, " identifier as text.")]
            pub fn as_str(&self) -> &str {
                &self.0
            }
        }

        impl Display for $name {
            /// Writes the stable text form of this identifier.
            fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
                formatter.write_str(&self.0)
            }
        }
    };
}

define_identifier!(
    MissionId,
    "Identifies a mission supplied to DEAIOS.",
    "mission"
);
define_identifier!(TaskId, "Identifies a task within a mission.", "task");
define_identifier!(
    ActorId,
    "Identifies a logical execution actor within a mission.",
    "actor"
);
define_identifier!(NodeId, "Identifies a logical execution node.", "node");
define_identifier!(
    LocalSystemId,
    "Identifies one local embodied system within a node.",
    "local system"
);
define_identifier!(SensorId, "Identifies one sensor within a node.", "sensor");
define_identifier!(
    RoleId,
    "Identifies a responsibility inside an execution group.",
    "role"
);
define_identifier!(ResourceId, "Identifies a reservable resource.", "resource");
define_identifier!(
    ExecutionGroupId,
    "Identifies a dynamic execution group.",
    "execution group"
);
define_identifier!(
    CoordinationContextId,
    "Identifies one Mission Intelligence coordination context.",
    "coordination context"
);
define_identifier!(
    ContextRoleId,
    "Identifies one role that remains continuous across Tasks in a Context.",
    "context role"
);
define_identifier!(
    ExecutionRelationId,
    "Identifies one execution coordination relation within a mission.",
    "execution relation"
);
define_identifier!(EventId, "Identifies one immutable event record.", "event");
define_identifier!(
    CorrelationId,
    "Identifies one end-to-end operation trace.",
    "correlation"
);
define_identifier!(LeaseId, "Identifies a renewable node lease.", "lease");
define_identifier!(
    NodeContractVersion,
    "Identifies a versioned heterogeneous node integration contract.",
    "node contract version"
);

impl NodeContractVersion {
    /// Returns the first supported heterogeneous Node Contract version.
    pub fn v0_1() -> Self {
        Self(NODE_CONTRACT_VERSION_V0_1.to_string())
    }

    /// Returns the aggregate Local Integration Node Contract version.
    pub fn v0_2() -> Self {
        Self(NODE_CONTRACT_VERSION_V0_2.to_string())
    }

    /// Returns the contract version carrying State and Memory extension declarations.
    pub fn v0_3() -> Self {
        Self(NODE_CONTRACT_VERSION_V0_3.to_string())
    }

    /// Returns the contract version carrying durable command admission evidence.
    pub fn v0_4() -> Self {
        Self(NODE_CONTRACT_VERSION_V0_4.to_string())
    }

    /// Returns the contract version carrying capability profiles and semantic intents.
    pub fn v0_5() -> Self {
        Self(NODE_CONTRACT_VERSION_V0_5.to_string())
    }

    /// Returns the contract version carrying explicit canonical operation support.
    pub fn v0_6() -> Self {
        Self(NODE_CONTRACT_VERSION_V0_6.to_string())
    }
}

/// Uniquely identifies a mission-scoped task across concurrent missions.
#[derive(
    Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Hash, serde::Serialize, serde::Deserialize,
)]
pub struct TaskRef {
    /// Mission that owns the task namespace.
    mission_id: MissionId,
    /// Task identity scoped by the owning mission.
    task_id: TaskId,
}

impl TaskRef {
    /// Creates an unambiguous task reference from its mission and local task identity.
    pub const fn new(mission_id: MissionId, task_id: TaskId) -> Self {
        Self {
            mission_id,
            task_id,
        }
    }

    /// Returns the mission that owns this task.
    pub const fn mission_id(&self) -> &MissionId {
        &self.mission_id
    }

    /// Returns the task identity within its mission namespace.
    pub const fn task_id(&self) -> &TaskId {
        &self.task_id
    }
}

impl Display for TaskRef {
    /// Formats a task reference without collapsing its mission namespace.
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        write!(formatter, "{}/{}", self.mission_id, self.task_id)
    }
}
