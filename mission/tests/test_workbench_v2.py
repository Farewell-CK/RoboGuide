"""Real Mission HTTP and private-session tests for explicit workbench execution intent."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import types
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import cast

import pytest
from mission.api import MissionRequestHttpServer
from mission.models import JSONObject
from mission.request_engine import MissionRequestEngine
from roboguide_workbench.actions import MissionActions
from roboguide_workbench.cli import parser
from roboguide_workbench.client import ReadOnlyController, WorkbenchError
from roboguide_workbench.configuration import apply_profile
from roboguide_workbench.persistence import SessionStore
from roboguide_workbench.workspace import WorkbenchSession
from test_http_commands import _post
from test_requests import (
    FakeController,
    FakeInterpreter,
    FakePlanner,
    _assessment,
    _engine,
    _fixture_contracts,
    _inventory,
)
from test_submission_intent import original_source
from test_workbench import ScriptedTransport, descriptor, inventory, no_model, node, router


@contextmanager
def mission_server(engine: MissionRequestEngine) -> Iterator[str]:
    """Run the production HTTP handler over actual SQLite and deterministic model fixtures."""
    server = MissionRequestHttpServer(("127.0.0.1", 0), engine, 1024 * 1024)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class ObservedController(ReadOnlyController):
    """Provide clearly labelled deployment projections while logging each fresh query."""

    def __init__(self) -> None:
        """Use an unreachable fixed origin; all calls below are explicitly synthetic fixtures."""
        super().__init__("http://127.0.0.1:9")
        self.reads: list[str] = []

    def inventory(self) -> JSONObject:
        """Return two distinct capabilities to test host-computed comparisons."""
        self.reads.append("inventory")
        return inventory(node("a"), node("b", mapping=True))

    def missions(self) -> JSONObject:
        """Return existing tenant-visible Mission records rather than a made-up list endpoint."""
        self.reads.append("missions")
        return {
            "schema": "roboguide.state-query/v0.1",
            "records": [
                {
                    "object": {"object_type": "mission", "object_id": "m1"},
                    "channel_id": "accepted-plan",
                    "value": {"objective": "检查仓库"},
                },
                {
                    "object": {"object_type": "mission", "object_id": "m1"},
                    "channel_id": "mission-lifecycle",
                    "value": {"lifecycle": "Running"},
                },
            ],
        }

    def mission(self, mission_id: str) -> JSONObject:
        """Return actual fixture Task state with no physical success assertion."""
        self.reads.append("mission:" + mission_id)
        return {"mission_id": mission_id, "group_id": "g1", "status": "Running", "tasks": []}

    def mission_bindings(self, group_id: str) -> JSONObject:
        """Supply a typed empty binding projection without assigning anything."""
        self.reads.append("bindings:" + group_id)
        return {"schema": "roboguide.state-query/v0.1", "records": []}

    def mission_recovery(self, mission_id: str) -> JSONObject:
        """Exercise partial diagnostic unavailability without failing the primary status read."""
        self.reads.append("recovery:" + mission_id)
        raise WorkbenchError("原恢复评估 unavailable")


def test_complete_plan_modify_execute_flow_with_real_http_and_resume(tmp_path: Path) -> None:
    """Plan, close the UI, resume, modify and execute one exact draft with only one submission."""
    controller = FakeController(_inventory(*_fixture_contracts()))
    engine = _engine(
        tmp_path, FakeInterpreter([_assessment(), _assessment()]), FakePlanner(), controller
    )
    with mission_server(engine) as endpoint:
        scope: JSONObject = {"mission": endpoint, "tenant": "local"}
        store = SessionStore(tmp_path / "sessions", "one", scope, False)
        session = WorkbenchSession(
            ObservedController(),
            no_model,
            store,
            MissionActions(ReadOnlyController(endpoint), store),
        )
        text = session.ask("/plan 检查仓库")
        assert "AwaitingExecution" in text and "计划任务与原始参数" in text
        assert controller.submissions == []
        request_id = session.request_id
        store.close()
        store = SessionStore(tmp_path / "sessions", "one", scope, True)
        try:
            session = WorkbenchSession(
                ObservedController(),
                no_model,
                store,
                MissionActions(ReadOnlyController(endpoint), store),
            )
            assert session.request_id == request_id
            with pytest.raises(WorkbenchError, match="先 /request"):
                session.ask("/execute")
            session.ask("/request")
            assert "AwaitingExecution" in session.ask("/answer 先检查东侧")
            assert controller.submissions == []
            assert "Accepted" in session.ask("/execute")
            assert len(controller.submissions) == 1
            assert len(cast(list[object], store.data["commands"])) == 3
        finally:
            store.close()


def test_natural_language_actions_wait_for_explicit_choice(tmp_path: Path) -> None:
    """An action classification forwards no command until an explicit original-text selection."""
    controller = FakeController(_inventory(*_fixture_contracts()))
    engine = _engine(tmp_path, FakeInterpreter([_assessment()]), FakePlanner(), controller)
    with mission_server(engine) as endpoint:
        store = SessionStore(tmp_path / "sessions", "one", {}, False)
        transport = ScriptedTransport([descriptor("run")])
        selected = router(transport)
        session = WorkbenchSession(
            ObservedController(),
            lambda: selected,
            store,
            MissionActions(ReadOnlyController(endpoint), store),
        )
        try:
            proposal = session.ask("让机器人巡检仓库")
            assert "/choose plan" in proposal
            assert engine._store.records() == ()
            result = session.ask("/choose plan")
            assert "AwaitingExecution" in result
            assert engine.get(session.request_id or "").instruction == "让机器人巡检仓库"
            assert controller.submissions == []
        finally:
            store.close()


def test_lost_create_response_is_observed_under_same_key_without_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A completed server command lost in transport is recovered from its original HTTP receipt."""
    controller = FakeController(_inventory(*_fixture_contracts()))
    interpreter = FakeInterpreter([_assessment()])
    engine = _engine(tmp_path, interpreter, FakePlanner(), controller)
    with mission_server(engine) as endpoint:
        store = SessionStore(tmp_path / "sessions", "one", {}, False)
        actions = MissionActions(ReadOnlyController(endpoint), store)
        original = actions._post

        def lost(path: str, body: JSONObject, key: str | None = None) -> JSONObject:
            """Deliver the original mutation successfully and lose its response."""
            original(path, body, key)
            raise WorkbenchError("injected response loss")

        monkeypatch.setattr(actions, "_post", lost)
        session = WorkbenchSession(ObservedController(), no_model, store, actions)
        try:
            with pytest.raises(WorkbenchError, match="response loss"):
                session.ask("/run 执行一次")
            with pytest.raises(WorkbenchError, match="原命令"):
                session.ask("/run 执行一次")
            assert len(controller.submissions) == 1
            pending = cast(list[JSONObject], store.data["commands"])[0]
            key = pending["idempotency_key"]
        finally:
            store.close()
        store = SessionStore(tmp_path / "sessions", "one", {}, True)
        try:
            session = WorkbenchSession(
                ObservedController(),
                no_model,
                store,
                MissionActions(ReadOnlyController(endpoint), store),
            )
            result = session.ask("/observe")
            assert "complete" in result and "Accepted" in result
            assert len(controller.submissions) == len(interpreter.calls) == 1
            assert cast(list[JSONObject], store.data["commands"])[0]["idempotency_key"] == key
        finally:
            store.close()


