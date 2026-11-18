//! Original deployment publication digests; never a restored physical routing table.

use crate::{ControlError, ControlPlane};
use domain::RegistryCommandKind;

impl ControlPlane {
    /// Returns original physical provenance even before explicit current deployment reattachment.
    pub fn physical_entity_registry_revision(&self) -> u64 {
        self.registry_provenance
            .as_ref()
            .map_or(0, |p| p.revision())
    }
    /// Returns immutable administrator document identities without declaring current topology.
    pub fn deployment_publications(
        &self,
        kind: RegistryCommandKind,
    ) -> Option<&std::collections::BTreeMap<u64, String>> {
        self.deployment_publication_history.get(key(kind)?)
    }

    /// Retains a monotonic original document identity inside the caller's existing transaction.
    pub fn retain_deployment_publication(
        &mut self,
        kind: RegistryCommandKind,
        revision: u64,
        digest: String,
    ) -> Result<(), ControlError> {
        let namespace = key(kind)
            .ok_or_else(|| ControlError::InvalidProposal("not a deployment publication".into()))?;
        let history = self
            .deployment_publication_history
            .entry(namespace.into())
            .or_default();
        if revision == 0
            || history.len() >= 128
            || history
                .last_key_value()
                .is_some_and(|(old, _)| revision <= *old)
            || !valid_digest(&digest)
        {
            return Err(ControlError::InvalidProposal(
                "deployment publication revision/digest is invalid".into(),
            ));
        }
        history.insert(revision, digest);
        Ok(())
    }

    /// Checks persisted receipts and bounded digest-only histories without recovering routing facts.
    pub(crate) fn validate_deployment_publications(&self) -> Result<(), ControlError> {
        for (namespace, history) in &self.deployment_publication_history {
            let kind = match namespace.as_str() {
                "physical_entities" => RegistryCommandKind::PhysicalEntities,
                "deployment_feasibility" => RegistryCommandKind::DeploymentFeasibility,
                _ => {
                    return Err(ControlError::InvalidProposal(
                        "unknown deployment history".into(),
                    ));
                }
            };
            if history.is_empty()
                || history.len() > 128
                || history.iter().any(|(revision, digest)| {
                    *revision == 0
                        || !valid_digest(digest)
                        || !self.registry_command_receipts.values().any(|receipt| {
                            receipt.kind == kind
                                && receipt.revision == *revision
                                && receipt.registry_digest == *digest
                        })
                })
            {
                return Err(ControlError::InvalidProposal(
                    "deployment history lacks original receipts".into(),
                ));
            }
        }
        Ok(())
    }
}

/// Selects only the two closed deployment authority histories.
fn key(kind: RegistryCommandKind) -> Option<&'static str> {
    match kind {
        RegistryCommandKind::PhysicalEntities => Some("physical_entities"),
        RegistryCommandKind::DeploymentFeasibility => Some("deployment_feasibility"),
        _ => None,
    }
}

/// Accepts only canonical prefixed SHA-256, never arbitrary document aliases.
fn valid_digest(value: &str) -> bool {
    value.strip_prefix("sha256:").is_some_and(|raw| {
        raw.len() == 64
            && raw
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    })
}
