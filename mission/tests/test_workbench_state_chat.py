"""State tools through real HTTP/CLI, frozen display references and ordinary conversation."""

from __future__ import annotations

import io
import json
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from mission.models import JSONObject
from roboguide_workbench import state_queries, terminal
from roboguide_workbench.actions import MissionActions
from roboguide_workbench.agent_tools import ReadToolAdapter
from roboguide_workbench.cli import display_question, main
from roboguide_workbench.client import WorkbenchError
from roboguide_workbench.conversation import ConversationSession
from roboguide_workbench.persistence import SessionStore
from test_workbench_agent_loop import Responses, _agent, _call, _message, _no_read, _response
from test_workbench_dialogue import _router
from test_workbench_display_v4 import FailingOutput
from test_workbench_state import StateController


def session_for_state(tmp_path: Path, transport: Responses) -> ConversationSession:
    """Compose actual host State tools with private storage and a finite model script."""
    client = StateController()
    store = SessionStore(tmp_path / "sessions", "state-chat", {}, False)
    return ConversationSession(
        client, lambda: _router(transport), store, MissionActions(client, store)
    )


def test_native_device_queries_and_followups_use_fresh_host_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A factual model cannot overwrite read values or turn an old reply into a new sample."""
    transport = Responses(
        [
            _response(_call("read_state", {"target": "dog2", "fields": ["battery", "position"]})),
            _response(_message("电量为零，已经到了东门并安全停下。")),
            _response(_call("read_state", {"target": "dog2", "fields": ["posture"]}, "next")),
            _response(_message("它现在站立。")),
        ]
    )
    session = session_for_state(tmp_path, transport)
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    try:
        display_question(session, "dog2 还有多少电，在哪里？")
        first = cast(list[JSONObject], session.store.data["messages"])[-1]["text"]
        assert "82 %" in cast(str, first) and "warehouse" in cast(str, first)
        assert "东门" not in cast(str, first) and "电量为零" not in cast(str, first)
        assert session.last_state == "dog2" and session.displayed_missions == []
        display_question(session, "它的姿态呢？")
        assert "站立" in cast(
            str, cast(list[JSONObject], session.store.data["messages"])[-1]["text"]
        )
        assert isinstance(session.controller, StateController)
        assert session.controller.reads == ["providers", "inventory", "state"] * 2
        assert session.store.data["commands"] == [] and len(transport.requests) == 4
    finally:
        session.store.close()


def test_model_latency_cannot_extend_device_sample_freshness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The final displayed card rechecks original expiry after a slow model response."""
    clock = [100.0]
    monkeypatch.setattr(state_queries, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    class SlowModel(Responses):
        """Spend display latency only after the actual State reads completed."""

        def post_json(self, *args: object, **kwargs: object) -> JSONObject:
            """Preserve the finite transport while advancing the isolated State display clock."""
            if self.requests:
                clock[0] += 6
            return super().post_json(*args, **kwargs)  # type: ignore[arg-type]

    transport = SlowModel(
        [
            _response(_call("read_state", {"target": "dog2", "fields": ["battery"]})),
            _response(_message("当前电量 82%。")),
        ]
    )
    session = session_for_state(tmp_path, transport)
    try:
        answer = session.ask("dog2 还有多少电？")
        assert session.query_status == "failed" and "已过期" in answer and "电量：82" not in answer
        assert session.evidence[0]["complete"] is True
        assert cast(JSONObject, session.evidence[0]["consumption_check"])["complete"] is False
        assert session.store.data["commands"] == []
    finally:
        session.store.close()


@pytest.mark.parametrize("state_first", [True, False])
def test_mixed_state_and_mission_question_retains_both_host_answers(
    tmp_path: Path, state_first: bool
) -> None:
    """A four-GET mixed query keeps task progress beside the freshly checked device card."""
    calls = [
        _call("read_state", {"target": "dog2", "fields": ["battery"]}),
        _call("read_mission", {"mission_id": "m1"}, "mission"),
    ]
    transport = Responses(
        [
            _response(*(calls if state_first else list(reversed(calls)))),
            _response(_message("电量已满，任务成功完成。")),
        ]
    )
    session = session_for_state(tmp_path, transport)
    try:
        answer = session.ask("dog2 多少电，m1 任务进度如何？")
        assert "82 %" in answer and "m1" in answer and "Running" in answer
        assert "电量已满" not in answer and "成功完成" not in answer
        assert session.query_status == "complete" and session.store.data["commands"] == []
        assert (
            isinstance(session.controller, StateController) and len(session.controller.reads) == 4
        )
    finally:
        session.store.close()


def test_discovery_then_field_query_fits_original_tool_budget(tmp_path: Path) -> None:
    """One discovery plus three actual State GETs fit the existing four-read turn."""
    transport = Responses(
        [
            _response(_call("list_state_fields", {"target": ""})),
            _response(
                _call("read_state", {"target": "entity-dog2", "fields": ["battery"]}, "read")
            ),
            _response(_message("已读取设备电量。")),
        ]
    )
    session = session_for_state(tmp_path, transport)
    try:
        result = session.ask("有哪些设备可以查电量？请查询其中的设备。")
        assert "82 %" in result and session.query_status == "complete"
        assert isinstance(session.controller, StateController)
        assert session.controller.reads == ["providers", "providers", "inventory", "state"]
        assert session.displayed_states == ["entity-dog2"]
    finally:
        session.store.close()


def test_two_state_reads_cannot_bypass_the_four_get_bound(tmp_path: Path) -> None:
    """A model batch with six underlying GETs is refused before the first source read."""
    session = session_for_state(tmp_path, Responses([]))
    adapter = ReadToolAdapter(session, "dog2", session.query_references())
    transport = Responses(
        [
            _response(
                _call("read_state", {"target": "dog2", "fields": ["battery"]}),
                _call("read_state", {"target": "dog2", "fields": ["posture"]}, "second"),
            )
        ]
    )
    try:
        result = _agent(transport).answer(
            "dog2 状态", {}, [], _no_read, tool_definitions=adapter.definitions()
        )
        assert result.status == "failed" and result.evidence == []
    finally:
        session.store.close()


def test_unprovided_device_target_refuses_before_any_get(tmp_path: Path) -> None:
    """The generic endpoint does not allow invented IDs, arbitrary paths or URLs."""
    session = session_for_state(tmp_path, Responses([]))
    adapter = ReadToolAdapter(session, "它在哪里？", session.query_references())
    try:
        with pytest.raises(WorkbenchError, match="依据"):
            adapter.read("read_state", {"target": "invented", "fields": ["battery"]}, 5)
        assert isinstance(session.controller, StateController) and session.controller.reads == []
    finally:
        session.store.close()


def test_state_projection_preserves_newer_node_and_mission_selection(tmp_path: Path) -> None:
    """A queued device read publishes its own focus without restoring stale unrelated references."""
    session = session_for_state(tmp_path, Responses([]))
    session.displayed_nodes, session.last_node = ["old-node"], "old-node"
    session.displayed_missions, session.last_mission = ["old-mission"], "old-mission"
    original = session.query_references()
    session.displayed_nodes, session.last_node = ["new-node"], "new-node"
    session.displayed_missions, session.last_mission = ["new-mission"], "new-mission"
    adapter = ReadToolAdapter(session, "dog2", original)
    try:
        result = adapter.read("read_state", {"target": "dog2", "fields": ["battery"]}, 5)
        evidence: list[JSONObject] = [{"components": list(result.components)}]
        adapter.publish(evidence)
        assert session.displayed_nodes == ["new-node"] and session.last_node == "new-node"
        assert (
            session.displayed_missions == ["new-mission"] and session.last_mission == "new-mission"
        )
        assert session.last_state == "dog2"
    finally:
        session.store.close()


def test_failed_state_display_preserves_visible_selection_after_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Hidden State reads retain their audit but cannot replace the previously visible device."""
    session = session_for_state(tmp_path, Responses([]))
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    try:
        display_question(session, "/state-fields")
        assert session.last_state == "entity-dog2"
        monkeypatch.setattr(sys, "stdout", FailingOutput("write", "dog2 · 设备状态"))
        with pytest.raises(OSError):
            display_question(session, "/state dog2 电量")
        assert session.last_state == "entity-dog2" and session.displayed_states == ["entity-dog2"]
        saved = json.loads(session.store.path.read_text())
        assert saved["context"]["last_state"] == "entity-dog2"
        assert session.store.data["commands"] == []
    finally:
        session.store.close()


@contextmanager
def state_server(status: int = 200) -> Iterator[tuple[str, list[tuple[str, str, str | None]]]]:
    """Serve production-shaped State and inventory JSON through actual bounded HTTP GETs."""
    client = StateController()
    calls: list[tuple[str, str, str | None]] = []
    responses = {
        "/v1/state/providers": client.catalog,
        "/v1/inventory": client.current_inventory,
        "/v1/state/records?semantic=reported": client.current_state,
    }

    class Handler(BaseHTTPRequestHandler):
        """Accept only explicit read routes; retain any attempted writes as test failures."""

        def do_GET(self) -> None:
            """Preserve the configured tenant and exact route in original captured evidence."""
            calls.append(("GET", self.path, self.headers.get("X-RoboGuide-Tenant")))
            body = json.dumps(responses.get(self.path, {})).encode()
            self.send_response(status if self.path in responses else 404)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:
            """Expose accidental effects instead of accepting a synthesized task response."""
            calls.append(("POST", self.path, None))
            self.send_error(405)

        def log_message(self, format: str, *args: object) -> None:
            """Keep fixture access logging out of actual CLI result streams."""

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("command", ["state", "chat", "state-fields"])
def test_actual_cli_reads_state_without_model_or_mission(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], command: str
) -> None:
    """Both command entry points and discovery preserve the same tenant-scoped GET-only path."""
    with state_server() as (origin, calls):
        arguments = [
            command,
            "--controller",
            origin,
            "--tenant",
            "fixture",
            "--session-dir",
            str(tmp_path / "sessions"),
        ]
        arguments += (
            ["--ask", "/state dog2 电量 位置", "--config", str(tmp_path / "no-model.toml")]
            if command == "chat"
            else ["dog2", "battery", "position"]
            if command == "state"
            else []
        )
        assert main(arguments) == 0
    output = capsys.readouterr().out
    assert all(method == "GET" and tenant == "fixture" for method, _, tenant in calls)
    assert len(calls) == (1 if command == "state-fields" else 3)
    assert ("entity-dog2" if command == "state-fields" else "82 %") in output


def test_help_and_empty_watch_interaction_explain_the_next_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The quick guide describes user tasks and model-free State commands without version menus."""
    session = session_for_state(tmp_path, Responses([]))
    try:
        help_text = session.ask("/help")
        assert "/state-fields" in help_text and "/state ID 电量 位置" in help_text
        assert "第四版" not in help_text and "第三版" not in help_text
        assert terminal._is_queueable("/state") is False
        assert terminal._is_queueable("/state #2 电量") is False
        assert terminal._is_queueable("/state dog2 电量") is True
    finally:
        session.store.close()


def test_state_query_http_refusal_is_nonzero_and_does_not_retry(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An actual permission refusal returns Unknown without another GET or a replacement task."""
    with state_server(403) as (origin, calls):
        status = main(
            [
                "chat",
                "--ask",
                "/state dog2 电量",
                "--output-format",
                "stream-json",
                "--controller",
                origin,
                "--session-dir",
                str(tmp_path / "sessions"),
                "--config",
                str(tmp_path / "no-model.toml"),
            ]
        )
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert status == 2 and len(calls) == 1 and events[-1]["type"] == "turn.failed"
    assert "403" in events[-1]["message"]


def test_empty_watch_messages_and_command_completion_are_actionable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty status and unknown watches explain a usable next action without raw empty labels."""
    from test_workbench_display import ScriptedPrompt, command

    session = session_for_state(tmp_path, Responses([]))
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    prompt = ScriptedPrompt([command("/watches"), command("/unwatch missing"), command("/quit")])
    prompt.install(monkeypatch)
    try:
        import asyncio

        assert asyncio.run(terminal.interactive(session)) == 0
        assert "当前没有任务跟踪" in output.getvalue() and "/watch MissionID" in output.getvalue()
        assert "没有正在跟踪这个任务" in output.getvalue() and "/state-fields" in terminal.COMMANDS
        assert isinstance(session.controller, StateController) and session.controller.reads == []
    finally:
        session.store.close()
