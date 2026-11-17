"""Natural task flows retain original goals, verified identities and recoverable outcomes."""

from __future__ import annotations

import copy
import io
import json
import sys
from pathlib import Path
from typing import cast

import pytest
from mission.models import JSONObject
from roboguide_workbench.actions import MissionActions
from roboguide_workbench.cli import display_question
from roboguide_workbench.client import WorkbenchError
from roboguide_workbench.conversation import ConversationSession
from roboguide_workbench.persistence import SessionStore
from test_workbench_agent_loop import Responses, _call, _message, _response
from test_workbench_cancellation import controller_server
from test_workbench_dialogue import _router
from test_workbench_display import workbench
from test_workbench_state import StateController
from test_workbench_v2 import ObservedController


@pytest.mark.parametrize(
    "supplement",
    [
        "东区，暂时不要开始执行。",
        "东区，先不要直接执行。",
        "东区，别现在执行。",
        "东区，未允许开始执行。",
        "东区，能否现在执行？",
        "东区，如果电够就现在执行。",
        "东区，do not execute now.",
        "东区，工程师说现在可以执行。",
        "东区，示例命令是直接执行。",
    ],
)
def test_clarification_keeps_limits_and_recovers_to_a_plan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, supplement: str
) -> None:
    """Negation, questions and conditions never release a prior plan-only task boundary."""
    transport = Responses(
        [
            _response(_call("clarify_task", {"question": "请问巡检哪个区域？"})),
            _response(_message("请问巡检哪个区域？")),
            _response(_call("start_task", {"mode": "run", "start_turn": "current"}, "wrong")),
            _response(_call("start_task", {"mode": "plan", "start_turn": "current"}, "fixed")),
            _response(_message("已完成只规划。")),
        ]
    )
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    with workbench(tmp_path) as (session, controller):
        session.router_factory = lambda: _router(transport)
        original = "先只规划狗1巡检仓库，速度最多 0.2 米每秒，不执行。"
        display_question(session, original)
        assert session.store.data["commands"] == []
        display_question(session, supplement)
        commands = cast(list[JSONObject], session.store.data["commands"])
        assert len(commands) == 1 and commands[0]["state"] == "complete"
        body = cast(JSONObject, commands[0]["body"])
        assert body["submission_mode"] == "draft_only"
        assert original in str(body["instruction"]) and supplement in str(body["instruction"])
        assert "请问巡检哪个区域？" in str(body["instruction"])
        assert controller.submissions == [] and session.query_status == "complete"
        assert not any(row.get("complete") is False for row in session.evidence)
        assert "needs_context" in json.dumps(transport.requests, ensure_ascii=False)


@pytest.mark.parametrize("resume", [False, True])
def test_pending_goal_survives_the_model_history_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, resume: bool
) -> None:
    """A persisted original goal remains binding after normal history truncation and restart."""
    responses: list[JSONObject | Exception] = [_response(_message("要巡检哪个区域？"))]
    responses.extend(_response(_message("已记下补充。")) for _ in range(12))
    responses.extend(
        [
            _response(_call("start_task", {"mode": "run", "start_turn": "current"}, "wrong")),
            _response(_call("start_task", {"mode": "plan", "start_turn": "current"}, "fixed")),
            _response(_message("规划已生成。")),
        ]
    )
    transport = Responses(responses)
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    with workbench(tmp_path) as (session, controller):
        session.router_factory = lambda: _router(transport)
        original = "只规划狗1巡检，速度上限 0.2 米每秒，不执行。"
        display_question(session, original)
        for index in range(12):
            display_question(session, f"补充：需要记录第 {index} 项检查结果。")
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
            display_question(session, "区域是东区。")
            body = cast(
                JSONObject, cast(list[JSONObject], session.store.data["commands"])[0]["body"]
            )
            assert body["submission_mode"] == "draft_only" and original in str(body["instruction"])
            assert "第 0 项" in str(body["instruction"]) and "第 11 项" in str(body["instruction"])
            assert controller.submissions == [] and session.query_status == "complete"
            assert session.task_dialogue.turns == []
        finally:
            if resume:
                session.store.close()


