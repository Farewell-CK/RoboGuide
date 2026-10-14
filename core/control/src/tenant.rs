//! Immutable Mission tenant authority and scoped evidence views for Matching, Commit and Recovery.

use crate::{ControlError, ControlPlane};
use domain::{MissionId, NodeId, NodeStateSnapshot};
use ports::SharedNodeStateReader;
use std::collections::BTreeSet;

/// Trusted migration input for historical accepted Missions lacking authenticated tenant ownership.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MissionTenantMigration {
    /// Immutable administrator command identity.
    pub command_id: String,
    /// Existing Mission, never a new plan or executor assignment.
    pub mission_id: MissionId,
    /// New authenticated namespace for previously unbound authority.
    pub tenant_id: String,
    /// Exact normalized accepted plan digest from the read-only authority projection.
    pub source_plan_digest: String,
}

/// Original audit receipt retained across restore and duplicate commands.
#[derive(Debug, Clone, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct MissionTenantMigrationReceipt {
    /// Exact immutable input.
    pub command: MissionTenantMigration,
    /// Actual authorized administrator principal.
    pub authority_subject: String,
    /// Original Controller receive time.
    pub accepted_at_ms: u64,
}

/// A reader whose allowlist comes from Control's deployment registry, not from client inventory.
pub(crate) struct TenantState<'a, S> {
    /// Original source-aware registration evidence.
    source: &'a S,
    /// Exact tenant-owned Nodes; None is the explicit legacy single-domain profile.
    allowed: Option<BTreeSet<NodeId>>,
}

impl<S: SharedNodeStateReader> SharedNodeStateReader for TenantState<'_, S> {
    /// Returns evidence only for a tenant-admitted Node.
    fn node(&self, node_id: &NodeId) -> Option<&NodeStateSnapshot> {
        if self
            .allowed
            .as_ref()
            .is_some_and(|allowed| !allowed.contains(node_id))
        {
            return None;
        }
        self.source.node(node_id)
    }

    /// Preserves source ordering while excluding foreign Nodes.
    fn nodes(&self) -> Vec<&NodeStateSnapshot> {
        self.source
            .nodes()
            .into_iter()
            .filter(|node| {
                self.allowed
                    .as_ref()
                    .is_none_or(|allowed| allowed.contains(node.node_id()))
            })
            .collect()
    }
}

