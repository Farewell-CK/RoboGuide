"""Immutable model/configuration and actual provider-call provenance."""

from __future__ import annotations

import hashlib
import json
import platform
import re
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Literal, cast

from mission.models import JSONObject, JSONValue
from mission.provider_mission_plan import HOST_OBJECTIVE_PROFILE
from mission.submission_evidence import canonical_plan_digest

PROVENANCE_SCHEMA = "roboguide.mission-provenance/v0.1"
MAX_PROVIDER_CALLS = 64
MAX_TRACE_BYTES = 128 * 1024
MAX_CALL_BYTES = 16 * 1024

_PROVIDER_STAGES = {
    "mission_intent_v0": "interpreter",
    "mission_plan_v0": "planner",
    "mission_plan_compact_v1": "planner",
    "mission_review_v0": "reviewer",
    "mission_plan_repair_v0": "repairer",
    "deployment_recovery_v0": "deployment_recovery",
}
_CACHE_PREFIX_DIGEST = re.compile(r"sha256:[a-f0-9]{64}")
_HTTP_REQUEST_ID = re.compile(r"[A-Za-z0-9_.:-]{1,256}")
TransportFailurePhase = Literal[
    "awaiting_headers", "reading_body", "http_status", "decoding_response"
]


@dataclass(frozen=True, slots=True)
class ProviderHttpTiming:
    """Monotonic timings and allowlisted HTTP metadata from one provider POST."""

    elapsed_ms: int
    headers_received_ms: int | None
    body_read_ms: int | None
    http_status_code: int | None
    upstream_request_id: str | None
    failure_phase: TransportFailurePhase | None

    def to_json(self) -> JSONObject:
        """Keep response metadata distinct from the provider's JSON response identity."""
        return {
            "elapsed_ms": self.elapsed_ms,
            "headers_received_ms": self.headers_received_ms,
            "body_read_ms": self.body_read_ms,
            "http_status_code": self.http_status_code,
            "upstream_request_id": self.upstream_request_id,
            "failure_phase": self.failure_phase,
        }


@dataclass(slots=True)
class ProviderHttpCapture:
    """Hold the transport observation for the current client call only."""

    timing: ProviderHttpTiming | None = None


_HTTP_CAPTURE: ContextVar[ProviderHttpCapture | None] = ContextVar(
    "mission_provider_http_capture", default=None
)


@contextmanager
def capture_provider_http() -> Iterator[ProviderHttpCapture]:
    """Scope optional HTTP evidence without changing injected transport interfaces."""
    capture = ProviderHttpCapture()
    token = _HTTP_CAPTURE.set(capture)
    try:
        yield capture
    finally:
        _HTTP_CAPTURE.reset(token)


def record_provider_http(timing: ProviderHttpTiming) -> None:
    """Record one actual transport observation, including incomplete responses."""
    capture = _HTTP_CAPTURE.get()
    if capture is not None:
        capture.timing = timing


def upstream_request_id(values: list[str]) -> str | None:
    """Accept one bounded opaque request ID from HTTP response headers."""
    if len(values) == 1 and _HTTP_REQUEST_ID.fullmatch(values[0]) is not None:
        return values[0]
    return None


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
    if trace is not None:
        original_bytes = 0
        for encoded in trace.calls:
            call = json.loads(encoded)
            call.pop("plan_assembly", None)
            original_bytes += len(json.dumps(call, sort_keys=True, separators=(",", ":")).encode())
        if (
            len(trace.calls) >= MAX_PROVIDER_CALLS
            or original_bytes + MAX_CALL_BYTES > MAX_TRACE_BYTES
        ):
            raise ValueError("provider provenance call limit exhausted before dispatch")
        _trim_optional_assemblies(trace, MAX_CALL_BYTES)


def _trim_optional_assemblies(trace: ProviderTrace, reserve_bytes: int) -> None:
    """Spend only spare trace bytes on optional assembly observations, never original call slots.

    Future snapshots may omit older optional observations; previously persisted
    snapshots and original request/response metadata remain immutable.
    """
    total = sum(len(call.encode()) for call in trace.calls)
    for index, encoded in enumerate(trace.calls):
        if total + reserve_bytes <= MAX_TRACE_BYTES:
            return
        call = json.loads(encoded)
        if "plan_assembly" not in call:
            continue
        del call["plan_assembly"]
        compact = json.dumps(call, sort_keys=True, separators=(",", ":"))
        total += len(compact.encode()) - len(encoded.encode())
        trace.calls[index] = compact


