"""Actual HTTP cancellation receipts keep preview, effect acceptance and observed state separate."""

from __future__ import annotations

import json
import shutil
import ssl
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import cast

import pytest
from mission.models import JSONObject
from roboguide_workbench.actions import MissionActions, commands_clear
from roboguide_workbench.cancellation import CancellationActions, render_preview
from roboguide_workbench.client import ReadOnlyController, WorkbenchError
from roboguide_workbench.persistence import SessionStore
from test_requests import (
    FakeController,
    FakeInterpreter,
    FakePlanner,
    _assessment,
    _engine,
    _fixture_contracts,
    _inventory,
)
from test_workbench_tls import openssl
from test_workbench_v2 import mission_server


@contextmanager
def controller_server(
    tls: ssl.SSLContext | None = None,
    *,
    ca: Path | None = None,
    certificate: Path | None = None,
    key: Path | None = None,
    body_waiting: threading.Event | None = None,
    body_release: threading.Event | None = None,
) -> Iterator[tuple[ReadOnlyController, JSONObject, list[tuple[str, str]]]]:
    """Serve actual scoped Controller HTTP without manufacturing device or stop evidence."""
    state: JSONObject = {
        "mission_id": "m1",
        "group_id": "g1",
        "status": "Running",
        "tasks": [],
        "post_status": 202,
        "reply_status": "Cancelling",
    }
    calls: list[tuple[str, str]] = []

    class Handler(BaseHTTPRequestHandler):
        """Record exact original request paths and scope before returning fixed software facts."""

        def do_GET(self) -> None:
            """Read only the exact original Mission under the fixture's tenant scope."""
            calls.append(("GET", self.path))
            assert self.headers.get("X-RoboGuide-Tenant") == "team-a"
            assert self.path == "/v1/missions/m1"
            self.send(200, {k: state[k] for k in ("mission_id", "group_id", "status", "tasks")})

        def do_POST(self) -> None:
            """Apply one fixture cancellation while retaining distinguishable HTTP acceptance."""
            calls.append(("POST", self.path))
            assert self.headers.get("X-RoboGuide-Tenant") == "team-a"
            assert self.headers.get("Idempotency-Key") is None
            assert self.path == "/v1/missions/m1/cancel"
            assert self.rfile.read(int(self.headers["Content-Length"])) == b"{}"
            if state["post_status"] == 202:
                state["status"] = state["reply_status"]
                self.send(202, {"status": state["reply_status"]})
            else:
                self.send(cast(int, state["post_status"]), {"error": "refused"})

        def send(self, status: int, raw: JSONObject) -> None:
            """Use complete framed responses so transport errors cannot hide assertions."""
            body = json.dumps(raw).encode()
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command == "POST" and body_waiting is not None:
                body_waiting.set()
            if self.command == "POST" and body_release is not None:
                body_release.wait(timeout=5)
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, format: str, *args: object) -> None:
            """Keep fixture HTTP access output independent of cancellation result rendering."""

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = False
    if tls is not None:
        server.socket = tls.wrap_socket(server.socket, server_side=True)
    worker = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    worker.start()
    try:
        scheme = "https" if tls is not None else "http"
        yield (
            ReadOnlyController(
                f"{scheme}://127.0.0.1:{server.server_port}",
                tenant="team-a",
                ca=ca,
                certificate=certificate,
                key=key,
            ),
            state,
            calls,
        )
    finally:
        if body_release is not None:
            body_release.set()
        server.shutdown()
        server.server_close()
        worker.join(timeout=5)
        assert not worker.is_alive()


def fixture_actions(tmp_path: Path, controller: ReadOnlyController) -> CancellationActions:
    """Keep the scoped local journal independent of an unused Mission endpoint."""
    store = SessionStore(
        tmp_path / "sessions", "cancel", {"controller": controller.endpoint}, False
    )
    return CancellationActions(
        controller, MissionActions(ReadOnlyController("http://127.0.0.1:9"), store)
    )


