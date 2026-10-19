//! Pure current authority checks for application-owned stopped Group exchanges.

use crate::{ControlError, ControlPlane, GroupLifecycle};
use domain::{
    ExecutionGroupId, GroupMembershipVersion, MemoryAccessOperation, MemoryArtifactManifest,
    MemoryScope, MemoryVisibility,
};

impl ControlPlane {
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
}
