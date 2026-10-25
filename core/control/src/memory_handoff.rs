//! Pure current authority checks for application-owned stopped Group exchanges.

use crate::{ControlError, ControlPlane, GroupLifecycle};
use domain::{
    ExecutionGroupId, GroupMembershipVersion, MapArtifactManifest, MemoryAccessOperation,
    MemoryArtifactManifest, MemoryScope, MemoryVisibility,
};

impl ControlPlane {
    /// Authorizes only an immutable child for a yet-unbound revision of the same Mission.
    ///
    /// No Group or resource is created here. The application must freeze the actual complete source
    /// attempts, prove stop/revocation, retain Review and publish inside its authority transaction.
    #[allow(clippy::too_many_arguments)]
    pub fn authorize_revision_memory_child(
        &self,
        parent: &MemoryArtifactManifest,
        child: &MemoryArtifactManifest,
        source: &ExecutionGroupId,
        source_membership: &GroupMembershipVersion,
        mission: &domain::MissionId,
        tenant: &str,
        principal: &str,
    ) -> Result<(), ControlError> {
        self.authorize_revision_memory_source(
            parent,
            child,
            None,
            source,
            source_membership,
            mission,
            tenant,
            principal,
        )
    }

    /// Checks the complete original typed source before a yet-unbound revision narrows its scope.
    /// The application must independently retain Review, original bytes and stop/revoke evidence.
    #[allow(clippy::too_many_arguments)]
    pub fn authorize_revision_typed_map_child(
        &self,
        map: &MapArtifactManifest,
        child: &MemoryArtifactManifest,
        source: &ExecutionGroupId,
        source_membership: &GroupMembershipVersion,
        mission: &domain::MissionId,
        tenant: &str,
        principal: &str,
    ) -> Result<(), ControlError> {
        let parent = domain::map_memory_manifest(map).map_err(ControlError::InvalidProposal)?;
        self.authorize_revision_memory_source(
            &parent,
            child,
            Some(map),
            source,
            source_membership,
            mission,
            tenant,
            principal,
        )
    }

    /// Authorizes only an exact already-scoped parent and retained typed production root.
    /// Current source preparation, stop/revoke and Review remain application-owned gates.
    #[allow(clippy::too_many_arguments)]
    pub fn authorize_revision_scoped_map_child(
        &self,
        map: &MapArtifactManifest,
        parent: &MemoryArtifactManifest,
        child: &MemoryArtifactManifest,
        source: &ExecutionGroupId,
        source_membership: &GroupMembershipVersion,
        mission: &domain::MissionId,
        tenant: &str,
        principal: &str,
    ) -> Result<(), ControlError> {
        domain::validate_scoped_map_manifest(map, parent, source)
            .map_err(ControlError::InvalidProposal)?;
        self.authorize_revision_memory_source(
            parent,
            child,
            Some(map),
            source,
            source_membership,
            mission,
            tenant,
            principal,
        )
    }

