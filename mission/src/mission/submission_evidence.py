"""Read-only evidence for the actual Mission Service HTTP submission boundary."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, cast

from mission.models import JSONObject

if TYPE_CHECKING:  # pragma: no cover - typing-only dependency keeps the modules acyclic
    from mission.deployment_recovery import DeploymentRecoverySession
    from mission.recovery import RequestRecoveryEvidence
    from mission.rejected_draft import RejectedDraftEvidence

SUBMISSION_EVIDENCE_SCHEMA = "roboguide.controller-submission-evidence/v0.1"
OBSERVATIONS_SCHEMA = "roboguide.mission-request-observations/v0.3"
DEPLOYMENT_OBSERVATIONS_SCHEMA = "roboguide.mission-request-observations/v0.4"
COMPATIBLE_OBSERVATIONS_SCHEMAS = frozenset(
    {
        "roboguide.mission-request-observations/v0.1",
        "roboguide.mission-request-observations/v0.2",
        OBSERVATIONS_SCHEMA,
        DEPLOYMENT_OBSERVATIONS_SCHEMA,
    }
)


@dataclass(frozen=True, slots=True)
class ControllerAdmissionEvidence:
    """Read the Controller's atomic admission receipt without overwriting original POST evidence."""

    mission_id: str
    group_id: str
    accepted_request_body_sha256: str
    admitted_at_ms: int
    schema_version: str = "roboguide.controller-mission-admission/v0.1"

    def __post_init__(self) -> None:
        """Refuse partial identities, malformed digests and invented admission times."""
        if (
            self.schema_version != "roboguide.controller-mission-admission/v0.1"
            or any(
                not isinstance(value, str) or not value.strip() or len(value) > 256
                for value in (self.mission_id, self.group_id)
            )
            or not isinstance(self.accepted_request_body_sha256, str)
            or re.fullmatch(r"sha256:[a-f0-9]{64}", self.accepted_request_body_sha256) is None
            or type(self.admitted_at_ms) is not int
            or self.admitted_at_ms < 0
        ):
            raise ValueError("invalid Controller admission receipt")

    def to_json(self) -> JSONObject:
        """Serialize the actual authority receipt independently of transport observations."""
        return cast(JSONObject, asdict(self))

    @classmethod
    def from_json(cls, value: object) -> ControllerAdmissionEvidence:
        """Restore the exact neutral schema, failing closed on extra or missing fields."""
        if not isinstance(value, dict) or set(value) != {
            "schema_version",
            "mission_id",
            "group_id",
            "accepted_request_body_sha256",
            "admitted_at_ms",
        }:
            raise ValueError("malformed Controller admission receipt")
        return cls(**value)


def canonical_plan_digest(document: Mapping[str, object]) -> str:
    """Hash sorted, compact UTF-8 JSON, matching immutable MI draft identity."""
    encoded = json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


@dataclass(frozen=True, slots=True)
class ControllerSubmissionEvidence:
    """Observe sent bytes and the response without granting Controller authority."""

    submitted_plan_digest: str
    raw_request_body_sha256: str
    submitted_mission_id: str
    submitted_at_ms: int
    request_id: str | None = None
    controller_status_code: int | None = None
    controller_mission_id: str | None = None
    controller_group_id: str | None = None
    transport_error: str | None = None
    schema_version: str = SUBMISSION_EVIDENCE_SCHEMA

    def to_json(self) -> JSONObject:
        """Serialize only immutable submission observations."""
        return cast(JSONObject, asdict(self))

    @classmethod
    def from_json(cls, value: object) -> ControllerSubmissionEvidence:
        """Restore evidence separately from MissionPlan acceptance policy."""
        if not isinstance(value, dict) or value.get("schema_version") != SUBMISSION_EVIDENCE_SCHEMA:
            raise ValueError("unsupported Controller submission evidence")
        return cls(**value)

    @classmethod
    def from_body(cls, body: bytes, submitted_at_ms: int) -> ControllerSubmissionEvidence:
        """Digest the exact Request.data bytes passed to the HTTP transport."""
        document = json.loads(body)
        return cls(
            submitted_plan_digest=canonical_plan_digest(document),
            raw_request_body_sha256=f"sha256:{hashlib.sha256(body).hexdigest()}",
            submitted_mission_id=document["mission"]["id"],
            submitted_at_ms=submitted_at_ms,
        )


@dataclass(frozen=True, slots=True)
class MissionRequestObservations:
    """Separate observability from the unchanged Mission Request v0.4 response."""

    request_id: str
    mission_id: str
    request_record_digest: str
    submission_evidence: ControllerSubmissionEvidence | None
    failure_evidence: JSONObject | None
    rejected_drafts: tuple[RejectedDraftEvidence, ...] = ()
    recovery_evidence: RequestRecoveryEvidence | None = None
    admission_evidence: ControllerAdmissionEvidence | None = None
    deployment_recovery: DeploymentRecoverySession | None = None
    schema_version: str = OBSERVATIONS_SCHEMA

    def __post_init__(self) -> None:
        """Keep the object version and serialized version consistent with session availability."""
        if self.deployment_recovery is not None:
            object.__setattr__(self, "schema_version", DEPLOYMENT_OBSERVATIONS_SCHEMA)
        elif self.schema_version == DEPLOYMENT_OBSERVATIONS_SCHEMA:
            raise ValueError("v0.4 observations require a deployment recovery session")

    def to_json(self) -> JSONObject:
        """Bind the observations to the exact public request projection."""
        document = cast(JSONObject, asdict(self))
        drafts = document["rejected_drafts"]
        if isinstance(drafts, tuple | list):
            document["rejected_drafts"] = list(drafts)
        document["recovery_evidence"] = (
            self.recovery_evidence.to_json() if self.recovery_evidence is not None else None
        )
        if self.deployment_recovery is not None:
            document["deployment_recovery"] = self.deployment_recovery.to_json()
        else:
            document.pop("deployment_recovery")
        return document
