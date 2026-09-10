"""Offline integration tests for the local LLM accounting proxy.

Each test starts a fake upstream server (canned OpenAI-style responses) and
the real proxy server on ephemeral ports, then drives requests through the
proxy and asserts forwarding fidelity plus accounting records. No network
beyond localhost is touched.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from roboguide_eval.accounting import (
    AccountingProxyConfig,
    AccountingProxyServer,
    read_accounting_log,
    summarize_accounting,
)

CANNED_USAGE: dict[str, object] = {
    "prompt_tokens": 100,
    "completion_tokens": 20,
    "total_tokens": 120,
    "prompt_tokens_details": {"cached_tokens": 64},
    "completion_tokens_details": {"reasoning_tokens": 5},
}
CANNED_RESPONSE: dict[str, object] = {"id": "chatcmpl-1", "usage": CANNED_USAGE}

type FakeHandlerFactory = Callable[[type[BaseHTTPRequestHandler]], ThreadingHTTPServer]


class _RecordingUpstream(BaseHTTPRequestHandler):
    """Fake upstream returning canned responses and recording what it got."""

    server: ThreadingHTTPServer

    def log_message(self, format_string: str, *args: object) -> None:
        """Silence per-request stderr chatter.

        Args:
            format_string: The printf-style format string.
            *args: Format arguments.
        """

    def do_POST(self) -> None:  # noqa: N802 - http.server API naming
        """Return the canned completion response and store the request.

        State changes:
            Appends the received authorization header and body to the
            server's ``requests`` list.
        """
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        self.server.requests.append(  # type: ignore[attr-defined]
            {"authorization": self.headers.get("Authorization"), "body": body}
        )
        payload = json.dumps(CANNED_RESPONSE).encode(encoding="utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


class _FailingUpstream(BaseHTTPRequestHandler):
    """Fake upstream that always answers 500."""

    def log_message(self, format_string: str, *args: object) -> None:
        """Silence per-request stderr chatter.

        Args:
            format_string: The printf-style format string.
            *args: Format arguments.
        """

    def do_POST(self) -> None:  # noqa: N802 - http.server API naming
        """Return a server error, as a real overloaded provider would.

        State changes:
            None beyond the HTTP response itself.
        """
        payload = b'{"error": "upstream overloaded"}'
        self.send_response(500)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def _start_server(handler: type[BaseHTTPRequestHandler]) -> ThreadingHTTPServer:
    """Start a threaded server on an ephemeral localhost port.

    Args:
        handler: The request handler class for the server.

    Returns:
        The started server (already serving in a daemon thread) with a
        ``requests`` list attached for handlers that record what they
        receive.
    """
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.requests = []  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


@pytest.fixture
def proxy_factory(tmp_path: Path) -> Callable[[str], tuple[AccountingProxyServer, int]]:
    """Provide a factory starting accounting proxies on ephemeral ports.

    Args:
        tmp_path: Per-test temporary directory.

    Returns:
        A factory taking the upstream base URL and returning the started
        proxy server plus its bound port.
    """

    def _factory(upstream_base_url: str) -> tuple[AccountingProxyServer, int]:
        """Start one accounting proxy with a fresh log file.

        Args:
            upstream_base_url: The real endpoint requests are forwarded to.

        Returns:
            The proxy server and the bound port.
        """
        config = AccountingProxyConfig(
            upstream_base_url=upstream_base_url,
            log_path=tmp_path / f"accounting-{id(threading.current_thread())}.ndjson",
        )
        server = AccountingProxyServer(("127.0.0.1", 0), config)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server, server.server_address[1]

    return _factory


def _post(
    port: int,
    payload: dict[str, object],
    *,
    authorization: str = "Bearer test-key",
) -> tuple[int, bytes]:
    """POST one chat-completions style request through the proxy.

    Args:
        port: The proxy port.
        payload: The JSON request payload.
        authorization: The credential header value to send.

    Returns:
        The response status and body bytes.
    """
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/v1/chat/completions",
        data=json.dumps(payload).encode(encoding="utf-8"),
        headers={"Content-Type": "application/json", "Authorization": authorization},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.status, response.read()


def test_proxy_forwards_body_and_captures_full_usage(
    proxy_factory: Callable[[str], tuple[AccountingProxyServer, int]],
) -> None:
    """Requests reach the upstream unchanged; usage breakdown is recorded."""
    upstream = _start_server(_RecordingUpstream)
    proxy, port = proxy_factory(f"http://127.0.0.1:{upstream.server_address[1]}")
    try:
        payload: dict[str, object] = {
            "model": "gpt-5.6-luna",
            "messages": [
                {"role": "system", "content": "system prompt"},
                {"role": "user", "content": "plan the subtask"},
            ],
        }
        status, body = _post(port, payload)
        assert status == 200
        assert json.loads(body) == CANNED_RESPONSE
        records = read_accounting_log(proxy.config.log_path)
        assert len(records) == 1
        record = records[0]
        assert record["status"] == 200
        assert record["usage"] == CANNED_USAGE
        assert record["prompt_messages"] == 2
        assert record["stream"] is False
        assert record["error"] is None
        assert isinstance(record["latency_ms"], float) and record["latency_ms"] >= 0.0
        # Credentials pass through to the upstream but never reach the log.
        upstream_requests: list[dict[str, object]] = upstream.requests  # type: ignore[attr-defined]
        assert upstream_requests[0]["authorization"] == "Bearer test-key"
        assert "test-key" not in json.dumps(records)
    finally:
        upstream.shutdown()
        proxy.shutdown()


def test_proxy_upstream_error_is_forwarded_and_recorded(
    proxy_factory: Callable[[str], tuple[AccountingProxyServer, int]],
) -> None:
    """Non-2xx upstream answers are forwarded and logged without usage."""
    upstream = _start_server(_FailingUpstream)
    proxy, port = proxy_factory(f"http://127.0.0.1:{upstream.server_address[1]}")
    try:
        with pytest.raises(urllib.error.HTTPError) as error_info:
            _post(port, {"model": "m", "messages": []})
        assert error_info.value.code == 500
        records = read_accounting_log(proxy.config.log_path)
        assert len(records) == 1
        assert records[0]["status"] == 500
        assert records[0]["usage"] is None
        summary = summarize_accounting(records)
        assert summary["calls"] == 1
        assert summary["calls_without_usage"] == 1
        assert summary["total_tokens"] == 0
    finally:
        upstream.shutdown()
        proxy.shutdown()


def test_proxy_records_transport_failures(
    proxy_factory: Callable[[str], tuple[AccountingProxyServer, int]],
) -> None:
    """An unreachable upstream yields a 502 and an error record."""
    # Port 1 on localhost is reserved and effectively always closed.
    proxy, port = proxy_factory("http://127.0.0.1:1")
    try:
        with pytest.raises(urllib.error.HTTPError) as error_info:
            _post(port, {"model": "m", "messages": []})
        assert error_info.value.code == 502
        records = read_accounting_log(proxy.config.log_path)
        assert len(records) == 1
        assert records[0]["status"] is None
        assert "upstream transport failure" in str(records[0]["error"])
    finally:
        proxy.shutdown()


def test_summarize_aggregates_token_breakdown(
    proxy_factory: Callable[[str], tuple[AccountingProxyServer, int]],
) -> None:
    """Totals include prompt/completion/cached/reasoning components."""
    upstream = _start_server(_RecordingUpstream)
    proxy, port = proxy_factory(f"http://127.0.0.1:{upstream.server_address[1]}")
    try:
        _post(port, {"model": "m", "messages": [{"role": "user", "content": "a"}]})
        _post(port, {"model": "m", "messages": [{"role": "user", "content": "b"}]})
        records = read_accounting_log(proxy.config.log_path)
        summary = summarize_accounting(records)
        assert summary["calls"] == 2
        assert summary["calls_without_usage"] == 0
        assert summary["total_prompt_tokens"] == 200
        assert summary["total_completion_tokens"] == 40
        assert summary["total_tokens"] == 240
        assert summary["total_cached_prompt_tokens"] == 128
        assert summary["total_reasoning_tokens"] == 10
    finally:
        upstream.shutdown()
        proxy.shutdown()


def test_proxy_handles_concurrent_calls_with_unique_sequence(
    proxy_factory: Callable[[str], tuple[AccountingProxyServer, int]],
) -> None:
    """Concurrent clients each get exactly one correctly sequenced record."""
    upstream = _start_server(_RecordingUpstream)
    proxy, port = proxy_factory(f"http://127.0.0.1:{upstream.server_address[1]}")
    try:
        errors: list[Exception] = []

        def call(index: int) -> None:
            """Send one proxied request from a worker thread.

            Args:
                index: Worker index used to vary the prompt content.
            """
            try:
                status, _ = _post(
                    port, {"model": "m", "messages": [{"role": "user", "content": str(index)}]}
                )
                assert status == 200
            except Exception as error:  # noqa: BLE001 - record for the assertion
                errors.append(error)

        threads = [threading.Thread(target=call, args=(index,)) for index in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        assert errors == []
        records = read_accounting_log(proxy.config.log_path)
        assert len(records) == 6
        assert sorted(int(str(record["seq"])) for record in records) == [1, 2, 3, 4, 5, 6]
    finally:
        upstream.shutdown()
        proxy.shutdown()