    /// Shares exact original topology and child identity checks without weakening typed permission.
    #[allow(clippy::too_many_arguments)]
    fn authorize_revision_memory_source(
        &self,
        parent: &MemoryArtifactManifest,
        child: &MemoryArtifactManifest,
        map: Option<&MapArtifactManifest>,
        source: &ExecutionGroupId,
        source_membership: &GroupMembershipVersion,
        mission: &domain::MissionId,
        tenant: &str,
        principal: &str,
    ) -> Result<(), ControlError> {
        let invalid = |text: &str| ControlError::InvalidProposal(text.into());
        parent.validate().map_err(|e| invalid(&e.to_string()))?;
        child.validate().map_err(|e| invalid(&e.to_string()))?;
        let MemoryScope::ExecutionGroup(target) = child.scope() else {
            return Err(invalid(
                "revision child requires its exact target Group scope",
            ));
        };
        let original = self
            .group(source)
            .ok_or_else(|| invalid("source Group absent"))?;
        if source == target
            || self.group(target).is_some()
            || original.lifecycle() == GroupLifecycle::Released
            || original.mission_id() != mission
            || original.membership_version() != Some(source_membership)
            || self.mission_tenant(mission) != Some(tenant)
            || if let Some(map) = map {
                let projection =
                    domain::map_memory_manifest(map).map_err(ControlError::InvalidProposal)?;
                if parent.scope() == &MemoryScope::Global {
                    projection != *parent
                } else {
                    domain::validate_scoped_map_manifest(map, parent, source).is_err()
                }
            } else {
                !matches!(parent.scope(), MemoryScope::ExecutionGroup(owner) if owner == source)
                    || parent.kind() == domain::MemoryKind::Spatial
            }
            || parent.selector() == child.selector()
            || parent.visibility() != MemoryVisibility::Exchangeable
            || parent.created_at() > child.created_at()
        {
            return Err(invalid(
                "revision Memory source, target, membership or tenant changed",
            ));
        }
        let original_json = serde_json::to_value(parent).map_err(|e| invalid(&e.to_string()))?;
        let mut child_json = serde_json::to_value(child).map_err(|e| invalid(&e.to_string()))?;
        for field in ["selector", "scope", "created_at"] {
            child_json[field] = original_json[field].clone();
        }
        if child_json != original_json {
            return Err(invalid(
                "revision child changed original owner, bytes or provenance",
            ));
        }
        if let Some(map) = map {
            self.authorize_typed_map_principal(
                map,
                tenant,
                principal,
                MemoryAccessOperation::Handoff,
            )?;
            if matches!(parent.scope(), MemoryScope::ExecutionGroup(_)) {
                self.authorize_memory_principal(
                    parent,
                    tenant,
                    principal,
                    MemoryAccessOperation::Handoff,
                )?;
            }
        } else {
            self.authorize_memory_principal(
                parent,
                tenant,
                principal,
                MemoryAccessOperation::Handoff,
            )?;
        }
        let registry = self
            .memory_authorization_registry()
            .ok_or_else(|| invalid("Memory policy absent"))?;
        let policy = registry
            .policy(child, tenant)
            .map_err(ControlError::InvalidProposal)?;
        if policy.typed_map_manifest_digest.is_some()
            || !policy.groups.iter().any(|grant| {
                &grant.group_id == target
                    && [MemoryAccessOperation::Read, MemoryAccessOperation::Import]
                        .iter()
                        .all(|operation| grant.operations.contains(operation))
            })
        {
            return Err(invalid(
                "revision child needs explicit generic target Read/Import policy",
            ));
        }
        Ok(())
    }

