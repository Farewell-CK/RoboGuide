"""Deterministic tests for producer publication barriers and archive attribution."""

from __future__ import annotations

import json
import threading
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from roboguide_eval.b1_evidence_closure import collect_evidence_closure


def sealed() -> dict[str, Any]:
    """Return a producer observation with an exact retained execution identity."""
    return {
        "schema_version": "roboguide.shared-world-closure/v0.1",
        "mission_id": "mission",
        "execution_session_digest": "sha256:" + "a" * 64,
        "phase": "closed",
        "finished": True,
        "summary_published": True,
        "evidence_flushed": True,
        "error": None,
        "execution_error": "execution:Stage2ContractViolation",
    }


@pytest.mark.parametrize("pending_phase", ["finalizing", "closed"])
def test_live_http_waits_for_publication_and_preserves_sut_failure(
    tmp_path: Path, pending_phase: str
) -> None:
    """A terminal execution before publication is not sealed; a SUT error may seal normally."""
    seen: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        """Serve a real loopback status while the deterministic producer finishes."""

        def do_GET(self) -> None:
            """Return a pending first snapshot followed by actual publication."""
            seen.append(self.path)
            document = sealed()
            if len(seen) == 1:
                document.update(phase=pending_phase, evidence_flushed=False)
            body = json.dumps(document).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            """Keep offline conformance quiet."""

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        result = collect_evidence_closure(
            tmp_path, f"http://127.0.0.1:{server.server_port}", "mission"
        )
        assert result["complete"] is True
        assert len(seen) == 2
        assert result["observations"][-1]["execution_error"] is not None
    finally:
        server.shutdown()
        server.server_close()
        worker.join(2)


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"mission_id": "other"}, "producer_closure_identity_mismatch"),
        ({"execution_session_digest": None}, "producer_evidence_incomplete"),
        ({"error": "terminal_evidence_unavailable"}, "producer_evidence_incomplete"),
    ],
)
def test_wrong_identity_and_failed_writer_do_not_claim_closure(
    tmp_path: Path, changes: dict[str, Any], reason: str
) -> None:
    """A foreign run or failed archive is separate from original task execution failure."""
    document = {**sealed(), **changes}
    result = collect_evidence_closure(
        tmp_path, "http://unused", "mission", fetch=partial(_constant, document)
    )
    assert result["complete"] is False
    assert result["reason"] == reason
    assert result["failure_owner"] == "HARNESS_ARCHIVAL"
    assert result["observations"] == [document]


def _constant(value: Any, _url: str, _timeout: float) -> Any:
    """Return a fixed producer observation without mutable-loop capture."""
    return value


def test_empty_malformed_and_temporary_reads_exhaust_bounded_budget(tmp_path: Path) -> None:
    """Empty responses never prove the end of the producer's output."""
    clock = [0.0]

    def advance(seconds: float) -> None:
        """Advance the virtual observation deadline without a real wait."""
        clock[0] += seconds

    result = collect_evidence_closure(
        tmp_path,
        "http://unused",
        "mission",
        budget_seconds=1,
        fetch=lambda _url, _timeout: {},
        monotonic=lambda: clock[0],
        sleep=advance,
    )
    assert result["complete"] is False
    assert len(result["observations"]) == 2
    assert json.loads((tmp_path / "evidence-closure-archive.json").read_bytes()) == result


def test_closed_pending_keeps_original_budget_and_never_claims_sealed(tmp_path: Path) -> None:
    """Execution closed without a flushed archive remains bounded harness unavailability."""
    clock = [0.0]

    def advance(seconds: float) -> None:
        """Charge each wait against the one original observation budget."""
        clock[0] += seconds

    result = collect_evidence_closure(
        tmp_path,
        "http://unused",
        "mission",
        budget_seconds=1,
        fetch=partial(_constant, {**sealed(), "evidence_flushed": False}),
        monotonic=lambda: clock[0],
        sleep=advance,
    )
    assert not result["complete"]
    assert result["reason"] == "producer_closure_observation_budget_exhausted"
    assert len(result["observations"]) == 2
    assert clock[0] == 1


def test_pending_and_sealed_must_belong_to_same_execution_session(tmp_path: Path) -> None:
    """A different session cannot fill the observed producer's missing final archive."""
    documents = iter(
        [
            {**sealed(), "evidence_flushed": False},
            {**sealed(), "execution_session_digest": "sha256:" + "b" * 64},
        ]
    )
    result = collect_evidence_closure(
        tmp_path, "http://unused", "mission", fetch=lambda *_: next(documents), sleep=lambda _: None
    )
    assert not result["complete"]
    assert result["reason"] == "producer_closure_identity_mismatch"
    assert len(result["observations"]) == 2
