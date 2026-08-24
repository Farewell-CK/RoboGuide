//! Stateless deterministic bootstrap policy for Control-owned node selection.
//!
//! The policy consumes Candidate Sets produced by Capability Matching and
//! returns selection evidence. It does not re-evaluate eligibility, validate
//! proposals, inspect reservations, commit resources, or mutate Groups/State.

use super::{CandidateSet, RecoveryCandidateSet};
use domain::{
    CorrelationId, EventPayload, ExecutionGroupId, NodeId, ResourceId, RoleAssignment, RoleId,
    RoleRequirement, TaskRef, TaskRequirement, TimestampMs,
};
use ports::{EventSink, SharedNodeStateReader};
use std::collections::BTreeSet;
use std::fmt::{Display, Formatter};

/// Failures produced while forming a deterministic scheduling decision.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SchedulerError {
    /// Candidate context did not match the supplied task or role requirements.
    InvalidCandidateSet(String),
    /// A Candidate Set referenced a node absent from Shared Node State.
    UnknownCandidate(NodeId),
    /// No candidate could provide an unused declared resource for one role.
    NoFeasibleSelection(RoleId),
}

impl Display for SchedulerError {
    /// Formats a scheduler boundary failure without claiming recovery exhaustion.
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::InvalidCandidateSet(reason) => {
                write!(formatter, "invalid scheduler candidate set: {reason}")
            }
            Self::UnknownCandidate(node_id) => {
                write!(
                    formatter,
                    "scheduler candidate {node_id} is absent from Shared State"
                )
            }
            Self::NoFeasibleSelection(role_id) => {
                write!(
                    formatter,
                    "no feasible deterministic selection for role {role_id}"
                )
            }
        }
    }
}

impl std::error::Error for SchedulerError {}

/// One Scheduler-selected role, node, and non-authoritative resource suggestion.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RoleSchedulingSelection {
    /// Role receiving the selection.
    role_id: RoleId,
    /// Candidate node selected by the bootstrap policy.
    node_id: NodeId,
    /// Declared resources suggested for later proposal validation.
    resource_ids: Vec<ResourceId>,
}

impl RoleSchedulingSelection {
    /// Creates one internal role selection from the shared policy primitive.
    fn new(role_id: RoleId, node_id: NodeId, resource_ids: Vec<ResourceId>) -> Self {
        Self {
            role_id,
            node_id,
            resource_ids,
        }
    }

    /// Returns the selected role.
    pub const fn role_id(&self) -> &RoleId {
        &self.role_id
    }

    /// Returns the selected candidate node.
    pub const fn node_id(&self) -> &NodeId {
        &self.node_id
    }

    /// Returns non-authoritative proposed resource IDs.
    pub fn resource_ids(&self) -> &[ResourceId] {
        &self.resource_ids
    }

    /// Converts selection evidence into the existing proposal-validation input type.
    fn to_role_assignment(&self) -> RoleAssignment {
        RoleAssignment::new(
            self.role_id.clone(),
            self.node_id.clone(),
            self.resource_ids.clone(),
        )
    }
}

/// Complete normal-task selection evidence produced before proposal validation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TaskSchedulingDecision {
    /// Mission-scoped task represented by the decision.
    task_ref: TaskRef,
    /// Role selections in TaskRequirement declaration order.
    selections: Vec<RoleSchedulingSelection>,
}

impl TaskSchedulingDecision {
    /// Creates a complete normal-task decision from deterministic role selections.
    fn new(task_ref: TaskRef, selections: Vec<RoleSchedulingSelection>) -> Self {
        Self {
            task_ref,
            selections,
        }
    }

    /// Returns the mission-scoped task represented by this decision.
    pub const fn task_ref(&self) -> &TaskRef {
        &self.task_ref
    }

    /// Returns role selections in requirement declaration order.
    pub fn selections(&self) -> &[RoleSchedulingSelection] {
        &self.selections
    }