impl ControlPlane {
    /// Verifies retained migration attribution against immutable Mission ownership during restore.
    pub(crate) fn validate_tenant_checkpoint(&self) -> Result<(), ControlError> {
        if self.tenant_migrations.len() > 16384
            || self.tenant_migrations.iter().any(|(id, receipt)| {
                id != &receipt.command.command_id
                    || id.trim().is_empty()
                    || id.len() > 128
                    || receipt.authority_subject.trim().is_empty()
                    || receipt.authority_subject.len() > 256
                    || self.mission_tenant(&receipt.command.mission_id)
                        != Some(receipt.command.tenant_id.as_str())
                    || !receipt
                        .command
                        .source_plan_digest
                        .strip_prefix("sha256:")
                        .is_some_and(|hex| {
                            hex.len() == 64
                                && hex.bytes().all(|byte| {
                                    byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte)
                                })
                        })
            })
        {
            return Err(ControlError::InvalidProposal(
                "tenant migration checkpoint is invalid".into(),
            ));
        }
        Ok(())
    }

    /// Applies explicit historical migration without scheduling, releasing ownership or creating facts.
    pub fn migrate_mission_tenant(
        &mut self,
        command: MissionTenantMigration,
        current_plan_digest: &str,
        authority_subject: &str,
        now: domain::TimestampMs,
    ) -> Result<MissionTenantMigrationReceipt, ControlError> {
        if let Some(previous) = self.tenant_migrations.get(&command.command_id) {
            return if previous.command == command {
                Ok(previous.clone())
            } else {
                Err(ControlError::InvalidProposal(
                    "tenant migration command conflicts".into(),
                ))
            };
        }
        if self.tenant_migrations.len() >= 16384
            || command.command_id.trim().is_empty()
            || command.command_id.len() > 128
            || authority_subject.trim().is_empty()
            || authority_subject.len() > 256
            || command.source_plan_digest != current_plan_digest
            || !current_plan_digest
                .strip_prefix("sha256:")
                .is_some_and(|hex| {
                    hex.len() == 64
                        && hex
                            .bytes()
                            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
                })
        {
            return Err(ControlError::InvalidProposal(
                "tenant migration source/command is invalid".into(),
            ));
        }
        if self.mission_tenant(&command.mission_id).is_some() {
            return Err(ControlError::InvalidProposal(
                "Mission already has immutable tenant ownership".into(),
            ));
        }
        if let Some(registry) = self.node_identity_registry()
            && self
                .groups
                .values()
                .filter(|group| group.mission_id() == &command.mission_id)
                .flat_map(|group| group.task_executions())
                .flat_map(|task| task.assignments())
                .any(|assignment| {
                    registry
                        .records
                        .get(assignment.node_id().as_str())
                        .is_none_or(|owner| owner.tenant_id != command.tenant_id)
                })
        {
            return Err(ControlError::InvalidProposal(
                "tenant migration conflicts with existing physical ownership".into(),
            ));
        }
        self.register_mission_tenant(command.mission_id.clone(), &command.tenant_id)?;
        let receipt = MissionTenantMigrationReceipt {
            command,
            authority_subject: authority_subject.into(),
            accepted_at_ms: now.as_millis(),
        };
        self.tenant_migrations
            .insert(receipt.command.command_id.clone(), receipt.clone());
        Ok(receipt)
    }

    /// Rejects lifecycle deployment over unmigrated or cross-tenant historical physical bindings.
    pub fn validate_mission_tenant_authority(&self) -> Result<(), ControlError> {
        if self.node_identity_registry().is_none() {
            return Ok(());
        }
        for group in self.groups.values() {
            if self.mission_tenant(group.mission_id()).is_none()
                || group
                    .task_executions()
                    .flat_map(|task| task.assignments())
                    .any(|assignment| {
                        !self.node_tenant_allowed(group.mission_id(), assignment.node_id())
                    })
            {
                return Err(ControlError::InvalidProposal("historical Mission requires trusted tenant/binding migration before lifecycle deployment".into()));
            }
        }
        Ok(())
    }
    /// Records a certificate-authorized immutable Mission tenant before Group creation.
    pub fn register_mission_tenant(
        &mut self,
        mission_id: MissionId,
        tenant: &str,
    ) -> Result<(), ControlError> {
        if tenant.trim().is_empty() || tenant.len() > 128 {
            return Err(ControlError::InvalidProposal(
                "Mission tenant is invalid".into(),
            ));
        }
        if self
            .mission_tenants
            .get(&mission_id)
            .is_some_and(|previous| previous != tenant)
        {
            return Err(ControlError::InvalidProposal(
                "Mission tenant is immutable".into(),
            ));
        }
        self.mission_tenants.insert(mission_id, tenant.into());
        Ok(())
    }

    /// Returns durable ownership, including terminal Missions retained for audit.
    pub fn mission_tenant(&self, mission_id: &MissionId) -> Option<&str> {
        self.mission_tenants.get(mission_id).map(String::as_str)
    }

    /// Creates an owned allowlist so subsequent Control mutations do not borrow a second authority.
    pub(crate) fn tenant_state<'a, S: SharedNodeStateReader>(
        &self,
        source: &'a S,
        mission_id: &MissionId,
    ) -> TenantState<'a, S> {
        let allowed = self.node_identity_registry().map(|registry| {
            registry
                .records
                .values()
                .filter(|identity| {
                    self.mission_tenant(mission_id) == Some(identity.tenant_id.as_str())
                })
                .filter_map(|identity| NodeId::new(&identity.node_id).ok())
                .collect()
        });
        TenantState { source, allowed }
    }

    /// Checks exact current owner before a recovery proposal can refer to a replacement Node.
    pub(crate) fn node_tenant_allowed(&self, mission_id: &MissionId, node_id: &NodeId) -> bool {
        self.node_identity_registry().is_none_or(|registry| {
            registry
                .records
                .get(node_id.as_str())
                .is_some_and(|identity| {
                    self.mission_tenant(mission_id) == Some(identity.tenant_id.as_str())
                })
        })
    }
}
