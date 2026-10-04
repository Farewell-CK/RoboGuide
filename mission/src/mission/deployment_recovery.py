"""Bounded MI decisions over neutral pre-submission feedback, never executor authority."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from mission.deployment_assessment import DIAGNOSTIC_SCHEMA, InitialOperationAssessment
from mission.models import JSONObject, MissionPlan
from mission.submission_evidence import canonical_plan_digest

SESSION_SCHEMA = "roboguide.deployment-recovery-session/v0.1"
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
MAX_ATTEMPTS = 3
MAX_DURATION_MS = 900_000
MAX_DOCUMENT_BYTES = 262_144


class DeploymentRecoveryAction(StrEnum):
    """Separate a model's proposal from the authority that may perform the next effect."""

    RECHECK = "recheck"
    REVISE = "revise_plan"
    WAIT = "wait_for_evidence"


def frozen_document(value: JSONObject) -> str:
    """Keep a defensive, bounded JSON copy rather than mutable nested provider evidence."""
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )
    if len(encoded.encode()) > MAX_DOCUMENT_BYTES:
        raise ValueError("deployment recovery document exceeds evidence budget")
    return encoded


@dataclass(frozen=True, slots=True)
class DeploymentRecoveryDecision:
    """Retain one reasoned proposal; a replacement remains an unapproved draft."""

    action: DeploymentRecoveryAction
    explanation: str
    replacement_plan: MissionPlan | None = None

    def __post_init__(self) -> None:
        """Require a bounded explanation and a plan only for semantic revision."""
        if (
            not isinstance(self.action, DeploymentRecoveryAction)
            or not isinstance(self.explanation, str)
            or not self.explanation.strip()
            or len(self.explanation) > 4096
        ):
            raise ValueError("deployment recovery decision is invalid")
        if (
            self.action is DeploymentRecoveryAction.REVISE
            and not isinstance(self.replacement_plan, MissionPlan)
        ) or (
            self.action is not DeploymentRecoveryAction.REVISE and self.replacement_plan is not None
        ):
            raise ValueError("only revise_plan requires a replacement MissionPlan")

    def to_json(self) -> JSONObject:
        """Serialize the proposed action without claiming Review or Controller acceptance."""
        return {
            "action": self.action.value,
            "explanation": self.explanation,
            "replacement_plan": self.replacement_plan.to_json() if self.replacement_plan else None,
        }

    @classmethod
    def from_json(cls, value: Any) -> DeploymentRecoveryDecision:
        """Restore only a closed, canonical decision; provider normalization is separate."""
        if not isinstance(value, dict) or set(value) != {
            "action",
            "explanation",
            "replacement_plan",
        }:
            raise ValueError("deployment recovery decision fields are invalid")
        return cls(
            DeploymentRecoveryAction(value["action"]),
            value["explanation"],
            MissionPlan.from_json(value["replacement_plan"])
            if value["replacement_plan"] is not None
            else None,
        )


