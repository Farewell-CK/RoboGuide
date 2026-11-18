"""Observe only Robonix chassis/state through Atlas and bounded MCP, without Pilot or actions."""

from __future__ import annotations

import copy
import json
import math
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from robonix_task.protocol import Liaison, message

CONTRACT = "robonix/primitive/chassis/state"
MAX_BYTES = 65536
MAX_AGE_MS = 5000


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Keep the fixed observation on the exact Atlas-discovered loopback endpoint."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        """Reject redirects instead of forwarding local read access elsewhere."""
        return None


def _rpc_result(raw: bytes, request_id: int) -> dict[str, Any]:
    """Decode the exact JSON or SSE JSON-RPC reply, refusing errors or ambiguous results."""
    text = raw.decode("utf-8")
    if text.lstrip().startswith("{"):
        replies = [json.loads(text)]
    else:
        replies = [
            json.loads(line[5:].strip()) for line in text.splitlines() if line.startswith("data:")
        ]
    selected = [r for r in replies if isinstance(r, dict) and r.get("id") == request_id]
    if len(selected) != 1 or "error" in selected[0]:
        raise ValueError("missing, ambiguous or failed MCP reply")
    result = selected[0].get("result")
    if not isinstance(result, dict) or result.get("isError"):
        raise ValueError("failed MCP observation")
    return result


def _endpoint(value: str) -> str:
    """Admit only a local MCP path and explicit port; no remote URL or credentials are allowed."""
    parsed = urllib.parse.urlsplit(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "0.0.0.0", "::1"}
        or parsed.port is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/") != "/mcp"
    ):
        raise ValueError("local MCP state endpoint required")
    host = "[::1]" if parsed.hostname == "::1" else "127.0.0.1"
    return f"http://{host}:{parsed.port}/mcp"


def _source_data(result: dict[str, Any]) -> dict[str, Any]:
    """Unwrap the source's std_msgs String without inventing posture or per-posture freshness."""
    content = result.get("content")
    texts = (
        [b.get("text") for b in content if isinstance(b, dict) and b.get("type") == "text"]
        if isinstance(content, list)
        else []
    )
    if len(texts) != 1 or not isinstance(texts[0], str):
        raise ValueError("one original MCP text result required")
    envelope = json.loads(texts[0])
    if not isinstance(envelope, dict) or not isinstance(envelope.get("data"), str):
        raise ValueError("original std_msgs String required")
    value = json.loads(envelope["data"])
    if not isinstance(value, dict) or value.get("posture") not in {"standing", "down", "unknown"}:
        raise ValueError("unsupported reported posture")
    age, stale_ms, stamp = value.get("age_ms"), value.get("stale_ms"), value.get("ts_ms")
    if (
        not isinstance(age, int | float)
        or isinstance(age, bool)
        or not math.isfinite(age)
        or age < 0
        or type(stale_ms) is not int
        or not 0 < stale_ms <= 60000
        or type(stamp) is not int
        or stamp <= 0
        or type(value.get("stale")) is not bool
    ):
        raise ValueError("source aggregate freshness metadata required")
    json.dumps(value, allow_nan=False)
    return value


