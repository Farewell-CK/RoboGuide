"""Original task dialogue and actual display acknowledgement across conversation boundaries."""

from __future__ import annotations

import asyncio
import io
import json
import sys
import threading
from pathlib import Path
from typing import cast

import pytest
from mission.models import JSONObject
from roboguide_workbench import terminal
from roboguide_workbench.actions import MissionActions
from roboguide_workbench.answers import bounded_history
from roboguide_workbench.cli import display_question
from roboguide_workbench.conversation import ConversationSession
from roboguide_workbench.display import DisplayTurn
from roboguide_workbench.persistence import SessionStore
from roboguide_workbench.provider_config import model_settings
from roboguide_workbench.routing import ResponsesRouter
from test_workbench_agent_conversation import session_for
from test_workbench_agent_loop import Responses, _call, _message, _response
from test_workbench_display import ScriptedPrompt, command, drain_foreground, workbench
from test_workbench_display_v4 import FailingOutput, ReorderingController


def _router(transport: Responses) -> ResponsesRouter:
    """Keep model replies finite and avoid any external inference or credentials."""
    settings = model_settings(
        {"base_url": "http://127.0.0.1:9/v1", "name": "fixture", "requires_auth": False}
    )
    return ResponsesRouter(settings, {}, transport)


@pytest.mark.parametrize("resume", [False, True])
def test_multiturn_confirmation_submits_the_displayed_original_dialogue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, resume: bool
) -> None:
    """An actual Request POST retains the original task, limits and clarification."""
    first = "让 dog2 巡检仓库，速度不要超过每秒 0.2 米。"
    clarification = "请问要巡检哪个区域？"
    last = "东区，先只规划。"
    transport = Responses(
        [
            _response(_message(clarification)),
            _response(_call("propose_action", {"action": "plan", "start_turn": "u1"})),
        ]
    )
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    with workbench(tmp_path) as (session, controller):
        session.router_factory = lambda: _router(transport)
        display_question(session, first)
        if resume:
            session.store.close()
            store = SessionStore(tmp_path / "sessions", "display", {}, True)
            session = ConversationSession(
                session.controller,
                lambda: _router(transport),
                store,
                MissionActions(session.actions.connection, store),
            )
        try:
            display_question(session, last)
            proposal = session.suggestion
            assert proposal is not None
            instruction = proposal[1]
            assert first in instruction and last in instruction and clarification in instruction
            assert first in output.getvalue() and last in output.getvalue()
            assert session.store.data["commands"] == [] and controller.submissions == []
            display_question(session, "/choose plan")
            commands = cast(list[JSONObject], session.store.data["commands"])
            assert len(commands) == 1 and commands[0]["state"] == "complete"
            assert cast(JSONObject, commands[0]["body"])["instruction"] == instruction
            assert cast(JSONObject, commands[0]["body"])["submission_mode"] == "draft_only"
            assert session.shown_draft is not None and controller.submissions == []
        finally:
            if resume:
                session.store.close()


@pytest.mark.parametrize("start", ["unknown", "a1", "", "u2"])
def test_unavailable_dialogue_sources_never_create_a_confirmable_proposal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, start: str
) -> None:
    """Invented sources, Assistant identities and absent user turns cannot supply a task."""
    transport = Responses(
        [
            _response(_message("要哪个区域？")),
            _response(_call("propose_action", {"action": "plan", "start_turn": start})),
        ]
    )
    session = session_for(tmp_path, transport)
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    try:
        display_question(session, "巡检仓库")
        display_question(session, "东区")
        assert session.suggestion is None and session.query_status == "failed"
        assert session.store.data["commands"] == []
    finally:
        session.store.close()


