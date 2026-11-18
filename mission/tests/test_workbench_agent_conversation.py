"""Continuous model conversations through host reads, display adoption and approvals."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from typing import cast

import pytest
from mission.models import JSONObject
from roboguide_workbench.actions import MissionActions
from roboguide_workbench.cli import display_question, main
from roboguide_workbench.conversation import ConversationSession
from roboguide_workbench.persistence import SessionStore
from roboguide_workbench.provider_config import model_settings
from roboguide_workbench.routing import ResponsesRouter
from test_workbench import http_fixture
from test_workbench_agent_loop import Responses, _call, _message, _response
from test_workbench_display import workbench
from test_workbench_v2 import ObservedController


def session_for(tmp_path: Path, transport: Responses) -> ConversationSession:
    """Create the default native conversation with a finite model and read-only Controller."""
    controller = ObservedController()
    store = SessionStore(tmp_path / "sessions", "native", {}, False)
    settings = model_settings(
        {"base_url": "http://127.0.0.1:9/v1", "name": "fixture", "requires_auth": False}
    )
    router = ResponsesRouter(settings, {}, transport)
    return ConversationSession(controller, lambda: router, store, MissionActions(controller, store))


def test_continuous_chat_and_live_query_are_chosen_by_the_model(tmp_path: Path) -> None:
    """Casual wording, a mixed query and its follow-up use one unchanged conversation path."""
    replies = ["你好！", "不客气。", "好呀。", "想聊点什么？"]
    steps: list[JSONObject | Exception] = [_response(_message(text)) for text in replies]
    steps.extend(
        [
            _response(_call("read_cluster", call_id="inventory")),
            _response(_message("目前有两个节点 a 和 b，b 支持建图。")),
            _response(_call("read_node", {"node_id": "b"}, "details")),
            _response(_message("b 提供建图合同。")),
        ]
    )
    transport = Responses(steps)
    session = session_for(tmp_path, transport)
    try:
        for question, answer in zip(["你好", "多谢啦", "好呀", "我想聊聊"], replies, strict=True):
            assert session.ask(question) == answer
            assert session.evidence == []
        assert isinstance(session.controller, ObservedController)
        assert session.controller.reads == [] and len(transport.requests) == 4
        result = session.ask("你好，帮我查下接入的节点")
        assert "两个节点" in result and "#1 a" in result and "#2 b" in result
        assert session.controller.reads == ["inventory"]
        assert "b 提供建图合同" in session.ask("第二个呢？")
        assert session.last_node == "b" and session.controller.reads == ["inventory", "inventory"]
        assert session.store.data["commands"] == []
        context = cast(JSONObject, session.store.data["context"])
        assert isinstance(context["agent_turns"], list)
        assert '"type": "function_call_output"' in json.dumps(context["agent_turns"])
        assert "模型解释" not in result and "[E1]" not in result
    finally:
        session.store.close()


def test_native_read_display_failure_preserves_original_visible_references(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A successful hidden read never becomes the durable ordinal list after failed output."""
    transport = Responses(
        [_response(_call("read_node", {"node_id": "b"})), _response(_message("查询结果 b"))]
    )
    session = session_for(tmp_path, transport)
    try:
        session.ask("/nodes")

        class BrokenOutput(io.StringIO):
            """Reject the actual assistant output independently of a successful Controller read."""

            def write(self, text: str) -> int:
                """Expose a write failure before the display can acknowledge new references."""
                if "查询结果 b" in text:
                    raise BrokenPipeError("injected display failure")
                return super().write(text)

        monkeypatch.setattr(sys, "stdout", BrokenOutput())
        with pytest.raises(BrokenPipeError):
            display_question(session, "看看第二个节点")
        assert session.displayed_nodes == ["a", "b"] and session.last_node is None
        saved = json.loads(session.store.path.read_text())
        assert saved["context"]["nodes"] == ["a", "b"]
        assert session.shown_draft is None and session.suggestion is None
    finally:
        session.store.close()


