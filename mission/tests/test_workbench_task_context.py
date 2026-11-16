"""Keep native task intent, input-time authority and independent answers across tool calls."""

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
from roboguide_workbench.cli import display_question
from roboguide_workbench.client import ToolRefusal
from roboguide_workbench.conversation import ConversationSession
from roboguide_workbench.persistence import SessionStore
from roboguide_workbench.task_tools import TaskTools
from test_workbench_agent_loop import Responses, _call, _message, _response
from test_workbench_dialogue import _router
from test_workbench_display import ScriptedPrompt, command, drain_foreground, workbench
from test_workbench_state import StateController


def test_task_receipt_keeps_the_independent_mission_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Actual Mission reads survive a task card even when the model omits their answer."""
    transport = Responses(
        [
            _response(
                _call("start_task", {"mode": "plan", "start_turn": "current"}),
                _call("read_active_missions", {}, "missions"),
            ),
            _response(_message("新规划已生成。")),
        ]
    )
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    with workbench(tmp_path) as (session, controller):
        session.router_factory = lambda: _router(transport)
        display_question(session, "只规划一次巡检，顺便看看当前有没有运行中的任务。")
        answer = cast(str, cast(list[JSONObject], session.store.data["messages"])[-1]["text"])
        assert "m1" in answer and "Running" in answer
        assert session.request_id is not None
        assert session.request_id in answer and "AwaitingExecution" in answer
        assert session.query_status == "complete" and controller.submissions == []


@pytest.mark.parametrize("resume", [False, True])
def test_plan_only_limit_survives_a_natural_clarification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, resume: bool
) -> None:
    """A short area answer never removes the original explicit prohibition on execution."""
    transport = Responses(
        [
            _response(_message("请问巡检哪个区域？")),
            _response(_call("start_task", {"mode": "run", "start_turn": "u1"})),
            _response(_message("已开始处理。")),
        ]
    )
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    with workbench(tmp_path) as (session, controller):
        session.router_factory = lambda: _router(transport)
        display_question(session, "先只规划巡检，不执行。")
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
            display_question(session, "东区。")
            assert session.store.data["commands"] == [] and controller.submissions == []
            assert session.query_status != "complete"
            assert "仅规划" in str(session.store.data["messages"])
        finally:
            if resume:
                session.store.close()


@pytest.mark.parametrize(
    "foreground,allowed",
    [("/plan 第二个任务", False), ("/answer 把区域改为东区", False), ("/nodes", True)],
)
def test_queued_execute_uses_only_the_input_time_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, foreground: str, allowed: bool
) -> None:
    """Queue an execution against visible A while B is held before its actual display ACK."""
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    entered, release, processed = threading.Event(), threading.Event(), threading.Event()
    with workbench(tmp_path) as (session, controller):
        display_question(session, "/plan 第一个任务")
        first_request = session.request_id
        original = session.ask
        references: list[JSONObject] = []
        transport = Responses(
            [_response(_call("execute_plan", {})), _response(_message("已处理执行要求。"))]
        )
        session.router_factory = lambda: _router(transport)

        def held_ask(text: str) -> str:
            """Hold the new plan without changing the terminal's visible input-time references."""
            if text == "执行当前计划":
                assert session.input_references is not None
                references.append(session.input_references)
            try:
                result = original(text)
                if text == foreground:
                    entered.set()
                    assert release.wait(5)
                return result
            finally:
                if text == "执行当前计划":
                    processed.set()

        monkeypatch.setattr(session, "ask", held_ask)

        async def queue_execution() -> str:
            """Enter execution while only the first plan has been fully displayed."""
            assert await asyncio.to_thread(entered.wait, 5)
            return "执行当前计划"

        async def finish() -> str:
            """Allow B to display and drain the queued instruction before leaving the terminal."""
            release.set()
            assert await asyncio.to_thread(processed.wait, 5)
            await drain_foreground()
            return "/quit"

        prompt = ScriptedPrompt([command(foreground), queue_execution, finish])
        prompt.install(monkeypatch)
        assert asyncio.run(terminal.interactive(session)) == 0
        commands = cast(list[JSONObject], session.store.data["commands"])
        assert references[0]["request_id"] == first_request
        assert len(commands) == 2 and len(controller.submissions) == int(allowed)
        assert (session.request_id != first_request) == foreground.startswith("/plan")
        if not allowed:
            assert "已变化" in output.getvalue()


