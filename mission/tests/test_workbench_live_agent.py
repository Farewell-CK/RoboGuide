"""Reproduce real-use failures and exercise natural task tools through actual Mission HTTP."""

from __future__ import annotations

import asyncio
import copy
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
from roboguide_workbench.client import ToolRefusal
from roboguide_workbench.conversation import ConversationSession
from roboguide_workbench.persistence import SessionStore
from roboguide_workbench.task_tools import TaskTools
from test_workbench_agent_loop import Responses, _call, _message, _response
from test_workbench_dialogue import _router
from test_workbench_display import ScriptedPrompt, command, drain_foreground, workbench
from test_workbench_display_v4 import FailingOutput
from test_workbench_state import StateController, declaration, legacy_value
from test_workbench_state_chat import session_for_state


def test_repeated_directory_does_not_discard_a_sibling_mission_query(tmp_path: Path) -> None:
    """Replay the failed compound turn with a repeated directory and a new independent query."""
    transport = Responses(
        [
            _response(_call("read_state", {"target": "invented", "fields": ["battery"]})),
            _response(_call("list_state_fields", {"target": "狗2"}, "directory")),
            _response(
                _call("list_state_fields", {"target": "狗2"}, "repeat"),
                _call("read_active_missions", {}, "missions"),
            ),
            _response(_message("任务 m1 仍在运行；设备状态需要选择具体来源。")),
        ]
    )
    session = session_for_state(tmp_path, transport)
    try:
        answer = session.ask("帮我查狗2状态，再看看有没有运行任务。")
        assert "m1" in answer and "运行" in answer
        assert isinstance(session.controller, StateController)
        assert session.controller.reads == ["providers", "missions"]
        assert "already_read" in json.dumps(transport.requests[-1], ensure_ascii=False)
        assert session.store.data["commands"] == []
    finally:
        session.store.close()


def test_spoken_alias_uses_the_current_catalog_without_borrowing_last_device(
    tmp_path: Path,
) -> None:
    """Resolve a Chinese alias from actual exports after selecting a different dog."""
    transport = Responses(
        [
            _response(
                _call("read_state", {"target": "entity-lite3-dog2", "fields": ["battery"]}),
                _call("read_active_missions", {}, "mission"),
            ),
            _response(_message("读取完成。")),
        ]
    )
    session = session_for_state(tmp_path, transport)
    session.last_state = "entity-dog1"
    session.last_node = "lite3-dog1"
    try:
        answer = session.ask("帮我查狗2状态，再看看有没有运行任务。")
        assert "82 %" in answer and "m1" in answer and "Running" in answer
        assert session.query_status == "complete"
        assert isinstance(session.controller, StateController)
        assert session.controller.reads == ["providers", "inventory", "state", "missions"]
    finally:
        session.store.close()


def test_two_state_objects_offer_working_source_choices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Node alias stays ambiguous while its displayed object ordinals select exact sources."""
    session = session_for_state(tmp_path, Responses([]))
    client = cast(StateController, session.controller)
    exports = cast(
        list[JSONObject], cast(list[JSONObject], client.catalog["nodes"])[0]["state_exports"]
    )
    exports.insert(0, declaration(legacy=True))
    records = cast(list[JSONObject], client.current_state["records"])
    row = copy.deepcopy(records[0])
    row.update(
        object=exports[0]["object"], source="node:dog2/robot-observer", channel_id="robot-state"
    )
    envelope = cast(JSONObject, row["value"])
    envelope.update(payload_schema="lite3.robot-state/v0.1", value=legacy_value())
    records.insert(0, row)
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    from roboguide_workbench.cli import display_question

    try:
        display_question(session, "/state-fields 狗2")
        assert session.displayed_states == ["object:dog2", "entity-dog2"]
        ambiguous = session.ask("/state 狗2 电量")
        assert "来源有歧义" in ambiguous and "/state object:dog2 battery" in ambiguous
        first = session.ask("/state #1 电量")
        assert "65 %" in first and session.query_status == "complete"
        second = session.ask("/state #2 电量")
        assert "82 %" in second and session.query_status == "complete"
    finally:
        session.store.close()


@pytest.mark.parametrize("hidden", [False, True])
def test_raw_survives_resume_and_ignores_a_failed_display(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, hidden: bool
) -> None:
    """Only acknowledged original JSON survives a new process, without a fresh GET or clock."""
    session = session_for_state(tmp_path, Responses([]))
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    from roboguide_workbench.cli import display_question

    display_question(session, "/nodes")
    original, read_at = copy.deepcopy(session.last_raw), session.last_read_at
    assert read_at is not None
    if hidden:
        monkeypatch.setattr(sys, "stdout", FailingOutput("write", "设备状态"))
        with pytest.raises(OSError):
            display_question(session, "/state 狗2 电量")
    session.store.close()
    store = SessionStore(tmp_path / "sessions", "state-chat", {}, True)
    resumed = ConversationSession(
        session.controller, session.router_factory, store, MissionActions(session.controller, store)
    )
    before = list(cast(StateController, resumed.controller).reads)
    try:
        answer = resumed.ask("/raw")
        assert "历史查询" in answer and read_at in answer
        assert resumed.last_raw == original and resumed.last_read_at == read_at
        assert cast(StateController, resumed.controller).reads == before
    finally:
        store.close()


@pytest.mark.parametrize("mode", ["plan", "run"])
def test_natural_task_calls_actual_mi_without_a_choose_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    """Create one actual MI Request with the user's original submission intent."""
    transport = Responses(
        [
            _response(_call("start_task", {"mode": mode, "start_turn": "current"})),
            _response(_message("已按照任务回执继续处理。")),
        ]
    )
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    from roboguide_workbench.cli import display_question

    with workbench(tmp_path) as (session, controller):
        session.router_factory = lambda: _router(transport)
        instruction = "先只规划一次巡检" if mode == "plan" else "执行一次巡检"
        display_question(session, instruction)
        commands = cast(list[JSONObject], session.store.data["commands"])
        assert len(commands) == 1 and commands[0]["state"] == "complete"
        body = cast(JSONObject, commands[0]["body"])
        assert body["instruction"] == instruction
        assert body["submission_mode"] == ("draft_only" if mode == "plan" else "submit_when_ready")
        assert len(controller.submissions) == (0 if mode == "plan" else 1)
        assert session.suggestion is None and session.request_id is not None
        assert "/choose" not in cast(io.StringIO, sys.stdout).getvalue()
        names = {tool["name"] for tool in cast(list[JSONObject], transport.requests[0]["tools"])}
        assert "start_task" in names and "propose_action" not in names


