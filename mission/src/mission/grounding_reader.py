"""Read-only assembly of bounded Mission grounding evidence from existing projections."""

from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from email.message import Message
from typing import Any, Protocol, cast

from mission.grounding_context import (
    GroundingContextError,
    GroundingContextSizeError,
    GroundingContextSnapshot,
    GroundingFreshness,
    GroundingGap,
    MemoryGroundingEvidence,
    StateGroundingEvidence,
    dialogue_digest,
    evidence_id,
    grounding_selection_policy_ref,
)
from mission.models import JSONObject, JSONValue
from mission.request_record import DialogueTurn

MAX_GROUNDING_RESPONSE_BYTES = 2 * 1024 * 1024
_STATE_RECORD_FIELDS = {
    "object",
    "semantic",
    "source",
    "channel_id",
    "value",
    "stale",
}
_STATE_VALUE_FIELDS = {
    "payload_schema",
    "value",
    "source_observed_at_ms",
    "received_at_ms",
    "valid_for_ms",
    "confidence_millionths",
    "source_epoch",
    "sequence",
}
_MEMORY_MANIFEST_FIELDS = {
    "schema",
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
    "created_at",
}
_SPATIAL_MEMORY_VIEW_FIELDS = {
    *_MEMORY_MANIFEST_FIELDS,
    "typed_extension",
    "status",
}


class GroundingReadError(RuntimeError):
    """Report an invalid endpoint, transport failure, or malformed grounding projection."""


class MissionGroundingReader(Protocol):
    """Capture one immutable context without granting Mission Intelligence State authority."""

    def capture(
        self,
        request_id: str,
        dialogue: tuple[DialogueTurn, ...],
        captured_at_ms: int,
    ) -> GroundingContextSnapshot:
        """Return evidence bound to the exact supplied dialogue revision."""
        ...


class EmptyMissionGroundingReader:
    """Provide a valid empty snapshot for offline and compatibility composition roots."""

    def capture(
        self,
        request_id: str,
        dialogue: tuple[DialogueTurn, ...],
        captured_at_ms: int,
    ) -> GroundingContextSnapshot:
        """Create an empty context that still carries request and dialogue identity."""
        return GroundingContextSnapshot.create(
            request_id=request_id,
            dialogue_digest=dialogue_digest(tuple(turn.to_json() for turn in dialogue)),
            captured_at_ms=captured_at_ms,
            selection_policy_ref=grounding_selection_policy_ref(frozenset()),
        )


