//! Content permission is a current Control decision, separate from catalog and replica evidence.

use crate::{ControlError, ControlPlane, GroupLifecycle};
use domain::{
    ExecutionGroupId, GroupMembershipVersion, MemoryAccessOperation, MemoryArtifactManifest,
    MemoryAuthorizationRegistry, MemoryOwner, MemoryProviderDescriptor, MemoryScope, NodeId,
    RoleId, TaskRef,
};

impl ControlPlane {
    /// Requires an accepted live session/lease and current identity/attestation evidence.
    /// Enrollment or a data-plane certificate alone never creates a registered Node.
    pub fn memory_node_identity_is_current(&self, node: &NodeId, now: domain::TimestampMs) -> bool {
        self.identity_is_eligible(node, now)
            && self
                .current_node_session(node)
                .is_some_and(|session| self.source_session_is_current(node, session, now))
    }
    /// Returns the committed stable data-plane certificate authority.
    pub fn artifact_authorization_registry(
        &self,
    ) -> Option<&domain::ArtifactAuthorizationRegistry> {
        self.artifact_authorization_registry.as_ref()
    }

    /// Admits one exact expected-revision data-plane identity update on an application candidate.
    pub fn accept_artifact_authorization_registry(
        &mut self,
        next: domain::ArtifactAuthorizationRegistry,
        expected_revision: u64,
    ) -> Result<(), ControlError> {
        next.validate().map_err(ControlError::InvalidProposal)?;
        match &self.artifact_authorization_registry {
            Some(old) if old == &next && old.revision == expected_revision => return Ok(()),
            Some(old) if old.revision == expected_revision => old
                .validates_successor(&next)
                .map_err(ControlError::InvalidProposal)?,
            None if expected_revision == 0 && next.revision == 1 => {}
            _ => return Err(denied("Artifact authorization expected revision conflicts")),
        }
        if self.artifact_authorization_history.len() >= 1024 {
            return Err(denied("Artifact authorization history is full"));
        }
        self.artifact_authorization_history
            .insert(next.revision, next.clone());
        self.artifact_authorization_registry = Some(next);
        Ok(())
    }

    /// Rechecks the actual TLS leaf under current policy, Node lifecycle, tenant and certificate time.
    pub fn authorize_artifact_certificate(
        &self,
        leaf: &str,
        actual_expires_at_ms: u64,
        now: domain::TimestampMs,
    ) -> Result<&domain::ArtifactCertificateGrant, ControlError> {
        let registry = self
            .artifact_authorization_registry
            .as_ref()
            .ok_or_else(|| denied("Artifact identity authority is unavailable"))?;
        let grant = registry
            .grants
            .get(leaf)
            .ok_or_else(|| denied("Artifact TLS leaf is not enrolled"))?;
        if now.as_millis() >= actual_expires_at_ms || !grant.active(now.as_millis()) {
            return Err(denied(
                "Artifact actual certificate or current grant is expired/revoked",
            ));
        }
        if let Some(node) = &grant.node_id
            && (!self.memory_node_identity_is_current(node, now)
                || self.node_identity_registry().is_some_and(|identities| {
                    !identities.accepts(
                        node.as_str(),
                        leaf.strip_prefix("sha256:").unwrap_or(leaf),
                        now.as_millis(),
                    ) || identities
                        .records
                        .get(node.as_str())
                        .is_none_or(|identity| identity.tenant_id != grant.tenant_id)
                }))
        {
            return Err(denied(
                "Artifact publisher leaf/tenant is outside the current Node lifecycle",
            ));
        }
        Ok(grant)
    }
    /// Returns explicit content policy without making discovery or bytes into permission.
    pub fn memory_authorization_registry(&self) -> Option<&MemoryAuthorizationRegistry> {
        self.memory_authorization_registry.as_ref()
    }

    /// Returns the original retained policy interpretation for checkpoint/audit validation only.
    pub fn memory_authorization_revision(
        &self,
        revision: u64,
    ) -> Option<&MemoryAuthorizationRegistry> {
        self.memory_authorization_history.get(&revision)
    }