@dataclass(frozen=True, slots=True)
class DeploymentRecoveryAttempt:
    """Freeze the exact reviewed draft and feedback before consuming one model-call budget."""

    attempt_index: int
    input_plan_json: str
    input_plan_digest: str
    grounding_context_digest: str
    assessment: InitialOperationAssessment
    started_at_ms: int
    finished_at_ms: int | None = None
    outcome: str = "pending"
    decision: DeploymentRecoveryDecision | None = None
    provider_output_json: str | None = None
    error_type: str | None = None

    def __post_init__(self) -> None:
        """Reject detached, oversized or contradictory attempt evidence on restore."""
        if type(self.attempt_index) is not int or not 1 <= self.attempt_index <= MAX_ATTEMPTS:
            raise ValueError("deployment recovery attempt index is invalid")
        for digest in (self.input_plan_digest, self.grounding_context_digest):
            if not isinstance(digest, str) or not _DIGEST.fullmatch(digest):
                raise ValueError("deployment recovery digest is invalid")
        if not isinstance(self.input_plan_json, str):
            raise ValueError("deployment recovery input plan is required")
        for encoded in (self.input_plan_json, self.provider_output_json):
            if encoded is not None and (
                not isinstance(encoded, str)
                or len(encoded.encode()) > MAX_DOCUMENT_BYTES
                or not isinstance(json.loads(encoded), dict)
            ):
                raise ValueError("deployment recovery document is invalid")
        plan = MissionPlan.from_json(json.loads(self.input_plan_json))
        if (
            canonical_plan_digest(plan.to_json()) != self.input_plan_digest
            or not isinstance(self.assessment, InitialOperationAssessment)
            or not self.assessment.matches_plan(plan)
        ):
            raise ValueError("deployment recovery feedback is detached from input plan")
        if (
            self.assessment.schema_version != DIAGNOSTIC_SCHEMA
            or self.assessment.decision == "not_blocked"
            or self.assessment.reason_code
            in {
                "source_unconfigured",
                "source_expired",
                "controller_restored",
                "world_already_admitted",
            }
            or any(
                value is None
                for value in (
                    self.assessment.source_digest,
                    self.assessment.world_snapshot_digest,
                    self.assessment.local_how_digest,
                )
            )
            or self.assessment.received_at_ms is None
            or self.assessment.expires_at_ms is None
            or not self.assessment.received_at_ms
            <= self.assessment.assessed_at_ms
            < self.assessment.expires_at_ms
            or not 0 < self.assessment.expires_at_ms - self.assessment.received_at_ms <= 600_000
        ):
            raise ValueError("deployment recovery requires fresh attributed negative feedback")
        if self.decision is not None and not isinstance(self.decision, DeploymentRecoveryDecision):
            raise ValueError("deployment recovery decision is invalid")
        if (
            self.decision is not None
            and self.decision.replacement_plan is not None
            and (self.decision.replacement_plan.mission.mission_id != plan.mission.mission_id)
        ):
            raise ValueError("deployment recovery proposal changes Mission identity")
        if (
            type(self.started_at_ms) is not int
            or self.started_at_ms < 0
            or (
                self.finished_at_ms is not None
                and (
                    type(self.finished_at_ms) is not int or self.finished_at_ms < self.started_at_ms
                )
            )
        ):
            raise ValueError("deployment recovery attempt time is invalid")
        if self.outcome not in {"pending", "decided", "failed", "interrupted", "expired"}:
            raise ValueError("deployment recovery attempt outcome is invalid")
        if (
            (self.outcome == "pending") != (self.finished_at_ms is None)
            or (self.outcome == "decided" and self.decision is None)
            or (self.outcome in {"pending", "failed", "interrupted"} and self.decision is not None)
            or (self.outcome == "pending" and self.provider_output_json is not None)
            or (self.outcome == "failed" and self.error_type is None)
            or (self.outcome in {"pending", "decided", "expired"} and self.error_type is not None)
        ):
            raise ValueError("deployment recovery outcome contradicts decision/time")
        if self.error_type is not None and (
            not isinstance(self.error_type, str)
            or not self.error_type.strip()
            or len(self.error_type) > 256
        ):
            raise ValueError("deployment recovery error type is invalid")

    def to_json(self) -> JSONObject:
        """Expose defensive full input/output evidence with exact draft/context binding."""
        return {
            "attempt_index": self.attempt_index,
            "input_plan": json.loads(self.input_plan_json),
            "input_plan_digest": self.input_plan_digest,
            "grounding_context_digest": self.grounding_context_digest,
            "assessment": self.assessment.to_json(),
            "started_at_ms": self.started_at_ms,
            "finished_at_ms": self.finished_at_ms,
            "outcome": self.outcome,
            "decision": self.decision.to_json() if self.decision else None,
            "provider_output": json.loads(self.provider_output_json)
            if self.provider_output_json
            else None,
            "error_type": self.error_type,
        }

    @classmethod
    def from_json(cls, value: Any) -> DeploymentRecoveryAttempt:
        """Restore full immutable evidence without letting arbitrary fields drive retry."""
        expected = (set(cls.__dataclass_fields__) - {"input_plan_json", "provider_output_json"}) | {
            "input_plan",
            "provider_output",
        }
        if not isinstance(value, dict) or set(value) != expected:
            raise ValueError("deployment recovery attempt fields are invalid")
        fields = dict(value)
        fields["input_plan_json"] = frozen_document(fields.pop("input_plan"))
        output = fields.pop("provider_output")
        fields["provider_output_json"] = frozen_document(output) if output is not None else None
        fields["assessment"] = InitialOperationAssessment.from_json(fields["assessment"])
        fields["decision"] = (
            DeploymentRecoveryDecision.from_json(fields["decision"])
            if fields["decision"] is not None
            else None
        )
        return cls(**fields)