def test_unrelated_task_is_not_carried_into_a_new_current_proposal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Choosing the current user turn leaves earlier unrelated requests out of the body."""
    transport = Responses(
        [
            _response(_message("知道了。")),
            _response(_call("propose_action", {"action": "plan", "start_turn": "current"})),
        ]
    )
    session = session_for(tmp_path, transport)
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    try:
        display_question(session, "刚才说的仓库巡检暂时放下。")
        display_question(session, "只规划 dog1 的一次拍照。")
        assert session.suggestion == ("plan", "只规划 dog1 的一次拍照。")
    finally:
        session.store.close()


@pytest.mark.parametrize("hidden", ["mute", "write", "flush", "checkpoint", "crash"])
def test_unacknowledged_reply_stays_in_audit_but_out_of_live_and_resumed_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hidden: str
) -> None:
    """Only a complete display ACK may expose an Assistant reply to a subsequent Provider input."""
    hidden_text = "隐藏顺序：#1 b；#2 a。"
    transport = Responses(
        [
            _response(_call("read_cluster")),
            _response(_message(hidden_text)),
            _response(_message("按已显示的列表回答。")),
            _response(_message("恢复后仍按已显示的列表回答。")),
        ]
    )
    reader = ReorderingController()
    store = SessionStore(tmp_path / "sessions", "history", {}, False)
    session = ConversationSession(
        reader, lambda: _router(transport), store, MissionActions(reader, store)
    )
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    try:
        display_question(session, "/nodes")
        turn = DisplayTurn(session)
        result = session.ask("刷新节点")
        turn.stage()
        assert hidden_text in result
        if hidden == "checkpoint":
            checkpoint = session.checkpoint

            def fail_ack() -> None:
                """Refuse the ACK before it can persist a visible message marker."""
                raise OSError("injected ACK checkpoint failure")

            monkeypatch.setattr(session, "checkpoint", fail_ack)
            with pytest.raises(OSError):
                turn.acknowledge()
            monkeypatch.setattr(session, "checkpoint", checkpoint)
        elif hidden in {"write", "flush"}:
            stream = FailingOutput(hidden, hidden_text)
            with pytest.raises(OSError):
                print(result, file=stream, flush=True)
        if hidden != "crash":
            turn.reject()
            assert session.displayed_nodes == ["a", "b"]
            display_question(session, "第二个节点呢？")
            assert hidden_text not in json.dumps(transport.requests[-1], ensure_ascii=False)
        saved = json.loads(session.store.path.read_text())
        assert hidden_text in json.dumps(saved["messages"], ensure_ascii=False)
        hidden_rows = [row for row in saved["messages"] if hidden_text in str(row.get("text"))]
        assert len(hidden_rows) == 1 and hidden_rows[0]["displayed"] is False
    finally:
        store.close()
    resumed_store = SessionStore(tmp_path / "sessions", "history", {}, True)
    resumed = ConversationSession(
        reader, lambda: _router(transport), resumed_store, MissionActions(reader, resumed_store)
    )
    try:
        display_question(resumed, "恢复后第二个节点呢？")
        supplied = json.dumps(transport.requests[-1]["input"], ensure_ascii=False)
        assert hidden_text not in supplied and "1. a" in supplied
        assert resumed.displayed_nodes == ["a", "b"]
    finally:
        resumed_store.close()


def test_old_history_without_display_proof_is_not_assumed_visible() -> None:
    """Legacy audit rows and malformed display flags do not invent an actual output ACK."""
    history = bounded_history(
        [
            {"role": "user", "text": "原输入"},
            {"role": "assistant", "text": "旧回复"},
            {"role": "assistant", "text": "假标记", "displayed": "true"},
            {"role": "assistant", "text": "显示成功", "displayed": True},
        ]
    )
    assert [row["text"] for row in history] == ["原输入", "显示成功"]


@pytest.mark.parametrize("truncated", ["user", "assistant"])
def test_truncated_original_dialogue_cannot_become_a_confirmable_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, truncated: str
) -> None:
    """A bounded model projection never stands in for the complete original task or question."""
    first = "巡检仓库，" + "保留限制。" * 600 if truncated == "user" else "巡检仓库"
    clarification = "要哪个区域？" + ("澄清上下文" * 1000 if truncated == "assistant" else "")
    transport = Responses(
        [
            _response(_message(clarification)),
            _response(_call("propose_action", {"action": "plan", "start_turn": "u1"})),
        ]
    )
    session = session_for(tmp_path, transport)
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    try:
        display_question(session, first)
        display_question(session, "东区")
        assert session.suggestion is None and session.query_status == "failed"
        assert session.store.data["commands"] == []
    finally:
        session.store.close()


def test_hidden_multiturn_proposal_cannot_be_confirmed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Freezing a complete task does not bypass the original display and explicit choose gates."""
    transport = Responses(
        [
            _response(_message("要哪个区域？")),
            _response(_call("propose_action", {"action": "run", "start_turn": "u1"})),
        ]
    )
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    with workbench(tmp_path) as (session, controller):
        session.router_factory = lambda: _router(transport)
        display_question(session, "dog2 巡检仓库，速度不超过每秒 0.2 米。")
        turn = DisplayTurn(session)
        session.ask("东区")
        turn.stage()
        assert turn.suggestion is not None
        turn.reject()
        with pytest.raises(ValueError, match="待确认"):
            display_question(session, "/choose run")
        assert session.store.data["commands"] == [] and controller.submissions == []


