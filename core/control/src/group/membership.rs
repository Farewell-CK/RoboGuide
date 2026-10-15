//! Current physical membership identity without a second binding or reservation authority.

use super::*;
use sha2::{Digest as _, Sha256};

impl ExecutionGroup {
    /// Starts a current Group at its actual empty or committed topology, never inventing history.
    pub(super) fn with_initial_membership(mut self) -> Self {
        self.membership_version = Some(domain::GroupMembershipVersion {
            generation: 1,
            bindings_digest: self.membership_digest(),
        });
        self
    }

    /// Returns a version only while it still describes the complete current Control bindings.
    pub fn membership_version(&self) -> Option<&domain::GroupMembershipVersion> {
        self.membership_version.as_ref().filter(|version| {
            version.validate().is_ok() && version.bindings_digest == self.membership_digest()
        })
    }

    /// Computes canonical exact ownership evidence; Actor labels and discovery never grant membership.
    pub fn membership_digest(&self) -> String {
        let mut legacy = self
            .assignments
            .iter()
            .map(|assignment| {
                let mut resources = assignment.resource_ids().iter().collect::<Vec<_>>();
                resources.sort();
                (assignment.role_id(), assignment.node_id(), resources)
            })
            .collect::<Vec<_>>();
        legacy.sort();
        let mut tasks = self
            .task_executions
            .values()
            .flat_map(|execution| {
                execution.assignments().iter().map(move |assignment| {
                    let mut resources = assignment.resource_ids().iter().collect::<Vec<_>>();
                    resources.sort();
                    (
                        execution.task_ref(),
                        assignment.role_id(),
                        assignment.node_id(),
                        resources,
                    )
                })
            })
            .collect::<Vec<_>>();
        tasks.sort();
        let mut contexts = self
            .context_bindings
            .values()
            .map(|binding| {
                let mut resources = binding.assignment.resource_ids().iter().collect::<Vec<_>>();
                resources.sort();
                (
                    &binding.context_id,
                    &binding.context_role_id,
                    binding.assignment.node_id(),
                    resources,
                )
            })
            .collect::<Vec<_>>();
        contexts.sort();
        let value = serde_json::json!({ "schema_version": "roboguide.group-membership/v0.1", "group_id": self.group_id, "mission_id": self.mission_id,
            "released": self.lifecycle == GroupLifecycle::Released, "legacy": legacy, "tasks": tasks, "contexts": contexts });
        format!(
            "sha256:{:x}",
            Sha256::digest(serde_json::to_vec(&value).expect("typed membership is JSON encodable"))
        )
    }

    /// Advances a known membership only on actual topology changes; legacy absence never becomes fresh.
    pub(crate) fn refresh_membership(&mut self) -> Result<(), ControlError> {
        let digest = self.membership_digest();
        let Some(version) = &mut self.membership_version else {
            return Ok(());
        };
        if digest != version.bindings_digest {
            version.generation = version.generation.checked_add(1).ok_or_else(|| {
                ControlError::InvalidProposal("Group membership generation exhausted".into())
            })?;
            version.bindings_digest = digest;
        }
        Ok(())
    }

    /// Validates retained version evidence before restored Groups may grant content authority.
    pub(crate) fn validate_membership_version(&self) -> Result<(), ControlError> {
        if let Some(version) = &self.membership_version {
            version.validate().map_err(ControlError::InvalidProposal)?;
            if version.bindings_digest != self.membership_digest() {
                return Err(ControlError::InvalidProposal(
                    "Group membership digest differs from current physical bindings".into(),
                ));
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Builds a pure topology identity fixture; it supplies no device or execution evidence.
    fn group() -> ExecutionGroup {
        ExecutionGroup::new_mission(
            ExecutionGroupId::new("membership").unwrap(),
            domain::MissionId::new("mission").unwrap(),
            TaskRef::new(
                domain::MissionId::new("mission").unwrap(),
                TaskId::new("task").unwrap(),
            ),
        )
    }

    /// Sets one topology input for canonical ordering and temporal anti-replay properties.
    fn assignment(node: &str, reverse: bool) -> RoleAssignment {
        let mut resources = vec![
            ResourceId::new("first").unwrap(),
            ResourceId::new("second").unwrap(),
        ];
        if reverse {
            resources.reverse();
        }
        RoleAssignment::new(
            RoleId::new("role").unwrap(),
            NodeId::new(node).unwrap(),
            resources,
        )
    }

    /// Returning to an earlier topology cannot reactivate its old generation; reordering is a no-op.
    #[test]
    fn membership_generation_survives_return_to_original_bindings() {
        let mut group = group();
        group.assignments = vec![assignment("first-owner", false)];
        group.refresh_membership().unwrap();
        let original = group.membership_version().unwrap().clone();
        group.assignments = vec![assignment("first-owner", true)];
        group.refresh_membership().unwrap();
        assert_eq!(group.membership_version(), Some(&original));
        group.assignments = vec![assignment("replacement-owner", false)];
        group.refresh_membership().unwrap();
        let replacement = group.membership_version().unwrap().clone();
        assert!(replacement.generation > original.generation);
        group.assignments = vec![assignment("first-owner", false)];
        group.refresh_membership().unwrap();
        let returned = group.membership_version().unwrap();
        assert_eq!(returned.bindings_digest, original.bindings_digest);
        assert!(returned.generation > replacement.generation);
    }

    /// Reading/restoring historical absence never manufactures an original or current grant epoch.
    #[test]
    fn legacy_membership_stays_unavailable_after_topology_changes() {
        let mut value = serde_json::to_value(group()).unwrap();
        value.as_object_mut().unwrap().remove("membership_version");
        let mut legacy: ExecutionGroup = serde_json::from_value(value).unwrap();
        legacy.assignments = vec![assignment("owner", false)];
        legacy.refresh_membership().unwrap();
        assert!(legacy.membership_version().is_none());
        legacy.validate_membership_version().unwrap();
        let restored: ExecutionGroup =
            serde_json::from_str(&serde_json::to_string(&legacy).unwrap()).unwrap();
        assert!(restored.membership_version().is_none());
    }

    /// A corrupt digest or exhausted counter cannot grant access through an out-of-date version.
    #[test]
    fn membership_corruption_and_overflow_fence_authority() {
        let mut group = group();
        group.membership_version.as_mut().unwrap().bindings_digest =
            format!("sha256:{}", "b".repeat(64));
        assert!(group.membership_version().is_none());
        assert!(group.validate_membership_version().is_err());
        group.membership_version = Some(domain::GroupMembershipVersion {
            generation: u64::MAX,
            bindings_digest: group.membership_digest(),
        });
        group.assignments = vec![assignment("changed", false)];
        assert!(group.refresh_membership().is_err());
        assert!(group.membership_version().is_none());
    }
}