def read_chassis_state(client: Liaison) -> dict[str, Any]:
    """Use only exact active chassis/state discovery, one state tool call and channel cleanup."""
    response = client.unary(
        "Query",
        message("robonix.atlas.QueryRequest", contract_id=CONTRACT, transport=3),
        "robonix.atlas.QueryResponse",
    )
    providers = [
        p
        for p in response.providers
        if p.state == 3
        and any(c.contract_id == CONTRACT and c.transport == 3 for c in p.capabilities)
    ]
    if len(providers) != 1:
        raise ValueError("one active chassis/state provider required")
    provider = providers[0]
    connection = client.unary(
        "ConnectCapability",
        message(
            "robonix.atlas.ConnectCapabilityRequest",
            consumer_id=client.consumer,
            provider_id=provider.id,
            contract_id=CONTRACT,
            transport=3,
        ),
        "robonix.atlas.ConnectCapabilityResponse",
    )
    session: str | None = None
    endpoint: str | None = None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    deadline = time.monotonic() + 3

    def post(payload: dict[str, Any], request_id: int | None = None) -> dict[str, Any]:
        """Send one bounded MCP handshake/state message within the original HTTP budget."""
        nonlocal session
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("chassis observation deadline expired")
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if session is not None:
            headers["Mcp-Session-Id"] = session
        req = urllib.request.Request(
            str(endpoint), data=json.dumps(payload).encode(), headers=headers
        )
        with opener.open(req, timeout=remaining) as reply:
            raw = reply.read(MAX_BYTES + 1)
            session = reply.headers.get("Mcp-Session-Id", session)
        if len(raw) > MAX_BYTES:
            raise ValueError("bounded state reply exceeded")
        return {} if request_id is None else _rpc_result(raw, request_id)

    try:
        endpoint = _endpoint(connection.endpoint)
        post(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "rg-readonly-chassis-state", "version": "1"},
                },
            },
            1,
        )
        post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        started = time.monotonic_ns()
        result = post(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "state", "arguments": {}},
            },
            2,
        )
        duration_ms = (time.monotonic_ns() - started) / 1_000_000
        value = _source_data(result)
        return {
            "received_at_ns": time.time_ns(),
            "request_duration_ms": duration_ms,
            "provider_id": provider.id,
            "registration_id": provider.registration_id,
            "contract_id": CONTRACT,
            "value": value,
        }
    finally:
        try:
            if endpoint is not None and session is not None:
                req = urllib.request.Request(
                    endpoint, method="DELETE", headers={"Mcp-Session-Id": session}
                )
                with opener.open(req, timeout=1):
                    pass
        except OSError:
            pass
        client.unary(
            "DisconnectCapability",
            message("robonix.atlas.DisconnectCapabilityRequest", channel_id=connection.channel_id),
            "robonix.atlas.DisconnectCapabilityResponse",
        )


def chassis_view(observation: dict[str, Any], now_ns: int) -> dict[str, Any]:
    """Age cached aggregate evidence without converting it into physical or motion authority."""
    result = copy.deepcopy(observation)
    value, received = result.get("value"), result.get("received_at_ns")
    age = None
    if isinstance(value, dict) and type(received) is int and 0 < received <= now_ns:
        original, duration = value.get("age_ms"), result.get("request_duration_ms")
        if (
            isinstance(original, int | float)
            and not isinstance(original, bool)
            and isinstance(duration, int | float)
            and not isinstance(duration, bool)
        ):
            age = original + duration + (now_ns - received) / 1_000_000
    current = (
        age is not None
        and 0 <= age < MAX_AGE_MS
        and isinstance(value, dict)
        and value.get("stale") is False
        and age < value.get("stale_ms", 0)
    )
    result.update(
        availability="current" if current else "stale" if value is not None else "unavailable",
        age_ms=age,
        freshness_basis=(
            "Robonix aggregate age + request duration + observer retention; "
            "individual posture timestamp unavailable"
        ),
        motion_authorized=False,
        damping_state="not_provided",
    )
    return result


class ChassisStateObserver:
    """Keep optional state polling in the existing Connector thread with no native task journal."""

    def __init__(self) -> None:
        """Fix the only allowed local contract and bound the original discovery calls."""
        self.client = Liaison(
            "127.0.0.1:50051", "rg/readonly-chassis-state", "local:rg-state", rpc_timeout=1
        )
        self.lock = threading.Lock()
        self.latest: dict[str, Any] = {"value": None, "reason": "not observed"}

    def sample(self) -> None:
        """Replace failed observations with unavailable, preserving no false fresh posture."""
        try:
            value = read_chassis_state(self.client)
        except Exception as error:
            value = {"value": None, "reason": f"{type(error).__name__}: chassis/state unavailable"}
        with self.lock:
            self.latest = value

    def read(self) -> dict[str, Any]:
        """Return only cached facts; HTTP queries never perform a native tool operation."""
        with self.lock:
            return chassis_view(self.latest, time.time_ns())

    def close(self) -> None:
        """Close the observer's existing local transport without any action or Pilot submission."""
        self.client.close()