class GroundingJsonTransport(Protocol):
    """Fetch one bounded JSON object behind an injectable read-only transport boundary."""

    def get_json(self, url: str, timeout_seconds: float) -> JSONObject:
        """Return one decoded object or raise a stable grounding transport failure."""
        ...


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Reject redirects so configured grounding authorities cannot change at response time."""

    def redirect_request(
        self,
        request: urllib.request.Request,
        response: Any,
        code: int,
        msg: str,
        headers: Message,
        newurl: str,
    ) -> None:
        """Reject a server-selected target without issuing another request."""
        raise GroundingReadError(f"grounding endpoint returned redirect HTTP {code}")


class UrllibGroundingJsonTransport:
    """Read bounded JSON from fixed State and Memory HTTP origins."""

    def __init__(self) -> None:
        """Create a redirect-rejecting opener for all grounding reads."""
        self._opener = urllib.request.build_opener(_NoRedirectHandler())

    def get_json(self, url: str, timeout_seconds: float) -> JSONObject:
        """Fetch one JSON document without retries or credential forwarding."""
        request = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
        try:
            with self._opener.open(request, timeout=timeout_seconds) as response:
                if response.status != 200:
                    raise GroundingReadError(f"grounding endpoint returned HTTP {response.status}")
                raw = response.read(MAX_GROUNDING_RESPONSE_BYTES + 1)
        except urllib.error.HTTPError as error:
            raise GroundingReadError(f"grounding endpoint returned HTTP {error.code}") from error
        except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError) as error:
            raise GroundingReadError(f"grounding endpoint request failed: {error}") from error
        if len(raw) > MAX_GROUNDING_RESPONSE_BYTES:
            raise GroundingReadError("grounding endpoint response exceeds local limit")
        try:
            decoded: object = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise GroundingReadError("grounding endpoint returned invalid JSON") from error
        if not isinstance(decoded, dict) or not all(isinstance(key, str) for key in decoded):
            raise GroundingReadError("grounding endpoint response must be an object")
        return cast(JSONObject, decoded)


class HttpMissionGroundingReader:
    """Build a fail-soft snapshot from World State and Global Memory catalog metadata."""

    def __init__(
        self,
        controller_endpoint: str,
        artifact_endpoint: str,
        timeout_seconds: float,
        max_state_evidence: int,
        max_memory_evidence: int,
        transport: GroundingJsonTransport | None = None,
        *,
        admitted_world_payload_schemas: frozenset[str] = frozenset(),
        max_gaps: int = 32,
    ) -> None:
        """Bind fixed origins and positive evidence budgets without accepting query injection."""
        self._controller_endpoint = _endpoint(controller_endpoint, "Controller")
        self._artifact_endpoint = _endpoint(artifact_endpoint, "Artifact")
        if timeout_seconds <= 0:
            raise GroundingReadError("grounding timeout must be positive")
        if max_state_evidence <= 0 or max_memory_evidence <= 0 or max_gaps <= 0:
            raise GroundingReadError("grounding evidence budgets must be positive")
        self._timeout_seconds = timeout_seconds
        self._max_state_evidence = max_state_evidence
        self._max_memory_evidence = max_memory_evidence
        self._max_gaps = max_gaps
        self._transport = transport or UrllibGroundingJsonTransport()
        if any(not schema.strip() for schema in admitted_world_payload_schemas):
            raise GroundingReadError("admitted World payload schemas must be nonblank")
        self._admitted_world_payload_schemas = admitted_world_payload_schemas
        self._selection_policy_ref = grounding_selection_policy_ref(admitted_world_payload_schemas)

    def capture(
        self,
        request_id: str,
        dialogue: tuple[DialogueTurn, ...],
        captured_at_ms: int,
    ) -> GroundingContextSnapshot:
        """Capture independent State and Memory reads while retaining partial-failure gaps."""
        gaps = _GroundingGapCollector(self._max_gaps)
        state = self._read_state(gaps)
        memory = self._read_memory(gaps)
        return self._bounded_snapshot(
            request_id=request_id,
            dialogue_digest=dialogue_digest(tuple(turn.to_json() for turn in dialogue)),
            captured_at_ms=captured_at_ms,
            state_evidence=state,
            memory_evidence=memory,
            gaps=gaps.finish(),
        )

    def _read_state(self, gaps: _GroundingGapCollector) -> tuple[StateGroundingEvidence, ...]:
        """Read only World records and preserve the Controller's freshness assessment."""
        path = "/v1/state/records?" + urllib.parse.urlencode({"object_class": "world"})
        try:
            response = self._transport.get_json(
                f"{self._controller_endpoint}{path}", self._timeout_seconds
            )
            if response.get("schema") != "roboguide.state-query/v0.1":
                raise GroundingReadError("unsupported State query schema")
            records = _array(response, "records")
        except (GroundingReadError, GroundingContextError, KeyError) as error:
            gaps.add("state_unavailable", "controller-state", str(error))
            return ()
        evidence: list[StateGroundingEvidence] = []
        for index, raw in enumerate(records):
            try:
                item = _state_evidence(raw, self._admitted_world_payload_schemas)
            except (GroundingContextError, KeyError) as error:
                gaps.add(
                    "state_record_rejected",
                    "controller-state",
                    f"record {index}: {error}",
                )
                continue
            if item is not None:
                evidence.append(item)
        evidence.sort(key=lambda item: item.evidence_id)
        if len(evidence) > self._max_state_evidence:
            evidence = evidence[: self._max_state_evidence]
            gaps.add(
                "state_results_truncated",
                "controller-state",
                f"retained at most {self._max_state_evidence} World State records",
            )
        return tuple(evidence)

    def _read_memory(self, gaps: _GroundingGapCollector) -> tuple[MemoryGroundingEvidence, ...]:
        """Read only admissible Global manifest metadata and never fetch artifact bytes."""
        try:
            response = self._transport.get_json(
                f"{self._artifact_endpoint}/v1/memories", self._timeout_seconds
            )
            if response.get("schema") != "roboguide.memory-catalog/v0.1":
                raise GroundingReadError("unsupported Memory catalog schema")
            manifests = _array(response, "memories")
        except (GroundingReadError, GroundingContextError, KeyError) as error:
            gaps.add("memory_unavailable", "memory-catalog", str(error))
            return ()
        evidence: list[MemoryGroundingEvidence] = []
        for index, raw in enumerate(manifests):
            try:
                item = _memory_evidence(raw)
            except (GroundingContextError, KeyError) as error:
                gaps.add(
                    "memory_manifest_rejected",
                    "memory-catalog",
                    f"manifest {index}: {error}",
                )
                continue
            if item is not None:
                evidence.append(item)
        evidence.sort(key=lambda item: item.evidence_id)
        if len(evidence) > self._max_memory_evidence:
            evidence = evidence[: self._max_memory_evidence]
            gaps.add(
                "memory_results_truncated",
                "memory-catalog",
                f"retained at most {self._max_memory_evidence} Global Memory manifests",
            )
        return tuple(evidence)

    def _bounded_snapshot(
        self,
        *,
        request_id: str,
        dialogue_digest: str,
        captured_at_ms: int,
        state_evidence: tuple[StateGroundingEvidence, ...],
        memory_evidence: tuple[MemoryGroundingEvidence, ...],
        gaps: tuple[GroundingGap, ...],
    ) -> GroundingContextSnapshot:
        """Drop deterministic stable tails until the complete snapshot fits its hard limit."""
        retained_state = list(state_evidence)
        retained_memory = list(memory_evidence)
        retained_gaps = list(gaps)
        removed_state = 0
        removed_memory = 0
        while True:
            current_gaps = _snapshot_gaps(
                tuple(retained_gaps),
                self._max_gaps,
                removed_state,
                removed_memory,
            )
            try:
                return GroundingContextSnapshot.create(
                    request_id=request_id,
                    dialogue_digest=dialogue_digest,
                    captured_at_ms=captured_at_ms,
                    state_evidence=tuple(retained_state),
                    memory_evidence=tuple(retained_memory),
                    gaps=current_gaps,
                    selection_policy_ref=self._selection_policy_ref,
                )
            except GroundingContextSizeError:
                if retained_memory:
                    retained_memory.pop()
                    removed_memory += 1
                elif retained_state:
                    retained_state.pop()
                    removed_state += 1
                elif retained_gaps:
                    retained_gaps.pop()
                else:
                    raise


