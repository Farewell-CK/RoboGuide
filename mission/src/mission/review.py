"""Structured Mission Plan review evidence and bounded repair ports."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, cast

from mission.capability_catalog import CanonicalCapabilityCatalog
from mission.intent import GroundedIntent
from mission.models import JSONObject, JSONValue, MissionPlan

_ISSUE_CODE = re.compile(r"^[a-z][a-z0-9_.-]*$")
_DRAFT_DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")


class MissionReviewError(ValueError):
    """Report malformed or internally contradictory semantic review evidence."""


class ReviewIssueAction(StrEnum):
    """Identify the only authority-safe next step for one blocking review issue."""

    REPAIR_PLAN = "RepairPlan"
    REQUEST_CLARIFICATION = "RequestClarification"
    REJECT_DRAFT = "RejectDraft"


class MissionReviewRoute(StrEnum):
    """Reduce structured findings to one unambiguous orchestration branch."""

    APPROVED = "Approved"
    REPAIR = "Repair"
    CLARIFICATION = "Clarification"
    REJECTED = "Rejected"


def _object(value: JSONValue | object, path: str) -> JSONObject:
    """Return a string-keyed object or reject malformed review evidence."""
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise MissionReviewError(f"{path} must be an object")
    return cast(JSONObject, value)


def _text(value: JSONValue | object, path: str) -> str:
    """Return nonblank text or reject incomplete review evidence."""
    if not isinstance(value, str) or not value.strip():
        raise MissionReviewError(f"{path} must be nonblank text")
    return value


@dataclass(frozen=True, slots=True)
class MissionReviewIssue:
    """Describe one blocking semantic defect and its required resolution path."""

    code: str
    path: str
    message: str
    required_action: ReviewIssueAction

    def __post_init__(self) -> None:
        """Reject issue values that cannot serve as stable machine-readable evidence."""
        if not isinstance(self.code, str):
            raise MissionReviewError("review issue code must be text")
        if _ISSUE_CODE.fullmatch(self.code) is None:
            raise MissionReviewError("review issue code is not canonical")
        if not isinstance(self.path, str):
            raise MissionReviewError("review issue path must be text")
        if not self.path.startswith("/"):
            raise MissionReviewError("review issue path must be a JSON Pointer")
        if not isinstance(self.message, str):
            raise MissionReviewError("review issue message must be text")
        if not self.message.strip():
            raise MissionReviewError("review issue message must be nonblank")
        if not isinstance(self.required_action, ReviewIssueAction):
            raise MissionReviewError("review issue required_action is invalid")

    @classmethod
    def from_json(cls, value: JSONValue, path: str) -> MissionReviewIssue:
        """Parse one issue while rejecting unstable codes and unknown fields."""
        item = _object(value, path)
        expected = {"code", "path", "message", "required_action"}
        if set(item) != expected:
            raise MissionReviewError(f"{path} fields do not match review issue v0.1")
        code = _text(item["code"], f"{path}.code")
        if _ISSUE_CODE.fullmatch(code) is None:
            raise MissionReviewError(f"{path}.code is not canonical")
        issue_path = _text(item["path"], f"{path}.path")
        if not issue_path.startswith("/"):
            raise MissionReviewError(f"{path}.path must be a JSON Pointer")
        action_text = _text(item["required_action"], f"{path}.required_action")
        try:
            action = ReviewIssueAction(action_text)
        except ValueError as error:
            raise MissionReviewError(f"{path}.required_action is unsupported") from error
        return cls(
            code=code,
            path=issue_path,
            message=_text(item["message"], f"{path}.message"),
            required_action=action,
        )

    def to_json(self) -> JSONObject:
        """Serialize one issue without exposing provider-specific review metadata."""
        return {
            "code": self.code,
            "path": self.path,
            "message": self.message,
            "required_action": self.required_action.value,
        }


@dataclass(frozen=True, slots=True)
class MissionPlanReview:
    """Record one immutable approval decision over an exact MissionPlan draft."""

    approved: bool
    issues: tuple[MissionReviewIssue, ...]

    def __post_init__(self) -> None:
        """Reject approvals with blockers and rejections without actionable evidence."""
        if not isinstance(self.approved, bool):
            raise MissionReviewError("review approved must be a boolean")
        if not isinstance(self.issues, tuple) or not all(
            isinstance(issue, MissionReviewIssue) for issue in self.issues
        ):
            raise MissionReviewError("review issues must contain MissionReviewIssue values")
        if self.approved == bool(self.issues):
            raise MissionReviewError(
                "approved reviews require no issues and rejected reviews require issues"
            )

    @classmethod
    def from_json(cls, value: JSONObject) -> MissionPlanReview:
        """Parse the strict structured output returned by a semantic Reviewer."""
        if set(value) != {"approved", "issues"}:
            raise MissionReviewError("mission review fields do not match v0.1")
        approved = value["approved"]
        issues_value = value["issues"]
        if not isinstance(approved, bool) or not isinstance(issues_value, list):
            raise MissionReviewError("mission review has invalid field types")
        issues = tuple(
            MissionReviewIssue.from_json(issue, f"issues[{index}]")
            for index, issue in enumerate(issues_value)
        )
        return cls(approved=approved, issues=issues)

    def to_json(self) -> JSONObject:
        """Serialize one review for provider input and durable request evidence."""
        return {
            "approved": self.approved,
            "issues": [issue.to_json() for issue in self.issues],
        }

    def actions(self) -> frozenset[ReviewIssueAction]:
        """Return the distinct required next steps without inventing precedence policy."""
        return frozenset(issue.required_action for issue in self.issues)


def route_mission_review(review: MissionPlanReview) -> MissionReviewRoute:
    """Apply clarification-first and rejection-before-repair routing policy."""
    if review.approved:
        return MissionReviewRoute.APPROVED
    actions = review.actions()
    if ReviewIssueAction.REQUEST_CLARIFICATION in actions:
        return MissionReviewRoute.CLARIFICATION
    if ReviewIssueAction.REJECT_DRAFT in actions:
        return MissionReviewRoute.REJECTED
    return MissionReviewRoute.REPAIR


@dataclass(frozen=True, slots=True)
class MissionPlanReviewAttempt:
    """Bind one review result to the immutable revision and digest it examined."""

    draft_revision: int
    draft_digest: str
    review: MissionPlanReview
    reviewed_at_ms: int

    def __post_init__(self) -> None:
        """Reject invalid draft identities and negative review evidence timestamps."""
        if (
            isinstance(self.draft_revision, bool)
            or not isinstance(self.draft_revision, int)
            or self.draft_revision <= 0
        ):
            raise MissionReviewError("review attempt draft_revision must be positive")
        if not isinstance(self.draft_digest, str):
            raise MissionReviewError("review attempt draft_digest must be text")
        if _DRAFT_DIGEST.fullmatch(self.draft_digest) is None:
            raise MissionReviewError("review attempt draft_digest is invalid")
        if (
            isinstance(self.reviewed_at_ms, bool)
            or not isinstance(self.reviewed_at_ms, int)
            or self.reviewed_at_ms < 0
        ):
            raise MissionReviewError("review attempt reviewed_at_ms must be nonnegative")

    @classmethod
    def from_json(cls, value: JSONValue, path: str) -> MissionPlanReviewAttempt:
        """Restore one review attempt while validating its immutable draft identity."""
        item = _object(value, path)
        expected = {"draft_revision", "draft_digest", "review", "reviewed_at_ms"}
        if set(item) != expected:
            raise MissionReviewError(f"{path} fields do not match review attempt v0.1")
        revision = item["draft_revision"]
        reviewed_at = item["reviewed_at_ms"]
        if isinstance(revision, bool) or not isinstance(revision, int) or revision <= 0:
            raise MissionReviewError(f"{path}.draft_revision must be positive")
        if isinstance(reviewed_at, bool) or not isinstance(reviewed_at, int) or reviewed_at < 0:
            raise MissionReviewError(f"{path}.reviewed_at_ms must be nonnegative")
        digest = _text(item["draft_digest"], f"{path}.draft_digest")
        if _DRAFT_DIGEST.fullmatch(digest) is None:
            raise MissionReviewError(f"{path}.draft_digest is invalid")
        return cls(
            draft_revision=revision,
            draft_digest=digest,
            review=MissionPlanReview.from_json(_object(item["review"], f"{path}.review")),
            reviewed_at_ms=reviewed_at,
        )

    def to_json(self) -> JSONObject:
        """Serialize review evidence separately from user dialogue and operational errors."""
        return {
            "draft_revision": self.draft_revision,
            "draft_digest": self.draft_digest,
            "review": self.review.to_json(),
            "reviewed_at_ms": self.reviewed_at_ms,
        }


class MissionPlanReviewer(Protocol):
    """Assess one validated draft without mutating or replacing it."""

    def review(
        self,
        grounded_intent: GroundedIntent,
        plan: MissionPlan,
        capability_catalog: CanonicalCapabilityCatalog,
    ) -> MissionPlanReview:
        """Return structured approval evidence or blocking issues."""
        ...


class MissionPlanRepairer(Protocol):
    """Produce a replacement draft only from grounded facts and review evidence."""

    def repair(
        self,
        mission_id: str,
        grounded_intent: GroundedIntent,
        rejected_plan: MissionPlan,
        review: MissionPlanReview,
        capability_catalog: CanonicalCapabilityCatalog,
    ) -> MissionPlan:
        """Return a revised plan without answering user clarification questions."""
        ...