def test_native_task_keeps_all_original_constraints_and_actual_contract_gaps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Retain the full original suffix and explain missing current contracts."""
    transport = Responses(
        [
            _response(_message("要巡检哪个区域？")),
            _response(_call("start_task", {"mode": "plan", "start_turn": "u1"})),
            _response(_message("规划已生成。")),
        ]
    )
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    from roboguide_workbench.cli import display_question

    with workbench(tmp_path) as (session, _controller):
        session.router_factory = lambda: _router(transport)
        display_question(session, "让狗1巡检仓库，速度最多 0.2 米每秒")
        display_question(session, "东区，先只规划")
        body = cast(JSONObject, cast(list[JSONObject], session.store.data["commands"])[0]["body"])
        assert "速度最多 0.2 米每秒" in str(body["instruction"]) and "东区，先只规划" in str(
            body["instruction"]
        )
        assert "当前设备接入情况" in output.getvalue() and "未登记导航/巡检" in output.getvalue()


def test_task_tool_cannot_execute_an_undisplayed_plan_or_greeting(tmp_path: Path) -> None:
    """Retain displayed-plan and ordinary-chat boundaries before network effects."""
    with workbench(tmp_path) as (session, controller):
        tasks = TaskTools(session, "请执行当前计划")
        with pytest.raises(ToolRefusal, match="完整显示"):
            tasks.invoke("execute_plan", {}, "请执行当前计划", 5)
        with pytest.raises(ToolRefusal, match="普通问候"):
            TaskTools(session, "谢谢。").invoke("start_task", {"mode": "run"}, "谢谢。", 5)
        assert session.store.data["commands"] == [] and controller.submissions == []


def test_native_cancel_cannot_guess_a_short_id_from_an_english_word(tmp_path: Path) -> None:
    """The letter a inside cancel does not authorize cancellation of an unknown Mission a."""
    with workbench(tmp_path) as (session, controller):
        with pytest.raises(ToolRefusal, match="取消目标"):
            TaskTools(session, "cancel that task").invoke(
                "cancel_task", {"kind": "mission", "target": "a"}, "cancel that task", 5
            )
        assert session.store.data["commands"] == [] and controller.submissions == []


def test_natural_execute_does_not_require_a_command_word(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An explicit natural instruction can execute the actual acknowledged plan without /choose."""
    from roboguide_workbench.cli import display_question

    monkeypatch.setattr(sys, "stdout", io.StringIO())
    with workbench(tmp_path) as (session, controller):
        display_question(session, "/plan 只规划")
        transport = Responses(
            [
                _response(_call("execute_plan", {})),
                _response(_message("已按原版本执行。")),
            ]
        )
        session.router_factory = lambda: _router(transport)
        display_question(session, "照这个方案做")
        commands = cast(list[JSONObject], session.store.data["commands"])
        assert len(commands) == 2 and str(commands[-1]["path"]).endswith("/execute")
        assert len(controller.submissions) == 1 and session.suggestion is None


