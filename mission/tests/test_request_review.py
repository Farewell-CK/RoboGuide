"""Deterministic Mission Request tests for Review, Repair, and clarification routing."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from mission.capability_catalog import CanonicalCapabilityCatalog
from mission.controller import SubmissionReceipt
from mission.intent import GroundedIntent
from mission.models import JSONObject, MissionPlan
from mission.request_record import (
    DialogueSpeaker,
    DialogueTurn,
    DialogueTurnKind,
    IntentAssessment,
    MissionRequestLifecycle,
    MissionRequestRecord,
)
from mission.request_store import MissionRequestStore
from mission.requests import MissionRequestEngine
from mission.review import MissionPlanReview, MissionReviewIssue, ReviewIssueAction

FIXTURE = Path("scenarios/phase1-mission-v0.3/mission-plan.json")
CATALOG = Path("contracts/capability/v0.1/catalog.json")


class FakeInterpreter:
    """Return one stable grounded assessment for each requested deliberation cycle."""

    def __init__(self, count: int = 1) -> None:
        """Create a finite number of identical resolved assessments."""
        self.remaining = count

    def interpret(self, dialogue: tuple[DialogueTurn, ...]) -> IntentAssessment:
        """Consume one call without using deployment facts or changing the objective."""
        del dialogue
        if self.remaining <= 0:
            raise AssertionError("fake Interpreter call budget is exhausted")
        self.remaining -= 1
        return IntentAssessment(
            objective="deliver the payload through the approved route",
            constraints=("preserve local safety",),
            assumptions=(),
            open_questions=(),
        )


def _dialogue() -> tuple[DialogueTurn, ...]:
    """Build one deterministic instruction turn for direct persistence tests."""
    return (
        DialogueTurn(
            "turn-0001",
            DialogueSpeaker.USER,
            DialogueTurnKind.INSTRUCTION,
            "test",
            1,
        ),
    )


class FakePlanner:
    """Create valid fixture-shaped drafts with the Engine-owned Mission identity."""

    def plan(
        self,
        mission_id: str,
        grounded_intent: GroundedIntent,
        capability_catalog: CanonicalCapabilityCatalog,
    ) -> MissionPlan:
        """Return one admitted initial draft without running semantic Review."""
        raw = cast(JSONObject, json.loads(FIXTURE.read_text(encoding="utf-8")))
        mission = cast(JSONObject, raw["mission"])
        mission["id"] = mission_id
        mission["objective"] = grounded_intent.objective
        plan = MissionPlan.from_json(raw)
        capability_catalog.validate_plan(plan)
        return plan


class AcceptingController:
    """Accept submitted plans while exposing exactly which revision crossed the boundary."""

    def __init__(self) -> None:
        """Initialize an empty submission trace."""
        self.submissions: list[MissionPlan] = []

    def submit_plan(self, plan: MissionPlan) -> SubmissionReceipt:
        """Record and accept one already reviewed MissionPlan."""
        self.submissions.append(plan)
        return SubmissionReceipt(True, 202, "Running")


class SequenceIds:
    """Generate deterministic UUID-shaped request and Mission identity suffixes."""

    def __init__(self) -> None:
        """Start before the first identity."""
        self.value = 0

    def __call__(self) -> str:
        """Return the next lowercase hexadecimal identity suffix."""
        self.value += 1
        return f"{self.value:032x}"


class SequenceClock:
    """Generate deterministic increasing deliberation evidence timestamps."""

    def __init__(self) -> None:
        """Start before the first persisted timestamp."""
        self.value = 100

    def __call__(self) -> int:
        """Return the next process-local test timestamp."""
        self.value += 1
        return self.value


class FakeReviewer:
    """Return scripted structured Reviews and retain exact examined drafts."""

    def __init__(self, reviews: list[MissionPlanReview]) -> None:
        """Initialize a finite review queue for deterministic orchestration tests."""
        self.reviews = reviews
        self.calls: list[MissionPlan] = []

    def review(
        self,
        grounded_intent: GroundedIntent,
        plan: MissionPlan,
        capability_catalog: CanonicalCapabilityCatalog,
    ) -> MissionPlanReview:
        """Record one admitted draft and return the next scripted outcome."""
        del grounded_intent, capability_catalog
        self.calls.append(plan)
        if not self.reviews:
            raise AssertionError("fake Reviewer result queue is empty")
        return self.reviews.pop(0)


class FakeRepairer:
    """Produce inspectably different valid drafts without inventing grounded facts."""

    def __init__(self) -> None:
        """Initialize an empty repair call trace."""
        self.calls: list[tuple[MissionPlan, MissionPlanReview]] = []

    def repair(
        self,
        mission_id: str,
        grounded_intent: GroundedIntent,
        rejected_plan: MissionPlan,
        review: MissionPlanReview,
        capability_catalog: CanonicalCapabilityCatalog,
    ) -> MissionPlan:
        """Return a valid revision with a deterministic description-only repair marker."""
        del capability_catalog
        self.calls.append((rejected_plan, review))
        raw = rejected_plan.to_json()
        mission = cast(JSONObject, raw["mission"])
        mission["id"] = mission_id
        mission["objective"] = grounded_intent.objective
        tasks = cast(list[JSONObject], raw["tasks"])
        tasks[0]["description"] = f"{tasks[0]['description']} [repaired {len(self.calls)}]"
        return MissionPlan.from_json(raw)


class UnknownContractRepairer(FakeRepairer):
    """Return a repaired draft outside the Catalog to test post-repair admission."""

    def repair(
        self,
        mission_id: str,
        grounded_intent: GroundedIntent,
        rejected_plan: MissionPlan,
        review: MissionPlanReview,
        capability_catalog: CanonicalCapabilityCatalog,
    ) -> MissionPlan:
        """Deliberately replace one canonical contract after recording the repair call."""
        repaired = super().repair(
            mission_id,
            grounded_intent,
            rejected_plan,
            review,
            capability_catalog,
        )
        raw = repaired.to_json()
        tasks = cast(list[JSONObject], raw["tasks"])
        roles = cast(list[JSONObject], tasks[0]["roles"])
        invented: JSONObject = {
            "namespace": "delivery",
            "name": "magic_move",
            "version": "v1",
        }
        roles[0]["contract"] = invented
        execution = cast(JSONObject, roles[0]["execution"])
        execution["capability_contract"] = invented
        return MissionPlan.from_json(raw)


def _review(action: ReviewIssueAction | None = None) -> MissionPlanReview:
    """Build an approval or one structured blocking Review for Engine tests."""
    if action is None:
        return MissionPlanReview(approved=True, issues=())
    return MissionPlanReview(
        approved=False,
        issues=(
            MissionReviewIssue(
                code="task.over_decomposed",
                path="/tasks/0",
                message="The Task exposes Local How.",
                required_action=action,
            ),
        ),
    )


def _engine(
    tmp_path: Path,
    interpreter: FakeInterpreter,
    reviewer: FakeReviewer,
    repairer: FakeRepairer,
    controller: AcceptingController,
    max_repair_attempts: int = 2,
) -> MissionRequestEngine:
    """Compose one persistent deterministic Review/Repair orchestration fixture."""
    return MissionRequestEngine(
        MissionRequestStore(tmp_path / "review-requests.sqlite3"),
        interpreter,
        FakePlanner(),
        controller,
        CanonicalCapabilityCatalog.load(CATALOG),
        frozenset(),
        SequenceIds(),
        SequenceClock(),
        reviewer=reviewer,
        repairer=repairer,
        max_repair_attempts=max_repair_attempts,
    )


def test_repairable_review_produces_a_new_approved_draft_revision(tmp_path: Path) -> None:
    """A repairable defect follows bounded Repair and submits only the approved revision."""
    reviewer = FakeReviewer([_review(ReviewIssueAction.REPAIR_PLAN), _review()])
    repairer = FakeRepairer()
    controller = AcceptingController()
    engine = _engine(tmp_path, FakeInterpreter(), reviewer, repairer, controller)

    accepted = engine.create("执行明确的运输任务")
    restored = engine.get(accepted.request_id)

    assert accepted.lifecycle is MissionRequestLifecycle.ACCEPTED
    assert accepted.draft_revision == 2
    assert accepted.repair_attempts == 1
    assert [attempt.draft_revision for attempt in accepted.review_history] == [1, 2]
    assert accepted.review_history[0].review.approved is False
    assert accepted.review_history[1].review.approved is True
    assert len(reviewer.calls) == 2
    assert len(repairer.calls) == 1
    assert controller.submissions == [accepted.plan]
    assert restored == accepted


def test_review_clarification_returns_to_dialogue_without_repair(tmp_path: Path) -> None:
    """Missing user facts route to dialogue and never let the Repairer guess an answer."""
    reviewer = FakeReviewer([_review(ReviewIssueAction.REQUEST_CLARIFICATION), _review()])
    repairer = FakeRepairer()
    controller = AcceptingController()
    engine = _engine(tmp_path, FakeInterpreter(2), reviewer, repairer, controller)

    waiting = engine.create("把物品送到指定地点")

    assert waiting.lifecycle is MissionRequestLifecycle.NEEDS_CLARIFICATION
    assert waiting.review_history[-1].review.issues[0].required_action is (
        ReviewIssueAction.REQUEST_CLARIFICATION
    )
    assert repairer.calls == []
    assert controller.submissions == []
    assert waiting.dialogue[-1].kind is DialogueTurnKind.CLARIFICATION_QUESTION
    assert len(waiting.review_history) == 1

    accepted = engine.add_message(waiting.request_id, "指定地点是实验室入口")

    assert accepted.lifecycle is MissionRequestLifecycle.ACCEPTED
    assert accepted.messages == ("指定地点是实验室入口",)
    assert accepted.dialogue[-1].in_reply_to == waiting.dialogue[-1].turn_id
    assert accepted.repair_attempts == 0
    assert [attempt.draft_revision for attempt in accepted.review_history] == [1, 2]
    assert len(controller.submissions) == 1


def test_review_reject_action_fails_without_invoking_repair(tmp_path: Path) -> None:
    """A non-repairable semantic boundary violation never reaches Repair or submission."""
    reviewer = FakeReviewer([_review(ReviewIssueAction.REJECT_DRAFT)])
    repairer = FakeRepairer()
    controller = AcceptingController()
    engine = _engine(tmp_path, FakeInterpreter(), reviewer, repairer, controller)

    rejected = engine.create("执行不允许自动修复的任务")

    assert rejected.lifecycle is MissionRequestLifecycle.FAILED
    assert rejected.issues == ("mission review rejected automatic repair",)
    assert len(rejected.review_history) == 1
    assert repairer.calls == []
    assert controller.submissions == []


def test_repair_budget_exhaustion_never_submits_the_last_rejected_draft(
    tmp_path: Path,
) -> None:
    """Exactly two repairs are allowed before a third rejected Review becomes Failed."""
    reviewer = FakeReviewer(
        [
            _review(ReviewIssueAction.REPAIR_PLAN),
            _review(ReviewIssueAction.REPAIR_PLAN),
            _review(ReviewIssueAction.REPAIR_PLAN),
        ]
    )
    repairer = FakeRepairer()
    controller = AcceptingController()
    engine = _engine(tmp_path, FakeInterpreter(), reviewer, repairer, controller)

    exhausted = engine.create("执行需要多次修复的任务")

    assert exhausted.lifecycle is MissionRequestLifecycle.FAILED
    assert exhausted.issues == ("mission review repair attempts exhausted",)
    assert exhausted.draft_revision == 3
    assert exhausted.repair_attempts == 2
    assert len(exhausted.review_history) == 3
    assert len(repairer.calls) == 2
    assert controller.submissions == []


def test_repaired_draft_reenters_catalog_validation_before_second_review(
    tmp_path: Path,
) -> None:
    """A Repairer cannot bypass deterministic Catalog admission with an invented contract."""
    reviewer = FakeReviewer([_review(ReviewIssueAction.REPAIR_PLAN)])
    controller = AcceptingController()
    engine = _engine(
        tmp_path,
        FakeInterpreter(),
        reviewer,
        UnknownContractRepairer(),
        controller,
    )

    failed = engine.create("执行修复后仍需确定性校验的任务")

    assert failed.lifecycle is MissionRequestLifecycle.FAILED
    assert "delivery.magic_move@v1" in failed.issues[0]
    assert len(failed.review_history) == 1
    assert len(reviewer.calls) == 1
    assert controller.submissions == []


def test_restart_fences_interrupted_repair_for_explicit_retry(tmp_path: Path) -> None:
    """Startup exposes an interrupted Repair as Failed instead of repeating model effects."""
    store = MissionRequestStore(tmp_path / "interrupted-repair.sqlite3")
    record = MissionRequestRecord(
        request_id="request-" + "1" * 32,
        mission_id="mission-" + "2" * 32,
        dialogue=_dialogue(),
        lifecycle=MissionRequestLifecycle.REPAIRING,
        assessment=None,
        plan=None,
        draft_revision=1,
        draft_digest=None,
        approval_required=False,
        issues=(),
        created_at_ms=1,
        updated_at_ms=1,
    )
    store.save(record)

    MissionRequestEngine(
        store,
        FakeInterpreter(0),
        FakePlanner(),
        AcceptingController(),
        CanonicalCapabilityCatalog.load(CATALOG),
        frozenset(),
        SequenceIds(),
        SequenceClock(),
    )

    restored = store.get(record.request_id)
    assert restored is not None
    assert restored.lifecycle is MissionRequestLifecycle.FAILED
    assert restored.issues == ("deliberation interrupted by Mission Service restart",)
