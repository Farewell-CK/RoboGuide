"""Bounded, credential-redacted body evidence over the production HTTP forwarder.

Only copies are observed. Original request/response bytes, client retries and model
selection remain unchanged; an incomplete archive never certifies a complete run.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import Path
from urllib.parse import urlsplit

from roboguide_eval.accounting import (
    AccountingProxyConfig,
    AccountingProxyServer,
    _AccountingHandler,
)
from roboguide_eval.models import JSONObject, JSONValue


class BodyCapture:
    """Own bounded immutable copies, never headers or credential fingerprints."""

    def __init__(
        self,
        directory: Path,
        secrets: tuple[str, ...],
        *,
        max_calls: int = 2048,
        max_body_bytes: int = 8 * 1024 * 1024,
        max_total_bytes: int = 512 * 1024 * 1024,
    ) -> None:
        """Refuse reused storage and retain credential values in process memory only."""
        if min(max_calls, max_body_bytes, max_total_bytes) <= 0:
            raise ValueError("capture budgets must be positive")
        directory.mkdir(parents=True, exist_ok=False)
        self.directory = directory
        self._secrets = tuple(value.encode() for value in secrets if value)
        self._lock = threading.RLock()
        self.started = self.responses = self.bytes = self.losses = self.failures = 0
        self.max_calls = max_calls
        self.max_body_bytes = max_body_bytes
        self.max_total_bytes = max_total_bytes
        self.closed = False

    def redact(self, body: bytes) -> bytes:
        """Remove secrets from copies, including credential-bearing JSON error messages."""
        for secret in self._secrets:
            body = body.replace(secret, b"[REDACTED]")
        body = re.sub(rb"\bsk-[A-Za-z0-9_-]+", b"[REDACTED]", body)
        try:
            value: JSONValue = json.loads(body)
        except (ValueError, UnicodeError):
            return body

        def walk(item: JSONValue) -> JSONValue:
            """Redact authentication fields recursively while preserving tool arguments."""
            if isinstance(item, dict):
                return {
                    key: "[REDACTED]"
                    if key.lower()
                    in {
                        "authorization",
                        "api_key",
                        "openai_api_key",
                        "access_token",
                        "password",
                        "secret",
                    }
                    or (
                        key.lower() == "message"
                        and isinstance(child, str)
                        and re.search(r"api[ _-]?key|authorization|bearer ", child, re.I)
                    )
                    else walk(child)
                    for key, child in item.items()
                }
            if isinstance(item, list):
                return [walk(child) for child in item]
            return item

        sanitized = walk(value)
        return (
            body
            if sanitized == value
            else json.dumps(sanitized, ensure_ascii=False, allow_nan=False).encode()
        )

    def begin(self, method: str, path: str) -> int:
        """Allocate one call before forwarding and omit URL queries and all headers."""
        with self._lock:
            self.started += 1
            sequence = self.started
            self.save(
                sequence,
                "request-metadata.json",
                json.dumps({"method": method, "path": urlsplit(path).path}).encode(),
            )
            return sequence

    def save(self, sequence: int, suffix: str, body: bytes) -> None:
        """Write a bounded sanitized copy; observation failure cannot change HTTP execution."""
        with self._lock:
            try:
                if sequence > self.max_calls or len(body) > self.max_body_bytes:
                    self.losses += 1
                    return
                sanitized = self.redact(body)
                if (
                    len(sanitized) > self.max_body_bytes
                    or self.bytes + len(sanitized) > self.max_total_bytes
                ):
                    self.losses += 1
                    return
                with (self.directory / f"call-{sequence:05d}-{suffix}").open("xb") as target:
                    target.write(sanitized)
                self.bytes += len(sanitized)
            except Exception:  # noqa: BLE001 - copies cannot change forwarding
                self.failures += 1
            finally:
                self.status()

    def complete_call(self) -> None:
        """Count a returned or failed request independently of archive write success."""
        with self._lock:
            self.responses += 1
            self.status()

    def status(self, *, close: bool = False) -> JSONObject:
        """Expose loss, closure and outstanding calls without inferring a complete prefix."""
        with self._lock:
            self.closed = self.closed or close
            result: JSONObject = {
                "schema_version": "roboguide.provider-body-capture/v0.1",
                "calls_started": self.started,
                "responses_observed": self.responses,
                "bytes_written": self.bytes,
                "records_lost": self.losses,
                "write_failures": self.failures,
                "max_calls": self.max_calls,
                "max_body_bytes": self.max_body_bytes,
                "max_total_bytes": self.max_total_bytes,
                "closed": self.closed,
                "headers_recorded": False,
                "complete": self.closed
                and self.started == self.responses
                and self.losses == self.failures == 0,
            }
            try:
                temporary = self.directory / "capture-status.tmp"
                temporary.write_text(json.dumps(result) + "\n", encoding="utf-8")
                temporary.replace(self.directory / "capture-status.json")
            except Exception:  # noqa: BLE001 - disk errors remain observable
                self.failures += 1
                result.update(complete=False, write_failures=self.failures)
            return result


class CaptureProxyServer(AccountingProxyServer):
    """Use the existing byte-preserving forwarder with a separate bounded body observer."""

    def __init__(
        self, address: tuple[str, int], config: AccountingProxyConfig, capture: BodyCapture
    ) -> None:
        """Bind once and keep observation separate from the upstream client contract."""
        super().__init__(address, config)
        self.capture = capture
        self.RequestHandlerClass = _CaptureHandler


class _CaptureHandler(_AccountingHandler):
    """Observe copies at the existing forwarding and accounting boundaries."""

    server: CaptureProxyServer

    def _handle(self, method: str) -> None:
        """Allocate a capture identity without adding an upstream request or retry."""
        self._capture_id = self.server.capture.begin(method, self.path)
        super()._handle(method)

    def _read_body(self) -> bytes:
        """Save the actual input before the original slow upstream call starts."""
        body = super()._read_body()
        self.server.capture.save(self._capture_id, "request.json", body)
        return body

    def _record(
        self,
        *,
        request_utc: str,
        response_utc: str,
        latency_ms: float,
        status: int | None,
        request_body: bytes,
        response_body: bytes,
        error: str | None,
    ) -> None:
        """Keep full sanitized responses and unchanged production accounting metadata."""
        capture = self.server.capture
        capture.save(self._capture_id, "response.json", response_body)
        if self.command != "POST":
            capture.save(self._capture_id, "request.json", request_body)
        metadata: JSONObject = {
            "capture_id": self._capture_id,
            "request_utc": request_utc,
            "response_utc": response_utc,
            "latency_ms": latency_ms,
            "status": status,
            "error": error,
            "saved_request_sha256": hashlib.sha256(capture.redact(request_body)).hexdigest(),
            "saved_response_sha256": hashlib.sha256(capture.redact(response_body)).hexdigest(),
        }
        capture.save(self._capture_id, "result-metadata.json", json.dumps(metadata).encode())
        capture.complete_call()
        super()._record(
            request_utc=request_utc,
            response_utc=response_utc,
            latency_ms=latency_ms,
            status=status,
            request_body=request_body,
            response_body=response_body,
            error=error,
        )
