"""Zero-Provider regressions for the bounded MI deployment reconsideration loop."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from mission.approval import ApprovalPolicy
from mission.capability_catalog import CanonicalCapabilityCatalog
from mission.controller import SubmissionReceipt
from mission.deployment_assessment import (
    DIAGNOSTIC_SCHEMA,
    CandidateDiagnostics,
    InitialOperationAssessment,
)
from mission.deployment_recovery import (
    DeploymentRecoveryAction,
    DeploymentRecoveryDecision,
    DeploymentRecoverySession,
)
from mission.grounding_context import GroundingContextSnapshot
from mission.intent import GroundedIntent
from mission.models import JSONObject, MissionPlan
from mission.provider_errors import MissionProviderError
from mission.requests import (
    MissionRequestEngine,
    MissionRequestError,
    MissionRequestLifecycle,
    MissionRequestStore,
)
from mission.review import MissionPlanReview, MissionReviewIssue, ReviewIssueAction
from mission.submission_evidence import canonical_plan_digest
from test_deployment_assessment import AssessingController
from test_requests import (
    FakeInterpreter,
    FakePlanner,
    SequenceClock,
    SequenceIds,
    _assessment,
    _catalog,
    _fixture_contracts,
)


class CurrentAssessmentController(AssessingController):
    """Return exact-plan v0.2 observations without an inventory read or hidden submission."""

    def assess_initial_support(self, plan: MissionPlan) -> InitialOperationAssessment:
        """Upgrade scripted replies with disjoint, bounded query-time candidate counts."""
        value = super().assess_initial_support(plan)
        return replace(
            value,
            schema_version=DIAGNOSTIC_SCHEMA,
            candidate_diagnostics=tuple(
                CandidateDiagnostics(role.task_id, role.role_id, 2, 2, ()) for role in value.roles
            ),
        )


class Reviewer:
    """Observe exact revisions independently of the reconsideration decision."""

    def __init__(self, second_action: ReviewIssueAction | None = None) -> None:
        """Retain reviewed plans, contexts and an optional grounded blocking outcome."""
        self.plans: list[MissionPlan] = []
        self.contexts: list[GroundingContextSnapshot] = []
        self.second_action = second_action

    def review(
        self,
        intent: GroundedIntent,
        plan: MissionPlan,
        catalog: CanonicalCapabilityCatalog,
        context: GroundingContextSnapshot,
    ) -> MissionPlanReview:
        """Reject a proposed replacement only when the fixture supplies a semantic finding."""
        self.plans.append(plan)
        self.contexts.append(context)
        if len(self.plans) > 1 and self.second_action:
            return MissionPlanReview(
                False,
                (
                    MissionReviewIssue(
                        "goal.not_preserved",
                        "/tasks/0",
                        "The confirmed effect must remain.",
                        self.second_action,
                    ),
                ),
            )
        return MissionPlanReview(True, ())


class ReasoningRepairer:
    """Script agent proposals; the engine, not the model, must enforce authority boundaries."""

    def __init__(self, actions: list[DeploymentRecoveryAction | BaseException]) -> None:
        """Freeze a finite model-call queue and the exact feedback/grounding trace."""
        self.actions = actions
        self.calls: list[
            tuple[
                MissionPlan,
                InitialOperationAssessment,
                GroundingContextSnapshot,
                DeploymentRecoverySession,
            ]
        ] = []

    def repair(
        self,
        mission_id: str,
        intent: GroundedIntent,
        plan: MissionPlan,
        review: MissionPlanReview,
        catalog: CanonicalCapabilityCatalog,
        context: GroundingContextSnapshot,
    ) -> MissionPlan:
        """Disallow an unrelated semantic-repair call in these deployment-loop fixtures."""
        raise AssertionError("ordinary repair was not authorized by this fixture")

    def reconsider_deployment(
        self,
        mission_id: str,
        intent: GroundedIntent,
        plan: MissionPlan,
        assessment: InitialOperationAssessment,
        catalog: CanonicalCapabilityCatalog,
        context: GroundingContextSnapshot,
        session: DeploymentRecoverySession,
    ) -> tuple[DeploymentRecoveryDecision, JSONObject]:
        """Return a proposed decision with no executor choice, physical action or goal mutation."""
        self.calls.append((plan, assessment, context, session))
        action = self.actions.pop(0)
        if isinstance(action, BaseException):
            raise action
        replacement = None
        if action is DeploymentRecoveryAction.REVISE:
            raw = plan.to_json()
            tasks = cast(list[JSONObject], raw["tasks"])
            tasks[0]["description"] = f"Grounded delivery description revision {len(self.calls)}"
            replacement = MissionPlan.from_json(raw)
        decision = DeploymentRecoveryDecision(
            action, "Preserve the requirement and check the supplied evidence.", replacement
        )
        return decision, decision.to_json()


def engine_for(
    tmp_path: Path,
    controller: AssessingController,
    model: ReasoningRepairer,
    reviewer: Reviewer | None = None,
    *,
    attempts: int = 2,
    timeout: int = 900_000,
    clock: SequenceClock | None = None,
) -> MissionRequestEngine:
    """Compose real durable orchestration with finite, zero-network ports and one request."""
    return MissionRequestEngine(
        MissionRequestStore(tmp_path / "requests.sqlite3"),
        FakeInterpreter([_assessment()]),
        FakePlanner(),
        controller,
        _catalog(),
        frozenset(),
        SequenceIds(),
        clock or SequenceClock(),
        reviewer=reviewer or Reviewer(),
        repairer=model,
        controller_preflight_enabled=True,
        max_deployment_recovery_attempts=attempts,
        deployment_recovery_timeout_ms=timeout,
    )


def test_agent_recheck_keeps_same_reviewed_draft_and_posts_once(tmp_path: Path) -> None:
    """Transient reconsideration does not regenerate the plan or acquire executor authority."""
    controller = CurrentAssessmentController(["blocked", "not_blocked"])
    model, reviewer = ReasoningRepairer([DeploymentRecoveryAction.RECHECK]), Reviewer()
    engine = engine_for(tmp_path, controller, model, reviewer)
    record = engine.create("deliver the declared payload")
    assert record.lifecycle is MissionRequestLifecycle.ACCEPTED
    assert len(model.calls) == len(reviewer.plans) == len(controller.submissions) == 1
    assert len(controller.assessments) == 2 and controller.inventory_calls == 0
    assert controller.assessments[0] == controller.assessments[1]
    assert (
        record.deployment_recovery is not None
        and record.deployment_recovery.attempts[0].outcome == "decided"
    )
    assert engine.get(record.request_id) == record
    assert record.to_json()["schema_version"] == "roboguide.mission-request/v0.4"
    assert record.observations().schema_version == "roboguide.mission-request-observations/v0.4"
    assert (
        record.observations().to_json()["schema_version"]
        == "roboguide.mission-request-observations/v0.4"
    )


def test_unchanged_negative_feedback_stops_without_spinning(tmp_path: Path) -> None:
    """A recheck with unchanged evidence spends only one model call and never POSTs."""
    controller = CurrentAssessmentController(["blocked", "blocked", "blocked"])
    model = ReasoningRepairer([DeploymentRecoveryAction.RECHECK])
    engine = engine_for(tmp_path, controller, model, attempts=3)
    record = engine.create("deliver the declared payload")
    assert record.lifecycle is MissionRequestLifecycle.BLOCKED
    retried = engine.retry(record.request_id)
    assert retried.lifecycle is MissionRequestLifecycle.BLOCKED and len(model.calls) == 1
    assert not controller.submissions and "unchanged" in retried.issues[-1]


def test_recheck_cannot_replace_frozen_source_identity(tmp_path: Path) -> None:
    """A new reset/profile digest cannot renew an existing reasoning session's source fence."""

    class ChangingSourceController(CurrentAssessmentController):
        """Expose a different source on the second query without pretending it is the old world."""

        def assess_initial_support(self, plan: MissionPlan) -> InitialOperationAssessment:
            """Return attributed replies with an explicit source identity change."""
            value = super().assess_initial_support(plan)
            return (
                replace(value, source_digest="sha256:" + "d" * 64)
                if len(self.assessments) > 1
                else value
            )

    controller = ChangingSourceController(["blocked", "blocked"])
    model = ReasoningRepairer([DeploymentRecoveryAction.RECHECK])
    record = engine_for(tmp_path, controller, model).create("deliver the declared payload")
    assert record.lifecycle is MissionRequestLifecycle.BLOCKED
    assert len(model.calls) == 1 and not controller.submissions
    assert "source changed" in record.issues[-1]