def test_planning_request_cancel_uses_original_mission_http_and_never_submits(
    tmp_path: Path,
) -> None:
    """Preview and cancel a real reviewed plan through production HTTP and SQLite once."""
    controller = FakeController(_inventory(*_fixture_contracts()))
    engine = _engine(tmp_path, FakeInterpreter([_assessment()]), FakePlanner(), controller)
    with mission_server(engine) as endpoint:
        store = SessionStore(tmp_path / "sessions", "request", {}, False)
        actions = MissionActions(ReadOnlyController(endpoint), store)
        cancellation = CancellationActions(ReadOnlyController("http://127.0.0.1:9"), actions)
        try:
            raw = actions.command(
                "create",
                {
                    "schema_version": "roboguide.mission-request-create/v0.2",
                    "instruction": "plan",
                    "submission_mode": "draft_only",
                },
            )
            request_id = cast(str, raw["request_id"])
            selection = cancellation.preview("request", request_id)
            assert "尚未发送取消" in render_preview(selection)
            assert engine.get(request_id).lifecycle.value == "AwaitingExecution"
            assert len(cast(list[object], store.data["commands"])) == 1
            result = cancellation.confirm(selection)
            assert result["status"] == "Cancelled" and controller.submissions == []
            assert engine.get(request_id).lifecycle.value == "Cancelled"
            assert len(cast(list[object], store.data["commands"])) == 2
            with pytest.raises(WorkbenchError, match="已为 Cancelled"):
                cancellation.confirm(selection)
            assert len(cast(list[object], store.data["commands"])) == 2
        finally:
            store.close()


def test_accepted_request_requires_explicit_mission_cancellation(tmp_path: Path) -> None:
    """An accepted planning Request cannot silently redirect a cancellation into another service."""
    controller = FakeController(_inventory(*_fixture_contracts()))
    engine = _engine(tmp_path, FakeInterpreter([_assessment()]), FakePlanner(), controller)
    record = engine.create("execute", "submit_when_ready")
    with mission_server(engine) as endpoint:
        store = SessionStore(tmp_path / "sessions", "accepted", {}, False)
        cancellation = CancellationActions(
            ReadOnlyController("http://127.0.0.1:9"),
            MissionActions(ReadOnlyController(endpoint), store),
        )
        try:
            with pytest.raises(WorkbenchError, match="/cancel mission"):
                cancellation.preview("request", record.request_id)
            assert store.data["commands"] == [] and len(controller.submissions) == 1
        finally:
            store.close()


def test_request_preview_does_not_authorize_a_changed_draft(tmp_path: Path) -> None:
    """The confirmation refresh checks the original Request version and lifecycle before POST."""
    controller = FakeController(_inventory(*_fixture_contracts()))
    engine = _engine(
        tmp_path, FakeInterpreter([_assessment(), _assessment()]), FakePlanner(), controller
    )
    record = engine.create("plan", "draft_only")
    with mission_server(engine) as endpoint:
        store = SessionStore(tmp_path / "sessions", "changed", {}, False)
        cancellation = CancellationActions(
            ReadOnlyController("http://127.0.0.1:9"),
            MissionActions(ReadOnlyController(endpoint), store),
        )
        try:
            selection = cancellation.preview("request", record.request_id)
            engine.add_message(record.request_id, "change route")
            with pytest.raises(WorkbenchError, match="状态或版本已改变"):
                cancellation.confirm(selection)
            assert store.data["commands"] == [] and controller.submissions == []
        finally:
            store.close()