def _cache_prefix_digest(payload: JSONObject) -> str | None:
    """Fingerprint the exact ordered wire prefix through its first explicit breakpoint."""
    messages = payload.get("input")
    if not isinstance(messages, list):
        return None
    prefix: list[JSONValue] = []
    for message in messages:
        if not isinstance(message, dict) or not isinstance(content := message.get("content"), list):
            return None
        for index, block in enumerate(content):
            if isinstance(block, dict) and block.get("prompt_cache_breakpoint") == {
                "mode": "explicit"
            }:
                prefix.append({**message, "content": content[: index + 1]})
                context = {
                    key: payload[key]
                    for key in ("model", "reasoning", "text", "prompt_cache_options", "tools")
                    if key in payload
                }
                # Structured schemas and message content keep their original wire order.
                packed = json.dumps(
                    {"settings": context, "prefix": prefix},
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                return "sha256:" + hashlib.sha256(packed).hexdigest()
        prefix.append(message)
    return None


def provider_call(
    payload: JSONObject,
    response: JSONObject | None,
    error: BaseException | None,
    started_at_ms: int,
    finished_at_ms: int,
    *,
    http_timing: ProviderHttpTiming | None = None,
) -> None:
    """Retain actual requested/returned identity and content digests without auth headers."""
    trace = _TRACE.get()
    if trace is None:
        return
    requested = payload.get("model")
    observed = response.get("model") if response is not None else None
    response_id = response.get("id") if response is not None else None
    requested_tier = payload.get("service_tier")
    observed_tier = response.get("service_tier") if response is not None else None
    reasoning = payload.get("reasoning")
    requested_effort = reasoning.get("effort") if isinstance(reasoning, dict) else None
    text = payload.get("text")
    response_format = text.get("format") if isinstance(text, dict) else None
    schema_name = response_format.get("name") if isinstance(response_format, dict) else None
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
    for name, tier in (
        ("requested_service_tier", requested_tier),
        ("observed_service_tier", observed_tier),
    ):
        if isinstance(tier, str) and 0 < len(tier) <= 256:
            evidence[name] = tier
    if isinstance(requested_effort, str) and 0 < len(requested_effort) <= 256:
        evidence["requested_reasoning_effort"] = requested_effort
    if isinstance(schema_name, str) and 0 < len(schema_name) <= 256:
        evidence["response_schema_name"] = schema_name
        if schema_name in _PROVIDER_STAGES:
            evidence["stage"] = _PROVIDER_STAGES[schema_name]
    if (prefix_digest := _cache_prefix_digest(payload)) is not None:
        evidence["cache_prefix_digest"] = prefix_digest
    if http_timing is not None:
        evidence["http_timing"] = http_timing.to_json()
    usage = response.get("usage") if response is not None else None
    if isinstance(usage, dict):
        token_usage: dict[str, int] = {}
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            val = usage.get(key)
            if type(val) is int and val >= 0:
                token_usage[key] = val
        for group, fields in (
            ("input_tokens_details", ("cached_tokens", "cache_write_tokens")),
            ("output_tokens_details", ("reasoning_tokens",)),
        ):
            details = usage.get(group)
            if isinstance(details, dict):
                for key in fields:
                    count = details.get(key)
                    if type(count) is int and count >= 0:
                        token_usage[key] = count
        if token_usage:
            evidence["token_usage"] = cast(JSONValue, token_usage)
    trace.calls.append(json.dumps(evidence, sort_keys=True, separators=(",", ":")))


def record_plan_assembly(
    provider_output: JSONObject, assembled_plan: JSONObject, grounded_intent: JSONObject
) -> None:
    """Bind bounded original draft bytes to host assembly on this exact scoped Provider call.

    Large drafts retain an explicit unavailable-content marker and their full digest.
    No observation grants goal satisfaction or changes the existing per-call byte budget.
    """
    trace = _TRACE.get()
    if trace is None or not trace.calls:
        return
    call = json.loads(trace.calls[-1])
    raw = json.dumps(provider_output, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    raw_bytes = len(raw.encode("utf-8"))
    call["plan_assembly"] = {
        "schema_version": "roboguide.provider-plan-assembly/v0.1",
        "profile": HOST_OBJECTIVE_PROFILE,
        "grounded_intent_digest": canonical_plan_digest(grounded_intent),
        "provider_output_digest": canonical_plan_digest(provider_output),
        "assembled_plan_digest": canonical_plan_digest(assembled_plan),
        "provider_output": provider_output
        if raw_bytes <= MAX_CALL_BYTES // 2
        else {"truncated": True, "byte_length": raw_bytes},
    }
    encoded = json.dumps(call, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if len(encoded.encode("utf-8")) <= MAX_CALL_BYTES:
        trace.calls[-1] = encoded
        _trim_optional_assemblies(trace, 0)


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
        for name in (
            "requested_reasoning_effort",
            "response_schema_name",
            "requested_service_tier",
            "observed_service_tier",
        ):
            setting = call.get(name)
            if name in call and (not isinstance(setting, str) or not 0 < len(setting) <= 256):
                raise ValueError("provenance provider request settings are invalid")
        if "stage" in call and (
            not isinstance(call["stage"], str)
            or call["stage"] != _PROVIDER_STAGES.get(str(call.get("response_schema_name")))
        ):
            raise ValueError("provenance provider stage does not match its response schema")
        if "cache_prefix_digest" in call:
            digest = call["cache_prefix_digest"]
            if not isinstance(digest, str) or _CACHE_PREFIX_DIGEST.fullmatch(digest) is None:
                raise ValueError("provenance cache prefix digest is invalid")
        if "token_usage" in call:
            token_usage = call["token_usage"]
            if not isinstance(token_usage, dict) or any(
                not isinstance(k, str) or type(v) is not int or v < 0
                for k, v in token_usage.items()
            ):
                raise ValueError("provenance token_usage must contain non-negative integer counts")
        if "http_timing" in call:
            _validate_http_timing(call["http_timing"])
        if "plan_assembly" in call:
            _validate_plan_assembly(call["plan_assembly"])


def _validate_http_timing(value: JSONValue) -> None:
    """Reject invalid HTTP observations while allowing older uninstrumented calls."""
    if not isinstance(value, dict) or set(value) != {
        "elapsed_ms",
        "headers_received_ms",
        "body_read_ms",
        "http_status_code",
        "upstream_request_id",
        "failure_phase",
    }:
        raise ValueError("provenance provider HTTP timing has invalid fields")
    elapsed = value["elapsed_ms"]
    headers = value["headers_received_ms"]
    body = value["body_read_ms"]
    status = value["http_status_code"]
    request_id = value["upstream_request_id"]
    phase = value["failure_phase"]
    if (
        type(elapsed) is not int
        or elapsed < 0
        or (headers is not None and (type(headers) is not int or not 0 <= headers <= elapsed))
        or (
            body is not None
            and (
                type(body) is not int
                or type(headers) is not int
                or not 0 <= body <= elapsed - headers
            )
        )
        or (status is not None and (type(status) is not int or not 100 <= status <= 599))
        or (
            request_id is not None
            and (not isinstance(request_id, str) or upstream_request_id([request_id]) is None)
        )
        or (headers is None and (status is not None or request_id is not None))
        or phase
        not in (None, "awaiting_headers", "reading_body", "http_status", "decoding_response")
        or ((phase == "awaiting_headers") != (headers is None))
        or (phase in (None, "http_status", "decoding_response") and body is None)
    ):
        raise ValueError("provenance provider HTTP timing is inconsistent")


def _validate_plan_assembly(value: JSONValue) -> None:
    """Refuse detached raw draft evidence while retaining explicit bounded unavailability."""
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "schema_version",
            "profile",
            "grounded_intent_digest",
            "provider_output_digest",
            "assembled_plan_digest",
            "provider_output",
        }
        or value.get("schema_version") != "roboguide.provider-plan-assembly/v0.1"
        or value.get("profile") != HOST_OBJECTIVE_PROFILE
    ):
        raise ValueError("invalid provider plan assembly evidence")
    for key in ("grounded_intent_digest", "provider_output_digest", "assembled_plan_digest"):
        digest = value[key]
        if not isinstance(digest, str) or re.fullmatch(r"sha256:[a-f0-9]{64}", digest) is None:
            raise ValueError("invalid provider plan assembly digest")
    raw = value["provider_output"]
    if not isinstance(raw, dict):
        raise ValueError("invalid original provider plan output")
    if raw.get("truncated") is True:
        size = raw.get("byte_length")
        if set(raw) != {"truncated", "byte_length"} or type(size) is not int or size <= 0:
            raise ValueError("invalid truncated provider plan evidence")
    elif canonical_plan_digest(raw) != value["provider_output_digest"]:
        raise ValueError("provider plan assembly raw output digest mismatch")
