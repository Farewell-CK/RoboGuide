//! Aggregate node registration and latest shared node-state snapshot.

use crate::*;
use std::collections::{BTreeMap, BTreeSet};

/// The local runtime and resources a node exposes to DEAIOS.
///
/// Owner maps are encoded as arrays of typed entries instead of JSON objects.  A
/// `CapabilityContractRef` is a structured value rather than a scalar string, so
/// serde_json cannot use it directly as an object key during controller checkpoint
/// serialization.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct NodeRegistration {
    /// Logical node identity exposed to DEAIOS.
    node_id: NodeId,
    /// Local EAIOS/runtime descriptors aggregated behind this Node identity.
    local_systems: Vec<LocalSystemDescriptor>,
    /// Semantic integration contract implemented by the adapter or bridge.
    contract_version: NodeContractVersion,
    /// Capabilities currently advertised by the node.
    capabilities: Vec<Capability>,
    /// Canonical capability contracts executable through this node's adapter boundary.
    supported_contracts: Vec<CapabilityContractRef>,
    /// Unique local-system owner of each canonical contract.
    #[serde(with = "capability_owner_map_serde")]
    capability_owners: BTreeMap<CapabilityContractRef, LocalSystemId>,
    /// Exact coarse capability category associated with each canonical contract.
    #[serde(default, with = "capability_kind_map_serde")]
    capability_kinds: BTreeMap<CapabilityContractRef, CapabilityKind>,
    /// Latest observed readiness of each canonical contract.
    #[serde(default, with = "capability_readiness_map_serde")]
    capability_readiness: BTreeMap<CapabilityContractRef, bool>,
    /// Sensors exposed by configured local systems.
    sensors: Vec<SensorDescriptor>,
    /// Resources currently advertised by the node.
    resources: Vec<Resource>,
    /// Unique local-system owner of each node-wide resource.
    #[serde(with = "resource_owner_map_serde")]
    resource_owners: BTreeMap<ResourceId, LocalSystemId>,
    /// Selective source-aware State channels exposed by configured local systems.
    #[serde(default)]
    state_exports: Vec<StateExportDescriptor>,
    /// Selective Memory discovery and exchange providers exposed by local systems.
    #[serde(default)]
    memory_providers: Vec<MemoryProviderDescriptor>,
}

/// Encodes structured capability-contract owner keys as checkpoint-safe records.
mod capability_owner_map_serde {
    use super::{CapabilityContractRef, LocalSystemId};
    use serde::{Deserialize, Deserializer, Serialize, Serializer};
    use std::collections::BTreeMap;

    /// Serializes each capability owner mapping as a typed two-element record.
    pub fn serialize<S: Serializer>(
        values: &BTreeMap<CapabilityContractRef, LocalSystemId>,
        serializer: S,
    ) -> Result<S::Ok, S::Error> {
        values.iter().collect::<Vec<_>>().serialize(serializer)
    }

    /// Restores capability owner mappings and rejects duplicate contract identities.
    pub fn deserialize<'de, D: Deserializer<'de>>(
        deserializer: D,
    ) -> Result<BTreeMap<CapabilityContractRef, LocalSystemId>, D::Error> {
        let entries: Vec<(CapabilityContractRef, LocalSystemId)> = Vec::deserialize(deserializer)?;
        let mut values = BTreeMap::new();
        for (contract, owner) in entries {
            if values.insert(contract, owner).is_some() {
                return Err(serde::de::Error::custom(
                    "duplicate capability owner contract",
                ));
            }
        }
        Ok(values)
    }
}

/// Encodes structured capability-contract readiness keys as checkpoint-safe records.
mod capability_readiness_map_serde {
    use super::CapabilityContractRef;
    use serde::{Deserialize, Deserializer, Serialize, Serializer};
    use std::collections::BTreeMap;

    /// Serializes each contract readiness fact as a typed two-element record.
    pub fn serialize<S: Serializer>(
        values: &BTreeMap<CapabilityContractRef, bool>,
        serializer: S,
    ) -> Result<S::Ok, S::Error> {
        values.iter().collect::<Vec<_>>().serialize(serializer)
    }