def test_controller_cancel_acknowledges_acceptance_without_claiming_a_terminal_state(
    tmp_path: Path,
) -> None:
    """A POST may acknowledge Cancelling while a later GET independently observes Cancelled."""
    with controller_server() as (controller, state, calls):
        cancellation = fixture_actions(tmp_path, controller)
        try:
            selection = cancellation.preview("mission", "m1")
            assert calls == [("GET", "/v1/missions/m1")]
            assert cancellation.actions.store.data["commands"] == []
            result = cancellation.confirm(selection)
            assert result["status"] == "Cancelling" and "尚未确认取消终态" in cast(
                str, result["text"]
            )
            assert calls.count(("POST", "/v1/missions/m1/cancel")) == 1
            command = cast(list[JSONObject], cancellation.actions.store.data["commands"])[-1]
            assert command["state"] == "complete" and command["original_http_outcome"] == "received"
            with pytest.raises(WorkbenchError, match="Cancelling"):
                cancellation.confirm(selection)
            state["status"] = "Cancelled"
            observed = cancellation.observe()
            assert observed["status"] == "Cancelled" and observed["command_state"] == "complete"
            assert "已收到" in cast(str, observed["text"])
            assert calls.count(("POST", "/v1/missions/m1/cancel")) == 1
        finally:
            cancellation.actions.store.close()


@pytest.mark.parametrize("changed", [{"status": "Completed"}, {"group_id": "different"}])
def test_changed_mission_target_refuses_before_effect(tmp_path: Path, changed: JSONObject) -> None:
    """A changed target snapshot cannot borrow an earlier user-visible cancellation selection."""
    with controller_server() as (controller, state, calls):
        cancellation = fixture_actions(tmp_path, controller)
        try:
            selection = cancellation.preview("mission", "m1")
            state.update(changed)
            with pytest.raises(WorkbenchError):
                cancellation.confirm(selection)
            assert not any(method == "POST" for method, _ in calls)
            assert cancellation.actions.store.data["commands"] == []
        finally:
            cancellation.actions.store.close()


@pytest.mark.parametrize("target", ["#1", "space id", "x/y", "x%20y", "trailing "])
def test_unsupported_encoded_cancel_ids_refuse_without_read_or_effect(
    tmp_path: Path, target: str
) -> None:
    """The current backend's literal path parser cannot receive a percent-encoded replacement ID."""
    with controller_server() as (controller, _, calls):
        cancellation = fixture_actions(tmp_path, controller)
        try:
            with pytest.raises(WorkbenchError, match="路径编码"):
                cancellation.preview("mission", target)
            assert calls == [] and cancellation.actions.store.data["commands"] == []
        finally:
            cancellation.actions.store.close()


@pytest.mark.parametrize("post_status", [403, 409, 503])
def test_refused_or_unavailable_cancel_stays_original_pending_without_replay(
    tmp_path: Path, post_status: int
) -> None:
    """A command HTTP failure does not become absence of effects or authorization to retry."""
    with controller_server() as (controller, state, calls):
        state["post_status"] = post_status
        cancellation = fixture_actions(tmp_path, controller)
        try:
            selection = cancellation.preview("mission", "m1")
            with pytest.raises(WorkbenchError, match=f"HTTP {post_status}"):
                cancellation.confirm(selection)
            command = cast(list[JSONObject], cancellation.actions.store.data["commands"])[-1]
            before = dict(command)
            assert command["state"] == "pending" and command["original_http_outcome"] == "unknown"
            with pytest.raises(WorkbenchError, match="原命令"):
                cancellation.confirm(selection)
            observed = cancellation.observe()
            assert observed["command_state"] == "pending" and not commands_clear([command])
            assert command["accepted_at"] == before["accepted_at"]
            assert command["operation_id"] == before["operation_id"]
            assert calls.count(("POST", "/v1/missions/m1/cancel")) == 1
        finally:
            cancellation.actions.store.close()


