"""Local v0.4 navigation, inspection and displayed cancellation integration regressions."""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import cast

import pytest
from mission.deployment_assessment import plan_body_digest
from mission.models import JSONObject, JSONValue
from roboguide_workbench.actions import MissionActions
from roboguide_workbench.cli import display_question
from roboguide_workbench.client import ReadOnlyController, WorkbenchError
from roboguide_workbench.conversation import ConversationSession
from roboguide_workbench.persistence import SessionStore
from roboguide_workbench.preflight import check_plan
from roboguide_workbench.sessions import list_sessions, navigate
from roboguide_workbench.status import status_line
from test_requests import (
    FakeController,
    FakeInterpreter,
    FakePlanner,
    _assessment,
    _engine,
    _fixture_contracts,
    _inventory,
)
from test_workbench import no_model
from test_workbench_deadline import response_server
from test_workbench_display import workbench
from test_workbench_v2 import ObservedController


def session_at(tmp_path: Path, name: str, tenant: str = "one") -> ConversationSession:
    """Open one private session under an exact local scope without service access."""
    store = SessionStore(tmp_path / "sessions", name, {"tenant": tenant}, False)
    reader = ObservedController()
    return ConversationSession(reader, no_model, store, MissionActions(reader, store))


def test_navigation_preserves_store_owner_and_original_session_history(tmp_path: Path) -> None:
    """A frontend may switch sessions while its original main-finally Store still owns the lease."""
    session = session_at(tmp_path, "one")
    owner = session.store
    try:
        session.ask("/nodes")
        original = session.store.path.read_bytes()
        assert "two" in (navigate(session, "/new two") or "")
        assert session.store is owner and session.store.path.stem == "two"
        assert session.shown_draft is None and session.displayed_nodes == []
        with pytest.raises(OSError):
            SessionStore(owner.path.parent, "two", {"tenant": "one"}, True)
        assert "one" in list_sessions(session) and "two" in list_sessions(session)
        assert "one" in (navigate(session, "/resume one") or "")
        assert session.displayed_nodes == ["a", "b"]
        assert session.store.path.read_bytes() == original
        # The departed session lock has actually been released.
        departed = SessionStore(owner.path.parent, "two", {"tenant": "one"}, True)
        departed.close()
    finally:
        owner.close()


def test_navigation_refuses_unknown_commands_and_cross_scope(tmp_path: Path) -> None:
    """Session menus preserve scope and the barrier from an original pending effect."""
    session = session_at(tmp_path, "one")
    other = session_at(tmp_path, "foreign", "two")
    other.store.close()
    try:
        assert "foreign" not in list_sessions(session)
        with pytest.raises(WorkbenchError, match="来源或身份"):
            navigate(session, "/resume foreign")
        session.store.data["commands"] = [{"state": "pending", "idempotency_key": "original"}]
        session.store.save()
        for text in ("/new next", "/resume foreign"):
            with pytest.raises(WorkbenchError, match="原命令"):
                navigate(session, text)
        assert not (session.store.path.parent / "next.json").exists()
    finally:
        session.store.close()


def test_session_listing_does_not_open_special_files(tmp_path: Path) -> None:
    """A user directory FIFO or symlink is never read as a bounded session snapshot."""
    import os

    session = session_at(tmp_path, "one")
    try:
        os.mkfifo(session.store.path.parent / "pipe.json", mode=0o600)
        (session.store.path.parent / "link.json").symlink_to(session.store.path)
        assert "one" in list_sessions(session) and "pipe" not in list_sessions(session)
    finally:
        session.store.close()


def test_status_is_local_and_does_not_claim_connection_readiness(tmp_path: Path) -> None:
    """The toolbar has scope/model/session facts while backend readiness stays unobserved."""
    session = session_at(tmp_path, "status")
    try:
        session.model_label = "configured-model"
        text = status_line(session)
        assert "configured-model" in text and "status" in text and "未探测" in text
        assert cast(ObservedController, session.controller).reads == []
    finally:
        session.store.close()