@pytest.mark.parametrize("batch", [False, True])
def test_implicit_request_cache_resolves_the_new_current_task(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, batch: bool
) -> None:
    """The same empty-ID read performs a new HTTP GET after a different Request is created."""
    calls = [
        _call("read_request", {"request_id": ""}, "old"),
        _call("start_task", {"mode": "plan", "start_turn": "current"}, "new"),
        _call("read_request", {"request_id": ""}, "current"),
    ]
    responses: list[JSONObject | Exception] = (
        [_response(*calls)] if batch else [_response(call) for call in calls]
    )
    responses.append(_response(_message("已核对新任务进度。")))
    transport = Responses(responses)
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    with workbench(tmp_path) as (session, controller):
        display_question(session, "/plan 第一个任务")
        first_request = session.request_id
        original_get = session.actions.get
        gets: list[str] = []

        def observed_get(request_id: str) -> JSONObject:
            """Witness actual HTTP targets without substituting Request responses."""
            gets.append(request_id)
            return original_get(request_id)

        monkeypatch.setattr(session.actions, "get", observed_get)
        session.router_factory = lambda: _router(transport)
        display_question(session, "先查看当前任务，再只规划另一个任务，并查看新任务进度。")
        assert session.request_id != first_request
        assert gets == [first_request, session.request_id]
        final_input = cast(list[JSONObject], transport.requests[-1]["input"])
        last_tool = next(row for row in reversed(final_input) if row.get("call_id") == "current")
        returned = json.loads(cast(str, last_tool["output"]))
        assert session.request_id in returned["text"]
        assert first_request not in returned["text"]
        assert session.query_status == "complete" and controller.submissions == []


@pytest.mark.parametrize("name", ["execute_plan", "approve_plan", "answer_request"])
def test_task_writes_do_not_rebind_to_a_new_input_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, name: str
) -> None:
    """Frozen task references protect every contextual edit or grant before any new POST."""
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    with workbench(tmp_path) as (session, controller):
        display_question(session, "/plan 第一个任务")
        references = session.query_references()
        display_question(session, "/plan 第二个任务")
        session.input_references = references
        assert session.query_references()["request_id"] == references["request_id"]
        with pytest.raises(ToolRefusal, match="已变化"):
            TaskTools(session, "请处理当前任务").invoke(name, {}, "请处理当前任务", 5)
        assert len(cast(list[JSONObject], session.store.data["commands"])) == 2
        assert controller.submissions == []


@pytest.mark.parametrize(
    "instruction,clarification,answer,mode,submission",
    [
        ("先只规划巡检，不执行。", "哪个区域？", "东区。", "plan", "draft_only"),
        ("先只规划巡检，不执行。", "哪个区域？", "东区，现在执行。", "run", "submit_when_ready"),
        ("让狗1巡检。", "是只规划还是直接执行？", "东区。", "run", "submit_when_ready"),
        ("先只讨论巡检条件，不要创建任务。", "哪个区域？", "东区。", "plan", None),
    ],
)
def test_clarification_intent_uses_user_turns_and_explicit_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    instruction: str,
    clarification: str,
    answer: str,
    mode: str,
    submission: str | None,
) -> None:
    """Only original user limits and their explicit changes determine submission permission."""
    transport = Responses(
        [
            _response(_message(clarification)),
            _response(_call("start_task", {"mode": mode, "start_turn": "u1"})),
            _response(_message("已处理当前要求。")),
        ]
    )
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    with workbench(tmp_path) as (session, controller):
        session.router_factory = lambda: _router(transport)
        display_question(session, instruction)
        display_question(session, answer)
        commands = cast(list[JSONObject], session.store.data["commands"])
        if submission is None:
            assert commands == [] and controller.submissions == []
        else:
            assert len(commands) == 1
            body = cast(JSONObject, commands[0]["body"])
            assert body["submission_mode"] == submission
            assert instruction in str(body["instruction"]) and answer in str(body["instruction"])
            assert len(controller.submissions) == int(mode == "run")


@pytest.mark.parametrize("failed", ["task", "missions"])
def test_independent_task_and_read_failures_remain_visible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed: str
) -> None:
    """A failed sibling retains its cause and partial status beside the other successful answer."""
    transport = Responses(
        [
            _response(
                _call(
                    "start_task",
                    {"mode": "run" if failed == "task" else "plan", "start_turn": "current"},
                ),
                _call("read_active_missions", {}, "missions"),
            ),
            _response(_message("两个问题都已完成。")),
        ]
    )
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    with workbench(tmp_path) as (session, controller):
        session.router_factory = lambda: _router(transport)

        def unavailable() -> JSONObject:
            """Fail only the independent Mission GET without replacing actual MI planning."""
            raise OSError("fixture-mission-read-unavailable")

        if failed == "missions":
            monkeypatch.setattr(session.controller, "missions", unavailable)
        display_question(session, "只规划一次巡检，顺便看看有没有运行任务。")
        answer = cast(str, cast(list[JSONObject], session.store.data["messages"])[-1]["text"])
        if failed == "task":
            assert "仅规划" in answer and "m1" in answer and "Running" in answer
            assert session.store.data["commands"] == []
        else:
            assert "AwaitingExecution" in answer and "fixture-mission-read-unavailable" in answer
        assert session.query_status == "partial" and controller.submissions == []
        assert "两个问题都已完成" not in answer