@dataclass(frozen=True, slots=True)
class DeploymentRecoverySession:
    """Keep request-wide, nonrenewable count/time bounds across explicit retries and restarts."""

    request_id: str
    mission_id: str
    max_attempts: int
    started_at_ms: int
    expires_at_ms: int
    attempts: tuple[DeploymentRecoveryAttempt, ...] = ()

    def __post_init__(self) -> None:
        """Validate ordered attempts and monotonic local time; Controller clocks remain separate."""
        if any(
            not isinstance(item, str) or not item.strip() or len(item) > 256
            for item in (self.request_id, self.mission_id)
        ):
            raise ValueError("deployment recovery session identity is invalid")
        if type(self.max_attempts) is not int or not 1 <= self.max_attempts <= MAX_ATTEMPTS:
            raise ValueError("deployment recovery session count bound is invalid")
        if (
            type(self.started_at_ms) is not int
            or type(self.expires_at_ms) is not int
            or self.started_at_ms < 0
            or not 0 < self.expires_at_ms - self.started_at_ms <= MAX_DURATION_MS
        ):
            raise ValueError("deployment recovery session time bound is invalid")
        if not isinstance(self.attempts, tuple) or len(self.attempts) > self.max_attempts:
            raise ValueError("deployment recovery attempts exceed durable budget")
        prior_time = self.started_at_ms
        source_identity = None
        for index, attempt in enumerate(self.attempts, 1):
            if (
                not isinstance(attempt, DeploymentRecoveryAttempt)
                or attempt.attempt_index != index
                or attempt.started_at_ms < prior_time
                or attempt.started_at_ms >= self.expires_at_ms
                or (attempt.outcome == "pending" and index != len(self.attempts))
                or (
                    attempt.outcome == "decided"
                    and attempt.finished_at_ms is not None
                    and attempt.finished_at_ms >= self.expires_at_ms
                )
                or (
                    attempt.outcome == "expired"
                    and attempt.finished_at_ms is not None
                    and attempt.finished_at_ms < self.expires_at_ms
                )
                or MissionPlan.from_json(json.loads(attempt.input_plan_json)).mission.mission_id
                != self.mission_id
            ):
                raise ValueError("deployment recovery attempt ordering or identity is invalid")
            identity = (
                attempt.grounding_context_digest,
                attempt.assessment.source_digest,
                attempt.assessment.world_snapshot_digest,
                attempt.assessment.local_how_digest,
                attempt.assessment.received_at_ms,
                attempt.assessment.expires_at_ms,
            )
            if source_identity is not None and identity != source_identity:
                raise ValueError("deployment recovery cannot replace its frozen context/source")
            source_identity = identity
            prior_time = (
                attempt.finished_at_ms
                if attempt.finished_at_ms is not None
                else attempt.started_at_ms
            )

    def to_json(self) -> JSONObject:
        """Serialize the count/time fence independently of public Mission Request v0.4."""
        return {
            "schema_version": SESSION_SCHEMA,
            "request_id": self.request_id,
            "mission_id": self.mission_id,
            "max_attempts": self.max_attempts,
            "started_at_ms": self.started_at_ms,
            "expires_at_ms": self.expires_at_ms,
            "attempts": [item.to_json() for item in self.attempts],
        }

    @classmethod
    def from_json(cls, value: Any) -> DeploymentRecoverySession:
        """Restore bounded evidence without resetting policy or inventing unfinished responses."""
        if (
            not isinstance(value, dict)
            or set(value) != set(cls.__dataclass_fields__) | {"schema_version"}
            or value["schema_version"] != SESSION_SCHEMA
            or not isinstance(value["attempts"], list)
        ):
            raise ValueError("deployment recovery session fields are invalid")
        fields = dict(value)
        fields.pop("schema_version")
        fields["attempts"] = tuple(
            DeploymentRecoveryAttempt.from_json(item) for item in fields["attempts"]
        )
        return cls(**fields)
