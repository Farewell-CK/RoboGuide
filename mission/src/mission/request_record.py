"""Versioned durable Mission Request projection and interpretation handoff."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, cast

from mission.intent import GroundedIntent
from mission.models import JSONObject, JSONValue, MissionPlan
from mission.review import MissionPlanReviewAttempt, MissionReviewError

MISSION_REQUEST_SCHEMA = "roboguide.mission-request/v0.2"
_COMPATIBLE_MISSION_REQUEST_SCHEMAS = {
    "roboguide.mission-request/v0.1",
    MISSION_REQUEST_SCHEMA,
}


class MissionRequestError(RuntimeError):
    """Report an unknown request, invalid command, or lifecycle conflict."""


class MissionRequestLifecycle(StrEnum):
    """Identify the durable pre-execution lifecycle of one user instruction."""

    RECEIVED = "Received"
    INTERPRETING = "Interpreting"
    NEEDS_CLARIFICATION = "NeedsClarification"
    DRAFTED = "Drafted"
    REVIEWING = "Reviewing"
    REPAIRING = "Repairing"
    AWAITING_APPROVAL = "AwaitingApproval"
    SUBMITTING = "Submitting"
    ACCEPTED = "Accepted"
    BLOCKED = "Blocked"
    FAILED = "Failed"
    CANCELLED = "Cancelled"


@dataclass(frozen=True, slots=True)
class IntentAssessment:
    """Capture a grounded objective and unresolved questions before Task decomposition."""

    objective: str
    constraints: tuple[str, ...]
    assumptions: tuple[str, ...]
    open_questions: tuple[str, ...]

    @classmethod
    def from_json(cls, value: JSONObject) -> IntentAssessment:
        """Parse strict structured interpreter output without accepting hidden fields."""
        expected = {"objective", "constraints", "assumptions", "open_questions"}
        if set(value) != expected:
            raise MissionRequestError("intent assessment fields do not match v0.1")
        objective = value["objective"]
        if not isinstance(objective, str) or not objective.strip():
            raise MissionRequestError("intent objective must be nonblank text")

        def text_tuple(field: str) -> tuple[str, ...]:
            """Read one assessment text array while rejecting blank entries."""
            items = value[field]
            if not isinstance(items, list) or not all(
                isinstance(item, str) and item.strip() for item in items
            ):
                raise MissionRequestError(f"intent {field} must contain nonblank text")
            return tuple(cast(list[str], items))

        return cls(
            objective=objective,
            constraints=text_tuple("constraints"),
            assumptions=text_tuple("assumptions"),
            open_questions=text_tuple("open_questions"),
        )

    def to_json(self) -> JSONObject:
        """Serialize grounded intent independently from the MissionPlan contract."""
        return {
            "objective": self.objective,
            "constraints": list(self.constraints),
            "assumptions": list(self.assumptions),
            "open_questions": list(self.open_questions),
        }

    def grounded_intent(self) -> GroundedIntent:
        """Return a resolved Planner handoff or reject an assessment with open questions."""
        if self.open_questions:
            raise MissionRequestError("intent with open questions cannot enter planning")
        return GroundedIntent(self.objective, self.constraints, self.assumptions)


class MissionInterpreter(Protocol):
    """Ground an instruction from user dialogue without consulting deployment placement facts."""

    def interpret(
        self,
        instruction: str,
        messages: tuple[str, ...],
    ) -> IntentAssessment:
        """Return a normalized objective or explicit open questions."""
        ...


@dataclass(frozen=True, slots=True)
class MissionRequestRecord:
    """Persist one Mission Intelligence request without mirroring execution lifecycle."""

    request_id: str
    mission_id: str
    instruction: str
    messages: tuple[str, ...]
    lifecycle: MissionRequestLifecycle
    assessment: IntentAssessment | None
    plan: MissionPlan | None
    draft_revision: int
    draft_digest: str | None
    approval_required: bool
    issues: tuple[str, ...]
    created_at_ms: int
    updated_at_ms: int
    repair_attempts: int = 0
    review_history: tuple[MissionPlanReviewAttempt, ...] = ()

    def to_json(self) -> JSONObject:
        """Serialize the versioned status projection returned by the Mission Request API."""
        return {
            "schema_version": MISSION_REQUEST_SCHEMA,
            "request_id": self.request_id,
            "mission_id": self.mission_id,
            "instruction": self.instruction,
            "messages": list(self.messages),
            "lifecycle": self.lifecycle.value,
            "assessment": self.assessment.to_json() if self.assessment is not None else None,
            "plan": self.plan.to_json() if self.plan is not None else None,
            "draft_revision": self.draft_revision,
            "draft_digest": self.draft_digest,
            "approval_required": self.approval_required,
            "issues": list(self.issues),
            "repair_attempts": self.repair_attempts,
            "review_history": [attempt.to_json() for attempt in self.review_history],
            "created_at_ms": self.created_at_ms,
            "updated_at_ms": self.updated_at_ms,
        }

    @classmethod
    def from_json(cls, value: JSONObject) -> MissionRequestRecord:
        """Restore one durable request and revalidate its embedded plan and assessment."""
        schema_version = value.get("schema_version")
        if (
            not isinstance(schema_version, str)
            or schema_version not in _COMPATIBLE_MISSION_REQUEST_SCHEMAS
        ):
            raise MissionRequestError("unsupported Mission Request schema")
        request_id = _required_text(value, "request_id")
        mission_id = _required_text(value, "mission_id")
        instruction = _required_text(value, "instruction")
        lifecycle_text = _required_text(value, "lifecycle")
        messages = _text_array(value, "messages")
        issues = _text_array(value, "issues")
        assessment_value = value.get("assessment")
        plan_value = value.get("plan")
        assessment = (
            None
            if assessment_value is None
            else IntentAssessment.from_json(_json_object(assessment_value, "assessment"))
        )
        plan = (
            None if plan_value is None else MissionPlan.from_json(_json_object(plan_value, "plan"))
        )
        draft_revision = _required_integer(value, "draft_revision")
        created_at_ms = _required_integer(value, "created_at_ms")
        updated_at_ms = _required_integer(value, "updated_at_ms")
        draft_digest = value.get("draft_digest")
        if draft_digest is not None and not isinstance(draft_digest, str):
            raise MissionRequestError("draft_digest must be text or null")
        approval_required = value.get("approval_required")
        if not isinstance(approval_required, bool):
            raise MissionRequestError("approval_required must be a boolean")
        try:
            lifecycle = MissionRequestLifecycle(lifecycle_text)
        except ValueError as error:
            raise MissionRequestError("unknown Mission Request lifecycle") from error
        if schema_version == MISSION_REQUEST_SCHEMA:
            repair_attempts = _required_integer(value, "repair_attempts")
            history_value = value.get("review_history")
            if not isinstance(history_value, list):
                raise MissionRequestError("review_history must be an array")
            try:
                review_history = tuple(
                    MissionPlanReviewAttempt.from_json(attempt, f"review_history[{index}]")
                    for index, attempt in enumerate(history_value)
                )
            except MissionReviewError as error:
                raise MissionRequestError(str(error)) from error
        else:
            repair_attempts = 0
            review_history = ()
        review_revisions = [attempt.draft_revision for attempt in review_history]
        if review_revisions != sorted(set(review_revisions)):
            raise MissionRequestError("review_history revisions must be strictly increasing")
        if review_revisions and review_revisions[-1] > draft_revision:
            raise MissionRequestError("review_history references a future draft revision")
        if repair_attempts > draft_revision:
            raise MissionRequestError("repair_attempts exceeds the current draft revision")
        return cls(
            request_id=request_id,
            mission_id=mission_id,
            instruction=instruction,
            messages=messages,
            lifecycle=lifecycle,
            assessment=assessment,
            plan=plan,
            draft_revision=draft_revision,
            draft_digest=draft_digest,
            approval_required=approval_required,
            issues=issues,
            created_at_ms=created_at_ms,
            updated_at_ms=updated_at_ms,
            repair_attempts=repair_attempts,
            review_history=review_history,
        )


def _json_object(value: JSONValue | object, field: str) -> JSONObject:
    """Return a string-keyed JSON object or reject corrupt persisted evidence."""
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise MissionRequestError(f"{field} must be an object")
    return cast(JSONObject, value)


def _required_text(value: JSONObject, field: str) -> str:
    """Read one required nonblank text field from persisted request JSON."""
    item = value.get(field)
    if not isinstance(item, str) or not item.strip():
        raise MissionRequestError(f"{field} must be nonblank text")
    return item


def _required_integer(value: JSONObject, field: str) -> int:
    """Read one required nonnegative integer without accepting Boolean coercion."""
    item = value.get(field)
    if isinstance(item, bool) or not isinstance(item, int) or item < 0:
        raise MissionRequestError(f"{field} must be a nonnegative integer")
    return item


def _text_array(value: JSONObject, field: str) -> tuple[str, ...]:
    """Read one persisted array containing only nonblank text."""
    items = value.get(field)
    if not isinstance(items, list) or not all(
        isinstance(item, str) and item.strip() for item in items
    ):
        raise MissionRequestError(f"{field} must contain nonblank text")
    return tuple(cast(list[str], items))
