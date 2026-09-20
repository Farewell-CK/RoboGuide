"""Exercise live loopback HTTP pagination and unchanged B1 verification offline."""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import pytest
from b1_helpers import make_run, write_json
from roboguide_eval.b1_admission import FailureOwner
from roboguide_eval.b1_artifacts import collect_b1_artifacts
from roboguide_eval.b1_event_archive import (
    ARCHIVE_FILE,
    EventArchiveLimits,
    collect_controller_events,
)
from roboguide_eval.b1_provenance import observed_request
from roboguide_eval.b1_run import assess_b1_directory

LIMITS = EventArchiveLimits(tail_confirmation_seconds=0)
Reply = tuple[int, Any]


def event(sequence: int, payload: Any = None) -> dict[str, Any]:
    """Build the exact Controller envelope without requiring contiguous sequence ids."""
    return {
        "sequence": sequence,
        "event_id": f"event-{sequence}",
        "timestamp_ms": sequence,
        "correlation_id": "test",
        "causation_id": None,
        "payload_schema": "domain.EventPayload.json/v11",
        "payload": payload or {"NodeHeartbeatAccepted": {"node_id": "node"}},
    }


class ControllerStub:
    """Serve the actual HTTP shapes, with deterministic fault or concurrent-append hooks."""

    def __init__(self, run: Path, events: list[dict[str, Any]]) -> None:
        """Load already-produced offline MI evidence; never synthesize it in the collector."""
        self.run = run
        self.events = events
        self.pages: list[tuple[int, int]] = []
        self.page_hook: Callable[[int, int, int], Reply | None] = lambda *_: None
        self.mission_hook: Callable[[int], Any] | None = None
        self.mission_reads = 0
        self.truncate_pages = False

    def read(self, name: str) -> Any:
        """Return fixture evidence or absence without substituting a success document."""
        path = self.run / name
        return json.loads(path.read_text()) if path.exists() else None

    def respond(self, path: str) -> Reply:
        """Implement strict after/limit paging and the real observation endpoint paths."""
        url = urlsplit(path)
        if url.path == "/v1/events":
            query = parse_qs(url.query)
            after, limit = int(query["after"][0]), int(query["limit"][0])
            self.pages.append((after, limit))
            custom = self.page_hook(len(self.pages), after, limit)
            if custom is not None:
                return custom
            return 200, {"events": [e for e in self.events if e["sequence"] > after][:limit]}
        if "/v1/mission-requests/" in path:
            return 200, self.read(
                "b1-request-observations.json"
                if path.endswith("/observations")
                else "b1-request-record.json"
            )
        if "/v1/missions/" in path:
            self.mission_reads += 1
            return 200, self.mission_hook(self.mission_reads) if self.mission_hook else self.read(
                "mission.json"
            )
        if url.path == "/v1/execution-attempts":
            return 200, self.read("execution-attempts.json")
        return 404, {"error": "unknown route"}


@contextmanager
def live_http(stub: ControllerStub) -> Iterator[str]:
    """Run an actual socket server so urllib, URL cursors and byte limits are exercised."""

    class Handler(BaseHTTPRequestHandler):
        """Expose deterministic Controller responses without any provider or simulator."""

        def do_GET(self) -> None:
            """Serialize one response, including intentionally malformed raw bytes."""
            status, document = stub.respond(self.path)
            body = document if isinstance(document, bytes) else json.dumps(document).encode()
            self.send_response(status)
            missing = 10 if stub.truncate_pages and self.path.startswith("/v1/events?") else 0
            self.send_header("Content-Length", str(len(body) + missing))
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass  # The bounded client deliberately closes oversized responses.

        def log_message(self, format: str, *args: Any) -> None:
            """Keep HTTP access logging out of deterministic test output."""

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


def request(stub: ControllerStub) -> dict[str, Any]:
    """Join the public MI record with its existing digest-bound observations."""
    return observed_request(
        stub.read("b1-request-record.json"), stub.read("b1-request-observations.json")
    )


