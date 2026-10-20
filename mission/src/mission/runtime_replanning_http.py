"""Fixed mTLS peers and before-I/O provider receipts for runtime MI work."""

from __future__ import annotations

import base64
import hashlib
import json
import ssl
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlparse

from mission.models import JSONObject
from mission.provider_errors import MissionProviderError
from mission.responses import JsonTransport
from mission.runtime_work_store import MAX_DOCUMENT_BYTES, RuntimeWorkError, RuntimeWorkStore
from mission.submission_evidence import canonical_plan_digest


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    """Keep original client identity and tenant on the configured peer only."""

    def redirect_request(
        self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        """Refuse redirect-driven re-execution or delegation to another endpoint."""
        return None


class RuntimeJsonPeer:
    """Connect to one deployment-owned HTTPS peer with an actual certificate identity."""

    def __init__(
        self,
        endpoint: str,
        tenant: str,
        principal: str,
        certificate: Path,
        key: Path,
        ca: Path,
    ) -> None:
        """Validate the origin; the actual server decides principal permission."""
        parsed = urlparse(endpoint)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
            or not tenant
            or tenant == "*"
            or len(tenant) > 256
            or not principal
            or len(principal) > 128
        ):
            raise RuntimeWorkError("runtime MI peer requires a fixed mTLS origin and exact tenant")
        context = ssl.create_default_context(cafile=str(ca))
        context.load_cert_chain(str(certificate), str(key))
        self.endpoint, self.tenant, self.principal = endpoint.rstrip("/"), tenant, principal
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(context=context),
            _NoRedirects(),
        )

    def request(
        self, method: str, path: str, body: dict[str, Any] | None = None, timeout: float = 5
    ) -> tuple[int, dict[str, Any]]:
        """Perform one bounded original call, returning definite HTTP outcomes without retrying."""
        if not path.startswith("/v1/") or not 0 < timeout <= 300:
            raise RuntimeWorkError("invalid original runtime peer request")
        headers = {"Accept": "application/json", "X-RoboGuide-Tenant": self.tenant}
        payload = None
        if body is not None:
            payload = json.dumps(
                body, ensure_ascii=False, separators=(",", ":"), allow_nan=False
            ).encode()
            if len(payload) > MAX_DOCUMENT_BYTES:
                raise RuntimeWorkError("runtime MI HTTP body exceeds its bound")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            self.endpoint + path, data=payload, headers=headers, method=method
        )
        try:
            response = self._opener.open(request, timeout=timeout)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            raw = response.read(MAX_DOCUMENT_BYTES + 1)
            status = int(response.code)
        if len(raw) > MAX_DOCUMENT_BYTES:
            raise RuntimeWorkError("runtime MI peer response exceeds its bound")
        document = json.loads(raw)
        if not isinstance(document, dict):
            raise RuntimeWorkError("runtime MI peer response is not an object")
        return status, document


class JournaledRuntimeTransport:
    """Persist original provider requests and responses without credentials."""

    def __init__(self, store: RuntimeWorkStore, transport: JsonTransport | None = None) -> None:
        """Retain an explicit production transport or a clearly labelled conformance fixture."""
        self.store = store
        self.transport = transport
        self.before_call: Callable[[str, str, str, str], None] | None = None
        self._context: ContextVar[tuple[str, str] | None] = ContextVar(
            "runtime_mi_effect", default=None
        )

    @contextmanager
    def bind(self, work_id: str, phase: str) -> Iterator[None]:
        """Attribute one actual adapter call to its already persisted original work ticket."""
        token = self._context.set((work_id, phase))
        try:
            yield
        finally:
            self._context.reset(token)

    def post_json(
        self, url: str, headers: Mapping[str, str], payload: JSONObject, timeout_seconds: float
    ) -> JSONObject:
        """Commit before I/O and never repeat an uncertain provider call."""
        context = self._context.get()
        if context is None:
            raise RuntimeWorkError("provider work has no original durable runtime ticket")
        work_id, phase = context
        original_request = {
            "endpoint_digest": "sha256:" + hashlib.sha256(url.encode()).hexdigest(),
            "payload": payload,
        }

        def send(remaining: float) -> dict[str, Any]:
            """Keep injected JSON fixtures explicit and retain actual wire bytes in production."""
            if self.before_call is not None:
                self.before_call(
                    work_id,
                    phase,
                    self.store.effect_nonce(work_id, phase),
                    canonical_plan_digest(original_request),
                )
                remaining = self.store.remaining(work_id) / 1000
                if remaining <= 0:
                    raise RuntimeWorkError(
                        "original provider budget expired while obtaining permission"
                    )
            if self.transport is not None:
                return {
                    "representation": "injected-json-transport",
                    "response": self.transport.post_json(
                        url, headers, payload, min(timeout_seconds, remaining)
                    ),
                }
            request = urllib.request.Request(
                url,
                data=json.dumps(payload, ensure_ascii=False).encode(),
                headers=dict(headers),
                method="POST",
            )
            opener = urllib.request.build_opener(_NoRedirects())
            try:
                response = opener.open(request, timeout=min(timeout_seconds, remaining))
            except urllib.error.HTTPError as error:
                response = error
            with response:
                raw = response.read(1024 * 1024 + 1)
                return {
                    "representation": "original-http-response/v0.1",
                    "status_code": int(response.code),
                    "truncated": len(raw) > 1024 * 1024,
                    "raw_response_base64": base64.b64encode(raw[: 1024 * 1024]).decode("ascii"),
                }

        result = self.store.perform(
            work_id,
            phase,
            original_request,
            send,
        )
        if result["representation"] == "injected-json-transport":
            return cast(JSONObject, result["response"])
        if result["truncated"]:
            raise MissionProviderError(
                "runtime MI provider response exceeds its original byte budget"
            )
        if not 200 <= result["status_code"] < 300:
            raise MissionProviderError(
                "runtime MI provider rejected request", status_code=result["status_code"]
            )
        decoded = json.loads(base64.b64decode(result["raw_response_base64"], validate=True))
        if not isinstance(decoded, dict):
            raise MissionProviderError("runtime MI provider response is not a JSON object")
        return cast(JSONObject, decoded)
