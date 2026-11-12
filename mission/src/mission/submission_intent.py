"""Explicit request-owned permission to submit a reviewed MissionPlan."""

from __future__ import annotations

from dataclasses import dataclass

from mission.models import JSONObject

INTENT_REQUEST_SCHEMA = "roboguide.mission-request/v0.5"
MODES = frozenset({"draft_only", "submit_when_ready"})


@dataclass(frozen=True, slots=True)
class SubmissionIntent:
    """Keep original request intent separate from an exact later draft execution grant."""

    mode: str
    revision: int | None = None
    digest: str | None = None

    def __post_init__(self) -> None:
        """Refuse incomplete or ambiguous grants before they can reach persistence."""
        if self.mode not in MODES:
            raise ValueError("unsupported submission mode")
        if (self.revision is None) != (self.digest is None):
            raise ValueError("execution grant requires revision and digest")
        if self.revision is not None and (
            type(self.revision) is not int
            or self.revision < 1
            or not isinstance(self.digest, str)
            or not self.digest.strip()
            or self.mode != "draft_only"
        ):
            raise ValueError("invalid exact draft execution grant")

    def permits(self, revision: int, digest: str | None) -> bool:
        """Return original request permission or an exact unchanged draft grant."""
        return self.mode == "submit_when_ready" or (
            self.revision is not None and self.revision == revision and self.digest == digest
        )

    def to_json(self) -> JSONObject:
        """Serialize mandatory mode and explicit grant availability without implicit defaults."""
        return {"mode": self.mode, "draft_revision": self.revision, "draft_digest": self.digest}

    @classmethod
    def from_json(cls, value: object) -> SubmissionIntent:
        """Restore a closed submission authority document, refusing missing evidence."""
        if not isinstance(value, dict) or set(value) != {"mode", "draft_revision", "draft_digest"}:
            raise ValueError("submission intent fields do not match v0.5")
        mode, revision, digest = value["mode"], value["draft_revision"], value["draft_digest"]
        if not isinstance(mode, str) or (revision is not None and type(revision) is not int):
            raise ValueError("invalid submission intent types")
        if digest is not None and not isinstance(digest, str):
            raise ValueError("invalid execution grant digest")
        return cls(mode, revision, digest)