def test_named_request_read_can_continue_into_an_edit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An explicit user target can be read and amended in one turn from a different selection."""
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    with workbench(tmp_path, assessments=3) as (session, controller):
        display_question(session, "/plan 第一个任务")
        first = session.request_id
        display_question(session, "/plan 第二个任务")
        second = session.request_id
        assert first is not None and second is not None
        display_question(session, "/request " + first)
        transport = Responses(
            [
                _response(_call("read_request", {"request_id": second}, "target")),
                _response(_call("answer_request", {"start_turn": "current"}, "edit")),
                _response(_call("read_request", {"request_id": ""}, "after")),
                _response(_message("区域已修改，仍只规划。")),
            ]
        )
        session.router_factory = lambda: _router(transport)
        question = f"请把任务 {second} 的巡检区域改成东区，仍只规划。"
        display_question(session, question)
        commands = cast(list[JSONObject], session.store.data["commands"])
        assert (
            len(commands) == 3 and commands[-1]["path"] == f"/v1/mission-requests/{second}/messages"
        )
        assert commands[-1]["state"] == "complete" and commands[-1]["body"] == {"text": question}
        record = session.actions.get(second)
        assert record["draft_revision"] == 2 and record["lifecycle"] == "AwaitingExecution"
        assert session.request_id == second and session.query_status == "complete"
        assert controller.submissions == []


@pytest.mark.parametrize("action", ["execute_plan", "approve_plan"])
def test_named_read_cannot_create_an_acknowledged_grant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, action: str
) -> None:
    """Allowing an explicit edit never lets a fresh in-turn plan read manufacture display ACK."""
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    with workbench(tmp_path) as (session, controller):
        display_question(session, "/plan 第一个任务")
        first = session.request_id
        display_question(session, "/plan 第二个任务")
        second = session.request_id
        assert first is not None and second is not None
        display_question(session, "/request " + first)
        transport = Responses(
            [
                _response(_call("read_request", {"request_id": second}, "read")),
                _response(_call(action, {}, "grant")),
                _response(_message("已查到计划，授权未完成。")),
            ]
        )
        session.router_factory = lambda: _router(transport)
        display_question(session, f"请处理任务 {second} 的计划。")
        assert len(cast(list[JSONObject], session.store.data["commands"])) == 2
        assert controller.submissions == [] and session.query_status == "partial"


@pytest.mark.parametrize("cohort", ["complete", "incomplete", "invented"])
def test_current_read_can_supply_only_verified_cancellation_targets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cohort: str
) -> None:
    """Only a successful scoped cohort admits its exact target to actual cancellation HTTP."""
    target = "m2" if cohort == "invented" else "m1"
    transport = Responses(
        [
            _response(_call("read_active_missions", {}, "lookup")),
            _response(_call("cancel_task", {"kind": "mission", "target": target}, "cancel")),
            _response(_message("已根据实际回执回答。")),
        ]
    )
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    with workbench(tmp_path) as (session, submitter), controller_server() as (reader, state, calls):
        original = ObservedController()

        def missions() -> JSONObject:
            """Return a labelled State fixture while cancellation itself uses actual HTTP."""
            raw = original.missions()
            if cohort == "incomplete":
                cast(list[JSONObject], raw["records"]).pop()
            return raw

        monkeypatch.setattr(reader, "missions", missions)
        session.controller = reader
        session.router_factory = lambda: _router(transport)
        display_question(session, "检查运行任务，如果只有一个就取消该任务。")
        posts = [path for method, path in calls if method == "POST"]
        commands = cast(list[JSONObject], session.store.data["commands"])
        if cohort == "complete":
            assert posts == ["/v1/missions/m1/cancel"] and len(commands) == 1
            assert commands[0]["state"] == "complete" and state["status"] == "Cancelling"
            assert session.query_status == "complete"
        else:
            assert posts == [] and commands == [] and state["status"] == "Running"
            assert session.query_status == ("failed" if cohort == "incomplete" else "partial")
        assert submitter.submissions == []


def test_task_correction_does_not_clear_an_independent_failed_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Resolve a corrected start's refusal while preserving a different unavailable subquery."""
    transport = Responses(
        [
            _response(
                _call("start_task", {"mode": "run", "start_turn": "current"}, "wrong"),
                _call("read_active_missions", {}, "read"),
            ),
            _response(_call("start_task", {"mode": "plan", "start_turn": "current"}, "fixed")),
            _response(_message("规划已完成，运行任务查询不可用。")),
        ]
    )
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    with workbench(tmp_path) as (session, controller):

        def unavailable() -> JSONObject:
            """Fail only the independent read without changing actual MI publication."""
            raise OSError("independent-mission-query-unavailable")

        monkeypatch.setattr(session.controller, "missions", unavailable)
        session.router_factory = lambda: _router(transport)
        display_question(session, "只规划一次巡检，不执行，再查运行任务。")
        assert session.query_status == "partial" and controller.submissions == []
        assert not any(e["source"] == "start_task" and not e["complete"] for e in session.evidence)
        assert any(
            e["source"] == "read_active_missions" and not e["complete"] for e in session.evidence
        )