def test_partial_recovery_keeps_failed_source_and_current_mission_facts(tmp_path: Path) -> None:
    """A missing original recovery view cannot become a generated all-clear conclusion."""
    transport = Responses(
        [
            _response(_call("read_explanation", {"mission_id": "m1"})),
            _response(_message("m1 没有任何恢复阻塞，可以继续执行。")),
        ]
    )
    session = session_for(tmp_path, transport)
    try:
        answer = session.ask("检查 m1 是否有恢复阻塞")
        assert session.query_status == "partial" and "Running" in answer
        assert "原恢复评估不可用" in answer and "未知" in answer
        assert "没有任何恢复阻塞" not in answer and "可以继续执行" not in answer
        assert session.last_mission == "m1" and session.store.data["commands"] == []
        assert isinstance(session.controller, ObservedController)
        assert session.controller.reads == ["mission:m1", "bindings:g1", "recovery:m1"]
    finally:
        session.store.close()


def test_model_proposal_requires_displayed_explicit_plan_and_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Native proposals cannot submit a Mission before the original command confirmation."""
    transport = Responses([_response(_call("propose_action", {"action": "run"}))])
    with workbench(tmp_path) as (session, controller):
        settings = model_settings(
            {"base_url": "http://127.0.0.1:9/v1", "name": "fixture", "requires_auth": False}
        )
        session.router_factory = lambda: ResponsesRouter(settings, {}, transport)
        monkeypatch.setattr(sys, "stdout", io.StringIO())
        display_question(session, "帮我执行巡检")
        assert controller.submissions == [] and session.suggestion == ("run", "帮我执行巡检")
        display_question(session, "/choose plan")
        assert controller.submissions == [] and session.shown_draft is not None
        display_question(session, "/execute")
        assert len(controller.submissions) == 1


def test_session_resume_keeps_natural_history_but_no_tool_or_action_is_replayed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resuming retains public tool audit and text while querying only when the model asks."""
    transport = Responses([_response(_message("你好！"))])
    session = session_for(tmp_path, transport)
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    display_question(session, "你好")
    controller, directory = session.controller, session.store.path.parent
    session.store.close()
    resumed_store = SessionStore(directory, "native", {}, True)
    again = Responses([_response(_message("不客气！"))])
    settings = model_settings(
        {"base_url": "http://127.0.0.1:9/v1", "name": "fixture", "requires_auth": False}
    )
    resumed = ConversationSession(
        controller,
        lambda: ResponsesRouter(settings, {}, again),
        resumed_store,
        MissionActions(controller, resumed_store),
    )
    try:
        assert resumed.ask("谢谢") == "不客气！"
        supplied = cast(list[JSONObject], again.requests[0]["input"])
        assert any(item.get("content") == "你好！" for item in supplied)
        assert resumed.shown_draft is None and resumed.suggestion is None
        assert isinstance(controller, ObservedController) and controller.reads == []
    finally:
        resumed_store.close()


def test_stream_json_is_native_chat_events_not_forced_model_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A real Provider HTTP call yields machine-readable lifecycle events for a free-text answer."""
    with http_fixture(json.dumps(_response(_message("你好，今天怎么样？"))).encode()) as (
        url,
        calls,
    ):
        model = tmp_path / "model.toml"
        model.write_text(f'[model]\nbase_url="{url}/v1"\nname="fixture"\nrequires_auth=false\n')
        status = main(
            [
                "chat",
                "--ask",
                "你好",
                "--output-format",
                "stream-json",
                "--config",
                str(model),
                "--session-dir",
                str(tmp_path / "sessions"),
            ]
        )
    assert status == 0 and len(calls) == 1
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert events[0]["type"] == "turn.started" and events[-1]["type"] == "turn.completed"
    assert (
        next(event for event in events if event["type"] == "assistant.completed")["text"]
        == "你好，今天怎么样？"
    )


@pytest.mark.parametrize("question", ["/not-a-command", "/execute", "你好", "/nodes"])
def test_stream_json_errors_end_the_original_turn_without_replaying_io(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], question: str
) -> None:
    """Refused commands, missing models and HTTP failures yield a single JSON failure event."""
    with http_fixture(b"{}", 403) as (url, calls):
        status = main(
            [
                "chat",
                "--ask",
                question,
                "--output-format",
                "stream-json",
                "--controller",
                url,
                "--config",
                str(tmp_path / "missing-model.toml"),
                "--session-dir",
                str(tmp_path / "sessions"),
                "--session",
                "failure",
            ]
        )
    captured = capsys.readouterr()
    events = [json.loads(line) for line in captured.out.splitlines()]
    assert status == 2 and events[0]["type"] == "turn.started"
    assert events[-1]["type"] == "turn.failed" and events[-1]["status"] == "failed"
    assert events[-1]["message"] and "查询失败" in captured.err
    assert sum(event["type"] in {"turn.completed", "turn.failed"} for event in events) == 1
    assert len(calls) == (1 if question == "/nodes" else 0)
    saved = json.loads((tmp_path / "sessions/failure.json").read_text())
    assert saved["commands"] == []


def test_stream_json_interrupt_ends_the_turn_with_original_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An interrupted one-shot turn reports failure without implying cancellation or retry."""

    def interrupt(_session: ConversationSession, _question: str) -> str:
        """Interrupt before any model or Controller call has been admitted."""
        raise KeyboardInterrupt

    monkeypatch.setattr(ConversationSession, "ask", interrupt)
    status = main(
        [
            "chat",
            "--ask",
            "你好",
            "--output-format",
            "stream-json",
            "--config",
            str(tmp_path / "missing-model.toml"),
            "--session-dir",
            str(tmp_path / "sessions"),
        ]
    )
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert status == 130 and events[0]["type"] == "turn.started"
    assert events[-1]["type"] == "turn.failed" and "中断" in events[-1]["message"]