@pytest.mark.parametrize("reply_kind", ["complete", "blocked", "detached", "unavailable"])
def test_check_uses_exact_plan_and_never_submits(tmp_path: Path, reply_kind: str) -> None:
    """Two readonly assessment POSTs cannot create a Mission or borrow another plan's feedback."""
    controller = FakeController(_inventory(*_fixture_contracts()))
    engine = _engine(tmp_path, FakeInterpreter([_assessment()]), FakePlanner(), controller)
    record = engine.create("只规划", "draft_only")
    assert record.plan is not None
    assert record.plan is not None
    digest = plan_body_digest(record.plan)
    validation: JSONObject = {
        "schema_version": "roboguide.plan-validation/v0.1",
        "scope": "published-contracts",
        "mission_id": record.mission_id,
        "plan_body_sha256": digest,
        "decision": "not_blocked",
        "verifier_source_digest": None,
        "deployment_digest": None,
        "execution_lifetime": None,
        "world_identity": None,
        "issues": [],
        "issues_truncated": False,
    }
    if reply_kind == "blocked":
        validation.update(
            {
                "decision": "blocked",
                "issues": [
                    {
                        "code": "deployment.operation_unsupported",
                        "path": "/tasks/0",
                        "message": "unsupported",
                    }
                ],
            }
        )
    if reply_kind == "detached":
        validation["mission_id"] = "other"
    assessment: JSONObject = {
        "schema_version": "roboguide.initial-operation-assessment/v0.1",
        "scope": "initial_static_world",
        "mission_id": record.mission_id,
        "plan_body_sha256": digest,
        "decision": "unavailable",
        "reason_code": "source_unconfigured",
        "source_digest": None,
        "world_snapshot_digest": None,
        "local_how_digest": None,
        "received_at_ms": None,
        "expires_at_ms": None,
        "assessed_at_ms": 1,
        "checked_combinations": 0,
        "roles": [],
    }
    seen: list[str] = []

    def serve(connection: socket.socket, stopped: object) -> None:
        """Read the actual original request path and body from the production HTTP client."""
        # response_server's handler consumed request bytes; choose fixtures by original call order.
        seen.append("assessment" if len(seen) else "validation")
        raw = json.dumps(validation if len(seen) == 1 else assessment).encode()
        connection.sendall(
            b"HTTP/1.1 200 OK\r\nContent-Length: "
            + str(len(raw)).encode()
            + b"\r\nConnection: close\r\n\r\n"
            + raw
        )

    with response_server(serve) as (endpoint, methods):
        checked = check_plan(ReadOnlyController(endpoint), record)
        assert methods == ["POST", "POST"]
    assert not checked.complete  # The original support source is unavailable, never silently green.
    assert checked.components[0]["complete"] is (reply_kind != "detached")
    assert controller.submissions == [] and engine.get(record.request_id) == record
    if reply_kind == "blocked":
        assert "存在已知阻塞" in checked.text


def test_cancel_preview_requires_successful_display_and_confirm(tmp_path: Path) -> None:
    """Production Request cancellation never bypasses display acknowledgment."""
    with workbench(tmp_path) as (session, controller):
        display_question(session, "/plan 只规划")
        request_id = session.request_id
        assert request_id is not None
        display_question(session, "/cancel request " + request_id)
        assert session.pending_cancel is not None and controller.submissions == []
        display_question(session, "/cancel confirm")
        current = session.actions.get(request_id)
        assert current["lifecycle"] == "Cancelled"
        assert controller.submissions == [] and session.pending_cancel is None
        with pytest.raises(WorkbenchError, match="请先 /cancel"):
            display_question(session, "/cancel confirm")