    /// Restores readiness facts and rejects duplicate contract identities.
    pub fn deserialize<'de, D: Deserializer<'de>>(
        deserializer: D,
    ) -> Result<BTreeMap<CapabilityContractRef, bool>, D::Error> {
        let entries: Vec<(CapabilityContractRef, bool)> = Vec::deserialize(deserializer)?;
        let mut values = BTreeMap::new();
        for (contract, available) in entries {
            if values.insert(contract, available).is_some() {
                return Err(serde::de::Error::custom(
                    "duplicate capability readiness contract",
                ));
            }
        }
        Ok(values)
    }
}

/// Encodes structured capability-contract category keys as checkpoint-safe records.
mod capability_kind_map_serde {
    use super::{CapabilityContractRef, CapabilityKind};
    use serde::{Deserialize, Deserializer, Serialize, Serializer};
    use std::collections::BTreeMap;

    /// Serializes each exact contract/category pair as a typed record.
    pub fn serialize<S: Serializer>(
        values: &BTreeMap<CapabilityContractRef, CapabilityKind>,
        serializer: S,
    ) -> Result<S::Ok, S::Error> {
        values.iter().collect::<Vec<_>>().serialize(serializer)
    }

    /// Restores category facts and rejects duplicate contract identities.
    pub fn deserialize<'de, D: Deserializer<'de>>(
        deserializer: D,
    ) -> Result<BTreeMap<CapabilityContractRef, CapabilityKind>, D::Error> {
        let entries: Vec<(CapabilityContractRef, CapabilityKind)> = Vec::deserialize(deserializer)?;
        let mut values = BTreeMap::new();
        for (contract, kind) in entries {
            if values.insert(contract, kind).is_some() {
                return Err(serde::de::Error::custom(
                    "duplicate capability kind contract",
                ));
            }
        }
        Ok(values)
    }
}

/// Encodes resource owner mappings as checkpoint-safe records.
mod resource_owner_map_serde {
    use super::{LocalSystemId, ResourceId};
    use serde::{Deserialize, Deserializer, Serialize, Serializer};
    use std::collections::BTreeMap;

    /// Serializes each resource owner mapping as a typed two-element record.
    pub fn serialize<S: Serializer>(
        values: &BTreeMap<ResourceId, LocalSystemId>,
        serializer: S,
    ) -> Result<S::Ok, S::Error> {
        values.iter().collect::<Vec<_>>().serialize(serializer)
    }

    /// Restores resource owner mappings and rejects duplicate resource identities.
    pub fn deserialize<'de, D: Deserializer<'de>>(
        deserializer: D,
    ) -> Result<BTreeMap<ResourceId, LocalSystemId>, D::Error> {
        let entries: Vec<(ResourceId, LocalSystemId)> = Vec::deserialize(deserializer)?;
        let mut values = BTreeMap::new();
        for (resource, owner) in entries {
            if values.insert(resource, owner).is_some() {
                return Err(serde::de::Error::custom(
                    "duplicate resource owner resource",
                ));
            }
        }
        Ok(values)
    }
}

impl NodeRegistration {
    /// Creates a node registration used by matching and adapter negotiation.
    pub fn new(
        node_id: NodeId,
        local_runtime: LocalRuntime,
        contract_version: NodeContractVersion,
        capabilities: Vec<Capability>,
        resources: Vec<Resource>,
    ) -> Self {
        Self::new_with_contracts(
            node_id,
            local_runtime,
            contract_version,
            capabilities,
            Vec::new(),
            resources,
        )
    }

    /// Creates a registration with coarse capabilities and executable canonical contracts.
    pub fn new_with_contracts(
        node_id: NodeId,
        local_runtime: LocalRuntime,
        contract_version: NodeContractVersion,
        capabilities: Vec<Capability>,
        supported_contracts: Vec<CapabilityContractRef>,
        resources: Vec<Resource>,
    ) -> Self {
        let exact_kind = (capabilities.len() == 1).then(|| capabilities[0].kind());
        Self {
            node_id,
            local_systems: vec![LocalSystemDescriptor::new(
                LocalSystemId("default".to_string()),
                local_runtime,
                BTreeMap::new(),
            )],
            contract_version,
            capabilities,
            capability_owners: supported_contracts
                .iter()
                .cloned()
                .map(|contract| (contract, LocalSystemId("default".to_string())))
                .collect(),
            capability_readiness: supported_contracts
                .iter()
                .cloned()
                .map(|contract| (contract, true))
                .collect(),
            capability_kinds: exact_kind
                .map(|kind| {
                    supported_contracts
                        .iter()
                        .cloned()
                        .map(|contract| (contract, kind))
                        .collect()
                })
                .unwrap_or_default(),
            supported_contracts,
            sensors: Vec::new(),
            resource_owners: resources
                .iter()
                .map(|resource| (resource.id().clone(), LocalSystemId("default".to_string())))
                .collect(),
            resources,
            state_exports: Vec::new(),
            memory_providers: Vec::new(),
        }
    }