    /// Installs one complete expected-revision registry while retaining every original interpretation.
    pub fn accept_memory_authorization_registry(
        &mut self,
        next: MemoryAuthorizationRegistry,
        expected_revision: u64,
    ) -> Result<(), ControlError> {
        next.validate().map_err(ControlError::InvalidProposal)?;
        match &self.memory_authorization_registry {
            Some(previous) if previous == &next && previous.revision == expected_revision => {
                return Ok(());
            }
            Some(previous) if previous.revision == expected_revision => previous
                .validates_successor(&next)
                .map_err(ControlError::InvalidProposal)?,
            None if expected_revision == 0 && next.revision == 1 => {}
            _ => return Err(denied("Memory authorization expected revision conflicts")),
        }
        if self.memory_authorization_history.len() >= 1024 {
            return Err(denied("Memory authorization history is full"));
        }
        self.memory_authorization_history
            .insert(next.revision, next.clone());
        self.memory_authorization_registry = Some(next);
        Ok(())
    }

    /// Authorizes a certificate-derived principal within immutable tenant and semantic scope.
    /// Group-scoped content requires an exact current member context through the consumer path.
    pub fn authorize_memory_principal(
        &self,
        manifest: &MemoryArtifactManifest,
        tenant: &str,
        principal: &str,
        operation: MemoryAccessOperation,
    ) -> Result<(), ControlError> {
        self.validate_memory_owner_tenant(manifest, tenant)?;
        if matches!(manifest.scope(), MemoryScope::ExecutionGroup(_))
            && operation != MemoryAccessOperation::Discover
            && operation != MemoryAccessOperation::Handoff
        {
            return Err(denied(
                "Group Memory content requires a current exact participant",
            ));
        }
        let policy = self.memory_policy(manifest, tenant)?;
        if !policy
            .principals
            .get(principal)
            .is_some_and(|ops| ops.contains(&operation))
        {
            return Err(denied(
                "Memory operation is not granted to the current principal",
            ));
        }
        Ok(())
    }

    /// Checks exact current physical membership and registered provider without creating a binding.
    /// A caller must separately prove Runtime attempt, actual Node session, certificate and deadline.
    #[allow(clippy::too_many_arguments)]
    pub fn authorize_memory_consumer(
        &self,
        manifest: &MemoryArtifactManifest,
        tenant: &str,
        group_id: &ExecutionGroupId,
        task: &TaskRef,
        role: &RoleId,
        node: &NodeId,
        provider: &MemoryProviderDescriptor,
        operation: MemoryAccessOperation,
        expected_membership: Option<&GroupMembershipVersion>,
    ) -> Result<GroupMembershipVersion, ControlError> {
        self.validate_memory_owner_tenant(manifest, tenant)?;
        let group = self
            .group(group_id)
            .ok_or_else(|| denied("Memory Group is absent"))?;
        let version = group
            .membership_version()
            .ok_or_else(|| denied("Memory Group membership is unavailable"))?;
        if group.lifecycle() == GroupLifecycle::Released
            || task.mission_id() != group.mission_id()
            || self.mission_tenant(group.mission_id()).map_or(
                self.node_identity_registry().is_some() || tenant != "local",
                |owner| owner != tenant,
            )
            || !self.node_tenant_allowed(group.mission_id(), node)
            || !group.task_execution(task).is_some_and(|task| {
                task.assignments()
                    .iter()
                    .any(|a| a.role_id() == role && a.node_id() == node)
            })
            || expected_membership.is_some_and(|expected| expected != version)
        {
            return Err(denied(
                "Memory consumer tenant, exact binding or membership generation changed",
            ));
        }
        match manifest.scope() {
            MemoryScope::Local if !matches!(manifest.owner(), MemoryOwner::Node{node_id,..} if node_id == node) =>
            {
                return Err(denied("Local Memory cannot cross its original Node"));
            }
            MemoryScope::ExecutionGroup(owner) if owner != group_id => {
                return Err(denied("Memory semantic scope belongs to another Group"));
            }
            _ => {}
        }
        provider
            .admit_import(manifest, node)
            .map_err(|e| denied(&e.to_string()))?;
        let policy = self.memory_policy(manifest, tenant)?;
        if !policy.groups.iter().any(|grant| {
            &grant.group_id == group_id
                && grant.operations.contains(&operation)
                && grant.providers.iter().any(|p| p.matches(provider))
        }) {
            return Err(denied(
                "Memory Group/provider operation has no explicit resource grant",
            ));
        }
        Ok(version.clone())
    }

