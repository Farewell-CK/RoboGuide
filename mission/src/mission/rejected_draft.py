"""Rejected Planner and Repairer draft evidence with pre-validation errors.

A generated draft that fails DTO normalization or MissionPlan validation is
preserved verbatim before any recovery attempt: the provider's raw output,
the normalized candidate when normalization succeeded, structured failure
reasons, and non-sensitive provider identity. Evidence lives on the
observations side of the request store (never in the public status
projection) and is versioned so old records without it stay readable.

``RejectedPlanError`` carries the rejected outputs out of a model adapter so
the engine can persist evidence and decide on bounded regeneration
without the Planner keeping cross-request state. It is raised only for
model-draft structure errors (``MissionPlanError`` family); provider
transport, authentication, and task-identity violations stay
``MissionProviderError`` and never trigger recovery.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, cast

from mission.contract_values import JSONObject, JSONValue, MissionPlanError, _object, _text
from mission.submission_evidence import canonical_plan_digest

LEGACY_REJECTED_DRAFT_SCHEMA = "roboguide.mission.rejected-draft/v0.1"
REJECTED_DRAFT_SCHEMA = "roboguide.mission.rejected-draft/v0.2"
MAX_PROVIDER_OUTPUT_BYTES = 256 * 1024
_VALIDATION_STAGES = ("normalization", "plan_validation")
_DELIBERATION_STAGES = ("planner", "repairer")


class RejectedPlanError(MissionPlanError):
    """One model-draft structure failure with its raw and normalized outputs.

    Attributes:
        stage: Where the draft failed: ``normalization`` or
            ``plan_validation``.
        provider_output: The provider's raw structured output.
        normalized_output: The normalized candidate when normalization
            succeeded; ``None`` when normalization itself failed.
        generated_at_ms: When the provider output was received.
    """

    def __init__(
        self,
        message: str,
        *,
        stage: str,
        provider_output: JSONObject,
        normalized_output: JSONObject | None,
        generated_at_ms: int,
    ) -> None:
        """Attach the failed draft payloads to the validation message."""
        super().__init__(message)
        if stage not in _VALIDATION_STAGES:
            raise ValueError(f"unknown rejection stage {stage!r}")
        self.stage = stage
        self.provider_output = provider_output
        self.normalized_output = normalized_output
        self.generated_at_ms = generated_at_ms


@dataclass(frozen=True, slots=True)
class RejectedDraftEvidence:
    """Persist one rejected model attempt for later audit and recovery.

    Attributes:
        request_id: The owning Mission Request.
        mission_id: The Mission identity the draft was generated for.
        attempt_index: One-based position among this request's attempts.
        attempt_id: Stable identity binding request, attempt, and content.
        stage: ``normalization`` or ``plan_validation``.
        deliberation_stage: Planner or Repairer origin of the generated draft.
        validation_errors: Structured failure reasons from the rejection.
        provider_output: The provider's raw structured output, possibly
            size-truncated with an explicit marker.
        provider_output_digest: Digest of the untruncated raw output.
        normalized_output: The normalized candidate when available.
        normalized_output_digest: Digest of the normalized candidate.
        grounding_context_digest: Frozen grounding snapshot identity.
        semantic_evidence_digest: Frozen authoritative semantics identity.
        provider_identity: Non-sensitive model/provider configuration.
        generated_at_ms: Provider output reception time.
        persisted_at_ms: Evidence persistence time.
    """

    request_id: str
    mission_id: str
    attempt_index: int
    attempt_id: str
    stage: str
    validation_errors: tuple[JSONObject, ...]
    provider_output: JSONObject
    provider_output_digest: str
    normalized_output: JSONObject | None
    normalized_output_digest: str | None
    grounding_context_digest: str | None
    semantic_evidence_digest: str | None
    provider_identity: JSONObject
    generated_at_ms: int
    persisted_at_ms: int
    deliberation_stage: str = "planner"
    schema_version: str = REJECTED_DRAFT_SCHEMA

    def __post_init__(self) -> None:
        """Keep a legacy Planner record distinct from a versioned Repairer record."""
        if self.schema_version not in {LEGACY_REJECTED_DRAFT_SCHEMA, REJECTED_DRAFT_SCHEMA}:
            raise ValueError("unsupported rejected draft evidence schema")
        if self.deliberation_stage not in _DELIBERATION_STAGES:
            raise ValueError("unsupported rejected draft deliberation stage")
        if (
            self.schema_version == LEGACY_REJECTED_DRAFT_SCHEMA
            and self.deliberation_stage != "planner"
        ):
            raise ValueError("legacy rejected draft evidence can only describe Planner output")

    def verify_integrity(self) -> None:
        """Check that stored content still matches its recorded digests.

        Untruncated raw and normalized payloads must rehash to their
        recorded digests; truncated raw payloads skip content rehashing
        (the digest deliberately covers the untruncated bytes) but must
        carry the explicit truncation marker. Raises ``ValueError`` on any
        mismatch or malformed marker so restore fails closed instead of
        trusting a bare digest string.
        """
        raw = self.provider_output
        truncated = raw.get("truncated") if isinstance(raw, dict) else None
        if truncated is True:
            if not isinstance(raw.get("byte_length"), int):
                raise ValueError("truncated rejected draft lacks its byte length")
        elif (
            canonical_plan_digest(raw) != self.provider_output_digest
            if isinstance(raw, dict)
            else True
        ):
            raise ValueError("rejected draft raw output does not match its digest")
        normalized = self.normalized_output
        if normalized is not None:
            digest_value = self.normalized_output_digest
            if (
                not isinstance(digest_value, str)
                or canonical_plan_digest(normalized) != digest_value
            ):
                raise ValueError("rejected draft normalized output does not match its digest")

    def to_json(self) -> JSONObject:
        """Serialize the evidence document for durable observations."""
        document: JSONObject = {
            "schema_version": self.schema_version,
            "request_id": self.request_id,
            "mission_id": self.mission_id,
            "attempt_index": self.attempt_index,
            "attempt_id": self.attempt_id,
            "stage": self.stage,
            **(
                {"deliberation_stage": self.deliberation_stage}
                if self.schema_version == REJECTED_DRAFT_SCHEMA
                else {}
            ),
            "validation_errors": [dict(error) for error in self.validation_errors],
            "provider_output": dict(self.provider_output),
            "provider_output_digest": self.provider_output_digest,
            "normalized_output": (
                dict(self.normalized_output) if self.normalized_output is not None else None
            ),
            "normalized_output_digest": self.normalized_output_digest,
            "grounding_context_digest": self.grounding_context_digest,
            "semantic_evidence_digest": self.semantic_evidence_digest,
            "provider_identity": dict(self.provider_identity),
            "generated_at_ms": self.generated_at_ms,
            "persisted_at_ms": self.persisted_at_ms,
        }
        return document

    @classmethod
    def from_json(cls, value: object) -> RejectedDraftEvidence:
        """Restore one evidence document, rejecting unknown schema versions.

        Raises:
            ValueError: On unsupported schemas or malformed fields.
        """
        item = _object(cast("JSONValue", value), "rejected draft evidence")
        schema_version = item.get("schema_version")
        if not isinstance(schema_version, str) or schema_version not in {
            LEGACY_REJECTED_DRAFT_SCHEMA,
            REJECTED_DRAFT_SCHEMA,
        }:
            raise ValueError("unsupported rejected draft evidence schema")
        required = {
            "schema_version",
            "request_id",
            "mission_id",
            "attempt_index",
            "attempt_id",
            "stage",
            "validation_errors",
            "provider_output",
            "provider_output_digest",
            "normalized_output",
            "normalized_output_digest",
            "grounding_context_digest",
            "semantic_evidence_digest",
            "provider_identity",
            "generated_at_ms",
            "persisted_at_ms",
        }
        if schema_version == REJECTED_DRAFT_SCHEMA:
            required.add("deliberation_stage")
        if set(item) != required:
            raise ValueError("rejected draft evidence fields do not match its schema")
        stage = _text(item["stage"], "rejected draft stage")
        if stage not in _VALIDATION_STAGES:
            raise ValueError("unsupported rejected draft stage")
        deliberation_stage = (
            "planner"
            if schema_version == LEGACY_REJECTED_DRAFT_SCHEMA
            else _text(item["deliberation_stage"], "rejected draft deliberation_stage")
        )
        if deliberation_stage not in _DELIBERATION_STAGES:
            raise ValueError("unsupported rejected draft deliberation stage")
        attempt_index = item["attempt_index"]
        if not isinstance(attempt_index, int) or isinstance(attempt_index, bool):
            raise ValueError("rejected draft attempt index must be an integer")
        errors_value = item["validation_errors"]
        if not isinstance(errors_value, list):
            raise ValueError("rejected draft validation errors must be a list")
        normalized = item["normalized_output"]
        return cls(
            request_id=_text(item["request_id"], "rejected draft request_id"),
            mission_id=_text(item["mission_id"], "rejected draft mission_id"),
            attempt_index=attempt_index,
            attempt_id=_text(item["attempt_id"], "rejected draft attempt_id"),
            stage=stage,
            validation_errors=tuple(
                dict(_object(error, "rejected draft error")) for error in errors_value
            ),
            provider_output=_object(item["provider_output"], "rejected draft provider output"),
            provider_output_digest=_text(
                item["provider_output_digest"], "rejected draft provider digest"
            ),
            normalized_output=(
                _object(normalized, "rejected draft normalized output")
                if normalized is not None
                else None
            ),
            normalized_output_digest=(
                _text(
                    item["normalized_output_digest"],
                    "rejected draft normalized digest",
                )
                if item["normalized_output_digest"] is not None
                else None
            ),
            grounding_context_digest=(
                _text(
                    item["grounding_context_digest"],
                    "rejected draft grounding digest",
                )
                if item["grounding_context_digest"] is not None
                else None
            ),
            semantic_evidence_digest=(
                _text(
                    item["semantic_evidence_digest"],
                    "rejected draft semantic digest",
                )
                if item["semantic_evidence_digest"] is not None
                else None
            ),
            provider_identity=_object(
                item["provider_identity"], "rejected draft provider identity"
            ),
            generated_at_ms=_integer(item["generated_at_ms"], "rejected draft generated_at_ms"),
            persisted_at_ms=_integer(item["persisted_at_ms"], "rejected draft persisted_at_ms"),
            deliberation_stage=deliberation_stage,
            schema_version=schema_version,
        )


def _integer(value: JSONValue, name: str) -> int:
    """Require one integer field.

    Raises:
        ValueError: When the field is not an integer.
    """
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    return value


def bounded_provider_output(output: JSONObject) -> tuple[JSONObject, bool]:
    """Bound one raw provider output, marking truncation explicitly.

    Returns:
        The (possibly truncated) output document and whether truncation
        happened. Truncation replaces the payload with a marker while the
        untruncated digest stays on the evidence for identity.
    """
    encoded = _stable_json(output)
    if len(encoded) <= MAX_PROVIDER_OUTPUT_BYTES:
        return output, False
    return {
        "truncated": True,
        "byte_length": len(encoded),
        "reason": "provider output exceeded the rejected-draft size bound",
    }, True


def provider_output_digest(output: JSONObject) -> str:
    """Digest the untruncated raw provider output for identity binding."""
    return canonical_plan_digest(output)


def build_rejected_draft_evidence(
    *,
    request_id: str,
    mission_id: str,
    attempt_index: int,
    error: RejectedPlanError,
    grounding_context_digest: str | None,
    semantic_evidence_digest: str | None,
    provider_identity: JSONObject,
    persisted_at_ms: int,
    deliberation_stage: str = "planner",
) -> RejectedDraftEvidence:
    """Assemble one evidence document from a rejected Planner or Repairer attempt."""
    if deliberation_stage not in _DELIBERATION_STAGES:
        raise ValueError("unsupported rejected draft deliberation stage")
    output, _truncated = bounded_provider_output(error.provider_output)
    normalized = error.normalized_output
    return RejectedDraftEvidence(
        request_id=request_id,
        mission_id=mission_id,
        attempt_index=attempt_index,
        attempt_id=(
            f"rejected-{request_id}-{attempt_index}-"
            f"{provider_output_digest(error.provider_output).removeprefix('sha256:')[:12]}"
        ),
        stage=error.stage,
        validation_errors=({"stage": error.stage, "message": str(error)},),
        provider_output=output,
        provider_output_digest=provider_output_digest(error.provider_output),
        normalized_output=normalized,
        normalized_output_digest=(
            canonical_plan_digest(normalized) if normalized is not None else None
        ),
        grounding_context_digest=grounding_context_digest,
        semantic_evidence_digest=semantic_evidence_digest,
        provider_identity=provider_identity,
        generated_at_ms=error.generated_at_ms,
        persisted_at_ms=persisted_at_ms,
        deliberation_stage=deliberation_stage,
    )


def _stable_json(value: Any) -> bytes:
    """Encode one value with the canonical sorted compact JSON form."""
    import json

    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
