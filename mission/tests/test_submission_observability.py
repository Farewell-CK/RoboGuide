"""Submission observability preserves exact HTTP identity and the public Request contract."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import urllib.error
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast
from unittest.mock import Mock

import pytest
from mission.api import MissionRequestHttpServer
from mission.controller import HttpMissionController, MissionControllerError
from mission.models import JSONObject, MissionPlan
from mission.request_record import MissionRequestLifecycle
from mission.request_store import MissionRequestStore
from mission.submission_evidence import canonical_plan_digest
from test_requests import (
    FailingPlanner,
    FakeController,
    FakeInterpreter,
    FakePlanner,
    _assessment,
    _engine,
    _fixture_contracts,
    _inventory,
)


@contextmanager
def http_boundary(status: int = 202, malformed: bool = False) -> Iterator[tuple[str, list[bytes]]]:
    """Serve an offline HTTP boundary and retain bytes actually read from the socket."""
    captured: list[bytes] = []

    class Handler(BaseHTTPRequestHandler):
        """Implement only the Controller POST response under test."""

        def do_POST(self) -> None:
            """Read exact bytes and return accepted, rejected or malformed evidence."""
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            captured.append(raw)
            mission_id = json.loads(raw)["mission"]["id"]
            response = (
                b"not-json"
                if malformed
                else json.dumps(
                    {"mission_id": mission_id, "group_id": "test-group", "status": "Running"}
                ).encode()
            )
            self.send_response(status)
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)

        def log_message(self, format: str, *args: Any) -> None:
            """Suppress local test access logs."""

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", captured
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def fixture_plan() -> MissionPlan:
    """Read a real canonical plan without invoking a provider."""
    return MissionPlan.from_json(
        cast(
            JSONObject,
            json.loads(Path("scenarios/e1-shared-world-episode-51/mission-plan.json").read_text()),
        )
    )


@pytest.mark.parametrize("status", [202, 409, 503])
def test_submission_digest_uses_actual_http_body(status: int) -> None:
    """All Controller status classes retain exact sent bytes and response identity."""
    with http_boundary(status) as (endpoint, bodies):
        receipt = HttpMissionController(endpoint, 2).submit_plan(fixture_plan())
    evidence = receipt.evidence
    assert evidence is not None
    assert evidence.raw_request_body_sha256 == "sha256:" + hashlib.sha256(bodies[0]).hexdigest()
    assert evidence.submitted_plan_digest == canonical_plan_digest(json.loads(bodies[0]))
    assert evidence.controller_status_code == status
    assert evidence.controller_mission_id == fixture_plan().mission.mission_id
    assert evidence.controller_group_id == "test-group"
    assert receipt.accepted is (status == 202)


def test_invalid_controller_response_keeps_sent_identity() -> None:
    """A SUT response decoding failure does not erase the attempted HTTP body."""
    with http_boundary(malformed=True) as (endpoint, bodies):
        with pytest.raises(MissionControllerError) as error:
            HttpMissionController(endpoint, 2).submit_plan(fixture_plan())
    assert error.value.submission_evidence is not None
    assert error.value.submission_evidence.submitted_plan_digest == canonical_plan_digest(
        json.loads(bodies[0])
    )
    assert error.value.submission_evidence.controller_status_code == 202


def test_transport_failure_keeps_sent_identity() -> None:
    """Transport ambiguity remains recorded without claiming Controller acceptance."""
    controller = HttpMissionController("http://127.0.0.1:1", 1)
    controller._opener = Mock(open=Mock(side_effect=urllib.error.URLError("connection lost")))
    with pytest.raises(MissionControllerError) as error:
        controller.submit_plan(fixture_plan())
    observed = error.value.submission_evidence
    assert observed is not None
    assert observed.controller_status_code is None
    assert observed.transport_error == "URLError"


def test_observations_api_and_storage_do_not_extend_request_v04(tmp_path: Path) -> None:
    """A sidecar endpoint survives restart while the strict existing projection is unchanged."""
    planner = FakePlanner()
    controller = FakeController(_inventory(*_fixture_contracts()))
    engine = _engine(tmp_path, FakeInterpreter([_assessment()]), planner, controller)
    record = engine.create("test instruction")
    with http_boundary() as (endpoint, _):
        assert record.plan is not None
        receipt = HttpMissionController(endpoint, 2).submit_plan(record.plan)
    assert receipt.evidence is not None
    from dataclasses import replace

    record = replace(
        record, submission_evidence=replace(receipt.evidence, request_id=record.request_id)
    )
    store = MissionRequestStore(tmp_path / "requests.sqlite3")
    store.save(record)
    restored = store.get(record.request_id)
    assert restored == record
    schema = json.loads(
        Path("contracts/mission/request-v0.4/mission-request.schema.json").read_text()
    )
    assert set(record.to_json()) == set(schema["properties"])
    assert "submission_evidence" not in record.to_json()
    assert "failure_evidence" not in record.to_json()
    server = MissionRequestHttpServer(("127.0.0.1", 0), engine, 1024 * 1024)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/v1/mission-requests/{record.request_id}/observations"
        with urllib.request.urlopen(url, timeout=2) as response:  # noqa: S310
            observation = json.loads(response.read())
        assert observation == record.observations().to_json()
        assert observation["request_record_digest"] == canonical_plan_digest(record.to_json())
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    # Legacy rows retain their public shape and restore with unavailable observations.
    with sqlite3.connect(tmp_path / "requests.sqlite3") as connection:
        connection.execute(
            "UPDATE mission_requests SET document_json = ? WHERE request_id = ?",
            (json.dumps(record.to_json()), record.request_id),
        )
    legacy = store.get(record.request_id)
    assert legacy is not None
    assert legacy.to_json() == record.to_json()
    assert legacy.submission_evidence is None


def test_model_failure_before_plan_is_durable_observation(tmp_path: Path) -> None:
    """An actual Planner exception records the reached model stage without a fake plan."""
    engine = _engine(
        tmp_path, FakeInterpreter([_assessment()]), FailingPlanner(), FakeController(_inventory())
    )
    record = engine.create("test instruction")
    assert record.lifecycle is MissionRequestLifecycle.FAILED
    assert record.plan is None
    assert record.failure_evidence is not None
    assert record.failure_evidence["stage"] == "planner"
    assert record.failure_evidence["failure_owner"] == "MODEL"
    assert MissionRequestStore(tmp_path / "requests.sqlite3").get(record.request_id) == record
