"""Immutable, source-aware evidence supplied to Mission deliberation."""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import dataclass
from enum import StrEnum
from typing import cast

from mission.models import JSONObject, JSONValue

GROUNDING_CONTEXT_SCHEMA = "roboguide.grounding-context/v0.1"
GROUNDING_SELECTION_POLICY = "roboguide.mission-grounding/admitted-world-and-global-memory/v0.2"
MAX_GROUNDING_CONTEXT_BYTES = 512 * 1024
EMPTY_GROUNDING_SELECTION_POLICY_REF = (
    f"{GROUNDING_SELECTION_POLICY}#world-payload-schemas="
    "sha256:4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
)
_DIGEST = re.compile(r"^sha256:[a-f0-9]{64}$")


class GroundingContextError(ValueError):
    """Report malformed or internally inconsistent Mission grounding evidence."""


class GroundingContextSizeError(GroundingContextError):
    """Report a snapshot whose canonical representation exceeds the local hard limit."""


class GroundingFreshness(StrEnum):
    """Preserve the Controller's receive-relative freshness assessment."""

    FRESH = "Fresh"
    STALE = "Stale"


class MemoryContentStatus(StrEnum):
    """Describe how much of one discovered Memory revision entered the snapshot."""

    METADATA_ONLY = "MetadataOnly"


@dataclass(frozen=True, slots=True)
class StateGroundingEvidence:
    """Retain one World State record without merging or changing its attribution."""

    evidence_id: str
    object_type: str
    object_id: str
    semantic: str
    source: str
    channel_id: str
    payload_schema: str
    value: JSONValue
    source_observed_at_ms: int | None
    received_at_ms: int
    valid_for_ms: int
    freshness: GroundingFreshness
    confidence_millionths: int | None
    source_epoch: str | None
    sequence: int

    def __post_init__(self) -> None:
        """Reject evidence that cannot preserve the existing State envelope semantics."""
        for field, value in (
            ("evidence_id", self.evidence_id),
            ("object_type", self.object_type),
            ("object_id", self.object_id),
            ("semantic", self.semantic),
            ("source", self.source),
            ("channel_id", self.channel_id),
            ("payload_schema", self.payload_schema),
        ):
            _require_text(value, field)
        if self.semantic not in {"reported", "observed", "derived", "belief"}:
            raise GroundingContextError("State grounding evidence has a forbidden semantic")
        if not isinstance(self.freshness, GroundingFreshness):
            raise GroundingContextError("State grounding freshness must be explicit")
        _require_nonnegative(self.received_at_ms, "received_at_ms")
        _require_positive(self.valid_for_ms, "valid_for_ms")
        _require_nonnegative(self.sequence, "sequence")
        if self.source_observed_at_ms is not None:
            _require_nonnegative(self.source_observed_at_ms, "source_observed_at_ms")
        if self.confidence_millionths is not None:
            if (
                isinstance(self.confidence_millionths, bool)
                or not isinstance(self.confidence_millionths, int)
                or not 0 <= self.confidence_millionths <= 1_000_000
            ):
                raise GroundingContextError("confidence_millionths must be between 0 and 1000000")
        if self.source_epoch is not None:
            _require_text(self.source_epoch, "source_epoch")
        object.__setattr__(self, "value", _clone_json(self.value, "state evidence value"))

    def __getattribute__(self, name: str) -> object:
        """Return defensive JSON copies so frozen evidence cannot be mutated through containers."""
        value = object.__getattribute__(self, name)
        if name == "value":
            return _clone_json(cast(JSONValue, value), "state evidence value")
        return value

    def to_json(self) -> JSONObject:
        """Serialize one exact attributed World State record for model input and persistence."""
        return {
            "evidence_id": self.evidence_id,
            "object": {
                "class": "world",
                "object_type": self.object_type,
                "object_id": self.object_id,
            },
            "semantic": self.semantic,
            "source": self.source,
            "channel_id": self.channel_id,
            "payload_schema": self.payload_schema,
            "value": _clone_json(self.value, "state evidence value"),
            "source_observed_at_ms": self.source_observed_at_ms,
            "received_at_ms": self.received_at_ms,
            "valid_for_ms": self.valid_for_ms,
            "freshness": self.freshness.value,
            "confidence_millionths": self.confidence_millionths,
            "source_epoch": self.source_epoch,
            "sequence": self.sequence,
        }

    @classmethod
    def from_json(cls, value: object) -> StateGroundingEvidence:
        """Restore one State item and reject deployment or Control records at the boundary."""
        item = _object(value, "state evidence")
        expected = {
            "evidence_id",
            "object",
            "semantic",
            "source",
            "channel_id",
            "payload_schema",
            "value",
            "source_observed_at_ms",
            "received_at_ms",
            "valid_for_ms",
            "freshness",
            "confidence_millionths",
            "source_epoch",
            "sequence",
        }
        _require_fields(item, expected, "state evidence")
        object_ref = _object(item["object"], "state evidence.object")
        _require_fields(object_ref, {"class", "object_type", "object_id"}, "state evidence.object")
        if object_ref["class"] != "world":
            raise GroundingContextError("grounding accepts only World State records")
        try:
            freshness = GroundingFreshness(_text(item, "freshness"))
        except ValueError as error:
            raise GroundingContextError("state evidence has unknown freshness") from error
        return cls(
            evidence_id=_text(item, "evidence_id"),
            object_type=_text(object_ref, "object_type"),
            object_id=_text(object_ref, "object_id"),
            semantic=_text(item, "semantic"),
            source=_text(item, "source"),
            channel_id=_text(item, "channel_id"),
            payload_schema=_text(item, "payload_schema"),
            value=item["value"],
            source_observed_at_ms=_optional_integer(item, "source_observed_at_ms"),
            received_at_ms=_integer(item, "received_at_ms"),
            valid_for_ms=_integer(item, "valid_for_ms"),
            freshness=freshness,
            confidence_millionths=_optional_integer(item, "confidence_millionths"),
            source_epoch=_optional_text(item, "source_epoch"),
            sequence=_integer(item, "sequence"),
        )


