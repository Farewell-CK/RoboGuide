"""Offline HTTP regressions for identity evidence and fail-soft accounting."""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast
from urllib.request import Request, urlopen

import pytest
from roboguide_eval.accounting import (
    AccountingProxyConfig,
    AccountingProxyServer,
    read_accounting_log,
)


class FakeProvider(BaseHTTPRequestHandler):
    """Return configured raw response bytes while recording the forwarded request."""

    response_bytes = b"{}"
    received: list[tuple[str, bytes, str | None]] = []
    request_received: threading.Event | None = None
    release_response: threading.Event | None = None

    def log_message(self, format: str, *args: object) -> None:
        """Keep local test HTTP traffic out of diagnostic output."""

    def do_POST(self) -> None:  # noqa: N802 - standard HTTP handler interface
        """Record exact path/body/auth and return the configured Provider response."""
        body = self.rfile.read(int(self.headers["Content-Length"]))
        type(self).received.append((self.path, body, self.headers.get("Authorization")))
        if self.request_received is not None:
            self.request_received.set()
        if self.release_response is not None:
            assert self.release_response.wait(timeout=10)
        self.send_response(200)
        self.send_header("Content-Length", str(len(self.response_bytes)))
        self.end_headers()
        self.wfile.write(self.response_bytes)


@contextmanager
def proxy_pair(
    tmp_path: Path, response: bytes, **budgets: Any
) -> Iterator[tuple[AccountingProxyServer, int]]:
    """Own two ephemeral loopback servers and always close their sockets and threads."""

    class Provider(FakeProvider):
        """Isolate each test's response and request evidence."""

        response_bytes = response
        received: list[tuple[str, bytes, str | None]] = []

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    proxy = AccountingProxyServer(
        ("127.0.0.1", 0),
        AccountingProxyConfig(
            f"http://127.0.0.1:{upstream.server_address[1]}",
            tmp_path / "calls.ndjson",
            run_id="run-test",
            **budgets,
        ),
    )
    threads = [
        threading.Thread(target=server.serve_forever, daemon=True) for server in (upstream, proxy)
    ]
    for thread in threads:
        thread.start()
    try:
        yield proxy, proxy.server_address[1]
    finally:
        for server in (proxy, upstream):
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=5)


def post(port: int, document: dict[str, object], path: str = "/responses") -> bytes:
    """Send one request with synthetic credentials; return the unmodified response."""
    request = Request(
        f"http://127.0.0.1:{port}{path}",
        data=json.dumps(document).encode(),
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer synthetic-secret",
        },
    )
    with urlopen(request, timeout=10) as response:
        return cast(bytes, response.read())


def test_responses_model_identity_is_observed_not_inferred(tmp_path: Path) -> None:
    """Record returned identity and actual parameters without logging sensitive inputs."""
    response = (
        b'{"model":"returned-version","id":"resp_123","usage":'
        b'{"input_tokens":3,"output_tokens":2,"total_tokens":5,'
        b'"authorization":"synthetic-secret"},"output":"synthetic-secret"}'
    )
    with proxy_pair(tmp_path, response) as (proxy, port):
        assert (
            post(
                port,
                {
                    "model": "requested-alias",
                    "reasoning": {"effort": "xhigh"},
                    "max_output_tokens": 4096,
                    "store": False,
                    "input": "synthetic-secret",
                },
            )
            == response
        )
        row = read_accounting_log(proxy.config.log_path)[0]
        assert row["run_id"] == "run-test"
        assert row["requested_model"] == "requested-alias"
        assert row["response_model"] == "returned-version"
        assert row["response_id"] == "resp_123"
        assert row["usage"] == {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5}
        assert row["request_parameters"] == {
            "reasoning_effort": "xhigh",
            "max_output_tokens": 4096,
            "store": False,
        }
        assert "synthetic-secret" not in proxy.config.log_path.read_text()


@pytest.mark.parametrize(
    "response", [b"{}", b'{"model":null}', b'{"model":"sk-secret"}', b"not-json", b"\xff"]
)
def test_missing_or_invalid_response_identity_stays_unavailable(
    tmp_path: Path, response: bytes
) -> None:
    """Never fill a missing returned model with the requested model or an unsafe string."""
    with proxy_pair(tmp_path, response) as (proxy, port):
        assert post(port, {"model": "requested-model"}) == response
        row = read_accounting_log(proxy.config.log_path)[0]
        assert row["requested_model"] == "requested-model"
        assert row["response_model"] is None
        assert row["model_observation_status"] == "unavailable"
        assert "sk-secret" not in proxy.config.log_path.read_text()


def test_log_write_failure_does_not_change_provider_response(tmp_path: Path) -> None:
    """A failed observer write preserves SUT output and invalidates completeness explicitly."""
    with proxy_pair(tmp_path, b'{"model":"actual"}') as (proxy, port):
        proxy.config.log_path.mkdir()
        assert post(port, {"model": "m"}) == b'{"model":"actual"}'
        status = json.loads((tmp_path / "calls.ndjson.status.json").read_text())
        assert status["complete"] is False
        assert status["records_dropped"] == status["write_failures"] == 1


