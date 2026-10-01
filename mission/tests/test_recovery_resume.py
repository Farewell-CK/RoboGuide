"""Explicit resume preserves frozen inputs and durable boundaries without hidden retries."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from mission.controller import HttpMissionController, SubmissionReceipt
from mission.provider_errors import MissionProviderError
from mission.recovery import FailureReason, RecoveryAction
from mission.requests import MissionRequestLifecycle, MissionRequestStore
from mission.review import ReviewIssueAction
from test_request_review import (
    AcceptingController,
    FakeRepairer,
    FakeReviewer,
    _review,
)
from test_request_review import (
    FakeInterpreter as ReviewInterpreter,
)
from test_request_review import (
    _engine as review_engine,
)
from test_requests import (
    FakeController,
    FakeInterpreter,
    FakePlanner,
    _assessment,
    _engine,
    _fixture_contracts,
    _inventory,
)
from test_submission_observability import http_boundary


def test_retry_retains_the_review_snapshot_and_original_failure(tmp_path: Path) -> None:
    """A repaired retry keeps all reviews bound to one context and archives the failed boundary."""
    reviewer = FakeReviewer([_review(ReviewIssueAction.REPAIR_PLAN)] * 3 + [_review()])
    engine = review_engine(
        tmp_path, ReviewInterpreter(2), reviewer, FakeRepairer(), AcceptingController()
    )
    failed = engine.create("deliver the payload")
    assert failed.lifecycle is MissionRequestLifecycle.FAILED
    accepted = engine.retry(failed.request_id)
    assert accepted.lifecycle is MissionRequestLifecycle.ACCEPTED
    assert accepted.grounding_context == failed.grounding_context
    assert len(accepted.review_history) == 4
    assert accepted.grounding_context is not None
    assert all(
        review.grounding_context_digest == accepted.grounding_context.context_digest
        for review in accepted.review_history
    )
    history = MissionRequestStore(tmp_path / "review-requests.sqlite3").history(failed.request_id)
    assert history[0] == failed
    assert history[0].failure_evidence == failed.failure_evidence


@pytest.mark.parametrize("status", [401, 403, 408, 429, 500, 503])
def test_operator_retry_can_resume_the_original_infrastructure_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    """Fixed infrastructure resumes only on an explicit command, with no draft regeneration."""
    planner = FakePlanner()
    original = planner.plan

    def unavailable(*args: Any, **kwargs: Any) -> Any:
        """Return a typed infrastructure fault before a draft exists."""
        raise MissionProviderError("provider unavailable", status_code=status)

    monkeypatch.setattr(planner, "plan", unavailable)
    controller = FakeController(
        _inventory(*_fixture_contracts()), [SubmissionReceipt(True, 202, "ok")]
    )
    engine = _engine(tmp_path, FakeInterpreter([_assessment(), _assessment()]), planner, controller)
    failed = engine.create("deliver the declared payload")
    assert controller.submissions == []
    assert failed.recovery_evidence is not None
    assert failed.recovery_evidence.reason is (
        FailureReason.PROVIDER_AUTHENTICATION
        if status in {401, 403}
        else FailureReason.PROVIDER_TRANSIENT
    )
    monkeypatch.setattr(planner, "plan", original)
    accepted = engine.retry(failed.request_id)
    assert accepted.lifecycle is MissionRequestLifecycle.ACCEPTED
    assert accepted.dialogue == failed.dialogue
    assert accepted.grounding_context == failed.grounding_context
    assert len(planner.calls) == len(controller.submissions) == 1
    assert (
        MissionRequestStore(tmp_path / "requests.sqlite3").history(failed.request_id)[0] == failed
    )


@pytest.mark.parametrize("status", [400, 409, 422])
def test_restart_reduces_a_saved_definitive_rejection(tmp_path: Path, status: int) -> None:
    """The old receipt-before-state crash window recovers refusal, never an invented POST."""
    with http_boundary(status=status) as (endpoint, bodies):
        controller = HttpMissionController(endpoint, 2)
        engine = _engine(tmp_path, FakeInterpreter([_assessment()]), FakePlanner(), controller)
        blocked = engine.create("deliver the declared payload")
        assert blocked.recovery_evidence is not None
        store = MissionRequestStore(tmp_path / "requests.sqlite3")
        store.save(
            replace(
                blocked,
                lifecycle=MissionRequestLifecycle.SUBMITTING,
                recovery_evidence=replace(
                    blocked.recovery_evidence,
                    reason=FailureReason.SUBMISSION_IN_FLIGHT,
                    controller_status_code=None,
                ),
            )
        )
        restored = _engine(tmp_path, FakeInterpreter([]), FakePlanner(), controller).get(
            blocked.request_id
        )
        assert restored.lifecycle is MissionRequestLifecycle.BLOCKED
        assert restored.recovery_evidence is not None
        assert restored.recovery_evidence.action is RecoveryAction.RESUBMIT_UNCHANGED
        assert restored.submission_evidence == blocked.submission_evidence
        assert len(bodies) == 1


def test_new_post_cannot_reuse_the_previous_rejection_after_a_crash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Before a second POST the original rejection is archived and the current receipt cleared."""
    with http_boundary(status=409) as (endpoint, bodies):
        controller = HttpMissionController(endpoint, 2)
        engine = _engine(tmp_path, FakeInterpreter([_assessment()]), FakePlanner(), controller)
        blocked = engine.create("deliver the declared payload")

        def crash(*args: Any, **kwargs: Any) -> Any:
            """Interrupt the new POST boundary without supplying a response."""
            raise KeyboardInterrupt

        monkeypatch.setattr(controller, "submit_plan", crash)
        with pytest.raises(KeyboardInterrupt):
            engine.retry(blocked.request_id)
        restored = _engine(tmp_path, FakeInterpreter([]), FakePlanner(), controller).get(
            blocked.request_id
        )
        assert restored.submission_evidence is None
        assert restored.recovery_evidence is not None
        assert restored.recovery_evidence.action is RecoveryAction.RECONCILE_SUBMISSION
        assert len(bodies) == 1
        assert (
            MissionRequestStore(tmp_path / "requests.sqlite3").history(blocked.request_id)[0]
            == blocked
        )