    /// Builds fresh proposal-validation inputs without granting proposal authority.
    pub fn proposed_assignments(&self) -> Vec<RoleAssignment> {
        self.selections
            .iter()
            .map(RoleSchedulingSelection::to_role_assignment)
            .collect()
    }
}

/// Role-scoped recovery selection evidence produced before recovery proposal validation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RecoverySchedulingDecision {
    /// Existing Group awaiting recovery.
    group_id: ExecutionGroupId,
    /// Mission-scoped task retained by the Group.
    task_ref: TaskRef,
    /// Single unbound role selected in this decision.
    role_id: RoleId,
    /// Failed node excluded by Recovery Matching.
    previous_node_id: NodeId,
    /// Candidate replacement selected by the bootstrap policy.
    replacement_node_id: NodeId,
    /// Non-authoritative resources suggested for proposal validation.
    resource_ids: Vec<ResourceId>,
}

impl RecoverySchedulingDecision {
    /// Creates one recovery decision from the shared role-selection primitive.
    fn new(
        group_id: ExecutionGroupId,
        task_ref: TaskRef,
        role_id: RoleId,
        previous_node_id: NodeId,
        replacement_node_id: NodeId,
        resource_ids: Vec<ResourceId>,
    ) -> Self {
        Self {
            group_id,
            task_ref,
            role_id,
            previous_node_id,
            replacement_node_id,
            resource_ids,
        }
    }

    /// Returns the existing Group awaiting the replacement.
    pub const fn group_id(&self) -> &ExecutionGroupId {
        &self.group_id
    }

    /// Returns the mission-scoped task retained by the Group.
    pub const fn task_ref(&self) -> &TaskRef {
        &self.task_ref
    }

    /// Returns the single recovery role represented by this decision.
    pub const fn role_id(&self) -> &RoleId {
        &self.role_id
    }

    /// Returns the failed node excluded from selection.
    pub const fn previous_node_id(&self) -> &NodeId {
        &self.previous_node_id
    }

    /// Returns the Scheduler-selected replacement node.
    pub const fn replacement_node_id(&self) -> &NodeId {
        &self.replacement_node_id
    }

    /// Returns non-authoritative proposed replacement resources.
    pub fn resource_ids(&self) -> &[ResourceId] {
        &self.resource_ids
    }
}

/// Recovery scheduling result that preserves empty candidates as non-terminal pending work.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RecoverySchedulingOutcome {
    /// No supplied candidate can currently form a deterministic selection.
    NoSelection,
    /// The bootstrap policy selected one recovery candidate and resources.
    Selected(RecoverySchedulingDecision),
}

/// Stateless stable-first Scheduler used to establish the Control selection boundary.
#[derive(Debug, Clone, Copy, Default)]
pub struct DeterministicBootstrapScheduler;

impl DeterministicBootstrapScheduler {
    /// Creates the stateless deterministic bootstrap policy.
    pub const fn new() -> Self {
        Self
    }

    /// Selects every normal task role without validating or committing a proposal.
    pub fn schedule_task<S: SharedNodeStateReader, E: EventSink>(
        &self,
        state: &S,
        requirement: &TaskRequirement,
        candidates: &CandidateSet,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<TaskSchedulingDecision, SchedulerError> {
        if candidates.task_ref() != requirement.task_ref() {
            return Err(SchedulerError::InvalidCandidateSet(
                "normal candidates belong to another task".to_string(),
            ));
        }
        let mut selected_resources = BTreeSet::new();
        let mut selections = Vec::with_capacity(requirement.roles().len());
        for role in requirement.roles() {
            let role_candidates = candidates.for_role(role.role_id()).ok_or_else(|| {
                SchedulerError::InvalidCandidateSet(format!(
                    "normal candidates omit role {}",
                    role.role_id()
                ))
            })?;
            let selection =
                select_role(state, role, role_candidates.node_ids(), &selected_resources)?
                    .ok_or_else(|| SchedulerError::NoFeasibleSelection(role.role_id().clone()))?;
            selected_resources.extend(selection.resource_ids().iter().cloned());
            selections.push(selection);
        }
        let decision = TaskSchedulingDecision::new(requirement.task_ref().clone(), selections);
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::TaskSchedulingSelected {
                task_ref: requirement.task_ref().clone(),
                assignments: decision.proposed_assignments(),
            },
        );
        Ok(decision)
    }

