"""Typed, durable recovery decisions at the Mission deliberation/submission boundary.

This module classifies observations; it never calls a model, selects a Node,
changes a goal, or authorizes recovery of an active physical execution.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from mission.deployment_assessment import InitialOperationAssessment
from mission.models import JSONObject, MissionPlanError
from mission.provider_errors import MissionIdentityError, MissionProviderError
from mission.rejected_draft import RejectedPlanError

RECOVERY_SCHEMA = "roboguide.mission-request-recovery/v0.1"
ASSESSMENT_RECOVERY_SCHEMA = "roboguide.mission-request-recovery/v0.2"
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")


class FailureStage(StrEnum):
    """Name an owned boundary without inferring it from diagnostic text."""

    GROUNDING = "grounding"
    INTERPRETER = "interpreter"
    PLANNER = "planner"
    DRAFT_VALIDATION = "draft_validation"
    REVIEWER = "reviewer"
    REPAIRER = "repairer"
    CONTROLLER_SUBMISSION = "controller_submission"
    CONTROLLER_PREFLIGHT = "controller_preflight"


class FailureReason(StrEnum):
    """Describe a failure or a submission fence using stable, deployment-neutral codes."""

    DRAFT_INVALID = "draft_invalid"
    IDENTITY_VIOLATION = "identity_violation"
    PROVIDER_AUTHENTICATION = "provider_authentication"
    PROVIDER_TRANSPORT = "provider_transport"
    PROVIDER_TRANSIENT = "provider_transient"
    PROVIDER_REJECTION = "provider_rejection"
    PROVIDER_CONFIGURATION = "provider_configuration"
    PROVIDER_RESPONSE = "provider_response"
    DRAFT_REJECTED = "draft_rejected"
    REPAIR_BUDGET_EXHAUSTED = "repair_budget_exhausted"
    REPAIR_UNAVAILABLE = "repair_unavailable"
    INTERNAL_FAILURE = "internal_failure"
    DELIBERATION_INTERRUPTED = "deliberation_interrupted"
    SUBMISSION_IN_FLIGHT = "submission_in_flight"
    SUBMISSION_AMBIGUOUS = "submission_ambiguous"
    SUBMISSION_REJECTED = "submission_rejected"
    SUBMISSION_ACCEPTED = "submission_accepted"
    SUBMISSION_RECONCILED = "submission_reconciled"
    DEPLOYMENT_SUPPORT_BLOCKED = "deployment_support_blocked"
    DEPLOYMENT_ASSESSMENT_UNAVAILABLE = "deployment_assessment_unavailable"


class RecoveryAction(StrEnum):
    """Expose the next permitted boundary, rather than a generic retry flag."""

    RETRY_DELIBERATION = "retry_deliberation"
    CHECK_CONFIGURATION = "check_configuration"
    REVIEW_INPUT = "review_input"
    RESUBMIT_UNCHANGED = "resubmit_unchanged"
    RECONCILE_SUBMISSION = "reconcile_submission"
    OBSERVE_MISSION = "observe_mission"
    RECHECK_DEPLOYMENT = "recheck_deployment"


def classify_failure(error: Exception) -> FailureReason:
    """Classify exception types and structured status; messages cannot grant retry authority."""
    if isinstance(error, MissionIdentityError):
        return FailureReason.IDENTITY_VIOLATION
    if isinstance(error, RejectedPlanError | MissionPlanError):
        return FailureReason.DRAFT_INVALID
    if isinstance(error, MissionProviderError):
        if error.status_code in {401, 403}:
            return FailureReason.PROVIDER_AUTHENTICATION
        if error.configuration_failure:
            return FailureReason.PROVIDER_CONFIGURATION
        if error.status_code in {408, 429} or (
            error.status_code is not None and 500 <= error.status_code <= 599
        ):
            return FailureReason.PROVIDER_TRANSIENT
        if error.status_code is not None:
            return FailureReason.PROVIDER_REJECTION
        if error.transport_failure:
            return FailureReason.PROVIDER_TRANSPORT
        return FailureReason.PROVIDER_RESPONSE
    return FailureReason.INTERNAL_FAILURE


def recovery_action(stage: FailureStage, reason: FailureReason) -> RecoveryAction:
    """Choose a conservative action; only explicit Controller rejection permits another POST."""
    if stage is FailureStage.CONTROLLER_PREFLIGHT:
        return RecoveryAction.RECHECK_DEPLOYMENT
    if stage is FailureStage.CONTROLLER_SUBMISSION:
        if reason is FailureReason.SUBMISSION_REJECTED:
            return RecoveryAction.RESUBMIT_UNCHANGED
        if reason in {FailureReason.SUBMISSION_ACCEPTED, FailureReason.SUBMISSION_RECONCILED}:
            return RecoveryAction.OBSERVE_MISSION
        return RecoveryAction.RECONCILE_SUBMISSION
    if reason in {
        FailureReason.PROVIDER_AUTHENTICATION,
        FailureReason.PROVIDER_CONFIGURATION,
        FailureReason.PROVIDER_REJECTION,
    }:
        return RecoveryAction.CHECK_CONFIGURATION
    if reason in {FailureReason.IDENTITY_VIOLATION, FailureReason.DRAFT_REJECTED}:
        return RecoveryAction.REVIEW_INPUT
    return RecoveryAction.RETRY_DELIBERATION


@dataclass(frozen=True, slots=True)
class ControllerMissionObservation:
    """Retain an immutable, bounded identity lookup without admission or Runtime authority."""

    mission_id: str
    lookup_result: str
    status_code: int | None
    group_id: str | None
    mission_status: str | None
    error_type: str | None = None

    def __post_init__(self) -> None:
        """Reject unbounded text and contradictory HTTP/availability observations."""
        if self.lookup_result not in {"found", "not_found", "unavailable"}:
            raise ValueError("invalid Controller lookup result")
        for item in (self.mission_id, self.group_id, self.mission_status, self.error_type):
            if item is not None and (
                not isinstance(item, str) or not item.strip() or len(item) > 256
            ):
                raise ValueError("Controller lookup text is invalid or unbounded")
        if not self.mission_id:
            raise ValueError("Controller lookup requires Mission identity")
        if self.lookup_result == "found":
            if (
                type(self.status_code) is not int
                or self.status_code != 200
                or not self.group_id
                or not self.mission_status
            ):
                raise ValueError("found Controller lookup requires a matching HTTP observation")
        elif self.group_id is not None or self.mission_status is not None:
            raise ValueError("unavailable/not-found lookup cannot contain invented Mission facts")
        elif self.lookup_result == "not_found":
            if type(self.status_code) is not int or self.status_code != 404:
                raise ValueError("not-found lookup requires HTTP 404")
        elif self.status_code is not None:
            raise ValueError("unavailable lookup has no trustworthy HTTP status")

    def to_json(self) -> JSONObject:
        """Return a fresh, flat JSON observation without headers or response diagnostics."""
        return {
            "schema_version": "roboguide.controller-mission-observation/v0.1",
            "mission_id": self.mission_id,
            "lookup_result": self.lookup_result,
            "status_code": self.status_code,
            "group_id": self.group_id,
            "mission_status": self.mission_status,
            "error_type": self.error_type,
        }

    @classmethod
    def from_json(cls, value: object) -> ControllerMissionObservation:
        """Restore the admitted flat schema and reject arbitrary adapter JSON."""
        expected = {
            "schema_version",
            "mission_id",
            "lookup_result",
            "status_code",
            "group_id",
            "mission_status",
        }
        if (
            not isinstance(value, dict)
            or set(value) not in (expected, expected | {"error_type"})
            or (value.get("schema_version") != "roboguide.controller-mission-observation/v0.1")
        ):
            raise ValueError("Controller lookup is malformed")
        fields = dict(value)
        fields.pop("schema_version")
        return cls(**fields)


@dataclass(frozen=True, slots=True)
class RequestRecoveryEvidence:
    """Bind one recovery decision to the exact request/draft/context and retain a submission fence.

    A Controller lookup is an observation, never proof of an accepted plan's
    complete content or permission to replay an ambiguous POST.
    """

    request_id: str
    mission_id: str
    stage: FailureStage
    reason: FailureReason
    draft_revision: int
    draft_digest: str | None
    grounding_context_digest: str | None
    observed_at_ms: int
    controller_status_code: int | None = None
    controller_observation: ControllerMissionObservation | None = None
    deployment_assessment: InitialOperationAssessment | None = None

    def __post_init__(self) -> None:
        """Reject malformed identities, contradictory classifications and unbounded lookups."""
        if any(
            not isinstance(value, str) or not value.strip() or len(value) > 256
            for value in (self.request_id, self.mission_id)
        ):
            raise ValueError("recovery evidence requires request and Mission identity")
        for numeric in (self.draft_revision, self.observed_at_ms):
            if type(numeric) is not int or numeric < 0:
                raise ValueError("recovery revision/time must be nonnegative integers")
        for value in (self.draft_digest, self.grounding_context_digest):
            if value is not None and (not isinstance(value, str) or not _DIGEST.fullmatch(value)):
                raise ValueError("recovery evidence digest is invalid")
        if not isinstance(self.stage, FailureStage) or not isinstance(self.reason, FailureReason):
            raise ValueError("recovery stage/reason must be typed")
        if (
            self.reason
            in {
                FailureReason.SUBMISSION_IN_FLIGHT,
                FailureReason.SUBMISSION_AMBIGUOUS,
                FailureReason.SUBMISSION_REJECTED,
                FailureReason.SUBMISSION_ACCEPTED,
                FailureReason.SUBMISSION_RECONCILED,
            }
            and self.stage is not FailureStage.CONTROLLER_SUBMISSION
        ):
            raise ValueError("submission facts must remain at the Controller boundary")
        status = self.controller_status_code
        if status is not None and (type(status) is not int or not 100 <= status <= 599):
            raise ValueError("recovery Controller status is invalid")
        if self.reason is FailureReason.SUBMISSION_REJECTED and (
            self.stage is not FailureStage.CONTROLLER_SUBMISSION or status not in {400, 409, 422}
        ):
            raise ValueError("submission rejection requires a definitive Controller rejection")
        if self.reason is FailureReason.SUBMISSION_ACCEPTED and (
            self.stage is not FailureStage.CONTROLLER_SUBMISSION or status not in {200, 202}
        ):
            raise ValueError("submission acceptance requires a Controller receipt")
        observation = self.controller_observation
        if observation is not None:
            if not isinstance(observation, ControllerMissionObservation) or (
                observation.mission_id != self.mission_id
                or self.stage is not FailureStage.CONTROLLER_SUBMISSION
            ):
                raise ValueError("Controller lookup is detached or malformed")
        if self.reason in {
            FailureReason.DEPLOYMENT_SUPPORT_BLOCKED,
            FailureReason.DEPLOYMENT_ASSESSMENT_UNAVAILABLE,
        }:
            if self.stage is not FailureStage.CONTROLLER_PREFLIGHT or status is not None:
                raise ValueError("deployment feedback must remain before Controller submission")
        if self.stage is FailureStage.CONTROLLER_PREFLIGHT and self.reason not in {
            FailureReason.DEPLOYMENT_SUPPORT_BLOCKED,
            FailureReason.DEPLOYMENT_ASSESSMENT_UNAVAILABLE,
        }:
            raise ValueError("Controller preflight reason is invalid")
        assessment = self.deployment_assessment
        if assessment is not None and (
            not isinstance(assessment, InitialOperationAssessment)
            or assessment.mission_id != self.mission_id
        ):
            raise ValueError("deployment assessment is detached from the request")
        if assessment is not None and (
            self.stage
            not in {FailureStage.CONTROLLER_PREFLIGHT, FailureStage.CONTROLLER_SUBMISSION}
            or (
                self.stage is FailureStage.CONTROLLER_SUBMISSION
                and assessment.decision != "not_blocked"
            )
        ):
            raise ValueError("deployment assessment cannot authorize this recovery boundary")
        if self.reason is FailureReason.DEPLOYMENT_SUPPORT_BLOCKED and (
            assessment is None or assessment.decision != "blocked"
        ):
            raise ValueError("blocked deployment requires actual scoped support feedback")
        if (
            self.reason is FailureReason.DEPLOYMENT_ASSESSMENT_UNAVAILABLE
            and assessment is not None
            and assessment.decision != "unavailable"
        ):
            raise ValueError("unavailable deployment feedback contradicts its decision")

    @property
    def action(self) -> RecoveryAction:
        """Derive permitted action from typed facts, never from stored free-form diagnostics."""
        return recovery_action(self.stage, self.reason)

    def to_json(self) -> JSONObject:
        """Serialize versioned evidence independently of the Mission Request v0.4 projection."""
        output: JSONObject = {
            "schema_version": ASSESSMENT_RECOVERY_SCHEMA
            if self.deployment_assessment is not None
            or self.stage is FailureStage.CONTROLLER_PREFLIGHT
            else RECOVERY_SCHEMA,
            "request_id": self.request_id,
            "mission_id": self.mission_id,
            "stage": self.stage.value,
            "reason": self.reason.value,
            "action": self.action.value,
            "draft_revision": self.draft_revision,
            "draft_digest": self.draft_digest,
            "grounding_context_digest": self.grounding_context_digest,
            "observed_at_ms": self.observed_at_ms,
            "controller_status_code": self.controller_status_code,
            "controller_observation": self.controller_observation.to_json()
            if self.controller_observation is not None
            else None,
        }
        if output["schema_version"] == ASSESSMENT_RECOVERY_SCHEMA:
            output["deployment_assessment"] = (
                self.deployment_assessment.to_json()
                if self.deployment_assessment is not None
                else None
            )
        return output

    @classmethod
    def from_json(cls, value: object) -> RequestRecoveryEvidence:
        """Restore a decision without allowing a corrupt stored action to authorize resubmission."""
        if not isinstance(value, dict) or value.get("schema_version") not in {
            RECOVERY_SCHEMA,
            ASSESSMENT_RECOVERY_SCHEMA,
        }:
            raise ValueError("unsupported Mission recovery evidence")
        fields = dict(value)
        schema = fields.pop("schema_version")
        if (schema == ASSESSMENT_RECOVERY_SCHEMA) != ("deployment_assessment" in fields):
            raise ValueError("recovery assessment must declare its schema and availability")
        if fields.get("stage") == FailureStage.CONTROLLER_PREFLIGHT.value and (
            schema != ASSESSMENT_RECOVERY_SCHEMA
        ):
            raise ValueError("Controller preflight requires the assessment recovery schema")
        assessment = fields.get("deployment_assessment")
        fields["deployment_assessment"] = (
            InitialOperationAssessment.from_json(assessment) if assessment is not None else None
        )
        action = fields.pop("action", None)
        fields["stage"] = FailureStage(fields["stage"])
        fields["reason"] = FailureReason(fields["reason"])
        observation = fields.get("controller_observation")
        fields["controller_observation"] = (
            ControllerMissionObservation.from_json(observation) if observation is not None else None
        )
        evidence = cls(**fields)
        if action != evidence.action.value:
            raise ValueError("recovery action contradicts its source evidence")
        return evidence

    def observe_controller(self, value: JSONObject) -> RequestRecoveryEvidence:
        """Return a copy with one bounded read-only lookup, retaining the original decision/time."""
        from dataclasses import replace

        return replace(self, controller_observation=ControllerMissionObservation.from_json(value))
