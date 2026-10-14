"""Public HTTP recovery and atomic journal tests; no model, robot or simulator is started."""

from __future__ import annotations

import json
import sqlite3
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import cast

import pytest
from mission.api import MissionRequestHttpServer
from mission.models import JSONObject
from mission.request_store import HttpCommandIdentity, MissionRequestStore
from mission.requests import MissionRequestError, MissionRequestRecord
from test_requests import (
    FakeController,
    FakeInterpreter,
    FakePlanner,
    _assessment,
    _engine,
    _fixture_contracts,
    _inventory,
)


def _post(
    endpoint: str, path: str, body: JSONObject, key: str | None = None
) -> tuple[int, JSONObject]:
    """Issue one real HTTP request and retain successful and rejected response bodies."""
    request = urllib.request.Request(
        endpoint + path,
        data=json.dumps(body).encode(),
        method="POST",
        headers={"Idempotency-Key": key} if key is not None else {},
    )
    try:
        with urllib.request.urlopen(request, timeout=3) as response:  # noqa: S310
            return response.status, cast(JSONObject, json.loads(response.read()))
    except urllib.error.HTTPError as error:
        return error.code, cast(JSONObject, json.loads(error.read()))


def test_lost_response_is_observed_reconciled_and_replayed_after_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Recover an actual committed Request over HTTP without invoking its original create again."""
    interpreter = FakeInterpreter([_assessment()])
    engine = _engine(
        tmp_path, interpreter, FakePlanner(), FakeController(_inventory(*_fixture_contracts()))
    )
    create = engine.create
    calls = 0

    def lose_response(instruction: str) -> MissionRequestRecord:
        """Drop only the response after the maintained engine has committed its outcome."""
        nonlocal calls
        calls += 1
        create(instruction)
        raise RuntimeError("injected HTTP result loss")

    monkeypatch.setattr(engine, "create", lose_response)
    original: JSONObject = {"instruction": "execute once"}
    lookup: JSONObject = {
        "method": "POST",
        "path": "/v1/mission-requests",
        "idempotency_key": "lost-response",
    }
    server = MissionRequestHttpServer(("127.0.0.1", 0), engine, 1024 * 1024)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        endpoint = f"http://127.0.0.1:{server.server_port}"
        assert _post(endpoint, "/v1/mission-requests", original, "lost-response")[0] == 500
        status, observed = _post(endpoint, "/v1/http-commands/observe", lookup)
        assert status == 200
        assert observed["status"] == "pending" and observed["executing"] is False
        progress = cast(JSONObject, observed["progress"])
        assert observed["request_id"] == progress["request_id"]
        assert progress["lifecycle"] == "Accepted"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)

    restarted = _engine(
        tmp_path,
        FakeInterpreter([]),
        FakePlanner(),
        FakeController(_inventory(*_fixture_contracts())),
    )
    server = MissionRequestHttpServer(("127.0.0.1", 0), restarted, 1024 * 1024)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        endpoint = f"http://127.0.0.1:{server.server_port}"
        status, reconciled = _post(endpoint, "/v1/http-commands/reconcile", lookup)
        assert status == 200 and reconciled["status"] == "complete"
        expected = cast(JSONObject, reconciled["response"])
        assert expected["execution_repeated"] is False
        assert expected["request_id"] == observed["request_id"]
        assert expected["status"] == "reconciliation_required"
        replay_status, replay = _post(endpoint, "/v1/mission-requests", original, "lost-response")
        assert replay_status == 202 and replay == expected
        assert _post(endpoint, "/v1/http-commands/reconcile", lookup)[1] == reconciled
        assert calls == 1
        assert len(MissionRequestStore(tmp_path / "requests.sqlite3").records()) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_running_command_cannot_be_completed_by_reconciliation(tmp_path: Path) -> None:
    """The same authority observes active work but cannot claim interruption or complete it."""
    engine = _engine(
        tmp_path,
        FakeInterpreter([_assessment()]),
        FakePlanner(),
        FakeController(_inventory(*_fixture_contracts())),
    )
    identity: HttpCommandIdentity = (
        "principal",
        "tenant",
        "POST",
        "/v1/mission-requests",
        "active",
    )
    assert (
        engine.begin_http_idempotency(
            principal_id=identity[0],
            tenant_id=identity[1],
            method=identity[2],
            path=identity[3],
            idempotency_key=identity[4],
            request_digest="sha256:input",
        )[0]
        == "new"
    )
    with engine.authenticated_command("tenant", False, identity):
        record = engine.create("one execution")
        observed = engine.http_command(identity, reconcile=True)
        assert observed["executing"] is True and observed["status"] == "pending"
        assert observed["request_id"] == record.request_id
    assert engine.http_command(identity, reconcile=True)["status"] == "complete"
    with pytest.raises(MissionRequestError, match="unknown HTTP"):
        engine.http_command(("another-principal", *identity[1:]))
    with pytest.raises(MissionRequestError, match="unknown HTTP"):
        engine.http_command((identity[0], "another-tenant", *identity[2:]))


def test_progress_write_failure_rolls_back_request_in_the_same_transaction(tmp_path: Path) -> None:
    """A journal linkage failure cannot leave a Request that the caller key cannot identify."""
    engine = _engine(
        tmp_path,
        FakeInterpreter([]),
        FakePlanner(),
        FakeController(_inventory(*_fixture_contracts())),
    )
    identity: HttpCommandIdentity = (
        "principal",
        "tenant",
        "POST",
        "/v1/mission-requests",
        "atomic",
    )
    engine.begin_http_idempotency(
        principal_id=identity[0],
        tenant_id=identity[1],
        method=identity[2],
        path=identity[3],
        idempotency_key=identity[4],
        request_digest="sha256:input",
    )
    database = tmp_path / "requests.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute("""CREATE TRIGGER reject_progress
                              BEFORE UPDATE ON mission_http_idempotency
                              BEGIN SELECT RAISE(ABORT, 'injected journal failure'); END""")
    with (
        engine.authenticated_command("tenant", False, identity),
        pytest.raises(sqlite3.IntegrityError),
    ):
        engine.create("must roll back")
    assert MissionRequestStore(database).records() == ()
    assert engine.http_command(identity)["request_id"] is None


def test_sqlite_http_profile_has_one_process_owner(tmp_path: Path) -> None:
    """A second HTTP writer rejects before listening, and shutdown permits an owned replacement."""
    engine = _engine(
        tmp_path,
        FakeInterpreter([]),
        FakePlanner(),
        FakeController(_inventory(*_fixture_contracts())),
    )
    server = MissionRequestHttpServer(("127.0.0.1", 0), engine, 1024)
    try:
        with pytest.raises(MissionRequestError, match="already owned"):
            MissionRequestHttpServer(("127.0.0.1", 0), engine, 1024)
        with pytest.raises(MissionRequestError, match="already owned"):
            _engine(
                tmp_path,
                FakeInterpreter([]),
                FakePlanner(),
                FakeController(_inventory(*_fixture_contracts())),
            )
    finally:
        server.server_close()
    replacement = MissionRequestHttpServer(("127.0.0.1", 0), engine, 1024)
    replacement.server_close()