    /// Creates a v0.2 aggregate registration with explicit local ownership.
    #[allow(clippy::too_many_arguments)]
    pub fn new_with_local_systems(
        node_id: NodeId,
        local_systems: Vec<LocalSystemDescriptor>,
        contract_version: NodeContractVersion,
        capabilities: Vec<Capability>,
        capability_owners: BTreeMap<CapabilityContractRef, LocalSystemId>,
        sensors: Vec<SensorDescriptor>,
        resources: Vec<Resource>,
        resource_owners: BTreeMap<ResourceId, LocalSystemId>,
    ) -> Result<Self, DomainError> {
        let capability_readiness = capability_owners
            .keys()
            .cloned()
            .map(|contract| (contract, true))
            .collect();
        let capability_kinds = (capabilities.len() == 1)
            .then(|| capabilities[0].kind())
            .map(|kind| {
                capability_owners
                    .keys()
                    .cloned()
                    .map(|contract| (contract, kind))
                    .collect()
            })
            .unwrap_or_default();
        Self::new_with_local_systems_and_readiness(
            node_id,
            local_systems,
            contract_version,
            capabilities,
            capability_owners,
            capability_kinds,
            capability_readiness,
            sensors,
            resources,
            resource_owners,
        )
    }

    /// Creates an aggregate registration with explicit ownership and exact readiness facts.
    #[allow(clippy::too_many_arguments)]
    pub fn new_with_local_systems_and_readiness(
        node_id: NodeId,
        local_systems: Vec<LocalSystemDescriptor>,
        contract_version: NodeContractVersion,
        capabilities: Vec<Capability>,
        capability_owners: BTreeMap<CapabilityContractRef, LocalSystemId>,
        capability_kinds: BTreeMap<CapabilityContractRef, CapabilityKind>,
        capability_readiness: BTreeMap<CapabilityContractRef, bool>,
        sensors: Vec<SensorDescriptor>,
        resources: Vec<Resource>,
        resource_owners: BTreeMap<ResourceId, LocalSystemId>,
    ) -> Result<Self, DomainError> {
        let owners = local_systems
            .iter()
            .map(LocalSystemDescriptor::id)
            .collect::<BTreeSet<_>>();
        let sensor_ids = sensors
            .iter()
            .map(SensorDescriptor::id)
            .collect::<BTreeSet<_>>();
        let resource_ids = resources.iter().map(Resource::id).collect::<BTreeSet<_>>();
        let advertised_capability_kinds = capabilities
            .iter()
            .map(Capability::kind)
            .collect::<BTreeSet<_>>();
        if local_systems.is_empty() || owners.len() != local_systems.len() {
            return Err(DomainError::InvalidMissionPlan {
                reason: "node local systems must be nonempty and unique".to_string(),
            });
        }
        if capability_readiness.keys().collect::<BTreeSet<_>>()
            != capability_owners.keys().collect::<BTreeSet<_>>()
            || (!capability_kinds.is_empty()
                && capability_kinds.keys().collect::<BTreeSet<_>>()
                    != capability_owners.keys().collect::<BTreeSet<_>>())
        {
            return Err(DomainError::InvalidMissionPlan {
                reason: "capability readiness and any supplied capability-kind map must cover every configured contract exactly"
                    .to_string(),
            });
        }
        if capability_owners
            .values()
            .any(|owner| !owners.contains(owner))
            || capability_kinds
                .values()
                .any(|kind| !advertised_capability_kinds.contains(kind))
            || sensors
                .iter()
                .any(|sensor| !owners.contains(sensor.local_system_id()))
            || sensor_ids.len() != sensors.len()
            || resource_owners
                .values()
                .any(|owner| !owners.contains(owner))
            || resource_owners.len() != resources.len()
            || resources
                .iter()
                .any(|resource| !resource_owners.contains_key(resource.id()))
            || resource_owners
                .keys()
                .any(|resource_id| !resource_ids.contains(resource_id))
        {
            return Err(DomainError::InvalidMissionPlan {
                reason: "node declaration references an unknown local system owner".to_string(),
            });
        }
        let supported_contracts = capability_owners.keys().cloned().collect();
        Ok(Self {
            node_id,
            local_systems,
            contract_version,
            capabilities,
            supported_contracts,
            capability_owners,
            capability_kinds,
            capability_readiness,
            sensors,
            resources,
            resource_owners,
            state_exports: Vec::new(),
            memory_providers: Vec::new(),
        })
    }

