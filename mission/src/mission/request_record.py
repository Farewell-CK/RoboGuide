"""Versioned durable Mission Request projection and interpretation handoff."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, cast

from mission.grounding_context import GroundingContextSnapshot, dialogue_digest
from mission.intent import GroundedIntent
from mission.models import JSONObject, JSONValue, MissionPlan
from mission.review import MissionPlanReviewAttempt, MissionReviewError

MISSION_REQUEST_SCHEMA = "roboguide.mission-request/v0.4"
_COMPATIBLE_MISSION_REQUEST_SCHEMAS = {
    "roboguide.mission-request/v0.1",
    "roboguide.mission-request/v0.2",
    "roboguide.mission-request/v0.3",
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


class DialogueSpeaker(StrEnum):
    """Identify the semantic source of one user-facing dialogue turn."""

    USER = "User"
    MISSION_INTELLIGENCE = "MissionIntelligence"


class DialogueTurnKind(StrEnum):
    """Distinguish initial instruction from clarification exchange."""

    INSTRUCTION = "Instruction"
    CLARIFICATION_QUESTION = "ClarificationQuestion"
    CLARIFICATION_ANSWER = "ClarificationAnswer"


@dataclass(frozen=True, slots=True)
class DialogueTurn:
    """Persist one ordered user-facing turn independently from internal review evidence."""

    turn_id: str
    speaker: DialogueSpeaker
    kind: DialogueTurnKind
    content: str
    created_at_ms: int
    in_reply_to: str | None = None

    def __post_init__(self) -> None:
        """Reject blank identities/content and invalid timestamp or speaker-kind combinations."""
        if not self.turn_id.strip() or not self.content.strip():
            raise MissionRequestError("Dialogue turn identity and content must be nonblank")
        if self.created_at_ms < 0:
            raise MissionRequestError("Dialogue turn timestamp must be nonnegative")
        expected_speaker = (
            DialogueSpeaker.MISSION_INTELLIGENCE
            if self.kind is DialogueTurnKind.CLARIFICATION_QUESTION
            else DialogueSpeaker.USER
        )
        if self.speaker is not expected_speaker:
            raise MissionRequestError("Dialogue turn speaker does not match its kind")
        if self.kind is DialogueTurnKind.INSTRUCTION and self.in_reply_to is not None:
            raise MissionRequestError("Dialogue instruction cannot reply to another turn")

    def to_json(self) -> JSONObject:
        """Serialize one closed Mission Request dialogue turn."""
        return {
            "turn_id": self.turn_id,
            "speaker": self.speaker.value,
            "kind": self.kind.value,
            "content": self.content,
            "created_at_ms": self.created_at_ms,
            "in_reply_to": self.in_reply_to,
        }

    @classmethod
    def from_json(cls, value: object, path: str) -> DialogueTurn:
        """Restore one current dialogue turn without accepting internal planning fields."""
        item = _json_object(value, path)
        expected = {"turn_id", "speaker", "kind", "content", "created_at_ms", "in_reply_to"}
        if set(item) != expected:
            raise MissionRequestError(f"{path} fields do not match v0.3")
        try:
            speaker = DialogueSpeaker(_required_text(item, "speaker"))
            kind = DialogueTurnKind(_required_text(item, "kind"))
        except ValueError as error:
            raise MissionRequestError(f"{path} has unknown speaker or kind") from error
        in_reply_to = item["in_reply_to"]
        if in_reply_to is not None and (
            not isinstance(in_reply_to, str) or not in_reply_to.strip()
        ):
            raise MissionRequestError(f"{path}.in_reply_to must be nonblank text or null")
        return cls(
            turn_id=_required_text(item, "turn_id"),
            speaker=speaker,
            kind=kind,
            content=_required_text(item, "content"),
            created_at_ms=_required_integer(item, "created_at_ms"),
            in_reply_to=in_reply_to,
        )


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
        dialogue: tuple[DialogueTurn, ...],
        grounding_context: GroundingContextSnapshot,
    ) -> IntentAssessment:
        """Return a normalized objective or explicit open questions."""
        ...


@dataclass(frozen=True, slots=True)
class MissionRequestRecord:
    """Persist one Mission Intelligence request without mirroring execution lifecycle."""

    request_id: str
    mission_id: str
    dialogue: tuple[DialogueTurn, ...]
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
    approval_reasons: tuple[str, ...] = ()
    grounding_context: GroundingContextSnapshot | None = None

    def __post_init__(self) -> None:
        """Reject a snapshot detached from the request or its captured dialogue revision."""
        context = self.grounding_context
        if context is None:
            return
        if context.request_id != self.request_id:
            raise MissionRequestError("grounding context belongs to another Mission Request")
        if context.dialogue_digest not in _allowed_grounding_dialogue_digests(self.dialogue):
            raise MissionRequestError(
                "grounding context does not match the Mission Request dialogue"
            )

    def to_json(self) -> JSONObject:
        """Serialize the versioned status projection returned by the Mission Request API."""
        return {
            "schema_version": MISSION_REQUEST_SCHEMA,
            "request_id": self.request_id,
            "mission_id": self.mission_id,
            "dialogue": [turn.to_json() for turn in self.dialogue],
            "lifecycle": self.lifecycle.value,
            "assessment": self.assessment.to_json() if self.assessment is not None else None,
            "plan": self.plan.to_json() if self.plan is not None else None,
            "grounding_context": (
                self.grounding_context.to_json() if self.grounding_context is not None else None
            ),
            "draft_revision": self.draft_revision,
            "draft_digest": self.draft_digest,
            "approval_required": self.approval_required,
            "approval_reasons": list(self.approval_reasons),
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
        lifecycle_text = _required_text(value, "lifecycle")
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
        if schema_version == MISSION_REQUEST_SCHEMA and "grounding_context" not in value:
            raise MissionRequestError("Mission Request v0.4 must carry grounding_context")
        context_value = value.get("grounding_context")
        grounding_context = (
            GroundingContextSnapshot.from_json(context_value)
            if schema_version == MISSION_REQUEST_SCHEMA and context_value is not None
            else None
        )
        draft_revision = _required_integer(value, "draft_revision")
        created_at_ms = _required_integer(value, "created_at_ms")
        updated_at_ms = _required_integer(value, "updated_at_ms")
        if schema_version in {"roboguide.mission-request/v0.3", MISSION_REQUEST_SCHEMA}:
            dialogue_value = value.get("dialogue")
            if not isinstance(dialogue_value, list):
                raise MissionRequestError("dialogue must be an array")
            dialogue = tuple(
                DialogueTurn.from_json(turn, f"dialogue[{index}]")
                for index, turn in enumerate(dialogue_value)
            )
        else:
            instruction = _required_text(value, "instruction")
            messages = _text_array(value, "messages")
            dialogue = _legacy_dialogue(instruction, messages, created_at_ms)
        _validate_dialogue(dialogue)
        draft_digest = value.get("draft_digest")
        if draft_digest is not None and not isinstance(draft_digest, str):
            raise MissionRequestError("draft_digest must be text or null")
        approval_required = value.get("approval_required")
        if not isinstance(approval_required, bool):
            raise MissionRequestError("approval_required must be a boolean")
        approval_reasons = (
            _text_array(value, "approval_reasons")
            if schema_version in {"roboguide.mission-request/v0.3", MISSION_REQUEST_SCHEMA}
            else ()
        )
        try:
            lifecycle = MissionRequestLifecycle(lifecycle_text)
        except ValueError as error:
            raise MissionRequestError("unknown Mission Request lifecycle") from error
        if schema_version in {
            "roboguide.mission-request/v0.2",
            "roboguide.mission-request/v0.3",
            MISSION_REQUEST_SCHEMA,
        }:
            repair_attempts = _required_integer(value, "repair_attempts")
            history_value = value.get("review_history")
            if not isinstance(history_value, list):
                raise MissionRequestError("review_history must be an array")
            if schema_version == MISSION_REQUEST_SCHEMA and any(
                not isinstance(attempt, dict) or "grounding_context_digest" not in attempt
                for attempt in history_value
            ):
                raise MissionRequestError(
                    "Mission Request v0.4 review attempts must carry grounding context identity"
                )
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
            dialogue=dialogue,
            lifecycle=lifecycle,
            assessment=assessment,
            plan=plan,
            grounding_context=grounding_context,
            draft_revision=draft_revision,
            draft_digest=draft_digest,
            approval_required=approval_required,
            issues=issues,
            created_at_ms=created_at_ms,
            updated_at_ms=updated_at_ms,
            repair_attempts=repair_attempts,
            review_history=review_history,
            approval_reasons=approval_reasons,
        )

    @property
    def instruction(self) -> str:
        """Return the initial user instruction from the normalized dialogue source."""
        return next(
            turn.content for turn in self.dialogue if turn.kind is DialogueTurnKind.INSTRUCTION
        )

    @property
    def messages(self) -> tuple[str, ...]:
        """Return legacy clarification-answer text without duplicating durable state."""
        return tuple(
            turn.content
            for turn in self.dialogue
            if turn.kind is DialogueTurnKind.CLARIFICATION_ANSWER
        )


def _allowed_grounding_dialogue_digests(
    dialogue: tuple[DialogueTurn, ...],
) -> frozenset[str]:
    """Allow the captured input plus clarification questions appended after deliberation."""
    full = dialogue_digest(tuple(turn.to_json() for turn in dialogue))
    captured_length = len(dialogue)
    while captured_length > 0:
        turn = dialogue[captured_length - 1]
        if (
            turn.speaker is not DialogueSpeaker.MISSION_INTELLIGENCE
            or turn.kind is not DialogueTurnKind.CLARIFICATION_QUESTION
        ):
            break
        captured_length -= 1
    if captured_length == len(dialogue):
        return frozenset({full})
    captured = dialogue_digest(tuple(turn.to_json() for turn in dialogue[:captured_length]))
    return frozenset({full, captured})


def _legacy_dialogue(
    instruction: str, messages: tuple[str, ...], created_at_ms: int
) -> tuple[DialogueTurn, ...]:
    """Normalize v0.1-v0.2 instruction/messages into explicit user dialogue turns."""
    turns = [
        DialogueTurn(
            "legacy-instruction",
            DialogueSpeaker.USER,
            DialogueTurnKind.INSTRUCTION,
            instruction,
            created_at_ms,
        )
    ]
    turns.extend(
        DialogueTurn(
            f"legacy-answer-{index + 1}",
            DialogueSpeaker.USER,
            DialogueTurnKind.CLARIFICATION_ANSWER,
            message,
            created_at_ms,
        )
        for index, message in enumerate(messages)
    )
    return tuple(turns)


def _validate_dialogue(dialogue: tuple[DialogueTurn, ...]) -> None:
    """Require one initial instruction, unique identities, and valid reply references."""
    if not dialogue or dialogue[0].kind is not DialogueTurnKind.INSTRUCTION:
        raise MissionRequestError("dialogue must begin with one user instruction")
    turn_ids = [turn.turn_id for turn in dialogue]
    if len(set(turn_ids)) != len(turn_ids):
        raise MissionRequestError("dialogue contains duplicate turn identities")
    known: set[str] = set()
    for turn in dialogue:
        if turn.in_reply_to is not None and turn.in_reply_to not in known:
            raise MissionRequestError("dialogue reply references an unknown or future turn")
        known.add(turn.turn_id)


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