def test_budget_exhaustion_preserves_provider_calls_but_marks_evidence_incomplete(
    tmp_path: Path,
) -> None:
    """A bounded log cannot turn omitted calls into complete model identity evidence."""
    with proxy_pair(tmp_path, b'{"model":"actual"}', max_records=1) as (proxy, port):
        for _ in range(2):
            assert post(port, {"model": "m"}) == b'{"model":"actual"}'
        assert len(read_accounting_log(proxy.config.log_path)) == 1
        assert proxy.recording_status()["records_dropped"] == 1
        assert proxy.recording_status()["complete"] is False


def test_only_graceful_proxy_closure_can_mark_observation_complete(tmp_path: Path) -> None:
    """A prefix from a still-running proxy cannot prove that all calls were archived."""
    with proxy_pair(tmp_path, b'{"model":"actual"}') as (proxy, port):
        post(port, {"model": "m"})
        assert proxy.recording_status()["closed"] is False
        assert proxy.recording_status()["complete"] is False
    status = json.loads((tmp_path / "calls.ndjson.status.json").read_text())
    assert status["closed"] is True
    assert status["complete"] is True
    assert status["records_written"] == 1


def test_run_bound_log_cannot_append_another_run(tmp_path: Path) -> None:
    """Prevent sequence and run identity reuse from contaminating future pair evidence."""
    log_path = tmp_path / "previous.ndjson"
    log_path.write_text('{"run_id":"previous"}\n')
    with pytest.raises(ValueError, match="unused log"):
        AccountingProxyServer(
            ("127.0.0.1", 0), AccountingProxyConfig("http://localhost", log_path, run_id="new")
        )


def test_proxy_shutdown_during_call_cannot_certify_a_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An actual in-flight upstream call fences completeness until its response is recorded."""
    received, release = threading.Event(), threading.Event()
    monkeypatch.setattr(FakeProvider, "request_received", received)
    monkeypatch.setattr(FakeProvider, "release_response", release)
    with proxy_pair(tmp_path, b'{"model":"actual"}') as (proxy, port):
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(post, port, {"model": "m"})
            try:
                assert received.wait(timeout=5)
                proxy.shutdown()
                proxy.server_close()
                assert proxy.recording_status()["closed"] is True
                assert proxy.recording_status()["in_flight"] == 1
                assert proxy.recording_status()["complete"] is False
            finally:
                release.set()
            assert pending.result(timeout=5) == b'{"model":"actual"}'
    status = json.loads((tmp_path / "calls.ndjson.status.json").read_text())
    assert status["in_flight"] == 0 and status["complete"] is True


def test_deeply_nested_metadata_cannot_change_provider_output(tmp_path: Path) -> None:
    """A parser recursion limit degrades observation, rather than causing an SDK retry."""
    response = b"[" * 2000 + b"0" + b"]" * 2000
    with proxy_pair(tmp_path, response) as (proxy, port):
        assert post(port, {"model": "m"}) == response
        assert (
            read_accounting_log(proxy.config.log_path)[0]["model_observation_status"]
            == "unavailable"
        )


def test_metadata_size_budget_preserves_large_provider_response(tmp_path: Path) -> None:
    """Large responses remain byte-exact while bounded metadata parsing reports unavailable."""
    response = b'{"model":"actual","padding":"' + b"x" * (4 * 1024 * 1024) + b'"}'
    with proxy_pair(tmp_path, response) as (proxy, port):
        assert post(port, {"model": "m"}) == response
        assert read_accounting_log(proxy.config.log_path)[0]["response_model"] is None


def test_failed_writes_also_consume_the_record_budget(tmp_path: Path) -> None:
    """Repeated storage failures cannot grow unbounded partial log data."""
    with proxy_pair(tmp_path, b'{"model":"actual"}', max_records=1) as (proxy, port):
        proxy.config.log_path.mkdir()
        for _ in range(2):
            assert post(port, {"model": "m"}) == b'{"model":"actual"}'
        status = proxy.recording_status()
        assert status["write_failures"] == 1
        assert status["records_dropped"] == 2
        assert status["complete"] is False


def test_occupied_port_preserves_startup_error(tmp_path: Path) -> None:
    """A bind failure cannot be masked by observer cleanup before HTTP initialization."""
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), FakeProvider)
    try:
        with pytest.raises(OSError):
            AccountingProxyServer(
                ("127.0.0.1", upstream.server_address[1]),
                AccountingProxyConfig("http://localhost", tmp_path / "calls.ndjson"),
            )
    finally:
        upstream.server_close()


def test_query_and_arbitrary_provider_fields_are_not_logged(tmp_path: Path) -> None:
    """Queries, authorization and non-usage response content never enter the observation log."""
    with proxy_pair(tmp_path, b'{"model":"actual","secret":"synthetic-secret"}') as (proxy, port):
        post(port, {"model": "m"}, "/v1/chat/completions?key=synthetic-secret")
        row = read_accounting_log(proxy.config.log_path)[0]
        assert row["path"] == "/v1/chat/completions"
        assert "synthetic-secret" not in proxy.config.log_path.read_text()


@pytest.mark.parametrize(
    "url",
    [
        "http://user:secret@localhost",
        "http://localhost?key=secret",
        "http://localhost#secret",
        "file:///secret",
    ],
)
def test_credential_bearing_upstream_urls_are_rejected(tmp_path: Path, url: str) -> None:
    """Require credentials in transient headers, never in a printable upstream URL."""
    with pytest.raises(ValueError, match="without credentials"):
        AccountingProxyConfig(url, tmp_path / "calls.ndjson")
