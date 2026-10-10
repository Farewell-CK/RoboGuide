"""Loopback-only byte-preservation, sanitization and explicit archive-loss regressions."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen

import pytest
from roboguide_eval.accounting import AccountingProxyConfig
from roboguide_eval.provider_capture import BodyCapture, CaptureProxyServer


class Upstream(BaseHTTPRequestHandler):
    """Receive the unmodified client bytes once and return a full original response."""

    seen: list[bytes] = []

    def log_message(self, format: str, *args: object) -> None:
        """Suppress the stub's transport noise without changing HTTP behavior."""

    def do_POST(self) -> None:
        """Echo original bytes as the stub response, preserving model and usage fields."""
        body = self.rfile.read(int(self.headers["Content-Length"]))
        self.seen.append(body)
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def test_capture_forwards_once_and_sanitizes_only_copies(tmp_path: Path) -> None:
    """Actual inputs/outputs remain byte-identical while archived secrets and headers disappear."""
    Upstream.seen = []
    upstream = ThreadingHTTPServer(("127.0.0.1", 0), Upstream)
    thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    thread.start()
    capture = BodyCapture(tmp_path / "bodies", ("private-value",))
    proxy = CaptureProxyServer(
        ("127.0.0.1", 0),
        AccountingProxyConfig(
            f"http://127.0.0.1:{upstream.server_port}",
            tmp_path / "accounting.jsonl",
            run_id="pair-emos",
        ),
        capture,
    )
    proxy_thread = threading.Thread(target=proxy.serve_forever, daemon=True)
    proxy_thread.start()
    body = json.dumps(
        {
            "model": "test-model",
            "input": "private-value",
            "authorization": "Bearer other",
            "tool": {"target_obj": "real-target"},
            "usage": {"total_tokens": 7},
        }
    ).encode()
    try:
        request = Request(
            f"http://127.0.0.1:{proxy.server_port}/v1/chat/completions",
            data=body,
            headers={"Authorization": "Bearer private-value"},
        )
        with urlopen(request, timeout=3) as response:
            assert response.read() == body
        assert Upstream.seen == [body]
        proxy.shutdown()
        assert proxy.wait_for_idle(2)
        proxy.server_close()
        status = capture.status(close=True)
        assert status["complete"] is True and status["calls_started"] == 1
        for name in ("request", "response"):
            saved = json.loads((capture.directory / f"call-00001-{name}.json").read_text())
            assert saved["input"] == "[REDACTED]" and saved["authorization"] == "[REDACTED]"
            assert saved["tool"]["target_obj"] == "real-target"
        accounting = json.loads((tmp_path / "accounting.jsonl").read_text())
        assert (
            accounting["response_model"] == "test-model"
            and accounting["usage"]["total_tokens"] == 7
        )
        assert b"private-value" not in b"".join(
            path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()
        )
    finally:
        proxy.server_close()
        upstream.shutdown()
        upstream.server_close()
        thread.join(timeout=2)
        proxy_thread.join(timeout=2)


@pytest.mark.parametrize("budget", ["calls", "body", "total", "write"])
def test_archive_loss_never_certifies_complete(tmp_path: Path, budget: str) -> None:
    """Exhausted budgets and disk failures expose incompleteness rather than silently truncating."""
    capture = BodyCapture(
        tmp_path / "bodies", (), max_calls=1, max_body_bytes=128, max_total_bytes=192
    )
    sequence = capture.begin("POST", "/responses?authorization=secret")
    if budget == "calls":
        capture.save(2, "request.json", b"{}")
    elif budget == "body":
        capture.save(sequence, "request.json", b"x" * 129)
    elif budget == "total":
        capture.save(sequence, "request.json", b"x" * 128)
        capture.save(sequence, "response.json", b"x" * 128)
    else:
        capture.directory.rename(tmp_path / "removed")
        capture.save(sequence, "request.json", b"{}")
    capture.complete_call()
    assert capture.status(close=True)["complete"] is False


def test_prefix_is_not_closed_and_reuse_is_rejected(tmp_path: Path) -> None:
    """Matching counts alone cannot prove that the collector has stopped accepting calls."""
    capture = BodyCapture(tmp_path / "bodies", ())
    capture.begin("POST", "/responses")
    capture.complete_call()
    assert capture.status()["complete"] is False
    assert capture.status(close=True)["complete"] is True
    with pytest.raises(FileExistsError):
        BodyCapture(tmp_path / "bodies", ())
