"""Deterministic tests for structured Mission Review evidence and routing."""

from __future__ import annotations

import pytest
from mission.review import (
    MissionPlanReview,
    MissionPlanReviewAttempt,
    MissionReviewError,
    MissionReviewIssue,
    MissionReviewRoute,
    ReviewIssueAction,
    route_mission_review,
)


def _issue(action: ReviewIssueAction) -> MissionReviewIssue:
    """Build one canonical blocking issue for review-policy tests."""
    return MissionReviewIssue(
        code="task.boundary",
        path="/tasks/0",
        message="Task decomposition exposes Local How.",
        required_action=action,
    )


def test_review_evidence_round_trips_with_exact_draft_identity() -> None:
    """Structured findings remain bound to the revision and digest that was examined."""
    attempt = MissionPlanReviewAttempt(
        draft_revision=2,
        draft_digest="sha256:" + "a" * 64,
        review=MissionPlanReview(
            approved=False,
            issues=(_issue(ReviewIssueAction.REPAIR_PLAN),),
        ),
        reviewed_at_ms=42,
    )

    assert MissionPlanReviewAttempt.from_json(attempt.to_json(), "attempt") == attempt


@pytest.mark.parametrize(
    ("approved", "issues"),
    [(True, (_issue(ReviewIssueAction.REPAIR_PLAN),)), (False, ())],
)
def test_review_rejects_contradictory_approval_evidence(
    approved: bool, issues: tuple[MissionReviewIssue, ...]
) -> None:
    """Approval cannot carry blockers and rejection cannot omit its evidence."""
    with pytest.raises(MissionReviewError, match="approved reviews"):
        MissionPlanReview(approved=approved, issues=issues)


def test_review_routing_prioritizes_missing_user_information() -> None:
    """Any clarification need prevents Repair from guessing even with repairable defects."""
    review = MissionPlanReview(
        approved=False,
        issues=(
            _issue(ReviewIssueAction.REPAIR_PLAN),
            _issue(ReviewIssueAction.REQUEST_CLARIFICATION),
            _issue(ReviewIssueAction.REJECT_DRAFT),
        ),
    )

    assert route_mission_review(review) is MissionReviewRoute.CLARIFICATION


def test_review_routing_rejects_before_attempting_repair() -> None:
    """A non-repairable issue prevents automatic changes to the rejected draft."""
    review = MissionPlanReview(
        approved=False,
        issues=(
            _issue(ReviewIssueAction.REPAIR_PLAN),
            _issue(ReviewIssueAction.REJECT_DRAFT),
        ),
    )

    assert route_mission_review(review) is MissionReviewRoute.REJECTED
