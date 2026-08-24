//! HTTP registration DTO and explicit domain conversion.

use crate::http::HttpAdapterError;
use domain::{
    Capability, CapabilityKind, LocalRuntime, NODE_CONTRACT_VERSION_V0_1, NodeContractVersion,
    NodeId, NodeRegistration, Resource, ResourceId, ResourceKind,
};
use serde::{Deserialize, Serialize};

/// HTTP representation of one local runtime descriptor.
#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct WireLocalRuntime {
    /// Local EAIOS or vendor runtime name.
    name: String,
    /// Local runtime version.
    version: String,
}

/// HTTP representation of one capability declaration.
#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct WireCapability {
    /// Stable v0.1 capability category.
    kind: String,
    /// Current source-declared availability.
    available: bool,
}

/// HTTP representation of one resource declaration.
#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct WireResource {
    /// Stable resource identity.
    id: String,
    /// Stable v0.1 resource category.
    kind: String,
    /// Positive source-declared capacity.
    capacity: u32,
}

/// Versioned HTTP registration response.
#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(crate) struct WireRegistration {
    /// Semantic Node Contract version, not an HTTP API implementation version.
    schema_version: String,
    /// Logical node identity.
    node_id: String,
    /// Local runtime descriptor.
    local_runtime: WireLocalRuntime,
    /// Exposed coarse capabilities.
    capabilities: Vec<WireCapability>,
    /// Exposed reservable resources.
    resources: Vec<WireResource>,
}

impl TryFrom<WireRegistration> for NodeRegistration {
    type Error = HttpAdapterError;

    /// Validates versioned registration data before it enters Shared Node State.
    fn try_from(registration: WireRegistration) -> Result<Self, Self::Error> {
        if registration.schema_version != NODE_CONTRACT_VERSION_V0_1 {
            return Err(HttpAdapterError::protocol(format!(
                "unsupported node contract {}",
                registration.schema_version
            )));
        }
        let capabilities = registration
            .capabilities
            .into_iter()
            .map(|capability| {
                Ok(Capability::new(
                    capability_kind(&capability.kind)?,
                    capability.available,
                ))
            })
            .collect::<Result<Vec<_>, HttpAdapterError>>()?;
        let resources = registration
            .resources
            .into_iter()
            .map(|resource| {
                Resource::new(
                    ResourceId::new(resource.id)
                        .map_err(|error| HttpAdapterError::protocol(error.to_string()))?,
                    resource_kind(&resource.kind)?,
                    resource.capacity,
                )
                .map_err(|error| HttpAdapterError::protocol(error.to_string()))
            })
            .collect::<Result<Vec<_>, HttpAdapterError>>()?;
        Ok(Self::new(
            NodeId::new(registration.node_id)
                .map_err(|error| HttpAdapterError::protocol(error.to_string()))?,
            LocalRuntime::new(
                registration.local_runtime.name,
                registration.local_runtime.version,
            )
            .map_err(|error| HttpAdapterError::protocol(error.to_string()))?,
            NodeContractVersion::v0_1(),
            capabilities,
            resources,
        ))
    }
}

/// Maps one v0.1 capability token into the closed bootstrap capability vocabulary.
fn capability_kind(value: &str) -> Result<CapabilityKind, HttpAdapterError> {
    match value {
        "mobility" => Ok(CapabilityKind::Mobility),
        "transport" => Ok(CapabilityKind::Transport),
        "compute" => Ok(CapabilityKind::Compute),
        "observation" => Ok(CapabilityKind::Observation),
        _ => Err(HttpAdapterError::protocol(format!(
            "unsupported capability {value}"
        ))),
    }
}

/// Maps one v0.1 resource token into the current resource vocabulary.
fn resource_kind(value: &str) -> Result<ResourceKind, HttpAdapterError> {
    match value {
        "space" => Ok(ResourceKind::Space),
        "compute" => Ok(ResourceKind::Compute),
        "time" => Ok(ResourceKind::Time),
        _ => Err(HttpAdapterError::protocol(format!(
            "unsupported resource kind {value}"
        ))),
    }
}