def test_lost_controller_response_is_observed_after_restart_without_invented_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An actual HTTP effect followed by response loss retains Unknown across session restart."""
    with controller_server() as (controller, state, calls):
        cancellation = fixture_actions(tmp_path, controller)
        original = cancellation._post

        def lose_response(path: str) -> JSONObject:
            """Deliver the original POST then lose its complete response at transport."""
            original(path)
            raise WorkbenchError("injected response loss")

        monkeypatch.setattr(cancellation, "_post", lose_response)
        selection = cancellation.preview("mission", "m1")
        with pytest.raises(WorkbenchError, match="response loss"):
            cancellation.confirm(selection)
        store = cancellation.actions.store
        before = cast(list[JSONObject], store.data["commands"])[-1].copy()
        store.close()
        restored = SessionStore(
            tmp_path / "sessions", "cancel", {"controller": controller.endpoint}, True
        )
        cancellation = CancellationActions(
            controller, MissionActions(ReadOnlyController("http://127.0.0.1:9"), restored)
        )
        try:
            pending = cancellation.observe()
            assert pending["status"] == "Cancelling" and pending["command_state"] == "pending"
            state["status"] = "Cancelled"
            final = cancellation.observe()
            command = cast(list[JSONObject], restored.data["commands"])[-1]
            assert command["state"] == "resolved" and commands_clear([command])
            assert command["original_http_outcome"] == "unknown" and "response" not in command
            assert final["original_http_outcome"] == "unknown"
            assert command["operation_id"] == before["operation_id"]
            assert command["accepted_at"] == before["accepted_at"]
            assert calls.count(("POST", "/v1/missions/m1/cancel")) == 1
            with pytest.raises(WorkbenchError, match="Cancelled"):
                cancellation.confirm(selection)
        finally:
            restored.close()


def test_journal_write_failure_refuses_cancel_before_http(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed original command fsync grants no permission to send cancellation bytes."""
    with controller_server() as (controller, _, calls):
        cancellation = fixture_actions(tmp_path, controller)

        def unavailable() -> None:
            """Refuse the retained original command before any Controller mutation starts."""
            raise OSError("injected save failure")

        monkeypatch.setattr(cancellation.actions.store, "save", unavailable)
        try:
            selection = cancellation.preview("mission", "m1")
            with pytest.raises(OSError, match="save failure"):
                cancellation.confirm(selection)
            assert not any(method == "POST" for method, _ in calls)
        finally:
            cancellation.actions.store.close()


def test_observation_refuses_changed_origin_or_group(tmp_path: Path) -> None:
    """Neither another service origin nor another Group can resolve the original cancellation."""
    with controller_server() as (controller, state, calls):
        cancellation = fixture_actions(tmp_path, controller)
        try:
            selection = cancellation.preview("mission", "m1")
            cancellation.confirm(selection)
            state["group_id"] = "other"
            with pytest.raises(WorkbenchError, match="Group"):
                cancellation.observe()
            command = cast(list[JSONObject], cancellation.actions.store.data["commands"])[-1]
            command["origin"] = "http://127.0.0.1:9"
            before = len(calls)
            with pytest.raises(WorkbenchError, match="来源或对象"):
                cancellation.observe()
            assert len(calls) == before
        finally:
            cancellation.actions.store.close()


