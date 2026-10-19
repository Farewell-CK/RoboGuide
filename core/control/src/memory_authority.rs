//! Content permission is a current Control decision, separate from catalog and replica evidence.

use crate::{ControlError, ControlPlane, GroupLifecycle};
use domain::{
    ExecutionGroupId, GroupMembershipVersion, MemoryAccessOperation, MemoryArtifactManifest,
    MemoryAuthorizationRegistry, MemoryOwner, MemoryProviderDescriptor, MemoryScope, NodeId,
    RoleId, TaskRef,
};

impl ControlPlane {
    /// Selects a compatible current receiver in stable provider identity order, without effects.
    pub fn select_memory_provider<S: ports::SharedNodeStateReader>(
        &self,
        state: &S,
        node: &NodeId,
        requirement: &domain::MemoryRequirement,
        operation: &domain::OperationRef,
        now: domain::TimestampMs,
    ) -> Result<MemoryProviderDescriptor, ControlError> {
        requirement
            .validate()
            .map_err(ControlError::InvalidProposal)?;
        if !self.memory_node_identity_is_current(node, now) {
            return Err(denied(
                "Memory receiver lacks a current accepted identity/session",
            ));
        }
        let snapshot = state
            .node(node)
            .ok_or_else(|| denied("Memory receiver is absent"))?;
        if !matches!(
            snapshot.registration().contract_version().as_str(),
            domain::NODE_CONTRACT_VERSION_V0_10 | domain::NODE_CONTRACT_VERSION_V0_11
        ) {
            return Err(denied(
                "Memory preparation requires explicitly negotiated Node Contract v0.10",
            ));
        }
        let owner = snapshot
            .registration()
            .operation_owner(operation)
            .ok_or_else(|| denied("Memory consumer operation owner is unavailable"))?;
        snapshot
            .registration()
            .memory_providers()
            .iter()
            .filter(|provider| {
                provider.supports_use_lease()
                    && requirement.matches_provider(provider)
                    && provider.local_system_id() == owner
            })
            .min_by(|left, right| left.provider_id().cmp(right.provider_id()))
            .cloned()
            .ok_or_else(|| denied("no registered receiver supports this Memory contract"))
    }
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
        Self::authorize_memory_policy_principal(policy, principal, operation)
    }

    /// Authorizes an immutable source-bound handoff without transferring ownership or broadening
    /// semantic scope. Cross-Group handoff is allowed only from a Global source to an existing
    /// current target Group; a Group source may narrow to Local or remain within that Group.
    pub fn authorize_memory_handoff(
        &self,
        parent: &MemoryArtifactManifest,
        child: &MemoryArtifactManifest,
        tenant: &str,
        principal: &str,
    ) -> Result<(), ControlError> {
        parent
            .validate()
            .map_err(|error| ControlError::InvalidProposal(error.to_string()))?;
        child
            .validate()
            .map_err(|error| ControlError::InvalidProposal(error.to_string()))?;
        if parent.selector() == child.selector()
            || parent.visibility() != domain::MemoryVisibility::Exchangeable
            || child.visibility() != domain::MemoryVisibility::Exchangeable
            || parent.kind() != child.kind()
            || parent.provider_id() != child.provider_id()
            || parent.payload_schema() != child.payload_schema()
            || parent.media_type() != child.media_type()
            || parent.owner() != child.owner()
            || parent.artifact() != child.artifact()
        {
            return Err(denied(
                "Memory handoff changes immutable ownership, contract or bytes",
            ));
        }
        self.authorize_memory_principal(parent, tenant, principal, MemoryAccessOperation::Handoff)?;
        let scope_allowed = match (parent.scope(), child.scope()) {
            (MemoryScope::Local, MemoryScope::Local)
            | (MemoryScope::ExecutionGroup(_), MemoryScope::Local)
            | (MemoryScope::Global, MemoryScope::Local | MemoryScope::Global) => true,
            (MemoryScope::ExecutionGroup(source), MemoryScope::ExecutionGroup(target)) => {
                if source != target {
                    return Err(denied(
                        "Group-scoped Memory handoff cannot cross Groups without a Global source",
                    ));
                }
                true
            }
            (MemoryScope::Global, MemoryScope::ExecutionGroup(target)) => {
                let group = self
                    .group(target)
                    .ok_or_else(|| denied("Memory handoff target Group is absent"))?;
                if group.lifecycle() == GroupLifecycle::Released
                    || group.membership_version().is_none()
                {
                    return Err(denied("Memory handoff target Group is not current"));
                }
                if child
                    .source_mission_id()
                    .is_some_and(|mission| mission != group.mission_id())
                {
                    return Err(denied(
                        "Memory handoff child provenance belongs to another Mission",
                    ));
                }
                true
            }
            _ => false,
        };
        if !scope_allowed {
            return Err(denied("Memory handoff broadens immutable semantic scope"));
        }
        Ok(())
    }

    /// Checks complete Spatial provenance before granting a certificate-derived principal access.
    pub fn authorize_typed_map_principal(
        &self,
        map: &domain::MapArtifactManifest,
        tenant: &str,
        principal: &str,
        operation: MemoryAccessOperation,
    ) -> Result<(), ControlError> {
        let manifest = domain::map_memory_manifest(map).map_err(ControlError::InvalidProposal)?;
        self.validate_memory_owner_tenant(&manifest, tenant)?;
        let policy = self
            .memory_authorization_registry
            .as_ref()
            .ok_or_else(|| denied("Memory content policy is unavailable"))?
            .typed_map_policy(map, tenant)
            .map_err(ControlError::InvalidProposal)?;
        Self::authorize_memory_policy_principal(policy, principal, operation)
    }

    /// Applies explicit principal operations after source identity and scope were validated.
    fn authorize_memory_policy_principal(
        policy: &domain::MemoryAccessPolicy,
        principal: &str,
        operation: MemoryAccessOperation,
    ) -> Result<(), ControlError> {
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
        self.authorize_memory_consumer_inner(
            manifest,
            tenant,
            group_id,
            task,
            role,
            node,
            provider,
            operation,
            expected_membership,
            None,
        )
    }

    /// Authorizes complete typed-map input for an exact current physical participant.
    #[allow(clippy::too_many_arguments)]
    pub fn authorize_typed_map_consumer(
        &self,
        map: &domain::MapArtifactManifest,
        tenant: &str,
        group_id: &ExecutionGroupId,
        task: &TaskRef,
        role: &RoleId,
        node: &NodeId,
        provider: &MemoryProviderDescriptor,
        operation: MemoryAccessOperation,
        expected_membership: Option<&GroupMembershipVersion>,
    ) -> Result<GroupMembershipVersion, ControlError> {
        let manifest = domain::map_memory_manifest(map).map_err(ControlError::InvalidProposal)?;
        self.authorize_memory_consumer_inner(
            &manifest,
            tenant,
            group_id,
            task,
            role,
            node,
            provider,
            operation,
            expected_membership,
            Some(map),
        )
    }

    /// Shares scope/binding checks while requiring complete typed provenance when policy names it.
    #[allow(clippy::too_many_arguments)]
    fn authorize_memory_consumer_inner(
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
        typed_map: Option<&domain::MapArtifactManifest>,
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
        let policy = if let Some(map) = typed_map {
            if domain::map_memory_manifest(map).map_err(ControlError::InvalidProposal)? != *manifest
            {
                return Err(denied(
                    "typed Memory source differs from its complete original projection",
                ));
            }
            self.memory_authorization_registry
                .as_ref()
                .ok_or_else(|| denied("Memory policy is unavailable"))?
                .typed_map_policy(map, tenant)
                .map_err(ControlError::InvalidProposal)?
        } else {
            self.memory_policy(manifest, tenant)?
        };
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

    /// Revalidates source policies and the exact retained role without revoking unrelated participants.
    /// Application must also prove the exact current Runtime attempt and original deadline.
    pub fn validate_memory_preparation<S: ports::SharedNodeStateReader>(
        &self,
        state: &S,
        preparation: &domain::MemoryPreparation,
        now: domain::TimestampMs,
    ) -> Result<(), ControlError> {
        self.validate_memory_context(
            state,
            preparation,
            now,
            &[MemoryAccessOperation::Import, MemoryAccessOperation::Read],
        )
    }

    /// Revalidates ongoing use under current Read authority without renewing import permission.
    pub fn validate_memory_use_context<S: ports::SharedNodeStateReader>(
        &self,
        state: &S,
        preparation: &domain::MemoryPreparation,
        now: domain::TimestampMs,
    ) -> Result<(), ControlError> {
        self.validate_memory_context(state, preparation, now, &[MemoryAccessOperation::Read])
    }

    /// Shares exact ownership checks while keeping initial import and ongoing read gates distinct.
    fn validate_memory_context<S: ports::SharedNodeStateReader>(
        &self,
        state: &S,
        preparation: &domain::MemoryPreparation,
        now: domain::TimestampMs,
        operations: &[MemoryAccessOperation],
    ) -> Result<(), ControlError> {
        preparation
            .validate()
            .map_err(ControlError::InvalidProposal)?;
        let group = self
            .group(&preparation.group_id)
            .ok_or_else(|| denied("prepared Memory Group is absent"))?;
        let membership = group
            .membership_version()
            .ok_or_else(|| denied("prepared Memory membership is unavailable"))?;
        let role = group
            .role_requirement(&preparation.task_ref, &preparation.role_id)
            .ok_or_else(|| denied("prepared Memory Role is absent"))?;
        if membership.generation < preparation.membership.generation
            || (membership.generation == preparation.membership.generation
                && membership.bindings_digest != preparation.membership.bindings_digest)
            || self.current_node_session(&preparation.node_id)
                != Some(preparation.source_session_id.as_str())
            || !self.node_is_eligible_for_role(state, &preparation.node_id, role, now)
            || role.memories()
                != preparation
                    .inputs
                    .iter()
                    .map(|input| input.requirement.clone())
                    .collect::<Vec<_>>()
        {
            return Err(denied(
                "prepared Memory source session, role requirements or current eligibility changed",
            ));
        }
        let assignment = group
            .task_execution(&preparation.task_ref)
            .and_then(|task| {
                task.assignments().iter().find(|a| {
                    a.role_id() == &preparation.role_id && a.node_id() == &preparation.node_id
                })
            })
            .ok_or_else(|| denied("prepared Memory current physical assignment changed"))?;
        let resources = assignment
            .resource_ids()
            .iter()
            .map(|id| id.as_str())
            .collect::<std::collections::BTreeSet<_>>();
        if resources
            != preparation
                .resource_ids
                .iter()
                .map(String::as_str)
                .collect()
        {
            return Err(denied("prepared Memory committed resources changed"));
        }
        let registry = self
            .memory_authorization_registry
            .as_ref()
            .ok_or_else(|| denied("prepared Memory content authority is unavailable"))?;
        if registry.registry_id != preparation.registry_id
            || registry.revision < preparation.registry_revision
        {
            return Err(denied(
                "prepared Memory content authority identity or revision changed",
            ));
        }
        let original = self
            .memory_authorization_history
            .get(&preparation.registry_revision)
            .ok_or_else(|| denied("original prepared Memory authority history is unavailable"))?;
        let registered = state
            .node(&preparation.node_id)
            .ok_or_else(|| denied("prepared Memory receiver is absent"))?;
        for input in &preparation.inputs {
            if !registered
                .registration()
                .memory_providers()
                .contains(&input.consumer)
            {
                return Err(denied(
                    "prepared Memory original receiver declaration changed",
                ));
            }
            let old_policy = if let Some(map) = &input.typed_map {
                original.typed_map_policy(map, &preparation.tenant_id)
            } else {
                original.policy(&input.manifest, &preparation.tenant_id)
            }
            .map_err(ControlError::InvalidProposal)?;
            for &operation in operations {
                if !old_policy.groups.iter().any(|grant| {
                    grant.group_id == preparation.group_id
                        && grant.operations.contains(&operation)
                        && grant.providers.iter().any(|p| p.matches(&input.consumer))
                }) {
                    return Err(denied(
                        "original prepared Memory policy did not authorize this participant/provider operation",
                    ));
                }
                self.authorize_memory_consumer_inner(
                    &input.manifest,
                    &preparation.tenant_id,
                    &preparation.group_id,
                    &preparation.task_ref,
                    &preparation.role_id,
                    &preparation.node_id,
                    &input.consumer,
                    operation,
                    None,
                    input.typed_map.as_ref(),
                )?;
            }
        }
        Ok(())
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