@dataclass(frozen=True, slots=True)
class MemoryGroundingEvidence:
    """Expose one admissible immutable Memory manifest without claiming content consumption."""

    evidence_id: str
    memory_id: str
    revision_id: str
    kind: str
    provider_id: str
    owner: JSONObject
    scope: str
    visibility: str
    payload_schema: str
    media_type: str
    artifact: JSONObject | None
    source_mission_id: str | None
    source_execution_id: str | None
    source_task_ref: JSONObject | None
    created_at_ms: int
    content_status: MemoryContentStatus = MemoryContentStatus.METADATA_ONLY

    def __post_init__(self) -> None:
        """Enforce the first grounding slice's global metadata-only Memory boundary."""
        for field, value in (
            ("evidence_id", self.evidence_id),
            ("memory_id", self.memory_id),
            ("revision_id", self.revision_id),
            ("provider_id", self.provider_id),
            ("payload_schema", self.payload_schema),
            ("media_type", self.media_type),
        ):
            _require_text(value, field)
        if self.kind not in {"semantic", "experience", "spatial"}:
            raise GroundingContextError("Memory kind is outside the grounding allowlist")
        if self.scope != "global":
            raise GroundingContextError("Mission grounding accepts only Global Memory")
        if self.visibility not in {"discoverable", "exchangeable"}:
            raise GroundingContextError("Memory visibility is unsupported")
        if not isinstance(self.content_status, MemoryContentStatus):
            raise GroundingContextError("Memory grounding content status must be explicit")
        _require_nonnegative(self.created_at_ms, "created_at_ms")
        if self.source_mission_id is not None:
            _require_text(self.source_mission_id, "source_mission_id")
        if self.source_execution_id is not None:
            _require_text(self.source_execution_id, "source_execution_id")
        object.__setattr__(self, "owner", _clone_object(self.owner, "memory owner"))
        if self.artifact is not None:
            object.__setattr__(
                self, "artifact", _clone_object(self.artifact, "memory artifact reference")
            )
        if self.source_task_ref is not None:
            object.__setattr__(
                self,
                "source_task_ref",
                _clone_object(self.source_task_ref, "memory source TaskRef"),
            )

    def __getattribute__(self, name: str) -> object:
        """Return defensive copies of nested Memory metadata owned by this frozen value."""
        value = object.__getattribute__(self, name)
        if name in {"owner", "artifact", "source_task_ref"} and value is not None:
            return _clone_object(cast(JSONObject, value), f"memory {name}")
        return value

    def to_json(self) -> JSONObject:
        """Serialize discovery evidence while explicitly declaring that bytes were not read."""
        return {
            "evidence_id": self.evidence_id,
            "selector": {"memory_id": self.memory_id, "revision_id": self.revision_id},
            "kind": self.kind,
            "provider_id": self.provider_id,
            "owner": _clone_object(self.owner, "memory owner"),
            "scope": self.scope,
            "visibility": self.visibility,
            "payload_schema": self.payload_schema,
            "media_type": self.media_type,
            "artifact": (
                None
                if self.artifact is None
                else _clone_object(self.artifact, "memory artifact reference")
            ),
            "source_mission_id": self.source_mission_id,
            "source_execution_id": self.source_execution_id,
            "source_task_ref": (
                None
                if self.source_task_ref is None
                else _clone_object(self.source_task_ref, "memory source TaskRef")
            ),
            "created_at_ms": self.created_at_ms,
            "content_status": self.content_status.value,
        }

    @classmethod
    def from_json(cls, value: object) -> MemoryGroundingEvidence:
        """Restore one sanitized Memory manifest without interpreting its payload schema."""
        item = _object(value, "memory evidence")
        expected = {
            "evidence_id",
            "selector",
            "kind",
            "provider_id",
            "owner",
            "scope",
            "visibility",
            "payload_schema",
            "media_type",
            "artifact",
            "source_mission_id",
            "source_execution_id",
            "source_task_ref",
            "created_at_ms",
            "content_status",
        }
        _require_fields(item, expected, "memory evidence")
        selector = _object(item["selector"], "memory evidence.selector")
        _require_fields(selector, {"memory_id", "revision_id"}, "memory evidence.selector")
        try:
            content_status = MemoryContentStatus(_text(item, "content_status"))
        except ValueError as error:
            raise GroundingContextError("memory evidence has unknown content status") from error
        artifact = item["artifact"]
        source_task_ref = item["source_task_ref"]
        return cls(
            evidence_id=_text(item, "evidence_id"),
            memory_id=_text(selector, "memory_id"),
            revision_id=_text(selector, "revision_id"),
            kind=_text(item, "kind"),
            provider_id=_text(item, "provider_id"),
            owner=_object(item["owner"], "memory evidence.owner"),
            scope=_text(item, "scope"),
            visibility=_text(item, "visibility"),
            payload_schema=_text(item, "payload_schema"),
            media_type=_text(item, "media_type"),
            artifact=None if artifact is None else _object(artifact, "memory evidence.artifact"),
            source_mission_id=_optional_text(item, "source_mission_id"),
            source_execution_id=_optional_text(item, "source_execution_id"),
            source_task_ref=(
                None
                if source_task_ref is None
                else _object(source_task_ref, "memory evidence.source_task_ref")
            ),
            created_at_ms=_integer(item, "created_at_ms"),
            content_status=content_status,
        )