def test_duplicate_native_task_does_not_repeat_request_post(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A second tool ID with the same action reuses its original receipt without another POST."""
    transport = Responses(
        [
            _response(_call("start_task", {"mode": "plan", "start_turn": "current"})),
            _response(_call("start_task", {"mode": "plan", "start_turn": "current"}, "repeat")),
            _response(_message("原规划请求已建立。")),
        ]
    )
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    from roboguide_workbench.cli import display_question

    with workbench(tmp_path) as (session, _controller):
        session.router_factory = lambda: _router(transport)
        display_question(session, "只规划巡检")
        assert len(cast(list[JSONObject], session.store.data["commands"])) == 1
        assert "already_read" in json.dumps(transport.requests[-1], ensure_ascii=False)


def test_native_cancel_keeps_request_and_controller_authority_separate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Natural cancellation closes the actual MI Request without issuing a Controller effect."""
    from roboguide_workbench.cli import display_question

    monkeypatch.setattr(sys, "stdout", io.StringIO())
    with workbench(tmp_path) as (session, controller):
        display_question(session, "/plan 只规划")
        request_id = session.request_id
        assert request_id is not None
        transport = Responses(
            [
                _response(_call("cancel_task", {"kind": "request", "target": request_id})),
                _response(_message("规划请求已取消。")),
            ]
        )
        session.router_factory = lambda: _router(transport)
        display_question(session, "取消刚才的规划请求")
        commands = cast(list[JSONObject], session.store.data["commands"])
        assert len(commands) == 2 and str(commands[-1]["path"]).endswith("/cancel")
        assert commands[-1]["state"] == "complete" and controller.submissions == []
        assert "Cancelled" in cast(
            str, cast(list[JSONObject], session.store.data["messages"])[-1]["text"]
        )


@pytest.mark.parametrize(
    "question,mode",
    [("先不执行，只讨论条件", "run"), ("先只规划", "run"), ("不要创建任务，只讨论", "plan")],
)
def test_discussion_and_plan_only_intent_cannot_turn_into_execution(
    tmp_path: Path, question: str, mode: str
) -> None:
    """Explicit user limits refuse task effects even if a model selects an incompatible tool."""
    with workbench(tmp_path) as (session, controller):
        with pytest.raises(ToolRefusal):
            TaskTools(session, question).invoke("start_task", {"mode": mode}, question, 5)
        assert session.store.data["commands"] == [] and controller.submissions == []


def test_lost_native_task_response_observes_the_original_command_without_reposting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Actual committed MI work survives response loss and a model's repeated start request."""
    from roboguide_workbench.cli import display_question

    monkeypatch.setattr(sys, "stdout", io.StringIO())
    transport = Responses(
        [
            _response(_call("start_task", {"mode": "plan", "start_turn": "current"})),
            _response(_call("observe_operation", {}, "observe")),
            _response(_call("start_task", {"mode": "plan", "start_turn": "current"}, "repeat")),
            _response(_message("已核对原规划请求。")),
        ]
    )
    with workbench(tmp_path) as (session, controller):
        session.router_factory = lambda: _router(transport)
        actual_post = session.actions._post
        posts: list[str] = []

        def lose_response(path: str, body: JSONObject, key: str | None = None) -> JSONObject:
            """Commit through real MI HTTP once, then lose only the client's response."""
            if path == "/v1/mission-requests":
                posts.append(path)
                actual_post(path, body, key)
                raise OSError("fixture response loss after actual MI commit")
            return actual_post(path, body, key)

        monkeypatch.setattr(session.actions, "_post", lose_response)
        display_question(session, "先只规划巡检")
        assert posts == ["/v1/mission-requests"]
        commands = cast(list[JSONObject], session.store.data["commands"])
        assert len(commands) == 1 and commands[0]["state"] == "complete"
        assert commands[0]["observation"] is not None and session.request_id is not None
        assert controller.submissions == []


def test_queued_messages_can_be_seen_withdrawn_and_edited_without_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Withdrawn queued text never reaches the session owner while the active operation drains."""
    entered, release = threading.Event(), threading.Event()
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    with workbench(tmp_path) as (session, _controller):
        original = session.ask
        calls: list[str] = []

        def ask(text: str) -> str:
            """Hold the first actual writer while input management remains available."""
            calls.append(text)
            result = original(text)
            if text == "/help":
                entered.set()
                assert release.wait(5)
            return result

        monkeypatch.setattr(session, "ask", ask)

        async def queued() -> str:
            """Submit a visible pending read during the first request."""
            assert await asyncio.to_thread(entered.wait, 5)
            return "/nodes"

        async def finish() -> str:
            """Drain only the original request after withdrawing the unsent second request."""
            release.set()
            await drain_foreground()
            return "/quit"

        prompt = ScriptedPrompt(
            [command("/help"), queued, command("/queue"), command("/dequeue 1"), finish]
        )
        prompt.install(monkeypatch)
        assert asyncio.run(terminal.interactive(session)) == 0
        assert calls == ["/help"]
        assert "1. /nodes" in output.getvalue() and "已撤回待发消息" in output.getvalue()
        assert prompt.app.refresh_interval is None
