//! Transient initial-decision ordering with no eligibility or commitment authority.

use crate::{ControlError, ControlPlane, valid_evidence_digest};
use domain::{MissionPlan, NodeId, RoleId, TaskRef, TimestampMs};
use std::collections::BTreeMap;

/// Maximum lifetime of an initial deployment observation in the local clock domain.
pub const MAX_INITIAL_PREFERENCE_AGE_MS: u64 = 600_000;

/// Bounded, attributed soft ordering for one Task's initial candidate search.
///
/// Ordinals are policy hints, not eligibility, reservations, or Actor bindings.
/// Unknown Nodes retain the ordinary fallback order within the CandidateSet.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct InitialCandidatePreferences {
    /// Exact Mission/Task namespace whose initial search may use these ordinals.
    task_ref: TaskRef,
    /// Role-local preference ordinals; lower values are explored first.
    priorities: BTreeMap<RoleId, BTreeMap<NodeId, u32>>,
    /// Immutable deployment source identity, without claiming sensor authenticity.
    source_digest: String,
    /// Local receive timestamp; not a remote observation clock.
    received_at: TimestampMs,
    /// Exclusive expiry boundary in the same local clock domain.
    expires_at: TimestampMs,
}

impl InitialCandidatePreferences {
    /// Validates bounded ordinals and a finite local validity window.
    pub fn new(
        task_ref: TaskRef,
        priorities: BTreeMap<RoleId, BTreeMap<NodeId, u32>>,
        source_digest: String,
        received_at: TimestampMs,
        expires_at: TimestampMs,
    ) -> Result<Self, ControlError> {
        let count: usize = priorities.values().map(BTreeMap::len).sum();
        if priorities.is_empty()
            || count == 0
            || count > 128
            || priorities.values().any(BTreeMap::is_empty)
            || priorities
                .values()
                .flat_map(BTreeMap::values)
                .any(|rank| *rank == u32::MAX)
            || !valid_evidence_digest(&source_digest)
            || expires_at <= received_at
            || expires_at.as_millis() - received_at.as_millis() > MAX_INITIAL_PREFERENCE_AGE_MS
        {
            return Err(ControlError::InvalidProposal(
                "initial candidate preferences have invalid scope, source, size or lifetime".into(),
            ));
        }
        Ok(Self {
            task_ref,
            priorities,
            source_digest,
            received_at,
            expires_at,
        })
    }

    /// Returns the source digest used to attribute the soft decision ordering.
    pub fn source_digest(&self) -> &str {
        &self.source_digest
    }

    /// Returns the exact Task namespace protected by this preference source.
    pub(crate) const fn task_ref(&self) -> &TaskRef {
        &self.task_ref
    }

    /// Returns an ordinal only at a receive-relative valid decision start time.
    pub(crate) fn priority(&self, role: &RoleId, node: &NodeId, at: TimestampMs) -> u32 {
        if at < self.received_at || at >= self.expires_at {
            return u32::MAX;
        }
        self.priorities
            .get(role)
            .and_then(|nodes| nodes.get(node))
            .copied()
            .unwrap_or(u32::MAX)
    }
}

impl ControlPlane {
    /// Installs optional initial search evidence without modifying any authority.
    ///
    /// The exact accepted-plan Task/Role scope is validated before mutation.
    /// A first successful Bind fences all of this Mission's preferences; restore
    /// and recovery never reacquire them. Replacing installed evidence is rejected.
    pub fn set_initial_candidate_preferences(
        &mut self,
        plan: &MissionPlan,
        preferences: InitialCandidatePreferences,
    ) -> Result<(), ControlError> {
        let task = plan
            .task_graph()
            .tasks()
            .iter()
            .find(|task| task.requirement().task_ref() == &preferences.task_ref)
            .ok_or_else(|| {
                ControlError::InvalidProposal("initial preferences reference another plan".into())
            })?;
        if self
            .actor_bindings
            .keys()
            .any(|(mission, _)| mission == plan.goal().mission_id())
            || self.groups.values().any(|group| {
                group.mission_id() == plan.goal().mission_id()
                    && group.task_executions().any(|execution| {
                        !matches!(
                            execution.lifecycle(),
                            domain::TaskExecutionLifecycle::Pending
                                | domain::TaskExecutionLifecycle::Ready
                        )
                    })
            })
            || preferences.priorities.keys().any(|role| {
                !task
                    .requirement()
                    .roles()
                    .iter()
                    .any(|required| required.role_id() == role)
            })
        {
            return Err(ControlError::InvalidProposal(
                "initial preferences require an unbound exact Task/Role scope".into(),
            ));
        }
        if let Some(existing) = self
            .initial_candidate_preferences
            .get(&preferences.task_ref)
        {
            if existing == &preferences {
                return Ok(());
            }
            return Err(ControlError::InvalidProposal(
                "initial preference evidence is immutable".into(),
            ));
        }
        self.initial_candidate_preferences
            .insert(preferences.task_ref.clone(), preferences);
        Ok(())
    }
}