@pytest.mark.parametrize("blocked", [False, True])
def test_check_complete_actual_http_is_bound_to_received_plan_bytes(
    tmp_path: Path, blocked: bool
) -> None:
    """A usable full-support reply must match exactly the bytes sent to each assessment endpoint."""
    import hashlib
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from mission.models import MissionPlan

    controller = FakeController(_inventory(*_fixture_contracts()))
    engine = _engine(tmp_path, FakeInterpreter([_assessment()]), FakePlanner(), controller)
    record = engine.create("只规划", "draft_only")
    assert record.plan is not None
    calls: list[tuple[str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        """Bind independent fixture observations to the actual received plan bytes."""

        def do_POST(self) -> None:
            """Produce one typed inspection projection without admitting or submitting a Mission."""
            body = self.rfile.read(int(self.headers["Content-Length"]))
            plan = MissionPlan.from_json(json.loads(body))
            digest = "sha256:" + hashlib.sha256(body).hexdigest()
            calls.append((self.path, digest))
            raw: JSONObject
            if self.path.endswith("validate-plan"):
                raw = {
                    "schema_version": "roboguide.plan-validation/v0.1",
                    "scope": "published-contracts",
                    "mission_id": record.mission_id,
                    "plan_body_sha256": digest,
                    "decision": "not_blocked",
                    "verifier_source_digest": None,
                    "deployment_digest": None,
                    "execution_lifetime": None,
                    "world_identity": None,
                    "issues": [],
                    "issues_truncated": False,
                }
            else:
                roots = [task for task in plan.tasks if not task.depends_on]
                roles = []
                for task in roots:
                    for role in task.roles:
                        operation = role.execution.operation
                        roles.append(
                            {
                                "task_id": task.task_id,
                                "role_id": role.role_id,
                                "operation": (
                                    f"{operation.namespace}.{operation.name}@{operation.version}"
                                ),
                                "witness_count": 0 if blocked else 1,
                                "bounded_miss_count": 0,
                                "scoped_disjoint_count": 1 if blocked else 0,
                                "unknown_count": 0,
                            }
                        )
                raw = {
                    "schema_version": "roboguide.initial-operation-assessment/v0.1",
                    "scope": "initial_static_world",
                    "mission_id": record.mission_id,
                    "plan_body_sha256": digest,
                    "decision": "blocked" if blocked else "not_blocked",
                    "reason_code": "scoped_static_support_shortage"
                    if blocked
                    else "no_scoped_shortage",
                    "source_digest": "sha256:" + "a" * 64,
                    "world_snapshot_digest": "sha256:" + "b" * 64,
                    "local_how_digest": "sha256:" + "c" * 64,
                    "received_at_ms": 1,
                    "expires_at_ms": 100,
                    "assessed_at_ms": 2,
                    "checked_combinations": 1,
                    "roles": cast(JSONValue, roles),
                }
            encoded = json.dumps(raw).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, format: str, *args: object) -> None:
            """Keep fixture access text out of ordinary CLI diagnostics."""

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = False
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        result = check_plan(ReadOnlyController(f"http://127.0.0.1:{server.server_port}"), record)
        assert result.complete, result.text
        assert len(calls) == 2
        assert all(digest == plan_body_digest(record.plan) for _, digest in calls)
        assert result.components[-1]["decision"] == ("blocked" if blocked else "not_blocked")
        assert controller.submissions == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_failed_cancel_preview_cannot_be_confirmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cancellation target becomes confirmable only after its original preview is flushed."""
    import io
    import sys

    class Output(io.StringIO):
        """Fail the preview write while allowing the actual original request to remain retained."""

        def write(self, text: str) -> int:
            """Expose a real failed output acknowledgment before user confirmation."""
            if "待确认取消" in text:
                raise BrokenPipeError("preview display lost")
            return super().write(text)

    with workbench(tmp_path) as (session, controller):
        display_question(session, "/plan 只规划")
        request = session.request_id
        assert request is not None
        monkeypatch.setattr(sys, "stdout", Output())
        with pytest.raises(BrokenPipeError):
            display_question(session, "/cancel request " + request)
        assert session.pending_cancel is None
        with pytest.raises(WorkbenchError, match="请先 /cancel"):
            display_question(session, "/cancel confirm")
        assert session.actions.get(request)["lifecycle"] == "AwaitingExecution"
        assert len(cast(list[JSONObject], session.store.data["commands"])) == 1
        assert controller.submissions == []


@pytest.mark.parametrize("frontend", ["plain", "async"])
def test_failed_session_switch_display_stops_dependent_queries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, frontend: str
) -> None:
    """An unseen session transition cannot silently change the next query's reference scope."""
    import asyncio
    import builtins
    import io
    import sys

    from roboguide_workbench import cli, terminal
    from test_workbench_display import ScriptedPrompt, command

    class Output(io.StringIO):
        """Fail the switched-context message once while preserving any later error explanation."""

        failed = False

        def write(self, text: str) -> int:
            """Make the switch effect real but its display unavailable."""
            if "已切换会话" in text and not self.failed:
                self.failed = True
                raise BrokenPipeError("switch display lost")
            return super().write(text)

    session = session_at(tmp_path, "one")
    output = Output()
    monkeypatch.setattr(sys, "stdout", output)
    try:
        if frontend == "plain":
            count = 0

            def enter(prompt: str) -> str:
                """A second prompt would allow a query against a session the user never saw."""
                nonlocal count
                count += 1
                assert count == 1
                return "/new two"

            monkeypatch.setattr(builtins, "input", enter)
            result = cli.interactive(session)
            assert count == 1
        else:
            prompt = ScriptedPrompt([command("/new two")])
            prompt.install(monkeypatch)
            result = asyncio.run(terminal.interactive(session))
        assert result == 2 and session.store.path.stem == "two"
        assert output.failed and session.pending_cancel is None and session.shown_draft is None
    finally:
        session.store.close()
