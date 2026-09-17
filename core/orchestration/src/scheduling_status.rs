//! Typed scheduling policy at the boundary between Core decisions and application drivers.

use crate::OrchestrationError;
use control::ControlError;

/// Durable reason that a Ready Task has not obtained a dispatchable assignment.
#[derive(Debug, Clone, Copy, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum SchedulingDeferral {
    /// No currently eligible provider satisfies one Role.
    NoCandidate,
    /// Deployment placement currently has no eligible realization.
    ActorPlacementUnavailable,
    /// Admitted grounding has no current deployment registry realization.
    ActorGroundingUnresolved,
    /// Current deployment cannot supply the Context's distinct physical executors.
    DistinctEntitiesUnavailable,
    /// An existing Actor binding needs a Control reconciliation decision.
    ReconciliationRequired,
    /// A selected resource is currently owned by another commitment.
    ResourceConflict,
    /// No joint node/resource/time selection is currently feasible.
    NoFeasibleInterval,
    /// The bounded search exhausted its budget without a complete selection.
    SearchLimited,
    /// The supplied timing cannot currently be represented by the Scheduler.
    InvalidTimeWindow,
    /// The immutable activation window has elapsed; automatic retries must stop.
    WindowMissed,
    /// A future selection no longer validates against current eligibility.
    ActivationRevalidation,
    /// A future selection conflicts at commitment time.
    ActivationConflict,
}

impl SchedulingDeferral {
    /// Returns the stable evidence/checkpoint spelling, never inferred from an error message.
    pub const fn as_str(self) -> &'static str {
        match self {
            Self::NoCandidate => "no-candidate",
            Self::ActorPlacementUnavailable => "actor-placement-unavailable",
            Self::ActorGroundingUnresolved => "actor-grounding-unresolved",
            Self::DistinctEntitiesUnavailable => "distinct-entities-unavailable",
            Self::ReconciliationRequired => "reconciliation-required",
            Self::ResourceConflict => "resource-conflict",
            Self::NoFeasibleInterval => "no-feasible-interval",
            Self::SearchLimited => "search-limited",
            Self::InvalidTimeWindow => "invalid-time-window",
            Self::WindowMissed => "window-missed",
            Self::ActivationRevalidation => "activation-revalidation",
            Self::ActivationConflict => "activation-conflict",
        }
    }

    /// Reports whether later deployment/evidence changes may justify automatic retry.
    pub const fn allows_retry(self) -> bool {
        !matches!(self, Self::WindowMissed)
    }
}

/// Application handling policy for one failed scheduling/preparation operation.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SchedulingDisposition {
    /// Keep the Task Ready and record the explicit scheduling reason.
    Deferred(SchedulingDeferral),
    /// Preserve the Actor binding while Control decides how to reconcile it.
    ReconciliationRequired,
    /// Reject a permanently invalid input rather than waiting for deployment changes.
    InvalidContract,
    /// Surface an inconsistent accepted execution or infrastructure failure.
    InternalFailure,
}

impl SchedulingDisposition {
    /// Returns observable waiting evidence only for nonfatal scheduling conditions.
    pub const fn deferral(self) -> Option<SchedulingDeferral> {
        match self {
            Self::Deferred(reason) => Some(reason),
            Self::ReconciliationRequired => Some(SchedulingDeferral::ReconciliationRequired),
            Self::InvalidContract | Self::InternalFailure => None,
        }
    }
}

impl OrchestrationError {
    /// Classifies preparation failures without parsing diagnostics or swallowing internal errors.
    pub const fn scheduling_disposition(&self) -> SchedulingDisposition {
        use SchedulingDeferral as Reason;
        use SchedulingDisposition as Disposition;
        match self {
            Self::SchedulingDeferred(Reason::ReconciliationRequired) => {
                Disposition::ReconciliationRequired
            }
            Self::SchedulingDeferred(reason) => Disposition::Deferred(*reason),
            Self::InvalidContract(_) => Disposition::InvalidContract,
            Self::Mission(_) => Disposition::InternalFailure,
            Self::Control(error) => match error {
                ControlError::NoCandidate(_) => Disposition::Deferred(Reason::NoCandidate),
                ControlError::AssignmentUnavailable(_) => {
                    Disposition::Deferred(Reason::ActivationRevalidation)
                }
                ControlError::ActorPlacementConstraintUnsatisfied { .. } => {
                    Disposition::Deferred(Reason::ActorPlacementUnavailable)
                }
                ControlError::ActorGroundingUnresolved { .. } => {
                    Disposition::Deferred(Reason::ActorGroundingUnresolved)
                }
                ControlError::DistinctBindingUnsatisfiable { .. } => {
                    Disposition::Deferred(Reason::DistinctEntitiesUnavailable)
                }
                ControlError::ActorBindingRequiresReconciliation { .. } => {
                    Disposition::ReconciliationRequired
                }
                ControlError::ResourceConflict { .. } => {
                    Disposition::Deferred(Reason::ResourceConflict)
                }
                ControlError::InvalidProposal(_)
                | ControlError::InvalidLease(_)
                | ControlError::UnknownNode(_)
                | ControlError::UnknownGroup(_)
                | ControlError::AllocationInvariant(_)
                | ControlError::PendingRecoveryCommitmentExists { .. }
                | ControlError::PendingRecoveryCommitmentNotFound { .. }
                | ControlError::PendingRecoveryCommitmentMismatch { .. }
                | ControlError::InvalidLifecycle(_)
                | ControlError::SharedState(_)
                | ControlError::UnknownLease { .. }
                | ControlError::LeaseExpired { .. } => Disposition::InternalFailure,
            },
        }
    }
}
