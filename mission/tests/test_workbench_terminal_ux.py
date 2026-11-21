"""Terminal readability improvements retain exact plan, display and single-writer boundaries."""

from __future__ import annotations

import asyncio
import io
import sys
import threading
from pathlib import Path
from typing import cast

import pytest
import yaml
from mission.models import JSONObject
from prompt_toolkit.utils import get_cwidth
from roboguide_workbench import terminal
from roboguide_workbench.actions import MissionActions
from roboguide_workbench.cli import display_question
from roboguide_workbench.conversation import ConversationSession
from roboguide_workbench.persistence import SessionStore
from roboguide_workbench.plan_view import plan_document
from test_workbench_agent_loop import Responses, _call, _message, _response
from test_workbench_dialogue import _router
from test_workbench_display import ScriptedPrompt, command, drain_foreground, workbench
from test_workbench_display_v4 import FailingOutput


def test_readable_plan_includes_the_entire_original_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every canonical field survives the readable view before execution acknowledgement."""
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    with workbench(tmp_path) as (session, controller):
        display_question(session, "/plan 只规划检查仓库")
        assert session.shown_draft is not None and session.acknowledged_draft is not None
        plan = cast(JSONObject, session.shown_draft["plan"])
        text = cast(str, cast(list[JSONObject], session.store.data["messages"])[-1]["text"])
        document = plan_document(plan)
        assert yaml.safe_load(document) == plan and document in text
        assert "规划完成，尚未执行" in text and "计划概览：" in text
        assert "完整计划" in text and "contexts:" in text and "tasks:" in text
        assert controller.submissions == []


@pytest.mark.parametrize("acknowledged", [False, True])
def test_only_acknowledged_same_version_progress_can_be_compact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, acknowledged: bool
) -> None:
    """A prior worker result without actual output ACK never suppresses complete plan display."""
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    with workbench(tmp_path) as (session, controller):
        if acknowledged:
            display_question(session, "/plan 检查仓库")
        else:
            session.ask("/plan 检查仓库")
        first = cast(str, cast(list[JSONObject], session.store.data["messages"])[-1]["text"])
        display_question(session, "/request")
        latest = cast(str, cast(list[JSONObject], session.store.data["messages"])[-1]["text"])
        if acknowledged:
            assert "计划内容未变" in latest and "完整计划" not in latest
            assert len(latest) < len(first) / 2
        else:
            assert "完整计划" in latest and "计划内容未变" not in latest
        assert len(cast(list[JSONObject], session.store.data["commands"])) == 1
        assert controller.submissions == []


def test_new_revision_expands_and_other_requests_do_not_show_a_fake_version_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Version comparison stays within a Request and every amended revision is shown completely."""
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    with workbench(tmp_path, assessments=3) as (session, _controller):
        display_question(session, "/plan 第一个任务")
        display_question(session, "/answer 保持原方案并记录补充")
        revised = cast(str, cast(list[JSONObject], session.store.data["messages"])[-1]["text"])
        assert "完整计划" in revised and "版本变化：1 → 2" in revised
        display_question(session, "/plan 第二个任务")
        switched = cast(str, cast(list[JSONObject], session.store.data["messages"])[-1]["text"])
        assert "完整计划" in switched and "版本变化" not in switched


def test_failed_display_cannot_leave_a_compact_plan_ack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A partial write revokes confirmation and compact display on the next query."""
    with workbench(tmp_path) as (session, controller):
        monkeypatch.setattr(sys, "stdout", FailingOutput("write", "完整计划"))
        with pytest.raises(OSError):
            display_question(session, "/plan 检查仓库")
        assert session.shown_draft is None and session.acknowledged_draft is None
        monkeypatch.setattr(sys, "stdout", io.StringIO())
        display_question(session, "/request")
        answer = cast(str, cast(list[JSONObject], session.store.data["messages"])[-1]["text"])
        assert "完整计划" in answer and "计划内容未变" not in answer
        assert len(cast(list[JSONObject], session.store.data["commands"])) == 1
        assert controller.submissions == []


def test_resume_does_not_restore_compact_plan_permission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """History cannot suppress the first complete plan display after restart."""
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    with workbench(tmp_path) as (session, controller):
        display_question(session, "/plan 检查仓库")
        session.store.close()
        store = SessionStore(tmp_path / "sessions", "display", {}, True)
        resumed = ConversationSession(
            session.controller,
            session.router_factory,
            store,
            MissionActions(session.actions.connection, store),
        )
        try:
            assert resumed.shown_draft is None and resumed.acknowledged_draft is None
            display_question(resumed, "/request")
            answer = cast(str, cast(list[JSONObject], store.data["messages"])[-1]["text"])
            assert "完整计划" in answer and "计划内容未变" not in answer
            assert controller.submissions == []
        finally:
            store.close()


def test_natural_plan_expansion_is_local_and_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The model can expand parameters naturally without a slash command or another HTTP query."""
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    with workbench(tmp_path) as (session, controller):
        display_question(session, "/plan 检查仓库")

        def unexpected_get(target: str) -> JSONObject:
            """Fail the test if an already displayed immutable plan is silently refreshed."""
            raise AssertionError("unexpected Request refresh: " + target)

        monkeypatch.setattr(session.actions, "get", unexpected_get)
        transport = Responses(
            [_response(_call("show_plan", {})), _response(_message("完整参数已展开。"))]
        )
        session.router_factory = lambda: _router(transport)
        display_question(session, "展开当前计划的完整参数。")
        answer = cast(str, cast(list[JSONObject], session.store.data["messages"])[-1]["text"])
        assert "完整原始计划（未刷新执行状态）" in answer
        assert session.shown_draft is not None
        assert plan_document(cast(JSONObject, session.shown_draft["plan"])) in answer
        assert len(cast(list[JSONObject], session.store.data["commands"])) == 1
        assert controller.submissions == []


