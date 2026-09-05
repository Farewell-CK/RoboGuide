//! Controller-owned preflight for execution coordination mechanisms implemented by this build.

use crate::OrchestrationError;
use domain::{ExecutionCouplingMode, ExecutionRelationKind, MissionPlan};

/// Closed implementation profile used to reject structurally valid but non-executable plans.
#[derive(Debug, Clone, Copy, Default)]
pub struct SupportedMechanismProfile;

impl SupportedMechanismProfile {
    /// Returns the mechanisms implemented by the current Controller and Runtime composition.
    pub const fn current() -> Self {
        Self
    }

    /// Returns whether Runtime has an evidence reducer for this relation family.
    pub const fn supports_relation(self, kind: ExecutionRelationKind) -> bool {
        matches!(
            kind,
            ExecutionRelationKind::RequiresActive | ExecutionRelationKind::SharedSpatialReference
        )
    }

    /// Returns whether the current architecture implements the mode's declared mechanisms.
    pub const fn supports_coupling_mode(self, mode: ExecutionCouplingMode) -> bool {
        matches!(
            mode,
            ExecutionCouplingMode::Independent
                | ExecutionCouplingMode::SequentialHandoff
                | ExecutionCouplingMode::ConcurrentCooperation
                | ExecutionCouplingMode::TightlyCoupledCooperation
        )
    }

    /// Rejects a valid MissionPlan when it requires a mechanism without an executable reducer.
    pub fn validate(self, plan: &MissionPlan) -> Result<(), OrchestrationError> {
        for context in plan.contexts() {
            if !self.supports_coupling_mode(context.coupling_mode()) {
                return Err(OrchestrationError::Mission(format!(
                    "coordination Context {} uses an unsupported coupling mode",
                    context.context_id()
                )));
            }
            for relation in context.relations() {
                if !self.supports_relation(relation.kind()) {
                    return Err(OrchestrationError::Mission(format!(
                        "execution relation {} uses {:?}, which is valid contract syntax but is not executable by this Controller build",
                        relation.relation_id(),
                        relation.kind()
                    )));
                }
            }
        }
        for task in plan.task_graph().tasks() {
            if task
                .continuity()
                .coupling_mode_override()
                .is_some_and(|mode| !self.supports_coupling_mode(mode))
            {
                return Err(OrchestrationError::Mission(format!(
                    "Task {} uses an unsupported coupling mode",
                    task.task_id()
                )));
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The profile exposes only relation reducers implemented by the current Runtime.
    #[test]
    fn current_profile_exposes_only_executable_relation_families() {
        let profile = SupportedMechanismProfile::current();
        assert!(profile.supports_relation(ExecutionRelationKind::RequiresActive));
        assert!(profile.supports_relation(ExecutionRelationKind::SharedSpatialReference));
        assert!(!profile.supports_relation(ExecutionRelationKind::RelativePose));
        assert!(!profile.supports_relation(ExecutionRelationKind::RelativeDistance));
        assert!(!profile.supports_relation(ExecutionRelationKind::GroupMemberState));
        assert!(!profile.supports_relation(ExecutionRelationKind::StateRequirement));
        assert!(!profile.supports_relation(ExecutionRelationKind::FreshnessRequirement));
    }
}
