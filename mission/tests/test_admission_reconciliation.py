"""Authority receipt reconciliation uses actual prepared bytes and never repeats dispatch."""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from mission.controller import HttpMissionController
from mission.recovery import FailureReason, RecoveryAction
from mission.requests import MissionRequestLifecycle
from test_requests import FakeInterpreter, FakePlanner, _assessment, _engine


@contextmanager
def admission_http(damage: str | None = None) -> Iterator[tuple[str, list[str]]]:
    """Admit a real HTTP body, lose its response, then expose a bounded authority receipt."""
    calls: list[str] = []
    proof: dict[str, Any] = {}

    class Handler(BaseHTTPRequestHandler):
        """Keep raw receipt evidence independent of the caller's local request record."""

        def do_POST(self) -> None:
            """Capture the exact bytes and return an invalid JSON acceptance response."""
            calls.append("POST")
            body = self.rfile.read(int(self.headers["Content-Length"]))
            proof.update(
                {
                    "schema_version": "roboguide.controller-mission-admission/v0.1",
                    "mission_id": json.loads(body)["mission"]["id"],
                    "group_id": "durable-group",
                    "accepted_request_body_sha256": "sha256:" + hashlib.sha256(body).hexdigest(),
                    "admitted_at_ms": 99,
                }
            )
            self._respond(202, b"invalid JSON")

        def do_GET(self) -> None:
            """Expose matched, mismatched, missing or transiently unavailable receipt evidence."""
            calls.append(f"GET {self.path}")
            payload = dict(proof)
            if damage == "digest":
                payload["accepted_request_body_sha256"] = "sha256:" + "0" * 64
            elif damage == "mission":
                payload["mission_id"] = "other-mission"
            elif damage == "schema":
                payload["schema_version"] = "other-schema"
            self._respond(
                404 if damage == "missing" else 503 if damage == "http" else 200,
                json.dumps(payload).encode(),
            )

        def _respond(self, status: int, body: bytes) -> None:
            """Write finite framed responses without retries or sensitive diagnostics."""
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            """Keep deterministic test output free of access noise."""

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@pytest.mark.parametrize("damage", [None, "digest", "mission", "schema", "missing", "http"])
def test_authoritative_receipt_is_bound_to_original_plan_bytes(
    tmp_path: Path, damage: str | None
) -> None:
    """Only the complete matching authority receipt can resume admission after response loss."""
    interpreter, planner = FakeInterpreter([_assessment()]), FakePlanner()
    with admission_http(damage) as (endpoint, calls):
        engine = _engine(tmp_path, interpreter, planner, HttpMissionController(endpoint, 2))
        failed = engine.create("deliver the declared payload")
        restored = engine.retry(failed.request_id)
    assert calls == ["POST", f"GET /v1/missions/{failed.mission_id}/admission"]
    assert restored.submission_evidence == failed.submission_evidence
    assert restored.failure_evidence == failed.failure_evidence
    assert len(planner.calls) == len(interpreter.calls) == 1
    assert restored.recovery_evidence is not None
    if damage is None:
        assert restored.lifecycle is MissionRequestLifecycle.ACCEPTED
        assert restored.recovery_evidence.reason is FailureReason.SUBMISSION_RECONCILED
        assert restored.admission_evidence is not None
        assert engine.get(failed.request_id) == restored
    else:
        assert restored.lifecycle is MissionRequestLifecycle.FAILED
        assert restored.recovery_evidence.action is RecoveryAction.RECONCILE_SUBMISSION
        assert restored.admission_evidence is None


def test_prepared_body_is_durable_before_transport_interruption(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hard interruption after preparation retains the original body fingerprint."""
    with admission_http() as (endpoint, calls):
        controller = HttpMissionController(endpoint, 2)

        def interrupted(*args: Any, **kwargs: Any) -> Any:
            """Simulate a hard process interruption after the before-send durable callback."""
            raise KeyboardInterrupt

        monkeypatch.setattr(controller._opener, "open", interrupted)
        engine = _engine(tmp_path, FakeInterpreter([_assessment()]), FakePlanner(), controller)
        with pytest.raises(KeyboardInterrupt):
            engine.create("deliver the declared payload")
        restored = _engine(tmp_path, FakeInterpreter([]), FakePlanner(), controller)
        record = restored.get("request-00000000000000000000000000000001")
        assert record.submission_evidence is not None
        assert record.submission_evidence.controller_status_code is None
        assert record.recovery_evidence is not None
        assert record.recovery_evidence.action is RecoveryAction.RECONCILE_SUBMISSION
        assert calls == []