def test_unsafe_formatting_characters_remain_exact_visible_plan_values() -> None:
    """Terminal sanitization cannot turn an invisible YAML scalar into a different value."""
    value: JSONObject = {"goal": "中文\u202e隐藏", "flag": "yes", "empty": "null", "count": 0}
    text = plan_document(value)
    assert yaml.safe_load(text) == value and "\u202e" not in text and "\\u202e" in text


def test_busy_controls_and_waiting_feedback_keep_one_foreground_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Local controls and readable queue feedback never dispatch a second foreground writer."""
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    monkeypatch.setattr(terminal, "SLOW_NOTICE_DELAYS", (0.0,))
    entered, release = threading.Event(), threading.Event()
    question = "了解节点能力\n以及支持的合同"
    with workbench(tmp_path) as (session, controller):
        transport = Responses([_response(_message("可以继续了解设备能力。"))])
        session.router_factory = lambda: _router(transport)
        original = session.ask
        calls: list[str] = []

        def held(text: str) -> str:
            """Keep the actual first query pending while testing local terminal controls."""
            calls.append(text)
            result = original(text)
            if text == "/nodes":
                entered.set()
                assert release.wait(5)
            return result

        monkeypatch.setattr(session, "ask", held)

        async def queue() -> str:
            """Enter one multiline original while the first real read remains undisplayed."""
            assert await asyncio.to_thread(entered.wait, 5)
            return question

        async def finish() -> str:
            """Check immediate local output, then drain the original and queued turns once."""
            text = output.getvalue()
            assert "任务与规划：" in text and "已等待" in text
            assert "待发消息：\n1." in text and "待发消息：\\u000a" not in text
            assert calls == ["/nodes"]
            release.set()
            await drain_foreground()
            await drain_foreground()
            return "/quit"

        prompt = ScriptedPrompt(
            [
                command("/nodes"),
                queue,
                command("/queue"),
                command("/status"),
                command("/help"),
                finish,
            ]
        )
        prompt.install(monkeypatch)
        assert asyncio.run(terminal.interactive(session)) == 0
        assert calls == ["/nodes", question]
        assert "待发问题：了解节点能力 以及支持的合同" in output.getvalue()
        assert output.getvalue().count("仍在处理") <= 2
        assert session.store.data["commands"] == [] and controller.submissions == []


def test_partial_reply_has_a_visible_turn_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A sibling read failure is labelled as a partial reply rather than a robot failure."""
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    transport = Responses(
        [
            _response(
                _call("read_cluster", {}), _call("read_mission", {"mission_id": "missing"}, "m")
            ),
            _response(_message("节点查询成功，missing 的任务状态不可用。")),
        ]
    )
    with workbench(tmp_path) as (session, controller):
        session.router_factory = lambda: _router(transport)

        def missing(target: str) -> JSONObject:
            """Make only the named Mission read unavailable."""
            raise OSError("fixture Mission unavailable: " + target)

        monkeypatch.setattr(session.controller, "mission", missing)

        async def finish() -> str:
            """Wait for the actual partial response and display acknowledgement."""
            await drain_foreground()
            return "/quit"

        prompt = ScriptedPrompt([command("查询节点并查看 missing 的任务状态。"), finish])
        prompt.install(monkeypatch)
        assert asyncio.run(terminal.interactive(session)) == 0
        assert "── RG · 助手 · 部分结果" in output.getvalue()
        assert "本轮结束" not in output.getvalue()
        assert session.query_status == "partial" and controller.submissions == []


def test_pending_preview_fits_chinese_terminal_cells() -> None:
    """A narrow composer bounds its preview while retaining the complete multiline original."""
    original = "第一行中文设备状态\n第二行还有更多内容" * 4
    preview = terminal._message_preview(original, 18)
    assert get_cwidth(preview) <= 18 and preview.endswith("…")
    assert "\n" not in preview and "\\u000a" not in preview


def test_local_snapshot_commands_preserve_the_conversation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Render the original snapshots without adding them to persisted dialogue."""
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    with workbench(tmp_path) as (session, controller):
        display_question(session, "/nodes")
        before = session.store.path.read_bytes()
        for _ in range(3):
            display_question(session, "/history")
            display_question(session, "/raw")
        assert session.store.path.read_bytes() == before
        assert session.previous_question == "/nodes" and session.query_status == "complete"
        assert "历史查询" in output.getvalue() and '"text": "/nodes"' in output.getvalue()
        assert controller.submissions == []