def test_task_suffix_retains_every_intervening_user_constraint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Selecting an original task cannot skip a later limit, correction or final clarification."""
    user_turns = ["dog2 巡检仓库", "速度最多每秒 0.2 米", "改为 0.1 米，并先只规划"]
    transport = Responses(
        [
            _response(_message("要哪个区域？")),
            _response(_message("确认区域和速度。")),
            _response(_call("propose_action", {"action": "plan", "start_turn": "u1"})),
        ]
    )
    session = session_for(tmp_path, transport)
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    try:
        for text in user_turns:
            display_question(session, text)
        assert session.suggestion is not None
        instruction = session.suggestion[1]
        assert all("用户原文：\n" + text in instruction for text in user_turns)
        assert [instruction.index(text) for text in user_turns] == sorted(
            instruction.index(text) for text in user_turns
        )
        inputs = cast(list[JSONObject], transport.requests[-1]["input"])
        data = json.loads(cast(str, inputs[0]["content"]).partition("\n")[2])
        assert [row["id"] for row in data["proposal_sources"]] == ["u1", "u2", "current"]
    finally:
        session.store.close()


@pytest.mark.parametrize("failure", ["interrupt", "write"])
def test_actual_terminal_and_queued_history_exclude_hidden_assistant_turns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """A real muted turn or failed write cannot enter the next terminal or queued model context."""
    hidden = "未显示的新顺序：#1 b；#2 a。"
    transport = Responses(
        [
            _response(_call("read_cluster")),
            _response(_message(hidden)),
            _response(_message("接着查看节点能力。")),
        ]
    )
    session = session_for(tmp_path, transport)
    output = FailingOutput(failure, hidden)
    monkeypatch.setattr(sys, "stdout", output)
    display_question(session, "/nodes")
    entered, release = threading.Event(), threading.Event()
    original = session.ask

    def ask(text: str) -> str:
        """Hold the real worker after its audit save and before its display decision."""
        result = original(text)
        if text == "刷新节点":
            entered.set()
            assert release.wait(5)
        return result

    monkeypatch.setattr(session, "ask", ask)

    async def mute_or_queue() -> str:
        """Trigger actual Ctrl-C, or queue independent text while a pending output will fail."""
        assert await asyncio.to_thread(entered.wait, 5)
        if failure == "interrupt":
            asyncio.get_running_loop().call_soon(release.set)
            raise KeyboardInterrupt
        return "查看节点能力"

    async def continue_or_drain() -> str:
        """Start a fresh query after mute, or drain the question queued before the failed write."""
        release.set()
        await drain_foreground()
        if failure == "interrupt":
            return "查看节点能力"
        return "/quit"

    async def finish() -> str:
        """Inspect the actual Provider input and persistent audit after foreground drain."""
        await drain_foreground()
        assert hidden not in json.dumps(transport.requests[-1]["input"], ensure_ascii=False)
        return "/quit"

    steps = [command("刷新节点"), mute_or_queue, continue_or_drain]
    if failure == "interrupt":
        steps.append(finish)
    prompt = ScriptedPrompt(steps)
    prompt.install(monkeypatch)
    try:
        assert asyncio.run(terminal.interactive(session)) == 0
        assert len(transport.requests) == 3
        assert hidden not in json.dumps(transport.requests[-1]["input"], ensure_ascii=False)
        saved = json.loads(session.store.path.read_text())
        rows = [row for row in saved["messages"] if hidden in str(row.get("text"))]
        assert len(rows) == 1 and rows[0]["displayed"] is False
        if failure == "write":
            assert output.failed and "已加入待处理队列" in output.getvalue()
        assert session.store.data["commands"] == []
    finally:
        release.set()
        session.store.close()


def test_old_local_snapshot_output_stays_out_of_model_context() -> None:
    """Keep old records intact while excluding both local dumps and their commands."""
    rows: list[JSONObject] = [
        {"role": "user", "text": "巡检计划怎么写？"},
        {"role": "assistant", "text": "先列目标。", "displayed": True},
        {"role": "user", "text": "/raw"},
        {"role": "assistant", "text": "原始查询内容", "displayed": True},
        {"role": "user", "text": "/history"},
        {"role": "assistant", "text": "过去的会话副本", "displayed": True},
        {"role": "user", "text": "再举个例子。"},
    ]
    history = bounded_history(rows)
    assert [row["text"] for row in history] == ["巡检计划怎么写？", "先列目标。", "再举个例子。"]
    assert len(rows) == 7 and rows[3]["text"] == "原始查询内容"