def test_replacement_is_rereviewed_against_same_context_before_control(tmp_path: Path) -> None:
    """An old approval cannot authorize the new digest; both revisions remain inspectable."""
    controller = CurrentAssessmentController(["blocked", "not_blocked"])
    model, reviewer = ReasoningRepairer([DeploymentRecoveryAction.REVISE]), Reviewer()
    engine = engine_for(tmp_path, controller, model, reviewer)
    record = engine.create("deliver the declared payload")
    assert record.lifecycle is MissionRequestLifecycle.ACCEPTED and record.draft_revision == 2
    assert len(reviewer.plans) == len(record.review_history) == 2
    assert reviewer.contexts[0] == reviewer.contexts[1] == record.grounding_context
    assert record.review_history[0].draft_digest != record.review_history[1].draft_digest
    assert record.review_history[-1].draft_digest == record.draft_digest
    assert controller.submissions == [reviewer.plans[-1]]
    assert record.deployment_recovery is not None
    attempt = record.deployment_recovery.attempts[0]
    assert json.loads(attempt.input_plan_json) == reviewer.plans[0].to_json()
    assert attempt.provider_output_json is not None
    assert attempt.input_plan_digest == record.review_history[0].draft_digest
    assert engine.get(record.request_id).deployment_recovery == record.deployment_recovery


