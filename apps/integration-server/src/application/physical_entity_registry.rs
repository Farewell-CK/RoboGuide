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
    parse_document(serde_json::from_str(&content)?).map_err(Into::into)
}

/// Validates the same closed deployment document for authenticated online administration.
pub(super) fn parse_document(
    document: serde_json::Value,
) -> Result<domain::PhysicalEntityRegistrySnapshot, String> {
    parse_file(document).map_err(|e| e.to_string())
}

/// Converts validated document fields into the existing unique entity/Node routing snapshot.
fn parse_file(
    document: serde_json::Value,
) -> Result<domain::PhysicalEntityRegistrySnapshot, Box<dyn std::error::Error>> {
    let file: PhysicalEntityRegistryFile = serde_json::from_value(document)?;
    if file.schema != PHYSICAL_ENTITY_REGISTRY_SCHEMA {
        return Err(format!(
            "physical entity registry uses unsupported schema {}",
            file.schema
        )
        .into());
    }
    if file.routing_profile != "one-routable-entity-per-node" {
        return Err(format!(
            "physical entity registry uses unsupported routing profile {}",
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

/// Projects canonical sorted complete routing evidence without creating any Node or binding.
pub(super) fn document(snapshot: &domain::PhysicalEntityRegistrySnapshot) -> serde_json::Value {
    serde_json::json!({"schema":PHYSICAL_ENTITY_REGISTRY_SCHEMA,"registry_id":snapshot.registry_id(),"revision":snapshot.revision(),"routing_profile":snapshot.routing_profile(),"entities":snapshot.entities().map(|e| serde_json::json!({"entity_id":e.entity_id(),"node_id":e.node_id()})).collect::<Vec<_>>()})
}