@dataclass(frozen=True, slots=True)
class GroundingGap:
    """Record unavailable or deliberately unsupported context without inventing facts."""

    code: str
    source: str
    detail: str

    def __post_init__(self) -> None:
        """Reject empty diagnostics before they enter durable deliberation evidence."""
        _require_text(self.code, "gap.code")
        _require_text(self.source, "gap.source")
        _require_text(self.detail, "gap.detail")

    def to_json(self) -> JSONObject:
        """Serialize one fail-soft context acquisition result."""
        return {"code": self.code, "source": self.source, "detail": self.detail}

    @classmethod
    def from_json(cls, value: object) -> GroundingGap:
        """Restore one exact acquisition diagnostic."""
        item = _object(value, "grounding gap")
        _require_fields(item, {"code", "source", "detail"}, "grounding gap")
        return cls(_text(item, "code"), _text(item, "source"), _text(item, "detail"))


@dataclass(frozen=True, slots=True)
class GroundingContextSnapshot:
    """Freeze the bounded evidence shared by one complete Mission deliberation cycle."""

    context_digest: str
    request_id: str
    dialogue_digest: str
    captured_at_ms: int
    selection_policy_ref: str
    state_evidence: tuple[StateGroundingEvidence, ...]
    memory_evidence: tuple[MemoryGroundingEvidence, ...]
    gaps: tuple[GroundingGap, ...]

    @classmethod
    def create(
        cls,
        request_id: str,
        dialogue_digest: str,
        captured_at_ms: int,
        state_evidence: tuple[StateGroundingEvidence, ...] = (),
        memory_evidence: tuple[MemoryGroundingEvidence, ...] = (),
        gaps: tuple[GroundingGap, ...] = (),
        selection_policy_ref: str = EMPTY_GROUNDING_SELECTION_POLICY_REF,
    ) -> GroundingContextSnapshot:
        """Create a deterministic snapshot and bind its digest to all included evidence."""
        state_evidence = tuple(sorted(state_evidence, key=lambda item: item.evidence_id))
        memory_evidence = tuple(sorted(memory_evidence, key=lambda item: item.evidence_id))
        gaps = tuple(sorted(gaps, key=lambda item: (item.source, item.code, item.detail)))
        payload = _snapshot_payload(
            request_id,
            dialogue_digest,
            captured_at_ms,
            selection_policy_ref,
            state_evidence,
            memory_evidence,
            gaps,
        )
        return cls(
            context_digest=_digest(payload),
            request_id=request_id,
            dialogue_digest=dialogue_digest,
            captured_at_ms=captured_at_ms,
            selection_policy_ref=selection_policy_ref,
            state_evidence=state_evidence,
            memory_evidence=memory_evidence,
            gaps=gaps,
        )

    def __post_init__(self) -> None:
        """Reject duplicate evidence, malformed identities, and mismatched snapshot digests."""
        for field, value in (
            ("context_digest", self.context_digest),
            ("request_id", self.request_id),
            ("dialogue_digest", self.dialogue_digest),
            ("selection_policy_ref", self.selection_policy_ref),
        ):
            _require_text(value, field)
        if (
            _DIGEST.fullmatch(self.context_digest) is None
            or _DIGEST.fullmatch(self.dialogue_digest) is None
        ):
            raise GroundingContextError("grounding digests must be canonical SHA-256 identities")
        _require_nonnegative(self.captured_at_ms, "captured_at_ms")
        ids = [item.evidence_id for item in self.state_evidence]
        ids.extend(item.evidence_id for item in self.memory_evidence)
        if len(ids) != len(set(ids)):
            raise GroundingContextError("grounding evidence identities must be unique")
        if self.state_evidence != tuple(
            sorted(self.state_evidence, key=lambda item: item.evidence_id)
        ):
            raise GroundingContextError("State grounding evidence must use canonical order")
        if self.memory_evidence != tuple(
            sorted(self.memory_evidence, key=lambda item: item.evidence_id)
        ):
            raise GroundingContextError("Memory grounding evidence must use canonical order")
        if self.gaps != tuple(
            sorted(self.gaps, key=lambda item: (item.source, item.code, item.detail))
        ):
            raise GroundingContextError("grounding gaps must use canonical order")
        expected = _digest(
            _snapshot_payload(
                self.request_id,
                self.dialogue_digest,
                self.captured_at_ms,
                self.selection_policy_ref,
                self.state_evidence,
                self.memory_evidence,
                self.gaps,
            )
        )
        if self.context_digest != expected:
            raise GroundingContextError("grounding context digest does not match its evidence")
        if len(_canonical_json_bytes(self.to_json())) > MAX_GROUNDING_CONTEXT_BYTES:
            raise GroundingContextSizeError("grounding context exceeds the local byte limit")

    def to_json(self) -> JSONObject:
        """Serialize the immutable snapshot for provider input and request persistence."""
        return {
            "schema_version": GROUNDING_CONTEXT_SCHEMA,
            "context_digest": self.context_digest,
            **_snapshot_payload(
                self.request_id,
                self.dialogue_digest,
                self.captured_at_ms,
                self.selection_policy_ref,
                self.state_evidence,
                self.memory_evidence,
                self.gaps,
            ),
        }

    @classmethod
    def from_json(cls, value: object) -> GroundingContextSnapshot:
        """Restore and revalidate one complete snapshot without refreshing its evidence."""
        item = _object(value, "grounding context")
        expected = {
            "schema_version",
            "context_digest",
            "request_id",
            "dialogue_digest",
            "captured_at_ms",
            "selection_policy_ref",
            "state_evidence",
            "memory_evidence",
            "gaps",
        }
        _require_fields(item, expected, "grounding context")
        if item["schema_version"] != GROUNDING_CONTEXT_SCHEMA:
            raise GroundingContextError("unsupported grounding context schema")
        state = _array(item, "state_evidence")
        memory = _array(item, "memory_evidence")
        gaps = _array(item, "gaps")
        return cls(
            context_digest=_text(item, "context_digest"),
            request_id=_text(item, "request_id"),
            dialogue_digest=_text(item, "dialogue_digest"),
            captured_at_ms=_integer(item, "captured_at_ms"),
            selection_policy_ref=_text(item, "selection_policy_ref"),
            state_evidence=tuple(StateGroundingEvidence.from_json(value) for value in state),
            memory_evidence=tuple(MemoryGroundingEvidence.from_json(value) for value in memory),
            gaps=tuple(GroundingGap.from_json(value) for value in gaps),
        )