def archive(stub: ControllerStub, limits: EventArchiveLimits = LIMITS) -> dict[str, Any]:
    """Exercise only the event collector over HTTP, retaining the fixture SUT outcome."""
    with live_http(stub) as endpoint:
        return collect_controller_events(stub.run, endpoint, request(stub), limits)


def registered_events(run: Path, count: int = 396) -> list[dict[str, Any]]:
    """Place genuine fixture registration payloads beyond the default HTTP first page."""
    original = json.loads((run / "events.json").read_text())["events"]
    result = [event(i) for i in range(1, count + 1)]
    for sequence, source in zip((131, 132, 133), original, strict=True):
        result[sequence - 1] = event(sequence, source["payload"])
    return result


@pytest.mark.parametrize("count", [0, 7, 99, 100, 101, 396])
def test_page_boundaries_need_confirmed_tail(tmp_path: Path, count: int) -> None:
    """Counts never decide completeness; even short and exact pages require two empty reads."""
    run = make_run(tmp_path)
    stub = ControllerStub(run, [event(i) for i in range(1, count + 1)])
    status = archive(stub)
    assert status["state"] == "complete"
    assert status["event_count"] == count
    assert stub.pages[-2:] == [(count, 100), (count, 100)]
    assert json.loads((run / "events.json").read_text()) == {"events": stub.events}
    assert stub.mission_reads == 3


def test_sequence_gaps_use_actual_last_sequence(tmp_path: Path) -> None:
    """Legal append-sequence gaps must neither truncate pages nor invent missing events."""
    run = make_run(tmp_path)
    stub = ControllerStub(run, [event(i) for i in (2, 17, 500, 900)])
    status = archive(stub, replace(LIMITS, page_size=2))
    assert status["state"] == "complete"
    assert stub.pages == [(0, 2), (17, 2), (900, 2), (900, 2)]


@pytest.mark.parametrize(
    "bad",
    [
        {"events": [event(100)]},
        {"events": [event(102), event(101)]},
        {"events": [event(101), event(101)]},
        {"events": [dict(event(101), event_id="event-1")]},
        {"events": [dict(event(101), sequence=True)]},
        {"events": [{"sequence": 101}]},
        {"events": [event(0)]},
        {"events": "invalid"},
        {"events": [], "unexpected": True},
        {"events": [event(i) for i in range(101, 202)]},
        [],
        b"{broken-json",
        b"\xff",
    ],
)
def test_invalid_pages_preserve_raw_and_previous_archive(tmp_path: Path, bad: Any) -> None:
    """Reject duplicates, disorder, nonprogress and malformed payloads without repairing them."""
    run = make_run(tmp_path)
    previous = (run / "events.json").read_bytes()
    stub = ControllerStub(run, [event(i) for i in range(1, 102)])
    stub.page_hook = lambda page, *_: (200, bad) if page == 2 else None
    result = archive(stub)
    assert result["state"] == "incomplete"
    assert (run / "events.json").read_bytes() == previous
    directory = run / result["attempt_directory"]
    expected = bad if isinstance(bad, bytes) else json.dumps(bad).encode()
    assert (directory / "response-0003.body").read_bytes() == expected
    verdict = assess_b1_directory(run)
    assert not verdict["admission"]["provenance_valid"]
    assert verdict["context"]["event_archive_error"] == "controller_event_archive_incomplete"
    assert not verdict["admission"]["system_failure"]
    assert not (run / "run-failure.json").exists()


@pytest.mark.parametrize("failure_page", [2, 4])
def test_later_http_failure_is_not_sut_failure(tmp_path: Path, failure_page: int) -> None:
    """Preserve earlier responses and the failed-page body without inventing a Node failure."""
    run = make_run(tmp_path)
    stub = ControllerStub(run, registered_events(run))
    stub.page_hook = lambda page, *_: (
        (503, {"error": "temporarily unavailable"}) if page == failure_page else None
    )
    result = archive(stub)
    assert result["state"] == "incomplete" and result["reason"] == "http_failure"
    assert result["event_count"] == (failure_page - 1) * 100
    assert not assess_b1_directory(run)["admission"]["system_failure"]


