"""Pinned dog2 protobuf messages and Atlas-discovered Liaison transport."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

import grpc
from google.protobuf import (
    descriptor_pb2,
    descriptor_pool,
    json_format,
    message_factory,
)

SUBMIT_CONTRACT = "robonix/system/liaison/submit"
SUBMIT_ROUTE = "/robonix.contracts.RobonixSystemLiaisonSubmit/SubmitTask"
EVENT_NAMES = (
    "text_chunk",
    "plan",
    "batch_result",
    "status",
    "final_text",
    "node_state",
    "task_state",
)
POOL = descriptor_pool.DescriptorPool()
VENDOR = Path(__file__).with_name("vendor")
for _entry in json.loads((VENDOR / "provenance.json").read_text(encoding="utf-8-sig"))["files"]:
    if hashlib.sha256((VENDOR / _entry["name"]).read_bytes()).hexdigest() != _entry["sha256"]:
        raise ValueError("pinned protocol integrity check failed: " + _entry["name"])
for _file in descriptor_pb2.FileDescriptorSet.FromString(
    Path(__file__).with_name("vendor").joinpath("dog2.pb").read_bytes()
).file:
    POOL.Add(_file)


def message(name: str, **fields: Any) -> Any:
    """Construct a message from the pinned descriptor without generated runtime imports."""
    return message_factory.GetMessageClass(POOL.FindMessageTypeByName(name))(**fields)


def event_dict(raw: bytes) -> dict[str, Any]:
    """Decode either deployed raw events or wrappers, retaining every known field."""
    wrapper = message("robonix.liaison.SubmitTask_Response")
    wrapper.ParseFromString(raw)
    if wrapper.HasField("event"):
        event = wrapper.event
    else:
        event = message("robonix.pilot.PilotEvent")
        event.ParseFromString(raw)
    if not event.session_id or event.event_kind >= len(EVENT_NAMES):
        raise ValueError("event has no session identity or has an unsupported kind")
    result: dict[str, Any] = json_format.MessageToDict(
        event,
        preserving_proto_field_name=True,
        always_print_fields_with_no_presence=True,
    )
    result["kind"] = EVENT_NAMES[event.event_kind]
    return result


def execution_intent(request: dict[str, Any]) -> dict[str, Any]:
    """Read the real Node workflow shape and the earlier laboratory envelope."""
    invocation = request.get("invocation")
    if not isinstance(invocation, dict):
        raise ValueError("invocation object required")
    intent = invocation.get("intent", invocation)
    if not isinstance(intent, dict):
        raise ValueError("intent object required")
    return cast(dict[str, Any], intent)


def task(record: dict[str, Any], user_id: str, *, abort: bool = False) -> Any:
    """Build an API Task using persisted identities, never generating IDs at send time."""
    context: dict[str, Any] = {
        "user_id": user_id,
        "modality": "api",
        "client": "rg-pilot-lab",
        "interaction_mode": "abort" if abort else "task",
    }
    if abort:
        context["abort_turn"] = True
    intent = execution_intent(record["request"])
    text = (
        ""
        if abort
        else intent["objective"]
        + "\nRG intent: "
        + json.dumps(intent, sort_keys=True, ensure_ascii=False, allow_nan=False)
    )
    return message(
        "robonix.pilot.Task",
        task_id=record["abort_id"] if abort else record["task_id"],
        session_id=record["session_id"],
        source=2,
        text=text,
        context_json=json.dumps(context, ensure_ascii=False),
        timestamp_ms=record["created_ns"] // 1_000_000,
    )


class Liaison:
    """Own bounded native RPCs; discovery has no fixed-port fallback or task retries."""

    def __init__(self, atlas: str, consumer: str, user_id: str, rpc_timeout: float = 3) -> None:
        """Freeze the deployment identity and initialize process-local stream tracking."""
        parsed = urlparse("grpc://" + atlas)
        if not parsed.hostname or not parsed.port or parsed.path:
            raise ValueError("atlas must be an explicit host:port")
        self.atlas, self.consumer, self.user_id = atlas, consumer, user_id
        self.rpc_timeout = rpc_timeout
        self.lock = threading.Lock()
        self.calls: set[Any] = set()
        self.closed = False

    def channel(self, endpoint: str) -> Any:
        """Open the native trusted-network channel with desktop proxy routing disabled."""
        return grpc.insecure_channel(
            endpoint,
            options=(
                ("grpc.enable_http_proxy", 0),
                ("grpc.max_receive_message_length", 262144),
            ),
        )

    def unary(self, route: str, request: Any, response_name: str) -> Any:
        """Issue one bounded Atlas operation without automatic application-level retry."""
        with self.channel(self.atlas) as channel:
            call = channel.unary_unary(
                "/robonix.atlas.Atlas/" + route,
                request_serializer=lambda value: value.SerializeToString(),
                response_deserializer=lambda raw: message(response_name).FromString(raw),
            )
            return call(request, timeout=self.rpc_timeout)

    def discover(self) -> Any:
        """Require exactly one active Liaison with the exact gRPC contract."""
        response = self.unary(
            "Query",
            message("robonix.atlas.QueryRequest", contract_id=SUBMIT_CONTRACT, transport=1),
            "robonix.atlas.QueryResponse",
        )
        providers = [
            p
            for p in response.providers
            if p.state == 3
            and any(c.contract_id == SUBMIT_CONTRACT and c.transport == 1 for c in p.capabilities)
        ]
        if len(providers) != 1:
            raise ValueError("expected one active Liaison provider")
        return providers[0]

    def stream(
        self,
        request: Any,
        timeout: float,
        *,
        on_connect: Callable[[dict[str, Any]], Any] | None = None,
        expected_provider: dict[str, Any] | None = None,
        send_guard: Callable[[], AbstractContextManager[Any]] | None = None,
        deadline: float | None = None,
    ) -> Iterator[tuple[bytes, dict[str, Any], dict[str, Any]]]:
        """Discover, connect once, consume events and close the Atlas edge on every exit."""
        deadline = time.monotonic() + timeout if deadline is None else deadline
        provider = self.discover()
        identity = {
            "provider_id": provider.id,
            "registration_id": provider.registration_id,
        }
        if expected_provider and any(identity[k] != expected_provider[k] for k in identity):
            raise ValueError("Liaison registration changed; original session ownership unknown")
        connection = self.unary(
            "ConnectCapability",
            message(
                "robonix.atlas.ConnectCapabilityRequest",
                consumer_id=self.consumer,
                provider_id=provider.id,
                contract_id=SUBMIT_CONTRACT,
                transport=1,
            ),
            "robonix.atlas.ConnectCapabilityResponse",
        )
        try:
            parsed = urlparse(
                "grpc://" + connection.endpoint.removeprefix("http://").removeprefix("grpc://")
            )
            if not parsed.hostname or not parsed.port or parsed.path:
                raise ValueError("invalid discovered Liaison endpoint")
            host = parsed.hostname
            if host in ("127.0.0.1", "localhost", "0.0.0.0", "::1"):
                host = urlparse("grpc://" + self.atlas).hostname
            endpoint = f"[{host}]:{parsed.port}" if ":" in host else f"{host}:{parsed.port}"
            identity["endpoint"] = endpoint
            if on_connect:
                on_connect(identity)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("original stream budget expired during discovery")
            # One immutable execution cannot silently move to a replacement provider.
            with self.channel(endpoint) as channel:
                with send_guard() if send_guard else nullcontext(), self.lock:
                    if self.closed:
                        raise RuntimeError("transport closed")
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("original budget expired before native call initiation")
                    call = channel.unary_stream(
                        SUBMIT_ROUTE,
                        request_serializer=lambda value: value.SerializeToString(),
                        response_deserializer=lambda raw: raw,
                    )(request, timeout=remaining)
                    self.calls.add(call)
                try:
                    for raw in call:
                        yield raw, event_dict(raw), identity
                finally:
                    call.cancel()
                    with self.lock:
                        self.calls.discard(call)
        finally:
            if connection.channel_id:
                try:
                    self.unary(
                        "DisconnectCapability",
                        message(
                            "robonix.atlas.DisconnectCapabilityRequest",
                            channel_id=connection.channel_id,
                        ),
                        "robonix.atlas.DisconnectCapabilityResponse",
                    )
                except Exception:
                    # Closing bookkeeping cannot establish a physical result or trigger replay.
                    pass

    def close(self) -> None:
        """Detach local streams during shutdown; this does not claim robot cancellation."""
        with self.lock:
            self.closed = True
            for call in self.calls:
                call.cancel()