    /// Validates an exact immutable Group exchange; the application must independently prove stop,
    /// revocation, original deadlines and receiving attempts before publication or Execute.
    #[allow(clippy::too_many_arguments)]
    pub fn authorize_stopped_memory_handoff(
        &self,
        parent: &MemoryArtifactManifest,
        child: &MemoryArtifactManifest,
        source: &ExecutionGroupId,
        source_membership: &GroupMembershipVersion,
        target_membership: &GroupMembershipVersion,
        tenant: &str,
        principal: &str,
    ) -> Result<(), ControlError> {
        let invalid = |text: &str| ControlError::InvalidProposal(text.into());
        parent.validate().map_err(|e| invalid(&e.to_string()))?;
        child.validate().map_err(|e| invalid(&e.to_string()))?;
        let MemoryScope::ExecutionGroup(target) = child.scope() else {
            return Err(invalid("handoff requires an exact target Group scope"));
        };
        if source == target
            || !matches!(parent.scope(), MemoryScope::ExecutionGroup(group) if group == source)
            || parent.selector() == child.selector()
            || parent.visibility() != MemoryVisibility::Exchangeable
            || child.visibility() != MemoryVisibility::Exchangeable
            || parent.owner() != child.owner()
            || parent.provider_id() != child.provider_id()
            || parent.kind() != child.kind()
            || parent.payload_schema() != child.payload_schema()
            || parent.media_type() != child.media_type()
            || parent.artifact() != child.artifact()
            || parent.source_mission_id() != child.source_mission_id()
            || parent.source_execution_id() != child.source_execution_id()
            || parent.source_task_ref() != child.source_task_ref()
            || parent.created_at() > child.created_at()
        {
            return Err(invalid(
                "handoff must preserve source ownership, bytes, contracts and provenance",
            ));
        }
        for (id, expected) in [(source, source_membership), (target, target_membership)] {
            let group = self
                .group(id)
                .ok_or_else(|| invalid("handoff Group is absent"))?;
            if group.lifecycle() == GroupLifecycle::Released
                || group.membership_version() != Some(expected)
                || self.mission_tenant(group.mission_id()).unwrap_or("local") != tenant
            {
                return Err(invalid("handoff Group tenant or membership changed"));
            }
        }
        self.authorize_memory_principal(parent, tenant, principal, MemoryAccessOperation::Handoff)?;
        let registry = self
            .memory_authorization_registry()
            .ok_or_else(|| invalid("handoff policy unavailable"))?;
        let policy = registry
            .policy(child, tenant)
            .map_err(ControlError::InvalidProposal)?;
        if policy.typed_map_manifest_digest.is_some()
            || child.kind() == domain::MemoryKind::Spatial
            || !policy.groups.iter().any(|grant| {
                &grant.group_id == target
                    && [MemoryAccessOperation::Read, MemoryAccessOperation::Import]
                        .iter()
                        .all(|operation| grant.operations.contains(operation))
            })
        {
            return Err(invalid(
                "handoff child requires explicit generic target Import/Read policy; maps retain their original typed source",
            ));
        }
        Ok(())
    }

    /// Authorizes an actual consuming Group's next typed scope while retaining original production.
    /// Application must additionally retain exact source preparation and complete stop/revoke proof.
    #[allow(clippy::too_many_arguments)]
    pub fn authorize_stopped_scoped_map_handoff(
        &self,
        map: &MapArtifactManifest,
        parent: &MemoryArtifactManifest,
        child: &MemoryArtifactManifest,
        source: &ExecutionGroupId,
        source_membership: &GroupMembershipVersion,
        target_membership: &GroupMembershipVersion,
        tenant: &str,
        principal: &str,
    ) -> Result<(), ControlError> {
        let invalid = |text: &str| ControlError::InvalidProposal(text.into());
        let root = domain::map_memory_manifest(map).map_err(ControlError::InvalidProposal)?;
        if parent != &root {
            domain::validate_scoped_map_manifest(map, parent, source)
                .map_err(ControlError::InvalidProposal)?;
        }
        let MemoryScope::ExecutionGroup(target) = child.scope() else {
            return Err(invalid(
                "typed scope handoff requires its exact receiving Group",
            ));
        };
        domain::validate_scoped_map_manifest(map, child, target)
            .map_err(ControlError::InvalidProposal)?;
        if source == target
            || parent.selector() == child.selector()
            || parent.created_at() > child.created_at()
        {
            return Err(invalid(
                "typed scope handoff changed original source, target or time",
            ));
        }
        for (id, expected) in [(source, source_membership), (target, target_membership)] {
            let group = self
                .group(id)
                .ok_or_else(|| invalid("typed scope handoff Group is absent"))?;
            if group.lifecycle() == GroupLifecycle::Released
                || group.membership_version() != Some(expected)
                || self.mission_tenant(group.mission_id()).unwrap_or("local") != tenant
            {
                return Err(invalid(
                    "typed scope handoff current topology or tenant differs",
                ));
            }
        }
        self.authorize_typed_map_principal(map, tenant, principal, MemoryAccessOperation::Handoff)?;
        if parent != &root {
            self.authorize_memory_principal(
                parent,
                tenant,
                principal,
                MemoryAccessOperation::Handoff,
            )?;
        }
        let registry = self
            .memory_authorization_registry()
            .ok_or_else(|| invalid("typed scope handoff policy is absent"))?;
        let child_policy = registry
            .policy(child, tenant)
            .map_err(ControlError::InvalidProposal)?;
        if child_policy.typed_map_manifest_digest.is_some()
            || !child_policy.groups.iter().any(|grant| {
                &grant.group_id == target
                    && [MemoryAccessOperation::Read, MemoryAccessOperation::Import]
                        .iter()
                        .all(|operation| grant.operations.contains(operation))
            })
        {
            return Err(invalid(
                "typed scope handoff lacks explicit child Read/Import authority",
            ));
        }
        Ok(())
    }

