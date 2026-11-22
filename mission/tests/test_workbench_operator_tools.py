"""Exercise the newly exposed operator tools through the Workbench action loop."""

from __future__ import annotations

import copy
import io
import json
import sys
from pathlib import Path
from typing import cast

import pytest
from mission.deployment_assessment import plan_body_digest
from mission.models import JSONObject
from roboguide_workbench import preflight
from roboguide_workbench.actions import checked_request
from roboguide_workbench.cli import display_question
from roboguide_workbench.display import DisplayTurn
from roboguide_workbench.runbooks import read
from test_workbench_agent_loop import Responses, _call, _message, _response
from test_workbench_dialogue import _router
from test_workbench_display import workbench


def test_plan_check_preserves_original_draft_and_cannot_submit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Expose actual blocked/unknown assessments under the visible draft and disallow a write."""
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    with workbench(tmp_path) as (session, controller):
        display_question(session, "/plan 只规划检查东区")
        assert session.shown_draft is not None
        draft = checked_request(session.shown_draft)
        assert draft.plan is not None
        original = draft.plan.to_json()
        digest = plan_body_digest(draft.plan)
        commands = copy.deepcopy(session.store.data["commands"])
        paths: list[str] = []
        stale = False

        def assess(
            url: str, method: str, headers: object, body: bytes, *args: object
        ) -> JSONObject:
            """Check exact assessed plan bytes and keep the two assessment sources independent."""
            assert method == "POST" and json.loads(body) == original
            paths.append(url)
            if url.endswith("/validate-plan"):
                return {
                    "schema_version": "roboguide.plan-validation/v0.1",
                    "scope": "published-contracts",
                    "mission_id": draft.mission_id,
                    "plan_body_sha256": digest,
                    "decision": "blocked",
                    "verifier_source_digest": None,
                    "deployment_digest": None,
                    "execution_lifetime": None,
                    "world_identity": None,
                    "issues": [
                        {
                            "code": "deployment.operation_unsupported",
                            "path": "/tasks/0",
                            "message": "缺少已发布的巡检合同",
                        }
                    ],
                    "issues_truncated": False,
                }
            assert url.endswith("/assess-initial-support")
            raise OSError("fixture source unavailable")

        original_get = session.actions.get

        def refresh(request_id: str) -> JSONObject:
            """Simulate an independently revised draft without selecting it in Chat."""
            raw = original_get(request_id)
            if stale:
                raw["draft_revision"] = draft.draft_revision + 1
            return raw

        monkeypatch.setattr(preflight, "request_json", assess)
        monkeypatch.setattr(session.actions, "get", refresh)
        transport = Responses(
            [
                _response(_call("check_plan", {})),
                _response(_call("execute_plan", {}, "attempt")),
                _response(_message("不能根据不完整的检查执行。")),
                _response(_call("check_plan", {})),
                _response(_message("先查看更新版。")),
            ]
        )
        session.router_factory = lambda: _router(transport)
        display_question(session, "这个计划为什么不能执行？")
        assert len(paths) == 2 and session.query_status == "partial"
        assert "缺少已发布的巡检合同" in output.getvalue()
        assert "证据不可用" in output.getvalue() and "下一步" in output.getvalue()
        assert session.store.data["commands"] == commands and controller.submissions == []
        continuation = cast(list[JSONObject], transport.requests[1]["input"])
        actual = json.loads(str(continuation[-1]["output"]))
        assert actual["scope"] == "configured-controller/current-identity"
        stale = True
        display_question(session, "检查计划执行条件")
        assert "请先查看新计划，再重新检查" in output.getvalue() and len(paths) == 2
        assert session.store.data["commands"] == commands and controller.submissions == []


def test_runbook_tools_are_local_and_show_verified_citations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Search Chinese operational terms, open the committed source and cite it independently."""
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    doc = "runtime-reliability-fault-matrix"
    path = Path(__file__).resolve().parents[2] / f"docs/development/{doc}.md"
    start = next(
        i for i, row in enumerate(path.read_text().splitlines(), 1) if "Node route or lease" in row
    )
    transport = Responses(
        [
            _response(_call("search_runbooks", {"query": "掉线 Unknown"})),
            _response(_call("read_runbook", {"document": doc, "start_line": start}, "page")),
            _response(_message("根据验收手册，先核对原执行和恢复状态。")),
        ]
    )
    with workbench(tmp_path) as (session, controller):
        session.router_factory = lambda: _router(transport)
        display_question(session, "按运维资料说明设备掉线后怎么核对恢复。")
        text = output.getvalue()
        assert "资料出处（历史参考）" in text and f"{doc}.md 第 {start}" in text
        assert "依据：随版本提供的运维资料" in text and "本轮 Controller 读取" not in text
        assert (
            "node route or lease" in json.dumps(transport.requests[1], ensure_ascii=False).lower()
        )
        assert session.store.data["commands"] == [] and controller.submissions == []
    from roboguide_workbench.client import ToolRefusal

    with pytest.raises(ToolRefusal):
        read("../../.config/roboguide/config", 1)


@pytest.mark.parametrize("visible", [True, False])
def test_numbered_clarification_requires_display_and_keeps_original_goal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, visible: bool
) -> None:
    """A bare number binds only to the acknowledged menu and never submits physical work."""
    output = io.StringIO()
    monkeypatch.setattr(sys, "stdout", output)
    transport = Responses(
        [
            _response(
                _call("start_task", {"mode": "plan", "start_turn": "current"}, "premature"),
                _call(
                    "clarify_task", {"question": "选哪个巡检区域？", "options": ["东区", "西区"]}
                ),
            ),
            _response(_call("start_task", {"mode": "plan", "start_turn": "current"})),
            _response(_message("已处理选择。")),
        ]
    )
    with workbench(tmp_path) as (session, controller):
        session.router_factory = lambda: _router(transport)
        original = "东区和西区可选，先只规划巡检，不执行。"
        if visible:
            display_question(session, original)
            assert "1. 东区" in output.getvalue() and "2. 西区" in output.getvalue()
            assert session.store.data["commands"] == [] and len(transport.requests) == 1
            display_question(session, "8")
            assert "序号超出范围" in output.getvalue() and len(transport.requests) == 1
        else:
            hidden = DisplayTurn(session)
            session.ask(original)
            hidden.reject()
            assert session.query_references()["clarification_options"] == []
        display_question(session, "第二个")
        commands = cast(list[JSONObject], session.store.data["commands"])
        if visible:
            body = cast(JSONObject, commands[0]["body"])
            assert body["submission_mode"] == "draft_only"
            assert original in str(body["instruction"]) and "第二个" in str(body["instruction"])
            assert "对已展示问题的参数选择（用户原始回复见上）： 西区" in str(body["instruction"])
        else:
            assert not commands
        assert controller.submissions == []