class _GroundingGapCollector:
    """Bound retained diagnostics while counting evidence that was deliberately omitted."""

    def __init__(self, maximum: int) -> None:
        """Initialize one capture-local collector with a strictly positive result bound."""
        self._maximum = maximum
        self._gaps: list[GroundingGap] = []
        self._omitted = 0

    def add(self, code: str, source: str, detail: str) -> None:
        """Retain one bounded diagnostic or count it when the configured limit is full."""
        gap = GroundingGap(code, source, _bounded_gap_detail(detail))
        if len(self._gaps) < self._maximum:
            self._gaps.append(gap)
        else:
            self._omitted += 1

    def finish(self) -> tuple[GroundingGap, ...]:
        """Return retained diagnostics and one explicit omission summary when required."""
        if self._omitted == 0:
            return tuple(self._gaps)
        summary = GroundingGap(
            "grounding_diagnostics_truncated",
            "mission-grounding",
            f"omitted {self._omitted + 1} additional grounding diagnostics",
        )
        if self._maximum == 1:
            return (summary,)
        return (*self._gaps[: self._maximum - 1], summary)


def _snapshot_gaps(
    gaps: tuple[GroundingGap, ...],
    maximum: int,
    removed_state: int,
    removed_memory: int,
) -> tuple[GroundingGap, ...]:
    """Add one mandatory summary when evidence was removed to meet the byte budget."""
    if removed_state == 0 and removed_memory == 0:
        return gaps
    summary = GroundingGap(
        "grounding_snapshot_truncated",
        "mission-grounding",
        f"byte limit omitted {removed_state} State and {removed_memory} Memory evidence records",
    )
    if maximum == 1:
        return (summary,)
    ordered = sorted(gaps, key=lambda item: (item.source, item.code, item.detail))
    return (*ordered[: maximum - 1], summary)


def _bounded_gap_detail(detail: str) -> str:
    """Limit untrusted source diagnostics before they enter durable model-visible evidence."""
    maximum = 512
    return detail if len(detail) <= maximum else f"{detail[: maximum - 3]}..."


