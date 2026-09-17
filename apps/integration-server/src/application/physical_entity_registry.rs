//! Deployment-owned physical executor routing snapshot ingestion.

use crate::*;

/// Deployment physical entity registry document.
#[derive(serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct PhysicalEntityRegistryFile {
    /// Frozen registry schema identity.
    schema: String,
    /// Stable deployment registry identity.
    registry_id: String,
    /// Monotonic deployment revision.
    revision: u64,
    /// Explicit current Node routing limitation.
    routing_profile: String,
    /// Entity-to-node registrations.
    entities: Vec<PhysicalEntityRegistryEntry>,
}

/// One deployment entity registration entry.
#[derive(serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct PhysicalEntityRegistryEntry {
    /// Stable deployment-owned physical identity.
    entity_id: String,
    /// Node currently providing the entity.
    node_id: String,
}

/// Loads a mission-independent deployment entity registry from versioned JSON.
///
/// Unlike mission-scoped placement, this maps stable physical executor identities
/// onto their current routing Nodes without authorizing a Mission or reservation.
pub(crate) fn load_physical_entity_registry_file(
    path: &Path,
) -> Result<domain::PhysicalEntityRegistrySnapshot, Box<dyn std::error::Error>> {
    let content = std::fs::read_to_string(path)?;
    let file: PhysicalEntityRegistryFile = serde_json::from_str(&content)?;
    if file.schema != PHYSICAL_ENTITY_REGISTRY_SCHEMA {
        return Err(format!(
            "physical entity registry {} uses unsupported schema {}",
            path.display(),
            file.schema
        )
        .into());
    }
    if file.routing_profile != "one-routable-entity-per-node" {
        return Err(format!(
            "physical entity registry {} uses unsupported routing profile {}",
            path.display(),
            file.routing_profile
        )
        .into());
    }
    let registrations = file
        .entities
        .into_iter()
        .map(|entry| {
            Ok(domain::PhysicalEntityRegistration::new(
                domain::PhysicalEntityId::new(entry.entity_id)?,
                domain::NodeId::new(entry.node_id)?,
            ))
        })
        .collect::<Result<Vec<_>, domain::DomainError>>()?;
    Ok(domain::PhysicalEntityRegistrySnapshot::new(
        domain::PhysicalEntityRegistryId::new(file.registry_id)?,
        file.revision,
        domain::PhysicalEntityRoutingProfile::OneRoutableEntityPerNode,
        registrations,
    )?)
}