def test_stale_displayed_plan_refuses_client_execute(tmp_path: Path) -> None:
    """An independent edit invalidates the UI selection before execute crosses the network."""
    controller = FakeController(_inventory(*_fixture_contracts()))
    engine = _engine(
        tmp_path, FakeInterpreter([_assessment(), _assessment()]), FakePlanner(), controller
    )
    with mission_server(engine) as endpoint:
        store = SessionStore(tmp_path / "sessions", "one", {}, False)
        session = WorkbenchSession(
            ObservedController(),
            no_model,
            store,
            MissionActions(ReadOnlyController(endpoint), store),
        )
        try:
            session.ask("/plan 规划")
            engine.add_message(session.request_id or "", "另一个客户端修改了目标")
            result = session.ask("/execute")
            assert "已改变，未发出命令" in result
            assert controller.submissions == []
            assert len(cast(list[object], store.data["commands"])) == 1
        finally:
            store.close()


def test_session_scope_lock_and_private_permissions(tmp_path: Path) -> None:
    """Concurrent UI writers and identity/origin changes cannot reuse another session's commands."""
    directory = tmp_path / "sessions"
    store = SessionStore(directory, "one", {"tenant": "a"}, False)
    try:
        assert store.path.stat().st_mode & 0o077 == 0
        with pytest.raises(OSError):
            SessionStore(directory, "one", {"tenant": "a"}, True)
    finally:
        store.close()
    with pytest.raises(WorkbenchError, match="不匹配"):
        SessionStore(directory, "one", {"tenant": "b"}, True)