def _state_evidence(
    raw: JSONValue, admitted_world_payload_schemas: frozenset[str]
) -> StateGroundingEvidence | None:
    """Sanitize one federated State record and exclude every deployment/control object."""
    item = _object(raw, "State record")
    _require_fields(item, _STATE_RECORD_FIELDS, "State record")
    object_ref = _object(item.get("object"), "State record.object")
    _require_fields(object_ref, {"class", "object_type", "object_id"}, "State record.object")
    if object_ref.get("class") != "world":
        return None
    semantic = _text(item, "semantic")
    if semantic not in {"reported", "observed", "derived", "belief"}:
        return None
    payload = _object(item.get("value"), "State record.value")
    _require_fields(payload, _STATE_VALUE_FIELDS, "State record.value")
    payload_schema = _text(payload, "payload_schema")
    if payload_schema not in admitted_world_payload_schemas:
        return None
    stale = item.get("stale")
    if not isinstance(stale, bool):
        raise GroundingContextError("World State record must carry a freshness assessment")
    sanitized: JSONObject = {
        "object": {
            "class": "world",
            "object_type": _text(object_ref, "object_type"),
            "object_id": _text(object_ref, "object_id"),
        },
        "semantic": semantic,
        "source": _text(item, "source"),
        "channel_id": _text(item, "channel_id"),
        "payload_schema": payload_schema,
        "value": payload["value"],
        "source_observed_at_ms": _optional_integer(payload, "source_observed_at_ms"),
        "received_at_ms": _integer(payload, "received_at_ms"),
        "valid_for_ms": _integer(payload, "valid_for_ms"),
        "freshness": GroundingFreshness.STALE.value if stale else GroundingFreshness.FRESH.value,
        "confidence_millionths": _optional_integer(payload, "confidence_millionths"),
        "source_epoch": _optional_text(payload, "source_epoch"),
        "sequence": _integer(payload, "sequence"),
    }
    return StateGroundingEvidence.from_json(
        {"evidence_id": evidence_id("state", sanitized), **sanitized}
    )


def _memory_evidence(raw: JSONValue) -> MemoryGroundingEvidence | None:
    """Sanitize one manifest while excluding local/group scope and unsupported Memory kinds."""
    item = _object(raw, "Memory manifest")
    schema = _text(item, "schema")
    if schema == "roboguide.memory-manifest/v0.1":
        _require_fields(item, _MEMORY_MANIFEST_FIELDS, "Memory manifest")
    elif schema == "roboguide.memory-manifest-view/v0.1":
        _require_fields(item, _SPATIAL_MEMORY_VIEW_FIELDS, "Spatial Memory manifest view")
        if item["typed_extension"] != "map":
            raise GroundingContextError("unsupported typed Memory manifest extension")
        if _text(item, "status") not in {"Declared", "Published"}:
            raise GroundingContextError("unknown Spatial Memory revision status")
    else:
        raise GroundingContextError("unsupported Memory manifest schema")
    kind = _text(item, "kind")
    if schema == "roboguide.memory-manifest-view/v0.1" and kind != "spatial":
        raise GroundingContextError("typed map Memory view must declare spatial kind")
    scope_value = _object(item.get("scope"), "Memory manifest.scope")
    scope = _text(scope_value, "kind")
    if scope == "execution_group":
        _require_fields(
            scope_value,
            {"kind", "execution_group_id"},
            "Memory manifest.scope",
        )
        _text(scope_value, "execution_group_id")
    elif scope in {"local", "global"}:
        _require_fields(scope_value, {"kind"}, "Memory manifest.scope")
    else:
        raise GroundingContextError("Memory manifest has an unknown scope kind")
    if kind not in {"semantic", "experience", "spatial"} or scope != "global":
        return None
    selector = _object(item.get("selector"), "Memory manifest.selector")
    _require_fields(selector, {"memory_id", "revision_id"}, "Memory manifest.selector")
    owner = _object(item.get("owner"), "Memory manifest.owner")
    _validate_memory_owner(
        owner,
        local_system_id_optional=schema == "roboguide.memory-manifest-view/v0.1",
    )
    artifact_raw = item.get("artifact")
    artifact = None if artifact_raw is None else _object(artifact_raw, "Memory manifest.artifact")
    if artifact is not None:
        _require_fields(artifact, {"content_digest", "byte_size"}, "Memory manifest.artifact")
        _text(artifact, "content_digest")
        _nonnegative_integer(artifact, "byte_size")
    task_raw = item.get("source_task_ref")
    source_task = None if task_raw is None else _object(task_raw, "Memory manifest.source_task_ref")
    if source_task is not None:
        _require_fields(source_task, {"mission_id", "task_id"}, "Memory manifest.source_task_ref")
        _text(source_task, "mission_id")
        _text(source_task, "task_id")
    created_at = item.get("created_at")
    if isinstance(created_at, dict):
        created_at = created_at.get("millis")
    if isinstance(created_at, bool) or not isinstance(created_at, int):
        raise GroundingContextError("Memory manifest.created_at must be an integer timestamp")
    sanitized: JSONObject = {
        "selector": {
            "memory_id": _text(selector, "memory_id"),
            "revision_id": _text(selector, "revision_id"),
        },
        "kind": kind,
        "provider_id": _text(item, "provider_id"),
        "owner": owner,
        "scope": scope,
        "visibility": _text(item, "visibility"),
        "payload_schema": _text(item, "payload_schema"),
        "media_type": _text(item, "media_type"),
        "artifact": artifact,
        "source_mission_id": _optional_text(item, "source_mission_id"),
        "source_execution_id": _optional_text(item, "source_execution_id"),
        "source_task_ref": source_task,
        "created_at_ms": created_at,
        "content_status": "MetadataOnly",
    }
    return MemoryGroundingEvidence.from_json(
        {"evidence_id": evidence_id("memory", sanitized), **sanitized}
    )


