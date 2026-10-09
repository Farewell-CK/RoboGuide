"""Local LLM accounting proxy: harness-owned token cost measurement.

Systems under test (currently EMOS) report only per-agent ``total_tokens``
and discard everything else the provider returns. The proxy closes that gap
without touching the baseline: the system under test points its
``OPENAI_BASE_URL`` at this proxy, which forwards every request unchanged to
the real endpoint and records, per call, the provider's numeric ``usage`` counters
(prompt/completion tokens, ``prompt_tokens_details.cached_tokens``,
``completion_tokens_details.reasoning_tokens`` when present), request and
response timestamps, latency, and transport metadata, as crash-safe NDJSON.

Design invariants:

- Transparent: bytes in, bytes out; headers other than hop-by-hop are
  forwarded unchanged (credentials never leave the machine — they pass
  through to the configured upstream and are never written to the log);
- Complete: failed upstream calls (non-2xx, connection errors) are recorded
  too — they are real cost/latency evidence;
- Honest: streaming requests are passed through without parsing (usage
  recorded as ``null`` with a reason); nothing is inferred or zero-filled.

Note on TTFT: clients that use non-streaming requests (EMOS does) make
first-token latency unobservable by construction; the proxy records total
call latency, which is the meaningful quantity for throughput experiments.
"""

from __future__ import annotations

import json
import math
import re
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Final
from urllib import error as urlerror
from urllib import request as urlrequest
from urllib.parse import urlsplit

_HOP_BY_HOP_HEADERS: Final = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
        "host",
        "content-length",
        "accept-encoding",
    }
)
_DEFAULT_UPSTREAM_TIMEOUT_SECONDS: Final = 600.0
_MAX_METADATA_BYTES: Final = 4 * 1024 * 1024
_PUBLIC_LABEL: Final = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}\Z")
_TOKEN_FIELDS: Final = frozenset(
    {
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "input_tokens",
        "output_tokens",
        "cached_tokens",
        "reasoning_tokens",
        "audio_tokens",
        "accepted_prediction_tokens",
        "rejected_prediction_tokens",
    }
)
_DETAIL_FIELDS: Final = frozenset(
    {
        "prompt_tokens_details",
        "completion_tokens_details",
        "input_tokens_details",
        "output_tokens_details",
    }
)


def _public_label(value: object) -> str | None:
    """Admit bounded public identifiers, excluding credential-shaped strings."""
    if not isinstance(value, str) or value.lower().startswith(("sk-", "bearer", "basic")):
        return None
    return value if _PUBLIC_LABEL.fullmatch(value) else None


def _metadata_document(body: bytes) -> dict[str, object]:
    """Parse bounded JSON metadata without persisting request or response content."""
    if len(body) > _MAX_METADATA_BYTES:
        return {}
    try:
        document = json.loads(body)
    except (ValueError, UnicodeError, RecursionError):
        return {}
    return document if isinstance(document, dict) else {}


def _token_usage(value: object) -> dict[str, object] | None:
    """Retain only Provider-reported numeric token counters and known details."""
    if not isinstance(value, dict):
        return None
    usage: dict[str, object] = {}
    for key, item in value.items():
        if key in _TOKEN_FIELDS and type(item) is int and item >= 0:
            usage[key] = item
        elif key in _DETAIL_FIELDS and isinstance(item, dict):
            details = {
                name: count
                for name, count in item.items()
                if name in _TOKEN_FIELDS and type(count) is int and count >= 0
            }
            usage[key] = details
    return usage or None


def _request_parameters(document: dict[str, object]) -> dict[str, object]:
    """Observe non-sensitive inference parameters without copying prompts or tools."""
    parameters: dict[str, object] = {}
    for name in (
        "temperature",
        "top_p",
        "seed",
        "max_tokens",
        "max_completion_tokens",
        "max_output_tokens",
    ):
        value = document.get(name)
        if (
            isinstance(value, int | float)
            and not isinstance(value, bool)
            and -(2**63) < value < 2**63
            and math.isfinite(value)
        ):
            parameters[name] = value
    effort = document.get("reasoning_effort")
    reasoning = document.get("reasoning")
    if isinstance(reasoning, dict):
        effort = reasoning.get("effort")
    if effort in ("none", "minimal", "low", "medium", "high", "xhigh"):
        parameters["reasoning_effort"] = effort
    if type(document.get("store")) is bool:
        parameters["store"] = document["store"]
    return parameters


def utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string.

    Returns:
        A timezone-aware ISO-8601 timestamp with millisecond precision.
    """
    return datetime.now(UTC).isoformat(timespec="milliseconds")


@dataclass(frozen=True, slots=True)
class AccountingProxyConfig:
    """Configure one accounting proxy instance.

    Attributes:
        upstream_base_url: Real OpenAI-compatible endpoint that receives the
            forwarded requests (for example the relay URL from ``emos.env``).
        log_path: NDJSON accounting log; appended, one record per call.
        upstream_timeout_seconds: Per-call upstream timeout; provider calls
            in agentic workloads routinely exceed 60 seconds.
        run_id: Optional public run identity; requires an unused log path.
        max_records: Maximum persisted calls, with overflow explicitly recorded.
        max_record_bytes: Maximum bytes per observation, excluding forwarded bodies.
    """

    upstream_base_url: str
    log_path: Path
    upstream_timeout_seconds: float = _DEFAULT_UPSTREAM_TIMEOUT_SECONDS
    run_id: str | None = None
    max_records: int = 10000
    max_record_bytes: int = 65536

    def __post_init__(self) -> None:
        """Reject credential-bearing URLs and invalid observation budgets before serving."""
        parsed = urlsplit(self.upstream_base_url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or any((parsed.username, parsed.password, parsed.query, parsed.fragment))
        ):
            raise ValueError(
                "upstream must be an HTTP(S) URL without credentials, query or fragment"
            )
        if not math.isfinite(self.upstream_timeout_seconds) or self.upstream_timeout_seconds <= 0:
            raise ValueError("upstream timeout must be finite and positive")
        if self.run_id is not None and _public_label(self.run_id) != self.run_id:
            raise ValueError("run_id must be a bounded public identifier")
        if type(self.max_records) is not int or self.max_records <= 0:
            raise ValueError("max_records must be positive")
        if type(self.max_record_bytes) is not int or self.max_record_bytes < 1024:
            raise ValueError("max_record_bytes must be at least 1024")


class AccountingProxyServer(ThreadingHTTPServer):
    """Threaded HTTP server forwarding to the upstream and recording usage."""

    config: AccountingProxyConfig
    daemon_threads: bool = True

    def __init__(self, server_address: tuple[str, int], config: AccountingProxyConfig) -> None:
        """Bind the proxy server to an address with its configuration.

        Args:
            server_address: ``(host, port)`` to listen on; port ``0`` picks
                an ephemeral port (useful for tests).
            config: The proxy configuration.
        """
        if config.run_id is not None and config.log_path.exists():
            raise ValueError("run-bound observation requires an unused log path")
        self.config = config
        self._sequence_lock = threading.Lock()
        self._sequence = 0
        self._record_lock = threading.RLock()
        self._idle = threading.Condition(self._record_lock)
        self._records_written = 0
        self._records_dropped = 0
        self._write_failures = 0
        self._closed = False
        self._calls_started = 0
        self._in_flight = 0
        super().__init__(server_address, _AccountingHandler)

    def next_sequence(self) -> int:
        """Return the next monotonically increasing call sequence number.

        Returns:
            A per-proxy unique sequence number for one recorded call.
        """
        with self._sequence_lock:
            self._sequence += 1
            return self._sequence

    def append_record(self, record: dict[str, object]) -> None:
        """Append bounded accounting evidence, isolating storage failures from forwarding.

        Args:
            record: The complete record for one call.
        """
        with self._record_lock:
            try:
                encoded = json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n"
                if (
                    self._records_written + self._records_dropped >= self.config.max_records
                    or len(encoded.encode("utf-8")) > self.config.max_record_bytes
                ):
                    self._records_dropped += 1
                else:
                    self.config.log_path.parent.mkdir(parents=True, exist_ok=True)
                    with self.config.log_path.open("a", encoding="utf-8") as log_file:
                        log_file.write(encoded)
                        log_file.flush()
                    self._records_written += 1
            except (OSError, ValueError, TypeError):
                self._records_dropped += 1
                self._write_failures += 1
            self._write_status()

    def recording_status(self) -> dict[str, object]:
        """Expose loss explicitly; an absent sidecar is never complete evidence."""
        with self._record_lock:
            return {
                "schema_version": "roboguide.provider-observation-status/v0.1",
                "run_id": self.config.run_id,
                "records_written": self._records_written,
                "calls_started": self._calls_started,
                "in_flight": self._in_flight,
                "records_dropped": self._records_dropped,
                "write_failures": self._write_failures,
                "max_records": self.config.max_records,
                "max_record_bytes": self.config.max_record_bytes,
                "closed": self._closed,
                "complete": (
                    self._closed
                    and self._in_flight == 0
                    and self._calls_started == self._records_written
                    and self._records_dropped == 0
                    and self._write_failures == 0
                ),
            }

    def call_started(self) -> None:
        """Fence completeness while a client request is still being forwarded."""
        with self._record_lock:
            self._calls_started += 1
            self._in_flight += 1

    def call_finished(self) -> None:
        """Refresh closure evidence after this handler has finished its forwarding attempt."""
        with self._record_lock:
            self._in_flight -= 1
            self._write_status()
            self._idle.notify_all()

    def wait_for_idle(self, timeout_seconds: float) -> bool:
        """Boundedly drain already accepted calls after stopping new HTTP acceptance.

        Callers stop their owned client and use ``shutdown()`` first. A timeout returns
        false; it does not invent a response, cancel a model call or certify closure.
        Body observers finish inside the handler before ``call_finished`` notifies us.
        """
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("drain timeout must be finite and positive")
        with self._idle:
            return self._idle.wait_for(lambda: self._in_flight == 0, timeout=timeout_seconds)

    def server_close(self) -> None:
        """Mark graceful observation closure; a live or killed writer never proves completeness."""
        super().server_close()
        with self._record_lock:
            self._closed = True
            self._write_status()

    def _write_status(self) -> None:
        """Best-effort atomically replace the bounded observer status beside the log."""
        target = self.config.log_path.with_name(self.config.log_path.name + ".status.json")
        temporary = target.with_name(target.name + ".tmp")
        try:
            temporary.write_text(json.dumps(self.recording_status()), encoding="utf-8")
            temporary.replace(target)
        except (OSError, ValueError):
            self._write_failures += 1
            # In-memory status stays invalid even if no file can be written.


class _AccountingHandler(BaseHTTPRequestHandler):
    """Forward one request to the upstream and record the observed call."""

    server: AccountingProxyServer

    def log_message(self, format_string: str, *args: object) -> None:
        """Silence the default per-request stderr chatter.

        Args:
            format_string: The printf-style format string.
            *args: Format arguments.
        """

    def _forwarded_headers(self) -> dict[str, str]:
        """Copy client headers minus hop-by-hop fields.

        Returns:
            A header map safe to send to the upstream.
        """
        forwarded: dict[str, str] = {}
        for key, value in self.headers.items():
            if key.lower() not in _HOP_BY_HOP_HEADERS:
                forwarded[key] = value
        forwarded.setdefault("Content-Type", "application/json")
        return forwarded

    def _read_body(self) -> bytes:
        """Read the request body announced by ``Content-Length``.

        Returns:
            The raw request body bytes (empty when absent).
        """
        length_header = self.headers.get("Content-Length")
        if length_header is None:
            return b""
        try:
            length = int(length_header)
        except ValueError:
            return b""
        if length <= 0:
            return b""
        return self.rfile.read(length)

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
        """Write one accounting record for the observed call.

        Args:
            request_utc: Request start timestamp.
            response_utc: Response completion timestamp.
            latency_ms: Wall-clock call latency in milliseconds.
            status: Upstream HTTP status, or ``None`` on transport failure.
            request_body: The raw request bytes (metadata extracted).
            response_body: The raw response bytes (usage extracted).
            error: Transport failure reason, when applicable.
        """
        request_document = _metadata_document(request_body)
        stream_requested = request_document.get("stream") is True
        prompt_messages: int | None = None
        messages = request_document.get("messages")
        if isinstance(messages, list):
            prompt_messages = len(messages)
        usage: dict[str, object] | None = None
        response_document: dict[str, object] = {}
        if status is not None and 200 <= status < 300 and response_body:
            response_document = _metadata_document(response_body)
            usage = _token_usage(response_document.get("usage"))
        response_model = _public_label(response_document.get("model"))
        record: dict[str, object] = {
            "schema_version": "roboguide.provider-call-observation/v0.1",
            "run_id": self.server.config.run_id,
            "seq": self.server.next_sequence(),
            "request_utc": request_utc,
            "response_utc": response_utc,
            "latency_ms": round(latency_ms, 1),
            "path": urlsplit(self.path).path
            if urlsplit(self.path).path
            in {"/responses", "/v1/responses", "/v1/chat/completions", "/v1/models"}
            else "unclassified",
            "method": self.command,
            "status": status,
            "stream": stream_requested,
            "prompt_messages": prompt_messages,
            "request_bytes": len(request_body),
            "response_bytes": len(response_body),
            "usage": usage,
            "error": error,
            "requested_model": _public_label(request_document.get("model")),
            "request_parameters": _request_parameters(request_document),
            "response_model": response_model,
            "response_id": _public_label(response_document.get("id")),
            "system_fingerprint": _public_label(response_document.get("system_fingerprint")),
            "model_observation_status": "available"
            if response_model is not None
            else "unavailable",
        }
        if stream_requested and usage is None:
            record["usage_note"] = "streaming response; usage not parsed"
        self.server.append_record(record)

    def _respond(self, status: int, content_type: str, body: bytes) -> None:
        """Send one complete HTTP response to the client.

        Args:
            status: HTTP status code.
            content_type: Response content type.
            body: Raw response body bytes.
        """
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle(self, method: str) -> None:
        """Track in-flight forwarding so shutdown cannot certify an incomplete prefix."""
        self.server.call_started()
        try:
            self._forward(method)
        finally:
            self.server.call_finished()

    def _forward(self, method: str) -> None:
        """Forward one request and record it, whatever the outcome.

        Args:
            method: The HTTP method to forward (``GET`` or ``POST``).
        """
        request_utc = utc_now_iso()
        started = time.monotonic()
        request_body = self._read_body() if method == "POST" else b""
        upstream_url = self.server.config.upstream_base_url.rstrip("/") + self.path
        forward_request = urlrequest.Request(
            upstream_url, data=request_body if method == "POST" else None, method=method
        )
        for key, value in self._forwarded_headers().items():
            forward_request.add_header(key, value)
        response_utc = utc_now_iso()
        try:
            with urlrequest.urlopen(
                forward_request, timeout=self.server.config.upstream_timeout_seconds
            ) as upstream_response:
                response_body = upstream_response.read()
                status = upstream_response.status
                content_type = upstream_response.headers.get("Content-Type", "application/json")
        except urlerror.HTTPError as http_error:
            response_body = http_error.read()
            status = http_error.code
            content_type = http_error.headers.get("Content-Type", "application/json")
            response_utc = utc_now_iso()
            self._record(
                request_utc=request_utc,
                response_utc=response_utc,
                latency_ms=(time.monotonic() - started) * 1000.0,
                status=status,
                request_body=request_body,
                response_body=response_body,
                error=None,
            )
            self._respond(status, content_type, response_body)
            return
        except (urlerror.URLError, TimeoutError, OSError) as transport_error:
            response_utc = utc_now_iso()
            latency_ms = (time.monotonic() - started) * 1000.0
            self._record(
                request_utc=request_utc,
                response_utc=response_utc,
                latency_ms=latency_ms,
                status=None,
                request_body=request_body,
                response_body=b"",
                error=f"upstream transport failure: {type(transport_error).__name__}",
            )
            self._respond(
                502, "application/json", b'{"error": "accounting proxy upstream failure"}'
            )
            return
        response_utc = utc_now_iso()
        self._record(
            request_utc=request_utc,
            response_utc=response_utc,
            latency_ms=(time.monotonic() - started) * 1000.0,
            status=status,
            request_body=request_body,
            response_body=response_body,
            error=None,
        )
        self._respond(status, content_type, response_body)

    def do_GET(self) -> None:  # noqa: N802 - http.server API naming
        """Forward a GET request (health checks, model listings).

        State changes:
            Forwards to the upstream and records the call.
        """
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802 - http.server API naming
        """Forward a POST request (chat completions and friends).

        State changes:
            Forwards to the upstream and records the call with its usage.
        """
        self._handle("POST")


def read_accounting_log(log_path: Path) -> list[dict[str, object]]:
    """Read every record from one accounting NDJSON log.

    Args:
        log_path: The log file written by :class:`AccountingProxyServer`.

    Returns:
        The list of parsed records; malformed lines are skipped.
    """
    if not log_path.is_file():
        return []
    records: list[dict[str, object]] = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        try:
            document = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(document, dict):
            records.append(document)
    return records


def _usage_field(records: list[dict[str, object]], field: str) -> int:
    """Sum one usage field over all records that carry it.

    Args:
        records: Parsed accounting records.
        field: The top-level usage field name to sum.

    Returns:
        The summed token count; records without the field contribute zero.
    """
    total = 0
    for record in records:
        usage = record.get("usage")
        if not isinstance(usage, dict):
            continue
        value = usage.get(field)
        if isinstance(value, int) and not isinstance(value, bool):
            total += value
    return total


def _usage_detail_field(records: list[dict[str, object]], detail: str, field: str) -> int:
    """Sum one nested usage-detail field over all records that carry it.

    Args:
        records: Parsed accounting records.
        detail: The usage detail object name (for example
            ``prompt_tokens_details``).
        field: The field inside the detail object to sum.

    Returns:
        The summed token count; absent details contribute zero.
    """
    total = 0
    for record in records:
        usage = record.get("usage")
        if not isinstance(usage, dict):
            continue
        details = usage.get(detail)
        if not isinstance(details, dict):
            continue
        value = details.get(field)
        if isinstance(value, int) and not isinstance(value, bool):
            total += value
    return total


def summarize_accounting(records: list[dict[str, object]]) -> dict[str, object]:
    """Aggregate accounting records into cost-relevant totals.

    Args:
        records: Parsed accounting records from one proxy log.

    Returns:
        Call counts, token totals (including cached and reasoning
        components when the upstream reported them), and latency summary.
        Totals cover only calls whose usage the upstream actually returned;
        calls without usage are counted under ``calls_without_usage``.
    """
    latencies = [
        value
        for record in records
        for value in [record.get("latency_ms")]
        if isinstance(value, int | float)
    ]
    return {
        "calls": len(records),
        "calls_without_usage": sum(1 for record in records if record.get("usage") is None),
        "total_prompt_tokens": _usage_field(records, "prompt_tokens"),
        "total_completion_tokens": _usage_field(records, "completion_tokens"),
        "total_tokens": _usage_field(records, "total_tokens"),
        "total_cached_prompt_tokens": _usage_detail_field(
            records, "prompt_tokens_details", "cached_tokens"
        ),
        "total_reasoning_tokens": _usage_detail_field(
            records, "completion_tokens_details", "reasoning_tokens"
        ),
        "avg_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else None,
        "max_latency_ms": round(max(latencies), 1) if latencies else None,
    }