    /// Adds validated selective State and Memory declarations to an aggregate registration.
    ///
    /// Existing constructors deliberately produce empty declarations so legacy checkpoints and
    /// in-process callers retain their prior behavior until a v0.3 adapter opts in.
    pub fn with_state_memory_exports(
        mut self,
        state_exports: Vec<StateExportDescriptor>,
        memory_providers: Vec<MemoryProviderDescriptor>,
    ) -> Result<Self, DomainError> {
        let owners = self
            .local_systems
            .iter()
            .map(LocalSystemDescriptor::id)
            .collect::<BTreeSet<_>>();
        let export_ids = state_exports
            .iter()
            .map(StateExportDescriptor::export_id)
            .collect::<BTreeSet<_>>();
        let provider_ids = memory_providers
            .iter()
            .map(MemoryProviderDescriptor::provider_id)
            .collect::<BTreeSet<_>>();
        if export_ids.len() != state_exports.len() {
            return Err(DomainError::InvalidState {
                reason: "node state export identities must be unique".to_string(),
            });
        }
        if provider_ids.len() != memory_providers.len() {
            return Err(DomainError::InvalidMemory {
                reason: "node memory provider identities must be unique".to_string(),
            });
        }
        if state_exports
            .iter()
            .any(|descriptor| !owners.contains(descriptor.local_system_id()))
        {
            return Err(DomainError::InvalidState {
                reason: "node state export references an unknown local system owner".to_string(),
            });
        }
        if memory_providers
            .iter()
            .any(|descriptor| !owners.contains(descriptor.local_system_id()))
        {
            return Err(DomainError::InvalidMemory {
                reason: "node memory provider references an unknown local system owner".to_string(),
            });
        }
        self.state_exports = state_exports;
        self.memory_providers = memory_providers;
        Ok(self)
    }

    /// Returns the logical node identity.
    pub fn node_id(&self) -> &NodeId {
        &self.node_id
    }

    /// Returns the first runtime for legacy single-runtime readers.
    ///
    /// Aggregate-aware consumers use [`Self::local_systems`] instead.
    pub fn local_runtime(&self) -> &LocalRuntime {
        self.local_systems[0].runtime()
    }

    /// Returns all configured local systems in stable declaration order.
    pub fn local_systems(&self) -> &[LocalSystemDescriptor] {
        &self.local_systems
    }

    /// Returns the semantic Node Contract version exposed by the integration boundary.
    pub const fn contract_version(&self) -> &NodeContractVersion {
        &self.contract_version
    }

    /// Returns the node's advertised capabilities.
    pub fn capabilities(&self) -> &[Capability] {
        &self.capabilities
    }

    /// Returns canonical capability contracts exposed by this node.
    pub fn supported_contracts(&self) -> &[CapabilityContractRef] {
        &self.supported_contracts
    }

    /// Returns the configured owner of one canonical capability contract.
    pub fn capability_owner(&self, contract: &CapabilityContractRef) -> Option<&LocalSystemId> {
        self.capability_owners.get(contract)
    }

    /// Returns whether one configured canonical contract is currently ready to execute.
    ///
    /// A missing fact can only come from a legacy checkpoint, whose former static-ready
    /// semantics remain in force until a complete registration observation replaces it.
    pub fn contract_is_available(&self, contract: &CapabilityContractRef) -> bool {
        self.capability_owners.contains_key(contract)
            && self
                .capability_readiness
                .get(contract)
                .copied()
                .unwrap_or(true)
    }

    /// Returns whether an exact canonical contract is ready under the requested capability kind.
    pub fn contract_is_available_for_kind(
        &self,
        contract: &CapabilityContractRef,
        kind: CapabilityKind,
    ) -> bool {
        self.contract_is_available(contract)
            && self
                .capability_kinds
                .get(contract)
                .copied()
                .or_else(|| self.inferred_legacy_capability_kind())
                == Some(kind)
    }