    /// Authorizes a typed Spatial source through the same stopped source/target membership
    /// barrier as a generic handoff, while retaining the complete original map manifest.
    #[allow(clippy::too_many_arguments)]
    pub fn authorize_stopped_typed_map_handoff(
        &self,
        map: &MapArtifactManifest,
        child: &MemoryArtifactManifest,
        source: &ExecutionGroupId,
        source_membership: &GroupMembershipVersion,
        target_membership: &GroupMembershipVersion,
        tenant: &str,
        principal: &str,
    ) -> Result<(), ControlError> {
        let parent = domain::map_memory_manifest(map).map_err(ControlError::InvalidProposal)?;
        child
            .validate()
            .map_err(|error| ControlError::InvalidProposal(error.to_string()))?;
        let MemoryScope::ExecutionGroup(target) = child.scope() else {
            return Err(ControlError::InvalidProposal(
                "typed-map handoff requires an exact target Group scope".into(),
            ));
        };
        if source == target
            || map.source_mission_id()
                != &self
                    .group(source)
                    .ok_or_else(|| ControlError::InvalidProposal("source Group absent".into()))?
                    .mission_id()
                    .clone()
            || parent.source_mission_id() != child.source_mission_id()
            || parent.selector() == child.selector()
            || parent.visibility() != MemoryVisibility::Exchangeable
            || child.visibility() != MemoryVisibility::Exchangeable
            || parent.kind() != child.kind()
            || parent.provider_id() != child.provider_id()
            || parent.payload_schema() != child.payload_schema()
            || parent.media_type() != child.media_type()
            || parent.owner() != child.owner()
            || parent.artifact() != child.artifact()
            || parent.source_execution_id() != child.source_execution_id()
            || parent.source_task_ref() != child.source_task_ref()
            || parent.created_at() > child.created_at()
        {
            return Err(ControlError::InvalidProposal(
                "typed-map handoff changes source ownership, bytes, contracts or provenance".into(),
            ));
        }
        for (id, expected) in [(source, source_membership), (target, target_membership)] {
            let group = self
                .group(id)
                .ok_or_else(|| ControlError::InvalidProposal("handoff Group is absent".into()))?;
            if group.lifecycle() == GroupLifecycle::Released
                || group.membership_version() != Some(expected)
                || self.mission_tenant(group.mission_id()).unwrap_or("local") != tenant
            {
                return Err(ControlError::InvalidProposal(
                    "typed-map handoff Group tenant or membership changed".into(),
                ));
            }
        }
        self.authorize_typed_map_handoff(map, child, tenant, principal)?;
        let registry = self
            .memory_authorization_registry()
            .ok_or_else(|| ControlError::InvalidProposal("handoff policy unavailable".into()))?;
        let policy = registry
            .policy(child, tenant)
            .map_err(ControlError::InvalidProposal)?;
        if policy.typed_map_manifest_digest.is_some()
            || !policy.groups.iter().any(|grant| {
                &grant.group_id == target
                    && [MemoryAccessOperation::Read, MemoryAccessOperation::Import]
                        .iter()
                        .all(|operation| grant.operations.contains(operation))
            })
        {
            return Err(ControlError::InvalidProposal(
                "typed-map handoff child requires explicit generic target Import/Read policy"
                    .into(),
            ));
        }
        Ok(())
    }
}