def test_archive_failure_preserves_independent_actual_sut_failure(tmp_path: Path) -> None:
    """Missing event evidence invalidates archival, while an observed SUT crash keeps its owner."""
    run = make_run(tmp_path)
    stub = ControllerStub(run, registered_events(run))
    stub.page_hook = lambda page, *_: (503, {"error": "unavailable"}) if page == 2 else None
    with live_http(stub) as endpoint:
        result = collect_b1_artifacts(
            run,
            request_id=request(stub)["request_id"],
            mission_endpoint=endpoint,
            controller_endpoint=endpoint,
            owner=FailureOwner.SUT_SYSTEM,
            component="node",
            reason="node_process_exited",
        )
    assert result["admission"]["provenance_valid"] is False
    assert result["context"]["event_archive_error"] == "controller_event_archive_incomplete"
    assert result["admission"]["failure_owner"] == "SUT_SYSTEM"
    assert result["admission"]["system_failure"] is True
    assert stub.read("run-failure.json")["reason"] == "node_process_exited"


@pytest.mark.parametrize(
    ("limit", "reason"),
    [
        ({"max_pages": 1}, "page_budget_exhausted"),
        ({"max_page_bytes": 1000}, "response_size_limit"),
        ({"max_total_bytes": 1500}, "response_size_limit"),
        ({"budget_seconds": 1e-12}, "time_budget_exhausted"),
    ],
)
def test_acquisition_budgets_fail_closed(
    tmp_path: Path, limit: dict[str, Any], reason: str
) -> None:
    """Bytes, page count and monotonic time all bound acquisition even on a responsive server."""
    run = make_run(tmp_path)
    stub = ControllerStub(run, registered_events(run))
    result = archive(stub, replace(LIMITS, **limit))
    assert result["state"] == "incomplete" and result["reason"] == reason
    assert assess_b1_directory(run)["admission"]["provenance_valid"] is False
    bodies = list((run / result["attempt_directory"]).glob("*.body"))
    if "max_page_bytes" in limit:
        assert max(p.stat().st_size for p in bodies) == limit["max_page_bytes"] + 1
    if "max_total_bytes" in limit:
        assert sum(p.stat().st_size for p in bodies) == limit["max_total_bytes"] + 1


def test_truncated_http_body_is_preserved_without_publication(tmp_path: Path) -> None:
    """Even parseable JSON cannot pass when the HTTP body is shorter than its declaration."""
    run = make_run(tmp_path)
    stub = ControllerStub(run, registered_events(run))
    stub.truncate_pages = True
    previous = (run / "events.json").read_bytes()
    result = archive(stub)
    assert result["state"] == "incomplete" and result["reason"] == "response_truncated"
    assert (run / "events.json").read_bytes() == previous
    body = run / result["attempt_directory"] / "response-0002.body"
    assert len(json.loads(body.read_bytes())["events"]) == 100


def test_transient_empty_and_concurrent_appends_resume_paging(tmp_path: Path) -> None:
    """One empty response never freezes the tail; subsequent writes reset confirmation."""
    run = make_run(tmp_path)
    stub = ControllerStub(run, [event(i) for i in range(1, 151)])
    stub.page_hook = lambda page, *_: (200, {"events": []}) if page == 2 else None
    result = archive(stub)
    assert result["state"] == "complete" and result["event_count"] == 150
    assert stub.pages == [(0, 100), (100, 100), (100, 100), (150, 100), (150, 100)]


def test_continuous_writer_exhausts_budget(tmp_path: Path) -> None:
    """A tail that never stabilizes remains incomplete instead of truncating at a count."""
    run = make_run(tmp_path)
    stub = ControllerStub(run, [])
    stub.page_hook = lambda _, after, limit: (200, {"events": [event(after + 1)]})
    result = archive(stub, replace(LIMITS, max_pages=5))
    assert result["state"] == "incomplete" and result["event_count"] == 5


