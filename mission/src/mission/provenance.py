"""Immutable model/configuration and actual provider-call provenance."""

from __future__ import annotations

import hashlib
import json
import platform
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import cast

from mission.models import JSONObject
from mission.submission_evidence import canonical_plan_digest

PROVENANCE_SCHEMA = "roboguide.mission-provenance/v0.1"
MAX_PROVIDER_CALLS = 64
MAX_TRACE_BYTES = 128 * 1024
MAX_CALL_BYTES = 16 * 1024


def implementation_identity() -> JSONObject:
    """Hash the actual installed MI implementation and Python version without machine paths."""
    root = Path(__file__).parent
    sources = {
        str(path.relative_to(root)): "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*.py"))
        if path.is_file()
    }
    return {
        "python_version": platform.python_version(),
        "source_digest": canonical_plan_digest(sources),
        "installed_dependencies": {
            name: _installed_version(name) for name in ("mission-intelligence", "PyYAML")
        },
    }


def _installed_version(name: str) -> str | None:
    """Retain an actual installed distribution version; absence is never filled from a lockfile."""
    try:
        return version(name)
    except PackageNotFoundError:
        return None


@dataclass(slots=True)
class ProviderTrace:
    """Collect bounded original calls for one actual deliberation context."""

    request_id: str
    input_dialogue_digest: str
    trace_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    calls: list[str] = field(default_factory=list)


_TRACE: ContextVar[ProviderTrace | None] = ContextVar("mission_provider_trace", default=None)


@contextmanager
def trace_deliberation(request_id: str, input_digest: str) -> Iterator[ProviderTrace]:
    """Bind independent HTTP workers to their own provider evidence, restoring nested contexts."""
    trace = ProviderTrace(request_id, input_digest)
    token = _TRACE.set(trace)
    try:
        yield trace
    finally:
        _TRACE.reset(token)


def current_trace(request_id: str) -> ProviderTrace | None:
    """Return only evidence attributed to this exact Request, never another concurrent worker."""
    trace = _TRACE.get()
    return trace if trace is not None and trace.request_id == request_id else None


def ensure_provider_budget() -> None:
    """Refuse an additional provider side effect before bounded evidence storage is exhausted."""
    trace = _TRACE.get()
    if trace is not None and (
        len(trace.calls) >= MAX_PROVIDER_CALLS
        or sum(len(call.encode()) for call in trace.calls) + MAX_CALL_BYTES > MAX_TRACE_BYTES
    ):
        raise ValueError("provider provenance call limit exhausted before dispatch")


def provider_call(
    payload: JSONObject,
    response: JSONObject | None,
    error: BaseException | None,
    started_at_ms: int,
    finished_at_ms: int,
) -> None:
    """Retain actual requested/returned identity and content digests without auth headers."""
    trace = _TRACE.get()
    if trace is None:
        return
    requested = payload.get("model")
    observed = response.get("model") if response is not None else None
    response_id = response.get("id") if response is not None else None
    evidence: JSONObject = {
        "ordinal": len(trace.calls) + 1,
        "call_id": f"{trace.trace_id}:{len(trace.calls) + 1}",
        "requested_model": requested if isinstance(requested, str) else None,
        "observed_model": observed[:256] if isinstance(observed, str) else None,
        "response_id": response_id[:256] if isinstance(response_id, str) else None,
        "request_digest": canonical_plan_digest(payload),
        "response_digest": canonical_plan_digest(response) if response is not None else None,
        "started_at_ms": started_at_ms,
        "finished_at_ms": finished_at_ms,
        "error_type": type(error).__name__ if error is not None else None,
    }
    trace.calls.append(json.dumps(evidence, sort_keys=True, separators=(",", ":")))


def snapshot(
    identity_json: str,
    request_id: str,
    input_digest: str,
    request_digest: str,
    grounding_digest: str | None,
    revision: int,
    lifecycle: str,
) -> JSONObject:
    """Capture immutable configured identity and actual calls at one durable Request transition."""
    trace = current_trace(request_id)
    return {
        "schema_version": PROVENANCE_SCHEMA,
        "request_id": request_id,
        "input_dialogue_digest": trace.input_dialogue_digest if trace is not None else input_digest,
        "request_record_digest": request_digest,
        "configuration": cast(JSONObject, json.loads(identity_json)),
        "configuration_digest": canonical_plan_digest(json.loads(identity_json)),
        "grounding_context_digest": grounding_digest,
        "draft_revision": revision,
        "lifecycle": lifecycle,
        "provider_calls": [cast(JSONObject, json.loads(call)) for call in trace.calls]
        if trace is not None
        else [],
        "deliberation_active": trace is not None,
    }


def validate_snapshot(value: JSONObject) -> None:
    """Reject detached interpretation digests and malformed call metadata before SQL admission."""
    configuration = value.get("configuration")
    if (
        value.get("schema_version") != PROVENANCE_SCHEMA
        or type(value.get("deliberation_active")) is not bool
        or not isinstance(configuration, dict)
        or canonical_plan_digest(configuration) != value.get("configuration_digest")
    ):
        raise ValueError("provenance configuration identity is invalid")
    calls = value.get("provider_calls")
    if not isinstance(calls, list) or len(calls) > MAX_PROVIDER_CALLS:
        raise ValueError("provenance provider call bounds are invalid")
    for ordinal, call in enumerate(calls, 1):
        if (
            not isinstance(call, dict)
            or call.get("ordinal") != ordinal
            or type(call.get("started_at_ms")) is not int
            or type(call.get("finished_at_ms")) is not int
        ):
            raise ValueError("provenance provider call identity is invalid")