    /// Infers a missing legacy contract category only when every coarse declaration agrees.
    fn inferred_legacy_capability_kind(&self) -> Option<CapabilityKind> {
        let mut kinds = self.capabilities.iter().map(Capability::kind);
        let first = kinds.next()?;
        kinds.all(|kind| kind == first).then_some(first)
    }

    /// Returns exact readiness facts in deterministic contract order.
    pub const fn capability_readiness(&self) -> &BTreeMap<CapabilityContractRef, bool> {
        &self.capability_readiness
    }

    /// Returns the selective State channels declared by this node.
    pub fn state_exports(&self) -> &[StateExportDescriptor] {
        &self.state_exports
    }

    /// Returns the selective Memory providers declared by this node.
    pub fn memory_providers(&self) -> &[MemoryProviderDescriptor] {
        &self.memory_providers
    }

    /// Returns all node sensors in stable declaration order.
    pub fn sensors(&self) -> &[SensorDescriptor] {
        &self.sensors
    }

    /// Returns the node's advertised resources.
    pub fn resources(&self) -> &[Resource] {
        &self.resources
    }

    /// Returns the configured local-system owner of one resource.
    pub fn resource_owner(&self, resource_id: &ResourceId) -> Option<&LocalSystemId> {
        self.resource_owners.get(resource_id)
    }

    /// Checks whether this registration can satisfy one role requirement.
    pub fn supports_role(&self, requirement: &RoleRequirement) -> bool {
        let has_capability = self.capabilities.iter().any(|capability| {
            capability.kind() == requirement.capability() && capability.is_available()
        });
        let has_contract = requirement.required_contract().is_none_or(|contract| {
            self.contract_is_available_for_kind(contract, requirement.capability())
        });
        let has_resource = requirement.resource_requirements().iter().all(|required| {
            self.resources.iter().any(|resource| {
                resource.kind() == required.kind() && resource.capacity() >= required.units()
            })
        });
        has_capability && has_contract && has_resource
    }

    /// Returns all resource identifiers of the requested category.
    pub fn resource_ids_of_kind(&self, kind: ResourceKind) -> Vec<ResourceId> {
        self.resources
            .iter()
            .filter(|resource| resource.kind() == kind)
            .map(|resource| resource.id().clone())
            .collect()
    }

    /// Checks whether a resource belongs to this node and has the requested kind.
    pub fn owns_resource(&self, resource_id: &ResourceId, kind: Option<ResourceKind>) -> bool {
        self.resources.iter().any(|resource| {
            resource.id() == resource_id && kind.is_none_or(|expected| resource.kind() == expected)
        })
    }
}

/// The latest shared registration, reported health, and liveness facts for one node.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct NodeStateSnapshot {
    /// Local runtime, capability, and resource facts advertised by the node.
    registration: NodeRegistration,
    /// Latest accepted health explicitly reported by the local EAIOS.
    reported_status: NodeStatus,
    /// RoboGuide-local receive time of the latest reported health.
    reported_status_received_at: TimestampMs,
    /// Latest reachability fact observed by RoboGuide.
    liveness: NodeLivenessObservation,
}

impl NodeStateSnapshot {
    /// Creates a shared node snapshot from transport-neutral domain facts.
    pub const fn new(
        registration: NodeRegistration,
        reported_status: NodeStatus,
        reported_status_received_at: TimestampMs,
        liveness: NodeLivenessObservation,
    ) -> Self {
        Self {
            registration,
            reported_status,
            reported_status_received_at,
            liveness,
        }
    }

    /// Returns the node identity represented by this snapshot.
    pub fn node_id(&self) -> &NodeId {
        self.registration.node_id()
    }

    /// Returns the node's latest advertised runtime, capabilities, and resources.
    pub const fn registration(&self) -> &NodeRegistration {
        &self.registration
    }

    /// Returns the latest health explicitly reported by the local EAIOS.
    pub const fn reported_status(&self) -> NodeStatus {
        self.reported_status
    }

    /// Returns when RoboGuide received the latest reported health.
    pub const fn reported_status_received_at(&self) -> TimestampMs {
        self.reported_status_received_at
    }

    /// Returns the latest system-observed liveness fact.
    pub const fn liveness(&self) -> NodeLivenessObservation {
        self.liveness
    }
}