def test_resolved_guard_refuses_arbitrary_or_contradictory_resolutions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only an exact same-Mission Cancelled observation may unlock a formerly unknown effect."""
    with controller_server() as (controller, state, _):
        cancellation = fixture_actions(tmp_path, controller)

        def lose_response(path: str) -> JSONObject:
            """Preserve Unknown without issuing a retry or fabricated complete response."""
            raise WorkbenchError("injected response loss")

        monkeypatch.setattr(cancellation, "_post", lose_response)
        try:
            with pytest.raises(WorkbenchError):
                cancellation.confirm(cancellation.preview("mission", "m1"))
            state["status"] = "Cancelled"
            cancellation.observe()
            command = cast(list[JSONObject], cancellation.actions.store.data["commands"])[-1]
            assert commands_clear([command])
            for key, value in {
                "service": "mission",
                "action": "execute",
                "path": "/other",
                "group_id": "other",
                "mission_id": "other",
                "operation_id": "",
                "original_http_outcome": "received",
            }.items():
                assert not commands_clear([{**command, key: value}]), key
            assert not commands_clear([{"state": "resolved"}])
        finally:
            cancellation.actions.store.close()


def test_actual_http_timeout_after_effect_keeps_original_pending(tmp_path: Path) -> None:
    """Actual cancel response timeout never retries a command already accepted by its handler."""
    waiting, release = threading.Event(), threading.Event()
    with controller_server(body_waiting=waiting, body_release=release) as (
        controller,
        state,
        calls,
    ):
        controller.timeout = 0.3
        cancellation = fixture_actions(tmp_path, controller)
        try:
            selection = cancellation.preview("mission", "m1")
            with pytest.raises(WorkbenchError, match="超时"):
                cancellation.confirm(selection)
            assert waiting.is_set() and state["status"] == "Cancelling"
            command = cast(list[JSONObject], cancellation.actions.store.data["commands"])[-1]
            assert command["state"] == "pending" and command["original_http_outcome"] == "unknown"
            with pytest.raises(WorkbenchError, match="原命令"):
                cancellation.confirm(selection)
            release.set()
            assert cancellation.observe()["status"] == "Cancelling"
            assert calls.count(("POST", "/v1/missions/m1/cancel")) == 1
        finally:
            cancellation.actions.store.close()


def test_cancel_uses_actual_original_mtls_context_and_tenant(tmp_path: Path) -> None:
    """The command uses its Controller certificate and tenant independently of Mission TLS."""
    if shutil.which("openssl") is None:
        pytest.skip("actual cancellation mTLS fixture requires openssl")
    openssl(
        tmp_path,
        "req",
        "-x509",
        "-newkey",
        "rsa:2048",
        "-nodes",
        "-days",
        "1",
        "-subj",
        "/CN=Cancel Test CA",
        "-keyout",
        "ca.key",
        "-out",
        "ca.crt",
        "-addext",
        "basicConstraints=critical,CA:TRUE",
        "-addext",
        "keyUsage=critical,keyCertSign,cRLSign",
    )
    for name, usage in (("server", "serverAuth"), ("client", "clientAuth")):
        openssl(
            tmp_path,
            "req",
            "-new",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-subj",
            f"/CN={name}",
            "-keyout",
            f"{name}.key",
            "-out",
            f"{name}.csr",
        )
        extension = tmp_path / f"{name}.ext"
        extension.write_text(
            "basicConstraints=critical,CA:FALSE\n"
            "keyUsage=critical,digitalSignature,keyEncipherment\n"
            f"extendedKeyUsage={usage}\nsubjectAltName=IP:127.0.0.1\n"
        )
        openssl(
            tmp_path,
            "x509",
            "-req",
            "-in",
            f"{name}.csr",
            "-CA",
            "ca.crt",
            "-CAkey",
            "ca.key",
            "-CAcreateserial",
            "-days",
            "1",
            "-extfile",
            extension.name,
            "-out",
            f"{name}.crt",
        )
    server_tls = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    server_tls.load_cert_chain(tmp_path / "server.crt", tmp_path / "server.key")
    server_tls.load_verify_locations(tmp_path / "ca.crt")
    server_tls.verify_mode = ssl.CERT_REQUIRED
    with controller_server(
        server_tls,
        ca=tmp_path / "ca.crt",
        certificate=tmp_path / "client.crt",
        key=tmp_path / "client.key",
    ) as (controller, _, calls):
        cancellation = fixture_actions(tmp_path, controller)
        try:
            result = cancellation.confirm(cancellation.preview("mission", "m1"))
            assert result["status"] == "Cancelling"
            assert cancellation.observe()["status"] == "Cancelling"
            assert calls.count(("POST", "/v1/missions/m1/cancel")) == 1
        finally:
            cancellation.actions.store.close()