@pytest.mark.parametrize("changed", ["Running", "Cancelled", "foreign"])
def test_terminal_view_must_remain_scoped_and_stable(tmp_path: Path, changed: str) -> None:
    """A changing lifecycle or Controller identity cannot establish a terminal archive."""
    run = make_run(tmp_path)
    stub = ControllerStub(run, registered_events(run))
    original = stub.read("mission.json")
    replacement = (
        {**original, "mission_id": "foreign"}
        if changed == "foreign"
        else {**original, "status": changed}
    )
    stub.mission_hook = lambda call: original if call < 3 else replacement
    result = archive(stub)
    assert result["state"] == "incomplete"


@pytest.mark.parametrize("bad", [None, [], {"status": "Failed"}, {"status": []}])
def test_malformed_terminal_view_is_explicitly_incomplete(tmp_path: Path, bad: Any) -> None:
    """Malformed or unscoped lifecycle evidence never substitutes for a terminal snapshot."""
    run = make_run(tmp_path)
    stub = ControllerStub(run, [])
    stub.mission_hook = lambda _: (
        {**stub.read("mission.json"), **bad} if isinstance(bad, dict) else bad
    )
    if bad == {"status": "Failed"}:
        stub.mission_hook = lambda _: bad
    result = archive(stub)
    assert result["state"] == "incomplete"
    assert result["reason"] == "terminal_mission_unconfirmed"


def test_running_mission_cannot_publish_an_empty_final_archive(tmp_path: Path) -> None:
    """An observation timeout must remain incomplete even when the Controller is reachable."""
    run = make_run(tmp_path)
    stub = ControllerStub(run, [])
    stub.mission_hook = lambda _: {**stub.read("mission.json"), "status": "Running"}
    result = archive(stub)
    assert result["state"] == "incomplete"
    assert not stub.pages


@pytest.mark.parametrize("mission_status", ["Completed", "Failed", "Cancelled"])
def test_full_collector_preserves_registration_and_real_outcome(
    tmp_path: Path, mission_status: str
) -> None:
    """396 events include both registrations past page one and preserve benchmark false."""
    run = make_run(tmp_path, case="B")
    stub = ControllerStub(run, registered_events(run))
    write_json(run / "mission.json", {**stub.read("mission.json"), "status": mission_status})
    with live_http(stub) as endpoint:
        verdict = collect_b1_artifacts(
            run,
            request_id=request(stub)["request_id"],
            mission_endpoint=endpoint,
            controller_endpoint=endpoint,
        )
    assert verdict["admission"]["provenance_valid"] is True
    assert verdict["admission"]["valid_for_formal_population"] is True
    assert verdict["admission"]["valid_for_benchmark_population"] is True
    assert verdict["admission"]["benchmark_outcome"] == "BENCHMARK_FALSE"
    assert verdict["admission"]["system_failure"] is (mission_status == "Failed")
    assert len(verdict["context"]["controller_receipt"]["registered_task_ids"]) == 2


def test_terminal_collection_refreshes_preceding_running_observation(tmp_path: Path) -> None:
    """Use the final bracketed Mission view for failure attribution, not a stale initial read."""
    run = make_run(tmp_path)
    stub = ControllerStub(run, registered_events(run))
    original = stub.read("mission.json")
    stub.mission_hook = lambda call: {**original, "status": "Running" if call == 1 else "Failed"}
    with live_http(stub) as endpoint:
        result = collect_b1_artifacts(
            run,
            request_id=request(stub)["request_id"],
            mission_endpoint=endpoint,
            controller_endpoint=endpoint,
        )
    assert result["admission"]["provenance_valid"] is True
    assert result["admission"]["failure_owner"] == "SUT_SYSTEM"
    assert stub.read("mission.json")["status"] == "Failed"