def test_task_receipt_keeps_state_and_mission_answers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Combine checked physical reports, Mission status and the actual task receipt."""
    transport = Responses(
        [
            _response(
                _call("start_task", {"mode": "plan", "start_turn": "current"}),
                _call("read_state", {"target": "狗2", "fields": ["battery"]}, "state"),
                _call("read_active_missions", {}, "missions"),
            ),
            _response(_message("已读取。")),
        ]
    )
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    with workbench(tmp_path) as (session, controller):
        session.controller = StateController()
        session.router_factory = lambda: _router(transport)
        display_question(session, "只规划一次巡检，顺便查狗2电量和运行任务。")
        answer = cast(str, cast(list[JSONObject], session.store.data["messages"])[-1]["text"])
        assert "82 %" in answer and "m1" in answer and "Running" in answer
        assert "AwaitingExecution" in answer and session.query_status == "complete"
        assert controller.submissions == []


def test_request_cache_refreshes_after_execution_and_reuses_an_unchanged_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A changed command invalidates same-Request status while an unchanged repeat avoids I/O."""
    transport = Responses(
        [
            _response(_call("read_request", {"request_id": ""}, "before")),
            _response(_call("execute_plan", {}, "execute")),
            _response(_call("read_request", {"request_id": ""}, "after")),
            _response(_call("read_request", {"request_id": ""}, "repeat")),
            _response(_message("已核对实际进度。")),
        ]
    )
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    with workbench(tmp_path) as (session, controller):
        display_question(session, "/plan 巡检")
        request_id = session.request_id
        original_get = session.actions.get
        gets: list[str] = []

        def observed_get(target: str) -> JSONObject:
            """Count reads including the unchanged server-side exact-plan validation."""
            gets.append(target)
            return original_get(target)

        monkeypatch.setattr(session.actions, "get", observed_get)
        session.router_factory = lambda: _router(transport)
        display_question(session, "查看当前进度，执行当前计划，并核对执行后的进度。")
        assert gets == [request_id, request_id, request_id]
        final_input = cast(list[JSONObject], transport.requests[-1]["input"])
        last_tool = next(row for row in reversed(final_input) if row.get("call_id") == "repeat")
        returned = json.loads(cast(str, last_tool["output"]))
        assert returned["status"] == "already_read" and "Accepted" in returned["result"]["text"]
        assert len(controller.submissions) == 1 and session.query_status == "complete"


def test_operation_cache_tracks_the_exact_original_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Observation after execution targets its original command, never the cached create receipt."""
    transport = Responses(
        [
            _response(_call("observe_operation", {}, "before")),
            _response(_call("execute_plan", {}, "execute")),
            _response(_call("observe_operation", {}, "after")),
            _response(_call("observe_operation", {}, "repeat")),
            _response(_message("已核对原操作。")),
        ]
    )
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    with workbench(tmp_path) as (session, controller):
        display_question(session, "/plan 巡检")
        original_observe = session.actions.observe
        keys: list[str] = []

        def observed(*, reconcile: bool = False) -> JSONObject:
            """Witness actual HTTP observation keys without replaying either original POST."""
            commands = cast(list[JSONObject], session.store.data["commands"])
            keys.append(cast(str, commands[-1]["idempotency_key"]))
            return original_observe(reconcile=reconcile)

        monkeypatch.setattr(session.actions, "observe", observed)
        session.router_factory = lambda: _router(transport)
        display_question(session, "核对原操作，执行当前计划，再核对执行操作的回执。")
        commands = cast(list[JSONObject], session.store.data["commands"])
        assert keys == [row["idempotency_key"] for row in commands]
        assert len(commands) == 2 and len(controller.submissions) == 1
        final_input = cast(list[JSONObject], transport.requests[-1]["input"])
        last_tool = next(row for row in reversed(final_input) if row.get("call_id") == "repeat")
        returned = json.loads(cast(str, last_tool["output"]))
        assert returned["status"] == "already_read" and "Accepted" in returned["result"]["text"]
