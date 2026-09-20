"""Read-only evidence for the actual Mission Service HTTP submission boundary."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, cast

from mission.models import JSONObject

if TYPE_CHECKING:  # pragma: no cover - typing-only dependency keeps the modules acyclic
    from mission.rejected_draft import RejectedDraftEvidence

SUBMISSION_EVIDENCE_SCHEMA = "roboguide.controller-submission-evidence/v0.1"
OBSERVATIONS_SCHEMA = "roboguide.mission-request-observations/v0.1"


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
    schema_version: str = OBSERVATIONS_SCHEMA

    def to_json(self) -> JSONObject:
        """Bind the observations to the exact public request projection."""
        document = cast(JSONObject, asdict(self))
        drafts = document["rejected_drafts"]
        if isinstance(drafts, tuple | list):
            document["rejected_drafts"] = list(drafts)
        return document
