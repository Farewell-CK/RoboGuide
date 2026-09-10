//! Shared Mission orchestration test fixtures.

use super::*;
use crate::mission::serialization::mission_plan_json;
use domain::{
    Capability, CapabilityContractRef, CapabilityKind, CoordinationContextId, LocalRuntime,
    LocalSystemDescriptor, LocalSystemId, NodeContractVersion, NodeHealth, NodeId,
    NodeRegistration, NodeStatus, Resource, ResourceId, ResourceKind,
};
use state::InMemorySharedNodeState;
use testkit::InMemoryEventLog;

mod contracts;
mod lifecycle;
mod scheduling;