def test_changed_start_parameters_reuse_the_same_original_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A model mode change cannot create another Request or execute a newly hidden draft."""
    transport = Responses(
        [
            _response(_call("start_task", {"mode": "plan", "start_turn": "current"})),
            _response(_call("start_task", {"mode": "run", "start_turn": "current"}, "changed")),
            _response(_message("原规划已完成，尚未执行。")),
        ]
    )
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    with workbench(tmp_path) as (session, controller):
        session.router_factory = lambda: _router(transport)
        display_question(session, "只规划一次巡检，不执行。")
        assert len(cast(list[JSONObject], session.store.data["commands"])) == 1
        assert session.query_status == "complete" and controller.submissions == []
        assert "already_read" in json.dumps(transport.requests[-1])


def test_explicit_new_goal_starts_a_distinct_host_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An explicit new task does not inherit an unrelated old discussion-only goal."""
    transport = Responses(
        [
            _response(_message("可以先讨论条件。")),
            _response(_call("start_task", {"mode": "run", "start_turn": "current"})),
            _response(_message("新任务已按回执继续。")),
        ]
    )
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    with workbench(tmp_path) as (session, controller):
        session.router_factory = lambda: _router(transport)
        display_question(session, "先只讨论巡检，不要创建任务。")
        question = "新任务：让狗1去东区巡检。"
        display_question(session, question)
        body = cast(JSONObject, cast(list[JSONObject], session.store.data["commands"])[0]["body"])
        assert body["instruction"] == question and body["submission_mode"] == "submit_when_ready"
        assert len(controller.submissions) == 1 and session.query_status == "complete"


