//! Explicit adoption of an existing static certificate map without inventing tenant ownership.

use crate::{IdentityAction, IdentityCommand, IdentityError, NodeIdentityRegistry};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;

/// Registry generation carrying durable evidence for an online static-profile adoption.
pub const ADOPTED_NODE_IDENTITY_REGISTRY_SCHEMA: &str = "roboguide.node-identity-registry/v0.2";
/// Exact closed administrator command for a complete static-map adoption.
pub const NODE_IDENTITY_ADOPTION_SCHEMA: &str = "roboguide.node-identity-adoption/v0.1";

/// Administrator-declared ownership and validity for one already trusted static leaf.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NodeIdentityAdoptionOwner {
    /// Explicit tenant; never inferred from the Node name or existing capability declaration.
    pub tenant_id: String,
    /// Explicit owning Control domain.
    pub domain_id: String,
    /// Exclusive lifecycle expiry, or explicit zero for deployment-managed validity.
    pub expires_at_ms: u64,
}

/// Complete expected static authority plus explicit ownership; this cannot add a new leaf.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NodeIdentityAdoptionCommand {
    /// Closed input generation.
    pub schema: String,
    /// Original idempotency identity; duplicate input returns the original acceptance receipt.
    pub command_id: String,
    /// Stable registry identity selected by the deployment administrator.
    pub registry_id: String,
    /// Exact current Node-to-leaf map, checked against the original transport before Commit.
    pub expected_bindings: BTreeMap<String, String>,
    /// Explicit owners for exactly the same Node set.
    pub owners: BTreeMap<String, NodeIdentityAdoptionOwner>,
}

/// Original adoption authority retained unchanged through later lifecycle commands and restart.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct NodeIdentityAdoptionReceipt {
    /// Full original bounded command; its source map remains distinct from current rotated leaves.
    pub command: NodeIdentityAdoptionCommand,
    /// Canonical typed original input digest.
    pub input_digest: String,
    /// Original Controller receive time, never refreshed by replay or restore.
    pub accepted_at_ms: u64,
    /// Actual authenticated administrator principal.
    pub authority_subject: String,
}

impl NodeIdentityAdoptionCommand {
    /// Builds a private complete candidate; no session, resource, transport or device is created.
    pub fn build_registry(
        &self,
        now_ms: u64,
        subject: &str,
    ) -> Result<NodeIdentityRegistry, IdentityError> {
        if self.schema != NODE_IDENTITY_ADOPTION_SCHEMA
            || self.command_id.trim().is_empty()
            || self.command_id.len() > 96
            || self.expected_bindings.is_empty()
            || self.expected_bindings.len() > 4096
            || self.expected_bindings.keys().ne(self.owners.keys())
            || subject.trim().is_empty()
            || subject.len() > 256
        {
            return Err(IdentityError::InvalidField("adoption command"));
        }
        let mut registry = NodeIdentityRegistry::new(&self.registry_id);
        for (index, (node, digest)) in self.expected_bindings.iter().enumerate() {
            let owner = &self.owners[node];
            let enrollment = IdentityCommand {
                command_id: format!("{}:enroll:{index}", self.command_id),
                node_id: node.clone(),
                expected_revision: 0,
                action: IdentityAction::Register {
                    tenant_id: owner.tenant_id.clone(),
                    domain_id: owner.domain_id.clone(),
                    digest: digest.clone(),
                    expires_at_ms: owner.expires_at_ms,
                },
            };
            if owner.expires_at_ms != 0 && owner.expires_at_ms <= now_ms {
                return Err(IdentityError::InvalidField("adoption expiry"));
            }
            registry.register(node, &owner.tenant_id, &owner.domain_id, digest, now_ms)?;
            registry
                .records
                .get_mut(node)
                .expect("registered original leaf")
                .expires_at_ms = owner.expires_at_ms;
            let bytes = serde_json::to_vec(&enrollment)
                .map_err(|_| IdentityError::InvalidField("enrollment codec"))?;
            registry.commands.insert(
                enrollment.command_id.clone(),
                crate::IdentityCommandReceipt {
                    command_id: enrollment.command_id,
                    input_digest: format!("sha256:{:x}", Sha256::digest(bytes)),
                    node_id: node.clone(),
                    revision: 1,
                    accepted_at_ms: now_ms,
                    authority_subject: subject.into(),
                },
            );
        }
        registry.validate()?;
        registry.schema = ADOPTED_NODE_IDENTITY_REGISTRY_SCHEMA.into();
        registry.adoption = Some(Box::new(NodeIdentityAdoptionReceipt {
            command: self.clone(),
            input_digest: self.input_digest()?,
            accepted_at_ms: now_ms,
            authority_subject: subject.into(),
        }));
        // Full adoption validation calls this builder to verify the original immutable receipts.
        Ok(registry)
    }

    /// Hashes typed complete input so command replay cannot change the original map or ownership.
    pub fn input_digest(&self) -> Result<String, IdentityError> {
        serde_json::to_vec(self)
            .map(|bytes| format!("sha256:{:x}", Sha256::digest(bytes)))
            .map_err(|_| IdentityError::InvalidField("adoption codec"))
    }
}

impl NodeIdentityAdoptionReceipt {
    /// Revalidates original authority without reapplying it to current rotated or revoked records.
    pub fn validate_registry(&self, current: &NodeIdentityRegistry) -> Result<(), IdentityError> {
        let initial = self
            .command
            .build_registry(self.accepted_at_ms, &self.authority_subject)?;
        if current.registry_id != self.command.registry_id
            || self.input_digest != self.command.input_digest()?
            || initial.adoption.as_deref() != Some(self)
            || initial
                .commands
                .iter()
                .any(|(id, receipt)| current.commands.get(id) != Some(receipt))
            || initial.records.iter().any(|(node, owner)| {
                current.records.get(node).is_none_or(|record| {
                    record.tenant_id != owner.tenant_id || record.domain_id != owner.domain_id
                })
            })
        {
            return Err(IdentityError::InvalidField("adoption receipt"));
        }
        Ok(())
    }
}
