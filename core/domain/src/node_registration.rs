//! Local-system and sensor facts aggregated by one RoboGuide node registration.

use crate::{LocalRuntime, LocalSystemId, SensorId};
use std::collections::BTreeMap;

/// One Local EAIOS/runtime aggregated behind a node identity.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct LocalSystemDescriptor {
    /// Stable node-local owner identity.
    id: LocalSystemId,
    /// Runtime product and version reported by the local system.
    runtime: LocalRuntime,
    /// Transport-neutral descriptive metadata.
    metadata: BTreeMap<String, String>,
}

impl LocalSystemDescriptor {
    /// Creates one local-system descriptor without granting global authority.
    pub const fn new(
        id: LocalSystemId,
        runtime: LocalRuntime,
        metadata: BTreeMap<String, String>,
    ) -> Self {
        Self {
            id,
            runtime,
            metadata,
        }
    }

    /// Returns the stable node-local identity.
    pub const fn id(&self) -> &LocalSystemId {
        &self.id
    }

    /// Returns the local runtime descriptor.
    pub const fn runtime(&self) -> &LocalRuntime {
        &self.runtime
    }

    /// Returns descriptive local metadata.
    pub const fn metadata(&self) -> &BTreeMap<String, String> {
        &self.metadata
    }
}

/// One sensor advertised by a configured local system.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct SensorDescriptor {
    /// Stable node-wide sensor identity.
    id: SensorId,
    /// Transport-neutral sensor category.
    kind: String,
    /// Local system that owns the sensor.
    local_system_id: LocalSystemId,
    /// Descriptive metadata that does not grant resource authority.
    metadata: BTreeMap<String, String>,
}

impl SensorDescriptor {
    /// Creates a sensor declaration with an explicit local owner.
    pub const fn new(
        id: SensorId,
        kind: String,
        local_system_id: LocalSystemId,
        metadata: BTreeMap<String, String>,
    ) -> Self {
        Self {
            id,
            kind,
            local_system_id,
            metadata,
        }
    }

    /// Returns the node-wide sensor identity.
    pub const fn id(&self) -> &SensorId {
        &self.id
    }

    /// Returns the sensor category.
    pub fn kind(&self) -> &str {
        &self.kind
    }

    /// Returns the local system that owns the sensor.
    pub const fn local_system_id(&self) -> &LocalSystemId {
        &self.local_system_id
    }

    /// Returns descriptive sensor metadata.
    pub const fn metadata(&self) -> &BTreeMap<String, String> {
        &self.metadata
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        ActorId, Capability, CapabilityContractRef, CapabilityKind, NodeContractVersion, NodeId,
        NodeRegistration, RoleId, RoleRequirement,
    };

    /// Aggregate registration preserves local-system ownership without changing matching facts.
    #[test]
    fn aggregate_registration_retains_capability_owner() {
        let system_id = LocalSystemId::new("motion").expect("local system id is valid");
        let contract = CapabilityContractRef::new("mobility", "reach_region", "v1")
            .expect("contract is valid");
        let registration = NodeRegistration::new_with_local_systems(
            NodeId::new("dog-a").expect("node id is valid"),
            vec![LocalSystemDescriptor::new(
                system_id.clone(),
                LocalRuntime::new("local-motion", "1").expect("runtime is valid"),
                BTreeMap::new(),
            )],
            NodeContractVersion::new("roboguide.node.v0.2").expect("contract version is valid"),
            vec![Capability::new(CapabilityKind::Mobility, true)],
            BTreeMap::from([(contract.clone(), system_id.clone())]),
            Vec::new(),
            Vec::new(),
            BTreeMap::new(),
        )
        .expect("aggregate registration is valid");
        assert_eq!(registration.capability_owner(&contract), Some(&system_id));
        assert_eq!(registration.local_systems().len(), 1);
    }

    /// Exact contract readiness fences one contract without hiding its configured owner.
    #[test]
    fn aggregate_registration_retains_exact_contract_readiness() {
        let system_id = LocalSystemId::new("mapping").expect("local system id is valid");
        let ready = CapabilityContractRef::new("spatial.map", "build", "v0")
            .expect("ready contract is valid");
        let unavailable = CapabilityContractRef::new("spatial.map", "localize", "v0")
            .expect("unavailable contract is valid");
        let owners = BTreeMap::from([
            (ready.clone(), system_id.clone()),
            (unavailable.clone(), system_id.clone()),
        ]);
        let registration = NodeRegistration::new_with_local_systems_and_readiness(
            NodeId::new("dog-a").expect("node id is valid"),
            vec![LocalSystemDescriptor::new(
                system_id,
                LocalRuntime::new("mapping", "1").expect("runtime is valid"),
                BTreeMap::new(),
            )],
            NodeContractVersion::v0_2(),
            vec![Capability::new(CapabilityKind::Compute, true)],
            owners,
            BTreeMap::from([
                (ready.clone(), CapabilityKind::Compute),
                (unavailable.clone(), CapabilityKind::Compute),
            ]),
            BTreeMap::from([(ready.clone(), true), (unavailable.clone(), false)]),
            Vec::new(),
            Vec::new(),
            BTreeMap::new(),
        )
        .expect("aggregate registration is valid");

        assert!(registration.contract_is_available(&ready));
        assert!(!registration.contract_is_available(&unavailable));
        assert!(registration.capability_owner(&unavailable).is_some());
        assert!(
            !registration.supports_role(&RoleRequirement::new_with_actor_and_contract(
                RoleId::new("localizer").expect("role id is valid"),
                ActorId::new("robot").expect("actor id is valid"),
                CapabilityKind::Compute,
                unavailable,
                None,
            ))
        );
    }
}