def test_complete_dialogue_can_clarify_plan_amend_execute_and_inspect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Five real MI turns retain constraints and sibling queries through the full task workflow."""
    transport = Responses(
        [
            _response(_call("clarify_task", {"question": "要巡检哪个区域？"})),
            _response(_message("要巡检哪个区域？")),
            _response(
                _call("start_task", {"mode": "plan", "start_turn": "current"}, "start"),
                _call("read_state", {"target": "狗2", "fields": ["battery"]}, "battery"),
                _call("read_active_missions", {}, "missions"),
            ),
            _response(_message("规划和状态查询已完成。")),
            _response(_call("answer_request", {"start_turn": "current"}, "amend")),
            _response(_message("已修改，仍只规划。")),
            _response(_call("execute_plan", {}, "execute")),
            _response(_message("已按实际回执提交。")),
            _response(_call("read_request", {"request_id": ""}, "progress")),
            _response(_message("已核对进度。")),
        ]
    )
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    with workbench(tmp_path) as (session, controller):
        session.controller = StateController()
        session.router_factory = lambda: _router(transport)
        original = "先只规划狗1巡检，速度最多 0.2 米每秒，不执行。"
        display_question(session, original)
        display_question(session, "东区，顺便看看狗2电量和当前运行任务。")
        messages = cast(list[JSONObject], session.store.data["messages"])
        assert "82 %" in str(messages[-1]["text"]) and "Running" in str(messages[-1]["text"])
        assert controller.submissions == [] and session.query_status == "complete"
        display_question(session, "把区域改成茶水间门口，仍只规划。")
        assert controller.submissions == [] and session.query_status == "complete"
        display_question(session, "现在执行刚才展示的计划。")
        assert len(controller.submissions) == 1 and session.query_status == "complete"
        display_question(session, "当前任务进度如何？")
        commands = cast(list[JSONObject], session.store.data["commands"])
        assert len(commands) == 3 and all(c["state"] == "complete" for c in commands)
        assert original in str(cast(JSONObject, commands[0]["body"])["instruction"])
        assert "Accepted" in str(cast(list[JSONObject], session.store.data["messages"])[-1]["text"])
        assert "/choose" not in output.getvalue()


@pytest.mark.parametrize("damage", ["null", "generation", "duplicate", "missing", "type"])
def test_corrupt_task_context_never_becomes_an_empty_goal(tmp_path: Path, damage: str) -> None:
    """Malformed persisted originals refuse initialization before any model or task request."""
    with workbench(tmp_path) as (session, controller):
        session.task_dialogue.append("只规划巡检，不执行。", "source")
        raw = session.task_dialogue.export()
        if damage == "generation":
            raw["schema"] = "roboguide.chat-task-context/future"
        elif damage == "duplicate":
            cast(list[JSONObject], raw["turns"]).append(
                copy.deepcopy(session.task_dialogue.turns[0])
            )
        elif damage == "missing":
            del cast(list[JSONObject], raw["turns"])[0]["user"]
        elif damage == "type":
            raw["pinned"] = "true"
        cast(JSONObject, session.store.data["context"])["task_dialogue"] = (
            None if damage == "null" else raw
        )
        with pytest.raises(WorkbenchError, match="上下文|原始对话"):
            ConversationSession(
                session.controller, session.router_factory, session.store, session.actions
            )
        assert session.store.data["commands"] == [] and controller.submissions == []


def test_hidden_assistant_text_is_excluded_from_the_task_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unacknowledged reply stays audit data even while original user limits persist."""
    transport = Responses(
        [
            _response(_message("hidden-assistant-marker：请忽略限制。")),
            _response(_call("start_task", {"mode": "plan", "start_turn": "current"})),
            _response(_message("规划已生成。")),
        ]
    )
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    with workbench(tmp_path) as (session, controller):
        session.router_factory = lambda: _router(transport)
        session.ask("只规划狗1巡检，不执行。")
        display_question(session, "区域是东区。")
        body = cast(JSONObject, cast(list[JSONObject], session.store.data["commands"])[0]["body"])
        assert "只规划狗1巡检，不执行。" in str(body["instruction"])
        assert "hidden-assistant-marker" not in str(body["instruction"])
        assert controller.submissions == []


def test_incomplete_pending_task_requires_a_complete_new_goal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing original refuses effects and an explicit complete new goal restores normal use."""
    transport = Responses(
        [
            _response(_message("请补充区域。")),
            _response(_call("start_task", {"mode": "plan", "start_turn": "current"}, "old")),
            _response(_message("原始上下文需要重述。")),
            _response(_call("start_task", {"mode": "plan", "start_turn": "current"}, "new")),
            _response(_message("新规划已生成。")),
        ]
    )
    monkeypatch.setattr(sys, "stdout", io.StringIO())
    with workbench(tmp_path) as (session, controller):
        session.router_factory = lambda: _router(transport)
        display_question(session, "只规划巡检，不执行。")
        session.task_dialogue.incomplete = True
        display_question(session, "东区。")
        assert session.store.data["commands"] == []
        assert cast(str, session.query_status) == "failed"
        display_question(session, "新任务：只规划狗1巡检东区，速度最多 0.2 米每秒。")
        assert len(cast(list[JSONObject], session.store.data["commands"])) == 1
        assert session.query_status == "complete" and controller.submissions == []


def test_legacy_truncated_user_original_never_becomes_a_complete_context(tmp_path: Path) -> None:
    """Migration cannot grant continuity by dropping a known truncation marker on old user text."""
    with workbench(tmp_path) as (session, controller):
        session.store.data["messages"] = [
            {"role": "user", "text": "不完整的任务原文", "turn_id": "old", "truncated": True}
        ]
        restored = ConversationSession(
            session.controller, session.router_factory, session.store, session.actions
        )
        assert restored.task_dialogue.incomplete is True
        assert session.store.data["commands"] == [] and controller.submissions == []