def test_write_ahead_failure_prevents_command_io(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If a pending intent cannot be saved, there is no HTTP request or server-side create."""
    controller = FakeController(_inventory(*_fixture_contracts()))
    engine = _engine(tmp_path, FakeInterpreter([_assessment()]), FakePlanner(), controller)
    with mission_server(engine) as endpoint:
        store = SessionStore(tmp_path / "sessions", "one", {}, False)

        def fail_save() -> None:
            """Simulate durable storage failure before any request may leave the client."""
            raise OSError("injected disk failure")

        monkeypatch.setattr(store, "save", fail_save)
        try:
            with pytest.raises(OSError):
                MissionActions(ReadOnlyController(endpoint), store).command(
                    "create",
                    {
                        "schema_version": "roboguide.mission-request-create/v0.2",
                        "instruction": "test",
                        "submission_mode": "submit_when_ready",
                    },
                )
            assert engine._store.records() == () and controller.submissions == []
        finally:
            store.close()


def test_mission_discovery_comparison_diagnostics_and_watch(tmp_path: Path) -> None:
    """Composed reads retain original identities and partial diagnostic unavailability."""
    reader = ObservedController()
    store = SessionStore(tmp_path / "sessions", "one", {}, False)
    selected = router(ScriptedTransport([descriptor("mission", index=1)]))
    session = WorkbenchSession(
        reader,
        lambda: selected,
        store,
        MissionActions(ReadOnlyController("http://127.0.0.1:9"), store),
    )
    try:
        assert "m1" in session.ask("/missions")
        assert "Running" in session.ask("第一个任务呢？")
        assert "不可用" in session.ask("/explain #1")
        assert reader.reads[-3:] == ["mission:m1", "bindings:g1", "recovery:m1"]
        session.ask("/nodes")
        assert "其它合同" in session.ask("/compare")
        rendered: list[str] = []
        session.watch("m1", rendered.append, interval=0, samples=2)
        assert len(rendered) == 2 and reader.reads[-2:] == ["mission:m1", "mission:m1"]
        assert store.data["commands"] == []
    finally:
        store.close()


def test_old_public_create_route_refuses_new_intent_before_deliberation(tmp_path: Path) -> None:
    """The genuine prior HTTP decoder refuses new create fields before deliberation."""
    engine = _engine(tmp_path, FakeInterpreter([]), FakePlanner(), FakeController(_inventory()))
    code = original_source("mission/src/mission/api.py")
    previous = types.ModuleType("mission._previous_api")
    sys.modules[previous.__name__] = previous
    try:
        exec(compile(code, "original-api.py", "exec"), previous.__dict__)
        server = previous.MissionRequestHttpServer(("127.0.0.1", 0), engine, 1024 * 1024)
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
        thread.start()
        try:
            status, _ = _post(
                f"http://127.0.0.1:{server.server_port}",
                "/v1/mission-requests",
                {
                    "schema_version": "roboguide.mission-request-create/v0.2",
                    "instruction": "plan",
                    "submission_mode": "draft_only",
                },
                "old-refusal",
            )
            assert status == 400 and engine._store.records() == ()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
    finally:
        sys.modules.pop(previous.__name__, None)


def test_installed_cli_plan_resume_and_execute(tmp_path: Path) -> None:
    """Exercise CLI parsing, scoped disk restore and production HTTP together."""
    controller = FakeController(_inventory(*_fixture_contracts()))
    engine = _engine(tmp_path, FakeInterpreter([_assessment()]), FakePlanner(), controller)
    with mission_server(engine) as endpoint:
        base = [
            str(Path(sys.executable).parent / "roboguide"),
            "chat",
            "--mission-service",
            endpoint,
            "--session-dir",
            str(tmp_path / "sessions"),
        ]
        planned = subprocess.run(
            base + ["--session", "cli", "--ask", "/plan 只规划"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert planned.returncode == 0, planned.stderr
        assert "AwaitingExecution" in planned.stdout and controller.submissions == []
        resumed = subprocess.run(
            base + ["--resume", "cli"],
            input="/request\n/execute\n/quit\n",
            cwd=tmp_path,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        assert resumed.returncode == 0 and "Accepted" in resumed.stdout
        assert len(controller.submissions) == 1


def test_workbench_profile_relative_paths_and_explicit_override(tmp_path: Path) -> None:
    """Explicit CLI values override profile defaults; profile paths stay relative to their file."""
    profile = tmp_path / "workbench.toml"
    profile.write_text(
        '[workbench]\ncontroller="http://127.0.0.1:9000"\ntimeout=3\nconfig="model.toml"\n'
    )
    argv = ["chat", "--profile", str(profile), "--controller", "http://127.0.0.1:8000"]
    args = parser().parse_args(argv)
    apply_profile(args, argv)
    assert args.controller == "http://127.0.0.1:8000" and args.timeout == 3
    assert args.config == tmp_path / "model.toml"


def test_malformed_persisted_session_never_drops_pending_authority(tmp_path: Path) -> None:
    """A damaged command collection is refused instead of resetting to an empty session."""
    store = SessionStore(tmp_path / "sessions", "one", {}, False)
    path = store.path
    store.close()
    raw = json.loads(path.read_text())
    raw["commands"] = None
    path.write_text(json.dumps(raw))
    with pytest.raises(WorkbenchError, match="损坏"):
        SessionStore(tmp_path / "sessions", "one", {}, True)
