"""Native Responses conversations, current reads and host proposal boundaries."""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from typing import cast

import pytest
from mission.models import JSONObject, JSONValue
from roboguide_workbench.agent_loop import AgentConversation
from roboguide_workbench.evidence import ReadResult
from roboguide_workbench.provider_config import model_settings
from roboguide_workbench.routing import ResponsesRouter


def _message(text: str) -> JSONObject:
    """Build a native Assistant output item rather than the retired intent JSON envelope."""
    return {
        "type": "message",
        "role": "assistant",
        "status": "completed",
        "content": [{"type": "output_text", "text": text}],
    }


def _response(*items: JSONObject, status: str = "completed") -> JSONObject:
    """Return a complete native response or an explicitly incomplete adversarial fixture."""
    return {"status": status, "output": cast(JSONValue, list(items))}


def _call(name: str, arguments: JSONObject | None = None, call_id: str = "call-1") -> JSONObject:
    """Encode a completed function request whose arguments remain independently validated."""
    return {
        "type": "function_call",
        "status": "completed",
        "call_id": call_id,
        "name": name,
        "arguments": json.dumps(arguments or {}),
    }


def _definition(name: str, fields: JSONObject | None = None) -> JSONObject:
    """Declare a closed host tool fixture with the same native Responses contract."""
    fields = fields or {}
    return {
        "type": "function",
        "name": name,
        "strict": True,
        "parameters": {
            "type": "object",
            "properties": fields,
            "required": list(fields),
            "additionalProperties": False,
        },
    }


TOOLS = [
    _definition("read_cluster"),
    _definition("read_active_missions"),
    _definition(
        "read_mission", {"mission_id": {"type": "string", "minLength": 1, "maxLength": 256}}
    ),
    _definition(
        "read_explanation", {"mission_id": {"type": "string", "minLength": 1, "maxLength": 256}}
    ),
    _definition(
        "propose_action",
        {"action": {"type": "string", "enum": ["plan", "run", "answer", "execute", "approve"]}},
    ),
]


class Responses:
    """Record immutable real request shapes while scripting model outputs and failures."""

    def __init__(self, steps: list[JSONObject | Exception]) -> None:
        """Freeze a finite sequence so unexpected extra model calls fail the actual loop."""
        self.steps = list(steps)
        self.requests: list[JSONObject] = []
        self.timeouts: list[float] = []

    def post_json(
        self, url: str, headers: Mapping[str, str], payload: JSONObject, timeout_seconds: float
    ) -> JSONObject:
        """Receive native payloads without opening network or exposing credentials."""
        assert url.endswith("/responses")
        self.requests.append(copy.deepcopy(payload))
        self.timeouts.append(timeout_seconds)
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def _agent(transport: Responses, *, seconds: float = 60) -> AgentConversation:
    """Build the production loop with no authenticated endpoint or legacy routing calls."""
    settings = model_settings(
        {"base_url": "http://127.0.0.1:9/v1", "name": "fixture", "requires_auth": False}
    )
    router = ResponsesRouter(settings, {}, transport)
    return AgentConversation(router, seconds=seconds)


def _no_read(_name: str, _arguments: JSONObject, _remaining: float) -> ReadResult:
    """Refuse any Controller access during a turn whose model returned plain conversation."""
    raise AssertionError("ordinary conversation must not invoke a read")


def test_consecutive_smalltalk_is_model_generated_and_keeps_role_aware_history() -> None:
    """Unrecognized wording stays natural without keywords, JSON classifications or reads."""
    questions = ["你好", "谢谢啦", "好呀", "我今天不想工作，随便聊点什么吧"]
    replies = ["你好！今天怎么样？", "不客气。", "嗯，我在。", "那就放轻松聊聊，最近有什么新鲜事？"]
    transport = Responses([_response(_message(text)) for text in replies])
    history: list[JSONObject] = []
    for question, expected in zip(questions, replies, strict=True):
        result = _agent(transport).answer(question, {}, history, _no_read, tool_definitions=TOOLS)
        assert result.text == expected and result.evidence == [] and result.status == "complete"
        assert result.proposal is None
        history.extend(
            [{"role": "user", "text": question}, {"role": "assistant", "text": result.text}]
        )
    assert len(transport.requests) == 4
    request = transport.requests[-1]
    assert "text" not in request and request["tool_choice"] == "auto"
    assert request["parallel_tool_calls"] is False
    assert request["include"] == ["reasoning.encrypted_content"]
    inputs = cast(list[JSONObject], request["input"])
    assert inputs[-1] == {"role": "user", "content": questions[-1]}
    assert inputs[-2] == {"role": "assistant", "content": replies[-2]}
    assert inputs[0]["role"] == "developer" and "不可信历史引用数据" in str(inputs[0]["content"])