def _endpoint(value: str, name: str) -> str:
    """Validate one fixed HTTP origin without path, credentials, query, or fragment."""
    parsed = urllib.parse.urlparse(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise GroundingReadError(f"{name} grounding endpoint must be a fixed HTTP(S) origin")
    return value.rstrip("/")


def _object(value: object, path: str) -> JSONObject:
    """Return one string-keyed JSON object or reject malformed transport data."""
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise GroundingContextError(f"{path} must be an object")
    return cast(JSONObject, value)


def _array(value: JSONObject, field: str) -> list[JSONValue]:
    """Read one JSON array from a transport response."""
    item = value.get(field)
    if not isinstance(item, list):
        raise GroundingContextError(f"{field} must be an array")
    return item


def _text(value: Mapping[str, JSONValue], field: str) -> str:
    """Read one required nonblank text field."""
    item = value.get(field)
    if not isinstance(item, str) or not item.strip():
        raise GroundingContextError(f"{field} must be nonblank text")
    return item


def _optional_text(value: Mapping[str, JSONValue], field: str) -> str | None:
    """Read one optional nonblank text field."""
    item = value.get(field)
    if item is None:
        return None
    if not isinstance(item, str) or not item.strip():
        raise GroundingContextError(f"{field} must be nonblank text or null")
    return item


def _integer(value: Mapping[str, JSONValue], field: str) -> int:
    """Read one integer without accepting Boolean coercion."""
    item = value.get(field)
    if isinstance(item, bool) or not isinstance(item, int):
        raise GroundingContextError(f"{field} must be an integer")
    return item


def _optional_integer(value: Mapping[str, JSONValue], field: str) -> int | None:
    """Read one optional integer without accepting Boolean coercion."""
    item = value.get(field)
    if item is None:
        return None
    if isinstance(item, bool) or not isinstance(item, int):
        raise GroundingContextError(f"{field} must be an integer or null")
    return item


def _nonnegative_integer(value: Mapping[str, JSONValue], field: str) -> int:
    """Read one nonnegative integer without accepting Boolean coercion."""
    item = _integer(value, field)
    if item < 0:
        raise GroundingContextError(f"{field} must be nonnegative")
    return item


def _validate_memory_owner(owner: JSONObject, *, local_system_id_optional: bool) -> None:
    """Validate the exact existing generic or typed-map Memory ownership envelope."""
    owner_kind = _text(owner, "owner")
    if owner_kind == "robo_guide":
        _require_fields(owner, {"owner", "component"}, "Memory manifest.owner")
        _text(owner, "component")
        return
    if owner_kind == "node":
        _require_fields(
            owner,
            {"owner", "node_id", "local_system_id"},
            "Memory manifest.owner",
        )
        _text(owner, "node_id")
        if local_system_id_optional:
            _optional_text(owner, "local_system_id")
        else:
            _text(owner, "local_system_id")
        return
    raise GroundingContextError("Memory manifest has an unknown owner kind")


def _require_fields(value: Mapping[str, JSONValue], expected: set[str], path: str) -> None:
    """Reject missing or additional fields in one versioned grounding source object."""
    if set(value) != expected:
        raise GroundingContextError(f"{path} fields do not match its declared schema")