def test_missing_real_registration_still_fails(tmp_path: Path) -> None:
    """Complete acquisition does not fabricate a missing domain event to rescue provenance."""
    run = make_run(tmp_path)
    events = registered_events(run)
    events = [e for e in events if e["sequence"] != 133]
    stub = ControllerStub(run, events)
    assert archive(stub)["state"] == "complete"
    result = assess_b1_directory(run)
    assert "controller_task_registration_mismatch" in result["context"]["provenance_failures"]


def test_early_mi_failure_does_not_require_controller_events(tmp_path: Path) -> None:
    """A genuine pre-submission model failure remains Formal without a fabricated empty log."""
    run = make_run(tmp_path, case="D")
    stub = ControllerStub(run, [])
    with live_http(stub) as endpoint:
        result = collect_b1_artifacts(
            run,
            request_id=request(stub)["request_id"],
            mission_endpoint=endpoint,
            controller_endpoint=endpoint,
        )
    assert not stub.pages
    assert not (run / "events.json").exists()
    assert result["admission"]["valid_for_formal_population"] is True
    assert result["admission"]["failure_owner"] == "MODEL"
    assert json.loads((run / ARCHIVE_FILE).read_text())["state"] == "not_required"


def test_stale_complete_archive_cannot_hide_failed_recollection(tmp_path: Path) -> None:
    """A failed retry preserves the former file but its incomplete status prevents reuse."""
    run = make_run(tmp_path)
    stub = ControllerStub(run, registered_events(run))
    assert archive(stub)["state"] == "complete"
    original = (run / "events.json").read_bytes()
    stub.page_hook = lambda *_: (500, {"error": "unavailable"})
    assert archive(stub)["state"] == "incomplete"
    assert (run / "events.json").read_bytes() == original
    assert len(list((run / "controller-event-pages").glob("attempt-*"))) == 2
    assert assess_b1_directory(run)["admission"]["provenance_valid"] is False


def test_controller_startup_failure_preserves_existing_admission(tmp_path: Path) -> None:
    """No MI submission needs no event snapshot; retain the actual startup failure owner."""
    run = make_run(tmp_path)
    for name in (
        "b1-request-record.json",
        "b1-request-observations.json",
        "mission.json",
        "events.json",
        "execution-attempts.json",
        "evidence/shared-world-summary.json",
    ):
        (run / name).unlink(missing_ok=True)
    stub = ControllerStub(run, [])
    with live_http(stub) as endpoint:
        result = collect_b1_artifacts(
            run,
            request_id="",
            mission_endpoint=endpoint,
            controller_endpoint=endpoint,
            owner=FailureOwner.SUT_SYSTEM,
            component="controller",
            reason="startup_failed",
        )
    assert not stub.pages
    assert result["admission"]["valid_for_formal_population"] is True
    assert result["admission"]["failure_owner"] == "SUT_SYSTEM"
    assert result["admission"]["valid_for_benchmark_population"] is False
    assert json.loads((run / ARCHIVE_FILE).read_text())["state"] == "not_required"


@pytest.mark.parametrize("mutation", ["digest", "missing", "malformed", "scope"])
def test_archive_status_cannot_be_bypassed(tmp_path: Path, mutation: str) -> None:
    """A partial or edited acquisition cannot reuse an otherwise valid old events file."""
    run = make_run(tmp_path)
    stub = ControllerStub(run, registered_events(run))
    status = archive(stub)
    assert status["state"] == "complete"
    if mutation == "digest":
        write_json(run / "events.json", {"events": stub.events[:-1]})
    elif mutation == "missing":
        (run / ARCHIVE_FILE).unlink()
    elif mutation == "malformed":
        (run / ARCHIVE_FILE).write_text("{broken-json")
    else:
        write_json(run / ARCHIVE_FILE, {**status, "scope": "global_snapshot"})
    result = assess_b1_directory(run)
    assert result["admission"]["provenance_valid"] is False
    assert result["context"]["event_archive_error"] == "controller_event_archive_invalid"