    /// Resolves exact immutable metadata under the current retained policy authority.
    fn memory_policy(
        &self,
        manifest: &MemoryArtifactManifest,
        tenant: &str,
    ) -> Result<&domain::MemoryAccessPolicy, ControlError> {
        self.memory_authorization_registry
            .as_ref()
            .ok_or_else(|| denied("Memory content policy is unavailable"))?
            .policy(manifest, tenant)
            .map_err(ControlError::InvalidProposal)
    }

    /// Rejects foreign/currently reattributed publishers independently of catalog visibility.
    fn validate_memory_owner_tenant(
        &self,
        manifest: &MemoryArtifactManifest,
        tenant: &str,
    ) -> Result<(), ControlError> {
        manifest.validate().map_err(|e| denied(&e.to_string()))?;
        if let MemoryOwner::Node { node_id, .. } = manifest.owner() {
            let allowed = self
                .node_identity_registry()
                .map_or(tenant == "local", |r| {
                    r.records
                        .get(node_id.as_str())
                        .is_some_and(|id| id.tenant_id == tenant)
                });
            if !allowed {
                return Err(denied("Memory owner is outside the original tenant"));
            }
        }
        Ok(())
    }

    /// Rechecks monotonic history without creating authorization for legacy missing evidence.
    pub(crate) fn validate_memory_authority_checkpoint(&self) -> Result<(), ControlError> {
        if self.artifact_authorization_history.len() > 1024 {
            return Err(denied("Artifact policy history exceeds its bound"));
        }
        let mut artifact_previous: Option<&domain::ArtifactAuthorizationRegistry> = None;
        for (revision, registry) in &self.artifact_authorization_history {
            registry.validate().map_err(ControlError::InvalidProposal)?;
            if *revision != registry.revision {
                return Err(denied("Artifact policy history key conflicts"));
            }
            if let Some(old) = artifact_previous {
                old.validates_successor(registry)
                    .map_err(ControlError::InvalidProposal)?;
            } else if *revision != 1 {
                return Err(denied("Artifact policy history must start at one"));
            }
            artifact_previous = Some(registry);
        }
        if artifact_previous != self.artifact_authorization_registry.as_ref() {
            return Err(denied("Artifact current policy lacks original history"));
        }
        if self.memory_authorization_history.len() > 1024 {
            return Err(denied("Memory policy history exceeds its bound"));
        }
        let mut previous: Option<&MemoryAuthorizationRegistry> = None;
        for (revision, registry) in &self.memory_authorization_history {
            registry.validate().map_err(ControlError::InvalidProposal)?;
            if *revision != registry.revision {
                return Err(denied("Memory policy history key conflicts"));
            }
            if let Some(old) = previous {
                old.validates_successor(registry)
                    .map_err(ControlError::InvalidProposal)?;
            } else if *revision != 1 {
                return Err(denied("Memory policy history must start at one"));
            }
            previous = Some(registry);
        }
        if previous != self.memory_authorization_registry.as_ref() {
            return Err(denied("Memory current registry lacks its original history"));
        }
        Ok(())
    }
}

/// Uses the existing typed contract-error boundary; diagnostics never select retry behavior.
fn denied(reason: &str) -> ControlError {
    ControlError::InvalidProposal(reason.into())
}