@pytest.mark.parametrize(
    "action", [ReviewIssueAction.REJECT_DRAFT, ReviewIssueAction.REQUEST_CLARIFICATION]
)
def test_reviewer_retains_semantic_veto_after_reorganization(
    tmp_path: Path, action: ReviewIssueAction
) -> None:
    """A model's repair decision never downgrades genuine semantic or evidence requirements."""
    controller = CurrentAssessmentController(["blocked"])
    model = ReasoningRepairer([DeploymentRecoveryAction.REVISE])
    record = engine_for(tmp_path, controller, model, Reviewer(action)).create(
        "deliver the declared payload"
    )
    assert record.lifecycle is (
        MissionRequestLifecycle.FAILED
        if action is ReviewIssueAction.REJECT_DRAFT
        else MissionRequestLifecycle.NEEDS_CLARIFICATION
    )
    assert not controller.submissions and len(controller.assessments) == 1
    assert record.review_history[-1].review.issues[0].code == "goal.not_preserved"


def test_real_evidence_gap_is_held_and_not_fabricated(tmp_path: Path) -> None:
    """Waiting preserves the objective, resources, all tasks and the physical evidence gap."""
    controller = CurrentAssessmentController(["blocked"])
    model = ReasoningRepairer([DeploymentRecoveryAction.WAIT])
    record = engine_for(tmp_path, controller, model).create("deliver the declared payload")
    assert record.lifecycle is MissionRequestLifecycle.BLOCKED and record.draft_revision == 1
    assert record.plan == model.calls[0][0] and not controller.submissions
    assert record.deployment_recovery is not None
    assert record.deployment_recovery.attempts[0].decision is not None
    assert record.deployment_recovery.attempts[0].decision.replacement_plan is None