def test_native_tool_roundtrip_preserves_reasoning_only_in_memory_and_host_read_identity() -> None:
    """Native call IDs pair actual reads with outputs without exposing private reasoning."""
    reasoning: JSONObject = {
        "type": "reasoning",
        "id": "rs-1",
        "encrypted_content": "private-state",
    }
    transport = Responses(
        [_response(reasoning, _call("read_cluster")), _response(_message("当前有两个节点。"))]
    )
    reads: list[str] = []
    metadata: JSONObject = {
        "source": "read-tool-adapter",
        "complete": True,
        "read_id": "host-read-1",
        "node_ids": ["a", "b"],
        "mission_ids": [],
    }

    def read(name: str, arguments: JSONObject, remaining: float) -> ReadResult:
        """Return one validated host projection and its exact publication identity."""
        assert arguments == {} and 0 < remaining <= 60
        reads.append(name)
        return ReadResult("已注册节点：2 个", components=(metadata,))

    result = _agent(transport).answer("你好，查一下节点", {}, [], read, tool_definitions=TOOLS)
    assert reads == ["read_cluster"] and result.status == "complete"
    assert result.text.startswith("当前有两个节点。") and "E1" not in result.text
    assert result.evidence[0]["components"] == [metadata]
    next_input = cast(list[JSONObject], transport.requests[1]["input"])
    assert reasoning in next_input
    output = next(row for row in next_input if row.get("type") == "function_call_output")
    assert output["call_id"] == "call-1"
    assert json.loads(str(output["output"]))["complete"] is True
    assert all(row.get("type") != "reasoning" for row in result.items)
    assert "private-state" not in json.dumps(result.items)


@pytest.mark.parametrize("status", ["in_progress", "incomplete", "failed", "cancelled"])
def test_unfinished_response_never_admits_a_tool(status: str) -> None:
    """Even plausible completed-looking calls cannot borrow an unfinished response's authority."""
    transport = Responses([_response(_call("read_cluster"), status=status)])
    result = _agent(transport).answer("查询节点", {}, [], _no_read, tool_definitions=TOOLS)
    assert result.status == "failed" and result.evidence == []


@pytest.mark.parametrize(
    "bad",
    [
        _call("shell", {"command": "unsafe"}),
        _call("read_cluster", {"url": "http://other.invalid"}),
        _call("read_mission", {"mission_id": 1}),
        _call("read_mission", {"mission_id": ""}),
        _call("read_mission", {"mission_id": "x", "extra": True}),
        {
            "type": "function_call",
            "call_id": "c",
            "name": "read_cluster",
            "arguments": '{"a":1,"a":2}',
        },
    ],
)
def test_whole_tool_batch_validates_before_the_first_read(bad: JSONObject) -> None:
    """A valid earlier call cannot execute before a malformed sibling has been refused."""
    transport = Responses([_response(_call("read_cluster", call_id="valid"), bad)])
    result = _agent(transport).answer("查询", {}, [], _no_read, tool_definitions=TOOLS)
    assert result.status == "failed" and result.evidence == []


@pytest.mark.parametrize("call_id", ["call-1", "different"])
def test_reused_call_id_and_repeated_read_do_not_reissue_http(call_id: str) -> None:
    """Both duplicate protocol identity and a new ID for the same query refuse another read."""
    followup = _call("read_cluster", call_id=call_id)
    transport = Responses([_response(_call("read_cluster")), _response(followup)])
    reads: list[str] = []

    def read(name: str, _arguments: JSONObject, _remaining: float) -> ReadResult:
        """Retain the exact number of admitted underlying reads."""
        reads.append(name)
        return ReadResult("已有两个节点")

    result = _agent(transport).answer("查询", {}, [], read, tool_definitions=TOOLS)
    assert reads == ["read_cluster"] and result.status == "partial"
    assert "已有两个节点" in result.text


def test_composite_read_cost_is_charged_before_batch_admission() -> None:
    """Two three-HTTP diagnostics cannot enter a four-read turn as two cheap model tools."""
    transport = Responses(
        [
            _response(
                _call("read_explanation", {"mission_id": "a"}),
                _call("read_explanation", {"mission_id": "b"}, "c2"),
            )
        ]
    )
    result = _agent(transport).answer("解释任务", {}, [], _no_read, tool_definitions=TOOLS)
    assert result.status == "failed" and result.evidence == []


def test_timeout_after_current_read_keeps_facts_and_original_wait_budget() -> None:
    """A failed model explanation retains current facts and never renews the turn timeout."""
    transport = Responses([_response(_call("read_cluster")), TimeoutError("模型服务暂时不可用")])

    def read(_name: str, _arguments: JSONObject, _remaining: float) -> ReadResult:
        """Return a useful current observation before the provider loses its next response."""
        return ReadResult("已注册两个节点")

    result = _agent(transport, seconds=0.5).answer("查询", {}, [], read, tool_definitions=TOOLS)
    assert result.status == "partial" and "已注册两个节点" in result.text
    assert "模型服务暂时不可用" in result.text
    assert 0 < transport.timeouts[1] <= transport.timeouts[0] <= 0.5