def dialogue_digest(dialogue: tuple[JSONObject, ...]) -> str:
    """Bind context selection to one exact normalized user-facing dialogue revision."""
    return _digest(list(dialogue))


def evidence_id(prefix: str, value: JSONValue) -> str:
    """Create a stable evidence identity from sanitized content rather than list position."""
    return f"{prefix}:{_digest(value).removeprefix('sha256:')}"


def grounding_selection_policy_ref(admitted_world_payload_schemas: frozenset[str]) -> str:
    """Bind one policy reference to the exact deployment-approved World schema set."""
    schema_digest = _digest(cast(JSONValue, sorted(admitted_world_payload_schemas)))
    return f"{GROUNDING_SELECTION_POLICY}#world-payload-schemas={schema_digest}"


def _snapshot_payload(
    request_id: str,
    dialogue_digest_value: str,
    captured_at_ms: int,
    selection_policy_ref: str,
    state_evidence: tuple[StateGroundingEvidence, ...],
    memory_evidence: tuple[MemoryGroundingEvidence, ...],
    gaps: tuple[GroundingGap, ...],
) -> JSONObject:
    """Return the canonical digest body without its self-referential identity."""
    return {
        "request_id": request_id,
        "dialogue_digest": dialogue_digest_value,
        "captured_at_ms": captured_at_ms,
        "selection_policy_ref": selection_policy_ref,
        "state_evidence": [item.to_json() for item in state_evidence],
        "memory_evidence": [item.to_json() for item in memory_evidence],
        "gaps": [item.to_json() for item in gaps],
    }