@pytest.mark.parametrize("fault", ["transport", "unavailable", "legacy"])
def test_untrustworthy_or_expired_feedback_never_calls_model(tmp_path: Path, fault: str) -> None:
    """Missing source/diagnostics cannot be promoted to an opportunity for semantic rewriting."""
    controller = (
        AssessingController(["blocked"])
        if fault == "legacy"
        else CurrentAssessmentController([fault])
    )
    model = ReasoningRepairer([])
    record = engine_for(tmp_path, controller, model).create("deliver the declared payload")
    assert record.lifecycle is MissionRequestLifecycle.BLOCKED
    assert not model.calls and record.deployment_recovery is None and not controller.submissions


def test_provider_failure_does_not_restart_deliberation_or_retry_automatically(
    tmp_path: Path,
) -> None:
    """The original provider fault and deployment hold remain independent evidence."""
    controller = CurrentAssessmentController(["blocked", "blocked"])
    model = ReasoningRepairer(
        [MissionProviderError("transport interrupted", transport_failure=True)]
    )
    engine = engine_for(tmp_path, controller, model)
    record = engine.create("deliver the declared payload")
    assert record.lifecycle is MissionRequestLifecycle.BLOCKED
    assert record.failure_evidence is not None and record.failure_evidence["stage"] == "repairer"
    assert record.failure_evidence["failure_owner"] == "SUT_SYSTEM"
    assert (
        record.deployment_recovery is not None
        and record.deployment_recovery.attempts[0].outcome == "failed"
    )
    assert engine.retry(record.request_id).lifecycle is MissionRequestLifecycle.BLOCKED
    assert len(model.calls) == 1 and not controller.submissions


def test_crash_consumes_original_budget_and_restores_without_model_effects(tmp_path: Path) -> None:
    """Restart retains the interrupted call and original budget without inventing a response."""
    controller = CurrentAssessmentController(["blocked", "blocked"])
    model, clock = ReasoningRepairer([KeyboardInterrupt("simulated termination")]), SequenceClock()
    engine = engine_for(tmp_path, controller, model, attempts=1, clock=clock)
    with pytest.raises(KeyboardInterrupt):
        engine.create("deliver the declared payload")
    interrupted = engine._store.records()[0]
    assert (
        interrupted.deployment_recovery is not None
        and interrupted.deployment_recovery.attempts[-1].outcome == "pending"
    )
    restored = engine_for(tmp_path, controller, model, attempts=3, clock=clock)
    record = restored.get(interrupted.request_id)
    assert record.lifecycle is MissionRequestLifecycle.BLOCKED
    assert record.deployment_recovery is not None and record.deployment_recovery.max_attempts == 1
    assert record.deployment_recovery.attempts[-1].outcome == "interrupted"
    assert restored.retry(record.request_id).lifecycle is MissionRequestLifecycle.BLOCKED
    assert len(model.calls) == 1 and not controller.submissions


def test_revisions_cannot_renew_count_budget_across_retry(tmp_path: Path) -> None:
    """Several valid new digests still share the same request-wide count limit."""
    controller = CurrentAssessmentController(["blocked"] * 4)
    model = ReasoningRepairer([DeploymentRecoveryAction.REVISE] * 2)
    engine = engine_for(tmp_path, controller, model, attempts=2)
    record = engine.create("deliver the declared payload")
    assert record.lifecycle is MissionRequestLifecycle.BLOCKED and record.draft_revision == 3
    assert len(model.calls) == 2 and len(record.review_history) == 3
    before = record.deployment_recovery
    engine._max_deployment_recovery_attempts = 3
    retried = engine.retry(record.request_id)
    assert retried.deployment_recovery == before and not controller.submissions


def test_expired_model_result_is_recorded_but_not_applied(tmp_path: Path) -> None:
    """A result arriving after the frozen deadline cannot create or submit another draft."""
    controller = CurrentAssessmentController(["blocked"])
    model, clock = ReasoningRepairer([DeploymentRecoveryAction.REVISE]), SequenceClock()
    record = engine_for(tmp_path, controller, model, timeout=1, clock=clock).create(
        "deliver the declared payload"
    )
    assert record.lifecycle is MissionRequestLifecycle.BLOCKED and record.draft_revision == 1
    assert (
        record.deployment_recovery is not None
        and record.deployment_recovery.attempts[0].outcome == "expired"
    )
    assert record.deployment_recovery.attempts[0].decision is not None
    assert len(record.review_history) == 1 and not controller.submissions