def test_failed_tool_does_not_allow_a_model_success_claim() -> None:
    """All unavailable sources produce host Unknown rather than an invented empty cluster."""
    transport = Responses(
        [_response(_call("read_cluster")), _response(_message("当前没有节点，已经全部停止。"))]
    )

    def read(_name: str, _arguments: JSONObject, _remaining: float) -> ReadResult:
        """Simulate a current Controller permission refusal."""
        raise ValueError("HTTP 403")

    result = _agent(transport).answer("查节点", {}, [], read, tool_definitions=TOOLS)
    assert result.status == "failed" and "HTTP 403" in result.text
    assert "没有节点" not in result.text and "状态无法确认" in result.text


@pytest.mark.parametrize("composite", [False, True])
def test_partial_evidence_cannot_support_an_unavailable_success_claim(composite: bool) -> None:
    """Independent and composite read failures retain host facts without generated assurances."""
    calls = (
        [_call("read_explanation", {"mission_id": "m1"})]
        if composite
        else [_call("read_cluster"), _call("read_mission", {"mission_id": "m1"}, "c2")]
    )
    transport = Responses(
        [_response(*calls), _response(_message("m1 没有任何恢复阻塞，可以继续执行。"))]
    )

    def read(name: str, _arguments: JSONObject, _remaining: float) -> ReadResult:
        """Return useful current facts beside an independently unavailable source."""
        if name == "read_cluster":
            return ReadResult("已注册两个节点")
        if name == "read_mission":
            raise ValueError("HTTP 403")
        return ReadResult(
            "Mission m1：Running\n原恢复评估不可用：HTTP 403",
            False,
            (
                {"source": "mission", "complete": True},
                {"source": "原恢复评估", "complete": False},
            ),
        )

    result = _agent(transport).answer("检查 m1 的恢复状态", {}, [], read, tool_definitions=TOOLS)
    assert result.status == "partial" and "HTTP 403" in result.text
    assert ("Running" if composite else "已注册两个节点") in result.text
    assert "没有任何恢复阻塞" not in result.text and "可以继续执行" not in result.text
    assert "未知" in result.text


def test_partial_tool_and_long_components_preserve_usable_evidence_identity() -> None:
    """A partial diagnostic keeps host publication metadata and its available subread."""
    transport = Responses(
        [
            _response(_call("read_explanation", {"mission_id": "a"})),
            _response(_message("任务仍在运行，绑定未知。")),
        ]
    )
    metadata: JSONObject = {"source": "read-tool-adapter", "complete": False, "read_id": "original"}

    def read(_name: str, _arguments: JSONObject, _remaining: float) -> ReadResult:
        """Return one useful Mission subread while the independent binding source is missing."""
        return ReadResult(
            "任务运行中" * 9000,
            False,
            (
                {"source": "mission", "complete": True, "details": "x" * 10_000},
                {"source": "原恢复评估", "complete": False, "text": "HTTP 403"},
                metadata,
            ),
        )

    result = _agent(transport).answer("解释 a", {}, [], read, tool_definitions=TOOLS)
    assert result.status == "partial"
    assert result.evidence[0]["has_complete_component"] is True
    components = cast(list[JSONObject], result.evidence[0]["components"])
    assert metadata in components
    assert len(str(result.evidence[0]["text"]).encode()) <= 24_000
    assert "原恢复评估" in result.text and "未知" in result.text


def test_proposal_returns_only_a_host_selection_without_entering_a_read() -> None:
    """A model's execution suggestion never becomes HTTP, approval or a command receipt."""
    transport = Responses([_response(_call("propose_action", {"action": "run"}))])
    result = _agent(transport).answer("帮我执行巡检", {}, [], _no_read, tool_definitions=TOOLS)
    assert result.proposal is not None and result.proposal.kind == "run"
    assert result.evidence == [] and result.text == ""


@pytest.mark.parametrize("after_read", [False, True])
def test_proposals_cannot_be_mixed_with_a_current_read(after_read: bool) -> None:
    """Reads cannot silently escalate into a write suggestion inside the same model loop."""
    initial = _response(_call("read_cluster"))
    proposal = _call("propose_action", {"action": "execute"}, "proposal")
    transport = Responses(
        [initial, _response(proposal)]
        if after_read
        else [_response(_call("read_cluster"), proposal)]
    )
    reads: list[str] = []

    def read(name: str, _arguments: JSONObject, _remaining: float) -> ReadResult:
        """Track legitimate current reads without exposing a mutation endpoint."""
        reads.append(name)
        return ReadResult("当前节点：两个")

    result = _agent(transport).answer("执行", {}, [], read, tool_definitions=TOOLS)
    assert result.proposal is None
    assert reads == (["read_cluster"] if after_read else [])


def test_refusal_and_unknown_output_type_cannot_enter_a_read() -> None:
    """Provider refusals and built-in tools do not bypass the host's function allowlist."""
    outputs: list[JSONObject] = [
        {"type": "message", "role": "assistant", "content": [{"type": "refusal", "refusal": "no"}]},
        {"type": "shell_call", "action": {"commands": ["not-allowed"]}},
    ]
    for output in outputs:
        transport = Responses([_response(_call("read_cluster"), output)])
        result = _agent(transport).answer("查询", {}, [], _no_read, tool_definitions=TOOLS)
        assert result.status == "failed" and result.evidence == []