def _digest(value: JSONValue) -> str:
    """Hash one JSON value in stable key and separator order."""
    return f"sha256:{hashlib.sha256(_canonical_json_bytes(value)).hexdigest()}"


def _canonical_json_bytes(value: JSONValue) -> bytes:
    """Encode one finite JSON value with the ordering used for identity and size limits."""
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    except (TypeError, ValueError) as error:
        raise GroundingContextError("grounding evidence must be canonical JSON") from error


def _clone_json(value: JSONValue, path: str) -> JSONValue:
    """Detach nested JSON containers so callers cannot mutate snapshot-owned evidence."""
    cloned = deepcopy(value)
    try:
        json.dumps(cloned, allow_nan=False)
    except (TypeError, ValueError) as error:
        raise GroundingContextError(f"{path} must be finite JSON") from error
    return cloned


def _clone_object(value: JSONObject, path: str) -> JSONObject:
    """Detach and retain one string-keyed JSON object."""
    return _object(_clone_json(value, path), path)


def _object(value: object, path: str) -> JSONObject:
    """Return one string-keyed JSON object or reject the malformed value."""
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise GroundingContextError(f"{path} must be an object")
    return cast(JSONObject, value)


def _array(value: JSONObject, field: str) -> list[JSONValue]:
    """Return one JSON array from a strict object field."""
    item = value[field]
    if not isinstance(item, list):
        raise GroundingContextError(f"{field} must be an array")
    return item


def _text(value: JSONObject, field: str) -> str:
    """Read one nonblank string field."""
    item = value[field]
    if not isinstance(item, str) or not item.strip():
        raise GroundingContextError(f"{field} must be nonblank text")
    return item


def _optional_text(value: JSONObject, field: str) -> str | None:
    """Read one optional nonblank string field."""
    item = value[field]
    if item is None:
        return None
    if not isinstance(item, str) or not item.strip():
        raise GroundingContextError(f"{field} must be nonblank text or null")
    return item


def _integer(value: JSONObject, field: str) -> int:
    """Read one integer without Boolean coercion."""
    item = value[field]
    if isinstance(item, bool) or not isinstance(item, int):
        raise GroundingContextError(f"{field} must be an integer")
    return item


def _optional_integer(value: JSONObject, field: str) -> int | None:
    """Read one optional integer without Boolean coercion."""
    item = value[field]
    if item is None:
        return None
    if isinstance(item, bool) or not isinstance(item, int):
        raise GroundingContextError(f"{field} must be an integer or null")
    return item


def _require_text(value: str, field: str) -> None:
    """Reject blank text supplied to a dataclass constructor."""
    if not isinstance(value, str) or not value.strip():
        raise GroundingContextError(f"{field} must be nonblank text")


def _require_nonnegative(value: int, field: str) -> None:
    """Reject Boolean and negative integer values."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise GroundingContextError(f"{field} must be a nonnegative integer")


def _require_positive(value: int, field: str) -> None:
    """Reject Boolean and nonpositive integer values."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise GroundingContextError(f"{field} must be a positive integer")


def _require_fields(value: JSONObject, expected: set[str], path: str) -> None:
    """Reject missing or additional fields in a versioned grounding object."""
    if set(value) != expected:
        raise GroundingContextError(f"{path} fields do not match v0.1")
