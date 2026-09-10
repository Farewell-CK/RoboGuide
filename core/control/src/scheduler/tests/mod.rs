use super::*;
use crate::{ControlError, ControlPlane, RoleCandidates};
use domain::{
    Capability, CapabilityKind, EventId, LocalRuntime, MissionId, NodeHealth, NodeLiveness,
    NodeLivenessObservation, NodeRegistration, NodeStateSnapshot, NodeStatus, Resource,
    ResourceKind, RoleRequirement, TaskId,
};
use ports::{SharedNodeStateWriter, SharedStateError};
use state::InMemorySharedNodeState;

mod joint_search;
mod recovery_policy;
mod selection_policy;

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
        LocalRuntime::new("scheduler-test-runtime", "0.1.0").expect("test runtime must be valid"),
        domain::NodeContractVersion::v0_1(),
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

/// Builds one node registration with explicit resource capacities.
fn registration_with_capacity(
    node_id: &str,
    capability: CapabilityKind,
    resources: Vec<(&str, ResourceKind, u32)>,
) -> NodeRegistration {
    NodeRegistration::new(
        NodeId::new(node_id).expect("test node id must be valid"),
        LocalRuntime::new("scheduler-test-runtime", "0.1.0").expect("test runtime must be valid"),
        domain::NodeContractVersion::v0_1(),
        vec![Capability::new(capability, true)],
        resources
            .into_iter()
            .map(|(resource_id, kind, capacity)| {
                Resource::new(
                    ResourceId::new(resource_id).expect("test resource id must be valid"),
                    kind,
                    capacity,
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

/// Builds one v0.2 Role with explicit quantitative resource requirements.
fn scheduled_role(
    role_id: &str,
    capability: CapabilityKind,
    resources: Vec<(ResourceKind, u32)>,
) -> RoleRequirement {
    RoleRequirement::new_scheduled(
        RoleId::new(role_id).expect("test role id must be valid"),
        None,
        capability,
        None,
        resources
            .into_iter()
            .map(|(kind, units)| {
                domain::ResourceRequirement::new(kind, units)
                    .expect("test resource requirement must be valid")
            })
            .collect(),
    )
    .expect("scheduled role must be valid")
}