@pytest.mark.parametrize("command", ["new", "resume"])
def test_stream_json_navigation_keeps_the_event_protocol_and_new_session_identity(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], command: str
) -> None:
    """A local session switch completes as JSON and reports the actual resulting session."""
    if command == "resume":
        assert (
            main(
                [
                    "chat",
                    "--ask",
                    "/help",
                    "--config",
                    str(tmp_path / "missing-model.toml"),
                    "--session-dir",
                    str(tmp_path / "sessions"),
                    "--session",
                    "after",
                ]
            )
            == 0
        )
        capsys.readouterr()
    status = main(
        [
            "chat",
            "--ask",
            f"/{command} after",
            "--output-format",
            "stream-json",
            "--config",
            str(tmp_path / "missing-model.toml"),
            "--session-dir",
            str(tmp_path / "sessions"),
            "--session",
            "before",
        ]
    )
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert status == 0 and events[0]["type"] == "turn.started"
    assert events[-1] == {"type": "turn.completed", "status": "complete", "session_id": "after"}
    assert "已切换会话：after" in events[-2]["text"]


def test_stream_json_partial_read_keeps_a_nonzero_completed_outcome(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Useful Mission facts with unavailable diagnostics complete once with a partial status."""
    raw = json.dumps(ObservedController().mission("m1")).encode()
    with http_fixture(raw) as (url, calls):
        status = main(
            [
                "chat",
                "--ask",
                "/explain m1",
                "--output-format",
                "stream-json",
                "--controller",
                url,
                "--config",
                str(tmp_path / "missing-model.toml"),
                "--session-dir",
                str(tmp_path / "sessions"),
            ]
        )
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert status == 2 and len(calls) == 3
    assert events[-1] == {"type": "turn.completed", "status": "partial"}
    assert "Running" in events[-2]["text"] and "不可用" in events[-2]["text"]
    assert not any(event["type"] == "turn.failed" for event in events)


def test_stream_json_post_command_error_preserves_the_original_receipt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A failed local checkpoint reports JSON failure without replaying a real Request POST."""
    checkpoint = ConversationSession.checkpoint
    failed = False

    def fail_once(session: ConversationSession) -> None:
        """Fail only after the real server response and original receipt have been persisted."""
        nonlocal failed
        if session.request_id is not None and not failed:
            failed = True
            raise OSError("injected post-command checkpoint failure")
        checkpoint(session)

    monkeypatch.setattr(ConversationSession, "checkpoint", fail_once)
    with workbench(tmp_path) as (session, controller):
        status = main(
            [
                "chat",
                "--ask",
                "/plan 检查仓库",
                "--output-format",
                "stream-json",
                "--mission-service",
                session.actions.connection.endpoint,
                "--config",
                str(tmp_path / "missing-model.toml"),
                "--session-dir",
                str(tmp_path / "sessions"),
                "--session",
                "receipt",
            ]
        )
        assert controller.submissions == []
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert failed and status == 2 and events[-1]["type"] == "turn.failed"
    assert "post-command checkpoint failure" in events[-1]["message"]
    assert sum(event["type"] in {"turn.completed", "turn.failed"} for event in events) == 1
    saved = json.loads((tmp_path / "sessions/receipt.json").read_text())
    assert len(saved["commands"]) == 1 and saved["commands"][0]["state"] == "complete"
    assert saved["commands"][0]["request_id"] == saved["context"]["request_id"]