def test_ambiguous_post_never_enters_agent_repair_or_resubmits(tmp_path: Path) -> None:
    """Once a POST might be accepted, only existing admission reconciliation is allowed."""
    controller = CurrentAssessmentController(["blocked", "not_blocked"])
    controller.receipts = [SubmissionReceipt(False, 502, "upstream interruption")]
    model = ReasoningRepairer([DeploymentRecoveryAction.RECHECK])
    engine = engine_for(tmp_path, controller, model)
    record = engine.create("deliver the declared payload")
    assert record.lifecycle is MissionRequestLifecycle.BLOCKED
    with pytest.raises(MissionRequestError, match="read-only Controller observer"):
        engine.retry(record.request_id)
    assert len(controller.submissions) == len(model.calls) == 1


def test_recovery_evidence_integrity_and_defensive_copy(tmp_path: Path) -> None:
    """Detached feedback, mutable snapshots and extra fields cannot renew durable authority."""
    record = engine_for(
        tmp_path,
        CurrentAssessmentController(["blocked"]),
        ReasoningRepairer([DeploymentRecoveryAction.WAIT]),
    ).create("deliver the declared payload")
    assert record.deployment_recovery is not None
    session = record.deployment_recovery
    extra = session.to_json()
    extra_attempts = cast(list[JSONObject], extra["attempts"])
    cast(JSONObject, extra_attempts[0]["input_plan"])["unknown_input"] = "not reviewed"
    with pytest.raises(ValueError):
        DeploymentRecoverySession.from_json(extra)
    raw = session.to_json()
    assert DeploymentRecoverySession.from_json(raw) == session
    attempts = cast(list[JSONObject], raw["attempts"])
    cast(JSONObject, attempts[0]["input_plan"])["mission"] = {}
    assert session.to_json() != raw
    with pytest.raises(ValueError):
        DeploymentRecoverySession.from_json(raw)
    with pytest.raises(ValueError, match="count bound"):
        replace(session, max_attempts=0)
    with pytest.raises(MissionRequestError, match="another request"):
        replace(record, deployment_recovery=replace(session, request_id="foreign-request"))
    assert record.plan is not None
    assert canonical_plan_digest(record.plan.to_json()) == session.attempts[0].input_plan_digest


def test_invalid_recovery_configuration_fails_before_effects(tmp_path: Path) -> None:
    """Opt-in requires independent Review and preflight instead of a hidden fallback."""
    with pytest.raises(MissionRequestError, match="requires preflight"):
        MissionRequestEngine(
            MissionRequestStore(tmp_path / "requests.sqlite3"),
            FakeInterpreter([]),
            FakePlanner(),
            CurrentAssessmentController([]),
            _catalog(),
            frozenset(),
            max_deployment_recovery_attempts=1,
        )


def test_revised_risk_gated_plan_requires_a_new_revision_bound_approval(tmp_path: Path) -> None:
    """The user's approval of the old digest cannot authorize a model's new draft."""
    controller = CurrentAssessmentController(["blocked"])
    model = ReasoningRepairer([DeploymentRecoveryAction.REVISE])
    engine = engine_for(tmp_path, controller, model)
    engine._approval_policy = ApprovalPolicy.from_contracts(frozenset(_fixture_contracts()))
    first = engine.create("deliver the declared payload")
    assert first.lifecycle is MissionRequestLifecycle.AWAITING_APPROVAL
    assert first.draft_digest is not None
    revised = engine.approve(first.request_id, first.draft_revision, first.draft_digest)
    assert revised.lifecycle is MissionRequestLifecycle.AWAITING_APPROVAL
    assert revised.draft_revision == 2 and revised.draft_digest != first.draft_digest
    assert not controller.submissions and len(revised.review_history) == 2
    with pytest.raises(MissionRequestError, match="stale"):
        engine.approve(first.request_id, first.draft_revision, first.draft_digest)