    /// Selects only the role represented by a Recovery Candidate Set.
    pub fn schedule_recovery<S: SharedNodeStateReader, E: EventSink>(
        &self,
        state: &S,
        requirement: &TaskRequirement,
        candidates: &RecoveryCandidateSet,
        timestamp: TimestampMs,
        correlation_id: &CorrelationId,
        events: &mut E,
    ) -> Result<RecoverySchedulingOutcome, SchedulerError> {
        if candidates.task_ref() != requirement.task_ref() {
            return Err(SchedulerError::InvalidCandidateSet(
                "recovery candidates belong to another task".to_string(),
            ));
        }
        let role = requirement
            .roles()
            .iter()
            .find(|role| role.role_id() == candidates.role_id())
            .ok_or_else(|| {
                SchedulerError::InvalidCandidateSet(format!(
                    "task requirement omits recovery role {}",
                    candidates.role_id()
                ))
            })?;
        let selection = select_role(
            state,
            role,
            candidates.candidate_node_ids(),
            &BTreeSet::new(),
        )?;
        let Some(selection) = selection else {
            events.append(
                timestamp,
                correlation_id,
                None,
                EventPayload::RecoverySchedulingNoSelection {
                    group_id: candidates.group_id().clone(),
                    task_ref: candidates.task_ref().clone(),
                    role_id: candidates.role_id().clone(),
                },
            );
            return Ok(RecoverySchedulingOutcome::NoSelection);
        };
        let decision = RecoverySchedulingDecision::new(
            candidates.group_id().clone(),
            candidates.task_ref().clone(),
            candidates.role_id().clone(),
            candidates.previous_node_id().clone(),
            selection.node_id().clone(),
            selection.resource_ids().to_vec(),
        );
        events.append(
            timestamp,
            correlation_id,
            None,
            EventPayload::RecoverySchedulingSelected {
                group_id: candidates.group_id().clone(),
                task_ref: candidates.task_ref().clone(),
                role_id: candidates.role_id().clone(),
                previous_node_id: candidates.previous_node_id().clone(),
                replacement_node_id: decision.replacement_node_id().clone(),
                resource_ids: decision.resource_ids().to_vec(),
            },
        );
        Ok(RecoverySchedulingOutcome::Selected(decision))
    }
}

/// Applies the shared stable-first node/resource policy to one role and Candidate Set.
fn select_role<S: SharedNodeStateReader>(
    state: &S,
    role: &RoleRequirement,
    candidate_node_ids: &[NodeId],
    selected_resources: &BTreeSet<ResourceId>,
) -> Result<Option<RoleSchedulingSelection>, SchedulerError> {
    let mut stable_candidates = candidate_node_ids.to_vec();
    stable_candidates.sort();
    stable_candidates.dedup();
    for node_id in stable_candidates {
        let node = state
            .node(&node_id)
            .ok_or_else(|| SchedulerError::UnknownCandidate(node_id.clone()))?;
        let resource_ids = if let Some(resource_kind) = role.resource_kind() {
            let mut declared = node.registration().resource_ids_of_kind(resource_kind);
            declared.sort();
            let Some(resource_id) = declared
                .into_iter()
                .find(|resource_id| !selected_resources.contains(resource_id))
            else {
                continue;
            };
            vec![resource_id]
        } else {
            vec![]
        };
        return Ok(Some(RoleSchedulingSelection::new(
            role.role_id().clone(),
            node_id,
            resource_ids,
        )));
    }
    Ok(None)
}

#[cfg(test)]
#[path = "scheduler_tests.rs"]
mod tests;
